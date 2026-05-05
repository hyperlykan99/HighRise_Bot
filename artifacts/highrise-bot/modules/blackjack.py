"""
modules/blackjack.py
--------------------
Casual lobby-style blackjack for the Highrise Mini Game Bot.

State lifecycle:
  idle ──► lobby (first /bj join) ──► round (countdown ends) ──► idle (round ends)

Public:  /bj join <bet>  /bj leave  /bj players  /bj table
         /bj hit  /bj stand  /bj double  /bj rules  /bj stats
Manager: /bj on  /bj off  /bj cancel  /bj settings
Admin:   /setbjminbet  /setbjmaxbet  /setbjcountdown
"""

import asyncio
from dataclasses import dataclass, field
from highrise import BaseBot, User

import database as db
from modules.cards       import make_deck, hand_str, hand_value, is_blackjack, card_str
from modules.shop        import get_player_benefits
from modules.permissions import can_manage_games

_BJ_CASINO_CAP = 5.0   # max % casino bonus applied to BJ winning payouts


# ─── In-memory state ─────────────────────────────────────────────────────────

@dataclass
class _Player:
    user_id:  str
    username: str
    bet:      int
    hand:     list = field(default_factory=list)
    status:   str  = "playing"   # playing | stood | bust | bj
    doubled:  bool = False


class _BJState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.phase:        str  = "idle"
        self.players:      list = []
        self.dealer_hand:  list = []
        self.deck:         list = []
        self.current_idx:  int  = 0
        self.lobby_task          = None
        self.turn_task           = None

    def get_player(self, user_id: str):
        for p in self.players:
            if p.user_id == user_id:
                return p
        return None

    def in_game(self, user_id: str) -> bool:
        return self.get_player(user_id) is not None


_state = _BJState()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _settings() -> dict:
    return db.get_bj_settings()


def _cancel_task(task, label: str = ""):
    if task and not task.done():
        task.cancel()
        if label:
            print(f"[BJ] {label} cancelled")


def _is_soft_17(hand: list) -> bool:
    if hand_value(hand) != 17:
        return False
    hard = sum(
        10 if r in ("J", "Q", "K") else (1 if r == "A" else int(r))
        for r, _ in hand
    )
    return hard != 17


def _current_player():
    if _state.current_idx < len(_state.players):
        return _state.players[_state.current_idx]
    return None


# ─── Lobby countdown ─────────────────────────────────────────────────────────

async def _lobby_countdown(bot: BaseBot, seconds: int):
    print(f"[BJ] Countdown started ({seconds}s)")
    try:
        await asyncio.sleep(seconds)
        await _start_round(bot)
    except asyncio.CancelledError:
        print("[BJ] Countdown cancelled")
        raise


# ─── Round start ─────────────────────────────────────────────────────────────

async def _start_round(bot: BaseBot):
    if _state.phase != "lobby" or not _state.players:
        _state.reset()
        return

    _state.phase = "round"
    _state.deck  = make_deck()

    for _ in range(2):
        for p in _state.players:
            p.hand.append(_state.deck.pop())
        _state.dealer_hand.append(_state.deck.pop())

    for p in _state.players:
        if is_blackjack(p.hand):
            p.status = "bj"

    _state.current_idx = 0
    await bot.highrise.chat(
        f"🃏 BJ started! Dealer shows: {card_str(_state.dealer_hand[0])}"
    )
    await _advance_turn(bot)


# ─── Turn management ─────────────────────────────────────────────────────────

async def _advance_turn(bot: BaseBot):
    _cancel_task(_state.turn_task, "Turn timer")
    _state.turn_task = None

    while _state.current_idx < len(_state.players):
        p = _state.players[_state.current_idx]
        if p.status == "playing":
            break
        if p.status == "bj":
            await bot.highrise.chat(f"🤑 @{p.username} has Blackjack!")
        _state.current_idx += 1
    else:
        await _dealer_play(bot)
        return

    p     = _state.players[_state.current_idx]
    total = hand_value(p.hand)
    await bot.highrise.chat(
        f"➡️ @{p.username}: {hand_str(p.hand)} = {total}. /bj hit or /bj stand"
    )
    s     = _settings()
    timer = int(s.get("turn_timer", 30))
    print(f"[BJ] Turn timer started ({timer}s) for @{p.username}")
    _state.turn_task = asyncio.create_task(_turn_timeout(bot, p.user_id, timer))


