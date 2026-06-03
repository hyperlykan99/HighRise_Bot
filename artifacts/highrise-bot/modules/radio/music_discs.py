"""Music Disc balance and ledger helpers for the radio rebuild."""

from __future__ import annotations

import database as db


BUY_REASON = "Music Shop 💽 Purchase"
REQUEST_REASON = "Song Request 💽"
REFUND_REASON = "Song Request 💽 Refund"
ADMIN_REASON = "Admin Music Disc Adjustment 💽"


def ensure_schema() -> None:
    with db.db_conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS music_disc_balances (
                user_id TEXT PRIMARY KEY,
                username TEXT,
                disc_balance INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS music_disc_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                username TEXT,
                amount INTEGER,
                action TEXT,
                reason TEXT,
                balance_after INTEGER,
                actor TEXT,
                created_at TEXT
            )"""
        )


def _ensure_balance_row(conn, user_id: str, username: str) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO music_disc_balances
           (user_id, username, disc_balance, created_at, updated_at)
           VALUES (?, ?, 0, datetime('now'), datetime('now'))""",
        (user_id, username or ""),
    )
    if username:
        conn.execute(
            """UPDATE music_disc_balances
               SET username=?, updated_at=datetime('now')
               WHERE user_id=? AND COALESCE(username, '') != ?""",
            (username, user_id, username),
        )


def _log_tx(conn, user_id: str, username: str, amount: int, action: str, reason: str, balance_after: int, actor: str) -> None:
    conn.execute(
        """INSERT INTO music_disc_transactions
           (user_id, username, amount, action, reason, balance_after, actor, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (user_id, username or "", int(amount), action, reason, int(balance_after), actor or "system"),
    )


def get_disc_balance(user_id: str) -> int:
    ensure_schema()
    with db.db_conn() as conn:
        row = conn.execute(
            "SELECT disc_balance FROM music_disc_balances WHERE user_id=?",
            (user_id,),
        ).fetchone()
    return int(row["disc_balance"]) if row else 0


def add_discs(user_id: str, username: str, amount: int, reason: str, actor: str = "system") -> int:
    ensure_schema()
    amount = max(0, int(amount))
    with db.db_conn() as conn:
        _ensure_balance_row(conn, user_id, username)
        conn.execute(
            """UPDATE music_disc_balances
               SET disc_balance=disc_balance+?, username=?, updated_at=datetime('now')
               WHERE user_id=?""",
            (amount, username or "", user_id),
        )
        row = conn.execute(
            "SELECT disc_balance FROM music_disc_balances WHERE user_id=?",
            (user_id,),
        ).fetchone()
        balance_after = int(row["disc_balance"]) if row else 0
        _log_tx(conn, user_id, username, amount, "add", reason, balance_after, actor)
    return balance_after


def deduct_discs(user_id: str, username: str, amount: int, reason: str, actor: str = "system") -> bool:
    ensure_schema()
    amount = max(0, int(amount))
    with db.db_conn() as conn:
        _ensure_balance_row(conn, user_id, username)
        row = conn.execute(
            "SELECT disc_balance FROM music_disc_balances WHERE user_id=?",
            (user_id,),
        ).fetchone()
        current = int(row["disc_balance"]) if row else 0
        if current < amount:
            return False
        balance_after = current - amount
        conn.execute(
            """UPDATE music_disc_balances
               SET disc_balance=?, username=?, updated_at=datetime('now')
               WHERE user_id=?""",
            (balance_after, username or "", user_id),
        )
        _log_tx(conn, user_id, username, -amount, "deduct", reason, balance_after, actor)
    return True


def refund_discs(user_id: str, username: str, amount: int, reason: str, actor: str = "system") -> int:
    return add_discs(user_id, username, amount, reason, actor)


def get_transaction_history(user_id: str | None = None, limit: int = 20) -> list[dict]:
    ensure_schema()
    limit = max(1, min(100, int(limit)))
    with db.db_conn() as conn:
        if user_id:
            rows = conn.execute(
                """SELECT * FROM music_disc_transactions
                   WHERE user_id=?
                   ORDER BY id DESC
                   LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM music_disc_transactions
                   ORDER BY id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
    return [dict(row) for row in rows]


def get_daily_purchased(user_id: str) -> int:
    ensure_schema()
    with db.db_conn() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(amount), 0) AS total
               FROM music_disc_transactions
               WHERE user_id=?
                 AND reason=?
                 AND amount > 0
                 AND date(created_at)=date('now')""",
            (user_id, BUY_REASON),
        ).fetchone()
    return int(row["total"]) if row else 0

