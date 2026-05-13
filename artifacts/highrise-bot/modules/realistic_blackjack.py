"""
modules/realistic_blackjack.py
-------------------------------
Realistic Blackjack — SIMULTANEOUS action model with persistent shoe.

Uses a persistent shared shoe (default 6 decks) that carries across rounds.
Reshuffles when >= shuffle_used_percent% is dealt OR < 52 cards remain.
All players act simultaneously during a shared action timer after deal.
Supports split (multiple hands per player) and double down.

Public:  /rbj join <bet>  /rbj leave  /rbj players  /rbj table  /rbj hand
         /rbj hit  /rbj stand  /rbj double  /rbj split
         /rbj rules  /rbj stats  /rbj shoe  /rbj limits  /rbj leaderboard
Manager: /rbj on  /rbj off  /rbj cancel  /rbj settings
         /rbj double on|off  /rbj split on|off  /rbj splitaces on|off
         /rbj state  /rbj recover  /rbj refund  /rbj forcefinish
Admin:   /setrbjdecks  /setrbjminbet  /setrbjmaxbet  /setrbjshuffle
         /setrbjblackjackpayout  /setrbjwinpayout  /setrbjcountdown
         /setrbjactiontimer  /setrbjmaxsplits
         /setrbjdailywinlimit  /setrbjdailylosslimit
"""

import asyncio
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from highrise import BaseBot, User

import database as db
from modules.quests      import track_quest
from modules.cards       import make_shoe, hand_str, hand_value, is_blackjack, card_str
from modules.shop        import get_player_benefits
from modules.permissions import can_manage_games, can_moderate, is_owner

_RBJ_CASINO_CAP = 5.0

# Watchers: user_id -> username — receive whisper copies of round events
_bj_watchers: dict[str, str] = {}
# Last bet per user for /bet same|repeat
_last_rbj_bets: dict[str, int] = {}


def _dn(p: "_Player") -> str:
    """Display name for a _Player object (badge + title + @username)."""
    try:
        return db.get_display_name(p.user_id, p.username)
    except Exception:
        return f"@{p.username}"


def _remaining_secs(iso_str: str, default: int = 0) -> int:
    if not iso_str:
        return default
    try:
        end = datetime.fromisoformat(iso_str)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return max(0, int((end - datetime.now(timezone.utc)).total_seconds()))
    except Exception:
        return default


def _hand_key(username: str, idx: int) -> str:
    return f"{username}_h{idx}"


def _make_hand(bet: int, from_split: bool = False) -> dict:
    return {
        "cards": [], "bet": bet, "status": "active",
        "doubled": False, "from_split": from_split,
    }


# ─── Persistent shoe ─────────────────────────────────────────────────────────

class _Shoe:
    def __init__(self, decks: int = 6):
        self._decks  = decks
        self._cards: list = []
        self._total  = 0
        self._reshuffle(decks)

    def _reshuffle(self, decks: int):
        self._decks  = decks
        self._total  = decks * 52
        self._cards  = make_shoe(decks)

    @property
    def remaining(self) -> int:
        return len(self._cards)

    @property
    def total(self) -> int:
        return self._total

    @property
    def used(self) -> int:
        return self._total - self.remaining

    @property
    def used_pct(self) -> float:
        return (self.used / self._total * 100) if self._total else 0.0

    @property
    def decks_used(self) -> float:
        return round(self.used / 52, 1)

    def needs_shuffle(self, threshold_pct: float = 75.0) -> bool:
        return self.used_pct >= threshold_pct or self.remaining < 52

    def shuffle_now(self, decks: int | None = None):
        self._reshuffle(decks if decks is not None else self._decks)

    def pop(self) -> tuple:
        if not self._cards:
            self._reshuffle(self._decks)
        return self._cards.pop()


_shoe = _Shoe(6)


# ─── In-memory game state ─────────────────────────────────────────────────────

@dataclass
class _Player:
    user_id:            str
    username:           str
    bet:                int
    hands:              list = field(default_factory=list)
    active_hand_idx:    int  = 0
    split_count:        int  = 0
    insurance_bet:      int  = 0
    insurance_taken:    bool = False
    insurance_resolved: bool = False

    def current_hand(self):
        if self.active_hand_idx < len(self.hands):
            return self.hands[self.active_hand_idx]
        return None

    def is_done(self) -> bool:
        return bool(self.hands) and all(h["status"] != "active" for h in self.hands)

    def total_bet(self) -> int:
        return sum(h["bet"] for h in self.hands) if self.hands else self.bet

    def advance_hand(self) -> None:
        self.active_hand_idx += 1
        while self.active_hand_idx < len(self.hands):
            if self.hands[self.active_hand_idx]["status"] == "active":
                break
            self.active_hand_idx += 1


class _RBJState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.phase:              str  = "idle"
        self.players:            list = []
        self.dealer_hand:        list = []
        self.lobby_task               = None
        self.action_task              = None
        self.round_id:           str  = ""
        self._countdown_ends_at: str  = ""
        self._action_ends_at:    str  = ""

    def get_player(self, user_id: str):
        for p in self.players:
            if p.user_id == user_id:
                return p
        return None

    def in_game(self, user_id: str) -> bool:
        return self.get_player(user_id) is not None


_state = _RBJState()


# ─── Force / debug state (owner-only, one-use per round) ──────────────────────

_force_state: dict = {
    "player_cards": {},    # {username_lower: ((rank,suit),(rank,suit))}
    "dealer_cards":  None, # ((rank,suit),(rank,suit)) | None
    "dealer_ace":    False,
    "player_pair":   None, # username_lower or None (None = not set)
    "player_bj":     None, # username_lower or None (None = not set)
    "shoe_low":      False,
}

_SUIT_MAP: dict[str, str] = {
    "s": "♠", "h": "♥", "d": "♦", "c": "♣",
    "♠": "♠", "♥": "♥", "♦": "♦", "♣": "♣",
}
_RANK_VALID = frozenset({"A","2","3","4","5","6","7","8","9","10","J","Q","K"})


def _parse_card(s: str) -> tuple | None:
    """Parse 'AS', 'A♠', '10D', '10♦' → ('A','♠'). Returns None if invalid."""
    s = s.strip().lstrip("@")
    for sym in ("♠", "♥", "♦", "♣"):
        if s.endswith(sym):
            rank = s[:-1].upper()
            return (rank, sym) if rank in _RANK_VALID else None
    if s and s[-1].lower() in _SUIT_MAP:
        suit = _SUIT_MAP[s[-1].lower()]
        rank = s[:-1].upper()
        return (rank, suit) if rank in _RANK_VALID else None
    return None


_BJ_PACE_PRESETS: dict[str, dict] = {
    "fast":   {"lobby_countdown": 15, "rbj_action_timer": 15},
    "normal": {"lobby_countdown": 30, "rbj_action_timer": 30},
    "long":   {"lobby_countdown": 60, "rbj_action_timer": 45},
}


# ─── DB persistence ───────────────────────────────────────────────────────────

def _save_table_state() -> None:
    try:
        shoe_snapshot = json.dumps({
            "cards": _shoe._cards,
            "total": _shoe._total,
            "decks": _shoe._decks,
        })
        db.save_casino_table("rbj", {
            "phase":                _state.phase,
            "round_id":             _state.round_id,
            "current_player_index": 0,
            "dealer_hand_json":     json.dumps(_state.dealer_hand),
            "deck_json":            "[]",
            "shoe_json":            shoe_snapshot,
            "shoe_cards_remaining": _shoe.remaining,
            "countdown_ends_at":    _state._countdown_ends_at,
            "turn_ends_at":         _state._action_ends_at,
            "active":               1 if _state.phase != "idle" else 0,
            "recovery_required":    0,
        })
    except Exception as exc:
        print(f"[RBJ] save_table_state error: {exc}")


def _save_player_state(p: _Player) -> None:
    try:
        db.save_casino_player("rbj", {
            "username":  p.username,
            "user_id":   p.user_id,
            "bet":       p.total_bet(),
            "hand_json": json.dumps({
                "hands":              p.hands,
                "split_count":        p.split_count,
                "insurance_bet":      p.insurance_bet,
                "insurance_taken":    p.insurance_taken,
                "insurance_resolved": p.insurance_resolved,
            }),
            "status":    "done" if p.is_done() else "playing",
            "doubled":   p.active_hand_idx,
            "payout":    0,
            "result":    "",
        })
    except Exception as exc:
        print(f"[RBJ] save_player_state error for {p.username}: {exc}")


def _save_all_player_states() -> None:
    for p in _state.players:
        _save_player_state(p)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _settings() -> dict:
    return db.get_rbj_settings()


def _cancel_task(task, label: str = ""):
    if task and not task.done():
        task.cancel()
        if label:
            print(f"[RBJ] {label} cancelled")


def _is_soft_17(hand: list) -> bool:
    if hand_value(hand) != 17:
        return False
    hard = sum(
        10 if r in ("J", "Q", "K") else (1 if r == "A" else int(r))
        for r, _ in hand
    )
    return hard != 17


def _card_clr(card: tuple) -> str:
    r, s = card
    if s in ("♥", "♦"):
        return f"<#FF5555>{r}{s}<#FFFFFF>"
    return f"{r}{s}"


def _hand_colored(cards: list) -> str:
    return " ".join(_card_clr(c) for c in cards)


def _all_done() -> bool:
    return bool(_state.players) and all(p.is_done() for p in _state.players)


# ─── Lobby countdown ─────────────────────────────────────────────────────────

async def _lobby_countdown(bot: BaseBot, seconds: int):
    print(f"[RBJ] Countdown started ({seconds}s)")
    end_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    _state._countdown_ends_at = end_at.isoformat()
    _save_table_state()
    try:
        await asyncio.sleep(seconds)
        await _start_round(bot)
    except asyncio.CancelledError:
        print("[RBJ] Countdown cancelled")
        raise


# ─── Round start ─────────────────────────────────────────────────────────────

