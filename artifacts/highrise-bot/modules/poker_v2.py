"""
modules/poker_v2.py — Poker V2

Clean in-memory Texas Hold'em. ChipSoprano / poker bot mode only.
No color markup. Plain-text messages throughout. In-memory source of truth.
Coins deducted at !join, returned at !leave or removal only.
"""
from __future__ import annotations

import asyncio
import random
import time
import uuid
from itertools import combinations
from collections import Counter
from typing import Optional

from highrise import BaseBot, User
import database as db
from modules.permissions import is_admin, is_owner, can_manage_games
from config import BOT_MODE

# ── Bot mode guard ─────────────────────────────────────────────────────────────
_POKER_MODES = {"poker", "all"}

def _is_poker_bot() -> bool:
    return BOT_MODE in _POKER_MODES

# ── Card utilities ─────────────────────────────────────────────────────────────
_RANKS    = "23456789TJQKA"
_SUITS    = "cdhs"
_SUIT_SYM = {"c": "♣", "d": "♦", "h": "♥", "s": "♠"}
_HAND_NAMES = {
    9: "Royal Flush", 8: "Straight Flush", 7: "Four of a Kind",
    6: "Full House",  5: "Flush",          4: "Straight",
    3: "Three of a Kind", 2: "Two Pair",   1: "Pair", 0: "High Card",
}

def _fc(card: str) -> str:
    return card[0].upper() + _SUIT_SYM[card[1]]

def _fcs(cards: list) -> str:
    return " ".join(_fc(c) for c in cards)

def _make_deck() -> list:
    return [r + s for r in _RANKS for s in _SUITS]

# ── Hand evaluator ─────────────────────────────────────────────────────────────
def _score5(cards: list) -> tuple:
    ranks    = sorted([_RANKS.index(c[0]) for c in cards], reverse=True)
    suits    = [c[1] for c in cards]
    cnt      = Counter(ranks)
    freq     = sorted(cnt.values(), reverse=True)
    kr       = sorted(cnt.keys(), key=lambda r: (cnt[r], r), reverse=True)
    flush    = len(set(suits)) == 1
    straight = (ranks == list(range(ranks[0], ranks[0] - 5, -1)))
    if sorted(ranks) == [0, 1, 2, 3, 12]:
        straight = True
        ranks = kr = [3, 2, 1, 0, -1]
    if flush and straight:
        return (9 if ranks[0] == 12 else 8,) + tuple(ranks)
    if freq[0] == 4:        return (7,) + tuple(kr)
    if freq[:2] == [3, 2]:  return (6,) + tuple(kr)
    if flush:               return (5,) + tuple(ranks)
    if straight:            return (4,) + tuple(ranks)
    if freq[0] == 3:        return (3,) + tuple(kr)
    if freq[:2] == [2, 2]:  return (2,) + tuple(kr)
    if freq[0] == 2:        return (1,) + tuple(kr)
    return                         (0,) + tuple(ranks)

def _best_hand(all_cards: list) -> tuple:
    best: Optional[tuple] = None
    for combo in combinations(all_cards, 5):
        s = _score5(list(combo))
        if best is None or s > best:
            best = s
    return best  # type: ignore[return-value]

def _hand_name(score: tuple) -> str:
    return _HAND_NAMES.get(score[0], "High Card")

# ── Table state ────────────────────────────────────────────────────────────────
_T: dict = {
    "phase":                "waiting",
    "seats":                [],        # ordered list of lowercase usernames
    "players":              {},        # username → player_dict
    "dealer_index":         -1,        # incremented before each hand
    "small_blind":          50,
    "big_blind":            100,
    "pot":                  0,
    "board":                [],
    "deck":                 [],
    "current_bet":          0,
    "current_turn_username": None,
    "turn_timer_task":      None,
    "countdown_task":       None,
    "hand_id":              None,
    "hand_number":          0,
    "first_turn_ready":     False,
    "turn_seconds":         30,
    "dealing_started_at":   0.0,
    "room_id_cache":        {},   # username_lower → user_id, refreshed each hand
    "card_delivery_log":    {},   # username → {cards, whisper, live_id}
}

def _new_player(user_id: str, username: str, stack: int) -> dict:
    return {
        "user_id":           user_id,
        "username":          username,
        "stack":             stack,
        "cards":             [],
        "current_bet":       0,
        "total_contributed": 0,
        "status":            "waiting",   # waiting|active|folded|allin|left
        "acted":             False,
        "afk_strikes":       0,
        "remove_after_hand": False,
    }

# ── Messenger helpers ──────────────────────────────────────────────────────────
async def _chat(bot: BaseBot, msg: str) -> None:
    try:
        await bot.highrise.chat(msg[:249])
    except Exception as e:
        print(f"[POKER V2] chat_err={str(e)[:80]}")

async def _w(bot: BaseBot, uid: str, msg: str) -> bool:
    """Whisper. Returns True if delivered successfully."""
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
        return True
    except Exception as e:
        print(f"[POKER V2] whisper_err uid={uid} err={str(e)[:80]}")
        return False

# ── Card delivery helpers ───────────────────────────────────────────────────────

def _get_v2_player_cards(username: str) -> list:
    """In-memory cards for player. Source of truth."""
    p = _T["players"].get(username)
    return p["cards"] if p and p.get("cards") else []

async def _deal_cards_one_by_one(bot: BaseBot, players: list, deck: list) -> bool:
    """Deal cards around the table one at a time (0.4s between). Returns False on deal error."""
    print(f"[POKER V2 DEAL] dealing one by one players={len(players)}")
    for p in players:
        p["cards"] = []
    # First card around the table
    for p in players:
        p["cards"].append(deck.pop())
        print(f"[POKER V2 DEAL] first card dealt to @{p['username']}")
        await asyncio.sleep(0.4)
    # Second card around the table
    for p in players:
        p["cards"].append(deck.pop())
        print(f"[POKER V2 DEAL] second card dealt to @{p['username']}")
        await asyncio.sleep(0.4)
    # Validate
    for p in players:
        if len(p["cards"]) != 2:
            return False
    return True

