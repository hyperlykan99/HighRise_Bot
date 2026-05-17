"""
modules/dj_music.py
-------------------
DJ_DUDU music / song-request commands for the Highrise bot.
Bot mode: "dj"  |  Bot account display name: DJ_DUDU

Commands (all owned by dj mode):
  !request <song>   — YouTube search, returns top 5 results (public, 5-min cooldown)
  !pick <1-5>       — confirm a search result and add it to the queue
  !queue            — show next 5 pending requests
  !nowplaying / !np — show current track + YouTube link
  !skip / !djskip   — advance queue (manager+)
  !skipvote         — public vote to skip current song (3 votes = auto-skip)
  !stopmusic        — clear all pending requests (manager+)

State:
  _pending_searches — per-user in-memory dict of last search results (volatile, OK)
  _skip_votes       — set of user_ids who voted to skip current #1 (volatile, resets on skip)
  dj_requests table — queue persists across restarts (SQLite)

All messages ≤ 249 characters.
DB tables: dj_requests (migration 3.2M), youtube_url + duration columns (3.2N)
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import database as db
from modules.permissions import can_manage_games

if TYPE_CHECKING:
    from highrise import BaseBot, User

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_COOLDOWN_MINUTES: int = 5
_QUEUE_CAP: int        = 20
_SKIPVOTE_THRESHOLD: int = 3   # votes needed to auto-skip

# ---------------------------------------------------------------------------
# Volatile in-memory state (resets on bot restart — intentional)
# ---------------------------------------------------------------------------

# { user_id: [{"title": str, "url": str, "duration": str, "channel": str}, ...] }
_pending_searches: dict[str, list[dict]] = {}

# user_ids who have voted to skip the current #1 track
_skip_votes: set[str] = set()

# ---------------------------------------------------------------------------
# YouTube search via yt-dlp (sync, run in executor)
# ---------------------------------------------------------------------------

def _yt_search_sync(query: str, max_results: int = 5) -> list[dict]:
    """Blocking YouTube search. Returns list of result dicts."""
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        return []

    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "default_search": f"ytsearch{max_results}",
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
            entries = info.get("entries", []) if info else []
            results = []
            for e in entries[:max_results]:
                dur_secs = e.get("duration") or 0
                mins, secs = divmod(int(dur_secs), 60)
                dur_str = f"{mins}:{secs:02d}" if dur_secs else "?:??"
                results.append({
                    "title":    (e.get("title") or "Unknown")[:60],
                    "url":      f"https://youtu.be/{e.get('id', '')}",
                    "duration": dur_str,
                    "channel":  (e.get("uploader") or e.get("channel") or "")[:25],
                })
            return results
    except Exception:
        return []


async def _yt_search(query: str, max_results: int = 5) -> list[dict]:
    """Async wrapper — runs blocking search in thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _yt_search_sync, query, max_results)

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _pending_count() -> int:
    try:
        conn = db.get_connection()
        row  = conn.execute(
            "SELECT COUNT(*) AS n FROM dj_requests WHERE status='pending'"
        ).fetchone()
        conn.close()
        return row["n"] if row else 0
    except Exception:
        return 0


