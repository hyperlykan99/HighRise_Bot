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

    # Song queue table: holds pending song requests in order.
    # The 'priority' column (1 = priority, 0 = normal) controls ordering:
    # current song → priority songs (by id) → normal songs (by id).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS song_queue (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      TEXT NOT NULL,
            username     TEXT NOT NULL,
            song         TEXT NOT NULL,
            priority     INTEGER NOT NULL DEFAULT 0,
            requested_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Migration: add 'priority' to song_queue if upgrading from an older version.
    # SQLite raises OperationalError if the column already exists — we ignore that.
    try:
        cursor.execute("ALTER TABLE song_queue ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists — nothing to do

    # Request history: every song that was ever requested
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS request_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL,
            username    TEXT NOT NULL,
            song        TEXT NOT NULL,
            priority    INTEGER NOT NULL DEFAULT 0,
            requested_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Migration: add 'priority' to request_history if upgrading from an older version.
    try:
        cursor.execute("ALTER TABLE request_history ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists — nothing to do

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
    # Always keep username up to date in case it changed
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

# Queue ordering rule (used in every SELECT from song_queue):
#   1. The current song (lowest id) always stays first.
#   2. Priority songs come next, ordered by the time they were added (id ASC).
#   3. Normal songs come last, also ordered by id ASC.
_QUEUE_ORDER = """
    ORDER BY
        CASE
            WHEN id = (SELECT MIN(id) FROM song_queue) THEN 0
            WHEN priority = 1                          THEN 1
            ELSE                                            2
        END,
        id ASC
"""


def is_song_in_queue(song: str) -> bool:
    """
    Return True if an identical song title/link is already in the queue.
    Comparison is case-insensitive and trims extra whitespace.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM song_queue WHERE LOWER(TRIM(song)) = LOWER(TRIM(?))",
        (song,)
    ).fetchone()
    conn.close()
    return row["cnt"] > 0


def get_queue_length() -> int:
    """Return the total number of songs currently in the queue."""
    conn = get_connection()
    row = conn.execute("SELECT COUNT(*) as cnt FROM song_queue").fetchone()
    conn.close()
    return row["cnt"]


def add_to_queue(user_id: str, username: str, song: str, priority: bool = False) -> int:
    """
    Add a song to the queue and history.
    If priority=True the song is flagged so it sorts after the current song
    but before all normal requests.
    Returns the visible queue position of the new song (1-indexed).
    """
    conn = get_connection()
    priority_val = 1 if priority else 0

    conn.execute(
        "INSERT INTO song_queue (user_id, username, song, priority) VALUES (?, ?, ?, ?)",
        (user_id, username, song, priority_val)
    )
    conn.execute(
        "INSERT INTO request_history (user_id, username, song, priority) VALUES (?, ?, ?, ?)",
        (user_id, username, song, priority_val)
    )
    conn.commit()

    # Calculate the visible position of the newly added song
    rows = conn.execute(f"SELECT id FROM song_queue {_QUEUE_ORDER}").fetchall()
    conn.close()

    # Find the new row (it will have the largest id)
    new_id = max(r["id"] for r in rows)
    for pos, row in enumerate(rows, start=1):
        if row["id"] == new_id:
            return pos
    return get_queue_length()  # fallback


def get_queue(limit: int = 5) -> list:
    """Return the next `limit` songs from the queue in display order."""
    conn = get_connection()
    rows = conn.execute(
        f"SELECT id, username, song, priority FROM song_queue {_QUEUE_ORDER} LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_current_song() -> dict | None:
    """Return the song at the front of the queue (the 'now playing' song)."""
    conn = get_connection()
    row = conn.execute(
        f"SELECT id, username, song, priority FROM song_queue {_QUEUE_ORDER} LIMIT 1"
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
        f"SELECT id, username, song, priority FROM song_queue {_QUEUE_ORDER}"
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
    """Remove and return the first song in the ordered queue."""
    conn = get_connection()
    row = conn.execute(
        f"SELECT id, username, song, priority FROM song_queue {_QUEUE_ORDER} LIMIT 1"
    ).fetchone()

    if row is None:
        conn.close()
        return None

    conn.execute("DELETE FROM song_queue WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return dict(row)


def clear_queue() -> int:
    """
    Remove every song from the queue.
    Returns the number of songs that were deleted.
    """
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) as cnt FROM song_queue").fetchone()["cnt"]
    conn.execute("DELETE FROM song_queue")
    conn.commit()
    conn.close()
    return count
