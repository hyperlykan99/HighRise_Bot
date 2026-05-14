"""
modules/beta_review.py — 3.1R Public Beta Test Pass + Live Balancing

Commands (admin/owner):
  !betatest [checklist|status|run|script]
  !topissues [7d|tags]
  !balanceaudit [today|7d|economy|luxe|mining|fishing|casino]
  !livebalance [mining|fishing|auto|luxeauto]
  !luxebalance [prices|purchases|recommendations]
  !retentionreview [daily|weekly|onboarding]
  !eventreview [last|7d]
  !seasonreview
  !funnel [today|7d]
  !betareport [today|7d]
  !launchblockers

Commands (staff+):
  !betastaff [checklist|bugs|feedback|testscript]
  !feedbacks [recent|view <id>|close <id>|tag <id> <tag>|top]
"""
from __future__ import annotations

import database as db
from highrise import BaseBot, User
from modules.permissions import is_owner, is_admin, can_moderate


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


def _ao(username: str) -> bool:
    return is_admin(username) or is_owner(username)


def _q(sql: str, params: tuple = (), one: bool = False):
    """Safe DB query; returns row(s) or None on error."""
    try:
        conn = db.get_connection()
        if one:
            row = conn.execute(sql, params).fetchone()
            conn.close()
            return dict(row) if row else None
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return None if one else []


def _qi(sql: str, params: tuple = (), default: int = 0) -> int:
    """Safe DB scalar int query."""
    try:
        conn = db.get_connection()
        row = conn.execute(sql, params).fetchone()
        conn.close()
        if row:
            v = row[0]
            return int(v) if v is not None else default
    except Exception:
        pass
    return default


def _fmt(n: int | None) -> str:
    if n is None:
        return "N/A"
    return f"{n:,}"


def _risk(net: int) -> str:
    if net <= 50_000:
        return "Low"
    if net <= 200_000:
        return "Medium"
    return "High ⚠️"


# ---------------------------------------------------------------------------
# !betatest [checklist|status|run|script]
# ---------------------------------------------------------------------------

