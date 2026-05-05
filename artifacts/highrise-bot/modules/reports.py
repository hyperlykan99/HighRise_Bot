"""
modules/reports.py
------------------
Player report system for the Highrise Mini Game Bot.

Player commands (everyone):
  /report <username> <reason>  — submit a player report
  /bug <message>               — submit a bug report
  /myreports                   — view your submitted reports

Staff commands (moderator+):
  /reports                     — latest 5 open reports
  /reportinfo <id>             — full report details
  /closereport <id>            — mark report as closed
  /reportwatch <username>      — reports by or against a player

Cooldown : 60 s per user for both /report and /bug.
Max reason: 120 characters.
All messages ≤ 249 characters.
"""

from highrise import BaseBot, User

import database as db
from modules.cooldowns  import check_user_cooldown, set_user_cooldown
from modules.permissions import can_moderate

_COOLDOWN   = 60
_MAX_REASON = 120

# Short type labels for compact display
_SHORT: dict[str, str] = {
    "player_report": "player",
    "bug_report":    "bug",
    "bank_issue":    "bank",
    "casino_issue":  "casino",
    "shop_issue":    "shop",
}


async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


# ---------------------------------------------------------------------------
# /report <username> <reason>
# ---------------------------------------------------------------------------

async def handle_report(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /report <username> <reason>")
        return

    remaining = check_user_cooldown("report", user.id, _COOLDOWN)
    if remaining:
        await _w(bot, user.id, f"⏳ Wait {remaining}s before submitting again.")
        return

    target = args[1].lstrip("@").strip()
    reason = " ".join(args[2:])[:_MAX_REASON].strip()

    if not target:
        await _w(bot, user.id, "Please provide a username.")
        return
    if not reason:
        await _w(bot, user.id, "Reason cannot be empty.")
        return
    if target.lower() == user.username.lower():
        await _w(bot, user.id, "You cannot report yourself.")
        return

    report_id = db.create_report(
        reporter_id       = user.id,
        reporter_username = user.username,
        target_username   = target,
        report_type       = "player_report",
        reason            = reason,
    )
    set_user_cooldown("report", user.id)
    await _w(bot, user.id, f"✅ Report #{report_id} submitted. Staff will review it.")


# ---------------------------------------------------------------------------
# /bug <message>
# ---------------------------------------------------------------------------

async def handle_bug(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /bug <describe the issue>")
        return

    remaining = check_user_cooldown("report", user.id, _COOLDOWN)
    if remaining:
        await _w(bot, user.id, f"⏳ Wait {remaining}s before submitting again.")
        return

    reason = " ".join(args[1:])[:_MAX_REASON].strip()
    if not reason:
        await _w(bot, user.id, "Bug description cannot be empty.")
        return

    report_id = db.create_report(
        reporter_id       = user.id,
        reporter_username = user.username,
        target_username   = "",
        report_type       = "bug_report",
        reason            = reason,
    )
    set_user_cooldown("report", user.id)
    await _w(bot, user.id, f"🐞 Bug #{report_id} submitted. Thank you!")


# ---------------------------------------------------------------------------
# /myreports
# ---------------------------------------------------------------------------

async def handle_myreports(bot: BaseBot, user: User) -> None:
    rows = db.get_my_reports(user.id, limit=5)
    if not rows:
        await _w(bot, user.id, "📋 You have no reports submitted yet.")
        return
    lines = ["📋 Your reports:"]
    for r in rows:
        t  = _SHORT.get(r["report_type"], r["report_type"])
        vs = f"→@{r['target_username']}" if r["target_username"] else ""
        lines.append(f"#{r['id']} {t}{vs} [{r['status']}]")
    await _w(bot, user.id, "\n".join(lines))


# ---------------------------------------------------------------------------
# /reports  (mod+)
# ---------------------------------------------------------------------------

async def handle_reports(bot: BaseBot, user: User) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    rows = db.get_open_reports(limit=5)
    if not rows:
        await _w(bot, user.id, "📋 No open reports.")
        return
    lines = [f"📋 Open ({len(rows)}):"]
    for r in rows:
        t  = _SHORT.get(r["report_type"], r["report_type"])
        by = r["reporter_username"][:12]
        vs = f"→@{r['target_username'][:12]}" if r["target_username"] else ""
        lines.append(f"#{r['id']} @{by}{vs} [{t}]")
    await _w(bot, user.id, "\n".join(lines))


# ---------------------------------------------------------------------------
# /reportinfo <id>  (mod+)
# ---------------------------------------------------------------------------

async def handle_reportinfo(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: /reportinfo <id>")
        return

    r = db.get_report_by_id(int(args[1]))
    if not r:
        await _w(bot, user.id, f"Report #{args[1]} not found.")
        return

    ts     = r["timestamp"][:16]
    t      = _SHORT.get(r["report_type"], r["report_type"])
    target = f" → @{r['target_username']}" if r["target_username"] else ""
    reason = r["reason"][:80]
    hby    = f"\nClosed by: @{r['handled_by']}" if r["handled_by"] else ""
    msg = (
        f"📋 #{r['id']} {t} [{r['status']}]\n"
        f"By: @{r['reporter_username']}{target}\n"
        f"{ts}\n"
        f"Reason: {reason}"
        f"{hby}"
    )
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# /closereport <id>  (mod+)
# ---------------------------------------------------------------------------

async def handle_closereport(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: /closereport <id>")
        return

    report_id = int(args[1])
    r = db.get_report_by_id(report_id)
    if not r:
        await _w(bot, user.id, f"Report #{report_id} not found.")
        return
    if r["status"] == "closed":
        await _w(bot, user.id, f"Report #{report_id} is already closed.")
        return

    ok = db.close_report(report_id, handled_by=user.username)
    if ok:
        await _w(bot, user.id, f"✅ Report #{report_id} closed.")
    else:
        await _w(bot, user.id, "Failed to close. Try again.")


# ---------------------------------------------------------------------------
# /reportwatch <username>  (mod+)
# ---------------------------------------------------------------------------

async def handle_reportwatch(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /reportwatch <username>")
        return

    target = args[1].lstrip("@").strip()
    rows   = db.get_reports_for_username(target, limit=5)
    if not rows:
        await _w(bot, user.id, f"📋 No reports found for @{target}.")
        return

    lines = [f"📋 @{target[:15]} reports:"]
    for r in rows:
        t    = _SHORT.get(r["report_type"], r["report_type"])
        role = "by" if r["reporter_username"].lower() == target.lower() else "against"
        lines.append(f"#{r['id']} {role} [{r['status']}] {t}")
    await _w(bot, user.id, "\n".join(lines))
