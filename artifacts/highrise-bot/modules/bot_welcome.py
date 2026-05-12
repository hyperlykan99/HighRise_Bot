"""
modules/bot_welcome.py
----------------------
Configurable per-bot whispered welcome messages.

Each bot whispers its own personalized welcome to a player once per
cooldown window (default 24 h).  Messages support placeholders:
  {username}, {bot}, {prefix}, {help_command}
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone, timedelta

import database as db
from highrise import BaseBot, User
from modules.permissions import is_admin, is_owner, is_manager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, str(msg)[:249])


def _can_manage(username: str) -> bool:
    return is_manager(username) or is_admin(username) or is_owner(username)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Default messages per bot username (lowercase key)
# ---------------------------------------------------------------------------

_DEFAULT_MESSAGES: dict[str, str] = {
    "emceebot":           (
        "Welcome {username}! I'm ChillTopiaMC. "
        "I handle room commands, events & AI help. "
        "Type !help or ask 'Chill, what can I do?'"
    ),
    "chilltopia":         (
        "Welcome {username}! I'm ChillTopiaMC. "
        "I handle room commands, events & AI help. "
        "Type !help or ask 'Chill, what can I do?'"
    ),
    "chilltopiamc":       (
        "Welcome {username}! I'm ChillTopiaMC. "
        "I handle room commands, events & AI help. "
        "Type !help or ask 'Chill, what can I do?'"
    ),
    "bankingbot":         (
        "Welcome {username}! I'm BankingBot. "
        "I handle coins, balances, transfers & gold tips. "
        "Type /bal or /bankhelp."
    ),
    "greatestprospector": (
        "Welcome {username}! I'm GreatestProspector. "
        "I run mining, ores, tools & ore weight leaderboards. "
        "Type /mine or /minehelp."
    ),
    "chipsoprano":        (
        "Welcome {username}! I'm ChipSoprano. "
        "I run poker tables. "
        "Type /pokerhelp or /poker join."
    ),
    "acesinatra":         (
        "Welcome {username}! I run Realistic Blackjack. "
        "Type /bj or /bjhelp."
    ),
    "keanuShield":        (
        "Welcome {username}! I'm KeanuShield. "
        "I help staff with room safety and controls."
    ),
    "dj_dudu":            (
        "Welcome {username}! I'm the DJ. "
        "I manage the music queue. "
        "Type /djhelp to see what I can do."
    ),
    "masterangler":       (
        "Welcome {username}! I'm MasterAngler. "
        "I run fishing — rods, catches & leaderboards. "
        "Type /fish to cast or /fishhelp for commands."
    ),
}

# Fallback for any bot not in the map
_DEFAULT_FALLBACK = (
    "Welcome {username}! Type /help to see what's available."
)


def _get_default_message(bot_username: str) -> str:
    return _DEFAULT_MESSAGES.get(bot_username.lower(), _DEFAULT_FALLBACK)


# ---------------------------------------------------------------------------
# DB helpers — bot_welcome_settings / bot_welcome_seen
# ---------------------------------------------------------------------------

def _get_setting(bot_username: str, key: str, default: str = "") -> str:
    conn = db.get_connection()
    row = conn.execute(
        "SELECT value FROM bot_welcome_settings WHERE bot_username=? AND key=?",
        (bot_username.lower(), key),
    ).fetchone()
    conn.close()
    return row["value"] if row else default


def _set_setting(bot_username: str, key: str, value: str) -> None:
    conn = db.get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO bot_welcome_settings
           (bot_username, key, value, updated_at)
           VALUES (?, ?, ?, datetime('now'))""",
        (bot_username.lower(), key, value),
    )
    conn.commit()
    conn.close()


def _global_enabled() -> bool:
    conn = db.get_connection()
    row = conn.execute(
        "SELECT value FROM room_settings WHERE key='bot_welcomes_enabled'"
    ).fetchone()
    conn.close()
    return (row["value"] if row else "1") == "1"


def _set_global_enabled(enabled: bool) -> None:
    db.set_room_setting("bot_welcomes_enabled", "1" if enabled else "0")


def _bot_enabled(bot_username: str) -> bool:
    return _get_setting(bot_username, "enabled", "1") == "1"


def _get_message(bot_username: str) -> str:
    custom = _get_setting(bot_username, "message", "")
    return custom if custom else _get_default_message(bot_username)


def _get_cooldown_hours(bot_username: str) -> int:
    try:
        return int(_get_setting(bot_username, "cooldown_hours", "24"))
    except ValueError:
        return 24


def _should_send(bot_username: str, user_id: str) -> bool:
    """True if cooldown has expired (or never sent)."""
    hours = _get_cooldown_hours(bot_username)
    conn  = db.get_connection()
    row   = conn.execute(
        """SELECT last_sent_at FROM bot_welcome_seen
           WHERE bot_username=? AND user_id=?""",
        (bot_username.lower(), user_id),
    ).fetchone()
    conn.close()
    if row is None:
        return True
    try:
        last = datetime.fromisoformat(row["last_sent_at"].replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last) >= timedelta(hours=hours)
    except Exception:
        return True


