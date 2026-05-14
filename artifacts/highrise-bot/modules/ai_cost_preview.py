"""
modules/ai_cost_preview.py — AI cost preview + 5 Luxe Ticket live-query confirmation.

Cost tiers (match ai_luxe_billing.py):
  0  — commands, help, role, name, ChillTopia guides, confirm/cancel
  1  — basic answers (explain, translate, small question)
  3  — advanced writing (room names, announcements, plans)
  5  — live / current info (weather, prices, exchange rates, news)

Live queries (5+) require an explicit "confirm" before the OpenAI call is
made so players are not accidentally charged.  A _LIVE_PENDING dict keyed by
user_id stores the original query text + cost for up to 60 seconds.
"""
from __future__ import annotations

import time
from typing import Optional

from modules.ai_luxe_billing import TIER_FREE, TIER_BASIC, TIER_ADVANCED, TIER_LIVE

# ── Live-query pending store ──────────────────────────────────────────────────
_LIVE_PENDING: dict[str, dict] = {}
_TIMEOUT: float = 60.0


def set_live_pending(user_id: str, query: str, cost: int) -> None:
    """Store a pending live query awaiting the user's confirm/cancel."""
    _LIVE_PENDING[user_id] = {
        "query":      query,
        "cost":       cost,
        "expires_at": time.monotonic() + _TIMEOUT,
    }


def get_live_pending(user_id: str) -> Optional[dict]:
    """Return the pending live query for user_id, or None if none/expired."""
    p = _LIVE_PENDING.get(user_id)
    if not p:
        return None
    if time.monotonic() > p["expires_at"]:
        del _LIVE_PENDING[user_id]
        return None
    return p


def clear_live_pending(user_id: str) -> None:
    """Remove any pending live query for user_id."""
    _LIVE_PENDING.pop(user_id, None)


def has_live_pending(user_id: str) -> bool:
    return get_live_pending(user_id) is not None


# ── Cost info ─────────────────────────────────────────────────────────────────

def cost_info_message() -> str:
    """Full cost table shown for 'ai cost' / 'ai prices' commands."""
    return (
        "AI costs:\n"
        "Free: commands, role, name, ChillTopia guides\n"
        "Basic: 1 \U0001f3ab (explain, translate, answer)\n"
        "Advanced: 3 \U0001f3ab (write, plan, generate)\n"
        "Live: 5 \U0001f3ab (weather, prices, current news)"
    )[:249]


def cost_note_str(cost: int) -> str:
    """Short inline suffix appended to a paid answer, e.g. ' [1 \U0001f3ab]'."""
    if cost <= TIER_FREE:
        return ""
    return f" [{cost} \U0001f3ab]"


def live_confirm_msg(cost: int) -> str:
    """Confirmation prompt shown before executing a live-data query."""
    return (
        f"\u26a1 Live AI answer costs {cost} \U0001f3ab Luxe Ticket(s).\n"
        "Reply confirm to continue, or cancel. (60s)"
    )[:249]


def requires_live_confirm(cost: int) -> bool:
    """Return True if this cost tier needs explicit confirmation first."""
    return cost >= TIER_LIVE
