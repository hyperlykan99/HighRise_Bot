"""
modules/notif_v2.py
────────────────────────────────────────────────────────────────────
Manual-subscribe-only notification system with configurable categories.

Player commands:
  !sub / !subscribe           — manual subscribe only (never auto)
  !unsub / !unsubscribe       — unsubscribe
  !notifysettings / !alerts   — view subscription status + categories
  !notify [category] on|off   — toggle a category

Admin commands (admin/owner only):
  !announcement [msg]         — broadcast to announcements category
  !promo [msg]                — broadcast to promos category
  !eventalert [msg]           — broadcast to events category
  !gamealert [msg]            — broadcast to games category
  !tipalert [msg]             — broadcast to tips category
  !substatus @user            — view a user's subscription (admin)
  !subcount                   — subscriber aggregate counts
  !unsubuser @user            — force-unsubscribe a user

Bank transfer confirmations bypass subscription checks entirely.
# Transactional bank messages bypass notification subscriptions.
"""
from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot, User

import database as db
from modules.permissions import is_owner, can_manage_economy, is_admin


# ─── Category registry ────────────────────────────────────────────────────────

CATEGORIES: tuple[str, ...] = ("events", "games", "announcements", "promos", "tips")

_CATEGORY_LABELS: dict[str, str] = {
    "events":        "Events",
    "games":         "Games",
    "announcements": "Announcements",
    "promos":        "Promos",
    "tips":          "Tips",
}

_CATEGORY_ALIASES: dict[str, str] = {
    "event":        "events",
    "game":         "games",
    "announcement": "announcements",
    "promo":        "promos",
    "promotions":   "promos",
    "promotion":    "promos",
    "tip":          "tips",
}

_BROADCAST_HEADERS: dict[str, str] = {
    "announcements": "📣 Announcement",
    "promos":        "🎁 Promo",
    "events":        "🎉 Event Alert",
    "games":         "🎮 Game Alert",
    "tips":          "💰 Tip Alert",
}


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


def _resolve_category(raw: str) -> str | None:
    """Return canonical category name or None if invalid."""
    c = raw.lower().strip()
    if c in CATEGORIES:
        return c
    return _CATEGORY_ALIASES.get(c)


def _is_admin(username: str) -> bool:
    return is_owner(username) or can_manage_economy(username) or is_admin(username)


def _yn(val: int | bool) -> str:
    return "ON" if val else "OFF"


# ─── Player: !notifysettings / !alerts ───────────────────────────────────────

async def handle_notifysettings_v2(bot: "BaseBot", user: "User") -> None:
    """!notifysettings / !alerts — show subscription status + 5-category view."""
    row = db.get_notification_subscription(user.id)

    if not row or not row.get("subscribed"):
        await _w(bot, user.id,
                 "🔔 Notifications\n"
                 "Status: OFF\n"
                 "Use !sub to subscribe.")
        return

    await _w(bot, user.id,
             f"🔔 Notification Settings\n"
             f"Status: ON\n"
             f"Events: {_yn(row.get('events', 1))}\n"
             f"Games: {_yn(row.get('games', 1))}\n"
             f"Announcements: {_yn(row.get('announcements', 1))}\n"
             f"Promos: {_yn(row.get('promos', 1))}\n"
             f"Tips: {_yn(row.get('tips', 0))}")
    await _w(bot, user.id,
             "Commands:\n"
             "!notify events on/off\n"
             "!notify games on/off\n"
             "!notify promos on/off\n"
             "!notify tips on/off")


# ─── Player: !notify [category] [on/off] ─────────────────────────────────────

