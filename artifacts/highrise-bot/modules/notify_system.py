"""
modules/notify_system.py
========================
Canonical notification system — single source of truth for all subscription
logic, DM handling, broadcasts, and admin audit.

Tables owned:
  notification_users      — unified subscription record per user
  notification_action_logs — shared audit trail

DM flow  : user messages bot → process_dm_notify() → subscribe / unsubscribe
Room flow: !sub, !unsub, !notifysettings, !notify [cat] on/off, !notifyhelp
Broadcast: !announcement, !promo, !eventalert, !gamealert, !tipalert
Admin    : !notifyaudit, !notifystatus, !subcount, !subscribers,
           !unsubuser, !notifyreset, !notifyresetall, !confirmnotifyresetall
"""

from __future__ import annotations
import asyncio
from typing import TYPE_CHECKING

import database as db
from modules.permissions import is_owner, is_admin, can_moderate

if TYPE_CHECKING:
    from main import BaseBot
    from highrise import User

# ── Regression lock markers ───────────────────────────────────────────────────
# These constants are checked by !qatest notify (modules/qa_test.py).
# Do not remove or rename them.
#
# LOCKED BEHAVIORS — must not regress:
#   1. main.py on_message uses messages[0] (SDK newest-first). Never messages[-1].
#   2. Hard gate: is_valid_notify_dm_command() fires BEFORE any other DM handler.
#   3. DM parser: exact frozenset match only — content.strip().lower() in VALID_DM_NOTIFY_COMMANDS.
#   4. Random DMs ("Hello", ".", "Ok", "?", "!notifysettings", etc.) → no reply, no DB row.
#   5. DM !sub / subscribe  → _dm_subscribe (sole owner of "Alerts: ON…" reply).
#   6. DM !unsub / unsubscribe → _dm_unsubscribe (sole owner of "Alerts: OFF" reply).
#   7. Room !sub requires existing conversation_id; prompts DM first if absent.
#   8. !notifysettings / !notify cat on/off → room-only; silently ignored in DM.
#   9. Broadcasts: subscribed=1 + conversation_id set + category=1 required.
#  10. First-time DM !sub works without a prior room join row.
#
# Regression suite: !qatest notify  →  Expected: Failed: 0  (19 checks)
_OWNS_NOTIFY_ROUTING = True

_VALID_CATEGORIES = ("events", "games", "announcements", "promos", "tips")
_CATEGORY_LABELS = {
    "events":        "🎉 Events",
    "games":         "🎮 Games",
    "announcements": "📢 Announcements",
    "promos":        "🏷️ Promos",
    "tips":          "💸 Tips",
}

# ── Exact DM command sets (spec-mandated; nothing else triggers action) ───────
_DM_SUB_CMDS   = frozenset({"!sub", "!subscribe", "sub", "subscribe"})
_DM_UNSUB_CMDS = frozenset({"!unsub", "!unsubscribe", "unsub", "unsubscribe"})

# Public gate — used by main.py on_message to hard-reject random DMs
VALID_DM_NOTIFY_COMMANDS: frozenset[str] = _DM_SUB_CMDS | _DM_UNSUB_CMDS


def is_valid_notify_dm_command(content: str) -> bool:
    """
    Hard gate: returns True ONLY for the exact DM keywords that trigger
    subscribe or unsubscribe. Everything else must be silently dropped
    before any other handler runs.
    """
    return content.strip().lower() in VALID_DM_NOTIFY_COMMANDS


# In-memory two-step confirm flag
_reset_pending: bool = False


# ── Low-level send helpers ────────────────────────────────────────────────────

async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


async def _send_conv_dm(bot: "BaseBot", conversation_id: str, msg: str) -> bool:
    try:
        await bot.highrise.send_message(conversation_id, msg[:249], "text")
        return True
    except Exception as exc:
        print(f"[NOTIFY] send_conv_dm failed conv={conversation_id[:12]}: {exc!r}")
        return False


