"""
modules/poker.py — Persistent Texas Hold'em Table

Design:
  - Players SIT at the table once; buy-in coins go to table_stack.
  - Each hand: table_stack → in-hand stack → play → table_stack updated.
  - Coins only return to wallet when player does /poker leave.
  - Blinds (SB/BB) posted each hand, dealer button rotates.
  - Auto-start next hand after configurable delay.

Public API consumed by main.py:
  handle_poker / handle_pokerhelp
  handle_pokerstats / handle_pokerlb
  handle_setpokerbuyin / handle_setpokerplayers
  handle_setpokerlobbytimer / handle_setpokertimer / handle_setpokerraise
  handle_setpokerdailywinlimit / handle_setpokerdailylosslimit
  handle_resetpokerlimits
  handle_setpokerturntimer / handle_setpokerlimits
  handle_setpokerblinds / handle_setpokerante / handle_setpokernexthandtimer
  handle_setpokermaxstack
  handle_pokerdebug / handle_pokerfix / handle_pokerrefundall
  startup_poker_recovery(bot)
  soft_reset_table()   — cancel tasks, keep DB state
  reset_table()        — cancel tasks + return all stacks to wallets + clear
  get_poker_state_str()
"""
from __future__ import annotations

import asyncio
import json
import random
import uuid
from datetime import datetime, date, timezone
from itertools import combinations
from collections import Counter
from typing import Optional

from highrise import BaseBot, User
import database as db
from modules.permissions import can_manage_games, is_admin, is_owner

# ── Card constants ─────────────────────────────────────────────────────────────
_RANKS = "23456789TJQKA"
_SUITS = "cdhs"
_SUIT_SYM = {"c": "♣", "d": "♦", "h": "♥", "s": "♠"}
_HAND_NAMES = {
    9: "Royal Flush", 8: "Straight Flush", 7: "Four of a Kind",
    6: "Full House",  5: "Flush",          4: "Straight",
    3: "Three of a Kind", 2: "Two Pair",   1: "Pair", 0: "High Card",
}

# ── In-memory timer handles ────────────────────────────────────────────────────
_lobby_task:     Optional[asyncio.Task] = None  # kept for compat
_turn_task:      Optional[asyncio.Task] = None
_next_hand_task: Optional[asyncio.Task] = None
_close_confirm_codes: dict[str, str] = {}  # code → actor_username (/poker closeforce)
_action_processing: set[str] = set()       # "round_id:user_id" dedup lock — prevents double-action
_poker_paused: bool = False                # /pokerpause flag


# ── Utilities ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _today() -> str:
    return date.today().isoformat()

def _new_round_id() -> str:
    return f"pk_{uuid.uuid4().hex[:12]}"

def _make_deck() -> list[str]:
    return [r + s for r in _RANKS for s in _SUITS]

def _fc(card: str) -> str:
    return card[0].upper() + _SUIT_SYM[card[1]]

def _fcs(cards: list[str]) -> str:
    return " ".join(_fc(c) for c in cards)


def _pdn(p: dict) -> str:
    """Display name for a poker player/seat dict (user_id + username keys)."""
    try:
        return db.get_display_name(p["user_id"], p["username"])
    except Exception:
        return f"@{p.get('username', '?')}"


def _udn(uid: str, uname: str) -> str:
    """Display name from separate uid + uname strings."""
    try:
        return db.get_display_name(uid, uname)
    except Exception:
        return f"@{uname}"


def _get_card_marker() -> str:
    try:
        return db.get_room_setting("poker_card_marker", "🂠")
    except Exception:
        return "🂠"

def _fmt_private_hand(cards: list, stack: int,
                      hand_num: int = 0, pos: str = "",
                      is_turn: bool = False, owe: int = 0,
                      rank_label: str = "") -> str:
    cards_s = _fcs(cards)
    if is_turn:
        if owe > 0:
            base = (f"{_PK_TURN} | {_PK_HAND}: {cards_s} | "
                    f"Call {owe:,} 🪙 | {_PK_STACK}: {stack:,} 🪙")
        else:
            base = f"{_PK_TURN} | {_PK_HAND}: {cards_s} | {_PK_STACK}: {stack:,} 🪙"
        if rank_label and len(base) + len(rank_label) + 3 < 249:
            base += f" | {rank_label}"
    else:
        prefix = f"Hand #{hand_num}" if hand_num else ""
        pos_s  = f" {pos.strip()}" if pos else ""
        if prefix or pos_s:
            base = f"{_PK_HAND}: {cards_s} | {prefix}{pos_s} | {_PK_STACK}: {stack:,} 🪙"
        else:
            base = f"{_PK_HAND}: {cards_s} | {_PK_STACK}: {stack:,} 🪙"
        if rank_label and len(base) + len(rank_label) + 3 < 249:
            base += f" | {rank_label}"
    return base[:249]

async def _chat(bot: BaseBot, msg: str) -> None:
    try:
        await bot.highrise.chat(msg[:249])
    except Exception:
        pass

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass

# ── Poker colored action labels (color tags auto-reset with <#FFFFFF>) ─────────
_PK_CHECK  = "<#00FF66>✅ CHECK<#FFFFFF>"
_PK_CALL   = "<#3399FF>📞 CALL<#FFFFFF>"
_PK_BET    = "<#FFD700>💰 BET<#FFFFFF>"
_PK_RAISE  = "<#FF9900>⬆️ RAISE<#FFFFFF>"
_PK_FOLD   = "<#FF3333>❌ FOLD<#FFFFFF>"
_PK_ALLIN  = "<#FFD700>🔥 ALL-IN<#FFFFFF>"
_PK_INVAL  = "<#FF3333>⚠️ INVALID<#FFFFFF>"
_PK_WIN    = "<#FFD700>🏆 WIN<#FFFFFF>"
_PK_POT    = "<#FFD700>🪙 POT<#FFFFFF>"
_PK_TURN   = "<#00FFCC>👉 YOUR TURN<#FFFFFF>"
_PK_CARDS  = "<#CC66FF>🃏 Cards<#FFFFFF>"
_PK_DEALER = "<#66CCFF>🎲 Dealer<#FFFFFF>"
_PK_BOARD  = "<#B388FF>🃏 BOARD<#FFFFFF>"
_PK_HAND   = "<#CC66FF>🃏 YOUR HAND<#FFFFFF>"
_PK_STACK  = "<#00FFAA>💵 STACK<#FFFFFF>"
_PK_BLIND  = "<#FFAA00>👁️ BLIND<#FFFFFF>"
_PK_TIMER  = "<#FFCC00>⏳ TIMER<#FFFFFF>"
_PK_WARN   = "<#FF3333>⚠️ WARNING<#FFFFFF>"
_PK_INFO   = "<#66CCFF>ℹ️ INFO<#FFFFFF>"
_PK_LOSS   = "<#FF3333>❌ LOSS<#FFFFFF>"

# ── Poker formatter helpers ───────────────────────────────────────────────────
def _pk_pot(n: int) -> str:
    return f"{_PK_POT}: {n:,} 🪙"

def _pk_stack(n: int) -> str:
    return f"{_PK_STACK}: {n:,} 🪙"

def _pk_board(cards: list) -> str:
    return f"{_PK_BOARD}: {_fcs(cards)}" if cards else ""

def _pk_hand(cards: list) -> str:
    return f"{_PK_HAND}: {_fcs(cards)}"

def _pk_warn(msg: str) -> str:
    return f"{_PK_WARN} — {msg}"

def _pk_info(msg: str) -> str:
    return f"{_PK_INFO} — {msg}"

def _cancel_task(t: Optional[asyncio.Task]) -> None:
    if t and not t.done():
        t.cancel()


# ── Hand evaluation ─────────────────────────────────────────────────────────────

def _score5(cards: list[str]) -> tuple:
    ranks = sorted([_RANKS.index(c[0]) for c in cards], reverse=True)
    suits = [c[1] for c in cards]
    cnt   = Counter(ranks)
    freq  = sorted(cnt.values(), reverse=True)
    kr    = sorted(cnt.keys(), key=lambda r: (cnt[r], r), reverse=True)
    flush    = len(set(suits)) == 1
    straight = (ranks == list(range(ranks[0], ranks[0] - 5, -1)))
    if sorted(ranks) == [0, 1, 2, 3, 12]:
        straight = True
        ranks = kr = [3, 2, 1, 0, -1]
    if flush and straight:
        return (9 if ranks[0] == 12 else 8,) + tuple(ranks)
    if freq[0] == 4:       return (7,) + tuple(kr)
    if freq[:2] == [3, 2]: return (6,) + tuple(kr)
    if flush:              return (5,) + tuple(ranks)
    if straight:           return (4,) + tuple(ranks)
    if freq[0] == 3:       return (3,) + tuple(kr)
    if freq[:2] == [2, 2]: return (2,) + tuple(kr)
    if freq[0] == 2:       return (1,) + tuple(kr)
    return                        (0,) + tuple(ranks)

def _best_hand(all_cards: list[str]) -> tuple:
    best: Optional[tuple] = None
    for combo in combinations(all_cards, 5):
        s = _score5(list(combo))
        if best is None or s > best:
            best = s
    return best  # type: ignore[return-value]


# ── Settings helpers ───────────────────────────────────────────────────────────

def _s(key: str, fallback: int = 0) -> int:
    conn = db.get_connection()
    row  = conn.execute(
        "SELECT value FROM poker_settings WHERE key=?", (key,)
    ).fetchone()
    conn.close()
    return int(row["value"]) if row else fallback

def _set(key: str, value) -> None:
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO poker_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()
    conn.close()

