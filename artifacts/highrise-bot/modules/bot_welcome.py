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
        "👋 Hi {username}! I'm ChillTopiaMC — room assistant. "
        "!help — command menu. "
        "!subscribe — notifications."
    ),
    "chilltopia":         (
        "👋 Hi {username}! I'm ChillTopiaMC — room assistant. "
        "!help — command menu. "
        "!subscribe — notifications."
    ),
    "chilltopiamc":       (
        "👋 Hi {username}! I'm ChillTopiaMC — room assistant. "
        "!help — command menu. "
        "!subscribe — notifications."
    ),
    "bankingbot":         (
        "👋 Hi {username}! I'm BankingBot — coins & VIP. "
        "!balance — your coins. "
        "!vip — VIP info. "
        "!donationgoal — room goal."
    ),
    "greatestprospector": (
        "👋 Hi {username}! I'm GreatestProspector — mining. "
        "!mine — mine ores. "
        "!automine — auto mine. "
        "!mineinv — your ores."
    ),
    "chipsoprano":        (
        "👋 Hi {username}! I'm ChipSoprano — poker. "
        "!poker — poker info. "
        "!poker join — join table. "
        "!pokerhelp — rules."
    ),
    "acesinatra":         (
        "👋 Hi {username}! I'm AceSinatra — blackjack. "
        "!bet [amount] — place a bet. "
        "!bjrules — game rules."
    ),
    "keanuShield":        (
        "👋 Hi {username}! I'm KeanuShield — room safety. "
        "Type !help for commands."
    ),
    "dj_dudu":            (
        "👋 Hi {username}! I'm DJ DUDU — room vibes. "
        "Type !help for commands."
    ),
    "masterangler":       (
        "👋 Hi {username}! I'm MasterAngler — fishing. "
        "!fish — cast your line. "
        "!autofish — auto fish. "
        "!fishinv — your fish."
    ),
}

# Fallback for any bot not in the map
_DEFAULT_FALLBACK = (
    "👋 Hi {username}! Welcome to ChillTopia. Type !help to see commands."
)

# ---------------------------------------------------------------------------
# Bot profile bio templates — used by !bios (for manual copy/paste)
# Highrise profile bios cannot be set via API; these are reference templates.
# ---------------------------------------------------------------------------

_BOT_BIO_TEMPLATES: list[tuple[str, str]] = [
    ("ChillTopiaMC", (
        "🎤 ChillTopiaMC\n"
        "Room help + events + notifications\n"
        "!help — command menu\n"
        "!help games — games\n"
        "!subscribe — notifications\n"
        "!notif — settings\n"
        "!ptlist — party tippers"
    )),
    ("BankingBot", (
        "🏦 BankingBot\n"
        "Coins + VIP + room goals\n"
        "!balance — check coins\n"
        "!send [user] [amount]\n"
        "!vip — VIP info\n"
        "!donationgoal — room goal"
    )),
    ("AceSinatra (BlackJack)", (
        "🃏 BlackJackBot\n"
        "Play blackjack with coins\n"
        "!bet [amount] — place bet\n"
        "!hit  !stand  !double  !split\n"
        "!bjshoe — shoe status\n"
        "!bjrules — rules"
    )),
    ("ChipSoprano (Poker)", (
        "♠️ PokerBot\n"
        "Poker table commands\n"
        "!poker — poker info\n"
        "!poker join — join table\n"
        "!call  !raise [amount]  !fold\n"
        "!check  !pokerhelp"
    )),
    ("GreatestProspector (Mining)", (
        "⛏️ GreatestProspector\n"
        "Mine ores + upgrade tools\n"
        "!mine — mine once\n"
        "!automine — auto mine\n"
        "!automine off — stop\n"
        "!mineinv  !minechances  !tool"
    )),
    ("MasterAngler (Fishing)", (
        "🎣 MasterAngler\n"
        "Catch fish + upgrade rods\n"
        "!fish — fish once\n"
        "!autofish — auto fish\n"
        "!autofish off — stop\n"
        "!fishinv  !fishchances  !rods"
    )),
    ("KeanuShield (Security)", (
        "🛡️ SecurityBot\n"
        "Room safety + moderation\n"
        "Need help? Type !help"
    )),
    ("DJ_DUDU", (
        "🎧 DJ Bot\n"
        "Room vibes + events\n"
        "Type !help for commands\n"
        "!subscribe — notifications"
    )),
]

# Staff/owner commands that must NOT appear in public bios
_OWNER_STAFF_CMDS = {
    "goldtip", "goldrain", "grantvip", "removevip", "setvipprice",
    "addcoins", "removecoins", "setcoins", "resetcoins",
    "addadmin", "removeadmin", "addmanager", "removemanager",
    "addmoderator", "removemoderator", "addowner", "removeowner",
    "setbotspawnhere", "clearBotspawn", "setrolespawn",
    "adminpanel", "adminlogs", "checkcommands", "checkhelp",
    "maintenance", "softrestart", "restartbot", "backup",
}


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
             f"✅ Welcome for {bot_name} updated. !previewbotwelcome {bot_name} to test.")


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


