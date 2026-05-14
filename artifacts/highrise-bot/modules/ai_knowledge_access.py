"""
modules/ai_knowledge_access.py — Permission-controlled knowledge access (3.3A).

Central dispatcher that:
1. Maps intent to required knowledge level
2. Checks the requesting user's permission
3. Routes to the correct context module
4. Returns a safe, permission-appropriate answer

Knowledge levels (ascending):
  PUBLIC          — anyone
  PLAYER_PRIVATE  — only the same player (or staff+)
  STAFF           — staff / admin / owner
  ADMIN           — admin / owner
  OWNER           — owner only
  NEVER_EXPOSE    — never, regardless of permission
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import User

from modules.ai_permissions import (
    requires_staff, requires_admin, requires_owner,
    PERM_OWNER,
)
from modules.ai_intent_router import (
    INTENT_DATE_TIME, INTENT_HOLIDAY, INTENT_PLAYER_GUIDANCE,
    INTENT_LUXE, INTENT_CHILLCOINS, INTENT_MINING, INTENT_FISHING,
    INTENT_CASINO, INTENT_EVENT, INTENT_VIP,
    INTENT_BUG, INTENT_FEEDBACK, INTENT_SUMMARIZE_BUGS,
    INTENT_MOD_HELP, INTENT_STAFF_INFO,
    INTENT_ADMIN_INFO, INTENT_PREPARE_SETTING,
    INTENT_OWNER_INFO,
    INTENT_PRIVATE_PLAYER_INFO,
    INTENT_CMD_EXPLAIN, INTENT_GENERAL, INTENT_UNKNOWN,
    INTENT_CONFIRM_SETTING, INTENT_CANCEL_SETTING,
    INTENT_DENIED_PERM,
)
from modules.ai_public_knowledge import get_public_answer, get_cmd_topic_answer
from modules.ai_player_context import get_player_own_info, deny_other_player_info
from modules.ai_staff_context import get_bug_summary, get_support_overview, get_warnings_summary
from modules.ai_admin_context import (
    get_event_settings_summary, get_vip_settings_summary,
    get_admin_panel_summary,
)
from modules.ai_owner_context import (
    get_economy_health_summary, get_analytics_summary, get_config_summary,
    is_never_expose_request, NEVER_EXPOSE_REPLY,
)

# ── Knowledge level constants ────────────────────────────────────────────────
KNOW_PUBLIC         = "public"
KNOW_PLAYER_PRIVATE = "player_private"
KNOW_STAFF          = "staff"
KNOW_ADMIN          = "admin"
KNOW_OWNER          = "owner"
KNOW_NEVER          = "never_expose"

# ── Intent → required knowledge level ───────────────────────────────────────
_INTENT_KNOW_MAP: dict[str, str] = {
    INTENT_DATE_TIME:           KNOW_PUBLIC,
    INTENT_HOLIDAY:             KNOW_PUBLIC,
    INTENT_PLAYER_GUIDANCE:     KNOW_PUBLIC,
    INTENT_LUXE:                KNOW_PUBLIC,
    INTENT_CHILLCOINS:          KNOW_PUBLIC,
    INTENT_MINING:              KNOW_PUBLIC,
    INTENT_FISHING:             KNOW_PUBLIC,
    INTENT_CASINO:              KNOW_PUBLIC,
    INTENT_EVENT:               KNOW_PUBLIC,
    INTENT_VIP:                 KNOW_PUBLIC,
    INTENT_CMD_EXPLAIN:         KNOW_PUBLIC,
    INTENT_GENERAL:             KNOW_PUBLIC,
    INTENT_BUG:                 KNOW_PUBLIC,
    INTENT_FEEDBACK:            KNOW_PUBLIC,
    INTENT_UNKNOWN:             KNOW_PUBLIC,
    INTENT_PRIVATE_PLAYER_INFO: KNOW_PLAYER_PRIVATE,
    INTENT_SUMMARIZE_BUGS:      KNOW_STAFF,
    INTENT_MOD_HELP:            KNOW_STAFF,
    INTENT_STAFF_INFO:          KNOW_STAFF,
    INTENT_ADMIN_INFO:          KNOW_ADMIN,
    INTENT_PREPARE_SETTING:     KNOW_ADMIN,
    INTENT_OWNER_INFO:          KNOW_OWNER,
}

_PERMISSION_DENY_MSG = {
    KNOW_STAFF: "🔒 That information is staff only.",
    KNOW_ADMIN: "🔒 Admin or owner only.",
    KNOW_OWNER: "🔒 That information is owner only.",
}


def check_access(
    intent: str,
    perm: str,
    text: str,
) -> str | None:
    """
    Return a denial message if the user lacks permission, or None if access is allowed.
    Also catches NEVER_EXPOSE patterns before any permission check.
    """
    if is_never_expose_request(text):
        return NEVER_EXPOSE_REPLY

    required = _INTENT_KNOW_MAP.get(intent, KNOW_PUBLIC)

    if required == KNOW_NEVER:
        return NEVER_EXPOSE_REPLY
    if required == KNOW_OWNER and not requires_owner(perm):
        return _PERMISSION_DENY_MSG[KNOW_OWNER]
    if required == KNOW_ADMIN and not requires_admin(perm):
        return _PERMISSION_DENY_MSG[KNOW_ADMIN]
    if required == KNOW_STAFF and not requires_staff(perm):
        return _PERMISSION_DENY_MSG[KNOW_STAFF]

    return None  # access granted


def get_knowledge_answer(
    user: "User",
    perm: str,
    intent: str,
    clean_text: str,
) -> str | None:
    """
    Return a knowledge answer for public/player-private/staff/admin/owner intents.
    Returns None for intents that should be handled by the action/confirmation flow.
    Returns a string answer otherwise.

    Does NOT handle: INTENT_CONFIRM_SETTING, INTENT_CANCEL_SETTING, INTENT_PREPARE_SETTING,
    INTENT_BUG, INTENT_FEEDBACK, INTENT_MOD_HELP, INTENT_DATE_TIME, INTENT_HOLIDAY,
    INTENT_PLAYER_GUIDANCE — those are handled directly in assistant_core.
    """
    # ── Player private info ──────────────────────────────────────────────────
    if intent == INTENT_PRIVATE_PLAYER_INFO:
        return get_player_own_info(user, clean_text)

    # ── Staff knowledge ──────────────────────────────────────────────────────
    if intent == INTENT_SUMMARIZE_BUGS:
        return get_bug_summary()

    if intent == INTENT_STAFF_INFO:
        low = clean_text.lower()
        if "warning" in low:
            return get_warnings_summary()
        return get_support_overview()

    # ── Admin knowledge ──────────────────────────────────────────────────────
    if intent == INTENT_ADMIN_INFO:
        low = clean_text.lower()
        if "event" in low:
            return get_event_settings_summary()
        if "vip" in low:
            return get_vip_settings_summary()
        return get_admin_panel_summary()

    # ── Owner knowledge ──────────────────────────────────────────────────────
    if intent == INTENT_OWNER_INFO:
        low = clean_text.lower()
        if "economy" in low or "health" in low:
            return get_economy_health_summary()
        if "analytics" in low:
            return get_analytics_summary()
        return get_config_summary()

    # ── Public topic knowledge ───────────────────────────────────────────────
    topic_map = {
        INTENT_LUXE:      "luxe_tickets",
        INTENT_CHILLCOINS:"chillcoins",
        INTENT_MINING:    "mining",
        INTENT_FISHING:   "fishing",
        INTENT_CASINO:    "casino",
        INTENT_EVENT:     "events",
        INTENT_VIP:       "vip",
    }
    if intent in topic_map:
        return get_public_answer(topic_map[intent])

    if intent == INTENT_CMD_EXPLAIN:
        import re
        m = re.search(r"[!/]?(\w+)\s*$", clean_text.strip())
        cmd_name = m.group(1).lower() if m else ""
        answer = get_cmd_topic_answer(cmd_name)
        return answer or f"ℹ️ Try !{cmd_name} or !help for usage info."

    return None  # caller handles remaining intents
