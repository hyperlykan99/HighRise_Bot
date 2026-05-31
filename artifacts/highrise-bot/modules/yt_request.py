"""
modules/yt_request.py
---------------------
!ytrequest <url>  — download YouTube audio → SFTP → AzuraCast Requests playlist
!ytnow            — show the latest successfully requested song (public)
!ytqueue          — show pending/recent jobs from DB (admin+)
!ytstatus         — SFTP + API config readiness and session stats (admin+)
!ytcooldown       — show cooldown / length-limit settings (admin+)
!setytcooldown <s>— set per-user cooldown in seconds (admin+)

BOT_MODE = dj only.  DJ_DUDU is the sole owner of these commands.

Validation per request:
    • Must be a single YouTube video URL (no playlists)
    • Livestreams are rejected
    • Videos longer than _MAX_DURATION_SECS (10 min) are rejected
    • Same URL blocked for _DEDUP_WINDOW_SECS (24 h) after a successful upload
    • Per-user cooldown: owner=0 s, admin=30 s, user=configurable (default 300 s)

Pipeline per request:
    1. Pre-flight: fetch metadata via yt-dlp (no download) → validate
    2. Download best-audio + ffmpeg → mp3 in tmpdir
    3. paramiko SFTP put → Requests/<id>.mp3
    4. Room chat announcement: 🎵 Added to radio: <title> — requested by @<user>
    5. AzuraCast API: rescan → search → playlist-add → request-queue
    6. All jobs logged to yt_request_jobs DB table (persistent history)
    7. Temp files cleaned up whether or not the upload succeeds

Required env vars:
    AZURA_SFTP_HOST   AZURA_SFTP_USER   AZURA_SFTP_PASS

Optional env vars:
    AZURA_SFTP_PORT (default 22)   AZURA_SFTP_PATH (default "Requests")
    AZURA_BASE_URL   AZURA_API_KEY   AZURA_STATION_ID (default "1")
    AZURA_MEDIA_DIR  AZURA_PLAYLIST_ID
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from typing import TYPE_CHECKING, Callable

import config as _config
import database as db
from modules import dashboard_settings as _dash
import modules.config_store as cs
from modules.permissions import is_admin, is_owner, is_manager
from modules.radio_status import ACTIVE_QUEUE_STATUSES
import modules.request_queue as rq
import modules.radio_diagnostics as diag
from modules.msg_utils import safe_send as _safe_send_mu

# DB file path — config.DB_PATH reads SHARED_DB_PATH env var (default highrise_hangout.db)
_DB_PATH: str = _config.DB_PATH

# Local staging directory — downloaded files wait here when /Requests slot is occupied (Option A).
# Created automatically on first use.  Path: artifacts/highrise-bot/request_staging/
STAGING_DIR: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "request_staging"
)
try:
    os.makedirs(STAGING_DIR, exist_ok=True)
except Exception:
    pass

if TYPE_CHECKING:
    from highrise import BaseBot, User

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await _safe_send_mu(bot, msg, whisper_target=uid, max_chars=240)
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# URL validation
# ─────────────────────────────────────────────────────────────────────────────

_YT_RE = re.compile(
    r"^https?://(?:www\.)?"
    r"(?:"
    r"youtube\.com/watch\?(?:.*&)?v=[\w\-]{11}"
    r"|youtu\.be/[\w\-]{11}"
    r"|youtube\.com/shorts/[\w\-]{11}"
    r")"
)

def _is_youtube_url(url: str) -> bool:
    return bool(_YT_RE.match(url))


def _is_youtube_playlist_url(url: str) -> bool:
    u = (url or "").lower()
    return (
        "youtube.com/playlist" in u
        or "list=" in u
        or "/mix" in u
        or "start_radio=1" in u
    )

# ─────────────────────────────────────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_COOLDOWN  = 300   # seconds
_MAX_ACTIVE_JOBS   = 20    # legacy fallback; runtime limit is config_store
_MAX_DURATION_SECS = 600   # 10 minutes — reject longer videos
_DEDUP_WINDOW_SECS = 86400 # 24 hours  — block re-requests of same URL

def _cooldown_secs() -> int:
    try:
        return max(30, int(db.get_room_setting("yt_request_cooldown", str(_DEFAULT_COOLDOWN))))
    except Exception:
        return _DEFAULT_COOLDOWN

# ─────────────────────────────────────────────────────────────────────────────
# Payment + VIP + presence settings helpers
# ─────────────────────────────────────────────────────────────────────────────

def _request_cost() -> int:
    """Chill coins charged to non-VIP users per request (0 = free for all)."""
    try:
        return max(0, int(db.get_room_setting("request_cost_coins", "500")))
    except Exception:
        return 500

def _priority_cost_tickets() -> int:
    """Luxe tickets for priority queue slot (0 = disabled)."""
    try:
        return max(0, int(db.get_room_setting("request_priority_cost_tickets", "100")))
    except Exception:
        return 100

def _vip_free_requests() -> bool:
    return db.get_room_setting("vip_free_requests", "true").lower() == "true"

def _vip_priority() -> bool:
    return db.get_room_setting("vip_priority", "true").lower() == "true"

def _skip_if_requester_leaves() -> bool:
    return db.get_room_setting("skip_if_requester_leaves", "true").lower() == "true"

def _refund_if_leaves() -> bool:
    return db.get_room_setting("refund_if_requester_leaves", "true").lower() == "true"

def _admin_ignore_leave() -> bool:
    return db.get_room_setting("admin_requests_ignore_leave", "true").lower() == "true"

def _is_vip(user_id: str) -> bool:
    try:
        return bool(db.owns_item(user_id, "vip"))
    except Exception:
        return False

def _charge_coins(user_id: str, amount: int) -> bool:
    """Deduct coins from user. Returns True if successful (had enough)."""
    if amount <= 0:
        return True
    try:
        bal = db.get_balance(user_id)
        if bal < amount:
            return False
        db.adjust_balance(user_id, -amount)
        return True
    except Exception as exc:
        print(f"[YT_PAY] charge error for {user_id}: {exc}")
        return False

def _refund_coins(user_id: str, amount: int) -> None:
    """Refund coins to user (non-fatal)."""
    if amount <= 0:
        return
    try:
        db.adjust_balance(user_id, amount)
        diag.log_radio_event(
            "refund",
            user_id=user_id,
            coins=amount,
            reason="yt_request_refund",
        )
    except Exception as exc:
        print(f"[YT_PAY] refund error for {user_id}: {exc}")

# ─────────────────────────────────────────────────────────────────────────────
# Requester presence tracking
# ─────────────────────────────────────────────────────────────────────────────

_presence_lock = threading.Lock()
_active_presence: dict[str, dict] = {}
# user_id → {db_id, job_id, coins_charged, payment_type, username}

# Set by nowplaying cycle the moment a request starts; cleared when it ends
_currently_playing_db_id: int = 0

_REQUIRED_SFTP_VARS = ("AZURA_SFTP_HOST", "AZURA_SFTP_USER", "AZURA_SFTP_PASS")
_DEFAULT_SFTP_PATH  = "Requests"

def _sftp_cfg() -> dict:
    # AZURA_SFTP_PATH — absolute remote directory on the AzuraCast server.
    # If unset, defaults to _DEFAULT_SFTP_PATH.
    # Note: AZURA_REQUESTS_FOLDER is intentionally NOT used here — it was the
    # old combined env var; use AZURA_SFTP_PATH for the upload path and
    # AZURA_MEDIA_DIR for the API rescan directory.
    sftp_path = (os.environ.get("AZURA_SFTP_PATH") or _DEFAULT_SFTP_PATH).strip()
    return {
        "host":   (os.environ.get("AZURA_SFTP_HOST") or "").strip(),
        "port":   int((os.environ.get("AZURA_SFTP_PORT") or "22").strip() or "22"),
        "user":   (os.environ.get("AZURA_SFTP_USER") or "").strip(),
        "passwd": (os.environ.get("AZURA_SFTP_PASS") or "").strip(),
        "folder": sftp_path,
    }

def _sftp_missing_vars() -> list[str]:
    missing = []
    for var in _REQUIRED_SFTP_VARS:
        raw = os.environ.get(var)
        if not raw or not raw.strip():
            missing.append(var)
    return missing

def _sftp_ready() -> bool:
    return len(_sftp_missing_vars()) == 0


def _has_requests_slot_free() -> bool:
    """
    True when no job currently has an active file in the /Requests SFTP folder.

    A 'slot' is occupied when any job has status IN ('queued','playing')
    AND azura_file_id is set (meaning a file was successfully registered with
    AzuraCast).  Called before SFTP upload to decide whether to upload now or
    save to STAGING_DIR (Option A: one active request in /Requests at a time).
    """
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM yt_request_jobs "
                "WHERE status IN ('queued','playing') "
                "  AND azura_file_id != '' AND played_at IS NULL",
            ).fetchone()
        return (row[0] if row else 0) == 0
    except Exception:
        return True   # err on side of allowing upload so requests never stall

def _log_sftp_env() -> None:
    """Log SFTP config at startup. Host printed as-is; credentials show length only."""
    host = (os.environ.get("AZURA_SFTP_HOST") or "").strip()
    port = (os.environ.get("AZURA_SFTP_PORT") or "22").strip()
    if host:
        print(f"[YT_REQUEST] AZURA_SFTP_HOST = {host}:{port}")
    else:
        print("[YT_REQUEST] AZURA_SFTP_HOST: NOT SET  ← required, YT requests disabled")
    for var in ("AZURA_SFTP_USER", "AZURA_SFTP_PASS"):
        raw = os.environ.get(var)
        if raw and raw.strip():
            print(f"[YT_REQUEST] {var}: SET (len={len(raw.strip())})")
        else:
            status = "EMPTY" if raw is not None else "NOT SET"
            print(f"[YT_REQUEST] {var}: {status}  ← required, YT requests disabled")
    sftp_path = (os.environ.get("AZURA_SFTP_PATH") or _DEFAULT_SFTP_PATH).strip()
    print(f"[YT_REQUEST] SFTP upload path = {sftp_path}")
    # API post-upload step (optional)
    base_url  = (os.environ.get("AZURA_BASE_URL") or "").strip()
    api_key   = (os.environ.get("AZURA_API_KEY") or "").strip()
    sid       = (os.environ.get("AZURA_STATION_ID") or "1").strip()
    media_dir = (os.environ.get("AZURA_MEDIA_DIR") or "").strip()
    if base_url and api_key:
        dir_disp = media_dir or "(root — full rescan)"
        print(f"[YT_REQUEST] Post-upload API: ENABLED → {base_url}  station={sid}  media_dir={dir_disp}")
    else:
        print("[YT_REQUEST] Post-upload API: DISABLED (AZURA_BASE_URL / AZURA_API_KEY not set)")

# Log SFTP + API config once at import (visible in workflow console)
_log_sftp_env()

# ─────────────────────────────────────────────────────────────────────────────
# In-memory job tracker
# ─────────────────────────────────────────────────────────────────────────────

_jobs_lock = threading.Lock()
_jobs: dict[int, dict] = {}
_next_job_id = 1

# ── Background prepare pipeline semaphores ────────────────────────────────────
_download_sem     = asyncio.Semaphore(2)   # max 2 concurrent downloads
_upload_sem       = asyncio.Semaphore(1)   # max 1 concurrent upload
_prep_active_jids: set = set()             # job IDs currently in _run_job pipeline
_prep_ids_lock    = threading.Lock()       # guards _prep_active_jids

def _new_job(user_id: str, username: str, url: str,
             coins_charged: int = 0, payment_type: str = "free",
             priority: int = 0) -> dict:
    global _next_job_id
    with _jobs_lock:
        jid = _next_job_id
        _next_job_id += 1
        job: dict = {
            "id":            jid,
            "db_id":         0,
            "user_id":       user_id,
            "username":      username,
            "url":           url,
            "status":        "pending",
            "title":         "",
            "error":         "",
            "started_at":    time.time(),
            "finished_at":   None,
            "coins_charged": coins_charged,
            "payment_type":  payment_type,
            "priority":      priority,
            "source_type":   "youtube",
        }
        _jobs[jid] = job
    # Persist outside the lock (non-fatal if DB is unavailable)
    db_id = _db_insert_job(job)
    with _jobs_lock:
        _jobs[jid]["db_id"] = db_id
    return _jobs[jid]

def _update_job(jid: int, **kwargs: object) -> None:
    db_id = 0
    with _jobs_lock:
        if jid in _jobs:
            _jobs[jid].update(kwargs)
            db_id = _jobs[jid].get("db_id", 0)
    if db_id:
        _db_update_job(db_id, **kwargs)

# ─────────────────────────────────────────────────────────────────────────────
# DB persistence helpers  (non-fatal — errors are logged and swallowed)
# ─────────────────────────────────────────────────────────────────────────────

def _db_insert_job(job: dict) -> int:
    """Insert a pending job into yt_request_jobs. Returns new DB row id (0 on error)."""
    return rq.create_request(job)


def _db_update_job(db_id: int, **kwargs: object) -> None:
    """Update a yt_request_jobs row by DB id (non-fatal on error)."""
    status = kwargs.get("status")
    if status == "ready":
        fields = {k: v for k, v in kwargs.items() if k != "status"}
        rq.mark_ready(db_id, **fields)
        return
    if status in ("error", "failed_download"):
        fields = {k: v for k, v in kwargs.items() if k not in ("status", "error")}
        rq.mark_failed(
            db_id,
            str(kwargs.get("error") or status),
            status=str(status),
            **fields,
        )
        return
    if status == "playing":
        rq.mark_playing(db_id, idempotent=True)
        return
    if status == "played":
        rq.mark_played(db_id, only_if_unplayed=True)
        return
    rq.update_job_fields(db_id, **kwargs)


def _db_check_dedup(url: str, window_secs: int) -> "dict | None":
    """Return the most recent done/played job for this URL within window_secs, or None."""
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                """SELECT title, username, finished_at
                     FROM yt_request_jobs
                    WHERE url     = ?
                      AND status  IN ('ready', 'played')
                      AND finished_at IS NOT NULL
                      AND finished_at >= datetime('now', ? || ' seconds')
                    ORDER BY finished_at DESC
                    LIMIT 1""",
                (url, f"-{window_secs}"),
            ).fetchone()
        if row:
            return {"title": row[0], "username": row[1], "finished_at": row[2]}
    except Exception as exc:
        print(f"[YT_REQUEST] DB dedup check error (non-fatal): {exc}")
    return None


def _extract_video_id_from_url(url: str) -> str:
    """Extract the 11-char YouTube video ID from a URL (no network call)."""
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([a-zA-Z0-9_\-]{11})", url)
    return m.group(1) if m else ""


# ─────────────────────────────────────────────────────────────────────────────
# Ban list helpers  (blocked tracks + blocked requesters)
# ─────────────────────────────────────────────────────────────────────────────

def _db_ban_track(pattern: str, added_by: str) -> None:
    p = pattern.strip()
    if not p:
        return
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO request_blocked_tracks
                   (pattern, added_by, added_at) VALUES (?, ?, datetime('now'))""",
                (p, added_by),
            )
            conn.commit()
    except Exception as exc:
        print(f"[YT_BAN] ban_track error: {exc}")

def _db_unban_track(pattern: str) -> bool:
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            cur = conn.execute(
                "DELETE FROM request_blocked_tracks WHERE LOWER(pattern) = LOWER(?)",
                (pattern.strip(),),
            )
            conn.commit()
        return cur.rowcount > 0
    except Exception:
        return False

def _db_ban_requester(username: str, added_by: str) -> None:
    if not username.strip():
        return
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO request_blocked_requesters
                   (username, added_by, added_at) VALUES (LOWER(?), ?, datetime('now'))""",
                (username, added_by),
            )
            conn.commit()
    except Exception as exc:
        print(f"[YT_BAN] ban_requester error: {exc}")

def _db_unban_requester(username: str) -> bool:
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            cur = conn.execute(
                "DELETE FROM request_blocked_requesters WHERE username = LOWER(?)",
                (username,),
            )
            conn.commit()
        return cur.rowcount > 0
    except Exception:
        return False

def _is_banned_requester(username: str) -> bool:
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                "SELECT 1 FROM request_blocked_requesters WHERE username = LOWER(?)",
                (username,),
            ).fetchone()
        return row is not None
    except Exception:
        return False

def _is_banned_track_by_id(video_id: str) -> bool:
    """Fast check: is this exact video_id in the ban list?"""
    if not video_id:
        return False
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                "SELECT 1 FROM request_blocked_tracks WHERE LOWER(pattern) = LOWER(?)",
                (video_id,),
            ).fetchone()
        return row is not None
    except Exception:
        return False

def _is_banned_track(video_id: str, title: str) -> "str | None":
    """Return the matching ban pattern if video_id OR title keyword matches, else None."""
    if not video_id and not title:
        return None
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            rows = conn.execute(
                "SELECT pattern FROM request_blocked_tracks"
            ).fetchall()
        vid_lc   = video_id.lower().strip()
        title_lc = title.lower().strip()
        for (pat,) in rows:
            p = pat.strip().lower()
            if not p:
                continue
            if vid_lc and p == vid_lc:
                return pat
            if title_lc and p in title_lc:
                return pat
    except Exception:
        pass
    return None

def _db_list_bans() -> dict:
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            tracks = [r[0] for r in conn.execute(
                "SELECT pattern FROM request_blocked_tracks ORDER BY id"
            ).fetchall()]
            requesters = [r[0] for r in conn.execute(
                "SELECT username FROM request_blocked_requesters ORDER BY id"
            ).fetchall()]
        return {"tracks": tracks, "requesters": requesters}
    except Exception:
        return {"tracks": [], "requesters": []}

def _queue_position() -> int:
    """Number of active jobs already in pipeline (queue depth before a new job)."""
    with _jobs_lock:
        return sum(
            1 for j in _jobs.values()
            if j["status"] in ("pending", "downloading", "uploading")
        )


def _db_recent_jobs(limit: int = 10) -> list[dict]:
    """Return the most recent yt_request_jobs rows from DB, newest first."""
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            rows = conn.execute(
                """SELECT id, username, url, title, status, error,
                          started_at, finished_at
                     FROM yt_request_jobs
                    ORDER BY id DESC
                    LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "id":          r[0], "username":    r[1], "url":         r[2],
                "title":       r[3], "status":      r[4], "error":       r[5],
                "started_at":  r[6], "finished_at": r[7],
            }
            for r in rows
        ]
    except Exception as exc:
        print(f"[YT_REQUEST] DB recent jobs error (non-fatal): {exc}")
    return []