async def _start_round(bot: BaseBot):
    if _state.phase != "lobby" or not _state.players:
        _state.reset()
        db.clear_casino_table("rbj")
        return

    s         = _settings()
    threshold = float(s.get("shuffle_used_percent", 75))
    decks     = int(s.get("decks", 6))

    if _shoe.needs_shuffle(threshold):
        _shoe.shuffle_now(decks)
        await bot.highrise.chat(
            f"🔀 Shoe reshuffled! {_shoe.total} cards ({decks} decks) ready."
        )

    _state.phase    = "round"
    _state.round_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f") + "_rbj"
    _state._countdown_ends_at = ""

    for p in _state.players:
        p.hands           = [_make_hand(p.bet)]
        p.active_hand_idx = 0
        p.split_count     = 0

    # Apply shoe_low force before deal (drains shoe near reshuffle threshold)
    if _force_state["shoe_low"]:
        target = max(54, int(_shoe.total * (1.0 - threshold / 100.0)) + 4)
        while _shoe.remaining > target:
            _shoe._cards.pop()
        _force_state["shoe_low"] = False
        print(f"[RBJ] [FORCE] Shoe drained to {_shoe.remaining} cards (near threshold).")

    for _ in range(2):
        for p in _state.players:
            p.hands[0]["cards"].append(_shoe.pop())
        _state.dealer_hand.append(_shoe.pop())

    # Apply forced dealer/player cards (owner debug, one-use)
    if (_force_state["dealer_cards"] or _force_state["dealer_ace"]
            or _force_state["player_cards"]
            or _force_state["player_pair"] is not None
            or _force_state["player_bj"] is not None):
        if _force_state["dealer_cards"] and len(_state.dealer_hand) >= 2:
            dc = _force_state["dealer_cards"]
            _state.dealer_hand[0] = dc[0]
            _state.dealer_hand[1] = dc[1]
            _force_state["dealer_cards"] = None
            print(f"[RBJ] [FORCE] Dealer: {dc[0]} {dc[1]}")
        if _force_state["dealer_ace"] and _state.dealer_hand:
            _state.dealer_hand[0] = ("A", random.choice(["♠", "♥", "♦", "♣"]))
            _force_state["dealer_ace"] = False
            print(f"[RBJ] [FORCE] Dealer ace forced")
        for p in _state.players:
            uname = p.username.lower()
            if uname in _force_state["player_cards"]:
                fc = _force_state["player_cards"][uname]
                if p.hands:
                    p.hands[0]["cards"] = list(fc)
                del _force_state["player_cards"][uname]
                print(f"[RBJ] [FORCE] @{p.username} cards: {fc}")
            elif (_force_state["player_pair"] is not None
                  and _force_state["player_pair"] == uname):
                rank  = random.choice(["2","3","4","5","6","7","8","9","10"])
                suits = random.sample(["♠","♥","♦","♣"], 2)
                p.hands[0]["cards"] = [(rank, suits[0]), (rank, suits[1])]
                _force_state["player_pair"] = None
                print(f"[RBJ] [FORCE] @{p.username} pair: {rank}")
            elif (_force_state["player_bj"] is not None
                  and _force_state["player_bj"] == uname):
                p.hands[0]["cards"] = [("A", "♠"), ("K", "♠")]
                _force_state["player_bj"] = None
                print(f"[RBJ] [FORCE] @{p.username} blackjack: A♠ K♠")
        # Clear any owner-self pair/BJ force after first player processed
        if _force_state["player_pair"] == "":
            _force_state["player_pair"] = None
        if _force_state["player_bj"] == "":
            _force_state["player_bj"] = None

    for p in _state.players:
        if is_blackjack(p.hands[0]["cards"]):
            p.hands[0]["status"] = "blackjack"

    _save_table_state()
    _save_all_player_states()
    print(f"[RBJ] Round started. round_id={_state.round_id}")

    await bot.highrise.chat(
        f"🃏 BlackJack started! Dealer shows: {card_str(_state.dealer_hand[0])}"
    )
    for p in _state.players:
        if p.hands[0]["status"] == "blackjack":
            await bot.highrise.chat(f"🤑 {_dn(p)} has Blackjack!")

    await _start_action_phase(bot)


# ─── Simultaneous action phase ────────────────────────────────────────────────

def _visible_dealer_total() -> int:
    """Value of just the dealer's face-up (first) card."""
    if not _state.dealer_hand:
        return 0
    r = _state.dealer_hand[0][0]
    if r == "A":
        return 11
    if r in ("J", "Q", "K"):
        return 10
    try:
        return int(r)
    except Exception:
        return 10


def _build_actions_compact(p: "_Player", s: dict) -> str:
    """One-line compact action string for public display mode."""
    h = p.current_hand()
    if h is None or h["status"] != "active":
        return ""
    cards = h["cards"]
    acts  = ["🃏!hit", "🛑!stand"]
    if int(s.get("rbj_double_enabled", 1)) and len(cards) == 2 and not h.get("doubled"):
        acts.append("💰!dbl")
    if (int(s.get("rbj_split_enabled", 1))
            and len(cards) == 2
            and cards[0][0] == cards[1][0]
            and p.split_count < int(s.get("rbj_max_splits", 1))):
        acts.append("✂️!split")
    dealer_up    = _state.dealer_hand[0] if _state.dealer_hand else None
    is_first_two = len(cards) == 2 and p.split_count == 0 and not p.insurance_taken
    if (dealer_up and dealer_up[0] == "A"
            and int(s.get("rbj_insurance_enabled", 1))
            and is_first_two):
        acts.append("🛡️!ins")
    if (int(s.get("rbj_surrender_enabled", 1))
            and len(cards) == 2 and p.split_count == 0
            and not p.insurance_taken):
        acts.append("🏳️!sur")
    return " ".join(acts)


def _build_actions(p: "_Player", s: dict) -> str:
    """Return the applicable action lines for a player based on current hand state."""
    h = p.current_hand()
    if h is None or h["status"] != "active":
        return ""
    cards = h["cards"]
    acts  = ["🃏 !hit", "🛑 !stand or !stay"]
    if int(s.get("rbj_double_enabled", 1)) and len(cards) == 2 and not h.get("doubled"):
        acts.append("💰 !double")
    if (int(s.get("rbj_split_enabled", 1))
            and len(cards) == 2
            and cards[0][0] == cards[1][0]
            and p.split_count < int(s.get("rbj_max_splits", 1))):
        acts.append("✂️ !split")
    dealer_up    = _state.dealer_hand[0] if _state.dealer_hand else None
    is_first_two = len(cards) == 2 and p.split_count == 0 and not p.insurance_taken
    if (dealer_up and dealer_up[0] == "A"
            and int(s.get("rbj_insurance_enabled", 1))
            and is_first_two):
        acts.append("🛡️ !insurance")
    if (int(s.get("rbj_surrender_enabled", 1))
            and len(cards) == 2 and p.split_count == 0
            and not p.insurance_taken):
        acts.append("🏳️ !surrender")
    return "\n".join(acts)


async def _start_action_phase(bot: BaseBot):
    s     = _settings()
    timer = int(s.get("rbj_action_timer", 30))

    end_at = datetime.now(timezone.utc) + timedelta(seconds=timer)
    _state._action_ends_at = end_at.isoformat()
    _save_table_state()

    if _all_done():
        await _finalize_round(bot)
        return

    s_cards   = str(db.get_bj_settings().get("bj_cards_mode", "whisper")).lower()
    upcard    = _state.dealer_hand[0] if _state.dealer_hand else None
    upcard_str = card_str(upcard) if upcard else "?"
    dealer_up  = _card_clr(upcard) if upcard else "?"
    vis_total  = _visible_dealer_total()

    # Msg 1 (always public): Dealer Cards
    await bot.highrise.chat(
        f"🃏 Dealer Cards\nDealer: {upcard_str} [?]\nVisible Total: {vis_total}"[:249]
    )

    # Msg 2: Player Cards + smart actions (compact public OR detailed whisper)
    if s_cards == "public":
        # Compact: all active players batched into paginated public messages
        player_lines: list[str] = []
        for p in _state.players:
            if p.is_done():
                continue
            hparts = []
            for i, h in enumerate(p.hands):
                hv   = hand_value(h["cards"])
                hstr = hand_str(h["cards"])
                note = f"[{h['status']}]" if h["status"] != "active" else ""
                pfx  = f"H{i+1}:" if len(p.hands) > 1 else ""
                hparts.append(f"{pfx}{hstr}={hv}{note}")
            hand_disp = " ".join(hparts)
            acts_c    = _build_actions_compact(p, s)
            line = f"@{p.username}: {hand_disp}" + (f" | {acts_c}" if acts_c else "")
            player_lines.append(line)

        HDR   = "🟢 Player Cards\n"
        pages: list[str] = []
        cur   = ""
        for line in player_lines:
            candidate = (cur + "\n" + line).strip() if cur else line
            if len(HDR + candidate) > 248:
                if cur:
                    pages.append(cur)
                cur = line
            else:
                cur = candidate
        if cur:
            pages.append(cur)

        for i, body in enumerate(pages):
            hdr = f"🟢 Player Cards {i+1}/{len(pages)}\n" if len(pages) > 1 else HDR
            try:
                await bot.highrise.chat((hdr + body)[:249])
            except Exception:
                pass
    else:
        # Whisper mode: detailed per-player green-coloured message
        for p in _state.players:
            if not p.is_done():
                try:
                    hparts = []
                    for i, h in enumerate(p.hands):
                        hdisp = _hand_colored(h["cards"])
                        hval  = hand_value(h["cards"])
                        note  = f" [{h['status']}]" if h["status"] != "active" else ""
                        hparts.append(f"H{i+1}: {hdisp}={hval}{note}")
                    cards_line = " | ".join(hparts)
                    acts       = _build_actions(p, s)
                    wtext = (
                        f"<#00FF66>🟢 Player Cards\n"
                        f"You: {cards_line}\n"
                        f"Dealer: {dealer_up} [?] | Visible: {vis_total}\n"
                        f"Bet: {p.total_bet():,}c\n"
                        f"{acts}<#FFFFFF>"
                    )
                    await bot.highrise.send_whisper(p.user_id, wtext[:249])
                except Exception:
                    pass

    print(f"[RBJ] Action timer started ({timer}s)")
    _state.action_task = asyncio.create_task(_action_timeout(bot, timer))


async def _action_timeout(bot: BaseBot, seconds: int):
    try:
        await asyncio.sleep(seconds)
    except asyncio.CancelledError:
        raise

    print("[RBJ] Action timer expired — auto-standing remaining hands")
    for p in _state.players:
        for h in p.hands:
            if h["status"] == "active":
                if len(h["cards"]) < 2:
                    h["status"] = "refunded"
                    print(f"[RBJ] @{p.username} hand has {len(h['cards'])} cards — marking refunded")
                else:
                    h["status"] = "stood"
        _save_player_state(p)

    _state.action_task     = None
    _state._action_ends_at = ""
    _save_table_state()

    await bot.highrise.chat("⏰ Action time over. Dealer plays.")
    await _finalize_round(bot)


async def _check_and_resolve(bot: BaseBot) -> bool:
    if _all_done():
        _cancel_task(_state.action_task, "Action timer")
        _state.action_task     = None
        _state._action_ends_at = ""
        _save_table_state()
        await _finalize_round(bot)
        return True
    return False


# ─── Finalize round ───────────────────────────────────────────────────────────

