"""
modules/radio_commands.py
--------------------------
All user-facing command handlers for the AzuraCast radio / DJ request system.

DJBot (bot_mode = "dj") is the single owner of every command here.
All messages are ≤ 249 characters.

Commands
--------
  Public:
    !request <song/URL>   — search YouTube or queue a YouTube URL
    !pick <1-5>           — confirm a pending search result
    !queue  !q            — show now-playing + pending queue
    !nowplaying  !now  !np — detailed now-playing card
    !history              — last 8 played requests
    !voteskip             — public vote to skip current song
    !radiohelp            — command reference card

  Staff (admin / owner):
    !skip                 — immediately skip current song
    !remove <#>           — cancel a pending request by queue position
    !clearqueue           — cancel all pending requests
    !vibe chill|party|status — switch or inspect room vibe
    !setrequestprice <n>  — set coin price per request (0 = free)

Startup:
    startup_radio(bot)    — call from on_start for DJ bot
"""
from __future__ import annotations
import asyncio
import functools
import re
import threading
import time
from typing import TYPE_CHECKING

import database as db
import modules.azuracast_controller as azura
import modules.config_store         as cs
import modules.dj_announcer         as ann
import modules.music_credits        as mc
import modules.payment_service      as ps
import modules.request_queue        as rq
import modules.playback_engine      as engine
import modules.radio_diagnostics    as diag
import modules.radio_rewards        as rr
import modules.radio_achievements   as _ra
from modules.permissions import is_admin, is_owner, can_moderate
from modules.luxe import get_luxe_balance, deduct_luxe_balance, log_luxe_transaction
from modules.msg_utils import safe_send as _safe_send_mu

if TYPE_CHECKING:
    from highrise import BaseBot, User

_LOG = "[RADIO_CMD]"

# ─── Bot-mode guard (same pattern as dj_music.py) ─────────────────────────────
try:
    from config import BOT_MODE as _rc_bot_mode
except Exception:
    _rc_bot_mode = ""
_IS_DJ_BOT: bool = (_rc_bot_mode == "dj")

# ─── Router log helper ────────────────────────────────────────────────────────

def _rlog(cmd: str, handler: str, username: str) -> None:
    try:
        from config import BOT_MODE as _bm
    except Exception:
        _bm = "unknown"
    print(
        f"[RADIO_ROUTER] stage=radio_command_router"
        f" command={cmd!r} handler={handler!r}"
        f" bot_mode={_bm!r} username={username!r}"
    )

# ─── Per-user like/dislike cooldown (in-memory, resets on restart) ────────────
_like_cd: "dict[str, float]" = {}
_LIKE_CD_SECS = 2

# ─── Priority request mode tracking (user IDs in priority search mode) ─────────
_priority_mode: "set[str]" = set()


def _priority_cost_tickets() -> int:
    """Luxe tickets required for a priority request slot (0 = disabled)."""
    try:
        return max(0, int(db.get_room_setting("request_priority_cost_tickets", "100")))
    except Exception:
        return 100

# ─── DB helpers for favorites / ratings (read from shared dj_* tables) ────────

def _azura_track() -> "dict | None":
    """Return {title, artist, key} for the current AzuraCast track, or None."""
    np = azura.fetch_nowplaying()
    if not np:
        return None
    song  = ((np.get("now_playing") or {}).get("song") or {})
    title = (song.get("title") or "").strip()
    if not title:
        return None
    return {
        "title":  title,
        "artist": (song.get("artist") or "").strip(),
        "key":    title.lower()[:150],
    }


def _current_track_full() -> "dict | None":
    """Return full current-track metadata for playlist/favorites ops, including AzuraCast IDs."""
    np = azura.fetch_nowplaying()
    if not np:
        return None
    song  = ((np.get("now_playing") or {}).get("song")  or {})
    media = ((np.get("now_playing") or {}).get("media") or {})
    title = (song.get("title") or "").strip()
    if not title:
        return None
    artist      = (song.get("artist") or "").strip()
    azura_sid   = (song.get("unique_id") or "").strip()
    azura_fid   = str(media.get("id") or "").strip()
    cp          = rq.currently_playing()
    youtube_url = (cp.get("url") or "") if cp else ""
    video_id    = (cp.get("video_id") or "") if cp else ""
    return {
        "title":         title,
        "artist":        artist,
        "key":           title.lower()[:150],
        "youtube_url":   youtube_url,
        "video_id":      video_id,
        "azura_song_id": azura_sid,
        "azura_file_id": azura_fid,
        "source_type":   "youtube" if youtube_url else "local",
    }


def _fav_get(user_id: str, limit: int = 10) -> list:
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT id, title, youtube_url, COALESCE(artist,''), "
                "COALESCE(source_type,''), COALESCE(azura_file_id,''), "
                "COALESCE(azura_song_id,'') FROM dj_favorites "
                "WHERE user_id=? ORDER BY favorited_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [
                {
                    "id": r[0], "title": r[1], "url": r[2], "artist": r[3],
                    "source_type": r[4], "azura_file_id": r[5],
                    "azura_song_id": r[6],
                }
                for r in rows
            ]
    except Exception:
        return []


def _fav_remember_youtube_source(fav_id: int, user_id: str, url: str, artist: str = "") -> None:
    """Remember a !playfav fallback URL until playback confirms it actually played."""
    if not fav_id or not user_id or not url:
        return
    try:
        with db.db_conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS playfav_fallback_sources ("
                "fav_id INTEGER NOT NULL, "
                "user_id TEXT NOT NULL, "
                "url TEXT NOT NULL, "
                "artist TEXT NOT NULL DEFAULT '', "
                "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
                "PRIMARY KEY (fav_id, url)"
                ")"
            )
            conn.execute(
                "INSERT OR REPLACE INTO playfav_fallback_sources "
                "(fav_id, user_id, url, artist, created_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                (fav_id, user_id, url, artist),
            )
        print(f"{_LOG} stage=playfav_fallback_pending fav_id={fav_id} url={url!r}")
    except Exception as exc:
        print(f"{_LOG} playfav fallback source remember error: {exc!r}")


def _fav_add(user_id: str, username: str, title: str, url: str, artist: str = "") -> bool:
    """Insert into dj_favorites. Returns False if already there."""
    try:
        with db.db_conn() as conn:
            if conn.execute(
                "SELECT id FROM dj_favorites WHERE user_id=? AND lower(title)=lower(?)",
                (user_id, title),
            ).fetchone():
                return False
            conn.execute(
                "INSERT INTO dj_favorites (user_id, username, title, youtube_url, artist) "
                "VALUES (?,?,?,?,?)",
                (user_id, username.lower(), title, url, artist),
            )
            return True
    except Exception:
        return False


def _fav_remove_by_pos(user_id: str, pos: int) -> "str | None":
    """Remove nth (1-indexed) favorite. Returns title if removed, None if out of range."""
    rows = _fav_get(user_id, limit=20)
    if pos < 1 or pos > len(rows):
        return None
    row = rows[pos - 1]
    try:
        with db.db_conn() as conn:
            conn.execute("DELETE FROM dj_favorites WHERE id=?", (row["id"],))
        return row["title"]
    except Exception:
        return None


def _fav_remove_title(user_id: str, title: str) -> bool:
    """Remove by title match (unfavorite current track)."""
    try:
        with db.db_conn() as conn:
            cur = conn.execute(
                "DELETE FROM dj_favorites WHERE user_id=? AND lower(title)=lower(?)",
                (user_id, title),
            )
            return cur.rowcount > 0
    except Exception:
        return False


def _rate(user_id: str, username: str, key: str, rating: str) -> str:
    """Upsert into dj_ratings. Returns 'added'|'changed'|'same'|'error'."""
    try:
        with db.db_conn() as conn:
            existing = conn.execute(
                "SELECT rating FROM dj_ratings WHERE user_id=? AND song_key=?",
                (user_id, key),
            ).fetchone()
            if existing:
                if existing[0] == rating:
                    return "same"
                conn.execute(
                    "UPDATE dj_ratings SET rating=?, username=?, rated_at=datetime('now') "
                    "WHERE user_id=? AND song_key=?",
                    (rating, username.lower(), user_id, key),
                )
                return "changed"
            conn.execute(
                "INSERT INTO dj_ratings (user_id, username, song_key, rating) "
                "VALUES (?,?,?,?)",
                (user_id, username.lower(), key, rating),
            )
            return "added"
    except Exception:
        return "error"


def _ratings(key: str) -> dict:
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT rating, COUNT(*) FROM dj_ratings WHERE song_key=? GROUP BY rating",
                (key,),
            ).fetchall()
            result: dict = {"likes": 0, "dislikes": 0}
            for r in rows:
                if r[0] == "like":
                    result["likes"] = r[1]
                elif r[0] == "dislike":
                    result["dislikes"] = r[1]
            return result
    except Exception:
        return {"likes": 0, "dislikes": 0}


def _user_job_history(user_id: str, limit: int = 5) -> list:
    """User's most recent entries from yt_request_jobs (active + played)."""
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT title, status FROM yt_request_jobs "
                "WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [{"title": r[0], "status": r[1]} for r in rows]
    except Exception:
        return []

# ─── YouTube URL pattern (same as yt_request.py) ──────────────────────────────
_YT_RE = re.compile(
    r"^https?://(?:www\.)?"
    r"(?:"
    r"youtube\.com/watch\?(?:.*&)?v=[\w\-]{11}"
    r"|youtu\.be/[\w\-]{11}"
    r"|youtube\.com/shorts/[\w\-]{11}"
    r")"
)

# ─── Per-user request cooldowns  (in-memory, resets on bot restart) ───────────
_cooldowns: "dict[str, float]" = {}

# ─── Vote-skip state ──────────────────────────────────────────────────────────
_vote_skip: "dict[str, set]" = {}   # azura song_id → set of voter user_ids
_vote_lock = threading.Lock()


def _is_yt_url(s: str) -> bool:
    return bool(_YT_RE.match(s))


def _is_any_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def _is_youtube_playlist_url(url: str) -> bool:
    u = (url or "").lower()
    return (
        "youtube.com/playlist" in u
        or "list=" in u
        or "/mix" in u
        or "start_radio=1" in u
    )


def _is_staff(username: str) -> bool:
    return is_owner(username) or is_admin(username)


from modules.radio_renderer import _fmt_secs, _progress_bar
import modules.radio_renderer as rdr

# ─── Title noise stripper (for search results display) ────────────────────────
_TITLE_NOISE = re.compile(
    r"\s*[\(\[]\s*(?:official\s+(?:music\s+)?(?:video|audio|lyric(?:\s+video)?|visualizer)"
    r"|lyric(?:s|\s+video)?|visualizer|audio|hd|4k|full\s+(?:video|song)"
    r"|official)\s*[\)\]]\s*"
    r"|\s*\|\s*(?:official\s+(?:music\s+)?(?:video|audio)|lyric(?:s|\s+video)?|hd|4k)\s*$"
    r"|\s+(?:official\s+(?:music\s+)?(?:video|audio)|official\s+lyric(?:\s+video)?)\s*$",
    re.IGNORECASE,
)
_TRAILING_SEP = re.compile(r"[\s\-–|]+$")


def _clean_title(title: str) -> str:
    """Strip common YouTube noise for compact display. Max 38 chars after cleaning."""
    cleaned = _TITLE_NOISE.sub("", title)
    cleaned = _TRAILING_SEP.sub("", cleaned).strip()
    return cleaned


async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await _safe_send_mu(bot, msg, whisper_target=uid, max_chars=240)
    except Exception:
        pass


async def _send_pages(bot: "BaseBot", uid: str, pages: list[str]) -> None:
    for page in pages:
        try:
            await _safe_send_mu(bot, page[:255], whisper_target=uid, max_chars=255)
        except Exception:
            pass
        await asyncio.sleep(0.05)


def _split_search_pages(results: list[dict], max_secs: int) -> list[str]:
    footer = "Reply: !pick 1-5"
    body_pages: list[list[str]] = []
    current: list[str] = []

    def _line(i: int, row: dict, limit: int = 190) -> str:
        title = (row.get("title") or "Unknown title").strip()
        dur = row.get("duration") or "?"
        flag = " ⚠️" if int(row.get("duration_secs") or 0) > max_secs else ""
        line = f"{i}. {title} [{dur}]{flag}"
        if len(line) > limit:
            line = line[: limit - 1] + "…"
        return line

    for i, row in enumerate(results[:5], 1):
        line = _line(i, row)
        test = current + [line]
        preview = "\n".join(["🎵 Search results 9/9", *test, footer])
        if current and len(preview) > 255:
            body_pages.append(current)
            current = [line]
        else:
            current = test
    if current:
        body_pages.append(current)

    total = max(1, len(body_pages))
    pages: list[str] = []
    for idx, lines in enumerate(body_pages, 1):
        page = "\n".join([f"🎵 Search results {idx}/{total}", *lines, footer])
        while len(page) > 255 and lines:
            last = lines[-1]
            lines[-1] = last[: max(20, len(last) - (len(page) - 254))] + "…"
            page = "\n".join([f"🎵 Search results {idx}/{total}", *lines, footer])
        pages.append(page[:255])
    return pages


# ─── Core request submission helper ──────────────────────────────────────────

