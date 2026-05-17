"""
modules/staff_alerts.py
-----------------------
Staff DM alert system. Staff opt in to per-category DM alerts delivered via
ChillTopiaBot only.

DB table  : staff_alert_users  (created in database._migrate_db)
Log tags  : [STAFF ALERT SEND] [STAFF ALERT SKIP] [STAFF ALERT QUEUE] [STAFF ALERT ERROR]

Root-cause note
---------------
`staff_alert_users` is only populated after a staff member runs !staffalerts.
Instead of querying that table first, delivery walks notification_users (which
has conversation_ids) and cross-checks staff_alert_users prefs (falling back to
role-based defaults when no row exists).
"""
from __future__ import annotations

import config as _cfg
from typing import TYPE_CHECKING

import database as db
from modules.permissions import is_owner, is_admin, can_moderate

if TYPE_CHECKING:
    from main import BaseBot
    from highrise import User


_CATEGORIES: tuple[str, ...] = (
    "security", "reports", "economy", "casino", "bothealth", "events", "qa",
)

_LABEL: dict[str, str] = {
    "security":  "Security",
    "reports":   "Reports",
    "economy":   "Economy",
    "casino":    "Casino",
    "bothealth": "Bot Health",
    "events":    "Events",
    "qa":        "QA",
}

# Default ON categories per role (used when user has no row in staff_alert_users)
_ROLE_DEFAULTS: dict[str, frozenset[str]] = {
    "owner": frozenset(_CATEGORIES),
    "admin": frozenset(
        ("security", "reports", "economy", "casino", "bothealth", "events")
    ),
    "mod":   frozenset(("security", "reports", "events")),
    "staff": frozenset(("reports", "events")),
}

_IS_HOST: bool = _cfg.BOT_MODE in ("host", "all")


# ---------------------------------------------------------------------------
# Role / eligibility helpers
# ---------------------------------------------------------------------------

def _role(username: str) -> str:
    if is_owner(username):     return "owner"
    if is_admin(username):     return "admin"
    if can_moderate(username): return "mod"
    return "staff"


def _is_eligible(username: str) -> bool:
    return is_owner(username) or is_admin(username) or can_moderate(username)


