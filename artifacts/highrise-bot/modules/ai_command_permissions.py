"""
modules/ai_command_permissions.py — Permission helpers for AI Command Control Layer (3.3F).

Wraps the existing ai_permissions.py system.
Adds VIP tier between PLAYER and STAFF.
"""
from __future__ import annotations

from modules.ai_permissions import (
    get_perm_level as _get_perm,
    PERM_PLAYER,
    PERM_STAFF,
    PERM_ADMIN,
    PERM_OWNER,
)

PERM_VIP = "vip"

# Higher number = more authority
_RANK: dict[str, int] = {
    PERM_PLAYER: 0,
    PERM_VIP:    1,
    PERM_STAFF:  2,
    PERM_ADMIN:  3,
    PERM_OWNER:  4,
}


def get_perm_level(username: str) -> str:
    """Return the AI permission level for username."""
    return _get_perm(username)


def has_permission(user_perm: str, required: str) -> bool:
    """Return True if user_perm satisfies the required level."""
    return _RANK.get(user_perm, 0) >= _RANK.get(required, 0)


def perm_display(perm: str) -> str:
    return {
        PERM_PLAYER: "Player",
        PERM_VIP:    "VIP",
        PERM_STAFF:  "Staff",
        PERM_ADMIN:  "Admin",
        PERM_OWNER:  "Owner",
    }.get(perm, "Player")
