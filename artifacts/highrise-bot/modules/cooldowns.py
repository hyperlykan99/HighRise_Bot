"""
modules/cooldowns.py
--------------------
In-memory cooldown tracker used across all bot modules.

Two kinds of cooldowns are supported:

  Room-wide  — one shared timer for the whole room (e.g. /trivia, /scramble)
               Any player starting the action resets the timer for everyone.

  Per-user   — a separate timer for every player (e.g. /coinflip, /answer)
               One player being on cooldown doesn't affect anyone else.

All functions are safe to call even if no data exists yet.
Errors are caught silently — a cooldown bug will never crash the bot.
The state is in-memory only; it resets when the bot restarts.
"""

import time

# Room-wide cooldowns: _room[key] = Unix timestamp of last use
_room: dict[str, float] = {}

# Per-user cooldowns: _user[key][user_id] = Unix timestamp of last use
_user: dict[str, dict[str, float]] = {}


# ---------------------------------------------------------------------------
# Room-wide helpers
# ---------------------------------------------------------------------------

def check_room_cooldown(key: str, seconds: int) -> int | None:
    """
    Check whether a room-wide action is still on cooldown.

    Returns the number of remaining seconds (>= 1) if it IS on cooldown,
    or None if the cooldown has expired (or was never set).
    """
    try:
        last = _room.get(key)
        if last is None:
            return None
        remaining = seconds - (time.time() - last)
        return max(1, int(remaining) + 1) if remaining > 0 else None
    except Exception:
        return None   # never crash over a cooldown check


def set_room_cooldown(key: str) -> None:
    """Record the current moment as the last time this room action was used."""
    try:
        _room[key] = time.time()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Per-user helpers
# ---------------------------------------------------------------------------

def check_user_cooldown(key: str, user_id: str, seconds: int) -> int | None:
    """
    Check whether a specific player is still on cooldown for an action.

    Returns remaining seconds (>= 1) if on cooldown, or None if not.
    """
    try:
        last = _user.get(key, {}).get(user_id)
        if last is None:
            return None
        remaining = seconds - (time.time() - last)
        return max(1, int(remaining) + 1) if remaining > 0 else None
    except Exception:
        return None


def set_user_cooldown(key: str, user_id: str, reduction: int = 0) -> None:
    """
    Record the current moment as this player's last use of an action.

    Parameters
    ----------
    reduction : seconds to subtract from the stored timestamp, which makes
                the cooldown effectively shorter.  For example, a 10 s cooldown
                with reduction=5 will expire after only 5 real seconds.
    """
    try:
        if key not in _user:
            _user[key] = {}
        _user[key][user_id] = time.time() - max(0, reduction)
    except Exception:
        pass
