"""DB schema and read helpers for the rebuilt radio skeleton."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import database as database
from modules.radio import models
from modules.radio import settings as radio_settings


SAFE_REQUEST_TITLE_PREFIXES = ("radio_yt_", "radio_local_", "radio_req_")
NORMALIZED_REQUEST_TITLE_PREFIXES = ("radio yt ", "radio local ", "radio req ")


def ensure_schema() -> None:
    radio_settings.ensure_radio_settings()
    with database.db_conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS radio_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                username TEXT,
                source_type TEXT,
                source_ref TEXT,
                title TEXT,
                artist TEXT,
                status TEXT,
                priority INTEGER DEFAULT 0,
                disc_cost_charged INTEGER DEFAULT 0,
                payment_reason TEXT,
                is_staff_free INTEGER DEFAULT 0,
                is_vip INTEGER DEFAULT 0,
                temp_filename TEXT,
                azura_file_id TEXT,
                azura_song_id TEXT,
                azura_path TEXT,
                created_at TEXT,
                prepared_at TEXT,
                submitted_at TEXT,
                playing_at TEXT,
                played_at TEXT,
                cleaned_at TEXT,
                cancelled_at TEXT,
                failed_at TEXT,
                error TEXT,
                finish_reason TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS radio_runtime_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS radio_track_stats (
                track_key TEXT PRIMARY KEY,
                title TEXT,
                artist TEXT,
                likes INTEGER DEFAULT 0,
                dislikes INTEGER DEFAULT 0,
                request_play_count INTEGER DEFAULT 0,
                last_played_at TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS radio_search_sessions (
                user_id TEXT PRIMARY KEY,
                username TEXT,
                query TEXT,
                results_json TEXT,
                created_at TEXT,
                expires_at TEXT
            )"""
        )


def queue_rows(limit: int = 20) -> list[dict]:
    placeholders = ",".join("?" for _ in models.QUEUE_DISPLAY_STATUSES)
    try:
        with database.db_conn() as conn:
            rows = conn.execute(
                f"""SELECT id, username, title, artist, status, priority, created_at
                    FROM radio_requests
                    WHERE status IN ({placeholders})
                    ORDER BY id ASC
                    LIMIT ?""",
                (*models.QUEUE_DISPLAY_STATUSES, max(1, min(50, int(limit)))),
            ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []


def create_request(
    user_id: str,
    username: str,
    source_type: str,
    source_ref: str,
    title: str,
    artist: str,
    disc_cost: int,
    payment_reason: str,
    is_staff_free: bool = False,
    is_vip: bool = False,
) -> int:
    ensure_schema()
    with database.db_conn() as conn:
        cur = conn.execute(
            """INSERT INTO radio_requests
               (user_id, username, source_type, source_ref, title, artist, status,
                priority, disc_cost_charged, payment_reason, is_staff_free, is_vip,
                created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?, datetime('now'))""",
            (
                user_id,
                username,
                source_type,
                source_ref,
                title,
                artist,
                int(disc_cost),
                payment_reason,
                1 if is_staff_free else 0,
                1 if is_vip else 0,
            ),
        )
        return int(cur.lastrowid)


def get_request(request_id: int) -> dict | None:
    ensure_schema()
    with database.db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM radio_requests WHERE id=?",
            (int(request_id),),
        ).fetchone()
    return dict(row) if row else None


def save_search_session(user_id: str, username: str, query: str, results: list[dict], timeout_secs: int) -> None:
    ensure_schema()
    with database.db_conn() as conn:
        conn.execute(
            """INSERT INTO radio_search_sessions
               (user_id, username, query, results_json, created_at, expires_at)
               VALUES (?, ?, ?, ?, datetime('now'), datetime('now', ?))
               ON CONFLICT(user_id) DO UPDATE SET
                   username=excluded.username,
                   query=excluded.query,
                   results_json=excluded.results_json,
                   created_at=datetime('now'),
                   expires_at=excluded.expires_at""",
            (
                str(user_id or ""),
                str(username or ""),
                str(query or ""),
                json.dumps(results or []),
                f"+{max(30, min(300, int(timeout_secs or 120)))} seconds",
            ),
        )


def get_search_session(user_id: str) -> dict | None:
    ensure_schema()
    with database.db_conn() as conn:
        row = conn.execute(
            """SELECT *, expires_at <= datetime('now') AS expired
               FROM radio_search_sessions
               WHERE user_id=?""",
            (str(user_id or ""),),
        ).fetchone()
    if not row:
        return None
    data = dict(row)
    try:
        data["results"] = json.loads(data.get("results_json") or "[]")
    except Exception:
        data["results"] = []
    data["expired"] = bool(data.get("expired"))
    return data