def _all_settings() -> dict:
    conn = db.get_connection()
    rows = conn.execute("SELECT key, value FROM poker_settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


# ── poker_seated_players DB helpers (persistent table roster) ──────────────────

def _get_all_seated() -> list[dict]:
    """All players currently at the table (any status)."""
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM poker_seated_players ORDER BY seat_number, rowid"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def _get_seated(username: str) -> Optional[dict]:
    conn = db.get_connection()
    row  = conn.execute(
        "SELECT * FROM poker_seated_players WHERE LOWER(username)=LOWER(?)",
        (username,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def _get_active_seated() -> list[dict]:
    """Seated players who can play next hand: status='seated', stack > 0."""
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM poker_seated_players "
        "WHERE status='seated' AND table_stack > 0 "
        "ORDER BY seat_number, rowid"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def _seat_player(username: str, user_id: str, table_stack: int,
                 seat_number: int) -> None:
    conn = db.get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO poker_seated_players "
        "(username, user_id, table_stack, buyin_total, status, seat_number, joined_at) "
        "VALUES (?, ?, ?, ?, 'seated', ?, ?)",
        (username, user_id, table_stack, table_stack, seat_number, _now()),
    )
    conn.commit()
    conn.close()

def _update_seated(username: str, **kw) -> None:
    if not kw:
        return
    kw["last_action_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in kw)
    vals = list(kw.values()) + [username]
    conn = db.get_connection()
    conn.execute(
        f"UPDATE poker_seated_players SET {sets} "
        f"WHERE LOWER(username)=LOWER(?)", vals,
    )
    conn.commit()
    conn.close()

def _remove_seated(username: str) -> None:
    conn = db.get_connection()
    conn.execute(
        "DELETE FROM poker_seated_players WHERE LOWER(username)=LOWER(?)",
        (username,),
    )
    conn.commit()
    conn.close()

def _seated_count() -> int:
    conn = db.get_connection()
    row  = conn.execute(
        "SELECT COUNT(*) as c FROM poker_seated_players"
    ).fetchone()
    conn.close()
    return row["c"] if row else 0

def _next_seat_number() -> int:
    conn = db.get_connection()
    row  = conn.execute(
        "SELECT COALESCE(MAX(seat_number),0)+1 AS n FROM poker_seated_players"
    ).fetchone()
    conn.close()
    return row["n"] if row else 1


# ── poker_active_table DB helpers ──────────────────────────────────────────────

def _get_table() -> Optional[dict]:
    conn = db.get_connection()
    row  = conn.execute("SELECT * FROM poker_active_table WHERE id=1").fetchone()
    conn.close()
    return dict(row) if row else None

def _save_table(**kw) -> None:
    kw["updated_at"] = _now()
    sets  = ", ".join(f"{k}=?" for k in kw)
    vals  = list(kw.values())
    conn  = db.get_connection()
    conn.execute(f"UPDATE poker_active_table SET {sets} WHERE id=1", vals)
    conn.commit()
    conn.close()

def _clear_hand() -> None:
    """Reset per-hand state. Table stays active=1 with seated players."""
    conn = db.get_connection()
    conn.execute(
        "UPDATE poker_active_table SET "
        "round_id=NULL, "
        "deck_json='[]', community_cards_json='[]', "
        "pot=0, current_bet=0, current_player_index=0, "
        "last_raiser_username=NULL, settings_snapshot_json=NULL, "
        "turn_ends_at=NULL, round_started_at=NULL, lobby_started_at=NULL, "
        "lobby_ends_at=NULL, restored_after_restart=0, "
        "small_blind_username=NULL, big_blind_username=NULL, "
        "next_hand_starts_at=NULL "
        "WHERE id=1"
    )
    conn.commit()
    conn.close()

def _clear_table() -> None:
    """Full reset: wipes everything including all state columns."""
    conn = db.get_connection()
    conn.execute(
        "UPDATE poker_active_table SET active=0, phase='idle', round_id=NULL, "
        "created_at=NULL, updated_at=NULL, lobby_started_at=NULL, "
        "lobby_ends_at=NULL, round_started_at=NULL, turn_ends_at=NULL, "
        "current_player_index=0, dealer_button_index=0, deck_json='[]', "
        "community_cards_json='[]', pot=0, current_bet=0, "
        "last_raiser_username=NULL, settings_snapshot_json=NULL, "
        "restored_after_restart=0, hand_number=0, table_closing=0, "
        "small_blind_username=NULL, big_blind_username=NULL, "
        "next_hand_starts_at=NULL WHERE id=1"
    )
    conn.commit()
    conn.close()

def _full_clear_table() -> None:
    """Full reset + clear all seated players."""
    _clear_table()
    conn = db.get_connection()
    conn.execute("DELETE FROM poker_seated_players")
    conn.commit()
    conn.close()


# ── poker_active_players DB helpers (per-hand) ─────────────────────────────────

def _get_players(round_id: str) -> list[dict]:
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM poker_active_players WHERE round_id=? ORDER BY id",
        (round_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def _get_player(round_id: str, user_id: str) -> Optional[dict]:
    """Look up a player by user_id first; fall back to username case-insensitive."""
    conn = db.get_connection()
    row  = conn.execute(
        "SELECT * FROM poker_active_players WHERE round_id=? AND user_id=?",
        (round_id, user_id),
    ).fetchone()
    if row is None:
        # Fallback: seated record maps user_id → username
        sp = conn.execute(
            "SELECT username FROM poker_seated_players WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if sp:
            row = conn.execute(
                "SELECT * FROM poker_active_players "
                "WHERE round_id=? AND LOWER(username)=LOWER(?)",
                (round_id, sp["username"]),
            ).fetchone()
    conn.close()
    return dict(row) if row else None

def _get_player_by_name(round_id: str, username: str) -> Optional[dict]:
    """Look up a player by case-insensitive username."""
    conn = db.get_connection()
    row  = conn.execute(
        "SELECT * FROM poker_active_players "
        "WHERE round_id=? AND LOWER(username)=LOWER(?)",
        (round_id, username),
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def _save_player(round_id: str, user_id: str, **kw) -> None:
    """Update a player row. Looks up by user_id; falls back to username via seated."""
    if not kw:
        return
    kw["acted_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in kw)
    vals = list(kw.values()) + [round_id, user_id]
    conn = db.get_connection()
    affected = conn.execute(
        f"UPDATE poker_active_players SET {sets} WHERE round_id=? AND user_id=?",
        vals,
    ).rowcount
    if affected == 0:
        # Fallback: find by seated username
        sp = conn.execute(
            "SELECT username FROM poker_seated_players WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if sp:
            vals2 = list(kw.values()) + [round_id, sp["username"]]
            conn.execute(
                f"UPDATE poker_active_players SET {sets} "
                f"WHERE round_id=? AND LOWER(username)=LOWER(?)",
                vals2,
            )
    conn.commit()
    conn.close()

def _insert_player(round_id: str, username: str, user_id: str, buyin: int) -> None:
    """Insert a single player row (used by recovery/legacy paths only).
    _start_hand now uses bulk single-transaction inserts directly."""
    conn = db.get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO poker_active_players "
        "(round_id, username, user_id, buyin, stack, status, joined_at) "
        "VALUES (?, ?, ?, ?, ?, 'lobby', ?)",
        (round_id, username, user_id, buyin, buyin, _now()),
    )
    conn.commit()
    conn.close()

def _clear_players(round_id: str) -> None:
    conn = db.get_connection()
    conn.execute(
        "DELETE FROM poker_active_players WHERE round_id=?", (round_id,)
    )
    conn.commit()
    conn.close()


def _validate_deal(round_id: str, expected_count: int) -> Optional[str]:
    """
    Check that every expected player has exactly 2 saved hole cards.
    Returns None on success, or an error string on failure.
    """
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT username, hole_cards_json FROM poker_active_players "
        "WHERE round_id=?",
        (round_id,),
    ).fetchall()
    conn.close()

    actual = len(rows)
    if actual != expected_count:
        return f"Expected {expected_count} hands, found {actual}"

    missing = []
    for r in rows:
        try:
            cards = json.loads(r["hole_cards_json"] or "[]")
        except Exception:
            cards = []
        if len(cards) != 2:
            missing.append(r["username"])

    if missing:
        names = ", ".join(missing[:4])
        return f"Missing cards for: {names}"

    return None


def _active_players(players: list[dict]) -> list[dict]:
    return [p for p in players if p["status"] == "active"]

def _eligible_players(players: list[dict]) -> list[dict]:
    return [p for p in players if p["status"] in ("active", "allin")]

def _needs_to_act(p: dict, table_current_bet: int) -> bool:
    if p["status"] != "active":
        return False
    return p["acted"] == 0 or p["current_bet"] < table_current_bet


# ── Round results / payout safety ──────────────────────────────────────────────

def _is_paid(round_id: str, username: str) -> bool:
    conn = db.get_connection()
    row  = conn.execute(
        "SELECT paid FROM poker_round_results WHERE round_id=? AND username=?",
        (round_id, username),
    ).fetchone()
    conn.close()
    return bool(row and row["paid"])

def _upsert_result(round_id: str, username: str, buyin: int,
                   result: str, payout: int, net: int) -> None:
    conn = db.get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO poker_round_results "
        "(round_id, username, buyin, result, payout, net, paid, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
        (round_id, username, buyin, result, payout, net, _now()),
    )
    conn.commit()
    conn.close()

def _mark_paid(round_id: str, username: str) -> None:
    conn = db.get_connection()
    conn.execute(
        "UPDATE poker_round_results SET paid=1 WHERE round_id=? AND username=?",
        (round_id, username),
    )
    conn.commit()
    conn.close()


# ── Daily limits ───────────────────────────────────────────────────────────────

def _get_daily_net(username: str) -> int:
    conn = db.get_connection()
    row  = conn.execute(
        "SELECT net FROM poker_daily_limits WHERE username=? AND date=?",
        (username, _today()),
    ).fetchone()
    conn.close()
    return row["net"] if row else 0

def _add_daily_net(username: str, delta: int) -> None:
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO poker_daily_limits (username, date, net) VALUES (?, ?, ?) "
        "ON CONFLICT(username, date) DO UPDATE SET net=net+?",
        (username, _today(), delta, delta),
    )
    conn.commit()
    conn.close()

def _reset_daily(username: str) -> None:
    conn = db.get_connection()
    conn.execute(
        "DELETE FROM poker_daily_limits WHERE username=?", (username,)
    )
    conn.commit()
    conn.close()


# ── Inconsistency detection & safe cleanup ─────────────────────────────────────

def detect_poker_inconsistent_state() -> Optional[str]:
    """
    Return a brief description if poker is in a broken state, else None.
    Detects: phase=finished but active hand rows still exist in DB.
    """
    tbl = _get_table()
    if not tbl or not tbl["active"]:
        return None
    phase    = tbl["phase"]
    round_id = tbl.get("round_id")
    if phase in ("idle", "waiting", "between_hands",
                 "preflop", "flop", "turn", "river"):
        return None
    if phase == "recovery_required":
        return "recovery_required"
    if phase == "finished" and round_id:
        players = _get_players(round_id)
        if players:
            return f"finished_stuck:{len(players)}players"
    return None


def _cleanup_finished_hand() -> dict:
    """
    Safely clean up a stuck 'finished' hand where active player rows remain.
    Returns dict: {action, players, pot, next_phase}.
    Possible actions: no_table, cleared_no_round, cleared_no_players,
                      cleared_paid, cleared_zero_pot, pot_unresolved.
    """
    tbl = _get_table()
    if not tbl or not tbl["active"]:
        return {"action": "no_table", "players": 0, "pot": 0, "next_phase": "?"}

    round_id = tbl.get("round_id")
    pot      = int(tbl.get("pot") or 0)

    if not round_id:
        _clear_hand()
        _save_table(phase="waiting")
        return {"action": "cleared_no_round", "players": 0, "pot": pot, "next_phase": "waiting"}

    players = _get_players(round_id)
    if not players:
        _clear_hand()
        n         = len(_get_active_seated())
        new_phase = "between_hands" if n >= 2 else "waiting"
        _save_table(phase=new_phase)
        return {"action": "cleared_no_players", "players": 0, "pot": pot, "next_phase": new_phase}

    all_paid = all(_is_paid(round_id, p["username"]) for p in players)

    if all_paid:
        _clear_players(round_id)
        _clear_hand()
        n         = len(_get_active_seated())
        new_phase = "between_hands" if n >= 2 else "waiting"
        _save_table(phase=new_phase)
        return {"action": "cleared_paid", "players": len(players), "pot": pot, "next_phase": new_phase}

    if pot == 0:
        for p in players:
            if not _is_paid(round_id, p["username"]):
                _pay_seated(p, "refund", 0, round_id)
        _clear_players(round_id)
        _clear_hand()
        n         = len(_get_active_seated())
        new_phase = "between_hands" if n >= 2 else "waiting"
        _save_table(phase=new_phase)
        return {"action": "cleared_zero_pot", "players": len(players), "pot": pot, "next_phase": new_phase}

    # Pot > 0 and not all paid — cannot safely guess payouts
    return {"action": "pot_unresolved", "players": len(players), "pot": pot, "next_phase": "?"}


def _recovery_block_msg() -> Optional[str]:
    """Return a block message if poker is stuck and blocks player commands, else None."""
    broken = detect_poker_inconsistent_state()
    if broken and "finished_stuck" in broken:
        return "⚠️ Poker table recovering. Staff: !poker recoverystatus"
    return None


# ── Recovery log ───────────────────────────────────────────────────────────────

def _log_recovery(action: str, round_id: str, phase: str, details: str) -> None:
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO poker_recovery_logs (timestamp, action, round_id, phase, details) "
        "VALUES (?, ?, ?, ?, ?)",
        (_now(), action, round_id, phase, details),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Recovery recommendation — never creates circular command references
# ---------------------------------------------------------------------------

def get_poker_recovery_recommendation() -> str:
    """
    Returns the single safest recovery command for the current table state.
    Values: forcefinish | hardrefund | clearhand | closeforce | no_action

    Rules (strictly non-circular):
    - Active hand + valid hole cards + 2+ eligible players → forcefinish
    - Pot > 0 (any stuck state, cards missing/corrupted)   → hardrefund
    - No pot + stuck hand rows                             → clearhand
    - No seated players at all                             → closeforce
    - Table idle / waiting / between_hands                 → no_action
    """
    try:
        tbl = _get_table()
        if not tbl or not tbl["active"]:
            return "no_action"
        phase    = tbl["phase"]
        round_id = tbl.get("round_id")
        pot      = int(tbl.get("pot") or 0)
        if phase in ("idle", "waiting", "between_hands"):
            return "no_action"
        # Finished phase with stuck hand rows → safe to clear
        if phase == "finished":
            return "clearhand"
        players  = _get_players(round_id) if round_id else []
        unpaid   = [p for p in players if not _is_paid(round_id, p["username"])]
        eligible = _eligible_players(unpaid) if unpaid else _eligible_players(players)
        community: list = []
        try:
            community = json.loads(tbl.get("community_cards_json") or "[]")
        except Exception:
            pass
        valid_cards = bool(
            eligible and
            all(len(json.loads(p.get("hole_cards_json") or "[]")) == 2
                for p in eligible)
        )
        # No rows or all already paid
        if not players or not unpaid:
            return "clearhand" if pot == 0 else "hardrefund"
        # No pot: safe to clear hand rows
        if pot == 0:
            return "clearhand"
        # Active-hand phases with valid hole cards and 2+ eligible: forcefinish works
        if (phase in ("preflop", "flop", "turn", "river")
                and valid_cards and len(eligible) >= 2):
            return "forcefinish"
        # Pot > 0: if contribution records exist, regular refund is safer
        if round_id and unpaid:
            has_contribs = all(int(p.get("total_contributed") or 0) > 0
                               for p in unpaid)
            if has_contribs:
                return "refund"
        return "hardrefund"
    except Exception:
        return "hardrefund"


# ---------------------------------------------------------------------------
# _hard_refund_hand — core emergency logic for /poker hardrefund & closeforce
# ---------------------------------------------------------------------------

async def _hard_refund_hand(actor_username: str) -> str:
    """
    Emergency pot resolution when normal refund/forcefinish cannot work.
    Returns a ≤249-char result message.

    Priority:
    1. Contribution-based proportional split (most fair, uses total_contributed)
    2. Equal split among eligible/active in-hand players
    3. Equal split among all seated players (no in-hand rows)
    4. Pot logged as lost + table cleared (no players at all)
    """
    global _lobby_task, _turn_task, _next_hand_task
    _cancel_task(_lobby_task);     _lobby_task     = None
    _cancel_task(_turn_task);      _turn_task      = None
    _cancel_task(_next_hand_task); _next_hand_task = None

    tbl = _get_table()
    if not tbl or not tbl["active"]:
        return "No active table."

    round_id = tbl.get("round_id")
    pot      = int(tbl.get("pot") or 0)
    phase    = tbl.get("phase", "?")

    # ── Case 1: No pot — just clear ──────────────────────────────────────────
    if pot == 0:
        if round_id:
            _clear_players(round_id)
        _clear_hand()
        n = len(_get_active_seated())
        _save_table(phase="between_hands" if n >= 2 else "waiting")
        _log_recovery("hardrefund_zero_pot", round_id or "", phase,
                      f"by @{actor_username} | pot=0")
        return "✅ Poker hand cleared (pot was 0)."

    # ── Case 2: In-hand player rows exist ────────────────────────────────────
    players = _get_players(round_id) if round_id else []
    unpaid  = [p for p in players if not _is_paid(round_id, p["username"])]

    if unpaid:
        total_contrib = sum(int(p.get("total_contributed") or 0) for p in unpaid)
        if total_contrib > 0:
            # Proportional refund based on recorded contribution amounts
            paid_so_far = 0
            for i, p in enumerate(unpaid):
                contrib = int(p.get("total_contributed") or 0)
                if i == len(unpaid) - 1:
                    share = pot - paid_so_far          # last player absorbs rounding
                else:
                    share = round(pot * contrib / total_contrib)
                    paid_so_far += share
                _pay_seated(p, "hardrefund_contrib", share, round_id)
            details = f"contrib-split {pot} 🪙/{len(unpaid)}p"
        else:
            # No contribution data recorded — equal split
            share     = pot // len(unpaid)
            remainder = pot % len(unpaid)
            for i, p in enumerate(unpaid):
                s = share + (remainder if i == 0 else 0)
                _pay_seated(p, "hardrefund_equal", s, round_id)
            details = f"equal-split {pot} 🪙/{len(unpaid)}p"
        if round_id:
            _clear_players(round_id)
        _clear_hand()
        n = len(_get_active_seated())
        _save_table(phase="between_hands" if n >= 2 else "waiting")
        _log_recovery("hardrefund", round_id or "", phase,
                      f"by @{actor_username} | pot={pot} 🪙 | {details}")
        return f"✅ Hard refund: {pot} 🪙 cleared. {details}."[:249]

    # ── Case 3: No in-hand rows — split pot among all seated players ─────────
    seated = _get_all_seated()
    if seated:
        share     = pot // len(seated)
        remainder = pot % len(seated)
        for i, s in enumerate(seated):
            amt       = share + (remainder if i == 0 else 0)
            new_stack = s["table_stack"] + amt
            _update_seated(s["username"], table_stack=new_stack)
        if round_id:
            _clear_players(round_id)
        _clear_hand()
        n = len(_get_active_seated())
        _save_table(phase="between_hands" if n >= 2 else "waiting")
        _log_recovery("hardrefund_seated", round_id or "", phase,
                      f"by @{actor_username} | pot={pot} 🪙 split among {len(seated)} seated")
        return f"✅ Hard refund: {pot} 🪙 split among {len(seated)} seated."[:249]

    # ── Case 4: No players at all — log pot as lost and clear ────────────────
    if round_id:
        _clear_players(round_id)
    _clear_hand()
    n = len(_get_active_seated())
    _save_table(phase="between_hands" if n >= 2 else "waiting")
    _log_recovery("hardrefund_pot_lost", round_id or "", phase,
                  f"by @{actor_username} | pot={pot} 🪙 — no players, logged")
    return f"⚠️ Pot {pot} 🪙 logged (no players). Table cleared."[:249]


# ── Daily limit check (called before join/rebuy) ────────────────────────────────

def _check_daily_limits(username: str, buyin: int) -> Optional[str]:
    win_en  = _s("win_limit_enabled",  1)
    loss_en = _s("loss_limit_enabled", 1)
    if not win_en and not loss_en:
        return None
    net = _get_daily_net(username)
    if win_en:
        limit = _s("table_daily_win_limit", 10000)
        if net >= limit:
            return "Poker win limit reached. Try tomorrow."
    if loss_en:
        limit = _s("table_daily_loss_limit", 5000)
        if net <= -limit:
            return "Poker loss limit reached. Try tomorrow."
        if net - buyin < -limit:
            return "Buy-in too high for daily loss limit."
    return None


# ── _pay_seated: update table_stack at hand end (NOT wallet) ──────────────────

def _pay_seated(p: dict, result: str, pot_share: int, round_id: str) -> int:
    """
    At hand end: credit player's final in-hand stack + pot share back
    to their table_stack. Does NOT touch the wallet.
    Returns the new table_stack amount.
    """
    if _is_paid(round_id, p["username"]):
        sp = _get_seated(p["username"])
        return sp["table_stack"] if sp else 0
    final = p["stack"] + pot_share
    net   = final - p["buyin"]
    _upsert_result(round_id, p["username"], p["buyin"], result, final, net)
    _update_seated(p["username"], table_stack=final)
    _add_daily_net(p["username"], net)
    _mark_paid(round_id, p["username"])
    return final


def _refund_wallet(p: dict, round_id: str, note: str = "refund") -> int:
    """
    Emergency: return remaining in-hand stack to wallet (bypasses table_stack).
    Also clears seated record if present.
    """
    if _is_paid(round_id, p["username"]):
        return 0
    total = p["stack"]
    net   = total - p["buyin"]
    _upsert_result(round_id, p["username"], p["buyin"], note, total, net)
    if total > 0:
        db.adjust_balance(p["user_id"], total)
        db.add_ledger_entry(
            p["user_id"], p["username"],
            total, f"Poker {note} rid={round_id}"
        )
    _mark_paid(round_id, p["username"])
    return total


# ── finish_poker_hand ──────────────────────────────────────────────────────────

async def finish_poker_hand(bot: BaseBot, reason: str) -> None:
    """
    End the current hand. Updates table_stacks (not wallets), processes
    leavers/busted players, then schedules the next hand or goes to waiting.
    Idempotent per round_id.
    """
    global _lobby_task, _turn_task, _next_hand_task
    _cancel_task(_lobby_task);     _lobby_task     = None
    _cancel_task(_turn_task);      _turn_task      = None
    _cancel_task(_next_hand_task); _next_hand_task = None

    tbl = _get_table()
    if not tbl or not tbl["active"] or tbl["phase"] in (
            "idle", "finished", "waiting", "between_hands"):
        return

    round_id  = tbl["round_id"]
    if not round_id:
        return

    phase     = tbl["phase"]
    pot       = tbl["pot"]
    community = json.loads(tbl["community_cards_json"] or "[]")
    players   = _get_players(round_id)

    _save_table(phase="finished")
    print(f"[POKER CLEANUP] phase=finished round={round_id} reason={reason}")

    # ── Determine winner(s) and update table_stacks ──────────────────────────

    if reason in ("everyone_folded", "not_enough_players", "cancelled",
                  "forcefinish", "recovery_refund"):
        eligible = _eligible_players(players)
        if reason == "everyone_folded" and len(eligible) == 1:
            w    = eligible[0]
            _pay_seated(w, "win_folds", pot, round_id)
            for p in players:
                if p["username"] != w["username"]:
                    _pay_seated(p, "fold_return", 0, round_id)
            await _chat(bot,
                f"{_PK_WIN} — {_pdn(w)} wins {pot:,} 🪙. Everyone else folded.")
            db.update_poker_stats(
                w["user_id"], w["username"],
                wins=1, total_won=pot, biggest_pot=pot,
                biggest_win=pot, net_delta=pot,
                total_buyin=w["buyin"], hands=1,
            )
            for p in players:
                if p["username"] != w["username"]:
                    db.update_poker_stats(
                        p["user_id"], p["username"],
                        losses=1, total_lost=p["total_contributed"],
                        folds=int(p["status"] == "folded"),
                        net_delta=-p["total_contributed"],
                        total_buyin=p["buyin"], hands=1,
                    )
        else:
            for p in players:
                _pay_seated(p, "refund", 0, round_id)
            if reason in ("cancelled", "recovery_refund"):
                await _chat(bot,
                    "♠️ Hand cancelled. Chips returned to stacks.")
            elif reason == "not_enough_players":
                await _chat(bot,
                    "♠️ Need 2+ players for poker. Chips returned to stacks.")

    elif reason == "showdown":
        eligible = _eligible_players(players)
        if len(eligible) == 1:
            await finish_poker_hand(bot, "everyone_folded")
            return
        if len(eligible) == 0:
            for p in players:
                _pay_seated(p, "refund", 0, round_id)
        else:
            scores = {
                p["username"]: _best_hand(
                    json.loads(p["hole_cards_json"] or "[]") + community
                )
                for p in eligible
                if len(json.loads(p["hole_cards_json"] or "[]")) == 2
            }
            if not scores:
                for p in players:
                    _pay_seated(p, "refund", 0, round_id)
            else:
                best      = max(scores.values())
                winners   = [p for p in eligible if scores.get(p["username"]) == best]
                share     = pot // len(winners)
                remainder = pot % len(winners)
                winner_set = {w["username"] for w in winners}

                for i, w in enumerate(winners):
                    pot_share = share + (remainder if i == 0 else 0)
                    _pay_seated(w, "win_showdown", pot_share, round_id)

                for p in players:
                    if p["username"] not in winner_set:
                        stat   = p["status"]
                        result = "fold_return" if stat == "folded" else "loss_showdown"
                        _pay_seated(p, result, 0, round_id)

                board  = _fcs(community)
                hname  = _HAND_NAMES.get(best[0], "High Card")
                # Step 1: Announce showdown + board
                await _chat(bot, f"👀 Showdown! Board: {board}")
                # Step 2: Show each eligible player's hole cards + hand rank
                for p in eligible:
                    h      = _fcs(json.loads(p["hole_cards_json"] or "[]"))
                    hn_p   = _HAND_NAMES.get(scores.get(p["username"], (0,))[0], "High Card")
                    p_disp = db.get_display_name(p["user_id"], p["username"])
                    await _chat(bot, f"@{p['username']}: {h} — {hn_p}")
                # Step 3: Announce winner(s)
                if len(winners) == 1:
                    w = winners[0]
                    w_disp = db.get_display_name(w["user_id"], w["username"])
                    await _chat(bot, f"{_PK_WIN} {w_disp} wins {pot:,} 🪙 with {hname}.")
                    print(f"[POKER SHOWDOWN] winner=@{w['username']} amount={pot} hand={hname}")
                else:
                    wnames = " & ".join(
                        db.get_display_name(w["user_id"], w["username"])
                        for w in winners
                    )
                    await _chat(bot,
                        f"🤝 Split: {wnames} each get {share:,} 🪙. {hname}.")
                    print(f"[POKER SHOWDOWN] split winners={[w['username'] for w in winners]} share={share} hand={hname}")

                for p in players:
                    won     = p["username"] in winner_set
                    folded  = p["status"] == "folded"
                    is_elig = p["username"] in scores
                    pot_won = share if won else 0
                    net_d   = (pot_won - p["total_contributed"]
                               ) if won else -p["total_contributed"]
                    db.update_poker_stats(
                        p["user_id"], p["username"],
                        wins=int(won),
                        losses=int(not won and is_elig),
                        folds=int(folded),
                        showdowns=int(is_elig),
                        total_won=pot_won,
                        total_lost=p["total_contributed"] if not won else 0,
                        total_buyin=p["buyin"],
                        biggest_pot=pot if won else 0,
                        biggest_win=max(0, net_d) if won else 0,
                        net_delta=net_d,
                        hands=1,
                    )

    # ── Clear per-hand records ───────────────────────────────────────────────
    _clear_players(round_id)
    _clear_hand()

    # ── Reload updated seated players ────────────────────────────────────────
    seated_all = _get_all_seated()

    # ── Process busted players (table_stack == 0) ────────────────────────────
    for s in seated_all:
        if s["table_stack"] == 0:
            _remove_seated(s["username"])
            s_disp = db.get_display_name(s["user_id"], s["username"])
            await _chat(bot, f"💸 {s_disp} busted out of the table.")

    # ── Process leaving players ──────────────────────────────────────────────
    seated_all = _get_all_seated()
    for s in seated_all:
        if s.get("leaving_after_hand", 0):
            stack = s["table_stack"]
            _remove_seated(s["username"])
            if stack > 0:
                db.adjust_balance(s["user_id"], stack)
                db.add_ledger_entry(
                    s["user_id"], s["username"],
                    stack, f"Poker cash-out rid={round_id}"
                )
            s_disp = db.get_display_name(s["user_id"], s["username"])
            await _chat(bot,
                f"👋 {s_disp} cashed out {stack} 🪙 from poker.")

    # ── Show current stacks ──────────────────────────────────────────────────
    remaining = _get_all_seated()
    if remaining:
        stack_str = "  ".join(
            f"@{s['username']}:{s['table_stack']} 🪙" for s in remaining
        )
        await _chat(bot, f"♠️ Stacks: {stack_str[:200]}")

    # ── Decide what happens next ─────────────────────────────────────────────
    closing    = _s("table_closing", 0)
    auto_start = _s("auto_start_next_hand", 1)
    min_pl     = _s("min_players", 2)
    active     = _get_active_seated()

    if closing or not remaining:
        # Return any leftover stacks and fully close
        if closing and remaining:
            for s in remaining:
                if s["table_stack"] > 0:
                    db.adjust_balance(s["user_id"], s["table_stack"])
                    db.add_ledger_entry(
                        s["user_id"], s["username"],
                        s["table_stack"], f"Poker table-close cash-out"
                    )
                    await _chat(bot,
                        f"👋 {_pdn(s)} cashed out {s['table_stack']} 🪙.")
        _set("table_closing", 0)
        _full_clear_table()
        await _chat(bot, "♠️ Poker table closed. !join <amount> to open.")
        return

    if len(active) < min_pl:
        _save_table(phase="waiting")
        need = min_pl - len(active)
        await _chat(bot,
            f"♠️ Waiting for {need} more player(s) to start next hand.")
        return

    if auto_start:
        delay = _s("next_hand_delay", 10)
        _save_table(phase="between_hands", next_hand_starts_at=_now())
        await _chat(bot, f"♠️ Next hand in {delay}s. !sitout to sit out.")
        _next_hand_task = asyncio.create_task(
            _next_hand_countdown(bot, delay)
        )
    else:
        _save_table(phase="waiting")
        await _chat(bot, "♠️ Hand done. Managers: !poker start for next hand.")


# ── Next-hand countdown ────────────────────────────────────────────────────────

async def _next_hand_countdown(bot: BaseBot, delay: int) -> None:
    global _next_hand_task
    await asyncio.sleep(delay)
    tbl = _get_table()
    if not tbl or not tbl["active"] or tbl["phase"] != "between_hands":
        return
    await _try_start_hand(bot)


async def _deliver_poker_cards_to_all(
    bot: "BaseBot",
    round_id: str,
    hand_num: int,
    only_usernames: set[str] | None = None,
) -> tuple[int, list[str]]:
    """Whisper hole cards to every in-hand player for round_id.

    Loads cards from poker_hole_cards (normalized-username lookup), falls back
    to hole_cards_json in poker_active_players.  Updates poker_card_delivery
    for every attempt.

    Args:
        only_usernames: if provided, only deliver to players in this set
                        (used for targeted retry).
    Returns:
        (sent_count, list_of_failed_usernames)
    """
    players    = _get_players(round_id)
    sent_count = 0
    failed: list[str] = []

    for p in players:
        if p["status"] not in ("active", "allin"):
            continue
        if only_usernames and p["username"].lower() not in {
                u.lower() for u in only_usernames}:
            continue

        # Load cards: poker_hole_cards first, then fallback
        hc = db.get_hole_cards(round_id, p["username"])
        if hc and hc.get("card1") and hc.get("card2"):
            cards = [hc["card1"], hc["card2"]]
        else:
            raw = json.loads(p.get("hole_cards_json") or "[]")
            if len(raw) == 2:
                cards = raw
            else:
                db.record_card_delivery(
                    round_id, p["username"], False,
                    "no_saved_cards", p["username"]
                )
                failed.append(p["username"])
                print(f"[POKER] No saved cards for @{p['username']} "
                      f"round={round_id[:8]}")
                continue

        msg = _fmt_private_hand(cards, p["stack"], hand_num=hand_num)
        delivered   = False
        fail_reason = ""
        try:
            await bot.highrise.send_whisper(p["user_id"], msg[:249])
            delivered = True
        except Exception as _de:
            fail_reason = str(_de)[:80]

        db.record_card_delivery(
            round_id, p["username"], delivered, fail_reason, p["username"]
        )
        if delivered:
            sent_count += 1
        else:
            failed.append(p["username"])
            print(f"[POKER] Card delivery FAILED @{p['username']}: {fail_reason}")

    return sent_count, failed


async def _deliver_cards_sequential(
    bot: "BaseBot",
    round_id: str,
    hand_num: int,
    player_rows: list[dict],
    sb_name: str = "",
    bb_name: str = "",
) -> tuple[int, list[str]]:
    """Simulate a dealer dealing hole cards one-by-one with room announcements.

    Announces 'Drawing cards...' then 'Dealing to @Player...' before each
    whisper. Uses pace_deal_delay_secs between players. Retries failures once.
    SB and BB are included in the deal loop; posting blinds does NOT skip them.
    Returns (sent_count, still_failed_usernames).
    """
    try:
        deal_delay = float(_s_str("pace_deal_delay_secs", "0.7"))
    except Exception:
        deal_delay = 0.7
    deal_delay = max(0.1, min(deal_delay, 3.0))

    active_rows = [pr for pr in player_rows if pr["status"] in ("active", "allin")]
    print(f"[POKER CARDS] hand_id={round_id[-8:]} sending_to={len(active_rows)}")
    print(f"[POKER_DEAL] hand={round_id[-8:]} | players={len(active_rows)} | delay={deal_delay}s | sb={sb_name} bb={bb_name}")

    await _chat(bot, f"{_PK_DEALER}: Drawing cards...")
    await asyncio.sleep(0.4)

    sent_count = 0
    failed: list[str] = []

    for pr in active_rows:
        pr_disp = db.get_display_name_by_username(pr["username"])
        await _chat(bot, f"{_PK_DEALER}: Dealing to {pr_disp}...")
        await asyncio.sleep(deal_delay)

        hc = db.get_hole_cards(round_id, pr["username"])
        if hc and hc.get("card1") and hc.get("card2"):
            cards = [hc["card1"], hc["card2"]]
        else:
            cards = json.loads(pr.get("hole_cards_json") or "[]")
        if len(cards) != 2:
            db.record_card_delivery(round_id, pr["username"], False, "no_cards", pr["username"])
            failed.append(pr["username"])
            print(f"[POKER_DEAL] dealing_to={pr['username']} whisper_ok=false reason=no_cards")
            if sb_name and pr["username"].lower() == sb_name.lower():
                print(f"[POKER_DEAL] small_blind={pr['username']} delivered=false reason=no_cards")
            if bb_name and pr["username"].lower() == bb_name.lower():
                print(f"[POKER_DEAL] big_blind={pr['username']} delivered=false reason=no_cards")
            continue

        msg = _fmt_private_hand(cards, pr["stack"], hand_num=hand_num)
        delivered = False
        fail_reason = ""
        try:
            await bot.highrise.send_whisper(pr["user_id"], msg[:249])
            delivered = True
        except Exception as exc:
            fail_reason = str(exc)[:80]

        db.record_card_delivery(round_id, pr["username"], delivered, fail_reason, pr["username"])
        print(f"[POKER_DEAL] dealing_to={pr['username']} whisper_ok={delivered} attempts=1")
        if sb_name and pr["username"].lower() == sb_name.lower():
            print(f"[POKER_DEAL] small_blind={pr['username']} delivered={str(delivered).lower()}")
        if bb_name and pr["username"].lower() == bb_name.lower():
            print(f"[POKER_DEAL] big_blind={pr['username']} delivered={str(delivered).lower()}")
        if delivered:
            sent_count += 1
            print(f"[POKER CARDS] user=@{pr['username']} cards_sent=true")
        else:
            failed.append(pr["username"])
            print(f"[POKER CARDS] user=@{pr['username']} cards_sent=false error={fail_reason}")

    # Retry pass — one more attempt for any failures
    if failed:
        await asyncio.sleep(1.2)
        still_missing: list[str] = []
        for uname in failed:
            pr = next((r for r in active_rows if r["username"].lower() == uname.lower()), None)
            if not pr:
                still_missing.append(uname)
                continue
            hc = db.get_hole_cards(round_id, pr["username"])
            if hc and hc.get("card1") and hc.get("card2"):
                cards = [hc["card1"], hc["card2"]]
            else:
                cards = json.loads(pr.get("hole_cards_json") or "[]")
            msg = _fmt_private_hand(cards, pr["stack"], hand_num=hand_num)
            try:
                await bot.highrise.send_whisper(pr["user_id"], msg[:249])
                db.record_card_delivery(round_id, pr["username"], True, "", pr["username"])
                sent_count += 1
                print(f"[POKER_DEAL] dealing_to={uname} whisper_ok=true attempts=2")
            except Exception as exc:
                still_missing.append(uname)
                print(f"[POKER_DEAL] dealing_to={uname} whisper_ok=false attempts=2 err={str(exc)[:40]}")
                try:
                    await bot.highrise.send_whisper(pr["user_id"], "⚠️ Cards ready. Type !ph to view.")
                except Exception:
                    pass
        failed = still_missing

    return sent_count, failed


async def _try_start_hand(bot: BaseBot) -> None:
    """Check conditions and start a new hand if possible."""
    global _next_hand_task
    _cancel_task(_next_hand_task); _next_hand_task = None

    tbl = _get_table()
    if not tbl or not tbl["active"]:
        return
    if tbl["phase"] in ("preflop", "flop", "turn", "river", "finished"):
        return  # hand already running

    active = _get_active_seated()
    min_pl = _s("min_players", 2)
    if len(active) < min_pl:
        _save_table(phase="waiting")
        await _chat(bot,
            f"♠️ Need {min_pl} players. {len(active)} ready. Join: !p <amt>")
        return

    await _start_hand(bot)


# ── Start a new hand from seated players ──────────────────────────────────────

async def _start_hand(bot: BaseBot) -> None:
    """Deal a new hand from the current seated roster.

    Fix (Part 1-4): Pre-compute ALL player data in Python first, then write
    every player in a SINGLE transaction with hole_cards_json already set.
    This eliminates the INSERT-then-UPDATE race that caused player 2+
    to silently receive no cards.
    """
    global _next_hand_task, _lobby_task
    _cancel_task(_next_hand_task); _next_hand_task = None
    _cancel_task(_lobby_task);     _lobby_task     = None

    tbl = _get_table()
    if not tbl:
        return

    # ── Part 1: select active seated players ──────────────────────────────
    active = _get_active_seated()   # status='seated' AND table_stack > 0
    max_pl = _s("max_players", 6)
    min_pl = _s("min_players", 2)

    if len(active) < min_pl:
        _save_table(phase="waiting")
        await _chat(bot, "♠️ Poker waiting for 2+ active players.")
        return

    seated = active[:max_pl]
    n      = len(seated)
    print(f"[POKER SEQ] hand_start=true players={n}")

    # ── Rotate dealer button ───────────────────────────────────────────────
    prev_dealer = tbl.get("dealer_button_index") or 0
    dealer_idx  = (prev_dealer + 1) % n

    # ── Blind positions ────────────────────────────────────────────────────
    if n == 2:
        sb_idx            = dealer_idx
        bb_idx            = (dealer_idx + 1) % 2
        first_preflop_idx = dealer_idx      # SB acts first HU preflop
        first_postflop    = bb_idx
    else:
        sb_idx            = (dealer_idx + 1) % n
        bb_idx            = (dealer_idx + 2) % n
        first_preflop_idx = (dealer_idx + 3) % n  # UTG
        first_postflop    = sb_idx

    blinds_on = _s("blinds_enabled", 1)
    sb_amt    = _s("small_blind",  50) if blinds_on else 0
    bb_amt    = _s("big_blind",   100) if blinds_on else 0
    ante_amt  = _s("ante",          0) if blinds_on else 0

    round_id = _new_round_id()
    hand_num = (tbl.get("hand_number") or 0) + 1

    # ── Part 2: PRE-COMPUTE every player's full row before any DB write ────
    deck = _make_deck()
    random.shuffle(deck)

    initial_pot: int = 0
    table_bet:   int = 0
    player_rows: list[dict] = []

    for i, sp in enumerate(seated):
        stack = sp["table_stack"]

        # Ante
        ante_contrib = 0
        if ante_amt > 0:
            ante_contrib = min(ante_amt, stack)
            stack       -= ante_contrib
            initial_pot += ante_contrib

        # Blind
        blind_contrib = 0
        if blinds_on:
            if i == sb_idx:
                blind_contrib = min(sb_amt, stack)
            elif i == bb_idx:
                blind_contrib = min(bb_amt, stack)
        stack       -= blind_contrib
        initial_pot += blind_contrib
        if blinds_on and i == bb_idx:
            table_bet = blind_contrib

        total_contrib = ante_contrib + blind_contrib
        status        = "allin" if stack == 0 else "active"
        # SB has posted and acted; BB keeps acted=0 so they get the option
        acted = 1 if (blinds_on and i == sb_idx and i != bb_idx) else 0

        # Deal exactly 2 cards
        h = [deck.pop(), deck.pop()]

        player_rows.append({
            "username":          sp["username"],
            "user_id":           sp["user_id"],
            "buyin":             sp["table_stack"],   # in-hand starting stack
            "stack":             stack,
            "current_bet":       blind_contrib,
            "total_contributed": total_contrib,
            "hole_cards_json":   json.dumps(h),
            "status":            status,
            "acted":             acted,
        })

    # ── SINGLE TRANSACTION: write all players atomically ──────────────────
    conn = db.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM poker_active_players WHERE round_id=?", (round_id,)
        )
        for pr in player_rows:
            conn.execute(
                "INSERT INTO poker_active_players "
                "(round_id, username, user_id, buyin, stack, current_bet, "
                "total_contributed, hole_cards_json, status, acted, joined_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    round_id,
                    pr["username"], pr["user_id"], pr["buyin"],
                    pr["stack"], pr["current_bet"], pr["total_contributed"],
                    pr["hole_cards_json"], pr["status"], pr["acted"], _now(),
                ),
            )
        conn.execute("COMMIT")
    except Exception as exc:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        print(f"[POKER] _start_hand DB error: {exc}")
        _save_table(phase="recovery_required")
        await _chat(bot, "⚠️ Poker deal error. Use !poker recoverystatus for fix.")
        return
    conn.close()

    # ── Save to poker_hole_cards (normalized-key lookup for /ph / resend) ─
    for pr in player_rows:
        h = json.loads(pr["hole_cards_json"])
        db.save_hole_cards(round_id, pr["username"].lower(),
                           pr["username"], h[0], h[1])

    # ── Create skeleton delivery rows BEFORE whispers ────────────────────
    for pr in player_rows:
        db.ensure_delivery_row(round_id, pr["username"].lower(), pr["username"])
    print(f"[POKER] Card delivery rows created: {len(player_rows)}")

    # ── Part 3: VALIDATE every player has exactly 2 cards ─────────────────
    err = _validate_deal(round_id, len(seated))
    if err:
        print(f"[POKER] deal validation FAILED: {err}")
        _clear_players(round_id)
        _save_table(phase="recovery_required")
        await _chat(bot, f"⚠️ Deal error: {err} | Use !poker recoverystatus.")
        return

    # ── Save table state (before whispering cards) ─────────────────────────
    sb_name = seated[sb_idx]["username"] if blinds_on else ""
    bb_name = seated[bb_idx]["username"] if blinds_on else ""
    dl_name = seated[dealer_idx]["username"]

    _save_table(
        active=1,
        phase="preflop",
        round_id=round_id,
        created_at=_now(),
        round_started_at=_now(),
        dealer_button_index=dealer_idx,
        small_blind_username=sb_name,
        big_blind_username=bb_name,
        deck_json=json.dumps(deck),
        community_cards_json="[]",
        pot=initial_pot,
        current_bet=table_bet,
        current_player_index=first_preflop_idx,
        restored_after_restart=0,
        hand_number=hand_num,
        next_hand_starts_at=None,
    )

    # ── Announce hand start (before dealing so players know what's coming) ──
    if blinds_on:
        _sb_disp = db.get_display_name(seated[sb_idx]["user_id"], sb_name)
        _bb_disp = db.get_display_name(seated[bb_idx]["user_id"], bb_name)
        await _chat(bot, (
            f"{_PK_DEALER}: Hand #{hand_num} | "
            f"{_PK_BLIND} SB:{_sb_disp}({sb_amt} 🪙) BB:{_bb_disp}({bb_amt} 🪙) | "
            f"{_PK_POT}:{initial_pot:,} 🪙")[:249])
        print(f"[POKER SEQ] blinds_announced=true sb={sb_amt} bb={bb_amt}")
    else:
        order_str = "  ".join(f"@{sp['username']}" for sp in seated)
        await _chat(bot, f"{_PK_DEALER}: Hand #{hand_num} | {order_str[:110]}"[:249])
        print(f"[POKER SEQ] blinds_announced=false (blinds_off)")

    # ── Part 4: sequential card delivery with dealer simulation ───────────
    sent_count, failed_players = await _deliver_cards_sequential(
        bot, round_id, hand_num, player_rows,
        sb_name=sb_name, bb_name=bb_name,
    )
    if failed_players:
        print(f"[POKER_DEAL] still failed after retry: {', '.join(failed_players)}")

    # ── Part 7: verify hole_cards saved; re-deliver if any missed ──────────
    _players_now = _get_players(round_id)
    for _pl in _players_now:
        if _pl["status"] not in ("active", "allin"):
            continue
        if not db.get_hole_cards(round_id, _pl["username"].lower()):
            raw = json.loads(_pl.get("hole_cards_json") or "[]")
            if len(raw) == 2:
                db.save_hole_cards(round_id, _pl["username"].lower(),
                                   _pl["username"], raw[0], raw[1])
                print(f"[POKER] Part7: rebuilt hole_cards for @{_pl['username']}")
    delivery_rows  = db.get_card_delivery_status(round_id)
    delivered_keys = {r["username"] for r in delivery_rows if r["cards_sent"]}
    need_deliver   = {
        pl["username"] for pl in _players_now
        if pl["status"] in ("active", "allin")
        and pl["username"].lower() not in delivered_keys
    }
    if need_deliver:
        await _deliver_poker_cards_to_all(
            bot, round_id, hand_num, only_usernames=need_deliver
        )

    # ── 5-second window: all whispers settle, then resend to first actor ──
    print(f"[POKER SEQ] waiting_before_first_turn=5s")
    await asyncio.sleep(5.0)

    # ── Guaranteed first-actor card whisper ────────────────────────────────
    players = _get_players(round_id)
    _first_actor_pl: dict | None = None
    for _fi in range(len(players)):
        _fidx = (first_preflop_idx + _fi) % len(players) if players else 0
        _fc_candidate = players[_fidx]
        if _fc_candidate["status"] == "active":
            _first_actor_pl = _fc_candidate
            break
    if _first_actor_pl:
        _fa_hc = db.get_hole_cards(round_id, _first_actor_pl["username"])
        if _fa_hc and _fa_hc.get("card1") and _fa_hc.get("card2"):
            _fa_cards = [_fa_hc["card1"], _fa_hc["card2"]]
        else:
            _fa_cards = json.loads(_first_actor_pl.get("hole_cards_json") or "[]")
        if len(_fa_cards) == 2:
            _fa_owe = max(0, tbl.get("current_bet", 0) -
                          _first_actor_pl.get("current_bet", 0))
            if _fa_owe > 0:
                _fa_acts = f"!call {_fa_owe:,}, !raise or !fold"
            else:
                _fa_acts = "!check, !raise or !fold"
            _fa_msg = (f"🎯 Your turn. {_PK_CARDS}: {_fcs(_fa_cards)} | "
                       f"{_fa_acts}")[:249]
            try:
                await bot.highrise.send_whisper(_first_actor_pl["user_id"], _fa_msg)
                print(f"[POKER FIRST TURN] delay=5s "
                      f"first_actor=@{_first_actor_pl['username']} cards_resend=true")
            except Exception as _fa_exc:
                print(f"[POKER FIRST TURN] delay=5s "
                      f"first_actor=@{_first_actor_pl['username']} cards_resend=false "
                      f"err={str(_fa_exc)[:60]}")
        else:
            try:
                await bot.highrise.send_whisper(
                    _first_actor_pl["user_id"],
                    "🎯 Your turn, but your cards were not found. "
                    "Type !cards or ask staff.")
            except Exception:
                pass
            print(f"[POKER FIRST TURN] delay=5s "
                  f"first_actor=@{_first_actor_pl['username']} cards_resend=false "
                  f"reason=no_cards")

    # ── Start preflop ──────────────────────────────────────────────────────
    tbl_new = _get_table()
    if tbl_new:
        await _start_street_from(
            bot, "preflop", round_id, players, first_preflop_idx
        )

    # Increment hands_at_table for all participants
    conn = db.get_connection()
    for sp in seated:
        conn.execute(
            "UPDATE poker_seated_players SET hands_at_table=hands_at_table+1 "
            "WHERE LOWER(username)=LOWER(?)",
            (sp["username"],),
        )
    conn.commit()
    conn.close()


# ── advance_turn_or_round ──────────────────────────────────────────────────────

async def advance_turn_or_round(bot: BaseBot) -> None:
    global _turn_task
    _cancel_task(_turn_task); _turn_task = None

    tbl = _get_table()
    if not tbl or not tbl["active"] or tbl["phase"] not in (
            "preflop", "flop", "turn", "river"):
        return

    round_id  = tbl["round_id"]
    table_bet = tbl["current_bet"]
    players   = _get_players(round_id)
    eligible  = _eligible_players(players)
    can_act   = _active_players(players)

    if len(eligible) <= 1:
        await finish_poker_hand(bot, "everyone_folded")
        return

    if len(can_act) == 0:
        await _deal_to_showdown(bot, tbl)
        return

    n      = len(players)
    start  = tbl["current_player_index"]
    target = None
    for i in range(1, n + 1):
        idx = (start + i) % n
        p   = players[idx]
        if _needs_to_act(p, table_bet):
            target = (idx, p)
            break

    if target is not None:
        idx, p = target
        _save_table(current_player_index=idx)
        await _prompt_player(bot, tbl, p)
        return

    await _advance_street(bot, tbl, players)


async def _deal_to_showdown(bot: BaseBot, tbl: dict) -> None:
    deck      = json.loads(tbl.get("deck_json") or "[]")
    community = json.loads(tbl.get("community_cards_json") or "[]")

    needs_more = len(community) < 5
    if needs_more:
        await _chat(bot, "🔥 All-in! Running the board...")
        await asyncio.sleep(0.5)

    # Deal flop (3 cards) if not dealt yet
    if len(community) < 3:
        f: list[str] = []
        for _ in range(3):
            if deck:
                f.append(deck.pop())
        community.extend(f)
        _save_table(phase="flop",
                    deck_json=json.dumps(deck),
                    community_cards_json=json.dumps(community))
        await _chat(bot, f"🃏 Flop: {_fcs(f)}")
        await asyncio.sleep(1.0)

    # Deal turn (1 card) if not dealt yet
    if len(community) < 4:
        if deck:
            t = deck.pop()
            community.append(t)
            _save_table(phase="turn",
                        deck_json=json.dumps(deck),
                        community_cards_json=json.dumps(community))
            await _chat(bot, f"🃏 Turn: {_fc(t)}")
            await asyncio.sleep(1.0)

    # Deal river (1 card) if not dealt yet
    if len(community) < 5:
        if deck:
            r = deck.pop()
            community.append(r)
            _save_table(phase="river",
                        deck_json=json.dumps(deck),
                        community_cards_json=json.dumps(community))
            await _chat(bot, f"🃏 River: {_fc(r)}")
            await asyncio.sleep(1.0)

    print(f"[POKER BOARD] all-in showdown — board={_fcs(community)}")
    await finish_poker_hand(bot, "showdown")


async def _advance_street(bot: BaseBot, tbl: dict, players: list[dict]) -> None:
    phase    = tbl["phase"]
    round_id = tbl["round_id"]
    deck     = json.loads(tbl["deck_json"] or "[]")
    community = json.loads(tbl["community_cards_json"] or "[]")

    # Reset per-player bet/acted for new street
    conn = db.get_connection()
    conn.execute(
        "UPDATE poker_active_players SET current_bet=0, acted=0 "
        "WHERE round_id=? AND status='active'",
        (round_id,),
    )
    conn.commit()
    conn.close()
    _save_table(current_bet=0)

    # Compute post-flop start: first active player after dealer
    n          = len(players)
    dealer_idx = tbl.get("dealer_button_index") or 0
    pf_start   = (dealer_idx + 1) % n if n > 0 else 0

    if phase == "preflop":
        # Burn one card, then deal three for the flop
        if deck: deck.pop()
        await _chat(bot, f"{_PK_DEALER}: Burns a card...")
        await asyncio.sleep(0.4)
        f = [deck.pop(), deck.pop(), deck.pop()]
        community.extend(f)
        _save_table(phase="flop",
                    deck_json=json.dumps(deck),
                    community_cards_json=json.dumps(community))
        await _chat(bot, f"{_PK_CARDS}: Flop {_fcs(f)} | Pot: {tbl['pot']:,} 🪙")
        await _start_street_from(bot, "flop", round_id, players, pf_start)

    elif phase == "flop":
        # Burn one card, then deal the turn
        if deck: deck.pop()
        await _chat(bot, f"{_PK_DEALER}: Burns a card...")
        await asyncio.sleep(0.4)
        t = deck.pop()
        community.append(t)
        _save_table(phase="turn",
                    deck_json=json.dumps(deck),
                    community_cards_json=json.dumps(community))
        await _chat(bot, f"{_PK_CARDS}: Turn {_fc(t)} | Board: {_fcs(community)}")
        await _start_street_from(bot, "turn", round_id, players, pf_start)

    elif phase == "turn":
        # Burn one card, then deal the river
        if deck: deck.pop()
        await _chat(bot, f"{_PK_DEALER}: Burns a card...")
        await asyncio.sleep(0.4)
        r = deck.pop()
        community.append(r)
        _save_table(phase="river",
                    deck_json=json.dumps(deck),
                    community_cards_json=json.dumps(community))
        await _chat(bot, f"{_PK_CARDS}: River {_fc(r)} | Board: {_fcs(community)}")
        await _start_street_from(bot, "river", round_id, players, pf_start)

    elif phase == "river":
        await finish_poker_hand(bot, "showdown")


async def _start_street_from(bot: BaseBot, phase: str, round_id: str,
                              players: list[dict], search_from: int = 0) -> None:
    """Find the first active player starting from search_from and prompt them."""
    eligible = _eligible_players(players)
    if len(eligible) <= 1:
        await finish_poker_hand(bot, "everyone_folded")
        return
    can_act = _active_players(players)
    if len(can_act) == 0:
        tbl = _get_table()
        if tbl:
            await _deal_to_showdown(bot, tbl)
        return
    n = len(players)
    first_actor = True
    for i in range(n):
        idx = (search_from + i) % n
        p   = players[idx]
        if p["status"] == "active":
            _save_table(current_player_index=idx)
            tbl = _get_table()
            if tbl:
                print(f"[POKER CARDS] first_actor=@{p['username']} cards_verified=true")
                await _prompt_player(bot, tbl, p, is_first_actor=first_actor)
            return
        first_actor = False


# ── Prompt a player for their turn ─────────────────────────────────────────────

async def _prompt_player(bot: BaseBot, tbl: dict, p: dict,
                         is_first_actor: bool = False) -> None:
    global _turn_task
    _cancel_task(_turn_task)

    turn_secs = _s("turn_timer", 20)
    ends_at   = _now()
    _save_table(turn_ends_at=ends_at)

    owe   = max(0, tbl["current_bet"] - p["current_bet"])
    pot   = tbl["pot"]
    stack = p["stack"]

    # ── Public room announcement ──────────────────────────────────────────
    pub = f"⏳ @{p['username']}'s turn. {turn_secs}s"
    await _chat(bot, pub[:249])
    print(f"[POKER TURN PUBLIC] current=@{p['username']} timer={turn_secs} sent=true")

    # ── Private whispers (non-fatal: failures silently ignored) ──────────
    try:
        tbl2 = _get_table()
        if tbl2:
            pp = _get_player(tbl2["round_id"], p["user_id"])
            if pp:
                cards = json.loads(pp["hole_cards_json"] or "[]")
                board = json.loads(tbl2["community_cards_json"] or "[]")
                if cards:
                    # Whisper 1: cards + pot + action hint
                    _cards_str = _fcs(cards)
                    if stack < owe and owe > 0:
                        hdr = (f"🎯 Your turn. {_PK_CARDS}: {_cards_str} | "
                               f"Pot:{pot:,} 🪙 | Need {owe:,} 🪙 (!allin or !fold)")
                    elif owe > 0:
                        hdr = (f"🎯 Your turn. {_PK_CARDS}: {_cards_str} | "
                               f"Pot:{pot:,} 🪙 | Call:{owe:,} 🪙")
                    else:
                        hdr = (f"🎯 Your turn. {_PK_CARDS}: {_cards_str} | "
                               f"Pot:{pot:,} 🪙 | Use !check, !raise or !fold")
                    _w1_sent = False
                    try:
                        await bot.highrise.send_whisper(p["user_id"], hdr[:249])
                        _w1_sent = True
                    except Exception:
                        pass
                    print(f"[POKER TURN WHISPER] user=@{p['username']} "
                          f"cards_found=true sent={str(_w1_sent).lower()}")

                    # Whisper 2: cards + board + hand strength
                    if board:
                        strength = _hand_strength_label(cards, board)
                        draws    = _detect_draws(cards, board)
                        rank_lbl = strength + (" + " + draws if draws else "")
                        card_msg = (f"{_PK_CARDS}: {_fcs(cards)} | "
                                    f"Board: {_fcs(board)} | {rank_lbl}")
                    else:
                        card_msg = (f"{_PK_CARDS}: {_fcs(cards)} | "
                                    f"Pre-flop | Pot:{pot:,} 🪙")
                    try:
                        await bot.highrise.send_whisper(p["user_id"], card_msg[:249])
                    except Exception:
                        pass

                    # Whisper 3: action buttons
                    if stack < owe and owe > 0:
                        act = f"{_PK_ALLIN} !allin | {_PK_FOLD} !fold"
                    elif owe > 0:
                        act = (f"{_PK_CALL} !call | {_PK_RAISE} !raise | "
                               f"{_PK_FOLD} !fold | {_PK_ALLIN} !allin")
                    else:
                        act = (f"{_PK_CHECK} !check | {_PK_RAISE} !raise | "
                               f"{_PK_FOLD} !fold | {_PK_ALLIN} !allin")
                    try:
                        await bot.highrise.send_whisper(p["user_id"], act[:249])
                    except Exception:
                        pass
    except Exception:
        pass

    round_id   = tbl["round_id"]
    _turn_task = asyncio.create_task(
        _turn_timeout(bot, p["user_id"], p["username"], round_id, owe > 0)
    )


async def _turn_timeout(bot: BaseBot, uid: str, uname: str,
                         round_id: str, must_call: bool) -> None:
    secs = _s("turn_timer", 20)
    await asyncio.sleep(secs)

    tbl = _get_table()
    if not tbl or not tbl["active"] or tbl["round_id"] != round_id:
        return
    if tbl["phase"] not in ("preflop", "flop", "turn", "river"):
        return
    p = _get_player(round_id, uid)
    if p is None or p["status"] != "active":
        return

    _td    = _udn(uid, uname)
    sp     = _get_seated(uname)
    limit  = _s("idle_strikes_limit", 3)
    if must_call and tbl["current_bet"] > p["current_bet"]:
        _save_player(round_id, uid, status="folded", acted=1)
        strikes = (sp.get("idle_strikes", 0) if sp else 0) + 1
        if sp:
            _update_seated(uname, idle_strikes=strikes)
        if _s("autositout_enabled", 0) and strikes >= limit:
            if sp:
                _update_seated(uname, status="sitting_out", idle_strikes=0)
            await _chat(bot, f"⏰ @{uname} timed out. Removed from poker (AFK).")
        else:
            await _chat(bot,
                f"⏰ @{uname} timed out. AFK warning {strikes}/{limit}.")
    else:
        _save_player(round_id, uid, acted=1)
        strikes = (sp.get("idle_strikes", 0) if sp else 0) + 1
        if sp:
            _update_seated(uname, idle_strikes=strikes)
        if _s("autositout_enabled", 0) and strikes >= limit:
            if sp:
                _update_seated(uname, status="sitting_out", idle_strikes=0)
            await _chat(bot, f"⏰ @{uname} auto-checked (AFK). Removed from table.")
        else:
            await _chat(bot,
                f"⏰ @{uname} auto-checked (AFK warning {strikes}/{limit}).")

    await advance_turn_or_round(bot)


# ── Action implementations ─────────────────────────────────────────────────────

async def _do_check(bot: BaseBot, round_id: str, uid: str, uname: str) -> None:
    sp = _get_seated(uname)
    if sp:
        _update_seated(uname, idle_strikes=0)
    _save_player(round_id, uid, acted=1)
    await _chat(bot, f"{_PK_CHECK} — {_udn(uid, uname)}")
    await advance_turn_or_round(bot)


async def _do_call(bot: BaseBot, round_id: str, p: dict, tbl: dict) -> None:
    owe = tbl["current_bet"] - p["current_bet"]
    if owe <= 0:
        await _do_check(bot, round_id, p["user_id"], p["username"])
        return
    if p["stack"] <= 0:
        await _w(bot, p["user_id"], "Already all-in.")
        return
    _update_seated(p["username"], idle_strikes=0)
    if p["stack"] < owe:
        commit      = p["stack"]
        new_cbet    = p["current_bet"] + commit
        new_contrib = p["total_contributed"] + commit
        new_pot     = tbl["pot"] + commit
        _save_player(round_id, p["user_id"],
                     stack=0, current_bet=new_cbet,
                     total_contributed=new_contrib,
                     status="allin", acted=1, allin_amount=commit)
        _save_table(pot=new_pot)
        await _chat(bot,
            f"{_PK_ALLIN} — {_pdn(p)} calls all-in for {commit:,} 🪙 | {_PK_POT}: {new_pot:,} 🪙")
        await advance_turn_or_round(bot)
        return
    new_stack   = p["stack"] - owe
    new_cbet    = p["current_bet"] + owe
    new_contrib = p["total_contributed"] + owe
    new_pot     = tbl["pot"] + owe
    _save_player(round_id, p["user_id"],
                 stack=new_stack, current_bet=new_cbet,
                 total_contributed=new_contrib, acted=1)
    _save_table(pot=new_pot)
    await _chat(bot, f"{_PK_CALL} — {_pdn(p)} called {owe:,} 🪙 | {_PK_POT}: {new_pot:,} 🪙")
    await advance_turn_or_round(bot)


async def _do_raise(bot: BaseBot, round_id: str, p: dict, tbl: dict,
                    raise_by: int) -> None:
    min_r = _s("min_raise", 50)
    max_r = _s("max_raise", 1000)
    rl_on = _s("raise_limit_enabled", 1)
    if raise_by < min_r:
        await _w(bot, p["user_id"], f"Minimum raise is {min_r} 🪙.")
        return
    if rl_on and raise_by > max_r:
        await _w(bot, p["user_id"], f"Maximum raise is {max_r} 🪙.")
        return
    raise_to = tbl["current_bet"] + raise_by
    extra    = raise_to - p["current_bet"]
    if extra > p["stack"]:
        await _w(bot, p["user_id"], "Not enough chips for that raise.")
        return
    if extra == p["stack"]:
        await _do_allin(bot, round_id, p, tbl)
        return
    new_stack   = p["stack"] - extra
    new_contrib = p["total_contributed"] + extra
    new_pot     = tbl["pot"] + extra
    _save_player(round_id, p["user_id"],
                 stack=new_stack, current_bet=raise_to,
                 total_contributed=new_contrib, acted=1)
    _save_table(pot=new_pot, current_bet=raise_to,
                last_raiser_username=p["username"])
    conn = db.get_connection()
    conn.execute(
        "UPDATE poker_active_players SET acted=0 "
        "WHERE round_id=? AND status='active' AND user_id!=?",
        (round_id, p["user_id"]),
    )
    conn.commit()
    conn.close()
    _update_seated(p["username"], idle_strikes=0)
    await _chat(bot,
        f"{_PK_RAISE} — {_pdn(p)} +{raise_by:,} 🪙 | New bet: {raise_to:,} 🪙")
    await advance_turn_or_round(bot)


async def _do_fold(bot: BaseBot, round_id: str, p: dict) -> None:
    _save_player(round_id, p["user_id"], status="folded", acted=1)
    _update_seated(p["username"], idle_strikes=0)
    await _chat(bot, f"{_PK_FOLD} — {_pdn(p)}")
    await advance_turn_or_round(bot)


async def _do_allin(bot: BaseBot, round_id: str, p: dict, tbl: dict) -> None:
    if not _s("allin_enabled", 1):
        await _w(bot, p["user_id"], "All-in is currently disabled.")
        return
    if p["stack"] <= 0:
        await _w(bot, p["user_id"], "Already all-in.")
        return
    commit      = p["stack"]
    new_cbet    = p["current_bet"] + commit
    new_contrib = p["total_contributed"] + commit
    new_pot     = tbl["pot"] + commit
    _save_player(round_id, p["user_id"],
                 stack=0, current_bet=new_cbet,
                 total_contributed=new_contrib,
                 status="allin", acted=1, allin_amount=commit)
    _save_table(pot=new_pot)
    if new_cbet > tbl["current_bet"]:
        _save_table(current_bet=new_cbet, last_raiser_username=p["username"])
        conn = db.get_connection()
        conn.execute(
            "UPDATE poker_active_players SET acted=0 "
            "WHERE round_id=? AND status='active' AND user_id!=?",
            (round_id, p["user_id"]),
        )
        conn.commit()
        conn.close()
    _update_seated(p["username"], idle_strikes=0)
    await _chat(bot, f"{_PK_ALLIN} — {_pdn(p)} all-in {commit:,} 🪙 | {_PK_POT}: {new_pot:,} 🪙")
    db.update_poker_stats(p["user_id"], p["username"], allins=1)
    await advance_turn_or_round(bot)


# ── Hand strength / draws / odds ────────────────────────────────────────────────

def _hand_strength_label(hole_cards: list[str], community: list[str]) -> str:
    if not community:
        return "Pre-flop"  # never evaluate with phantom padding cards
    all_cards = hole_cards + community
    if len(all_cards) < 2:
        return "No cards"
    if len(all_cards) < 5:
        best: Optional[tuple] = None
        for n in range(2, min(5, len(all_cards) + 1)):
            for combo in combinations(all_cards, n):
                s = (_score5(list(combo)) if n == 5
                     else _score5(list(combo) + ["2c"] * (5 - n)))
                if best is None or s > best:
                    best = s
        score = best or (0,)
    else:
        score = _best_hand(all_cards)
    return _HAND_NAMES.get(score[0], "High Card")


def _detect_draws(hole_cards: list[str], community: list[str]) -> str:
    all_cards = hole_cards + community
    if len(all_cards) < 3:
        return ""
    draws: list[str] = []
    suit_cnt = Counter(c[1] for c in all_cards)
    if max(suit_cnt.values()) == 4:
        draws.append("Flush Draw")
    ranks = sorted(set(_RANKS.index(c[0]) for c in all_cards))
    for i in range(len(ranks) - 3):
        window = ranks[i:i + 4]
        if window[-1] - window[0] == 3:
            draws.append("Straight Draw")
            break
    return " + ".join(draws)


def _calc_odds(hole_cards: list[str], community: list[str],
               deck: list[str], opponents: int) -> int:
    if len(hole_cards) < 2 or opponents < 1:
        return -1
    needed   = 5 - len(community)
    rem_deck = [c for c in deck if c not in hole_cards and c not in community]
    if len(rem_deck) < needed + opponents * 2:
        return -1
    wins = 0
    sims = 200
    for _ in range(sims):
        shuffled    = rem_deck.copy()
        random.shuffle(shuffled)
        idx         = 0
        sim_board   = list(community) + shuffled[idx:idx + needed]
        idx        += needed
        my_score    = _best_hand(hole_cards + sim_board)
        beaten      = False
        for _opp in range(opponents):
            opp_cards = shuffled[idx:idx + 2]
            idx      += 2
            if len(opp_cards) < 2:
                break
            if _best_hand(opp_cards + sim_board) > my_score:
                beaten = True
                break
        if not beaten:
            wins += 1
    return wins * 100 // sims


# ── Command: /poker join ────────────────────────────────────────────────────────

async def _handle_join(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _s("poker_enabled", 1):
        await _w(bot, user.id, "Poker is currently disabled.")
        return

    if _s("table_closing", 0):
        await _w(bot, user.id, "Table is closing after this hand. Try again soon.")
        return

    # Check if already seated
    sp = _get_seated(user.username)
    if sp:
        await _w(bot, user.id,
            f"Already at table. Stack: {sp['table_stack']} 🪙. "
            f"!sitout !rebuy <amt> !poker leave")
        return

    if len(args) < 3 or not args[2].isdigit():
        await _w(bot, user.id, "Usage: !join <buyin>  E.g. !join 5000")
        return

    buyin  = int(args[2])
    min_b  = _s("min_buyin", 100)
    max_b  = _s("max_buyin", 5000)
    max_pl = _s("max_players", 6)

    if _s("buyin_limit_enabled", 0):
        if buyin < min_b:
            await _w(bot, user.id, f"Min buy-in is {min_b} 🪙.")
            return
        if buyin > max_b:
            await _w(bot, user.id, f"Max buy-in is {max_b:,} 🪙.")
            return
    elif buyin < 1:
        await _w(bot, user.id, "Buy-in must be at least 1c.")
        return

    # Check max stack limit
    if _s("max_stack_enabled", 0):
        max_stk = _s("max_table_stack", 100000)
        if buyin > max_stk:
            await _w(bot, user.id, f"Max stack is {max_stk:,} 🪙.")
            return

    # Check table not full
    current_count = _seated_count()
    if current_count >= max_pl:
        await _w(bot, user.id, f"Table full ({max_pl} players max).")
        return

    err = _check_daily_limits(user.username, buyin)
    if err:
        await _w(bot, user.id, err)
        return

    db.ensure_user(user.id, user.username)
    bal = db.get_balance(user.id)
    if bal < buyin:
        await _w(bot, user.id, f"Not enough coins. Balance: {bal} 🪙.")
        return

    db.adjust_balance(user.id, -buyin)
    db.add_ledger_entry(
        user.id, user.username, -buyin,
        "Poker buy-in to table_stack"
    )
    db.ensure_poker_stats(user.id, user.username)

    seat_num = _next_seat_number()
    _seat_player(user.username, user.id, buyin, seat_num)

    # Ensure table record is open
    tbl = _get_table()
    if not tbl or not tbl["active"]:
        _save_table(
            active=1,
            phase="waiting",
            created_at=_now(),
            hand_number=0,
            dealer_button_index=0,
        )

    count = _seated_count()
    tbl   = _get_table()
    phase = tbl["phase"] if tbl else "waiting"

    if phase in ("preflop", "flop", "turn", "river"):
        await _chat(bot,
            f"✅ @{user.username} joined table ({buyin} 🪙). "
            f"Sits next hand. Players:{count}/{max_pl}")
    else:
        await _chat(bot,
            f"✅ @{user.username} joined poker ({buyin} 🪙). "
            f"Stack:{buyin} 🪙 | Players:{count}/{max_pl}")
        min_pl = _s("min_players", 2)
        if count >= min_pl and phase in ("waiting", "between_hands", "idle"):
            delay = _s("next_hand_delay", 10)
            _save_table(phase="between_hands")
            await _chat(bot, f"♠️ Starting first hand in {delay}s!")
            global _next_hand_task
            _cancel_task(_next_hand_task)
            _next_hand_task = asyncio.create_task(
                _next_hand_countdown(bot, delay)
            )


# ── Command: /poker leave ───────────────────────────────────────────────────────

async def _handle_leave(bot: BaseBot, user: User) -> None:
    sp = _get_seated(user.username)
    if sp is None:
        await _w(bot, user.id, "You're not at the table.")
        return

    tbl = _get_table()
    phase = tbl["phase"] if tbl else "idle"

    if phase in ("preflop", "flop", "turn", "river"):
        # In an active hand: defer until hand ends
        if sp.get("leaving_after_hand", 0):
            await _w(bot, user.id, "Already marked to leave after this hand.")
            return
        _update_seated(user.username, leaving_after_hand=1)
        await _w(bot, user.id,
            f"✅ You will leave and cash out {sp['table_stack']} 🪙 "
            f"after this hand ends.")
        return

    # Not in active hand: leave immediately
    stack = sp["table_stack"]
    _remove_seated(user.username)
    if stack > 0:
        db.adjust_balance(user.id, stack)
        db.add_ledger_entry(
            user.id, user.username, stack,
            "Poker cash-out (immediate leave)"
        )
    await _chat(bot, f"👋 {db.get_display_name(user.id, user.username)} left table. Cashed out {stack} 🪙.")

    # If table empty, close it
    remaining = _get_all_seated()
    if not remaining:
        _full_clear_table()
        await _chat(bot, "♠️ Poker table closed (no players).")


# ── Command: /sitout ────────────────────────────────────────────────────────────

async def _handle_sitout(bot: BaseBot, user: User) -> None:
    sp = _get_seated(user.username)
    if sp is None:
        await _w(bot, user.id, "You're not at the table.")
        return
    if sp["status"] == "sitting_out":
        await _w(bot, user.id, "Already sitting out. !sitin to return.")
        return
    _update_seated(user.username, status="sitting_out")
    await _chat(bot,
        f"🪑 @{user.username} is sitting out. Stack: {sp['table_stack']} 🪙. "
        f"!sitin to return.")


# ── Command: /sitin ─────────────────────────────────────────────────────────────

async def _handle_sitin(bot: BaseBot, user: User) -> None:
    global _next_hand_task
    sp = _get_seated(user.username)
    if sp is None:
        await _w(bot, user.id, "You're not at the table. !join <amount> to join.")
        return
    if sp["status"] == "seated":
        await _w(bot, user.id, "Already active at the table.")
        return
    if sp["table_stack"] == 0:
        await _w(bot, user.id, "Stack is 0. !rebuy <amount> to add chips.")
        return
    _update_seated(user.username, status="seated", leaving_after_hand=0)
    await _chat(bot,
        f"✅ @{user.username} is back in. Stack: {sp['table_stack']} 🪙.")

    # If waiting and now have enough players, kick off countdown
    tbl  = _get_table()
    if tbl and tbl["active"] and tbl["phase"] == "waiting":
        active = _get_active_seated()
        min_pl = _s("min_players", 2)
        if len(active) >= min_pl:
            delay = _s("next_hand_delay", 10)
            _save_table(phase="between_hands")
            await _chat(bot, f"♠️ Starting hand in {delay}s!")
            _cancel_task(_next_hand_task)
            _next_hand_task = asyncio.create_task(
                _next_hand_countdown(bot, delay)
            )


# ── Command: /rebuy ─────────────────────────────────────────────────────────────

async def _handle_rebuy(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _s("rebuy_enabled", 1):
        await _w(bot, user.id, "Rebuy is currently disabled.")
        return

    sp = _get_seated(user.username)
    if sp is None:
        await _w(bot, user.id,
            "You're not at the table. !join <amount> to join first.")
        return

    if len(args) < 3 or not args[2].isdigit():
        await _w(bot, user.id, "Usage: !rebuy <amount>  or  !poker rebuy <amt>")
        return

    amount = int(args[2])
    if amount < 1:
        await _w(bot, user.id, "Amount must be at least 1c.")
        return

    # Max stack check
    if _s("max_stack_enabled", 0):
        max_stk   = _s("max_table_stack", 100000)
        new_stack = sp["table_stack"] + amount
        if new_stack > max_stk:
            await _w(bot, user.id,
                f"Would exceed max stack of {max_stk:,} 🪙. "
                f"Current: {sp['table_stack']} 🪙.")
            return

    err = _check_daily_limits(user.username, amount)
    if err:
        await _w(bot, user.id, err)
        return

    bal = db.get_balance(user.id)
    if bal < amount:
        await _w(bot, user.id, f"Not enough coins. Balance: {bal} 🪙.")
        return

    db.adjust_balance(user.id, -amount)
    db.add_ledger_entry(
        user.id, user.username, -amount, "Poker rebuy"
    )
    new_stack = sp["table_stack"] + amount
    _update_seated(user.username,
                   table_stack=new_stack,
                   buyin_total=sp["buyin_total"] + amount)
    await _chat(bot,
        f"✅ @{user.username} rebought {amount} 🪙. New stack: {new_stack} 🪙.")

    # If sitting_out and rebought, ask if they want to sit in
    if sp["status"] == "sitting_out":
        await _w(bot, user.id, "Use !sitin when ready to play.")


# ── Command: /pstacks  /mystack ────────────────────────────────────────────────

async def _handle_stacks(bot: BaseBot, user: User) -> None:
    seated = _get_all_seated()
    if not seated:
        await _w(bot, user.id, "No one at the table.")
        return
    parts = []
    for s in seated:
        flag = ""
        if s["status"] == "sitting_out":
            flag = "(out)"
        elif s.get("leaving_after_hand", 0):
            flag = "(leaving)"
        parts.append(f"@{s['username']}:{s['table_stack']} 🪙{flag}")
    await _w(bot, user.id, ("♠️ Stacks: " + "  ".join(parts))[:249])


async def _handle_mystack(bot: BaseBot, user: User) -> None:
    sp = _get_seated(user.username)
    if sp is None:
        await _w(bot, user.id, "You're not at the table.")
        return
    tbl    = _get_table()
    phase  = tbl["phase"] if tbl else "idle"
    status = sp["status"]
    flag   = "(sitting out)" if status == "sitting_out" else ""
    leave  = " (leaving after hand)" if sp.get("leaving_after_hand", 0) else ""
    await _w(bot, user.id,
        f"♠️ Stack: {sp['table_stack']} 🪙 {flag}{leave} | "
        f"Table phase: {phase} | !rebuy <amt> !sitout !poker leave")


# ── Command: /poker close ───────────────────────────────────────────────────────

async def _handle_close(bot: BaseBot, user: User) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    tbl = _get_table()
    if not tbl or not tbl["active"]:
        await _w(bot, user.id, "No active poker table.")
        return
    phase = tbl["phase"]
    if phase in ("preflop", "flop", "turn", "river"):
        _set("table_closing", 1)
        await _chat(bot,
            "♠️ Table closing after this hand. No new hands will start.")
    else:
        # Close immediately — return all stacks
        seated = _get_all_seated()
        for s in seated:
            if s["table_stack"] > 0:
                db.adjust_balance(s["user_id"], s["table_stack"])
                db.add_ledger_entry(
                    s["user_id"], s["username"],
                    s["table_stack"], "Poker table-close cash-out"
                )
                await _chat(bot,
                    f"👋 @{s['username']} cashed out {s['table_stack']} 🪙.")
        _set("table_closing", 0)
        _full_clear_table()
        global _next_hand_task
        _cancel_task(_next_hand_task); _next_hand_task = None
        await _chat(bot, "♠️ Poker table closed by staff.")


# ── Command: /poker start ───────────────────────────────────────────────────────

async def _handle_start(bot: BaseBot, user: User) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    tbl = _get_table()
    if not tbl or not tbl["active"]:
        await _w(bot, user.id, "No table open. Players must join first.")
        return
    phase = tbl["phase"]
    if phase in ("preflop", "flop", "turn", "river"):
        await _w(bot, user.id, "Hand already in progress.")
        return
    await _try_start_hand(bot)
    await _w(bot, user.id, "Starting next hand now.")


# ── Startup recovery ────────────────────────────────────────────────────────────

async def startup_poker_recovery(bot: BaseBot) -> None:
    """Called from on_start. Recovers any active poker state from DB."""
    global _lobby_task, _turn_task, _next_hand_task

    tbl = _get_table()
    if not tbl or not tbl["active"]:
        # Check if seated players exist without an active table flag
        seated = _get_all_seated()
        if seated:
            _save_table(active=1, phase="waiting")
            await _chat(bot,
                f"♻️ Poker table restored. {len(seated)} player(s) at table. "
                f"Starting next hand...")
            tbl = _get_table()
        else:
            return

    round_id = tbl.get("round_id")
    phase    = tbl["phase"]
    seated   = _get_all_seated()

    print(f"[POKER] Recovery: phase={phase} round={round_id} "
          f"seated={len(seated)}")

    if phase in ("preflop", "flop", "turn", "river"):
        if not round_id:
            _save_table(phase="waiting")
            return
        players = _get_players(round_id)
        active  = _active_players(players)
        if len(_eligible_players(players)) <= 1:
            await finish_poker_hand(bot, "everyone_folded")
            return

        # Part 7: validate every active player has exactly 2 cards
        missing_cards = [
            p["username"]
            for p in active
            if len(json.loads(p["hole_cards_json"] or "[]")) != 2
        ]
        if missing_cards:
            names = ", ".join(missing_cards[:4])
            print(f"[POKER] Recovery: missing cards for {names}")
            _save_table(phase="recovery_required")
            await _chat(bot,
                f"⚠️ Poker recovery: {len(missing_cards)} player(s) missing cards. "
                f"Use !poker hardrefund.")
            _log_recovery("recovery_required", round_id, phase,
                          f"missing_cards={names}")
            return

        _save_table(restored_after_restart=1)
        await _chat(bot, "♻️ Poker restored. Cards and stacks loaded.")
        for p in active:
            try:
                await bot.highrise.send_whisper(
                    p["user_id"], "♻️ Poker restored. Use !ph to see your cards.")
            except Exception:
                pass

        _log_recovery("recovered", round_id, phase,
                      f"active={len(active)} pot={tbl['pot']}")

        idx = tbl["current_player_index"]
        if 0 <= idx < len(players):
            cur = players[idx]
            if cur["status"] == "active" and _needs_to_act(cur, tbl["current_bet"]):
                tbl_fresh = _get_table()
                if tbl_fresh:
                    await _prompt_player(bot, tbl_fresh, cur)
                return
        await advance_turn_or_round(bot)
        return

    if phase == "between_hands":
        if not seated:
            _full_clear_table()
            return
        active = _get_active_seated()
        min_pl = _s("min_players", 2)
        if len(active) >= min_pl:
            delay = _s("next_hand_delay", 10)
            await _chat(bot,
                f"♻️ Poker table restored. Next hand in {delay}s.")
            _next_hand_task = asyncio.create_task(
                _next_hand_countdown(bot, delay)
            )
        else:
            _save_table(phase="waiting")
            await _chat(bot,
                f"♻️ Poker table restored. {len(active)}/{min_pl} ready.")
        return

    if phase == "waiting":
        if not seated:
            _full_clear_table()
            return
        await _chat(bot,
            f"♻️ Poker table restored. {len(seated)} seated. Waiting for players.")
        return

    if phase == "finished":
        _clear_hand()
        _save_table(phase="waiting")
        return

    if phase == "lobby":
        # Old-format lobby: if there's a round_id with players, start the hand
        if round_id:
            players = _get_players(round_id)
            if players:
                # Move them to seated
                for p in players:
                    existing = _get_seated(p["username"])
                    if not existing:
                        sn = _next_seat_number()
                        _seat_player(
                            p["username"], p["user_id"], p["buyin"], sn
                        )
                _clear_players(round_id)
            _clear_hand()
        _save_table(phase="waiting")
        await _chat(bot,
            "♻️ Poker table restored from old lobby format.")
        return

    # Corrupted state
    _save_table(phase="recovery_required")
    await _chat(bot,
        "⚠️ Poker recovery needed. Use !poker recoverystatus for details.")
    _log_recovery("recovery_required", round_id or "", phase, "corrupted state")


# ── Main dispatcher ────────────────────────────────────────────────────────────

async def handle_poker(bot: BaseBot, user: User, args: list[str]) -> None:
    try:
        await _dispatch(bot, user, args)
    except Exception as exc:
        print(f"[POKER] error {user.username}: {exc}")
        try:
            await _w(bot, user.id, "Poker error. Please try again.")
        except Exception:
            pass


async def _dispatch(bot: BaseBot, user: User, args: list[str]) -> None:
    global _lobby_task, _turn_task, _next_hand_task
    if len(args) < 2:
        sp = _get_seated(user.username)
        if sp:
            await _w(bot, user.id,
                f"♠️ Stack:{sp['table_stack']} 🪙 | !ph !check !call !r !fold !allin "
                f"| !sitout !rebuy !poker leave | !phelp")
        else:
            await _w(bot, user.id,
                "♠️ !join <amount> to join poker. !phelp for help.")
        return

    sub = args[1].lower()

    # ── Public info ──────────────────────────────────────────────────────────
    if sub == "rules":
        min_b  = _s("min_buyin", 100)
        max_b  = _s("max_buyin", 5000)
        ai_on  = "ON" if _s("allin_enabled", 1) else "OFF"
        bl_on  = "ON" if _s("blinds_enabled", 1) else "OFF"
        sb_amt = _s("small_blind", 50)
        bb_amt = _s("big_blind", 100)
        await _w(bot, user.id,
            f"♠️ Hold'em. Buy {min_b}-{max_b} 🪙. Persistent stacks. "
            f"Blinds {bl_on} SB:{sb_amt} BB:{bb_amt}. All-in {ai_on}. "
            f"!phelp for cmds.")
        return

    if sub == "stats":
        target_name = args[2] if len(args) >= 3 else None
        if target_name:
            s = db.get_poker_stats_by_username(target_name)
            if not s:
                await _w(bot, user.id, f"No poker stats for @{target_name}.")
                return
            net   = s.get("net_profit", 0)
            net_s = f"+{net} 🪙" if net >= 0 else f"{net} 🪙"
            await _w(bot, user.id,
                f"♠️ @{target_name}: {s['wins']}W/{s['losses']}L | "
                f"Net {net_s} | All-ins {s.get('allins', 0)}")
        else:
            db.ensure_poker_stats(user.id, user.username)
            s     = db.get_poker_stats(user.id)
            net   = s.get("net_profit", 0)
            net_s = f"+{net} 🪙" if net >= 0 else f"{net} 🪙"
            await _w(bot, user.id,
                f"♠️ {user.username}: {s['wins']}W/{s['losses']}L | "
                f"Net {net_s} | All-ins {s.get('allins', 0)}")
        return

    if sub == "limits":
        min_b  = _s("min_buyin", 100);  max_b = _s("max_buyin", 5000)
        bl_on  = "ON" if _s("buyin_limit_enabled", 0) else "OFF"
        min_r  = _s("min_raise", 50);   max_r = _s("max_raise", 1000)
        rl_on  = _s("raise_limit_enabled", 1)
        wl     = _s("table_daily_win_limit", 10000)
        ll     = _s("table_daily_loss_limit", 5000)
        we     = "ON" if _s("win_limit_enabled", 1) else "OFF"
        le     = "ON" if _s("loss_limit_enabled", 1) else "OFF"
        raise_str = f"Raise {min_r}-{max_r} 🪙 ON" if rl_on else "Raise limit OFF"
        await _w(bot, user.id,
            f"♠️ Buy {min_b}-{max_b} 🪙 {bl_on} | {raise_str} | "
            f"W/L {wl}/{ll} {we}/{le}")
        return

    if sub == "players":
        seated = _get_all_seated()
        if not seated:
            await _w(bot, user.id, "No one at the table.")
            return
        parts = []
        for s in seated:
            flag = "(out)" if s["status"] == "sitting_out" else ""
            parts.append(f"@{s['username']}({s['table_stack']} 🪙){flag}")
        await _w(bot, user.id, ("♠️ Table: " + "  ".join(parts))[:249])
        return

    if sub in ("stacks", "pstacks"):
        await _handle_stacks(bot, user)
        return

    if sub == "table":
        tbl = _get_table()
        if not tbl or not tbl["active"]:
            count = _seated_count()
            if count == 0:
                await _w(bot, user.id,
                    "♠️ Poker Table\nWaiting for players.\nUse !join [amount]")
            else:
                await _w(bot, user.id,
                    f"♠️ Poker Table\nWaiting for players. ({count} seated)\n"
                    f"Use !join [amount]")
            return
        phase = tbl["phase"]
        if phase == "between_hands":
            count = _seated_count()
            await _w(bot, user.id,
                f"♠️ Poker Table\nNext hand soon.\nPlayers: {count}")
            return
        if phase in ("waiting", "idle"):
            count = _seated_count()
            await _w(bot, user.id,
                f"♠️ Poker Table\nWaiting for players. ({count} seated)\n"
                f"Use !join [amount]")
            return
        if phase == "finished":
            # Auto-repair if safe; otherwise show recovery notice
            res = _cleanup_finished_hand()
            if res["action"] == "pot_unresolved":
                await _w(bot, user.id,
                    "♠️ Poker Table\nRecovering. Staff: !poker recoverystatus")
            else:
                tbl2 = _get_table()
                ph2  = tbl2["phase"] if tbl2 else "waiting"
                cnt  = _seated_count()
                if ph2 == "between_hands":
                    await _w(bot, user.id,
                        f"♠️ Poker Table\nNext hand soon.\nPlayers: {cnt}")
                else:
                    await _w(bot, user.id,
                        f"♠️ Poker Table\nWaiting for players. ({cnt} seated)\n"
                        f"Use !join [amount]")
            return
        community = json.loads(tbl["community_cards_json"] or "[]")
        board     = _fcs(community) or "—"
        players   = _get_players(tbl["round_id"]) if tbl.get("round_id") else []
        idx       = tbl["current_player_index"]
        turn_name = players[idx]["username"] if 0 <= idx < len(players) else "?"
        pot       = tbl["pot"]
        await _w(bot, user.id, (
            f"♠️ Poker Table\n"
            f"Phase: {phase.title()} | Pot: {pot:,} 🪙\n"
            f"Turn: @{turn_name} | Board: {board}\n"
            f"Players: {len(players)}")[:249])
        return

    if sub in ("hand", "cards"):
        tbl = _get_table()
        sp  = _get_seated(user.username)

        # Auto-repair stuck finished state if safe
        broken = detect_poker_inconsistent_state()
        if broken and "finished_stuck" in broken:
            res = _cleanup_finished_hand()
            if res["action"] == "pot_unresolved":
                await _w(bot, user.id,
                    "⚠️ Poker table is recovering. Staff: !poker cleanup")
                return
            # Successfully cleaned — reload table
            tbl = _get_table()

        # Not in any active betting phase
        if not tbl or not tbl["active"] or tbl["phase"] not in (
                "preflop", "flop", "turn", "river"):
            if sp is None:
                await _w(bot, user.id, "Join poker with !join <amount>.")
            elif sp["status"] == "sitting_out":
                await _w(bot, user.id,
                    "You are sitting out. Use !sitin for next hand.")
            else:
                stack = sp["table_stack"]
                await _w(bot, user.id,
                    f"No active hand. Table stack: {stack:,} 🪙. Next hand soon.")
            return

        p = _get_player(tbl["round_id"], user.id)
        # Fallback: lookup by normalized username (handles stale user_id)
        if p is None:
            p = _get_player_by_name(tbl["round_id"], user.username)
        if p is None:
            if sp is None:
                await _w(bot, user.id, "⚠️ You are not in the current hand.")
            elif sp["status"] == "sitting_out":
                await _w(bot, user.id,
                    "You are sitting out. Use !sitin for next hand.")
            else:
                await _w(bot, user.id,
                    "No active hand yet. Next hand starts soon.")
            return

        # Load cards: poker_hole_cards (normalized-key) first, then fallback
        hc = db.get_hole_cards(tbl["round_id"], user.username)
        if hc and hc.get("card1") and hc.get("card2"):
            cards = [hc["card1"], hc["card2"]]
        else:
            cards = json.loads(p["hole_cards_json"] or "[]")
        if not cards or len(cards) != 2:
            await _w(bot, user.id,
                "⚠️ Your cards were not found. Ask staff: !pokerforceresend.")
            print(f"[POKER] /ph: no cards for {user.username} "
                  f"round={tbl['round_id']}")
            return

        community = json.loads(tbl["community_cards_json"] or "[]")
        board_str = _fcs(community) if community else "—"
        pot       = tbl.get("pot", 0) or 0
        c1, c2    = _fc(cards[0]), _fc(cards[1])
        hand_msg  = (
            f"🃏 Your hand: {c1} {c2}\n"
            f"Board: {board_str}\n"
            f"Pot: {pot:,} 🪙"
        )[:249]
        try:
            await bot.highrise.send_whisper(user.id, hand_msg)
            if tbl.get("round_id"):
                db.ensure_delivery_row(
                    tbl["round_id"], user.username, user.username)
                db.mark_card_delivered(tbl["round_id"], user.username)
        except Exception:
            pass
        return

    if sub in ("odds", "chance"):
        tbl = _get_table()
        if not tbl or not tbl["active"] or tbl["phase"] not in (
                "preflop", "flop", "turn", "river"):
            await _w(bot, user.id, "No active hand.")
            return
        p = _get_player(tbl["round_id"], user.id)
        if p is None:
            await _w(bot, user.id, "You are not in this hand.")
            return
        cards     = json.loads(p["hole_cards_json"] or "[]")
        community = json.loads(tbl["community_cards_json"] or "[]")
        deck      = json.loads(tbl["deck_json"] or "[]")
        if not cards:
            await _w(bot, user.id, "Cards not dealt yet.")
            return
        strength = _hand_strength_label(cards, community)
        draws    = _detect_draws(cards, community)
        players  = _get_players(tbl["round_id"])
        opps     = len(_eligible_players(players)) - 1
        pct      = _calc_odds(cards, community, deck, max(1, opps))
        label    = strength + (" + " + draws if draws else "")
        if pct < 0:
            await _w(bot, user.id, f"📊 {label} | Chance unavailable.")
        else:
            await _w(bot, user.id, f"📊 {label} | Win chance: ~{pct}%")
        return

    if sub in ("mystack", "stack"):
        await _handle_mystack(bot, user)
        return

    # ── Join / Leave / Sit-out / Sit-in / Rebuy ──────────────────────────────
    if sub in ("join", "leave", "sitout", "sitin", "rebuy"):
        # Auto-repair check: block player commands if table is stuck
        blk = _recovery_block_msg()
        if blk:
            broken = detect_poker_inconsistent_state()
            if broken and "finished_stuck" in broken:
                # Try silent auto-repair first
                res = _cleanup_finished_hand()
                if res["action"] == "pot_unresolved":
                    await _w(bot, user.id, blk)
                    return
                # Auto-repaired — fall through to the command

    if sub == "join":
        await _handle_join(bot, user, args)
        return

    if sub == "leave":
        await _handle_leave(bot, user)
        return

    if sub == "sitout":
        await _handle_sitout(bot, user)
        return

    if sub == "sitin":
        await _handle_sitin(bot, user)
        return

    if sub == "rebuy":
        await _handle_rebuy(bot, user, args)
        return

    if sub == "start":
        await _handle_start(bot, user)
        return

    if sub == "close":
        await _handle_close(bot, user)
        return

    # ── Toggles (manager+) ───────────────────────────────────────────────────
    if sub == "buyinlimit" and len(args) >= 3 and args[2].lower() in ("on", "off"):
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        v = args[2].lower()
        _set("buyin_limit_enabled", 1 if v == "on" else 0)
        await _w(bot, user.id,
            f"{'✅' if v=='on' else '⛔'} Poker buy-in limit {v.upper()}.")
        return

    if sub == "allin" and len(args) >= 3 and args[2].lower() in ("on", "off"):
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        v = args[2].lower()
        _set("allin_enabled", 1 if v == "on" else 0)
        await _w(bot, user.id,
            f"{'✅' if v=='on' else '⛔'} Poker all-in {v.upper()}.")
        return

    if sub == "blinds" and len(args) >= 3 and args[2].lower() in ("on", "off"):
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        v = args[2].lower()
        _set("blinds_enabled", 1 if v == "on" else 0)
        await _w(bot, user.id,
            f"{'✅' if v=='on' else '⛔'} Poker blinds {v.upper()}.")
        return

    if sub == "rebuy" and len(args) >= 3 and args[2].lower() in ("on", "off"):
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        v = args[2].lower()
        _set("rebuy_enabled", 1 if v == "on" else 0)
        await _w(bot, user.id,
            f"{'✅' if v=='on' else '⛔'} Poker rebuy {v.upper()}.")
        return

    if sub == "maxstack" and len(args) >= 3 and args[2].lower() in ("on", "off"):
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        v = args[2].lower()
        _set("max_stack_enabled", 1 if v == "on" else 0)
        await _w(bot, user.id,
            f"{'✅' if v=='on' else '⛔'} Poker max-stack {v.upper()}.")
        return

    if sub == "autostart" and len(args) >= 3 and args[2].lower() in ("on", "off"):
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        v = args[2].lower()
        _set("auto_start_next_hand", 1 if v == "on" else 0)
        await _w(bot, user.id,
            f"{'✅' if v=='on' else '⛔'} Poker auto-start {v.upper()}.")
        return

    if sub == "autositout" and len(args) >= 3 and args[2].lower() in ("on", "off"):
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        v = args[2].lower()
        _set("autositout_enabled", 1 if v == "on" else 0)
        await _w(bot, user.id,
            f"{'✅' if v=='on' else '⛔'} Auto-sit-out on idle {v.upper()}.")
        return

    if sub == "leaderboard":
        mode_args = args[2:] if len(args) > 2 else []
        await handle_pokerlb(bot, user, mode_args)
        return

    # ── Betting actions (check/call/raise/fold/allin) ────────────────────────
    if sub in ("check", "call", "raise", "fold", "allin", "all-in", "shove"):
        # Pause guard
        if _poker_paused:
            await _w(bot, user.id, "♠️ Poker is paused by staff. Please wait.")
            return
        tbl = _get_table()
        if not tbl or not tbl["active"]:
            await _w(bot, user.id, "No poker hand active.")
            return
        if tbl["phase"] not in ("preflop", "flop", "turn", "river"):
            await _w(bot, user.id, "No active betting round.")
            return
        round_id = tbl["round_id"]
        # Duplicate-action dedup guard: one action at a time per player per round
        _dedup_key = f"{round_id}:{user.id}"
        if _dedup_key in _action_processing:
            print(f"[POKER_TURN] duplicate_action ignored actor={user.username} sub={sub}")
            return
        _action_processing.add(_dedup_key)
        try:
            p = _get_player(round_id, user.id)
            if p is None:
                await _w(bot, user.id, "You're not in this hand.")
                return
            if p["status"] == "allin":
                await _w(bot, user.id, "You are already all-in.")
                return
            if p["status"] != "active":
                await _w(bot, user.id, "You've already folded or left.")
                return
            players = _get_players(round_id)
            idx     = tbl["current_player_index"]
            if 0 <= idx < len(players):
                cur = players[idx]
                if cur["user_id"] != user.id:
                    _cur_disp = _pdn(cur)
                    print(f"[POKER BLOCK] user=@{user.username} "
                          f"current=@{cur['username']} reason=not_turn")
                    await _w(bot, user.id, f"⏳ It's {_cur_disp}'s turn.")
                    return
            if sub == "check":
                if tbl["current_bet"] > p["current_bet"]:
                    owe = tbl["current_bet"] - p["current_bet"]
                    await _w(bot, user.id,
                        f"{_PK_INVAL} — Can't check. {owe} 🪙 to call. !call or !fold.")
                    return
                await _do_check(bot, round_id, user.id, user.username)
            elif sub == "call":
                await _do_call(bot, round_id, p, tbl)
            elif sub == "raise":
                if len(args) < 3 or not args[2].isdigit():
                    min_r = _s("min_raise", 50)
                    await _w(bot, user.id, f"Use !raise <amount>. Min {min_r} 🪙.")
                    return
                await _do_raise(bot, round_id, p, tbl, int(args[2]))
            elif sub == "fold":
                await _do_fold(bot, round_id, p)
            elif sub in ("allin", "all-in", "shove"):
                await _do_allin(bot, round_id, p, tbl)
        finally:
            _action_processing.discard(_dedup_key)
        return

    # ── on/off ──────────────────────────────────────────────────────────────
    if sub == "on":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        _set("poker_enabled", 1)
        await _chat(bot, "✅ Poker is now ON.")
        return

    if sub == "off":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        _set("poker_enabled", 0)
        await _chat(bot, "⛔ Poker is now OFF.")
        return

    # ── Settings display (public) ──────────────────────────────────────────────
    if sub == "settings":
        page = int(args[2]) if len(args) >= 3 and args[2].isdigit() else 1
        if page == 2:
            min_p  = _s("min_players", 2);  max_p = _s("max_players", 6)
            min_b  = _s("min_buyin", 100);   max_b = _s("max_buyin", 5000)
            sb     = _s("small_blind", 50);  bb    = _s("big_blind", 100)
            ante   = _s("ante", 0)
            bl_en  = "ON" if _s("blinds_enabled", 1) else "OFF"
            await _w(bot, user.id,
                f"♠️ Poker 2 | Players {min_p}-{max_p} | "
                f"Buy {min_b}-{max_b} 🪙 | Blinds {bl_en} SB:{sb} BB:{bb} A:{ante}")
        elif page == 3:
            rb     = "ON" if _s("rebuy_enabled", 1) else "OFF"
            ms_en  = "ON" if _s("max_stack_enabled", 0) else "OFF"
            ms     = _s("max_table_stack", 100000)
            delay  = _s("next_hand_delay", 10)
            auto   = "ON" if _s("auto_start_next_hand", 1) else "OFF"
            ao     = "ON" if _s("autositout_enabled", 0) else "OFF"
            await _w(bot, user.id,
                f"♠️ Poker 3 | Rebuy {rb} | MaxStack {ms_en}({ms} 🪙) | "
                f"NextHand {delay}s Auto:{auto} | AutoSitOut {ao}")
        else:
            en     = "ON" if _s("poker_enabled", 1) else "OFF"
            ai_on  = "ON" if _s("allin_enabled", 1) else "OFF"
            rl_on  = "ON" if _s("raise_limit_enabled", 1) else "OFF"
            tt     = _s("turn_timer", 20)
            cl     = "Closing" if _s("table_closing", 0) else "Open"
            await _w(bot, user.id,
                f"♠️ Poker {en} {cl} | All-in {ai_on} | "
                f"RaiseLimit {rl_on} | Turn {tt}s | !poker settings 2")
        return

    # ── cancel ─────────────────────────────────────────────────────────────────
    if sub == "cancel":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        tbl = _get_table()
        if not tbl or not tbl["active"]:
            await _w(bot, user.id, "No active poker game.")
            return
        if tbl["phase"] not in ("preflop", "flop", "turn", "river"):
            await _w(bot, user.id,
                "No active hand to cancel (use !poker close to close table).")
            return
        await finish_poker_hand(bot, "cancelled")
        return

    # ── reset ──────────────────────────────────────────────────────────────────
    if sub == "reset":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        tbl = _get_table()
        if tbl and tbl["active"] and tbl["phase"] in (
                "preflop", "flop", "turn", "river"):
            await finish_poker_hand(bot, "cancelled")
        else:
            _cancel_task(_next_hand_task); _next_hand_task = None
            _clear_hand()
            _save_table(phase="waiting")
        await _w(bot, user.id, "♠️ Poker hand reset. Table still open.")
        return

    # ── state ──────────────────────────────────────────────────────────────────
    if sub == "state":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        tbl    = _get_table()
        seated = _get_all_seated()
        active = _get_active_seated()
        phase  = tbl["phase"] if tbl else "no-table"
        round_id = tbl.get("round_id") if tbl else None
        players  = _get_players(round_id) if round_id else []
        pot      = tbl.get("pot", 0) if tbl else 0
        # Detect broken state
        broken = detect_poker_inconsistent_state()
        if broken and "finished_stuck" in broken:
            rec = get_poker_recovery_recommendation()
            await _w(bot, user.id,
                (f"⚠️ Poker stuck | Finished stuck | "
                 f"Seated:{len(seated)} InHand:{len(players)} Pot:{pot} 🪙 | "
                 f"Fix: !poker {rec}")[:249])
            return
        if broken == "recovery_required":
            rec = get_poker_recovery_recommendation()
            await _w(bot, user.id,
                (f"⚠️ Poker stuck | Phase:recovery_required | "
                 f"Seated:{len(seated)} Pot:{pot} 🪙 | "
                 f"Fix: !poker {rec}")[:249])
            return
        if phase == "waiting":
            need = max(0, 2 - len(active))
            await _w(bot, user.id,
                f"Poker waiting | Seated {len(seated)} | Need {need} more player(s)")
            return
        if phase == "between_hands":
            await _w(bot, user.id,
                f"Poker between hands | Seated {len(seated)} Active {len(active)} | Next hand soon")
            return
        if phase in ("preflop", "flop", "turn", "river"):
            idx  = tbl["current_player_index"] if tbl else 0
            turn = players[idx]["username"] if players and 0 <= idx < len(players) else "?"
            await _w(bot, user.id,
                f"Poker active | Hand #{tbl.get('hand_number',0)} {phase.title()} | "
                f"Pot {pot} 🪙 | Turn @{turn} | InHand {len(players)}")
            return
        await _w(bot, user.id,
            f"Poker | {phase} | Seated:{len(seated)} Active:{len(active)} "
            f"InHand:{len(players)} | Hand#:{tbl.get('hand_number',0) if tbl else 0}")
        return

    # ── recover ────────────────────────────────────────────────────────────────
    if sub == "recover":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        await startup_poker_recovery(bot)
        await _w(bot, user.id, "♻️ Poker recovered.")
        return

    # ── refund ────────────────────────────────────────────────────────────────
    if sub == "refund":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        tbl = _get_table()
        if not tbl or not tbl["active"]:
            await _w(bot, user.id, "No active poker game.")
            return
        phase    = tbl["phase"]
        round_id = tbl.get("round_id") or ""
        # Handle stuck finished state
        if phase == "finished":
            res = _cleanup_finished_hand()
            if res["action"] == "pot_unresolved":
                await _w(bot, user.id,
                    f"⚠️ Pot {res['pot']} 🪙 — normal refund blocked. Use !poker hardrefund.")
                return
            _log_recovery("refund_cleanup", round_id, phase,
                          f"manual by @{user.username} | action={res['action']}")
            if res["next_phase"] == "between_hands":
                _cancel_task(_next_hand_task)
                delay = _s("next_hand_delay", 10)
                _next_hand_task = asyncio.create_task(_next_hand_countdown(bot, delay))
            await _w(bot, user.id, "✅ Poker hand refunded. Table safe.")
            return
        if phase not in ("preflop", "flop", "turn", "river", "recovery_required"):
            await _w(bot, user.id, "No active hand to refund.")
            return
        await finish_poker_hand(bot, "recovery_refund")
        _log_recovery("refund", round_id, phase, f"manual by @{user.username}")
        await _w(bot, user.id, "✅ Poker hand refunded. Table safe.")
        return

    # ── forcefinish ────────────────────────────────────────────────────────────
    if sub == "forcefinish":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        try:
            tbl = _get_table()
            if not tbl or not tbl["active"]:
                await _w(bot, user.id, "No active poker game.")
                return
            ph       = tbl["phase"]
            round_id = tbl.get("round_id") or ""
            # Handle stuck finished state (the primary bug fix)
            if ph == "finished":
                res = _cleanup_finished_hand()
                if res["action"] == "pot_unresolved":
                    await _w(bot, user.id,
                        f"⚠️ Pot {res['pot']} 🪙 unresolved. Use !poker hardrefund.")
                    return
                _log_recovery("forcefinish_cleanup", round_id, ph,
                              f"by @{user.username} | action={res['action']}")
                if res["next_phase"] == "between_hands":
                    _cancel_task(_next_hand_task)
                    delay = _s("next_hand_delay", 10)
                    _next_hand_task = asyncio.create_task(
                        _next_hand_countdown(bot, delay))
                await _w(bot, user.id, "✅ Stuck hand cleared. Table ready.")
                return
            if ph in ("waiting", "between_hands"):
                await _w(bot, user.id, "No active hand to finish.")
                return
            if ph == "recovery_required":
                await _w(bot, user.id, "Hand corrupted. Use !poker hardrefund.")
                return
            if ph not in ("preflop", "flop", "turn", "river"):
                rec = get_poker_recovery_recommendation()
                await _w(bot, user.id,
                    f"No active hand (phase={ph}). Use !poker {rec}.")
                return
            # Check for corrupted cards on preflop
            players = _get_players(round_id) if round_id else []
            if ph == "preflop":
                missing = [p["username"] for p in players
                           if len(json.loads(p["hole_cards_json"] or "[]")) != 2]
                if len(missing) == len(players) and players:
                    await _w(bot, user.id, "Hand corrupted. Use !poker hardrefund.")
                    return
            eligible = _eligible_players(players)
            if len(eligible) >= 2:
                _save_table(phase="river")
                await finish_poker_hand(bot, "showdown")
            else:
                await finish_poker_hand(bot, "everyone_folded")
        except Exception as exc:
            import traceback
            print(f"[POKER] forcefinish error: {exc}\n{traceback.format_exc()}")
            _log_recovery("forcefinish_error", "", "", str(exc)[:200])
            await _w(bot, user.id, "Forcefinish failed. Use !poker hardrefund.")
        return

    # ── hardrefund ─────────────────────────────────────────────────────────────
    if sub == "hardrefund":
        if not (is_admin(user.username) or is_owner(user.username)):
            await _w(bot, user.id, "Owner/admin only.")
            return
        tbl = _get_table()
        if not tbl or not tbl["active"]:
            await _w(bot, user.id, "No active poker table.")
            return
        print(f"[POKER] /poker hardrefund by @{user.username}")
        msg = await _hard_refund_hand(user.username)
        await _w(bot, user.id, msg)
        return

    # ── clearhand ──────────────────────────────────────────────────────────────
    if sub == "clearhand":
        if not (is_admin(user.username) or is_owner(user.username)):
            await _w(bot, user.id, "Owner/admin only.")
            return
        tbl = _get_table()
        if not tbl or not tbl["active"]:
            await _w(bot, user.id, "No active poker table.")
            return
        pot = int(tbl.get("pot") or 0)
        if pot > 0:
            await _w(bot, user.id, f"Pot {pot} 🪙 exists. Use !poker hardrefund.")
            return
        round_id = tbl.get("round_id")
        _cancel_task(_next_hand_task)
        if round_id:
            _clear_players(round_id)
        _clear_hand()
        n = len(_get_active_seated())
        _save_table(phase="between_hands" if n >= 2 else "waiting")
        _log_recovery("clearhand", round_id or "", tbl.get("phase", "?"),
                      f"by @{user.username}")
        await _w(bot, user.id, "✅ Poker hand cleared.")
        return

    # ── closeforce ─────────────────────────────────────────────────────────────
    if sub == "closeforce":
        if not (is_admin(user.username) or is_owner(user.username)):
            await _w(bot, user.id, "Owner/admin only.")
            return
        tbl = _get_table()
        if not tbl or not tbl["active"]:
            await _w(bot, user.id, "No active poker table.")
            return
        import secrets
        code = secrets.token_hex(3).upper()
        _close_confirm_codes[code] = user.username
        await _w(bot, user.id,
            f"Confirm table close: /confirmclosepoker {code} (expires 60s)")
        async def _expire_code(c: str = code) -> None:
            await asyncio.sleep(60)
            _close_confirm_codes.pop(c, None)
        asyncio.create_task(_expire_code())
        return

    # ── recoverystatus / recovery / status / emergency ─────────────────────────
    if (sub == "recoverystatus" or sub == "recovery"
            or sub == "status" or sub == "emergency"):
        if sub == "emergency":
            if not (is_owner(user.username) or is_admin(user.username)):
                await _w(bot, user.id, "Owner/admin only.")
                return
        elif not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        tbl      = _get_table()
        seated   = _get_all_seated()
        phase    = tbl["phase"] if tbl else "no-table"
        round_id = tbl.get("round_id") if tbl else None
        pot      = int(tbl.get("pot") or 0) if tbl else 0
        players  = _get_players(round_id) if round_id else []
        active   = _active_players(players)
        eligible = _eligible_players(players)
        unpaid   = ([p for p in players
                     if not _is_paid(round_id, p["username"])]
                    if round_id else [])
        cards_ok = bool(eligible and all(
            len(json.loads(p.get("hole_cards_json") or "[]")) == 2
            for p in eligible
        ))
        community: list = []
        if tbl and tbl.get("community_cards_json"):
            try:
                community = json.loads(tbl["community_cards_json"])
            except Exception:
                pass
        rec = get_poker_recovery_recommendation()
        if not tbl:
            status_line = "Poker recovery: no table found. No action needed."
        elif rec == "no_action":
            if phase in ("waiting", "idle", "between_hands"):
                status_line = f"Poker recovery: table {phase} | no stuck hand."
            else:
                status_line = f"Poker recovery: active hand | pot {pot:,} 🪙 | no fix needed."
        elif rec == "forcefinish":
            status_line = f"Poker recovery: active hand | pot {pot:,} 🪙 | use !poker forcefinish."
        elif rec == "refund":
            status_line = f"Poker recovery: unresolved pot {pot:,} 🪙 | use !poker refund."
        elif rec == "hardrefund":
            status_line = f"Poker recovery: corrupt pot {pot:,} 🪙 | use !poker hardrefund."
        elif rec == "clearhand":
            status_line = f"Poker recovery: finished stuck | use !poker clearhand."
        elif rec == "closeforce":
            status_line = f"Poker recovery: severe issue | use !poker closeforce."
        else:
            status_line = f"Poker recovery: unknown ({rec})."
        details_line = (f"Seated:{len(seated)} Active:{len(active)}"
                        f" Elig:{len(eligible)} | Cards:{'ok' if cards_ok else 'missing'}"
                        f" Board:{len(community)}"
                        f" Round:{(round_id or 'none')[-6:]}")
        await _w(bot, user.id, status_line[:249])
        await _w(bot, user.id, details_line[:249])
        return

    # ── cleanup ────────────────────────────────────────────────────────────────
    if sub == "cleanup":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        tbl = _get_table()
        if not tbl or not tbl["active"]:
            await _w(bot, user.id, "No active poker table.")
            return
        broken = detect_poker_inconsistent_state()
        if not broken:
            await _w(bot, user.id, "No stuck poker hand found.")
            return
        round_id = tbl.get("round_id") or ""
        res = _cleanup_finished_hand()
        if res["action"] == "pot_unresolved":
            await _w(bot, user.id,
                f"⚠️ Pot {res['pot']} 🪙 unresolved. Use !poker hardrefund.")
            return
        _log_recovery("cleanup", round_id, tbl["phase"],
                      f"by @{user.username} | action={res['action']}")
        if res["next_phase"] == "between_hands":
            _cancel_task(_next_hand_task)
            delay = _s("next_hand_delay", 10)
            _next_hand_task = asyncio.create_task(_next_hand_countdown(bot, delay))
            await _w(bot, user.id, "✅ Poker cleanup done. Table ready. Next hand starting.")
        else:
            await _w(bot, user.id, "✅ Poker cleanup done. Table ready.")
        return

    # ── testcards ────────────────────────────────────────────────────────────
    if sub == "testcards":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Staff only.")
            return
        marker = _get_card_marker()
        sample = ["As", "Kd"]
        t1 = _fmt_private_hand(sample, 9900, hand_num=1)
        t2 = _fmt_private_hand(sample, 9900, hand_num=1, is_turn=True, owe=100,
                               rank_label="Pair of Aces")
        t3 = _fmt_private_hand(sample, 9900, hand_num=1, is_turn=True, owe=0,
                               rank_label="Pair of Aces")
        await _w(bot, user.id, f"Marker: {marker} | Deal: {t1}"[:249])
        await _w(bot, user.id, f"Call turn: {t2}"[:249])
        await _w(bot, user.id, f"Check turn: {t3}"[:249])
        return

    # ── cardstatus ─────────────────────────────────────────────────────────────
    if sub == "cardstatus":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Staff only.")
            return

        # ── 5-level round_id fallback ─────────────────────────────────────
        if len(args) > 2:
            round_id = args[2].strip()
        else:
            tbl      = _get_table()
            round_id = tbl["round_id"] if (tbl and tbl.get("round_id")) else None

        if not round_id:
            conn2 = db.get_connection()
            # level 3: poker_active_table any row
            r3 = conn2.execute(
                "SELECT round_id FROM poker_active_table "
                "WHERE round_id IS NOT NULL AND round_id != '' LIMIT 1"
            ).fetchone()
            if r3:
                round_id = r3["round_id"]
            else:
                # level 4: latest poker_hole_cards
                r4 = conn2.execute(
                    "SELECT round_id FROM poker_hole_cards "
                    "ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                if r4:
                    round_id = r4["round_id"]
                else:
                    # level 5: latest poker_card_delivery
                    r5 = conn2.execute(
                        "SELECT round_id FROM poker_card_delivery "
                        "ORDER BY rowid DESC LIMIT 1"
                    ).fetchone()
                    if r5:
                        round_id = r5["round_id"]
            conn2.close()

        if not round_id:
            await _w(bot, user.id, "No active poker hand.")
            return

        rows = db.get_card_delivery_status(round_id)

        # ── Auto-rebuild if hole cards exist but delivery rows don't ──────
        if not rows:
            hcc = db.get_connection()
            hc_cnt = hcc.execute(
                "SELECT COUNT(*) FROM poker_hole_cards WHERE round_id=?",
                (round_id,),
            ).fetchone()[0]
            hcc.close()
            if hc_cnt > 0:
                rebuilt = db.rebuild_delivery_rows(round_id)
                await _w(bot, user.id,
                    (f"Delivery rebuilt: 0/{rebuilt} sent. "
                     f"Use !poker resendcards.")[:249])
            else:
                await _w(bot, user.id, "No active card delivery data.")
            return

        sent    = [r for r in rows if r["cards_sent"]]
        missing = [r["username"] for r in rows if not r["cards_sent"]]
        if not missing:
            await _w(bot, user.id, f"Cards: {len(sent)}/{len(rows)} sent.")
        else:
            miss_str = " ".join(f"@{u}" for u in missing[:5])
            await _w(bot, user.id,
                f"Cards: {len(sent)}/{len(rows)} sent | Missing: {miss_str}"[:249])
        return

    # ── rebuilddelivery ────────────────────────────────────────────────────────
    if sub == "rebuilddelivery":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Staff only.")
            return
        tbl = _get_table()
        round_id = tbl["round_id"] if (tbl and tbl.get("round_id")) else None
        if not round_id:
            conn3 = db.get_connection()
            r_hc = conn3.execute(
                "SELECT round_id FROM poker_hole_cards "
                "ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            conn3.close()
            round_id = r_hc["round_id"] if r_hc else None
        if not round_id:
            await _w(bot, user.id, "No saved poker cards found.")
            return
        rebuilt = db.rebuild_delivery_rows(round_id)
        if rebuilt == 0:
            existing = db.get_card_delivery_status(round_id)
            if existing:
                await _w(bot, user.id,
                    f"Delivery rows already exist ({len(existing)} players).")
            else:
                await _w(bot, user.id, "No saved poker cards found.")
        else:
            await _w(bot, user.id,
                f"✅ Delivery tracking rebuilt for {rebuilt} player(s).")
        return

    # ── resendcards ────────────────────────────────────────────────────────────
    if sub == "resendcards":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Staff only.")
            return
        tbl = _get_table()
        if not tbl or not tbl["active"] or tbl["phase"] not in (
                "preflop", "flop", "turn", "river"):
            await _w(bot, user.id, "No active hand.")
            return
        round_id    = tbl["round_id"]
        hn          = tbl.get("hand_number", 0)
        players     = _get_players(round_id)
        target_name = args[2].lstrip("@").strip() if len(args) > 2 else None

        # Auto-rebuild delivery rows if missing (safety net)
        if not db.get_card_delivery_status(round_id):
            db.rebuild_delivery_rows(round_id)

        if target_name:
            p = _get_player_by_name(round_id, target_name)
            if not p:
                await _w(bot, user.id, f"@{target_name} is not in this hand.")
                return
            # Load from poker_hole_cards first for reliable normalized lookup
            hc = db.get_hole_cards(round_id, target_name)
            if hc and hc.get("card1") and hc.get("card2"):
                cards = [hc["card1"], hc["card2"]]
            else:
                cards = json.loads(p.get("hole_cards_json") or "[]")
            if len(cards) != 2:
                await _w(bot, user.id, f"@{target_name} has no cards to resend.")
                return
            msg = _fmt_private_hand(cards, p["stack"], hand_num=hn)
            try:
                await bot.highrise.send_whisper(p["user_id"], msg[:249])
                db.record_card_delivery(round_id, p["username"], True, "",
                                        p["username"])
                await _w(bot, user.id, f"{_PK_INFO}: Resent cards to @{p['username']}.")
            except Exception as _re:
                await _w(bot, user.id, f"{_PK_WARN}: Resend failed: {str(_re)[:60]}")
        else:
            sent_n, _ = await _deliver_poker_cards_to_all(bot, round_id, hn)
            await _w(bot, user.id, f"✅ Resent cards to {sent_n} player(s).")
        return

    # ── integrity ─────────────────────────────────────────────────────────────
    if sub == "integrity":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Staff only.")
            return
        sub2 = args[2].lower() if len(args) > 2 else ""
        from modules.casino_integrity import run_poker_integrity
        await run_poker_integrity(bot, user, sub2)
        return

    # ── refundtable ────────────────────────────────────────────────────────────
    if sub == "refundtable":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        _cancel_task(_lobby_task);     _lobby_task     = None
        _cancel_task(_turn_task);      _turn_task      = None
        _cancel_task(_next_hand_task); _next_hand_task = None
        tbl      = _get_table()
        round_id = tbl.get("round_id") if tbl else None
        refunded = 0
        count    = 0
        if round_id:
            players = _get_players(round_id)
            for p in players:
                if _is_paid(round_id, p["username"]):
                    continue
                final = p["stack"]
                _upsert_result(round_id, p["username"], p["buyin"],
                               "refundtable", final, final - p["buyin"])
                _update_seated(p["username"], table_stack=final)
                _mark_paid(round_id, p["username"])
            _clear_players(round_id)
        seated = _get_all_seated()
        for s in seated:
            stack = s["table_stack"]
            if stack > 0:
                db.adjust_balance(s["user_id"], stack)
                db.add_ledger_entry(
                    s["user_id"], s["username"],
                    stack, f"Poker refundtable by @{user.username}"
                )
                refunded += stack
                count    += 1
            _remove_seated(s["username"])
        _log_recovery("refundtable", round_id or "", tbl["phase"] if tbl else "?",
                      f"by @{user.username} | {count} players | {refunded} 🪙")
        _full_clear_table()
        await _chat(bot, f"♠️ Poker table refunded and closed. {refunded} 🪙 returned.")
        await _w(bot, user.id, "✅ Poker table refunded and closed.")
        return

    # ── winlimit / losslimit ───────────────────────────────────────────────────
    if sub == "winlimit":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Staff only.")
            return
        if len(args) >= 3 and args[2].lower() in ("on", "off"):
            v = args[2].lower()
            _set("win_limit_enabled", 1 if v == "on" else 0)
            await _w(bot, user.id,
                f"{'✅' if v=='on' else '⛔'} Poker win limit {v.upper()}.")
        else:
            await _w(bot, user.id, "Usage: !poker winlimit on|off")
        return

    if sub == "losslimit":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Staff only.")
            return
        if len(args) >= 3 and args[2].lower() in ("on", "off"):
            v = args[2].lower()
            _set("loss_limit_enabled", 1 if v == "on" else 0)
            await _w(bot, user.id,
                f"{'✅' if v=='on' else '⛔'} Poker loss limit {v.upper()}.")
        else:
            await _w(bot, user.id, "Usage: !poker losslimit on|off")
        return

    # ── raiselimit / allintoggle ───────────────────────────────────────────────
    if sub == "raiselimit":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        val = args[2].lower() if len(args) >= 3 else ""
        if val == "on":
            _set("raise_limit_enabled", 1)
            max_r = _s("max_raise", 1000)
            await _w(bot, user.id, f"✅ Raise cap ON (max {max_r} 🪙).")
        elif val == "off":
            _set("raise_limit_enabled", 0)
            await _w(bot, user.id, "⛔ Raise cap OFF.")
        else:
            st = "ON" if _s("raise_limit_enabled", 1) else "OFF"
            await _w(bot, user.id, f"Raise limit is {st}. !poker raiselimit on|off")
        return

    if sub in ("allinmode", "allintoggle"):
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        val = args[2].lower() if len(args) >= 3 else ""
        if val == "on":
            _set("allin_enabled", 1)
            await _w(bot, user.id, "✅ All-in enabled.")
        elif val == "off":
            _set("allin_enabled", 0)
            await _w(bot, user.id, "⛔ All-in disabled.")
        else:
            st = "ON" if _s("allin_enabled", 1) else "OFF"
            await _w(bot, user.id, f"All-in is {st}. !poker allintoggle on|off")
        return

    # ── Dashboard + staff shortcuts ────────────────────────────────────────
    if sub in ("dashboard", "dash", "admin"):
        await handle_pokerdashboard(bot, user)
        return

    if sub == "pause":
        await handle_pokerpause(bot, user)
        return

    if sub == "resume":
        await handle_pokerresume(bot, user)
        return

    if sub == "forceadvance":
        await handle_pokerforceadvance(bot, user)
        return

    if sub == "forceresend":
        await handle_pokerforceresend(bot, user)
        return

    if sub == "turn":
        await handle_pokerturn(bot, user)
        return

    if sub == "pots":
        await handle_pokerpots(bot, user)
        return

    if sub == "actions":
        await handle_pokeractions(bot, user)
        return

    if sub == "resetturn":
        await handle_pokerresetturn(bot, user)
        return

    if sub == "resethand":
        await handle_pokerresethand(bot, user)
        return

    if sub == "resettable":
        await handle_pokerresettable(bot, user)
        return

    await _w(bot, user.id, "Unknown poker command. !phelp for help.")


# ── Settings setter commands ────────────────────────────────────────────────────

async def handle_setpokerbuyin(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 3 or not args[1].isdigit() or not args[2].isdigit():
        await _w(bot, user.id, "Usage: !setpokerbuyin <min> <max>")
        return
    mn, mx = int(args[1]), int(args[2])
    if mn < 1:
        await _w(bot, user.id, "Min buy-in must be at least 1."); return
    if mx < mn:
        await _w(bot, user.id, "Max must be >= min."); return
    _set("min_buyin", mn); _set("max_buyin", mx)
    await _w(bot, user.id, f"✅ Poker buy-in set: {mn}-{mx} 🪙.")


async def handle_setpokerplayers(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 3 or not args[1].isdigit() or not args[2].isdigit():
        await _w(bot, user.id, "Usage: !setpokerplayers <min> <max>")
        return
    mn, mx = int(args[1]), int(args[2])
    if mn < 2:
        await _w(bot, user.id, "Min players must be 2+."); return
    if mx < 2 or mx > 9:
        await _w(bot, user.id, "Max players must be 2-9."); return
    if mx < mn:
        await _w(bot, user.id, "Max must be >= min."); return
    _set("min_players", mn); _set("max_players", mx)
    await _w(bot, user.id, f"✅ Poker players set: {mn}-{mx}")


async def handle_setpokerlobbytimer(bot: BaseBot, user: User,
                                    args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Use !setpokerlobbytimer 20.")
        return
    secs = int(args[1])
    if not (5 <= secs <= 120):
        await _w(bot, user.id, "Timer must be 5-120 seconds.")
        return
    _set("lobby_countdown", secs)
    await _w(bot, user.id, f"✅ Poker lobby timer set to {secs}s.")


async def handle_setpokertimer(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Use !setpokertimer 30.")
        return
    secs = int(args[1])
    if not (10 <= secs <= 60):
        await _w(bot, user.id, "Turn timer must be 10-60 seconds.")
        return
    _set("turn_timer", secs)
    await _w(bot, user.id, f"✅ Poker turn timer set to {secs}s.")


async def handle_setpokerraise(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 3 or not args[1].isdigit() or not args[2].isdigit():
        await _w(bot, user.id, "Usage: !setpokerraise <min> <max>")
        return
    mn, mx = int(args[1]), int(args[2])
    if mn < 1:
        await _w(bot, user.id, "Min raise must be at least 1."); return
    if mx < mn:
        await _w(bot, user.id, "Max must be >= min."); return
    _set("min_raise", mn); _set("max_raise", mx)
    await _w(bot, user.id, f"✅ Poker raise set: {mn}-{mx} 🪙")


async def handle_setpokerdailywinlimit(bot: BaseBot, user: User,
                                        args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !setpokerdailywinlimit <amount>")
        return
    amt = int(args[1])
    if amt < 1:
        await _w(bot, user.id, "Must be positive."); return
    _set("table_daily_win_limit", amt)
    await _w(bot, user.id, f"✅ Poker daily win limit: {amt} 🪙")


async def handle_setpokerdailylosslimit(bot: BaseBot, user: User,
                                         args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !setpokerdailylosslimit <amount>")
        return
    amt = int(args[1])
    if amt < 1:
        await _w(bot, user.id, "Must be positive."); return
    _set("table_daily_loss_limit", amt)
    await _w(bot, user.id, f"✅ Poker daily loss limit: {amt} 🪙")


async def handle_resetpokerlimits(bot: BaseBot, user: User,
                                   args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !resetpokerlimits <username>")
        return
    target = args[1].lstrip("@")
    _reset_daily(target)
    await _w(bot, user.id, f"✅ Poker daily limits reset for @{target}")


# ---------------------------------------------------------------------------
# Pace modes  (/pokermode, /pokerpace, /setpokerpace)
# ---------------------------------------------------------------------------

_PACE_PRESETS: dict[str, dict] = {
    "fast": {
        "pace_preflop_secs": "20", "pace_flop_secs": "25",
        "pace_turn_secs":    "20", "pace_river_secs": "20",
        "pace_deal_delay_secs": "0.3", "turn_timer": "20",
    },
    "normal": {
        "pace_preflop_secs": "30", "pace_flop_secs": "45",
        "pace_turn_secs":    "30", "pace_river_secs": "30",
        "pace_deal_delay_secs": "0.5", "turn_timer": "30",
    },
    "long": {
        "pace_preflop_secs": "60", "pace_flop_secs": "90",
        "pace_turn_secs":    "60", "pace_river_secs": "60",
        "pace_deal_delay_secs": "1.0", "turn_timer": "60",
    },
}

_PACE_SETTING_KEYS: set[str] = {
    "pace_preflop_secs", "pace_flop_secs", "pace_turn_secs",
    "pace_river_secs", "pace_deal_delay_secs", "pace_autofold_secs",
    "pace_inactivity_secs", "turn_timer",
}


async def handle_pokermode(bot: BaseBot, user: User, args: list[str]) -> None:
    """/pokermode [fast|normal|long] — show or apply a timing preset."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 2:
        cur = _s_str("pace_mode", "normal")
        await _w(bot, user.id,
                 f"♠️ Current pace mode: {cur}\n"
                 "Options: fast / normal / long\n"
                 "Usage: !pokermode <mode>")
        return
    mode = args[1].lower()
    if mode not in _PACE_PRESETS:
        await _w(bot, user.id, "Valid modes: fast, normal, long.")
        return
    preset = _PACE_PRESETS[mode]
    for k, v in preset.items():
        _set(k, v)
    _set("pace_mode", mode)
    await _w(bot, user.id,
             f"✅ Poker pace → {mode.upper()} | "
             f"Turn timer: {preset['turn_timer']}s | "
             f"Deal delay: {preset['pace_deal_delay_secs']}s")


def _s_str(key: str, fallback: str = "") -> str:
    """Read a poker setting as a string (like _s but no int conversion)."""
    conn = db.get_connection()
    row  = conn.execute(
        "SELECT value FROM poker_settings WHERE key=?", (key,)
    ).fetchone()
    conn.close()
    return row["value"] if row else fallback


async def handle_pokerpace(bot: BaseBot, user: User) -> None:
    """/pokerpace — show all current pace settings."""
    mode  = _s_str("pace_mode", "normal")
    pref  = _s("pace_preflop_secs", 30)
    flop  = _s("pace_flop_secs",    45)
    turn  = _s("pace_turn_secs",    30)
    river = _s("pace_river_secs",   30)
    dd    = _s_str("pace_deal_delay_secs", "0.5")
    tt    = _s("turn_timer", 30)
    await _w(bot, user.id,
             f"{_PK_TIMER}: Pace:{mode.upper()} TT:{tt}s\n"
             f"Preflop:{pref}s Flop:{flop}s Turn:{turn}s River:{river}s\n"
             f"DealDelay:{dd}s | /setpokerpace <key> <val>")


async def handle_setpokerpace(bot: BaseBot, user: User, args: list[str]) -> None:
    """/setpokerpace <setting> <value> — set a single pace setting."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 3:
        keys = ", ".join(sorted(_PACE_SETTING_KEYS))
        await _w(bot, user.id,
                 f"Usage: !setpokerpace <setting> <value>\nKeys: {keys}"[:249])
        return
    key = args[1].lower()
    val = args[2]
    if key not in _PACE_SETTING_KEYS:
        await _w(bot, user.id,
                 f"Unknown setting. Valid: {', '.join(sorted(_PACE_SETTING_KEYS))}"[:249])
        return
    try:
        float(val)
    except ValueError:
        await _w(bot, user.id, "Value must be a number.")
        return
    _set(key, val)
    _set("pace_mode", "custom")
    await _w(bot, user.id, f"✅ {key} → {val} | Pace mode set to custom.")


# ---------------------------------------------------------------------------
# Stack settings  (/pokerstacks, /setpokerstack)
# ---------------------------------------------------------------------------

_STACK_SETTING_MAP: dict[str, str] = {
    "min":       "stack_min_buyin",
    "max":       "stack_max_buyin",
    "default":   "stack_default",
    "rebuymin":  "stack_rebuy_min",
    "rebuymax":  "stack_rebuy_max",
}


async def handle_pokerstacks(bot: BaseBot, user: User) -> None:
    """/pokerstacks — show all stack/buyin settings."""
    mn  = _s("stack_min_buyin",  100)
    mx  = _s("stack_max_buyin",  10000)
    def_= _s("stack_default",    1000)
    rm  = _s("stack_rebuy_min",  100)
    rmx = _s("stack_rebuy_max",  10000)
    await _w(bot, user.id,
             f"{_PK_STACK}: Buy {mn:,}-{mx:,} 🪙 | Default:{def_:,} 🪙\n"
             f"Rebuy: {rm:,}-{rmx:,} 🪙 | /setpokerstack <key> <val>")


async def handle_setpokerstack(bot: BaseBot, user: User, args: list[str]) -> None:
    """/setpokerstack min|max|default|rebuymin|rebuymax <amount>"""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 3:
        keys = ", ".join(_STACK_SETTING_MAP.keys())
        await _w(bot, user.id,
                 f"Usage: !setpokerstack <{keys}> <amount>")
        return
    key = args[1].lower()
    val = args[2]
    if key not in _STACK_SETTING_MAP:
        await _w(bot, user.id,
                 f"Unknown key. Options: {', '.join(_STACK_SETTING_MAP.keys())}")
        return
    if not val.isdigit() or int(val) < 1:
        await _w(bot, user.id, "Amount must be a positive integer.")
        return
    db_key = _STACK_SETTING_MAP[key]
    _set(db_key, int(val))
    # Sync min_buyin / max_buyin to main settings if applicable
    if key == "min":
        _set("min_buyin", int(val))
    elif key == "max":
        _set("max_buyin", int(val))
    elif key == "rebuymin":
        _set("min_buyin", int(val))
    elif key == "rebuymax":
        _set("max_buyin", int(val))
    await _w(bot, user.id, f"✅ {key} → {val} 🪙 saved.")


async def handle_dealstatus(bot: BaseBot, user: User) -> None:
    """/dealstatus — card delivery status for the active hand (staff)."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    tbl = _get_table()
    if not tbl or not tbl.get("round_id"):
        await _w(bot, user.id, "No active poker round.")
        return
    rnd     = tbl["round_id"]
    sb_name = (tbl.get("small_blind_username") or "—").lower()
    bb_name = (tbl.get("big_blind_username")   or "—").lower()
    rows    = db.get_card_delivery_status(rnd)
    ok_cnt  = sum(1 for r in rows if r.get("cards_sent"))
    bad_cnt = len(rows) - ok_cnt
    await _w(bot, user.id,
        f"♠️ Hand#{tbl.get('hand_number',0)} | Round:{rnd[-8:]} | "
        f"SB:@{sb_name} BB:@{bb_name} | Sent:{ok_cnt} Failed:{bad_cnt}")
    if not rows:
        await _w(bot, user.id, "No delivery records for this round.")
        return
    for r in rows:
        ukey     = r["username"]
        dname    = r.get("display_name") or ukey
        hc       = db.get_hole_cards(rnd, ukey)
        assigned = "Y" if (hc and hc.get("card1")) else "N"
        sent     = "Y" if r.get("cards_sent") else "N"
        attempts = r.get("attempts", 0)
        err      = (r.get("failed_reason") or "")[:30]
        role     = ""
        if ukey == sb_name:
            role = " [SB]"
        elif ukey == bb_name:
            role = " [BB]"
        line = (f"@{dname}{role}: cards={assigned} sent={sent} "
                f"tries={attempts}")
        if err:
            line += f" err={err}"
        await _w(bot, user.id, line[:249])


async def handle_setpokerturntimer(bot: BaseBot, user: User,
                                    args: list[str]) -> None:
    await handle_setpokertimer(bot, user, args)


async def handle_setpokerlimits(bot: BaseBot, user: User,
                                 args: list[str]) -> None:
    """Bulk setter: /setpokerlimits <minbuyin> <maxbuyin> <minraise> <maxraise> <winlimit> <losslimit>"""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    usage = "Use !setpokerlimits 100 5000 50 1000 10000 5000."
    if len(args) < 7 or not all(a.isdigit() for a in args[1:7]):
        await _w(bot, user.id, usage); return
    min_b, max_b, min_r, max_r, wl, ll = (int(a) for a in args[1:7])
    errors = []
    if min_b < 1:     errors.append("min_buyin≥1")
    if max_b < min_b: errors.append("max_buyin≥min_buyin")
    if min_r < 1:     errors.append("min_raise≥1")
    if max_r < min_r: errors.append("max_raise≥min_raise")
    if wl < 1:        errors.append("winlimit≥1")
    if ll < 1:        errors.append("losslimit≥1")
    if errors:
        await _w(bot, user.id, "Invalid: " + ", ".join(errors)); return
    _set("min_buyin",             min_b)
    _set("max_buyin",             max_b)
    _set("min_raise",             min_r)
    _set("max_raise",             max_r)
    _set("table_daily_win_limit", wl)
    _set("table_daily_loss_limit", ll)
    await _w(bot, user.id, "✅ Poker limits updated.")


async def handle_setpokerblinds(bot: BaseBot, user: User, args: list[str]) -> None:
    """Set SB and BB: /setpokerblinds <sb> <bb>"""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 3 or not args[1].isdigit() or not args[2].isdigit():
        await _w(bot, user.id, "Usage: !setpokerblinds <small_blind> <big_blind>")
        return
    sb, bb = int(args[1]), int(args[2])
    if sb < 1:
        await _w(bot, user.id, "Small blind must be >= 1."); return
    if bb < sb:
        await _w(bot, user.id, "Big blind must be >= small blind."); return
    _set("small_blind", sb)
    _set("big_blind",   bb)
    await _w(bot, user.id, f"✅ Poker blinds: SB={sb} 🪙 BB={bb} 🪙.")


async def handle_setpokercardmarker(bot: BaseBot, user: User, args: list[str]) -> None:
    """Set poker private-hand card marker emoji: /setpokercardmarker <emoji>"""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 2:
        current = _get_card_marker()
        await _w(bot, user.id,
            f"Poker card marker: {current} | Use !setpokercardmarker <emoji>")
        return
    marker = args[1].strip()
    if not marker:
        await _w(bot, user.id, "Usage: !setpokercardmarker <emoji>")
        return
    db.set_room_setting("poker_card_marker", marker[:10])
    await _w(bot, user.id, f"✅ Poker card marker set to: {marker[:10]}")


async def handle_setpokerante(bot: BaseBot, user: User, args: list[str]) -> None:
    """Set ante: /setpokerante <amount>"""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !setpokerante <amount> (0 to disable)")
        return
    amt = int(args[1])
    if amt < 0:
        await _w(bot, user.id, "Ante must be 0 or positive."); return
    _set("ante", amt)
    if amt == 0:
        await _w(bot, user.id, "✅ Poker ante disabled.")
    else:
        await _w(bot, user.id, f"✅ Poker ante set to {amt} 🪙 per hand.")


async def handle_setpokernexthandtimer(bot: BaseBot, user: User,
                                        args: list[str]) -> None:
    """Set delay between hands: /setpokernexthandtimer <seconds>"""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !setpokernexthandtimer <seconds>")
        return
    secs = int(args[1])
    if not (3 <= secs <= 120):
        await _w(bot, user.id, "Must be 3-120 seconds.")
        return
    _set("next_hand_delay", secs)
    await _w(bot, user.id, f"✅ Next-hand delay set to {secs}s.")


async def handle_setpokermaxstack(bot: BaseBot, user: User,
                                   args: list[str]) -> None:
    """Set max table stack: /setpokermaxstack <amount>"""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id,
            "Usage: !setpokermaxstack <amount>. Enable with !poker maxstack on")
        return
    amt = int(args[1])
    if amt < 1:
        await _w(bot, user.id, "Must be positive."); return
    _set("max_table_stack", amt)
    await _w(bot, user.id, f"✅ Max table stack set to {amt:,} 🪙.")


async def handle_setpokeridlestrikes(bot: BaseBot, user: User,
                                      args: list[str]) -> None:
    """Set idle-strike limit: /setpokeridlestrikes <n>"""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !setpokeridlestrikes <n>")
        return
    n = int(args[1])
    if n < 1:
        await _w(bot, user.id, "Must be at least 1."); return
    _set("idle_strikes_limit", n)
    await _w(bot, user.id, f"✅ Auto-sit-out after {n} idle timeout(s).")


async def handle_setpokerminplayers(bot: BaseBot, user: User,
                                     args: list[str]) -> None:
    await handle_setpokerplayers(bot, user, args)


async def handle_setpokermaxplayers(bot: BaseBot, user: User,
                                     args: list[str]) -> None:
    await handle_setpokerplayers(bot, user, args)


# ── Help pages ─────────────────────────────────────────────────────────────────

POKER_HELP_PAGES = [
    (
        "♠️ Poker Commands\n"
        "Join: !join [amount]\n"
        "Play: !check | !call | !raise [amt] | !fold | !allin\n"
        "Info: !hand | !table\n"
        "Leave: !leave"
    ),
    (
        "♠️ Poker Info\n"
        "Odds: !po  Stats: !pokerstats [user]\n"
        "Leaderboard: !pokerlb wins|pots|hands\n"
        "Sit out: !sitout  Back in: !sitin\n"
        "Rebuy: !rebuy [amount]  Stack: !mystack"
    ),
    (
        "♠️ Poker Staff (Mgr+)\n"
        "!poker on|off|cancel|start|close\n"
        "!setpokerbuyin [min] [max]\n"
        "!setpokerblinds [SB] [BB]\n"
        "!setpokertimer [sec]  !poker recoverystatus"
    ),
]


async def handle_pokerhelp(bot: BaseBot, user: User, args: list[str]) -> None:
    page = 0
    if len(args) >= 2 and args[1].isdigit():
        page = int(args[1]) - 1
    page  = max(0, min(page, len(POKER_HELP_PAGES) - 1))
    total = len(POKER_HELP_PAGES)
    msg   = POKER_HELP_PAGES[page] + f"\n(Page {page+1}/{total})"
    await _w(bot, user.id, msg[:249])


async def handle_pokerstatus(bot: BaseBot, user: User, args: list[str]) -> None:
    """!pokerstatus — show current poker table status."""
    tbl = _get_table()
    if not tbl or not tbl.get("active"):
        await _w(
            bot, user.id,
            "♠️ Poker Status\n"
            "No active table.\n"
            "Use !poker to start."
        )
        return
    phase     = tbl.get("phase", "waiting")
    pot       = tbl.get("pot", 0)
    seated    = _get_all_seated()
    n_players = len(seated)
    if phase in ("waiting", "between_hands"):
        msg = (
            f"♠️ Poker Table\n"
            f"Status: waiting\n"
            f"Players: {n_players}\n"
            f"Use !join [amount]"
        )
    else:
        msg = (
            f"♠️ Poker Status\n"
            f"Table: {phase}\n"
            f"Players: {n_players}\n"
            f"Pot: {pot:,} 🪙"
        )
    await _w(bot, user.id, msg[:249])


# ── Debug / fix / refund tools ────────────────────────────────────────────────

async def handle_pokerdebug(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Staff only.")
        return

    sub = args[1].lower() if len(args) >= 2 else ""
    tbl = _get_table()

    if sub == "seated":
        seated = _get_all_seated()
        if not seated:
            await _w(bot, user.id, "No seated players.")
            return
        parts = [
            f"@{s['username']}:{s['table_stack']} 🪙({s['status']})"
            for s in seated
        ]
        await _w(bot, user.id, ("Seated: " + "  ".join(parts))[:249])
        return

    if sub == "players":
        if not tbl or not tbl.get("round_id"):
            await _w(bot, user.id, "No active round.")
            return
        players   = _get_players(tbl["round_id"])
        page_size = 3
        page_arg  = args[2] if len(args) >= 3 and args[2].isdigit() else "1"
        page      = max(1, int(page_arg))
        total_pages = max(1, (len(players) + page_size - 1) // page_size)
        page      = min(page, total_pages)
        start     = (page - 1) * page_size
        chunk     = players[start:start + page_size]
        parts     = []
        for p in chunk:
            a = "Y" if p["acted"] else "N"
            parts.append(
                f"@{p['username']} s{p['stack']} b{p['current_bet']} "
                f"{p['status']} a{a}"
            )
        body = " | ".join(parts)
        msg  = (f"Players {page}/{total_pages}: " + body)[:249]
        await _w(bot, user.id, msg)
        if page < total_pages:
            await _w(bot, user.id, f"More: /pokerdebug players {page + 1}")
        return

    if sub == "table":
        if not tbl:
            await _w(bot, user.id, "No poker table record.")
            return
        community = json.loads(tbl["community_cards_json"] or "[]")
        board     = _fcs(community) if community else "—"
        te        = (tbl.get("turn_ends_at") or "—")[:16]
        msg = (f"Table: {tbl['phase'].title()} {board} | "
               f"Pot {tbl['pot']} 🪙 | Bet {tbl['current_bet']} | "
               f"Btn {tbl['dealer_button_index']} | "
               f"Idx {tbl['current_player_index']} | Ends {te}")
        await _w(bot, user.id, msg[:249])
        return

    if sub == "state":
        db_active    = bool(tbl and tbl["active"])
        phase        = tbl["phase"] if tbl else "—"
        round_id     = tbl.get("round_id") if tbl else None
        pot          = tbl.get("pot", 0) if tbl else 0
        next_alive   = _next_hand_task is not None and not _next_hand_task.done()
        turn_alive   = _turn_task is not None and not _turn_task.done()
        seated       = _seated_count()
        active_s     = len(_get_active_seated())
        players      = _get_players(round_id) if round_id else []
        missing_cds  = [p["username"] for p in players
                        if len(json.loads(p["hole_cards_json"] or "[]")) != 2]
        all_paid     = (all(_is_paid(round_id, p["username"]) for p in players)
                        if players else True)
        broken       = detect_poker_inconsistent_state()
        rid_short    = round_id[-8:] if round_id else "none"
        await _w(bot, user.id,
            (f"State: DB {'yes' if db_active else 'no'} | Phase {phase} | "
             f"Round …{rid_short} | Pot {pot} 🪙")[:249])
        await _w(bot, user.id,
            (f"Seated {seated} Active {active_s} | InHand {len(players)} | "
             f"MissingCards {len(missing_cds)} | PayoutsDone {'yes' if all_paid else 'no'} | "
             f"Next {'yes' if next_alive else 'no'} Turn {'yes' if turn_alive else 'no'}")[:249])
        await _w(bot, user.id,
            (f"Recovery: {'YES — ' + broken if broken else 'no'}")[:249])
        return

    # Part 6: /pokerdebug deal — validate current deal state
    if sub == "deal":
        round_id = tbl.get("round_id") if tbl else None
        seated_ct = len(_get_active_seated())
        if not round_id or not tbl or tbl["phase"] not in (
                "preflop", "flop", "turn", "river"):
            await _w(bot, user.id,
                f"Deal: No active hand | Seated {seated_ct}")
            return
        players = _get_players(round_id)
        hand_ct = len(players)
        missing = [
            p["username"]
            for p in players
            if len(json.loads(p["hole_cards_json"] or "[]")) != 2
        ]
        deck_ct = len(json.loads(tbl["deck_json"] or "[]"))
        rid_short = round_id[-8:] if round_id else "—"
        status = "OK" if not missing else f"FAIL({','.join(missing[:3])})"
        msg = (f"Deal {status} | Seated {seated_ct} | Hands {hand_ct} | "
               f"Missing {len(missing)} | Deck {deck_ct} | #{rid_short}")
        await _w(bot, user.id, msg[:249])
        return

    # Part 6: /pokerdebug hand <username> — show one player's hand status
    if sub == "hand":
        target = args[2] if len(args) >= 3 else user.username
        sp     = _get_seated(target)
        round_id = tbl.get("round_id") if tbl else None
        p_row  = None
        if round_id:
            p_row = _get_player_by_name(round_id, target)
        seated_yn  = "yes" if sp else "no"
        in_hand_yn = "yes" if p_row else "no"
        status_str = sp["status"] if sp else "—"
        stack_str  = f"{sp['table_stack']:,} 🪙" if sp else "—"
        if p_row:
            cards = json.loads(p_row["hole_cards_json"] or "[]")
            cards_yn = f"yes({len(cards)})" if cards else "no"
        else:
            cards_yn = "no"
        rid_short = round_id[-6:] if round_id else "—"
        msg = (f"@{target}: Seated={seated_yn} InHand={in_hand_yn} "
               f"Cards={cards_yn} Status={status_str} Stack={stack_str} "
               f"Round=…{rid_short}")
        await _w(bot, user.id, msg[:249])
        return

    if sub == "cleanup":
        broken = detect_poker_inconsistent_state()
        if not broken:
            await _w(bot, user.id, "Cleanup preview: No stuck hand detected.")
            return
        round_id_d = tbl.get("round_id") if tbl else None
        players_d  = _get_players(round_id_d) if round_id_d else []
        pot_d      = tbl.get("pot", 0) if tbl else 0
        all_paid_d = (all(_is_paid(round_id_d, p["username"]) for p in players_d)
                      if players_d else True)
        n_active_d = len(_get_active_seated())
        next_ph    = "between_hands" if n_active_d >= 2 else "waiting"
        if not all_paid_d and pot_d > 0:
            await _w(bot, user.id,
                (f"Cleanup preview: pot {pot_d} 🪙 unresolved — cannot auto-clear. "
                 f"Use !poker hardrefund.")[:249])
        else:
            action_d = "clear paid rows" if all_paid_d else "refund zero-pot rows"
            await _w(bot, user.id,
                (f"Cleanup preview: {action_d} | {len(players_d)} in-hand | "
                 f"Seated {_seated_count()} | Next: {next_ph} | "
                 f"Next hand: {'yes' if n_active_d >= 2 else 'no'}")[:249])
        return

    # Overview
    enabled  = "ON" if _s("poker_enabled", 1) else "OFF"
    phase    = tbl["phase"].title() if tbl else "Idle"
    pot      = tbl["pot"] if tbl else 0
    cbet     = tbl["current_bet"] if tbl else 0
    round_id = tbl.get("round_id") if tbl else None
    players  = _get_players(round_id) if round_id else []
    deck_ct  = len(json.loads(tbl["deck_json"] or "[]")) if tbl else 0
    idx      = tbl["current_player_index"] if tbl else 0
    cur_name = (players[idx]["username"]
                if players and 0 <= idx < len(players) else "—")
    timers   = []
    if _next_hand_task and not _next_hand_task.done(): timers.append("Next")
    if _turn_task      and not _turn_task.done():      timers.append("Turn")
    t_str    = "+".join(timers) if timers else "none"
    msg = (f"♠️ Debug: {enabled} | {phase} | Pot {pot} 🪙 | Bet {cbet} 🪙 | "
           f"Turn @{cur_name} | P {len(players)} | Deck {deck_ct} | T:{t_str} "
           f"| Seated:{_seated_count()}")
    await _w(bot, user.id, msg[:249])


async def handle_pokerfix(bot: BaseBot, user: User, args: list[str]) -> None:
    """Attempt to unstick a stuck poker table."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return

    tbl = _get_table()
    if not tbl or not tbl["active"]:
        await _w(bot, user.id, "No active poker table.")
        return

    phase    = tbl["phase"]
    round_id = tbl.get("round_id")
    players  = _get_players(round_id) if round_id else []
    active   = _active_players(players)

    print(f"[POKER] /pokerfix by @{user.username} | phase={phase} "
          f"active={len(active)} total={len(players)}")

    if phase == "between_hands":
        await _try_start_hand(bot)
        await _w(bot, user.id, "Tried to start next hand.")
        return

    if phase == "waiting":
        active_s = _get_active_seated()
        if active_s:
            delay = _s("next_hand_delay", 10)
            _save_table(phase="between_hands")
            await _chat(bot, f"♠️ Starting hand in {delay}s.")
            global _next_hand_task
            _cancel_task(_next_hand_task)
            _next_hand_task = asyncio.create_task(
                _next_hand_countdown(bot, delay)
            )
        else:
            await _w(bot, user.id, "No active players — table stays waiting.")
        return

    if phase in ("preflop", "flop", "turn", "river"):
        if len(_eligible_players(players)) <= 1:
            await _w(bot, user.id, "One player left — finishing hand...")
            await finish_poker_hand(bot, "everyone_folded")
            return
        table_bet = tbl["current_bet"]
        all_done  = all(
            p["acted"] and p["current_bet"] >= table_bet
            for p in active
        )
        if all_done:
            await _w(bot, user.id, "All acted — advancing street...")
            await _advance_street(bot, tbl, players)
        else:
            pending = [p for p in active if _needs_to_act(p, table_bet)]
            if not pending:
                await _w(bot, user.id, "All done — advancing street...")
                await _advance_street(bot, tbl, players)
            else:
                p = pending[0]
                for i, pl in enumerate(players):
                    if pl["user_id"] == p["user_id"]:
                        _save_table(current_player_index=i)
                        break
                tbl_fresh = _get_table()
                if tbl_fresh:
                    await _prompt_player(bot, tbl_fresh, p)
                await _w(bot, user.id, f"Prompted @{p['username']}.")
        return

    if phase == "recovery_required":
        await _w(bot, user.id, "recovery_required — use !poker hardrefund.")
        return

    if phase in ("finished", "idle"):
        _clear_hand()
        _save_table(phase="waiting")
        await _w(bot, user.id, f"Phase was '{phase}' — reset to waiting.")
        return

    await _w(bot, user.id, f"Unknown phase '{phase}' — use !poker refund.")


async def handle_pokerrefundall(bot: BaseBot, user: User, args: list[str]) -> None:
    """Return ALL table stacks to wallets and fully close the table."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return

    global _lobby_task, _turn_task, _next_hand_task
    _cancel_task(_lobby_task);     _lobby_task     = None
    _cancel_task(_turn_task);      _turn_task      = None
    _cancel_task(_next_hand_task); _next_hand_task = None

    tbl      = _get_table()
    round_id = tbl.get("round_id") if tbl else None
    refunded = 0
    count    = 0

    # First: refund any in-hand chips back to seated stacks
    if round_id:
        players = _get_players(round_id)
        for p in players:
            if _is_paid(round_id, p["username"]):
                continue
            final = p["stack"]
            _upsert_result(round_id, p["username"], p["buyin"],
                           "pokerrefundall", final, final - p["buyin"])
            _update_seated(p["username"], table_stack=final)
            _mark_paid(round_id, p["username"])
        _clear_players(round_id)

    # Now: return all table_stacks to wallets
    seated = _get_all_seated()
    for s in seated:
        stack = s["table_stack"]
        if stack > 0:
            db.adjust_balance(s["user_id"], stack)
            db.add_ledger_entry(
                s["user_id"], s["username"],
                stack, f"Poker refundall by @{user.username}"
            )
            refunded += stack
            count    += 1
        _remove_seated(s["username"])

    _log_recovery("pokerrefundall", round_id or "", tbl["phase"] if tbl else "?",
                  f"by @{user.username} | {count} players | {refunded} 🪙")
    _full_clear_table()

    await _chat(bot,
        f"♠️ Poker refunded. {count} player(s) | {refunded} 🪙 returned.")
    await _w(bot, user.id,
        f"Done. Refunded {refunded} 🪙 to {count} player(s). Table closed.")


# ── Stats & leaderboard ────────────────────────────────────────────────────────

async def handle_pokerstats(bot: BaseBot, user: User, args: list[str]) -> None:
    target_name = args[1] if len(args) >= 2 else None
    if target_name:
        s = db.get_poker_stats_by_username(target_name)
        if not s:
            await _w(bot, user.id, f"No poker stats found for @{target_name}.")
            return
        name = target_name
    else:
        db.ensure_poker_stats(user.id, user.username)
        s    = db.get_poker_stats(user.id)
        name = user.username
    net    = s.get("net_profit", 0)
    net_s  = f"+{net} 🪙" if net >= 0 else f"{net} 🪙"
    streak = s.get("current_win_streak", 0)
    best   = s.get("best_win_streak", 0)
    await _w(bot, user.id,
        f"♠️ @{name}: {s['hands_played']} hands | "
        f"{s['wins']}W {s['losses']}L {s.get('folds', 0)}F | "
        f"Net {net_s} | All-ins {s.get('allins', 0)} | "
        f"Streak {streak} (best {best})")


async def handle_pokerlb(bot: BaseBot, user: User, args: list[str]) -> None:
    mode  = args[0].lower() if args else "profit"
    valid = {"wins", "pots", "allins", "hands", "profit", "daily", "streak"}
    if mode == "weekly":
        await _w(bot, user.id, "Weekly poker leaderboard coming soon.")
        return
    if mode not in valid:
        mode = "profit"
    rows = db.get_poker_leaderboard(mode=mode, limit=5)
    if not rows:
        await _w(bot, user.id, "♠️ No poker leaderboard data yet.")
        return
    titles = {
        "wins":   "Poker Wins",
        "pots":   "Biggest Pots",
        "allins": "All-In Kings",
        "hands":  "Poker Hands",
        "profit": "Poker Profit",
        "daily":  "Daily Poker",
        "streak": "Win Streaks",
    }
    header = f"♠️ {titles.get(mode, mode.title())}"
    parts  = []
    for i, r in enumerate(rows):
        val    = r[1]
        suffix = " 🪙" if mode in ("profit", "pots", "daily") else ""
        sign   = "+" if mode in ("profit", "daily") and isinstance(val, int) and val > 0 else ""
        parts.append(f"#{i+1} @{r[0]} {sign}{val}{suffix}")
    await _w(bot, user.id, (header + "\n" + "\n".join(parts))[:249])


# ── Public API for main.py / maintenance.py ────────────────────────────────────

def get_poker_state_str() -> str:
    tbl = _get_table()
    if not tbl or not tbl["active"]:
        return "idle"
    phase = tbl["phase"]
    if phase == "recovery_required":
        return "recovery_required"
    # Detect stuck finished state
    broken = detect_poker_inconsistent_state()
    if broken and "finished_stuck" in broken:
        return f"finished_stuck({_seated_count()}seated)"
    if phase in ("waiting", "between_hands"):
        return f"{phase}({_seated_count()}seated)"
    round_id = tbl.get("round_id")
    if not round_id:
        return phase
    players = _get_players(round_id)
    active  = _active_players(players)
    return f"{phase}({len(active)}p,{tbl['pot']} 🪙,hand#{tbl.get('hand_number',0)})"


async def handle_pokercleanup(bot: BaseBot, user: User, args: list[str]) -> None:
    """Standalone /pokercleanup command — delegates to /poker cleanup logic."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    tbl = _get_table()
    if not tbl or not tbl["active"]:
        await _w(bot, user.id, "No active poker table.")
        return
    broken = detect_poker_inconsistent_state()
    if not broken:
        await _w(bot, user.id, "No stuck poker hand found.")
        return
    round_id = tbl.get("round_id") or ""
    res = _cleanup_finished_hand()
    if res["action"] == "pot_unresolved":
        await _w(bot, user.id,
            f"⚠️ Pot {res['pot']} 🪙 unresolved. Use !poker hardrefund.")
        return
    _log_recovery("cleanup", round_id, tbl["phase"],
                  f"by @{user.username} | action={res['action']}")
    global _next_hand_task
    if res["next_phase"] == "between_hands":
        _cancel_task(_next_hand_task)
        delay = _s("next_hand_delay", 10)
        _next_hand_task = asyncio.create_task(_next_hand_countdown(bot, delay))
        await _w(bot, user.id, "✅ Poker cleanup done. Table ready. Next hand starting.")
    else:
        await _w(bot, user.id, "✅ Poker cleanup done. Table ready.")


async def handle_confirmclosepoker(bot: BaseBot, user: User, args: list[str]) -> None:
    """/confirmclosepoker <code> — confirm emergency table close from /poker closeforce."""
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Owner/admin only.")
        return
    code = args[1].upper() if len(args) >= 2 else ""
    if not code or code not in _close_confirm_codes:
        await _w(bot, user.id, "Invalid or expired code. Use !poker closeforce first.")
        return
    _close_confirm_codes.pop(code, None)

    # Resolve any active hand / pot first
    tbl = _get_table()
    if tbl and tbl["active"]:
        pot = int(tbl.get("pot") or 0)
        if pot > 0 or tbl.get("round_id"):
            await _hard_refund_hand(user.username)

    # Cancel all timers
    global _lobby_task, _turn_task, _next_hand_task
    _cancel_task(_lobby_task);     _lobby_task     = None
    _cancel_task(_turn_task);      _turn_task      = None
    _cancel_task(_next_hand_task); _next_hand_task = None

    # Return all seated table stacks to wallets, remove from table
    tbl2      = _get_table()
    round_id2 = tbl2.get("round_id") if tbl2 else None
    seated    = _get_all_seated()
    refunded  = 0
    count     = 0
    for s in seated:
        stack = s["table_stack"]
        if stack > 0:
            db.adjust_balance(s["user_id"], stack)
            db.add_ledger_entry(
                s["user_id"], s["username"],
                stack, f"Poker closeforce by @{user.username}"
            )
            refunded += stack
            count    += 1
        _remove_seated(s["username"])

    _log_recovery("closeforce", round_id2 or "",
                  tbl2["phase"] if tbl2 else "?",
                  f"by @{user.username} | {count}p refunded {refunded} 🪙")
    _full_clear_table()
    await _chat(bot, f"♠️ Poker table emergency closed. {refunded} 🪙 returned.")
    await _w(bot, user.id,
             f"✅ Poker closed. {refunded} 🪙 refunded to {count} player(s)."[:249])


def soft_reset_table() -> None:
    """Cancel in-memory timers. Keep DB state intact for recovery."""
    global _lobby_task, _turn_task, _next_hand_task
    _cancel_task(_lobby_task);     _lobby_task     = None
    _cancel_task(_turn_task);      _turn_task      = None
    _cancel_task(_next_hand_task); _next_hand_task = None


def reset_table() -> None:
    """Hard reset: cancel timers, return all stacks to wallets, clear table."""
    global _lobby_task, _turn_task, _next_hand_task
    _cancel_task(_lobby_task);     _lobby_task     = None
    _cancel_task(_turn_task);      _turn_task      = None
    _cancel_task(_next_hand_task); _next_hand_task = None

    tbl      = _get_table()
    round_id = tbl.get("round_id") if tbl else None

    # Return in-hand stacks to seated
    if round_id:
        players = _get_players(round_id)
        for p in players:
            if not _is_paid(round_id, p["username"]):
                _update_seated(p["username"], table_stack=p["stack"])
                _mark_paid(round_id, p["username"])
        _clear_players(round_id)

    # Return seated stacks to wallets
    seated = _get_all_seated()
    for s in seated:
        if s["table_stack"] > 0:
            db.adjust_balance(s["user_id"], s["table_stack"])
            db.add_ledger_entry(
                s["user_id"], s["username"],
                s["table_stack"], "Poker hard-reset refund"
            )

    _full_clear_table()


# ── Staff dashboard + controls ─────────────────────────────────────────────────

async def handle_pokerdashboard(bot: BaseBot, user: User) -> None:
    """/pokerdashboard /pdash /pokeradmin — staff overview of poker state."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    tbl      = _get_table()
    enabled  = "ON" if _s("poker_enabled", 1) else "OFF"
    paused   = "PAUSED" if _poker_paused else "live"
    phase    = tbl["phase"].title() if tbl else "Idle"
    pot      = tbl["pot"] if tbl else 0
    bet      = tbl["current_bet"] if tbl else 0
    round_id = tbl.get("round_id") if tbl else None
    players  = _get_players(round_id) if round_id else []
    active_p = _active_players(players)
    allin_p  = [p for p in players if p["status"] == "allin"]
    seated   = _get_all_seated()
    active_s = _get_active_seated()
    hn       = tbl.get("hand_number", 0) if tbl else 0
    turn_alive = _turn_task is not None and not _turn_task.done()
    next_alive = _next_hand_task is not None and not _next_hand_task.done()
    broken     = detect_poker_inconsistent_state()

    # Line 1: table overview
    stuck = f"{_PK_WARN}: STUCK" if broken else "OK"
    await _w(bot, user.id, (
        f"♠️ Poker {enabled} | {paused} | {phase} | Hand#{hn} | "
        f"{_PK_POT}:{pot:,} 🪙 Bet:{bet:,} 🪙 | {stuck}")[:249])

    # Line 2: player counts + current actor
    idx = tbl["current_player_index"] if tbl else 0
    cur = players[idx]["username"] if players and 0 <= idx < len(players) else "—"
    await _w(bot, user.id, (
        f"InHand:{len(players)} Active:{len(active_p)} AllIn:{len(allin_p)} "
        f"| Seated:{len(seated)} Ready:{len(active_s)} | {_PK_TURN} @{cur}")[:249])

    # Line 3: pace + settings
    mode = _s_str("pace_mode", "normal")
    tt   = _s("turn_timer", 30)
    bb   = _s("big_blind", 100)
    sb   = _s("small_blind", 50)
    mb   = _s("min_buyin", 100)
    xb   = _s("max_buyin", 5000)
    await _w(bot, user.id, (
        f"{_PK_TIMER}: {mode} TT:{tt}s | SB:{sb}/BB:{bb} | Buy:{mb:,}-{xb:,} 🪙 | "
        f"Tasks:{'Turn' if turn_alive else '-'}/{'Next' if next_alive else '-'}")[:249])

    # Line 4: quick control reminder
    await _w(bot, user.id,
        "!poker pause|resume|forceadvance|forceresend|turn|pots|"
        "actions|resetturn|resethand|resettable | !pokerfix"[:249])


async def handle_pokerpause(bot: BaseBot, user: User) -> None:
    """/pokerpause !poker pause — pause all betting actions."""
    global _poker_paused
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    _poker_paused = True
    print(f"[POKER] paused by @{user.username}")
    await _chat(bot, "⏸️ Poker is paused by staff. Please wait.")
    await _w(bot, user.id, "✅ Poker paused. !poker resume to unpause.")


async def handle_pokerresume(bot: BaseBot, user: User) -> None:
    """/pokerresume /poker resume — resume betting actions."""
    global _poker_paused
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    _poker_paused = False
    print(f"[POKER] resumed by @{user.username}")
    await _chat(bot, "▶️ Poker resumed.")
    await _w(bot, user.id, "✅ Poker resumed.")


async def handle_pokerforceadvance(bot: BaseBot, user: User) -> None:
    """/pokerforceadvance /poker forceadvance — force advance to next street."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    tbl = _get_table()
    if not tbl or not tbl["active"]:
        await _w(bot, user.id, "No active poker table.")
        return
    phase = tbl["phase"]
    if phase not in ("preflop", "flop", "turn", "river"):
        await _w(bot, user.id, f"Cannot advance from '{phase}'.")
        return
    players = _get_players(tbl["round_id"])
    print(f"[POKER] forceadvance by @{user.username} phase={phase}")
    await _w(bot, user.id, f"Force advancing from {phase}...")
    await _advance_street(bot, tbl, players)


async def handle_pokerforceresend(bot: BaseBot, user: User) -> None:
    """/pokerforceresend /poker forceresend — resend cards to all players."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    tbl = _get_table()
    if not tbl or not tbl.get("round_id"):
        await _w(bot, user.id, "No active hand.")
        return
    round_id = tbl["round_id"]
    hn       = tbl.get("hand_number", 0)
    players  = _get_players(round_id)
    print(f"[POKER] forceresend by @{user.username} round={round_id[-8:]}")
    sent, _ = await _deliver_poker_cards_to_all(bot, round_id, hn)
    await _w(bot, user.id, f"✅ Resent cards to {sent}/{len(players)} player(s).")


async def handle_pokerturn(bot: BaseBot, user: User) -> None:
    """/pokerturn /poker turn — show who has the current action."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    tbl = _get_table()
    if not tbl or not tbl.get("round_id"):
        await _w(bot, user.id, "No active hand.")
        return
    players = _get_players(tbl["round_id"])
    idx     = tbl["current_player_index"]
    phase   = tbl["phase"].title()
    pot     = tbl["pot"]
    bet     = tbl["current_bet"]
    if players and 0 <= idx < len(players):
        cur = players[idx]
        owe = max(0, bet - cur["current_bet"])
        await _w(bot, user.id, (
            f"{_PK_TURN} @{cur['username']} (#{idx}) | {phase} | "
            f"{_PK_POT}:{pot:,} 🪙 Bet:{bet:,} 🪙 Owe:{owe:,} 🪙 | "
            f"{_PK_STACK}:{cur['stack']:,} 🪙")[:249])
    else:
        await _w(bot, user.id, f"{_PK_INFO}: {phase} | No current player (idx={idx})")


async def handle_pokerpots(bot: BaseBot, user: User) -> None:
    """/pokerpots /poker pots — show pot + per-player bet breakdown."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    tbl = _get_table()
    if not tbl or not tbl.get("round_id"):
        await _w(bot, user.id, "No active hand.")
        return
    players = _get_players(tbl["round_id"])
    pot     = tbl["pot"]
    bet     = tbl["current_bet"]
    parts   = [f"@{p['username']}:{p['current_bet']:,} 🪙"
               for p in players if p["status"] in ("active", "allin")]
    await _w(bot, user.id,
        (f"{_PK_POT}:{pot:,} 🪙 | Bet:{bet:,} 🪙 | " + "  ".join(parts))[:249])


async def handle_pokeractions(bot: BaseBot, user: User) -> None:
    """/pokeractions /poker actions — show acted/pending players this street."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    tbl = _get_table()
    if not tbl or not tbl.get("round_id"):
        await _w(bot, user.id, "No active hand.")
        return
    players = _get_players(tbl["round_id"])
    bet     = tbl["current_bet"]
    acted   = [p["username"] for p in players if p.get("acted") and p["status"] == "active"]
    pending = [p["username"] for p in players if not p.get("acted") and p["status"] == "active"]
    allins  = [p["username"] for p in players if p["status"] == "allin"]
    msg = (f"{_PK_INFO}: Bet:{bet:,} 🪙 | "
           f"Acted:{','.join('@'+u for u in acted) or '—'} | "
           f"Pending:{','.join('@'+u for u in pending) or '—'} | "
           f"{_PK_ALLIN}:{','.join('@'+u for u in allins) or '—'}")
    await _w(bot, user.id, msg[:249])


async def handle_pokerresetturn(bot: BaseBot, user: User) -> None:
    """/pokerresetturn /poker resetturn — re-prompt the current player."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    tbl = _get_table()
    if not tbl or not tbl.get("round_id"):
        await _w(bot, user.id, "No active hand.")
        return
    if tbl["phase"] not in ("preflop", "flop", "turn", "river"):
        await _w(bot, user.id, "No active betting round.")
        return
    players = _get_players(tbl["round_id"])
    idx     = tbl["current_player_index"]
    if not players or not (0 <= idx < len(players)):
        await _w(bot, user.id, "Cannot determine current player.")
        return
    p = players[idx]
    print(f"[POKER] resetturn by @{user.username} | re-prompting @{p['username']}")
    await _prompt_player(bot, tbl, p)
    await _w(bot, user.id, f"✅ Re-prompted @{p['username']}.")


async def handle_pokerresethand(bot: BaseBot, user: User) -> None:
    """/pokerresethand /poker resethand — cancel current hand and deal next."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    tbl = _get_table()
    if not tbl or not tbl["active"]:
        await _w(bot, user.id, "No active poker table.")
        return
    round_id = tbl.get("round_id")
    pot      = tbl.get("pot", 0)
    print(f"[POKER] resethand by @{user.username} | round={round_id and round_id[-8:]} pot={pot} 🪙")
    global _turn_task, _next_hand_task
    _cancel_task(_turn_task);      _turn_task      = None
    _cancel_task(_next_hand_task); _next_hand_task = None
    # Return in-hand stacks to seated
    if round_id:
        players = _get_players(round_id)
        for p in players:
            if not _is_paid(round_id, p["username"]):
                _update_seated(p["username"], table_stack=p["stack"])
                _mark_paid(round_id, p["username"])
        _clear_players(round_id)
    _save_table(phase="between_hands", pot=0, current_bet=0,
                round_id=None, community_cards_json="[]", deck_json="[]")
    delay = _s("next_hand_delay", 10)
    _next_hand_task = asyncio.create_task(_next_hand_countdown(bot, delay))
    await _chat(bot, f"♠️ Hand cancelled by staff. Next hand in {delay}s.")
    await _w(bot, user.id, "✅ Hand reset. Next hand starting shortly.")


async def handle_pokerresettable(bot: BaseBot, user: User) -> None:
    """/pokerresettable /poker resettable — alias for /poker refundtable."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    fake_user_args = ["poker", "refundtable"]
    await _dispatch(bot, user, fake_user_args)


async def handle_pokerstylepreview(bot: BaseBot, user: User) -> None:
    """/pokerstylepreview — preview all poker color label styles."""
    samples = [
        f"{_PK_DEALER}: Drawing cards...",
        f"{_PK_HAND}: A♠ K♦ | {_PK_BOARD}: 7♣ 2♥ 9♦",
        f"{_PK_TURN} @You | {_PK_POT}: 500 🪙 | Call: 100 🪙",
        f"{_PK_CHECK} — @player",
        f"{_PK_CALL} — @player called 100 🪙 | {_PK_POT}: 300 🪙",
        f"{_PK_RAISE} — @player +200 🪙 | New bet: 300 🪙",
        f"{_PK_FOLD} — @player",
        f"{_PK_ALLIN} — @player all-in 500 🪙 | {_PK_POT}: 1,000 🪙",
        f"{_PK_WIN} — @player wins 1,000 🪙 with Full House",
        f"{_PK_LOSS} — @player Two Pair",
        f"{_PK_WARN}: Must call 200 🪙 or fold",
        f"{_PK_BLIND} SB:@p1(50 🪙) BB:@p2(100 🪙) | {_PK_POT}: 150 🪙",
        f"{_PK_TIMER}: 30s | {_PK_STACK}: 1,000 🪙 | {_PK_INFO}: Preview done.",
    ]
    for s in samples:
        await _w(bot, user.id, s[:249])


async def handle_pokerplayers(bot: BaseBot, user: User) -> None:
    """/pokerplayers — show all seated players, stacks, and hand status (staff)."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    seated = _get_all_seated()
    if not seated:
        await _w(bot, user.id, "No players at the poker table.")
        return
    tbl      = _get_table()
    round_id = tbl.get("round_id") if tbl else None
    players  = _get_players(round_id) if round_id else []
    idx      = tbl["current_player_index"] if tbl else -1
    cur_name = (players[idx]["username"].lower()
                if players and 0 <= idx < len(players) else "")

    hn_now = tbl.get("hand_number", 0) if tbl else 0
    lines: list[str] = [f"{_PK_INFO}: Seats({len(seated)}) Hand#{hn_now}"]
    for sp in seated:
        uname  = sp["username"]
        stack  = sp["table_stack"]
        status = sp["status"]
        p_row  = next((p for p in players
                       if p["username"].lower() == uname.lower()), None)
        if p_row:
            pst = p_row["status"]
            tag = f" {_PK_FOLD}" if pst == "folded" else f" {_PK_ALLIN}" if pst == "allin" else " ♠"
        elif status == "sitting_out":
            tag = " 💤"
        else:
            tag = ""
        actor = " ◄" if uname.lower() == cur_name else ""
        lines.append(f"@{uname}:{stack:,} 🪙{tag}{actor}")

    chunk = lines[0]
    for line in lines[1:]:
        candidate = chunk + " | " + line
        if len(candidate) <= 249:
            chunk = candidate
        else:
            await _w(bot, user.id, chunk)
            chunk = line
    if chunk:
        await _w(bot, user.id, chunk)


# ── handle_poker_user_left — called from on_user_leave ────────────────────────

async def handle_poker_user_left(bot: "BaseBot", user: "User") -> None:
    """Handle room-exit for a poker player.

    - Not seated → no-op.
    - Before hand (or not in this hand): cash out table_stack to wallet.
    - In active hand, all-in: keep eligible for showdown; cash out after hand.
    - In active hand, active: auto-fold, advance turn, cash out remaining after hand.
    """
    import os as _os
    sp = _get_seated(user.username)
    if sp is None:
        return

    tbl   = _get_table()
    phase = tbl["phase"] if tbl else "idle"
    _td   = db.get_display_name(user.id, user.username)

    if phase in ("preflop", "flop", "turn", "river"):
        round_id = tbl["round_id"]
        p = _get_player(round_id, user.id)
        if p is None:
            p = _get_player_by_name(round_id, user.username)

        if p and p["status"] == "allin":
            # All-in — keep eligible for showdown, cash out automatically after
            _update_seated(user.username, leaving_after_hand=1)
            await _chat(bot,
                f"🚪 @{user.username} left but is all-in. Hand runs to showdown.")
            print(f"[POKER LEAVE] @{user.username} left room, status=allin — kept for showdown")
            return

        if p and p["status"] == "active":
            # Active — auto-fold and advance
            _save_player(round_id, user.id, status="folded", acted=1)
            _update_seated(user.username, leaving_after_hand=1)
            await _chat(bot,
                f"🚪 @{user.username} left during hand. Auto-folded.")
            print(f"[POKER LEAVE] @{user.username} auto-folded, stack cashed after hand")
            # Only the poker bot advances the turn
            if _os.getenv("BOT_MODE", "") == "poker":
                await advance_turn_or_round(bot)
            return

        # Folded / observer — cash out table stack now
        stack = sp["table_stack"]
        _remove_seated(user.username)
        if stack > 0:
            db.adjust_balance(user.id, stack)
            db.add_ledger_entry(user.id, user.username, stack,
                                "Poker leave (room exit, not in hand)")
            await _chat(bot, f"🚪 {_td} left poker. Cashed out {stack:,} 🪙.")
        print(f"[POKER LEAVE] @{user.username} left room, not in hand, cashed {stack}")
    else:
        # No active hand — cash out immediately
        stack = sp["table_stack"]
        _remove_seated(user.username)
        if stack > 0:
            db.adjust_balance(user.id, stack)
            db.add_ledger_entry(user.id, user.username, stack,
                                "Poker leave (room exit)")
            await _chat(bot, f"🚪 {_td} left poker. Cashed out {stack:,} 🪙.")
        print(f"[POKER LEAVE] @{user.username} left room, cashed out {stack}")
