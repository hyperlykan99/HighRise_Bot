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
import modules.payment_service      as ps
import modules.request_queue        as rq
import modules.playback_engine      as engine
from modules.permissions import is_admin, is_owner

if TYPE_CHECKING:
    from highrise import BaseBot, User

_LOG = "[RADIO_CMD]"

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
_LIKE_CD_SECS = 30

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


def _fav_get(user_id: str, limit: int = 10) -> list:
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT id, title, youtube_url FROM dj_favorites "
                "WHERE user_id=? ORDER BY favorited_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [{"id": r[0], "title": r[1], "url": r[2]} for r in rows]
    except Exception:
        return []


def _fav_add(user_id: str, username: str, title: str, url: str) -> bool:
    """Insert into dj_favorites. Returns False if already there."""
    try:
        with db.db_conn() as conn:
            if conn.execute(
                "SELECT id FROM dj_favorites WHERE user_id=? AND lower(title)=lower(?)",
                (user_id, title),
            ).fetchone():
                return False
            conn.execute(
                "INSERT INTO dj_favorites (user_id, username, title, youtube_url) "
                "VALUES (?,?,?,?)",
                (user_id, username.lower(), title, url),
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


def _is_staff(username: str) -> bool:
    return is_owner(username) or is_admin(username)


def _fmt_secs(secs: int) -> str:
    m, s = divmod(max(0, int(secs)), 60)
    return f"{m}:{s:02d}"


def _progress_bar(elapsed: int, total: int, cells: int = 10) -> str:
    if total <= 0:
        return "▱" * cells
    filled = round(cells * min(elapsed, total) / total)
    return "▰" * filled + "▱" * (cells - filled)


# ─── Title noise stripper (for search results display) ────────────────────────
_TITLE_NOISE = re.compile(
    r"\s*[\(\[]\s*(?:official\s+(?:music\s+)?(?:video|audio|lyric\s+video|visualizer)"
    r"|lyric(?:s|\s+video)?|visualizer|audio|hd|4k|full\s+(?:video|song)"
    r"|official)\s*[\)\]]\s*",
    re.IGNORECASE,
)


def _clean_title(title: str) -> str:
    """Strip common YouTube noise for compact display (Official Video, Lyrics, etc.)."""
    return _TITLE_NOISE.sub(" ", title).strip()


async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


# ─── Core request submission helper ──────────────────────────────────────────

async def _submit_url(
    bot: "BaseBot", user: "User", url: str,
    metadata: "dict | None" = None,
) -> None:
    """
    Validate, charge, and launch a job for a confirmed YouTube/audio URL.
    Handles dedup, queue capacity, payment, and cooldown in one place.
    """
    uid      = user.id
    uname    = user.username
    is_staff = _is_staff(uname)

    # Strip playlist cruft
    url = url.split("&list=")[0].split("?list=")[0]

    # Regular users: YouTube URLs only
    if not is_staff and not _is_yt_url(url):
        await _w(bot, uid, "🎵 Please use a YouTube URL or search by song name.")
        return

    # Banned requester check
    if rq.is_banned_requester(uname):
        await _w(bot, uid, "🚫 You are not allowed to request songs in this room.")
        return

    # Cooldown (admin bypasses)
    if not is_staff:
        cd      = cs.cooldown_secs()
        elapsed = time.time() - _cooldowns.get(uid, 0)
        if elapsed < cd:
            remaining = int(cd - elapsed)
            await _w(bot, uid, f"⏳ Cooldown: {remaining}s remaining. Please wait.")
            return

    # Dedup (admin bypasses)
    if not is_staff:
        dup = rq.check_dedup(url, cs.DEDUP_WINDOW_SECS)
        if dup:
            req_by = (dup.get("username") or "someone")[:20]
            await _w(
                bot, uid,
                f"⚠️ That song was requested recently by @{req_by}. Try again tomorrow.",
            )
            return

    # Queue capacity
    if rq.active_count() >= cs.MAX_ACTIVE_JOBS:
        await _w(
            bot, uid,
            f"📋 Queue is full ({cs.MAX_ACTIVE_JOBS} requests in progress). Please wait.",
        )
        return

    # Per-user queue limit
    if not is_staff:
        u_count = rq.user_active_count(uid)
        if u_count >= cs.MAX_PER_USER_JOBS:
            await _w(
                bot, uid,
                f"📋 You already have {u_count} song(s) queued. Wait for them to play first.",
            )
            return

    # Price + payment
    price = ps.request_cost_for(uname)
    ok, err = ps.charge(uid, price)
    if not ok:
        await _w(bot, uid, f"💸 {err}")
        return

    # Update cooldown after successful charge
    _cooldowns[uid] = time.time()

    # Compute queue position: count only future songs, excluding currently playing
    _pos = rq.future_count() + 1

    # Confirmation whisper — spec format (title/artist only when known from !pick)
    _title  = ((metadata.get("title")  or "") if metadata else "")[:55]
    _artist = ((metadata.get("artist") or metadata.get("uploader") or "") if metadata else "")[:30]
    _lines  = ["✅ Added to queue"]
    if _title:
        _lines.append(f"Title: {_title}")
    if _artist:
        _lines.append(f"Artist: {_artist}")
    _lines.append(f"Position: #{_pos}")
    _lines.append(f"🙋 @{uname[:20]}")
    _lines.append("📻 ChillTopia Radio")
    await _w(bot, uid, "\n".join(_lines)[:249])

    # Launch pipeline
    rq.submit_job(
        bot, uid, uname, url,
        coins_charged=price,
        payment_type="paid" if price > 0 else "free",
    )


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
    max_min = cs.MAX_DURATION_SECS // 60
    lines   = ["🎵 Pick a result — reply !pick <1-5>:"]
    for i, r in enumerate(results, 1):
        flag  = " ⚠️" if r.get("duration_secs", 0) > cs.MAX_DURATION_SECS else ""
        clean = _clean_title(r["title"])[:36]
        lines.append(f"{i}. {clean} [{r.get('duration', '?')}]{flag}")
    lines.append(f"(Max {max_min}m)")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ─── !pick ────────────────────────────────────────────────────────────────────

async def handle_pick(bot: "BaseBot", user: "User", args: list) -> None:
    """!pick <1-5>  — confirm a pending search result from !request."""
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
    rq.clear_pending_search(user.id)

    if not url:
        await _w(bot, user.id, "❌ Could not get URL for that result. Try again.")
        return

    await _submit_url(bot, user, url, metadata=picked)


# ─── !queue / !q ──────────────────────────────────────────────────────────────

async def handle_queue(bot: "BaseBot", user: "User", _args: list) -> None:
    """
    !queue / !q — preparing/ready requests (downloading/uploaded/ready), compact ≤249 chars.

    Fetches AzuraCast Now Playing first to filter out any request that is
    currently streaming.  Ensures the same song never appears in both
    NOW PLAYING and UP NEXT.  If a display-list item matches NP, its DB
    status is immediately updated to 'playing'.
    """
    loop = asyncio.get_running_loop()

    # ── Fetch NP to build current-song identifier set ─────────────────────────
    np       = await loop.run_in_executor(None, azura.fetch_nowplaying)
    np_obj   = (np or {}).get("now_playing") or {}
    np_song  = np_obj.get("song")  or {}
    np_media = np_obj.get("media") or {}
    np_sid    = (np_song.get("id")        or "").strip()
    np_uid    = (np_song.get("unique_id") or "").strip()
    np_title  = (np_song.get("title")     or "").strip().lower()
    np_artist = (np_song.get("artist")    or "").strip().lower()
    np_text   = (np_song.get("text")      or "").strip().lower()  # "Artist - Title"
    np_fid    = str(np_media.get("id") or "").strip()
    np_path   = (np_media.get("path") or "").strip()
    np_fn     = np_path.rsplit("/", 1)[-1].lower() if np_path else ""

    def _np_title_hit(jt: str) -> bool:
        """True if job title matches any NP title variant (title, text, artist)."""
        if not jt or len(jt) < 5:
            return False
        for ref in (np_title, np_text):
            if ref and (jt in ref or ref in jt or jt[:30] == ref[:30]):
                return True
        return False

    # ── Queue audit: fix stale 'playing' rows before reading display queue ─────
    # Jobs can get stuck as status='playing' if the bot restarted between the
    # song starting and the poll loop detecting the song change.  Check each
    # one against NP — if it no longer matches, mark it played immediately.
    stale = rq.stale_playing_jobs()
    audit_fixed = 0
    for sp in stale:
        jfid   = (sp.get("azura_file_id") or "").strip()
        jsid   = (sp.get("azura_song_id") or "").strip()
        jfn    = (sp.get("filename")      or "").lower()
        jvid   = (sp.get("video_id")      or "").strip()
        jtitle = (sp.get("title")         or "").lower().strip()

        still_np = bool(
            (np_fid and jfid and np_fid == jfid)
            or (np_sid and jsid and (np_sid == jsid or np_uid == jsid))
            or (np_fn and jfn and (jfn == np_fn or jfn in np_fn or np_fn in jfn))
            or (jvid and np_path and jvid in np_path)
            or _np_title_hit(jtitle)
        )
        if not still_np:
            rq.mark_as_played(sp["id"])
            audit_fixed += 1
            print(
                f"{_LOG} stage=queue_status_fix"
                f" request_id={sp['id']}"
                f" old_status=playing new_status=played"
                f" title={sp.get('title','?')!r}"
                f" reason=np_mismatch"
            )

    print(
        f"{_LOG} stage=queue_audit"
        f" playing_rows_checked={len(stale)}"
        f" fixed={audit_fixed}"
        f" np_title={np_title!r}"
    )

    # ── Load display queue (already excludes 'playing' status) ────────────────
    waiting = rq.display_jobs()

    # ── Filter out any item that matches the current NP ───────────────────────
    filtered: list = []
    for j in waiting:
        jfid   = (j.get("azura_file_id") or "").strip()
        jsid   = (j.get("azura_song_id") or "").strip()
        jfn    = (j.get("filename")      or "").lower()
        jvid   = (j.get("video_id")      or "").strip()
        jtitle = (j.get("title")         or "").lower().strip()

        match_method: "str | None" = None
        if np_fid and jfid and np_fid == jfid:
            match_method = "media_id"
        elif np_sid and jsid and (np_sid == jsid or np_uid == jsid):
            match_method = "song_id"
        elif np_fn and jfn and (jfn == np_fn or jfn in np_fn or np_fn in jfn):
            match_method = "filename"
        elif jvid and np_path and jvid in np_path:
            match_method = "video_id"
        elif _np_title_hit(jtitle):
            match_method = "title_fuzzy"

        if match_method:
            rq.mark_as_playing(j["id"])
            print(
                f"{_LOG} stage=nowplaying_match"
                f" request_id={j['id']}"
                f" match_method={match_method!r}"
                f" title={j.get('title','?')!r}"
                f" nowplaying_title={np_title!r}"
            )
            print(
                f"{_LOG} stage=queue_status_fix"
                f" request_id={j['id']}"
                f" old_status={j.get('status','?')!r} new_status=playing"
                f" reason=nowplaying_match"
            )
        else:
            filtered.append(j)

    print(
        f"{_LOG} stage=queue_nowplaying_filter"
        f" count_before={len(waiting)} count_after={len(filtered)}"
        f" np_title={np_title!r}"
    )

    total = len(filtered)
    print(
        f"{_LOG} stage=queue_read command=queue"
        f" statuses={list(rq._DISPLAY_STATUSES)!r}"
        f" count={total}"
    )

    if not total:
        await _w(bot, user.id, "🎧 UP NEXT:\nEmpty")
        return

    _MAX   = 249
    _TTMAX = 28

    rows: list[str] = []
    for i, j in enumerate(filtered, 1):
        t    = (j.get("title")    or "…").strip()[:_TTMAX]
        a    = (j.get("artist")   or "").strip()[:15]
        u    = (j.get("username") or "?").strip()[:12]
        st   = j.get("status", "")
        icon = "✅" if st == "ready" else ("❌" if st in ("error", "failed_download") else "⏳")
        if a:
            rows.append(f"{i}. {t} - {a} - @{u} {icon}")
        else:
            rows.append(f"{i}. {t} - @{u} {icon}")

    header = "🎧 UP NEXT:"
    shown  = total
    msg    = ""
    while shown > 0:
        body = "\n".join(rows[:shown])
        rest = total - shown
        tail = f"\n+{rest} more" if rest > 0 else ""
        if len(header) + 1 + len(body) + len(tail) <= _MAX:
            msg = header + "\n" + body + tail
            break
        shown -= 1
    if not msg:
        msg = f"{header}\n+{total} more"

    print(
        f"{_LOG} stage=queue_render"
        f" total={total} visible_count={shown}"
        f" message_len={len(msg)}"
    )
    await _w(bot, user.id, msg[:_MAX])


# ─── !nowplaying ──────────────────────────────────────────────────────────────

async def handle_nowplaying(bot: "BaseBot", user: "User", _args: list) -> None:
    """!nowplaying / !now / !np — compact vertical now-playing card."""
    loop = asyncio.get_running_loop()
    np   = await loop.run_in_executor(None, azura.fetch_nowplaying)

    if not np:
        await _w(
            bot, user.id,
            "📻 ChillTopia Radio is live, but I can't read the current track right now.",
        )
        return

    np_obj   = np.get("now_playing") or {}
    song     = np_obj.get("song") or {}
    elapsed  = int(np_obj.get("elapsed")  or 0)
    duration = int(np_obj.get("duration") or song.get("length") or 0)
    title    = (song.get("title")  or "").strip() or "Unknown"
    artist   = (song.get("artist") or "").strip()

    if artist and artist.lower() not in title.lower():
        track = f"{artist} — {title}"
    else:
        track = title

    # Header + source line — AutoDJ vs Request
    cp = rq.currently_playing()
    if cp:
        header      = "▶ REQUEST LIVE"
        req_uname   = (cp.get("username") or "")[:20]
        source_line = f"🙋 @{req_uname}" if req_uname else "🙋 Requested"
    else:
        from modules.dj_announcer import _VIBE_LINE as _vl
        header      = "▶ NOW PLAYING"
        source_line = _vl.get(cs.vibe(), "🌙 AutoDJ • Chill")

    # Progress bar + time string (always 10 blocks)
    bar      = _progress_bar(elapsed, duration) if duration else "▱" * 10
    time_str = (
        f"⏱ {_fmt_secs(elapsed)} / {_fmt_secs(duration)}" if duration
        else "⏱ Live stream"
    )

    msg = "\n".join([
        header,
        f"🎵 {track[:42]}",
        source_line,
        time_str,
        bar,
        "📻 ChillTopia Radio",
    ])
    await _w(bot, user.id, msg[:249])


# ─── !skip ────────────────────────────────────────────────────────────────────

async def handle_skip(bot: "BaseBot", user: "User", _args: list) -> None:
    """
    !skip — immediately skip the current song (admin+).

    After a confirmed skip:
    - Marks the playing request as 'played' immediately (don't wait for the
      poll loop to detect the song change).  This ensures !q shows it gone.
    - Queues file cleanup via playback engine (move to PlayedRequests/, rescan).
    - Logs stage=request_skipped for audit trail.
    """
    if not _is_staff(user.username):
        await _w(bot, user.id, "🔒 Staff only.")
        return

    loop = asyncio.get_running_loop()

    # Snapshot the current playing request BEFORE the skip
    cp = rq.currently_playing()

    np   = await loop.run_in_executor(None, azura.fetch_nowplaying)
    title_str = ""
    if np:
        s   = ((np.get("now_playing") or {}).get("song") or {})
        art = (s.get("artist") or "").strip()
        ttl = (s.get("title")  or "").strip()
        title_str = (f"{art} — {ttl}" if art else ttl)[:60]

    ok = await loop.run_in_executor(None, azura.skip_current)
    if ok:
        # Immediately mark the playing request as played so it vanishes from !q
        # without waiting for the poll loop to detect the song transition.
        if cp:
            job_id = cp.get("id")
            if job_id:
                print(
                    f"{_LOG} stage=request_skipped"
                    f" request_id={job_id}"
                    f" title={cp.get('title','?')!r}"
                    f" username={cp.get('username','?')!r}"
                    f" skipped_by={user.username!r}"
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
        note = f" ({coins:,} coins refunded)"

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

    jid   = job["id"]
    title = (job.get("title") or "in progress")[:40]
    coins = job.get("coins_charged", 0)

    cancelled = rq.cancel_job(jid, "cancelled_by_user")
    if not cancelled:
        await _w(bot, uid, "⚠️ Could not cancel — it may have just started playing.")
        return

    note = ""
    if coins > 0:
        ps.refund(uid, coins, "cancelled_by_user")
        note = f"\n💸 {coins:,} coins refunded."

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
    """!history — last 8 played requests."""
    history = rq.recent_history(8)
    if not history:
        await _w(bot, user.id, "📜 No request history yet. Be the first to request a song!")
        return

    lines = ["📜 Recent requests:"]
    for row in history:
        t = (row.get("title") or "?")[:28]
        u = (row.get("username") or "?")[:12]
        lines.append(f"• {t} — @{u}")
    await _w(bot, user.id, "\n".join(lines)[:249])


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
    """!vibes — list all available vibes (anyone)."""
    _rlog("vibes", "handle_vibes", user.username)
    names = ", ".join(cs.VIBE_NAMES)
    await _w(bot, user.id, f"🎶 Vibes:\n{names}\nUse: !vibe <name>")


# ─── !vibe ────────────────────────────────────────────────────────────────────

_VIBE_DISPLAY: "dict[str, str]" = {
    "chill":      "Chill",
    "party":      "Party Remixes",
    "afrobeats":  "Afrobeats",
    "edm":        "EDM",
    "house":      "House",
    "kpop":       "KPop",
    "opm":        "OPM",
    "lofi":       "LoFi",
    "rnb":        "RNB",
    "hiphop":     "HipHop",
    "nightdrive": "NightDrive",
}


async def handle_vibe(bot: "BaseBot", user: "User", args: list) -> None:
    """
    !vibe status  — show current vibe (anyone)
    !vibe <name>  — switch vibe (staff only)
    """
    sub = (args[1].lower().strip() if len(args) > 1 else "status")

    if sub == "status":
        v      = cs.vibe()
        price  = cs.request_price()
        req_id = cs.requests_playlist_id()
        pl_id  = cs.vibe_playlist_id(v)
        label  = _VIBE_DISPLAY.get(v, v.title())
        api_ok = "✓" if cs.azura_api_ready() else "✗"
        await _w(
            bot, user.id,
            f"📻 Vibe: {label} | Price: {price:,} coins | "
            f"API: {api_ok} | Requests: {'✓' if req_id else '✗'} | Playlist: {'✓' if pl_id else '✗'}",
        )
        return

    if sub not in cs.VIBE_NAMES:
        await _w(bot, user.id, "❌ Unknown vibe. Use !vibes.")
        return

    if not _is_staff(user.username):
        await _w(bot, user.id, "🔒 Staff only.")
        return

    if not cs.azura_api_ready():
        await _w(bot, user.id, "📻 AzuraCast API not configured (AZURA_BASE_URL / AZURA_API_KEY).")
        return

    pid = cs.vibe_playlist_id(sub)
    if not pid:
        label = _VIBE_DISPLAY.get(sub, sub.title())
        await _w(
            bot, user.id,
            f"❌ {label} playlist not configured "
            f"(set AZURA_PLAYLIST_{sub.upper()}_ID).",
        )
        return

    # Verify playlist has songs before switching
    count = await asyncio.get_running_loop().run_in_executor(
        None, azura.get_playlist_media_count, pid
    )
    if count == 0:
        await _w(bot, user.id, "❌ That vibe has no songs yet.")
        return

    cs.set_vibe(sub)
    await engine.apply_vibe_change(bot)
    label = _VIBE_DISPLAY.get(sub, sub.title())
    await ann.announce_vibe_changed(bot, sub)

    mode = engine.get_playlist_mode()
    note = "\n(Takes effect when request queue clears.)" if mode == "requests" else ""
    await _w(bot, user.id, f"🎶 Vibe changed\nMode: {label}\nRequests: ON{note}")


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
    """!radiohelp — whisper the radio command reference card."""
    price    = cs.request_price()
    cost_str = f"{price:,} coins" if price else "free"
    await _w(
        bot, user.id,
        f"📻 DJ DUDU commands:\n"
        f"!request <song/URL> ({cost_str}) | !pick <1-5>\n"
        f"!q | !now | !voteskip | !cancel | !radiohelp",
    )


# ─── !like ────────────────────────────────────────────────────────────────────

async def handle_like(bot: "BaseBot", user: "User", _args: list) -> None:
    """!like — like the currently playing AzuraCast track."""
    _rlog("like", "handle_like", user.username)
    loop  = asyncio.get_running_loop()
    track = await loop.run_in_executor(None, _azura_track)
    if not track:
        await _w(bot, user.id, "🎵 Nothing playing right now. Try !np to check the stream.")
        return
    wait = _LIKE_CD_SECS - int(time.time() - _like_cd.get(user.id, 0))
    if wait > 0:
        await _w(bot, user.id, f"⏳ Wait {wait}s before rating again.")
        return
    _like_cd[user.id] = time.time()
    result = _rate(user.id, user.username, track["key"], "like")
    counts = _ratings(track["key"])
    score  = f"👍 {counts['likes']} | 👎 {counts['dislikes']}"
    title  = track["title"][:48]
    if result == "same":
        await _w(bot, user.id, f"👍 Already liked: {title}\n{score}")
    elif result == "changed":
        await _w(bot, user.id, f"👍 Changed to like: {title}\n{score}")
    elif result == "added":
        await _w(bot, user.id, f"👍 Liked: {title}\n{score}")
    else:
        await _w(bot, user.id, "⚠️ Could not save rating. Try again.")


# ─── !dislike ─────────────────────────────────────────────────────────────────

async def handle_dislike(bot: "BaseBot", user: "User", _args: list) -> None:
    """!dislike — dislike the currently playing AzuraCast track."""
    _rlog("dislike", "handle_dislike", user.username)
    loop  = asyncio.get_running_loop()
    track = await loop.run_in_executor(None, _azura_track)
    if not track:
        await _w(bot, user.id, "🎵 Nothing playing right now. Try !np to check the stream.")
        return
    wait = _LIKE_CD_SECS - int(time.time() - _like_cd.get(user.id, 0))
    if wait > 0:
        await _w(bot, user.id, f"⏳ Wait {wait}s before rating again.")
        return
    _like_cd[user.id] = time.time()
    result = _rate(user.id, user.username, track["key"], "dislike")
    counts = _ratings(track["key"])
    score  = f"👍 {counts['likes']} | 👎 {counts['dislikes']}"
    title  = track["title"][:48]
    if result == "same":
        await _w(bot, user.id, f"👎 Already disliked: {title}\n{score}")
    elif result == "changed":
        await _w(bot, user.id, f"👎 Changed to dislike: {title}\n{score}")
    elif result == "added":
        await _w(bot, user.id, f"👎 Disliked: {title}\n{score}")
    else:
        await _w(bot, user.id, "⚠️ Could not save rating. Try again.")


# ─── !favorite / !fav / !addtoplaylist ───────────────────────────────────────

async def handle_favorite(bot: "BaseBot", user: "User", _args: list) -> None:
    """!favorite / !fav / !addtoplaylist — save current AzuraCast track to favorites."""
    _rlog("favorite", "handle_favorite", user.username)
    loop  = asyncio.get_running_loop()
    track = await loop.run_in_executor(None, _azura_track)
    if not track:
        await _w(bot, user.id, "🎵 Nothing playing right now. Try !np to check the stream.")
        return
    added = _fav_add(user.id, user.username, track["title"], "")
    if added:
        await _w(bot, user.id, f"⭐ Saved to favorites: {track['title'][:55]}")
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
    """!favorites / !favs / !myplaylist — list your saved songs (newest first)."""
    _rlog("favorites", "handle_favorites", user.username)
    rows = _fav_get(user.id, limit=8)
    if not rows:
        await _w(bot, user.id, "⭐ No favorites yet! Use !favorite while a song plays.")
        return
    lines = [f"⭐ Your favorites ({len(rows)}):"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {r['title'][:52]}")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ─── !removefavorite <number> ────────────────────────────────────────────────

async def handle_removefavorite(bot: "BaseBot", user: "User", args: list) -> None:
    """!removefavorite <number> — remove a saved favorite by list position."""
    _rlog("removefavorite", "handle_removefavorite", user.username)
    if len(args) < 2 or not args[1].isdigit():
        rows = _fav_get(user.id, limit=8)
        if not rows:
            await _w(bot, user.id, "⭐ No favorites yet.")
            return
        lines = ["⭐ Your favorites (use !removefavorite <#>):"]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. {r['title'][:52]}")
        await _w(bot, user.id, "\n".join(lines)[:249])
        return
    pos   = int(args[1])
    title = _fav_remove_by_pos(user.id, pos)
    if title:
        await _w(bot, user.id, f"💔 Removed #{pos}: {title[:55]}")
    else:
        await _w(bot, user.id, f"⚠️ No favorite #{pos}. Use !favorites to see your list.")


# ─── !myrequests ─────────────────────────────────────────────────────────────

async def handle_myrequests(bot: "BaseBot", user: "User", _args: list) -> None:
    """!myrequests — show your active and recent requests from the unified queue."""
    _rlog("myrequests", "handle_myrequests", user.username)
    rows = _user_job_history(user.id, limit=6)
    if not rows:
        await _w(bot, user.id, "📋 You have no requests yet. Try !request <song>!")
        return
    _ACTIVE_ST = {"pending", "downloading", "uploading", "staged", "done", "queued", "playing"}
    _ICON = {
        "pending": "⏳", "downloading": "⬇️", "uploading": "📤",
        "staged": "📦", "done": "✅", "queued": "📋", "playing": "▶",
        "played": "✅", "error": "❌",
    }
    active  = [r for r in rows if r["status"] in _ACTIVE_ST]
    history = [r for r in rows if r["status"] not in _ACTIVE_ST]
    lines: list = []
    if active:
        lines.append(f"🎵 Active ({len(active)}):")
        for r in active:
            lines.append(f"  {_ICON.get(r['status'], '•')} {r['title'][:42]}")
    if history:
        lines.append("📜 Recent:")
        for r in history[:3]:
            lines.append(f"  {_ICON.get(r['status'], '•')} {r['title'][:46]}")
    await _w(bot, user.id, "\n".join(lines)[:249])


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
    print(f"{_LOG} Starting radio / playback engine…")
    await _start_cleanup(bot)
    asyncio.create_task(_cleanup_poll_task())


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
handle_like            = _safe(handle_like)
handle_dislike         = _safe(handle_dislike)
handle_favorite        = _safe(handle_favorite)
handle_unfavorite      = _safe(handle_unfavorite)
handle_favorites       = _safe(handle_favorites)
handle_removefavorite  = _safe(handle_removefavorite)
handle_myrequests      = _safe(handle_myrequests)
handle_cancel          = _safe(handle_cancel)
