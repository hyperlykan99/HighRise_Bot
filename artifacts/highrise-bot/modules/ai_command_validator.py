"""
modules/ai_command_validator.py — Validates OpenAI-suggested commands (OpenAI-First Brain).

Checks:
  1. Command is in the local whitelist (AI_COMMAND_WHITELIST from ai_command_mapper)
  2. User's permission level meets the required level
  3. Economy lock is not active (for economy-impacting commands)

Returns (valid: bool, error_message: str).
error_message is "" when valid=True.
"""
from __future__ import annotations

from modules.ai_command_mapper     import AI_COMMAND_WHITELIST, get_command_config
from modules.ai_permissions        import (
    PERM_PLAYER, PERM_STAFF, PERM_ADMIN, PERM_OWNER, perm_label,
)

# Ordered rank so we can compare numeric permission levels
_RANK: dict[str, int] = {
    PERM_PLAYER: 0,
    PERM_STAFF:  1,
    PERM_ADMIN:  2,
    PERM_OWNER:  3,
}

# Map whitelist "requires_permission" strings → PERM_* constants
_PERM_MAP: dict[str, str] = {
    "player": PERM_PLAYER,
    "staff":  PERM_STAFF,
    "admin":  PERM_ADMIN,
    "owner":  PERM_OWNER,
}


def validate_command(
    cmd_key:  str,
    args:     list[str],
    perm:     str,
    username: str = "",
) -> tuple[bool, str]:
    """
    Validate cmd_key against the local whitelist and the user's permission.

    Args:
        cmd_key:  The command key suggested by OpenAI (e.g. "balance", "mine").
        args:     Argument list from OpenAI.
        perm:     The user's resolved permission level (PERM_PLAYER/STAFF/ADMIN/OWNER).
        username: For logging only.

    Returns:
        (True,  "")            — command is valid and user has permission.
        (False, error_message) — command is blocked; reason in error_message.
    """
    if not cmd_key:
        return False, "No command was detected in your request."

    cfg = get_command_config(cmd_key)
    if cfg is None:
        print(f"[AI VALIDATOR] cmd={cmd_key!r} not_in_whitelist")
        return False, f"'{cmd_key}' is not in the AI command whitelist."

    # ── Permission check ──────────────────────────────────────────────────────
    required_str  = cfg.get("requires_permission", "player")
    required_perm = _PERM_MAP.get(required_str, PERM_PLAYER)
    user_rank     = _RANK.get(perm, 0)
    required_rank = _RANK.get(required_perm, 0)

    if user_rank < required_rank:
        needed_label = required_str.capitalize()
        print(
            f"[AI VALIDATOR] cmd={cmd_key!r} user={username!r} "
            f"perm={perm!r} required={required_str!r} DENIED"
        )
        return False, (
            f"You need {needed_label} permission to run !{cmd_key} through AI. "
            f"Your role: {perm_label(perm)}."
        )

    # ── Economy lock check ────────────────────────────────────────────────────
    if cfg.get("blocked_if_economy_lock"):
        try:
            import database as db
            if db.get_room_setting("economy_lock", "off") == "on":
                print(f"[AI VALIDATOR] cmd={cmd_key!r} economy_lock=on DENIED")
                return False, (
                    "The economy is currently locked. "
                    f"!{cmd_key} cannot run through AI while the lock is active."
                )
        except Exception:
            pass  # Fail open if DB unavailable

    print(
        f"[AI VALIDATOR] cmd={cmd_key!r} user={username!r} "
        f"perm={perm!r} required={required_str!r} OK"
    )
    return True, ""
