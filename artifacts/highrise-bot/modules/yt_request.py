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
from modules.permissions import is_admin, is_owner, is_manager

# DB file path — config.DB_PATH reads SHARED_DB_PATH env var (default highrise_hangout.db)
_DB_PATH: str = _config.DB_PATH

if TYPE_CHECKING:
    from highrise import BaseBot, User

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
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

# ─────────────────────────────────────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_COOLDOWN  = 300   # seconds
_MAX_ACTIVE_JOBS   = 5     # refuse new jobs when full
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
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            cur = conn.execute(
                """INSERT INTO yt_request_jobs
                       (user_id, username, url, title, status, started_at,
                        coins_charged, payment_type, priority)
                   VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?, ?)""",
                (job["user_id"], job["username"], job["url"],
                 job["title"], job["status"],
                 job.get("coins_charged", 0),
                 job.get("payment_type", "free"),
                 job.get("priority", 0)),
            )
            conn.commit()
            return cur.lastrowid or 0
    except Exception as exc:
        print(f"[YT_REQUEST] DB insert error (non-fatal): {exc}")
        return 0


def _db_update_job(db_id: int, **kwargs: object) -> None:
    """Update a yt_request_jobs row by DB id (non-fatal on error)."""
    if not db_id:
        return
    allowed = {"title", "status", "error", "finished_at", "filename",
               "azura_file_id", "azura_song_id",
               "video_id", "yt_uploader",
               "coins_charged", "payment_type", "priority"}
    fields  = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    # Convert epoch float to ISO string for finished_at
    if "finished_at" in fields and isinstance(fields["finished_at"], float):
        import datetime as _dt
        fields["finished_at"] = _dt.datetime.utcfromtimestamp(
            fields["finished_at"]
        ).strftime("%Y-%m-%dT%H:%M:%S")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values     = list(fields.values()) + [db_id]
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                f"UPDATE yt_request_jobs SET {set_clause} WHERE id = ?",
                values,
            )
            conn.commit()
    except Exception as exc:
        print(f"[YT_REQUEST] DB update error (non-fatal): {exc}")


def _db_check_dedup(url: str, window_secs: int) -> "dict | None":
    """Return the most recent done/played job for this URL within window_secs, or None."""
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                """SELECT title, username, finished_at
                     FROM yt_request_jobs
                    WHERE url     = ?
                      AND status  IN ('done', 'played')
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
    if not db_id:
        return
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                "UPDATE yt_request_jobs SET azura_file_id=?, azura_song_id=? WHERE id=?",
                (str(file_id), song_id, db_id),
            )
            conn.commit()
    except Exception as exc:
        print(f"[YT_REQUEST] DB azura_ids update error (non-fatal): {exc}")


def _db_get_pending_cleanup() -> list[dict]:
    """Return done/played jobs that have an azura_file_id but have not been cleaned yet."""
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            rows = conn.execute(
                """SELECT id, title, azura_file_id, azura_song_id, filename, video_id
                     FROM yt_request_jobs
                    WHERE status        IN ('done', 'played')
                      AND azura_file_id != ''
                      AND cleaned_at   IS NULL
                    ORDER BY id DESC""",
            ).fetchall()
        return [
            {
                "id":            r[0], "title":         r[1],
                "azura_file_id": r[2], "azura_song_id": r[3],
                "filename":      r[4], "video_id":      r[5],
            }
            for r in rows
        ]
    except Exception as exc:
        print(f"[YT_REQUEST] DB pending cleanup query error (non-fatal): {exc}")
    return []


def _db_mark_cleaned(db_id: int) -> None:
    """Set cleaned_at = now on a job record to mark it as removed from AzuraCast."""
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                "UPDATE yt_request_jobs SET cleaned_at = datetime('now') WHERE id = ?",
                (db_id,),
            )
            conn.commit()
    except Exception as exc:
        print(f"[YT_REQUEST] DB mark_cleaned error (non-fatal): {exc}")


def _db_mark_played(db_id: int) -> None:
    """Set status='played' and played_at=now (idempotent — only fires once per job)."""
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                """UPDATE yt_request_jobs
                      SET status='played', played_at=datetime('now')
                    WHERE id=? AND played_at IS NULL""",
                (db_id,),
            )
            conn.commit()
    except Exception as exc:
        print(f"[YT_REQUEST] DB mark_played error (non-fatal): {exc}")


def _db_recent_played(limit: int = 10) -> list[dict]:
    """Return recently played request jobs (status='played'), newest first by played_at."""
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            rows = conn.execute(
                """SELECT id, username, title, played_at, cleaned_at
                     FROM yt_request_jobs
                    WHERE status IN ('played', 'done')
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
                    WHERE status        = 'done'
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
                      AND status IN ('done', 'played', 'playing')
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
                      AND status IN ('done', 'played', 'playing')
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