def _user_cooldown_mins(user_id: str) -> int:
    """Minutes since user's last request (999 = never / already expired)."""
    try:
        conn = db.get_connection()
        row  = conn.execute(
            """SELECT (CAST(strftime('%s','now') AS INTEGER)
                       - CAST(strftime('%s', requested_at) AS INTEGER)) / 60 AS mins_ago
               FROM dj_requests
               WHERE user_id = ? AND status IN ('pending','playing')
               ORDER BY requested_at DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
        conn.close()
        if row and row["mins_ago"] is not None:
            return int(row["mins_ago"])
    except Exception:
        pass
    return 999


def _add_request(user_id: str, username: str, title: str,
                 youtube_url: str = "", duration: str = "") -> int:
    """Insert a pending request. Returns queue position (1-based)."""
    try:
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO dj_requests
                   (user_id, username, title, youtube_url, duration, status)
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (user_id, username.lower(), title, youtube_url, duration),
        )
        conn.commit()
        pos = conn.execute(
            "SELECT COUNT(*) AS n FROM dj_requests WHERE status='pending'"
        ).fetchone()["n"]
        conn.close()
        return pos
    except Exception:
        return -1


def _get_queue(limit: int = 5) -> list[dict]:
    try:
        conn = db.get_connection()
        rows = conn.execute(
            """SELECT id, username, title, youtube_url, duration
               FROM dj_requests
               WHERE status = 'pending'
               ORDER BY requested_at ASC LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _get_current() -> dict | None:
    rows = _get_queue(limit=1)
    return rows[0] if rows else None


def _skip_current() -> str | None:
    """Mark the first pending request as 'played'. Returns its title or None."""
    global _skip_votes
    try:
        conn = db.get_connection()
        row  = conn.execute(
            """SELECT id, title FROM dj_requests
               WHERE status = 'pending'
               ORDER BY requested_at ASC LIMIT 1"""
        ).fetchone()
        if not row:
            conn.close()
            return None
        conn.execute(
            """UPDATE dj_requests
               SET status = 'played', played_at = datetime('now')
               WHERE id = ?""",
            (row["id"],),
        )
        conn.commit()
        conn.close()
        _skip_votes = set()   # reset votes for next track
        return row["title"]
    except Exception:
        return None


def _clear_queue() -> int:
    """Mark all pending requests as skipped. Returns count cleared."""
    global _skip_votes
    try:
        conn = db.get_connection()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM dj_requests WHERE status='pending'"
        ).fetchone()["n"]
        conn.execute(
            """UPDATE dj_requests
               SET status = 'skipped', played_at = datetime('now')
               WHERE status = 'pending'"""
        )
        conn.commit()
        conn.close()
        _skip_votes = set()
        return n
    except Exception:
        return 0

# ---------------------------------------------------------------------------
# Whisper / chat helpers
# ---------------------------------------------------------------------------

async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


async def _chat(bot: "BaseBot", msg: str) -> None:
    try:
        await bot.highrise.chat(msg[:249])
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def handle_dj_request(
    bot: "BaseBot", user: "User", args: list[str],
) -> None:
    """!request <song> — search YouTube, whisper top 5 results."""
    if len(args) < 2:
        await _w(bot, user.id, "🎵 Usage: !request <song title>  then !pick <1-5>")
        return

    query = " ".join(args[1:]).strip()[:120]
    if not query:
        await _w(bot, user.id, "🎵 Please include a song title.")
        return

    # Cooldown check
    mins_ago = _user_cooldown_mins(user.id)
    if mins_ago < _COOLDOWN_MINUTES:
        wait = _COOLDOWN_MINUTES - mins_ago
        await _w(bot, user.id, f"⏳ You can request again in {wait}m.")
        return

    # Queue cap check
    if _pending_count() >= _QUEUE_CAP:
        await _w(bot, user.id,
                 f"🚫 Queue is full ({_QUEUE_CAP} songs). Try again soon!")
        return

    await _w(bot, user.id, f"🔍 Searching YouTube for: {query[:60]}…")

    results = await _yt_search(query, max_results=5)
    if not results:
        await _w(bot, user.id,
                 "⚠️ No results found. Try a different search term.")
        return

    _pending_searches[user.id] = results

    lines = ["🎵 Pick a song — reply !pick <number>:"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title'][:45]} [{r['duration']}]")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_dj_pick(
    bot: "BaseBot", user: "User", args: list[str],
) -> None:
    """!pick <1-5> — confirm a search result and add it to the queue."""
    if user.id not in _pending_searches:
        await _w(bot, user.id,
                 "🎵 No active search. Use !request <song> first.")
        return

    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "🎵 Usage: !pick <1-5>")
        return

    choice = int(args[1])
    results = _pending_searches[user.id]
    if choice < 1 or choice > len(results):
        await _w(bot, user.id,
                 f"🎵 Pick a number between 1 and {len(results)}.")
        return

    pick = results[choice - 1]
    del _pending_searches[user.id]   # clear pending search

    pos = _add_request(
        user.id, user.username,
        pick["title"], pick["url"], pick["duration"],
    )
    if pos < 0:
        await _w(bot, user.id, "⚠️ Could not add your request. Try again.")
        return

    name = user.username[:15]
    await _w(bot, user.id,
             f"✅ Added #{pos}: {pick['title'][:50]} [{pick['duration']}]")
    await _chat(bot,
        f"🎵 @{name} added: {pick['title'][:55]} (#{pos} in queue)"
    )


