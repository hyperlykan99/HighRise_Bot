"""
modules/request_queue.py
------------------------
Clean interface to the radio request queue.

Reads come from the in-memory job state (exposed by yt_request's public API)
which is always kept in sync with the yt_request_jobs DB table.

Write operations (cancel, clear) delegate back to yt_request's public API so
that all state transitions remain atomic under the existing locks.

Import pattern: uses a lazy _rq() accessor to avoid circular imports at load time.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot


def _rq():
    """Deferred import of yt_request to break circular dependency at load time."""
    import modules.yt_request as _mod
    return _mod


# ─── Queue reads ──────────────────────────────────────────────────────────────

def pending_jobs() -> list:
    """
    All jobs currently pending / downloading / uploading, sorted by job ID
    (which is stable chronological order within a session).
    """
    snap = _rq().radio_get_jobs_snapshot()
    return sorted(
        [j for j in snap if j["status"] in ("pending", "downloading", "uploading")],
        key=lambda j: j.get("id", 0),
    )


def active_count() -> int:
    return _rq().radio_active_count()


def user_pending_count(user_id: str) -> int:
    return _rq().radio_user_pending_count(user_id)


def currently_playing() -> "dict | None":
    """
    Return the job dict for the request currently playing on AzuraCast, or None.
    Matches on the db_id that the nowplaying cycle sets.
    """
    cp_db_id = _rq().radio_get_currently_playing_id()
    if not cp_db_id:
        return None
    snap = _rq().radio_get_jobs_snapshot()
    return next(
        (j for j in snap if j.get("db_id") == cp_db_id or j.get("id") == cp_db_id),
        None,
    )


def recent_history(limit: int = 10) -> list:
    return _rq().radio_request_history(limit)


# ─── Dedup / ban checks ───────────────────────────────────────────────────────

def check_dedup(url: str, window_secs: int) -> "dict | None":
    return _rq().radio_check_dedup(url, window_secs)


def is_banned_requester(username: str) -> bool:
    return _rq().radio_check_banned_requester(username)


# ─── SFTP readiness ───────────────────────────────────────────────────────────

def sftp_ready() -> bool:
    return _rq().radio_sftp_ready()


def sftp_missing() -> list:
    return _rq().radio_sftp_missing()


# ─── Job lifecycle ────────────────────────────────────────────────────────────

def submit_job(
    bot: "BaseBot",
    user_id: str,
    username: str,
    url: str,
    coins_charged: int = 0,
    payment_type: str = "paid",
) -> None:
    """Create a job record and launch the yt-dlp → SFTP pipeline."""
    _rq().radio_submit_job(bot, user_id, username, url, coins_charged, payment_type)


def cancel_job(jid: int, reason: str = "cancelled_by_admin") -> "dict | None":
    """
    Cancel an active job by in-memory job ID.
    Returns the (now-cancelled) job dict, or None if not found / already done.
    Does NOT refund — caller is responsible for refunding if needed.
    """
    return _rq().radio_cancel_job(jid, reason)


def clear_all_pending(refund: bool = True) -> list:
    """
    Cancel every pending / downloading / uploading job.
    If refund=True, issues coin refunds automatically.
    Returns list of cancelled job dicts.
    """
    import modules.payment_service as ps

    jobs = pending_jobs()
    cancelled = []
    for j in jobs:
        result = cancel_job(j["id"], "cleared_by_admin")
        if result:
            if refund:
                coins = result.get("coins_charged", 0)
                uid   = result.get("user_id", "")
                if coins > 0 and uid:
                    ps.refund(uid, coins, "queue_cleared")
            cancelled.append(result)
    return cancelled


# ─── YouTube search pending state ─────────────────────────────────────────────

def has_pending_search(user_id: str) -> bool:
    return _rq().radio_has_pending_search(user_id)


def get_pending_search(user_id: str) -> "list | None":
    return _rq().radio_get_pending_search(user_id)


def set_pending_search(user_id: str, results: list) -> None:
    _rq().radio_set_pending_search(user_id, results)


def clear_pending_search(user_id: str) -> None:
    _rq().radio_clear_pending_search(user_id)


def search_yt(query: str, max_results: int = 5) -> list:
    return _rq().radio_search_yt(query, max_results)


# ─── Nowplaying proxy ─────────────────────────────────────────────────────────

def get_nowplaying() -> "dict | None":
    return _rq().radio_nowplaying()
