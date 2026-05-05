"""
modules/notifications.py
------------------------
Central notification delivery + preference management for the Highrise bot.

send_notification()        — unified delivery hub (DM → whisper → pending)

Player commands:
  /notifysettings [2]      — show preference summary (page 1 or 2)
  /notify <type> on|off    — toggle one or all preference types
  /notifyhelp              — help for notify commands
  /notifications           — show pending notifications
  /clearnotifications      — clear pending notifications

Staff commands (manager+):
  /notifystats             — subscriber aggregate stats
  /notifyprefs <username>  — view a user's preferences
  /notifyuser <u> <t> on|off — override a user's preference
  /broadcasttest <type>    — send a test notification of given type
"""
from __future__ import annotations

import asyncio
import database as db
from highrise import BaseBot, User
from modules.permissions import is_owner, is_admin, can_moderate, is_manager


# ── Permission helpers ────────────────────────────────────────────────────────

def _is_admin_or_owner(username: str) -> bool:
    return is_owner(username) or is_admin(username)


def _is_manager_or_above(username: str) -> bool:
    return is_owner(username) or is_admin(username) or is_manager(username)


def _is_staff(username: str) -> bool:
    return can_moderate(username)


# ── Messaging helpers ─────────────────────────────────────────────────────────

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


async def _dm(bot: BaseBot, conv_id: str, msg: str) -> bool:
    try:
        await bot.highrise.send_message(conv_id, msg[:249])
        return True
    except Exception as exc:
        print(f"[NOTIFY] send_message failed (conv={conv_id[:12]}...): {exc}")
        return False


# ── Preference type → column mapping ─────────────────────────────────────────

_PREF_COLUMN: dict[str, str] = {
    "bank":          "bank_alerts",
    "events":        "event_alerts",
    "event":         "event_alerts",
    "gold":          "gold_alerts",
    "vip":           "vip_alerts",
    "casino":        "casino_alerts",
    "quests":        "quest_alerts",
    "quest":         "quest_alerts",
    "shop":          "shop_alerts",
    "announcements": "announcement_alerts",
    "announcement":  "announcement_alerts",
    "staff":         "staff_alerts",
    "dm":            "dm_alerts",
    "whisper":       "room_whisper_alerts",
}

_UNSUB_FOOTER = "\nStop alerts: reply unsubscribe."


def _cap(msg: str, footer: str = "") -> str:
    """Append footer if it fits; truncate main message to keep total ≤ 249."""
    if not footer:
        return msg[:249]
    if len(msg) + len(footer) <= 249:
        return msg + footer
    return msg[: 249 - len(footer)] + footer


# ── Central delivery function ─────────────────────────────────────────────────

async def send_notification(
    bot: BaseBot,
    username: str,
    notification_type: str,
    message: str,
    prefer_dm: bool = True,
) -> str:
    """
    Deliver a notification to *username* using the best available channel.

    Delivery order:
      1. Outside-room DM  (dm_alerts ON, dm_available, conversation_id set)
      2. Room whisper      (room_whisper_alerts ON, user_id available — may fail if not in room)
      3. pending_notifications queue

    Returns one of: "sent", "whispered", "pending", "skipped".
    """
    uname = username.lower().lstrip("@").strip()

    try:
        sub = db.get_subscriber(uname)
        if not sub or not sub.get("subscribed"):
            db.log_notification(uname, notification_type, "none", message, "skipped",
                                "not subscribed")
            return "skipped"

        prefs = db.get_notify_prefs(uname)
        pref_col = _PREF_COLUMN.get(notification_type, "announcement_alerts")

        if not prefs.get(pref_col, 1):
            db.log_notification(uname, notification_type, "none", message, "skipped",
                                f"pref {pref_col} OFF")
            return "skipped"

        if notification_type == "staff" and not _is_staff(uname):
            db.log_notification(uname, notification_type, "none", message, "skipped",
                                "not staff")
            return "skipped"

        dm_msg = _cap(message, _UNSUB_FOOTER)
        conv_id   = sub.get("conversation_id")
        dm_avail  = sub.get("dm_available")
        user_id   = sub.get("user_id")

        # ── Try DM ────────────────────────────────────────────────────────
        if prefer_dm and prefs.get("dm_alerts", 1) and conv_id and dm_avail:
            ok = await _dm(bot, conv_id, dm_msg)
            if ok:
                db.set_subscriber_last_dm(uname)
                db.log_notification(uname, notification_type, "dm", dm_msg, "sent")
                return "sent"
            db.set_dm_available(uname, False)

        # ── Try room whisper ───────────────────────────────────────────────
        if prefs.get("room_whisper_alerts", 1) and user_id:
            try:
                await bot.highrise.send_whisper(user_id, message[:249])
                db.log_notification(uname, notification_type, "whisper", message, "sent")
                return "whispered"
            except Exception:
                pass

        # ── Queue as pending ───────────────────────────────────────────────
        db.add_pending_notification(uname, notification_type, dm_msg)
        db.log_notification(uname, notification_type, "pending", dm_msg, "pending")
        return "pending"

    except Exception as exc:
        print(f"[NOTIFY] send_notification error for @{uname}: {exc!r}")
        db.log_notification(uname, notification_type, "error", message, "failed", str(exc))
        return "pending"


