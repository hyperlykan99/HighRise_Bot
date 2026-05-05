"""
modules/poker.py
Texas Hold'em Lite — crash-safe, fully DB-persisted.

Public API consumed by main.py:
  handle_poker(bot, user, args)
  handle_pokerhelp(bot, user, args)
  handle_setpokerbuyin / handle_setpokerplayers / handle_setpokerlobbytimer
  handle_setpokertimer / handle_setpokerraise
  handle_setpokerdailywinlimit / handle_setpokerdailylosslimit
  handle_resetpokerlimits(bot, user, args)
  startup_poker_recovery(bot)
  soft_reset_table()   — softrestart (cancel tasks, keep DB)
  reset_table()        — hard reset (cancel tasks + refund)
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

# ── In-memory timer handles (not persisted) ────────────────────────────────────
_lobby_task: Optional[asyncio.Task] = None
_turn_task:  Optional[asyncio.Task] = None


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
    """Load a single int setting from poker_settings."""
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


# ── DB table helpers ────────────────────────────────────────────────────────────

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

def _clear_table() -> None:
    conn = db.get_connection()
    conn.execute(
        "UPDATE poker_active_table SET active=0, phase='idle', round_id=NULL, "
        "created_at=NULL, updated_at=NULL, lobby_started_at=NULL, "
        "lobby_ends_at=NULL, round_started_at=NULL, turn_ends_at=NULL, "
        "current_player_index=0, dealer_button_index=0, deck_json='[]', "
        "community_cards_json='[]', pot=0, current_bet=0, "
        "last_raiser_username=NULL, settings_snapshot_json=NULL, "
        "restored_after_restart=0 WHERE id=1"
    )
    conn.commit()
    conn.close()

def _get_players(round_id: str) -> list[dict]:
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM poker_active_players WHERE round_id=? ORDER BY id",
        (round_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def _get_player(round_id: str, user_id: str) -> Optional[dict]:
    conn = db.get_connection()
    row  = conn.execute(
        "SELECT * FROM poker_active_players WHERE round_id=? AND user_id=?",
        (round_id, user_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def _save_player(round_id: str, user_id: str, **kw) -> None:
    if not kw:
        return
    kw["acted_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in kw)
    vals = list(kw.values()) + [round_id, user_id]
    conn = db.get_connection()
    conn.execute(
        f"UPDATE poker_active_players SET {sets} WHERE round_id=? AND user_id=?",
        vals,
    )
    conn.commit()
    conn.close()

def _insert_player(round_id: str, username: str, user_id: str, buyin: int) -> None:
    conn = db.get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO poker_active_players "
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

def _active_players(players: list[dict]) -> list[dict]:
    """Players who can still act (not allin, not folded)."""
    return [p for p in players if p["status"] == "active"]

def _eligible_players(players: list[dict]) -> list[dict]:
    """Players still in hand (active + allin) — eligible for pot/showdown."""
    return [p for p in players if p["status"] in ("active", "allin")]

def _needs_to_act(p: dict, table_current_bet: int) -> bool:
    """True if this active (non-allin) player still needs to act."""
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


# ── Daily limit check (call before join) ────────────────────────────────────────

def _check_daily_limits(username: str, buyin: int) -> Optional[str]:
    """Return error string if player is blocked by daily limits, else None."""
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


# ── Core: pay out a single player safely ───────────────────────────────────────

def _pay_player(p: dict, result: str, pot_share: int, round_id: str) -> int:
    """
    Pay (stack_remaining + pot_share) to a player.
    Returns total coins returned to the player's wallet.
    Skips if already paid.
    """
    if _is_paid(round_id, p["username"]):
        return 0
    total = p["stack"] + pot_share
    net   = total - p["buyin"]
    _upsert_result(round_id, p["username"], p["buyin"], result, total, net)
    if total > 0:
        db.adjust_balance(p["user_id"], total)
        _add_daily_net(p["username"], net)
        db.add_ledger_entry(
            p["user_id"], p["username"],
            total, f"Poker hand {result} rid={round_id}"
        )
    _mark_paid(round_id, p["username"])
    return total


# ── finish_poker_hand ──────────────────────────────────────────────────────────

async def finish_poker_hand(bot: BaseBot, reason: str) -> None:
    """
    End the current hand for any reason. Pays winner(s), returns stacks,
    updates stats, clears table. Idempotent per round_id.
    """
    global _lobby_task, _turn_task
    _cancel_task(_lobby_task); _lobby_task = None
    _cancel_task(_turn_task);  _turn_task  = None

    tbl = _get_table()
    if not tbl or not tbl["active"] or tbl["phase"] in ("idle", "finished"):
        return

    round_id  = tbl["round_id"]
    phase     = tbl["phase"]
    pot       = tbl["pot"]
    community = json.loads(tbl["community_cards_json"] or "[]")
    players   = _get_players(round_id)

    _save_table(phase="finished")

    # ── Determine winner(s) and pay ─────────────────────────────────────────

    if reason in ("everyone_folded", "not_enough_players", "cancelled",
                  "forcefinish", "recovery_refund"):
        # Give pot to last eligible player (active or allin)
        eligible = _eligible_players(players)
        if reason == "everyone_folded" and len(eligible) == 1:
            w = eligible[0]
            net_w = pot  # they get pot on top of their uncommitted stack
            _pay_player(w, "win_folds", pot, round_id)
            for p in players:
                if p["username"] != w["username"]:
                    _pay_player(p, "fold_return", 0, round_id)
            await _chat(bot,
                f"🏆 @{w['username']} wins {pot}c. Everyone else folded.")
            db.update_poker_stats(
                w["user_id"], w["username"],
                wins=1, total_won=pot, biggest_pot=pot,
                biggest_win=pot, net_delta=net_w - w["buyin"],
                total_buyin=w["buyin"], hands=1,
            )
            for p in players:
                if p["username"] != w["username"]:
                    net_l = -p["total_contributed"]
                    db.update_poker_stats(
                        p["user_id"], p["username"],
                        losses=1, total_lost=p["total_contributed"],
                        folds=int(p["status"] == "folded"),
                        net_delta=net_l,
                        total_buyin=p["buyin"], hands=1,
                    )
        else:
            # Refund everyone (cancel / not_enough / forcefinish fallback)
            for p in players:
                _pay_player(p, "refund", 0, round_id)
            if reason in ("cancelled", "recovery_refund"):
                await _chat(bot,
                    "♠️ Poker cancelled. All chips refunded.")
            elif reason == "not_enough_players":
                await _chat(bot,
                    "Poker cancelled. Need 2+ players. Buy-ins refunded.")

    elif reason == "showdown":
        eligible = _eligible_players(players)   # active + allin
        if len(eligible) == 1:
            # One eligible player — treat as everyone_folded
            await finish_poker_hand(bot, "everyone_folded")
            return
        if len(eligible) == 0:
            # Degenerate — refund
            for p in players:
                _pay_player(p, "refund", 0, round_id)
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
                    _pay_player(p, "refund", 0, round_id)
            else:
                best      = max(scores.values())
                winners   = [p for p in eligible if scores.get(p["username"]) == best]
                share     = pot // len(winners)
                remainder = pot % len(winners)
                winner_set = {w["username"] for w in winners}

                # Return uncommitted stacks + pot share to winners
                for i, w in enumerate(winners):
                    pot_share = share + (remainder if i == 0 else 0)
                    _pay_player(w, "win_showdown", pot_share, round_id)

                # Return uncommitted stacks only to non-winners
                for p in players:
                    if p["username"] not in winner_set:
                        stat = p["status"]
                        result = "fold_return" if stat == "folded" else "loss_showdown"
                        _pay_player(p, result, 0, round_id)

                # Announce
                board  = _fcs(community)
                hname  = _HAND_NAMES.get(best[0], "High Card")
                if len(winners) == 1:
                    w = winners[0]
                    await _chat(bot, f"👀 Showdown! Board: {board}")
                    await _chat(bot, f"🏆 @{w['username']} wins {pot}c with {hname}.")
                    for p in eligible:
                        h  = _fcs(json.loads(p["hole_cards_json"] or "[]"))
                        hn = _HAND_NAMES.get(scores.get(p["username"], (0,))[0], "")
                        await _chat(bot, f"  @{p['username']}: {h} — {hn}")
                else:
                    wnames = " & ".join(f"@{w['username']}" for w in winners)
                    await _chat(bot,
                        f"🤝 Split pot: {wnames} each get {share}c. {hname}.")

                # Update stats for all players
                for p in players:
                    won    = p["username"] in winner_set
                    folded = p["status"] == "folded"
                    is_elig = p["username"] in scores
                    pot_won = share if won else 0
                    net_d   = (pot_won - p["total_contributed"]) if won else -p["total_contributed"]
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

    await _chat(bot, "♠️ Poker table closed. New game: /p <buyin>")
    _clear_table()
    _clear_players(round_id)


# ── advance_turn_or_round ──────────────────────────────────────────────────────

async def advance_turn_or_round(bot: BaseBot) -> None:
    """
    Called after every action. Determines if we advance to the next player,
    next street, or end the hand. Handles all-in players correctly.
    """
    global _turn_task
    _cancel_task(_turn_task); _turn_task = None

    tbl = _get_table()
    if not tbl or not tbl["active"] or tbl["phase"] not in (
            "preflop", "flop", "turn", "river"):
        return

    round_id    = tbl["round_id"]
    table_bet   = tbl["current_bet"]
    players     = _get_players(round_id)
    eligible    = _eligible_players(players)    # active + allin
    can_act     = _active_players(players)      # active only (not allin)

    # Only one eligible player left — they win
    if len(eligible) <= 1:
        await finish_poker_hand(bot, "everyone_folded")
        return

    # All remaining eligible are all-in → deal remaining board to showdown
    if len(can_act) == 0:
        await _deal_to_showdown(bot, tbl)
        return

    # Find next active (non-allin) player who needs to act
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

    # All active non-allin players have acted and matched the bet → advance street
    await _advance_street(bot, tbl, players)


async def _deal_to_showdown(bot: BaseBot, tbl: dict) -> None:
    """All eligible players are all-in — deal remaining board cards and showdown."""
    round_id  = tbl["round_id"]
    deck      = json.loads(tbl["deck_json"] or "[]")
    community = json.loads(tbl["community_cards_json"] or "[]")

    new_cards: list[str] = []
    while len(community) < 5 and deck:
        c = deck.pop()
        community.append(c)
        new_cards.append(c)

    if new_cards:
        await _chat(bot, f"🔥 All-in! Board: {_fcs(community)}")
        _save_table(phase="river",
                    deck_json=json.dumps(deck),
                    community_cards_json=json.dumps(community))

    await finish_poker_hand(bot, "showdown")


async def _advance_street(bot: BaseBot, tbl: dict, players: list[dict]) -> None:
    phase    = tbl["phase"]
    round_id = tbl["round_id"]
    deck     = json.loads(tbl["deck_json"] or "[]")
    community = json.loads(tbl["community_cards_json"] or "[]")

    # Reset per-player current_bet and acted for new street
    conn = db.get_connection()
    conn.execute(
        "UPDATE poker_active_players SET current_bet=0, acted=0 "
        "WHERE round_id=? AND status='active'",
        (round_id,),
    )
    conn.commit()
    conn.close()
    _save_table(current_bet=0)

    if phase == "preflop":
        f = [deck.pop(), deck.pop(), deck.pop()]
        community.extend(f)
        _save_table(
            phase="flop",
            deck_json=json.dumps(deck),
            community_cards_json=json.dumps(community),
        )
        await _chat(bot,
            f"🃏 Flop: {_fcs(f)} | Pot: {tbl['pot']}c")
        await _start_street(bot, "flop", round_id, players)

    elif phase == "flop":
        t = deck.pop()
        community.append(t)
        _save_table(
            phase="turn",
            deck_json=json.dumps(deck),
            community_cards_json=json.dumps(community),
        )
        await _chat(bot,
            f"🃏 Turn: {_fc(t)} | Board: {_fcs(community)}")
        await _start_street(bot, "turn", round_id, players)

    elif phase == "turn":
        r = deck.pop()
        community.append(r)
        _save_table(
            phase="river",
            deck_json=json.dumps(deck),
            community_cards_json=json.dumps(community),
        )
        await _chat(bot,
            f"🃏 River: {_fc(r)} | Board: {_fcs(community)}")
        await _start_street(bot, "river", round_id, players)

    elif phase == "river":
        await finish_poker_hand(bot, "showdown")


async def _start_street(bot: BaseBot, phase: str, round_id: str,
                         players: list[dict]) -> None:
    """Find the first active (non-allin) player and prompt them, or auto-showdown."""
    eligible = _eligible_players(players)
    if len(eligible) <= 1:
        await finish_poker_hand(bot, "everyone_folded")
        return
    can_act = _active_players(players)
    if len(can_act) == 0:
        # All eligible are allin — deal remaining board
        tbl = _get_table()
        if tbl:
            await _deal_to_showdown(bot, tbl)
        return
    for i, p in enumerate(players):
        if p["status"] == "active":
            _save_table(current_player_index=i)
            tbl = _get_table()
            if tbl:
                await _prompt_player(bot, tbl, p)
            break


# ── Prompt a player for their turn ─────────────────────────────────────────────

async def _prompt_player(bot: BaseBot, tbl: dict, p: dict) -> None:
    global _turn_task
    _cancel_task(_turn_task)

    turn_secs = _s("turn_timer", 20)
    ends_at   = _now()
    _save_table(turn_ends_at=ends_at)

    owe = max(0, tbl["current_bet"] - p["current_bet"])
    pot = tbl["pot"]
    if owe > 0:
        msg = (f"➡️ @{p['username']} turn | Pot:{pot}c | "
               f"To call:{owe}c | /call /r <amt> /fold /ai")
    else:
        msg = (f"➡️ @{p['username']} turn | Pot:{pot}c | "
               f"Free check | /check /r <amt> /fold /ai")
    await _chat(bot, msg[:249])
    # Whisper hand info to the current player
    try:
        tbl2 = _get_table()
        if tbl2:
            pp = _get_player(tbl2["round_id"], p["user_id"])
            if pp:
                cards = json.loads(pp["hole_cards_json"] or "[]")
                board = json.loads(tbl2["community_cards_json"] or "[]")
                if cards:
                    strength = _hand_strength_label(cards, board)
                    draws    = _detect_draws(cards, board)
                    label    = strength + (" + " + draws if draws else "")
                    board_str = _fcs(board) if board else "—"
                    await bot.highrise.send_whisper(
                        p["user_id"],
                        f"🂡 {_fcs(cards)} | Board:{board_str} | {label}"
                    )
    except Exception:
        pass

    round_id = tbl["round_id"]
    _turn_task = asyncio.create_task(
        _turn_timeout(bot, p["user_id"], p["username"], round_id, owe > 0)
    )


async def _turn_timeout(bot: BaseBot, uid: str, uname: str,
                         round_id: str, must_call: bool) -> None:
    global _turn_task
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

    if must_call and tbl["current_bet"] > p["current_bet"]:
        _save_player(round_id, uid, status="folded", acted=1)
        await _chat(bot, f"⏰ @{uname} timed out and folded.")
    else:
        _save_player(round_id, uid, acted=1)
        await _chat(bot, f"⏰ @{uname} timed out and checked.")

    await advance_turn_or_round(bot)


# ── Lobby & game start ─────────────────────────────────────────────────────────

async def _lobby_countdown(bot: BaseBot, round_id: str, secs: int) -> None:
    global _lobby_task
    await asyncio.sleep(secs)

    tbl = _get_table()
    if not tbl or not tbl["active"] or tbl["round_id"] != round_id:
        return
    if tbl["phase"] != "lobby":
        return

    players = _get_players(round_id)
    min_pl  = _s("min_players", 2)
    if len(players) < min_pl:
        # Refund and clear
        for p in players:
            if not _is_paid(round_id, p["username"]):
                db.adjust_balance(p["user_id"], p["buyin"])
                _mark_paid(round_id, p["username"])
        _clear_table()
        _clear_players(round_id)
        await _chat(bot,
            "Poker cancelled. Need 2+ players. Buy-ins refunded.")
        return

    await _start_hand(bot, tbl, players)


async def _start_hand(bot: BaseBot, tbl: dict, players: list[dict]) -> None:
    round_id = tbl["round_id"]
    max_pl   = _s("max_players", 6)

    deck = _make_deck()
    random.shuffle(deck)
    buyin_to_pot = _s("poker_buyin_to_pot", 0)

    # Mark all lobby players as active, deal hole cards, init stacks
    conn = db.get_connection()
    conn.execute(
        "UPDATE poker_active_players SET status='active', current_bet=0, "
        "total_contributed=0, acted=0, hole_cards_json='[]' WHERE round_id=?",
        (round_id,),
    )
    conn.commit()
    conn.close()

    players = _get_players(round_id)  # reload
    initial_pot = 0
    for p in players:
        h = [deck.pop(), deck.pop()]
        buyin_contribution = p["buyin"] if buyin_to_pot else 0
        stack = p["buyin"] - buyin_contribution
        initial_pot += buyin_contribution
        conn = db.get_connection()
        conn.execute(
            "UPDATE poker_active_players SET hole_cards_json=?, stack=?, "
            "total_contributed=? WHERE round_id=? AND user_id=?",
            (json.dumps(h), stack, buyin_contribution, round_id, p["user_id"]),
        )
        conn.commit()
        conn.close()
        try:
            await bot.highrise.send_whisper(
                p["user_id"],
                f"🂡 Your hand: {_fcs(h)} | Use /poker hand anytime."
            )
        except Exception:
            pass

    _save_table(
        phase="preflop",
        deck_json=json.dumps(deck),
        community_cards_json="[]",
        pot=initial_pot,
        current_bet=0,
        current_player_index=0,
        round_started_at=_now(),
        restored_after_restart=0,
    )

    await _chat(bot,
        f"♠️ Poker started! Pot:{initial_pot}c. Check /ph for your cards.")

    # Find first active player
    players = _get_players(round_id)
    tbl     = _get_table()
    if tbl:
        await _start_street(bot, "preflop", round_id, players)


# ── Action implementations ─────────────────────────────────────────────────────

async def _do_check(bot: BaseBot, round_id: str, uid: str, uname: str) -> None:
    _save_player(round_id, uid, acted=1)
    await _chat(bot, f"✅ @{uname} checks.")
    await advance_turn_or_round(bot)


async def _do_call(bot: BaseBot, round_id: str, p: dict, tbl: dict) -> None:
    owe = tbl["current_bet"] - p["current_bet"]
    if owe <= 0:
        await _do_check(bot, round_id, p["user_id"], p["username"])
        return
    if p["stack"] <= 0:
        await _w(bot, p["user_id"], "Already all-in.")
        return
    # All-in call — player can't cover the full owe
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
            f"🔥 @{p['username']} calls all-in for {commit}c! Pot: {new_pot}c.")
        await advance_turn_or_round(bot)
        return
    # Normal call
    new_stack   = p["stack"] - owe
    new_cbet    = p["current_bet"] + owe
    new_contrib = p["total_contributed"] + owe
    new_pot     = tbl["pot"] + owe
    _save_player(round_id, p["user_id"],
                 stack=new_stack, current_bet=new_cbet,
                 total_contributed=new_contrib, acted=1)
    _save_table(pot=new_pot)
    await _chat(bot, f"✅ @{p['username']} calls {owe}c. Pot: {new_pot}c.")
    await advance_turn_or_round(bot)


async def _do_raise(bot: BaseBot, round_id: str, p: dict, tbl: dict,
                    raise_by: int) -> None:
    min_r  = _s("min_raise", 50)
    max_r  = _s("max_raise", 1000)
    rl_on  = _s("raise_limit_enabled", 1)
    if raise_by < min_r:
        await _w(bot, p["user_id"], f"Minimum raise is {min_r}c.")
        return
    if rl_on and raise_by > max_r:
        await _w(bot, p["user_id"], f"Maximum raise is {max_r}c.")
        return
    raise_to = tbl["current_bet"] + raise_by
    extra    = raise_to - p["current_bet"]
    if extra > p["stack"]:
        await _w(bot, p["user_id"], "Not enough poker chips.")
        return
    # If raise uses up entire stack → treat as all-in raise
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
    # Reset all other active players' acted flag
    conn = db.get_connection()
    conn.execute(
        "UPDATE poker_active_players SET acted=0 "
        "WHERE round_id=? AND status='active' AND user_id!=?",
        (round_id, p["user_id"]),
    )
    conn.commit()
    conn.close()
    await _chat(bot,
        f"⬆️ @{p['username']} raises {raise_by}c. To call: {raise_to}c.")
    await advance_turn_or_round(bot)


async def _do_fold(bot: BaseBot, round_id: str, p: dict) -> None:
    _save_player(round_id, p["user_id"], status="folded", acted=1)
    await _chat(bot, f"🂠 @{p['username']} folds.")
    await advance_turn_or_round(bot)


async def _do_allin(bot: BaseBot, round_id: str, p: dict, tbl: dict) -> None:
    """Commit all remaining chips. Works as a raise if it exceeds current_bet."""
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
    # If this raises the table bet, reset other active players' acted flag
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
    await _chat(bot, f"🔥 @{p['username']} ALL-IN for {commit}c! Pot: {new_pot}c.")
    # Track all-in stat immediately
    db.update_poker_stats(p["user_id"], p["username"], allins=1)
    await advance_turn_or_round(bot)


# ── Hand strength / draws / odds ────────────────────────────────────────────────

def _hand_strength_label(hole_cards: list[str], community: list[str]) -> str:
    """Return best hand name from hole + community cards."""
    all_cards = hole_cards + community
    if len(all_cards) < 2:
        return "No cards"
    if len(all_cards) < 5:
        # With fewer than 5 cards evaluate partial combos
        best: Optional[tuple] = None
        for n in range(2, min(5, len(all_cards) + 1)):
            for combo in combinations(all_cards, n):
                s = _score5(list(combo)) if n == 5 else _score5(list(combo) + ["2c"] * (5 - n))
                if best is None or s > best:
                    best = s
        score = best or (0,)
    else:
        score = _best_hand(all_cards)
    return _HAND_NAMES.get(score[0], "High Card")


def _detect_draws(hole_cards: list[str], community: list[str]) -> str:
    """Detect flush draw or straight draw from hole + community (4 cards min)."""
    all_cards = hole_cards + community
    if len(all_cards) < 3:
        return ""
    draws: list[str] = []
    # Flush draw: 4 cards of same suit
    suit_cnt = Counter(c[1] for c in all_cards)
    if max(suit_cnt.values()) == 4:
        draws.append("Flush Draw")
    # Open-ended straight draw: 4 consecutive distinct ranks
    ranks = sorted(set(_RANKS.index(c[0]) for c in all_cards))
    found_straight_draw = False
    for i in range(len(ranks) - 3):
        window = ranks[i:i + 4]
        if window[-1] - window[0] == 3:
            draws.append("Straight Draw")
            found_straight_draw = True
            break
    return " + ".join(draws)


def _calc_odds(hole_cards: list[str], community: list[str],
               deck: list[str], opponents: int) -> int:
    """Approximate win % via Monte Carlo (200 simulations). Returns -1 on error."""
    if len(hole_cards) < 2 or opponents < 1:
        return -1
    needed   = 5 - len(community)
    rem_deck = [c for c in deck if c not in hole_cards and c not in community]
    if len(rem_deck) < needed + opponents * 2:
        return -1
    wins = 0
    sims = 200
    for _ in range(sims):
        shuffled = rem_deck.copy()
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
    global _lobby_task

    if not _s("poker_enabled", 1):
        await _w(bot, user.id, "Poker is currently disabled.")
        return

    tbl = _get_table()
    if tbl and tbl["active"] and tbl["phase"] not in ("idle", "lobby"):
        await _w(bot, user.id, "Game in progress. Join next round!")
        return

    if len(args) < 3 or not args[2].isdigit():
        await _w(bot, user.id, "Usage: /poker join <buyin>")
        return

    buyin   = int(args[2])
    min_b   = _s("min_buyin", 100)
    max_b   = _s("max_buyin", 5000)
    max_pl  = _s("max_players", 6)
    lob_sec = _s("lobby_countdown", 15)

    if buyin < min_b:
        await _w(bot, user.id, f"Min buy-in is {min_b}c.")
        return
    if buyin > max_b:
        await _w(bot, user.id, f"Max buy-in is {max_b:,}c.")
        return

    err = _check_daily_limits(user.username, buyin)
    if err:
        await _w(bot, user.id, err)
        return

    # Check if table is active/lobby
    is_new_lobby = not tbl or not tbl["active"]

    if is_new_lobby:
        round_id = _new_round_id()
        lobby_ends = _now()
        conn = db.get_connection()
        conn.execute(
            "UPDATE poker_active_table SET active=1, phase='lobby', "
            "round_id=?, created_at=?, updated_at=?, lobby_started_at=?, "
            "lobby_ends_at=?, pot=0, current_bet=0, deck_json='[]', "
            "community_cards_json='[]' WHERE id=1",
            (round_id, _now(), _now(), _now(), lobby_ends),
        )
        conn.commit()
        conn.close()
    else:
        round_id = tbl["round_id"]
        # Check already joined
        ex = _get_player(round_id, user.id)
        if ex:
            await _w(bot, user.id, "You're already at the table.")
            return
        # Check table full
        cur_count = len(_get_players(round_id))
        if cur_count >= max_pl:
            await _w(bot, user.id, f"Table full ({max_pl} max).")
            return

    db.ensure_user(user.id, user.username)
    bal = db.get_balance(user.id)
    if bal < buyin:
        await _w(bot, user.id, f"Not enough coins. Balance: {bal}c.")
        return

    db.adjust_balance(user.id, -buyin)
    db.ensure_poker_stats(user.id, user.username)
    _insert_player(round_id, user.username, user.id, buyin)

    count = len(_get_players(round_id))

    if is_new_lobby:
        await _chat(bot,
            f"♠️ Poker lobby open! /p <buyin>. Starts in {lob_sec}s.")
        await _chat(bot,
            f"✅ @{user.username} joined Poker with {buyin}c. Players:{count}/{max_pl}")
        _lobby_task = asyncio.create_task(
            _lobby_countdown(bot, round_id, lob_sec)
        )
    else:
        await _chat(bot,
            f"✅ @{user.username} joined Poker with {buyin}c. Players:{count}/{max_pl}")


# ── Command: /poker leave ───────────────────────────────────────────────────────

async def _handle_leave(bot: BaseBot, user: User) -> None:
    global _lobby_task
    tbl = _get_table()
    if not tbl or not tbl["active"]:
        await _w(bot, user.id, "No active poker game.")
        return
    round_id = tbl["round_id"]
    p = _get_player(round_id, user.id)
    if p is None:
        await _w(bot, user.id, "You're not at the table.")
        return
    if tbl["phase"] != "lobby":
        await _w(bot, user.id, "Game in progress — use /poker fold to exit.")
        return
    # Refund and remove
    if not _is_paid(round_id, p["username"]):
        db.adjust_balance(user.id, p["buyin"])
        _mark_paid(round_id, p["username"])
    _clear_players.__doc__  # keep reference
    conn = db.get_connection()
    conn.execute(
        "DELETE FROM poker_active_players WHERE round_id=? AND user_id=?",
        (round_id, user.id),
    )
    conn.commit()
    conn.close()
    remaining = len(_get_players(round_id))
    max_pl = _s("max_players", 6)
    await _chat(bot,
        f"@{user.username} left lobby. {p['buyin']}c refunded. "
        f"Players:{remaining}/{max_pl}")
    if remaining == 0:
        _cancel_task(_lobby_task); _lobby_task = None
        _clear_table()


# ── Startup recovery ───────────────────────────────────────────────────────────

async def startup_poker_recovery(bot: BaseBot) -> None:
    """Called from on_start. Recovers any active poker table from DB."""
    global _lobby_task, _turn_task

    tbl = _get_table()
    if not tbl or not tbl["active"]:
        return

    round_id = tbl["round_id"]
    phase    = tbl["phase"]
    players  = _get_players(round_id)

    print(f"[POKER] Recovery: phase={phase} round={round_id} players={len(players)}")

    if phase == "lobby":
        if not players:
            _clear_table()
            return
        min_pl  = _s("min_players", 2)
        lob_sec = _s("lobby_countdown", 15)
        if len(players) < min_pl:
            for p in players:
                if not _is_paid(round_id, p["username"]):
                    db.adjust_balance(p["user_id"], p["buyin"])
                    _mark_paid(round_id, p["username"])
            _clear_table()
            _clear_players(round_id)
            await _chat(bot,
                "Poker cancelled after restart. Not enough players. Refunded.")
            return
        # Enough players — start the hand now
        await _chat(bot, "♻️ Poker lobby restored. Starting hand now...")
        await _start_hand(bot, tbl, players)
        _log_recovery("recovered_lobby", round_id, phase, f"{len(players)} players")
        return

    if phase in ("preflop", "flop", "turn", "river"):
        active = _active_players(players)
        if len(active) <= 1:
            await finish_poker_hand(bot, "everyone_folded")
            return

        _save_table(restored_after_restart=1)
        await _chat(bot,
            "♻️ Poker restored. Cards, pot, and bets loaded.")

        # Whisper each active player their cards
        for p in active:
            cards = json.loads(p["hole_cards_json"] or "[]")
            if cards:
                try:
                    await bot.highrise.send_whisper(
                        p["user_id"],
                        f"♻️ Poker restored. Use /ph."
                    )
                except Exception:
                    pass

        _log_recovery("recovered", round_id, phase,
                      f"active={len(active)} pot={tbl['pot']}")

        # Resume from current player
        idx = tbl["current_player_index"]
        if 0 <= idx < len(players):
            cur = players[idx]
            if cur["status"] == "active" and _needs_to_act(cur, tbl["current_bet"]):
                tbl_fresh = _get_table()
                if tbl_fresh:
                    await _prompt_player(bot, tbl_fresh, cur)
                return
        # Otherwise advance
        await advance_turn_or_round(bot)
        return

    if phase == "finished":
        _clear_table()
        return

    # Corrupted state
    _save_table(phase="recovery_required")
    await _chat(bot,
        "⚠️ Poker recovery needed. Use /poker recover or /poker refund.")
    _log_recovery("recovery_required", round_id, phase, "corrupted state")


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
    if len(args) < 2:
        await _w(bot, user.id,
                 "♠️ /poker join <buyin> | hand | table | players | rules | stats")
        return

    sub = args[1].lower()

    # ── Public info ──────────────────────────────────────────────────────────
    if sub == "rules":
        min_b = _s("min_buyin", 100); max_b = _s("max_buyin", 5000)
        ai_on = "ON" if _s("allin_enabled", 1) else "OFF"
        await _w(bot, user.id,
                 f"♠️ Hold'em. Buy-in {min_b}-{max_b}c. "
                 f"2 hole cards + 5 board. Best 5-card hand wins. "
                 f"/check /call /raise /fold /allin. All-in {ai_on}.")
        return

    if sub == "stats":
        # /poker stats [username]
        target_name = args[2] if len(args) >= 3 else None
        if target_name:
            s = db.get_poker_stats_by_username(target_name)
            if not s:
                await _w(bot, user.id, f"No poker stats found for @{target_name}.")
                return
            net = s.get("net_profit", 0)
            net_s = f"+{net}c" if net >= 0 else f"{net}c"
            await _w(bot, user.id,
                     f"♠️ @{target_name}: {s['wins']}W/{s['losses']}L | "
                     f"Net {net_s} | All-ins {s.get('allins', 0)}")
        else:
            db.ensure_poker_stats(user.id, user.username)
            s = db.get_poker_stats(user.id)
            net = s.get("net_profit", 0)
            net_s = f"+{net}c" if net >= 0 else f"{net}c"
            await _w(bot, user.id,
                     f"♠️ {user.username}: {s['wins']}W/{s['losses']}L | "
                     f"Net {net_s} | All-ins {s.get('allins', 0)}")
        return

    if sub == "limits":
        min_b  = _s("min_buyin", 100);  max_b = _s("max_buyin", 5000)
        min_r  = _s("min_raise", 50);   max_r = _s("max_raise", 1000)
        rl_on  = _s("raise_limit_enabled", 1)
        wl     = _s("table_daily_win_limit", 10000)
        ll     = _s("table_daily_loss_limit", 5000)
        we     = "ON" if _s("win_limit_enabled", 1) else "OFF"
        le     = "ON" if _s("loss_limit_enabled", 1) else "OFF"
        if rl_on:
            raise_str = f"Raise {min_r}-{max_r}c ON"
        else:
            raise_str = "Raise limit OFF"
        await _w(bot, user.id,
                 f"♠️ Buy {min_b}-{max_b}c | {raise_str} | W/L {wl}/{ll} {we}/{le}")
        return

    if sub == "players":
        tbl = _get_table()
        if not tbl or not tbl["active"]:
            await _w(bot, user.id, "No poker game running.")
            return
        players = _get_players(tbl["round_id"])
        parts = [f"@{p['username']}({p['status']})" for p in players]
        await _w(bot, user.id, ("♠️ Table: " + "  ".join(parts))[:249])
        return

    if sub == "table":
        tbl = _get_table()
        if not tbl or not tbl["active"] or tbl["phase"] in ("idle", "lobby"):
            await _w(bot, user.id, "No active game yet.")
            return
        community = json.loads(tbl["community_cards_json"] or "[]")
        board     = _fcs(community) or "—"
        players   = _get_players(tbl["round_id"])
        idx       = tbl["current_player_index"]
        turn_name = players[idx]["username"] if 0 <= idx < len(players) else "?"
        owe       = tbl["current_bet"]
        await _w(bot, user.id,
                 f"♠️ {tbl['phase'].title()} | Board:{board} | Pot:{tbl['pot']}c | "
                 f"To call:{owe}c | Turn:@{turn_name}")
        return

    if sub in ("hand", "cards"):
        tbl = _get_table()
        if not tbl or not tbl["active"] or tbl["phase"] in ("idle", "lobby"):
            await _w(bot, user.id, "No active hand. Join with /p <buyin>.")
            return
        p = _get_player(tbl["round_id"], user.id)
        if p is None:
            await _w(bot, user.id, "You are not in this hand.")
            return
        cards     = json.loads(p["hole_cards_json"] or "[]")
        community = json.loads(tbl["community_cards_json"] or "[]")
        board_str = _fcs(community) if community else "—"
        if not cards:
            await _w(bot, user.id, "Cards not dealt yet.")
            return
        strength = _hand_strength_label(cards, community)
        draws    = _detect_draws(cards, community)
        label    = strength + (" + " + draws if draws else "")
        await _w(bot, user.id,
                 f"🂡 {_fcs(cards)} | Board:{board_str} | {label} | Stack:{p['stack']}c")
        return

    if sub in ("odds", "chance"):
        tbl = _get_table()
        if not tbl or not tbl["active"] or tbl["phase"] in ("idle", "lobby"):
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

    # ── Join / Leave ─────────────────────────────────────────────────────────
    if sub == "join":
        await _handle_join(bot, user, args)
        return

    if sub == "leave":
        await _handle_leave(bot, user)
        return

    # ── /poker allin on|off (settings toggle — checked BEFORE betting block) ─
    if sub == "allin" and len(args) >= 3 and args[2].lower() in ("on", "off"):
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        val = args[2].lower()
        if val == "on":
            _set("allin_enabled", 1)
            await _w(bot, user.id, "✅ Poker all-in ON.")
        else:
            _set("allin_enabled", 0)
            await _w(bot, user.id, "⛔ Poker all-in OFF.")
        return

    # ── /poker leaderboard [mode] ─────────────────────────────────────────────
    if sub == "leaderboard":
        mode_args = args[2:] if len(args) > 2 else []
        await handle_pokerlb(bot, user, mode_args)
        return

    # ── Betting actions (check/call/raise/fold/allin) ────────────────────────
    if sub in ("check", "call", "raise", "fold", "allin", "all-in", "shove"):
        tbl = _get_table()
        if not tbl or not tbl["active"]:
            await _w(bot, user.id, "No poker hand active. Use /p <buyin>.")
            return
        if tbl["phase"] not in ("preflop", "flop", "turn", "river"):
            await _w(bot, user.id, "No active betting round.")
            return
        round_id = tbl["round_id"]
        p = _get_player(round_id, user.id)
        if p is None:
            await _w(bot, user.id, "You're not at the table.")
            return
        if p["status"] == "allin":
            await _w(bot, user.id, "You are already all-in.")
            return
        if p["status"] != "active":
            await _w(bot, user.id, "You've already folded or left.")
            return
        # Enforce turn order
        players = _get_players(round_id)
        idx     = tbl["current_player_index"]
        if 0 <= idx < len(players):
            cur = players[idx]
            if cur["user_id"] != user.id:
                await _w(bot, user.id, "Not your turn.")
                return
        if sub == "check":
            if tbl["current_bet"] > p["current_bet"]:
                owe = tbl["current_bet"] - p["current_bet"]
                await _w(bot, user.id,
                         f"Can't check — {owe}c to call. /call or /fold.")
                return
            await _do_check(bot, round_id, user.id, user.username)
        elif sub == "call":
            await _do_call(bot, round_id, p, tbl)
        elif sub == "raise":
            if len(args) < 3 or not args[2].isdigit():
                min_r = _s("min_raise", 50)
                await _w(bot, user.id, f"Use /raise <amount>. Min {min_r}c.")
                return
            await _do_raise(bot, round_id, p, tbl, int(args[2]))
        elif sub == "fold":
            await _do_fold(bot, round_id, p)
        elif sub in ("allin", "all-in", "shove"):
            await _do_allin(bot, round_id, p, tbl)
        return

    # ── Staff: on/off ─────────────────────────────────────────────────────────
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
            min_p = _s("min_players", 2);  max_p = _s("max_players", 6)
            lc    = _s("lobby_countdown", 15)
            min_b = _s("min_buyin", 100);  max_b = _s("max_buyin", 5000)
            await _w(bot, user.id,
                     f"♠️ Poker 2 | Lobby {lc}s | Players {min_p}-{max_p} | "
                     f"Buy {min_b}-{max_b}c")
        else:
            en    = "ON" if _s("poker_enabled", 1) else "OFF"
            ai_on = "ON" if _s("allin_enabled", 1) else "OFF"
            rl_on = "ON" if _s("raise_limit_enabled", 1) else "OFF"
            tt    = _s("turn_timer", 20)
            await _w(bot, user.id,
                     f"♠️ Poker {en} | All-in {ai_on} | RaiseLimit {rl_on} | "
                     f"Turn {tt}s | /poker settings 2")
        return

    # ── Staff: cancel ──────────────────────────────────────────────────────────
    if sub == "cancel":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        tbl = _get_table()
        if not tbl or not tbl["active"]:
            await _w(bot, user.id, "No active poker game.")
            return
        await finish_poker_hand(bot, "cancelled")
        return

    # ── Staff: reset ───────────────────────────────────────────────────────────
    if sub == "reset":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        tbl = _get_table()
        if tbl and tbl["active"]:
            await finish_poker_hand(bot, "cancelled")
        else:
            _clear_table()
        await _w(bot, user.id, "♠️ Poker table reset.")
        return

    # ── Staff: state ───────────────────────────────────────────────────────────
    if sub == "state":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        tbl = _get_table()
        if not tbl or not tbl["active"]:
            await _w(bot, user.id, "Poker idle.")
            return
        round_id = tbl["round_id"]
        players  = _get_players(round_id)
        active   = _active_players(players)
        idx      = tbl["current_player_index"]
        cur_name = players[idx]["username"] if 0 <= idx < len(players) else "?"
        await _w(bot, user.id,
                 f"Poker active | {tbl['phase'].title()} | "
                 f"Pot {tbl['pot']}c | Turn @{cur_name} | "
                 f"Active:{len(active)}/{len(players)}")
        return

    # ── Staff: recover ─────────────────────────────────────────────────────────
    if sub == "recover":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        await startup_poker_recovery(bot)
        await _w(bot, user.id, "♻️ Poker recovered. Previous cards loaded.")
        return

    # ── Staff: refund ──────────────────────────────────────────────────────────
    if sub == "refund":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        tbl = _get_table()
        if not tbl or not tbl["active"]:
            await _w(bot, user.id, "No active poker game.")
            return
        await finish_poker_hand(bot, "recovery_refund")
        _log_recovery("refund", tbl["round_id"], tbl["phase"],
                      f"manual by @{user.username}")
        await _w(bot, user.id, "♠️ Poker refunded. Table cleared.")
        return

    # ── Staff: forcefinish ─────────────────────────────────────────────────────
    if sub == "forcefinish":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        tbl = _get_table()
        if not tbl or not tbl["active"]:
            await _w(bot, user.id, "No active poker game.")
            return
        ph = tbl["phase"]
        if ph == "lobby":
            await finish_poker_hand(bot, "not_enough_players")
        elif ph in ("preflop", "flop", "turn", "river"):
            eligible = _eligible_players(_get_players(tbl["round_id"]))
            if len(eligible) >= 2:
                # Force showdown with current cards
                _save_table(phase="river")
                await finish_poker_hand(bot, "showdown")
            else:
                await finish_poker_hand(bot, "everyone_folded")
        else:
            await _w(bot, user.id, "Cannot forcefinish — use /poker refund.")
        return

    # ── Staff: winlimit / losslimit ────────────────────────────────────────────
    if sub == "winlimit":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Staff only.")
            return
        if len(args) >= 3 and args[2].lower() == "on":
            _set("win_limit_enabled", 1)
            await _w(bot, user.id, "✅ Poker win limit ON.")
        elif len(args) >= 3 and args[2].lower() == "off":
            _set("win_limit_enabled", 0)
            await _w(bot, user.id, "⛔ Poker win limit OFF.")
        else:
            await _w(bot, user.id, "Usage: /poker winlimit on|off")
        return

    if sub == "losslimit":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Staff only.")
            return
        if len(args) >= 3 and args[2].lower() == "on":
            _set("loss_limit_enabled", 1)
            await _w(bot, user.id, "✅ Poker loss limit ON.")
        elif len(args) >= 3 and args[2].lower() == "off":
            _set("loss_limit_enabled", 0)
            await _w(bot, user.id, "⛔ Poker loss limit OFF.")
        else:
            await _w(bot, user.id, "Usage: /poker losslimit on|off")
        return

    # ── Staff: raiselimit on/off ────────────────────────────────────────────────
    if sub == "raiselimit":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        val = args[2].lower() if len(args) >= 3 else ""
        if val == "on":
            _set("raise_limit_enabled", 1)
            max_r = _s("max_raise", 1000)
            await _w(bot, user.id, f"✅ Raise cap ON (max {max_r}c).")
        elif val == "off":
            _set("raise_limit_enabled", 0)
            await _w(bot, user.id, "⛔ Raise cap OFF — unlimited raises allowed.")
        else:
            st = "ON" if _s("raise_limit_enabled", 1) else "OFF"
            await _w(bot, user.id, f"Raise limit is {st}. /poker raiselimit on|off")
        return

    # ── Staff: allin on/off ─────────────────────────────────────────────────────
    if sub in ("allinmode", "allintoggle"):
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        val = args[2].lower() if len(args) >= 3 else ""
        if val == "on":
            _set("allin_enabled", 1)
            await _w(bot, user.id, "✅ All-in is now enabled.")
        elif val == "off":
            _set("allin_enabled", 0)
            await _w(bot, user.id, "⛔ All-in is now disabled.")
        else:
            st = "ON" if _s("allin_enabled", 1) else "OFF"
            await _w(bot, user.id, f"All-in is {st}. /poker allintoggle on|off")
        return

    await _w(bot, user.id, "Unknown poker command. /phelp for help.")


# ── Settings setter commands ────────────────────────────────────────────────────

async def handle_setpokerbuyin(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 3 or not args[1].isdigit() or not args[2].isdigit():
        await _w(bot, user.id, "Usage: /setpokerbuyin <min> <max>")
        return
    mn, mx = int(args[1]), int(args[2])
    if mn < 1:
        await _w(bot, user.id, "Min buy-in must be at least 1."); return
    if mx < mn:
        await _w(bot, user.id, "Max must be >= min."); return
    _set("min_buyin", mn); _set("max_buyin", mx)
    await _w(bot, user.id, f"✅ Poker buy-in set: {mn}-{mx}c")


async def handle_setpokerplayers(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 3 or not args[1].isdigit() or not args[2].isdigit():
        await _w(bot, user.id, "Usage: /setpokerplayers <min> <max>")
        return
    mn, mx = int(args[1]), int(args[2])
    if mn < 2:
        await _w(bot, user.id, "Min players must be 2+."); return
    if mx < 2 or mx > 6:
        await _w(bot, user.id, "Max players must be 2-6."); return
    if mx < mn:
        await _w(bot, user.id, "Max must be >= min."); return
    _set("min_players", mn); _set("max_players", mx)
    await _w(bot, user.id, f"✅ Poker players set: {mn}-{mx}")


async def handle_setpokerlobbytimer(bot: BaseBot, user: User, args: list[str]) -> None:
    perm = can_manage_games(user.username)
    print(f"[POKER TIMER] setpokerlobbytimer | user={user.username} "
          f"args={args[1:]} perm={perm}")
    if not perm:
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Use /setpokerlobbytimer 20.")
        return
    secs = int(args[1])
    print(f"[POKER TIMER] setpokerlobbytimer parsed secs={secs}")
    if not (5 <= secs <= 120):
        await _w(bot, user.id, "Lobby timer must be 5-120 seconds.")
        return
    _set("lobby_countdown", secs)
    await _w(bot, user.id, f"✅ Poker lobby timer set to {secs}s.")


async def handle_setpokertimer(bot: BaseBot, user: User, args: list[str]) -> None:
    perm = can_manage_games(user.username)
    print(f"[POKER TIMER] setpokertimer | user={user.username} "
          f"args={args[1:]} perm={perm}")
    if not perm:
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Use /setpokertimer 30.")
        return
    secs = int(args[1])
    print(f"[POKER TIMER] setpokertimer parsed secs={secs}")
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
        await _w(bot, user.id, "Usage: /setpokerraise <min> <max>")
        return
    mn, mx = int(args[1]), int(args[2])
    if mn < 1:
        await _w(bot, user.id, "Min raise must be at least 1."); return
    if mx < mn:
        await _w(bot, user.id, "Max must be >= min."); return
    _set("min_raise", mn); _set("max_raise", mx)
    await _w(bot, user.id, f"✅ Poker raise set: {mn}-{mx}c")


async def handle_setpokerdailywinlimit(bot: BaseBot, user: User,
                                        args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: /setpokerdailywinlimit <amount>")
        return
    amt = int(args[1])
    if amt < 1:
        await _w(bot, user.id, "Must be positive."); return
    _set("table_daily_win_limit", amt)
    await _w(bot, user.id, f"✅ Poker daily win limit: {amt}c")


async def handle_setpokerdailylosslimit(bot: BaseBot, user: User,
                                         args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: /setpokerdailylosslimit <amount>")
        return
    amt = int(args[1])
    if amt < 1:
        await _w(bot, user.id, "Must be positive."); return
    _set("table_daily_loss_limit", amt)
    await _w(bot, user.id, f"✅ Poker daily loss limit: {amt}c")


async def handle_resetpokerlimits(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /resetpokerlimits <username>")
        return
    target = args[1].lstrip("@")
    _reset_daily(target)
    await _w(bot, user.id, f"✅ Poker daily limits reset for @{target}")


async def handle_setpokerturntimer(bot: BaseBot, user: User, args: list[str]) -> None:
    """Alias for /setpokertimer — routed through handle_setpokertimer."""
    await handle_setpokertimer(bot, user, args)


async def handle_setpokerlimits(bot: BaseBot, user: User, args: list[str]) -> None:
    """Bulk setter: /setpokerlimits <minbuyin> <maxbuyin> <minraise> <maxraise> <winlimit> <losslimit>"""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    usage = "Use /setpokerlimits 100 5000 50 1000 10000 5000."
    if len(args) < 7 or not all(a.isdigit() for a in args[1:7]):
        await _w(bot, user.id, usage); return
    min_b, max_b, min_r, max_r, wl, ll = (int(a) for a in args[1:7])
    errors = []
    if min_b < 1:         errors.append("min_buyin≥1")
    if max_b < min_b:     errors.append("max_buyin≥min_buyin")
    if min_r < 1:         errors.append("min_raise≥1")
    if max_r < min_r:     errors.append("max_raise≥min_raise")
    if wl < 1:            errors.append("winlimit≥1")
    if ll < 1:            errors.append("losslimit≥1")
    if errors:
        await _w(bot, user.id, "Invalid: " + ", ".join(errors)); return
    _set("min_buyin",              min_b)
    _set("max_buyin",              max_b)
    _set("min_raise",              min_r)
    _set("max_raise",              max_r)
    _set("table_daily_win_limit",  wl)
    _set("table_daily_loss_limit", ll)
    await _w(bot, user.id, "✅ Poker limits updated.")


# ── Help pages ─────────────────────────────────────────────────────────────────

POKER_HELP_PAGES = [
    (
        "♠️ Poker 1/6 — Join & Info\n"
        "/p <buyin>  or  /poker join <buyin>\n"
        "/pj <buyin>   — quick join\n"
        "/pt  — table info\n"
        "/ph  — your cards + hand strength\n"
        "/po  — win odds estimate"
    ),
    (
        "♠️ Poker 2/6 — Actions\n"
        "/check  or  /ch\n"
        "/call   or  /ca\n"
        "/raise <amt>  or  /r <amt>\n"
        "/fold   or  /f\n"
        "/allin  or  /ai  or  /shove"
    ),
    (
        "♠️ Poker 3/6 — Info & Stats\n"
        "/pp  — players list\n"
        "/pstats  or  /pokerstats — your stats\n"
        "/plb wins|pots|allins|hands|profit\n"
        "/poker settings [2]\n"
        "/poker limits | /poker rules"
    ),
    (
        "♠️ Poker 4/6 — Staff\n"
        "/poker on | off | cancel\n"
        "/poker raiselimit on|off\n"
        "/poker allin on|off\n"
        "/poker winlimit on|off\n"
        "/poker losslimit on|off"
    ),
    (
        "♠️ Poker 5/6 — Settings\n"
        "/setpokerbuyin <min> <max>\n"
        "/setpokerplayers <min> <max>\n"
        "/setpokerraise <min> <max>\n"
        "/setpokerturntimer <sec>\n"
        "/setpokerdailywinlimit <amt>"
    ),
    (
        "♠️ Poker 6/6 — Debug (Mgr+)\n"
        "/pokerdebug [players|table|state]\n"
        "/pokerfix\n"
        "/pokerrefundall\n"
        "/poker recover | refund | forcefinish"
    ),
]

async def handle_pokerhelp(bot: BaseBot, user: User, args: list[str]) -> None:
    page = 0
    if len(args) >= 2 and args[1].isdigit():
        page = int(args[1]) - 1
    page = max(0, min(page, len(POKER_HELP_PAGES) - 1))
    total = len(POKER_HELP_PAGES)
    msg   = POKER_HELP_PAGES[page] + f"\n(Page {page+1}/{total})"
    await _w(bot, user.id, msg[:249])


# ── Debug / fix / refund tools ────────────────────────────────────────────────

async def handle_pokerdebug(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Staff only.")
        return

    sub  = args[1].lower() if len(args) >= 2 else ""
    tbl  = _get_table()

    # ── /pokerdebug players [page] ────────────────────────────────────────────
    if sub == "players":
        if not tbl or not tbl["active"]:
            await _w(bot, user.id, "No active poker table.")
            return
        players = _get_players(tbl["round_id"])
        if not players:
            await _w(bot, user.id, "No players in round.")
            return

        page_size = 3
        page_arg  = args[2] if len(args) >= 3 and args[2].isdigit() else "1"
        page      = max(1, int(page_arg))
        total_pages = max(1, (len(players) + page_size - 1) // page_size)
        page      = min(page, total_pages)
        start     = (page - 1) * page_size
        chunk     = players[start:start + page_size]

        parts = []
        for p in chunk:
            a = "Y" if p["acted"] else "N"
            parts.append(
                f"@{p['username']} s{p['stack']} b{p['current_bet']} "
                f"{p['status']} a{a}"
            )
        body = " | ".join(parts)
        hdr  = f"Players {page}/{total_pages}: "
        msg  = (hdr + body)[:249]
        await _w(bot, user.id, msg)
        if page < total_pages:
            await _w(bot, user.id,
                     f"More: /pokerdebug players {page + 1}")
        return

    # ── /pokerdebug table ─────────────────────────────────────────────────────
    if sub == "table":
        if not tbl or not tbl["active"]:
            await _w(bot, user.id, "No active poker table.")
            return
        community = json.loads(tbl["community_cards_json"] or "[]")
        board     = _fcs(community) if community else "—"
        te        = (tbl["turn_ends_at"] or "—")[:16]
        msg = (f"Table: {tbl['phase'].title()} {board} | "
               f"Pot {tbl['pot']}c | Bet {tbl['current_bet']} | "
               f"Btn {tbl['dealer_button_index']} | "
               f"TurnIdx {tbl['current_player_index']} | Ends {te}")
        await _w(bot, user.id, msg[:249])
        return

    # ── /pokerdebug state ─────────────────────────────────────────────────────
    if sub == "state":
        db_exists   = tbl is not None
        db_active   = bool(tbl and tbl["active"])
        phase       = tbl["phase"] if tbl else "—"
        lobby_alive = _lobby_task is not None and not _lobby_task.done()
        turn_alive  = _turn_task  is not None and not _turn_task.done()
        mem_alive   = lobby_alive or turn_alive
        recovery    = phase == "recovery_required"
        match       = db_exists and (db_active == mem_alive or phase == "lobby")
        msg = (f"State: DB {'yes' if db_exists else 'no'} | "
               f"Memory {'yes' if mem_alive else 'no'} | "
               f"Match {'yes' if match else 'no'} | "
               f"Recovery {'yes' if recovery else 'no'} | "
               f"Active {'yes' if db_active else 'no'}")
        await _w(bot, user.id, msg[:249])
        return

    # ── /pokerdebug (overview) ────────────────────────────────────────────────
    enabled  = "ON" if _s("poker_enabled", 1) else "OFF"
    phase    = tbl["phase"].title() if tbl else "Idle"
    pot      = tbl["pot"] if tbl else 0
    cbet     = tbl["current_bet"] if tbl else 0
    players  = (_get_players(tbl["round_id"])
                if tbl and tbl["active"] and tbl["round_id"] else [])
    deck_ct  = len(json.loads(tbl["deck_json"] or "[]")) if tbl else 0
    idx      = tbl["current_player_index"] if tbl else 0
    cur_name = (players[idx]["username"]
                if players and 0 <= idx < len(players) else "—")
    timers   = []
    if _lobby_task and not _lobby_task.done(): timers.append("Lobby")
    if _turn_task  and not _turn_task.done():  timers.append("Turn")
    t_str    = "+".join(timers) if timers else "none"
    msg = (f"♠️ Debug: {enabled} | {phase} | Pot {pot}c | Bet {cbet}c | "
           f"Turn @{cur_name} | P {len(players)} | Deck {deck_ct} | T:{t_str}")
    await _w(bot, user.id, msg[:249])


async def handle_pokerfix(bot: BaseBot, user: User, args: list[str]) -> None:
    """Attempt to unstick a stuck poker table."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return

    tbl = _get_table()
    if not tbl or not tbl["active"]:
        await _w(bot, user.id, "No active poker table to fix.")
        return

    phase    = tbl["phase"]
    round_id = tbl["round_id"]
    players  = _get_players(round_id)
    active   = _active_players(players)

    print(f"[POKER] /pokerfix by @{user.username} | phase={phase} "
          f"active={len(active)} total={len(players)}")

    if phase == "lobby":
        min_pl = _s("min_players", 2)
        if len(players) >= min_pl:
            await _w(bot, user.id, "Starting hand from lobby state...")
            await _start_hand(bot, tbl, players)
        else:
            await _w(bot, user.id, "Not enough players — refunding lobby...")
            await finish_poker_hand(bot, "not_enough_players")
        return

    if phase in ("preflop", "flop", "turn", "river"):
        if len(active) <= 1:
            await _w(bot, user.id, "One player left — finishing hand...")
            await finish_poker_hand(bot, "everyone_folded")
            return

        # Check if all active players have acted and matched bet
        table_bet    = tbl["current_bet"]
        all_done     = all(
            p["acted"] and p["current_bet"] >= table_bet
            for p in active
        )
        if all_done:
            await _w(bot, user.id, "All acted — advancing street...")
            await _advance_street(bot, tbl, players)
        else:
            # Find who still needs to act
            pending = [p for p in active if _needs_to_act(p, table_bet)]
            if not pending:
                await _w(bot, user.id, "All done — advancing street...")
                await _advance_street(bot, tbl, players)
            else:
                p = pending[0]
                # Find their index
                for i, pl in enumerate(players):
                    if pl["user_id"] == p["user_id"]:
                        _save_table(current_player_index=i)
                        break
                tbl_fresh = _get_table()
                if tbl_fresh:
                    await _prompt_player(bot, tbl_fresh, p)
                await _w(bot, user.id,
                         f"Prompted @{p['username']} to act.")
        return

    if phase == "recovery_required":
        await _w(bot, user.id, "Table in recovery_required — use /poker refund.")
        return

    if phase in ("showdown", "finished", "idle"):
        await _w(bot, user.id, f"Phase is '{phase}' — clearing table.")
        _clear_table()
        return

    await _w(bot, user.id, f"Unknown phase '{phase}' — use /poker refund.")


