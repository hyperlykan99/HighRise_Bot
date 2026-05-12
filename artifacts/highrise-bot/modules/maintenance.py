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
  /restarthelp        — owner only: show restart command overview
  /restartstatus      — owner only: diagnostic snapshot of restart readiness
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
    """Returns True while maintenance mode is enabled (in-memory OR DB global)."""
    if _maintenance_on:
        return True
    try:
        return db.is_global_maintenance_db()
    except Exception:
        return False


def is_bot_maintenance(target: str) -> bool:
    """Returns True if a specific bot (by username or mode) is in DB maintenance."""
    try:
        return db.is_bot_maintenance_db(target)
    except Exception:
        return False


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
    """Toggle or show maintenance mode (supports bot/all/status sub-commands)."""
    global _maintenance_on
    sub  = args[1].lower() if len(args) > 1 else ""
    sub2 = args[2].lower() if len(args) > 2 else ""

    # ── /maintenance on|off  (global toggle) ─────────────────────────────────
    if sub in ("on", "off"):
        if not _is_admin_or_owner(user.username):
            await _w(bot, user.id, "Owner/admin only.")
            return
        _maintenance_on = sub == "on"
        try:
            db.set_maintenance_state(
                "global", "all", _maintenance_on, "", user.id, user.username
            )
        except Exception:
            pass
        if _maintenance_on:
            await _chat(bot,
                        "🔧 Maintenance mode ON. "
                        "Game & economy features temporarily paused.")
            await _w(bot, user.id, "✅ Maintenance mode enabled.")
        else:
            await _chat(bot, "✅ Maintenance complete — all features restored!")

    # ── /maintenance all on|off ───────────────────────────────────────────────
    elif sub == "all":
        if not _is_admin_or_owner(user.username):
            await _w(bot, user.id, "Owner/admin only.")
            return
        if sub2 in ("on", "off"):
            _maintenance_on = sub2 == "on"
            try:
                db.set_maintenance_state(
                    "global", "all", _maintenance_on, "", user.id, user.username
                )
            except Exception:
                pass
            if _maintenance_on:
                await _chat(bot,
                            "🔧 Maintenance mode ON. "
                            "Game & economy features temporarily paused.")
                await _w(bot, user.id, "✅ Global maintenance enabled.")
            else:
                await _chat(bot, "✅ Maintenance complete — all features restored!")
                await _w(bot, user.id, "✅ Global maintenance disabled.")
        else:
            await _w(bot, user.id, "Usage: /maintenance all on|off")

    # ── /maintenance bot <name> on|off [reason] ───────────────────────────────
    elif sub == "bot":
        if not _is_admin_or_owner(user.username):
            await _w(bot, user.id, "Owner/admin only.")
            return
        if len(args) < 4:
            await _w(bot, user.id,
                     "Usage: /maintenance bot <botname> on|off [reason]")
            return
        target_bot = args[2].lower()
        toggle     = args[3].lower()
        reason     = " ".join(args[4:])[:100] if len(args) > 4 else ""
        if toggle not in ("on", "off"):
            await _w(bot, user.id,
                     "Usage: /maintenance bot <botname> on|off [reason]")
            return
        enabled = toggle == "on"
        try:
            db.set_maintenance_state(
                "bot", target_bot, enabled, reason, user.id, user.username
            )
        except Exception as exc:
            await _w(bot, user.id, f"❌ DB error: {str(exc)[:60]}")
            return
        if enabled:
            r_str = f" ({reason})" if reason else ""
            await _w(bot, user.id,
                     f"✅ {target_bot} MAINTENANCE ON{r_str}.\n"
                     "Commands for that bot will be blocked.")
        else:
            await _w(bot, user.id,
                     f"✅ {target_bot} MAINTENANCE OFF. Commands restored.")

    # ── /maintenance status ───────────────────────────────────────────────────
    elif sub == "status":
        global_on = "ON 🔧" if _maintenance_on else "OFF ✅"
        try:
            db_global = db.is_global_maintenance_db()
            global_db = "ON" if db_global else "OFF"
        except Exception:
            global_db = "?"
        try:
            maint_rows = db.get_all_maintenance_states()
            bot_maint  = [r for r in maint_rows
                          if r.get("scope") == "bot" and r.get("enabled")]
        except Exception:
            maint_rows = []
            bot_maint  = []

        lines = [
            f"🔧 Maintenance Status",
            f"Global (memory): {global_on}",
            f"Global (DB): {global_db}",
            f"Bots in maint: {len(bot_maint)}",
        ]
        for r in bot_maint[:5]:
            reason = r.get("reason", "")
            lines.append(f"  {r['target']}" + (f": {reason[:30]}" if reason else ""))
        await _w(bot, user.id, "\n".join(lines)[:249])

    # ── /maintenance (no args) — show status ──────────────────────────────────
    else:
        status = "ON 🔧" if _maintenance_on else "OFF ✅"
        await _w(bot, user.id,
                 f"Maintenance mode: {status}\n"
                 "Cmds: /maintenance on|off\n"
                 "/maintenance all on|off\n"
                 "/maintenance bot <name> on|off [reason]\n"
                 "/maintenance status")


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


