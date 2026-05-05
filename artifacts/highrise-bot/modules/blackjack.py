"""
modules/blackjack.py
--------------------
PLACEHOLDER — Blackjack Bot module.

This file is ready for you to build out. Wire it into bot.py
the same way the DJ and economy modules are connected.

To activate:
  1. Add blackjack commands to the routing tables in bot.py
  2. Implement game logic below
"""

from highrise import BaseBot, User


async def handle_blackjack_command(bot: BaseBot, user: User, args: list[str]):
    """Entry point for all /blackjack commands."""
    await bot.highrise.send_whisper(user.id, "🃏 Blackjack bot coming soon!")
