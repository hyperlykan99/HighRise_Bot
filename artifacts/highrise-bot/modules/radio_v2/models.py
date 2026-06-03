"""Radio V2 model constants."""
from __future__ import annotations

ACTIVE_STATUSES = (
    "pending",
    "preparing",
    "uploaded",
    "ready",
    "submitted",
    "prequeued",
    "playing",
)
WAITING_STATUSES = (
    "pending",
    "preparing",
    "uploaded",
    "ready",
    "submitted",
    "prequeued",
)
TERMINAL_STATUSES = (
    "played",
    "cancelled",
    "failed",
    "skipped",
    "cleaned",
)
READY_STATUSES = ("uploaded", "ready", "submitted", "prequeued")
SUBMITTABLE_STATUSES = ("uploaded", "ready")


def is_terminal(status: str) -> bool:
    return (status or "").strip().lower() in TERMINAL_STATUSES
