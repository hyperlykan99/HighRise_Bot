"""
modules/multi_bot.py
Multi-bot system — command ownership gating, heartbeat, staff controls.

BOT_MODE=all (default) → always handles everything (backwards-compatible).
BOT_MODE=blackjack    → BJ + RBJ commands only.
BOT_MODE=poker        → Poker commands only.
BOT_MODE=dealer       → Legacy casino fallback if dedicated bots offline.
BOT_MODE=host         → Help, profiles, room utilities, unknown-cmd fallback.
All other modes handle their own module commands.
"""

from __future__ import annotations
import asyncio
import time
from datetime import datetime, timezone

import database as db
from config import BOT_ID, BOT_MODE, BOT_USERNAME
from modules.permissions import can_manage_economy

# ---------------------------------------------------------------------------
# Default command → bot_mode ownership map
# bot_command_ownership DB table overrides any entry at runtime.
# ---------------------------------------------------------------------------

_DEFAULT_COMMAND_OWNERS: dict[str, str] = {
    # ── host ────────────────────────────────────────────────────────────────
    "help": "host", "mycommands": "host", "helpsearch": "host",
    "tutorial": "host", "guide": "host", "newbiehelp": "host",
    "profile": "host", "me": "host", "whois": "host", "pinfo": "host",
    "privacy": "host", "rules": "host", "roleshelp": "host",
    "players": "host", "roomlist": "host", "online": "host",
    "roomhelp": "host", "teleporthelp": "host", "emotehelp": "host",
    "alerthelp": "host", "welcomehelp": "host", "socialhelp": "host",
    "control": "host", "status": "host", "roomstatus": "host",
    "botmodehelp": "host", "multibothelp": "host",
    # General casino info pages — host owns so only one bot replies
    "casino": "host", "casinohelp": "host",
    "casinosettings": "host", "casinolimits": "host",
    "casinotoggles": "host", "mycasino": "host",
    # ── banker ──────────────────────────────────────────────────────────────
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
    # ── blackjack (Casual BJ + RBJ) ─────────────────────────────────────────
    "bj": "blackjack", "bjoin": "blackjack",
    "bh": "blackjack", "bs": "blackjack", "bd": "blackjack", "bsp": "blackjack",
    "bjh": "blackjack", "bjs": "blackjack", "bjd": "blackjack", "bjsp": "blackjack",
    "bt": "blackjack", "bhand": "blackjack", "bjhand": "blackjack",
    "blimits": "blackjack", "bstats": "blackjack", "bjhelp": "blackjack",
    "setbjlimits": "blackjack", "resetbjlimits": "blackjack",
    "setbjactiontimer": "blackjack", "setbjmaxsplits": "blackjack",
    "setbj": "blackjack",
    "rbj": "blackjack", "rjoin": "blackjack",
    "rh": "blackjack", "rs": "blackjack", "rd": "blackjack", "rsp": "blackjack",
    "rbjh": "blackjack", "rbjs": "blackjack", "rbjd": "blackjack", "rbjsp": "blackjack",
    "rt": "blackjack", "rhand": "blackjack", "rbjhand": "blackjack",
    "rshoe": "blackjack", "rlimits": "blackjack", "rstats": "blackjack",
    "rbjhelp": "blackjack",
    "setrbjlimits": "blackjack", "resetrbjlimits": "blackjack",
    "setrbjactiontimer": "blackjack", "setrbjmaxsplits": "blackjack",
    "setrbj": "blackjack",
    # ── poker ───────────────────────────────────────────────────────────────
    "poker": "poker", "p": "poker",
    "pj": "poker", "pt": "poker", "ptable": "poker",
    "ph": "poker", "pcards": "poker", "po": "poker", "podds": "poker",
    "check": "poker", "ch": "poker",
    "call": "poker", "ca": "poker",
    "raise": "poker", "r": "poker",
    "fold": "poker", "f": "poker",
    "allin": "poker", "ai": "poker", "shove": "poker",
    "pp": "poker", "pplayers": "poker", "pstats": "poker",
    "plb": "poker", "pleaderboard": "poker", "pokerlb": "poker",
    "pokerhelp": "poker", "pokerstats": "poker",
    "sitout": "poker", "sitin": "poker", "rebuy": "poker",
    "pstacks": "poker", "mystack": "poker", "stack": "poker",
    "pokerhistory": "poker", "pokerdebug": "poker",
    "pokerfix": "poker", "pokercleanup": "poker",
    "confirmclosepoker": "poker",
    "setpokertimer": "poker", "setpokerturntimer": "poker",
    "setpokerlobbytimer": "poker", "setpokernexthandtimer": "poker",
    "setpokerblinds": "poker", "setpokerante": "poker",
    "setpokerraise": "poker", "setpokerminplayers": "poker",
    "setpokermaxplayers": "poker",
    # ── miner ───────────────────────────────────────────────────────────────
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
    "useluckboost": "miner", "usexpboost": "miner", "useenergy": "miner",
    "mining": "miner",
    "miningevent": "miner", "miningevents": "miner",
    "startminingevent": "miner", "stopminingevent": "miner",
    "setminecooldown": "miner", "setmineenergycost": "miner", "setminingenergy": "miner",
    "addore": "miner", "removeore": "miner",
    "settoollevel": "miner", "setminelevel": "miner",
    "addminexp": "miner", "setminexp": "miner",
    "resetmining": "miner", "miningroomrequired": "miner",
    "orelist": "miner",
    "orebook": "miner", "oremastery": "miner", "claimoremastery": "miner", "orestats": "miner",
    "contracts": "miner", "miningjobs": "miner",
    "job": "miner", "deliver": "miner", "claimjob": "miner", "rerolljob": "miner",
    # ── shopkeeper ──────────────────────────────────────────────────────────
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
    # ── security ────────────────────────────────────────────────────────────
    "report": "security", "reports": "security",
    "bug": "security", "myreports": "security", "reporthelp": "security",
    "warn": "security", "warnings": "security",
    "mute": "security", "unmute": "security", "mutes": "security",
    "kick": "security", "ban": "security",
    "tempban": "security", "unban": "security", "bans": "security",
    "modlog": "security", "roomlogs": "security",
    "modhelp": "security", "staffhelp": "security",
    "automod": "security", "setrules": "security",
    # ── dj ──────────────────────────────────────────────────────────────────
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
    # ── eventhost ───────────────────────────────────────────────────────────
    "events": "eventhost", "event": "eventhost",
    "eventhelp": "eventhost", "eventstatus": "eventhost",
    "startevent": "eventhost", "stopevent": "eventhost",
    "eventpoints": "eventhost", "eventshop": "eventhost",
    "buyevent": "eventhost",
    "alert": "eventhost", "staffalert": "eventhost",
    "vipalert": "eventhost", "roomalert": "eventhost",
    "announce_subs": "eventhost", "dmnotify": "eventhost",
    "announce": "eventhost", "announce_vip": "eventhost",
    "announce_staff": "eventhost",
    "eventstart": "eventhost", "eventstop": "eventhost",
    "addeventcoins": "eventhost", "removeeventcoins": "eventhost",
    "seteventcoins": "eventhost", "editeventcoins": "eventhost",
    "reseteventcoins": "eventhost",
    "autogamesowner": "eventhost",
    "stopautogames": "eventhost", "killautogames": "eventhost",
    "goldtip": "eventhost", "goldrain": "eventhost",
    "goldrainall": "eventhost", "goldrefund": "eventhost",
    "goldraineligible": "eventhost", "goldrainrole": "eventhost",
    "goldrainvip": "eventhost", "goldraintitle": "eventhost",
    "goldrainbadge": "eventhost", "goldrainlist": "eventhost",
    "goldhelp": "eventhost", "goldwallet": "eventhost",
    "goldtips": "eventhost", "goldtx": "eventhost",
    "pendinggold": "eventhost", "confirmgoldtip": "eventhost",
    "setgoldrainstaff": "eventhost", "setgoldrainmax": "eventhost",
    # ── security (additions) ─────────────────────────────────────────────────
    "clearwarnings": "security",
    "reportinfo": "security", "closereport": "security",
    "reportwatch": "security",
    "audit": "security", "audithelp": "security",
    "auditbank": "security", "auditcasino": "security",
    "auditeconomy": "security",
    # ── banker (additions) ───────────────────────────────────────────────────
    "tip": "banker", "gift": "banker",
    "addcoins": "banker", "removecoins": "banker",
    "bankstats": "banker", "banknotify": "banker",
    "bankhelp": "banker", "coinhelp": "banker",
    # ── host (audit / status commands) ───────────────────────────────────────
    "checkcommands": "host", "checkhelp": "host",
    "missingcommands": "host", "routecheck": "host",
    "silentcheck": "host", "commandtest": "host",
    "fixcommands": "host", "testcommands": "host",
    "deploymentcheck": "host", "bothealth": "host",
    "botconflicts": "host", "botmodules": "host",
    "startupannounce": "host", "modulestartup": "host",
    "startupstatus": "host", "setmainmode": "host",
}

