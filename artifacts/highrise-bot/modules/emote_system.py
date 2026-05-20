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

# Safe import: real emote durations.  If the file is missing or broken,
# bots still start and fall back to a 5 s loop sleep.
try:
    from data.emote_timings import get_emote_time
except Exception as _eti_exc:
    print("[emote_timings] disabled:", _eti_exc)
    def get_emote_time(emote_id: str, fallback: float = 5.0) -> float:
        return fallback

# Safe import: custom emote manager (legacy — kept for back-compat handlers).
try:
    import modules.custom_emote_manager as _cem
except Exception as _cem_exc:
    print("[custom_emote_manager] disabled:", _cem_exc)
    _cem = None  # type: ignore[assignment]

# Safe import: central emote registry (THE source of truth).
try:
    from data import emote_registry as _reg
except Exception as _reg_exc:
    print("[emote_registry] CRITICAL — disabled:", _reg_exc)
    _reg = None  # type: ignore[assignment]


def _merged_bot_names() -> "list[str]":
    """Sorted display names of all bot-usable emotes (registry-driven)."""
    if _reg is not None:
        try:
            return sorted(
                (e.get("name") or k for k, e in _reg.all_entries().items()
                 if e.get("bot")),
                key=str.lower,
            )
        except Exception:
            pass
    return sorted([d for d, _ in BOT_SELF_EMOTES], key=str.lower)


# ---------------------------------------------------------------------------
# Merged runtime emote dicts — rebuilt by reload_custom_emotes()
# ALL_PLAYER_EMOTES: {norm_alias -> raw_id}  (registry entries where player=True)
# ALL_BOT_EMOTES:    {norm_alias -> raw_id}  (registry entries where bot=True)
# ---------------------------------------------------------------------------
ALL_PLAYER_EMOTES: dict[str, str] = {}
ALL_BOT_EMOTES:    dict[str, str] = {}


def _build_merged_dicts() -> None:
    """Rebuild ALL_PLAYER_EMOTES and ALL_BOT_EMOTES from the central registry."""
    global ALL_PLAYER_EMOTES, ALL_BOT_EMOTES
    p: dict[str, str] = {}
    b: dict[str, str] = {}
    if _reg is not None:
        try:
            for alias, entry in _reg.all_entries().items():
                rid = entry.get("id")
                if not rid:
                    continue
                if entry.get("player"):
                    p[alias] = rid
                if entry.get("bot"):
                    b[alias] = rid
        except Exception as exc:
            print(f"[EMOTE_SYS] registry rebuild failed, falling back: {exc}")
    if not p:
        # Hard fallback if registry is unavailable.
        p = dict(PLAYER_EMOTES)
    if not b:
        b = {_norm(d): eid for d, eid in BOT_SELF_EMOTES}
    ALL_PLAYER_EMOTES = p
    ALL_BOT_EMOTES    = b


_build_merged_dicts()


def reload_custom_emotes() -> None:
    """Rebuild merged dicts from current in-memory custom state.

    Called automatically after !addbotemote / !addplayeremote / !remove* /
    !setemotetime.  Changes become live immediately — no bot restart needed.
    """
    _build_merged_dicts()


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
# Shared registry lives in modules.live_bot_registry — re-exported here for callers.
from modules.live_bot_registry import LIVE_BOTS, get_live_bot, live_bot_keys

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
    # First emote is sent BEFORE this task is created (start_player_emote /
    # handle_emote_cmd both call _send_player first).  The loop only handles
    # the repeats: get timing → sleep → send, forever.
    # Timing is re-read on every iteration so !setemotetime is live immediately.
    while True:
        sleep_time = get_emote_time(eid)
        print(f"[PLAYER_EMOTE_LOOP] uid={uid} eid={eid} sleep={sleep_time}")
        await asyncio.sleep(sleep_time)
        try:
            await bot.highrise.send_emote(eid, uid)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[EMOTE LOOP FAIL] eid={eid!r} uid={uid!r} {exc!r}")


# ---------------------------------------------------------------------------
# Bot self-emote loop (NO user_id)
# ---------------------------------------------------------------------------

