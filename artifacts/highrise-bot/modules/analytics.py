"""
modules/analytics.py  —  3.1O Owner Analytics + Economy Dashboard
-------------------------------------------------------------------
All commands are owner/admin-only whispers. Every message ≤ 249 chars.
Currencies: 🪙 ChillCoins | 🎫 Luxe Tickets  (never bare "c").
Graceful N/A fallback on any missing table or data.

Commands
────────
!ownerdash [today|yesterday|7d|30d|safety]
!playerstats [today|7d|30d]
!economydash [today|7d|30d|sources|sinks]
!luxedash [today|7d|purchases|conversions]
!conversiondash
!minedash [today|7d|30d]
!fishdash [today|7d|30d]
!hourlyaudit [mining|fishing|luxe]
!shopdash [today|7d|luxe|coins]
!vipdash [list]
!retentiondash [today|7d|30d]
!eventdash [current|last|7d]
!seasondash
!activitydash [mining|fishing]    (alias → minedash / fishdash)
"""

from __future__ import annotations

from highrise import BaseBot, User

import database as db
from modules.permissions import is_admin, is_owner


# ── helpers ──────────────────────────────────────────────────────────────────

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


async def _send_lines(bot: BaseBot, uid: str, lines: list[str]) -> None:
    chunk = ""
    for line in lines:
        candidate = (chunk + "\n" + line).lstrip("\n") if chunk else line
        if len(candidate) > 249:
            if chunk:
                await _w(bot, uid, chunk)
            chunk = line
        else:
            chunk = candidate
    if chunk:
        await _w(bot, uid, chunk)


def _perm(user: User) -> bool:
    return is_admin(user.username) or is_owner(user.username)


def _fmt(n: int | None, suffix: str = " 🪙") -> str:
    if n is None:
        return "N/A"
    return f"{n:,}{suffix}"


def _sign(n: int | None, suffix: str = " 🪙") -> str:
    if n is None:
        return "N/A"
    sign = "+" if n >= 0 else ""
    return f"{sign}{n:,}{suffix}"


def _date_expr(col: str, period: str) -> str:
    """Return a SQLite WHERE clause fragment for the given period."""
    if period == "yesterday":
        return f"DATE({col}) = DATE('now', '-1 day')"
    if period in ("7d", "week"):
        return f"DATE({col}) >= DATE('now', '-7 days')"
    if period in ("30d", "month"):
        return f"DATE({col}) >= DATE('now', '-30 days')"
    return f"DATE({col}) = DATE('now')"   # default: today


def _period_label(period: str) -> str:
    return {"today": "Today", "yesterday": "Yesterday",
            "7d": "7d", "30d": "30d"}.get(period, "Today")


def _parse_period(args: list[str], *, offset: int = 1) -> str:
    """Pull the period keyword from args[offset:], default 'today'."""
    kw = args[offset].lower().strip() if len(args) > offset else ""
    return kw if kw in ("today", "yesterday", "7d", "30d") else "today"


def _q(conn, sql: str, params: tuple = ()) -> list:
    """Safe fetchall; returns [] on error."""
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        return []


def _q1(conn, sql: str, params: tuple = (), default=None):
    """Safe fetchone scalar (first column); returns default on error."""
    try:
        row = conn.execute(sql, params).fetchone()
        if row:
            return row[0]
    except Exception:
        pass
    return default