def _db_update_azura_ids(db_id: int, file_id: str, song_id: str) -> None:
    """Persist AzuraCast file_id and song unique_id to the job record."""
    rq.update_azura_ids(db_id, file_id, song_id)


def _db_job_identity(db_id: int) -> dict:
    if not db_id:
        return {}
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                "SELECT id, user_id, username, url, title, status, filename, "
                "azura_file_id, video_id, source_type "
                "FROM yt_request_jobs WHERE id=?",
                (db_id,),
            ).fetchone()
        if not row:
            return {}
        return {
            "id": row[0],
            "user_id": row[1] or "",
            "username": row[2] or "",
            "url": row[3] or "",
            "title": row[4] or "",
            "status": row[5] or "",
            "filename": row[6] or "",
            "azura_file_id": row[7] or "",
            "video_id": row[8] or "",
            "source_type": row[9] or "",
        }
    except Exception:
        return {}


def _identity_matches(row: dict, *, filename: str = "", video_id: str = "", url: str = "") -> bool:
    if not row:
        return False
    if filename and (row.get("filename") or "") == filename:
        return True
    if video_id and (row.get("video_id") or "").strip().lower() == video_id.strip().lower():
        return True
    if url and (row.get("url") or "") == url:
        return True
    return False


def _supersede_duplicate_active_requests(
    keep_id: int,
    *,
    filename: str = "",
    video_id: str = "",
    url: str = "",
) -> None:
    """Mark unregistered duplicate active rows for the same request identity as terminal."""
    if not keep_id:
        return
    keep_row = _db_job_identity(keep_id)
    if not _identity_matches(keep_row, filename=filename, video_id=video_id, url=url):
        print(
            f"[RADIO_HARDEN] event=duplicate_guard_invalid_kept_id"
            f" old_request_id=0 kept_request_id={keep_id}"
        )
        return
    clauses: list[str] = []
    params: list[object] = []
    if filename:
        clauses.append("filename=?")
        params.append(filename)
    if video_id:
        clauses.append("video_id=?")
        params.append(video_id)
    if url:
        clauses.append("url=?")
        params.append(url)
    if not clauses:
        return
    duplicate_statuses = tuple(ACTIVE_QUEUE_STATUSES) + ("error",)
    ph = ",".join("?" * len(duplicate_statuses))
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            rows = conn.execute(
                "SELECT id, COALESCE(azura_file_id,''), status FROM yt_request_jobs "
                f"WHERE id<>? AND played_at IS NULL AND status IN ({ph}) "
                "AND COALESCE(azura_file_id,'')='' "
                f"AND ({' OR '.join(clauses)}) "
                "ORDER BY CASE WHEN COALESCE(azura_file_id,'')!='' THEN 0 ELSE 1 END, id ASC",
                (keep_id, *duplicate_statuses, *params),
            ).fetchall()
            for old_id, old_fid, old_status in rows:
                if int(old_id or 0) == int(keep_id):
                    print(f"[RADIO_CLEAN] event=duplicate_guard_skip_current request_id={keep_id}")
                    continue
                conn.execute(
                    "UPDATE yt_request_jobs "
                    "SET status='duplicate_superseded', error='duplicate_superseded', "
                    "finished_at=datetime('now') "
                    "WHERE id=? AND played_at IS NULL",
                    (old_id,),
                )
                print(
                    f"[RADIO_HARDEN] event=duplicate_request_superseded"
                    f" old_request_id={old_id} kept_request_id={keep_id}"
                    f" old_status={old_status!r} old_media_id={old_fid!r}"
                )
    except Exception as exc:
        print(f"[RADIO_HARDEN] duplicate_supersede_error request_id={keep_id} error={exc!r}")


def _active_duplicate_for_request(
    *,
    url: str = "",
    video_id: str = "",
    filename: str = "",
) -> "dict | None":
    clauses: list[str] = []
    params: list[object] = []
    if url:
        clauses.append("url=?")
        params.append(url)
    if video_id:
        clauses.append("video_id=?")
        params.append(video_id)
    if filename:
        clauses.append("filename=?")
        params.append(filename)
    if not clauses:
        return None
    active = tuple(ACTIVE_QUEUE_STATUSES)
    ph = ",".join("?" * len(active))
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                "SELECT id, status, title, filename, azura_file_id FROM yt_request_jobs "
                f"WHERE played_at IS NULL AND (status IN ({ph}) "
                "OR (status='error' AND COALESCE(azura_file_id,'')!='')) "
                f"AND ({' OR '.join(clauses)}) "
                "ORDER BY CASE WHEN COALESCE(azura_file_id,'')!='' THEN 0 ELSE 1 END, id ASC "
                "LIMIT 1",
                (*active, *params),
            ).fetchone()
        if not row:
            return None
        if (row[1] or "").strip().lower() == "error" and (row[4] or ""):
            rq.mark_ready(row[0], finished_at=time.time())
            print(
                f"[RADIO_HARDEN] event=media_ready_after_registration_error"
                f" request_id={row[0]} media_id={row[4]}"
            )
        recovered_error = (row[1] or "").strip().lower() == "error" and (row[4] or "")
        return {
            "id": row[0],
            "status": "ready" if recovered_error else row[1] or "",
            "title": row[2] or "",
            "filename": row[3] or "",
            "azura_file_id": row[4] or "",
        }
    except Exception as exc:
        print(f"[RADIO_HARDEN] duplicate_lookup_error error={exc!r}")
        return None


def _safe_uploaded_request_filename(filename: str) -> bool:
    name = os.path.basename((filename or "").strip())
    if not name or name != (filename or "").strip() or not name.lower().endswith(".mp3"):
        return False
    if name.startswith(("tmp_replay_", "local_request_", "request_")):
        return True
    stem = name[:-4]
    return 6 <= len(stem) <= 32 and all(c.isalnum() or c in "_-" for c in stem)


def _cleanup_failed_uploaded_request(db_id: int, reason: str = "request_failed_after_upload") -> None:
    job = _db_job_identity(db_id)
    filename = (job.get("filename") or "").strip()
    azura_file_id = (job.get("azura_file_id") or "").strip()
    if not db_id or not filename or not azura_file_id:
        return
    if not _safe_uploaded_request_filename(filename):
        print(
            f"[RADIO_HARDEN] event=cleanup_safety_skip"
            f" request_id={db_id} filename={filename!r} reason=unsafe_request_filename"
        )
        return
    print(
        f"[RADIO_HARDEN] event=failed_request_cleanup_start"
        f" request_id={db_id} filename={filename!r} reason={reason!r}"
    )
    try:
        ok = _azura_full_cleanup(job)
        print(
            f"[RADIO_HARDEN] event=failed_request_cleanup_done"
            f" request_id={db_id} filename={filename!r} ok={bool(ok)}"
        )
    except Exception as exc:
        print(
            f"[RADIO_HARDEN] event=failed_request_cleanup_error"
            f" request_id={db_id} filename={filename!r} error={exc!r}"
        )


def _db_get_oldest_staged() -> "dict | None":
    """
    Return the oldest job with status='staged' (downloaded file waiting in
    STAGING_DIR for the /Requests slot to free up), or None if none exists.
    Used by radio_promote_staged_job() and playback_engine's poll loop.
    """
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                "SELECT id, filename, title, username FROM yt_request_jobs "
                "WHERE status='staged' AND played_at IS NULL AND filename!='' "
                "ORDER BY id ASC LIMIT 1",
            ).fetchone()
        if row:
            return {
                "db_id": row[0], "filename": row[1],
                "title": row[2], "username": row[3],
            }
    except Exception as exc:
        print(f"[YT_STAGING] _db_get_oldest_staged error (non-fatal): {exc}")
    return None


def _fail_staged_file_missing(db_id: int, filename: str) -> None:
    """Terminally fail a staged request whose local staging file is gone."""
    print(
        f"[RADIO_HARDEN] event=staged_file_missing"
        f" request_id={db_id} filename={filename!r}"
    )
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                "SELECT user_id, coins_charged FROM yt_request_jobs WHERE id=?",
                (db_id,),
            ).fetchone()
        if row and row[0] and int(row[1] or 0) > 0:
            _refund_coins(row[0], int(row[1] or 0))
    except Exception as exc:
        print(f"[YT_STAGING] staged_file_missing refund check error: {exc!r}")
    _db_update_job(
        db_id,
        status="failed",
        error="staged_file_missing",
        finished_at=time.time(),
    )


def _db_get_pending_cleanup() -> list[dict]:
    """
    Return jobs that have an azura_file_id but have not been cleaned yet.

    Includes status='playing' so the safety-net catches files that are
    currently streaming (proactive deletion prevents AzuraCast from looping them).
    """
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            rows = conn.execute(
                """SELECT id, title, azura_file_id, azura_song_id, filename, video_id, username
                     FROM yt_request_jobs
                    WHERE status        IN ('ready', 'playing', 'played')
                      AND azura_file_id != ''
                      AND cleaned_at   IS NULL
                    ORDER BY id DESC""",
            ).fetchall()
        return [
            {
                "id":            r[0], "title":         r[1],
                "azura_file_id": r[2], "azura_song_id": r[3],
                "filename":      r[4], "video_id":      r[5],
                "username":      r[6],
            }
            for r in rows
        ]
    except Exception as exc:
        print(f"[YT_REQUEST] DB pending cleanup query error (non-fatal): {exc}")
    return []


def _db_mark_cleaned(db_id: int) -> None:
    """Set cleaned_at = now on a job record to mark it as removed from AzuraCast."""
    rq.mark_cleaned(db_id)


def _db_mark_played(db_id: int) -> None:
    """Set status='played' and played_at=now (idempotent — only fires once per job)."""
    rq.mark_played(db_id, only_if_unplayed=True)


def _db_mark_playing(db_id: int) -> None:
    """Set status='playing' when Now Playing detects the request is live."""
    rq.mark_playing(db_id, idempotent=True)


def _db_recent_played(limit: int = 10) -> list[dict]:
    """Return recently played request jobs (status='played'), newest first by played_at."""
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            rows = conn.execute(
                """SELECT id, username, title, played_at, cleaned_at
                     FROM yt_request_jobs
                    WHERE status IN ('played', 'ready')
                      AND played_at IS NOT NULL
                    ORDER BY played_at DESC
                    LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "id":         r[0], "username":   r[1], "title":      r[2],
                "played_at":  r[3], "cleaned_at": r[4],
            }
            for r in rows
        ]
    except Exception as exc:
        print(f"[YT_REQUEST] DB recent played error (non-fatal): {exc}")
    return []


def _db_get_old_pending_cleanup(hours: int = 24) -> list[dict]:
    """
    Return done jobs whose azura_file_id is set, cleaned_at is NULL,
    and started_at is older than `hours` hours ago.
    Used by !requestcleanup (REQUEST_AUTO_DELETE_HOURS-based sweep).
    """
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            rows = conn.execute(
                """SELECT id, title, azura_file_id, azura_song_id
                     FROM yt_request_jobs
                    WHERE status        = 'ready'
                      AND azura_file_id != ''
                      AND cleaned_at   IS NULL
                      AND started_at   <= datetime('now', ? || ' hours')
                    ORDER BY id DESC""",
                (f"-{max(1, hours)}",),
            ).fetchall()
        return [
            {
                "id":            r[0], "title":         r[1],
                "azura_file_id": r[2], "azura_song_id": r[3],
            }
            for r in rows
        ]
    except Exception as exc:
        print(f"[YT_REQUEST] DB old pending cleanup query error (non-fatal): {exc}")
    return []


def _db_lookup_by_azura_song_id(song_id: str) -> "str | None":
    """Return the requester username for a job matching azura_song_id, or None."""
    if not song_id:
        return None
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                "SELECT username FROM yt_request_jobs WHERE azura_song_id=? LIMIT 1",
                (song_id,),
            ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _db_lookup_for_now(song_id: str, np_title: str) -> "dict | None":
    """
    Return {username, title} for the job that matches the currently playing song.

    Three strategies (tried in order):
      1. azura_song_id exact match          — works after ID3 tags are set
      2. stored YouTube title == np_title   — works after first-time tag embed
      3. np_title == filename stem          — catches old no-tag uploads (filename shown)

    Returns None when the song is not one of our tracked requests.
    """
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            # Strategy 1: AzuraCast song-hash matches stored unique_id
            if song_id:
                row = conn.execute(
                    """SELECT username, title FROM yt_request_jobs
                        WHERE azura_song_id = ?
                        ORDER BY id DESC LIMIT 1""",
                    (song_id,),
                ).fetchone()
                if row:
                    return {"username": row[0], "title": row[1]}

            if not np_title:
                return None

            # Strategy 2: AzuraCast title (from ID3 tag) matches stored YouTube title
            row = conn.execute(
                """SELECT username, title FROM yt_request_jobs
                    WHERE title = ?
                      AND status IN ('ready', 'played', 'playing')
                    ORDER BY id DESC LIMIT 1""",
                (np_title,),
            ).fetchone()
            if row:
                return {"username": row[0], "title": row[1]}

            # Strategy 3: AzuraCast shows raw filename (no tags) — match against stored filename
            # e.g. np_title = "KFMYX1TibeQ" → filename = "KFMYX1TibeQ.mp3"
            row = conn.execute(
                """SELECT username, title FROM yt_request_jobs
                    WHERE filename = ? || '.mp3'
                      AND status IN ('ready', 'played', 'playing')
                    ORDER BY id DESC LIMIT 1""",
                (np_title,),
            ).fetchone()
            if row:
                return {"username": row[0], "title": row[1]}

    except Exception as exc:
        print(f"[YT_REQUEST] _db_lookup_for_now error (non-fatal): {exc}")
    return None


def _db_request_history(limit: int = 10) -> list[dict]:
    """Return the last `limit` yt_request_jobs rows with lifecycle fields."""
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            rows = conn.execute(
                """SELECT id, username, title, url, status, started_at, cleaned_at
                     FROM yt_request_jobs
                    ORDER BY id DESC
                    LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "id":         r[0], "username":   r[1], "title":      r[2],
                "url":        r[3], "status":     r[4], "started_at": r[5],
                "cleaned_at": r[6],
            }
            for r in rows
        ]
    except Exception as exc:
        print(f"[YT_REQUEST] DB request history error (non-fatal): {exc}")
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Per-user cooldown tracker
# ─────────────────────────────────────────────────────────────────────────────

_cooldowns: dict[str, float] = {}   # user_id → last_request_timestamp

# ─────────────────────────────────────────────────────────────────────────────
# YouTube search — pending results for !request / !pick flow
# ─────────────────────────────────────────────────────────────────────────────

_SEARCH_SESSION_TTL: int               = 180   # seconds — search sessions expire after 3 min
_yt_pending:         dict[str, list]   = {}    # user_id → search results
_yt_pending_ts:      dict[str, float]  = {}    # user_id → stored timestamp (time.time())
_yt_pending_lock:    threading.Lock    = threading.Lock()


def _yt_pending_is_fresh(user_id: str) -> bool:
    """Call while holding _yt_pending_lock. Returns True if entry exists and is within TTL."""
    if user_id not in _yt_pending:
        return False
    if time.time() - _yt_pending_ts.get(user_id, 0) > _SEARCH_SESSION_TTL:
        _yt_pending.pop(user_id, None)
        _yt_pending_ts.pop(user_id, None)
        return False
    return True


def has_pending_yt_search(user_id: str) -> bool:
    """True if the user has unexpired results from a search waiting for !pick."""
    with _yt_pending_lock:
        return _yt_pending_is_fresh(user_id)


def _yt_search_sync(query: str, max_results: int = 5) -> list[dict]:
    """
    Blocking YouTube search via yt-dlp (run in executor).
    Returns up to max_results dicts: {url, title, duration, duration_secs}.
    Uses 'ytsearch<n>:query' URL form — the only reliable way to trigger
    yt-dlp's YouTube search (default_search option is unreliable).
    """
    import yt_dlp  # already installed globally

    prefixed = f"ytsearch{max_results}:{query}"
    opts: dict = {
        "quiet":        True,
        "no_warnings":  True,
        "extract_flat": True,   # metadata only, no full fetch per video
    }
    results: list[dict] = []
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(prefixed, download=False)

    if not isinstance(info, dict) or not info.get("entries"):
        return results

    for entry in info["entries"][:max_results]:
        if not entry:
            continue
        vid_id  = entry.get("id") or ""
        dur_s   = int(entry.get("duration") or 0)
        mins, s = divmod(dur_s, 60)
        results.append({
            "url":           f"https://www.youtube.com/watch?v={vid_id}",
            "title":         (entry.get("title") or "(unknown)")[:80],
            "duration":      f"{mins}:{s:02d}",
            "duration_secs": dur_s,
        })
    return results


# ─────────────────────────────────────────────────────────────────────────────
_DOWNLOAD_TIMEOUT = 300   # seconds — total budget for yt-dlp download + ffmpeg

# Phrases (checked case-insensitively) that indicate YouTube is blocking the
# download because of a sign-in/bot/age/privacy restriction.
_YT_BLOCK_PHRASES = (
    "sign in to confirm",
    "confirm you're not a bot",
    "video unavailable",
    "private video",
    "members-only",
    "login required",
    "this video may be inappropriate",
    "age-restricted",
    "age restricted",
    "requires authentication",
    "inappropriate for some users",
)


class _YtBlockedError(Exception):
    """yt-dlp reported a restriction/block — show a clean message to the player."""


