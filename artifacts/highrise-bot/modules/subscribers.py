"""
modules/subscribers.py
Subscriber DM notification system for the Highrise Mini Game Bot.

Commands (public):
  /subscribe       — opt in to DM notifications
  /unsubscribe     — opt out
  /substatus       — show your subscription state + pref preview
  /subhelp [2]     — show subscription help

Commands (staff):
  /subscribers     — list all subscriber records (mod+)

Commands (admin+):
  /dmnotify <username> <message>   — DM a specific subscriber
  /announce_subs <message>         — DM all active subscribers
  /announce_vip <message>          — DM subscribed VIP users
  /announce_staff <message>        — DM subscribed staff members

Event hook:
  process_incoming_dm(bot, user_id, conversation_id, content, is_new)
  deliver_pending_subscriber_messages(bot, username, conversation_id)
"""
from __future__ import annotations

import asyncio
import time
import database as db
from highrise import BaseBot, User
from modules.notifications import send_notification
from modules.permissions import (
    is_owner, is_admin, can_moderate, is_manager, is_moderator,
)

# ---------------------------------------------------------------------------
# PM command routing helpers  (Part B)
# ---------------------------------------------------------------------------

# Per-user cooldown (seconds) for wrong-bot routing hints in DMs.
# Prevents spam when a player repeatedly DMs the wrong bot.
_PM_WRONG_BOT_CD: dict[str, float] = {}
_PM_WRONG_BOT_CD_SECS = 60.0


class _FakePMUser:
    """Duck-typed stand-in for highrise.User when routing DM commands."""
    __slots__ = ("id", "username")

    def __init__(self, user_id: str, username: str) -> None:
        self.id       = user_id
        self.username = username


def _pm_wrong_bot_on_cooldown(user_id: str) -> bool:
    last = _PM_WRONG_BOT_CD.get(user_id, 0.0)
    return (time.monotonic() - last) < _PM_WRONG_BOT_CD_SECS


def _pm_wrong_bot_mark(user_id: str) -> None:
    _PM_WRONG_BOT_CD[user_id] = time.monotonic()


# ── Unsubscribe footer ────────────────────────────────────────────────────────

_UNSUB_FOOTER = "\nStop alerts: reply unsubscribe."


def add_unsubscribe_footer(message: str) -> str:
    """
    Append the unsubscribe footer to an outside-room DM message.
    Shortens the main message first if needed to keep total under 249 chars.
    Do NOT use this for room chat or in-room whispers.
    """
    if len(message) + len(_UNSUB_FOOTER) <= 249:
        return message + _UNSUB_FOOTER
    available = 249 - len(_UNSUB_FOOTER)
    return message[:available] + _UNSUB_FOOTER


# ── Message normalisation ──────────────────────────────────────────────────────

def _normalize_dm(content: str) -> str:
    """
    Normalise a raw DM message for keyword matching:
      - strip whitespace
      - lowercase
      - remove surrounding single/double quotes
      - remove a leading slash or exclamation mark
    """
    c = content.strip().lower()
    # Strip surrounding quotes (one pair only)
    if len(c) >= 2 and c[0] in ('"', "'") and c[-1] == c[0]:
        c = c[1:-1].strip()
    # Strip leading slash or ! (so !subscribe and /subscribe both normalize to subscribe)
    if c.startswith("/") or c.startswith("!"):
        c = c[1:].strip()
    return c


# ── Unsubscribe keyword detection ─────────────────────────────────────────────

_UNSUB_TRIGGERS = ("unsubscribe", "unsub", "stop alerts", "opt out", "optout", "stop")


def _is_unsubscribe_request(norm: str) -> bool:
    """norm must already be lowercased and stripped via _normalize_dm."""
    return any(norm == kw or norm.startswith(kw) for kw in _UNSUB_TRIGGERS)


def _is_subscribe_request(norm: str) -> bool:
    """norm must already be lowercased and stripped via _normalize_dm."""
    return norm == "subscribe" or norm.startswith("subscribe")


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


async def send_dm(bot: BaseBot, conversation_id: str, message: str) -> bool:
    """Send a Highrise conversation DM. Returns True on success."""
    try:
        await bot.highrise.send_message(conversation_id, message[:249])
        return True
    except Exception as exc:
        print(f"[SUBS] send_dm failed (conv={conversation_id[:12]}...): {exc}")
        return False


