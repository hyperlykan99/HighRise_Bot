"""
modules/subscribers.py
Subscriber DM notification system for the Highrise Mini Game Bot.

Allows players to opt in to outside-room DM notifications via Highrise
conversation messages. The bot can only DM a player if they have first
sent the bot a DM (which creates a conversation_id in the SDK).

Commands (public):
  /subscribe       — opt in to DM notifications
  /unsubscribe     — opt out
  /substatus       — show your subscription state

Commands (staff):
  /subscribers     — list all subscriber records (mod+)

Commands (admin+):
  /dmnotify <username> <message>   — DM a specific subscriber
  /announce_subs <message>         — DM all active subscribers

Event hook:
  process_incoming_dm(bot, user_id, conversation_id, content, is_new)
  — called from on_message; saves conversation_id, handles "subscribe"/"unsubscribe" keywords.
"""
from __future__ import annotations

import asyncio
import database as db
from highrise import BaseBot, User
from modules.permissions import is_owner, is_admin, can_moderate


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


async def send_dm(bot: BaseBot, conversation_id: str, message: str) -> bool:
    """
    Send a Highrise conversation/inbox DM.
    Returns True on success, False on failure.
    Logs real errors to console.
    """
    try:
        await bot.highrise.send_message(conversation_id, message[:249])
        return True
    except Exception as exc:
        print(f"[SUBS] send_dm failed (conv={conversation_id[:12]}...): {exc}")
        return False


def _is_admin_or_owner(username: str) -> bool:
    return is_owner(username) or is_admin(username)


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

    1. Looks up the user by user_id in the users table.
    2. Saves/updates their conversation_id in subscriber_users.
    3. If content contains 'subscribe' → mark subscribed, reply confirmation.
    4. If content contains 'unsubscribe' → mark unsubscribed, reply confirmation.
    5. Otherwise acknowledge with instructions.
    """
    # Resolve username from DB by user_id
    user_row = db.get_user_by_username_via_id(user_id)
    if user_row is None:
        # User not yet in room/DB — store minimal record keyed by user_id only
        # We can't link to bank notifications without a username, but save the
        # conversation_id so we can update it once they join the room.
        print(f"[SUBS] DM from unknown user_id={user_id[:12]}... conv={conversation_id[:12]}...")
        # Still reply so they aren't left hanging
        await send_dm(bot, conversation_id,
                      "👋 Hi! Join the room and type /subscribe to get notifications.")
        return

    username = user_row["username"]
    uname_lower = username.lower()

    # Upsert subscriber record with conversation_id
    db.upsert_subscriber(uname_lower, user_id, conversation_id)

    content_lower = content.lower().strip()

    if "unsubscribe" in content_lower:
        db.set_subscribed(uname_lower, False)
        await send_dm(bot, conversation_id,
                      "❌ Unsubscribed. You won't receive outside-room notifications.")
        print(f"[SUBS] @{username} unsubscribed via DM.")
        return

    if "subscribe" in content_lower:
        db.set_subscribed(uname_lower, True)
        await send_dm(bot, conversation_id,
                      "✅ Subscribed. I can now notify you outside the room.")
        print(f"[SUBS] @{username} subscribed via DM.")
        return

    # Generic DM — just acknowledge
    await send_dm(bot, conversation_id,
                  "👋 Hi! Reply 'subscribe' to get outside-room alerts, or 'unsubscribe' to opt out.")


# ── /subscribe ────────────────────────────────────────────────────────────────

async def handle_subscribe(bot: BaseBot, user: User, args: list[str]) -> None:
    """/subscribe — opt in to DM notifications."""
    db.ensure_user(user.id, user.username)
    sub = db.get_subscriber(user.username)

    # Create or refresh record
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
    await _w(bot, user.id, "❌ Unsubscribed. You won't receive outside-room notifications.")


# ── /substatus ────────────────────────────────────────────────────────────────

async def handle_substatus(bot: BaseBot, user: User, args: list[str]) -> None:
    """/substatus — show subscription state."""
    db.ensure_user(user.id, user.username)
    sub = db.get_subscriber(user.username)
    subscribed = sub and sub.get("subscribed")
    has_dm = sub and bool(sub.get("conversation_id")) and sub.get("dm_available")

    sub_label = "YES" if subscribed else "NO"
    if has_dm:
        dm_label = "YES"
    elif sub and sub.get("conversation_id"):
        dm_label = "PENDING"
    else:
        dm_label = "NO"

    msg = f"Subscribed: {sub_label} | DM connected: {dm_label}"
    if subscribed and not has_dm:
        msg += ". DM me 'subscribe' to connect."
    await _w(bot, user.id, msg)


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
    active = sum(1 for r in rows if r.get("subscribed") and r.get("conversation_id") and r.get("dm_available"))
    # Show summary + first 5
    lines = [f"📬 {total} subscriber(s), {active} DM-ready:"]
    for r in rows[:5]:
        sub_icon = "✅" if r.get("subscribed") else "❌"
        dm_icon  = "📩" if (r.get("conversation_id") and r.get("dm_available")) else "🔕"
        lines.append(f"{sub_icon}{dm_icon} @{r['username']}")
    if total > 5:
        lines.append(f"...and {total - 5} more.")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ── /dmnotify (admin+) ────────────────────────────────────────────────────────

async def handle_dmnotify(bot: BaseBot, user: User, args: list[str]) -> None:
    """/dmnotify <username> <message> — send a DM to a specific subscriber."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /dmnotify <username> <message>")
        return
    target_name = args[1].lstrip("@").lower().strip()
    message = " ".join(args[2:])[:200]
    sub = db.get_subscriber(target_name)
    if not sub or not sub.get("conversation_id"):
        await _w(bot, user.id,
                 f"No DM conversation for @{target_name}. Ask them to DM the bot 'subscribe'.")
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
        await _w(bot, user.id, f"❌ DM to @{target_name} failed. Channel may be unavailable.")


# ── /announce_subs (admin+) ───────────────────────────────────────────────────

async def handle_announce_subs(bot: BaseBot, user: User, args: list[str]) -> None:
    """/announce_subs <message> — DM all active subscribers (rate-limited)."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /announce_subs <message>")
        return
    message = " ".join(args[1:])[:200]
    subscribers = db.get_all_subscribed_with_dm()
    if not subscribers:
        await _w(bot, user.id, "No active subscribers with DM connections.")
        return
    await _w(bot, user.id, f"📤 Sending to {len(subscribers)} subscriber(s)...")
    sent = 0
    failed = 0
    for sub in subscribers:
        ok = await send_dm(bot, sub["conversation_id"], message)
        if ok:
            sent += 1
            db.set_subscriber_last_dm(sub["username"])
        else:
            failed += 1
            db.set_dm_available(sub["username"], False)
        # Rate limit: 1 DM per second
        await asyncio.sleep(1)
    await _w(bot, user.id, f"✅ Sent to {sent} subscriber(s). Failed: {failed}.")
    print(f"[SUBS] announce_subs by @{user.username}: {sent} sent, {failed} failed.")
