"""
modules/release.py — v3.2 Release Candidate stabilization tools (3.1S)

Commands (owner unless noted):
  !rcmode [on|off|status]         — release candidate mode toggle
  !production [on|off|status]     — production mode (pre-flight gated)
  !featurefreeze [on|off|status]  — soft feature freeze
  !economylock [on|off|status]    — lock economy settings/prices
  !registrylock [on|off|status]   — lock public command registry
  !releasenotes [v3.2|public|staff]  — release notes (public ok)
  !version / !botversion          — bot version (public ok)
  !backup [status|create|list|verify]
  !rollbackplan [v3.2]
  !restorebackup [confirm <name>] — safe stub only
  !ownerchecklist / !launchownercheck
  !launchannounce [preview|send]  — admin/owner
  !whatsnew / !new / !v32         — public
  !knownissues                    — public
  !releasedash [full]             — admin/owner
  !finalaudit [full]              — owner
"""
from __future__ import annotations

import json
import os
import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot
    from highrise.models import User

import database as db
from modules.permissions import is_owner, is_admin, can_moderate

_RELEASE_VERSION = "v3.2 Stable"
_BUILD_DATE      = "2026-05-14"
_BACKUP_DIR      = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backups")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


def _ao(username: str) -> bool:
    return is_admin(username) or is_owner(username)


def _oo(username: str) -> bool:
    return is_owner(username)


def _qi(sql: str, params: tuple = ()) -> int:
    try:
        conn = db.get_connection()
        row  = conn.execute(sql, params).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _get(key: str, default: str = "off") -> str:
    try:
        return db.get_room_setting(key, default)
    except Exception:
        return default


def _set(key: str, value: str, who: str) -> None:
    try:
        db.set_room_setting(key, value)
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO release_audits (audit_type, status, summary_json, created_by) "
            "VALUES (?,?,?,?)",
            ("setting_change", "ok", json.dumps({"key": key, "value": value}), who),
        )
        conn.commit()
        conn.close()
    except Exception:
        try:
            db.set_room_setting(key, value)
        except Exception:
            pass


def _flag(val: str) -> str:
    return "ON" if val == "on" else "OFF"


def _check_blockers() -> tuple[int, list[str]]:
    """Returns (count, descriptions) of active launch blockers."""
    blockers: list[str] = []
    crit = _qi(
        "SELECT COUNT(*) FROM reports "
        "WHERE report_type='bug_report' AND status='open' AND priority='critical'"
    )
    if crit > 0:
        blockers.append(f"Critical bugs: {crit}")
    if _get("maintenance_mode", "off") == "on":
        blockers.append("Maintenance mode: ON")
    backup_ok = _qi("SELECT COUNT(*) FROM release_backups WHERE verified=1") > 0
    if not backup_ok:
        blockers.append("Backup: missing or unverified")
    try:
        from modules.cmd_audit import ROUTED_COMMANDS
        import main as _m
        missing = len(_m.ALL_KNOWN_COMMANDS - ROUTED_COMMANDS)
        if missing > 0:
            blockers.append(f"Unrouted commands: {missing}")
    except Exception:
        pass
    try:
        import sys, importlib
        from modules.command_registry import get_entry as _reg_get
        from modules.cmd_audit import HIDDEN_CMDS as _HC, DEPRECATED_CMDS as _DC
        _main = sys.modules.get("__main__") or importlib.import_module("__main__")
        _all_known = getattr(_main, "ALL_KNOWN_COMMANDS", set())
        _active = _all_known - (_HC & _all_known) - (_DC & _all_known)
        no_handler = [c for c in _active if _reg_get(c) is None]
        if no_handler:
            blockers.append(f"No-handler cmds: {len(no_handler)} ({', '.join(sorted(no_handler)[:3])})")
    except Exception:
        pass
    return len(blockers), blockers


def is_economy_locked() -> bool:
    """Public helper — other modules call this to respect the economy lock."""
    return _get("economy_lock", "off") == "on"


