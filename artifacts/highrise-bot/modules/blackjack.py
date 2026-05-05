"""
modules/blackjack.py
--------------------
Casual lobby-style blackjack for the Highrise Mini Game Bot.

State lifecycle:
  idle ──► lobby (first /bj join) ──► round (countdown ends) ──► idle (round ends)

Public:  /bj join <bet>  /bj leave  /bj players  /bj table
         /bj hit  /bj stand  /bj double  /bj rules  /bj stats
Manager: /bj on  /bj off  /bj cancel  /bj settings
         /bj state  /bj recover  /bj refund  /bj forcefinish
Admin:   /setbjminbet  /setbjmaxbet  /setbjcountdown
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from highrise import BaseBot, User

import database as db
from modules.cards       import make_deck, hand_str, hand_value, is_blackjack, card_str
from modules.quests      import track_quest
from modules.shop        import get_player_benefits
from modules.permissions import can_manage_games, can_moderate

_BJ_CASINO_CAP = 5.0   # max % casino bonus applied to BJ winning payouts


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _remaining_secs(iso_str: str, default: int = 0) -> int:
    """Seconds remaining until iso_str deadline, or default if expired/missing."""
    if not iso_str:
        return default
    try:
        end = datetime.fromisoformat(iso_str)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return max(0, int((end - datetime.now(timezone.utc)).total_seconds()))
    except Exception:
        return default


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
        self.phase:              str  = "idle"
        self.players:            list = []
        self.dealer_hand:        list = []
        self.deck:               list = []
        self.current_idx:        int  = 0
        self.lobby_task               = None
        self.turn_task                = None
        self.round_id:           str  = ""
        self._countdown_ends_at: str  = ""
        self._turn_ends_at:      str  = ""

    def get_player(self, user_id: str):
        for p in self.players:
            if p.user_id == user_id:
                return p
        return None

    def in_game(self, user_id: str) -> bool:
        return self.get_player(user_id) is not None


_state = _BJState()


# ─── DB persistence helpers ───────────────────────────────────────────────────

def _save_table_state() -> None:
    try:
        db.save_casino_table("bj", {
            "phase":                _state.phase,
            "round_id":             _state.round_id,
            "current_player_index": _state.current_idx,
            "dealer_hand_json":     json.dumps(_state.dealer_hand),
            "deck_json":            json.dumps(_state.deck),
            "shoe_json":            "[]",
            "shoe_cards_remaining": 0,
            "countdown_ends_at":    _state._countdown_ends_at,
            "turn_ends_at":         _state._turn_ends_at,
            "active":               1 if _state.phase != "idle" else 0,
            "recovery_required":    0,
        })
    except Exception as exc:
        print(f"[BJ] save_table_state error: {exc}")


def _save_player_state(p: _Player) -> None:
    try:
        db.save_casino_player("bj", {
            "username":  p.username,
            "user_id":   p.user_id,
            "bet":       p.bet,
            "hand_json": json.dumps(p.hand),
            "status":    p.status,
            "doubled":   1 if p.doubled else 0,
            "payout":    0,
            "result":    "",
        })
    except Exception as exc:
        print(f"[BJ] save_player_state error for {p.username}: {exc}")


def _save_all_player_states() -> None:
    for p in _state.players:
        _save_player_state(p)


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
    end_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    _state._countdown_ends_at = end_at.isoformat()
    _save_table_state()
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
        db.clear_casino_table("bj")
        return

    _state.phase   = "round"
    _state.deck    = make_deck()
    _state.round_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f") + "_bj"
    _state._countdown_ends_at = ""

    for _ in range(2):
        for p in _state.players:
            p.hand.append(_state.deck.pop())
        _state.dealer_hand.append(_state.deck.pop())

    for p in _state.players:
        if is_blackjack(p.hand):
            p.status = "bj"

    _state.current_idx = 0
    _save_table_state()
    _save_all_player_states()
    print(f"[BJ] Round started. round_id={_state.round_id}")

    await bot.highrise.chat(
        f"🃏 BJ started! Dealer shows: {card_str(_state.dealer_hand[0])}"
    )
    await _advance_turn(bot)


# ─── Turn management ─────────────────────────────────────────────────────────

async def _advance_turn(bot: BaseBot):
    _cancel_task(_state.turn_task, "Turn timer")
    _state.turn_task     = None
    _state._turn_ends_at = ""

    while _state.current_idx < len(_state.players):
        p = _state.players[_state.current_idx]
        if p.status == "playing":
            break
        if p.status == "bj":
            await bot.highrise.chat(f"🤑 @{p.username} has Blackjack!")
        _state.current_idx += 1
    else:
        _save_table_state()
        await _finalize_round(bot)
        return

    p     = _state.players[_state.current_idx]
    total = hand_value(p.hand)
    s     = _settings()
    timer = int(s.get("bj_turn_timer", 20))

    end_at = datetime.now(timezone.utc) + timedelta(seconds=timer)
    _state._turn_ends_at = end_at.isoformat()
    _save_table_state()

    await bot.highrise.chat(
        f"➡️ @{p.username}: {hand_str(p.hand)} = {total}. Act in {timer}s."
    )
    print(f"[BJ] Turn timer started ({timer}s) for @{p.username}")
    _state.turn_task = asyncio.create_task(_turn_timeout(bot, p.user_id, timer))


async def _turn_timeout(bot: BaseBot, user_id: str, seconds: int):
    try:
        await asyncio.sleep(seconds)
        p = _state.get_player(user_id)
        if p and p.status == "playing":
            p.status = "stood"
            _save_player_state(p)
            await bot.highrise.chat(f"⏳ @{p.username} timed out. Auto-stand.")
            _state.current_idx += 1
            _state.turn_task = None
            await _advance_turn(bot)
    except asyncio.CancelledError:
        raise


# ─── Finalize round (dealer play + settle + cleanup) ─────────────────────────

async def _finalize_round(bot: BaseBot):
    """Dealer plays to completion, settles all bets, always resets state."""
    _cancel_task(_state.turn_task, "Turn timer")
    _state.turn_task     = None
    _state._turn_ends_at = ""
    try:
        s           = _settings()
        hits_soft17 = bool(int(s.get("dealer_hits_soft_17", 1)))
        win_payout  = float(s.get("win_payout", 2.0))
        bj_payout   = float(s.get("blackjack_payout", 2.5))
        push_rule   = s.get("push_rule", "refund")

        total = hand_value(_state.dealer_hand)
        await bot.highrise.chat(
            f"Dealer reveals: {hand_str(_state.dealer_hand)} = {total}"
        )

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
            _save_table_state()
            await bot.highrise.chat(
                f"Dealer hits {card_str(card)}. "
                f"Hand: {hand_str(_state.dealer_hand)} = {total}"
            )

        dealer_total  = hand_value(_state.dealer_hand)
        dealer_bust   = dealer_total > 21
        _bj_event_pts = (db.is_event_active()
                         and bool(int(db.get_bj_settings().get("bj_enabled", 1))))
        round_id      = _state.round_id

        for p in _state.players:
            try:
                # ── Dedup guard ────────────────────────────────────────────
                if round_id and db.is_result_paid("bj", round_id, p.username):
                    print(f"[BJ] Skipping already-paid {p.username}")
                    continue

                ptotal    = hand_value(p.hand)
                track_quest(p.user_id, "bj_round")
                if _bj_event_pts:
                    db.add_event_points(p.user_id, 1)
                benefits  = get_player_benefits(p.user_id)
                bonus_pct = min(
                    float(benefits.get("coinflip_payout_pct", 0.0)),
                    _BJ_CASINO_CAP
                ) / 100.0

                if p.status == "bust":
                    if round_id:
                        db.save_round_result(
                            "bj", round_id, p.username, p.user_id,
                            p.bet, "bust", 0, -p.bet)
                    db.update_bj_stats(p.user_id, loss=1, bet=p.bet, lost=p.bet)
                    db.add_bj_daily_net(p.user_id, -p.bet)
                    await bot.highrise.chat(f"❌ @{p.username} loses {p.bet:,}c.")
                    if round_id:
                        db.mark_result_paid("bj", round_id, p.username)

                elif p.status == "bj":
                    payout = int(p.bet * bj_payout * (1.0 + bonus_pct))
                    if round_id:
                        db.save_round_result(
                            "bj", round_id, p.username, p.user_id,
                            p.bet, "blackjack", payout, payout - p.bet)
                    db.adjust_balance(p.user_id, payout)
                    db.add_coins_earned(p.user_id, payout - p.bet)
                    db.update_bj_stats(p.user_id, win=1, bj=1, bet=p.bet, won=payout)
                    db.add_bj_daily_net(p.user_id, payout - p.bet)
                    await bot.highrise.chat(
                        f"🤑 @{p.username} blackjack! Paid {payout:,}c."
                    )
                    if round_id:
                        db.mark_result_paid("bj", round_id, p.username)

                elif dealer_bust or ptotal > dealer_total:
                    payout = int(p.bet * win_payout * (1.0 + bonus_pct))
                    if round_id:
                        db.save_round_result(
                            "bj", round_id, p.username, p.user_id,
                            p.bet, "win", payout, payout - p.bet)
                    db.adjust_balance(p.user_id, payout)
                    db.add_coins_earned(p.user_id, payout - p.bet)
                    db.update_bj_stats(p.user_id, win=1, bet=p.bet, won=payout)
                    db.add_bj_daily_net(p.user_id, payout - p.bet)
                    await bot.highrise.chat(f"✅ @{p.username} wins! Paid {payout:,}c.")
                    if round_id:
                        db.mark_result_paid("bj", round_id, p.username)

                elif ptotal == dealer_total:
                    refund = p.bet if push_rule == "refund" else 0
                    if round_id:
                        db.save_round_result(
                            "bj", round_id, p.username, p.user_id,
                            p.bet, "push", refund, 0 if refund else -p.bet)
                    if push_rule == "refund":
                        db.adjust_balance(p.user_id, p.bet)
                    db.update_bj_stats(p.user_id, push=1, bet=p.bet)
                    await bot.highrise.chat(
                        f"↔️ @{p.username} pushes. {p.bet:,}c refunded."
                    )
                    if round_id:
                        db.mark_result_paid("bj", round_id, p.username)

                else:
                    if round_id:
                        db.save_round_result(
                            "bj", round_id, p.username, p.user_id,
                            p.bet, "loss", 0, -p.bet)
                    db.update_bj_stats(p.user_id, loss=1, bet=p.bet, lost=p.bet)
                    db.add_bj_daily_net(p.user_id, -p.bet)
                    await bot.highrise.chat(f"❌ @{p.username} loses {p.bet:,}c.")
                    if round_id:
                        db.mark_result_paid("bj", round_id, p.username)

            except Exception as exc:
                print(f"[BJ] settle error for {p.username}: {exc}")

    except Exception as exc:
        print(f"[BJ] finalize_round error: {exc}")
    finally:
        print("[BJ] Round ended")
        db.clear_casino_table("bj")
        _state.reset()


# ─── Public reset functions ───────────────────────────────────────────────────

def reset_table() -> str:
    """Cancel active BJ game, refund all bets, reset to idle."""
    if _state.phase == "idle":
        return "idle"
    for p in _state.players:
        try:
            db.adjust_balance(p.user_id, p.bet)
            db.add_ledger_entry(p.user_id, p.username, p.bet, "bj_cancel_refund")
        except Exception as exc:
            print(f"[BJ] reset_table refund error for {p.username}: {exc}")
    _cancel_task(_state.lobby_task, "Countdown")
    _cancel_task(_state.turn_task, "Turn timer")
    db.clear_casino_table("bj")
    _state.reset()
    print("[BJ] Table reset by admin")
    return "reset"


def soft_reset_table() -> None:
    """Cancel timers and clear in-memory state WITHOUT refunding bets.
    State is preserved in SQLite for startup recovery."""
    if _state.phase == "idle":
        return
    _save_table_state()
    _save_all_player_states()
    _cancel_task(_state.lobby_task, "Countdown")
    _cancel_task(_state.turn_task, "Turn timer")
    _state.reset()
    print("[BJ] Table soft-reset (state saved to DB for recovery)")


# ─── Startup recovery ─────────────────────────────────────────────────────────

async def startup_bj_recovery(bot: BaseBot) -> None:
    """Called on startup to restore any active BJ table from SQLite."""
    row = db.load_casino_table("bj")
    if not row or not row.get("active") or row.get("phase", "idle") == "idle":
        return

    phase = row.get("phase", "idle")
    print(f"[RECOVERY] BJ found saved phase={phase}")

    if row.get("recovery_required"):
        print("[RECOVERY] BJ marked recovery_required — alerting in chat.")
        try:
            await bot.highrise.chat(
                "⚠️ BJ recovery needed. Use /bj recover or /bj refund."
            )
        except Exception:
            pass
        return

    try:
        players_data = db.load_casino_players("bj")
        if not players_data:
            print("[RECOVERY] BJ: no players found, clearing state.")
            db.clear_casino_table("bj")
            return

        _state.players = [
            _Player(
                user_id=pd["user_id"],
                username=pd["username"],
                bet=int(pd["bet"]),
                hand=json.loads(pd.get("hand_json") or "[]"),
                status=pd.get("status", "lobby"),
                doubled=bool(int(pd.get("doubled", 0))),
            )
            for pd in players_data
        ]
        _state.round_id           = row.get("round_id", "")
        _state.current_idx        = int(row.get("current_player_index", 0))
        _state.dealer_hand        = json.loads(row.get("dealer_hand_json") or "[]")
        _state.deck               = json.loads(row.get("deck_json") or "[]")
        _state._countdown_ends_at = row.get("countdown_ends_at", "")
        _state._turn_ends_at      = row.get("turn_ends_at", "")

        if phase == "lobby":
            _state.phase = "lobby"
            secs = _remaining_secs(_state._countdown_ends_at, default=5)
            _state.lobby_task = asyncio.create_task(_lobby_countdown(bot, secs))
            await bot.highrise.chat("♻️ BJ table restored after restart.")
            print(f"[RECOVERY] BJ lobby restored, countdown in {secs}s.")

        elif phase in ("round", "active"):
            _state.phase = "round"
            # Complete any crash-interrupted payouts first
            if _state.round_id:
                unpaid = db.get_unpaid_results("bj", _state.round_id)
                if unpaid:
                    print(f"[RECOVERY] BJ: completing {len(unpaid)} unpaid payouts...")
                    await _complete_unpaid_payouts(bot, "bj", unpaid)
                    db.clear_casino_table("bj")
                    _state.reset()
                    return

            await bot.highrise.chat("♻️ BJ table restored after restart.")

            active_ps = [p for p in _state.players if p.status == "playing"]
            if not active_ps:
                asyncio.create_task(_finalize_round(bot))
                return

            secs = _remaining_secs(_state._turn_ends_at, default=0)
            cur  = (_state.players[_state.current_idx]
                    if _state.current_idx < len(_state.players) else None)
            if cur and cur.status == "playing":
                if secs > 0:
                    _state.turn_task = asyncio.create_task(
                        _turn_timeout(bot, cur.user_id, secs)
                    )
                    print(f"[RECOVERY] BJ turn timer restarted: {secs}s for @{cur.username}")
                else:
                    asyncio.create_task(_auto_stand_advance(bot, cur.user_id))
            print(f"[RECOVERY] BJ round restored. Players={len(_state.players)}")

        elif phase == "finished":
            print("[RECOVERY] BJ phase=finished, clearing state.")
            db.clear_casino_table("bj")

    except Exception as exc:
        print(f"[RECOVERY] BJ recovery failed: {exc}")
        try:
            db.save_casino_table("bj", {
                "phase": row.get("phase", "?"),
                "round_id": row.get("round_id", ""),
                "current_player_index": 0,
                "dealer_hand_json": "[]",
                "deck_json": "[]",
                "shoe_json": "[]",
                "shoe_cards_remaining": 0,
                "countdown_ends_at": "",
                "turn_ends_at": "",
                "active": 1,
                "recovery_required": 1,
            })
        except Exception:
            pass
        try:
            await bot.highrise.chat(
                "⚠️ BJ recovery needed. Use /bj recover or /bj refund."
            )
        except Exception:
            pass


async def _auto_stand_advance(bot: BaseBot, user_id: str) -> None:
    p = _state.get_player(user_id)
    if p and p.status == "playing":
        p.status = "stood"
        _save_player_state(p)
        await bot.highrise.chat(f"⏳ @{p.username} timed out (recovery). Auto-stand.")
        _state.current_idx += 1
        await _advance_turn(bot)


async def _complete_unpaid_payouts(bot: BaseBot, mode: str, unpaid: list) -> None:
    """Pay out any results recorded but not yet marked paid (crash recovery)."""
    for row in unpaid:
        try:
            if db.is_result_paid(mode, row["round_id"], row["username"]):
                continue
            payout = int(row.get("payout", 0))
            if payout > 0:
                db.adjust_balance(row["user_id"], payout)
                db.add_ledger_entry(
                    row["user_id"], row["username"], payout,
                    f"{mode}_recovery_payout"
                )
                msg = (f"♻️ @{row['username']} recovered {row['result']}: "
                       f"{payout:,}c.")
                await bot.highrise.chat(msg[:249])
            db.mark_result_paid(mode, row["round_id"], row["username"])
        except Exception as exc:
            print(f"[RECOVERY] unpaid payout error for {row.get('username')}: {exc}")


# ─── Top-level router ─────────────────────────────────────────────────────────

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
        elif sub == "limits":
            await _cmd_limits(bot, user)
        elif sub == "leaderboard":
            await _cmd_leaderboard(bot, user)
        elif sub == "settings":
            await _cmd_settings(bot, user)
        elif sub == "on":
            await _cmd_bj_mode(bot, user, True)
        elif sub == "off":
            await _cmd_bj_mode(bot, user, False)
        # ── Recovery commands (manager+) ──────────────────────────────────
        elif sub == "state":
            await _cmd_bj_state(bot, user)
        elif sub == "recover":
            await _cmd_bj_recover(bot, user)
        elif sub == "refund":
            await _cmd_bj_refund(bot, user)
        elif sub == "forcefinish":
            await _cmd_bj_forcefinish(bot, user)
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


# ─── Sub-command handlers ─────────────────────────────────────────────────────

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

    from modules.events import get_event_effect as _gee
    _ev_bj      = _gee()
    eff_max_bet = int(max_bet * _ev_bj["casino_bet_mult"])

    if bet < min_bet or bet > eff_max_bet:
        note = " (Casino Hour 2x limit!)" if _ev_bj["casino_bet_mult"] > 1 else ""
        await bot.highrise.send_whisper(
            user.id, f"Bet must be {min_bet:,}–{eff_max_bet:,} coins.{note}"
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

    net      = db.get_bj_daily_net(user.id)
    win_lim  = int(s.get("bj_daily_win_limit", 5000))
    loss_lim = int(s.get("bj_daily_loss_limit", 3000))
    win_on   = int(s.get("bj_win_limit_enabled", 1))
    loss_on  = int(s.get("bj_loss_limit_enabled", 1))
    if win_on and net >= win_lim:
        await bot.highrise.send_whisper(user.id, "BJ win limit reached. Try again tomorrow.")
        return
    if loss_on and net <= -loss_lim:
        await bot.highrise.send_whisper(user.id, "BJ loss limit reached. Try again tomorrow.")
        return
    if loss_on and max(0, -net) + bet > loss_lim:
        await bot.highrise.send_whisper(user.id, "Bet too high for your daily loss limit.")
        return

    db.adjust_balance(user.id, -bet)
    p = _Player(user_id=user.id, username=user.username, bet=bet)
    _state.players.append(p)
    _save_player_state(p)
    print(f"[BJ] @{user.username} joined with {bet:,}c")

    count   = len(_state.players)
    display = db.get_display_name(user.id, user.username)
    await bot.highrise.chat(
        f"✅ {display} joined BJ with {bet:,}c. Players: {count}/{max_players}"
    )

    if _state.phase == "idle":
        _state.phase  = "lobby"
        countdown     = int(s.get("lobby_countdown", 15))
        _cancel_task(_state.lobby_task, "Countdown")
        _save_table_state()
        _state.lobby_task = asyncio.create_task(_lobby_countdown(bot, countdown))
        await bot.highrise.chat(
            f"🃏 BJ lobby open! /bj join <bet>. Starts in {countdown}s."
        )
    else:
        _save_table_state()


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
        db.clear_casino_table("bj")
        _state.reset()
        await bot.highrise.chat("BJ lobby closed — no players.")
    else:
        _save_table_state()


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
    await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])


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
            f"🃏 BJ Lobby — {count} player(s)\n{names}"[:249]
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
    _save_player_state(p)
    _save_table_state()
    print(f"[BJ] @{p.username} hit → {card_str(card)} total={total}")

    await bot.highrise.chat(
        f"🃏 @{p.username} drew {card_str(card)}. Total: {total}"
    )

    if total > 21:
        p.status = "bust"
        _save_player_state(p)
        await bot.highrise.chat(f"💥 @{p.username} busts at {total}.")
        _state.current_idx += 1
        await _advance_turn(bot)
    else:
        s     = _settings()
        timer = int(s.get("bj_turn_timer", 20))
        end_at = datetime.now(timezone.utc) + timedelta(seconds=timer)
        _state._turn_ends_at = end_at.isoformat()
        _save_table_state()
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
    _save_player_state(p)
    _save_table_state()
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
    _save_player_state(p)
    _save_table_state()

    if total > 21:
        p.status = "bust"
        _save_player_state(p)
        await bot.highrise.chat(
            f"⚡ @{p.username} doubled to {p.bet:,}c, "
            f"drew {card_str(card)}, busts at {total}."
        )
    else:
        p.status = "stood"
        _save_player_state(p)
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


async def _cmd_limits(bot: BaseBot, user: User):
    db.ensure_user(user.id, user.username)
    s    = _settings()
    net  = db.get_bj_daily_net(user.id)
    wlim = int(s.get("bj_daily_win_limit", 5000))
    llim = int(s.get("bj_daily_loss_limit", 3000))
    sign = "+" if net >= 0 else ""
    await bot.highrise.send_whisper(user.id,
        f"-- BJ Daily Limits --\n"
        f"Win limit: {wlim:,}c  Loss limit: {llim:,}c\n"
        f"Your today: {sign}{net:,}c"
    )


async def _cmd_leaderboard(bot: BaseBot, user: User):
    rows = db.get_bj_leaderboard()
    if not rows:
        await bot.highrise.send_whisper(user.id, "No BJ stats yet. Play some games!")
        return
    lines = ["-- BJ Top 5 (Net Profit) --"]
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
        await bot.highrise.send_whisper(user.id, "No active BJ game to cancel.")
        return

    for p in _state.players:
        db.adjust_balance(p.user_id, p.bet)
        db.add_ledger_entry(p.user_id, p.username, p.bet, "bj_cancel_refund")
    _cancel_task(_state.lobby_task, "Countdown")
    _cancel_task(_state.turn_task, "Turn timer")
    db.clear_casino_table("bj")
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
        f"turn:{s.get('bj_turn_timer',20)}s  "
        f"max:{s.get('max_players',6)}p\n"
        f"daily win:{s.get('bj_daily_win_limit',5000):,}c  "
        f"loss:{s.get('bj_daily_loss_limit',3000):,}c"
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


# ─── Recovery staff commands ──────────────────────────────────────────────────

async def _cmd_bj_state(bot: BaseBot, user: User):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return

    if _state.phase == "idle":
        row = db.load_casino_table("bj")
        if row and row.get("active"):
            await bot.highrise.send_whisper(
                user.id,
                f"BJ: idle in memory | DB phase:{row.get('phase')}\n"
                "Use /bj recover or /bj refund."
            )
        else:
            await bot.highrise.send_whisper(user.id, "BJ: no active table.")
        return

    cur         = _current_player()
    total_bets  = sum(p.bet for p in _state.players)
    dealer_card = card_str(_state.dealer_hand[0]) if _state.dealer_hand else "?"
    turn_info   = f"@{cur.username}" if cur and cur.status == "playing" else "none"
    rid         = _state.round_id[-10:] if _state.round_id else "?"
    msg = (
        f"BJ {_state.phase} | Players:{len(_state.players)}\n"
        f"Turn:{turn_info} | Dealer:{dealer_card}\n"
        f"Bets:{total_bets:,}c | id:{rid}"
    )
    await bot.highrise.send_whisper(user.id, msg[:249])


async def _cmd_bj_recover(bot: BaseBot, user: User):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return

    if _state.phase != "idle":
        await bot.highrise.send_whisper(
            user.id, "BJ is active. Use /bj state to inspect, /bj refund to cancel."
        )
        return

    row = db.load_casino_table("bj")
    if not row or not row.get("active"):
        await bot.highrise.send_whisper(user.id, "No saved BJ state found.")
        return

    await bot.highrise.send_whisper(user.id, "♻️ Attempting BJ recovery...")
    try:
        db.save_casino_table("bj", {**dict(row), "recovery_required": 0})
    except Exception:
        pass
    await startup_bj_recovery(bot)
    await bot.highrise.send_whisper(user.id, "♻️ BJ recovery attempted. Check /bj state.")


async def _cmd_bj_refund(bot: BaseBot, user: User):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return

    refunded = 0
    for p in list(_state.players):
        try:
            db.adjust_balance(p.user_id, p.bet)
            db.add_ledger_entry(p.user_id, p.username, p.bet, "bj_recovery_refund")
            refunded += p.bet
        except Exception as exc:
            print(f"[BJ] refund error for {p.username}: {exc}")

    if _state.phase == "idle":
        for pd in db.load_casino_players("bj"):
            try:
                db.adjust_balance(pd["user_id"], int(pd["bet"]))
                db.add_ledger_entry(pd["user_id"], pd["username"],
                                    int(pd["bet"]), "bj_recovery_refund")
                refunded += int(pd["bet"])
            except Exception as exc:
                print(f"[BJ] DB refund error for {pd.get('username')}: {exc}")

    _cancel_task(_state.lobby_task, "Countdown")
    _cancel_task(_state.turn_task, "Turn timer")
    db.clear_casino_table("bj")
    _state.reset()
    print(f"[BJ] /bj refund: {refunded:,}c total")
    await bot.highrise.chat(f"♻️ BJ refunded. Total returned: {refunded:,}c.")
    await bot.highrise.send_whisper(user.id, f"✅ BJ cleared. {refunded:,}c refunded.")


async def _cmd_bj_forcefinish(bot: BaseBot, user: User):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return

    if _state.phase == "round":
        await bot.highrise.send_whisper(user.id, "♻️ Forcing BJ dealer resolution...")
        asyncio.create_task(_finalize_round(bot))
        return

    row = db.load_casino_table("bj")
    if not row or not row.get("active"):
        await bot.highrise.send_whisper(user.id, "No active BJ state. Use /bj refund instead.")
        return

    await bot.highrise.send_whisper(user.id, "♻️ Loading BJ state for force-finish...")
    try:
        db.save_casino_table("bj", {**dict(row), "recovery_required": 0})
    except Exception:
        pass
    await startup_bj_recovery(bot)
    if _state.phase == "round":
        asyncio.create_task(_finalize_round(bot))
    else:
        await bot.highrise.send_whisper(user.id, "Could not restore state. Use /bj refund.")


# ─── Admin setting commands (/setbjXXX) ───────────────────────────────────────

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

        elif cmd == "setbjturntimer":
            if not raw.isdigit() or not (10 <= int(raw) <= 60):
                await bot.highrise.send_whisper(
                    user.id, "Turn timer must be 10–60 seconds."
                )
                return
            val = int(raw)
            db.set_bj_setting("bj_turn_timer", val)
            await bot.highrise.send_whisper(
                user.id, f"✅ BJ turn timer set to {val}s."
            )

        elif cmd == "setbjdailywinlimit":
            if not raw.isdigit() or not (100 <= int(raw) <= 1_000_000):
                await bot.highrise.send_whisper(
                    user.id, "Use /setbjdailywinlimit <amount>."
                )
                return
            val = int(raw)
            db.set_bj_setting("bj_daily_win_limit", val)
            await bot.highrise.send_whisper(
                user.id, f"✅ BJ daily win limit set to {val:,}c."
            )

        elif cmd == "setbjdailylosslimit":
            if not raw.isdigit() or not (100 <= int(raw) <= 1_000_000):
                await bot.highrise.send_whisper(
                    user.id, "Use /setbjdailylosslimit <amount>."
                )
                return
            val = int(raw)
            db.set_bj_setting("bj_daily_loss_limit", val)
            await bot.highrise.send_whisper(
                user.id, f"✅ BJ daily loss limit set to {val:,}c."
            )

        else:
            await bot.highrise.send_whisper(
                user.id,
                "BJ settings: /setbjminbet /setbjmaxbet\n"
                "/setbjcountdown /setbjturntimer\n"
                "/setbjdailywinlimit /setbjdailylosslimit"
            )

    except Exception as exc:
        print(f"[BJ] {cmd} error for {user.username}: {exc}")
        try:
            await bot.highrise.send_whisper(user.id, "Setting update failed. Try again!")
        except Exception:
            pass
