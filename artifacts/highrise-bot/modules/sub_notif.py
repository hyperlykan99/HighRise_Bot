"""
modules/sub_notif.py
---------------------
Subscriber notification preference system.

Subscribers control which categories they receive DMs for.
Staff broadcast targeted DMs to opted-in subscribers.

Player commands (anyone):
  /notif                       — show your notification settings
  /notifon <category>          — enable a category
  /notifoff <category>         — disable a category
  /notifall on|off             — toggle all at once

Staff commands (manager+):
  /subnotify <cat> <message>   — DM all opted-in subscribers
  /subnotifyinvite <cat> <msg> — same (invite-framing variant)
  /subnotifystatus             — view recent send stats
"""
from __future__ import annotations

import asyncio
import time as _time

import database as db
from modules.permissions import can_manage_economy

# Category registry: key → display label
NOTIF_CATEGORIES: dict[str, str] = {
    "mining":    "Mining",
    "fishing":   "Fishing",
    "events":    "Events",
    "blackjack": "BlackJack",
    "poker":     "Poker",
    "rewards":   "Rewards",
    "firsthunt": "First Hunt",
    "donations": "Donations",
    "updates":   "Updates",
}

# Categories enabled by default for new subs (others default OFF)
_DEFAULT_ON: frozenset[str] = frozenset({"events", "rewards", "firsthunt", "updates"})

# Per-category send cooldown (seconds) — prevents spam, 5 minutes
_NOTIF_COOLDOWN: dict[str, float] = {}


async def _w(bot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


def _default_enabled(cat: str) -> int:
    return 1 if cat in _DEFAULT_ON else 0


# ---------------------------------------------------------------------------
# /notif — show preferences
# ---------------------------------------------------------------------------

async def handle_notif(bot, user) -> None:
    """/notif — view your notification category settings."""
    prefs = db.get_sub_notif_prefs(user.id)
    lines = ["🔔 Notification Settings"]
    for key, label in NOTIF_CATEGORIES.items():
        on = prefs.get(key, _default_enabled(key))
        lines.append(f"{'✅' if on else '❌'} {label}")
    lines.append("Use /notifon <cat> | /notifoff <cat>")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /notifon <category>
# ---------------------------------------------------------------------------

async def handle_notifon(bot, user, args: list[str]) -> None:
    """/notifon <category> — enable notifications for a category."""
    if len(args) < 2:
        cats = " | ".join(NOTIF_CATEGORIES.keys())
        await _w(bot, user.id, f"Usage: /notifon <category>\nOptions: {cats}"[:249])
        return
    cat = args[1].lower()
    if cat not in NOTIF_CATEGORIES:
        cats = ", ".join(NOTIF_CATEGORIES.keys())
        await _w(bot, user.id, f"Unknown category.\nOptions: {cats}"[:249])
        return
    db.set_sub_notif_pref(user.id, user.username, cat, 1)
    await _w(bot, user.id, f"🔔 {NOTIF_CATEGORIES[cat]} notifications: ON ✅")


# ---------------------------------------------------------------------------
# /notifoff <category>
# ---------------------------------------------------------------------------

async def handle_notifoff(bot, user, args: list[str]) -> None:
    """/notifoff <category> — disable notifications for a category."""
    if len(args) < 2:
        cats = " | ".join(NOTIF_CATEGORIES.keys())
        await _w(bot, user.id, f"Usage: /notifoff <category>\nOptions: {cats}"[:249])
        return
    cat = args[1].lower()
    if cat not in NOTIF_CATEGORIES:
        cats = ", ".join(NOTIF_CATEGORIES.keys())
        await _w(bot, user.id, f"Unknown category.\nOptions: {cats}"[:249])
        return
    db.set_sub_notif_pref(user.id, user.username, cat, 0)
    await _w(bot, user.id, f"🔔 {NOTIF_CATEGORIES[cat]} notifications: OFF ❌")


# ---------------------------------------------------------------------------
# /notifall on|off
# ---------------------------------------------------------------------------

async def handle_notifall(bot, user, args: list[str]) -> None:
    """/notifall on|off — enable or disable all notification categories."""
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        await _w(bot, user.id, "Usage: /notifall on|off")
        return
    enabled = 1 if args[1].lower() == "on" else 0
    db.set_sub_notif_all_prefs(user.id, user.username, enabled)
    state = "ON ✅" if enabled else "OFF ❌"
    await _w(bot, user.id, f"🔔 All notifications turned {state}")


# ---------------------------------------------------------------------------
# /subnotify <category> <message>  (manager+)
# ---------------------------------------------------------------------------

async def handle_subnotify(bot, user, args: list[str]) -> None:
    """/subnotify <category> <message> — DM opted-in subscribers."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id,
                 "Usage: /subnotify <category> <message>")
        return
    cat = args[1].lower()
    if cat not in NOTIF_CATEGORIES:
        cats = ", ".join(NOTIF_CATEGORIES.keys())
        await _w(bot, user.id, f"Unknown category.\nOptions: {cats}"[:249])
        return

    # Anti-spam: 5-minute per-category cooldown
    now = _time.time()
    last = _NOTIF_COOLDOWN.get(cat, 0.0)
    if now - last < 300:
        remain = int(300 - (now - last))
        await _w(bot, user.id,
                 f"Cooldown: wait {remain}s before sending "
                 f"another {NOTIF_CATEGORIES[cat]} notification.")
        return

    msg_text = " ".join(args[2:])[:200]
    await _do_send(bot, user, cat, msg_text)


# ---------------------------------------------------------------------------
# /subnotifyinvite <category> <message>  (manager+) — invite-framing alias
# ---------------------------------------------------------------------------

async def handle_subnotifyinvite(bot, user, args: list[str]) -> None:
    """/subnotifyinvite — same as /subnotify (invite-framing variant)."""
    await handle_subnotify(bot, user, args)


# ---------------------------------------------------------------------------
# /subnotifystatus  (manager+)
# ---------------------------------------------------------------------------

async def handle_subnotifystatus(bot, user) -> None:
    """/subnotifystatus — recent subscriber notification send stats."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/owner only.")
        return
    rows = db.get_sub_notif_logs(limit=5)
    if not rows:
        await _w(bot, user.id, "🔔 No notifications sent yet.")
        return
    lines = ["🔔 Recent Notifications"]
    for r in rows:
        cat  = NOTIF_CATEGORIES.get(r.get("category", ""), r.get("category", "?"))
        s    = r.get("sent_count", 0)
        sk   = r.get("skipped_count", 0)
        nc   = r.get("no_conversation_count", 0)
        lines.append(f"{cat}: sent={s} skip={sk} no-conv={nc}")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# Internal: send notification to all opted-in subscribers
