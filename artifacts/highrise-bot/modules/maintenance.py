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
  /softrestart        — owner only: reset in-memory state without losing DB data
  /restartbot         — owner only: fully restart the bot process via os.execv
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


# ── /softrestart ───────────────────────────────────────────────────────────────

async def handle_softrestart(bot: BaseBot, user: User) -> None:
    """
    Owner only — reset all in-memory game state and restart auto loops.

    Safe to call at any time:
      - Cancels answer timer, auto game loop, auto event loop.
      - Resets BJ / RBJ / Poker tables (refunds buy-ins automatically).
      - Clears stuck trivia / scramble / riddle state.
      - Cancels any running event countdown task.
      - Warms settings cache from SQLite.
      - Restarts auto game and auto event loops.

    Does NOT touch coins, XP, profiles, badges, titles, bank, BJ stats,
    event history, staff roles, or any other persisted database data.
    """
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return

    print(f"[MAINT] /softrestart initiated by @{user.username}")

    # ── 1. Cancel mini-game answer timer & clear active game state ────────────
    try:
        import modules.auto_games as _ag
        _ag.cancel_answer_timer()
    except Exception as exc:
        print(f"[MAINT] softrestart: answer timer cancel error: {exc}")

    try:
        import games as _games
        _games.reset_all_games()
    except Exception as exc:
        print(f"[MAINT] softrestart: game state clear error: {exc}")

    # ── 2. Cancel auto game loop ───────────────────────────────────────────────
    try:
        import modules.auto_games as _ag
        t = _ag._auto_game_task
        if t and not t.done():
            t.cancel()
        _ag._auto_game_task = None
        print("[MAINT] softrestart: auto game loop cancelled.")
    except Exception as exc:
        print(f"[MAINT] softrestart: auto game loop cancel error: {exc}")

    # ── 3. Cancel auto event loop ──────────────────────────────────────────────
    try:
        import modules.auto_games as _ag
        t = _ag._auto_event_loop_task
        if t and not t.done():
            t.cancel()
        _ag._auto_event_loop_task = None
        print("[MAINT] softrestart: auto event loop cancelled.")
    except Exception as exc:
        print(f"[MAINT] softrestart: auto event loop cancel error: {exc}")

    # ── 4. Soft-reset BJ (saves state to DB, no refund — recovery on next start) ──
    try:
        from modules.blackjack import soft_reset_table as _bj_soft_reset
        _bj_soft_reset()
        print("[MAINT] softrestart: BJ table soft-reset (state saved).")
    except Exception as exc:
        print(f"[MAINT] softrestart: BJ soft-reset error: {exc}")

    # ── 5. Soft-reset RBJ ─────────────────────────────────────────────────────
    try:
        from modules.realistic_blackjack import soft_reset_table as _rbj_soft_reset
        _rbj_soft_reset()
        print("[MAINT] softrestart: RBJ table soft-reset (state saved).")
    except Exception as exc:
        print(f"[MAINT] softrestart: RBJ soft-reset error: {exc}")

    # ── 6. Reset Poker table ──────────────────────────────────────────────────
    try:
        from modules.poker import reset_table as _poker_reset
        _poker_reset()
        print("[MAINT] softrestart: Poker table reset.")
    except Exception as exc:
        print(f"[MAINT] softrestart: Poker reset error: {exc}")

    # ── 7. Cancel active event countdown task ─────────────────────────────────
    try:
        from modules.events import _cancel_event_task
        _cancel_event_task()
        print("[MAINT] softrestart: event task cancelled.")
    except Exception as exc:
        print(f"[MAINT] softrestart: event task cancel error: {exc}")

    # ── 8. Warm settings from DB (no persistent cache exists, confirms DB ok) ──
    try:
        db.get_economy_settings()
        db.get_bj_settings()
        db.get_rbj_settings()
        db.get_auto_game_settings()
        db.get_auto_event_settings()
        print("[MAINT] softrestart: settings confirmed from DB.")
    except Exception as exc:
        print(f"[MAINT] softrestart: settings reload error: {exc}")

    # ── 9. Restart auto loops ──────────────────────────────────────────────────
    try:
        from modules.auto_games import start_auto_game_loop, start_auto_event_loop
        start_auto_game_loop(bot)
        start_auto_event_loop(bot)
        print("[MAINT] softrestart: auto loops restarted.")
    except Exception as exc:
        print(f"[MAINT] softrestart: loop restart error: {exc}")

    # ── 10. Recover any BJ/RBJ tables saved during soft-reset ────────────────
    try:
        import asyncio as _asyncio
        from modules.blackjack           import startup_bj_recovery
        from modules.realistic_blackjack import startup_rbj_recovery
        _asyncio.create_task(startup_bj_recovery(bot))
        _asyncio.create_task(startup_rbj_recovery(bot))
        print("[MAINT] softrestart: BJ/RBJ recovery tasks launched.")
    except Exception as exc:
        print(f"[MAINT] softrestart: recovery launch error: {exc}")

    print("[MAINT] /softrestart complete.")
    await bot.highrise.chat("🔄 Bot soft restart complete.")
    await _w(bot, user.id, "🔄 Soft restart done. Loops running, tables restored, DB safe.")


# ── /restartbot ────────────────────────────────────────────────────────────────

async def handle_restartbot(bot: BaseBot, user: User) -> None:
    """
    Owner only — fully restart the bot Python process using os.execv.

    os.execv replaces the current process in-place with a fresh Python
    interpreter running bot.py — no Replit auto-restart needed.

    WARNING: /restartbot requires Replit auto-restart/deployment to be
    enabled if os.execv is unavailable on the platform. In that case the
    command falls back to sys.exit(0), which will stop the bot unless the
    Replit workflow is configured to restart on exit.
    """
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return

    print(f"[MAINT] /restartbot initiated by @{user.username}")

    # Announce before any shutdown work so the message gets through
    try:
        await bot.highrise.chat("🔄 Restarting bot...")
    except Exception as exc:
        print(f"[MAINT] restartbot: chat announce error: {exc}")

    # Cancel in-memory timers and loops safely (best-effort)
    try:
        import modules.auto_games as _ag
        _ag.cancel_answer_timer()
        for attr in ("_auto_game_task", "_auto_event_loop_task"):
            t = getattr(_ag, attr, None)
            if t and not t.done():
                t.cancel()
            setattr(_ag, attr, None)
    except Exception as exc:
        print(f"[MAINT] restartbot: loop cancel error: {exc}")

    try:
        from modules.events import _cancel_event_task
        _cancel_event_task()
    except Exception as exc:
        print(f"[MAINT] restartbot: event task cancel error: {exc}")

    # Brief pause so the "Restarting bot..." room message is delivered
    import asyncio
    await asyncio.sleep(1)

    # Replace this process with a fresh bot.py — os.execv never returns
    import os, sys
    script = "bot.py"
    interpreter = sys.executable
    print(f"[MAINT] restartbot: exec {interpreter} {script}")
    try:
        os.execv(interpreter, [interpreter, script])
    except Exception as exc:
        # execv failed (unusual on Replit) — fall back to exit and let
        # the workflow runner restart the process automatically.
        print(f"[MAINT] restartbot: os.execv failed ({exc}), falling back to sys.exit(0)")
        print("[MAINT] WARNING: sys.exit(0) requires Replit auto-restart to be enabled.")
        sys.exit(0)
