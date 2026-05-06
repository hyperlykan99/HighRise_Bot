"""
modules/blackjack.py
--------------------
Casual lobby-style blackjack — SIMULTANEOUS action model.

State lifecycle:
  idle → lobby (first /bjoin) → round (countdown ends) → idle (round ends)

All players act simultaneously during a shared action timer after deal.
Supports split (multiple hands per player) and double down.

Public:  /bj join <bet>  /bj leave  /bj players  /bj table  /bj hand
         /bj hit  /bj stand  /bj double  /bj split
         /bj rules  /bj stats  /bj limits  /bj leaderboard
Manager: /bj on  /bj off  /bj cancel  /bj settings
         /bj double on|off  /bj split on|off  /bj splitaces on|off
         /bj state  /bj recover  /bj refund  /bj forcefinish
Admin:   /setbjminbet  /setbjmaxbet  /setbjcountdown  /setbjturntimer
         /setbjactiontimer  /setbjmaxsplits
         /setbjdailywinlimit  /setbjdailylosslimit
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

_BJ_CASINO_CAP = 5.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


# ─── In-memory state ─────────────────────────────────────────────────────────

@dataclass
class _Player:
    user_id:         str
    username:        str
    bet:             int
    hands:           list = field(default_factory=list)
    active_hand_idx: int  = 0
    split_count:     int  = 0

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


class _BJState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.phase:              str  = "idle"
        self.players:            list = []
        self.dealer_hand:        list = []
        self.deck:               list = []
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


_state = _BJState()


# ─── DB persistence ───────────────────────────────────────────────────────────

def _save_table_state() -> None:
    try:
        db.save_casino_table("bj", {
            "phase":                _state.phase,
            "round_id":             _state.round_id,
            "current_player_index": 0,
            "dealer_hand_json":     json.dumps(_state.dealer_hand),
            "deck_json":            json.dumps(_state.deck),
            "shoe_json":            "[]",
            "shoe_cards_remaining": 0,
            "countdown_ends_at":    _state._countdown_ends_at,
            "turn_ends_at":         _state._action_ends_at,
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
            "bet":       p.total_bet(),
            "hand_json": json.dumps({"hands": p.hands, "split_count": p.split_count}),
            "status":    "done" if p.is_done() else "playing",
            "doubled":   p.active_hand_idx,
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


def _all_done() -> bool:
    return bool(_state.players) and all(p.is_done() for p in _state.players)


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

    _state.phase    = "round"
    _state.deck     = make_deck()
    _state.round_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f") + "_bj"
    _state._countdown_ends_at = ""

    for p in _state.players:
        p.hands           = [_make_hand(p.bet)]
        p.active_hand_idx = 0
        p.split_count     = 0

    for _ in range(2):
        for p in _state.players:
            p.hands[0]["cards"].append(_state.deck.pop())
        _state.dealer_hand.append(_state.deck.pop())

    for p in _state.players:
        if is_blackjack(p.hands[0]["cards"]):
            p.hands[0]["status"] = "blackjack"

    _save_table_state()
    _save_all_player_states()
    print(f"[BJ] Round started. round_id={_state.round_id}")

    await bot.highrise.chat(
        f"🃏 BJ started! Dealer shows: {card_str(_state.dealer_hand[0])}"
    )
    for p in _state.players:
        if p.hands[0]["status"] == "blackjack":
            await bot.highrise.chat(f"🤑 @{p.username} has Blackjack!")

    await _start_action_phase(bot)


# ─── Simultaneous action phase ────────────────────────────────────────────────

async def _start_action_phase(bot: BaseBot):
    s     = _settings()
    timer = int(s.get("bj_action_timer", 30))

    end_at = datetime.now(timezone.utc) + timedelta(seconds=timer)
    _state._action_ends_at = end_at.isoformat()
    _save_table_state()

    if _all_done():
        await _finalize_round(bot)
        return

    await bot.highrise.chat(
        f"🃏 BJ action open! Act within {timer}s: /bh /bs /bd /bsp"
    )

    for p in _state.players:
        if not p.is_done():
            try:
                parts = [
                    f"H{i+1} {hand_str(h['cards'])}={hand_value(h['cards'])} {h['status']}"
                    for i, h in enumerate(p.hands)
                ]
                await bot.highrise.send_whisper(
                    p.user_id,
                    f"🃏 Your BJ hand: {' | '.join(parts)}"[:249]
                )
            except Exception:
                pass

    print(f"[BJ] Action timer started ({timer}s)")
    _state.action_task = asyncio.create_task(_action_timeout(bot, timer))


async def _action_timeout(bot: BaseBot, seconds: int):
    try:
        await asyncio.sleep(seconds)
    except asyncio.CancelledError:
        raise

    print("[BJ] Action timer expired — auto-standing remaining hands")
    for p in _state.players:
        for h in p.hands:
            if h["status"] == "active":
                if len(h["cards"]) < 2:
                    h["status"] = "refunded"
                    print(f"[BJ] @{p.username} hand has {len(h['cards'])} cards — marking refunded")
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
            card = _state.deck.pop()
            _state.dealer_hand.append(card)
            dealer_total = hand_value(_state.dealer_hand)
            _save_table_state()
            await bot.highrise.chat(
                f"Dealer hits {card_str(card)}. "
                f"Hand: {hand_str(_state.dealer_hand)} = {dealer_total}"
            )

        dealer_total  = hand_value(_state.dealer_hand)
        dealer_bust   = dealer_total > 21
        _bj_event_pts = (db.is_event_active()
                         and bool(int(db.get_bj_settings().get("bj_enabled", 1))))
        round_id      = _state.round_id

        for p in _state.players:
            try:
                track_quest(p.user_id, "bj_round")
                if _bj_event_pts:
                    db.add_event_points(p.user_id, 1)
                benefits  = get_player_benefits(p.user_id)
                bonus_pct = min(
                    float(benefits.get("coinflip_payout_pct", 0.0)),
                    _BJ_CASINO_CAP
                ) / 100.0

                total_net    = 0
                result_parts = []

                for i, h in enumerate(p.hands):
                    hkey   = _hand_key(p.username, i)
                    hbet   = h["bet"]
                    hst    = h["status"]
                    htotal = hand_value(h["cards"])

                    if round_id and db.is_result_paid("bj", round_id, hkey):
                        print(f"[BJ] Skipping already-paid {hkey}")
                        continue

                    if len(h["cards"]) < 2 or hst == "refunded":
                        db.adjust_balance(p.user_id, hbet)
                        db.add_ledger_entry(p.user_id, p.username, hbet, "bj_deal_refund")
                        result_parts.append(f"H{i+1} refund(no cards)")
                        print(f"[BJ] @{p.username} H{i+1} refunded {hbet}c (cards={len(h['cards'])})")
                        if round_id:
                            db.save_round_result("bj", round_id, hkey, p.user_id,
                                                 hbet, "refund", hbet, 0)
                            db.mark_result_paid("bj", round_id, hkey)
                        continue

                    if hst == "bust":
                        if round_id:
                            db.save_round_result("bj", round_id, hkey, p.user_id,
                                                 hbet, "bust", 0, -hbet)
                        db.update_bj_stats(p.user_id, loss=1, bet=hbet, lost=hbet)
                        db.add_bj_daily_net(p.user_id, -hbet)
                        total_net -= hbet
                        result_parts.append(f"H{i+1} bust")
                        if round_id:
                            db.mark_result_paid("bj", round_id, hkey)

                    elif hst == "blackjack":
                        payout = int(hbet * bj_payout * (1.0 + bonus_pct))
                        if round_id:
                            db.save_round_result("bj", round_id, hkey, p.user_id,
                                                 hbet, "blackjack", payout, payout - hbet)
                        db.adjust_balance(p.user_id, payout)
                        db.add_coins_earned(p.user_id, payout - hbet)
                        db.update_bj_stats(p.user_id, win=1, bj=1, bet=hbet, won=payout)
                        db.add_bj_daily_net(p.user_id, payout - hbet)
                        total_net += payout - hbet
                        result_parts.append(f"H{i+1} BJ +{payout:,}c")
                        if round_id:
                            db.mark_result_paid("bj", round_id, hkey)

                    elif dealer_bust or htotal > dealer_total:
                        payout = int(hbet * win_payout * (1.0 + bonus_pct))
                        if round_id:
                            db.save_round_result("bj", round_id, hkey, p.user_id,
                                                 hbet, "win", payout, payout - hbet)
                        db.adjust_balance(p.user_id, payout)
                        db.add_coins_earned(p.user_id, payout - hbet)
                        db.update_bj_stats(p.user_id, win=1, bet=hbet, won=payout)
                        db.add_bj_daily_net(p.user_id, payout - hbet)
                        total_net += payout - hbet
                        result_parts.append(f"H{i+1} win +{payout:,}c")
                        if round_id:
                            db.mark_result_paid("bj", round_id, hkey)

                    elif htotal == dealer_total:
                        refund = hbet if push_rule == "refund" else 0
                        if round_id:
                            db.save_round_result("bj", round_id, hkey, p.user_id,
                                                 hbet, "push", refund, 0 if refund else -hbet)
                        if push_rule == "refund":
                            db.adjust_balance(p.user_id, hbet)
                        db.update_bj_stats(p.user_id, push=1, bet=hbet)
                        result_parts.append(f"H{i+1} push")
                        if round_id:
                            db.mark_result_paid("bj", round_id, hkey)

                    else:
                        if round_id:
                            db.save_round_result("bj", round_id, hkey, p.user_id,
                                                 hbet, "loss", 0, -hbet)
                        db.update_bj_stats(p.user_id, loss=1, bet=hbet, lost=hbet)
                        db.add_bj_daily_net(p.user_id, -hbet)
                        total_net -= hbet
                        result_parts.append(f"H{i+1} loss")
                        if round_id:
                            db.mark_result_paid("bj", round_id, hkey)

                if result_parts:
                    net_str = f"+{total_net:,}c" if total_net >= 0 else f"{total_net:,}c"
                    inner   = " | ".join(result_parts)
                    summary = f"@{p.username}: {inner} | Net {net_str}"
                    await bot.highrise.chat(summary[:249])

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
    if _state.phase == "idle":
        return "idle"
    for p in _state.players:
        try:
            refund = p.total_bet()
            db.adjust_balance(p.user_id, refund)
            db.add_ledger_entry(p.user_id, p.username, refund, "bj_cancel_refund")
        except Exception as exc:
            print(f"[BJ] reset_table refund error for {p.username}: {exc}")
    _cancel_task(_state.lobby_task, "Countdown")
    _cancel_task(_state.action_task, "Action timer")
    db.clear_casino_table("bj")
    _state.reset()
    print("[BJ] Table reset by admin")
    return "reset"


def soft_reset_table() -> None:
    if _state.phase == "idle":
        return
    _save_table_state()
    _save_all_player_states()
    _cancel_task(_state.lobby_task, "Countdown")
    _cancel_task(_state.action_task, "Action timer")
    _state.reset()
    print("[BJ] Table soft-reset (state saved to DB for recovery)")


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
    )


async def startup_bj_recovery(bot: BaseBot) -> None:
    row = db.load_casino_table("bj")
    if not row or not row.get("active") or row.get("phase", "idle") == "idle":
        return

    phase = row.get("phase", "idle")
    print(f"[RECOVERY] BJ found saved phase={phase}")

    if row.get("recovery_required"):
        print("[RECOVERY] BJ marked recovery_required — alerting in chat.")
        try:
            await bot.highrise.chat("⚠️ BJ recovery needed. Use /bj recover or /bj refund.")
        except Exception:
            pass
        return

    try:
        players_data = db.load_casino_players("bj")
        if not players_data:
            print("[RECOVERY] BJ: no players found, clearing state.")
            db.clear_casino_table("bj")
            return

        _state.players            = [_restore_player_from_db(pd) for pd in players_data]
        _state.round_id           = row.get("round_id", "")
        _state.dealer_hand        = json.loads(row.get("dealer_hand_json") or "[]")
        _state.deck               = json.loads(row.get("deck_json") or "[]")
        _state._countdown_ends_at = row.get("countdown_ends_at", "")
        _state._action_ends_at    = row.get("turn_ends_at", "")

        if phase == "lobby":
            _state.phase = "lobby"
            secs = _remaining_secs(_state._countdown_ends_at, default=5)
            _state.lobby_task = asyncio.create_task(_lobby_countdown(bot, secs))
            await bot.highrise.chat("♻️ BJ table restored after restart.")
            print(f"[RECOVERY] BJ lobby restored, countdown in {secs}s.")

        elif phase in ("round", "active"):
            _state.phase = "round"
            if _state.round_id:
                unpaid = db.get_unpaid_results("bj", _state.round_id)
                if unpaid:
                    print(f"[RECOVERY] BJ: completing {len(unpaid)} unpaid payouts...")
                    await _complete_unpaid_payouts(bot, "bj", unpaid)
                    db.clear_casino_table("bj")
                    _state.reset()
                    return

            await bot.highrise.chat("♻️ BJ restored. Cards and bets loaded.")

            if _all_done():
                asyncio.create_task(_finalize_round(bot))
                return

            secs = _remaining_secs(_state._action_ends_at, default=0)
            if secs > 0:
                _state.action_task = asyncio.create_task(_action_timeout(bot, secs))
                print(f"[RECOVERY] BJ action timer restarted: {secs}s")
            else:
                asyncio.create_task(_action_timeout(bot, 0))
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
                "dealer_hand_json": "[]", "deck_json": "[]",
                "shoe_json": "[]", "shoe_cards_remaining": 0,
                "countdown_ends_at": "", "turn_ends_at": "",
                "active": 1, "recovery_required": 1,
            })
        except Exception:
            pass
        try:
            await bot.highrise.chat("⚠️ BJ recovery needed. Use /bj recover or /bj refund.")
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
            print(f"[RECOVERY] unpaid payout error for {row.get('username')}: {exc}")


# ─── Top-level router ─────────────────────────────────────────────────────────

async def handle_bj(bot: BaseBot, user: User, args: list[str]):
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
                await bot.highrise.send_whisper(user.id, "Usage: /bj splitaces on|off")
        elif sub == "betlimit":
            if len(args) > 2 and args[2].lower() in ("on", "off"):
                await _cmd_toggle_betlimit(bot, user, args[2].lower() == "on")
            else:
                await bot.highrise.send_whisper(user.id, "Usage: /bj betlimit on|off")
        elif sub == "winlimit":
            if not can_manage_games(user.username):
                await bot.highrise.send_whisper(user.id, "Staff only.")
                return
            val = args[2].lower() if len(args) > 2 else ""
            if val == "on":
                db.set_bj_setting("bj_win_limit_enabled", 1)
                await bot.highrise.chat("✅ BJ win limit ON.")
            elif val == "off":
                db.set_bj_setting("bj_win_limit_enabled", 0)
                await bot.highrise.chat("⛔ BJ win limit OFF.")
            else:
                await bot.highrise.send_whisper(user.id, "Use /bj winlimit on/off.")
        elif sub == "losslimit":
            if not can_manage_games(user.username):
                await bot.highrise.send_whisper(user.id, "Staff only.")
                return
            val = args[2].lower() if len(args) > 2 else ""
            if val == "on":
                db.set_bj_setting("bj_loss_limit_enabled", 1)
                await bot.highrise.chat("✅ BJ loss limit ON.")
            elif val == "off":
                db.set_bj_setting("bj_loss_limit_enabled", 0)
                await bot.highrise.chat("⛔ BJ loss limit OFF.")
            else:
                await bot.highrise.send_whisper(user.id, "Use /bj losslimit on/off.")
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
        elif sub == "state":
            await _cmd_bj_state(bot, user)
        elif sub == "recover":
            await _cmd_bj_recover(bot, user)
        elif sub == "refund":
            await _cmd_bj_refund(bot, user)
        elif sub == "forcefinish":
            await _cmd_bj_forcefinish(bot, user)
        elif sub == "integrity":
            if not can_manage_games(user.username):
                await bot.highrise.send_whisper(user.id, "Staff only.")
                return
            sub2 = args[2].lower() if len(args) > 2 else ""
            from modules.casino_integrity import run_bj_integrity
            await run_bj_integrity(bot, user, sub2)
        else:
            await bot.highrise.send_whisper(
                user.id,
                "🃏 BJ: /bjoin <bet>  /bh hit  /bs stand\n"
                "/bd double  /bsp split\n"
                "/bt table  /bhand  /bj rules  /bstats"
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

    if len(args) < 3 or not args[2].isdigit() or int(args[2]) < 1:
        await bot.highrise.send_whisper(user.id, "Use /bjoin <bet>.")
        return

    bet     = int(args[2])
    min_bet = int(s.get("min_bet", 10))
    max_bet = int(s.get("max_bet", 1000))

    from modules.events import get_event_effect as _gee
    _ev_bj      = _gee()
    eff_max_bet = int(max_bet * _ev_bj["casino_bet_mult"])

    bet_limit_on = int(s.get("bj_betlimit_enabled", 1))
    if bet_limit_on and (bet < min_bet or bet > eff_max_bet):
        note = " (Casino Hour 2x limit!)" if _ev_bj["casino_bet_mult"] > 1 else ""
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
        _state.phase = "lobby"
        countdown    = int(s.get("lobby_countdown", 15))
        _cancel_task(_state.lobby_task, "Countdown")
        _save_table_state()
        _state.lobby_task = asyncio.create_task(_lobby_countdown(bot, countdown))
        await bot.highrise.chat(f"🃏 BJ lobby open! /bjoin <bet>. Starts in {countdown}s.")
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
        await bot.highrise.send_whisper(user.id, "No BJ table active. Use /bjoin <bet>.")
        return
    lines = [f"-- BJ Players ({_state.phase}) --"]
    for p in _state.players:
        lines.append(f"  @{p.username}  {p.total_bet():,}c")
    if not _state.players:
        lines.append("  (none)")
    await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])


async def _cmd_table(bot: BaseBot, user: User):
    if _state.phase == "idle":
        await bot.highrise.send_whisper(user.id, "No BJ table active. Use /bjoin <bet>.")
        return
    if _state.phase == "lobby":
        count = len(_state.players)
        names = ", ".join(f"@{p.username}" for p in _state.players) or "none"
        await bot.highrise.send_whisper(user.id,
            f"🃏 BJ Lobby — {count} player(s)\n{names}"[:249])
        return

    secs  = _remaining_secs(_state._action_ends_at, 0)
    dc    = card_str(_state.dealer_hand[0]) if _state.dealer_hand else "?"
    lines = [f"BJ | Dealer: {dc} ? | {secs}s left"]
    for p in _state.players:
        if len(p.hands) == 1:
            h     = p.hands[0]
            total = hand_value(h["cards"])
            lines.append(f"@{p.username} {total} {h['status']}")
        else:
            parts = [
                f"H{i+1} {hand_value(h['cards'])} {h['status']}"
                for i, h in enumerate(p.hands)
            ]
            lines.append(f"@{p.username}: " + " | ".join(parts))
    msg = "\n".join(lines)
    await bot.highrise.send_whisper(user.id, msg[:249])


async def _cmd_hand(bot: BaseBot, user: User):
    if _state.phase != "round":
        await bot.highrise.send_whisper(user.id, "No BJ round active.")
        return
    p = _state.get_player(user.id)
    if p is None:
        await bot.highrise.send_whisper(user.id, "You are not in BJ.")
        return
    parts = []
    for i, h in enumerate(p.hands):
        val = hand_value(h["cards"])
        st  = h["status"]
        if st == "active" and i != p.active_hand_idx:
            st = "wait"
        parts.append(f"H{i+1} {hand_str(h['cards'])}={val} {st}")
    msg = f"BJ: {' | '.join(parts)}"
    if len(msg) <= 249:
        await bot.highrise.send_whisper(user.id, msg)
    else:
        for part in parts:
            await bot.highrise.send_whisper(user.id, f"BJ {part}"[:249])


async def _cmd_hit(bot: BaseBot, user: User):
    if _state.phase != "round":
        await bot.highrise.send_whisper(user.id, "No blackjack round active.")
        return
    p = _state.get_player(user.id)
    if p is None:
        await bot.highrise.send_whisper(user.id, "You are not in BJ.")
        return
    h = p.current_hand()
    if h is None or h["status"] != "active":
        await bot.highrise.send_whisper(user.id, "No active hand to hit.")
        return

    card  = _state.deck.pop()
    h["cards"].append(card)
    total = hand_value(h["cards"])
    hidx  = p.active_hand_idx + 1
    print(f"[BJ] @{p.username} H{hidx} hit → {card_str(card)} total={total}")

    if total > 21:
        h["status"] = "bust"
        _save_player_state(p)
        _save_table_state()
        await bot.highrise.chat(
            f"🃏 @{p.username} H{hidx}: {hand_str(h['cards'])} = {total} — bust!"
        )
        p.advance_hand()
        if not await _check_and_resolve(bot):
            _save_player_state(p)
    elif total == 21:
        h["status"] = "stood"
        _save_player_state(p)
        _save_table_state()
        await bot.highrise.chat(
            f"🃏 @{p.username} H{hidx}: 21 — auto-stand!"
        )
        p.advance_hand()
        if not await _check_and_resolve(bot):
            _save_player_state(p)
    else:
        _save_player_state(p)
        _save_table_state()
        await bot.highrise.chat(
            f"🃏 @{p.username} hits: {hand_str(h['cards'])} = {total}"
        )


async def _cmd_stand(bot: BaseBot, user: User):
    if _state.phase != "round":
        await bot.highrise.send_whisper(user.id, "No blackjack round active.")
        return
    p = _state.get_player(user.id)
    if p is None:
        await bot.highrise.send_whisper(user.id, "You are not in BJ.")
        return
    h = p.current_hand()
    if h is None or h["status"] != "active":
        await bot.highrise.send_whisper(user.id, "No active hand to stand.")
        return

    total       = hand_value(h["cards"])
    h["status"] = "stood"
    await bot.highrise.chat(f"✋ @{p.username} stands at {total}.")
    p.advance_hand()
    _save_player_state(p)
    _save_table_state()
    await _check_and_resolve(bot)


async def _cmd_double(bot: BaseBot, user: User):
    if _state.phase != "round":
        await bot.highrise.send_whisper(user.id, "No blackjack round active.")
        return
    s = _settings()
    if not int(s.get("bj_double_enabled", 1)):
        await bot.highrise.send_whisper(user.id, "Double is currently disabled.")
        return
    p = _state.get_player(user.id)
    if p is None:
        await bot.highrise.send_whisper(user.id, "You are not in BJ.")
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
    card  = _state.deck.pop()
    h["cards"].append(card)
    total = hand_value(h["cards"])
    hidx  = p.active_hand_idx + 1
    print(f"[BJ] @{p.username} H{hidx} doubled total={total}")

    if total > 21:
        h["status"] = "bust"
        await bot.highrise.chat(
            f"💰 @{p.username} doubles to {h['bet']:,}c, "
            f"draws {card_str(card)}. H{hidx}: {total} — bust!"
        )
    else:
        h["status"] = "stood"
        await bot.highrise.chat(
            f"💰 @{p.username} doubles to {h['bet']:,}c, "
            f"draws {card_str(card)}. H{hidx}: {total}."
        )

    p.advance_hand()
    _save_player_state(p)
    _save_table_state()
    await _check_and_resolve(bot)


async def _cmd_split(bot: BaseBot, user: User):
    if _state.phase != "round":
        await bot.highrise.send_whisper(user.id, "No blackjack round active.")
        return
    s = _settings()
    if not int(s.get("bj_split_enabled", 1)):
        await bot.highrise.send_whisper(user.id, "Split is currently disabled.")
        return
    p = _state.get_player(user.id)
    if p is None:
        await bot.highrise.send_whisper(user.id, "You are not in BJ.")
        return
    h = p.current_hand()
    if h is None or h["status"] != "active":
        await bot.highrise.send_whisper(user.id, "No active hand to split.")
        return
    if len(h["cards"]) != 2:
        await bot.highrise.send_whisper(user.id, "❌ Split needs exactly 2 cards.")
        return

    max_splits = int(s.get("bj_max_splits", 1))
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

    # Deal one new card to each split hand and save immediately
    new_card_a = _state.deck.pop()
    p.hands[idx]["cards"].append(new_card_a)
    new_card_b = _state.deck.pop()
    p.hands[idx + 1]["cards"].append(new_card_b)

    split_aces = int(s.get("bj_split_aces_one_card", 1))
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
        _save_table_state()
        await bot.highrise.chat(f"✂️ @{p.username} splits Aces. One card each.")
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
        _save_table_state()
        await bot.highrise.chat(f"✂️ @{p.username} splits {r1}s.")
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
    db.set_bj_setting("bj_double_enabled", 1 if enabled else 0)
    await bot.highrise.send_whisper(user.id,
        f"✅ BJ double is now {'ON' if enabled else 'OFF'}.")


async def _cmd_toggle_split(bot: BaseBot, user: User, enabled: bool):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return
    db.set_bj_setting("bj_split_enabled", 1 if enabled else 0)
    await bot.highrise.send_whisper(user.id,
        f"✅ BJ split is now {'ON' if enabled else 'OFF'}.")


async def _cmd_toggle_splitaces(bot: BaseBot, user: User, enabled: bool):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return
    db.set_bj_setting("bj_split_aces_one_card", 1 if enabled else 0)
    await bot.highrise.send_whisper(user.id,
        f"✅ BJ split aces one-card rule is now {'ON' if enabled else 'OFF'}.")


async def _cmd_toggle_betlimit(bot: BaseBot, user: User, enabled: bool):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return
    db.set_bj_setting("bj_betlimit_enabled", 1 if enabled else 0)
    if enabled:
        await bot.highrise.chat("✅ BJ bet limit ON.")
    else:
        await bot.highrise.chat("⛔ BJ bet limit OFF.")


async def _cmd_rules(bot: BaseBot, user: User):
    s = _settings()
    await bot.highrise.send_whisper(user.id,
        f"🃏 BJ Rules\n"
        f"Bet: {s.get('min_bet',10):,}–{s.get('max_bet',1000):,}c\n"
        f"Win: {s.get('win_payout',2.0)}x  BJ: {s.get('blackjack_payout',2.5)}x\n"
        f"Push: {s.get('push_rule','refund')}  "
        f"Soft17: {'hit' if s.get('dealer_hits_soft_17',1) else 'stand'}\n"
        f"Timer: {s.get('bj_action_timer',30)}s  "
        f"Double: {'ON' if int(s.get('bj_double_enabled',1)) else 'OFF'}  "
        f"Split: {'ON' if int(s.get('bj_split_enabled',1)) else 'OFF'}"
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
    won  = "ON" if int(s.get("bj_win_limit_enabled", 1)) else "OFF"
    lon  = "ON" if int(s.get("bj_loss_limit_enabled", 1)) else "OFF"
    blon = int(s.get("bj_betlimit_enabled", 1))
    sign = "+" if net >= 0 else ""
    if blon:
        bet_str = f"BJ bet {s.get('min_bet',10):,}–{s.get('max_bet',1000):,}c ON"
    else:
        bet_str = "BJ bet limit OFF"
    await bot.highrise.send_whisper(user.id,
        f"{bet_str} | W/L {wlim:,}/{llim:,} {won}/{lon}\n"
        f"Today: {sign}{net:,}c"
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
        refund = p.total_bet()
        db.adjust_balance(p.user_id, refund)
        db.add_ledger_entry(p.user_id, p.username, refund, "bj_cancel_refund")
    _cancel_task(_state.lobby_task, "Countdown")
    _cancel_task(_state.action_task, "Action timer")
    db.clear_casino_table("bj")
    _state.reset()
    await bot.highrise.chat("🃏 BJ cancelled. All bets refunded.")


async def _cmd_settings(bot: BaseBot, user: User):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Admins and managers only.")
        return
    s       = _settings()
    enabled = "ON" if int(s.get("bj_enabled", 1)) else "OFF"
    dbl     = "ON" if int(s.get("bj_double_enabled", 1)) else "OFF"
    spl     = "ON" if int(s.get("bj_split_enabled", 1)) else "OFF"
    win_on  = "ON" if int(s.get("bj_win_limit_enabled", 1)) else "OFF"
    loss_on = "ON" if int(s.get("bj_loss_limit_enabled", 1)) else "OFF"
    await bot.highrise.send_whisper(user.id,
        f"-- BJ Settings --\n"
        f"BJ {enabled} | Timer {s.get('bj_action_timer',30)}s | "
        f"Double {dbl} | Split {spl} | MaxSplits {s.get('bj_max_splits',1)}\n"
        f"min:{s.get('min_bet',10):,}c  max:{s.get('max_bet',1000):,}c\n"
        f"win:{s.get('win_payout',2.0)}x  bj:{s.get('blackjack_payout',2.5)}x\n"
        f"lobby:{s.get('lobby_countdown',15)}s  max:{s.get('max_players',6)}p\n"
        f"W/L limit: {win_on}/{loss_on}"
    )


async def _cmd_bj_mode(bot: BaseBot, user: User, enabled: bool):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Admins and managers only.")
        return
    db.set_bj_setting("bj_enabled", 1 if enabled else 0)
    status = "ON" if enabled else "OFF"
    await bot.highrise.chat(f"{'✅' if enabled else '⛔'} Casual BJ is now {status}.")


# ─── Recovery staff commands ──────────────────────────────────────────────────

async def _cmd_bj_state(bot: BaseBot, user: User):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return
    if _state.phase == "idle":
        row = db.load_casino_table("bj")
        if row and row.get("active"):
            await bot.highrise.send_whisper(user.id,
                f"BJ: idle in memory | DB phase:{row.get('phase')}\n"
                "Use /bj recover or /bj refund.")
        else:
            await bot.highrise.send_whisper(user.id, "BJ: no active table.")
        return

    total_bets = sum(p.total_bet() for p in _state.players)
    active_ps  = [p for p in _state.players if not p.is_done()]
    dc         = card_str(_state.dealer_hand[0]) if _state.dealer_hand else "?"
    secs       = _remaining_secs(_state._action_ends_at, 0)
    rid        = _state.round_id[-10:] if _state.round_id else "?"
    msg = (
        f"BJ {_state.phase} | Players:{len(_state.players)}\n"
        f"Active:{len(active_ps)} | Timer:{secs}s | Dealer:{dc}\n"
        f"Bets:{total_bets:,}c | id:{rid}"
    )
    await bot.highrise.send_whisper(user.id, msg[:249])


async def _cmd_bj_recover(bot: BaseBot, user: User):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return
    if _state.phase != "idle":
        await bot.highrise.send_whisper(user.id,
            "BJ is active. Use /bj state to inspect, /bj refund to cancel.")
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
            refund = p.total_bet()
            db.adjust_balance(p.user_id, refund)
            db.add_ledger_entry(p.user_id, p.username, refund, "bj_recovery_refund")
            refunded += refund
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
    _cancel_task(_state.action_task, "Action timer")
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
            if val >= int(_settings().get("max_bet", 1000)):
                await bot.highrise.send_whisper(user.id, "Min bet must be less than max bet.")
                return
            db.set_bj_setting("min_bet", val)
            await bot.highrise.send_whisper(user.id, f"✅ BJ min bet set to {val:,}c.")

        elif cmd == "setbjmaxbet":
            if not raw.isdigit() or int(raw) < 1:
                await bot.highrise.send_whisper(user.id, "Max bet must be >= 1.")
                return
            val = int(raw)
            if val <= int(_settings().get("min_bet", 10)):
                await bot.highrise.send_whisper(user.id, "Max bet must be greater than min bet.")
                return
            db.set_bj_setting("max_bet", val)
            await bot.highrise.send_whisper(user.id, f"✅ BJ max bet set to {val:,}c.")

        elif cmd == "setbjcountdown":
            if not raw.isdigit() or not (5 <= int(raw) <= 120):
                await bot.highrise.send_whisper(user.id, "Countdown must be 5–120 seconds.")
                return
            db.set_bj_setting("lobby_countdown", int(raw))
            await bot.highrise.send_whisper(user.id, f"✅ BJ lobby countdown set to {raw}s.")

        elif cmd == "setbjturntimer":
            if not raw.isdigit() or not (10 <= int(raw) <= 60):
                await bot.highrise.send_whisper(user.id, "Turn timer must be 10–60 seconds.")
                return
            db.set_bj_setting("bj_turn_timer", int(raw))
            await bot.highrise.send_whisper(user.id, f"✅ BJ turn timer set to {raw}s.")

        elif cmd == "setbjactiontimer":
            if not raw.isdigit() or not (10 <= int(raw) <= 90):
                await bot.highrise.send_whisper(user.id, "Action timer must be 10–90 seconds.")
                return
            db.set_bj_setting("bj_action_timer", int(raw))
            await bot.highrise.send_whisper(user.id, f"✅ BJ action timer set to {raw}s.")

        elif cmd == "setbjmaxsplits":
            if not raw.isdigit() or not (0 <= int(raw) <= 3):
                await bot.highrise.send_whisper(user.id, "Max splits must be 0–3.")
                return
            db.set_bj_setting("bj_max_splits", int(raw))
            await bot.highrise.send_whisper(user.id, f"✅ BJ max splits set to {raw}.")

        elif cmd == "setbjdailywinlimit":
            if not raw.isdigit() or not (100 <= int(raw) <= 1_000_000):
                await bot.highrise.send_whisper(user.id, "Use /setbjdailywinlimit <amount>.")
                return
            db.set_bj_setting("bj_daily_win_limit", int(raw))
            await bot.highrise.send_whisper(user.id, f"✅ BJ daily win limit set to {int(raw):,}c.")

        elif cmd == "setbjdailylosslimit":
            if not raw.isdigit() or not (100 <= int(raw) <= 1_000_000):
                await bot.highrise.send_whisper(user.id, "Use /setbjdailylosslimit <amount>.")
                return
            db.set_bj_setting("bj_daily_loss_limit", int(raw))
            await bot.highrise.send_whisper(user.id, f"✅ BJ daily loss limit set to {int(raw):,}c.")

        else:
            await bot.highrise.send_whisper(
                user.id,
                "BJ settings: /setbjminbet /setbjmaxbet\n"
                "/setbjcountdown /setbjactiontimer\n"
                "/setbjmaxsplits /setbjdailywinlimit /setbjdailylosslimit"
            )

    except Exception as exc:
        print(f"[BJ] {cmd} error for {user.username}: {exc}")
        try:
            await bot.highrise.send_whisper(user.id, "Setting update failed. Try again!")
        except Exception:
            pass
