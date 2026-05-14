"""
modules/ai_confirmation_manager.py — Pending confirmation flow (3.3A rebuild).

One pending action per user. 60-second timeout before auto-expiry.
Confirmation phrase must be supplied exactly by the same user.
"""
from __future__ import annotations

import time
from typing import Optional

_PENDING: dict[str, dict] = {}
_TIMEOUT_SECS: float = 60.0


def set_pending(
    user_id:        str,
    action_key:     str,
    label:          str,
    confirm_phrase: str,
    current_value:  str,
    new_value:      str,
    risk:           str,
) -> None:
    """Store a pending action for user_id. Overwrites any previous pending."""
    _PENDING[user_id] = {
        "action_key":     action_key,
        "label":          label,
        "confirm_phrase": confirm_phrase.upper(),
        "current_value":  current_value,
        "new_value":      new_value,
        "risk":           risk,
        "expires_at":     time.monotonic() + _TIMEOUT_SECS,
    }


def get_pending(user_id: str) -> Optional[dict]:
    """Return the pending action for user_id, or None if none / expired."""
    p = _PENDING.get(user_id)
    if not p:
        return None
    if time.monotonic() > p["expires_at"]:
        del _PENDING[user_id]
        return None
    return p


def clear_pending(user_id: str) -> None:
    """Remove any pending action for user_id."""
    _PENDING.pop(user_id, None)


# ── Simple confirm / cancel word sets (mirrors ai_command_confirmation) ───────
_SIMPLE_CONFIRM = {"confirm", "yes", "y", "ok", "okay", "approve"}
_SIMPLE_CANCEL  = {"cancel", "no", "n", "stop", "nevermind", "never mind"}


def is_simple_confirm(text: str) -> bool:
    """Return True if text is a short affirmative word."""
    return text.strip().lower() in _SIMPLE_CONFIRM


def is_simple_cancel(text: str) -> bool:
    """Return True if text is a short negative word."""
    return text.strip().lower() in _SIMPLE_CANCEL


def preview_message(p: dict) -> str:
    """Build the confirmation prompt whisper (≤ 249 chars)."""
    msg = (
        f"⚙️ Prepared change:\n"
        f"{p['label']}: {p['current_value']} → {p['new_value']}\n"
        f"Risk: {p['risk']}\n"
        f"Reply confirm to apply, or cancel.\n"
        f"(Also: {p['confirm_phrase']})"
    )
    return msg[:249]


def has_pending(user_id: str) -> bool:
    return get_pending(user_id) is not None
