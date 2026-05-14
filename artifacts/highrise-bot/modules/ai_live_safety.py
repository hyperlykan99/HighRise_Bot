"""
modules/ai_live_safety.py — Safety filter for live internet queries (3.3D).

Blocks queries that attempt to:
  - Expose secrets, tokens, API keys, database content
  - Aid hacking, exploiting, account theft, bypassing moderation
  - Solicit illegal activity
"""
from __future__ import annotations

import re

_BLOCKED = re.compile(
    r"\b(bot\s+token|api\s+key|database\s+password|db\s+pass|secret\s+key"
    r"|access\s+token|private\s+key|auth\s+token|bearer\s+token"
    r"|\bexploit\b.{0,30}(bot|highrise|roblox|game|account|server|system)"
    r"|(highrise|roblox|game|discord|minecraft)\s+(exploit|hack|cheat|glitch)"
    r"|latest\s+exploit|new\s+exploit|find\s+exploit|exploit\s+for\b"
    r"|how\s+to\s+hack|hack\s+(account|roblox|highrise|discord|game)"
    r"|bypass\s+(ban|mod|moderation|auth|security|captcha)"
    r"|steal\s+(account|password|coins?|tickets?)"
    r"|leak\s+private|expose\s+private|find\s+(password|token|secret)"
    r"|sql\s+injection|xss\s+attack|ddos|denial.of.service"
    r"|cheat\s+(engine|code|coins?)|dupe\s+(glitch|bug)"
    r"|private\s+(data|database|info)\s+of\s+\w+"
    r"|internal\s+(config|settings|env|environment\s+variables?))\b",
    re.I,
)

_BLOCKED_REPLIES: dict[str, str] = {
    "token":    "🚫 I can't help with tokens or API keys.",
    "password": "🚫 I can't retrieve passwords or secrets.",
    "exploit":  "🚫 I can't help with exploits.",
    "hack":     "🚫 I can't help with hacking.",
    "bypass":   "🚫 I can't help with bypassing security.",
    "steal":    "🚫 I can't help with stealing accounts or items.",
    "leak":     "🚫 I can't help with leaking private data.",
    "cheat":    "🚫 I can't help with cheating or duping.",
    "inject":   "🚫 I can't help with security attacks.",
    "ddos":     "🚫 I can't help with attacks.",
}


def is_blocked_live_query(text: str) -> str | None:
    """Return a refusal string if blocked, else None."""
    m = _BLOCKED.search(text)
    if not m:
        return None
    word = m.group(0).lower()
    for key, reply in _BLOCKED_REPLIES.items():
        if key in word:
            return reply
    return "🚫 I can't help with that kind of request."
