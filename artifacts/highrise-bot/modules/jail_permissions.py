"""modules/jail_permissions.py — Protection checks for the Luxe Jail system (3.4A)."""
from __future__ import annotations
from modules.permissions import is_owner, is_admin, is_manager, is_moderator
from modules.jail_config import protect_staff, allow_owner_override

def _get_bot_name_fragments() -> tuple[str, ...]:
    """Include the configured security bot name so it cannot be jailed."""
    from config import SECURITY_BOT_NAME
    base = (
        "securitybot", "security_bot", "blackjackbot", "pokerbot", "hostbot",
        "minerbot", "bankerbot", "shopbot", "djbot", "eventbot", "fisherbot",
        "hangoutbot",
    )
    extra = SECURITY_BOT_NAME.lower().replace(" ", "").replace("_", "")
    return base + (extra,) if extra not in base else base

_BOT_NAME_FRAGMENTS: tuple[str, ...] = _get_bot_name_fragments()


def is_bot_account(username: str) -> bool:
    """Heuristic: return True if the username matches a known bot name fragment."""
    low = username.lower().replace(" ", "").replace("_", "")
    return any(b.replace("_", "") in low for b in _BOT_NAME_FRAGMENTS)


def is_jail_protected(username: str) -> bool:
    """Return True if this player cannot be jailed by normal players."""
    low = username.lower()
    if is_bot_account(low):
        return True
    if is_owner(low):
        return True
    if protect_staff():
        return is_admin(low) or is_manager(low) or is_moderator(low)
    return False


def can_actor_jail_target(
    actor_username: str,
    actor_perm: str,
    target_username: str,
) -> tuple[bool, str]:
    """
    Return (allowed, denial_reason).
    Owners bypass staff protection if allow_owner_override() is True.
    """
    if actor_username.lower() == target_username.lower():
        return False, "You can't jail yourself."
    if is_bot_account(target_username.lower()):
        return False, "Bots can't be jailed."
    if is_owner(target_username.lower()):
        return False, f"{target_username} is the owner and can't be jailed."
    if is_jail_protected(target_username):
        if actor_perm == "owner" and allow_owner_override():
            return True, ""
        return False, f"{target_username} is protected and can't be jailed."
    return True, ""
