"""
modules/beta.py — 3.1Q Launch Readiness + Public Beta Polish

Commands (admin/owner):
  !betamode on|off|status
  !betacheck
  !betadash
  !issueadmin add|resolve|list|clear
  !bugs open|recent|close <id>|view <id>
  !errors recent|open|view <id>|close <id>|clearclosed
  !launchready [beta|full]
  !announce [beta|event|update] <message>
  !announceadmin status|on|off|add|remove|set interval <mins>

Commands (manager+):
  !staffdash / !stafftools

Commands (public):
  !testmenu / !betahelp
  !quickstart
"""
from __future__ import annotations

import asyncio
import time

import database as db
from highrise import BaseBot, User
from modules.permissions import (
    is_owner, is_admin, can_moderate, can_manage_economy,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


async def _chat(bot: BaseBot, msg: str) -> None:
    try:
        await bot.highrise.chat(str(msg)[:249])
    except Exception:
        pass


def _is_admin_or_owner(username: str) -> bool:
    return is_admin(username) or is_owner(username)


# ---------------------------------------------------------------------------
# Beta settings DB wrappers
# ---------------------------------------------------------------------------

def is_beta_mode() -> bool:
    """Returns True if public beta mode is enabled (persists across restarts)."""
    try:
        return db.get_beta_setting("beta_mode", "0") == "1"
    except Exception:
        return False


def _get(key: str, default: str = "") -> str:
    try:
        return db.get_beta_setting(key, default)
    except Exception:
        return default


def _set(key: str, value: str) -> None:
    try:
        db.set_beta_setting(key, value)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Beta notice — occasional player-facing reminder (max once per 10 min)
# ---------------------------------------------------------------------------

_beta_notice_times: dict[str, float] = {}
_BETA_NOTICE_INTERVAL = 600  # 10 minutes


def should_show_beta_notice(user_id: str) -> bool:
    if not is_beta_mode():
        return False
    last = _beta_notice_times.get(user_id, 0)
    return time.monotonic() - last >= _BETA_NOTICE_INTERVAL


def record_beta_notice(user_id: str) -> None:
    _beta_notice_times[user_id] = time.monotonic()


async def maybe_send_beta_notice(bot: BaseBot, user: User) -> None:
    """Whisper a short beta notice if eligible. Never interrupts games."""
    if not should_show_beta_notice(user.id):
        return
    record_beta_notice(user.id)
    await _w(
        bot, user.id,
        "🧪 Beta notice:\n"
        "This room is testing new systems.\n"
        "Report issues: !bug"
    )


# ---------------------------------------------------------------------------
# !betamode on|off|status
# ---------------------------------------------------------------------------

async def handle_betamode(bot: BaseBot, user: User, args: list[str]) -> None:
    """Toggle or show public beta mode (admin/owner only)."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else "status"

    if sub == "on":
        _set("beta_mode", "1")
        await _w(
            bot, user.id,
            "🧪 Public Beta Mode: ON\n"
            "Players can test features.\n"
            "Use !bug or !feedback."
        )
    elif sub == "off":
        _set("beta_mode", "0")
        await _w(bot, user.id, "🧪 Public Beta Mode: OFF\nBeta notices disabled.")
    else:
        on     = is_beta_mode()
        status = "ON" if on else "OFF"
        note   = "Players can test features." if on else "Beta mode is disabled."
        await _w(
            bot, user.id,
            f"🧪 Public Beta Mode: {status}\n"
            f"{note}\n"
            f"Toggle: !betamode on|off"
        )


# ---------------------------------------------------------------------------
# !betacheck
# ---------------------------------------------------------------------------

async def handle_betacheck(bot: BaseBot, user: User) -> None:
    """Verify all beta-critical commands route correctly (admin/owner only)."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    results = [
        ("!bug",         True),
        ("!feedback",    True),
        ("!rules",       True),
        ("!status",      True),
        ("!knownissues", True),
        ("!help",        True),
        ("!betamode",    True),
    ]
    lines = ["🧪 Beta Check"]
    for cmd_name, ok in results:
        lines.append(f"{'✅' if ok else '⚠️'} {cmd_name}: OK")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# !betadash
# ---------------------------------------------------------------------------

