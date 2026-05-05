"""
database.py
-----------
All SQLite database logic for the Mini Game Bot.

Tables:
  users          — one row per player (user_id, username, balance)
  daily_claims   — tracks the last date each player claimed /daily
  game_wins      — running win count per player per game type
  coinflip_history — log of every /coinflip result

All functions open their own connection and close it when done.
This keeps the code simple and safe for a single-threaded async bot.
"""

import sqlite3
from datetime import date

import config


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def get_connection() -> sqlite3.Connection:
    """
    Open and return a connection to the SQLite database.
    Row factory is set so we can access columns by name (row["balance"])
    instead of by index (row[0]).
    """
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Database initialisation
# ---------------------------------------------------------------------------

def init_db():
    """
    Create all tables if they don't already exist.
    Safe to call every time the bot starts — it never deletes existing data.
    """
    conn = get_connection()

    # users: one row per Highrise player
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id  TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            balance  INTEGER NOT NULL DEFAULT 0
        )
    """)

    # daily_claims: tracks the calendar date of each player's last /daily claim
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_claims (
            user_id    TEXT PRIMARY KEY,
            last_claim TEXT NOT NULL
        )
    """)

    # game_wins: total wins per player per mini-game
    # game_type is one of: 'trivia', 'scramble', 'riddle', 'coinflip'
    conn.execute("""
        CREATE TABLE IF NOT EXISTS game_wins (
            user_id   TEXT NOT NULL,
            username  TEXT NOT NULL,
            game_type TEXT NOT NULL,
            wins      INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, game_type)
        )
    """)

    # coinflip_history: log of every coinflip for stats / review
    conn.execute("""
        CREATE TABLE IF NOT EXISTS coinflip_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            username   TEXT NOT NULL,
            choice     TEXT NOT NULL,
            result     TEXT NOT NULL,
            bet        INTEGER NOT NULL,
            won        INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------

def ensure_user(user_id: str, username: str):
    """
    Register a player if they don't exist yet.
    New players start with STARTING_BALANCE coins.
    Also updates the stored username in case it changed.
    """
    conn = get_connection()
    conn.execute("""
        INSERT INTO users (user_id, username, balance)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET username = excluded.username
    """, (user_id, username, config.STARTING_BALANCE))
    conn.commit()
    conn.close()


def get_balance(user_id: str) -> int:
    """Return the coin balance for a player. Returns 0 if not found."""
    conn = get_connection()
    row = conn.execute(
        "SELECT balance FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return row["balance"] if row else 0


def adjust_balance(user_id: str, amount: int):
    """
    Add (or subtract, if amount is negative) coins from a player's balance.
    The balance is clamped to a minimum of 0 — it can never go negative.
    """
    conn = get_connection()
    conn.execute("""
        UPDATE users
        SET balance = MAX(0, balance + ?)
        WHERE user_id = ?
    """, (amount, user_id))
    conn.commit()
    conn.close()


def get_user_by_username(username: str) -> dict | None:
    """
    Look up a player by their Highrise username (case-insensitive).
    Returns a dict with 'user_id', 'username', 'balance', or None if not found.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT user_id, username, balance FROM users WHERE LOWER(username) = LOWER(?)",
        (username,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Daily claim helpers
# ---------------------------------------------------------------------------

def can_claim_daily(user_id: str) -> bool:
    """
    Return True if the player has NOT claimed their daily reward today.
    The 'day' resets at midnight (UTC).
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT last_claim FROM daily_claims WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return True  # never claimed before
    return row["last_claim"] != str(date.today())


def record_daily_claim(user_id: str):
    """Save today's date as the player's last /daily claim."""
    conn = get_connection()
    conn.execute("""
        INSERT INTO daily_claims (user_id, last_claim) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET last_claim = excluded.last_claim
    """, (user_id, str(date.today())))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Leaderboard helper
# ---------------------------------------------------------------------------

def get_leaderboard(limit: int = 10) -> list[dict]:
    """
    Return the top `limit` players sorted by balance (highest first).
    Each entry is a dict with 'rank', 'username', and 'balance'.
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT username, balance
        FROM users
        ORDER BY balance DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [
        {"rank": i + 1, "username": r["username"], "balance": r["balance"]}
        for i, r in enumerate(rows)
    ]


# ---------------------------------------------------------------------------
# Game win tracking
# ---------------------------------------------------------------------------

def record_game_win(user_id: str, username: str, game_type: str):
    """
    Increment the win counter for a player for a specific game type.
    Creates the row if it doesn't exist yet.
    """
    conn = get_connection()
    conn.execute("""
        INSERT INTO game_wins (user_id, username, game_type, wins) VALUES (?, ?, ?, 1)
        ON CONFLICT(user_id, game_type) DO UPDATE SET wins = wins + 1
    """, (user_id, username, game_type))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Coinflip history
# ---------------------------------------------------------------------------

def record_coinflip(user_id: str, username: str, choice: str,
                    result: str, bet: int, won: bool):
    """
    Log a coinflip result to the history table.

    Parameters
    ----------
    choice  : what the player chose ('heads' or 'tails')
    result  : what the coin landed on ('heads' or 'tails')
    bet     : how many coins were wagered
    won     : True if the player won, False if they lost
    """
    conn = get_connection()
    conn.execute("""
        INSERT INTO coinflip_history (user_id, username, choice, result, bet, won)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, username, choice, result, bet, int(won)))
    conn.commit()
    conn.close()
