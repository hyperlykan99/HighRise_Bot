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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS achievements (
            user_id        TEXT NOT NULL,
            achievement_id TEXT NOT NULL,
            unlocked_at    TEXT NOT NULL DEFAULT (datetime('now')),
            claimed        INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, achievement_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bj_stats (
            user_id        TEXT PRIMARY KEY,
            bj_wins        INTEGER NOT NULL DEFAULT 0,
            bj_losses      INTEGER NOT NULL DEFAULT 0,
            bj_pushes      INTEGER NOT NULL DEFAULT 0,
            bj_blackjacks  INTEGER NOT NULL DEFAULT 0,
            bj_total_bet   INTEGER NOT NULL DEFAULT 0,
            bj_total_won   INTEGER NOT NULL DEFAULT 0,
            bj_total_lost  INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bj_settings (
            id                  INTEGER PRIMARY KEY DEFAULT 1,
            min_bet             INTEGER NOT NULL DEFAULT 10,
            max_bet             INTEGER NOT NULL DEFAULT 1000,
            win_payout          REAL    NOT NULL DEFAULT 2.0,
            blackjack_payout    REAL    NOT NULL DEFAULT 2.5,
            push_rule           TEXT    NOT NULL DEFAULT 'refund',
            dealer_hits_soft_17 INTEGER NOT NULL DEFAULT 1,
            lobby_countdown     INTEGER NOT NULL DEFAULT 60,
            turn_timer          INTEGER NOT NULL DEFAULT 30,
            max_players         INTEGER NOT NULL DEFAULT 6
        )
    """)
    # Ensure the default settings row exists
    conn.execute("""
        INSERT OR IGNORE INTO bj_settings (id) VALUES (1)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rbj_stats (
            user_id          TEXT PRIMARY KEY,
            rbj_wins         INTEGER NOT NULL DEFAULT 0,
            rbj_losses       INTEGER NOT NULL DEFAULT 0,
            rbj_pushes       INTEGER NOT NULL DEFAULT 0,
            rbj_blackjacks   INTEGER NOT NULL DEFAULT 0,
            rbj_total_bet    INTEGER NOT NULL DEFAULT 0,
            rbj_total_won    INTEGER NOT NULL DEFAULT 0,
            rbj_total_lost   INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rbj_settings (
            id                   INTEGER PRIMARY KEY DEFAULT 1,
            decks                INTEGER NOT NULL DEFAULT 6,
            shuffle_used_percent INTEGER NOT NULL DEFAULT 75,
            min_bet              INTEGER NOT NULL DEFAULT 10,
            max_bet              INTEGER NOT NULL DEFAULT 1000,
            win_payout           REAL    NOT NULL DEFAULT 2.0,
            blackjack_payout     REAL    NOT NULL DEFAULT 2.5,
            push_rule            TEXT    NOT NULL DEFAULT 'refund',
            dealer_hits_soft_17  INTEGER NOT NULL DEFAULT 1,
            lobby_countdown      INTEGER NOT NULL DEFAULT 60,
            turn_timer           INTEGER NOT NULL DEFAULT 30,
            max_players          INTEGER NOT NULL DEFAULT 6
        )
    """)
    conn.execute("INSERT OR IGNORE INTO rbj_settings (id) VALUES (1)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS managers (
            username TEXT PRIMARY KEY
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS moderators (
            username TEXT PRIMARY KEY
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_users (
            username TEXT PRIMARY KEY
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS owner_users (
            username TEXT PRIMARY KEY
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bj_daily (
            user_id TEXT NOT NULL,
            date    TEXT NOT NULL,
            net     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, date)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rbj_daily (
            user_id TEXT NOT NULL,
            date    TEXT NOT NULL,
            net     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, date)
        )
    """)

    # ── Bank tables ──────────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bank_transactions (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp               TEXT NOT NULL DEFAULT (datetime('now')),
            sender_id               TEXT NOT NULL,
            sender_username         TEXT NOT NULL,
            receiver_id             TEXT NOT NULL,
            receiver_username       TEXT NOT NULL,
            amount_sent             INTEGER NOT NULL,
            fee                     INTEGER NOT NULL DEFAULT 0,
            amount_received         INTEGER NOT NULL,
            sender_balance_before   INTEGER NOT NULL DEFAULT 0,
            sender_balance_after    INTEGER NOT NULL DEFAULT 0,
            receiver_balance_before INTEGER NOT NULL DEFAULT 0,
            receiver_balance_after  INTEGER NOT NULL DEFAULT 0,
            risk_level              TEXT NOT NULL DEFAULT 'LOW',
            risk_reason             TEXT NOT NULL DEFAULT '',
            status                  TEXT NOT NULL DEFAULT 'completed'
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bank_user_stats (
            user_id                   TEXT PRIMARY KEY,
            total_sent                INTEGER NOT NULL DEFAULT 0,
            total_received            INTEGER NOT NULL DEFAULT 0,
            total_transfer_fees_paid  INTEGER NOT NULL DEFAULT 0,
            daily_sent                INTEGER NOT NULL DEFAULT 0,
            daily_sent_date           TEXT NOT NULL DEFAULT '',
            bank_blocked              INTEGER NOT NULL DEFAULT 0,
            suspicious_transfer_count INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bank_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    for k, v in [
        ("min_send_amount",              "10"),
        ("max_send_amount",              "1000"),
        ("daily_send_limit",             "3000"),
        ("new_account_days",             "3"),
        ("min_level_to_send",            "3"),
        ("min_total_earned_to_send",     "500"),
        ("min_daily_claim_days_to_send", "2"),
        ("send_tax_percent",             "5"),
        ("high_risk_blocks",             "true"),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO bank_settings (key, value) VALUES (?, ?)", (k, v)
        )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ledger (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT NOT NULL DEFAULT (datetime('now')),
            user_id       TEXT NOT NULL,
            username      TEXT NOT NULL,
            change_amount INTEGER NOT NULL,
            reason        TEXT NOT NULL,
            balance_before INTEGER NOT NULL DEFAULT 0,
            balance_after  INTEGER NOT NULL DEFAULT 0,
            related_user  TEXT NOT NULL DEFAULT '',
            metadata      TEXT NOT NULL DEFAULT ''
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS economy_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    for k, v in [
        ("daily_coins",     "50"),
        ("trivia_reward",   "20"),
        ("scramble_reward", "20"),
        ("riddle_reward",   "25"),
        ("max_balance",     "1000000"),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO economy_settings (key, value) VALUES (?, ?)", (k, v)
        )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS quest_progress (
            user_id    TEXT    NOT NULL,
            quest_id   TEXT    NOT NULL,
            period_key TEXT    NOT NULL,
            progress   INTEGER NOT NULL DEFAULT 0,
            claimed    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, quest_id, period_key)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_points (
            user_id TEXT    PRIMARY KEY,
            points  INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO event_settings (key, value) VALUES ('event_active', '0')"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS reputation (
            user_id           TEXT    PRIMARY KEY,
            username          TEXT    NOT NULL,
            rep_received      INTEGER NOT NULL DEFAULT 0,
            rep_given         INTEGER NOT NULL DEFAULT 0,
            last_rep_given_at TEXT    NOT NULL DEFAULT ''
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS reputation_logs (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp         TEXT    NOT NULL DEFAULT (datetime('now')),
            giver_id          TEXT    NOT NULL DEFAULT '',
            giver_username    TEXT    NOT NULL,
            receiver_username TEXT    NOT NULL,
            amount            INTEGER NOT NULL DEFAULT 1,
            reason            TEXT    NOT NULL DEFAULT '',
            risk_note         TEXT    NOT NULL DEFAULT ''
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mutes (
            user_id    TEXT    PRIMARY KEY,
            username   TEXT    NOT NULL,
            muted_by   TEXT    NOT NULL,
            muted_at   TEXT    NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT    NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS warnings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT    NOT NULL,
            username   TEXT    NOT NULL,
            warned_by  TEXT    NOT NULL,
            reason     TEXT    NOT NULL,
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp         TEXT    NOT NULL DEFAULT (datetime('now')),
            reporter_id       TEXT    NOT NULL,
            reporter_username TEXT    NOT NULL,
            target_username   TEXT    NOT NULL DEFAULT '',
            report_type       TEXT    NOT NULL,
            reason            TEXT    NOT NULL,
            status            TEXT    NOT NULL DEFAULT 'open',
            handled_by        TEXT    NOT NULL DEFAULT '',
            resolution_note   TEXT    NOT NULL DEFAULT ''
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS poker_stats (
            user_id      TEXT    PRIMARY KEY,
            username     TEXT    NOT NULL,
            hands_played INTEGER NOT NULL DEFAULT 0,
            wins         INTEGER NOT NULL DEFAULT 0,
            losses       INTEGER NOT NULL DEFAULT 0,
            folds        INTEGER NOT NULL DEFAULT 0,
            total_won    INTEGER NOT NULL DEFAULT 0,
            biggest_pot  INTEGER NOT NULL DEFAULT 0
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
        "ALTER TABLE daily_claims ADD COLUMN streak        INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE daily_claims ADD COLUMN total_claims  INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE bj_settings  ADD COLUMN bj_enabled     INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE rbj_settings ADD COLUMN rbj_enabled    INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE bj_settings  ADD COLUMN bj_turn_timer         INTEGER NOT NULL DEFAULT 20",
        "ALTER TABLE rbj_settings ADD COLUMN rbj_turn_timer        INTEGER NOT NULL DEFAULT 20",
        "ALTER TABLE bj_settings  ADD COLUMN bj_daily_win_limit    INTEGER NOT NULL DEFAULT 5000",
        "ALTER TABLE bj_settings  ADD COLUMN bj_daily_loss_limit   INTEGER NOT NULL DEFAULT 3000",
        "ALTER TABLE rbj_settings ADD COLUMN rbj_daily_win_limit   INTEGER NOT NULL DEFAULT 5000",
        "ALTER TABLE rbj_settings ADD COLUMN rbj_daily_loss_limit  INTEGER NOT NULL DEFAULT 3000",
        "ALTER TABLE users         ADD COLUMN first_seen TEXT",
        "ALTER TABLE daily_claims  ADD COLUMN last_claim_ts TEXT",
        "ALTER TABLE bank_user_stats ADD COLUMN bank_notify INTEGER NOT NULL DEFAULT 1",
    ]:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass

    # Data migrations — safe no-ops if already applied or no matching rows exist
    conn.execute("UPDATE users SET first_seen = datetime('now') WHERE first_seen IS NULL")
    conn.execute("UPDATE bj_settings  SET lobby_countdown = 15 WHERE id = 1 AND lobby_countdown = 60")
    conn.execute("UPDATE rbj_settings SET lobby_countdown = 15 WHERE id = 1 AND lobby_countdown = 60")
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

# Tables whose rows reference user_id and need re-pointing when an offline
# placeholder is merged into a real user record.
_PLACEHOLDER_TABLES = [
    ("bank_user_stats",   "user_id"),
    ("ledger",            "user_id"),
    ("game_wins",         "user_id"),
    ("daily_claims",      "user_id"),
    ("quest_progress",    "user_id"),
    ("coinflip_history",  "user_id"),
    ("bank_transactions", "sender_id"),
    ("bank_transactions", "receiver_id"),
    ("event_points",      "user_id"),
    ("achievements",      "user_id"),
    ("purchase_history",  "user_id"),
    ("warnings",          "user_id"),
    ("mutes",             "user_id"),
    ("reputation",        "user_id"),
    ("bj_stats",          "user_id"),
    ("rbj_stats",         "user_id"),
    ("bj_daily",          "user_id"),
    ("rbj_daily",         "user_id"),
]


def ensure_user(user_id: str, username: str):
    """
    Register or update a user.
    - If already present by user_id → refresh display name only.
    - If an offline placeholder exists for the same username → merge all data
      into the real record and delete the placeholder.
    - Otherwise → insert brand-new user row.
    """
    conn  = get_connection()
    clean = username.strip()

    # Already in DB by real user_id — just keep username fresh
    if conn.execute(
        "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
    ).fetchone():
        conn.execute(
            "UPDATE users SET username = ? WHERE user_id = ?", (clean, user_id)
        )
        conn.commit()
        conn.close()
        return

    # Merge offline placeholder if one was created before real join
    placeholder_id = f"offline_{clean.lower()}"
    placeholder = conn.execute(
        "SELECT balance FROM users WHERE user_id = ?", (placeholder_id,)
    ).fetchone()

    if placeholder:
        merged_bal = placeholder["balance"]
        for table, col in _PLACEHOLDER_TABLES:
            try:
                conn.execute(
                    f"UPDATE {table} SET {col} = ? WHERE {col} = ?",
                    (user_id, placeholder_id),
                )
            except Exception:
                pass
        conn.execute("DELETE FROM users WHERE user_id = ?", (placeholder_id,))
        conn.execute("""
            INSERT INTO users (user_id, username, balance, first_seen)
            VALUES (?, ?, ?, datetime('now'))
        """, (user_id, clean, merged_bal))
        conn.commit()
        conn.close()
        print(f"[DB] Merged offline placeholder @{clean} → real id {user_id}")
        return

    # Brand-new user
    conn.execute("""
        INSERT INTO users (user_id, username, balance, first_seen)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET username = excluded.username
    """, (user_id, clean, config.STARTING_BALANCE))
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


def resolve_or_create_user(username: str) -> dict | None:
    """
    Case-insensitive lookup by username (strips leading @, trims whitespace).

    - If found in DB → return existing record.
    - If not found → create a placeholder record with id 'offline_<username_lower>'
      and starting balance; the placeholder is automatically merged into the real
      record by ensure_user() when the player next joins the room.
    - Returns dict(user_id, username, balance) or None if username is blank.
    """
    clean = username.lstrip("@").strip()
    if not clean:
        return None

    conn = get_connection()
    row = conn.execute(
        "SELECT user_id, username, balance FROM users WHERE LOWER(username) = LOWER(?)",
        (clean,),
    ).fetchone()
    conn.close()
    if row:
        return dict(row)

    # Not in DB — create offline placeholder
    synthetic_id = f"offline_{clean.lower()}"
    conn = get_connection()
    conn.execute("""
        INSERT INTO users (user_id, username, balance, first_seen)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(user_id) DO NOTHING
    """, (synthetic_id, clean, config.STARTING_BALANCE))
    conn.commit()
    conn.close()
    ensure_bank_user(synthetic_id)
    print(f"[DB] Offline placeholder created: @{clean} (id={synthetic_id})")
    return {"user_id": synthetic_id, "username": clean, "balance": get_balance(synthetic_id)}


def add_ledger_entry(
    user_id: str,
    username: str,
    change_amount: int,
    reason: str,
    related_user: str | None = None,
    balance_before: int | None = None,
) -> None:
    """
    Write a single ledger row (for admin gifts, penalties, etc.).

    If balance_before is supplied it is used directly; otherwise the function
    reads the current balance and back-computes balance_before from it
    (call AFTER adjust_balance so the current value is already the after-state).
    """
    from datetime import datetime as _dt

    conn = get_connection()
    if balance_before is None:
        bal_row = conn.execute(
            "SELECT balance FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        current = bal_row["balance"] if bal_row else 0
        balance_before = current - change_amount
    balance_after = balance_before + change_amount
    now_ts = _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""
        INSERT INTO ledger
            (timestamp, user_id, username, change_amount, reason,
             balance_before, balance_after, related_user)
        VALUES (?,?,?,?,?,?,?,?)
    """, (now_ts, user_id, username, change_amount, reason,
          balance_before, balance_after, related_user))
    conn.commit()
    conn.close()


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
    from datetime import timedelta
    conn  = get_connection()
    today     = str(date.today())
    yesterday = str(date.today() - timedelta(days=1))

    row = conn.execute(
        "SELECT last_claim, streak, total_claims FROM daily_claims WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if row is None:
        streak = 1
        total  = 1
    else:
        old_streak = row["streak"] or 1
        old_total  = row["total_claims"] or 1
        streak = (old_streak + 1) if row["last_claim"] == yesterday else 1
        total  = old_total + 1

    now_ts = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""
        INSERT INTO daily_claims (user_id, last_claim, streak, total_claims, last_claim_ts)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE
          SET last_claim    = excluded.last_claim,
              streak        = excluded.streak,
              total_claims  = excluded.total_claims,
              last_claim_ts = excluded.last_claim_ts
    """, (user_id, today, streak, total, now_ts))
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
# Achievement helpers
# ---------------------------------------------------------------------------

def get_game_wins(user_id: str, game_type: str) -> int:
    """Return win count for a specific game type."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT wins FROM game_wins WHERE user_id = ? AND game_type = ?",
        (user_id, game_type)
    ).fetchone()
    conn.close()
    return row["wins"] if row else 0


def get_daily_stats(user_id: str) -> dict:
    """Return current daily streak and total claim count."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT streak, total_claims FROM daily_claims WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return {"streak": 0, "total_claims": 0}
    return {"streak": row["streak"] or 0, "total_claims": row["total_claims"] or 0}


def get_owned_item_counts(user_id: str) -> dict:
    """Return total, badge, and title counts of owned shop items."""
    conn   = get_connection()
    total  = conn.execute(
        "SELECT COUNT(*) as c FROM owned_items WHERE user_id = ?", (user_id,)
    ).fetchone()["c"]
    badges = conn.execute(
        "SELECT COUNT(*) as c FROM owned_items WHERE user_id = ? AND item_type = 'badge'",
        (user_id,)
    ).fetchone()["c"]
    titles = conn.execute(
        "SELECT COUNT(*) as c FROM owned_items WHERE user_id = ? AND item_type = 'title'",
        (user_id,)
    ).fetchone()["c"]
    conn.close()
    return {"total": total, "badges": badges, "titles": titles}


def unlock_achievement(user_id: str, achievement_id: str) -> bool:
    """Record a newly unlocked achievement. Returns True if it was new."""
    conn = get_connection()
    existing = conn.execute(
        "SELECT 1 FROM achievements WHERE user_id = ? AND achievement_id = ?",
        (user_id, achievement_id)
    ).fetchone()
    if existing:
        conn.close()
        return False
    conn.execute(
        "INSERT INTO achievements (user_id, achievement_id) VALUES (?, ?)",
        (user_id, achievement_id)
    )
    conn.commit()
    conn.close()
    return True


def get_unlocked_achievements(user_id: str) -> list[str]:
    """Return all achievement IDs the player has unlocked (claimed or not)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT achievement_id FROM achievements WHERE user_id = ?", (user_id,)
    ).fetchall()
    conn.close()
    return [r["achievement_id"] for r in rows]


def get_claimable_achievements(user_id: str) -> list[str]:
    """Return unlocked-but-unclaimed achievement IDs."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT achievement_id FROM achievements WHERE user_id = ? AND claimed = 0",
        (user_id,)
    ).fetchall()
    conn.close()
    return [r["achievement_id"] for r in rows]


def claim_achievement(user_id: str, achievement_id: str) -> bool:
    """Mark an achievement as claimed. Returns True if a row was updated."""
    conn   = get_connection()
    before = conn.total_changes
    conn.execute(
        "UPDATE achievements SET claimed = 1 "
        "WHERE user_id = ? AND achievement_id = ? AND claimed = 0",
        (user_id, achievement_id)
    )
    changed = conn.total_changes - before
    conn.commit()
    conn.close()
    return changed > 0


# ---------------------------------------------------------------------------
# Blackjack helpers
# ---------------------------------------------------------------------------

def get_bj_settings() -> dict:
    """Return the single bj_settings row as a plain dict."""
    conn = get_connection()
    row  = conn.execute("SELECT * FROM bj_settings WHERE id = 1").fetchone()
    conn.close()
    if row is None:
        return {
            "min_bet": 10, "max_bet": 1000,
            "win_payout": 2.0, "blackjack_payout": 2.5,
            "push_rule": "refund", "dealer_hits_soft_17": 1,
            "lobby_countdown": 15, "turn_timer": 30, "bj_turn_timer": 20,
            "max_players": 6, "bj_enabled": 1,
            "bj_daily_win_limit": 5000, "bj_daily_loss_limit": 3000,
        }
    return dict(row)


_BJ_SETTING_COLS = {
    "min_bet", "max_bet", "win_payout", "blackjack_payout", "push_rule",
    "dealer_hits_soft_17", "lobby_countdown", "turn_timer", "bj_turn_timer",
    "max_players", "bj_enabled",
    "bj_daily_win_limit", "bj_daily_loss_limit",
}


def set_bj_setting(key: str, value) -> bool:
    """Update a single BJ setting by column name. Returns False for invalid keys."""
    if key not in _BJ_SETTING_COLS:
        return False
    conn = get_connection()
    conn.execute(f"UPDATE bj_settings SET {key} = ? WHERE id = 1", (value,))
    conn.commit()
    conn.close()
    return True


def get_bj_stats(user_id: str) -> dict:
    """Return a player's blackjack stats row, creating it if needed."""
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO bj_stats (user_id) VALUES (?)", (user_id,)
    )
    row = conn.execute(
        "SELECT * FROM bj_stats WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.commit()
    conn.close()
    return dict(row) if row else {
        "bj_wins": 0, "bj_losses": 0, "bj_pushes": 0, "bj_blackjacks": 0,
        "bj_total_bet": 0, "bj_total_won": 0, "bj_total_lost": 0,
    }


def update_bj_stats(
    user_id: str,
    *,
    win:  int = 0,
    loss: int = 0,
    push: int = 0,
    bj:   int = 0,
    bet:  int = 0,
    won:  int = 0,
    lost: int = 0,
):
    """Increment a player's blackjack stats by the given deltas."""
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO bj_stats (user_id) VALUES (?)", (user_id,)
    )
    conn.execute("""
        UPDATE bj_stats
        SET bj_wins       = bj_wins       + ?,
            bj_losses     = bj_losses     + ?,
            bj_pushes     = bj_pushes     + ?,
            bj_blackjacks = bj_blackjacks + ?,
            bj_total_bet  = bj_total_bet  + ?,
            bj_total_won  = bj_total_won  + ?,
            bj_total_lost = bj_total_lost + ?
        WHERE user_id = ?
    """, (win, loss, push, bj, bet, won, lost, user_id))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Realistic Blackjack helpers
# ---------------------------------------------------------------------------

_RBJ_SETTING_COLS = {
    "decks", "shuffle_used_percent", "min_bet", "max_bet",
    "win_payout", "blackjack_payout", "push_rule",
    "dealer_hits_soft_17", "lobby_countdown", "turn_timer", "rbj_turn_timer",
    "max_players", "rbj_enabled",
    "rbj_daily_win_limit", "rbj_daily_loss_limit",
}


def get_rbj_settings() -> dict:
    """Return the single rbj_settings row as a plain dict."""
    conn = get_connection()
    row  = conn.execute("SELECT * FROM rbj_settings WHERE id = 1").fetchone()
    conn.close()
    if row is None:
        return {
            "decks": 6, "shuffle_used_percent": 75,
            "min_bet": 10, "max_bet": 1000,
            "win_payout": 2.0, "blackjack_payout": 2.5,
            "push_rule": "refund", "dealer_hits_soft_17": 1,
            "lobby_countdown": 15, "turn_timer": 30, "rbj_turn_timer": 20,
            "max_players": 6, "rbj_enabled": 1,
            "rbj_daily_win_limit": 5000, "rbj_daily_loss_limit": 3000,
        }
    return dict(row)


def set_rbj_setting(key: str, value) -> bool:
    """Update a single RBJ setting by column name. Returns False for invalid keys."""
    if key not in _RBJ_SETTING_COLS:
        return False
    conn = get_connection()
    conn.execute(f"UPDATE rbj_settings SET {key} = ? WHERE id = 1", (value,))
    conn.commit()
    conn.close()
    return True


# ---------------------------------------------------------------------------
# Daily profit/loss tracking
# ---------------------------------------------------------------------------

def _today() -> str:
    """Return today's UTC date as ISO string YYYY-MM-DD."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_bj_daily_net(user_id: str) -> int:
    """Return today's BJ net coins for a player (+win / -loss)."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT net FROM bj_daily WHERE user_id = ? AND date = ?",
        (user_id, _today())
    ).fetchone()
    conn.close()
    return row["net"] if row else 0


def add_bj_daily_net(user_id: str, delta: int):
    """Add delta to today's BJ net for a player (atomic upsert)."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO bj_daily (user_id, date, net) VALUES (?, ?, ?)"
        " ON CONFLICT(user_id, date) DO UPDATE SET net = net + excluded.net",
        (user_id, _today(), delta)
    )
    conn.commit()
    conn.close()


def get_rbj_daily_net(user_id: str) -> int:
    """Return today's RBJ net coins for a player (+win / -loss)."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT net FROM rbj_daily WHERE user_id = ? AND date = ?",
        (user_id, _today())
    ).fetchone()
    conn.close()
    return row["net"] if row else 0


def add_rbj_daily_net(user_id: str, delta: int):
    """Add delta to today's RBJ net for a player (atomic upsert)."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO rbj_daily (user_id, date, net) VALUES (?, ?, ?)"
        " ON CONFLICT(user_id, date) DO UPDATE SET net = net + excluded.net",
        (user_id, _today(), delta)
    )
    conn.commit()
    conn.close()


def get_bj_leaderboard(limit: int = 5) -> list:
    """Top players by BJ net profit (total_won - total_bet)."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT s.user_id, u.username,
               s.bj_total_bet  AS total_bet,
               s.bj_total_won  AS total_won,
               s.bj_total_lost AS total_lost,
               (s.bj_total_won - s.bj_total_bet) AS net
        FROM bj_stats s
        JOIN users u ON s.user_id = u.user_id
        WHERE s.bj_total_bet > 0
        ORDER BY net DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_rbj_leaderboard(limit: int = 5) -> list:
    """Top players by RBJ net profit (total_won - total_bet)."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT s.user_id, u.username,
               s.rbj_total_bet  AS total_bet,
               s.rbj_total_won  AS total_won,
               s.rbj_total_lost AS total_lost,
               (s.rbj_total_won - s.rbj_total_bet) AS net
        FROM rbj_stats s
        JOIN users u ON s.user_id = u.user_id
        WHERE s.rbj_total_bet > 0
        ORDER BY net DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_rbj_stats(user_id: str) -> dict:
    """Return a player's realistic blackjack stats row, creating it if needed."""
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO rbj_stats (user_id) VALUES (?)", (user_id,)
    )
    row = conn.execute(
        "SELECT * FROM rbj_stats WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.commit()
    conn.close()
    return dict(row) if row else {
        "rbj_wins": 0, "rbj_losses": 0, "rbj_pushes": 0, "rbj_blackjacks": 0,
        "rbj_total_bet": 0, "rbj_total_won": 0, "rbj_total_lost": 0,
    }


def update_rbj_stats(
    user_id: str,
    *,
    win:  int = 0,
    loss: int = 0,
    push: int = 0,
    bj:   int = 0,
    bet:  int = 0,
    won:  int = 0,
    lost: int = 0,
):
    """Increment a player's realistic blackjack stats by the given deltas."""
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO rbj_stats (user_id) VALUES (?)", (user_id,)
    )
    conn.execute("""
        UPDATE rbj_stats
        SET rbj_wins       = rbj_wins       + ?,
            rbj_losses     = rbj_losses     + ?,
            rbj_pushes     = rbj_pushes     + ?,
            rbj_blackjacks = rbj_blackjacks + ?,
            rbj_total_bet  = rbj_total_bet  + ?,
            rbj_total_won  = rbj_total_won  + ?,
            rbj_total_lost = rbj_total_lost + ?
        WHERE user_id = ?
    """, (win, loss, push, bj, bet, won, lost, user_id))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Manager role helpers
# ---------------------------------------------------------------------------

def is_manager_db(username: str) -> bool:
    """Return True if username is stored in the managers table."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT 1 FROM managers WHERE username = ?", (username.lower(),)
    ).fetchone()
    conn.close()
    return row is not None


def add_manager(username: str) -> str:
    """Add a manager. Returns 'exists' or 'added'."""
    conn = get_connection()
    if conn.execute(
        "SELECT 1 FROM managers WHERE username = ?", (username.lower(),)
    ).fetchone():
        conn.close()
        return "exists"
    conn.execute("INSERT INTO managers (username) VALUES (?)", (username.lower(),))
    conn.commit()
    conn.close()
    return "added"


def remove_manager(username: str) -> str:
    """Remove a manager. Returns 'not_found' or 'removed'."""
    conn = get_connection()
    if not conn.execute(
        "SELECT 1 FROM managers WHERE username = ?", (username.lower(),)
    ).fetchone():
        conn.close()
        return "not_found"
    conn.execute("DELETE FROM managers WHERE username = ?", (username.lower(),))
    conn.commit()
    conn.close()
    return "removed"


def get_managers() -> list:
    """Return all manager usernames sorted alphabetically."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT username FROM managers ORDER BY username"
    ).fetchall()
    conn.close()
    return [r["username"] for r in rows]


# ---------------------------------------------------------------------------
# Moderator helpers
# ---------------------------------------------------------------------------

def is_moderator_db(username: str) -> bool:
    conn = get_connection()
    row  = conn.execute(
        "SELECT 1 FROM moderators WHERE username = ?", (username.lower(),)
    ).fetchone()
    conn.close()
    return row is not None


def add_moderator(username: str) -> str:
    conn = get_connection()
    if conn.execute(
        "SELECT 1 FROM moderators WHERE username = ?", (username.lower(),)
    ).fetchone():
        conn.close()
        return "exists"
    conn.execute("INSERT INTO moderators (username) VALUES (?)", (username.lower(),))
    conn.commit()
    conn.close()
    return "added"


def remove_moderator(username: str) -> str:
    conn = get_connection()
    if not conn.execute(
        "SELECT 1 FROM moderators WHERE username = ?", (username.lower(),)
    ).fetchone():
        conn.close()
        return "not_found"
    conn.execute("DELETE FROM moderators WHERE username = ?", (username.lower(),))
    conn.commit()
    conn.close()
    return "removed"


def get_moderators() -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT username FROM moderators ORDER BY username"
    ).fetchall()
    conn.close()
    return [r["username"] for r in rows]


