"""
modules/ai_intent_router.py — Keyword-based intent detection (3.3A rebuild).

Intent detection is purely keyword/regex based — no external APIs.
Rules are evaluated top-to-bottom; first match wins.
"""
from __future__ import annotations

import re

INTENT_DATE_TIME        = "date_time_question"
INTENT_HOLIDAY          = "holiday_question"
INTENT_PLAYER_GUIDANCE  = "player_guidance"
INTENT_CMD_EXPLAIN      = "command_explanation"
INTENT_LUXE             = "luxe_explanation"
INTENT_MINING           = "mining_explanation"
INTENT_FISHING          = "fishing_explanation"
INTENT_CASINO           = "casino_explanation"
INTENT_EVENT            = "event_explanation"
INTENT_BUG              = "bug_report"
INTENT_FEEDBACK         = "feedback_report"
INTENT_SUMMARIZE_BUGS   = "summarize_bugs"
INTENT_MOD_HELP         = "moderation_help"
INTENT_PREPARE_SETTING  = "prepare_setting_change"
INTENT_CONFIRM_SETTING  = "confirm_setting_change"
INTENT_CANCEL_SETTING   = "cancel_setting_change"
INTENT_GENERAL          = "general_question"
INTENT_UNKNOWN          = "unknown"

_RULES: list[tuple[str, re.Pattern]] = [
    # Confirm / cancel (check first — exact match)
    (INTENT_CANCEL_SETTING,
     re.compile(r"^cancel\b", re.I)),
    (INTENT_CONFIRM_SETTING,
     re.compile(r"^confirm\b", re.I)),

    # Holidays (check before date_time — more specific)
    (INTENT_HOLIDAY,
     re.compile(r"\bholiday(s)?\b", re.I)),

    # Date / time
    (INTENT_DATE_TIME,
     re.compile(
         r"\b(what.{0,8}(date|time|day)|today|right\s+now|current\s+time"
         r"|day\s+of\s+week|what\s+day|clock|hour|minute)\b",
         re.I,
     )),

    # Player guidance / what to do
    (INTENT_PLAYER_GUIDANCE,
     re.compile(
         r"\b(what\s+(should|can)\s+i\s+do"
         r"|what\s+to\s+do"
         r"|help\s+me\s+(start|begin|play|get\s+started)"
         r"|next\s+step"
         r"|what\s+now"
         r"|guide\s+me"
         r"|i\s+don.{0,3}t\s+know\s+what\s+to\s+do"
         r"|where\s+do\s+i\s+start"
         r"|how\s+do\s+i\s+start)\b",
         re.I,
     )),

    # Bug summary (staff) — check before generic bug
    (INTENT_SUMMARIZE_BUGS,
     re.compile(
         r"\b(summarize|summary|list|show\s+me)\s+(open\s+)?"
         r"(bugs?|issues?|reports?|errors?)\b",
         re.I,
     )),

    # Bug report
    (INTENT_BUG,
     re.compile(
         r"\b(bug|broken|not\s+working|glitch|crash|error|issue"
         r"|stuck|freezing|lagging|broken)\b",
         re.I,
     )),

    # Feedback / suggestion
    (INTENT_FEEDBACK,
     re.compile(
         r"\b(feedback|suggestion|suggest|idea|improvement|request|feature\s+request)\b",
         re.I,
     )),

    # Moderation help
    (INTENT_MOD_HELP,
     re.compile(
         r"\b(spam|spammer|harass|bully|banning|muting|kick|warn"
         r"|handle.*player|troublesome|disruptive|report.*user"
         r"|player.*problem|someone\s+(is\s+)?(being|causing))\b",
         re.I,
     )),

    # Prepared setting change
    (INTENT_PREPARE_SETTING,
     re.compile(
         r"\b(set|change|update|adjust)\s+\w+.{0,20}(to|price|value|amount|rate)\b",
         re.I,
     )),

    # Topic: Luxe Tickets
    (INTENT_LUXE,
     re.compile(r"\b(luxe\s*ticket|luxe\s*shop|luxe|🎫|premium\s*ticket)\b", re.I)),

    # Topic: Mining
    (INTENT_MINING,
     re.compile(r"\b(mine|mining|ore|pickaxe|dig|automine|mineinv|minehelp)\b", re.I)),

    # Topic: Fishing
    (INTENT_FISHING,
     re.compile(r"\b(fish|fishing|catch|rod|bait|angl|autofish|fishinv|fishhelp)\b", re.I)),

    # Topic: Casino
    (INTENT_CASINO,
     re.compile(r"\b(casino|blackjack|poker|bet\s+\d|card\s+game|gamble|bj\b)\b", re.I)),

    # Topic: Events
    (INTENT_EVENT,
     re.compile(r"\b(event|events|limited.time|bonus\s+event|active\s+event)\b", re.I)),

    # Command explanation
    (INTENT_CMD_EXPLAIN,
     re.compile(
         r"\b(explain|what\s+is|what\s+does|how\s+(does|do|to\s+use)\s+)[!/]?\w+\b",
         re.I,
     )),

    # General question fallback
    (INTENT_GENERAL,
     re.compile(r"\b(what|how|why|when|where|can\s+i|should\s+i|help)\b", re.I)),
]


def detect_intent(text: str) -> str:
    """Return the best-matching intent for the given (trigger-stripped) text."""
    low = text.strip().lower()
    for intent, pattern in _RULES:
        if pattern.search(low):
            return intent
    return INTENT_UNKNOWN