def _start_bot_loop(bot: "BaseBot", bot_mode: str, eid: str,
                    bot_uid: str = "") -> float:
    """Start (or restart) a bot self-emote loop — send_emote(eid) only."""
    old = _bot_loops.pop(bot_mode, None)
    if old and not old.done():
        old.cancel()

    # Compute once for logging / return value; loop re-reads dynamically.
    initial_interval = get_emote_time(eid)

    async def _loop() -> None:
        _iter = 0
        while True:
            _iter += 1
            sleep_time = get_emote_time(eid)
            if _iter == 1 or _iter % 20 == 0:
                print(f"[EMOTE BOT] mode={bot_mode!r} eid={eid!r}"
                      f" iter={_iter} sleep={sleep_time}s")
            try:
                await bot.highrise.send_emote(eid)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[EMOTE BOT FAIL] mode={bot_mode!r} eid={eid!r}"
                      f" iter={_iter} error={exc!r}")
            await asyncio.sleep(sleep_time)

    _bot_loops[bot_mode] = asyncio.create_task(_loop())
    _log("bot_loop_start", bot=bot_mode, emote=eid,
         interval=round(initial_interval, 2))
    return initial_interval


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
    return _norm(text.strip()) in ALL_PLAYER_EMOTES


async def start_player_emote(bot: "BaseBot", user: "User",
                              emote_name: str) -> None:
    """Start (or replace) a looping player emote from a plain chat trigger."""
    uid = user.id
    if _cd_remaining(_emote_cd, uid, _EMOTE_CD) > 0:
        return
    eid = ALL_PLAYER_EMOTES.get(_norm(emote_name))
    if not eid:
        return
    _cancel_player_loop(uid)
    _cd_set(_emote_cd, uid)
    ok = await _send_player(bot, eid, uid)
    if not ok:
        return
    interval = get_emote_time(eid)
    if interval <= 0:
        _log("emote_oneshot", user_id=uid, emote=eid)
        return
    await _w(bot, uid, f"✅ Looping {emote_name} every {interval}s")
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
        n = len(ALL_PLAYER_EMOTES)
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
                 f"Player emotes: {len(ALL_PLAYER_EMOTES)} | "
                 f"Bot emotes: {len(ALL_BOT_EMOTES)}")
    else:
        # treat as emote name — search merged dict (hardcoded + custom)
        eid = ALL_PLAYER_EMOTES.get(_norm(sub))
        if not eid:
            await _w(bot, uid,
                     f"Unknown emote '{sub}'. Try !emote list to see all.")
            return
        _cancel_player_loop(uid)
        ok = await _send_player(bot, eid, uid)
        if not ok:
            await _w(bot, uid, f"Could not send emote '{sub}'.")
            return
        interval = get_emote_time(eid)
        if interval <= 0:
            return
        await _w(bot, uid, f"✅ Looping {sub} every {interval}s")
        task = asyncio.create_task(_run_player_loop(bot, uid, eid))
        _player_loops[uid]  = task
        _player_emotes[uid] = eid
        _log("emote_start_cmd", user_id=uid, username=uname, emote=eid)