async def _turn_timeout(bot: BaseBot, user_id: str, seconds: int):
    try:
        await asyncio.sleep(seconds)
        p = _state.get_player(user_id)
        if p and p.status == "playing":
            p.status = "stood"
            await bot.highrise.chat(
                f"⏱️ @{p.username} timed out. Auto-stand at {hand_value(p.hand)}."
            )
            _state.current_idx += 1
            await _advance_turn(bot)
    except asyncio.CancelledError:
        raise


# ─── Dealer play ─────────────────────────────────────────────────────────────

async def _dealer_play(bot: BaseBot):
    total = hand_value(_state.dealer_hand)
    await bot.highrise.chat(
        f"Dealer reveals: {hand_str(_state.dealer_hand)} = {total}"
    )

    s           = _settings()
    hits_soft17 = bool(int(s.get("dealer_hits_soft_17", 1)))

    while True:
        total = hand_value(_state.dealer_hand)
        if total > 17:
            break
        if total == 17 and not hits_soft17:
            break
        if total == 17 and not _is_soft_17(_state.dealer_hand):
            break
        card = _state.deck.pop()
        _state.dealer_hand.append(card)
        total = hand_value(_state.dealer_hand)
        await bot.highrise.chat(
            f"Dealer hits {card_str(card)}. "
            f"Hand: {hand_str(_state.dealer_hand)} = {total}"
        )

    await _settle(bot)


# ─── Settlement ──────────────────────────────────────────────────────────────

async def _settle(bot: BaseBot):
    s            = _settings()
    win_payout   = float(s.get("win_payout", 2.0))
    bj_payout    = float(s.get("blackjack_payout", 2.5))
    push_rule    = s.get("push_rule", "refund")
    dealer_total = hand_value(_state.dealer_hand)
    dealer_bust  = dealer_total > 21

    for p in _state.players:
        try:
            ptotal    = hand_value(p.hand)
            benefits  = get_player_benefits(p.user_id)
            bonus_pct = min(
                float(benefits.get("coinflip_payout_pct", 0.0)),
                _BJ_CASINO_CAP
            ) / 100.0

            if p.status == "bust":
                db.update_bj_stats(p.user_id, loss=1, bet=p.bet, lost=p.bet)
                await bot.highrise.chat(f"❌ @{p.username} loses {p.bet:,}c.")

            elif p.status == "bj":
                payout = int(p.bet * bj_payout * (1.0 + bonus_pct))
                db.adjust_balance(p.user_id, payout)
                db.add_coins_earned(p.user_id, payout - p.bet)
                db.update_bj_stats(p.user_id, win=1, bj=1, bet=p.bet, won=payout)
                await bot.highrise.chat(f"🤑 @{p.username} blackjack! Paid {payout:,}c.")

            elif dealer_bust or ptotal > dealer_total:
                payout = int(p.bet * win_payout * (1.0 + bonus_pct))
                db.adjust_balance(p.user_id, payout)
                db.add_coins_earned(p.user_id, payout - p.bet)
                db.update_bj_stats(p.user_id, win=1, bet=p.bet, won=payout)
                await bot.highrise.chat(f"✅ @{p.username} wins! Paid {payout:,}c.")

            elif ptotal == dealer_total:
                if push_rule == "refund":
                    db.adjust_balance(p.user_id, p.bet)
                db.update_bj_stats(p.user_id, push=1, bet=p.bet)
                await bot.highrise.chat(
                    f"↔️ @{p.username} pushes. {p.bet:,}c refunded."
                )

            else:
                db.update_bj_stats(p.user_id, loss=1, bet=p.bet, lost=p.bet)
                await bot.highrise.chat(f"❌ @{p.username} loses {p.bet:,}c.")

        except Exception as exc:
            print(f"[BJ] settle error for {p.username}: {exc}")

    _state.reset()


# ─── Top-level router ────────────────────────────────────────────────────────

