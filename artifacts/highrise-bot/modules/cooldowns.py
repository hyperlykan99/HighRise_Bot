"""
modules/cooldowns.py
--------------------
Central in-memory cooldown tracker used across all bot modules.

Usage example:
    from modules.cooldowns import check_cooldown, set_cooldown

    remaining = check_cooldown("request", user.id, seconds=30)
    if remaining:
        await bot.highrise.send_whisper(user.id, f"Wait {remaining}s before using this again.")
        return

    # ... do the action ...
    set_cooldown("request", user.id)

Design notes:
  - Pure in-memory — resets on bot restart (fine for short cooldowns).
  - All functions are safe to call even if no data exists yet for a user.
  - No exceptions are ever raised; errors return safe defaults.
  - The cooldown key is just a string — use the command name or any unique label.
"""

import time

# _store[cooldown_key][user_id] = Unix timestamp of the user's last use
_store: dict[str, dict[str, float]] = {}


def check_cooldown(key: str, user_id: str, seconds: int) -> int | None:
    """
    Check whether a user is still on cooldown for a given action.

    Parameters
    ----------
    key     : identifies the action (e.g. "request", "ytsearch", "pick")
    user_id : the Highrise user ID to check
    seconds : the total cooldown duration

    Returns
    -------
    int  — remaining seconds (always >= 1) if the user is still on cooldown
    None — if the user is NOT on cooldown or no data exists (safe default)
    """
    try:
        last_used = _store.get(key, {}).get(user_id)
        if last_used is None:
            return None  # never used this command — no cooldown

        elapsed   = time.time() - last_used
        remaining = seconds - elapsed

        if remaining > 0:
            # Round up so we never show "0 seconds remaining"
            return max(1, int(remaining) + 1)

        return None  # cooldown has expired

    except Exception:
        # If anything goes wrong reading cooldown data, let the action through
        return None


def set_cooldown(key: str, user_id: str) -> None:
    """
    Record the current moment as the user's last use of an action.

    Call this AFTER a successful action so failed attempts (e.g. not enough
    tokens) do not penalise the user.

    Parameters
    ----------
    key     : the action identifier (must match what you pass to check_cooldown)
    user_id : the Highrise user ID
    """
    try:
        if key not in _store:
            _store[key] = {}
        _store[key][user_id] = time.time()
    except Exception:
        pass  # never crash over cooldown bookkeeping


def clear_cooldown(key: str, user_id: str) -> None:
    """
    Remove a user's cooldown for a specific action early.
    Useful for admin overrides or refund scenarios.
    """
    try:
        if key in _store and user_id in _store[key]:
            del _store[key][user_id]
    except Exception:
        pass
