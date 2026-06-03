"""Radio V2 settings facade."""
from __future__ import annotations

import os

import modules.config_store as cs


def system_version() -> str:
    return (os.getenv("RADIO_SYSTEM_VERSION", "v1") or "v1").strip().lower()


def enabled() -> bool:
    return system_version() == "v2"


def playback_mode() -> str:
    return (os.getenv("RADIO_REQUEST_PLAYBACK_MODE", "azura_native_request") or "").strip().lower()


def drain_mode() -> bool:
    return (os.getenv("RADIO_REQUEST_DRAIN_MODE", "true") or "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def block_mode() -> bool:
    default = "true" if enabled() else "false"
    return (os.getenv("RADIO_REQUEST_BLOCK_MODE", default) or default).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def requests_enabled() -> bool:
    return cs.request_system_enabled()


def cooldown_secs() -> int:
    return cs.cooldown_secs()


def max_queue_size() -> int:
    return cs.max_active_queue_limit()


def max_per_user() -> int:
    return cs.per_user_queue_limit()


def voteskip_threshold() -> int:
    return cs.voteskip_threshold()


def skip_if_requester_leaves() -> bool:
    return cs.skip_if_requester_leaves()


def refund_if_leaves() -> bool:
    return cs.refund_if_leaves()


def admin_requests_ignore_leave() -> bool:
    return cs.admin_requests_ignore_leave()
