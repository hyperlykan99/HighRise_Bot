"""
modules/sub_notif.py
---------------------
Subscriber notification preference system — clean rewrite.

Subscribers control which categories they receive notifications for.
Staff broadcast targeted whispers to opted-in, in-room subscribers.

Delivery reality (Highrise SDK):
  - send_whisper(user_id, msg) works ONLY when user is currently in the room.
  - No DM/inbox API is available in this SDK version.
  - Players NOT in the room at send time receive status=no_conversation.

Player commands (everyone):
  /notif                       — show your notification settings
  /notifon <category>          — enable a category
  /notifoff <category>         — disable a category
  /notifall on|off             — toggle global on/off (preserves per-cat prefs)
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
import time as _time
import traceback

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
    "updates":   "Updates",
}

# Categories ON by default for new subscribers (all others default OFF)
_DEFAULT_ON: frozenset[str] = frozenset({"events", "rewards", "firsthunt", "updates"})

# Per-category cooldown tracking {category: unix_timestamp_of_last_send}
_NOTIF_COOLDOWN: dict[str, float] = {}

# Cooldown in seconds (adjustable via /setsubnotifycooldown)
_COOLDOWN_SECS: int = 300  # 5 minutes


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
    """Return True if user_id is currently in the room (tracked via room_utils)."""
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
        lines.append(f"{'✅' if on else '❌'} {label}")

    lines.append("DM Status: SDK Unsupported")
    lines.append(
        f"In-Room Whisper: {'Available' if in_room else 'Offline — not reachable'}"
    )
    lines.append("/notifon <cat> | /notifoff <cat> | /notifall on|off")
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
        await _w(bot, user.id, _valid_category_msg()[:249])
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
        await _w(bot, user.id, _valid_category_msg()[:249])
        return
    db.set_sub_notif_pref(user.id, user.username, cat, 0)
    await _w(bot, user.id, f"🔔 {NOTIF_CATEGORIES[cat]} notifications: OFF ❌")


# ---------------------------------------------------------------------------
# /notifall on|off
# ---------------------------------------------------------------------------

async def handle_notifall(bot, user, args: list[str]) -> None:
    """/notifall on|off — toggle global notifications on or off."""
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        await _w(bot, user.id, "Usage: /notifall on|off")
        return
    enabled = 1 if args[1].lower() == "on" else 0
    db.set_sub_notif_global(user.id, user.username, enabled)
    # When enabling, seed default prefs if none exist yet
    if enabled:
        existing = db.get_sub_notif_prefs(user.id)
        if not existing:
            db.set_sub_notif_all_prefs(user.id, user.username, -1)  # seed defaults
    state = "ON ✅" if enabled else "OFF ❌"
    await _w(bot, user.id, f"🔔 Global notifications turned {state}")


# ---------------------------------------------------------------------------
# /notifdm  /opennotifs
# ---------------------------------------------------------------------------

async def handle_notifdm(bot, user) -> None:
    """/notifdm — explain DM notification status."""
    await _w(
        bot, user.id,
        "🔔 Notification DM Setup\n"
        "DM notifications are not supported by this SDK right now.\n"
        "I can only whisper players who are currently in the room.\n"
        "Stay in the room to receive notifications."
    )


async def handle_opennotifs(bot, user) -> None:
    """/opennotifs — alias for /notifdm."""
    await handle_notifdm(bot, user)


# ---------------------------------------------------------------------------
# /setsubnotifycooldown <minutes>  (manager+)
# ---------------------------------------------------------------------------

async def handle_setsubnotifycooldown(bot, user, args: list[str]) -> None:
    """/setsubnotifycooldown <minutes> — set per-category cooldown."""
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
    """/subnotify <category> <message> — send whisper notification to eligible in-room subscribers."""
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

    # Anti-spam cooldown (owner may bypass)
    now  = _time.time()
    last = _NOTIF_COOLDOWN.get(cat, 0.0)
    if _COOLDOWN_SECS > 0 and (now - last) < _COOLDOWN_SECS and not is_owner(user.username):
        remain = int(_COOLDOWN_SECS - (now - last))
        await _w(
            bot, user.id,
            f"🔔 Notification Cooldown\n"
            f"{NOTIF_CATEGORIES[cat]} notifications can be sent again in {_fmt_remain(remain)}."
        )
        return

    msg_text  = " ".join(args[2:])[:180]
    send_type = "invite" if invite else "normal"
    await _do_send(bot, user, cat, msg_text, send_type=send_type)


# ---------------------------------------------------------------------------
# /subnotifyinvite <category> <message>  (manager+)
# ---------------------------------------------------------------------------

async def handle_subnotifyinvite(bot, user, args: list[str]) -> None:
    """/subnotifyinvite — same as /subnotify with invite framing."""
    await handle_subnotify(bot, user, args, invite=True)


# ---------------------------------------------------------------------------
# /subnotifystatus [latest|failed|noconversation]  (manager+)
# ---------------------------------------------------------------------------

async def handle_subnotifystatus(bot, user, args: list[str] | None = None) -> None:
    """/subnotifystatus — show recent notification stats."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    sub = (args[1].lower() if args and len(args) > 1 else "latest")

    if sub == "noconversation":
        rows = db.get_sub_notif_no_conv_recipients(limit=10)
        if not rows:
            await _w(bot, user.id, "🔔 No players missing conversations.")
            return
        names = [f"@{r.get('username','?')}" for r in rows]
        msg = "Players without DM conversation:\n" + "\n".join(names)
        await _w(bot, user.id, msg[:249])
        return

    if sub == "failed":
        rows = db.get_sub_notif_failed_recipients(limit=10)
        if not rows:
            await _w(bot, user.id, "🔔 No failed deliveries found.")
            return
        lines = ["🔔 Failed Deliveries"]
        for r in rows:
            lines.append(f"@{r.get('username','?')} [{r.get('category','?')}]: {r.get('error','')[:40]}")
        await _w(bot, user.id, "\n".join(lines)[:249])
        return

    # Default: latest
    rows = db.get_sub_notif_logs(limit=5)
    if not rows:
        await _w(bot, user.id, "🔔 No notifications sent yet.")
        return

    latest = rows[0]
    cat    = NOTIF_CATEGORIES.get(latest.get("category", ""), latest.get("category", "?"))
    lines  = [
        "🔔 Notification Status",
        f"Last: {cat}",
        f"DM Sent: {latest.get('sent_dm_count', latest.get('sent_count', 0))}",
        f"Whisper Sent: {latest.get('sent_whisper_count', 0)}",
        f"Skipped: {latest.get('skipped_count', 0)}",
        f"No Conversation: {latest.get('no_conversation_count', 0)}",
        f"SDK Unsupported: {latest.get('unsupported_sdk_count', 0)}",
        f"Failed: {latest.get('failed_count', 0)}",
    ]
    if len(rows) > 1:
        lines.append(f"(+{len(rows)-1} older entries — /subnotifystatus latest)")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /testnotify @username <category> <message>  (manager+)
