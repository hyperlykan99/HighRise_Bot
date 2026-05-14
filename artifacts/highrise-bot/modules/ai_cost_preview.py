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

import database as db
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


# ── DB-persisted AI cost preview setting ─────────────────────────────────────
_SETTING_KEY = "ai_cost_preview_required"


def is_cost_preview_required() -> bool:
    """Return True if basic/advanced queries must show cost + confirm before charging."""
    try:
        return db.get_room_setting(_SETTING_KEY, "0") == "1"
    except Exception:
        return False


def set_cost_preview_required(value: bool) -> None:
    """Persist the ai_cost_preview_required setting (owner only)."""
    db.set_room_setting(_SETTING_KEY, "1" if value else "0")


def cost_preview_status_msg() -> str:
    """Status line for 'ai cost preview status' queries."""
    state  = "ON" if is_cost_preview_required() else "OFF"
    detail = (
        "Paid AI answers show cost + confirm before charging."
        if state == "ON" else
        "1\u20133 \U0001f3ab answers auto-charge; 5 \U0001f3ab live always confirms."
    )
    return f"AI Cost Preview: {state}\n{detail}"[:249]


# ── Basic-query pending store (System D) ─────────────────────────────────────
_BASIC_PENDING: dict[str, dict] = {}


def set_basic_pending(user_id: str, query: str, cost: int) -> None:
    """Store a pending basic/advanced query awaiting the user's confirm/cancel."""
    _BASIC_PENDING[user_id] = {
        "query":      query,
        "cost":       cost,
        "expires_at": time.monotonic() + _TIMEOUT,
    }


def get_basic_pending(user_id: str) -> Optional[dict]:
    """Return the pending basic query for user_id, or None if none/expired."""
    p = _BASIC_PENDING.get(user_id)
    if not p:
        return None
    if time.monotonic() > p["expires_at"]:
        del _BASIC_PENDING[user_id]
        return None
    return p


def clear_basic_pending(user_id: str) -> None:
    """Remove any pending basic query for user_id."""
    _BASIC_PENDING.pop(user_id, None)


def has_basic_pending(user_id: str) -> bool:
    return get_basic_pending(user_id) is not None


def basic_confirm_msg(cost: int) -> str:
    """Confirmation prompt shown before executing a 1–3🎫 query when preview is ON."""
    return (
        f"\U0001f4b0 This costs {cost} \U0001f3ab Luxe Ticket(s).\n"
        "Reply confirm to continue, or cancel. (60s)"
    )[:249]


def requires_basic_confirm(cost: int) -> bool:
    """True when AI cost preview is ON and the tier is basic or advanced (not live)."""
    return is_cost_preview_required() and TIER_BASIC <= cost < TIER_LIVE
