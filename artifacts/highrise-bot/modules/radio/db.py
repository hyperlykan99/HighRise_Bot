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
                    ORDER BY priority DESC, id ASC
                    LIMIT ?""",
                (*models.QUEUE_DISPLAY_STATUSES, max(1, min(50, int(limit)))),
            ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []


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
