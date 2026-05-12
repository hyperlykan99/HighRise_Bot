"""
modules/daily_admin.py
Daily Admin Checklist system for the Highrise Mini Game Bot.

Commands (manager+):
  /dailyadmin              — overview summary
  /dailyadmin bank         — bank flags & pending
  /dailyadmin casino       — BJ/RBJ state & casino net
  /dailyadmin events       — current event & auto-game settings
  /dailyadmin notify       — subscriber & notification stats
  /dailyadmin reports      — open reports & active mutes
  /dailyadmin errors       — bot error log (graceful if missing)

Moderators may use !dailyadmin reports and /dailyadmin errors only.
"""
from __future__ import annotations

import database as db
from highrise import BaseBot, User
from modules.permissions import (
    is_owner, is_admin, is_manager, can_moderate,
)

# ── helpers ──────────────────────────────────────────────────────────────────

def _is_manager_plus(username: str) -> bool:
    uname = username.lower()
    return is_owner(uname) or is_admin(uname) or is_manager(uname)


async def _w(bot: BaseBot, uid: str, text: str) -> None:
    await bot.highrise.send_whisper(uid, text[:249])


def _yn(val) -> str:
    return "ON" if val else "OFF"


def _safe_query(sql: str, params: tuple = ()) -> int:
    """Run a COUNT query safely; return 0 on any error."""
    try:
        conn = db.get_connection()
        result = conn.execute(sql, params).fetchone()
        conn.close()
        return result[0] if result else 0
    except Exception:
        return 0


def _safe_query_row(sql: str, params: tuple = ()) -> dict | None:
    """Run a single-row SELECT safely; return None on any error."""
    try:
        conn = db.get_connection()
        row = conn.execute(sql, params).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def _log_usage(username: str, section: str, summary: str) -> None:
    try:
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO daily_admin_logs (username, section, summary_text) VALUES (?, ?, ?)",
            (username.lower(), section, summary[:249]),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[DAILY_ADMIN] log_usage error: {exc!r}")


# ── /dailyadmin overview ──────────────────────────────────────────────────────

async def _section_overview(bot: BaseBot, user: User) -> None:
    try:
        open_reports = _safe_query(
            "SELECT COUNT(*) FROM reports WHERE status = 'open'"
        )
        errors = _safe_query(
            "SELECT COUNT(*) FROM notification_logs "
            "WHERE status = 'failed' AND DATE(timestamp) = DATE('now')"
        )
        bank_flags = _safe_query(
            "SELECT COUNT(*) FROM bank_user_stats WHERE suspicious_transfer_count > 0"
        )
        bank_blocked = _safe_query(
            "SELECT COUNT(*) FROM bank_user_stats WHERE bank_blocked = 1"
        )
        pending_dms = _safe_query(
            "SELECT COUNT(*) FROM pending_notifications WHERE delivered = 0"
        )

        bj  = db.get_bj_settings()
        rbj = db.get_rbj_settings()
        bj_state  = _yn(bj.get("bj_enabled", 1))
        rbj_state = _yn(rbj.get("rbj_enabled", 1))

        event = db.get_active_event()
        event_name = (event.get("event_id", "?") if event else "none")[:12]

        summary = (
            f"📋 Admin Today\n"
            f"Reports: {open_reports} open | Errors: {errors}\n"
            f"Bank flags: {bank_flags + bank_blocked} | Pending DMs: {pending_dms}\n"
            f"BJ/RBJ: {bj_state}/{rbj_state} | Event: {event_name}"
        )
        await _w(bot, user.id, summary)
        _log_usage(user.username, "overview", summary)
    except Exception as exc:
        await _w(bot, user.id, f"Overview error: {exc!r}"[:249])


# ── /dailyadmin bank ──────────────────────────────────────────────────────────

async def _section_bank(bot: BaseBot, user: User) -> None:
    try:
        suspicious = _safe_query(
            "SELECT COUNT(*) FROM bank_user_stats WHERE suspicious_transfer_count > 0"
        )
        blocked = _safe_query(
            "SELECT COUNT(*) FROM bank_user_stats WHERE bank_blocked = 1"
        )
        pending_bank = _safe_query(
            "SELECT COUNT(*) FROM bank_notifications WHERE delivered = 0"
        )
        pending_typed = _safe_query(
            "SELECT COUNT(*) FROM pending_notifications "
            "WHERE delivered = 0 AND notification_type = 'bank'"
        )

        top_sender = _safe_query_row(
            "SELECT sender_username, SUM(amount_sent) as total "
            "FROM bank_transactions "
            "WHERE DATE(timestamp) = DATE('now') "
            "GROUP BY sender_username ORDER BY total DESC LIMIT 1"
        )
        top_receiver = _safe_query_row(
            "SELECT receiver_username, SUM(amount_received) as total "
            "FROM bank_transactions "
            "WHERE DATE(timestamp) = DATE('now') "
            "GROUP BY receiver_username ORDER BY total DESC LIMIT 1"
        )

        ts = f"{top_sender['sender_username']}({top_sender['total']})" if top_sender else "none"
        tr = f"{top_receiver['receiver_username']}({top_receiver['total']})" if top_receiver else "none"

        summary = (
            f"🏦 Bank Today\n"
            f"Suspicious: {suspicious} | Blocked: {blocked}\n"
            f"Pending notifs: {pending_bank + pending_typed}\n"
            f"Top sender: {ts}\n"
            f"Top receiver: {tr}"
        )
        await _w(bot, user.id, summary[:249])
        _log_usage(user.username, "bank", summary)
    except Exception as exc:
        await _w(bot, user.id, f"Bank error: {exc!r}"[:249])