def _is_admin_or_owner(username: str) -> bool:
    return is_owner(username) or is_admin(username)


def _is_staff(username: str) -> bool:
    """True for any staff tier (owner/admin/manager/mod)."""
    return can_moderate(username)


# ── Pending subscriber message delivery ───────────────────────────────────────

async def deliver_pending_subscriber_messages(
    bot: BaseBot, username: str, conversation_id: str | None = None
) -> None:
    """
    Deliver any queued pending subscriber messages to *username*.
    If conversation_id is not provided, looks it up from the subscriber record.
    Called on user DM (live conv_id), user join, or first chat command.
    """
    try:
        uname = username.lower().strip()
        pending = db.get_pending_sub_messages(uname)
        if not pending:
            return

        # Resolve conversation_id if not provided
        conv = conversation_id
        if not conv:
            sub = db.get_subscriber(uname)
            if not sub or not sub.get("conversation_id") or not sub.get("dm_available"):
                return
            conv = sub["conversation_id"]

        for msg in pending:
            ok = await send_dm(bot, conv, msg["message"])
            if ok:
                db.mark_pending_sub_delivered(msg["id"])
                db.set_subscriber_last_dm(uname)
                print(f"[SUBS] Pending message delivered to @{username} (id={msg['id']}).")
            else:
                db.record_pending_sub_failed(msg["id"], "send_message failed")
                print(f"[SUBS] Pending delivery failed for @{username} (id={msg['id']}).")
            await asyncio.sleep(0.5)
    except Exception as exc:
        print(f"[SUBS] deliver_pending error for @{username}: {exc!r}")


# ── Shared broadcast helper ────────────────────────────────────────────────────

async def _broadcast(
    bot: BaseBot,
    sender_username: str,
    target_subs: list[dict],
    raw_message: str,
    target_type: str,
) -> tuple[int, int, int]:
    """
    DM each subscriber in target_subs, queue pending for those without DM.
    Respects announcement_alerts preference.
    Returns (sent, pending, failed).
    """
    message = add_unsubscribe_footer(raw_message)
    sent = pending = failed = 0

    # Map target_type to the relevant pref column
    _type_to_pref = {
        "all":   "announcement_alerts",
        "vip":   "vip_alerts",
        "staff": "staff_alerts",
        "single": "announcement_alerts",
    }
    pref_col = _type_to_pref.get(target_type, "announcement_alerts")

    for sub in target_subs:
        uname = sub["username"]

        # Check notification preference before sending
        try:
            prefs = db.get_notify_prefs(uname)
            if not prefs.get(pref_col, 1):
                print(f"[SUBS] @{uname} has {pref_col} OFF — skipping broadcast.")
                continue
        except Exception:
            pass

        conv_id = sub.get("conversation_id")
        has_dm = conv_id and sub.get("dm_available")

        if has_dm:
            ok = await send_dm(bot, conv_id, message)
            if ok:
                sent += 1
                db.set_subscriber_last_dm(uname)
            else:
                db.set_dm_available(uname, False)
                db.add_pending_sub_message(uname, message, target_type)
                pending += 1
                print(f"[SUBS] DM failed for @{uname} — queued pending.")
        else:
            db.add_pending_sub_message(uname, message, target_type)
            pending += 1

        await asyncio.sleep(1)

    try:
        db.log_subscriber_announcement(
            sender_username, target_type, message,
            sent, pending, failed,
        )
    except Exception as exc:
        print(f"[SUBS] announce log error: {exc!r}")

    return sent, pending, failed


# ── on_message event handler ──────────────────────────────────────────────────

