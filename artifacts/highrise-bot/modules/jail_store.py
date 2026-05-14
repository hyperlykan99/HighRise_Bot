"""modules/jail_store.py — DB operations for jail sentences and logs (3.4A)."""
from __future__ import annotations
import time
import database as db


def create_sentence(
    target_uid: str,
    target_uname: str,
    by_uid: str,
    by_uname: str,
    duration_seconds: int,
    bail_cost: int,
    reason: str = "luxe_jail",
) -> int:
    """Create a new active jail sentence. Returns the new sentence id."""
    now = time.time()
    end = now + duration_seconds
    conn = db.get_connection()
    cur = conn.execute(
        """INSERT INTO jail_sentences
           (target_user_id, target_username, jailed_by_user_id, jailed_by_username,
            start_ts, end_ts, duration_seconds, remaining_seconds,
            jail_reason, jail_source, status, bail_cost, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'player', 'active', ?,
                   datetime('now'), datetime('now'))""",
        (
            target_uid, target_uname.lower(), by_uid, by_uname.lower(),
            now, end, duration_seconds, duration_seconds, reason, bail_cost,
        ),
    )
    conn.commit()
    sid = cur.lastrowid
    conn.close()
    return sid


def get_active_sentence(user_id: str) -> dict | None:
    """Return the most recent active sentence for a user, or None."""
    conn = db.get_connection()
    row = conn.execute(
        "SELECT * FROM jail_sentences WHERE target_user_id=? AND status='active' "
        "ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_active_sentences() -> list[dict]:
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM jail_sentences WHERE status='active' ORDER BY end_ts ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _update_status(sentence_id: int, status: str) -> None:
    conn = db.get_connection()
    conn.execute(
        "UPDATE jail_sentences SET status=?, updated_at=datetime('now') WHERE id=?",
        (status, sentence_id),
    )
    conn.commit()
    conn.close()


def mark_bailed(sentence_id: int) -> None:
    _update_status(sentence_id, "bailed")


def mark_expired(sentence_id: int) -> None:
    _update_status(sentence_id, "expired")


def mark_released(sentence_id: int) -> None:
    _update_status(sentence_id, "released")


def count_today_jails_by(user_id: str) -> int:
    """Count sentences this user created today (any terminal status)."""
    conn = db.get_connection()
    row = conn.execute(
        """SELECT COUNT(*) AS cnt FROM jail_sentences
           WHERE jailed_by_user_id=?
             AND status IN ('active','bailed','expired','released')
             AND date(created_at) = date('now')""",
        (user_id,),
    ).fetchone()
    conn.close()
    return int(row["cnt"]) if row else 0


def get_last_jail_by(user_id: str) -> float:
    """Return unix timestamp of the last sentence this user created, or 0.0."""
    conn = db.get_connection()
    row = conn.execute(
        "SELECT start_ts FROM jail_sentences WHERE jailed_by_user_id=? "
        "ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return float(row["start_ts"]) if row else 0.0


def log_jail_action(
    action: str,
    target_uid: str,
    target_uname: str,
    actor_uid: str,
    actor_uname: str,
    amount: int = 0,
    details: str = "",
) -> None:
    try:
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO jail_logs
               (action, target_user_id, target_username,
                actor_user_id, actor_username, amount, details, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (action, target_uid, target_uname.lower(),
             actor_uid, actor_uname.lower(), amount, details),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[JAIL LOG ERROR] {e!r}")