# ── Deliver pending notifications on join/DM ──────────────────────────────────

async def deliver_pending_notifications(
    bot: BaseBot, username: str, conversation_id: str | None = None
) -> None:
    """
    Deliver queued pending_notifications for *username*.
    Called on join, DM, or first chat.
    """
    try:
        uname = username.lower().strip()
        pending = db.get_pending_notifications(uname)
        if not pending:
            return

        sub = db.get_subscriber(uname)
        prefs = db.get_notify_prefs(uname)

        if not sub or not sub.get("subscribed"):
            return

        conv = conversation_id or sub.get("conversation_id")
        dm_ok = bool(conv and sub.get("dm_available") and prefs.get("dm_alerts", 1))
        user_id = sub.get("user_id")

        if len(pending) > 3:
            summary = f"🔔 You have {len(pending)} pending alerts. Use /notifications to see them."
            if dm_ok:
                await _dm(bot, conv, summary)
            elif user_id:
                try:
                    await bot.highrise.send_whisper(user_id, summary[:249])
                except Exception:
                    pass
            return

        for notif in pending:
            notif_id = notif["id"]
            msg = notif["message"]
            pref_col = _PREF_COLUMN.get(notif.get("notification_type", "general"), "announcement_alerts")

            if not prefs.get(pref_col, 1):
                db.mark_pending_notification_delivered(notif_id)
                db.log_notification(uname, notif.get("notification_type", ""), "skipped", msg, "skipped")
                continue

            if dm_ok:
                ok = await _dm(bot, conv, msg)
                if ok:
                    db.mark_pending_notification_delivered(notif_id)
                    db.set_subscriber_last_dm(uname)
                    continue
                else:
                    db.mark_pending_notification_failed(notif_id, "send_message failed")
            elif user_id:
                try:
                    await bot.highrise.send_whisper(user_id, msg[:249])
                    db.mark_pending_notification_delivered(notif_id)
                    continue
                except Exception as exc:
                    db.mark_pending_notification_failed(notif_id, str(exc))
            await asyncio.sleep(0.4)
    except Exception as exc:
        print(f"[NOTIFY] deliver_pending_notifications error for @{username}: {exc!r}")


# ── /notifysettings ───────────────────────────────────────────────────────────

async def handle_notifysettings(bot: BaseBot, user: User, args: list[str]) -> None:
    """/notifysettings [2] — show notification preference summary."""
    db.ensure_user(user.id, user.username)
    db.ensure_notify_prefs(user.username)
    prefs = db.get_notify_prefs(user.username)
    page = args[1].strip() if len(args) > 1 else "1"

    def _yn(col: str) -> str:
        return "ON" if prefs.get(col, 1) else "OFF"

    if page == "2":
        msg = (
            f"🔔 More: Casino {_yn('casino_alerts')} | Quests {_yn('quest_alerts')} | "
            f"Shop {_yn('shop_alerts')} | DM {_yn('dm_alerts')} | "
            f"Whisper {_yn('room_whisper_alerts')}"
        )
    else:
        msg = (
            f"🔔 Alerts: Bank {_yn('bank_alerts')} | Events {_yn('event_alerts')} | "
            f"Gold {_yn('gold_alerts')} | VIP {_yn('vip_alerts')} | "
            f"Announce {_yn('announcement_alerts')}"
        )
    await _w(bot, user.id, msg[:249])


# ── /notify <type> on|off ─────────────────────────────────────────────────────

