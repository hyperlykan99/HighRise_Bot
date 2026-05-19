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
import time
from typing import TYPE_CHECKING

import database as db
from modules.permissions import is_admin, is_owner

if TYPE_CHECKING:
    from highrise import BaseBot, User

_LOG = "[EMOTE_REG]"

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
    return s.lower().replace("-", "").replace("_", "").replace(" ", "")

# name→id lookup built from candidates (for !testemote by display name)
_CAND_BY_NAME: dict[str, str] = {_norm(dn): eid for dn, eid in _CANDIDATES}
# Also index by emote_id directly (strip "emote-" prefix for matching)
for _dn, _eid in _CANDIDATES:
    _CAND_BY_NAME[_norm(_eid)] = _eid


# ─── DB helpers ───────────────────────────────────────────────────────────────

def _db_upsert(emote_id: str, display_name: str, status: str) -> None:
    conn = db.get_connection()
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
    """Return sorted display_names of all active emotes."""
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT display_name FROM active_emotes WHERE status='active' ORDER BY display_name"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def _db_get_tested_ids() -> set[str]:
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT emote_id FROM active_emotes WHERE status IN ('active','disabled')"
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}


# ─── Public API ───────────────────────────────────────────────────────────────

def get_active_emote_names() -> list[str]:
    """Return sorted display names of verified-active emotes (may be empty)."""
    try:
        return _db_get_active_names()
    except Exception:
        return []


def resolve_emote_id(name: str) -> str | None:
    """Resolve a player-typed name to an emote_id. Returns None if unrecognised."""
    return _CAND_BY_NAME.get(_norm(name))


# ─── Candidate loading ────────────────────────────────────────────────────────

def _community_catalog_path() -> str:
    """Absolute path to the community emote catalog JSON."""
    return os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "data", "highrise_emotes.json")
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
    """Return full candidate list, augmented by community catalog and local overrides."""
    seen_ids: set[str] = set()
    base: list[tuple[str, str]] = []

    for dn, eid in _CANDIDATES:
        if eid not in seen_ids:
            base.append((dn, eid))
            seen_ids.add(eid)

    def _merge(path: str, label: str) -> int:
        added = 0
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for dn, eid in _parse_emote_json(data):
                if eid and eid not in seen_ids:
                    base.append((dn, eid))
                    seen_ids.add(eid)
                    added += 1
            print(f"{_LOG} Merged {label}: +{added} new IDs (total candidates={len(base)})")
        except FileNotFoundError:
            pass
        except Exception as exc:
            print(f"{_LOG} {label} load error: {exc}")
        return added

    # Priority 1: community catalog (data/highrise_emotes.json)
    _merge(_community_catalog_path(), "community catalog")

    # Priority 2: legacy emotes.json override (bot root or modules dir)
    for path in (
        os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "emotes.json")),
        os.path.normpath(os.path.join(os.path.dirname(__file__), "emotes.json")),
    ):
        if os.path.isfile(path):
            _merge(path, f"emotes.json ({os.path.basename(os.path.dirname(path))})")
            break

    # Future-proof: SDK GetEmotesRequest (not yet available in current SDK)
    try:
        from highrise import GetEmotesRequest  # type: ignore[import]
        print(f"{_LOG} SDK GetEmotesRequest found — will query at runtime")
    except ImportError:
        pass

    return base


# ─── Discovery background task ────────────────────────────────────────────────

async def startup_emote_discovery(bot: "BaseBot") -> None:
    """
    Background task launched on bot start.
    Only runs on the dj bot — emote testing must use the same Highrise account
    that owns the emote commands, otherwise send_emote calls fail cross-account.
    """
    from config import BOT_MODE
    if BOT_MODE != "dj":
        print(f"{_LOG} Discovery skipped — only runs on dj bot (current: {BOT_MODE})")
        return

    await asyncio.sleep(12)   # let bot fully connect

    from modules.gold import get_bot_user_id
    bot_uid = get_bot_user_id()
    if not bot_uid:
        print(f"{_LOG} No bot UID available — emote discovery skipped")
        return

    # Coordinate across multi-bot processes: only run if not scanned recently
    last_run = float(db.get_room_setting("emote_discovery_last_run", "0"))
    if time.time() - last_run < 3600:
        already = _db_count("active")
        print(f"{_LOG} Discovery ran <1h ago — {already} active emotes cached, skipping")
        return

    candidates = _load_candidates()
    tested     = _db_get_tested_ids()
    pending    = [(dn, eid) for dn, eid in candidates if eid not in tested]

    if not pending:
        print(f"{_LOG} All {len(candidates)} candidates already tested — nothing to do")
        db.set_room_setting("emote_discovery_last_run", str(time.time()))
        return

    print(f"{_LOG} Discovery starting: {len(pending)} untested / {len(candidates)} total")
    active_n = disabled_n = error_n = 0
    first_error: str | None = None

    for display_name, emote_id in pending:
        try:
            await bot.highrise.send_emote(emote_id, bot_uid)
            _db_upsert(emote_id, display_name, "active")
            active_n += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raw = str(exc)
            err = raw.lower()
            if first_error is None:
                first_error = f"{type(exc).__name__}: {raw}"
            if any(p in err for p in (
                "invalid", "not found", "unknown", "no such",
                "does not exist", "bad emote", "not supported",
                "unsupported", "unrecognized", "emote_id",
                "not free or owned", "not owned", "free or owned",
                "responseError", "response_error",
            )):
                _db_upsert(emote_id, display_name, "disabled")
                disabled_n += 1
            else:
                # Truly unclassified error (rate-limit? SDK crash?) — log and skip
                error_n += 1
                print(f"{_LOG} [ERROR] {emote_id}: {type(exc).__name__}: {raw[:120]}")
        await asyncio.sleep(0.35)

    db.set_room_setting("emote_discovery_last_run", str(time.time()))
    summary = (
        f"{_LOG} Discovery complete: active={active_n}"
        f" disabled={disabled_n} errors={error_n}"
    )
    print(summary)
    if active_n == 0 and first_error:
        print(f"{_LOG} [DIAG] No emotes activated. First error → {first_error}")
    if active_n == 0 and error_n > 0:
        print(
            f"{_LOG} [DIAG] {error_n} unclassified errors — likely rate-limit or"
            f" SDK mismatch. Try !reloademotes after 60s."
        )


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
        f"🎭 Total candidates after merge: {total_candidates}\n"
        f"🔄 Testing all emotes in background (~{total_candidates // 3}s)..."
    )
    asyncio.create_task(startup_emote_discovery(bot))


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
    await _w(
        bot, uid,
        f"🔄 Emote cache cleared. Rescanning {len(candidates)} candidates in "
        f"background (~{len(candidates) // 3}s)..."
    )
    asyncio.create_task(startup_emote_discovery(bot))


