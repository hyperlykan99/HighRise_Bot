"""
modules/poker.py
Texas Hold'em Lite for the Highrise Mini Game Bot.

Commands (all prefixed /poker):
  join <buyin>  leave  hand  table  players  check  call  raise <amt>  fold
  rules  stats  cancel  on  off
"""
from __future__ import annotations
import asyncio
import random
from itertools import combinations
from collections import Counter
from typing import Optional

from highrise import BaseBot, User
import database as db
from modules.permissions import can_manage_games

# ── Constants ─────────────────────────────────────────────────────────────────

_RANKS      = "23456789TJQKA"
_SUITS      = "cdhs"
_SUIT_SYM   = {"c": "♣", "d": "♦", "h": "♥", "s": "♠"}
_HAND_NAMES = {
    9: "Royal Flush",    8: "Straight Flush", 7: "Four of a Kind",
    6: "Full House",     5: "Flush",          4: "Straight",
    3: "Three of a Kind",2: "Two Pair",        1: "Pair",  0: "High Card",
}

MIN_BUYIN     = 100
MAX_BUYIN     = 5_000
MAX_PLAYERS   = 6
LOBBY_SECS    = 15
TURN_SECS     = 20
MIN_RAISE     = 10    # minimum raise-by amount

_poker_enabled: bool = True


# ── Card utilities ────────────────────────────────────────────────────────────

def _make_deck() -> list[str]:
    return [r + s for r in _RANKS for s in _SUITS]


def _fc(card: str) -> str:
    return card[0].upper() + _SUIT_SYM[card[1]]


def _fcs(cards: list[str]) -> str:
    return " ".join(_fc(c) for c in cards)


# ── Hand evaluation ───────────────────────────────────────────────────────────

def _score5(cards: list[str]) -> tuple:
    """Score exactly 5 cards. Higher tuple = stronger hand."""
    ranks  = sorted([_RANKS.index(c[0]) for c in cards], reverse=True)
    suits  = [c[1] for c in cards]
    cnt    = Counter(ranks)
    freq   = sorted(cnt.values(), reverse=True)
    kr     = sorted(cnt.keys(), key=lambda r: (cnt[r], r), reverse=True)

    flush    = len(set(suits)) == 1
    straight = (ranks == list(range(ranks[0], ranks[0] - 5, -1)))
    if sorted(ranks) == [0, 1, 2, 3, 12]:     # wheel A-2-3-4-5
        straight = True
        ranks = kr = [3, 2, 1, 0, -1]

    if flush and straight:
        return (9 if ranks[0] == 12 else 8,) + tuple(ranks)
    if freq[0] == 4:         return (7,) + tuple(kr)
    if freq[:2] == [3, 2]:   return (6,) + tuple(kr)
    if flush:                return (5,) + tuple(ranks)
    if straight:             return (4,) + tuple(ranks)
    if freq[0] == 3:         return (3,) + tuple(kr)
    if freq[:2] == [2, 2]:   return (2,) + tuple(kr)
    if freq[0] == 2:         return (1,) + tuple(kr)
    return                         (0,) + tuple(ranks)


def _best_hand(all_cards: list[str]) -> tuple:
    """Best 5-card score from 7 cards (2 hole + 5 community)."""
    best: Optional[tuple] = None
    for combo in combinations(all_cards, 5):
        s = _score5(list(combo))
        if best is None or s > best:
            best = s
    return best  # type: ignore[return-value]


# ── Player & Table ────────────────────────────────────────────────────────────

class _Player:
    __slots__ = ("user_id", "username", "buyin", "stack",
                 "round_bet", "invested", "hole", "folded", "acted")

    def __init__(self, user_id: str, username: str, buyin: int) -> None:
        self.user_id   = user_id
        self.username  = username
        self.buyin     = buyin
        self.stack     = buyin   # chips at table, not yet bet
        self.round_bet = 0       # chips bet in this street
        self.invested  = 0       # cumulative chips placed in pot
        self.hole      : list[str] = []
        self.folded    = False
        self.acted     = False   # acted in current betting round


class _Table:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.state        : str                    = "idle"
        self.players      : list[_Player]          = []
        self.deck         : list[str]              = []
        self.community    : list[str]              = []
        self.pot          : int                    = 0
        self.current_bet  : int                    = 0
        self.action_idx   : int                    = 0
        self._cdown_task  : Optional[asyncio.Task] = None
        self._turn_task   : Optional[asyncio.Task] = None


_T = _Table()


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def _active() -> list[_Player]:
    return [p for p in _T.players if not p.folded]


