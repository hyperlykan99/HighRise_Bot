"""
database.py
-----------
All SQLite database logic for the Mini Game Bot.

Tables:
  users            — one row per player (id, username, balance, xp, level,
                     wins, coins_earned, equipped_badge, equipped_title)
  daily_claims     — tracks the last date each player claimed /daily
  game_wins        — running win count per player per game type
  coinflip_history — log of every /coinflip result
  owned_items      — shop items each player has purchased
  purchase_history — log of every shop purchase

All functions open their own connection and close it when done.
"""

import math
import sqlite3
from datetime import date

import config


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def get_connection() -> sqlite3.Connection:
    """
    Open and return a connection to the SQLite database.
    Row factory is set so columns are accessible by name (row["balance"]).
    """
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Database initialisation + migration
# ---------------------------------------------------------------------------

def init_db():
    """
    Create all tables if they don't already exist, then run migrations.
    Safe to call every time the bot starts — never deletes existing data.
    """
    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id            TEXT PRIMARY KEY,
            username           TEXT NOT NULL,
            balance            INTEGER NOT NULL DEFAULT 0,
            xp                 INTEGER NOT NULL DEFAULT 0,
            level              INTEGER NOT NULL DEFAULT 1,
            total_games_won    INTEGER NOT NULL DEFAULT 0,
            total_coins_earned INTEGER NOT NULL DEFAULT 0,
            equipped_badge     TEXT,
            equipped_title     TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_claims (
            user_id    TEXT PRIMARY KEY,
            last_claim TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS game_wins (
            user_id   TEXT NOT NULL,
            username  TEXT NOT NULL,
            game_type TEXT NOT NULL,
            wins      INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, game_type)
        )
    """)

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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS owned_items (
            user_id   TEXT NOT NULL,
            item_id   TEXT NOT NULL,
            item_type TEXT NOT NULL,
            PRIMARY KEY (user_id, item_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS purchase_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            username   TEXT NOT NULL,
            item_id    TEXT NOT NULL,
            price      INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()

    _migrate_db()


def _migrate_db():
    """
    Add new columns to existing tables if they don't exist yet.
    SQLite doesn't support IF NOT EXISTS for columns, so we use try/except.
    """
    conn = get_connection()
    for sql in [
        "ALTER TABLE users ADD COLUMN xp                 INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN level              INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE users ADD COLUMN total_games_won    INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN total_coins_earned INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN equipped_badge     TEXT",
        "ALTER TABLE users ADD COLUMN equipped_title     TEXT",
    ]:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass   # column already exists
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# XP / levelling helpers
# ---------------------------------------------------------------------------

def _xp_to_level(xp: int) -> int:
    """
    Compute level from total accumulated XP.

    Level formula — total XP required to REACH level n:
        xp_for_level(n) = 50 * n * (n - 1)
        Level 1 =    0 XP  |  Level 2 =  100 XP
        Level 3 =  300 XP  |  Level 4 =  600 XP  |  Level 5 = 1000 XP

    Inverse:  n = floor( (1 + sqrt(1 + 2*xp/25)) / 2 )
    """
    if xp <= 0:
        return 1
    return max(1, math.floor((1 + math.sqrt(1 + 2 * xp / 25)) / 2))


def xp_for_level(level: int) -> int:
    """Return the total XP required to reach `level` (level 1 = 0 XP)."""
    if level <= 1:
        return 0
    return 50 * level * (level - 1)


def add_xp(user_id: str, amount: int) -> tuple[int, int, int]:
    """
    Add `amount` XP to a player and recompute their level.
    Returns (total_xp, old_level, new_level).
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT xp, level FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row is None:
        conn.close()
        return 0, 1, 1
    old_xp    = row["xp"]
    old_level = row["level"]
    new_xp    = old_xp + max(0, amount)
    new_level = _xp_to_level(new_xp)
    conn.execute(
        "UPDATE users SET xp = ?, level = ? WHERE user_id = ?",
        (new_xp, new_level, user_id)
    )
    conn.commit()
    conn.close()
    return new_xp, old_level, new_level


def add_coins_earned(user_id: str, amount: int):
    """Increment the lifetime coins-earned counter for a player."""
    if amount <= 0:
        return
    conn = get_connection()
    conn.execute(
        "UPDATE users SET total_coins_earned = total_coins_earned + ? WHERE user_id = ?",
        (amount, user_id)
    )
    conn.commit()
    conn.close()


