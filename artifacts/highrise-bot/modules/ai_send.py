"""
modules/ai_send.py — Shared AI reply helper (no circular imports).

Both ai_brain.py (outer orchestrator) and ai_openai_brain.py (inner pipeline)
import from here so both respect the same reply-mode logic without circularity.

Public API:
    ai_send(bot, user, message, response_type, knowledge_level, contains_private)
        — Routes public or whisper based on current reply mode + safety overrides.
    ai_whisper(bot, uid, message)
        — Always-whisper for private/sensitive data. Ignores reply mode entirely.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot, User

from modules.ai_reply_mode import choose_reply_channel, get_reply_mode


async def ai_send(
    bot:              "BaseBot",
    user:             "User",
    message:          str,
    response_type:    str  = "general",
    knowledge_level:  str  = "public",
    contains_private: bool = False,
) -> None:
    """
    Route the reply through public chat or whisper depending on the current
    AI reply mode, with safety overrides for sensitive content.
    Falls back to whisper if public chat raises an exception.
    """
    channel = choose_reply_channel(response_type, knowledge_level, contains_private)
    print(
        f"[AI REPLY] mode={get_reply_mode()} channel={channel} "
        f"type={response_type} private={contains_private}"
    )
    if channel == "public":
        try:
            await bot.highrise.chat(message[:249])
            return
        except Exception as exc:
            print(f"[AI REPLY] public_failed={exc!r}")
    try:
        await bot.highrise.send_whisper(user.id, message[:249])
    except Exception as exc:
        print(f"[AI REPLY] whisper_failed={exc!r}")


async def ai_whisper(bot: "BaseBot", uid: str, message: str) -> None:
    """
    Always-whisper for private, sensitive, or system data.
    Ignores reply mode entirely — use for confirmations, billing errors,
    permission denials, balance info, and anything personally identifiable.
    """
    try:
        await bot.highrise.send_whisper(uid, message[:249])
    except Exception:
        pass
