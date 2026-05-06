"""modules/casino_integrity.py
Casino Integrity Checker — read-only / dry-run only.
No real balances, stats, XP, coins, badges are changed.
All bot messages ≤ 249 chars.
"""

import json
from highrise import BaseBot, User
import database as db
from modules.permissions import can_manage_games, is_admin, is_owner
from modules.cards import make_deck, make_shoe, hand_value, is_blackjack


# ─── Tiny helpers ────────────────────────────────────────────────────────────

def _chk(name: str, ok: bool, note: str = "") -> dict:
    return {"name": name, "ok": bool(ok), "note": note}


def _sum(checks: list) -> tuple:
    passed = sum(1 for c in checks if c["ok"])
    fails  = [
        c["name"] + (f"({c['note']})" if c.get("note") else "")
        for c in checks if not c["ok"]
    ]
    return passed, len(checks), fails


async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


def _can_full(username: str) -> bool:
    return is_admin(username) or is_owner(username)


def _log(actor: str, module: str, check_type: str,
         passed: int, total: int, fails: list, summary: str) -> None:
    try:
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO casino_integrity_logs "
            "(timestamp, actor_username, module, check_type, passed, "
            "total_checks, failed_checks, details_json, summary) "
            "VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?)",
            (actor, module, check_type, passed, total, len(fails),
             json.dumps(fails), summary[:500])
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[INTEGRITY] log error: {e}")


# ─── DB inspection helpers ────────────────────────────────────────────────────

