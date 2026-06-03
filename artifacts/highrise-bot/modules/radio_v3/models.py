"""Radio V3 status constants."""
from __future__ import annotations

ACTIVE_STATUSES = (
    "pending",
    "preparing",
    "ready",
    "loading",
    "playing",
)
WAITING_STATUSES = (
    "pending",
    "preparing",
    "ready",
    "loading",
)
TERMINAL_STATUSES = (
    "played",
    "cancelled",
    "failed",
    "cleaned",
)
READY_STATUSES = ("ready", "loading")


def is_terminal(status: str) -> bool:
    return (status or "").strip().lower() in TERMINAL_STATUSES

