"""Radio skeleton constants and lightweight model helpers."""

from __future__ import annotations


STATUS_PENDING = "pending"
STATUS_PENDING_SEARCH = "pending_search"
STATUS_PREPARING = "preparing"
STATUS_READY = "ready"
STATUS_RELEASED = "released"
STATUS_SUBMITTED = "submitted"
STATUS_PLAYING = "playing"
STATUS_PLAYED = "played"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_REMOVED = "removed"
STATUS_SKIPPED = "skipped"
STATUS_REFUNDED = "refunded"
STATUS_CLEANED = "cleaned"

REQUEST_STATUSES = {
    STATUS_PENDING,
    STATUS_PENDING_SEARCH,
    STATUS_PREPARING,
    STATUS_READY,
    STATUS_RELEASED,
    STATUS_SUBMITTED,
    STATUS_PLAYING,
    STATUS_PLAYED,
    STATUS_FAILED,
    STATUS_CANCELLED,
    STATUS_REMOVED,
    STATUS_SKIPPED,
    STATUS_REFUNDED,
    STATUS_CLEANED,
}

QUEUE_DISPLAY_STATUSES = (
    STATUS_READY,
)

TERMINAL_STATUSES = (
    STATUS_PLAYED,
    STATUS_FAILED,
    STATUS_CANCELLED,
    STATUS_REMOVED,
    STATUS_SKIPPED,
    STATUS_REFUNDED,
    STATUS_CLEANED,
)


def normalize_status(value: str | None) -> str:
    status = (value or "").strip().lower()
    return status if status in REQUEST_STATUSES else STATUS_PENDING
