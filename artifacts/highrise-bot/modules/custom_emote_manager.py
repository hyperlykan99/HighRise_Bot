"""modules/custom_emote_manager.py
----------------------------------
Runtime custom emote registry.  Persisted to data/custom_emotes.json.

Provides:
  - Add / remove bot and player emotes at runtime (admin commands).
  - Merged sorted lookup helpers used by emote_system.py.
  - !missingtimings  !addbotemote  !addplayeremote
    !removebotemote  !removeplayeremote  !customemotes

Rules:
  - Bad JSON must never crash bots (safe try/except everywhere).
  - Custom emotes merge with hardcoded emotes at runtime for lookups + lists.
  - Custom timing, when provided, is registered into TIMED_EMOTES_BY_ID so
    all existing get_emote_time() calls pick it up automatically.
  - No auto-prefix.  Exact raw IDs only.
"""
from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot, User

from modules.admin_cmds import is_admin, is_owner

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE       = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR   = os.path.join(_HERE, "..", "data")
_JSON_PATH  = os.path.join(_DATA_DIR, "custom_emotes.json")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _is_admin(username: str) -> bool:
    return is_admin(username) or is_owner(username)


async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception as exc:
        print(f"[CUSTOM_EMOTE WHISPER ERR] {exc}")


# ---------------------------------------------------------------------------
# In-memory store  { norm_name: {"id": str, "time": float} }
# ---------------------------------------------------------------------------
_BOT:     dict[str, dict]  = {}   # custom bot emotes
_PLAYER:  dict[str, dict]  = {}   # custom player emotes
_TIMINGS: dict[str, float] = {}   # !setemotetime overrides  {raw_id: seconds}


def _register_timing(eid: str, t: float) -> None:
    """Write timing to the central registry (single source of truth).

    ALL timing reads use data.emote_registry exclusively.
    _TIMINGS is kept in sync so _save() can persist it to custom_emotes.json
    for the migration path, but it is never read by get_emote_time().
    """
    if t <= 0:
        return
    # Write to registry — this is what every loop, !timingaudit, !emoteinfo,
    # and !emotetime now reads.
    try:
        from data import emote_registry as _reg
        aliases = _reg.aliases_for_id(str(eid))
        if aliases:
            for alias in aliases:
                _reg.set_field(alias, "time", float(t))
    except Exception:
        pass
    # Keep _TIMINGS in sync so _save() can persist it to custom_emotes.json.
    _TIMINGS[str(eid)] = float(t)


