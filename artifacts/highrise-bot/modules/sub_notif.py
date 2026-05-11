"""
modules/sub_notif.py
---------------------
Subscriber notification preference system.

EmceeBot (host mode) handles ALL notification delivery.
Category-to-bot routing is disabled; deliver_here is always True.

Player commands (everyone):
  /notif                       — show your notification settings
  /notifon <category>          — enable a category
  /notifoff <category>         — disable a category
  /notifall on|off             — toggle global on/off
  /notifdm                     — explain DM availability
  /opennotifs                  — alias for /notifdm

Manager+ commands:
  /subnotify <cat> <msg>       — send to all eligible in-room subscribers
  /subnotifyinvite <cat> <msg> — same with invite framing
  /subnotifystatus [sub]       — view send stats / sub-views
  /testnotify @user <cat> <msg>— test delivery to one player
  /setsubnotifycooldown <mins> — set per-category cooldown (default 5m)
"""
from __future__ import annotations

import asyncio
import json
import time as _time
import traceback

import config
import database as db
from modules.permissions import can_manage_economy, is_owner

# ---------------------------------------------------------------------------
# Category registry
# ---------------------------------------------------------------------------

NOTIF_CATEGORIES: dict[str, str] = {
    "mining":    "Mining",
    "fishing":   "Fishing",
    "events":    "Events",
    "blackjack": "BlackJack",
    "poker":     "Poker",
    "rewards":   "Rewards",
    "firsthunt": "First Hunt",
    "donations": "Donations",
    "security":  "Security",
    "updates":   "Updates",
}

# Category → bot_mode that should send this category's notifications.
# "host"      = EmceeBot (Host Bot)
# "miner"     = GreatestProspector
# "fisher"    = MasterAngler
# "banker"    = BankingBot / ChipSoprano (banker/shopkeeper merged)
# "blackjack" = BlackJack Bot (AceSinatra etc.)
# "poker"     = Poker Bot
# "security"  = SecurityBot
# "eventhost" = EventBot / EmceeBot (often merged into host)
CATEGORY_BOT_MODE: dict[str, str] = {
    "mining":    "miner",
    "fishing":   "fisher",
    "events":    "eventhost",
    "firsthunt": "eventhost",
    "rewards":   "banker",
    "donations": "banker",
    "blackjack": "blackjack",
    "poker":     "poker",
    "security":  "security",
    "updates":   "host",
}

# Category → (emoji, human label for message header)
CATEGORY_HEADERS: dict[str, tuple[str, str]] = {
    "mining":    ("⛏️", "Mining Alert"),
    "fishing":   ("🎣", "Fishing Alert"),
    "events":    ("🎉", "Event Alert"),
    "firsthunt": ("🏁", "First Hunt Alert"),
    "rewards":   ("💰", "Reward Alert"),
    "donations": ("💛", "Donation Alert"),
    "blackjack": ("🃏", "BlackJack Alert"),
    "poker":     ("♠️", "Poker Alert"),
    "security":  ("🛡️", "Security Alert"),
    "updates":   ("📢", "Update Alert"),
}

# Fallback bot mode if selected sender is unavailable
_FALLBACK_MODE = "host"

# Human-readable display names for bot modes — used when DB lookup returns None.
# Always shows the correct bot name instead of internal mode key.
_MODE_DISPLAY_NAMES: dict[str, str] = {
    "host":       "EmceeBot",
    "eventhost":  "EmceeBot",
    "banker":     "BankingBot",
    "miner":      "GreatestProspector",
    "fisher":     "MasterAngler",
    "blackjack":  "ChipSoprano",
    "poker":      "AceSinatra",
    "dj":         "DJ_DUDU",
    "security":   "KeanuShield",
    "shopkeeper": "BankingBot",
}

# Categories ON by default for new subscribers (all others default OFF)
_DEFAULT_ON: frozenset[str] = frozenset({"events", "rewards", "firsthunt", "updates"})

# Per-category cooldown tracking {category: unix_timestamp_of_last_send}
_NOTIF_COOLDOWN: dict[str, float] = {}

