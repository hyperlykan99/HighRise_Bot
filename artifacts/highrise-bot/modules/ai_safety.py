"""
modules/ai_safety.py — AceSinatra hard safety rules (3.3A rebuild).

Hard rules (never bypass):
- Never wipe/reset/delete player data
- Never grant ChillCoins or Luxe Tickets casually
- Never change casino odds silently
- Never change prices silently
- Never change economy rewards silently
- Never reveal bot tokens/API keys/secrets
"""
from __future__ import annotations
import re

_BLOCKED_RE = re.compile(
    r"\b("
    r"wipe\s+(all\s+)?(data|coins?|players?|economy|profiles?)"
    r"|delete\s+(all\s+)?(users?|data|players?|coins?|profiles?|tokens?)"
    r"|reset\s+all\s+(economy|coins?|data|players?)"
    r"|drop\s+table"
    r"|grant\s+(myself|everyone|all)\s+(coins?|tickets?|currency)"
    r"|give\s+(everyone|all\s+players|all)\s+(coins?|tickets?|currency|gold)"
    r"|add\s+coins?\s+(to\s+)?(all|everyone|myself)"
    r"|set\s+(my|everyone.?s)?\s*coins?\s+to"
    r"|change\s+(casino\s+)?odds"
    r"|change\s+prices?\s+silently"
    r"|mass\s+(ban|kick|mute)"
    r"|clear\s+(all\s+)?(data|profiles?|economy)"
    r"|api[_\s]?key|bot[_\s]?token|room[_\s]?id"
    r"|make\s+me\s+(owner|admin|mod|staff)"
    r"|bypass\s+(perm|security|protection|safety)"
    r"|sql\s+inject|sql\s+attack"
    r")\b",
    re.I,
)


def is_blocked(text: str) -> bool:
    """Return True when the message contains a hard-blocked pattern."""
    return bool(_BLOCKED_RE.search(text))


def blocked_response() -> str:
    return (
        "⛔ I can't do that — it affects protected data or economy safety.\n"
        "These actions require direct owner commands, not AI."
    )