async def handle_dj_queue(bot: "BaseBot", user: "User") -> None:
    """!queue — show next 5 pending requests."""
    rows = _get_queue(limit=5)
    if not rows:
        await _w(bot, user.id,
                 "🎵 Queue is empty. Use !request <song> to add one!")
        return

    total = _pending_count()
    lines = [f"🎵 DJ Queue ({total} pending):"]
    for i, r in enumerate(rows, 1):
        dur = f" [{r['duration']}]" if r.get("duration") else ""
        lines.append(f"#{i} {r['title'][:38]}{dur}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_dj_nowplaying(bot: "BaseBot", user: "User") -> None:
    """!nowplaying / !np — show current track + link."""
    rows = _get_queue(limit=2)
    if not rows:
        await _w(bot, user.id,
                 "🎵 Nothing queued. Use !request <song> to add one!")
        return

    cur = rows[0]
    dur = f" [{cur['duration']}]" if cur.get("duration") else ""
    url = f"\n{cur['youtube_url']}" if cur.get("youtube_url") else ""
    votes = len(_skip_votes)
    vote_str = (f"\n👎 Skip votes: {votes}/{_SKIPVOTE_THRESHOLD}"
                if votes > 0 else "")
    msg = (
        f"🎵 Now Playing{dur}:\n"
        f"{cur['title'][:60]} (@{cur['username'][:15]})"
        f"{url}{vote_str}"
    )
    if len(rows) > 1:
        nxt = rows[1]
        ndur = f" [{nxt['duration']}]" if nxt.get("duration") else ""
        msg += f"\nUp next: {nxt['title'][:35]}{ndur}"
    await _w(bot, user.id, msg[:249])


async def handle_dj_skip(bot: "BaseBot", user: "User") -> None:
    """!skip / !djskip — advance queue (manager+)."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "🔒 Manager only. Use !skipvote to vote.")
        return

    skipped = _skip_current()
    if skipped is None:
        await _w(bot, user.id, "🎵 Queue is already empty.")
        return

    nxt = _get_current()
    if nxt:
        url = f" {nxt['youtube_url']}" if nxt.get("youtube_url") else ""
        await _chat(bot, f"⏭️ Skipped! Now up: {nxt['title'][:55]}{url}")
    else:
        await _chat(bot, "⏭️ Song skipped. Queue is now empty.")


async def handle_dj_skipvote(bot: "BaseBot", user: "User") -> None:
    """!skipvote — public vote to skip current song."""
    cur = _get_current()
    if cur is None:
        await _w(bot, user.id, "🎵 Nothing is playing right now.")
        return

    if user.id in _skip_votes:
        await _w(bot, user.id,
                 f"👎 You already voted to skip. "
                 f"({len(_skip_votes)}/{_SKIPVOTE_THRESHOLD})")
        return

    _skip_votes.add(user.id)
    votes = len(_skip_votes)

    if votes >= _SKIPVOTE_THRESHOLD:
        # Auto-skip
        title = cur["title"]
        _skip_current()
        nxt = _get_current()
        if nxt:
            url = f" {nxt['youtube_url']}" if nxt.get("youtube_url") else ""
            await _chat(bot,
                f"👎 Vote skip passed! Skipping: {title[:40]}\n"
                f"⏭️ Now up: {nxt['title'][:45]}{url}"
            )
        else:
            await _chat(bot,
                f"👎 Vote skip passed! Skipping: {title[:40]}\n"
                f"🎵 Queue is now empty."
            )
    else:
        remaining = _SKIPVOTE_THRESHOLD - votes
        name = user.username[:15]
        await _chat(bot,
            f"👎 @{name} voted to skip. "
            f"{remaining} more vote(s) needed."
        )


async def handle_dj_stopmusic(bot: "BaseBot", user: "User") -> None:
    """!stopmusic — clear all pending requests (manager+)."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "🔒 Manager only.")
        return

    cleared = _clear_queue()
    if cleared == 0:
        await _w(bot, user.id, "🎵 Queue was already empty.")
        return

    await _w(bot, user.id, f"🛑 Queue cleared. {cleared} request(s) removed.")
    await _chat(bot, f"🛑 DJ queue cleared by staff. ({cleared} removed)")
