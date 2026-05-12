"""
modules/control_panel.py
Bot Control Center — /control, /status, /quicktoggles, /toggle
All messages ≤ 249 chars.
"""

from __future__ import annotations
import database as db
from modules.permissions import (
    is_owner, can_manage_economy, can_manage_games, can_moderate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _w(bot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


def _bj_on() -> str:
    return "ON" if int(db.get_bj_settings().get("bj_enabled", 1)) else "OFF"


def _rbj_on() -> str:
    return "ON" if int(db.get_rbj_settings().get("rbj_enabled", 1)) else "OFF"


def _poker_on() -> str:
    try:
        from modules.poker import get_poker_state_str
        s = db.get_poker_settings()
        return "ON" if int(s.get("poker_enabled", 1)) else "OFF"
    except Exception:
        return "?"


def _mining_on() -> str:
    return "ON" if db.get_mine_setting("mining_enabled", "true") == "true" else "OFF"


def _welcome_on() -> str:
    return "ON" if db.get_room_setting("welcome_enabled", "false") == "true" else "OFF"


def _intervals_on() -> str:
    return "ON" if db.get_room_setting("intervals_enabled", "false") == "true" else "OFF"


def _botprefix_on() -> str:
    return "ON" if db.get_room_setting("bot_prefix_enabled", "true") == "true" else "OFF"


def _event_status() -> str:
    return "active" if db.is_event_active() else "none"


# ---------------------------------------------------------------------------
# /control  —  main dispatcher
# ---------------------------------------------------------------------------

_MAIN_PANEL = (
    "⚙️ Control\n"
    "/control room - room tools\n"
    "/control economy - coins/bank\n"
    "/control casino - BJ/RBJ/Poker\n"
    "/control games - mining/events"
)
_MAIN_EXTRA = "More: /control shop | staff | system"

_ROOM_PAGES = [
    (
        "🏠 Room\n"
        "/roomsettings - settings\n"
        "/spawns - spawns\n"
        "/welcomehelp - welcome\n"
        "/intervals - auto msgs"
    ),
    (
        "🏠 Room 2\n"
        "/teleporthelp - teleport\n"
        "/emotehelp - emotes\n"
        "/socialhelp - social\n"
        "/botmodehelp - bot modes"
    ),
]

_ECONOMY_PAGES = [
    (
        "💰 Economy\n"
        "/bal user - balance\n"
        "/setcoins user amt - set\n"
        "/addcoins user amt - add\n"
        "/adminlogs - logs"
    ),
    (
        "🏦 Bank\n"
        "/banksettings - settings\n"
        "/viewtx user - tx\n"
        "/bankblock user - block\n"
        "/resetbanklimits user - reset"
    ),
]

_CASINO_PAGES = [
    (
        "🎰 Casino\n"
        "/casinosettings - settings\n"
        "/bj limits - BJ\n"
        "/rbj limits - RBJ\n"
        "/poker settings - poker"
    ),
    (
        "🎰 Casino 2\n"
        "/bj recover\n"
        "/rbj recover\n"
        "/poker state\n"
        "/poker cleanup\n"
        "/poker refundtable"
    ),
]

_GAMES_PAGES = [
    (
        "🎮 Games\n"
        "/miningadmin - mining\n"
        "/miningevent - event\n"
        "/startminingevent id\n"
        "/minelb - mining LB"
    ),
    (
        "🎉 Events\n"
        "/events - list\n"
        "/startevent id\n"
        "/stopevent\n"
        "/eventshop"
    ),
]

_SHOP_PAGES = [
    (
        "🛒 Shop\n"
        "/shopadmin - shop admin\n"
        "/shop badges\n"
        "/shop titles\n"
        "/vipshop"
    ),
    (
        "🏷️ Badges\n"
        "/addbadge id emoji name rarity price\n"
        "/givebadge user id\n"
        "/badgemarketlogs"
    ),
]

_STAFF_PAGES = [
    (
        "🛡️ Staff\n"
        "/allstaff - list\n"
        "/addmoderator user\n"
        "/addmanager user\n"
        "/reports - reports"
    ),
    (
        "🔨 Moderation\n"
        "/warn user reason\n"
        "/mute user min reason\n"
        "/ban user reason\n"
        "/modlog user"
    ),
    (
        "👑 Owner Roles\n"
        "/addowner user\n"
        "/addadmin user\n"
        "/removeadmin user\n"
        "/owners"
    ),
]

_SYSTEM_PAGES = [
    (
        "🧰 System\n"
        "/healthcheck - health\n"
        "/restartstatus - restart\n"
        "/checkcommands - routes\n"
        "/checkhelp - help"
    ),
    (
        "🧰 System 2\n"
        "/missingcommands\n"
        "/silentcheck\n"
        "/roomlogs\n"
        "/botoutfitlogs"
    ),
    (
        "👑 Owner System\n"
        "/backup\n"
        "/softrestart\n"
        "/adminlogs\n"
        "/dbstats"
    ),
]


async def handle_control(bot, user, args: list[str]) -> None:
    """/control [section] [page]"""
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return

    sub   = args[1].lower() if len(args) > 1 else ""
    page  = int(args[2]) if len(args) > 2 and args[2].isdigit() else (
            int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
    )

    # ── Numeric page of main panel
    if sub.isdigit():
        await _w(bot, user.id, _MAIN_PANEL)
        if can_manage_economy(user.username):
            await _w(bot, user.id, _MAIN_EXTRA)
        return

    # ── Sub-panel dispatchers
    if sub in ("room",):
        pages = _ROOM_PAGES
    elif sub in ("economy",):
        if not can_manage_economy(user.username):
            await _w(bot, user.id, "Admin and owner only.")
            return
        pages = _ECONOMY_PAGES
    elif sub in ("casino",):
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Manager and above only.")
            return
        pages = _CASINO_PAGES
    elif sub in ("games",):
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Manager and above only.")
            return
        pages = _GAMES_PAGES
    elif sub in ("shop",):
        if not can_manage_economy(user.username):
            await _w(bot, user.id, "Admin and owner only.")
            return
        pages = _SHOP_PAGES
    elif sub in ("staff",):
        pages = _STAFF_PAGES
    elif sub in ("system",):
        if not can_manage_economy(user.username):
            await _w(bot, user.id, "Admin and owner only.")
            return
        pages = _SYSTEM_PAGES
    else:
        # Default — main panel
        await _w(bot, user.id, _MAIN_PANEL)
        if can_manage_economy(user.username):
            await _w(bot, user.id, _MAIN_EXTRA)
        return

    # Page filtering (staff page 3 is owner-only)
    if sub == "staff" and page == 3 and not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    if sub == "system" and page == 3 and not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return

    n = len(pages)
    if page < 1 or page > n:
        await _w(bot, user.id, f"Pages 1-{n} for /control {sub}.")
        return
    await _w(bot, user.id, pages[page - 1])


# ---------------------------------------------------------------------------
# /adminpanel alias  (already exists in admin_cmds; kept separate here
# for /managerpanel and /ownerpanel shortcuts)
# ---------------------------------------------------------------------------

async def handle_ownerpanel(bot, user) -> None:
    """/ownerpanel — owner-only control hub shortcut."""
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    await _w(bot, user.id, _MAIN_PANEL)
    await _w(bot, user.id, _MAIN_EXTRA)
    await _w(bot, user.id, "👑 Owner: /control staff 3 | system 3 | /backup | /softrestart")


async def handle_managerpanel(bot, user) -> None:
    """/managerpanel — manager control hub shortcut."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Manager and above only.")
        return
    await _w(bot, user.id, _MAIN_PANEL)
    await _w(bot, user.id, "🧰 Manager: /control room | games | casino")


# ---------------------------------------------------------------------------
# /status  /roomstatus
# ---------------------------------------------------------------------------

async def handle_status(bot, user) -> None:
    """/status — public summary; staff get an extended view."""
    mining  = _mining_on()
    bj_s    = db.get_bj_settings()
    rbj_s   = db.get_rbj_settings()
    casino  = "ON" if int(bj_s.get("bj_enabled", 1)) or int(rbj_s.get("rbj_enabled", 1)) else "OFF"
    try:
        from modules.poker import get_poker_state_str
        pstate = get_poker_state_str()
    except Exception:
        pstate = "?"
    event = _event_status()

    if not can_moderate(user.username):
        await _w(
            bot, user.id,
            f"🤖 Bot: Online | Mining: {mining} | Casino: {casino}\n"
            f"Poker: {pstate} | Event: {event}"
        )
        return

    # Staff extended view
    try:
        from modules.blackjack           import _state as _bj
        from modules.realistic_blackjack import _state as _rbj
        bj_phase  = getattr(_bj,  "phase", "idle")
        rbj_phase = getattr(_rbj, "phase", "idle")
    except Exception:
        bj_phase = rbj_phase = "?"

    bj_on  = "ON"  if int(bj_s.get("bj_enabled",  1)) else "OFF"
    rbj_on = "ON"  if int(rbj_s.get("rbj_enabled", 1)) else "OFF"

    await _w(
        bot, user.id,
        f"✅ Bot: OK | Mining: {mining} | Event: {event}\n"
        f"BJ: {bj_on}[{bj_phase}] | RBJ: {rbj_on}[{rbj_phase}]\n"
        f"Poker: {pstate} | Welcome: {_welcome_on()}"
    )


async def handle_roomstatus(bot, user) -> None:
    """/roomstatus — staff room-utility status snapshot."""
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    await _w(
        bot, user.id,
        f"🏠 Room Status\n"
        f"Welcome: {_welcome_on()} | Intervals: {_intervals_on()}\n"
        f"BotPrefix: {_botprefix_on()}\n"
        f"Use !roomsettings for full config."
    )


# ---------------------------------------------------------------------------
# /quicktoggles
# ---------------------------------------------------------------------------

async def handle_quicktoggles(bot, user) -> None:
    """/quicktoggles — show all toggle states at a glance."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Manager and above only.")
        return
    await _w(
        bot, user.id,
        f"⚡ Toggles:\n"
        f"Mining: {_mining_on()} | BJ: {_bj_on()} | RBJ: {_rbj_on()}\n"
        f"Poker: {_poker_on()} | Welcome: {_welcome_on()}\n"
        f"Intervals: {_intervals_on()} | BotPrefix: {_botprefix_on()}"
    )


# ---------------------------------------------------------------------------
# /toggle <module>
# ---------------------------------------------------------------------------

async def handle_toggle(bot, user, args: list[str]) -> None:
    """/toggle <mining|bj|rbj|poker|welcome|intervals|botprefix>"""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Manager and above only.")
        return

    if len(args) < 2:
        await _w(
            bot, user.id,
            "Usage: !toggle <mining|bj|rbj|poker|welcome|intervals|botprefix>"
        )
        return

    target = args[1].lower()

    if target == "mining":
        cur = db.get_mine_setting("mining_enabled", "true")
        new = "false" if cur == "true" else "true"
        db.set_mine_setting("mining_enabled", new)
        label = "ON" if new == "true" else "OFF"
        icon  = "✅" if new == "true" else "⛔"
        await _w(bot, user.id, f"{icon} Mining {label}.")

    elif target == "bj":
        cur = int(db.get_bj_settings().get("bj_enabled", 1))
        new = 0 if cur else 1
        db.set_bj_setting("bj_enabled", new)
        label = "ON" if new else "OFF"
        icon  = "✅" if new else "⛔"
        await _w(bot, user.id, f"{icon} BJ {label}.")

    elif target == "rbj":
        cur = int(db.get_rbj_settings().get("rbj_enabled", 1))
        new = 0 if cur else 1
        db.set_rbj_setting("rbj_enabled", new)
        label = "ON" if new else "OFF"
        icon  = "✅" if new else "⛔"
        await _w(bot, user.id, f"{icon} RBJ {label}.")

    elif target == "poker":
        try:
            from modules.poker import _set as _poker_set, _s as _poker_get
            cur = int(_poker_get("poker_enabled", 1))
            new = 0 if cur else 1
            _poker_set("poker_enabled", new)
            label = "ON" if new else "OFF"
            icon  = "✅" if new else "⛔"
            await _w(bot, user.id, f"{icon} Poker {label}.")
        except Exception as exc:
            await _w(bot, user.id, f"❌ Could not toggle poker: {exc}"[:249])

    elif target == "welcome":
        cur = db.get_room_setting("welcome_enabled", "false")
        new = "false" if cur == "true" else "true"
        db.set_room_setting("welcome_enabled", new)
        label = "ON" if new == "true" else "OFF"
        icon  = "✅" if new == "true" else "⛔"
        await _w(bot, user.id, f"{icon} Welcome messages {label}.")

    elif target == "intervals":
        cur = db.get_room_setting("intervals_enabled", "false")
        new = "false" if cur == "true" else "true"
        db.set_room_setting("intervals_enabled", new)
        label = "ON" if new == "true" else "OFF"
        icon  = "✅" if new == "true" else "⛔"
        await _w(bot, user.id, f"{icon} Interval messages {label}.")

    elif target == "botprefix":
        cur = db.get_room_setting("bot_prefix_enabled", "true")
        new = "false" if cur == "true" else "true"
        db.set_room_setting("bot_prefix_enabled", new)
        label = "ON" if new == "true" else "OFF"
        icon  = "✅" if new == "true" else "⛔"
        await _w(bot, user.id, f"{icon} Bot prefix {label}.")

    else:
        await _w(
            bot, user.id,
            f"Unknown toggle '{target}'. "
            "Options: mining bj rbj poker welcome intervals botprefix"
        )
