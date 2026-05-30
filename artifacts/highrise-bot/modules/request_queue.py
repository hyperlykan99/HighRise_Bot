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
  staged      — download done, waiting for /Requests slot to free up (Option A)
  done        — file uploaded + AzuraCast registered, azura_file_id set
  queued      — promoted by playback_engine; Requests playlist is active
  playing     — song is currently streaming on AzuraCast
  played      — finished playing; played_at set; file may or may not be deleted yet
  error       — pipeline failed or admin-cancelled

Write operations that need the download pipeline delegate to yt_request via
_rq() (deferred import to break circular dependency at load time).
"""
from __future__ import annotations
import time
from typing import TYPE_CHECKING

import database as db
import modules.radio_diagnostics as diag
from modules.radio_status import ACTIVE_QUEUE_STATUSES, TERMINAL_QUEUE_STATUSES

if TYPE_CHECKING:
    from highrise import BaseBot

_LOG = "[RQ]"

# All in-flight statuses used for capacity / dedup checks (full pipeline).
_ACTIVE = ACTIVE_QUEUE_STATUSES
_ACT_PH = ",".join("?" * len(_ACTIVE))
_TERMINAL = TERMINAL_QUEUE_STATUSES
_TERM_PH = ",".join("?" * len(_TERMINAL))

# Statuses shown by !queue — upcoming/waiting stages only.
# The current playing request is intentionally excluded from user-visible !q.
_DISPLAY_STATUSES = tuple(s for s in ACTIVE_QUEUE_STATUSES if s != "playing")
_DSP_PH = ",".join("?" * len(_DISPLAY_STATUSES))

# Statuses counted for queue-position / per-user limit checks.
# Excludes "playing" so the on-air song is not counted against a user's limit.
_WAITING_STATUSES = (
    "pending", "processing", "downloading", "downloaded", "uploading",
    "indexing", "staged", "ready", "queued", "submitted",
)
_WAI_PH = ",".join("?" * len(_WAITING_STATUSES))

# Statuses cancelled by !djclear / !clearqueue.
_CLEAR_STATUSES = (
    "pending", "downloading", "downloaded", "uploading",
    "staged", "ready", "queued", "submitted", "done",
)
_CLR_PH = ",".join("?" * len(_CLEAR_STATUSES))

_COLS = (
    "id", "user_id", "username", "url", "title", "status",
    "filename", "azura_file_id", "azura_song_id", "coins_charged", "started_at",
    "video_id", "artist", "priority", "source_type",
)
_SEL = (
    "id, user_id, username, url, title, status, "
    "filename, azura_file_id, azura_song_id, coins_charged, started_at, video_id, "
    "COALESCE(artist, '') AS artist, "
    "COALESCE(priority, 0) AS priority, "
    "COALESCE(source_type, '') AS source_type"
)


def _jrow(row) -> dict:
    return dict(zip(_COLS, row))


def _rq():
    """Deferred import of yt_request to avoid circular imports at load time."""
    import modules.yt_request as _m
    return _m


def _job_user_id(job_id: int) -> str:
    if not job_id:
        return ""
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                "SELECT user_id FROM yt_request_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            return (row[0] or "") if row else ""
    except Exception:
        return ""


def get_job_status(job_id: int) -> str:
    """Return the current yt_request_jobs.status for a row, or empty string."""
    if not job_id:
        return ""
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                "SELECT status FROM yt_request_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            return (row[0] or "") if row else ""
    except Exception:
        return ""


def get_job_identity(job_id: int) -> dict:
    if not job_id:
        return {}
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                "SELECT id, status, filename, azura_file_id, azura_song_id, source_type "
                "FROM yt_request_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
        if not row:
            return {}
        return {
            "id": row[0],
            "status": row[1] or "",
            "filename": row[2] or "",
            "azura_file_id": row[3] or "",
            "azura_song_id": row[4] or "",
            "source_type": row[5] or "",
        }
    except Exception:
        return {}


def get_oldest_active_unplayed_request() -> dict:
    """Return FIFO head across active unplayed queue rows. No Azura side effects."""
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                f"SELECT {_SEL} FROM yt_request_jobs "
                f"WHERE status IN ({_ACT_PH}) "
                "AND played_at IS NULL "
                "AND cleaned_at IS NULL "
                "ORDER BY id ASC LIMIT 1",
                _ACTIVE,
            ).fetchone()
        return _jrow(row) if row else {}
    except Exception as exc:
        print(f"{_LOG} get_oldest_active_unplayed_request error: {exc}")
        return {}


def is_terminal_status(status: str) -> bool:
    return (status or "").strip().lower() in _TERMINAL


def is_terminal_job(job_id: int) -> bool:
    return is_terminal_status(get_job_status(job_id))


def _log_terminal_revival_block(job_id: int, attempted_status: str, job: "dict | None" = None) -> None:
    data = job or get_job_identity(job_id)
    status = (data.get("status") or "").strip().lower()
    azura_file_id = data.get("azura_file_id", "")
    if status == "error" and attempted_status == "ready" and azura_file_id:
        return
    print(
        f"[RADIO_HARDEN] event=failed_row_not_revived"
        f" request_id={job_id}"
        f" old_status={status!r}"
        f" attempted_status={attempted_status!r}"
    )
    diag.log_radio_event(
        "terminal_guard_blocked_sync",
        request_id=job_id,
        status=status,
        attempted_status=attempted_status,
        azura_file_id=azura_file_id,
        azura_song_id=data.get("azura_song_id", ""),
        temp_path=data.get("filename", ""),
    )
    diag.log_radio_event(
        "refused_terminal_revival",
        request_id=job_id,
        status=status,
        attempted_status=attempted_status,
        azura_file_id=data.get("azura_file_id", ""),
        azura_song_id=data.get("azura_song_id", ""),
        temp_path=data.get("filename", ""),
    )


# ─── Queue writes (single DB writer facade) ──────────────────────────────────

def create_request(job: "dict | None" = None, **fields: object) -> int:
    """Insert a request row into yt_request_jobs. Returns the new row id."""
    data = dict(job or {})
    data.update(fields)
    try:
        with db.db_conn() as conn:
            cur = conn.execute(
                """INSERT INTO yt_request_jobs
                       (user_id, username, url, title, status, started_at,
                        filename, azura_file_id, azura_song_id,
                        coins_charged, payment_type, priority, source_type)
                   VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["user_id"], data["username"], data.get("url", ""),
                    data["title"], data.get("status", "pending"),
                    data.get("filename", ""),
                    data.get("azura_file_id", ""),
                    data.get("azura_song_id", ""),
                    data.get("coins_charged", 0),
                    data.get("payment_type", "free"),
                    data.get("priority", 0),
                    data.get("source_type", ""),
                ),
            )
            request_id = cur.lastrowid or 0
            diag.log_radio_event(
                "queue_event",
                request_id=request_id,
                user_id=data.get("user_id", ""),
                username=data.get("username", ""),
                title=data.get("title", ""),
                source_type=data.get("source_type", ""),
                status_transition=f"created->{data.get('status', 'pending')}",
                queue_event="create_request",
            )
            diag.log_radio_event(
                "request_created",
                request_id=request_id,
                user_id=data.get("user_id", ""),
                username=data.get("username", ""),
                title=data.get("title", ""),
                source_type=data.get("source_type", ""),
                temp_path=data.get("filename", ""),
                source_path=data.get("url", ""),
                azura_file_id=data.get("azura_file_id", ""),
                azura_song_id=data.get("azura_song_id", ""),
            )
            return request_id
    except Exception as exc:
        print(f"{_LOG} create_request error: {exc}")
        return 0


