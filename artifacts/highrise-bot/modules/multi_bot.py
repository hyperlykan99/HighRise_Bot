"""
modules/multi_bot.py
Multi-bot system — command ownership gating, heartbeat, staff controls.

When BOT_MODE="all" (default), should_this_bot_handle() always returns True
and the bot behaves exactly as before — fully backwards-compatible.
"""

from __future__ import annotations
import asyncio
import time
from datetime import datetime, timezone

import database as db
from config import BOT_ID, BOT_MODE, BOT_USERNAME
from modules.permissions import is_owner, can_manage_economy, can_manage_games, can_moderate

# ---------------------------------------------------------------------------
# Default command → bot_mode ownership map
# DB table bot_command_ownership can override any entry.
# ---------------------------------------------------------------------------

_DEFAULT_COMMAND_OWNERS: dict[str, str] = {
    # host
    "help": "host", "mycommands": "host", "helpsearch": "host",
    "tutorial": "host", "guide": "host", "newbiehelp": "host",
    "profile": "host", "me": "host", "whois": "host", "pinfo": "host",
    "privacy": "host", "rules": "host", "roleshelp": "host",
    "players": "host", "roomlist": "host", "online": "host",
    "roomhelp": "host", "teleporthelp": "host", "emotehelp": "host",
    "alerthelp": "host", "welcomehelp": "host", "socialhelp": "host",
    "control": "host", "status": "host", "roomstatus": "host",
    "botmodehelp": "host", "multibothelp": "host",
    # banker
    "bal": "banker", "balance": "banker", "b": "banker",
    "wallet": "banker", "w": "banker",
    "coins": "banker", "coin": "banker", "money": "banker",
    "send": "banker", "bank": "banker",
    "transactions": "banker", "bankstats": "banker",
    "bankhelp": "banker", "banknotify": "banker",
    "daily": "banker", "leaderboard": "banker", "lb": "banker",
    "addcoins": "banker", "setcoins": "banker", "removecoins": "banker",
    "resetcoins": "banker", "editcoins": "banker",
    "viewtx": "banker", "ledger": "banker",
    "bankblock": "banker", "bankunblock": "banker",
    "coinhelp": "banker", "bankadminhelp": "banker",
    "dash": "banker", "dashboard": "banker",
    # dealer
    "casino": "dealer", "casinohelp": "dealer",
    "bj": "dealer", "bjoin": "dealer",
    "bh": "dealer", "bs": "dealer", "bd": "dealer", "bsp": "dealer",
    "bt": "dealer", "bhand": "dealer", "blimits": "dealer",
    "bstats": "dealer", "bjhelp": "dealer",
    "rbj": "dealer", "rjoin": "dealer",
    "rh": "dealer", "rs": "dealer", "rd": "dealer", "rsp": "dealer",
    "rt": "dealer", "rhand": "dealer", "rshoe": "dealer",
    "rlimits": "dealer", "rstats": "dealer", "rbjhelp": "dealer",
    "poker": "dealer", "p": "dealer",
    "pj": "dealer", "pt": "dealer", "ptable": "dealer",
    "ph": "dealer", "pcards": "dealer", "po": "dealer", "podds": "dealer",
    "check": "dealer", "ch": "dealer",
    "call": "dealer", "ca": "dealer",
    "raise": "dealer", "r": "dealer",
    "fold": "dealer", "f": "dealer",
    "allin": "dealer", "ai": "dealer", "shove": "dealer",
    "pp": "dealer", "pplayers": "dealer", "pstats": "dealer",
    "plb": "dealer", "pleaderboard": "dealer",
    "pokerhelp": "dealer", "pokerlb": "dealer",
    "sitout": "dealer", "sitin": "dealer", "rebuy": "dealer",
    "pstacks": "dealer", "mystack": "dealer",
    "casinosettings": "dealer", "casinolimits": "dealer",
    "casinotoggles": "dealer", "casinoadminhelp": "dealer",
    "casinodash": "dealer", "mycasino": "dealer",
    # miner
    "mine": "miner", "m": "miner", "dig": "miner",
    "ores": "miner", "mineinv": "miner",
    "tool": "miner", "pickaxe": "miner",
    "upgradetool": "miner", "upick": "miner",
    "sellores": "miner", "sellore": "miner",
    "mineprofile": "miner", "mp": "miner",
    "minelb": "miner", "minerank": "miner",
    "mineshop": "miner", "minebuy": "miner",
    "craft": "miner", "minedaily": "miner",
    "minehelp": "miner", "miningadmin": "miner",
    # shopkeeper
    "shop": "shopkeeper", "buy": "shopkeeper",
    "vipshop": "shopkeeper", "buyvip": "shopkeeper",
    "badges": "shopkeeper", "titles": "shopkeeper",
    "mybadges": "shopkeeper", "badgeinfo": "shopkeeper",
    "badgemarket": "shopkeeper", "badgelist": "shopkeeper",
    "badgebuy": "shopkeeper", "badgecancel": "shopkeeper",
    "mybadgelistings": "shopkeeper", "badgeprices": "shopkeeper",
    "equip": "shopkeeper", "myitems": "shopkeeper",
    "titleinfo": "shopkeeper", "shophelp": "shopkeeper",
    "shopadmin": "shopkeeper", "vipstatus": "shopkeeper",
    # security
    "report": "security", "reports": "security",
    "bug": "security", "myreports": "security",
    "reporthelp": "security",
    "warn": "security", "warnings": "security",
    "mute": "security", "unmute": "security", "mutes": "security",
    "kick": "security", "ban": "security",
    "tempban": "security", "unban": "security", "bans": "security",
    "modlog": "security", "roomlogs": "security",
    "modhelp": "security", "staffhelp": "security",
    "automod": "security", "setrules": "security",
    # dj
    "emote": "dj", "emotes": "dj",
    "stopemote": "dj", "dance": "dj", "wave": "dj",
    "sit": "dj", "clap": "dj",
    "loopemote": "dj", "stoploop": "dj", "stopallloops": "dj",
    "forceemote": "dj", "forceemoteall": "dj",
    "syncdance": "dj", "synchost": "dj", "stopsync": "dj",
    "hug": "dj", "kiss": "dj", "slap": "dj", "punch": "dj",
    "highfive": "dj", "boop": "dj", "waveat": "dj", "cheer": "dj",
    "heart": "dj", "hearts": "dj", "heartlb": "dj",
    "social": "dj", "blocksocial": "dj", "unblocksocial": "dj",
    # eventhost
    "events": "eventhost", "event": "eventhost",
    "eventhelp": "eventhost", "eventstatus": "eventhost",
    "startevent": "eventhost", "stopevent": "eventhost",
    "eventpoints": "eventhost", "eventshop": "eventhost",
    "buyevent": "eventhost",
    "alert": "eventhost", "staffalert": "eventhost",
    "vipalert": "eventhost", "roomalert": "eventhost",
    "announce_subs": "eventhost", "dmnotify": "eventhost",
}

