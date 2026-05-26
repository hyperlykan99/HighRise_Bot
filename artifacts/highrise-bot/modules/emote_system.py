"""modules/emote_system.py
--------------------------
Clean two-catalog emote system.

BOT_SELF_EMOTES  → !botemote / bot loops  → send_emote(eid)           [NO user_id]
PLAYER_EMOTES    → player chat trigger    → bot emotes toward user     [WITH user_id]

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
from modules.emote_targeting import (
    UPGRADED_ROOM_EMOTE_NOTICE,
    log_emote_command_received,
    resolve_send_emote_target_style,
    send_emote_capabilities,
    send_targeted_emote,
)
from data.hardcoded_emotes import (
    BOT_SELF_EMOTES,
    PLAYER_EMOTES,
    PLAYER_EMOTE_ALIASES,
    lookup_bot_emote,
    lookup_player_emote,
    get_player_trigger_names,
    _norm,
)

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


def get_emote_time(raw_id: str, fallback: float = 5.0) -> float:
    """Registry-only timing — data/emotes.json is the single source of truth.

    Never reads data.emote_timings, _TIMINGS, or TIMED_EMOTES_BY_ID.
    Every loop (player, bot, dancefloor, relay) calls this function so all
    timing paths are identical.
    """
    if _reg is None:
        return float(fallback)
    try:
        return float(_reg.get_emote_time(raw_id, fallback))
    except Exception:
        return float(fallback)


def is_emote_controller_bot() -> bool:
    """True when this subprocess is DJ_DUDU (BOT_MODE=dj).

    DJ_DUDU is the sole owner of all global emote registry commands
    (!emoteinfo, !setemote, !addemote, !removeemote, !exportemotes,
    !emotedebug, !timingaudit, !missingtimings, !findemote, !emotetime,
    !emotes, !stopemote) and all player self-emote triggers (plain chat +
    !emote <name>).  All other bots silently ignore those.
    """
    try:
        from config import BOT_MODE
        return BOT_MODE == "dj"
    except Exception:
        return False


async def _bot_self_present(bot: "BaseBot") -> tuple[bool, str]:
    """Return whether this bot's own avatar is currently visible in the room."""
    try:
        from modules.gold import get_bot_user_id
        bot_uid = get_bot_user_id()
    except Exception:
        bot_uid = ""
    if not bot_uid:
        return False, "no_bot_uid_yet"
    try:
        resp = await bot.highrise.get_room_users()
        pairs = list(resp.content) if hasattr(resp, "content") else []
        for room_user, _pos in pairs:
            if getattr(room_user, "id", "") == bot_uid:
                return True, "present"
        return False, "bot_not_in_room"
    except Exception as exc:
        return False, f"get_room_users_failed:{type(exc).__name__}"


async def _send_bot_self_emote_if_present(
    bot: "BaseBot",
    eid: str,
    *,
    mode: str,
    stage: str,
    iteration: int = 0,
) -> bool:
    present, reason = await _bot_self_present(bot)
    if not present:
        print(
            f"[EMOTE BOT SKIP] mode={mode!r} eid={eid!r} "
            f"stage={stage} iter={iteration} reason={reason}"
        )
        return False
    try:
        await bot.highrise.send_emote(eid)
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        text = repr(exc).lower()
        if "user not in room" in text:
            print(
                f"[EMOTE BOT SKIP] mode={mode!r} eid={eid!r} "
                f"stage={stage} iter={iteration} reason=user_not_in_room"
            )
            return False
        print(
            f"[EMOTE BOT FAIL] mode={mode!r} eid={eid!r} "
            f"stage={stage} iter={iteration} error={exc!r}"
        )
        return False