def _find(user_id: str) -> Optional[_Player]:
    for p in _T.players:
        if p.user_id == user_id:
            return p
    return None


def _cancel(task: Optional[asyncio.Task]) -> None:
    if task and not task.done():
        task.cancel()


def _round_over() -> bool:
    active = _active()
    if len(active) <= 1:
        return True
    return (all(p.acted for p in active)
            and len(set(p.round_bet for p in active)) == 1)


# ── Countdown ─────────────────────────────────────────────────────────────────

async def _lobby_countdown(bot: BaseBot) -> None:
    await asyncio.sleep(LOBBY_SECS)
    if _T.state != "lobby":
        return
    if len(_T.players) < 2:
        players_copy = _T.players[:]
        _T.reset()
        for p in players_copy:
            db.adjust_balance(p.user_id, p.buyin)
        names = " ".join(f"@{p.username}" for p in players_copy)
        await _chat(bot, f"🃏 Not enough players. Buy-ins refunded: {names}.")
        return
    await _start_game(bot)


# ── Game start ────────────────────────────────────────────────────────────────

async def _start_game(bot: BaseBot) -> None:
    _T.state = "dealing"
    deck = _make_deck()
    random.shuffle(deck)
    _T.deck = deck
    _T.community = []
    _T.pot = 0

    for p in _T.players:
        p.hole      = [deck.pop(), deck.pop()]
        p.stack     = p.buyin
        p.invested  = 0
        p.folded    = False
        p.acted     = False

    plist = " ".join(f"@{p.username}" for p in _T.players)
    await _chat(bot, f"🃏 Poker! {plist[:90]}. Cards dealt — use /poker hand.")
    for p in _T.players:
        await _w(bot, p.user_id, f"🃏 Your cards: {_fcs(p.hole)}. Use /poker hand anytime.")

    await _start_betting_round(bot, "preflop")


# ── Betting round ─────────────────────────────────────────────────────────────

async def _start_betting_round(bot: BaseBot, street: str) -> None:
    _T.state       = street
    _T.current_bet = 0
    for p in _T.players:
        p.round_bet = 0
        p.acted     = False
    for i, p in enumerate(_T.players):
        if not p.folded:
            _T.action_idx = i
            break
    await _prompt(bot)


async def _prompt(bot: BaseBot) -> None:
    active = _active()
    if len(active) <= 1:
        await _end_early(bot)
        return

    p   = _T.players[_T.action_idx]
    bet = _T.current_bet
    owe = bet - p.round_bet
    st  = _T.state.upper()

    if owe <= 0:
        opts = "check/raise/fold"
    else:
        opts = f"call({owe}c)/raise/fold"

    msg = (f"🃏 [{st}] @{p.username}'s turn. Pot:{_T.pot}c Bet:{bet}c. "
           f"{opts} — /poker ___. {TURN_SECS}s")
    await _chat(bot, msg)
    _cancel(_T._turn_task)
    _T._turn_task = asyncio.create_task(_turn_timeout(bot, p.user_id))


async def _turn_timeout(bot: BaseBot, uid: str) -> None:
    await asyncio.sleep(TURN_SECS)
    if _T.state not in ("preflop", "flop", "turn", "river"):
        return
    p = _find(uid)
    if p is None or p.folded or p.acted:
        return
    if _T.players[_T.action_idx].user_id != uid:
        return
    if _T.current_bet == 0 or _T.current_bet == p.round_bet:
        await _chat(bot, f"⏱ @{p.username} timed out — auto-check.")
        await _do_check(bot, p)
    else:
        await _chat(bot, f"⏱ @{p.username} timed out — auto-fold.")
        await _do_fold(bot, p)


async def _advance(bot: BaseBot) -> None:
    """After an action: move to the next player or close the betting round."""
    if _round_over():
        await _advance_street(bot)
        return
    n   = len(_T.players)
    idx = _T.action_idx
    for _ in range(n):
        idx = (idx + 1) % n
        if not _T.players[idx].folded:
            _T.action_idx = idx
            break
    _cancel(_T._turn_task)
    await _prompt(bot)


async def _advance_street(bot: BaseBot) -> None:
    active = _active()
    if len(active) <= 1:
        await _end_early(bot)
        return
    s = _T.state
    if s == "preflop":
        f = [_T.deck.pop(), _T.deck.pop(), _T.deck.pop()]
        _T.community.extend(f)
        await _chat(bot, f"🃏 FLOP: {_fcs(f)}. Pot: {_T.pot}c.")
        await _start_betting_round(bot, "flop")
    elif s == "flop":
        t = _T.deck.pop()
        _T.community.append(t)
        await _chat(bot, f"🃏 TURN: {_fc(t)} | Board: {_fcs(_T.community)}. Pot: {_T.pot}c.")
        await _start_betting_round(bot, "turn")
    elif s == "turn":
        r = _T.deck.pop()
        _T.community.append(r)
        await _chat(bot, f"🃏 RIVER: {_fc(r)} | Board: {_fcs(_T.community)}. Pot: {_T.pot}c.")
        await _start_betting_round(bot, "river")
    elif s == "river":
        await _showdown(bot)


