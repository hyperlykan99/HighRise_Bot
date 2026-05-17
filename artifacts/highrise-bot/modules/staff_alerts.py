"""
modules/staff_alerts.py
-----------------------
Staff DM alert system. Staff opt in to per-category DM alerts delivered via
ChillTopiaBot only.

DB table  : staff_alert_users  (created in database._migrate_db)
Log tags  : [STAFF ALERT]
"""
from __future__ import annotations

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

# Default ON categories per role
_ROLE_DEFAULTS: dict[str, frozenset[str]] = {
    "owner": frozenset(_CATEGORIES),
    "admin": frozenset(
        ("security", "reports", "economy", "casino", "bothealth", "events")
    ),
    "mod":   frozenset(("security", "reports", "events")),
    "staff": frozenset(("reports", "events")),
}


def _role(username: str) -> str:
    if is_owner(username):    return "owner"
    if is_admin(username):    return "admin"
    if can_moderate(username): return "mod"
    return "staff"


def _is_eligible(username: str) -> bool:
    return is_owner(username) or is_admin(username) or can_moderate(username)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_prefs(user_id: str, username: str) -> dict:
    """Return prefs row, inserting role-based defaults if first call."""
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
                cats["casino"], cats["bothealth"], cats["events"], cats["qa"],
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


def _get_subscribed_rows(category: str) -> list[dict]:
    if category not in _CATEGORIES:
        return []
    try:
        conn = db.get_connection()
        rows = conn.execute(
            f"""SELECT user_id, username FROM staff_alert_users
                WHERE alerts_on=1 AND {category}=1""",
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Send helpers
# ---------------------------------------------------------------------------

async def send_staff_alert(bot: "BaseBot", category: str, message: str) -> int:
    """
    DM all staff subscribed to *category*. Sends directly (host bot context).
    Returns count of DMs sent.
    """
    if category not in _CATEGORIES:
        return 0

    rows = _get_subscribed_rows(category)
    sent = 0
    for row in rows:
        uid   = row["user_id"]
        uname = row["username"]
        nr    = db.get_notify_user(uid)
        if not nr:
            continue
        conv_id = nr.get("conversation_id", "")
        if not conv_id:
            continue
        try:
            await bot.highrise.send_message(conv_id, message[:249], "text")
            sent += 1
            print(f"[STAFF ALERT] category={category} user=@{uname} status=sent")
        except Exception as exc:
            print(f"[STAFF ALERT] DM failed user=@{uname}: {exc!r}")
    return sent


def queue_staff_alert(category: str, message: str) -> None:
    """
    Queue a staff alert for host bot delivery (use from non-async or non-host
    bot contexts). Only queues if the category is valid.
    """
    if category not in _CATEGORIES:
        return
    from modules.dm_queue import queue_host_dm  # noqa: PLC0415

    rows = _get_subscribed_rows(category)
    for row in rows:
        queue_host_dm(
            row["user_id"], row["username"],
            "staff_alert", category, message,
        )


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

    # Settings display
    nr = db.get_notify_user(user.id)
    if not nr or not nr.get("conversation_id"):
        await _w(bot, user.id,
                 "⚠️ No DM linked.\nDM ChillTopiaBot !sub first.")
        return

    prefs  = _get_prefs(user.id, user.username)
    status = "ON" if prefs.get("alerts_on", 1) else "OFF"
    lines  = [f"🛡️ Staff Alerts: {status}"]
    for c in _CATEGORIES:
        flag = "ON" if prefs.get(c, 0) else "OFF"
        lines.append(f"  {_LABEL[c]}: {flag}")
    # Split across two whispers to stay ≤249 chars each
    await _w(bot, user.id, "\n".join(lines[:5])[:249])
    if len(lines) > 5:
        await _w(bot, user.id, "\n".join(lines[5:])[:249])


async def handle_staffalert_test(
    bot: "BaseBot", user: "User", args: list[str],
) -> None:
    """!staffalert test — send a test DM to all subscribed staff."""
    if not _is_eligible(user.username):
        await _w(bot, user.id, "🔒 Staff only.")
        return
    msg   = f"🛡️ Staff Alert Test\nSent by: @{user.username}"
    count = await send_staff_alert(bot, "security", msg)
    await _w(bot, user.id, f"✅ Test alert sent to {count} staff.")


async def handle_staffalertaudit(
    bot: "BaseBot", user: "User", args: list[str],
) -> None:
    """!staffalertaudit [@user] — show alert prefs for a staff member."""
    if not is_admin(user.username) and not is_owner(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return
    target = args[1].lstrip("@").strip() if len(args) >= 2 else user.username
    rec    = db.get_user_by_username(target)
    if not rec:
        await _w(bot, user.id, f"@{target} not found.")
        return

    prefs  = _get_prefs(rec["user_id"], rec["username"])
    status = "ON" if prefs.get("alerts_on", 1) else "OFF"
    lines  = [f"🛡️ @{rec['username']} Alerts: {status}"]
    for c in _CATEGORIES:
        flag = "ON" if prefs.get(c, 0) else "OFF"
        lines.append(f"  {_LABEL[c]}: {flag}")
    await _w(bot, user.id, "\n".join(lines[:5])[:249])
    if len(lines) > 5:
        await _w(bot, user.id, "\n".join(lines[5:])[:249])


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
