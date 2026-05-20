"""modules/emote_system.py
--------------------------
Clean two-catalog emote system.

BOT_SELF_EMOTES  → !botemote / bot loops  → send_emote(eid)           [NO user_id]
PLAYER_EMOTES    → player chat trigger    → send_emote(eid, user.id)   [WITH user_id]

All IDs exact — no auto-prefix, no SDK scan, no guessing.
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
    PLAYER_EMOTE_ALIASES,
    lookup_bot_emote,
    lookup_player_emote,
    get_player_trigger_names,
    _norm,
)

# ---------------------------------------------------------------------------
# Timing map — loaded from timed_free_emotes catalog
# ---------------------------------------------------------------------------
_EMOTE_DURATIONS: dict[str, float] = {}
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
# Player + bot loop state
# ---------------------------------------------------------------------------
_player_loops:  dict[str, asyncio.Task] = {}
_player_emotes: dict[str, str]          = {}
_bot_loops:     dict[str, asyncio.Task] = {}

_emote_cd:       dict[str, float] = {}
_punch_cd:       dict[str, float] = {}
_sword_cd:       dict[str, float] = {}
_force_emote_cd: dict[str, float] = {}
_room_emote_cd:  dict[str, float] = {}
_EMOTE_CD       = 3
_PUNCH_CD       = 10
_SWORD_CD       = 10
_FORCE_EMOTE_CD = 5
_ROOM_EMOTE_CD  = 30

# For notify_emote_event (API-compat with emote_logger / on_emote)
_emote_event_listeners: dict[tuple[str, str], asyncio.Event] = {}


def _cd_remaining(store: dict, uid: str, secs: int) -> float:
    return max(0.0, secs - (time.time() - store.get(uid, 0.0)))


def _cd_set(store: dict, uid: str) -> None:
    store[uid] = time.time()


# ---------------------------------------------------------------------------
# Send helpers
# ---------------------------------------------------------------------------

async def _send_player(bot: "BaseBot", eid: str, uid: str) -> bool:
    """send_emote(eid, uid) — player directed."""
    try:
        await bot.highrise.send_emote(eid, uid)
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"[EMOTE FAIL player] eid={eid!r} uid={uid!r} {exc!r}")
        return False


def _cancel_player_loop(uid: str) -> None:
    task = _player_loops.pop(uid, None)
    if task and not task.done():
        task.cancel()
    _player_emotes.pop(uid, None)


async def _run_player_loop(bot: "BaseBot", uid: str, eid: str) -> None:
    interval = _EMOTE_DURATIONS.get(eid) or _DEFAULT_LOOP_INTERVAL
    if interval <= 0:
        return
    while True:
        try:
            await bot.highrise.send_emote(eid, uid)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[EMOTE LOOP FAIL] eid={eid!r} uid={uid!r} {exc!r}")
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Bot self-emote loop (NO user_id)
# ---------------------------------------------------------------------------

def _start_bot_loop(bot: "BaseBot", bot_mode: str, eid: str,
                    bot_uid: str = "") -> float:
    """Start (or restart) a bot self-emote loop — send_emote(eid) only."""
    old = _bot_loops.pop(bot_mode, None)
    if old and not old.done():
        old.cancel()

    raw_dur = _EMOTE_DURATIONS.get(eid)
    interval: float
    if raw_dur is None:
        interval = _BOT_LOOP_INTERVAL
    elif raw_dur <= 0:
        interval = 30.0
    else:
        interval = raw_dur

    async def _loop() -> None:
        _iter = 0
        while True:
            _iter += 1
            if _iter == 1 or _iter % 20 == 0:
                print(f"[EMOTE BOT] mode={bot_mode!r} eid={eid!r} iter={_iter}")
            try:
                await bot.highrise.send_emote(eid)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[EMOTE BOT FAIL] mode={bot_mode!r} eid={eid!r}"
                      f" iter={_iter} error={exc!r}")
            await asyncio.sleep(interval)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        print(f"[EMOTE] _start_bot_loop: no running event loop "
              f"(mode={bot_mode!r} eid={eid!r}) — skipped.")
        return interval
    _bot_loops[bot_mode] = loop.create_task(_loop())
    _log("bot_loop_start", bot=bot_mode, emote=eid, interval=round(interval, 2))
    return interval


# ---------------------------------------------------------------------------
# Stacked-column page builder  (≤ 249 chars, 3 cols × 4 rows = 12 per page)
# ---------------------------------------------------------------------------
_COLS    = 3
_ROWS    = 4
_COL_W   = 16
_PER_PAGE = _COLS * _ROWS   # 12


def _stacked_page(items: list[str], page: int, total_pages: int,
                  header: str) -> str:
    """Build a multi-line stacked-columns whisper page."""
    lines = [f"{header} {page}/{total_pages}"]
    for r in range(_ROWS):
        chunk = items[r * _COLS: (r + 1) * _COLS]
        if not chunk:
            break
        row_parts = []
        for i, name in enumerate(chunk):
            cell = name[:_COL_W - 1]
            row_parts.append(cell.ljust(_COL_W) if i < len(chunk) - 1 else cell)
        lines.append("".join(row_parts).rstrip())
    return "\n".join(lines)[:249]


# ---------------------------------------------------------------------------
# Public API — called from main.py on_chat
# ---------------------------------------------------------------------------

def is_plain_emote(text: str) -> bool:
    """True if the player's chat message matches a known PLAYER_EMOTE."""
    return _norm(text.strip()) in PLAYER_EMOTES


