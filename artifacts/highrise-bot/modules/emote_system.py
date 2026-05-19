"""
modules/emote_system.py
-----------------------
Full player emote system for ChillTopia / DJ_DUDU:

  Plain-text trigger  — typing "dance" (no !) starts a looping emote for that player
  Plain-text "stop"   — cancels the player's active emote loop
  !emotes             — auto-sends every page with a short delay (no manual pagination)
  !botemote  <emote>  — admin: loop an emote on this bot (DB-persisted, survives restart)
  !stopbotemote       — admin: stop this bot's emote (optional: !stopbotemote <botname>)
  !punch @user        — attacker gets punch emote, target gets reaction emote; 10 s CD
  !swordfight @user   — both players get swordfight emote simultaneously; 10 s CD
"""
from __future__ import annotations

import asyncio
import os
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
    # Extended aliases
    "thewave":        "emote-wave",
    "gangnam":        "emote-gangnam",
    "gangnamstyle":   "emote-gangnam",
    "heartfingers":   "emote-heartfingers",
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


# ── Load timed_emotes catalog ─────────────────────────────────────────────────
# Merges data/timed_emotes.py into EMOTE_REGISTRY and builds:
#   _EMOTE_DURATIONS  — emote_id → loop re-send interval in seconds
#   _ONE_SHOT_EMOTES  — emote IDs with time=0 (play once, no loop)
_EMOTE_DURATIONS: dict[str, float] = {}
_ONE_SHOT_EMOTES: set[str] = set()

def _load_timed_emotes() -> None:
    import importlib.util as _iutil
    _path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "data", "timed_emotes.py")
    )
    try:
        spec = _iutil.spec_from_file_location("_hr_timed_data", _path)
        mod  = _iutil.module_from_spec(spec)   # type: ignore[arg-type]
        spec.loader.exec_module(mod)           # type: ignore[union-attr]
        for entry in getattr(mod, "timed_emotes", []):
            text  = str(entry.get("text",  "")).strip()
            value = str(entry.get("value", "")).strip()
            t     = entry.get("time")
            if not value:
                continue
            cmd = _normalize(text)
            if cmd and cmd not in EMOTE_REGISTRY:
                EMOTE_REGISTRY[cmd] = value
            if t == 0:
                _ONE_SHOT_EMOTES.add(value)
            else:
                _EMOTE_DURATIONS[value] = float(t) if t is not None else 5.0
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"[EMOTE_SYS] timed_emotes load error: {exc}")

_load_timed_emotes()

# Normalised lookup table (built once at import time, includes timed_emotes entries)
_NORM_MAP: dict[str, str] = {_normalize(k): v for k, v in EMOTE_REGISTRY.items()}

# Also accept any "emote-X" id directly (strip "emote" prefix after normalise)
_EMOTE_PREFIX = "emote"


# ── Free emote catalog (data/timed_free_emotes.py) ───────────────────────────
# Built from timed_free_emotes_list: confirmed working emotes with precise timing.
# Used in the default "free" emote mode.
_FREE_EMOTE_REGISTRY:    dict[str, str]   = {}  # cmd → emote_id
_FREE_EMOTE_DURATIONS:   dict[str, float] = {}  # emote_id → loop interval (s)
_FREE_ONE_SHOT_EMOTES:   set[str]         = set()
_FREE_NORM_MAP:          dict[str, str]   = {}  # normalized_cmd → emote_id
_FREE_PLAYER_EMOTE_NAMES: frozenset[str]  = frozenset()


def _load_free_emotes() -> None:
    """Load data/timed_free_emotes.py and populate free-mode data structures."""
    global _FREE_PLAYER_EMOTE_NAMES
    import importlib.util as _iutil
    _path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "data", "timed_free_emotes.py")
    )
    try:
        spec = _iutil.spec_from_file_location("_hr_free_data", _path)
        mod  = _iutil.module_from_spec(spec)   # type: ignore[arg-type]
        spec.loader.exec_module(mod)           # type: ignore[union-attr]
        for entry in getattr(mod, "timed_free_emotes_list", []):
            text  = str(entry.get("text",  "")).strip()
            value = str(entry.get("value", "")).strip()
            t     = entry.get("time")
            if not value:
                continue
            cmd = _normalize(text)
            if cmd:
                _FREE_EMOTE_REGISTRY[cmd] = value
                _FREE_NORM_MAP[cmd] = value
            if t == 0:
                _FREE_ONE_SHOT_EMOTES.add(value)
            else:
                dur = float(t) if t is not None else 5.0
                _FREE_EMOTE_DURATIONS[value] = dur
                _EMOTE_DURATIONS[value] = dur   # refine main timing dict with precise value
        _FREE_PLAYER_EMOTE_NAMES = frozenset(_FREE_NORM_MAP.keys())
        print(f"[EMOTE_SYS] free catalog loaded: "
              f"{len(_FREE_EMOTE_REGISTRY)} emotes "
              f"({len(_FREE_ONE_SHOT_EMOTES)} one-shot)")
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"[EMOTE_SYS] timed_free_emotes load error: {exc}")


