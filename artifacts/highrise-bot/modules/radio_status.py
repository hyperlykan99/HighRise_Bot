"""
Shared radio request status constants.

Keep this module passive: constants only, no imports from the radio pipeline.
"""
from __future__ import annotations


ACTIVE_QUEUE_STATUSES = (
    "pending",
    "processing",
    "downloading",
    "downloaded",
    "uploading",
    "indexing",
    "staged",
    "ready",
    "queued",
    "submitted",
    "playing",
)

TERMINAL_QUEUE_STATUSES = (
    "done",
    "played",
    "skipped",
    "failed",
    "failed_download",
    "error",
    "duplicate_superseded",
    "cancelled",
    "cleaned",
)
