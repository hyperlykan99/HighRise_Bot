"""
modules/minigames.py
--------------------
PLACEHOLDER — Mini Games module.

Wire into bot.py the same way the DJ module is connected.
"""

from highrise import BaseBot, User


async def handle_minigame_command(bot: BaseBot, user: User, args: list[str]):
    """Entry point for all /game commands."""
    await bot.highrise.send_whisper(user.id, "🎮 Mini games coming soon!")