_load_free_emotes()


# ---------------------------------------------------------------------------
# Emote mode — "free" (default) or "all"
# ---------------------------------------------------------------------------
def get_emote_mode() -> str:
    """Return the active emote catalog mode: 'free' (default) or 'all'."""
    return db.get_room_setting("emote_mode", "free")


def get_free_emote_names() -> list[str]:
    """Return sorted command names from the confirmed-free catalog."""
    return sorted(_FREE_EMOTE_REGISTRY.keys())


def lookup_emote(name: str) -> str | None:
    """Return SDK emote-ID for a player-typed name, or None if unknown.

    Resolution order:
      1. active_cmd_map  — unified post-scan map (most authoritative, works for
                           !botemote, !testemote, plain chat triggers, !emotes)
      2. free/all static catalog — fallback before the first scan completes

    In 'free' mode (default): static fallback only resolves timed_free_emotes.
    In 'all' mode: static fallback resolves from EMOTE_REGISTRY + community catalog.
    """
    norm = _normalize(name)

    # 1. Unified active command map — built after every scan
    try:
        from modules.emote_registry import get_active_cmd_map
        cmap = get_active_cmd_map()
        if cmap:
            eid = cmap.get(norm)
            if eid:
                return eid
    except Exception:
        pass

    # 2. Static fallback (pre-scan or empty map)
    if get_emote_mode() == "free":
        result = _FREE_NORM_MAP.get(norm)
        if result:
            return result
        if norm.startswith(_EMOTE_PREFIX):
            stripped = norm[len(_EMOTE_PREFIX):]
            return _FREE_NORM_MAP.get(stripped)
        return None   # strict free mode — no fallthrough to full catalog

    # "all" mode — full static lookup
    if norm in _NORM_MAP:
        return _NORM_MAP[norm]
    if norm.startswith(_EMOTE_PREFIX):
        stripped = norm[len(_EMOTE_PREFIX):]
        if stripped in _NORM_MAP:
            return _NORM_MAP[stripped]
        candidate = f"emote-{stripped}"
        if candidate in set(EMOTE_REGISTRY.values()):
            return candidate
    try:
        from modules.emote_registry import resolve_emote_id
        eid = resolve_emote_id(name)
        if eid:
            return eid
    except Exception:
        pass
    return None


# Frozenset of all valid plain-text trigger names — full catalog (all mode)
PLAYER_EMOTE_NAMES: frozenset[str] = frozenset(_normalize(k) for k in EMOTE_REGISTRY)


def is_plain_emote(text: str) -> bool:
    """True if the chat message matches a known emote name in the active catalog."""
    norm = _normalize(text.strip())

    # Active command map is the single source of truth after scan completes.
    try:
        from modules.emote_registry import get_active_cmd_map
        cmap = get_active_cmd_map()
        if cmap:
            return norm in cmap
    except Exception:
        pass

    # Static fallback (pre-scan)
    if get_emote_mode() == "free":
        return norm in _FREE_PLAYER_EMOTE_NAMES
    return norm in PLAYER_EMOTE_NAMES