async def _wait_for_bot_self_presence(
    bot: "BaseBot",
    *,
    mode: str,
    stage: str,
    attempts: int = 8,
) -> bool:
    delays = (2.0, 5.0, 10.0, 20.0, 35.0, 60.0, 90.0, 120.0)
    for attempt in range(1, attempts + 1):
        delay = delays[min(attempt - 1, len(delays) - 1)]
        if delay:
            await asyncio.sleep(delay)
        present, reason = await _bot_self_present(bot)
        print(
            f"[EMOTE BOT PRESENCE] mode={mode!r} stage={stage} "
            f"attempt={attempt} present={present} reason={reason}"
        )
        if present:
            return True
    return False


def reload_emote_registry() -> None:
    """Reload data/emotes.json into memory before a registry read or write.

    Called at the top of every emote registry command handler so that stale
    in-memory state from other bots' writes is never used.
    """
    if _reg is not None:
        try:
            _reg.reload()
        except Exception as exc:
            print(f"[EMOTE] registry reload failed: {exc!r}")


def apply_saved_emote_timings() -> None:
    """Re-apply DB-persisted timing overrides onto the central emote registry.

    Called at bot startup (all modes) so that !setemote <alias> time <N>
    changes survive registry rebuilds triggered by migration, !importemotes,
    !reloademotes, or a corrupt/missing data/emotes.json.

    Each override is written back via set_field so data/emotes.json also stays
    in sync — the registry remains the single runtime source of truth.
    """
    if _reg is None:
        return
    import json as _json_mod
    try:
        _raw = db.get_room_setting("emote_timing_overrides", "{}")
        overrides: dict = _json_mod.loads(_raw) if (_raw or "").strip() else {}
    except Exception:
        return
    if not overrides:
        return
    count = 0
    for alias, seconds in overrides.items():
        try:
            ok = _reg.set_field(alias, "time", float(seconds))
            if ok:
                print(f"[EMOTE_TIMING] Startup: {alias} = {seconds}s")
                count += 1
            else:
                print(f"[EMOTE_TIMING] Startup skip (not in registry): {alias}")
        except Exception as exc:
            print(f"[EMOTE_TIMING] Startup error {alias}: {exc!r}")
    if count:
        print(f"[EMOTE_TIMING] Applied {count} saved timing override(s) from DB.")


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


def _check_timing_conflicts() -> None:
    """Startup guard — CRITICAL warning if any timing store diverges from registry.

    Runs once at import time.  After the fix, _TIMINGS values are always written
    into the registry by _register_timing(), so there should be zero conflicts.
    """
    try:
        from modules import custom_emote_manager as _cem_check
        from data import emote_registry as _r
        conflicts: list[str] = []
        for eid, legacy_t in list(_cem_check._TIMINGS.items()):
            reg_t = _r.get_emote_time(str(eid), fallback=0)
            if reg_t > 0 and abs(float(legacy_t) - float(reg_t)) > 0.001:
                conflicts.append(f"{eid}: _TIMINGS={legacy_t}s registry={reg_t}s")
        if conflicts:
            print(
                f"[EMOTE] CRITICAL DUPLICATE TIMING SYSTEM — "
                f"{len(conflicts)} conflict(s): " + "; ".join(conflicts[:3])
            )
        else:
            print("[EMOTE] Timing unification OK — single source: data/emotes.json")
    except Exception:
        pass


_check_timing_conflicts()


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

# ---------------------------------------------------------------------------
# Group-sync hooks — registered by emote_extras at import time.
# Using a callback avoids circular imports (emote_system never imports extras).
# ---------------------------------------------------------------------------
_group_start_hook: "Callable | None" = None   # async (bot, uid, eid, alias) -> bool
_cancel_loop_hook: "Callable | None" = None   # (uid) -> None


def set_group_start_hook(fn: "Callable") -> None:
    """Register hook called before leader's first send in _start_player_loop."""
    global _group_start_hook
    _group_start_hook = fn


def set_cancel_loop_hook(fn: "Callable") -> None:
    """Register hook called when _cancel_player_loop fires (for group cleanup)."""
    global _cancel_loop_hook
    _cancel_loop_hook = fn