async def handle_notify_v2(
    bot: "BaseBot", user: "User", args: list[str]
) -> None:
    """
    !notify [category] [on/off] — toggle a notification category.
    New categories: events, games, announcements, promos, tips.
    Falls through to old handler for unrecognised categories.
    """
    if len(args) < 3:
        await _w(bot, user.id,
                 "Usage: !notify [category] on/off\n"
                 "Categories: events games announcements promos tips")
        return

    cat = _resolve_category(args[1])
    toggle = args[2].lower().strip()

    if cat is None:
        # Unknown category — let the old handler deal with it
        from modules.notifications import handle_notify as _old_handle_notify
        await _old_handle_notify(bot, user, args)
        return

    if toggle not in ("on", "off"):
        await _w(bot, user.id,
                 f"Use: !notify {args[1]} on or !notify {args[1]} off")
        return

    row = db.get_notification_subscription(user.id)
    if not row or not row.get("subscribed"):
        await _w(bot, user.id, "⚠️ You are not subscribed. Use !sub first.")
        return

    enabled = toggle == "on"
    db.set_notification_category(user.id, user.username, cat, enabled)
    db.insert_notification_action_log(
        "notification_settings_change", user.id, user.username,
        category=cat, details=toggle,
    )
    label = _CATEGORY_LABELS[cat]
    await _w(bot, user.id, f"✅ {label} alerts {toggle.upper()}.")
    print(f"[NOTIFY] settings_change user=@{user.username} cat={cat} val={toggle}")


# ─── Player help ─────────────────────────────────────────────────────────────

async def handle_notifyhelp_player(bot: "BaseBot", user: "User") -> None:
    """Player notification help."""
    await _w(bot, user.id,
             "🔔 Notifications\n"
             "DM bot: !sub\n"
             "Stop: !unsub\n"
             "Settings: !notifysettings")
    await _w(bot, user.id,
             "!notify events on/off\n"
             "!notify games on/off\n"
             "!notify announcements on/off\n"
             "!notify promos on/off\n"
             "!notify tips on/off")


# ─── Broadcast engine ────────────────────────────────────────────────────────

async def _broadcast_to_category(
    bot: "BaseBot",
    actor_username: str,
    category: str,
    message: str,
) -> tuple[int, int, int]:
    """
    DM/whisper all subscribers with category=ON.
    Rate-limited: 0.3–0.7s sleep between sends.
    Returns (delivered, skipped, failed).
    """
    users = db.get_subscribed_users_for_category(category)
    if not users:
        return 0, 0, 0

    header = _BROADCAST_HEADERS.get(category, "📣 Alert")
    full_msg = f"{header}\n{message}"[:249]
    delivered = skipped = failed = 0

    for row in users:
        uid      = row.get("user_id", "")
        uname    = row.get("username", "")
        if not uid:
            skipped += 1
            print(f"[NOTIFY] skipped user=@{uname} reason=no_user_id")
            continue

        # Try whisper (works if user is in room)
        sent = False
        try:
            await bot.highrise.send_whisper(uid, full_msg)
            sent = True
            delivered += 1
            print(f"[NOTIFY] notification_dm_sent user=@{uname} cat={category}")
        except Exception:
            pass

        if not sent:
            # Try conversation DM
            sub = db.get_subscriber_by_user_id(uid) or db.get_subscriber(uname)
            conv_id = sub.get("conversation_id") if sub else None
            dm_avail = sub.get("dm_available") if sub else False
            if conv_id and dm_avail:
                try:
                    await bot.highrise.send_message(conv_id, full_msg)
                    delivered += 1
                    print(f"[NOTIFY] notification_dm_sent user=@{uname} cat={category} method=conv_dm")
                    sent = True
                except Exception as e:
                    failed += 1
                    print(f"[NOTIFY] notification_dm_failed user=@{uname} reason={e!r}")
            else:
                skipped += 1
                print(f"[NOTIFY] skipped user=@{uname} reason=no_delivery_route cat={category}")

        delay = random.uniform(0.3, 0.7)
        await asyncio.sleep(delay)

    db.insert_notification_action_log(
        "notification_broadcast",
        details=f"cat={category} delivered={delivered} failed={failed} skipped={skipped}",
    )
    print(
        f"[NOTIFY BROADCAST] category={category} delivered={delivered} "
        f"failed={failed} skipped={skipped} by=@{actor_username}"
    )
    return delivered, skipped, failed