async def _handle_emote_list(bot: "BaseBot", uid: str) -> None:
    """Auto-send all pages of player emotes (hardcoded + custom), alphabetical."""
    names       = sorted(ALL_PLAYER_EMOTES.keys())
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
    """!botemotes — all bot emotes (hardcoded + custom), alphabetical, auto-paged."""
    uid         = user.id
    names       = _merged_bot_names()
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
    for norm_key, eid in ALL_BOT_EMOTES.items():
        if kw in norm_key or kw in _norm(eid):
            bot_hits.append(f"{norm_key}={eid}")

    player_hits: list[str] = []
    for trigger, eid in ALL_PLAYER_EMOTES.items():
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

    name      = _norm(" ".join(args[1:]))
    bot_eid   = ALL_BOT_EMOTES.get(name)
    plyr_eid  = ALL_PLAYER_EMOTES.get(name)

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

    if len(args) >= 3 and not ALL_BOT_EMOTES.get(_norm(raw1)):
        raw_target = raw1
        emote_name = args[2].lstrip("@").lower()
    else:
        raw_target = BOT_MODE.lower()
        emote_name = raw1

    eid = ALL_BOT_EMOTES.get(_norm(emote_name))
    if not eid:
        await _w(bot, uid,
                 f"Unknown emote '{emote_name}'. Try !botemotes.")
        return

    this_mode  = BOT_MODE.lower()
    this_uname = (_get_bot_uname() or BOT_USERNAME or "").strip().lower()
    is_this_bot = (raw_target == this_mode or
                   bool(this_uname and raw_target == this_uname))

    if is_this_bot:
        # Memory-only: cancel existing loop, start new one at 5s, no DB write.
        old = _bot_loops.pop(BOT_MODE, None)
        if old and not old.done():
            old.cancel()
        _emote_id = eid  # capture for closure
        async def _imm_loop() -> None:
            while True:
                try:
                    await bot.highrise.send_emote(_emote_id)
                except asyncio.CancelledError:
                    raise
                except Exception as _exc:
                    print(f"[EMOTE BOT] loop err mode={BOT_MODE!r} eid={_emote_id!r}: {_exc!r}")
                await asyncio.sleep(get_emote_time(_emote_id))
        _bot_loops[BOT_MODE] = asyncio.create_task(_imm_loop())
        display = f"@{_get_bot_uname() or BOT_MODE}"
        _log("bot_emote_set", admin=uname, bot=BOT_MODE, emote=eid)
        await _w(bot, uid,
                 f"✅ {display} is now looping {emote_name} ({eid})")
        return

    # Not this bot — queue is PRIMARY, direct LIVE_BOTS is fallback if the
    # queue write itself fails.
    _log("bot_emote_set", admin=uname, bot=raw_target, emote=eid)
    await _dispatch_emote_to_other_bot(
        bot, uid, raw_target, eid, emote_name,
    )


async def _dispatch_emote_to_other_bot(bot, uid, raw_target: str,
                                       eid: str, emote_name: str) -> None:
    """Primary: write to bot_command_queue, poll for completion (5 s).

    Fallback (direct LIVE_BOTS) is used ONLY if the queue write itself fails.
    On queue timeout we warn the admin instead — per upgrade spec.
    """
    import json as _json
    cmd_id = None
    try:
        payload_json = _json.dumps({
            "emote_id":   eid,
            "emote_name": emote_name,
            "loop":       True,
        })
        cmd_id = db.enqueue_bot_command(
            target_bot=raw_target, action="botemote",
            payload_json=payload_json, requester_id=str(uid),
        )
    except Exception as exc:
        print(f"[EMOTE] queue write failed ({exc!r}) — using direct fallback.")
        await _direct_emote_fallback(bot, uid, raw_target, eid, emote_name)
        return

    # Target's 1 s poller will pick this up and whisper the admin itself.
    deadline = asyncio.get_event_loop().time() + 5.0
    final_status = "pending"
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.5)
        final_status = db.get_bot_command_status(cmd_id)
        if final_status in ("completed", "error", "unknown_action"):
            break
    if final_status == "completed":
        return
    # Queue is up but target never claimed in time — do NOT fall back, just warn.
    await _w(bot, uid,
             f"⚠️ @{raw_target} did not respond. It may be offline.")