# ---------------------------------------------------------------------------
# Dynamic admin helpers
# ---------------------------------------------------------------------------

def is_admin_db(username: str) -> bool:
    conn = get_connection()
    row  = conn.execute(
        "SELECT 1 FROM admin_users WHERE username = ?", (username.lower(),)
    ).fetchone()
    conn.close()
    return row is not None


def add_admin_user(username: str) -> str:
    conn = get_connection()
    if conn.execute(
        "SELECT 1 FROM admin_users WHERE username = ?", (username.lower(),)
    ).fetchone():
        conn.close()
        return "exists"
    conn.execute("INSERT INTO admin_users (username) VALUES (?)", (username.lower(),))
    conn.commit()
    conn.close()
    return "added"


def remove_admin_user(username: str) -> str:
    conn = get_connection()
    if not conn.execute(
        "SELECT 1 FROM admin_users WHERE username = ?", (username.lower(),)
    ).fetchone():
        conn.close()
        return "not_found"
    conn.execute("DELETE FROM admin_users WHERE username = ?", (username.lower(),))
    conn.commit()
    conn.close()
    return "removed"


def get_admin_users() -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT username FROM admin_users ORDER BY username"
    ).fetchall()
    conn.close()
    return [r["username"] for r in rows]


# ---------------------------------------------------------------------------
# Dynamic owner helpers
# ---------------------------------------------------------------------------

