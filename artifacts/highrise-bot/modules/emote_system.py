"""modules/emote_system.py
--------------------------
Clean two-catalog emote system rebuild.

BOT_SELF_EMOTES  →  !botemote / bot loops  →  send_emote(eid)           [NO user_id]
PLAYER_EMOTES    →  player chat trigger    →  send_emote(eid, user.id)   [WITH user_id]

All IDs are exact — no auto-prefix, no SDK scan, no guessing.
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot, User

import database as db
from modules.admin_cmds import is_admin, is_owner, can_moderate
from data.hardcoded_emotes import (
    BOT_SELF_EMOTES,
    PLAYER_EMOTES,
    UNRESOLVED_PLAYER_EMOTES,
    lookup_bot_emote,
    lookup_player_emote,
    get_player_emote_list,
    _norm,
)

# ---------------------------------------------------------------------------
# Timing map — loaded from timed_free_emotes catalog
# ---------------------------------------------------------------------------
_EMOTE_DURATIONS: dict[str, float] = {}   # emote_id → seconds (0 = one-shot)
_DEFAULT_LOOP_INTERVAL: float = 5.0
_BOT_LOOP_INTERVAL: float = 8.0


def _load_timing() -> None:
    global _EMOTE_DURATIONS
    try:
        from data.timed_free_emotes import timed_free_emotes_list
        _EMOTE_DURATIONS = {e["value"]: float(e["time"])
                            for e in timed_free_emotes_list if e.get("value")}
    except Exception as exc:
        print(f"[EMOTE] timing load failed: {exc}")


_load_timing()

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception as exc:
        print(f"[EMOTE WHISPER ERR] {exc}")


def _is_admin(username: str) -> bool:
    return is_admin(username) or is_owner(username)


def _log(stage: str, **kw: object) -> None:
    parts = " ".join(f"{k}={v!r}" for k, v in kw.items())
    print(f"[EMOTE] stage={stage} {parts}")


# ---------------------------------------------------------------------------
# Player emote send + loop
# ---------------------------------------------------------------------------
_player_loops:  dict[str, asyncio.Task] = {}   # user_id  → active loop Task
_player_emotes: dict[str, str]          = {}   # user_id  → current emote_id
_bot_loops:     dict[str, asyncio.Task] = {}   # bot_mode → active loop Task

_emote_cd: dict[str, float] = {}
_punch_cd: dict[str, float] = {}
_sword_cd: dict[str, float] = {}
_force_emote_cd: dict[str, float] = {}
_room_emote_cd:  dict[str, float] = {}
_EMOTE_CD   = 3
_PUNCH_CD   = 10
_SWORD_CD   = 10
_FORCE_EMOTE_CD = 5
_ROOM_EMOTE_CD  = 30

# For notify_emote_event (used by emote_logger / on_emote in main.py)
_emote_event_listeners: dict[tuple[str, str], asyncio.Event] = {}


def _cd_remaining(store: dict, uid: str, secs: int) -> float:
    return max(0.0, secs - (time.time() - store.get(uid, 0.0)))


def _cd_set(store: dict, uid: str) -> None:
    store[uid] = time.time()


async def _send_player(bot: "BaseBot", eid: str, uid: str) -> bool:
    """Send a player-directed emote: send_emote(eid, uid)."""
    try:
        await bot.highrise.send_emote(eid, uid)
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"[EMOTE FAIL player] eid={eid!r} uid={uid!r} error={exc!r}")
        return False


def _cancel_player_loop(uid: str) -> None:
    task = _player_loops.pop(uid, None)
    if task and not task.done():
        task.cancel()
    _player_emotes.pop(uid, None)


async def _run_player_loop(bot: "BaseBot", uid: str, eid: str) -> None:
    """Loop a player emote: send_emote(eid, uid) on interval."""
    interval = _EMOTE_DURATIONS.get(eid)
    if interval is None:
        interval = _DEFAULT_LOOP_INTERVAL
    if interval <= 0:
        return  # one-shot, do not loop
    while True:
        try:
            await bot.highrise.send_emote(eid, uid)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[EMOTE LOOP FAIL player] eid={eid!r} uid={uid!r} {exc!r}")
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Bot self-emote loop (NO user_id)
# ---------------------------------------------------------------------------

def _start_bot_loop(bot: "BaseBot", bot_mode: str, eid: str,
                    bot_uid: str = "") -> float:
    """Start (or restart) a bot self-emote loop using send_emote(eid) — no target."""
    old = _bot_loops.pop(bot_mode, None)
    if old and not old.done():
        old.cancel()

    raw_dur = _EMOTE_DURATIONS.get(eid)
    interval: float
    if raw_dur is None:
        interval = _BOT_LOOP_INTERVAL
    elif raw_dur <= 0:
        interval = 30.0   # one-shot: re-play after 30 s
    else:
        interval = raw_dur

    async def _loop() -> None:
        _iter = 0
        while True:
            _iter += 1
            if _iter == 1 or _iter % 20 == 0:
                print(f"[EMOTE BOT] mode={bot_mode!r} eid={eid!r} iter={_iter}")
            try:
                await bot.highrise.send_emote(eid)   # ← NO user_id
                if _iter == 1:
                    print(f"[EMOTE BOT OK] eid={eid!r}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[EMOTE BOT FAIL] mode={bot_mode!r} eid={eid!r}"
                      f" iter={_iter} error={exc!r}")
            await asyncio.sleep(interval)

    _bot_loops[bot_mode] = asyncio.create_task(_loop())
    _log("bot_loop_start", bot=bot_mode, emote=eid, interval=round(interval, 2))
    return interval


# ---------------------------------------------------------------------------
# Public API — called from main.py on_chat
# ---------------------------------------------------------------------------

def is_plain_emote(text: str) -> bool:
    """True if the player's chat message matches a known PLAYER_EMOTE."""
    return _norm(text.strip()) in PLAYER_EMOTES


