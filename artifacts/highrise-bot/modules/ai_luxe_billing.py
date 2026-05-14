"""
modules/ai_luxe_billing.py — Luxe Ticket billing for AI OpenAI calls (3.3G).

Tiers:
  TIER_FREE     (0 🎫) — local commands, help, role, name, guides, refusals
  TIER_BASIC    (1 🎫) — simple explanations, translations, jokes, small writing
  TIER_ADVANCED (3 🎫) — room names, announcements, longer planning, complex answers
  TIER_LIVE     (5 🎫) — live/current data (news, weather, exchange rates)

Billing is OFF by default (room setting ai_billing_enabled=off).
Owners enable it with /setaisetting ai_billing_enabled on.
When OFF, all calls cost 0 🎫 and no balance is checked.
"""
from __future__ import annotations

import re

TIER_FREE     = 0
TIER_BASIC    = 1
TIER_ADVANCED = 3
TIER_LIVE     = 5

# ── Keywords that push a question to ADVANCED tier ───────────────────────────
_ADVANCED_KW = re.compile(
    r"\b(write|create|generate|draft|compose|come\s+up\s+with"
    r"|room\s+name|welcome\s+message|announcement|business\s+name"
    r"|slogan|bio|description|story|poem|letter|catchy|ideas?\s+for"
    r"|plan|outline|strategy|longer|detailed|in\s+depth)\b",
    re.I,
)


def estimate_cost(question: str, intent: str = "", is_live: bool = False) -> int:
    """
    Estimate the Luxe Ticket cost for an OpenAI call.
    Returns one of TIER_FREE, TIER_BASIC, TIER_ADVANCED, TIER_LIVE.
    """
    if is_live:
        return TIER_LIVE
    if _ADVANCED_KW.search(question):
        return TIER_ADVANCED
    return TIER_BASIC


def is_billing_enabled() -> bool:
    """Return True when AI billing is enabled (default: on)."""
    try:
        import database as db
        return db.get_room_setting("ai_billing_enabled", "on") == "on"
    except Exception:
        return True   # default: charge normally


def is_owner_free() -> bool:
    """Return True when the owner is exempt from AI billing (default: on)."""
    try:
        import database as db
        return db.get_room_setting("owner_free_ai", "on") == "on"
    except Exception:
        return True


def is_staff_free() -> bool:
    """Return True when staff/admin are exempt from AI billing (default: off)."""
    try:
        import database as db
        return db.get_room_setting("staff_free_ai", "off") == "on"
    except Exception:
        return False


def should_charge(user_id: str, username: str, perm: str, cost: int) -> tuple[bool, str]:
    """
    Return (True, "") if the user should be charged, or (False, reason) if exempt.
    Reasons: free_local, billing_disabled, owner_free, staff_free.
    """
    if cost <= 0:
        return False, "free_local"
    if not is_billing_enabled():
        return False, "billing_disabled"
    if perm == "owner" and is_owner_free():
        return False, "owner_free"
    if perm in ("staff", "admin") and is_staff_free():
        return False, "staff_free"
    return True, ""


def get_balance_for_log(user_id: str) -> int:
    """Safely read current Luxe balance for debug logging."""
    try:
        from modules.luxe import get_luxe_balance
        return get_luxe_balance(user_id)
    except Exception:
        return -1


def get_billing_status_msg() -> str:
    """Status panel for 'ai billing status' queries."""
    billing = "ON"  if is_billing_enabled() else "OFF"
    owner_f = "ON"  if is_owner_free()      else "OFF"
    staff_f = "ON"  if is_staff_free()      else "OFF"
    return (
        f"AI Billing: {billing} | Owner free: {owner_f} | Staff free: {staff_f}\n"
        "Costs: Free=0 | Basic=1 | Advanced=3 | Live=5 \U0001f3ab"
    )[:249]


def check_can_afford(user_id: str, cost: int) -> tuple[bool, int]:
    """
    Return (can_afford, current_luxe_balance).
    Always returns True when cost == 0.
    """
    if cost == 0:
        return True, 0
    try:
        from modules.luxe import get_luxe_balance
        bal = get_luxe_balance(user_id)
        return bal >= cost, bal
    except Exception:
        return True, 0   # Fail open if luxe module unavailable


def charge_luxe(user_id: str, username: str, cost: int) -> bool:
    """
    Deduct cost from the user's Luxe balance.
    Returns True on success, False if deduction failed.
    Always returns True when cost == 0.
    """
    if cost == 0:
        return True
    try:
        from modules.luxe import deduct_luxe_balance
        return deduct_luxe_balance(user_id, username, cost)
    except Exception:
        return False


def insufficient_funds_msg(cost: int, balance: int) -> str:
    """Return a user-friendly "not enough tickets" message."""
    return (
        f"You need {cost} 🎫 Luxe Ticket(s) for this AI answer. "
        f"You have {balance}. Earn more in the Luxe Shop!"
    )[:249]