def insert_request_job(job: dict) -> int:
    """Backward-compatible alias for create_request()."""
    return create_request(job)


def update_job_fields(job_id: int, **kwargs: object) -> None:
    """Update allowed yt_request_jobs fields. Non-fatal on error."""
    if not job_id:
        return
    allowed = {
        "title", "status", "error", "finished_at", "filename",
        "azura_file_id", "azura_song_id",
        "video_id", "yt_uploader", "artist",
        "coins_charged", "payment_type", "priority", "source_type",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    next_status = str(fields.get("status") or "").strip().lower()
    if next_status and next_status in _ACTIVE:
        current = get_job_identity(job_id)
        if is_terminal_status(current.get("status", "")):
            _log_terminal_revival_block(job_id, next_status, current)
            return
    if "finished_at" in fields and isinstance(fields["finished_at"], float):
        import datetime as _dt
        fields["finished_at"] = _dt.datetime.utcfromtimestamp(
            fields["finished_at"]
        ).strftime("%Y-%m-%dT%H:%M:%S")

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [job_id]
    try:
        with db.db_conn() as conn:
            conn.execute(
                f"UPDATE yt_request_jobs SET {set_clause} WHERE id = ?",
                values,
            )
        if "status" in fields:
            diag.log_radio_event(
                "status_transition",
                request_id=job_id,
                user_id=_job_user_id(job_id),
                status_transition=f"->{fields['status']}",
            )
    except Exception as exc:
        print(f"{_LOG} update_job_fields({job_id}): {exc}")


def mark_ready(job_id: int, finished_at: "object | None" = None,
               **fields: object) -> None:
    """Mark a request ready for AzuraCast playback."""
    current = get_job_identity(job_id)
    if is_terminal_status(current.get("status", "")):
        _log_terminal_revival_block(job_id, "ready", current)
        return
    updates = dict(fields)
    updates["status"] = "ready"
    if finished_at is not None:
        updates["finished_at"] = finished_at
    update_job_fields(job_id, **updates)


def mark_submitted(job_id: int) -> bool:
    """
    Mark a ready request as queued after AzuraCast accepts submit_request().

    This makes Azura submission durable across bot restarts: a queued row can
    still be matched when it plays, but it will not be selected for submission
    again by playback_engine.
    """
    if not job_id:
        return False
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                "SELECT user_id, username, title, filename, source_type, status, "
                "azura_file_id, azura_song_id "
                "FROM yt_request_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if row and is_terminal_status(row[5]):
                _log_terminal_revival_block(job_id, "queued")
                return False
            cur = conn.execute(
                "UPDATE yt_request_jobs "
                "SET status='queued' "
                "WHERE id=? AND status='ready' "
                "AND played_at IS NULL AND cleaned_at IS NULL",
                (job_id,),
            )
        changed = bool(cur.rowcount)
        if changed:
            diag.log_radio_event(
                "status_transition",
                request_id=job_id,
                user_id=_job_user_id(job_id),
                status_transition="ready->queued",
                queue_event="azuracast_submit_accepted",
            )
            diag.log_radio_event(
                "submitted_to_azura",
                request_id=job_id,
                user_id=(row[0] if row else ""),
                username=(row[1] if row else ""),
                title=(row[2] if row else ""),
                source_type=(row[4] if row else ""),
                temp_path=(row[3] if row else ""),
                source_path="",
                azura_file_id=(row[6] if row else ""),
                azura_song_id=(row[7] if row else ""),
            )
        return changed
    except Exception as exc:
        print(f"{_LOG} mark_submitted({job_id}): {exc}")
        return False


def mark_playing(job_id: int, media_id: str = "", reason: str = "request_queue",
                 idempotent: bool = False) -> None:
    """Mark a request as currently playing."""
    if idempotent:
        mark_playing_if_not_terminal(job_id)
        return
    set_playback_status(job_id, "playing", media_id=media_id, reason=reason)


def mark_played(job_id: int, only_if_unplayed: bool = False,
                reason: str = "request_queue") -> None:
    """Mark a request as played."""
    if only_if_unplayed:
        mark_played_if_unplayed(job_id)
        return
    set_playback_status(job_id, "played", reason=reason)


def mark_failed(job_id: int, reason: str, status: str = "error",
                finished_at: "object | None" = None,
                refund_details: bool = False) -> "dict | None":
    """Mark a request failed. Optionally return refund details for callers."""
    if refund_details:
        return mark_failed_if_unplayed(job_id, reason)
    if finished_at is None:
        finished_at = time.time()
    update_job_fields(job_id, status=status, error=reason, finished_at=finished_at)
    return None


def mark_cancelled(job_id: int, reason: str = "cancelled_by_admin") -> None:
    """
    Mark a request cancelled using the table's current terminal representation.

    Existing request rows use status='error' plus an error reason for cancelled
    jobs, so this preserves behavior while providing a formal lifecycle API.
    """
    update_job_fields(job_id, status="error", error=reason)


def update_azura_ids(job_id: int, file_id: str, song_id: str) -> None:
    """Persist AzuraCast file_id and song unique_id to a request row."""
    if not job_id:
        return
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                "SELECT user_id, username, title, filename, source_type "
                "FROM yt_request_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            conn.execute(
                "UPDATE yt_request_jobs SET azura_file_id=?, azura_song_id=? WHERE id=?",
                (str(file_id), song_id, job_id),
            )
        diag.log_radio_event(
            "file_source_ready",
            request_id=job_id,
            user_id=(row[0] if row else ""),
            username=(row[1] if row else ""),
            title=(row[2] if row else ""),
            source_type=(row[4] if row else ""),
            temp_path=(row[3] if row else ""),
            source_path="",
            azura_file_id=str(file_id),
            azura_song_id=song_id,
        )
    except Exception as exc:
        print(f"{_LOG} update_azura_ids({job_id}): {exc}")