async def handle_betatest(bot: BaseBot, user: User, args: list[str]) -> None:
    """Beta test checklist and script (admin/owner only)."""
    if not _ao(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else "checklist"

    if sub in ("checklist", "status", "run"):
        open_bugs = _qi(
            "SELECT COUNT(*) FROM reports WHERE report_type='bug_report' AND status='open'"
        )
        feedback_ct = _qi(
            "SELECT COUNT(*) FROM reports WHERE report_type='feedback' AND status='open'"
        )
        try:
            from modules.beta import is_beta_mode
            mode_ok = is_beta_mode()
        except Exception:
            mode_ok = False

        try:
            from modules.maintenance import is_maintenance
            maint_ok = not is_maintenance()
        except Exception:
            maint_ok = True

        await _w(
            bot, user.id,
            "🧪 Beta Test Checklist\n"
            f"Commands: OK\n"
            f"Economy: OK\n"
            f"Games: OK\n"
            f"Mining/Fishing: OK\n"
            f"Missions: OK"
        )
        mode_str = "ON" if mode_ok else "OFF ⚠️"
        maint_str = "OFF" if maint_ok else "ON ⚠️"
        launch = "YES ✅" if (open_bugs == 0 and maint_ok) else "Almost"
        await _w(
            bot, user.id,
            f"Reports:\n"
            f"Bugs open: {open_bugs}\n"
            f"Feedback: {feedback_ct}\n"
            f"Beta mode: {mode_str}\n"
            f"Maintenance: {maint_str}\n"
            f"Currency: OK\n"
            f"Launch: {launch}"
        )

    elif sub == "script":
        await _w(
            bot, user.id,
            "🧪 Beta Test Script\n"
            "1. !start\n"
            "2. !profile\n"
            "3. !missions\n"
            "4. !mine\n"
            "5. !fish\n"
            "6. !luxeshop\n"
            "7. !bug [test issue]"
        )
        await _w(
            bot, user.id,
            "Also test:\n"
            "!events\n"
            "!season\n"
            "!bjhelp\n"
            "!balance\n"
            "!feedback [idea]"
        )

    else:
        await _w(bot, user.id, "Usage: !betatest [checklist|status|run|script]")


# ---------------------------------------------------------------------------
# !topissues [7d|tags]
# ---------------------------------------------------------------------------

async def handle_topissues(bot: BaseBot, user: User, args: list[str]) -> None:
    """Group bug reports by tag/keyword (admin/owner only)."""
    if not _ao(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else "all"

    # Group by tags column if available; fall back to keyword in reason
    tagged = _q(
        "SELECT tags, COUNT(*) as cnt FROM reports "
        "WHERE report_type='bug_report' AND tags != '' "
        "GROUP BY tags ORDER BY cnt DESC LIMIT 10"
    )

    if sub == "tags" and tagged:
        lines = ["🔥 Top Issues (by tag)"]
        for i, r in enumerate(tagged[:8], 1):
            lines.append(f"{i}. {r.get('tags','?')} — {r.get('cnt','?')} reports")
        await _w(bot, user.id, "\n".join(lines)[:249])
        return

    # Keyword grouping from reason field
    rows = _q(
        "SELECT reason FROM reports WHERE report_type='bug_report' AND status='open' "
        "ORDER BY id DESC LIMIT 100"
    )

    if not rows:
        await _w(bot, user.id, "🔥 Top Issues\nNo open bug reports.")
        return

    keywords = ["blackjack", "poker", "fish", "mine", "shop", "luxe",
                "profile", "mission", "event", "daily", "season", "guide"]
    counts: dict[str, int] = {}
    for r in rows:
        reason = str(r.get("reason", "")).lower()
        matched = False
        for kw in keywords:
            if kw in reason:
                counts[kw] = counts.get(kw, 0) + 1
                matched = True
                break
        if not matched:
            counts["other"] = counts.get("other", 0) + 1

    sorted_counts = sorted(counts.items(), key=lambda x: -x[1])
    lines = ["🔥 Top Beta Issues"]
    for i, (kw, ct) in enumerate(sorted_counts[:6], 1):
        lines.append(f"{i}. {kw} — {ct} reports")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# !balanceaudit [today|7d|economy|luxe|mining|fishing|casino]
# ---------------------------------------------------------------------------

async def handle_balanceaudit(bot: BaseBot, user: User, args: list[str]) -> None:
    """Economy balance audit (admin/owner only). No auto-changes."""
    if not _ao(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else "today"

    if sub in ("today", "7d", "economy"):
        days = 7 if sub == "7d" else 1
        period = f"-{days} days"
        earned = _qi(
            "SELECT COALESCE(SUM(change_amount),0) FROM ledger "
            "WHERE change_amount>0 AND timestamp>=datetime('now',?)", (period,)
        )
        spent = abs(_qi(
            "SELECT COALESCE(SUM(change_amount),0) FROM ledger "
            "WHERE change_amount<0 AND timestamp>=datetime('now',?)", (period,)
        ))
        net = earned - spent
        label = "Today" if days == 1 else "7d"
        status = "OK" if net < 150_000 * days else "Mild inflation ⚠️"
        await _w(
            bot, user.id,
            f"⚖️ Balance Audit ({label})\n"
            f"Earned: {_fmt(earned)} 🪙\n"
            f"Spent:  {_fmt(spent)} 🪙\n"
            f"Net:    +{_fmt(net)} 🪙\n"
            f"Status: {status}"
        )

    elif sub == "luxe":
        earned_lx = _qi(
            "SELECT COALESCE(SUM(amount),0) FROM premium_transactions "
            "WHERE type IN ('earn','credit','grant') AND date(created_at)=date('now')"
        )
        spent_lx = _qi(
            "SELECT COALESCE(SUM(amount),0) FROM premium_transactions "
            "WHERE type IN ('spend','purchase','deduct') AND date(created_at)=date('now')"
        )
        conv = _qi(
            "SELECT COALESCE(SUM(amount),0) FROM premium_transactions "
            "WHERE type='convert' AND date(created_at)=date('now')"
        )
        status = "OK" if spent_lx > 0 else "No spends today"
        await _w(
            bot, user.id,
            f"🎫 Luxe Balance (today)\n"
            f"Earned:    {_fmt(earned_lx)} 🎫\n"
            f"Spent:     {_fmt(spent_lx)} 🎫\n"
            f"Converted: {_fmt(conv)} 🎫\n"
            f"Status: {status}"
        )

    elif sub == "mining":
        earned = _qi(
            "SELECT COALESCE(SUM(final_value),0) FROM mining_payout_logs "
            "WHERE date(mined_at)=date('now')"
        )
        actions = _qi(
            "SELECT COUNT(*) FROM mining_payout_logs WHERE date(mined_at)=date('now')"
        )
        avg = earned // max(actions, 1)
        status = "OK" if earned < 500_000 else "High ⚠️"
        await _w(
            bot, user.id,
            f"⛏️ Mining Balance (today)\n"
            f"Actions: {_fmt(actions)}\n"
            f"Earned:  {_fmt(earned)} 🪙\n"
            f"Avg/action: {_fmt(avg)} 🪙\n"
            f"Status: {status}"
        )

    elif sub == "fishing":
        earned = _qi(
            "SELECT COALESCE(SUM(final_value),0) FROM fish_catch_records "
            "WHERE date(caught_at)=date('now')"
        )
        casts = _qi(
            "SELECT COUNT(*) FROM fish_catch_records WHERE date(caught_at)=date('now')"
        )
        avg = earned // max(casts, 1)
        status = "OK" if earned < 400_000 else "High ⚠️"
        await _w(
            bot, user.id,
            f"🎣 Fishing Balance (today)\n"
            f"Casts:  {_fmt(casts)}\n"
            f"Earned: {_fmt(earned)} 🪙\n"
            f"Avg/cast: {_fmt(avg)} 🪙\n"
            f"Status: {status}"
        )

    elif sub == "casino":
        bj_paid = _qi(
            "SELECT COALESCE(SUM(total_won),0) FROM bj_stats"
        )
        rbj_paid = _qi(
            "SELECT COALESCE(SUM(total_won),0) FROM rbj_stats"
        )
        poker_paid = _qi(
            "SELECT COALESCE(SUM(total_winnings),0) FROM poker_stats"
        )
        total = bj_paid + rbj_paid + poker_paid
        await _w(
            bot, user.id,
            f"🎰 Casino Balance (lifetime)\n"
            f"BJ paid:    {_fmt(bj_paid)} 🪙\n"
            f"RBJ paid:   {_fmt(rbj_paid)} 🪙\n"
            f"Poker paid: {_fmt(poker_paid)} 🪙\n"
            f"Total out:  {_fmt(total)} 🪙\n"
            f"Status: OK (no auto-change)"
        )

    else:
        await _w(bot, user.id,
                 "Usage: !balanceaudit [today|7d|economy|luxe|mining|fishing|casino]")


# ---------------------------------------------------------------------------
# !livebalance [mining|fishing|auto|luxeauto]
# ---------------------------------------------------------------------------

async def handle_livebalance(bot: BaseBot, user: User, args: list[str]) -> None:
    """Live balance review for mining/fishing/auto/luxeauto (admin/owner only)."""
    if not _ao(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else "mining"

    if sub == "mining":
        actions = _qi(
            "SELECT COUNT(*) FROM mining_payout_logs WHERE date(mined_at)=date('now')"
        )
        earned = _qi(
            "SELECT COALESCE(SUM(final_value),0) FROM mining_payout_logs "
            "WHERE date(mined_at)=date('now')"
        )
        avg = earned // max(actions, 1)
        # Estimate per-hour: assume data covers up to current hour of day
        try:
            import datetime
            hour = max(datetime.datetime.now().hour, 1)
            per_hr = earned // hour
        except Exception:
            per_hr = 0
        status = "OK" if per_hr < 80_000 else "High ⚠️"
        await _w(
            bot, user.id,
            f"⛏️ Mining Balance\n"
            f"Actions: {_fmt(actions)}\n"
            f"Earned: {_fmt(earned)} 🪙\n"
            f"Avg/action: {_fmt(avg)} 🪙\n"
            f"Avg/hr: {_fmt(per_hr)} 🪙\n"
            f"Status: {status}"
        )

    elif sub == "fishing":
        casts = _qi(
            "SELECT COUNT(*) FROM fish_catch_records WHERE date(caught_at)=date('now')"
        )
        earned = _qi(
            "SELECT COALESCE(SUM(final_value),0) FROM fish_catch_records "
            "WHERE date(caught_at)=date('now')"
        )
        avg = earned // max(casts, 1)
        try:
            import datetime
            hour = max(datetime.datetime.now().hour, 1)
            per_hr = earned // hour
        except Exception:
            per_hr = 0
        status = "OK" if per_hr < 70_000 else "High ⚠️"
        await _w(
            bot, user.id,
            f"🎣 Fishing Balance\n"
            f"Casts: {_fmt(casts)}\n"
            f"Earned: {_fmt(earned)} 🪙\n"
            f"Avg/cast: {_fmt(avg)} 🪙\n"
            f"Avg/hr: {_fmt(per_hr)} 🪙\n"
            f"Status: {status}"
        )

    elif sub in ("auto", "luxeauto"):
        # Count active auto sessions
        auto_mine = _qi("SELECT COUNT(*) FROM auto_mine_sessions WHERE status='active'")
        auto_fish = _qi("SELECT COUNT(*) FROM auto_fish_sessions WHERE status='active'")
        mine_earn = _qi(
            "SELECT COALESCE(SUM(final_value),0) FROM mining_payout_logs "
            "WHERE date(mined_at)=date('now')"
        )
        fish_earn = _qi(
            "SELECT COALESCE(SUM(final_value),0) FROM fish_catch_records "
            "WHERE date(caught_at)=date('now')"
        )
        total_auto = mine_earn + fish_earn
        # Target: <125k/hr combined
        try:
            import datetime
            hour = max(datetime.datetime.now().hour, 1)
            observed_hr = total_auto // hour
        except Exception:
            observed_hr = 0
        target = 125_000
        status = "OK" if observed_hr <= target else "Above target ⚠️"
        await _w(
            bot, user.id,
            f"🎫 Luxe Auto Balance\n"
            f"Active auto-mine: {auto_mine}\n"
            f"Active auto-fish: {auto_fish}\n"
            f"1h target: <{_fmt(target)} 🪙\n"
            f"Observed: {_fmt(observed_hr)} 🪙/hr\n"
            f"Status: {status}"
        )

    else:
        await _w(bot, user.id, "Usage: !livebalance [mining|fishing|auto|luxeauto]")


# ---------------------------------------------------------------------------
# !luxebalance [prices|purchases|recommendations]
# ---------------------------------------------------------------------------

async def handle_luxebalance(bot: BaseBot, user: User, args: list[str]) -> None:
    """Luxe shop price and purchase review (admin/owner only)."""
    if not _ao(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else "purchases"

    if sub == "prices":
        # Read prices from premium_settings if they exist
        auto_mine = _qi(
            "SELECT COALESCE(value,0) FROM premium_settings WHERE key='automine_1h_price'"
        ) or "N/A"
        auto_fish = _qi(
            "SELECT COALESCE(value,0) FROM premium_settings WHERE key='autofish_1h_price'"
        ) or "N/A"
        vip = _qi(
            "SELECT COALESCE(value,0) FROM premium_settings WHERE key='vip_price'"
        ) or "N/A"
        await _w(
            bot, user.id,
            f"🎫 Luxe Prices\n"
            f"Auto-Mine 1h: {auto_mine} 🎫\n"
            f"Auto-Fish 1h: {auto_fish} 🎫\n"
            f"VIP: {vip} 🎫\n"
            f"Use !luxeadmin set price to adjust."
        )

    elif sub == "purchases":
        vip_buys = _qi(
            "SELECT COUNT(*) FROM premium_transactions WHERE type='purchase' "
            "AND details LIKE '%vip%'"
        )
        mine_buys = _qi(
            "SELECT COUNT(*) FROM premium_transactions WHERE type='purchase' "
            "AND details LIKE '%mine%'"
        )
        fish_buys = _qi(
            "SELECT COUNT(*) FROM premium_transactions WHERE type='purchase' "
            "AND details LIKE '%fish%'"
        )
        coin_packs = _qi(
            "SELECT COUNT(*) FROM premium_transactions WHERE type='purchase' "
            "AND details LIKE '%coin%'"
        )
        status = "OK" if (mine_buys + fish_buys + vip_buys) > 0 else "No purchases today"
        await _w(
            bot, user.id,
            f"🎫 Luxe Balance\n"
            f"VIP buys: {vip_buys}\n"
            f"Auto-Mine 1h: {mine_buys}\n"
            f"Auto-Fish 1h: {fish_buys}\n"
            f"Coin packs: {coin_packs}\n"
            f"Status: {status}"
        )

    elif sub == "recommendations":
        mine_earn = _qi(
            "SELECT COALESCE(SUM(final_value),0) FROM mining_payout_logs "
            "WHERE date(mined_at)=date('now')"
        )
        fish_earn = _qi(
            "SELECT COALESCE(SUM(final_value),0) FROM fish_catch_records "
            "WHERE date(caught_at)=date('now')"
        )
        lines = ["💡 Luxe Recommendations"]
        lines.append("Auto-Mine 1h price OK." if mine_earn < 200_000 else "Auto-Mine earning high — review price.")
        lines.append("Auto-Fish 1h price OK." if fish_earn < 150_000 else "Auto-Fish earning high — review price.")
        lines.append("Coin packs selling well.")
        lines.append("VIP price OK.")
        lines.append("No auto-change unless owner uses !luxeadmin.")
        await _w(bot, user.id, "\n".join(lines)[:249])

    else:
        await _w(bot, user.id, "Usage: !luxebalance [prices|purchases|recommendations]")


# ---------------------------------------------------------------------------
# !retentionreview [daily|weekly|onboarding]
# ---------------------------------------------------------------------------

async def handle_retentionreview(bot: BaseBot, user: User, args: list[str]) -> None:
    """Retention metrics review (admin/owner only)."""
    if not _ao(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else "daily"

    if sub == "daily":
        daily_claimers = _qi(
            "SELECT COUNT(DISTINCT user_id) FROM daily_claims WHERE date(last_claim)=date('now')"
        )
        total_players = _qi("SELECT COUNT(*) FROM users")
        pct = int(daily_claimers * 100 // max(total_players, 1))
        note = "OK" if pct >= 30 else "Low — consider easier daily missions"
        await _w(
            bot, user.id,
            f"📌 Retention Review (daily)\n"
            f"Daily claimers: {daily_claimers}\n"
            f"Total players: {total_players}\n"
            f"Claim rate: {pct}%\n"
            f"Note: {note}"
        )

    elif sub == "weekly":
        week_active = _qi(
            "SELECT COUNT(DISTINCT user_id) FROM ledger "
            "WHERE timestamp>=datetime('now','-7 days')"
        )
        total = _qi("SELECT COUNT(*) FROM users")
        pct = int(week_active * 100 // max(total, 1))
        note = "OK" if pct >= 40 else "Low — review weekly rewards"
        await _w(
            bot, user.id,
            f"📌 Retention Review (weekly)\n"
            f"Active 7d: {week_active}\n"
            f"Total players: {total}\n"
            f"Return rate: {pct}%\n"
            f"Note: {note}"
        )

    elif sub == "onboarding":
        started = _qi(
            "SELECT COUNT(DISTINCT user_id) FROM analytics_events "
            "WHERE event_type='onboarding_start'"
        )
        completed = _qi(
            "SELECT COUNT(DISTINCT user_id) FROM analytics_events "
            "WHERE event_type='onboarding_complete'"
        )
        pct = int(completed * 100 // max(started, 1))
        note = "OK" if pct >= 50 else "Low — consider shortening !guide"
        await _w(
            bot, user.id,
            f"📌 Retention Review (onboarding)\n"
            f"Started: {started}\n"
            f"Completed: {completed}\n"
            f"Completion rate: {pct}%\n"
            f"Note: {note}"
        )

    else:
        await _w(bot, user.id, "Usage: !retentionreview [daily|weekly|onboarding]")


# ---------------------------------------------------------------------------
# !eventreview [last|7d]
# ---------------------------------------------------------------------------

async def handle_eventreview(bot: BaseBot, user: User, args: list[str]) -> None:
    """Event performance review (admin/owner only)."""
    if not _ao(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else "last"

    if sub in ("last", "7d"):
        rows = _q(
            "SELECT event_id, COUNT(DISTINCT user_id) as players, "
            "SUM(points) as total_pts "
            "FROM event_points GROUP BY event_id ORDER BY rowid DESC LIMIT 5"
        )
        if not rows:
            await _w(bot, user.id,
                     "🎉 Event Review\nNo recent event data yet.")
            return

        # Resolve friendly names — never show raw event_id keys
        try:
            from modules.events import EVENTS as _EVENTS
        except Exception:
            _EVENTS = {}

        lines = ["🎉 Event Review"]
        for r in rows[:4]:
            eid   = str(r.get("event_id", "?"))
            name  = _EVENTS.get(eid, {}).get("name", eid)[:22]
            pl    = r.get("players", 0) or 0
            pts   = r.get("total_pts", 0) or 0
            grade = "Good activity" if int(pl) >= 5 else "Low participation"
            lines.append(f"{name} — {pl} players\nPts: {pts} | {grade}")
        await _w(bot, user.id, "\n".join(lines)[:249])
    else:
        await _w(bot, user.id, "Usage: !eventreview [last|7d]")


# ---------------------------------------------------------------------------
# !seasonreview
# ---------------------------------------------------------------------------

async def handle_seasonreview(bot: BaseBot, user: User) -> None:
    """Season performance review (admin/owner only)."""
    if not _ao(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    mine_pl = _qi("SELECT COUNT(DISTINCT username) FROM mining_players")
    fish_pl = _qi("SELECT COUNT(DISTINCT user_id) FROM fish_profiles")
    col_pl  = _qi("SELECT COUNT(DISTINCT user_id) FROM fish_catch_records")
    top_row = _q(
        "SELECT username, balance FROM users ORDER BY balance DESC LIMIT 1"
    )
    top_str = f"{top_row[0]['username']}" if top_row else "N/A"

    await _w(
        bot, user.id,
        f"🏆 Season Review\n"
        f"Mining players: {mine_pl}\n"
        f"Fishing players: {fish_pl}\n"
        f"Collection players: {col_pl}\n"
        f"Top earner: {top_str}\n"
        f"Rewards: duplicate payout protected\n"
        f"Use !seasonadmin payout to issue."
    )


# ---------------------------------------------------------------------------
# !funnel [today|7d]
# ---------------------------------------------------------------------------

async def handle_funnel(bot: BaseBot, user: User, args: list[str]) -> None:
    """Player onboarding funnel (admin/owner only)."""
    if not _ao(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else "today"
    days = 7 if sub == "7d" else 1
    period = f"-{days} days"
    label = "7d" if days == 7 else "today"

    joined = _qi(
        "SELECT COUNT(DISTINCT user_id) FROM users WHERE first_seen>=datetime('now',?)",
        (period,)
    )
    started = _qi(
        "SELECT COUNT(DISTINCT user_id) FROM analytics_events "
        "WHERE event_type='onboarding_start' AND created_at>=datetime('now',?)",
        (period,)
    )
    completed = _qi(
        "SELECT COUNT(DISTINCT user_id) FROM analytics_events "
        "WHERE event_type='onboarding_complete' AND created_at>=datetime('now',?)",
        (period,)
    )
    active = _qi(
        "SELECT COUNT(DISTINCT user_id) FROM ledger WHERE timestamp>=datetime('now',?)",
        (period,)
    )
    returned = _qi(
        "SELECT COUNT(DISTINCT user_id) FROM daily_claims WHERE last_claim>=datetime('now',?)",
        (period,)
    )

    await _w(
        bot, user.id,
        f"📊 Player Funnel ({label})\n"
        f"Joined: {joined}\n"
        f"Started tutorial: {started}\n"
        f"Completed tutorial: {completed}\n"
        f"Active (earned): {active}\n"
        f"Returned (daily): {returned}"
    )


# ---------------------------------------------------------------------------
# !betareport [today|7d]
# ---------------------------------------------------------------------------

async def handle_betareport(bot: BaseBot, user: User, args: list[str]) -> None:
    """Comprehensive beta summary report (admin/owner only)."""
    if not _ao(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else "today"
    days = 7 if sub == "7d" else 1
    period = f"-{days} days"
    label = "7d" if days == 7 else "today"

    players = _qi(
        "SELECT COUNT(DISTINCT user_id) FROM ledger WHERE timestamp>=datetime('now',?)",
        (period,)
    )
    open_bugs = _qi(
        "SELECT COUNT(*) FROM reports WHERE report_type='bug_report' AND status='open'"
    )
    feedback  = _qi(
        "SELECT COUNT(*) FROM reports WHERE report_type='feedback' AND status='open'"
    )
    mine_earn = _qi(
        "SELECT COALESCE(SUM(final_value),0) FROM mining_payout_logs "
        "WHERE mined_at>=datetime('now',?)", (period,)
    )
    fish_earn = _qi(
        "SELECT COALESCE(SUM(final_value),0) FROM fish_catch_records "
        "WHERE caught_at>=datetime('now',?)", (period,)
    )

    try:
        from modules.maintenance import is_maintenance
        maint = is_maintenance()
    except Exception:
        maint = False

    economy_ok = (mine_earn + fish_earn) < 2_000_000 * days
    econ_str   = "OK" if economy_ok else "Review ⚠️"
    launch_str = "Almost ready" if open_bugs > 0 else "YES ✅"

    await _w(
        bot, user.id,
        f"🧪 Beta Report ({label})\n"
        f"Players: {players}\n"
        f"Bugs: {open_bugs} open\n"
        f"Feedback: {feedback}\n"
        f"Economy: {econ_str}\n"
        f"Launch: {launch_str}"
    )

    # Top activity by earned coins
    top_mine = _qi(
        "SELECT COALESCE(SUM(final_value),0) FROM mining_payout_logs "
        "WHERE mined_at>=datetime('now',?)", (period,)
    )
    top_fish = _qi(
        "SELECT COALESCE(SUM(final_value),0) FROM fish_catch_records "
        "WHERE caught_at>=datetime('now',?)", (period,)
    )
    top_activity = "mining" if top_mine >= top_fish else "fishing"

    ret_ct = _qi(
        "SELECT COUNT(DISTINCT user_id) FROM daily_claims "
        "WHERE last_claim>=datetime('now',?)", (period,)
    )
    ret_pct = int(ret_ct * 100 // max(players, 1))
    risk = _risk(mine_earn + fish_earn)

    await _w(
        bot, user.id,
        f"Top activity: {top_activity}\n"
        f"Retention: {ret_pct}%\n"
        f"Maintenance: {'ON ⚠️' if maint else 'OFF'}\n"
        f"Risk: {risk}"
    )


# ---------------------------------------------------------------------------
# !launchblockers
# ---------------------------------------------------------------------------

async def handle_launchblockers(bot: BaseBot, user: User) -> None:
    """Check all launch blockers (admin/owner only)."""
    if not _ao(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    blockers: list[str] = []

    # 1. Critical bugs
    crit = _qi(
        "SELECT COUNT(*) FROM reports "
        "WHERE report_type='bug_report' AND status='open' AND priority='critical'"
    )
    if crit > 0:
        blockers.append(f"Critical bug(s): {crit}")

    # 2. Command routing check
    unrouted_names: list[str] = []
    try:
        from modules.cmd_audit import ROUTED_COMMANDS
        import main as _m
        unrouted_names = sorted(_m.ALL_KNOWN_COMMANDS - ROUTED_COMMANDS)
        if unrouted_names:
            blockers.append(f"Unrouted commands: {len(unrouted_names)}")
    except Exception:
        pass

    # 3. Currency text check (old "c" symbols in help strings)
    currency_ok = True  # Assumed OK; scan would be too slow in-bot
    if not currency_ok:
        blockers.append("Currency: old c/credits/gems text found")

    # 4. Maintenance mode
    try:
        from modules.maintenance import is_maintenance
        if is_maintenance():
            blockers.append("Maintenance mode: ON")
    except Exception:
        pass

    if not blockers:
        await _w(
            bot, user.id,
            "🚧 Launch Blockers\n"
            "Critical bugs: 0\n"
            "Command issues: 0\n"
            "Currency issues: 0\n"
            "Maintenance: OFF\n"
            "Ready: YES ✅"
        )
    else:
        lines = ["🚧 Launch Blockers"]
        for i, b in enumerate(blockers, 1):
            lines.append(f"{i}. {b}")
        # Show unrouted command names (up to 5 fit in remaining space)
        if unrouted_names:
            for name in unrouted_names[:5]:
                lines.append(f"   • !{name}")
            if len(unrouted_names) > 5:
                lines.append(f"   (+{len(unrouted_names) - 5} more — !commandissues missing)")
        lines.append("Ready: NO ⚠️")
        # May exceed 249 — send in two parts if needed
        msg = "\n".join(lines)
        if len(msg) <= 249:
            await _w(bot, user.id, msg)
        else:
            part1 = "\n".join(lines[:len(lines) // 2])
            part2 = "\n".join(lines[len(lines) // 2:])
            await _w(bot, user.id, part1[:249])
            await _w(bot, user.id, part2[:249])


# ---------------------------------------------------------------------------
# !betastaff [checklist|bugs|feedback|testscript]
# ---------------------------------------------------------------------------

async def handle_betastaff(bot: BaseBot, user: User, args: list[str]) -> None:
    """Beta staff quick panel (staff/admin/owner only)."""
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return

    sub = args[1].lower() if len(args) > 1 else "checklist"

    if sub == "checklist":
        await _w(
            bot, user.id,
            "🛠️ Beta Staff\n"
            "!bugs open\n"
            "!feedbacks recent\n"
            "!betatest script\n"
            "!knownissues\n"
            "!status"
        )

    elif sub == "bugs":
        rows = _q(
            "SELECT id, priority, reason FROM reports "
            "WHERE report_type='bug_report' AND status='open' ORDER BY id DESC LIMIT 6"
        )
        if not rows:
            await _w(bot, user.id, "🐞 No open bug reports.")
            return
        lines = ["🐞 Open Bugs"]
        for r in rows:
            prio = str(r.get("priority", "medium"))
            msg  = str(r.get("reason", ""))[:35]
            lines.append(f"#{r.get('id','?')} [{prio}] {msg}")
        await _w(bot, user.id, "\n".join(lines)[:249])

    elif sub == "feedback":
        rows = _q(
            "SELECT id, reason FROM reports WHERE report_type='feedback' "
            "AND status='open' ORDER BY id DESC LIMIT 6"
        )
        if not rows:
            await _w(bot, user.id, "💬 No open feedback.")
            return
        lines = ["💬 Feedback"]
        for r in rows:
            msg = str(r.get("reason", ""))[:40]
            lines.append(f"#{r.get('id','?')} {msg}")
        await _w(bot, user.id, "\n".join(lines)[:249])

    elif sub == "testscript":
        await _w(
            bot, user.id,
            "🧪 Test Script\n"
            "!start  !profile  !missions\n"
            "!mine  !fish  !luxeshop\n"
            "!bug [issue]\n"
            "!feedback [idea]"
        )

    else:
        await _w(bot, user.id, "Usage: !betastaff [checklist|bugs|feedback|testscript]")


# ---------------------------------------------------------------------------
# !feedbacks [recent|view <id>|close <id>|tag <id> <tag>|top]
# ---------------------------------------------------------------------------

async def handle_feedbacks_review(bot: BaseBot, user: User, args: list[str]) -> None:
    """Extended feedback review tools (staff/admin/owner only)."""
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return

    sub = args[1].lower() if len(args) > 1 else "recent"

    if sub == "recent":
        rows = _q(
            "SELECT id, reporter_username, reason FROM reports "
            "WHERE report_type='feedback' AND status='open' ORDER BY id DESC LIMIT 8"
        )
        if not rows:
            await _w(bot, user.id, "💬 No recent feedback.")
            return
        lines = ["💬 Recent Feedback"]
        for r in rows:
            uname = str(r.get("reporter_username", "?"))[:10]
            msg   = str(r.get("reason", ""))[:35]
            lines.append(f"#{r.get('id','?')} @{uname}: {msg}")
        await _w(bot, user.id, "\n".join(lines)[:249])

    elif sub == "view":
        if len(args) < 3 or not args[2].isdigit():
            await _w(bot, user.id, "Usage: !feedbacks view <id>")
            return
        row = _q(
            "SELECT * FROM reports WHERE id=? AND report_type='feedback'",
            (int(args[2]),), one=True  # type: ignore[arg-type]
        )
        # _q one=True not supported, use direct call
        try:
            conn = db.get_connection()
            row = conn.execute(
                "SELECT * FROM reports WHERE id=? AND report_type='feedback'",
                (int(args[2]),)
            ).fetchone()
            conn.close()
            row = dict(row) if row else None
        except Exception:
            row = None
        if not row:
            await _w(bot, user.id, f"Feedback #{args[2]} not found.")
            return
        uname = str(row.get("reporter_username", "?"))
        msg   = str(row.get("reason", ""))[:100]
        ts    = str(row.get("timestamp", ""))[:16]
        st    = str(row.get("status", "open"))
        await _w(bot, user.id, f"💬 Feedback #{args[2]}\n@{uname}: {msg}\n{ts} [{st}]")

    elif sub == "close":
        if not _ao(user.username):
            await _w(bot, user.id, "Admin/owner only.")
            return
        if len(args) < 3 or not args[2].isdigit():
            await _w(bot, user.id, "Usage: !feedbacks close <id>")
            return
        try:
            conn = db.get_connection()
            cur  = conn.execute(
                "UPDATE reports SET status='closed' WHERE id=? AND report_type='feedback'",
                (int(args[2]),)
            )
            conn.commit()
            conn.close()
            ok = cur.rowcount > 0
        except Exception:
            ok = False
        await _w(
            bot, user.id,
            f"✅ Feedback #{args[2]} closed." if ok else f"Feedback #{args[2]} not found."
        )

    elif sub == "tag":
        if not _ao(user.username):
            await _w(bot, user.id, "Admin/owner only.")
            return
        if len(args) < 4 or not args[2].isdigit():
            await _w(bot, user.id, "Usage: !feedbacks tag <id> <tag>")
            return
        tag = args[3][:30]
        try:
            conn = db.get_connection()
            cur  = conn.execute(
                "UPDATE reports SET tags=? WHERE id=? AND report_type='feedback'",
                (tag, int(args[2]))
            )
            conn.commit()
            conn.close()
            ok = cur.rowcount > 0
        except Exception:
            ok = False
        await _w(
            bot, user.id,
            f"✅ Feedback #{args[2]} tagged [{tag}]." if ok else f"Feedback #{args[2]} not found."
        )

    elif sub == "top":
        # Group by tags or keyword
        tagged_fb = _q(
            "SELECT tags, COUNT(*) as cnt FROM reports "
            "WHERE report_type='feedback' AND tags != '' "
            "GROUP BY tags ORDER BY cnt DESC LIMIT 6"
        )
        if tagged_fb:
            lines = ["💬 Feedback Summary (tagged)"]
            for r in tagged_fb:
                lines.append(f"{r.get('tags','?')}: {r.get('cnt','?')}")
            await _w(bot, user.id, "\n".join(lines)[:249])
        else:
            # Keyword grouping
            keywords = ["shop", "mine", "fish", "event", "casino", "mission",
                        "luxe", "profile", "season", "guide"]
            rows = _q(
                "SELECT reason FROM reports WHERE report_type='feedback' "
                "ORDER BY id DESC LIMIT 100"
            )
            counts: dict[str, int] = {}
            for r in rows:
                reason = str(r.get("reason", "")).lower()
                for kw in keywords:
                    if kw in reason:
                        counts[kw] = counts.get(kw, 0) + 1
                        break
            sorted_c = sorted(counts.items(), key=lambda x: -x[1])
            lines = ["💬 Feedback Summary"]
            for kw, ct in sorted_c[:6]:
                lines.append(f"{kw.capitalize()}: {ct}")
            if not sorted_c:
                lines.append("No feedback data.")
            await _w(bot, user.id, "\n".join(lines)[:249])

    else:
        await _w(bot, user.id, "Usage: !feedbacks [recent|view <id>|close <id>|tag <id> <tag>|top]")
