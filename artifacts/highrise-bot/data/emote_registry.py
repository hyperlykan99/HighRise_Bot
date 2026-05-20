"""data/emote_registry.py
==========================
THE single editable source of truth for every emote the bots and players
can use.  Backed by a flat JSON file at data/emotes.json.

Schema (one entry per alias):
    {
      "alias_key": {
        "name":     "Display Name",
        "id":       "raw-emote-id",
        "time":     5.0,
        "bot":      true,
        "player":   true,
        "category": "uncategorized"
      }
    }

Rules:
  * Timing is always keyed by RAW EMOTE ID.  Renaming an alias never changes
    timing.  If two aliases share a raw ID, they share the timing.
  * `bot` and `player` are independent capability flags.  An emote can be
    bot-only, player-only, or both.
  * No alias is required to live under a category — default is "uncategorized".
  * On first import, if data/emotes.json is missing, this module migrates from
    the legacy sources (hardcoded BOT_SELF_EMOTES / PLAYER_EMOTES +
    data/custom_emotes.json) and writes the file.

Public API:
    get_emote(alias_or_id)        -> dict | None
    get_emote_time(raw_id)        -> float
    can_player_use(alias_or_id)   -> bool
    can_bot_use(alias_or_id)      -> bool
    all_entries()                 -> dict[alias, entry]
    player_aliases()              -> list[str]  (sorted, alphabetical)
    bot_aliases()                 -> list[str]  (sorted, alphabetical)
    lookup_id(alias)              -> str | None
    aliases_for_id(raw_id)        -> list[str]

    add_emote(alias, raw_id, time, bot, player, category="uncategorized") -> bool
    remove_emote(alias)           -> bool
    set_field(alias, field, val)  -> bool
    save()
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE      = os.path.dirname(os.path.abspath(__file__))
_JSON_PATH = os.path.join(_HERE, "emotes.json")
_LEGACY_CUSTOM_JSON = os.path.join(_HERE, "custom_emotes.json")

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------
# _REGISTRY:  alias_key  -> entry dict (canonical)
# _BY_ID:     raw_id     -> set[alias_key]   (reverse index, rebuilt on every change)
_REGISTRY: dict[str, dict] = {}
_BY_ID:    dict[str, set[str]] = {}

_VALID_FIELDS = ("name", "id", "time", "bot", "player", "category")
_DEFAULT_TIME = 5.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _norm(s: str) -> str:
    """Lowercase, strip all non-alphanumeric chars."""
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower().replace("\u2019", ""))


def _rebuild_by_id() -> None:
    """Rebuild the raw_id -> alias_keys reverse index."""
    _BY_ID.clear()
    for alias, entry in _REGISTRY.items():
        rid = entry.get("id")
        if rid:
            _BY_ID.setdefault(rid, set()).add(alias)


def _coerce_entry(name: str, raw_id: str, time: float,
                  bot: bool, player: bool, category: str) -> dict:
    return {
        "name":     str(name),
        "id":       str(raw_id),
        "time":     float(time) if time and float(time) > 0 else _DEFAULT_TIME,
        "bot":      bool(bot),
        "player":   bool(player),
        "category": str(category) if category else "uncategorized",
    }


# ---------------------------------------------------------------------------
# Migration from legacy sources
# ---------------------------------------------------------------------------
def _migrate_from_legacy() -> dict[str, dict]:
    """Build the initial registry from BOT_SELF_EMOTES + PLAYER_EMOTES + custom_emotes.json."""
    reg: dict[str, dict] = {}
    by_id: dict[str, str] = {}   # raw_id -> first alias_key (for dedup-by-id)

    # ---- Hardcoded bot emotes ---------------------------------------------
    try:
        from data.hardcoded_emotes import BOT_SELF_EMOTES
    except Exception:
        BOT_SELF_EMOTES = []
    for display, eid in BOT_SELF_EMOTES:
        if not eid:
            continue
        key = _norm(display) or _norm(eid)
        if not key:
            continue
        if key in reg:
            continue
        reg[key] = _coerce_entry(display, eid, _DEFAULT_TIME, True, False, "uncategorized")
        by_id.setdefault(eid, key)

    # ---- Hardcoded player triggers ----------------------------------------
    try:
        from data.hardcoded_emotes import PLAYER_EMOTES
    except Exception:
        PLAYER_EMOTES = {}
    for trigger, eid in PLAYER_EMOTES.items():
        if not eid:
            continue
        key = _norm(trigger)
        if not key:
            continue
        if key in reg:
            # alias exists — flag it player-usable
            reg[key]["player"] = True
            # if the existing entry has a different id, prefer the player one
            if reg[key]["id"] != eid:
                reg[key]["id"] = eid
            continue
        # New alias.  If another alias already owns this raw ID, also mark
        # that entry as player-usable (timing is shared via raw ID anyway).
        existing_alias = by_id.get(eid)
        if existing_alias:
            reg[existing_alias]["player"] = True
        reg[key] = _coerce_entry(trigger, eid, _DEFAULT_TIME, False, True, "uncategorized")
        by_id.setdefault(eid, key)

    # ---- Built-in timings catalog (data/emote_timings.py) -----------------
    try:
        from data.emote_timings import TIMED_EMOTES_BY_ID as _BUILTIN_TIMINGS
    except Exception:
        _BUILTIN_TIMINGS = {}
    for entry in reg.values():
        t = _BUILTIN_TIMINGS.get(entry["id"])
        if t is not None:
            try:
                tv = float(t)
                if tv > 0:
                    entry["time"] = tv
            except Exception:
                pass

    # ---- Legacy custom_emotes.json (preserve user additions + overrides) --
    try:
        with open(_LEGACY_CUSTOM_JSON, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        raw = {}
    except Exception:
        raw = {}

    for name, info in (raw.get("bot_emotes") or {}).items():
        try:
            eid = str(info["id"])
        except Exception:
            continue
        key = _norm(name)
        if not key:
            continue
        t = float(info.get("time") or _DEFAULT_TIME)
        if key in reg:
            reg[key]["bot"] = True
            reg[key]["id"]  = eid
            if t > 0:
                reg[key]["time"] = t
        else:
            reg[key] = _coerce_entry(name, eid, t, True, False, "custom")
            by_id.setdefault(eid, key)

    for name, info in (raw.get("player_emotes") or {}).items():
        try:
            eid = str(info["id"])
        except Exception:
            continue
        key = _norm(name)
        if not key:
            continue
        t = float(info.get("time") or _DEFAULT_TIME)
        if key in reg:
            reg[key]["player"] = True
            reg[key]["id"]     = eid
            if t > 0:
                reg[key]["time"] = t
        else:
            reg[key] = _coerce_entry(name, eid, t, False, True, "custom")
            by_id.setdefault(eid, key)

    # !setemotetime overrides — apply LAST so they win, and apply to ALL
    # aliases sharing that raw ID (timing follows raw ID).
    for eid, t in (raw.get("timings") or {}).items():
        try:
            tv = float(t)
            if tv <= 0:
                continue
        except Exception:
            continue
        eid_s = str(eid)
        for entry in reg.values():
            if entry["id"] == eid_s:
                entry["time"] = tv

    return reg


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------
def _load() -> None:
    """Load data/emotes.json, or migrate from legacy sources if missing."""
    global _REGISTRY
    try:
        with open(_JSON_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        _REGISTRY.clear()
        for alias, entry in raw.items():
            try:
                _REGISTRY[_norm(alias)] = _coerce_entry(
                    entry.get("name") or alias,
                    entry["id"],
                    entry.get("time") or _DEFAULT_TIME,
                    entry.get("bot",    False),
                    entry.get("player", False),
                    entry.get("category", "uncategorized"),
                )
            except Exception:
                continue
        _rebuild_by_id()
        return
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"[EMOTE_REGISTRY] load failed: {exc} — migrating fresh")

    # Fall through: migrate.
    _REGISTRY.clear()
    _REGISTRY.update(_migrate_from_legacy())
    _rebuild_by_id()
    try:
        save()
        print(f"[EMOTE_REGISTRY] migrated {len(_REGISTRY)} emotes -> {_JSON_PATH}")
    except Exception as exc:
        print(f"[EMOTE_REGISTRY] initial save failed: {exc}")


def save() -> None:
    """Persist the registry to data/emotes.json."""
    try:
        os.makedirs(_HERE, exist_ok=True)
        with open(_JSON_PATH, "w", encoding="utf-8") as fh:
            json.dump(_REGISTRY, fh, indent=2, ensure_ascii=False, sort_keys=True)
    except Exception as exc:
        print(f"[EMOTE_REGISTRY] save failed: {exc}")


# Load on import (safe — never raises).
_load()


# ---------------------------------------------------------------------------
# Public lookups
# ---------------------------------------------------------------------------
def get_emote(alias_or_id: str) -> dict | None:
    """Return the entry by alias OR by raw ID.  None if not found."""
    if not alias_or_id:
        return None
    key = _norm(alias_or_id)
    if key in _REGISTRY:
        return _REGISTRY[key]
    # Fallback: lookup by raw ID
    aliases = _BY_ID.get(str(alias_or_id))
    if aliases:
        # Return the alphabetically-first alias's entry (deterministic).
        first = sorted(aliases)[0]
        return _REGISTRY.get(first)
    return None


def get_emote_time(raw_id: str, fallback: float = _DEFAULT_TIME) -> float:
    """Timing by RAW EMOTE ID.  All aliases sharing the ID share the time."""
    if not raw_id:
        return float(fallback)
    aliases = _BY_ID.get(str(raw_id))
    if aliases:
        # All aliases for the same raw ID hold the same time — read any of them.
        any_alias = next(iter(aliases))
        try:
            t = float(_REGISTRY[any_alias].get("time") or 0)
            if t > 0:
                return t
        except Exception:
            pass
    # Also accept alias lookups (best-effort): if caller passed an alias here.
    entry = _REGISTRY.get(_norm(raw_id))
    if entry:
        try:
            t = float(entry.get("time") or 0)
            if t > 0:
                return t
        except Exception:
            pass
    return float(fallback)


def can_player_use(alias_or_id: str) -> bool:
    entry = get_emote(alias_or_id)
    return bool(entry and entry.get("player"))


def can_bot_use(alias_or_id: str) -> bool:
    entry = get_emote(alias_or_id)
    return bool(entry and entry.get("bot"))


def lookup_id(alias: str) -> str | None:
    """Return the raw emote ID for an alias, or None."""
    entry = _REGISTRY.get(_norm(alias))
    return entry["id"] if entry else None


def aliases_for_id(raw_id: str) -> list[str]:
    """Return sorted aliases that map to this raw ID."""
    return sorted(_BY_ID.get(str(raw_id), set()))


def all_entries() -> dict[str, dict]:
    """Return a copy of the full registry (read-only snapshot)."""
    return {k: dict(v) for k, v in _REGISTRY.items()}


def player_aliases() -> list[str]:
    """Alphabetically sorted aliases where player=True."""
    return sorted(k for k, v in _REGISTRY.items() if v.get("player"))


def bot_aliases() -> list[str]:
    """Alphabetically sorted aliases where bot=True."""
    return sorted(k for k, v in _REGISTRY.items() if v.get("bot"))


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------
def add_emote(alias: str, raw_id: str, time: float,
              bot: bool, player: bool,
              category: str = "uncategorized") -> bool:
    """Create a new alias entry.  Returns False if alias already exists."""
    key = _norm(alias)
    if not key or not raw_id:
        return False
    if key in _REGISTRY:
        return False
    _REGISTRY[key] = _coerce_entry(alias, raw_id, time, bot, player, category)
    _rebuild_by_id()
    save()
    return True


def remove_emote(alias: str) -> bool:
    """Delete an alias entry.  Other aliases with the same raw ID are unaffected."""
    key = _norm(alias)
    if key not in _REGISTRY:
        return False
    _REGISTRY.pop(key, None)
    _rebuild_by_id()
    save()
    return True


def set_field(alias: str, field: str, value: Any) -> bool:
    """Update one field on an entry.  Timing edits propagate to all aliases
    sharing the raw ID (timing follows raw ID).  Returns False on bad input."""
    key = _norm(alias)
    if key not in _REGISTRY or field not in _VALID_FIELDS:
        return False
    entry = _REGISTRY[key]

    if field == "time":
        try:
            tv = float(value)
        except Exception:
            return False
        if tv <= 0:
            return False
        # propagate to ALL aliases sharing this raw ID
        rid = entry["id"]
        for a in _BY_ID.get(rid, {key}):
            _REGISTRY[a]["time"] = tv

    elif field == "id":
        new_id = str(value)
        if not new_id:
            return False
        entry["id"] = new_id
        _rebuild_by_id()

    elif field in ("bot", "player"):
        if isinstance(value, str):
            entry[field] = value.strip().lower() in ("true", "1", "yes", "on")
        else:
            entry[field] = bool(value)

    elif field == "name":
        entry["name"] = str(value)

    elif field == "category":
        entry["category"] = str(value) if value else "uncategorized"

    save()
    return True


def reload() -> None:
    """Re-read data/emotes.json from disk (does NOT re-migrate)."""
    _load()