async def start_player_emote(bot: "BaseBot", user: "User",
                              emote_name: str) -> None:
    """Start (or replace) a looping player emote from a plain chat trigger."""
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
    interval = _EMOTE_DURATIONS.get(eid) or _DEFAULT_LOOP_INTERVAL
    if interval <= 0:
        _log("emote_oneshot", user_id=uid, emote=eid)
        return
    task = asyncio.create_task(_run_player_loop(bot, uid, eid))
    _player_loops[uid]  = task
    _player_emotes[uid] = eid
    _log("emote_start", user_id=uid, username=user.username, emote=eid)


async def stop_player_emote(bot: "BaseBot", user: "User") -> None:
    """Cancel a player's active emote loop."""
    uid = user.id
    had = uid in _player_loops
    _cancel_player_loop(uid)
    _log("emote_stop", user_id=uid, had_loop=had)
    await _w(bot, uid, "Emote stopped.")


def on_player_leave(uid: str) -> None:
    """Clean up a player's emote loop when they leave."""
    _cancel_player_loop(uid)


def notify_emote_event(user_id: str, emote_id: str) -> None:
    """Signal that on_emote fired — kept for API compat with emote_logger."""
    key = (user_id, emote_id)
    evt = _emote_event_listeners.pop(key, None)
    if evt:
        print(f"[EMOTE EVENT] performer={user_id!r} emote={emote_id!r}")
        evt.set()


# ---------------------------------------------------------------------------
# !emote <sub> dispatcher — routes: list, stop, count, <name>
# ---------------------------------------------------------------------------

async def handle_emote_cmd(bot: "BaseBot", user: "User", args: list) -> None:
    """!emote <name|list|stop|count> — player self-emote command."""
    uid   = user.id
    uname = user.username

    if len(args) < 2:
        n = len(PLAYER_EMOTES)
        await _w(bot, uid,
                 f"Usage: !emote <name>  !emote list  !emote stop  "
                 f"!emote count  ({n} emotes available)")
        return

    sub = args[1].lower()

    if sub == "list":
        await _handle_emote_list(bot, uid)
    elif sub == "stop":
        await stop_player_emote(bot, user)
    elif sub == "count":
        await _w(bot, uid,
                 f"Player emotes: {len(PLAYER_EMOTES)} | "
                 f"Bot emotes: {len(BOT_SELF_EMOTES)}")
    else:
        # treat as emote name
        eid = lookup_player_emote(sub)
        if not eid:
            await _w(bot, uid,
                     f"Unknown emote '{sub}'. Try !emote list to see all.")
            return
        _cancel_player_loop(uid)
        ok = await _send_player(bot, eid, uid)
        if not ok:
            await _w(bot, uid, f"Could not send emote '{sub}'.")
            return
        interval = _EMOTE_DURATIONS.get(eid) or _DEFAULT_LOOP_INTERVAL
        if interval <= 0:
            return
        task = asyncio.create_task(_run_player_loop(bot, uid, eid))
        _player_loops[uid]  = task
        _player_emotes[uid] = eid
        _log("emote_start_cmd", user_id=uid, username=uname, emote=eid)


