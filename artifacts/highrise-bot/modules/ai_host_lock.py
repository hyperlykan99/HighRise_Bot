"""
modules/ai_host_lock.py — Single-host AI responder lock (3.3B, fixed).

Only the bot account named ChillTopiaMC should process "ai" messages.

Root-cause fix: get_bot_username() lives in modules.gold (not multi_bot).
Fallback: ChillTopiaMC always runs as BOT_MODE="host" — use that when
the username isn't populated yet.
"""
from __future__ import annotations

AI_HOST_BOT_NAME: str = "ChillTopiaMC"
AI_HOST_BOT_MODE: str = "host"   # BOT_MODE env var for ChillTopiaMC


def is_ai_host_bot(debug: bool = False) -> bool:
    """
    Return True only if this running bot is the designated AI host.

    Checks (in order):
      1. Highrise account username from gold.get_bot_username()
      2. BOT_MODE == "host" (reliable fallback — ChillTopiaMC always = host)
    """
    username = ""
    mode     = ""

    # ── 1. Username from on_start identity (gold.py) ─────────────────────────
    try:
        from modules.gold import get_bot_username   # correct module
        username = get_bot_username() or ""
    except Exception as exc:
        if debug:
            print(f"[AI DEBUG] get_bot_username() failed: {exc!r}")

    # ── 2. BOT_MODE fallback ─────────────────────────────────────────────────
    try:
        from config import BOT_MODE
        mode = BOT_MODE or ""
    except Exception:
        pass

    by_name = username.strip().lower() == AI_HOST_BOT_NAME.lower()
    by_mode = mode.strip().lower() == AI_HOST_BOT_MODE.lower()
    result  = by_name or by_mode

    if debug:
        print(
            f"[AI DEBUG] current_bot_name={username!r}"
            f" bot_mode={mode!r}"
            f" is_host={result}"
        )

    return result