# Cooldown in seconds (adjustable via /setsubnotifycooldown)
_COOLDOWN_SECS: int = 300  # 5 minutes

# Channel message type for cross-bot notification dispatch
_CHANNEL_TYPE = "notif_dispatch"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _w(bot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


def _default_enabled(cat: str) -> int:
    return 1 if cat in _DEFAULT_ON else 0


def _is_in_room(user_id: str) -> bool:
    """Return True if user_id is currently in the room."""
    try:
        from modules.room_utils import _user_positions
        return user_id in _user_positions
    except Exception:
        return False


def _fmt_remain(secs: int) -> str:
    if secs >= 60:
        m, s = divmod(secs, 60)
        return f"{m}m {s}s"
    return f"{secs}s"


def _valid_category_msg() -> str:
    cats = ", ".join(NOTIF_CATEGORIES.keys())
    return f"🔔 Invalid Category\nValid:\n{cats}"


def _current_bot_modes() -> frozenset[str]:
    """Return the set of bot modes this process runs (primary + extra)."""
    modes = {config.BOT_MODE}
    modes.update(config.BOT_EXTRA_MODES)
    return frozenset(modes)


def get_notification_sender_info(category: str) -> dict:
    """
    Always returns EmceeBot (host mode) as the sender.
    Job-based category-to-bot routing is disabled; EmceeBot delivers all.
    """
    emceebot_name = db.get_bot_username_for_mode("host") or "EmceeBot"
    return {
        "target_mode":       "host",
        "sender_bot_name":   emceebot_name,
        "original_bot_name": emceebot_name,
        "fallback_used":     False,
        "deliver_here":      True,
    }


def _build_full_msg(cat: str, msg_text: str, send_type: str = "normal") -> str:
    """Build the final whisper message with category-specific header."""
    emoji, label = CATEGORY_HEADERS.get(cat, ("📣", NOTIF_CATEGORIES.get(cat, cat)))
    header = f"{emoji} {label}"
    if send_type == "invite":
        body = f"{msg_text}\nJoin the room to participate!"
    else:
        body = msg_text
    return f"{header}\n{body}"[:249]


# ---------------------------------------------------------------------------
# /notif — view preferences
# ---------------------------------------------------------------------------

async def handle_notif(bot, user) -> None:
    """/notif — view your notification category settings."""
    prefs      = db.get_sub_notif_prefs(user.id)
    global_row = db.get_sub_notif_global(user.id)
    global_on  = global_row.get("global_enabled", 1)
    in_room    = _is_in_room(user.id)

    lines = [
        "🔔 Notification Settings",
        f"Global: {'ON' if global_on else 'OFF'}",
    ]
    for key, label in NOTIF_CATEGORIES.items():
        on = prefs.get(key, _default_enabled(key))
        lines.append(f"{label}: {'ON' if on else 'OFF'}")

    lines.append("DM Status: SDK Unsupported")
    lines.append("In-Room Whisper: Available while you are in room")
    lines.append("/notifon <cat> | /notifoff <cat> | /notifall on|off")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /notifon <category>
# ---------------------------------------------------------------------------

async def handle_notifon(bot, user, args: list[str]) -> None:
    if len(args) < 2:
        cats = " | ".join(NOTIF_CATEGORIES.keys())
        await _w(bot, user.id, f"Usage: /notifon <category>\nOptions: {cats}"[:249])
        return
    cat = args[1].lower()
    if cat not in NOTIF_CATEGORIES:
        await _w(bot, user.id, _valid_category_msg()[:249])
        return
    db.set_sub_notif_pref(user.id, user.username, cat, 1)
    await _w(bot, user.id, f"🔔 {NOTIF_CATEGORIES[cat]} notifications: ON ✅")


# ---------------------------------------------------------------------------
# /notifoff <category>
# ---------------------------------------------------------------------------

async def handle_notifoff(bot, user, args: list[str]) -> None:
    if len(args) < 2:
        cats = " | ".join(NOTIF_CATEGORIES.keys())
        await _w(bot, user.id, f"Usage: /notifoff <category>\nOptions: {cats}"[:249])
        return
    cat = args[1].lower()
    if cat not in NOTIF_CATEGORIES:
        await _w(bot, user.id, _valid_category_msg()[:249])
        return
    db.set_sub_notif_pref(user.id, user.username, cat, 0)
    await _w(bot, user.id, f"🔔 {NOTIF_CATEGORIES[cat]} notifications: OFF ❌")


# ---------------------------------------------------------------------------
# /notifall on|off
# ---------------------------------------------------------------------------

async def handle_notifall(bot, user, args: list[str]) -> None:
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        await _w(bot, user.id, "Usage: /notifall on|off")
        return
    enabled = 1 if args[1].lower() == "on" else 0
    db.set_sub_notif_global(user.id, user.username, enabled)
    if enabled:
        existing = db.get_sub_notif_prefs(user.id)
        if not existing:
            db.set_sub_notif_all_prefs(user.id, user.username, -1)
    state = "ON ✅" if enabled else "OFF ❌"
    await _w(bot, user.id, f"🔔 Global notifications turned {state}")


# ---------------------------------------------------------------------------
# /notifdm  /opennotifs
# ---------------------------------------------------------------------------

async def handle_notifdm(bot, user) -> None:
    await _w(
        bot, user.id,
        "🔔 Notification DM Setup\n"
        "DM notifications are not supported by this SDK right now.\n"
        "I can only whisper players who are currently in the room.\n"
        "Stay in the room to receive notifications."
    )


async def handle_opennotifs(bot, user) -> None:
    await handle_notifdm(bot, user)


# ---------------------------------------------------------------------------
# /setsubnotifycooldown <minutes>  (manager+)
# ---------------------------------------------------------------------------

async def handle_setsubnotifycooldown(bot, user, args: list[str]) -> None:
    global _COOLDOWN_SECS
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, f"Usage: /setsubnotifycooldown <minutes>  (current: {_COOLDOWN_SECS // 60}m)")
        return
    mins = max(0, min(60, int(args[1])))
    _COOLDOWN_SECS = mins * 60
    await _w(bot, user.id, f"✅ Notification cooldown set to {mins}m.")