def set_azura_file_id_if_empty(job_id: int, file_id: str) -> None:
    """Backfill azura_file_id only when it is missing."""
    if not job_id or not file_id:
        return
    try:
        with db.db_conn() as conn:
            conn.execute(
                "UPDATE yt_request_jobs SET azura_file_id=?"
                " WHERE id=?"
                " AND (azura_file_id IS NULL OR azura_file_id='')",
                (str(file_id), job_id),
            )
    except Exception as exc:
        print(f"{_LOG} set_azura_file_id_if_empty({job_id}): {exc}")


def mark_cleaned(job_id: int) -> None:
    """Set cleaned_at=now on a request row."""
    if not job_id:
        return
    try:
        with db.db_conn() as conn:
            conn.execute(
                "UPDATE yt_request_jobs SET cleaned_at=datetime('now') WHERE id=?",
                (job_id,),
            )
        diag.log_radio_event(
            "cleanup_event",
            request_id=job_id,
            user_id=_job_user_id(job_id),
            cleanup_event="mark_cleaned",
        )
    except Exception as exc:
        print(f"{_LOG} mark_cleaned({job_id}): {exc}")


def mark_played_if_unplayed(job_id: int) -> None:
    """Set status='played' and played_at=now only if played_at is still NULL."""
    if not job_id:
        return
    try:
        with db.db_conn() as conn:
            conn.execute(
                """UPDATE yt_request_jobs
                      SET status='played', played_at=datetime('now')
                    WHERE id=? AND played_at IS NULL""",
                (job_id,),
            )
    except Exception as exc:
        print(f"{_LOG} mark_played_if_unplayed({job_id}): {exc}")


