"""
modules/ai_human_brain.py — Human-like AI pipeline orchestrator (3.3G).

Wraps OpenAI with:
  - Luxe Ticket billing (configurable via ai_billing_enabled room setting)
  - Role-aware prompting (Player/Staff/Admin/Owner personality)
  - Response style (short, friendly, ≤249 chars)
  - Billing and usage audit logs

This is the single entry point for all general/unknown questions that
require OpenAI. It replaces direct ask_openai_short() calls in ai_brain.py.

Public API:
  ask_human_brain(question, username, user_id, role, intent, is_live)
  → str (the answer) or None (if failed / user can't afford / intent blocked)
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from modules.ai_llm_fallback  import ask_openai_short, _build_skip_set
from modules.ai_luxe_billing  import (
    estimate_cost, is_billing_enabled,
    check_can_afford, charge_luxe, insufficient_funds_msg,
    TIER_FREE,
)
from modules.ai_usage_logs import log_billing, log_llm_call

MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")


async def ask_human_brain(
    question: str,
    username: str,
    user_id:  str,
    role:     str     = "Player",
    intent:   str     = "",
    is_live:  bool    = False,
) -> str | None:
    """
    Full human-like AI pipeline:
      1. Check if billing is on and estimate cost
      2. Check Luxe balance (if billing on and cost > 0)
      3. Call OpenAI via ask_openai_short
      4. Charge Luxe after successful answer
      5. Log billing + LLM usage
      6. Return styled answer

    Returns:
      str  — the answer to send to the player
      None — if OpenAI failed or intent is in skip set (caller handles fallback)
    """
    # Determine billing cost
    billing_on = is_billing_enabled()
    cost = estimate_cost(question, intent, is_live) if billing_on else TIER_FREE

    # Balance check BEFORE calling OpenAI
    if billing_on and cost > TIER_FREE:
        can_afford, balance = check_can_afford(user_id, cost)
        if not can_afford:
            log_billing(username, cost, False, "insufficient_funds")
            return insufficient_funds_msg(cost, balance)

    # Call OpenAI with role-aware prompt
    print(f"[AI HUMAN BRAIN] user={username} role={role} intent={intent!r} cost={cost} is_live={is_live}")
    answer = await ask_openai_short(question, username, role)

    if answer:
        # Charge AFTER success (never charge failures)
        if billing_on and cost > TIER_FREE:
            charged = charge_luxe(user_id, username, cost)
            log_billing(username, cost, charged)
        else:
            log_billing(username, cost, True, "free")
        log_llm_call(username, intent, True, MODEL)
        return answer
    else:
        log_billing(username, cost, False, "llm_failed")
        log_llm_call(username, intent, False, MODEL)
        return None


async def ask_human_brain_gated(
    user,
    text:    str,
    intent:  str,
    perm:    str,
) -> str | None:
    """
    Intent-gated version used by try_llm_answer in ai_llm_fallback.py.
    Checks the skip set before calling OpenAI.
    Returns None if the intent is in the skip set or OpenAI fails.
    """
    skip = _build_skip_set()
    if intent in skip:
        print(f"[AI HUMAN BRAIN] skipped — intent {intent!r} in skip set")
        return None

    role = _perm_to_role(perm)
    return await ask_human_brain(
        question = text,
        username = user.username,
        user_id  = user.id,
        role     = role,
        intent   = intent,
        is_live  = False,
    )


def _perm_to_role(perm: str) -> str:
    """Map internal perm string to human-readable role label."""
    return {
        "owner":  "Owner",
        "admin":  "Admin",
        "staff":  "Staff",
        "vip":    "VIP",
        "player": "Player",
    }.get(perm, "Player")