# ---------------------------------------------------------------------------
# In-memory cache for DB ownership overrides and online status
# ---------------------------------------------------------------------------

_owner_cache: dict[str, str] = {}            # cmd → owner_bot_mode (DB overrides)
_owner_cache_ts: float = 0.0
_OWNER_CACHE_TTL = 60.0                       # refresh every 60s

_online_cache: dict[str, bool] = {}          # bot_mode → is_online
_online_cache_ts: float = 0.0
_ONLINE_CACHE_TTL = 30.0                      # refresh every 30s


def _refresh_owner_cache() -> None:
    global _owner_cache, _owner_cache_ts
    try:
        rows = db.get_all_command_owners()
        _owner_cache = {r["command"]: r["owner_bot_mode"] for r in rows}
        _owner_cache_ts = time.monotonic()
    except Exception:
        pass


def _refresh_online_cache() -> None:
    global _online_cache, _online_cache_ts
    try:
        instances = db.get_bot_instances()
        now = datetime.now(timezone.utc)
        cache: dict[str, bool] = {}
        for inst in instances:
            if not inst.get("enabled", 1):
                cache[inst["bot_mode"]] = False
                continue
            last_seen = inst.get("last_seen_at", "")
            if not last_seen:
                continue
            try:
                ls = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
                if ls.tzinfo is None:
                    from datetime import timezone as _tz
                    ls = ls.replace(tzinfo=_tz.utc)
                age = (now - ls).total_seconds()
                cache[inst["bot_mode"]] = age < 120 and inst.get("status") == "online"
            except Exception:
                pass
        _online_cache = cache
        _online_cache_ts = time.monotonic()
    except Exception:
        pass


