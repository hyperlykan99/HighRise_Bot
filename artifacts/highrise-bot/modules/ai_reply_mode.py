"""
modules/ai_reply_mode.py — AI reply channel mode setting (3.3B).

Modes:
  public  — all AI responses go to room chat (except private data, always whisper)
  whisper — all AI responses whispered privately to requester
  smart   — AI auto-chooses based on content sensitivity (default)

Only the owner can change the mode. Change requires confirmation.
Private/staff/admin/owner data is ALWAYS whispered regardless of mode.
"""
from __future__ import annotations

import database as db

_KEY          = "ai_reply_mode"
_DEFAULT      = "smart"
VALID_MODES   = frozenset({"public", "whisper", "smart"})

# Knowledge levels that force whisper regardless of mode
_FORCE_WHISPER_LEVELS = frozenset({
    "player_private", "staff", "admin", "owner",
})

# Response types that force whisper regardless of mode
_FORCE_WHISPER_TYPES = frozenset({
    "confirmation_preview",
    "owner_summary",
    "staff_summary",
    "moderation_help",
    "player_private_summary",
    "permission_denied_private",
    "bug_summary_staff",
    "debug_summary",
    "private_balance",
    "personalized_guidance",
})


def get_reply_mode() -> str:
    """Return the current AI reply mode from room settings."""
    try:
        mode = db.get_room_setting(_KEY, _DEFAULT)
        return mode if mode in VALID_MODES else _DEFAULT
    except Exception:
        return _DEFAULT


def set_reply_mode(mode: str) -> bool:
    """Persist a new reply mode. Returns True on success."""
    if mode not in VALID_MODES:
        return False
    try:
        db.set_room_setting(_KEY, mode)
        return True
    except Exception:
        return False


def choose_reply_channel(
    response_type:    str  = "general",
    knowledge_level:  str  = "public",
    contains_private: bool = False,
    reply_mode:       str | None = None,
) -> str:
    """
    Return 'public' or 'whisper' for this response.

    Safety overrides always win:
      - contains_private=True        → whisper
      - knowledge_level in STAFF+    → whisper
      - response_type in sensitive   → whisper

    After safety overrides, apply the global mode:
      - mode='whisper' → whisper
      - mode='public'  → public
      - mode='smart'   → public for general, whisper handled by safety above
    """
    if reply_mode is None:
        reply_mode = get_reply_mode()

    # ── Safety overrides (always whisper) ────────────────────────────────────
    if contains_private:
        return "whisper"
    if knowledge_level.lower() in _FORCE_WHISPER_LEVELS:
        return "whisper"
    if response_type in _FORCE_WHISPER_TYPES:
        return "whisper"

    # ── Apply global mode ─────────────────────────────────────────────────────
    if reply_mode == "whisper":
        return "whisper"
    if reply_mode == "public":
        return "public"

    # smart → public for safe content (private already caught above)
    return "public"