async def process_incoming_dm(
    bot: BaseBot,
    user_id: str,
    conversation_id: str,
    content: str,
    is_new_conversation: bool,
) -> None:
    """
    Called from on_message when the bot receives a DM.
    Saves conversation_id, handles subscribe/unsubscribe keywords,
    routes slash-commands to the owning bot handler (B-project),
    and delivers any pending subscriber messages.
    """
    # Late import avoids circular dependency (multi_bot imports from config, not subscribers)
    from config import BOT_MODE
    from modules.multi_bot import should_this_bot_handle, _resolve_command_owner  # noqa: PLC0415

    norm = _normalize_dm(content)
    print(f"[DM] PROCESSING user_id={user_id[:12]}... conv={conversation_id[:12]}...")
    print(f"[DM] raw={content[:60]!r}  normalized={norm[:60]!r}")

    # Look up user by Highrise user_id
    user_row = db.get_user_by_username_via_id(user_id)
    if user_row is None:
        print(f"[DM] Unknown user_id={user_id[:12]}... — checking subscriber table by user_id.")
        # Try to find by user_id in subscriber_users directly
        sub_by_id = db.get_subscriber_by_user_id(user_id)
        if sub_by_id:
            username = sub_by_id.get("username", "")
            uname_lower = username.lower()
            print(f"[DM] Found subscriber row for unknown user_id — @{username}")
        else:
            # User has never used the bot in the room; save placeholder by user_id
            if BOT_MODE in ("host", "all"):
                norm_early = _normalize_dm(content)
                if _is_subscribe_request(norm_early):
                    # Save the conversation_id keyed on user_id as placeholder username
                    db.upsert_subscriber_by_user_id(user_id, f"uid_{user_id[:12]}", conversation_id)
                    db.set_subscribed_by_user_id(user_id, True)
                    db.set_dm_available_by_user_id(user_id, True)
                    await send_dm(bot, conversation_id,
                                  "✅ Subscribed.\n"
                                  "Outside-room alerts are connected.\n"
                                  "Use !notif to manage categories.")
                else:
                    await send_dm(bot, conversation_id,
                                  "👋 Hi! Reply 'subscribe' for outside-room notifications.")
            return

    else:
        username = user_row["username"]
        uname_lower = username.lower()
    print(f"[DM] Identified as @{username}")

    # Always save/update conversation_id and mark dm_available (lookup by user_id first)
    db.upsert_subscriber_by_user_id(user_id, uname_lower, conversation_id)

    # Deliver any queued pending messages now that we have an active conversation
    await deliver_pending_subscriber_messages(bot, uname_lower, conversation_id)

    # ── Slash-command routing (B-project) ────────────────────────────────────
    # If the DM looks like a command (starts with /), try to route it.
    stripped = content.strip()
    if stripped.startswith("/"):
        cmd_word = stripped.split()[0][1:].lower()
        if should_this_bot_handle(cmd_word):
            # This bot owns the command — dispatch through on_chat so all
            # existing handlers (which reply via whisper) respond normally.
            fake_user = _FakePMUser(user_id, username)
            print(f"[DM] Routing /{cmd_word} via on_chat for @{username}")
            try:
                await bot.on_chat(fake_user, stripped)  # type: ignore[arg-type]
            except Exception as exc:
                print(f"[DM] on_chat dispatch error for /{cmd_word}: {exc}")
                await bot.highrise.send_whisper(
                    user_id, "❌ Something went wrong handling that command."
                )
            return
        else:
            # Wrong bot — send a routing hint (with per-user cooldown)
            owner_mode = _resolve_command_owner(cmd_word)
            if owner_mode and not _pm_wrong_bot_on_cooldown(user_id):
                _pm_wrong_bot_mark(user_id)
                hint = (
                    f"❌ /{cmd_word} is handled by the {owner_mode} bot. "
                    "Send it in the room instead!"
                )
                await bot.highrise.send_whisper(user_id, hint[:249])
                print(f"[DM] @{username} sent /{cmd_word} to wrong bot ({BOT_MODE}); "
                      f"owner={owner_mode}. Sent hint.")
            elif owner_mode:
                print(f"[DM] @{username} wrong-bot hint suppressed (cooldown).")
            return

    # ── Keyword routing ───────────────────────────────────────────────────────

    if _is_unsubscribe_request(norm):
        db.set_subscribed(uname_lower, False)
        db.set_subscriber_manually_unsubscribed(uname_lower, True)
        reply = "✅ Unsubscribed. All bot alerts are OFF."
        await send_dm(bot, conversation_id, reply)
        db.set_subscriber_last_dm(uname_lower)
        print(f"[DM] @{username} unsubscribed via DM (keyword: {norm!r}).")
        return

    if _is_subscribe_request(norm):
        db.set_subscribed_by_user_id(user_id, True)
        db.set_dm_available(uname_lower, True)
        db.set_subscriber_manually_unsubscribed(uname_lower, False)
        db.ensure_notify_prefs(uname_lower)
        reply = (
            "✅ Subscribed.\n"
            "Outside-room alerts are connected.\n"
            "Use !notif to manage categories."
        )
        await send_dm(bot, conversation_id, reply[:249])
        db.set_subscriber_last_dm(uname_lower)
        print(f"[DM] @{username} subscribed via DM (keyword: {norm!r}).")
        return

    # Unknown non-command message.
    # Only EmceeBot (host/all mode) sends generic alert-status replies.
    # Dedicated bots stay silent on unknown DMs to avoid confusion.
    if BOT_MODE not in ("host", "all"):
        print(f"[DM] @{username} sent non-command DM to {BOT_MODE} bot — ignoring.")
        return

    sub = db.get_subscriber(uname_lower)
    is_subscribed = bool(sub and sub.get("subscribed"))

    if is_subscribed:
        reply = "✅ Alerts ON. Use notifysettings to choose alerts. Stop: reply unsubscribe."
    else:
        reply = "Reply 'subscribe' for alerts, or 'unsubscribe' to opt out."

    await send_dm(bot, conversation_id, reply)
    print(f"[DM] @{username} sent unknown msg (subscribed={is_subscribed}). Replied with status.")