def _resolve_command_owner(cmd: str) -> str | None:
    """Return the bot_mode that owns this command, or None if unowned."""
    now = time.monotonic()
    if now - _owner_cache_ts > _OWNER_CACHE_TTL:
        _refresh_owner_cache()
    if cmd in _owner_cache:
        return _owner_cache[cmd]
    return _DEFAULT_COMMAND_OWNERS.get(cmd)


def _is_mode_online(mode: str) -> bool:
    """True if another running bot instance of this mode has been seen recently."""
    now = time.monotonic()
    if now - _online_cache_ts > _ONLINE_CACHE_TTL:
        _refresh_online_cache()
    return _online_cache.get(mode, False)


def _fallback_enabled() -> bool:
    try:
        return db.get_room_setting("multibot_fallback_enabled", "true") == "true"
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Main gate — called in on_chat before any processing
# ---------------------------------------------------------------------------

def should_this_bot_handle(cmd: str) -> bool:
    """
    Returns True if this bot instance should respond to the command.
    When BOT_MODE == "all" (default single-bot mode), always True.
    """
    if BOT_MODE == "all":
        return True

    owner_mode = _resolve_command_owner(cmd)

    if owner_mode is None:
        # Unowned/unknown command — only host or all-mode replies
        return BOT_MODE in ("host", "all")

    if owner_mode == BOT_MODE:
        return True

    # Command belongs to a different bot mode
    if _is_mode_online(owner_mode):
        return False   # That bot is online — let it handle; we ignore

    # Owner bot offline — check fallback
    if _fallback_enabled() and BOT_MODE in ("host", "all"):
        return True

    return False


# ---------------------------------------------------------------------------
# Heartbeat loop — updates bot_instances every 30 s
# ---------------------------------------------------------------------------

_heartbeat_task: asyncio.Task | None = None


async def start_heartbeat_loop(bot) -> None:
    global _heartbeat_task
    if _heartbeat_task and not _heartbeat_task.done():
        return

    async def _loop():
        while True:
            try:
                prefix = db.get_room_setting("bot_prefix_enabled", "true")
                mode_prefix = ""
                try:
                    from modules.bot_modes import get_current_mode_prefix
                    mode_prefix = get_current_mode_prefix()
                except Exception:
                    pass
                db.upsert_bot_instance(
                    bot_id=BOT_ID,
                    bot_username=BOT_USERNAME or BOT_ID,
                    bot_mode=BOT_MODE,
                    prefix=mode_prefix,
                    status="online",
                )
                _refresh_online_cache()
            except Exception as exc:
                print(f"[MULTIBOT] Heartbeat error: {exc}")
            await asyncio.sleep(30)

    _heartbeat_task = asyncio.create_task(_loop())
    print(f"[MULTIBOT] Heartbeat loop started | ID:{BOT_ID} Mode:{BOT_MODE}")


