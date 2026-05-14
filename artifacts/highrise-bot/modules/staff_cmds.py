"""
modules/staff_cmds.py
--------------------
3.2I — Staff utility commands.

Commands (staff+):
    !staffnote @user [note]  — add internal note to a player
    !staffnotes @user        — view notes for a player

Commands (any user, admin+ for others):
    !permissioncheck [@user] — show role and allowed commands
    !rolecheck [@user]       — alias for !permissioncheck

All messages ≤ 249 chars.
"""
from __future__ import annotations

import database as db
from highrise import BaseBot, User
from modules.permissions import (
    can_moderate, can_manage_games, can_manage_economy,
    is_admin, is_owner,
)


async def _w(bot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_staff_note(target_id: str, target_name: str,
                    staff_id: str, staff_name: str, note: str) -> int:
    try:
        conn = db.get_connection()
        cur = conn.execute(
            """INSERT INTO staff_notes
               (target_id, target_name, staff_id, staff_name, note, created_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (target_id, target_name.lower(), staff_id, staff_name.lower(), note[:200]),
        )
        note_id = cur.lastrowid or 0
        conn.commit()
        conn.close()
        return note_id
    except Exception as exc:
        print(f"[STAFF_CMDS] _add_staff_note error: {exc!r}")
        return 0


def _get_staff_notes(target_name: str, limit: int = 5) -> list[dict]:
    try:
        conn = db.get_connection()
        rows = conn.execute(
            """SELECT note, staff_name, created_at FROM staff_notes
               WHERE LOWER(target_name)=?
               ORDER BY id DESC LIMIT ?""",
            (target_name.lower(), limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _role_label(username: str) -> str:
    if is_owner(username):
        return "Owner"
    if is_admin(username):
        return "Admin"
    if can_manage_economy(username):
        return "Manager"
    if can_manage_games(username):
        return "Manager"
    if can_moderate(username):
        return "Staff"
    return "Player"


# ---------------------------------------------------------------------------
# !staffnote @user [note]   (staff+)
# ---------------------------------------------------------------------------

async def handle_staffnote(bot, user: User, args: list[str]) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "🔒 Staff only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !staffnote @user [note]")
        return

    target_name = args[1].lstrip("@").strip()
    note_text   = " ".join(args[2:])[:200].strip()

    if not note_text:
        await _w(bot, user.id, "Note cannot be empty.")
        return

    target = db.get_user_by_username(target_name)
    if target is None:
        await _w(bot, user.id, f"@{target_name} not found.")
        return

    _add_staff_note(
        target_id   = target["user_id"],
        target_name = target["username"],
        staff_id    = user.id,
        staff_name  = user.username,
        note        = note_text,
    )
    await _w(bot, user.id,
             f"📝 Staff Note Added\n@{target['username'][:20]}")


# ---------------------------------------------------------------------------
# !staffnotes @user   (staff+)
# ---------------------------------------------------------------------------

async def handle_staffnotes(bot, user: User, args: list[str]) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "🔒 Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !staffnotes @user")
        return

    target_name = args[1].lstrip("@").strip()
    rows = _get_staff_notes(target_name, limit=5)

    if not rows:
        await _w(bot, user.id, f"📝 No staff notes for @{target_name}.")
        return

    lines = [f"📝 Staff Notes @{target_name[:15]}"]
    for i, r in enumerate(rows, 1):
        note = r["note"][:50]
        lines.append(f"{i}. {note}")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# !permissioncheck [@user]   (self: any; others: admin+)
# !rolecheck [@user]         (alias)
# ---------------------------------------------------------------------------

async def handle_permissioncheck(bot, user: User, args: list[str]) -> None:
    is_adm = is_admin(user.username) or is_owner(user.username)

    target_name = args[1].lstrip("@").strip() if len(args) > 1 else user.username
    checking_other = target_name.lower() != user.username.lower()

    if checking_other and not is_adm:
        await _w(bot, user.id, "🔒 Admin only for checking other users.")
        return

    role = _role_label(target_name)

    if role == "Owner":
        allowed_str = "all commands, economy grant, production"
        denied_str  = ""
    elif role == "Admin":
        allowed_str = "warn, mute, softban, economy, safetyadmin"
        denied_str  = "production lock, backup restore"
    elif role == "Manager":
        allowed_str = "warn, mute, softban, bugs open"
        denied_str  = "economy grant, production"
    elif role == "Staff":
        allowed_str = "warn, bugs open, feedbacks recent"
        denied_str  = "economy grant, mute, production"
    else:
        allowed_str = "play, report, bug"
        denied_str  = "warn, mute, softban, economy grant"

    lines = [
        "🔐 Permission Check",
        f"@{target_name[:20]}",
        f"Role: {role}",
        f"Allowed: {allowed_str}",
    ]
    if denied_str:
        lines.append(f"Denied: {denied_str}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_rolecheck(bot, user: User, args: list[str]) -> None:
    is_adm = is_admin(user.username) or is_owner(user.username)

    target_name = args[1].lstrip("@").strip() if len(args) > 1 else user.username
    checking_other = target_name.lower() != user.username.lower()

    if checking_other and not is_adm:
        await _w(bot, user.id, "🔒 Admin only for checking other users.")
        return

    if not target_name:
        await _w(bot, user.id, "Use: !rolecheck @user")
        return

    role = _role_label(target_name)
    is_staff_flag  = "YES" if role in ("Staff", "Manager", "Admin", "Owner") else "NO"
    is_admin_flag  = "YES" if role in ("Admin", "Owner") else "NO"
    is_owner_flag  = "YES" if role == "Owner" else "NO"

    await _w(bot, user.id,
             f"🔐 Role Check\n"
             f"@{target_name[:20]}\n"
             f"Role: {role}\n"
             f"Staff: {is_staff_flag}\n"
             f"Admin: {is_admin_flag}\n"
             f"Owner: {is_owner_flag}")
