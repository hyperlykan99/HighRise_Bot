"""
modules/notif_debug.py
Notification / room whisper debug tools.

Commands:
  /notifydebug @username  — full notification status for one player (manager+)
  /roomusers              — list live room users (manager+)
  /testwhisper @username  — test direct whisper to player (manager+)
  /notifrefresh           — refresh room user cache (manager+)

Owner: EmceeBot (host mode)
"""
from __future__ import annotations
import asyncio

import database as db
from modules.permissions import can_manage_economy


async def _w(bot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /notifydebug @username
# ---------------------------------------------------------------------------

async def handle_notifydebug(bot, user, args: list[str]) -> None:
    """/notifydebug @username — full notification debug for one player."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /notifydebug @username")
        return

    raw = args[1].lstrip("@").lower()
    sub_row = db.get_subscriber(raw)
    if not sub_row:
        await _w(bot, user.id,
                 "🔔 Notify Debug\n"
                 "Player not found. Make sure they have used the bot or are in the room.")
        return

    uid      = sub_row.get("user_id", "") or ""
    uname    = sub_row.get("username", raw)
    subbed   = bool(sub_row.get("subscribed", 0))
    conv_id  = sub_row.get("conversation_id", "") or ""

    global_row = db.get_sub_notif_global(uid) if uid else {}
    global_on  = bool(global_row.get("global_enabled", 1))

    prefs = db.get_sub_notif_prefs(uid) if uid else {}

    # Live room check using SDK
    in_room = False
    if uid:
        try:
            from modules.sub_notif import _get_live_room_user_ids
            live_ids = await _get_live_room_user_ids(bot)
            in_room  = uid in live_ids
        except Exception:
            try:
                from modules.room_utils import _user_positions
                in_room = uid in _user_positions
            except Exception:
                pass

    # Last delivery from recipient log
    last_status = last_method = last_error = "none"
    try:
        rows = db.get_sub_notif_recipient_history(uid, limit=1)
        if rows:
            r = rows[0]
            last_status = r.get("status", "none") or "none"
            last_method = r.get("delivery_method", "none") or "none"
            last_error  = (r.get("error", "") or "none")[:25]
    except Exception:
        pass

    from modules.sub_notif import NOTIF_CATEGORIES, _default_enabled

    # Build compact category grid — 2 per row to fit 249 char limit
    cat_keys = list(NOTIF_CATEGORIES.keys())
    cat_rows = []
    for i in range(0, len(cat_keys), 2):
        parts = []
        for k in cat_keys[i:i+2]:
            label = NOTIF_CATEGORIES[k]
            val   = prefs.get(k, _default_enabled(k))
            parts.append(f"{label}:{'ON' if val else 'OFF'}")
        cat_rows.append(" | ".join(parts))

    msg1 = "\n".join([
        f"🔔 Notify Debug: @{uname}",
        f"Subscribed: {'YES' if subbed else 'NO'}  Global: {'ON' if global_on else 'OFF'}",
    ] + cat_rows)

    msg2 = "\n".join([
        f"In Room: {'YES' if in_room else 'NO'}",
        f"User ID Found: {'YES' if uid else 'NO'}",
        f"Convo ID Found: {'YES' if conv_id else 'NO'}",
        f"Last: {last_status}",
        f"Method: {last_method}",
        f"Error: {last_error}",
    ])

    await _w(bot, user.id, msg1[:249])
    await _w(bot, user.id, msg2[:249])


# ---------------------------------------------------------------------------
# /roomusers
# ---------------------------------------------------------------------------

async def handle_roomusers(bot, user, args: list[str] | None = None) -> None:
    """/roomusers — list players EmceeBot currently sees in the room."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    source = "get_room_users"
    users_in_room: list[tuple[str, str]] = []

    try:
        resp = await bot.highrise.get_room_users()
        if hasattr(resp, "content"):
            for item in resp.content:
                u = item[0]
                users_in_room.append((u.id, u.username))
        else:
            raise ValueError("no content attr")
    except Exception:
        source = "recent seen fallback"
        try:
            from modules.room_utils import _user_positions
            for uid in list(_user_positions.keys()):
                users_in_room.append((uid, uid[:8]))
        except Exception:
            pass

    count  = len(users_in_room)
    shown  = users_in_room[:10]
    lines  = [f"👥 Room Users\nDetected: {count}"]
    for _, uname in shown:
        lines.append(f"@{uname}")
    if count > 10:
        lines.append(f"...and {count - 10} more")
    lines.append(f"Source: {source}")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /testwhisper @username
# ---------------------------------------------------------------------------

async def handle_testwhisper(bot, user, args: list[str]) -> None:
    """/testwhisper @username — test if EmceeBot can whisper a player."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /testwhisper @username")
        return

    raw     = args[1].lstrip("@").lower()
    sub_row = db.get_subscriber(raw)
    uid     = sub_row.get("user_id", "") if sub_row else ""

    in_room = False
    if uid:
        try:
            from modules.sub_notif import _get_live_room_user_ids
            in_room = uid in (await _get_live_room_user_ids(bot))
        except Exception:
            try:
                from modules.room_utils import _user_positions
                in_room = uid in _user_positions
            except Exception:
                pass

    whisper_sent = False
    error_msg    = ""
    if uid:
        try:
            await bot.highrise.send_whisper(uid, "🧪 Test whisper from EmceeBot.")
            whisper_sent = True
        except Exception as exc:
            error_msg = str(exc)[:60]

    lines = [
        "🧪 Test Whisper",
        f"Target: @{raw}",
        f"User ID Found: {'YES' if uid else 'NO'}",
        f"Currently In Room: {'YES' if in_room else 'NO'}",
        f"Whisper Sent: {'YES' if whisper_sent else 'NO'}",
    ]
    if error_msg:
        lines.append(f"Error: {error_msg}")
    elif not uid:
        lines.append("Note: player not found in subscriber DB.")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /notifrefresh
# ---------------------------------------------------------------------------

async def handle_notifrefresh(bot, user, args: list[str] | None = None) -> None:
    """/notifrefresh — refresh in-memory room user cache."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    source = "get_room_users"
    count  = 0

    try:
        resp = await bot.highrise.get_room_users()
        if hasattr(resp, "content"):
            ids = {item[0].id for item in resp.content}
            count = len(ids)
            try:
                from modules.room_utils import _user_positions
                stale = [k for k in list(_user_positions.keys()) if k not in ids]
                for k in stale:
                    _user_positions.pop(k, None)
            except Exception:
                pass
        else:
            raise ValueError("no content")
    except Exception:
        source = "recent seen fallback"
        try:
            from modules.room_utils import _user_positions
            count = len(_user_positions)
        except Exception:
            count = 0

    await _w(bot, user.id,
             f"🔄 Notification Room Cache Refreshed\n"
             f"Room users detected: {count}\n"
             f"Source: {source}")