async def _direct_emote_fallback(bot, uid, raw_target: str,
                                 eid: str, emote_name: str) -> None:
    """Same-process LIVE_BOTS direct loop — used only when DB write failed."""
    target_bot = get_live_bot(raw_target)
    if target_bot is not None:
        old = _bot_loops.pop(raw_target, None)
        if old and not old.done():
            old.cancel()
        _eid2 = eid
        async def _direct_loop() -> None:
            while True:
                try:
                    await target_bot.highrise.send_emote(_eid2)
                except asyncio.CancelledError:
                    raise
                except Exception as _exc:
                    print(f"[EMOTE BOT] direct loop err target={raw_target!r}: {_exc!r}")
                await asyncio.sleep(get_emote_time(_eid2))
        _bot_loops[raw_target] = asyncio.create_task(_direct_loop())
        tgt_display = (db.get_bot_username_for_mode(raw_target) or raw_target)
        msg = f"✅ @{tgt_display} is now looping {emote_name} ({eid})"
        await target_bot.highrise.send_whisper(uid, msg[:249])
        return
    await _w(bot, uid,
             f"⚠️ @{raw_target} did not respond. It may be offline.")


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
    from config import BOT_MODE, BOT_USERNAME
    from modules.gold import get_bot_username as _get_bot_uname2
    bot_name     = args[1].lstrip("@").lower() if len(args) >= 2 else BOT_MODE.lower()
    this_mode_l  = BOT_MODE.lower()
    this_uname_l = (_get_bot_uname2() or BOT_USERNAME or "").strip().lower()
    is_this_bot  = (bot_name == this_mode_l or
                    bool(this_uname_l and bot_name == this_uname_l))

    if is_this_bot:
        task = _bot_loops.pop(BOT_MODE, None)
        if task and not task.done():
            task.cancel()
        # Clear restart-recovery key so we don't resume on next start.
        try:
            db.set_room_setting(f"bot_emote_{BOT_MODE.lower()}", "")
        except Exception:
            pass
        _log("bot_emote_cleared", admin=uname, bot=bot_name)
        disp = _get_bot_uname2() or BOT_USERNAME or BOT_MODE
        await _w(bot, uid, f"✅ @{disp} stopped emote loop.")
        return

    # Cross-bot: queue is PRIMARY.  Target replies when it claims the row.
    _log("bot_emote_cleared", admin=uname, bot=bot_name)
    await _dispatch_stop_to_other_bot(bot, uid, bot_name)


def _clear_emote_recovery_keys(raw_target: str) -> None:
    """Clear room_setting recovery key by BOTH raw_target and canonical mode."""
    try:
        db.set_room_setting(f"bot_emote_{raw_target}", "")
    except Exception:
        pass
    try:
        mode = db.get_bot_mode_for_username(raw_target)
        if mode and mode.lower() != raw_target:
            db.set_room_setting(f"bot_emote_{mode.lower()}", "")
    except Exception:
        pass


async def _dispatch_stop_to_other_bot(bot, uid, raw_target: str) -> None:
    import json as _json
    try:
        cmd_id = db.enqueue_bot_command(
            target_bot=raw_target, action="stopbotemote",
            payload_json=_json.dumps({}), requester_id=str(uid),
        )
        _clear_emote_recovery_keys(raw_target)
    except Exception as exc:
        print(f"[EMOTE] stop queue write failed ({exc!r}) — using fallback.")
        await _direct_stop_fallback(bot, uid, raw_target)
        return

    deadline = asyncio.get_event_loop().time() + 5.0
    final_status = "pending"
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.5)
        final_status = db.get_bot_command_status(cmd_id)
        if final_status in ("completed", "error", "unknown_action"):
            break
    if final_status == "completed":
        return
    await _w(bot, uid,
             f"⚠️ @{raw_target} did not respond. It may be offline.")


async def _direct_stop_fallback(bot, uid, raw_target: str) -> None:
    target_bot = get_live_bot(raw_target)
    if target_bot is not None:
        task = _bot_loops.pop(raw_target, None)
        if task and not task.done():
            task.cancel()
        _clear_emote_recovery_keys(raw_target)
        tgt_display = (db.get_bot_username_for_mode(raw_target) or raw_target)
        await target_bot.highrise.send_whisper(
            uid, f"✅ @{tgt_display} stopped emote loop.")
        return
    await _w(bot, uid,
             f"⚠️ @{raw_target} did not respond. It may be offline.")


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

    eid        = raw_id
    emote_name = raw_id  # no alias lookup — name == id

    this_mode  = BOT_MODE.lower()
    this_uname = (_get_bot_uname() or BOT_USERNAME or "").strip().lower()
    is_this_bot = (raw_target == this_mode or
                   bool(this_uname and raw_target == this_uname))

    if is_this_bot:
        # Persist for restart recovery (raw ID always).
        try:
            db.set_room_setting(f"bot_emote_{this_mode}", eid)
        except Exception:
            pass
        dur     = _start_bot_loop(bot, BOT_MODE, eid)
        display = f"@{_get_bot_uname() or BOT_MODE}"
        await _w(bot, uid,
                 f"✅ {display} is now looping {eid} ({eid}) [every {dur:.0f}s]")
        return

    # Cross-bot: queue is PRIMARY (same path as !botemote, no alias lookup).
    _log("bot_emote_set", admin=uname, bot=raw_target, emote=eid)
    await _dispatch_emote_to_other_bot(
        bot, uid, raw_target, eid, emote_name,
    )


