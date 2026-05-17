"""
modules/dj_music.py
-------------------
DJ_DUDU music / song-request commands for the Highrise bot.
Bot mode: "dj"  |  Bot account display name: DJ_DUDU

Commands (owned by dj mode):
  !request <song>   — add a song to the queue  (public, 5-min cooldown)
  !queue            — show the next 5 pending requests
  !nowplaying       — show current / next song
  !skip             — advance the queue  (manager+)
  !stopmusic        — clear all pending requests  (manager+)

All messages ≤ 249 characters.
DB table: dj_requests  (created via migration 3.2M in database.py)
"""
from __future__ import annotations

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


# ---------------------------------------------------------------------------
# DB helpers (all use get_connection — no external state)
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
    """Returns how many minutes ago the user last requested (999 = never / expired)."""
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


def _add_request(user_id: str, username: str, title: str) -> int:
    """Insert a pending request. Returns queue position (1-based)."""
    try:
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO dj_requests (user_id, username, title, status)
               VALUES (?, ?, ?, 'pending')""",
            (user_id, username.lower(), title),
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
            """SELECT id, username, title
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
    """Return the first pending request (top of queue)."""
    rows = _get_queue(limit=1)
    return rows[0] if rows else None


def _skip_current() -> str | None:
    """Mark the first pending request as 'played'. Returns its title or None."""
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
        return row["title"]
    except Exception:
        return None


def _clear_queue() -> int:
    """Mark all pending requests as skipped. Returns count cleared."""
    try:
        conn = db.get_connection()
        n    = conn.execute(
            "SELECT COUNT(*) AS n FROM dj_requests WHERE status='pending'"
        ).fetchone()["n"]
        conn.execute(
            """UPDATE dj_requests
               SET status = 'skipped', played_at = datetime('now')
               WHERE status = 'pending'"""
        )
        conn.commit()
        conn.close()
        return n
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Whisper helper
# ---------------------------------------------------------------------------

async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def handle_dj_request(
    bot: "BaseBot", user: "User", args: list[str],
) -> None:
    """!request <song title>  — public, 5-min per-player cooldown, cap 20."""
    if len(args) < 2:
        await _w(bot, user.id, "🎵 Usage: !request <song title>")
        return

    title = " ".join(args[1:]).strip()[:80]
    if not title:
        await _w(bot, user.id, "🎵 Please include a song title.")
        return

    # Cooldown check
    mins_ago = _user_cooldown_mins(user.id)
    if mins_ago < _COOLDOWN_MINUTES:
        wait = _COOLDOWN_MINUTES - mins_ago
        await _w(bot, user.id,
                 f"⏳ You can request again in {wait}m.")
        return

    # Queue cap check
    count = _pending_count()
    if count >= _QUEUE_CAP:
        await _w(bot, user.id,
                 f"🚫 Queue is full ({_QUEUE_CAP} requests). Try again soon!")
        return

    pos = _add_request(user.id, user.username, title)
    if pos < 0:
        await _w(bot, user.id, "⚠️ Could not add your request. Try again.")
        return

    name = user.username[:15]
    await _w(bot, user.id,
             f"✅ Added to queue (#{pos}): {title[:60]}")
    try:
        await bot.highrise.chat(
            f"🎵 @{name} requested: {title[:60]} (#{pos} in queue)"[:249]
        )
    except Exception:
        pass


async def handle_dj_queue(bot: "BaseBot", user: "User") -> None:
    """!queue  — show next 5 pending song requests."""
    rows = _get_queue(limit=5)
    if not rows:
        await _w(bot, user.id, "🎵 The queue is empty. Use !request <song>!")
        return

    total = _pending_count()
    lines = [f"🎵 DJ Queue ({total} pending):"]
    for i, r in enumerate(rows, 1):
        uname = r["username"][:12]
        title = r["title"][:35]
        lines.append(f"#{i} {title} (@{uname})")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_dj_nowplaying(bot: "BaseBot", user: "User") -> None:
    """!nowplaying  — show current top of queue and next."""
    rows = _get_queue(limit=2)
    if not rows:
        await _w(bot, user.id,
                 "🎵 Nothing queued. Use !request <song> to add one!")
        return

    cur = rows[0]
    msg = (
        f"🎵 Now Playing:\n"
        f"{cur['title'][:60]} (by @{cur['username'][:15]})"
    )
    if len(rows) > 1:
        nxt = rows[1]
        msg += f"\nUp next: {nxt['title'][:40]} (@{nxt['username'][:12]})"
    await _w(bot, user.id, msg[:249])


async def handle_dj_skip(bot: "BaseBot", user: "User") -> None:
    """!skip  — advance the queue (manager+)."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "🔒 Manager only.")
        return

    skipped = _skip_current()
    if skipped is None:
        await _w(bot, user.id, "🎵 Queue is already empty.")
        return

    nxt = _get_current()
    if nxt:
        await _w(bot, user.id,
                 f"⏭️ Skipped. Now up: {nxt['title'][:60]}")
        try:
            await bot.highrise.chat(
                f"⏭️ Skipped! Now playing: {nxt['title'][:60]}"[:249]
            )
        except Exception:
            pass
    else:
        await _w(bot, user.id,
                 f"⏭️ Skipped. Queue is now empty.")
        try:
            await bot.highrise.chat("⏭️ Song skipped. Queue is now empty.")
        except Exception:
            pass


async def handle_dj_stopmusic(bot: "BaseBot", user: "User") -> None:
    """!stopmusic  — clear all pending requests (manager+)."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "🔒 Manager only.")
        return

    cleared = _clear_queue()
    if cleared == 0:
        await _w(bot, user.id, "🎵 Queue was already empty.")
        return

    await _w(bot, user.id, f"🛑 Queue cleared. {cleared} request(s) removed.")
    try:
        await bot.highrise.chat(
            f"🛑 DJ queue cleared by staff. ({cleared} removed)"[:249]
        )
    except Exception:
        pass
