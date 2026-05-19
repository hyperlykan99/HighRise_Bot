"""
modules/emote_scan.py
---------------------
Full bot emote scanning system for ChillTopia / DJ_DUDU.

Commands (DJ bot only):
  !scanallbotemotes          — scan all active emotes on DJ_DUDU
  !pauseemotescan            — pause ongoing scan
  !resumeemotescan           — resume paused or interrupted scan
  !stopemotescan             — stop and reset scan
  !scanprogress              — show scan statistics
  !workingemotes [page]      — paginated list of confirmed working emotes
  !exportworkingemotes       — export Python list of verified emotes

Persistent cache: data/emote_diag_cache.json
Statuses: confirmed_working | sdk_accepted_needs_visual | api_failed
          social_only | manual_unsupported
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
from typing import TYPE_CHECKING

from modules.permissions import is_admin, is_owner

if TYPE_CHECKING:
    from highrise import BaseBot, User

# ---------------------------------------------------------------------------
# Cache persistence
# ---------------------------------------------------------------------------

_BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_PATH = os.path.join(_BASE_DIR, "data", "emote_diag_cache.json")

_CACHE: dict = {"emotes": {}, "scan": {}}

# Public set — all emote IDs with status == "confirmed_working"
VERIFIED_WORKING_BOT_EMOTES: set[str] = set()


def _load_cache() -> None:
    global _CACHE
    if os.path.exists(_CACHE_PATH):
        try:
            with open(_CACHE_PATH, "r", encoding="utf-8") as fh:
                _CACHE = json.load(fh)
        except Exception as exc:
            print(f"[EMOTE_SCAN] Cache load error: {exc}")
            _CACHE = {"emotes": {}, "scan": {}}
    _CACHE.setdefault("emotes", {})
    _CACHE.setdefault("scan", {})
    _rebuild_verified()


def _save_cache() -> None:
    os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
    try:
        tmp = _CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_CACHE, fh, indent=2)
        os.replace(tmp, _CACHE_PATH)
    except Exception as exc:
        print(f"[EMOTE_SCAN] Cache save error: {exc}")


def _rebuild_verified() -> None:
    VERIFIED_WORKING_BOT_EMOTES.clear()
    for eid, info in _CACHE["emotes"].items():
        if info.get("status") == "confirmed_working":
            VERIFIED_WORKING_BOT_EMOTES.add(eid)


def _set_status(eid: str, status: str, error: str = "") -> None:
    entry = _CACHE["emotes"].setdefault(eid, {})
    entry["status"] = status
    entry["tested_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if error:
        entry["error"] = error
    else:
        entry.pop("error", None)
    if status == "confirmed_working":
        VERIFIED_WORKING_BOT_EMOTES.add(eid)
    else:
        VERIFIED_WORKING_BOT_EMOTES.discard(eid)


# Load cache on module import
_load_cache()


# ---------------------------------------------------------------------------
# Public API — called from emote_system.py mark handlers
# ---------------------------------------------------------------------------

def apply_mark(eid: str, category: str) -> None:
    """Apply a manual classification override and persist to disk.

    Called by handle_markemoteworks / handle_markemoteunsupported in
    emote_system.py so that the persistent JSON cache stays in sync with
    the in-memory UNSUPPORTED_BOT_EMOTES dict.
    """
    if category == "manual_works":
        _set_status(eid, "confirmed_working")
    elif category == "manual_unsupported":
        _set_status(eid, "manual_unsupported")
    _save_cache()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_admin_user(uname: str) -> bool:
    return is_admin(uname) or is_owner(uname)


async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


def _get_social_eids() -> frozenset:
    try:
        from modules.emote_system import _SOCIAL_EMOTES
        return _SOCIAL_EMOTES
    except Exception:
        return frozenset()


def _count_statuses(scan_list: list) -> dict:
    counts: dict[str, int] = {
        "confirmed_working":         0,
        "auto_confirmed":            0,   # subset: confirmed by on_emote event
        "sdk_accepted_needs_visual": 0,
        "api_failed":                0,
        "social_only":               0,
        "manual_unsupported":        0,
        "untested":                  0,
    }
    for eid in scan_list:
        info   = _CACHE["emotes"].get(eid, {})
        status = info.get("status", "untested")
        if status in counts:
            counts[status] += 1
        else:
            counts["untested"] += 1
        if status == "confirmed_working" and info.get("auto_confirmed"):
            counts["auto_confirmed"] += 1
    return counts


# ---------------------------------------------------------------------------
# Scan state
# ---------------------------------------------------------------------------

_SCAN_STATE: dict = {
    "running":       False,
    "paused":        False,
    "task":          None,
    "scan_list":     [],
    "current_index": 0,
    "admin_uid":     None,
}
_pause_evt: asyncio.Event = asyncio.Event()
_pause_evt.set()  # starts unpaused


# ---------------------------------------------------------------------------
# Scan loop
# ---------------------------------------------------------------------------

async def _run_scan_loop(bot: "BaseBot") -> None:
    social_eids = _get_social_eids()
    scan_list   = _SCAN_STATE["scan_list"]
    start_idx   = _SCAN_STATE["current_index"]
    total       = len(scan_list)
    admin_uid   = _SCAN_STATE["admin_uid"]

    print(f"[EMOTE_SCAN] Starting at {start_idx}/{total}")

    for i in range(start_idx, total):
        # Stop check
        if not _SCAN_STATE["running"]:
            print("[EMOTE_SCAN] Stopped by user request.")
            break

        # Pause — block until resumed
        if not _pause_evt.is_set():
            print(f"[EMOTE_SCAN] Pausing at {i}/{total} …")
            await _pause_evt.wait()
            if not _SCAN_STATE["running"]:
                break
            print(f"[EMOTE_SCAN] Resumed at {i}/{total}")

        eid = scan_list[i]

        # Save progress
        _SCAN_STATE["current_index"]    = i
        _CACHE["scan"]["current_index"] = i

        # Skip social — mark informational, no test needed
        if eid in social_eids:
            if _CACHE["emotes"].get(eid, {}).get("status") != "social_only":
                _set_status(eid, "social_only")
            continue

        cached_status = _CACHE["emotes"].get(eid, {}).get("status", "")

        # Skip manual_unsupported — respect admin override
        if cached_status == "manual_unsupported":
            continue

        # Skip confirmed_working — preserve prior manual confirmation
        if cached_status == "confirmed_working":
            continue

        # Grab bot UID and pre-register on_emote listener BEFORE sending
        # to eliminate any race between send and the backend echo.
        from modules.gold import get_bot_user_id as _gbuid
        from modules.emote_system import _emote_event_listeners
        bot_uid = _gbuid()
        key     = (bot_uid, eid) if bot_uid else None

        evt: asyncio.Event | None = None
        if key:
            evt = asyncio.Event()
            _emote_event_listeners[key] = evt

        # Test emote (self-emote, no target)
        print(f"[EMOTE_SCAN] ({i+1}/{total}) {eid}")
        try:
            await bot.highrise.send_emote(eid)
        except Exception as exc:
            err = str(exc)
            print(f"[SCAN FAILED] {eid} error={err!r}")
            _set_status(eid, "api_failed", err)
            if key:
                _emote_event_listeners.pop(key, None)
            if i % 5 == 0:
                _save_cache()
            await asyncio.sleep(1.0)
            continue

        # Wait up to 3 s for on_emote confirmation from Highrise backend
        confirmed = False
        if evt:
            try:
                await asyncio.wait_for(evt.wait(), timeout=3.0)
                confirmed = True
            except asyncio.TimeoutError:
                _emote_event_listeners.pop(key, None)

        if confirmed:
            print(f"[SCAN CONFIRMED] {eid}")
            _set_status(eid, "confirmed_working")
            _CACHE["emotes"][eid]["auto_confirmed"] = True
        else:
            print(f"[SCAN SDK_ONLY] {eid}")
            _set_status(eid, "sdk_accepted_needs_visual")

        # Persist every 5 emotes
        if i % 5 == 0:
            _save_cache()

        # Wait 1.5–2.5 s after the 3 s event window (~4.5–5.5 s total per emote)
        await asyncio.sleep(random.uniform(1.5, 2.5))

    # ── Finished (or stopped) ────────────────────────────────────────────
    _SCAN_STATE["running"]          = False
    _SCAN_STATE["current_index"]    = _SCAN_STATE["current_index"]
    _CACHE["scan"]["current_index"] = _SCAN_STATE["current_index"]
    _save_cache()

    finished = _SCAN_STATE["current_index"] >= total - 1
    counts   = _count_statuses(scan_list)
    summary  = (
        f"{'✅ Scan complete!' if finished else '⏹️ Scan stopped.'} "
        f"Auto-confirmed: {counts['auto_confirmed']} "
        f"| SDK-only: {counts['sdk_accepted_needs_visual']} "
        f"| Failed: {counts['api_failed']}"
    )
    print(f"[EMOTE_SCAN] {summary}")
    if admin_uid:
        await _w(bot, admin_uid, summary[:249])


# ---------------------------------------------------------------------------
# !scanallbotemotes
# ---------------------------------------------------------------------------

async def handle_scanallbotemotes(bot: "BaseBot", user: "User", args: list) -> None:
    """!scanallbotemotes — admin: test every active emote on this bot.

    Resumes from saved progress if the emote list is unchanged.
    Use !stopemotescan to reset progress.
    """
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    if _SCAN_STATE["running"]:
        idx   = _SCAN_STATE["current_index"]
        total = len(_SCAN_STATE["scan_list"])
        state = "⏸️ paused" if _SCAN_STATE["paused"] else "▶️ running"
        await _w(bot, uid,
            f"Scan already {state} ({idx}/{total}). "
            f"Use !pauseemotescan / !stopemotescan.")
        return

    from modules.emote_registry import get_active_cmd_map
    scan_list = sorted(set(get_active_cmd_map().values()))

    # Check for resumable saved progress
    saved       = _CACHE.get("scan", {})
    saved_list  = saved.get("scan_list", [])
    saved_index = int(saved.get("current_index", 0))
    resume      = (saved_list == scan_list and 0 < saved_index < len(scan_list))

    if resume:
        _SCAN_STATE["scan_list"]     = scan_list
        _SCAN_STATE["current_index"] = saved_index
        await _w(bot, uid,
            f"▶️ Resuming from {saved_index}/{len(scan_list)}. "
            f"!stopemotescan to reset.")
    else:
        _SCAN_STATE["scan_list"]     = scan_list
        _SCAN_STATE["current_index"] = 0
        est_min = len(scan_list) * 5 // 60
        _CACHE["scan"] = {
            "scan_list":     scan_list,
            "current_index": 0,
            "started_at":    time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        await _w(bot, uid,
            f"🔬 Starting fresh scan: {len(scan_list)} emotes, "
            f"~{est_min} min. Bot will animate each one.")

    _SCAN_STATE["running"]   = True
    _SCAN_STATE["paused"]    = False
    _SCAN_STATE["admin_uid"] = uid
    _pause_evt.set()

    _CACHE["scan"]["scan_list"]     = scan_list
    _CACHE["scan"]["current_index"] = _SCAN_STATE["current_index"]
    _save_cache()

    _SCAN_STATE["task"] = asyncio.create_task(_run_scan_loop(bot))


# ---------------------------------------------------------------------------
# !pauseemotescan
# ---------------------------------------------------------------------------

async def handle_pauseemotescan(bot: "BaseBot", user: "User", _args: list) -> None:
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    if not _SCAN_STATE["running"]:
        await _w(bot, uid, "No scan is running. Use !scanallbotemotes.")
        return
    if _SCAN_STATE["paused"]:
        await _w(bot, uid, "Already paused. Use !resumeemotescan.")
        return

    _SCAN_STATE["paused"] = True
    _pause_evt.clear()
    idx   = _SCAN_STATE["current_index"]
    total = len(_SCAN_STATE["scan_list"])
    await _w(bot, uid, f"⏸️ Paused at {idx}/{total}. Use !resumeemotescan.")


# ---------------------------------------------------------------------------
# !resumeemotescan
# ---------------------------------------------------------------------------

async def handle_resumeemotescan(bot: "BaseBot", user: "User", _args: list) -> None:
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    # Case 1: running but paused
    if _SCAN_STATE["running"] and _SCAN_STATE["paused"]:
        _SCAN_STATE["paused"] = False
        _pause_evt.set()
        idx   = _SCAN_STATE["current_index"]
        total = len(_SCAN_STATE["scan_list"])
        await _w(bot, uid, f"▶️ Resumed from {idx}/{total}.")
        return

    # Case 2: not running — try to restore from saved cache (after bot restart)
    saved       = _CACHE.get("scan", {})
    saved_list  = saved.get("scan_list", [])
    saved_index = int(saved.get("current_index", 0))

    if not saved_list or saved_index >= len(saved_list):
        await _w(bot, uid,
            "No resumable scan found. Use !scanallbotemotes to start.")
        return

    from modules.emote_registry import get_active_cmd_map
    current_list = sorted(set(get_active_cmd_map().values()))
    if saved_list != current_list:
        await _w(bot, uid,
            "⚠️ Emote list changed since last scan. "
            "Run !scanallbotemotes for a fresh scan.")
        return

    _SCAN_STATE["scan_list"]     = saved_list
    _SCAN_STATE["current_index"] = saved_index
    _SCAN_STATE["running"]       = True
    _SCAN_STATE["paused"]        = False
    _SCAN_STATE["admin_uid"]     = uid
    _pause_evt.set()
    _SCAN_STATE["task"] = asyncio.create_task(_run_scan_loop(bot))
    await _w(bot, uid,
        f"▶️ Scan resumed after restart from {saved_index}/{len(saved_list)}.")


# ---------------------------------------------------------------------------
# !stopemotescan
# ---------------------------------------------------------------------------

async def handle_stopemotescan(bot: "BaseBot", user: "User", _args: list) -> None:
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    if not _SCAN_STATE["running"] and not _SCAN_STATE.get("paused"):
        await _w(bot, uid, "No scan is running.")
        return

    _SCAN_STATE["running"] = False
    _SCAN_STATE["paused"]  = False
    _pause_evt.set()  # unblock loop so it can exit

    task = _SCAN_STATE.get("task")
    if task and not task.done():
        task.cancel()
    _SCAN_STATE["task"]             = None
    _SCAN_STATE["current_index"]    = 0
    _CACHE["scan"]["current_index"] = 0
    _save_cache()
    await _w(bot, uid, "⏹️ Scan stopped and progress reset.")


# ---------------------------------------------------------------------------
# !scanprogress
# ---------------------------------------------------------------------------

async def handle_scanprogress(bot: "BaseBot", user: "User", _args: list) -> None:
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    scan_list = (
        _SCAN_STATE.get("scan_list")
        or _CACHE.get("scan", {}).get("scan_list", [])
    )
    total = len(scan_list)
    if total == 0:
        await _w(bot, uid, "No scan data yet. Run !scanallbotemotes first.")
        return

    counts    = _count_statuses(scan_list)
    idx       = (
        _SCAN_STATE["current_index"]
        if _SCAN_STATE["running"]
        else _CACHE.get("scan", {}).get("current_index", 0)
    )
    tested    = sum(v for k, v in counts.items() if k != "untested")
    remaining = max(0, total - tested)

    if _SCAN_STATE["running"] and not _SCAN_STATE["paused"]:
        state_str = "▶️ Running"
    elif _SCAN_STATE["paused"]:
        state_str = "⏸️ Paused"
    else:
        state_str = "⏹️ Stopped"

    line1 = (
        f"🔬 Scan [{state_str}] {idx}/{total}\n"
        f"Tested: {tested} | Remaining: {remaining}"
    )
    line2 = (
        f"🤩 Auto-confirmed: {counts['auto_confirmed']}"
        f" | ⚠️ SDK-only: {counts['sdk_accepted_needs_visual']}"
        f" | ❌ Failed: {counts['api_failed']}"
    )
    line3 = (
        f"✅ Total confirmed: {counts['confirmed_working']}"
        f" | 🤝 Social: {counts['social_only']}"
        f" | 🔴 Manual-bad: {counts['manual_unsupported']}"
        f" | ❓ Untested: {counts['untested']}"
    )
    for line in (line1, line2, line3):
        await _w(bot, uid, line[:249])
        await asyncio.sleep(0.2)


# ---------------------------------------------------------------------------
# !workingemotes [page]
# ---------------------------------------------------------------------------

_PAGE_SIZE = 20


async def handle_workingemotes(bot: "BaseBot", user: "User", args: list) -> None:
    """!workingemotes [page] — show the verified production emote catalog.

    This command shows the manually verified 224-emote list.
    Use !autoconfirmedemotes for scan-confirmed emotes,
    !sdkokemotes for SDK-only, !experimentalemotes for unverified SDK emotes.
    """
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    from data.verified_working_emotes import get_verified_list
    pool = get_verified_list()

    try:
        page = int(args[1]) if len(args) > 1 else 1
    except ValueError:
        page = 1

    total_pages = max(1, (len(pool) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page  = max(1, min(page, total_pages))
    start = (page - 1) * _PAGE_SIZE
    chunk = pool[start : start + _PAGE_SIZE]
    names = [e.replace("emote-", "") for e in chunk]

    await _w(bot, uid,
        (
            f"✅ Verified working emotes (p{page}/{total_pages}, {len(pool)} total):\n"
            f"{', '.join(names)}"
        )[:249])
    if total_pages > 1 and page == 1:
        await _w(bot, uid,
            "Use !workingemotes 2, 3 … for more pages. "
            "!workingcount for totals.")


# ---------------------------------------------------------------------------
# !addworkingemote — add an emote to the verified production list
# ---------------------------------------------------------------------------

async def handle_addworkingemote(bot: "BaseBot", user: "User", args: list) -> None:
    """!addworkingemote <emote> — add an emote to the verified working list."""
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return
    if len(args) < 2:
        await _w(bot, uid, "Usage: !addworkingemote <emote_name>")
        return

    from modules.emote_system import lookup_emote
    from data.verified_working_emotes import add_verified_emote
    name = args[1].lower().strip()
    eid  = lookup_emote(name) or f"emote-{name}"
    ok   = add_verified_emote(eid)
    short = eid.replace("emote-", "")
    if ok:
        await _w(bot, uid,
            f"✅ Added '{short}' to verified working list. "
            f"!botemote now accepts it.")
    else:
        await _w(bot, uid, f"ℹ️ '{short}' is already in the verified working list.")


# ---------------------------------------------------------------------------
# !removeworkingemote — remove an emote from the verified production list
# ---------------------------------------------------------------------------

async def handle_removeworkingemote(bot: "BaseBot", user: "User", args: list) -> None:
    """!removeworkingemote <emote> — remove an emote from the verified working list."""
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return
    if len(args) < 2:
        await _w(bot, uid, "Usage: !removeworkingemote <emote_name>")
        return

    from modules.emote_system import lookup_emote
    from data.verified_working_emotes import remove_verified_emote
    name = args[1].lower().strip()
    eid  = lookup_emote(name) or f"emote-{name}"
    ok   = remove_verified_emote(eid)
    short = eid.replace("emote-", "")
    if ok:
        await _w(bot, uid,
            f"✅ Removed '{short}' from verified list. "
            f"It moves to experimental. Use !addworkingemote to restore.")
    else:
        await _w(bot, uid, f"ℹ️ '{short}' was not in the verified working list.")


# ---------------------------------------------------------------------------
# !workingcount — emote count summary
# ---------------------------------------------------------------------------

async def handle_workingcount(bot: "BaseBot", user: "User", args: list) -> None:
    """!workingcount — show counts for verified, experimental, and failed emotes."""
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    from data.verified_working_emotes import get_verified_set
    verified = get_verified_set()
    experimental = sum(
        1 for eid, info in _CACHE["emotes"].items()
        if info.get("status") == "sdk_accepted_needs_visual"
        and eid not in verified
    )
    failed = sum(
        1 for info in _CACHE["emotes"].values()
        if info.get("status") == "api_failed"
    )
    unsupported = sum(
        1 for info in _CACHE["emotes"].values()
        if info.get("status") == "manual_unsupported"
    )
    await _w(bot, uid,
        (f"📊 Emote counts: ✅ Verified working: {len(verified)} "
         f"| 🔬 Experimental: {experimental}")[:249])
    await _w(bot, uid,
        (f"❌ Failed: {failed} | 🚫 Unsupported: {unsupported} "
         f"| Use !workingemotes to browse verified list.")[:249])


# ---------------------------------------------------------------------------
# !experimentalemotes — SDK-discovered but not in the verified production list
# ---------------------------------------------------------------------------

async def handle_experimentalemotes(bot: "BaseBot", user: "User", args: list) -> None:
    """!experimentalemotes [page] — SDK-discovered emotes NOT in the verified list.

    These sent without API error during !scanallbotemotes but haven't been
    manually verified. Use !addworkingemote <name> to promote to production.
    """
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    from data.verified_working_emotes import get_verified_set
    verified = get_verified_set()
    pool = sorted(
        eid for eid, info in _CACHE["emotes"].items()
        if info.get("status") == "sdk_accepted_needs_visual"
        and eid not in verified
    )
    if not pool:
        await _w(bot, uid,
            "No experimental emotes. Run !scanallbotemotes first, "
            "or all SDK emotes are already in the verified list.")
        return

    try:
        page = int(args[1]) if len(args) > 1 else 1
    except ValueError:
        page = 1

    total_pages = max(1, (len(pool) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page  = max(1, min(page, total_pages))
    start = (page - 1) * _PAGE_SIZE
    chunk = pool[start : start + _PAGE_SIZE]
    names = [e.replace("emote-", "") for e in chunk]

    await _w(bot, uid,
        (
            f"🔬 Experimental emotes (p{page}/{total_pages}, {len(pool)} total):\n"
            f"{', '.join(names)}"
        )[:249])
    if page == 1:
        await _w(bot, uid,
            "SDK-accepted but not verified. "
            "Use !addworkingemote <name> to promote to production.")


# ---------------------------------------------------------------------------
# !autoconfirmedemotes — emotes auto-confirmed by on_emote event during scan
# ---------------------------------------------------------------------------

async def handle_autoconfirmedemotes(bot: "BaseBot", user: "User", args: list) -> None:
    """!autoconfirmedemotes [page] — emotes the scan confirmed via on_emote event."""
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    pool = sorted(
        eid for eid, info in _CACHE["emotes"].items()
        if info.get("status") == "confirmed_working" and info.get("auto_confirmed")
    )
    if not pool:
        await _w(bot, uid,
            "No auto-confirmed emotes yet.\n"
            "Run !scanallbotemotes — on_emote events will auto-confirm them.")
        return

    try:
        page = int(args[1]) if len(args) > 1 else 1
    except ValueError:
        page = 1

    total_pages = max(1, (len(pool) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page  = max(1, min(page, total_pages))
    start = (page - 1) * _PAGE_SIZE
    chunk = pool[start : start + _PAGE_SIZE]
    names = [e.replace("emote-", "") for e in chunk]

    await _w(bot, uid,
        (
            f"🤩 Auto-confirmed by on_emote (p{page}/{total_pages},"
            f" {len(pool)} total):\n"
            f"{', '.join(names)}"
        )[:249])


# ---------------------------------------------------------------------------
# !sdkokemotes — emotes SDK accepted but Highrise sent no on_emote event
# ---------------------------------------------------------------------------

async def handle_sdkokemotes(bot: "BaseBot", user: "User", args: list) -> None:
    """!sdkokemotes [page] — SDK-accepted emotes with no on_emote confirmation.

    These are usable — SDK accepted them without error — but visual
    animation was not auto-confirmed because Highrise did not send an
    on_emote event. Use !markemoteworks <name> if you saw it animate.
    """
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    pool = sorted(
        eid for eid, info in _CACHE["emotes"].items()
        if info.get("status") == "sdk_accepted_needs_visual"
    )
    if not pool:
        await _w(bot, uid,
            "No SDK-only emotes yet. Run !scanallbotemotes first.")
        return

    try:
        page = int(args[1]) if len(args) > 1 else 1
    except ValueError:
        page = 1

    total_pages = max(1, (len(pool) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page  = max(1, min(page, total_pages))
    start = (page - 1) * _PAGE_SIZE
    chunk = pool[start : start + _PAGE_SIZE]
    names = [e.replace("emote-", "") for e in chunk]

    await _w(bot, uid,
        (
            f"⚠️ SDK-accepted/unconfirmed (p{page}/{total_pages},"
            f" {len(pool)} total):\n"
            f"{', '.join(names)}"
        )[:249])
    if page == 1:
        await _w(bot, uid,
            "Usable unless proven broken. "
            "Use !markemoteworks <name> to confirm visually.")


# ---------------------------------------------------------------------------
# !exportworkingemotes
# ---------------------------------------------------------------------------

async def handle_exportworkingemotes(bot: "BaseBot", user: "User", _args: list) -> None:
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    working = sorted(VERIFIED_WORKING_BOT_EMOTES)
    if not working:
        await _w(bot, uid,
            "No confirmed working emotes yet. "
            "Use !markemoteworks <name> first.")
        return

    # Build Python list string lines
    all_lines = (
        ["VERIFIED_WORKING_BOT_EMOTES = ["]
        + [f'    "{e}",' for e in working]
        + ["]"]
    )

    # Group into ~220-char whisper chunks
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for ln in all_lines:
        need = len(ln) + (1 if current else 0)
        if current_len + need > 220 and current:
            chunks.append("\n".join(current))
            current     = [ln]
            current_len = len(ln)
        else:
            current.append(ln)
            current_len += need
    if current:
        chunks.append("\n".join(current))

    total_parts = len(chunks)
    await _w(bot, uid,
        f"📋 Exporting {len(working)} verified emotes ({total_parts} msg):")
    await asyncio.sleep(0.3)

    for i, chunk in enumerate(chunks, 1):
        await _w(bot, uid, f"[{i}/{total_parts}]\n{chunk}")
        await asyncio.sleep(0.4)