# ---------------------------------------------------------------------------
# Startup recovery
# ---------------------------------------------------------------------------

async def handle_livebots(bot: "BaseBot", user: "User", args: list) -> None:
    """!livebots — admin debug: show keys registered in LIVE_BOTS this process."""
    if not _is_admin(user.username):
        await _w(bot, user.id, "Admin only.")
        return
    keys = live_bot_keys()
    if not keys:
        await _w(bot, user.id, "LIVE_BOTS empty in this process.")
        return
    msg = "LIVE_BOTS (this proc): " + ", ".join(keys)
    await _w(bot, user.id, msg[:249])


async def handle_bot_emote_channel_event(bot: "BaseBot", payload: dict) -> None:
    """Called from on_channel when action=bot_emote_start or bot_emote_stop.

    Lets any bot instantly start/stop another bot's emote loop via channel.
    """
    from config import BOT_MODE, BOT_USERNAME
    from modules.gold import get_bot_username as _get_uname
    action       = payload.get("action", "")
    target       = (payload.get("target") or "").strip().lower()
    this_mode_l  = BOT_MODE.lower()
    this_uname_l = (_get_uname() or BOT_USERNAME or "").strip().lower()
    is_me = (target == this_mode_l or
             bool(this_uname_l and target == this_uname_l))
    if not is_me:
        return
    if action == "bot_emote_start":
        eid = (payload.get("emote_id") or "").strip()
        if not eid:
            return
        old = _bot_loops.pop(BOT_MODE, None)
        if old and not old.done():
            old.cancel()
        _eid = eid
        async def _ch_loop() -> None:
            while True:
                try:
                    await bot.highrise.send_emote(_eid)
                except asyncio.CancelledError:
                    raise
                except Exception as _exc:
                    print(f"[EMOTE BOT] ch-loop err mode={BOT_MODE!r} eid={_eid!r}: {_exc!r}")
                await asyncio.sleep(get_emote_time(_eid))
        _bot_loops[BOT_MODE] = asyncio.create_task(_ch_loop())
        _log("bot_emote_channel_start", bot=BOT_MODE, emote=eid)
        # Whisper the admin who sent the command — confirmation comes from this bot.
        requester_id = (payload.get("requester_id") or "").strip()
        if requester_id:
            try:
                _own_disp = _get_uname() or BOT_USERNAME or BOT_MODE
                await bot.highrise.send_whisper(
                    requester_id, f"✅ @{_own_disp} is now looping {eid}.")
            except Exception as _we:
                print(f"[EMOTE BOT] whisper confirmation failed: {_we!r}")
    elif action == "bot_emote_stop":
        task = _bot_loops.pop(BOT_MODE, None)
        if task and not task.done():
            task.cancel()
        _log("bot_emote_channel_stop", bot=BOT_MODE)


async def startup_bot_emote_recovery(bot: "BaseBot") -> None:
    from config import BOT_MODE
    eid = db.get_room_setting(f"bot_emote_{BOT_MODE.lower()}", "")
    if not eid:
        return
    await asyncio.sleep(6)
    _start_bot_loop(bot, BOT_MODE, eid)
    _log("emote_recovery", bot=BOT_MODE, emote=eid)


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
    eid = ALL_PLAYER_EMOTES.get(_norm(emote_name)) or ALL_BOT_EMOTES.get(_norm(emote_name))
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
    eid = ALL_PLAYER_EMOTES.get(_norm(emote_name)) or ALL_BOT_EMOTES.get(_norm(emote_name))
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