async def handle_emotemode(bot: "BaseBot", user: "User", args: list) -> None:
    """!emotemode [free|all] — get or switch the active emote catalog.

    free (default) — only confirmed timed_free_emotes_list emotes are playable
    all            — full experimental catalog (EMOTE_REGISTRY + community)
    """
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        return
    if len(args) < 2:
        mode   = get_emote_mode()
        free_n = len(_FREE_EMOTE_REGISTRY)
        all_n  = len(EMOTE_REGISTRY)
        await _w(bot, uid,
            f"🎭 Emote mode: {mode} | free={free_n} | all={all_n} emotes")
        return
    new_mode = args[1].lower()
    if new_mode not in ("free", "all"):
        await _w(bot, uid, "🎭 Usage: !emotemode free|all")
        return
    db.set_room_setting("emote_mode", new_mode)
    n = len(_FREE_EMOTE_REGISTRY) if new_mode == "free" else len(EMOTE_REGISTRY)
    await _w(bot, uid,
        f"🎭 Emote mode → {new_mode} ({n} emotes active).")
    _log("emote_mode_set", admin=uname, mode=new_mode)


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------
_player_loops:  dict[str, asyncio.Task] = {}   # user_id  → active loop Task
_player_emotes: dict[str, str]          = {}   # user_id  → current emote-ID
_bot_loops:     dict[str, asyncio.Task] = {}   # bot_mode → active loop Task

_DEFAULT_LOOP_INTERVAL = 5  # fallback if emote has no entry in _EMOTE_DURATIONS
_BOT_LOOP_INTERVAL     = 8

# ---------------------------------------------------------------------------
# Cooldowns
# ---------------------------------------------------------------------------
_emote_cd: dict[str, float] = {}
_punch_cd: dict[str, float] = {}
_sword_cd: dict[str, float] = {}
_EMOTE_CD = 3
_PUNCH_CD = 10
_SWORD_CD = 10

# ── Social emotes (require a target player; will silently do nothing in self-mode) ─
_SOCIAL_EMOTES: frozenset[str] = frozenset({
    "emote-hug",        "emote-kiss",       "emote-highfive",
    "emote-handshake",  "emote-fistbump",   "emote-carry",
    "emote-piggyback",  "emote-lean",       "emote-couple",
})

# ── Runtime bot emote diagnostics ────────────────────────────────────────────
# emote_id → {fail_count, silent_count, category, unsupported_for_bots, last_error}
# category: "api_fail" | "silent" | "social" | "player_only"
UNSUPPORTED_BOT_EMOTES: dict[str, dict] = {}
_SILENT_THRESHOLD = 3   # silent OK-but-no-animation before marking player_only