async def _submit_url(
    bot: "BaseBot", user: "User", url: str,
    metadata: "dict | None" = None,
    priority: int = 0,
) -> bool:
    """
    Validate, charge, and launch a job for a confirmed YouTube/audio URL.
    Handles dedup, queue capacity, payment, and cooldown in one place.
    priority=1 means the job was already paid with luxe tickets by caller.
    """
    uid      = user.id
    uname    = user.username

    if _is_youtube_playlist_url(url):
        await _w(bot, uid, "⚠️ Please use one YouTube song URL, not a playlist or mix.")
        return False

    is_staff = _is_staff(uname)

    # Regular users: YouTube URLs only
    if not is_staff and not _is_yt_url(url):
        await _w(bot, uid, "🎵 Please use a YouTube URL or search by song name.")
        return False

    # Banned requester check
    if rq.is_banned_requester(uname):
        await _w(bot, uid, "🚫 You are not allowed to request songs in this room.")
        return False

    # Cooldown (admin bypasses)
    if not is_staff:
        cd      = cs.cooldown_secs()
        elapsed = time.time() - _cooldowns.get(uid, 0)
        if elapsed < cd:
            remaining = int(cd - elapsed)
            await _w(bot, uid, f"⏳ Cooldown: {remaining}s remaining. Please wait.")
            return False

    # Dedup (admin bypasses)
    if not is_staff:
        dup = rq.check_dedup(url, cs.DEDUP_WINDOW_SECS)
        if dup:
            req_by = (dup.get("username") or "someone")[:20]
            await _w(
                bot, uid,
                f"⚠️ That song was requested recently by @{req_by}. Try again tomorrow.",
            )
            return False

    # Queue capacity
    active_now = rq.active_count()
    max_queue = cs.max_active_queue_limit()
    if active_now >= max_queue:
        await _w(
            bot, uid,
            f"📋 Queue is full ({active_now}/{max_queue} requests in progress). Please wait.",
        )
        return False

    # Per-user queue limit
    if not is_staff:
        limit   = cs.per_user_queue_limit()
        u_count = rq.user_active_count(uid)
        if limit > 0 and u_count >= limit:
            await _w(
                bot, uid,
                f"📋 You already have {u_count} song(s) queued. Wait for them to play first.",
            )
            return False

    # Music request credit check (non-staff, non-priority only)
    _credit_consumed = False
    if not is_staff and not priority:
        print(
            f"[RADIO_CMD] stage=music_credit_check"
            f" user_id={uid!r} username={uname!r}"
        )
        if not mc.has_credits(uid, uname):
            await _w(
                bot, uid,
                "❌ Out of 💿 Song Plays! Use !musicshop to buy more.\n"
                "New players get 5 free plays. Packs from 500 🪙 or 20 🎟️",
            )
            return False
        if not mc.consume_credit(uid, uname):
            await _w(bot, uid, "❌ Out of 💿 Song Plays! Use !musicshop.")
            return False
        _credit_consumed = True

    # Price + payment
    price = ps.request_cost_for(uname)
    ok, err = ps.charge(uid, price)
    if not ok:
        if _credit_consumed:
            mc.refund_credit(uid, uname)
        await _w(bot, uid, f"💸 {err}")
        return False

    # Update cooldown after successful charge
    _cooldowns[uid] = time.time()

    # Compute queue position: count only future songs, excluding currently playing
    _pos = rq.future_count() + 1

    # Confirmation whisper — shared with local favorites.
    _title  = ((metadata.get("title")  or "") if metadata else "")[:50]
    _artist = ((metadata.get("artist") or metadata.get("uploader") or "") if metadata else "")[:28]
    _remaining = None if (is_staff or priority) else mc.get_credits(uid, uname)["total"]
    await _w(
        bot,
        uid,
        rq.render_added_to_queue_message(
            title=_title,
            artist=_artist,
            position=_pos,
            priority=priority,
            staff_free=is_staff,
            plays_left=_remaining,
        ),
    )

    # Radio rewards: successful request queued
    _req_key = (_title or url)[:150]
    rr.record_reward(uid, uname, "request", song_key=_req_key)
    asyncio.create_task(_ra.check_radio_achievements(bot, uid, uname))
    if _title:
        rr.update_song_info(_req_key, _title, _artist)

    # Launch pipeline
    rq.submit_job(
        bot, uid, uname, url,
        coins_charged=price,
        payment_type="paid" if price > 0 else "free",
        priority=priority,
    )
    return True


# ─── !request ─────────────────────────────────────────────────────────────────

async def handle_request(
    bot: "BaseBot", user: "User", args: list
) -> None:
    """
    !request <song name or YouTube URL>
    Aliases: !sr !req !song
    """
    if not cs.sftp_ready():
        missing = cs.sftp_missing_vars()
        await _w(bot, user.id, f"📻 Requests not available (missing: {', '.join(missing)}).")
        return

    if not cs.request_system_enabled():
        await _w(bot, user.id, "📻 Song requests are currently disabled.")
        return

    if len(args) < 2:
        price    = cs.request_price()
        cost_str = f"{price:,} coins" if price else "free"
        await _w(
            bot, user.id,
            f"🎵 !request <song name or YouTube URL>  ({cost_str})\n"
            f"e.g. !request blinding lights  or  !request youtu.be/dQw4w9WgXcQ",
        )
        return

    query = " ".join(args[1:]).strip()[:200]
    if not query:
        await _w(bot, user.id, "🎵 Please include a song name or YouTube URL.")
        return

    # Direct URL → skip search
    if _is_any_url(query):
        if _is_youtube_playlist_url(query):
            await _w(bot, user.id, "⚠️ Please use one YouTube song URL, not a playlist or mix.")
            return
        await _submit_url(bot, user, query)
        return

    # Text → search YouTube
    await _w(bot, user.id, f"🔍 Searching: {query[:60]}…")
    loop = asyncio.get_running_loop()
    try:
        results = await loop.run_in_executor(None, rq.search_yt, query, 5)
    except Exception as exc:
        await _w(bot, user.id, f"❌ Search error: {str(exc)[:60]}")
        return

    if not results:
        await _w(bot, user.id, "❌ No results found. Try a different search term.")
        return

    rq.set_pending_search(user.id, results)
    pages = _split_search_pages(results, cs.MAX_DURATION_SECS)
    if len(pages) > 1:
        diag.log_radio_event(
            "youtube_search_paginated",
            user_id=user.id,
            username=user.username,
            pages=len(pages),
            results=len(results[:5]),
        )
    await _send_pages(bot, user.id, pages)


# ─── !priority ────────────────────────────────────────────────────────────────

async def handle_priority(bot: "BaseBot", user: "User", args: list) -> None:
    """
    !priority <song/URL> — submit a priority request using luxe tickets.
    Costs _priority_cost_tickets() luxe tickets (default 100).
    For search queries, the charge is applied when !pick is confirmed.
    For direct URLs, the charge is applied immediately.
    """
    if not cs.sftp_ready():
        missing = cs.sftp_missing_vars()
        await _w(bot, user.id, f"📻 Requests not available (missing: {', '.join(missing)}).")
        return

    if not cs.request_system_enabled():
        await _w(bot, user.id, "📻 Song requests are currently disabled.")
        return

    cost = _priority_cost_tickets()
    if cost <= 0:
        await _w(bot, user.id, "⭐ Priority queue is not enabled right now.")
        return

    bal = get_luxe_balance(user.id)

    if len(args) < 2:
        await _w(
            bot, user.id,
            f"⭐ Priority Request\n!priority <song/URL>\n"
            f"Cost: {cost} luxe tickets | Your balance: {bal}",
        )
        return

    if bal < cost:
        await _w(
            bot, user.id,
            f"🎟 Not enough luxe tickets.\nPriority costs {cost} tickets. You have {bal}.",
        )
        return

    query = " ".join(args[1:]).strip()[:200]
    if not query:
        await _w(bot, user.id, "⭐ Please include a song name or YouTube URL.")
        return

    # Direct URL — charge immediately then submit with priority=1
    if _is_any_url(query):
        if _is_youtube_playlist_url(query):
            await _w(bot, user.id, "⚠️ Please use one YouTube song URL, not a playlist or mix.")
            return
        if not deduct_luxe_balance(user.id, user.username, cost):
            await _w(bot, user.id, "⚠️ Could not charge luxe tickets. Try again.")
            return
        try:
            log_luxe_transaction(
                user.id, user.username,
                "priority_request", cost, "luxe_tickets",
                f"URL priority request",
            )
        except Exception:
            pass
        await _submit_url(bot, user, query, priority=1)
        return

    # Text search — charge on !pick confirmation
    await _w(bot, user.id, f"🔍 Searching: {query[:60]}…")
    loop = asyncio.get_running_loop()
    try:
        results = await loop.run_in_executor(None, rq.search_yt, query, 5)
    except Exception as exc:
        await _w(bot, user.id, f"❌ Search error: {str(exc)[:60]}")
        return

    if not results:
        await _w(bot, user.id, "❌ No results found. Try a different search term.")
        return

    _priority_mode.add(user.id)
    rq.set_pending_search(user.id, results)
    pages = _split_search_pages(results, cs.MAX_DURATION_SECS)
    pages = [p.replace("🎵 Search results", "⭐ Priority results", 1) for p in pages]
    if len(pages) > 1:
        diag.log_radio_event(
            "youtube_search_paginated",
            user_id=user.id,
            username=user.username,
            pages=len(pages),
            results=len(results[:5]),
        )
    await _send_pages(bot, user.id, pages)


# ─── !pick ────────────────────────────────────────────────────────────────────

async def handle_pick(bot: "BaseBot", user: "User", args: list) -> None:
    """!pick <1-5>  — confirm a pending search result from !request or !priority."""
    if not rq.has_pending_search(user.id):
        await _w(bot, user.id, "🎵 No pending search. Use !request <song> first.")
        return

    results = rq.get_pending_search(user.id) or []
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, f"🎵 Reply !pick <1-{len(results)}> to select a result.")
        return

    idx = int(args[1]) - 1
    if idx < 0 or idx >= len(results):
        await _w(bot, user.id, f"⚠️ Pick a number between 1 and {len(results)}.")
        return

    picked = results[idx]
    url    = picked.get("url", "")

    # ── Priority mode: charge luxe tickets before clearing search ────────────
    is_priority = user.id in _priority_mode
    if is_priority:
        cost = _priority_cost_tickets()
        bal  = get_luxe_balance(user.id)
        if bal < cost:
            rq.clear_pending_search(user.id)
            _priority_mode.discard(user.id)
            await _w(
                bot, user.id,
                f"🎟 Not enough luxe tickets. Priority costs {cost} tickets. You have {bal}.",
            )
            return
        if not deduct_luxe_balance(user.id, user.username, cost):
            rq.clear_pending_search(user.id)
            _priority_mode.discard(user.id)
            await _w(bot, user.id, "⚠️ Could not charge luxe tickets. Try again.")
            return
        try:
            log_luxe_transaction(
                user.id, user.username,
                "priority_request", cost, "luxe_tickets",
                f"Priority pick: {(picked.get('title') or '')[:40]}",
            )
        except Exception:
            pass
        _priority_mode.discard(user.id)

    rq.clear_pending_search(user.id)

    if not url:
        await _w(bot, user.id, "❌ Could not get URL for that result. Try again.")
        return

    await _submit_url(bot, user, url, metadata=picked, priority=1 if is_priority else 0)


# ─── !queue / !q ──────────────────────────────────────────────────────────────

async def handle_queue(bot: "BaseBot", user: "User", _args: list) -> None:
    """
    !queue / !q — display-only queue board.

    This handler must not repair, mutate, mark played, or clean files. Lifecycle
    transitions are owned by playback_engine.
    """
    rq.sync_indexed_active_statuses()
    all_jobs = rq.display_jobs()
    print(f"{_LOG} stage=queue_render command=queue count={len(all_jobs)}")

    def _qicon(status: str) -> str:
        if status == "playing":
            return "✅"
        if status in ("ready", "queued", "submitted", "done"):
            return "✅"
        if status in ("error", "failed", "cancelled"):
            return "❌"
        if status in ("processing", "copying", "downloading", "uploading", "indexing", "staged"):
            return "⏳"
        return "⏳"  # pending/downloading/downloaded/uploading

    async def _send_queue_page(msg: str) -> None:
        try:
            await _safe_send_mu(bot, msg, whisper_target=user.id, max_chars=255)
        except Exception:
            pass

    if not all_jobs:
        await _send_queue_page("🎶 QUEUE\nempty\n!play to request a song")
        return

    lines: list[str] = ["🎶 QUEUE"]
    for n, j in enumerate(all_jobs, 1):
        icon   = _qicon(j.get("status", ""))
        uname  = (j.get("username") or "?").strip()[:12]
        title  = (j.get("title")    or "…").strip()
        artist = (j.get("artist")   or "").strip()
        label  = title[:30] + (f" - {artist[:16]}" if artist else "")
        lines.append(f"{n}. @{uname} - {label} {icon}")

    lines.append("!play to request a song")

    # Paginate: flush page when adding next line would exceed 255 chars.
    page = ""
    pages: list[str] = []
    for ln in lines:
        candidate = (page + "\n" + ln) if page else ln
        if len(candidate) > 255:
            if page:
                pages.append(page)
            page = ln
        else:
            page = candidate
    if page:
        pages.append(page)

    for p in pages:
        await _send_queue_page(p[:255])


# ─── !nowplaying ──────────────────────────────────────────────────────────────

async def handle_nowplaying(bot: "BaseBot", user: "User", _args: list) -> None:
    """!nowplaying / !now / !np — compact vertical now-playing card."""
    from modules.track_resolver import resolve_current_track, render_now_playing

    loop = asyncio.get_running_loop()
    np   = await loop.run_in_executor(None, azura.fetch_nowplaying)

    if not np:
        await _w(
            bot, user.id,
            "📻 ChillTopia Radio is live, but I can't read the current track right now.",
        )
        return

    track = resolve_current_track(np)
    await _w(bot, user.id, render_now_playing(track))


# ─── !skip ────────────────────────────────────────────────────────────────────