def _is_yt_blocked(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(p in msg for p in _YT_BLOCK_PHRASES)


# Blocking download step  (run in thread executor — must not touch event loop)
# ─────────────────────────────────────────────────────────────────────────────

def _download_step(url: str, tmpdir: str) -> tuple[dict, str]:
    """
    Pre-flight validate then download best-audio from YouTube → mp3 via ffmpeg.
    Returns (info_dict, local_mp3_path).  Raises ValueError for policy rejects.
    No audio is streamed — file lands in tmpdir only.
    """
    import yt_dlp  # already installed globally

    # ── 1. Pre-flight: fetch metadata WITHOUT downloading ────────────────────
    _pre_opts: dict = {
        "quiet":          True,
        "no_warnings":    True,
        "noplaylist":     True,
        "socket_timeout": 30,
    }
    try:
        with yt_dlp.YoutubeDL(_pre_opts) as ydl:
            pre = ydl.extract_info(url, download=False)
    except Exception as _pre_exc:
        if _is_yt_blocked(_pre_exc):
            raise _YtBlockedError(str(_pre_exc)) from _pre_exc
        raise

    # Unwrap single-entry playlist wrapper
    if isinstance(pre, dict) and pre.get("entries"):
        if pre.get("_type") in ("playlist", "multi_video"):
            raise ValueError(
                "Playlists not supported. Link a single video."
            )
        pre = pre["entries"][0]

    # Reject livestreams (duration is None/0 for active streams)
    if pre.get("is_live") or (pre.get("was_live") and not pre.get("duration")):
        raise ValueError("Livestreams are not supported.")

    # Reject videos that exceed the length limit
    duration = pre.get("duration") or 0
    if duration > _MAX_DURATION_SECS:
        mins, secs = divmod(int(duration), 60)
        max_min = _MAX_DURATION_SECS // 60
        raise ValueError(
            f"Video too long ({mins}m{secs:02d}s). Max allowed: {max_min} min."
        )

    # ── 2. Download + convert ────────────────────────────────────────────────
    ydl_opts: dict = {
        "format":         "bestaudio/best",
        "outtmpl":        os.path.join(tmpdir, "%(id)s.%(ext)s"),
        "noplaylist":     True,
        "quiet":          True,
        "no_warnings":    True,
        "socket_timeout": 30,
        "postprocessors": [
            {
                "key":              "FFmpegExtractAudio",
                "preferredcodec":   "mp3",
                "preferredquality": "192",
            },
            # Embed ID3 tags (title, artist/uploader) so AzuraCast
            # displays the real YouTube title instead of the filename.
            {
                "key":          "FFmpegMetadata",
                "add_metadata": True,
            },
        ],
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as _dl_exc:
        if _is_yt_blocked(_dl_exc):
            raise _YtBlockedError(str(_dl_exc)) from _dl_exc
        raise

    # Unwrap playlist wrapper (safety — noplaylist=True should prevent this)
    if isinstance(info, dict) and info.get("entries"):
        info = info["entries"][0]

    # Find the mp3 output file
    for fname in os.listdir(tmpdir):
        if fname.endswith(".mp3"):
            return info, os.path.join(tmpdir, fname)

    raise FileNotFoundError("mp3 file not found after yt-dlp conversion")

# ─────────────────────────────────────────────────────────────────────────────
# Blocking SFTP upload step  (runs inside a daemon thread started by _run_job)
# ─────────────────────────────────────────────────────────────────────────────

_SFTP_CONNECT_TIMEOUT  = 30   # seconds — TCP connect + SSH handshake + auth
_SFTP_TRANSFER_TIMEOUT = 180  # seconds — per-channel socket timeout during put()

def _sftp_step(
    mp3_path: str,
    on_put_done: "Callable[[], None]",
) -> None:
    """
    Blocking SFTP upload.  Runs inside a daemon thread started by _run_job.

    Design contract
    ───────────────
    • on_put_done() is called the INSTANT sftp.put() returns without raising.
      The caller uses this to signal the async layer and whisper success
      immediately — before this function does any cleanup.
    • on_put_done() is NOT called on failure; the exception propagates and the
      thread wrapper signals the error instead.
    • sftp.close() / ssh.close() run after on_put_done() and are fire-and-forget:
      wrapped in try/except, can never block or propagate.

    Log lines (always in order for a successful upload):
      [YT_SFTP] Connecting → host:port …
      [YT_SFTP] SFTP connected
      [YT_SFTP] Upload started → <remote path>
      [YT_SFTP] Upload 25 / 50 / 75 / 100 % …
      [YT_SFTP] Upload finished ✓ — <N> bytes
      [YT_SFTP] SFTP closed
    """
    import paramiko

    cfg             = _sftp_cfg()
    remote_filename = os.path.basename(mp3_path)
    remote_path     = f"{cfg['folder'].rstrip('/')}/{remote_filename}"
    file_size       = os.path.getsize(mp3_path)

    print(
        f"[YT_SFTP] Connecting → {cfg['host']}:{cfg['port']}"
        f" user={cfg['user']}"
        f" file={remote_filename} ({file_size:,} bytes)"
    )

    ssh  = paramiko.SSHClient()
    sftp = None
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        # ── 1. Connect + authenticate ────────────────────────────────────────
        try:
            ssh.connect(
                hostname=cfg["host"],
                port=cfg["port"],
                username=cfg["user"],
                password=cfg["passwd"],
                timeout=_SFTP_CONNECT_TIMEOUT,
                banner_timeout=_SFTP_CONNECT_TIMEOUT,
                auth_timeout=_SFTP_CONNECT_TIMEOUT,
                look_for_keys=False,
                allow_agent=False,
            )
        except paramiko.AuthenticationException as exc:
            print(f"[YT_SFTP] Auth failed — check AZURA_SFTP_USER / AZURA_SFTP_PASS: {exc}")
            raise
        except Exception as exc:
            print(f"[YT_SFTP] Connection failed — check AZURA_SFTP_HOST / AZURA_SFTP_PORT: {exc}")
            raise

        print("[YT_SFTP] SFTP connected")

        # ── 2. Open channel; set transfer timeout ────────────────────────────
        sftp = ssh.open_sftp()
        sftp.get_channel().settimeout(_SFTP_TRANSFER_TIMEOUT)

        # ── 3. Upload with 25 % progress milestones ──────────────────────────
        _milestone = [0]

        def _progress(transferred: int, total: int) -> None:
            if not total:
                return
            pct       = int(transferred * 100 / total)
            next_mark = ((_milestone[0] // 25) + 1) * 25
            if pct >= next_mark <= 100:
                _milestone[0] = next_mark
                print(f"[YT_SFTP] Upload {next_mark}% ({transferred:,} / {total:,} bytes)")

        print(f"[YT_SFTP] Upload started → {remote_path}")
        sftp.put(mp3_path, remote_path, callback=_progress)

        # ── 4. PUT returned — file is on server — signal success immediately ──
        print(f"[YT_SFTP] Upload finished ✓ — {file_size:,} bytes")
        on_put_done()

        # ── 5. Cleanup (fire-and-forget; never raises) ────────────────────────
        try:
            sftp.get_channel().settimeout(5)
            sftp.close()
        except Exception as exc:
            print(f"[YT_SFTP] sftp.close() warning (ignored): {exc}")
        try:
            ssh.close()
            print("[YT_SFTP] SFTP closed")
        except Exception as exc:
            print(f"[YT_SFTP] ssh.close() warning (ignored): {exc}")

    except Exception:
        if sftp is not None:
            try:
                sftp.get_channel().settimeout(5)
                sftp.close()
            except Exception:
                pass
        try:
            ssh.close()
        except Exception:
            pass
        raise

# ─────────────────────────────────────────────────────────────────────────────
# Optional AzuraCast API post-upload step
# ─────────────────────────────────────────────────────────────────────────────

_API_WAIT_SECS   = 10  # seconds to wait after SFTP before first rescan attempt
_API_RETRY_COUNT = 5   # how many times to search for the file if not indexed yet
_API_RETRY_DELAY = 5   # seconds between search retries

def _azura_api_cfg() -> "dict | None":
    """Return API config dict if AZURA_BASE_URL + AZURA_API_KEY are both set, else None."""
    base_url = (os.environ.get("AZURA_BASE_URL") or "").rstrip("/").strip()
    api_key  = (os.environ.get("AZURA_API_KEY") or "").strip()
    if not base_url or not api_key:
        return None
    # AZURA_MEDIA_DIR — currentDirectory sent to the AzuraCast rescan API.
    # This is a path relative to the station's media library root (NOT the
    # SFTP filesystem path).  Leave empty ("") to rescan the entire library.
    media_dir = (os.environ.get("AZURA_MEDIA_DIR") or "").strip()
    return {
        "base_url":   base_url,
        "api_key":    api_key,
        "station_id": (os.environ.get("AZURA_STATION_ID") or "1").strip(),
        "folder":     media_dir,
    }

def _azura_post_upload(filename: str, db_id: int = 0, bot: "object | None" = None, loop: "object | None" = None) -> None:
    """
    Blocking post-SFTP API step.  Called from the upload daemon thread AFTER
    sftp.put() has returned.

    Steps
    ─────
    1. Wait _API_WAIT_SECS for AzuraCast to notice the file on disk.
    2. POST rescan           (stage=media_rescan)    — index the Requests folder.
    3. GET search + retry   (stage=media_lookup)    — find media_id by filename,
       path, or title/youtube_id fallback.  Re-triggers rescan on every other
       failed attempt.
    4. Resolve playlist_id  (stage=playlist_lookup) — AZURA_PLAYLIST_ID env var
       first; if not set, GET /playlists and match by name "Requests".
       ABORT (refund + error whisper) if playlist_id cannot be resolved.
    5. POST/PUT assign      (stage=playlist_assign)  — two methods tried in order:
         A. batch endpoint  POST /files/batch {"do":"playlist",...}
         B. direct PUT      PUT /file/{id}    {"playlists":[id]}
       ABORT (refund + error whisper) if both methods fail to verify.
    6. GET verify           (stage=playlist_verify)  — confirm file is in playlist.
    7. POST skip            (stage=azuracast_skip)   — skip current song so the
       request plays immediately.

    Abort behaviour (playlist_id missing OR assignment verification fails):
      • Refund coins_charged to the requester.
      • Whisper "❌ Request upload failed. Try again." to the requester.
      • Set job status='error' in DB.
      • Do NOT call skip — do NOT send any room announcement.

    Log fields on every line:
      stage=  filename=  media_id=  playlist_id=  result=
    All HTTP response bodies are logged.  Never raises.
    """
    import requests as req_lib

    cfg = _azura_api_cfg()
    if cfg is None:
        print("[YT_API] AZURA_BASE_URL / AZURA_API_KEY not configured — skipping post-upload step")
        return

    base      = cfg["base_url"]
    sid       = cfg["station_id"]
    headers   = {
        "X-API-Key":    cfg["api_key"],
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }
    batch_url  = f"{base}/api/station/{sid}/files/batch"
    search_url = f"{base}/api/station/{sid}/files"

    # Derive the AzuraCast media-library subdirectory for the rescan call.
    # Priority: AZURA_MEDIA_DIR (explicit override) → basename of AZURA_SFTP_PATH
    # (e.g. "Requests") → empty string (full-library rescan as last resort).
    sftp_raw   = (os.environ.get("AZURA_SFTP_PATH") or "Requests").strip()
    rescan_dir = cfg.get("folder") or os.path.basename(sftp_raw.rstrip("/")) or sftp_raw

    playlist_id = (os.environ.get("AZURA_PLAYLIST_ID") or "").strip()

    def _rlog(stage: str, result: str, media_id: "int | str | None" = None, **kw: str) -> None:
        """Emit one structured log line with all required fields."""
        mid = str(media_id) if media_id is not None else ""
        extras = "".join(f" {k}={v}" for k, v in kw.items())
        print(
            f"[YT_API] stage={stage}"
            f" filename={filename!r}"
            f" media_id={mid!r}"
            f" playlist_id={playlist_id!r}"
            f" result={result}{extras}"
        )

    def _do_rescan(label: str) -> bool:
        """POST batch rescan and return True on HTTP 2xx."""
        try:
            r = req_lib.post(
                batch_url,
                json={"do": "rescan", "currentDirectory": rescan_dir},
                headers=headers,
                timeout=30,
            )
            ok = r.status_code in (200, 204)
            _rlog("media_rescan", "success" if ok else "fail",
                  label=label, folder=repr(rescan_dir),
                  http_status=str(r.status_code),
                  response_body=repr(r.text[:200]))
            return ok
        except Exception as exc:
            _rlog("media_rescan", "error", label=label, exception=repr(str(exc)))
            return False

    def _check_in_playlist(fid: int) -> bool:
        """GET /file/{fid} and return True if playlist_id is in the playlists list."""
        if not playlist_id:
            return False
        try:
            r = req_lib.get(
                f"{base}/api/station/{sid}/file/{fid}",
                headers=headers,
                timeout=15,
            )
            if r.status_code == 200:
                playlists = r.json().get("playlists") or []
                return any(
                    str(p.get("id") if isinstance(p, dict) else p) == str(playlist_id)
                    for p in playlists
                )
        except Exception:
            pass
        return False

    def _lookup_playlist_by_name(*names: str) -> str:
        """
        GET /api/station/{sid}/playlists and return the id of the first playlist
        whose name matches any of `names` (case-insensitive).  Returns '' on
        error or no match.

        stage=playlist_lookup  result=success|not_found|error|http_<N>
        """
        try:
            r = req_lib.get(
                f"{base}/api/station/{sid}/playlists",
                headers=headers,
                timeout=15,
            )
            _rlog("playlist_lookup", f"http_{r.status_code}",
                  response_body=repr(r.text[:400]))
            if r.status_code == 200:
                items = r.json() if isinstance(r.json(), list) else []
                targets = [n.lower().strip() for n in names]
                for item in items:
                    pl_name = (item.get("name") or "").strip().lower()
                    if pl_name in targets:
                        found = str(item.get("id") or "")
                        _rlog("playlist_lookup", "success",
                              found_id=repr(found),
                              matched_name=repr(pl_name))
                        return found
            _rlog("playlist_lookup", "not_found",
                  names_tried=repr(list(names)))
        except Exception as exc:
            _rlog("playlist_lookup", "error", exception=repr(str(exc)))
        return ""

    def _abort_request(reason: str) -> None:
        """
        Refund coins_charged, whisper error to requester, set job status=error.
        Called when playlist assignment cannot be confirmed.  Non-fatal.
        """
        if _coins_charged > 0 and _uid:
            try:
                _refund_coins(_uid, _coins_charged)
                _rlog("user_refund", "success",
                      reason=reason, coins=str(_coins_charged))
            except Exception as _re:
                _rlog("user_refund", "error",
                      reason=reason, exception=repr(str(_re)))
        if db_id:
            try:
                _db_update_job(db_id, status="error",
                               error=reason, finished_at=time.time())
            except Exception:
                pass
        if bot and loop and _uid:
            try:
                asyncio.run_coroutine_threadsafe(
                    bot.highrise.send_whisper(
                        _uid, "❌ Couldn't prepare that song. Try another version."
                    ),
                    loop,
                ).result(5)
            except Exception as _we:
                _rlog("whisper_error", "fail",
                      reason=reason, exception=repr(str(_we)))

    try:
        # ── 0. Look up requester info for abort / refund handling ─────────
        _uid:           str = ""
        _coins_charged: int = 0
        if db_id:
            try:
                with sqlite3.connect(_DB_PATH) as _uc:
                    _ur = _uc.execute(
                        "SELECT user_id, coins_charged"
                        " FROM yt_request_jobs WHERE id=?",
                        (db_id,),
                    ).fetchone()
                if _ur:
                    _uid           = (_ur[0] or "").strip()
                    _coins_charged = int(_ur[1] or 0)
            except Exception:
                pass

        # ── 1. Wait ──────────────────────────────────────────────────────────
        _rlog("media_rescan", "waiting", label=f"sleep_{_API_WAIT_SECS}s")
        time.sleep(_API_WAIT_SECS)

        # ── 2. Initial rescan ─────────────────────────────────────────────────
        _do_rescan("initial")

        # ── 3. Search + retry (stage=media_lookup) ────────────────────────────
        file_id:   "int | None" = None
        unique_id: "str | None" = None

        for attempt in range(1, _API_RETRY_COUNT + 1):
            _rlog("media_lookup", "searching",
                  attempt=f"{attempt}/{_API_RETRY_COUNT}")
            try:
                resp = req_lib.get(
                    search_url,
                    params={"searchPhrase": filename},
                    headers=headers,
                    timeout=15,
                )
                _rlog("media_lookup", f"http_{resp.status_code}",
                      attempt=f"{attempt}/{_API_RETRY_COUNT}",
                      response_body=repr(resp.text[:400]))
                if resp.status_code == 200:
                    data = resp.json()
                    rows = data if isinstance(data, list) else data.get("rows", [])
                    # One-time DB lookup for title/video_id fallback matching
                    _job_vid_lc = ""
                    _job_ttl_lc = ""
                    if db_id and file_id is None:
                        try:
                            with sqlite3.connect(_DB_PATH) as _jc:
                                _jr = _jc.execute(
                                    "SELECT video_id, title FROM yt_request_jobs WHERE id=?",
                                    (db_id,),
                                ).fetchone()
                            if _jr:
                                _job_vid_lc = (_jr[0] or "").strip().lower()
                                _job_ttl_lc = (_jr[1] or "").strip().lower()
                        except Exception:
                            pass
                    for row in rows:
                        row_path  = row.get("path", "")
                        row_title = (row.get("title") or
                                     (row.get("song") or {}).get("title") or "").strip()
                        # Strategy 1: full AzuraCast path match
                        path_match = (
                            row_path == f"{rescan_dir}/{filename}"
                            or row_path == f"Requests/{filename}"
                        )
                        # Strategy 2: basename match
                        base_match = os.path.basename(row_path) == filename
                        # Strategy 3: video_id or title fallback
                        fb_match   = bool(
                            (_job_vid_lc and _job_vid_lc in row_path.lower())
                            or (_job_ttl_lc and row_title.lower() == _job_ttl_lc)
                        )
                        if not (path_match or base_match or fb_match):
                            continue
                        raw_id    = row.get("id")
                        file_id   = int(raw_id) if raw_id is not None else None
                        unique_id = (
                            row.get("unique_id")
                            or row.get("song_unique_id")
                            or (row.get("song") or {}).get("id")
                            or ""
                        )
                        _strategy = (
                            "path" if path_match else
                            "basename" if base_match else
                            "fallback"
                        )
                        _rlog("media_lookup", "success", media_id=file_id,
                              unique_id=repr(unique_id),
                              path=repr(row_path),
                              strategy=_strategy)
                        _db_update_azura_ids(db_id, str(file_id), unique_id or "")
                        print(
                            f"[RADIO_HARDEN] event=azura_registration_ok"
                            f" request_id={db_id} media_id={file_id}"
                        )
                        _supersede_duplicate_active_requests(
                            db_id,
                            filename=filename,
                            video_id=_job_vid_lc,
                        )
                        break
            except Exception as exc:
                _rlog("media_lookup", "error",
                      attempt=f"{attempt}/{_API_RETRY_COUNT}",
                      exception=repr(str(exc)))

            if file_id is not None:
                break
            if attempt < _API_RETRY_COUNT:
                if attempt % 2 == 0:
                    _do_rescan(f"retry_{attempt}")
                _rlog("media_lookup", "not_indexed_yet",
                      waiting=f"{_API_RETRY_DELAY}s")
                time.sleep(_API_RETRY_DELAY)

        if file_id is None:
            _rlog("media_lookup", "not_found",
                  after=f"{_API_RETRY_COUNT}_attempts",
                  note="aborting_playlist_assign_and_skip")
            return

        # ── 4a. Resolve playlist_id — env var first, then name lookup ────────
        # Never rely on "Apply to Folders" — always explicitly assign.
        if not playlist_id:
            _rlog("playlist_lookup", "env_var_not_set",
                  note="falling_back_to_name_lookup")
            playlist_id = _lookup_playlist_by_name("Requests", "Request")

        if not playlist_id:
            _rlog("playlist_assign", "aborted", media_id=file_id,
                  note="playlist_id_unavailable_no_env_var_and_name_lookup_failed")
            _abort_request("playlist_id_not_found")
            return

        # ── 4. Assign to Requests playlist ────────────────────────────────────
        # Method A: batch  POST /files/batch {"do":"playlist",...}
        # Method B: direct PUT  PUT /file/{id} {"playlists":[pid]}
        # If BOTH verify fail → abort: refund + error whisper + no skip.
        playlist_assign_statuses: list[int] = []
        try:
            resp = req_lib.post(
                batch_url,
                json={"do": "playlist", "playlist": playlist_id, "files": [file_id]},
                headers=headers,
                timeout=15,
            )
            playlist_assign_statuses.append(int(resp.status_code))
            _rlog("playlist_assign", "success" if resp.status_code in (200, 204) else "fail",
                  media_id=file_id, method="batch",
                  http_status=str(resp.status_code),
                  response_body=repr(resp.text[:300]))
        except Exception as exc:
            _rlog("playlist_assign", "error", media_id=file_id, method="batch",
                  exception=repr(str(exc)))

        # Small pause so AzuraCast can process the batch write
        time.sleep(2)

        # ── 5a. Verify method A ───────────────────────────────────────────────
        in_pl = _check_in_playlist(file_id)
        _rlog("playlist_verify", "success" if in_pl else "fail", media_id=file_id)

        if not in_pl:
            # ── Method B: direct PUT /file/{id} {"playlists":[pid]} ──────────
            # Some AzuraCast versions silently ignore the batch playlist action;
            # the PUT file-update endpoint is more reliable.
            _rlog("playlist_assign", "trying_method_b", media_id=file_id, method="put")
            try:
                try:
                    pid_val: "int | str" = int(playlist_id)
                except ValueError:
                    pid_val = playlist_id
                resp = req_lib.put(
                    f"{base}/api/station/{sid}/file/{file_id}",
                    json={"playlists": [pid_val]},
                    headers=headers,
                    timeout=15,
                )
                playlist_assign_statuses.append(int(resp.status_code))
                _rlog("playlist_assign", "success" if resp.status_code in (200, 204) else "fail",
                      media_id=file_id, method="put",
                      http_status=str(resp.status_code),
                      response_body=repr(resp.text[:300]))
            except Exception as exc:
                _rlog("playlist_assign", "error", media_id=file_id, method="put",
                      exception=repr(str(exc)))

            time.sleep(2)

            # ── 5b. Verify method B ───────────────────────────────────────────
            in_pl = _check_in_playlist(file_id)
            _rlog("playlist_verify", "success" if in_pl else "fail",
                  media_id=file_id, attempt="2")

            if not in_pl:
                if 405 in playlist_assign_statuses and file_id is not None:
                    print(
                        f"[RADIO_HARDEN] event=playlist_assign_skipped_nonfatal"
                        f" request_id={db_id} media_id={file_id} status=405"
                    )
                    print(
                        f"[RADIO_HARDEN] event=media_ready_after_playlist_assign_405"
                        f" request_id={db_id} media_id={file_id}"
                    )
                    _rlog(
                        "playlist_assign",
                        "nonfatal_405_media_indexed",
                        media_id=file_id,
                        note="engine_will_detect_indexed_requests_media",
                    )
                else:
                    # Both methods failed verification — abort, refund, notify user.
                    _rlog("playlist_assign", "final_fail", media_id=file_id,
                          note="both_methods_failed_aborting_skip_user_notified")
                    _abort_request("playlist_assign_failed")
                    return

        # ── 6. (skip removed) ─────────────────────────────────────────────────
        # Upload is complete — playback_engine's poll loop detects when this
        # request naturally becomes the current song and fires REQUEST LIVE.
        # We must NOT call backend/skip here; that would interrupt the current
        # track.  Only the staff !skip command may do that.
        _rlog("azuracast_skip", "suppressed_no_skip", media_id=file_id,
              note="skip_removed_engine_handles_detection")

    except Exception as exc:
        print(f"[YT_API] Unexpected error in post-upload step (non-fatal): {exc}")

# ─────────────────────────────────────────────────────────────────────────────
# Async pipeline orchestrator
# ─────────────────────────────────────────────────────────────────────────────

async def _run_job(bot: "BaseBot", job: dict) -> None:
    """
    Orchestrate the full pipeline for one job.

    Download step  — run_in_executor (blocks until audio file is ready).
    Upload step    — daemon thread + asyncio.Event.  The bot whispers success
                     the instant sftp.put() returns; SSH cleanup runs in the
                     background thread without blocking the event loop.
    """
    jid    = job["id"]
    uid    = job["user_id"]
    db_id  = int(job.get("db_id") or 0)
    tmpdir = tempfile.mkdtemp(prefix="ytr_")
    loop   = asyncio.get_running_loop()
    _stage      = "unknown"   # tracks which pipeline stage raised
    _staged_mp3 = ""          # STAGING_DIR path; cleared on success

    with _prep_ids_lock:
        _prep_active_jids.add(jid)

    try:
        # ── Step 1: Download + convert ──────────────────────────────────────
        _stage = "download"
        _update_job(jid, status="downloading")

        async with _download_sem:
            info, mp3_path = await asyncio.wait_for(
                loop.run_in_executor(None, _download_step, job["url"], tmpdir),
                timeout=_DOWNLOAD_TIMEOUT,
            )
        title        = (info.get("title") or "Unknown")[:160]
        yt_filename  = os.path.basename(mp3_path)
        video_id     = (info.get("id") or "")[:32]
        yt_uploader  = (
            info.get("uploader") or info.get("channel") or ""
        )[:100]
        yt_artist    = (
            info.get("artist") or info.get("creator") or ""
        )[:100]
        _update_job(
            jid,
            title=title,
            filename=yt_filename,
            video_id=video_id,
            yt_uploader=yt_uploader,
            artist=yt_artist,
        )
        print(
            f"[YT_REQUEST] Job #{jid} downloaded:"
            f" title={title[:60]!r}"
            f" video_id={video_id}"
            f" uploader={yt_uploader[:30]!r}"
        )

        # ── Check title-based track ban (video_id ban checked at request time) ─
        ban_pat = _is_banned_track(video_id, title)
        if ban_pat:
            print(f"[YT_BAN] Job #{jid} rejected — banned pattern: {ban_pat!r}")
            coins_c = job.get("coins_charged", 0)
            if coins_c > 0:
                _refund_coins(uid, coins_c)
            with _presence_lock:
                _p = _active_presence.get(uid)
                if _p and _p.get("job_id") == jid:
                    _active_presence.pop(uid, None)
            _update_job(jid, status="error", error="banned_track", finished_at=time.time())
            await _w(
                bot, uid,
                "🚫 That song is not allowed in this room. Your coins have been refunded."
                if coins_c > 0 else
                "🚫 That song is not allowed in this room."
            )
            return

        # ── Move downloaded file to staging dir ───────────────────────────────
        os.makedirs(STAGING_DIR, exist_ok=True)
        _staged_mp3 = os.path.join(STAGING_DIR, yt_filename)
        shutil.move(mp3_path, _staged_mp3)
        mp3_path = _staged_mp3
        _update_job(jid, status="downloaded")
        print(
            f"[YT_REQUEST] Job #{jid} — downloaded:"
            f" title={title[:60]!r} file={yt_filename!r}"
        )

        # ── Step 2: SFTP upload + AzuraCast registration ─────────────────────
        _stage = "sftp"
        upload_secs = 0.0
        async with _upload_sem:
            _update_job(jid, status="uploading")
            print(f"[YT_REQUEST] Job #{jid} — uploading: {title[:80]}")

            upload_done: asyncio.Event               = asyncio.Event()
            upload_exc:  list[BaseException | None]  = [None]

            def _upload_thread() -> None:
                try:
                    _sftp_step(mp3_path, lambda: None)
                    _azura_post_upload(os.path.basename(mp3_path), db_id, bot=bot, loop=loop)
                except Exception as exc:
                    upload_exc[0] = exc
                finally:
                    loop.call_soon_threadsafe(upload_done.set)

            t = threading.Thread(
                target=_upload_thread,
                daemon=True,
                name=f"ytr_upload_{jid}",
            )
            upload_start = time.time()
            t.start()

            await upload_done.wait()      # unblocks after upload + API registration
            upload_secs = time.time() - upload_start

        if upload_exc[0] is not None:
            raise upload_exc[0]

        db_id = int(job.get("db_id") or jid)
        status_after_upload = rq.get_job_status(db_id)
        if rq.is_terminal_status(status_after_upload):
            print(
                f"[RADIO_HARDEN] event=failed_row_not_revived"
                f" request_id={db_id} old_status={status_after_upload!r} attempted_status='ready'"
            )
            return
        with sqlite3.connect(_DB_PATH) as conn:
            _az_row = conn.execute(
                "SELECT azura_file_id, azura_song_id FROM yt_request_jobs WHERE id=?",
                (db_id,),
            ).fetchone()
        _az_fid = (_az_row[0] or "") if _az_row else ""
        _az_sid = (_az_row[1] or "") if _az_row else ""
        if not _az_fid:
            raise RuntimeError("azura_registration_missing")
        print(
            f"[RADIO_HARDEN] event=request_ready_after_playlist_assign"
            f" request_id={db_id} media_id={_az_fid}"
        )

        # ── Ready: uploaded + registered in AzuraCast Requests playlist ───────
        _update_job(jid, status="ready", finished_at=time.time())
        _staged_mp3 = ""  # clear so finally won't delete (file is now on AzuraCast)
        print(f"[YT_REQUEST] Job #{jid} — ready in {upload_secs:.1f}s: {title[:80]}")
        print(f"[RADIO_CLEAN] event=duplicate_queue_confirmation_suppressed request_id={db_id}")

    except _YtBlockedError as exc:
        raw_err = str(exc)
        _update_job(
            jid, status="failed_download", error=raw_err[:200], finished_at=time.time()
        )
        print(
            f"[REQUEST_FAIL] stage=download"
            f" video_id={job.get('video_id') or job['url']}"
            f" title={job.get('title', 'unknown')!r}"
            f" requester={job.get('username', '?')!r}"
            f" raw_error={raw_err}"
        )
        coins_c = job.get("coins_charged", 0)
        if coins_c > 0:
            _refund_coins(uid, coins_c)
        diag.log_radio_event(
            "request_failed_refunded",
            request_id=job.get("db_id", 0),
            user_id=uid,
            username=job.get("username", ""),
            title=job.get("title", ""),
            source_type=job.get("source_type", "youtube"),
            temp_path=job.get("filename", ""),
            source_path=job.get("url", ""),
        )
        refund_note = f" {coins_c:,} coins refunded." if coins_c > 0 else ""
        await _w(bot, uid, f"❌ Couldn't prepare that song. Try another version.{refund_note}")

    except asyncio.TimeoutError:
        _update_job(
            jid, status="failed_download", error="download_timeout", finished_at=time.time()
        )
        print(
            f"[REQUEST_FAIL] stage=download type=TimeoutError"
            f" url={job['url']!r}"
            f" requester={job.get('username', '?')!r}"
            f" raw_error=Download exceeded {_DOWNLOAD_TIMEOUT}s timeout"
        )
        coins_c = job.get("coins_charged", 0)
        if coins_c > 0:
            _refund_coins(uid, coins_c)
        diag.log_radio_event(
            "request_failed_refunded",
            request_id=job.get("db_id", 0),
            user_id=uid,
            username=job.get("username", ""),
            title=job.get("title", ""),
            source_type=job.get("source_type", "youtube"),
            temp_path=job.get("filename", ""),
            source_path=job.get("url", ""),
        )
        refund_note = f" {coins_c:,} coins refunded." if coins_c > 0 else ""
        await _w(bot, uid, f"❌ Couldn't prepare that song. Try another version.{refund_note}")

    except Exception as exc:
        import traceback as _tb
        err = str(exc)
        _cleanup_failed_uploaded_request(int(job.get("db_id") or 0), err[:80])
        _update_job(jid, status="error", error=err[:200], finished_at=time.time())
        print(
            f"[REQUEST_FAIL] stage={_stage}"
            f" type={type(exc).__name__}"
            f" msg={err[:200]}"
        )
        _tb.print_exc()
        coins_c = job.get("coins_charged", 0)
        if coins_c > 0:
            _refund_coins(uid, coins_c)
        diag.log_radio_event(
            "request_failed_refunded",
            request_id=job.get("db_id", 0),
            user_id=uid,
            username=job.get("username", ""),
            title=job.get("title", ""),
            source_type=job.get("source_type", "youtube"),
            temp_path=job.get("filename", ""),
            source_path=job.get("url", ""),
        )
        refund_note = f" {coins_c:,} coins refunded." if coins_c > 0 else ""
        await _w(bot, uid, f"❌ Couldn't prepare that song. Try another version.{refund_note}")

    finally:
        # Clear presence tracking (job done or failed)
        with _presence_lock:
            _pres = _active_presence.get(uid)
            if _pres and _pres.get("job_id") == jid:
                _active_presence.pop(uid, None)
        # Clean up staging file on failure; _staged_mp3 is cleared on success
        if _staged_mp3 and os.path.exists(_staged_mp3):
            try:
                os.unlink(_staged_mp3)
            except Exception:
                pass
        # Temp dir removed here; upload thread may still be running ssh.close()
        # but it holds no reference to tmpdir, so this is safe.
        shutil.rmtree(tmpdir, ignore_errors=True)
        with _prep_ids_lock:
            _prep_active_jids.discard(jid)


async def process_existing_request_file(
    bot: "BaseBot",
    db_id: int,
    filename: str,
    *,
    source_type: str = "local_replay",
    source_path: str = "",
) -> bool:
    """
    Continue the normal request pipeline after a temp MP3 already exists in
    the AzuraCast Requests folder.

    YouTube jobs differ only in their source step (download). Local favorites
    differ only in their source step (copy). Once `filename` exists, both use
    the same Azura post-upload indexing/playlist assignment, ready status,
    playback confirmation, and cleanup path.
    """
    if not db_id or not filename:
        return False

    loop = asyncio.get_running_loop()
    try:
        rq.update_job_fields(db_id, status="uploading", filename=filename)
        diag.log_radio_event(
            "file_source_ready",
            request_id=db_id,
            source_type=source_type,
            temp_path=filename,
            source_path=source_path,
        )
        async with _upload_sem:
            await loop.run_in_executor(
                None,
                _azura_post_upload,
                filename,
                db_id,
                bot,
                loop,
            )

        status = rq.get_job_status(db_id)
        if rq.is_terminal_status(status):
            print(
                f"[RADIO_HARDEN] event=failed_row_not_revived"
                f" request_id={db_id} old_status={status!r} attempted_status='ready'"
            )
            return False

        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                "SELECT azura_file_id, azura_song_id FROM yt_request_jobs WHERE id=?",
                (db_id,),
            ).fetchone()
        azura_file_id = (row[0] or "") if row else ""
        azura_song_id = (row[1] or "") if row else ""
        if not azura_file_id:
            return False

        print(
            f"[RADIO_HARDEN] event=request_ready_after_playlist_assign"
            f" request_id={db_id} media_id={azura_file_id}"
        )
        rq.mark_ready(db_id, filename=filename, source_type=source_type)
        diag.log_radio_event(
            "submitted_to_azura",
            request_id=db_id,
            source_type=source_type,
            temp_path=filename,
            source_path=source_path,
            azura_file_id=azura_file_id,
            azura_song_id=azura_song_id,
        )
        return True
    except Exception as exc:
        print(f"[YT_REQUEST] process_existing_request_file error: {exc!r}")
        return False


async def process_staged_existing_mp3(
    bot: "BaseBot",
    db_id: int,
    source_mp3_path: str,
    *,
    filename: str = "",
    source_type: str = "local_favorite",
    source_path: str = "",
) -> bool:
    """
    Continue the normal YouTube request pipeline after a non-YouTube source
    has produced a local MP3. The source step differs; upload, Azura
    registration, ready status, playback detection, and cleanup remain shared.
    """
    if not db_id or not source_mp3_path or not os.path.isfile(source_mp3_path):
        return False

    safe_name = os.path.basename(filename or source_mp3_path)
    if not safe_name.endswith(".mp3"):
        safe_name = f"{safe_name}.mp3"
    if not (safe_name.startswith("local_request_") or safe_name.startswith("tmp_replay_")):
        safe_name = f"local_request_{safe_name}"

    loop = asyncio.get_running_loop()
    staged_path = os.path.join(STAGING_DIR, safe_name)
    try:
        os.makedirs(STAGING_DIR, exist_ok=True)
        if os.path.abspath(source_mp3_path) != os.path.abspath(staged_path):
            shutil.copyfile(source_mp3_path, staged_path)
        rq.update_job_fields(db_id, status="downloaded", filename=safe_name, source_type=source_type)
        diag.log_radio_event(
            "file_source_ready",
            request_id=db_id,
            source_type=source_type,
            temp_path=safe_name,
            source_path=source_path or source_mp3_path,
        )

        async with _upload_sem:
            rq.update_job_fields(db_id, status="uploading", filename=safe_name, source_type=source_type)
            await loop.run_in_executor(None, _sftp_step, staged_path, lambda: None)
            await loop.run_in_executor(None, _azura_post_upload, safe_name, db_id, bot, loop)

        status = rq.get_job_status(db_id)
        if rq.is_terminal_status(status):
            print(
                f"[RADIO_HARDEN] event=failed_row_not_revived"
                f" request_id={db_id} old_status={status!r} attempted_status='ready'"
            )
            return False

        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                "SELECT azura_file_id, azura_song_id FROM yt_request_jobs WHERE id=?",
                (db_id,),
            ).fetchone()
        azura_file_id = (row[0] or "") if row else ""
        azura_song_id = (row[1] or "") if row else ""
        if not azura_file_id:
            return False

        print(
            f"[RADIO_HARDEN] event=request_ready_after_playlist_assign"
            f" request_id={db_id} media_id={azura_file_id}"
        )
        rq.mark_ready(db_id, filename=safe_name, source_type=source_type, finished_at=time.time())
        diag.log_radio_event(
            "submitted_to_azura",
            request_id=db_id,
            source_type=source_type,
            temp_path=safe_name,
            source_path=source_path or source_mp3_path,
            azura_file_id=azura_file_id,
            azura_song_id=azura_song_id,
        )
        return True
    except Exception as exc:
        print(f"[YT_REQUEST] process_staged_existing_mp3 error: {exc!r}")
        return False
    finally:
        try:
            if os.path.isfile(staged_path) and os.path.basename(staged_path).startswith(("local_request_", "tmp_replay_")):
                os.unlink(staged_path)
        except Exception:
            pass

# ─────────────────────────────────────────────────────────────────────────────
# Staged-job promotion (Option A: one active file in /Requests at a time)
# ─────────────────────────────────────────────────────────────────────────────

def radio_promote_staged_job(bot: "object | None", loop: "object | None") -> bool:
    """
    Blocking.  Find the oldest staged job (status='staged'), upload its
    pre-downloaded MP3 from STAGING_DIR to the SFTP /Requests folder, and run
    the full AzuraCast post-upload step.

    Called from playback_engine via loop.run_in_executor() after the current
    request finishes and the /Requests slot is free.

    Returns True if a job was promoted, False if none were staged or on error.
    Log fields:  stage=request_promote  request_id=  filename=  result=
    """
    job = _db_get_oldest_staged()
    if not job:
        return False

    db_id    = job["db_id"]
    filename = job["filename"]
    staged_path = os.path.join(STAGING_DIR, filename)

    if not os.path.exists(staged_path):
        print(
            f"[YT_STAGING] stage=request_promote"
            f" request_id={db_id}"
            f" filename={filename!r}"
            f" result=fail"
            f" error=staged_file_missing"
        )
        _fail_staged_file_missing(db_id, filename)
        return False

    print(
        f"[YT_STAGING] stage=request_promote"
        f" request_id={db_id}"
        f" filename={filename!r}"
        f" title={job.get('title', '?')[:60]!r}"
        f" username={job.get('username', '?')!r}"
        f" status=uploading"
    )
    _db_update_job(db_id, status="uploading")

    def _noop() -> None:
        pass

    try:
        _sftp_step(staged_path, _noop)
        _db_update_job(db_id, status="ready", finished_at=time.time())
        print(
            f"[YT_STAGING] stage=request_promote"
            f" request_id={db_id}"
            f" filename={filename!r}"
            f" result=success"
        )
        # Post-upload: rescan → playlist assign → skip → room announce
        _azura_post_upload(filename, db_id, bot=bot, loop=loop)
        # Remove the local staging file; SFTP now has the copy
        try:
            os.remove(staged_path)
        except Exception:
            pass
        return True

    except Exception as exc:
        print(
            f"[YT_STAGING] stage=request_promote"
            f" request_id={db_id}"
            f" filename={filename!r}"
            f" result=fail"
            f" error={exc!r}"
        )
        # Revert to staged so the poll loop retries on the next cycle
        try:
            _db_update_job(db_id, status="staged")
        except Exception:
            pass
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Command handlers
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Formatting helpers for !now display
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_secs(secs: int) -> str:
    """Format seconds → M:SS (e.g. 184 → '3:04')."""
    m, s = divmod(max(0, int(secs)), 60)
    return f"{m}:{s:02d}"


def _progress_bar(elapsed: int, total: int, cells: int = 10) -> str:
    """Unicode block progress bar, e.g. '▰▰▰▱▱▱▱▱▱▱'."""
    if total <= 0:
        return "▱" * cells
    filled = round(cells * min(elapsed, total) / total)
    return "▰" * filled + "▱" * (cells - filled)


# ─────────────────────────────────────────────────────────────────────────────
# AzuraCast skip helper
# ─────────────────────────────────────────────────────────────────────────────

def _azura_skip_song() -> bool:
    """Blocking: POST /api/station/{id}/backend/skip. Returns True on success."""
    import requests as req_lib

    cfg = _azura_api_cfg()
    if cfg is None:
        return False
    try:
        resp = req_lib.post(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/backend/skip",
            headers={"X-API-Key": cfg["api_key"], "Accept": "application/json"},
            timeout=10,
        )
        print(f"[YT_SKIP] Skip → HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.status_code in (200, 204)
    except Exception as exc:
        print(f"[YT_SKIP] Skip error: {exc}")
    return False


def _azura_skip_with_retry(max_attempts: int = 3, delay: float = 2.0) -> bool:
    """
    Skip current AzuraCast song with retry + verification.

    1. Snapshot the current song ID from the Now Playing API.
    2. POST backend/skip.
    3. Wait `delay` seconds, then re-fetch Now Playing.
    4. If the song ID changed — verified success.  If not, retry up to
       `max_attempts` times.
    5. If unverified after all attempts, return False (caller decides what to tell room).

    Never raises; always returns bool.
    """
    import requests as req_lib
    import time as _time

    cfg = _azura_api_cfg()
    if cfg is None:
        return False

    headers  = {"X-API-Key": cfg["api_key"], "Accept": "application/json"}
    skip_url = f"{cfg['base_url']}/api/station/{cfg['station_id']}/backend/skip"

    # Snapshot song ID before skip
    id_before = ""
    np_before = _azura_fetch_nowplaying()
    if np_before:
        id_before = (
            (np_before.get("now_playing") or {}).get("song", {}).get("id", "")
        ) or ""

    for attempt in range(1, max_attempts + 1):
        try:
            resp = req_lib.post(skip_url, headers=headers, timeout=10)
            print(
                f"[YT_SKIP] Attempt {attempt}/{max_attempts}"
                f" → HTTP {resp.status_code}: {resp.text[:200]}"
            )
            if resp.status_code in (200, 204):
                if not id_before:
                    return True  # can't verify; treat accepted as success
                _time.sleep(delay)
                np_after = _azura_fetch_nowplaying()
                if np_after:
                    id_after = (
                        (np_after.get("now_playing") or {}).get("song", {}).get("id", "")
                    ) or ""
                    if id_after and id_after != id_before:
                        print(f"[YT_SKIP] ✓ Verified song changed on attempt {attempt}.")
                        return True
                    print(f"[YT_SKIP] Song unchanged after attempt {attempt}.")
        except Exception as exc:
            print(f"[YT_SKIP] Skip attempt {attempt} error: {exc}")

        if attempt < max_attempts:
            _time.sleep(delay)

    print(f"[YT_SKIP] Skip unconfirmed after {max_attempts} attempts.")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Public radio command handlers  (!play  !now  !skip)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_play(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!play <song name or YouTube URL> — request a song for ChillTopia Radio.

    • URL  → directly queues the song for SFTP upload.
    • Text → searches YouTube and shows top 5; user picks with !pick <1-5>.
    Aliases: !request !sr !song !req !ytrequest
    """
    if not _dash.dashboard_gate_open("radio", "requests_enabled"):
        await _w(bot, user.id, "📻 Song requests are currently disabled.")
        return

    if _sftp_missing_vars():
        await _w(bot, user.id, "📻 YT Requests not configured (missing SFTP secrets).")
        return

    if len(args) < 2:
        cd = _cooldown_secs()
        await _w(
            bot, user.id,
            f"🎵 !play <song name or YouTube URL>\n"
            f"Search: !play despacito\n"
            f"Direct: !play youtu.be/...\n"
            f"Cooldown: {cd}s"
        )
        return

    query = " ".join(args[1:]).strip()[:120]
    if not query:
        await _w(bot, user.id, "🎵 Please include a song name or YouTube URL.")
        return

    # YouTube URL → skip search, go straight to upload
    if _is_youtube_playlist_url(query):
        await _w(bot, user.id, "⚠️ Please use one YouTube song URL, not a playlist or mix.")
        return
    if _is_youtube_url(query):
        await handle_ytrequest(bot, user, [args[0], query])
        return

    # Text search → show top 5 results
    await _w(bot, user.id, f"🔍 Searching: {query[:60]}…")

    loop = asyncio.get_running_loop()
    try:
        results = await loop.run_in_executor(None, _yt_search_sync, query, 5)
    except Exception as exc:
        await _w(bot, user.id, f"❌ Search error: {str(exc)[:80]}")
        return

    if not results:
        await _w(bot, user.id, "❌ No results found. Try a different search.")
        return

    with _yt_pending_lock:
        _yt_pending[user.id]    = results
        _yt_pending_ts[user.id] = time.time()

    max_min = _MAX_DURATION_SECS // 60
    await _w(
        bot, user.id,
        f"🔎 {query[:55]}\n!pick 1-{len(results)} to select | max {max_min}m",
    )
    for i, r in enumerate(results, 1):
        flag = " ⚠️" if r["duration_secs"] > _MAX_DURATION_SECS else ""
        await _w(bot, user.id, f"{i}. {r['title'][:60]}\n⏱ {r['duration']}{flag}")
        await asyncio.sleep(0.1)


async def handle_now(bot: "BaseBot", user: "User", _args: list[str]) -> None:
    """!now / !np / !nowplaying — delegates to unified resolver + renderer."""
    import modules.radio_commands as _rc
    await _rc.handle_nowplaying(bot, user, _args)


async def handle_skip(bot: "BaseBot", user: "User", _args: list[str]) -> None:
    """!skip — skip current song. Admins always; VIP members if vip_priority enabled."""
    _is_adm     = is_admin(user.username)
    _is_vip_usr = not _is_adm and _is_vip(user.id) and _vip_priority()
    if not _is_adm and not _is_vip_usr:
        await _w(bot, user.id, "🔒 Admins and VIP members only.")
        return

    if _azura_api_cfg() is None:
        await _w(bot, user.id, "📻 Radio API not configured.")
        return

    # Refund the currently-playing paid request (if applicable)
    playing_db_id = _currently_playing_db_id
    if playing_db_id and _refund_if_leaves():
        try:
            with sqlite3.connect(_DB_PATH) as _rc:
                row = _rc.execute(
                    "SELECT user_id, coins_charged FROM yt_request_jobs WHERE id=?",
                    (playing_db_id,),
                ).fetchone()
            if row and row[0] and int(row[1] or 0) > 0:
                _refund_coins(row[0], int(row[1]))
                print(f"[YT_SKIP] Refunded {row[1]} coins to {row[0]} on skip.")
        except Exception as _re:
            print(f"[YT_SKIP] Refund check error (non-fatal): {_re}")

    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(None, _azura_skip_with_retry)
    if ok:
        await _w(bot, user.id, "⏭ Song skipped.")
        try:
            await bot.highrise.chat("⏭ Song skipped.")
        except Exception:
            pass
    else:
        await _w(
            bot, user.id,
            "⚠️ Skip sent but song may not have changed yet. Check !now in a moment.",
        )


async def handle_ytrequest(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!ytrequest <youtube_url> — download and add to AzuraCast Requests playlist."""
    if not _dash.dashboard_gate_open("radio", "requests_enabled"):
        await _w(bot, user.id, "📻 Song requests are currently disabled.")
        return

    # ── SFTP readiness check ─────────────────────────────────────────────────
    missing = _sftp_missing_vars()
    if missing:
        print(f"[YT_REQUEST] blocked — missing env vars: {', '.join(missing)}")
        await _w(bot, user.id, "📻 Radio requests are not available right now.")
        return

    # ── Usage hint ───────────────────────────────────────────────────────────
    if len(args) < 2:
        cd   = _cooldown_secs()
        cost = _request_cost()
        cost_str = f"Cost: {cost} chill coins" if cost > 0 else "Free for everyone"
        await _w(
            bot, user.id,
            f"🎵 !play <song name or YouTube URL>\n"
            f"Cooldown: {cd}s | {cost_str}"
        )
        return

    url = args[1].strip()
    if _is_youtube_playlist_url(url):
        await _w(bot, user.id, "⚠️ Please use one YouTube song URL, not a playlist or mix.")
        return

    # ── Validate URL ─────────────────────────────────────────────────────────
    if not _is_youtube_url(url):
        await _w(bot, user.id, "⚠️ Invalid URL. Try: !play <song name> to search.")
        return

    # ── Banned requester check ───────────────────────────────────────────────
    if _is_banned_requester(user.username):
        await _w(bot, user.id, "🚫 You are not allowed to make song requests.")
        return

    # ── Fast video ID ban check (no network) ─────────────────────────────────
    vid_id = _extract_video_id_from_url(url)
    if vid_id and _is_banned_track_by_id(vid_id):
        await _w(bot, user.id, "🚫 That song is not allowed in this room.")
        return

    active_dup = _active_duplicate_for_request(url=url, video_id=vid_id)
    if active_dup:
        print(
            f"[RADIO_HARDEN] event=duplicate_request_superseded"
            f" old_request_id=0 kept_request_id={active_dup.get('id')}"
            f" reason='active_duplicate_request'"
        )
        await _w(
            bot,
            user.id,
            f"📋 That song is already in the request queue as #{active_dup.get('id')}.",
        )
        return

    # ── Duplicate check (same URL in last 24 h) ──────────────────────────────
    _owner_flag = is_owner(user.username)
    if not _owner_flag:
        _dup = _db_check_dedup(url, _DEDUP_WINDOW_SECS)
        if _dup:
            t   = (_dup["title"] or "")[:50]
            msg = "⚠️ That song was already played recently."
            if t:
                msg += f"\n🎵 {t}"
            msg += "\nTry a different song."
            await _w(bot, user.id, msg)
            return

    # ── Per-user cooldown ─────────────────────────────────────────────────────
    _owner = _owner_flag
    _admin = not _owner and is_admin(user.username)
    cd = 0 if _owner else (30 if _admin else _cooldown_secs())

    if cd > 0:
        elapsed = time.time() - _cooldowns.get(user.id, 0.0)
        if elapsed < cd:
            remaining = int(cd - elapsed)
            await _w(bot, user.id, f"⏳ Please wait {remaining}s before your next request.")
            return

    # ── Queue capacity check ──────────────────────────────────────────────────
    active_count = rq.active_count()
    max_queue = cs.max_active_queue_limit()
    if active_count >= max_queue:
        await _w(
            bot, user.id,
            f"📋 Queue is full ({active_count}/{max_queue} requests in progress). Please wait.",
        )
        return

    # ── Payment logic ─────────────────────────────────────────────────────────
    coins_to_charge = 0
    payment_type    = "free"
    priority        = 0

    if _owner or _admin:
        payment_type = "admin"
        priority     = 1
    else:
        # VIP users pay the same price as all other users.
        # Only owner/admin get free requests.
        cost = _request_cost()
        if cost > 0:
            bal = db.get_balance(user.id)
            if bal < cost:
                await _w(
                    bot, user.id,
                    f"💰 You need {cost} chill coins to request a song.\n"
                    f"Your balance: {bal} coins.\n"
                    f"Earn coins by playing games or mining!"
                )
                return
            coins_to_charge = cost
            payment_type    = "coins"

    if coins_to_charge > 0 and not _charge_coins(user.id, coins_to_charge):
        await _w(bot, user.id, f"💰 Not enough coins. You need {coins_to_charge} chill coins.")
        return

    # ── Create job + track presence ───────────────────────────────────────────
    _cooldowns[user.id] = time.time()
    job = _new_job(
        user.id, user.username, url,
        coins_charged=coins_to_charge,
        payment_type=payment_type,
        priority=priority,
    )

    with _presence_lock:
        _active_presence[user.id] = {
            "db_id":         job["db_id"],
            "job_id":        job["id"],
            "coins_charged": coins_to_charge,
            "payment_type":  payment_type,
            "username":      user.username,
        }

    # ── User-friendly confirmation ─────────────────────────────────────────────
    await _w(
        bot,
        user.id,
        rq.render_added_to_queue_message(
            position=rq.future_count(),
            priority=priority,
            staff_free=(payment_type == "admin"),
        ),
    )
    print(
        f"[RADIO_CLEAN] event=queue_confirmation_preserved"
        f" request_id={job.get('db_id', 0)} title={job.get('title', '')!r}"
    )

    asyncio.create_task(_run_job(bot, job))


async def handle_ytqueue(bot: "BaseBot", user: "User", _args: list[str]) -> None:
    """!ytqueue — show recent YT request jobs from DB (admin+)."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return

    rows = _db_recent_jobs(limit=8)
    if not rows:
        await _w(bot, user.id, "📋 YT Requests: no jobs logged yet.")
        return

    _STATUS_ICON = {
        "pending":     "⏳",
        "downloading": "⬇️",
        "uploading":   "📤",
        "done":        "✅",
        "error":       "❌",
    }
    lines = ["📋 YT Requests (newest first):"]
    for j in rows:
        icon  = _STATUS_ICON.get(j["status"], "?")
        title = f" — {j['title'][:25]}" if j["title"] else ""
        err   = f" [{j['error'][:18]}]" if j["status"] == "error" and j["error"] else ""
        lines.append(f"{icon} #{j['id']} @{j['username']}{title}{err}")

    await _w(bot, user.id, "\n".join(lines))


async def handle_ytstatus(bot: "BaseBot", user: "User", _args: list[str]) -> None:
    """!ytstatus / !radiostatus — YT request config + per-status job counts (admin+)."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return

    cfg   = _sftp_cfg()
    ready = _sftp_ready()
    cd    = _cooldown_secs()
    api   = _azura_api_cfg()

    sftp_ok = "✅" if ready else "❌ missing"
    host_d  = cfg["host"][:20] if cfg["host"] else "(not set)"
    api_ok  = "✅" if api else "⬜ disabled"
    station = api["station_id"] if api else "?"
    folder  = cfg["folder"] or "(not set)"
    plist   = os.environ.get("AZURA_PLAYLIST_ID", "?")[:10]

    await _w(
        bot, user.id,
        f"📻 YT Request Status\n"
        f"SFTP: {sftp_ok} {host_d}:{cfg['port']}\n"
        f"path: {folder}  API: {api_ok}\n"
        f"station: {station}  playlist: {plist}  CD: {cd}s",
    )

    # ── Per-status counts from DB ──────────────────────────────────────────────
    try:
        import sqlite3 as _sq
        with _sq.connect(_DB_PATH) as _conn:
            _rows = _conn.execute(
                "SELECT status, COUNT(*) FROM yt_request_jobs "
                "WHERE played_at IS NULL GROUP BY status"
            ).fetchall()
            _counts: dict = {r[0]: r[1] for r in _rows}
            _pd_row = _conn.execute(
                "SELECT COUNT(*) FROM yt_request_jobs "
                "WHERE strftime('%Y-%m-%d', played_at) = strftime('%Y-%m-%d', 'now')"
            ).fetchone()
            _played_today = _pd_row[0] if _pd_row else 0
    except Exception as _exc:
        await _w(bot, user.id, f"⚠️ DB error: {str(_exc)[:80]}")
        return

    _order = ("pending", "downloading", "uploading", "staged", "ready", "playing", "error")
    _parts = [f"{s}: {_counts.get(s, 0)}" for s in _order]
    _parts.append(f"played today: {_played_today}")
    await _w(bot, user.id, "📋 Status counts\n" + "\n".join(_parts))


async def handle_ytnow(bot: "BaseBot", user: "User", _args: list[str]) -> None:
    """!ytnow — show the latest successfully requested song (public)."""
    rows = _db_recent_jobs(limit=20)
    done = [r for r in rows if r["status"] in ("ready", "playing", "played")]
    if not done:
        await _w(bot, user.id, "🎵 No songs added to radio via YT Request yet.")
        return
    j     = done[0]
    title = (j["title"] or "(unknown)")[:100]
    when  = (j["finished_at"] or j["started_at"] or "")[:16].replace("T", " ")
    await _w(
        bot, user.id,
        f"🎵 Latest YT Request:\n{title}\nBy @{j['username']} | {when} UTC"[:249],
    )


async def handle_ytcooldown(bot: "BaseBot", user: "User", _args: list[str]) -> None:
    """!ytcooldown — show YT request cooldown and limit settings (admin+)."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return
    cd      = _cooldown_secs()
    max_min = _MAX_DURATION_SECS // 60
    dedup_h = _DEDUP_WINDOW_SECS // 3600
    await _w(
        bot, user.id,
        (f"⏳ YT Request Settings:\n"
         f"Cooldown: {cd}s | admin: 30s | owner: 0s\n"
         f"Max length: {max_min}m | Dedup window: {dedup_h}h\n"
         f"Change: !setytcooldown <seconds>")[:249],
    )


async def handle_setytcooldown(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!setytcooldown <seconds> — set per-user YT request cooldown (admin+)."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !setytcooldown <seconds>  (30–3600)")
        return
    try:
        secs = max(30, min(3600, int(args[1])))
    except ValueError:
        await _w(bot, user.id, "⚠️ Invalid value. Must be a number (30–3600).")
        return
    db.set_room_setting("yt_request_cooldown", str(secs))
    await _w(bot, user.id, f"✅ YT Request cooldown set to {secs}s.")


async def handle_request(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!request <song name>  — search YouTube, show top 5 for AzuraCast upload.
    If a YouTube URL is given instead, delegates directly to handle_ytrequest.
    """
    if not _dash.dashboard_gate_open("radio", "requests_enabled"):
        await _w(bot, user.id, "📻 Song requests are currently disabled.")
        return

    # SFTP must be ready — no point searching if we can't upload
    if _sftp_missing_vars():
        await _w(bot, user.id, "📻 YT Requests not configured (missing SFTP secrets).")
        return

    if len(args) < 2:
        await _w(
            bot, user.id,
            "🎵 !request <song name> — search YouTube\n"
            "Then !pick <1-5> to upload to radio.\n"
            "Or: !ytrequest <url> to add directly."
        )
        return

    query = " ".join(args[1:]).strip()[:120]
    if not query:
        await _w(bot, user.id, "🎵 Please include a song name.")
        return

    # If it's a YouTube URL, skip search and go straight to upload
    if _is_youtube_playlist_url(query):
        await _w(bot, user.id, "⚠️ Please use one YouTube song URL, not a playlist or mix.")
        return
    if _is_youtube_url(query):
        await handle_ytrequest(bot, user, [args[0], query])
        return

    await _w(bot, user.id, f"🔍 Searching YouTube: {query[:60]}…")

    loop = asyncio.get_running_loop()
    try:
        results = await loop.run_in_executor(None, _yt_search_sync, query, 5)
    except Exception as exc:
        await _w(bot, user.id, f"❌ Search error: {str(exc)[:80]}")
        return

    if not results:
        await _w(bot, user.id, "❌ No results found. Try a different search.")
        return

    with _yt_pending_lock:
        _yt_pending[user.id]    = results
        _yt_pending_ts[user.id] = time.time()

    max_min = _MAX_DURATION_SECS // 60
    await _w(
        bot, user.id,
        f"🔎 {query[:55]}\n!pick 1-{len(results)} to select | max {max_min}m",
    )
    for i, r in enumerate(results, 1):
        flag = " ⚠️" if r["duration_secs"] > _MAX_DURATION_SECS else ""
        await _w(bot, user.id, f"{i}. {r['title'][:60]}\n⏱ {r['duration']}{flag}")
        await asyncio.sleep(0.1)


async def handle_ytpick(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!pick <1-5>  — upload the selected search result to AzuraCast via SFTP.
    Only runs when the user has a pending !request search. Otherwise the main.py
    routing falls through to handle_dj_pick (in-room DJ queue).
    """
    with _yt_pending_lock:
        results = _yt_pending.get(user.id) if _yt_pending_is_fresh(user.id) else None

    if not results:
        await _w(
            bot, user.id,
            "🎵 No pending search. Use !request <song name> first."
        )
        return

    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, f"Usage: !pick <1–{len(results)}>")
        return

    num = int(args[1])
    if not (1 <= num <= len(results)):
        await _w(bot, user.id, f"⚠️ Pick a number between 1 and {len(results)}.")
        return

    chosen = results[num - 1]
    url    = chosen["url"]

    # Reject too-long tracks before even starting the download
    if chosen["duration_secs"] > _MAX_DURATION_SECS:
        max_min = _MAX_DURATION_SECS // 60
        await _w(
            bot, user.id,
            f"❌ Track too long: {chosen['title'][:50]} [{chosen['duration']}]\n"
            f"Max {max_min} min. Pick another result or !request a shorter song."
        )
        return

    # Consume the pending search (one-shot)
    with _yt_pending_lock:
        _yt_pending.pop(user.id, None)
        _yt_pending_ts.pop(user.id, None)

    # Whisper what was picked, then let handle_ytrequest do all validation + upload
    await _w(
        bot, user.id,
        f"🎵 Picked: {chosen['title'][:80]} [{chosen['duration']}]\nStarting upload…"
    )
    await handle_ytrequest(bot, user, [args[0], url])


# ─────────────────────────────────────────────────────────────────────────────
# AzuraCast cleanup — blocking helpers + background poll task
# ─────────────────────────────────────────────────────────────────────────────

_NOWPLAYING_POLL_SECS = 10   # how often to poll the Now Playing API
_HISTORY_POLL_SECS   = 90   # how often to run the history fallback sweep

# {azura_song_id: db_job_id} for requests currently being tracked in now-playing.
# Module-level so it persists across loop iterations; mutated in-place (no global needed).
_seen_playing: dict[str, int] = {}
_seen_announced:   set[str]       = set()  # AzuraCast song IDs already announced
_radio_skip_votes: dict[str, set] = {}     # song_id → set of voter user_ids
_radio_vote_lock   = threading.Lock()      # guards _radio_skip_votes


def _auto_delete_enabled() -> bool:
    """Return True unless REQUEST_AUTO_DELETE_AFTER_PLAY is explicitly falsy."""
    val = (os.environ.get("REQUEST_AUTO_DELETE_AFTER_PLAY") or "true").strip().lower()
    return val in ("1", "true", "yes")


def _azura_fetch_nowplaying() -> "dict | None":
    """
    Blocking: GET /api/nowplaying/{station_id}
    Returns the full now-playing response dict, or None on error.
    The public now-playing endpoint does not require an API key.
    """
    import requests as req_lib

    cfg = _azura_api_cfg()
    if cfg is None:
        return None
    try:
        resp = req_lib.get(
            f"{cfg['base_url']}/api/nowplaying/{cfg['station_id']}",
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
        print(f"[YT_NOWPLAY] Now-playing HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as exc:
        print(f"[YT_NOWPLAY] Now-playing fetch error: {exc}")
    return None


def _azura_fetch_history(rows: int = 150) -> list[dict]:
    """
    Blocking: GET /api/station/{id}/history — return recently-played song dicts.
    Each dict has a 'song' sub-dict with an 'id' field (the unique_id we store).
    """
    import requests as req_lib

    cfg = _azura_api_cfg()
    if cfg is None:
        return []
    try:
        resp = req_lib.get(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/history",
            params={"rows": rows},
            headers={
                "X-API-Key": cfg["api_key"],
                "Accept":    "application/json",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            # History can be {"rows": [...]} or a bare list
            return data if isinstance(data, list) else data.get("rows", [])
        print(f"[YT_CLEANUP] History fetch HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as exc:
        print(f"[YT_CLEANUP] History fetch error: {exc}")
    return []


def _azura_delete_file(file_id: str) -> bool:
    """
    Blocking: DELETE /api/station/{id}/file/{file_id}
    Returns True if AzuraCast acknowledged the deletion (HTTP 200 or 204).
    Only touches files in the Requests folder (file_id was set only for those).
    """
    import requests as req_lib

    cfg = _azura_api_cfg()
    if cfg is None or not file_id:
        return False
    try:
        resp = req_lib.delete(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/file/{file_id}",
            headers={
                "X-API-Key":    cfg["api_key"],
                "Content-Type": "application/json",
                "Accept":       "application/json",
            },
            timeout=15,
        )
        ok = resp.status_code in (200, 204, 404)
        print(
            f"[YT_CLEANUP] DELETE file/{file_id}"
            f" → HTTP {resp.status_code} ok={ok}: {resp.text[:200]}"
        )
        return ok
    except Exception as exc:
        print(f"[YT_CLEANUP] Delete file/{file_id} error: {exc}")
    return False


def _azura_sftp_delete(filename: str) -> bool:
    """
    Blocking SFTP delete fallback.
    Connects and removes Requests/<filename> directly via SFTP.
    Called when the AzuraCast API delete fails or azura_file_id is empty.
    Treats FileNotFoundError as success (file already gone).
    Returns True on success or if file was already absent.
    """
    import paramiko

    if not filename:
        return False

    cfg         = _sftp_cfg()
    remote_path = f"{cfg['folder'].rstrip('/')}/{filename}"
    print(f"[YT_CLEANUP] SFTP delete fallback → {remote_path}")

    ssh  = paramiko.SSHClient()
    sftp = None
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(
            hostname=cfg["host"],
            port=cfg["port"],
            username=cfg["user"],
            password=cfg["passwd"],
            timeout=30,
            banner_timeout=30,
            auth_timeout=30,
            look_for_keys=False,
            allow_agent=False,
        )
        sftp = ssh.open_sftp()
        sftp.remove(remote_path)
        print(f"[YT_CLEANUP] ✓ SFTP deleted: {remote_path}")
        return True
    except IOError as exc:
        # paramiko raises IOError (errno 2) for "no such file"
        if getattr(exc, "errno", None) == 2 or "No such file" in str(exc):
            print(f"[YT_CLEANUP] SFTP: file already gone — {remote_path}")
            return True  # treat as success
        print(f"[YT_CLEANUP] SFTP delete IOError: {exc}")
        return False
    except Exception as exc:
        print(f"[YT_CLEANUP] SFTP delete failed: {exc}")
        return False
    finally:
        try:
            if sftp:
                sftp.close()
        except Exception:
            pass
        try:
            ssh.close()
        except Exception:
            pass


def _azura_full_cleanup(job: dict) -> bool:
    """
    Full post-play cleanup sequence (blocking — safe to call via run_in_executor).

    Stages executed in order:
        request_cleanup_remove_playlist — PUT /file/{id} {"playlists":[]}
        request_cleanup_delete_file     — DELETE /file/{id}  (SFTP fallback if API fails)
        request_cleanup_rescan          — POST /files/batch {"do":"rescan"}
        request_cleanup_verify          — GET /files confirm file is gone

    Safety:
      • Only processes files whose filename ends in .mp3 (bot-uploaded requests only).
      • Never touches non-Requests files — file_id was only written for uploads we made.
      • Idempotent: 404 / already-gone = success.
    Returns True if the file was confirmed deleted (or already absent).
    """
    import requests as _rq_lib

    cfg         = _azura_api_cfg()
    fid         = (job.get("azura_file_id") or "").strip()
    fn          = (job.get("filename")      or "").strip()
    playlist_id = (os.environ.get("AZURA_PLAYLIST_ID") or "").strip()

    def _clog(stage: str, result: str, **kw: str) -> None:
        extras = "".join(f" {k}={v}" for k, v in kw.items())
        print(f"[YT_API] stage={stage} media_id={fid!r} filename={fn!r} result={result}{extras}")

    if fn and not fn.lower().endswith(".mp3"):
        _clog("request_cleanup_delete_file", "skipped", note="not_mp3_guard")
        return False

    deleted = False

    # ── Step 1: remove from Requests playlist ────────────────────────────────
    if cfg and fid:
        _hdrs = {
            "X-API-Key":    cfg["api_key"],
            "Content-Type": "application/json",
            "Accept":       "application/json",
        }
        if playlist_id:
            try:
                r = _rq_lib.put(
                    f"{cfg['base_url']}/api/station/{cfg['station_id']}/file/{fid}",
                    json={"playlists": []},
                    headers=_hdrs,
                    timeout=15,
                )
                _clog("request_cleanup_remove_playlist",
                      "success" if r.status_code in (200, 204, 404) else "fail",
                      http=str(r.status_code))
            except Exception as exc:
                _clog("request_cleanup_remove_playlist", "error",
                      exception=repr(str(exc)))
        else:
            _clog("request_cleanup_remove_playlist", "skipped",
                  note="AZURA_PLAYLIST_ID_not_set")

        # ── Step 2: delete file via API ───────────────────────────────────────
        try:
            r = _rq_lib.delete(
                f"{cfg['base_url']}/api/station/{cfg['station_id']}/file/{fid}",
                headers={
                    "X-API-Key": cfg["api_key"],
                    "Accept":    "application/json",
                },
                timeout=15,
            )
            deleted = r.status_code in (200, 204, 404)
            _clog("request_cleanup_delete_file",
                  "success" if deleted else "fail",
                  method="api", http=str(r.status_code))
        except Exception as exc:
            _clog("request_cleanup_delete_file", "error",
                  method="api", exception=repr(str(exc)))

    # Fallback: SFTP delete by filename
    if not deleted and fn:
        deleted = _azura_sftp_delete(fn)
        _clog("request_cleanup_delete_file",
              "success" if deleted else "fail", method="sftp")

    if not deleted:
        return False

    # ── Step 3: rescan after deletion ─────────────────────────────────────────
    if cfg:
        sftp_raw   = (os.environ.get("AZURA_SFTP_PATH") or "Requests").strip()
        rescan_dir = os.path.basename(sftp_raw.rstrip("/")) or sftp_raw
        try:
            r = _rq_lib.post(
                f"{cfg['base_url']}/api/station/{cfg['station_id']}/files/batch",
                json={"do": "rescan", "currentDirectory": rescan_dir},
                headers={
                    "X-API-Key":    cfg["api_key"],
                    "Content-Type": "application/json",
                    "Accept":       "application/json",
                },
                timeout=30,
            )
            _clog("request_cleanup_rescan",
                  "success" if r.status_code in (200, 204) else "fail",
                  http=str(r.status_code))
        except Exception as exc:
            _clog("request_cleanup_rescan", "error", exception=repr(str(exc)))

        # ── Step 4: verify file is gone ───────────────────────────────────────
        if fn:
            time.sleep(2)
            try:
                r = _rq_lib.get(
                    f"{cfg['base_url']}/api/station/{cfg['station_id']}/files",
                    params={"searchPhrase": fn},
                    headers={"X-API-Key": cfg["api_key"], "Accept": "application/json"},
                    timeout=15,
                )
                if r.status_code == 200:
                    _rows = r.json()
                    _rows = _rows if isinstance(_rows, list) else _rows.get("rows", [])
                    still_here = any(
                        os.path.basename(_row.get("path", "")) == fn
                        for _row in _rows
                    )
                    _clog("request_cleanup_verify",
                          "fail_still_exists" if still_here else "success")
                else:
                    _clog("request_cleanup_verify", f"http_{r.status_code}")
            except Exception as exc:
                _clog("request_cleanup_verify", "error", exception=repr(str(exc)))

    return True


async def _run_nowplaying_cycle(
    prev_song_id_ref: list[str],
    bot_inst: "BaseBot | None" = None,
) -> None:
    """
    One 15-second poll cycle:

    Phase 1 — Now Playing detection:
      • Fetch current now-playing song from AzuraCast.
      • If it's one of our tracked request songs and we haven't seen it yet,
        mark it as 'played' in the DB and record it in _seen_playing.

    Phase 2 — Post-play deletion:
      • If the previous song has transitioned out (different song now playing)
        AND it was one of our requests (in _seen_playing), delete its file
        from AzuraCast now.  AzuraCast will have already buffered + played
        the song; deleting the file prevents any future replay.

    History fallback (runs every _HISTORY_POLL_SECS / _NOWPLAYING_POLL_SECS cycles):
      • Sweep play history for any tracked request songs that have played but
        whose files have not been deleted yet.  This catches songs that played
        while the bot was offline or before the now-playing loop was tracking them.
    """
    global _currently_playing_db_id
    loop = asyncio.get_running_loop()

    pending = _db_get_pending_cleanup()

    # ── Build three lookup maps for multi-strategy matching ──────────────────
    pending_by_sid:      dict[str, dict] = {}  # azura_song_id     → job
    pending_by_vid:      dict[str, dict] = {}  # video_id          → job
    pending_by_title:    dict[str, dict] = {}  # lower-cased title → job
    pending_by_filename: dict[str, dict] = {}  # filename stem     → job

    for j in pending:
        sid = (j.get("azura_song_id") or "").strip()
        if sid:
            pending_by_sid[sid] = j

        vid = (j.get("video_id") or "").strip()
        if vid:
            pending_by_vid[vid] = j

        t = (j.get("title") or "").strip()
        if t:
            pending_by_title[t.lower()] = j

        fn = (j.get("filename") or "").strip()
        stem = fn[:-4] if fn.lower().endswith(".mp3") else fn
        if stem:
            pending_by_filename[stem.lower()] = j

    # ── Phase 1 + 2: Now Playing ─────────────────────────────────────────────
    np_data = await loop.run_in_executor(None, _azura_fetch_nowplaying)
    current_song_id = ""
    matched_job: "dict | None" = None

    if np_data:
        np_obj      = np_data.get("now_playing") or {}
        song        = np_obj.get("song") or {}
        current_song_id = (song.get("id") or "").strip()
        np_title    = (song.get("title") or "").strip()
        np_title_lc = np_title.lower()

        # Strategy 1: azura_song_id exact match (primary — reliable once ID3 tags set)
        if current_song_id and current_song_id in pending_by_sid:
            matched_job = pending_by_sid[current_song_id]

        # Strategy 2: video_id appears inside AzuraCast's song hash or title
        if matched_job is None:
            for vid, j in pending_by_vid.items():
                if vid and (vid in current_song_id or vid.lower() in np_title_lc):
                    matched_job = j
                    print(f"[YT_NOWPLAY] Matched by video_id: {vid}")
                    break

        # Strategy 3: stored YouTube title matches what AzuraCast reports
        if matched_job is None and np_title_lc:
            if np_title_lc in pending_by_title:
                matched_job = pending_by_title[np_title_lc]
                print(f"[YT_NOWPLAY] Matched by title: {np_title[:50]}")
            else:
                for stored_t, j in pending_by_title.items():
                    if stored_t and stored_t in np_title_lc:
                        matched_job = j
                        print(f"[YT_NOWPLAY] Matched by partial title: {stored_t[:50]}")
                        break

        # Strategy 4: AzuraCast showing raw filename (no ID3 tags on old upload)
        if matched_job is None and np_title_lc:
            if np_title_lc in pending_by_filename:
                matched_job = pending_by_filename[np_title_lc]
                print(f"[YT_NOWPLAY] Matched by filename stem: {np_title}")

    # Phase 1: request song just started playing — mark playing + track
    # Use current_song_id as the tracking key (AzuraCast's stable per-song ID)
    track_key = current_song_id or ""
    if matched_job and track_key and track_key not in _seen_playing:
        _seen_playing[track_key] = matched_job["id"]
        _db_mark_playing(matched_job["id"])
        _currently_playing_db_id = matched_job["id"]
        _fid_log = (matched_job.get("azura_file_id") or "")
        _fn_log  = (matched_job.get("filename")      or "")
        _uln_log = (matched_job.get("username")      or "?")
        print(
            f"[YT_API] stage=request_started"
            f" media_id={_fid_log!r}"
            f" filename={_fn_log!r}"
            f" title={(matched_job.get('title') or '?')[:60]!r}"
            f" user={_uln_log!r}"
        )

    # ── Announce every new track once (chill / party / request) ──────────────
    if current_song_id and current_song_id not in _seen_announced and bot_inst and np_data:
        _seen_announced.add(current_song_id)
        if len(_seen_announced) > 500:
            _seen_announced.clear()
            _seen_announced.add(current_song_id)

        np_song   = (np_data.get("now_playing") or {}).get("song", {})
        np_artist = (np_song.get("artist") or "").strip()
        if np_artist and np_artist.lower() not in np_title.lower():
            song_text = f"{np_artist} — {np_title}"
        else:
            song_text = np_title or "Unknown"

        # Room announcement is handled exclusively by dj_announcer.py via
        # playback_engine. Sending a chat here would create a duplicate message.
        if matched_job:
            uname = (matched_job.get("username") or "?")[:20]
            print(f"[YT_ANNOUNCE] Track noted (request @{uname}): {song_text[:80]}")
        else:
            print(f"[YT_ANNOUNCE] Track noted (AutoDJ): {song_text[:80]}")

    # ── Reset radio vote-skip state on song change ────────────────────────────
    if current_song_id and current_song_id != prev_song_id_ref[0]:
        with _radio_vote_lock:
            _radio_skip_votes.clear()

    # Phase 2: previous request song finished → full cleanup sequence
    prev_id = prev_song_id_ref[0]
    if prev_id and prev_id != current_song_id and prev_id in _seen_playing:
        job_id    = _seen_playing.pop(prev_id)
        if _currently_playing_db_id == job_id:
            _currently_playing_db_id = 0
        job_match = next((j for j in pending if j["id"] == job_id), None)
        if job_match:
            fid       = (job_match.get("azura_file_id") or "").strip()
            fn        = (job_match.get("filename")      or "").strip()
            title_log = (job_match.get("title")    or "?")[:60]
            uname_log = (job_match.get("username") or "?")
            print(
                f"[YT_API] stage=request_finished"
                f" media_id={fid!r} filename={fn!r}"
                f" title={title_log!r} user={uname_log!r}"
            )
            _db_mark_played(job_match["id"])
            if _auto_delete_enabled():
                ok = await loop.run_in_executor(None, _azura_full_cleanup, job_match)
                if ok:
                    _db_mark_cleaned(job_match["id"])

    prev_song_id_ref[0] = current_song_id


async def _run_history_fallback() -> None:
    """
    History-sweep fallback: fetch recent AzuraCast play history and delete
    files for any tracked request songs that appear in it but haven't been
    cleaned yet.  Runs at a slower cadence than the now-playing loop.
    Handles songs that played while the bot was offline.
    """
    loop = asyncio.get_running_loop()

    pending = _db_get_pending_cleanup()
    if not pending:
        return

    history = await loop.run_in_executor(None, _azura_fetch_history, 150)
    if not history:
        return

    # Collect all song identifiers that appear in recent history
    played_sids:    set[str] = set()   # azura song hashes
    played_titles:  set[str] = set()   # lower-cased titles
    played_texts:   set[str] = set()   # lower-cased "Artist - Title" strings

    for entry in history:
        if not isinstance(entry, dict):
            continue
        s = entry.get("song") or {}
        sid = (s.get("id") or "").strip()
        if sid:
            played_sids.add(sid)
        t = (s.get("title") or "").strip().lower()
        if t:
            played_titles.add(t)
        tx = (s.get("text") or "").strip().lower()
        if tx:
            played_texts.add(tx)

    cleaned = 0
    for job in pending:
        azura_song_id = (job.get("azura_song_id") or "").strip()
        azura_file_id = (job.get("azura_file_id") or "").strip()
        fn            = (job.get("filename")      or "").strip()
        stored_title  = (job.get("title")         or "").strip().lower()
        video_id      = (job.get("video_id")      or "").strip()
        title_log     = (job.get("title") or "?")[:60]

        # Check whether this job appears in history (any strategy)
        in_history = False
        if azura_song_id and azura_song_id in played_sids:
            in_history = True
        elif stored_title and stored_title in played_titles:
            in_history = True
            print(f"[YT_CLEANUP] History match by title: {title_log}")
        elif stored_title:
            for tx in played_texts:
                if stored_title in tx:
                    in_history = True
                    print(f"[YT_CLEANUP] History match by text: {title_log}")
                    break
        if not in_history and video_id:
            for sid in played_sids:
                if video_id in sid:
                    in_history = True
                    print(f"[YT_CLEANUP] History match by video_id: {video_id}")
                    break

        if not in_history:
            continue

        ok = False
        # Primary: AzuraCast API delete
        if azura_file_id:
            ok = await loop.run_in_executor(None, _azura_delete_file, azura_file_id)
            if ok:
                print(f"[YT_CLEANUP] ✓ History-fallback API deleted: {title_log}")
            else:
                print(f"[YT_CLEANUP] History-fallback API delete failed — trying SFTP…")

        # Fallback: SFTP delete by filename
        if not ok and fn:
            ok = await loop.run_in_executor(None, _azura_sftp_delete, fn)
            if ok:
                print(f"[YT_CLEANUP] ✓ History-fallback SFTP deleted: {title_log}")

        if ok:
            _db_mark_played(job["id"])
            _db_mark_cleaned(job["id"])
            _seen_playing.pop(azura_song_id, None)
            cleaned += 1
        else:
            print(f"[YT_CLEANUP] ✗ History-fallback cleanup failed: {title_log}")

    if cleaned:
        print(f"[YT_CLEANUP] {cleaned} request file(s) removed via history fallback.")


async def _cleanup_loop(bot_inst: "BaseBot | None" = None) -> None:
    """
    Legacy cleanup loop retained for import/backwards compatibility only.

    playback_engine.py owns request playback confirmation, played status, and
    post-play cleanup. This loop must not mark yt_request_jobs played/cleaned.
    """
    print("[YT_CLEANUP] Legacy cleanup loop disabled; playback_engine owns cleanup.")
    return


# ─────────────────────────────────────────────────────────────────────────────
# Requester presence protection — called from main.py on_user_leave
# ─────────────────────────────────────────────────────────────────────────────

async def on_request_user_left(bot: "BaseBot", user_id: str, username: str) -> None:
    """
    Called when a user leaves the room.
    • Cancels their pending/in-progress request and refunds coins if applicable.
    • Skips the currently-playing song if it belongs to them and refunds if applicable.
    """
    with _presence_lock:
        info = _active_presence.pop(user_id, None)
    if not info:
        return

    db_id    = info.get("db_id", 0)
    job_id   = info.get("job_id", 0)
    coins    = info.get("coins_charged", 0)
    pay_type = info.get("payment_type", "free")

    # Admin requests: keep running unless setting says otherwise
    if pay_type == "admin" and _admin_ignore_leave():
        with _presence_lock:
            _active_presence[user_id] = info  # restore
        return

    if not _skip_if_requester_leaves():
        return

    # ── Cancel pending/downloading/uploading job ─────────────────────────────
    was_pending = False
    with _jobs_lock:
        for j in _jobs.values():
            if (j.get("db_id") == db_id or j["id"] == job_id) and \
               j["status"] in ("pending", "downloading", "uploading"):
                j["status"] = "cancelled"
                was_pending = True
                break

    if was_pending:
        if coins > 0 and _refund_if_leaves():
            _refund_coins(user_id, coins)
            print(f"[YT_PRESENCE] @{username} left — request cancelled + {coins} coins refunded.")
        else:
            print(f"[YT_PRESENCE] @{username} left — request cancelled.")
        try:
            await bot.highrise.chat(
                f"📻 @{username}'s request was removed (left the room)."[:249]
            )
        except Exception:
            pass
        return

    # ── Their song is currently playing — skip it ────────────────────────────
    if not db_id or db_id != _currently_playing_db_id:
        return

    loop = asyncio.get_running_loop()
    ok   = await loop.run_in_executor(None, _azura_skip_song)
    if ok:
        if coins > 0 and _refund_if_leaves():
            _refund_coins(user_id, coins)
            print(f"[YT_PRESENCE] @{username} left mid-play — skipped + {coins} coins refunded.")
        else:
            print(f"[YT_PRESENCE] @{username} left mid-play — skipped.")
        try:
            await bot.highrise.chat("⏭ Skipping — requester left the room.")
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Admin command handlers — request system settings
# ─────────────────────────────────────────────────────────────────────────────

async def handle_setrequestcost(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!setrequestcost <coins> — set coin cost per request (0 = free for all)."""
    if not is_manager(user.username):
        await _w(bot, user.id, "🔒 Manager only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        cost = _request_cost()
        await _w(bot, user.id, f"💰 Current request cost: {cost} coins.\nUsage: !setrequestcost <coins>")
        return
    val = max(0, int(args[1]))
    db.set_room_setting("request_cost_coins", str(val))
    if val > 0:
        await _w(bot, user.id, f"✅ Request cost set to {val} coins per request.")
    else:
        await _w(bot, user.id, "✅ Requests are now free for everyone.")


async def handle_setprioritycost(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!setprioritycost <tickets> — set luxe ticket cost for priority queue (0 = disabled)."""
    if not is_manager(user.username):
        await _w(bot, user.id, "🔒 Manager only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        cost = _priority_cost_tickets()
        await _w(bot, user.id, f"🎟 Current priority cost: {cost} luxe tickets.\nUsage: !setprioritycost <tickets>")
        return
    val = max(0, int(args[1]))
    db.set_room_setting("request_priority_cost_tickets", str(val))
    if val > 0:
        await _w(bot, user.id, f"✅ Priority cost set to {val} luxe tickets.")
    else:
        await _w(bot, user.id, "✅ Priority queue via luxe tickets disabled.")


async def handle_bantrack(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!bantrack <video_id or keyword> — block a video ID or title keyword from requests."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !bantrack <YouTube video ID or title keyword>")
        return
    pattern = " ".join(args[1:]).strip()
    _db_ban_track(pattern, user.username)
    await _w(bot, user.id, f"🚫 Track banned: {pattern[:80]}")


async def handle_unbantrack(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!unbantrack [pattern] — remove a track/keyword ban, or list all bans."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return
    if len(args) < 2:
        bans   = _db_list_bans()
        tracks = bans["tracks"]
        if not tracks:
            await _w(bot, user.id, "📋 No banned tracks.")
        else:
            lines = ["🚫 Banned tracks:"] + [f"  {i+1}. {t[:50]}" for i, t in enumerate(tracks[:15])]
            await _w(bot, user.id, "\n".join(lines)[:249])
        return
    pattern = " ".join(args[1:]).strip()
    ok = _db_unban_track(pattern)
    if ok:
        await _w(bot, user.id, f"✅ Track unbanned: {pattern[:80]}")
    else:
        await _w(bot, user.id, f"⚠️ No ban found matching: {pattern[:80]}")


async def handle_banrequester(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!banrequester <username> — prevent a user from making requests."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !banrequester <username>")
        return
    target = args[1].lstrip("@").strip().lower()
    if not target:
        await _w(bot, user.id, "⚠️ Please provide a username.")
        return
    _db_ban_requester(target, user.username)
    await _w(bot, user.id, f"🚫 @{target} banned from requests.")


async def handle_unbanrequester(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!unbanrequester [username] — allow a user to request again, or list all bans."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return
    if len(args) < 2:
        bans       = _db_list_bans()
        requesters = bans["requesters"]
        if not requesters:
            await _w(bot, user.id, "📋 No banned requesters.")
        else:
            lines = ["🚫 Banned requesters:"] + [f"  {i+1}. @{r}" for i, r in enumerate(requesters[:15])]
            await _w(bot, user.id, "\n".join(lines)[:249])
        return
    target = args[1].lstrip("@").strip().lower()
    ok = _db_unban_requester(target)
    if ok:
        await _w(bot, user.id, f"✅ @{target} can make requests again.")
    else:
        await _w(bot, user.id, f"⚠️ @{target} is not in the ban list.")


async def handle_queueadmin(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!queueadmin — show request system config overview (admin+)."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return

    cost     = _request_cost()
    pri_cost = _priority_cost_tickets()
    vip_free = _vip_free_requests()
    vip_pri  = _vip_priority()
    skip_lv  = _skip_if_requester_leaves()
    refund_l = _refund_if_leaves()
    bans     = _db_list_bans()

    lines = [
        "📻 Request System",
        f"Cost: {cost} coins | Priority: {pri_cost} tickets",
        f"VIP free: {'on' if vip_free else 'off'} | VIP priority: {'on' if vip_pri else 'off'}",
        f"Skip if leave: {'on' if skip_lv else 'off'} | Refund: {'on' if refund_l else 'off'}",
        f"Banned tracks: {len(bans['tracks'])} | Requesters: {len(bans['requesters'])}",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])


async def startup_yt_cleanup_task(_bot: "BaseBot") -> None:
    """
    Compatibility hook for the former yt_request cleanup background loop.

    Phase 4 ownership: playback_engine.py is the single owner of request
    lifecycle cleanup after jobs become ready. yt_request.py no longer starts
    a loop that marks requests played/cleaned.
    Called from on_start() — DJ bot only, guarded by should_this_bot_run_module.
    """
    print("[YT_CLEANUP] Startup skipped; playback_engine owns request cleanup.")
    return


# ─────────────────────────────────────────────────────────────────────────────
# !clearrequests — admin manual cleanup
# ─────────────────────────────────────────────────────────────────────────────

async def handle_clearrequests(bot: "BaseBot", user: "User", _args: list[str]) -> None:
    """
    !clearrequests — delete all tracked Requests MP3s from AzuraCast
    that have not yet been cleaned up (regardless of whether they played).
    Admin only.  Does NOT touch General playlist songs.
    """
    from modules.permissions import is_admin

    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return

    cfg = _azura_api_cfg()
    if not cfg:
        await _w(bot, user.id, "⚠️ AzuraCast API not configured.")
        return

    pending = _db_get_pending_cleanup()
    if not pending:
        await _w(bot, user.id, "✅ No pending request files to clean up.")
        return

    await _w(bot, user.id, f"🧹 Clearing {len(pending)} request file(s) from AzuraCast…")

    loop    = asyncio.get_running_loop()
    deleted = 0
    failed  = 0
    for job in pending:
        fid = (job.get("azura_file_id") or "").strip()
        if not fid:
            continue
        ok = await loop.run_in_executor(None, _azura_delete_file, fid)
        if ok:
            _db_mark_cleaned(job["id"])
            deleted += 1
        else:
            failed += 1

    parts = [f"✅ Deleted {deleted} request file(s)."]
    if failed:
        parts.append(f"⚠️ {failed} failed — check logs.")
    await _w(bot, user.id, " ".join(parts)[:249])


async def handle_requesthistory(bot: "BaseBot", user: "User", _args: list[str]) -> None:
    """!requesthistory — show last 10 song requests with their current status."""
    rows = _db_request_history(10)
    if not rows:
        await _w(bot, user.id, "🎵 No request history yet.")
        return

    lines = ["🎵 Last 10 requests:"]
    for r in rows:
        # Derive display status: cleaned > done > other
        st = r.get("status") or "?"
        if r.get("cleaned_at"):
            st = "cleaned"
        display = (r["title"] or r["url"] or "?")[:36]
        uname   = (r["username"] or "?")[:12]
        lines.append(f"• {display} — {uname} [{st}]")

    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_requestcleanup(bot: "BaseBot", user: "User", _args: list[str]) -> None:
    """
    !requestcleanup — delete AzuraCast request files that are older than
    REQUEST_AUTO_DELETE_HOURS hours (default 24).  Admin only.
    Never deletes General playlist songs; only files tracked in yt_request_jobs.
    """
    from modules.permissions import is_admin

    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return

    cfg = _azura_api_cfg()
    if not cfg:
        await _w(bot, user.id, "⚠️ AzuraCast API not configured.")
        return

    try:
        hours = max(1, int(os.environ.get("REQUEST_AUTO_DELETE_HOURS") or "24"))
    except (ValueError, TypeError):
        hours = 24

    pending = _db_get_old_pending_cleanup(hours)
    if not pending:
        await _w(
            bot, user.id,
            f"✅ No uncleaned request files older than {hours}h found."
        )
        return

    await _w(bot, user.id, f"🧹 Cleaning {len(pending)} file(s) older than {hours}h…")

    loop    = asyncio.get_running_loop()
    deleted = 0
    failed  = 0
    for job in pending:
        fid = (job.get("azura_file_id") or "").strip()
        if not fid:
            continue
        ok = await loop.run_in_executor(None, _azura_delete_file, fid)
        if ok:
            _db_mark_cleaned(job["id"])
            deleted += 1
        else:
            failed += 1

    parts = [f"✅ Removed {deleted} request file(s) older than {hours}h."]
    if failed:
        parts.append(f"⚠️ {failed} failed — check logs.")
    await _w(bot, user.id, " ".join(parts)[:249])


async def handle_playedrequests(bot: "BaseBot", user: "User", _args: list[str]) -> None:
    """!playedrequests — show the last 10 songs that played from DJ_DUDU requests."""
    rows = _db_recent_played(10)
    if not rows:
        await _w(bot, user.id, "🎵 No songs have played from requests yet.")
        return

    lines = ["🎶 Recently played requests:"]
    for r in rows:
        title  = (r["title"] or "?")[:36]
        uname  = (r["username"] or "?")[:12]
        status = " ✓" if r.get("cleaned_at") else ""
        lines.append(f"• {title} — @{uname}{status}")

    await _w(bot, user.id, "\n".join(lines)[:249])


# ─────────────────────────────────────────────────────────────────────────────
# Radio vote-skip threshold helper
# ─────────────────────────────────────────────────────────────────────────────

def _radio_voteskip_threshold() -> int:
    """Return required votes to skip the current radio song (default 3, min 2)."""
    try:
        return max(2, int(db.get_room_setting("radio_voteskip_threshold", "3")))
    except Exception:
        return 3


# ─────────────────────────────────────────────────────────────────────────────
# !queue / !q  —  public radio request queue display
# ─────────────────────────────────────────────────────────────────────────────

async def handle_radio_queue(bot: "BaseBot", user: "User", _args: list[str]) -> None:
    """!queue / !q — show the current AzuraCast request queue (public)."""
    with _jobs_lock:
        all_jobs = sorted(_jobs.values(), key=lambda j: j.get("id", 0))

    pending = [j for j in all_jobs if j["status"] in ("pending", "downloading", "uploading")]
    cp_id   = _currently_playing_db_id

    if not cp_id and not pending:
        await _w(bot, user.id, "🎵 No song requests in the queue right now.")
        return

    lines = ["📋 Request Queue:"]

    if cp_id:
        playing = next((j for j in all_jobs if j.get("id") == cp_id), None)
        if playing:
            t = (playing.get("title") or "?")[:40]
            u = (playing.get("username") or "?")[:12]
            lines.append(f"▶ Playing: {t} — @{u}")

    for i, j in enumerate(pending[:5], 1):
        t = (j.get("title") or "downloading…")[:35]
        u = (j.get("username") or "?")[:12]
        lines.append(f"  {i}. {t} — @{u}")

    if len(pending) > 5:
        lines.append(f"  … +{len(pending) - 5} more pending")

    await _w(bot, user.id, "\n".join(lines)[:249])


# ─────────────────────────────────────────────────────────────────────────────
# !remove <#>  —  cancel a pending request by queue position (admin+)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_radio_remove(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!remove <#> — cancel a pending radio request by queue position (admin+)."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only. Use !queue to see positions.")
        return

    with _jobs_lock:
        pending = sorted(
            [j for j in _jobs.values() if j["status"] in ("pending", "downloading", "uploading")],
            key=lambda j: j.get("id", 0),
        )

    if len(args) < 2 or not args[1].isdigit():
        if not pending:
            await _w(bot, user.id, "📋 No pending requests to remove.")
        else:
            lines = ["📋 Pending (use !remove <#>):"]
            for i, j in enumerate(pending, 1):
                t = (j.get("title") or "in progress")[:30]
                u = (j.get("username") or "?")[:12]
                lines.append(f"  {i}. {t} — @{u}")
            await _w(bot, user.id, "\n".join(lines)[:249])
        return

    idx = int(args[1]) - 1
    if idx < 0 or idx >= len(pending):
        await _w(bot, user.id, f"⚠️ No request #{args[1]}. Use !queue to see the list.")
        return

    job   = pending[idx]
    jid   = job["id"]
    title = (job.get("title") or "?")[:40]
    uid   = job.get("user_id", "")
    coins = job.get("coins_charged", 0)

    # Mark cancelled in memory + DB
    with _jobs_lock:
        if jid in _jobs:
            _jobs[jid]["status"] = "cancelled"
    rq.mark_cancelled(jid, "Removed by admin")

    # Remove from presence tracking
    with _presence_lock:
        _active_presence.pop(uid, None)

    # Refund if paid
    note = ""
    if coins > 0 and uid:
        _refund_coins(uid, coins)
        note = f" ({coins} coins refunded)"

    # Best-effort file cleanup
    fid = (job.get("azura_file_id") or "").strip()
    fn  = (job.get("filename")      or "").strip()
    if fid or fn:
        loop = asyncio.get_running_loop()
        if fid:
            loop.run_in_executor(None, _azura_delete_file, fid)
        elif fn:
            loop.run_in_executor(None, _azura_sftp_delete, fn)

    await _w(bot, user.id, f"✅ Removed: {title}{note}")


# ─────────────────────────────────────────────────────────────────────────────
# !clearqueue  —  cancel all pending radio requests (admin+)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_radio_clearqueue(bot: "BaseBot", user: "User", _args: list[str]) -> None:
    """!clearqueue — cancel all pending radio requests and clean up files (admin+)."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return

    with _jobs_lock:
        pending = [
            j for j in _jobs.values()
            if j["status"] in ("pending", "downloading", "uploading")
        ]
        for j in pending:
            j["status"] = "cancelled"

    if not pending:
        await _w(bot, user.id, "✅ No pending requests to clear.")
        return

    await _w(bot, user.id, f"🧹 Clearing {len(pending)} pending request(s)…")

    loop     = asyncio.get_running_loop()
    refunded = 0
    for j in pending:
        jid   = j["id"]
        uid   = j.get("user_id", "")
        coins = j.get("coins_charged", 0)

        rq.mark_cancelled(jid, "Cleared by admin")

        if coins > 0 and uid:
            _refund_coins(uid, coins)
            refunded += coins

        fid = (j.get("azura_file_id") or "").strip()
        fn  = (j.get("filename")      or "").strip()
        if fid:
            await loop.run_in_executor(None, _azura_delete_file, fid)
        elif fn:
            await loop.run_in_executor(None, _azura_sftp_delete, fn)

    # Clear presence for affected users
    uids = {j.get("user_id", "") for j in pending if j.get("user_id")}
    with _presence_lock:
        for uid in uids:
            _active_presence.pop(uid, None)

    note = f" | {refunded} coins refunded" if refunded else ""
    await _w(bot, user.id, f"✅ Cleared {len(pending)} request(s){note}.")


# ─────────────────────────────────────────────────────────────────────────────
# !voteskip  —  vote to skip the currently playing radio song (public)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_radio_voteskip(bot: "BaseBot", user: "User", _args: list[str]) -> None:
    """!voteskip — vote to skip the currently playing radio song (public)."""
    loop = asyncio.get_running_loop()
    try:
        np_data = await loop.run_in_executor(None, _azura_fetch_nowplaying)
    except Exception:
        np_data = None

    current_sid = ""
    if np_data:
        current_sid = (
            (np_data.get("now_playing") or {}).get("song", {}).get("id", "")
        ) or ""

    if not current_sid:
        await _w(bot, user.id, "🎵 Nothing is playing right now.")
        return

    thresh = _radio_voteskip_threshold()

    with _radio_vote_lock:
        if current_sid not in _radio_skip_votes:
            _radio_skip_votes[current_sid] = set()

        if user.id in _radio_skip_votes[current_sid]:
            cur  = len(_radio_skip_votes[current_sid])
            need = thresh - cur
            await _w(bot, user.id, f"👎 Already voted. {need} more vote(s) needed to skip.")
            return

        _radio_skip_votes[current_sid].add(user.id)
        votes = len(_radio_skip_votes[current_sid])

    if votes >= thresh:
        s     = (np_data.get("now_playing") or {}).get("song", {})
        art   = (s.get("artist") or "").strip()
        ttl   = (s.get("title")  or "").strip()
        label = (f"{art} — {ttl}" if art else ttl)[:60] or "current song"
        try:
            await bot.highrise.chat(
                f"👎 Vote skip passed ({votes}/{thresh})! Skipping: {label}"[:249]
            )
        except Exception:
            pass
        await loop.run_in_executor(None, _azura_skip_with_retry)
        with _radio_vote_lock:
            _radio_skip_votes.pop(current_sid, None)
    else:
        remaining = thresh - votes
        try:
            await bot.highrise.chat(
                f"👎 @{user.username[:15]} voted to skip. {remaining} more vote(s) needed."[:249]
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# !radiohelp  —  show all radio commands (public)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_radiohelp(bot: "BaseBot", user: "User", _args: list[str]) -> None:
    """!radiohelp — show all AzuraCast radio commands (public)."""
    cost     = _request_cost()
    cost_str = f"{cost} coins" if cost else "free"
    thresh   = _radio_voteskip_threshold()
    await _w(
        bot, user.id,
        f"📻 Radio Commands:\n"
        f"🎵 !play <song/URL> ({cost_str}) | !queue (!q) | !np\n"
        f"👎 !voteskip ({thresh} votes) | !history\n"
        f"⭐ !fav | !favs | !unfav <#> | !ratings\n"
        f"🔒 Staff: !skip | !remove <#> | !clearqueue",
    )
    await _w(
        bot, user.id,
        "📂 VIP Playlists: !playlist create <name>\n"
        "  !playlist songs <name> | add <name> <URL>\n"
        "  !playlist addcurrent|remove|delete|play <name>\n"
        "🎛️ !vibe chill|party|status | !radiotutorial",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC RADIO INTERFACE
# Used by radio_commands.py / request_queue.py / media_cleanup.py.
# All names begin with  radio_  to make the contract explicit.
# ═══════════════════════════════════════════════════════════════════════════════

def radio_sftp_ready() -> bool:
    return _sftp_ready()


def radio_sftp_missing() -> list:
    return _sftp_missing_vars()


def radio_check_banned_requester(username: str) -> bool:
    return _is_banned_requester(username)


def radio_check_dedup(url: str, window_secs: int) -> "dict | None":
    return _db_check_dedup(url, window_secs)


def radio_user_pending_count(user_id: str) -> int:
    with _jobs_lock:
        return sum(
            1 for j in _jobs.values()
            if j["user_id"] == user_id
            and j["status"] in ("pending", "downloading", "uploading")
        )


def radio_active_count() -> int:
    with _jobs_lock:
        return sum(
            1 for j in _jobs.values()
            if j["status"] in ("pending", "downloading", "uploading")
        )


def radio_get_jobs_snapshot() -> list:
    """Snapshot copy of all in-memory jobs (safe to iterate without holding the lock)."""
    with _jobs_lock:
        return [dict(j) for j in _jobs.values()]


def radio_get_currently_playing_id() -> int:
    return _currently_playing_db_id


async def radio_request_prepare_worker(
    bot: "BaseBot",
    stop_event: "threading.Event",
) -> None:
    """
    Background coroutine started by playback_engine._startup_init_task.

    Scans every 30 s for jobs stuck in pre-ready statuses that are NOT
    currently being processed (bot restarted mid-pipeline).  Resets them
    to 'pending' and relaunches _run_job so the pipeline resumes without
    manual intervention.
    """
    print("[PREPARE] Background prepare worker started")
    await asyncio.sleep(15)   # let the bot settle after startup

    while not stop_event.is_set():
        try:
            db.set_room_setting("radio_worker_heartbeat_prepare", str(time.time()))
            restore_candidates = ("pending", "downloading", "downloaded", "uploading")
            stuck = tuple(s for s in restore_candidates if s in ACTIVE_QUEUE_STATUSES)
            restore_ph = ",".join("?" * len(stuck))
            with sqlite3.connect(_DB_PATH) as _conn:
                rows = _conn.execute(
                    "SELECT id, user_id, username, url, title, status, "
                    "       coins_charged, payment_type, filename, source_type "
                    "FROM yt_request_jobs "
                    f"WHERE status IN ({restore_ph}) AND played_at IS NULL "
                    "ORDER BY id ASC LIMIT 20",
                    stuck,
                ).fetchall()

            for row in rows:
                jid, uid, uname, url_val, title, status, coins, ptype, filename, source_type = row
                with _prep_ids_lock:
                    if jid in _prep_active_jids:
                        continue          # already being processed
                    _prep_active_jids.add(jid)

                job = {
                    "id": jid, "user_id": uid, "username": uname,
                    "url": url_val, "title": title or "", "status": status,
                    "coins_charged": coins or 0, "payment_type": ptype or "free",
                    "filename": filename or "", "source_type": source_type or "",
                }

                if status in ("downloading", "downloaded", "uploading"):
                    fn = (filename or "").strip()
                    source = (source_type or "").strip()
                    staged_path = os.path.join(STAGING_DIR, fn) if fn else ""
                    if source != "youtube" and (not staged_path or not os.path.exists(staged_path)):
                        _fail_staged_file_missing(jid, fn)
                        with _prep_ids_lock:
                            _prep_active_jids.discard(jid)
                        continue
                    # Reset stuck in-flight job so _run_job starts fresh
                    print(f"[PREPARE] Resetting stuck job #{jid} ({status}→pending)")
                    _update_job(jid, status="pending")
                    job["status"] = "pending"

                print(f"[PREPARE] Recovering job #{jid}: {(title or url_val or '')[:60]!r}")
                asyncio.create_task(_run_job(bot, job))

        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"[PREPARE] Worker tick error: {exc}")

        await asyncio.sleep(30)

    print("[PREPARE] Background prepare worker stopped")


def radio_submit_job(
    bot: "BaseBot",
    user_id: str,
    username: str,
    url: str,
    coins_charged: int = 0,
    payment_type: str = "paid",
    priority: int = 0,
) -> int:
    """Create a new job record and launch the yt-dlp → SFTP pipeline."""
    job = _new_job(user_id, username, url, coins_charged, payment_type, priority)
    with _prep_ids_lock:
        _prep_active_jids.add(job["id"])
    asyncio.create_task(_run_job(bot, job))
    return int(job.get("db_id") or 0)


def radio_cancel_job(jid: int, reason: str = "cancelled_by_admin") -> "dict | None":
    """
    Cancel an active job by in-memory job ID.
    Returns a copy of the cancelled job dict, or None if not found / already finished.
    Refunds are NOT issued here — caller is responsible.
    """
    found     = None
    db_id_upd = 0
    uid_clear = ""

    with _jobs_lock:
        job = _jobs.get(jid)
        if job and job["status"] in ("pending", "downloading", "uploading"):
            _jobs[jid]["status"] = "cancelled"
            found     = dict(_jobs[jid])
            db_id_upd = found.get("db_id", 0)
            uid_clear = found.get("user_id", "")

    if not found:
        return None

    if db_id_upd:
        rq.mark_cancelled(db_id_upd, reason)

    if uid_clear:
        with _presence_lock:
            pres = _active_presence.get(uid_clear)
            if pres and pres.get("job_id") == jid:
                _active_presence.pop(uid_clear, None)

    return found


def radio_request_history(limit: int = 10) -> list:
    return _db_request_history(limit)


def radio_nowplaying() -> "dict | None":
    return _azura_fetch_nowplaying()


def radio_skip_song(max_attempts: int = 3, delay: float = 2.0) -> bool:
    return _azura_skip_with_retry(max_attempts, delay)


def radio_search_yt(query: str, max_results: int = 5) -> list:
    return _yt_search_sync(query, max_results)


def radio_has_pending_search(user_id: str) -> bool:
    with _yt_pending_lock:
        return _yt_pending_is_fresh(user_id)


def radio_get_pending_search(user_id: str) -> "list | None":
    with _yt_pending_lock:
        if not _yt_pending_is_fresh(user_id):
            return None
        return _yt_pending.get(user_id)


def radio_set_pending_search(user_id: str, results: list) -> None:
    with _yt_pending_lock:
        _yt_pending[user_id]    = results
        _yt_pending_ts[user_id] = time.time()


def radio_clear_pending_search(user_id: str) -> None:
    with _yt_pending_lock:
        _yt_pending.pop(user_id, None)
        _yt_pending_ts.pop(user_id, None)