async def handle_notify(bot: BaseBot, user: User, args: list[str]) -> None:
    """/notify <type> on|off — toggle a notification preference."""
    db.ensure_user(user.id, user.username)
    if len(args) < 3:
        await _w(bot, user.id,
                 "Usage: /notify <type> on|off\n"
                 "Types: bank events gold vip casino quests shop announcements dm whisper all")
        return

    ntype  = args[1].lower().strip()
    toggle = args[2].lower().strip()

    if toggle not in ("on", "off"):
        await _w(bot, user.id, "Usage: /notify <type> on|off")
        return

    val = 1 if toggle == "on" else 0
    uname = user.username.lower()

    if ntype == "all":
        db.set_all_notify_prefs(uname, val)
        await _w(bot, user.id, f"✅ All alerts set to {toggle.upper()}.")
        return

    col = _PREF_COLUMN.get(ntype)
    if not col:
        await _w(bot, user.id,
                 "Unknown type. Use: bank events gold vip casino quests shop "
                 "announcements staff dm whisper all")
        return

    try:
        db.set_notify_pref(uname, col, val)
        await _w(bot, user.id, f"✅ {ntype.capitalize()} alerts set to {toggle.upper()}.")
    except Exception as exc:
        await _w(bot, user.id, "❌ Error saving preference.")
        print(f"[NOTIFY] handle_notify error: {exc!r}")


# ── /notifyhelp ───────────────────────────────────────────────────────────────

async def handle_notifyhelp(bot: BaseBot, user: User, args: list[str]) -> None:
    """/notifyhelp — help for notification preference commands."""
    page = args[1].strip() if len(args) > 1 else "1"
    if page == "2":
        await _w(bot, user.id,
                 "Types:\nbank events gold vip\n"
                 "casino quests shop\nannouncements staff\ndm whisper all")
    else:
        await _w(bot, user.id,
                 "🔔 Notify\n"
                 "/notifysettings\n"
                 "/notify bank on/off\n"
                 "/notify events on/off\n"
                 "/notify gold on/off\n"
                 "/notify all on/off")


# ── /notifications ────────────────────────────────────────────────────────────

async def handle_notifications(bot: BaseBot, user: User, args: list[str]) -> None:
    """/notifications — show pending notifications."""
    db.ensure_user(user.id, user.username)
    uname = user.username.lower()
    pending = db.get_pending_notifications(uname)

    if not pending:
        await _w(bot, user.id, "📭 No pending notifications.")
        return

    lines = [f"🔔 {len(pending)} pending alert(s):"]
    for n in pending[:4]:
        ntype = n.get("notification_type", "?")
        msg   = n.get("message", "")[:35]
        lines.append(f"[{ntype}] {msg}...")
    if len(pending) > 4:
        lines.append(f"+{len(pending)-4} more. /clearnotifications to dismiss.")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ── /clearnotifications ───────────────────────────────────────────────────────

async def handle_clearnotifications(bot: BaseBot, user: User, args: list[str]) -> None:
    """/clearnotifications — mark all pending notifications as read."""
    db.ensure_user(user.id, user.username)
    uname = user.username.lower()
    pending = db.get_pending_notifications(uname)
    count = len(pending)
    if not count:
        await _w(bot, user.id, "📭 No pending notifications to clear.")
        return
    db.mark_all_pending_notifications_read(uname)
    await _w(bot, user.id, f"✅ {count} notification(s) cleared.")


# ── /notifystats (manager+) ───────────────────────────────────────────────────