# (bot_uid, emote_id) → asyncio.Event — set by notify_emote_event when on_emote fires
_emote_event_listeners: dict[tuple[str, str], asyncio.Event] = {}


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
    """Send a directed emote at user `uid`; return True on success.

    Player-triggered emotes (plain chat words, !emote) are directed at the user
    who typed them — ownership is required.  Bot self-loops (handle_botemote /
    _start_bot_loop) use send_emote(eid) with NO target so ownership is not needed.
    """
    print(f"[EMOTE TRY] resolved={eid!r} target={uid!r} mode=directed")
    try:
        await bot.highrise.send_emote(eid, uid)
        print(f"[EMOTE OK] resolved={eid!r}")
        return True
    except Exception as exc:
        err = str(exc)
        print(f"[EMOTE FAIL] resolved={eid!r} error={err!r}")
        _log("send_error", emote=eid, user_id=uid, error=err)
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
    """Continuously repeat a directed emote at player `uid` until cancelled."""
    interval = _EMOTE_DURATIONS.get(eid, _DEFAULT_LOOP_INTERVAL)
    _iter = 0
    while True:
        _iter += 1
        try:
            if _iter == 1:
                # Log first iteration only; failures always log.
                print(f"[EMOTE TRY] resolved={eid!r} target={uid!r} mode=directed iter=1")
            await bot.highrise.send_emote(eid, uid)
            if _iter == 1:
                print(f"[EMOTE OK] resolved={eid!r} iter=1")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[EMOTE FAIL] resolved={eid!r} target={uid!r} iter={_iter} error={str(exc)!r}")
        await asyncio.sleep(interval)


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

    if eid in _ONE_SHOT_EMOTES:
        _log("emote_oneshot", user_id=uid, username=uname, emote=eid)
        return  # play once, no loop

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
def _start_bot_loop(bot: "BaseBot", bot_mode: str, eid: str, bot_uid: str) -> float:
    """Start (or restart) a persistent emote loop for this bot process.

    Duration priority:
      1. _FREE_EMOTE_DURATIONS  — precise timing from timed_free_emotes_list
      2. _EMOTE_DURATIONS       — timing from full timed_emotes catalog
      3. _DEFAULT_LOOP_INTERVAL — 5 s fallback

    One-shot emotes (time=0) play once then pause 30 s before re-playing.
    Returns the interval used (useful for status messages).
    """
    old = _bot_loops.pop(bot_mode, None)
    if old and not old.done():
        old.cancel()

    if eid in _FREE_ONE_SHOT_EMOTES or eid in _ONE_SHOT_EMOTES:
        interval: float = 30.0
    else:
        interval = (
            _FREE_EMOTE_DURATIONS.get(eid)
            or _EMOTE_DURATIONS.get(eid)
            or float(_DEFAULT_LOOP_INTERVAL)
        )

    async def _loop() -> None:
        _iter = 0
        while True:
            # Skip silently if runtime diagnostics marked this emote as non-functional.
            if UNSUPPORTED_BOT_EMOTES.get(eid, {}).get("unsupported_for_bots"):
                await asyncio.sleep(interval)
                continue
            _iter += 1
            if _iter == 1 or _iter % 20 == 0:
                # Log first iteration and every 20th to confirm loop is alive.
                print(
                    f"[EMOTE TRY] bot={bot_mode!r} resolved={eid!r}"
                    f" target=self mode=self iter={_iter}"
                )
            try:
                await bot.highrise.send_emote(eid)
                if _iter == 1 or _iter % 20 == 0:
                    print(f"[EMOTE OK] resolved={eid!r} iter={_iter}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                err = str(exc)
                print(f"[EMOTE FAIL] bot={bot_mode!r} resolved={eid!r} iter={_iter} error={err!r}")
                _record_bot_emote_result(eid, "api_fail", err)
            await asyncio.sleep(interval)

    _bot_loops[bot_mode] = asyncio.create_task(_loop())
    _log("bot_loop_start", bot=bot_mode, emote=eid, bot_uid=bot_uid, interval=round(interval, 3))
    return interval


async def handle_botemote(bot: "BaseBot", user: "User", args: list) -> None:
    """!botemote [@<botname>] <emote> — admin: loop a self-emote on a bot (DB-persisted).

    Formats:
      !botemote wave              — loop wave on this bot (no target needed)
      !botemote @DJ_DUDU gangnam  — target by username, starts immediately
      !botemote dj wave           — target by bot mode name (legacy)
    """
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "👑 Admin only.")
        return
    if len(args) < 2:
        await _w(bot, uid, "Usage: !botemote <emote>  or  !botemote @botname <emote>")
        return

    from config import BOT_MODE, BOT_USERNAME
    from modules.gold import get_bot_username as _get_bot_uname

    # Detect target bot: if args[1] (stripped of @) is NOT a valid emote name,
    # treat it as a bot name.  Works for both @DJ_DUDU and dj style.
    raw1 = args[1].lstrip("@").lower()
    if len(args) >= 3 and not lookup_emote(raw1):
        raw_target = raw1
        emote_name = args[2].lstrip("@").lower()
    else:
        raw_target = BOT_MODE.lower()
        emote_name = raw1

    eid = lookup_emote(emote_name)
    if not eid:
        await _w(bot, uid, f"❌ Unknown emote '{emote_name}'. Try !emotes for the list.")
        return

    # Bot self-loops use send_emote(eid) with no target, so only truly
    # permission-locked emotes (fail even without a target) are blocked.
    from modules.emote_registry import is_permission_locked
    if is_permission_locked(eid):
        await _w(bot, uid, "🔒 That emote requires ownership. Bot cannot use it.")
        return

    # Social emotes require a paired target — they silently do nothing in self-mode.
    if eid in _SOCIAL_EMOTES:
        _record_bot_emote_result(eid, "social")
        short = eid.replace("emote-", "")
        await _w(
            bot, uid,
            f"⚠️ '{short}' is a social emote — requires a target player."
            f" Use !emote @user {short} instead."
        )
        return

    # Determine whether the target is THIS running bot instance.
    this_mode  = BOT_MODE.lower()
    this_uname = (_get_bot_uname() or BOT_USERNAME or "").strip().lower()
    is_this_bot = (raw_target == this_mode or
                   bool(this_uname and raw_target == this_uname))

    # Always persist using the BOT_MODE key so startup_bot_emote_recovery can find it.
    store_key = this_mode if is_this_bot else raw_target
    db.set_room_setting(f"bot_emote_{store_key}", eid)
    _log("bot_emote_set", admin=uname, bot=store_key, emote=eid)

    if is_this_bot:
        # Start loop immediately — no reconnect needed.
        bot_uid = get_bot_user_id()
        dur     = _start_bot_loop(bot, BOT_MODE, eid, bot_uid)
        display = f"@{_get_bot_uname() or BOT_MODE}"
        await _w(bot, uid, f"✅ {display} is now looping {eid} (every {dur:.0f}s).")
        return

    await _w(bot, uid, f"✅ Saved. @{raw_target} will loop {eid} on next reconnect.")