async def handle_bj(bot: BaseBot, user: User, args: list[str]):
    """Route /bj <subcommand> [args]."""
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
        elif sub == "hit":
            await _cmd_hit(bot, user)
        elif sub == "stand":
            await _cmd_stand(bot, user)
        elif sub == "double":
            await _cmd_double(bot, user)
        elif sub == "rules":
            await _cmd_rules(bot, user)
        elif sub == "stats":
            await _cmd_stats(bot, user)
        elif sub == "cancel":
            await _cmd_cancel(bot, user)
        elif sub == "settings":
            await _cmd_settings(bot, user)
        elif sub == "on":
            await _cmd_bj_mode(bot, user, True)
        elif sub == "off":
            await _cmd_bj_mode(bot, user, False)
        else:
            await bot.highrise.send_whisper(
                user.id,
                "🃏 BJ: /bj join <bet>  /bj hit  /bj stand  /bj double\n"
                "/bj leave  /bj table  /bj players  /bj rules  /bj stats"
            )
    except Exception as exc:
        print(f"[BJ] /{' '.join(args)} error for {user.username}: {exc}")
        try:
            await bot.highrise.send_whisper(user.id, "Blackjack error. Try again!")
        except Exception:
            pass


# ─── Sub-command handlers ────────────────────────────────────────────────────

async def _cmd_join(bot: BaseBot, user: User, args: list[str]):
    s = _settings()

    if not int(s.get("bj_enabled", 1)):
        await bot.highrise.send_whisper(user.id, "Casual BJ is currently closed.")
        return

    if len(args) < 3 or not args[2].isdigit():
        await bot.highrise.send_whisper(user.id, "Invalid bet. Use /bj join <amount>.")
        return

    bet     = int(args[2])
    min_bet = int(s.get("min_bet", 10))
    max_bet = int(s.get("max_bet", 1000))

    if bet < min_bet or bet > max_bet:
        await bot.highrise.send_whisper(
            user.id, f"Bet must be {min_bet:,}–{max_bet:,} coins."
        )
        return

    if _state.phase == "round":
        await bot.highrise.send_whisper(
            user.id, "Round in progress. Wait for the next game."
        )
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

    db.adjust_balance(user.id, -bet)
    _state.players.append(
        _Player(user_id=user.id, username=user.username, bet=bet)
    )

    count   = len(_state.players)
    display = db.get_display_name(user.id, user.username)
    await bot.highrise.chat(
        f"✅ {display} joined BJ with {bet:,}c. Players: {count}/{max_players}"
    )

    if _state.phase == "idle":
        _state.phase      = "lobby"
        countdown         = int(s.get("lobby_countdown", 15))
        _cancel_task(_state.lobby_task, "Countdown")
        _state.lobby_task = asyncio.create_task(_lobby_countdown(bot, countdown))
        await bot.highrise.chat(
            f"🃏 BJ lobby open! /bj join <bet>. Starts in {countdown}s."
        )


async def _cmd_leave(bot: BaseBot, user: User):
    if _state.phase == "round":
        await bot.highrise.send_whisper(user.id, "Can't leave during a round.")
        return

    p = _state.get_player(user.id)
    if p is None:
        await bot.highrise.send_whisper(user.id, "You're not in the BJ lobby.")
        return

    db.adjust_balance(user.id, p.bet)
    _state.players.remove(p)
    display = db.get_display_name(user.id, user.username)
    await bot.highrise.chat(f"↩️ {display} left BJ. Bet refunded.")

    if not _state.players:
        _cancel_task(_state.lobby_task, "Countdown")
        _state.reset()
        await bot.highrise.chat("BJ lobby closed — no players.")


async def _cmd_players(bot: BaseBot, user: User):
    if _state.phase == "idle":
        await bot.highrise.send_whisper(
            user.id, "No blackjack table active. Use /bj join <bet>."
        )
        return

    lines = [f"-- BJ Players ({_state.phase}) --"]
    for p in _state.players:
        lines.append(f"  @{p.username}  {p.bet:,}c")
    if not _state.players:
        lines.append("  (none)")
    await bot.highrise.send_whisper(user.id, "\n".join(lines))


async def _cmd_table(bot: BaseBot, user: User):
    if _state.phase == "idle":
        await bot.highrise.send_whisper(
            user.id, "No blackjack table active. Use /bj join <bet>."
        )
        return

    if _state.phase == "lobby":
        count = len(_state.players)
        names = ", ".join(f"@{p.username}" for p in _state.players) or "none"
        await bot.highrise.send_whisper(
            user.id,
            f"🃏 BJ Lobby — {count} player(s)\n{names}"
        )
        return

    lines = [f"Dealer: {card_str(_state.dealer_hand[0])} ?"]
    for i, p in enumerate(_state.players):
        arrow = "➡️" if i == _state.current_idx and p.status == "playing" else "  "
        total = hand_value(p.hand)
        lines.append(
            f"{arrow}@{p.username}: {hand_str(p.hand)}={total}[{p.status}]"
        )
    msg = "\n".join(lines)
    if len(msg) > 245:
        msg = msg[:242] + "..."
    await bot.highrise.send_whisper(user.id, msg)