async def start_player_emote(bot: "BaseBot", user: "User",
                              emote_name: str) -> None:
    """Start (or replace) a looping player emote from a plain chat trigger.

    Silently ignores unknown names and rate-limited requests.
    """
    uid = user.id
    if _cd_remaining(_emote_cd, uid, _EMOTE_CD) > 0:
        return

    eid = lookup_player_emote(emote_name)
    if not eid:
        return

    _cancel_player_loop(uid)
    _cd_set(_emote_cd, uid)

    ok = await _send_player(bot, eid, uid)
    if not ok:
        return

    interval = _EMOTE_DURATIONS.get(eid)
    if interval is None:
        interval = _DEFAULT_LOOP_INTERVAL
    if interval <= 0:
        _log("emote_oneshot", user_id=uid, emote=eid)
        return

    task = asyncio.create_task(_run_player_loop(bot, uid, eid))
    _player_loops[uid]  = task
    _player_emotes[uid] = eid
    _log("emote_start", user_id=uid, username=user.username, emote=eid)


async def stop_player_emote(bot: "BaseBot", user: "User") -> None:
    """Cancel a player's active emote loop."""
    uid      = user.id
    had_loop = uid in _player_loops
    _cancel_player_loop(uid)
    _log("emote_stop", user_id=uid, username=user.username, had_loop=had_loop)
    await _w(bot, uid, "Emote stopped.")


def on_player_leave(uid: str) -> None:
    """Clean up a player's emote loop when they leave."""
    _cancel_player_loop(uid)


def notify_emote_event(user_id: str, emote_id: str) -> None:
    """Signal that on_emote fired for (user_id, emote_id).

    Called from main.py on_emote. Releases any scan/diag listener waiting
    for animation confirmation. (Listeners will be empty after the rebuild —
    this is kept for API compatibility with emote_logger.)
    """
    key = (user_id, emote_id)
    evt = _emote_event_listeners.pop(key, None)
    if evt:
        print(f"[EMOTE EVENT] performer={user_id!r} emote={emote_id!r}")
        evt.set()


# ---------------------------------------------------------------------------
# Pagination helper
# ---------------------------------------------------------------------------
_PAGE_SIZE = 12