async def _finalize_round(bot: BaseBot):
    _cancel_task(_state.action_task, "Action timer")
    _state.action_task     = None
    _state._action_ends_at = ""
    try:
        s           = _settings()
        hits_soft17 = bool(int(s.get("dealer_hits_soft_17", 1)))
        win_payout  = float(s.get("win_payout", 2.0))
        bj_payout   = float(s.get("blackjack_payout", 2.5))
        push_rule   = s.get("push_rule", "refund")

        dealer_total = hand_value(_state.dealer_hand)
        await bot.highrise.chat(
            f"Dealer reveals: {hand_str(_state.dealer_hand)} = {dealer_total}"
        )

        while True:
            dealer_total = hand_value(_state.dealer_hand)
            if dealer_total > 17:
                break
            if dealer_total == 17 and not hits_soft17:
                break
            if dealer_total == 17 and not _is_soft_17(_state.dealer_hand):
                break
            card = _shoe.pop()
            _state.dealer_hand.append(card)
            dealer_total = hand_value(_state.dealer_hand)
            _save_table_state()
            await bot.highrise.chat(
                f"Dealer hits {card_str(card)}. "
                f"Hand: {hand_str(_state.dealer_hand)} = {dealer_total}"
            )

        dealer_total   = hand_value(_state.dealer_hand)
        dealer_bust    = dealer_total > 21
        dealer_has_bj  = is_blackjack(_state.dealer_hand)
        _rbj_event_pts = (db.is_event_active()
                          and bool(int(db.get_rbj_settings().get("rbj_enabled", 1))))
        round_id       = _state.round_id

        # ── Resolve insurance bets ──────────────────────────────────────────────
        for p in _state.players:
            if p.insurance_taken and not p.insurance_resolved:
                try:
                    if dealer_has_bj:
                        ins_payout = p.insurance_bet * 3  # return bet + 2:1 profit
                        db.adjust_balance(p.user_id, ins_payout)
                        db.add_ledger_entry(p.user_id, p.username, ins_payout, "insurance_payout")
                        await bot.highrise.send_whisper(
                            p.user_id,
                            f"🛡️ Insurance wins! +{p.insurance_bet * 2:,}c profit returned."
                        )
                    else:
                        await bot.highrise.send_whisper(
                            p.user_id,
                            f"🛡️ Insurance lost. -{p.insurance_bet:,}c."
                        )
                    p.insurance_resolved = True
                    _save_player_state(p)
                except Exception as exc:
                    print(f"[RBJ] Insurance resolve error for {p.username}: {exc}")

        for p in _state.players:
            try:
                track_quest(p.user_id, "bj_round")
                if _rbj_event_pts:
                    db.add_event_points(p.user_id, 1)
                benefits  = get_player_benefits(p.user_id)
                bonus_pct = min(
                    float(benefits.get("coinflip_payout_pct", 0.0)),
                    _RBJ_CASINO_CAP
                ) / 100.0

                total_net    = 0
                result_parts = []

                for i, h in enumerate(p.hands):
                    hkey   = _hand_key(p.username, i)
                    hbet   = h["bet"]
                    hst    = h["status"]
                    htotal = hand_value(h["cards"])

                    if round_id and db.is_result_paid("rbj", round_id, hkey):
                        print(f"[RBJ] Skipping already-paid {hkey}")
                        continue

                    if len(h["cards"]) < 2 or hst == "refunded":
                        db.adjust_balance(p.user_id, hbet)
                        db.add_ledger_entry(p.user_id, p.username, hbet, "rbj_deal_refund")
                        result_parts.append(f"H{i+1} refund(no cards)")
                        print(f"[RBJ] @{p.username} H{i+1} refunded {hbet}c (cards={len(h['cards'])})")
                        if round_id:
                            db.save_round_result("rbj", round_id, hkey, p.user_id,
                                                 hbet, "refund", hbet, 0)
                            db.mark_result_paid("rbj", round_id, hkey)
                        continue

                    if hst == "bust":
                        if round_id:
                            db.save_round_result("rbj", round_id, hkey, p.user_id,
                                                 hbet, "bust", 0, -hbet)
                        db.update_rbj_stats(p.user_id, loss=1, bet=hbet, lost=hbet)
                        db.add_rbj_daily_net(p.user_id, -hbet)
                        total_net -= hbet
                        result_parts.append(f"H{i+1} bust")
                        if round_id:
                            db.mark_result_paid("rbj", round_id, hkey)

                    elif hst == "blackjack":
                        payout = int(hbet * bj_payout * (1.0 + bonus_pct))
                        if round_id:
                            db.save_round_result("rbj", round_id, hkey, p.user_id,
                                                 hbet, "blackjack", payout, payout - hbet)
                        db.adjust_balance(p.user_id, payout)
                        db.add_coins_earned(p.user_id, payout - hbet)
                        db.update_rbj_stats(p.user_id, win=1, bj=1, bet=hbet, won=payout)
                        db.add_rbj_daily_net(p.user_id, payout - hbet)
                        total_net += payout - hbet
                        result_parts.append(f"H{i+1} BJ +{payout:,}c")
                        if round_id:
                            db.mark_result_paid("rbj", round_id, hkey)

                    elif dealer_bust or htotal > dealer_total:
                        payout = int(hbet * win_payout * (1.0 + bonus_pct))
                        if round_id:
                            db.save_round_result("rbj", round_id, hkey, p.user_id,
                                                 hbet, "win", payout, payout - hbet)
                        db.adjust_balance(p.user_id, payout)
                        db.add_coins_earned(p.user_id, payout - hbet)
                        db.update_rbj_stats(p.user_id, win=1, bet=hbet, won=payout)
                        db.add_rbj_daily_net(p.user_id, payout - hbet)
                        total_net += payout - hbet
                        result_parts.append(f"H{i+1} win +{payout:,}c")
                        if round_id:
                            db.mark_result_paid("rbj", round_id, hkey)

                    elif htotal == dealer_total:
                        refund = hbet if push_rule == "refund" else 0
                        if round_id:
                            db.save_round_result("rbj", round_id, hkey, p.user_id,
                                                 hbet, "push", refund, 0 if refund else -hbet)
                        if push_rule == "refund":
                            db.adjust_balance(p.user_id, hbet)
                        db.update_rbj_stats(p.user_id, push=1, bet=hbet)
                        result_parts.append(f"H{i+1} push")
                        if round_id:
                            db.mark_result_paid("rbj", round_id, hkey)

                    else:
                        if round_id:
                            db.save_round_result("rbj", round_id, hkey, p.user_id,
                                                 hbet, "loss", 0, -hbet)
                        db.update_rbj_stats(p.user_id, loss=1, bet=hbet, lost=hbet)
                        db.add_rbj_daily_net(p.user_id, -hbet)
                        total_net -= hbet
                        result_parts.append(f"H{i+1} loss")
                        if round_id:
                            db.mark_result_paid("rbj", round_id, hkey)

                if result_parts:
                    net_str = f"+{total_net:,}c" if total_net >= 0 else f"{total_net:,}c"
                    inner   = " | ".join(result_parts)
                    summary = f"{_dn(p)}: {inner} | Net {net_str}"
                    await bot.highrise.chat(summary[:249])
                    try:
                        hlines = []
                        for i, h in enumerate(p.hands):
                            hlines.append(
                                f"H{i+1}: {_hand_colored(h['cards'])}"
                                f"={hand_value(h['cards'])} [{h['status']}]"
                            )
                        dlr_disp = _hand_colored(_state.dealer_hand)
                        dlr_val  = hand_value(_state.dealer_hand)
                        wlines   = [
                            "🏁 Result",
                            f"Dealer: {dlr_disp}={dlr_val}",
                        ] + hlines + [f"Net: {net_str}"]
                        await bot.highrise.send_whisper(
                            p.user_id, "\n".join(wlines)[:249]
                        )
                    except Exception:
                        pass

            except Exception as exc:
                print(f"[RBJ] settle error for {p.username}: {exc}")

    except Exception as exc:
        print(f"[RBJ] finalize_round error: {exc}")
    finally:
        print("[RBJ] Round ended")
        db.clear_casino_table("rbj")
        _state.reset()


# ─── Public reset functions ───────────────────────────────────────────────────

def reset_table() -> str:
    if _state.phase == "idle":
        return "idle"
    for p in _state.players:
        try:
            refund = p.total_bet()
            db.adjust_balance(p.user_id, refund)
            db.add_ledger_entry(p.user_id, p.username, refund, "rbj_cancel_refund")
        except Exception as exc:
            print(f"[RBJ] reset_table refund error for {p.username}: {exc}")
    _cancel_task(_state.lobby_task, "Countdown")
    _cancel_task(_state.action_task, "Action timer")
    db.clear_casino_table("rbj")
    _state.reset()
    print("[RBJ] Table reset by admin")
    return "reset"


def soft_reset_table() -> None:
    if _state.phase == "idle":
        return
    _save_table_state()
    _save_all_player_states()
    _cancel_task(_state.lobby_task, "Countdown")
    _cancel_task(_state.action_task, "Action timer")
    _state.reset()
    print("[RBJ] Table soft-reset (state saved to DB for recovery)")


# ─── Startup recovery ─────────────────────────────────────────────────────────

def _restore_player_from_db(pd: dict) -> "_Player":
    raw       = pd.get("hand_json") or "{}"
    hand_data = json.loads(raw)
    if isinstance(hand_data, dict) and "hands" in hand_data:
        hands       = hand_data["hands"]
        split_count = int(hand_data.get("split_count", 0))
    elif isinstance(hand_data, list):
        hands       = [{"cards": hand_data, "bet": int(pd["bet"]),
                        "status": pd.get("status", "active"), "doubled": False}]
        split_count = 0
    else:
        hands       = []
        split_count = 0
    active_hand_idx = int(pd.get("doubled", 0))
    return _Player(
        user_id=pd["user_id"],
        username=pd["username"],
        bet=hands[0]["bet"] if hands else int(pd["bet"]),
        hands=hands,
        active_hand_idx=active_hand_idx,
        split_count=split_count,
        insurance_bet=int(hand_data.get("insurance_bet", 0)),
        insurance_taken=bool(hand_data.get("insurance_taken", False)),
        insurance_resolved=bool(hand_data.get("insurance_resolved", False)),
    )


async def startup_rbj_recovery(bot: BaseBot) -> None:
    row = db.load_casino_table("rbj")
    if not row or not row.get("active") or row.get("phase", "idle") == "idle":
        return

    phase = row.get("phase", "idle")
    print(f"[RECOVERY] RBJ found saved phase={phase}")

    if row.get("recovery_required"):
        print("[RECOVERY] RBJ marked recovery_required — alerting in chat.")
        try:
            await bot.highrise.chat("⚠️ BlackJack (Shoe) recovery needed. Use !rbj recover or !rbj refund.")
        except Exception:
            pass
        return

    try:
        shoe_raw = row.get("shoe_json", "[]")
        if shoe_raw and shoe_raw not in ("[]", "{}"):
            try:
                shoe_data = json.loads(shoe_raw)
                if isinstance(shoe_data, dict) and shoe_data.get("cards"):
                    _shoe._cards = shoe_data["cards"]
                    _shoe._total = int(shoe_data.get("total", len(_shoe._cards)))
                    _shoe._decks = int(shoe_data.get("decks", 6))
                    print(f"[RECOVERY] RBJ shoe restored: {_shoe.remaining} cards")
                elif isinstance(shoe_data, list) and shoe_data:
                    _shoe._cards = shoe_data
                    _shoe._total = max(len(shoe_data), _shoe._total)
            except Exception as exc:
                print(f"[RECOVERY] RBJ shoe restore error: {exc}")

        players_data = db.load_casino_players("rbj")
        if not players_data:
            print("[RECOVERY] RBJ: no players found, clearing state.")
            db.clear_casino_table("rbj")
            return

        _state.players            = [_restore_player_from_db(pd) for pd in players_data]
        _state.round_id           = row.get("round_id", "")
        _state.dealer_hand        = json.loads(row.get("dealer_hand_json") or "[]")
        _state._countdown_ends_at = row.get("countdown_ends_at", "")
        _state._action_ends_at    = row.get("turn_ends_at", "")

        if phase == "lobby":
            _state.phase = "lobby"
            secs = _remaining_secs(_state._countdown_ends_at, default=5)
            _state.lobby_task = asyncio.create_task(_lobby_countdown(bot, secs))
            await bot.highrise.chat("♻️ BlackJack table restored after restart.")
            print(f"[RECOVERY] RBJ lobby restored, countdown in {secs}s.")

        elif phase in ("round", "active"):
            _state.phase = "round"
            if _state.round_id:
                unpaid = db.get_unpaid_results("rbj", _state.round_id)
                if unpaid:
                    print(f"[RECOVERY] RBJ: completing {len(unpaid)} unpaid payouts...")
                    await _complete_unpaid_payouts(bot, "rbj", unpaid)
                    db.clear_casino_table("rbj")
                    _state.reset()
                    return

            await bot.highrise.chat("♻️ BlackJack restored. Cards, shoe, and bets loaded.")

            if _all_done():
                asyncio.create_task(_finalize_round(bot))
                return

            secs = _remaining_secs(_state._action_ends_at, default=0)
            if secs > 0:
                _state.action_task = asyncio.create_task(_action_timeout(bot, secs))
                print(f"[RECOVERY] RBJ action timer restarted: {secs}s")
            else:
                asyncio.create_task(_action_timeout(bot, 0))
            print(f"[RECOVERY] RBJ round restored. Players={len(_state.players)}")

        elif phase == "finished":
            print("[RECOVERY] RBJ phase=finished, clearing state.")
            db.clear_casino_table("rbj")

    except Exception as exc:
        print(f"[RECOVERY] RBJ recovery failed: {exc}")
        try:
            db.save_casino_table("rbj", {
                "phase": row.get("phase", "?"),
                "round_id": row.get("round_id", ""),
                "current_player_index": 0,
                "dealer_hand_json": "[]", "deck_json": "[]",
                "shoe_json": "[]", "shoe_cards_remaining": 0,
                "countdown_ends_at": "", "turn_ends_at": "",
                "active": 1, "recovery_required": 1,
            })
        except Exception:
            pass
        try:
            await bot.highrise.chat("⚠️ BlackJack (Shoe) recovery needed. Use !rbj recover or !rbj refund.")
        except Exception:
            pass