async def handle_pokerrefundall(bot: BaseBot, user: User, args: list[str]) -> None:
    """Safely refund all unresolved poker chips and clear the table."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers+ only.")
        return

    global _lobby_task, _turn_task
    _cancel_task(_lobby_task); _lobby_task = None
    _cancel_task(_turn_task);  _turn_task  = None

    tbl = _get_table()
    if not tbl or not tbl["active"]:
        await _w(bot, user.id, "No active poker table.")
        return

    round_id = tbl["round_id"]
    players  = _get_players(round_id)
    refunded = 0
    count    = 0

    for p in players:
        if _is_paid(round_id, p["username"]):
            continue
        # Return remaining stack (uncommitted chips) + outstanding contributions
        # We pay back the original buy-in minus what's already in the pot
        # Safe: return stack (what they have left) + 0 pot share
        total = p["stack"]
        # Also return total_contributed from pot for safety on forced refund
        # Actually: we return stack. The pot is zeroed out.
        # But to be fully safe, refund buy-in if stack==0 and nothing was bet yet
        if total == 0 and p["total_contributed"] == 0:
            total = p["buyin"]
        net = total - p["buyin"]
        _upsert_result(round_id, p["username"], p["buyin"], "pokerrefundall", total, net)
        if total > 0:
            db.adjust_balance(p["user_id"], total)
            db.add_ledger_entry(
                p["user_id"], p["username"],
                total, f"Poker refundall by @{user.username} rid={round_id}"
            )
            refunded += total
            count    += 1
            print(f"[POKER] refundall: @{p['username']} +{total}c")
        _mark_paid(round_id, p["username"])

    _log_recovery("pokerrefundall", round_id, tbl["phase"],
                  f"by @{user.username} | {count} players | {refunded}c total")
    _clear_table()
    _clear_players(round_id)

    await _chat(bot,
        f"♠️ Poker refunded. {count} player(s) | {refunded}c returned.")
    await _w(bot, user.id,
             f"Done. Refunded {refunded}c to {count} player(s). Table cleared.")


async def handle_pokerstats(bot: BaseBot, user: User, args: list[str]) -> None:
    """Public /pokerstats [username] — personal or named player's poker stats."""
    target_name = args[1] if len(args) >= 2 else None
    if target_name:
        s = db.get_poker_stats_by_username(target_name)
        if not s:
            await _w(bot, user.id, f"No poker stats found for @{target_name}.")
            return
        name = target_name
    else:
        db.ensure_poker_stats(user.id, user.username)
        s = db.get_poker_stats(user.id)
        name = user.username
    net    = s.get("net_profit", 0)
    net_s  = f"+{net}c" if net >= 0 else f"{net}c"
    streak = s.get("current_win_streak", 0)
    best   = s.get("best_win_streak", 0)
    await _w(bot, user.id,
             f"♠️ @{name}: {s['hands_played']} hands | "
             f"{s['wins']}W {s['losses']}L {s.get('folds', 0)}F | "
             f"Net {net_s} | All-ins {s.get('allins', 0)} | "
             f"Streak {streak} (best {best})")


