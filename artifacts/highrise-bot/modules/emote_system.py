"""
modules/emote_system.py
-----------------------
Full player emote system for ChillTopia / DJ_DUDU:

  Plain-text trigger  — typing "dance" (no !) starts a looping emote for that player
  Plain-text "stop"   — cancels the player's active emote loop
  !emotes             — auto-sends every page with a short delay (no manual pagination)
  !botemote  <b> <e>  — admin: loop an emote on a named bot (DB-persisted, survives restart)
  !stopbotemote <b>   — admin: stop a bot's looping emote
  !punch @user        — attacker gets punch emote, target gets reaction emote; 10 s CD
  !swordfight @user   — both players get swordfight emote simultaneously; 10 s CD
"""
from __future__ import annotations

import asyncio
import re as _re
import time
from typing import TYPE_CHECKING

import database as db
from modules.permissions import is_admin, is_owner, can_moderate
from modules.gold import get_bot_user_id

if TYPE_CHECKING:
    from highrise import BaseBot, User


async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Emote registry — friendly name → SDK emote-ID
# ---------------------------------------------------------------------------
EMOTE_REGISTRY: dict[str, str] = {
    # Basic
    "wave":           "emote-wave",
    "greet":          "emote-greet",
    "hello":          "emote-hello",
    "dance":          "emote-disco",
    "dance2":         "emote-dance2",
    "dance3":         "emote-dance3",
    "dance4":         "emote-dance4",
    "sit":            "emote-idle_sitfloor",
    "sit2":           "emote-sit2",
    "clap":           "emote-clap",
    "point":          "emote-point",
    "laugh":          "emote-laugh",
    "salute":         "emote-salute",
    "blowkiss":       "emote-blowkiss",
    # Reactions
    "yes":            "emote-yes",
    "no":             "emote-no",
    "thumbsup":       "emote-thumbsup",
    "thumbsdown":     "emote-thumbsdown",
    "shrug":          "emote-shrug",
    "sorry":          "emote-sorry",
    "angry":          "emote-angry",
    "sad":            "emote-sad",
    "cry":            "emote-cry",
    "celebrate":      "emote-celebrate",
    "surprise":       "emote-surprise",
    # Social
    "hug":            "emote-hug",
    "kiss":           "emote-kiss",
    "highfive":       "emote-highfive",
    "handshake":      "emote-handshake",
    "bow":            "emote-bow",
    "curtsy":         "emote-curtsy",
    "curtsey":        "emote-curtsey",
    # Fun / party
    "flex":           "emote-flex",
    "pose":           "emote-pose",
    "pose2":          "emote-pose2",
    "snowball":       "emote-snowball",
    "snowangel":      "emote-snowangel",
    "telekinesis":    "emote-telekinesis",
    "float":          "emote-float",
    "levitate":       "emote-levitate",
    "headbang":       "emote-headbang",
    "headbang2":      "emote-headbang2",
    "sleep":          "emote-sleep",
    "sniff":          "emote-sniff",
    # Seasonal / events
    "zombie":         "emote-zombie",
    "witch":          "emote-witch",
    "ghost":          "emote-ghost",
    "fly":            "emote-fly",
    "spin":           "emote-spin",
    "magic":          "emote-magic",
    "magic2":         "emote-magic2",
    # Aliases & friendly variants
    "fistpump":       "emote-flex",
    "fist":           "emote-flex",
    "pump":           "emote-flex",
    "sword":          "emote-telekinesis",
    "fight":          "emote-telekinesis",
    "idle":           "emote-idle_loop",
    "idleloop":       "emote-idle_loop",
    "idlelook":       "emote-idle_look",
    "enthusiastic":   "emote-idle_enthusiastic",
    # Extended aliases — match _ALIAS_MAP in emote_registry
    "gangnam":        "emote-gangnam",
    "gangnamstyle":   "emote-gangnam",
    "groovy":         "emote-dance3",
    "partydance":     "emote-dance4",
    "hi":             "emote-wave",
    "bye":            "emote-wave",
    "pray":           "emote-sorry",
    "beg":            "emote-sorry",
    "smooch":         "emote-kiss",
    "muah":           "emote-blowkiss",
    "lol":            "emote-laugh",
    "haha":           "emote-laugh",
    "omg":            "emote-surprise",
    "wow":            "emote-surprise",
    "yep":            "emote-yes",
    "yup":            "emote-yes",
    "nope":           "emote-no",
    "ok":             "emote-thumbsup",
    "nice":           "emote-thumbsup",
    "good":           "emote-thumbsup",
    "mad":            "emote-angry",
    "upset":          "emote-sad",
    "tears":          "emote-cry",
    "weep":           "emote-cry",
    "woo":            "emote-celebrate",
    "yay":            "emote-celebrate",
    "win":            "emote-celebrate",
    "stand":          "emote-idle_loop",
    "reset":          "emote-idle_loop",
    "spooky":         "emote-ghost",
    "brooms":         "emote-witch",
    "zzz":            "emote-sleep",
    "nap":            "emote-sleep",
    # Command overrides (match _ALIAS_OVERRIDES in emote_registry)
    "swordfight":     "emote-swordfight",
    "punch":          "emote-punch",
    "drop":           "emote-deathdrop",
    "deathdrop":      "emote-deathdrop",
    "fail":           "emote-fail1",
    "disco":          "emote-disco",
    "fistbump":       "emote-fistbump",
    "dj":             "emote-dj",
}