async def _handle_emote_list(bot: "BaseBot", uid: str) -> None:
    """Auto-send all pages of PLAYER_EMOTES in stacked columns."""
    names       = get_player_trigger_names()
    total       = len(names)
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    for page in range(1, total_pages + 1):
        start = (page - 1) * _PER_PAGE
        chunk = names[start: start + _PER_PAGE]
        msg   = _stacked_page(chunk, page, total_pages, "🎭 Player Emotes")
        await _w(bot, uid, msg)
        if page < total_pages:
            await asyncio.sleep(0.4)


# Legacy alias kept for main.py routing that still calls handle_emotes_auto
async def handle_emotes_auto(bot: "BaseBot", user: "User",
                              _args: list) -> None:
    """!emotes — redirect to !emote list (now !botemotes for bot list)."""
    await _handle_emote_list(bot, user.id)


# ---------------------------------------------------------------------------
# !botemotes — show all BOT_SELF_EMOTES in stacked columns
# ---------------------------------------------------------------------------

async def handle_botemotes(bot: "BaseBot", user: "User",
                            args: list) -> None:
    """!botemotes — show all bot self-emotes in stacked columns, auto-paged."""
    uid         = user.id
    names       = [d for d, _ in BOT_SELF_EMOTES]
    total       = len(names)
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    for page in range(1, total_pages + 1):
        start = (page - 1) * _PER_PAGE
        chunk = names[start: start + _PER_PAGE]
        msg   = _stacked_page(chunk, page, total_pages, "🤖 Bot Emotes")
        await _w(bot, uid, msg)
        if page < total_pages:
            await asyncio.sleep(0.4)


# ---------------------------------------------------------------------------
# !testplayeremote <raw_id> / !testplayeremote @user <raw_id>
# ---------------------------------------------------------------------------

async def handle_testplayeremote(bot: "BaseBot", user: "User",
                                  args: list) -> None:
    """Test an exact raw emote ID on yourself or a target (admin only)."""
    uid   = user.id
    uname = user.username

    if len(args) < 2:
        await _w(bot, uid,
                 "Usage: !testplayeremote <raw_id>  "
                 "or  !testplayeremote @user <raw_id>")
        return

    if args[1].startswith("@") and len(args) >= 3:
        if not _is_admin(uname):
            await _w(bot, uid, "Admin only.")
            return
        target_name = args[1].lstrip("@")
        raw_id      = args[2]
        from modules.room_utils import _resolve_user_in_room
        pair = await _resolve_user_in_room(bot, target_name)
        if not pair:
            await _w(bot, uid, f"@{target_name} is not in the room.")
            return
        target_user, _ = pair
        try:
            await bot.highrise.send_emote(raw_id, target_user.id)
            await _w(bot, uid,
                     f"✅ Sent {raw_id!r} to @{target_user.username}")
        except Exception as exc:
            await _w(bot, uid,
                     f"❌ Rejected: {raw_id!r} → {str(exc)}"[:249])
    else:
        raw_id = args[1]
        try:
            await bot.highrise.send_emote(raw_id, uid)
            await _w(bot, uid, f"✅ Sent {raw_id!r}")
        except Exception as exc:
            await _w(bot, uid,
                     f"❌ Rejected: {raw_id!r} → {str(exc)}"[:249])


# ---------------------------------------------------------------------------
# !findemote <keyword> — search both catalogs
# ---------------------------------------------------------------------------

async def handle_findemote(bot: "BaseBot", user: "User",
                            args: list) -> None:
    """!findemote <keyword> — search bot and player emote names/IDs."""
    uid = user.id
    if len(args) < 2:
        await _w(bot, uid, "Usage: !findemote <keyword>")
        return

    kw = _norm(" ".join(args[1:]))

    bot_hits: list[str] = []
    for disp, eid in BOT_SELF_EMOTES:
        if kw in _norm(disp) or kw in _norm(eid):
            bot_hits.append(f"{disp}={eid}")

    player_hits: list[str] = []
    for trigger, eid in PLAYER_EMOTES.items():
        if kw in trigger or kw in _norm(eid):
            player_hits.append(f"{trigger}={eid}")

    if not bot_hits and not player_hits:
        await _w(bot, uid, f"No emotes found matching '{kw}'")
        return

    if bot_hits:
        snippet = ", ".join(bot_hits[:6])
        msg = f"Bot ({len(bot_hits)}): {snippet}"
        await _w(bot, uid, msg[:249])
    if player_hits:
        snippet = ", ".join(player_hits[:6])
        msg = f"Player ({len(player_hits)}): {snippet}"
        await _w(bot, uid, msg[:249])


