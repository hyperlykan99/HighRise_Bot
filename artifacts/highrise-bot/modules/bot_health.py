"""
modules/bot_health.py
Multi-bot health monitoring, module status, and deployment safety checks.

Permissions:
  manager+  — view commands (/bothealth, /modulehealth, /deploymentcheck,
               /botlocks, /botheartbeat, /moduleowners, /botconflicts)
  admin+    — repair commands (/clearstalebotlocks, /fixbotowners)
"""

from __future__ import annotations
from datetime import datetime, timezone

import database as db
from config import BOT_ID, BOT_MODE
from modules.permissions import can_manage_games, can_manage_economy

# ---------------------------------------------------------------------------
# Module → owner mode + sample commands
# ---------------------------------------------------------------------------

_MODULE_MAP: dict[str, dict] = {
    "blackjack": {"owner": "blackjack", "cmds": ["bj", "bjoin", "bh"], "label": "BJ"},
    "rbj":       {"owner": "blackjack", "cmds": ["rbj", "rjoin", "rh"], "label": "RBJ"},
    "poker":     {"owner": "poker",     "cmds": ["poker", "p", "fold"], "label": "Poker"},
    "mining":    {"owner": "miner",     "cmds": ["mine", "ores"],       "label": "Mining"},
    "bank":      {"owner": "banker",    "cmds": ["bal", "send"],        "label": "Bank"},
    "shop":      {"owner": "shopkeeper","cmds": ["shop", "buy"],        "label": "Shop"},
    "security":  {"owner": "security",  "cmds": ["report", "warn"],     "label": "Security"},
    "dj":        {"owner": "dj",        "cmds": ["emote", "dance"],     "label": "DJ"},
    "events":    {"owner": "eventhost", "cmds": ["events", "alert"],    "label": "Events"},
    "autogames": {"owner": "eventhost", "cmds": ["autogames", "autoevents"], "label": "AutoGames"},
}

_MODE_NAMES: dict[str, str] = {
    "host": "Host", "banker": "Banker", "blackjack": "Blackjack",
    "poker": "Poker", "dealer": "Dealer", "miner": "Miner",
    "shopkeeper": "Shop", "security": "Security",
    "dj": "DJ", "eventhost": "Events", "all": "Main",
}