async def mark_bot_offline() -> None:
    try:
        db.upsert_bot_instance(
            bot_id=BOT_ID,
            bot_username=BOT_USERNAME or BOT_ID,
            bot_mode=BOT_MODE,
            prefix="",
            status="offline",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _w(bot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


def _mode_display(mode: str) -> str:
    icons = {
        "host": "🎙️", "banker": "🏦", "dealer": "🎰",
        "miner": "⛏️", "shopkeeper": "🛒", "security": "🛡️",
        "dj": "🎧", "eventhost": "🎉", "all": "🤖",
    }
    return f"{icons.get(mode, '🤖')} {mode.title()}"


# ---------------------------------------------------------------------------
# /bots (overrides the one in bot_modes.py when running multi-bot)
# ---------------------------------------------------------------------------

async def handle_bots_live(bot, user) -> None:
    """/bots — show live bot instance status."""
    instances = db.get_bot_instances()
    if not instances:
        await _w(bot, user.id, "🤖 Main bot handling all modules (BOT_MODE=all).")
        return
    now = datetime.now(timezone.utc)
    parts: list[str] = []
    for inst in instances:
        mode = inst.get("bot_mode", "?")
        last_seen = inst.get("last_seen_at", "")
        enabled   = inst.get("enabled", 1)
        if not enabled:
            parts.append(f"{mode}:DISABLED")
            continue
        if last_seen:
            try:
                ls = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
                if ls.tzinfo is None:
                    ls = ls.replace(tzinfo=timezone.utc)
                age = (now - ls).total_seconds()
                state = "ON" if age < 120 and inst.get("status") == "online" else "OFF"
            except Exception:
                state = "?"
        else:
            state = "?"
        parts.append(f"{mode}:{state}")
    await _w(bot, user.id, ("🤖 Bots: " + " | ".join(parts))[:249])


# ---------------------------------------------------------------------------
# /botmodules
# ---------------------------------------------------------------------------

async def handle_botmodules(bot, user) -> None:
    """/botmodules — show which module each bot_mode owns."""
    if BOT_MODE == "all":
        await _w(bot, user.id, "🤖 Single bot mode — main handles all modules.")
        return
    module_map = {
        "host": "help/profile/room",
        "banker": "economy/bank",
        "dealer": "casino/BJ/RBJ/poker",
        "miner": "mining",
        "shopkeeper": "shop/badges/VIP",
        "security": "moderation/reports",
        "dj": "emotes/social",
        "eventhost": "events/alerts",
    }
    parts = [f"{m}={v}" for m, v in module_map.items()]
    await _w(bot, user.id, ("Modules: " + " | ".join(parts))[:249])


# ---------------------------------------------------------------------------
# /commandowners
# ---------------------------------------------------------------------------

async def handle_commandowners(bot, user) -> None:
    """/commandowners — show DB-overridden command owners (admin+)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    rows = db.get_all_command_owners()
    if not rows:
        await _w(bot, user.id, "No command ownership overrides set. Defaults in use.")
        return
    parts = [f"/{r['command']}→{r['owner_bot_mode']}" for r in rows[:15]]
    await _w(bot, user.id, ("Owners: " + " | ".join(parts))[:249])


# ---------------------------------------------------------------------------
# /enablebot  /disablebot  /setbotmodule  /setcommandowner  /botfallback
# ---------------------------------------------------------------------------

async def handle_enablebot(bot, user, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /enablebot <bot_id>")
        return
    bid = args[1].lower()
    db.enable_bot_instance(bid, True)
    await _w(bot, user.id, f"✅ Bot '{bid}' enabled.")


async def handle_disablebot(bot, user, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /disablebot <bot_id>")
        return
    bid = args[1].lower()
    db.enable_bot_instance(bid, False)
    await _w(bot, user.id, f"⛔ Bot '{bid}' disabled.")


async def handle_setbotmodule(bot, user, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /setbotmodule <bot_id> <mode>")
        return
    bid, mode = args[1].lower(), args[2].lower()
    db.set_bot_instance_module(bid, mode)
    await _w(bot, user.id, f"✅ Bot '{bid}' module set to '{mode}'.")


async def handle_setcommandowner(bot, user, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /setcommandowner <cmd> <bot_mode>")
        return
    cmd_name  = args[1].lstrip("/").lower()
    bot_mode  = args[2].lower()
    db.set_command_owner_db(cmd_name, "", bot_mode, fallback_allowed=1)
    _refresh_owner_cache()
    await _w(bot, user.id, f"✅ /{cmd_name} → owner: {bot_mode}.")


async def handle_botfallback(bot, user, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        await _w(bot, user.id, "Usage: /botfallback on|off")
        return
    new = "true" if args[1].lower() == "on" else "false"
    db.set_room_setting("multibot_fallback_enabled", new)
    label = "ON" if new == "true" else "OFF"
    await _w(bot, user.id, f"✅ Bot command fallback {label}.")


# ---------------------------------------------------------------------------
# /botstartupannounce
# ---------------------------------------------------------------------------

async def handle_botstartupannounce(bot, user, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        cur = db.get_room_setting("bot_startup_announce_enabled", "false")
        await _w(bot, user.id, f"Startup announce: {'ON' if cur == 'true' else 'OFF'}. Usage: /botstartupannounce on|off")
        return
    new = "true" if args[1].lower() == "on" else "false"
    db.set_room_setting("bot_startup_announce_enabled", new)
    label = "ON" if new == "true" else "OFF"
    await _w(bot, user.id, f"✅ Bot startup announce {label}.")


def should_announce_startup() -> bool:
    try:
        return db.get_room_setting("bot_startup_announce_enabled", "false") == "true"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# /multibothelp
# ---------------------------------------------------------------------------

_MULTIBOT_HELP_PAGES = [
    (
        "🤖 Multi-Bot\n"
        "/bots - bot status\n"
        "/botmodules - module owners\n"
        "/botstatus - bot health\n"
        "/commandowners - cmd owners"
    ),
    (
        "👑 Owner Controls\n"
        "/setcommandowner cmd mode\n"
        "/enablebot id\n"
        "/disablebot id\n"
        "/setbotmodule id mode\n"
        "/botfallback on|off"
    ),
]


async def handle_multibothelp(bot, user, args: list[str]) -> None:
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
    n = len(_MULTIBOT_HELP_PAGES)
    if page == 0:
        await _w(bot, user.id, _MULTIBOT_HELP_PAGES[0])
        if can_manage_economy(user.username):
            await _w(bot, user.id, _MULTIBOT_HELP_PAGES[1])
    elif 1 <= page <= n:
        if page == 2 and not can_manage_economy(user.username):
            await _w(bot, user.id, "Admin and owner only.")
        else:
            await _w(bot, user.id, _MULTIBOT_HELP_PAGES[page - 1])
    else:
        await _w(bot, user.id, f"Pages 1-{n}.")


# ---------------------------------------------------------------------------
# get_command_owner_for_audit  (used by cmd_audit.py)
# ---------------------------------------------------------------------------

def get_command_owner_for_audit(cmd: str) -> str:
    """Return the owner bot_mode for a command (for /commandtest output)."""
    owner = _resolve_command_owner(cmd)
    return owner if owner else "all"


# ---------------------------------------------------------------------------
# Export BOT_ID / BOT_MODE for use by main.py and other modules
# ---------------------------------------------------------------------------

__all__ = [
    "BOT_ID", "BOT_MODE", "BOT_USERNAME",
    "should_this_bot_handle",
    "start_heartbeat_loop", "mark_bot_offline",
    "should_announce_startup",
    "handle_bots_live", "handle_botmodules", "handle_commandowners",
    "handle_enablebot", "handle_disablebot",
    "handle_setbotmodule", "handle_setcommandowner", "handle_botfallback",
    "handle_botstartupannounce", "handle_multibothelp",
    "get_command_owner_for_audit",
]
