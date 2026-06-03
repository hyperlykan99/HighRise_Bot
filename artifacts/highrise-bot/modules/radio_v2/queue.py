"""Radio V2 DB queue state."""
from __future__ import annotations

import time

import database as db
from modules.radio_v2 import models


_COLS = (
    "id", "user_id", "username", "title", "artist", "source_type", "source_ref",
    "temp_filename", "azura_file_id", "azura_song_id", "azura_request_id",
    "status", "payment_type", "song_play_refunded", "priority", "error",
    "created_at", "updated_at", "submitted_at", "started_at", "played_at",
    "cancelled_at", "cleaned_at",
)
_SEL = ", ".join(_COLS)


def _row(row) -> dict:
    return dict(zip(_COLS, row)) if row else {}


def ensure_schema() -> None:
    with db.db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS radio_v2_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                username TEXT,
                title TEXT,
                artist TEXT,
                source_type TEXT,
                source_ref TEXT,
                temp_filename TEXT,
                azura_file_id TEXT,
                azura_song_id TEXT,
                azura_request_id TEXT,
                status TEXT,
                payment_type TEXT,
                song_play_refunded INTEGER DEFAULT 0,
                priority INTEGER DEFAULT 0,
                error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                submitted_at TEXT,
                started_at TEXT,
                played_at TEXT,
                cancelled_at TEXT,
                cleaned_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_radio_v2_status ON radio_v2_requests(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_radio_v2_user ON radio_v2_requests(user_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_radio_v2_media ON radio_v2_requests(azura_file_id, azura_song_id, temp_filename)")


def create_request(
    *,
    user_id: str,
    username: str,
    title: str,
    artist: str = "",
    source_type: str,
    source_ref: str,
    payment_type: str,
    priority: int = 0,
) -> int:
    ensure_schema()
    with db.db_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO radio_v2_requests
                (user_id, username, title, artist, source_type, source_ref,
                 status, payment_type, priority, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, datetime('now'))
            """,
            (user_id, username, title, artist, source_type, source_ref, payment_type, int(priority or 0)),
        )
        return int(cur.lastrowid or 0)


def update_request(request_id: int, **fields: object) -> None:
    if not request_id or not fields:
        return
    ensure_schema()
    allowed = {c for c in _COLS if c != "id"}
    sets: list[str] = []
    vals: list[object] = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key}=?")
        vals.append(value)
    if not sets:
        return
    sets.append("updated_at=datetime('now')")
    vals.append(request_id)
    with db.db_conn() as conn:
        conn.execute(f"UPDATE radio_v2_requests SET {', '.join(sets)} WHERE id=?", vals)


def mark_status(request_id: int, status: str, **fields: object) -> None:
    status = (status or "").strip().lower()
    extra = dict(fields)
    extra["status"] = status
    if status == "submitted":
        extra.setdefault("submitted_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    elif status == "playing":
        extra.setdefault("started_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    elif status == "played":
        extra.setdefault("played_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    elif status == "cancelled":
        extra.setdefault("cancelled_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    elif status == "cleaned":
        extra.setdefault("cleaned_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    update_request(request_id, **extra)


def get_request(request_id: int) -> dict:
    ensure_schema()
    with db.db_conn() as conn:
        row = conn.execute(f"SELECT {_SEL} FROM radio_v2_requests WHERE id=?", (request_id,)).fetchone()
    return _row(row)


def active_count() -> int:
    ensure_schema()
    ph = ",".join("?" * len(models.ACTIVE_STATUSES))
    with db.db_conn() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) FROM radio_v2_requests WHERE status IN ({ph})",
            models.ACTIVE_STATUSES,
        ).fetchone()
    return int(row[0] if row else 0)


def future_count() -> int:
    ensure_schema()
    ph = ",".join("?" * len(models.WAITING_STATUSES))
    with db.db_conn() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) FROM radio_v2_requests WHERE status IN ({ph})",
            models.WAITING_STATUSES,
        ).fetchone()
    return int(row[0] if row else 0)


def user_active_count(user_id: str) -> int:
    ensure_schema()
    ph = ",".join("?" * len(models.ACTIVE_STATUSES))
    with db.db_conn() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) FROM radio_v2_requests WHERE user_id=? AND status IN ({ph})",
            (user_id, *models.ACTIVE_STATUSES),
        ).fetchone()
    return int(row[0] if row else 0)


def display_jobs() -> list[dict]:
    ensure_schema()
    ph = ",".join("?" * len(models.WAITING_STATUSES))
    with db.db_conn() as conn:
        rows = conn.execute(
            f"SELECT {_SEL} FROM radio_v2_requests "
            f"WHERE status IN ({ph}) ORDER BY id ASC LIMIT 20",
            models.WAITING_STATUSES,
        ).fetchall()
    return [_row(r) for r in rows]


def cancelable_for_user(user_id: str) -> list[dict]:
    ensure_schema()
    ph = ",".join("?" * len(models.WAITING_STATUSES))
    with db.db_conn() as conn:
        rows = conn.execute(
            f"SELECT {_SEL} FROM radio_v2_requests "
            f"WHERE user_id=? AND status IN ({ph}) ORDER BY id ASC",
            (user_id, *models.WAITING_STATUSES),
        ).fetchall()
    return [_row(r) for r in rows]


def active_for_user(user_id: str) -> list[dict]:
    ensure_schema()
    ph = ",".join("?" * len(models.ACTIVE_STATUSES))
    with db.db_conn() as conn:
        rows = conn.execute(
            f"SELECT {_SEL} FROM radio_v2_requests "
            f"WHERE user_id=? AND status IN ({ph}) ORDER BY id ASC",
            (user_id, *models.ACTIVE_STATUSES),
        ).fetchall()
    return [_row(r) for r in rows]


def currently_playing() -> dict:
    ensure_schema()
    with db.db_conn() as conn:
        row = conn.execute(
            f"SELECT {_SEL} FROM radio_v2_requests "
            "WHERE status='playing' ORDER BY started_at DESC, id DESC LIMIT 1"
        ).fetchone()
    return _row(row)


def match_nowplaying(media_id: str = "", song_id: str = "", path: str = "", title: str = "") -> dict:
    ensure_schema()
    filename = (path or "").rsplit("/", 1)[-1].strip()
    active = models.ACTIVE_STATUSES
    ph = ",".join("?" * len(active))
    with db.db_conn() as conn:
        if media_id:
            row = conn.execute(
                f"SELECT {_SEL} FROM radio_v2_requests WHERE azura_file_id=? AND status IN ({ph}) LIMIT 1",
                (media_id, *active),
            ).fetchone()
            if row:
                return _row(row)
        if song_id:
            row = conn.execute(
                f"SELECT {_SEL} FROM radio_v2_requests WHERE azura_song_id=? AND status IN ({ph}) LIMIT 1",
                (song_id, *active),
            ).fetchone()
            if row:
                return _row(row)
        if filename:
            row = conn.execute(
                f"SELECT {_SEL} FROM radio_v2_requests WHERE temp_filename=? AND status IN ({ph}) LIMIT 1",
                (filename, *active),
            ).fetchone()
            if row:
                return _row(row)
        if title:
            row = conn.execute(
                f"SELECT {_SEL} FROM radio_v2_requests "
                f"WHERE lower(title)=lower(?) AND status IN ({ph}) "
                "ORDER BY id ASC LIMIT 1",
                (title, *active),
            ).fetchone()
            if row:
                return _row(row)
    return {}


def recent_for_user(user_id: str, limit: int = 6) -> list[dict]:
    ensure_schema()
    with db.db_conn() as conn:
        rows = conn.execute(
            f"SELECT {_SEL} FROM radio_v2_requests WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, int(limit)),
        ).fetchall()
    return [_row(r) for r in rows]


def recent_room(limit: int = 10) -> list[dict]:
    ensure_schema()
    with db.db_conn() as conn:
        rows = conn.execute(f"SELECT {_SEL} FROM radio_v2_requests ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [_row(r) for r in rows]