def _paginate(items: list[str], page: int,
              total: int, header: str) -> str:
    """Build a ≤249-char page string from a flat list of short labels."""
    pages = max(1, (len(items) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page  = max(1, min(page, pages))
    start = (page - 1) * _PAGE_SIZE
    chunk = items[start: start + _PAGE_SIZE]
    prefix = f"{header} p{page}/{pages}: "
    body   = ", ".join(chunk)
    msg    = prefix + body
    if len(msg) > 249:
        msg = msg[:246] + "..."
    return msg


# ---------------------------------------------------------------------------
# !emotes [page] — list BOT_SELF_EMOTES
# ---------------------------------------------------------------------------

async def handle_emotes_auto(bot: "BaseBot", user: "User",
                             args: list) -> None:
    """!emotes [page] — show the bot self-emote catalog (BOT_SELF_EMOTES)."""
    page = int(args[1]) if len(args) >= 2 and args[1].isdigit() else 1
    names = [d for d, _ in BOT_SELF_EMOTES]
    total = len(names)
    msg   = _paginate(names, page, total, f"🎭 Emotes ({total})")
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# !playeremotes [page] — list resolved PLAYER_EMOTES
# ---------------------------------------------------------------------------

async def handle_playeremotes(bot: "BaseBot", user: "User",
                               args: list) -> None:
    """!playeremotes [page] — show player-trigger emote catalog."""
    page  = int(args[1]) if len(args) >= 2 and args[1].isdigit() else 1
    pairs = get_player_emote_list()
    names = [d for d, _ in pairs]
    total = len(names)
    if not names:
        await _w(bot, user.id, "No player emotes resolved. See !unresolvedplayeremotes.")
        return
    msg = _paginate(names, page, total, f"🕹 Player emotes ({total})")
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# !unresolvedplayeremotes — list names that couldn't resolve to an ID
# ---------------------------------------------------------------------------

async def handle_unresolvedplayeremotes(bot: "BaseBot", user: "User",
                                         _args: list) -> None:
    """!unresolvedplayeremotes — show player names with no matching emote ID."""
    uid = user.id
    if not UNRESOLVED_PLAYER_EMOTES:
        await _w(bot, uid, "All player emote names resolved successfully.")
        return
    n     = len(UNRESOLVED_PLAYER_EMOTES)
    names = ", ".join(UNRESOLVED_PLAYER_EMOTES)
    msg   = f"Unresolved ({n}): {names}"
    # Send in 249-char chunks if needed
    chunks = [msg[i:i+249] for i in range(0, len(msg), 249)]
    for chunk in chunks:
        await _w(bot, uid, chunk)
        if len(chunks) > 1:
            await asyncio.sleep(0.3)


# ---------------------------------------------------------------------------
# !emoteid <name> — show the raw ID for a bot self-emote
# ---------------------------------------------------------------------------

async def handle_emoteid(bot: "BaseBot", user: "User", args: list) -> None:
    """!emoteid <name> — show the raw emote_id from BOT_SELF_EMOTES."""
    uid = user.id
    if len(args) < 2:
        await _w(bot, uid, "Usage: !emoteid <name>")
        return
    name = " ".join(args[1:]).lower()
    eid  = lookup_bot_emote(name)
    if not eid:
        await _w(bot, uid, f"Not found in bot emote catalog: '{name}'")
        return
    await _w(bot, uid, f"Bot emote ID: {eid}")


# ---------------------------------------------------------------------------
# !playeremoteid <name> — show the raw ID for a player emote
# ---------------------------------------------------------------------------

async def handle_playeremoteid(bot: "BaseBot", user: "User",
                                args: list) -> None:
    """!playeremoteid <name> — show the raw emote_id from PLAYER_EMOTES."""
    uid = user.id
    if len(args) < 2:
        await _w(bot, uid, "Usage: !playeremoteid <name>")
        return
    name = " ".join(args[1:]).lower()
    eid  = lookup_player_emote(name)
    if not eid:
        await _w(bot, uid, f"Not found in player emote catalog: '{name}'")
        return
    await _w(bot, uid, f"Player emote ID: {eid}")


# ---------------------------------------------------------------------------
# !botemote [@botname] <name> — admin: set a bot self-emote loop
# ---------------------------------------------------------------------------

async def handle_botemote(bot: "BaseBot", user: "User", args: list) -> None:
    """!botemote [@botname] <emote> — admin: loop a self-emote on a bot.

    Examples:
      !botemote wave              — loop on this bot
      !botemote @DJ_DUDU wave     — target by username
    Sends: send_emote(emote_id)   — NO user_id.
    """
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "Admin only.")
        return
    if len(args) < 2:
        await _w(bot, uid, "Usage: !botemote <emote>  or  !botemote @botname <emote>")
        return

    from config import BOT_MODE, BOT_USERNAME
    from modules.gold import get_bot_username as _get_bot_uname

    raw1 = args[1].lstrip("@").lower()

    # If args[1] is not a valid emote name, treat it as a bot-name target
    if len(args) >= 3 and not lookup_bot_emote(raw1):
        raw_target = raw1
        emote_name = args[2].lstrip("@").lower()
    else:
        raw_target = BOT_MODE.lower()
        emote_name = raw1

    eid = lookup_bot_emote(emote_name)
    if not eid:
        await _w(bot, uid,
                 f"Unknown emote '{emote_name}'. Try !emotes to browse.")
        return

    this_mode  = BOT_MODE.lower()
    this_uname = (_get_bot_uname() or BOT_USERNAME or "").strip().lower()
    is_this_bot = (raw_target == this_mode or
                   bool(this_uname and raw_target == this_uname))

    store_key = this_mode if is_this_bot else raw_target
    db.set_room_setting(f"bot_emote_{store_key}", eid)
    _log("bot_emote_set", admin=uname, bot=store_key, emote=eid)

    if is_this_bot:
        dur     = _start_bot_loop(bot, BOT_MODE, eid)
        display = f"@{_get_bot_uname() or BOT_MODE}"
        await _w(bot, uid, f"✅ {display} looping {eid} (every {dur:.0f}s).")
        return

    await _w(bot, uid, f"✅ Saved. @{raw_target} will loop {eid} on next restart.")


# ---------------------------------------------------------------------------
# !stopbotemote [botname] — admin: stop bot emote loop
# ---------------------------------------------------------------------------

async def handle_stopbotemote(bot: "BaseBot", user: "User",
                               args: list) -> None:
    """!stopbotemote [botname] — admin: stop this bot's looping emote."""
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "Admin only.")
        return

    from config import BOT_MODE
    bot_name = args[1].lower() if len(args) >= 2 else BOT_MODE.lower()
    db.set_room_setting(f"bot_emote_{bot_name}", "")

    if BOT_MODE.lower() == bot_name:
        task = _bot_loops.pop(BOT_MODE, None)
        if task and not task.done():
            task.cancel()

    _log("bot_emote_cleared", admin=uname, bot=bot_name)
    await _w(bot, uid, "✅ Bot emote stopped.")