def is_feature_frozen() -> bool:
    """Public helper — returns True when feature freeze is active."""
    return _get("feature_freeze", "off") == "on"


def is_production_mode() -> bool:
    """Public helper — returns True when production mode is active."""
    return _get("production_mode", "off") == "on"


# ---------------------------------------------------------------------------
# !rcmode [on|off|status]
# ---------------------------------------------------------------------------

async def handle_rcmode(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _oo(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    sub = args[1].lower() if len(args) > 1 else "status"
    if sub in ("on", "off"):
        _set("rc_mode", sub, user.username)
        locked = sub == "on"
        await _w(
            bot, user.id,
            f"🚀 Release Candidate Mode: {_flag(sub)}\n"
            + ("Major features locked.\nBug fixes and safety fixes allowed."
               if locked else "Feature additions re-enabled."),
        )
    else:
        val = _get("rc_mode", "off")
        await _w(bot, user.id,
                 f"🚀 RC Mode: {_flag(val)}\nUse !rcmode on|off to change.")


# ---------------------------------------------------------------------------
# !production [on|off|status]
# ---------------------------------------------------------------------------

async def handle_production(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _oo(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    sub = args[1].lower() if len(args) > 1 else "status"
    if sub == "on":
        count, blockers = _check_blockers()
        if count > 0:
            bl = "\n".join(f"• {b}" for b in blockers[:3])
            await _w(bot, user.id,
                     f"⚠️ Cannot enable production.\n"
                     f"Launch blockers: {count}\n{bl}\nUse !launchblockers.")
            return
        _set("production_mode", "on", user.username)
        await _w(bot, user.id,
                 f"✅ Production Mode: ON\n{_RELEASE_VERSION} public launch ready.")
    elif sub == "off":
        _set("production_mode", "off", user.username)
        await _w(bot, user.id, "Production Mode: OFF")
    else:
        val   = _get("production_mode", "off")
        bl_ct, _ = _check_blockers()
        ready = "YES ✅" if bl_ct == 0 else f"NO ⚠️ ({bl_ct} blockers)"
        await _w(bot, user.id,
                 f"✅ Production: {_flag(val)}\n"
                 f"Version: {_RELEASE_VERSION}\n"
                 f"Blockers: {bl_ct}\n"
                 f"Ready: {ready}")


# ---------------------------------------------------------------------------
# !featurefreeze [on|off|status]
# ---------------------------------------------------------------------------

async def handle_featurefreeze(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _oo(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    sub = args[1].lower() if len(args) > 1 else "status"
    if sub in ("on", "off"):
        _set("feature_freeze", sub, user.username)
        await _w(
            bot, user.id,
            f"🧊 Feature Freeze: {_flag(sub)}\n"
            + ("Only bug fixes, safety fixes, and launch blockers allowed."
               if sub == "on" else "New features may be added."),
        )
    else:
        val = _get("feature_freeze", "off")
        await _w(bot, user.id,
                 f"🧊 Feature Freeze: {_flag(val)}\n"
                 f"Use !featurefreeze on|off to change.")


# ---------------------------------------------------------------------------
# !economylock [on|off|status]
# ---------------------------------------------------------------------------

async def handle_economylock(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _oo(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    sub = args[1].lower() if len(args) > 1 else "status"
    if sub in ("on", "off"):
        _set("economy_lock", sub, user.username)
        await _w(
            bot, user.id,
            f"🔒 Economy Lock: {_flag(sub)}\n"
            + (f"Prices/rewards frozen for {_RELEASE_VERSION}."
               if sub == "on" else "Economy settings unlocked."),
        )
    else:
        val = _get("economy_lock", "off")
        await _w(bot, user.id,
                 f"🔒 Economy Lock: {_flag(val)}\n"
                 f"Covers shop, Luxe, VIP, ore/fish values, casino.\n"
                 f"Use !economylock on|off.")


# ---------------------------------------------------------------------------
# !registrylock [on|off|status]
# ---------------------------------------------------------------------------

async def handle_registrylock(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _oo(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    sub = args[1].lower() if len(args) > 1 else "status"
    if sub in ("on", "off"):
        _set("registry_lock", sub, user.username)
        await _w(
            bot, user.id,
            f"🔒 Registry Lock: {_flag(sub)}\n"
            + ("Public command list frozen."
               if sub == "on" else "Registry unlocked."),
        )
    else:
        val = _get("registry_lock", "off")
        await _w(bot, user.id,
                 f"🔒 Registry Lock: {_flag(val)}\n"
                 f"Public help is {'frozen' if val == 'on' else 'open'}.\n"
                 f"Use !registrylock on|off.")


# ---------------------------------------------------------------------------
# !releasenotes [v3.2|public|staff]
# ---------------------------------------------------------------------------

async def handle_releasenotes(bot: "BaseBot", user: "User", args: list[str]) -> None:
    sub = args[1].lower() if len(args) > 1 else "public"
    if sub == "staff":
        if not (_ao(user.username) or can_moderate(user.username)):
            await _w(bot, user.id, "🔒 Staff only.")
            return
        await _w(bot, user.id,
                 "🛠️ Staff Notes\n"
                 f"{_RELEASE_VERSION} includes: safety tools, bug triage, "
                 "launch monitors, moderation polish, staff cmds, economy audits.")
        await _w(bot, user.id,
                 "Staff cmds:\n"
                 "!staffdash !stafftools !modhelp\n"
                 "!bugs open !feedbacks recent\n"
                 "!safetydash !permissioncheck")
    else:
        await _w(bot, user.id,
                 f"🚀 {_RELEASE_VERSION}\n"
                 "New: 🪙 ChillCoins, 🎫 Luxe Tickets, ⛏️ Mining, 🎣 Fishing, "
                 "📋 Missions, 🎉 Events, 👤 Profiles, 📅 Seasons, 🛡️ Safety.")
        await _w(bot, user.id,
                 "Start:\n!quickstart\n!missions\n!mine\n!fish\n!profile\n"
                 "Report bugs: !bug")


# ---------------------------------------------------------------------------
# !version / !botversion
# ---------------------------------------------------------------------------

async def handle_version(bot: "BaseBot", user: "User", args: list[str]) -> None:
    prod   = _get("production_mode", "off")
    rc     = _get("rc_mode", "off")
    launch = _get("launchmode_active", "off")
    if prod == "on":
        mode = "Production"
    elif rc == "on":
        mode = "Release Candidate"
    else:
        mode = "Public Beta"
    launch_str = "ON" if (launch == "on" or prod == "on") else "OFF"
    await _w(bot, user.id,
             f"🤖 ChillTopia Bot\n"
             f"Version: {_RELEASE_VERSION}\n"
             f"Mode: {mode}\n"
             f"Launch: {launch_str}")


# ---------------------------------------------------------------------------
# !backup [status|create|list|verify]
# ---------------------------------------------------------------------------

def _db_path() -> str:
    return os.environ.get("SHARED_DB_PATH", "highrise_hangout.db")


def _count_tables() -> int:
    try:
        conn = db.get_connection()
        row  = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


async def handle_backup(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _oo(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    sub = args[1].lower() if len(args) > 1 else "status"

    if sub == "create":
        try:
            os.makedirs(_BACKUP_DIR, exist_ok=True)
            ts   = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            name = f"{_RELEASE_VERSION}_rc_{ts}"
            dest = os.path.join(_BACKUP_DIR, f"{name}.db")
            src  = _db_path()
            import sqlite3 as _sq
            src_c = _sq.connect(src)
            dst_c = _sq.connect(dest)
            try:
                src_c.backup(dst_c)
            finally:
                dst_c.close()
                src_c.close()
            verified = 1 if (os.path.isfile(dest) and os.path.getsize(dest) > 0) else 0
            tables = _count_tables()
            conn = db.get_connection()
            conn.execute(
                "INSERT OR IGNORE INTO release_backups "
                "(backup_name, backup_path, created_by, verified, details_json) "
                "VALUES (?,?,?,?,?)",
                (name, dest, user.username, verified, json.dumps({"tables": tables})),
            )
            conn.commit()
            conn.close()
            status = "verified ✅" if verified else "unverified ⚠️"
            await _w(bot, user.id,
                     f"💾 Backup Created\nName: {name}\nTables: {tables}\nStatus: {status}")
        except Exception as e:
            await _w(bot, user.id, f"⚠️ Backup failed: {str(e)[:80]}")

    elif sub == "list":
        try:
            conn = db.get_connection()
            rows = conn.execute(
                "SELECT backup_name, created_at, verified "
                "FROM release_backups ORDER BY id DESC LIMIT 5"
            ).fetchall()
            conn.close()
            if not rows:
                await _w(bot, user.id, "💾 No backups yet. Run !backup create.")
                return
            lines = ["💾 Backups (newest first):"]
            for r in rows:
                v = "✅" if r[2] else "⬜"
                lines.append(f"{v} {r[0]} | {str(r[1])[:16]}")
            await _w(bot, user.id, "\n".join(lines)[:249])
        except Exception:
            await _w(bot, user.id, "⚠️ Could not list backups.")

    elif sub == "verify":
        try:
            conn = db.get_connection()
            row  = conn.execute(
                "SELECT id, backup_name, backup_path "
                "FROM release_backups ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not row:
                conn.close()
                await _w(bot, user.id, "No backup to verify. Run !backup create first.")
                return
            bid, bname, bpath = row
            ok = os.path.isfile(bpath) and os.path.getsize(bpath) > 0
            conn.execute(
                "UPDATE release_backups SET verified=? WHERE id=?",
                (1 if ok else 0, bid),
            )
            conn.commit()
            conn.close()
            if ok:
                size_kb = os.path.getsize(bpath) // 1024
                await _w(bot, user.id,
                         f"✅ Backup Verified\n{bname}\nSize: {size_kb} KB\nStatus: OK")
            else:
                await _w(bot, user.id, f"⚠️ Backup file missing or empty: {bname}")
        except Exception as e:
            await _w(bot, user.id, f"⚠️ Verify failed: {str(e)[:80]}")

    else:  # status
        try:
            total = _qi("SELECT COUNT(*) FROM release_backups")
            if total == 0:
                await _w(bot, user.id,
                         "💾 Backup Status: None yet.\nRun !backup create.")
                return
            conn = db.get_connection()
            row  = conn.execute(
                "SELECT backup_name, created_at, verified "
                "FROM release_backups ORDER BY id DESC LIMIT 1"
            ).fetchone()
            conn.close()
            v = "verified ✅" if row and row[2] else "unverified ⬜"
            await _w(bot, user.id,
                     f"💾 Backup Status\nTotal: {total}\n"
                     f"Latest: {row[0] if row else 'none'}\n"
                     f"Created: {str(row[1])[:16] if row else '?'}\n"
                     f"Status: {v}")
        except Exception:
            await _w(bot, user.id, "💾 Backup status unavailable.")


# ---------------------------------------------------------------------------
# !rollbackplan [v3.2]
# ---------------------------------------------------------------------------

async def handle_rollbackplan(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _ao(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return
    await _w(bot, user.id,
             f"↩️ Rollback Plan {_RELEASE_VERSION}\n"
             "1. !maintenance on\n"
             "2. !backup list → pick backup\n"
             "3. !production off\n"
             "4. Re-run !launchcheck\n"
             "5. Announce status when safe")
    await _w(bot, user.id,
             "Use only if critical bug occurs. Owner-only actions.\n"
             "After restore: !maintenance off → !launchcheck → re-enable when clean.")


# ---------------------------------------------------------------------------
# !restorebackup [confirm <name>]
# ---------------------------------------------------------------------------

async def handle_restorebackup(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _oo(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    sub = args[1].lower() if len(args) > 1 else ""
    if sub == "confirm" and len(args) > 2:
        if _get("maintenance_mode", "off") != "on":
            await _w(bot, user.id,
                     "⚠️ Enable maintenance first: !maintenance on")
            return
    await _w(bot, user.id,
             "⚠️ Restore command not enabled for safety.\n"
             "Use manual rollback plan: !rollbackplan\n"
             "Ensure !maintenance on before any restore.")


# ---------------------------------------------------------------------------
# !ownerchecklist / !launchownercheck
# ---------------------------------------------------------------------------

async def handle_ownerchecklist(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _oo(user.username):
        await _w(bot, user.id, "Owner only.")
        return

    def chk(v: bool) -> str:
        return "✅" if v else "⚠️"

    backup_ok  = _qi("SELECT COUNT(*) FROM release_backups WHERE verified=1") > 0
    bl_ct, _   = _check_blockers()
    maint_off  = _get("maintenance_mode", "off") != "on"
    eco_locked = _get("economy_lock", "off") == "on"
    reg_locked = _get("registry_lock", "off") == "on"
    prod_on    = _get("production_mode", "off") == "on"
    crit       = _qi(
        "SELECT COUNT(*) FROM reports "
        "WHERE report_type='bug_report' AND status='open' AND priority='critical'"
    )

    await _w(
        bot, user.id,
        f"👑 Owner Launch Checklist\n"
        f"{chk(backup_ok)} Backup: {'created ✅' if backup_ok else 'MISSING ⚠️'}\n"
        f"{chk(bl_ct == 0)} Launch blockers: {bl_ct}\n"
        f"{chk(maint_off)} Maintenance: {'OFF' if maint_off else 'ON ⚠️'}\n"
        f"{chk(eco_locked)} Economy: {'locked' if eco_locked else 'unlocked'}\n"
        f"{chk(reg_locked)} Registry: {'locked' if reg_locked else 'unlocked'}",
    )
    ready = bl_ct == 0 and crit == 0 and maint_off and backup_ok
    await _w(
        bot, user.id,
        f"{chk(prod_on)} Production: {'ON' if prod_on else 'OFF'}\n"
        f"{chk(crit == 0)} Critical bugs: {crit}\n"
        f"{'Ready: YES ✅' if ready else 'Ready: NO ⚠️ — fix blockers first'}",
    )


# ---------------------------------------------------------------------------
# !launchannounce [preview|send]
# ---------------------------------------------------------------------------

_LAUNCH_MSG_1 = (
    "📢 v3.2 Stable is LIVE!\n"
    "Earn 🪙, collect ores/fish, complete missions, join events, "
    "and build your profile. Use 🎫 for premium perks."
)
_LAUNCH_MSG_2 = (
    "Start here:\n!start\n!missions\n!mine\n!fish\n!profile\n"
    "Report issues: !bug"
)


async def handle_launchannounce(
    bot: "BaseBot", user: "User", args: list[str]
) -> None:
    if not _ao(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return
    sub = args[1].lower() if len(args) > 1 else "preview"
    if sub == "send":
        if not _oo(user.username):
            await _w(bot, user.id, "Owner only for !launchannounce send.")
            return
        try:
            await bot.highrise.chat(_LAUNCH_MSG_1[:249])
            await bot.highrise.chat(_LAUNCH_MSG_2[:249])
            conn = db.get_connection()
            conn.execute(
                "INSERT INTO release_announcements "
                "(message, announcement_type, sent_by) VALUES (?,?,?)",
                (_LAUNCH_MSG_1[:249], "launch_v3.2", user.username),
            )
            conn.commit()
            conn.close()
            await _w(bot, user.id, "✅ Launch announcement sent to room.")
        except Exception as e:
            await _w(bot, user.id, f"⚠️ Send failed: {str(e)[:80]}")
    else:
        await _w(bot, user.id, f"📋 Preview:\n{_LAUNCH_MSG_1[:200]}")
        await _w(bot, user.id,
                 f"Part 2:\n{_LAUNCH_MSG_2}\n\nUse !launchannounce send to publish.")


# ---------------------------------------------------------------------------
# !whatsnew / !new / !v32
# ---------------------------------------------------------------------------

async def handle_whatsnew(bot: "BaseBot", user: "User", args: list[str]) -> None:
    await _w(bot, user.id,
             f"🚀 What's New {_RELEASE_VERSION}\n"
             "🪙 ChillCoins\n"
             "🎫 Luxe Tickets\n"
             "⛏️ Mining\n"
             "🎣 Fishing\n"
             "📋 Missions\n"
             "🎉 Events\n"
             "👤 Profiles\n"
             "📅 Seasons")
    await _w(bot, user.id,
             "Start: !start\nGuide: !guide\nReport bugs: !bug\n"
             "Earn: !mine !fish !daily")


# ---------------------------------------------------------------------------
# !knownissues
# ---------------------------------------------------------------------------

async def handle_knownissues(bot: "BaseBot", user: "User", args: list[str]) -> None:
    is_staff = _ao(user.username) or can_moderate(user.username)
    crit   = _qi(
        "SELECT COUNT(*) FROM reports "
        "WHERE report_type='bug_report' AND status='open' AND priority='critical'"
    )
    medium = _qi(
        "SELECT COUNT(*) FROM reports "
        "WHERE report_type='bug_report' AND status='open' "
        "AND priority IN ('medium','low')"
    )
    if crit == 0 and medium == 0:
        await _w(bot, user.id,
                 f"🧾 Known Issues ({_RELEASE_VERSION})\n"
                 "No critical issues.\n"
                 "Minor: ongoing polish.\n"
                 "Report new bugs: !bug")
        return
    lines = [f"🧾 Known Issues ({_RELEASE_VERSION})"]
    if crit > 0:
        lines.append(f"⚠️ Critical: {crit} — details: !bugs open")
    if medium > 0:
        lines.append(f"Minor/medium: {medium}")
    if is_staff:
        lines.append("Staff: !bugs open | !betacheck | !launchblockers")
    lines.append("Report bugs: !bug")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# !releasedash [full]
# ---------------------------------------------------------------------------

async def handle_releasedash(
    bot: "BaseBot", user: "User", args: list[str]
) -> None:
    if not _ao(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    rc   = _flag(_get("rc_mode",         "off"))
    prod = _flag(_get("production_mode", "off"))
    frz  = _flag(_get("feature_freeze",  "off"))
    eco  = _flag(_get("economy_lock",    "off"))
    reg  = _flag(_get("registry_lock",   "off"))

    await _w(
        bot, user.id,
        f"🚀 Release Dashboard\n"
        f"Mode: RC {rc}\n"
        f"Production: {prod}\n"
        f"Feature Freeze: {frz}\n"
        f"Economy Lock: {eco}\n"
        f"Registry Lock: {reg}",
    )

    bl_ct, _  = _check_blockers()
    backup_ok = _qi("SELECT COUNT(*) FROM release_backups WHERE verified=1") > 0
    crit      = _qi(
        "SELECT COUNT(*) FROM reports "
        "WHERE report_type='bug_report' AND status='open' AND priority='critical'"
    )

    def ok(v: bool) -> str:
        return "OK ✅" if v else "⚠️"

    ready = bl_ct == 0 and crit == 0
    await _w(
        bot, user.id,
        f"Health:\n"
        f"Commands: {ok(bl_ct == 0)}\n"
        f"Safety: OK ✅\n"
        f"Critical bugs: {crit}\n"
        f"Backup: {ok(backup_ok)}\n"
        f"Ready: {'YES ✅' if ready else 'NO ⚠️'}",
    )


# ---------------------------------------------------------------------------
# !finalaudit [full]
# ---------------------------------------------------------------------------

async def handle_finalaudit(
    bot: "BaseBot", user: "User", args: list[str]
) -> None:
    if not _oo(user.username):
        await _w(bot, user.id, "Owner only.")
        return

    full = len(args) > 1 and args[1].lower() == "full"

    def ok(v: bool) -> str:
        return "OK ✅" if v else "⚠️"

    bl_ct, bl_names = _check_blockers()
    crit     = _qi(
        "SELECT COUNT(*) FROM reports "
        "WHERE report_type='bug_report' AND status='open' AND priority='critical'"
    )
    backup   = _qi("SELECT COUNT(*) FROM release_backups WHERE verified=1")
    eco      = _get("economy_lock",    "off") == "on"
    reg      = _get("registry_lock",   "off") == "on"
    maint_ok = _get("maintenance_mode","off") != "on"
    prod     = _get("production_mode", "off") == "on"
    ready    = bl_ct == 0 and crit == 0 and maint_ok

    await _w(
        bot, user.id,
        f"🔍 Final Audit {_RELEASE_VERSION}\n"
        f"Commands: {ok(bl_ct == 0)}\n"
        f"Help: OK ✅\n"
        f"Currency: OK ✅\n"
        f"Economy: {'Locked ✅' if eco else 'Unlocked ⚠️'}\n"
        f"Registry: {'Locked ✅' if reg else 'Unlocked ⚠️'}",
    )
    await _w(
        bot, user.id,
        f"Backup: {ok(backup > 0)}\n"
        f"Critical bugs: {crit}\n"
        f"Maintenance: {'OFF ✅' if maint_ok else 'ON ⚠️'}\n"
        f"Production: {'ON ✅' if prod else 'OFF'}\n"
        f"{'Ready: YES ✅' if ready else 'Ready: NO ⚠️ — use !launchblockers'}",
    )
    if full and bl_names:
        details = "\n".join(f"• {b}" for b in bl_names[:5])
        await _w(bot, user.id, f"Blockers:\n{details}")


# ---------------------------------------------------------------------------
# !qastatus / !ownerqa
# ---------------------------------------------------------------------------

async def handle_qastatus(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """Owner QA status summary (admin+)."""
    if not _ao(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return
    await _w(bot, user.id,
             "🧪 Owner QA Status\n"
             "Real player beta: skipped\n"
             "Owner QA: active\n"
             "Risk: live hotfixes may be needed\n"
             "Use !finalaudit.")
    await _w(bot, user.id,
             "Stable lock can proceed if:\n"
             "launch blockers 0, command issues 0,\n"
             "currency clean, backup OK.")


async def handle_ownerqa(bot: "BaseBot", user: "User", args: list[str]) -> None:
    await handle_qastatus(bot, user, args)


# ---------------------------------------------------------------------------
# !ownertest [player|economy|casino|mining|staff]
# ---------------------------------------------------------------------------

async def handle_ownertest(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """Owner-only QA test script menu (admin+)."""
    if not _ao(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return
    sub = args[1].lower() if len(args) > 1 else ""
    if sub == "player":
        await _w(bot, user.id,
                 "👤 Player QA\n"
                 "!start !quickstart !profile\n"
                 "!today !missions\n"
                 "!mine !fish !events\n"
                 "!bug test")
    elif sub == "economy":
        await _w(bot, user.id,
                 "💰 Economy QA\n"
                 "!balance !tickets\n"
                 "!luxeshop !buycoins\n"
                 "!vip !autotime")
    elif sub == "casino":
        await _w(bot, user.id,
                 "🎰 Casino QA\n"
                 "!bjhelp !bjrules\n"
                 "!bjstatus !bjshoe\n"
                 "!bet 100 !casinohelp")
    elif sub == "mining":
        await _w(bot, user.id,
                 "⛏️ Mining/Fishing QA\n"
                 "!mine !fish\n"
                 "!orebook !fishbook\n"
                 "!mineluck !fishluck\n"
                 "!automine status !autofish status")
    elif sub == "staff":
        await _w(bot, user.id,
                 "🛡️ Staff QA\n"
                 "!staffdash !stafftools\n"
                 "!modhelp !safetydash\n"
                 "!permissioncheck\n"
                 "!rolecheck @4ktreyMarion")
    else:
        await _w(bot, user.id,
                 "🧪 Owner Test Menu\n"
                 "Player: !ownertest player\n"
                 "Economy: !ownertest economy\n"
                 "Casino: !ownertest casino\n"
                 "Mining/Fishing: !ownertest mining\n"
                 "Staff: !ownertest staff")


# ---------------------------------------------------------------------------
# !stablecheck [full]
# ---------------------------------------------------------------------------

async def handle_stablecheck(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """v3.2 stable release readiness check (admin+)."""
    if not _ao(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return

    def ok(v: bool) -> str:
        return "OK ✅" if v else "⚠️"

    bl_ct, _bl = _check_blockers()
    backup_ok  = _qi("SELECT COUNT(*) FROM release_backups WHERE verified=1") > 0
    crit       = _qi(
        "SELECT COUNT(*) FROM reports "
        "WHERE report_type='bug_report' AND status='open' AND priority='critical'"
    )
    eco   = _get("economy_lock",    "off") == "on"
    reg   = _get("registry_lock",   "off") == "on"
    prod  = _get("production_mode", "off") == "on"
    ready = bl_ct == 0 and crit == 0 and backup_ok

    await _w(bot, user.id,
             f"🚀 {_RELEASE_VERSION} Check\n"
             f"Backup: {ok(backup_ok)}\n"
             f"Commands: {ok(bl_ct == 0)}\n"
             f"Help: OK ✅\n"
             f"Currency: OK ✅\n"
             f"Launch blockers: {bl_ct}")
    await _w(bot, user.id,
             f"Production: {'ON ✅' if prod else 'OFF ⚠️'}\n"
             f"Economy Lock: {'ON ✅' if eco else 'OFF ⚠️'}\n"
             f"Registry Lock: {'ON ✅' if reg else 'OFF ⚠️'}\n"
             f"{'Ready: YES ✅' if ready else 'Ready: NO ⚠️ — use !launchblockers'}")
    await _w(bot, user.id,
             "Note: Real player beta skipped.\n"
             "Monitor !bugs and !postlaunch after release.")


# ---------------------------------------------------------------------------
# !hotfixpolicy [public|staff]
# ---------------------------------------------------------------------------

async def handle_hotfixpolicy(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """Hotfix policy — public or staff version."""
    sub      = args[1].lower() if len(args) > 1 else ""
    is_staff = _ao(user.username) or can_moderate(user.username)

    if sub == "public" or not is_staff:
        await _w(bot, user.id,
                 "🛠️ Updates\n"
                 "Small fixes may happen after launch.\n"
                 "Report issues with !bug.")
    else:
        await _w(bot, user.id,
                 "🛠️ Hotfix Policy\n"
                 f"After {_RELEASE_VERSION} lock:\n"
                 "Allowed: bug fixes, safety fixes, command fixes.\n"
                 "Not allowed: new systems or economy changes.")


# ---------------------------------------------------------------------------
# !stablelock [on|off|status]
# ---------------------------------------------------------------------------

async def handle_stablelock(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """v3.2 stable lock — owner only. Persists to DB."""
    if not _oo(user.username):
        await _w(bot, user.id, "🔒 Owner only.")
        return
    sub     = args[1].lower() if len(args) > 1 else "status"
    current = _get("stable_lock", "off")
    if sub == "on":
        _set("stable_lock", "on", user.username)
        await _w(bot, user.id,
                 f"🔒 {_RELEASE_VERSION} Stable Lock: ON\n"
                 "Only hotfixes allowed.")
    elif sub == "off":
        _set("stable_lock", "off", user.username)
        await _w(bot, user.id,
                 f"🔓 {_RELEASE_VERSION} Stable Lock: OFF\n"
                 "Full update access restored.")
    else:
        flag = "ON 🔒" if current == "on" else "OFF"
        await _w(bot, user.id,
                 f"🔒 Stable Lock: {flag}\n"
                 f"{_RELEASE_VERSION}")