# ── End conditions ────────────────────────────────────────────────────────────

async def _end_early(bot: BaseBot) -> None:
    """All but one player folded — last player wins pot."""
    _cancel(_T._turn_task)
    active          = _active()
    pot             = _T.pot
    players_snap    = _T.players[:]
    _T.reset()

    if active:
        w = active[0]
        db.adjust_balance(w.user_id, pot + w.stack)
        for p in players_snap:
            if p.user_id != w.user_id and p.stack > 0:
                db.adjust_balance(p.user_id, p.stack)
        await _chat(bot, f"🏆 @{w.username} wins {pot}c — everyone else folded!")
        db.update_poker_stats(w.user_id, w.username,
                              wins=1, total_won=pot, biggest_pot=pot, hands=1)
        for p in players_snap:
            if p.user_id != w.user_id:
                db.update_poker_stats(p.user_id, p.username, folds=1, hands=1)
    else:
        for p in players_snap:
            db.adjust_balance(p.user_id, p.stack)


async def _showdown(bot: BaseBot) -> None:
    _T.state = "showdown"
    _cancel(_T._turn_task)

    active       = _active()
    pot          = _T.pot
    community    = _T.community[:]
    players_snap = _T.players[:]

    scores  = {p.user_id: _best_hand(p.hole + community) for p in active}
    best    = max(scores.values())
    winners = [p for p in active if scores[p.user_id] == best]

    share     = pot // len(winners)
    remainder = pot % len(winners)

    # Return unbet stacks to all players
    for p in players_snap:
        if p.stack > 0:
            db.adjust_balance(p.user_id, p.stack)

    # Pay out pot to winner(s)
    for i, w in enumerate(winners):
        prize = share + (remainder if i == 0 else 0)
        db.adjust_balance(w.user_id, prize)

    # Announce result
    board = _fcs(community)
    hname = _HAND_NAMES.get(best[0], "High Card")
    if len(winners) == 1:
        w = winners[0]
        await _chat(bot, f"🏆 @{w.username} wins {pot}c with {hname}! Board: {board}")
    else:
        wnames = " & ".join(f"@{w.username}" for w in winners)
        await _chat(bot, f"🤝 Split! {wnames} each get {share}c. {hname}.")

    # Reveal all active hands
    for p in active:
        hn = _HAND_NAMES.get(scores[p.user_id][0], "")
        await _chat(bot, f"  @{p.username}: {_fcs(p.hole)} — {hn}")

    # Update stats
    winner_set = {w.user_id for w in winners}
    for p in players_snap:
        won = p.user_id in winner_set
        db.update_poker_stats(
            p.user_id, p.username,
            wins=int(won),
            losses=int(not won and not p.folded),
            folds=int(p.folded),
            total_won=share if won else 0,
            biggest_pot=pot if won else 0,
            hands=1,
        )

    _T.reset()


# ── Action implementations ────────────────────────────────────────────────────

async def _do_check(bot: BaseBot, p: _Player) -> None:
    p.acted = True
    _cancel(_T._turn_task)
    await _chat(bot, f"✅ @{p.username} checks.")
    await _advance(bot)


async def _do_call(bot: BaseBot, p: _Player) -> None:
    owe = _T.current_bet - p.round_bet
    if owe <= 0:
        await _do_check(bot, p)
        return
    if p.stack < owe:
        await _w(bot, p.user_id,
                 f"Not enough chips to call {owe}c (stack: {p.stack}c). /poker fold")
        return
    p.stack     -= owe
    p.round_bet += owe
    p.invested  += owe
    _T.pot      += owe
    p.acted      = True
    _cancel(_T._turn_task)
    await _chat(bot, f"📞 @{p.username} calls {owe}c. Pot: {_T.pot}c.")
    await _advance(bot)