def _normalize(name: str) -> str:
    """Normalize to command key: lowercase, strip all non-alphanumeric characters.

    Examples:
      'Snow Angel'       → 'snowangel'
      "Don't Start Now"  → 'dontstartnow'
      'Fist Pump'        → 'fistpump'
      'emote-dance'      → 'emotedance'
    """
    s = name.lower().replace("\u2019", "")  # curly apostrophe → nothing
    return _re.sub(r"[^a-z0-9]", "", s)


# Normalised lookup table (built once at import time)
_NORM_MAP: dict[str, str] = {_normalize(k): v for k, v in EMOTE_REGISTRY.items()}

# Also accept any "emote-X" id directly (strip "emote" prefix after normalise)
_EMOTE_PREFIX = "emote"


def lookup_emote(name: str) -> str | None:
    """Return SDK emote-ID for a player-typed name, or None if unknown.

    Resolution order:
      1. EMOTE_REGISTRY (includes aliases)     — fast dict lookup
      2. Strip leading "emote" prefix variant  — handles "emote-dance" input
      3. emote_registry alias/candidate map    — community aliases
      4. Unverified construction               — only when allow_unverified is on
    """
    norm = _normalize(name)
    if norm in _NORM_MAP:
        return _NORM_MAP[norm]
    # Strip leading "emote" prefix so "emotedance" or "emote-dance" also works
    if norm.startswith(_EMOTE_PREFIX):
        stripped = norm[len(_EMOTE_PREFIX):]
        if stripped in _NORM_MAP:
            return _NORM_MAP[stripped]
        candidate = f"emote-{stripped}"
        if candidate in set(EMOTE_REGISTRY.values()):
            return candidate
    # Extended fallback: check emote_registry alias / candidate map
    try:
        from modules.emote_registry import resolve_emote_id
        eid = resolve_emote_id(name)
        if eid:
            return eid
    except Exception:
        pass
    return None


# Frozenset of all valid plain-text trigger names (normalised)
PLAYER_EMOTE_NAMES: frozenset[str] = frozenset(_normalize(k) for k in EMOTE_REGISTRY)


def is_plain_emote(text: str) -> bool:
    """True if the exact chat message (no ! prefix) matches a known emote name."""
    return _normalize(text.strip()) in PLAYER_EMOTE_NAMES


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------
_player_loops:  dict[str, asyncio.Task] = {}   # user_id  → active loop Task
_player_emotes: dict[str, str]          = {}   # user_id  → current emote-ID
_bot_loops:     dict[str, asyncio.Task] = {}   # bot_mode → active loop Task

_PLAYER_LOOP_INTERVAL = 7   # seconds between re-sends for player loops
_BOT_LOOP_INTERVAL    = 8

# ---------------------------------------------------------------------------
# Cooldowns
# ---------------------------------------------------------------------------
_emote_cd: dict[str, float] = {}
_punch_cd: dict[str, float] = {}
_sword_cd: dict[str, float] = {}
_EMOTE_CD = 3
_PUNCH_CD = 10
_SWORD_CD = 10


def _cd_remaining(store: dict, uid: str, secs: int) -> float:
    return max(0.0, secs - (time.time() - store.get(uid, 0.0)))


def _cd_set(store: dict, uid: str) -> None:
    store[uid] = time.time()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _is_admin(username: str) -> bool:
    return is_admin(username) or is_owner(username)


def _log(stage: str, **kw: object) -> None:
    parts = " ".join(f"{k}={v!r}" for k, v in kw.items())
    print(f"[EMOTE] stage={stage} {parts}")


async def _send(bot: "BaseBot", eid: str, uid: str) -> bool:
    """Send one emote; return True on success."""
    try:
        await bot.highrise.send_emote(eid, uid)
        return True
    except Exception as exc:
        _log("send_error", emote=eid, user_id=uid, error=str(exc))
        return False