# ---------------------------------------------------------------------------
# /subnotify <category> <message>  (manager+)
# ---------------------------------------------------------------------------

async def handle_subnotify(bot, user, args: list[str], *, invite: bool = False) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /subnotify <category> <message>")
        return
    cat = args[1].lower()
    if cat not in NOTIF_CATEGORIES:
        await _w(bot, user.id, _valid_category_msg()[:249])
        return

    now  = _time.time()
    last = _NOTIF_COOLDOWN.get(cat, 0.0)
    if _COOLDOWN_SECS > 0 and (now - last) < _COOLDOWN_SECS and not is_owner(user.username):
        remain = int(_COOLDOWN_SECS - (now - last))
        await _w(
            bot, user.id,
            f"🔔 Notification Cooldown\n"
            f"{NOTIF_CATEGORIES[cat]} can be sent again in {_fmt_remain(remain)}."
        )
        return

    msg_text  = " ".join(args[2:])[:180]
    send_type = "invite" if invite else "normal"
    await _do_send(bot, user, cat, msg_text, send_type=send_type)


# ---------------------------------------------------------------------------
# /subnotifyinvite <category> <message>  (manager+)
# ---------------------------------------------------------------------------

async def handle_subnotifyinvite(bot, user, args: list[str]) -> None:
    await handle_subnotify(bot, user, args, invite=True)


# ---------------------------------------------------------------------------
# /subnotifystatus [sub]  (manager+)
# ---------------------------------------------------------------------------