async def handle_skip(bot: "BaseBot", user: "User", _args: list) -> None:
    """
    !skip — immediately skip the current song (admin+).

    After a confirmed skip:
    - Matches the NP response against ALL active jobs (ready OR playing) so a
      request that hasn't yet been promoted to status='playing' by the poll loop
      is still consumed exactly once.
    - Falls back to rq.currently_playing() (status='playing' DB rows) if the
      NP match finds nothing.
    - Queues file cleanup via playback engine (move to PlayedRequests/, rescan).
    - Logs stage=request_skipped with match_method and prior_status for audit.
    """
    if not _is_staff(user.username):
        await _w(bot, user.id, "🔒 Staff only.")
        return

    loop = asyncio.get_running_loop()

    # Fetch NP BEFORE the skip so we can match what is currently on air.
    np = await loop.run_in_executor(None, azura.fetch_nowplaying)
    title_str = ""
    if np:
        s   = ((np.get("now_playing") or {}).get("song") or {})
        art = (s.get("artist") or "").strip()
        ttl = (s.get("title")  or "").strip()
        title_str = (f"{art} — {ttl}" if art else ttl)[:60]

    # Match NP against active jobs (status in ready/playing) using the same
    # 5-strategy logic as the poll loop.  This catches requests whose DB status
    # is still 'ready' (poll loop hasn't confirmed them yet).
    # Fall back to rq.currently_playing() as a safety net.
    np_match       = engine.match_nowplaying_to_job(np) if np else None
    cp             = rq.currently_playing()
    job_to_consume = np_match or cp

    ok = await loop.run_in_executor(None, azura.skip_current)
    if ok:
        if job_to_consume:
            job_id = job_to_consume.get("id")
            if job_id:
                match_method  = (np_match.get("_match_method") or "np_unknown") if np_match else "db_cp"
                prior_status  = (job_to_consume.get("status") or "?")
                print(
                    f"{_LOG} stage=request_skipped"
                    f" request_id={job_id}"
                    f" title={job_to_consume.get('title','?')!r}"
                    f" username={job_to_consume.get('username','?')!r}"
                    f" skipped_by={user.username!r}"
                    f" match_method={match_method!r}"
                    f" prior_status={prior_status!r}"
                )
                asyncio.create_task(engine.on_request_skipped(bot, job_id))
        await ann.announce_skip(bot, title_str)
        await _w(bot, user.id, "⏭️ Skipped.")
    else:
        await _w(bot, user.id, "❌ Skip failed. Try again.")


# ─── !remove ──────────────────────────────────────────────────────────────────

async def handle_remove(bot: "BaseBot", user: "User", args: list) -> None:
    """!remove <#> — cancel a pending request by queue position (admin+)."""
    if not _is_staff(user.username):
        await _w(bot, user.id, "🔒 Staff only.")
        return

    pending = rq.pending_jobs()

    if len(args) < 2 or not args[1].isdigit():
        if not pending:
            await _w(bot, user.id, "📋 No pending requests to remove.")
        else:
            lines = ["📋 Pending (use !remove <#>):"]
            for i, j in enumerate(pending, 1):
                t = (j.get("title") or "downloading…")[:28]
                u = (j.get("username") or "?")[:12]
                lines.append(f"  {i}. {t} — @{u}")
            await _w(bot, user.id, "\n".join(lines)[:249])
        return

    idx = int(args[1]) - 1
    if idx < 0 or idx >= len(pending):
        await _w(bot, user.id, f"⚠️ No request #{args[1]}. Use !queue to see the list.")
        return

    job   = pending[idx]
    jid   = job["id"]
    title = (job.get("title") or "in progress")[:40]
    uid   = job.get("user_id", "")
    coins = job.get("coins_charged", 0)

    cancelled = rq.cancel_job(jid, "removed_by_admin")
    if not cancelled:
        await _w(bot, user.id, "⚠️ Could not remove — job may have just finished.")
        return

    note = ""
    if coins > 0 and uid:
        ps.refund(uid, coins, "removed_by_admin")
        diag.log_radio_event(
            "refund",
            request_id=jid,
            user_id=uid,
            coins=coins,
            reason="removed_by_admin",
        )
        note = f" ({coins:,} coins refunded)"

    # Refund music request credit if the requester was not a staff member
    # (staff requests bypass the credit system, so no credit to refund).
    req_uname = (job.get("username") or "").strip()
    if req_uname and not _is_staff(req_uname):
        try:
            mc.refund_credit(uid, req_uname)
            note += " + 1 play refunded"
        except Exception as _mce:
            print(f"{_LOG} handle_remove mc.refund_credit error: {_mce!r}")

    # Best-effort file cleanup
    loop = asyncio.get_running_loop()
    fid  = (job.get("azura_file_id") or "").strip()
    fn   = (job.get("filename")      or "").strip()
    if fid:
        loop.run_in_executor(None, azura.delete_media_file, fid)
    elif fn:
        loop.run_in_executor(None, azura.sftp_delete_file, fn)

    await _w(bot, user.id, f"✅ Removed: {title}{note}")


# ─── !cancel ──────────────────────────────────────────────────────────────────

async def handle_cancel(bot: "BaseBot", user: "User", args: list) -> None:
    """!cancel [#] — cancel your own pending request by position in your queue."""
    uid  = user.id
    all_pending = rq.pending_jobs()
    user_jobs   = [j for j in all_pending if j.get("user_id") == uid
                   and j.get("status") not in ("playing",)]

    if not user_jobs:
        await _w(bot, uid, "📋 You have no pending requests to cancel.")
        return

    if len(user_jobs) > 1 and (len(args) < 2 or not args[1].isdigit()):
        lines = [f"📋 Your requests (use !cancel <#>):"]
        for i, j in enumerate(user_jobs, 1):
            t = (j.get("title") or "downloading…")[:30]
            lines.append(f"  {i}. {t}")
        await _w(bot, uid, "\n".join(lines)[:249])
        return

    if len(args) > 1 and args[1].isdigit():
        idx = int(args[1]) - 1
        if idx < 0 or idx >= len(user_jobs):
            await _w(bot, uid, f"⚠️ You have {len(user_jobs)} request(s). Use !cancel 1–{len(user_jobs)}.")
            return
        job = user_jobs[idx]
    else:
        job = user_jobs[0]

    jid        = job["id"]
    title      = (job.get("title") or "in progress")[:40]
    coins      = job.get("coins_charged", 0)
    is_priority = bool(job.get("priority", 0))

    cancelled = rq.cancel_job(jid, "cancelled_by_user")
    if not cancelled:
        await _w(bot, uid, "⚠️ Could not cancel — it may have just started playing.")
        return

    note = ""
    if coins > 0:
        ps.refund(uid, coins, "cancelled_by_user")
        diag.log_radio_event(
            "refund",
            request_id=jid,
            user_id=uid,
            coins=coins,
            reason="cancelled_by_user",
        )
        note = f"\n💸 {coins:,} coins refunded."

    # Refund music request credit (non-staff, non-priority requests only)
    if not _is_staff(user.username) and not is_priority:
        mc.refund_credit(uid, user.username)
        note += "\n🎟 1 music request refunded."

    loop = asyncio.get_running_loop()
    fid  = (job.get("azura_file_id") or "").strip()
    fn   = (job.get("filename")      or "").strip()
    if fid:
        loop.run_in_executor(None, azura.delete_media_file, fid)
    elif fn:
        loop.run_in_executor(None, azura.sftp_delete_file, fn)

    await _w(bot, uid, f"✅ Cancelled: {title}{note}")


# ─── !clearqueue ──────────────────────────────────────────────────────────────

async def handle_clearqueue(bot: "BaseBot", user: "User", _args: list) -> None:
    """!clearqueue — cancel all pending/queued/staged requests with refunds (admin+)."""
    if not _is_staff(user.username):
        await _w(bot, user.id, "🔒 Staff only.")
        return

    loop   = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, lambda: rq.queue_clear_all(command="clearqueue", refund=True)
    )

    count = result["count_before"]
    ref   = result["refunded_coins"]

    print(
        f"{_LOG} stage=queue_clear command=clearqueue"
        f" count_before={count} count_after={result['count_after']}"
        f" refunded_coins={ref}"
    )

    if count == 0:
        await _w(bot, user.id, "✅ Queue is already empty.")
        return

    await ann.announce_queue_cleared(bot, count, ref)


# ─── !history ─────────────────────────────────────────────────────────────────

async def handle_history(bot: "BaseBot", user: "User", _args: list) -> None:
    """!history — last 8 played requests, paginated 4-per-page."""
    history = rq.recent_history(8)
    if not history:
        await _w(bot, user.id, "📜 No request history yet. Be the first to request a song!")
        return
    pages = rdr.render_history_pages(history)
    for i, msg in enumerate(pages):
        await _w(bot, user.id, msg)
        if i < len(pages) - 1:
            await asyncio.sleep(0.1)


# ─── !voteskip ────────────────────────────────────────────────────────────────

async def handle_voteskip(bot: "BaseBot", user: "User", _args: list) -> None:
    """!voteskip — cast a vote to skip the currently playing song (public)."""
    loop = asyncio.get_running_loop()
    np   = await loop.run_in_executor(None, azura.fetch_nowplaying)

    if not np:
        await _w(bot, user.id, "🎵 Nothing is playing right now.")
        return

    s       = ((np.get("now_playing") or {}).get("song") or {})
    song_id = (s.get("id") or "").strip()
    art     = (s.get("artist") or "").strip()
    ttl     = (s.get("title")  or "").strip()
    label   = (f"{art} — {ttl}" if art else ttl)[:50]

    if not song_id:
        await _w(bot, user.id, "🎵 Nothing is playing right now.")
        return

    thresh = cs.voteskip_threshold()

    with _vote_lock:
        if song_id not in _vote_skip:
            _vote_skip[song_id] = set()

        if user.id in _vote_skip[song_id]:
            cur  = len(_vote_skip[song_id])
            need = thresh - cur
            await _w(bot, user.id, f"👎 Already voted. {need} more vote(s) needed to skip.")
            return

        _vote_skip[song_id].add(user.id)
        votes = len(_vote_skip[song_id])

    if votes >= thresh:
        await ann.announce_voteskip_passed(bot, votes, thresh, label)
        await loop.run_in_executor(None, azura.skip_current)
        with _vote_lock:
            _vote_skip.pop(song_id, None)
    else:
        await ann.announce_voteskip_progress(bot, user.username, votes, thresh, label)


# ─── !vibes ───────────────────────────────────────────────────────────────────

async def handle_vibes(bot: "BaseBot", user: "User", _args: list) -> None:
    """!vibes — list all auto-discovered vibes from AzuraCast (DJ_DUDU only)."""
    if not _IS_DJ_BOT:
        return
    _rlog("vibes", "handle_vibes", user.username)

    loop  = asyncio.get_running_loop()
    cache = cs.get_vibe_scan_cache()
    if not cache.get("vibes"):
        await _w(bot, user.id, "🔍 Scanning AzuraCast for vibes…")
        cache = await loop.run_in_executor(None, azura.scan_vibe_sources)
        cs.set_vibe_scan_cache(cache)

    vibes = cache.get("vibes", {})
    if not vibes:
        await _w(bot, user.id, "❌ No vibes found. Check AzuraCast or run !vibescan.")
        return

    current = cs.vibe()
    names   = []
    for key in sorted(vibes.keys()):
        display = vibes[key].get("display", key.title())
        marker  = " ◀" if key == current else ""
        names.append(f"{display}{marker}")

    msg = "🎶 Vibes: " + ", ".join(names) + " | !vibe <name>"
    await _w(bot, user.id, msg)


# ─── !vibe ────────────────────────────────────────────────────────────────────

# Short-form alias map: normalized input → normalized canonical key.
# These are convenience shortcuts — the cache is the primary lookup.
# Normalized = re.sub(r"[\s\-_]+", "", s.lower())
_VIBE_ALIAS_MAP: "dict[str, str]" = {
    "remix":     "party",
    "remixes":   "party",
    "hiphops":   "hiphop",
    "phonks":    "phonk",
    "djsets":    "djset",
    "dj":        "djset",
    "rnb":       "rnb",
    "r&b":       "rnb",
}