# ---------------------------------------------------------------------------

async def handle_testnotify(bot, user, args: list[str]) -> None:
    """/testnotify @username <category> <message> — test delivery to one player."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 4:
        await _w(bot, user.id, "Usage: /testnotify @username <category> <message>")
        return

    raw_target = args[1].lstrip("@").lower()
    cat        = args[2].lower()
    msg_text   = " ".join(args[3:])[:180]

    if cat not in NOTIF_CATEGORIES:
        await _w(bot, user.id, _valid_category_msg()[:249])
        return

    # Resolve target subscriber
    sub_row = db.get_subscriber(raw_target)

    subscribed = bool(sub_row and sub_row.get("subscribed") == 1)
    uid        = sub_row.get("user_id", "") if sub_row else ""

    global_row  = db.get_sub_notif_global(uid) if uid else {}
    global_on   = bool(global_row.get("global_enabled", 1))

    prefs       = db.get_sub_notif_prefs(uid) if uid else {}
    cat_on      = bool(prefs.get(cat, _default_enabled(cat)))

    in_room     = _is_in_room(uid) if uid else False

    # Determine what delivery would happen
    delivered   = "NO"
    reason      = ""
    if not subscribed:
        reason = "not_subscribed"
    elif not global_on:
        reason = "global_off"
    elif not cat_on:
        reason = "category_off"
    elif in_room and uid:
        full_msg = f"📣 Subscriber Alert — {NOTIF_CATEGORIES[cat]}\n[TEST] {msg_text}"[:249]
        try:
            await bot.highrise.send_whisper(uid, full_msg)
            delivered = "sent_whisper_in_room"
        except Exception as exc:
            delivered = "NO"
            reason = f"send_error: {str(exc)[:60]}"
    elif uid:
        reason = "no_conversation"
    else:
        reason = "user_id_unknown"

    lines = [
        "🔔 Test Notify",
        f"Target: @{raw_target}",
        f"Subscribed: {'YES' if subscribed else 'NO'}",
        f"Global: {'ON' if global_on else 'OFF'}",
        f"Category {NOTIF_CATEGORIES.get(cat, cat)}: {'ON' if cat_on else 'OFF'}",
        f"DM Conversation: SDK Unsupported",
        f"Currently In Room: {'YES' if in_room else 'NO'}",
        f"Delivered: {delivered}" + (f"\nReason: {reason}" if reason and delivered == "NO" else ""),
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
    Usage:
        from modules.sub_notif import send_subscriber_notification
        await send_subscriber_notification(bot, "mining", "Ore surge active!", send_type="auto")
    """
    if category not in NOTIF_CATEGORIES:
        print(f"[SUB_NOTIF] Unknown category '{category}'; skipping notification.")
        return

    class _FakeSender:
        id       = "system"
        username = "system"

    sender_obj = sender if sender is not None else _FakeSender()
    await _do_send(bot, sender_obj, category, message[:180], send_type=send_type)