def _mark_sent(bot_username: str, user_id: str, username: str) -> None:
    conn = db.get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO bot_welcome_seen
           (bot_username, user_id, username, last_sent_at)
           VALUES (?, ?, ?, datetime('now'))""",
        (bot_username.lower(), user_id, username.lower()),
    )
    conn.commit()
    conn.close()


def _render(template: str, username: str, bot_username: str,
            prefix: str = "", help_cmd: str = "!help") -> str:
    return template.format(
        username=username,
        bot=bot_username,
        prefix=prefix,
        help_command=help_cmd,
    )


# ---------------------------------------------------------------------------
# send_bot_welcome  — called from on_user_join for each bot process
# ---------------------------------------------------------------------------

async def send_bot_welcome(
    bot: BaseBot,
    user: User,
    this_bot_username: str,
    stagger_seconds: float = 0.0,
) -> None:
    """
    Whisper the per-bot welcome to `user` if enabled and cooldown passed.
    `this_bot_username` is the username of the bot calling this.
    """
    if not _global_enabled():
        return
    if not _bot_enabled(this_bot_username):
        return
    if not _should_send(this_bot_username, user.id):
        return
    if stagger_seconds > 0:
        await asyncio.sleep(stagger_seconds)
    template = _get_message(this_bot_username)
    msg = _render(template, user.username, this_bot_username)
    try:
        await bot.highrise.send_whisper(user.id, msg[:249])
        _mark_sent(this_bot_username, user.id, user.username)
    except Exception as exc:
        print(f"[BOTWELCOME] Error welcoming @{user.username}: {exc}")


# ---------------------------------------------------------------------------
# /botwelcome   — show status
# ---------------------------------------------------------------------------

async def handle_botwelcome(bot: BaseBot, user: User) -> None:
    """/botwelcome — show global bot welcome status."""
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    enabled = "ON" if _global_enabled() else "OFF"
    await _w(bot, user.id,
             f"<#66CCFF>Bot Welcomes<#FFFFFF>: {enabled}")
    await _w(bot, user.id,
             "Use !setbotwelcome [bot] [msg] to customize. "
             "!botwelcomes on|off to toggle.")


# ---------------------------------------------------------------------------
# /setbotwelcome <bot_username> <message>
# ---------------------------------------------------------------------------

async def handle_setbotwelcome(bot: BaseBot, user: User, args: list[str]) -> None:
    """/setbotwelcome <bot> <message> — set custom welcome for a bot."""
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 3:
        await _w(bot, user.id,
                 "Usage: !setbotwelcome <bot_username> <message>")
        return
    bot_name = args[1]
    message  = " ".join(args[2:])
    _set_setting(bot_name, "message", message)
    await _w(bot, user.id,
             f"✅ Welcome for {bot_name} updated. /previewbotwelcome {bot_name} to test.")


# ---------------------------------------------------------------------------
# /resetbotwelcome <bot_username>
# ---------------------------------------------------------------------------

async def handle_resetbotwelcome(bot: BaseBot, user: User, args: list[str]) -> None:
    """/resetbotwelcome <bot> — restore default welcome message."""
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !resetbotwelcome <bot_username>")
        return
    bot_name = args[1]
    _set_setting(bot_name, "message", "")
    await _w(bot, user.id,
             f"✅ Welcome for {bot_name} reset to default.")


# ---------------------------------------------------------------------------
# /previewbotwelcome <bot_username>
# ---------------------------------------------------------------------------

async def handle_previewbotwelcome(bot: BaseBot, user: User, args: list[str]) -> None:
    """/previewbotwelcome <bot> — whisper a preview of the bot's welcome."""
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !previewbotwelcome <bot_username>")
        return
    bot_name = args[1]
    template = _get_message(bot_name)
    preview  = _render(template, user.username, bot_name)
    await _w(bot, user.id, f"Preview ({bot_name}): {preview}"[:249])


# ---------------------------------------------------------------------------
# /botwelcomes on|off   — global toggle
# ---------------------------------------------------------------------------

async def handle_botwelcomes(bot: BaseBot, user: User, args: list[str]) -> None:
    """/botwelcomes on|off — globally enable or disable bot welcome whispers."""
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    sub = args[1].lower() if len(args) > 1 else ""
    if sub in ("on", "enable", "1", "true"):
        _set_global_enabled(True)
        await _w(bot, user.id, "✅ Bot welcome whispers ON.")
    elif sub in ("off", "disable", "0", "false"):
        _set_global_enabled(False)
        await _w(bot, user.id, "✅ Bot welcome whispers OFF.")
    else:
        cur = "ON" if _global_enabled() else "OFF"
        await _w(bot, user.id,
                 f"Bot welcomes: {cur}. Usage: !botwelcomes on | off")