# ---------------------------------------------------------------------------
# !emoteid <name> — show raw ID + status
# ---------------------------------------------------------------------------

async def handle_emoteid(bot: "BaseBot", user: "User", args: list) -> None:
    """!emoteid <name> — show raw emote ID and status (bot-only / player-usable / both)."""
    uid = user.id
    if len(args) < 2:
        await _w(bot, uid, "Usage: !emoteid <name>")
        return

    name      = " ".join(args[1:]).lower()
    bot_eid   = lookup_bot_emote(name)
    plyr_eid  = lookup_player_emote(name)

    if not bot_eid and not plyr_eid:
        await _w(bot, uid, f"not found: '{name}'")
        return

    eid = bot_eid or plyr_eid
    if bot_eid and plyr_eid:
        status = "both"
    elif bot_eid:
        status = "bot-only"
    else:
        status = "player-usable"

    await _w(bot, uid, f"[{status}] {eid}")


# ---------------------------------------------------------------------------
# !botemote [@botname] <name> — bot self-loop from BOT_SELF_EMOTES
# ---------------------------------------------------------------------------

async def handle_botemote(bot: "BaseBot", user: "User", args: list) -> None:
    """!botemote [@botname] <emote> — admin: loop a self-emote on a bot.

    Sends: send_emote(emote_id)  — NO user_id.
    """
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "Admin only.")
        return
    if len(args) < 2:
        await _w(bot, uid,
                 "Usage: !botemote <emote>  or  !botemote @botname <emote>")
        return

    from config import BOT_MODE, BOT_USERNAME
    from modules.gold import get_bot_username as _get_bot_uname

    raw1 = args[1].lstrip("@").lower()

    if len(args) >= 3 and not lookup_bot_emote(raw1):
        raw_target = raw1
        emote_name = args[2].lstrip("@").lower()
    else:
        raw_target = BOT_MODE.lower()
        emote_name = raw1

    eid = lookup_bot_emote(emote_name)
    if not eid:
        await _w(bot, uid,
                 f"Unknown emote '{emote_name}'. Try !botemotes.")
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
# !stopbotemote [botname]
# ---------------------------------------------------------------------------

async def handle_stopbotemote(bot: "BaseBot", user: "User",
                               args: list) -> None:
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
# !botemoteid [@botname] <raw-id>
# ---------------------------------------------------------------------------

async def handle_botemoteid(bot: "BaseBot", user: "User",
                             args: list) -> None:
    """!botemoteid [@botname] <raw-id> — loop by exact raw ID, no alias."""
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

    eid = raw_id

    this_mode  = BOT_MODE.lower()
    this_uname = (_get_bot_uname() or BOT_USERNAME or "").strip().lower()
    is_this_bot = (raw_target == this_mode or
                   bool(this_uname and raw_target == this_uname))

    store_key = this_mode if is_this_bot else raw_target
    db.set_room_setting(f"bot_emote_{store_key}", eid)

    if is_this_bot:
        dur     = _start_bot_loop(bot, BOT_MODE, eid)
        display = f"@{_get_bot_uname() or BOT_MODE}"
        await _w(bot, uid,
                 f"✅ {display} looping {eid} (every {dur:.0f}s) [raw ID].")
        return

    await _w(bot, uid, f"✅ Saved. @{raw_target} will loop {eid} on next restart.")


# ---------------------------------------------------------------------------
# Startup recovery
# ---------------------------------------------------------------------------

async def startup_bot_emote_recovery(bot: "BaseBot") -> None:
    """Emote loop recovery — DISABLED for diagnostic run. Re-enable after crash is found."""
    from config import BOT_MODE
    print(f"[EMOTE] startup_bot_emote_recovery: SKIPPED (diagnostic mode) bot={BOT_MODE}")