async def handle_pokerlb(bot: BaseBot, user: User, args: list[str]) -> None:
    """Public /pokerlb [wins|pots|allins|hands|profit|daily|weekly|streak]."""
    mode = args[0].lower() if args else "profit"
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
        val = r[1]
        if isinstance(val, int):
            suffix = "c" if mode in ("profit", "pots", "daily") else ""
            sign   = "+" if mode in ("profit", "daily") and val > 0 else ""
            parts.append(f"#{i+1} @{r[0]} {sign}{val}{suffix}")
        else:
            parts.append(f"#{i+1} @{r[0]} {val}")
    await _w(bot, user.id, (header + "\n" + "\n".join(parts))[:249])


# ── Public API for main.py / maintenance.py ────────────────────────────────────

def get_poker_state_str() -> str:
    """One-line state summary for healthcheck/restartstatus."""
    tbl = _get_table()
    if not tbl or not tbl["active"]:
        return "idle"
    phase = tbl["phase"]
    if phase == "recovery_required":
        return "recovery_required"
    round_id = tbl["round_id"]
    players  = _get_players(round_id)
    active   = _active_players(players)
    return f"{phase}({len(active)}p,{tbl['pot']}c)"


def soft_reset_table() -> None:
    """Cancel in-memory timers. Keep DB state intact for recovery."""
    global _lobby_task, _turn_task
    _cancel_task(_lobby_task); _lobby_task = None
    _cancel_task(_turn_task);  _turn_task  = None


def reset_table() -> None:
    """Hard reset: cancel timers + refund all buy-ins + clear table."""
    global _lobby_task, _turn_task
    _cancel_task(_lobby_task); _lobby_task = None
    _cancel_task(_turn_task);  _turn_task  = None
    tbl = _get_table()
    if not tbl or not tbl["active"]:
        return
    round_id = tbl["round_id"]
    if round_id:
        players = _get_players(round_id)
        for p in players:
            if not _is_paid(round_id, p["username"]):
                db.adjust_balance(p["user_id"], p["buyin"])
                _mark_paid(round_id, p["username"])
        _clear_players(round_id)
    _clear_table()
