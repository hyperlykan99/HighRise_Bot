"""
modules/ai_host_lock.py — Single-host AI responder lock (3.3B).

Only the bot account named ChillTopiaMC should process "ai" messages.
All other bots silently ignore AI triggers so there are no duplicate replies.
"""
from __future__ import annotations

AI_HOST_BOT_NAME: str = "ChillTopiaMC"


def is_ai_host_bot() -> bool:
    """Return True only if this running bot is the designated AI host."""
    try:
        from modules.multi_bot import get_bot_username
        username: str = get_bot_username() or ""
        return username.strip().lower() == AI_HOST_BOT_NAME.lower()
    except Exception:
        return False
