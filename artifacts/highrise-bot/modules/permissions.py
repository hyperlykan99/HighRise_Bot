"""
modules/permissions.py
-----------------------
Centralised permission helpers for the Mini Game Bot.

Role hierarchy (highest → lowest):
  1. Owner   — OWNER_USERS list in config.py
  2. Admin   — ADMIN_USERS list in config.py  (owner is also admin)
  3. Manager — stored in SQLite managers table (admin is also manager)
  4. Player  — everyone else
"""

import config
import database as db


def is_owner(username: str) -> bool:
    return username.lower() in [u.lower() for u in config.OWNER_USERS]


def is_admin(username: str) -> bool:
    return (
        is_owner(username)
        or username.lower() in [u.lower() for u in config.ADMIN_USERS]
    )


def is_manager(username: str) -> bool:
    """Owner, admin, or a DB-stored manager."""
    return is_admin(username) or db.is_manager_db(username)


def can_manage_games(username: str) -> bool:
    """Owner, admin, or manager — can control casino/game settings."""
    return is_manager(username)


def can_manage_economy(username: str) -> bool:
    """Only owner or admin — can change player coin balances."""
    return is_admin(username)