async def _send_cards_one_by_one(bot: BaseBot, players: list) -> tuple:
    """Whisper each player their cards one by one (0.8s between). Returns (success, failed_list)."""
    success: int = 0
    failed:  list = []
    print(f"[POKER V2 CARD SEND] start one_by_one players={len(players)}")
    for p in players:
        cards = p.get("cards", [])
        if len(cards) != 2:
            failed.append((p["username"], "cards_missing"))
            print(f"[POKER V2 CARD SEND] @{p['username']} ok=false reason=cards_missing")
            continue
        cstr = _fcs(cards)
        msg  = f"Your cards: {cstr}"
        ok   = False
        try:
            await bot.highrise.send_whisper(p["user_id"], msg[:249])
            ok = True
        except Exception as e:
            print(f"[POKER V2 CARD SEND] first fail @{p['username']}: {e!r}")
        if not ok:
            await asyncio.sleep(1)
            try:
                await bot.highrise.send_whisper(p["user_id"], msg[:249])
                ok = True
            except Exception as e:
                print(f"[POKER V2 CARD SEND] retry fail @{p['username']}: {e!r}")
        if ok:
            success += 1
            print(f"[POKER V2 CARD SEND] @{p['username']} ok=true")
        else:
            failed.append((p["username"], "whisper_failed"))
            print(f"[POKER V2 CARD SEND] @{p['username']} ok=false")
        await asyncio.sleep(0.8)
    print(f"[POKER V2 CARD SEND] complete success={success} failed={len(failed)}")
    return success, failed

async def _whisper_v2_cards(bot: BaseBot, player: dict, reason: str = "deal") -> bool:
    """Single-player card whisper with one retry. Used by resendcards / force-open paths."""
    username = player["username"]
    cards    = _get_v2_player_cards(username)
    if not cards:
        print(f"[POKER V2 CARD SEND] @{username} ok=false reason=cards_missing")
        return False
    cstr = _fcs(cards)
    msg  = f"Your cards: {cstr}"
    ok   = False
    try:
        await bot.highrise.send_whisper(player["user_id"], msg[:249])
        ok = True
    except Exception:
        pass
    if not ok:
        await asyncio.sleep(1)
        try:
            await bot.highrise.send_whisper(player["user_id"], msg[:249])
            ok = True
        except Exception:
            pass
    print(f"[POKER V2 CARD SEND] @{username} ok={str(ok).lower()} reason={reason}")
    return ok

# ── Task management ────────────────────────────────────────────────────────────
def _cancel_turn_task() -> None:
    t = _T.get("turn_timer_task")
    if t and not t.done():
        t.cancel()
    _T["turn_timer_task"] = None

def _cancel_countdown_task() -> None:
    t = _T.get("countdown_task")
    if t and not t.done():
        t.cancel()
    _T["countdown_task"] = None

def _cancel_all_tasks() -> None:
    _cancel_turn_task()
    _cancel_countdown_task()

# ── Wallet helpers ─────────────────────────────────────────────────────────────
def _get_balance(user_id: str) -> int:
    try:
        return db.get_balance(user_id)
    except Exception:
        return 0

def _deduct(user_id: str, amount: int) -> bool:
    """Deduct coins from wallet. Returns True if successful."""
    try:
        bal = db.get_balance(user_id)
        if bal < amount:
            return False
        db.adjust_balance(user_id, -amount)
        return True
    except Exception:
        return False

def _credit(user_id: str, amount: int) -> None:
    """Return coins to wallet (only at !leave / table removal time)."""
    try:
        db.adjust_balance(user_id, amount)
    except Exception:
        pass

# ── Board helper ───────────────────────────────────────────────────────────────
def _board_str() -> str:
    return _fcs(_T["board"]) if _T["board"] else "—"

# ── Seat / status helpers ──────────────────────────────────────────────────────
def _seated() -> list:
    return [u for u in _T["seats"] if _T["players"][u]["status"] != "left"]

def _active_in_hand() -> list:
    return [u for u in _T["seats"] if _T["players"][u]["status"] == "active"]

def _eligible_in_hand() -> list:
    return [u for u in _T["seats"]
            if _T["players"][u]["status"] in ("active", "allin")]

def _not_folded() -> list:
    return [u for u in _T["seats"]
            if _T["players"][u]["status"] in ("active", "allin")]

def _needs_action() -> list:
    """Active players who have not yet acted or still owe chips."""
    result = []
    for u in _T["seats"]:
        p = _T["players"][u]
        if p["status"] != "active":
            continue
        if not p["acted"] or p["current_bet"] < _T["current_bet"]:
            result.append(u)
    return result

# ── Side-pot builder ───────────────────────────────────────────────────────────
def _build_pots() -> list:
    """
    Build side pots from each player's total_contributed.
    Folded players contribute to pots but cannot win them.
    Returns [{amount, eligible}] from smallest all-in level up.
    """
    contribs: dict[str, int] = {
        u: _T["players"][u]["total_contributed"]
        for u in _T["seats"]
        if _T["players"][u]["status"] in ("active", "allin", "folded")
        and _T["players"][u]["total_contributed"] > 0
    }
    eligible_set = {
        u for u in _T["seats"]
        if _T["players"][u]["status"] in ("active", "allin")
    }

    if not contribs:
        return [{"amount": _T["pot"], "eligible": list(eligible_set)}]

    pots: list[dict] = []
    remaining = dict(contribs)

    while remaining:
        level   = min(remaining.values())
        pot_amt = sum(min(v, level) for v in remaining.values())
        pot_elig = [u for u in remaining if u in eligible_set]
        pots.append({"amount": pot_amt, "eligible": pot_elig})
        remaining = {u: v - level for u, v in remaining.items() if v > level}

    # Reconcile rounding with actual pot total
    diff = _T["pot"] - sum(p["amount"] for p in pots)
    if diff > 0 and pots:
        pots[-1]["amount"] += diff

    return pots