async def handle_stopbotemote(bot: "BaseBot", user: "User", args: list) -> None:
    """!stopbotemote [botname] — admin: stop this bot's looping emote.

    Simple format (preferred):
      !stopbotemote            — stops this bot's current emote

    Legacy format (backward compat):
      !stopbotemote dj         — stops the dj bot's looping emote
    """
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "👑 Admin only.")
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
# Runtime diagnostics helpers
# ---------------------------------------------------------------------------

def notify_emote_event(user_id: str, emote_id: str) -> None:
    """Signal that on_emote fired for (user_id, emote_id).

    Called from main.py's on_emote hook.  Releases any !emotediag listener
    waiting for animation confirmation.
    """
    key = (user_id, emote_id)
    evt = _emote_event_listeners.pop(key, None)
    if evt:
        evt.set()


def _record_bot_emote_result(eid: str, category: str, error: str = "") -> None:
    """Track a bot emote failure result.

    After _SILENT_THRESHOLD consecutive silent successes, marks the emote as
    player_only / unsupported so the bot loop auto-skips it.
    """
    entry = UNSUPPORTED_BOT_EMOTES.setdefault(eid, {
        "fail_count": 0, "silent_count": 0,
        "category": category, "unsupported_for_bots": False, "last_error": "",
    })
    if error:
        entry["last_error"] = error
    if category == "silent":
        entry["silent_count"] = entry.get("silent_count", 0) + 1
        entry["category"] = "silent"
        if entry["silent_count"] >= _SILENT_THRESHOLD:
            entry["category"] = "player_only"
            entry["unsupported_for_bots"] = True
            print(
                f"[EMOTE_DIAG] {eid!r} marked player_only after"
                f" {entry['silent_count']} silent failures"
            )
    elif category == "social":
        entry["category"] = "social"
        entry["unsupported_for_bots"] = True
    else:
        entry["fail_count"] = entry.get("fail_count", 0) + 1
        entry["category"] = category


# ---------------------------------------------------------------------------
# !emotediag <name> — live test with 3-second animation detection
# ---------------------------------------------------------------------------