async def handle_notifystats(bot: BaseBot, user: User, args: list[str]) -> None:
    """/notifystats — show subscriber aggregate stats."""
    if not _is_manager_or_above(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    try:
        stats = db.get_notify_stats()
        msg = (
            f"🔔 Subs: {stats['total']} | "
            f"DM: {stats['dm_connected']} | "
            f"Off: {stats['unsubscribed']} | "
            f"Pending: {stats['pending']}"
        )
        await _w(bot, user.id, msg)
    except Exception as exc:
        await _w(bot, user.id, "❌ Error fetching stats.")
        print(f"[NOTIFY] notifystats error: {exc!r}")


# ── /notifyprefs <username> (manager+) ───────────────────────────────────────

async def handle_notifyprefs(bot: BaseBot, user: User, args: list[str]) -> None:
    """/notifyprefs <username> — view a user's notification preferences."""
    if not _is_manager_or_above(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /notifyprefs <username>")
        return

    target = args[1].lstrip("@").lower().strip()
    prefs = db.get_notify_prefs(target)

    def _yn(col: str) -> str:
        return "Y" if prefs.get(col, 1) else "N"

    msg = (
        f"🔔 @{target}:\n"
        f"Bank {_yn('bank_alerts')} Events {_yn('event_alerts')} "
        f"Gold {_yn('gold_alerts')} VIP {_yn('vip_alerts')}\n"
        f"Casino {_yn('casino_alerts')} Quests {_yn('quest_alerts')} "
        f"Shop {_yn('shop_alerts')}\n"
        f"Announce {_yn('announcement_alerts')} Staff {_yn('staff_alerts')} "
        f"DM {_yn('dm_alerts')} Whisper {_yn('room_whisper_alerts')}"
    )
    await _w(bot, user.id, msg[:249])


# ── /notifyuser <username> <type> on|off (admin+) ─────────────────────────────

async def handle_notifyuser(bot: BaseBot, user: User, args: list[str]) -> None:
    """/notifyuser <username> <type> on|off — override a user's notification pref."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 4:
        await _w(bot, user.id, "Usage: /notifyuser <username> <type> on|off")
        return

    target = args[1].lstrip("@").lower().strip()
    ntype  = args[2].lower().strip()
    toggle = args[3].lower().strip()

    if toggle not in ("on", "off"):
        await _w(bot, user.id, "Usage: /notifyuser <username> <type> on|off")
        return

    val = 1 if toggle == "on" else 0

    if ntype == "all":
        db.set_all_notify_prefs(target, val)
        await _w(bot, user.id, f"✅ All alerts for @{target} set to {toggle.upper()}.")
        return

    col = _PREF_COLUMN.get(ntype)
    if not col:
        await _w(bot, user.id, "Unknown type. Use: bank events gold vip casino quests shop announcements staff dm whisper all")
        return

    try:
        db.set_notify_pref(target, col, val)
        await _w(bot, user.id, f"✅ {ntype} alerts for @{target} set to {toggle.upper()}.")
    except Exception as exc:
        await _w(bot, user.id, "❌ Error updating preference.")
        print(f"[NOTIFY] notifyuser error: {exc!r}")


# ── /broadcasttest <type> (admin+) ───────────────────────────────────────────

async def handle_broadcasttest(bot: BaseBot, user: User, args: list[str]) -> None:
    """/broadcasttest <type> — send a test alert of given type to subscribed users."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 2:
        await _w(bot, user.id,
                 "Usage: /broadcasttest <type>\n"
                 "Types: bank events gold vip casino quests shop announcements staff")
        return

    ntype = args[1].lower().strip()
    if ntype not in _PREF_COLUMN and ntype != "all":
        await _w(bot, user.id, "Unknown type. Use: bank events gold vip casino quests shop announcements staff")
        return

    test_msg = f"🔔 Test {ntype} alert."
    subs_with_dm  = db.get_all_subscribed_with_dm()
    subs_no_dm    = db.get_all_subscribed_no_dm()
    all_subs      = subs_with_dm + subs_no_dm

    if not all_subs:
        await _w(bot, user.id, "No subscribers found.")
        return

    await _w(bot, user.id, f"📤 Sending {ntype} test to up to {len(all_subs)} subscriber(s)...")
    sent = pending = skipped = 0

    for sub in all_subs:
        uname = sub["username"]
        result = await send_notification(bot, uname, ntype, test_msg)
        if result in ("sent", "whispered"):
            sent += 1
        elif result == "pending":
            pending += 1
        else:
            skipped += 1
        await asyncio.sleep(0.8)

    await _w(bot, user.id,
             f"📣 Test done: {sent} sent, {pending} pending, {skipped} skipped.")
    print(f"[NOTIFY] broadcasttest {ntype} by @{user.username}: {sent}s {pending}p {skipped}sk")


# ── /debugnotify <username> (admin+) ─────────────────────────────────────────

async def handle_debugnotify(bot: BaseBot, user: User, args: list[str]) -> None:
    """/debugnotify <username> — full diagnostic view for a subscriber."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /debugnotify <username>")
        return

    target = args[1].lstrip("@").lower().strip()
    sub = db.get_subscriber(target)

    if not sub:
        await _w(bot, user.id, f"No subscriber record for @{target}.")
        return

    sub_flag    = "YES" if sub.get("subscribed") else "NO"
    dm_flag     = "YES" if sub.get("dm_available") else "NO"
    conv_flag   = "YES" if sub.get("conversation_id") else "NO"
    manual_unsub = "YES" if sub.get("manually_unsubscribed") else "NO"
    last_dm     = (sub.get("last_dm_at") or "never")[:16]

    pending = db.get_pending_notifications(target)
    pend_count = len(pending)

    lines = [
        f"🔍 @{target} notify debug:",
        f"Sub: {sub_flag} | DM avail: {dm_flag} | Conv saved: {conv_flag}",
        f"Manual unsub: {manual_unsub}",
        f"Last DM: {last_dm}",
        f"Pending: {pend_count}",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])


# ── /testnotify <username> <type> (admin+) ────────────────────────────────────

async def handle_testnotify(bot: BaseBot, user: User, args: list[str]) -> None:
    """/testnotify <username> <type> — send a test notification to one user."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 3:
        await _w(bot, user.id,
                 "Usage: /testnotify <username> <type>\n"
                 "Types: bank events gold vip casino quests shop announcements staff")
        return

    target = args[1].lstrip("@").lower().strip()
    ntype  = args[2].lower().strip()

    if ntype not in _PREF_COLUMN:
        await _w(bot, user.id,
                 "Unknown type. Use: bank events gold vip casino quests shop announcements staff")
        return

    sub = db.get_subscriber(target)
    if not sub:
        await _w(bot, user.id, f"No subscriber record for @{target}.")
        return

    test_msg = f"🔔 Test {ntype} alert."
    result = await send_notification(bot, target, ntype, test_msg)

    if result in ("sent", "whispered"):
        await _w(bot, user.id, f"✅ Test {ntype} alert sent to @{target}.")
    elif result == "pending":
        await _w(bot, user.id,
                 f"✅ Test saved as pending for @{target} (no DM/whisper available).")
    else:
        await _w(bot, user.id,
                 f"Notification skipped: @{target} unsubscribed or {ntype} alerts OFF.")

    print(f"[NOTIFY] testnotify {ntype} → @{target} by @{user.username}: {result}")


# ── /testnotifyall <type> (owner only) ────────────────────────────────────────

async def handle_testnotifyall(bot: BaseBot, user: User, args: list[str]) -> None:
    """/testnotifyall <type> — owner: send test notification to all matching subscribers."""
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id,
                 "Usage: /testnotifyall <type>\n"
                 "Types: bank events gold vip casino quests shop announcements staff")
        return

    ntype = args[1].lower().strip()
    if ntype not in _PREF_COLUMN:
        await _w(bot, user.id,
                 "Unknown type. Use: bank events gold vip casino quests shop announcements staff")
        return

    all_subs = db.get_all_subscribed_with_dm() + db.get_all_subscribed_no_dm()
    if not all_subs:
        await _w(bot, user.id, "No subscribers found.")
        return

    await _w(bot, user.id, f"📤 Sending {ntype} test to {len(all_subs)} subscriber(s)...")
    test_msg = f"🔔 Test {ntype} alert."
    sent = pending = skipped = 0

    for sub in all_subs:
        uname = sub["username"]
        result = await send_notification(bot, uname, ntype, test_msg)
        if result in ("sent", "whispered"):
            sent += 1
        elif result == "pending":
            pending += 1
        else:
            skipped += 1
        await asyncio.sleep(1.0)

    await _w(bot, user.id,
             f"Test sent: {sent} delivered, {pending} pending, {skipped} skipped.")
    print(f"[NOTIFY] testnotifyall {ntype} by @{user.username}: {sent}s {pending}p {skipped}sk")


# ── /pendingnotify <username> (admin+) ────────────────────────────────────────

async def handle_pendingnotify(bot: BaseBot, user: User, args: list[str]) -> None:
    """/pendingnotify <username> — show pending notification count + latest types."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /pendingnotify <username>")
        return

    target  = args[1].lstrip("@").lower().strip()
    pending = db.get_pending_notifications(target)

    if not pending:
        await _w(bot, user.id, f"📭 No pending notifications for @{target}.")
        return

    count  = len(pending)
    latest = pending[-3:]  # newest 3
    types  = ", ".join(n.get("notification_type", "?") for n in latest)
    await _w(bot, user.id,
             f"🔔 @{target}: {count} pending\nLatest types: {types}"[:249])


# ── /clearpendingnotify <username> (admin+) ───────────────────────────────────

async def handle_clearpendingnotify(bot: BaseBot, user: User, args: list[str]) -> None:
    """/clearpendingnotify <username> — clear all pending notifications for a user."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /clearpendingnotify <username>")
        return

    target  = args[1].lstrip("@").lower().strip()
    pending = db.get_pending_notifications(target)
    count   = len(pending)

    if not count:
        await _w(bot, user.id, f"📭 No pending notifications for @{target}.")
        return

    db.mark_all_pending_notifications_read(target)
    await _w(bot, user.id, f"✅ Cleared {count} pending notification(s) for @{target}.")
    print(f"[NOTIFY] clearpendingnotify @{target} by @{user.username}: {count} cleared.")