# ── Turn timeout ───────────────────────────────────────────────────────────────
async def _turn_timeout(bot: BaseBot, username: str) -> None:
    await asyncio.sleep(_T.get("turn_seconds", 30))
    if _T["current_turn_username"] != username:
        return
    if _T["phase"] not in ("preflop", "flop", "turn", "river"):
        return
    p = _T["players"].get(username)
    if not p or p["status"] != "active":
        return

    p["afk_strikes"] += 1
    owe = _T["current_bet"] - p["current_bet"]

    if owe == 0:
        p["acted"] = True
        action = "auto-check"
    else:
        p["status"] = "folded"
        p["acted"]  = True
        action = "auto-fold"

    await _chat(bot, f"@{username} timed out. AFK warning {p['afk_strikes']}/3.")
    print(f"[POKER V2] timeout user={username} action={action} strikes={p['afk_strikes']}")

    if p["afk_strikes"] >= 3:
        await _chat(bot, f"@{username} removed from poker for AFK.")
        p["remove_after_hand"] = True
        print(f"[POKER V2] afk_remove user={username}")

    await _resolve(bot)

# ── Betting resolver ───────────────────────────────────────────────────────────
async def _resolve(bot: BaseBot) -> None:
    """Called after every player action. Decides the next state."""

    # One player left → fold win
    nf = _not_folded()
    if len(nf) == 1:
        winner  = nf[0]
        win_amt = _T["pot"]
        _T["players"][winner]["stack"] += win_amt
        await _chat(bot, f"@{winner} wins {win_amt:,} coins. Everyone else folded.")
        print(f"[POKER V2] resolve action=fold_win winner={winner}")
        await _complete_hand(bot, "fold_win")
        return

    # No active players can act → run board to showdown
    if not _active_in_hand():
        print(f"[POKER V2] resolve action=showdown reason=all_allin_or_folded")
        await _run_to_showdown(bot)
        return

    # Betting round complete → advance street
    na = _needs_action()
    if not na:
        print(f"[POKER V2] resolve action=advance_street phase={_T['phase']}")
        await _advance_street(bot)
        return

    # Find next player in seat order after the current actor
    seats = _T["seats"]
    n     = len(seats)
    cur   = _T["current_turn_username"]
    next_u = na[0]  # fallback: first in needs_action list
    if cur and cur in seats:
        cur_idx = seats.index(cur)
        for i in range(1, n + 1):
            u = seats[(cur_idx + i) % n]
            if u in na:
                next_u = u
                break

    _T["current_turn_username"] = next_u
    print(f"[POKER V2] resolve action=next_turn next={next_u} needs_action={len(na)}")
    await _prompt_turn(bot, next_u)

# ── Prompt player ──────────────────────────────────────────────────────────────
async def _prompt_turn(bot: BaseBot, username: str) -> None:
    p    = _T["players"][username]
    uid  = p["user_id"]
    owe  = max(0, _T["current_bet"] - p["current_bet"])
    pot  = _T["pot"]
    csz  = _fcs(p["cards"]) if p["cards"] else "?"
    secs = _T.get("turn_seconds", 30)

    _cancel_turn_task()
    _T["turn_timer_task"] = asyncio.create_task(_turn_timeout(bot, username))

    if owe > 0:
        wh = f"Your cards: {csz}\nPot: {pot:,} coins\nCall: {owe:,} coins"
    else:
        wh = f"Your cards: {csz}\nPot: {pot:,} coins\n!check, !raise, !fold, !allin"

    wh_ok = await _w(bot, uid, wh)
    print(f"[POKER V2] whisper user={username} ok={str(wh_ok).lower()}")
    await _chat(bot, f"@{username}'s turn. {secs}s")

# ── Street advancement ─────────────────────────────────────────────────────────
async def _advance_street(bot: BaseBot) -> None:
    phase = _T["phase"]
    deck  = _T["deck"]

    # Reset per-street bets
    _T["current_bet"] = 0
    for u in _T["seats"]:
        p = _T["players"][u]
        if p["status"] == "active":
            p["current_bet"] = 0
            p["acted"]       = False

    # First to act postflop: first active player left of dealer
    dealer_idx = _T["dealer_index"]
    seats      = _T["seats"]
    n          = len(seats)
    first_u: Optional[str] = None
    for i in range(1, n + 1):
        u = seats[(dealer_idx + i) % n]
        if _T["players"][u]["status"] == "active":
            first_u = u
            break

    if phase == "preflop":
        deck.pop()
        flop = [deck.pop(), deck.pop(), deck.pop()]
        _T["board"].extend(flop)
        _T["phase"] = "flop"
        await _chat(bot, f"Flop: {_fcs(flop)}\nPot: {_T['pot']:,} coins")

    elif phase == "flop":
        deck.pop()
        turn_c = deck.pop()
        _T["board"].append(turn_c)
        _T["phase"] = "turn"
        await _chat(bot, f"Turn: {_fc(turn_c)}\nBoard: {_board_str()}")

    elif phase == "turn":
        deck.pop()
        river_c = deck.pop()
        _T["board"].append(river_c)
        _T["phase"] = "river"
        await _chat(bot, f"River: {_fc(river_c)}\nBoard: {_board_str()}")

    elif phase == "river":
        await _showdown(bot)
        return

    # After dealing the new street, check if anyone needs to act
    if first_u and _needs_action():
        _T["current_turn_username"] = first_u
        await _prompt_turn(bot, first_u)
    else:
        await _run_to_showdown(bot)

