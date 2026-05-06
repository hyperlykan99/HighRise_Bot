"""modules/dashboard.py
--------------------
Wallet and Casino Dashboard commands.

/wallet /w          — coin balance, daily status, VIP, event, poker stack
/wallet 2           — bank sent today, alerts, money privacy
/casinodash /mycasino /casino (no sub) — BJ/RBJ/Poker net today + table status
/casino 2 /casinodash 2               — daily win/loss limit status
/dashboard /dash    — self overview; staff: /dashboard <username>
"""
from datetime import date

from highrise import BaseBot, User

import database as db
from economy import fmt_coins
from modules.permissions import can_moderate, is_admin


# ─── compact signed formatter ─────────────────────────────────────────────────

def _net(n: int) -> str:
    """Compact signed: +1.5K, -500c, +2M, +0c."""
    sign = "+" if n >= 0 else "-"
    a = abs(n)
    if a >= 1_000_000_000:
        v = f"{a / 1_000_000_000:.10g}B"
    elif a >= 1_000_000:
        v = f"{a / 1_000_000:.10g}M"
    elif a >= 1_000:
        v = f"{a / 1_000:.10g}K"
    else:
        v = f"{a}c"
    return f"{sign}{v}"


# ─── shared lookups ───────────────────────────────────────────────────────────

def _is_vip(user_id: str) -> bool:
    try:
        return db.owns_item(user_id, "vip")
    except Exception:
        return False


def _poker_stack(username: str) -> int | None:
    """Return table_stack if player is seated at poker, else None."""
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT table_stack FROM poker_seated_players "
            "WHERE LOWER(username)=LOWER(?)",
            (username,),
        ).fetchone()
        conn.close()
        return row["table_stack"] if row else None
    except Exception:
        return None


def _poker_daily_net(username: str) -> int:
    """Today's poker net_profit from poker_daily_stats."""
    try:
        today = str(date.today())
        conn  = db.get_connection()
        row   = conn.execute(
            "SELECT net_profit FROM poker_daily_stats WHERE username=? AND date=?",
            (username, today),
        ).fetchone()
        conn.close()
        return row["net_profit"] if row else 0
    except Exception:
        return 0


def _poker_lifetime_net(username: str) -> int:
    """Sum of net_profit across all poker_daily_stats rows for username."""
    try:
        conn = db.get_connection()
        row  = conn.execute(
            "SELECT COALESCE(SUM(net_profit), 0) AS total "
            "FROM poker_daily_stats WHERE username=?",
            (username,),
        ).fetchone()
        conn.close()
        return row["total"] if row else 0
    except Exception:
        return 0


def _bj_phase() -> str:
    try:
        from modules.blackjack import _state
        return _state.phase
    except Exception:
        return "?"


def _rbj_phase() -> str:
    try:
        from modules.realistic_blackjack import _state
        return _state.phase
    except Exception:
        return "?"


def _poker_phase() -> str:
    try:
        conn = db.get_connection()
        row  = conn.execute(
            "SELECT active, phase FROM poker_active_table LIMIT 1"
        ).fetchone()
        conn.close()
        if not row or not row["active"]:
            return "idle"
        return row["phase"] or "idle"
    except Exception:
        return "?"


# ─── /wallet  /w ─────────────────────────────────────────────────────────────

async def handle_wallet(bot: BaseBot, user: User, args: list) -> None:
    """/wallet [2]  — personal wallet overview."""
    db.ensure_user(user.id, user.username)
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
    if page == 2:
        await _wallet_p2(bot, user)
    else:
        await _wallet_p1(bot, user)


