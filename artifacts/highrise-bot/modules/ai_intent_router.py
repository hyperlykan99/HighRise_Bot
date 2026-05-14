"""
modules/ai_intent_router.py — Keyword-based intent detection (3.3A).

Intent detection is purely keyword/regex based — no external APIs.
Rules are evaluated top-to-bottom; first match wins.

Priority groups (top → bottom):
  1. Confirm / cancel
  2. Permission-gated ChillTopia (owner / admin / staff / player-private)
  3. Global time / holiday with location (before generic date / holiday)
  4. Generic date / holiday (Philippines default)
  5. ChillTopia topic intents (guidance, bugs, feedback, mod, setting, topics)
  6. Real-world: live-data | sensitive | translation | math | general
  7. Unknown fallback
"""
from __future__ import annotations

import re

# ── ChillTopia intents ───────────────────────────────────────────────────────
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

# ── 3.3B AI self-management intents ─────────────────────────────────────────
INTENT_AI_STATUS            = "ai_status_check"
INTENT_AI_DEBUG             = "ai_debug_summary"
INTENT_AI_REPLY_MODE_VIEW   = "ai_reply_mode_view"
INTENT_AI_REPLY_MODE_SET    = "ai_reply_mode_set"
INTENT_PERSONALIZED_GUIDANCE = "personalized_guidance"

# ── Real-world intents ───────────────────────────────────────────────────────
INTENT_RW_GENERAL           = "real_world_general_question"
INTENT_RW_GLOBAL            = "real_world_global_question"
INTENT_RW_DATETIME          = "real_world_date_time_question"
INTENT_RW_GLOBAL_TIME       = "real_world_global_time_question"
INTENT_RW_HOLIDAY           = "real_world_holiday_question"
INTENT_RW_GLOBAL_HOLIDAY    = "real_world_global_holiday_question"
INTENT_RW_CURRENT_INFO      = "real_world_current_info_question"
INTENT_RW_SENSITIVE         = "real_world_sensitive_question"
INTENT_RW_TRANSLATION       = "real_world_translation_question"
INTENT_RW_MATH              = "real_world_math_question"
INTENT_RW_UNKNOWN           = "real_world_unknown"

# Set of all real-world intents for easy routing in assistant_core
RW_INTENTS: frozenset[str] = frozenset({
    INTENT_RW_GENERAL, INTENT_RW_GLOBAL, INTENT_RW_DATETIME,
    INTENT_RW_GLOBAL_TIME, INTENT_RW_HOLIDAY, INTENT_RW_GLOBAL_HOLIDAY,
    INTENT_RW_CURRENT_INFO, INTENT_RW_SENSITIVE,
    INTENT_RW_TRANSLATION, INTENT_RW_MATH, INTENT_RW_UNKNOWN,
})

# ── Location keywords used in global time / holiday detection ────────────────
# (shared pattern so we don't repeat the long list)
_LOC_KW = (
    r"japan|tokyo|osaka"
    r"|korea|seoul"
    r"|singapore"
    r"|uk|united\s+kingdom|england|london"
    r"|france|paris"
    r"|germany|berlin"
    r"|spain|madrid"
    r"|italy|rome"
    r"|australia|sydney|melbourne"
    r"|new\s+zealand|auckland"
    r"|canada|toronto|vancouver"
    r"|new\s+york|nyc|california|los\s+angeles|chicago|texas|miami"
    r"|united\s+states|usa"
    r"|brazil|argentina|colombia|peru|chile|mexico"
    r"|russia|moscow|ukraine"
    r"|india|mumbai|delhi"
    r"|china|beijing|shanghai"
    r"|indonesia|jakarta|bali"
    r"|thailand|bangkok"
    r"|vietnam|hanoi"
    r"|malaysia|kuala\s+lumpur"
    r"|uae|dubai|saudi\s+arabia|riyadh"
    r"|egypt|cairo|nigeria|kenya|south\s+africa"
    r"|turkey|istanbul|iran|iraq|israel"
)