def clear_search_session(user_id: str) -> None:
    ensure_schema()
    with database.db_conn() as conn:
        conn.execute("DELETE FROM radio_search_sessions WHERE user_id=?", (str(user_id or ""),))


def update_request(request_id: int, **fields) -> None:
    if not fields:
        return
    ensure_schema()
    allowed = {
        "source_type", "source_ref", "title", "artist", "status", "priority",
        "disc_cost_charged", "payment_reason", "is_staff_free", "is_vip",
        "temp_filename", "azura_file_id", "azura_song_id", "azura_path",
        "prepared_at", "submitted_at", "playing_at", "played_at", "cleaned_at",
        "cancelled_at", "failed_at", "error", "finish_reason",
    }
    pairs = [(k, v) for k, v in fields.items() if k in allowed]
    if not pairs:
        return
    with database.db_conn() as conn:
        current = conn.execute("SELECT status FROM radio_requests WHERE id=?", (int(request_id),)).fetchone()
        if current and current["status"] in models.TERMINAL_STATUSES and fields.get("status") not in models.TERMINAL_STATUSES:
            print(
                f"[RADIO_PHASE4] event=refused_terminal_revival request_id={request_id} "
                f"old_status={current['status']!r} attempted_status={fields.get('status')!r}"
            )
            return
        sql = ", ".join(f"{key}=?" for key, _value in pairs)
        conn.execute(
            f"UPDATE radio_requests SET {sql} WHERE id=?",
            (*[value for _key, value in pairs], int(request_id)),
        )


def mark_status(request_id: int, status: str, **fields) -> None:
    status = models.normalize_status(status)
    stamps = {
        "preparing": "prepared_at",
        "ready": "prepared_at",
        "submitted": "submitted_at",
        "playing": "playing_at",
        "played": "played_at",
        "cleaned": "cleaned_at",
        "cancelled": "cancelled_at",
        "failed": "failed_at",
    }
    fields["status"] = status
    stamp = stamps.get(status)
    if stamp and stamp not in fields:
        fields[stamp] = _sql_now_marker()
    update_request_with_sql_markers(request_id, **fields)


def _sql_now_marker() -> str:
    return "__SQL_DATETIME_NOW__"


def update_request_with_sql_markers(request_id: int, **fields) -> None:
    if not fields:
        return
    ensure_schema()
    allowed = {
        "source_type", "source_ref", "title", "artist", "status", "priority",
        "disc_cost_charged", "payment_reason", "is_staff_free", "is_vip",
        "temp_filename", "azura_file_id", "azura_song_id", "azura_path",
        "prepared_at", "submitted_at", "playing_at", "played_at", "cleaned_at",
        "cancelled_at", "failed_at", "error", "finish_reason",
    }
    pairs = [(k, v) for k, v in fields.items() if k in allowed]
    if not pairs:
        return
    with database.db_conn() as conn:
        current = conn.execute("SELECT status FROM radio_requests WHERE id=?", (int(request_id),)).fetchone()
        attempted = fields.get("status")
        if current and current["status"] in models.TERMINAL_STATUSES and attempted not in models.TERMINAL_STATUSES:
            print(
                f"[RADIO_PHASE4] event=refused_terminal_revival request_id={request_id} "
                f"old_status={current['status']!r} attempted_status={attempted!r}"
            )
            return
        assignments = []
        values = []
        for key, value in pairs:
            if value == _sql_now_marker():
                assignments.append(f"{key}=datetime('now')")
            else:
                assignments.append(f"{key}=?")
                values.append(value)
        conn.execute(
            f"UPDATE radio_requests SET {', '.join(assignments)} WHERE id=?",
            (*values, int(request_id)),
        )


def active_requests(limit: int = 50) -> list[dict]:
    ensure_schema()
    with database.db_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM radio_requests
               WHERE status IN ('pending','preparing','ready','submitted','playing')
               ORDER BY id ASC
               LIMIT ?""",
            (max(1, min(100, int(limit))),),
        ).fetchall()
    return [dict(row) for row in rows]


def active_requests_for_user(user_id: str, limit: int = 50) -> list[dict]:
    ensure_schema()
    with database.db_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM radio_requests
               WHERE user_id=?
                 AND status IN ('pending','preparing','ready','submitted','playing')
               ORDER BY id ASC
               LIMIT ?""",
            (str(user_id or ""), max(1, min(100, int(limit)))),
        ).fetchall()
    return [dict(row) for row in rows]