# ---------------------------------------------------------------------------
# !botemoteid [@botname] <raw-id> — admin: loop by exact raw ID
# ---------------------------------------------------------------------------

async def handle_botemoteid(bot: "BaseBot", user: "User",
                             args: list) -> None:
    """!botemoteid [@botname] <raw-id> — admin: loop an emote by exact raw ID.

    The ID is used exactly as given — no alias lookup, no prefix added.
    Useful for testing IDs before adding them to the catalog.
    """
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "Admin only.")
        return
    if len(args) < 2:
        await _w(bot, uid,
                 "Usage: !botemoteid <raw-id>  or  !botemoteid @botname <raw-id>")
        return

    from config import BOT_MODE, BOT_USERNAME
    from modules.gold import get_bot_username as _get_bot_uname

    raw1 = args[1].lstrip("@").lower()
    if len(args) >= 3:
        raw_target = raw1
        raw_id     = args[2].lstrip("@").lower()
    else:
        raw_target = BOT_MODE.lower()
        raw_id     = raw1

    eid = raw_id   # exact — no prefix added

    this_mode  = BOT_MODE.lower()
    this_uname = (_get_bot_uname() or BOT_USERNAME or "").strip().lower()
    is_this_bot = (raw_target == this_mode or
                   bool(this_uname and raw_target == this_uname))

    store_key = this_mode if is_this_bot else raw_target
    db.set_room_setting(f"bot_emote_{store_key}", eid)
    _log("bot_emote_set", admin=uname, bot=store_key, emote=eid, via="raw_id")

    if is_this_bot:
        dur     = _start_bot_loop(bot, BOT_MODE, eid)
        display = f"@{_get_bot_uname() or BOT_MODE}"
        await _w(bot, uid,
                 f"✅ {display} looping {eid} (every {dur:.0f}s) [raw ID].")
        return

    await _w(bot, uid, f"✅ Saved. @{raw_target} will loop {eid} on next restart.")


# ---------------------------------------------------------------------------
# Startup recovery — restore bot emote loop from DB
# ---------------------------------------------------------------------------