# ── Detection rules (top-to-bottom, first match wins) ────────────────────────
_RULES: list[tuple[str, re.Pattern]] = [

    # ── 1. Confirm / cancel ─────────────────────────────────────────────────
    (INTENT_CANCEL_SETTING,
     re.compile(r"^cancel\b", re.I)),
    (INTENT_CONFIRM_SETTING,
     re.compile(r"^confirm\b", re.I)),

    # ── 1b. AI self-management (status, debug, reply mode) ──────────────────
    (INTENT_AI_DEBUG,
     re.compile(
         r"\b(debug\s+summary|debug\s+info|ai\s+diagnostics"
         r"|module\s+status|ai\s+modules?|ai\s+errors?)\b",
         re.I,
     )),
    (INTENT_AI_REPLY_MODE_SET,
     re.compile(
         r"\b(set\s+(ai\s+)?reply\s+mode\s+to\b"
         r"|make\s+ai\s+(public|whisper|smart)\b"
         r"|ai\s+(reply\s+mode|replies)\s+(to\s+)?(public|whisper|smart)\b"
         r"|switch\s+(ai\s+)?(to\s+)?(public|whisper|smart)\s+(mode\b|reply\b))\b",
         re.I,
     )),
    (INTENT_AI_REPLY_MODE_VIEW,
     re.compile(
         r"\b(reply\s+mode\b"
         r"|is\s+ai\s+(public|whisper|private|smart)\b"
         r"|ai\s+(public|whisper|private)\s+or\b"
         r"|what\s+is\s+(the\s+)?reply\s+mode\b)\b",
         re.I,
     )),
    (INTENT_AI_STATUS,
     re.compile(
         r"^status$"
         r"|\bai\s+status\b"
         r"|\b(are\s+you\s+online|is\s+ai\s+online|are\s+you\s+there"
         r"|are\s+you\s+alive|are\s+you\s+working|is\s+the\s+ai\s+on)\b",
         re.I,
     )),

    # ── 2. Permission-gated ChillTopia ──────────────────────────────────────
    (INTENT_OWNER_INFO,
     re.compile(
         r"\b(economy\s+(dashboard|health|summary|logs?)"
         r"|show\s+(economy|database|db|analytics|env|environment)"
         r"|economy\s+analytics"
         r"|owner\s+(summary|report|dashboard))\b",
         re.I,
     )),
    (INTENT_ADMIN_INFO,
     re.compile(
         r"\b(show\s+(event|shop|vip|assistant)\s+settings?"
         r"|event\s+settings?"
         r"|shop\s+settings?"
         r"|admin\s+(config|settings?|info|panel))\b",
         re.I,
     )),
    (INTENT_STAFF_INFO,
     re.compile(
         r"\b(show\s+(reports?|warnings?|support\s+queue)"
         r"|recent\s+warnings?"
         r"|support\s+queue"
         r"|staff\s+(info|data|panel))\b",
         re.I,
     )),
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

    # ── 3. Global time / holiday WITH a foreign location ────────────────────
    # Must come BEFORE generic date/holiday rules so location beats default.
    (INTENT_RW_GLOBAL_TIME,
     re.compile(
         rf"\b(time|date|day|what\s+day|what\s+time|clock|hour)\b.{{0,40}}\b({_LOC_KW})\b"
         rf"|\b({_LOC_KW})\b.{{0,40}}\b(time|date|day|what\s+time|clock)\b",
         re.I,
     )),
    (INTENT_RW_GLOBAL_HOLIDAY,
     re.compile(
         rf"\b(holiday|public\s+holiday|national\s+holiday|when\s+is|next\s+holiday)\b"
         rf".{{0,60}}\b({_LOC_KW})\b"
         rf"|\b({_LOC_KW})\b.{{0,60}}\b(holiday|public\s+holiday)\b"
         rf"|\b(thanksgiving|independence\s+day|golden\s+week|chuseok|seollal"
         rf"|anzac|boxing\s+day|canada\s+day|australia\s+day|labor\s+day)\b",
         re.I,
     )),

    # ── 4. Generic date / time / holiday (Philippines default) ──────────────
    (INTENT_HOLIDAY,
     re.compile(r"\bholiday\b", re.I)),
    (INTENT_DATE_TIME,
     re.compile(
         r"\b(what.{0,8}(date|time|day)|today|right\s+now|current\s+time"
         r"|day\s+of\s+week|what\s+day|clock|hour|minute|what\s+time)\b",
         re.I,
     )),

    # ── 5. ChillTopia topic intents ─────────────────────────────────────────

    # Personalized guidance (needs real player data — always whispered)
    (INTENT_PERSONALIZED_GUIDANCE,
     re.compile(
         r"\b(what\s+can\s+i\s+afford\b"
         r"|can\s+i\s+afford\b"
         r"|summarize\s+(my\s+)?progress\b"
         r"|how\s+am\s+i\s+doing\b"
         r"|what\s+should\s+i\s+grind\b"
         r"|what\s+can\s+i\s+earn\b"
         r"|how\s+do\s+i\s+earn\s+more\b"
         r"|help\s+me\s+progress\b"
         r"|what\s+can\s+i\s+buy\b"
         r"|how\s+much\s+more\s+do\s+i\s+need\b"
         r"|my\s+progress\s+summary\b"
         r"|progress\s+report\b)\b",
         re.I,
     )),
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
    (INTENT_SUMMARIZE_BUGS,
     re.compile(
         r"\b(summarize|summary|list|show\s+me)\s+(open\s+)?"
         r"(bugs?|issues?|reports?|errors?)\b",
         re.I,
     )),
    (INTENT_BUG,
     re.compile(
         r"\b(bug|broken|not\s+working|glitch|crash|error|issue"
         r"|stuck|freezing|lagging|doesnt\s+work|doesn.t\s+work)\b",
         re.I,
     )),
    (INTENT_FEEDBACK,
     re.compile(
         r"\b(feedback|suggestion|suggest|idea|improvement"
         r"|request|feature\s+request)\b",
         re.I,
     )),
    (INTENT_MOD_HELP,
     re.compile(
         r"\b(spam|spammer|harass|bully|handle.*player|troublesome"
         r"|disruptive|report.*user|player.*problem"
         r"|someone\s+(is\s+)?(being|causing)|how\s+should\s+i\s+handle)\b",
         re.I,
     )),
    (INTENT_PREPARE_SETTING,
     re.compile(
         r"\b(set|change|update|adjust)\s+\w+.{0,30}(to|price|value|amount|rate)\b",
         re.I,
     )),
    (INTENT_VIP,
     re.compile(r"\b(vip|vip\s+(perks?|access|benefits?|price|cost|buy))\b", re.I)),
    (INTENT_LUXE,
     re.compile(r"\b(luxe\s*ticket|luxe\s*shop|luxe|🎫|premium\s*ticket)\b", re.I)),
    (INTENT_CHILLCOINS,
     re.compile(r"\b(chillcoin|chill\s*coin|coins?\s+(work|earn|get)|earn\s+coins?)\b", re.I)),
    (INTENT_MINING,
     re.compile(r"\b(mine|mining|ore|pickaxe|dig|automine|mineinv|minehelp|how\s+to\s+mine)\b", re.I)),
    (INTENT_FISHING,
     re.compile(r"\b(fish|fishing|catch|rod|bait|angl|autofish|fishinv|fishhelp|how\s+to\s+fish)\b", re.I)),
    (INTENT_CASINO,
     re.compile(r"\b(casino|blackjack|how\s+to\s+(play\s+)?(bj|blackjack))\b", re.I)),
    (INTENT_EVENT,
     re.compile(r"\b(event|events|limited.time|bonus\s+event|active\s+event|what\s+events)\b", re.I)),
    (INTENT_CMD_EXPLAIN,
     re.compile(
         r"\b(explain|what\s+is|what\s+does|how\s+(does|do|to\s+use)\s+)[!/]?\w+\b",
         re.I,
     )),

    # ── 6. Real-world intents ───────────────────────────────────────────────

    # Live / current data (needs internet)
    (INTENT_RW_CURRENT_INFO,
     re.compile(
         r"\b(weather|forecast|temperature\s+in"
         r"|latest\s+news|breaking\s+news"
         r"|live\s+score|who\s+won\s+the|current\s+score"
         r"|usd\s+(to|vs)|exchange\s+rate|forex"
         r"|bitcoin\s+price|crypto\s+price|stock\s+price"
         r"|promo\s+code|latest\s+update"
         r"|current\s+president|prime\s+minister\s+of"
         r"|flight\s+price|bus\s+schedule|train\s+schedule"
         r"|cinema\s+schedule|lotto\s+result)\b",
         re.I,
     )),

    # Sensitive / emergency (medical, legal, financial, mental health)
    (INTENT_RW_SENSITIVE,
     re.compile(
         r"\b(chest\s+pain|heart\s+attack|can.?t\s+breathe|overdose|poison"
         r"|suicide|suicidal|self.?harm|bleeding\s+out"
         r"|medical\s+advice|diagnos|what\s+medicine"
         r"|legal\s+advice|is\s+it\s+legal\s+to"
         r"|immigration\s+advice|visa\s+(denial|rejection)"
         r"|tax\s+(advice|evasion)"
         r"|mental\s+health\s+crisis|am\s+i\s+depressed"
         r"|emergency|call\s+(911|112|995|999)|ambulance)\b",
         re.I,
     )),

    # Translation requests
    (INTENT_RW_TRANSLATION,
     re.compile(
         r"\b(translate\b|how\s+(do\s+you\s+say|to\s+say)\b"
         r"|\bin\s+(japanese|korean|spanish|french|german|chinese|arabic|tagalog)\b"
         r"|\bto\s+(japanese|korean|spanish|french|german|chinese|arabic|tagalog)\b)\b",
         re.I,
     )),

    # Math evaluation
    (INTENT_RW_MATH,
     re.compile(
         r"\b(calculate|compute|evaluate|what\s+is\s+\d|solve\s+\d"
         r"|\d+\s*[\+\-\*\/\^]\s*\d+)\b",
         re.I,
     )),

    # General real-world knowledge (science, geography, history, people, etc.)
    (INTENT_RW_GENERAL,
     re.compile(
         r"\b(what\s+is\s+(gravity|photosynthesis|evolution|inflation|gdp|dna"
         r"|climate\s+change|black\s+hole|the\s+internet|artificial\s+intelligence)"
         r"|what\s+is\s+the\s+(capital|speed\s+of\s+light|largest|smallest|most\s+populous"
         r"|longest|highest|tallest|circumference|population)"
         r"|capital\s+of\b|who\s+(is|was)\s+[A-Z]"
         r"|who\s+is\s+albert|who\s+is\s+isaac|who\s+is\s+nikola|who\s+is\s+marie"
         r"|explain\s+(gravity|photosynthesis|evolution|inflation|dna|climate|blackhole"
         r"|quantum|relativity|capitalism|democracy|physics|biology|chemistry)"
         r"|fun\s+fact|tell\s+me\s+a\s+fact|random\s+fact"
         r"|study\s+tip|how\s+to\s+study|how\s+to\s+be\s+productive"
         r"|how\s+many\s+(planet|language|continent|country|ocean)"
         r"|how\s+many\s+(countries|planets|continents|languages|oceans)"
         r"|largest\s+(country|ocean|continent)|smallest\s+country"
         r"|most\s+spoken\s+language|most\s+populous\s+country"
         r"|how\s+does\s+the\s+(internet|stock|economy|government|solar\s+system|body)\s+work"
         r"|what\s+causes\s+(rain|thunder|lightning|earthquake|volcano|inflation)"
         r"|give\s+me\s+(a\s+)?(study\s+tips?|fun\s+fact|advice|tip)"
         r"|who\s+invented\s+|who\s+discovered\s+"
         r"|difference\s+between\s+\w+\s+and\s+\w+"
         r"|define\s+\w+|what\s+does\s+\w+\s+mean"
         r"|meaning\s+of\s+\w+)\b",
         re.I,
     )),

    # Real-world general catch-all (after all ChillTopia rules)
    (INTENT_RW_GLOBAL,
     re.compile(
         r"\b(what\s+is|who\s+(is|was)|explain|tell\s+me\s+about|how\s+does)\b.{3,}",
         re.I,
     )),
]


def detect_intent(text: str) -> str:
    """Return the best-matching intent for the given (trigger-stripped) text."""
    low = text.strip().lower()
    for intent, pattern in _RULES:
        if pattern.search(low):
            return intent
    return INTENT_UNKNOWN
