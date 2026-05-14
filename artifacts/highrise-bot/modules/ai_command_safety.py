"""
modules/ai_command_safety.py — Safety gate for AI Command Control Layer (3.3F).

NEVER_EXECUTE check runs before any command mapping or permission check.
Any text matching a NEVER pattern returns a safe refusal immediately.
"""
from __future__ import annotations

import re

# ── Patterns that are always blocked ─────────────────────────────────────────
_NEVER_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(
        r"\b(wipe|delete|drop|truncate)\s+(all\s+)?(data|player|users?|profiles?|tables?|database|db)\b",
        re.I,
    ), "I can't wipe or delete player data through AI. That action is always blocked."),

    (re.compile(
        r"\b(reset\s+(economy|all\s+coins?|all\s+balances?|everything)|mass\s+reset)\b",
        re.I,
    ), "I can't reset the economy through AI. That action is always blocked."),

    (re.compile(
        r"\b(reveal|show|display|dump|print|expose)\s+(the\s+)?"
        r"(database|db|sql|api\s+key|bot\s+token|token|secret|env|environment\s+var|password|passkey)\b",
        re.I,
    ), "I can't show secrets, tokens, or environment variables."),

    (re.compile(
        r"\b(show|print|display|get)\s+(bot|BOT|main)[\s_]?token\b",
        re.I,
    ), "I can't show secrets, tokens, or environment variables."),

    (re.compile(
        r"\b(make\s+me|set\s+me|grant\s+me|give\s+me)\s+(owner|admin|root|super)\b",
        re.I,
    ), "I can't change your permission level through AI. Use the normal command path."),

    (re.compile(
        r"\b(bypass|override|skip)\s+(permissions?|perm|auth|security|lock)\b",
        re.I,
    ), "I can't bypass permissions or security checks through AI."),

    (re.compile(
        r"\b(grant\s+admin|give\s+admin|make\s+admin)\b",
        re.I,
    ), "I can't grant admin roles through AI. Use the normal command path."),

    (re.compile(
        r"\b(direct\s+sql|raw\s+sql|execute\s+sql|run\s+sql|sql\s+inject)\b",
        re.I,
    ), "I can't run direct SQL commands through AI."),

    (re.compile(
        r"\b(mass\s+ban|ban\s+all|kick\s+all|mute\s+all|ban\s+everyone)\b",
        re.I,
    ), "I can't mass-moderate players through AI. That action is always blocked."),

    (re.compile(
        r"\b(change|set|alter)\s+(casino\s+)?odds?\b",
        re.I,
    ), "I can't silently change casino odds through AI."),

    (re.compile(
        r"\b(hack|exploit|cheat|inject|overflow)\b",
        re.I,
    ), "I can't run exploits or hacks through AI."),

    (re.compile(
        r"\bgive\s+(me\s+)?(unlimited|infinite|999\d{3,}|\d{6,})\s+(coins?|tokens?|tickets?)\b",
        re.I,
    ), "I can't casually grant unlimited currency. Economy edits require owner confirmation."),
]


def safety_check(text: str) -> str | None:
    """
    Return a safe refusal string if text matches any NEVER pattern, else None.
    """
    low = text.strip()
    for pattern, refusal in _NEVER_PATTERNS:
        if pattern.search(low):
            return refusal
    return None


def validate_coin_amount(amount_str: str, user_perm: str) -> tuple[bool, str]:
    """
    Validate a coin amount supplied in an AI command.
    Returns (ok, error_message).
    """
    from modules.ai_command_permissions import PERM_OWNER, has_permission
    try:
        val = int(amount_str.replace(",", "").strip())
    except ValueError:
        return False, f"Invalid amount '{amount_str}'. Please use a number."
    if val < 0:
        return False, "Amount cannot be negative."
    if val > 1_000_000 and not has_permission(user_perm, PERM_OWNER):
        return False, "Only owners can modify amounts above 1,000,000 coins through AI."
    if val > 10_000_000:
        return False, "Amount exceeds the maximum AI can process. Use the direct owner command."
    return True, ""