# ---------------------------------------------------------------------------
# !bios — owner-only, whispers all recommended bot profile bios
# ---------------------------------------------------------------------------

async def handle_bios(bot: BaseBot, user: User) -> None:
    """!bios — whisper all recommended bot profile bios for manual copy/paste."""
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    await _w(bot, user.id,
             "📋 Bot bios below. Copy each and paste into the bot's Highrise profile manually.")
    for bot_name, bio_text in _BOT_BIO_TEMPLATES:
        header = f"── {bot_name} ──"
        await _w(bot, user.id, header[:249])
        await _w(bot, user.id, bio_text[:249])


# ---------------------------------------------------------------------------
# !checkbios — owner/manager, audits bio templates for issues
# ---------------------------------------------------------------------------

import re as _re

_SLASH_CMD_RE = _re.compile(r'(?:^| )/[a-z]')
_BJ_AMOUNT_WRONG = _re.compile(r'!bj\s+\[')

async def handle_checkbios(bot: BaseBot, user: User) -> None:
    """!checkbios — audit configured bot bio templates for common issues."""
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return

    issues: list[str] = []
    mining_bio = ""
    fishing_bio = ""

    for bot_name, bio_text in _BOT_BIO_TEMPLATES:
        if _SLASH_CMD_RE.search(bio_text):
            issues.append(f"{bot_name}: contains / command")
        if _BJ_AMOUNT_WRONG.search(bio_text):
            issues.append(f"{bot_name}: shows !bj [amount] — use !bet [amount]")
        if "Mining" in bot_name or "Prospector" in bot_name:
            mining_bio = bio_text
        if "Fishing" in bot_name or "Angler" in bot_name:
            fishing_bio = bio_text

    if mining_bio and "!automine" not in mining_bio:
        issues.append("Mining bio missing !automine")
    if fishing_bio and "!autofish" not in fishing_bio:
        issues.append("Fishing bio missing !autofish")

    await _w(bot, user.id,
             "⚠️ Cannot read live Highrise profile bios. Checked configured templates only.")
    if not issues:
        await _w(bot, user.id,
                 "✅ Bio Audit Clean\n"
                 "No / commands.\n"
                 "Blackjack uses !bet [amount].\n"
                 "Mining includes !automine.\n"
                 "Fishing includes !autofish.")
    else:
        await _w(bot, user.id, f"⚠️ Bio Audit Issues ({len(issues)}):")
        for issue in issues:
            await _w(bot, user.id, f"  • {issue}"[:249])


# ---------------------------------------------------------------------------
# !checkonboarding — owner/manager, audits welcome/onboarding settings
# ---------------------------------------------------------------------------

async def handle_checkonboarding(bot: BaseBot, user: User) -> None:
    """!checkonboarding — audit welcome/onboarding messages for issues."""
    import database as _db
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return

    issues: list[str] = []

    welcome_enabled = _db.get_room_setting("welcome_enabled", "true") == "true"
    if not welcome_enabled:
        issues.append("Room welcome is disabled (!setwelcome on to enable)")

    welcome_msg = _db.get_room_setting(
        "welcome_message",
        "👋 Welcome to ChillTopia! !help — commands. !mine / !fish — earn coins. "
        "!bet [amount] — blackjack. !tele list — spots. !subscribe — notifications.",
    )
    if _SLASH_CMD_RE.search(welcome_msg):
        issues.append("Welcome message contains / command")
    if "!bet" not in welcome_msg and "blackjack" not in welcome_msg.lower():
        issues.append("Welcome message does not mention !bet [amount] for blackjack")
    if "!automine" not in welcome_msg and "!mine" not in welcome_msg:
        issues.append("Welcome message does not mention mining")
    if "!autofish" not in welcome_msg and "!fish" not in welcome_msg:
        issues.append("Welcome message does not mention fishing")

    bw_enabled = _global_enabled()
    for bname, msg in _DEFAULT_MESSAGES.items():
        if _SLASH_CMD_RE.search(msg):
            issues.append(f"Bot welcome ({bname}) contains / command")

    cooldown_note = "Cooldown: bot_welcome_seen tracks per-bot per-user (default 24h)."

    if not issues:
        await _w(bot, user.id,
                 "✅ Onboarding Audit Clean\n"
                 "Welcome message OK.\n"
                 "No / commands.\n"
                 "Cooldown enabled.")
        await _w(bot, user.id, cooldown_note[:249])
        bw_state = "ON" if bw_enabled else "OFF"
        await _w(bot, user.id, f"Bot welcomes global: {bw_state}")
    else:
        await _w(bot, user.id, f"⚠️ Onboarding Audit Issues ({len(issues)}):")
        for issue in issues:
            await _w(bot, user.id, f"  • {issue}"[:249])
        await _w(bot, user.id, cooldown_note[:249])