async def handle_vibe(bot: "BaseBot", user: "User", args: list) -> None:
    """
    !vibe status   — show current vibe (anyone; DJ_DUDU only)
    !vibe <name>   — switch vibe (admin only; DJ_DUDU only)

    Auto-discovers every playlist and media folder from AzuraCast — no env
    vars needed.  Resolution order:
      1. Alias map (remix → party, dj → djset …)
      2. Exact normalized key match in discovery cache
      3. Prefix / substring fuzzy match in cache
      4. If cache miss: trigger a fresh scan, retry once
    For folder-only entries (no playlist yet), the playlist is created
    automatically and songs are assigned before switching.
    Requests playlist is NEVER disabled.
    """
    if not _IS_DJ_BOT:
        return
    _rlog("vibe", "handle_vibe", user.username)

    sub = " ".join(args[1:]).lower().strip() if len(args) > 1 else "status"

    # ── Status (public) ───────────────────────────────────────────────────────
    if sub == "status":
        v       = cs.vibe()
        price   = cs.request_price()
        req_id  = cs.requests_playlist_id()
        cache   = cs.get_vibe_scan_cache()
        entry   = (cache.get("vibes") or {}).get(v, {})
        pl_id   = entry.get("playlist_id") or cs.vibe_playlist_id(v)
        display = entry.get("display", v.title())
        api_ok  = "✓" if cs.azura_api_ready() else "✗"
        await _w(
            bot, user.id,
            f"📻 Vibe: {display} | Price: {price:,} coins | "
            f"API: {api_ok} | Requests: {'✓' if req_id else '✗'} | Playlist: {'✓' if pl_id else '✗'}",
        )
        return

    # ── Guard: admin/owner only for switching ────────────────────────────────
    if not is_admin(user.username):
        await _w(bot, user.id, "❌ Admins only.")
        return

    if not cs.azura_api_ready():
        await _w(bot, user.id, "📻 AzuraCast API not configured (AZURA_BASE_URL / AZURA_API_KEY).")
        return

    loop = asyncio.get_running_loop()

    # ── Normalise input ───────────────────────────────────────────────────────
    sub_norm  = re.sub(r"[\s\-_]+", "", sub)      # "dj set" → "djset"
    canonical = _VIBE_ALIAS_MAP.get(sub_norm, sub_norm)

    # ── Load / refresh cache ──────────────────────────────────────────────────
    cache = cs.get_vibe_scan_cache()
    if not cache.get("vibes"):
        await _w(bot, user.id, "🔍 Scanning AzuraCast for vibes…")
        cache = await loop.run_in_executor(None, azura.scan_vibe_sources)
        cs.set_vibe_scan_cache(cache)

    vibes = cache.get("vibes", {})

    # ── Resolve entry: exact → fuzzy → rescan once ───────────────────────────
    def _find_entry(v_map: dict, key: str) -> "tuple[str, dict | None]":
        if key in v_map:
            return key, v_map[key]
        for k, v in v_map.items():
            if k.startswith(key) or key.startswith(k):
                return k, v
        return key, None

    canonical, entry = _find_entry(vibes, canonical)

    if entry is None and not cs.vibe_scan_is_fresh():
        await _w(bot, user.id, "🔄 Refreshing vibe list…")
        cache = await loop.run_in_executor(None, azura.scan_vibe_sources)
        cs.set_vibe_scan_cache(cache)
        vibes = cache.get("vibes", {})
        canonical, entry = _find_entry(vibes, canonical)

    if entry is None:
        known = ", ".join(sorted(vibes.keys())) or "none — try !vibescan"
        await _w(bot, user.id, f"❌ Unknown vibe '{sub}'. Available: {known}"[:249])
        return

    label = entry.get("display", canonical.title())
    pid   = entry.get("playlist_id")

    # ── Resolve playlist ID (create from folder if needed) ────────────────────
    if not pid:
        folder_name = entry.get("display", canonical)
        await _w(bot, user.id, f"🔍 Setting up '{label}' playlist…")

        pl_data = await loop.run_in_executor(None, azura.find_playlist_by_name, folder_name)
        if pl_data:
            pid = str(pl_data.get("id", ""))

        if not pid:
            file_count = await loop.run_in_executor(None, azura.count_folder_files, folder_name)
            if file_count == 0:
                await _w(bot, user.id, f"❌ '{label}' folder has no songs yet.")
                return
            pl_data = await loop.run_in_executor(None, azura.create_playlist, folder_name)
            if not pl_data:
                await _w(bot, user.id, f"⚠️ Could not create '{label}' playlist. Check AzuraCast.")
                return
            pid = str(pl_data.get("id", ""))
            await loop.run_in_executor(None, azura.assign_folder_to_playlist, folder_name, pid)

        if pid:
            vibes[canonical]["playlist_id"] = pid
            cs.set_vibe_scan_cache(cache)
            cs.set_dynamic_vibe_playlist(canonical, pid)   # backward compat

    if not pid:
        await _w(bot, user.id, f"⚠️ Could not resolve playlist for '{label}'.")
        return

    # ── Validate song count ───────────────────────────────────────────────────
    count = await loop.run_in_executor(None, azura.get_playlist_media_count, pid)
    if count == 0:
        await _w(bot, user.id, f"❌ '{label}' has no songs yet.")
        return

    # ── Collect every known vibe playlist ID for the disable sweep ────────────
    all_vibe_pids: list = [
        str(v.get("playlist_id")) for v in vibes.values() if v.get("playlist_id")
    ]
    for vn in cs.VIBE_NAMES:                   # env-var backed IDs (backward compat)
        ep = cs.vibe_playlist_id(vn)
        if ep and ep not in all_vibe_pids:
            all_vibe_pids.append(ep)

    # ── Switch ────────────────────────────────────────────────────────────────
    res = await loop.run_in_executor(None, azura.switch_vibe_to, pid, all_vibe_pids)
    cs.set_vibe(canonical)

    mode    = engine.get_playlist_mode()
    note    = "\n(Takes effect when request queue clears.)" if mode == "requests" else ""
    ok_icon = "✅" if res.get("status") == "ok" else "⚠️"
    await _w(bot, user.id, f"{ok_icon} Vibe → {label}\n{count} songs | Requests: ON{note}")
    try:
        await bot.highrise.chat(f"🎶 Now playing: {label} vibes!"[:249])
    except Exception:
        pass
    await ann.announce_vibe_changed(bot, canonical)


# ─── !vibescan ────────────────────────────────────────────────────────────────

async def handle_vibescan(bot: "BaseBot", user: "User", _args: list) -> None:
    """
    !vibescan — admin command; rescans AzuraCast playlists and media folders,
    rebuilds the vibe discovery cache, and reports what was found.
    """
    if not _IS_DJ_BOT:
        return
    _rlog("vibescan", "handle_vibescan", user.username)

    if not is_admin(user.username):
        await _w(bot, user.id, "❌ Admins only.")
        return

    if not cs.azura_api_ready():
        await _w(bot, user.id, "📻 AzuraCast API not configured (AZURA_BASE_URL / AZURA_API_KEY).")
        return

    await _w(bot, user.id, "🔍 Scanning AzuraCast for vibes…")
    loop = asyncio.get_running_loop()
    cs.clear_vibe_scan_cache()
    cache = await loop.run_in_executor(None, azura.scan_vibe_sources)
    cs.set_vibe_scan_cache(cache)

    vibes = cache.get("vibes", {})
    if not vibes:
        await _w(bot, user.id, "❌ No vibes found. Check AzuraCast playlists/folders.")
        return

    pl_count  = sum(1 for v in vibes.values() if v.get("source") == "playlist")
    fld_count = sum(1 for v in vibes.values() if v.get("source") == "folder")
    names     = ", ".join(sorted(vibes.keys()))
    summary   = f"✅ {len(vibes)} vibes ({pl_count} playlists, {fld_count} folders):\n{names}"
    await _w(bot, user.id, summary[:249])


# ─── !queuelimit / !setqueuelimit ─────────────────────────────────────────────

async def handle_queuelimit(bot: "BaseBot", user: "User", _args: list) -> None:
    """!queuelimit [number] — show or set room-wide active queue limit."""
    if len(_args) >= 2:
        await handle_setqueuelimit(bot, user, _args)
        return
    limit = cs.max_active_queue_limit()
    active = rq.active_count()
    diag.log_radio_event(
        "queue_limit_read",
        user_id=user.id,
        username=user.username,
        queue_event="queuelimit",
        limit=limit,
        active=active,
    )
    await _w(bot, user.id, f"📋 Queue limit: {active}/{limit} requests in progress.")


async def handle_setqueuelimit(bot: "BaseBot", user: "User", args: list) -> None:
    """!setqueuelimit <number> — set room-wide active queue limit. Admin/owner only."""
    if not is_admin(user.username):
        await _w(bot, user.id, "❌ Admins only.")
        return

    if len(args) < 2 or not args[1].isdigit():
        limit = cs.max_active_queue_limit()
        if len(args) >= 2:
            diag.log_radio_event(
                "queue_limit_rejected",
                user_id=user.id,
                username=user.username,
                requested=args[1],
                reason="not_numeric",
            )
        await _w(
            bot, user.id,
            f"📋 Queue limit: {limit}.\nUsage: !queuelimit <1-50>",
        )
        return

    raw = int(args[1])
    if raw < 1 or raw > 50:
        diag.log_radio_event(
            "queue_limit_rejected",
            user_id=user.id,
            username=user.username,
            requested=raw,
        )
    n = cs.set_max_active_queue_limit(raw)
    diag.log_radio_event(
        "queue_limit_updated",
        user_id=user.id,
        username=user.username,
        requested=raw,
        limit=n,
    )
    await _w(bot, user.id, f"⚙️ Queue limit set to {n}.")


# ─── !setrequestprice ─────────────────────────────────────────────────────────

async def handle_setrequestprice(bot: "BaseBot", user: "User", args: list) -> None:
    """!setrequestprice <amount>  — set coin price per request (0 = free). Admin+."""
    if not _is_staff(user.username):
        await _w(bot, user.id, "🔒 Staff only.")
        return

    if len(args) < 2 or not args[1].isdigit():
        current = cs.request_price()
        await _w(
            bot, user.id,
            f"💰 Current request price: {current:,} coins.\n"
            f"Usage: !setrequestprice <amount>  (0 = free for all)",
        )
        return

    price = max(0, int(args[1]))
    cs.set_request_price(price)
    label = f"{price:,} coins" if price else "FREE"
    await _w(bot, user.id, f"✅ Request price set to {label}.")
    try:
        await bot.highrise.chat(f"💰 Song request price updated: {label}"[:249])
    except Exception:
        pass


# ─── !radiohelp ───────────────────────────────────────────────────────────────

async def handle_radiohelp(bot: "BaseBot", user: "User", _args: list) -> None:
    """!radiohelp — role-aware command reference (player / staff / admin tiers)."""
    uid   = user.id
    uname = user.username
    print(f"[RADIO_CMD] stage=radio_help user_id={uid!r} username={uname!r}")

    # ── Whisper 1: How Radio Works ────────────────────────────────────────────
    await _w(
        bot, uid,
        "🎧 How Radio Works\n"
        "Auto DJ plays vibe music when no requests are active.\n"
        "Use !play <song> to request a song.\n"
        "Requests play first, then Auto DJ resumes.",
    )
    await asyncio.sleep(0.2)

    # ── Whisper 2: public commands ────────────────────────────────────────────
    await _w(
        bot, uid,
        "💿 !play <song/URL> — Request a song\n"
        "📜 !queue — See request queue\n"
        "🎧 !np — Current song\n"
        "📀 !vibe status — Current vibe\n"
        "⭐ !fav / !favorite — Save current song\n"
        "📂 !playlist — VIP playlists",
    )
    await asyncio.sleep(0.2)

    # ── Whisper 3: more commands + rating (everyone) ──────────────────────────
    await _w(
        bot, uid,
        "🎵 More Commands\n"
        "!pick #  !myrequests  !cancel #\n"
        "👍 !like  !dislike  !ratings\n"
        "💿 !musicshop  !buyplays\n"
        "⚡ !priority song\n"
        "Need help? !radiotutorial",
    )

    await asyncio.sleep(0.2)

    # ── Whisper 4: radio rewards ──────────────────────────────────────────────
    await _w(
        bot, uid,
        "🏆 Radio Rewards\n"
        "Earn pts: request, like, fav, playlists.\n"
        "!radiostats  !toplisteners  !toprequests\n"
        "!topliked  !topdisliked  !radioachievements",
    )

    # ── Whisper 5: staff commands (mod / manager / admin / owner) ────────────
    if can_moderate(uname):
        await asyncio.sleep(0.2)
        await _w(
            bot, uid,
            "🛠️ Staff Radio Commands\n"
            "!skip  !playedby @user  !voters\n"
            "!likeslist  !dislikeslist\n"
            "!queuelimit  !radiocleanup",
        )

    # ── Whisper 4: admin commands (admin / owner only) ───────────────────────
    if is_admin(uname):
        await asyncio.sleep(0.2)
        await _w(
            bot, uid,
            "👑 Admin Radio Commands\n"
            "!vibe genre  !vibescan\n"
            "!setqueuelimit #\n"
            "!setrequestprice #\n"
            "!djannounce on/off",
        )


# ─── !radiotutorial ───────────────────────────────────────────────────────────

async def handle_radiotutorial(bot: "BaseBot", user: "User", _args: list) -> None:
    """!radiotutorial — step-by-step guide sent as whispers (≤249 chars each)."""
    print(f"[RADIO_CMD] stage=radio_tutorial user_id={user.id!r} username={user.username!r}")
    steps = [
        "🎧 DJ DUDU Tutorial (1/7)\n"
        "Step 1: Search for a song\n"
        "→ !play <song name>  e.g. !play blinding lights",

        "🎧 Tutorial (2/7)\n"
        "Step 2: Pick from search results\n"
        "→ !pick 1  (or 2, 3, 4, 5)\n"
        "The song will start downloading!",

        "🎧 Tutorial (3/7)\n"
        "Step 3: Check the queue\n"
        "→ !q   to see what's queued\n"
        "→ !now to see what's playing",

        "🎧 Tutorial (4/7)\n"
        "Step 4: Rate the music\n"
        "→ !like    to like current song\n"
        "→ !dislike to dislike",

        "🎧 Tutorial (5/7)\n"
        "Step 5: Save songs you love\n"
        "→ !fav / !favorite  saves current song\n"
        "→ !favorites        view saved songs\n"
        "→ !playfav #        re-play a saved song",

        "🎧 Tutorial (6/7)\n"
        "Step 6: Priority requests\n"
        "→ !priority <song>  costs 100 Luxe Tickets\n"
        "Plays after current song, before the queue.",

        "🎧 Tutorial (7/7)\n"
        "Step 7: 💿 Song Plays\n"
        "→ !myrequests  see your Song Play balance\n"
        "→ !musicshop   buy more plays\n"
        "New players get 5 free 💿 Song Plays!",
    ]
    for step in steps:
        await _w(bot, user.id, step)
        await asyncio.sleep(0.3)


# ─── !musicshop ───────────────────────────────────────────────────────────────

async def handle_musicshop(bot: "BaseBot", user: "User", _args: list) -> None:
    """!musicshop — show Song Play pricing (two compact whispers)."""
    print(f"[RADIO_CMD] stage=music_shop_view user_id={user.id!r} username={user.username!r}")
    if _is_staff(user.username):
        await _w(bot, user.id, "🛠️ Staff: Unlimited 💿 Song Plays. No shop needed!")
        return
    c = mc.get_credits(user.id, user.username)
    # Message 1: balance + coin packs
    await _w(
        bot, user.id,
        f"🎵 ChillTopia Music Shop\n"
        f"💿 Plays: Free {c['free']} · 👑VIP {c['vip']} · Bought {c['purchased']}\n"
        f"🪙 Chill Coins:\n"
        f"• 1→500  • 5→2400  • 10→4500  • 25→10000",
    )
    await asyncio.sleep(0.2)
    # Message 2: luxe packs + priority
    await _w(
        bot, user.id,
        f"🎟️ Luxe Tickets:\n"
        f"• 1→20  • 5→95  • 10→180  • 25→400\n"
        f"⚡ Priority: 100 🎟️ (jumps the queue)\n"
        f"Buy: !buyplays coins 5  or  !buyplays luxe 5",
    )


