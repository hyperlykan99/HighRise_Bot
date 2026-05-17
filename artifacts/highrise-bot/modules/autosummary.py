"""
modules/autosummary.py
-----------------------
Auto-session summary commands: !autosummary, !minesummary, !fishsummary.

DB table used : auto_session_summaries  (pre-existing)
DB table used : auto_summary_settings   (added in _migrate_db)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import database as db

if TYPE_CHECKING:
    from main import BaseBot
    from highrise import User


async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_autosummary_enabled(user_id: str) -> bool:
    """True if user has auto-summary DMs enabled (default ON)."""
    try:
        conn = db.get_connection()
        row  = conn.execute(
            "SELECT enabled FROM auto_summary_settings WHERE user_id=?",
            (user_id,),
        ).fetchone()
        conn.close()
        return bool(row["enabled"]) if row else True
    except Exception:
        return True


def set_autosummary_enabled(user_id: str, username: str, enabled: bool) -> None:
    try:
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO auto_summary_settings
                   (user_id, username, enabled, updated_at)
               VALUES (?,?,?,datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET
                 enabled=excluded.enabled,
                 updated_at=excluded.updated_at""",
            (user_id, username.lower(), 1 if enabled else 0),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def handle_autosummary(
    bot: "BaseBot", user: "User", args: list[str],
) -> None:
    """!autosummary [on|off] — show last session summary or toggle DM setting."""
    db.ensure_user(user.id, user.username)
    sub = args[1].lower() if len(args) >= 2 else ""

    if sub in ("on", "off"):
        on = sub == "on"
        set_autosummary_enabled(user.id, user.username, on)
        state = "ON" if on else "OFF"
        note  = (
            "Sessions will DM you via ChillTopiaBot."
            if on else "Session DMs disabled."
        )
        await _w(bot, user.id,
                 f"📊 Auto-summary DMs: {state}\n{note}")
        return

    enabled = get_autosummary_enabled(user.id)
    status  = "ON" if enabled else "OFF"

    mine_txt = db.get_auto_session_summary(user.id, "mining")
    fish_txt = db.get_auto_session_summary(user.id, "fishing")

    if not mine_txt and not fish_txt:
        await _w(bot, user.id,
                 f"📊 Auto Summary\nStatus: {status}\n"
                 f"No session found yet.")
        return

    if mine_txt:
        await _w(bot, user.id,
                 f"⛏️ Last Mine Session\n{mine_txt[:210]}")
    if fish_txt:
        await _w(bot, user.id,
                 f"🎣 Last Fish Session\n{fish_txt[:210]}")
    await _w(bot, user.id,
             f"DM Summaries: {status}\nToggle: !autosummary on/off")


async def handle_minesummary(
    bot: "BaseBot", user: "User", args: list[str] | None = None,
) -> None:
    """!minesummary — show last auto-mine session summary."""
    db.ensure_user(user.id, user.username)
    txt = db.get_auto_session_summary(user.id, "mining")
    if not txt:
        await _w(bot, user.id,
                 "⛏️ Mining Summary\nNo auto-mine session yet.\n"
                 "Start with: !automine")
        return
    await _w(bot, user.id, f"⛏️ Mining Summary\n{txt[:220]}")


async def handle_fishsummary(
    bot: "BaseBot", user: "User", args: list[str] | None = None,
) -> None:
    """!fishsummary — show last auto-fish session summary."""
    db.ensure_user(user.id, user.username)
    txt = db.get_auto_session_summary(user.id, "fishing")
    if not txt:
        await _w(bot, user.id,
                 "🎣 Fishing Summary\nNo auto-fish session yet.\n"
                 "Start with: !autofish")
        return
    await _w(bot, user.id, f"🎣 Fishing Summary\n{txt[:220]}")