def is_owner_db(username: str) -> bool:
    conn = get_connection()
    row  = conn.execute(
        "SELECT 1 FROM owner_users WHERE username = ?", (username.lower(),)
    ).fetchone()
    conn.close()
    return row is not None


def add_owner_user(username: str) -> str:
    """Add an owner. Returns 'exists' or 'added'."""
    conn = get_connection()
    if conn.execute(
        "SELECT 1 FROM owner_users WHERE username = ?", (username.lower(),)
    ).fetchone():
        conn.close()
        return "exists"
    conn.execute("INSERT INTO owner_users (username) VALUES (?)", (username.lower(),))
    conn.commit()
    conn.close()
    return "added"


def remove_owner_user(username: str) -> str:
    """Remove an owner. Returns 'not_found', 'last_owner', or 'removed'."""
    import config as _cfg
    conn = get_connection()
    if not conn.execute(
        "SELECT 1 FROM owner_users WHERE username = ?", (username.lower(),)
    ).fetchone():
        conn.close()
        return "not_found"
    db_owners   = [r["username"] for r in conn.execute(
        "SELECT username FROM owner_users"
    ).fetchall()]
    cfg_owners  = [u.lower() for u in _cfg.OWNER_USERS]
    all_owners  = set(db_owners) | set(cfg_owners)
    if len(all_owners) <= 1:
        conn.close()
        return "last_owner"
    conn.execute("DELETE FROM owner_users WHERE username = ?", (username.lower(),))
    conn.commit()
    conn.close()
    return "removed"