async def _send_player(
    bot: "BaseBot",
    eid: str,
    uid: str,
    *,
    command: str = "player_emote",
    sender_id: str = "",
    sender_username: str = "",
    sender_obj: object | None = None,
) -> bool:
    """send_emote(eid, uid) — player directed."""
    try:
        await send_targeted_emote(
            bot, eid, uid, command=command, sender_id=sender_id or uid,
            sender_username=sender_username, sender_obj=sender_obj)
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
    if _cancel_loop_hook is not None:
        try:
            _cancel_loop_hook(uid)
        except Exception:
            pass


async def _run_player_loop(bot: "BaseBot", uid: str, eid: str) -> None:
    # First emote is sent BEFORE this task is created (_start_player_loop).
    # The loop only handles repeats: read timing from registry → sleep → send.
    # Timing is re-read on EVERY iteration so !setemotetime is live immediately.
    while True:
        sleep_time = get_emote_time(eid)
        print(f"[PLAYER_EMOTE_LOOP] uid={uid} eid={eid} sleep={sleep_time}")
        await asyncio.sleep(sleep_time)
        try:
            await send_targeted_emote(
                bot, eid, uid, command="player_emote_loop", sender_id=uid)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[EMOTE LOOP FAIL] eid={eid!r} uid={uid!r} {exc!r}")


async def _start_player_loop(
    bot: "BaseBot",
    uid: str,
    eid: str,
    display_name: str,
    *,
    username: str = "",
    sender_obj: object | None = None,
    log_event: str = "emote_start",
) -> bool:
    """Perform a bot emote toward a player and avoid claiming avatar control."""
    _cancel_player_loop(uid)
    ok = await _send_player(
        bot, eid, uid, command=display_name, sender_id=uid,
        sender_username=username, sender_obj=sender_obj)
    if not ok:
        await _w(bot, uid, f"Could not perform emote '{display_name}' toward you.")
        return False
    await _w(bot, uid, UPGRADED_ROOM_EMOTE_NOTICE)
    _log(log_event, user_id=uid, username=username, emote=eid, bot_performs=True)
    return True


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
            await _send_bot_self_emote_if_present(
                bot, eid, mode=bot_mode, stage="bot_loop", iteration=_iter)
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
    """Perform a bot emote toward the user for a player-facing emote command."""
    uid = user.id
    if _cd_remaining(_emote_cd, uid, _EMOTE_CD) > 0:
        log_emote_command_received(
            emote_name, user, "emote_system.start_player_emote.cooldown_skip",
            cooldown_seconds=round(_cd_remaining(_emote_cd, uid, _EMOTE_CD), 2),
        )
        return
    eid = ALL_PLAYER_EMOTES.get(_norm(emote_name))
    if not eid:
        log_emote_command_received(
            emote_name, user, "emote_system.start_player_emote.no_match")
        return
    _cd_set(_emote_cd, uid)
    await _start_player_loop(
        bot, uid, eid, emote_name,
        username=user.username, sender_obj=user, log_event="emote_start",
    )


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
    log_emote_command_received(
        " ".join(str(a) for a in args), user, "emote_system.handle_emote_cmd")

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
        # Delegate entirely to the canonical helper — identical to plain-chat path.
        await _start_player_loop(
            bot, uid, eid, sub,
            username=uname, sender_obj=user, log_event="emote_start_cmd",
        )