_yt_pending:      dict[str, list[dict]] = {}  # user_id → search results
_yt_pending_lock: threading.Lock        = threading.Lock()


def has_pending_yt_search(user_id: str) -> bool:
    """True if the user has results from a !request search waiting for !pick."""
    with _yt_pending_lock:
        return user_id in _yt_pending


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
        "quiet":       True,
        "no_warnings": True,
        "noplaylist":  True,
    }
    with yt_dlp.YoutubeDL(_pre_opts) as ydl:
        pre = ydl.extract_info(url, download=False)

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
        "format":      "bestaudio/best",
        "outtmpl":     os.path.join(tmpdir, "%(id)s.%(ext)s"),
        "noplaylist":  True,
        "quiet":       True,
        "no_warnings": True,
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
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

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

def _azura_post_upload(filename: str, db_id: int = 0) -> None:
    """
    Blocking post-SFTP API step.  Called from the upload daemon thread AFTER
    sftp.put() has returned and the success whisper has already been sent.

    Steps
    ─────
    1. Wait _API_WAIT_SECS (10 s) for AzuraCast to notice the new file.
    2. POST /api/station/{id}/files/batch  {"do":"rescan"}  — tell AzuraCast
       to index the Requests folder.
    3. GET  /api/station/{id}/files?searchPhrase={filename}  — look for the
       media record.  Retry up to _API_RETRY_COUNT (5) times (_API_RETRY_DELAY s
       apart) if the file isn't indexed yet.  Does NOT fail on first miss.
    4. POST /api/station/{id}/files/batch  {"do":"playlist","playlist":id}
       — add the confirmed file to the Requests playlist (if AZURA_PLAYLIST_ID
       is set).
    5. POST /api/station/{id}/request/{unique_id}
       — submit the song to the AzuraCast request queue.

    All HTTP responses are logged in full.  Every error is non-fatal; this
    function never raises.  Skipped silently when AZURA_BASE_URL / AZURA_API_KEY
    are not set.
    """
    import requests as req_lib

    cfg = _azura_api_cfg()
    if cfg is None:
        print("[YT_API] AZURA_BASE_URL / AZURA_API_KEY not configured — skipping post-upload step")
        return

    base      = cfg["base_url"]
    sid       = cfg["station_id"]
    folder    = cfg["folder"]
    headers   = {
        "X-API-Key":    cfg["api_key"],
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }
    batch_url  = f"{base}/api/station/{sid}/files/batch"
    search_url = f"{base}/api/station/{sid}/files"

    try:
        # ── 1. Wait ──────────────────────────────────────────────────────────
        print(f"[YT_API] Waiting {_API_WAIT_SECS}s for AzuraCast to notice new file…")
        time.sleep(_API_WAIT_SECS)

        # ── 2. Rescan ────────────────────────────────────────────────────────
        try:
            resp = req_lib.post(
                batch_url,
                json={"do": "rescan", "currentDirectory": folder},
                headers=headers,
                timeout=30,
            )
            print(f"[YT_API] Rescan → HTTP {resp.status_code}: {resp.text[:300]}")
        except Exception as exc:
            print(f"[YT_API] Rescan request error (non-fatal): {exc}")

        # ── 3. Search + retry (up to _API_RETRY_COUNT attempts) ─────────────
        file_id:   "int | None" = None
        unique_id: "str | None" = None
        for attempt in range(1, _API_RETRY_COUNT + 1):
            try:
                resp = req_lib.get(
                    search_url,
                    params={"searchPhrase": filename},
                    headers=headers,
                    timeout=15,
                )
                print(
                    f"[YT_API] Search {attempt}/{_API_RETRY_COUNT}"
                    f" → HTTP {resp.status_code}: {resp.text[:400]}"
                )
                if resp.status_code == 200:
                    data = resp.json()
                    rows = data if isinstance(data, list) else data.get("rows", [])
                    for row in rows:
                        if os.path.basename(row.get("path", "")) == filename:
                            file_id   = row.get("id")
                            unique_id = (
                                row.get("unique_id")
                                or row.get("song_unique_id")
                                or (row.get("song") or {}).get("id")
                                or ""
                            )
                            print(
                                f"[YT_API] File found — id={file_id}"
                                f"  unique_id={unique_id}  path={row.get('path')}"
                            )
                            # Persist IDs immediately for cleanup tracking
                            _db_update_azura_ids(db_id, str(file_id), unique_id or "")
                            break
            except Exception as exc:
                print(f"[YT_API] Search attempt {attempt} error: {exc}")

            if file_id is not None:
                break
            if attempt < _API_RETRY_COUNT:
                print(f"[YT_API] Not indexed yet — waiting {_API_RETRY_DELAY}s before retry…")
                time.sleep(_API_RETRY_DELAY)

        if file_id is None:
            print(
                f"[YT_API] '{filename}' not found after {_API_RETRY_COUNT} attempts"
                f" — skipping playlist/request steps"
            )
            return

        # ── 4. Add to Requests playlist ──────────────────────────────────────
        playlist_id = (os.environ.get("AZURA_PLAYLIST_ID") or "").strip()
        if playlist_id:
            try:
                resp = req_lib.post(
                    batch_url,
                    json={"do": "playlist", "playlist": playlist_id, "files": [file_id]},
                    headers=headers,
                    timeout=15,
                )
                print(f"[YT_API] Playlist add → HTTP {resp.status_code}: {resp.text[:300]}")
            except Exception as exc:
                print(f"[YT_API] Playlist add error (non-fatal): {exc}")
        else:
            print("[YT_API] AZURA_PLAYLIST_ID not set — skipping playlist add step")

        # ── 5. Submit to request queue ───────────────────────────────────────
        if unique_id:
            request_url = f"{base}/api/station/{sid}/request/{unique_id}"
            try:
                resp = req_lib.post(request_url, headers=headers, timeout=15)
                print(f"[YT_API] Request queue → HTTP {resp.status_code}: {resp.text[:300]}")
            except Exception as exc:
                print(f"[YT_API] Request queue error (non-fatal): {exc}")
        else:
            print("[YT_API] unique_id unavailable — skipping request queue step")

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
    tmpdir = tempfile.mkdtemp(prefix="ytr_")
    loop   = asyncio.get_running_loop()

    try:
        # ── Step 1: Download + convert ──────────────────────────────────────
        _update_job(jid, status="downloading")

        info, mp3_path = await loop.run_in_executor(
            None, _download_step, job["url"], tmpdir
        )
        title        = (info.get("title") or "Unknown")[:160]
        yt_filename  = os.path.basename(mp3_path)
        video_id     = (info.get("id") or "")[:32]
        yt_uploader  = (
            info.get("uploader") or info.get("channel") or ""
        )[:100]
        _update_job(
            jid,
            title=title,
            filename=yt_filename,
            video_id=video_id,
            yt_uploader=yt_uploader,
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

        # ── Step 2: SFTP upload (fire-and-forget after put completes) ────────
        _update_job(jid, status="uploading")
        print(f"[YT_REQUEST] Job #{jid} — SFTP upload starting: {title[:80]}")

        # asyncio.Event is set by the upload thread the moment sftp.put()
        # returns.  _run_job unblocks HERE and whispers success immediately;
        # the thread keeps running to close the SSH connection in the background.
        upload_done: asyncio.Event               = asyncio.Event()
        upload_exc:  list[BaseException | None]  = [None]

        def _on_put_done() -> None:
            loop.call_soon_threadsafe(upload_done.set)

        def _upload_thread() -> None:
            try:
                _sftp_step(mp3_path, _on_put_done)
                # on_put_done() already fired → success whispered to user.
                # Now run API post-processing in this same background thread.
                _azura_post_upload(os.path.basename(mp3_path), jid)
            except Exception as exc:
                # Only reached if _sftp_step raised BEFORE on_put_done() was called.
                upload_exc[0] = exc
                loop.call_soon_threadsafe(upload_done.set)

        t = threading.Thread(
            target=_upload_thread,
            daemon=True,
            name=f"ytr_upload_{jid}",
        )
        upload_start = time.time()
        t.start()

        await upload_done.wait()          # unblocks as soon as put() finishes
        upload_secs = time.time() - upload_start

        if upload_exc[0] is not None:
            raise upload_exc[0]

        # ── Done: whisper success immediately after put() ────────────────────
        _update_job(jid, status="done", finished_at=time.time())
        print(f"[YT_REQUEST] Job #{jid} — success in {upload_secs:.1f}s: {title[:80]}")
        await _w(bot, uid, f"✅ Added to queue: {title[:80]}")
        # Room-wide announcement
        try:
            uname = job.get("username") or uid
            announce = f"🎵 Added to radio: {title[:80]} — requested by @{uname}"
            await bot.highrise.chat(announce[:249])
        except Exception as _ann_exc:
            print(f"[YT_REQUEST] Room announce error (non-fatal): {_ann_exc}")
        # Background thread is still closing the SSH connection — that's fine.

    except Exception as exc:
        err = str(exc)[:120]
        _update_job(jid, status="error", error=err, finished_at=time.time())
        print(f"[YT_REQUEST] Job #{jid} — FAILED: {exc}")
        coins_c = job.get("coins_charged", 0)
        if coins_c > 0:
            _refund_coins(uid, coins_c)
            await _w(bot, uid, "❌ Could not process your request. Your coins have been refunded.")
        else:
            await _w(bot, uid, "❌ Could not process your request.")

    finally:
        # Clear presence tracking (job done or failed)
        with _presence_lock:
            _pres = _active_presence.get(uid)
            if _pres and _pres.get("job_id") == jid:
                _active_presence.pop(uid, None)
        # Temp dir removed here; upload thread may still be running ssh.close()
        # but it holds no reference to tmpdir, so this is safe.
        shutil.rmtree(tmpdir, ignore_errors=True)

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


# ─────────────────────────────────────────────────────────────────────────────
# Public radio command handlers  (!play  !now  !skip)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_play(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!play <song name or YouTube URL> — request a song for ChillTopia Radio.

    • URL  → directly queues the song for SFTP upload.
    • Text → searches YouTube and shows top 5; user picks with !pick <1-5>.
    Aliases: !request !sr !song !req !ytrequest
    """
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
    clean_url = query.split("&list=")[0]
    if _is_youtube_url(clean_url):
        await handle_ytrequest(bot, user, [args[0], clean_url])
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
        _yt_pending[user.id] = results

    max_min = _MAX_DURATION_SECS // 60
    lines = ["🎵 Top results — reply !pick <1-5>:"]
    for i, r in enumerate(results, 1):
        flag = " ⚠️" if r["duration_secs"] > _MAX_DURATION_SECS else ""
        lines.append(f"{i}. {r['title'][:44]} [{r['duration']}]{flag}")
    lines.append(f"(Max {max_min}m per track)")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_now(bot: "BaseBot", user: "User", _args: list[str]) -> None:
    """!now / !np / !nowplaying — show what's currently playing on ChillTopia Radio."""
    loop = asyncio.get_running_loop()
    np_data = await loop.run_in_executor(None, _azura_fetch_nowplaying)

    if not np_data:
        if _azura_api_cfg() is None:
            await _w(bot, user.id, "📻 AzuraCast not configured.")
        else:
            await _w(bot, user.id, "📻 Radio is offline or unreachable right now.")
        return

    np_obj   = np_data.get("now_playing") or {}
    song     = np_obj.get("song") or {}
    song_id  = (song.get("id") or "").strip()
    np_title = (song.get("title") or "").strip()

    # Cross-reference DB: get real YouTube title and requester.
    # Handles cases where AzuraCast shows the filename instead of real title
    # (e.g. "KFMYX1TibeQ") because ID3 tags were missing on the uploaded file.
    db_job = _db_lookup_for_now(song_id, np_title)
    if db_job:
        real_title = (db_job.get("title") or np_title or "Unknown Track").strip()
        requester  = (db_job.get("username") or "").strip()
    else:
        real_title = np_title or "Unknown Track"
        requester  = ""

    raw_artist = (song.get("artist") or "").strip()
    if raw_artist and raw_artist.lower() not in real_title.lower():
        display = f"{raw_artist} — {real_title}"
    else:
        display = real_title
    display = display[:48]

    elapsed  = int(np_obj.get("elapsed")  or 0)
    duration = int(np_obj.get("duration") or 0)

    req_line  = f"👤 Requested by: @{requester[:20]}" if requester else "👤 AutoDJ"

    elapsed_str  = _fmt_secs(elapsed)
    duration_str = _fmt_secs(duration) if duration > 0 else "--:--"
    bar          = _progress_bar(elapsed, duration)

    msg = (
        "🎧 ChillTopia Radio\n"
        f"▶ Now Playing: {display}\n"
        f"{req_line}\n"
        f"⏱ {elapsed_str} / {duration_str}\n"
        f"{bar}\n"
        "📻 DJ_DUDU is live"
    )
    await _w(bot, user.id, msg[:249])


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
    ok = await loop.run_in_executor(None, _azura_skip_song)
    if ok:
        await _w(bot, user.id, "⏭ Song skipped.")
        try:
            await bot.highrise.chat("⏭ Song skipped.")
        except Exception:
            pass
    else:
        await _w(bot, user.id, "❌ Skip failed — please try again.")


async def handle_ytrequest(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!ytrequest <youtube_url> — download and add to AzuraCast Requests playlist."""

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

    url = args[1].strip().split("&list=")[0]   # strip playlist suffix

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
    with _jobs_lock:
        active_count = sum(
            1 for j in _jobs.values()
            if j["status"] in ("pending", "downloading", "uploading")
        )
    if active_count >= _MAX_ACTIVE_JOBS:
        await _w(bot, user.id, "📋 Queue is full right now. Try again in a few minutes.")
        return

    # ── Payment logic ─────────────────────────────────────────────────────────
    vip             = not _owner and not _admin and _is_vip(user.id)
    coins_to_charge = 0
    payment_type    = "free"
    priority        = 0

    if _owner or _admin:
        payment_type = "admin"
        priority     = 1
    elif vip and _vip_free_requests():
        payment_type = "vip_free"
        priority     = 1 if _vip_priority() else 0
    else:
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
    pos = _queue_position()
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
    pos_str = f"⏳ Position: #{pos + 1} in queue" if pos > 0 else "⏳ You're next!"
    pay_str = ""
    if coins_to_charge > 0:
        pay_str = f"\n💰 {coins_to_charge} coins charged."
    elif payment_type == "vip_free":
        pay_str = "\n⭐ VIP request — enjoy!"
    await _w(bot, user.id, f"🎵 Request received!\n{pos_str}{pay_str}"[:249])

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

    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_ytstatus(bot: "BaseBot", user: "User", _args: list[str]) -> None:
    """!ytstatus — show YT request system config and session stats (admin+)."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return

    cfg   = _sftp_cfg()
    ready = _sftp_ready()
    cd    = _cooldown_secs()

    with _jobs_lock:
        total  = len(_jobs)
        active = sum(1 for j in _jobs.values() if j["status"] in ("pending", "downloading", "uploading"))
        done   = sum(1 for j in _jobs.values() if j["status"] == "done")
        errors = sum(1 for j in _jobs.values() if j["status"] == "error")

    sftp_ok   = "✅" if ready else "❌ missing vars"
    host_disp = cfg["host"][:30] if cfg["host"] else "(not set)"
    api_cfg   = _azura_api_cfg()
    api_disp  = "✅ " + (api_cfg["base_url"][:25] if api_cfg else "") if api_cfg else "⬜ disabled"

    await _w(
        bot, user.id,
        (f"📻 YT Request System:\n"
         f"SFTP: {sftp_ok} | {host_disp}:{cfg['port']}\n"
         f"API: {api_disp}\n"
         f"Folder: {cfg['folder']} | CD: {cd}s\n"
         f"Active: {active} | Done: {done} | Err: {errors} | Total: {total}")[:249],
    )


async def handle_ytnow(bot: "BaseBot", user: "User", _args: list[str]) -> None:
    """!ytnow — show the latest successfully requested song (public)."""
    rows = _db_recent_jobs(limit=20)
    done = [r for r in rows if r["status"] == "done"]
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
    clean_url = query.split("&list=")[0]
    if _is_youtube_url(clean_url):
        await handle_ytrequest(bot, user, [args[0], clean_url])
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
        _yt_pending[user.id] = results

    max_min = _MAX_DURATION_SECS // 60
    lines   = [f"🎵 Results — reply !pick <1-5>:"]
    for i, r in enumerate(results, 1):
        flag = " ⚠️long" if r["duration_secs"] > _MAX_DURATION_SECS else ""
        lines.append(f"{i}. {r['title'][:44]} [{r['duration']}]{flag}")
    lines.append(f"(Max {max_min}m per track)")

    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_ytpick(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!pick <1-5>  — upload the selected search result to AzuraCast via SFTP.
    Only runs when the user has a pending !request search. Otherwise the main.py
    routing falls through to handle_dj_pick (in-room DJ queue).
    """
    with _yt_pending_lock:
        results = _yt_pending.get(user.id)

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
        print(
            f"[YT_CLEANUP] DELETE file/{file_id}"
            f" → HTTP {resp.status_code}: {resp.text[:200]}"
        )
        return resp.status_code in (200, 204)
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


async def _run_nowplaying_cycle(prev_song_id_ref: list[str]) -> None:
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

    # Phase 1: request song just started playing — mark played + track
    # Use current_song_id as the tracking key (AzuraCast's stable per-song ID)
    track_key = current_song_id or ""
    if matched_job and track_key and track_key not in _seen_playing:
        _seen_playing[track_key] = matched_job["id"]
        _db_mark_played(matched_job["id"])
        _currently_playing_db_id = matched_job["id"]
        print(
            f"[YT_NOWPLAY] ▶ Request started playing: "
            f"{(matched_job.get('title') or '?')[:60]}"
        )

    # Phase 2: previous request song finished → delete the file now
    prev_id = prev_song_id_ref[0]
    if prev_id and prev_id != current_song_id and prev_id in _seen_playing:
        job_id    = _seen_playing.pop(prev_id)
        if _currently_playing_db_id == job_id:
            _currently_playing_db_id = 0
        job_match = next((j for j in pending if j["id"] == job_id), None)
        if job_match and _auto_delete_enabled():
            fid       = (job_match.get("azura_file_id") or "").strip()
            fn        = (job_match.get("filename")      or "").strip()
            title_log = (job_match.get("title") or "?")[:60]

            ok = False
            # Primary: AzuraCast API delete (uses stored numeric file_id)
            if fid:
                ok = await loop.run_in_executor(None, _azura_delete_file, fid)
                if ok:
                    print(f"[YT_CLEANUP] ✓ API deleted after play: {title_log}")
                else:
                    print(f"[YT_CLEANUP] API delete failed for file_id={fid}, trying SFTP…")

            # Fallback: direct SFTP delete by filename
            if not ok and fn:
                ok = await loop.run_in_executor(None, _azura_sftp_delete, fn)
                if ok:
                    print(f"[YT_CLEANUP] ✓ SFTP deleted after play: {title_log}")

            if ok:
                _db_mark_cleaned(job_match["id"])
            else:
                print(
                    f"[YT_CLEANUP] ✗ Cleanup failed: {title_log}"
                    f" (file_id={fid!r}, filename={fn!r})"
                )

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


async def _cleanup_loop() -> None:
    """
    Infinite loop: poll AzuraCast Now Playing every 10 s.
    Runs a history-based fallback sweep every ~90 s (every 9 now-playing cycles).

    • Now-playing path (10 s): marks requests as played the instant they start,
      then deletes the file the moment the song transitions — prevents ALL replays.
      Tries API delete first, falls back to SFTP delete if that fails.
    • History fallback (90 s): safety net for songs that played while the bot
      was offline or before azura_song_id was recorded.
    """
    print(
        f"[YT_CLEANUP] Poll loop started"
        f" (now-playing every {_NOWPLAYING_POLL_SECS}s,"
        f" history fallback every {_HISTORY_POLL_SECS}s)."
    )
    prev_song_id_ref: list[str] = [""]
    history_cycles = _HISTORY_POLL_SECS // _NOWPLAYING_POLL_SECS
    cycle = 0

    while True:
        try:
            await asyncio.sleep(_NOWPLAYING_POLL_SECS)
            await _run_nowplaying_cycle(prev_song_id_ref)
            cycle += 1
            if cycle >= history_cycles:
                cycle = 0
                if _auto_delete_enabled():
                    await _run_history_fallback()
        except asyncio.CancelledError:
            print("[YT_CLEANUP] Poll loop cancelled.")
            break
        except Exception as exc:
            print(f"[YT_CLEANUP] Loop error (non-fatal): {exc}")


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
    Start the AzuraCast played-song auto-cleanup background loop.
    Called from on_start() — DJ bot only, guarded by should_this_bot_run_module.
    Skipped silently if REQUEST_AUTO_DELETE_AFTER_PLAY=false or AzuraCast
    API is not configured.
    """
    if not _auto_delete_enabled():
        print("[YT_CLEANUP] REQUEST_AUTO_DELETE_AFTER_PLAY=false — cleanup disabled.")
        return
    if not _azura_api_cfg():
        print("[YT_CLEANUP] AzuraCast API not configured — cleanup disabled.")
        return
    asyncio.create_task(_cleanup_loop())


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