# ─── !buyplays / !buyrequests ────────────────────────────────────────────────

_BR_COINS = mc.SHOP_COINS   # {1: 500, 5: 2400, 10: 4500, 25: 10000}
_BR_LUXE  = mc.SHOP_LUXE    # {1: 20,  5: 95,   10: 180,  25: 400}
_BR_VALID = sorted(_BR_COINS.keys())   # [1, 5, 10, 25]


async def handle_buyplays(bot: "BaseBot", user: "User", args: list) -> None:
    """
    !buyplays coins <1|5|10|25>   — buy Song Plays with Chill Coins
    !buyplays luxe  <1|5|10|25>   — buy Song Plays with Luxe Tickets
    Alias: !buyrequests (same handler)
    """
    uid   = user.id
    uname = user.username

    if _is_staff(uname):
        await _w(bot, uid, "🛠️ Staff have unlimited 💿 Song Plays — no purchase needed!")
        return

    packs = "/".join(str(x) for x in _BR_VALID)
    usage = (
        f"💿 Buy Song Plays:\n"
        f"!buyplays coins <{packs}>\n"
        f"!buyplays luxe  <{packs}>\n"
        f"Use !musicshop to see prices."
    )

    if len(args) < 3:
        await _w(bot, uid, usage)
        return

    currency = args[1].lower()
    if currency not in ("coins", "luxe"):
        await _w(bot, uid, usage)
        return

    if not args[2].isdigit():
        await _w(bot, uid, usage)
        return

    amount = int(args[2])
    if amount not in _BR_COINS:
        await _w(
            bot, uid,
            f"❌ Pack size must be {', '.join(str(x) for x in _BR_VALID)}.\n{usage}",
        )
        return

    if currency == "coins":
        price = _BR_COINS[amount]
        ok, err = ps.charge(uid, price)
        if not ok:
            await _w(
                bot, uid,
                f"❌ Not enough 🪙 Chill Coins. Need {price:,}.\n{err[:60]}",
            )
            return
        mc.add_credits(uid, uname, amount, "purchased")
        total = mc.get_credits(uid, uname)["total"]
        print(
            f"[RADIO_CMD] stage=music_shop_purchase"
            f" user_id={uid!r} username={uname!r}"
            f" amount={amount} currency=coins price={price}"
        )
        await _w(
            bot, uid,
            f"✅ Bought {amount} 💿 Song Play{'s' if amount != 1 else ''}!\n"
            f"💿 Plays left: {total}\n"
            f"Cost: {price:,} 🪙 Chill Coins",
        )

    else:  # luxe
        price = _BR_LUXE[amount]
        bal   = get_luxe_balance(uid)
        if bal < price:
            await _w(
                bot, uid,
                f"❌ Not enough 🎟️ Luxe Tickets. Need {price}, you have {bal}.",
            )
            return
        if not deduct_luxe_balance(uid, uname, price):
            await _w(bot, uid, "❌ Purchase failed. Try again.")
            return
        try:
            log_luxe_transaction(
                uid, uname, "musicshop_plays", price, "luxe_tickets",
                f"Bought {amount} Song Plays",
            )
        except Exception:
            pass
        mc.add_credits(uid, uname, amount, "purchased")
        total = mc.get_credits(uid, uname)["total"]
        print(
            f"[RADIO_CMD] stage=music_shop_purchase"
            f" user_id={uid!r} username={uname!r}"
            f" amount={amount} currency=luxe price={price}"
        )
        await _w(
            bot, uid,
            f"✅ Bought {amount} 💿 Song Play{'s' if amount != 1 else ''}!\n"
            f"💿 Plays left: {total}\n"
            f"Cost: {price} 🎟️ Luxe Tickets",
        )


async def handle_buyrequests(bot: "BaseBot", user: "User", args: list) -> None:
    """!buyrequests — backward-compat alias for !buyplays."""
    await handle_buyplays(bot, user, args)


# ─── !like ────────────────────────────────────────────────────────────────────

async def handle_like(bot: "BaseBot", user: "User", _args: list) -> None:
    """!like — like the currently playing AzuraCast track."""
    loop  = asyncio.get_running_loop()
    track = await loop.run_in_executor(None, _azura_track)
    if not track:
        await _w(bot, user.id, "🎵 Nothing playing right now. Try !np to check the stream.")
        return
    wait = _LIKE_CD_SECS - int(time.time() - _like_cd.get(user.id, 0))
    if wait > 0:
        await _w(bot, user.id, f"⏳ Wait {wait}s before voting again.")
        return
    _like_cd[user.id] = time.time()
    result = _rate(user.id, user.username, track["key"], "like")
    counts = _ratings(track["key"])
    score  = f"👍 {counts['likes']} | 👎 {counts['dislikes']}"
    title  = track["title"][:40]
    print(
        f"{_LOG} stage=vote_like"
        f" user={user.username!r} song={track['key'][:40]!r}"
        f" result={result!r}"
    )
    if result == "same":
        await _w(bot, user.id, f"👍 Already liked: {title}\n{score}")
        return
    elif result == "changed":
        await _w(bot, user.id, f"👍 Changed to like: {title}\n{score}")
    elif result == "added":
        await _w(bot, user.id, f"👍 Liked: {title}\n{score}")
        rr.record_reward(user.id, user.username, "like", song_key=track["key"])
        asyncio.create_task(_ra.check_radio_achievements(bot, user.id, user.username))
    else:
        await _w(bot, user.id, "⚠️ Could not save rating. Try again.")
        return
    if result in ("added", "changed"):
        mode = db.get_room_setting("vote_broadcast_mode", "public")
        if mode == "public":
            try:
                await bot.highrise.chat(f"👍 @{user.username} liked: {title}")
            except Exception:
                pass


# ─── !dislike ─────────────────────────────────────────────────────────────────

async def handle_dislike(bot: "BaseBot", user: "User", _args: list) -> None:
    """!dislike — dislike the currently playing AzuraCast track."""
    loop  = asyncio.get_running_loop()
    track = await loop.run_in_executor(None, _azura_track)
    if not track:
        await _w(bot, user.id, "🎵 Nothing playing right now. Try !np to check the stream.")
        return
    wait = _LIKE_CD_SECS - int(time.time() - _like_cd.get(user.id, 0))
    if wait > 0:
        await _w(bot, user.id, f"⏳ Wait {wait}s before voting again.")
        return
    _like_cd[user.id] = time.time()
    result = _rate(user.id, user.username, track["key"], "dislike")
    counts = _ratings(track["key"])
    score  = f"👍 {counts['likes']} | 👎 {counts['dislikes']}"
    title  = track["title"][:40]
    print(
        f"{_LOG} stage=vote_dislike"
        f" user={user.username!r} song={track['key'][:40]!r}"
        f" result={result!r}"
    )
    if result == "same":
        await _w(bot, user.id, f"👎 Already disliked: {title}\n{score}")
        return
    elif result == "changed":
        await _w(bot, user.id, f"👎 Changed to dislike: {title}\n{score}")
    elif result == "added":
        await _w(bot, user.id, f"👎 Disliked: {title}\n{score}")
        rr.record_reward(user.id, user.username, "dislike", song_key=track["key"])
    else:
        await _w(bot, user.id, "⚠️ Could not save rating. Try again.")
        return
    if result in ("added", "changed"):
        mode = db.get_room_setting("vote_broadcast_mode", "public")
        if mode == "public":
            try:
                await bot.highrise.chat(f"👎 @{user.username} disliked: {title}")
            except Exception:
                pass


# ─── !likes / !votes [public|private] ────────────────────────────────────────

async def handle_likes(bot: "BaseBot", user: "User", _args: list) -> None:
    """!likes — show vote counts. !votes public|private — change broadcast mode (staff)."""
    if len(_args) >= 2 and _args[1].lower() in ("public", "private"):
        if not _is_staff(user.username):
            await _w(bot, user.id, "❌ Staff only.")
            return
        mode = _args[1].lower()
        db.set_room_setting("vote_broadcast_mode", mode)
        print(
            f"{_LOG} stage=vote_mode_change"
            f" user={user.username!r} result={mode!r}"
        )
        await _w(bot, user.id, f"✅ Vote broadcasts: {mode.upper()}")
        return
    loop  = asyncio.get_running_loop()
    track = await loop.run_in_executor(None, _azura_track)
    if not track:
        await _w(bot, user.id, "🎵 Nothing is playing right now.")
        return
    counts = _ratings(track["key"])
    title  = track["title"][:50]
    await _w(bot, user.id, f"👍 {counts['likes']} | 👎 {counts['dislikes']}\n{title}")


# ─── Top songs / top requesters DB helpers ────────────────────────────────────

def _top_songs_db(limit: int = 5) -> list:
    """Most liked songs (song_key, count) ordered by likes desc."""
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT song_key, COUNT(*) AS cnt "
                "FROM dj_ratings WHERE rating='like' "
                "GROUP BY song_key ORDER BY cnt DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [{"key": r[0], "count": r[1]} for r in rows]
    except Exception:
        return []


def _top_requesters_db(limit: int = 5) -> list:
    """Users whose requested songs received the most likes (joined via title)."""
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT rj.username, COUNT(*) AS cnt "
                "FROM dj_ratings dr "
                "JOIN yt_request_jobs rj "
                "  ON LOWER(SUBSTR(rj.title, 1, 150)) = dr.song_key "
                "WHERE dr.rating='like' AND rj.username != '' "
                "GROUP BY LOWER(rj.username) ORDER BY cnt DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [{"username": r[0], "count": r[1]} for r in rows]
    except Exception:
        return []


# ─── !topsongs ────────────────────────────────────────────────────────────────

async def handle_topsongs(bot: "BaseBot", user: "User", _args: list) -> None:
    """!topsongs — songs with the most requests/plays."""
    rows = rr.top_songs(limit=5)
    if not rows:
        await _w(bot, user.id, "🔥 No song stats yet.")
        return
    lines = ["🔥 Top Songs"]
    for i, r in enumerate(rows, 1):
        try:
            raw   = str(r.get("title") or r.get("song_key") or "")
            title = rr._prettify(raw) or "Unknown Title"
            title = rr._trunc(title, 28)
            cnt   = int(r.get("count") or 0)
            lines.append(f"{i}. {title} • {cnt} plays")
        except Exception:
            lines.append(f"{i}. (data error)")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ─── !toprequesters ───────────────────────────────────────────────────────────

async def handle_toprequesters(bot: "BaseBot", user: "User", _args: list) -> None:
    """!toprequesters — users with the most successful song requests."""
    rows = rr.top_requesters(limit=5)
    if not rows:
        await _w(bot, user.id, "💿 No requester data yet.")
        return
    lines = ["💿 Top Requesters"]
    for i, r in enumerate(rows, 1):
        try:
            uname = str(r.get("username") or "unknown")[:18]
            cnt   = int(r.get("count") or 0)
            lines.append(f"{i}. @{uname} — {cnt} requests")
        except Exception:
            lines.append(f"{i}. (data error)")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ─── !voters ──────────────────────────────────────────────────────────────────

async def handle_voters(bot: "BaseBot", user: "User", _args: list) -> None:
    """!voters — show all voters for the current track (staff only)."""
    if not _is_staff(user.username):
        await _w(bot, user.id, "❌ Staff only.")
        return
    loop  = asyncio.get_running_loop()
    track = await loop.run_in_executor(None, _azura_track)
    if not track:
        await _w(bot, user.id, "🎵 Nothing is playing right now.")
        return
    key = track["key"]
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT username, rating FROM dj_ratings "
                "WHERE song_key=? ORDER BY rated_at ASC",
                (key,),
            ).fetchall()
    except Exception:
        rows = []
    if not rows:
        await _w(bot, user.id, f"🗳 No votes yet for: {track['title'][:50]}")
        return
    lines = [f"🗳 Votes ({track['title'][:35]}):"]
    for r in rows[:12]:
        icon = "👍" if r[1] == "like" else "👎"
        lines.append(f"{icon} @\u200b{r[0][:15]}")
    if len(rows) > 12:
        lines.append(f"…+{len(rows)-12} more")
    await _w(bot, user.id, "\n".join(lines))


# ─── !likeslist ───────────────────────────────────────────────────────────────

async def handle_likeslist(bot: "BaseBot", user: "User", _args: list) -> None:
    """!likeslist — show usernames who liked the current track (staff only)."""
    if not _is_staff(user.username):
        await _w(bot, user.id, "❌ Staff only.")
        return
    loop  = asyncio.get_running_loop()
    track = await loop.run_in_executor(None, _azura_track)
    if not track:
        await _w(bot, user.id, "🎵 Nothing is playing right now.")
        return
    key = track["key"]
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT username FROM dj_ratings "
                "WHERE song_key=? AND rating='like' ORDER BY rated_at ASC",
                (key,),
            ).fetchall()
    except Exception:
        rows = []
    if not rows:
        await _w(bot, user.id, f"👍 No likes yet for: {track['title'][:50]}")
        return
    names = ", ".join(f"@\u200b{r[0]}" for r in rows[:15])
    suffix = f" (+{len(rows)-15} more)" if len(rows) > 15 else ""
    await _w(bot, user.id, f"👍 Liked by: {names}{suffix}")


# ─── !dislikeslist ────────────────────────────────────────────────────────────