def _cat_default(username: str, category: str) -> bool:
    """Role-based default for a category when no prefs row exists."""
    role = _role(username)
    return category in _ROLE_DEFAULTS.get(role, frozenset())


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_prefs_ro(user_id: str, username: str) -> dict:
    """
    Return prefs from staff_alert_users if row exists, else return in-memory
    role-based defaults. Does NOT insert a row.
    """
    try:
        conn = db.get_connection()
        row  = conn.execute(
            "SELECT * FROM staff_alert_users WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception:
        pass

    role     = _role(username)
    defaults = _ROLE_DEFAULTS.get(role, frozenset())
    return {
        "user_id":   user_id,
        "username":  username.lower(),
        "alerts_on": 1,
        **{c: (1 if c in defaults else 0) for c in _CATEGORIES},
    }


def _get_prefs(user_id: str, username: str) -> dict:
    """Return prefs, inserting role-based defaults if this is the first call."""
    try:
        conn = db.get_connection()
        row  = conn.execute(
            "SELECT * FROM staff_alert_users WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception:
        pass

    role     = _role(username)
    defaults = _ROLE_DEFAULTS.get(role, frozenset())
    cats     = {c: (1 if c in defaults else 0) for c in _CATEGORIES}
    try:
        conn = db.get_connection()
        conn.execute(
            """INSERT OR IGNORE INTO staff_alert_users
                   (user_id, username, alerts_on,
                    security, reports, economy,
                    casino, bothealth, events, qa,
                    updated_at)
               VALUES (?,?,1,?,?,?,?,?,?,?,datetime('now'))""",
            (
                user_id, username.lower(),
                cats["security"], cats["reports"], cats["economy"],
                cats["casino"],   cats["bothealth"], cats["events"], cats["qa"],
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    return {"user_id": user_id, "username": username.lower(),
            "alerts_on": 1, **cats}


def _set_cat(user_id: str, username: str, category: str, on: bool) -> None:
    if category not in _CATEGORIES:
        return
    try:
        conn = db.get_connection()
        conn.execute(
            f"""INSERT INTO staff_alert_users
                    (user_id, username, {category}, updated_at)
                VALUES (?,?,?,datetime('now'))
                ON CONFLICT(user_id) DO UPDATE SET
                  {category}=excluded.{category},
                  updated_at=excluded.updated_at""",
            (user_id, username.lower(), 1 if on else 0),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _set_global(user_id: str, username: str, on: bool) -> None:
    try:
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO staff_alert_users
                   (user_id, username, alerts_on, updated_at)
               VALUES (?,?,?,datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET
                 alerts_on=excluded.alerts_on,
                 updated_at=excluded.updated_at""",
            (user_id, username.lower(), 1 if on else 0),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Conv-id resolution  (Part 5 — ordered lookup)
# ---------------------------------------------------------------------------

def _resolve_conv_id(user_id: str, username: str) -> tuple[str, str]:
    """
    Resolve DM conversation_id for a user. Returns (conv_id, source_table).
    Lookup order:
      1. notification_users (primary — set by !sub DM flow)
      2. player_dm_conversations (fallback secondary table)
    """
    try:
        nr = db.get_notify_user(user_id)
        if nr and nr.get("conversation_id"):
            return nr["conversation_id"], "notification_users"
    except Exception:
        pass

    try:
        pr = db.get_player_dm_conv(user_id)
        if pr and pr.get("conversation_id"):
            return pr["conversation_id"], "player_dm_conversations"
    except Exception:
        pass

    return "", "none"


# ---------------------------------------------------------------------------
# Eligible recipient scan
# ---------------------------------------------------------------------------

def _eligible_recipients(category: str) -> list[dict]:
    """
    Walk all notification_users who have conversation_id + subscribed=1.
    For each:
      • Must be staff-eligible (is_admin / is_owner / can_moderate)
      • Must have alerts_on (from prefs or role default)
      • Must have the category enabled (from prefs or role default)
    Returns list of dicts with keys: user_id, username, conv_id, source, prefs.
    """
    if category not in _CATEGORIES:
        return []

    try:
        conn = db.get_connection()
        rows = conn.execute(
            """SELECT user_id, username, conversation_id
               FROM notification_users
               WHERE subscribed=1
                 AND conversation_id IS NOT NULL
                 AND conversation_id != ''""",
        ).fetchall()
        conn.close()
    except Exception:
        return []

    out: list[dict] = []
    for row in rows:
        uid    = row["user_id"]
        uname  = (row["username"] or "").lower()
        conv   = row["conversation_id"] or ""

        if not uname or not _is_eligible(uname):
            continue

        prefs = _get_prefs_ro(uid, uname)

        # Master switch — explicit row takes precedence; default is ON
        if not prefs.get("alerts_on", 1):
            continue

        # Category switch — explicit row takes precedence; use role default
        cat_val = prefs.get(category)
        if cat_val is None:
            cat_val = 1 if _cat_default(uname, category) else 0
        if not cat_val:
            continue

        # Try fallback conv_id sources if notification_users one is empty
        src = "notification_users"
        if not conv:
            conv, src = _resolve_conv_id(uid, uname)

        out.append({
            "user_id":  uid,
            "username": uname,
            "conv_id":  conv,
            "source":   src,
            "prefs":    prefs,
        })

    return out


# ---------------------------------------------------------------------------
# Send helpers
# ---------------------------------------------------------------------------

async def send_staff_alert(
    bot: "BaseBot", category: str, message: str
) -> int:
    """
    DM staff subscribed to *category*. Host bot only — non-host bots are
    re-routed to queue_staff_alert() automatically.
    Returns count of DMs sent.
    """
    if not _IS_HOST:
        print(f"[STAFF ALERT QUEUE] type={category} (non-host bot — queuing)")
        queue_staff_alert(category, message)
        return 0

    if category not in _CATEGORIES:
        return 0

    recipients = _eligible_recipients(category)
    sent = 0

    for rec in recipients:
        uname   = rec["username"]
        conv_id = rec["conv_id"]

        if not conv_id:
            print(f"[STAFF ALERT SKIP] target=@{uname} reason=no_conversation_id")
            continue

        try:
            await bot.highrise.send_message(conv_id, message[:249], "text")
            print(f"[STAFF ALERT SEND] host=YES target=@{uname} status=sent")
            sent += 1
        except Exception as exc:
            print(f"[STAFF ALERT ERROR] target=@{uname} error={exc!r}")

    return sent


async def _send_staff_alert_verbose(
    bot: "BaseBot", category: str, message: str
) -> dict:
    """
    Same as send_staff_alert but returns a detailed delivery report dict:
    { sent, skipped_no_dm, skipped_off, skipped_cat, failed }
    Used by !staffalert test for a rich room reply.
    """
    if not _IS_HOST:
        print(f"[STAFF ALERT QUEUE] type=test (non-host bot — queuing)")
        queue_staff_alert(category, message)
        return {"sent": 0, "skipped_no_dm": 0, "skipped_off": 0,
                "skipped_cat": 0, "failed": 0, "queued": True}

    if category not in _CATEGORIES:
        return {"sent": 0, "skipped_no_dm": 0, "skipped_off": 0,
                "skipped_cat": 0, "failed": 0}

    # Walk eligible recipients (already filtered for alerts_on + cat)
    recipients = _eligible_recipients(category)
    sent = failed = no_dm = 0

    for rec in recipients:
        uname   = rec["username"]
        conv_id = rec["conv_id"]

        if not conv_id:
            print(f"[STAFF ALERT SKIP] target=@{uname} reason=no_conversation_id")
            no_dm += 1
            continue

        try:
            await bot.highrise.send_message(conv_id, message[:249], "text")
            print(f"[STAFF ALERT SEND] host=YES target=@{uname} status=sent")
            sent += 1
        except Exception as exc:
            print(f"[STAFF ALERT ERROR] target=@{uname} error={exc!r}")
            failed += 1

    return {
        "sent":          sent,
        "skipped_no_dm": no_dm,
        "skipped_off":   0,
        "skipped_cat":   0,
        "failed":        failed,
    }


def queue_staff_alert(category: str, message: str) -> None:
    """
    Queue a staff alert for host bot delivery.
    Walks eligible recipients same as send_staff_alert, inserts one queue row
    per recipient. Host bot processes via process_host_dm_queue().
    """
    if category not in _CATEGORIES:
        return

    try:
        from modules.dm_queue import queue_host_dm  # noqa: PLC0415
    except Exception:
        return

    # Walk notification_users for eligible staff (same logic as send_staff_alert)
    try:
        conn = db.get_connection()
        rows = conn.execute(
            """SELECT user_id, username, conversation_id
               FROM notification_users
               WHERE subscribed=1
                 AND conversation_id IS NOT NULL
                 AND conversation_id != ''""",
        ).fetchall()
        conn.close()
    except Exception:
        return

    queued = 0
    for row in rows:
        uid   = row["user_id"]
        uname = (row["username"] or "").lower()
        conv  = row["conversation_id"] or ""

        if not uname or not _is_eligible(uname):
            continue

        prefs = _get_prefs_ro(uid, uname)

        if not prefs.get("alerts_on", 1):
            continue

        cat_val = prefs.get(category)
        if cat_val is None:
            cat_val = 1 if _cat_default(uname, category) else 0
        if not cat_val:
            continue

        queue_host_dm(uid, uname, "staff_alert", category, message, conv)
        print(f"[STAFF ALERT QUEUE] type={category} target=@{uname}")
        queued += 1

    print(f"[STAFF ALERT QUEUE] category={category} queued={queued}")


# ---------------------------------------------------------------------------
# Whisper helper
# ---------------------------------------------------------------------------

async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def handle_staffalerts(
    bot: "BaseBot", user: "User", args: list[str],
) -> None:
    """
    !staffalerts                  — show current settings
    !staffalerts on|off           — global toggle
    !staffalerts <cat> on|off     — per-category toggle
    !staffalerts settings         — same as bare
    """
    if not _is_eligible(user.username):
        await _w(bot, user.id, "🔒 Staff only.")
        return

    sub = args[1].lower() if len(args) >= 2 else ""
    cat = args[2].lower() if len(args) >= 3 else ""

    # Global on/off
    if sub in ("on", "off") and not cat:
        on = sub == "on"
        _set_global(user.id, user.username, on)
        await _w(bot, user.id,
                 f"🛡️ Staff Alerts: {'ON' if on else 'OFF'}")
        return

    # Per-category on/off
    if sub in _CATEGORIES and cat in ("on", "off"):
        on = cat == "on"
        _set_cat(user.id, user.username, sub, on)
        await _w(bot, user.id,
                 f"🛡️ {_LABEL[sub]} alerts: {'ON' if on else 'OFF'}")
        return

    # Settings display (show even if DM not connected — just indicate at bottom)
    prefs  = _get_prefs(user.id, user.username)
    status = "ON" if prefs.get("alerts_on", 1) else "OFF"

    # Check DM connection
    conv_id, _ = _resolve_conv_id(user.id, user.username)
    dm_status  = "Connected" if conv_id else "Not connected"

    lines = [
        f"🛡️ Staff Alerts",
        f"Status: {status}",
    ]
    for c in _CATEGORIES:
        flag = "ON" if prefs.get(c, 0) else "OFF"
        lines.append(f"{_LABEL[c]}: {flag}")
    lines.append(f"DM: {dm_status}")

    # Two whispers to stay ≤249 chars each
    await _w(bot, user.id, "\n".join(lines[:6])[:249])
    await _w(bot, user.id, "\n".join(lines[6:])[:249])

    if not conv_id:
        await _w(bot, user.id, "DM ChillTopiaBot !sub first.")


async def handle_staffalert_test(
    bot: "BaseBot", user: "User", args: list[str],
) -> None:
    """
    !staffalert test            — general security-category test
    !staffalert <cat> test      — category-specific test
    """
    if not _is_eligible(user.username):
        await _w(bot, user.id, "🔒 Staff only.")
        return

    # Detect   !staffalert <cat> test
    sub1 = args[1].lower() if len(args) >= 2 else ""
    sub2 = args[2].lower() if len(args) >= 3 else ""

    if sub1 in _CATEGORIES and sub2 == "test":
        category = sub1
        if category == "reports":
            msg = "📣 Staff Report Alert Test\nReports category is working."
        else:
            msg = f"🛡️ Staff Alert Test\n{_LABEL[category]} category is working."
    else:
        category = "security"
        msg = "🛡️ Staff Alert Test\nThis is a test staff alert."

    report  = await _send_staff_alert_verbose(bot, category, msg)
    sent    = report["sent"]
    no_dm   = report["skipped_no_dm"]
    off_ct  = report["skipped_off"]
    cat_ct  = report["skipped_cat"]
    failed  = report["failed"]
    skipped = no_dm + off_ct + cat_ct

    label = _LABEL.get(category, category.title())
    summary = (
        f"🛡️ {label} Alert Test\n"
        f"Delivered: {sent}\n"
        f"Skipped: {skipped}\n"
        f"Failed: {failed}"
    )
    await _w(bot, user.id, summary[:249])

    if skipped:
        reasons: list[str] = []
        if no_dm:  reasons.append(f"No DM linked: {no_dm}")
        if off_ct: reasons.append(f"Alerts off: {off_ct}")
        if cat_ct: reasons.append(f"Category off: {cat_ct}")
        await _w(bot, user.id, "\n".join(reasons)[:249])

    if report.get("queued"):
        await _w(bot, user.id,
                 "⚠️ Non-host bot — test queued for ChillTopiaBot.")


async def handle_staffalertaudit(
    bot: "BaseBot", user: "User", args: list[str],
) -> None:
    """!staffalertaudit [@user] — show alert prefs + DM link info."""
    if not is_admin(user.username) and not is_owner(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return

    target = args[1].lstrip("@").strip() if len(args) >= 2 else user.username
    rec    = db.get_user_by_username(target)
    if not rec:
        await _w(bot, user.id, f"@{target} not found.")
        return

    uid    = rec["user_id"]
    uname  = rec["username"]
    prefs  = _get_prefs(uid, uname)
    status = "ON" if prefs.get("alerts_on", 1) else "OFF"

    # Resolve conv_id with source info
    conv_id, src = _resolve_conv_id(uid, uname)
    dm_linked = "YES" if conv_id else "NO"

    # Role detection
    role_str = _role(uname).title()
    eligible = _is_eligible(uname)

    # Can receive report alerts?
    alerts_on  = bool(prefs.get("alerts_on", 1))
    reports_on = bool(prefs.get("reports", _cat_default(uname, "reports")))
    can_recv   = eligible and alerts_on and reports_on and bool(conv_id)
    can_str    = "YES" if can_recv else "NO"

    # Build reason if NO
    block_reason = ""
    if not can_recv:
        if not eligible:
            block_reason = "not staff/admin/owner"
        elif not alerts_on:
            block_reason = "staff alerts OFF"
        elif not reports_on:
            block_reason = "reports OFF"
        else:
            block_reason = "no DM connected"

    lines = [
        f"🛡️ @{uname} Staff Alert Audit",
        f"Status: {status}",
        f"Reports: {'ON' if reports_on else 'OFF'}",
        f"DM connected: {dm_linked}",
        f"Source: {src}",
        f"Role detected: {role_str}",
        f"Can receive report alerts: {can_str}",
    ]
    if block_reason:
        lines.append(f"Reason: {block_reason}")

    # Category breakdown
    cat_lines = []
    for c in _CATEGORIES:
        flag = "ON" if prefs.get(c, 0) else "OFF"
        cat_lines.append(f"  {_LABEL[c]}: {flag}")

    # Whisper 1: header + key fields
    await _w(bot, user.id, "\n".join(lines)[:249])
    # Whisper 2: full category breakdown
    await _w(bot, user.id, "\n".join(cat_lines)[:249])


async def handle_staffsubcount(
    bot: "BaseBot", user: "User",
) -> None:
    """!staffsubcount — total staff with DM alerts enabled."""
    if not is_admin(user.username) and not is_owner(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return
    try:
        conn  = db.get_connection()
        total = conn.execute(
            "SELECT COUNT(*) FROM staff_alert_users WHERE alerts_on=1"
        ).fetchone()[0]
        cats: dict[str, int] = {}
        for c in _CATEGORIES:
            cats[c] = conn.execute(
                f"SELECT COUNT(*) FROM staff_alert_users"
                f" WHERE alerts_on=1 AND {c}=1"
            ).fetchone()[0]
        conn.close()

        lines = [f"🛡️ Staff Subscribers: {total}"]
        for c in _CATEGORIES:
            lines.append(f"  {_LABEL[c]}: {cats[c]}")
        await _w(bot, user.id, "\n".join(lines[:5])[:249])
        if len(lines) > 5:
            await _w(bot, user.id, "\n".join(lines[5:])[:249])
    except Exception as exc:
        await _w(bot, user.id, f"⚠️ Error: {str(exc)[:80]}")