def _load() -> None:
    """Load custom_emotes.json into _BOT / _PLAYER / _TIMINGS, registering timings."""
    global _BOT, _PLAYER, _TIMINGS
    try:
        with open(_JSON_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        raw = {}
    except Exception as exc:
        print(f"[CUSTOM_EMOTE] load failed: {exc}")
        raw = {}

    _BOT.clear()
    _PLAYER.clear()
    _TIMINGS.clear()

    for name, info in raw.get("bot_emotes", {}).items():
        try:
            eid = str(info["id"])
            t   = float(info.get("time") or 5.0)
            _BOT[_norm(name)] = {"id": eid, "time": t, "display": name}
            _register_timing(eid, t)
        except Exception:
            pass

    for name, info in raw.get("player_emotes", {}).items():
        try:
            eid = str(info["id"])
            t   = float(info.get("time") or 5.0)
            _PLAYER[_norm(name)] = {"id": eid, "time": t, "display": name}
            _register_timing(eid, t)
        except Exception:
            pass

    # Load !setemotetime overrides last — they take highest priority.
    for eid, t in raw.get("timings", {}).items():
        try:
            t = float(t)
            if t > 0:
                _TIMINGS[str(eid)] = t
                _register_timing(str(eid), t)   # overwrites any lower-priority entry
        except Exception:
            pass


def _save() -> None:
    """Persist _BOT / _PLAYER / _TIMINGS to custom_emotes.json, then rebuild."""
    try:
        bot_out = {v["display"]: {"id": v["id"], "time": v["time"]}
                   for v in _BOT.values()}
        ply_out = {v["display"]: {"id": v["id"], "time": v["time"]}
                   for v in _PLAYER.values()}
        payload = {
            "bot_emotes":    bot_out,
            "player_emotes": ply_out,
            "timings":       dict(_TIMINGS),
        }
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_JSON_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
    except Exception as exc:
        print(f"[CUSTOM_EMOTE] save failed: {exc}")
    # Rebuild emote_system merged dicts immediately — no restart needed.
    try:
        import modules.emote_system as _es
        _es.reload_custom_emotes()
    except Exception as _rel_exc:
        print(f"[CUSTOM_EMOTE] reload trigger failed: {_rel_exc}")


# Load on import (safe — any error is printed, not raised)
_load()

# ---------------------------------------------------------------------------
# Public lookup helpers (used by emote_system.py)
# ---------------------------------------------------------------------------

def lookup_custom_bot(name: str) -> str | None:
    """Return emote_id for a custom bot emote by display name, or None."""
    entry = _BOT.get(_norm(name))
    return entry["id"] if entry else None


def lookup_custom_player(name: str) -> str | None:
    """Return emote_id for a custom player emote by display name, or None."""
    entry = _PLAYER.get(_norm(name))
    return entry["id"] if entry else None


def get_merged_bot_names() -> list[str]:
    """Sorted display names: hardcoded BOT_SELF_EMOTES + custom bot emotes."""
    try:
        from data.hardcoded_emotes import BOT_SELF_EMOTES
        hardcoded = [d for d, _ in BOT_SELF_EMOTES]
    except Exception:
        hardcoded = []
    custom   = [v["display"] for v in _BOT.values()]
    combined = sorted(set(hardcoded) | set(custom), key=lambda s: s.lower())
    return combined


def get_merged_player_names() -> list[str]:
    """Sorted trigger names: hardcoded PLAYER_EMOTES keys + custom player emote keys."""
    try:
        from data.hardcoded_emotes import PLAYER_EMOTES
        hardcoded = list(PLAYER_EMOTES.keys())
    except Exception:
        hardcoded = []
    custom   = [_norm(v["display"]) for v in _PLAYER.values()]
    combined = sorted(set(hardcoded) | set(custom))
    return combined


# ---------------------------------------------------------------------------
# !missingtimings
# ---------------------------------------------------------------------------

async def handle_missingtimings(bot: "BaseBot", user: "User",
                                 _args: list) -> None:
    """!missingtimings — show emote IDs with no known timing (admin only)."""
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "Admin only.")
        return

    try:
        from data.emote_timings import TIMED_EMOTES_BY_ID
    except Exception:
        TIMED_EMOTES_BY_ID = {}

    try:
        from data.hardcoded_emotes import BOT_SELF_EMOTES, PLAYER_EMOTES
    except Exception:
        BOT_SELF_EMOTES = []
        PLAYER_EMOTES   = {}

    seen:    set[str] = set()
    missing: list[str] = []

    for _d, eid in BOT_SELF_EMOTES:
        if eid not in seen:
            seen.add(eid)
            if eid not in TIMED_EMOTES_BY_ID:
                missing.append(eid)

    for eid in PLAYER_EMOTES.values():
        if eid not in seen:
            seen.add(eid)
            if eid not in TIMED_EMOTES_BY_ID:
                missing.append(eid)

    missing.sort()

    if not missing:
        await _w(bot, uid, "✅ All emotes have timing data.")
        return

    await _w(bot, uid,
             f"⏱ Missing timings ({len(missing)} emotes) — using 5 s fallback:")

    chunk_size = 8
    for i in range(0, len(missing), chunk_size):
        chunk = missing[i: i + chunk_size]
        await _w(bot, uid, "  " + "  ".join(chunk))
        import asyncio
        await asyncio.sleep(0.3)


# ---------------------------------------------------------------------------
# !addbotemote <name> <raw_id> [time]
# ---------------------------------------------------------------------------