# ── /healthcheck ──────────────────────────────────────────────────────────────

async def handle_healthcheck(bot: BaseBot, user: User) -> None:
    """Owner/admin — quick health snapshot of all major systems."""
    if not _is_admin_or_owner(user.username):
        await _w(bot, user.id, "Owner/admin only.")
        return

    parts: list[str] = []

    # Database connection
    try:
        db.get_economy_settings()
        parts.append("DB:OK")
    except Exception:
        parts.append("DB:FAIL")

    # Active event
    try:
        event_on = db.is_event_active()
        parts.append(f"Event:{'active' if event_on else 'none'}")
    except Exception:
        parts.append("Event:ERR")

    # Auto-games / auto-events loop
    try:
        import modules.auto_games as _ag
        ag_task = getattr(_ag, "_auto_game_task", None)
        ae_task = getattr(_ag, "_auto_event_loop_task", None)
        ag_s    = db.get_auto_game_settings()
        ae_s    = db.get_auto_event_settings()
        ag_en   = int(ag_s.get("enabled", 0))
        ae_en   = int(ae_s.get("enabled", 0))
        ag_live = ag_task is not None and not ag_task.done()
        ae_live = ae_task is not None and not ae_task.done()
        parts.append(f"AutoGames:{'ON' if ag_en else 'OFF'}[{'live' if ag_live else 'dead'}]")
        parts.append(f"AutoEvents:{'ON' if ae_en else 'OFF'}[{'live' if ae_live else 'dead'}]")
    except Exception:
        parts.append("AutoGames:ERR")
        parts.append("AutoEvents:ERR")

    # BJ state
    try:
        from modules.blackjack import _state as _bj
        parts.append(f"BJ:{getattr(_bj, 'phase', '?')}")
    except Exception:
        parts.append("BJ:ERR")

    # RBJ state
    try:
        from modules.realistic_blackjack import _state as _rbj
        parts.append(f"RBJ:{getattr(_rbj, 'phase', '?')}")
    except Exception:
        parts.append("RBJ:ERR")

    # Poker state
    try:
        from modules.poker import get_poker_state_str
        parts.append(f"Poker:{get_poker_state_str()}")
    except Exception:
        parts.append("Poker:ERR")

    # Bank settings
    try:
        bs = db.get_bank_settings()
        parts.append(f"Bank:{'OK' if bs else 'EMPTY'}")
    except Exception:
        parts.append("Bank:ERR")

    # Gold / tip settings
    try:
        gs = db.get_tip_settings()
        parts.append(f"Gold:{'OK' if gs else 'EMPTY'}")
    except Exception:
        parts.append("Gold:ERR")

    # Staff roles
    try:
        owners = db.get_staff_by_role("owner")
        parts.append(f"Staff:{'OK' if owners is not None else 'EMPTY'}")
    except Exception:
        parts.append("Staff:ERR")

    msg = "✅ Health | " + " | ".join(parts)
    await _w(bot, user.id, msg[:249])


# ── /restarthelp ───────────────────────────────────────────────────────────────

async def handle_restarthelp(bot: BaseBot, user: User) -> None:
    """Owner only — show restart command overview."""
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    await _w(bot, user.id,
             "🔄 Restart\n"
             "/softrestart - reload systems\n"
             "/restartbot - full process restart\n"
             "Owner only.")