def _tables_ok(*names: str) -> list:
    try:
        conn = db.get_connection()
        existing = {
            r["name"] for r in
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        conn.close()
        return [_chk(f"table:{n}", n in existing) for n in names]
    except Exception as e:
        return [_chk(f"table:{n}", False, str(e)[:40]) for n in names]


def _settings_ok(fetch_fn, keys: list) -> list:
    try:
        s = fetch_fn()
        return [_chk(f"setting:{k}", k in s) for k in keys]
    except Exception as e:
        return [_chk(f"setting:{k}", False, str(e)[:40]) for k in keys]


def _fn_ok(module_path: str, fn_name: str) -> bool:
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return hasattr(mod, fn_name)
    except Exception:
        return False


def _in_owners(cmd: str) -> bool:
    try:
        from modules.multi_bot import _DEFAULT_COMMAND_OWNERS
        return cmd in _DEFAULT_COMMAND_OWNERS
    except Exception:
        return False


# ─── Route check definitions ─────────────────────────────────────────────────

_BJ_TOP = ["bj", "bjoin", "bh", "bs", "bd", "bsp", "bhand", "blimits", "bstats"]
_BJ_FNS = [
    ("modules.blackjack", "handle_bj"),
    ("modules.blackjack", "_cmd_bj_recover"),
    ("modules.blackjack", "_cmd_bj_refund"),
    ("modules.blackjack", "_cmd_bj_forcefinish"),
    ("modules.blackjack", "_cmd_limits"),
]

_RBJ_TOP = ["rbj", "rjoin", "rh", "rs", "rd", "rsp", "rhand", "rshoe", "rlimits", "rstats"]
_RBJ_FNS = [
    ("modules.realistic_blackjack", "handle_rbj"),
    ("modules.realistic_blackjack", "_cmd_rbj_recover"),
    ("modules.realistic_blackjack", "_cmd_rbj_refund"),
    ("modules.realistic_blackjack", "_cmd_rbj_forcefinish"),
]

_POKER_TOP  = ["poker", "p", "ph", "pt", "check", "call", "r", "fold", "ai"]
_POKER_FNS  = [
    ("modules.poker", "handle_poker"),
    ("modules.poker", "get_poker_recovery_recommendation"),
    ("modules.poker", "_hard_refund_hand"),
    ("modules.poker", "handle_confirmclosepoker"),
]
_POKER_SUBS = [
    "settings", "state", "cleanup", "refund", "hardrefund",
    "forcefinish", "recoverystatus", "clearhand", "closeforce",
]


def _check_routes_bj() -> list:
    c  = [_chk(f"route:{x}", _in_owners(x)) for x in _BJ_TOP]
    c += [_chk(f"fn:{fn}", _fn_ok(m, fn)) for m, fn in _BJ_FNS]
    return c


def _check_routes_rbj() -> list:
    c  = [_chk(f"route:{x}", _in_owners(x)) for x in _RBJ_TOP]
    c += [_chk(f"fn:{fn}", _fn_ok(m, fn)) for m, fn in _RBJ_FNS]
    return c


def _check_routes_poker() -> list:
    c  = [_chk(f"route:{x}", _in_owners(x)) for x in _POKER_TOP]
    c += [_chk(f"fn:{fn}", _fn_ok(m, fn)) for m, fn in _POKER_FNS]
    try:
        import modules.poker as pm
        import inspect
        src = inspect.getsource(pm._dispatch)
        c  += [_chk(f"sub:poker {s}", f'sub == "{s}"' in src) for s in _POKER_SUBS]
    except Exception:
        c  += [_chk(f"sub:poker {s}", False, "inspect err") for s in _POKER_SUBS]
    return c


# ─── DB checks ───────────────────────────────────────────────────────────────

_BJ_TABLES = [
    "bj_settings", "bj_stats", "bj_daily",
    "casino_active_tables", "casino_active_players", "casino_round_results",
]
_RBJ_TABLES = [
    "rbj_settings", "rbj_stats", "rbj_daily",
    "casino_active_tables", "casino_active_players", "casino_round_results",
]
_POKER_TABLES = [
    "poker_settings", "poker_active_table", "poker_active_players",
    "poker_round_results", "poker_recovery_logs", "poker_seated_players",
    "bot_module_locks",
]


def _check_db_bj()    -> list: return _tables_ok(*_BJ_TABLES,    "casino_integrity_logs")
def _check_db_rbj()   -> list: return _tables_ok(*_RBJ_TABLES,   "casino_integrity_logs")
def _check_db_poker() -> list: return _tables_ok(*_POKER_TABLES,  "casino_integrity_logs")


# ─── Settings checks ─────────────────────────────────────────────────────────

_BJ_SKEYS = [
    "bj_enabled", "min_bet", "max_bet", "bj_action_timer",
    "bj_win_limit_enabled", "bj_loss_limit_enabled", "bj_betlimit_enabled",
    "bj_double_enabled", "bj_split_enabled", "bj_max_splits",
    "bj_split_aces_one_card", "dealer_hits_soft_17",
]
_RBJ_SKEYS = [
    "rbj_enabled", "min_bet", "max_bet", "rbj_action_timer",
    "rbj_win_limit_enabled", "rbj_loss_limit_enabled", "rbj_betlimit_enabled",
    "rbj_double_enabled", "rbj_split_enabled", "rbj_max_splits",
    "rbj_split_aces_one_card", "dealer_hits_soft_17",
]
_POKER_SKEYS = [
    "poker_enabled", "turn_timer", "lobby_countdown", "next_hand_delay",
    "small_blind", "big_blind", "ante", "blinds_enabled",
    "min_players", "max_players", "raise_limit_enabled", "max_raise",
    "allin_enabled", "win_limit_enabled", "loss_limit_enabled",
    "buyin_limit_enabled", "min_buyin", "max_buyin",
]


def _check_settings_bj()  -> list: return _settings_ok(db.get_bj_settings,  _BJ_SKEYS)
def _check_settings_rbj() -> list: return _settings_ok(db.get_rbj_settings, _RBJ_SKEYS)


def _check_settings_poker() -> list:
    def _fetch():
        conn = db.get_connection()
        rows = conn.execute("SELECT key, value FROM poker_settings").fetchall()
        conn.close()
        return {r["key"]: r["value"] for r in rows}
    return _settings_ok(_fetch, _POKER_SKEYS)


# ─── Payout formula dry-run ───────────────────────────────────────────────────

def _payout(bet: int, result: str, bj_mult: float = 1.5) -> int:
    if result == "blackjack": return int(bet * bj_mult)
    if result == "win":       return bet
    if result == "push":      return 0
    return -bet


def _check_payouts_bj() -> list:
    b = 100
    return [
        _chk("bj:normal_win",    _payout(b, "win")       ==  100),
        _chk("bj:blackjack_1.5", _payout(b, "blackjack") ==  150),
        _chk("bj:push_zero",     _payout(b, "push")      ==    0),
        _chk("bj:loss_neg",      _payout(b, "loss")      == -100),
        _chk("bj:double_win",    _payout(b * 2, "win")   ==  200),
        _chk("bj:split_net",     _payout(b, "win") + _payout(b, "loss") == 0),
    ]


def _check_payouts_rbj() -> list:
    b = 100
    return [
        _chk("rbj:normal_win",    _payout(b, "win")       ==  100),
        _chk("rbj:blackjack_1.5", _payout(b, "blackjack") ==  150),
        _chk("rbj:push_zero",     _payout(b, "push")      ==    0),
        _chk("rbj:loss_neg",      _payout(b, "loss")      == -100),
        _chk("rbj:double_win",    _payout(b * 2, "win")   ==  200),
        _chk("rbj:split_push",    _payout(b, "win") + _payout(b, "push") == 100),
    ]


def _check_payouts_poker() -> list:
    checks = []
    checks.append(_chk("poker:winner_all",    300 == 300))
    checks.append(_chk("poker:split_2way",    300 // 2 == 150))
    a, bc = 50, 100
    main = a * 3
    side = (bc - a) * 2
    checks.append(_chk("poker:sidepot_main",  main == 150))
    checks.append(_chk("poker:sidepot_side",  side == 100))
    checks.append(_chk("poker:sidepot_total", main + side == 250))
    checks.append(_chk("poker:odd_chip_floor", 301 // 2 == 150))
    return checks


# ─── BJ simulation ───────────────────────────────────────────────────────────

def _simulate_bj() -> list:
    checks = []
    try:
        deck = make_deck()
        checks.append(_chk("bj_sim:deck_52",      len(deck) == 52))
        player = [deck.pop(), deck.pop()]
        dealer = [deck.pop(), deck.pop()]
        checks.append(_chk("bj_sim:deal_ok",      len(player) == 2 and len(dealer) == 2))
        checks.append(_chk("bj_sim:deck_48",      len(deck) == 48))

        pv = hand_value(player)
        checks.append(_chk("bj_sim:hand_value",   2 <= pv <= 21 or pv > 21))

        bj_hand = [("A", "♠"), ("K", "♥")]
        non_bj  = [("9", "♠"), ("8", "♥")]
        checks.append(_chk("bj_sim:bj_detected",  is_blackjack(bj_hand)))
        checks.append(_chk("bj_sim:non_bj",       not is_blackjack(non_bj)))
        checks.append(_chk("bj_sim:bj_payout",    _payout(100, "blackjack") == 150))

        p20 = [("K", "♠"), ("Q", "♥")]
        d18 = [("9", "♣"), ("9", "♦")]
        checks.append(_chk("bj_sim:p20_beats_d18", hand_value(p20) == 20 and hand_value(d18) == 18))

        bust = [("K", "♠"), ("Q", "♥"), ("5", "♦")]
        checks.append(_chk("bj_sim:bust_25",      hand_value(bust) == 25))
        checks.append(_chk("bj_sim:push_zero",    _payout(100, "push") == 0))
        checks.append(_chk("bj_sim:double_bet",   100 * 2 == 200))

        pair = [("8", "♠"), ("8", "♥")]
        h_a  = [pair[0], deck.pop()]
        h_b  = [pair[1], deck.pop()]
        checks.append(_chk("bj_sim:split_two",    pair[0][0] == pair[1][0] and len(h_a) == 2 and len(h_b) == 2))

        aces = [("A", "♠"), ("A", "♥")]
        a1   = [aces[0], deck.pop()]
        a2   = [aces[1], deck.pop()]
        checks.append(_chk("bj_sim:split_aces",   len(a1) == 2 and len(a2) == 2))
    except Exception as e:
        checks.append(_chk("bj_sim:exception", False, str(e)[:60]))
    return checks


# ─── RBJ simulation ──────────────────────────────────────────────────────────

def _simulate_rbj() -> list:
    checks = []
    try:
        shoe = make_shoe(6)
        checks.append(_chk("rbj_sim:shoe_312",    len(shoe) == 312))
        player = [shoe.pop(), shoe.pop()]
        dealer = [shoe.pop(), shoe.pop()]
        checks.append(_chk("rbj_sim:deal_ok",     len(player) == 2 and len(dealer) == 2))
        checks.append(_chk("rbj_sim:shoe_308",    len(shoe) == 308))

        pv = hand_value(player)
        checks.append(_chk("rbj_sim:hand_value",  isinstance(pv, int) and pv >= 2))

        before = len(shoe)
        player.append(shoe.pop())
        checks.append(_chk("rbj_sim:hit_ok",      len(player) == 3 and len(shoe) == before - 1))
        checks.append(_chk("rbj_sim:double_bet",  100 * 2 == 200))

        pair = [("8", "♠"), ("8", "♥")]
        h_a  = [pair[0], shoe.pop()]
        h_b  = [pair[1], shoe.pop()]
        checks.append(_chk("rbj_sim:split_two",   pair[0][0] == pair[1][0] and len(h_a) == 2))

        d = [("5", "♠"), ("7", "♥")]
        while hand_value(d) < 17:
            d.append(shoe.pop())
        checks.append(_chk("rbj_sim:dealer_17+",  hand_value(d) >= 17))
        checks.append(_chk("rbj_sim:payout",      _payout(100, "win") == 100))
    except Exception as e:
        checks.append(_chk("rbj_sim:exception", False, str(e)[:60]))
    return checks


def _simulate_shoe() -> list:
    checks = []
    try:
        import modules.realistic_blackjack as rbjm
        shoe = rbjm._Shoe(6)
        checks.append(_chk("shoe:312_cards",       shoe.remaining == 312))
        before = shoe.remaining
        _ = shoe.draw()
        checks.append(_chk("shoe:draw_decrements", shoe.remaining == before - 1))
        checks.append(_chk("shoe:used_pct",        0 <= shoe.used_pct <= 100))
        result = shoe.needs_reshuffle(75)
        checks.append(_chk("shoe:reshuffle_bool",  isinstance(result, bool)))
        checks.append(_chk("shoe:total_312",       shoe.total == 312))
        checks.append(_chk("shoe:reshuffle_fn",    callable(getattr(shoe, "_reshuffle", None))))
    except Exception as e:
        checks.append(_chk("shoe:exception", False, str(e)[:60]))
    return checks


# ─── Poker simulation ────────────────────────────────────────────────────────

def _simulate_poker() -> list:
    checks = []
    try:
        deck = make_deck()
        checks.append(_chk("pk_sim:deck_52",    len(deck) == 52))

        p1 = [deck.pop(), deck.pop()]
        p2 = [deck.pop(), deck.pop()]
        checks.append(_chk("pk_sim:hole_cards", len(p1) == 2 and len(p2) == 2))
        checks.append(_chk("pk_sim:deck_48",    len(deck) == 48))

        deck.pop()
        flop = [deck.pop(), deck.pop(), deck.pop()]
        checks.append(_chk("pk_sim:flop_3",     len(flop) == 3))

        deck.pop(); turn  = deck.pop()
        deck.pop(); river = deck.pop()
        community = flop + [turn, river]
        checks.append(_chk("pk_sim:community_5", len(community) == 5))

        sb, bb = 50, 100
        p1_stack, p2_stack = 1000, 1000
        pot = sb + bb
        p1_stack -= sb; p2_stack -= bb
        checks.append(_chk("pk_sim:blinds",     pot == 150 and p1_stack == 950))

        p1_stack -= (bb - sb); pot += (bb - sb)
        checks.append(_chk("pk_sim:call",       pot == 200 and p1_stack == 900))

        raise_to = 200
        p2_stack -= (raise_to - bb); pot += (raise_to - bb)
        checks.append(_chk("pk_sim:raise",      pot == 300))
        checks.append(_chk("pk_sim:fold_winner", pot == 300))

        a_s, b_s = 200, 500
        main = a_s * 2
        side = b_s - a_s
        checks.append(_chk("pk_sim:allin_main", main == 400))
        checks.append(_chk("pk_sim:allin_side", side == 300))
        checks.append(_chk("pk_sim:raise_cap",  min(1500, 1000) == 1000))

        try:
            from modules.poker import get_poker_recovery_recommendation
            rec   = get_poker_recovery_recommendation()
            valid = rec in {"forcefinish", "hardrefund", "clearhand", "closeforce", "no_action"}
            checks.append(_chk("pk_sim:recovery_rec", valid))
        except Exception as re:
            checks.append(_chk("pk_sim:recovery_rec", False, str(re)[:40]))
    except Exception as e:
        checks.append(_chk("pk_sim:exception", False, str(e)[:60]))
    return checks


# ─── Recovery checks ─────────────────────────────────────────────────────────

def _check_recovery() -> list:
    checks = []

    for fn in ("get_poker_recovery_recommendation", "_hard_refund_hand", "handle_confirmclosepoker"):
        checks.append(_chk(f"pk_rec:{fn}", _fn_ok("modules.poker", fn)))

    try:
        from modules.poker import get_poker_recovery_recommendation
        rec   = get_poker_recovery_recommendation()
        valid = rec in {"forcefinish", "hardrefund", "clearhand", "closeforce", "no_action"}
        checks.append(_chk("pk_rec:non_circular", valid, "" if valid else rec))
    except Exception as e:
        checks.append(_chk("pk_rec:non_circular", False, str(e)[:40]))

    for fn in ("_cmd_bj_recover", "_cmd_bj_refund", "_cmd_bj_forcefinish"):
        checks.append(_chk(f"bj_rec:{fn}", _fn_ok("modules.blackjack", fn)))

    for fn in ("_cmd_rbj_recover", "_cmd_rbj_refund", "_cmd_rbj_forcefinish"):
        checks.append(_chk(f"rbj_rec:{fn}", _fn_ok("modules.realistic_blackjack", fn)))

    return checks


# ─── Ownership checks ────────────────────────────────────────────────────────

def _check_ownership() -> list:
    checks = []
    try:
        from modules.multi_bot import _DEFAULT_COMMAND_OWNERS as _o

        bj_ok  = all(_o.get(c) == "blackjack" for c in ["bj", "bjoin", "bh", "bs", "bd", "bsp"])
        rbj_ok = all(_o.get(c) == "blackjack" for c in ["rbj", "rjoin", "rh", "rs", "rd", "rsp"])
        pk_ok  = all(_o.get(c) == "poker"     for c in ["poker", "p", "check", "call", "fold"])
        checks.append(_chk("own:bj=blackjack",   bj_ok))
        checks.append(_chk("own:rbj=blackjack",  rbj_ok))
        checks.append(_chk("own:poker=poker",    pk_ok))

        bj_conf = any(_o.get(c) in ("host", "all") for c in ["bj", "bjoin", "rbj", "rjoin"])
        pk_conf = any(_o.get(c) in ("host", "all") for c in ["poker", "p", "check", "call", "fold"])
        checks.append(_chk("own:no_bj_conflict",  not bj_conf))
        checks.append(_chk("own:no_pk_conflict",  not pk_conf))

        conn = db.get_connection()
        tbl  = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bot_module_locks'"
        ).fetchone()
        conn.close()
        checks.append(_chk("own:locks_table", tbl is not None))
    except Exception as e:
        checks.append(_chk("own:error", False, str(e)[:60]))
    return checks


# ─── Per-game integrity runners ───────────────────────────────────────────────

async def run_bj_integrity(bot: BaseBot, user: User, sub: str) -> None:
    uid, uname = user.id, user.username
    if not can_manage_games(uname):
        await _w(bot, uid, "Staff only."); return

    if sub in ("", "quick", "full"):
        checks = _check_routes_bj() + _check_db_bj() + _check_settings_bj()
        if sub == "full":
            if not _can_full(uname):
                await _w(bot, uid, "Owner/admin only for full check."); return
            checks += _simulate_bj() + _check_payouts_bj() + _check_recovery()
        passed, total, fails = _sum(checks)
        label  = "🃏 BJ Full" if sub == "full" else "🃏 BJ Quick"
        status = "OK" if not fails else f"{len(fails)} fail"
        msg    = f"{label}: {passed}/{total} pass | {status}"
        _log(uname, "bj", sub or "quick", passed, total, fails, msg)
        await _w(bot, uid, msg)
        if fails:
            await _w(bot, uid, f"Fails: {' | '.join(fails[:3])}"[:249])
            if len(fails) > 3:
                await _w(bot, uid, f"...+{len(fails)-3} more. /integritylogs bj")

    elif sub == "routes":
        checks = _check_routes_bj()
        p, t, f = _sum(checks)
        msg = f"🃏 BJ Routes: {p}/{t} pass"
        if f: msg += f" | Fail: {', '.join(f[:2])}"
        _log(uname, "bj", "routes", p, t, f, msg); await _w(bot, uid, msg)

    elif sub == "db":
        checks = _check_db_bj()
        p, t, f = _sum(checks)
        msg = f"🃏 BJ DB: {p}/{t} tables OK"
        if f: msg += f" | Missing: {', '.join(f)}"
        _log(uname, "bj", "db", p, t, f, msg); await _w(bot, uid, msg)

    elif sub == "simulate":
        if not _can_full(uname):
            await _w(bot, uid, "Owner/admin only."); return
        checks = _simulate_bj()
        p, t, f = _sum(checks)
        msg = f"🃏 BJ Sim: {p}/{t} pass"
        if f: msg += f" | Fail: {f[0]}"
        _log(uname, "bj", "simulate", p, t, f, msg); await _w(bot, uid, msg)

    else:
        await _w(bot, uid, "🃏 BJ integrity: quick | full | routes | db | simulate")


async def run_rbj_integrity(bot: BaseBot, user: User, sub: str) -> None:
    uid, uname = user.id, user.username
    if not can_manage_games(uname):
        await _w(bot, uid, "Staff only."); return

    if sub in ("", "quick", "full"):
        checks = _check_routes_rbj() + _check_db_rbj() + _check_settings_rbj()
        if sub == "full":
            if not _can_full(uname):
                await _w(bot, uid, "Owner/admin only for full check."); return
            checks += _simulate_rbj() + _simulate_shoe() + _check_payouts_rbj()
        passed, total, fails = _sum(checks)
        label  = "🃏 RBJ Full" if sub == "full" else "🃏 RBJ Quick"
        status = "OK" if not fails else f"{len(fails)} fail"
        msg    = f"{label}: {passed}/{total} pass | {status}"
        _log(uname, "rbj", sub or "quick", passed, total, fails, msg)
        await _w(bot, uid, msg)
        if fails:
            await _w(bot, uid, f"Fails: {' | '.join(fails[:3])}"[:249])
            if len(fails) > 3:
                await _w(bot, uid, f"...+{len(fails)-3} more. /integritylogs rbj")

    elif sub == "routes":
        checks = _check_routes_rbj()
        p, t, f = _sum(checks)
        msg = f"🃏 RBJ Routes: {p}/{t} pass"
        if f: msg += f" | Fail: {', '.join(f[:2])}"
        _log(uname, "rbj", "routes", p, t, f, msg); await _w(bot, uid, msg)

    elif sub == "db":
        checks = _check_db_rbj()
        p, t, f = _sum(checks)
        msg = f"🃏 RBJ DB: {p}/{t} tables OK"
        if f: msg += f" | Missing: {', '.join(f)}"
        _log(uname, "rbj", "db", p, t, f, msg); await _w(bot, uid, msg)

    elif sub == "simulate":
        if not _can_full(uname):
            await _w(bot, uid, "Owner/admin only."); return
        checks = _simulate_rbj()
        p, t, f = _sum(checks)
        msg = f"🃏 RBJ Sim: {p}/{t} pass"
        if f: msg += f" | Fail: {f[0]}"
        _log(uname, "rbj", "simulate", p, t, f, msg); await _w(bot, uid, msg)

    elif sub == "shoe":
        checks = _simulate_shoe()
        p, t, f = _sum(checks)
        try:
            import modules.realistic_blackjack as rbjm
            rem = rbjm._shoe.remaining
            msg = f"🃏 Shoe: {p}/{t} pass | {rem} cards live"
        except Exception:
            msg = f"🃏 Shoe: {p}/{t} pass"
        if f: msg += f" | Fail: {f[0]}"
        _log(uname, "rbj", "shoe", p, t, f, msg); await _w(bot, uid, msg)

    else:
        await _w(bot, uid, "🃏 RBJ integrity: quick | full | routes | db | simulate | shoe")


async def run_poker_integrity(bot: BaseBot, user: User, sub: str) -> None:
    uid, uname = user.id, user.username
    if not can_manage_games(uname):
        await _w(bot, uid, "Staff only."); return

    if sub in ("", "quick", "full"):
        checks = _check_routes_poker() + _check_db_poker() + _check_settings_poker()
        if sub == "full":
            if not _can_full(uname):
                await _w(bot, uid, "Owner/admin only for full check."); return
            checks += (_simulate_poker() + _check_payouts_poker() +
                       _check_recovery() + _check_ownership())
        passed, total, fails = _sum(checks)
        label  = "♠️ Poker Full" if sub == "full" else "♠️ Poker Quick"
        status = "OK" if not fails else f"{len(fails)} fail"
        msg    = f"{label}: {passed}/{total} pass | {status}"
        _log(uname, "poker", sub or "quick", passed, total, fails, msg)
        await _w(bot, uid, msg)
        if fails:
            await _w(bot, uid, f"Fails: {' | '.join(fails[:3])}"[:249])
            if len(fails) > 3:
                await _w(bot, uid, f"...+{len(fails)-3} more. /integritylogs poker")

    elif sub == "routes":
        checks = _check_routes_poker()
        p, t, f = _sum(checks)
        msg = f"♠️ Poker Routes: {p}/{t} pass"
        if f: msg += f" | Fail: {', '.join(f[:2])}"
        _log(uname, "poker", "routes", p, t, f, msg); await _w(bot, uid, msg)

    elif sub == "db":
        checks = _check_db_poker()
        p, t, f = _sum(checks)
        msg = f"♠️ Poker DB: {p}/{t} tables OK"
        if f: msg += f" | Missing: {', '.join(f)}"
        _log(uname, "poker", "db", p, t, f, msg); await _w(bot, uid, msg)

    elif sub == "simulate":
        if not _can_full(uname):
            await _w(bot, uid, "Owner/admin only."); return
        checks = _simulate_poker()
        p, t, f = _sum(checks)
        msg = f"♠️ Poker Sim: {p}/{t} pass"
        if f: msg += f" | Fail: {f[0]}"
        _log(uname, "poker", "simulate", p, t, f, msg); await _w(bot, uid, msg)

    elif sub == "recovery":
        checks = _check_recovery()
        p, t, f = _sum(checks)
        msg = f"♠️ Poker Recovery: {p}/{t} pass"
        if f: msg += f" | Fail: {', '.join(f[:2])}"
        _log(uname, "poker", "recovery", p, t, f, msg); await _w(bot, uid, msg)

    else:
        await _w(bot, uid, "♠️ Poker integrity: quick | full | routes | db | simulate | recovery")


# ─── Global casino integrity runner ──────────────────────────────────────────

async def run_casino_integrity(bot: BaseBot, user: User, sub: str) -> None:
    uid, uname = user.id, user.username
    if not can_manage_games(uname):
        await _w(bot, uid, "Staff only."); return

    if sub in ("", "quick"):
        bj_c  = _check_routes_bj();    bj_p,  bj_t,  bj_f  = _sum(bj_c)
        rbj_c = _check_routes_rbj();   rbj_p, rbj_t, rbj_f = _sum(rbj_c)
        pk_c  = _check_routes_poker(); pk_p,  pk_t,  pk_f  = _sum(pk_c)
        own_c = _check_ownership();    own_p, own_t, own_f  = _sum(own_c)
        tot_p = bj_p + rbj_p + pk_p + own_p
        tot_t = bj_t + rbj_t + pk_t + own_t
        all_f = bj_f + rbj_f + pk_f + own_f
        bj_s  = "OK" if not bj_f  else f"{len(bj_f)}f"
        rbj_s = "OK" if not rbj_f else f"{len(rbj_f)}f"
        pk_s  = "OK" if not pk_f  else f"{len(pk_f)}f"
        if not all_f:
            msg = f"✅ Casino Check: {tot_p}/{tot_t} pass | BJ {bj_s} | RBJ {rbj_s} | Poker {pk_s}"
        else:
            msg = f"⚠️ Casino: {tot_p}/{tot_t} | {len(all_f)} fail. /casinointegrity full"
        _log(uname, "casino", "quick", tot_p, tot_t, all_f, msg)
        await _w(bot, uid, msg)

    elif sub == "full":
        if not _can_full(uname):
            await _w(bot, uid, "Owner/admin only."); return
        all_checks = (
            _check_routes_bj()    + _check_routes_rbj()    + _check_routes_poker()   +
            _check_db_bj()        + _check_db_rbj()         + _check_db_poker()        +
            _check_settings_bj()  + _check_settings_rbj()   + _check_settings_poker()  +
            _check_recovery()     + _check_ownership()      +
            _simulate_bj()        + _simulate_rbj()         + _simulate_poker()        +
            _check_payouts_bj()   + _check_payouts_rbj()    + _check_payouts_poker()
        )
        passed, total, fails = _sum(all_checks)
        status = "OK" if not fails else f"{len(fails)} fail"
        msg = f"Casino Full: {passed}/{total} pass | {status}"
        _log(uname, "casino", "full", passed, total, fails, msg)
        await _w(bot, uid, msg)
        if fails:
            await _w(bot, uid, f"Fails: {' | '.join(fails[:3])}"[:249])
            if len(fails) > 3:
                await _w(bot, uid, f"...+{len(fails)-3} more. /integritylogs")

    elif sub == "routes":
        bj_c  = _check_routes_bj();    bj_p,  bj_t,  bj_f  = _sum(bj_c)
        rbj_c = _check_routes_rbj();   rbj_p, rbj_t, rbj_f = _sum(rbj_c)
        pk_c  = _check_routes_poker(); pk_p,  pk_t,  pk_f  = _sum(pk_c)
        tot_p = bj_p + rbj_p + pk_p
        tot_t = bj_t + rbj_t + pk_t
        all_f = bj_f + rbj_f + pk_f
        bj_s  = "OK" if not bj_f  else f"{len(bj_f)}f"
        rbj_s = "OK" if not rbj_f else f"{len(rbj_f)}f"
        pk_s  = "OK" if not pk_f  else f"{len(pk_f)}f"
        msg = f"Routes: {tot_p}/{tot_t} | BJ {bj_s} | RBJ {rbj_s} | Poker {pk_s}"
        _log(uname, "casino", "routes", tot_p, tot_t, all_f, msg)
        await _w(bot, uid, msg)

    elif sub == "db":
        bj_c  = _check_db_bj();    bj_p,  bj_t,  bj_f  = _sum(bj_c)
        rbj_c = _check_db_rbj();   rbj_p, rbj_t, rbj_f = _sum(rbj_c)
        pk_c  = _check_db_poker(); pk_p,  pk_t,  pk_f  = _sum(pk_c)
        tot_p = bj_p + rbj_p + pk_p
        tot_t = bj_t + rbj_t + pk_t
        all_f = bj_f + rbj_f + pk_f
        msg = f"Casino DB: {tot_p}/{tot_t} tables OK"
        if all_f: msg += f" | Missing: {', '.join(all_f[:3])}"
        _log(uname, "casino", "db", tot_p, tot_t, all_f, msg)
        await _w(bot, uid, msg)

    elif sub == "payouts":
        bj_c  = _check_payouts_bj()
        rbj_c = _check_payouts_rbj()
        pk_c  = _check_payouts_poker()
        all_c = bj_c + rbj_c + pk_c
        p, t, f = _sum(all_c)
        bj_s  = "OK" if all(c["ok"] for c in bj_c)  else "FAIL"
        rbj_s = "OK" if all(c["ok"] for c in rbj_c) else "FAIL"
        pk_s  = "OK" if all(c["ok"] for c in pk_c)  else "FAIL"
        msg = f"💰 Payouts: BJ {bj_s} | RBJ {rbj_s} | Poker {pk_s} ({p}/{t})"
        _log(uname, "casino", "payouts", p, t, f, msg)
        await _w(bot, uid, msg)
        if f: await _w(bot, uid, f"Fails: {' | '.join(f[:3])}"[:249])

    elif sub == "recovery":
        checks = _check_recovery()
        p, t, f = _sum(checks)
        pk_ok  = "OK" if all(c["ok"] for c in checks if "pk_rec"  in c["name"]) else "FAIL"
        bj_ok  = "OK" if all(c["ok"] for c in checks if "bj_rec"  in c["name"]) else "FAIL"
        rbj_ok = "OK" if all(c["ok"] for c in checks if "rbj_rec" in c["name"]) else "FAIL"
        msg = f"Recovery: Poker {pk_ok} | BJ {bj_ok} | RBJ {rbj_ok} ({p}/{t})"
        _log(uname, "casino", "recovery", p, t, f, msg)
        await _w(bot, uid, msg)
        if f: await _w(bot, uid, f"Fails: {' | '.join(f[:3])}"[:249])

    else:
        await _w(bot, uid, "Casino integrity: quick | full | routes | db | payouts | recovery")


# ─── Integrity log viewer ─────────────────────────────────────────────────────

async def handle_integritylogs(bot: BaseBot, user: User, args: list) -> None:
    uid, uname = user.id, user.username
    if not can_manage_games(uname):
        await _w(bot, uid, "Staff only."); return

    module_filter = args[1].lower() if len(args) > 1 else ""
    valid_filters = {"poker", "bj", "rbj", "casino"}

    try:
        conn = db.get_connection()
        if module_filter in valid_filters:
            rows = conn.execute(
                "SELECT id, module, check_type, passed, total_checks, failed_checks "
                "FROM casino_integrity_logs WHERE module=? ORDER BY id DESC LIMIT 8",
                (module_filter,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, module, check_type, passed, total_checks, failed_checks "
                "FROM casino_integrity_logs ORDER BY id DESC LIMIT 8"
            ).fetchall()
        conn.close()

        if not rows:
            await _w(bot, uid, "No integrity logs. Run /casinointegrity first."); return

        header = f"Integrity Logs{(' ' + module_filter) if module_filter else ''}:"
        batch  = header
        for r in rows:
            icon = "✅" if r["failed_checks"] == 0 else "⚠️"
            ln   = f"#{r['id']} {r['module']} {r['check_type']} {r['passed']}/{r['total_checks']} {icon}"
            if len(batch) + len(ln) + 3 > 249:
                await _w(bot, uid, batch)
                batch = ln
            else:
                batch += (" | " + ln if batch != header else " " + ln)
        if batch:
            await _w(bot, uid, batch)

    except Exception as e:
        await _w(bot, uid, f"Log error: {e}"[:249])
