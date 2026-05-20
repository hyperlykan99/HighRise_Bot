"""Shared LIVE_BOTS registry.

Process-local map of running bot instances keyed by any known alias
(username, bot_name, name, mode).  In multi-bot mode each bot runs in its
OWN subprocess, so this dict only contains bots that share THIS subprocess.
For cross-process targeting the caller should fall back to a channel
broadcast.  `!livebots` exposes the current contents for debugging.
"""

from __future__ import annotations

LIVE_BOTS: dict[str, object] = {}


def _collect_keys(bot) -> set[str]:
    keys: set[str] = set()
    for attr in ("username", "bot_name", "name", "mode", "bot_mode"):
        val = getattr(bot, attr, None)
        if val:
            keys.add(str(val).strip().lower().lstrip("@"))
    # Config-level identity (BOT_MODE / BOT_USERNAME)
    try:
        from config import BOT_MODE, BOT_USERNAME
        if BOT_MODE:
            keys.add(BOT_MODE.strip().lower())
        if BOT_USERNAME:
            keys.add(BOT_USERNAME.strip().lower().lstrip("@"))
    except Exception:
        pass
    # Runtime-resolved username (gold module caches the in-room username)
    try:
        from modules.gold import get_bot_username
        gu = get_bot_username()
        if gu:
            keys.add(gu.strip().lower().lstrip("@"))
    except Exception:
        pass
    return {k for k in keys if k}


def register_bot(bot) -> list[str]:
    """Register `bot` under every known alias.  Returns the keys used."""
    keys = _collect_keys(bot)
    for k in keys:
        LIVE_BOTS[k] = bot
    sorted_keys = sorted(keys)
    print(f"[LIVE_BOTS] registered {sorted_keys}")
    return sorted_keys


def get_live_bot(name: str):
    """Look up a bot by any alias.  Returns None if not in this process."""
    if not name:
        return None
    return LIVE_BOTS.get(name.strip().lower().lstrip("@"))


def live_bot_keys() -> list[str]:
    return sorted(LIVE_BOTS.keys())