# ---------------------------------------------------------------------------
# Social: !punch / !swordfight
# ---------------------------------------------------------------------------
_PUNCH_ATTACKER_EMOTE = "emoji-punch"
_PUNCH_TARGET_EMOTE   = "emote-embarrassed"
_SWORD_EMOTE          = "emote-swordfight"


async def handle_punch_emote(bot: "BaseBot", user: "User",
                              args: list) -> None:
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
        await bot.highrise.chat(
            f"🥊 {uname} punched {target_user.username}!"[:249])
    except Exception:
        pass
    _log("punch", user_id=uid, username=uname, target=target_user.username)


async def handle_swordfight(bot: "BaseBot", user: "User",
                             args: list) -> None:
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
            f"⚔️ {uname} and {target_user.username} are swordfighting!"[:249])
    except Exception:
        pass
    _log("swordfight", user_id=uid, username=uname, target=target_user.username)


# ---------------------------------------------------------------------------
# Staff forced emotes
# ---------------------------------------------------------------------------
_force_emote_cd: dict[str, float] = {}
_room_emote_cd:  dict[str, float] = {}


async def handle_force_emote(bot: "BaseBot", user: "User",
                              args: list) -> None:
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
        await _w(bot, uid, f"Unknown emote '{emote_name}'. See !emote list.")
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
    _cd_set(_force_emote_cd, uid)
    _cancel_player_loop(target_user.id)
    ok = await _send_player(bot, eid, target_user.id)
    if not ok:
        await _w(bot, uid, "Emote could not be sent.")
        return
    task = asyncio.create_task(_run_player_loop(bot, target_user.id, eid))
    _player_loops[target_user.id]  = task
    _player_emotes[target_user.id] = eid
    await _w(bot, uid, f"Forced @{target_user.username} → {eid}")
    _log("force_emote", staff=uname, target=target_user.username, emote=eid)


async def handle_room_emote(bot: "BaseBot", user: "User",
                             args: list) -> None:
    uid   = user.id
    uname = user.username
    if not can_moderate(uname):
        await _w(bot, uid, "Staff only.")
        return
    if len(args) < 3:
        await _w(bot, uid,
                 "Usage: !emote all <emote>  or  !emote allbots <emote>")
        return
    sub          = args[1].lower()
    emote_name   = args[2].lower()
    include_bots = sub == "allbots"
    eid = lookup_player_emote(emote_name) or lookup_bot_emote(emote_name)
    if not eid:
        await _w(bot, uid, f"Unknown emote '{emote_name}'. See !emote list.")
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
# Stubs for removed/deprecated commands (imports still satisfy main.py)
# ---------------------------------------------------------------------------

async def handle_emotemode(bot: "BaseBot", user: "User",
                            args: list) -> None:
    await _w(bot, user.id,
             "Emote mode removed. Use !emote list (player) or !botemotes (bot).")


async def handle_emotediag(bot: "BaseBot", user: "User",
                            args: list) -> None:
    await _w(bot, user.id,
             "Emote diagnostics removed. Use !emoteid <name> or !testplayeremote <raw-id>.")


async def handle_unsupportedemotes(bot: "BaseBot", user: "User",
                                    _args: list) -> None:
    await _w(bot, user.id,
             "Removed. Use !findemote to search the catalog.")


async def handle_markemoteworks(bot: "BaseBot", user: "User",
                                 args: list) -> None:
    await _w(bot, user.id, "Command removed. Catalog is hardcoded from verified list.")


async def handle_markemoteunsupported(bot: "BaseBot", user: "User",
                                       args: list) -> None:
    await _w(bot, user.id, "Command removed. Catalog is hardcoded from verified list.")


async def handle_playeremotes(bot: "BaseBot", user: "User",
                               args: list) -> None:
    """Deprecated — redirects to !emote list."""
    await _handle_emote_list(bot, user.id)


async def handle_unresolvedplayeremotes(bot: "BaseBot", user: "User",
                                         _args: list) -> None:
    """Deprecated — unresolved set replaced by explicit aliases."""
    await _w(bot, user.id,
             "Unresolved list removed. All player emotes now use explicit alias mapping.")


async def handle_playeremoteid(bot: "BaseBot", user: "User",
                                args: list) -> None:
    """Deprecated — use !emoteid instead."""
    await handle_emoteid(bot, user, args)