# ---------------------------------------------------------------------------
# Player loop management
# ---------------------------------------------------------------------------
def _cancel_player_loop(uid: str) -> None:
    task = _player_loops.pop(uid, None)
    if task and not task.done():
        task.cancel()
    _player_emotes.pop(uid, None)


async def _run_player_loop(bot: "BaseBot", uid: str, eid: str) -> None:
    while True:
        try:
            await bot.highrise.send_emote(eid, uid)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(_PLAYER_LOOP_INTERVAL)


async def start_player_emote(bot: "BaseBot", user: "User", emote_name: str) -> None:
    """Start (or replace) a looping emote for a player.

    Called from the plain-text dispatch in on_chat.
    Silently skips if on cooldown or emote name is unknown.
    No confirmation whisper — avoids chat spam.
    """
    uid   = user.id
    uname = user.username

    if _cd_remaining(_emote_cd, uid, _EMOTE_CD) > 0:
        return   # silently skip

    eid = lookup_emote(emote_name)
    if not eid:
        return   # unknown name

    _cancel_player_loop(uid)
    _cd_set(_emote_cd, uid)

    ok = await _send(bot, eid, uid)
    if not ok:
        return

    task = asyncio.create_task(_run_player_loop(bot, uid, eid))
    _player_loops[uid]  = task
    _player_emotes[uid] = eid
    _log("emote_start", user_id=uid, username=uname, emote=eid)


async def stop_player_emote(bot: "BaseBot", user: "User") -> None:
    """Cancel the player's active emote loop and reset to idle.

    Called when the player types "stop" (no ! prefix).
    """
    uid      = user.id
    uname    = user.username
    had_loop = uid in _player_loops
    _cancel_player_loop(uid)
    await _send(bot, "emote-idle_loop", uid)
    _log("emote_stop", user_id=uid, username=uname, had_loop=had_loop)
    await _w(bot, uid, "⏹️ Emote stopped.")


def on_player_leave(uid: str) -> None:
    """Clean up a player's emote loop when they leave the room."""
    _cancel_player_loop(uid)


# ---------------------------------------------------------------------------
# !emotes — auto-paginated
# ---------------------------------------------------------------------------
_PAGE_SIZE = 10


async def handle_emotes_auto(bot: "BaseBot", user: "User", _args: list) -> None:
    """Send all active emote names automatically (0.4 s between pages, ≤249 chars each).

    Pulls from the emote_registry cache when available; falls back to the
    static EMOTE_REGISTRY if no discovered emotes are cached yet.
    """
    from modules.emote_registry import handle_emotes_paged
    await handle_emotes_paged(bot, user, _args)


# ---------------------------------------------------------------------------
# Bot emote loops (DB-persisted)
# ---------------------------------------------------------------------------
def _start_bot_loop(bot: "BaseBot", bot_mode: str, eid: str, bot_uid: str) -> None:
    """Start (or restart) a persistent emote loop for this bot process."""
    old = _bot_loops.pop(bot_mode, None)
    if old and not old.done():
        old.cancel()

    async def _loop() -> None:
        while True:
            try:
                await bot.highrise.send_emote(eid, bot_uid)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(_BOT_LOOP_INTERVAL)

    _bot_loops[bot_mode] = asyncio.create_task(_loop())
    _log("bot_loop_start", bot=bot_mode, emote=eid, bot_uid=bot_uid)


async def handle_botemote(bot: "BaseBot", user: "User", args: list) -> None:
    """!botemote <botname> <emote> — admin: loop an emote on a specific bot (DB-persisted)."""
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "👑 Admin only.")
        return
    if len(args) < 3:
        await _w(bot, uid, "Usage: !botemote <botname> <emote>\nEx: !botemote dj dance")
        return

    bot_name   = args[1].lower()
    emote_name = args[2].lower()
    eid = lookup_emote(emote_name)
    if not eid:
        await _w(bot, uid, f"❌ Unknown emote '{emote_name}'. See !emotes for list.")
        return

    db.set_room_setting(f"bot_emote_{bot_name}", eid)
    _log("bot_emote_set", admin=uname, bot=bot_name, emote=eid)

    from config import BOT_MODE
    if BOT_MODE.lower() == bot_name:
        bot_uid = get_bot_user_id()
        if bot_uid:
            _start_bot_loop(bot, BOT_MODE, eid, bot_uid)
            await _w(bot, uid, f"✅ {bot_name} now looping {eid}.")
            return

    await _w(bot, uid,
             f"✅ Saved. {bot_name} will loop {eid} on next reconnect.")