async def _run_broadcast(
    bot: "BaseBot", user: "User", args: list[str], category: str
) -> None:
    """Shared broadcast dispatcher used by all broadcast commands."""
    if not _is_admin(user.username):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    if len(args) < 2:
        label = _CATEGORY_LABELS.get(category, category.capitalize())
        await _w(bot, user.id, f"Usage: !{category[:8]}alert {label} message here")
        return
    message = " ".join(args[1:]).strip()
    if not message:
        await _w(bot, user.id, "⚠️ Broadcast message cannot be empty.")
        return
    users = db.get_subscribed_users_for_category(category)
    if not users:
        await _w(bot, user.id, f"No subscribed users for {category}.")
        return
    await _w(bot, user.id, f"📤 Sending to {len(users)} subscriber(s)...")
    delivered, skipped, failed = await _broadcast_to_category(
        bot, user.username, category, message
    )
    await _w(bot, user.id,
             f"📣 Sent.\n"
             f"Delivered: {delivered}\n"
             f"Skipped: {skipped}\n"
             f"Failed: {failed}")


# ─── Admin broadcast commands ─────────────────────────────────────────────────

async def handle_announcement(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!announcement [msg] — broadcast to announcements category."""
    await _run_broadcast(bot, user, args, "announcements")


async def handle_promo(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!promo [msg] — broadcast to promos category."""
    await _run_broadcast(bot, user, args, "promos")


async def handle_eventalert(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!eventalert [msg] — broadcast to events category."""
    await _run_broadcast(bot, user, args, "events")


async def handle_gamealert(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!gamealert [msg] — broadcast to games category."""
    await _run_broadcast(bot, user, args, "games")


async def handle_tipalert(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!tipalert [msg] — broadcast to tips category."""
    await _run_broadcast(bot, user, args, "tips")


# ─── Admin tools ─────────────────────────────────────────────────────────────

async def handle_substatus_admin(
    bot: "BaseBot", user: "User", args: list[str]
) -> None:
    """!substatus @user — admin view of a user's notification subscription."""
    if not _is_admin(user.username):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !substatus @user")
        return
    target_name = args[1].lstrip("@").strip().lower()
    rec = db.get_user_by_username(target_name)
    if not rec:
        await _w(bot, user.id, f"@{target_name} not found in DB.")
        return
    row = db.get_notification_subscription(rec["user_id"])
    sub_row = db.get_subscriber_by_user_id(rec["user_id"]) or db.get_subscriber(target_name)
    subscribed = bool(row.get("subscribed")) or bool(sub_row and sub_row.get("subscribed"))
    source = row.get("source", "manual") if row else "manual"

    if not row and not sub_row:
        await _w(bot, user.id, f"@{target_name} has no subscription record.")
        return

    await _w(bot, user.id,
             f"@{rec['username']} notifications: {'ON' if subscribed else 'OFF'}\n"
             f"Events: {_yn(row.get('events', 1))}\n"
             f"Games: {_yn(row.get('games', 1))}\n"
             f"Announcements: {_yn(row.get('announcements', 1))}\n"
             f"Promos: {_yn(row.get('promos', 1))}\n"
             f"Tips: {_yn(row.get('tips', 0))}\n"
             f"Source: {source}")


async def handle_subcount(bot: "BaseBot", user: "User") -> None:
    """!subcount — aggregate subscription counts (admin)."""
    if not _is_admin(user.username):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    counts = db.get_notification_subscription_counts()
    await _w(bot, user.id,
             f"🔔 Subscribers: {counts.get('total', 0)}\n"
             f"Events ON: {counts.get('events', 0)}\n"
             f"Games ON: {counts.get('games', 0)}\n"
             f"Announcements ON: {counts.get('announcements', 0)}\n"
             f"Promos ON: {counts.get('promos', 0)}\n"
             f"Tips ON: {counts.get('tips', 0)}")


async def handle_unsubuser(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!unsubuser @user — force-unsubscribe a user (admin)."""
    if not _is_admin(user.username):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !unsubuser @user")
        return
    target_name = args[1].lstrip("@").strip().lower()
    rec = db.get_user_by_username(target_name)
    if not rec:
        await _w(bot, user.id, f"@{target_name} not found in DB.")
        return
    try:
        db.set_notification_subscribed(rec["user_id"], target_name, False, source="manual")
        db.set_subscribed(target_name, False)
        db.set_subscriber_manually_unsubscribed(target_name, True)
        db.insert_notification_action_log(
            "notification_unsubscribe", rec["user_id"], target_name,
            details=f"admin_forced by @{user.username}",
        )
        print(f"[NOTIFY] admin_unsubuser target=@{target_name} by=@{user.username}")
    except Exception as e:
        await _w(bot, user.id, f"⚠️ Error: {e!r}")
        return
    await _w(bot, user.id, f"✅ @{rec['username']} unsubscribed from notifications.")


# ─── Admin notification help ──────────────────────────────────────────────────

async def handle_notifyadmin_help(bot: "BaseBot", user: "User") -> None:
    """Admin help for notification broadcast commands."""
    if not _is_admin(user.username):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    await _w(bot, user.id,
             "🔔 Notification Admin\n"
             "!announcement message\n"
             "!promo message\n"
             "!eventalert message\n"
             "!gamealert message\n"
             "!tipalert message")
    await _w(bot, user.id,
             "!substatus @user\n"
             "!subcount\n"
             "!unsubuser @user")


# ─── Admin audit: !notifyaudit / !notifystatus ────────────────────────────────

async def handle_notifyaudit_admin(
    bot: "BaseBot", user: "User", args: list[str]
) -> None:
    """!notifyaudit @user — full canonical subscription audit (admin)."""
    if not _is_admin(user.username):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !notifyaudit @user")
        return
    target_name = args[1].lstrip("@").strip().lower()
    rec = db.get_user_by_username(target_name)
    if not rec:
        await _w(bot, user.id, f"@{target_name} not found in DB.")
        return
    uid = rec["user_id"]
    row = db.get_notification_subscription(uid)
    sub_row = db.get_subscriber_by_user_id(uid) or db.get_subscriber(target_name)

    if not row and not sub_row:
        await _w(bot, user.id, f"@{target_name} has no notification record.")
        return

    subscribed = bool(row.get("subscribed") if row else (sub_row and sub_row.get("subscribed")))
    source = (row.get("source", "manual") if row else "unknown")
    manually_unsub = bool(sub_row and sub_row.get("manually_unsubscribed")) if sub_row else False
    dm_avail = bool(sub_row and sub_row.get("dm_available")) if sub_row else False
    conv_id = (sub_row.get("conversation_id") or "") if sub_row else ""
    has_dm = bool(conv_id and dm_avail)

    await _w(bot, user.id,
             f"🔔 Notify Audit: @{rec['username']}\n"
             f"Subscribed: {'YES' if subscribed else 'NO'}\n"
             f"Source: {source}\n"
             f"DM connected: {'YES' if has_dm else 'NO'}")
    if row:
        await _w(bot, user.id,
                 f"Events: {_yn(row.get('events', 1))}\n"
                 f"Games: {_yn(row.get('games', 1))}\n"
                 f"Announcements: {_yn(row.get('announcements', 1))}\n"
                 f"Promos: {_yn(row.get('promos', 1))}\n"
                 f"Tips: {_yn(row.get('tips', 0))}\n"
                 f"Manual unsubscribed: {'YES' if manually_unsub else 'NO'}")
    else:
        await _w(bot, user.id,
                 f"(No v2 category record)\n"
                 f"Manual unsubscribed: {'YES' if manually_unsub else 'NO'}")