async def _complete_unpaid_payouts(bot: BaseBot, mode: str, unpaid: list) -> None:
    for row in unpaid:
        try:
            if db.is_result_paid(mode, row["round_id"], row["username"]):
                continue
            payout = int(row.get("payout", 0))
            if payout > 0:
                db.adjust_balance(row["user_id"], payout)
                db.add_ledger_entry(row["user_id"], row["username"], payout,
                                    f"{mode}_recovery_payout")
                await bot.highrise.chat(
                    f"♻️ @{row['username']} recovered {row['result']}: {payout:,}c."
                )
            db.mark_result_paid(mode, row["round_id"], row["username"])
        except Exception as exc:
            print(f"[RECOVERY] RBJ unpaid payout error for {row.get('username')}: {exc}")


# ─── Top-level router ─────────────────────────────────────────────────────────

async def handle_rbj(bot: BaseBot, user: User, args: list[str]):
    sub = args[1].lower() if len(args) > 1 else ""
    try:
        if sub == "join":
            await _cmd_join(bot, user, args)
        elif sub == "leave":
            await _cmd_leave(bot, user)
        elif sub == "players":
            await _cmd_players(bot, user)
        elif sub == "table":
            await _cmd_table(bot, user)
        elif sub == "hand":
            await _cmd_hand(bot, user)
        elif sub == "hit":
            await _cmd_hit(bot, user)
        elif sub == "stand":
            await _cmd_stand(bot, user)
        elif sub == "double":
            if len(args) > 2 and args[2].lower() in ("on", "off"):
                await _cmd_toggle_double(bot, user, args[2].lower() == "on")
            else:
                await _cmd_double(bot, user)
        elif sub == "split":
            if len(args) > 2 and args[2].lower() in ("on", "off"):
                await _cmd_toggle_split(bot, user, args[2].lower() == "on")
            else:
                await _cmd_split(bot, user)
        elif sub == "splitaces":
            if len(args) > 2 and args[2].lower() in ("on", "off"):
                await _cmd_toggle_splitaces(bot, user, args[2].lower() == "on")
            else:
                await bot.highrise.send_whisper(user.id, "Usage: !rbj splitaces on|off")
        elif sub == "betlimit":
            if len(args) > 2 and args[2].lower() in ("on", "off"):
                await _cmd_toggle_betlimit(bot, user, args[2].lower() == "on")
            else:
                await bot.highrise.send_whisper(user.id, "Usage: !rbj betlimit on|off")
        elif sub == "winlimit":
            if not can_manage_games(user.username):
                await bot.highrise.send_whisper(user.id, "Staff only.")
                return
            val = args[2].lower() if len(args) > 2 else ""
            if val == "on":
                db.set_rbj_setting("rbj_win_limit_enabled", 1)
                await bot.highrise.chat("✅ BlackJack win limit ON.")
            elif val == "off":
                db.set_rbj_setting("rbj_win_limit_enabled", 0)
                await bot.highrise.chat("⛔ BlackJack win limit OFF.")
            else:
                await bot.highrise.send_whisper(user.id, "Use !rbj winlimit on/off.")
        elif sub == "losslimit":
            if not can_manage_games(user.username):
                await bot.highrise.send_whisper(user.id, "Staff only.")
                return
            val = args[2].lower() if len(args) > 2 else ""
            if val == "on":
                db.set_rbj_setting("rbj_loss_limit_enabled", 1)
                await bot.highrise.chat("✅ BlackJack loss limit ON.")
            elif val == "off":
                db.set_rbj_setting("rbj_loss_limit_enabled", 0)
                await bot.highrise.chat("⛔ BlackJack loss limit OFF.")
            else:
                await bot.highrise.send_whisper(user.id, "Use !rbj losslimit on/off.")
        elif sub == "rules":
            await _cmd_rules(bot, user)
        elif sub == "stats":
            await _cmd_stats(bot, user)
        elif sub == "shoe":
            await _cmd_shoe(bot, user)
        elif sub == "cancel":
            await _cmd_cancel(bot, user)
        elif sub == "limits":
            await _cmd_limits(bot, user)
        elif sub == "leaderboard":
            await _cmd_leaderboard(bot, user)
        elif sub == "settings":
            await _cmd_settings_show(bot, user)
        elif sub == "on":
            await _cmd_rbj_mode(bot, user, True)
        elif sub == "off":
            await _cmd_rbj_mode(bot, user, False)
        elif sub == "state":
            await _cmd_rbj_state(bot, user)
        elif sub == "recover":
            await _cmd_rbj_recover(bot, user)
        elif sub == "refund":
            await _cmd_rbj_refund(bot, user)
        elif sub == "forcefinish":
            await _cmd_rbj_forcefinish(bot, user)
        elif sub == "integrity":
            if not can_manage_games(user.username):
                await bot.highrise.send_whisper(user.id, "Staff only.")
                return
            sub2 = args[2].lower() if len(args) > 2 else ""
            from modules.casino_integrity import run_rbj_integrity
            await run_rbj_integrity(bot, user, sub2)
        elif sub == "insurance":
            await _cmd_insurance(bot, user)
        elif sub == "surrender":
            await _cmd_surrender_rbj(bot, user)
        elif sub == "reset":
            if not can_manage_games(user.username):
                await bot.highrise.send_whisper(user.id, "Staff only.")
                return
            result = reset_table()
            await bot.highrise.chat(
                f"🃏 BlackJack table {'reset' if result == 'reset' else 'was already idle'}."
            )
        elif sub == "cards":
            val2 = args[2].lower() if len(args) > 2 else ""
            if val2 in ("whisper", "public"):
                if not can_manage_games(user.username):
                    await bot.highrise.send_whisper(user.id, "Staff only.")
                    return
                db.set_bj_setting("bj_cards_mode", val2)
                await bot.highrise.chat(f"🃏 Cards display mode set to {val2}.")
            else:
                mode = db.get_bj_settings().get("bj_cards_mode", "whisper")
                await bot.highrise.send_whisper(
                    user.id, f"Cards mode: {mode}. Use !bj cards whisper|public"
                )
        elif sub == "fairness":
            s_f = _settings()
            decks       = s_f.get("decks", 6)
            shuffle_pct = s_f.get("shuffle_used_percent", 75)
            bj_pay      = s_f.get("blackjack_payout", 2.5)
            soft17      = "hits" if int(s_f.get("dealer_hits_soft_17", 1)) else "stands"
            await bot.highrise.send_whisper(
                user.id,
                f"🃏 BlackJack Fairness\n"
                f"Shoe: {decks} decks | Reshuffle at: {shuffle_pct}%\n"
                f"BJ Payout: {bj_pay}x | Dealer {soft17} soft 17\n"
                f"Remaining: {_shoe.remaining}/{_shoe.total} cards"
            )
        elif sub == "setminbet":
            await handle_rbj_set(bot, user, "setrbjminbet", ["setrbjminbet"] + args[2:])
        elif sub == "setmaxbet":
            await handle_rbj_set(bot, user, "setrbjmaxbet", ["setrbjmaxbet"] + args[2:])
        elif sub == "setcountdown":
            await handle_rbj_set(bot, user, "setrbjcountdown", ["setrbjcountdown"] + args[2:])
        elif sub == "setactiontimer":
            await handle_rbj_set(bot, user, "setrbjactiontimer", ["setrbjactiontimer"] + args[2:])
        elif sub == "setdecks":
            await handle_rbj_set(bot, user, "setrbjdecks", ["setrbjdecks"] + args[2:])
        elif sub == "shuffleat":
            await handle_rbj_set(bot, user, "setrbjshuffle", ["setrbjshuffle"] + args[2:])
        elif sub == "setsoft17":
            if not can_manage_games(user.username):
                await bot.highrise.send_whisper(user.id, "Staff only.")
                return
            val2 = args[2].lower() if len(args) > 2 else ""
            if val2 == "hit":
                db.set_rbj_setting("dealer_hits_soft_17", 1)
                await bot.highrise.chat("✅ Dealer hits soft 17.")
            elif val2 == "stand":
                db.set_rbj_setting("dealer_hits_soft_17", 0)
                await bot.highrise.chat("✅ Dealer stands on soft 17.")
            else:
                await bot.highrise.send_whisper(user.id, "Use !bj setsoft17 hit|stand")
        elif sub == "setsurrender":
            if not can_manage_games(user.username):
                await bot.highrise.send_whisper(user.id, "Staff only.")
                return
            val2 = args[2].lower() if len(args) > 2 else ""
            if val2 in ("on", "off"):
                db.set_rbj_setting("rbj_surrender_enabled", 1 if val2 == "on" else 0)
                label = "ON" if val2 == "on" else "OFF"
                await bot.highrise.chat(
                    f"{'✅' if val2 == 'on' else '⛔'} BlackJack surrender is now {label}."
                )
            else:
                await bot.highrise.send_whisper(user.id, "Use !bj setsurrender on|off")
        elif sub == "setinsurance":
            if not can_manage_games(user.username):
                await bot.highrise.send_whisper(user.id, "Staff only.")
                return
            val2 = args[2].lower() if len(args) > 2 else ""
            if val2 in ("on", "off"):
                db.set_rbj_setting("rbj_insurance_enabled", 1 if val2 == "on" else 0)
                label = "ON" if val2 == "on" else "OFF"
                await bot.highrise.chat(
                    f"{'✅' if val2 == 'on' else '⛔'} BlackJack insurance is now {label}."
                )
            else:
                await bot.highrise.send_whisper(user.id, "Use !bj setinsurance on|off")
        elif sub == "setmaxsplits":
            await handle_rbj_set(bot, user, "setrbjmaxsplits", ["setrbjmaxsplits"] + args[2:])
        elif sub == "pace":
            await _cmd_bj_pace(bot, user, args)
        elif sub in ("forcecards", "forcedealer", "forcedealerace",
                     "forceplayerpair", "forceblackjack", "forceshoe",
                     "debug", "testcommands"):
            await _cmd_bj_force(bot, user, sub, args)
        elif sub == "watch":
            _bj_watchers[user.id] = user.username
            s_now = _settings()
            phase = _state.phase
            players_in = len(_state.players)
            await bot.highrise.send_whisper(
                user.id,
                f"👁️ BlackJack Watch ON\n"
                f"Phase: {phase} | Players: {players_in}\n"
                f"You'll get whispers of key round events.\n"
                f"!bj unwatch to stop."
            )
        elif sub == "unwatch":
            _bj_watchers.pop(user.id, None)
            await bot.highrise.send_whisper(
                user.id, "👁️ BlackJack Watch OFF"
            )
        elif sub in ("help", ""):
            await _cmd_bj_help(bot, user)
        else:
            await _cmd_bj_help(bot, user)
    except Exception as exc:
        print(f"[RBJ] /{' '.join(args)} error for {user.username}: {exc}")
        try:
            await bot.highrise.send_whisper(user.id, "BlackJack error. Try again!")
        except Exception:
            pass


# ─── Sub-command handlers ─────────────────────────────────────────────────────

