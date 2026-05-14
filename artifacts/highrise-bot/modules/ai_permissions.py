"""
modules/ai_permissions.py — Permission levels for AceSinatra (3.3A rebuild).

Levels:
  player — can ask questions, request guidance, submit bug/feedback
  staff  — can ask moderation help, summarize reports
  admin  — can prepare setting/event changes (must confirm)
  owner  — can prepare protected changes (must confirm)
"""
from __future__ import annotations

from modules.permissions import is_owner, is_admin, can_moderate

PERM_PLAYER = "player"
PERM_STAFF  = "staff"
PERM_ADMIN  = "admin"
PERM_OWNER  = "owner"


def get_perm_level(username: str) -> str:
    u = username.lower()
    if is_owner(u):       return PERM_OWNER
    if is_admin(u):       return PERM_ADMIN
    if can_moderate(u):   return PERM_STAFF
    return PERM_PLAYER


def requires_staff(perm: str) -> bool:
    return perm in (PERM_STAFF, PERM_ADMIN, PERM_OWNER)


def requires_admin(perm: str) -> bool:
    return perm in (PERM_ADMIN, PERM_OWNER)


def requires_owner(perm: str) -> bool:
    return perm == PERM_OWNER


def perm_label(perm: str) -> str:
    labels = {
        PERM_PLAYER: "Player",
        PERM_STAFF:  "Staff",
        PERM_ADMIN:  "Admin",
        PERM_OWNER:  "Owner",
    }
    return labels.get(perm, "Player")