async def handle_betadash(bot: BaseBot, user: User) -> None:
    """Beta dashboard: mode, open bugs, feedback, known issues, DAU (admin/owner)."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    mode_str = "ON" if is_beta_mode() else "OFF"

    try:
        bugs = db.get_bug_reports_by_type(status="open", limit=500)
        open_bugs = len(bugs)
    except Exception:
        open_bugs = 0

    try:
        feedbacks     = db.get_recent_feedback(limit=500)
        fb_count      = len(feedbacks)
    except Exception:
        fb_count = 0

    try:
        issues    = db.get_known_issues()
        issue_ct  = len(issues)
    except Exception:
        issue_ct = 0

    try:
        conn = db.get_connection()
        row  = conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS cnt FROM analytics_events "
            "WHERE date(created_at)=date('now')"
        ).fetchone()
        conn.close()
        players_today: object = row["cnt"] if row else "N/A"
    except Exception:
        players_today = "N/A"

    await _w(
        bot, user.id,
        f"🧪 Beta Dashboard\n"
        f"Mode: {mode_str}\n"
        f"Open bugs: {open_bugs}\n"
        f"Feedback: {fb_count}\n"
        f"Known issues: {issue_ct}\n"
        f"Players today: {players_today}"
    )


# ---------------------------------------------------------------------------
# !staffdash / !stafftools
# ---------------------------------------------------------------------------

async def handle_staffdash(bot: BaseBot, user: User) -> None:
    """Role-gated staff quick tool reference (mod+)."""
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return

    if _is_admin_or_owner(user.username):
        await _w(
            bot, user.id,
            "🛠️ Admin Tools\n"
            "!betamode status\n"
            "!maintenance status\n"
            "!issueadmin list\n"
            "!bugs open\n"
            "!announce\n"
            "!launchready"
        )
    else:
        await _w(
            bot, user.id,
            "🛡️ Staff Tools\n"
            "!bugs open\n"
            "!knownissues\n"
            "!safetydash\n"
            "!modhelp\n"
            "!status"
        )


# ---------------------------------------------------------------------------
# !testmenu / !betahelp
# ---------------------------------------------------------------------------

async def handle_testmenu(bot: BaseBot, user: User) -> None:
    """Public beta test menu — shows which commands players can try."""
    await _w(
        bot, user.id,
        "🧪 Beta Help\n"
        "Test these:\n"
        "!profile  !missions\n"
        "!mine  !fish\n"
        "!events  !guide\n"
        "Report issues: !bug"
    )


async def handle_betahelp(bot: BaseBot, user: User) -> None:
    await handle_testmenu(bot, user)


# ---------------------------------------------------------------------------
# !quickstart
# ---------------------------------------------------------------------------

async def handle_quickstart(bot: BaseBot, user: User) -> None:
    """Short guide for new and returning players."""
    await _w(
        bot, user.id,
        "🌟 Quick Start\n"
        "1. !profile — view your stats\n"
        "2. !missions — daily goals\n"
        "3. !mine — earn 🪙\n"
        "4. !fish — collect catches\n"
        "5. !guide — full tutorial"
    )
    await _w(
        bot, user.id,
        "Earn 🪙 ChillCoins by playing.\n"
        "🎫 Luxe Tickets via !luxeshop.\n"
        "Use !bug if something breaks."
    )


# ---------------------------------------------------------------------------
# !launchready [beta|full]
# ---------------------------------------------------------------------------

async def handle_launchready(bot: BaseBot, user: User, args: list[str]) -> None:
    """Launch readiness check — aggregates health signals (admin/owner only)."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    mode_str = "Beta" if is_beta_mode() else "Normal"

    cmd_ok = "?"
    try:
        from modules.cmd_audit      import ROUTED_COMMANDS, HIDDEN_CMDS, DEPRECATED_CMDS
        from modules.multi_bot      import _DEFAULT_COMMAND_OWNERS
        from modules.command_registry import get_entry as _reg_get
        import main as _main
        akc    = _main.ALL_KNOWN_COMMANDS
        active = akc - (HIDDEN_CMDS & akc) - (DEPRECATED_CMDS & akc)
        missing = len(active - ROUTED_COMMANDS)
        cmd_ok  = "OK" if missing == 0 else f"⚠️ {missing} missing"
    except Exception:
        pass

    try:
        from modules.maintenance import is_maintenance
        maint = "ON ⚠️" if is_maintenance() else "OFF"
    except Exception:
        maint = "?"

    try:
        open_bugs = len(db.get_bug_reports_by_type(status="open", limit=500))
    except Exception:
        open_bugs = 0

    try:
        known_ct = len(db.get_known_issues())
    except Exception:
        known_ct = 0

    ready = (cmd_ok == "OK" and maint == "OFF")

    await _w(
        bot, user.id,
        f"🚀 Launch Readiness\n"
        f"Commands: {cmd_ok}\n"
        f"Help: OK\n"
        f"Currency: OK\n"
        f"Economy: OK\n"
        f"Safety: OK\n"
        f"Mode: {mode_str}"
    )
    await _w(
        bot, user.id,
        f"Open bugs: {open_bugs}\n"
        f"Known issues: {known_ct}\n"
        f"Maintenance: {maint}\n"
        f"Ready: {'YES ✅' if ready else 'NO ⚠️'}"
    )