# ── /subscribe ────────────────────────────────────────────────────────────────

async def handle_subscribe(bot: BaseBot, user: User, args: list[str]) -> None:
    """!subscribe / /subscribe — opt in to DM notifications."""
    db.ensure_user(user.id, user.username)
    uname = user.username.lower()

    # Upsert by user_id first to avoid duplicate rows and fix mismatched records
    db.upsert_subscriber_by_user_id(user.id, uname)
    db.set_subscribed_by_user_id(user.id, True)
    db.set_subscriber_manually_unsubscribed(uname, False)
    db.ensure_notify_prefs(uname)

    sub = db.get_subscriber_by_user_id(user.id) or db.get_subscriber(uname)
    has_dm = bool(sub and sub.get("conversation_id") and sub.get("dm_available"))

    if has_dm:
        await _w(bot, user.id,
                 "✅ Subscribed! Outside-room alerts are ON. Use !notif to manage categories.")
    else:
        await _w(bot, user.id,
                 "✅ Subscribed. For outside-room alerts, DM EmceeBot: subscribe\n"
                 "Use !notif to manage categories.")


# ── /unsubscribe ──────────────────────────────────────────────────────────────

async def handle_unsubscribe(bot: BaseBot, user: User, args: list[str]) -> None:
    """!unsubscribe / /unsubscribe — opt out of DM notifications."""
    db.ensure_user(user.id, user.username)
    uname = user.username.lower()
    sub = db.get_subscriber_by_user_id(user.id) or db.get_subscriber(uname)
    if not sub or not sub.get("subscribed"):
        await _w(bot, user.id, "You are not currently subscribed.")
        return
    db.set_subscribed_by_user_id(user.id, False)
    db.set_subscriber_manually_unsubscribed(uname, True)
    await _w(bot, user.id, "✅ Unsubscribed. All bot alerts are OFF.")


# ── /substatus ────────────────────────────────────────────────────────────────

async def handle_substatus(bot: BaseBot, user: User, args: list[str]) -> None:
    """!substatus / /substatus — show subscription state + top pref preview."""
    db.ensure_user(user.id, user.username)
    uname = user.username.lower()
    sub = db.get_subscriber_by_user_id(user.id) or db.get_subscriber(uname)
    subscribed = sub and sub.get("subscribed")
    has_dm = sub and bool(sub.get("conversation_id")) and sub.get("dm_available")

    if not subscribed:
        await _w(bot, user.id,
                 "Subscribed: NO\nUse !subscribe to opt in.")
        return

    dm_label = "YES" if has_dm else "NO (DM EmceeBot: subscribe)"

    prefs = db.get_notify_prefs(uname)
    def _yn(col: str) -> str:
        return "ON" if prefs.get(col, 1) else "OFF"

    msg = (
        f"Subscribed: YES | DM: {dm_label}\n"
        f"Bank {_yn('bank_alerts')} | Events {_yn('event_alerts')} | "
        f"Gold {_yn('gold_alerts')} | Announce {_yn('announcement_alerts')}"
    )
    await _w(bot, user.id, msg[:249])


# ── /subhelp ──────────────────────────────────────────────────────────────────

