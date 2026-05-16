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
import os
import sqlite3
from contextlib import contextmanager
from datetime import date
from typing import Optional

import config


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=NORMAL")   # WAL best-practice: halves write latency, still crash-safe
        conn.execute("PRAGMA cache_size=-8000")     # 8 MB page cache (default is ~2 MB)
        conn.execute("PRAGMA temp_store=MEMORY")    # temp tables in RAM, not disk
    except Exception:
        pass
    return conn


@contextmanager
def db_conn():
    """Context manager for safe connection lifecycle — commits on success, rolls back on error.

    Use this for new code that needs guaranteed close-on-exception behaviour.
    Existing helpers that open/close inline are safe under CPython GC, but
    this makes close-on-exception deterministic and avoids holding WAL readers open.

    Example:
        with db_conn() as conn:
            conn.execute("UPDATE ...")
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Database initialisation + migration
# ---------------------------------------------------------------------------

def init_db():
    """Create all tables if needed, then run safe column migrations.

    Called once by bot.py before any subprocess or asyncio loop starts,
    so there is never concurrent write contention during schema setup.
    """
    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=1000")  # checkpoint every 1000 pages; prevents WAL file bloat

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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS time_exp_daily (
            user_id TEXT NOT NULL,
            date    TEXT NOT NULL,
            earned  INTEGER NOT NULL DEFAULT 0,
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
            total_lost   INTEGER NOT NULL DEFAULT 0,
            total_buyin  INTEGER NOT NULL DEFAULT 0,
            biggest_pot  INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Migrate poker_stats: add new columns if they don't exist yet
    for _col, _def in [
        ("total_lost",          "INTEGER NOT NULL DEFAULT 0"),
        ("total_buyin",         "INTEGER NOT NULL DEFAULT 0"),
        ("allins",              "INTEGER NOT NULL DEFAULT 0"),
        ("net_profit",          "INTEGER NOT NULL DEFAULT 0"),
        ("biggest_win",         "INTEGER NOT NULL DEFAULT 0"),
        ("current_win_streak",  "INTEGER NOT NULL DEFAULT 0"),
        ("best_win_streak",     "INTEGER NOT NULL DEFAULT 0"),
        ("showdowns",           "INTEGER NOT NULL DEFAULT 0"),
        ("last_played_at",      "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE poker_stats ADD COLUMN {_col} {_def}")
        except Exception:
            pass

    # Migrate poker_active_players: add allin_amount if missing
    try:
        conn.execute(
            "ALTER TABLE poker_active_players ADD COLUMN allin_amount INTEGER DEFAULT 0"
        )
    except Exception:
        pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS poker_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    for _k, _v in [
        ("poker_enabled",          "1"),
        ("min_buyin",              "100"),
        ("max_buyin",              "5000"),
        ("min_players",            "2"),
        ("max_players",            "6"),
        ("lobby_countdown",        "15"),
        ("turn_timer",             "20"),
        ("poker_card_marker",      "🂠"),
        ("min_raise",              "50"),
        ("max_raise",              "1000"),
        ("table_daily_win_limit",  "10000"),
        ("table_daily_loss_limit", "5000"),
        ("win_limit_enabled",      "1"),
        ("loss_limit_enabled",     "1"),
        ("poker_buyin_to_pot",     "0"),
        ("raise_limit_enabled",    "1"),
        ("allin_enabled",          "1"),
        ("buyin_limit_enabled",    "0"),
        ("small_blind",            "50"),
        ("big_blind",              "100"),
        ("ante",                   "0"),
        ("blinds_enabled",         "1"),
        ("auto_start_next_hand",   "1"),
        ("next_hand_delay",        "10"),
        ("rebuy_enabled",          "1"),
        ("max_stack_enabled",      "0"),
        ("max_table_stack",        "100000"),
        ("autositout_enabled",     "0"),
        ("idle_strikes_limit",     "3"),
        ("table_closing",          "0"),
        # Pace mode: fast / normal / long
        ("pace_mode",              "normal"),
        ("pace_preflop_secs",      "30"),
        ("pace_flop_secs",         "45"),
        ("pace_turn_secs",         "30"),
        ("pace_river_secs",        "30"),
        ("pace_deal_delay_secs",   "0.5"),
        ("pace_autofold_secs",     "60"),
        ("pace_inactivity_secs",   "300"),
        # Stack settings
        ("stack_min_buyin",        "100"),
        ("stack_max_buyin",        "10000"),
        ("stack_default",          "1000"),
        ("stack_rebuy_min",        "100"),
        ("stack_rebuy_max",        "10000"),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO poker_settings (key, value) VALUES (?, ?)",
            (_k, _v),
        )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS poker_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            hand_number INTEGER DEFAULT 0,
            action      TEXT NOT NULL,
            user_id     TEXT DEFAULT '',
            username    TEXT DEFAULT '',
            amount      INTEGER DEFAULT 0,
            pot         INTEGER DEFAULT 0,
            stack       INTEGER DEFAULT 0,
            details     TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS poker_active_table (
            id                     INTEGER PRIMARY KEY CHECK (id = 1),
            active                 INTEGER DEFAULT 0,
            phase                  TEXT    DEFAULT 'idle',
            round_id               TEXT,
            created_at             TEXT,
            updated_at             TEXT,
            lobby_started_at       TEXT,
            lobby_ends_at          TEXT,
            round_started_at       TEXT,
            turn_ends_at           TEXT,
            current_player_index   INTEGER DEFAULT 0,
            dealer_button_index    INTEGER DEFAULT 0,
            deck_json              TEXT    DEFAULT '[]',
            community_cards_json   TEXT    DEFAULT '[]',
            pot                    INTEGER DEFAULT 0,
            current_bet            INTEGER DEFAULT 0,
            last_raiser_username   TEXT,
            settings_snapshot_json TEXT,
            restored_after_restart INTEGER DEFAULT 0
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO poker_active_table (id, active, phase) VALUES (1, 0, 'idle')"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS poker_active_players (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id          TEXT    NOT NULL,
            username          TEXT    NOT NULL,
            user_id           TEXT    NOT NULL,
            buyin             INTEGER NOT NULL,
            stack             INTEGER NOT NULL,
            current_bet       INTEGER DEFAULT 0,
            total_contributed INTEGER DEFAULT 0,
            hole_cards_json   TEXT    DEFAULT '[]',
            status            TEXT    DEFAULT 'lobby',
            acted             INTEGER DEFAULT 0,
            joined_at         TEXT,
            acted_at          TEXT,
            result            TEXT,
            payout            INTEGER DEFAULT 0,
            UNIQUE(round_id, username)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS poker_round_results (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id  TEXT    NOT NULL,
            username  TEXT    NOT NULL,
            buyin     INTEGER DEFAULT 0,
            result    TEXT,
            payout    INTEGER DEFAULT 0,
            net       INTEGER DEFAULT 0,
            paid      INTEGER DEFAULT 0,
            timestamp TEXT,
            UNIQUE(round_id, username)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS poker_daily_limits (
            username TEXT NOT NULL,
            date     TEXT NOT NULL,
            net      INTEGER DEFAULT 0,
            PRIMARY KEY(username, date)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS poker_daily_stats (
            username     TEXT NOT NULL,
            date         TEXT NOT NULL,
            hands_played INTEGER DEFAULT 0,
            wins         INTEGER DEFAULT 0,
            losses       INTEGER DEFAULT 0,
            net_profit   INTEGER DEFAULT 0,
            biggest_pot  INTEGER DEFAULT 0,
            PRIMARY KEY(username, date)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS poker_recovery_logs (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            action    TEXT,
            round_id  TEXT,
            phase     TEXT,
            details   TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS poker_seated_players (
            username           TEXT    PRIMARY KEY,
            user_id            TEXT    NOT NULL,
            table_stack        INTEGER NOT NULL DEFAULT 0,
            buyin_total        INTEGER NOT NULL DEFAULT 0,
            status             TEXT    NOT NULL DEFAULT 'seated',
            seat_number        INTEGER NOT NULL DEFAULT 0,
            joined_at          TEXT    NOT NULL,
            last_action_at     TEXT,
            hands_at_table     INTEGER NOT NULL DEFAULT 0,
            leaving_after_hand INTEGER NOT NULL DEFAULT 0,
            idle_strikes       INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tip_conversions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT    NOT NULL DEFAULT (datetime('now')),
            user_id       TEXT    NOT NULL,
            username      TEXT    NOT NULL,
            gold_amount   INTEGER NOT NULL,
            bonus_pct     INTEGER NOT NULL DEFAULT 0,
            coins_awarded INTEGER NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tip_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # Seed tip setting defaults (safe: ON CONFLICT DO NOTHING)
    for _key, _val in [
        ("coins_per_gold",    "10"),
        ("min_tip_gold",      "10"),
        ("daily_cap_gold",    "10000"),
        ("tier_100_bonus",    "10"),
        ("tier_500_bonus",    "20"),
        ("tier_1000_bonus",   "30"),
        ("tier_5000_bonus",   "50"),
    ]:
        conn.execute(
            "INSERT INTO tip_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO NOTHING",
            (_key, _val),
        )

    # ── Tip transactions log (spec-required table with dedup hash) ────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tip_transactions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp        TEXT    NOT NULL DEFAULT (datetime('now')),
            username         TEXT    NOT NULL DEFAULT '',
            gold_amount      INTEGER NOT NULL DEFAULT 0,
            coins_awarded    INTEGER NOT NULL DEFAULT 0,
            bonus_percent    INTEGER NOT NULL DEFAULT 0,
            status           TEXT    NOT NULL DEFAULT 'success',
            event_id_or_hash TEXT    NOT NULL DEFAULT ''
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS auto_game_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    for _k, _v in [
        ("game_answer_timer",      "60"),
        ("auto_minigames_enabled", "1"),
        ("auto_minigame_interval", "10"),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO auto_game_settings (key, value) VALUES (?, ?)",
            (_k, _v),
        )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS auto_event_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    for _k, _v in [
        ("auto_events_enabled",  "1"),
        ("auto_event_interval",  "60"),
        ("auto_event_duration",  "30"),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO auto_event_settings (key, value) VALUES (?, ?)",
            (_k, _v),
        )

    # ── Gold transactions log ─────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gold_transactions (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp         TEXT    NOT NULL DEFAULT (datetime('now')),
            action_type       TEXT    NOT NULL,
            sender_owner      TEXT    NOT NULL DEFAULT '',
            receiver_username TEXT    NOT NULL DEFAULT '',
            receiver_user_id  TEXT    NOT NULL DEFAULT '',
            amount_gold       INTEGER NOT NULL DEFAULT 0,
            reason            TEXT    NOT NULL DEFAULT '',
            status            TEXT    NOT NULL DEFAULT '',
            denominations     TEXT    NOT NULL DEFAULT '',
            batch_id          TEXT    NOT NULL DEFAULT '',
            error_message     TEXT    NOT NULL DEFAULT ''
        )
    """)

    # ── Gold settings (key/value store) ──────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gold_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    for _k, _v in [
        ("goldrain_include_staff",          "true"),
        ("goldrain_min_players",            "1"),
        ("goldrain_max_total",              "1000"),
        ("goldrain_require_confirm_above",  "100"),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO gold_settings (key, value) VALUES (?, ?)",
            (_k, _v),
        )

    # ── Gold Rain tables ─────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gold_rain_events (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            mode                 TEXT    NOT NULL DEFAULT 'normal',
            target_group         TEXT    NOT NULL DEFAULT 'all',
            total_gold           REAL    NOT NULL DEFAULT 0,
            winners_count        INTEGER NOT NULL DEFAULT 0,
            gold_each            REAL    NOT NULL DEFAULT 0,
            interval_seconds     INTEGER NOT NULL DEFAULT 0,
            replacement_enabled  INTEGER NOT NULL DEFAULT 1,
            status               TEXT    NOT NULL DEFAULT 'pending',
            created_by_user_id   TEXT    NOT NULL DEFAULT '',
            created_by_username  TEXT    NOT NULL DEFAULT '',
            created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
            started_at           TEXT,
            completed_at         TEXT,
            cancelled_at         TEXT,
            error                TEXT    NOT NULL DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gold_rain_winners (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id       INTEGER NOT NULL,
            user_id        TEXT    NOT NULL DEFAULT '',
            username       TEXT    NOT NULL DEFAULT '',
            gold_amount    REAL    NOT NULL DEFAULT 0,
            rank           INTEGER NOT NULL DEFAULT 0,
            payout_status  TEXT    NOT NULL DEFAULT 'pending',
            payout_error   TEXT    NOT NULL DEFAULT '',
            selected_at    TEXT    NOT NULL DEFAULT (datetime('now')),
            paid_at        TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gold_rain_settings (
            key        TEXT PRIMARY KEY,
            value      TEXT    NOT NULL DEFAULT '',
            updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    for _k, _v in [
        ("default_interval",    "10"),
        ("replacement_enabled", "true"),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO gold_rain_settings (key, value) VALUES (?, ?)",
            (_k, _v),
        )

    # ── Casino state persistence tables ──────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS casino_active_tables (
            mode                 TEXT PRIMARY KEY,
            phase                TEXT NOT NULL DEFAULT 'idle',
            round_id             TEXT NOT NULL DEFAULT '',
            current_player_index INTEGER NOT NULL DEFAULT 0,
            dealer_hand_json     TEXT NOT NULL DEFAULT '[]',
            deck_json            TEXT NOT NULL DEFAULT '[]',
            shoe_json            TEXT NOT NULL DEFAULT '[]',
            shoe_cards_remaining INTEGER NOT NULL DEFAULT 0,
            countdown_ends_at    TEXT NOT NULL DEFAULT '',
            turn_ends_at         TEXT NOT NULL DEFAULT '',
            active               INTEGER NOT NULL DEFAULT 0,
            recovery_required    INTEGER NOT NULL DEFAULT 0,
            created_at           TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS casino_active_players (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            mode      TEXT NOT NULL,
            username  TEXT NOT NULL,
            user_id   TEXT NOT NULL,
            bet       INTEGER NOT NULL DEFAULT 0,
            hand_json TEXT NOT NULL DEFAULT '[]',
            status    TEXT NOT NULL DEFAULT 'lobby',
            doubled   INTEGER NOT NULL DEFAULT 0,
            joined_at TEXT NOT NULL DEFAULT (datetime('now')),
            acted_at  TEXT NOT NULL DEFAULT '',
            payout    INTEGER NOT NULL DEFAULT 0,
            result    TEXT NOT NULL DEFAULT '',
            UNIQUE(mode, username)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS casino_round_results (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            mode      TEXT NOT NULL,
            round_id  TEXT NOT NULL,
            username  TEXT NOT NULL,
            user_id   TEXT NOT NULL DEFAULT '',
            bet       INTEGER NOT NULL DEFAULT 0,
            result    TEXT NOT NULL DEFAULT '',
            payout    INTEGER NOT NULL DEFAULT 0,
            net       INTEGER NOT NULL DEFAULT 0,
            paid      INTEGER NOT NULL DEFAULT 0,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(mode, round_id, username)
        )
    """)

    # ── Subscriber DM users ───────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subscriber_users (
            username                TEXT PRIMARY KEY,
            user_id                 TEXT,
            conversation_id         TEXT,
            subscribed              INTEGER NOT NULL DEFAULT 0,
            subscribed_at           TEXT,
            last_dm_at              TEXT,
            last_seen_at            TEXT,
            dm_available            INTEGER NOT NULL DEFAULT 0,
            auto_subscribed_from_tip INTEGER NOT NULL DEFAULT 0
        )
    """)

    # ── Subscriber announcements log ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subscriber_announcements (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp        TEXT NOT NULL DEFAULT (datetime('now')),
            sender_username  TEXT NOT NULL,
            target_type      TEXT NOT NULL,
            target_username  TEXT,
            message          TEXT NOT NULL,
            delivered_count  INTEGER NOT NULL DEFAULT 0,
            pending_count    INTEGER NOT NULL DEFAULT 0,
            failed_count     INTEGER NOT NULL DEFAULT 0
        )
    """)

    # ── Pending subscriber messages ───────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_subscriber_messages (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            receiver_username TEXT NOT NULL,
            message           TEXT NOT NULL,
            created_at        TEXT NOT NULL DEFAULT (datetime('now')),
            delivered         INTEGER NOT NULL DEFAULT 0,
            delivered_at      TEXT,
            delivery_attempts INTEGER NOT NULL DEFAULT 0,
            last_error        TEXT,
            message_type      TEXT NOT NULL DEFAULT 'general'
        )
    """)

    # ── Bank pending notifications ────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bank_notifications (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            receiver_username TEXT NOT NULL,
            sender_username   TEXT NOT NULL,
            amount_received   INTEGER NOT NULL DEFAULT 0,
            fee               INTEGER NOT NULL DEFAULT 0,
            timestamp         TEXT NOT NULL DEFAULT (datetime('now')),
            delivered         INTEGER NOT NULL DEFAULT 0,
            delivered_at      TEXT,
            delivery_attempts INTEGER NOT NULL DEFAULT 0,
            last_error        TEXT
        )
    """)

    # ── Notification preferences ───────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_preferences (
            username              TEXT PRIMARY KEY,
            bank_alerts           INTEGER NOT NULL DEFAULT 1,
            event_alerts          INTEGER NOT NULL DEFAULT 1,
            gold_alerts           INTEGER NOT NULL DEFAULT 1,
            vip_alerts            INTEGER NOT NULL DEFAULT 1,
            casino_alerts         INTEGER NOT NULL DEFAULT 1,
            quest_alerts          INTEGER NOT NULL DEFAULT 1,
            shop_alerts           INTEGER NOT NULL DEFAULT 1,
            announcement_alerts   INTEGER NOT NULL DEFAULT 1,
            staff_alerts          INTEGER NOT NULL DEFAULT 1,
            dm_alerts             INTEGER NOT NULL DEFAULT 1,
            room_whisper_alerts   INTEGER NOT NULL DEFAULT 1
        )
    """)

    # ── Notification audit log ─────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_logs (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp         TEXT NOT NULL DEFAULT (datetime('now')),
            username          TEXT NOT NULL DEFAULT '',
            notification_type TEXT NOT NULL DEFAULT '',
            channel           TEXT NOT NULL DEFAULT '',
            message           TEXT NOT NULL DEFAULT '',
            status            TEXT NOT NULL DEFAULT '',
            error_message     TEXT NOT NULL DEFAULT ''
        )
    """)

    # ── Pending typed notifications (per-type, separate from broadcasts) ───
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_notifications (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            receiver_username TEXT NOT NULL,
            notification_type TEXT NOT NULL DEFAULT 'general',
            message           TEXT NOT NULL,
            created_at        TEXT NOT NULL DEFAULT (datetime('now')),
            delivered         INTEGER NOT NULL DEFAULT 0,
            delivered_at      TEXT,
            delivery_attempts INTEGER NOT NULL DEFAULT 0,
            last_error        TEXT
        )
    """)

    # ── Moderation settings (rules, automod config) ───────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS moderation_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )
    """)
    _DEFAULT_MOD_SETTINGS = {
        "room_rules":       "📜 Rules: Be respectful. No spam. No scams. Staff decisions are final.",
        "automod_enabled":  "1",
        "max_same_message": "3",
        "max_commands":     "8",
        "max_reports":      "3",
    }
    for _k, _v in _DEFAULT_MOD_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO moderation_settings (key, value) VALUES (?, ?)",
            (_k, _v),
        )

    # ── Daily admin checklist log ──────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_admin_logs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT NOT NULL DEFAULT (datetime('now')),
            username     TEXT NOT NULL DEFAULT '',
            section      TEXT NOT NULL DEFAULT '',
            summary_text TEXT NOT NULL DEFAULT ''
        )
    """)

    # ── Profile privacy settings ────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS profile_privacy (
            username          TEXT PRIMARY KEY,
            show_money        INTEGER NOT NULL DEFAULT 1,
            show_casino       INTEGER NOT NULL DEFAULT 1,
            show_achievements INTEGER NOT NULL DEFAULT 1,
            show_inventory    INTEGER NOT NULL DEFAULT 1,
            updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # ── Event Manager — numbered catalog + pool + history ─────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_definitions (
            event_id               TEXT PRIMARY KEY,
            event_number           INTEGER UNIQUE,
            event_name             TEXT,
            emoji                  TEXT,
            event_type             TEXT DEFAULT 'mining',
            effect_desc            TEXT,
            default_duration_minutes INTEGER DEFAULT 30,
            manual_only            INTEGER DEFAULT 0,
            stackable              INTEGER DEFAULT 0,
            default_weight         INTEGER DEFAULT 1,
            cooldown_minutes       INTEGER DEFAULT 60,
            enabled                INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auto_event_pool (
            event_id         TEXT PRIMARY KEY,
            weight           INTEGER DEFAULT 1,
            cooldown_minutes INTEGER DEFAULT 60,
            last_started_at  TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_history (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id         TEXT,
            event_name       TEXT,
            started_by       TEXT,
            auto_started     INTEGER DEFAULT 0,
            started_at       TEXT,
            ended_at         TEXT DEFAULT '',
            duration_seconds INTEGER DEFAULT 0,
            status           TEXT DEFAULT 'active'
        )
    """)

    # ── Fishing tables ────────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fish_profiles (
            user_id          TEXT PRIMARY KEY,
            username         TEXT NOT NULL,
            fishing_level    INTEGER NOT NULL DEFAULT 1,
            fishing_xp       INTEGER NOT NULL DEFAULT 0,
            total_catches    INTEGER NOT NULL DEFAULT 0,
            equipped_rod     TEXT NOT NULL DEFAULT 'Driftwood Rod',
            best_fish_name   TEXT,
            best_fish_weight REAL    DEFAULT 0,
            best_fish_value  INTEGER DEFAULT 0,
            last_fish_at     TEXT,
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS fish_catch_records (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL,
            username    TEXT NOT NULL,
            fish_name   TEXT NOT NULL,
            rarity      TEXT NOT NULL DEFAULT 'common',
            weight      REAL NOT NULL DEFAULT 0,
            base_value  INTEGER NOT NULL DEFAULT 0,
            final_value INTEGER NOT NULL DEFAULT 0,
            fxp_earned  INTEGER NOT NULL DEFAULT 0,
            caught_at   TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS player_rods (
            user_id   TEXT NOT NULL,
            rod_name  TEXT NOT NULL,
            username  TEXT NOT NULL,
            bought_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, rod_name)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS auto_activity_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS forced_fishing_drops (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            target_user_id  TEXT NOT NULL DEFAULT '',
            target_username TEXT NOT NULL DEFAULT '',
            forced_type     TEXT NOT NULL DEFAULT 'rarity',
            forced_value    TEXT NOT NULL DEFAULT '',
            created_by      TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at      TEXT NOT NULL DEFAULT '',
            used_at         TEXT,
            status          TEXT NOT NULL DEFAULT 'pending'
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS big_announcement_settings (
            category     TEXT NOT NULL,
            rarity       TEXT NOT NULL,
            routing_mode TEXT NOT NULL DEFAULT 'off',
            enabled      INTEGER NOT NULL DEFAULT 1,
            updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (category, rarity)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS big_announcement_bot_reactions (
            bot_name   TEXT PRIMARY KEY,
            enabled    INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS big_announcement_logs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            category     TEXT NOT NULL DEFAULT '',
            rarity       TEXT NOT NULL DEFAULT '',
            item_name    TEXT NOT NULL DEFAULT '',
            user_id      TEXT NOT NULL DEFAULT '',
            username     TEXT NOT NULL DEFAULT '',
            routing_mode TEXT NOT NULL DEFAULT '',
            reacted_bots TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            status       TEXT NOT NULL DEFAULT 'pending'
        )
    """)

    # ── Tip Audit Log (tracks every tip event for owner review) ─────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tip_audit_logs (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            event_hash           TEXT DEFAULT '',
            sender_user_id       TEXT DEFAULT '',
            sender_username      TEXT DEFAULT '',
            receiver_user_id     TEXT DEFAULT '',
            receiver_username    TEXT DEFAULT '',
            bot_mode             TEXT DEFAULT '',
            raw_tip_type         TEXT DEFAULT '',
            raw_tip_id           TEXT DEFAULT '',
            gold_amount          INTEGER DEFAULT 0,
            luxe_expected        INTEGER DEFAULT 0,
            luxe_awarded         INTEGER DEFAULT 0,
            luxe_balance_before  INTEGER DEFAULT 0,
            luxe_balance_after   INTEGER DEFAULT 0,
            coins_awarded        INTEGER DEFAULT 0,
            coins_balance_before INTEGER DEFAULT 0,
            coins_balance_after  INTEGER DEFAULT 0,
            status               TEXT DEFAULT '',
            failure_reason       TEXT DEFAULT '',
            duplicate_detected   INTEGER DEFAULT 0,
            created_at           TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tip_audit_sender "
        "ON tip_audit_logs(sender_username, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tip_audit_receiver "
        "ON tip_audit_logs(receiver_username, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tip_audit_event_hash "
        "ON tip_audit_logs(event_hash)"
    )

    # ── Luxe Conversion Log (tracks !buycoins / !buyluxe coin pack purchases) ─
    conn.execute("""
        CREATE TABLE IF NOT EXISTS luxe_conversion_logs (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id              TEXT DEFAULT '',
            username             TEXT DEFAULT '',
            item_key             TEXT DEFAULT '',
            tickets_spent        INTEGER DEFAULT 0,
            coins_awarded        INTEGER DEFAULT 0,
            luxe_balance_before  INTEGER DEFAULT 0,
            luxe_balance_after   INTEGER DEFAULT 0,
            coins_balance_before INTEGER DEFAULT 0,
            coins_balance_after  INTEGER DEFAULT 0,
            status               TEXT DEFAULT '',
            failure_reason       TEXT DEFAULT '',
            created_at           TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_luxe_conv_username "
        "ON luxe_conversion_logs(username, created_at)"
    )

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
        "ALTER TABLE bank_user_stats ADD COLUMN bank_notify    INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE users           ADD COLUMN tip_coins_earned INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE bj_settings  ADD COLUMN bj_win_limit_enabled  INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE bj_settings  ADD COLUMN bj_loss_limit_enabled INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE rbj_settings ADD COLUMN rbj_win_limit_enabled  INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE rbj_settings ADD COLUMN rbj_loss_limit_enabled INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE bj_settings  ADD COLUMN bj_action_timer        INTEGER NOT NULL DEFAULT 30",
        "ALTER TABLE rbj_settings ADD COLUMN rbj_action_timer       INTEGER NOT NULL DEFAULT 30",
        "ALTER TABLE bj_settings  ADD COLUMN bj_double_enabled      INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE rbj_settings ADD COLUMN rbj_double_enabled     INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE bj_settings  ADD COLUMN bj_split_enabled       INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE rbj_settings ADD COLUMN rbj_split_enabled      INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE bj_settings  ADD COLUMN bj_max_splits          INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE rbj_settings ADD COLUMN rbj_max_splits         INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE bj_settings  ADD COLUMN bj_split_aces_one_card  INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE rbj_settings ADD COLUMN rbj_split_aces_one_card INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE bank_notifications ADD COLUMN delivery_attempts INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE bank_notifications ADD COLUMN last_error        TEXT",
        "ALTER TABLE subscriber_users   ADD COLUMN auto_subscribed_from_tip       INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE subscriber_users   ADD COLUMN unsubscribed_at                TEXT",
        "ALTER TABLE subscriber_users   ADD COLUMN auto_subscribed_from_dm        INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE subscriber_users   ADD COLUMN auto_subscribed_from_whisper   INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE subscriber_users   ADD COLUMN manually_unsubscribed          INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE bj_settings  ADD COLUMN bj_betlimit_enabled  INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE rbj_settings ADD COLUMN rbj_betlimit_enabled INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE bot_instances ADD COLUMN db_connected        INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE bot_instances ADD COLUMN last_error          TEXT    NOT NULL DEFAULT ''",
        "ALTER TABLE bot_instances ADD COLUMN current_room_id     TEXT    NOT NULL DEFAULT ''",
        "ALTER TABLE bot_instances ADD COLUMN last_heartbeat_at   TEXT    NOT NULL DEFAULT ''",
        "ALTER TABLE poker_active_table ADD COLUMN hand_number          INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE poker_active_table ADD COLUMN small_blind_username TEXT",
        "ALTER TABLE poker_active_table ADD COLUMN big_blind_username   TEXT",
        "ALTER TABLE poker_active_table ADD COLUMN next_hand_starts_at  TEXT",
        "ALTER TABLE poker_active_table ADD COLUMN table_closing        INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE poker_active_table ADD COLUMN first_turn_ready     INTEGER NOT NULL DEFAULT 1",
        "CREATE TABLE IF NOT EXISTS admin_action_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "timestamp TEXT DEFAULT (datetime('now')), "
        "actor_username TEXT NOT NULL DEFAULT '', "
        "target_username TEXT NOT NULL DEFAULT '', "
        "action TEXT NOT NULL DEFAULT '', "
        "old_value TEXT DEFAULT '', "
        "new_value TEXT DEFAULT '', "
        "reason TEXT DEFAULT '')",
        "ALTER TABLE event_points ADD COLUMN lifetime_event_coins INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE event_points ADD COLUMN updated_at TEXT",
        "CREATE TABLE IF NOT EXISTS bot_settings ("
        "key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')",
        # Emoji Badge Market tables
        "CREATE TABLE IF NOT EXISTS emoji_badges ("
        "badge_id TEXT PRIMARY KEY, emoji TEXT NOT NULL DEFAULT '', "
        "name TEXT NOT NULL DEFAULT '', rarity TEXT NOT NULL DEFAULT 'common', "
        "price INTEGER NOT NULL DEFAULT 0, purchasable INTEGER NOT NULL DEFAULT 1, "
        "tradeable INTEGER NOT NULL DEFAULT 1, sellable INTEGER NOT NULL DEFAULT 1, "
        "source TEXT NOT NULL DEFAULT 'shop', created_at TEXT, created_by TEXT)",
        "CREATE TABLE IF NOT EXISTS user_badges ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, "
        "badge_id TEXT NOT NULL, acquired_at TEXT, source TEXT, "
        "equipped INTEGER NOT NULL DEFAULT 0, locked INTEGER NOT NULL DEFAULT 0, "
        "UNIQUE(username, badge_id))",
        "CREATE TABLE IF NOT EXISTS badge_market_listings ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, seller_username TEXT NOT NULL, "
        "badge_id TEXT NOT NULL, emoji TEXT NOT NULL DEFAULT '', "
        "price INTEGER NOT NULL DEFAULT 0, listed_at TEXT, "
        "status TEXT NOT NULL DEFAULT 'active', "
        "buyer_username TEXT, sold_at TEXT)",
        "CREATE TABLE IF NOT EXISTS badge_market_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, "
        "action TEXT, seller_username TEXT, buyer_username TEXT, "
        "badge_id TEXT, emoji TEXT, price INTEGER, fee INTEGER, status TEXT)",
        # Numbered shop session system
        "CREATE TABLE IF NOT EXISTS shop_view_sessions ("
        "username TEXT PRIMARY KEY, shop_type TEXT, page INTEGER DEFAULT 1, "
        "items_json TEXT, viewed_at TEXT)",
        "CREATE TABLE IF NOT EXISTS pending_shop_purchases ("
        "code TEXT PRIMARY KEY, username TEXT, shop_type TEXT, "
        "item_id TEXT, item_name TEXT, price INTEGER, currency TEXT, "
        "listing_id INTEGER, created_at TEXT, expires_at TEXT)",
        # Room utility + bot mode tables
        "CREATE TABLE IF NOT EXISTS room_settings ("
        "key TEXT PRIMARY KEY, value TEXT)",
        "CREATE TABLE IF NOT EXISTS room_spawns ("
        "spawn_name TEXT PRIMARY KEY, x REAL DEFAULT 0, y REAL DEFAULT 0, z REAL DEFAULT 0, "
        "facing TEXT DEFAULT 'FrontLeft', created_by TEXT, created_at TEXT)",
        "CREATE TABLE IF NOT EXISTS room_bans ("
        "username TEXT PRIMARY KEY, banned_by TEXT, reason TEXT, "
        "banned_until TEXT, permanent INTEGER DEFAULT 0, created_at TEXT)",
        "CREATE TABLE IF NOT EXISTS room_warnings ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, warned_by TEXT, "
        "reason TEXT, created_at TEXT, active INTEGER DEFAULT 1)",
        "CREATE TABLE IF NOT EXISTS room_welcome_seen ("
        "username TEXT PRIMARY KEY, welcomed INTEGER DEFAULT 0, "
        "welcomed_at TEXT, last_seen_at TEXT)",
        "CREATE TABLE IF NOT EXISTS room_interval_messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT, "
        "interval_minutes INTEGER DEFAULT 10, enabled INTEGER DEFAULT 1, "
        "created_by TEXT, created_at TEXT, last_sent_at TEXT)",
        "CREATE TABLE IF NOT EXISTS room_social_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, "
        "actor_username TEXT, target_username TEXT, action TEXT, message TEXT)",
        "CREATE TABLE IF NOT EXISTS room_follow_state ("
        "bot_id TEXT PRIMARY KEY, target_username TEXT, "
        "enabled INTEGER DEFAULT 0, updated_at TEXT)",
        "CREATE TABLE IF NOT EXISTS room_emote_loops ("
        "username TEXT PRIMARY KEY, emote_id TEXT, started_by TEXT, "
        "enabled INTEGER DEFAULT 1, created_at TEXT)",
        "CREATE TABLE IF NOT EXISTS room_action_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, "
        "actor_username TEXT, target_username TEXT, action TEXT, details TEXT)",
        "CREATE TABLE IF NOT EXISTS room_hearts ("
        "giver_username TEXT, receiver_username TEXT, count INTEGER DEFAULT 0, "
        "last_given_at TEXT, PRIMARY KEY(giver_username, receiver_username))",
        "CREATE TABLE IF NOT EXISTS room_heart_totals ("
        "username TEXT PRIMARY KEY, hearts_received INTEGER DEFAULT 0, "
        "hearts_given INTEGER DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS social_preferences ("
        "username TEXT PRIMARY KEY, social_enabled INTEGER DEFAULT 1)",
        "CREATE TABLE IF NOT EXISTS social_blocks ("
        "username TEXT, blocked_username TEXT, "
        "PRIMARY KEY(username, blocked_username))",
        "CREATE TABLE IF NOT EXISTS bot_modes ("
        "mode_id TEXT PRIMARY KEY, mode_name TEXT, prefix TEXT, title TEXT, "
        "description TEXT, outfit_name TEXT, outfit_data_json TEXT, "
        "enabled INTEGER DEFAULT 1, created_by TEXT, created_at TEXT, updated_at TEXT)",
        "CREATE TABLE IF NOT EXISTS bot_mode_assignments ("
        "bot_id TEXT PRIMARY KEY, bot_username TEXT, mode_id TEXT, "
        "active INTEGER DEFAULT 1, assigned_by TEXT, assigned_at TEXT)",
        "CREATE TABLE IF NOT EXISTS bot_outfit_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, "
        "actor_username TEXT, bot_username TEXT, mode_id TEXT, "
        "outfit_name TEXT, action TEXT, details TEXT)",
        # Mining game tables
        "CREATE TABLE IF NOT EXISTS mining_players ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, "
        "mining_level INTEGER NOT NULL DEFAULT 1, mining_xp INTEGER NOT NULL DEFAULT 0, "
        "tool_level INTEGER NOT NULL DEFAULT 1, "
        "energy INTEGER NOT NULL DEFAULT 100, max_energy INTEGER NOT NULL DEFAULT 100, "
        "total_mines INTEGER NOT NULL DEFAULT 0, total_ores INTEGER NOT NULL DEFAULT 0, "
        "rare_finds INTEGER NOT NULL DEFAULT 0, coins_earned INTEGER NOT NULL DEFAULT 0, "
        "streak_days INTEGER NOT NULL DEFAULT 0, "
        "last_mine_at TEXT, last_daily_bonus TEXT, last_energy_reset TEXT, "
        "luck_boost_until TEXT, xp_boost_until TEXT, "
        "created_at TEXT, updated_at TEXT)",
        "CREATE TABLE IF NOT EXISTS mining_inventory ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, "
        "item_id TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0, "
        "UNIQUE(username, item_id))",
        "CREATE TABLE IF NOT EXISTS mining_items ("
        "item_id TEXT PRIMARY KEY, name TEXT NOT NULL, emoji TEXT NOT NULL DEFAULT '', "
        "rarity TEXT NOT NULL DEFAULT 'common', item_type TEXT NOT NULL DEFAULT 'ore', "
        "sell_value INTEGER NOT NULL DEFAULT 0, drop_enabled INTEGER NOT NULL DEFAULT 1, "
        "created_at TEXT)",
        "CREATE TABLE IF NOT EXISTS mining_settings ("
        "key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')",
        "CREATE TABLE IF NOT EXISTS mining_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, username TEXT, "
        "action TEXT, item_id TEXT, quantity INTEGER DEFAULT 0, "
        "coins INTEGER DEFAULT 0, details TEXT)",
        "CREATE TABLE IF NOT EXISTS mining_events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL, "
        "started_by TEXT, started_at TEXT, ends_at TEXT, "
        "active INTEGER NOT NULL DEFAULT 0)",
        # ── Ore mastery & mining contracts ────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS ore_mastery ("
        "username TEXT NOT NULL, milestone INTEGER NOT NULL, "
        "claimed_at TEXT DEFAULT (datetime('now')), "
        "PRIMARY KEY (username, milestone))",
        "CREATE TABLE IF NOT EXISTS miner_contracts ("
        "username TEXT PRIMARY KEY, contract_id INTEGER NOT NULL, "
        "ore_id TEXT NOT NULL, qty_needed INTEGER NOT NULL, "
        "qty_delivered INTEGER NOT NULL DEFAULT 0, "
        "reward_coins INTEGER NOT NULL, "
        "expires_at TEXT NOT NULL, "
        "created_at TEXT DEFAULT (datetime('now')))",
        # ── Multi-bot module locks ────────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS bot_module_locks ("
        "module TEXT PRIMARY KEY, "
        "bot_id TEXT NOT NULL DEFAULT '', "
        "locked_at TEXT NOT NULL DEFAULT '', "
        "expires_at TEXT NOT NULL DEFAULT '')",
        # ── Multi-bot system ──────────────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS bot_instances ("
        "bot_id TEXT PRIMARY KEY, "
        "bot_username TEXT NOT NULL DEFAULT '', "
        "bot_mode TEXT NOT NULL DEFAULT 'all', "
        "enabled INTEGER NOT NULL DEFAULT 1, "
        "prefix TEXT NOT NULL DEFAULT '', "
        "description TEXT NOT NULL DEFAULT '', "
        "last_seen_at TEXT NOT NULL DEFAULT '', "
        "status TEXT NOT NULL DEFAULT 'offline')",
        "CREATE TABLE IF NOT EXISTS bot_command_ownership ("
        "command TEXT PRIMARY KEY, "
        "module TEXT NOT NULL DEFAULT '', "
        "owner_bot_mode TEXT NOT NULL, "
        "fallback_allowed INTEGER NOT NULL DEFAULT 1)",
        # ── Casino integrity checker ──────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS casino_integrity_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "timestamp TEXT, "
        "actor_username TEXT, "
        "module TEXT, "
        "check_type TEXT, "
        "passed INTEGER, "
        "total_checks INTEGER, "
        "failed_checks INTEGER, "
        "details_json TEXT, "
        "summary TEXT)",
        "CREATE TABLE IF NOT EXISTS casino_integrity_temp ("
        "test_id TEXT PRIMARY KEY, "
        "module TEXT, "
        "state_json TEXT, "
        "created_at TEXT)",
        "CREATE TABLE IF NOT EXISTS casino_message_test_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "timestamp TEXT, "
        "module TEXT, "
        "target_username TEXT, "
        "private INTEGER, "
        "message_preview TEXT, "
        "passed INTEGER, "
        "error TEXT)",
        # ── Module restore announce dedupe locks ──────────────────────────────
        "CREATE TABLE IF NOT EXISTS module_announcement_locks ("
        "module TEXT NOT NULL, "
        "message_key TEXT NOT NULL DEFAULT '', "
        "bot_id TEXT NOT NULL DEFAULT '', "
        "sent_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "expires_at TEXT NOT NULL DEFAULT '', "
        "PRIMARY KEY (module, message_key))",
        # ── Poker hole-card delivery tracking ────────────────────────────────
        "CREATE TABLE IF NOT EXISTS poker_card_delivery ("
        "round_id TEXT NOT NULL, "
        "username TEXT NOT NULL, "
        "cards_sent INTEGER NOT NULL DEFAULT 0, "
        "sent_at TEXT NOT NULL DEFAULT '', "
        "failed_reason TEXT NOT NULL DEFAULT '', "
        "PRIMARY KEY (round_id, username))",
        # ── Poker hole-card secure storage (normalized-username lookup) ───────
        "CREATE TABLE IF NOT EXISTS poker_hole_cards ("
        "round_id TEXT NOT NULL, "
        "username_key TEXT NOT NULL, "
        "display_name TEXT NOT NULL DEFAULT '', "
        "card1 TEXT NOT NULL DEFAULT '', "
        "card2 TEXT NOT NULL DEFAULT '', "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "PRIMARY KEY (round_id, username_key))",
        # ── Extend delivery table with attempt tracking ───────────────────────
        "ALTER TABLE poker_card_delivery ADD COLUMN display_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE poker_card_delivery ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE poker_card_delivery ADD COLUMN last_attempt_at TEXT NOT NULL DEFAULT ''",
        # ── AI assistant pending actions ───────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS ai_pending_actions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id TEXT NOT NULL, "
        "username TEXT NOT NULL DEFAULT '', "
        "proposed_command TEXT NOT NULL DEFAULT '', "
        "proposed_args TEXT NOT NULL DEFAULT '', "
        "human_readable_action TEXT NOT NULL DEFAULT '', "
        "risk_level TEXT NOT NULL DEFAULT 'SAFE', "
        "status TEXT NOT NULL DEFAULT 'pending', "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "expires_at TEXT NOT NULL DEFAULT '')",
        # ── AI assistant action log ────────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS ai_action_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "timestamp TEXT NOT NULL DEFAULT (datetime('now')), "
        "username TEXT NOT NULL DEFAULT '', "
        "intent_text TEXT NOT NULL DEFAULT '', "
        "proposed_command TEXT NOT NULL DEFAULT '', "
        "risk_level TEXT NOT NULL DEFAULT '', "
        "outcome TEXT NOT NULL DEFAULT '')",
        # ── AI delegated tasks (cross-bot outfit/command delegation) ───────────
        "CREATE TABLE IF NOT EXISTS ai_delegated_tasks ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id TEXT NOT NULL, "
        "username TEXT NOT NULL DEFAULT '', "
        "original_text TEXT NOT NULL DEFAULT '', "
        "command_text TEXT NOT NULL DEFAULT '', "
        "owner_mode TEXT NOT NULL DEFAULT 'host', "
        "target_bot_username TEXT NOT NULL DEFAULT '', "
        "human_readable_action TEXT NOT NULL DEFAULT '', "
        "risk_level TEXT NOT NULL DEFAULT 'ADMIN_CONFIRM', "
        "status TEXT NOT NULL DEFAULT 'pending', "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "expires_at TEXT NOT NULL DEFAULT '', "
        "completed_at TEXT, "
        "error TEXT NOT NULL DEFAULT '')",
        # ── Bot spawn locations ───────────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS bot_spawns ("
        "bot_username TEXT PRIMARY KEY, "
        "spawn_name   TEXT NOT NULL DEFAULT '', "
        "x REAL NOT NULL DEFAULT 0, "
        "y REAL NOT NULL DEFAULT 0, "
        "z REAL NOT NULL DEFAULT 0, "
        "facing TEXT NOT NULL DEFAULT 'FrontRight', "
        "set_by TEXT NOT NULL DEFAULT '', "
        "set_at TEXT NOT NULL DEFAULT (datetime('now')))",
        # ── Ore weight records (mining weight system) ─────────────────────────
        "CREATE TABLE IF NOT EXISTS ore_weight_records ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ore_name TEXT NOT NULL DEFAULT '', "
        "rarity TEXT NOT NULL DEFAULT 'common', "
        "weight REAL NOT NULL DEFAULT 0.0, "
        "base_value INTEGER NOT NULL DEFAULT 0, "
        "final_value INTEGER NOT NULL DEFAULT 0, "
        "mxp INTEGER NOT NULL DEFAULT 0, "
        "user_id TEXT NOT NULL DEFAULT '', "
        "username TEXT NOT NULL DEFAULT '', "
        "mined_at TEXT NOT NULL DEFAULT (datetime('now')))",
        # ── Mining weight settings (key-value) ────────────────────────────────
        "CREATE TABLE IF NOT EXISTS mining_weight_settings ("
        "key TEXT PRIMARY KEY, "
        "value TEXT NOT NULL DEFAULT '')",
        # ── Per-bot welcome settings ───────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS bot_welcome_settings ("
        "bot_username TEXT NOT NULL, "
        "key TEXT NOT NULL, "
        "value TEXT NOT NULL DEFAULT '', "
        "updated_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "PRIMARY KEY (bot_username, key))",
        # ── Per-bot welcome seen (dedup per player) ────────────────────────────
        "CREATE TABLE IF NOT EXISTS bot_welcome_seen ("
        "bot_username TEXT NOT NULL, "
        "user_id TEXT NOT NULL, "
        "username TEXT NOT NULL DEFAULT '', "
        "last_sent_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "PRIMARY KEY (bot_username, user_id))",
        # ── Gold tip events ────────────────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS gold_tip_events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "event_id TEXT UNIQUE, "
        "from_user_id TEXT NOT NULL DEFAULT '', "
        "from_username TEXT NOT NULL DEFAULT '', "
        "receiving_bot TEXT NOT NULL DEFAULT '', "
        "gold_amount REAL NOT NULL DEFAULT 0.0, "
        "coins_converted INTEGER NOT NULL DEFAULT 0, "
        "conversion_rate REAL NOT NULL DEFAULT 1000.0, "
        "processed_by TEXT NOT NULL DEFAULT 'bankingbot', "
        "status TEXT NOT NULL DEFAULT 'pending', "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))",
        # ── Owner-forced mining drops ──────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS forced_mining_drops ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "target_username TEXT NOT NULL DEFAULT '', "
        "forced_type TEXT NOT NULL DEFAULT 'rarity', "
        "forced_value TEXT NOT NULL DEFAULT '', "
        "created_by TEXT NOT NULL DEFAULT '', "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "expires_at TEXT NOT NULL DEFAULT '', "
        "used_at TEXT, "
        "status TEXT NOT NULL DEFAULT 'pending')",
        # ── Mining payout logs ────────────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS mining_payout_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "username TEXT NOT NULL, "
        "ore_id TEXT NOT NULL DEFAULT '', "
        "ore_name TEXT NOT NULL DEFAULT '', "
        "rarity TEXT NOT NULL DEFAULT '', "
        "weight_kg REAL, "
        "base_value INTEGER DEFAULT 0, "
        "weight_mult REAL DEFAULT 1.0, "
        "event_mult REAL DEFAULT 1.0, "
        "final_value INTEGER DEFAULT 0, "
        "cap_applied INTEGER DEFAULT 0, "
        "cap_amount INTEGER DEFAULT 0, "
        "mined_at TEXT DEFAULT (datetime('now')))",
        "CREATE INDEX IF NOT EXISTS idx_mpl_uname ON mining_payout_logs(username)",
        "CREATE INDEX IF NOT EXISTS idx_mpl_fval  ON mining_payout_logs(final_value DESC)",
        "CREATE INDEX IF NOT EXISTS idx_mpl_at    ON mining_payout_logs(mined_at DESC)",
        # ── Legendary ore value rescale (idempotent) ──────────────────────────
        "UPDATE mining_items SET sell_value=8000  WHERE item_id='platinum_ore' AND sell_value=3000",
        "UPDATE mining_items SET sell_value=15000 WHERE item_id='emerald'      AND sell_value=5000",
        "UPDATE mining_items SET sell_value=15000 WHERE item_id='ruby'         AND sell_value=5000",
        "UPDATE mining_items SET sell_value=15000 WHERE item_id='sapphire'     AND sell_value=5000",
        # ── AutoMine sessions (restart-safe persistence) ─────────────────────
        "CREATE TABLE IF NOT EXISTS auto_mine_sessions ("
        "user_id TEXT PRIMARY KEY, "
        "username TEXT NOT NULL DEFAULT '', "
        "started_at TEXT NOT NULL DEFAULT '', "
        "max_attempts INTEGER NOT NULL DEFAULT 30, "
        "max_minutes INTEGER NOT NULL DEFAULT 30, "
        "attempts_done INTEGER NOT NULL DEFAULT 0, "
        "status TEXT NOT NULL DEFAULT 'active', "
        "resumed INTEGER NOT NULL DEFAULT 0, "
        "updated_at TEXT NOT NULL DEFAULT '')",
        # ── AutoFish sessions (restart-safe persistence) ──────────────────────
        "CREATE TABLE IF NOT EXISTS auto_fish_sessions ("
        "user_id TEXT PRIMARY KEY, "
        "username TEXT NOT NULL DEFAULT '', "
        "started_at TEXT NOT NULL DEFAULT '', "
        "max_attempts INTEGER NOT NULL DEFAULT 30, "
        "max_minutes INTEGER NOT NULL DEFAULT 30, "
        "attempts_done INTEGER NOT NULL DEFAULT 0, "
        "status TEXT NOT NULL DEFAULT 'active', "
        "resumed INTEGER NOT NULL DEFAULT 0, "
        "updated_at TEXT NOT NULL DEFAULT '')",
        # ── First-find rewards ────────────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS first_find_rewards ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "category TEXT NOT NULL DEFAULT '', "
        "rarity TEXT NOT NULL DEFAULT '', "
        "players_count INTEGER NOT NULL DEFAULT 1, "
        "gold_amount REAL NOT NULL DEFAULT 0, "
        "coin_fallback_amount INTEGER NOT NULL DEFAULT 0, "
        "enabled INTEGER NOT NULL DEFAULT 1, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "updated_at TEXT NOT NULL DEFAULT (datetime('now')))",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_ffr_cat_rar ON first_find_rewards(category, rarity)",
        # ── First-find claims ─────────────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS first_find_claims ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "reward_id INTEGER NOT NULL DEFAULT 0, "
        "user_id TEXT NOT NULL DEFAULT '', "
        "username TEXT NOT NULL DEFAULT '', "
        "category TEXT NOT NULL DEFAULT '', "
        "rarity TEXT NOT NULL DEFAULT '', "
        "claim_rank INTEGER NOT NULL DEFAULT 1, "
        "reward_status TEXT NOT NULL DEFAULT 'pending', "
        "claimed_at TEXT NOT NULL DEFAULT (datetime('now')))",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_ffc_rid_uid ON first_find_claims(reward_id, user_id)",
        # ── First-find pending announcements (cross-bot: emcee + banker) ──────
        "CREATE TABLE IF NOT EXISTS first_find_announce_pending ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "reward_id INTEGER NOT NULL DEFAULT 0, "
        "category TEXT NOT NULL DEFAULT '', "
        "rarity TEXT NOT NULL DEFAULT '', "
        "username TEXT NOT NULL DEFAULT '', "
        "user_id TEXT NOT NULL DEFAULT '', "
        "claim_rank INTEGER NOT NULL DEFAULT 1, "
        "gold_amount REAL NOT NULL DEFAULT 0, "
        "emcee_msg TEXT NOT NULL DEFAULT '', "
        "banker_msg TEXT NOT NULL DEFAULT '', "
        "emcee_done INTEGER NOT NULL DEFAULT 0, "
        "banker_done INTEGER NOT NULL DEFAULT 0, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))",
        # ── Big announce settings (room_settings inserts — safe no-ops) ───────
        "INSERT OR IGNORE INTO room_settings (key, value) VALUES ('big_announce_threshold', 'legendary')",
        "INSERT OR IGNORE INTO room_settings (key, value) VALUES ('big_announce_bot_react_threshold', 'prismatic')",
        "INSERT OR IGNORE INTO room_settings (key, value) VALUES ('big_announce_enabled', '1')",
        # ── BJ pair-bonus + cards-mode settings ──────────────────────────────
        "INSERT OR IGNORE INTO bj_settings (key, value) VALUES ('bj_bonus_pair_pct', '10')",
        "INSERT OR IGNORE INTO bj_settings (key, value) VALUES ('bj_bonus_color_pct', '25')",
        "INSERT OR IGNORE INTO bj_settings (key, value) VALUES ('bj_bonus_perfect_pct', '50')",
        "INSERT OR IGNORE INTO bj_settings (key, value) VALUES ('bj_bonus_cap', '10000')",
        "INSERT OR IGNORE INTO bj_settings (key, value) VALUES ('bj_bonus_enabled', '1')",
        "INSERT OR IGNORE INTO bj_settings (key, value) VALUES ('bj_cards_mode', 'whisper')",
        # ── Add target_user_id to forced_mining_drops ─────────────────────────
        "ALTER TABLE forced_mining_drops ADD COLUMN target_user_id TEXT NOT NULL DEFAULT ''",
        # ── BJ bonus + cards-mode as proper columns ───────────────────────────
        "ALTER TABLE bj_settings ADD COLUMN bj_bonus_enabled     INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE bj_settings ADD COLUMN bj_bonus_pair_pct    INTEGER NOT NULL DEFAULT 10",
        "ALTER TABLE bj_settings ADD COLUMN bj_bonus_color_pct   INTEGER NOT NULL DEFAULT 25",
        "ALTER TABLE bj_settings ADD COLUMN bj_bonus_perfect_pct INTEGER NOT NULL DEFAULT 50",
        "ALTER TABLE bj_settings ADD COLUMN bj_bonus_cap         INTEGER NOT NULL DEFAULT 10000",
        "ALTER TABLE bj_settings ADD COLUMN bj_cards_mode        TEXT    NOT NULL DEFAULT 'whisper'",
        "ALTER TABLE bj_settings  ADD COLUMN bj_insurance_enabled  INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE rbj_settings ADD COLUMN rbj_insurance_enabled INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE rbj_settings ADD COLUMN rbj_surrender_enabled INTEGER NOT NULL DEFAULT 1",
        # ── Owner-forced fishing drops ─────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS forced_fishing_drops ("
        "id              INTEGER PRIMARY KEY AUTOINCREMENT, "
        "target_user_id  TEXT NOT NULL DEFAULT '', "
        "target_username TEXT NOT NULL DEFAULT '', "
        "forced_type     TEXT NOT NULL DEFAULT 'rarity', "
        "forced_value    TEXT NOT NULL DEFAULT '', "
        "created_by      TEXT NOT NULL DEFAULT '', "
        "created_at      TEXT NOT NULL DEFAULT (datetime('now')), "
        "expires_at      TEXT NOT NULL DEFAULT '', "
        "used_at         TEXT, "
        "status          TEXT NOT NULL DEFAULT 'pending')",
        "ALTER TABLE forced_fishing_drops ADD COLUMN last_error TEXT NOT NULL DEFAULT ''",
        # ── First-find race event system ──────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS first_find_races ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "status TEXT NOT NULL DEFAULT 'draft', "
        "category TEXT NOT NULL DEFAULT '', "
        "target_type TEXT NOT NULL DEFAULT 'rarity', "
        "target_value TEXT NOT NULL DEFAULT '', "
        "winners_count INTEGER NOT NULL DEFAULT 1, "
        "gold_amount REAL NOT NULL DEFAULT 0, "
        "started_at TEXT, "
        "ends_at TEXT, "
        "created_by TEXT NOT NULL DEFAULT 'system', "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "updated_at TEXT NOT NULL DEFAULT (datetime('now')))",
        "CREATE TABLE IF NOT EXISTS first_find_race_winners ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "race_id INTEGER NOT NULL DEFAULT 0, "
        "user_id TEXT NOT NULL DEFAULT '', "
        "username TEXT NOT NULL DEFAULT '', "
        "rank INTEGER NOT NULL DEFAULT 1, "
        "category TEXT NOT NULL DEFAULT '', "
        "target_type TEXT NOT NULL DEFAULT 'rarity', "
        "target_value TEXT NOT NULL DEFAULT '', "
        "matched_item_name TEXT NOT NULL DEFAULT '', "
        "matched_rarity TEXT NOT NULL DEFAULT '', "
        "gold_amount REAL NOT NULL DEFAULT 0, "
        "payout_status TEXT NOT NULL DEFAULT 'pending_manual_gold', "
        "payout_error TEXT NOT NULL DEFAULT '', "
        "won_at TEXT NOT NULL DEFAULT (datetime('now')))",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_ffrw_race_user "
        "ON first_find_race_winners(race_id, user_id)",
        # ── Staff audit log ────────────────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS staff_audit_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "actor_user_id TEXT NOT NULL DEFAULT '', "
        "actor_username TEXT NOT NULL DEFAULT '', "
        "action_type TEXT NOT NULL DEFAULT '', "
        "target_user_id TEXT NOT NULL DEFAULT '', "
        "target_username TEXT NOT NULL DEFAULT '', "
        "details TEXT NOT NULL DEFAULT '', "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))",
        # ── Weekly leaderboard snapshots ───────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS weekly_leaderboard_snapshots ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "week_start TEXT NOT NULL DEFAULT '', "
        "week_end TEXT NOT NULL DEFAULT '', "
        "category TEXT NOT NULL DEFAULT '', "
        "rank INTEGER NOT NULL DEFAULT 1, "
        "user_id TEXT NOT NULL DEFAULT '', "
        "username TEXT NOT NULL DEFAULT '', "
        "score TEXT NOT NULL DEFAULT '', "
        "reward_status TEXT NOT NULL DEFAULT 'pending', "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))",
        # ── Weekly reward config ───────────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS weekly_rewards ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "category TEXT NOT NULL DEFAULT '', "
        "rank INTEGER NOT NULL DEFAULT 1, "
        "reward_type TEXT NOT NULL DEFAULT 'coins', "
        "reward_amount INTEGER NOT NULL DEFAULT 0, "
        "enabled INTEGER NOT NULL DEFAULT 1)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_weekly_rewards_cat_rank "
        "ON weekly_rewards(category, rank)",
        # ── Suggestions ────────────────────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS suggestions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id TEXT NOT NULL DEFAULT '', "
        "username TEXT NOT NULL DEFAULT '', "
        "message TEXT NOT NULL DEFAULT '', "
        "status TEXT NOT NULL DEFAULT 'open', "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))",
        # ── Bug reports ────────────────────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS bug_reports ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id TEXT NOT NULL DEFAULT '', "
        "username TEXT NOT NULL DEFAULT '', "
        "message TEXT NOT NULL DEFAULT '', "
        "status TEXT NOT NULL DEFAULT 'open', "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))",
        # ── Event votes ────────────────────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS event_votes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id TEXT NOT NULL DEFAULT '', "
        "username TEXT NOT NULL DEFAULT '', "
        "choice TEXT NOT NULL DEFAULT '', "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_event_votes_user "
        "ON event_votes(user_id)",
        # ── Fish inventory ─────────────────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS fish_inventory ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id TEXT NOT NULL DEFAULT '', "
        "username TEXT NOT NULL DEFAULT '', "
        "fish_name TEXT NOT NULL DEFAULT '', "
        "rarity TEXT NOT NULL DEFAULT 'common', "
        "weight REAL NOT NULL DEFAULT 0, "
        "value INTEGER NOT NULL DEFAULT 0, "
        "sold INTEGER NOT NULL DEFAULT 0, "
        "sold_at TEXT, "
        "caught_at TEXT NOT NULL DEFAULT (datetime('now')))",
        # ── Fish auto-sell settings ────────────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS fish_auto_sell_settings ("
        "user_id TEXT PRIMARY KEY, "
        "username TEXT NOT NULL DEFAULT '', "
        "auto_sell_enabled INTEGER NOT NULL DEFAULT 1, "
        "auto_sell_rare_enabled INTEGER NOT NULL DEFAULT 0, "
        "updated_at TEXT NOT NULL DEFAULT (datetime('now')))",
        # ── Subscriber notification preferences ────────────────────────────────
        "CREATE TABLE IF NOT EXISTS subscriber_notification_prefs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id TEXT NOT NULL DEFAULT '', "
        "username TEXT NOT NULL DEFAULT '', "
        "category TEXT NOT NULL DEFAULT '', "
        "enabled INTEGER NOT NULL DEFAULT 1, "
        "updated_at TEXT NOT NULL DEFAULT (datetime('now')))",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sub_notif_prefs_user_cat "
        "ON subscriber_notification_prefs(user_id, category)",
        # ── Subscriber notification logs ───────────────────────────────────────
        "CREATE TABLE IF NOT EXISTS subscriber_notification_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "category TEXT NOT NULL DEFAULT '', "
        "message TEXT NOT NULL DEFAULT '', "
        "sender_user_id TEXT NOT NULL DEFAULT '', "
        "sender_username TEXT NOT NULL DEFAULT '', "
        "sent_count INTEGER NOT NULL DEFAULT 0, "
        "skipped_count INTEGER NOT NULL DEFAULT 0, "
        "no_conversation_count INTEGER NOT NULL DEFAULT 0, "
        "failed_count INTEGER NOT NULL DEFAULT 0, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))",
        # ── Subscriber notification recipients ─────────────────────────────────
        "CREATE TABLE IF NOT EXISTS subscriber_notification_recipients ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "notification_id INTEGER NOT NULL DEFAULT 0, "
        "user_id TEXT NOT NULL DEFAULT '', "
        "username TEXT NOT NULL DEFAULT '', "
        "category TEXT NOT NULL DEFAULT '', "
        "status TEXT NOT NULL DEFAULT 'sent', "
        "error TEXT NOT NULL DEFAULT '', "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))",
        # ── New: global notification preference per user ───────────────────────
        "CREATE TABLE IF NOT EXISTS subscriber_notification_global ("
        "user_id TEXT PRIMARY KEY, "
        "username TEXT, "
        "global_enabled INTEGER NOT NULL DEFAULT 1, "
        "updated_at TEXT)",
        # ── New: DM conversation tracking (future SDK support) ────────────────
        "CREATE TABLE IF NOT EXISTS subscriber_notification_conversations ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id TEXT NOT NULL, "
        "username TEXT, "
        "bot_name TEXT, "
        "conversation_id TEXT, "
        "can_dm INTEGER NOT NULL DEFAULT 0, "
        "last_seen_in_room_at TEXT, "
        "last_dm_seen_at TEXT, "
        "updated_at TEXT, "
        "created_at TEXT)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sub_notif_conv_user_bot "
        "ON subscriber_notification_conversations(user_id, bot_name)",
        # ── ALTER TABLE: add new columns to existing logs table ───────────────
        "ALTER TABLE subscriber_notification_logs ADD COLUMN sent_dm_count INTEGER DEFAULT 0",
        "ALTER TABLE subscriber_notification_logs ADD COLUMN sent_whisper_count INTEGER DEFAULT 0",
        "ALTER TABLE subscriber_notification_logs ADD COLUMN unsupported_sdk_count INTEGER DEFAULT 0",
        "ALTER TABLE subscriber_notification_logs ADD COLUMN send_type TEXT DEFAULT 'normal'",
        # ── ALTER TABLE: add new columns to existing recipients table ─────────
        "ALTER TABLE subscriber_notification_recipients ADD COLUMN delivery_method TEXT DEFAULT 'none'",
        "ALTER TABLE subscriber_notification_recipients ADD COLUMN subscribed INTEGER DEFAULT 0",
        "ALTER TABLE subscriber_notification_recipients ADD COLUMN category_enabled INTEGER DEFAULT 0",
        "ALTER TABLE subscriber_notification_recipients ADD COLUMN global_enabled INTEGER DEFAULT 1",
        # ── event_history: add skipped_by for skip/cancel tracking ────────────
        "ALTER TABLE event_history ADD COLUMN skipped_by TEXT DEFAULT ''",
        # ── subscriber_notification_logs: sender bot tracking ─────────────────
        "ALTER TABLE subscriber_notification_logs ADD COLUMN sender_bot_name TEXT DEFAULT ''",
        "ALTER TABLE subscriber_notification_logs ADD COLUMN original_sender_bot_name TEXT DEFAULT ''",
        "ALTER TABLE subscriber_notification_logs ADD COLUMN fallback_used INTEGER DEFAULT 0",
        # ── subscriber_notification_logs: per-method delivery counts ──────────
        "ALTER TABLE subscriber_notification_logs ADD COLUMN sent_bulk_dm_count INTEGER DEFAULT 0",
        "ALTER TABLE subscriber_notification_logs ADD COLUMN sent_conv_dm_count INTEGER DEFAULT 0",
        "ALTER TABLE subscriber_notification_logs ADD COLUMN no_delivery_route_count INTEGER DEFAULT 0",
        # ── subscriber_notification_recipients: sender bot tracking ───────────
        "ALTER TABLE subscriber_notification_recipients ADD COLUMN sender_bot_name TEXT DEFAULT ''",
        "ALTER TABLE subscriber_notification_recipients ADD COLUMN original_sender_bot_name TEXT DEFAULT ''",
        "ALTER TABLE subscriber_notification_recipients ADD COLUMN fallback_used INTEGER DEFAULT 0",
        # ── QoL / debug tables ────────────────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS player_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT '',
            username TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS staff_todo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_by_user_id TEXT NOT NULL DEFAULT '',
            created_by_username TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS known_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue TEXT NOT NULL DEFAULT '',
            added_by_username TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS bot_update_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note TEXT NOT NULL DEFAULT '',
            added_by_username TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS pending_coin_rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT '',
            username TEXT NOT NULL DEFAULT '',
            amount INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            claimed_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS bot_maintenance_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT 'global',
            target TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            set_by_user_id TEXT NOT NULL DEFAULT '',
            set_by_username TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(scope, target)
        )""",
        # ── Teleport / tag / role-spawn system ───────────────────────────────
        "ALTER TABLE room_spawns ADD COLUMN permission TEXT DEFAULT 'everyone'",
        """CREATE TABLE IF NOT EXISTS role_spawns (
            role    TEXT PRIMARY KEY,
            x       REAL NOT NULL DEFAULT 0,
            y       REAL NOT NULL DEFAULT 0,
            z       REAL NOT NULL DEFAULT 0,
            facing  TEXT NOT NULL DEFAULT 'FrontLeft',
            set_by  TEXT NOT NULL DEFAULT '',
            set_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS room_tags (
            tag_name          TEXT PRIMARY KEY,
            created_by        TEXT NOT NULL DEFAULT '',
            allow_member_edit INTEGER NOT NULL DEFAULT 0,
            spawn_x           REAL,
            spawn_y           REAL,
            spawn_z           REAL,
            spawn_facing      TEXT DEFAULT 'FrontLeft',
            created_at        TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS room_tag_members (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_name  TEXT NOT NULL DEFAULT '',
            user_id   TEXT NOT NULL DEFAULT '',
            username  TEXT NOT NULL DEFAULT '',
            added_by  TEXT NOT NULL DEFAULT '',
            added_at  TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(tag_name, user_id)
        )""",
        # ── Economy audit log ─────────────────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS economy_audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_username TEXT NOT NULL DEFAULT '',
            action_type    TEXT NOT NULL DEFAULT '',
            game           TEXT NOT NULL DEFAULT '',
            setting        TEXT NOT NULL DEFAULT '',
            old_value      TEXT NOT NULL DEFAULT '',
            new_value      TEXT NOT NULL DEFAULT '',
            created_at     TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        # ── Game prices table ─────────────────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS game_prices (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            game       TEXT NOT NULL DEFAULT '',
            setting    TEXT NOT NULL DEFAULT '',
            value      INTEGER NOT NULL DEFAULT 0,
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(game, setting)
        )""",
        # ── Persistent BJ shoe state (separate from casino_active_tables) ────
        """CREATE TABLE IF NOT EXISTS blackjack_shoe_state (
            game                TEXT PRIMARY KEY,
            shoe_json           TEXT NOT NULL DEFAULT '[]',
            decks_count         INTEGER NOT NULL DEFAULT 6,
            cards_remaining     INTEGER NOT NULL DEFAULT 0,
            last_saved_at       TEXT NOT NULL DEFAULT '',
            loaded_from_restart INTEGER NOT NULL DEFAULT 0,
            rebuild_reason      TEXT NOT NULL DEFAULT ''
        )""",
        # ── Party Tip Wallet system (ChillTopiaMC) ────────────────────────────
        """CREATE TABLE IF NOT EXISTS party_tippers (
            id          INTEGER PRIMARY KEY,
            user_id     TEXT DEFAULT '',
            username    TEXT UNIQUE,
            added_by    TEXT DEFAULT '',
            added_at    TEXT DEFAULT '',
            expires_at  TEXT DEFAULT '',
            daily_used  INTEGER DEFAULT 0,
            last_reset  TEXT DEFAULT ''
        )""",
        """CREATE TABLE IF NOT EXISTS party_tip_log (
            id            INTEGER PRIMARY KEY,
            tipper_id     TEXT DEFAULT '',
            tipper_name   TEXT DEFAULT '',
            receiver_id   TEXT DEFAULT '',
            receiver_name TEXT DEFAULT '',
            amount        INTEGER DEFAULT 0,
            wallet_before INTEGER DEFAULT 0,
            wallet_after  INTEGER DEFAULT 0,
            party_mode    TEXT DEFAULT 'ON',
            result        TEXT DEFAULT '',
            note          TEXT DEFAULT '',
            created_at    TEXT DEFAULT ''
        )""",
        """CREATE TABLE IF NOT EXISTS p2p_gold_tip_logs (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id          TEXT UNIQUE,
            sender_id         TEXT NOT NULL DEFAULT '',
            sender_username   TEXT NOT NULL DEFAULT '',
            receiver_id       TEXT NOT NULL DEFAULT '',
            receiver_username TEXT NOT NULL DEFAULT '',
            amount            REAL NOT NULL DEFAULT 0.0,
            source            TEXT NOT NULL DEFAULT 'p2p_gold_tip',
            created_at        TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        "ALTER TABLE big_announcement_logs ADD COLUMN weight_str  TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE big_announcement_logs ADD COLUMN value_str   TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE big_announcement_logs ADD COLUMN xp_str      TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE big_announcement_logs ADD COLUMN item_emoji  TEXT NOT NULL DEFAULT ''",
        # ── Badge P2P trade system (3.1F) ─────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS badge_trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_a_id       TEXT NOT NULL DEFAULT '',
            user_a_name     TEXT NOT NULL DEFAULT '',
            user_b_id       TEXT NOT NULL DEFAULT '',
            user_b_name     TEXT NOT NULL DEFAULT '',
            status          TEXT NOT NULL DEFAULT 'active',
            user_a_confirmed INTEGER NOT NULL DEFAULT 0,
            user_b_confirmed INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at      TEXT NOT NULL DEFAULT (datetime('now','+5 minutes'))
        )""",
        """CREATE TABLE IF NOT EXISTS badge_trade_items (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id         INTEGER NOT NULL DEFAULT 0,
            user_id          TEXT NOT NULL DEFAULT '',
            badge_id         TEXT NOT NULL DEFAULT '',
            emoji            TEXT NOT NULL DEFAULT '',
            UNIQUE(trade_id, user_id)
        )""",
        """CREATE TABLE IF NOT EXISTS badge_trade_coins (
            trade_id         INTEGER NOT NULL DEFAULT 0,
            user_id          TEXT NOT NULL DEFAULT '',
            amount           INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(trade_id, user_id)
        )""",
        # ── Collection book (3.1H) ─────────────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS player_collection (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         TEXT    NOT NULL DEFAULT '',
            username        TEXT    NOT NULL DEFAULT '',
            collection_type TEXT    NOT NULL DEFAULT '',
            item_key        TEXT    NOT NULL DEFAULT '',
            item_name       TEXT    NOT NULL DEFAULT '',
            rarity          TEXT    NOT NULL DEFAULT 'common',
            first_seen_at   TEXT    NOT NULL DEFAULT (datetime('now')),
            last_seen_at    TEXT    NOT NULL DEFAULT (datetime('now')),
            count           INTEGER NOT NULL DEFAULT 0,
            best_value      INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, collection_type, item_key)
        )""",
        # ── Auto-session summaries (3.1H hotfix) ───────────────────────────────
        """CREATE TABLE IF NOT EXISTS auto_session_summaries (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      TEXT    NOT NULL DEFAULT '',
            username     TEXT    NOT NULL DEFAULT '',
            summary_type TEXT    NOT NULL DEFAULT '',
            summary_text TEXT    NOT NULL DEFAULT '',
            created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, summary_type)
        )""",
        # ── Player DM conversation_ids (3.1H hotfix) ───────────────────────────
        """CREATE TABLE IF NOT EXISTS player_dm_conversations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         TEXT    NOT NULL DEFAULT '',
            username        TEXT    NOT NULL DEFAULT '',
            conversation_id TEXT    NOT NULL DEFAULT '',
            bot_name        TEXT    NOT NULL DEFAULT '',
            stale           INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, bot_name)
        )""",
        # ── Player active boosts / potions (3.1I) ─────────────────────────────
        """CREATE TABLE IF NOT EXISTS player_active_boosts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       TEXT    NOT NULL DEFAULT '',
            username      TEXT    NOT NULL DEFAULT '',
            boost_type    TEXT    NOT NULL DEFAULT '',
            target_system TEXT    NOT NULL DEFAULT '',
            amount        REAL    NOT NULL DEFAULT 0,
            expires_at    TEXT    NOT NULL DEFAULT '',
            source        TEXT    NOT NULL DEFAULT '',
            created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        )""",
        # ── Room-wide active boosts (3.1I) ────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS room_active_boosts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            target_system TEXT    NOT NULL DEFAULT '',
            boost_type    TEXT    NOT NULL DEFAULT '',
            amount        REAL    NOT NULL DEFAULT 0,
            expires_at    TEXT    NOT NULL DEFAULT '',
            source        TEXT    NOT NULL DEFAULT '',
            created_by    TEXT    NOT NULL DEFAULT '',
            created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        )""",
        # ── Luxe Tickets premium economy (3.1I ADDON) ─────────────────────────
        """CREATE TABLE IF NOT EXISTS premium_balances (
            user_id       TEXT    PRIMARY KEY,
            username      TEXT    NOT NULL DEFAULT '',
            luxe_tickets  INTEGER NOT NULL DEFAULT 0,
            updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS premium_transactions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       TEXT    NOT NULL DEFAULT '',
            username      TEXT    NOT NULL DEFAULT '',
            type          TEXT    NOT NULL DEFAULT '',
            amount        INTEGER NOT NULL DEFAULT 0,
            currency      TEXT    NOT NULL DEFAULT 'luxe',
            details       TEXT    NOT NULL DEFAULT '',
            created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS premium_settings (
            key           TEXT    PRIMARY KEY,
            value         TEXT    NOT NULL DEFAULT '',
            updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        )""",
        # Stackable Luxe auto time (3.1I UPDATE)
        """CREATE TABLE IF NOT EXISTS luxe_auto_time (
            user_id          TEXT    NOT NULL,
            username         TEXT    NOT NULL DEFAULT '',
            auto_type        TEXT    NOT NULL,
            remaining_seconds INTEGER NOT NULL DEFAULT 0,
            updated_at       TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, auto_type)
        )""",
        # ── Numbered Coin Pack config ──────────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS coin_packs (
            pack_id           INTEGER PRIMARY KEY,
            ticket_cost       INTEGER NOT NULL DEFAULT 0,
            chillcoins_amount INTEGER NOT NULL DEFAULT 0,
            enabled           INTEGER NOT NULL DEFAULT 1,
            updated_at        TEXT    NOT NULL DEFAULT (datetime('now'))
        )""",
        # ── Luxe Ticket audit log ─────────────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS luxe_ticket_logs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            action          TEXT    NOT NULL DEFAULT '',
            user_id         TEXT    NOT NULL DEFAULT '',
            username        TEXT    NOT NULL DEFAULT '',
            target_user_id  TEXT    NOT NULL DEFAULT '',
            target_username TEXT    NOT NULL DEFAULT '',
            amount          INTEGER NOT NULL DEFAULT 0,
            balance_after   INTEGER NOT NULL DEFAULT 0,
            reason          TEXT    NOT NULL DEFAULT '',
            ref_id          TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_ltl_user_id ON luxe_ticket_logs(user_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_ltl_ref_id  ON luxe_ticket_logs(ref_id)",
        # ── Manual-subscribe-only notification subscriptions ──────────────────
        """CREATE TABLE IF NOT EXISTS notification_subscriptions (
            user_id       TEXT PRIMARY KEY,
            username      TEXT NOT NULL DEFAULT '',
            subscribed    INTEGER NOT NULL DEFAULT 0,
            events        INTEGER NOT NULL DEFAULT 1,
            games         INTEGER NOT NULL DEFAULT 1,
            announcements INTEGER NOT NULL DEFAULT 1,
            promos        INTEGER NOT NULL DEFAULT 1,
            tips          INTEGER NOT NULL DEFAULT 0,
            source        TEXT NOT NULL DEFAULT 'manual',
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_ns_subscribed ON notification_subscriptions(subscribed)",
        # ── Notification action log ────────────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS notification_action_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            action     TEXT NOT NULL DEFAULT '',
            user_id    TEXT NOT NULL DEFAULT '',
            username   TEXT NOT NULL DEFAULT '',
            category   TEXT NOT NULL DEFAULT '',
            details    TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        # ── Badge wishlist (Final Features) ───────────────────────────────────
        """CREATE TABLE IF NOT EXISTS badge_wishlist (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            username   TEXT DEFAULT '',
            badge_id   TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, badge_id)
        )""",
        # ── Badge starter claims (Final Features) ─────────────────────────────
        """CREATE TABLE IF NOT EXISTS badge_claims (
            user_id    TEXT PRIMARY KEY,
            username   TEXT DEFAULT '',
            badge_id   TEXT DEFAULT '',
            claimed_at TEXT DEFAULT (datetime('now'))
        )""",
        # ── Title V2 tables ────────────────────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS title_catalog (
            title_id         TEXT PRIMARY KEY,
            display_name     TEXT DEFAULT '',
            tier             TEXT DEFAULT 'Common',
            source           TEXT DEFAULT 'Shop',
            price            INTEGER DEFAULT 0,
            buyable          INTEGER DEFAULT 0,
            active           INTEGER DEFAULT 1,
            secret           INTEGER DEFAULT 0,
            requirement_type TEXT DEFAULT '',
            requirement_value INTEGER DEFAULT 0,
            category         TEXT DEFAULT '',
            perks_json       TEXT DEFAULT '{}',
            created_at       TEXT DEFAULT (datetime('now')),
            updated_at       TEXT DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS user_titles (
            user_id     TEXT NOT NULL,
            username    TEXT DEFAULT '',
            title_id    TEXT NOT NULL,
            source      TEXT DEFAULT 'Shop',
            unlocked_at TEXT DEFAULT (datetime('now')),
            expires_at  TEXT DEFAULT '',
            PRIMARY KEY(user_id, title_id)
        )""",
        """CREATE TABLE IF NOT EXISTS user_title_stats (
            user_id                    TEXT PRIMARY KEY,
            username                   TEXT DEFAULT '',
            fish_caught                INTEGER DEFAULT 0,
            ores_mined                 INTEGER DEFAULT 0,
            casino_hands_played        INTEGER DEFAULT 0,
            casino_hands_won           INTEGER DEFAULT 0,
            casino_lifetime_wagered    INTEGER DEFAULT 0,
            casino_lifetime_won        INTEGER DEFAULT 0,
            casino_biggest_win         INTEGER DEFAULT 0,
            blackjack_wins             INTEGER DEFAULT 0,
            poker_wins                 INTEGER DEFAULT 0,
            poker_allin_wins           INTEGER DEFAULT 0,
            poker_royal_flush_wins     INTEGER DEFAULT 0,
            lifetime_gold_tipped       INTEGER DEFAULT 0,
            lifetime_chillcoins_earned INTEGER DEFAULT 0,
            lifetime_chillcoins_spent  INTEGER DEFAULT 0,
            room_visit_days            INTEGER DEFAULT 0,
            room_join_count            INTEGER DEFAULT 0,
            last_visit_date            TEXT DEFAULT '',
            minigames_played           INTEGER DEFAULT 0,
            minigames_won              INTEGER DEFAULT 0,
            times_jailed               INTEGER DEFAULT 0,
            players_jailed             INTEGER DEFAULT 0,
            bails_paid                 INTEGER DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS title_logs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            action          TEXT DEFAULT '',
            user_id         TEXT DEFAULT '',
            username        TEXT DEFAULT '',
            target_user_id  TEXT DEFAULT '',
            target_username TEXT DEFAULT '',
            title_id        TEXT DEFAULT '',
            details         TEXT DEFAULT '',
            created_at      TEXT DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS title_loadouts (
            user_id    TEXT NOT NULL,
            name       TEXT NOT NULL,
            title_id   TEXT DEFAULT '',
            badge_id   TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY(user_id, name)
        )""",
    ]:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass

    # Commit and close before seed functions — each seed opens its own
    # connection; leaving this one open with a pending write transaction
    # would cause the second writer to block indefinitely in WAL mode.
    conn.commit()
    conn.close()

    # Seed default emoji badge catalog (idempotent)
    seed_emoji_badges()
    seed_room_settings()
    from modules.bot_modes import seed_bot_modes as _seed_bot_modes
    _seed_bot_modes()

    # Seed default numbered coin packs (idempotent)
    init_default_coin_packs()

    # Seed mining ore catalog (idempotent)
    seed_mining_items()

    # Seed event catalog — done here via deferred import to avoid circular deps
    try:
        from modules.events import EVENT_CATALOG, _DEFAULT_AUTO_POOL as _pool_ids
        seed_event_catalog_data(EVENT_CATALOG, _pool_ids)
    except Exception as _exc:
        print(f"[DB] seed_event_catalog skipped: {_exc!r}")

    # Reopen for remaining data migrations
    conn = get_connection()

    # Seed new tip settings defaults (INSERT OR IGNORE — safe to run every boot)
    for key, val in [("tip_auto_sub", "1"), ("tip_resubscribe", "0")]:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO tip_settings (key, value) VALUES (?, ?)", (key, val)
            )
        except Exception:
            pass

    # Data migrations — safe no-ops if already applied or no matching rows exist
    conn.execute("UPDATE users SET first_seen = datetime('now') WHERE first_seen IS NULL")
    conn.execute("UPDATE bj_settings  SET lobby_countdown = 15 WHERE id = 1 AND lobby_countdown = 60")
    conn.execute("UPDATE rbj_settings SET lobby_countdown = 15 WHERE id = 1 AND lobby_countdown = 60")
    conn.execute("UPDATE owned_items      SET item_id = 'elite'                                              WHERE item_id = 'room_legend'")
    conn.execute("UPDATE purchase_history SET item_id = 'elite'                                              WHERE item_id = 'room_legend'")
    conn.execute("UPDATE users            SET equipped_title = '[Elite]', equipped_title_id = 'elite'        WHERE equipped_title_id = 'room_legend'")

    # ── Mining economy rebalance (2025-05) ───────────────────────────────────
    # Common sell values roughly doubled; uncommon raised ~1.7×.
    # Rare and above are intentionally unchanged.
    # These are safe no-ops if the row already holds the new value.
    conn.execute("UPDATE mining_items SET sell_value = 12  WHERE item_id = 'stone'      AND sell_value <  12")
    conn.execute("UPDATE mining_items SET sell_value = 18  WHERE item_id = 'coal'       AND sell_value <  18")
    conn.execute("UPDATE mining_items SET sell_value = 28  WHERE item_id = 'copper_ore' AND sell_value <  28")
    conn.execute("UPDATE mining_items SET sell_value = 40  WHERE item_id = 'iron_ore'   AND sell_value <  40")
    conn.execute("UPDATE mining_items SET sell_value = 55  WHERE item_id = 'tin_ore'    AND sell_value <  55")
    conn.execute("UPDATE mining_items SET sell_value = 65  WHERE item_id = 'lead_ore'   AND sell_value <  65")
    conn.execute("UPDATE mining_items SET sell_value = 75  WHERE item_id = 'zinc_ore'   AND sell_value <  75")
    conn.execute("UPDATE mining_items SET sell_value = 100 WHERE item_id = 'quartz'     AND sell_value < 100")

    # ── Emoji badge price rebalance (2026-05) ────────────────────────────────
    # common 500c→1,500c; uncommon 2,500c→7,500c; rare 10,000c→25,000c.
    # epic/legendary/mythic are intentionally unchanged.
    # Condition guards make these safe no-ops if already at the new value.
    conn.execute("UPDATE emoji_badges SET price = 1500  WHERE rarity = 'common'   AND price < 1500  AND source = 'shop'")
    conn.execute("UPDATE emoji_badges SET price = 7500  WHERE rarity = 'uncommon' AND price < 7500  AND source = 'shop'")
    # Rare raised 10,000c → 25,000c so the rarity ladder keeps a consistent ~3× step
    # between each tier (uncommon 7,500 → rare 25,000 = 3.3×, matching rare→epic 2×,
    # epic→legendary 3×, legendary→mythic 3.3×).  Previously the gap was only 1.3×.
    conn.execute("UPDATE emoji_badges SET price = 25000 WHERE rarity = 'rare'     AND price < 25000 AND source = 'shop'")

    # ── Goldtip command ownership hard-fix (2026-05) ──────────────────────────
    # Force goldtip + aliases to banker in the DB override table so that even
    # if a previous /setcommandowner stored 'eventhost', BankerBot wins.
    # fallback_allowed=0 means host/eventhost CANNOT fall back on these commands.
    _GOLDTIP_CMDS_FIX = [
        ("goldtip",    "goldtip", "banker", 0),
        ("tipgold",    "goldtip", "banker", 0),
        ("goldreward", "goldtip", "banker", 0),
        ("rewardgold", "goldtip", "banker", 0),
    ]
    for _gcmd, _gmod, _gmode, _gfb in _GOLDTIP_CMDS_FIX:
        try:
            conn.execute(
                "INSERT INTO bot_command_ownership "
                "(command, module, owner_bot_mode, fallback_allowed) "
                "VALUES (?,?,?,?) ON CONFLICT(command) DO UPDATE SET "
                "owner_bot_mode=excluded.owner_bot_mode, "
                "fallback_allowed=excluded.fallback_allowed",
                (_gcmd, _gmod, _gmode, _gfb))
        except Exception:
            pass

    # ── Force-correct gold rain + msgcap command ownership (runs every startup) ─
    # Fixes any stale DB record that previously mapped goldrain → eventhost.
    # ON CONFLICT DO UPDATE overwrites the owner unconditionally.
    _GOLDRAIN_OWNERSHIP_FIX = [
        # Main goldrain + aliases → BankerBot
        ("goldrain",          "goldrain", "banker", 0),
        ("raingold",          "goldrain", "banker", 0),
        ("goldstorm",         "goldrain", "banker", 0),
        ("golddrop",          "goldrain", "banker", 0),
        ("goldrainstatus",    "goldrain", "banker", 0),
        ("cancelgoldrain",    "goldrain", "banker", 0),
        ("goldrainhistory",   "goldrain", "banker", 0),
        ("goldraininterval",  "goldrain", "banker", 0),
        ("setgoldraininterval","goldrain","banker", 0),
        ("goldrainreplace",   "goldrain", "banker", 0),
        ("goldrainpace",      "goldrain", "banker", 0),
        ("setgoldrainpace",   "goldrain", "banker", 0),
        ("goldrainall",       "goldrain", "banker", 0),
        ("goldraineligible",  "goldrain", "banker", 0),
        ("goldrainrole",      "goldrain", "banker", 0),
        ("goldrainvip",       "goldrain", "banker", 0),
        ("goldraintitle",     "goldrain", "banker", 0),
        ("goldrainbadge",     "goldrain", "banker", 0),
        ("goldrainlist",      "goldrain", "banker", 0),
        ("setgoldrainstaff",  "goldrain", "banker", 0),
        ("setgoldrainmax",    "goldrain", "banker", 0),
        ("goldtipbots",       "goldtip",  "banker", 0),
        ("tipall",            "party_tip", "host",   0),
        ("goldtipall",        "goldtip",  "banker", 0),
        # Msg cap → EmceeBot (host)
        ("msgcap",            "msg_cap",  "host",   1),
        ("setmsgcap",         "msg_cap",  "host",   0),
    ]
    for _gcmd, _gmod, _gmode, _gfb in _GOLDRAIN_OWNERSHIP_FIX:
        try:
            conn.execute(
                "INSERT INTO bot_command_ownership "
                "(command, module, owner_bot_mode, fallback_allowed) "
                "VALUES (?,?,?,?) ON CONFLICT(command) DO UPDATE SET "
                "owner_bot_mode=excluded.owner_bot_mode, "
                "fallback_allowed=excluded.fallback_allowed",
                (_gcmd, _gmod, _gmode, _gfb))
        except Exception:
            pass

    # ── Seed big announcement default settings ────────────────────────────────
    _BIG_ANN_DEFAULTS = [
        ("mining",  "common",    "off"),
        ("mining",  "rare",      "off"),
        ("mining",  "epic",      "off"),
        ("mining",  "legendary", "miner_only"),
        ("mining",  "mythic",    "miner_only"),
        ("mining",  "ultra_rare","miner_only"),
        ("mining",  "prismatic", "all_bots"),
        ("mining",  "exotic",    "all_bots"),
        ("fishing", "common",    "off"),
        ("fishing", "rare",      "off"),
        ("fishing", "epic",      "off"),
        ("fishing", "legendary", "fishing_only"),
        ("fishing", "mythic",    "fishing_only"),
        ("fishing", "ultra_rare","fishing_only"),
        ("fishing", "prismatic", "all_bots"),
        ("fishing", "exotic",    "all_bots"),
    ]
    for _cat, _rar, _mode in _BIG_ANN_DEFAULTS:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO big_announcement_settings "
                "(category, rarity, routing_mode) VALUES (?,?,?)",
                (_cat, _rar, _mode))
        except Exception:
            pass
    _BIG_BOT_DEFAULTS = [
        ("bankingbot",   1), ("eventbot",    1), ("emceebot",  1),
        ("miningbot",    1), ("fishingbot",  1), ("djbot",     0),
        ("securitybot",  0), ("pokerbot",    0), ("blackjackbot", 0),
    ]
    for _bname, _en in _BIG_BOT_DEFAULTS:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO big_announcement_bot_reactions "
                "(bot_name, enabled) VALUES (?,?)",
                (_bname, _en))
        except Exception:
            pass

    # 3.1B — best_streak column for daily streak tracking
    try:
        conn.execute(
            "ALTER TABLE daily_claims ADD COLUMN best_streak INTEGER NOT NULL DEFAULT 1"
        )
    except Exception:
        pass

    # 3.1E — enabled flag on emoji_badges (owner can hide badges from shop)
    try:
        conn.execute(
            "ALTER TABLE emoji_badges ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"
        )
    except Exception:
        pass

    # 3.1I Economy Balance — player auto-convert setting table
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS player_auto_convert (
                user_id    TEXT PRIMARY KEY,
                username   TEXT NOT NULL DEFAULT '',
                enabled    INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT ''
            )
        """)
    except Exception:
        pass

    # 3.1J Player Retention — missions, collection milestones, season points
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS player_missions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                username    TEXT NOT NULL DEFAULT '',
                mission_key TEXT NOT NULL,
                period_key  TEXT NOT NULL,
                progress    INTEGER NOT NULL DEFAULT 0,
                claimed     INTEGER NOT NULL DEFAULT 0,
                updated_at  TEXT NOT NULL DEFAULT '',
                UNIQUE(user_id, mission_key, period_key)
            )
        """)
    except Exception:
        pass

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS player_mission_sets (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL,
                username   TEXT NOT NULL DEFAULT '',
                period_key TEXT NOT NULL,
                set_type   TEXT NOT NULL,
                completed  INTEGER NOT NULL DEFAULT 0,
                claimed    INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(user_id, set_type, period_key)
            )
        """)
    except Exception:
        pass

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS collection_milestone_claims (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         TEXT NOT NULL,
                username        TEXT NOT NULL DEFAULT '',
                collection_type TEXT NOT NULL,
                milestone       INTEGER NOT NULL,
                claimed_at      TEXT NOT NULL DEFAULT '',
                UNIQUE(user_id, collection_type, milestone)
            )
        """)
    except Exception:
        pass

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS season_points (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL,
                username   TEXT NOT NULL DEFAULT '',
                season_key TEXT NOT NULL,
                category   TEXT NOT NULL,
                points     INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(user_id, season_key, category)
            )
        """)
    except Exception:
        pass

    # 3.1L — Player profile settings (balance/collection/badge/vip/season/level visibility)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS player_profile_settings (
                user_id               TEXT PRIMARY KEY,
                username              TEXT NOT NULL DEFAULT '',
                balance_visibility    TEXT NOT NULL DEFAULT 'private',
                collection_visibility TEXT NOT NULL DEFAULT 'public',
                badge_visibility      TEXT NOT NULL DEFAULT 'public',
                vip_visibility        TEXT NOT NULL DEFAULT 'public',
                season_visibility     TEXT NOT NULL DEFAULT 'public',
                level_visibility      TEXT NOT NULL DEFAULT 'public',
                updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    except Exception:
        pass

    # 3.1K — Season reward history
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS season_reward_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      TEXT NOT NULL,
                username     TEXT NOT NULL DEFAULT '',
                season_key   TEXT NOT NULL,
                category     TEXT NOT NULL DEFAULT '',
                reward_coins INTEGER NOT NULL DEFAULT 0,
                awarded_by   TEXT NOT NULL DEFAULT '',
                awarded_at   TEXT NOT NULL DEFAULT ''
            )
        """)
    except Exception:
        pass

    # 3.1M — Onboarding / tutorial tracking
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS player_onboarding (
                user_id               TEXT PRIMARY KEY,
                username              TEXT NOT NULL DEFAULT '',
                joined_at             TEXT NOT NULL DEFAULT (datetime('now')),
                welcome_sent          INTEGER NOT NULL DEFAULT 0,
                tutorial_started      INTEGER NOT NULL DEFAULT 0,
                tutorial_completed    INTEGER NOT NULL DEFAULT 0,
                current_step          INTEGER NOT NULL DEFAULT 0,
                starter_reward_claimed INTEGER NOT NULL DEFAULT 0,
                last_reminder_at      TEXT,
                updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    except Exception:
        pass
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS player_tutorial_steps (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      TEXT NOT NULL,
                username     TEXT NOT NULL DEFAULT '',
                step_key     TEXT NOT NULL,
                completed    INTEGER NOT NULL DEFAULT 0,
                reward_claimed INTEGER NOT NULL DEFAULT 0,
                completed_at TEXT,
                UNIQUE(user_id, step_key)
            )
        """)
    except Exception:
        pass
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS onboarding_rewards_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL,
                username   TEXT NOT NULL DEFAULT '',
                reward_key TEXT NOT NULL,
                amount     INTEGER NOT NULL DEFAULT 0,
                currency   TEXT NOT NULL DEFAULT 'coins',
                details    TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    except Exception:
        pass

    # ── 3.1N — moderation + anti-abuse + economy safety ──────────────────────
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS softbans (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          TEXT NOT NULL,
                username         TEXT NOT NULL DEFAULT '',
                banned_by        TEXT NOT NULL DEFAULT '',
                reason           TEXT NOT NULL DEFAULT '',
                duration_minutes INTEGER NOT NULL DEFAULT 60,
                created_at       TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at       TEXT NOT NULL DEFAULT (datetime('now','+60 minutes')),
                active           INTEGER NOT NULL DEFAULT 1
            )
        """)
    except Exception:
        pass
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS economy_transactions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_id      TEXT UNIQUE NOT NULL,
                user_id    TEXT NOT NULL DEFAULT '',
                username   TEXT NOT NULL DEFAULT '',
                currency   TEXT NOT NULL DEFAULT 'chillcoins',
                amount     INTEGER NOT NULL DEFAULT 0,
                direction  TEXT NOT NULL DEFAULT 'credit',
                source     TEXT NOT NULL DEFAULT '',
                details    TEXT NOT NULL DEFAULT '',
                event_id   TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    except Exception:
        pass
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS moderation_logs (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                action_id        TEXT UNIQUE NOT NULL,
                staff_id         TEXT NOT NULL DEFAULT '',
                staff_name       TEXT NOT NULL DEFAULT '',
                target_id        TEXT NOT NULL DEFAULT '',
                target_name      TEXT NOT NULL DEFAULT '',
                action           TEXT NOT NULL DEFAULT '',
                reason           TEXT NOT NULL DEFAULT '',
                duration_minutes INTEGER NOT NULL DEFAULT 0,
                created_at       TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at       TEXT NOT NULL DEFAULT '',
                active           INTEGER NOT NULL DEFAULT 1
            )
        """)
    except Exception:
        pass
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS safety_alerts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id   TEXT UNIQUE NOT NULL,
                user_id    TEXT NOT NULL DEFAULT '',
                username   TEXT NOT NULL DEFAULT '',
                alert_type TEXT NOT NULL DEFAULT '',
                severity   TEXT NOT NULL DEFAULT 'low',
                source     TEXT NOT NULL DEFAULT '',
                details    TEXT NOT NULL DEFAULT '',
                blocked    INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    except Exception:
        pass
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_events (
                event_id   TEXT UNIQUE NOT NULL,
                event_type TEXT NOT NULL DEFAULT '',
                user_id    TEXT NOT NULL DEFAULT '',
                username   TEXT NOT NULL DEFAULT '',
                source     TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    except Exception:
        pass

    # ── 3.1O — lightweight analytics event log ────────────────────────────────
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analytics_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id   TEXT UNIQUE NOT NULL,
                user_id    TEXT NOT NULL DEFAULT '',
                username   TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL DEFAULT '',
                category   TEXT NOT NULL DEFAULT '',
                amount     INTEGER NOT NULL DEFAULT 0,
                currency   TEXT NOT NULL DEFAULT '',
                details    TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    except Exception:
        pass

    # 3.1Q — Beta mode + command error tracking
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS beta_settings (
                key        TEXT UNIQUE NOT NULL,
                value      TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    except Exception:
        pass

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS command_error_logs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       TEXT NOT NULL DEFAULT '',
                username      TEXT NOT NULL DEFAULT '',
                command       TEXT NOT NULL DEFAULT '',
                args          TEXT NOT NULL DEFAULT '',
                error_summary TEXT NOT NULL DEFAULT '',
                traceback     TEXT NOT NULL DEFAULT '',
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                status        TEXT NOT NULL DEFAULT 'open'
            )
        """)
    except Exception:
        pass

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rotating_announcements (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                message    TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    except Exception:
        pass

    # 3.1R — Beta review: add tags/priority/assigned_to to reports
    for _col, _dflt in [
        ("tags",        "''"),
        ("priority",    "'medium'"),
        ("assigned_to", "''"),
    ]:
        try:
            conn.execute(
                f"ALTER TABLE reports ADD COLUMN {_col} TEXT NOT NULL DEFAULT {_dflt}"
            )
        except Exception:
            pass

    # 3.1R — Beta test run log + snapshot store + recommendation store
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS beta_test_runs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id       TEXT UNIQUE NOT NULL,
                started_by   TEXT NOT NULL DEFAULT '',
                started_at   TEXT NOT NULL DEFAULT (datetime('now')),
                status       TEXT NOT NULL DEFAULT 'running',
                summary_json TEXT NOT NULL DEFAULT '{}'
            )
        """)
    except Exception:
        pass

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS beta_review_snapshots (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_type TEXT NOT NULL DEFAULT '',
                range_key     TEXT NOT NULL DEFAULT '',
                summary_json  TEXT NOT NULL DEFAULT '{}',
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    except Exception:
        pass

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS beta_recommendations (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                category       TEXT NOT NULL DEFAULT '',
                severity       TEXT NOT NULL DEFAULT 'low',
                recommendation TEXT NOT NULL DEFAULT '',
                status         TEXT NOT NULL DEFAULT 'open',
                created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                resolved_at    TEXT NOT NULL DEFAULT ''
            )
        """)
    except Exception:
        pass

    # ── 3.1S — Release Candidate + Production Lock tables ────────────────────
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS release_backups (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                backup_name  TEXT    UNIQUE NOT NULL,
                backup_path  TEXT    NOT NULL DEFAULT '',
                created_by   TEXT    NOT NULL DEFAULT '',
                created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
                verified     INTEGER NOT NULL DEFAULT 0,
                details_json TEXT    NOT NULL DEFAULT '{}'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS release_audits (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_type   TEXT NOT NULL DEFAULT '',
                status       TEXT NOT NULL DEFAULT '',
                summary_json TEXT NOT NULL DEFAULT '{}',
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                created_by   TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS release_announcements (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                message           TEXT NOT NULL DEFAULT '',
                announcement_type TEXT NOT NULL DEFAULT '',
                sent_by           TEXT NOT NULL DEFAULT '',
                sent_at           TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    except Exception:
        pass

    # ── 3.2A — Public Launch + Post-Launch Monitoring ─────────────────────────
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS launch_alerts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_type  TEXT NOT NULL DEFAULT '',
                severity    TEXT NOT NULL DEFAULT 'info',
                message     TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                resolved_at TEXT,
                status      TEXT NOT NULL DEFAULT 'open'
            )
        """)
    except Exception:
        pass
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hotfix_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                message     TEXT NOT NULL DEFAULT '',
                created_by  TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    except Exception:
        pass
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS launch_snapshots (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                range_key    TEXT NOT NULL DEFAULT '',
                summary_json TEXT NOT NULL DEFAULT '{}',
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                created_by   TEXT NOT NULL DEFAULT ''
            )
        """)
    except Exception:
        pass

    # 3.2I — Staff notes
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS staff_notes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id   TEXT NOT NULL DEFAULT '',
                target_name TEXT NOT NULL DEFAULT '',
                staff_id    TEXT NOT NULL DEFAULT '',
                staff_name  TEXT NOT NULL DEFAULT '',
                note        TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    except Exception:
        pass

    # 3.4A — Luxe Jail system
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jail_sentences (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                target_user_id      TEXT NOT NULL DEFAULT '',
                target_username     TEXT NOT NULL DEFAULT '',
                jailed_by_user_id   TEXT NOT NULL DEFAULT '',
                jailed_by_username  TEXT NOT NULL DEFAULT '',
                start_ts            REAL NOT NULL DEFAULT 0,
                end_ts              REAL NOT NULL DEFAULT 0,
                duration_seconds    INTEGER NOT NULL DEFAULT 0,
                remaining_seconds   INTEGER NOT NULL DEFAULT 0,
                jail_reason         TEXT NOT NULL DEFAULT 'luxe_jail',
                jail_source         TEXT NOT NULL DEFAULT 'player',
                status              TEXT NOT NULL DEFAULT 'active',
                bail_cost           INTEGER NOT NULL DEFAULT 0,
                created_at          TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    except Exception:
        pass

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jail_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                action          TEXT NOT NULL DEFAULT '',
                target_user_id  TEXT NOT NULL DEFAULT '',
                target_username TEXT NOT NULL DEFAULT '',
                actor_user_id   TEXT NOT NULL DEFAULT '',
                actor_username  TEXT NOT NULL DEFAULT '',
                amount          INTEGER NOT NULL DEFAULT 0,
                details         TEXT NOT NULL DEFAULT '',
                timestamp       TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
    except Exception:
        pass

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# AI assistant helpers
# ---------------------------------------------------------------------------

def create_pending_ai_action(
    user_id: str,
    username: str,
    command: str,
    args_str: str,
    human_readable: str,
    risk_level: str,
) -> int:
    """Create (or replace) a pending AI action for a user. Returns the new row id."""
    from datetime import datetime, timedelta
    now     = datetime.utcnow()
    expires = now + timedelta(seconds=60)
    conn    = get_connection()
    conn.execute(
        "UPDATE ai_pending_actions SET status='cancelled' "
        "WHERE user_id=? AND status='pending'",
        (user_id,),
    )
    cur = conn.execute(
        """INSERT INTO ai_pending_actions
               (user_id, username, proposed_command, proposed_args,
                human_readable_action, risk_level, status, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (
            user_id, username, command, args_str, human_readable, risk_level,
            now.strftime("%Y-%m-%d %H:%M:%S"),
            expires.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_pending_ai_action(user_id: str) -> Optional[dict]:
    """Return the active pending AI action for a user, or None if none/expired."""
    from datetime import datetime
    now  = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    row  = conn.execute(
        """SELECT * FROM ai_pending_actions
           WHERE user_id=? AND status='pending' AND expires_at > ?
           ORDER BY id DESC LIMIT 1""",
        (user_id, now),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def confirm_pending_ai_action(user_id: str) -> Optional[dict]:
    """
    Atomically mark the pending action as 'executing' and return its data.
    Returns None if action is missing, expired, or already claimed (duplicate guard).
    Only the FIRST caller that transitions status pending->executing gets the row.
    """
    from datetime import datetime
    now  = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    # Read the action first
    row = conn.execute(
        "SELECT * FROM ai_pending_actions"
        " WHERE user_id=? AND status='pending' AND expires_at > ?"
        " ORDER BY id DESC LIMIT 1",
        (user_id, now),
    ).fetchone()
    if row is None:
        conn.close()
        return None
    action = dict(row)
    # Atomically claim: only succeeds if status is still 'pending'
    cur = conn.execute(
        "UPDATE ai_pending_actions SET status='executing' WHERE id=? AND status='pending'",
        (action["id"],),
    )
    conn.commit()
    if cur.rowcount == 0:
        # Another concurrent call already claimed this action
        print(f"[AI_CONFIRM] action_id={action['id']} already executed — ignored")
        conn.close()
        return None
    print(f"[AI_CONFIRM] action_id={action['id']} status=executing")
    conn.close()
    return action


def cancel_pending_ai_action(user_id: str) -> bool:
    """Cancel any pending AI action for a user. Returns True if one was found."""
    conn = get_connection()
    cur  = conn.execute(
        "UPDATE ai_pending_actions SET status='cancelled' "
        "WHERE user_id=? AND status='pending'",
        (user_id,),
    )
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed


def expire_old_ai_actions() -> int:
    """Expire all past-deadline pending actions. Returns the count expired."""
    from datetime import datetime
    now  = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cur  = conn.execute(
        "UPDATE ai_pending_actions SET status='expired' "
        "WHERE status='pending' AND expires_at <= ?",
        (now,),
    )
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n


def log_ai_action(
    username: str,
    intent_text: str,
    proposed_command: str,
    risk_level: str,
    outcome: str,
) -> None:
    """Append one row to ai_action_logs (fire-and-forget; never raises)."""
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO ai_action_logs
                   (username, intent_text, proposed_command, risk_level, outcome)
               VALUES (?, ?, ?, ?, ?)""",
            (username, intent_text[:200], proposed_command[:100], risk_level, outcome),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# AI delegated tasks (cross-bot delegation for outfit and other commands)
# ---------------------------------------------------------------------------

def create_delegated_task(
    user_id: str,
    username: str,
    original_text: str,
    command_text: str,
    owner_mode: str,
    target_bot_username: str,
    human_readable_action: str,
    risk_level: str = "ADMIN_CONFIRM",
) -> int:
    """Create a pending delegated task for a target bot. Returns row id."""
    from datetime import datetime, timedelta
    now     = datetime.utcnow()
    expires = now + timedelta(seconds=90)
    conn    = get_connection()
    cur     = conn.execute(
        """INSERT INTO ai_delegated_tasks
               (user_id, username, original_text, command_text, owner_mode,
                target_bot_username, human_readable_action, risk_level,
                status, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (
            user_id, username.lower(), original_text[:200], command_text,
            owner_mode, target_bot_username.lower(), human_readable_action,
            risk_level,
            now.strftime("%Y-%m-%d %H:%M:%S"),
            expires.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_pending_delegated_tasks_for_bot(bot_username: str) -> list:
    """Return pending (non-expired) delegated tasks for a given bot username."""
    from datetime import datetime
    now  = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM ai_delegated_tasks
           WHERE target_bot_username=? AND status='pending' AND expires_at > ?
           ORDER BY id ASC""",
        (bot_username.lower(), now),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def complete_delegated_task(task_id: int, error: str = "") -> None:
    """Mark a delegated task as completed (or failed if error given)."""
    from datetime import datetime
    now    = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    status = "failed" if error else "completed"
    conn   = get_connection()
    conn.execute(
        "UPDATE ai_delegated_tasks SET status=?, completed_at=?, error=? WHERE id=?",
        (status, now, error[:200], task_id),
    )
    conn.commit()
    conn.close()


def expire_old_delegated_tasks() -> int:
    """Expire all past-deadline pending delegated tasks. Returns count expired."""
    from datetime import datetime
    now  = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cur  = conn.execute(
        "UPDATE ai_delegated_tasks SET status='expired' "
        "WHERE status='pending' AND expires_at <= ?",
        (now,),
    )
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n


def get_recent_delegated_tasks(limit: int = 10) -> list:
    """Return the most recent AI delegated tasks (all statuses) for /aidelegations."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, username, human_readable_action, owner_mode,
                  target_bot_username, status, created_at, completed_at, error
           FROM ai_delegated_tasks
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Bot spawn helpers
# ---------------------------------------------------------------------------

def get_bot_spawn(bot_username: str) -> Optional[dict]:
    """Return the saved spawn row for a bot username, or None."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM bot_spawns WHERE LOWER(bot_username)=?",
        (bot_username.lower(),),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def set_bot_spawn(
    bot_username: str,
    spawn_name: str,
    x: float,
    y: float,
    z: float,
    facing: str,
    set_by: str,
) -> None:
    """Upsert a spawn location for a bot."""
    from datetime import datetime
    now  = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    conn.execute(
        """INSERT INTO bot_spawns (bot_username, spawn_name, x, y, z, facing, set_by, set_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(bot_username) DO UPDATE SET
               spawn_name=excluded.spawn_name, x=excluded.x, y=excluded.y,
               z=excluded.z, facing=excluded.facing, set_by=excluded.set_by,
               set_at=excluded.set_at""",
        (bot_username.lower(), spawn_name, x, y, z, facing,
         set_by.lower(), now),
    )
    conn.commit()
    conn.close()


def clear_bot_spawn(bot_username: str) -> bool:
    """Delete a bot's saved spawn. Returns True if a row was removed."""
    conn = get_connection()
    cur  = conn.execute(
        "DELETE FROM bot_spawns WHERE LOWER(bot_username)=?",
        (bot_username.lower(),),
    )
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n > 0


def list_bot_spawns() -> list:
    """Return all saved bot spawns."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM bot_spawns ORDER BY set_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_bot_mode_for_username(bot_username: str) -> Optional[str]:
    """Return the bot_mode for a known bot username, or None if not found."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT bot_mode FROM bot_instances WHERE LOWER(bot_username)=? LIMIT 1",
        (bot_username.lower(),),
    ).fetchone()
    conn.close()
    return row["bot_mode"] if row else None


def get_bot_username_for_mode(mode: str) -> Optional[str]:
    """Return the bot_username for the first bot_instance with matching bot_mode.
    Online bots are preferred; falls back to any matching row."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT bot_username FROM bot_instances "
        "WHERE LOWER(bot_mode)=? "
        "ORDER BY (status='online') DESC, last_seen_at DESC LIMIT 1",
        (mode.lower(),),
    ).fetchone()
    conn.close()
    return row["bot_username"] if row else None


def is_bot_mode_online(mode: str) -> bool:
    """Return True if any bot_instance with matching bot_mode is online."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT 1 FROM bot_instances WHERE bot_mode=? AND status='online' LIMIT 1",
        (mode,),
    ).fetchone()
    conn.close()
    return row is not None


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
        SELECT username, balance, xp, level, total_games_won,
               total_coins_earned, tip_coins_earned,
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

# ---------------------------------------------------------------------------
# Display-flags cache — avoids per-call DB hits for badge/title toggles.
# Cleared immediately when /displaybadges or /displaytitles changes a setting.
# ---------------------------------------------------------------------------
_display_flags: dict[str, bool] | None = None


def _get_display_flags() -> tuple[bool, bool]:
    """Return (show_badge, show_title) respecting room settings (cached)."""
    global _display_flags
    if _display_flags is None:
        _display_flags = {
            "b": get_room_setting("display_badges_enabled", "true") == "true",
            "t": get_room_setting("display_titles_enabled", "true") == "true",
        }
    return _display_flags["b"], _display_flags["t"]


def invalidate_display_cache() -> None:
    """Force-flush the display-flags cache (call after changing display settings)."""
    global _display_flags
    _display_flags = None


def _build_display(badge: str | None, username: str, title: str | None) -> str:
    show_badge, show_title = _get_display_flags()
    parts = []
    if badge and show_badge:
        parts.append(badge)
    if title and show_title:
        parts.append(title)
    parts.append(f"@{username}")
    return " ".join(parts)


def get_display_name(user_id: str, username: str) -> str:
    """
    Return the player's full display string: <badge> [title] @username
    Respects display_badges_enabled / display_titles_enabled room settings.
    Falls back to @username if they have nothing equipped or settings are off.
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


def get_display_name_by_username(username: str) -> str:
    """
    Look up a player's display name by username (case-insensitive).
    Use when you only have a username, not the user_id.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT equipped_badge, equipped_title "
        "FROM users WHERE LOWER(username) = LOWER(?)",
        (username,)
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


# ---------------------------------------------------------------------------
# Profile privacy helpers
# ---------------------------------------------------------------------------

def get_profile_privacy(username: str) -> dict:
    """Return the profile_privacy row for username, inserting defaults if missing."""
    conn = get_connection()
    conn.execute(
        """
        INSERT OR IGNORE INTO profile_privacy (username)
        VALUES (?)
        """,
        (username.lower(),),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM profile_privacy WHERE username = ?",
        (username.lower(),),
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return {
        "username": username.lower(),
        "show_money": 1,
        "show_casino": 1,
        "show_achievements": 1,
        "show_inventory": 1,
    }


def set_profile_privacy(username: str, field: str, value: int) -> None:
    """Set a single privacy field (show_money / show_casino / show_achievements / show_inventory)."""
    _allowed = {"show_money", "show_casino", "show_achievements", "show_inventory"}
    if field not in _allowed:
        return
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO profile_privacy (username) VALUES (?)",
        (username.lower(),),
    )
    conn.execute(
        f"UPDATE profile_privacy SET {field} = ?, updated_at = datetime('now') WHERE username = ?",
        (value, username.lower()),
    )
    conn.commit()
    conn.close()


def reset_profile_privacy(username: str) -> None:
    """Reset all privacy fields to their default (visible) state."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO profile_privacy
            (username, show_money, show_casino, show_achievements, show_inventory, updated_at)
        VALUES (?, 1, 1, 1, 1, datetime('now'))
        ON CONFLICT(username) DO UPDATE SET
            show_money        = 1,
            show_casino       = 1,
            show_achievements = 1,
            show_inventory    = 1,
            updated_at        = datetime('now')
        """,
        (username.lower(),),
    )
    conn.commit()
    conn.close()


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
    related_user: str = "",
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


def record_daily_claim(user_id: str) -> tuple:
    """Record today's daily claim. Returns (new_streak, total_claims)."""
    from datetime import timedelta
    conn      = get_connection()
    today     = str(date.today())
    yesterday = str(date.today() - timedelta(days=1))

    row = conn.execute(
        "SELECT last_claim, streak, total_claims, best_streak FROM daily_claims WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if row is None:
        streak     = 1
        total      = 1
        best_streak = 1
    else:
        old_streak  = row["streak"] or 1
        old_total   = row["total_claims"] or 1
        old_best    = row["best_streak"] if row["best_streak"] is not None else old_streak
        streak      = (old_streak + 1) if row["last_claim"] == yesterday else 1
        total       = old_total + 1
        best_streak = max(old_best, streak)

    now_ts = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""
        INSERT INTO daily_claims
            (user_id, last_claim, streak, total_claims, last_claim_ts, best_streak)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE
          SET last_claim    = excluded.last_claim,
              streak        = excluded.streak,
              total_claims  = excluded.total_claims,
              last_claim_ts = excluded.last_claim_ts,
              best_streak   = excluded.best_streak
    """, (user_id, today, streak, total, now_ts, best_streak))
    conn.commit()
    conn.close()
    return (streak, total)


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
    """Return current daily streak, best streak, and total claim count."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT streak, total_claims, best_streak FROM daily_claims WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return {"streak": 0, "total_claims": 0, "best_streak": 0}
    streak      = row["streak"] or 0
    best_streak = row["best_streak"] if row["best_streak"] is not None else streak
    return {
        "streak":       streak,
        "total_claims": row["total_claims"] or 0,
        "best_streak":  best_streak,
    }


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
            "bj_win_limit_enabled": 1, "bj_loss_limit_enabled": 1,
            "bj_betlimit_enabled": 1,
            "bj_action_timer": 30,
            "bj_double_enabled": 1, "bj_split_enabled": 1,
            "bj_max_splits": 1, "bj_split_aces_one_card": 1,
            "bj_bonus_enabled": 1, "bj_bonus_pair_pct": 10,
            "bj_bonus_color_pct": 25, "bj_bonus_perfect_pct": 50,
            "bj_bonus_cap": 10000, "bj_cards_mode": "whisper",
            "bj_insurance_enabled": 1,
        }
    return dict(row)


_BJ_SETTING_COLS = {
    "min_bet", "max_bet", "win_payout", "blackjack_payout", "push_rule",
    "dealer_hits_soft_17", "lobby_countdown", "turn_timer", "bj_turn_timer",
    "max_players", "bj_enabled",
    "bj_daily_win_limit", "bj_daily_loss_limit",
    "bj_win_limit_enabled", "bj_loss_limit_enabled",
    "bj_betlimit_enabled",
    "bj_action_timer",
    "bj_double_enabled", "bj_split_enabled",
    "bj_max_splits", "bj_split_aces_one_card",
    "bj_bonus_enabled", "bj_bonus_pair_pct",
    "bj_bonus_color_pct", "bj_bonus_perfect_pct",
    "bj_bonus_cap", "bj_cards_mode",
    "bj_insurance_enabled",
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
    "rbj_win_limit_enabled", "rbj_loss_limit_enabled",
    "rbj_betlimit_enabled",
    "rbj_action_timer",
    "rbj_double_enabled", "rbj_split_enabled",
    "rbj_max_splits", "rbj_split_aces_one_card",
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
            "rbj_win_limit_enabled": 1, "rbj_loss_limit_enabled": 1,
            "rbj_betlimit_enabled": 1,
            "rbj_action_timer": 30,
            "rbj_double_enabled": 1, "rbj_split_enabled": 1,
            "rbj_max_splits": 1, "rbj_split_aces_one_card": 1,
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


def get_time_exp_daily(user_id: str) -> int:
    """Return EXP earned today from time-in-room for a player."""
    conn = get_connection()
    row = conn.execute(
        "SELECT earned FROM time_exp_daily WHERE user_id=? AND date=?",
        (user_id, _today())
    ).fetchone()
    conn.close()
    return row["earned"] if row else 0


def add_time_exp_daily(user_id: str, amount: int) -> int:
    """Add to today's time-EXP total (atomic upsert). Returns new total."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO time_exp_daily (user_id, date, earned) VALUES (?, ?, ?)"
        " ON CONFLICT(user_id, date) DO UPDATE SET earned = earned + excluded.earned",
        (user_id, _today(), amount)
    )
    conn.commit()
    row = conn.execute(
        "SELECT earned FROM time_exp_daily WHERE user_id=? AND date=?",
        (user_id, _today())
    ).fetchone()
    conn.close()
    return row["earned"] if row else amount


def get_poker_settings() -> dict:
    """Return all poker settings as a flat dict (key → coerced value)."""
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM poker_settings").fetchall()
    conn.close()
    result: dict = {}
    for row in rows:
        try:
            result[row["key"]] = int(row["value"])
        except (ValueError, TypeError):
            try:
                result[row["key"]] = float(row["value"])
            except (ValueError, TypeError):
                result[row["key"]] = row["value"]
    return result


def save_poker_v2_setting(key: str, value) -> None:
    """Upsert a Poker V2 setting into poker_settings."""
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO poker_settings (key, value) VALUES (?, ?)",
        (str(key), str(value)),
    )
    conn.commit()
    conn.close()


def insert_poker_log(
    hand_number: int = 0,
    action: str = "",
    user_id: str = "",
    username: str = "",
    amount: int = 0,
    pot: int = 0,
    stack: int = 0,
    details: str = "",
) -> None:
    """Insert one row into poker_logs."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO poker_logs
           (hand_number, action, user_id, username, amount, pot, stack, details)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (hand_number, action, user_id, username, amount, pot, stack, details),
    )
    conn.commit()
    conn.close()


def get_poker_v2_logs(limit: int = 20, username: str = "", hand_number: int = -1) -> list:
    """Fetch poker_logs rows. Filter by username or hand_number when provided."""
    conn = get_connection()
    if username:
        rows = conn.execute(
            "SELECT * FROM poker_logs WHERE username=? ORDER BY id DESC LIMIT ?",
            (username.lower(), limit),
        ).fetchall()
    elif hand_number >= 0:
        rows = conn.execute(
            "SELECT * FROM poker_logs WHERE hand_number=? ORDER BY id DESC LIMIT ?",
            (hand_number, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM poker_logs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def reset_bj_daily_limits(user_id: str) -> None:
    """Reset today's BJ daily net for a player to 0."""
    conn = get_connection()
    conn.execute(
        "DELETE FROM bj_daily WHERE user_id = ? AND date = ?",
        (user_id, _today())
    )
    conn.commit()
    conn.close()


def reset_rbj_daily_limits(user_id: str) -> None:
    """Reset today's RBJ daily net for a player to 0."""
    conn = get_connection()
    conn.execute(
        "DELETE FROM rbj_daily WHERE user_id = ? AND date = ?",
        (user_id, _today())
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
        "SELECT first_seen, level, total_coins_earned, tip_coins_earned "
        "FROM users WHERE user_id = ?",
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

    # 3. Organic earned (tip coins excluded — tipping gold cannot bypass this gate)
    min_earned     = int(settings.get("min_total_earned_to_send", 500))
    tip_earned_so  = (u_row["tip_coins_earned"] or 0) if u_row else 0
    organic_earned = (u_row["total_coins_earned"] or 0) - tip_earned_so
    if u_row and organic_earned < min_earned:
        conn.close()
        return {"eligible": False,
                "reason": f"Must earn {min_earned}c from gameplay first."}

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
        "SELECT user_id, username, balance, level, "
        "       total_coins_earned, tip_coins_earned, first_seen "
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
    _total_e = u["total_coins_earned"] or 0
    _tip_e   = u["tip_coins_earned"]   or 0
    return {
        "user_id":        uid,
        "username":       u["username"],
        "balance":        u["balance"] or 0,
        "level":          u["level"] or 1,
        "total_earned":   _total_e,
        "tip_earned":     _tip_e,
        "organic_earned": _total_e - _tip_e,
        "first_seen":     (u["first_seen"] or "unknown")[:10],
        "total_sent":     bus["total_sent"] if bus else 0,
        "total_received": bus["total_received"] if bus else 0,
        "daily_sent":     daily_sent,
        "bank_blocked":   bool(bus["bank_blocked"]) if bus else False,
        "suspicious_count": bus["suspicious_transfer_count"] if bus else 0,
        "total_claims":   (ds_row["total_claims"] or 0) if ds_row else 0,
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
    gain = max(0, amount)
    conn.execute("""
        INSERT INTO event_points (user_id, points, lifetime_event_coins, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
            points               = MAX(0, points + excluded.points),
            lifetime_event_coins = lifetime_event_coins + ?,
            updated_at           = datetime('now')
    """, (user_id, amount, gain, gain))
    conn.commit()
    conn.close()


def is_event_active() -> bool:
    """Return True only if an event is active AND has not yet expired."""
    return get_active_event() is not None


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


def check_event_expired() -> bool:
    """
    Check whether the currently-active event has passed its wall-clock expiry.
    If yes: clear it from the DB and return True.
    If no active event or still within its window: return False.
    Called automatically by get_active_event() so all callers stay in sync.
    """
    from datetime import datetime as _dt, timezone as _tz
    conn = get_connection()
    rows = {
        r["key"]: r["value"]
        for r in conn.execute("SELECT key, value FROM event_settings").fetchall()
    }
    conn.close()
    if rows.get("event_active") != "1":
        return False
    expires_at_str = rows.get("event_expires_at", "")
    if not expires_at_str:
        return False
    try:
        exp = _dt.fromisoformat(expires_at_str)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=_tz.utc)
        if _dt.now(_tz.utc) >= exp:
            clear_active_event()
            print(f"[EVENTS] check_event_expired: event cleared (expired {expires_at_str}).")
            return True
    except Exception:
        pass
    return False


def get_active_event() -> dict | None:
    """
    Return {"event_id": str, "expires_at": str} if an event is active
    and has not yet expired, otherwise None.
    Auto-clears the DB flag when the event window has passed.
    """
    # Wall-clock expiry guard — clears stale events before any caller acts on them
    if check_event_expired():
        return None
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


def get_poker_stats_by_username(username: str) -> Optional[dict]:
    """Fetch poker stats for any player by username (case-insensitive)."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM poker_stats WHERE LOWER(username) = LOWER(?)", (username,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_poker_stats(
    user_id: str,
    username: str,
    *,
    wins: int = 0,
    losses: int = 0,
    folds: int = 0,
    showdowns: int = 0,
    allins: int = 0,
    total_won: int = 0,
    total_lost: int = 0,
    total_buyin: int = 0,
    biggest_pot: int = 0,
    biggest_win: int = 0,
    net_delta: int = 0,
    hands: int = 0,
) -> None:
    from datetime import date as _date_cls
    today = _date_cls.today().isoformat()
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO poker_stats (user_id, username) VALUES (?, ?)",
        (user_id, username),
    )
    conn.execute("""
        UPDATE poker_stats SET
            hands_played       = hands_played + ?,
            wins               = wins + ?,
            losses             = losses + ?,
            folds              = folds + ?,
            showdowns          = showdowns + ?,
            allins             = allins + ?,
            total_won          = total_won + ?,
            total_lost         = total_lost + ?,
            total_buyin        = total_buyin + ?,
            biggest_pot        = MAX(biggest_pot, ?),
            biggest_win        = MAX(biggest_win, ?),
            net_profit         = net_profit + ?,
            last_played_at     = ?
        WHERE user_id = ?
    """, (hands, wins, losses, folds, showdowns, allins,
          total_won, total_lost, total_buyin,
          biggest_pot, biggest_win, net_delta, today, user_id))
    # Update streak: wins > 0 → increment; losses > 0 → reset
    if wins > 0:
        conn.execute("""
            UPDATE poker_stats SET
                current_win_streak = current_win_streak + 1,
                best_win_streak    = MAX(best_win_streak, current_win_streak + 1)
            WHERE user_id = ?
        """, (user_id,))
    elif losses > 0:
        conn.execute(
            "UPDATE poker_stats SET current_win_streak=0 WHERE user_id=?",
            (user_id,)
        )
    # Update daily stats table
    conn.execute("""
        INSERT INTO poker_daily_stats (username, date, hands_played, wins, losses, net_profit, biggest_pot)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(username, date) DO UPDATE SET
            hands_played = hands_played + excluded.hands_played,
            wins         = wins + excluded.wins,
            losses       = losses + excluded.losses,
            net_profit   = net_profit + excluded.net_profit,
            biggest_pot  = MAX(biggest_pot, excluded.biggest_pot)
    """, (username, today, hands, wins, losses, net_delta, biggest_pot))
    conn.commit()
    conn.close()


def get_poker_leaderboard(mode: str = "profit", limit: int = 5) -> list:
    """Return top players for a given leaderboard mode."""
    from datetime import date as _date_cls
    today = _date_cls.today().isoformat()
    conn = get_connection()
    if mode == "wins":
        rows = conn.execute(
            "SELECT username, wins FROM poker_stats ORDER BY wins DESC LIMIT ?",
            (limit,)
        ).fetchall()
    elif mode == "pots":
        rows = conn.execute(
            "SELECT username, biggest_pot FROM poker_stats ORDER BY biggest_pot DESC LIMIT ?",
            (limit,)
        ).fetchall()
    elif mode == "streak":
        rows = conn.execute(
            "SELECT username, best_win_streak FROM poker_stats ORDER BY best_win_streak DESC LIMIT ?",
            (limit,)
        ).fetchall()
    elif mode == "hands":
        rows = conn.execute(
            "SELECT username, hands_played FROM poker_stats ORDER BY hands_played DESC LIMIT ?",
            (limit,)
        ).fetchall()
    elif mode == "allins":
        rows = conn.execute(
            "SELECT username, allins FROM poker_stats ORDER BY allins DESC LIMIT ?",
            (limit,)
        ).fetchall()
    elif mode == "daily":
        rows = conn.execute(
            "SELECT username, net_profit FROM poker_daily_stats WHERE date=? ORDER BY net_profit DESC LIMIT ?",
            (today, limit)
        ).fetchall()
    else:  # profit
        rows = conn.execute(
            "SELECT username, net_profit FROM poker_stats ORDER BY net_profit DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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


def clear_automod_warnings(username: str) -> int:
    """
    Delete only the warnings issued by the automod system for a user.
    Returns the number of rows deleted.
    Called during /unmute and /forceunmute to reset escalation state.
    """
    conn = get_connection()
    cur  = conn.execute(
        "DELETE FROM warnings WHERE LOWER(username) = ? AND warned_by = '__automod__'",
        (username.lower(),),
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


# ---------------------------------------------------------------------------
# Tip system helpers
# ---------------------------------------------------------------------------

def is_tip_duplicate(event_hash: str) -> bool:
    """Return True if a tip with this hash was already processed (DB-level dedup)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM tip_transactions WHERE event_id_or_hash = ? LIMIT 1",
        (event_hash,),
    ).fetchone()
    conn.close()
    return row is not None


def log_tip_transaction(
    username: str,
    gold_amount: int,
    coins_awarded: int,
    bonus_percent: int,
    status: str,
    event_hash: str,
) -> None:
    """Insert one row into tip_transactions (spec-required log table)."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO tip_transactions
            (username, gold_amount, coins_awarded, bonus_percent, status, event_id_or_hash)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (username, gold_amount, coins_awarded, bonus_percent, status, event_hash),
    )
    conn.commit()
    conn.close()


def log_tip_audit(
    event_hash: str,
    sender_user_id: str,
    sender_username: str,
    receiver_user_id: str,
    receiver_username: str,
    bot_mode: str,
    raw_tip_type: str,
    raw_tip_id: str,
    gold_amount: int,
    luxe_expected: int,
    luxe_awarded: int,
    luxe_balance_before: int,
    luxe_balance_after: int,
    coins_awarded: int,
    coins_balance_before: int,
    coins_balance_after: int,
    status: str,
    failure_reason: str = "",
    duplicate_detected: int = 0,
) -> None:
    """Insert one row into tip_audit_logs. Never raises."""
    try:
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO tip_audit_logs (
                event_hash, sender_user_id, sender_username,
                receiver_user_id, receiver_username, bot_mode,
                raw_tip_type, raw_tip_id, gold_amount,
                luxe_expected, luxe_awarded,
                luxe_balance_before, luxe_balance_after,
                coins_awarded, coins_balance_before, coins_balance_after,
                status, failure_reason, duplicate_detected
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_hash, sender_user_id, sender_username,
                receiver_user_id, receiver_username, bot_mode,
                raw_tip_type, raw_tip_id, gold_amount,
                luxe_expected, luxe_awarded,
                luxe_balance_before, luxe_balance_after,
                coins_awarded, coins_balance_before, coins_balance_after,
                status, failure_reason, duplicate_detected,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[TIP AUDIT ERROR] ignored: {e!r}")


def log_luxe_conversion(
    user_id: str,
    username: str,
    item_key: str,
    tickets_spent: int,
    coins_awarded: int,
    luxe_balance_before: int,
    luxe_balance_after: int,
    coins_balance_before: int,
    coins_balance_after: int,
    status: str,
    failure_reason: str = "",
) -> None:
    """Insert one row into luxe_conversion_logs. Never raises."""
    try:
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO luxe_conversion_logs (
                user_id, username, item_key, tickets_spent, coins_awarded,
                luxe_balance_before, luxe_balance_after,
                coins_balance_before, coins_balance_after,
                status, failure_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, username, item_key, tickets_spent, coins_awarded,
                luxe_balance_before, luxe_balance_after,
                coins_balance_before, coins_balance_after,
                status, failure_reason,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[LUXE CONVERSION AUDIT] error: {e!r}")


# ── Coin pack helpers ─────────────────────────────────────────────────────────

_DEFAULT_COIN_PACKS = [
    (1, 100,  1_000),
    (2, 500,  5_500),
    (3, 5_000, 60_000),
]


def init_default_coin_packs() -> None:
    """Seed default coin packs if not present. Safe to call every startup."""
    try:
        conn = get_connection()
        for pack_id, cost, coins in _DEFAULT_COIN_PACKS:
            conn.execute(
                """INSERT OR IGNORE INTO coin_packs
                   (pack_id, ticket_cost, chillcoins_amount, enabled, updated_at)
                   VALUES (?, ?, ?, 1, datetime('now'))""",
                (pack_id, cost, coins),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[COIN PACKS] init_default_coin_packs error: {e!r}")


def get_all_coin_packs() -> list:
    """Return all enabled coin packs ordered by pack_id."""
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT pack_id, ticket_cost, chillcoins_amount, enabled "
            "FROM coin_packs ORDER BY pack_id"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[COIN PACKS] get_all_coin_packs error: {e!r}")
        return []


def get_coin_pack_by_id(pack_id: int):
    """Return (ticket_cost, chillcoins_amount) for a pack, or None."""
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT ticket_cost, chillcoins_amount FROM coin_packs "
            "WHERE pack_id=? AND enabled=1",
            (pack_id,),
        ).fetchone()
        conn.close()
        if row:
            return (int(row["ticket_cost"]), int(row["chillcoins_amount"]))
        return None
    except Exception as e:
        print(f"[COIN PACKS] get_coin_pack_by_id error: {e!r}")
        return None


def set_coin_pack(pack_id: int, ticket_cost: int, chillcoins_amount: int) -> None:
    """Upsert a numbered coin pack."""
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO coin_packs (pack_id, ticket_cost, chillcoins_amount, enabled, updated_at)
               VALUES (?, ?, ?, 1, datetime('now'))
               ON CONFLICT(pack_id) DO UPDATE SET
                 ticket_cost=excluded.ticket_cost,
                 chillcoins_amount=excluded.chillcoins_amount,
                 enabled=1,
                 updated_at=excluded.updated_at""",
            (pack_id, ticket_cost, chillcoins_amount),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[COIN PACKS] set_coin_pack error: {e!r}")


# ── Luxe ticket log helpers ────────────────────────────────────────────────────

def insert_luxe_ticket_log(
    action: str,
    user_id: str,
    username: str,
    target_user_id: str = "",
    target_username: str = "",
    amount: int = 0,
    balance_after: int = 0,
    reason: str = "",
    ref_id: str = "",
) -> None:
    """Write one row to luxe_ticket_logs. Never raises."""
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO luxe_ticket_logs
               (action, user_id, username, target_user_id, target_username,
                amount, balance_after, reason, ref_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (action, user_id, username.lower(), target_user_id,
             target_username.lower() if target_username else "",
             amount, balance_after, reason, ref_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[LUXE LOG] insert_luxe_ticket_log error: {e!r}")


def is_luxe_ref_duplicate(ref_id: str) -> bool:
    """Return True if ref_id already exists in luxe_ticket_logs."""
    if not ref_id:
        return False
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT 1 FROM luxe_ticket_logs WHERE ref_id=? AND action='tip_award' LIMIT 1",
            (ref_id,),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def get_luxe_ticket_logs(
    user_id: str = "",
    action: str = "",
    ref_id: str = "",
    limit: int = 10,
) -> list:
    """Fetch luxe_ticket_logs rows with optional filters."""
    try:
        conn = get_connection()
        clauses = []
        params: list = []
        if user_id:
            clauses.append("(user_id=? OR target_user_id=?)")
            params += [user_id, user_id]
        if action:
            clauses.append("action=?")
            params.append(action)
        if ref_id:
            clauses.append("ref_id=?")
            params.append(ref_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        rows = conn.execute(
            f"SELECT * FROM luxe_ticket_logs {where} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[LUXE LOG] get_luxe_ticket_logs error: {e!r}")
        return []


# ── Notification subscription helpers (manual subscribe only) ─────────────────

_NOTIF_CATEGORIES = ("events", "games", "announcements", "promos", "tips")


def ensure_notification_subscription(user_id: str, username: str) -> None:
    """Create a notification_subscriptions row if not present. Never raises."""
    try:
        conn = get_connection()
        conn.execute(
            """INSERT OR IGNORE INTO notification_subscriptions
               (user_id, username, subscribed, events, games, announcements, promos, tips,
                source, created_at, updated_at)
               VALUES (?, ?, 0, 1, 1, 1, 1, 0, 'manual', datetime('now'), datetime('now'))""",
            (user_id, username.lower()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[NOTIFY] ensure_notification_subscription error: {e!r}")


def get_notification_subscription(user_id: str) -> dict:
    """Return the notification_subscriptions row for user_id, or an empty dict."""
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM notification_subscriptions WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else {}
    except Exception as e:
        print(f"[NOTIFY] get_notification_subscription error: {e!r}")
        return {}


def set_notification_subscribed(
    user_id: str, username: str, subscribed: bool, source: str = "manual"
) -> None:
    """Upsert subscription status. source must always be 'manual'. Never raises."""
    if source != "manual":
        print(f"[NOTIFY] BLOCKED non-manual subscription source={source!r} for uid={user_id[:12]}")
        return
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO notification_subscriptions
               (user_id, username, subscribed, source, updated_at)
               VALUES (?, ?, ?, 'manual', datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET
                 subscribed=excluded.subscribed,
                 source='manual',
                 updated_at=excluded.updated_at""",
            (user_id, username.lower(), 1 if subscribed else 0),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[NOTIFY] set_notification_subscribed error: {e!r}")


def set_notification_category(
    user_id: str, username: str, category: str, enabled: bool
) -> None:
    """Enable/disable a specific notification category. Never raises."""
    if category not in _NOTIF_CATEGORIES:
        print(f"[NOTIFY] set_notification_category: invalid category {category!r}")
        return
    try:
        conn = get_connection()
        conn.execute(
            f"""INSERT INTO notification_subscriptions
               (user_id, username, {category}, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET
                 {category}=excluded.{category},
                 updated_at=excluded.updated_at""",
            (user_id, username.lower(), 1 if enabled else 0),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[NOTIFY] set_notification_category error: {e!r}")


def is_notification_allowed(user_id: str, category: str) -> bool:
    """Return True if user is subscribed and the category is enabled."""
    try:
        row = get_notification_subscription(user_id)
        if not row:
            return False
        if not row.get("subscribed"):
            return False
        return bool(row.get(category, 1))
    except Exception:
        return False


def get_subscribed_users_for_category(category: str) -> list:
    """Return all users subscribed=1 AND category=1."""
    try:
        conn = get_connection()
        if category not in _NOTIF_CATEGORIES:
            conn.close()
            return []
        rows = conn.execute(
            f"SELECT user_id, username FROM notification_subscriptions "
            f"WHERE subscribed=1 AND {category}=1",
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[NOTIFY] get_subscribed_users_for_category error: {e!r}")
        return []


def get_notification_subscription_counts() -> dict:
    """Return aggregate counts for admin !subcount command."""
    try:
        conn = get_connection()
        total = conn.execute(
            "SELECT COUNT(*) FROM notification_subscriptions WHERE subscribed=1"
        ).fetchone()[0]
        counts = {"total": total}
        for cat in _NOTIF_CATEGORIES:
            counts[cat] = conn.execute(
                f"SELECT COUNT(*) FROM notification_subscriptions "
                f"WHERE subscribed=1 AND {cat}=1"
            ).fetchone()[0]
        conn.close()
        return counts
    except Exception as e:
        print(f"[NOTIFY] get_notification_subscription_counts error: {e!r}")
        return {"total": 0}


def insert_notification_action_log(
    action: str,
    user_id: str = "",
    username: str = "",
    category: str = "",
    details: str = "",
) -> None:
    """Write one row to notification_action_logs. Never raises."""
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO notification_action_logs
               (action, user_id, username, category, details, created_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (action, user_id, username.lower() if username else "", category, details),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[NOTIFY] insert_notification_action_log error: {e!r}")


def get_tip_settings() -> dict:
    """Return all tip settings as a plain dict."""
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM tip_settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def set_tip_setting(key: str, value: str) -> None:
    """Upsert a single tip setting."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO tip_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_daily_gold_converted(user_id: str) -> int:
    """Total gold already converted to coins by this user today (UTC date)."""
    from datetime import date as _date
    conn  = get_connection()
    today = str(_date.today())
    row   = conn.execute("""
        SELECT COALESCE(SUM(gold_amount), 0) AS total
        FROM tip_conversions
        WHERE user_id = ? AND DATE(timestamp) = ?
    """, (user_id, today)).fetchone()
    conn.close()
    return row["total"] if row else 0


def record_tip_conversion(
    user_id: str,
    username: str,
    gold_amount: int,
    bonus_pct: int,
    coins_awarded: int,
) -> None:
    """
    Persist a tip conversion, credit the player's balance (capped at
    max_balance), increment total_coins_earned, and write a ledger entry.
    """
    from datetime import datetime as _dt
    now_ts  = _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    max_bal = get_max_balance()

    conn = get_connection()
    conn.execute("""
        INSERT INTO tip_conversions
            (timestamp, user_id, username, gold_amount, bonus_pct, coins_awarded)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (now_ts, user_id, username, gold_amount, bonus_pct, coins_awarded))

    # Credit balance (capped), record total earned, and track tip-sourced coins separately
    conn.execute("""
        UPDATE users
        SET balance             = MIN(balance + ?, ?),
            total_coins_earned  = total_coins_earned  + ?,
            tip_coins_earned    = tip_coins_earned    + ?
        WHERE user_id = ?
    """, (coins_awarded, max_bal, coins_awarded, coins_awarded, user_id))

    conn.commit()
    conn.close()

    # Ledger entry (calls add_ledger_entry which opens its own connection)
    add_ledger_entry(user_id, username, coins_awarded, "gold_tip")


def get_tip_stats(user_id: str) -> dict:
    """Return total and today gold/coins for a single user."""
    from datetime import date as _date
    today = str(_date.today())
    conn  = get_connection()

    total = conn.execute("""
        SELECT COALESCE(SUM(gold_amount), 0) AS gold,
               COALESCE(SUM(coins_awarded), 0) AS coins
        FROM tip_conversions WHERE user_id = ?
    """, (user_id,)).fetchone()

    today_gold = conn.execute("""
        SELECT COALESCE(SUM(gold_amount), 0) AS gold
        FROM tip_conversions
        WHERE user_id = ? AND DATE(timestamp) = ?
    """, (user_id, today)).fetchone()

    conn.close()
    return {
        "total_gold":  total["gold"]       if total      else 0,
        "total_coins": total["coins"]      if total      else 0,
        "today_gold":  today_gold["gold"]  if today_gold else 0,
    }


def get_tip_leaderboard(limit: int = 10) -> list[dict]:
    """Top tippers ordered by total gold converted."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT username,
               SUM(gold_amount)   AS total_gold,
               SUM(coins_awarded) AS total_coins
        FROM tip_conversions
        GROUP BY user_id
        ORDER BY total_gold DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Auto game settings helpers
# ---------------------------------------------------------------------------

_AUTO_GAME_DEFAULTS: dict[str, int] = {
    "game_answer_timer":      60,
    "auto_minigames_enabled":  1,
    "auto_minigame_interval": 10,
}


def get_auto_game_settings() -> dict[str, int]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT key, value FROM auto_game_settings"
    ).fetchall()
    conn.close()
    result = dict(_AUTO_GAME_DEFAULTS)
    for r in rows:
        try:
            result[r["key"]] = int(r["value"])
        except (ValueError, TypeError):
            pass
    return result


def set_auto_game_setting(key: str, value: int) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO auto_game_settings (key, value) VALUES (?, ?)",
        (key, str(value)),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Auto event settings helpers
# ---------------------------------------------------------------------------

_AUTO_EVENT_DEFAULTS: dict[str, int] = {
    "auto_events_enabled": 1,
    "auto_event_interval": 60,
    "auto_event_duration": 30,
}


def get_auto_event_settings() -> dict[str, int]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT key, value FROM auto_event_settings"
    ).fetchall()
    conn.close()
    result = dict(_AUTO_EVENT_DEFAULTS)
    for r in rows:
        try:
            result[r["key"]] = int(r["value"])
        except (ValueError, TypeError):
            pass
    return result


def set_auto_event_setting(key: str, value: int) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO auto_event_settings (key, value) VALUES (?, ?)",
        (key, str(value)),
    )
    conn.commit()
    conn.close()


def get_auto_event_setting_str(key: str, default: str = "") -> str:
    """Get a string value from auto_event_settings (for timestamp/text fields)."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT value FROM auto_event_settings WHERE key=?", (key,)
    ).fetchone()
    conn.close()
    return row["value"] if row else default


def set_auto_event_setting_str(key: str, value: str) -> None:
    """Store a string value in auto_event_settings (for timestamp/text fields)."""
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO auto_event_settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Event pool (auto_event_pool) helpers
# ---------------------------------------------------------------------------

def get_event_pool() -> list[dict]:
    """Return all events currently in the auto-event pool."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT event_id, weight, cooldown_minutes, last_started_at "
        "FROM auto_event_pool ORDER BY weight DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_event_pool_entry(event_id: str) -> dict | None:
    """Return pool row for one event_id, or None if not in pool."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT event_id, weight, cooldown_minutes, last_started_at "
        "FROM auto_event_pool WHERE event_id=?", (event_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def add_to_event_pool(event_id: str, weight: int = 1,
                      cooldown_minutes: int = 60) -> None:
    """Add or update an event in the auto-event pool."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO auto_event_pool (event_id, weight, cooldown_minutes, last_started_at) "
        "VALUES (?, ?, ?, '') "
        "ON CONFLICT(event_id) DO UPDATE SET weight=excluded.weight, "
        "cooldown_minutes=excluded.cooldown_minutes",
        (event_id, weight, cooldown_minutes),
    )
    conn.commit()
    conn.close()


def remove_from_event_pool(event_id: str) -> bool:
    """Remove an event from the pool. Returns True if removed."""
    conn = get_connection()
    cur  = conn.execute(
        "DELETE FROM auto_event_pool WHERE event_id=?", (event_id,)
    )
    removed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return removed


def update_pool_last_started(event_id: str) -> None:
    """Stamp last_started_at = now for an event in the pool."""
    conn = get_connection()
    conn.execute(
        "UPDATE auto_event_pool SET last_started_at=datetime('now') WHERE event_id=?",
        (event_id,),
    )
    conn.commit()
    conn.close()


def get_eligible_pool_events() -> list[dict]:
    """Return pool events whose cooldown has passed (eligible to auto-start)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT event_id, weight, cooldown_minutes, last_started_at
           FROM auto_event_pool
           WHERE weight > 0
             AND (
               last_started_at = ''
               OR datetime(last_started_at, '+' || cooldown_minutes || ' minutes')
                  <= datetime('now')
             )
           ORDER BY weight DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_pool_weight(event_id: str, weight: int) -> None:
    """Update the selection weight for a pool event."""
    conn = get_connection()
    conn.execute(
        "UPDATE auto_event_pool SET weight=? WHERE event_id=?", (weight, event_id)
    )
    conn.commit()
    conn.close()


def set_pool_cooldown(event_id: str, cooldown_minutes: int) -> None:
    """Update the cooldown for a pool event."""
    conn = get_connection()
    conn.execute(
        "UPDATE auto_event_pool SET cooldown_minutes=? WHERE event_id=?",
        (cooldown_minutes, event_id),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Event history helpers
# ---------------------------------------------------------------------------

def add_event_history_entry(
    event_id: str,
    event_name: str,
    started_by: str,
    auto_started: bool,
    duration_seconds: int,
) -> int:
    """Insert a new event_history row. Returns the row id."""
    conn = get_connection()
    cur  = conn.execute(
        "INSERT INTO event_history "
        "(event_id, event_name, started_by, auto_started, started_at, "
        " duration_seconds, status) "
        "VALUES (?, ?, ?, ?, datetime('now'), ?, 'active')",
        (event_id, event_name, started_by, 1 if auto_started else 0, duration_seconds),
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id or 0


def close_event_history_entry(
    history_id: int, status: str = "ended"
) -> None:
    """Mark an event_history row as ended."""
    conn = get_connection()
    conn.execute(
        "UPDATE event_history SET ended_at=datetime('now'), status=? WHERE id=?",
        (status, history_id),
    )
    conn.commit()
    conn.close()


def get_event_history(limit: int = 5) -> list[dict]:
    """Return the most recent event_history rows."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, event_id, event_name, started_by, auto_started, "
        "started_at, ended_at, duration_seconds, status "
        "FROM event_history ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def cleanup_expired_history() -> int:
    """
    Mark event_history rows with status='active' as 'ended' if their
    calculated end time (started_at + duration_seconds) has passed.
    Returns the number of rows updated.
    """
    conn = get_connection()
    cur  = conn.execute(
        """UPDATE event_history
           SET   ended_at = COALESCE(ended_at, datetime('now')),
                 status   = 'ended'
           WHERE status   = 'active'
             AND (
               ended_at IS NOT NULL
               OR (
                 duration_seconds IS NOT NULL AND duration_seconds > 0
                 AND datetime(started_at, '+' || duration_seconds || ' seconds')
                     <= datetime('now')
               )
             )"""
    )
    updated = cur.rowcount
    conn.commit()
    conn.close()
    return updated


# ---------------------------------------------------------------------------
# Event definitions helpers
# ---------------------------------------------------------------------------

def seed_event_catalog_data(catalog: list[dict], pool_ids: list[str]) -> None:
    """
    Idempotent seed of event_definitions and auto_event_pool.
    Called from modules/events.py after init_db().
    catalog: list of EVENT_CATALOG dicts.
    pool_ids: list of event_ids to add to the default pool.
    """
    conn = get_connection()
    for ev in catalog:
        conn.execute(
            "INSERT OR IGNORE INTO event_definitions "
            "(event_id, event_number, event_name, emoji, event_type, effect_desc, "
            "default_duration_minutes, manual_only, stackable, default_weight, "
            "cooldown_minutes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                ev["event_id"], ev["number"], ev["name"], ev["emoji"],
                ev["event_type"], ev["effect_desc"], ev["default_duration"],
                1 if ev["manual_only"] else 0, 0,
                ev["default_weight"], ev["cooldown_minutes"],
            ),
        )
    for ev in catalog:
        if ev["event_id"] in pool_ids:
            conn.execute(
                "INSERT OR IGNORE INTO auto_event_pool "
                "(event_id, weight, cooldown_minutes, last_started_at) "
                "VALUES (?,?,?,'')",
                (ev["event_id"], ev["default_weight"], ev["cooldown_minutes"]),
            )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Gold transaction logging
# ---------------------------------------------------------------------------

def log_gold_tx(
    action_type: str,
    sender_owner: str,
    receiver_username: str,
    receiver_user_id: str,
    amount_gold: int,
    reason: str,
    status: str,
    denominations: str,
    batch_id: str,
    error_message: str,
) -> None:
    """Insert one row into gold_transactions."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO gold_transactions
            (action_type, sender_owner, receiver_username, receiver_user_id,
             amount_gold, reason, status, denominations, batch_id, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            action_type, sender_owner, receiver_username, receiver_user_id,
            amount_gold, reason, status, denominations, batch_id, error_message,
        ),
    )
    conn.commit()
    conn.close()


def get_gold_transactions(limit: int = 10) -> list[dict]:
    """Return the most recent gold transactions, newest first."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, timestamp, action_type, sender_owner, receiver_username,
               receiver_user_id, amount_gold, reason, status, denominations,
               batch_id, error_message
        FROM gold_transactions
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_gold_transactions_by_user(username: str, limit: int = 5) -> list[dict]:
    """Return recent transactions where receiver_username matches (case-insensitive)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, timestamp, action_type, sender_owner, receiver_username,
               amount_gold, status
        FROM gold_transactions
        WHERE LOWER(receiver_username) = LOWER(?)
        ORDER BY id DESC
        LIMIT ?
        """,
        (username, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pending_gold_transactions() -> list[dict]:
    """Return transactions logged with status='pending'."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, timestamp, action_type, sender_owner, receiver_username,
               amount_gold, reason, denominations, batch_id
        FROM gold_transactions
        WHERE status = 'pending'
        ORDER BY id DESC
        """,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Gold settings helpers
# ---------------------------------------------------------------------------

def get_gold_setting(key: str) -> str | None:
    """Return the stored value for a gold setting key, or None if not set."""
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM gold_settings WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    return row["value"] if row else None


def set_gold_setting(key: str, value: str) -> None:
    """Upsert a gold setting."""
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO gold_settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Gold Rain helpers
# ---------------------------------------------------------------------------

def log_gold_rain_event(
    mode: str,
    target_group: str,
    total_gold: float,
    winners_count: int,
    gold_each: float,
    interval_seconds: int,
    replacement_enabled: int,
    status: str,
    created_by_user_id: str,
    created_by_username: str,
) -> int:
    """Insert a new gold_rain_events row and return its id."""
    conn = get_connection()
    cur  = conn.execute(
        """INSERT INTO gold_rain_events
               (mode, target_group, total_gold, winners_count, gold_each,
                interval_seconds, replacement_enabled, status,
                created_by_user_id, created_by_username)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (mode, target_group, total_gold, winners_count, gold_each,
         interval_seconds, replacement_enabled, status,
         created_by_user_id, created_by_username),
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id or 0


def update_gold_rain_event(event_id: int, status: str) -> None:
    """Update the status (and completed_at / cancelled_at) of a gold rain event."""
    if not event_id:
        return
    ts_col = "completed_at" if status == "complete" else (
        "cancelled_at" if status == "cancelled" else None
    )
    conn = get_connection()
    if ts_col:
        conn.execute(
            f"UPDATE gold_rain_events SET status=?, {ts_col}=datetime('now') WHERE id=?",
            (status, event_id),
        )
    else:
        conn.execute(
            "UPDATE gold_rain_events SET status=? WHERE id=?",
            (status, event_id),
        )
    conn.commit()
    conn.close()


def log_gold_rain_winner(
    event_id: int,
    user_id: str,
    username: str,
    gold_amount: float,
    rank: int,
    payout_status: str,
    payout_error: str = "",
) -> None:
    """Insert one winner row into gold_rain_winners."""
    conn = get_connection()
    paid_at = "datetime('now')" if payout_status == "paid" else "NULL"
    conn.execute(
        f"""INSERT INTO gold_rain_winners
               (event_id, user_id, username, gold_amount, rank,
                payout_status, payout_error, paid_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, {paid_at})""",
        (event_id, user_id, username, gold_amount, rank,
         payout_status, payout_error),
    )
    conn.commit()
    conn.close()


def get_gold_rain_history(limit: int = 8) -> list[dict]:
    """Return recent gold_rain_events rows, newest first."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, mode, target_group, total_gold, winners_count,
                  gold_each, interval_seconds, status, created_at
           FROM gold_rain_events
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_gold_rain_event(event_id: int) -> dict:
    """Return a single gold_rain_events row as a dict (or empty dict)."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM gold_rain_events WHERE id=?", (event_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def get_gold_rain_winners(event_id: int) -> list[dict]:
    """Return all winner rows for a given event."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM gold_rain_winners WHERE event_id=? ORDER BY rank ASC",
        (event_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_gold_rain_setting(key: str) -> str | None:
    """Return a value from gold_rain_settings, or None if missing."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT value FROM gold_rain_settings WHERE key=?", (key,)
    ).fetchone()
    conn.close()
    return row["value"] if row else None


def set_gold_rain_setting(key: str, value: str) -> None:
    """Upsert a key in gold_rain_settings."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO gold_rain_settings (key, value, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                                          updated_at=excluded.updated_at""",
        (key, value),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Casino state persistence helpers
# ---------------------------------------------------------------------------

def save_casino_table(mode: str, data: dict) -> None:
    """Upsert casino_active_tables row for the given mode."""
    from datetime import datetime as _dt
    conn = get_connection()
    now  = _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO casino_active_tables
            (mode, updated_at, created_at, phase, round_id,
             current_player_index, dealer_hand_json, deck_json, shoe_json,
             shoe_cards_remaining, countdown_ends_at, turn_ends_at,
             active, recovery_required)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(mode) DO UPDATE SET
            updated_at           = excluded.updated_at,
            phase                = excluded.phase,
            round_id             = excluded.round_id,
            current_player_index = excluded.current_player_index,
            dealer_hand_json     = excluded.dealer_hand_json,
            deck_json            = excluded.deck_json,
            shoe_json            = excluded.shoe_json,
            shoe_cards_remaining = excluded.shoe_cards_remaining,
            countdown_ends_at    = excluded.countdown_ends_at,
            turn_ends_at         = excluded.turn_ends_at,
            active               = excluded.active,
            recovery_required    = excluded.recovery_required
        """,
        (
            mode, now, now,
            data.get("phase", "idle"),
            data.get("round_id", ""),
            int(data.get("current_player_index", 0)),
            data.get("dealer_hand_json", "[]"),
            data.get("deck_json", "[]"),
            data.get("shoe_json", "[]"),
            int(data.get("shoe_cards_remaining", 0)),
            data.get("countdown_ends_at", ""),
            data.get("turn_ends_at", ""),
            int(data.get("active", 0)),
            int(data.get("recovery_required", 0)),
        ),
    )
    conn.commit()
    conn.close()


def load_casino_table(mode: str) -> dict | None:
    """Return casino_active_tables row for mode, or None."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM casino_active_tables WHERE mode = ?", (mode,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def clear_casino_table(mode: str) -> None:
    """Delete casino_active_tables AND all casino_active_players for mode."""
    conn = get_connection()
    conn.execute("DELETE FROM casino_active_tables  WHERE mode = ?", (mode,))
    conn.execute("DELETE FROM casino_active_players WHERE mode = ?", (mode,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Persistent BJ shoe state helpers
# ---------------------------------------------------------------------------

def save_bj_shoe_state(
    shoe_json: str,
    decks_count: int,
    cards_remaining: int,
    loaded_from_restart: int = 0,
    rebuild_reason: str = "",
) -> None:
    """Upsert the dedicated blackjack_shoe_state row (game='bj')."""
    from datetime import datetime as _dt
    conn = get_connection()
    now  = _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO blackjack_shoe_state
            (game, shoe_json, decks_count, cards_remaining,
             last_saved_at, loaded_from_restart, rebuild_reason)
        VALUES ('bj', ?, ?, ?, ?, ?, ?)
        ON CONFLICT(game) DO UPDATE SET
            shoe_json           = excluded.shoe_json,
            decks_count         = excluded.decks_count,
            cards_remaining     = excluded.cards_remaining,
            last_saved_at       = excluded.last_saved_at,
            loaded_from_restart = excluded.loaded_from_restart,
            rebuild_reason      = excluded.rebuild_reason
        """,
        (shoe_json, decks_count, cards_remaining, now, loaded_from_restart, rebuild_reason),
    )
    conn.commit()
    conn.close()


def load_bj_shoe_state() -> "dict | None":
    """Return the blackjack_shoe_state row for game='bj', or None."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM blackjack_shoe_state WHERE game = 'bj'"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_rbj_shoe_state(shoe_json: str, decks_count: int, cards_remaining: int) -> None:
    """Upsert the dedicated blackjack_shoe_state row (game='rbj')."""
    from datetime import datetime as _dt
    conn = get_connection()
    now  = _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO blackjack_shoe_state
            (game, shoe_json, decks_count, cards_remaining,
             last_saved_at, loaded_from_restart, rebuild_reason)
        VALUES ('rbj', ?, ?, ?, ?, 0, '')
        ON CONFLICT(game) DO UPDATE SET
            shoe_json       = excluded.shoe_json,
            decks_count     = excluded.decks_count,
            cards_remaining = excluded.cards_remaining,
            last_saved_at   = excluded.last_saved_at
        """,
        (shoe_json, decks_count, cards_remaining, now),
    )
    conn.commit()
    conn.close()


def load_rbj_shoe_state() -> "dict | None":
    """Return the blackjack_shoe_state row for game='rbj', or None."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM blackjack_shoe_state WHERE game = 'rbj'"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_casino_player(mode: str, data: dict) -> None:
    """Upsert a player row in casino_active_players."""
    from datetime import datetime as _dt
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO casino_active_players
            (mode, username, user_id, bet, hand_json, status, doubled,
             joined_at, acted_at, payout, result)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)
        ON CONFLICT(mode, username) DO UPDATE SET
            user_id   = excluded.user_id,
            bet       = excluded.bet,
            hand_json = excluded.hand_json,
            status    = excluded.status,
            doubled   = excluded.doubled,
            payout    = excluded.payout,
            result    = excluded.result,
            acted_at  = datetime('now')
        """,
        (
            mode,
            data.get("username", ""),
            data.get("user_id", ""),
            int(data.get("bet", 0)),
            data.get("hand_json", "[]"),
            data.get("status", "lobby"),
            int(data.get("doubled", 0)),
            _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            int(data.get("payout", 0)),
            data.get("result", ""),
        ),
    )
    conn.commit()
    conn.close()


def load_casino_players(mode: str) -> list[dict]:
    """Return all casino_active_players for mode, ordered by id."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM casino_active_players WHERE mode = ? ORDER BY id ASC",
        (mode,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def clear_casino_players(mode: str) -> None:
    """Delete all casino_active_players rows for mode."""
    conn = get_connection()
    conn.execute("DELETE FROM casino_active_players WHERE mode = ?", (mode,))
    conn.commit()
    conn.close()


def save_round_result(
    mode: str, round_id: str, username: str, user_id: str,
    bet: int, result: str, payout: int, net: int,
) -> None:
    """INSERT OR IGNORE a single round result (dedup key: mode+round_id+username)."""
    from datetime import datetime as _dt
    conn = get_connection()
    conn.execute(
        """
        INSERT OR IGNORE INTO casino_round_results
            (mode, round_id, username, user_id, bet, result, payout, net, paid, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            mode, round_id, username, user_id,
            int(bet), result, int(payout), int(net),
            _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()


def mark_result_paid(mode: str, round_id: str, username: str) -> None:
    """Mark a round result as paid=1."""
    conn = get_connection()
    conn.execute(
        "UPDATE casino_round_results SET paid = 1 "
        "WHERE mode = ? AND round_id = ? AND username = ?",
        (mode, round_id, username),
    )
    conn.commit()
    conn.close()


def get_unpaid_results(mode: str, round_id: str) -> list[dict]:
    """Return all casino_round_results for the round that are not yet paid."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM casino_round_results "
        "WHERE mode = ? AND round_id = ? AND paid = 0",
        (mode, round_id),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def is_result_paid(mode: str, round_id: str, username: str) -> bool:
    """Return True if the result for this mode+round+player is already paid."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT paid FROM casino_round_results "
        "WHERE mode = ? AND round_id = ? AND username = ?",
        (mode, round_id, username),
    ).fetchone()
    conn.close()
    return bool(row and row["paid"])


# ---------------------------------------------------------------------------
# Bank pending notifications
# ---------------------------------------------------------------------------

def add_bank_notification(receiver_username: str, sender_username: str,
                           amount_received: int, fee: int) -> None:
    """Save a pending bank notification for an offline receiver."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO bank_notifications
            (receiver_username, sender_username, amount_received, fee,
             timestamp, delivered, delivered_at)
        VALUES (?, ?, ?, ?, datetime('now'), 0, NULL)
        """,
        (receiver_username.lower().lstrip("@").strip(),
         sender_username.lower().lstrip("@").strip(),
         amount_received, fee),
    )
    conn.commit()
    conn.close()


def get_pending_bank_notifications(username: str) -> list[dict]:
    """Return all undelivered notifications for *username*."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM bank_notifications
        WHERE receiver_username = ? AND delivered = 0
        ORDER BY timestamp ASC
        """,
        (username.lower().lstrip("@").strip(),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_bank_notifications_delivered(username: str) -> None:
    """Mark every pending notification for *username* as delivered."""
    conn = get_connection()
    conn.execute(
        """
        UPDATE bank_notifications
        SET delivered = 1, delivered_at = datetime('now')
        WHERE receiver_username = ? AND delivered = 0
        """,
        (username.lower().lstrip("@").strip(),),
    )
    conn.commit()
    conn.close()


def get_recent_bank_notifications(username: str, limit: int = 10) -> list[dict]:
    """Return the most recent notifications (delivered or not) for *username*."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM bank_notifications
        WHERE receiver_username = ?
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (username.lower().lstrip("@").strip(), limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Subscriber DM functions ────────────────────────────────────────────────────

def upsert_subscriber(username: str, user_id: str, conversation_id: str | None = None) -> None:
    """Create or update a subscriber record. Preserves subscribed flag."""
    uname = username.lower().lstrip("@").strip()
    conn = get_connection()
    existing = conn.execute(
        "SELECT subscribed FROM subscriber_users WHERE username = ?", (uname,)
    ).fetchone()
    if existing:
        if conversation_id:
            conn.execute(
                """UPDATE subscriber_users
                   SET user_id = ?, conversation_id = ?, dm_available = 1,
                       last_seen_at = datetime('now')
                   WHERE username = ?""",
                (user_id, conversation_id, uname),
            )
        else:
            conn.execute(
                "UPDATE subscriber_users SET user_id = ?, last_seen_at = datetime('now') WHERE username = ?",
                (user_id, uname),
            )
    else:
        conn.execute(
            """INSERT INTO subscriber_users
               (username, user_id, conversation_id, subscribed, last_seen_at, dm_available)
               VALUES (?, ?, ?, 0, datetime('now'), ?)""",
            (uname, user_id, conversation_id, 1 if conversation_id else 0),
        )
    conn.commit()
    conn.close()


def get_user_by_username_via_id(user_id: str) -> dict | None:
    """Return users row (user_id, username, balance) for a given user_id."""
    conn = get_connection()
    row = conn.execute(
        "SELECT user_id, username, balance FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_subscriber(username: str) -> dict | None:
    """Lookup subscriber by username (case-insensitive)."""
    uname = username.lower().lstrip("@").strip()
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM subscriber_users WHERE username = ?", (uname,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_subscriber_by_user_id(user_id: str) -> dict | None:
    """Lookup subscriber by Highrise user_id."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM subscriber_users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def set_subscribed(username: str, subscribed: bool) -> None:
    """Enable or disable subscription for a user (by username)."""
    uname = username.lower().lstrip("@").strip()
    conn = get_connection()
    conn.execute(
        """UPDATE subscriber_users
           SET subscribed = ?, subscribed_at = CASE WHEN ? = 1 THEN datetime('now') ELSE subscribed_at END
           WHERE username = ?""",
        (1 if subscribed else 0, 1 if subscribed else 0, uname),
    )
    conn.commit()
    conn.close()


def set_subscribed_by_user_id(user_id: str, subscribed: bool) -> None:
    """Enable or disable subscription for a user, looking up by user_id first."""
    conn = get_connection()
    val = 1 if subscribed else 0
    conn.execute(
        """UPDATE subscriber_users
           SET subscribed = ?,
               subscribed_at = CASE WHEN ? = 1 THEN datetime('now') ELSE subscribed_at END
           WHERE user_id = ?""",
        (val, val, user_id),
    )
    conn.commit()
    conn.close()


def set_dm_available_by_user_id(user_id: str, available: bool) -> None:
    """Mark whether a subscriber's DM channel is working (by user_id)."""
    conn = get_connection()
    conn.execute(
        "UPDATE subscriber_users SET dm_available = ? WHERE user_id = ?",
        (1 if available else 0, user_id),
    )
    conn.commit()
    conn.close()


def upsert_subscriber_by_user_id(
    user_id: str,
    username: str,
    conversation_id: str | None = None,
) -> None:
    """
    Create or update a subscriber record, looking up by user_id FIRST,
    then by username. Prevents duplicate rows. Preserves subscribed flag.
    """
    uname = username.lower().lstrip("@").strip()
    conn = get_connection()

    # 1. Find existing row by user_id
    row_by_id = conn.execute(
        "SELECT username, subscribed, conversation_id FROM subscriber_users WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    if row_by_id:
        existing_uname = row_by_id["username"]
        existing_conv  = row_by_id["conversation_id"]
        best_conv = conversation_id or existing_conv
        conn.execute(
            """UPDATE subscriber_users
               SET username = ?,
                   conversation_id = ?,
                   dm_available = CASE WHEN ? IS NOT NULL THEN 1 ELSE dm_available END,
                   last_seen_at = datetime('now')
               WHERE user_id = ?""",
            (uname, best_conv, best_conv, user_id),
        )
        # Remove stale username-keyed duplicate row if it exists (different from this row)
        if existing_uname and existing_uname != uname:
            conn.execute(
                "DELETE FROM subscriber_users WHERE username = ? AND user_id != ?",
                (existing_uname, user_id),
            )
        # Also remove any stale row keyed on the new uname belonging to a different user_id
        conn.execute(
            "DELETE FROM subscriber_users WHERE username = ? AND user_id != ?",
            (uname, user_id),
        )
        conn.commit()
        conn.close()
        return

    # 2. Find existing row by username (no user_id match yet)
    row_by_name = conn.execute(
        "SELECT username, subscribed, conversation_id FROM subscriber_users WHERE username = ?",
        (uname,),
    ).fetchone()

    if row_by_name:
        existing_conv = row_by_name["conversation_id"]
        best_conv = conversation_id or existing_conv
        conn.execute(
            """UPDATE subscriber_users
               SET user_id = ?,
                   conversation_id = ?,
                   dm_available = CASE WHEN ? IS NOT NULL THEN 1 ELSE dm_available END,
                   last_seen_at = datetime('now')
               WHERE username = ?""",
            (user_id, best_conv, best_conv, uname),
        )
        conn.commit()
        conn.close()
        return

    # 3. No existing row — insert with subscribed=0 (explicit subscribe call sets it)
    conn.execute(
        """INSERT INTO subscriber_users
           (username, user_id, conversation_id, subscribed, last_seen_at, dm_available)
           VALUES (?, ?, ?, 0, datetime('now'), ?)""",
        (uname, user_id, conversation_id, 1 if conversation_id else 0),
    )
    conn.commit()
    conn.close()


def force_subscribe_user(user_id: str | None, username: str) -> dict:
    """
    Force-set subscribed=True for a user regardless of previous state.
    Looks up by user_id first, then by username. Returns the updated row dict.
    """
    uname = username.lower().lstrip("@").strip()
    conn = get_connection()

    row = None
    if user_id:
        row = conn.execute(
            "SELECT * FROM subscriber_users WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT * FROM subscriber_users WHERE username = ?", (uname,)
        ).fetchone()

    if row:
        target_uname = row["username"]
        conn.execute(
            """UPDATE subscriber_users
               SET subscribed = 1,
                   manually_unsubscribed = 0,
                   subscribed_at = datetime('now'),
                   dm_available = CASE WHEN conversation_id IS NOT NULL THEN 1 ELSE dm_available END
               WHERE username = ?""",
            (target_uname,),
        )
        conn.commit()
        result = dict(row)
        result["subscribed"] = 1
        result["manually_unsubscribed"] = 0
    else:
        # Create a minimal row
        conn.execute(
            """INSERT OR IGNORE INTO subscriber_users
               (username, user_id, subscribed, manually_unsubscribed, last_seen_at)
               VALUES (?, ?, 1, 0, datetime('now'))""",
            (uname, user_id or ""),
        )
        conn.commit()
        result = {"username": uname, "user_id": user_id, "subscribed": 1,
                  "conversation_id": None, "dm_available": 0}

    conn.close()
    ensure_notify_prefs(uname)
    return result


def merge_duplicate_subscriber_rows(username: str) -> dict:
    """
    Find and merge all subscriber_users rows that share the same username.
    Keeps the row with the best conversation_id; merges subscribed flag.
    Returns a summary dict with merge details.
    """
    uname = username.lower().lstrip("@").strip()
    conn = get_connection()
    rows = [
        dict(r) for r in conn.execute(
            "SELECT rowid, * FROM subscriber_users WHERE LOWER(username) = ?", (uname,)
        ).fetchall()
    ]
    result = {
        "found": len(rows),
        "merged": 0,
        "user_id": None,
        "conversation_id": None,
        "subscribed": False,
    }

    if not rows:
        conn.close()
        return result

    if len(rows) == 1:
        r = rows[0]
        result["user_id"]        = r.get("user_id")
        result["conversation_id"] = r.get("conversation_id")
        result["subscribed"]      = bool(r.get("subscribed"))
        conn.close()
        return result

    # Pick primary: prefer row with conversation_id + subscribed=True
    def _score(r: dict) -> int:
        return (2 if r.get("conversation_id") else 0) + (1 if r.get("subscribed") else 0)

    rows.sort(key=_score, reverse=True)
    primary   = rows[0]
    secondary = rows[1:]

    best_conv     = primary.get("conversation_id")
    best_user_id  = primary.get("user_id")
    best_subbed   = bool(primary.get("subscribed"))
    best_man_unsub = bool(primary.get("manually_unsubscribed"))

    for r in secondary:
        if not best_conv and r.get("conversation_id"):
            best_conv = r["conversation_id"]
        if not best_user_id and r.get("user_id"):
            best_user_id = r["user_id"]
        if r.get("subscribed") and not r.get("manually_unsubscribed"):
            best_subbed = True
            best_man_unsub = False
        conn.execute(
            "DELETE FROM subscriber_users WHERE rowid = ?", (r["rowid"],)
        )
        result["merged"] += 1

    conn.execute(
        """UPDATE subscriber_users
           SET user_id = ?,
               conversation_id = ?,
               subscribed = ?,
               manually_unsubscribed = ?,
               dm_available = CASE WHEN ? IS NOT NULL THEN 1 ELSE dm_available END
           WHERE rowid = ?""",
        (best_user_id, best_conv, 1 if best_subbed else 0,
         1 if best_man_unsub else 0, best_conv, primary["rowid"]),
    )
    conn.commit()
    conn.close()

    result["user_id"]        = best_user_id
    result["conversation_id"] = best_conv
    result["subscribed"]      = best_subbed
    return result


def set_dm_available(username: str, available: bool) -> None:
    """Mark whether a subscriber's DM channel is working."""
    uname = username.lower().lstrip("@").strip()
    conn = get_connection()
    conn.execute(
        "UPDATE subscriber_users SET dm_available = ? WHERE username = ?",
        (1 if available else 0, uname),
    )
    conn.commit()
    conn.close()


def set_subscriber_last_dm(username: str) -> None:
    """Record when a DM was last sent to a subscriber."""
    uname = username.lower().lstrip("@").strip()
    conn = get_connection()
    conn.execute(
        "UPDATE subscriber_users SET last_dm_at = datetime('now') WHERE username = ?",
        (uname,),
    )
    conn.commit()
    conn.close()


def get_all_subscribed_with_dm() -> list[dict]:
    """Return subscribers who have an active DM conversation (dm_available=1)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM subscriber_users
           WHERE subscribed = 1
             AND conversation_id IS NOT NULL
             AND dm_available = 1
           ORDER BY subscribed_at ASC""",
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_tip_auto_subscribed(username: str) -> None:
    """Set auto_subscribed_from_tip=1 for a subscriber (called after gold tip)."""
    uname = username.lower().lstrip("@").strip()
    conn = get_connection()
    conn.execute(
        "UPDATE subscriber_users SET auto_subscribed_from_tip = 1 WHERE username = ?",
        (uname,),
    )
    conn.commit()
    conn.close()


def log_subscriber_announcement(
    sender_username: str,
    target_type: str,
    message: str,
    delivered_count: int,
    pending_count: int,
    failed_count: int,
    target_username: str | None = None,
) -> None:
    """Record a subscriber announcement in the audit log."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO subscriber_announcements
            (sender_username, target_type, target_username, message,
             delivered_count, pending_count, failed_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (sender_username.lower(), target_type, target_username, message[:249],
         delivered_count, pending_count, failed_count),
    )
    conn.commit()
    conn.close()


def add_pending_sub_message(
    receiver_username: str, message: str, message_type: str = "general"
) -> None:
    """Queue an outside-room message for delivery when the user next DMs the bot."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO pending_subscriber_messages
            (receiver_username, message, message_type)
        VALUES (?, ?, ?)
        """,
        (receiver_username.lower().strip(), message[:249], message_type),
    )
    conn.commit()
    conn.close()


def get_pending_sub_messages(username: str) -> list[dict]:
    """Return all undelivered pending subscriber messages for *username*."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM pending_subscriber_messages
        WHERE receiver_username = ? AND delivered = 0
        ORDER BY created_at ASC
        """,
        (username.lower().strip(),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_pending_sub_delivered(msg_id: int) -> None:
    """Mark a pending subscriber message as delivered."""
    conn = get_connection()
    conn.execute(
        """
        UPDATE pending_subscriber_messages
        SET delivered = 1, delivered_at = datetime('now')
        WHERE id = ?
        """,
        (msg_id,),
    )
    conn.commit()
    conn.close()


def record_pending_sub_failed(msg_id: int, error: str) -> None:
    """Increment delivery_attempts and store last_error for a pending sub message."""
    conn = get_connection()
    conn.execute(
        """
        UPDATE pending_subscriber_messages
        SET delivery_attempts = delivery_attempts + 1, last_error = ?
        WHERE id = ?
        """,
        (str(error)[:200], msg_id),
    )
    conn.commit()
    conn.close()


def get_all_subscribed_no_dm() -> list[dict]:
    """Return subscribed users who have no usable DM channel."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM subscriber_users
        WHERE subscribed = 1
          AND (conversation_id IS NULL OR dm_available = 0)
        ORDER BY username ASC
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_subscribers_staff() -> list[dict]:
    """Return all subscriber records for staff view."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM subscriber_users ORDER BY subscribed DESC, username ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def record_notification_attempt_failed(notif_id: int, error: str) -> None:
    """Increment delivery_attempts and store last_error for a notification."""
    conn = get_connection()
    conn.execute(
        """
        UPDATE bank_notifications
        SET delivery_attempts = delivery_attempts + 1,
            last_error = ?
        WHERE id = ?
        """,
        (str(error)[:200], notif_id),
    )
    conn.commit()
    conn.close()


def get_pending_notifications_for_staff(username: str) -> list[dict]:
    """Return all undelivered notifications for *username* (staff view)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM bank_notifications
        WHERE receiver_username = ? AND delivered = 0
        ORDER BY timestamp ASC
        """,
        (username.lower().lstrip("@").strip(),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Notification preferences
# ---------------------------------------------------------------------------

_DEFAULT_PREFS: dict = {
    "bank_alerts": 1, "event_alerts": 1, "gold_alerts": 1,
    "vip_alerts": 1, "casino_alerts": 1, "quest_alerts": 1,
    "shop_alerts": 1, "announcement_alerts": 1, "staff_alerts": 1,
    "dm_alerts": 1, "room_whisper_alerts": 1,
}


def ensure_notify_prefs(username: str) -> None:
    """Create default notification preferences row if not present."""
    uname = username.lower().lstrip("@").strip()
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO notification_preferences (username) VALUES (?)", (uname,)
    )
    conn.commit()
    conn.close()


def get_notify_prefs(username: str) -> dict:
    """Return notification prefs dict for *username*. Returns all-ON defaults if missing."""
    uname = username.lower().lstrip("@").strip()
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM notification_preferences WHERE username = ?", (uname,)
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"username": uname, **_DEFAULT_PREFS}


def set_notify_pref(username: str, column: str, value: int) -> None:
    """Set a single notification preference column (must be a valid column name)."""
    _VALID = {
        "bank_alerts", "event_alerts", "gold_alerts", "vip_alerts",
        "casino_alerts", "quest_alerts", "shop_alerts", "announcement_alerts",
        "staff_alerts", "dm_alerts", "room_whisper_alerts",
    }
    if column not in _VALID:
        raise ValueError(f"Invalid pref column: {column!r}")
    uname = username.lower().lstrip("@").strip()
    conn = get_connection()
    conn.execute(
        f"INSERT OR IGNORE INTO notification_preferences (username) VALUES (?)", (uname,)
    )
    conn.execute(
        f"UPDATE notification_preferences SET {column} = ? WHERE username = ?",
        (value, uname),
    )
    conn.commit()
    conn.close()


def set_all_notify_prefs(username: str, value: int) -> None:
    """Set all notification preference columns to *value* (0 or 1)."""
    uname = username.lower().lstrip("@").strip()
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO notification_preferences (username) VALUES (?)", (uname,)
    )
    conn.execute(
        """UPDATE notification_preferences
           SET bank_alerts=?, event_alerts=?, gold_alerts=?, vip_alerts=?,
               casino_alerts=?, quest_alerts=?, shop_alerts=?,
               announcement_alerts=?, staff_alerts=?, dm_alerts=?,
               room_whisper_alerts=?
           WHERE username=?""",
        (value,) * 11 + (uname,),
    )
    conn.commit()
    conn.close()


def get_notify_stats() -> dict:
    """Return aggregate subscriber stats for /notifystats."""
    conn = get_connection()
    total = conn.execute(
        "SELECT COUNT(*) FROM subscriber_users"
    ).fetchone()[0]
    dm_connected = conn.execute(
        "SELECT COUNT(*) FROM subscriber_users WHERE subscribed=1 AND dm_available=1 AND conversation_id IS NOT NULL"
    ).fetchone()[0]
    unsubscribed = conn.execute(
        "SELECT COUNT(*) FROM subscriber_users WHERE subscribed=0"
    ).fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM pending_notifications WHERE delivered=0"
    ).fetchone()[0]
    pending += conn.execute(
        "SELECT COUNT(*) FROM pending_subscriber_messages WHERE delivered=0"
    ).fetchone()[0]
    conn.close()
    return {
        "total": total,
        "dm_connected": dm_connected,
        "unsubscribed": unsubscribed,
        "pending": pending,
    }


def set_subscriber_manually_unsubscribed(username: str, value: bool) -> None:
    """Set manually_unsubscribed flag and record unsubscribed_at timestamp."""
    uname = username.lower().lstrip("@").strip()
    conn = get_connection()
    if value:
        conn.execute(
            """UPDATE subscriber_users
               SET manually_unsubscribed = 1, unsubscribed_at = datetime('now')
               WHERE username = ?""",
            (uname,),
        )
    else:
        conn.execute(
            "UPDATE subscriber_users SET manually_unsubscribed = 0 WHERE username = ?",
            (uname,),
        )
    conn.commit()
    conn.close()


def auto_subscribe_whisper(username: str, user_id: str) -> bool:
    """
    Auto-subscribe a user from a whisper event.
    Respects manually_unsubscribed + tip_resubscribe setting.
    Returns True if the user was newly subscribed.
    """
    uname = username.lower().lstrip("@").strip()
    conn = get_connection()
    existing = conn.execute(
        "SELECT subscribed, manually_unsubscribed FROM subscriber_users WHERE username = ?",
        (uname,)
    ).fetchone()
    conn.close()

    if existing:
        if existing["manually_unsubscribed"]:
            return False
        if existing["subscribed"]:
            upsert_subscriber(uname, user_id)
            return False

    upsert_subscriber(uname, user_id)
    set_subscribed(uname, True)
    conn2 = get_connection()
    conn2.execute(
        "UPDATE subscriber_users SET auto_subscribed_from_whisper = 1 WHERE username = ?",
        (uname,),
    )
    conn2.commit()
    conn2.close()
    ensure_notify_prefs(uname)
    return True


# ---------------------------------------------------------------------------
# Pending notifications (typed, per-notification-type)
# ---------------------------------------------------------------------------

def add_pending_notification(
    receiver_username: str, notification_type: str, message: str
) -> None:
    """Queue a typed notification for later delivery."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO pending_notifications
               (receiver_username, notification_type, message)
           VALUES (?, ?, ?)""",
        (receiver_username.lower().strip(), notification_type, message[:249]),
    )
    conn.commit()
    conn.close()


def get_pending_notifications(username: str) -> list[dict]:
    """Return all undelivered pending_notifications for *username*."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM pending_notifications
           WHERE receiver_username = ? AND delivered = 0
           ORDER BY created_at ASC""",
        (username.lower().strip(),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_pending_notification_delivered(notif_id: int) -> None:
    """Mark a pending_notification as delivered."""
    conn = get_connection()
    conn.execute(
        """UPDATE pending_notifications
           SET delivered=1, delivered_at=datetime('now')
           WHERE id=?""",
        (notif_id,),
    )
    conn.commit()
    conn.close()


def mark_pending_notification_failed(notif_id: int, error: str) -> None:
    """Increment attempts and store error for a pending_notification."""
    conn = get_connection()
    conn.execute(
        """UPDATE pending_notifications
           SET delivery_attempts = delivery_attempts + 1, last_error = ?
           WHERE id = ?""",
        (str(error)[:200], notif_id),
    )
    conn.commit()
    conn.close()


def mark_all_pending_notifications_read(username: str) -> None:
    """Mark all pending_notifications for *username* as delivered (cleared)."""
    conn = get_connection()
    conn.execute(
        """UPDATE pending_notifications
           SET delivered=1, delivered_at=datetime('now')
           WHERE receiver_username=? AND delivered=0""",
        (username.lower().strip(),),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Notification audit log
# ---------------------------------------------------------------------------

def log_notification(
    username: str,
    notification_type: str,
    channel: str,
    message: str,
    status: str,
    error_message: str = "",
) -> None:
    """Insert one row into notification_logs."""
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO notification_logs
                   (username, notification_type, channel, message, status, error_message)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                username.lower().strip(),
                notification_type,
                channel,
                message[:249],
                status,
                (error_message or "")[:200],
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[DB] log_notification error: {exc!r}")


# ---------------------------------------------------------------------------
# Admin action log helpers
# ---------------------------------------------------------------------------

def log_admin_action(
    actor_username: str,
    target_username: str,
    action: str,
    old_value: str = "",
    new_value: str = "",
    reason: str = "",
) -> None:
    """Insert one row into admin_action_logs."""
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO admin_action_logs
                   (actor_username, target_username, action, old_value, new_value, reason)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (actor_username, target_username, action,
             str(old_value)[:200], str(new_value)[:200], str(reason)[:200]),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[DB] log_admin_action error: {exc!r}")


def get_bot_setting(key: str, default: str = "") -> str:
    """Retrieve a persistent bot setting by key. Returns default if not set."""
    try:
        conn = get_connection()
        row  = conn.execute("SELECT value FROM bot_settings WHERE key = ?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else default
    except Exception:
        return default


def set_bot_setting(key: str, value: str) -> None:
    """Upsert a persistent bot setting."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO bot_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def clear_equipped_title(user_id: str) -> None:
    """Unequip the player's current title."""
    conn = get_connection()
    conn.execute(
        "UPDATE users SET equipped_title = '', equipped_title_id = '' WHERE user_id = ?",
        (user_id,)
    )
    conn.commit()
    conn.close()


def clear_equipped_badge(user_id: str) -> None:
    """Unequip the player's current badge."""
    conn = get_connection()
    conn.execute(
        "UPDATE users SET equipped_badge = '', equipped_badge_id = '' WHERE user_id = ?",
        (user_id,)
    )
    conn.commit()
    conn.close()


def get_admin_log_by_id(log_id: int) -> dict | None:
    """Return a single admin action log entry by ID."""
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT * FROM admin_action_logs WHERE id = ?", (log_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def get_admin_logs(target_username: str | None = None, limit: int = 10) -> list[dict]:
    """Return recent admin action log entries, optionally filtered by target."""
    try:
        conn = get_connection()
        if target_username:
            rows = conn.execute(
                """SELECT * FROM admin_action_logs
                   WHERE LOWER(target_username) = LOWER(?)
                   ORDER BY id DESC LIMIT ?""",
                (target_username, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM admin_action_logs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Direct balance / XP / level setters  (admin use)
# ---------------------------------------------------------------------------

def set_balance_direct(user_id: str, amount: int) -> int:
    """Set a player's balance to an exact amount (floor 0). Returns new balance."""
    amount = max(0, int(amount))
    conn = get_connection()
    conn.execute(
        "UPDATE users SET balance = ? WHERE user_id = ?", (amount, user_id)
    )
    conn.commit()
    conn.close()
    return amount


def set_xp_direct(user_id: str, xp: int) -> tuple[int, int]:
    """Set XP directly and recompute level. Returns (new_xp, new_level)."""
    xp    = max(0, int(xp))
    level = _xp_to_level(xp)
    conn  = get_connection()
    conn.execute(
        "UPDATE users SET xp = ?, level = ? WHERE user_id = ?",
        (xp, level, user_id),
    )
    conn.commit()
    conn.close()
    return xp, level


def set_level_direct(user_id: str, level: int) -> tuple[int, int]:
    """Set level and matching XP. Returns (new_xp, new_level)."""
    level = max(1, int(level))
    xp    = xp_for_level(level)
    conn  = get_connection()
    conn.execute(
        "UPDATE users SET xp = ?, level = ? WHERE user_id = ?",
        (xp, level, user_id),
    )
    conn.commit()
    conn.close()
    return xp, level


# ---------------------------------------------------------------------------
# Item grant / revoke helpers  (admin use)
# ---------------------------------------------------------------------------

def grant_item(user_id: str, item_id: str, item_type: str) -> None:
    """Insert item into owned_items (idempotent)."""
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO owned_items (user_id, item_id, item_type) VALUES (?, ?, ?)",
        (user_id, item_id, item_type),
    )
    conn.commit()
    conn.close()


def revoke_item(user_id: str, item_id: str) -> None:
    """Delete item from owned_items and clear equipped slot if equipped."""
    conn = get_connection()
    row = conn.execute(
        "SELECT item_type FROM owned_items WHERE user_id = ? AND item_id = ?",
        (user_id, item_id),
    ).fetchone()
    conn.execute(
        "DELETE FROM owned_items WHERE user_id = ? AND item_id = ?",
        (user_id, item_id),
    )
    if row:
        it = row["item_type"]
        if it == "badge":
            conn.execute(
                "UPDATE users SET equipped_badge='', equipped_badge_id='' "
                "WHERE user_id=? AND equipped_badge_id=?",
                (user_id, item_id),
            )
        elif it == "title":
            conn.execute(
                "UPDATE users SET equipped_title='', equipped_title_id='' "
                "WHERE user_id=? AND equipped_title_id=?",
                (user_id, item_id),
            )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Event points direct setter  (admin use)
# ---------------------------------------------------------------------------

def set_event_points_direct(user_id: str, amount: int) -> None:
    """Set event points to an exact amount (floor 0). Does not touch lifetime total."""
    amount = max(0, int(amount))
    conn   = get_connection()
    conn.execute(
        """INSERT INTO event_points (user_id, points, updated_at) VALUES (?, ?, datetime('now'))
           ON CONFLICT(user_id) DO UPDATE SET points = ?, updated_at = datetime('now')""",
        (user_id, amount, amount),
    )
    conn.commit()
    conn.close()


def get_event_points_for_user(username: str) -> int | None:
    """Look up event points by username (case-insensitive). Returns None if not found."""
    conn = get_connection()
    row  = conn.execute(
        """SELECT ep.points FROM event_points ep
           JOIN users u ON u.user_id = ep.user_id
           WHERE lower(u.username) = lower(?)""",
        (username.lstrip("@").strip(),)
    ).fetchone()
    conn.close()
    return row["points"] if row else None


# ---------------------------------------------------------------------------
# Reputation direct setter  (admin use)
# ---------------------------------------------------------------------------

def set_rep_direct(username: str, amount: int) -> bool:
    """Set rep_received to exact amount for username. Returns True if found."""
    amount = max(0, int(amount))
    conn   = get_connection()
    conn.execute(
        "UPDATE reputation SET rep_received = ? WHERE LOWER(username) = ?",
        (amount, username.lower()),
    )
    changed = conn.execute("SELECT changes()").fetchone()[0]
    conn.commit()
    conn.close()
    return changed > 0


# ---------------------------------------------------------------------------
# Casino stats reset helpers  (admin use)
# ---------------------------------------------------------------------------

def reset_bj_stats_for_user(user_id: str) -> None:
    """Reset a player's BJ stats to zero."""
    conn = get_connection()
    conn.execute(
        """UPDATE bj_stats
           SET bj_wins=0, bj_losses=0, bj_pushes=0, bj_blackjacks=0,
               bj_total_bet=0, bj_total_won=0, bj_total_lost=0
           WHERE user_id=?""",
        (user_id,),
    )
    try:
        conn.execute("DELETE FROM bj_daily WHERE user_id=?", (user_id,))
    except Exception:
        pass
    conn.commit()
    conn.close()


def reset_rbj_stats_for_user(user_id: str) -> None:
    """Reset a player's RBJ stats to zero."""
    conn = get_connection()
    conn.execute(
        """UPDATE rbj_stats
           SET rbj_wins=0, rbj_losses=0, rbj_pushes=0, rbj_blackjacks=0,
               rbj_total_bet=0, rbj_total_won=0, rbj_total_lost=0
           WHERE user_id=?""",
        (user_id,),
    )
    try:
        conn.execute("DELETE FROM rbj_daily WHERE user_id=?", (user_id,))
    except Exception:
        pass
    conn.commit()
    conn.close()


def reset_poker_stats_for_user(user_id: str) -> None:
    """Reset a player's poker stats to zero."""
    conn = get_connection()
    conn.execute(
        """UPDATE poker_stats
           SET hands_played=0, wins=0, losses=0, folds=0,
               total_won=0, total_lost=0, total_buyin=0,
               biggest_pot=0, allins=0, net_profit=0,
               biggest_win=0, current_win_streak=0, best_win_streak=0, showdowns=0
           WHERE user_id=?""",
        (user_id,),
    )
    try:
        conn.execute(
            "DELETE FROM poker_daily_limits WHERE user_id=?", (user_id,)
        )
    except Exception:
        pass
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# VIP list helper  (admin use)
# ---------------------------------------------------------------------------

def get_vip_list() -> list[str]:
    """Return usernames of all VIP players."""
    try:
        conn  = get_connection()
        rows  = conn.execute(
            """SELECT u.username FROM owned_items oi
               JOIN users u ON u.user_id = oi.user_id
               WHERE oi.item_id = 'vip'
               ORDER BY u.username ASC""",
        ).fetchall()
        conn.close()
        return [r["username"] for r in rows]
    except Exception:
        return []


def get_top_gold_donors(limit: int = 10) -> list[dict]:
    """Return top gold donors from gold_tip_events, summed by from_username."""
    try:
        bot_filter = _get_bot_name_filter()
        conn = get_connection()
        rows = conn.execute(
            """SELECT from_username AS username,
                      CAST(SUM(gold_amount) AS INTEGER) AS total_gold
               FROM gold_tip_events
               WHERE from_username != ''
               GROUP BY LOWER(from_username)
               ORDER BY total_gold DESC
               LIMIT ?""",
            (limit + 20,),
        ).fetchall()
        conn.close()
        filtered = [
            dict(r) for r in rows
            if r["username"].lower() not in bot_filter
        ]
        return filtered[:limit]
    except Exception:
        return []


def get_total_gold_donated() -> int:
    """Sum all gold tips from real players (bots excluded) across gold_tip_events."""
    try:
        bot_filter = _get_bot_name_filter()
        conn = get_connection()
        rows = conn.execute(
            """SELECT from_username, CAST(SUM(gold_amount) AS INTEGER) AS total
               FROM gold_tip_events
               WHERE from_username != ''
               GROUP BY LOWER(from_username)"""
        ).fetchall()
        conn.close()
        return sum(
            int(r["total"]) for r in rows
            if r["from_username"].lower() not in bot_filter
        )
    except Exception:
        return 0


def get_user_gold_donated(username: str) -> dict:
    """Return total donated gold, record count, and last tip timestamp for one user."""
    try:
        conn = get_connection()
        row = conn.execute(
            """SELECT from_username,
                      CAST(SUM(gold_amount) AS INTEGER) AS total_gold,
                      COUNT(*) AS record_count,
                      MAX(created_at) AS last_tip
               FROM gold_tip_events
               WHERE LOWER(from_username) = LOWER(?)
               GROUP BY LOWER(from_username)""",
            (username,),
        ).fetchone()
        conn.close()
        return dict(row) if row else {}
    except Exception:
        return {}


def record_p2p_gold_tip(
    sender_id: str,
    sender_username: str,
    receiver_id: str,
    receiver_username: str,
    amount: float,
    event_id: str,
) -> bool:
    """Log a real player-to-player gold tip. Returns True if inserted, False if duplicate."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO p2p_gold_tip_logs
               (event_id, sender_id, sender_username, receiver_id, receiver_username, amount)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (event_id, sender_id, sender_username.lower(),
             receiver_id, receiver_username.lower(), float(amount)),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        return False
    finally:
        conn.close()


def get_top_p2p_senders(limit: int = 5) -> list[dict]:
    """Top players by total gold sent P2P, bots excluded."""
    try:
        bot_filter = _get_bot_name_filter()
        conn = get_connection()
        rows = conn.execute(
            """SELECT sender_username AS username,
                      CAST(SUM(amount) AS INTEGER) AS total_gold
               FROM p2p_gold_tip_logs
               WHERE sender_username != ''
               GROUP BY LOWER(sender_username)
               ORDER BY total_gold DESC
               LIMIT ?""",
            (limit + 20,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows if r["username"].lower() not in bot_filter][:limit]
    except Exception:
        return []


def get_top_p2p_receivers(limit: int = 5) -> list[dict]:
    """Top players by total gold received P2P, bots excluded."""
    try:
        bot_filter = _get_bot_name_filter()
        conn = get_connection()
        rows = conn.execute(
            """SELECT receiver_username AS username,
                      CAST(SUM(amount) AS INTEGER) AS total_gold
               FROM p2p_gold_tip_logs
               WHERE receiver_username != ''
               GROUP BY LOWER(receiver_username)
               ORDER BY total_gold DESC
               LIMIT ?""",
            (limit + 20,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows if r["username"].lower() not in bot_filter][:limit]
    except Exception:
        return []


def get_user_p2p_stats(username: str) -> dict:
    """Return P2P gold sent and received totals for one user."""
    try:
        conn = get_connection()
        sent = conn.execute(
            "SELECT CAST(COALESCE(SUM(amount),0) AS INTEGER) AS t "
            "FROM p2p_gold_tip_logs WHERE LOWER(sender_username)=LOWER(?)",
            (username,),
        ).fetchone()
        recv = conn.execute(
            "SELECT CAST(COALESCE(SUM(amount),0) AS INTEGER) AS t "
            "FROM p2p_gold_tip_logs WHERE LOWER(receiver_username)=LOWER(?)",
            (username,),
        ).fetchone()
        cnt = conn.execute(
            "SELECT COUNT(*) AS c FROM p2p_gold_tip_logs "
            "WHERE LOWER(sender_username)=LOWER(?) OR LOWER(receiver_username)=LOWER(?)",
            (username, username),
        ).fetchone()
        conn.close()
        return {
            "gold_sent":     sent["t"] if sent else 0,
            "gold_received": recv["t"] if recv else 0,
            "records":       cnt["c"]  if cnt  else 0,
        }
    except Exception:
        return {"gold_sent": 0, "gold_received": 0, "records": 0}


def record_gold_donation(
    donor_id: str,
    donor_username: str,
    receiver_bot: str,
    gold_amount: float,
    coins: int,
    event_id: str,
) -> bool:
    """
    Write one gold tip into gold_tip_events for donation tracking.
    Uses INSERT OR IGNORE so it is safe to call even if already present.
    Returns True if a new row was inserted, False if duplicate.
    """
    conn = get_connection()
    try:
        rate = round(float(coins) / max(1.0, float(gold_amount)), 4)
        cur = conn.execute(
            """INSERT OR IGNORE INTO gold_tip_events
               (event_id, from_user_id, from_username, receiving_bot,
                gold_amount, coins_converted, conversion_rate,
                processed_by, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                event_id,
                donor_id,
                donor_username.lower(),
                receiver_bot.lower(),
                float(gold_amount),
                coins,
                rate,
                receiver_bot.lower(),
                "rewarded",
            ),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        return False
    finally:
        conn.close()


def backfill_gold_donations_from_tip_transactions() -> int:
    """
    One-time idempotent backfill: copy every successful tip_transactions row
    into gold_tip_events so !topdonators reflects all-time tipping history.
    Safe to call on every startup — INSERT OR IGNORE skips already-present rows.
    Returns count of newly inserted rows.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT username, gold_amount, coins_awarded,
                      event_id_or_hash, timestamp
               FROM tip_transactions
               WHERE status = 'success'
               AND event_id_or_hash != ''
               AND gold_amount > 0"""
        ).fetchall()
        inserted = 0
        for r in rows:
            rate = round(
                float(r["coins_awarded"]) / max(1.0, float(r["gold_amount"])), 4
            )
            cur = conn.execute(
                """INSERT OR IGNORE INTO gold_tip_events
                   (event_id, from_user_id, from_username, receiving_bot,
                    gold_amount, coins_converted, conversion_rate,
                    processed_by, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    r["event_id_or_hash"],
                    "",
                    r["username"].lower(),
                    "",
                    float(r["gold_amount"]),
                    int(r["coins_awarded"]),
                    rate,
                    "backfill",
                    "rewarded",
                    r["timestamp"],
                ),
            )
            inserted += cur.rowcount
        conn.commit()
        if inserted:
            print(f"[BACKFILL] gold_tip_events ← tip_transactions: {inserted} row(s) added")
        return inserted
    except Exception as e:
        print(f"[BACKFILL] backfill_gold_donations error: {e!r}")
        return 0
    finally:
        conn.close()


# ===========================================================================
# EMOJI BADGE MARKET SYSTEM
# ===========================================================================

# ---------------------------------------------------------------------------
# Emoji badge catalog helpers
# ---------------------------------------------------------------------------

def get_emoji_badge(badge_id: str) -> dict | None:
    """Return one row from emoji_badges or None."""
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT * FROM emoji_badges WHERE badge_id = ?", (badge_id.lower().strip(),)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def get_emoji_badges_page(
    page: int = 1,
    per_page: int = 8,
    purchasable_only: bool = True,
    rarity: str | None = None,
) -> tuple[list[dict], int]:
    """Return (rows_for_page, total_pages). Filters by purchasable and/or rarity."""
    try:
        conn   = get_connection()
        where  = []
        params: list = []
        if purchasable_only:
            where.append("purchasable = 1")
            where.append("COALESCE(enabled, 1) = 1")
        if rarity:
            where.append("rarity = ?")
            params.append(rarity)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        total  = conn.execute(
            f"SELECT COUNT(*) AS n FROM emoji_badges {clause}", params
        ).fetchone()["n"]
        total_pages = max(1, -(-total // per_page))  # ceiling div
        offset = (max(1, page) - 1) * per_page
        rows   = conn.execute(
            f"SELECT rowid, * FROM emoji_badges {clause} "
            "ORDER BY CASE rarity "
            "WHEN 'common' THEN 1 WHEN 'uncommon' THEN 2 WHEN 'rare' THEN 3 "
            "WHEN 'epic' THEN 4 WHEN 'legendary' THEN 5 WHEN 'mythic' THEN 6 "
            "ELSE 7 END, price ASC LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows], total_pages
    except Exception:
        return [], 1


def add_emoji_badge(
    badge_id: str, emoji: str, name: str, rarity: str, price: int,
    purchasable: int = 1, tradeable: int = 1, sellable: int = 1,
    source: str = "shop", created_by: str = ""
) -> bool:
    """Insert a new badge into the emoji_badges catalog. Returns False if already exists."""
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO emoji_badges
               (badge_id, emoji, name, rarity, price, purchasable, tradeable,
                sellable, source, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)""",
            (badge_id.lower().strip(), emoji, name, rarity.lower(), max(0, price),
             purchasable, tradeable, sellable, source, created_by),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def update_emoji_badge_field(badge_id: str, field: str, value) -> bool:
    """Update a single field of an emoji_badge row."""
    _allowed = {"price", "purchasable", "tradeable", "sellable", "rarity", "name", "emoji", "enabled"}
    if field not in _allowed:
        return False
    try:
        conn = get_connection()
        conn.execute(
            f"UPDATE emoji_badges SET {field} = ? WHERE badge_id = ?",
            (value, badge_id.lower().strip()),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def find_emoji_badge_by_name(name: str) -> dict | None:
    """Find an emoji badge by case-insensitive name match. Returns first match or None."""
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT * FROM emoji_badges WHERE lower(name) = lower(?) LIMIT 1",
            (name.strip(),)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def get_emoji_badge_by_rowid(rowid: int) -> dict | None:
    """Look up a badge by SQLite rowid (used for B### display IDs)."""
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT rowid, * FROM emoji_badges WHERE rowid = ?", (rowid,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def search_emoji_badges(
    query: str,
    purchasable_only: bool = True,
    limit: int = 5,
) -> list[dict]:
    """Full-text search across badge name and badge_id. Returns up to limit rows."""
    try:
        conn   = get_connection()
        where  = ["(lower(name) LIKE lower(?) OR lower(badge_id) LIKE lower(?))"]
        params: list = [f"%{query}%", f"%{query}%"]
        if purchasable_only:
            where.append("purchasable = 1")
            where.append("COALESCE(enabled, 1) = 1")
        clause = "WHERE " + " AND ".join(where)
        rows   = conn.execute(
            f"SELECT rowid, * FROM emoji_badges {clause} ORDER BY price ASC LIMIT ?",
            params + [limit],
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_emoji_badges_by_ids(
    badge_ids: list,
    page: int = 1,
    per_page: int = 5,
    purchasable_only: bool = True,
) -> tuple[list[dict], int]:
    """Return paginated badges filtered to a specific list of badge_ids."""
    if not badge_ids:
        return [], 1
    try:
        conn         = get_connection()
        placeholders = ",".join("?" * len(badge_ids))
        where        = [f"badge_id IN ({placeholders})"]
        params: list = list(badge_ids)
        if purchasable_only:
            where.append("purchasable = 1")
            where.append("COALESCE(enabled, 1) = 1")
        clause = "WHERE " + " AND ".join(where)
        total  = conn.execute(
            f"SELECT COUNT(*) AS n FROM emoji_badges {clause}", params
        ).fetchone()["n"]
        total_pages = max(1, -(-total // per_page))
        offset = (max(1, page) - 1) * per_page
        rows   = conn.execute(
            f"SELECT rowid, * FROM emoji_badges {clause} "
            "ORDER BY price ASC LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows], total_pages
    except Exception:
        return [], 1


def get_affordable_badges(
    balance: int,
    page: int = 1,
    per_page: int = 5,
) -> tuple[list[dict], int]:
    """Return paginated badges the player can afford (price <= balance, purchasable=1)."""
    try:
        conn   = get_connection()
        clause = "WHERE purchasable = 1 AND COALESCE(enabled,1) = 1 AND price <= ?"
        params: list = [balance]
        total  = conn.execute(
            f"SELECT COUNT(*) AS n FROM emoji_badges {clause}", params
        ).fetchone()["n"]
        total_pages = max(1, -(-total // per_page))
        offset = (max(1, page) - 1) * per_page
        rows   = conn.execute(
            f"SELECT rowid, * FROM emoji_badges {clause} "
            "ORDER BY price ASC LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows], total_pages
    except Exception:
        return [], 1


def get_sold_badges(
    page: int = 1,
    per_page: int = 5,
) -> tuple[list[dict], int]:
    """Return paginated shop badges that have been sold (purchasable=0, source=shop)."""
    try:
        conn   = get_connection()
        clause = "WHERE purchasable = 0 AND source = 'shop'"
        total  = conn.execute(
            f"SELECT COUNT(*) AS n FROM emoji_badges {clause}"
        ).fetchone()["n"]
        total_pages = max(1, -(-total // per_page))
        offset = (max(1, page) - 1) * per_page
        rows   = conn.execute(
            f"SELECT rowid, * FROM emoji_badges {clause} "
            "ORDER BY price DESC LIMIT ? OFFSET ?",
            [per_page, offset],
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows], total_pages
    except Exception:
        return [], 1


def search_badge_listings(
    query: str,
    page: int = 1,
    per_page: int = 3,
) -> tuple[list[dict], int]:
    """Search active badge market listings by badge_id or badge name."""
    try:
        conn   = get_connection()
        params = [f"%{query}%", f"%{query}%"]
        clause = (
            "WHERE bml.status = 'active' AND "
            "(lower(bml.badge_id) LIKE lower(?) OR lower(COALESCE(eb.name,'')) LIKE lower(?))"
        )
        total  = conn.execute(
            "SELECT COUNT(*) AS n FROM badge_market_listings bml "
            f"LEFT JOIN emoji_badges eb ON eb.badge_id = bml.badge_id {clause}",
            params,
        ).fetchone()["n"]
        total_pages = max(1, -(-total // per_page))
        offset = (max(1, page) - 1) * per_page
        rows   = conn.execute(
            "SELECT bml.*, COALESCE(eb.name, bml.badge_id) AS badge_name "
            "FROM badge_market_listings bml "
            f"LEFT JOIN emoji_badges eb ON eb.badge_id = bml.badge_id {clause} "
            "ORDER BY bml.price ASC LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows], total_pages
    except Exception:
        return [], 1


def get_badge_listings_filtered(
    rarity: str | None = None,
    sort: str = "default",
    page: int = 1,
    per_page: int = 3,
    max_price: int | None = None,
) -> tuple[list[dict], int]:
    """Get active badge market listings, optionally filtered by rarity, max_price, sorted."""
    try:
        conn   = get_connection()
        where  = ["bml.status = 'active'"]
        params: list = []
        if rarity:
            where.append("eb.rarity = ?")
            params.append(rarity)
        if max_price is not None:
            where.append("bml.price <= ?")
            params.append(max_price)
        clause = "WHERE " + " AND ".join(where)
        total  = conn.execute(
            "SELECT COUNT(*) AS n FROM badge_market_listings bml "
            f"LEFT JOIN emoji_badges eb ON eb.badge_id = bml.badge_id {clause}",
            params,
        ).fetchone()["n"]
        total_pages = max(1, -(-total // per_page))
        offset = (max(1, page) - 1) * per_page
        if sort == "cheap":
            order = "bml.price ASC"
        elif sort == "expensive":
            order = "bml.price DESC"
        else:
            order = "bml.id DESC"
        rows   = conn.execute(
            "SELECT bml.*, COALESCE(eb.name, bml.badge_id) AS badge_name, "
            "COALESCE(eb.rarity,'') AS badge_rarity "
            "FROM badge_market_listings bml "
            f"LEFT JOIN emoji_badges eb ON eb.badge_id = bml.badge_id {clause} "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows], total_pages
    except Exception:
        return [], 1


def set_emoji_badge_enabled(badge_id: str, enabled: int) -> bool:
    """Enable (1) or disable (0) a badge in the shop. Does not affect ownership."""
    try:
        conn = get_connection()
        conn.execute(
            "UPDATE emoji_badges SET enabled = ? WHERE badge_id = ?",
            (1 if enabled else 0, badge_id.lower().strip()),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Badge wishlist helpers  (badge_wishlist table)
# ---------------------------------------------------------------------------

def add_badge_wishlist(user_id: str, username: str, badge_id: str) -> str:
    """Add badge to wishlist. Returns 'ok', 'duplicate', or 'error'."""
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO badge_wishlist (user_id, username, badge_id) VALUES (?, ?, ?)",
            (user_id, username.lower(), badge_id.lower()),
        )
        conn.commit()
        conn.close()
        return "ok"
    except Exception as e:
        if "UNIQUE" in str(e).upper():
            return "duplicate"
        return "error"


def get_badge_wishlist(username: str) -> list[dict]:
    """Return all wishlist entries for a user."""
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM badge_wishlist WHERE lower(username)=lower(?) ORDER BY created_at ASC",
            (username,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def remove_badge_wishlist(user_id: str, badge_id: str) -> bool:
    """Remove a badge from the wishlist. Returns True if a row was deleted."""
    try:
        conn = get_connection()
        cur  = conn.execute(
            "DELETE FROM badge_wishlist WHERE user_id=? AND badge_id=?",
            (user_id, badge_id.lower()),
        )
        conn.commit()
        conn.close()
        return cur.rowcount > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Badge starter claim helpers  (badge_claims table)
# ---------------------------------------------------------------------------

def has_claimed_badge(user_id: str) -> bool:
    """Return True if the user has already claimed a starter badge."""
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT 1 FROM badge_claims WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def claim_starter_badge(user_id: str, username: str, badge_id: str) -> bool:
    """Record a starter badge claim. Returns True if inserted."""
    try:
        conn = get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO badge_claims (user_id, username, badge_id) VALUES (?, ?, ?)",
            (user_id, username.lower(), badge_id.lower()),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Badge lock / unlock helpers  (user_badges.locked column)
# ---------------------------------------------------------------------------

def lock_emoji_badge(username: str, badge_id: str) -> bool:
    """Set locked=1 on a user's badge. Returns True on success."""
    try:
        conn = get_connection()
        cur  = conn.execute(
            "UPDATE user_badges SET locked=1 WHERE lower(username)=lower(?) AND badge_id=?",
            (username, badge_id.lower()),
        )
        conn.commit()
        conn.close()
        return cur.rowcount > 0
    except Exception:
        return False


def unlock_emoji_badge(username: str, badge_id: str) -> bool:
    """Set locked=0 on a user's badge. Returns True on success."""
    try:
        conn = get_connection()
        cur  = conn.execute(
            "UPDATE user_badges SET locked=0 WHERE lower(username)=lower(?) AND badge_id=?",
            (username, badge_id.lower()),
        )
        conn.commit()
        conn.close()
        return cur.rowcount > 0
    except Exception:
        return False


def get_locked_badges(username: str) -> list[dict]:
    """Return all locked badges for a user, joined with emoji_badges for display info."""
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT ub.badge_id, eb.emoji, eb.name, eb.rarity "
            "FROM user_badges ub "
            "LEFT JOIN emoji_badges eb ON eb.badge_id = ub.badge_id "
            "WHERE lower(ub.username)=lower(?) AND ub.locked=1 "
            "ORDER BY ub.badge_id ASC",
            (username,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Badge ownership transfer  (gift / trade)
# ---------------------------------------------------------------------------

def transfer_badge_ownership(from_username: str, to_username: str, badge_id: str) -> str | None:
    """
    Move a badge from one user to another atomically.
    Returns None on success, or an error string on failure.
    """
    bid = badge_id.lower()
    try:
        conn = get_connection()
        # Verify source still owns it and it is not locked
        src = conn.execute(
            "SELECT locked FROM user_badges WHERE lower(username)=lower(?) AND badge_id=?",
            (from_username, bid),
        ).fetchone()
        if src is None:
            conn.close()
            return "Sender does not own this badge"
        if src["locked"]:
            conn.close()
            return "Badge is locked"
        # Verify destination does not already own it
        dst = conn.execute(
            "SELECT 1 FROM user_badges WHERE lower(username)=lower(?) AND badge_id=?",
            (to_username, bid),
        ).fetchone()
        if dst is not None:
            conn.close()
            return "Receiver already owns this badge"
        # Transfer
        conn.execute(
            "DELETE FROM user_badges WHERE lower(username)=lower(?) AND badge_id=?",
            (from_username, bid),
        )
        conn.execute(
            "INSERT OR IGNORE INTO user_badges (username, badge_id, acquired_at, source, equipped, locked) "
            "VALUES (?, ?, datetime('now'), 'gift', 0, 0)",
            (to_username.lower(), bid),
        )
        conn.commit()
        conn.close()
        return None
    except Exception as exc:
        return str(exc)


# ---------------------------------------------------------------------------
# User emoji badge ownership  (user_badges table)
# ---------------------------------------------------------------------------

def owns_emoji_badge(username: str, badge_id: str) -> bool:
    conn = get_connection()
    row  = conn.execute(
        "SELECT 1 FROM user_badges WHERE lower(username)=lower(?) AND badge_id=?",
        (username, badge_id.lower())
    ).fetchone()
    conn.close()
    return row is not None


def get_user_emoji_badges(username: str) -> list[dict]:
    """Return all emoji badges owned by username."""
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT ub.*, eb.emoji, eb.name, eb.rarity FROM user_badges ub "
            "LEFT JOIN emoji_badges eb ON eb.badge_id = ub.badge_id "
            "WHERE lower(ub.username) = lower(?)",
            (username,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def grant_emoji_badge(
    username: str, badge_id: str, source: str = "admin", locked: int = 0
) -> bool:
    """Add badge to user_badges (idempotent). Returns True if inserted."""
    try:
        conn = get_connection()
        cursor = conn.execute(
            """INSERT OR IGNORE INTO user_badges
               (username, badge_id, acquired_at, source, equipped, locked)
               VALUES (lower(?), ?, datetime('now'), ?, 0, ?)""",
            (username, badge_id.lower(), source, locked),
        )
        inserted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return inserted
    except Exception:
        return False


def revoke_emoji_badge(username: str, badge_id: str) -> bool:
    """Remove badge from user_badges. Clears equipped slot if badge was equipped."""
    try:
        conn = get_connection()
        conn.execute(
            "DELETE FROM user_badges WHERE lower(username)=lower(?) AND badge_id=?",
            (username, badge_id.lower()),
        )
        # Also clear from users table if this badge was equipped
        conn.execute(
            """UPDATE users SET equipped_badge='', equipped_badge_id=''
               WHERE lower(username)=lower(?) AND equipped_badge_id=?""",
            (username, badge_id.lower()),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def is_badge_listed(username: str, badge_id: str) -> bool:
    """Return True if this badge currently has an active market listing by username."""
    try:
        conn = get_connection()
        row  = conn.execute(
            """SELECT 1 FROM badge_market_listings
               WHERE lower(seller_username)=lower(?) AND badge_id=? AND status='active'""",
            (username, badge_id.lower()),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Badge marketplace
# ---------------------------------------------------------------------------

def get_active_badge_listings(page: int = 1, per_page: int = 8) -> tuple[list[dict], int]:
    """Return (listings_page, total_pages)."""
    try:
        conn        = get_connection()
        total       = conn.execute(
            "SELECT COUNT(*) AS n FROM badge_market_listings WHERE status='active'"
        ).fetchone()["n"]
        total_pages = max(1, -(-total // per_page))
        offset      = (max(1, page) - 1) * per_page
        rows        = conn.execute(
            """SELECT * FROM badge_market_listings WHERE status='active'
               ORDER BY listed_at DESC LIMIT ? OFFSET ?""",
            (per_page, offset),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows], total_pages
    except Exception:
        return [], 1


def get_badge_listing(listing_id: int) -> dict | None:
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT * FROM badge_market_listings WHERE id = ?", (listing_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def get_user_badge_listings(username: str) -> list[dict]:
    try:
        conn = get_connection()
        rows = conn.execute(
            """SELECT * FROM badge_market_listings
               WHERE lower(seller_username)=lower(?) AND status='active'
               ORDER BY listed_at DESC""",
            (username,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def create_badge_listing(
    seller_username: str, badge_id: str, emoji: str, price: int
) -> int:
    """Create an active badge market listing. Returns the new listing id (or -1 on error)."""
    try:
        conn = get_connection()
        cursor = conn.execute(
            """INSERT INTO badge_market_listings
               (seller_username, badge_id, emoji, price, listed_at, status)
               VALUES (lower(?), ?, ?, ?, datetime('now'), 'active')""",
            (seller_username, badge_id.lower(), emoji, price),
        )
        listing_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return listing_id
    except Exception:
        return -1


def buy_badge_listing(listing_id: int, buyer_username: str, fee_pct: float) -> str:
    """
    Atomic marketplace purchase with BEGIN IMMEDIATE to prevent race conditions.
    Returns '' on success, or an error string on failure.
    Caller should notify seller separately.
    """
    conn = get_connection()
    try:
        # BEGIN IMMEDIATE acquires a write lock before any reads — prevents two
        # simultaneous buyers from both seeing 'active' and both succeeding.
        conn.execute("BEGIN IMMEDIATE")

        listing = conn.execute(
            "SELECT * FROM badge_market_listings WHERE id = ? AND status = 'active'",
            (listing_id,),
        ).fetchone()
        if not listing:
            conn.rollback()
            conn.close()
            return "Listing not found or already sold."
        if listing["seller_username"].lower() == buyer_username.lower():
            conn.rollback()
            conn.close()
            return "You cannot buy your own listing."

        # Verify badge still belongs to seller and is not bound/staff-created
        badge_def = conn.execute(
            "SELECT tradeable, sellable FROM emoji_badges WHERE badge_id=?",
            (listing["badge_id"],)
        ).fetchone()
        if badge_def and (not badge_def["tradeable"] or not badge_def["sellable"]):
            conn.rollback()
            conn.close()
            return "This badge is bound and cannot be sold."

        owns = conn.execute(
            "SELECT 1 FROM user_badges WHERE lower(username)=lower(?) AND badge_id=? AND locked=0",
            (listing["seller_username"], listing["badge_id"])
        ).fetchone()
        if not owns:
            # Seller no longer owns the badge — auto-cancel the listing
            conn.execute(
                "UPDATE badge_market_listings SET status='cancelled' WHERE id=?",
                (listing_id,)
            )
            conn.commit()
            conn.close()
            return "Listing cancelled: seller no longer owns this badge."

        price = listing["price"]
        fee   = max(0, int(price * fee_pct / 100))
        net   = price - fee

        # Check buyer balance
        buyer_row = conn.execute(
            "SELECT balance FROM users WHERE lower(username)=lower(?)",
            (buyer_username,)
        ).fetchone()
        if not buyer_row or buyer_row["balance"] < price:
            conn.rollback()
            conn.close()
            return f"Not enough coins. Need {price:,}c."

        seller_row = conn.execute(
            "SELECT user_id FROM users WHERE lower(username)=lower(?)",
            (listing["seller_username"],)
        ).fetchone()

        # Deduct buyer, credit seller
        conn.execute(
            "UPDATE users SET balance = balance - ? WHERE lower(username)=lower(?)",
            (price, buyer_username)
        )
        if seller_row:
            conn.execute(
                "UPDATE users SET balance = balance + ? WHERE lower(username)=lower(?)",
                (net, listing["seller_username"])
            )

        # Transfer badge ownership: remove from seller, give to buyer
        conn.execute(
            "DELETE FROM user_badges WHERE lower(username)=lower(?) AND badge_id=?",
            (listing["seller_username"], listing["badge_id"])
        )
        conn.execute(
            """INSERT OR IGNORE INTO user_badges
               (username, badge_id, acquired_at, source, equipped, locked)
               VALUES (lower(?), ?, datetime('now'), 'player_market', 0, 0)""",
            (buyer_username, listing["badge_id"])
        )

        # Mark listing sold
        conn.execute(
            """UPDATE badge_market_listings
               SET status='sold', buyer_username=lower(?), sold_at=datetime('now')
               WHERE id=?""",
            (buyer_username, listing_id)
        )

        # Log inside same transaction
        _log_badge_market_inner(
            conn, "sold", listing["seller_username"], buyer_username,
            listing["badge_id"], listing["emoji"], price, fee, "sold"
        )
        conn.commit()
        conn.close()
        return ""
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()
        return f"Transaction failed: {exc}"


def cancel_badge_listing(listing_id: int, requester: str, is_staff: bool = False) -> str:
    """Cancel a listing. Returns '' on success, error string on failure."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        listing = conn.execute(
            "SELECT * FROM badge_market_listings WHERE id=? AND status='active'",
            (listing_id,)
        ).fetchone()
        if not listing:
            conn.rollback()
            conn.close()
            return "Listing not found or not active."
        if not is_staff and listing["seller_username"].lower() != requester.lower():
            conn.rollback()
            conn.close()
            return "🔒 You can only cancel your own listing."

        conn.execute(
            "UPDATE badge_market_listings SET status='cancelled' WHERE id=?", (listing_id,)
        )
        # Badge stays in user_badges (was never removed at listing time)
        conn.commit()
        conn.close()
        return ""
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()
        return f"Error: {exc}"


def get_market_audit_stats() -> dict:
    """Return audit stats for !marketaudit."""
    try:
        conn    = get_connection()
        active  = conn.execute(
            "SELECT COUNT(*) AS n FROM badge_market_listings WHERE status='active'"
        ).fetchone()["n"]
        orphans = conn.execute(
            """SELECT COUNT(*) AS n FROM badge_market_listings bml
               WHERE bml.status='active'
               AND NOT EXISTS (
                 SELECT 1 FROM user_badges ub
                 WHERE lower(ub.username)=lower(bml.seller_username)
                 AND ub.badge_id=bml.badge_id
               )"""
        ).fetchone()["n"]
        trades  = conn.execute(
            "SELECT COUNT(*) AS n FROM badge_trades WHERE status='active'"
        ).fetchone()["n"] if _table_exists(conn, "badge_trades") else 0
        conn.close()
        return {"active": active, "orphans": orphans, "trades": trades}
    except Exception:
        return {"active": 0, "orphans": 0, "trades": 0}


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def get_badge_listing_detail(listing_id: int) -> dict | None:
    """Full listing info for !marketdebug."""
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT * FROM badge_market_listings WHERE id=?", (listing_id,)
        ).fetchone()
        if not row:
            conn.close()
            return None
        listing = dict(row)
        # Check if seller still owns the badge
        owns = conn.execute(
            "SELECT 1 FROM user_badges WHERE lower(username)=lower(?) AND badge_id=?",
            (listing["seller_username"], listing["badge_id"])
        ).fetchone()
        listing["seller_still_owns"] = owns is not None
        conn.close()
        return listing
    except Exception:
        return None


def force_cancel_badge_listing(listing_id: int, actor: str) -> str:
    """Owner/admin force-cancel any active listing."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        listing = conn.execute(
            "SELECT * FROM badge_market_listings WHERE id=? AND status='active'",
            (listing_id,)
        ).fetchone()
        if not listing:
            conn.rollback()
            conn.close()
            return "Listing not found or not active."
        conn.execute(
            "UPDATE badge_market_listings SET status='cancelled' WHERE id=?", (listing_id,)
        )
        _log_badge_market_inner(
            conn, "force_cancelled", listing["seller_username"], actor,
            listing["badge_id"], listing["emoji"], listing["price"], 0, "cancelled"
        )
        conn.commit()
        conn.close()
        return ""
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()
        return f"Error: {exc}"


def clear_stale_badge_locks(actor: str) -> int:
    """Cancel orphaned listings where seller no longer owns the badge. Returns count fixed."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, seller_username, badge_id, emoji, price
               FROM badge_market_listings WHERE status='active'"""
        ).fetchall()
        fixed = 0
        for r in rows:
            owns = conn.execute(
                "SELECT 1 FROM user_badges WHERE lower(username)=lower(?) AND badge_id=?",
                (r["seller_username"], r["badge_id"])
            ).fetchone()
            if not owns:
                conn.execute(
                    "UPDATE badge_market_listings SET status='cancelled' WHERE id=?", (r["id"],)
                )
                _log_badge_market_inner(
                    conn, "stale_lock_cleared", r["seller_username"], actor,
                    r["badge_id"], r["emoji"], r["price"], 0, "cancelled"
                )
                fixed += 1
        conn.commit()
        conn.close()
        return fixed
    except Exception:
        conn.close()
        return 0


def get_badge_recent_prices(badge_id: str, limit: int = 5) -> list[int]:
    """Return last N sold prices for a badge."""
    try:
        conn = get_connection()
        rows = conn.execute(
            """SELECT price FROM badge_market_listings
               WHERE badge_id=? AND status='sold'
               ORDER BY sold_at DESC LIMIT ?""",
            (badge_id.lower(), limit),
        ).fetchall()
        conn.close()
        return [r["price"] for r in rows]
    except Exception:
        return []


def _log_badge_market_inner(
    conn, action: str, seller: str, buyer: str,
    badge_id: str, emoji: str, price: int, fee: int, status: str
) -> None:
    try:
        conn.execute(
            """INSERT INTO badge_market_logs
               (timestamp, action, seller_username, buyer_username,
                badge_id, emoji, price, fee, status)
               VALUES (datetime('now'),?,?,?,?,?,?,?,?)""",
            (action, seller, buyer, badge_id, emoji, price, fee, status),
        )
    except Exception:
        pass


def log_badge_market_action(
    action: str, seller: str, buyer: str,
    badge_id: str, emoji: str, price: int, fee: int, status: str
) -> None:
    conn = get_connection()
    _log_badge_market_inner(conn, action, seller, buyer, badge_id, emoji, price, fee, status)
    conn.commit()
    conn.close()


def get_badge_market_logs(username: str | None = None, limit: int = 8) -> list[dict]:
    try:
        conn = get_connection()
        if username:
            rows = conn.execute(
                """SELECT * FROM badge_market_logs
                   WHERE lower(seller_username)=lower(?) OR lower(buyer_username)=lower(?)
                   ORDER BY id DESC LIMIT ?""",
                (username, username, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM badge_market_logs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Badge P2P Trade system (3.1F)
# ---------------------------------------------------------------------------

def get_active_trade_for_user(user_id: str) -> dict | None:
    """Return the active trade involving user_id, or None."""
    try:
        conn = get_connection()
        row  = conn.execute(
            """SELECT * FROM badge_trades
               WHERE (user_a_id=? OR user_b_id=?) AND status='active'
               ORDER BY id DESC LIMIT 1""",
            (user_id, user_id)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def create_badge_trade(
    user_a_id: str, user_a_name: str,
    user_b_id: str, user_b_name: str
) -> int:
    """Create a new active trade. Returns new trade_id, or -1 on error."""
    try:
        conn   = get_connection()
        cursor = conn.execute(
            """INSERT INTO badge_trades
               (user_a_id,user_a_name,user_b_id,user_b_name,status,
                user_a_confirmed,user_b_confirmed,
                created_at,updated_at,expires_at)
               VALUES (?,?,?,?,'active',0,0,
                datetime('now'),datetime('now'),datetime('now','+5 minutes'))""",
            (user_a_id, user_a_name.lower(), user_b_id, user_b_name.lower())
        )
        tid = cursor.lastrowid
        conn.commit()
        conn.close()
        return tid
    except Exception:
        return -1


def set_trade_badge(trade_id: int, user_id: str, badge_id: str, emoji: str) -> bool:
    """Set (or replace) the badge a user is offering in a trade. Resets both confirms."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT OR REPLACE INTO badge_trade_items
               (trade_id,user_id,badge_id,emoji) VALUES (?,?,?,?)""",
            (trade_id, user_id, badge_id.lower(), emoji)
        )
        conn.execute(
            """UPDATE badge_trades
               SET user_a_confirmed=0, user_b_confirmed=0, updated_at=datetime('now'),
                   expires_at=datetime('now','+5 minutes')
               WHERE id=?""",
            (trade_id,)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()
        return False


def set_trade_coins(trade_id: int, user_id: str, amount: int) -> bool:
    """Set (or replace) coin offer for a user in a trade. Resets both confirms."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT OR REPLACE INTO badge_trade_coins
               (trade_id,user_id,amount) VALUES (?,?,?)""",
            (trade_id, user_id, max(0, amount))
        )
        conn.execute(
            """UPDATE badge_trades
               SET user_a_confirmed=0, user_b_confirmed=0, updated_at=datetime('now'),
                   expires_at=datetime('now','+5 minutes')
               WHERE id=?""",
            (trade_id,)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()
        return False


def confirm_trade_user(trade_id: int, user_id: str, is_a: bool) -> bool:
    """Mark one side as confirmed. Returns True on success."""
    try:
        col  = "user_a_confirmed" if is_a else "user_b_confirmed"
        conn = get_connection()
        conn.execute(
            f"UPDATE badge_trades SET {col}=1, updated_at=datetime('now') WHERE id=?",
            (trade_id,)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def get_trade_items(trade_id: int) -> list[dict]:
    try:
        conn  = get_connection()
        rows  = conn.execute(
            "SELECT * FROM badge_trade_items WHERE trade_id=?", (trade_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_trade_coins(trade_id: int) -> list[dict]:
    try:
        conn  = get_connection()
        rows  = conn.execute(
            "SELECT * FROM badge_trade_coins WHERE trade_id=?", (trade_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def execute_badge_trade(trade_id: int) -> str:
    """
    Atomically complete a confirmed trade.
    Validates ownership, balance, not-bound. Returns '' on success or error string.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        trade = conn.execute(
            "SELECT * FROM badge_trades WHERE id=? AND status='active'",
            (trade_id,)
        ).fetchone()
        if not trade:
            conn.rollback()
            conn.close()
            return "Trade not found or already complete."
        if not trade["user_a_confirmed"] or not trade["user_b_confirmed"]:
            conn.rollback()
            conn.close()
            return "Both players must confirm first."

        items = conn.execute(
            "SELECT * FROM badge_trade_items WHERE trade_id=?", (trade_id,)
        ).fetchall()
        coins = conn.execute(
            "SELECT * FROM badge_trade_coins WHERE trade_id=?", (trade_id,)
        ).fetchall()

        # Build lookup {user_id: badge_row, ...} and {user_id: coin_amount}
        item_map = {r["user_id"]: r for r in items}
        coin_map = {r["user_id"]: r["amount"] for r in coins}

        a_id, b_id = trade["user_a_id"], trade["user_b_id"]
        a_name, b_name = trade["user_a_name"], trade["user_b_name"]

        # Validate badges (each side)
        for uid, uname in ((a_id, a_name), (b_id, b_name)):
            item = item_map.get(uid)
            if item:
                badge_def = conn.execute(
                    "SELECT tradeable,sellable FROM emoji_badges WHERE badge_id=?",
                    (item["badge_id"],)
                ).fetchone()
                if badge_def and (not badge_def["tradeable"] or not badge_def["sellable"]):
                    conn.rollback()
                    conn.close()
                    return f"Badge {item['badge_id']} is bound and cannot be traded."
                owns = conn.execute(
                    "SELECT 1 FROM user_badges WHERE lower(username)=lower(?) AND badge_id=? AND locked=0",
                    (uname, item["badge_id"])
                ).fetchone()
                if not owns:
                    conn.rollback()
                    conn.close()
                    return f"@{uname} no longer owns that badge."
                listed = conn.execute(
                    "SELECT 1 FROM badge_market_listings WHERE lower(seller_username)=lower(?) AND badge_id=? AND status='active'",
                    (uname, item["badge_id"])
                ).fetchone()
                if listed:
                    conn.rollback()
                    conn.close()
                    return f"Badge is currently listed on market. Cancel listing first."

        # Validate coins
        for uid, uname in ((a_id, a_name), (b_id, b_name)):
            amount = coin_map.get(uid, 0)
            if amount > 0:
                bal = conn.execute(
                    "SELECT balance FROM users WHERE lower(username)=lower(?)", (uname,)
                ).fetchone()
                if not bal or bal["balance"] < amount:
                    conn.rollback()
                    conn.close()
                    return f"@{uname} doesn't have enough coins."

        # Execute transfers — badges
        for uid, uname in ((a_id, a_name), (b_id, b_name)):
            other_name = b_name if uid == a_id else a_name
            item = item_map.get(uid)
            if item:
                conn.execute(
                    "DELETE FROM user_badges WHERE lower(username)=lower(?) AND badge_id=?",
                    (uname, item["badge_id"])
                )
                conn.execute(
                    """INSERT OR IGNORE INTO user_badges
                       (username,badge_id,acquired_at,source,equipped,locked)
                       VALUES (lower(?),?,datetime('now'),'p2p_trade',0,0)""",
                    (other_name, item["badge_id"])
                )

        # Execute transfers — coins
        for uid, uname in ((a_id, a_name), (b_id, b_name)):
            other_name = b_name if uid == a_id else a_name
            amount = coin_map.get(uid, 0)
            if amount > 0:
                conn.execute(
                    "UPDATE users SET balance=balance-? WHERE lower(username)=lower(?)",
                    (amount, uname)
                )
                conn.execute(
                    "UPDATE users SET balance=balance+? WHERE lower(username)=lower(?)",
                    (amount, other_name)
                )

        # Mark trade complete
        conn.execute(
            "UPDATE badge_trades SET status='completed',updated_at=datetime('now') WHERE id=?",
            (trade_id,)
        )
        conn.commit()
        conn.close()
        return ""
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()
        return f"Trade failed: {exc}"


def cancel_badge_trade(trade_id: int) -> bool:
    """Cancel an active trade (no transfers). Returns True on success."""
    try:
        conn = get_connection()
        conn.execute(
            "UPDATE badge_trades SET status='cancelled',updated_at=datetime('now') WHERE id=? AND status='active'",
            (trade_id,)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def expire_stale_trades() -> int:
    """Cancel trades older than 5 minutes that have not been confirmed. Returns count."""
    try:
        conn  = get_connection()
        cur   = conn.execute(
            "UPDATE badge_trades SET status='expired',updated_at=datetime('now') "
            "WHERE status='active' AND expires_at < datetime('now')"
        )
        fixed = cur.rowcount
        conn.commit()
        conn.close()
        return fixed
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Seed default emoji badge catalog
# ---------------------------------------------------------------------------

_BADGE_SEED: list[tuple] = [
    # badge_id, emoji, name, rarity, price, purchasable, tradeable, sellable, source
    # Common 1500c
    ("star","⭐","Star","common",1500,1,1,1,"shop"),
    ("glow","🌟","Glow","common",1500,1,1,1,"shop"),
    ("sparkle","✨","Sparkle","common",1500,1,1,1,"shop"),
    ("stardust","💫","Stardust","common",1500,1,1,1,"shop"),
    ("redheart","❤️","Red Heart","common",1500,1,1,1,"shop"),
    ("blueheart","💙","Blue Heart","common",1500,1,1,1,"shop"),
    ("greenheart","💚","Green Heart","common",1500,1,1,1,"shop"),
    ("yellowheart","💛","Yellow Heart","common",1500,1,1,1,"shop"),
    ("orangeheart","🧡","Orange Heart","common",1500,1,1,1,"shop"),
    ("purpleheart","💜","Purple Heart","common",1500,1,1,1,"shop"),
    ("blackheart","🖤","Black Heart","common",1500,1,1,1,"shop"),
    ("whiteheart","🤍","White Heart","common",1500,1,1,1,"shop"),
    # Uncommon 7500c
    ("fire","🔥","Fire","uncommon",7500,1,1,1,"shop"),
    ("ice","❄️","Ice","uncommon",7500,1,1,1,"shop"),
    ("lightning","⚡","Lightning","uncommon",7500,1,1,1,"shop"),
    ("moon","🌙","Moon","uncommon",7500,1,1,1,"shop"),
    ("sun","☀️","Sun","uncommon",7500,1,1,1,"shop"),
    ("rainbow","🌈","Rainbow","uncommon",7500,1,1,1,"shop"),
    ("clover","🍀","Clover","uncommon",7500,1,1,1,"shop"),
    ("music","🎵","Music","uncommon",7500,1,1,1,"shop"),
    ("gamepad","🎮","Gamepad","uncommon",7500,1,1,1,"shop"),
    ("diceroll","🎲","Dice","uncommon",7500,1,1,1,"shop"),
    ("target","🎯","Target","uncommon",7500,1,1,1,"shop"),
    # Rare 25000c  (3.3× uncommon — keeps the rarity ladder consistent)
    ("diamond","💎","Diamond","rare",25000,1,1,1,"shop"),
    ("crown","👑","Crown","rare",25000,1,1,1,"shop"),
    ("butterfly","🦋","Butterfly","rare",25000,1,1,1,"shop"),
    ("wyrm","🐉","Wyrm","rare",25000,1,1,1,"shop"),
    ("eagle","🦅","Eagle","rare",25000,1,1,1,"shop"),
    ("wolf","🐺","Wolf","rare",25000,1,1,1,"shop"),
    ("fox","🦊","Fox","rare",25000,1,1,1,"shop"),
    ("panda","🐼","Panda","rare",25000,1,1,1,"shop"),
    ("lion","🦁","Lion","rare",25000,1,1,1,"shop"),
    ("tiger","🐯","Tiger","rare",25000,1,1,1,"shop"),
    # Epic 50000c
    ("galaxy","🌌","Galaxy","epic",50000,1,1,1,"shop"),
    ("shootingstar","🌠","Shooting Star","epic",50000,1,1,1,"shop"),
    ("planet","🪐","Planet","epic",50000,1,1,1,"shop"),
    ("rocket","🚀","Rocket","epic",50000,1,1,1,"shop"),
    ("shield","🛡️","Shield","epic",50000,1,1,1,"shop"),
    ("sword","⚔️","Sword","epic",50000,1,1,1,"shop"),
    ("trophy","🏆","Trophy","epic",50000,1,1,1,"shop"),
    ("medal","🎖️","Medal","epic",50000,1,1,1,"shop"),
    ("amulet","🧿","Amulet","epic",50000,1,1,1,"shop"),
    ("dna","🧬","DNA","epic",50000,1,1,1,"shop"),
    # Legendary 150000c
    ("demon","👹","Demon","legendary",150000,1,1,1,"shop"),
    ("goblin","👺","Goblin","legendary",150000,1,1,1,"shop"),
    ("unicorn","🦄","Unicorn","legendary",150000,1,1,1,"shop"),
    ("crystal","🧊","Crystal","legendary",150000,1,1,1,"shop"),
    ("goldcoin","🪙","Gold Coin","legendary",150000,1,1,1,"shop"),
    ("moneybag","💰","Money Bag","legendary",150000,1,1,1,"shop"),
    ("goldbadge","🏅","Gold Badge","legendary",150000,1,1,1,"shop"),
    ("mask","🎭","Mask","legendary",150000,1,1,1,"shop"),
    ("phantom","🗡️","Phantom","legendary",150000,1,1,1,"shop"),
    ("lance","⚜️","Lance","legendary",150000,1,1,1,"shop"),
    # Mythic 500000c
    ("wing","🪽","Wing","mythic",500000,1,1,1,"shop"),
    ("wizard","🧙","Wizard","mythic",500000,1,1,1,"shop"),
    ("vampire","🧛","Vampire","mythic",500000,1,1,1,"shop"),
    ("genie","🧞","Genie","mythic",500000,1,1,1,"shop"),
    ("mermaid","🧜","Mermaid","mythic",500000,1,1,1,"shop"),
    ("fairy","🧚","Fairy","mythic",500000,1,1,1,"shop"),
    ("zombie","🧟","Zombie","mythic",500000,1,1,1,"shop"),
    ("dove","🕊️","Dove","mythic",500000,1,1,1,"shop"),
    ("wand","🪄","Wand","mythic",500000,1,1,1,"shop"),
    # Exclusive — not purchasable
    ("phoenixbadge","🐦‍🔥","Phoenix","exclusive",0,0,0,0,"exclusive"),
    ("dragoncrest","🔱","Dragon Crest","exclusive",0,0,0,0,"exclusive"),
    ("pirate","🏴‍☠️","Pirate","exclusive",0,0,0,0,"exclusive"),
    # ── Faces (common 1500c) ─────────────────────────────────────────────────
    ("smile","😀","Smile","common",1500,1,1,1,"shop"),
    ("grin","😃","Grin","common",1500,1,1,1,"shop"),
    ("laugh","😄","Laugh","common",1500,1,1,1,"shop"),
    ("beaming","😁","Beaming","common",1500,1,1,1,"shop"),
    ("squintlaugh","😆","Squint Laugh","common",1500,1,1,1,"shop"),
    ("sweat","😅","Sweat Smile","common",1500,1,1,1,"shop"),
    ("rofl","😂","ROFL","common",1500,1,1,1,"shop"),
    ("slightsmile","🙂","Slight Smile","common",1500,1,1,1,"shop"),
    ("upsidedown","🙃","Upside Down","common",1500,1,1,1,"shop"),
    ("wink","😉","Wink","common",1500,1,1,1,"shop"),
    ("blush","😊","Blush","common",1500,1,1,1,"shop"),
    ("shades","😎","Shades","common",1500,1,1,1,"shop"),
    ("starstruck","🤩","Starstruck","common",1500,1,1,1,"shop"),
    ("partying","🥳","Partying","common",1500,1,1,1,"shop"),
    ("halo","😇","Halo","common",1500,1,1,1,"shop"),
    ("devil","😈","Devil","common",1500,1,1,1,"shop"),
    ("ghost","👻","Ghost","common",1500,1,1,1,"shop"),
    ("skull","💀","Skull","common",1500,1,1,1,"shop"),
    ("robot","🤖","Robot","common",1500,1,1,1,"shop"),
    # ── Extra Hearts (common 1500c) ─────────────────────────────────────────
    ("brownheart","🤎","Brown Heart","common",1500,1,1,1,"shop"),
    ("sparkleheart","💖","Sparkle Heart","common",1500,1,1,1,"shop"),
    ("growingheart","💗","Growing Heart","common",1500,1,1,1,"shop"),
    ("beatingheart","💓","Beating Heart","common",1500,1,1,1,"shop"),
    ("revolvingheart","💞","Revolving Heart","common",1500,1,1,1,"shop"),
    ("twohearts","💕","Two Hearts","common",1500,1,1,1,"shop"),
    ("arrowheart","💘","Arrow Heart","common",1500,1,1,1,"shop"),
    ("ribbonheart","💝","Ribbon Heart","common",1500,1,1,1,"shop"),
    # ── Sky & Nature extras (uncommon 7500c) ────────────────────────────────
    ("cloud","☁️","Cloud","uncommon",7500,1,1,1,"shop"),
    ("wave","🌊","Wave","uncommon",7500,1,1,1,"shop"),
    ("earth","🌍","Earth","uncommon",7500,1,1,1,"shop"),
    ("earth2","🌎","Earth Americas","uncommon",7500,1,1,1,"shop"),
    ("earth3","🌏","Earth Asia","uncommon",7500,1,1,1,"shop"),
    # ── Animals (uncommon 7500c) ────────────────────────────────────────────
    ("dog","🐶","Dog","uncommon",7500,1,1,1,"shop"),
    ("cat","🐱","Cat","uncommon",7500,1,1,1,"shop"),
    ("mouse","🐭","Mouse","uncommon",7500,1,1,1,"shop"),
    ("hamster","🐹","Hamster","uncommon",7500,1,1,1,"shop"),
    ("rabbit","🐰","Rabbit","uncommon",7500,1,1,1,"shop"),
    ("bear","🐻","Bear","uncommon",7500,1,1,1,"shop"),
    ("koala","🐨","Koala","uncommon",7500,1,1,1,"shop"),
    ("cow","🐮","Cow","uncommon",7500,1,1,1,"shop"),
    ("pig","🐷","Pig","uncommon",7500,1,1,1,"shop"),
    ("frog","🐸","Frog","uncommon",7500,1,1,1,"shop"),
    ("monkey","🐵","Monkey","uncommon",7500,1,1,1,"shop"),
    ("bee","🐝","Bee","uncommon",7500,1,1,1,"shop"),
    ("turtle","🐢","Turtle","uncommon",7500,1,1,1,"shop"),
    ("shark","🦈","Shark","uncommon",7500,1,1,1,"shop"),
    ("dolphin","🐬","Dolphin","uncommon",7500,1,1,1,"shop"),
    ("whale","🐳","Whale","uncommon",7500,1,1,1,"shop"),
    # ── Food (common 1500c) ─────────────────────────────────────────────────
    ("apple","🍎","Apple","common",1500,1,1,1,"shop"),
    ("orange","🍊","Orange","common",1500,1,1,1,"shop"),
    ("lemon","🍋","Lemon","common",1500,1,1,1,"shop"),
    ("banana","🍌","Banana","common",1500,1,1,1,"shop"),
    ("watermelon","🍉","Watermelon","common",1500,1,1,1,"shop"),
    ("grapes","🍇","Grapes","common",1500,1,1,1,"shop"),
    ("strawberry","🍓","Strawberry","common",1500,1,1,1,"shop"),
    ("cherry","🍒","Cherry","common",1500,1,1,1,"shop"),
    ("peach","🍑","Peach","common",1500,1,1,1,"shop"),
    ("pineapple","🍍","Pineapple","common",1500,1,1,1,"shop"),
    ("coconut","🥥","Coconut","common",1500,1,1,1,"shop"),
    ("avocado","🥑","Avocado","common",1500,1,1,1,"shop"),
    ("burger","🍔","Burger","common",1500,1,1,1,"shop"),
    ("pizza","🍕","Pizza","common",1500,1,1,1,"shop"),
    ("taco","🌮","Taco","common",1500,1,1,1,"shop"),
    ("sushi","🍣","Sushi","common",1500,1,1,1,"shop"),
    ("donut","🍩","Donut","common",1500,1,1,1,"shop"),
    ("cookie","🍪","Cookie","common",1500,1,1,1,"shop"),
    ("cake","🍰","Cake","common",1500,1,1,1,"shop"),
    ("lollipop","🍭","Lollipop","common",1500,1,1,1,"shop"),
    # ── Objects (common & uncommon) ─────────────────────────────────────────
    ("phone","📱","Phone","common",1500,1,1,1,"shop"),
    ("gift","🎁","Gift","common",1500,1,1,1,"shop"),
    ("key","🔑","Key","common",1500,1,1,1,"shop"),
    ("tophat","🎩","Top Hat","uncommon",7500,1,1,1,"shop"),
    ("headphones","🎧","Headphones","uncommon",7500,1,1,1,"shop"),
    ("joystick","🕹️","Joystick","uncommon",7500,1,1,1,"shop"),
    ("laptop","💻","Laptop","uncommon",7500,1,1,1,"shop"),
    ("goldmedal","🥇","Gold Medal","uncommon",7500,1,1,1,"shop"),
    ("moneywings","💸","Money Wings","uncommon",7500,1,1,1,"shop"),
    # ── Activities & Sports (uncommon 7500c) ────────────────────────────────
    ("soccer","⚽","Soccer","uncommon",7500,1,1,1,"shop"),
    ("basketball","🏀","Basketball","uncommon",7500,1,1,1,"shop"),
    ("football","🏈","Football","uncommon",7500,1,1,1,"shop"),
    ("baseball","⚾","Baseball","uncommon",7500,1,1,1,"shop"),
    ("tennis","🎾","Tennis","uncommon",7500,1,1,1,"shop"),
    ("volleyball","🏐","Volleyball","uncommon",7500,1,1,1,"shop"),
    ("pingpong","🏓","Ping Pong","uncommon",7500,1,1,1,"shop"),
    ("boxing","🥊","Boxing","uncommon",7500,1,1,1,"shop"),
    ("fishing","🎣","Fishing","uncommon",7500,1,1,1,"shop"),
    ("pickaxe","⛏️","Pickaxe","uncommon",7500,1,1,1,"shop"),
    ("microphone","🎤","Microphone","uncommon",7500,1,1,1,"shop"),
    ("musicnotes","🎶","Music Notes","uncommon",7500,1,1,1,"shop"),
    ("palette","🎨","Palette","uncommon",7500,1,1,1,"shop"),
    ("car","🚗","Car","uncommon",7500,1,1,1,"shop"),
    ("airplane","✈️","Airplane","uncommon",7500,1,1,1,"shop"),
    # ── Zodiac (common 1500c) ────────────────────────────────────────────────
    ("aries","♈","Aries","common",1500,1,1,1,"shop"),
    ("taurus","♉","Taurus","common",1500,1,1,1,"shop"),
    ("gemini","♊","Gemini","common",1500,1,1,1,"shop"),
    ("cancer","♋","Cancer","common",1500,1,1,1,"shop"),
    ("leo","♌","Leo","common",1500,1,1,1,"shop"),
    ("virgo","♍","Virgo","common",1500,1,1,1,"shop"),
    ("libra","♎","Libra","common",1500,1,1,1,"shop"),
    ("scorpio","♏","Scorpio","common",1500,1,1,1,"shop"),
    ("sagittarius","♐","Sagittarius","common",1500,1,1,1,"shop"),
    ("capricorn","♑","Capricorn","common",1500,1,1,1,"shop"),
    ("aquarius","♒","Aquarius","common",1500,1,1,1,"shop"),
    ("pisces","♓","Pisces","common",1500,1,1,1,"shop"),
    # ── Symbols (common 1500c) ──────────────────────────────────────────────
    ("check","✅","Check","common",1500,1,1,1,"shop"),
    ("cross","❌","Cross","common",1500,1,1,1,"shop"),
    ("exclaim","❗","Exclaim","common",1500,1,1,1,"shop"),
    ("question","❓","Question","common",1500,1,1,1,"shop"),
    ("hundred","💯","Hundred","common",1500,1,1,1,"shop"),
    ("bell","🔔","Bell","common",1500,1,1,1,"shop"),
    ("locked","🔒","Locked","common",1500,1,1,1,"shop"),
    ("unlocked","🔓","Unlocked","common",1500,1,1,1,"shop"),
    # ── Room & Highrise (common & uncommon) ─────────────────────────────────
    ("house","🏠","House","common",1500,1,1,1,"shop"),
    ("party","🎉","Party","common",1500,1,1,1,"shop"),
    ("confetti","🎊","Confetti","common",1500,1,1,1,"shop"),
    ("couch","🛋️","Couch","common",1500,1,1,1,"shop"),
    ("bed","🛏️","Bed","common",1500,1,1,1,"shop"),
    ("island","🏝️","Island","uncommon",7500,1,1,1,"shop"),
    ("night","🌃","Night City","uncommon",7500,1,1,1,"shop"),
    ("discoball","🪩","Disco Ball","uncommon",7500,1,1,1,"shop"),
    ("maledancer","🕺","Dancer","uncommon",7500,1,1,1,"shop"),
    ("femaledancer","💃","Dancer Girl","uncommon",7500,1,1,1,"shop"),
]


def seed_emoji_badges() -> None:
    """Insert default badge catalog rows. Skips any that already exist."""
    conn = get_connection()
    for row in _BADGE_SEED:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO emoji_badges
                   (badge_id, emoji, name, rarity, price, purchasable,
                    tradeable, sellable, source, created_at, created_by)
                   VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),'system')""",
                row,
            )
        except Exception:
            pass
    conn.commit()
    conn.close()


# ===========================================================================
# NUMBERED SHOP SESSION SYSTEM
# ===========================================================================

import json as _json


def save_shop_session(username: str, shop_type: str, page: int, items: list) -> None:
    """Save the items a player last viewed in any shop."""
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO shop_view_sessions
               (username, shop_type, page, items_json, viewed_at)
               VALUES (lower(?), ?, ?, ?, datetime('now'))
               ON CONFLICT(username) DO UPDATE SET
                 shop_type=excluded.shop_type, page=excluded.page,
                 items_json=excluded.items_json, viewed_at=excluded.viewed_at""",
            (username, shop_type, page, _json.dumps(items)),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_shop_session(username: str) -> dict | None:
    """Return active shop session (None if expired or missing). Expires after 10 min."""
    try:
        conn = get_connection()
        row  = conn.execute(
            """SELECT shop_type, page, items_json, viewed_at FROM shop_view_sessions
               WHERE lower(username)=lower(?)
                 AND datetime(viewed_at, '+10 minutes') > datetime('now')""",
            (username,)
        ).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "shop_type":  row["shop_type"],
            "page":       row["page"],
            "items":      _json.loads(row["items_json"]),
            "viewed_at":  row["viewed_at"],
        }
    except Exception:
        return None


def save_pending_purchase(
    code: str, username: str, shop_type: str,
    item_id: str, item_name: str, price: int, currency: str,
    listing_id: int | None = None,
) -> None:
    try:
        conn = get_connection()
        conn.execute(
            """INSERT OR REPLACE INTO pending_shop_purchases
               (code, username, shop_type, item_id, item_name, price, currency,
                listing_id, created_at, expires_at)
               VALUES (?, lower(?), ?, ?, ?, ?, ?, ?,
                       datetime('now'), datetime('now', '+5 minutes'))""",
            (code, username, shop_type, item_id, item_name, price, currency, listing_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_pending_purchase(code: str) -> dict | None:
    """Return pending purchase row, or None if expired/missing."""
    try:
        conn = get_connection()
        row  = conn.execute(
            """SELECT * FROM pending_shop_purchases
               WHERE code=? AND datetime(expires_at) > datetime('now')""",
            (code.upper().strip(),)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def delete_pending_purchase(code: str) -> None:
    try:
        conn = get_connection()
        conn.execute("DELETE FROM pending_shop_purchases WHERE code=?", (code,))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ===========================================================================
# MINING GAME — DB TABLES + HELPERS
# ===========================================================================

_MINING_ITEMS_SEED = [
    # item_id, name, emoji, rarity, item_type, sell_value
    # Common sell values raised ~2× so early miners aren't stuck with near-
    # worthless drops; stone/coal especially felt unrewarding at 5/8c.
    # Uncommon values raised ~1.7× to create a clearer step from common.
    # Rare and above are left unchanged — they are already well-calibrated.
    ("stone",               "Stone",              "🪨", "common",    "ore", 12),   # was 5
    ("coal",                "Coal",               "⚫", "common",    "ore", 18),   # was 8
    ("copper_ore",          "Copper Ore",         "🟠", "common",    "ore", 28),   # was 15
    ("iron_ore",            "Iron Ore",           "⛓️", "common",    "ore", 40),   # was 20
    ("tin_ore",             "Tin Ore",            "◽", "uncommon",  "ore", 55),   # was 30
    ("lead_ore",            "Lead Ore",           "▪️", "uncommon",  "ore", 65),   # was 35
    ("zinc_ore",            "Zinc Ore",           "🔘", "uncommon",  "ore", 75),   # was 40
    ("quartz",              "Quartz",             "🔹", "uncommon",  "mineral", 100),  # was 60
    ("silver_ore",          "Silver Ore",         "⚪", "rare",      "ore", 120),
    ("gold_ore",            "Gold Ore",           "🟡", "rare",      "ore", 250),
    ("amethyst",            "Amethyst",           "💜", "rare",      "gemstone", 400),
    ("garnet",              "Garnet",             "🔴", "rare",      "gemstone", 450),
    ("nickel_ore",          "Nickel Ore",         "🩶", "epic",      "ore", 700),
    ("bauxite",             "Bauxite",            "🟤", "epic",      "mineral", 800),
    ("jade",                "Jade",               "🟢", "epic",      "gemstone", 1200),
    ("topaz",               "Topaz",              "🟨", "epic",      "gemstone", 1500),
    ("platinum_ore",        "Platinum Ore",       "⚙️", "legendary", "ore",      8000),
    ("emerald",             "Emerald",            "💚", "legendary", "gemstone", 15000),
    ("ruby",                "Ruby",               "❤️", "legendary", "gemstone", 15000),
    ("sapphire",            "Sapphire",           "💙", "legendary", "gemstone", 15000),
    ("diamond",             "Diamond",            "💎", "mythic",    "gemstone", 15000),
    ("opal",                "Opal",               "🌈", "mythic",    "gemstone", 20000),
    ("black_opal",          "Black Opal",         "🌑", "mythic",    "gemstone", 35000),
    ("alexandrite",         "Alexandrite",        "✨", "ultra_rare","gemstone", 75000),
    ("meteorite_fragment",  "Meteorite Fragment", "☄️", "ultra_rare","relic",    150000),
    # ── Additional real-world ores/minerals (added 2026-05) ──────────────────
    ("hematite",            "Hematite",           "🔴", "common",    "mineral",  15),
    ("magnetite",           "Magnetite",          "🧲", "common",    "mineral",  18),
    ("manganese_ore",       "Manganese Ore",      "🟫", "uncommon",  "ore",      82),
    ("galena",              "Galena",             "🔷", "uncommon",  "ore",      70),
    ("sphalerite",          "Sphalerite",         "🔸", "uncommon",  "ore",      78),
    ("chalcopyrite",        "Chalcopyrite",       "🟡", "rare",      "ore",     145),
    ("cassiterite",         "Cassiterite",        "◾", "rare",      "ore",     135),
    ("chromite",            "Chromite",           "💠", "rare",      "ore",     165),
    ("cobalt_ore",          "Cobalt Ore",         "🔵", "epic",      "ore",     850),
    ("titanium_ore",        "Titanium Ore",       "🩵", "epic",      "ore",     950),
    ("uraninite",           "Uraninite",          "☢️", "epic",      "mineral", 1100),
    ("obsidian",            "Obsidian",           "🖤", "rare",      "mineral",  200),
    ("mythril",             "Mythril",            "🌀", "legendary", "ore",    7000),
    # ── Common expansion (Prospecting-style) ─────────────────────────────────
    ("clay",               "Clay",               "🟫", "common",    "mineral",  10),
    ("sandstone",          "Sandstone",          "🪨", "common",    "mineral",  14),
    ("pyrite",             "Pyrite",             "🟡", "common",    "mineral",  16),
    ("blue_ice",           "Blue Ice",           "🧊", "common",    "mineral",  20),
    ("seashell",           "Seashell",           "🐚", "common",    "mineral",  22),
    ("pearl_common",       "Pearl",              "⚪", "common",    "gemstone", 30),
    # ── Rare expansion ────────────────────────────────────────────────────────
    ("coral",              "Coral",              "🪸", "rare",      "mineral", 120),
    ("zircon",             "Zircon",             "🔷", "rare",      "gemstone",150),
    ("malachite",          "Malachite",          "🟢", "rare",      "mineral", 180),
    ("smoky_quartz",       "Smoky Quartz",       "🔘", "rare",      "gemstone",160),
    ("aquamarine",         "Aquamarine",         "🩵", "rare",      "gemstone",200),
    ("amber",              "Amber",              "🟠", "rare",      "gemstone",220),
    ("ruby_shard",         "Ruby Shard",         "🔴", "rare",      "gemstone",240),
    ("frost_quartz",       "Frost Quartz",       "❄️", "rare",      "gemstone",280),
    ("lapis_lazuli",       "Lapis Lazuli",       "💙", "rare",      "mineral", 300),
    # ── Epic expansion ────────────────────────────────────────────────────────
    ("iridium",            "Iridium",            "🩶", "epic",      "ore",     900),
    ("moonstone",          "Moonstone",          "🌙", "epic",      "gemstone",1100),
    ("ammonite_fossil",    "Ammonite Fossil",    "🐌", "epic",      "relic",   1200),
    ("ashvein",            "Ashvein",            "🖤", "epic",      "mineral", 950),
    ("pyronium",           "Pyronium",           "🔥", "epic",      "ore",     1300),
    ("sunstone",           "Sunstone",           "🌟", "epic",      "gemstone",1400),
    ("bloodstone",         "Bloodstone",         "🔴", "epic",      "gemstone",1500),
    ("celestite",          "Celestite",          "🩵", "epic",      "mineral", 1600),
    ("aether_quartz",      "Aether Quartz",      "✨", "epic",      "gemstone",1800),
    ("dragon_glass",       "Dragon Glass",       "🫧", "epic",      "mineral", 2000),
    ("ancient_coral",      "Ancient Coral",      "🪸", "epic",      "relic",   2200),
    ("void_shale",         "Void Shale",         "🌑", "epic",      "mineral", 2400),
    ("crystalized_amber",  "Crystalized Amber",  "🟠", "epic",      "gemstone",2500),
    ("spirit_opal",        "Spirit Opal",        "🌈", "epic",      "gemstone",2800),
    # ── Legendary expansion ───────────────────────────────────────────────────
    ("rose_gold",          "Rose Gold",          "🌹", "legendary", "ore",     4000),
    ("palladium",          "Palladium",          "⚙️", "legendary", "ore",     4500),
    ("cinnabar",           "Cinnabar",           "🔴", "legendary", "mineral", 4800),
    ("star_sapphire",      "Star Sapphire",      "⭐", "legendary", "gemstone",5500),
    ("royal_emerald",      "Royal Emerald",      "💚", "legendary", "gemstone",6000),
    ("molten_core",        "Molten Core",        "🌋", "legendary", "relic",   6500),
    ("frost_diamond",      "Frost Diamond",      "❄️", "legendary", "gemstone",7000),
    ("ancient_relic_ore",  "Ancient Relic Ore",  "🏛️", "legendary", "relic",   7500),
    ("eclipse_stone",      "Eclipse Stone",      "🌑", "legendary", "gemstone",8000),
    ("dragon_ruby",        "Dragon Ruby",        "🐉", "legendary", "gemstone",8500),
    ("golden_fossil",      "Golden Fossil",      "🦴", "legendary", "relic",   9000),
    ("phantom_crystal",    "Phantom Crystal",    "👻", "legendary", "gemstone",9500),
    ("titanium_diamond",   "Titanium Diamond",   "💎", "legendary", "gemstone",10000),
    ("celestial_pearl",    "Celestial Pearl",    "🌕", "legendary", "gemstone",11000),
    # ── Mythic expansion ──────────────────────────────────────────────────────
    ("adamantite",         "Adamantite",         "🛡️", "mythic",    "ore",     18000),
    ("orichalcum",         "Orichalcum",         "⚡", "mythic",    "ore",     22000),
    ("voidstone",          "Voidstone",          "🕳️", "mythic",    "mineral", 25000),
    ("solarite",           "Solarite",           "☀️", "mythic",    "ore",     28000),
    ("lunarium",           "Lunarium",           "🌙", "mythic",    "ore",     30000),
    ("abyss_pearl",        "Abyss Pearl",        "🌊", "mythic",    "gemstone",35000),
    ("phoenix_opal",       "Phoenix Opal",       "🦅", "mythic",    "gemstone",40000),
    ("nebula_crystal",     "Nebula Crystal",     "🌌", "mythic",    "gemstone",45000),
    ("chrono_quartz",      "Chrono Quartz",      "⏳", "mythic",    "gemstone",50000),
    ("ethereal_diamond",   "Ethereal Diamond",   "💎", "mythic",    "gemstone",55000),
    ("leviathan_scale_ore","Leviathan Scale Ore","🐋", "mythic",    "relic",   60000),
    ("arcane_sapphire",    "Arcane Sapphire",    "🔵", "mythic",    "gemstone",65000),
    ("demonite",           "Demonite",           "😈", "mythic",    "ore",     70000),
    ("angelite",           "Angelite",           "😇", "mythic",    "gemstone",75000),
    # ── Prismatic ─────────────────────────────────────────────────────────────
    ("aurora_crystal",     "Aurora Crystal",     "🌌", "prismatic", "gemstone",80000),
    ("rainbow_diamond",    "Rainbow Diamond",    "💎", "prismatic", "gemstone",100000),
    ("prismarine_core",    "Prismarine Core",    "🪩", "prismatic", "gemstone",110000),
    ("chromalite",         "Chromalite",         "🌈", "prismatic", "mineral", 120000),
    ("opal_nova",          "Opal Nova",          "🌟", "prismatic", "gemstone",130000),
    ("spectrum_quartz",    "Spectrum Quartz",    "✨", "prismatic", "gemstone",140000),
    ("celestial_prism",    "Celestial Prism",    "🔮", "prismatic", "gemstone",150000),
    ("prismatic_pearl",    "Prismatic Pearl",    "🌈", "prismatic", "gemstone",160000),
    ("rainbow_obsidian",   "Rainbow Obsidian",   "🖤", "prismatic", "mineral", 175000),
    ("astral_aurora",      "Astral Aurora",      "🌌", "prismatic", "relic",   200000),
    # ── Exotic ────────────────────────────────────────────────────────────────
    ("blood_diamond",      "Blood Diamond",      "💎", "exotic",    "gemstone",220000),
    ("hellfire_ruby",      "Hellfire Ruby",      "🔴", "exotic",    "gemstone",250000),
    ("doomstone",          "Doomstone",          "💀", "exotic",    "mineral", 280000),
    ("infernal_obsidian",  "Infernal Obsidian",  "🖤", "exotic",    "mineral", 300000),
    ("crimson_void_ore",   "Crimson Void Ore",   "🔴", "exotic",    "ore",     330000),
    ("demon_heart_crystal","Demon Heart Crystal", "💔", "exotic",   "gemstone",360000),
    ("elder_dragon_gem",   "Elder Dragon Gem",   "🐉", "exotic",    "gemstone",400000),
    ("abyssal_crown_ore",  "Abyssal Crown Ore",  "👑", "exotic",    "ore",     450000),
    ("scarlet_eclipse",    "Scarlet Eclipse",    "🌑", "exotic",    "gemstone",500000),
    ("forbidden_core",     "Forbidden Core",     "⚠️", "exotic",    "relic",   600000),
]


def ensure_miner_row(username: str) -> None:
    """Ensure a mining_players row exists for this username (no-op if already present)."""
    get_or_create_miner(username)


def seed_mining_items() -> None:
    conn = get_connection()
    for row in _MINING_ITEMS_SEED:
        conn.execute(
            """INSERT OR IGNORE INTO mining_items
               (item_id, name, emoji, rarity, item_type, sell_value, drop_enabled, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, datetime('now'))""",
            row,
        )
    conn.commit()
    conn.close()


# ── Player record ────────────────────────────────────────────────────────────

def get_or_create_miner(username: str) -> dict:
    conn = get_connection()
    key  = username.lower()
    row  = conn.execute(
        "SELECT * FROM mining_players WHERE lower(username)=?", (key,)
    ).fetchone()
    if row is None:
        conn.execute(
            """INSERT OR IGNORE INTO mining_players (username, created_at, updated_at)
               VALUES (?, datetime('now'), datetime('now'))""",
            (username,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM mining_players WHERE lower(username)=?", (key,)
        ).fetchone()
    conn.close()
    return dict(row)


def update_miner(username: str, **kwargs) -> None:
    if not kwargs:
        return
    fields = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [username.lower()]
    conn   = get_connection()
    conn.execute(
        f"UPDATE mining_players SET {fields}, updated_at=datetime('now') WHERE lower(username)=?",
        values,
    )
    conn.commit()
    conn.close()


# ── Settings ─────────────────────────────────────────────────────────────────

def get_mine_setting(key: str, default: str = "") -> str:
    conn = get_connection()
    row  = conn.execute(
        "SELECT value FROM mining_settings WHERE key=?", (key,)
    ).fetchone()
    conn.close()
    return row["value"] if row else default


def set_mine_setting(key: str, value: str) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO mining_settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()
    conn.close()


# ── Inventory ─────────────────────────────────────────────────────────────────

def add_ore(username: str, item_id: str, qty: int) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT INTO mining_inventory (username, item_id, quantity)
           VALUES (lower(?), ?, ?)
           ON CONFLICT(username, item_id) DO UPDATE SET quantity=quantity+?""",
        (username, item_id, qty, qty),
    )
    conn.commit()
    conn.close()


def get_ore_qty(username: str, item_id: str) -> int:
    conn = get_connection()
    row  = conn.execute(
        "SELECT quantity FROM mining_inventory WHERE lower(username)=lower(?) AND item_id=?",
        (username, item_id),
    ).fetchone()
    conn.close()
    return row["quantity"] if row else 0


def get_inventory(username: str) -> list:
    conn = get_connection()
    rows = conn.execute(
        """SELECT mi.item_id, mi.quantity, it.name, it.emoji, it.sell_value, it.rarity
           FROM mining_inventory mi
           JOIN mining_items it ON mi.item_id=it.item_id
           WHERE lower(mi.username)=lower(?) AND mi.quantity>0
           ORDER BY it.sell_value DESC""",
        (username,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def remove_ore(username: str, item_id: str, qty: int) -> bool:
    """Remove qty of ore; returns False if not enough."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT quantity FROM mining_inventory WHERE lower(username)=lower(?) AND item_id=?",
            (username, item_id),
        ).fetchone()
        if not row or row["quantity"] < qty:
            return False
        conn.execute(
            """UPDATE mining_inventory SET quantity=quantity-?
               WHERE lower(username)=lower(?) AND item_id=?""",
            (qty, username, item_id),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def sell_all_ores(username: str, user_id: str) -> dict:
    """Sell all ores atomically. Returns {coins, count}."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT mi.item_id, mi.quantity, it.sell_value
               FROM mining_inventory mi
               JOIN mining_items it ON mi.item_id=it.item_id
               WHERE lower(mi.username)=lower(?) AND mi.quantity>0""",
            (username,),
        ).fetchall()
        total = sum(r["quantity"] * r["sell_value"] for r in rows)
        count = sum(r["quantity"] for r in rows)
        if total == 0:
            return {"coins": 0, "count": 0}
        conn.execute(
            "UPDATE mining_inventory SET quantity=0 WHERE lower(username)=lower(?)",
            (username,),
        )
        conn.execute(
            "UPDATE users SET balance=balance+? WHERE user_id=?",
            (total, user_id),
        )
        conn.execute(
            """INSERT INTO mining_logs (timestamp, username, action, item_id, quantity, coins, details)
               VALUES (datetime('now'), lower(?), 'sellall', '', ?, ?, 'sell all ores')""",
            (username, count, total),
        )
        conn.commit()
        return {"coins": total, "count": count}
    except Exception:
        conn.rollback()
        return {"coins": 0, "count": 0}
    finally:
        conn.close()


def sell_ore_item(username: str, user_id: str, item_id: str, qty: int) -> dict:
    """Sell specific ore atomically. Returns {coins, ok, error}."""
    conn = get_connection()
    try:
        irow = conn.execute(
            "SELECT sell_value FROM mining_items WHERE item_id=?", (item_id,)
        ).fetchone()
        if not irow:
            return {"ok": False, "error": "unknown_item"}
        inv = conn.execute(
            "SELECT quantity FROM mining_inventory WHERE lower(username)=lower(?) AND item_id=?",
            (username, item_id),
        ).fetchone()
        have = inv["quantity"] if inv else 0
        if have < qty:
            return {"ok": False, "error": "not_enough", "have": have}
        total = qty * irow["sell_value"]
        conn.execute(
            "UPDATE mining_inventory SET quantity=quantity-? WHERE lower(username)=lower(?) AND item_id=?",
            (qty, username, item_id),
        )
        conn.execute(
            "UPDATE users SET balance=balance+? WHERE user_id=?",
            (total, user_id),
        )
        conn.execute(
            """INSERT INTO mining_logs (timestamp, username, action, item_id, quantity, coins, details)
               VALUES (datetime('now'), lower(?), 'sell', ?, ?, ?, '')""",
            (username, item_id, qty, total),
        )
        conn.commit()
        return {"ok": True, "coins": total}
    except Exception:
        conn.rollback()
        return {"ok": False, "error": "db_error"}
    finally:
        conn.close()


# ── Mining items catalog ──────────────────────────────────────────────────────

def get_mining_item(item_id: str) -> dict | None:
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM mining_items WHERE item_id=?", (item_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_mining_items(drop_enabled: bool = True) -> list:
    conn  = get_connection()
    q     = "SELECT * FROM mining_items"
    if drop_enabled:
        q += " WHERE drop_enabled=1"
    q += " ORDER BY sell_value"
    rows  = conn.execute(q).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Forced mining drops ───────────────────────────────────────────────────────

def set_forced_drop(
    target_username: str,
    forced_type: str,
    forced_value: str,
    created_by: str,
    expires_hours: int = 24,
    target_user_id: str = "",
) -> int:
    """
    Insert a pending forced drop for target_username.
    forced_type: 'rarity' or 'ore'
    forced_value: rarity key (e.g. 'legendary') or item_id (e.g. 'gold_ore')
    Returns the new row id.
    """
    conn = get_connection()
    conn.execute(
        "UPDATE forced_mining_drops SET status='cleared' "
        "WHERE lower(target_username)=lower(?) AND status='pending'",
        (target_username,),
    )
    cur = conn.execute(
        "INSERT INTO forced_mining_drops "
        "(target_user_id, target_username, forced_type, forced_value, created_by, "
        " expires_at, status) "
        "VALUES (?, lower(?), ?, lower(?), lower(?), "
        " datetime('now', '+' || ? || ' hours'), 'pending')",
        (target_user_id, target_username, forced_type, forced_value, created_by, expires_hours),
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_active_forced_drop(target_username: str, target_user_id: str = "") -> dict | None:
    """Return the oldest pending, non-expired forced drop for target_username or user_id."""
    conn = get_connection()
    row = None
    if target_user_id:
        row = conn.execute(
            "SELECT * FROM forced_mining_drops "
            "WHERE target_user_id=? AND status='pending' "
            "  AND (expires_at='' OR expires_at > datetime('now')) "
            "ORDER BY id ASC LIMIT 1",
            (target_user_id,),
        ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT * FROM forced_mining_drops "
            "WHERE lower(target_username)=lower(?) AND status='pending' "
            "  AND (expires_at='' OR expires_at > datetime('now')) "
            "ORDER BY id ASC LIMIT 1",
            (target_username,),
        ).fetchone()
    conn.close()
    return dict(row) if row else None


def mark_forced_drop_used(drop_id: int) -> None:
    """Mark a forced drop as used."""
    conn = get_connection()
    conn.execute(
        "UPDATE forced_mining_drops "
        "SET status='used', used_at=datetime('now') WHERE id=?",
        (drop_id,),
    )
    conn.commit()
    conn.close()


def clear_forced_drop_by_username(target_username: str, cleared_by: str) -> int:
    """Clear all pending forced drops for target_username. Returns rows affected."""
    conn = get_connection()
    cur  = conn.execute(
        "UPDATE forced_mining_drops SET status='cleared' "
        "WHERE lower(target_username)=lower(?) AND status='pending'",
        (target_username,),
    )
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n


def get_all_active_forced_drops() -> list:
    """Return all pending, non-expired forced drops ordered by creation time."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM forced_mining_drops "
        "WHERE status='pending' "
        "  AND (expires_at='' OR expires_at > datetime('now')) "
        "ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Logging ───────────────────────────────────────────────────────────────────

def log_mine(username: str, action: str, item_id: str = "", qty: int = 0,
             coins: int = 0, details: str = "") -> None:
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO mining_logs (timestamp, username, action, item_id, quantity, coins, details)
               VALUES (datetime('now'), lower(?), ?, ?, ?, ?, ?)""",
            (username, action, item_id, qty, coins, details),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def log_mining_payout(
    username: str,
    ore_id: str,
    ore_name: str,
    rarity: str,
    weight_kg: float | None,
    base_value: int,
    weight_mult: float,
    event_mult: float,
    final_value: int,
    cap_applied: bool,
    cap_amount: int,
) -> None:
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO mining_payout_logs
               (username, ore_id, ore_name, rarity, weight_kg, base_value,
                weight_mult, event_mult, final_value, cap_applied, cap_amount)
               VALUES (lower(?), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (username, ore_id, ore_name, rarity, weight_kg, base_value,
             weight_mult, event_mult, final_value, 1 if cap_applied else 0, cap_amount),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_payout_logs(limit: int = 20, username: str | None = None) -> list:
    conn = get_connection()
    if username:
        rows = conn.execute(
            "SELECT * FROM mining_payout_logs WHERE lower(username)=lower(?)"
            " ORDER BY mined_at DESC LIMIT ?",
            (username, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM mining_payout_logs ORDER BY mined_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_biggest_payouts(limit: int = 10) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM mining_payout_logs ORDER BY final_value DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Leaderboard ───────────────────────────────────────────────────────────────

def get_mine_leaderboard(field: str, limit: int = 5) -> list:
    _VALID = {
        "mining_level", "mining_xp", "total_mines",
        "total_ores", "rare_finds", "coins_earned",
    }
    if field not in _VALID:
        field = "total_mines"
    conn = get_connection()
    rows = conn.execute(
        f"SELECT username, {field} as val FROM mining_players ORDER BY {field} DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_meteorite_leaderboard(limit: int = 5) -> list:
    """Count meteorite_fragment ownership from mining_inventory."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT username, quantity as val FROM mining_inventory
           WHERE item_id='meteorite_fragment' AND quantity>0
           ORDER BY quantity DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Mining events ─────────────────────────────────────────────────────────────

def get_active_mining_event() -> dict | None:
    conn = get_connection()
    row  = conn.execute(
        """SELECT * FROM mining_events
           WHERE active=1 AND datetime(ends_at) > datetime('now')
           ORDER BY id DESC LIMIT 1""",
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def start_mining_event(event_id: str, started_by: str, duration_minutes: int = 60) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE mining_events SET active=0 WHERE active=1",
    )
    conn.execute(
        """INSERT INTO mining_events (event_id, started_by, started_at, ends_at, active)
           VALUES (?, ?, datetime('now'), datetime('now', ?), 1)""",
        (event_id, started_by, f"+{duration_minutes} minutes"),
    )
    conn.commit()
    conn.close()


def stop_mining_event() -> None:
    conn = get_connection()
    conn.execute("UPDATE mining_events SET active=0 WHERE active=1")
    conn.commit()
    conn.close()


# ===========================================================================
# ROOM UTILITY + BOT MODE — DB TABLES + HELPERS
# ===========================================================================

def get_room_setting(key: str, default: str = "") -> str:
    try:
        conn = get_connection()
        row  = conn.execute("SELECT value FROM room_settings WHERE key=?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else default
    except Exception:
        return default


def set_room_setting(key: str, value: str) -> None:
    conn = get_connection()
    conn.execute("INSERT OR REPLACE INTO room_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


# ── Spawns ───────────────────────────────────────────────────────────────────

def get_spawn(name: str) -> dict | None:
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM room_spawns WHERE lower(spawn_name)=lower(?)", (name,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_spawn(name: str, x: float, y: float, z: float, facing: str, created_by: str) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO room_spawns
           (spawn_name, x, y, z, facing, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
        (name, x, y, z, facing, created_by),
    )
    conn.commit()
    conn.close()


def delete_spawn(name: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM room_spawns WHERE lower(spawn_name)=lower(?)", (name,))
    conn.commit()
    conn.close()


def get_all_spawns() -> list:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM room_spawns ORDER BY spawn_name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_spawn_permission(name: str, permission: str) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE room_spawns SET permission=? WHERE lower(spawn_name)=lower(?)",
        (permission, name),
    )
    conn.commit()
    conn.close()


# ── Role spawns ───────────────────────────────────────────────────────────────

def save_role_spawn(role: str, x: float, y: float, z: float,
                    facing: str, set_by: str) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO role_spawns (role, x, y, z, facing, set_by, set_at)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
        (role, x, y, z, facing, set_by),
    )
    conn.commit()
    conn.close()


def get_role_spawn(role: str) -> dict | None:
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM role_spawns WHERE lower(role)=lower(?)", (role,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_role_spawns() -> list:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM role_spawns ORDER BY role").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_role_spawn(role: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM role_spawns WHERE lower(role)=lower(?)", (role,))
    conn.commit()
    conn.close()


# ── Room tags ─────────────────────────────────────────────────────────────────

def get_tag(name: str) -> dict | None:
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM room_tags WHERE lower(tag_name)=lower(?)", (name,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def create_tag(name: str, created_by: str) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT OR IGNORE INTO room_tags (tag_name, created_by, created_at)
           VALUES (?, ?, datetime('now'))""",
        (name, created_by),
    )
    conn.commit()
    conn.close()


def delete_tag(name: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM room_tag_members WHERE lower(tag_name)=lower(?)", (name,))
    conn.execute("DELETE FROM room_tags WHERE lower(tag_name)=lower(?)", (name,))
    conn.commit()
    conn.close()


def get_all_tags() -> list:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM room_tags ORDER BY tag_name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_tag_member(tag_name: str, user_id: str, username: str,
                   added_by: str) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT OR IGNORE INTO room_tag_members
           (tag_name, user_id, username, added_by, added_at)
           VALUES (?, ?, ?, ?, datetime('now'))""",
        (tag_name, user_id, username.lower(), added_by),
    )
    conn.commit()
    conn.close()


def remove_tag_member(tag_name: str, username: str) -> None:
    conn = get_connection()
    conn.execute(
        "DELETE FROM room_tag_members WHERE lower(tag_name)=lower(?) AND lower(username)=lower(?)",
        (tag_name, username),
    )
    conn.commit()
    conn.close()


def get_tag_members(tag_name: str) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM room_tag_members WHERE lower(tag_name)=lower(?) ORDER BY added_at",
        (tag_name,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_tag_spawn(tag_name: str, x, y, z, facing) -> None:
    conn = get_connection()
    conn.execute(
        """UPDATE room_tags SET spawn_x=?, spawn_y=?, spawn_z=?, spawn_facing=?
           WHERE lower(tag_name)=lower(?)""",
        (x, y, z, facing, tag_name),
    )
    conn.commit()
    conn.close()


def set_tag_allow_edit(tag_name: str, enabled: bool) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE room_tags SET allow_member_edit=? WHERE lower(tag_name)=lower(?)",
        (1 if enabled else 0, tag_name),
    )
    conn.commit()
    conn.close()


# ── Room bans ─────────────────────────────────────────────────────────────────

def room_ban_user(username: str, banned_by: str, reason: str,
                  minutes: int | None = None) -> None:
    conn  = get_connection()
    perm  = 0 if minutes else 1
    until = None
    if minutes:
        until = f"datetime('now', '+{minutes} minutes')"
    conn.execute(
        f"""INSERT OR REPLACE INTO room_bans
            (username, banned_by, reason, banned_until, permanent, created_at)
            VALUES (lower(?), ?, ?, {("datetime('now', '+' || ? || ' minutes')" if minutes else 'NULL')}, ?, datetime('now'))""",
        ((username, banned_by, reason, str(minutes), perm) if minutes else (username, banned_by, reason, perm)),
    )
    conn.commit()
    conn.close()


def room_unban_user(username: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM room_bans WHERE lower(username)=lower(?)", (username,))
    conn.commit()
    conn.close()


def is_room_banned(username: str) -> bool:
    conn = get_connection()
    row  = conn.execute(
        """SELECT 1 FROM room_bans WHERE lower(username)=lower(?)
           AND (permanent=1 OR datetime(banned_until) > datetime('now'))""",
        (username,),
    ).fetchone()
    conn.close()
    return row is not None


def get_all_room_bans() -> list:
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM room_bans
           WHERE permanent=1 OR datetime(banned_until) > datetime('now')
           ORDER BY created_at DESC LIMIT 20""",
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Room warnings ─────────────────────────────────────────────────────────────

def add_room_warning(username: str, warned_by: str, reason: str) -> int:
    conn = get_connection()
    conn.execute(
        """INSERT INTO room_warnings (username, warned_by, reason, created_at, active)
           VALUES (lower(?), ?, ?, datetime('now'), 1)""",
        (username, warned_by, reason),
    )
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM room_warnings WHERE lower(username)=lower(?) AND active=1",
        (username,),
    ).fetchone()[0]
    conn.close()
    return count


def get_room_warnings(username: str) -> list:
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM room_warnings WHERE lower(username)=lower(?) AND active=1
           ORDER BY created_at DESC LIMIT 10""",
        (username,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Hearts ────────────────────────────────────────────────────────────────────

def give_heart(giver: str, receiver: str) -> dict:
    conn = get_connection()
    conn.execute(
        """INSERT INTO room_hearts (giver_username, receiver_username, count, last_given_at)
           VALUES (lower(?), lower(?), 1, datetime('now'))
           ON CONFLICT(giver_username, receiver_username)
           DO UPDATE SET count=count+1, last_given_at=datetime('now')""",
        (giver, receiver),
    )
    conn.execute(
        """INSERT INTO room_heart_totals (username, hearts_received, hearts_given)
           VALUES (lower(?), 1, 0)
           ON CONFLICT(username) DO UPDATE SET hearts_received=hearts_received+1""",
        (receiver,),
    )
    conn.execute(
        """INSERT INTO room_heart_totals (username, hearts_received, hearts_given)
           VALUES (lower(?), 0, 1)
           ON CONFLICT(username) DO UPDATE SET hearts_given=hearts_given+1""",
        (giver,),
    )
    total = conn.execute(
        "SELECT hearts_received FROM room_heart_totals WHERE lower(username)=lower(?)",
        (receiver,),
    ).fetchone()
    conn.commit()
    conn.close()
    return {"total": total["hearts_received"] if total else 1}


def get_heart_totals(username: str) -> dict:
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM room_heart_totals WHERE lower(username)=lower(?)", (username,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {"hearts_received": 0, "hearts_given": 0}


def get_heart_leaderboard(limit: int = 5) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT username, hearts_received FROM room_heart_totals ORDER BY hearts_received DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_heart_cooldown_remaining(giver: str, receiver: str) -> float:
    cooldown = int(get_room_setting("heart_cooldown_seconds", "60"))
    conn     = get_connection()
    row      = conn.execute(
        "SELECT last_given_at FROM room_hearts WHERE lower(giver_username)=lower(?) AND lower(receiver_username)=lower(?)",
        (giver, receiver),
    ).fetchone()
    conn.close()
    if not row or not row["last_given_at"]:
        return 0
    import time
    from datetime import datetime, timezone
    try:
        dt   = datetime.strptime(row["last_given_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        sec  = (datetime.now(timezone.utc) - dt).total_seconds()
        return max(0, cooldown - sec)
    except Exception:
        return 0


# ── Social ────────────────────────────────────────────────────────────────────

def is_social_enabled(username: str) -> bool:
    conn = get_connection()
    row  = conn.execute(
        "SELECT social_enabled FROM social_preferences WHERE lower(username)=lower(?)", (username,)
    ).fetchone()
    conn.close()
    return row["social_enabled"] == 1 if row else True


def set_social_enabled(username: str, enabled: bool) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO social_preferences (username, social_enabled)
           VALUES (lower(?), ?)""",
        (username, 1 if enabled else 0),
    )
    conn.commit()
    conn.close()


def is_social_blocked(username: str, blocked_by: str) -> bool:
    conn = get_connection()
    row  = conn.execute(
        "SELECT 1 FROM social_blocks WHERE lower(username)=lower(?) AND lower(blocked_username)=lower(?)",
        (blocked_by, username),
    ).fetchone()
    conn.close()
    return row is not None


def set_social_block(blocker: str, target: str, blocked: bool) -> None:
    conn = get_connection()
    if blocked:
        conn.execute(
            "INSERT OR IGNORE INTO social_blocks (username, blocked_username) VALUES (lower(?), lower(?))",
            (blocker, target),
        )
    else:
        conn.execute(
            "DELETE FROM social_blocks WHERE lower(username)=lower(?) AND lower(blocked_username)=lower(?)",
            (blocker, target),
        )
    conn.commit()
    conn.close()


# ── Welcome ────────────────────────────────────────────────────────────────────

def has_been_welcomed(username: str) -> bool:
    conn = get_connection()
    row  = conn.execute(
        "SELECT welcomed FROM room_welcome_seen WHERE lower(username)=lower(?)", (username,)
    ).fetchone()
    conn.close()
    return bool(row and row["welcomed"])


def mark_welcomed(username: str) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO room_welcome_seen
           (username, welcomed, welcomed_at, last_seen_at)
           VALUES (lower(?), 1, datetime('now'), datetime('now'))""",
        (username,),
    )
    conn.commit()
    conn.close()


def reset_welcome_seen(username: str) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE room_welcome_seen SET welcomed=0 WHERE lower(username)=lower(?)", (username,)
    )
    conn.commit()
    conn.close()


# ── Intervals ─────────────────────────────────────────────────────────────────

def get_all_intervals() -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM room_interval_messages ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_interval(message: str, minutes: int, created_by: str) -> int:
    conn = get_connection()
    cur  = conn.execute(
        """INSERT INTO room_interval_messages (message, interval_minutes, enabled, created_by, created_at)
           VALUES (?, ?, 1, ?, datetime('now'))""",
        (message, minutes, created_by),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id


def delete_interval(interval_id: int) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM room_interval_messages WHERE id=?", (interval_id,))
    conn.commit()
    conn.close()


def toggle_interval(interval_id: int, enabled: bool) -> None:
    conn = get_connection()
    conn.execute("UPDATE room_interval_messages SET enabled=? WHERE id=?",
                 (1 if enabled else 0, interval_id))
    conn.commit()
    conn.close()


def mark_interval_sent(interval_id: int) -> None:
    conn = get_connection()
    conn.execute("UPDATE room_interval_messages SET last_sent_at=datetime('now') WHERE id=?",
                 (interval_id,))
    conn.commit()
    conn.close()


# ── Room action log ───────────────────────────────────────────────────────────

def log_room_action(actor: str, target: str, action: str, details: str = "") -> None:
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO room_action_logs (timestamp, actor_username, target_username, action, details)
               VALUES (datetime('now'), ?, ?, ?, ?)""",
            (actor, target, action, details),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_room_action_logs(username: str = "", limit: int = 10) -> list:
    conn = get_connection()
    if username:
        rows = conn.execute(
            """SELECT * FROM room_action_logs
               WHERE lower(actor_username)=lower(?) OR lower(target_username)=lower(?)
               ORDER BY id DESC LIMIT ?""",
            (username, username, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM room_action_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Follow state ──────────────────────────────────────────────────────────────

def get_follow_state() -> dict | None:
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM room_follow_state WHERE bot_id='main' AND enabled=1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def set_follow_state(target_username: str, enabled: bool) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO room_follow_state
           (bot_id, target_username, enabled, updated_at)
           VALUES ('main', ?, ?, datetime('now'))""",
        (target_username if enabled else "", 1 if enabled else 0),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Multi-bot DB helpers
# ---------------------------------------------------------------------------

def acquire_module_lock(module: str, bot_id: str, ttl_seconds: int = 30) -> bool:
    """
    Acquire a lock for a game module. Returns True if lock was acquired.
    Automatically clears stale locks older than ttl_seconds.
    """
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO bot_module_locks (module, bot_id, locked_at, expires_at)
               VALUES (?, ?, datetime('now'), datetime('now', ?))
               ON CONFLICT(module) DO UPDATE SET
                   bot_id     = excluded.bot_id,
                   locked_at  = excluded.locked_at,
                   expires_at = excluded.expires_at
               WHERE expires_at < datetime('now')""",
            (module, bot_id, f"+{ttl_seconds} seconds"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT bot_id FROM bot_module_locks WHERE module=?", (module,)
        ).fetchone()
        return row is not None and row["bot_id"] == bot_id
    except Exception:
        return False
    finally:
        conn.close()


def acquire_module_announce_lock(
        module: str, message_key: str, bot_id: str,
        ttl_seconds: int = 300) -> bool:
    """
    Acquire a restore-announce dedupe lock for (module, message_key).
    Returns True if this bot acquired the lock (safe to send).
    Returns False if another bot already sent within ttl_seconds.
    """
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO module_announcement_locks
               (module, message_key, bot_id, sent_at, expires_at)
               VALUES (?, ?, ?, datetime('now'), datetime('now', ?))
               ON CONFLICT(module, message_key) DO UPDATE SET
                   bot_id     = excluded.bot_id,
                   sent_at    = excluded.sent_at,
                   expires_at = excluded.expires_at
               WHERE expires_at < datetime('now')""",
            (module, message_key, bot_id, f"+{ttl_seconds} seconds"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT bot_id FROM module_announcement_locks "
            "WHERE module=? AND message_key=?",
            (module, message_key),
        ).fetchone()
        return row is not None and row["bot_id"] == bot_id
    except Exception:
        return True  # if table missing, allow send
    finally:
        conn.close()


def release_module_lock(module: str, bot_id: str) -> None:
    """Release a lock only if this bot_id owns it."""
    conn = get_connection()
    conn.execute(
        "DELETE FROM bot_module_locks WHERE module=? AND bot_id=?",
        (module, bot_id),
    )
    conn.commit()
    conn.close()


def is_module_locked(module: str) -> bool:
    """Return True if a non-expired lock exists for this module."""
    conn = get_connection()
    row = conn.execute(
        "SELECT bot_id FROM bot_module_locks WHERE module=? AND expires_at > datetime('now')",
        (module,),
    ).fetchone()
    conn.close()
    return row is not None


def get_module_lock_owner(module: str) -> str | None:
    """Return the bot_id holding a non-expired lock, or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT bot_id FROM bot_module_locks WHERE module=? AND expires_at > datetime('now')",
        (module,),
    ).fetchone()
    conn.close()
    return row["bot_id"] if row else None


def upsert_bot_instance(bot_id: str, bot_username: str, bot_mode: str,
                        prefix: str = "", status: str = "online",
                        db_connected: int = 1, last_error: str = "",
                        current_room_id: str = "",
                        write_heartbeat: bool = False) -> None:
    conn = get_connection()
    if write_heartbeat:
        conn.execute(
            """INSERT INTO bot_instances
                   (bot_id, bot_username, bot_mode, prefix,
                    status, last_seen_at, db_connected, last_error,
                    current_room_id, last_heartbeat_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?, ?, datetime('now'))
               ON CONFLICT(bot_id) DO UPDATE SET
                   bot_username     = excluded.bot_username,
                   bot_mode         = excluded.bot_mode,
                   prefix           = excluded.prefix,
                   status           = excluded.status,
                   last_seen_at     = excluded.last_seen_at,
                   db_connected     = excluded.db_connected,
                   last_error       = excluded.last_error,
                   current_room_id  = excluded.current_room_id,
                   last_heartbeat_at = excluded.last_heartbeat_at""",
            (bot_id, bot_username, bot_mode, prefix, status,
             db_connected, last_error, current_room_id),
        )
    else:
        conn.execute(
            """INSERT INTO bot_instances
                   (bot_id, bot_username, bot_mode, prefix,
                    status, last_seen_at, db_connected, last_error, current_room_id)
               VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?, ?)
               ON CONFLICT(bot_id) DO UPDATE SET
                   bot_username     = excluded.bot_username,
                   bot_mode         = excluded.bot_mode,
                   prefix           = excluded.prefix,
                   status           = excluded.status,
                   last_seen_at     = excluded.last_seen_at,
                   db_connected     = excluded.db_connected,
                   last_error       = excluded.last_error,
                   current_room_id  = excluded.current_room_id""",
            (bot_id, bot_username, bot_mode, prefix, status,
             db_connected, last_error, current_room_id),
        )
    conn.commit()
    conn.close()


def get_bot_instances() -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM bot_instances ORDER BY bot_id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_command_owners() -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM bot_command_ownership ORDER BY command"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_command_owner_db(command: str, module: str, owner_bot_mode: str,
                         fallback_allowed: int = 1) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT INTO bot_command_ownership
               (command, module, owner_bot_mode, fallback_allowed)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(command) DO UPDATE SET
               module           = excluded.module,
               owner_bot_mode   = excluded.owner_bot_mode,
               fallback_allowed = excluded.fallback_allowed""",
        (command, module, owner_bot_mode, fallback_allowed),
    )
    conn.commit()
    conn.close()


def enable_bot_instance(bot_id: str, enabled: bool) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE bot_instances SET enabled=? WHERE bot_id=?",
        (1 if enabled else 0, bot_id),
    )
    conn.commit()
    conn.close()


def set_bot_instance_module(bot_id: str, mode: str) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE bot_instances SET bot_mode=? WHERE bot_id=?",
        (mode, bot_id),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Ore mastery helpers
# ---------------------------------------------------------------------------

def get_ore_mastery_claimed(username: str) -> set:
    """Return the set of milestone thresholds already claimed by this player."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT milestone FROM ore_mastery WHERE lower(username)=lower(?)",
        (username,),
    ).fetchall()
    conn.close()
    return {r["milestone"] for r in rows}


def claim_ore_mastery(username: str, milestone: int) -> None:
    """Record a claimed mastery milestone (idempotent)."""
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO ore_mastery (username, milestone) VALUES (lower(?), ?)",
        (username, milestone),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Mining contract helpers
# ---------------------------------------------------------------------------

def get_miner_contract(username: str) -> dict | None:
    """Return the player's current active contract row, or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM miner_contracts WHERE lower(username)=lower(?)",
        (username,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def set_miner_contract(username: str, contract_id: int, ore_id: str,
                       qty_needed: int, reward_coins: int, expires_at: str) -> None:
    """Assign (or replace) a mining contract for a player."""
    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO miner_contracts
           (username, contract_id, ore_id, qty_needed, qty_delivered, reward_coins, expires_at)
           VALUES (lower(?), ?, ?, ?, 0, ?, ?)""",
        (username, contract_id, ore_id, qty_needed, reward_coins, expires_at),
    )
    conn.commit()
    conn.close()


def update_contract_delivery(username: str, qty: int) -> int:
    """Increment qty_delivered for the player's contract. Returns new total."""
    conn = get_connection()
    conn.execute(
        "UPDATE miner_contracts SET qty_delivered=qty_delivered+? WHERE lower(username)=lower(?)",
        (qty, username),
    )
    conn.commit()
    row = conn.execute(
        "SELECT qty_delivered FROM miner_contracts WHERE lower(username)=lower(?)",
        (username,),
    ).fetchone()
    conn.close()
    return row["qty_delivered"] if row else 0


def clear_miner_contract(username: str) -> None:
    """Delete the player's active contract (claim or reroll)."""
    conn = get_connection()
    conn.execute(
        "DELETE FROM miner_contracts WHERE lower(username)=lower(?)",
        (username,),
    )
    conn.commit()
    conn.close()


def get_ore_qty(username: str, item_id: str) -> int:
    """Return how many of a specific ore the player holds (0 if none)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT quantity FROM mining_inventory WHERE lower(username)=lower(?) AND item_id=?",
        (username, item_id),
    ).fetchone()
    conn.close()
    return row["quantity"] if row else 0


def add_balance(user_id: str, amount: int) -> None:
    """Add coins to a user's balance (use a negative amount to subtract)."""
    conn = get_connection()
    conn.execute(
        "UPDATE users SET balance=balance+? WHERE user_id=?",
        (amount, user_id),
    )
    conn.commit()
    conn.close()


# ── Poker hole-card secure storage ────────────────────────────────────────────

def ensure_delivery_row(round_id: str, username: str,
                        display_name: str = "") -> None:
    """INSERT OR IGNORE a skeleton delivery row (cards_sent=0) for a player.

    Called before whispers so cardstatus always has rows even if delivery fails.
    """
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO poker_card_delivery "
        "(round_id, username, display_name, cards_sent, attempts, "
        "sent_at, last_attempt_at, failed_reason) "
        "VALUES (?, LOWER(?), ?, 0, 0, '', '', '')",
        (round_id, username, (display_name or username)[:60]),
    )
    conn.commit()
    conn.close()


def rebuild_delivery_rows(round_id: str) -> int:
    """Create missing poker_card_delivery rows from poker_hole_cards.

    Does not overwrite existing rows.  Returns number of rows created.
    """
    conn = get_connection()
    hc_rows = conn.execute(
        "SELECT username_key, display_name FROM poker_hole_cards "
        "WHERE round_id=?",
        (round_id,),
    ).fetchall()
    created = 0
    for row in hc_rows:
        result = conn.execute(
            "INSERT OR IGNORE INTO poker_card_delivery "
            "(round_id, username, display_name, cards_sent, attempts, "
            "sent_at, last_attempt_at, failed_reason) "
            "VALUES (?, ?, ?, 0, 0, '', '', '')",
            (round_id, row["username_key"], row["display_name"] or row["username_key"]),
        )
        if result.rowcount > 0:
            created += 1
    conn.commit()
    conn.close()
    return created


def save_hole_cards(round_id: str, username_key: str, display_name: str,
                    card1: str, card2: str) -> None:
    """Save hole cards for a player (INSERT OR IGNORE — never overwrites)."""
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO poker_hole_cards "
        "(round_id, username_key, display_name, card1, card2, created_at) "
        "VALUES (?, LOWER(?), ?, ?, ?, datetime('now'))",
        (round_id, username_key, display_name, card1, card2),
    )
    conn.commit()
    conn.close()


def get_hole_cards(round_id: str, username_key: str) -> dict | None:
    """Return a player's saved hole cards by normalized username."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM poker_hole_cards "
        "WHERE round_id=? AND username_key=LOWER(?)",
        (round_id, username_key),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── Poker card delivery tracking ──────────────────────────────────────────────

def record_card_delivery(round_id: str, username: str, sent: bool,
                         reason: str = "", display_name: str = "") -> None:
    """Upsert a card delivery attempt record, incrementing the attempts counter."""
    conn = get_connection()
    ukey = username.lower()
    dn   = (display_name or username)[:60]
    row  = conn.execute(
        "SELECT attempts, cards_sent, display_name FROM poker_card_delivery "
        "WHERE round_id=? AND username=?",
        (round_id, ukey),
    ).fetchone()
    if row:
        new_attempts = (row["attempts"] or 0) + 1
        new_sent     = 1 if (sent or row["cards_sent"]) else 0
        keep_dn      = row["display_name"] or dn
        conn.execute(
            "UPDATE poker_card_delivery "
            "SET display_name=?, cards_sent=?, attempts=?, "
            "last_attempt_at=datetime('now'), failed_reason=? "
            "WHERE round_id=? AND username=?",
            (keep_dn, new_sent, new_attempts,
             "" if sent else reason[:120], round_id, ukey),
        )
    else:
        conn.execute(
            "INSERT INTO poker_card_delivery "
            "(round_id, username, display_name, cards_sent, attempts, "
            "sent_at, last_attempt_at, failed_reason) "
            "VALUES (?, ?, ?, ?, 1, "
            "CASE WHEN ? THEN datetime('now') ELSE '' END, "
            "datetime('now'), ?)",
            (round_id, ukey, dn, 1 if sent else 0,
             1 if sent else 0, "" if sent else reason[:120]),
        )
    conn.commit()
    conn.close()


def get_card_delivery_status(round_id: str) -> list:
    """Return delivery rows for a round (list of dicts)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT username, display_name, cards_sent, attempts, "
        "sent_at, last_attempt_at, failed_reason "
        "FROM poker_card_delivery WHERE round_id=? ORDER BY rowid",
        (round_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_card_delivered(round_id: str, username: str) -> None:
    """Mark delivery as successful and increment attempt count (/ph fallback)."""
    conn = get_connection()
    ukey = username.lower()
    row  = conn.execute(
        "SELECT attempts FROM poker_card_delivery WHERE round_id=? AND username=?",
        (round_id, ukey),
    ).fetchone()
    if row:
        new_att = (row["attempts"] or 0) + 1
        conn.execute(
            "UPDATE poker_card_delivery "
            "SET cards_sent=1, attempts=?, last_attempt_at=datetime('now'), "
            "failed_reason='' WHERE round_id=? AND username=?",
            (new_att, round_id, ukey),
        )
    else:
        conn.execute(
            "INSERT INTO poker_card_delivery "
            "(round_id, username, display_name, cards_sent, attempts, "
            "sent_at, last_attempt_at, failed_reason) "
            "VALUES (?, ?, '', 1, 1, datetime('now'), datetime('now'), '')",
            (round_id, ukey),
        )
    conn.commit()
    conn.close()


def seed_room_settings() -> None:
    defaults = [
        ("self_teleport_enabled",   "false"),
        ("group_teleport_enabled",  "true"),
        ("public_emotes_enabled",   "true"),
        ("force_emotes_enabled",    "true"),
        ("loop_emotes_enabled",     "true"),
        ("sync_dance_enabled",      "true"),
        ("emote_loop_interval_seconds", "8"),
        ("bot_follow_enabled",      "true"),
        ("follow_interval_seconds", "3"),
        ("welcome_enabled",         "true"),
        ("welcome_message",         "👋 Welcome to the Lounge! Type /help to get started."),
        ("welcome_interval_enabled","false"),
        ("welcome_interval_minutes","30"),
        ("heart_cooldown_seconds",  "60"),
        ("bot_prefix_enabled",      "true"),
        ("category_prefix_enabled", "true"),
        ("bot_mode_switch_allowed", "true"),
        ("social_enabled",          "true"),
        ("min_interval_minutes",         "10"),
        ("repeat_max_count",             "5"),
        ("repeat_min_seconds",           "10"),
        ("multibot_fallback_enabled",    "true"),
        ("bot_startup_announce_enabled", "false"),
        ("autogames_owner_bot_mode",         "eventhost"),
        ("module_restore_announce_enabled",  "true"),
        # ── Player display format ─────────────────────────────────────────────
        ("display_badges_enabled",  "true"),
        ("display_titles_enabled",  "true"),
        # ── Time-in-Room EXP ─────────────────────────────────────────────────
        ("time_exp_enabled",              "true"),
        ("time_exp_cap",                  "1500"),
        ("time_exp_tick_seconds",         "60"),
        ("time_exp_active_bonus_enabled", "true"),
        ("time_exp_active_bonus",         "0.25"),
        ("time_exp_active_window_min",    "5"),
        ("time_exp_bot_exp_enabled",      "false"),
    ]
    conn = get_connection()
    for k, v in defaults:
        conn.execute("INSERT OR IGNORE INTO room_settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()


# ============================================================================
# Fishing helpers
# ============================================================================

def get_or_create_fish_profile(user_id: str, username: str) -> dict:
    """Return the fish_profiles row for a user, creating it if absent."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM fish_profiles WHERE user_id=?", (user_id,)
    ).fetchone()
    if row is None:
        conn.execute(
            """INSERT OR IGNORE INTO fish_profiles (user_id, username)
               VALUES (?, ?)""",
            (user_id, username),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM fish_profiles WHERE user_id=?", (user_id,)
        ).fetchone()
    conn.close()
    return dict(row)


def update_fish_profile(user_id: str, username: str, **kwargs) -> None:
    """Update one or more columns in fish_profiles."""
    if not kwargs:
        return
    fields = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [user_id]
    conn   = get_connection()
    conn.execute(
        f"UPDATE fish_profiles SET {fields}, updated_at=datetime('now') WHERE user_id=?",
        values,
    )
    conn.commit()
    conn.close()


def save_fish_catch(
    fish_name: str,
    rarity: str,
    weight: float,
    base_value: int,
    final_value: int,
    fxp: int,
    user_id: str,
    username: str,
) -> None:
    """Record a fish catch."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO fish_catch_records
           (user_id, username, fish_name, rarity, weight,
            base_value, final_value, fxp_earned)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, username, fish_name, rarity, weight,
         base_value, final_value, fxp),
    )
    conn.commit()
    conn.close()


def get_fish_catches(user_id: str, limit: int = 5) -> list[dict]:
    """Return the most recent fish catches for a user."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT fish_name, rarity, weight, final_value, fxp_earned, caught_at
           FROM fish_catch_records
           WHERE user_id=?
           ORDER BY caught_at DESC LIMIT ?""",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_top_fishers(limit: int = 10) -> list[dict]:
    """Return top players by total catch count."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT username, fishing_level, fishing_xp, total_catches
           FROM fish_profiles
           ORDER BY total_catches DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_biggest_fish_catches(limit: int = 10) -> list[dict]:
    """Return the single heaviest catch per user (best_fish_weight)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT username, best_fish_name AS fish_name,
                  best_fish_weight AS weight, best_fish_value AS value
           FROM fish_profiles
           WHERE best_fish_weight > 0
           ORDER BY best_fish_weight DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def player_owns_rod(user_id: str, rod_name: str) -> bool:
    """Return True if the player owns the specified rod."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT 1 FROM player_rods WHERE user_id=? AND rod_name=?",
        (user_id, rod_name),
    ).fetchone()
    conn.close()
    return row is not None


def add_player_rod(user_id: str, username: str, rod_name: str) -> None:
    """Record a rod purchase (idempotent)."""
    conn = get_connection()
    conn.execute(
        """INSERT OR IGNORE INTO player_rods (user_id, rod_name, username)
           VALUES (?, ?, ?)""",
        (user_id, rod_name, username),
    )
    conn.commit()
    conn.close()


def equip_rod(user_id: str, username: str, rod_name: str) -> None:
    """Set the player's equipped rod."""
    conn = get_connection()
    conn.execute(
        "UPDATE fish_profiles SET equipped_rod=?, updated_at=datetime('now') WHERE user_id=?",
        (rod_name, user_id),
    )
    if conn.execute("SELECT changes()").fetchone()[0] == 0:
        conn.execute(
            """INSERT OR IGNORE INTO fish_profiles (user_id, username, equipped_rod)
               VALUES (?, ?, ?)""",
            (user_id, username, rod_name),
        )
    conn.commit()
    conn.close()


def get_auto_activity_setting(key: str, default: str = "") -> str:
    """Return a value from auto_activity_settings."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT value FROM auto_activity_settings WHERE key=?", (key,)
    ).fetchone()
    conn.close()
    return row["value"] if row else default


def set_auto_activity_setting(key: str, value: str) -> None:
    """Upsert a key/value in auto_activity_settings."""
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO auto_activity_settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()
    conn.close()


# ── AutoMine session helpers ───────────────────────────────────────────────────

def save_auto_mine_session(user_id: str, username: str, started_at: str,
                           max_attempts: int, max_minutes: int,
                           attempts_done: int = 0, resumed: int = 0) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO auto_mine_sessions
           (user_id, username, started_at, max_attempts, max_minutes,
            attempts_done, status, resumed, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'active', ?, datetime('now'))""",
        (user_id, username, started_at, max_attempts, max_minutes, attempts_done, resumed),
    )
    conn.commit()
    conn.close()


def update_auto_mine_attempts(user_id: str, attempts_done: int) -> None:
    conn = get_connection()
    conn.execute(
        """UPDATE auto_mine_sessions SET attempts_done=?, updated_at=datetime('now')
           WHERE user_id=? AND status='active'""",
        (attempts_done, user_id),
    )
    conn.commit()
    conn.close()


def stop_auto_mine_session(user_id: str, status: str = "stopped") -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE auto_mine_sessions SET status=?, updated_at=datetime('now') WHERE user_id=?",
        (status, user_id),
    )
    conn.commit()
    conn.close()


def get_active_auto_mine_session(user_id: str) -> dict | None:
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM auto_mine_sessions WHERE user_id=? AND status='active'",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_active_auto_mine_sessions() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM auto_mine_sessions WHERE status='active'"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── AutoFish session helpers ───────────────────────────────────────────────────

def save_auto_fish_session(user_id: str, username: str, started_at: str,
                           max_attempts: int, max_minutes: int,
                           attempts_done: int = 0, resumed: int = 0) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO auto_fish_sessions
           (user_id, username, started_at, max_attempts, max_minutes,
            attempts_done, status, resumed, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'active', ?, datetime('now'))""",
        (user_id, username, started_at, max_attempts, max_minutes, attempts_done, resumed),
    )
    conn.commit()
    conn.close()


def update_auto_fish_attempts(user_id: str, attempts_done: int) -> None:
    conn = get_connection()
    conn.execute(
        """UPDATE auto_fish_sessions SET attempts_done=?, updated_at=datetime('now')
           WHERE user_id=? AND status='active'""",
        (attempts_done, user_id),
    )
    conn.commit()
    conn.close()


def stop_auto_fish_session(user_id: str, status: str = "stopped") -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE auto_fish_sessions SET status=?, updated_at=datetime('now') WHERE user_id=?",
        (status, user_id),
    )
    conn.commit()
    conn.close()


def get_active_auto_fish_session(user_id: str) -> dict | None:
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM auto_fish_sessions WHERE user_id=? AND status='active'",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_active_auto_fish_sessions() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM auto_fish_sessions WHERE status='active'"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Luxe auto time helpers (3.1I UPDATE) ──────────────────────────────────────

def get_luxe_auto_time(user_id: str, auto_type: str) -> int:
    """Return remaining Luxe auto seconds for user. auto_type: 'mining'|'fishing'."""
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT remaining_seconds FROM luxe_auto_time WHERE user_id=? AND auto_type=?",
            (user_id, auto_type),
        ).fetchone()
        conn.close()
        return max(0, int(row["remaining_seconds"])) if row else 0
    except Exception:
        return 0


def set_luxe_auto_time(user_id: str, username: str, auto_type: str, seconds: int) -> int:
    """Set remaining Luxe auto seconds. Returns the new value (clamped ≥ 0)."""
    seconds = max(0, int(seconds))
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO luxe_auto_time (user_id, username, auto_type, remaining_seconds, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))
               ON CONFLICT(user_id, auto_type)
               DO UPDATE SET remaining_seconds=excluded.remaining_seconds,
                             username=excluded.username,
                             updated_at=excluded.updated_at""",
            (user_id, username.lower(), auto_type, seconds),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    return seconds


def add_luxe_auto_time(user_id: str, username: str, auto_type: str, seconds: int) -> int:
    """Add Luxe auto seconds. Returns new total."""
    current = get_luxe_auto_time(user_id, auto_type)
    return set_luxe_auto_time(user_id, username, auto_type, current + max(0, seconds))


def deduct_luxe_auto_time(user_id: str, auto_type: str, seconds: int) -> int:
    """Deduct seconds from Luxe auto time (floor 0). Returns remaining."""
    current = get_luxe_auto_time(user_id, auto_type)
    new_val = max(0, current - int(seconds))
    try:
        conn = get_connection()
        conn.execute(
            """UPDATE luxe_auto_time
               SET remaining_seconds=?, updated_at=datetime('now')
               WHERE user_id=? AND auto_type=?""",
            (new_val, user_id, auto_type),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    return new_val


# ── First-find reward helpers ──────────────────────────────────────────────────

def get_first_find_reward(category: str, rarity: str) -> dict | None:
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM first_find_rewards WHERE category=? AND rarity=? AND enabled=1",
        (category, rarity),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def set_first_find_reward(category: str, rarity: str, players_count: int,
                          gold_amount: float, coin_fallback: int = 0) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT INTO first_find_rewards
               (category, rarity, players_count, gold_amount,
                coin_fallback_amount, enabled, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 1, datetime('now'), datetime('now'))
           ON CONFLICT(category, rarity) DO UPDATE SET
               players_count=excluded.players_count,
               gold_amount=excluded.gold_amount,
               coin_fallback_amount=excluded.coin_fallback_amount,
               enabled=1,
               updated_at=datetime('now')""",
        (category, rarity, players_count, gold_amount, coin_fallback),
    )
    conn.commit()
    conn.close()


def get_all_first_find_rewards() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM first_find_rewards ORDER BY category, rarity"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_first_find_claim_count(reward_id: int) -> int:
    conn = get_connection()
    row  = conn.execute(
        "SELECT COUNT(*) FROM first_find_claims WHERE reward_id=?", (reward_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def has_first_find_claimed(reward_id: int, user_id: str) -> bool:
    conn = get_connection()
    row  = conn.execute(
        "SELECT 1 FROM first_find_claims WHERE reward_id=? AND user_id=?",
        (reward_id, user_id),
    ).fetchone()
    conn.close()
    return row is not None


def add_first_find_claim(reward_id: int, user_id: str, username: str,
                         category: str, rarity: str, claim_rank: int,
                         reward_status: str = "pending") -> int:
    conn = get_connection()
    cur  = conn.execute(
        """INSERT OR IGNORE INTO first_find_claims
               (reward_id, user_id, username, category, rarity,
                claim_rank, reward_status, claimed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (reward_id, user_id, username, category, rarity, claim_rank, reward_status),
    )
    conn.commit()
    rid = cur.lastrowid or 0
    conn.close()
    return rid


def reset_first_find(category: str, rarity: str) -> int:
    conn   = get_connection()
    reward = conn.execute(
        "SELECT id FROM first_find_rewards WHERE category=? AND rarity=?",
        (category, rarity),
    ).fetchone()
    deleted = 0
    if reward:
        cur     = conn.execute(
            "DELETE FROM first_find_claims WHERE reward_id=?", (reward[0],)
        )
        deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


def get_first_find_claims(category: str, rarity: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT ffc.* FROM first_find_claims ffc
           JOIN first_find_rewards ffr ON ffc.reward_id=ffr.id
           WHERE ffr.category=? AND ffr.rarity=?
           ORDER BY ffc.claim_rank""",
        (category, rarity),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def disable_first_find_reward(category: str, rarity: str) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE first_find_rewards SET enabled=0, updated_at=datetime('now') WHERE category=? AND rarity=?",
        (category, rarity),
    )
    conn.commit()
    conn.close()


# ── First-find announce pending (cross-bot EmceeBot / BankerBot) ──────────────

def add_first_find_pending(reward_id: int, category: str, rarity: str,
                           username: str, user_id: str, claim_rank: int,
                           gold_amount: float,
                           emcee_msg: str, banker_msg: str) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT INTO first_find_announce_pending
               (reward_id, category, rarity, username, user_id, claim_rank,
                gold_amount, emcee_msg, banker_msg,
                emcee_done, banker_done, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, datetime('now'))""",
        (reward_id, category, rarity, username, user_id, claim_rank,
         gold_amount, emcee_msg, banker_msg),
    )
    conn.commit()
    conn.close()


def get_pending_firstfind_for_emcee(limit: int = 5) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM first_find_announce_pending "
        "WHERE emcee_done=0 ORDER BY id ASC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_firstfind_emcee_done(row_id: int) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE first_find_announce_pending SET emcee_done=1 WHERE id=?",
        (row_id,),
    )
    conn.commit()
    conn.close()


def get_pending_firstfind_for_banker(limit: int = 5) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM first_find_announce_pending "
        "WHERE banker_done=0 ORDER BY id ASC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_firstfind_banker_done(row_id: int) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE first_find_announce_pending SET banker_done=1 WHERE id=?",
        (row_id,),
    )
    conn.commit()
    conn.close()


# ── First-find race system ────────────────────────────────────────────────────

def create_first_find_race(
    category: str, target_type: str, target_value: str,
    winners_count: int, gold_amount: float, created_by: str = "system",
) -> int:
    """Cancel any draft/active race, then create a new draft. Returns new race id."""
    conn = get_connection()
    conn.execute(
        "UPDATE first_find_races SET status='stopped', updated_at=datetime('now') "
        "WHERE status IN ('draft', 'active')"
    )
    race_id = conn.execute(
        """INSERT INTO first_find_races
           (status, category, target_type, target_value, winners_count,
            gold_amount, created_by, created_at, updated_at)
           VALUES ('draft', ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
        (category, target_type, target_value, winners_count, gold_amount, created_by),
    ).lastrowid
    conn.commit()
    conn.close()
    return race_id


def start_first_find_race(race_id: int, minutes: int) -> None:
    """Activate a draft race with a time limit (SQLite datetime arithmetic)."""
    conn = get_connection()
    conn.execute(
        """UPDATE first_find_races
           SET status='active',
               started_at=datetime('now'),
               ends_at=datetime('now', ? || ' minutes'),
               updated_at=datetime('now')
           WHERE id=? AND status='draft'""",
        (str(minutes), race_id),
    )
    conn.commit()
    conn.close()


def stop_first_find_race(race_id: int, new_status: str = "stopped") -> None:
    """Mark a race stopped / completed / expired."""
    conn = get_connection()
    conn.execute(
        "UPDATE first_find_races SET status=?, updated_at=datetime('now') WHERE id=?",
        (new_status, race_id),
    )
    conn.commit()
    conn.close()


def get_active_first_find_race():
    """Return the currently active race row as dict, or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM first_find_races WHERE status='active' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_draft_first_find_race():
    """Return the most recent draft race row as dict, or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM first_find_races WHERE status='draft' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_latest_first_find_race():
    """Return the most recent race row (any status) as dict, or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM first_find_races ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def expire_first_find_races() -> list:
    """Find active races past ends_at, mark expired, return them."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM first_find_races
           WHERE status='active' AND ends_at IS NOT NULL
             AND datetime('now') >= datetime(ends_at)"""
    ).fetchall()
    for r in rows:
        conn.execute(
            "UPDATE first_find_races SET status='expired', updated_at=datetime('now') WHERE id=?",
            (r["id"],),
        )
    conn.commit()
    conn.close()
    return [dict(r) for r in rows]


def add_first_find_race_winner(
    race_id: int, user_id: str, username: str, rank: int,
    category: str, target_type: str, target_value: str,
    matched_item_name: str, matched_rarity: str, gold_amount: float,
) -> int:
    """Insert a race winner. Returns the new row id, or 0 on duplicate."""
    conn = get_connection()
    try:
        winner_id = conn.execute(
            """INSERT INTO first_find_race_winners
               (race_id, user_id, username, rank, category, target_type,
                target_value, matched_item_name, matched_rarity, gold_amount,
                payout_status, won_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_manual_gold', datetime('now'))""",
            (race_id, user_id, username, rank, category, target_type,
             target_value, matched_item_name, matched_rarity, gold_amount),
        ).lastrowid
        conn.commit()
        return winner_id
    except Exception:
        return 0
    finally:
        conn.close()


def has_first_find_race_winner(race_id: int, user_id: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM first_find_race_winners WHERE race_id=? AND user_id=?",
        (race_id, user_id),
    ).fetchone()
    conn.close()
    return row is not None


def count_first_find_race_winners(race_id: int) -> int:
    conn = get_connection()
    n = conn.execute(
        "SELECT COUNT(*) FROM first_find_race_winners WHERE race_id=?", (race_id,)
    ).fetchone()[0]
    conn.close()
    return n


def get_first_find_race_winners(race_id: int) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM first_find_race_winners WHERE race_id=? ORDER BY rank ASC",
        (race_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_race_winner_by_race_user(race_id: int, user_id: str):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM first_find_race_winners WHERE race_id=? AND user_id=?",
        (race_id, user_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_race_winner_payout(winner_id: int, payout_status: str, payout_error: str = "") -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE first_find_race_winners SET payout_status=?, payout_error=? WHERE id=?",
        (payout_status, payout_error, winner_id),
    )
    conn.commit()
    conn.close()


def get_pending_race_winners_for_banker(limit: int = 10) -> list:
    """Winners with pending_manual_gold payout, joined with race info."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT ffrw.*, ffr.category AS race_category,
                  ffr.target_value AS race_target,
                  ffr.target_type AS race_target_type
           FROM first_find_race_winners ffrw
           JOIN first_find_races ffr ON ffrw.race_id=ffr.id
           WHERE ffrw.payout_status='pending_manual_gold'
             AND ffrw.gold_amount > 0
           ORDER BY ffrw.won_at ASC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def reset_first_find_race() -> None:
    """Cancel any active or draft race."""
    conn = get_connection()
    conn.execute(
        "UPDATE first_find_races SET status='stopped', updated_at=datetime('now') "
        "WHERE status IN ('draft', 'active')"
    )
    conn.commit()
    conn.close()


def update_first_find_claim_payout_status(reward_id: int, user_id: str, status: str) -> None:
    """Update reward_status on a first_find_claims row after payout attempt."""
    conn = get_connection()
    conn.execute(
        "UPDATE first_find_claims SET reward_status=? WHERE reward_id=? AND user_id=?",
        (status, reward_id, user_id),
    )
    conn.commit()
    conn.close()


def get_first_find_pending_manual() -> list[dict]:
    """Return all first_find_claims with pending_manual_gold status, with reward info."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT ffc.reward_id, ffc.user_id, ffc.username, ffc.category, ffc.rarity,
                  ffc.claim_rank, ffr.gold_amount
           FROM first_find_claims ffc
           JOIN first_find_rewards ffr ON ffc.reward_id=ffr.id
           WHERE ffc.reward_status='pending_manual_gold'
           ORDER BY ffc.claimed_at ASC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Fishing forced drops ──────────────────────────────────────────────────────

def set_forced_fish_drop(
    target_username: str,
    forced_type: str,
    forced_value: str,
    created_by: str,
    expires_hours: int = 24,
    target_user_id: str = "",
) -> int:
    """Insert a pending forced fishing drop. forced_type: 'rarity' or 'fish'."""
    conn = get_connection()
    conn.execute(
        "UPDATE forced_fishing_drops SET status='cleared' "
        "WHERE lower(target_username)=lower(?) AND status='pending'",
        (target_username,),
    )
    cur = conn.execute(
        "INSERT INTO forced_fishing_drops "
        "(target_user_id, target_username, forced_type, forced_value, created_by, "
        " expires_at, status) "
        "VALUES (?, lower(?), ?, lower(?), lower(?), "
        " datetime('now', '+' || ? || ' hours'), 'pending')",
        (target_user_id, target_username, forced_type, forced_value, created_by, expires_hours),
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_active_forced_fish_drop(target_user_id: str = "",
                                target_username: str = "") -> dict | None:
    """Return the oldest pending, non-expired forced fish drop for this player."""
    conn = get_connection()
    row = None
    if target_user_id:
        row = conn.execute(
            "SELECT * FROM forced_fishing_drops "
            "WHERE target_user_id=? AND status='pending' "
            "  AND (expires_at='' OR expires_at > datetime('now')) "
            "ORDER BY id ASC LIMIT 1",
            (target_user_id,),
        ).fetchone()
    if not row and target_username:
        row = conn.execute(
            "SELECT * FROM forced_fishing_drops "
            "WHERE lower(target_username)=lower(?) AND status='pending' "
            "  AND (expires_at='' OR expires_at > datetime('now')) "
            "ORDER BY id ASC LIMIT 1",
            (target_username,),
        ).fetchone()
    conn.close()
    return dict(row) if row else None


def mark_forced_fish_drop_used(drop_id: int) -> None:
    """Mark a forced fish drop as used."""
    conn = get_connection()
    conn.execute(
        "UPDATE forced_fishing_drops "
        "SET status='used', used_at=datetime('now') WHERE id=?",
        (drop_id,),
    )
    conn.commit()
    conn.close()


def set_forced_fish_drop_error(drop_id: int, error_msg: str) -> None:
    """Record an error message on a forced fish drop without consuming it."""
    conn = get_connection()
    conn.execute(
        "UPDATE forced_fishing_drops SET last_error=? WHERE id=?",
        (error_msg[:200], drop_id),
    )
    conn.commit()
    conn.close()


def clear_forced_fish_drop_by_username(target_username: str, cleared_by: str) -> int:
    """Clear all pending forced fish drops for target_username. Returns rows affected."""
    conn = get_connection()
    cur  = conn.execute(
        "UPDATE forced_fishing_drops SET status='cleared' "
        "WHERE lower(target_username)=lower(?) AND status='pending'",
        (target_username,),
    )
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n


def get_all_active_forced_fish_drops() -> list:
    """Return all pending, non-expired forced fish drops ordered by creation time."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM forced_fishing_drops "
        "WHERE status='pending' "
        "  AND (expires_at='' OR expires_at > datetime('now')) "
        "ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Big announcement settings ─────────────────────────────────────────────────

def get_big_announce_setting(category: str, rarity: str) -> dict | None:
    """Return the big_announcement_settings row for category+rarity."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM big_announcement_settings WHERE category=? AND rarity=?",
        (category, rarity),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def set_big_announce_setting(category: str, rarity: str, routing_mode: str) -> None:
    """Upsert the routing mode for a category+rarity pair."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO big_announcement_settings (category, rarity, routing_mode, updated_at) "
        "VALUES (?,?,?,datetime('now')) "
        "ON CONFLICT(category, rarity) DO UPDATE SET "
        "routing_mode=excluded.routing_mode, updated_at=excluded.updated_at",
        (category, rarity, routing_mode),
    )
    conn.commit()
    conn.close()


def get_big_announce_bot_reaction(bot_name: str) -> bool:
    """Return True if bot_name is enabled for big announce reactions."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT enabled FROM big_announcement_bot_reactions WHERE bot_name=?",
        (bot_name.lower(),),
    ).fetchone()
    conn.close()
    if row is None:
        return True  # default ON if not in table
    return bool(row[0])


def set_big_announce_bot_reaction(bot_name: str, enabled: int) -> None:
    """Upsert bot reaction enabled flag."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO big_announcement_bot_reactions (bot_name, enabled, updated_at) "
        "VALUES (?,?,datetime('now')) "
        "ON CONFLICT(bot_name) DO UPDATE SET "
        "enabled=excluded.enabled, updated_at=excluded.updated_at",
        (bot_name.lower(), enabled),
    )
    conn.commit()
    conn.close()


def get_all_big_announce_bot_reactions() -> list:
    """Return all bot reaction rows."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM big_announcement_bot_reactions ORDER BY bot_name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_big_announce_pending(category: str, rarity: str, item_name: str,
                              user_id: str, username: str,
                              weight_str: str = "", value_str: str = "",
                              xp_str: str = "", item_emoji: str = "") -> int:
    """Write a pending big announcement for reaction polling by other bots."""
    conn = get_connection()
    cur  = conn.execute(
        "INSERT INTO big_announcement_logs "
        "(category, rarity, item_name, user_id, username, routing_mode, status,"
        " weight_str, value_str, xp_str, item_emoji) "
        "VALUES (?,?,?,?,?,'all_bots','pending',?,?,?,?)",
        (category, rarity, item_name, user_id, username,
         weight_str, value_str, xp_str, item_emoji),
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_pending_big_announce_reactions(bot_friendly: str) -> list:
    """
    Return big_announcement_logs entries from the last 10 minutes that:
    - are 'pending' or have reacted_bots not containing this bot
    - were created recently (no stale reactions)
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM big_announcement_logs "
        "WHERE status='pending' "
        "  AND created_at > datetime('now', '-10 minutes') "
        "  AND (',' || reacted_bots || ',') NOT LIKE ? "
        "ORDER BY id ASC LIMIT 10",
        (f"%,{bot_friendly.lower()},%",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_big_announce_reacted(log_id: int, bot_friendly: str) -> None:
    """Add bot_friendly to reacted_bots for the given log entry."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT reacted_bots FROM big_announcement_logs WHERE id=?", (log_id,)
    ).fetchone()
    if row is None:
        conn.close()
        return
    existing = row[0] or ""
    names = [n for n in existing.split(",") if n]
    fn = bot_friendly.lower()
    if fn not in names:
        names.append(fn)
    new_val = ",".join(names)
    conn.execute(
        "UPDATE big_announcement_logs SET reacted_bots=? WHERE id=?",
        (new_val, log_id),
    )
    conn.commit()
    conn.close()


# ── Staff audit log ────────────────────────────────────────────────────────────

def log_staff_action(
    actor_user_id: str,
    actor_username: str,
    action_type: str,
    target_user_id: str,
    target_username: str,
    details: str,
) -> None:
    """Write a staff action to staff_audit_logs."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO staff_audit_logs
           (actor_user_id, actor_username, action_type,
            target_user_id, target_username, details)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (actor_user_id, actor_username, action_type,
         target_user_id, target_username, details),
    )
    conn.commit()
    conn.close()


def get_staff_audit_logs(action_type: str | None = None, limit: int = 10) -> list:
    """Return recent staff audit log entries, optionally filtered by action_type."""
    conn = get_connection()
    if action_type:
        rows = conn.execute(
            "SELECT * FROM staff_audit_logs "
            "WHERE action_type=? ORDER BY created_at DESC LIMIT ?",
            (action_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM staff_audit_logs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Economy report ─────────────────────────────────────────────────────────────

def get_economy_report_today() -> dict:
    """Return a snapshot of today's economy activity from available logs."""
    conn = get_connection()

    fish_earned = conn.execute(
        "SELECT COALESCE(SUM(final_value), 0) FROM fish_catch_records "
        "WHERE DATE(caught_at) = DATE('now')"
    ).fetchone()[0] or 0

    gold_converted = conn.execute(
        "SELECT COALESCE(SUM(gold_amount), 0) FROM gold_tip_events "
        "WHERE DATE(created_at) = DATE('now')"
    ).fetchone()[0] or 0

    race_gold_today = conn.execute(
        "SELECT COALESCE(SUM(gold_amount), 0) FROM first_find_race_winners "
        "WHERE DATE(won_at) = DATE('now')"
    ).fetchone()[0] or 0

    p2p_transfers = conn.execute(
        "SELECT COUNT(*) FROM bank_transactions "
        "WHERE DATE(timestamp) = DATE('now')"
    ).fetchone()[0] or 0

    pending_count = conn.execute(
        "SELECT COUNT(*) FROM first_find_race_winners "
        "WHERE payout_status='pending_manual_gold'"
    ).fetchone()[0] or 0

    conn.close()
    return {
        "fish_earned_today":     fish_earned,
        "gold_converted_today":  gold_converted,
        "race_gold_today":       race_gold_today,
        "p2p_transfers_today":   p2p_transfers,
        "pending_gold_count":    pending_count,
    }


# ── Weekly leaderboard ─────────────────────────────────────────────────────────

def get_weekly_leaderboard_data(week_start: str) -> dict:
    """Compute current week's leaders per category from available tables."""
    result: dict = {}
    conn = get_connection()

    # Top Fisher — fish_catch_records (has caught_at)
    try:
        row = conn.execute(
            """SELECT user_id, username, COUNT(*) AS cnt
               FROM fish_catch_records
               WHERE DATE(caught_at) >= DATE(?)
               GROUP BY user_id ORDER BY cnt DESC LIMIT 1""",
            (week_start,),
        ).fetchone()
        if row:
            result["fisher"] = {
                "user_id":  row["user_id"],
                "username": row["username"],
                "score":    f"{row['cnt']} catches",
            }
    except Exception:
        pass

    # Top Race Winner — first_find_race_winners (has won_at)
    try:
        row = conn.execute(
            """SELECT user_id, username, COUNT(*) AS cnt
               FROM first_find_race_winners
               WHERE DATE(won_at) >= DATE(?)
               GROUP BY user_id ORDER BY cnt DESC LIMIT 1""",
            (week_start,),
        ).fetchone()
        if row:
            result["racer"] = {
                "user_id":  row["user_id"],
                "username": row["username"],
                "score":    f"{row['cnt']} wins",
            }
    except Exception:
        pass

    # Top Tipper — gold_tip_events (has created_at)
    try:
        row = conn.execute(
            """SELECT from_user_id AS user_id, from_username AS username,
                      SUM(gold_amount) AS total_g
               FROM gold_tip_events
               WHERE DATE(created_at) >= DATE(?)
               GROUP BY from_user_id ORDER BY total_g DESC LIMIT 1""",
            (week_start,),
        ).fetchone()
        if row:
            result["tipper"] = {
                "user_id":  row["user_id"],
                "username": row["username"],
                "score":    f"{row['total_g']:g}g",
            }
    except Exception:
        pass

    # Top Coin Earner — bank_transactions this week
    try:
        row = conn.execute(
            """SELECT receiver_id AS user_id, receiver_username AS username,
                      SUM(amount_received) AS total_r
               FROM bank_transactions
               WHERE DATE(timestamp) >= DATE(?)
               GROUP BY receiver_id ORDER BY total_r DESC LIMIT 1""",
            (week_start,),
        ).fetchone()
        if row:
            result["earner"] = {
                "user_id":  row["user_id"],
                "username": row["username"],
                "score":    f"{int(row['total_r']):,}c",
            }
    except Exception:
        pass

    # Top Miner — mining_players all-time (no weekly filter available)
    try:
        row = conn.execute(
            "SELECT username, total_mines FROM mining_players "
            "ORDER BY total_mines DESC LIMIT 1"
        ).fetchone()
        if row:
            result["miner"] = {
                "user_id":  "",
                "username": row["username"],
                "score":    f"{row['total_mines']} mines",
            }
    except Exception:
        pass

    conn.close()
    return result


def save_weekly_snapshot(
    week_start: str,
    week_end: str,
    category: str,
    rank: int,
    user_id: str,
    username: str,
    score: str,
) -> None:
    """Archive a weekly winner snapshot."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO weekly_leaderboard_snapshots
           (week_start, week_end, category, rank, user_id, username, score)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (week_start, week_end, category, rank, user_id, username, score),
    )
    conn.commit()
    conn.close()


def get_latest_weekly_snapshot() -> dict | None:
    """Return the most recent weekly snapshot row."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM weekly_leaderboard_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def set_weekly_reward(
    category: str,
    rank: int,
    reward_type: str,
    amount: int,
) -> None:
    """Upsert a weekly reward config entry."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO weekly_rewards (category, rank, reward_type, reward_amount, enabled)
           VALUES (?, ?, ?, ?, 1)
           ON CONFLICT(category, rank) DO UPDATE SET
               reward_type=excluded.reward_type,
               reward_amount=excluded.reward_amount,
               enabled=1""",
        (category, rank, reward_type, amount),
    )
    conn.commit()
    conn.close()


def get_weekly_rewards() -> list:
    """Return all configured weekly reward rows."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM weekly_rewards ORDER BY category, rank"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Race winner reward helpers ─────────────────────────────────────────────────

def mark_race_winner_paid_manual(winner_id: int, paid_by: str) -> bool:
    """Mark a first_find_race_winners row as paid_manual. Returns True if found."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id, payout_status FROM first_find_race_winners WHERE id=?",
        (winner_id,),
    ).fetchone()
    if not row:
        conn.close()
        return False
    conn.execute(
        "UPDATE first_find_race_winners "
        "SET payout_status='paid_manual', payout_error=? WHERE id=?",
        (f"paid_by:{paid_by}", winner_id),
    )
    conn.commit()
    conn.close()
    return True


def get_pending_race_winners_by_username(username: str, limit: int = 10) -> list:
    """Return pending_manual_gold rows for a given username."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM first_find_race_winners
           WHERE LOWER(username)=? AND payout_status='pending_manual_gold'
           ORDER BY won_at DESC LIMIT ?""",
        (username.lower(), limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_race_winners(limit: int = 10) -> list:
    """Return most recent race winner rows ordered by won_at DESC."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM first_find_race_winners ORDER BY won_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_race_wins_count(user_id: str) -> int:
    """Return total first-find race wins for a user."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) FROM first_find_race_winners WHERE user_id=?",
        (user_id,),
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def get_recent_race_winners_for_user(user_id: str, limit: int = 5) -> list:
    """Return recent race winner rows for a specific user."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM first_find_race_winners WHERE user_id=? ORDER BY won_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Suggestions ────────────────────────────────────────────────────────────────

def add_suggestion(user_id: str, username: str, message: str) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO suggestions (user_id, username, message) VALUES (?, ?, ?)",
        (user_id, username, message),
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid or 0


def get_suggestions(status: str | None = None, limit: int = 10) -> list:
    conn = get_connection()
    if status:
        rows = conn.execute(
            "SELECT * FROM suggestions WHERE status=? ORDER BY id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM suggestions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Bug reports ────────────────────────────────────────────────────────────────

def add_bug_report(user_id: str, username: str, message: str) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO bug_reports (user_id, username, message) VALUES (?, ?, ?)",
        (user_id, username, message),
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid or 0


def get_bug_reports(status: str | None = None, limit: int = 10) -> list:
    conn = get_connection()
    if status:
        rows = conn.execute(
            "SELECT * FROM bug_reports WHERE status=? ORDER BY id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM bug_reports ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Event votes ────────────────────────────────────────────────────────────────

def cast_event_vote(user_id: str, username: str, choice: str) -> bool:
    """Insert or replace a vote. Returns True if new, False if changed."""
    conn = get_connection()
    existing = conn.execute(
        "SELECT choice FROM event_votes WHERE user_id=?", (user_id,)
    ).fetchone()
    conn.execute(
        """INSERT INTO event_votes (user_id, username, choice)
           VALUES (?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET choice=excluded.choice,
           created_at=datetime('now')""",
        (user_id, username, choice),
    )
    conn.commit()
    conn.close()
    return existing is None


def get_event_vote_counts() -> dict:
    conn = get_connection()
    rows = conn.execute(
        "SELECT choice, COUNT(*) AS cnt FROM event_votes GROUP BY choice ORDER BY cnt DESC"
    ).fetchall()
    conn.close()
    return {r["choice"]: r["cnt"] for r in rows}


def clear_event_votes() -> int:
    conn = get_connection()
    cur = conn.execute("DELETE FROM event_votes")
    count = cur.rowcount
    conn.commit()
    conn.close()
    return count


# ── Fish inventory ─────────────────────────────────────────────────────────────

def save_fish_to_inventory(
    user_id: str, username: str,
    fish_name: str, rarity: str,
    weight: float, value: int,
    sold: int = 0,
) -> int:
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO fish_inventory
           (user_id, username, fish_name, rarity, weight, value, sold)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, username, fish_name, rarity, weight, value, sold),
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid or 0


def get_fish_inventory(user_id: str, limit: int = 20, sold: int = 0) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM fish_inventory WHERE user_id=? AND sold=? ORDER BY id DESC LIMIT ?",
        (user_id, sold, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def sell_all_fish_inventory(user_id: str) -> tuple[int, int]:
    """Mark all unsold fish as sold. Returns (count, total_coins)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, value FROM fish_inventory WHERE user_id=? AND sold=0",
        (user_id,),
    ).fetchall()
    total = sum(r["value"] for r in rows)
    count = len(rows)
    if count:
        conn.execute(
            "UPDATE fish_inventory SET sold=1, sold_at=datetime('now') "
            "WHERE user_id=? AND sold=0",
            (user_id,),
        )
        conn.commit()
    conn.close()
    return count, total


def sell_fish_inventory_by_rarity(user_id: str, rarity: str) -> tuple[int, int]:
    """Mark all unsold fish of a rarity as sold. Returns (count, total_coins)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, value FROM fish_inventory WHERE user_id=? AND rarity=? AND sold=0",
        (user_id, rarity),
    ).fetchall()
    total = sum(r["value"] for r in rows)
    count = len(rows)
    if count:
        conn.execute(
            "UPDATE fish_inventory SET sold=1, sold_at=datetime('now') "
            "WHERE user_id=? AND rarity=? AND sold=0",
            (user_id, rarity),
        )
        conn.commit()
    conn.close()
    return count, total


def get_fish_auto_sell_settings(user_id: str) -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM fish_auto_sell_settings WHERE user_id=?", (user_id,)
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"auto_sell_enabled": 1, "auto_sell_rare_enabled": 0}


def set_fish_auto_sell(user_id: str, username: str, enabled: int) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT INTO fish_auto_sell_settings (user_id, username, auto_sell_enabled)
           VALUES (?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
               auto_sell_enabled=excluded.auto_sell_enabled,
               updated_at=datetime('now')""",
        (user_id, username, enabled),
    )
    conn.commit()
    conn.close()


def set_fish_auto_sell_rare(user_id: str, username: str, enabled: int) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT INTO fish_auto_sell_settings (user_id, username, auto_sell_rare_enabled)
           VALUES (?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
               auto_sell_rare_enabled=excluded.auto_sell_rare_enabled,
               updated_at=datetime('now')""",
        (user_id, username, enabled),
    )
    conn.commit()
    conn.close()


def get_fish_book(user_id: str) -> list:
    """Return distinct fish species caught by user (from fish_inventory)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT fish_name, rarity, MAX(weight) AS best_weight,
                  MAX(value) AS best_value, COUNT(*) AS total_caught
           FROM fish_inventory WHERE user_id=?
           GROUP BY fish_name, rarity ORDER BY rarity, fish_name""",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Collection book (3.1H) ────────────────────────────────────────────────────

def record_collection_item(user_id: str, username: str, ctype: str,
                           item_key: str, item_name: str, rarity: str,
                           value: int = 0) -> bool:
    """UPSERT a discovered item. Returns True on first discovery."""
    conn = get_connection()
    existing = conn.execute(
        "SELECT id FROM player_collection "
        "WHERE user_id=? AND collection_type=? AND item_key=?",
        (user_id, ctype, item_key),
    ).fetchone()
    is_new = existing is None
    if is_new:
        conn.execute(
            """INSERT INTO player_collection
               (user_id, username, collection_type, item_key, item_name, rarity,
                first_seen_at, last_seen_at, count, best_value)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), 1, ?)""",
            (user_id, username, ctype, item_key, item_name, rarity, value),
        )
    else:
        conn.execute(
            """UPDATE player_collection
               SET count=count+1,
                   last_seen_at=datetime('now'),
                   best_value=MAX(best_value, ?),
                   username=?
               WHERE user_id=? AND collection_type=? AND item_key=?""",
            (value, username, user_id, ctype, item_key),
        )
    conn.commit()
    conn.close()
    return is_new


def get_player_collection(user_id: str, ctype: str,
                          rarity: str | None = None) -> list:
    """Return discovered items for a player in a collection type."""
    conn = get_connection()
    if rarity:
        rows = conn.execute(
            """SELECT * FROM player_collection
               WHERE user_id=? AND collection_type=? AND rarity=?
               ORDER BY rarity, item_name""",
            (user_id, ctype, rarity),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM player_collection
               WHERE user_id=? AND collection_type=?
               ORDER BY rarity, item_name""",
            (user_id, ctype),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_collection_counts(user_id: str) -> dict:
    """Return {collection_type: discovered_count} for a player."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT collection_type, COUNT(*) AS cnt
           FROM player_collection WHERE user_id=?
           GROUP BY collection_type""",
        (user_id,),
    ).fetchall()
    conn.close()
    result: dict = {"mining": 0, "fishing": 0}
    for r in rows:
        result[r["collection_type"]] = r["cnt"]
    return result


def count_collection_items(user_id: str, ctype: str) -> int:
    """Return count of distinct discovered items for a player in a type."""
    conn = get_connection()
    n = conn.execute(
        "SELECT COUNT(*) FROM player_collection WHERE user_id=? AND collection_type=?",
        (user_id, ctype),
    ).fetchone()[0]
    conn.close()
    return n


def get_top_collectors(ctype: str | None = None, limit: int = 5) -> list:
    """Leaderboard by unique item discoveries, bots excluded."""
    bot_filter = _get_bot_name_filter()
    conn       = get_connection()
    if ctype:
        rows = conn.execute(
            """SELECT user_id, username, COUNT(*) AS disc
               FROM player_collection WHERE collection_type=?
               GROUP BY user_id ORDER BY disc DESC LIMIT ?""",
            (ctype, limit + 25),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT user_id, username, COUNT(*) AS disc
               FROM player_collection
               GROUP BY user_id ORDER BY disc DESC LIMIT ?""",
            (limit + 25,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows
            if r["username"].lower() not in bot_filter][:limit]


def get_rare_finds_collection(user_id: str, ctype: str | None = None) -> list:
    """Return rare+ collection items for a player."""
    _rare = "('rare','epic','legendary','mythic','ultra_rare','prismatic','exotic')"
    conn  = get_connection()
    if ctype:
        rows = conn.execute(
            f"""SELECT * FROM player_collection
                WHERE user_id=? AND collection_type=?
                  AND rarity IN {_rare}
                ORDER BY rarity, item_name""",
            (user_id, ctype),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""SELECT * FROM player_collection
                WHERE user_id=? AND rarity IN {_rare}
                ORDER BY collection_type, rarity, item_name""",
            (user_id,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_active_room_boosts(target_system: str) -> list[dict]:
    """Return all non-expired room boosts for a target system (mining/fishing)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM room_active_boosts
           WHERE target_system=? AND expires_at > datetime('now')
           ORDER BY created_at""",
        (target_system,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_room_boost(target_system: str, boost_type: str, amount: float,
                   expires_at: str, source: str, created_by: str) -> int:
    """Insert a new room-wide boost. Returns the new row id."""
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO room_active_boosts
               (target_system, boost_type, amount, expires_at, source, created_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (target_system, boost_type, amount, expires_at, source, created_by),
    )
    rowid = cur.lastrowid
    conn.commit()
    conn.close()
    return rowid


def remove_room_boosts(target_system: str) -> int:
    """Delete all room boosts for a system. Returns number deleted."""
    conn = get_connection()
    cur = conn.execute(
        "DELETE FROM room_active_boosts WHERE target_system=?",
        (target_system,),
    )
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n


def get_active_player_boosts(user_id: str, target_system: str) -> list[dict]:
    """Return all non-expired player boosts for a given system."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM player_active_boosts
           WHERE user_id=? AND target_system=? AND expires_at > datetime('now')
           ORDER BY created_at""",
        (user_id, target_system),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_player_boost(user_id: str, username: str, boost_type: str,
                     target_system: str, amount: float,
                     expires_at: str, source: str) -> int:
    """Insert a new player boost. Returns the new row id."""
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO player_active_boosts
               (user_id, username, boost_type, target_system, amount, expires_at, source)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, username, boost_type, target_system, amount, expires_at, source),
    )
    rowid = cur.lastrowid
    conn.commit()
    conn.close()
    return rowid


def save_player_dm_conv(user_id: str, username: str,
                        conversation_id: str, bot_name: str) -> None:
    """Upsert a player's inbox conversation_id for a specific bot."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO player_dm_conversations
               (user_id, username, conversation_id, bot_name, stale, updated_at)
           VALUES (?, ?, ?, ?, 0, datetime('now'))
           ON CONFLICT(user_id, bot_name) DO UPDATE SET
               conversation_id = excluded.conversation_id,
               username        = excluded.username,
               stale           = 0,
               updated_at      = datetime('now')""",
        (user_id, username, conversation_id, bot_name),
    )
    conn.commit()
    conn.close()


def get_player_dm_conv(user_id: str) -> dict | None:
    """Return the most recently updated non-stale DM conversation_id for a player."""
    conn = get_connection()
    row = conn.execute(
        """SELECT * FROM player_dm_conversations
           WHERE user_id=? AND stale=0
           ORDER BY updated_at DESC LIMIT 1""",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def mark_player_dm_conv_stale(user_id: str, conversation_id: str) -> None:
    """Mark a specific conversation_id as stale (delivery failed)."""
    conn = get_connection()
    conn.execute(
        """UPDATE player_dm_conversations SET stale=1, updated_at=datetime('now')
           WHERE user_id=? AND conversation_id=?""",
        (user_id, conversation_id),
    )
    conn.commit()
    conn.close()


def backfill_fishing_collection(user_id: str, username: str) -> int:
    """
    Seed player_collection(fishing) from existing fish_catch_records.
    Uses fish_name → normalised item_key (lowercase + underscores).
    Returns the number of new collection rows inserted.
    """
    conn  = get_connection()
    rows  = conn.execute(
        """SELECT fish_name, rarity,
                  COUNT(*)        AS cnt,
                  MAX(final_value) AS best_val
           FROM fish_catch_records
           WHERE user_id = ?
           GROUP BY fish_name, rarity""",
        (user_id,),
    ).fetchall()
    added = 0
    for r in rows:
        item_key = r["fish_name"].lower().replace(" ", "_")
        existing = conn.execute(
            "SELECT id FROM player_collection "
            "WHERE user_id=? AND collection_type='fishing' AND item_key=?",
            (user_id, item_key),
        ).fetchone()
        if not existing:
            conn.execute(
                """INSERT INTO player_collection
                   (user_id, username, collection_type, item_key, item_name, rarity,
                    first_seen_at, last_seen_at, count, best_value)
                   VALUES (?, ?, 'fishing', ?, ?, ?, datetime('now'), datetime('now'), ?, ?)""",
                (user_id, username, item_key, r["fish_name"],
                 r["rarity"] or "common", r["cnt"], r["best_val"] or 0),
            )
            added += 1
    if added:
        conn.commit()
    conn.close()
    return added


def get_mining_totals_by_rarity() -> dict:
    """Return {rarity: count} of all mining items in the catalog."""
    try:
        conn  = get_connection()
        rows  = conn.execute(
            "SELECT rarity, COUNT(*) AS n FROM mining_items GROUP BY rarity"
        ).fetchall()
        conn.close()
        return {r["rarity"]: r["n"] for r in rows}
    except Exception:
        return {}


def save_auto_session_summary(user_id: str, username: str, stype: str, text: str) -> None:
    """Save (or replace) an auto-session summary for a player. stype: 'mining' or 'fishing'."""
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO auto_session_summaries
                   (user_id, username, summary_type, summary_text, created_at)
               VALUES (?, ?, ?, ?, datetime('now'))
               ON CONFLICT(user_id, summary_type) DO UPDATE SET
                   summary_text = excluded.summary_text,
                   created_at   = excluded.created_at""",
            (user_id, username.lower(), stype, text),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_auto_session_summary(user_id: str, stype: str) -> str:
    """Retrieve the last saved auto-session summary text. Returns '' if none."""
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT summary_text FROM auto_session_summaries WHERE user_id=? AND summary_type=?",
            (user_id, stype),
        ).fetchone()
        conn.close()
        return row["summary_text"] if row else ""
    except Exception:
        return ""


# ── Subscriber notification preferences ───────────────────────────────────────

def get_sub_notif_prefs(user_id: str) -> dict:
    """Return {category: enabled} for a user."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT category, enabled FROM subscriber_notification_prefs WHERE user_id=?",
        (user_id,),
    ).fetchall()
    conn.close()
    return {r["category"]: r["enabled"] for r in rows}


def set_sub_notif_pref(user_id: str, username: str, category: str, enabled: int) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT INTO subscriber_notification_prefs (user_id, username, category, enabled)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(user_id, category) DO UPDATE SET
               enabled=excluded.enabled,
               updated_at=datetime('now')""",
        (user_id, username, category, enabled),
    )
    conn.commit()
    conn.close()


def set_sub_notif_all_prefs(user_id: str, username: str, enabled: int) -> None:
    """Set all known categories for a user to enabled/disabled."""
    from modules.sub_notif import NOTIF_CATEGORIES
    conn = get_connection()
    for cat in NOTIF_CATEGORIES:
        conn.execute(
            """INSERT INTO subscriber_notification_prefs (user_id, username, category, enabled)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, category) DO UPDATE SET
                   enabled=excluded.enabled,
                   updated_at=datetime('now')""",
            (user_id, username, cat, enabled),
        )
    conn.commit()
    conn.close()


def get_subscribers_opted_into_category(category: str) -> list:
    """Return list of {user_id, username} opted into category (enabled=1)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT user_id, username FROM subscriber_notification_prefs "
        "WHERE category=? AND enabled=1",
        (category,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def log_sub_notification(
    category: str, message: str,
    sender_user_id: str, sender_username: str,
    sent: int, skipped: int, no_conv: int, failed: int,
) -> int:
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO subscriber_notification_logs
           (category, message, sender_user_id, sender_username,
            sent_count, skipped_count, no_conversation_count, failed_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (category, message, sender_user_id, sender_username,
         sent, skipped, no_conv, failed),
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid or 0


def update_sub_notif_log(
    log_id: int, sent: int, skipped: int, no_conv: int, failed: int
) -> None:
    conn = get_connection()
    conn.execute(
        """UPDATE subscriber_notification_logs SET
               sent_count=?, skipped_count=?,
               no_conversation_count=?, failed_count=?
           WHERE id=?""",
        (sent, skipped, no_conv, failed, log_id),
    )
    conn.commit()
    conn.close()


def log_sub_notif_recipient(
    notification_id: int, user_id: str, username: str,
    category: str, status: str, error: str,
) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT INTO subscriber_notification_recipients
           (notification_id, user_id, username, category, status, error)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (notification_id, user_id, username, category, status, error),
    )
    conn.commit()
    conn.close()


def get_sub_notif_logs(limit: int = 10) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM subscriber_notification_logs ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_sub_notif_logs_by_method(limit: int = 5, method: str = "whisper") -> list:
    """Return recent notification logs filtered by delivery method (via recipients)."""
    conn = get_connection()
    if method == "whisper":
        rows = conn.execute(
            """SELECT DISTINCT l.* FROM subscriber_notification_logs l
               JOIN subscriber_notification_recipients r ON r.notification_id = l.id
               WHERE r.delivery_method = 'whisper' AND r.status = 'sent_whisper_in_room'
               ORDER BY l.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT DISTINCT l.* FROM subscriber_notification_logs l
               JOIN subscriber_notification_recipients r ON r.notification_id = l.id
               WHERE r.delivery_method = 'dm'
               ORDER BY l.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Compatibility aliases ─────────────────────────────────────────────────────

def get_or_create_mine_profile(user_id: str, username: str) -> dict:
    """Alias wrapping get_or_create_miner for player_cmds compatibility."""
    rec = get_or_create_miner(username)
    # Normalize last_mine_at from mining_players.last_mine
    if "last_mine" in rec and "last_mine_at" not in rec:
        rec["last_mine_at"] = rec.get("last_mine")
    return rec


def get_daily_status(user_id: str) -> dict | None:
    """Return today's daily_claims row for user_id, with claimed_today bool."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM daily_claims WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    rec = dict(row)
    last = rec.get("last_claim") or rec.get("last_claim_ts") or ""
    today = __import__("datetime").date.today().isoformat()
    rec["claimed_today"] = last.startswith(today)
    return rec


# ── Subscriber notification global preferences ────────────────────────────────

def get_sub_notif_global(user_id: str) -> dict:
    """Return {global_enabled: 1/0} for a user. Defaults to 1 (enabled) if no record."""
    if not user_id:
        return {"global_enabled": 1}
    conn = get_connection()
    row = conn.execute(
        "SELECT global_enabled FROM subscriber_notification_global WHERE user_id=?",
        (user_id,),
    ).fetchone()
    conn.close()
    if row:
        return {"global_enabled": row["global_enabled"]}
    return {"global_enabled": 1}  # default ON


def set_sub_notif_global(user_id: str, username: str, enabled: int) -> None:
    """Upsert global notification preference for a user."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO subscriber_notification_global (user_id, username, global_enabled, updated_at)
           VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(user_id) DO UPDATE SET
               username=excluded.username,
               global_enabled=excluded.global_enabled,
               updated_at=datetime('now')""",
        (user_id, (username or "").lower(), enabled),
    )
    conn.commit()
    conn.close()


# ── Get all subscribed users for notification delivery ────────────────────────

def get_all_subscribed_users_for_notify() -> list[dict]:
    """Return all subscribed users (subscribed=1, user_id not empty) for notification delivery.
    Includes conversation_id so the delivery engine can attempt out-of-room DM.
    Does NOT filter on dm_available — the delivery engine handles in-room whisper first."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT user_id, username, conversation_id, dm_available
           FROM subscriber_users
           WHERE subscribed = 1
             AND user_id IS NOT NULL
             AND user_id != ''
           ORDER BY subscribed_at ASC""",
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Subscriber notification logging (v2) ──────────────────────────────────────

def log_sub_notification_v2(
    category: str,
    message: str,
    send_type: str,
    sender_user_id: str,
    sender_username: str,
    *,
    sender_bot_name: str = "",
    original_sender_bot_name: str = "",
    fallback_used: int = 0,
) -> int:
    """Create a notification log entry; return the new log_id."""
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO subscriber_notification_logs
           (category, message, send_type,
            sender_user_id, sender_username,
            sent_count, sent_dm_count, sent_whisper_count,
            skipped_count, no_conversation_count,
            unsupported_sdk_count, failed_count,
            sender_bot_name, original_sender_bot_name, fallback_used)
           VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, 0, ?, ?, ?)""",
        (category, message, send_type, sender_user_id, sender_username,
         sender_bot_name, original_sender_bot_name, fallback_used),
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid or 0


def update_sub_notif_log_v2(
    log_id: int,
    sent_whisper: int,
    sent_bulk_dm: int,
    sent_conv_dm: int,
    no_delivery: int,
    skipped: int,
    failed: int,
) -> None:
    """Update counts on an existing notification log row."""
    total_dm   = sent_bulk_dm + sent_conv_dm
    total_sent = sent_whisper + total_dm
    conn = get_connection()
    conn.execute(
        """UPDATE subscriber_notification_logs SET
               sent_count              = ?,
               sent_dm_count           = ?,
               sent_whisper_count      = ?,
               sent_bulk_dm_count      = ?,
               sent_conv_dm_count      = ?,
               skipped_count           = ?,
               no_conversation_count   = ?,
               no_delivery_route_count = ?,
               failed_count            = ?
           WHERE id = ?""",
        (total_sent, total_dm, sent_whisper,
         sent_bulk_dm, sent_conv_dm,
         skipped, no_delivery, no_delivery, failed, log_id),
    )
    conn.commit()
    conn.close()


def log_sub_notif_recipient_v2(
    notification_id: int,
    user_id: str,
    username: str,
    category: str,
    subscribed: int,
    category_enabled: int,
    global_enabled: int,
    delivery_method: str,
    status: str,
    error: str,
) -> None:
    """Log the delivery result for one recipient."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO subscriber_notification_recipients
           (notification_id, user_id, username, category,
            subscribed, category_enabled, global_enabled,
            delivery_method, status, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (notification_id, user_id, username, category,
         subscribed, category_enabled, global_enabled,
         delivery_method, status, error),
    )
    conn.commit()
    conn.close()


def get_sub_notif_no_conv_recipients(limit: int = 10) -> list[dict]:
    """Return recent recipients with status=no_conversation."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT DISTINCT user_id, username FROM subscriber_notification_recipients
           WHERE status = 'no_conversation'
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_sub_notif_failed_recipients(limit: int = 10) -> list[dict]:
    """Return recent recipients with status=failed."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT user_id, username, category, error, created_at
           FROM subscriber_notification_recipients
           WHERE status = 'failed'
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def close_event_history_as_skipped(history_id: int, skipped_by: str) -> None:
    """Mark an event_history row as skipped and record who skipped it."""
    conn = get_connection()
    conn.execute(
        "UPDATE event_history SET ended_at=datetime('now'), status='skipped', "
        "skipped_by=? WHERE id=?",
        ((skipped_by or "").lower(), history_id),
    )
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# QoL / Debug DB helpers
# ─────────────────────────────────────────────────────────────────────────────

# ── Player Feedback ───────────────────────────────────────────────────────────

def add_player_feedback(user_id: str, username: str, message: str) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO player_feedback (user_id, username, message) VALUES (?, ?, ?)",
        (user_id or "", (username or "").lower(), (message or "")[:200]),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id or 0


def get_recent_feedback(limit: int = 10) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, user_id, username, message, status, created_at "
        "FROM player_feedback ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Staff Todo ────────────────────────────────────────────────────────────────

def add_staff_todo(task: str, user_id: str, username: str) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO staff_todo (task, created_by_user_id, created_by_username) "
        "VALUES (?, ?, ?)",
        ((task or "")[:150], user_id or "", (username or "").lower()),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id or 0


def get_staff_todo() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, task, status, created_by_username, created_at, completed_at "
        "FROM staff_todo ORDER BY id ASC",
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def complete_staff_todo(todo_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute(
        "UPDATE staff_todo SET status='done', completed_at=datetime('now') "
        "WHERE id=? AND status='pending'",
        (todo_id,),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def remove_staff_todo(todo_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute("DELETE FROM staff_todo WHERE id=?", (todo_id,))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def clear_staff_todo() -> int:
    conn = get_connection()
    cur = conn.execute("DELETE FROM staff_todo")
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n


# ── Known Issues ──────────────────────────────────────────────────────────────

def add_known_issue(issue: str, username: str) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO known_issues (issue, added_by_username) VALUES (?, ?)",
        ((issue or "")[:200], (username or "").lower()),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id or 0


def get_known_issues() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, issue, added_by_username, created_at "
        "FROM known_issues ORDER BY id ASC",
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def remove_known_issue(issue_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute("DELETE FROM known_issues WHERE id=?", (issue_id,))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def clear_known_issues() -> int:
    conn = get_connection()
    cur = conn.execute("DELETE FROM known_issues")
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n


# ── Bot Update Notes ──────────────────────────────────────────────────────────

def add_update_note(note: str, username: str) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO bot_update_notes (note, added_by_username) VALUES (?, ?)",
        ((note or "")[:200], (username or "").lower()),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id or 0


def get_update_notes() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, note, added_by_username, created_at "
        "FROM bot_update_notes ORDER BY id ASC",
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_update_notes(lines: list[str], username: str) -> None:
    """Replace all update notes with the provided lines."""
    conn = get_connection()
    conn.execute("DELETE FROM bot_update_notes")
    uname = (username or "").lower()
    for line in lines:
        if line.strip():
            conn.execute(
                "INSERT INTO bot_update_notes (note, added_by_username) VALUES (?, ?)",
                (line.strip()[:200], uname),
            )
    conn.commit()
    conn.close()


def clear_update_notes() -> None:
    conn = get_connection()
    conn.execute("DELETE FROM bot_update_notes")
    conn.commit()
    conn.close()


# ── Pending Coin Rewards ──────────────────────────────────────────────────────

def add_pending_coin_reward(user_id: str, username: str,
                            amount: int, source: str = "") -> int:
    """Queue a coin reward for a player to claim via /claimrewards."""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO pending_coin_rewards (user_id, username, amount, source) "
        "VALUES (?, ?, ?, ?)",
        (user_id or "", (username or "").lower(), max(0, int(amount)), source or ""),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id or 0


def get_pending_coin_rewards_for_user(user_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, amount, source, created_at "
        "FROM pending_coin_rewards WHERE user_id=? AND status='pending' "
        "ORDER BY id ASC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def claim_pending_coin_rewards(user_id: str) -> tuple[int, int]:
    """
    Mark all pending coin rewards for user as claimed.
    Returns (total_coins, reward_count). Does NOT credit the balance —
    caller (banker bot) must call add_balance(user_id, total).
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, amount FROM pending_coin_rewards "
        "WHERE user_id=? AND status='pending'",
        (user_id,),
    ).fetchall()
    if not rows:
        conn.close()
        return 0, 0
    ids   = [r["id"] for r in rows]
    total = sum(r["amount"] for r in rows)
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE pending_coin_rewards SET status='claimed', "
        f"claimed_at=datetime('now') WHERE id IN ({placeholders})",
        ids,
    )
    conn.commit()
    conn.close()
    return total, len(rows)


# ── Bot Maintenance Settings ──────────────────────────────────────────────────

def get_maintenance_state(scope: str, target: str) -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM bot_maintenance_settings WHERE scope=? AND LOWER(target)=LOWER(?)",
        (scope, target or ""),
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def set_maintenance_state(scope: str, target: str, enabled: bool,
                          reason: str, user_id: str, username: str) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT INTO bot_maintenance_settings
               (scope, target, enabled, reason, set_by_user_id, set_by_username, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(scope, target) DO UPDATE SET
               enabled         = excluded.enabled,
               reason          = excluded.reason,
               set_by_user_id  = excluded.set_by_user_id,
               set_by_username = excluded.set_by_username,
               updated_at      = excluded.updated_at""",
        (scope, (target or "").lower(), int(enabled),
         reason or "", user_id or "", (username or "").lower()),
    )
    conn.commit()
    conn.close()


def get_all_maintenance_states() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM bot_maintenance_settings ORDER BY scope, target ASC",
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def is_bot_maintenance_db(target: str) -> bool:
    """Return True if a specific bot (by username or mode) is in maintenance."""
    row = get_maintenance_state("bot", target)
    if row:
        return bool(row.get("enabled", 0))
    return False


def is_global_maintenance_db() -> bool:
    """Return True if global maintenance is enabled in the DB."""
    row = get_maintenance_state("global", "all")
    if row:
        return bool(row.get("enabled", 0))
    return False


# ── Bot Instances Status ──────────────────────────────────────────────────────

def get_all_bot_instances_status() -> list[dict]:
    """Return all bot_instances rows for status display."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT bot_mode, bot_username, status, last_seen_at, enabled "
        "FROM bot_instances ORDER BY bot_mode ASC",
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Subscriber Notification Recipient History ─────────────────────────────────

def get_sub_notif_recipient_history(user_id: str, limit: int = 5) -> list[dict]:
    """Return last N delivery records for a user_id."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT notification_id, user_id, username, category,
                  delivery_method, status, error, created_at
           FROM subscriber_notification_recipients
           WHERE user_id = ?
           ORDER BY id DESC LIMIT ?""",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── QoL / debug helper functions ──────────────────────────────────────────────

# -- Feedback ------------------------------------------------------------------

def add_player_feedback(user_id: str, username: str, message: str) -> int:
    """Insert a player feedback row and return its id."""
    conn = get_connection()
    cur  = conn.execute(
        "INSERT INTO player_feedback (user_id, username, message) VALUES (?, ?, ?)",
        (user_id or "", (username or "").lower(), (message or "")[:500]),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id or 0


def get_recent_feedback(limit: int = 10) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, user_id, username, message, status, created_at "
        "FROM player_feedback ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# -- Staff TODO ---------------------------------------------------------------

def add_staff_todo(task: str, user_id: str = "", username: str = "") -> int:
    """Insert a staff todo item and return its id."""
    conn   = get_connection()
    cur    = conn.execute(
        "INSERT INTO staff_todo (task, created_by_user_id, created_by_username) "
        "VALUES (?, ?, ?)",
        ((task or "")[:300], user_id or "", (username or "").lower()),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id or 0


def get_staff_todo() -> list:
    conn  = get_connection()
    rows  = conn.execute(
        "SELECT id, task, status, created_by_username, created_at, completed_at "
        "FROM staff_todo ORDER BY status ASC, id ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def complete_staff_todo(todo_id: int) -> bool:
    conn = get_connection()
    conn.execute(
        "UPDATE staff_todo SET status='done', completed_at=datetime('now') "
        "WHERE id=? AND status!='done'",
        (todo_id,),
    )
    changed = conn.total_changes
    conn.commit()
    conn.close()
    return changed > 0


def remove_staff_todo(todo_id: int) -> bool:
    conn = get_connection()
    conn.execute("DELETE FROM staff_todo WHERE id=?", (todo_id,))
    changed = conn.total_changes
    conn.commit()
    conn.close()
    return changed > 0


def clear_staff_todo() -> int:
    conn = get_connection()
    conn.execute("DELETE FROM staff_todo")
    count = conn.total_changes
    conn.commit()
    conn.close()
    return count


# -- Known issues -------------------------------------------------------------

def add_known_issue(issue: str, username: str = "") -> int:
    conn   = get_connection()
    cur    = conn.execute(
        "INSERT INTO known_issues (issue, added_by_username) VALUES (?, ?)",
        ((issue or "")[:300], (username or "").lower()),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id or 0


def get_known_issues() -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, issue, added_by_username, created_at "
        "FROM known_issues ORDER BY id ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def remove_known_issue(issue_id: int) -> bool:
    conn = get_connection()
    conn.execute("DELETE FROM known_issues WHERE id=?", (issue_id,))
    changed = conn.total_changes
    conn.commit()
    conn.close()
    return changed > 0


def clear_known_issues() -> int:
    conn = get_connection()
    conn.execute("DELETE FROM known_issues")
    count = conn.total_changes
    conn.commit()
    conn.close()
    return count


# -- Update notes -------------------------------------------------------------

def add_update_note(note: str, username: str = "") -> int:
    conn   = get_connection()
    cur    = conn.execute(
        "INSERT INTO bot_update_notes (note, added_by_username) VALUES (?, ?)",
        ((note or "")[:300], (username or "").lower()),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id or 0


def get_update_notes() -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, note, added_by_username, created_at "
        "FROM bot_update_notes ORDER BY id ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_update_notes(lines: list, username: str = "") -> None:
    """Replace all update notes with the given list of strings."""
    conn = get_connection()
    conn.execute("DELETE FROM bot_update_notes")
    uname = (username or "").lower()
    for line in lines:
        conn.execute(
            "INSERT INTO bot_update_notes (note, added_by_username) VALUES (?, ?)",
            ((line or "")[:300], uname),
        )
    conn.commit()
    conn.close()


def clear_update_notes() -> None:
    conn = get_connection()
    conn.execute("DELETE FROM bot_update_notes")
    conn.commit()
    conn.close()


# -- Pending coin rewards -----------------------------------------------------

def add_pending_coin_reward(user_id: str, username: str,
                            amount: int, source: str = "") -> int:
    """Queue a coin reward for a player. Returns the new row id."""
    conn   = get_connection()
    cur    = conn.execute(
        "INSERT INTO pending_coin_rewards (user_id, username, amount, source) "
        "VALUES (?, ?, ?, ?)",
        (user_id or "", (username or "").lower(), max(0, int(amount)), (source or "")[:100]),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id or 0


def get_pending_coin_rewards_for_user(user_id: str) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, amount, source, created_at "
        "FROM pending_coin_rewards "
        "WHERE user_id=? AND status='pending' ORDER BY id ASC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def claim_pending_coin_rewards(user_id: str) -> tuple:
    """
    Mark all pending coin rewards for user_id as claimed.
    Returns (total_coins, reward_count).
    Does NOT credit the balance — caller must call add_balance() after.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, amount FROM pending_coin_rewards "
        "WHERE user_id=? AND status='pending'",
        (user_id,),
    ).fetchall()
    if not rows:
        conn.close()
        return (0, 0)
    total = sum(r["amount"] for r in rows)
    ids   = [r["id"] for r in rows]
    conn.execute(
        f"UPDATE pending_coin_rewards "
        f"SET status='claimed', claimed_at=datetime('now') "
        f"WHERE id IN ({','.join('?' * len(ids))})",
        ids,
    )
    conn.commit()
    conn.close()
    return (total, len(ids))


# -- Maintenance settings -----------------------------------------------------

def get_maintenance_state(scope: str, target: str) -> dict:
    """Return the maintenance row for the given scope+target, or {}."""
    conn = get_connection()
    row  = conn.execute(
        "SELECT * FROM bot_maintenance_settings WHERE scope=? AND LOWER(target)=LOWER(?)",
        (scope, target or ""),
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def set_maintenance_state(scope: str, target: str, enabled: bool,
                          reason: str = "",
                          user_id: str = "", username: str = "") -> None:
    """Upsert a maintenance state row."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO bot_maintenance_settings
               (scope, target, enabled, reason, set_by_user_id, set_by_username, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(scope, target) DO UPDATE SET
               enabled          = excluded.enabled,
               reason           = excluded.reason,
               set_by_user_id   = excluded.set_by_user_id,
               set_by_username  = excluded.set_by_username,
               updated_at       = excluded.updated_at""",
        (scope, (target or "").lower(), 1 if enabled else 0,
         (reason or "")[:200], user_id or "", (username or "").lower()),
    )
    conn.commit()
    conn.close()


def get_all_maintenance_states() -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM bot_maintenance_settings ORDER BY scope ASC, target ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def is_global_maintenance_db() -> bool:
    """True if global maintenance is enabled in the DB."""
    row = get_maintenance_state("global", "all")
    return bool(row.get("enabled", 0))


def is_bot_maintenance_db(target: str) -> bool:
    """True if a specific bot (by mode or username) is in maintenance."""
    row = get_maintenance_state("bot", target)
    return bool(row.get("enabled", 0))


# -- Bot instances status query -----------------------------------------------

def get_all_bot_instances_status() -> list:
    """Return all bot_instances rows ordered by bot_username."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT bot_mode, bot_username, status, last_seen_at, enabled "
        "FROM bot_instances ORDER BY bot_username ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# -- Notification recipient history -------------------------------------------

def get_sub_notif_recipient_history(user_id: str, limit: int = 5) -> list:
    """Return recent notification delivery rows for a user."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT user_id, username, category, status, delivery_method,
                  error, created_at
           FROM subscriber_notification_recipients
           WHERE user_id=?
           ORDER BY id DESC LIMIT ?""",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# -- Game prices --------------------------------------------------------------

def get_game_price(game: str, setting: str, default=None):
    """Return the current value for a game/setting pair, or *default* if unset."""
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM game_prices WHERE game=? AND setting=?",
        (game.lower(), setting.lower()),
    ).fetchone()
    conn.close()
    return int(row["value"]) if row else default


def set_game_price(game: str, setting: str, value: int, updated_by: str = "") -> None:
    """Upsert a game price setting."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO game_prices (game, setting, value, updated_by, updated_at)
           VALUES (?, ?, ?, ?, datetime('now'))
           ON CONFLICT(game, setting) DO UPDATE SET
               value=excluded.value,
               updated_by=excluded.updated_by,
               updated_at=excluded.updated_at""",
        (game.lower(), setting.lower(), int(value), updated_by),
    )
    conn.commit()
    conn.close()


def get_all_game_prices(game: str | None = None) -> list:
    """Return game_prices rows. If *game* is given, filter to that game only."""
    conn = get_connection()
    if game:
        rows = conn.execute(
            "SELECT game, setting, value, updated_by, updated_at "
            "FROM game_prices WHERE game=? ORDER BY setting ASC",
            (game.lower(),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT game, setting, value, updated_by, updated_at "
            "FROM game_prices ORDER BY game ASC, setting ASC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# -- Economy audit log --------------------------------------------------------

def log_economy_action(
    actor_username: str,
    action_type: str,
    game: str = "",
    setting: str = "",
    old_value: str = "",
    new_value: str = "",
) -> None:
    """Append a row to economy_audit_log."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO economy_audit_log
               (actor_username, action_type, game, setting, old_value, new_value)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (actor_username, action_type, game, setting, str(old_value), str(new_value)),
    )
    conn.commit()
    conn.close()


def get_economy_audit_log(limit: int = 20) -> list:
    """Return the most recent economy audit log entries."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM economy_audit_log ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Leaderboard helpers — 3.1D
# ---------------------------------------------------------------------------

def _get_bot_name_filter() -> frozenset:
    """Return a lowercase frozenset of known bot usernames for LB filtering."""
    _FALLBACK: frozenset = frozenset({
        "bankingbot", "bankerbot", "chilltopiamc",
        "greatestprospector", "masterangler",
        "chipsoprano", "acesinatra", "keanushield", "dj_dudu",
    })
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT LOWER(bot_username) AS n FROM bot_instances "
            "WHERE bot_username IS NOT NULL AND bot_username != ''"
        ).fetchall()
        conn.close()
        names = frozenset(r["n"] for r in rows)
        return names | _FALLBACK if names else _FALLBACK
    except Exception:
        return _FALLBACK


def get_top_balances(limit: int = 5) -> list[dict]:
    """Return top players by coin balance, bots excluded."""
    try:
        bot_filter = _get_bot_name_filter()
        conn = get_connection()
        rows = conn.execute(
            "SELECT username, balance FROM users ORDER BY balance DESC LIMIT ?",
            (limit + 25,),
        ).fetchall()
        conn.close()
        return [
            {"username": r["username"], "balance": r["balance"]}
            for r in rows
            if r["username"].lower() not in bot_filter
        ][:limit]
    except Exception:
        return []


def get_top_miners(limit: int = 5) -> list[dict]:
    """Return top players by mining XP, bots excluded."""
    try:
        bot_filter = _get_bot_name_filter()
        conn = get_connection()
        rows = conn.execute(
            "SELECT username, mining_xp, mining_level, total_ores "
            "FROM mining_players ORDER BY mining_xp DESC LIMIT ?",
            (limit + 25,),
        ).fetchall()
        conn.close()
        return [
            dict(r)
            for r in rows
            if r["username"].lower() not in bot_filter
        ][:limit]
    except Exception:
        return []


def get_top_streaks(limit: int = 5) -> list[dict]:
    """Return top players by best daily streak, bots excluded."""
    try:
        bot_filter = _get_bot_name_filter()
        conn = get_connection()
        rows = conn.execute(
            """SELECT u.username, dc.streak, dc.best_streak, dc.total_claims
               FROM daily_claims dc
               JOIN users u ON u.user_id = dc.user_id
               ORDER BY dc.best_streak DESC
               LIMIT ?""",
            (limit + 25,),
        ).fetchall()
        conn.close()
        return [
            dict(r)
            for r in rows
            if r["username"].lower() not in bot_filter
        ][:limit]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 3.1J — Mission progress helpers
# ---------------------------------------------------------------------------

def get_mission_progress(user_id: str, mission_key: str, period_key: str) -> int:
    """Return current progress for a player's mission."""
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT progress FROM player_missions "
            "WHERE user_id=? AND mission_key=? AND period_key=?",
            (user_id, mission_key, period_key),
        ).fetchone()
        conn.close()
        return row["progress"] if row else 0
    except Exception:
        return 0


def increment_mission_progress(
    user_id: str,
    username: str,
    mission_key: str,
    period_key: str,
    amount: int = 1,
    target: int = 9999,
) -> int:
    """Increment mission progress capped at target. Returns new progress."""
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT progress FROM player_missions "
            "WHERE user_id=? AND mission_key=? AND period_key=?",
            (user_id, mission_key, period_key),
        ).fetchone()
        cur = row["progress"] if row else 0
        if cur >= target:
            conn.close()
            return cur
        new_prog = min(cur + amount, target)
        conn.execute(
            """INSERT INTO player_missions
                   (user_id, username, mission_key, period_key, progress, claimed, updated_at)
               VALUES (?, ?, ?, ?, ?, 0, datetime('now'))
               ON CONFLICT(user_id, mission_key, period_key) DO UPDATE SET
                   progress=excluded.progress, updated_at=excluded.updated_at""",
            (user_id, username, mission_key, period_key, new_prog),
        )
        conn.commit()
        conn.close()
        return new_prog
    except Exception:
        return 0


def is_mission_claimed(user_id: str, mission_key: str, period_key: str) -> bool:
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT claimed FROM player_missions "
            "WHERE user_id=? AND mission_key=? AND period_key=?",
            (user_id, mission_key, period_key),
        ).fetchone()
        conn.close()
        return bool(row and row["claimed"])
    except Exception:
        return False


def claim_mission_db(user_id: str, mission_key: str, period_key: str) -> None:
    """Mark a mission as claimed."""
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO player_missions
                   (user_id, username, mission_key, period_key, progress, claimed, updated_at)
               VALUES (?, '', ?, ?, 0, 1, datetime('now'))
               ON CONFLICT(user_id, mission_key, period_key) DO UPDATE SET
                   claimed=1, updated_at=datetime('now')""",
            (user_id, mission_key, period_key),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def is_set_claimed(user_id: str, set_type: str, period_key: str) -> bool:
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT claimed FROM player_mission_sets "
            "WHERE user_id=? AND set_type=? AND period_key=?",
            (user_id, set_type, period_key),
        ).fetchone()
        conn.close()
        return bool(row and row["claimed"])
    except Exception:
        return False


def claim_mission_set_db(
    user_id: str, username: str, set_type: str, period_key: str
) -> None:
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO player_mission_sets
                   (user_id, username, period_key, set_type, completed, claimed, updated_at)
               VALUES (?, ?, ?, ?, 1, 1, datetime('now'))
               ON CONFLICT(user_id, set_type, period_key) DO UPDATE SET
                   completed=1, claimed=1, updated_at=datetime('now')""",
            (user_id, username, period_key, set_type),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def increment_weekly_daily_sets(
    user_id: str, username: str, weekly_period: str
) -> int:
    """Track daily sets completed this week. Returns new count."""
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT completed FROM player_mission_sets "
            "WHERE user_id=? AND set_type='weekly_daily_sets' AND period_key=?",
            (user_id, weekly_period),
        ).fetchone()
        cur   = row["completed"] if row else 0
        new_v = cur + 1
        conn.execute(
            """INSERT INTO player_mission_sets
                   (user_id, username, period_key, set_type, completed, claimed, updated_at)
               VALUES (?, ?, ?, 'weekly_daily_sets', ?, 0, datetime('now'))
               ON CONFLICT(user_id, set_type, period_key) DO UPDATE SET
                   completed=excluded.completed, updated_at=excluded.updated_at""",
            (user_id, username, weekly_period, new_v),
        )
        conn.commit()
        conn.close()
        return new_v
    except Exception:
        return 0


def is_milestone_claimed(
    user_id: str, collection_type: str, milestone: int
) -> bool:
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT id FROM collection_milestone_claims "
            "WHERE user_id=? AND collection_type=? AND milestone=?",
            (user_id, collection_type, milestone),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def record_milestone_claim(
    user_id: str, username: str, collection_type: str, milestone: int
) -> None:
    try:
        conn = get_connection()
        conn.execute(
            """INSERT OR IGNORE INTO collection_milestone_claims
                   (user_id, username, collection_type, milestone, claimed_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (user_id, username, collection_type, milestone),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def add_season_points(
    user_id: str, username: str, season_key: str, category: str, points: int
) -> None:
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO season_points
                   (user_id, username, season_key, category, points, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(user_id, season_key, category) DO UPDATE SET
                   points=points+excluded.points, updated_at=excluded.updated_at""",
            (user_id, username, season_key, category, points),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_season_leaderboard(
    season_key: str, category: str, limit: int = 10
) -> list[dict]:
    try:
        conn = get_connection()
        rows = conn.execute(
            """SELECT user_id, username, points FROM season_points
               WHERE season_key=? AND category=?
               ORDER BY points DESC LIMIT ?""",
            (season_key, category, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_player_xp_info(user_id: str) -> dict:
    """Return level and total_xp from users table."""
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT xp, level FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()
        conn.close()
        if row:
            return {"total_xp": row["xp"] or 0, "level": row["level"] or 1}
        return {"total_xp": 0, "level": 1}
    except Exception:
        return {"total_xp": 0, "level": 1}


def count_active_missions(mission_type: str, period_key: str) -> int:
    """Count distinct players who started missions this period."""
    try:
        prefix = "daily_" if mission_type == "daily" else "weekly_"
        conn   = get_connection()
        row    = conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS cnt FROM player_missions "
            "WHERE mission_key LIKE ? AND period_key=?",
            (f"{prefix}%", period_key),
        ).fetchone()
        conn.close()
        return row["cnt"] if row else 0
    except Exception:
        return 0


def reset_missions_for_user(user_id: str, period_key: str) -> None:
    """Delete all mission progress for a user for a given period."""
    try:
        conn = get_connection()
        conn.execute(
            "DELETE FROM player_missions WHERE user_id=? AND period_key=?",
            (user_id, period_key),
        )
        conn.execute(
            "DELETE FROM player_mission_sets WHERE user_id=? AND period_key=?",
            (user_id, period_key),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_user_id_by_username(username: str) -> str | None:
    """Return user_id for the given username (case-insensitive), or None."""
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT user_id FROM users WHERE LOWER(username)=?",
            (username.lower(),),
        ).fetchone()
        conn.close()
        return row["user_id"] if row else None
    except Exception:
        return None


def record_season_reward(
    user_id: str,
    username: str,
    season_key: str,
    category: str,
    reward_coins: int,
    awarded_by: str,
) -> None:
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO season_reward_history
                   (user_id, username, season_key, category, reward_coins, awarded_by, awarded_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
            (user_id, username, season_key, category, reward_coins, awarded_by),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_season_reward_history(season_key: str, limit: int = 10) -> list[dict]:
    try:
        conn = get_connection()
        rows = conn.execute(
            """SELECT username, category, reward_coins, awarded_by, awarded_at
               FROM season_reward_history WHERE season_key=?
               ORDER BY awarded_at DESC LIMIT ?""",
            (season_key, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 3.1Q — Beta settings helpers
# ---------------------------------------------------------------------------

def get_beta_setting(key: str, default: str = "") -> str:
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT value FROM beta_settings WHERE key=?", (key,)
        ).fetchone()
        conn.close()
        return row["value"] if row else default
    except Exception:
        return default


def set_beta_setting(key: str, value: str) -> None:
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO beta_settings (key, value, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 3.1Q — Command error log helpers
# ---------------------------------------------------------------------------

def add_command_error_log(
    user_id: str,
    username: str,
    command: str,
    args: str,
    error_summary: str,
    traceback: str = "",
) -> int:
    try:
        conn = get_connection()
        cur  = conn.execute(
            "INSERT INTO command_error_logs "
            "(user_id, username, command, args, error_summary, traceback) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, username, command, args, error_summary[:500], traceback[:2000]),
        )
        conn.commit()
        row_id = cur.lastrowid
        conn.close()
        return row_id or 0
    except Exception:
        return 0


def get_command_error_logs(status: str | None = None, limit: int = 10) -> list[dict]:
    try:
        conn = get_connection()
        if status:
            rows = conn.execute(
                "SELECT * FROM command_error_logs WHERE status=? ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM command_error_logs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def close_command_error_log(log_id: int) -> bool:
    try:
        conn = get_connection()
        cur  = conn.execute(
            "UPDATE command_error_logs SET status='closed' WHERE id=?", (log_id,)
        )
        conn.commit()
        conn.close()
        return cur.rowcount > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 3.1Q — Rotating announcements helpers
# ---------------------------------------------------------------------------

def get_rotating_announcements() -> list[dict]:
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM rotating_announcements ORDER BY id ASC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def add_rotating_announcement(message: str) -> int:
    try:
        conn = get_connection()
        cur  = conn.execute(
            "INSERT INTO rotating_announcements (message) VALUES (?)", (message,)
        )
        conn.commit()
        row_id = cur.lastrowid
        conn.close()
        return row_id or 0
    except Exception:
        return 0


def remove_rotating_announcement(ann_id: int) -> bool:
    try:
        conn = get_connection()
        cur  = conn.execute(
            "DELETE FROM rotating_announcements WHERE id=?", (ann_id,)
        )
        conn.commit()
        conn.close()
        return cur.rowcount > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 3.1Q — Bug report helpers (reads from existing reports table)
# ---------------------------------------------------------------------------

def close_bug_report_by_id(report_id: int) -> bool:
    try:
        conn = get_connection()
        cur  = conn.execute(
            "UPDATE reports SET status='closed' "
            "WHERE id=? AND report_type='bug_report'",
            (report_id,),
        )
        conn.commit()
        conn.close()
        return cur.rowcount > 0
    except Exception:
        return False


def get_bug_reports_by_type(
    status: str | None = None, limit: int = 10
) -> list[dict]:
    try:
        conn = get_connection()
        if status:
            rows = conn.execute(
                "SELECT * FROM reports WHERE report_type='bug_report' "
                "AND status=? ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM reports WHERE report_type='bug_report' "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Title V2 DB helpers
# ---------------------------------------------------------------------------

def add_user_title(user_id: str, username: str, title_id: str,
                    source: str = "Shop", expires_at: str = "") -> None:
    """Grant a title to a user (INSERT OR IGNORE)."""
    try:
        conn = get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO user_titles "
            "(user_id, username, title_id, source, expires_at) VALUES (?,?,?,?,?)",
            (user_id, username.lower(), title_id, source, expires_at or ""),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def remove_user_title(user_id: str, title_id: str) -> None:
    try:
        conn = get_connection()
        conn.execute(
            "DELETE FROM user_titles WHERE user_id=? AND title_id=?",
            (user_id, title_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def has_user_title(user_id: str, title_id: str) -> bool:
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT 1 FROM user_titles WHERE user_id=? AND title_id=?",
            (user_id, title_id),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def get_user_title(user_id: str, title_id: str) -> dict | None:
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT * FROM user_titles WHERE user_id=? AND title_id=?",
            (user_id, title_id),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def get_user_titles(user_id: str) -> list[dict]:
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM user_titles WHERE user_id=? ORDER BY unlocked_at ASC",
            (user_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_active_seasonal_titles() -> list[dict]:
    """Return all user_titles rows with source=Seasonal that have not expired."""
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM user_titles WHERE source='Seasonal' "
            "AND (expires_at='' OR expires_at > datetime('now')) "
            "ORDER BY unlocked_at DESC",
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def expire_seasonal_titles() -> int:
    """Remove seasonal titles that have expired. Returns count removed."""
    try:
        conn = get_connection()
        cur  = conn.execute(
            "DELETE FROM user_titles WHERE source='Seasonal' "
            "AND expires_at != '' AND expires_at <= datetime('now')",
        )
        conn.commit()
        conn.close()
        return cur.rowcount
    except Exception:
        return 0


# ── user_title_stats helpers ──────────────────────────────────────────────

def _ensure_title_stats(conn, user_id: str, username: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO user_title_stats (user_id, username) VALUES (?,?)",
        (user_id, username.lower()),
    )


def get_title_stats(user_id: str) -> dict:
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT * FROM user_title_stats WHERE user_id=?",
            (user_id,),
        ).fetchone()
        conn.close()
        return dict(row) if row else {}
    except Exception:
        return {}


def increment_title_stat(user_id: str, username: str,
                          stat: str, amount: int = 1) -> None:
    _ALLOWED = {
        "fish_caught", "ores_mined", "casino_hands_played", "casino_hands_won",
        "casino_lifetime_wagered", "casino_lifetime_won", "casino_biggest_win",
        "blackjack_wins", "poker_wins", "poker_allin_wins", "poker_royal_flush_wins",
        "lifetime_gold_tipped", "lifetime_chillcoins_earned",
        "lifetime_chillcoins_spent", "room_visit_days", "room_join_count",
        "minigames_played", "minigames_won", "times_jailed", "players_jailed",
        "bails_paid",
    }
    if stat not in _ALLOWED:
        return
    try:
        conn = get_connection()
        _ensure_title_stats(conn, user_id, username)
        conn.execute(
            f"UPDATE user_title_stats SET {stat}={stat}+? WHERE user_id=?",
            (amount, user_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def set_title_stat(user_id: str, username: str, stat: str, value) -> None:
    """Set a single stat column (for biggest_win, last_visit_date, etc.)."""
    _SAFE = {
        "casino_biggest_win", "last_visit_date",
        "room_visit_days", "room_join_count",
    }
    if stat not in _SAFE:
        return
    try:
        conn = get_connection()
        _ensure_title_stats(conn, user_id, username)
        conn.execute(
            f"UPDATE user_title_stats SET {stat}=? WHERE user_id=?",
            (value, user_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_title_stat_leaderboard(stat: str, limit: int = 10) -> list[dict]:
    _ALLOWED = {
        "fish_caught", "ores_mined", "casino_lifetime_won",
        "lifetime_chillcoins_earned",
    }
    if stat not in _ALLOWED:
        return []
    try:
        conn  = get_connection()
        rows  = conn.execute(
            f"SELECT username, {stat} as value FROM user_title_stats "
            f"ORDER BY {stat} DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_title_count_leaderboard(limit: int = 10) -> list[dict]:
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT username, COUNT(*) as count FROM user_titles "
            "GROUP BY user_id ORDER BY count DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── title_logs helpers ────────────────────────────────────────────────────

def log_title_action(action: str, user_id: str, username: str,
                      title_id: str, target_user_id: str = "",
                      target_username: str = "", details: str = "") -> None:
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO title_logs "
            "(action, user_id, username, target_user_id, target_username, "
            "title_id, details) VALUES (?,?,?,?,?,?,?)",
            (action, user_id, username.lower(), target_user_id or "",
             (target_username or "").lower(), title_id, details or ""),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_title_logs(user_id: str | None = None, limit: int = 10) -> list[dict]:
    try:
        conn = get_connection()
        if user_id:
            rows = conn.execute(
                "SELECT * FROM title_logs "
                "WHERE user_id=? OR target_user_id=? "
                "ORDER BY id DESC LIMIT ?",
                (user_id, user_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM title_logs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── title_loadouts helpers ────────────────────────────────────────────────

def save_title_loadout(user_id: str, name: str,
                        title_id: str, badge_id: str) -> None:
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO title_loadouts (user_id, name, title_id, badge_id) "
            "VALUES (?,?,?,?) ON CONFLICT(user_id, name) DO UPDATE SET "
            "title_id=excluded.title_id, badge_id=excluded.badge_id, "
            "updated_at=datetime('now')",
            (user_id, name, title_id, badge_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_title_loadout(user_id: str, name: str) -> dict | None:
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT * FROM title_loadouts WHERE user_id=? AND name=?",
            (user_id, name),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def get_title_loadouts(user_id: str) -> list[dict]:
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM title_loadouts WHERE user_id=? ORDER BY name",
            (user_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── title_catalog helpers ─────────────────────────────────────────────────

def get_catalog_title(title_id: str) -> dict | None:
    try:
        conn = get_connection()
        row  = conn.execute(
            "SELECT * FROM title_catalog WHERE title_id=?",
            (title_id,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        d = dict(row)
        try:
            d["perks"] = __import__("json").loads(d.get("perks_json") or "{}")
        except Exception:
            d["perks"] = {}
        return d
    except Exception:
        return None


def upsert_catalog_title(title_id: str, display_name: str, tier: str,
                          source: str, price: int,
                          buyable: bool = False, active: bool = True,
                          requirement_type: str = "",
                          requirement_value: int = 0,
                          category: str = "",
                          perks: dict | None = None) -> None:
    import json as _json
    perks_json = _json.dumps(perks or {})
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO title_catalog "
            "(title_id, display_name, tier, source, price, buyable, active, "
            "requirement_type, requirement_value, category, perks_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(title_id) DO UPDATE SET "
            "display_name=excluded.display_name, tier=excluded.tier, "
            "source=excluded.source, price=excluded.price, "
            "buyable=excluded.buyable, active=excluded.active, "
            "updated_at=datetime('now')",
            (title_id, display_name, tier, source, price,
             int(buyable), int(active),
             requirement_type, requirement_value, category, perks_json),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def edit_catalog_title(title_id: str, field: str, value) -> None:
    _SAFE = {"display_name", "tier", "source", "price", "buyable",
             "active", "requirement_value"}
    if field not in _SAFE:
        raise ValueError(f"Field '{field}' cannot be edited via this helper.")
    try:
        conn = get_connection()
        conn.execute(
            f"UPDATE title_catalog SET {field}=?, updated_at=datetime('now') "
            f"WHERE title_id=?",
            (value, title_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        raise RuntimeError(str(e))




# ---------------------------------------------------------------------------
# Standalone owned_items helpers (title_system.py uses these)
# ---------------------------------------------------------------------------

def add_owned_item(user_id: str, username: str,
                   item_id: str, item_type: str) -> None:
    """INSERT OR IGNORE an item into owned_items."""
    try:
        conn = get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO owned_items "
            "(user_id, item_id, item_type) VALUES (?,?,?)",
            (user_id, item_id, item_type),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def remove_owned_item(user_id: str, item_id: str,
                      item_type: str = "") -> None:
    """Remove an item from owned_items (optionally filter by type)."""
    try:
        conn = get_connection()
        if item_type:
            conn.execute(
                "DELETE FROM owned_items "
                "WHERE user_id=? AND item_id=? AND item_type=?",
                (user_id, item_id, item_type),
            )
        else:
            conn.execute(
                "DELETE FROM owned_items WHERE user_id=? AND item_id=?",
                (user_id, item_id),
            )
        conn.commit()
        conn.close()
    except Exception:
        pass