async def send_notify_dm(bot: "BaseBot", user_id: str, message: str) -> str:
    """
    SDK-safe DM delivery.
    Returns 'sent' / 'no_conversation_id' / 'unsubscribed' / 'failed'.
    """
    row = db.get_notify_user(user_id)
    if not row or not row.get("conversation_id"):
        return "no_conversation_id"
    if not row.get("subscribed"):
        return "unsubscribed"
    try:
        await bot.highrise.send_message(row["conversation_id"], message[:249], "text")
        return "sent"
    except Exception as exc:
        print(f"[NOTIFY] send_notify_dm failed uid={user_id[:12]}: {exc!r}")
        return "failed"


# ── Normalisation helpers (also used by QA tests) ────────────────────────────
# Spec: raw.strip().lower() is matched against the exact command sets.
# No prefix stripping — "!sub" and "sub" are BOTH in _DM_SUB_CMDS explicitly.

def _is_sub_command(raw: str) -> bool:
    """True only if raw (stripped, lowercased) is an exact subscribe command."""
    return raw.strip().lower() in _DM_SUB_CMDS


def _is_unsub_command(raw: str) -> bool:
    """True only if raw (stripped, lowercased) is an exact unsubscribe command."""
    return raw.strip().lower() in _DM_UNSUB_CMDS


# ── DM handler — called from subscribers.process_incoming_dm ─────────────────

async def process_dm_notify(
    bot: "BaseBot",
    user_id: str,
    username: str,
    conversation_id: str,
    content: str,
) -> bool:
    """
    Process a DM for notification sub/unsub.
    Returns True if handled (sub/unsub), False if silently ignored.

    Per spec — exact matching only:
      _DM_SUB_CMDS   → subscribe + save conv_id
      _DM_UNSUB_CMDS → unsubscribe
      Everything else → [NOTIFY DM IGNORE] log + silent return

    Do NOT call subscribe, settings, status, help, or any fallback handler.
    Only host/all bot should call this.
    """
    uname = username.lower() if username else f"uid_{user_id[:12]}"
    raw   = content.strip()
    lower = raw.lower()

    print(f"[NOTIFY DM PARSE] user=@{uname} raw={raw[:60]!r}")

    # If user already has a record, refresh their conversation_id (any DM)
    existing = db.get_notify_user(user_id)
    if existing and conversation_id:
        try:
            db.upsert_notify_user(
                user_id, uname,
                conversation_id=conversation_id, dm_available=1,
            )
        except Exception:
            pass

    # ── Exact-set matching — only these literals trigger an action ────────────
    if lower in _DM_SUB_CMDS:
        print(f"[NOTIFY DM PARSE] raw={raw!r} action=subscribe")
        await _dm_subscribe(bot, user_id, uname, conversation_id)
        return True

    if lower in _DM_UNSUB_CMDS:
        print(f"[NOTIFY DM PARSE] raw={raw!r} action=unsubscribe")
        await _dm_unsubscribe(bot, user_id, uname, conversation_id)
        return True

    # Everything else: silent ignore — no reply, no subscribe, no status
    print(f"[NOTIFY DM IGNORE] user=@{uname} raw={raw[:60]!r} action=ignore")
    print(f"[NOTIFY BLOCKED] source=dm_fallback reason=random_dm_no_subscribe")
    return False


async def _dm_subscribe(
    bot: "BaseBot", user_id: str, uname: str, conversation_id: str
) -> None:
    """Subscribe via DM — saves conversation_id, sets subscribed=1."""
    db.upsert_notify_user(
        user_id, uname,
        subscribed=1, source="manual_dm",
        conversation_id=conversation_id, dm_available=1,
        manual_unsubscribed=0,
    )
    try:
        db.insert_notification_action_log("notification_subscribe", user_id, uname)
    except Exception:
        pass
    reply = "Alerts: ON\nTo unsubscribe, reply !unsub or !unsubscribe."
    await _send_conv_dm(bot, conversation_id, reply)
    print(f"[NOTIFY] subscribe user=@{uname} source=manual_dm conv={conversation_id[:12]}")