# ── All-in run-out ─────────────────────────────────────────────────────────────
async def _run_to_showdown(bot: BaseBot) -> None:
    """Deal remaining board cards then go to showdown."""
    _cancel_turn_task()
    phase = _T["phase"]
    deck  = _T["deck"]

    await _chat(bot, "All-in. Running the board...")
    await asyncio.sleep(1)

    if phase == "preflop":
        deck.pop()
        flop = [deck.pop(), deck.pop(), deck.pop()]
        _T["board"].extend(flop)
        await _chat(bot, f"Flop: {_fcs(flop)}")
        await asyncio.sleep(1)
        deck.pop()
        turn_c = deck.pop()
        _T["board"].append(turn_c)
        await _chat(bot, f"Turn: {_fc(turn_c)}")
        await asyncio.sleep(1)
        deck.pop()
        river_c = deck.pop()
        _T["board"].append(river_c)
        await _chat(bot, f"River: {_fc(river_c)}")
        await asyncio.sleep(1)

    elif phase == "flop":
        deck.pop()
        turn_c = deck.pop()
        _T["board"].append(turn_c)
        await _chat(bot, f"Turn: {_fc(turn_c)}")
        await asyncio.sleep(1)
        deck.pop()
        river_c = deck.pop()
        _T["board"].append(river_c)
        await _chat(bot, f"River: {_fc(river_c)}")
        await asyncio.sleep(1)

    elif phase == "turn":
        deck.pop()
        river_c = deck.pop()
        _T["board"].append(river_c)
        await _chat(bot, f"River: {_fc(river_c)}")
        await asyncio.sleep(1)

    await _showdown(bot)

# ── Showdown ───────────────────────────────────────────────────────────────────
async def _showdown(bot: BaseBot) -> None:
    board    = _T["board"]
    eligible = _eligible_in_hand()

    await _chat(bot, f"Showdown.\nBoard: {_board_str()}")
    await asyncio.sleep(1)

    # Evaluate each eligible player's best hand
    scores: dict[str, tuple] = {}
    lines:  list[str]        = []
    for u in eligible:
        p         = _T["players"][u]
        all_cards = p["cards"] + board
        score     = _best_hand(all_cards) if len(all_cards) >= 5 else _score5(all_cards + p["cards"])
        scores[u] = score
        lines.append(f"@{u}: {_fcs(p['cards'])} — {_hand_name(score)}")

    # Announce in ≤249-char chunks
    chunk = ""
    for line in lines:
        test = (chunk + "\n" + line).strip() if chunk else line
        if len(test) > 249:
            await _chat(bot, chunk)
            chunk = line
        else:
            chunk = test
    if chunk:
        await _chat(bot, chunk)
    await asyncio.sleep(1)

    # Award each pot
    pots = _build_pots()
    for pot_info in pots:
        elig_here = [u for u in pot_info["eligible"] if u in scores]
        if not elig_here:
            continue
        best_score = max(scores[u] for u in elig_here)
        winners    = [u for u in elig_here if scores[u] == best_score]
        share      = pot_info["amount"] // len(winners)
        remainder  = pot_info["amount"] % len(winners)
        for i, w in enumerate(winners):
            _T["players"][w]["stack"] += share + (remainder if i == 0 else 0)
        if len(winners) == 1:
            w = winners[0]
            await _chat(bot,
                f"@{w} wins {pot_info['amount']:,} coins with {_hand_name(scores[w])}.")
        else:
            names = " and ".join(f"@{w}" for w in winners)
            await _chat(bot, f"Split pot. {names} each receive {share:,} coins.")

    print(f"[POKER V2] resolve action=showdown")
    await _complete_hand(bot, "showdown")

# ── Complete hand ──────────────────────────────────────────────────────────────
async def _complete_hand(bot: BaseBot, reason: str) -> None:
    """Post-hand cleanup. Always called after any hand ends."""
    print(f"[POKER V2] complete reason={reason}")
    _cancel_all_tasks()

    # Show current stacks before removal
    seated_now = _seated()
    if seated_now:
        parts = [f"@{u} {_T['players'][u]['stack']:,}" for u in seated_now[:6]]
        await _chat(bot, ("Stacks: " + " | ".join(parts))[:249])

    # Cash out / remove players marked for removal or busted
    to_remove: list = []
    for u in list(_T["seats"]):
        p = _T["players"][u]
        if p.get("remove_after_hand") or p["stack"] <= 0:
            if p["stack"] > 0:
                _credit(p["user_id"], p["stack"])
                await _chat(bot, f"@{u} left table. Cashed out {p['stack']:,} coins.")
            else:
                await _chat(bot, f"@{u} busted out.")
            to_remove.append(u)

    for u in to_remove:
        _T["seats"].remove(u)
        del _T["players"][u]

    # Reset hand state (stacks survive to next hand)
    _T["board"]                  = []
    _T["deck"]                   = []
    _T["current_bet"]            = 0
    _T["current_turn_username"]  = None
    _T["hand_id"]                = None
    _T["first_turn_ready"]       = False
    _T["pot"]                    = 0

    for u in _T["seats"]:
        p = _T["players"][u]
        p["cards"]             = []
        p["current_bet"]       = 0
        p["total_contributed"] = 0
        p["status"]            = "waiting"
        p["acted"]             = False

    # Start next hand or wait
    remaining = _seated()
    if len(remaining) >= 2:
        _T["phase"] = "between_hands"
        await _chat(bot, "Next hand in 10s.")
        _T["countdown_task"] = asyncio.create_task(_start_hand_countdown(bot, 10))
        print(f"[POKER V2] complete next=between_hands")
    else:
        _T["phase"] = "waiting"
        await _chat(bot, "Waiting for one more player. Use !join 5000.")
        print(f"[POKER V2] complete next=waiting")

