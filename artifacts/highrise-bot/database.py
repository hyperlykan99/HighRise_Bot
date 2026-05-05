"""
database.py
-----------
All SQLite database logic for the Mini Game Bot.

Tables:
  users            — one row per player (id, username, balance, xp, level,
                     wins, coins_earned, equipped display values and IDs)
  daily_claims     — tracks the last date each player claimed /daily
  game_wins        — running win count per player per game type
  coinflip_history — log of every /coinflip result
  owned_items      — shop items each player has purchased
  purchase_history — log of every shop purchase

Column notes for equipped cosmetics:
  equipped_badge    / equipped_title    — display values ("🔥" / "[High Roller]")
                                          used by get_display_name()
  equipped_badge_id / equipped_title_id — catalog IDs ("fire_badge" / "high_roller")
                                          used by get_equipped_ids() for benefit lookups
"""

import math
import sqlite3
from datetime import date

import config


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Database initialisation + migration
# ---------------------------------------------------------------------------

def init_db():
    """Create all tables if needed, then run safe column migrations."""
    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id              TEXT PRIMARY KEY,
            username             TEXT NOT NULL,
            balance              INTEGER NOT NULL DEFAULT 0,
            xp                   INTEGER NOT NULL DEFAULT 0,
            level                INTEGER NOT NULL DEFAULT 1,
            total_games_won      INTEGER NOT NULL DEFAULT 0,
            total_coins_earned   INTEGER NOT NULL DEFAULT 0,
            equipped_badge       TEXT,
            equipped_title       TEXT,
            equipped_badge_id    TEXT,
            equipped_title_id    TEXT
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
    """Add new columns to existing tables. Safe to run every startup."""
    conn = get_connection()
    for sql in [
        "ALTER TABLE users ADD COLUMN xp                   INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN level                INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE users ADD COLUMN total_games_won      INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN total_coins_earned   INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN equipped_badge       TEXT",
        "ALTER TABLE users ADD COLUMN equipped_title       TEXT",
        "ALTER TABLE users ADD COLUMN equipped_badge_id    TEXT",
        "ALTER TABLE users ADD COLUMN equipped_title_id    TEXT",
    ]:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass

    # Data migrations — safe no-ops if already applied or no matching rows exist
    conn.execute("UPDATE owned_items      SET item_id = 'elite'                                              WHERE item_id = 'room_legend'")
    conn.execute("UPDATE purchase_history SET item_id = 'elite'                                              WHERE item_id = 'room_legend'")
    conn.execute("UPDATE users            SET equipped_title = '[Elite]', equipped_title_id = 'elite'        WHERE equipped_title_id = 'room_legend'")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# XP / levelling helpers
# ---------------------------------------------------------------------------

def _xp_to_level(xp: int) -> int:
    if xp <= 0:
        return 1
    return max(1, math.floor((1 + math.sqrt(1 + 2 * xp / 25)) / 2))


def xp_for_level(level: int) -> int:
    if level <= 1:
        return 0
    return 50 * level * (level - 1)


def add_xp(user_id: str, amount: int) -> tuple[int, int, int]:
    """Add XP, recompute level. Returns (total_xp, old_level, new_level)."""
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
    conn = get_connection()
    row = conn.execute("""
        SELECT username, balance, xp, level, total_games_won, total_coins_earned,
               equipped_badge, equipped_title
        FROM users WHERE user_id = ?
    """, (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def get_xp_leaderboard(limit: int = 10) -> list[dict]:
    conn = get_connection()
    rows = conn.execute("""
        SELECT username, xp, level, equipped_badge, equipped_title
        FROM users ORDER BY xp DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [
        {
            "rank":    i + 1,
            "username": r["username"],
            "xp":      r["xp"],
            "level":   r["level"],
            "display": _build_display(r["equipped_badge"], r["username"], r["equipped_title"]),
        }
        for i, r in enumerate(rows)
    ]


# ---------------------------------------------------------------------------
# Shop / cosmetics helpers
# ---------------------------------------------------------------------------

def _build_display(badge: str | None, username: str, title: str | None) -> str:
    parts = []
    if badge:
        parts.append(badge)
    if title:
        parts.append(title)
    parts.append(f"@{username}")
    return " ".join(parts)


def get_display_name(user_id: str, username: str) -> str:
    """
    Return the player's full display string: <badge> @username <title>
    Falls back to @username if they have nothing equipped.
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


def get_equipped_ids(user_id: str) -> dict:
    """
    Return the catalog IDs of what the player has equipped.
    Used by get_player_benefits() in modules/shop.py.
    Returns {'badge_id': str|None, 'title_id': str|None}.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT equipped_badge_id, equipped_title_id FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return {"badge_id": None, "title_id": None}
    return {
        "badge_id":  row["equipped_badge_id"],
        "title_id":  row["equipped_title_id"],
    }


def get_owned_items(user_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT item_id, item_type FROM owned_items WHERE user_id = ?",
        (user_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def owns_item(user_id: str, item_id: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM owned_items WHERE user_id = ? AND item_id = ?",
        (user_id, item_id)
    ).fetchone()
    conn.close()
    return row is not None


def buy_item(user_id: str, username: str, item_id: str,
             item_type: str, price: int) -> bool:
    """Deduct coins and record purchase atomically. Returns True on success."""
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
    Stores both the display value (for announcements) and item ID (for benefits).
    """
    display_col = "equipped_badge"    if item_type == "badge" else "equipped_title"
    id_col      = "equipped_badge_id" if item_type == "badge" else "equipped_title_id"
    conn = get_connection()
    conn.execute(
        f"UPDATE users SET {display_col} = ?, {id_col} = ? WHERE user_id = ?",
        (display, item_id, user_id)
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------

def ensure_user(user_id: str, username: str):
    conn = get_connection()
    conn.execute("""
        INSERT INTO users (user_id, username, balance)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET username = excluded.username
    """, (user_id, username, config.STARTING_BALANCE))
    conn.commit()
    conn.close()


def get_balance(user_id: str) -> int:
    conn = get_connection()
    row = conn.execute(
        "SELECT balance FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return row["balance"] if row else 0


def adjust_balance(user_id: str, amount: int):
    conn = get_connection()
    conn.execute("""
        UPDATE users SET balance = MAX(0, balance + ?) WHERE user_id = ?
    """, (amount, user_id))
    conn.commit()
    conn.close()


def get_user_by_username(username: str) -> dict | None:
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
    conn = get_connection()
    row = conn.execute(
        "SELECT last_claim FROM daily_claims WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return True
    return row["last_claim"] != str(date.today())


def record_daily_claim(user_id: str):
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
    conn = get_connection()
    rows = conn.execute("""
        SELECT username, balance, equipped_badge, equipped_title
        FROM users ORDER BY balance DESC LIMIT ?
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
    conn = get_connection()
    conn.execute("""
        INSERT INTO game_wins (user_id, username, game_type, wins) VALUES (?, ?, ?, 1)
        ON CONFLICT(user_id, game_type) DO UPDATE SET wins = wins + 1
    """, (user_id, username, game_type))
    conn.execute("""
        UPDATE users SET total_games_won = total_games_won + 1 WHERE user_id = ?
    """, (user_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Coinflip history
# ---------------------------------------------------------------------------

def record_coinflip(user_id: str, username: str, choice: str,
                    result: str, bet: int, won: bool):
    conn = get_connection()
    conn.execute("""
        INSERT INTO coinflip_history (user_id, username, choice, result, bet, won)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, username, choice, result, bet, int(won)))
    conn.commit()
    conn.close()