def mark_playing_if_not_terminal(job_id: int) -> None:
    """Set status='playing' unless the row is already terminal."""
    if not job_id:
        return
    try:
        with db.db_conn() as conn:
            conn.execute(
                "UPDATE yt_request_jobs SET status='playing'"
                f" WHERE id=? AND status NOT IN ({_TERM_PH})",
                (job_id, *_TERMINAL),
            )
    except Exception as exc:
        print(f"{_LOG} mark_playing_if_not_terminal({job_id}): {exc}")


def set_playback_status(job_id: int, status: str, media_id: str = "",
                        reason: str = "request_queue") -> None:
    """Set a playback lifecycle status using playback_engine's prior semantics."""
    if not job_id:
        return
    try:
        changed = False
        with db.db_conn() as conn:
            if status == "played":
                cur = conn.execute(
                    "UPDATE yt_request_jobs "
                    f"SET status='played', played_at=COALESCE(played_at, datetime('now')) "
                    f"WHERE id=? AND status NOT IN ({_TERM_PH})",
                    (job_id, *_TERMINAL),
                )
                changed = bool(cur.rowcount)
            elif status == "playing":
                if media_id:
                    cur = conn.execute(
                        "UPDATE yt_request_jobs "
                        "SET status='playing', started_at=datetime('now'), "
                        "azura_file_id=CASE WHEN (azura_file_id IS NULL OR azura_file_id='') "
                        "THEN ? ELSE azura_file_id END "
                        f"WHERE id=? AND status NOT IN ({_TERM_PH})",
                        (media_id, job_id, *_TERMINAL),
                    )
                else:
                    cur = conn.execute(
                        "UPDATE yt_request_jobs "
                        f"SET status='playing', started_at=datetime('now') "
                        f"WHERE id=? AND status NOT IN ({_TERM_PH})",
                        (job_id, *_TERMINAL),
                    )
                changed = bool(cur.rowcount)
            else:
                cur = conn.execute(
                    "UPDATE yt_request_jobs SET status=? WHERE id=?",
                    (status, job_id),
                )
                changed = bool(cur.rowcount)
        if not changed:
            print(
                f"[RADIO_STATUS] job={job_id} unchanged status={status!r}"
                f" reason={reason}"
            )
            return
        print(f"[RADIO_STATUS] job={job_id} new={status!r} reason={reason}")
        diag.log_radio_event(
            "status_transition",
            request_id=job_id,
            user_id=_job_user_id(job_id),
            status_transition=f"->{status}",
            queue_event=reason,
        )
    except Exception as exc:
        print(f"{_LOG} set_playback_status({job_id},{status!r}): {exc}")