def get_owner_users() -> list:
    """Return all DB-stored owner usernames sorted alphabetically."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT username FROM owner_users ORDER BY username"
    ).fetchall()
    conn.close()
    return [r["username"] for r in rows]


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


# ---------------------------------------------------------------------------
# Bank helpers
# ---------------------------------------------------------------------------

def get_bank_settings() -> dict:
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM bank_settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def get_bank_setting(key: str, default: str = "0") -> str:
    """Return a single bank_settings value by key."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT value FROM bank_settings WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    return row["value"] if row else default


def set_bank_setting(key: str, value: str):
    conn = get_connection()
    conn.execute(
        "INSERT INTO bank_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value)
    )
    conn.commit()
    conn.close()


def ensure_bank_user(user_id: str):
    """Create bank_user_stats row if it doesn't exist."""
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO bank_user_stats (user_id) VALUES (?)", (user_id,)
    )
    conn.commit()
    conn.close()


def get_bank_user_stats(user_id: str) -> dict:
    ensure_bank_user(user_id)
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM bank_user_stats WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def get_daily_sent_today(user_id: str) -> int:
    """Return how many coins the user has sent today (resets at UTC midnight)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT daily_sent, daily_sent_date FROM bank_user_stats WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return 0
    today = str(date.today())
    if row["daily_sent_date"] != today:
        return 0
    return row["daily_sent"] or 0


def check_send_eligibility(user_id: str, settings: dict) -> dict:
    """Return {'eligible': bool, 'reason': str}."""
    from datetime import datetime as _dt
    conn = get_connection()

    # 1. Account age
    new_account_days = int(settings.get("new_account_days", 3))
    u_row = conn.execute(
        "SELECT first_seen, level, total_coins_earned FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    if u_row and u_row["first_seen"]:
        try:
            age_days = (_dt.utcnow() - _dt.fromisoformat(u_row["first_seen"])).days
            if age_days < new_account_days:
                conn.close()
                return {"eligible": False,
                        "reason": f"Account must be {new_account_days}d old to send."}
        except Exception:
            pass

    # 2. Level
    min_level = int(settings.get("min_level_to_send", 3))
    if u_row and (u_row["level"] or 1) < min_level:
        conn.close()
        return {"eligible": False, "reason": f"Need Level {min_level} to send coins."}

    # 3. Total earned
    min_earned = int(settings.get("min_total_earned_to_send", 500))
    if u_row and (u_row["total_coins_earned"] or 0) < min_earned:
        conn.close()
        return {"eligible": False,
                "reason": f"Must earn {min_earned}c from games first."}

    # 4. Daily claim count
    min_claims = int(settings.get("min_daily_claim_days_to_send", 2))
    dc_row = conn.execute(
        "SELECT total_claims FROM daily_claims WHERE user_id = ?", (user_id,)
    ).fetchone()
    claims = (dc_row["total_claims"] or 0) if dc_row else 0
    if claims < min_claims:
        conn.close()
        return {"eligible": False,
                "reason": f"Claim /daily {min_claims}x first to send coins."}

    # 5. Game activity (5 game wins OR 5 casino/coinflip rounds)
    gw_row = conn.execute(
        "SELECT total_games_won FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    game_wins = (gw_row["total_games_won"] or 0) if gw_row else 0
    bj_row = conn.execute(
        "SELECT COALESCE(bj_wins+bj_losses+bj_pushes, 0) AS r FROM bj_stats WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    rbj_row = conn.execute(
        "SELECT COALESCE(rbj_wins+rbj_losses+rbj_pushes, 0) AS r FROM rbj_stats WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    cf_row = conn.execute(
        "SELECT COUNT(*) AS c FROM coinflip_history WHERE user_id = ?", (user_id,)
    ).fetchone()
    casino = ((bj_row["r"] if bj_row else 0)
              + (rbj_row["r"] if rbj_row else 0)
              + (cf_row["c"] if cf_row else 0))
    if game_wins < 5 and casino < 5:
        conn.close()
        return {"eligible": False,
                "reason": "Win 5 games or play 5 casino rounds to send."}

    conn.close()
    return {"eligible": True, "reason": ""}


def get_last_daily_claim_ts(user_id: str) -> str | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT last_claim_ts FROM daily_claims WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return row["last_claim_ts"] if row else None


def get_recent_sends_count_to(sender_id: str, receiver_id: str, hours: int = 24) -> int:
    conn = get_connection()
    row = conn.execute("""
        SELECT COUNT(*) AS c FROM bank_transactions
        WHERE sender_id = ? AND receiver_id = ?
          AND status = 'completed'
          AND timestamp >= datetime('now', ?)
    """, (sender_id, receiver_id, f"-{hours} hours")).fetchone()
    conn.close()
    return row["c"] if row else 0


def count_low_level_senders_to(receiver_id: str, hours: int = 24) -> int:
    """Count distinct senders to receiver in last N hours who are level < 3."""
    conn = get_connection()
    row = conn.execute("""
        SELECT COUNT(DISTINCT bt.sender_id) AS c
        FROM bank_transactions bt
        JOIN users u ON bt.sender_id = u.user_id
        WHERE bt.receiver_id = ?
          AND bt.status = 'completed'
          AND bt.timestamp >= datetime('now', ?)
          AND (u.level IS NULL OR u.level < 3)
    """, (receiver_id, f"-{hours} hours")).fetchone()
    conn.close()
    return row["c"] if row else 0


def get_recent_received_amount(user_id: str, hours: int = 24) -> int:
    """Total coins received by user in the last N hours."""
    conn = get_connection()
    row = conn.execute("""
        SELECT COALESCE(SUM(amount_received), 0) AS total
        FROM bank_transactions
        WHERE receiver_id = ? AND status = 'completed'
          AND timestamp >= datetime('now', ?)
    """, (user_id, f"-{hours} hours")).fetchone()
    conn.close()
    return row["total"] if row else 0


def do_bank_transfer(sender_id: str, sender_username: str,
                     receiver_id: str, receiver_username: str,
                     amount_sent: int, fee: int,
                     risk_level: str, risk_reason: str) -> dict:
    """Atomic coin transfer. Returns dict with 'success' bool and details."""
    from datetime import datetime as _dt
    amount_received = amount_sent - fee
    conn = get_connection()
    try:
        sb_row = conn.execute(
            "SELECT balance FROM users WHERE user_id = ?", (sender_id,)
        ).fetchone()
        if sb_row is None or sb_row["balance"] < amount_sent:
            conn.close()
            return {"success": False, "reason": "insufficient_funds"}
        sb_before = sb_row["balance"]

        rb_row = conn.execute(
            "SELECT balance FROM users WHERE user_id = ?", (receiver_id,)
        ).fetchone()
        rb_before = rb_row["balance"] if rb_row else 0

        # Deduct sender
        conn.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
            (amount_sent, sender_id, amount_sent)
        )
        if conn.execute(
            "SELECT changes() AS c"
        ).fetchone()["c"] == 0:
            conn.rollback()
            conn.close()
            return {"success": False, "reason": "insufficient_funds"}

        # Credit receiver
        conn.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (amount_received, receiver_id)
        )

        sb_after = conn.execute(
            "SELECT balance FROM users WHERE user_id = ?", (sender_id,)
        ).fetchone()["balance"]
        rb_after = conn.execute(
            "SELECT balance FROM users WHERE user_id = ?", (receiver_id,)
        ).fetchone()["balance"]

        now_ts = _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        today  = str(date.today())

        conn.execute("""
            INSERT INTO bank_transactions
              (timestamp, sender_id, sender_username, receiver_id, receiver_username,
               amount_sent, fee, amount_received,
               sender_balance_before, sender_balance_after,
               receiver_balance_before, receiver_balance_after,
               risk_level, risk_reason, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'completed')
        """, (now_ts, sender_id, sender_username, receiver_id, receiver_username,
              amount_sent, fee, amount_received,
              sb_before, sb_after, rb_before, rb_after,
              risk_level, risk_reason))

        # Sender bank stats
        conn.execute("""
            INSERT INTO bank_user_stats (user_id, total_sent, total_transfer_fees_paid,
                                         daily_sent, daily_sent_date)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              total_sent               = total_sent + ?,
              total_transfer_fees_paid = total_transfer_fees_paid + ?,
              daily_sent               = CASE WHEN daily_sent_date = ?
                                              THEN daily_sent + ? ELSE ? END,
              daily_sent_date          = ?
        """, (sender_id, amount_sent, fee, amount_sent, today,
              amount_sent, fee, today, amount_sent, amount_sent, today))

        # Receiver bank stats
        conn.execute("""
            INSERT INTO bank_user_stats (user_id, total_received) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET total_received = total_received + ?
        """, (receiver_id, amount_received, amount_received))

        # Ledger entries
        conn.execute("""
            INSERT INTO ledger (timestamp, user_id, username, change_amount, reason,
                                balance_before, balance_after, related_user)
            VALUES (?,?,?,?,?,?,?,?)
        """, (now_ts, sender_id, sender_username, -amount_sent, "bank_send",
              sb_before, sb_after, receiver_username))
        conn.execute("""
            INSERT INTO ledger (timestamp, user_id, username, change_amount, reason,
                                balance_before, balance_after, related_user)
            VALUES (?,?,?,?,?,?,?,?)
        """, (now_ts, receiver_id, receiver_username, amount_received, "bank_receive",
              rb_before, rb_after, sender_username))

        conn.commit()
        conn.close()
        return {
            "success":         True,
            "amount_sent":     amount_sent,
            "fee":             fee,
            "amount_received": amount_received,
        }
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()
        print(f"[BANK] Transfer error: {exc}")
        return {"success": False, "reason": "error"}


def record_blocked_transaction(sender_id: str, sender_username: str,
                                receiver_id: str, receiver_username: str,
                                amount_sent: int, risk_level: str, risk_reason: str):
    from datetime import datetime as _dt
    conn = get_connection()
    sb = conn.execute(
        "SELECT balance FROM users WHERE user_id = ?", (sender_id,)
    ).fetchone()
    sb_before = sb["balance"] if sb else 0
    conn.execute("""
        INSERT INTO bank_transactions
          (sender_id, sender_username, receiver_id, receiver_username,
           amount_sent, fee, amount_received,
           sender_balance_before, risk_level, risk_reason, status)
        VALUES (?,?,?,?,?,0,0,?,?,?,'blocked')
    """, (sender_id, sender_username, receiver_id, receiver_username,
          amount_sent, sb_before, risk_level, risk_reason))
    conn.commit()
    conn.close()


def increment_suspicious_count(user_id: str):
    ensure_bank_user(user_id)
    conn = get_connection()
    conn.execute("""
        UPDATE bank_user_stats
        SET suspicious_transfer_count = suspicious_transfer_count + 1
        WHERE user_id = ?
    """, (user_id,))
    conn.commit()
    conn.close()


def set_bank_notify(user_id: str, enabled: bool):
    ensure_bank_user(user_id)
    conn = get_connection()
    conn.execute(
        "UPDATE bank_user_stats SET bank_notify = ? WHERE user_id = ?",
        (int(enabled), user_id)
    )
    conn.commit()
    conn.close()


def set_bank_blocked(user_id: str, blocked: bool):
    ensure_bank_user(user_id)
    conn = get_connection()
    conn.execute(
        "UPDATE bank_user_stats SET bank_blocked = ? WHERE user_id = ?",
        (int(blocked), user_id)
    )
    conn.commit()
    conn.close()


def get_transactions_for(user_id: str, direction: str | None = None,
                         page: int = 1, limit: int = 5) -> list:
    """Return paginated transactions for user_id. direction: None/sent/received."""
    offset = (page - 1) * limit
    conn   = get_connection()
    if direction == "sent":
        clause = "WHERE sender_id = ?"
        params = (user_id, limit, offset)
    elif direction == "received":
        clause = "WHERE receiver_id = ?"
        params = (user_id, limit, offset)
    else:
        clause = "WHERE sender_id = ? OR receiver_id = ?"
        params = (user_id, user_id, limit, offset)
    rows = conn.execute(f"""
        SELECT * FROM bank_transactions {clause}
        ORDER BY id DESC LIMIT ? OFFSET ?
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_bank_watch_info(username: str) -> dict | None:
    """Full bank-watch details for a given username."""
    conn = get_connection()
    u = conn.execute(
        "SELECT user_id, username, balance, level, total_coins_earned, first_seen "
        "FROM users WHERE LOWER(username) = LOWER(?)", (username,)
    ).fetchone()
    if u is None:
        conn.close()
        return None
    uid = u["user_id"]
    bus = conn.execute(
        "SELECT * FROM bank_user_stats WHERE user_id = ?", (uid,)
    ).fetchone()
    daily_sent = 0
    if bus:
        today = str(date.today())
        daily_sent = bus["daily_sent"] if bus["daily_sent_date"] == today else 0
    ds_row = conn.execute(
        "SELECT total_claims FROM daily_claims WHERE user_id = ?", (uid,)
    ).fetchone()
    conn.close()
    return {
        "user_id":       uid,
        "username":      u["username"],
        "balance":       u["balance"] or 0,
        "level":         u["level"] or 1,
        "total_earned":  u["total_coins_earned"] or 0,
        "first_seen":    (u["first_seen"] or "unknown")[:10],
        "total_sent":    bus["total_sent"] if bus else 0,
        "total_received":bus["total_received"] if bus else 0,
        "daily_sent":    daily_sent,
        "bank_blocked":  bool(bus["bank_blocked"]) if bus else False,
        "suspicious_count": bus["suspicious_transfer_count"] if bus else 0,
        "total_claims":  (ds_row["total_claims"] or 0) if ds_row else 0,
    }