# ---------------------------------------------------------------------------
# Core delivery engine
# ---------------------------------------------------------------------------

async def _do_send(bot, sender, cat: str, msg_text: str, *, send_type: str = "normal") -> None:
    """
    Deliver a subscriber notification to all eligible in-room subscribers.

    Delivery order per player:
      1. Not subscribed → skipped_not_subscribed
      2. global_enabled=0 → skipped_global_off
      3. category enabled=0 → skipped_category_off
      4. In room → send_whisper → sent_whisper_in_room
      5. Not in room → no_conversation
    """
    # Load all subscribed users (subscribed=1, has user_id)
    try:
        subscribers = db.get_all_subscribed_users_for_notify()
    except Exception as exc:
        print(f"[SUB_NOTIF] Error loading subscribers: {exc}")
        await _w(bot, sender.id, f"🔔 Error loading subscribers: {str(exc)[:100]}"[:249])
        return

    sent_dm       = 0
    sent_whisper  = 0
    skipped       = 0
    no_conv       = 0
    unsupported   = 0
    failed        = 0

    # Create log entry up front
    log_id = db.log_sub_notification_v2(
        cat, msg_text, send_type, sender.id, sender.username
    )

    cat_label = NOTIF_CATEGORIES[cat]
    if send_type == "invite":
        full_msg = (
            f"📣 Subscriber Alert — {cat_label}\n"
            f"{msg_text}\n"
            f"Join the room to participate!"
        )[:249]
    else:
        full_msg = f"📣 Subscriber Alert — {cat_label}\n{msg_text}"[:249]

    for sub in subscribers:
        uid   = sub.get("user_id", "")
        uname = sub.get("username", "") or "?"

        if not uid:
            continue

        # ── 1. Subscription already guaranteed by get_all_subscribed_users_for_notify ──

        # ── 2. Global enabled check ──────────────────────────────────────────
        global_row = db.get_sub_notif_global(uid)
        global_on  = global_row.get("global_enabled", 1)
        if not global_on:
            skipped += 1
            db.log_sub_notif_recipient_v2(
                log_id, uid, uname, cat, 1, 1, 0, "none", "skipped_global_off", ""
            )
            continue

        # ── 3. Category preference check ─────────────────────────────────────
        prefs  = db.get_sub_notif_prefs(uid)
        cat_on = prefs.get(cat, _default_enabled(cat))
        if not cat_on:
            skipped += 1
            db.log_sub_notif_recipient_v2(
                log_id, uid, uname, cat, 1, 0, 1, "none", "skipped_category_off", ""
            )
            continue

        # ── 4. Delivery ───────────────────────────────────────────────────────
        in_room = _is_in_room(uid)

        if in_room:
            try:
                await bot.highrise.send_whisper(uid, full_msg)
                sent_whisper += 1
                db.log_sub_notif_recipient_v2(
                    log_id, uid, uname, cat, 1, 1, 1, "whisper", "sent_whisper_in_room", ""
                )
            except Exception as exc:
                err = str(exc)[:120]
                traceback.print_exc()
                failed += 1
                db.log_sub_notif_recipient_v2(
                    log_id, uid, uname, cat, 1, 1, 1, "whisper", "failed", err
                )
        else:
            no_conv += 1
            db.log_sub_notif_recipient_v2(
                log_id, uid, uname, cat, 1, 1, 1, "none", "no_conversation", ""
            )

        await asyncio.sleep(0.05)

    # ── Update log with final counts ─────────────────────────────────────────
    db.update_sub_notif_log_v2(
        log_id, sent_dm, sent_whisper, skipped, no_conv, unsupported, failed
    )
    _NOTIF_COOLDOWN[cat] = _time.time()

    # ── Summary response ──────────────────────────────────────────────────────
    total_sent = sent_dm + sent_whisper
    total_subs = len(subscribers)

    if total_subs == 0:
        await _w(
            bot, sender.id,
            "🔔 Subscriber Notification\n"
            "No subscribed players found."
        )
    elif total_sent == 0 and no_conv > 0 and failed == 0:
        await _w(
            bot, sender.id,
            f"🔔 Subscriber Notification\n"
            f"No messages delivered.\n"
            f"Reason: No eligible players currently in room.\n"
            f"No Conversation: {no_conv} | Skipped: {skipped}"
        )
    else:
        await _w(
            bot, sender.id,
            f"🔔 Subscriber Notification Complete\n"
            f"Category: {cat_label}\n"
            f"DM Sent: {sent_dm}\n"
            f"Whisper Sent: {sent_whisper}\n"
            f"Skipped: {skipped} | No Conversation: {no_conv}\n"
            f"SDK Unsupported: {unsupported} | Failed: {failed}"
        )