async def handle_addbotemote(bot: "BaseBot", user: "User",
                              args: list) -> None:
    """!addbotemote <name> <raw_id> [time] — add a custom bot emote (admin)."""
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "Admin only.")
        return
    if len(args) < 3:
        await _w(bot, uid,
                 "Usage: !addbotemote <name> <raw_id> [time]  "
                 "Example: !addbotemote justvibing emote-vibing 5")
        return

    name   = args[1]
    raw_id = args[2]
    try:
        t = float(args[3]) if len(args) >= 4 else 5.0
    except ValueError:
        t = 5.0
    if t <= 0:
        t = 5.0

    key = _norm(name)
    _BOT[key] = {"id": raw_id, "time": t, "display": name}
    _register_timing(raw_id, t)
    _save()
    await _w(bot, uid,
             f"✅ Bot emote added: '{name}' → {raw_id} ({t}s loop)")


# ---------------------------------------------------------------------------
# !addplayeremote <name> <raw_id> [time]
# ---------------------------------------------------------------------------

async def handle_addplayeremote(bot: "BaseBot", user: "User",
                                 args: list) -> None:
    """!addplayeremote <name> <raw_id> [time] — add a custom player emote (admin)."""
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "Admin only.")
        return
    if len(args) < 3:
        await _w(bot, uid,
                 "Usage: !addplayeremote <name> <raw_id> [time]  "
                 "Example: !addplayeremote bloom emote-bloom-pose 5")
        return

    name   = args[1]
    raw_id = args[2]
    try:
        t = float(args[3]) if len(args) >= 4 else 5.0
    except ValueError:
        t = 5.0
    if t <= 0:
        t = 5.0

    key = _norm(name)
    _PLAYER[key] = {"id": raw_id, "time": t, "display": name}
    _register_timing(raw_id, t)
    _save()
    await _w(bot, uid,
             f"✅ Player emote added: '{name}' → {raw_id} ({t}s loop)")


# ---------------------------------------------------------------------------
# !removebotemote <name>
# ---------------------------------------------------------------------------

async def handle_removebotemote(bot: "BaseBot", user: "User",
                                 args: list) -> None:
    """!removebotemote <name> — remove a custom bot emote (admin)."""
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "Admin only.")
        return
    if len(args) < 2:
        await _w(bot, uid, "Usage: !removebotemote <name>")
        return

    key = _norm(args[1])
    removed = _BOT.pop(key, None)
    if removed:
        _save()
        await _w(bot, uid, f"✅ Removed custom bot emote '{removed['display']}'.")
    else:
        await _w(bot, uid,
                 f"Not found in custom bot emotes: '{args[1]}'. "
                 "Use !customemotes to see your list.")


# ---------------------------------------------------------------------------
# !removeplayeremote <name>
# ---------------------------------------------------------------------------

async def handle_removeplayeremote(bot: "BaseBot", user: "User",
                                    args: list) -> None:
    """!removeplayeremote <name> — remove a custom player emote (admin)."""
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "Admin only.")
        return
    if len(args) < 2:
        await _w(bot, uid, "Usage: !removeplayeremote <name>")
        return

    key = _norm(args[1])
    removed = _PLAYER.pop(key, None)
    if removed:
        _save()
        await _w(bot, uid,
                 f"✅ Removed custom player emote '{removed['display']}'.")
    else:
        await _w(bot, uid,
                 f"Not found in custom player emotes: '{args[1]}'. "
                 "Use !customemotes to see your list.")


# ---------------------------------------------------------------------------
# !customemotes
# ---------------------------------------------------------------------------

async def handle_customemotes(bot: "BaseBot", user: "User",
                               _args: list) -> None:
    """!customemotes — show all custom bot and player emotes."""
    uid = user.id

    if not _BOT and not _PLAYER:
        await _w(bot, uid,
                 "No custom emotes yet. "
                 "Use !addbotemote or !addplayeremote to add some.")
        return

    if _BOT:
        lines = [f"🤖 Custom bot emotes ({len(_BOT)}):"]
        for v in sorted(_BOT.values(), key=lambda x: x["display"].lower()):
            lines.append(f"  {v['display']} → {v['id']} ({v['time']}s)")
        msg = "\n".join(lines)[:249]
        await _w(bot, uid, msg)

    import asyncio
    if _BOT and _PLAYER:
        await asyncio.sleep(0.3)

    if _PLAYER:
        lines = [f"🎭 Custom player emotes ({len(_PLAYER)}):"]
        for v in sorted(_PLAYER.values(), key=lambda x: x["display"].lower()):
            lines.append(f"  {v['display']} → {v['id']} ({v['time']}s)")
        msg = "\n".join(lines)[:249]
        await _w(bot, uid, msg)