# Friendly display names for modes
_MODE_NAMES: dict[str, str] = {
    "host": "Host", "banker": "Banker", "blackjack": "Blackjack",
    "poker": "Poker", "dealer": "Dealer", "miner": "Miner",
    "shopkeeper": "Shop", "security": "Security",
    "dj": "DJ", "eventhost": "Events", "all": "Main",
}

# ---------------------------------------------------------------------------
# In-memory cache for DB ownership overrides and online status
# ---------------------------------------------------------------------------

_owner_cache: dict[str, str] = {}
_owner_cache_ts: float = 0.0
_OWNER_CACHE_TTL = 60.0

_online_cache: dict[str, bool] = {}
_online_cache_ts: float = 0.0
_ONLINE_CACHE_TTL = 30.0

# Runtime effective-mode cache (allows /setmainmode to change behaviour without restart)
_effective_mode_cache: str | None = None
_effective_mode_ts: float = 0.0
_EFFECTIVE_MODE_TTL = 30.0


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
                    ls = ls.replace(tzinfo=timezone.utc)
                age = (now - ls).total_seconds()
                cache[inst["bot_mode"]] = (age < 120 and inst.get("status") == "online")
            except Exception:
                pass
        _online_cache = cache
        _online_cache_ts = time.monotonic()
    except Exception:
        pass