# ---------------------------------------------------------------------------
# Quest progress helpers
# ---------------------------------------------------------------------------

def get_quest_progress(user_id: str, quest_id: str, period_key: str) -> int:
    conn = get_connection()
    row  = conn.execute(
        "SELECT progress FROM quest_progress "
        "WHERE user_id = ? AND quest_id = ? AND period_key = ?",
        (user_id, quest_id, period_key)
    ).fetchone()
    conn.close()
    return row["progress"] if row else 0


def increment_quest_progress(user_id: str, quest_id: str, period_key: str, amount: int = 1):
    conn = get_connection()
    conn.execute("""
        INSERT INTO quest_progress (user_id, quest_id, period_key, progress)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, quest_id, period_key)
        DO UPDATE SET progress = progress + excluded.progress
    """, (user_id, quest_id, period_key, amount))
    conn.commit()
    conn.close()


def is_quest_claimed(user_id: str, quest_id: str, period_key: str) -> bool:
    conn = get_connection()
    row  = conn.execute(
        "SELECT claimed FROM quest_progress "
        "WHERE user_id = ? AND quest_id = ? AND period_key = ?",
        (user_id, quest_id, period_key)
    ).fetchone()
    conn.close()
    return bool(row["claimed"]) if row else False


def mark_quest_claimed(user_id: str, quest_id: str, period_key: str):
    conn = get_connection()
    conn.execute("""
        INSERT INTO quest_progress (user_id, quest_id, period_key, claimed)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(user_id, quest_id, period_key)
        DO UPDATE SET claimed = 1
    """, (user_id, quest_id, period_key))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Event system helpers