async def _cmd_join(bot: BaseBot, user: User, args: list[str]):
    s = _settings()
    if not int(s.get("rbj_enabled", 1)):
        await bot.highrise.send_whisper(user.id, "BlackJack is currently closed.")
        return

    if len(args) < 3 or not args[2].isdigit() or int(args[2]) < 1:
        min_b = int(s.get("min_bet", 10))
        max_b = int(s.get("max_bet", 1000))
        await bot.highrise.send_whisper(
            user.id, f"Usage: !bet <amount>  Min: {min_b:,}c  Max: {max_b:,}c"
        )
        return

    bet     = int(args[2])
    min_bet = int(s.get("min_bet", 10))
    max_bet = int(s.get("max_bet", 1000))

    from modules.events import get_event_effect as _gee
    _ev_rbj     = _gee()
    eff_max_bet = int(max_bet * _ev_rbj["casino_bet_mult"])

    bet_limit_on = int(s.get("rbj_betlimit_enabled", 1))
    if bet_limit_on and (bet < min_bet or bet > eff_max_bet):
        note = " (Casino Hour 2x limit!)" if _ev_rbj["casino_bet_mult"] > 1 else ""
        await bot.highrise.send_whisper(
            user.id, f"Bet must be {min_bet:,}–{eff_max_bet:,} coins.{note}"
        )
        return

    if _state.phase == "round":
        await bot.highrise.send_whisper(user.id, "Round in progress. Wait for next game.")
        return
    if _state.in_game(user.id):
        await bot.highrise.send_whisper(user.id, "You're already in the lobby.")
        return

    max_players = int(s.get("max_players", 6))
    if len(_state.players) >= max_players:
        await bot.highrise.send_whisper(user.id, "Table is full.")
        return

    db.ensure_user(user.id, user.username)
    if db.get_balance(user.id) < bet:
        await bot.highrise.send_whisper(user.id, "Not enough coins.")
        return

    net      = db.get_rbj_daily_net(user.id)
    win_lim  = int(s.get("rbj_daily_win_limit", 5000))
    loss_lim = int(s.get("rbj_daily_loss_limit", 3000))
    win_on   = int(s.get("rbj_win_limit_enabled", 1))
    loss_on  = int(s.get("rbj_loss_limit_enabled", 1))
    if win_on and net >= win_lim:
        await bot.highrise.send_whisper(user.id, "BlackJack win limit reached. Try again tomorrow.")
        return
    if loss_on and net <= -loss_lim:
        await bot.highrise.send_whisper(user.id, "BlackJack loss limit reached. Try again tomorrow.")
        return
    if loss_on and max(0, -net) + bet > loss_lim:
        await bot.highrise.send_whisper(user.id, "Bet too high for your daily loss limit.")
        return

    db.adjust_balance(user.id, -bet)
    _last_rbj_bets[user.id] = bet   # store for /bet same
    p = _Player(user_id=user.id, username=user.username, bet=bet)
    _state.players.append(p)
    _save_player_state(p)
    print(f"[RBJ] @{user.username} joined with {bet:,}c")

    count   = len(_state.players)
    display = db.get_display_name(user.id, user.username)
    await bot.highrise.chat(
        f"✅ {display} joined BlackJack with {bet:,}c. Players: {count}/{max_players}"
    )

    if _state.phase == "idle":
        _state.phase = "lobby"
        countdown    = int(s.get("lobby_countdown", 15))
        _cancel_task(_state.lobby_task, "Countdown")
        _save_table_state()
        _state.lobby_task = asyncio.create_task(_lobby_countdown(bot, countdown))
        await bot.highrise.chat(
            f"🃏 BlackJack table opened! Round starts in {countdown}s.\nJoin with !bet <amount>"
        )
    else:
        _save_table_state()


async def _cmd_leave(bot: BaseBot, user: User):
    if _state.phase == "round":
        p = _state.get_player(user.id)
        if p is None:
            await bot.highrise.send_whisper(user.id, "You're not in this game.")
            return
        h = p.current_hand()
        if h is not None and h["status"] == "active":
            h["status"] = "stood"
            p.advance_hand()
            _save_player_state(p)
            _save_table_state()
            display = db.get_display_name(user.id, user.username)
            await bot.highrise.chat(f"↩️ {display} left — auto-stand.")
            await _check_and_resolve(bot)
        else:
            await bot.highrise.send_whisper(user.id, "Your hand is already resolved.")
        return
    p = _state.get_player(user.id)
    if p is None:
        await bot.highrise.send_whisper(user.id, "You're not in the BlackJack lobby.")
        return
    db.adjust_balance(user.id, p.bet)
    _state.players.remove(p)
    display = db.get_display_name(user.id, user.username)
    await bot.highrise.chat(f"↩️ {display} left BlackJack. Bet refunded.")
    if not _state.players:
        _cancel_task(_state.lobby_task, "Countdown")
        db.clear_casino_table("rbj")
        _state.reset()
        await bot.highrise.chat("BlackJack lobby closed — no players.")
    else:
        _save_table_state()


async def _cmd_players(bot: BaseBot, user: User):
    if _state.phase == "idle":
        await bot.highrise.send_whisper(user.id, "No BlackJack table active. Use !rjoin <bet>.")
        return
    lines = [f"-- BlackJack Players ({_state.phase}) --"]
    for p in _state.players:
        lines.append(f"  {_dn(p)}  {p.total_bet():,}c")
    if not _state.players:
        lines.append("  (none)")
    await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])


async def _cmd_table(bot: BaseBot, user: User):
    if _state.phase == "idle":
        await bot.highrise.send_whisper(user.id, "No BlackJack table active. Use !rjoin <bet>.")
        return
    if _state.phase == "lobby":
        count = len(_state.players)
        names = ", ".join(_dn(p) for p in _state.players) or "none"
        await bot.highrise.send_whisper(user.id,
            f"🃏 BlackJack Lobby — {count} player(s)\n{names}"[:249])
        return

    secs  = _remaining_secs(_state._action_ends_at, 0)
    dc    = card_str(_state.dealer_hand[0]) if _state.dealer_hand else "?"
    lines = [f"BlackJack | Dealer: {dc} ? | Shoe {_shoe.remaining} | {secs}s left"]
    for p in _state.players:
        if len(p.hands) == 1:
            h     = p.hands[0]
            total = hand_value(h["cards"])
            lines.append(f"{_dn(p)} {total} {h['status']}")
        else:
            parts = [
                f"H{i+1} {hand_value(h['cards'])} {h['status']}"
                for i, h in enumerate(p.hands)
            ]
            lines.append(f"{_dn(p)}: " + " | ".join(parts))
    msg = "\n".join(lines)
    await bot.highrise.send_whisper(user.id, msg[:249])


async def _cmd_hand(bot: BaseBot, user: User):
    if _state.phase != "round":
        await bot.highrise.send_whisper(user.id, "No BlackJack round active.")
        return
    p = _state.get_player(user.id)
    if p is None:
        await bot.highrise.send_whisper(user.id, "You are not in this game.")
        return
    parts = []
    for i, h in enumerate(p.hands):
        val = hand_value(h["cards"])
        st  = h["status"]
        if st == "active" and i != p.active_hand_idx:
            st = "wait"
        parts.append(f"H{i+1} {hand_str(h['cards'])}={val} {st}")
    msg = f"🃏 {' | '.join(parts)}"
    if len(msg) <= 249:
        await bot.highrise.send_whisper(user.id, msg)
    else:
        for part in parts:
            await bot.highrise.send_whisper(user.id, f"🃏 {part}"[:249])


async def _cmd_hit(bot: BaseBot, user: User):
    if _state.phase != "round":
        await bot.highrise.send_whisper(user.id, "No BlackJack round active.")
        return
    p = _state.get_player(user.id)
    if p is None:
        await bot.highrise.send_whisper(user.id, "You are not in this game.")
        return
    h = p.current_hand()
    if h is None or h["status"] != "active":
        await bot.highrise.send_whisper(user.id, "No active hand to hit.")
        return

    card  = _shoe.pop()
    h["cards"].append(card)
    total = hand_value(h["cards"])
    hidx  = p.active_hand_idx + 1
    print(f"[RBJ] @{p.username} H{hidx} hit → {card_str(card)} total={total}")
    _save_table_state()

    if total > 21:
        h["status"] = "bust"
        _save_player_state(p)
        _save_table_state()
        await bot.highrise.chat(
            f"🃏 {_dn(p)} H{hidx}: {hand_str(h['cards'])} = {total} — bust!"
        )
        p.advance_hand()
        if not await _check_and_resolve(bot):
            _save_player_state(p)
    elif total == 21:
        h["status"] = "stood"
        _save_player_state(p)
        _save_table_state()
        await bot.highrise.chat(f"🃏 {_dn(p)} H{hidx}: 21 — auto-stand!")
        p.advance_hand()
        if not await _check_and_resolve(bot):
            _save_player_state(p)
    else:
        _save_player_state(p)
        _save_table_state()
        await bot.highrise.chat(
            f"🃏 {_dn(p)} hits: {hand_str(h['cards'])} = {total}"
        )


async def _cmd_stand(bot: BaseBot, user: User):
    if _state.phase != "round":
        await bot.highrise.send_whisper(user.id, "No BlackJack round active.")
        return
    p = _state.get_player(user.id)
    if p is None:
        await bot.highrise.send_whisper(user.id, "You are not in this game.")
        return
    h = p.current_hand()
    if h is None or h["status"] != "active":
        await bot.highrise.send_whisper(user.id, "No active hand to stand.")
        return

    total       = hand_value(h["cards"])
    h["status"] = "stood"
    await bot.highrise.chat(f"✋ {_dn(p)} stands at {total}.")
    p.advance_hand()
    _save_player_state(p)
    _save_table_state()
    await _check_and_resolve(bot)


async def _cmd_double(bot: BaseBot, user: User):
    if _state.phase != "round":
        await bot.highrise.send_whisper(user.id, "No BlackJack round active.")
        return
    s = _settings()
    if not int(s.get("rbj_double_enabled", 1)):
        await bot.highrise.send_whisper(user.id, "Double is currently disabled.")
        return
    p = _state.get_player(user.id)
    if p is None:
        await bot.highrise.send_whisper(user.id, "You are not in this game.")
        return
    h = p.current_hand()
    if h is None or h["status"] != "active":
        await bot.highrise.send_whisper(user.id, "No active hand to double.")
        return
    if len(h["cards"]) != 2:
        await bot.highrise.send_whisper(user.id, "❌ Double allowed only on first 2 cards.")
        return
    if db.get_balance(user.id) < h["bet"]:
        await bot.highrise.send_whisper(user.id, "❌ Not enough coins to double.")
        return

    db.adjust_balance(user.id, -h["bet"])
    h["bet"]    *= 2
    h["doubled"] = True
    card  = _shoe.pop()
    h["cards"].append(card)
    total = hand_value(h["cards"])
    hidx  = p.active_hand_idx + 1
    print(f"[RBJ] @{p.username} H{hidx} doubled total={total}")

    if total > 21:
        h["status"] = "bust"
        await bot.highrise.chat(
            f"💰 {_dn(p)} doubles to {h['bet']:,}c, "
            f"draws {card_str(card)}. H{hidx}: {total} — bust!"
        )
    else:
        h["status"] = "stood"
        await bot.highrise.chat(
            f"💰 {_dn(p)} doubles to {h['bet']:,}c, "
            f"draws {card_str(card)}. H{hidx}: {total}."
        )

    p.advance_hand()
    _save_player_state(p)
    _save_table_state()
    await _check_and_resolve(bot)


