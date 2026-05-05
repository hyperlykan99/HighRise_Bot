"""
admin.py
--------
Admin commands shared across all bot modes.

Commands handled:
  /addcoins    <username> <amount>  — gift coins to any player (offline-safe)
  /removecoins <username> <amount>  — take coins from any player (offline-safe)
  /resetgame                        — clear any stuck active game
  /announce    <message>            — post a room-wide announcement

Username lookup rules (all commands):
  - Strips leading @, trims whitespace, case-insensitive.
  - If the username is not in the DB a placeholder record is auto-created
    (id = "offline_<username_lower>") and merged when the player next joins.
  - Player does NOT need to be in the room or have chatted this session.
"""

from highrise import BaseBot, User
import database as db
import games   # needed so /resetgame can clear active game state


async def handle_admin_command(bot: BaseBot, user: User, cmd: str, args: list[str]):
    """Dispatch an admin command to the correct private handler."""
    if cmd == "addcoins":
        await _cmd_addcoins(bot, user, args)

    elif cmd == "removecoins":
        await _cmd_removecoins(bot, user, args)

    elif cmd == "resetgame":
        await _cmd_resetgame(bot, user)

    elif cmd == "announce":
        await _cmd_announce(bot, user, args)


# ---------------------------------------------------------------------------
# Private handlers
# ---------------------------------------------------------------------------

async def _cmd_addcoins(bot: BaseBot, user: User, args: list[str]):
    """
    Admin gift: add coins to any player, online or offline.
    Usage: /addcoins <username> <amount>

    - Username is normalised (strips @, trimmed, case-insensitive lookup).
    - If not in DB, a placeholder record is created automatically.
    - Transaction is written to the ledger as reason 'admin_addcoins'.
    - Recipient is notified by whisper; if offline, the error is logged and
      the transfer still completes.
    """
    if len(args) < 3:
        await bot.highrise.send_whisper(user.id, "Usage: /addcoins <username> <amount>")
        return

    raw_name = args[1]
    raw_amt  = args[2]

    if not raw_amt.isdigit():
        await bot.highrise.send_whisper(user.id, "❌ Amount must be a whole number.")
        return

    target_username = raw_name.lstrip("@").strip()
    amount          = int(raw_amt)

    if not target_username:
        await bot.highrise.send_whisper(user.id, "❌ Invalid username.")
        return
    if amount <= 0:
        await bot.highrise.send_whisper(user.id, "❌ Amount must be greater than 0.")
        return

    # Resolve or create (offline placeholder if unknown)
    target = db.resolve_or_create_user(target_username)
    if target is None:
        await bot.highrise.send_whisper(user.id, "❌ Invalid username.")
        return

    bal_before = db.get_balance(target["user_id"])
    db.adjust_balance(target["user_id"], amount)
    bal_after = db.get_balance(target["user_id"])
    db.add_ledger_entry(
        target["user_id"], target["username"],
        amount, "admin_addcoins",
        related_user=user.username,
        balance_before=bal_before,
    )

    await bot.highrise.send_whisper(
        user.id,
        f"✅ Added {amount:,}c to @{target['username']}. Balance: {bal_after:,}c."
    )

    # Notify recipient (best-effort; offline is fine)
    try:
        await bot.highrise.send_whisper(
            target["user_id"],
            f"🎁 @{user.username} gave you {amount:,}c! Balance: {bal_after:,}c."
        )
    except Exception:
        print(f"[ADMIN] @{target['username']} offline; addcoins notification skipped.")


async def _cmd_removecoins(bot: BaseBot, user: User, args: list[str]):
    """
    Admin command: remove coins from a player (balance clamped to 0).
    Usage: /removecoins <username> <amount>

    - Username is normalised (strips @, trimmed, case-insensitive lookup).
    - If not in DB, a placeholder record is created automatically.
    - Actual deduction is logged to the ledger as 'admin_removecoins'.
    """
    if len(args) < 3:
        await bot.highrise.send_whisper(user.id, "Usage: /removecoins <username> <amount>")
        return

    raw_name = args[1]
    raw_amt  = args[2]

    if not raw_amt.isdigit():
        await bot.highrise.send_whisper(user.id, "❌ Amount must be a whole number.")
        return

    target_username = raw_name.lstrip("@").strip()
    amount          = int(raw_amt)

    if not target_username:
        await bot.highrise.send_whisper(user.id, "❌ Invalid username.")
        return
    if amount <= 0:
        await bot.highrise.send_whisper(user.id, "❌ Amount must be greater than 0.")
        return

    target = db.resolve_or_create_user(target_username)
    if target is None:
        await bot.highrise.send_whisper(user.id, "❌ Invalid username.")
        return

    bal_before      = db.get_balance(target["user_id"])
    db.adjust_balance(target["user_id"], -amount)   # MAX(0, balance - amount) via DB
    bal_after       = db.get_balance(target["user_id"])
    actually_removed = bal_before - bal_after       # reflects the clamp

    if actually_removed > 0:
        db.add_ledger_entry(
            target["user_id"], target["username"],
            -actually_removed, "admin_removecoins",
            related_user=user.username,
            balance_before=bal_before,
        )

    await bot.highrise.send_whisper(
        user.id,
        f"✅ Removed {actually_removed:,}c from @{target['username']}. "
        f"Balance: {bal_after:,}c."
    )


async def _cmd_resetgame(bot: BaseBot, user: User):
    """Clear all active mini-game state."""
    games.reset_all_games()
    await bot.highrise.chat("[Admin] All active games have been reset.")


async def _cmd_announce(bot: BaseBot, user: User, args: list[str]):
    """Post a public announcement. Usage: /announce <message>"""
    message_text = " ".join(args[1:]).strip()
    if not message_text:
        await bot.highrise.send_whisper(user.id, "Usage: /announce <message>")
        return
    await bot.highrise.chat(f"[Announcement] {message_text}")