async def _handle_emote_list(bot: "BaseBot", uid: str) -> None:
    """Auto-send all pages of player emotes — compact, alphabetical, comma-sep."""
    try:
        from modules.emote_extras import _send_compact_pages
        names = sorted(ALL_PLAYER_EMOTES.keys())
        await _send_compact_pages(bot, uid, "🎭 Emotes", names)
    except Exception as exc:
        print(f"[EMOTE_LIST compact fail] {exc!r}")
        # Fallback to legacy stacked output
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
    """!botemotes — all bot emotes, compact, alphabetical, comma-separated."""
    uid   = user.id
    names = _merged_bot_names()
    try:
        from modules.emote_extras import _send_compact_pages
        await _send_compact_pages(bot, uid, "🤖 Bot Emotes", names)
        return
    except Exception as exc:
        print(f"[BOTEMOTES compact fail] {exc!r}")
    # Fallback: legacy stacked
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
            await send_targeted_emote(
                bot, raw_id, target_user.id,
                command="testplayeremote", sender_id=uid,
                sender_username=user.username, sender_obj=user)
            await _w(bot, uid,
                     f"✅ Bot performed {raw_id!r} toward @{target_user.username}")
        except Exception as exc:
            await _w(bot, uid,
                     f"❌ Rejected: {raw_id!r} → {str(exc)}"[:249])
    else:
        raw_id = args[1]
        try:
            await send_targeted_emote(
                bot, raw_id, uid, command="testplayeremote", sender_id=uid,
                sender_username=user.username, sender_obj=user)
            await _w(bot, uid, f"✅ Bot performed {raw_id!r} toward you")
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
        # Persist for restart recovery, then start loop.
        try:
            db.set_room_setting(f"bot_emote_{BOT_MODE.lower()}", eid)
            print(f"[BOT_EMOTE_PERSIST] mode={BOT_MODE.lower()!r} eid={eid!r}")
        except Exception as _pe:
            print(f"[BOT_EMOTE_PERSIST] save failed: {_pe!r}")
        old = _bot_loops.pop(BOT_MODE, None)
        if old and not old.done():
            old.cancel()
        _emote_id = eid  # capture for closure
        _t_imm = get_emote_time(_emote_id)
        print(f"[BOT_LOOP_INTERVAL] alias={emote_name!r} id={_emote_id!r} resolved={_t_imm} source=registry")
        async def _imm_loop() -> None:
            _iter = 0
            while True:
                _iter += 1
                await _send_bot_self_emote_if_present(
                    bot, _emote_id, mode=BOT_MODE, stage="botemote_self", iteration=_iter)
                await asyncio.sleep(get_emote_time(_emote_id))
        _bot_loops[BOT_MODE] = asyncio.create_task(_imm_loop())
        display = f"@{_get_bot_uname() or BOT_MODE}"
        _log("bot_emote_set", admin=uname, bot=BOT_MODE, emote=eid)
        await _w(bot, uid,
                 f"✅ {display} is now looping {emote_name} every {_t_imm}s")
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
            _iter = 0
            while True:
                _iter += 1
                await _send_bot_self_emote_if_present(
                    target_bot, _eid2, mode=raw_target, stage="direct_fallback", iteration=_iter)
                await asyncio.sleep(get_emote_time(_eid2))
        _bot_loops[raw_target] = asyncio.create_task(_direct_loop())
        # Persist using the resolved target mode so recovery can resume it.
        try:
            db.set_room_setting(f"bot_emote_{raw_target}", eid)
            print(f"[BOT_EMOTE_PERSIST] mode={raw_target!r} eid={eid!r}")
        except Exception as _pe:
            print(f"[BOT_EMOTE_PERSIST] save failed: {_pe!r}")
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
        # Reload registry from disk so !setemote changes from DJ_DUDU are
        # visible. Look up timing by alias (most reliable for custom emotes).
        if _reg is not None:
            try:
                _reg.reload()
            except Exception as _rle:
                print(f"[EMOTE BOT] ch-event registry reload failed: {_rle!r}")
        _eid = eid
        # Resolve alias for timing — alias lookup hits _REGISTRY directly.
        _alias_ch = eid
        try:
            if _reg is not None:
                _ch_entry = _reg.get_emote(eid)
                if _ch_entry:
                    _alias_ch = _ch_entry.get("name") or eid
        except Exception:
            pass
        _t_ch = get_emote_time(_eid)
        print(f"[BOT_LOOP_INTERVAL] alias={_alias_ch!r} id={_eid!r} resolved={_t_ch} source=registry")
        async def _ch_loop() -> None:
            _iter = 0
            while True:
                _iter += 1
                await _send_bot_self_emote_if_present(
                    bot, _eid, mode=BOT_MODE, stage="channel_event", iteration=_iter)
                await asyncio.sleep(get_emote_time(_eid))
        _bot_loops[BOT_MODE] = asyncio.create_task(_ch_loop())
        # Persist for restart recovery.
        try:
            db.set_room_setting(f"bot_emote_{BOT_MODE.lower()}", eid)
            print(f"[BOT_EMOTE_PERSIST] mode={BOT_MODE.lower()!r} eid={eid!r}")
        except Exception as _pe:
            print(f"[BOT_EMOTE_PERSIST] save failed: {_pe!r}")
        _log("bot_emote_channel_start", bot=BOT_MODE, emote=eid)
        # Whisper the admin who sent the command — confirmation comes from this bot.
        requester_id = (payload.get("requester_id") or "").strip()
        if requester_id:
            try:
                _own_disp = _get_uname() or BOT_USERNAME or BOT_MODE
                await bot.highrise.send_whisper(
                    requester_id, f"✅ @{_own_disp} is now looping {eid} every {_t_ch}s.")
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
    if not await _wait_for_bot_self_presence(
        bot, mode=BOT_MODE, stage="startup_recovery", attempts=8):
        print(
            f"[EMOTE BOT SKIP] mode={BOT_MODE!r} eid={eid!r} "
            "stage=startup_recovery reason=presence_timeout"
        )
        return
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
    if not _is_admin(user.username):
        await _w(bot, user.id, "Admin only.")
        return

    try:
        import highrise as _highrise  # type: ignore
        sdk_version = getattr(_highrise, "__version__", "unknown")
    except Exception as exc:
        sdk_version = f"unavailable:{type(exc).__name__}"

    style, signature = resolve_send_emote_target_style(bot)
    caps = send_emote_capabilities(bot)
    cap_line = (
        f"target_user_id={caps.get('target_user_id')} "
        f"user_id={caps.get('user_id')} "
        f"receiver_id={caps.get('receiver_id')} "
        f"target={caps.get('target')} "
        f"positional={caps.get('positional_second')}"
    )
    print(
        "[EMOTE_DIAG] "
        f"sdk_version={sdk_version!r} style={style!r} "
        f"signature={signature!r} {cap_line}"
    )

    await _w(bot, user.id, f"EMOTE DIAG sdk={sdk_version} style={style}")
    await _w(bot, user.id, f"send_emote: {signature}")
    await _w(bot, user.id, cap_line)

    if len(args) >= 2 and str(args[1]).lower() in {"test", "send"}:
        emote_id = str(args[2]).strip() if len(args) >= 3 else "emote-wave"
        if not emote_id:
            emote_id = "emote-wave"
        try:
            await send_targeted_emote(
                bot,
                emote_id,
                user.id,
                command="emotediag",
                sender_id=user.id,
                sender_username=user.username,
                sender_obj=user,
            )
            print(
                "[EMOTE_DIAG] test_send "
                f"style={style!r} target={user.id!r} emote={emote_id!r} result='ok'"
            )
            await _w(bot, user.id,
                     "Sent one targeted test. Watch whether you or the bot moves.")
        except Exception as exc:
            print(
                "[EMOTE_DIAG] test_send "
                f"style={style!r} target={user.id!r} emote={emote_id!r} "
                f"result='error' error={exc!r}"
            )
            await _w(bot, user.id,
                     f"Targeted test failed: {type(exc).__name__}: {exc}")


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
    # Persist timing overrides to DB so they survive registry rebuilds on restart
    if field == "time":
        import json as _json_mod
        try:
            _raw_ov = db.get_room_setting("emote_timing_overrides", "{}")
            _ov: dict = _json_mod.loads(_raw_ov) if (_raw_ov or "").strip() else {}
        except Exception:
            _ov = {}
        _ov[alias.strip().lower()] = float(value)
        db.set_room_setting("emote_timing_overrides", _json_mod.dumps(_ov))
        print(f"[SETEMOTE_TIME] Persisted {alias}={float(value)}s to DB")
    try:
        reload_custom_emotes()
    except Exception:
        pass
    if field == "time":
        await _w(bot, uid, f"✅ Updated {alias} time to {float(value)}s permanently.")
    else:
        entry = _reg.get_emote(alias)
        if entry:
            await _w(bot, uid, f"✅ {alias}: {field}={value}")
        else:
            await _w(bot, uid, f"✅ Updated {alias}.{field}")