# ── /dailyadmin casino ────────────────────────────────────────────────────────

async def _section_casino(bot: BaseBot, user: User) -> None:
    try:
        bj  = db.get_bj_settings()
        rbj = db.get_rbj_settings()

        bj_state  = _yn(bj.get("bj_enabled", 1))
        rbj_state = _yn(rbj.get("rbj_enabled", 1))

        # Recovery-required tables — stored in bj_settings as a flag
        bj_recovery  = _safe_query(
            "SELECT recovery_required FROM bj_settings WHERE id = 1"
        )
        rbj_recovery = _safe_query(
            "SELECT recovery_required FROM rbj_settings WHERE id = 1"
        )

        # Daily casino net from profit-tracking table if available
        casino_net = _safe_query_row(
            "SELECT SUM(net_profit) as net FROM casino_daily_stats "
            "WHERE DATE(date) = DATE('now')"
        )
        net_val = casino_net["net"] if casino_net and casino_net["net"] is not None else "N/A"

        win_limit_bj  = _yn(bj.get("bj_win_limit_enabled", 1))
        loss_limit_bj = _yn(bj.get("bj_loss_limit_enabled", 1))

        summary = (
            f"🎰 Casino Today\n"
            f"BJ: {bj_state} | RBJ: {rbj_state}\n"
            f"BJ recovery: {'YES' if bj_recovery else 'NO'} | "
            f"RBJ recovery: {'YES' if rbj_recovery else 'NO'}\n"
            f"Net today: {net_val}\n"
            f"BJ limits W/L: {win_limit_bj}/{loss_limit_bj}"
        )
        await _w(bot, user.id, summary[:249])
        _log_usage(user.username, "casino", summary)
    except Exception as exc:
        await _w(bot, user.id, f"Casino error: {exc!r}"[:249])


# ── /dailyadmin events ────────────────────────────────────────────────────────

async def _section_events(bot: BaseBot, user: User) -> None:
    try:
        event = db.get_active_event()
        if event:
            ev_name    = event.get("event_id", "?")
            ev_expires = (event.get("expires_at") or "?")[:16]
            ev_line    = f"{ev_name} (ends {ev_expires})"
        else:
            ev_line = "none"

        ag = db.get_auto_game_settings()
        ae = db.get_auto_event_settings()

        games_on  = _yn(ag.get("auto_games_enabled", 1))
        events_on = _yn(ae.get("auto_events_enabled", 1))

        game_interval  = ag.get("auto_game_interval", 0)
        event_interval = ae.get("auto_event_interval", 0)

        summary = (
            f"📅 Events Today\n"
            f"Event: {ev_line}\n"
            f"Auto games: {games_on} (every {game_interval}min)\n"
            f"Auto events: {events_on} (every {event_interval}min)"
        )
        await _w(bot, user.id, summary[:249])
        _log_usage(user.username, "events", summary)
    except Exception as exc:
        await _w(bot, user.id, f"Events error: {exc!r}"[:249])


# ── /dailyadmin notify ────────────────────────────────────────────────────────

async def _section_notify(bot: BaseBot, user: User) -> None:
    try:
        stats = db.get_notify_stats()

        failed_today = _safe_query(
            "SELECT COUNT(*) FROM notification_logs "
            "WHERE status = 'failed' AND DATE(timestamp) = DATE('now')"
        )

        summary = (
            f"🔔 Notify Today\n"
            f"Subs: {stats['total']} | DM: {stats['dm_connected']}\n"
            f"Unsub: {stats['unsubscribed']} | Pending: {stats['pending']}\n"
            f"Failed today: {failed_today}"
        )
        await _w(bot, user.id, summary[:249])
        _log_usage(user.username, "notify", summary)
    except Exception as exc:
        await _w(bot, user.id, f"Notify error: {exc!r}"[:249])