# ---------------------------------------------------------------------------

def get_event_points(user_id: str) -> int:
    conn = get_connection()
    row  = conn.execute(
        "SELECT points FROM event_points WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return row["points"] if row else 0


def add_event_points(user_id: str, amount: int) -> None:
    conn = get_connection()
    conn.execute("""
        INSERT INTO event_points (user_id, points) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET points = MAX(0, points + excluded.points)
    """, (user_id, amount))
    conn.commit()
    conn.close()


def is_event_active() -> bool:
    conn = get_connection()
    row  = conn.execute(
        "SELECT value FROM event_settings WHERE key = 'event_active'"
    ).fetchone()
    conn.close()
    return row is not None and row["value"] == "1"


def set_event_active(active: bool) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO event_settings (key, value) VALUES ('event_active', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        ("1" if active else "0",)
    )
    conn.commit()
    conn.close()


def set_active_event(event_id: str, expires_at: str) -> None:
    """Start a named event: store event_id, expiry, and mark active."""
    conn = get_connection()
    for key, val in [
        ("event_active",     "1"),
        ("event_name",       event_id),
        ("event_expires_at", expires_at),
    ]:
        conn.execute(
            "INSERT INTO event_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, val),
        )
    conn.commit()
    conn.close()


def get_active_event() -> dict | None:
    """
    Return {"event_id": str, "expires_at": str} if an event is active,
    otherwise None.
    """
    conn = get_connection()
    rows = {
        r["key"]: r["value"]
        for r in conn.execute("SELECT key, value FROM event_settings").fetchall()
    }
    conn.close()
    if rows.get("event_active") != "1":
        return None
    event_id   = rows.get("event_name", "")
    expires_at = rows.get("event_expires_at", "")
    if not event_id or not expires_at:
        return None
    return {"event_id": event_id, "expires_at": expires_at}


def clear_active_event() -> None:
    """Stop the active event (mark inactive, keep name/expiry rows)."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO event_settings (key, value) VALUES ('event_active', '0') "
        "ON CONFLICT(key) DO UPDATE SET value = '0'"
    )
    conn.commit()
    conn.close()


def buy_event_item(user_id: str, username: str,
                   item_id: str, item_type: str, cost: int) -> str:
    """
    Atomically spend event points and record ownership.
    Returns: "ok" | "no_points" | "duplicate" | "error"
    """
    conn = get_connection()
    try:
        dup = conn.execute(
            "SELECT 1 FROM owned_items WHERE user_id = ? AND item_id = ?",
            (user_id, item_id)
        ).fetchone()
        if dup:
            return "duplicate"

        row     = conn.execute(
            "SELECT points FROM event_points WHERE user_id = ?", (user_id,)
        ).fetchone()
        current = row["points"] if row else 0
        if current < cost:
            return "no_points"

        conn.execute(
            "UPDATE event_points SET points = points - ? WHERE user_id = ?",
            (cost, user_id)
        )
        conn.execute(
            "INSERT OR IGNORE INTO owned_items (user_id, item_id, item_type) VALUES (?, ?, ?)",
            (user_id, item_id, item_type)
        )
        conn.commit()
        return "ok"
    except Exception:
        conn.rollback()
        return "error"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reputation helpers
# ---------------------------------------------------------------------------

def ensure_reputation(user_id: str, username: str) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO reputation (user_id, username) VALUES (?, ?)",
        (user_id, username),
    )
    conn.commit()
    conn.close()


def get_reputation(user_id: str) -> dict | None:
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM reputation WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_reputation_by_username(username: str) -> dict | None:
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM reputation WHERE LOWER(username) = ?", (username.lower(),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_rep_cooldown_remaining(user_id: str) -> int | None:
    """Return remaining seconds of the 24 h give-rep cooldown, or None if ready."""
    from datetime import datetime, timezone as _tz2
    conn = get_connection()
    row  = conn.execute(
        "SELECT last_rep_given_at FROM reputation WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if row is None or not row[0]:
        return None
    last      = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_tz2.utc)
    elapsed   = (datetime.now(_tz2.utc) - last).total_seconds()
    remaining = 86400 - elapsed
    return max(1, int(remaining)) if remaining > 0 else None


def give_rep(giver_id: str, giver_username: str,
             receiver_id: str, receiver_username: str,
             risk_note: str = "") -> None:
    """Give +1 rep from giver to receiver and update both records + log."""
    from datetime import datetime, timezone as _tz2
    now_str = datetime.now(_tz2.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn    = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO reputation (user_id, username) VALUES (?, ?)",
        (giver_id, giver_username),
    )
    conn.execute(
        "INSERT OR IGNORE INTO reputation (user_id, username) VALUES (?, ?)",
        (receiver_id, receiver_username),
    )
    conn.execute(
        "UPDATE reputation SET rep_given = rep_given + 1, last_rep_given_at = ? WHERE user_id = ?",
        (now_str, giver_id),
    )
    conn.execute(
        "UPDATE reputation SET rep_received = rep_received + 1 WHERE user_id = ?",
        (receiver_id,),
    )
    conn.execute(
        """INSERT INTO reputation_logs
               (giver_id, giver_username, receiver_username, amount, reason, risk_note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (giver_id, giver_username, receiver_username, 1, "", risk_note),
    )
    conn.commit()
    conn.close()


def add_rep_staff(username: str, amount: int, by_username: str) -> int:
    """Add rep as a staff action. Returns new total, or -1 if user not found."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT user_id FROM reputation WHERE LOWER(username) = ?", (username.lower(),)
    ).fetchone()
    if row is None:
        conn.close()
        return -1
    conn.execute(
        "UPDATE reputation SET rep_received = rep_received + ? WHERE LOWER(username) = ?",
        (amount, username.lower()),
    )
    conn.execute(
        """INSERT INTO reputation_logs
               (giver_id, giver_username, receiver_username, amount, reason)
           VALUES (?, ?, ?, ?, ?)""",
        ("staff", by_username, username, amount, "staff_add"),
    )
    conn.commit()
    new_total = conn.execute(
        "SELECT rep_received FROM reputation WHERE LOWER(username) = ?", (username.lower(),)
    ).fetchone()[0]
    conn.close()
    return new_total


def remove_rep_staff(username: str, amount: int, by_username: str) -> int:
    """Remove rep as a staff action (floor 0). Returns new total, or -1 if not found."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT user_id FROM reputation WHERE LOWER(username) = ?", (username.lower(),)
    ).fetchone()
    if row is None:
        conn.close()
        return -1
    conn.execute(
        "UPDATE reputation SET rep_received = MAX(0, rep_received - ?) WHERE LOWER(username) = ?",
        (amount, username.lower()),
    )
    conn.execute(
        """INSERT INTO reputation_logs
               (giver_id, giver_username, receiver_username, amount, reason)
           VALUES (?, ?, ?, ?, ?)""",
        ("staff", by_username, username, -amount, "staff_remove"),
    )
    conn.commit()
    new_total = conn.execute(
        "SELECT rep_received FROM reputation WHERE LOWER(username) = ?", (username.lower(),)
    ).fetchone()[0]
    conn.close()
    return new_total