def _resolve_command_owner(cmd: str) -> str | None:
    now = time.monotonic()
    if now - _owner_cache_ts > _OWNER_CACHE_TTL:
        _refresh_owner_cache()
    if cmd in _owner_cache:
        return _owner_cache[cmd]
    return _DEFAULT_COMMAND_OWNERS.get(cmd)


def _is_mode_online(mode: str) -> bool:
    now = time.monotonic()
    if now - _online_cache_ts > _ONLINE_CACHE_TTL:
        _refresh_online_cache()
    return _online_cache.get(mode, False)


def _fallback_enabled() -> bool:
    try:
        return db.get_room_setting("multibot_fallback_enabled", "true") == "true"
    except Exception:
        return True


def _effective_bot_mode() -> str:
    """
    Returns this bot's effective operating mode.
    Normally BOT_MODE from config.  When BOT_ID == 'main', a /setmainmode
    command can store an override in room_settings that is respected here
    (cached 30 s to avoid per-message DB hits).
    """
    global _effective_mode_cache, _effective_mode_ts
    now = time.monotonic()
    if _effective_mode_cache is None or now - _effective_mode_ts > _EFFECTIVE_MODE_TTL:
        if BOT_ID == "main":
            try:
                override = db.get_room_setting("main_bot_mode_override", "")
            except Exception:
                override = ""
            _effective_mode_cache = override if override else BOT_MODE
        else:
            _effective_mode_cache = BOT_MODE
        _effective_mode_ts = now
    return _effective_mode_cache


def is_bot_mode_active(mode: str) -> bool:
    """Public helper — True if a bot with this mode heartbeated within 90 s."""
    return _is_mode_online(mode)


# ---------------------------------------------------------------------------
# Main gate — called in on_chat before any processing
# ---------------------------------------------------------------------------

def should_this_bot_handle(cmd: str) -> bool:
    """
    Returns True if this bot instance should respond to cmd.

    Key rules:
    - BOT_MODE=all: handle everything UNLESS a dedicated live bot owns the cmd.
    - BOT_MODE=host: handle help/room/unknown only; never game commands.
    - Dedicated modes (blackjack, poker, …): own their command set only.
    - Fallback: host/all may handle offline-owner commands if fallback ON.
    """
    mode = _effective_bot_mode()

    # ── all mode: defer to any dedicated online bot ──────────────────────────
    if mode == "all":
        owner_mode = _resolve_command_owner(cmd)
        if owner_mode is None:
            # Unknown / unowned command — defer to dedicated host bot if online
            return not _is_mode_online("host")
        if owner_mode not in ("all",) and _is_mode_online(owner_mode):
            return False    # dedicated bot is live — stay silent
        return True

    owner_mode = _resolve_command_owner(cmd)

    # Unowned / unknown command — only host or all handles it
    if owner_mode is None:
        return mode in ("host", "all")

    # This bot owns the command
    if owner_mode == mode:
        return True

    # Legacy dealer mode: handles BJ/RBJ/Poker if dedicated bots are offline
    if mode == "dealer" and owner_mode in ("blackjack", "poker"):
        return not _is_mode_online(owner_mode)

    # Owner mode is online — defer silently
    if _is_mode_online(owner_mode):
        return False

    # Owner mode offline — host/all may fall back
    if _fallback_enabled() and mode in ("host", "all"):
        return True

    return False