def mark_failed_if_unplayed(job_id: int, reason: str) -> "dict | None":
    """
    Mark an unplayed request as error and return refund details for the caller.

    Returns None when the row is missing or already played.
    """
    if not job_id:
        return None
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                "SELECT user_id, username, coins_charged, status, played_at "
                "FROM yt_request_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if not row:
                return None
            uid, username, coins, status, played_at = row
            if played_at or status == "played":
                return None
            conn.execute(
                "UPDATE yt_request_jobs SET status='error', error=?, finished_at=datetime('now') "
                "WHERE id=? AND status!='played' AND played_at IS NULL",
                (reason, job_id),
            )
            diag.log_radio_event(
                "status_transition",
                request_id=job_id,
                user_id=uid,
                status_transition=f"{status}->error",
                queue_event=reason,
            )
            return {
                "user_id": uid,
                "username": username,
                "coins_charged": int(coins or 0),
                "status": status,
                "played_at": played_at,
            }
    except Exception as exc:
        print(f"{_LOG} mark_failed_if_unplayed({job_id},{reason!r}): {exc}")
        return None


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


def display_jobs() -> list:
    """
    Jobs shown by !queue — all visible in-flight stages.

    Includes: pending, downloading, downloaded, uploading, staged, ready,
    playing.  Excludes played/error (terminal).

    "playing" rows appear at the top of !queue as ▶️ NOW PLAYING.
    "staged"  rows are downloaded but waiting for AzuraCast slot (📦).
    "ready"   rows are uploaded and waiting to stream (✅).

    Oldest-first so queue position numbers are stable.
    stage=queue_read is logged by the calling command handler.
    """
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                f"SELECT {_SEL} FROM yt_request_jobs "
                f"WHERE status IN ({_DSP_PH}) AND played_at IS NULL "
                "AND TRIM(COALESCE(title, '')) != '' "
                "ORDER BY id ASC",
                _DISPLAY_STATUSES,
            ).fetchall()
            return [_jrow(r) for r in rows]
    except Exception as exc:
        print(f"{_LOG} display_jobs error: {exc}")
        return []