async def startup_bot_emote_recovery(bot: "BaseBot") -> None:
    """On startup, restore a persisted bot emote loop from the DB."""
    from config import BOT_MODE
    eid = db.get_room_setting(f"bot_emote_{BOT_MODE.lower()}", "")
    if not eid:
        return
    await asyncio.sleep(6)   # let the bot fully connect first
    _start_bot_loop(bot, BOT_MODE, eid)
    _log("emote_recovery", bot=BOT_MODE, emote=eid)


# ---------------------------------------------------------------------------
# Social interactions — !punch / !swordfight
# ---------------------------------------------------------------------------
_PUNCH_ATTACKER_EMOTE = "emoji-punch"       # confirmed in BOT_SELF_EMOTES
_PUNCH_TARGET_EMOTE   = "emote-embarrassed" # confirmed in BOT_SELF_EMOTES
_SWORD_EMOTE          = "emote-swordfight"  # confirmed in BOT_SELF_EMOTES


async def handle_punch_emote(bot: "BaseBot", user: "User",
                              args: list) -> None:
    """!punch @user — attacker does punch emote, target does embarrassed; 10 s CD."""
    uid   = user.id
    uname = user.username

    remaining = _cd_remaining(_punch_cd, uid, _PUNCH_CD)
    if remaining > 0:
        await _w(bot, uid, f"Punch cooldown: {remaining:.0f}s")
        return

    if len(args) < 2:
        await _w(bot, uid, "Usage: !punch @username")
        return

    target_name = args[1].lstrip("@")
    if target_name.lower() == uname.lower():
        await _w(bot, uid, "You can't punch yourself!")
        return

    from modules.room_utils import _resolve_user_in_room
    pair = await _resolve_user_in_room(bot, target_name)
    if not pair:
        await _w(bot, uid, f"@{target_name} is not in the room.")
        return
    target_user, _ = pair

    if can_moderate(target_user.username) and not _is_admin(uname):
        await _w(bot, uid, "You can't punch staff members.")
        return

    _cd_set(_punch_cd, uid)
    await _send_player(bot, _PUNCH_ATTACKER_EMOTE, uid)
    await asyncio.sleep(0.3)
    await _send_player(bot, _PUNCH_TARGET_EMOTE, target_user.id)

    try:
        await bot.highrise.chat(f"🥊 {uname} punched {target_user.username}!"[:249])
    except Exception:
        pass
    _log("punch", user_id=uid, username=uname, target=target_user.username)


async def handle_swordfight(bot: "BaseBot", user: "User",
                             args: list) -> None:
    """!swordfight @user — both players do swordfight emote simultaneously; 10 s CD."""
    uid   = user.id
    uname = user.username

    if len(args) < 2:
        await _w(bot, uid, "Usage: !swordfight @user")
        return

    target_name = args[1].lstrip("@")
    if target_name.lower() == uname.lower():
        await _w(bot, uid, "You can't swordfight yourself!")
        return

    remaining = _cd_remaining(_sword_cd, uid, _SWORD_CD)
    if remaining > 0:
        await _w(bot, uid, f"Swordfight cooldown: {remaining:.0f}s")
        return

    from modules.room_utils import _resolve_user_in_room
    pair = await _resolve_user_in_room(bot, target_name)
    if not pair:
        await _w(bot, uid, f"@{target_name} is not in the room.")
        return
    target_user, _ = pair

    _cd_set(_sword_cd, uid)
    await asyncio.gather(
        _send_player(bot, _SWORD_EMOTE, uid),
        _send_player(bot, _SWORD_EMOTE, target_user.id),
    )

    try:
        await bot.highrise.chat(
            f"⚔️ {uname} and {target_user.username} are swordfighting!"[:249]
        )
    except Exception:
        pass
    _log("swordfight", user_id=uid, username=uname, target=target_user.username)


# ---------------------------------------------------------------------------
# Staff forced emotes — !emote @user <name> / !emote all <name>
# ---------------------------------------------------------------------------

