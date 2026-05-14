"""
modules/ai_command_confirmation.py — Pending confirmation flow for AI commands (3.3F).

One pending AI command per user. 60-second timeout.
Phrase: "CONFIRM AI COMMAND". Cannot be confirmed by another user.
Separate from ai_confirmation_manager.py (which handles setting changes).
"""
from __future__ import annotations

import time
from typing import Optional

_PENDING: dict[str, dict] = {}
_TIMEOUT: float = 60.0

CONFIRM_PHRASE = "CONFIRM AI COMMAND"
CANCEL_PHRASE  = "CANCEL"


def prepare_command(
    user_id:    str,
    command:    str,
    args:       list[str],
    risk:       str,
    perm_label: str,
    economy:    bool = False,
) -> None:
    """Store a pending AI command for user_id (overwrites any existing)."""
    _PENDING[user_id] = {
        "command":    command,
        "args":       args,
        "risk":       risk,
        "perm_label": perm_label,
        "economy":    economy,
        "expires_at": time.monotonic() + _TIMEOUT,
    }


def get_pending(user_id: str) -> Optional[dict]:
    """Return the pending command for user_id, or None if none/expired."""
    p = _PENDING.get(user_id)
    if not p:
        return None
    if time.monotonic() > p["expires_at"]:
        del _PENDING[user_id]
        return None
    return p


def clear_pending(user_id: str) -> None:
    _PENDING.pop(user_id, None)


def has_pending(user_id: str) -> bool:
    return get_pending(user_id) is not None


def is_confirm(text: str) -> bool:
    return text.strip().upper() == CONFIRM_PHRASE


def is_cancel(text: str) -> bool:
    return text.strip().upper() == CANCEL_PHRASE


def build_prompt(command: str, args: list[str], risk: str, perm_label: str, economy_locked: bool) -> str:
    """Build the whisper confirmation prompt (≤249 chars)."""
    cmd_str  = "!" + command
    if args:
        cmd_str += " " + " ".join(args)
    lock_note = " | Eco lock ON" if economy_locked else ""
    msg = (
        f"⚙️ AI Command Ready:\n"
        f"{cmd_str}\n"
        f"Risk: {risk} | Perm: {perm_label}{lock_note}\n"
        f"Reply: CONFIRM AI COMMAND or CANCEL (60s)"
    )
    return msg[:249]