async def _cmd_split(bot: BaseBot, user: User):
    if _state.phase != "round":
        await bot.highrise.send_whisper(user.id, "No BlackJack round active.")
        return
    s = _settings()
    if not int(s.get("rbj_split_enabled", 1)):
        await bot.highrise.send_whisper(user.id, "Split is currently disabled.")
        return
    p = _state.get_player(user.id)
    if p is None:
        await bot.highrise.send_whisper(user.id, "You are not in this game.")
        return
    h = p.current_hand()
    if h is None or h["status"] != "active":
        await bot.highrise.send_whisper(user.id, "No active hand to split.")
        return
    if len(h["cards"]) != 2:
        await bot.highrise.send_whisper(user.id, "❌ Split needs exactly 2 cards.")
        return

    max_splits = int(s.get("rbj_max_splits", 1))
    if p.split_count >= max_splits:
        await bot.highrise.send_whisper(user.id, f"❌ Max splits ({max_splits}) reached.")
        return

    r1, r2 = h["cards"][0][0], h["cards"][1][0]
    if r1 != r2:
        await bot.highrise.send_whisper(user.id, "❌ Split needs two matching ranks.")
        return
    if db.get_balance(user.id) < h["bet"]:
        await bot.highrise.send_whisper(user.id, "❌ Not enough coins to split.")
        return

    split_bet = h["bet"]
    db.adjust_balance(user.id, -split_bet)

    card_a, card_b = h["cards"][0], h["cards"][1]
    idx            = p.active_hand_idx
    p.hands.pop(idx)

    hand_a = _make_hand(split_bet, from_split=True)
    hand_a["cards"].append(card_a)
    hand_b = _make_hand(split_bet, from_split=True)
    hand_b["cards"].append(card_b)
    p.hands.insert(idx, hand_b)
    p.hands.insert(idx, hand_a)
    p.split_count += 1

    # Deal one new card to each split hand; save shoe after each pop
    new_card_a = _shoe.pop()
    p.hands[idx]["cards"].append(new_card_a)
    _save_player_state(p)
    _save_table_state()                  # shoe saved after first card

    new_card_b = _shoe.pop()
    p.hands[idx + 1]["cards"].append(new_card_b)

    split_aces = int(s.get("rbj_split_aces_one_card", 1))
    is_aces    = (r1 == "A")

    val_a   = hand_value(p.hands[idx]["cards"])
    val_b   = hand_value(p.hands[idx + 1]["cards"])
    cards_a = hand_str(p.hands[idx]["cards"])
    cards_b = hand_str(p.hands[idx + 1]["cards"])

    if is_aces and split_aces:
        p.hands[idx]["status"]     = "stood"
        p.hands[idx + 1]["status"] = "stood"
        p.active_hand_idx = idx + 2
        _save_player_state(p)
        _save_table_state()              # shoe saved after second card
        await bot.highrise.chat(f"✂️ {_dn(p)} splits Aces. One card each.")
        h1_str = f"H{idx+1}: {cards_a}={val_a} stood"
        h2_str = f"H{idx+2}: {cards_b}={val_b} stood"
        line   = f"{h1_str} | {h2_str}"
        if len(line) > 240:
            await bot.highrise.chat(h1_str[:249])
            await bot.highrise.chat(h2_str[:249])
        else:
            await bot.highrise.chat(line[:249])
    else:
        p.active_hand_idx = idx
        # Auto-stand if either split hand hits 21 on deal
        if val_a == 21:
            p.hands[idx]["status"] = "stood"
            p.advance_hand()
        if val_b == 21:
            p.hands[idx + 1]["status"] = "stood"
        stat_a = "stood" if val_a == 21 else "active"
        stat_b = "stood" if val_b == 21 else "wait"
        _save_player_state(p)
        _save_table_state()              # shoe saved after second card
        await bot.highrise.chat(f"✂️ {_dn(p)} splits {r1}s.")
        h1_str = f"H{idx+1}: {cards_a}={val_a} {stat_a}"
        h2_str = f"H{idx+2}: {cards_b}={val_b} {stat_b}"
        line   = f"{h1_str} | {h2_str}"
        if len(line) > 240:
            await bot.highrise.chat(h1_str[:249])
            await bot.highrise.chat(h2_str[:249])
        else:
            await bot.highrise.chat(line[:249])

    await _check_and_resolve(bot)


async def _cmd_toggle_double(bot: BaseBot, user: User, enabled: bool):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return
    db.set_rbj_setting("rbj_double_enabled", 1 if enabled else 0)
    await bot.highrise.send_whisper(user.id,
        f"✅ BlackJack double is now {'ON' if enabled else 'OFF'}.")


async def _cmd_toggle_split(bot: BaseBot, user: User, enabled: bool):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return
    db.set_rbj_setting("rbj_split_enabled", 1 if enabled else 0)
    await bot.highrise.send_whisper(user.id,
        f"✅ BlackJack split is now {'ON' if enabled else 'OFF'}.")


async def _cmd_toggle_splitaces(bot: BaseBot, user: User, enabled: bool):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return
    db.set_rbj_setting("rbj_split_aces_one_card", 1 if enabled else 0)
    await bot.highrise.send_whisper(user.id,
        f"✅ BlackJack split aces one-card rule is now {'ON' if enabled else 'OFF'}.")


async def _cmd_toggle_betlimit(bot: BaseBot, user: User, enabled: bool):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return
    db.set_rbj_setting("rbj_betlimit_enabled", 1 if enabled else 0)
    if enabled:
        await bot.highrise.chat("✅ BlackJack bet limit ON.")
    else:
        await bot.highrise.chat("⛔ BlackJack bet limit OFF.")


async def _cmd_rules(bot: BaseBot, user: User):
    s = _settings()
    await bot.highrise.send_whisper(user.id,
        f"🃏 BlackJack Rules\n"
        f"Shoe: {s.get('decks',6)} decks  Reshuffle: {s.get('shuffle_used_percent',75)}%\n"
        f"Bet: {s.get('min_bet',10):,}–{s.get('max_bet',1000):,}c\n"
        f"Win: {s.get('win_payout',2.0)}x  BJ: {s.get('blackjack_payout',2.5)}x\n"
        f"Push: {s.get('push_rule','refund')}  "
        f"Soft17: {'hit' if s.get('dealer_hits_soft_17',1) else 'stand'}\n"
        f"Timer: {s.get('rbj_action_timer',30)}s  "
        f"Double: {'ON' if int(s.get('rbj_double_enabled',1)) else 'OFF'}  "
        f"Split: {'ON' if int(s.get('rbj_split_enabled',1)) else 'OFF'}"
    )


async def _cmd_stats(bot: BaseBot, user: User):
    db.ensure_user(user.id, user.username)
    s = db.get_rbj_stats(user.id)
    await bot.highrise.send_whisper(user.id,
        f"-- {user.username} BlackJack Stats --\n"
        f"W:{s['rbj_wins']} L:{s['rbj_losses']} "
        f"P:{s['rbj_pushes']} BJ:{s['rbj_blackjacks']}\n"
        f"Bet:{s['rbj_total_bet']:,}c  "
        f"Won:{s['rbj_total_won']:,}c  "
        f"Lost:{s['rbj_total_lost']:,}c"
    )


async def _cmd_shoe(bot: BaseBot, user: User):
    s         = _settings()
    threshold = float(s.get("shuffle_used_percent", 75))
    pct       = round(_shoe.used_pct, 1)
    soon      = _shoe.needs_shuffle(threshold)
    await bot.highrise.send_whisper(user.id,
        f"-- BlackJack Shoe --\n"
        f"Cards left: {_shoe.remaining}/{_shoe.total}\n"
        f"Decks used: {_shoe.decks_used}  Shuffle at: {threshold}%\n"
        f"Used: {pct}%  {'⚠️ Reshuffle coming!' if soon else '✅ Shoe fresh'}"
    )


async def _cmd_limits(bot: BaseBot, user: User):
    db.ensure_user(user.id, user.username)
    s    = _settings()
    net  = db.get_rbj_daily_net(user.id)
    wlim = int(s.get("rbj_daily_win_limit", 5000))
    llim = int(s.get("rbj_daily_loss_limit", 3000))
    won  = "ON" if int(s.get("rbj_win_limit_enabled", 1)) else "OFF"
    lon  = "ON" if int(s.get("rbj_loss_limit_enabled", 1)) else "OFF"
    blon = int(s.get("rbj_betlimit_enabled", 1))
    sign = "+" if net >= 0 else ""
    if blon:
        bet_str = f"BlackJack bet {s.get('min_bet',10):,}–{s.get('max_bet',1000):,}c ON"
    else:
        bet_str = "BlackJack bet limit OFF"
    await bot.highrise.send_whisper(user.id,
        f"{bet_str} | W/L {wlim:,}/{llim:,} {won}/{lon}\n"
        f"Today: {sign}{net:,}c"
    )


async def _cmd_leaderboard(bot: BaseBot, user: User):
    rows = db.get_rbj_leaderboard()
    if not rows:
        await bot.highrise.send_whisper(user.id, "No BlackJack stats yet. Play some games!")
        return
    lines = ["-- BlackJack Top 5 (Net Profit) --"]
    for i, r in enumerate(rows, 1):
        name = db.get_display_name(r["user_id"], r["username"])
        net  = r["net"]
        sign = "+" if net >= 0 else ""
        lines.append(f"{i}. {name}  {sign}{net:,}c")
    await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])


async def _cmd_cancel(bot: BaseBot, user: User):
    if not can_moderate(user.username):
        await bot.highrise.send_whisper(user.id, "Staff only.")
        return
    if _state.phase == "idle":
        await bot.highrise.send_whisper(user.id, "No active BlackJack game to cancel.")
        return
    for p in _state.players:
        refund = p.total_bet()
        db.adjust_balance(p.user_id, refund)
        db.add_ledger_entry(p.user_id, p.username, refund, "rbj_cancel_refund")
    _cancel_task(_state.lobby_task, "Countdown")
    _cancel_task(_state.action_task, "Action timer")
    db.clear_casino_table("rbj")
    _state.reset()
    await bot.highrise.chat("🃏 BlackJack cancelled. All bets refunded.")


async def _cmd_settings_show(bot: BaseBot, user: User):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Admins and managers only.")
        return
    s       = _settings()
    enabled = "ON" if int(s.get("rbj_enabled", 1)) else "OFF"
    dbl     = "ON" if int(s.get("rbj_double_enabled", 1)) else "OFF"
    spl     = "ON" if int(s.get("rbj_split_enabled", 1)) else "OFF"
    win_on  = "ON" if int(s.get("rbj_win_limit_enabled", 1)) else "OFF"
    loss_on = "ON" if int(s.get("rbj_loss_limit_enabled", 1)) else "OFF"
    await bot.highrise.send_whisper(user.id,
        f"-- BlackJack Settings --\n"
        f"BlackJack {enabled} | Timer {s.get('rbj_action_timer',30)}s | "
        f"Double {dbl} | Split {spl} | MaxSplits {s.get('rbj_max_splits',1)}\n"
        f"decks:{s.get('decks',6)}  shuffle:{s.get('shuffle_used_percent',75)}%  "
        f"shoe:{_shoe.remaining}/{_shoe.total}\n"
        f"min:{s.get('min_bet',10):,}c  max:{s.get('max_bet',1000):,}c\n"
        f"win:{s.get('win_payout',2.0)}x  bj:{s.get('blackjack_payout',2.5)}x\n"
        f"W/L limit: {win_on}/{loss_on}"
    )


async def _cmd_rbj_mode(bot: BaseBot, user: User, enabled: bool):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Admins and managers only.")
        return
    db.set_rbj_setting("rbj_enabled", 1 if enabled else 0)
    status = "ON" if enabled else "OFF"
    await bot.highrise.chat(f"{'✅' if enabled else '⛔'} BlackJack (Shoe) is now {status}.")


# ─── Recovery staff commands ──────────────────────────────────────────────────