async def _wallet_p1(bot: BaseBot, user: User) -> None:
    balance   = db.get_balance(user.id)
    can_daily = db.can_claim_daily(user.id)
    vip       = "YES" if _is_vip(user.id) else "NO"
    event     = db.get_active_event()
    ev_str    = f"Event: {event['event_id']}" if event else "Event: none"
    daily_str = "available" if can_daily else "claimed"

    stack = _poker_stack(user.username)
    lines = [
        f"💼 Wallet — @{user.username}",
        f"Coins: {fmt_coins(balance)}",
        f"Daily: {daily_str} | VIP: {vip}",
        ev_str,
    ]
    if stack is not None:
        lines.append(f"Poker stack: {fmt_coins(stack)}")
    lines.append("/wallet 2 for bank & alerts")
    await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])


async def _wallet_p2(bot: BaseBot, user: User) -> None:
    try:
        sent_today = db.get_daily_sent_today(user.id)
        bus        = db.get_bank_user_stats(user.id)
        alert_on   = bool(bus.get("bank_notify", 1))
        recv_total = bus.get("total_received", 0)
    except Exception:
        sent_today = 0
        alert_on   = True
        recv_total = 0

    try:
        priv      = db.get_profile_privacy(user.username.lower())
        money_str = "public" if bool(priv.get("show_money", 1)) else "private"
    except Exception:
        money_str = "public"

    alert_str = "ON" if alert_on else "OFF"
    msg = (
        f"💼 Wallet 2 — @{user.username}\n"
        f"Sent today: {fmt_coins(sent_today)}\n"
        f"Total recv: {fmt_coins(recv_total)}\n"
        f"Alerts: {alert_str} | Money: {money_str}"
    )
    await bot.highrise.send_whisper(user.id, msg[:249])


# ─── /casinodash  /mycasino  /casino (no sub/page) ───────────────────────────

async def handle_casino_dash(bot: BaseBot, user: User, args: list) -> None:
    """/casinodash [2]  — personal casino stats overview."""
    db.ensure_user(user.id, user.username)
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
    if page == 2:
        await _casino_p2(bot, user)
    else:
        await _casino_p1(bot, user)


async def _casino_p1(bot: BaseBot, user: User) -> None:
    bj_today  = db.get_bj_daily_net(user.id)
    rbj_today = db.get_rbj_daily_net(user.id)
    pk_today  = _poker_daily_net(user.username)

    try:
        s       = db.get_bj_stats(user.id)
        bj_life = s.get("bj_total_won", 0) - s.get("bj_total_bet", 0)
    except Exception:
        bj_life = 0

    try:
        s        = db.get_rbj_stats(user.id)
        rbj_life = s.get("rbj_total_won", 0) - s.get("rbj_total_bet", 0)
    except Exception:
        rbj_life = 0

    pk_life = _poker_lifetime_net(user.username)

    bj_ph  = _bj_phase()
    rbj_ph = _rbj_phase()
    pk_ph  = _poker_phase()

    msg = (
        f"🎰 Casino — @{user.username}\n"
        f"BJ: {_net(bj_today)} today | life {_net(bj_life)}\n"
        f"RBJ: {_net(rbj_today)} today | life {_net(rbj_life)}\n"
        f"Poker: {_net(pk_today)} today | life {_net(pk_life)}\n"
        f"BJ {bj_ph} | RBJ {rbj_ph} | Poker {pk_ph}"
    )
    await bot.highrise.send_whisper(user.id, msg[:249])