# ===========================================================================
# Central registry commands — single source of truth (data/emotes.json)
# ===========================================================================

def _fmt_bool(v: object) -> str:
    return "true" if bool(v) else "false"


def _parse_bool(s: str) -> bool | None:
    s = (s or "").strip().lower()
    if s in ("true", "1", "yes", "on", "t", "y"):
        return True
    if s in ("false", "0", "no", "off", "f", "n"):
        return False
    return None


async def handle_setemote(bot: "BaseBot", user: "User", args: list) -> None:
    """!setemote <alias> <id|name|time|bot|player|category> <value>"""
    uid = user.id
    if not _is_admin(user.username):
        await _w(bot, uid, "Admin/owner only.")
        return
    if _reg is None:
        await _w(bot, uid, "Emote registry unavailable.")
        return
    if len(args) < 4:
        await _w(bot, uid,
                 "Usage: !setemote <alias> <id|name|time|bot|player|category> <value>")
        return
    alias = args[1]
    field = args[2].lower()
    value = " ".join(args[3:])
    if field not in ("id", "name", "time", "bot", "player", "category"):
        await _w(bot, uid, "Field must be: id, name, time, bot, player, category.")
        return
    if field in ("bot", "player"):
        b = _parse_bool(value)
        if b is None:
            await _w(bot, uid, f"!setemote {alias} {field} true|false")
            return
        value = b
    elif field == "time":
        try:
            value = float(value)
        except Exception:
            await _w(bot, uid, "Time must be a positive number (seconds).")
            return
    ok = _reg.set_field(alias, field, value)
    if not ok:
        await _w(bot, uid, f"Failed — alias '{alias}' not found or bad value.")
        return
    try:
        reload_custom_emotes()
    except Exception:
        pass
    entry = _reg.get_emote(alias)
    if entry:
        await _w(bot, uid,
                 f"✅ {alias}: {field}={value}  "
                 f"(id={entry['id']} t={entry['time']}s "
                 f"bot={_fmt_bool(entry['bot'])} player={_fmt_bool(entry['player'])})")
    else:
        await _w(bot, uid, f"✅ Updated {alias}.{field}")


async def handle_addemote(bot: "BaseBot", user: "User", args: list) -> None:
    """!addemote <alias> <raw_id> <time> <bot:true/false> <player:true/false> [category]"""
    uid = user.id
    if not _is_admin(user.username):
        await _w(bot, uid, "Admin/owner only.")
        return
    if _reg is None:
        await _w(bot, uid, "Emote registry unavailable.")
        return
    if len(args) < 6:
        await _w(bot, uid,
                 "Usage: !addemote <alias> <raw_id> <time> <bot> <player> [category]")
        return
    alias  = args[1]
    raw_id = args[2]
    try:
        t = float(args[3])
        if t <= 0:
            raise ValueError
    except Exception:
        await _w(bot, uid, "Time must be a positive number (seconds).")
        return
    b = _parse_bool(args[4])
    p = _parse_bool(args[5])
    if b is None or p is None:
        await _w(bot, uid, "bot and player must be true or false.")
        return
    category = args[6] if len(args) >= 7 else "uncategorized"
    ok = _reg.add_emote(alias, raw_id, t, b, p, category)
    if not ok:
        await _w(bot, uid, f"Failed — alias '{alias}' already exists. Use !setemote.")
        return
    try:
        reload_custom_emotes()
    except Exception:
        pass
    await _w(bot, uid,
             f"✅ Added {alias} → {raw_id} ({t}s bot={_fmt_bool(b)} player={_fmt_bool(p)} cat={category})")


