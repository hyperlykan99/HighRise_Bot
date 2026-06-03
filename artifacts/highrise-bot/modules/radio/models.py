"""Radio skeleton constants and lightweight model helpers."""

from __future__ import annotations


STATUS_PENDING = "pending"
STATUS_PREPARING = "preparing"
STATUS_READY = "ready"
STATUS_SUBMITTED = "submitted"
STATUS_PLAYING = "playing"
STATUS_PLAYED = "played"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_CLEANED = "cleaned"

REQUEST_STATUSES = {
    STATUS_PENDING,
    STATUS_PREPARING,
    STATUS_READY,
    STATUS_SUBMITTED,
    STATUS_PLAYING,
    STATUS_PLAYED,
    STATUS_FAILED,
    STATUS_CANCELLED,
    STATUS_CLEANED,
}

QUEUE_DISPLAY_STATUSES = (
    STATUS_PENDING,
    STATUS_PREPARING,
    STATUS_READY,
    STATUS_SUBMITTED,
)

TERMINAL_STATUSES = (
    STATUS_PLAYED,
    STATUS_FAILED,
    STATUS_CANCELLED,
    STATUS_CLEANED,
)


def normalize_status(value: str | None) -> str:
    status = (value or "").strip().lower()
    return status if status in REQUEST_STATUSES else STATUS_PENDING

