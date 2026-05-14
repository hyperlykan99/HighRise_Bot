"""
modules/ai_assistant_core.py — ChillTopiaMC AI entry point (3.3B thin rewrite).

Provides handle_acesinatra() imported by main.py.
All orchestration logic lives in ai_brain.py.

Trigger rules:
- "ai" must be the FIRST word — "said", "paid", "rain" do NOT trigger.
- Never intercepts !/slash commands.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot, User

from modules.ai_brain import is_ai_trigger, handle_ai_message


async def handle_acesinatra(
    bot:     "BaseBot",
    user:    "User",
    message: str,
) -> bool:
    """
    ChillTopiaMC AI assistant handler (name kept for registry compatibility).
    Called from on_chat().  Returns True when the message is claimed.
    """
    if not is_ai_trigger(message):
        return False

    # Never intercept slash or bot commands
    if message.strip().startswith("!") or message.strip().startswith("/"):
        return False

    return await handle_ai_message(bot, user, message)