# ── Hand countdown ─────────────────────────────────────────────────────────────
async def _start_hand_countdown(bot: BaseBot, delay: int = 10) -> None:
    await asyncio.sleep(delay)
    if _T["phase"] in ("countdown", "between_hands"):
        await _start_hand(bot)

# ── Start hand ─────────────────────────────────────────────────────────────────
async def _start_hand(bot: BaseBot) -> None:
    seated = _seated()
    if len(seated) < 2:
        _T["phase"] = "waiting"
        return

    _cancel_all_tasks()
    _T["phase"]              = "dealing"
    _T["first_turn_ready"]   = False
    _T["dealing_started_at"] = time.time()
    _T["hand_number"]       += 1
    _T["hand_id"]          = f"pkv2_{uuid.uuid4().hex[:10]}"

    print(f"[POKER V2 START] phase=dealing players={len(seated)}")

    # Rotate dealer
    n          = len(seated)
    _T["dealer_index"] = (_T["dealer_index"] + 1) % n
    dealer_idx = _T["dealer_index"]

    if n == 2:
        # Heads-up: dealer = SB
        sb_idx = dealer_idx
        bb_idx = (dealer_idx + 1) % n
    else:
        sb_idx = (dealer_idx + 1) % n
        bb_idx = (dealer_idx + 2) % n

    sb_user = seated[sb_idx]
    bb_user = seated[bb_idx]
    sb      = _T["small_blind"]
    bb      = _T["big_blind"]

    # ── Steps 3-7: Reset, shuffle, deal one-by-one, whisper one-by-one ──────
    _T["card_delivery_log"] = {}
    for u in seated:
        p = _T["players"][u]
        p["cards"]             = []
        p["current_bet"]       = 0
        p["total_contributed"] = 0
        p["status"]            = "active"
        p["acted"]             = False

    deck = _make_deck()
    random.shuffle(deck)
    _T["deck"]  = deck
    _T["board"] = []
    _T["pot"]   = 0
    _T["current_bet"] = 0

    await _chat(bot, "Dealer is dealing cards...")

    players_list = [_T["players"][u] for u in seated]
    deal_ok = await _deal_cards_one_by_one(bot, players_list, deck)
    if not deal_ok:
        await _chat(bot, "Poker hand cancelled. Card deal error.")
        _T["phase"] = "waiting"
        return

    n_success, failed_list = await _send_cards_one_by_one(bot, players_list)
    failed_names = {f[0] for f in failed_list}

    for u in seated:
        p = _T["players"][u]
        _T["card_delivery_log"][u] = {
            "cards":   bool(p.get("cards")),
            "whisper": u not in failed_names,
            "live_id": True,
        }
        if u in failed_names:
            try:
                await bot.highrise.send_whisper(
                    p["user_id"], "Could not auto-send your cards. Use !hand.")
            except Exception:
                pass

    if n_success == 0:
        await _chat(bot, "Poker hand cancelled. Could not send cards.")
        _T["phase"]   = "waiting"
        _T["hand_id"] = None
        return

    # ── Steps 8-9: Post blinds and announce ──────────────────────────────────
    def _post_blind(uname: str, amount: int) -> int:
        p      = _T["players"][uname]
        actual = min(amount, p["stack"])
        p["stack"]             -= actual
        p["current_bet"]        = actual
        p["total_contributed"]  = actual
        _T["pot"]              += actual
        if p["stack"] == 0:
            p["status"] = "allin"
        return actual

    actual_sb         = _post_blind(sb_user, sb)
    actual_bb         = _post_blind(bb_user, bb)
    _T["current_bet"] = actual_bb

    blind_msg = (
        f"Cards have been dealt.\n"
        f"SB: @{sb_user} {actual_sb:,} coins\n"
        f"BB: @{bb_user} {actual_bb:,} coins\n"
        f"Pot: {_T['pot']:,} coins"
    )
    await _chat(bot, blind_msg)
    print(f"[POKER V2 BLINDS] posted sb=@{sb_user} bb=@{bb_user} pot={_T['pot']}")

    if n_success < len(seated):
        await _chat(bot, "If you did not receive cards, type !hand.")

    # ── Steps 10-15: Select first actor, open turn ───────────────────────────
    if n == 2:
        first_preflop_idx = sb_idx
    else:
        first_preflop_idx = (bb_idx + 1) % n
    first_actor = seated[first_preflop_idx]
    first_p     = _T["players"][first_actor]

    print(f"[POKER V2 FIRST ACTOR] user=@{first_actor}")
    await _open_first_turn(bot, first_actor, first_p)

# ── Open first turn (never stays in dealing) ───────────────────────────────────
async def _open_first_turn(bot: BaseBot, first_actor: str, first_p: dict) -> None:
    """Whisper first actor turn reminder, then open preflop. Always advances phase."""
    # Ensure room cache is populated (force-open paths may skip hand start)
    if not _T.get("room_id_cache"):
        _T["room_id_cache"] = await _fetch_room_user_ids(bot)

    live_uid = _resolve_live_user_id(first_actor, first_p["user_id"])
    owe  = max(0, _T["current_bet"] - first_p["current_bet"])
    cstr = _fcs(first_p["cards"]) if first_p.get("cards") else "?"
    pot  = _T["pot"]
    if owe > 0:
        gate_msg = (
            f"Your turn.\n"
            f"Your cards: {cstr}\n"
            f"Pot: {pot:,} coins\n"
            f"Call: {owe:,} coins"
        )
    else:
        gate_msg = (
            f"Your turn.\n"
            f"Your cards: {cstr}\n"
            f"Pot: {pot:,} coins\n"
            f"!check, !raise, !fold, !allin"
        )

    gate_ok = await _w(bot, live_uid, gate_msg)
    print(f"[POKER V2 FIRST TURN] user=@{first_actor} whisper={str(gate_ok).lower()}")

    if not gate_ok:
        print("[POKER V2] first card whisper failed, opening turn anyway")
        try:
            await bot.highrise.send_whisper(
                live_uid, "Could not auto-send your cards. Type !hand to view them.")
        except Exception:
            pass

    secs = _T.get("turn_seconds", 30)
    _T["phase"]                 = "preflop"
    _T["current_turn_username"] = first_actor
    _T["first_turn_ready"]      = True
    _cancel_turn_task()
    _T["turn_timer_task"] = asyncio.create_task(_turn_timeout(bot, first_actor))
    await _chat(bot, f"@{first_actor}'s turn. {secs}s")
    print(f"[POKER V2 FIRST TURN] user=@{first_actor} public=true")
    print(f"[POKER V2 OPEN] first_actor=@{first_actor} phase=preflop ready=true")


