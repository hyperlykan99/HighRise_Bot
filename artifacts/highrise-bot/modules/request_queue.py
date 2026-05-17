"""
modules/request_queue.py
------------------------
Standalone, DB-backed radio request queue.

All queue state is read directly from the yt_request_jobs table — no dependency
on yt_request's in-memory _jobs dict.  This means the queue survives bot restarts
and crashes without losing any pending requests.

Queue statuses used by this system (superset of yt_request originals):
  pending     — job created, waiting for pipeline
  downloading — yt-dlp step running
  uploading   — SFTP step running
  done        — file uploaded + AzuraCast registered, azura_file_id set
  queued      — promoted by playback_engine; Requests playlist is active
  playing     — song is currently streaming on AzuraCast
  played      — finished playing; played_at set; file may or may not be deleted yet
  error       — pipeline failed or admin-cancelled

Write operations that need the download pipeline delegate to yt_request via
_rq() (deferred import to break circular dependency at load time).
"""
from __future__ import annotations
from typing import TYPE_CHECKING

import database as db

if TYPE_CHECKING:
    from highrise import BaseBot

_LOG = "[RQ]"

_ACTIVE = ("pending", "downloading", "uploading", "done", "queued", "playing")
_ACT_PH = ",".join("?" * len(_ACTIVE))

_COLS = (
    "id", "user_id", "username", "url", "title", "status",
    "filename", "azura_file_id", "azura_song_id", "coins_charged", "started_at",
)
_SEL = (
    "id, user_id, username, url, title, status, "
    "filename, azura_file_id, azura_song_id, coins_charged, started_at"
)


def _jrow(row) -> dict:
    return dict(zip(_COLS, row))


def _rq():
    """Deferred import of yt_request to avoid circular imports at load time."""
    import modules.yt_request as _m
    return _m


# ─── Queue reads (DB-based, restart-safe) ─────────────────────────────────────

def pending_jobs() -> list:
    """
    All in-flight jobs (from 'pending' through 'playing'), oldest first.
    Excludes already-played and cleaned-up entries.
    """
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                f"SELECT {_SEL} FROM yt_request_jobs "
                f"WHERE status IN ({_ACT_PH}) AND played_at IS NULL "
                "ORDER BY id ASC",
                _ACTIVE,
            ).fetchall()
            return [_jrow(r) for r in rows]
    except Exception as exc:
        print(f"{_LOG} pending_jobs error: {exc}")
        return []


def active_count() -> int:
    """Count of all in-flight jobs (pending → playing)."""
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM yt_request_jobs "
                f"WHERE status IN ({_ACT_PH}) AND played_at IS NULL",
                _ACTIVE,
            ).fetchone()
            return row[0] if row else 0
    except Exception as exc:
        print(f"{_LOG} active_count error: {exc}")
        return 0


def user_pending_count(user_id: str) -> int:
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM yt_request_jobs "
                f"WHERE status IN ({_ACT_PH}) AND user_id=? AND played_at IS NULL",
                (*_ACTIVE, user_id),
            ).fetchone()
            return row[0] if row else 0
    except Exception as exc:
        print(f"{_LOG} user_pending_count error: {exc}")
        return 0


def currently_playing() -> "dict | None":
    """
    Return the job currently playing on AzuraCast, or None.
    Asks the playback engine first (fast, in-memory), falls back to DB query.
    """
    try:
        from modules.playback_engine import get_current_request
        return get_current_request()
    except Exception:
        pass
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                f"SELECT {_SEL} FROM yt_request_jobs "
                "WHERE status='playing' AND played_at IS NULL "
                "ORDER BY id DESC LIMIT 1",
            ).fetchone()
            return _jrow(row) if row else None
    except Exception as exc:
        print(f"{_LOG} currently_playing error: {exc}")
        return None


def recent_history(limit: int = 10) -> list:
    """Last `limit` played requests, newest first."""
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT id, user_id, username, title, coins_charged, played_at "
                "FROM yt_request_jobs "
                "WHERE played_at IS NOT NULL "
                "ORDER BY played_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            cols = ("id", "user_id", "username", "title", "coins_charged", "played_at")
            return [dict(zip(cols, r)) for r in rows]
    except Exception as exc:
        print(f"{_LOG} recent_history error: {exc}")
        return []


# ─── Dedup + ban checks (delegate to yt_request) ─────────────────────────────

def check_dedup(url: str, window_secs: int) -> "dict | None":
    return _rq().radio_check_dedup(url, window_secs)


def is_banned_requester(username: str) -> bool:
    return _rq().radio_check_banned_requester(username)


# ─── SFTP readiness (delegate to yt_request) ─────────────────────────────────

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
    """Create a job record and launch the yt-dlp → SFTP → AzuraCast pipeline."""
    _rq().radio_submit_job(bot, user_id, username, url, coins_charged, payment_type)


def cancel_job(jid: int, reason: str = "cancelled_by_admin") -> "dict | None":
    """
    Cancel an in-flight job by its yt_request_jobs.id (DB primary key).

    Tries the yt_request in-memory path first (handles pending/downloading/uploading).
    Falls back to a direct DB update for jobs already past the pipeline
    (done/queued) that only exist in the DB.

    Returns the job dict for the caller to issue refunds, or None if not found.
    """
    result = _rq().radio_cancel_job(jid, reason)
    if result:
        return result

    try:
        with db.db_conn() as conn:
            row = conn.execute(
                f"SELECT {_SEL} FROM yt_request_jobs "
                f"WHERE id=? AND status IN ({_ACT_PH})",
                (jid, *_ACTIVE),
            ).fetchone()
            if row:
                job = _jrow(row)
                conn.execute(
                    "UPDATE yt_request_jobs SET status='error', error=? WHERE id=?",
                    (reason, jid),
                )
                return job
    except Exception as exc:
        print(f"{_LOG} cancel_job DB fallback error: {exc}")
    return None


def clear_all_pending(refund: bool = True) -> list:
    """
    Cancel every in-flight job.  If refund=True, issues coin refunds.
    Returns list of cancelled job dicts.
    """
    import modules.payment_service as ps

    jobs      = pending_jobs()
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


# ─── YouTube search state (delegates to yt_request in-memory dict) ────────────

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


# ─── AzuraCast nowplaying proxy ───────────────────────────────────────────────

def get_nowplaying() -> "dict | None":
    return _rq().radio_nowplaying()
