"""
modules/maintenance.py
Owner/admin maintenance tools for the Highrise Mini Game Bot.

Commands:
  /botstatus          — public: bot health snapshot
  /dbstats            — owner/admin: database row counts
  /backup             — owner only: timestamped DB backup
  /maintenance        — public: show maintenance state
  /maintenance on/off — owner/admin: toggle maintenance mode
  /reloadsettings     — owner/admin: confirm settings loaded from DB
  /cleanup            — owner/admin: purge expired mutes & stale data
"""
from __future__ import annotations
import time
import shutil
from datetime import datetime, timezone

from highrise import BaseBot, User
import database as db
from modules.permissions import is_owner, is_admin

# ── Module state ──────────────────────────────────────────────────────────────

_START_TIME: float = time.monotonic()
_maintenance_on: bool = False


def is_maintenance() -> bool:
    """Returns True while maintenance mode is enabled."""
    return _maintenance_on


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


async def _chat(bot: BaseBot, msg: str) -> None:
    try:
        await bot.highrise.chat(msg[:249])
    except Exception:
        pass


def _uptime() -> str:
    secs = int(time.monotonic() - _START_TIME)
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s" if h else f"{m}m {s}s"


def _is_admin_or_owner(username: str) -> bool:
    return is_owner(username) or is_admin(username)


# ── /botstatus ────────────────────────────────────────────────────────────────

async def handle_botstatus(bot: BaseBot, user: User) -> None:
    """Public — snapshot of bot health and active table states."""
    try:
        from modules.blackjack           import _state as _bj
        from modules.realistic_blackjack import _state as _rbj
        from modules.poker               import _T     as _pk
        bj_phase  = getattr(_bj,  "phase", "?")
        rbj_phase = getattr(_rbj, "phase", "?")
        pok_state = getattr(_pk,  "state", "?")
    except Exception:
        bj_phase = rbj_phase = pok_state = "?"

    bj_s   = db.get_bj_settings()
    rbj_s  = db.get_rbj_settings()
    bj_on  = "ON"  if int(bj_s.get("bj_enabled",  1)) else "OFF"
    rbj_on = "ON"  if int(rbj_s.get("rbj_enabled", 1)) else "OFF"
    event  = "ON"  if db.is_event_active()             else "OFF"
    maint  = "ON"  if _maintenance_on                  else "OFF"

    await _w(bot, user.id,
             f"🤖 Bot: Online | Up: {_uptime()}\n"
             f"Maintenance: {maint} | Event: {event}\n"
             f"BJ:{bj_on}[{bj_phase}] RBJ:{rbj_on}[{rbj_phase}]\n"
             f"Poker: {pok_state}")


# ── /dbstats ──────────────────────────────────────────────────────────────────

async def handle_dbstats(bot: BaseBot, user: User) -> None:
    """Owner/admin — database row-count summary."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Owner/admin only.")
        return
    try:
        s = db.get_db_stats()
        await _w(bot, user.id,
                 f"📊 DB Stats:\n"
                 f"Users:{s['users']}  Coins:{s['total_coins']:,}\n"
                 f"Transactions:{s['transactions']}  Open reports:{s['open_reports']}\n"
                 f"Shop purchases:{s['purchases']}  BJ rounds:{s['bj_rounds']}")
    except Exception as exc:
        print(f"[MAINT] dbstats error: {exc}")
        await _w(bot, user.id, "Error fetching DB stats.")


# ── /backup ───────────────────────────────────────────────────────────────────

async def handle_backup(bot: BaseBot, user: User) -> None:
    """Owner only — copy the live SQLite DB to a timestamped file."""
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    try:
        ts  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        src = "highrise_hangout.db"
        dst = f"backup_highrise_hangout_{ts}.db"
        shutil.copy2(src, dst)
        print(f"[BACKUP] Created: {dst}")
        await _w(bot, user.id, "✅ Database backup created.")
    except Exception as exc:
        print(f"[BACKUP] Failed: {exc}")
        await _w(bot, user.id, "❌ Backup failed — check bot logs.")


# ── /maintenance ──────────────────────────────────────────────────────────────

async def handle_maintenance(bot: BaseBot, user: User, args: list[str]) -> None:
    """Toggle or show maintenance mode."""
    global _maintenance_on
    sub = args[1].lower() if len(args) > 1 else ""

    if sub == "on":
        if not _is_admin_or_owner(user.username):
            await _w(bot, user.id, "Owner/admin only.")
            return
        _maintenance_on = True
        await _chat(bot, "🔧 Maintenance mode ON. Game & economy features temporarily paused.")
        await _w(bot, user.id, "✅ Maintenance mode enabled.")

    elif sub == "off":
        if not _is_admin_or_owner(user.username):
            await _w(bot, user.id, "Owner/admin only.")
            return
        _maintenance_on = False
        await _chat(bot, "✅ Maintenance complete — all features restored!")

    else:
        status = "ON 🔧" if _maintenance_on else "OFF ✅"
        await _w(bot, user.id,
                 f"Maintenance mode: {status}\n"
                 "Toggle with /maintenance on  or  /maintenance off  (admin+).")


# ── /reloadsettings ───────────────────────────────────────────────────────────

async def handle_reloadsettings(bot: BaseBot, user: User) -> None:
    """Owner/admin — confirm economy settings are current from DB."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Owner/admin only.")
        return
    try:
        settings = db.get_economy_settings()
        bj_s     = db.get_bj_settings()
        rbj_s    = db.get_rbj_settings()
        await _w(bot, user.id,
                 f"✅ Settings reloaded:\n"
                 f"Economy keys: {len(settings)}\n"
                 f"BJ keys: {len(bj_s)}  RBJ keys: {len(rbj_s)}\n"
                 "All settings read live from DB — no restart needed.")
    except Exception as exc:
        print(f"[MAINT] reloadsettings error: {exc}")
        await _w(bot, user.id, "Error reloading settings.")


# ── /cleanup ──────────────────────────────────────────────────────────────────

async def handle_cleanup(bot: BaseBot, user: User) -> None:
    """Owner/admin — remove expired mutes and stale data."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Owner/admin only.")
        return
    try:
        result = db.cleanup_expired_data()
        await _w(bot, user.id,
                 f"✅ Cleanup done:\n"
                 f"Expired mutes removed: {result['mutes']}\n"
                 f"Note: coins, XP, items, and transactions were not touched.")
    except Exception as exc:
        print(f"[MAINT] cleanup error: {exc}")
        await _w(bot, user.id, "Error during cleanup.")
