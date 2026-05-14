"""
modules/ai_clarifier.py — Clarifying question generator (3.3B).

When a user request is too vague to route confidently, the AI asks
one short clarifying question instead of guessing wrong.
"""
from __future__ import annotations

import re

# (trigger pattern, clarifying question)
_CLARIFIERS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bstart\s+event\b", re.I),
     "Which event? Mining Rush, Fishing Frenzy, or Luxe Drop?"),
    (re.compile(r"\bchange\s+the\s+price\b", re.I),
     "Which price do you want to change? (VIP, shop item, etc.)"),
    (re.compile(r"\bmake\s+it\s+private\b", re.I),
     "Do you mean set AI reply mode to whisper?"),
    (re.compile(r"\bset\s+it\s+to\b", re.I),
     "Set what to which value? Please be more specific."),
    (re.compile(r"\btime\s+(there|in\s+there)\b", re.I),
     "Which country or city are you asking about?"),
    (re.compile(r"\btime\s+there\b|\bthere\s+time\b", re.I),
     "Which location do you mean?"),
    (re.compile(r"\bsend\s+announcement\b|\bmake\s+announcement\b", re.I),
     "What should the announcement say?"),
    (re.compile(r"\bchange\s+(the\s+)?setting\b", re.I),
     "Which setting do you want to change?"),
    (re.compile(r"\b(ban|mute|kick)\s+(them|that\s+player|him|her)\b", re.I),
     "Which player's username do you mean?"),
    (re.compile(r"\bupdate\s+it\b|\bchange\s+it\b|\bfix\s+it\b", re.I),
     "What exactly do you want to update or fix?"),
]

_GENERIC = "Could you be a bit more specific? I want to make sure I help correctly."


def get_clarification(text: str) -> str | None:
    """
    Return a clarifying question if the text is ambiguous, else None.
    The caller should only ask for clarification if intent confidence is low.
    """
    for pattern, question in _CLARIFIERS:
        if pattern.search(text):
            return question
    return None


def needs_clarification(text: str) -> bool:
    """True if text matches a known ambiguity pattern."""
    return get_clarification(text) is not None


def generic_clarification() -> str:
    return _GENERIC