async def _dm_unsubscribe(
    bot: "BaseBot", user_id: str, uname: str, conversation_id: str
) -> None:
    """Unsubscribe via DM — keeps conversation_id, sets subscribed=0."""
    db.upsert_notify_user(
        user_id, uname,
        subscribed=0, manual_unsubscribed=1,
    )
    try:
        db.insert_notification_action_log("notification_unsubscribe", user_id, uname)
    except Exception:
        pass
    await _send_conv_dm(bot, conversation_id, "Alerts: OFF")
    print(f"[NOTIFY] unsubscribe user=@{uname} source=manual_dm")


# ── Room — !sub / !subscribe ──────────────────────────────────────────────────

async def handle_room_sub(bot: "BaseBot", user: "User") -> None:
    """
    Room !sub.
    Gate: requires existing conversation_id in notification_users.
    If no conversation_id → whisper 'DM me !sub first'.
    If record exists → re-enable subscription.
    """
    row = db.get_notify_user(user.id)
    if not row or not row.get("conversation_id"):
        await _w(
            bot, user.id,
            "📩 DM me !sub or !subscribe first to enable alerts.",
        )
        print(
            f"[NOTIFY BLOCKED] source=room_sub user=@{user.username}"
            " reason=no_conversation_id"
        )
        return

    uname = user.username.lower()
    db.upsert_notify_user(
        user.id, uname,
        subscribed=1, source="manual_room_resubscribe",
        manual_unsubscribed=0,
    )
    try:
        db.insert_notification_action_log("notification_subscribe", user.id, uname)
    except Exception:
        pass
    await _w(
        bot, user.id,
        "Alerts: ON\nTo unsubscribe, DM me !unsub or !unsubscribe.",
    )
    print(f"[NOTIFY] subscribe user=@{user.username} source=manual_room_resubscribe")


# ── Room — !unsub / !unsubscribe ──────────────────────────────────────────────

async def handle_room_unsub(bot: "BaseBot", user: "User") -> None:
    """Room !unsub."""
    row = db.get_notify_user(user.id)
    if not row:
        await _w(bot, user.id, "You are not subscribed.")
        return
    uname = user.username.lower()
    db.upsert_notify_user(user.id, uname, subscribed=0, manual_unsubscribed=1)
    try:
        db.insert_notification_action_log("notification_unsubscribe", user.id, uname)
    except Exception:
        pass
    await _w(bot, user.id, "Alerts: OFF")
    print(f"[NOTIFY] unsubscribe user=@{user.username} source=manual_room")


# ── Room — !notifysettings / !alerts ─────────────────────────────────────────

async def handle_notifysettings(bot: "BaseBot", user: "User") -> None:
    """Room !notifysettings — view-only, never subscribes."""
    row = db.get_notify_user(user.id)
    if not row or not row.get("subscribed"):
        await _w(bot, user.id,
                 "🔔 Notifications\nStatus: OFF\nDM me !sub to subscribe.")
        return

    def _yn(col: str, default: int = 1) -> str:
        return "ON" if row.get(col, default) else "OFF"

    await _w(bot, user.id,
             f"🔔 Notification Settings\nStatus: ON\n"
             f"🎉 Events: {_yn('events')}\n🎮 Games: {_yn('games')}\n"
             f"📢 Announcements: {_yn('announcements')}\n"
             f"🏷️ Promos: {_yn('promos')}\n💸 Tips: {_yn('tips', 0)}")
    await _w(bot, user.id,
             "Edit:\n!notify events on/off\n!notify games on/off\n"
             "!notify promos on/off\n!notify tips on/off")


# ── Room — !notify [category] on/off ─────────────────────────────────────────