async def handle_emotecount(bot: "BaseBot", user: "User", _args: list) -> None:
    """!emotecount — show how many emotes are active/disabled/untested."""
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    active     = _db_count("active")
    disabled   = _db_count("disabled")
    candidates = _load_candidates()
    source     = db.get_room_setting("emote_catalog_source", "builtin")
    await _w(
        bot, uid,
        f"🎭 Emotes: {active} active | {disabled} disabled\n"
        f"📋 Candidates: {len(candidates)} | Source: {source}\n"
        f"Use !reloademotes to rescan | !emotesource for details."
    )


async def handle_emotesource(bot: "BaseBot", user: "User", _args: list) -> None:
    """!emotesource — show where the current emote catalog came from."""
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    source     = db.get_room_setting("emote_catalog_source", "builtin")
    active     = _db_count("active")
    disabled   = _db_count("disabled")
    candidates = _load_candidates()
    last_run   = float(db.get_room_setting("emote_discovery_last_run", "0"))
    catalog    = _community_catalog_path()

    if last_run > 0:
        import datetime
        ts = datetime.datetime.fromtimestamp(last_run).strftime("%Y-%m-%d %H:%M")
        last_str = f"Last scan: {ts}"
    else:
        last_str = "Not yet scanned"

    community_file = "✅ present" if os.path.isfile(catalog) else "❌ missing"

    source_label = {
        "builtin":   "Built-in list (~155 IDs)",
        "community": "Community catalog (data/highrise_emotes.json)",
        "sdk":       "Highrise SDK discovery",
    }.get(source, source)

    await _w(
        bot, uid,
        f"🎭 Emote catalog source: {source_label}\n"
        f"📋 {len(candidates)} candidates | ✅ {active} active | ❌ {disabled} disabled\n"
        f"📁 data/highrise_emotes.json: {community_file}\n"
        f"{last_str}"
    )


async def handle_testemote(bot: "BaseBot", user: "User", args: list) -> None:
    """!testemote <name|emote-id> — test one emote and report success/fail."""
    uid   = user.id
    uname = user.username
    if not _is_admin(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    if len(args) < 2:
        await _w(bot, uid, "Usage: !testemote <name>  e.g. !testemote dance")
        return

    raw     = " ".join(args[1:]).strip()
    emote_id = resolve_emote_id(raw)
    if not emote_id:
        # Maybe they typed the emote ID directly
        if raw.startswith("emote-") or raw.startswith("emote_"):
            emote_id = raw
        else:
            emote_id = f"emote-{_norm(raw)}"

    from modules.gold import get_bot_user_id
    bot_uid = get_bot_user_id()
    if not bot_uid:
        await _w(bot, uid, "❌ Bot UID not available yet — try again in a moment.")
        return

    try:
        await bot.highrise.send_emote(emote_id, bot_uid)
        _db_upsert(emote_id, _norm(raw), "active")
        await _w(bot, uid, f"✅ {emote_id} works!")
        print(f"{_LOG} testemote id={emote_id!r} result=active tester={uname!r}")
    except Exception as exc:
        err = str(exc)
        err_low = err.lower()
        if any(p in err_low for p in ("invalid", "not found", "unknown", "no such", "does not exist", "bad emote")):
            _db_upsert(emote_id, _norm(raw), "disabled")
            await _w(bot, uid, f"❌ {emote_id} is not a valid emote.")
        else:
            await _w(bot, uid, f"⚠️ {emote_id} — unexpected error: {err[:80]}")
        print(f"{_LOG} testemote id={emote_id!r} result=fail error={err!r} tester={uname!r}")


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