async def _do_raise(bot: BaseBot, p: _Player, raise_by: int) -> None:
    """raise_by is the raise AMOUNT on top of current_bet (not the total)."""
    if raise_by < MIN_RAISE:
        await _w(bot, p.user_id,
                 f"Min raise is {MIN_RAISE}c. Usage: /poker raise {MIN_RAISE}")
        return
    raise_to = _T.current_bet + raise_by
    extra    = raise_to - p.round_bet       # new chips this player must add
    if extra > p.stack:
        await _w(bot, p.user_id,
                 f"Not enough chips. Need {extra}c but stack is {p.stack}c.")
        return
    p.stack          -= extra
    p.round_bet       = raise_to
    p.invested       += extra
    _T.pot           += extra
    _T.current_bet    = raise_to
    p.acted           = True
    for other in _T.players:
        if other.user_id != p.user_id and not other.folded:
            other.acted = False
    _cancel(_T._turn_task)
    await _chat(bot, f"⬆️ @{p.username} raises by {raise_by}c (to {raise_to}c). Pot: {_T.pot}c.")
    await _advance(bot)


async def _do_fold(bot: BaseBot, p: _Player) -> None:
    p.folded = True
    p.acted  = True
    _cancel(_T._turn_task)
    await _chat(bot, f"🗂 @{p.username} folds.")
    await _advance(bot)


# ── Command handlers ──────────────────────────────────────────────────────────

async def handle_poker(bot: BaseBot, user: User, args: list[str]) -> None:
    try:
        await _dispatch(bot, user, args)
    except Exception as exc:
        print(f"[POKER] error for {user.username}: {exc}")
        try:
            await _w(bot, user.id, "Poker error — please try again.")
        except Exception:
            pass


async def _dispatch(bot: BaseBot, user: User, args: list[str]) -> None:
    global _poker_enabled

    if len(args) < 2:
        await _w(bot, user.id,
                 "🃏 /poker join <buyin> | hand | table | players\n"
                 "/poker check | call | raise <amt> | fold\n"
                 "/poker rules | stats")
        return

    sub = args[1].lower()

    # ── Staff controls ──────────────────────────────────────────────────────
    if sub == "on":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        _poker_enabled = True
        await _w(bot, user.id, "🃏 Poker enabled.")
        return

    if sub == "off":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        _poker_enabled = False
        await _w(bot, user.id, "🃏 Poker disabled.")
        return

    if sub == "cancel":
        if not can_manage_games(user.username):
            await _w(bot, user.id, "Managers+ only.")
            return
        if _T.state == "idle":
            await _w(bot, user.id, "No active poker game.")
            return
        _cancel(_T._cdown_task)
        _cancel(_T._turn_task)
        players_copy = _T.players[:]
        _T.reset()
        for p in players_copy:
            db.adjust_balance(p.user_id, p.buyin)
        await _chat(bot, f"🃏 Poker cancelled by @{user.username}. Buy-ins refunded.")
        return

    # ── Info commands ────────────────────────────────────────────────────────
    if sub == "rules":
        await _w(bot, user.id,
                 "🃏 Hold'em Lite: /poker join <buyin> (100–5000c). 2–6 players.\n"
                 "2 private + 5 board cards. Best 5-card hand wins the pot.\n"
                 "check/call/raise <amt>/fold. 20s per turn. No blinds.")
        return

    if sub == "stats":
        db.ensure_poker_stats(user.id, user.username)
        s = db.get_poker_stats(user.id)
        await _w(bot, user.id,
                 f"🃏 {user.username} Poker Stats:\n"
                 f"Hands:{s['hands_played']}  W:{s['wins']}  L:{s['losses']}  F:{s['folds']}\n"
                 f"Total won:{s['total_won']}c  Best pot:{s['biggest_pot']}c")
        return

    if sub == "players":
        if _T.state == "idle":
            await _w(bot, user.id, "No poker game running.")
            return
        parts = [f"@{p.username}({'folded' if p.folded else 'in'})" for p in _T.players]
        await _w(bot, user.id, ("🃏 Table: " + "  ".join(parts))[:249])
        return

    if sub == "table":
        if _T.state in ("idle", "lobby"):
            await _w(bot, user.id, "No active game yet.")
            return
        board = _fcs(_T.community) if _T.community else "—"
        await _w(bot, user.id,
                 f"🃏 {_T.state.upper()} | Board: {board} | Pot: {_T.pot}c")
        return

    if sub == "hand":
        p = _find(user.id)
        if p is None:
            await _w(bot, user.id, "You're not at the table.")
            return
        board = _fcs(_T.community) if _T.community else "not dealt yet"
        await _w(bot, user.id,
                 f"🃏 Hand: {_fcs(p.hole)} | Board: {board} | Stack: {p.stack}c")
        return

    # ── Join / Leave ─────────────────────────────────────────────────────────
    if sub == "join":
        await _join(bot, user, args)
        return

    if sub == "leave":
        await _leave(bot, user)
        return

    # ── Betting actions ──────────────────────────────────────────────────────
    if sub in ("check", "call", "raise", "fold"):
        await _action(bot, user, sub, args)
        return

    await _w(bot, user.id, "Unknown poker command. /poker for help.")