async def handle_dislikeslist(bot: "BaseBot", user: "User", _args: list) -> None:
    """!dislikeslist — show usernames who disliked the current track (staff only)."""
    if not _is_staff(user.username):
        await _w(bot, user.id, "❌ Staff only.")
        return
    loop  = asyncio.get_running_loop()
    track = await loop.run_in_executor(None, _azura_track)
    if not track:
        await _w(bot, user.id, "🎵 Nothing is playing right now.")
        return
    key = track["key"]
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT username FROM dj_ratings "
                "WHERE song_key=? AND rating='dislike' ORDER BY rated_at ASC",
                (key,),
            ).fetchall()
    except Exception:
        rows = []
    if not rows:
        await _w(bot, user.id, f"👎 No dislikes yet for: {track['title'][:50]}")
        return
    names = ", ".join(f"@\u200b{r[0]}" for r in rows[:15])
    suffix = f" (+{len(rows)-15} more)" if len(rows) > 15 else ""
    await _w(bot, user.id, f"👎 Disliked by: {names}{suffix}")


# ─── !favorite / !fav / !addtoplaylist ───────────────────────────────────────

async def handle_favorite(bot: "BaseBot", user: "User", _args: list) -> None:
    """!favorite / !fav / !addtoplaylist — save current AzuraCast track to favorites."""
    _rlog("favorite", "handle_favorite", user.username)
    loop  = asyncio.get_running_loop()
    track = await loop.run_in_executor(None, _azura_track)
    if not track:
        await _w(bot, user.id, "🎵 Nothing playing right now. Try !np to check the stream.")
        return
    cp  = rq.currently_playing()
    url = (cp.get("url") or "") if cp else ""
    added = _fav_add(user.id, user.username, track["title"], url, track.get("artist", ""))
    if added:
        await _w(bot, user.id, f"⭐ Saved to favorites: {track['title'][:55]}")
        rr.record_reward(user.id, user.username, "favorite", song_key=track["key"])
        asyncio.create_task(_ra.check_radio_achievements(bot, user.id, user.username))
    else:
        await _w(bot, user.id, f"⭐ Already in your favorites: {track['title'][:50]}")


# ─── !unfavorite ─────────────────────────────────────────────────────────────

async def handle_unfavorite(bot: "BaseBot", user: "User", _args: list) -> None:
    """!unfavorite — remove current AzuraCast track from favorites."""
    _rlog("unfavorite", "handle_unfavorite", user.username)
    loop  = asyncio.get_running_loop()
    track = await loop.run_in_executor(None, _azura_track)
    if not track:
        await _w(bot, user.id, "🎵 Nothing playing right now. Try !np to check the stream.")
        return
    removed = _fav_remove_title(user.id, track["title"])
    if removed:
        await _w(bot, user.id, f"💔 Removed from favorites: {track['title'][:55]}")
    else:
        await _w(bot, user.id, f"⭐ Not in your favorites: {track['title'][:50]}")


# ─── !favorites / !favs / !myplaylist ────────────────────────────────────────

async def handle_favorites(bot: "BaseBot", user: "User", _args: list) -> None:
    """!favorites / !favs — list your saved songs, 4 per page (newest first)."""
    _rlog("favorites", "handle_favorites", user.username)
    rows = _fav_get(user.id, limit=20)
    if not rows:
        await _w(bot, user.id, "⭐ No favorites yet! Use !fav while a song plays.")
        return
    items: list[str] = []
    for i, r in enumerate(rows, 1):
        t = (r.get("title") or "?")[:28]
        a = (r.get("artist") or "")[:14]
        items.append(f"{i}. {t}" + (f" — {a}" if a else ""))
    chunk_size  = 4
    chunks      = [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]
    total_pages = len(chunks)
    for pg, chunk in enumerate(chunks, 1):
        hdr = f"⭐ Favorites {pg}/{total_pages}" if total_pages > 1 else "⭐ Your Favorites"
        msg = hdr + "\n" + "\n".join(chunk)
        await _w(bot, user.id, msg[:249])
        if pg < total_pages:
            await asyncio.sleep(0.1)
    await asyncio.sleep(0.05)
    await _w(bot, user.id, "!playfav <#>  !unfav <#>  !fav")


# ─── !removefavorite <number> ────────────────────────────────────────────────

async def handle_removefavorite(bot: "BaseBot", user: "User", args: list) -> None:
    """!removefavorite / !unfav <#> — remove a saved favorite by list position."""
    _rlog("removefavorite", "handle_removefavorite", user.username)
    if len(args) < 2 or not args[1].isdigit():
        rows = _fav_get(user.id, limit=20)
        if not rows:
            await _w(bot, user.id, "⭐ No favorites yet.")
            return
        items: list[str] = []
        for i, r in enumerate(rows, 1):
            items.append(f"{i}. {(r.get('title') or '?')[:34]}")
        chunk_size  = 4
        chunks      = [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]
        total_pages = len(chunks)
        for pg, chunk in enumerate(chunks, 1):
            hdr = f"⭐ Favs {pg}/{total_pages}" if total_pages > 1 else "⭐ Your Favorites"
            await _w(bot, user.id, hdr + " (!unfav <#>)\n" + "\n".join(chunk))
            if pg < total_pages:
                await asyncio.sleep(0.1)
        return
    pos   = int(args[1])
    title = _fav_remove_by_pos(user.id, pos)
    if title:
        await _w(bot, user.id, f"💔 Removed #{pos}: {title[:55]}")
    else:
        await _w(bot, user.id, f"⚠️ No favorite #{pos}. Use !favorites to see your list.")


# ─── !save ───────────────────────────────────────────────────────────────────

async def handle_save(bot: "BaseBot", user: "User", args: list) -> None:
    """!save — alias for !favorite: save current playing song with artist + URL."""
    await handle_favorite(bot, user, args)


# ─── !mysongs ─────────────────────────────────────────────────────────────────

async def handle_mysongs(bot: "BaseBot", user: "User", args: list) -> None:
    """!mysongs [page] — paginated list of saved songs (5 per page), newest first."""
    _rlog("mysongs", "handle_mysongs", user.username)
    per   = 5
    page  = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
    rows  = _fav_get(user.id, limit=20)
    if not rows:
        await _w(bot, user.id, "⭐ No saved songs. Use !save while a song plays.")
        return
    total  = len(rows)
    pages  = (total + per - 1) // per
    start  = (page - 1) * per
    chunk  = rows[start:start + per]
    if not chunk:
        await _w(bot, user.id, f"⭐ Page {page} of {pages}. Use !mysongs <page>.")
        return
    lines = [f"⭐ My songs p{page}/{pages}:"]
    for i, r in enumerate(chunk, start + 1):
        t = (r.get("title") or "?")[:30]
        a = (r.get("artist") or "")[:18]
        if a:
            lines.append(f"{i}. {t} — {a}")
        else:
            lines.append(f"{i}. {t}")
    lines.append("!playmine <#> to request")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ─── !removefav ──────────────────────────────────────────────────────────────

async def handle_removefav(bot: "BaseBot", user: "User", args: list) -> None:
    """!removefav <#> — alias for !removefavorite."""
    await handle_removefavorite(bot, user, args)


async def _playfav_youtube_fallback(bot: "BaseBot", user: "User", fav: dict, pos: int) -> None:
    """
    Rehydrate a source-less favorite by searching YouTube from saved metadata,
    then submit through the normal request pipeline.
    """
    title = (fav.get("title") or "").strip()
    artist = (fav.get("artist") or "").strip()
    query = " ".join(p for p in (title, artist) if p).strip()
    if not query:
        await _w(bot, user.id, f"⚠️ Favorite #{pos} has no title to search.")
        return

    await _w(bot, user.id, f"🔍 Finding saved favorite: {query[:54]}")
    loop = asyncio.get_running_loop()
    try:
        results = await loop.run_in_executor(None, rq.search_yt, query, 5)
    except Exception as exc:
        print(f"{_LOG} playfav fallback search error: {exc!r}")
        await _w(bot, user.id, "❌ Could not search YouTube for that favorite.")
        return

    if not results:
        await _w(bot, user.id, f"❌ No YouTube match found for '{title[:40]}'.")
        return

    picked = next(
        (r for r in results if int(r.get("duration_secs") or 0) <= cs.MAX_DURATION_SECS),
        results[0],
    )
    url = (picked.get("url") or "").strip()
    if not url:
        await _w(bot, user.id, f"❌ Search found '{title[:40]}' but no playable URL.")
        return

    ok = await _submit_url(
        bot, user, url,
        metadata={
            "title": picked.get("title") or title,
            "artist": artist,
        },
    )
    if ok:
        _fav_remember_youtube_source(fav.get("id") or 0, user.id, url, artist)


# ─── !playfav ─────────────────────────────────────────────────────────────────

async def handle_playfav(bot: "BaseBot", user: "User", args: list) -> None:
    """!playfav <#> — queue a specific favorite by number."""
    _rlog("playfav", "handle_playfav", user.username)
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !playfav <#>  (see !favs for your list)")
        return
    pos  = int(args[1])
    rows = _fav_get(user.id, limit=20)
    if not rows:
        await _w(bot, user.id, "⭐ No favorites yet. Use !fav while a song plays.")
        return
    if pos < 1 or pos > len(rows):
        await _w(bot, user.id,
                 f"❌ Favorite #{pos} not found. You have {len(rows)}. Use !favs.")
        return
    fav = rows[pos - 1]
    t   = (fav.get("title") or "?")[:34]
    url = (fav.get("url") or "").strip()
    if url:
        await _submit_url(
            bot, user, url,
            metadata={"title": fav["title"], "artist": fav.get("artist", "")},
        )
    elif fav.get("azura_file_id"):
        # Local AzuraCast file — run through local replay pipeline with same
        # credit/cooldown/capacity rules as a YouTube request.
        uid    = user.id
        uname  = user.username
        is_stf = _is_staff(uname)

        # Cooldown (mirrors _submit_url)
        if not is_stf:
            cd      = cs.cooldown_secs()
            elapsed = time.time() - _cooldowns.get(uid, 0)
            if elapsed < cd:
                await _w(bot, uid,
                    f"⏳ Cooldown: {int(cd - elapsed)}s remaining. Please wait.")
                return

        # Queue capacity (mirrors _submit_url)
        active_now = rq.active_count()
        max_queue = cs.max_active_queue_limit()
        if active_now >= max_queue:
            await _w(bot, uid,
                f"📋 Queue is full ({active_now}/{max_queue} requests in progress). Please wait.")
            return

        # Per-user queue limit (mirrors _submit_url)
        if not is_stf:
            limit = cs.per_user_queue_limit()
            if limit > 0 and rq.user_active_count(uid) >= limit:
                await _w(bot, uid,
                    f"📋 You already have {rq.user_active_count(uid)} song(s) queued.")
                return

        # Music request credit (mirrors _submit_url)
        _cr_consumed = False
        _plays_left_after = None
        if not is_stf:
            if not mc.has_credits(uid, uname):
                await _w(bot, uid,
                    "❌ Out of 💿 Song Plays! Use !musicshop to buy more.\n"
                    "New players get 5 free plays. Packs from 500 🪙 or 20 🎟️")
                return
            if not mc.consume_credit(uid, uname):
                await _w(bot, uid,
                    "❌ Could not consume Song Play credit. Try again.")
                return
            _cr_consumed = True
            try:
                _plays_left_after = mc.get_credits(uid, uname)["total"]
            except Exception:
                _plays_left_after = None

        price = ps.request_cost_for(uname)
        ok, err = ps.charge(uid, price)
        if not ok:
            if _cr_consumed:
                mc.refund_credit(uid, uname)
            await _w(bot, uid, f"💸 {err}")
            return

        _cooldowns[uid] = time.time()
        _pos = rq.future_count() + 1

        from modules.local_replay import queue_local_fav as _ql
        await _ql(
            bot, user, fav, pos,
            credit_consumed=_cr_consumed,
            coins_charged=price,
            payment_type="paid" if price > 0 else "free",
            queue_position=_pos,
            staff_free=is_stf,
            plays_left=_plays_left_after,
        )
    else:
        await _playfav_youtube_fallback(bot, user, fav, pos)


# ─── !playmine ────────────────────────────────────────────────────────────────

async def handle_playmine(bot: "BaseBot", user: "User", args: list) -> None:
    """!playmine <#> — re-request a saved song from your playlist by list position."""
    _rlog("playmine", "handle_playmine", user.username)
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !playmine <#>  (see !mysongs for your list)")
        return
    pos  = int(args[1])
    rows = _fav_get(user.id, limit=20)
    if pos < 1 or pos > len(rows):
        await _w(
            bot, user.id,
            f"⚠️ No saved song #{pos}. You have {len(rows)}. Use !mysongs.",
        )
        return
    fav = rows[pos - 1]
    url = (fav.get("url") or "").strip()
    if not url:
        await _w(
            bot, user.id,
            f"⚠️ '{fav['title'][:40]}' has no URL stored. Search with !request instead.",
        )
        return
    await _submit_url(bot, user, url, metadata={"title": fav["title"], "artist": fav.get("artist", "")})


# ─── !myrequests ─────────────────────────────────────────────────────────────

