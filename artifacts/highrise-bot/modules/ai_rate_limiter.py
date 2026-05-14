"""
modules/ai_rate_limiter.py — Per-user AI request rate limiter (3.3B).

Rules:
- Max 5 requests per 30-second window per user.
- Identical message within 10 s → silent duplicate ignore.
- After hitting limit → 60-second cooldown, friendly message once.
"""
from __future__ import annotations

import time

_MAX_REQUESTS: int   = 5
_WINDOW_SECS: int    = 30
_DUP_WINDOW: int     = 10
_COOLDOWN_SECS: int  = 60

_requests:  dict[str, list[float]] = {}   # user_id → timestamps
_last_msg:  dict[str, tuple[str, float]] = {}  # user_id → (msg_lower, timestamp)
_cooldown:  dict[str, float] = {}         # user_id → expiry timestamp


def check_rate_limit(user_id: str, message: str) -> str | None:
    """
    Check whether this user is allowed to send another AI request.

    Returns:
        None                  → request is allowed
        "duplicate"           → silent ignore (identical repeat)
        str (non-duplicate)   → error message to whisper to the user
    """
    now = time.monotonic()

    # 1. Active cooldown
    if user_id in _cooldown and _cooldown[user_id] > now:
        remaining = int(_cooldown[user_id] - now)
        return f"⏱️ Please wait {remaining}s before asking again."

    # 2. Duplicate detection
    msg_low = message.strip().lower()
    if user_id in _last_msg:
        prev_msg, prev_ts = _last_msg[user_id]
        if prev_msg == msg_low and (now - prev_ts) < _DUP_WINDOW:
            return "duplicate"

    # 3. Sliding-window rate limit
    if user_id not in _requests:
        _requests[user_id] = []
    _requests[user_id] = [t for t in _requests[user_id] if now - t < _WINDOW_SECS]

    if len(_requests[user_id]) >= _MAX_REQUESTS:
        _cooldown[user_id] = now + _COOLDOWN_SECS
        _requests[user_id] = []
        return "⏱️ Slow down! You're asking too fast. Try again in a minute."

    # 4. Record request
    _requests[user_id].append(now)
    _last_msg[user_id] = (msg_low, now)
    return None


def clear_user(user_id: str) -> None:
    """Remove all rate-limit tracking for a user (e.g. on leave)."""
    _requests.pop(user_id, None)
    _last_msg.pop(user_id, None)
    _cooldown.pop(user_id, None)


def get_status() -> dict:
    """Return a safe status dict for debug summary."""
    now = time.monotonic()
    active_cooldowns = sum(1 for exp in _cooldown.values() if exp > now)
    return {
        "tracked_users": len(_requests),
        "active_cooldowns": active_cooldowns,
        "window_secs": _WINDOW_SECS,
        "max_requests": _MAX_REQUESTS,
    }