async def _join(bot: BaseBot, user: User, args: list[str]) -> None:
    global _poker_enabled
    if not _poker_enabled:
        await _w(bot, user.id, "Poker is currently disabled.")
        return
    if _T.state not in ("idle", "lobby"):
        await _w(bot, user.id, "A game is in progress. Join next round!")
        return
    if _find(user.id) is not None:
        await _w(bot, user.id, "You're already at the table.")
        return
    if len(_T.players) >= MAX_PLAYERS:
        await _w(bot, user.id, f"Table is full ({MAX_PLAYERS} max).")
        return
    if len(args) < 3 or not args[2].isdigit():
        await _w(bot, user.id, "Usage: /poker join <buyin>  (100–5000 coins)")
        return

    buyin = int(args[2])
    if buyin < MIN_BUYIN:
        await _w(bot, user.id, f"Minimum buy-in is {MIN_BUYIN}c.")
        return
    if buyin > MAX_BUYIN:
        await _w(bot, user.id, f"Maximum buy-in is {MAX_BUYIN:,}c.")
        return

    db.ensure_user(user.id, user.username)
    bal = db.get_balance(user.id)
    if bal < buyin:
        await _w(bot, user.id, f"Not enough coins. Balance: {bal}c.")
        return

    db.adjust_balance(user.id, -buyin)
    _T.players.append(_Player(user.id, user.username, buyin))
    db.ensure_poker_stats(user.id, user.username)
    count = len(_T.players)

    if _T.state == "idle":
        _T.state = "lobby"
        _T._cdown_task = asyncio.create_task(_lobby_countdown(bot))
        await _chat(bot,
                    f"🃏 @{user.username} opened a poker table! Buy-in: {buyin}c. "
                    f"Starting in {LOBBY_SECS}s.  /poker join <buyin> to play!")
    else:
        await _chat(bot,
                    f"🃏 @{user.username} joined poker. Buy-in: {buyin}c. "
                    f"Players: {count}/{MAX_PLAYERS}.")


async def _leave(bot: BaseBot, user: User) -> None:
    p = _find(user.id)
    if p is None:
        await _w(bot, user.id, "You're not at the table.")
        return
    if _T.state == "lobby":
        db.adjust_balance(user.id, p.buyin)
        _T.players.remove(p)
        count = len(_T.players)
        await _chat(bot,
                    f"🃏 @{user.username} left the lobby. {p.buyin}c refunded. "
                    f"Players: {count}/{MAX_PLAYERS}.")
        if not _T.players:
            _cancel(_T._cdown_task)
            _T.reset()
    else:
        await _w(bot, user.id, "Game in progress — use /poker fold to leave.")


async def _action(bot: BaseBot, user: User, sub: str, args: list[str]) -> None:
    p = _find(user.id)
    if p is None:
        await _w(bot, user.id, "You're not at the table.")
        return
    if _T.state not in ("preflop", "flop", "turn", "river"):
        await _w(bot, user.id, "No active betting round right now.")
        return
    if p.folded:
        await _w(bot, user.id, "You've already folded this hand.")
        return
    cur = _T.players[_T.action_idx]
    if cur.user_id != user.id:
        await _w(bot, user.id, f"It's @{cur.username}'s turn, not yours.")
        return

    if sub == "check":
        if _T.current_bet > p.round_bet:
            await _w(bot, user.id,
                     f"Can't check — there's a bet of {_T.current_bet}c. "
                     f"Try /poker call or /poker fold.")
            return
        await _do_check(bot, p)

    elif sub == "call":
        await _do_call(bot, p)

    elif sub == "raise":
        if len(args) < 3 or not args[2].isdigit():
            await _w(bot, user.id,
                     f"Usage: /poker raise <amount>  (min {MIN_RAISE}c above current bet)")
            return
        await _do_raise(bot, p, int(args[2]))

    elif sub == "fold":
        await _do_fold(bot, p)


# ── Public reset (called by /casino reset) ────────────────────────────────────

def reset_table() -> None:
    """Safely wipe the table and refund all buy-ins."""
    _cancel(_T._cdown_task)
    _cancel(_T._turn_task)
    players_copy = _T.players[:]
    _T.reset()
    for p in players_copy:
        db.adjust_balance(p.user_id, p.buyin)