async def _cmd_hit(bot: BaseBot, user: User):
    if _state.phase != "round":
        await bot.highrise.send_whisper(user.id, "No blackjack round active.")
        return
    p = _current_player()
    if p is None or p.user_id != user.id or p.status != "playing":
        await bot.highrise.send_whisper(user.id, "Not your turn yet.")
        return

    _cancel_task(_state.turn_task, "Turn timer")
    card  = _state.deck.pop()
    p.hand.append(card)
    total = hand_value(p.hand)

    await bot.highrise.chat(
        f"🃏 @{p.username} drew {card_str(card)}. Total: {total}"
    )

    if total > 21:
        p.status = "bust"
        await bot.highrise.chat(f"💥 @{p.username} busts at {total}.")
        _state.current_idx += 1
        await _advance_turn(bot)
    else:
        s     = _settings()
        timer = int(s.get("turn_timer", 30))
        print(f"[BJ] Turn timer started ({timer}s) for @{p.username}")
        _state.turn_task = asyncio.create_task(
            _turn_timeout(bot, p.user_id, timer)
        )


async def _cmd_stand(bot: BaseBot, user: User):
    if _state.phase != "round":
        await bot.highrise.send_whisper(user.id, "No blackjack round active.")
        return
    p = _current_player()
    if p is None or p.user_id != user.id or p.status != "playing":
        await bot.highrise.send_whisper(user.id, "Not your turn yet.")
        return

    _cancel_task(_state.turn_task, "Turn timer")
    p.status = "stood"
    total    = hand_value(p.hand)
    await bot.highrise.chat(f"✋ @{p.username} stands at {total}.")
    _state.current_idx += 1
    await _advance_turn(bot)


async def _cmd_double(bot: BaseBot, user: User):
    if _state.phase != "round":
        await bot.highrise.send_whisper(user.id, "No blackjack round active.")
        return
    p = _current_player()
    if p is None or p.user_id != user.id or p.status != "playing":
        await bot.highrise.send_whisper(user.id, "Not your turn yet.")
        return

    if db.get_balance(user.id) < p.bet:
        await bot.highrise.send_whisper(user.id, "Not enough coins to double.")
        return

    _cancel_task(_state.turn_task, "Turn timer")
    db.adjust_balance(user.id, -p.bet)
    p.bet    *= 2
    p.doubled = True

    card  = _state.deck.pop()
    p.hand.append(card)
    total = hand_value(p.hand)

    if total > 21:
        p.status = "bust"
        await bot.highrise.chat(
            f"⚡ @{p.username} doubled to {p.bet:,}c, "
            f"drew {card_str(card)}, busts at {total}."
        )
    else:
        p.status = "stood"
        await bot.highrise.chat(
            f"⚡ @{p.username} doubled to {p.bet:,}c, "
            f"drew {card_str(card)}, stands at {total}."
        )

    _state.current_idx += 1
    await _advance_turn(bot)


async def _cmd_rules(bot: BaseBot, user: User):
    s = _settings()
    await bot.highrise.send_whisper(user.id,
        f"🃏 BJ Rules\n"
        f"Bet: {s.get('min_bet',10):,}–{s.get('max_bet',1000):,}c\n"
        f"Win: {s.get('win_payout',2.0)}x  BJ: {s.get('blackjack_payout',2.5)}x\n"
        f"Push: {s.get('push_rule','refund')}  "
        f"Soft17: {'hit' if s.get('dealer_hits_soft_17',1) else 'stand'}"
    )


async def _cmd_stats(bot: BaseBot, user: User):
    db.ensure_user(user.id, user.username)
    s = db.get_bj_stats(user.id)
    await bot.highrise.send_whisper(user.id,
        f"-- {user.username} BJ Stats --\n"
        f"W:{s['bj_wins']} L:{s['bj_losses']} "
        f"P:{s['bj_pushes']} BJ:{s['bj_blackjacks']}\n"
        f"Bet:{s['bj_total_bet']:,}c  "
        f"Won:{s['bj_total_won']:,}c  "
        f"Lost:{s['bj_total_lost']:,}c"
    )