async def handle_addemote(bot: "BaseBot", user: "User", args: list) -> None:
    """!addemote <alias> <emote_id> <category>"""
    uid = user.id
    if not _is_admin(user.username):
        await _w(bot, uid, "Admin/owner only.")
        return
    if _reg is None:
        await _w(bot, uid, "Emote registry unavailable.")
        return
    if len(args) < 4:
        await _w(bot, uid,
                 "Usage: !addemote <alias> <emote_id> <category>")
        return
    alias    = args[1]
    emote_id = args[2]
    category = args[3]
    # Defaults: 5s duration, playable by both bot and player
    t, b, p = 5.0, True, True
    print(f"[ADD_EMOTE_PARSE] alias={alias!r} id={emote_id!r} category={category!r}")
    ok = _reg.add_emote(alias, emote_id, t, b, p, category)
    if not ok:
        await _w(bot, uid, f"Failed — alias '{alias}' already exists. Use !setemote.")
        return
    try:
        reload_custom_emotes()
    except Exception:
        pass
    await _w(bot, uid,
             f"Added {alias} → {emote_id} cat={category}")


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
        await _w(bot, uid, f"❌ Emote not found: {query}")
        return
    rid     = entry["id"]
    aliases = _reg.aliases_for_id(rid)
    is_bot  = bool(entry.get("bot"))
    is_plyr = bool(entry.get("player"))
    if is_bot and is_plyr:
        etype = "both"
    elif is_bot:
        etype = "bot"
    elif is_plyr:
        etype = "player"
    else:
        etype = "none"
    cat       = entry.get("category") or "uncategorized"
    t_val     = entry["time"]
    t_disp    = int(t_val) if t_val == int(t_val) else t_val
    alias_str = ", ".join(aliases[:3]) + ("…" if len(aliases) > 3 else "")
    await _w(bot, uid,
             f"🎭 Emote: {alias_str}\n"
             f"🆔 ID: {rid}\n"
             f"⏱️ Time: {t_disp}s\n"
             f"📌 Type: {etype}  💾 Source: registry\n"
             f"🗂️ Cat: {cat}  ✅ Exists: yes")


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


async def handle_timingaudit(bot: "BaseBot", user: "User", args: list) -> None:
    """!timingaudit <alias> — verify all timing paths return identical value.

    Reply format:
        registry=20 player_loop=20 bot_loop=20
    All three must be equal after the unification fix.
    """
    uid = user.id
    if len(args) < 2:
        await _w(bot, uid, "Usage: !timingaudit <alias_or_raw_id>")
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

    def _fmt(t: float) -> str:
        return str(int(t)) if t == int(t) else str(t)

    t_registry    = entry["time"]
    t_player_loop = get_emote_time(rid)
    t_bot_loop    = get_emote_time(rid)

    match = "✅" if (t_registry == t_player_loop == t_bot_loop) else "⚠️ MISMATCH"
    await _w(bot, uid,
             f"{match} registry={_fmt(t_registry)} "
             f"player_loop={_fmt(t_player_loop)} "
             f"bot_loop={_fmt(t_bot_loop)} "
             f"(id={rid})")