def get_profile(user_id: str) -> dict:
    """Return a dict with the player's full profile, or {} if not found."""
    conn = get_connection()
    row = conn.execute("""
        SELECT username, balance, xp, level, total_games_won, total_coins_earned,
               equipped_badge, equipped_title
        FROM users
        WHERE user_id = ?
    """, (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def get_xp_leaderboard(limit: int = 10) -> list[dict]:
    """Return the top `limit` players sorted by XP (highest first)."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT username, xp, level, equipped_badge, equipped_title
        FROM users
        ORDER BY xp DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [
        {
            "rank":     i + 1,
            "username": r["username"],
            "xp":       r["xp"],
            "level":    r["level"],
            "display":  _build_display(r["equipped_badge"], r["username"], r["equipped_title"]),
        }
        for i, r in enumerate(rows)
    ]


# ---------------------------------------------------------------------------
# Shop / cosmetics helpers
# ---------------------------------------------------------------------------

def _build_display(badge: str | None, username: str, title: str | None) -> str:
    """Assemble the display string from badge, username, and title."""
    parts = []
    if badge:
        parts.append(badge)
    parts.append(f"@{username}")
    if title:
        parts.append(title)
    return " ".join(parts)


def get_display_name(user_id: str, username: str) -> str:
    """
    Return the player's display string with equipped badge and title.

    Format:   <badge> @username <title>
    Example:  🔥 @Marion [High Roller]

    If nothing is equipped, returns @username.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT equipped_badge, equipped_title FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return f"@{username}"
    return _build_display(row["equipped_badge"], username, row["equipped_title"])


def get_owned_items(user_id: str) -> list[dict]:
    """Return all shop items owned by a player as a list of dicts."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT item_id, item_type FROM owned_items WHERE user_id = ?",
        (user_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def owns_item(user_id: str, item_id: str) -> bool:
    """Return True if the player already owns this item."""
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM owned_items WHERE user_id = ? AND item_id = ?",
        (user_id, item_id)
    ).fetchone()
    conn.close()
    return row is not None


def buy_item(user_id: str, username: str, item_id: str,
             item_type: str, price: int) -> bool:
    """
    Deduct `price` coins and record the purchase.
    Uses a single transaction so balance and ownership stay in sync.
    Returns True on success, False if the player can't afford it.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT balance FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None or row["balance"] < price:
            return False
        conn.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (price, user_id)
        )
        conn.execute(
            "INSERT OR IGNORE INTO owned_items (user_id, item_id, item_type) VALUES (?, ?, ?)",
            (user_id, item_id, item_type)
        )
        conn.execute(
            "INSERT INTO purchase_history (user_id, username, item_id, price) VALUES (?, ?, ?, ?)",
            (user_id, username, item_id, price)
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def equip_item(user_id: str, item_id: str, item_type: str, display: str):
    """
    Set the player's equipped badge or title.
    `display` is the emoji (badge) or bracket text like "[High Roller]" (title).
    """
    column = "equipped_badge" if item_type == "badge" else "equipped_title"
    conn = get_connection()
    conn.execute(
        f"UPDATE users SET {column} = ? WHERE user_id = ?",
        (display, user_id)
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------

def ensure_user(user_id: str, username: str):
    """
    Register a player if they don't exist yet, or update their stored username.
    New players start with STARTING_BALANCE coins, 0 XP, and Level 1.
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
    Add (or subtract, if negative) coins from a player's balance.
    Balance is clamped to a minimum of 0.
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
    """Look up a player by Highrise username (case-insensitive)."""
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
    """Return True if the player has NOT claimed their daily reward today."""
    conn = get_connection()
    row = conn.execute(
        "SELECT last_claim FROM daily_claims WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return True
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
# Leaderboard helpers
# ---------------------------------------------------------------------------

def get_leaderboard(limit: int = 10) -> list[dict]:
    """Return the top `limit` players sorted by coin balance (highest first)."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT username, balance, equipped_badge, equipped_title
        FROM users
        ORDER BY balance DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [
        {
            "rank":     i + 1,
            "username": r["username"],
            "balance":  r["balance"],
            "display":  _build_display(r["equipped_badge"], r["username"], r["equipped_title"]),
        }
        for i, r in enumerate(rows)
    ]


# ---------------------------------------------------------------------------
# Game win tracking
# ---------------------------------------------------------------------------

def record_game_win(user_id: str, username: str, game_type: str):
    """
    Increment the per-game-type win counter and the overall total_games_won.
    Creates the game_wins row if it doesn't exist yet.
    """
    conn = get_connection()
    conn.execute("""
        INSERT INTO game_wins (user_id, username, game_type, wins) VALUES (?, ?, ?, 1)
        ON CONFLICT(user_id, game_type) DO UPDATE SET wins = wins + 1
    """, (user_id, username, game_type))
    conn.execute("""
        UPDATE users SET total_games_won = total_games_won + 1
        WHERE user_id = ?
    """, (user_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Coinflip history
# ---------------------------------------------------------------------------

def record_coinflip(user_id: str, username: str, choice: str,
                    result: str, bet: int, won: bool):
    """Log a coinflip result to the history table."""
    conn = get_connection()
    conn.execute("""
        INSERT INTO coinflip_history (user_id, username, choice, result, bet, won)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, username, choice, result, bet, int(won)))
    conn.commit()
    conn.close()