def cancelable_requests_for_user(user_id: str, limit: int = 20) -> list[dict]:
    ensure_schema()
    with database.db_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM radio_requests
               WHERE user_id=?
                 AND status IN ('pending','preparing','ready','submitted')
               ORDER BY id ASC
               LIMIT ?""",
            (str(user_id or ""), max(1, min(50, int(limit)))),
        ).fetchall()
    return [dict(row) for row in rows]


def failed_requests_for_cleanup(limit: int = 50) -> list[dict]:
    ensure_schema()
    with database.db_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM radio_requests
               WHERE status='failed'
               ORDER BY id ASC
               LIMIT ?""",
            (max(1, min(100, int(limit))),),
        ).fetchall()
    return [dict(row) for row in rows]


def stuck_requests_for_cleanup(minutes: int = 30, limit: int = 50) -> list[dict]:
    ensure_schema()
    with database.db_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM radio_requests
               WHERE status IN ('pending','preparing','ready','submitted')
                 AND COALESCE(submitted_at, prepared_at, created_at) <= datetime('now', ?)
               ORDER BY id ASC
               LIMIT ?""",
            (f"-{max(1, int(minutes))} minutes", max(1, min(100, int(limit)))),
        ).fetchall()
    return [dict(row) for row in rows]


def stale_ready_submitted_requests(minutes: int = 30, limit: int = 50) -> list[dict]:
    ensure_schema()
    with database.db_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM radio_requests
               WHERE status IN ('ready','submitted')
                 AND COALESCE(submitted_at, prepared_at, created_at) <= datetime('now', ?)
               ORDER BY id ASC
               LIMIT ?""",
            (f"-{max(1, int(minutes))} minutes", max(1, min(100, int(limit)))),
        ).fetchall()
    return [dict(row) for row in rows]


def normalize_generated_request_name(value) -> str:
    text = str(value or "").strip().lower()
    if text.endswith(".mp3"):
        text = text[:-4]
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _match_method_for_track(row: dict, track: dict) -> str:
    np_media_id = str(track.get("media_id") or "").strip()
    np_song_id = str(track.get("song_id") or "").strip()
    np_unique_id = str(track.get("unique_id") or "").strip()
    np_path = str(track.get("path") or "").strip()
    np_filename = os.path.basename(np_path)
    candidate_file_id = str(row.get("azura_file_id") or "").strip()
    candidate_song_id = str(row.get("azura_song_id") or "").strip()
    candidate_filename = str(row.get("temp_filename") or "").strip()
    candidate_stem = os.path.splitext(candidate_filename)[0] if candidate_filename else ""
    candidate_path = str(row.get("azura_path") or "").strip()
    candidate_id = str(row.get("id") or "").strip()
    np_title = str(track.get("title") or "").strip()
    normalized_np_title = normalize_generated_request_name(np_title)
    normalized_candidate_filename = normalize_generated_request_name(candidate_filename)
    if np_media_id and candidate_file_id and np_media_id == candidate_file_id:
        return "media_id"
    if np_unique_id and candidate_song_id and np_unique_id == candidate_song_id:
        return "song_unique_id"
    if np_song_id and candidate_song_id and np_song_id == candidate_song_id:
        return "song_id"
    if np_filename and candidate_filename and np_filename == candidate_filename:
        return "media_path_basename"
    if np_path and candidate_path and np_path == candidate_path:
        return "media_path_exact"
    if np_path and candidate_filename and np_path.endswith(candidate_filename):
        return "media_path_endswith"
    if np_title.startswith(SAFE_REQUEST_TITLE_PREFIXES):
        if candidate_filename and np_title == candidate_filename:
            return "nowplaying_title_temp_filename"
        if candidate_stem and np_title == candidate_stem:
            return "nowplaying_title_temp_stem"
        if candidate_stem and np_title.startswith(candidate_stem):
            return "nowplaying_title_temp_stem"
        for prefix in SAFE_REQUEST_TITLE_PREFIXES:
            if candidate_id and np_title.startswith(f"{prefix}{candidate_id}_"):
                return "nowplaying_title_request_id_prefix"
    if normalized_np_title.startswith(NORMALIZED_REQUEST_TITLE_PREFIXES):
        if normalized_candidate_filename and normalized_np_title == normalized_candidate_filename:
            return "nowplaying_title_normalized_temp_filename"
        for prefix in NORMALIZED_REQUEST_TITLE_PREFIXES:
            if candidate_id and normalized_np_title.startswith(f"{prefix}{candidate_id}"):
                return "nowplaying_title_normalized_request_id_prefix"
    return ""


