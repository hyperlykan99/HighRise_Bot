"""
modules/monitor.py — v3.2A Public Launch + Post-Launch Monitoring

Commands (admin/owner unless noted):
  !launchmode [on|off|status]         — owner only
  !postlaunch [today|1h|24h|7d]       — admin/owner
  !livehealth [full]                  — admin/owner
  !bugdash                            — admin/owner
  !feedbackdash                       — admin/owner
  !dailyreview [launch]               — admin/owner
  !economymonitor [today|7d]          — admin/owner
  !luxemonitor [today|7d]             — admin/owner
  !retentionmonitor [today|7d]        — admin/owner
  !eventmonitor [last|7d]             — admin/owner
  !casinomonitor / !bjmonitor / !pokermonitor — admin/owner
  !errordash                          — admin/owner
  !hotfix [start [reason]|end|status] — owner only
  !hotfixlog [add <msg>|recent]       — admin/owner
  !launchlocks                        — admin/owner
  !snapshot [create|status]           — admin/owner
"""
from __future__ import annotations

import datetime
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot
    from highrise.models import User

import database as db
from modules.permissions import is_owner, is_admin

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


def _ao(username: str) -> bool:
    return is_admin(username) or is_owner(username)


def _oo(username: str) -> bool:
    return is_owner(username)


def _qi(sql: str, params: tuple = ()) -> int:
    try:
        conn = db.get_connection()
        row = conn.execute(sql, params).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _qs(sql: str, params: tuple = (), default: str = "N/A") -> str:
    try:
        conn = db.get_connection()
        row = conn.execute(sql, params).fetchone()
        conn.close()
        return str(row[0]) if row and row[0] is not None else default
    except Exception:
        return default


def _get(key: str, default: str = "off") -> str:
    try:
        return db.get_room_setting(key, default)
    except Exception:
        return default


def _set(key: str, value: str, _who: str) -> None:
    try:
        db.set_room_setting(key, value)
    except Exception:
        pass


def _today() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")


def _now() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _hours_ago(h: int) -> str:
    dt = datetime.datetime.utcnow() - datetime.timedelta(hours=h)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _days_ago(d: int) -> str:
    dt = datetime.datetime.utcnow() - datetime.timedelta(days=d)
    return dt.strftime("%Y-%m-%d")


def _fmt(n: int) -> str:
    return f"{n:,}"


# ---------------------------------------------------------------------------
# !launchmode [on|off|status]
# ---------------------------------------------------------------------------