async def handle_notify_category(
    bot: "BaseBot", user: "User", args: list[str]
) -> None:
    """Room !notify [category] on/off — never subscribes."""
    if len(args) < 3:
        await _w(bot, user.id,
                 "Usage: !notify [category] on/off\n"
                 "Categories: events games announcements promos tips")
        return

    raw_cat = args[1].strip().lower()
    toggle  = args[2].strip().lower()

    cat = raw_cat if raw_cat in _VALID_CATEGORIES else None
    if cat is None:
        await _w(bot, user.id,
                 "⚠️ Unknown category.\n"
                 "Use: events, games, announcements, promos, tips.")
        return

    if toggle not in ("on", "off"):
        await _w(bot, user.id,
                 f"⚠️ Use ON or OFF.\nExample: !notify {raw_cat} on")
        return

    row = db.get_notify_user(user.id)
    if not row or not row.get("subscribed"):
        await _w(bot, user.id, "⚠️ Alerts are OFF.\nDM me !sub first.")
        return

    enabled = toggle == "on"
    uname = user.username.lower()
    db.set_notify_category(user.id, uname, cat, enabled)
    try:
        db.insert_notification_action_log(
            "notification_settings_change", user.id, uname,
            category=cat, details=toggle,
        )
    except Exception:
        pass
    label = _CATEGORY_LABELS.get(cat, cat.capitalize())
    await _w(bot, user.id,
             f"✅ {label} alerts {toggle.upper()}.\nSettings: !notifysettings")
    print(
        f"[NOTIFY] category_change user=@{user.username}"
        f" category={cat} value={toggle}"
    )


# ── Room — !notifyhelp ────────────────────────────────────────────────────────

async def handle_notifyhelp(
    bot: "BaseBot", user: "User", args: list[str] | None = None
) -> None:
    """Room !notifyhelp."""
    await _w(bot, user.id,
             "🔔 Notification Help\n"
             "Start: DM me !sub\n"
             "Stop: DM me !unsub\n"
             "Settings: !notifysettings\n"
             "Edit: !notify events on/off")
    await _w(bot, user.id,
             "📢 Categories\n"
             "events, games, announcements,\n"
             "promos, tips")


# ── Broadcast helper ──────────────────────────────────────────────────────────

_CAT_TO_CMD = {
    "announcements": "!announcement",
    "promos":        "!promo",
    "events":        "!eventalert",
    "games":         "!gamealert",
    "tips":          "!tipalert",
}


async def _run_broadcast(
    bot: "BaseBot", user: "User", args: list[str], category: str
) -> None:
    """Broadcast a message to all subscribed users with the category enabled."""
    if not is_admin(user.username) and not is_owner(user.username):
        await _w(bot, user.id, "⚠️ Admin only.")
        return
    if len(args) < 2:
        cmd = _CAT_TO_CMD.get(category, "!" + category)
        await _w(bot, user.id, f"Usage: {cmd} [message]")
        return

    msg = " ".join(args[1:]).strip()
    if not msg:
        await _w(bot, user.id, "⚠️ Message cannot be empty.")
        return

    rows = db.get_notify_users_for_broadcast(category)
    delivered = skipped = failed = 0

    for row in rows:
        uid    = row["user_id"]
        uname  = row.get("username", "?")
        conv   = row.get("conversation_id", "")

        if not row.get("subscribed"):
            print(f"[NOTIFY SKIP] user=@{uname} reason=unsubscribed")
            skipped += 1
            continue
        if not row.get(category, 1):
            print(f"[NOTIFY SKIP] user=@{uname} reason=category_off")
            skipped += 1
            continue
        if not conv:
            print(f"[NOTIFY SKIP] user=@{uname} reason=no_conversation_id")
            skipped += 1
            continue

        try:
            await bot.highrise.send_message(conv, msg[:249], "text")
            delivered += 1
        except Exception as exc:
            print(f"[NOTIFY] broadcast DM failed user=@{uname}: {exc!r}")
            failed += 1
        await asyncio.sleep(0.8)

    print(
        f"[NOTIFY BROADCAST] category={category}"
        f" delivered={delivered} skipped={skipped} failed={failed}"
    )
    await _w(bot, user.id,
             f"📤 Broadcast complete\n"
             f"Delivered: {delivered}\n"
             f"Skipped: {skipped}\n"
             f"Failed: {failed}")