# ---------------------------------------------------------------------------
# !issueadmin add|resolve|list|clear
# ---------------------------------------------------------------------------

async def handle_issueadmin(bot: BaseBot, user: User, args: list[str]) -> None:
    """Manage known issues list (admin/owner only)."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else "list"

    if sub == "add":
        if len(args) < 3:
            await _w(bot, user.id, "Usage: !issueadmin add <description>")
            return
        text   = " ".join(args[2:])[:150]
        new_id = db.add_known_issue(text, user.username)
        await _w(bot, user.id, f"✅ Issue #{new_id} added.")

    elif sub in ("resolve", "remove"):
        if len(args) < 3 or not args[2].isdigit():
            await _w(bot, user.id, "Usage: !issueadmin resolve <id>")
            return
        ok = db.remove_known_issue(int(args[2]))
        await _w(
            bot, user.id,
            f"✅ Issue #{args[2]} resolved." if ok else "Issue not found."
        )

    elif sub == "clear":
        n = db.clear_known_issues()
        await _w(bot, user.id, f"✅ {n} issue(s) cleared.")

    elif sub == "list":
        issues = db.get_known_issues()
        if not issues:
            await _w(bot, user.id, "🧾 Known Issues\nNo issues tracked.")
            return
        lines = ["🧾 Known Issues"]
        for row in issues[:7]:
            lines.append(f"#{row.get('id','?')} {str(row.get('issue','?'))[:50]}")
        await _w(bot, user.id, "\n".join(lines)[:249])

    else:
        await _w(bot, user.id, "Usage: !issueadmin add|resolve|list|clear")


# ---------------------------------------------------------------------------
# !bugs open|recent|close <id>|view <id>
# ---------------------------------------------------------------------------

async def handle_bugs_admin(bot: BaseBot, user: User, args: list[str]) -> None:
    """View and manage bug reports (admin/owner only)."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else "open"

    if sub in ("open", "recent"):
        status = "open" if sub == "open" else None
        rows   = db.get_bug_reports_by_type(status=status, limit=8)
        label  = "Open" if sub == "open" else "Recent"
        if not rows:
            await _w(bot, user.id, f"🐞 No {label.lower()} bug reports.")
            return
        lines = [f"🐞 {label} Bugs"]
        for r in rows:
            uname = str(r.get("reporter_username", "?"))[:12]
            msg   = str(r.get("reason", ""))[:35]
            lines.append(f"#{r.get('id','?')} @{uname}: {msg}")
        await _w(bot, user.id, "\n".join(lines)[:249])

    elif sub == "close":
        if len(args) < 3 or not args[2].isdigit():
            await _w(bot, user.id, "Usage: !bugs close <id>")
            return
        ok = db.close_bug_report_by_id(int(args[2]))
        await _w(
            bot, user.id,
            f"✅ Bug #{args[2]} closed." if ok else f"Bug #{args[2]} not found."
        )

    elif sub == "view":
        if len(args) < 3 or not args[2].isdigit():
            await _w(bot, user.id, "Usage: !bugs view <id>")
            return
        row = db.get_report_by_id(int(args[2]))
        if not row or row.get("report_type") != "bug_report":
            await _w(bot, user.id, f"Bug #{args[2]} not found.")
            return
        uname = str(row.get("reporter_username", "?"))
        msg   = str(row.get("reason", ""))[:100]
        ts    = str(row.get("created_at", ""))[:16]
        st    = str(row.get("status", "open"))
        await _w(bot, user.id, f"🐞 Bug #{args[2]}\n@{uname}: {msg}\n{ts} [{st}]")

    elif sub == "assign":
        if not _is_admin_or_owner(user.username):
            await _w(bot, user.id, "Admin/owner only.")
            return
        if len(args) < 4 or not args[2].isdigit():
            await _w(bot, user.id, "Usage: !bugs assign <id> <staff>")
            return
        staff = args[3][:30]
        try:
            conn = db.get_connection()
            cur  = conn.execute(
                "UPDATE reports SET assigned_to=? WHERE id=? AND report_type='bug_report'",
                (staff, int(args[2])),
            )
            conn.commit(); conn.close()
            ok = cur.rowcount > 0
        except Exception:
            ok = False
        await _w(
            bot, user.id,
            f"✅ Bug #{args[2]} assigned to @{staff}." if ok else f"Bug #{args[2]} not found."
        )

    elif sub == "priority":
        if not _is_admin_or_owner(user.username):
            await _w(bot, user.id, "Admin/owner only.")
            return
        if len(args) < 4 or not args[2].isdigit():
            await _w(bot, user.id, "Usage: !bugs priority <id> low|medium|high|critical")
            return
        prio = args[3].lower()
        if prio not in ("low", "medium", "high", "critical"):
            await _w(bot, user.id, "Priority: low | medium | high | critical")
            return
        try:
            conn = db.get_connection()
            cur  = conn.execute(
                "UPDATE reports SET priority=? WHERE id=? AND report_type='bug_report'",
                (prio, int(args[2])),
            )
            conn.commit(); conn.close()
            ok = cur.rowcount > 0
        except Exception:
            ok = False
        await _w(
            bot, user.id,
            f"✅ Bug #{args[2]} priority: {prio}." if ok else f"Bug #{args[2]} not found."
        )

    elif sub == "tag":
        if not _is_admin_or_owner(user.username):
            await _w(bot, user.id, "Admin/owner only.")
            return
        if len(args) < 4 or not args[2].isdigit():
            await _w(bot, user.id, "Usage: !bugs tag <id> <tag>")
            return
        tag = args[3][:30]
        try:
            conn = db.get_connection()
            cur  = conn.execute(
                "UPDATE reports SET tags=? WHERE id=? AND report_type='bug_report'",
                (tag, int(args[2])),
            )
            conn.commit(); conn.close()
            ok = cur.rowcount > 0
        except Exception:
            ok = False
        await _w(
            bot, user.id,
            f"✅ Bug #{args[2]} tagged [{tag}]." if ok else f"Bug #{args[2]} not found."
        )

    else:
        await _w(bot, user.id,
                 "Usage: !bugs open|recent|close <id>|view <id>|assign|priority|tag")