async def handle_subnotifystatus(bot, user, args: list[str] | None = None) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    sub = (args[1].lower() if args and len(args) > 1 else "latest")

    if sub in ("room", "whisper"):
        rows = db.get_sub_notif_logs_by_method(limit=5, method="whisper")
    elif sub == "dm":
        rows = db.get_sub_notif_logs_by_method(limit=5, method="dm")
    elif sub in ("noconversation", "nodelivery", "nodm"):
        rows = db.get_sub_notif_no_conv_recipients(limit=10)
        if not rows:
            await _w(bot, user.id, "🔔 No players with missing delivery route.")
            return
        lines = ["🔔 No Delivery Route"]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. @{r.get('username','?')} — room NO — DM NO")
        await _w(bot, user.id, "\n".join(lines)[:249])
        return
    elif sub == "failed":
        rows = db.get_sub_notif_failed_recipients(limit=8)
        if not rows:
            await _w(bot, user.id, "🔔 No failed deliveries found.")
            return
        lines = ["🔔 Failed Notifications"]
        for i, r in enumerate(rows, 1):
            cat_label = NOTIF_CATEGORIES.get(r.get("category",""), r.get("category","?"))
            err       = (r.get("error","") or "unknown")[:30]
            lines.append(f"{i}. @{r.get('username','?')} — {cat_label} — {err}")
        await _w(bot, user.id, "\n".join(lines)[:249])
        return
    else:
        rows = db.get_sub_notif_logs(limit=5)

    if not rows:
        await _w(bot, user.id, "🔔 No notifications sent yet.")
        return

    latest  = rows[0]
    cat     = NOTIF_CATEGORIES.get(latest.get("category", ""), latest.get("category", "?"))
    dm_sent = (latest.get("sent_bulk_dm_count", 0) or 0) + (latest.get("sent_conv_dm_count", 0) or 0)

    lines = [
        "🔔 Notification Status",
        f"Last: {cat}",
        "Sender: EmceeBot",
        f"Whisper Sent In Room: {latest.get('sent_whisper_count', 0)}",
        f"DM Sent Out of Room: {dm_sent}",
        f"No Delivery Route: {latest.get('no_delivery_route_count', latest.get('no_conversation_count', 0))}",
        f"Skipped Opt-Out: {latest.get('skipped_count', 0)}",
        f"Failed: {latest.get('failed_count', 0)}",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /testnotify @username <category> <message>  (manager+)
# ---------------------------------------------------------------------------

async def handle_testnotify(bot, user, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 4:
        await _w(bot, user.id, "Usage: /testnotify @username <category> <message>")
        return

    raw_target = args[1].lstrip("@").lower()
    cat        = args[2].lower()
    msg_text   = " ".join(args[3:])[:120]

    if cat not in NOTIF_CATEGORIES:
        await _w(bot, user.id, _valid_category_msg()[:249])
        return

    # Resolve sender info
    sender_info      = get_notification_sender_info(cat)
    selected_sender  = sender_info["original_bot_name"]
    actual_sender    = sender_info["sender_bot_name"]
    fallback_used    = sender_info["fallback_used"]

    # Resolve target subscriber
    sub_row    = db.get_subscriber(raw_target)
    subscribed = bool(sub_row and sub_row.get("subscribed") == 1)
    uid        = sub_row.get("user_id", "") if sub_row else ""

    global_row = db.get_sub_notif_global(uid) if uid else {}
    global_on  = bool(global_row.get("global_enabled", 1))

    prefs      = db.get_sub_notif_prefs(uid) if uid else {}
    cat_on     = bool(prefs.get(cat, _default_enabled(cat)))

    conv_id    = sub_row.get("conversation_id", "") if sub_row else ""
    in_room    = False
    if uid:
        # Use live SDK room check for test accuracy
        live_ids = await _get_live_room_user_ids(bot)
        in_room  = uid in live_ids

    bulk_supported = hasattr(bot.highrise, "send_message_bulk")
    conv_supported = hasattr(bot.highrise, "send_message")

    delivered    = "NO"
    delivery_method = "none"
    status       = ""
    reason       = ""

    if not subscribed:
        reason = "not_subscribed"
    elif not global_on:
        reason = "global_off"
    elif not cat_on:
        reason = "category_off"
    elif not uid:
        reason = "user_id_unknown"
    elif in_room:
        full_msg = _build_full_msg(cat, f"[TEST] {msg_text}")
        try:
            await bot.highrise.send_whisper(uid, full_msg)
            delivered       = "YES"
            delivery_method = "whisper"
            status          = "sent_whisper_in_room"
        except Exception as exc:
            reason = f"whisper_error: {str(exc)[:40]}"
    elif bulk_supported:
        full_msg = _build_full_msg(cat, f"[TEST] {msg_text}")
        try:
            result = await bot.highrise.send_message_bulk([uid], full_msg)
            is_error = result is not None and hasattr(result, "message")
            if not is_error:
                delivered       = "YES"
                delivery_method = "bulk_dm"
                status          = "sent_bulk_dm"
            else:
                reason = f"bulk_dm_error: {result}"
        except Exception as exc:
            reason = f"bulk_dm_exception: {str(exc)[:40]}"
        if delivered == "NO" and conv_supported and conv_id:
            full_msg = _build_full_msg(cat, f"[TEST] {msg_text}")
            try:
                result = await bot.highrise.send_message(conv_id, full_msg)
                is_error = result is not None and hasattr(result, "message")
                if not is_error:
                    delivered       = "YES"
                    delivery_method = "conversation_dm"
                    status          = "sent_conversation_dm"
                    reason          = ""
                else:
                    reason = "conversation_dm_error"
            except Exception as exc:
                reason = f"conv_dm_exception: {str(exc)[:40]}"
        if delivered == "NO" and not reason:
            reason = "no_delivery_route"
    elif conv_supported and conv_id:
        full_msg = _build_full_msg(cat, f"[TEST] {msg_text}")
        try:
            result = await bot.highrise.send_message(conv_id, full_msg)
            is_error = result is not None and hasattr(result, "message")
            if not is_error:
                delivered       = "YES"
                delivery_method = "conversation_dm"
                status          = "sent_conversation_dm"
            else:
                reason = "conversation_dm_error"
        except Exception as exc:
            reason = f"conv_dm_exception: {str(exc)[:40]}"
    else:
        reason = "no_conversation" if not conv_id else "no_delivery_route"

    lines = [
        "🔔 Test Notify",
        f"Target: @{raw_target}",
        f"Category: {NOTIF_CATEGORIES.get(cat, cat)}",
        f"Sender: {actual_sender}",
        f"Subscribed: {'YES' if subscribed else 'NO'}",
        f"Global: {'ON' if global_on else 'OFF'}",
        f"Category {NOTIF_CATEGORIES.get(cat,cat)}: {'ON' if cat_on else 'OFF'}",
        f"Currently In Room: {'YES' if in_room else 'NO'}",
        f"DM Supported: {'YES' if bulk_supported or conv_supported else 'NO'}",
        f"Conversation ID: {'YES' if conv_id else 'NO'}",
        f"Delivery Used: {delivery_method}",
        f"Delivered: {delivered}",
        f"Status: {status}" if status else f"Reason: {reason}",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# Public helper for programmatic use from other modules
# ---------------------------------------------------------------------------

async def send_subscriber_notification(
    bot,
    category: str,
    message: str,
    send_type: str = "normal",
    sender=None,
) -> None:
    """
    Public async helper for other systems to send subscriber notifications.
    Routes to the correct bot automatically.
    """
    if category not in NOTIF_CATEGORIES:
        print(f"[SUB_NOTIF] Unknown category '{category}'; skipping.")
        return

    class _FakeSender:
        id       = "system"
        username = "system"

    sender_obj = sender if sender is not None else _FakeSender()
    await _do_send(bot, sender_obj, category, message[:180], send_type=send_type)


# ---------------------------------------------------------------------------
# Core: _do_send — route to correct sender bot
# ---------------------------------------------------------------------------

async def _do_send(bot, sender, cat: str, msg_text: str, *, send_type: str = "normal") -> None:
    """
    Deliver a subscriber notification.

    EmceeBot (host) always handles delivery — deliver_here is always True.
    Channel dispatch path is retained for safety but will not be triggered.
    """
    sender_info      = get_notification_sender_info(cat)
    target_mode      = sender_info["target_mode"]
    sender_bot_name  = sender_info["sender_bot_name"]
    original_bot     = sender_info["original_bot_name"]
    fallback_used    = sender_info["fallback_used"]
    deliver_here     = sender_info["deliver_here"]

    full_msg = _build_full_msg(cat, msg_text, send_type)

    # Create log entry
    log_id = db.log_sub_notification_v2(
        cat, msg_text, send_type,
        sender.id, sender.username,
        sender_bot_name=sender_bot_name,
        original_sender_bot_name=original_bot,
        fallback_used=1 if fallback_used else 0,
    )

    if deliver_here:
        # Deliver from this bot's connection
        sent_w, sent_b, sent_c, no_del, skipped, failed = \
            await _do_deliver_notif(bot, cat, full_msg, log_id)

        db.update_sub_notif_log_v2(
            log_id, sent_w, sent_b, sent_c, no_del, skipped, failed
        )
        _NOTIF_COOLDOWN[cat] = _time.time()

        fb_note = f"\n⚠️ Fallback: {original_bot} offline" if fallback_used else ""
        _report_delivery(bot, sender, cat, sent_w, sent_b, sent_c,
                         no_del, skipped, failed, sender_bot_name, fb_note)
    else:
        # Dispatch to the target bot via Highrise channel
        payload = json.dumps({
            "type":                   _CHANNEL_TYPE,
            "target_mode":            target_mode,
            "category":               cat,
            "full_msg":               full_msg,
            "log_id":                 log_id,
            "send_type":              send_type,
            "sender_bot_name":        sender_bot_name,
            "original_sender_bot_name": original_bot,
            "fallback_used":          0,
        })
        try:
            await bot.highrise.send_channel(payload)
            _NOTIF_COOLDOWN[cat] = _time.time()
            await _w(
                bot, sender.id,
                f"🔔 Notification Dispatched\n"
                f"Category: {NOTIF_CATEGORIES[cat]}\n"
                f"Sender: {sender_bot_name}\n"
                f"Status: dispatched_to_sender_bot"
            )
        except Exception as exc:
            # Channel send failed — deliver here as emergency fallback
            print(f"[SUB_NOTIF] Channel dispatch failed: {exc}; delivering locally.")
            sent_w, sent_b, sent_c, no_del, skipped, failed = \
                await _do_deliver_notif(bot, cat, full_msg, log_id)
            db.update_sub_notif_log_v2(
                log_id, sent_w, sent_b, sent_c, no_del, skipped, failed
            )
            _NOTIF_COOLDOWN[cat] = _time.time()
            _report_delivery(bot, sender, cat, sent_w, sent_b, sent_c,
                             no_del, skipped, failed,
                             sender_bot_name, "\n⚠️ Channel failed — local fallback")


async def _load_subs() -> list[dict]:
    try:
        return db.get_all_subscribed_users_for_notify()
    except Exception:
        return []


async def _get_live_room_user_ids(bot) -> set[str]:
    """Return the set of user IDs currently in the room.
    Uses SDK get_room_users() as source of truth, falls back to _user_positions."""
    try:
        resp = await bot.highrise.get_room_users()
        if hasattr(resp, "content"):
            return {item[0].id for item in resp.content}
    except Exception as exc:
        print(f"[SUB_NOTIF] get_room_users failed ({exc}); using _user_positions fallback")
    try:
        from modules.room_utils import _user_positions
        return set(_user_positions.keys())
    except Exception:
        return set()


def _report_delivery(bot, sender, cat, sent_whisper, sent_bulk_dm,
                     sent_conv_dm, no_delivery, skipped, failed,
                     sender_bot_name, extra_note="") -> None:
    """Fire-and-forget summary whisper to the staff member who triggered the send."""
    cat_label  = NOTIF_CATEGORIES[cat]
    total_sent = sent_whisper + sent_bulk_dm + sent_conv_dm
    dm_sent    = sent_bulk_dm + sent_conv_dm

    if total_sent == 0 and skipped == 0 and failed == 0 and no_delivery == 0:
        msg = (
            f"🔔 Subscriber Notification\n"
            f"No messages delivered.\n"
            f"No eligible subscribed players in room and out-of-room DM unavailable."
        )
    elif total_sent == 0:
        msg = (
            f"🔔 Subscriber Notification\n"
            f"Not delivered.\n"
            f"No Delivery Route: {no_delivery} | Skipped Opt-Out: {skipped} | Failed: {failed}"
        )
    else:
        msg = (
            f"🔔 Notification Complete\n"
            f"Category: {cat_label}\n"
            f"Sender: {sender_bot_name}\n"
            f"Whisper Sent In Room: {sent_whisper}\n"
            f"DM Sent Out of Room: {dm_sent}\n"
            f"No Delivery Route: {no_delivery}\n"
            f"Skipped Opt-Out: {skipped}\n"
            f"Failed: {failed}"
        )
    if extra_note:
        msg = (msg + extra_note)[:249]
    asyncio.ensure_future(_w(bot, sender.id, msg[:249]))


# ---------------------------------------------------------------------------
# Delivery engine
# ---------------------------------------------------------------------------

async def _do_deliver_notif(
    bot,
    cat: str,
    full_msg: str,
    log_id: int,
) -> tuple[int, int, int, int, int, int]:
    """
    Deliver a subscriber notification to all eligible subscribers.

    Priority per player:
      1. In room        → send_whisper (status=sent_whisper_in_room)
      2. Out of room    → send_message_bulk if SDK supports it (status=sent_bulk_dm)
      3. Out of room    → send_message via conversation_id (status=sent_conversation_dm)
      4. No route       → status=no_delivery_route

    Returns (sent_whisper, sent_bulk_dm, sent_conv_dm, no_delivery, skipped, failed).
    """
    try:
        subscribers = db.get_all_subscribed_users_for_notify()
    except Exception as exc:
        print(f"[SUB_NOTIF] Error loading subscribers: {exc}")
        return 0, 0, 0, 0, 0, 0

    # Refresh live room membership once before the loop
    room_ids = await _get_live_room_user_ids(bot)

    bulk_supported = hasattr(bot.highrise, "send_message_bulk")
    conv_supported = hasattr(bot.highrise, "send_message")

    sent_whisper = sent_bulk_dm = sent_conv_dm = no_delivery = skipped = failed = 0

    # Separate eligible out-of-room users for one bulk DM call
    out_of_room_eligible: list[dict] = []

    for sub in subscribers:
        uid   = sub.get("user_id", "")
        uname = sub.get("username", "") or "?"
        if not uid:
            continue

        # ── Global preference ────────────────────────────────────────────────
        global_row = db.get_sub_notif_global(uid)
        if not global_row.get("global_enabled", 1):
            skipped += 1
            db.log_sub_notif_recipient_v2(
                log_id, uid, uname, cat, 1, 1, 0, "none", "skipped_global_off", ""
            )
            continue

        # ── Category preference ──────────────────────────────────────────────
        prefs = db.get_sub_notif_prefs(uid)
        if not prefs.get(cat, _default_enabled(cat)):
            skipped += 1
            db.log_sub_notif_recipient_v2(
                log_id, uid, uname, cat, 1, 0, 1, "none", "skipped_category_off", ""
            )
            continue

        # ── 1. In-room whisper ───────────────────────────────────────────────
        if uid in room_ids:
            try:
                await bot.highrise.send_whisper(uid, full_msg)
                sent_whisper += 1
                db.log_sub_notif_recipient_v2(
                    log_id, uid, uname, cat, 1, 1, 1,
                    "whisper", "sent_whisper_in_room", ""
                )
            except Exception as exc:
                err = str(exc)[:120]
                failed += 1
                db.log_sub_notif_recipient_v2(
                    log_id, uid, uname, cat, 1, 1, 1,
                    "whisper", "failed", err
                )
            await asyncio.sleep(0.05)
            continue

        # ── Out-of-room: queue for bulk DM attempt ───────────────────────────
        out_of_room_eligible.append(sub)

    # ── 2. Bulk DM for out-of-room subscribers ────────────────────────────────
    remaining_for_conv: list[dict] = []

    if bulk_supported and out_of_room_eligible:
        uids_to_bulk = [s["user_id"] for s in out_of_room_eligible]
        try:
            result = await bot.highrise.send_message_bulk(uids_to_bulk, full_msg)
            # None = success; Error object = failure
            is_error = result is not None and hasattr(result, "message")
            if not is_error:
                for s in out_of_room_eligible:
                    uid, uname = s["user_id"], s.get("username", "?")
                    sent_bulk_dm += 1
                    db.log_sub_notif_recipient_v2(
                        log_id, uid, uname, cat, 1, 1, 1,
                        "bulk_dm", "sent_bulk_dm", ""
                    )
                # All handled — nothing left for conversation DM
            else:
                print(f"[SUB_NOTIF] send_message_bulk error: {result}")
                remaining_for_conv = out_of_room_eligible
        except Exception as exc:
            print(f"[SUB_NOTIF] send_message_bulk exception: {exc}")
            remaining_for_conv = out_of_room_eligible
    else:
        remaining_for_conv = out_of_room_eligible

    # ── 3. Conversation DM fallback ───────────────────────────────────────────
    for s in remaining_for_conv:
        uid     = s.get("user_id", "")
        uname   = s.get("username", "?")
        conv_id = s.get("conversation_id") or ""

        if conv_supported and conv_id:
            try:
                result = await bot.highrise.send_message(conv_id, full_msg)
                is_error = result is not None and hasattr(result, "message")
                if not is_error:
                    sent_conv_dm += 1
                    db.log_sub_notif_recipient_v2(
                        log_id, uid, uname, cat, 1, 1, 1,
                        "conversation_dm", "sent_conversation_dm", ""
                    )
                else:
                    no_delivery += 1
                    db.log_sub_notif_recipient_v2(
                        log_id, uid, uname, cat, 1, 1, 1,
                        "none", "no_conversation", str(result)[:80]
                    )
            except Exception as exc:
                failed += 1
                db.log_sub_notif_recipient_v2(
                    log_id, uid, uname, cat, 1, 1, 1,
                    "conversation_dm", "failed", str(exc)[:80]
                )
        else:
            no_delivery += 1
            reason = "no_conversation" if not conv_id else "unsupported_conversation_dm"
            db.log_sub_notif_recipient_v2(
                log_id, uid, uname, cat, 1, 1, 1,
                "none", reason, ""
            )

        await asyncio.sleep(0.05)

    return sent_whisper, sent_bulk_dm, sent_conv_dm, no_delivery, skipped, failed


# ---------------------------------------------------------------------------
# Cross-bot channel handler
# ---------------------------------------------------------------------------

async def handle_notif_dispatch_channel(bot, payload: dict) -> None:
    """
    Called from on_channel when a notif_dispatch message arrives.
    Only the bot whose mode matches target_mode delivers.
    """
    target_mode    = payload.get("target_mode", "")
    current_modes  = _current_bot_modes()

    if target_mode not in current_modes:
        return  # not our job

    cat            = payload.get("category", "")
    full_msg       = payload.get("full_msg", "")
    log_id         = int(payload.get("log_id", 0))
    sender_bot     = payload.get("sender_bot_name", "")

    if not cat or not full_msg or not log_id:
        print(f"[SUB_NOTIF] notif_dispatch: missing fields {payload}")
        return

    print(f"[SUB_NOTIF] notif_dispatch received: cat={cat} mode={target_mode} log_id={log_id}")

    sent_whisper, sent_bulk_dm, sent_conv_dm, no_delivery, skipped, failed = \
        await _do_deliver_notif(bot, cat, full_msg, log_id)

    db.update_sub_notif_log_v2(
        log_id, sent_whisper, sent_bulk_dm, sent_conv_dm, no_delivery, skipped, failed
    )
    _NOTIF_COOLDOWN[cat] = _time.time()

    print(
        f"[SUB_NOTIF] dispatch done: cat={cat} whisper={sent_whisper} "
        f"bulk_dm={sent_bulk_dm} conv_dm={sent_conv_dm} "
        f"no_route={no_delivery} skipped={skipped} failed={failed}"
    )