# ---------------------------------------------------------------------------
# Timing overrides
# ---------------------------------------------------------------------------

def _resolve_emote_id(token: str) -> str | None:
    """Return raw emote ID from a name/alias or a raw ID string.

    Tries (in order):
      1. ALL_BOT_EMOTES  (normalised name → id)
      2. ALL_PLAYER_EMOTES (normalised name → id)
      3. Treat token as a raw ID and return it directly.
    """
    key = _norm(token)
    try:
        from modules.emote_system import ALL_BOT_EMOTES, ALL_PLAYER_EMOTES
        if key in ALL_BOT_EMOTES:
            return ALL_BOT_EMOTES[key]
        if key in ALL_PLAYER_EMOTES:
            return ALL_PLAYER_EMOTES[key]
    except Exception:
        pass
    # Treat as raw ID (e.g. "emote-jewelrise-vibing")
    return token if token else None


async def handle_setemotetime(bot: "BaseBot", user: "User",
                               args: list) -> None:
    """!setemotetime <raw_id_or_name> <seconds>  — admin only.

    Sets a persistent custom timing for any emote.  Takes highest priority
    over all other timing sources.  Effective immediately, no restart needed.

    NOTE: `args` here includes the command word at args[0] (project convention,
    same as handle_addbotemote etc.) — so we read args[1] and args[2].
    """
    uid = user.id
    if not _is_admin(user.username):
        await _w(bot, uid, "❌ Admin only.")
        return

    # Strip command word — real args start at index 1.
    if len(args) < 3:
        await _w(bot, uid,
                 "Usage: !setemotetime <raw_id_or_name> <seconds>  "
                 "(e.g. !setemotetime emote-jewelrise-vibing 20)")
        return

    raw_token = args[1]
    try:
        seconds = float(args[2])
    except (ValueError, TypeError):
        await _w(bot, uid,
                 "Usage: !setemotetime <raw_id_or_name> <seconds>  "
                 f"(got non-number: {args[2]!r})")
        return
    if seconds <= 0:
        await _w(bot, uid, "❌ Seconds must be > 0.")
        return

    eid = _resolve_emote_id(raw_token)
    if not eid:
        await _w(bot, uid, "❌ Could not resolve emote.")
        return

    # Safety: never accept the command word itself as the emote ID.
    if eid.lower() in {"setemotetime", "emotetime"}:
        await _w(bot, uid, "❌ Could not resolve emote (got command word).")
        return

    _TIMINGS[eid] = seconds
    _register_timing(eid, seconds)  # immediately hot in TIMED_EMOTES_BY_ID
    _save()                          # persists + calls reload_custom_emotes()
    await _w(bot, uid, f"✅ Timing set: {eid} = {seconds}s")


async def handle_emotetime(bot: "BaseBot", user: "User",
                            args: list) -> None:
    """!emotetime <raw_id_or_name>  — show current timing and its source.

    `args[0]` is the command word; real argument is args[1].
    """
    uid = user.id
    if len(args) < 2:
        await _w(bot, uid, "Usage: !emotetime <raw_id_or_name>")
        return

    eid = _resolve_emote_id(args[1])
    if not eid:
        await _w(bot, uid, "❌ Could not resolve emote.")
        return

    # Priority 1 — custom timings (raw ID, set by !setemotetime / !addbotemote etc.)
    if eid in _TIMINGS:
        await _w(bot, uid, f"{eid} = {_TIMINGS[eid]}s source=custom")
        return

    # Priority 2 — built-in catalog
    try:
        from data.emote_timings import TIMED_EMOTES_BY_ID
        if eid in TIMED_EMOTES_BY_ID:
            await _w(bot, uid,
                     f"{eid} = {TIMED_EMOTES_BY_ID[eid]}s source=built-in")
            return
    except Exception:
        pass

    # Priority 3 — fallback
    await _w(bot, uid, f"{eid} = 5.0s source=fallback")