def sync_indexed_active_statuses() -> int:
    """
    Repair rows stuck in a preparing-style status after AzuraCast identifiers
    were already persisted. Display-only callers may run this before rendering.
    """
    stuck = (
        "pending", "processing", "downloading", "downloaded",
        "uploading", "indexing", "staged",
    )
    ph = ",".join("?" * len(stuck))
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                f"SELECT {_SEL} FROM yt_request_jobs "
                f"WHERE status IN ({ph}) AND played_at IS NULL "
                "AND cleaned_at IS NULL "
                "AND (COALESCE(azura_file_id,'')!='' OR COALESCE(azura_song_id,'')!='')",
                stuck,
            ).fetchall()
            jobs = [_jrow(r) for r in rows]
            synced_jobs = []
            for job in jobs:
                if is_terminal_status(job.get("status", "")):
                    _log_terminal_revival_block(job["id"], "ready", job)
                    continue
                fn = (job.get("filename") or "").strip()
                source = (job.get("source_type") or "").strip()
                if source not in ("youtube", "local_replay", "local_copy", "local", "local_favorite"):
                    continue
                if source != "youtube" and not (
                    fn.startswith("tmp_replay_") or fn.startswith("local_request_")
                ):
                    continue
                conn.execute(
                    "UPDATE yt_request_jobs SET status='ready' WHERE id=?",
                    (job["id"],),
                )
                synced_jobs.append(job)
        for job in synced_jobs:
            diag.log_radio_event(
                "queue_status_sync",
                request_id=job["id"],
                user_id=job.get("user_id", ""),
                username=job.get("username", ""),
                title=job.get("title", ""),
                source_type=job.get("source_type", ""),
                temp_path=job.get("filename", ""),
                azura_file_id=job.get("azura_file_id", ""),
                azura_song_id=job.get("azura_song_id", ""),
                status_transition=f"{job.get('status', '')}->ready",
            )
            diag.log_radio_event(
                "azura_index_confirmed",
                request_id=job["id"],
                user_id=job.get("user_id", ""),
                username=job.get("username", ""),
                title=job.get("title", ""),
                source_type=job.get("source_type", ""),
                temp_path=job.get("filename", ""),
                azura_file_id=job.get("azura_file_id", ""),
                azura_song_id=job.get("azura_song_id", ""),
            )
        return len(synced_jobs)
    except Exception as exc:
        print(f"{_LOG} sync_indexed_active_statuses error: {exc}")
        return 0


def render_added_to_queue_message(
    *,
    title: str = "",
    artist: str = "",
    position: int = 0,
    priority: int = 0,
    staff_free: bool = False,
    plays_left: "int | None" = None,
) -> str:
    """Shared request acceptance whisper for YouTube and local favorites."""
    lines = ["⭐ Priority added" if priority else "✅ Added to queue"]
    title = (title or "").strip()[:50]
    artist = (artist or "").strip()[:28]
    if title:
        lines.append(f"Title: {title}")
    if artist:
        lines.append(f"Artist: {artist}")
    lines.append(f"Position: #{position or '?'}")
    if staff_free:
        lines.append("🛠️ Staff: Free")
    elif priority:
        lines.append("⭐ Priority")
    elif plays_left is not None:
        lines.append(f"💿 Plays left: {int(plays_left)}")
    else:
        lines.append("Cost: Free")
    lines.append("Please wait…")
    return "\n".join(lines)[:249]


def mark_as_playing(job_id: int) -> None:
    """
    Mark a request as playing in the DB.

    Used by the !queue NP filter when a display-list item is found to already
    be streaming — updates status and sets started_at if not already set.
    Safe to call multiple times (idempotent via COALESCE).
    """
    if not job_id:
        return
    try:
        with db.db_conn() as conn:
            conn.execute(
                "UPDATE yt_request_jobs "
                "SET status='playing', "
                "    started_at=COALESCE(NULLIF(started_at,''), datetime('now')) "
                "WHERE id=? AND status NOT IN ('played','error')",
                (job_id,),
            )
        print(
            f"[RADIO_STATUS] job={job_id} new=playing"
            f" reason=mark_as_playing"
        )
    except Exception as exc:
        print(f"{_LOG} mark_as_playing({job_id}): {exc}")


def mark_as_played(job_id: int) -> None:
    """
    Mark a request as finished/played in the DB.  Sets played_at=now.
    Idempotent — safe to call even if already played.
    Used by !skip and queue_audit to move rows out of display immediately.
    """
    if not job_id:
        return
    try:
        with db.db_conn() as conn:
            conn.execute(
                "UPDATE yt_request_jobs "
                "SET status='played', "
                "    played_at=COALESCE(NULLIF(played_at,''), datetime('now')) "
                "WHERE id=? AND status NOT IN ('error')",
                (job_id,),
            )
        print(
            f"[RADIO_STATUS] job={job_id} new=played"
            f" reason=mark_as_played"
        )
    except Exception as exc:
        print(f"{_LOG} mark_as_played({job_id}): {exc}")