# ── Action guard ───────────────────────────────────────────────────────────────
async def _action_guard(bot: BaseBot, user: User) -> bool:
    """Returns True if the player may act. Whispers the reason if not."""
    username = user.username.lower()
    phase    = _T["phase"]

    if phase not in ("preflop", "flop", "turn", "river"):
        if phase == "dealing":
            # Safety: if stuck in dealing > 10s, force-open first turn
            elapsed = time.time() - _T.get("dealing_started_at", 0.0)
            if elapsed > 10 and username in _T["players"] and _T["players"][username].get("cards"):
                seated = _seated()
                n = len(seated)
                if n >= 2:
                    dealer_idx = _T["dealer_index"]
                    if n == 2:
                        first_idx = dealer_idx
                    else:
                        bb_idx    = (dealer_idx + 2) % n
                        first_idx = (bb_idx + 1) % n
                    first_actor = seated[first_idx]
                    first_p     = _T["players"][first_actor]
                    print(f"[POKER V2] force_open_first_turn after {elapsed:.1f}s stuck in dealing")
                    await _open_first_turn(bot, first_actor, first_p)
                    return _T["current_turn_username"] == username
            await _w(bot, user.id, "Dealer is preparing the hand. Please wait.")
        else:
            await _w(bot, user.id, "No active poker hand.")
        return False

    if not _T["first_turn_ready"]:
        await _w(bot, user.id, "Dealer is preparing the hand. Please wait.")
        return False

    if username not in _T["players"]:
        await _w(bot, user.id, "You are not at the poker table.")
        return False

    p = _T["players"][username]
    if p["status"] == "folded":
        await _w(bot, user.id, "You have already folded.")
        return False
    if p["status"] == "allin":
        await _w(bot, user.id, "You are all-in.")
        return False
    if p["status"] != "active":
        await _w(bot, user.id, "You are not active in this hand.")
        return False

    if _T["current_turn_username"] != username:
        cur = _T["current_turn_username"] or "?"
        await _w(bot, user.id, f"It is @{cur}'s turn.")
        return False

    return True

# ── !join ──────────────────────────────────────────────────────────────────────
async def _cmd_join(bot: BaseBot, user: User, args: list) -> None:
    username  = user.username.lower()
    min_buyin = 1000

    if username in _T["players"]:
        stack = _T["players"][username]["stack"]
        await _w(bot, user.id,
            f"You are already seated with {stack:,} coins. Use !leave to cash out.")
        return

    if len(_T["seats"]) >= 6:
        await _w(bot, user.id, "Poker table is full. (6/6)")
        return

    # No amount provided → prompt
    if len(args) < 2:
        await _w(bot, user.id, "Use: !join 5000")
        return

    amt_str = args[1]
    try:
        amount = int(str(amt_str).replace(",", ""))
    except (ValueError, TypeError):
        await _w(bot, user.id, "Use: !join 5000")
        return

    if amount < min_buyin:
        await _w(bot, user.id,
            f"Minimum poker buy-in is {min_buyin:,} coins. Use !join {min_buyin:,}.")
        return

    bal = _get_balance(user.id)
    if bal < amount:
        await _w(bot, user.id, f"Not enough coins. Balance: {bal:,} coins.")
        return

    if not _deduct(user.id, amount):
        await _w(bot, user.id, "Could not deduct coins. Please try again.")
        return

    _T["players"][username] = _new_player(user.id, username, amount)
    _T["seats"].append(username)
    cnt = len(_T["seats"])
    await _chat(bot, f"@{user.username} joined poker with {amount:,} coins. Players: {cnt}/6")
    print(f"[POKER V2] join user={user.username} amount={amount}")

    if cnt >= 2 and _T["phase"] == "waiting":
        _T["phase"] = "countdown"
        await _chat(bot, "Starting first hand in 10s.")
        _T["countdown_task"] = asyncio.create_task(_start_hand_countdown(bot, 10))

# ── !leave ─────────────────────────────────────────────────────────────────────
async def _cmd_leave(bot: BaseBot, user: User) -> None:
    username = user.username.lower()
    if username not in _T["players"]:
        await _w(bot, user.id, "You are not at the poker table.")
        return

    p     = _T["players"][username]
    phase = _T["phase"]

    if phase in ("preflop", "flop", "turn", "river"):
        if p["status"] == "allin":
            p["remove_after_hand"] = True
            await _chat(bot, f"@{user.username} is all-in and will leave after showdown.")
        elif p["status"] == "active":
            p["status"]            = "folded"
            p["remove_after_hand"] = True
            await _chat(bot, f"@{user.username} left during the hand and folded.")
            await _resolve(bot)
        else:
            p["remove_after_hand"] = True
            await _w(bot, user.id, "You will be removed after this hand.")
    else:
        stack = p["stack"]
        _T["seats"].remove(username)
        del _T["players"][username]
        if stack > 0:
            _credit(user.id, stack)
            await _chat(bot, f"@{user.username} left table. Cashed out {stack:,} coins.")
        else:
            await _chat(bot, f"@{user.username} left table.")

        if len(_T["seats"]) < 2 and _T["phase"] in ("countdown", "between_hands"):
            _cancel_countdown_task()
            _T["phase"] = "waiting"
            await _chat(bot, "Waiting for one more player. Use !join 5000.")