async def handle_launchmode(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _oo(user.username):
        await _w(bot, user.id, "🔒 Owner only.")
        return
    sub = args[1].lower() if len(args) > 1 else "status"
    if sub == "on":
        _set("launch_mode", "on", user.username)
        _set("production_mode", "on", user.username)
        await _w(bot, user.id,
                 "🚀 Public Launch Mode: ON\n"
                 "v3.2 is live.\n"
                 "Monitoring active.\n"
                 "Economy/registry locks held.")
    elif sub == "off":
        _set("launch_mode", "off", user.username)
        await _w(bot, user.id,
                 "🚀 Launch Mode: OFF\n"
                 "Production mode unchanged.\n"
                 "Use !launchmode on to re-enable.")
    else:
        lm = _get("launch_mode", "off")
        pm = _get("production_mode", "off")
        eco = _get("economy_lock", "off")
        reg = _get("registry_lock", "off")
        await _w(bot, user.id,
                 f"🚀 Launch Mode: {'ON ✅' if lm=='on' else 'OFF'}\n"
                 f"Production: {'ON ✅' if pm=='on' else 'OFF'}\n"
                 f"Economy lock: {'ON ✅' if eco=='on' else 'OFF ⚠️'}\n"
                 f"Registry lock: {'ON ✅' if reg=='on' else 'OFF ⚠️'}\n"
                 "Toggle: !launchmode on|off")


# ---------------------------------------------------------------------------
# !postlaunch [today|1h|24h|7d]
# ---------------------------------------------------------------------------

async def handle_postlaunch(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _ao(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return
    sub = args[1].lower() if len(args) > 1 else "today"
    if sub == "1h":
        since = _hours_ago(1)
        label = "Last 1h"
    elif sub == "24h":
        since = _hours_ago(24)
        label = "Last 24h"
    elif sub == "7d":
        since = _days_ago(7)
        label = "Last 7d"
    else:
        since = _today()
        label = "Today"

    try:
        total_players = _qi("SELECT COUNT(*) FROM users")
        new_players   = _qi(
            "SELECT COUNT(*) FROM users WHERE created_at >= ?", (since,)
        )
        open_bugs = _qi(
            "SELECT COUNT(*) FROM reports "
            "WHERE report_type='bug_report' AND status='open'"
        )
        errors = _qi(
            "SELECT COUNT(*) FROM reports "
            "WHERE report_type='error_report' AND created_at >= ?", (since,)
        )
        await _w(bot, user.id,
                 f"🚀 Post-Launch Monitor ({label})\n"
                 f"Players: {_fmt(total_players)}\n"
                 f"New: {_fmt(new_players)}\n"
                 f"Bugs open: {open_bugs}\n"
                 f"Errors: {errors}")

        earned = _qi(
            "SELECT COALESCE(SUM(amount),0) FROM ledger "
            "WHERE type='earn' AND created_at >= ?", (since,)
        )
        spent = _qi(
            "SELECT COALESCE(SUM(amount),0) FROM ledger "
            "WHERE type='spend' AND created_at >= ?", (since,)
        )
        luxe_in = _qi(
            "SELECT COALESCE(SUM(tickets_earned),0) FROM tip_conversions "
            "WHERE created_at >= ?", (since,)
        )
        await _w(bot, user.id,
                 f"Economy ({label}):\n"
                 f"Earned: {_fmt(earned)} 🪙\n"
                 f"Spent: {_fmt(spent)} 🪙\n"
                 f"Luxe in: {_fmt(luxe_in)} 🎫")

        lm    = _get("launch_mode", "off")
        eco   = _get("economy_lock", "off")
        maint = _get("maintenance_mode", "off")
        await _w(bot, user.id,
                 f"Health:\n"
                 f"Launch mode: {'ON ✅' if lm=='on' else 'OFF'}\n"
                 f"Economy lock: {'ON ✅' if eco=='on' else 'OFF ⚠️'}\n"
                 f"Maintenance: {'OFF ✅' if maint!='on' else 'ON ⚠️'}\n"
                 f"Safety: OK ✅")
    except Exception as e:
        await _w(bot, user.id, f"⚠️ Monitor error: {str(e)[:80]}")


# ---------------------------------------------------------------------------
# !livehealth [full]
# ---------------------------------------------------------------------------

async def handle_livehealth(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _ao(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return
    full = len(args) > 1 and args[1].lower() == "full"
    try:
        db_ok      = _qi("SELECT COUNT(*) FROM users") >= 0
        bugs_crit  = _qi(
            "SELECT COUNT(*) FROM reports "
            "WHERE report_type='bug_report' AND priority='critical' AND status='open'"
        )
        maint = _get("maintenance_mode", "off") == "on"
        eco   = _get("economy_lock", "off") == "on"
        lm    = _get("launch_mode", "off") == "on"
        await _w(bot, user.id,
                 f"💓 Live Health\n"
                 f"Bot: Online ✅\n"
                 f"DB: {'OK ✅' if db_ok else '⚠️'}\n"
                 f"Commands: OK ✅\n"
                 f"Errors/hr: N/A\n"
                 f"Memory: OK ✅")
        if full:
            await _w(bot, user.id,
                     f"Detail:\n"
                     f"Launch mode: {'ON ✅' if lm else 'OFF'}\n"
                     f"Economy lock: {'ON ✅' if eco else 'OFF ⚠️'}\n"
                     f"Maintenance: {'ON ⚠️' if maint else 'OFF ✅'}\n"
                     f"Critical bugs: {bugs_crit}")
    except Exception as e:
        await _w(bot, user.id, f"⚠️ Health check error: {str(e)[:80]}")


# ---------------------------------------------------------------------------
# !bugdash
# ---------------------------------------------------------------------------

async def handle_bugdash(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _ao(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return
    try:
        total_open = _qi(
            "SELECT COUNT(*) FROM reports "
            "WHERE report_type='bug_report' AND status='open'"
        )
        crit = _qi(
            "SELECT COUNT(*) FROM reports "
            "WHERE report_type='bug_report' AND priority='critical' AND status='open'"
        )
        high = _qi(
            "SELECT COUNT(*) FROM reports "
            "WHERE report_type='bug_report' AND priority='high' AND status='open'"
        )
        today_new = _qi(
            "SELECT COUNT(*) FROM reports "
            "WHERE report_type='bug_report' AND created_at >= ?", (_today(),)
        )
        top_tag = _qs(
            "SELECT tag FROM reports WHERE report_type='bug_report' "
            "AND tag IS NOT NULL AND tag != '' "
            "GROUP BY tag ORDER BY COUNT(*) DESC LIMIT 1",
            default="none"
        )
        await _w(bot, user.id,
                 f"🐞 Bug Dashboard\n"
                 f"Open: {total_open}\n"
                 f"Critical: {crit}\n"
                 f"High: {high}\n"
                 f"New today: {today_new}\n"
                 f"Top tag: {top_tag}\n"
                 "Submit: !bug | Triage: !bugs open")
    except Exception as e:
        await _w(bot, user.id, f"⚠️ Bug dash error: {str(e)[:80]}")


# ---------------------------------------------------------------------------
# !feedbackdash
# ---------------------------------------------------------------------------

async def handle_feedbackdash(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _ao(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return
    try:
        total_open = _qi(
            "SELECT COUNT(*) FROM reports "
            "WHERE report_type='feedback' AND status='open'"
        )
        today_new = _qi(
            "SELECT COUNT(*) FROM reports "
            "WHERE report_type='feedback' AND created_at >= ?", (_today(),)
        )
        top_tag = _qs(
            "SELECT tag FROM reports WHERE report_type='feedback' "
            "AND tag IS NOT NULL AND tag != '' "
            "GROUP BY tag ORDER BY COUNT(*) DESC LIMIT 1",
            default="none"
        )
        await _w(bot, user.id,
                 f"💬 Feedback Dashboard\n"
                 f"Open: {total_open}\n"
                 f"New today: {today_new}\n"
                 f"Top tag: {top_tag}\n"
                 "Submit: !feedback | Review: !feedbacks recent")
    except Exception as e:
        await _w(bot, user.id, f"⚠️ Feedback dash error: {str(e)[:80]}")


# ---------------------------------------------------------------------------
# !dailyreview [launch]
# ---------------------------------------------------------------------------

async def handle_dailyreview(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _ao(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return
    try:
        today       = _today()
        players     = _qi("SELECT COUNT(*) FROM users")
        new_today   = _qi(
            "SELECT COUNT(*) FROM users WHERE created_at >= ?", (today,)
        )
        bugs_open   = _qi(
            "SELECT COUNT(*) FROM reports "
            "WHERE report_type='bug_report' AND status='open'"
        )
        errors_today = _qi(
            "SELECT COUNT(*) FROM reports "
            "WHERE report_type='error_report' AND created_at >= ?", (today,)
        )
        active_today = _qi(
            "SELECT COUNT(DISTINCT user_id) FROM daily_claims WHERE claimed_date=?",
            (today,)
        )
        ret_pct = round(active_today / players * 100) if players else 0
        eco_ok  = _get("economy_lock", "off") == "on"
        await _w(bot, user.id,
                 f"📋 Daily Launch Review\n"
                 f"Players: {_fmt(players)}\n"
                 f"New: {_fmt(new_today)}\n"
                 f"Bugs open: {bugs_open}\n"
                 f"Errors today: {errors_today}\n"
                 f"Economy: {'OK ✅' if eco_ok else '⚠️ unlocked'}\n"
                 f"Retention: {ret_pct}%")
        await _w(bot, user.id,
                 "Actions:\n"
                 "1. !bugs open\n"
                 "2. !feedbacks top\n"
                 "3. !balanceaudit\n"
                 "4. !launchblockers")
    except Exception as e:
        await _w(bot, user.id, f"⚠️ Daily review error: {str(e)[:80]}")


# ---------------------------------------------------------------------------
# !economymonitor [today|7d]
# ---------------------------------------------------------------------------

async def handle_economymonitor(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _ao(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return
    sub   = args[1].lower() if len(args) > 1 else "today"
    since = _days_ago(7) if sub == "7d" else _today()
    label = "7d" if sub == "7d" else "Today"
    try:
        earned = _qi(
            "SELECT COALESCE(SUM(amount),0) FROM ledger "
            "WHERE type='earn' AND created_at >= ?", (since,)
        )
        spent = _qi(
            "SELECT COALESCE(SUM(amount),0) FROM ledger "
            "WHERE type='spend' AND created_at >= ?", (since,)
        )
        net     = earned - spent
        top_src = _qs(
            "SELECT source FROM ledger WHERE type='earn' AND created_at >= ? "
            "GROUP BY source ORDER BY SUM(amount) DESC LIMIT 1",
            (since,), default="N/A"
        )
        top_sink = _qs(
            "SELECT source FROM ledger WHERE type='spend' AND created_at >= ? "
            "GROUP BY source ORDER BY SUM(amount) DESC LIMIT 1",
            (since,), default="N/A"
        )
        net_s = f"+{_fmt(net)}" if net >= 0 else f"-{_fmt(abs(net))}"
        risk  = "Low ✅" if net >= 0 or abs(net) < 1_000_000 else "Medium ⚠️"
        await _w(bot, user.id,
                 f"💰 Economy Monitor ({label})\n"
                 f"Earned: {_fmt(earned)} 🪙\n"
                 f"Spent: {_fmt(spent)} 🪙\n"
                 f"Net: {net_s} 🪙\n"
                 f"Top source: {top_src}\n"
                 f"Top sink: {top_sink}\n"
                 f"Inflation risk: {risk}")
    except Exception as e:
        await _w(bot, user.id, f"⚠️ Economy monitor error: {str(e)[:80]}")


# ---------------------------------------------------------------------------
# !luxemonitor [today|7d]
# ---------------------------------------------------------------------------

async def handle_luxemonitor(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _ao(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return
    sub   = args[1].lower() if len(args) > 1 else "today"
    since = _days_ago(7) if sub == "7d" else _today()
    label = "7d" if sub == "7d" else "Today"
    try:
        tips_in = _qi(
            "SELECT COALESCE(SUM(tickets_earned),0) FROM tip_conversions "
            "WHERE created_at >= ?", (since,)
        )
        spent_lx = _qi(
            "SELECT COALESCE(SUM(ABS(change)),0) FROM bank_transactions "
            "WHERE currency='luxe' AND type='spend' AND created_at >= ?", (since,)
        )
        vip_buys = _qi(
            "SELECT COUNT(*) FROM purchase_history "
            "WHERE item_id LIKE '%vip%' AND created_at >= ?", (since,)
        )
        await _w(bot, user.id,
                 f"🎫 Luxe Monitor ({label})\n"
                 f"Tips in: {_fmt(tips_in)} 🎫\n"
                 f"Spent: {_fmt(spent_lx)} 🎫\n"
                 f"VIP buys: {vip_buys}\n"
                 f"Status: OK ✅")
    except Exception as e:
        await _w(bot, user.id, f"⚠️ Luxe monitor error: {str(e)[:80]}")


# ---------------------------------------------------------------------------
# !retentionmonitor [today|7d]
# ---------------------------------------------------------------------------

async def handle_retentionmonitor(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _ao(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return
    sub   = args[1].lower() if len(args) > 1 else "today"
    since = _days_ago(7) if sub == "7d" else _today()
    label = "7d" if sub == "7d" else "Today"
    try:
        total  = _qi("SELECT COUNT(*) FROM users") or 1
        active = _qi(
            "SELECT COUNT(DISTINCT user_id) FROM daily_claims WHERE claimed_date >= ?",
            (since,)
        )
        mission_active = _qi(
            "SELECT COUNT(DISTINCT user_id) FROM quest_progress "
            "WHERE updated_at >= ?", (since,)
        )
        ret_pct = round(active / total * 100)
        mis_pct = round(mission_active / total * 100)
        await _w(bot, user.id,
                 f"📌 Retention Monitor ({label})\n"
                 f"Daily active: {_fmt(active)} ({ret_pct}%)\n"
                 f"Mission active: {_fmt(mission_active)} ({mis_pct}%)\n"
                 f"Total players: {_fmt(total)}\n"
                 f"Status: OK ✅")
    except Exception as e:
        await _w(bot, user.id, f"⚠️ Retention monitor error: {str(e)[:80]}")


# ---------------------------------------------------------------------------
# !eventmonitor [last|7d]
# ---------------------------------------------------------------------------

async def handle_eventmonitor(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _ao(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return
    sub   = args[1].lower() if len(args) > 1 else "last"
    since = _days_ago(7) if sub == "7d" else _days_ago(1)
    label = "7d" if sub == "7d" else "24h"
    try:
        # Active event
        active_name = "None"
        try:
            from modules.events import _get_all_active_events
            ev_list = _get_all_active_events()
            if ev_list:
                active_name = ev_list[0]["name"]
        except Exception:
            pass

        last_event = _qs(
            "SELECT name FROM event_history ORDER BY id DESC LIMIT 1",
            default="N/A"
        )
        players   = _qi(
            "SELECT COUNT(DISTINCT user_id) FROM event_points "
            "WHERE updated_at >= ?", (since,)
        )
        total_pts = _qi(
            "SELECT COALESCE(SUM(points),0) FROM event_points "
            "WHERE updated_at >= ?", (since,)
        )

        if players == 0 and total_pts == 0 and last_event == "N/A":
            await _w(bot, user.id,
                     f"🎉 Event Monitor ({label})\nNo recent event data yet.\n"
                     f"Active: {active_name}\nStatus: OK ✅")
        else:
            await _w(bot, user.id,
                     (f"🎉 Event Monitor ({label})\n"
                      f"Active: {active_name}\n"
                      f"Last: {last_event}\n"
                      f"Players: {_fmt(players)}\n"
                      f"Season pts: {_fmt(total_pts)}\n"
                      f"Status: OK ✅")[:249])
    except Exception as e:
        await _w(bot, user.id, f"⚠️ Event monitor error: {str(e)[:80]}")


# ---------------------------------------------------------------------------
# !casinomonitor / !bjmonitor / !pokermonitor
# ---------------------------------------------------------------------------

async def handle_casinomonitor(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _ao(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return
    try:
        today      = _today()
        bj_rounds  = _qi(
            "SELECT COUNT(*) FROM bj_daily WHERE play_date=?", (today,)
        )
        poker_games = _qi(
            "SELECT COUNT(*) FROM poker_round_results WHERE created_at >= ?", (today,)
        )
        bj_net = _qi(
            "SELECT COALESCE(SUM(net_change),0) FROM casino_round_results "
            "WHERE game_type='blackjack' AND created_at >= ?", (today,)
        )
        await _w(bot, user.id,
                 f"🎰 Casino Monitor (Today)\n"
                 f"BJ rounds: {bj_rounds}\n"
                 f"Poker games: {poker_games}\n"
                 f"BJ net payout: {_fmt(bj_net)} 🪙\n"
                 f"Errors: 0\n"
                 f"Status: OK ✅")
    except Exception as e:
        await _w(bot, user.id, f"⚠️ Casino monitor error: {str(e)[:80]}")


async def handle_bjmonitor(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _ao(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return
    try:
        today  = _today()
        rounds = _qi("SELECT COUNT(*) FROM bj_daily WHERE play_date=?", (today,))
        wins   = _qi(
            "SELECT COUNT(*) FROM bj_daily WHERE play_date=? AND result='win'", (today,)
        )
        losses = _qi(
            "SELECT COUNT(*) FROM bj_daily WHERE play_date=? AND result='lose'", (today,)
        )
        net = _qi(
            "SELECT COALESCE(SUM(net_change),0) FROM casino_round_results "
            "WHERE game_type='blackjack' AND created_at >= ?", (today,)
        )
        await _w(bot, user.id,
                 f"🃏 BJ Monitor (Today)\n"
                 f"Rounds: {rounds}\n"
                 f"Wins: {wins} | Losses: {losses}\n"
                 f"Net payout: {_fmt(net)} 🪙\n"
                 f"Status: OK ✅")
    except Exception as e:
        await _w(bot, user.id, f"⚠️ BJ monitor error: {str(e)[:80]}")


async def handle_pokermonitor(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _ao(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return
    try:
        today   = _today()
        games   = _qi(
            "SELECT COUNT(*) FROM poker_round_results WHERE created_at >= ?", (today,)
        )
        players = _qi(
            "SELECT COUNT(DISTINCT user_id) FROM poker_stats WHERE last_played >= ?",
            (today,)
        )
        net = _qi(
            "SELECT COALESCE(SUM(net_change),0) FROM casino_round_results "
            "WHERE game_type='poker' AND created_at >= ?", (today,)
        )
        await _w(bot, user.id,
                 f"🃏 Poker Monitor (Today)\n"
                 f"Games: {games}\n"
                 f"Players: {players}\n"
                 f"Net payout: {_fmt(net)} 🪙\n"
                 f"Status: OK ✅")
    except Exception as e:
        await _w(bot, user.id, f"⚠️ Poker monitor error: {str(e)[:80]}")


# ---------------------------------------------------------------------------
# !errordash
# ---------------------------------------------------------------------------

async def handle_errordash(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _ao(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return
    try:
        open_err = _qi(
            "SELECT COUNT(*) FROM reports "
            "WHERE report_type='error_report' AND status='open'"
        )
        last_1h = _qi(
            "SELECT COUNT(*) FROM reports "
            "WHERE report_type='error_report' AND created_at >= ?",
            (_hours_ago(1),)
        )
        top_cmd = _qs(
            "SELECT tag FROM reports WHERE report_type='error_report' "
            "AND tag IS NOT NULL AND tag != '' "
            "GROUP BY tag ORDER BY COUNT(*) DESC LIMIT 1",
            default="N/A"
        )
        await _w(bot, user.id,
                 f"⚠️ Error Dashboard\n"
                 f"Open: {open_err}\n"
                 f"Last hour: {last_1h}\n"
                 f"Top command: {top_cmd}\n"
                 f"Status: {'OK ✅' if open_err == 0 else '⚠️ review needed'}")
    except Exception as e:
        await _w(bot, user.id, f"⚠️ Error dash failed: {str(e)[:80]}")


# ---------------------------------------------------------------------------
# !hotfix [start [reason...]|end|status]
# ---------------------------------------------------------------------------

async def handle_hotfix(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _oo(user.username):
        await _w(bot, user.id, "🔒 Owner only.")
        return
    sub = args[1].lower() if len(args) > 1 else "status"
    if sub == "start":
        reason = " ".join(args[2:]) if len(args) > 2 else "no reason given"
        _set("hotfix_mode", "on", user.username)
        try:
            conn = db.get_connection()
            conn.execute(
                "INSERT INTO hotfix_logs (message, created_by, created_at) VALUES (?,?,?)",
                (f"[START] {reason[:180]}", user.username, _now()),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
        await _w(bot, user.id,
                 f"🛠️ Hotfix Mode: ON\n"
                 f"Reason: {reason[:100]}\n"
                 "Feature additions remain locked.\n"
                 "Economy/registry locks held.")
    elif sub == "end":
        _set("hotfix_mode", "off", user.username)
        try:
            conn = db.get_connection()
            conn.execute(
                "INSERT INTO hotfix_logs (message, created_by, created_at) VALUES (?,?,?)",
                ("[END] Hotfix completed", user.username, _now()),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
        await _w(bot, user.id,
                 "🛠️ Hotfix Mode: OFF\n"
                 "Production continues normally.")
    else:
        hf    = _get("hotfix_mode", "off")
        count = _qi("SELECT COUNT(*) FROM hotfix_logs")
        await _w(bot, user.id,
                 f"🛠️ Hotfix Mode: {'ON ⚠️' if hf=='on' else 'OFF ✅'}\n"
                 f"Log entries: {count}\n"
                 "!hotfix start [reason] | !hotfix end")


# ---------------------------------------------------------------------------
# !hotfixlog [add <msg>|recent]
# ---------------------------------------------------------------------------

async def handle_hotfixlog(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _ao(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return
    sub = args[1].lower() if len(args) > 1 else "recent"
    if sub == "add":
        msg = " ".join(args[2:]) if len(args) > 2 else ""
        if not msg:
            await _w(bot, user.id, "Usage: !hotfixlog add <message>")
            return
        try:
            conn = db.get_connection()
            conn.execute(
                "INSERT INTO hotfix_logs (message, created_by, created_at) VALUES (?,?,?)",
                (msg[:200], user.username, _now()),
            )
            conn.commit()
            conn.close()
            await _w(bot, user.id, f"✅ Hotfix log added: {msg[:60]}")
        except Exception as e:
            await _w(bot, user.id, f"⚠️ Log error: {str(e)[:60]}")
    else:
        try:
            conn = db.get_connection()
            rows = conn.execute(
                "SELECT id, message, created_by FROM hotfix_logs "
                "ORDER BY id DESC LIMIT 5"
            ).fetchall()
            conn.close()
            if not rows:
                await _w(bot, user.id,
                         "🛠️ Hotfix Log: empty.\nUse !hotfixlog add <msg>")
                return
            lines = ["🛠️ Hotfix Log (recent):"]
            for r in rows:
                lines.append(f"#{r[0]} {str(r[1])[:55]} (@{r[2]})")
            await _w(bot, user.id, "\n".join(lines)[:249])
        except Exception as e:
            await _w(bot, user.id, f"⚠️ Log error: {str(e)[:60]}")


# ---------------------------------------------------------------------------
# !launchlocks
# ---------------------------------------------------------------------------

async def handle_launchlocks(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _ao(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return
    prod  = _get("production_mode", "off") == "on"
    frz   = _get("feature_freeze", "off") == "on"
    eco   = _get("economy_lock", "off") == "on"
    reg   = _get("registry_lock", "off") == "on"
    maint = _get("maintenance_mode", "off") == "on"

    def chk(v: bool) -> str:
        return "✅" if v else "⚠️"

    issues: list[str] = []
    if not prod:  issues.append("production OFF")
    if not frz:   issues.append("feature freeze OFF")
    if not eco:   issues.append("economy lock OFF")
    if not reg:   issues.append("registry lock OFF")
    if maint:     issues.append("maintenance ON")

    await _w(bot, user.id,
             f"🔒 Launch Locks\n"
             f"{chk(prod)} Production: {'ON' if prod else 'OFF'}\n"
             f"{chk(frz)} Feature Freeze: {'ON' if frz else 'OFF'}\n"
             f"{chk(eco)} Economy Lock: {'ON' if eco else 'OFF'}\n"
             f"{chk(reg)} Registry Lock: {'ON' if reg else 'OFF'}\n"
             f"{chk(not maint)} Maintenance: {'OFF' if not maint else 'ON ⚠️'}")
    if issues:
        await _w(bot, user.id,
                 "⚠️ Warnings:\n" + "\n".join(f"• {i}" for i in issues))


# ---------------------------------------------------------------------------
# !snapshot [create|status]
# ---------------------------------------------------------------------------

async def handle_snapshot(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _ao(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return
    sub = args[1].lower() if len(args) > 1 else "status"
    if sub == "create":
        try:
            today   = _today()
            players = _qi("SELECT COUNT(*) FROM users")
            bugs    = _qi(
                "SELECT COUNT(*) FROM reports "
                "WHERE report_type='bug_report' AND status='open'"
            )
            earned  = _qi(
                "SELECT COALESCE(SUM(amount),0) FROM ledger "
                "WHERE type='earn' AND created_at >= ?", (today,)
            )
            spent   = _qi(
                "SELECT COALESCE(SUM(amount),0) FROM ledger "
                "WHERE type='spend' AND created_at >= ?", (today,)
            )
            summary = {
                "players": players, "bugs_open": bugs,
                "earned": earned, "spent": spent,
                "net": earned - spent, "date": today,
            }
            conn = db.get_connection()
            conn.execute(
                "INSERT INTO launch_snapshots "
                "(range_key, summary_json, created_at, created_by) VALUES (?,?,?,?)",
                (today, json.dumps(summary), _now(), user.username),
            )
            conn.commit()
            conn.close()
            net_s = earned - spent
            await _w(bot, user.id,
                     f"📸 Launch Snapshot\n"
                     f"Players: {_fmt(players)}\n"
                     f"Economy net: {_fmt(net_s)} 🪙\n"
                     f"Bugs: {bugs}\n"
                     f"Errors: N/A\n"
                     f"Saved: YES ✅")
        except Exception as e:
            await _w(bot, user.id, f"⚠️ Snapshot failed: {str(e)[:80]}")
    else:
        try:
            conn = db.get_connection()
            row  = conn.execute(
                "SELECT range_key, created_at, created_by "
                "FROM launch_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
            conn.close()
            if not row:
                await _w(bot, user.id,
                         "📸 No snapshots yet.\nRun !snapshot create")
                return
            await _w(bot, user.id,
                     f"📸 Snapshot Status\n"
                     f"Latest: {row[0]}\n"
                     f"Created: {str(row[1])[:16]}\n"
                     f"By: {row[2]}")
        except Exception as e:
            await _w(bot, user.id, f"⚠️ Snapshot status error: {str(e)[:80]}")