def _log_nowplaying_match_debug(track: dict, candidate: dict | None, match_method: str = "") -> None:
    candidate = candidate or {}
    print(
        "[RADIO_PHASE4] event=nowplaying_match_debug "
        f"np_media_id={str(track.get('media_id') or '')!r} "
        f"np_media_path={str(track.get('path') or '')!r} "
        f"np_song_id={str(track.get('song_id') or '')!r} "
        f"np_song_unique_id={str(track.get('unique_id') or '')!r} "
        f"np_title={str(track.get('title') or '')!r} "
        f"np_artist={str(track.get('artist') or '')!r} "
        f"candidate_request_id={candidate.get('id', '')!r} "
        f"candidate_status={candidate.get('status', '')!r} "
        f"candidate_temp_filename={candidate.get('temp_filename', '')!r} "
        f"normalized_np_title={normalize_generated_request_name(track.get('title'))!r} "
        f"normalized_candidate_filename={normalize_generated_request_name(candidate.get('temp_filename'))!r} "
        f"candidate_azura_file_id={candidate.get('azura_file_id', '')!r} "
        f"candidate_azura_song_id={candidate.get('azura_song_id', '')!r} "
        f"candidate_azura_path={candidate.get('azura_path', '')!r} "
        f"match_method={match_method!r}"
    )


def match_request_for_track(track: dict) -> dict | None:
    ensure_schema()
    with database.db_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM radio_requests
               WHERE status IN ('ready','submitted','playing')
               ORDER BY id ASC
               LIMIT 50"""
        ).fetchall()
    first_candidate = dict(rows[0]) if rows else None
    for row in rows:
        candidate = dict(row)
        method = _match_method_for_track(candidate, track)
        if method:
            _log_nowplaying_match_debug(track, candidate, method)
            return candidate
    _log_nowplaying_match_debug(track, first_candidate, "")
    return None


def increment_request_play_count(track_key: str, title: str, artist: str) -> None:
    ensure_schema()
    if not track_key:
        return
    with database.db_conn() as conn:
        conn.execute(
            """INSERT INTO radio_track_stats
               (track_key, title, artist, likes, dislikes, request_play_count, last_played_at)
               VALUES (?, ?, ?, 0, 0, 1, datetime('now'))
               ON CONFLICT(track_key) DO UPDATE SET
                   title=excluded.title,
                   artist=excluded.artist,
                   request_play_count=request_play_count+1,
                   last_played_at=datetime('now')""",
            (track_key, title or "", artist or ""),
        )


def active_queue_count() -> int:
    ensure_schema()
    with database.db_conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS n
               FROM radio_requests
               WHERE status IN ('pending','preparing','ready','submitted','playing')"""
        ).fetchone()
    return int(row["n"]) if row else 0


def get_runtime_state(key: str, default: str = "") -> str:
    ensure_schema()
    with database.db_conn() as conn:
        row = conn.execute(
            "SELECT value FROM radio_runtime_state WHERE key=?",
            (key,),
        ).fetchone()
    return str(row["value"]) if row else default


def set_runtime_state(key: str, value: Any) -> None:
    ensure_schema()
    raw = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
    with database.db_conn() as conn:
        conn.execute(
            """INSERT INTO radio_runtime_state (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                   value=excluded.value,
                   updated_at=datetime('now')""",
            (key, raw),
        )


def read_track_stats(track_key: str, title: str = "", artist: str = "") -> dict:
    ensure_schema()
    if not track_key:
        return {"likes": 0, "dislikes": 0, "request_play_count": 0}
    with database.db_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO radio_track_stats
               (track_key, title, artist, likes, dislikes, request_play_count, last_played_at)
               VALUES (?, ?, ?, 0, 0, 0, NULL)""",
            (track_key, title or "", artist or ""),
        )
        row = conn.execute(
            """SELECT likes, dislikes, request_play_count
               FROM radio_track_stats
               WHERE track_key=?""",
            (track_key,),
        ).fetchone()
    return dict(row) if row else {"likes": 0, "dislikes": 0, "request_play_count": 0}


def mark_last_poll(ok: bool, error: str = "") -> None:
    payload = {"ok": bool(ok), "error": error or ""}
    set_runtime_state("last_poll", payload)
    set_runtime_state("last_error", error or "none")