# ---------------------------------------------------------------------------

async def _do_send(bot, user, cat: str, msg_text: str) -> None:
    try:
        subscribers = db.get_all_subscribed_with_dm()
    except Exception as exc:
        await _w(bot, user.id, f"Error loading subscribers: {exc}"[:249])
        return

    sent = skipped = no_conv = failed = 0
    log_id = db.log_sub_notification(
        cat, msg_text, user.id, user.username, 0, 0, 0, 0
    )

    full_msg = f"🔔 [{NOTIF_CATEGORIES[cat]}] {msg_text}"[:249]

    for sub in subscribers:
        uid   = sub.get("user_id", "")
        uname = sub.get("username", "")
        prefs = db.get_sub_notif_prefs(uid)
        on    = prefs.get(cat, _default_enabled(cat))
        if not on:
            skipped += 1
            db.log_sub_notif_recipient(log_id, uid, uname, cat, "skipped_category_off", "")
            continue
        try:
            await bot.highrise.send_whisper(uid, full_msg)
            sent += 1
            db.log_sub_notif_recipient(log_id, uid, uname, cat, "sent", "")
        except Exception as exc:
            err = str(exc)[:100]
            if "no_conversation" in err.lower() or "not found" in err.lower():
                no_conv += 1
                db.log_sub_notif_recipient(log_id, uid, uname, cat, "no_conversation", err)
            else:
                failed += 1
                db.log_sub_notif_recipient(log_id, uid, uname, cat, "failed", err)
        await asyncio.sleep(0.05)

    db.update_sub_notif_log(log_id, sent, skipped, no_conv, failed)
    _NOTIF_COOLDOWN[cat] = _time.time()

    cat_label = NOTIF_CATEGORIES[cat]
    if sent == 0 and skipped > 0:
        await _w(bot, user.id,
                 f"🔔 {cat_label}\n"
                 f"No subscribers have this category enabled.\n"
                 f"Skipped: {skipped}")
    else:
        await _w(bot, user.id,
                 f"🔔 Notification Sent\n"
                 f"Category: {cat_label}\n"
                 f"Sent: {sent} | Skipped: {skipped}\n"
                 f"No conv: {no_conv} | Failed: {failed}")
