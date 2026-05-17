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
import re
import threading
import time
from typing import TYPE_CHECKING

import modules.azuracast_controller as azura
import modules.config_store         as cs
import modules.dj_announcer         as ann
import modules.payment_service      as ps
import modules.request_queue        as rq
from modules.permissions import is_admin, is_owner

if TYPE_CHECKING:
    from highrise import BaseBot, User

_LOG = "[RADIO_CMD]"

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


async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


# ─── Core request submission helper ──────────────────────────────────────────

async def _submit_url(
    bot: "BaseBot", user: "User", url: str
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

    # Price + payment
    price = ps.request_cost_for(uname)
    ok, err = ps.charge(uid, price)
    if not ok:
        await _w(bot, uid, f"💸 {err}")
        return

    # Update cooldown after successful charge
    _cooldowns[uid] = time.time()

    # Confirm to user
    cost_str = f"{price:,} coins" if price else "free"
    await _w(bot, uid, f"🎵 Got it ({cost_str})! Downloading & uploading your request…")

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
    lines   = ["🎵 Top results — reply !pick <1-5>:"]
    for i, r in enumerate(results, 1):
        flag = " ⚠️" if r.get("duration_secs", 0) > cs.MAX_DURATION_SECS else ""
        lines.append(f"{i}. {r['title'][:44]} [{r.get('duration', '?')}]{flag}")
    lines.append(f"(Max {max_min}m/track)")
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

    await _submit_url(bot, user, url)


# ─── !queue / !q ──────────────────────────────────────────────────────────────

async def handle_queue(bot: "BaseBot", user: "User", _args: list) -> None:
    """!queue / !q — show now-playing request and pending queue."""
    loop    = asyncio.get_running_loop()
    np      = await loop.run_in_executor(None, azura.fetch_nowplaying)
    pending = rq.pending_jobs()

    lines: list = []

    if np:
        s   = ((np.get("now_playing") or {}).get("song") or {})
        ttl = (s.get("title")  or "").strip()
        art = (s.get("artist") or "").strip()
        label = (f"{art} — {ttl}" if art and art.lower() not in ttl.lower() else ttl) or "Unknown"
        cp = rq.currently_playing()
        if cp:
            req_by = (cp.get("username") or "")[:15]
            lines.append(f"▶ {label[:60]} (req: @{req_by})" if req_by else f"▶ {label[:70]}")
        else:
            lines.append(f"▶ {label[:80]}")

    if not pending:
        lines.append("📋 No pending requests. !request <song> to queue one!")
    else:
        for i, j in enumerate(pending[:5], 1):
            t = (j.get("title") or "…downloading")[:30]
            u = (j.get("username") or "?")[:12]
            lines.append(f"{i}. {t} — @{u}")
        if len(pending) > 5:
            lines.append(f"(+{len(pending) - 5} more)")

    await _w(bot, user.id, "\n".join(lines)[:249])


# ─── !nowplaying ──────────────────────────────────────────────────────────────

async def handle_nowplaying(bot: "BaseBot", user: "User", _args: list) -> None:
    """!nowplaying / !now / !np — detailed now-playing card."""
    loop = asyncio.get_running_loop()
    np   = await loop.run_in_executor(None, azura.fetch_nowplaying)

    if not np:
        await _w(bot, user.id, "📻 AzuraCast not responding or not configured.")
        return

    np_obj  = np.get("now_playing") or {}
    song    = np_obj.get("song") or {}
    elapsed = int(np_obj.get("elapsed")  or 0)
    duration= int(np_obj.get("duration") or song.get("length") or 0)
    title   = (song.get("title")  or "").strip() or "Unknown"
    artist  = (song.get("artist") or "").strip()

    label = (f"{artist} — {title}" if artist and artist.lower() not in title.lower()
             else title)

    # Check if it's one of our requests
    cp = rq.currently_playing()
    req_str = f" | @{cp['username'][:15]}" if cp and cp.get("username") else ""

    bar      = _progress_bar(elapsed, duration)
    time_str = f"{_fmt_secs(elapsed)}/{_fmt_secs(duration)}" if duration else ""

    parts = [f"🎧 {label[:70]}"]
    if time_str:
        parts.append(f"{bar} {time_str}")
    if req_str:
        parts.append(req_str)

    await _w(bot, user.id, " | ".join(parts)[:249])


# ─── !skip ────────────────────────────────────────────────────────────────────

async def handle_skip(bot: "BaseBot", user: "User", _args: list) -> None:
    """!skip — immediately skip the current song (admin+)."""
    if not _is_staff(user.username):
        await _w(bot, user.id, "🔒 Staff only.")
        return

    loop = asyncio.get_running_loop()
    np   = await loop.run_in_executor(None, azura.fetch_nowplaying)
    title_str = ""
    if np:
        s   = ((np.get("now_playing") or {}).get("song") or {})
        art = (s.get("artist") or "").strip()
        ttl = (s.get("title")  or "").strip()
        title_str = (f"{art} — {ttl}" if art else ttl)[:60]

    ok = await loop.run_in_executor(None, azura.skip_current)
    if ok:
        await ann.announce_skip(bot, title_str)
        await _w(bot, user.id, "✅ Skipped.")
    else:
        await _w(bot, user.id, "❌ Skip failed. Check AzuraCast API connectivity.")


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


# ─── !clearqueue ──────────────────────────────────────────────────────────────

async def handle_clearqueue(bot: "BaseBot", user: "User", _args: list) -> None:
    """!clearqueue — cancel all pending requests with refunds (admin+)."""
    if not _is_staff(user.username):
        await _w(bot, user.id, "🔒 Staff only.")
        return

    pending = rq.pending_jobs()
    if not pending:
        await _w(bot, user.id, "✅ Queue is already empty.")
        return

    await _w(bot, user.id, f"🧹 Clearing {len(pending)} request(s)…")

    loop          = asyncio.get_running_loop()
    total_refunded = 0

    for j in pending:
        uid   = j.get("user_id", "")
        coins = j.get("coins_charged", 0)

        rq.cancel_job(j["id"], "cleared_by_admin")

        if coins > 0 and uid:
            ps.refund(uid, coins, "queue_cleared")
            total_refunded += coins

        fid = (j.get("azura_file_id") or "").strip()
        fn  = (j.get("filename")      or "").strip()
        if fid:
            await loop.run_in_executor(None, azura.delete_media_file, fid)
        elif fn:
            await loop.run_in_executor(None, azura.sftp_delete_file, fn)

    await ann.announce_queue_cleared(bot, len(pending), total_refunded)


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


# ─── !vibe ────────────────────────────────────────────────────────────────────

async def handle_vibe(bot: "BaseBot", user: "User", args: list) -> None:
    """
    !vibe status           — show current vibe and playlist config
    !vibe chill / !vibe party  — switch vibe (admin+)
    """
    sub = (args[1].lower() if len(args) > 1 else "status")

    if sub == "status":
        v        = cs.vibe()
        price    = cs.request_price()
        chill_id = cs.chill_playlist_id()
        party_id = cs.party_playlist_id()
        req_id   = cs.requests_playlist_id()
        api_ok   = "✓" if cs.azura_api_ready() else "✗"
        pl_ok    = "✓" if (chill_id and party_id) else "✗ (set AZURA_PLAYLIST_CHILL/PARTY_ID)"
        await _w(
            bot, user.id,
            f"📻 Vibe: {v.upper()} | Price: {price:,} coins | "
            f"API: {api_ok} | Playlists: {pl_ok} | Requests pl: {'✓' if req_id else '✗'}",
        )
        return

    if sub not in ("chill", "party"):
        await _w(bot, user.id, "🎛️ Usage: !vibe chill | !vibe party | !vibe status")
        return

    if not _is_staff(user.username):
        await _w(bot, user.id, "🔒 Staff only.")
        return

    if not cs.azura_api_ready():
        await _w(bot, user.id, "📻 AzuraCast API not configured (AZURA_BASE_URL / AZURA_API_KEY).")
        return

    if not cs.chill_playlist_id() or not cs.party_playlist_id():
        await _w(bot, user.id, "⚠️ Playlist IDs not set. Configure AZURA_PLAYLIST_CHILL_ID & AZURA_PLAYLIST_PARTY_ID.")
        return

    loop                   = asyncio.get_running_loop()
    chill_ok, party_ok     = await loop.run_in_executor(None, azura.apply_vibe, sub)
    cs.set_vibe(sub)
    await ann.announce_vibe_changed(bot, sub)

    if not chill_ok or not party_ok:
        await _w(bot, user.id, f"⚠️ Vibe set to {sub.upper()} but one playlist toggle failed — check API.")
    else:
        await _w(bot, user.id, f"✅ Vibe switched to {sub.upper()}.")


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
    thresh   = cs.voteskip_threshold()
    await _w(
        bot, user.id,
        f"📻 Radio commands:\n"
        f"!request <song/URL> ({cost_str}) | !queue (!q) | !nowplaying | !history\n"
        f"!voteskip ({thresh} votes needed)\n"
        f"Staff: !skip | !remove <#> | !clearqueue | !vibe <mode> | !setrequestprice",
    )


# ─── Startup ──────────────────────────────────────────────────────────────────

async def startup_radio(bot: "BaseBot") -> None:
    """
    Called from on_start for the DJ bot.
    Starts the AzuraCast media-cleanup / now-playing announcement loop.
    """
    from modules.media_cleanup import start as _start_cleanup
    print(f"{_LOG} Starting radio cleanup loop…")
    await _start_cleanup(bot)
