"""
modules/pets.py
---------------
PLACEHOLDER — Pet Bot module.

Wire into bot.py the same way the DJ module is connected.
"""

from highrise import BaseBot, User


async def handle_pet_command(bot: BaseBot, user: User, args: list[str]):
    """Entry point for all /pet commands."""
    await bot.highrise.send_whisper(user.id, "🐾 Pet bot coming soon!")
