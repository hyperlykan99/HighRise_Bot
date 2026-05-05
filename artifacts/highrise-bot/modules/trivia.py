"""
modules/trivia.py
-----------------
PLACEHOLDER — Trivia Bot module.

This file is ready for you to build out. The structure below shows
how to plug a new module into the existing command system.

To activate:
  1. Add trivia commands to the routing tables in bot.py
  2. Implement the handler functions below
"""

from highrise import BaseBot, User


async def handle_trivia_command(bot: BaseBot, user: User, args: list[str]):
    """Entry point for all /trivia commands."""
    await bot.highrise.send_whisper(user.id, "🧠 Trivia bot coming soon!")
