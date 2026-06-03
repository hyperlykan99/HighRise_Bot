"""Radio V2 model constants."""
from __future__ import annotations

ACTIVE_STATUSES = (
    "pending",
    "preparing",
    "uploaded",
    "submitted",
    "playing",
)
WAITING_STATUSES = (
    "pending",
    "preparing",
    "uploaded",
    "submitted",
)
TERMINAL_STATUSES = (
    "played",
    "cancelled",
    "failed",
    "cleaned",
)
READY_STATUSES = ("uploaded", "submitted")


def is_terminal(status: str) -> bool:
    return (status or "").strip().lower() in TERMINAL_STATUSES