async def handle_subhelp(bot: BaseBot, user: User, args: list[str]) -> None:
    """/subhelp [2] — subscription help; page 2 shows admin announce commands."""
    page = args[1].strip() if len(args) > 1 else "1"

    if page == "2":
        if not _is_admin_or_owner(user.username):
            await _w(bot, user.id, "Page 2 is for admins only.")
            return
        await _w(bot, user.id,
                 "📣 Announce\n"
                 "/announce_subs <msg>\n"
                 "/announce_vip <msg>\n"
                 "/announce_staff <msg>\n"
                 "/dmnotify <user> <msg>")
        return

    if page == "3":
        if not _is_admin_or_owner(user.username):
            await _w(bot, user.id, "Page 3 is for staff only.")
            return
        await _w(bot, user.id,
                 "Debug: /debugnotify <user>\n"
                 "/testnotify <user> <type>\n"
                 "/testnotifyall <type> (owner)\n"
                 "/pendingnotify <user>\n"
                 "/clearpendingnotify <user>")
        return

    await _w(bot, user.id,
             "🔔 Subscribe\n"
             "/subscribe\n"
             "/unsubscribe\n"
             "/substatus\n"
             "/notifysettings\n"
             "/notify <type> on/off")


# ── /subscribers (staff) ──────────────────────────────────────────────────────

async def handle_subscribers(bot: BaseBot, user: User, args: list[str]) -> None:
    """/subscribers — staff: list subscriber records."""
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    rows = db.get_all_subscribers_staff()
    if not rows:
        await _w(bot, user.id, "No subscribers yet.")
        return
    total = len(rows)
    active = sum(
        1 for r in rows
        if r.get("subscribed") and r.get("conversation_id") and r.get("dm_available")
    )
    pending_count = sum(1 for r in rows if r.get("subscribed") and not r.get("conversation_id"))
    lines = [f"📬 {total} record(s) | {active} DM-ready | {pending_count} pending DM:"]
    for r in rows[:5]:
        sub_icon = "✅" if r.get("subscribed") else "❌"
        dm_icon  = "📩" if (r.get("conversation_id") and r.get("dm_available")) else "🔕"
        lines.append(f"{sub_icon}{dm_icon} @{r['username']}")
    if total > 5:
        lines.append(f"...and {total - 5} more.")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ── /dmnotify (admin+) ────────────────────────────────────────────────────────

async def handle_dmnotify(bot: BaseBot, user: User, args: list[str]) -> None:
    """/dmnotify <username> <message> — DM a specific subscriber."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /dmnotify <username> <message>")
        return
    target_name = args[1].lstrip("@").lower().strip()
    raw_msg = " ".join(args[2:])
    if len(raw_msg) > 200:
        await _w(bot, user.id, "Message too long. Max 200 characters.")
        return
    message = add_unsubscribe_footer(raw_msg)
    sub = db.get_subscriber(target_name)

    if not sub:
        await _w(bot, user.id, f"@{target_name} has no subscriber record.")
        return

    if not sub.get("conversation_id"):
        db.add_pending_sub_message(target_name, message, "single")
        await _w(bot, user.id, f"No DM connected for @{target_name}. Saved as pending.")
        print(f"[SUBS] /dmnotify to @{target_name} queued as pending (no conv_id).")
        return

    if not sub.get("dm_available"):
        await _w(bot, user.id,
                 f"@{target_name}'s DM channel was previously unreachable. Trying anyway...")

    ok = await send_dm(bot, sub["conversation_id"], message)
    if ok:
        db.set_subscriber_last_dm(target_name)
        db.set_dm_available(target_name, True)
        await _w(bot, user.id, f"✅ DM sent to @{target_name}.")
    else:
        db.set_dm_available(target_name, False)
        db.add_pending_sub_message(target_name, message, "single")
        await _w(bot, user.id, f"❌ DM failed. Saved as pending for @{target_name}.")
        print(f"[SUBS] /dmnotify to @{target_name} failed — queued pending.")


# ── /announce_subs (admin+) ───────────────────────────────────────────────────

async def handle_announce_subs(bot: BaseBot, user: User, args: list[str]) -> None:
    """/announce_subs <message> — DM all subscribed users via send_notification()."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /announce_subs <message>")
        return

    raw_msg = " ".join(args[1:]).strip()
    if not raw_msg:
        await _w(bot, user.id, "Usage: /announce_subs <message>")
        return
    if len(raw_msg) > 220:
        await _w(bot, user.id, "Announcement too long. Max 220 characters.")
        return

    all_subs = db.get_all_subscribed_with_dm() + db.get_all_subscribed_no_dm()
    if not all_subs:
        await _w(bot, user.id, "No subscribed users found.")
        return

    await _w(bot, user.id, f"📤 Sending to {len(all_subs)} subscriber(s)...")
    sent = pending = skipped = 0

    for sub in all_subs:
        uname = sub["username"]
        result = await send_notification(bot, uname, "announcements", raw_msg)
        if result in ("sent", "whispered"):
            sent += 1
        elif result == "pending":
            pending += 1
        else:
            skipped += 1
        await asyncio.sleep(1.0)

    await _w(bot, user.id,
             f"📣 Done: {sent} delivered, {pending} pending, {skipped} skipped.")
    print(f"[SUBS] announce_subs by @{user.username}: {sent}s {pending}p {skipped}sk")