def get_offline_message(cmd: str) -> str | None:
    """
    Returns a user-facing message when the owning bot is offline and fallback is OFF.
    Only host/all mode should call this (others silently ignore).
    """
    mode = _effective_bot_mode()
    if mode not in ("host", "all"):
        return None
    owner_mode = _resolve_command_owner(cmd)
    if owner_mode is None:
        return None
    if owner_mode in ("host", "all"):
        return None
    if _is_mode_online(owner_mode):
        return None
    if _fallback_enabled():
        return None
    name = _MODE_NAMES.get(owner_mode, owner_mode.title())
    return f"{name} bot is currently offline."


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
            last_error = ""
            db_connected = 1
            try:
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
                    db_connected=1,
                    last_error="",
                )
                _refresh_online_cache()
            except Exception as exc:
                last_error = str(exc)[:80]
                print(f"[MULTIBOT] Heartbeat error: {exc}")
                try:
                    db.upsert_bot_instance(
                        bot_id=BOT_ID,
                        bot_username=BOT_USERNAME or BOT_ID,
                        bot_mode=BOT_MODE,
                        prefix="",
                        status="online",
                        db_connected=0,
                        last_error=last_error,
                    )
                except Exception:
                    pass
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


def _mode_icon(mode: str) -> str:
    icons = {
        "host": "🎙️", "banker": "🏦", "blackjack": "🃏",
        "poker": "♠️", "dealer": "🎰", "miner": "⛏️",
        "shopkeeper": "🛒", "security": "🛡️",
        "dj": "🎧", "eventhost": "🎉", "all": "🤖",
    }
    return icons.get(mode, "🤖")


# ---------------------------------------------------------------------------
# /bots — live cluster status
# ---------------------------------------------------------------------------

async def handle_bots_live(bot, user) -> None:
    instances = db.get_bot_instances()
    if not instances:
        await _w(bot, user.id, "🤖 Main bot (BOT_MODE=all) handling all modules.")
        return
    now = datetime.now(timezone.utc)
    parts: list[str] = []
    for inst in instances:
        mode = inst.get("bot_mode", "?")
        enabled = inst.get("enabled", 1)
        if not enabled:
            parts.append(f"{_MODE_NAMES.get(mode, mode)} DISABLED")
            continue
        last_seen = inst.get("last_seen_at", "")
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
        parts.append(f"{_MODE_NAMES.get(mode, mode)} {state}")
    await _w(bot, user.id, ("🤖 Bots: " + " | ".join(parts))[:249])


# ---------------------------------------------------------------------------
# /botstatus [bot_id]
# ---------------------------------------------------------------------------

async def handle_botstatus_cluster(bot, user, args: list[str]) -> None:
    if len(args) >= 2:
        target = args[1].lower()
        instances = db.get_bot_instances()
        found = next((i for i in instances
                      if i.get("bot_id", "").lower() == target
                      or i.get("bot_mode", "").lower() == target), None)
        if not found:
            await _w(bot, user.id, f"No bot found with ID or mode '{target}'.")
            return
        mode = found.get("bot_mode", "?")
        icon = _mode_icon(mode)
        enabled = "ON" if found.get("enabled", 1) else "DISABLED"
        last_seen = found.get("last_seen_at", "")
        age_str = "never"
        if last_seen:
            try:
                ls = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
                if ls.tzinfo is None:
                    ls = ls.replace(tzinfo=timezone.utc)
                age = int((datetime.now(timezone.utc) - ls).total_seconds())
                age_str = f"{age}s ago"
            except Exception:
                age_str = "?"
        name = _MODE_NAMES.get(mode, mode.title())
        await _w(bot, user.id,
                 f"{icon} {name} Bot: {enabled} | Mode {mode} | Last seen {age_str}"[:249])
    else:
        await handle_bots_live(bot, user)