async def _casino_p2(bot: BaseBot, user: User) -> None:
    s_bj  = db.get_bj_settings()
    s_rbj = db.get_rbj_settings()
    s_pk  = db.get_poker_settings()

    def _lim(en_key: str, lim_key: str, cfg: dict, default: int = 5000) -> str:
        on  = "ON"  if int(cfg.get(en_key,  1)) else "OFF"
        lim = int(cfg.get(lim_key, default))
        return f"{lim:,}c {on}"

    bj_w  = _lim("bj_win_limit_enabled",   "bj_daily_win_limit",    s_bj,  5000)
    bj_l  = _lim("bj_loss_limit_enabled",  "bj_daily_loss_limit",   s_bj,  3000)
    rbj_w = _lim("rbj_win_limit_enabled",  "rbj_daily_win_limit",   s_rbj, 5000)
    rbj_l = _lim("rbj_loss_limit_enabled", "rbj_daily_loss_limit",  s_rbj, 3000)
    pk_w  = _lim("win_limit_enabled",      "table_daily_win_limit",  s_pk, 10000)
    pk_l  = _lim("loss_limit_enabled",     "table_daily_loss_limit", s_pk,  5000)

    msg = (
        f"🎰 Limits\n"
        f"BJ Win: {bj_w} | Loss: {bj_l}\n"
        f"RBJ Win: {rbj_w} | Loss: {rbj_l}\n"
        f"Poker Win: {pk_w} | Loss: {pk_l}"
    )
    await bot.highrise.send_whisper(user.id, msg[:249])


# ─── /dashboard  /dash ────────────────────────────────────────────────────────

async def handle_dashboard(bot: BaseBot, user: User, args: list) -> None:
    """/dashboard — self overview. Staff: /dashboard <username>."""
    target_arg = args[1].lstrip("@").strip() if len(args) > 1 else None

    if not target_arg:
        await _dashboard_self(bot, user)
        return

    if not can_moderate(user.username) and not is_admin(user.username):
        await bot.highrise.send_whisper(
            user.id, "Staff only: /dashboard <username>"
        )
        return

    target = db.get_user_by_username(target_arg)
    if not target:
        await bot.highrise.send_whisper(
            user.id, "User not found. They may need to chat first."
        )
        return

    t_id   = target["user_id"]
    t_name = target["username"]
    full   = is_admin(user.username)

    priv        = db.get_profile_privacy(t_name.lower())
    show_money  = full or bool(priv.get("show_money",  1))
    show_casino = full or bool(priv.get("show_casino", 1))

    balance_str = fmt_coins(db.get_balance(t_id)) if show_money else "private"
    vip         = "YES" if _is_vip(t_id) else "NO"

    casino_line: str
    if show_casino:
        bj_t  = db.get_bj_daily_net(t_id)
        rbj_t = db.get_rbj_daily_net(t_id)
        casino_line = f"BJ: {_net(bj_t)} | RBJ: {_net(rbj_t)}"
    else:
        casino_line = "Casino: hidden"

    w_str = ""
    if full:
        try:
            _, warn_count = db.get_warnings(t_name, limit=1)
            w_str = f" | Warns: {warn_count}"
        except Exception:
            pass

    bj_ph, rbj_ph, pk_ph = _bj_phase(), _rbj_phase(), _poker_phase()
    lines = [
        f"💼 @{t_name}",
        f"Coins: {balance_str} | VIP: {vip}{w_str}",
        casino_line,
        f"BJ {bj_ph} | RBJ {rbj_ph} | Poker {pk_ph}",
    ]
    await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])


async def _dashboard_self(bot: BaseBot, user: User) -> None:
    db.ensure_user(user.id, user.username)
    balance   = db.get_balance(user.id)
    vip       = "YES" if _is_vip(user.id) else "NO"
    can_daily = db.can_claim_daily(user.id)
    bj_today  = db.get_bj_daily_net(user.id)
    rbj_today = db.get_rbj_daily_net(user.id)
    pk_today  = _poker_daily_net(user.username)
    bj_ph, rbj_ph, pk_ph = _bj_phase(), _rbj_phase(), _poker_phase()
    daily_str = "available" if can_daily else "claimed"

    msg = (
        f"💼 @{user.username}\n"
        f"Coins: {fmt_coins(balance)} | VIP: {vip}\n"
        f"Daily: {daily_str}\n"
        f"BJ: {_net(bj_today)} | RBJ: {_net(rbj_today)} | Poker: {_net(pk_today)}\n"
        f"BJ {bj_ph} | RBJ {rbj_ph} | Poker {pk_ph}"
    )
    await bot.highrise.send_whisper(user.id, msg[:249])