async def _cmd_rbj_state(bot: BaseBot, user: User):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return
    if _state.phase == "idle":
        row = db.load_casino_table("rbj")
        if row and row.get("active"):
            await bot.highrise.send_whisper(user.id,
                f"BJ Shoe: idle in memory | DB phase:{row.get('phase')}\n"
                "Use !rbj recover or !rbj refund.")
        else:
            await bot.highrise.send_whisper(user.id, "BJ Shoe: no active table.")
        return

    total_bets = sum(p.total_bet() for p in _state.players)
    active_ps  = [p for p in _state.players if not p.is_done()]
    dc         = card_str(_state.dealer_hand[0]) if _state.dealer_hand else "?"
    secs       = _remaining_secs(_state._action_ends_at, 0)
    rid        = _state.round_id[-10:] if _state.round_id else "?"
    msg = (
        f"BJ Shoe {_state.phase} | Players:{len(_state.players)}\n"
        f"Active:{len(active_ps)} | Timer:{secs}s | Dealer:{dc}\n"
        f"Bets:{total_bets:,}c | Shoe:{_shoe.remaining} | id:{rid}"
    )
    await bot.highrise.send_whisper(user.id, msg[:249])


async def _cmd_rbj_recover(bot: BaseBot, user: User):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return
    if _state.phase != "idle":
        await bot.highrise.send_whisper(user.id,
            "BJ Shoe is active. Use !rbj state to inspect, !rbj refund to cancel.")
        return
    row = db.load_casino_table("rbj")
    if not row or not row.get("active"):
        await bot.highrise.send_whisper(user.id, "No saved BJ Shoe state found.")
        return
    await bot.highrise.send_whisper(user.id, "♻️ Attempting BJ Shoe recovery...")
    try:
        db.save_casino_table("rbj", {**dict(row), "recovery_required": 0})
    except Exception:
        pass
    await startup_rbj_recovery(bot)
    await bot.highrise.send_whisper(user.id, "♻️ BJ Shoe recovery attempted. Check !rbj state.")


async def _cmd_rbj_refund(bot: BaseBot, user: User):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return

    refunded = 0
    for p in list(_state.players):
        try:
            refund = p.total_bet()
            db.adjust_balance(p.user_id, refund)
            db.add_ledger_entry(p.user_id, p.username, refund, "rbj_recovery_refund")
            refunded += refund
        except Exception as exc:
            print(f"[RBJ] refund error for {p.username}: {exc}")

    if _state.phase == "idle":
        for pd in db.load_casino_players("rbj"):
            try:
                db.adjust_balance(pd["user_id"], int(pd["bet"]))
                db.add_ledger_entry(pd["user_id"], pd["username"],
                                    int(pd["bet"]), "rbj_recovery_refund")
                refunded += int(pd["bet"])
            except Exception as exc:
                print(f"[RBJ] DB refund error for {pd.get('username')}: {exc}")

    _cancel_task(_state.lobby_task, "Countdown")
    _cancel_task(_state.action_task, "Action timer")
    db.clear_casino_table("rbj")
    _state.reset()
    print(f"[RBJ] /rbj refund: {refunded:,}c total")
    await bot.highrise.chat(f"♻️ RBJ refunded. Total returned: {refunded:,}c.")
    await bot.highrise.send_whisper(user.id, f"✅ RBJ cleared. {refunded:,}c refunded.")


async def _cmd_rbj_forcefinish(bot: BaseBot, user: User):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return
    if _state.phase == "round":
        await bot.highrise.send_whisper(user.id, "♻️ Forcing RBJ dealer resolution...")
        asyncio.create_task(_finalize_round(bot))
        return
    row = db.load_casino_table("rbj")
    if not row or not row.get("active"):
        await bot.highrise.send_whisper(user.id, "No active RBJ state. Use !rbj refund instead.")
        return
    await bot.highrise.send_whisper(user.id, "♻️ Loading RBJ state for force-finish...")
    try:
        db.save_casino_table("rbj", {**dict(row), "recovery_required": 0})
    except Exception:
        pass
    await startup_rbj_recovery(bot)
    if _state.phase == "round":
        asyncio.create_task(_finalize_round(bot))
    else:
        await bot.highrise.send_whisper(user.id, "Could not restore state. Use !rbj refund.")


# ─── Admin setting commands (/setrbjXXX) ──────────────────────────────────────

async def _cmd_bj_pace(bot: BaseBot, user: User, args: list[str]) -> None:
    """/bj pace [fast|normal|long] — Manager+; view or change BJ speed preset."""
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Manager+ only.")
        return

    pace  = args[2].lower() if len(args) > 2 else ""
    s     = _settings()
    cd    = int(s.get("lobby_countdown", 15))
    at    = int(s.get("rbj_action_timer", 30))

    if not pace:
        label = "Custom"
        for k, v in _BJ_PACE_PRESETS.items():
            if v["lobby_countdown"] == cd and v["rbj_action_timer"] == at:
                label = k.title()
                break
        await bot.highrise.send_whisper(
            user.id,
            f"⏱️ BlackJack Pace\nCurrent: {label}\nBetting countdown: {cd}s\nAction timer: {at}s"
        )
        return

    if pace not in _BJ_PACE_PRESETS:
        await bot.highrise.send_whisper(user.id, "Usage: !bj pace fast|normal|long")
        return

    preset = _BJ_PACE_PRESETS[pace]
    db.set_rbj_setting("lobby_countdown",  preset["lobby_countdown"])
    db.set_rbj_setting("rbj_action_timer", preset["rbj_action_timer"])
    await bot.highrise.chat(
        f"⏱️ BlackJack Pace: {pace.title()}\n"
        f"Betting countdown: {preset['lobby_countdown']}s\n"
        f"Action timer: {preset['rbj_action_timer']}s"
    )


async def _cmd_bj_force(bot: BaseBot, user: User, sub: str, args: list[str]) -> None:
    """Owner-only debug/force commands for BlackJack testing."""
    if not is_owner(user.username):
        return   # silently ignore for non-owners

    global _force_state

    if sub in ("debug", "testcommands"):
        await bot.highrise.send_whisper(
            user.id,
            "🧪 BJ Debug Commands\n"
            "!bj forcecards @Player A♠ K♠\n"
            "!bj forcedealer A♣ K♦\n"
            "!bj forcedealerace\n"
            "!bj forceplayerpair [@Player]\n"
            "!bj forceblackjack [@Player]\n"
            "!bj forceshoe low"
        )
        return

    if sub == "forcecards":
        if len(args) < 5:
            await bot.highrise.send_whisper(
                user.id, "Usage: !bj forcecards @Player A♠ K♠"
            )
            return
        target = args[2].lstrip("@").lower()
        c1 = _parse_card(args[3])
        c2 = _parse_card(args[4])
        if not c1 or not c2:
            await bot.highrise.send_whisper(
                user.id, "🧪 Invalid card. Example: !bj forcecards @Player A♠ K♠"
            )
            return
        _force_state["player_cards"][target] = (c1, c2)
        await bot.highrise.send_whisper(
            user.id,
            f"🧪 Force set: @{target} → {card_str(c1)} {card_str(c2)} next deal."
        )

    elif sub == "forcedealer":
        if len(args) < 4:
            await bot.highrise.send_whisper(user.id, "Usage: !bj forcedealer A♣ K♦")
            return
        c1 = _parse_card(args[2])
        c2 = _parse_card(args[3])
        if not c1 or not c2:
            await bot.highrise.send_whisper(
                user.id, "🧪 Invalid card. Example: !bj forcedealer A♣ K♦"
            )
            return
        _force_state["dealer_cards"] = (c1, c2)
        await bot.highrise.send_whisper(
            user.id, f"🧪 Dealer forced: {card_str(c1)} [hidden] next deal."
        )

    elif sub == "forcedealerace":
        _force_state["dealer_ace"]   = True
        _force_state["dealer_cards"] = None
        await bot.highrise.send_whisper(
            user.id, "🧪 Dealer Ace forced for next round."
        )

    elif sub == "forceplayerpair":
        target = args[2].lstrip("@").lower() if len(args) > 2 else user.username.lower()
        _force_state["player_pair"] = target
        _force_state["player_bj"]   = None
        await bot.highrise.send_whisper(
            user.id, f"🧪 Pair forced for @{target} next round."
        )

    elif sub == "forceblackjack":
        target = args[2].lstrip("@").lower() if len(args) > 2 else user.username.lower()
        _force_state["player_bj"]   = target
        _force_state["player_pair"] = None
        await bot.highrise.send_whisper(
            user.id, f"🧪 Natural BlackJack forced for @{target} next round."
        )

    elif sub == "forceshoe":
        val = args[2].lower() if len(args) > 2 else ""
        if val == "low":
            _force_state["shoe_low"] = True
            await bot.highrise.send_whisper(
                user.id, "🧪 Shoe will drain near shuffle threshold before next round."
            )
        else:
            await bot.highrise.send_whisper(user.id, "Usage: !bj forceshoe low")


async def handle_rbj_set(bot: BaseBot, user: User, cmd: str, args: list[str]):
    try:
        if len(args) < 2:
            await bot.highrise.send_whisper(user.id, f"Usage: /{cmd} <value>")
            return

        raw = args[1]

        if cmd == "setrbjdecks":
            if not raw.isdigit() or not (1 <= int(raw) <= 8):
                await bot.highrise.send_whisper(user.id, "Decks must be 1–8.")
                return
            db.set_rbj_setting("decks", int(raw))
            await bot.highrise.send_whisper(user.id,
                f"✅ RBJ decks set to {raw}. Takes effect on next reshuffle.")

        elif cmd == "setrbjminbet":
            if not raw.isdigit() or int(raw) < 1:
                await bot.highrise.send_whisper(user.id, "Min bet must be >= 1.")
                return
            val = int(raw)
            if val >= int(_settings().get("max_bet", 1000)):
                await bot.highrise.send_whisper(user.id, "Min bet must be less than max bet.")
                return
            db.set_rbj_setting("min_bet", val)
            await bot.highrise.send_whisper(user.id, f"✅ RBJ min bet set to {val:,}c.")

        elif cmd == "setrbjmaxbet":
            if not raw.isdigit() or int(raw) < 1:
                await bot.highrise.send_whisper(user.id, "Max bet must be >= 1.")
                return
            val = int(raw)
            if val <= int(_settings().get("min_bet", 10)):
                await bot.highrise.send_whisper(user.id, "Max bet must be greater than min bet.")
                return
            db.set_rbj_setting("max_bet", val)
            await bot.highrise.send_whisper(user.id, f"✅ RBJ max bet set to {val:,}c.")

        elif cmd == "setrbjshuffle":
            if not raw.isdigit() or not (50 <= int(raw) <= 95):
                await bot.highrise.send_whisper(user.id, "Shuffle percent must be 50–95.")
                return
            db.set_rbj_setting("shuffle_used_percent", int(raw))
            await bot.highrise.send_whisper(user.id, f"✅ RBJ shuffle threshold set to {raw}%.")

        elif cmd == "setrbjblackjackpayout":
            try:
                val = float(raw)
            except ValueError:
                await bot.highrise.send_whisper(user.id, "Payout must be a number (e.g. 2.5).")
                return
            if not (1.0 <= val <= 5.0):
                await bot.highrise.send_whisper(user.id, "BJ payout must be 1.0–5.0.")
                return
            db.set_rbj_setting("blackjack_payout", val)
            await bot.highrise.send_whisper(user.id, f"✅ RBJ blackjack payout set to {val}x.")

        elif cmd == "setrbjwinpayout":
            try:
                val = float(raw)
            except ValueError:
                await bot.highrise.send_whisper(user.id, "Payout must be a number (e.g. 2.0).")
                return
            if not (1.0 <= val <= 5.0):
                await bot.highrise.send_whisper(user.id, "Win payout must be 1.0–5.0.")
                return
            db.set_rbj_setting("win_payout", val)
            await bot.highrise.send_whisper(user.id, f"✅ RBJ win payout set to {val}x.")

        elif cmd == "setrbjcountdown":
            if not raw.isdigit() or not (5 <= int(raw) <= 120):
                await bot.highrise.send_whisper(user.id, "Countdown must be 5–120 seconds.")
                return
            db.set_rbj_setting("lobby_countdown", int(raw))
            await bot.highrise.send_whisper(user.id, f"✅ RBJ lobby countdown set to {raw}s.")

        elif cmd == "setrbjturntimer":
            if not raw.isdigit() or not (10 <= int(raw) <= 60):
                await bot.highrise.send_whisper(user.id, "Turn timer must be 10–60 seconds.")
                return
            db.set_rbj_setting("rbj_turn_timer", int(raw))
            await bot.highrise.send_whisper(user.id, f"✅ RBJ turn timer set to {raw}s.")

        elif cmd == "setrbjactiontimer":
            if not raw.isdigit() or not (10 <= int(raw) <= 90):
                await bot.highrise.send_whisper(user.id, "Action timer must be 10–90 seconds.")
                return
            db.set_rbj_setting("rbj_action_timer", int(raw))
            await bot.highrise.send_whisper(user.id, f"✅ RBJ action timer set to {raw}s.")

        elif cmd == "setrbjmaxsplits":
            if not raw.isdigit() or not (0 <= int(raw) <= 3):
                await bot.highrise.send_whisper(user.id, "Max splits must be 0–3.")
                return
            db.set_rbj_setting("rbj_max_splits", int(raw))
            await bot.highrise.send_whisper(user.id, f"✅ RBJ max splits set to {raw}.")

        elif cmd == "setrbjdailywinlimit":
            if not raw.isdigit() or not (100 <= int(raw) <= 1_000_000):
                await bot.highrise.send_whisper(user.id, "Use !setrbjdailywinlimit <amount>.")
                return
            db.set_rbj_setting("rbj_daily_win_limit", int(raw))
            await bot.highrise.send_whisper(user.id, f"✅ RBJ daily win limit set to {int(raw):,}c.")

        elif cmd == "setrbjdailylosslimit":
            if not raw.isdigit() or not (100 <= int(raw) <= 1_000_000):
                await bot.highrise.send_whisper(user.id, "Use !setrbjdailylosslimit <amount>.")
                return
            db.set_rbj_setting("rbj_daily_loss_limit", int(raw))
            await bot.highrise.send_whisper(user.id, f"✅ RBJ daily loss limit set to {int(raw):,}c.")

        else:
            await bot.highrise.send_whisper(
                user.id,
                "RBJ settings: !setrbjdecks !setrbjminbet !setrbjmaxbet\n"
                "!setrbjshuffle !setrbjblackjackpayout !setrbjwinpayout\n"
                "!setrbjcountdown !setrbjactiontimer !setrbjmaxsplits\n"
                "!setrbjdailywinlimit !setrbjdailylosslimit"
            )

    except Exception as exc:
        print(f"[RBJ] {cmd} error for {user.username}: {exc}")
        try:
            await bot.highrise.send_whisper(user.id, "Setting update failed. Try again!")
        except Exception:
            pass