# ---------------------------------------------------------------------------
# /botmodules
# ---------------------------------------------------------------------------

async def handle_botmodules(bot, user) -> None:
    mode = _effective_bot_mode()
    if mode == "all":
        await _w(bot, user.id, "🤖 Single bot mode — main handles all modules.")
        return
    _refresh_online_cache()
    module_rows = [
        ("BJ/RBJ", "blackjack"), ("Poker", "poker"),
        ("Bank",   "banker"),    ("Mining", "miner"),
        ("Shop",   "shopkeeper"), ("Mod",  "security"),
        ("Emotes", "dj"),        ("Events", "eventhost"),
        ("Help",   "host"),
    ]
    parts = []
    for label, m in module_rows:
        name = _MODE_NAMES.get(m, m.title())
        flag = "✅" if _is_mode_online(m) else "·"
        parts.append(f"{label}={name}{flag}")
    await _w(bot, user.id, ("Modules: " + " | ".join(parts))[:249])


# ---------------------------------------------------------------------------
# /commandowners
# ---------------------------------------------------------------------------

async def handle_commandowners(bot, user) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    rows = db.get_all_command_owners()
    if not rows:
        await _w(bot, user.id,
                 "No DB overrides. Defaults: /bj→Blackjack | /p→Poker | /bal→Banker")
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
    _refresh_online_cache()
    await _w(bot, user.id, f"✅ Bot '{bid}' disabled.")


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
    cmd_name = args[1].lstrip("/").lower()
    bot_mode = args[2].lower()
    db.set_command_owner_db(cmd_name, "", bot_mode, fallback_allowed=1)
    _refresh_owner_cache()
    name = _MODE_NAMES.get(bot_mode, bot_mode.title())
    await _w(bot, user.id, f"✅ /{cmd_name} owner set to {name}.")


async def handle_setmainmode(bot, user, args: list[str]) -> None:
    """
    /setmainmode host|all
    Changes the effective operating mode of the main/all bot at runtime.
    Stored in room_settings so it survives the cache but NOT a full restart
    (bot.py re-applies env-var defaults on startup).
    Admin+ only.
    """
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2 or args[1].lower() not in ("host", "all"):
        await _w(bot, user.id, "Usage: /setmainmode host | all")
        return
    global _effective_mode_cache, _effective_mode_ts
    new_mode = args[1].lower()
    try:
        db.set_room_setting("main_bot_mode_override", new_mode)
    except Exception as e:
        await _w(bot, user.id, f"DB error: {str(e)[:40]}")
        return
    # Immediately apply in-process
    _effective_mode_cache = new_mode
    _effective_mode_ts = time.monotonic()
    await _w(bot, user.id, f"✅ Main bot mode set to {new_mode}.")