async def handle_stopbotemote(bot: "BaseBot", user: "User", args: list) -> None:
    """!stopbotemote <botname> — admin: stop a bot's looping emote."""
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "👑 Admin only.")
        return
    if len(args) < 2:
        await _w(bot, uid, "Usage: !stopbotemote <botname>")
        return

    bot_name = args[1].lower()
    db.set_room_setting(f"bot_emote_{bot_name}", "")

    from config import BOT_MODE
    if BOT_MODE.lower() == bot_name:
        task = _bot_loops.pop(BOT_MODE, None)
        if task and not task.done():
            task.cancel()

    _log("bot_emote_cleared", admin=uname, bot=bot_name)
    await _w(bot, uid, f"✅ {bot_name} bot emote stopped.")


async def startup_bot_emote_recovery(bot: "BaseBot") -> None:
    """On bot startup, restore a persisted bot emote loop from DB (if any)."""
    from config import BOT_MODE
    eid = db.get_room_setting(f"bot_emote_{BOT_MODE.lower()}", "")
    if not eid:
        return
    await asyncio.sleep(6)   # allow the bot to fully connect first
    bot_uid = get_bot_user_id()
    if not bot_uid:
        _log("emote_recovery_skipped", bot=BOT_MODE, reason="no_bot_uid")
        return
    _start_bot_loop(bot, BOT_MODE, eid, bot_uid)
    _log("emote_recovery", bot=BOT_MODE, emote=eid)


# ---------------------------------------------------------------------------
# !punch @user — SDK emotes for both attacker and target
# ---------------------------------------------------------------------------
_PUNCH_ATTACKER_EMOTE = "emote-flex"      # attacker power gesture
_PUNCH_TARGET_EMOTE   = "emote-surprise"  # target reaction


async def handle_punch_emote(bot: "BaseBot", user: "User", args: list) -> None:
    """!punch @user — attacker does flex/punch emote, target does surprise; 10 s CD."""
    uid   = user.id
    uname = user.username

    remaining = _cd_remaining(_punch_cd, uid, _PUNCH_CD)
    if remaining > 0:
        await _w(bot, uid, f"⏳ Punch cooldown: {remaining:.0f}s")
        return

    if len(args) < 2:
        await _w(bot, uid, "Usage: !punch @username")
        return

    target_name = args[1].lstrip("@")
    if target_name.lower() == uname.lower():
        await _w(bot, uid, "❌ You can't punch yourself!")
        return

    from modules.room_utils import _resolve_user_in_room
    pair = await _resolve_user_in_room(bot, target_name)
    if not pair:
        await _w(bot, uid, f"❌ @{target_name} is not in the room.")
        return
    target_user, _ = pair

    # Cannot punch staff unless the caller is an admin
    if can_moderate(target_user.username) and not _is_admin(uname):
        await _w(bot, uid, "❌ You can't punch staff members.")
        return

    _cd_set(_punch_cd, uid)

    await _send(bot, _PUNCH_ATTACKER_EMOTE, uid)
    await asyncio.sleep(0.3)
    await _send(bot, _PUNCH_TARGET_EMOTE, target_user.id)

    try:
        await bot.highrise.chat(
            f"🥊 {uname} punched {target_user.username}!"[:249]
        )
    except Exception:
        pass
    _log("punch", user_id=uid, username=uname, target=target_user.username)


# ---------------------------------------------------------------------------
# !swordfight @user — both get swordfight emote simultaneously
# ---------------------------------------------------------------------------
_SWORD_EMOTE = "emote-telekinesis"   # closest available combat gesture


async def handle_swordfight(bot: "BaseBot", user: "User", args: list) -> None:
    """!swordfight @user — both players do swordfight emote simultaneously; 10 s CD."""
    uid   = user.id
    uname = user.username

    if len(args) < 2:
        await _w(bot, uid, "❌ Tag someone to swordfight.\nUsage: !swordfight @user")
        return

    target_name = args[1].lstrip("@")
    if target_name.lower() == uname.lower():
        await _w(bot, uid, "❌ You can't swordfight yourself!")
        return

    remaining = _cd_remaining(_sword_cd, uid, _SWORD_CD)
    if remaining > 0:
        await _w(bot, uid, f"⏳ Swordfight cooldown: {remaining:.0f}s")
        return

    from modules.room_utils import _resolve_user_in_room
    pair = await _resolve_user_in_room(bot, target_name)
    if not pair:
        await _w(bot, uid, f"❌ @{target_name} is not in the room.")
        return
    target_user, _ = pair

    _cd_set(_sword_cd, uid)

    await asyncio.gather(
        _send(bot, _SWORD_EMOTE, uid),
        _send(bot, _SWORD_EMOTE, target_user.id),
    )

    try:
        await bot.highrise.chat(
            f"⚔️ {uname} and {target_user.username} are swordfighting!"[:249]
        )
    except Exception:
        pass
    _log("swordfight", user_id=uid, username=uname, target=target_user.username)


