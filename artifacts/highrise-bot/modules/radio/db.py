"""DB schema and read helpers for the rebuilt radio skeleton."""

from __future__ import annotations

import json
from typing import Any

import database as database
from modules.radio import models
from modules.radio import settings as radio_settings


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


def match_request_for_track(track: dict) -> dict | None:
    ensure_schema()
    media_id = str(track.get("media_id") or "").strip()
    song_id = str(track.get("unique_id") or track.get("song_id") or "").strip()
    filename = str(track.get("filename") or "").strip()
    path = str(track.get("path") or "").strip()
    path_file = path.rsplit("/", 1)[-1] if path else ""
    clauses = []
    params = []
    if media_id:
        clauses.append("azura_file_id=?")
        params.append(media_id)
    if song_id:
        clauses.append("azura_song_id=?")
        params.append(song_id)
    for value in (filename, path_file):
        if value:
            clauses.append("temp_filename=?")
            params.append(value)
            clauses.append("azura_path=?")
            params.append(f"Requests/{value}")
    if not clauses:
        return None
    with database.db_conn() as conn:
        row = conn.execute(
            f"""SELECT * FROM radio_requests
                WHERE status IN ('pending','preparing','ready','submitted','playing')
                  AND ({' OR '.join(clauses)})
                ORDER BY id ASC
                LIMIT 1""",
            tuple(params),
        ).fetchone()
    return dict(row) if row else None


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
