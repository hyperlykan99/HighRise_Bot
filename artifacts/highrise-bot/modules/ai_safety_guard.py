"""
modules/ai_safety_guard.py — Consolidated local pre-OpenAI safety guard (OpenAI-First Brain).

Runs BEFORE any OpenAI call to block clearly unsafe requests instantly,
saving API quota and preventing prompt injection.

Returns (blocked: bool, message: str).  message is "" when not blocked.
"""
from __future__ import annotations

import re

# ── NEVER patterns — always blocked, no OpenAI call ──────────────────────────
_NEVER: list[tuple[re.Pattern, str]] = [
    # Data wipe / delete
    (re.compile(
        r"\b(wipe|delete|drop|truncate)\s+(all\s+)?"
        r"(data|player|users?|profiles?|tables?|database|db)\b", re.I,
    ), "I can't wipe or delete player data. That action is always blocked."),

    # Economy reset
    (re.compile(
        r"\b(reset\s+(economy|all\s+coins?|all\s+balances?|everything)|mass\s+reset)\b", re.I,
    ), "I can't reset the economy through AI. That action is always blocked."),

    # Reveal secrets / tokens / env
    (re.compile(
        r"\b(reveal|show|display|dump|print|expose|get)\s+(the\s+)?"
        r"(database|db|sql|api[\s_]?key|bot[\s_]?token|token|secret|"
        r"env|environment[\s_]?var|password|passkey)\b", re.I,
    ), "I can't show secrets, tokens, or environment variables."),

    # Privilege escalation
    (re.compile(
        r"\b(make\s+me|set\s+me|grant\s+me|give\s+me)\s+(owner|admin|root|super)\b", re.I,
    ), "I can't change your permission level through AI."),

    # Permission bypass
    (re.compile(
        r"\b(bypass|override|skip)\s+(permissions?|perm|auth|security|lock)\b", re.I,
    ), "I can't bypass security or permissions."),

    # Direct SQL
    (re.compile(
        r"\b(execute|run|inject)\s+(sql|query|select|insert|update|delete)\b", re.I,
    ), "I can't run direct database queries through AI."),

    # Mass ban
    (re.compile(
        r"\b(mass|bulk)\s+(ban|kick|mute|remove)\b", re.I,
    ), "I can't run mass moderation actions through AI."),

    # Unlimited currency
    (re.compile(
        r"\bgive\s+(me\s+)?(unlimited|infinite|999\d{3,}|\d{6,})\s+(coins?|tokens?|tickets?)\b",
        re.I,
    ), "I can't grant unlimited currency through AI."),

    # Secretly change odds
    (re.compile(
        r"\b(secretly|quietly|silently)\s+(change|set|alter)\s+(odds?|casino|rng)\b", re.I,
    ), "I can't secretly change casino odds through AI."),

    # Exploit / hack
    (re.compile(
        r"\b(hack|exploit|cheat|inject|overflow)\b", re.I,
    ), "I can't run exploits or hacks through AI."),

    # Ignore rules / jailbreak
    (re.compile(
        r"\b(ignore|forget|override|disregard)\s+(your\s+)?"
        r"(rules?|instructions?|guidelines?|system\s+prompt|restrictions?|limits?)\b", re.I,
    ), "I can't ignore my safety rules or guidelines."),

    (re.compile(
        r"\b(pretend|act\s+as\s+if|act\s+like)\s+(you\s+)?"
        r"(have\s+no\s+rules?|have\s+no\s+restrictions?|are\s+unrestricted|can\s+do\s+anything)\b",
        re.I,
    ), "I can't pretend to have no rules. My safety guidelines always apply."),

    (re.compile(r"\bjailbreak\b", re.I),
     "I can't bypass my safety guidelines through AI."),

    # Prompt injection markers
    (re.compile(
        r"(ignore\s+previous\s+instructions?|new\s+system\s+prompt|you\s+are\s+now\s+a"
        r"|disregard\s+all\s+prior|forget\s+everything\s+above)", re.I,
    ), "I can't process that request due to safety filters."),
]


def safety_check(text: str) -> tuple[bool, str]:
    """
    Run all NEVER patterns against text.
    Returns (True, refusal_message) if blocked, (False, "") if safe.
    """
    for pattern, msg in _NEVER:
        if pattern.search(text):
            print(f"[AI SAFETY] blocked pattern={pattern.pattern[:60]!r}")
            return True, msg
    return False, ""
