"""
modules/ai_intent_router.py — Keyword-based intent detection (3.3A rebuild).

Intent detection is purely keyword/regex based — no external APIs.
Rules are evaluated top-to-bottom; first match wins.
"""
from __future__ import annotations

import re

# ── Intent constants ────────────────────────────────────────────────────────
INTENT_DATE_TIME            = "date_time_question"
INTENT_HOLIDAY              = "holiday_question"
INTENT_PLAYER_GUIDANCE      = "player_guidance"
INTENT_CMD_EXPLAIN          = "command_explanation"
INTENT_LUXE                 = "luxe_explanation"
INTENT_CHILLCOINS           = "chillcoins_explanation"
INTENT_MINING               = "mining_explanation"
INTENT_FISHING              = "fishing_explanation"
INTENT_CASINO               = "casino_explanation"
INTENT_EVENT                = "event_explanation"
INTENT_VIP                  = "vip_explanation"
INTENT_BUG                  = "bug_report"
INTENT_FEEDBACK             = "feedback_report"
INTENT_SUMMARIZE_BUGS       = "summarize_bugs"
INTENT_MOD_HELP             = "moderation_help"
INTENT_PREPARE_SETTING      = "prepare_setting_change"
INTENT_CONFIRM_SETTING      = "confirm_setting_change"
INTENT_CANCEL_SETTING       = "cancel_setting_change"
INTENT_PRIVATE_PLAYER_INFO  = "private_player_info_request"
INTENT_STAFF_INFO           = "staff_info_request"
INTENT_ADMIN_INFO           = "admin_info_request"
INTENT_OWNER_INFO           = "owner_info_request"
INTENT_DENIED_PERM          = "denied_permission"
INTENT_GENERAL              = "general_question"
INTENT_UNKNOWN              = "unknown"

# ── Detection rules (top-to-bottom, first match wins) ───────────────────────
_RULES: list[tuple[str, re.Pattern]] = [

    # Confirm / cancel (exact prefix — check first)
    (INTENT_CANCEL_SETTING,
     re.compile(r"^cancel\b", re.I)),
    (INTENT_CONFIRM_SETTING,
     re.compile(r"^confirm\b", re.I)),

    # Owner-only information requests
    (INTENT_OWNER_INFO,
     re.compile(
         r"\b(economy\s+(dashboard|health|summary|logs?)"
         r"|show\s+(economy|database|db|analytics|env|environment)"
         r"|economy\s+analytics"
         r"|owner\s+(summary|report|dashboard))\b",
         re.I,
     )),

    # Admin information requests
    (INTENT_ADMIN_INFO,
     re.compile(
         r"\b(show\s+(event|shop|vip|assistant)\s+settings?"
         r"|event\s+settings?"
         r"|shop\s+settings?"
         r"|admin\s+(config|settings?|info|panel))\b",
         re.I,
     )),

    # Staff information requests
    (INTENT_STAFF_INFO,
     re.compile(
         r"\b(show\s+(reports?|warnings?|support\s+queue)"
         r"|recent\s+warnings?"
         r"|support\s+queue"
         r"|staff\s+(info|data|panel))\b",
         re.I,
     )),

    # Private player info (own balance, level, progress, inventory)
    (INTENT_PRIVATE_PLAYER_INFO,
     re.compile(
         r"\b(how\s+many\s+(tickets?|coins?|luxe|chillcoins?)\s+(do\s+i|i|do|have)"
         r"|(my|my\s+own)\s+(balance|tickets?|coins?|luxe|level|progress|inventory|xp)"
         r"|how\s+(many|much)\s+(do\s+i\s+have|i\s+have)"
         r"|does\s+\w+\s+have\s+(tickets?|coins?|balance)"
         r"|\w+'s\s+(balance|tickets?|coins?|level)"
         r"|my\s+(stats?|status|info))\b",
         re.I,
     )),

    # Holidays (more specific — check before date_time)
    (INTENT_HOLIDAY,
     re.compile(r"\bholiday(s)?\b", re.I)),

    # Date / time
    (INTENT_DATE_TIME,
     re.compile(
         r"\b(what.{0,8}(date|time|day)|today|right\s+now|current\s+time"
         r"|day\s+of\s+week|what\s+day|clock|hour|minute|what\s+time)\b",
         re.I,
     )),

    # Player guidance / what to do next
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
         r"|how\s+do\s+i\s+start"
         r"|what\s+should\s+i\s+do\s+next)\b",
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
         r"|stuck|freezing|lagging|doesnt\s+work|doesn.t\s+work)\b",
         re.I,
     )),

    # Feedback / suggestion
    (INTENT_FEEDBACK,
     re.compile(
         r"\b(feedback|suggestion|suggest|idea|improvement"
         r"|request|feature\s+request)\b",
         re.I,
     )),

    # Moderation help
    (INTENT_MOD_HELP,
     re.compile(
         r"\b(spam|spammer|harass|bully|handle.*player|troublesome"
         r"|disruptive|report.*user|player.*problem"
         r"|someone\s+(is\s+)?(being|causing)|how\s+should\s+i\s+handle)\b",
         re.I,
     )),

    # Prepared setting change
    (INTENT_PREPARE_SETTING,
     re.compile(
         r"\b(set|change|update|adjust)\s+\w+.{0,30}(to|price|value|amount|rate)\b",
         re.I,
     )),

    # Topic: VIP
    (INTENT_VIP,
     re.compile(r"\b(vip|vip\s+(perks?|access|benefits?|price|cost|buy))\b", re.I)),

    # Topic: Luxe Tickets
    (INTENT_LUXE,
     re.compile(r"\b(luxe\s*ticket|luxe\s*shop|luxe|🎫|premium\s*ticket)\b", re.I)),

    # Topic: ChillCoins
    (INTENT_CHILLCOINS,
     re.compile(r"\b(chillcoin|chill\s*coin|coins?\s+(work|earn|get)|earn\s+coins?)\b", re.I)),

    # Topic: Mining
    (INTENT_MINING,
     re.compile(r"\b(mine|mining|ore|pickaxe|dig|automine|mineinv|minehelp|how\s+to\s+mine)\b", re.I)),

    # Topic: Fishing
    (INTENT_FISHING,
     re.compile(r"\b(fish|fishing|catch|rod|bait|angl|autofish|fishinv|fishhelp|how\s+to\s+fish)\b", re.I)),

    # Topic: Casino
    (INTENT_CASINO,
     re.compile(r"\b(casino|blackjack|poker|gamble|card\s+game|how\s+to\s+(play\s+)?(bj|poker|blackjack))\b", re.I)),

    # Topic: Events
    (INTENT_EVENT,
     re.compile(r"\b(event|events|limited.time|bonus\s+event|active\s+event|what\s+events)\b", re.I)),

    # Command explanation
    (INTENT_CMD_EXPLAIN,
     re.compile(
         r"\b(explain|what\s+is|what\s+does|how\s+(does|do|to\s+use)\s+)[!/]?\w+\b",
         re.I,
     )),

    # General question fallback
    (INTENT_GENERAL,
     re.compile(r"\b(what|how|why|when|where|can\s+i|should\s+i|help|tell\s+me)\b", re.I)),
]


def detect_intent(text: str) -> str:
    """Return the best-matching intent for the given (trigger-stripped) text."""
    low = text.strip().lower()
    for intent, pattern in _RULES:
        if pattern.search(low):
            return intent
    return INTENT_UNKNOWN