def stale_playing_jobs() -> list:
    """
    Return all jobs with status='playing' and played_at IS NULL.
    Used by the queue audit to detect rows that are stuck as 'playing'
    after the bot restarted or the poll loop missed the song change.
    """
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                f"SELECT {_SEL} FROM yt_request_jobs "
                "WHERE status='playing' AND played_at IS NULL "
                "ORDER BY id ASC",
            ).fetchall()
            return [_jrow(r) for r in rows]
    except Exception as exc:
        print(f"{_LOG} stale_playing_jobs error: {exc}")
        return []


def queue_clear_all(command: str = "clearqueue", refund: bool = True) -> dict:
    """
    Cancel every clearable job (status in _CLEAR_STATUSES).

    This is the single authoritative implementation used by BOTH
    !djclear (dj_music.py) and !clearqueue (radio_commands.py).
    Calling either command always touches the same rows.

    Steps
    ─────
    1. Snapshot clearable jobs before any writes.
    2. Cancel each job via cancel_job() — DB update to status='error'.
    3. Refund coins if refund=True (via payment_service).
    4. File cleanup:
       - staged jobs  → delete local staging file (request_staging/<filename>)
       - done/queued  → delete AzuraCast media (API first, SFTP fallback)
       Never deletes the currently-playing job.
    5. Count remaining after all cancellations.
    6. Emit structured log: stage=queue_clear.

    Returns
    ───────
    {
        "count_before":   int,   # jobs that were clearable
        "count_after":    int,   # should be 0 on success
        "refunded_coins": int,
        "cancelled_ids":  list[int],
    }

    Synchronous — call via loop.run_in_executor from async handlers.
    """
    import os
    import modules.payment_service as ps
    import modules.azuracast_controller as azura

    # ── 1. Snapshot ───────────────────────────────────────────────────────────
    try:
        with db.db_conn() as conn:
            before_rows = conn.execute(
                f"SELECT {_SEL} FROM yt_request_jobs "
                f"WHERE status IN ({_CLR_PH}) AND played_at IS NULL "
                "ORDER BY id ASC",
                _CLEAR_STATUSES,
            ).fetchall()
    except Exception as exc:
        print(f"{_LOG} stage=queue_clear command={command} snapshot_error={exc!r}")
        before_rows = []

    jobs         = [_jrow(r) for r in before_rows]
    count_before = len(jobs)

    print(
        f"{_LOG} stage=queue_clear command={command}"
        f" statuses={list(_CLEAR_STATUSES)!r}"
        f" count_before={count_before}"
    )

    refunded_coins = 0
    cancelled_ids: list = []

    for j in jobs:
        jid    = j["id"]
        uid    = j.get("user_id", "") or ""
        coins  = int(j.get("coins_charged") or 0)
        fn     = (j.get("filename")      or "").strip()
        fid    = (j.get("azura_file_id") or "").strip()
        status = j.get("status", "")

        # ── 2. Cancel in DB ───────────────────────────────────────────────────
        cancel_job(jid, "cleared_by_admin")
        cancelled_ids.append(jid)

        # ── 3. Refund ─────────────────────────────────────────────────────────
        if refund and coins > 0 and uid:
            try:
                ps.refund(uid, coins, "queue_cleared")
                refunded_coins += coins
                diag.log_radio_event(
                    "refund",
                    request_id=jid,
                    user_id=uid,
                    coins=coins,
                    reason="queue_cleared",
                )
            except Exception as exc:
                print(
                    f"{_LOG} stage=queue_clear command={command}"
                    f" refund_error jid={jid} err={exc!r}"
                )

        # ── 4. File cleanup ───────────────────────────────────────────────────
        if status in ("staged", "downloaded") and fn:
            # Local staging file — downloaded but not yet in AzuraCast; delete from disk.
            try:
                from modules.yt_request import STAGING_DIR as _sd
                staged_path = os.path.join(_sd, fn)
                if os.path.isfile(staged_path):
                    os.remove(staged_path)
                    print(
                        f"{_LOG} stage=queue_clear command={command}"
                        f" staging_delete=ok jid={jid} fn={fn!r}"
                    )
            except Exception as exc:
                print(
                    f"{_LOG} stage=queue_clear command={command}"
                    f" staging_delete_error jid={jid} fn={fn!r} err={exc!r}"
                )

        elif status in ("done", "queued", "ready") and fn:
            # Uploaded file in AzuraCast /Requests — delete via API, SFTP fallback.
            ok = False
            if fid:
                try:
                    ok = azura.delete_media_file(fid)
                    print(
                        f"{_LOG} stage=queue_clear command={command}"
                        f" api_delete={'ok' if ok else 'fail'}"
                        f" jid={jid} fid={fid!r}"
                    )
                except Exception as exc:
                    print(
                        f"{_LOG} stage=queue_clear command={command}"
                        f" api_delete_error jid={jid} fid={fid!r} err={exc!r}"
                    )
            if not ok and fn:
                try:
                    if "/" in fn or "\\" in fn:
                        diag.log_radio_event(
                            "cleanup_safety_skip",
                            request_id=jid,
                            user_id=uid,
                            temp_path=fn,
                            source_path="",
                        )
                        print(
                            f"{_LOG} stage=queue_clear command={command}"
                            f" sftp_delete=skipped_safety jid={jid} fn={fn!r}"
                        )
                    else:
                        ok2 = azura.sftp_delete_file(fn)
                        print(
                            f"{_LOG} stage=queue_clear command={command}"
                            f" sftp_delete={'ok' if ok2 else 'fail'}"
                            f" jid={jid} fn={fn!r}"
                        )
                except Exception as exc:
                    print(
                        f"{_LOG} stage=queue_clear command={command}"
                        f" sftp_delete_error jid={jid} fn={fn!r} err={exc!r}"
                    )

    # ── 5. Count after ────────────────────────────────────────────────────────
    try:
        with db.db_conn() as conn:
            count_after = conn.execute(
                f"SELECT COUNT(*) FROM yt_request_jobs "
                f"WHERE status IN ({_CLR_PH}) AND played_at IS NULL",
                _CLEAR_STATUSES,
            ).fetchone()[0]
    except Exception:
        count_after = 0

    print(
        f"{_LOG} stage=queue_clear command={command}"
        f" count_before={count_before} count_after={count_after}"
        f" refunded_coins={refunded_coins}"
    )

    return {
        "count_before":   count_before,
        "count_after":    count_after,
        "refunded_coins": refunded_coins,
        "cancelled_ids":  cancelled_ids,
    }


