"""
modules/subscribers.py
Subscriber DM notification system for the Highrise Mini Game Bot.

Commands (public):
  /subscribe       — opt in to DM notifications
  /unsubscribe     — opt out
  /substatus       — show your subscription state
  /subhelp         — show subscription help
  /subhelp 2       — show admin announcement commands

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
import database as db
from highrise import BaseBot, User
from modules.permissions import (
    is_owner, is_admin, can_moderate, is_manager, is_moderator,
)


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
      - remove a leading slash
    """
    c = content.strip().lower()
    # Strip surrounding quotes (one pair only)
    if len(c) >= 2 and c[0] in ('"', "'") and c[-1] == c[0]:
        c = c[1:-1].strip()
    # Strip leading slash
    if c.startswith("/"):
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
    Returns (sent, pending, failed).
    """
    message = add_unsubscribe_footer(raw_message)
    sent = pending = failed = 0

    for sub in target_subs:
        uname = sub["username"]
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
    and delivers any pending subscriber messages.
    """
    norm = _normalize_dm(content)
    print(f"[DM] PROCESSING user_id={user_id[:12]}... conv={conversation_id[:12]}...")
    print(f"[DM] raw={content[:60]!r}  normalized={norm[:60]!r}")

    # Look up user by Highrise user_id
    user_row = db.get_user_by_username_via_id(user_id)
    if user_row is None:
        print(f"[DM] Unknown user_id={user_id[:12]}... — prompting to join room.")
        await send_dm(bot, conversation_id,
                      "👋 Hi! Join the room first, then reply 'subscribe' for notifications.")
        return

    username = user_row["username"]
    uname_lower = username.lower()
    print(f"[DM] Identified as @{username}")

    # Always save/update conversation_id and mark dm_available
    db.upsert_subscriber(uname_lower, user_id, conversation_id)

    # Deliver any queued pending messages now that we have an active conversation
    await deliver_pending_subscriber_messages(bot, uname_lower, conversation_id)

    # ── Keyword routing ───────────────────────────────────────────────────────

    if _is_unsubscribe_request(norm):
        db.set_subscribed(uname_lower, False)
        reply = "✅ Unsubscribed. Alerts are OFF."
        await send_dm(bot, conversation_id, reply)
        db.set_subscriber_last_dm(uname_lower)
        print(f"[DM] @{username} unsubscribed (keyword: {norm!r}).")
        return

    if _is_subscribe_request(norm):
        db.set_subscribed(uname_lower, True)
        db.set_dm_available(uname_lower, True)
        reply = "✅ Subscribed. Outside-room alerts are ON.\nStop alerts: reply unsubscribe."
        await send_dm(bot, conversation_id, reply[:249])
        db.set_subscriber_last_dm(uname_lower)
        print(f"[DM] @{username} subscribed (keyword: {norm!r}).")
        return

    # Unknown message — check if already subscribed to avoid repeating the pitch
    sub = db.get_subscriber(uname_lower)
    is_subscribed = bool(sub and sub.get("subscribed"))

    if is_subscribed:
        reply = "✅ You are subscribed. Stop alerts: reply unsubscribe."
    else:
        reply = "👋 Reply 'subscribe' for outside-room alerts, or 'unsubscribe' to opt out."

    await send_dm(bot, conversation_id, reply)
    print(f"[DM] @{username} sent unknown msg (subscribed={is_subscribed}). Replied with status.")


# ── /subscribe ────────────────────────────────────────────────────────────────

async def handle_subscribe(bot: BaseBot, user: User, args: list[str]) -> None:
    """/subscribe — opt in to DM notifications."""
    db.ensure_user(user.id, user.username)
    sub = db.get_subscriber(user.username)

    db.upsert_subscriber(user.username.lower(), user.id)

    if sub and sub.get("subscribed"):
        has_dm = bool(sub.get("conversation_id"))
        if has_dm:
            await _w(bot, user.id, "✅ Already subscribed with DM connected.")
        else:
            await _w(bot, user.id,
                     "✅ Already subscribed. DM me 'subscribe' once so I can notify you outside the room.")
        return

    db.set_subscribed(user.username.lower(), True)
    sub = db.get_subscriber(user.username)
    has_dm = bool(sub and sub.get("conversation_id"))

    if has_dm:
        await _w(bot, user.id, "✅ Subscribed! You'll receive outside-room notifications.")
    else:
        await _w(bot, user.id,
                 "✅ Subscribed! DM me 'subscribe' once so I can notify you outside the room.")


# ── /unsubscribe ──────────────────────────────────────────────────────────────

async def handle_unsubscribe(bot: BaseBot, user: User, args: list[str]) -> None:
    """/unsubscribe — opt out of DM notifications."""
    db.ensure_user(user.id, user.username)
    sub = db.get_subscriber(user.username)
    if not sub or not sub.get("subscribed"):
        await _w(bot, user.id, "You are not currently subscribed.")
        return
    db.set_subscribed(user.username.lower(), False)
    await _w(bot, user.id, "✅ Unsubscribed. You won't receive outside-room notifications.")


# ── /substatus ────────────────────────────────────────────────────────────────

async def handle_substatus(bot: BaseBot, user: User, args: list[str]) -> None:
    """/substatus — show subscription state."""
    db.ensure_user(user.id, user.username)
    sub = db.get_subscriber(user.username)
    subscribed = sub and sub.get("subscribed")
    has_dm = sub and bool(sub.get("conversation_id")) and sub.get("dm_available")

    if not subscribed:
        await _w(bot, user.id, "Subscribed: NO")
        return

    if has_dm:
        dm_label = "YES"
    elif sub and sub.get("conversation_id"):
        dm_label = "PENDING (DM bot 'subscribe' to activate)"
    else:
        dm_label = "NO (DM bot 'subscribe' to connect)"

    await _w(bot, user.id, f"Subscribed: YES | DM connected: {dm_label}")


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

    await _w(bot, user.id,
             "🔔 Subscribe\n"
             "/subscribe\n"
             "/unsubscribe\n"
             "/substatus\n"
             "DM bot 'subscribe' for outside-room alerts.")


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
    """/announce_subs <message> — DM all subscribed users (rate-limited)."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /announce_subs <message>")
        return
    raw_msg = " ".join(args[1:])
    if len(raw_msg) > 200:
        await _w(bot, user.id, "Announcement too long. Max 200 characters.")
        return

    # All subscribed users with DM + those without DM (will queue pending)
    dm_subs  = db.get_all_subscribed_with_dm()
    no_dm    = db.get_all_subscribed_no_dm()
    all_subs = dm_subs + no_dm

    if not all_subs:
        await _w(bot, user.id, "No subscribed users found.")
        return

    await _w(bot, user.id, f"📤 Sending to {len(all_subs)} subscriber(s)...")
    sent, pending, failed = await _broadcast(
        bot, user.username, all_subs, raw_msg, "all"
    )
    await _w(bot, user.id,
             f"📣 Announcement sent: {sent} delivered, {pending} pending, {failed} failed.")
    print(f"[SUBS] announce_subs by @{user.username}: {sent} sent, {pending} pending, {failed} failed.")


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