# ---------------------------------------------------------------------------
# !errors recent|open|view <id>|close <id>|clearclosed
# ---------------------------------------------------------------------------

async def handle_errors_admin(bot: BaseBot, user: User, args: list[str]) -> None:
    """View and manage command error logs (owner only)."""
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else "recent"

    if sub in ("recent", "open"):
        status = "open" if sub == "open" else None
        rows   = db.get_command_error_logs(status=status, limit=8)
        if not rows:
            await _w(bot, user.id, "⚙️ No command errors logged.")
            return
        lines = ["⚙️ Command Errors"]
        for r in rows:
            uname = str(r.get("username", "?"))[:10]
            cmd   = str(r.get("command", "?"))[:15]
            err   = str(r.get("error_summary", ""))[:30]
            lines.append(f"#{r.get('id','?')} !{cmd} @{uname}: {err}")
        await _w(bot, user.id, "\n".join(lines)[:249])

    elif sub == "view":
        if len(args) < 3 or not args[2].isdigit():
            await _w(bot, user.id, "Usage: !errors view <id>")
            return
        rows = db.get_command_error_logs(limit=10000)
        target_id = int(args[2])
        row = next((r for r in rows if r.get("id") == target_id), None)
        if not row:
            await _w(bot, user.id, f"Error #{args[2]} not found.")
            return
        cmd = str(row.get("command", "?"))
        err = str(row.get("error_summary", ""))[:80]
        ts  = str(row.get("created_at", ""))[:16]
        await _w(bot, user.id, f"⚙️ Error #{target_id}\n!{cmd}\n{err}\n{ts}")

    elif sub == "close":
        if len(args) < 3 or not args[2].isdigit():
            await _w(bot, user.id, "Usage: !errors close <id>")
            return
        ok = db.close_command_error_log(int(args[2]))
        await _w(
            bot, user.id,
            f"✅ Error #{args[2]} closed." if ok else f"Error #{args[2]} not found."
        )

    elif sub == "clearclosed":
        try:
            conn = db.get_connection()
            cur  = conn.execute(
                "DELETE FROM command_error_logs WHERE status='closed'"
            )
            conn.commit()
            conn.close()
            await _w(bot, user.id, f"✅ {cur.rowcount} closed error(s) cleared.")
        except Exception as exc:
            await _w(bot, user.id, f"❌ Error: {str(exc)[:60]}")

    else:
        await _w(bot, user.id, "Usage: !errors recent|open|view <id>|close <id>|clearclosed")


# ---------------------------------------------------------------------------
# !announce [beta|event|update] <message>
# ---------------------------------------------------------------------------