async def _cmd_cancel(bot: BaseBot, user: User):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Admins and managers only.")
        return

    if _state.phase == "idle":
        await bot.highrise.send_whisper(user.id, "No active BJ game to cancel.")
        return

    for p in _state.players:
        db.adjust_balance(p.user_id, p.bet)

    _cancel_task(_state.lobby_task, "Countdown")
    _cancel_task(_state.turn_task, "Turn timer")
    _state.reset()
    await bot.highrise.chat("🃏 BJ cancelled. All bets refunded.")


async def _cmd_settings(bot: BaseBot, user: User):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Admins and managers only.")
        return

    s = _settings()
    enabled = "ON" if int(s.get("bj_enabled", 1)) else "OFF"
    await bot.highrise.send_whisper(user.id,
        f"-- BJ Settings --\n"
        f"enabled:{enabled}\n"
        f"min:{s.get('min_bet',10):,}c  max:{s.get('max_bet',1000):,}c\n"
        f"win:{s.get('win_payout',2.0)}x  bj:{s.get('blackjack_payout',2.5)}x\n"
        f"push:{s.get('push_rule','refund')}  "
        f"soft17:{'yes' if s.get('dealer_hits_soft_17',1) else 'no'}\n"
        f"lobby:{s.get('lobby_countdown',15)}s  "
        f"turn:{s.get('turn_timer',30)}s  "
        f"max:{s.get('max_players',6)}p"
    )


async def _cmd_bj_mode(bot: BaseBot, user: User, enabled: bool):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Admins and managers only.")
        return
    db.set_bj_setting("bj_enabled", 1 if enabled else 0)
    status = "ON" if enabled else "OFF"
    await bot.highrise.chat(
        f"{'✅' if enabled else '⛔'} Casual BJ is now {status}."
    )


# ─── Admin setting commands (/setbjXXX) ──────────────────────────────────────

async def handle_bj_set(bot: BaseBot, user: User, cmd: str, args: list[str]):
    """Handle /setbjXXX admin commands. Permission gate enforced in main.py."""
    try:
        if len(args) < 2:
            await bot.highrise.send_whisper(user.id, f"Usage: /{cmd} <value>")
            return

        raw = args[1]

        if cmd == "setbjminbet":
            if not raw.isdigit() or int(raw) < 1:
                await bot.highrise.send_whisper(user.id, "Min bet must be >= 1.")
                return
            val = int(raw)
            s = _settings()
            if val >= int(s.get("max_bet", 1000)):
                await bot.highrise.send_whisper(
                    user.id, "Min bet must be less than max bet."
                )
                return
            db.set_bj_setting("min_bet", val)
            await bot.highrise.send_whisper(
                user.id, f"✅ BJ min bet set to {val:,}c."
            )

        elif cmd == "setbjmaxbet":
            if not raw.isdigit() or int(raw) < 1:
                await bot.highrise.send_whisper(user.id, "Max bet must be >= 1.")
                return
            val = int(raw)
            s = _settings()
            if val <= int(s.get("min_bet", 10)):
                await bot.highrise.send_whisper(
                    user.id, "Max bet must be greater than min bet."
                )
                return
            db.set_bj_setting("max_bet", val)
            await bot.highrise.send_whisper(
                user.id, f"✅ BJ max bet set to {val:,}c."
            )

        elif cmd == "setbjcountdown":
            if not raw.isdigit() or not (5 <= int(raw) <= 120):
                await bot.highrise.send_whisper(
                    user.id, "Countdown must be 5–120 seconds."
                )
                return
            val = int(raw)
            db.set_bj_setting("lobby_countdown", val)
            await bot.highrise.send_whisper(
                user.id, f"✅ BJ lobby countdown set to {val}s."
            )

        else:
            await bot.highrise.send_whisper(
                user.id,
                "BJ settings: /setbjminbet /setbjmaxbet /setbjcountdown"
            )

    except Exception as exc:
        print(f"[BJ] {cmd} error for {user.username}: {exc}")
        try:
            await bot.highrise.send_whisper(user.id, "Setting update failed. Try again!")
        except Exception:
            pass