def get_rep_logs(username: str, limit: int = 5) -> list[dict]:
    """Return rep log entries where the user is giver or receiver (newest first)."""
    conn  = get_connection()
    uname = username.lower()
    rows  = conn.execute(
        """SELECT * FROM reputation_logs
           WHERE LOWER(giver_username) = ? OR LOWER(receiver_username) = ?
           ORDER BY id DESC LIMIT ?""",
        (uname, uname, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_top_rep(limit: int = 10) -> list[dict]:
    """Return top players sorted by rep_received descending."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM reputation ORDER BY rep_received DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def award_rep_title(user_id: str, title_id: str) -> None:
    """Grant a reputation-unlocked title. INSERT OR IGNORE prevents duplicates."""
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO owned_items (user_id, item_id, item_type) VALUES (?, ?, ?)",
        (user_id, title_id, "title"),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Poker helpers
# ---------------------------------------------------------------------------

def ensure_poker_stats(user_id: str, username: str) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO poker_stats (user_id, username) VALUES (?, ?)",
        (user_id, username),
    )
    conn.commit()
    conn.close()


def get_poker_stats(user_id: str) -> dict:
    conn  = get_connection()
    row   = conn.execute(
        "SELECT * FROM poker_stats WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return {
        "user_id": user_id, "username": "", "hands_played": 0,
        "wins": 0, "losses": 0, "folds": 0, "total_won": 0, "biggest_pot": 0,
    }


def update_poker_stats(
    user_id: str,
    username: str,
    *,
    wins: int = 0,
    losses: int = 0,
    folds: int = 0,
    total_won: int = 0,
    biggest_pot: int = 0,
    hands: int = 0,
) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO poker_stats (user_id, username) VALUES (?, ?)",
        (user_id, username),
    )
    conn.execute("""
        UPDATE poker_stats SET
            hands_played = hands_played + ?,
            wins         = wins + ?,
            losses       = losses + ?,
            folds        = folds + ?,
            total_won    = total_won + ?,
            biggest_pot  = MAX(biggest_pot, ?)
        WHERE user_id = ?
    """, (hands, wins, losses, folds, total_won, biggest_pot, user_id))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Maintenance helpers
# ---------------------------------------------------------------------------

def get_db_stats() -> dict:
    """Row-count snapshot for /dbstats."""
    conn = get_connection()
    users        = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_coins  = conn.execute(
        "SELECT COALESCE(SUM(balance), 0) FROM users"
    ).fetchone()[0]
    transactions = conn.execute(
        "SELECT COUNT(*) FROM bank_transactions"
    ).fetchone()[0]
    open_reports = conn.execute(
        "SELECT COUNT(*) FROM reports WHERE status = 'open'"
    ).fetchone()[0]
    purchases    = conn.execute(
        "SELECT COUNT(*) FROM purchase_history"
    ).fetchone()[0]
    bj_rounds    = conn.execute(
        "SELECT COALESCE(SUM(bj_wins + bj_losses + bj_pushes), 0) FROM bj_stats"
    ).fetchone()[0]
    conn.close()
    return {
        "users":        users,
        "total_coins":  total_coins,
        "transactions": transactions,
        "open_reports": open_reports,
        "purchases":    purchases,
        "bj_rounds":    bj_rounds,
    }


def cleanup_expired_data() -> dict:
    """Remove expired mutes and other safely-purgeable stale rows."""
    conn = get_connection()
    cur  = conn.execute("DELETE FROM mutes WHERE expires_at < datetime('now')")
    mutes_removed = cur.rowcount
    conn.commit()
    conn.close()
    return {"mutes": mutes_removed}


# ---------------------------------------------------------------------------
# Moderation helpers — mutes
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone as _tz


def mute_user(user_id: str, username: str, muted_by: str, duration_minutes: int) -> None:
    """Insert or replace an active mute record."""
    expires_at = (
        datetime.now(_tz.utc) + timedelta(minutes=duration_minutes)
    ).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO mutes (user_id, username, muted_by, expires_at) VALUES (?, ?, ?, ?)",
        (user_id, username, muted_by, expires_at),
    )
    conn.commit()
    conn.close()


def unmute_user(user_id: str) -> bool:
    """Remove a mute. Returns True if a record was deleted."""
    conn = get_connection()
    cur  = conn.execute("DELETE FROM mutes WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def get_active_mute(user_id: str) -> dict | None:
    """
    Return mute info (with 'mins_left' key) if the user is still muted,
    otherwise None. Expired mutes are cleaned up automatically.
    """
    conn = get_connection()
    row  = conn.execute("SELECT * FROM mutes WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        conn.close()
        return None
    r       = dict(row)
    now     = datetime.now(_tz.utc)
    expires = datetime.strptime(r["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_tz.utc)
    if now >= expires:
        conn.execute("DELETE FROM mutes WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return None
    r["mins_left"] = max(1, int((expires - now).total_seconds() / 60) + 1)
    conn.close()
    return r


def get_all_active_mutes(limit: int = 5) -> list[dict]:
    """Return up to `limit` active mutes, cleaning expired ones first."""
    now_str = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn    = get_connection()
    conn.execute("DELETE FROM mutes WHERE expires_at <= ?", (now_str,))
    conn.commit()
    rows = conn.execute(
        "SELECT * FROM mutes WHERE expires_at > ? ORDER BY expires_at ASC LIMIT ?",
        (now_str, limit),
    ).fetchall()
    conn.close()
    now    = datetime.now(_tz.utc)
    result = []
    for row in rows:
        r       = dict(row)
        expires = datetime.strptime(r["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_tz.utc)
        r["mins_left"] = max(1, int((expires - now).total_seconds() / 60) + 1)
        result.append(r)
    return result


# ---------------------------------------------------------------------------
# Moderation helpers — warnings
# ---------------------------------------------------------------------------


def add_warning(user_id: str, username: str, warned_by: str, reason: str) -> int:
    """Add a warning and return the new total warning count for this user."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO warnings (user_id, username, warned_by, reason) VALUES (?, ?, ?, ?)",
        (user_id, username, warned_by, reason),
    )
    conn.commit()
    total = conn.execute(
        "SELECT COUNT(*) FROM warnings WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    conn.close()
    return total


def get_warnings(username: str, limit: int = 5) -> tuple[list[dict], int]:
    """Return (last N warnings newest-first, total count) for the given username."""
    conn  = get_connection()
    uname = username.lower()
    total = conn.execute(
        "SELECT COUNT(*) FROM warnings WHERE LOWER(username) = ?", (uname,)
    ).fetchone()[0]
    rows  = conn.execute(
        "SELECT * FROM warnings WHERE LOWER(username) = ? ORDER BY id DESC LIMIT ?",
        (uname, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def clear_warnings(username: str) -> int:
    """Delete all warnings for the given username. Returns number deleted."""
    conn = get_connection()
    cur  = conn.execute(
        "DELETE FROM warnings WHERE LOWER(username) = ?", (username.lower(),)
    )
    conn.commit()
    conn.close()
    return cur.rowcount


# ---------------------------------------------------------------------------
# Report system helpers
# ---------------------------------------------------------------------------

def create_report(reporter_id: str, reporter_username: str,
                  target_username: str, report_type: str, reason: str) -> int:
    """Insert a new report and return its auto-assigned ID."""
    conn = get_connection()
    cur  = conn.execute("""
        INSERT INTO reports (reporter_id, reporter_username, target_username,
                             report_type, reason)
        VALUES (?, ?, ?, ?, ?)
    """, (reporter_id, reporter_username, target_username, report_type, reason))
    report_id = cur.lastrowid
    conn.commit()
    conn.close()
    return report_id


def get_open_reports(limit: int = 5) -> list[dict]:
    """Return the newest `limit` open reports."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM reports WHERE status = 'open' ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_report_by_id(report_id: int) -> dict | None:
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM reports WHERE id = ?", (report_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def close_report(report_id: int, handled_by: str) -> bool:
    """Mark a report as closed. Returns True on success."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE reports SET status = 'closed', handled_by = ? WHERE id = ?",
            (handled_by, report_id)
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def get_reports_for_username(username: str, limit: int = 5) -> list[dict]:
    """Return reports where the given username is reporter OR target."""
    conn  = get_connection()
    uname = username.lower()
    rows  = conn.execute("""
        SELECT * FROM reports
        WHERE LOWER(reporter_username) = ?
           OR LOWER(target_username)   = ?
        ORDER BY id DESC LIMIT ?
    """, (uname, uname, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_my_reports(reporter_id: str, limit: int = 5) -> list[dict]:
    """Return reports submitted by this player."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM reports WHERE reporter_id = ? ORDER BY id DESC LIMIT ?",
        (reporter_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Economy-settings helpers
# ---------------------------------------------------------------------------

_ECONOMY_DEFAULTS: dict[str, int] = {
    "daily_coins":     50,
    "trivia_reward":   20,
    "scramble_reward": 20,
    "riddle_reward":   25,
    "max_balance":     1_000_000,
}


def get_economy_settings() -> dict[str, int]:
    """Return all economy settings as a dict of int values."""
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM economy_settings").fetchall()
    conn.close()
    settings = {r["key"]: int(r["value"]) for r in rows}
    for k, v in _ECONOMY_DEFAULTS.items():
        settings.setdefault(k, v)
    return settings


def set_economy_setting(key: str, value: str):
    conn = get_connection()
    conn.execute(
        "INSERT INTO economy_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value)
    )
    conn.commit()
    conn.close()


def get_max_balance() -> int:
    """Return the current max balance cap."""
    return get_economy_settings()["max_balance"]


def adjust_balance_capped(user_id: str, amount: int) -> int:
    """
    Credit amount to user_id's balance, capped at max_balance.
    Returns the actual amount credited (may be less if cap was hit).
    Negative amounts are ignored (use adjust_balance for deductions).
    """
    if amount <= 0:
        return 0
    max_bal = get_max_balance()
    conn    = get_connection()
    row     = conn.execute(
        "SELECT balance FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    current = row["balance"] if row else 0
    actual  = min(amount, max(0, max_bal - current))
    if actual > 0:
        conn.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (actual, user_id)
        )
        conn.commit()
    conn.close()
    return actual


# ---------------------------------------------------------------------------
# Audit query helpers
# ---------------------------------------------------------------------------

def get_audit_full(username: str) -> dict | None:
    """All fields needed for /audit <username>."""
    conn = get_connection()
    u = conn.execute(
        "SELECT user_id, username, balance, level, total_coins_earned "
        "FROM users WHERE LOWER(username) = LOWER(?)", (username,)
    ).fetchone()
    if not u:
        conn.close()
        return None
    uid = u["user_id"]
    bus = conn.execute(
        "SELECT total_sent, total_received, bank_blocked, suspicious_transfer_count "
        "FROM bank_user_stats WHERE user_id = ?", (uid,)
    ).fetchone()
    bj = conn.execute(
        "SELECT bj_total_won, bj_total_bet FROM bj_stats WHERE user_id = ?", (uid,)
    ).fetchone()
    rbj = conn.execute(
        "SELECT rbj_total_won, rbj_total_bet FROM rbj_stats WHERE user_id = ?", (uid,)
    ).fetchone()
    conn.close()
    bj_net  = (bj["bj_total_won"]  - bj["bj_total_bet"])  if bj  else 0
    rbj_net = (rbj["rbj_total_won"] - rbj["rbj_total_bet"]) if rbj else 0
    return {
        "username":       u["username"],
        "balance":        u["balance"] or 0,
        "level":          u["level"] or 1,
        "total_earned":   u["total_coins_earned"] or 0,
        "total_sent":     bus["total_sent"] if bus else 0,
        "total_received": bus["total_received"] if bus else 0,
        "casino_net":     bj_net + rbj_net,
        "bank_blocked":   bool(bus["bank_blocked"]) if bus else False,
        "risk_count":     bus["suspicious_transfer_count"] if bus else 0,
    }


def get_audit_casino_data(user_id: str) -> dict:
    """Casino stats for /auditcasino."""
    today = str(date.today())
    conn  = get_connection()
    bj  = conn.execute("SELECT * FROM bj_stats  WHERE user_id = ?", (user_id,)).fetchone()
    rbj = conn.execute("SELECT * FROM rbj_stats WHERE user_id = ?", (user_id,)).fetchone()
    bj_day  = conn.execute(
        "SELECT net FROM bj_daily  WHERE user_id = ? AND date = ?", (user_id, today)
    ).fetchone()
    rbj_day = conn.execute(
        "SELECT net FROM rbj_daily WHERE user_id = ? AND date = ?", (user_id, today)
    ).fetchone()
    conn.close()
    bj_net  = (bj["bj_total_won"]  - bj["bj_total_bet"])  if bj  else 0
    rbj_net = (rbj["rbj_total_won"] - rbj["rbj_total_bet"]) if rbj else 0
    return {
        "bj_wins":   bj["bj_wins"]   if bj  else 0,
        "bj_losses": bj["bj_losses"] if bj  else 0,
        "bj_pushes": bj["bj_pushes"] if bj  else 0,
        "bj_net":    bj_net,
        "rbj_wins":   rbj["rbj_wins"]   if rbj else 0,
        "rbj_losses": rbj["rbj_losses"] if rbj else 0,
        "rbj_pushes": rbj["rbj_pushes"] if rbj else 0,
        "rbj_net":    rbj_net,
        "casino_net": bj_net + rbj_net,
        "bj_daily":  bj_day["net"]  if bj_day  else 0,
        "rbj_daily": rbj_day["net"] if rbj_day else 0,
    }


def get_audit_economy_data(user_id: str) -> dict:
    """Ledger extremes + recent entries for /auditeconomy."""
    conn = get_connection()
    recent = conn.execute("""
        SELECT change_amount, reason, timestamp
        FROM ledger WHERE user_id = ?
        ORDER BY id DESC LIMIT 5
    """, (user_id,)).fetchall()
    best_gain = conn.execute(
        "SELECT MAX(change_amount) AS v FROM ledger WHERE user_id = ? AND change_amount > 0",
        (user_id,)
    ).fetchone()
    biggest_loss = conn.execute(
        "SELECT MIN(change_amount) AS v FROM ledger WHERE user_id = ? AND change_amount < 0",
        (user_id,)
    ).fetchone()
    conn.close()
    return {
        "recent":       [dict(r) for r in recent],
        "best_gain":    best_gain["v"]    if best_gain    and best_gain["v"]    is not None else 0,
        "biggest_loss": biggest_loss["v"] if biggest_loss and biggest_loss["v"] is not None else 0,
    }


def get_ledger_for(user_id: str, page: int = 1, limit: int = 5) -> list:
    offset = (page - 1) * limit
    conn   = get_connection()
    rows   = conn.execute("""
        SELECT timestamp, change_amount, reason, balance_before, balance_after, related_user
        FROM ledger WHERE user_id = ?
        ORDER BY id DESC LIMIT ? OFFSET ?
    """, (user_id, limit, offset)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
