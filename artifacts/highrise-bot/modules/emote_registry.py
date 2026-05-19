"""
modules/emote_registry.py
--------------------------
Emote discovery, testing, and caching.

Load sources (in priority order):
  1. Highrise SDK       — if it ever exposes a GetEmotesRequest
  2. data/highrise_emotes.json — community catalog (saved by !importemotes)
  3. emotes.json        — optional local override file
  4. Built-in candidate list (~155 entries)

Each candidate is tested by calling send_emote on the bot's own user ID.
Results are cached in the active_emotes DB table.

Admin commands:
  !importemotes     — load community catalog from data/highrise_emotes.json, rescan
  !reloademotes     — reset all to untested and re-scan (uses current candidate set)
  !emotecount       — show how many active emotes are cached
  !emotesource      — show where the current emote catalog came from
  !testemote <name> — test one emote and report result

Player command:
  !emotes           — auto-paginated list of active emotes (≤249 chars each)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import TYPE_CHECKING

import database as db
from modules.permissions import is_admin, is_owner

if TYPE_CHECKING:
    from highrise import BaseBot, User

_LOG      = "[EMOTE_REG]"
_SCAN_LOG = "[EMOTE_SCAN]"

# ─── Scan state (in-process, single dj bot) ──────────────────────────────────
_scan_state: dict = {
    "status":     "idle",   # idle | running | done | failed
    "tested":     0,
    "total":      0,
    "active":     0,
    "alias_only": 0,        # works via alt SDK ID (⚠️)
    "failed":     0,        # permission + invalid + unclassified
    "permission": 0,        # valid ID but bot doesn't own
    "invalid":    0,        # truly bad ID
    "transient":  0,        # rate-limit / timeout left untested
    "last_error": "",
    "started_at": 0.0,
}

# Per-scan failure log: emote_id → {display_name, category, reason}
# Populated on each scan, read by !emotefailures.
_failure_log: dict[str, dict] = {}

# Source breakdown from last _load_candidates() call
_source_counts: dict[str, int] = {"builtin": 0, "community": 0, "local": 0}


async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


def _is_admin(username: str) -> bool:
    return is_admin(username) or is_owner(username)


# ─── Candidate list ───────────────────────────────────────────────────────────
# (display_name, emote_id) — tested on startup; only working ones go to DB.
# Keep alphabetical within categories for readability.
_CANDIDATES: tuple[tuple[str, str], ...] = (
    # ── Confirmed (from existing EMOTE_REGISTRY) ──────────────────────────────
    ("angry",         "emote-angry"),
    ("blowkiss",      "emote-blowkiss"),
    ("bow",           "emote-bow"),
    ("bow2",          "emote-bow2"),
    ("celebrate",     "emote-celebrate"),
    ("clap",          "emote-clap"),
    ("clap2",         "emote-clap2"),
    ("cry",           "emote-cry"),
    ("curtsey",       "emote-curtsey"),
    ("curtsy",        "emote-curtsy"),
    ("dance",         "emote-dance"),
    ("dance2",        "emote-dance2"),
    ("dance3",        "emote-dance3"),
    ("dance4",        "emote-dance4"),
    ("float",         "emote-float"),
    ("flex",          "emote-flex"),
    ("fly",           "emote-fly"),
    ("ghost",         "emote-ghost"),
    ("greet",         "emote-greet"),
    ("handshake",     "emote-handshake"),
    ("headbang",      "emote-headbang"),
    ("headbang2",     "emote-headbang2"),
    ("hello",         "emote-hello"),
    ("highfive",      "emote-highfive"),
    ("hug",           "emote-hug"),
    ("idle",          "emote-idle_loop"),
    ("idlelook",      "emote-idle_look"),
    ("enthusiastic",  "emote-idle_enthusiastic"),
    ("kiss",          "emote-kiss"),
    ("laugh",         "emote-laugh"),
    ("levitate",      "emote-levitate"),
    ("magic",         "emote-magic"),
    ("magic2",        "emote-magic2"),
    ("no",            "emote-no"),
    ("point",         "emote-point"),
    ("pose",          "emote-pose"),
    ("pose2",         "emote-pose2"),
    ("sad",           "emote-sad"),
    ("salute",        "emote-salute"),
    ("shrug",         "emote-shrug"),
    ("sit",           "emote-sit"),
    ("sit2",          "emote-sit2"),
    ("sleep",         "emote-sleep"),
    ("sniff",         "emote-sniff"),
    ("snowangel",     "emote-snowangel"),
    ("snowball",      "emote-snowball"),
    ("sorry",         "emote-sorry"),
    ("spin",          "emote-spin"),
    ("surprise",      "emote-surprise"),
    ("telekinesis",   "emote-telekinesis"),
    ("thumbsdown",    "emote-thumbsdown"),
    ("thumbsup",      "emote-thumbsup"),
    ("wave",          "emote-wave"),
    ("witch",         "emote-witch"),
    ("yes",           "emote-yes"),
    ("zombie",        "emote-zombie"),
    # ── Numbered variants ─────────────────────────────────────────────────────
    ("dance5",        "emote-dance5"),
    ("dance6",        "emote-dance6"),
    ("dance7",        "emote-dance7"),
    ("dance8",        "emote-dance8"),
    ("dance9",        "emote-dance9"),
    ("dance10",       "emote-dance10"),
    ("dance11",       "emote-dance11"),
    ("dance12",       "emote-dance12"),
    ("sit3",          "emote-sit3"),
    ("sit4",          "emote-sit4"),
    ("pose3",         "emote-pose3"),
    ("pose4",         "emote-pose4"),
    ("wave2",         "emote-wave2"),
    ("laugh2",        "emote-laugh2"),
    ("cry2",          "emote-cry2"),
    ("idle2",         "emote-idle2"),
    ("idle3",         "emote-idle3"),
    # ── Reactions / feelings ──────────────────────────────────────────────────
    ("applause",      "emote-applause"),
    ("boo",           "emote-boo"),
    ("cheer",         "emote-cheer"),
    ("confused",      "emote-confused"),
    ("disgusted",     "emote-disgusted"),
    ("excited",       "emote-excited"),
    ("eyeroll",       "emote-eyeroll"),
    ("facepalm",      "emote-facepalm"),
    ("peace",         "emote-peace"),
    ("rock",          "emote-rock"),
    ("scared",        "emote-scared"),
    ("think",         "emote-think"),
    ("wink",          "emote-wink"),
    # ── Activities ────────────────────────────────────────────────────────────
    ("balloon",       "emote-balloon"),
    ("basketball",    "emote-basketball"),
    ("cartwheel",     "emote-cartwheel"),
    ("coffee",        "emote-coffee"),
    ("confetti",      "emote-confetti"),
    ("drink",         "emote-drink"),
    ("drums",         "emote-drums"),
    ("eat",           "emote-eat"),
    ("guitar",        "emote-guitar"),
    ("jump",          "emote-jump"),
    ("meditate",      "emote-meditate"),
    ("phone",         "emote-phone"),
    ("piano",         "emote-piano"),
    ("read",          "emote-read"),
    ("rocket",        "emote-rocket"),
    ("run",           "emote-run"),
    ("selfie",        "emote-selfie"),
    ("skip",          "emote-skip"),
    ("soccer",        "emote-soccer"),
    ("sparkle",       "emote-sparkle"),
    ("stretch",       "emote-stretch"),
    ("tennis",        "emote-tennis"),
    ("type",          "emote-type"),
    ("watergun",      "emote-watergun"),
    ("yoga",          "emote-yoga"),
    # ── Dance styles ──────────────────────────────────────────────────────────
    ("ballet",        "emote-ballet"),
    ("breakdance",    "emote-breakdance"),
    ("disco",         "emote-disco"),
    ("floss",         "emote-floss"),
    ("hiphop",        "emote-hiphop"),
    ("jive",          "emote-jive"),
    ("moonwalk",      "emote-moonwalk"),
    ("robot",         "emote-robot"),
    ("salsa",         "emote-salsa"),
    ("shimmy",        "emote-shimmy"),
    ("shuffle",       "emote-shuffle"),
    ("stomp",         "emote-stomp"),
    ("swing",         "emote-swing"),
    ("tango",         "emote-tango"),
    ("waltz",         "emote-waltz"),
    # ── Seasonal / holiday ────────────────────────────────────────────────────
    ("cupid",         "emote-cupid"),
    ("elf",           "emote-elf"),
    ("firework",      "emote-firework"),
    ("frankenstein",  "emote-frankenstein"),
    ("heart",         "emote-heart"),
    ("heartdance",    "emote-heartdance"),
    ("heartshape",    "emote-heartshape"),
    ("mummy",         "emote-mummy"),
    ("pumpkin",       "emote-pumpkin"),
    ("reindeer",      "emote-reindeer"),
    ("santa",         "emote-santa"),
    ("skeleton",      "emote-skeleton"),
    ("snowflake",     "emote-snowflake"),
    ("vampire",       "emote-vampire"),
    # ── Idle variants ─────────────────────────────────────────────────────────
    ("crouch",        "emote-idle_crouch"),
    ("laydown",       "emote-idle_laydown"),
    ("sitfloor",      "emote-idle_sitfloor"),
    # ── Misc ──────────────────────────────────────────────────────────────────
    ("interact",      "emote-interact"),
    ("look",          "emote-look"),
    ("peek",          "emote-peek"),
    ("smoke",         "emote-smoke"),
    ("wander",        "emote-wander"),
)

# Normalize: "Sword Fight" → "swordfight"
def _norm(s: str) -> str:
    """Normalize to a command key.

    'Snow Angel'       → 'snowangel'
    "Don't Start Now"  → 'dontstartnow'
    'emote-dance'      → 'emotedance'
    """
    s = s.lower().replace("\u2019", "")   # curly apostrophe
    return re.sub(r"[^a-z0-9]", "", s)

# name→id lookup built from candidates (for !testemote by display name)
_CAND_BY_NAME: dict[str, str] = {_norm(dn): eid for dn, eid in _CANDIDATES}
# Also index by emote_id directly (strip "emote-" prefix for matching)
for _dn, _eid in _CANDIDATES:
    _CAND_BY_NAME[_norm(_eid)] = _eid

# ─── Alias map: additional name aliases → emote_id ───────────────────────────
# Players can type any of these names; also used as scanner name resolution.
# Primary _CAND_BY_NAME entries always win on collision.
_ALIAS_MAP: dict[str, str] = {
    # Gangnam / K-pop
    "gangnam":          "emote-gangnam",
    "gangnamstyle":     "emote-gangnam",
    # Dance shortcuts
    "groovy":           "emote-dance3",
    "partydance":       "emote-dance4",
    # Social shortcuts
    "hi":               "emote-wave",
    "bye":              "emote-wave",
    "pray":             "emote-sorry",
    "beg":              "emote-sorry",
    "smooch":           "emote-kiss",
    "muah":             "emote-blowkiss",
    # Reaction shortcuts
    "lol":              "emote-laugh",
    "haha":             "emote-laugh",
    "omg":              "emote-surprise",
    "wow":              "emote-surprise",
    "yep":              "emote-yes",
    "yup":              "emote-yes",
    "nope":             "emote-no",
    "ok":               "emote-thumbsup",
    "nice":             "emote-thumbsup",
    "good":             "emote-thumbsup",
    "mad":              "emote-angry",
    "upset":            "emote-sad",
    "tears":            "emote-cry",
    "weep":             "emote-cry",
    "woo":              "emote-celebrate",
    "yay":              "emote-celebrate",
    "win":              "emote-celebrate",
    # Idle / reset
    "stand":            "emote-idle_loop",
    "reset":            "emote-idle_loop",
    # Combat
    "fight":            "emote-telekinesis",
    "sword":            "emote-telekinesis",
    # Seasonal
    "spooky":           "emote-ghost",
    "brooms":           "emote-witch",
    # Sleep
    "zzz":              "emote-sleep",
    "nap":              "emote-sleep",
    # Flex
    "fistpump":         "emote-flex",
    "pump":             "emote-flex",
    "fist":             "emote-flex",
}
# Merge aliases into _CAND_BY_NAME (primary wins on collision)
for _alias_k, _alias_v in _ALIAS_MAP.items():
    _CAND_BY_NAME.setdefault(_alias_k, _alias_v)

# ─── Explicit command overrides ───────────────────────────────────────────────
# These win over BOTH primary _CAND_BY_NAME entries AND _ALIAS_MAP entries.
# Also reapplied in _load_candidates() after the full catalog is loaded.
_ALIAS_OVERRIDES: dict[str, str] = {
    "dance":       "emote-disco",          # "dance" command → disco animation
    "punch":       "emote-punch",          # punch emote
    "drop":        "emote-deathdrop",      # death drop
    "fall":        "emote-fail1",          # fail animation (not emote-fall)
    "swordfight":  "emote-swordfight",     # sword fight (was telekinesis)
    "sit":         "emote-idle_sitfloor",  # floor sit idle
}
for _ok, _ov in _ALIAS_OVERRIDES.items():
    _CAND_BY_NAME[_ok] = _ov

# ─── Alt IDs: fallback SDK IDs tried when the primary scan fails ──────────────
# Maps canonical_emote_id → tuple of alternative IDs to attempt in order.
# If an alt ID succeeds during a scan, status is saved as 'alias_only'.
_ALT_IDS: dict[str, tuple[str, ...]] = {
    "emote-gangnam":           ("emote-gangnamstyle",),
    "emote-idle_loop":         ("emote-idle",),
    "emote-idle_look":         ("emote-idlelook",),
    "emote-idle_enthusiastic": ("emote-enthusiastic",),
    "emote-curtsy":            ("emote-curtsey",),
    "emote-curtsey":           ("emote-curtsy",),
    "emote-peace":             ("emote-peaceout",),
    "emote-run":               ("emote-running", "emote-sprint"),
    "emote-jump":              ("emote-jumping",),
    "emote-cheer":             ("emote-cheer2",),
}

# ─── Unsafe mode ──────────────────────────────────────────────────────────────
# When True, resolve_emote_id() constructs "emote-{norm}" for any unknown input
# instead of returning None.  Set via !setemoteunverified on|off.
_allow_unverified: bool = False


def get_allow_unverified() -> bool:
    return _allow_unverified


def set_allow_unverified(val: bool) -> None:
    global _allow_unverified
    _allow_unverified = val


# ─── DB helpers ───────────────────────────────────────────────────────────────

def _db_upsert(
    emote_id: str,
    display_name: str,
    status: str,
    fail_reason: str = "",
    alias_id: str = "",
) -> None:
    conn = db.get_connection()
    try:
        conn.execute(
            """INSERT INTO active_emotes
               (emote_id, display_name, status, fail_reason, alias_id, tested_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(emote_id) DO UPDATE SET
                   display_name = excluded.display_name,
                   status       = excluded.status,
                   fail_reason  = excluded.fail_reason,
                   alias_id     = excluded.alias_id,
                   tested_at    = excluded.tested_at""",
            (emote_id, display_name, status, fail_reason, alias_id),
        )
    except Exception:
        try:
            conn.execute(
                """INSERT INTO active_emotes
                   (emote_id, display_name, status, fail_reason, tested_at)
                   VALUES (?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(emote_id) DO UPDATE SET
                       display_name = excluded.display_name,
                       status       = excluded.status,
                       fail_reason  = excluded.fail_reason,
                       tested_at    = excluded.tested_at""",
                (emote_id, display_name, status, fail_reason),
            )
        except Exception:
            conn.execute(
                """INSERT INTO active_emotes (emote_id, display_name, status, tested_at)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT(emote_id) DO UPDATE SET
                       display_name = excluded.display_name,
                       status       = excluded.status,
                       tested_at    = excluded.tested_at""",
                (emote_id, display_name, status),
            )
    conn.commit()
    conn.close()


def _db_reset_all() -> None:
    conn = db.get_connection()
    conn.execute("DELETE FROM active_emotes")
    conn.commit()
    conn.close()


def _db_count(status: str) -> int:
    conn = db.get_connection()
    row = conn.execute(
        "SELECT COUNT(*) FROM active_emotes WHERE status=?", (status,)
    ).fetchone()
    conn.close()
    return int(row[0]) if row else 0


def _db_get_active_names() -> list[str]:
    """Return sorted display_names of active and alias_only emotes."""
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT display_name FROM active_emotes"
        " WHERE status IN ('active','alias_only') ORDER BY display_name"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def _db_get_tested_ids() -> set[str]:
    """Return emote IDs that have been conclusively tested.
    Excludes transient failures so they are retried next scan."""
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT emote_id FROM active_emotes"
        " WHERE status IN ('active','alias_only','disabled','permission')"
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}


def _classify_error(exc: Exception) -> tuple[str | None, str, str, bool]:
    """Classify an emote-test failure.

    Returns (db_status, category, reason, is_retriable):
      db_status    : 'disabled' | 'permission' | None
                     None = don't save; leave untested so next scan retries.
      category     : short label ('invalid' | 'permission' | 'rate_limit' |
                                   'timeout' | 'unknown')
      reason       : human-readable detail string
      is_retriable : True if the error may be transient (rate-limit / timeout)
    """
    raw = str(exc)
    err = raw.lower()

    if isinstance(exc, asyncio.TimeoutError) or "timeout" in err:
        return None, "timeout", "request timed out", True

    if any(p in err for p in ("rate", "too many", "429", "ratelimit", "throttl")):
        return None, "rate_limit", "API rate limited", True

    if any(p in err for p in ("not free or owned", "not owned", "free or owned")):
        return "permission", "permission", "not owned by bot account", False

    if any(p in err for p in (
        "invalid", "not found", "unknown emote", "no such",
        "does not exist", "bad emote", "unsupported",
        "unrecognized", "emote_id", "wrong format",
    )):
        return "disabled", "invalid", "invalid emote ID", False

    # Unknown — don't permanently mark; log it
    return None, "unknown", f"{type(exc).__name__}: {raw[:80]}", False


# ─── Public API ───────────────────────────────────────────────────────────────

def get_active_emote_names() -> list[str]:
    """Return sorted display names of verified-active emotes (may be empty)."""
    try:
        return _db_get_active_names()
    except Exception:
        return []


def resolve_emote_id_full(name: str) -> tuple[str | None, str]:
    """Multi-step emote ID resolution. Returns (emote_id, method).

    Resolution order:
      1. Candidate / alias exact match     → method='exact'
      2. Already a full emote- ID          → method='exact'
      3. Reconstruct emote-{norm} if known → method='constructed'
      4. Unsafe construction               → method='unverified' (only if _allow_unverified)
    """
    norm = _norm(name)
    eid  = _CAND_BY_NAME.get(norm)
    if eid:
        return eid, "exact"
    if name.startswith("emote-") or name.startswith("emote_"):
        return name, "exact"
    candidate = f"emote-{norm}"
    known_ids = {e for _, e in _CANDIDATES}
    if candidate in known_ids:
        return candidate, "constructed"
    if _allow_unverified:
        return f"emote-{norm}", "unverified"
    return None, "none"


def resolve_emote_id(name: str) -> str | None:
    """Resolve a player-typed name to an emote_id.

    Checks candidate names, alias map, direct emote- prefix.
    Returns None if unrecognised (unless allow_unverified is enabled).
    """
    eid, _ = resolve_emote_id_full(name)
    return eid


# ─── Candidate loading ────────────────────────────────────────────────────────

def _community_catalog_path() -> str:
    """Absolute path to the community emote catalog JSON."""
    return os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "data", "highrise_emotes.json")
    )


def _python_catalog_path() -> str:
    """Absolute path to the Python emote catalog (data/highrise_emotes.py)."""
    return os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "data", "highrise_emotes.py")
    )


def _timed_catalog_path() -> str:
    """Absolute path to the timed emote catalog (data/timed_emotes.py)."""
    return os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "data", "timed_emotes.py")
    )


def _parse_emote_json(data: object) -> list[tuple[str, str]]:
    """Parse a JSON object from any of the supported emote catalog formats.

    Supported formats:
      • {"emotes": [{"id": "emote-dance", "name": "dance"}, ...]}  (community format)
      • [{"id": "emote-dance", "name": "dance"}, ...]              (plain list of dicts)
      • ["emote-dance", ...]                                        (plain list of strings)
      • {"dance": "emote-dance", ...}                              (name→id dict)

    Returns a list of (display_name, emote_id) tuples.
    """
    results: list[tuple[str, str]] = []
    items: object = data

    if isinstance(data, dict):
        if "emotes" in data and isinstance(data["emotes"], list):
            items = data["emotes"]
        else:
            for dn, eid in data.items():
                if dn.startswith("_"):
                    continue
                if eid and isinstance(eid, str):
                    results.append((_norm(str(dn)), str(eid)))
            return results

    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                eid = str(item.get("id") or item.get("emote_id") or "").strip()
                dn  = str(item.get("name") or item.get("display_name") or eid).strip()
            elif isinstance(item, str):
                eid = item.strip()
                dn  = eid.replace("emote-", "").replace("_", " ")
            else:
                continue
            if eid:
                results.append((_norm(dn), eid))

    return results


def _load_candidates() -> list[tuple[str, str]]:
    """Return full candidate list from all sources.

    Priority order:
      0. data/timed_emotes.py      — timed catalog (primary source, text+value+time)
      1. data/highrise_emotes.py   — Python catalog (display_name, emote_id)
      2. data/highrise_emotes.json — community JSON catalog
      3. emotes.json               — legacy local override
      4. _CANDIDATES tuple         — always included as base

    Side effect: refreshes _CAND_BY_NAME with all loaded entries, then
    reapplies _ALIAS_OVERRIDES so explicit command mappings always win.
    """
    global _source_counts, _CAND_BY_NAME
    seen_ids: set[str] = set()
    base: list[tuple[str, str]] = []

    for dn, eid in _CANDIDATES:
        if eid not in seen_ids:
            base.append((dn, eid))
            seen_ids.add(eid)
    builtin_n = len(base)

    def _merge_pairs(pairs: list[tuple[str, str]], label: str) -> int:
        added = 0
        for dn, eid in pairs:
            if eid and eid not in seen_ids:
                base.append((dn, eid))
                seen_ids.add(eid)
                added += 1
        if added:
            print(f"{_LOG} Merged {label}: +{added} new IDs (total={len(base)})")
        return added

    def _merge_json(path: str, label: str) -> int:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return _merge_pairs(_parse_emote_json(data), label)
        except FileNotFoundError:
            return 0
        except Exception as exc:
            print(f"{_LOG} {label} load error: {exc}")
            return 0

    # Priority 0: data/timed_emotes.py (timed catalog — primary source)
    timedcat_n = 0
    try:
        import importlib.util as _iutil_t
        _t_path = _timed_catalog_path()
        spec_t = _iutil_t.spec_from_file_location("_hr_timed_reg", _t_path)
        mod_t  = _iutil_t.module_from_spec(spec_t)   # type: ignore[arg-type]
        spec_t.loader.exec_module(mod_t)              # type: ignore[union-attr]
        raw_t: list[dict] = getattr(mod_t, "timed_emotes", [])
        pairs_t = [
            (_norm(str(e.get("text", ""))), str(e["value"]))
            for e in raw_t if e.get("value") and e.get("text")
        ]
        timedcat_n = _merge_pairs(pairs_t, "timed_emotes.py")
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"{_LOG} timed_emotes.py load error: {exc}")

    # Priority 1: data/highrise_emotes.py (Python catalog)
    pycatalog_n = 0
    py_path = _python_catalog_path()
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_hr_emotes_data", py_path)
        mod  = importlib.util.module_from_spec(spec)   # type: ignore[arg-type]
        spec.loader.exec_module(mod)                    # type: ignore[union-attr]
        raw: list[tuple[str, str]] = getattr(mod, "all_emote_list", [])
        pairs = [(_norm(dn), eid) for dn, eid in raw if eid]
        pycatalog_n = _merge_pairs(pairs, "highrise_emotes.py")
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"{_LOG} highrise_emotes.py load error: {exc}")

    # Priority 1: data/highrise_emotes.json (community JSON catalog)
    community_n = _merge_json(_community_catalog_path(), "community catalog")

    # Priority 2: legacy emotes.json override
    local_n = 0
    for path in (
        os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "emotes.json")),
        os.path.normpath(os.path.join(os.path.dirname(__file__), "emotes.json")),
    ):
        if os.path.isfile(path):
            local_n = _merge_json(
                path, f"emotes.json ({os.path.basename(os.path.dirname(path))})"
            )
            break

    # Future-proof: SDK GetEmotesRequest (not yet available)
    sdk_n = 0
    try:
        from highrise import GetEmotesRequest  # type: ignore[import]
        sdk_n = 0
    except ImportError:
        pass

    # Refresh _CAND_BY_NAME with all loaded candidates (setdefault — first writer wins)
    for dn, eid in base:
        _CAND_BY_NAME.setdefault(dn, eid)
        _CAND_BY_NAME.setdefault(_norm(eid), eid)

    # Reapply explicit overrides — these always win regardless of catalog order
    for _ok, _ov in _ALIAS_OVERRIDES.items():
        _CAND_BY_NAME[_ok] = _ov

    _source_counts = {
        "builtin":   builtin_n,
        "timedcat":  timedcat_n,
        "pycatalog": pycatalog_n,
        "community": community_n,
        "local":     local_n,
        "sdk":       sdk_n,
    }
    return base


# ─── Discovery background task ────────────────────────────────────────────────

async def startup_emote_discovery(
    bot: "BaseBot",
    reporter_uid: str | None = None,
) -> None:
    """
    Background task launched on bot start (or manually via !reloademotes/!importemotes).
    Only runs on the dj bot — emote testing must use the same Highrise account
    that owns the emote commands, otherwise send_emote calls fail cross-account.

    reporter_uid: if set, progress whispers are sent to that user during the scan.
    """
    global _scan_state, _allow_unverified
    # Restore unsafe-mode setting from DB on each scan
    _allow_unverified = db.get_room_setting("emote_allow_unverified", "false") == "true"
    from config import BOT_MODE
    if BOT_MODE != "dj":
        print(f"{_LOG} Discovery skipped — only runs on dj bot (current: {BOT_MODE})")
        return

    # Prevent overlapping scans
    if _scan_state["status"] == "running":
        if reporter_uid:
            st = _scan_state
            await _w(
                bot, reporter_uid,
                f"⏳ Scan already running: {st['tested']}/{st['total']} tested, "
                f"{st['active']} active"
            )
        return

    await asyncio.sleep(12)   # let bot fully connect

    from modules.gold import get_bot_user_id
    bot_uid = get_bot_user_id()
    if not bot_uid:
        print(f"{_LOG} No bot UID available — emote discovery skipped")
        _scan_state["status"] = "failed"
        _scan_state["last_error"] = "No bot UID available"
        return

    # Coordinate across multi-bot processes: only run if not scanned recently
    last_run = float(db.get_room_setting("emote_discovery_last_run", "0"))
    if time.time() - last_run < 3600 and reporter_uid is None:
        already = _db_count("active")
        print(f"{_LOG} Discovery ran <1h ago — {already} active emotes cached, skipping")
        return

    candidates = _load_candidates()
    tested     = _db_get_tested_ids()
    pending    = [(dn, eid) for dn, eid in candidates if eid not in tested]

    if not pending:
        print(f"{_LOG} All {len(candidates)} candidates already tested — nothing to do")
        db.set_room_setting("emote_discovery_last_run", str(time.time()))
        if reporter_uid:
            active_n = _db_count("active")
            disabled_n = _db_count("disabled")
            await _w(
                bot, reporter_uid,
                f"✅ Emote scan complete\n"
                f"Active: {active_n}\n"
                f"Failed: {disabled_n}\n"
                f"Total: {len(candidates)}"
            )
        return

    total = len(pending)
    _scan_state.update({
        "status":     "running",
        "tested":     0,
        "total":      total,
        "active":     0,
        "failed":     0,
        "last_error": "",
        "started_at": time.time(),
    })
    print(f"{_SCAN_LOG} started total={total}")

    if reporter_uid:
        await _w(
            bot, reporter_uid,
            f"🎭 Emote scan started...\nCandidates: {total}"
        )

    # Capture pre-scan active count for mismatch detection
    prev_active = _db_count("active")

    # Clear the in-memory failure log for this scan
    _failure_log.clear()

    active_n = alias_n = disabled_n = permission_n = transient_n = error_n = 0
    first_error: str | None = None
    _RETRY_DELAYS = (1.5, 5.0)
    MAX_RETRIES = 2

    for idx, (display_name, emote_id) in enumerate(pending, 1):
        success       = False
        final_db_status: str | None = None
        final_category  = "unknown"
        final_reason    = ""

        for attempt in range(MAX_RETRIES + 1):
            if attempt > 0:
                await asyncio.sleep(_RETRY_DELAYS[attempt - 1])
            try:
                await bot.highrise.send_emote(emote_id, bot_uid)
                success = True
                break
            except asyncio.CancelledError:
                _scan_state["status"] = "failed"
                _scan_state["last_error"] = "Cancelled"
                raise
            except Exception as exc:
                db_status, category, reason, retriable = _classify_error(exc)
                final_db_status = db_status
                final_category  = category
                final_reason    = reason
                if first_error is None:
                    first_error = f"{emote_id}: {reason}"
                    _scan_state["last_error"] = first_error[:120]
                if retriable and attempt < MAX_RETRIES:
                    print(
                        f"{_SCAN_LOG} retry {attempt + 1}/{MAX_RETRIES}"
                        f" {emote_id}: {reason}"
                    )
                    continue
                break  # non-retriable or retries exhausted

        if success:
            _db_upsert(emote_id, display_name, "active")
            active_n += 1
        else:
            # Try alt IDs before declaring failure
            alt_ids    = _ALT_IDS.get(emote_id, ())
            alias_found = ""
            for alt_id in alt_ids:
                try:
                    await asyncio.sleep(0.2)
                    await bot.highrise.send_emote(alt_id, bot_uid)
                    alias_found = alt_id
                    break
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass

            if alias_found:
                _db_upsert(emote_id, display_name, "alias_only", "", alias_found)
                alias_n += 1
                print(f"{_SCAN_LOG} alias {emote_id} → {alias_found}")
            else:
                _failure_log[emote_id] = {
                    "display_name": display_name,
                    "category":     final_category,
                    "reason":       final_reason,
                }
                print(
                    f"{_SCAN_LOG} fail {emote_id}"
                    f" [{final_category}] {final_reason[:80]}"
                )
                if final_db_status == "disabled":
                    _db_upsert(emote_id, display_name, "disabled", final_reason)
                    disabled_n += 1
                elif final_db_status == "permission":
                    _db_upsert(emote_id, display_name, "permission", final_reason)
                    permission_n += 1
                elif final_category in ("rate_limit", "timeout"):
                    transient_n += 1   # leave untested — next scan will retry
                else:
                    error_n += 1       # unknown, also leave untested

        failed_n = disabled_n + permission_n + transient_n + error_n
        _scan_state.update({
            "tested":     idx,
            "active":     active_n,
            "alias_only": alias_n,
            "failed":     failed_n,
            "permission": permission_n,
            "invalid":    disabled_n,
            "transient":  transient_n,
        })

        # Progress whisper every 10 tested
        if reporter_uid and idx % 10 == 0:
            await _w(
                bot, reporter_uid,
                f"🎭 Scanning emotes...\n"
                f"Tested: {idx}/{total}\n"
                f"Active: {active_n}\n"
                f"Failed: {failed_n}"
            )
            print(
                f"{_SCAN_LOG} progress tested={idx}"
                f" active={active_n} failed={failed_n}"
            )

        await asyncio.sleep(0.35)

    failed_total = disabled_n + permission_n + transient_n + error_n
    _scan_state.update({
        "status":     "done",
        "tested":     total,
        "active":     active_n,
        "alias_only": alias_n,
        "failed":     failed_total,
        "permission": permission_n,
        "invalid":    disabled_n,
        "transient":  transient_n,
    })
    db.set_room_setting("emote_discovery_last_run", str(time.time()))

    print(
        f"{_SCAN_LOG} complete"
        f" active={active_n} alias={alias_n} failed={failed_total}"
        f" (permission={permission_n} invalid={disabled_n}"
        f" transient={transient_n} unknown={error_n})"
    )

    # Mismatch: if active count dropped, explain breakdown
    if prev_active > 0 and (active_n + alias_n) < prev_active:
        dropped = prev_active - (active_n + alias_n)
        print(
            f"{_SCAN_LOG} MISMATCH prev_active={prev_active}"
            f" now_active={active_n} alias={alias_n} dropped={dropped}"
            f" — permission={permission_n} invalid={disabled_n}"
            f" transient={transient_n}"
        )

    if transient_n > 0:
        print(
            f"{_SCAN_LOG} {transient_n} transient left untested"
            f" — run !reloademotes to retry"
        )

    if reporter_uid:
        alias_str = f" ⚠️{alias_n}" if alias_n else ""
        perm_str  = f" (perm:{permission_n})" if permission_n else ""
        inv_str   = f" (invalid:{disabled_n})" if disabled_n else ""
        trans_str = f" (retry:{transient_n})" if transient_n else ""
        await _w(
            bot, reporter_uid,
            f"✅ Emote scan complete\n"
            f"✅ Active: {active_n}{alias_str}\n"
            f"❌ Failed: {failed_total}{perm_str}{inv_str}{trans_str}\n"
            f"Total: {total}"
        )

    if active_n == 0 and first_error:
        print(f"{_LOG} [DIAG] No active emotes. First error → {first_error}")


# ─── Admin commands ───────────────────────────────────────────────────────────

async def handle_importemotes(bot: "BaseBot", user: "User", _args: list) -> None:
    """!importemotes — admin: load community catalog, merge with built-ins, rescan.

    Reads data/highrise_emotes.json, adds any IDs not already in the candidate
    set, clears the test cache, and kicks off a full rediscovery pass.
    Only keeps emotes that actually work (send_emote succeeds).
    """
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    catalog_path = _community_catalog_path()
    if not os.path.isfile(catalog_path):
        await _w(
            bot, uid,
            "❌ Community catalog not found.\n"
            "Expected: data/highrise_emotes.json\n"
            "Drop the file in the bot's data/ folder, then retry."
        )
        return

    try:
        with open(catalog_path, encoding="utf-8") as f:
            data = json.load(f)
        entries = _parse_emote_json(data)
    except Exception as exc:
        await _w(bot, uid, f"❌ Failed to parse catalog: {exc!s:.120}")
        return

    if not entries:
        await _w(bot, uid, "❌ Catalog is empty or unrecognised format.")
        return

    candidates = _load_candidates()
    total_candidates = len(candidates)

    db.set_room_setting("emote_catalog_source", "community")
    _db_reset_all()
    db.set_room_setting("emote_discovery_last_run", "0")

    print(f"{_LOG} importemotes by={uname!r} catalog_entries={len(entries)}"
          f" total_candidates={total_candidates}")

    await _w(
        bot, uid,
        f"📥 Community catalog loaded: {len(entries)} entries\n"
        f"🎭 Total candidates after merge: {total_candidates}"
    )
    asyncio.create_task(startup_emote_discovery(bot, reporter_uid=uid))


async def handle_reloademotes(bot: "BaseBot", user: "User", _args: list) -> None:
    """!reloademotes — admin: wipe cache and re-discover all emotes."""
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    candidates = _load_candidates()
    _db_reset_all()
    db.set_room_setting("emote_discovery_last_run", "0")
    print(f"{_LOG} Cache cleared by {uname} — rescan triggered ({len(candidates)} candidates)")
    asyncio.create_task(startup_emote_discovery(bot, reporter_uid=uid))


async def handle_emotecount(bot: "BaseBot", user: "User", _args: list) -> None:
    """!emotecount — show how many emotes are active/permission/invalid."""
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    active     = _db_count("active")
    alias_only = _db_count("alias_only")
    permission = _db_count("permission")
    disabled   = _db_count("disabled")
    candidates = _load_candidates()
    sc  = _source_counts
    src = f"builtin:{sc.get('builtin', 0)}"
    if sc.get("pycatalog"):
        src += f" py:{sc['pycatalog']}"
    if sc.get("community"):
        src += f" community:{sc['community']}"
    if sc.get("local"):
        src += f" local:{sc['local']}"
    alias_str = f" ⚠️Alias:{alias_only}" if alias_only else ""
    await _w(
        bot, uid,
        f"🎭 ✅Active:{active}{alias_str} 🔒Perm:{permission} ❌Invalid:{disabled}\n"
        f"📋 Candidates:{len(candidates)} ({src})\n"
        f"!emotefailures for breakdown | !reloademotes to rescan"
    )


async def handle_emotescanstatus(bot: "BaseBot", user: "User", _args: list) -> None:
    """!emotescanstatus — show current or last emote scan progress."""
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    st = _scan_state
    status = st["status"]
    tested = st["tested"]
    total  = st["total"]
    active = st["active"]
    failed = st["failed"]
    err    = st["last_error"] or "none"

    alias_only = st.get("alias_only", 0)
    lines = [
        f"🎭 Scan: {status}",
        f"Tested: {tested}/{total}",
        f"✅ Active: {active}" + (f"  ⚠️ Alias: {alias_only}" if alias_only else ""),
        f"❌ Failed: {failed}",
        f"Last error: {err[:80]}",
    ]
    await _w(bot, uid, "\n".join(lines))


async def handle_emotesource(bot: "BaseBot", user: "User", _args: list) -> None:
    """!emotesource — show where the emote catalog came from and result breakdown."""
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    active     = _db_count("active")
    alias_only = _db_count("alias_only")
    permission = _db_count("permission")
    disabled   = _db_count("disabled")
    candidates = _load_candidates()
    last_run   = float(db.get_room_setting("emote_discovery_last_run", "0"))
    catalog    = _community_catalog_path()
    sc         = _source_counts

    if last_run > 0:
        import datetime
        ts = datetime.datetime.fromtimestamp(last_run).strftime("%Y-%m-%d %H:%M")
        last_str = f"Last scan: {ts}"
    else:
        last_str = "Not yet scanned"

    community_file = "✅ present" if os.path.isfile(catalog) else "❌ missing"

    src_parts = [f"builtin:{sc.get('builtin', 0)}"]
    if sc.get("pycatalog"):
        src_parts.append(f"py:{sc['pycatalog']}")
    if sc.get("community"):
        src_parts.append(f"community:{sc['community']}")
    if sc.get("local"):
        src_parts.append(f"local:{sc['local']}")
    if sc.get("sdk"):
        src_parts.append(f"sdk:{sc['sdk']}")

    alias_str = f" ⚠️{alias_only} alias" if alias_only else ""
    await _w(
        bot, uid,
        f"🎭 Sources: {', '.join(src_parts)}\n"
        f"📋 {len(candidates)} candidates"
        f" → ✅{active}{alias_str}"
        f" 🔒{permission} ❌{disabled}\n"
        f"📁 community: {community_file}\n"
        f"{last_str}"
    )


async def handle_emotefailures(bot: "BaseBot", user: "User", args: list) -> None:
    """!emotefailures [category] — show failed emote names and reasons from last scan.

    Categories: permission  invalid  rate_limit  timeout  unknown
    No category → summary of all categories.
    """
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    # Use in-memory failure log from last scan; fall back to DB for persistent data
    failures: dict[str, dict] = dict(_failure_log)
    if not failures:
        try:
            conn = db.get_connection()
            rows = conn.execute(
                "SELECT emote_id, display_name, status, COALESCE(fail_reason,'') "
                "FROM active_emotes WHERE status IN ('disabled','permission') "
                "ORDER BY status, emote_id"
            ).fetchall()
            conn.close()
            for eid, dn, status, reason in rows:
                cat = "invalid" if status == "disabled" else "permission"
                failures[eid] = {
                    "display_name": dn or eid,
                    "category":     cat,
                    "reason":       reason,
                }
        except Exception:
            pass

    if not failures:
        await _w(bot, uid, "🎭 No failures recorded. Run !reloademotes to scan.")
        return

    # Group by category
    by_cat: dict[str, list[str]] = {}
    for eid, info in failures.items():
        cat = info.get("category", "unknown")
        by_cat.setdefault(cat, []).append(info.get("display_name") or eid)

    filt = args[1].lower() if len(args) > 1 else None

    if filt is None:
        # Summary page
        total_f = len(failures)
        lines = [f"🎭 Emote failures ({total_f} total):"]
        for cat in sorted(by_cat):
            lines.append(f"  {cat}: {len(by_cat[cat])}")
        lines.append("!emotefailures <category> for names")
        await _w(bot, uid, "\n".join(lines)[:249])
        return

    # Category detail: paginated list of names
    cat_names = sorted(by_cat.get(filt, []))
    if not cat_names:
        known = ", ".join(sorted(by_cat)) or "none"
        await _w(bot, uid, f"❌ Category '{filt}' not found. Known: {known}")
        return

    MAX = 249
    pages: list[list[str]] = []
    buf:   list[str]       = []
    used                   = 0
    for name in cat_names:
        chunk_len = len(name) + 2  # "name, "
        if buf and used + chunk_len > MAX - 22:   # 22 = header budget
            pages.append(buf[:])
            buf  = []
            used = 0
        buf.append(name)
        used += chunk_len
    if buf:
        pages.append(buf)

    total_pg = max(1, len(pages))
    for pg, chunk in enumerate(pages, 1):
        hdr  = f"❌ {filt} {pg}/{total_pg} ({len(cat_names)}):\n"
        text = (hdr + ", ".join(chunk))[:249]
        await _w(bot, uid, text)
        if pg < total_pg:
            await asyncio.sleep(0.4)


async def handle_testemote(bot: "BaseBot", user: "User", args: list) -> None:
    """!testemote <name|emote-id> — multi-step test: show command, alias, and result."""
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    if len(args) < 2:
        await _w(bot, uid, "Usage: !testemote <name>  e.g. !testemote dance")
        return

    raw              = " ".join(args[1:]).strip()
    resolved, method = resolve_emote_id_full(raw)

    # Accept explicit emote- IDs even if not in registry
    if resolved is None:
        if raw.startswith("emote-") or raw.startswith("emote_"):
            resolved, method = raw, "explicit"
        else:
            resolved, method = f"emote-{_norm(raw)}", "constructed"

    emote_id = resolved
    steps: list[str] = [f"🔍 {raw!r} → {emote_id} [{method}]"]

    from modules.gold import get_bot_user_id
    bot_uid = get_bot_user_id()
    if not bot_uid:
        await _w(bot, uid, "❌ Bot UID not available yet.")
        return

    # Step 1: try primary emote_id
    try:
        await bot.highrise.send_emote(emote_id, bot_uid)
        _db_upsert(emote_id, _norm(raw), "active")
        steps.append(f"✅ Primary OK")
        await _w(bot, uid, "\n".join(steps)[:249])
        print(f"{_LOG} testemote {emote_id!r} result=active tester={uname!r}")
        return
    except Exception as exc:
        _, category, reason, _ = _classify_error(exc)
        steps.append(f"❌ Primary [{category}]: {reason[:55]}")

    # Step 2: try alt IDs
    for alt_id in _ALT_IDS.get(emote_id, ()):
        try:
            await asyncio.sleep(0.2)
            await bot.highrise.send_emote(alt_id, bot_uid)
            _db_upsert(emote_id, _norm(raw), "alias_only", "", alt_id)
            steps.append(f"⚠️ Alias {alt_id} → OK")
            await _w(bot, uid, "\n".join(steps)[:249])
            print(f"{_LOG} testemote {emote_id!r} alias={alt_id!r} result=alias_only tester={uname!r}")
            return
        except Exception as exc2:
            _, cat2, reason2, _ = _classify_error(exc2)
            steps.append(f"❌ Alt {alt_id} [{cat2}]")

    steps.append("❌ No working ID found")
    await _w(bot, uid, "\n".join(steps)[:249])
    print(f"{_LOG} testemote {emote_id!r} result=fail steps={len(steps)} tester={uname!r}")


async def handle_failedemotes(bot: "BaseBot", user: "User", args: list) -> None:
    """!failedemotes — alias for !emotefailures."""
    await handle_emotefailures(bot, user, args)


async def handle_setemoteunverified(bot: "BaseBot", user: "User", args: list) -> None:
    """!setemoteunverified on|off — allow untested emote IDs (unsafe mode).

    When ON, lookup_emote() will construct 'emote-{norm}' for any unknown input
    so community emotes can be used even if they haven't been scanned yet.
    """
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "👑 Admin only.")
        return
    sub = args[1].lower() if len(args) > 1 else ""
    if sub == "on":
        set_allow_unverified(True)
        db.set_room_setting("emote_allow_unverified", "true")
        await _w(
            bot, uid,
            "⚠️ Unverified emotes ON\n"
            "Untested IDs will be attempted.\n"
            "Use !reloademotes to scan and validate."
        )
        print(f"{_LOG} allow_unverified=True by={uname!r}")
    elif sub == "off":
        set_allow_unverified(False)
        db.set_room_setting("emote_allow_unverified", "false")
        await _w(bot, uid, "✅ Unverified emotes OFF — only scanned emotes used.")
        print(f"{_LOG} allow_unverified=False by={uname!r}")
    else:
        cur = "ON ⚠️" if _allow_unverified else "OFF ✅"
        await _w(bot, uid, f"!setemoteunverified on|off\nCurrent: {cur}")


# ─── Player command: !emotes ──────────────────────────────────────────────────

async def handle_emotes_paged(bot: "BaseBot", user: "User", _args: list) -> None:
    """
    !emotes — whisper all active emote names in auto-paged messages (≤249 chars).
    Falls back to the static EMOTE_REGISTRY if no active emotes are cached yet.
    """
    names = get_active_emote_names()

    if not names:
        try:
            from modules.emote_system import EMOTE_REGISTRY
            names = sorted(EMOTE_REGISTRY.keys())
        except Exception:
            names = []

    if not names:
        await _w(bot, user.id, "🎭 No emotes available yet. An admin can run !reloademotes.")
        return

    # Pack names into ≤249-char pages
    # Reserve space for worst-case header "🎭 Emotes 99/99:\n" (18 chars)
    MAX     = 249
    HDR_MAX = 18
    AVAIL   = MAX - HDR_MAX

    pages: list[list[str]] = []
    buf:   list[str]       = []
    used                   = 0

    for name in sorted(names):
        item_len = len(name) + 2   # "name, "
        if buf and used + item_len > AVAIL:
            pages.append(buf[:])
            buf  = []
            used = 0
        buf.append(name)
        used += item_len

    if buf:
        pages.append(buf)

    total = max(1, len(pages))
    for pg, chunk in enumerate(pages, 1):
        hdr  = f"🎭 Emotes {pg}/{total}:\n"
        text = (hdr + ", ".join(chunk))[:249]
        await _w(bot, user.id, text)
        if pg < total:
            await asyncio.sleep(0.4)

    print(
        f"[EMOTE_REG] emotes_list user={user.username!r}"
        f" total_emotes={len(names)} pages={total}"
    )
