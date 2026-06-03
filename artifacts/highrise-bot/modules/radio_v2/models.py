"""Radio V2 model constants."""
from __future__ import annotations

ACTIVE_STATUSES = (
    "pending",
    "preparing",
    "uploaded",
    "ready",
    "submitted",
    "playing",
)
WAITING_STATUSES = (
    "pending",
    "preparing",
    "uploaded",
    "ready",
    "submitted",
)
TERMINAL_STATUSES = (
    "played",
    "cancelled",
    "failed",
    "cleaned",
)
READY_STATUSES = ("uploaded", "ready", "submitted")
SUBMITTABLE_STATUSES = ("uploaded", "ready")


def is_terminal(status: str) -> bool:
    return (status or "").strip().lower() in TERMINAL_STATUSES