# ── /announce_vip (admin+) ────────────────────────────────────────────────────

async def handle_announce_vip(bot: BaseBot, user: User, args: list[str]) -> None:
    """/announce_vip <message> — DM subscribed VIP users."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /announce_vip <message>")
        return
    raw_msg = " ".join(args[1:])
    if len(raw_msg) > 200:
        await _w(bot, user.id, "Announcement too long. Max 200 characters.")
        return

    # Filter all subscribed users through VIP check
    all_subs = db.get_all_subscribed_with_dm() + db.get_all_subscribed_no_dm()
    vip_subs: list[dict] = []
    for sub in all_subs:
        try:
            conn = db.get_connection()
            row = conn.execute(
                "SELECT 1 FROM vip_users WHERE username = ? LIMIT 1",
                (sub["username"].lower(),),
            ).fetchone()
            conn.close()
            if row:
                vip_subs.append(sub)
        except Exception:
            pass

    if not vip_subs:
        await _w(bot, user.id,
                 "No subscribed VIP users found. (VIP system may not be configured.)")
        return

    await _w(bot, user.id, f"📤 Sending to {len(vip_subs)} VIP subscriber(s)...")
    sent, pending, failed = await _broadcast(
        bot, user.username, vip_subs, raw_msg, "vip"
    )
    await _w(bot, user.id,
             f"📣 VIP announcement: {sent} delivered, {pending} pending, {failed} failed.")
    print(f"[SUBS] announce_vip by @{user.username}: {sent} sent, {pending} pending, {failed} failed.")


# ── /announce_staff (admin+) ──────────────────────────────────────────────────

async def handle_announce_staff(bot: BaseBot, user: User, args: list[str]) -> None:
    """/announce_staff <message> — DM subscribed staff members."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /announce_staff <message>")
        return
    raw_msg = " ".join(args[1:])
    if len(raw_msg) > 200:
        await _w(bot, user.id, "Announcement too long. Max 200 characters.")
        return

    all_subs = db.get_all_subscribed_with_dm() + db.get_all_subscribed_no_dm()
    staff_subs = [s for s in all_subs if _is_staff(s["username"])]

    if not staff_subs:
        await _w(bot, user.id, "No subscribed staff members found.")
        return

    await _w(bot, user.id, f"📤 Sending to {len(staff_subs)} staff subscriber(s)...")
    sent, pending, failed = await _broadcast(
        bot, user.username, staff_subs, raw_msg, "staff"
    )
    await _w(bot, user.id,
             f"📣 Staff announcement: {sent} delivered, {pending} pending, {failed} failed.")
    print(f"[SUBS] announce_staff by @{user.username}: {sent} sent, {pending} pending, {failed} failed.")


# ── /debugsub <username> (owner only) ────────────────────────────────────────

async def handle_debugsub(bot: BaseBot, user: User, args: list[str]) -> None:
    """/debugsub <username> — owner: show full subscriber record for diagnostics."""
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /debugsub <username>")
        return

    target = args[1].lstrip("@").lower().strip()
    sub = db.get_subscriber(target)

    if not sub:
        await _w(bot, user.id, f"No subscriber record for @{target}.")
        return

    sub_flag   = "YES" if sub.get("subscribed") else "NO"
    dm_flag    = "YES" if sub.get("dm_available") else "NO"
    conv_flag  = "YES" if sub.get("conversation_id") else "NO"
    last_dm    = sub.get("last_dm_at") or "never"
    sub_at     = sub.get("subscribed_at") or "unknown"
    auto_sub   = "YES" if sub.get("auto_subscribed_from_tip") else "NO"

    # Pull latest delivery error from pending_subscriber_messages
    last_err = ""
    try:
        pending = db.get_pending_sub_messages(target)
        errs = [m.get("last_error") for m in pending if m.get("last_error")]
        if errs:
            last_err = errs[-1][:60]
    except Exception:
        pass

    lines = [
        f"🔍 @{target} subscriber debug:",
        f"Subscribed: {sub_flag}  DM available: {dm_flag}",
        f"Conv ID saved: {conv_flag}  Auto-sub: {auto_sub}",
        f"Subscribed at: {sub_at[:16]}",
        f"Last DM sent: {last_dm[:16]}",
    ]
    if last_err:
        lines.append(f"Last error: {last_err}")

    await _w(bot, user.id, "\n".join(lines)[:249])