async def handle_myrequests(bot: "BaseBot", user: "User", _args: list) -> None:
    """!myrequests — show Song Play credit balance."""
    _rlog("myrequests", "handle_myrequests", user.username)
    if _is_staff(user.username):
        await _w(
            bot, user.id,
            "💿 Song Plays: Unlimited\n"
            "🛠️ Staff: Free\n"
            "⚡ Priority: Free (Staff)",
        )
        return
    c = mc.get_credits(user.id, user.username)
    await _w(
        bot, user.id,
        f"💿 Song Plays:\n"
        f"Free: {c['free']}\n"
        f"👑 VIP: {c['vip']}\n"
        f"Bought: {c['purchased']}\n"
        f"⚡ Priority: 100 🎟️\n"
        f"Buy more: !musicshop",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# VIP PERSONAL RADIO PLAYLISTS
# ═══════════════════════════════════════════════════════════════════════════════

_PL_MAX      = 5    # max playlists per VIP user
_PL_SONG_MAX = 25   # max songs per playlist
_PL_NAME_LEN = 24   # max playlist name length
_PL_NAME_RE  = re.compile(r"^[\w][\w\s\-]*$")


def _pl_name_ok(name: str) -> bool:
    name = name.strip()
    return bool(_PL_NAME_RE.match(name)) and 1 <= len(name) <= _PL_NAME_LEN


def _pl_is_vip(user_id: str, username: str) -> bool:
    """True for VIP, manager, admin, or owner."""
    return _is_staff(username) or db.owns_item(user_id, "vip")


def _pl_list(user_id: str) -> list:
    """Return [{id, name, count}, …] for user's playlists."""
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT p.id, p.name, COUNT(s.id) AS cnt "
                "FROM radio_playlists p "
                "LEFT JOIN radio_playlist_songs s ON s.playlist_id = p.id "
                "WHERE p.user_id=? GROUP BY p.id ORDER BY p.created_at ASC",
                (user_id,),
            ).fetchall()
            return [{"id": r[0], "name": r[1], "count": r[2]} for r in rows]
    except Exception:
        return []


def _pl_get(user_id: str, name: str) -> "dict | None":
    """Get playlist by name (case-insensitive). Returns {id, name} or None."""
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                "SELECT id, name FROM radio_playlists "
                "WHERE user_id=? AND LOWER(name)=LOWER(?)",
                (user_id, name.strip()),
            ).fetchone()
            return {"id": row[0], "name": row[1]} if row else None
    except Exception:
        return None


def _pl_create(user_id: str, username: str, name: str) -> str:
    """Returns 'created'|'limit'|'duplicate'|'invalid'|'error'."""
    name = name.strip()
    if not _pl_name_ok(name):
        return "invalid"
    if len(_pl_list(user_id)) >= _PL_MAX:
        return "limit"
    if _pl_get(user_id, name):
        return "duplicate"
    try:
        with db.db_conn() as conn:
            conn.execute(
                "INSERT INTO radio_playlists (user_id, username, name) VALUES (?,?,?)",
                (user_id, username.lower(), name),
            )
        return "created"
    except Exception:
        return "error"


def _pl_delete(user_id: str, name: str) -> bool:
    pl = _pl_get(user_id, name)
    if not pl:
        return False
    try:
        with db.db_conn() as conn:
            conn.execute("DELETE FROM radio_playlists WHERE id=?", (pl["id"],))
        return True
    except Exception:
        return False


def _pl_rename(user_id: str, old: str, new: str) -> str:
    """Returns 'renamed'|'not_found'|'duplicate'|'invalid'|'error'."""
    new = new.strip()
    if not _pl_name_ok(new):
        return "invalid"
    pl = _pl_get(user_id, old)
    if not pl:
        return "not_found"
    if _pl_get(user_id, new):
        return "duplicate"
    try:
        with db.db_conn() as conn:
            conn.execute(
                "UPDATE radio_playlists SET name=?, updated_at=datetime('now') WHERE id=?",
                (new, pl["id"]),
            )
        return "renamed"
    except Exception:
        return "error"


def _pl_songs(playlist_id: int, limit: int = 25) -> list:
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT id, source_type, title, artist, youtube_url, "
                "COALESCE(video_id,''), COALESCE(azura_song_id,''), COALESCE(azura_file_id,'') "
                "FROM radio_playlist_songs WHERE playlist_id=? "
                "ORDER BY position ASC, added_at ASC LIMIT ?",
                (playlist_id, limit),
            ).fetchall()
            return [
                {"id": r[0], "source_type": r[1], "title": r[2],
                 "artist": r[3], "youtube_url": r[4], "video_id": r[5],
                 "azura_song_id": r[6], "azura_file_id": r[7]}
                for r in rows
            ]
    except Exception:
        return []


def _pl_song_count(playlist_id: int) -> int:
    try:
        with db.db_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM radio_playlist_songs WHERE playlist_id=?",
                (playlist_id,),
            ).fetchone()[0]
    except Exception:
        return 0


def _pl_song_add(
    playlist_id: int,
    user_id: str,
    source_type: str,
    title: str,
    artist: str,
    youtube_url: str,
    video_id: str,
    azura_song_id: str,
    azura_file_id: str,
) -> str:
    """Returns 'added'|'duplicate'|'limit'|'error'."""
    if _pl_song_count(playlist_id) >= _PL_SONG_MAX:
        return "limit"
    try:
        with db.db_conn() as conn:
            if youtube_url:
                dup = conn.execute(
                    "SELECT id FROM radio_playlist_songs "
                    "WHERE playlist_id=? AND youtube_url=?",
                    (playlist_id, youtube_url),
                ).fetchone()
            elif azura_song_id:
                dup = conn.execute(
                    "SELECT id FROM radio_playlist_songs "
                    "WHERE playlist_id=? AND azura_song_id=? AND azura_song_id!=''",
                    (playlist_id, azura_song_id),
                ).fetchone()
            else:
                dup = conn.execute(
                    "SELECT id FROM radio_playlist_songs "
                    "WHERE playlist_id=? AND LOWER(title)=LOWER(?)"
                    " AND LOWER(artist)=LOWER(?)",
                    (playlist_id, title, artist),
                ).fetchone()
            if dup:
                return "duplicate"
            pos = _pl_song_count(playlist_id)
            conn.execute(
                "INSERT INTO radio_playlist_songs "
                "(playlist_id, user_id, source_type, title, artist, youtube_url, "
                " video_id, azura_song_id, azura_file_id, position) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (playlist_id, user_id, source_type, title[:120], artist[:80],
                 youtube_url, video_id, azura_song_id, azura_file_id, pos),
            )
        return "added"
    except Exception:
        return "error"


def _pl_song_remove(playlist_id: int, pos: int) -> "str | None":
    """Remove nth (1-indexed) song. Returns title or None."""
    songs = _pl_songs(playlist_id)
    if pos < 1 or pos > len(songs):
        return None
    song = songs[pos - 1]
    try:
        with db.db_conn() as conn:
            conn.execute("DELETE FROM radio_playlist_songs WHERE id=?", (song["id"],))
        return song["title"]
    except Exception:
        return None


_YT_RE_PL = re.compile(
    r"^https?://(?:www\.)?"
    r"(?:youtube\.com/watch\?(?:.*&)?v=[\w\-]{11}"
    r"|youtu\.be/[\w\-]{11}"
    r"|youtube\.com/shorts/[\w\-]{11})"
)


def _is_yt_url_pl(url: str) -> bool:
    return bool(_YT_RE_PL.match(url.strip())) and not _is_youtube_playlist_url(url)


# ─── !playlist / !pl ──────────────────────────────────────────────────────────

async def handle_playlist(bot: "BaseBot", user: "User", args: list) -> None:
    """!playlist / !pl — VIP personal radio playlists.
    Subcommands: create, songs, add, addcurrent, rename, remove, delete, play.
    No subcommand: list your playlists.
    """
    sub   = args[1].lower() if len(args) > 1 else ""
    uid   = user.id
    uname = user.username

    _WRITE_SUBS = frozenset(("create", "add", "addcurrent", "rename",
                              "remove", "delete", "del"))
    _ALL_SUBS   = _WRITE_SUBS | {"songs", "play"}

    async def _show_playlist(pl: dict) -> None:
        songs = _pl_songs(pl["id"])
        if not songs:
            await _w(bot, uid,
                     f"📂 {pl['name'][:22]} is empty.\n"
                     "!playlist add <name> <YouTube URL>")
            return
        items = []
        for i, s in enumerate(songs, 1):
            t = (s.get("title") or "?")[:28]
            src = "📺" if s.get("source_type") == "youtube" else "📻"
            items.append(f"{i}. {src} {t}")
        chunks = [items[i : i + 4] for i in range(0, len(items), 4)]
        for pg, chunk in enumerate(chunks, 1):
            hdr = (f"📂 {pl['name'][:17]} {pg}/{len(chunks)}"
                   if len(chunks) > 1 else f"📂 {pl['name'][:24]}")
            await _w(bot, uid, hdr + "\n" + "\n".join(chunk))
            if pg < len(chunks):
                await asyncio.sleep(0.1)
        await asyncio.sleep(0.05)
        pn = pl["name"][:16]
        await _w(bot, uid,
                 f"!pl {pn} <#>\n"
                 f"!playlist addcurrent {pn}\n"
                 f"!playlist remove {pn} <#>")

    async def _queue_playlist_song(pl: dict, song_idx: int) -> None:
        songs = _pl_songs(pl["id"])
        if song_idx < 1 or song_idx > len(songs):
            await _w(bot, uid, f"❌ Song #{song_idx} not found in playlist.")
            return
        s = songs[song_idx - 1]
        t = (s.get("title") or "?")[:50]
        a = (s.get("artist") or "")[:28]
        if s.get("youtube_url"):
            await _submit_url(
                bot, user, s["youtube_url"],
                metadata={"title": s.get("title", ""), "artist": s.get("artist", "")},
            )
            return
        if s.get("azura_file_id"):
            is_stf = _is_staff(uname)
            if not is_stf:
                cd = cs.cooldown_secs()
                elapsed = time.time() - _cooldowns.get(uid, 0)
                if elapsed < cd:
                    await _w(bot, uid, f"⏳ Cooldown: {int(cd - elapsed)}s remaining. Please wait.")
                    return
            active_now = rq.active_count()
            max_queue = cs.max_active_queue_limit()
            if active_now >= max_queue:
                await _w(bot, uid,
                    f"📋 Queue is full ({active_now}/{max_queue} requests in progress). Please wait.")
                return
            if not is_stf:
                limit = cs.per_user_queue_limit()
                if limit > 0 and rq.user_active_count(uid) >= limit:
                    await _w(bot, uid, f"📋 You already have {rq.user_active_count(uid)} song(s) queued.")
                    return
            credit_used = False
            plays_left = None
            if not is_stf:
                if not mc.has_credits(uid, uname):
                    await _w(bot, uid, "❌ Out of 💿 Song Plays! Use !musicshop to buy more.")
                    return
                if not mc.consume_credit(uid, uname):
                    await _w(bot, uid, "❌ Could not consume Song Play credit. Try again.")
                    return
                credit_used = True
                try:
                    plays_left = mc.get_credits(uid, uname)["total"]
                except Exception:
                    plays_left = None
            price = ps.request_cost_for(uname)
            ok, err = ps.charge(uid, price)
            if not ok:
                if credit_used:
                    mc.refund_credit(uid, uname)
                await _w(bot, uid, f"💸 {err}")
                return
            _cooldowns[uid] = time.time()
            from modules.local_replay import queue_local_fav as _ql
            await _ql(
                bot, user,
                {
                    "id": s.get("id", 0),
                    "title": s.get("title", ""),
                    "artist": s.get("artist", ""),
                    "azura_file_id": s.get("azura_file_id", ""),
                    "azura_song_id": s.get("azura_song_id", ""),
                },
                song_idx,
                credit_consumed=credit_used,
                coins_charged=price,
                payment_type="paid" if price > 0 else "free",
                queue_position=rq.future_count() + 1,
                staff_free=is_stf,
                plays_left=plays_left,
            )
            return
        await _playfav_youtube_fallback(
            bot, user,
            {"id": 0, "title": t, "artist": a},
            song_idx,
        )

    # ── No sub / unrecognised → list playlists ───────────────────────────────
    if sub not in _ALL_SUBS:
        if sub:
            song_idx = None
            name_args = args[1:]
            if len(name_args) >= 2 and name_args[-1].isdigit():
                song_idx = int(name_args[-1])
                name_args = name_args[:-1]
            pl = _pl_get(uid, " ".join(name_args))
            if pl:
                if song_idx is None:
                    await _show_playlist(pl)
                else:
                    await _queue_playlist_song(pl, song_idx)
                return
        playlists = _pl_list(uid)
        if not playlists:
            await _w(bot, uid,
                     "📂 No playlists yet.\n"
                     "VIP: !playlist create <name>\n"
                     "!buyvip to unlock VIP.")
            return
        items = [f"{i}. {p['name'][:22]} ({p['count']} songs)"
                 for i, p in enumerate(playlists, 1)]
        chunks = [items[i : i + 4] for i in range(0, len(items), 4)]
        for pg, chunk in enumerate(chunks, 1):
            hdr = (f"📂 Playlists {pg}/{len(chunks)}"
                   if len(chunks) > 1 else "📂 Your Playlists")
            await _w(bot, uid, hdr + "\n" + "\n".join(chunk))
            if pg < len(chunks):
                await asyncio.sleep(0.1)
        await asyncio.sleep(0.05)
        await _w(bot, uid,
                 "!playlist <name>\n"
                 "!pl <name> <#>\n"
                 "!playlist create <name>")
        return

    # ── VIP gate for all write operations ────────────────────────────────────
    if sub in _WRITE_SUBS and not _pl_is_vip(uid, uname):
        await _w(bot, uid,
                 "🔒 VIP only. !buyvip to create & manage radio playlists.")
        return

    # ── !playlist create <name> ──────────────────────────────────────────────
    if sub == "create":
        if len(args) < 3:
            await _w(bot, uid, "Usage: !playlist create <name>  (max 24 chars)")
            return
        name   = " ".join(args[2:])
        result = _pl_create(uid, uname, name)
        msgs = {
            "created":   f"✅ Created playlist: {name[:24]}",
            "duplicate": f"⭐ Playlist '{name[:24]}' already exists.",
            "limit":     f"❌ Max {_PL_MAX} playlists reached.",
            "invalid":   "⚠️ Name: 1-24 chars, letters/numbers/spaces/-/_",
            "error":     "❌ Could not create playlist. Try again.",
        }
        await _w(bot, uid, msgs.get(result, "❌ Error."))
        if result == "created":
            rr.record_reward(uid, uname, "playlist_create",
                             target_key=name.strip().lower()[:150])
            asyncio.create_task(_ra.check_radio_achievements(bot, uid, uname))
        return

    # ── Remaining subs need playlist name ────────────────────────────────────
    if len(args) < 3:
        await _w(bot, uid, f"Usage: !playlist {sub} <playlist name>")
        return

    # ── !playlist songs <name> ───────────────────────────────────────────────
    if sub == "songs":
        pl = _pl_get(uid, " ".join(args[2:]))
        if not pl:
            await _w(bot, uid, f"❌ Playlist not found: {' '.join(args[2:])[:22]}")
            return
        await _show_playlist(pl)
        return

    # ── !playlist add <name> <YouTube URL> ───────────────────────────────────
    if sub == "add":
        if len(args) < 4:
            await _w(bot, uid,
                     "Usage: !playlist add <name> <YouTube URL>\n"
                     "Tip: !playlist addcurrent <name> saves now-playing song.")
            return
        url     = args[-1].strip()
        pl_name = " ".join(args[2:-1])
        if _is_youtube_playlist_url(url):
            await _w(bot, uid, "⚠️ Add one YouTube song URL, not a playlist or mix.")
            return
        if not _is_yt_url_pl(url):
            await _w(bot, uid,
                     "⚠️ Please provide a valid YouTube URL.\n"
                     "Tip: !play <song> first, then !playlist addcurrent <name>.")
            return
        pl = _pl_get(uid, pl_name)
        if not pl:
            await _w(bot, uid, f"❌ Playlist not found: {pl_name[:24]}")
            return
        result = _pl_song_add(pl["id"], uid, "youtube", "(YouTube)", "",
                               url, "", "", "")
        msgs = {
            "added":     f"✅ Added to {pl['name'][:22]}.",
            "duplicate": f"⭐ Already in {pl['name'][:22]}.",
            "limit":     f"❌ Max {_PL_SONG_MAX} songs per playlist.",
            "error":     "❌ Could not add song. Try again.",
        }
        await _w(bot, uid, msgs.get(result, "❌ Error."))
        if result == "added":
            rr.record_reward(uid, uname, "playlist_add",
                             target_key=f"{pl['id']}|{url}"[:150])
            asyncio.create_task(_ra.check_radio_achievements(bot, uid, uname))
        return

    # ── !playlist addcurrent <name> ───────────────────────────────────────────
    if sub == "addcurrent":
        pl = _pl_get(uid, " ".join(args[2:]))
        if not pl:
            await _w(bot, uid, f"❌ Playlist not found: {' '.join(args[2:])[:22]}")
            return
        loop  = asyncio.get_running_loop()
        track = await loop.run_in_executor(None, _current_track_full)
        if not track:
            await _w(bot, uid, "❌ Nothing is currently playing.")
            return
        result = _pl_song_add(
            pl["id"], uid,
            track["source_type"], track["title"], track["artist"],
            track["youtube_url"], track["video_id"],
            track["azura_song_id"], track["azura_file_id"],
        )
        t = track["title"][:36]
        msgs = {
            "added":     f"✅ Added to {pl['name'][:20]}: {t}",
            "duplicate": f"⭐ Already in {pl['name'][:20]}: {t}",
            "limit":     f"❌ Max {_PL_SONG_MAX} songs per playlist.",
            "error":     "❌ Could not add song. Try again.",
        }
        await _w(bot, uid, msgs.get(result, "❌ Error."))
        if result == "added":
            _akey = f"{pl['id']}|{track.get('youtube_url') or track['title']}"[:150]
            rr.record_reward(uid, uname, "playlist_add", target_key=_akey)
            asyncio.create_task(_ra.check_radio_achievements(bot, uid, uname))
        return

    # ── !playlist rename <old> <new> ──────────────────────────────────────────
    if sub == "rename":
        if len(args) < 4:
            await _w(bot, uid, "Usage: !playlist rename <old name> <new name>")
            return
        old_name = args[2]
        new_name = " ".join(args[3:])
        result   = _pl_rename(uid, old_name, new_name)
        msgs = {
            "renamed":   f"✅ Renamed: {old_name[:16]} → {new_name[:16]}",
            "not_found": f"❌ Playlist not found: {old_name[:22]}",
            "duplicate": f"⭐ Name '{new_name[:20]}' already exists.",
            "invalid":   "⚠️ Name: 1-24 chars, letters/numbers/spaces/-/_",
            "error":     "❌ Could not rename. Try again.",
        }
        await _w(bot, uid, msgs.get(result, "❌ Error."))
        return

    # ── !playlist remove <name> <#> ───────────────────────────────────────────
    if sub == "remove":
        if len(args) < 4 or not args[-1].isdigit():
            await _w(bot, uid, "Usage: !playlist remove <name> <#>")
            return
        pos     = int(args[-1])
        pl_name = " ".join(args[2:-1])
        pl      = _pl_get(uid, pl_name)
        if not pl:
            await _w(bot, uid, f"❌ Playlist not found: {pl_name[:22]}")
            return
        title = _pl_song_remove(pl["id"], pos)
        if title:
            await _w(bot, uid,
                     f"✅ Removed #{pos} from {pl['name'][:17]}: {title[:30]}")
        else:
            await _w(bot, uid, f"❌ No song #{pos} in {pl['name'][:22]}.")
        return

    # ── !playlist delete <name> ───────────────────────────────────────────────
    if sub in ("delete", "del"):
        pl_name = " ".join(args[2:])
        if _pl_delete(uid, pl_name):
            await _w(bot, uid, f"✅ Deleted playlist: {pl_name[:24]}")
        else:
            await _w(bot, uid, f"❌ Playlist not found: {pl_name[:24]}")
        return

    # ── !playlist play <name> [#] ─────────────────────────────────────────────
    if sub == "play":
        # Detect optional song index as last arg: !playlist play <name> <#>
        song_idx: "int | None" = None
        if len(args) >= 4 and args[-1].isdigit():
            candidate = _pl_get(uid, " ".join(args[2:-1]))
            if candidate:
                pl       = candidate
                song_idx = int(args[-1])
            else:
                pl = _pl_get(uid, " ".join(args[2:]))
        else:
            pl = _pl_get(uid, " ".join(args[2:]))

        if not pl:
            await _w(bot, uid, f"❌ Playlist not found: {' '.join(args[2:])[:22]}")
            return

        # ── Play specific song by index ───────────────────────────────────────
        if song_idx is not None:
            await _queue_playlist_song(pl, song_idx)
            return

        await _w(
            bot, uid,
            "⚠️ Playlist autoplay is disabled. Use !playlist <name> to view songs, then !playlist <name> <#> to queue one song.",
        )
        return


