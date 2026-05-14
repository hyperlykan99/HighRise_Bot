"""
modules/ai_context_manager.py — Conversation context resolver (3.3B).

Wraps ai_memory_short_term to resolve ambiguous references and
topic-based context for follow-up questions.

Example:
  User: "ai explain Luxe Tickets"  → stores topic "Luxe Tickets"
  User: "ai how do I get more?"   → resolved to "how do I get more Luxe Tickets"
"""
from __future__ import annotations

import re

from modules.ai_memory_short_term import (
    get_context_hint,
    update_memory,
    clear_memory,
    memory_count,
)

# Short ambiguous words that signal a follow-up question
_AMBIGUOUS = re.compile(
    r"\b(it|them|more|that|those|this|these|there|one|ones|the\s+same)\b",
    re.I,
)

# Intents → topic labels stored in memory
INTENT_TOPIC_MAP: dict[str, str] = {
    "luxe_explanation":             "Luxe Tickets",
    "chillcoins_explanation":       "ChillCoins",
    "mining_explanation":           "mining",
    "fishing_explanation":          "fishing",
    "casino_explanation":           "casino/blackjack",
    "event_explanation":            "events",
    "vip_explanation":              "VIP",
    "player_guidance":              "ChillTopia activities",
    "personalized_guidance":        "personalized progress",
    "command_explanation":          "commands",
    "bug_report":                   "bug reports",
    "feedback_report":              "feedback/suggestions",
    "date_time_question":           "date and time",
    "holiday_question":             "holidays",
    "real_world_global_time_question":    "global time",
    "real_world_global_holiday_question": "holidays",
    "real_world_general_question":  "general knowledge",
    "real_world_math_question":     "math",
    "real_world_translation_question":    "translation",
    # 3.3E action intents stored for vague follow-up resolution
    "ai_reply_mode_set":                  "AI reply mode",
    "ai_reply_mode_view":                 "AI reply mode",
    "teleport_self":                      "teleport spots",
}


def resolve_context(user_id: str, clean: str) -> str:
    """
    If `clean` is short and ambiguous, append the last known topic
    so the intent router can match it correctly.

    "how do I get more?" + topic "Luxe Tickets"
    → "how do I get more? [Luxe Tickets]"
    """
    words = clean.split()
    if len(words) > 8:
        return clean  # Not ambiguous — long enough to stand alone

    if not _AMBIGUOUS.search(clean):
        return clean

    hint = get_context_hint(user_id)
    if hint:
        return f"{clean} [{hint}]"
    return clean


def record_interaction(user_id: str, intent: str, question: str) -> None:
    """Record an AI interaction into short-term memory."""
    topic = INTENT_TOPIC_MAP.get(intent, "")
    update_memory(user_id, intent, question, topic)


def clear_user_context(user_id: str) -> None:
    """Clear memory when a user leaves the room."""
    clear_memory(user_id)


def active_memory_count() -> int:
    return memory_count()
