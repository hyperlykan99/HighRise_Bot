"""
database.py
-----------
Handles all SQLite database operations for the bot.
Tables:
  - users          : stores user info and token balances
  - song_queue     : stores the current song request queue
  - request_history: stores all past song requests
  - daily_claims   : tracks when users last claimed their daily tokens
"""

import sqlite3
import os
from datetime import date

# The database file will be stored next to this file
DB_PATH = os.path.join(os.path.dirname(__file__), "bot_data.db")


def get_connection() -> sqlite3.Connection:
    """Open and return a SQLite connection with row factory set."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # allows dict-like access to rows
    return conn


def init_db():
    """Create all tables if they don't already exist. Call this on startup."""
    conn = get_connection()
    cursor = conn.cursor()

    # Users table: stores username and token balance
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id   TEXT PRIMARY KEY,
            username  TEXT NOT NULL,
            balance   INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Song queue table: holds pending song requests in order
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS song_queue (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL,
            username    TEXT NOT NULL,
            song        TEXT NOT NULL,
            requested_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Request history: every song that was ever requested
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS request_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL,
            username    TEXT NOT NULL,
            song        TEXT NOT NULL,
            requested_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Daily claims: tracks the last date a user claimed free tokens
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_claims (
            user_id    TEXT PRIMARY KEY,
            last_claim TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------

def ensure_user(user_id: str, username: str):
    """Create a user record if it doesn't exist yet."""
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, username, balance) VALUES (?, ?, 0)",
        (user_id, username)
    )
    # Always keep username up to date
    conn.execute(
        "UPDATE users SET username = ? WHERE user_id = ?",
        (username, user_id)
    )
    conn.commit()
    conn.close()


def get_balance(user_id: str) -> int:
    """Return the token balance for a user (0 if not found)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT balance FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return row["balance"] if row else 0


def adjust_balance(user_id: str, amount: int):
    """Add (or subtract if negative) tokens from a user's balance."""
    conn = get_connection()
    conn.execute(
        "UPDATE users SET balance = balance + ? WHERE user_id = ?",
        (amount, user_id)
    )
    conn.commit()
    conn.close()


def set_balance_by_username(username: str, amount: int) -> bool:
    """
    Add tokens to a user found by username (case-insensitive).
    Returns True if the user was found, False otherwise.
    """
    conn = get_connection()
    cursor = conn.execute(
        "UPDATE users SET balance = balance + ? WHERE LOWER(username) = LOWER(?)",
        (amount, username)
    )
    found = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return found


# ---------------------------------------------------------------------------
# Daily claim helpers
# ---------------------------------------------------------------------------

def can_claim_daily(user_id: str) -> bool:
    """Return True if the user hasn't claimed tokens today."""
    conn = get_connection()
    row = conn.execute(
        "SELECT last_claim FROM daily_claims WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return True
    return row["last_claim"] != str(date.today())


def record_daily_claim(user_id: str):
    """Record that a user claimed their daily tokens today."""
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO daily_claims (user_id, last_claim) VALUES (?, ?)",
        (user_id, str(date.today()))
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Song queue helpers
# ---------------------------------------------------------------------------

def add_to_queue(user_id: str, username: str, song: str) -> int:
    """
    Add a song to the queue and history.
    Returns the position number in the queue (1-indexed).
    """
    conn = get_connection()
    conn.execute(
        "INSERT INTO song_queue (user_id, username, song) VALUES (?, ?, ?)",
        (user_id, username, song)
    )
    conn.execute(
        "INSERT INTO request_history (user_id, username, song) VALUES (?, ?, ?)",
        (user_id, username, song)
    )
    conn.commit()

    # Figure out the position of the newly added song
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM song_queue"
    ).fetchone()
    conn.close()
    return row["cnt"]


def get_queue(limit: int = 5) -> list:
    """Return the next `limit` songs from the front of the queue."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, username, song FROM song_queue ORDER BY id ASC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_current_song() -> dict | None:
    """Return the song at the front of the queue (the 'now playing' song)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id, username, song FROM song_queue ORDER BY id ASC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def remove_from_queue(queue_position: int) -> dict | None:
    """
    Remove a song by its visible queue position (1-indexed).
    Returns the removed song dict, or None if position is invalid.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, username, song FROM song_queue ORDER BY id ASC"
    ).fetchall()

    if queue_position < 1 or queue_position > len(rows):
        conn.close()
        return None

    target = rows[queue_position - 1]
    conn.execute("DELETE FROM song_queue WHERE id = ?", (target["id"],))
    conn.commit()
    conn.close()
    return dict(target)


def skip_current_song() -> dict | None:
    """Remove and return the first song in the queue (skip it)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id, username, song FROM song_queue ORDER BY id ASC LIMIT 1"
    ).fetchone()

    if row is None:
        conn.close()
        return None

    conn.execute("DELETE FROM song_queue WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return dict(row)


def get_queue_length() -> int:
    """Return the total number of songs currently in the queue."""
    conn = get_connection()
    row = conn.execute("SELECT COUNT(*) as cnt FROM song_queue").fetchone()
    conn.close()
    return row["cnt"]