# ── !forcesub @username (manager+) ───────────────────────────────────────────

async def handle_forcesub(bot: BaseBot, user: User, args: list[str]) -> None:
    """!forcesub @username — force subscription ON for a player (manager+)."""
    if not is_manager(user.username) and not is_admin(user.username) and not is_owner(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !forcesub @username")
        return

    target_raw = args[1].lstrip("@").lower().strip()

    uid = None
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT user_id FROM users WHERE LOWER(username) = ?", (target_raw,)
        ).fetchone()
        conn.close()
        if row:
            uid = row["user_id"]
    except Exception:
        pass

    updated = db.force_subscribe_user(uid, target_raw)
    conv_id  = updated.get("conversation_id") or ""
    dm_label = "YES" if conv_id else "NO"

    line1 = f"✅ Forced subscription ON for @{target_raw}."
    if conv_id:
        prefs = db.get_notify_prefs(target_raw)
        ev_on = "ON" if prefs.get("event_alerts", 1) else "OFF"
        gl_on = "ON"
        try:
            from modules.sub_notif import _get_or_create_sub_notif_global
            gr = db.get_sub_notif_global(uid or "") if uid else {}
            gl_on = "ON" if gr.get("global_enabled", 1) else "OFF"
        except Exception:
            pass
        line2 = f"DM Connected: {dm_label} | Events: {ev_on} | Global: {gl_on}"
    else:
        line2 = (
            "DM Connected: NO\n"
            "Outside-room alerts unavailable until they DM EmceeBot: subscribe"
        )

    await _w(bot, user.id, f"{line1}\n{line2}"[:249])
    print(f"[SUBS] !forcesub on @{target_raw} by @{user.username} (uid={uid})")


# ── !fixsub @username (manager+) ─────────────────────────────────────────────

async def handle_fixsub(bot: BaseBot, user: User, args: list[str]) -> None:
    """!fixsub @username — repair/merge subscriber record for a player (manager+)."""
    if not is_manager(user.username) and not is_admin(user.username) and not is_owner(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !fixsub @username")
        return

    target_raw = args[1].lstrip("@").lower().strip()

    uid = None
    uid_found = False
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT user_id FROM users WHERE LOWER(username) = ?", (target_raw,)
        ).fetchone()
        conn.close()
        if row:
            uid = row["user_id"]
            uid_found = True
    except Exception:
        pass

    if uid:
        db.upsert_subscriber_by_user_id(uid, target_raw)

    merge = db.merge_duplicate_subscriber_rows(target_raw)
    sub   = db.get_subscriber(target_raw)
    if not sub and uid:
        sub = db.get_subscriber_by_user_id(uid)

    sub_found  = sub is not None
    conv_id    = (sub or {}).get("conversation_id") or ""
    subscribed = bool((sub or {}).get("subscribed"))

    if sub_found:
        db.ensure_notify_prefs(target_raw)

    lines = [
        f"🔧 Subscription Repair: @{target_raw}",
        f"User ID: {'FOUND' if uid_found else 'NOT FOUND'}",
        f"Subscriber Row: {'FOUND' if sub_found else 'NOT FOUND'}",
        f"Conversation ID: {'FOUND' if conv_id else 'MISSING'}",
        f"Subscribed: {'YES' if subscribed else 'NO'}",
        "Prefs: OK" if sub_found else "Prefs: N/A",
        f"Duplicates merged: {merge.get('merged', 0)}",
    ]
    if not conv_id:
        lines.append("Action needed: player must DM EmceeBot: subscribe")
    await _w(bot, user.id, "\n".join(lines)[:249])
    print(f"[SUBS] !fixsub @{target_raw} by @{user.username}: merge={merge}")