# ── !check ─────────────────────────────────────────────────────────────────────
async def _cmd_check(bot: BaseBot, user: User) -> None:
    if not await _action_guard(bot, user):
        return
    username = user.username.lower()
    p        = _T["players"][username]
    owe      = _T["current_bet"] - p["current_bet"]
    if owe > 0:
        await _w(bot, user.id, f"Cannot check. Call {owe:,} coins, raise, or fold.")
        return
    p["acted"] = True
    await _chat(bot, f"@{user.username} checks.")
    print(f"[POKER V2] action user={user.username} action=check")
    await _resolve(bot)

# ── !call ──────────────────────────────────────────────────────────────────────
async def _cmd_call(bot: BaseBot, user: User) -> None:
    if not await _action_guard(bot, user):
        return
    username = user.username.lower()
    p        = _T["players"][username]
    owe      = _T["current_bet"] - p["current_bet"]
    if owe <= 0:
        await _w(bot, user.id, "Nothing to call. Use !check.")
        return

    call_amt = min(owe, p["stack"])
    p["stack"]             -= call_amt
    p["current_bet"]       += call_amt
    p["total_contributed"] += call_amt
    _T["pot"]              += call_amt

    if p["stack"] == 0:
        p["status"] = "allin"
        await _chat(bot,
            f"@{user.username} calls {call_amt:,} — all-in! Pot: {_T['pot']:,} coins")
    else:
        p["acted"] = True
        await _chat(bot,
            f"@{user.username} calls {call_amt:,}. Pot: {_T['pot']:,} coins")

    print(f"[POKER V2] action user={user.username} action=call amount={call_amt}")
    await _resolve(bot)

# ── !raise ─────────────────────────────────────────────────────────────────────
async def _cmd_raise(bot: BaseBot, user: User, args: list) -> None:
    if not await _action_guard(bot, user):
        return
    username = user.username.lower()
    p        = _T["players"][username]

    amt_str = args[1] if len(args) > 1 else ""
    try:
        total_raise = int(str(amt_str).replace(",", ""))
    except (ValueError, TypeError):
        await _w(bot, user.id, "Use: !raise <amount>")
        return

    min_raise = _T["current_bet"] + _T["big_blind"]
    if total_raise < min_raise:
        await _w(bot, user.id, f"Minimum raise is {min_raise:,} coins total.")
        return

    need = total_raise - p["current_bet"]
    if need > p["stack"]:
        await _w(bot, user.id,
            f"Not enough chips. Use !allin ({p['stack']:,} coins).")
        return

    p["stack"]             -= need
    p["total_contributed"] += need
    _T["pot"]              += need
    p["current_bet"]        = total_raise
    _T["current_bet"]       = total_raise
    p["acted"]              = True

    # Reset acted for other active players
    for u in _T["seats"]:
        if u != username and _T["players"][u]["status"] == "active":
            _T["players"][u]["acted"] = False

    await _chat(bot,
        f"@{user.username} raises to {total_raise:,}. Pot: {_T['pot']:,} coins")
    print(f"[POKER V2] action user={user.username} action=raise amount={total_raise}")
    await _resolve(bot)

# ── !fold ──────────────────────────────────────────────────────────────────────
async def _cmd_fold(bot: BaseBot, user: User) -> None:
    if not await _action_guard(bot, user):
        return
    username = user.username.lower()
    p        = _T["players"][username]
    p["status"] = "folded"
    p["acted"]  = True
    await _chat(bot, f"@{user.username} folds.")
    print(f"[POKER V2] action user={user.username} action=fold")
    await _resolve(bot)

# ── !allin ─────────────────────────────────────────────────────────────────────
async def _cmd_allin(bot: BaseBot, user: User) -> None:
    if not await _action_guard(bot, user):
        return
    username = user.username.lower()
    p        = _T["players"][username]
    amt      = p["stack"]
    if amt <= 0:
        await _w(bot, user.id, "You have no chips left.")
        return

    new_total               = p["current_bet"] + amt
    p["total_contributed"] += amt
    _T["pot"]              += amt
    p["current_bet"]        = new_total
    p["stack"]              = 0
    p["status"]             = "allin"

    if new_total > _T["current_bet"]:
        _T["current_bet"] = new_total
        for u in _T["seats"]:
            if u != username and _T["players"][u]["status"] == "active":
                _T["players"][u]["acted"] = False

    await _chat(bot,
        f"@{user.username} goes all-in for {amt:,}! Pot: {_T['pot']:,} coins")
    print(f"[POKER V2] action user={user.username} action=allin amount={amt}")
    await _resolve(bot)

# ── !hand ──────────────────────────────────────────────────────────────────────
async def _cmd_hand(bot: BaseBot, user: User) -> None:
    username = user.username.lower()
    # Allow !hand during dealing so players who didn't get auto-whisper can still see cards
    if _T["phase"] not in ("preflop", "flop", "turn", "river", "dealing"):
        await _w(bot, user.id, "No active hand yet.")
        return
    if username not in _T["players"]:
        await _w(bot, user.id, "You are not at the poker table.")
        return
    # Read cards from in-memory player state (source of truth)
    p = _T["players"][username]
    if not p.get("cards"):
        await _w(bot, user.id, "Your cards are not yet dealt. Please wait.")
        return
    cstr = _fcs(p["cards"])
    msg  = f"Your cards: {cstr}"
    board = _T.get("board", [])
    if board:
        msg += f"\nBoard: {_board_str()}"
    else:
        msg += "\nBoard: —"
    msg += f"\nPot: {_T['pot']:,} coins"
    await _w(bot, user.id, msg)