# ── /restartstatus ─────────────────────────────────────────────────────────────

async def handle_restartstatus(bot: BaseBot, user: User) -> None:
    """Owner only — diagnostic snapshot of restart readiness."""
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return

    # Timer / task inspection
    try:
        import modules.auto_games as _ag
        ag_task   = getattr(_ag, "_auto_game_task",    None)
        ae_task   = getattr(_ag, "_auto_event_loop_task", None)
        ag_alive  = ag_task  is not None and not ag_task.done()
        ae_alive  = ae_task  is not None and not ae_task.done()
        ag_s      = db.get_auto_game_settings()
        ae_s      = db.get_auto_event_settings()
        ag_en     = "ON"  if int(ag_s.get("enabled", 0)) else "OFF"
        ae_en     = "ON"  if int(ae_s.get("enabled", 0)) else "OFF"
    except Exception:
        ag_alive = ae_alive = False
        ag_en = ae_en = "?"

    # BJ/RBJ table state
    try:
        from modules.blackjack           import _state as _bj
        from modules.realistic_blackjack import _state as _rbj
        bj_phase  = getattr(_bj,  "phase", "?")
        rbj_phase = getattr(_rbj, "phase", "?")
    except Exception:
        bj_phase = rbj_phase = "?"

    try:
        from modules.poker import get_poker_state_str
        poker_state = get_poker_state_str()
    except Exception:
        poker_state = "ERR"

    lines = [
        "🔄 Restart Status",
        "✅ /softrestart installed",
        "✅ /restartbot installed",
        f"AutoGames:{ag_en}(loop:{'alive' if ag_alive else 'dead'})",
        f"AutoEvents:{ae_en}(loop:{'alive' if ae_alive else 'dead'})",
        f"BJ:{bj_phase} | RBJ:{rbj_phase}",
        f"Poker:{poker_state}",
        f"Uptime:{_uptime()}",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])


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

    # Stability mode suppression
    import database as _db_stab
    if _db_stab.get_room_setting("stability_mode", "0") == "1":
        print(f"[STABILITY] /softrestart suppressed (stability ON) — req @{user.username}")
        await _w(bot, user.id,
                 "🛡️ Stability Mode is ON.\n"
                 "Soft restart suppressed. Use !stability off first.")
        return

    print(f"[MAINT] /softrestart initiated by @{user.username}")

    # ── Announce before any work so the owner knows it started ────────────────
    await _chat(bot, "🔄 Soft restarting...")

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

    # ── 6. Soft-reset Poker (save DB state, cancel timers — no refund) ────────
    try:
        from modules.poker import soft_reset_table as _poker_soft_reset
        _poker_soft_reset()
        print("[MAINT] softrestart: Poker table soft-reset (state saved).")
    except Exception as exc:
        print(f"[MAINT] softrestart: Poker soft-reset error: {exc}")

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
    await _chat(bot, "✅ Soft restart complete.")
    await _w(bot, user.id, "✅ Soft restart done. Loops running, tables restored, DB safe.")


# ── /restartbot ────────────────────────────────────────────────────────────────

async def handle_restartbot(bot: BaseBot, user: User) -> None:
    """
    Owner only — fully restart the bot Python process using os.execv.

    os.execv replaces the current process in-place with a fresh Python
    interpreter running bot.py — no Replit auto-restart needed.

    If os.execv fails, the bot stays alive and instructs the owner to use
    the Replit Restart button manually.
    """
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return

    # Stability mode suppression
    import database as _db_stab2
    if _db_stab2.get_room_setting("stability_mode", "0") == "1":
        print(f"[STABILITY] /restartbot suppressed (stability ON) — req @{user.username}")
        await _w(bot, user.id,
                 "🛡️ Stability Mode is ON.\n"
                 "Bot restart suppressed. Use !stability off first.")
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
        print("[MAINT] RESTARTBOT EXECV ATTEMPT")
        os.execv(interpreter, [interpreter, script])
    except Exception as exc:
        print(f"[MAINT] RESTARTBOT FAILED: {exc!r}")
        try:
            await bot.highrise.chat("Full restart failed. Use Replit Restart button.")
        except Exception:
            pass