def active_count() -> int:
    """Count of real active request workload, excluding terminal rows."""
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


def user_active_count(user_id: str) -> int:
    """Count of in-flight jobs for a specific user (pending → playing)."""
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM yt_request_jobs "
                f"WHERE user_id=? AND status IN ({_ACT_PH}) AND played_at IS NULL",
                (user_id, *_ACTIVE),
            ).fetchone()
            return row[0] if row else 0
    except Exception as exc:
        print(f"{_LOG} user_active_count error: {exc}")
        return 0


def future_count() -> int:
    """
    Count of jobs waiting to play (pending→staged→ready, excludes playing).

    Excludes the currently-playing request and all terminal statuses so the
    queue position shown to users never includes the song already on air.
    """
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM yt_request_jobs "
                f"WHERE status IN ({_WAI_PH}) AND played_at IS NULL",
                _WAITING_STATUSES,
            ).fetchone()
            return row[0] if row else 0
    except Exception as exc:
        print(f"{_LOG} future_count error: {exc}")
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
    priority: int = 0,
) -> int:
    """Create a job record and launch the yt-dlp → SFTP → AzuraCast pipeline."""
    return int(_rq().radio_submit_job(bot, user_id, username, url, coins_charged, payment_type, priority) or 0)


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
            mark_cancelled(jid, reason)
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
                    diag.log_radio_event(
                        "refund",
                        request_id=j["id"],
                        user_id=uid,
                        coins=coins,
                        reason="queue_cleared",
                    )
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