# ── !table ─────────────────────────────────────────────────────────────────────
async def _cmd_table(bot: BaseBot, user: User) -> None:
    phase = _T["phase"]
    cnt   = len(_T["seats"])

    if phase == "waiting":
        if cnt == 1:
            await _w(bot, user.id, "Poker table:\nWaiting for 1 more player.\nUse !join 5000.")
        else:
            await _w(bot, user.id, "Poker table:\nWaiting for players.\nUse !join 5000.")
    elif phase == "countdown":
        await _w(bot, user.id,
            f"Poker table:\nNext hand starting soon.\nPlayers: {cnt}")
    elif phase == "dealing":
        elapsed = time.time() - _T.get("dealing_started_at", 0.0)
        if elapsed > 10:
            # Stuck — force-open first turn now
            seated = _seated()
            n = len(seated)
            if n >= 2:
                dealer_idx = _T["dealer_index"]
                if n == 2:
                    first_idx = dealer_idx
                else:
                    bb_idx    = (dealer_idx + 2) % n
                    first_idx = (bb_idx + 1) % n
                first_actor = seated[first_idx]
                first_p     = _T["players"][first_actor]
                await _w(bot, user.id,
                    f"Poker table:\nOpening first turn now.\nPlayers: {cnt}")
                print(f"[POKER V2] table_cmd force_open after {elapsed:.1f}s")
                await _open_first_turn(bot, first_actor, first_p)
                return
        await _w(bot, user.id,
            f"Poker table:\nDealer is preparing the hand.\nPlayers: {cnt}")
    elif phase in ("preflop", "flop", "turn", "river"):
        turn = _T["current_turn_username"] or "?"
        pot  = _T["pot"]
        await _w(bot, user.id, (
            f"Poker table:\nPhase: {phase.title()}\n"
            f"Pot: {pot:,} coins\nTurn: @{turn}\n"
            f"Board: {_board_str()}\nPlayers: {cnt}"
        )[:249])
    elif phase == "between_hands":
        await _w(bot, user.id,
            f"Poker table:\nNext hand soon.\nPlayers: {cnt}")
    else:
        await _w(bot, user.id, "Poker table:\nWaiting for players.\nUse !join 5000.")

# ── !poker debugcards ──────────────────────────────────────────────────────────
async def _cmd_debugcards(bot: BaseBot, user: User) -> None:
    if not (is_owner(user.username) or is_admin(user.username)):
        await _w(bot, user.id, "Owner only.")
        return
    log  = _T.get("card_delivery_log", {})
    hand = _T["hand_number"]
    if not log:
        await _w(bot, user.id, f"Poker card debug:\nHand: {hand}\nNo delivery log.")
        return
    parts = [f"Poker card debug:\nHand: {hand}"]
    for uname, info in log.items():
        c_ok = "yes"     if info.get("cards")   else "no"
        w_ok = "success" if info.get("whisper")  else "failed"
        l_ok = "live"    if info.get("live_id")  else "stored"
        parts.append(f"@{uname}: cards={c_ok}, whisper={w_ok}, id={l_ok}")
    await _w(bot, user.id, "\n".join(parts)[:249])

# ── !poker resendcards ─────────────────────────────────────────────────────────
async def _cmd_resendcards(bot: BaseBot, user: User) -> None:
    if not (is_owner(user.username) or is_admin(user.username)):
        await _w(bot, user.id, "Owner only.")
        return
    phase = _T["phase"]
    if phase not in ("preflop", "flop", "turn", "river", "dealing"):
        await _w(bot, user.id, "No active hand to resend cards for.")
        return
    _T["room_id_cache"] = await _fetch_room_user_ids(bot)
    count = 0
    for u in _T["seats"]:
        p = _T["players"].get(u)
        if p and p.get("cards") and p.get("status") not in ("folded", "left"):
            ok = await _whisper_v2_cards(bot, p, "resend")
            if ok:
                count += 1
    await _w(bot, user.id, f"Cards resent to {count} player(s).")

# ── Main command dispatch ──────────────────────────────────────────────────────
async def handle_poker_v2(bot: BaseBot, user: User, cmd: str, args: list) -> None:
    """Entry point for all Poker V2 player commands. Called by main.py."""
    if not _is_poker_bot():
        return

    print(f"[POKER V2] command user={user.username} cmd={cmd}")

    if cmd == "poker":
        # If the first arg looks like a number the player tried !poker 5000
        if len(args) > 1 and args[1].replace(",", "").isdigit():
            amt = args[1]
            await _w(bot, user.id, f"Use !join {amt} to join poker.")
        else:
            await _w(bot, user.id, (
                "Poker commands:\n"
                "Join: !join 5000\n"
                "Play: !check, !call, !raise 500, !fold, !allin\n"
                "Info: !hand, !table\n"
                "Leave: !leave"
            ))
        return

    if   cmd == "join":        await _cmd_join(bot, user, args)
    elif cmd == "check":       await _cmd_check(bot, user)
    elif cmd == "call":        await _cmd_call(bot, user)
    elif cmd == "raise":       await _cmd_raise(bot, user, args)
    elif cmd == "fold":        await _cmd_fold(bot, user)
    elif cmd == "allin":       await _cmd_allin(bot, user)
    elif cmd == "hand":        await _cmd_hand(bot, user)
    elif cmd == "leave":       await _cmd_leave(bot, user)
    elif cmd == "table":       await _cmd_table(bot, user)
    elif cmd in ("debugcards", "debugdeal"): await _cmd_debugcards(bot, user)
    elif cmd == "resendcards": await _cmd_resendcards(bot, user)
