"""
economy.py
----------
Shared coin-economy module for ALL bot modes.

Any future bot (GameBot, DJBot, BlackjackBot, HostBot) should import
the functions it needs from here so player balances and daily rewards
are consistent across bots.

User-facing commands handled here:
  /balance      — whisper the player's current coin balance
  /daily        — claim 50 free coins once every 24 hours
  /leaderboard  — whisper the top 10 richest players

Admin coin commands (/addcoins, /removecoins) live in admin.py so that
economy.py stays purely about the player-facing experience.
"""

from highrise import BaseBot, User
import database as db
import config


async def handle_balance(bot: BaseBot, user: User):
    """Whisper the player's current coin balance privately."""
    db.ensure_user(user.id, user.username)
    balance = db.get_balance(user.id)
    await bot.highrise.send_whisper(user.id, f"Your balance: {balance} coins.")


async def handle_daily(bot: BaseBot, user: User):
    """
    Give the player their daily coin reward.
    Claimable once per calendar day — resets at midnight UTC.
    """
    db.ensure_user(user.id, user.username)

    if not db.can_claim_daily(user.id):
        await bot.highrise.send_whisper(
            user.id,
            "You already claimed your daily coins today! Come back tomorrow."
        )
        return

    db.adjust_balance(user.id, config.DAILY_REWARD)
    db.record_daily_claim(user.id)
    new_balance = db.get_balance(user.id)

    await bot.highrise.send_whisper(
        user.id,
        f"You claimed {config.DAILY_REWARD} daily coins! New balance: {new_balance} coins."
    )


async def handle_leaderboard(bot: BaseBot, user: User):
    """Whisper the top players sorted by coin balance."""
    top = db.get_leaderboard(config.LEADERBOARD_SIZE)

    if not top:
        await bot.highrise.send_whisper(user.id, "No players on the leaderboard yet!")
        return

    lines = [f"-- Top {len(top)} Players --"]
    for entry in top:
        lines.append(f"  #{entry['rank']}  {entry['username']}  —  {entry['balance']} coins")

    await bot.highrise.send_whisper(user.id, "\n".join(lines))