def _bot_excl(conn) -> list[str]:
    """Return lowercased bot usernames to exclude from player counts."""
    try:
        rows = conn.execute(
            "SELECT LOWER(bot_username) FROM bot_instances WHERE bot_username != ''"
        ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


# ── !ownerdash ────────────────────────────────────────────────────────────────

async def handle_ownerdash(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _perm(user):
        await _w(bot, user.id, "🔒 Owner/Admin only.")
        return

    sub = args[1].lower().strip() if len(args) > 1 else "today"

    if sub == "safety":
        await _ownerdash_safety(bot, user)
        return

    period = sub if sub in ("today", "yesterday", "7d", "30d") else "today"
    label  = _period_label(period)

    conn = db.get_connection()
    bots = _bot_excl(conn)
    bot_excl_sql = ("AND LOWER(username) NOT IN (" +
                    ",".join("?" * len(bots)) + ")") if bots else ""

    # ── DAU (daily_claims + mining + fish) ────────────────────────────────────
    dc_expr = _date_expr("last_claim", period)
    dau_dc  = _q1(conn,
        f"SELECT COUNT(DISTINCT user_id) FROM daily_claims WHERE {dc_expr}")
    mn_expr = _date_expr("timestamp", period)
    dau_mn  = _q1(conn,
        f"SELECT COUNT(DISTINCT username) FROM mining_logs WHERE {mn_expr} {bot_excl_sql}",
        tuple(bots))
    fs_expr = _date_expr("caught_at", period)
    dau_fs  = _q1(conn,
        f"SELECT COUNT(DISTINCT username) FROM fish_catch_records WHERE {fs_expr} {bot_excl_sql}",
        tuple(bots))
    dau = max(dau_dc or 0, dau_mn or 0, dau_fs or 0)

    # ── New players (player_onboarding started) ───────────────────────────────
    ob_expr = _date_expr("created_at", period)
    new_p   = _q1(conn,
        f"SELECT COUNT(*) FROM player_onboarding WHERE {ob_expr}", default=0)

    # ── Onboarding completion % ───────────────────────────────────────────────
    ob_total  = _q1(conn, "SELECT COUNT(*) FROM player_onboarding", default=0)
    ob_done   = _q1(conn,
        "SELECT COUNT(*) FROM player_onboarding WHERE completed=1", default=0)
    ob_pct    = int(ob_done * 100 / ob_total) if ob_total else 0

    # ── Economy ───────────────────────────────────────────────────────────────
    tx_expr  = _date_expr("created_at", period)
    earned   = _q1(conn,
        f"SELECT COALESCE(SUM(amount),0) FROM economy_transactions "
        f"WHERE direction='credit' AND {tx_expr}", default=0)
    spent    = _q1(conn,
        f"SELECT COALESCE(SUM(amount),0) FROM economy_transactions "
        f"WHERE direction='debit' AND {tx_expr}", default=0)

    # ── Luxe ──────────────────────────────────────────────────────────────────
    gt_expr  = _date_expr("created_at", period)
    luxe_in  = _q1(conn,
        f"SELECT COALESCE(SUM(gold_amount),0) FROM gold_tip_events WHERE {gt_expr}",
        default=0)
    cv_expr  = _date_expr("created_at", period)
    luxe_cv  = _q1(conn,
        f"SELECT COALESCE(SUM(gold_amount),0) FROM tip_conversions WHERE {cv_expr}",
        default=0)
    ph_expr  = _date_expr("created_at", period)
    vip_buys = _q1(conn,
        f"SELECT COUNT(*) FROM purchase_history WHERE item_id='vip' AND {ph_expr}",
        default=0)
    luxe_sp  = _q1(conn,
        f"SELECT COALESCE(SUM(price),0) FROM purchase_history "
        f"WHERE item_id IN ('vip','automine_1h','autofish_1h','coin_pack_s',"
        f"'coin_pack_m','coin_pack_l','coin_pack_max') AND {ph_expr}",
        default=0)

    # ── Activity ──────────────────────────────────────────────────────────────
    mine_acts = _q1(conn,
        f"SELECT COUNT(*) FROM mining_logs WHERE action='mine' AND {mn_expr}",
        default=0)
    fish_acts = _q1(conn,
        f"SELECT COUNT(*) FROM fish_catch_records WHERE {fs_expr}", default=0)
    bj_acts   = _q1(conn,
        f"SELECT COALESCE(SUM(hands_played),0) FROM bj_stats", default=0)
    ev_row    = db.get_active_event()
    ev_name   = ev_row["event_id"][:16] if ev_row else "none"

    conn.close()

    # ── Mission count (best-effort) ───────────────────────────────────────────
    miss_ct = 0
    try:
        c2 = db.get_connection()
        mission_expr = _date_expr("claimed_at", period)
        miss_ct = _q1(c2,
            f"SELECT COUNT(*) FROM mission_claims WHERE {mission_expr}", default=0)
        c2.close()
    except Exception:
        pass

    # ── Send messages ─────────────────────────────────────────────────────────
    msg1 = (f"📊 Owner Dashboard {label}\n"
            f"DAU: {dau} | New: {new_p}\n"
            f"Onboarding: {ob_pct}%\n"
            f"Earned: {earned:,} 🪙\n"
            f"Spent: {spent:,} 🪙")
    await _w(bot, user.id, msg1)

    luxe_net = (luxe_in or 0) - (luxe_cv or 0)
    msg2 = (f"🎫 Luxe {label}\n"
            f"Tips: {luxe_in:,} 🎫\n"
            f"Spent: {luxe_sp:,} 🎫\n"
            f"Converted: {luxe_cv:,} 🎫\n"
            f"VIP buys: {vip_buys}")
    await _w(bot, user.id, msg2)

    msg3 = (f"🔥 Activity {label}\n"
            f"Mine: {mine_acts:,}\n"
            f"Fish: {fish_acts:,}\n"
            f"Casino: {bj_acts:,}\n"
            f"Missions: {miss_ct:,}\n"
            f"Events: {ev_name}")
    await _w(bot, user.id, msg3)


async def _ownerdash_safety(bot: BaseBot, user: User) -> None:
    conn = db.get_connection()
    alerts   = _q1(conn, "SELECT COUNT(*) FROM safety_alerts", default=0)
    dup_bl   = _q1(conn,
        "SELECT COUNT(*) FROM safety_alerts WHERE alert_type='duplicate_blocked'",
        default=0)
    muted    = _q1(conn,
        "SELECT COUNT(*) FROM room_mutes WHERE expires_at > datetime('now')",
        default=0)
    softbans = _q1(conn,
        "SELECT COUNT(*) FROM softbans WHERE expires_at > datetime('now')",
        default=0)
    conn.close()

    risk = "Low"
    if (alerts or 0) > 20 or (dup_bl or 0) > 10:
        risk = "High"
    elif (alerts or 0) > 5:
        risk = "Med"

    await _w(bot, user.id,
             f"🛡️ Safety\n"
             f"Alerts: {alerts}\n"
             f"Dup blocks: {dup_bl}\n"
             f"Muted: {muted}\n"
             f"Softbanned: {softbans}\n"
             f"Risk: {risk}")


# ── !playerstats ──────────────────────────────────────────────────────────────

async def handle_playerstats(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _perm(user):
        await _w(bot, user.id, "🔒 Owner/Admin only.")
        return

    period = _parse_period(args)
    label  = _period_label(period)
    conn   = db.get_connection()
    bots   = _bot_excl(conn)
    bot_sql = ("AND LOWER(username) NOT IN (" +
               ",".join("?" * len(bots)) + ")") if bots else ""

    # DAU proxy: distinct daily_claims claimers
    dc_expr = _date_expr("last_claim", period)
    dau     = _q1(conn,
        f"SELECT COUNT(DISTINCT user_id) FROM daily_claims WHERE {dc_expr}",
        default=0)

    # New players
    ob_expr = _date_expr("created_at", period)
    new_p   = _q1(conn,
        f"SELECT COUNT(*) FROM player_onboarding WHERE {ob_expr}", default=0)

    returning = max(0, (dau or 0) - (new_p or 0))

    # Tutorial completion
    ob_total = _q1(conn, "SELECT COUNT(*) FROM player_onboarding", default=0)
    ob_done  = _q1(conn,
        "SELECT COUNT(*) FROM player_onboarding WHERE completed=1", default=0)

    # Top active by mining actions
    mn_expr  = _date_expr("timestamp", period)
    top_rows = _q(conn,
        f"SELECT username, COUNT(*) AS cnt FROM mining_logs "
        f"WHERE action='mine' AND {mn_expr} {bot_sql} "
        f"GROUP BY LOWER(username) ORDER BY cnt DESC LIMIT 1",
        tuple(bots))
    top_name = ("@" + top_rows[0]["username"][:16]) if top_rows else "N/A"
    conn.close()

    msg = (f"👥 Player Stats {label}\n"
           f"DAU: {dau}\n"
           f"New: {new_p}\n"
           f"Returning: {returning}\n"
           f"Tutorial: {ob_done}/{ob_total}\n"
           f"Top active: {top_name}")
    await _w(bot, user.id, msg)


# ── !economydash ──────────────────────────────────────────────────────────────

async def handle_economydash(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _perm(user):
        await _w(bot, user.id, "🔒 Owner/Admin only.")
        return

    sub = args[1].lower().strip() if len(args) > 1 else "today"

    if sub == "sources":
        await _economydash_sources(bot, user, "today")
        return
    if sub == "sinks":
        await _economydash_sinks(bot, user, "today")
        return

    period = sub if sub in ("today", "yesterday", "7d", "30d") else "today"
    label  = _period_label(period)

    conn    = db.get_connection()
    tx_expr = _date_expr("created_at", period)
    earned  = _q1(conn,
        f"SELECT COALESCE(SUM(amount),0) FROM economy_transactions "
        f"WHERE direction='credit' AND {tx_expr}", default=0)
    spent   = _q1(conn,
        f"SELECT COALESCE(SUM(amount),0) FROM economy_transactions "
        f"WHERE direction='debit' AND {tx_expr}", default=0)
    wallets = _q1(conn,
        f"SELECT COUNT(DISTINCT username) FROM economy_transactions WHERE {tx_expr}",
        default=0)
    conn.close()

    net = (earned or 0) - (spent or 0)
    msg = (f"💰 Economy {label}\n"
           f"Earned: {earned:,} 🪙\n"
           f"Spent: {spent:,} 🪙\n"
           f"Net: {_sign(net)}\n"
           f"Active wallets: {wallets}")
    await _w(bot, user.id, msg)
    await _economydash_sources(bot, user, period)
    await _economydash_sinks(bot, user, period)


async def _economydash_sources(bot: BaseBot, user: User, period: str) -> None:
    conn    = db.get_connection()
    mn_expr = _date_expr("timestamp", period)
    fs_expr = _date_expr("caught_at", period)
    tx_expr = _date_expr("created_at", period)

    mine_earn = _q1(conn,
        f"SELECT COALESCE(SUM(coins),0) FROM mining_logs WHERE {mn_expr}", default=0)
    fish_earn = _q1(conn,
        f"SELECT COALESCE(SUM(final_value),0) FROM fish_catch_records WHERE {fs_expr}",
        default=0)
    mission_earn = _q1(conn,
        f"SELECT COALESCE(SUM(amount),0) FROM economy_transactions "
        f"WHERE source='mission' AND direction='credit' AND {tx_expr}", default=0)
    casino_earn  = _q1(conn,
        f"SELECT COALESCE(SUM(amount),0) FROM economy_transactions "
        f"WHERE source IN ('blackjack','rbj','poker') AND direction='credit' AND {tx_expr}",
        default=0)
    daily_earn   = _q1(conn,
        f"SELECT COALESCE(SUM(amount),0) FROM economy_transactions "
        f"WHERE source='daily' AND direction='credit' AND {tx_expr}", default=0)
    conn.close()

    lines = [
        "🪙 Top Sources",
        f"Mining: {mine_earn:,} 🪙",
        f"Fishing: {fish_earn:,} 🪙",
        f"Missions: {mission_earn:,} 🪙",
        f"Casino wins: {casino_earn:,} 🪙",
        f"Daily: {daily_earn:,} 🪙",
    ]
    await _send_lines(bot, user.id, lines)


async def _economydash_sinks(bot: BaseBot, user: User, period: str) -> None:
    conn    = db.get_connection()
    ph_expr = _date_expr("created_at", period)
    tx_expr = _date_expr("created_at", period)

    shop_spent   = _q1(conn,
        f"SELECT COALESCE(SUM(price),0) FROM purchase_history WHERE {ph_expr}",
        default=0)
    casino_bets  = _q1(conn,
        f"SELECT COALESCE(SUM(amount),0) FROM economy_transactions "
        f"WHERE source IN ('blackjack','rbj','poker') AND direction='debit' AND {tx_expr}",
        default=0)
    transfer_out = _q1(conn,
        f"SELECT COALESCE(SUM(amount),0) FROM economy_transactions "
        f"WHERE source='bank_transfer' AND direction='debit' AND {tx_expr}", default=0)
    conn.close()

    lines = [
        "🪙 Top Sinks",
        f"Shop: {shop_spent:,} 🪙",
        f"Casino bets: {casino_bets:,} 🪙",
        f"Transfers: {transfer_out:,} 🪙",
    ]
    await _send_lines(bot, user.id, lines)


# ── !luxedash ─────────────────────────────────────────────────────────────────

async def handle_luxedash(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _perm(user):
        await _w(bot, user.id, "🔒 Owner/Admin only.")
        return

    sub = args[1].lower().strip() if len(args) > 1 else "today"

    if sub == "purchases":
        await _luxedash_purchases(bot, user, "today")
        return
    if sub == "conversions":
        await _luxedash_conversions(bot, user)
        return

    period = sub if sub in ("today", "yesterday", "7d", "30d") else "today"
    label  = _period_label(period)
    conn   = db.get_connection()

    gt_expr  = _date_expr("created_at", period)
    luxe_in  = _q1(conn,
        f"SELECT COALESCE(SUM(gold_amount),0) FROM gold_tip_events WHERE {gt_expr}",
        default=0)
    cv_expr  = _date_expr("created_at", period)
    luxe_cv  = _q1(conn,
        f"SELECT COALESCE(SUM(gold_amount),0) FROM tip_conversions WHERE {cv_expr}",
        default=0)
    ph_expr  = _date_expr("created_at", period)
    luxe_sp  = _q1(conn,
        f"SELECT COALESCE(SUM(price),0) FROM purchase_history "
        f"WHERE item_id IN ('vip','automine_1h','autofish_1h',"
        f"'coin_pack_s','coin_pack_m','coin_pack_l','coin_pack_max') AND {ph_expr}",
        default=0)
    conn.close()

    net = (luxe_in or 0) - (luxe_cv or 0) - (luxe_sp or 0)
    await _w(bot, user.id,
             f"🎫 Luxe Dashboard {label}\n"
             f"Earned from tips: {luxe_in:,} 🎫\n"
             f"Spent: {luxe_sp:,} 🎫\n"
             f"Converted to 🪙: {luxe_cv:,} 🎫\n"
             f"Net held: {_sign(net, ' 🎫')}")
    await _luxedash_purchases(bot, user, period)


async def _luxedash_purchases(bot: BaseBot, user: User, period: str) -> None:
    conn    = db.get_connection()
    ph_expr = _date_expr("created_at", period)

    def _cnt(item_id: str) -> int:
        return _q1(conn,
            f"SELECT COUNT(*) FROM purchase_history "
            f"WHERE item_id=? AND {ph_expr}", (item_id,), default=0) or 0

    vip    = _cnt("vip")
    am1h   = _cnt("automine_1h")
    af1h   = _cnt("autofish_1h")
    cps    = _cnt("coin_pack_s")
    cpm    = _cnt("coin_pack_m")
    cpl    = _cnt("coin_pack_l")
    cpmx   = _cnt("coin_pack_max")
    conn.close()

    lines = [
        "🛒 Luxe Purchases",
        f"VIP: {vip}",
        f"Auto-Mine 1h: {am1h}",
        f"Auto-Fish 1h: {af1h}",
        f"Coin Packs S/M/L/Max: {cps}/{cpm}/{cpl}/{cpmx}",
    ]
    await _send_lines(bot, user.id, lines)


async def _luxedash_conversions(bot: BaseBot, user: User) -> None:
    conn = db.get_connection()
    total_gold  = _q1(conn,
        "SELECT COALESCE(SUM(gold_amount),0) FROM tip_conversions", default=0)
    total_coins = _q1(conn,
        "SELECT COALESCE(SUM(coins_converted),0) FROM tip_conversions", default=0)
    count       = _q1(conn,
        "SELECT COUNT(*) FROM tip_conversions", default=0)
    conn.close()

    await _w(bot, user.id,
             f"🔁 Conversions (all-time)\n"
             f"Total events: {count}\n"
             f"Gold in: {total_gold:,} 🎫\n"
             f"Coins out: {total_coins:,} 🪙")


# ── !conversiondash ───────────────────────────────────────────────────────────

async def handle_conversiondash(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _perm(user):
        await _w(bot, user.id, "🔒 Owner/Admin only.")
        return

    conn = db.get_connection()

    def _buys(item_id: str) -> int:
        return _q1(conn,
            "SELECT COUNT(*) FROM purchase_history WHERE item_id=?",
            (item_id,), default=0) or 0

    s_ct  = _buys("coin_pack_s")
    m_ct  = _buys("coin_pack_m")
    l_ct  = _buys("coin_pack_l")
    mx_ct = _buys("coin_pack_max")

    total_gold  = _q1(conn,
        "SELECT COALESCE(SUM(gold_amount),0) FROM tip_conversions", default=0)
    total_coins = _q1(conn,
        "SELECT COALESCE(SUM(coins_converted),0) FROM tip_conversions", default=0)
    conn.close()

    await _w(bot, user.id,
             f"🔁 Conversions\n"
             f"Small: {s_ct} buys\n"
             f"Medium: {m_ct} buys\n"
             f"Large: {l_ct} buys\n"
             f"Max: {mx_ct} buys\n"
             f"Total: {total_gold:,} 🎫 → {total_coins:,} 🪙")


# ── !minedash ─────────────────────────────────────────────────────────────────

async def handle_minedash(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _perm(user):
        await _w(bot, user.id, "🔒 Owner/Admin only.")
        return

    period  = _parse_period(args)
    label   = _period_label(period)
    mn_expr = _date_expr("timestamp", period)
    conn    = db.get_connection()

    mines    = _q1(conn,
        f"SELECT COUNT(*) FROM mining_logs WHERE action='mine' AND {mn_expr}",
        default=0)
    earned   = _q1(conn,
        f"SELECT COALESCE(SUM(coins),0) FROM mining_logs WHERE {mn_expr}", default=0)
    rare_expr = mn_expr
    rare_cnt = _q1(conn,
        f"SELECT COUNT(*) FROM mining_logs WHERE {rare_expr} "
        f"AND details LIKE '%rarity%' AND (details LIKE '%legendary%' "
        f"OR details LIKE '%mythic%' OR details LIKE '%prismatic%' "
        f"OR details LIKE '%exotic%')", default=0)
    luxe_min = _q1(conn,
        "SELECT COALESCE(SUM(quantity),0) FROM mining_logs "
        "WHERE action='automine' AND item_id='automine_1h'", default=0)
    conn.close()

    avg = int((earned or 0) / (mines or 1))
    await _w(bot, user.id,
             f"⛏️ Mining Dashboard {label}\n"
             f"Mines: {mines:,}\n"
             f"Earned: {earned:,} 🪙\n"
             f"Avg/action: {avg:,} 🪙\n"
             f"Rare finds: {rare_cnt}\n"
             f"Luxe sessions: {luxe_min}")


# ── !fishdash ─────────────────────────────────────────────────────────────────

async def handle_fishdash(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _perm(user):
        await _w(bot, user.id, "🔒 Owner/Admin only.")
        return

    period  = _parse_period(args)
    label   = _period_label(period)
    fs_expr = _date_expr("caught_at", period)
    conn    = db.get_connection()

    casts    = _q1(conn,
        f"SELECT COUNT(*) FROM fish_catch_records WHERE {fs_expr}", default=0)
    earned   = _q1(conn,
        f"SELECT COALESCE(SUM(final_value),0) FROM fish_catch_records WHERE {fs_expr}",
        default=0)
    rare_cnt = _q1(conn,
        f"SELECT COUNT(*) FROM fish_catch_records WHERE {fs_expr} "
        f"AND rarity IN ('legendary','mythic','prismatic','exotic')", default=0)
    conn.close()

    # Luxe auto-fish time from purchase_history
    luxe_sess = 0
    try:
        c2 = db.get_connection()
        ph_expr = _date_expr("created_at", period)
        luxe_sess = _q1(c2,
            f"SELECT COUNT(*) FROM purchase_history "
            f"WHERE item_id='autofish_1h' AND {ph_expr}", default=0)
        c2.close()
    except Exception:
        pass

    avg = int((earned or 0) / (casts or 1))
    await _w(bot, user.id,
             f"🎣 Fishing Dashboard {label}\n"
             f"Casts: {casts:,}\n"
             f"Earned: {earned:,} 🪙\n"
             f"Avg/cast: {avg:,} 🪙\n"
             f"Rare catches: {rare_cnt}\n"
             f"Luxe sessions: {luxe_sess}")


# ── !activitydash ─────────────────────────────────────────────────────────────

async def handle_activitydash(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _perm(user):
        await _w(bot, user.id, "🔒 Owner/Admin only.")
        return

    sub = args[1].lower().strip() if len(args) > 1 else ""
    if sub == "fishing" or sub == "fish":
        await handle_fishdash(bot, user, args)
    else:
        await handle_minedash(bot, user, args)


# ── !hourlyaudit ──────────────────────────────────────────────────────────────

async def handle_hourlyaudit(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _perm(user):
        await _w(bot, user.id, "🔒 Owner/Admin only.")
        return

    sub = args[1].lower().strip() if len(args) > 1 else ""
    conn = db.get_connection()

    # Mining: sum coins in mining_logs for last 24 hours / 24 = per-hour avg
    mine_24h = _q1(conn,
        "SELECT COALESCE(SUM(coins),0) FROM mining_logs "
        "WHERE timestamp >= datetime('now', '-24 hours')", default=0)
    mine_hr  = int((mine_24h or 0) / 24)

    fish_24h = _q1(conn,
        "SELECT COALESCE(SUM(final_value),0) FROM fish_catch_records "
        "WHERE caught_at >= datetime('now', '-24 hours')", default=0)
    fish_hr  = int((fish_24h or 0) / 24)

    luxe_24h = _q1(conn,
        "SELECT COALESCE(SUM(gold_amount),0) FROM gold_tip_events "
        "WHERE created_at >= datetime('now', '-24 hours')", default=0)
    luxe_hr  = int((luxe_24h or 0) / 24)
    conn.close()

    # Target caps
    MINE_TARGET = 125_000
    FISH_TARGET = 125_000

    if sub == "mining" or sub == "mine":
        status = "OK" if mine_hr <= MINE_TARGET else "⚠️ HIGH"
        await _w(bot, user.id,
                 f"⏱️ Mining Hourly\n"
                 f"Observed: {mine_hr:,} 🪙/hr\n"
                 f"Target: <{MINE_TARGET:,} 🪙/hr\n"
                 f"Status: {status}")
    elif sub == "fishing" or sub == "fish":
        status = "OK" if fish_hr <= FISH_TARGET else "⚠️ HIGH"
        await _w(bot, user.id,
                 f"⏱️ Fishing Hourly\n"
                 f"Observed: {fish_hr:,} 🪙/hr\n"
                 f"Target: <{FISH_TARGET:,} 🪙/hr\n"
                 f"Status: {status}")
    elif sub == "luxe":
        await _w(bot, user.id,
                 f"⏱️ Luxe Hourly\n"
                 f"Tips/hr: {luxe_hr:,} 🎫\n"
                 f"24h total: {luxe_24h:,} 🎫")
    else:
        m_st = "OK" if mine_hr <= MINE_TARGET else "⚠️"
        f_st = "OK" if fish_hr <= FISH_TARGET else "⚠️"
        await _w(bot, user.id,
                 f"⏱️ Hourly Audit\n"
                 f"Mining: {mine_hr:,} 🪙/hr [{m_st}]\n"
                 f"Fishing: {fish_hr:,} 🪙/hr [{f_st}]\n"
                 f"Luxe: {luxe_hr:,} 🎫/hr\n"
                 f"Target: <{MINE_TARGET:,} 🪙/hr")


# ── !shopdash ─────────────────────────────────────────────────────────────────

async def handle_shopdash(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _perm(user):
        await _w(bot, user.id, "🔒 Owner/Admin only.")
        return

    sub = args[1].lower().strip() if len(args) > 1 else "today"

    if sub == "luxe":
        await _shopdash_luxe(bot, user, "today")
        return
    if sub == "coins":
        await _shopdash_coins(bot, user, "today")
        return

    period  = sub if sub in ("today", "yesterday", "7d", "30d") else "today"
    label   = _period_label(period)
    ph_expr = _date_expr("created_at", period)
    conn    = db.get_connection()

    total_ct  = _q1(conn,
        f"SELECT COUNT(*) FROM purchase_history WHERE {ph_expr}", default=0)
    total_sp  = _q1(conn,
        f"SELECT COALESCE(SUM(price),0) FROM purchase_history WHERE {ph_expr}",
        default=0)
    top_item_row = _q(conn,
        f"SELECT item_id, COUNT(*) AS cnt FROM purchase_history "
        f"WHERE {ph_expr} GROUP BY item_id ORDER BY cnt DESC LIMIT 1")
    top_item  = top_item_row[0]["item_id"][:18] if top_item_row else "N/A"
    luxe_ids  = ("'vip','automine_1h','autofish_1h',"
                 "'coin_pack_s','coin_pack_m','coin_pack_l','coin_pack_max'")
    luxe_buys = _q1(conn,
        f"SELECT COUNT(*) FROM purchase_history "
        f"WHERE item_id IN ({luxe_ids}) AND {ph_expr}", default=0)
    conn.close()

    await _w(bot, user.id,
             f"🛒 Shop Dashboard {label}\n"
             f"Purchases: {total_ct}\n"
             f"Spent: {total_sp:,} 🪙\n"
             f"Top item: {top_item}\n"
             f"Luxe buys: {luxe_buys}")
    await _shopdash_luxe(bot, user, period)


async def _shopdash_luxe(bot: BaseBot, user: User, period: str) -> None:
    conn    = db.get_connection()
    ph_expr = _date_expr("created_at", period)

    def _cnt(item_id: str) -> int:
        return _q1(conn,
            f"SELECT COUNT(*) FROM purchase_history WHERE item_id=? AND {ph_expr}",
            (item_id,), default=0) or 0

    lines = [
        "🎫 Luxe Shop Stats",
        f"VIP: {_cnt('vip')}",
        f"Auto-Mine 1h: {_cnt('automine_1h')}",
        f"Auto-Fish 1h: {_cnt('autofish_1h')}",
        f"Coin Pack S: {_cnt('coin_pack_s')}",
        f"Coin Pack M: {_cnt('coin_pack_m')}",
        f"Coin Pack L: {_cnt('coin_pack_l')}",
        f"Coin Pack Max: {_cnt('coin_pack_max')}",
    ]
    conn.close()
    await _send_lines(bot, user.id, lines)


async def _shopdash_coins(bot: BaseBot, user: User, period: str) -> None:
    conn    = db.get_connection()
    ph_expr = _date_expr("created_at", period)
    rows    = _q(conn,
        f"SELECT item_id, COUNT(*) AS cnt, COALESCE(SUM(price),0) AS total "
        f"FROM purchase_history "
        f"WHERE item_id NOT IN ('vip','automine_1h','autofish_1h',"
        f"'coin_pack_s','coin_pack_m','coin_pack_l','coin_pack_max') "
        f"AND {ph_expr} GROUP BY item_id ORDER BY total DESC LIMIT 5")
    conn.close()

    if not rows:
        await _w(bot, user.id, "🪙 Coin Shop: no purchases yet.")
        return

    lines = ["🪙 Coin Shop Stats"]
    for r in rows:
        lines.append(f"{r['item_id'][:16]}: {r['cnt']}x — {r['total']:,} 🪙")
    await _send_lines(bot, user.id, lines)


# ── !vipdash ──────────────────────────────────────────────────────────────────

async def handle_vipdash(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _perm(user):
        await _w(bot, user.id, "🔒 Owner/Admin only.")
        return

    sub  = args[1].lower().strip() if len(args) > 1 else ""
    conn = db.get_connection()

    # Active VIPs: owned_items or purchase_history item_id='vip' and expiry in future
    # Try owned_items first, fall back to purchase_history
    active_vip = 0
    try:
        active_vip = _q1(conn,
            "SELECT COUNT(*) FROM owned_items WHERE item_id='vip' "
            "AND (expires_at IS NULL OR expires_at > datetime('now'))",
            default=0) or 0
    except Exception:
        try:
            active_vip = _q1(conn,
                "SELECT COUNT(*) FROM purchase_history "
                "WHERE item_id='vip'", default=0) or 0
        except Exception:
            pass

    new_today = _q1(conn,
        "SELECT COUNT(*) FROM purchase_history "
        "WHERE item_id='vip' AND DATE(created_at) = DATE('now')", default=0)

    tickets_spent = _q1(conn,
        "SELECT COALESCE(SUM(price),0) FROM purchase_history "
        "WHERE item_id='vip'", default=0)

    if sub == "list":
        rows = _q(conn,
            "SELECT username, expires_at FROM owned_items "
            "WHERE item_id='vip' AND (expires_at IS NULL OR expires_at > datetime('now')) "
            "ORDER BY expires_at ASC LIMIT 10")
        conn.close()
        if not rows:
            await _w(bot, user.id, "👑 Active VIPs: none found.")
            return
        lines = ["👑 Active VIPs"]
        for r in rows:
            exp = (r["expires_at"] or "permanent")[:10]
            lines.append(f"@{r['username'][:16]} — {exp}")
        await _send_lines(bot, user.id, lines)
        return

    conn.close()
    await _w(bot, user.id,
             f"👑 VIP Dashboard\n"
             f"Active VIPs: {active_vip}\n"
             f"New today: {new_today}\n"
             f"Tickets spent: {tickets_spent:,} 🎫")


# ── !retentiondash ────────────────────────────────────────────────────────────

async def handle_retentiondash(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _perm(user):
        await _w(bot, user.id, "🔒 Owner/Admin only.")
        return

    period  = _parse_period(args)
    label   = _period_label(period)
    ob_expr = _date_expr("created_at", period)
    dc_expr = _date_expr("last_claim", period)
    conn    = db.get_connection()

    new_p   = _q1(conn, f"SELECT COUNT(*) FROM player_onboarding WHERE {ob_expr}",
                  default=0)
    ob_done = _q1(conn,
        f"SELECT COUNT(*) FROM player_onboarding WHERE completed=1 AND {ob_expr}",
        default=0)
    daily_done = _q1(conn,
        f"SELECT COUNT(DISTINCT user_id) FROM daily_claims WHERE {dc_expr}",
        default=0)
    returning  = max(0, (daily_done or 0) - (new_p or 0))

    # Weekly missions best-effort
    weekly_done = 0
    try:
        wm_expr = _date_expr("completed_at", period)
        weekly_done = _q1(conn,
            f"SELECT COUNT(DISTINCT user_id) FROM weekly_mission_claims WHERE {wm_expr}",
            default=0) or 0
    except Exception:
        pass

    # Daily missions best-effort
    miss_done = 0
    try:
        mc_expr = _date_expr("claimed_at", period)
        miss_done = _q1(conn,
            f"SELECT COUNT(*) FROM mission_claims WHERE {mc_expr}", default=0) or 0
    except Exception:
        pass

    conn.close()
    await _w(bot, user.id,
             f"📌 Retention {label}\n"
             f"New: {new_p}\n"
             f"Tutorial complete: {ob_done}\n"
             f"Daily missions: {miss_done}\n"
             f"Weekly progress: {weekly_done}\n"
             f"Returning: {returning}")


# ── !eventdash ────────────────────────────────────────────────────────────────

async def handle_eventdash(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _perm(user):
        await _w(bot, user.id, "🔒 Owner/Admin only.")
        return

    sub = args[1].lower().strip() if len(args) > 1 else "current"
    conn = db.get_connection()

    # Active event
    ev = db.get_active_event()
    if not ev and sub in ("current", ""):
        conn.close()
        await _w(bot, user.id, "🎉 Event Performance\nNo active event.")
        return

    ev_id = ev["event_id"] if ev else "N/A"

    # Mining activity during current event (since event started)
    ev_since = ev.get("started_at", "") if ev else ""
    if ev_since:
        mine_ct  = _q1(conn,
            "SELECT COUNT(*) FROM mining_logs WHERE action='mine' AND timestamp >= ?",
            (ev_since,), default=0)
        fish_ct  = _q1(conn,
            "SELECT COUNT(*) FROM fish_catch_records WHERE caught_at >= ?",
            (ev_since,), default=0)
        ep_rows  = _q(conn,
            "SELECT COUNT(DISTINCT user_id) AS players, COALESCE(SUM(points),0) AS pts "
            "FROM event_points WHERE season_key=?", (ev_id,))
        players  = ep_rows[0]["players"] if ep_rows else 0
        pts      = ep_rows[0]["pts"]     if ep_rows else 0
    else:
        mine_ct = fish_ct = players = pts = 0

    conn.close()
    await _w(bot, user.id,
             f"🎉 Event Performance\n"
             f"{ev_id}\n"
             f"Players: {players}\n"
             f"Mines: {mine_ct:,}\n"
             f"Fish: {fish_ct:,}\n"
             f"Season points: {pts:,}")


# ── !seasondash ───────────────────────────────────────────────────────────────

async def handle_seasondash(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _perm(user):
        await _w(bot, user.id, "🔒 Owner/Admin only.")
        return

    conn = db.get_connection()

    # Most recent season key
    sk_row = _q(conn,
        "SELECT season_key FROM season_points GROUP BY season_key "
        "ORDER BY MAX(updated_at) DESC LIMIT 1")
    sk = sk_row[0]["season_key"] if sk_row else None

    if not sk:
        conn.close()
        await _w(bot, user.id,
                 "🏆 Season Dashboard\nNo season data yet.")
        return

    mine_players = _q1(conn,
        "SELECT COUNT(DISTINCT user_id) FROM season_points "
        "WHERE season_key=? AND category='mining'", (sk,), default=0)
    fish_players = _q1(conn,
        "SELECT COUNT(DISTINCT user_id) FROM season_points "
        "WHERE season_key=? AND category='fishing'", (sk,), default=0)
    paid = _q1(conn,
        "SELECT COUNT(*) FROM season_reward_history WHERE season_key=?",
        (sk,), default=0)

    # Top players per category
    top_mine_row = _q(conn,
        "SELECT username FROM season_points WHERE season_key=? AND category='mining' "
        "ORDER BY points DESC LIMIT 1", (sk,))
    top_fish_row = _q(conn,
        "SELECT username FROM season_points WHERE season_key=? AND category='fishing' "
        "ORDER BY points DESC LIMIT 1", (sk,))
    top_coll_row = _q(conn,
        "SELECT username FROM season_points WHERE season_key=? AND category='collection' "
        "ORDER BY points DESC LIMIT 1", (sk,))

    top_mine = ("@" + top_mine_row[0]["username"][:14]) if top_mine_row else "N/A"
    top_fish = ("@" + top_fish_row[0]["username"][:14]) if top_fish_row else "N/A"
    top_coll = ("@" + top_coll_row[0]["username"][:14]) if top_coll_row else "N/A"
    conn.close()

    payout_str = f"Paid: {paid}" if paid else "unpaid"

    await _w(bot, user.id,
             f"🏆 Season Dashboard\n"
             f"Key: {sk[:20]}\n"
             f"Mining players: {mine_players}\n"
             f"Fishing players: {fish_players}\n"
             f"Payout: {payout_str}")
    await _w(bot, user.id,
             f"Top:\n"
             f"Mining: {top_mine}\n"
             f"Fishing: {top_fish}\n"
             f"Collection: {top_coll}")


# ── Analytics help ────────────────────────────────────────────────────────────

ANALYTICS_HELP_1 = (
    "📊 Analytics\n"
    "!ownerdash [today|7d|30d|safety]\n"
    "!economydash [today|7d|sources|sinks]\n"
    "!luxedash [today|7d|purchases|conversions]\n"
    "!playerstats [today|7d]\n"
    "!retentiondash [today|7d]\n"
    "!eventdash | !seasondash"
)

ANALYTICS_HELP_2 = (
    "📊 More Analytics\n"
    "!shopdash [today|7d|luxe|coins]\n"
    "!vipdash [list]\n"
    "!hourlyaudit [mining|fishing|luxe]\n"
    "!conversiondash\n"
    "!minedash | !fishdash\n"
    "!activitydash [mining|fishing]"
)


async def handle_analyticsdash(bot: BaseBot, user: User, args: list[str]) -> None:
    """!analytics — help menu for all analytics commands."""
    if not _perm(user):
        await _w(bot, user.id, "🔒 Owner/Admin only.")
        return
    await _w(bot, user.id, ANALYTICS_HELP_1)
    await _w(bot, user.id, ANALYTICS_HELP_2)