async def handle_announcement(bot: "BaseBot", user: "User", args: list[str]) -> None:
    await _run_broadcast(bot, user, args, "announcements")


async def handle_promo(bot: "BaseBot", user: "User", args: list[str]) -> None:
    await _run_broadcast(bot, user, args, "promos")


async def handle_eventalert(bot: "BaseBot", user: "User", args: list[str]) -> None:
    await _run_broadcast(bot, user, args, "events")


async def handle_gamealert(bot: "BaseBot", user: "User", args: list[str]) -> None:
    await _run_broadcast(bot, user, args, "games")


async def handle_tipalert(bot: "BaseBot", user: "User", args: list[str]) -> None:
    await _run_broadcast(bot, user, args, "tips")


# ── Admin — !notifyaudit / !notifystatus ─────────────────────────────────────

async def handle_notifyaudit(
    bot: "BaseBot", user: "User", args: list[str]
) -> None:
    """!notifyaudit @user — full audit of notification record (admin)."""
    if not is_admin(user.username) and not is_owner(user.username):
        await _w(bot, user.id, "⚠️ Admin only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !notifyaudit @user")
        return
    target = args[1].lstrip("@").strip().lower()
    rec = db.get_user_by_username(target)
    if not rec:
        await _w(bot, user.id, f"@{target} not found in DB.")
        return
    row = db.get_notify_user(rec["user_id"])
    if not row:
        await _w(bot, user.id, f"@{target} has no notification record.")
        return

    subscribed   = bool(row.get("subscribed"))
    source       = row.get("source", "unknown")
    has_dm       = bool(row.get("conversation_id") and row.get("dm_available"))
    manual_unsub = bool(row.get("manual_unsubscribed"))

    await _w(bot, user.id,
             f"🔔 Notify Audit: @{rec['username']}\n"
             f"Subscribed: {'YES' if subscribed else 'NO'}\n"
             f"Source: {source}\n"
             f"DM connected: {'YES' if has_dm else 'NO'}")

    def _yn(col: str, default: int = 1) -> str:
        return "ON" if row.get(col, default) else "OFF"

    await _w(bot, user.id,
             f"Events: {_yn('events')}\n"
             f"Games: {_yn('games')}\n"
             f"Announcements: {_yn('announcements')}\n"
             f"Promos: {_yn('promos')}\n"
             f"Tips: {_yn('tips', 0)}\n"
             f"Manual unsubscribed: {'YES' if manual_unsub else 'NO'}")


# ── Admin — !subcount ─────────────────────────────────────────────────────────

async def handle_subcount(bot: "BaseBot", user: "User") -> None:
    """!subcount — aggregate subscription counts (admin)."""
    if not is_admin(user.username) and not is_owner(user.username):
        await _w(bot, user.id, "⚠️ Admin only.")
        return
    counts = db.get_notify_user_counts()
    await _w(bot, user.id,
             f"🔔 Subscribers\n"
             f"Total ON: {counts.get('total', 0)}\n"
             f"Events: {counts.get('events', 0)}\n"
             f"Games: {counts.get('games', 0)}\n"
             f"Announcements: {counts.get('announcements', 0)}\n"
             f"Promos: {counts.get('promos', 0)}\n"
             f"Tips: {counts.get('tips', 0)}")


# ── Staff — !subscribers ──────────────────────────────────────────────────────

async def handle_subscribers(
    bot: "BaseBot", user: "User", args: list[str]
) -> None:
    """!subscribers — staff list of subscriber records."""
    if not can_moderate(user.username):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    rows = db.get_all_notify_users()
    if not rows:
        await _w(bot, user.id, "No subscribers yet.")
        return
    total   = len(rows)
    active  = sum(1 for r in rows if r.get("subscribed") and r.get("conversation_id"))
    pending = sum(1 for r in rows if r.get("subscribed") and not r.get("conversation_id"))
    lines   = [f"📬 {total} record(s) | {active} DM-ready | {pending} pending:"]
    for r in rows[:5]:
        s = "✅" if r.get("subscribed") else "❌"
        d = "📩" if r.get("conversation_id") else "🔕"
        lines.append(f"{s}{d} @{r.get('username', '?')}")
    if total > 5:
        lines.append(f"...and {total - 5} more.")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ── Admin — !unsubuser ────────────────────────────────────────────────────────