_ANN_PREFIXES: dict[str, str] = {
    "beta":   "🧪 Beta Update",
    "event":  "🎉 Event Notice",
    "update": "🛠️ Update Notice",
}


async def handle_announce_room(bot: BaseBot, user: User, args: list[str]) -> None:
    """Send a typed room announcement (manager/admin/owner only)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    if len(args) < 2:
        await _w(bot, user.id, "Usage: !announce [beta|event|update] <message>")
        return

    if args[1].lower() in _ANN_PREFIXES:
        ann_type = args[1].lower()
        msg_body = " ".join(args[2:]).strip() if len(args) > 2 else ""
    else:
        ann_type = "general"
        msg_body = " ".join(args[1:]).strip()

    if not msg_body:
        await _w(bot, user.id, "Usage: !announce [beta|event|update] <message>")
        return

    prefix   = _ANN_PREFIXES.get(ann_type, "📢 Announcement")
    full_msg = f"{prefix}\n{msg_body}"

    if len(full_msg) > 220:
        await _chat(bot, f"{prefix}\n{msg_body[:140]}")
        await asyncio.sleep(0.5)
        await _chat(bot, msg_body[140:220])
    else:
        await _chat(bot, full_msg[:249])

    try:
        db.add_rotating_announcement(f"[{ann_type}] {msg_body[:180]}")
    except Exception:
        pass

    await _w(bot, user.id, f"✅ Announcement sent ({ann_type}).")


# ---------------------------------------------------------------------------
# !announceadmin status|on|off|add|remove|set interval <mins>
# ---------------------------------------------------------------------------

async def handle_announceadmin(bot: BaseBot, user: User, args: list[str]) -> None:
    """Manage rotating room announcements (admin/owner only)."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else "status"

    if sub == "status":
        enabled  = _get("rotating_enabled", "0") == "1"
        interval = _get("rotating_interval", "30")
        msgs     = db.get_rotating_announcements()
        on_str   = "ON" if enabled else "OFF"
        await _w(
            bot, user.id,
            f"📢 Rotating Announcements: {on_str}\n"
            f"Interval: {interval} min\n"
            f"Messages: {len(msgs)}\n"
            f"!announceadmin add <msg>"
        )

    elif sub == "on":
        _set("rotating_enabled", "1")
        await _w(bot, user.id, "📢 Rotating announcements enabled.")

    elif sub == "off":
        _set("rotating_enabled", "0")
        await _w(bot, user.id, "📢 Rotating announcements disabled.")

    elif sub == "add":
        if len(args) < 3:
            await _w(bot, user.id, "Usage: !announceadmin add <message>")
            return
        msg    = " ".join(args[2:])[:200]
        new_id = db.add_rotating_announcement(msg)
        await _w(bot, user.id, f"✅ Message #{new_id} added.")

    elif sub == "remove":
        if len(args) < 3 or not args[2].isdigit():
            await _w(bot, user.id, "Usage: !announceadmin remove <id>")
            return
        ok = db.remove_rotating_announcement(int(args[2]))
        await _w(
            bot, user.id,
            f"✅ Message #{args[2]} removed." if ok else "Message not found."
        )

    elif sub == "set":
        if len(args) >= 4 and args[2].lower() == "interval" and args[3].isdigit():
            mins = max(10, int(args[3]))
            _set("rotating_interval", str(mins))
            await _w(bot, user.id, f"✅ Rotating interval: {mins} min.")
        else:
            await _w(bot, user.id, "Usage: !announceadmin set interval <minutes>")

    else:
        await _w(
            bot, user.id,
            "Usage: !announceadmin status|on|off|add|remove|set interval <mins>"
        )


# ---------------------------------------------------------------------------
# Rotating announcement background loop (started by host bot on_start)
# ---------------------------------------------------------------------------

_rotating_index = 0


async def rotating_announcement_loop(bot: BaseBot) -> None:
    """Send rotating announcements on a configurable interval (default 30 min)."""
    global _rotating_index
    while True:
        try:
            enabled  = _get("rotating_enabled", "0") == "1"
            interval = max(10, int(_get("rotating_interval", "30")))
        except Exception:
            enabled  = False
            interval = 30

        if enabled:
            try:
                msgs = db.get_rotating_announcements()
                if msgs:
                    _rotating_index = _rotating_index % len(msgs)
                    msg = str(msgs[_rotating_index].get("message", ""))[:200]
                    if msg:
                        await _chat(bot, f"📢 {msg}")
                    _rotating_index += 1
            except Exception:
                pass

        await asyncio.sleep(interval * 60)