# ─── !ratings ─────────────────────────────────────────────────────────────────

async def handle_ratings(bot: "BaseBot", user: "User", args: list) -> None:
    """!ratings — show current song like/dislike counts (alias for !likes)."""
    await handle_likes(bot, user, args)


async def handle_radiostatus(bot: "BaseBot", user: "User", _args: list) -> None:
    """!radiostatus — lightweight radio pipeline health snapshot."""
    import modules.radio_diagnostics as diag

    rq.sync_indexed_active_statuses()
    snap = await diag.snapshot(timeout_s=2.5)
    for msg in diag.format_status_messages(snap):
        await _w(bot, user.id, msg)


# ─── Startup ──────────────────────────────────────────────────────────────────

async def _cleanup_poll_task() -> None:
    """Poll every 60 s for the cleanup_requested DB flag and run reconcile when set."""
    while True:
        await asyncio.sleep(60)
        try:
            flag = db.get_room_setting("cleanup_requested", "0")
            if flag == "1":
                db.set_room_setting("cleanup_requested", "0")
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, azura.reconcile_requests_playlist)
                print(f"{_LOG} stage=cleanup_poll result={result!r}")
        except Exception as exc:
            print(f"{_LOG} cleanup_poll error: {exc}")


async def startup_radio(bot: "BaseBot") -> None:
    """
    Called from on_start for the DJ bot.
    Starts the bot-controlled playback engine + legacy file cleanup safety-net.
    """
    from modules.media_cleanup import start as _start_cleanup
    import modules.radio_diagnostics as diag

    print(f"{_LOG} Starting radio / playback engine…")
    await _start_cleanup(bot)
    asyncio.create_task(_cleanup_poll_task(), name="radio_cleanup_poll")
    asyncio.create_task(diag.log_startup_health(bot), name="radio_startup_health")
    print(f"[DJ_RADIO] radio startup complete")


# ─── Safety guard ─────────────────────────────────────────────────────────────
# Wraps every public-facing command handler so that unexpected exceptions are
# caught locally, logged, and whispered to the user.  This is defence-in-depth:
# the on_chat outer try/except already protects the bot, but this ensures the
# user gets a friendly error message and the stack trace is always printed.

def _safe(fn):
    """Decorator: catch any exception in a radio handler, log it, reply to user."""
    @functools.wraps(fn)
    async def _wrapper(bot: "BaseBot", user: "object", *args, **kwargs):
        try:
            return await fn(bot, user, *args, **kwargs)
        except Exception as exc:
            import traceback
            print(f"{_LOG} {fn.__name__} non-fatal error: {exc}")
            traceback.print_exc()
            try:
                uid = getattr(user, "id", None) or (user[0] if user else "")
                await bot.highrise.send_whisper(
                    uid, "❌ Radio error — please try again."
                )
            except Exception:
                pass
    return _wrapper


handle_request         = _safe(handle_request)
handle_pick            = _safe(handle_pick)
handle_queue           = _safe(handle_queue)
handle_nowplaying      = _safe(handle_nowplaying)
handle_skip            = _safe(handle_skip)
handle_remove          = _safe(handle_remove)
handle_clearqueue      = _safe(handle_clearqueue)
handle_history         = _safe(handle_history)
handle_voteskip        = _safe(handle_voteskip)
handle_vibes           = _safe(handle_vibes)
handle_vibe            = _safe(handle_vibe)
handle_setrequestprice = _safe(handle_setrequestprice)
handle_radiohelp       = _safe(handle_radiohelp)
handle_radiostatus     = _safe(handle_radiostatus)
handle_queuelimit      = _safe(handle_queuelimit)
handle_setqueuelimit   = _safe(handle_setqueuelimit)
handle_radiotutorial   = _safe(handle_radiotutorial)
handle_musicshop       = _safe(handle_musicshop)
handle_buyplays        = _safe(handle_buyplays)
handle_buyrequests     = _safe(handle_buyrequests)
handle_like            = _safe(handle_like)
handle_dislike         = _safe(handle_dislike)
handle_favorite        = _safe(handle_favorite)
handle_unfavorite      = _safe(handle_unfavorite)
handle_favorites       = _safe(handle_favorites)
handle_removefavorite  = _safe(handle_removefavorite)
handle_myrequests      = _safe(handle_myrequests)
handle_cancel          = _safe(handle_cancel)
handle_priority        = _safe(handle_priority)
handle_save            = _safe(handle_save)
handle_mysongs         = _safe(handle_mysongs)
handle_removefav       = _safe(handle_removefav)
handle_playmine        = _safe(handle_playmine)
handle_playlist        = _safe(handle_playlist)
handle_ratings         = _safe(handle_ratings)
handle_likes           = _safe(handle_likes)
handle_voters          = _safe(handle_voters)
handle_likeslist       = _safe(handle_likeslist)
handle_dislikeslist    = _safe(handle_dislikeslist)


# ─── !playedby / !myplayed ────────────────────────────────────────────────────

def _get_played_by(username: str, limit: int = 5) -> list:
    """Return [(title, artist), …] for a user's most recently played requests."""
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT title, COALESCE(artist,'') FROM yt_request_jobs "
                "WHERE lower(username)=lower(?) AND status='played' AND played_at IS NOT NULL "
                "ORDER BY played_at DESC LIMIT ?",
                (username, limit),
            ).fetchall()
            return [(r[0] or "Unknown", r[1]) for r in rows]
    except Exception:
        return []


async def _handle_playedby_raw(bot: "BaseBot", user: "User", args: list) -> None:
    """
    !playedby @username  — show recent requests by any player (staff only).
    Normal players see their own history with !myplayed.
    """
    _rlog("playedby", "handle_playedby", user.username)
    if not _is_staff(user.username):
        await _w(bot, user.id, "🔒 Staff only.")
        return
    target = " ".join(args[1:]).lstrip("@").strip() if len(args) > 1 else ""
    if not target:
        await _w(bot, user.id, "Usage: !playedby @username")
        return
    rows = _get_played_by(target, 5)
    if not rows:
        await _w(bot, user.id, f"🎵 @{target} has no requests yet.")
        return
    lines = [f"🎵 @{target} played:"]
    for i, (title, artist) in enumerate(rows, 1):
        entry = f"{i}. {title}" + (f" - {artist}" if artist else "")
        lines.append(entry[:70])
    await _w(bot, user.id, "\n".join(lines)[:249])


async def _handle_myplayed_raw(bot: "BaseBot", user: "User", _args: list) -> None:
    """!myplayed — show your own recent request history (anyone)."""
    _rlog("myplayed", "handle_myplayed", user.username)
    rows = _get_played_by(user.username, 5)
    if not rows:
        await _w(bot, user.id, "🎵 You haven't had any requests played yet.")
        return
    lines = ["🎵 Your recent requests:"]
    for i, (title, artist) in enumerate(rows, 1):
        entry = f"{i}. {title}" + (f" - {artist}" if artist else "")
        lines.append(entry[:70])
    await _w(bot, user.id, "\n".join(lines)[:249])


handle_playedby = _safe(_handle_playedby_raw)
handle_myplayed = _safe(_handle_myplayed_raw)
