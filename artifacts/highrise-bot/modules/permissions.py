"""
modules/permissions.py
-----------------------
Centralised permission helpers for the Mini Game Bot.

Role hierarchy (highest → lowest):
  1. Owner     — OWNER_USERS list in config.py
  2. Admin     — ADMIN_USERS in config.py + admin_users DB table; owner is also admin
  3. Manager   — managers DB table; admin is also manager
  4. Moderator — moderators DB table; manager is also moderator
  5. Player    — everyone else
"""

import config
import database as db


def is_owner(username: str) -> bool:
    return (
        username.lower() in [u.lower() for u in config.OWNER_USERS]
        or db.is_owner_db(username)
    )


def is_admin(username: str) -> bool:
    return (
        is_owner(username)
        or username.lower() in [u.lower() for u in config.ADMIN_USERS]
        or db.is_admin_db(username)
    )


def is_manager(username: str) -> bool:
    """Owner, admin, or a DB-stored manager."""
    return is_admin(username) or db.is_manager_db(username)


def is_moderator(username: str) -> bool:
    """True only for the moderator DB role itself (not inherited by higher roles)."""
    return db.is_moderator_db(username)


def can_moderate(username: str) -> bool:
    """Moderator, manager, admin, or owner — can use staff moderation tools."""
    return is_manager(username) or is_moderator(username)


def can_manage_games(username: str) -> bool:
    """Manager, admin, or owner — can control casino/game settings."""
    return is_manager(username)


def can_manage_economy(username: str) -> bool:
    """Only admin or owner — can change player coin balances."""
    return is_admin(username)


def can_manage_staff(username: str) -> bool:
    """Only admin or owner — can add/remove moderators and managers."""
    return is_admin(username)


def can_audit(username: str) -> bool:
    """Moderator, manager, admin, or owner — can view audit/report data."""
    return can_moderate(username)