async def handle_emotediag(bot: "BaseBot", user: "User", args: list) -> None:
    """!emotediag <name> — admin: live-test an emote with full diagnostics.

    Steps:
      1. Resolve the name → emote_id
      2. Log [EMOTE TRY]
      3. Call send_emote — log [EMOTE OK] or [EMOTE FAIL]
      4. If OK, wait 3 s for on_emote animation event
      5. If event arrives → confirmed working
      6. If timeout  → SILENT (accepted by SDK, no animation back from backend)
         After _SILENT_THRESHOLD silent hits → classified as PLAYER_ONLY_OR_DISABLED
    """
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "👑 Admin only.")
        return
    if len(args) < 2:
        await _w(bot, uid, "Usage: !emotediag <emote name or id>")
        return

    raw = " ".join(args[1:])
    eid = lookup_emote(raw)
    if not eid:
        await _w(bot, uid, f"❌ Unknown emote '{raw}'. Try !emoteresolve {raw}")
        return

    # Social emote early-out
    if eid in _SOCIAL_EMOTES:
        short = eid.replace("emote-", "")
        await _w(bot, uid,
            f"⚠️ '{short}' is a social emote — needs target player."
            f" Bot cannot self-loop it.")
        return

    bot_uid = get_bot_user_id()

    # Register listener BEFORE sending to eliminate race condition.
    key = (bot_uid, eid) if bot_uid else None
    evt: asyncio.Event | None = None
    if key:
        evt = asyncio.Event()
        _emote_event_listeners[key] = evt

    from config import BOT_MODE as _BM
    print(
        f"[EMOTE TRY] bot={_BM!r} input={raw!r} resolved={eid!r}"
        f" target=self mode=self"
    )
    await _w(bot, uid,
        f"🔬 Testing {eid}\n"
        f"[EMOTE TRY] resolved={eid!r} target=self")

    try:
        await bot.highrise.send_emote(eid)
    except Exception as exc:
        err = str(exc)
        print(f"[EMOTE FAIL] resolved={eid!r} error={err!r}")
        _record_bot_emote_result(eid, "api_fail", err)
        if key:
            _emote_event_listeners.pop(key, None)
        await _w(bot, uid,
            (f"[EMOTE FAIL] resolved={eid!r}\nerror={err}")[:249])
        return

    print(f"[EMOTE OK] resolved={eid!r}")

    # Wait up to 3 s for the on_emote animation confirmation.
    animation_seen = False
    if evt:
        try:
            await asyncio.wait_for(evt.wait(), timeout=3.0)
            animation_seen = True
        except asyncio.TimeoutError:
            _emote_event_listeners.pop(key, None)

    if animation_seen:
        await _w(bot, uid,
            f"✅ [EMOTE OK] {eid!r}\nAnimation event confirmed ✓")
    else:
        _record_bot_emote_result(eid, "silent")
        entry = UNSUPPORTED_BOT_EMOTES.get(eid, {})
        sc    = entry.get("silent_count", 1)
        cat   = entry.get("category", "silent")
        mark  = "🔴 PLAYER_ONLY" if cat == "player_only" else "⚠️ SILENT"
        await _w(bot, uid,
            (
                f"⚠️ SDK accepted but no animation in 3s.\n"
                f"{mark} ({sc}/{_SILENT_THRESHOLD}x)"
                f" — may be player-only or backend-disabled."
            )[:249])


# ---------------------------------------------------------------------------
# !unsupportedemotes — categorised runtime failure report
# ---------------------------------------------------------------------------

async def handle_unsupportedemotes(bot: "BaseBot", user: "User", _args: list) -> None:
    """!unsupportedemotes — admin: show runtime-detected non-functional bot emotes.

    Categories reported:
      social       — paired emotes that need a target (hug, kiss, highfive…)
      player_only  — SDK accepts it but no animation fires (3+ silent hits)
      silent       — SDK accepts it but animation not yet confirmed (< 3 hits)
      api_fail     — SDK raises an exception outright

    Summary line shows total usable vs unsupported counts.
    """
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    if not UNSUPPORTED_BOT_EMOTES:
        await _w(bot, uid,
            "✅ No unsupported emotes recorded yet.\n"
            "Use !emotediag <name> to test individual emotes.")
        return

    api_fail_names: list[str] = []
    silent_names:   list[str] = []
    social_names:   list[str] = []
    player_only_names: list[str] = []

    for eid, info in sorted(UNSUPPORTED_BOT_EMOTES.items()):
        short = eid.replace("emote-", "")
        cat   = info.get("category", "api_fail")
        if cat == "social":
            social_names.append(short)
        elif cat == "player_only":
            player_only_names.append(short)
        elif cat == "silent":
            sc = info.get("silent_count", 0)
            silent_names.append(f"{short}({sc}x)")
        else:
            api_fail_names.append(short)

    total_unsup = sum(
        1 for e in UNSUPPORTED_BOT_EMOTES.values()
        if e.get("unsupported_for_bots")
    )

    lines: list[str] = [
        (
            f"🔬 Bot Emote Diagnostics\n"
            f"Unsupported: {total_unsup}"
            f" | Social: {len(social_names)}"
            f" | Player-only: {len(player_only_names)}"
            f" | Silent: {len(silent_names)}"
            f" | API-fail: {len(api_fail_names)}"
        ),
    ]
    if social_names:
        lines.append(f"🤝 Social (need target): {', '.join(social_names)}")
    if player_only_names:
        lines.append(f"🔴 Player-only/disabled: {', '.join(player_only_names)}")
    if silent_names:
        lines.append(f"⚠️ Silent (unconfirmed): {', '.join(silent_names)}")
    if api_fail_names:
        lines.append(f"❌ API-rejected: {', '.join(api_fail_names)}")

    for line in lines:
        await _w(bot, uid, line[:249])
        await asyncio.sleep(0.3)


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