_MODE_ICONS: dict[str, str] = {
    "host": "🎙️", "banker": "🏦", "blackjack": "🃏",
    "poker": "♠️", "dealer": "🎰", "miner": "⛏️",
    "shopkeeper": "🛒", "security": "🛡️",
    "dj": "🎧", "eventhost": "🎉", "all": "🤖",
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _w(bot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


def _age_str(last_seen: str) -> str:
    if not last_seen:
        return "never"
    try:
        ls = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
        if ls.tzinfo is None:
            ls = ls.replace(tzinfo=timezone.utc)
        secs = int((datetime.now(timezone.utc) - ls).total_seconds())
        return f"{secs}s ago"
    except Exception:
        return "?"


def _bot_is_online(mode: str, instances: list, threshold: int = 90) -> bool:
    """True if any enabled instance with this mode has heartbeated within threshold seconds.

    Uses last_heartbeat_at (written by the 30-s heartbeat loop after a stable
    30-s connection) rather than last_seen_at (written on every reconnect).
    Falls back to last_seen_at only when last_heartbeat_at is absent so that
    existing rows without the column still behave correctly.
    """
    now = datetime.now(timezone.utc)
    for inst in instances:
        if inst.get("bot_mode") != mode:
            continue
        if not inst.get("enabled", 1):
            continue
        hb = inst.get("last_heartbeat_at", "")
        ts = hb if hb else inst.get("last_seen_at", "")
        if not ts:
            continue
        try:
            ls = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if ls.tzinfo is None:
                ls = ls.replace(tzinfo=timezone.utc)
            age = (now - ls).total_seconds()
            if age < threshold:
                return True
        except Exception:
            pass
    return False


def _check_db() -> bool:
    try:
        conn = db.get_connection()
        conn.execute("SELECT 1")
        conn.close()
        return True
    except Exception:
        return False


def _get_stale_locks() -> list[dict]:
    """Return all locks that are expired (expires_at < now)."""
    try:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT * FROM bot_module_locks WHERE expires_at < datetime('now')"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _get_all_locks() -> list[dict]:
    try:
        conn = db.get_connection()
        rows = conn.execute("SELECT * FROM bot_module_locks").fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# /bothealth [bot_id]
# ---------------------------------------------------------------------------

async def handle_bothealth(bot, user, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Manager and above only.")
        return
    instances = db.get_bot_instances()
    db_ok = _check_db()
    stale = _get_stale_locks()

    if len(args) >= 2:
        target = args[1].lower()
        found = next((i for i in instances
                      if i.get("bot_id", "").lower() == target
                      or i.get("bot_mode", "").lower() == target), None)
        if not found:
            await _w(bot, user.id, f"No bot found for '{target}'.")
            return
        mode = found.get("bot_mode", "?")
        icon = _MODE_ICONS.get(mode, "🤖")
        name = _MODE_NAMES.get(mode, mode.title())
        online = _bot_is_online(mode, instances)
        state = "ON ✅" if online else "OFF ⛔"
        age = _age_str(found.get("last_seen_at", ""))
        db_flag = found.get("db_connected", 1)
        err = found.get("last_error", "")
        db_str = "DB OK" if db_flag else "DB ERR"
        msg = f"{icon} {name}: {state} | Mode {mode} | {db_str} | {age}"
        if err:
            msg += f" | Err: {err[:40]}"
        await _w(bot, user.id, msg[:249])
        return

    # Summary
    parts: list[str] = []
    modes_seen: set[str] = set()
    for inst in instances:
        mode = inst.get("bot_mode", "?")
        if mode in modes_seen:
            continue
        modes_seen.add(mode)
        online = _bot_is_online(mode, instances)
        parts.append(f"{_MODE_NAMES.get(mode, mode)} {'ON' if online else 'OFF'}")

    if not parts:
        parts = [f"Main ({'ON' if _check_db() else 'ERR'})"]

    db_str = "DB OK" if db_ok else "DB ERR"
    conflict_count = _count_conflicts(instances)
    await _w(bot, user.id,
             (f"Health: {' | '.join(parts)} | {db_str}"
              f" | Conflicts {conflict_count}")[:249])


# ---------------------------------------------------------------------------
# /modulehealth [module]
# ---------------------------------------------------------------------------

async def handle_modulehealth(bot, user, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Manager and above only.")
        return
    instances = db.get_bot_instances()
    db_ok = _check_db()

    if len(args) >= 2:
        target = args[1].lower()
        # Accept aliases: bj, rbj, poker, mining, bank, shop, mod, security, dj, events
        _alias = {
            "bj": "blackjack", "blackjack": "blackjack",
            "rbj": "rbj", "realisticbj": "rbj",
            "poker": "poker",
            "mine": "mining", "mining": "mining",
            "bank": "bank", "economy": "bank",
            "shop": "shop", "shopkeeper": "shop",
            "mod": "security", "moderation": "security", "security": "security",
            "dj": "dj", "emote": "dj",
            "events": "events", "event": "events",
        }
        key = _alias.get(target, target)
        info = _MODULE_MAP.get(key)
        if not info:
            await _w(bot, user.id,
                     f"Unknown module '{target}'. Try: bj rbj poker mining bank shop dj events")
            return
        owner_mode = info["owner"]
        label = info["label"]
        icon = _MODE_ICONS.get(owner_mode, "🤖")
        name = _MODE_NAMES.get(owner_mode, owner_mode.title())
        online = _bot_is_online(owner_mode, instances)
        bot_state = "ON ✅" if online else "OFF ⛔"
        db_state = "DB OK" if db_ok else "DB ERR"
        # Check locks
        locks = _get_all_locks()
        locked = any(l["module"] == key and
                     l.get("expires_at", "") > datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                     for l in locks)
        lock_state = "locked" if locked else "locks OK"
        await _w(bot, user.id,
                 f"{icon} {label}: owner {name} | bot {bot_state} | {db_state}"
                 f" | {lock_state}"[:249])
        return

    # Summary of all modules
    parts: list[str] = []
    for key, info in _MODULE_MAP.items():
        owner_mode = info["owner"]
        online = _bot_is_online(owner_mode, instances)
        state = "OK" if (db_ok and (online or BOT_MODE == "all")) else "WARN"
        parts.append(f"{info['label']} {state}")
    # Send in chunks if needed
    chunk, line = [], ""
    for p in parts:
        if len(line) + len(p) + 3 > 200:
            chunk.append(line)
            line = p
        else:
            line = (line + " | " + p) if line else p
    if line:
        chunk.append(line)
    for c in chunk[:3]:
        await _w(bot, user.id, ("Modules: " + c)[:249])


# ---------------------------------------------------------------------------
# /deploymentcheck [page]
# ---------------------------------------------------------------------------

async def handle_deploymentcheck(bot, user, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Manager and above only.")
        return
    instances = db.get_bot_instances()
    db_ok = _check_db()
    stale_locks = _get_stale_locks()
    conflicts = _collect_conflicts(instances)

    checks: list[tuple[str, bool, str]] = []

    # 1 DB connected
    checks.append(("DB connected", db_ok, "" if db_ok else "Cannot reach database"))

    # 2 bot_instances table
    try:
        conn = db.get_connection()
        conn.execute("SELECT 1 FROM bot_instances LIMIT 1")
        conn.close()
        checks.append(("bot_instances table", True, ""))
    except Exception as e:
        checks.append(("bot_instances table", False, str(e)[:40]))

    # 3 command ownership table
    try:
        conn = db.get_connection()
        conn.execute("SELECT 1 FROM bot_command_ownership LIMIT 1")
        conn.close()
        checks.append(("ownership table", True, ""))
    except Exception as e:
        checks.append(("ownership table", False, str(e)[:40]))

    # 4-6 Key bots online
    if not instances:
        checks.append(("Bots registered", False, "No bot_instances rows. Run a bot."))
    else:
        online_modes = {i["bot_mode"] for i in instances
                        if _bot_is_online(i["bot_mode"], instances)}
        all_mode_active = any(i["bot_mode"] == "all" for i in instances
                              if _bot_is_online("all", instances))
        for mode in ("host", "blackjack", "poker"):
            present = any(i["bot_mode"] == mode for i in instances)
            if not present and not all_mode_active:
                continue  # not configured, skip
            online = mode in online_modes or all_mode_active
            label = _MODE_NAMES.get(mode, mode.title()) + " bot"
            checks.append((label, online, "" if online else f"{label} not seen in 90s"))

    # 7 No duplicate command owners
    checks.append(("No duplicate cmd owners", len(conflicts) == 0,
                   "" if not conflicts else "; ".join(conflicts)[:60]))

    # 8 Stale locks
    checks.append(("No stale locks", len(stale_locks) == 0,
                   "" if not stale_locks else f"{len(stale_locks)} stale lock(s)"))

    # 9 BOT_MODE=all conflict
    all_active = _bot_is_online("all", instances)
    split_active = any(_bot_is_online(m, instances) for m in ("blackjack", "poker"))
    all_conflict = all_active and split_active
    checks.append(("No all+split conflict", not all_conflict,
                   "BOT_MODE=all active with split bots" if all_conflict else ""))

    # Build pages of 4 checks each
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
    total_pages = (len(checks) + 3) // 4
    page = max(1, min(page, total_pages))
    start = (page - 1) * 4
    slice_ = checks[start:start + 4]

    ok_count  = sum(1 for _, ok, _ in checks if ok)
    fail_count = len(checks) - ok_count

    # Summary header
    summary = (f"Deploy Check p{page}/{total_pages}: "
               f"DB {'OK' if db_ok else 'ERR'} | "
               f"{len(instances)} bots | "
               f"Pass {ok_count} Fail {fail_count}")
    await _w(bot, user.id, summary[:249])

    for label, ok, detail in slice_:
        flag = "✅" if ok else "⚠️"
        line = f"  {flag} {label}"
        if not ok and detail:
            line += f": {detail}"
        await _w(bot, user.id, line[:249])

    if fail_count > 0:
        await _w(bot, user.id,
                 "Use /clearstalebotlocks to clear stale locks. "
                 "/botconflicts for details.")


# ---------------------------------------------------------------------------
# /botlocks
# ---------------------------------------------------------------------------

async def handle_botlocks(bot, user) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Manager and above only.")
        return
    locks = _get_all_locks()
    if not locks:
        await _w(bot, user.id, "🔓 No active module locks.")
        return
    now = datetime.now(timezone.utc)
    parts: list[str] = []
    stale_count = 0
    for lock in locks:
        mod = lock.get("module", "?")
        bid = lock.get("bot_id", "?")
        exp = lock.get("expires_at", "")
        try:
            ex = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            if ex.tzinfo is None:
                ex = ex.replace(tzinfo=timezone.utc)
            age = int((now - ex).total_seconds())
            if age > 0:
                parts.append(f"{mod} STALE {age}s by {bid}")
                stale_count += 1
            else:
                ttl = -age
                parts.append(f"{mod} LIVE {ttl}s by {bid}")
        except Exception:
            parts.append(f"{mod} ? by {bid}")
    await _w(bot, user.id, ("Locks: " + " | ".join(parts))[:249])
    if stale_count:
        await _w(bot, user.id,
                 f"⚠️ {stale_count} stale lock(s). Use /clearstalebotlocks to remove.")


# ---------------------------------------------------------------------------
# /clearstalebotlocks
# ---------------------------------------------------------------------------

async def handle_clearstalebotlocks(bot, user) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    try:
        conn = db.get_connection()
        cur = conn.execute(
            "DELETE FROM bot_module_locks WHERE expires_at < datetime('now')"
        )
        count = cur.rowcount
        conn.commit()
        conn.close()
        if count:
            await _w(bot, user.id, f"✅ Cleared {count} stale lock(s).")
        else:
            await _w(bot, user.id, "✅ No stale locks to clear.")
    except Exception as e:
        await _w(bot, user.id, f"Error clearing locks: {str(e)[:60]}")


# ---------------------------------------------------------------------------
# /botheartbeat
# ---------------------------------------------------------------------------

async def handle_botheartbeat(bot, user) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Manager and above only.")
        return
    instances = db.get_bot_instances()
    found = next((i for i in instances if i.get("bot_id") == BOT_ID), None)
    if not found:
        await _w(bot, user.id,
                 f"🤖 ID:{BOT_ID} Mode:{BOT_MODE} — not yet registered (heartbeat pending).")
        return
    age = _age_str(found.get("last_seen_at", ""))
    status = found.get("status", "?")
    db_flag = found.get("db_connected", 1)
    err = found.get("last_error", "")
    db_str = "DB OK" if db_flag else "DB ERR"
    msg = f"🤖 ID:{BOT_ID} Mode:{BOT_MODE} | Status:{status} | {db_str} | Last:{age}"
    if err:
        msg += f" | Err:{err[:30]}"
    await _w(bot, user.id, msg[:249])


# ---------------------------------------------------------------------------
# /moduleowners [page]
# ---------------------------------------------------------------------------

async def handle_moduleowners(bot, user, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Manager and above only.")
        return
    from modules.multi_bot import _DEFAULT_COMMAND_OWNERS, _MODE_NAMES as _MN
    db_overrides = {r["command"]: r["owner_bot_mode"] for r in db.get_all_command_owners()}
    merged = {**_DEFAULT_COMMAND_OWNERS, **db_overrides}
    items = sorted(merged.items())
    PAGE_SIZE = 10
    total_pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    chunk = items[start:start + PAGE_SIZE]
    parts = [f"/{cmd}={_MN.get(owner, owner)}" for cmd, owner in chunk]
    await _w(bot, user.id,
             f"({page}/{total_pages}) Owners: " + " | ".join(parts)[:200])
    if page < total_pages:
        await _w(bot, user.id, f"Type /moduleowners {page + 1} for next page.")


# ---------------------------------------------------------------------------
# Conflict helpers
# ---------------------------------------------------------------------------

def _get_autogames_lock_holder() -> str:
    """Return the bot_id currently holding the autogames module lock, or ''."""
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT bot_id FROM bot_module_locks "
            "WHERE module='autogames' AND expires_at > datetime('now')",
        ).fetchone()
        conn.close()
        return row["bot_id"] if row else ""
    except Exception:
        return ""


def _count_conflicts(instances: list) -> int:
    return len(_collect_conflicts(instances))


def _collect_conflicts(instances: list) -> list[str]:
    conflicts: list[str] = []
    # 1. Duplicate bot_mode in registered instances
    mode_count: dict[str, int] = {}
    for inst in instances:
        m = inst.get("bot_mode", "")
        if _bot_is_online(m, instances):
            mode_count[m] = mode_count.get(m, 0) + 1
    for mode, count in mode_count.items():
        if count > 1:
            conflicts.append(f"Duplicate mode '{mode}' ({count} bots)")
    # 2. BOT_MODE=all + dedicated split bots both active (causes duplicate replies)
    all_active = _bot_is_online("all", instances)
    split_active_modes = [m for m in ("blackjack", "poker", "miner", "banker",
                                      "shopkeeper", "security", "dj", "eventhost")
                          if _bot_is_online(m, instances)]
    if all_active and split_active_modes:
        conflicts.append(
            f"BOT_MODE=all active with split bots "
            f"({', '.join(split_active_modes)}). "
            f"Use /setmainmode host to fix."
        )
    # 3. Legacy dealer + dedicated bots both active
    dealer_active = _bot_is_online("dealer", instances)
    split_casino = [m for m in ("blackjack", "poker") if _bot_is_online(m, instances)]
    if dealer_active and split_casino:
        conflicts.append("Legacy dealer bot active with Blackjack/Poker bots")
    # 4. AutoGames ownership conflict
    # "host" and "all" are known fallback modes with built-in deferral guards —
    # they skip autogames when the owner is online, so they are NOT real conflicts.
    # Only flag modes that have no deferral logic and are not the configured owner.
    _AUTOGAMES_NEVER = frozenset({
        "blackjack", "poker", "miner", "banker", "shopkeeper", "security", "dj"
    })
    _AUTOGAMES_FALLBACK = frozenset({"host", "all"})
    try:
        autogames_owner = db.get_room_setting("autogames_owner_bot_mode", "eventhost")
        if autogames_owner not in ("disabled",):
            owner_online = _bot_is_online(autogames_owner, instances)
            # Real conflict: module lock held by a non-owner bot while owner is online
            lock_holder = _get_autogames_lock_holder()
            if lock_holder and lock_holder != autogames_owner and owner_online:
                conflicts.append(
                    f"AutoGames lock held by '{lock_holder}' "
                    f"but owner '{autogames_owner}' is online. Run /fixautogames."
                )
            # Flag unexpected modes that have no built-in deferral
            true_dupes = [
                inst["bot_mode"] for inst in instances
                if _bot_is_online(inst.get("bot_mode", ""), instances)
                and inst.get("bot_mode") != autogames_owner
                and inst.get("bot_mode") not in _AUTOGAMES_NEVER
                and inst.get("bot_mode") not in _AUTOGAMES_FALLBACK
            ]
            if true_dupes and owner_online:
                conflicts.append(
                    f"AutoGames owner={autogames_owner} online but bots "
                    f"({', '.join(set(true_dupes))}) may duplicate. "
                    f"Run /fixautogames."
                )
    except Exception:
        pass
    return conflicts


# ---------------------------------------------------------------------------
# /botconflicts
# ---------------------------------------------------------------------------

async def handle_botconflicts(bot, user) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Manager and above only.")
        return
    instances = db.get_bot_instances()
    conflicts = _collect_conflicts(instances)
    if not conflicts:
        await _w(bot, user.id, "✅ No bot conflicts found.")
        return
    await _w(bot, user.id, f"⚠️ {len(conflicts)} conflict(s) found:")
    for c in conflicts[:4]:
        await _w(bot, user.id, f"  • {c}"[:249])


# ---------------------------------------------------------------------------
# /fixbotowners [force]
# ---------------------------------------------------------------------------

async def handle_fixbotowners(bot, user, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    force = len(args) > 1 and args[1].lower() == "force"
    from modules.multi_bot import _DEFAULT_COMMAND_OWNERS
    if force:
        # Overwrite all
        count = 0
        for cmd, owner_mode in _DEFAULT_COMMAND_OWNERS.items():
            try:
                db.set_command_owner_db(cmd, "", owner_mode, fallback_allowed=1)
                count += 1
            except Exception:
                pass
        await _w(bot, user.id,
                 f"✅ Force-set {count} command owners from defaults.")
    else:
        # Only fill missing
        existing = {r["command"] for r in db.get_all_command_owners()}
        added = 0
        for cmd, owner_mode in _DEFAULT_COMMAND_OWNERS.items():
            if cmd not in existing:
                try:
                    db.set_command_owner_db(cmd, "", owner_mode, fallback_allowed=1)
                    added += 1
                except Exception:
                    pass
        if added:
            await _w(bot, user.id,
                     f"✅ Added {added} missing command owner(s) from defaults.")
        else:
            await _w(bot, user.id,
                     "✅ All command owners already set. Use /fixbotowners force to overwrite.")


async def handle_dblockcheck(bot, user, args: list[str]) -> None:
    """/dblockcheck — verify DB health: WAL mode, filelock, sqlite write, processes."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Staff only.")
        return

    import sqlite3
    import config

    # 1. WAL mode check
    wal_status = "WAL=?"
    sq_status  = "sqlite ERR"
    try:
        conn = sqlite3.connect(config.DB_PATH, timeout=3)
        conn.row_factory = sqlite3.Row
        jm_row = conn.execute("PRAGMA journal_mode").fetchone()
        jm     = jm_row[0].lower() if jm_row else "?"
        wal_status = "WAL ON" if jm == "wal" else f"WAL={jm}"
        # Quick write test
        conn.execute(
            "CREATE TABLE IF NOT EXISTS _dblocktest (x INTEGER)")
        conn.execute("INSERT INTO _dblocktest VALUES (1)")
        conn.execute("DELETE FROM _dblocktest")
        conn.commit()
        conn.close()
        sq_status = "sqlite free"
    except Exception as _dbe:
        sq_status = f"sqlite ERR:{str(_dbe)[:20]}"

    # 2. filelock check
    fl_status = "filelock ERR"
    try:
        from filelock import FileLock, Timeout as _FLT
        _tlock = FileLock(config.DB_PATH + ".write.lock", timeout=2)
        _tlock.acquire(timeout=2)
        _tlock.release()
        fl_status = "filelock OK"
    except Exception:
        fl_status = "filelock ERR"

    # 3. Multi-bot process check
    proc_status = "multi-bot ERR"
    try:
        conn2 = db.get_connection()
        row   = conn2.execute(
            "SELECT COUNT(*) FROM bot_instances "
            "WHERE last_heartbeat_at >= datetime('now', '-2 minutes')"
        ).fetchone()
        conn2.close()
        live = row[0] if row else 0
        proc_status = f"multi-bot OK ({live} live)"
    except Exception:
        pass

    msg = f"DB: {wal_status} | {fl_status} | {sq_status} | Processes: {proc_status}"
    await _w(bot, user.id, msg[:249])