async def handle_botfallback(bot, user, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        cur = db.get_room_setting("multibot_fallback_enabled", "true")
        await _w(bot, user.id,
                 f"Fallback: {'ON' if cur == 'true' else 'OFF'}. Usage: /botfallback on|off")
        return
    new = "true" if args[1].lower() == "on" else "false"
    db.set_room_setting("multibot_fallback_enabled", new)
    label = "ON ✅" if new == "true" else "OFF ⛔"
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
        await _w(bot, user.id,
                 f"Startup announce: {'ON' if cur == 'true' else 'OFF'}."
                 " Usage: /botstartupannounce on|off")
        return
    new = "true" if args[1].lower() == "on" else "false"
    db.set_room_setting("bot_startup_announce_enabled", new)
    label = "ON" if new == "true" else "OFF"
    await _w(bot, user.id, f"✅ Bot startup announce {label}.")


def should_announce_startup() -> bool:
    """Legacy helper kept for backward compat."""
    try:
        return db.get_room_setting("bot_startup_announce_enabled", "false") == "true"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Mode-specific startup messages
# ---------------------------------------------------------------------------

_MODULE_STARTUP_MSGS: dict[str, str] = {
    "blackjack":  "🃏 Blackjack Bot online.",
    "poker":      "♠️ Poker Bot online.",
    "miner":      "⛏️ Miner Bot online.",
    "banker":     "🏦 Banker Bot online.",
    "shopkeeper": "🛒 Shop Bot online.",
    "security":   "🛡️ Security Bot online.",
    "dj":         "🎧 DJ Bot online.",
    "eventhost":  "🎉 Event Bot online.",
}

_STARTUP_COOLDOWN_SECONDS = 600  # 10 minutes


async def send_startup_announce(bot) -> None:
    """
    Centralised startup-announce logic.  Called once per bot startup.

    Rules:
    - Console log always printed.
    - host/all mode: sends room message only if bot_startup_announce_enabled=true
      AND the 10-minute cooldown has not elapsed.
    - Module bots: sends room message only if module_startup_announce_enabled=true
      AND the per-bot 10-minute cooldown has not elapsed.
    - Old generic "Mini Game Bot is online!" is NEVER sent.
    """
    mode = _effective_bot_mode()
    uname = BOT_USERNAME or BOT_ID
    print(f"[BOT] {uname} online | id={BOT_ID} | mode={mode}")

    now = datetime.now(timezone.utc)

    def _cooldown_ok(key: str) -> bool:
        try:
            last = db.get_room_setting(key, "")
            if not last:
                return True
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            return (now - last_dt).total_seconds() >= _STARTUP_COOLDOWN_SECONDS
        except Exception:
            return True

    is_host = mode in ("host", "all")

    if is_host:
        try:
            host_enabled = db.get_room_setting("bot_startup_announce_enabled", "false") == "true"
        except Exception:
            host_enabled = False
        if host_enabled and _cooldown_ok("last_host_startup_announce_at"):
            try:
                await bot.highrise.chat("🎙️ Host online. Type /help or /tutorial.")
                db.set_room_setting("last_host_startup_announce_at", now.isoformat())
            except Exception as exc:
                print(f"[BOT] Startup announce error: {exc}")
    else:
        try:
            mod_enabled = db.get_room_setting("module_startup_announce_enabled", "false") == "true"
        except Exception:
            mod_enabled = False
        if mod_enabled:
            msg = _MODULE_STARTUP_MSGS.get(mode)
            cooldown_key = f"last_module_startup_announce_at_{BOT_ID}"
            if msg and _cooldown_ok(cooldown_key):
                try:
                    await bot.highrise.chat(msg)
                    db.set_room_setting(cooldown_key, now.isoformat())
                except Exception as exc:
                    print(f"[BOT] Module startup announce error: {exc}")


# ---------------------------------------------------------------------------
# /startupannounce  /modulestartup  /startupstatus
# ---------------------------------------------------------------------------

async def handle_startupannounce(bot, user, args: list[str]) -> None:
    """
    /startupannounce on|off
    Enables/disables the Host Bot room message on startup.
    """
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        cur = db.get_room_setting("bot_startup_announce_enabled", "false")
        label = "ON" if cur == "true" else "OFF"
        await _w(bot, user.id, f"Host startup announce: {label}. Usage: /startupannounce on|off")
        return
    new = "true" if args[1].lower() == "on" else "false"
    db.set_room_setting("bot_startup_announce_enabled", new)
    if new == "true":
        await _w(bot, user.id, "✅ Host startup announce ON.")
    else:
        await _w(bot, user.id, "⛔ Host startup announce OFF.")


async def handle_modulestartup(bot, user, args: list[str]) -> None:
    """
    /modulestartup on|off
    Enables/disables short role-specific startup messages for non-host bots.
    """
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        cur = db.get_room_setting("module_startup_announce_enabled", "false")
        label = "ON" if cur == "true" else "OFF"
        await _w(bot, user.id, f"Module startup announce: {label}. Usage: /modulestartup on|off")
        return
    new = "true" if args[1].lower() == "on" else "false"
    db.set_room_setting("module_startup_announce_enabled", new)
    if new == "true":
        await _w(bot, user.id, "✅ Module startup announce ON.")
    else:
        await _w(bot, user.id, "⛔ Module startup announce OFF.")


async def handle_startupstatus(bot, user) -> None:
    """
    /startupstatus
    Shows current startup announce settings for Host and modules.
    """
    try:
        host_val = db.get_room_setting("bot_startup_announce_enabled", "false")
        mod_val = db.get_room_setting("module_startup_announce_enabled", "false")
    except Exception:
        await _w(bot, user.id, "DB error reading startup settings.")
        return
    host_lbl = "ON" if host_val == "true" else "OFF"
    mod_lbl = "ON" if mod_val == "true" else "OFF"
    await _w(bot, user.id, f"Startup: Host {host_lbl} | Modules {mod_lbl}")


# Keep old handler as an alias so any existing /botstartupannounce calls still work
async def handle_botstartupannounce(bot, user, args: list[str]) -> None:
    await handle_startupannounce(bot, user, args)


# ---------------------------------------------------------------------------
# /multibothelp
# ---------------------------------------------------------------------------

_MULTIBOT_HELP_PAGES = [
    (
        "🤖 Multi-Bot\n"
        "/bots - bot list\n"
        "/bothealth - health\n"
        "/modulehealth - modules\n"
        "/deploymentcheck - check setup\n"
        "/botstatus id - bot details"
    ),
    (
        "🔍 Diagnostics\n"
        "/botlocks - active locks\n"
        "/botconflicts - find conflicts\n"
        "/botheartbeat - this bot status\n"
        "/moduleowners - cmd owners\n"
        "/commandowners - DB overrides"
    ),
    (
        "👑 Owner Controls\n"
        "/setcommandowner cmd mode\n"
        "/enablebot id | /disablebot id\n"
        "/setbotmodule id mode\n"
        "/botfallback on|off\n"
        "/fixbotowners [force]\n"
        "/clearstalebotlocks"
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

def check_startup_safety() -> list[str]:
    """
    Run safety checks at bot startup. Returns a list of warning strings.
    Prints each warning to console; caller may also announce in-room.
    """
    warnings: list[str] = []
    try:
        instances = db.get_bot_instances()
        now_ts = __import__("time").time()

        # 1. Duplicate BOT_ID active recently
        for inst in instances:
            if inst.get("bot_id") != BOT_ID:
                continue
            last_seen = inst.get("last_seen_at", "")
            if not last_seen:
                continue
            from datetime import datetime as _dt, timezone as _tz
            try:
                ls = _dt.fromisoformat(last_seen.replace("Z", "+00:00"))
                if ls.tzinfo is None:
                    ls = ls.replace(tzinfo=_tz.utc)
                age = (_dt.now(_tz.utc) - ls).total_seconds()
                if age < 60 and inst.get("status") == "online":
                    w = f"[MULTIBOT] WARN: Another bot with ID '{BOT_ID}' was active {int(age)}s ago."
                    print(w)
                    warnings.append(w)
            except Exception:
                pass

        # 2. BOT_MODE=all with dedicated split bots active
        if BOT_MODE == "all":
            split_modes = ("blackjack", "poker", "miner", "banker",
                           "shopkeeper", "security", "dj", "eventhost")
            active_splits = []
            for inst in instances:
                m = inst.get("bot_mode", "")
                if m not in split_modes:
                    continue
                if not inst.get("enabled", 1):
                    continue
                last_seen = inst.get("last_seen_at", "")
                if not last_seen:
                    continue
                from datetime import datetime as _dt2, timezone as _tz2
                try:
                    ls = _dt2.fromisoformat(last_seen.replace("Z", "+00:00"))
                    if ls.tzinfo is None:
                        ls = ls.replace(tzinfo=_tz2.utc)
                    if (_dt2.now(_tz2.utc) - ls).total_seconds() < 90:
                        active_splits.append(m)
                except Exception:
                    pass
            if active_splits:
                w = (f"[MULTIBOT] WARN: BOT_MODE=all active alongside split bots: "
                     f"{', '.join(active_splits)}. Duplicate replies possible.")
                print(w)
                warnings.append(w)
    except Exception as e:
        print(f"[MULTIBOT] Startup safety check error: {e}")
    return warnings


def get_command_owner_for_audit(cmd: str) -> str:
    owner = _resolve_command_owner(cmd)
    if not owner:
        return "all"
    return _MODE_NAMES.get(owner, owner.title())


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

__all__ = [
    "BOT_ID", "BOT_MODE", "BOT_USERNAME",
    "should_this_bot_handle", "get_offline_message", "is_bot_mode_active",
    "start_heartbeat_loop", "mark_bot_offline",
    "should_announce_startup", "send_startup_announce",
    "handle_bots_live", "handle_botstatus_cluster",
    "handle_botmodules", "handle_commandowners",
    "handle_enablebot", "handle_disablebot",
    "handle_setbotmodule", "handle_setcommandowner", "handle_botfallback",
    "handle_botstartupannounce",                   # backward-compat alias
    "handle_startupannounce", "handle_modulestartup", "handle_startupstatus",
    "handle_setmainmode", "handle_multibothelp",
    "get_command_owner_for_audit",
]