async def handle_removeemote(bot: "BaseBot", user: "User", args: list) -> None:
    """!removeemote <alias>"""
    uid = user.id
    if not _is_admin(user.username):
        await _w(bot, uid, "Admin/owner only.")
        return
    if _reg is None:
        await _w(bot, uid, "Emote registry unavailable.")
        return
    if len(args) < 2:
        await _w(bot, uid, "Usage: !removeemote <alias>")
        return
    alias = args[1]
    ok = _reg.remove_emote(alias)
    if not ok:
        await _w(bot, uid, f"Alias '{alias}' not found.")
        return
    try:
        reload_custom_emotes()
    except Exception:
        pass
    await _w(bot, uid, f"✅ Removed alias '{alias}'.")


async def handle_exportemotes(bot: "BaseBot", user: "User", _args: list) -> None:
    """!exportemotes — whisper summary + count + file location."""
    uid = user.id
    if not _is_admin(user.username):
        await _w(bot, uid, "Admin/owner only.")
        return
    if _reg is None:
        await _w(bot, uid, "Emote registry unavailable.")
        return
    try:
        _reg.save()
        entries = _reg.all_entries()
        n_player = sum(1 for e in entries.values() if e.get("player"))
        n_bot    = sum(1 for e in entries.values() if e.get("bot"))
        await _w(bot, uid,
                 f"📦 {len(entries)} emotes saved → data/emotes.json  "
                 f"(player={n_player} bot={n_bot})")
    except Exception as exc:
        await _w(bot, uid, f"Export failed: {exc}")


async def handle_emotedebug(bot: "BaseBot", user: "User", args: list) -> None:
    """!emotedebug <alias> — show which handler routes it and exact loop time."""
    uid = user.id
    if len(args) < 2:
        await _w(bot, uid, "Usage: !emotedebug <alias>")
        return
    if _reg is None:
        await _w(bot, uid, "Emote registry unavailable.")
        return
    name = args[1]
    entry = _reg.get_emote(name)
    if not entry:
        await _w(bot, uid, f"❌ '{name}' not in registry.")
        return
    rid = entry["id"]
    t   = get_emote_time(rid)
    handler = "emote_system.handle_emote_cmd" if entry.get("player") else "(no player handler)"
    bot_h   = "emote_system.handle_botemote" if entry.get("bot") else "(no bot handler)"
    aliases = _reg.aliases_for_id(rid)
    await _w(bot, uid,
             f"🔍 {entry.get('name', name)} | id={rid} | loop={t}s | "
             f"player→{handler} | bot→{bot_h} | aliases={','.join(aliases)}")


async def handle_emoteinfo(bot: "BaseBot", user: "User", args: list) -> None:
    """!emoteinfo <alias_or_id> — show full registry entry for an emote."""
    uid = user.id
    if len(args) < 2:
        await _w(bot, uid, "Usage: !emoteinfo <alias_or_id>")
        return
    if _reg is None:
        await _w(bot, uid, "Emote registry unavailable.")
        return
    query = args[1]
    entry = _reg.get_emote(query)
    if not entry:
        await _w(bot, uid, f"❌ '{query}' not found in registry.")
        return
    rid     = entry["id"]
    aliases = _reg.aliases_for_id(rid)
    cat     = entry.get("category") or "uncategorized"
    t_disp  = int(entry["time"]) if entry["time"] == int(entry["time"]) else entry["time"]
    await _w(bot, uid,
             f"Registry match: alias={','.join(aliases)} "
             f"id={rid} time={t_disp} "
             f"player={_fmt_bool(entry.get('player'))} bot={_fmt_bool(entry.get('bot'))} "
             f"name={entry.get('name','?')} cat={cat}")


async def handle_emotetime(bot: "BaseBot", user: "User", args: list) -> None:
    """!emotetime <alias_or_id> — show exact timing from data/emotes.json."""
    uid = user.id
    if len(args) < 2:
        await _w(bot, uid, "Usage: !emotetime <alias_or_id>")
        return
    if _reg is None:
        await _w(bot, uid, "Emote registry unavailable.")
        return
    query = args[1]
    entry = _reg.get_emote(query)
    if not entry:
        await _w(bot, uid, f"❌ '{query}' not found in registry.")
        return
    rid = entry["id"]
    t   = get_emote_time(rid)
    await _w(bot, uid, f"⏱ {rid} = {t}s  (source: data/emotes.json)")
