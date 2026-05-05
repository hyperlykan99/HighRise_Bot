"""
modules/economy.py
------------------
Token economy module.

Handles all token-related commands:
  /balance              - show your current token balance
  /daily                - claim free daily tokens

Admin commands:
  /addtokens <username> <amount>  - give tokens to a user
  /refund    <username> <amount>  - refund tokens to a user
"""

from highrise import BaseBot, User
import database as db
import config


async def handle_economy_command(bot: BaseBot, user: User, args: list[str]):
    """
    Route a token economy command to the correct handler.

    Parameters
    ----------
    bot  : running bot instance
    user : the Highrise user who typed the command
    args : words after the '/', e.g. ["balance"] or ["daily"]
    """
    if not args:
        return

    cmd = args[0].lower()

    if cmd == "balance":
        await _cmd_balance(bot, user)
    elif cmd == "daily":
        await _cmd_daily(bot, user)


async def handle_economy_admin_command(bot: BaseBot, user: User, args: list[str]):
    """
    Route an admin-only economy command.

    Parameters
    ----------
    bot  : running bot instance
    user : the admin user
    args : words after the '/', e.g. ["addtokens", "alice", "50"]
    """
    if not args:
        return

    cmd = args[0].lower()

    if cmd == "addtokens":
        await _cmd_addtokens(bot, user, args[1:])
    elif cmd == "refund":
        await _cmd_refund(bot, user, args[1:])


# ---------------------------------------------------------------------------
# User commands
# ---------------------------------------------------------------------------

async def _cmd_balance(bot: BaseBot, user: User):
    """Whisper the user's current token balance."""
    db.ensure_user(user.id, user.username)
    balance = db.get_balance(user.id)
    await bot.highrise.send_whisper(
        user.id,
        f"Your balance: {balance} tokens. Song requests cost {config.SONG_REQUEST_COST} tokens."
    )


async def _cmd_daily(bot: BaseBot, user: User):
    """
    Give the user their daily free tokens.
    Can only be claimed once per calendar day per user.
    """
    db.ensure_user(user.id, user.username)

    if not db.can_claim_daily(user.id):
        await bot.highrise.send_whisper(
            user.id,
            f"You already claimed your daily {config.DAILY_REWARD} tokens today! Come back tomorrow."
        )
        return

    db.adjust_balance(user.id, config.DAILY_REWARD)
    db.record_daily_claim(user.id)
    new_balance = db.get_balance(user.id)

    await bot.highrise.send_whisper(
        user.id,
        f"You claimed {config.DAILY_REWARD} free tokens! New balance: {new_balance}"
    )


# ---------------------------------------------------------------------------
# Admin commands
# ---------------------------------------------------------------------------

async def _cmd_addtokens(bot: BaseBot, user: User, args: list[str]):
    """
    Add tokens to another user's balance by username.
    Usage: /addtokens <username> <amount>
    The target user must have joined the room at least once (so they exist in the DB).
    """
    if len(args) < 2 or not args[1].isdigit():
        await bot.highrise.send_whisper(user.id, "Usage: /addtokens <username> <amount>")
        return

    target_username = args[0]
    amount          = int(args[1])

    if amount <= 0:
        await bot.highrise.send_whisper(user.id, "Amount must be a positive number.")
        return

    found = db.set_balance_by_username(target_username, amount)

    if not found:
        await bot.highrise.send_whisper(
            user.id,
            f"User '{target_username}' not found. They must have joined the room at least once."
        )
        return

    await bot.highrise.send_whisper(user.id, f"Added {amount} tokens to @{target_username}.")
    await bot.highrise.chat(f"@{target_username} received {amount} tokens from an admin!")


async def _cmd_refund(bot: BaseBot, user: User, args: list[str]):
    """
    Refund tokens to a user (identical to addtokens, but announced as a refund).
    Usage: /refund <username> <amount>
    """
    if len(args) < 2 or not args[1].isdigit():
        await bot.highrise.send_whisper(user.id, "Usage: /refund <username> <amount>")
        return

    target_username = args[0]
    amount          = int(args[1])

    if amount <= 0:
        await bot.highrise.send_whisper(user.id, "Amount must be a positive number.")
        return

    found = db.set_balance_by_username(target_username, amount)

    if not found:
        await bot.highrise.send_whisper(
            user.id, f"User '{target_username}' not found."
        )
        return

    await bot.highrise.send_whisper(user.id, f"Refunded {amount} tokens to @{target_username}.")
    await bot.highrise.chat(f"@{target_username} was refunded {amount} tokens.")
