"""
modules/economy.py
------------------
Coin economy commands for the Mini Game Bot.

User commands:
  /balance      - whispers your current coin balance
  /daily        - claim 50 free coins (once every 24 hours)
  /leaderboard  - whispers the top 10 richest players

Admin commands:
  /addcoins <username> <amount>    - give coins to a player
  /removecoins <username> <amount> - take coins from a player
"""

from highrise import BaseBot, User
import database as db
import config


# ---------------------------------------------------------------------------
# User commands
# ---------------------------------------------------------------------------

async def handle_balance(bot: BaseBot, user: User):
    """Whisper the player's current coin balance to them privately."""
    db.ensure_user(user.id, user.username)
    balance = db.get_balance(user.id)
    await bot.highrise.send_whisper(user.id, f"Your balance: {balance} coins.")


async def handle_daily(bot: BaseBot, user: User):
    """
    Give the player their daily coin reward.
    Each player can only claim this once per calendar day (resets at midnight UTC).
    """
    db.ensure_user(user.id, user.username)

    # Check if they already claimed today
    if not db.can_claim_daily(user.id):
        await bot.highrise.send_whisper(
            user.id,
            "You already claimed your daily coins today! Come back tomorrow."
        )
        return

    # Award the coins and record the claim
    db.adjust_balance(user.id, config.DAILY_REWARD)
    db.record_daily_claim(user.id)
    new_balance = db.get_balance(user.id)

    await bot.highrise.send_whisper(
        user.id,
        f"You claimed {config.DAILY_REWARD} daily coins! New balance: {new_balance} coins."
    )


async def handle_leaderboard(bot: BaseBot, user: User):
    """Whisper the top players ranked by coin balance."""
    top = db.get_leaderboard(config.LEADERBOARD_SIZE)

    if not top:
        await bot.highrise.send_whisper(user.id, "No players on the leaderboard yet!")
        return

    lines = [f"-- Top {len(top)} Players --"]
    for entry in top:
        lines.append(f"  #{entry['rank']}  {entry['username']}  —  {entry['balance']} coins")

    await bot.highrise.send_whisper(user.id, "\n".join(lines))


# ---------------------------------------------------------------------------
# Admin commands
# ---------------------------------------------------------------------------

async def handle_addcoins(bot: BaseBot, user: User, args: list[str]):
    """
    Give coins to any registered player.
    Usage: /addcoins <username> <amount>
    """
    # args = ["addcoins", "playername", "100"]
    if len(args) < 3 or not args[2].isdigit():
        await bot.highrise.send_whisper(user.id, "Usage: /addcoins <username> <amount>")
        return

    target_username = args[1]
    amount          = int(args[2])

    if amount <= 0:
        await bot.highrise.send_whisper(user.id, "Amount must be greater than 0.")
        return

    # Find the player in the database by username
    target = db.get_user_by_username(target_username)
    if not target:
        await bot.highrise.send_whisper(
            user.id,
            f"Player '{target_username}' not found. They need to chat in the room first."
        )
        return

    db.adjust_balance(target["user_id"], amount)
    new_balance = db.get_balance(target["user_id"])

    await bot.highrise.send_whisper(
        user.id,
        f"Added {amount} coins to {target['username']}. Their balance is now {new_balance}."
    )


async def handle_removecoins(bot: BaseBot, user: User, args: list[str]):
    """
    Remove coins from any registered player.
    Balance is clamped to 0 — it will never go negative.
    Usage: /removecoins <username> <amount>
    """
    if len(args) < 3 or not args[2].isdigit():
        await bot.highrise.send_whisper(user.id, "Usage: /removecoins <username> <amount>")
        return

    target_username = args[1]
    amount          = int(args[2])

    if amount <= 0:
        await bot.highrise.send_whisper(user.id, "Amount must be greater than 0.")
        return

    target = db.get_user_by_username(target_username)
    if not target:
        await bot.highrise.send_whisper(
            user.id,
            f"Player '{target_username}' not found."
        )
        return

    db.adjust_balance(target["user_id"], -amount)
    new_balance = db.get_balance(target["user_id"])

    await bot.highrise.send_whisper(
        user.id,
        f"Removed {amount} coins from {target['username']}. Their balance is now {new_balance}."
    )