# ─── Insurance ─────────────────────────────────────────────────────────────────

async def _cmd_insurance(bot: BaseBot, user: User):
    s = _settings()
    if not int(s.get("rbj_insurance_enabled", 1)):
        await bot.highrise.send_whisper(user.id, "🛡️ Insurance is currently disabled.")
        return
    if _state.phase != "round":
        await bot.highrise.send_whisper(user.id, "No BlackJack round active.")
        return
    p = _state.get_player(user.id)
    if p is None:
        await bot.highrise.send_whisper(user.id, "You are not in this game.")
        return
    if not _state.dealer_hand or _state.dealer_hand[0][0] != "A":
        await bot.highrise.send_whisper(
            user.id, "🛡️ Insurance only available when dealer shows an Ace."
        )
        return
    h = p.current_hand()
    if h is None or len(h["cards"]) != 2 or p.split_count > 0:
        await bot.highrise.send_whisper(
            user.id, "🛡️ Insurance must be taken before your first action."
        )
        return
    if p.insurance_taken:
        await bot.highrise.send_whisper(user.id, "🛡️ You already have insurance.")
        return
    insurance_bet = max(1, h["bet"] // 2)
    if db.get_balance(user.id) < insurance_bet:
        await bot.highrise.send_whisper(
            user.id, f"❌ Not enough coins for insurance ({insurance_bet:,}c)."
        )
        return
    db.adjust_balance(user.id, -insurance_bet)
    db.add_ledger_entry(user.id, user.username, -insurance_bet, "insurance_bet")
    p.insurance_bet      = insurance_bet
    p.insurance_taken    = True
    p.insurance_resolved = False
    _save_player_state(p)
    _save_table_state()
    await bot.highrise.send_whisper(
        user.id,
        f"🛡️ Insurance placed: {insurance_bet:,}c. Pays 2:1 if dealer has BlackJack."
    )


# ─── Surrender (RBJ) ───────────────────────────────────────────────────────────

async def _cmd_surrender_rbj(bot: BaseBot, user: User):
    s = _settings()
    if not int(s.get("rbj_surrender_enabled", 1)):
        await bot.highrise.send_whisper(user.id, "🏳️ Surrender is currently disabled.")
        return
    if _state.phase != "round":
        await bot.highrise.send_whisper(user.id, "No BlackJack round active.")
        return
    p = _state.get_player(user.id)
    if p is None:
        await bot.highrise.send_whisper(user.id, "You are not in this game.")
        return
    h = p.current_hand()
    if h is None or h["status"] != "active":
        await bot.highrise.send_whisper(user.id, "No active hand to surrender.")
        return
    if len(h["cards"]) != 2 or p.split_count > 0:
        await bot.highrise.send_whisper(
            user.id, "🏳️ Surrender only on first 2 cards (no split hands)."
        )
        return
    refund = h["bet"] // 2
    h["status"] = "surrendered"
    db.adjust_balance(p.user_id, refund)
    db.add_ledger_entry(p.user_id, p.username, refund, "bj_surrender_refund")
    _save_player_state(p)
    _save_table_state()
    display = db.get_display_name(user.id, user.username)
    await bot.highrise.chat(
        f"🏳️ {display} surrenders. {refund:,}c returned."[:249]
    )
    p.advance_hand()
    _save_player_state(p)
    await _check_and_resolve(bot)


# ─── Help ──────────────────────────────────────────────────────────────────────

async def _cmd_bj_help(bot: BaseBot, user: User):
    s     = _settings()
    min_b = int(s.get("min_bet", 10))
    max_b = int(s.get("max_bet", 1000))
    await bot.highrise.send_whisper(
        user.id,
        f"🃏 BlackJack (Casino Shoe)\n"
        f"Bet: {min_b:,}–{max_b:,}c\n"
        f"!bet <amount> — join or update bet\n"
        f"🃏 !hit  🛑 !stand or !stay\n"
        f"💰 !double  ✂️ !split\n"
        f"🛡️ !insurance  🏳️ !surrender\n"
        f"!bj rules  !bj stats  !bj shoe"
    )


# ─── Primary join command (/bet <amount>) ──────────────────────────────────────

async def handle_bet(bot: BaseBot, user: User, args: list) -> None:
    """Primary join/bet-update command: /bet <amount|same|repeat>"""
    s = _settings()
    if not int(s.get("rbj_enabled", 1)):
        await bot.highrise.send_whisper(user.id, "🃏 BlackJack is currently closed.")
        return

    raw = args[1] if len(args) >= 2 else ""

    # /bet same or /bet repeat — reuse last bet
    if raw.lower() in ("same", "repeat"):
        last = _last_rbj_bets.get(user.id)
        if not last:
            await bot.highrise.send_whisper(
                user.id,
                "🃏 No previous bet found.\n"
                "Use !bet <amount> to place your first bet."
            )
            return
        raw = str(last)

    if not raw.isdigit() or int(raw) < 1:
        min_b = int(s.get("min_bet", 10))
        max_b = int(s.get("max_bet", 1000))
        await bot.highrise.send_whisper(
            user.id,
            f"🃏 BlackJack\nUsage: !bet <amount>\nMin: {min_b:,}c  Max: {max_b:,}c"
        )
        return

    bet = int(raw)

    # Round active + player already in → reject
    if _state.phase == "round" and _state.get_player(user.id) is not None:
        await bot.highrise.send_whisper(
            user.id,
            "🃏 BlackJack\nYou already have an active hand.\n"
            "Finish this round before changing your bet."
        )
        return

    # Round active + player NOT in → wait message
    if _state.phase == "round" and _state.get_player(user.id) is None:
        await bot.highrise.send_whisper(
            user.id, "Round in progress. Wait for next game, then !bet <amount>."
        )
        return

    # Lobby + already joined → update bet
    if _state.phase == "lobby" and _state.get_player(user.id) is not None:
        p       = _state.get_player(user.id)
        min_bet = int(s.get("min_bet", 10))
        max_bet = int(s.get("max_bet", 1000))
        from modules.events import get_event_effect as _gee
        _ev      = _gee()
        eff_max  = int(max_bet * _ev["casino_bet_mult"])
        bet_limit_on = int(s.get("rbj_betlimit_enabled", 1))
        if bet_limit_on and (bet < min_bet or bet > eff_max):
            note = " (Casino Hour 2x limit!)" if _ev["casino_bet_mult"] > 1 else ""
            await bot.highrise.send_whisper(
                user.id, f"Bet must be {min_bet:,}–{eff_max:,} coins.{note}"
            )
            return
        db.ensure_user(user.id, user.username)
        bal = db.get_balance(user.id)
        # Old bet already deducted from balance; net available = bal + old_bet
        if bal + p.bet < bet:
            await bot.highrise.send_whisper(
                user.id,
                f"❌ Not enough coins. Balance: {bal:,}c + current bet: {p.bet:,}c."
            )
            return
        db.adjust_balance(user.id, p.bet)   # refund old bet
        db.adjust_balance(user.id, -bet)    # charge new bet
        old_bet = p.bet
        p.bet   = bet
        _save_player_state(p)
        _save_table_state()
        await bot.highrise.send_whisper(
            user.id, f"✅ Bet updated: {old_bet:,}c → {bet:,}c."
        )
        return

    # New join (idle or lobby with new player)
    await _cmd_join(bot, user, ["bet", "join", raw])


# ─── Top-level action wrappers ─────────────────────────────────────────────────

async def handle_hit(bot: BaseBot, user: User) -> None:
    await _cmd_hit(bot, user)

async def handle_stand(bot: BaseBot, user: User) -> None:
    await _cmd_stand(bot, user)

async def handle_double(bot: BaseBot, user: User) -> None:
    await _cmd_double(bot, user)

async def handle_split(bot: BaseBot, user: User) -> None:
    await _cmd_split(bot, user)

async def handle_insurance(bot: BaseBot, user: User) -> None:
    await _cmd_insurance(bot, user)

async def handle_surrender(bot: BaseBot, user: User) -> None:
    await _cmd_surrender_rbj(bot, user)


async def handle_bjstatus(bot: BaseBot, user: User) -> None:
    """!bjstatus — show the player's current blackjack hand."""
    p = _state.get_player(user.id)
    if p is None or _state.phase not in ("round", "lobby"):
        await bot.highrise.send_whisper(
            user.id,
            "🃏 Blackjack Status\n"
            "No active hand.\n"
            "Start with !bet [amount]."
        )
        return
    hand = p.current_hand()
    if not hand or not hand.get("cards"):
        await bot.highrise.send_whisper(
            user.id,
            "🃏 Blackjack Status\n"
            "No active hand.\n"
            "Start with !bet [amount]."
        )
        return
    hv        = hand_value(hand["cards"])
    h_str     = hand_str(hand["cards"])
    bet       = p.total_bet()
    dealer_up = card_str(_state.dealer_hand[0]) if _state.dealer_hand else "?"
    msg = (
        f"🃏 BJ Status\n"
        f"Bet: {bet:,}c\n"
        f"You: {h_str} = {hv}\n"
        f"Dealer: {dealer_up}\n"
        f"Use !hit or !stand."
    )
    await bot.highrise.send_whisper(user.id, msg[:249])
