"""
modules/ai_staff_context.py — Staff-level knowledge context (3.3A).

Access: staff / admin / owner only.

Provides:
- Open bug report summary
- Moderation suggestions
- Recent warnings summary (count only — no private user details)
- Support queue overview
"""
from __future__ import annotations

import database as db


def _count_bugs(status: str = "open") -> int:
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE report_type='bug_report' AND status=?",
            (status,),
        ).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _count_critical_bugs() -> int:
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT COUNT(*) FROM reports "
            "WHERE report_type='bug_report' AND status='open' AND priority='critical'",
        ).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _count_feedback(status: str = "open") -> int:
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE report_type='feedback' AND status=?",
            (status,),
        ).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _recent_bug_types() -> list[str]:
    try:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT reason FROM reports WHERE report_type='bug_report' AND status='open' "
            "ORDER BY id DESC LIMIT 5",
        ).fetchall()
        conn.close()
        return [r[0][:40] for r in rows if r and r[0]]
    except Exception:
        return []


def _count_warnings() -> int:
    try:
        conn = db.get_connection()
        row = conn.execute("SELECT COUNT(*) FROM room_warnings").fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def get_bug_summary() -> str:
    total    = _count_bugs("open")
    critical = _count_critical_bugs()
    feedback = _count_feedback("open")
    recent   = _recent_bug_types()

    lines = [f"🐛 Open Bug Reports: {total}"]
    if critical:
        lines.append(f"🔴 Critical: {critical}")
    if feedback:
        lines.append(f"💬 Open Feedback: {feedback}")
    if recent:
        lines.append("Recent:")
        for r in recent[:3]:
            lines.append(f"  • {r}")
    lines.append("Use !bugs open for full list.")
    return "\n".join(lines)[:249]


def get_warnings_summary() -> str:
    total = _count_warnings()
    return (
        f"⚠️ Room warnings on record: {total}\n"
        "Use !warnings or staff moderation commands for details."
    )[:249]


def get_support_overview() -> str:
    bugs     = _count_bugs("open")
    feedback = _count_feedback("open")
    return (
        f"📋 Support queue:\n"
        f"• Open bugs: {bugs}\n"
        f"• Open feedback: {feedback}\n"
        "Use !bugs open / !feedback open for details."
    )[:249]