async def handle_unsubuser(
    bot: "BaseBot", user: "User", args: list[str]
) -> None:
    """!unsubuser @user — force-unsubscribe a user (admin)."""
    if not is_admin(user.username) and not is_owner(user.username):
        await _w(bot, user.id, "⚠️ Admin only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !unsubuser @user")
        return
    target = args[1].lstrip("@").strip().lower()
    rec = db.get_user_by_username(target)
    if not rec:
        await _w(bot, user.id, f"@{target} not found in DB.")
        return
    uid   = rec["user_id"]
    uname = rec["username"].lower()
    db.upsert_notify_user(uid, uname, subscribed=0, manual_unsubscribed=1)
    try:
        db.insert_notification_action_log(
            "notification_unsubscribe", uid, uname,
            details=f"admin_forced by @{user.username}",
        )
    except Exception:
        pass
    await _w(bot, user.id, f"✅ @{rec['username']} unsubscribed.")
    print(f"[NOTIFY] admin_unsubuser target=@{uname} by=@{user.username}")


# ── Admin — !notifyreset @user ────────────────────────────────────────────────

async def handle_notifyreset(
    bot: "BaseBot", user: "User", args: list[str]
) -> None:
    """!notifyreset @user — delete one user's notification record (admin)."""
    if not is_admin(user.username) and not is_owner(user.username):
        await _w(bot, user.id, "⚠️ Admin only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !notifyreset @user")
        return
    target = args[1].lstrip("@").strip().lower()
    rec = db.get_user_by_username(target)
    if not rec:
        await _w(bot, user.id, f"@{target} not found in DB.")
        return
    db.delete_notify_user(rec["user_id"])
    try:
        db.insert_notification_action_log(
            "notification_reset", rec["user_id"], rec["username"].lower(),
            details=f"single_reset by @{user.username}",
        )
    except Exception:
        pass
    await _w(bot, user.id,
             f"✅ Notification record cleared for @{rec['username']}.")
    print(f"[NOTIFY RESET] single user=@{target} by=@{user.username}")


# ── Owner — !notifyresetall ───────────────────────────────────────────────────

async def handle_notifyresetall(bot: "BaseBot", user: "User") -> None:
    """!notifyresetall — owner only: initiate two-step reset."""
    global _reset_pending
    if not is_owner(user.username):
        await _w(bot, user.id, "⚠️ Owner only.")
        return
    _reset_pending = True
    await _w(bot, user.id,
             "⚠️ This clears notification data only.\n"
             "Type !confirmnotifyresetall to confirm.")


async def handle_confirmnotifyresetall(bot: "BaseBot", user: "User") -> None:
    """!confirmnotifyresetall — owner only: execute the reset."""
    global _reset_pending
    if not is_owner(user.username):
        await _w(bot, user.id, "⚠️ Owner only.")
        return
    if not _reset_pending:
        await _w(bot, user.id,
                 "⚠️ Run !notifyresetall first to initiate reset.")
        return
    _reset_pending = False
    print("[NOTIFY RESET] starting notification-only reset")
    _tables = [
        "notification_users",
        "subscriber_users",
        "notification_subscriptions",
        "notification_action_logs",
        "notification_logs",
        "pending_notifications",
    ]
    for tbl in _tables:
        ok = db.clear_notify_table(tbl)
        if ok:
            print(f"[NOTIFY RESET] cleared table={tbl}")
    print("[NOTIFY RESET] complete")
    await _w(bot, user.id, "♻️ All notification subscription data cleared.")