async def handle_force_emote(bot: "BaseBot", user: "User",
                              args: list) -> None:
    """!emote @user <emote> — staff: force-loop a player into an emote."""
    uid   = user.id
    uname = user.username

    if not can_moderate(uname):
        await _w(bot, uid, "Staff only.")
        return

    if len(args) < 3:
        await _w(bot, uid, "Usage: !emote @user <emote>")
        return

    target_name = args[1].lstrip("@")
    emote_name  = args[2].lower()

    eid = lookup_player_emote(emote_name) or lookup_bot_emote(emote_name)
    if not eid:
        await _w(bot, uid, f"Unknown emote '{emote_name}'. See !emotes.")
        return

    remaining = _cd_remaining(_force_emote_cd, uid, _FORCE_EMOTE_CD)
    if remaining > 0:
        await _w(bot, uid, f"Cooldown: {remaining:.0f}s")
        return

    from modules.room_utils import _resolve_user_in_room
    pair = await _resolve_user_in_room(bot, target_name)
    if not pair:
        await _w(bot, uid, f"@{target_name} is not in the room.")
        return
    target_user, _ = pair
    target_uid = target_user.id

    _cd_set(_force_emote_cd, uid)
    _cancel_player_loop(target_uid)

    ok = await _send_player(bot, eid, target_uid)
    if not ok:
        await _w(bot, uid, "Emote could not be sent.")
        return

    task = asyncio.create_task(_run_player_loop(bot, target_uid, eid))
    _player_loops[target_uid]  = task
    _player_emotes[target_uid] = eid

    await _w(bot, uid, f"Forced @{target_user.username} → {eid}")
    _log("force_emote", staff=uname, target=target_user.username, emote=eid)


async def handle_room_emote(bot: "BaseBot", user: "User",
                             args: list) -> None:
    """!emote all|allbots <emote> — staff: one-shot emote for everyone."""
    uid   = user.id
    uname = user.username

    if not can_moderate(uname):
        await _w(bot, uid, "Staff only.")
        return

    if len(args) < 3:
        await _w(bot, uid, "Usage: !emote all <emote>  or  !emote allbots <emote>")
        return

    sub          = args[1].lower()
    emote_name   = args[2].lower()
    include_bots = sub == "allbots"

    eid = lookup_player_emote(emote_name) or lookup_bot_emote(emote_name)
    if not eid:
        await _w(bot, uid, f"Unknown emote '{emote_name}'. See !emotes.")
        return

    remaining = _cd_remaining(_room_emote_cd, uid, _ROOM_EMOTE_CD)
    if remaining > 0:
        await _w(bot, uid, f"Room emote cooldown: {remaining:.0f}s")
        return

    _cd_set(_room_emote_cd, uid)

    bot_usernames: frozenset[str] = frozenset()
    if not include_bots:
        try:
            instances = db.get_bot_instances()
            bot_usernames = frozenset(
                r.get("bot_username", "").lower()
                for r in instances if r.get("bot_username")
            )
        except Exception:
            pass

    from modules.room_utils import _get_all_room_users
    users = await _get_all_room_users(bot)

    count = 0
    for u, _ in users:
        if not include_bots and u.username.lower() in bot_usernames:
            continue
        if await _send_player(bot, eid, u.id):
            count += 1

    await _w(bot, uid, f"Room emote → {eid} ({count} players)")
    _log("room_emote", staff=uname, emote=eid, count=count)


# ---------------------------------------------------------------------------
# Stubs for removed commands — kept so main.py imports don't break
# ---------------------------------------------------------------------------

async def handle_emotemode(bot: "BaseBot", user: "User",
                            args: list) -> None:
    """Removed — emote mode system replaced by two-catalog architecture."""
    await _w(bot, user.id,
             "Emote mode removed. Use !emotes (bot catalog) and !playeremotes (player catalog).")


async def handle_emotediag(bot: "BaseBot", user: "User",
                            args: list) -> None:
    """Removed — replaced by two-catalog system."""
    await _w(bot, user.id, "Emote diagnostics removed. Use !emoteid <name> or !botemoteid <raw-id>.")


async def handle_unsupportedemotes(bot: "BaseBot", user: "User",
                                    _args: list) -> None:
    """Removed — replaced by two-catalog system."""
    await _w(bot, user.id, "Unsupported emotes list removed. Use !unresolvedplayeremotes.")


async def handle_markemoteworks(bot: "BaseBot", user: "User",
                                 args: list) -> None:
    """Removed."""
    await _w(bot, user.id, "Command removed. Catalog is now hardcoded from verified list.")


async def handle_markemoteunsupported(bot: "BaseBot", user: "User",
                                       args: list) -> None:
    """Removed."""
    await _w(bot, user.id, "Command removed. Catalog is now hardcoded from verified list.")