# ---------------------------------------------------------------------------
# Staff forced emotes
# ---------------------------------------------------------------------------
_force_emote_cd: dict[str, float] = {}
_room_emote_cd:  dict[str, float] = {}
_FORCE_EMOTE_CD = 5    # seconds per staff member between forced single-target emotes
_ROOM_EMOTE_CD  = 30   # seconds per staff member between room-wide emotes


async def handle_force_emote(bot: "BaseBot", user: "User", args: list) -> None:
    """!emote @user <emote> — staff: force-loop a player into an emote.

    Target can still type "stop" to cancel their own loop.
    """
    uid   = user.id
    uname = user.username

    if not can_moderate(uname):
        await _w(bot, uid, "❌ Staff only.")
        return

    if len(args) < 3:
        await _w(bot, uid, "Usage: !emote @user <emote>")
        return

    target_name = args[1].lstrip("@")
    emote_name  = args[2].lower()

    eid = lookup_emote(emote_name)
    if not eid:
        await _w(bot, uid, f"❌ Unknown emote '{emote_name}'. See !emotes.")
        return

    remaining = _cd_remaining(_force_emote_cd, uid, _FORCE_EMOTE_CD)
    if remaining > 0:
        await _w(bot, uid, f"⏳ Cooldown: {remaining:.0f}s")
        return

    from modules.room_utils import _resolve_user_in_room
    pair = await _resolve_user_in_room(bot, target_name)
    if not pair:
        await _w(bot, uid, f"❌ @{target_name} is not in the room.")
        return
    target_user, _ = pair
    target_uid = target_user.id

    _cd_set(_force_emote_cd, uid)

    # Cancel any existing loop for that player, then start a fresh one
    _cancel_player_loop(target_uid)

    ok = await _send(bot, eid, target_uid)
    if not ok:
        await _w(bot, uid, "❌ Emote could not be sent.")
        return

    task = asyncio.create_task(_run_player_loop(bot, target_uid, eid))
    _player_loops[target_uid]  = task
    _player_emotes[target_uid] = eid

    short = eid.replace("emote-", "")
    await _w(bot, uid, f"🎭 Forced @{target_user.username} → {short}")
    _log("force_emote",
         stage="force_emote", staff=uname,
         target=target_user.username, emote=eid, result="ok")


async def handle_room_emote(bot: "BaseBot", user: "User", args: list) -> None:
    """!emote all|allbots <emote> — staff: one-shot emote for everyone in the room.

    'all'     excludes known bots.
    'allbots' includes everyone (bots too).
    Does NOT loop — fires once per user.
    """
    uid   = user.id
    uname = user.username

    if not can_moderate(uname):
        await _w(bot, uid, "❌ Staff only.")
        return

    if len(args) < 3:
        await _w(bot, uid, "Usage: !emote all <emote>  or  !emote allbots <emote>")
        return

    sub          = args[1].lower()   # "all" or "allbots"
    emote_name   = args[2].lower()
    include_bots = sub == "allbots"

    eid = lookup_emote(emote_name)
    if not eid:
        await _w(bot, uid, f"❌ Unknown emote '{emote_name}'. See !emotes.")
        return

    remaining = _cd_remaining(_room_emote_cd, uid, _ROOM_EMOTE_CD)
    if remaining > 0:
        await _w(bot, uid, f"⏳ Room emote cooldown: {remaining:.0f}s")
        return

    _cd_set(_room_emote_cd, uid)

    # Build the set of known bot usernames to exclude
    bot_usernames: frozenset[str] = frozenset()
    if not include_bots:
        try:
            instances = db.get_bot_instances()
            bot_usernames = frozenset(
                r.get("bot_username", "").lower()
                for r in instances
                if r.get("bot_username")
            )
        except Exception:
            pass

    from modules.room_utils import _get_all_room_users
    users = await _get_all_room_users(bot)

    count = 0
    for u, _ in users:
        if not include_bots and u.username.lower() in bot_usernames:
            continue
        if await _send(bot, eid, u.id):
            count += 1

    short = eid.replace("emote-", "")
    await _w(bot, uid, f"🎭 Room emote → {short} ({count} players)")
    _log("room_emote",
         stage="room_emote", staff=uname, emote=eid,
         include_bots=include_bots, result=f"sent_to_{count}")