# ── /dailyadmin reports ───────────────────────────────────────────────────────

async def _section_reports(bot: BaseBot, user: User) -> None:
    try:
        open_count = _safe_query(
            "SELECT COUNT(*) FROM reports WHERE status = 'open'"
        )
        newest = _safe_query_row(
            "SELECT id, target_username FROM reports "
            "WHERE status = 'open' ORDER BY id DESC LIMIT 1"
        )
        newest_id   = newest["id"] if newest else "none"
        newest_user = newest["target_username"] if newest else ""

        active_mutes = _safe_query(
            "SELECT COUNT(*) FROM mutes WHERE expires_at > datetime('now')"
        )
        active_warns = _safe_query(
            "SELECT COUNT(*) FROM warnings"
        )

        summary = (
            f"📝 Reports\n"
            f"Open: {open_count} | Newest ID: {newest_id}"
            + (f" (@{newest_user})" if newest_user else "") + "\n"
            f"Active mutes: {active_mutes} | Warnings on file: {active_warns}\n"
            f"Use !reports or /reportinfo <id>"
        )
        await _w(bot, user.id, summary[:249])
        _log_usage(user.username, "reports", summary)
    except Exception as exc:
        await _w(bot, user.id, f"Reports error: {exc!r}"[:249])


# ── /dailyadmin errors ────────────────────────────────────────────────────────

async def _section_errors(bot: BaseBot, user: User) -> None:
    try:
        # Check if bot_errors table exists
        conn = db.get_connection()
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bot_errors'"
        ).fetchone()
        conn.close()

        if not tbl:
            # Fall back to notification_logs failures as error proxy
            failed_today = _safe_query(
                "SELECT COUNT(*) FROM notification_logs "
                "WHERE status = 'failed' AND DATE(timestamp) = DATE('now')"
            )
            latest = _safe_query_row(
                "SELECT id, notification_type, error_message FROM notification_logs "
                "WHERE status = 'failed' ORDER BY id DESC LIMIT 1"
            )
            if latest:
                latest_id  = latest["id"]
                latest_cmd = latest["notification_type"] or "?"
                latest_err = (latest["error_message"] or "")[:40]
            else:
                latest_id = latest_cmd = latest_err = "none"

            summary = (
                f"⚠️ Errors (notify log)\n"
                f"Failed today: {failed_today}\n"
                f"Latest ID: {latest_id} | Type: {latest_cmd}\n"
                f"Msg: {latest_err}\n"
                f"Use !notifystats for details"
            )
        else:
            unresolved = _safe_query(
                "SELECT COUNT(*) FROM bot_errors WHERE resolved = 0"
            )
            latest = _safe_query_row(
                "SELECT id, command, error_message FROM bot_errors "
                "WHERE resolved = 0 ORDER BY id DESC LIMIT 1"
            )
            latest_id  = latest["id"] if latest else "none"
            latest_cmd = latest["command"] if latest else "?"
            latest_err = (latest["error_message"] if latest else "")[:40]

            summary = (
                f"⚠️ Errors\n"
                f"Unresolved: {unresolved}\n"
                f"Latest ID: {latest_id} | Cmd: {latest_cmd}\n"
                f"Msg: {latest_err}\n"
                f"Use !errors or /lasterror"
            )

        await _w(bot, user.id, summary[:249])
        _log_usage(user.username, "errors", summary)
    except Exception as exc:
        await _w(bot, user.id, f"Errors section error: {exc!r}"[:249])


# ── Main router ───────────────────────────────────────────────────────────────

_MOD_ALLOWED = {"reports", "errors"}

async def handle_dailyadmin(bot: BaseBot, user: User, args: list[str]) -> None:
    """/dailyadmin [bank|casino|events|notify|reports|errors]"""
    section = args[1].lower().strip() if len(args) > 1 else "overview"

    is_staff = _is_manager_plus(user.username)
    is_mod   = can_moderate(user.username)

    if not is_staff and not is_mod:
        await _w(bot, user.id, "Managers, admins, and owners only.")
        return

    if not is_staff and section not in _MOD_ALLOWED:
        await _w(bot, user.id, "Moderators may only use: !dailyadmin reports, /dailyadmin errors")
        return

    _SECTIONS = {
        "overview": _section_overview,
        "bank":     _section_bank,
        "casino":   _section_casino,
        "events":   _section_events,
        "notify":   _section_notify,
        "reports":  _section_reports,
        "errors":   _section_errors,
    }

    handler = _SECTIONS.get(section)
    if handler is None:
        valid = " | ".join(_SECTIONS.keys())
        await _w(bot, user.id, f"Unknown section. Options: {valid}"[:249])
        return

    await handler(bot, user)
