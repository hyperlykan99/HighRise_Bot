"""
modules/auto_games.py
---------------------
Automated game timers, random mini game hosting, and random events.

Features:
  - Configurable answer timer for trivia / scramble / riddle (default 60 s)
  - Auto random mini game loop (default every 10 minutes)
  - Auto random event loop (default every 60 minutes)

Commands:
  /resetgame                      — mod+; cancel active game, reveal answer
  /gameconfig                     — manager+; show all auto settings
  /setgametimer <seconds>         — manager+; answer timer 15–180 s
  /autogames on|off|status        — on/off: manager+; status: public
  /setautogameinterval <minutes>  — manager+; 5–120 min
  /autoevents on|off|status       — on/off: manager+; status: public
  /setautoeventinterval <minutes> — manager+; 30–1440 min
  /setautoeventduration <minutes> — manager+; 5–180 min
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone, timedelta

from highrise import BaseBot, User

import database as db
from config import BOT_ID, BOT_MODE
from modules.maintenance import is_maintenance
from modules.permissions  import can_manage_games, can_moderate, is_admin, is_owner
import modules.trivia   as trivia
import modules.scramble as scramble
import modules.riddle   as riddle


# ---------------------------------------------------------------------------
# Autogames ownership constants
# ---------------------------------------------------------------------------

# Modes that may NEVER run auto-games unless explicitly set as owner via setting
_NEVER_AUTOGAMES_MODES = frozenset({
    "blackjack", "poker", "miner", "banker", "shopkeeper", "security", "dj"
})

_AG_MODE_NAMES: dict[str, str] = {
    "eventhost": "Event", "host": "Host", "all": "Main", "disabled": "Disabled",
}


def _get_autogames_lock_holder() -> str:
    """Return the bot_id currently holding the autogames module lock, or ''."""
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT bot_id FROM bot_module_locks "
            "WHERE module='autogames' AND expires_at > datetime('now')",
        ).fetchone()
        conn.close()
        return row["bot_id"] if row else ""
    except Exception:
        return ""


def should_this_bot_run_autogames() -> tuple[bool, str]:
    """
    Determines if THIS bot instance should run the auto-games loop.
    Returns (should_run, reason_string).

    Rules:
    - autogames_owner_bot_mode=disabled  → no bot runs
    - mode matches owner                 → run
    - mode in _NEVER_AUTOGAMES_MODES     → never (unless explicitly owner)
    - mode=all + no split bots online    → run (fallback only)
    - mode=host + owner offline + fallback ON → run
    """
    try:
        owner_mode = db.get_room_setting("autogames_owner_bot_mode", "eventhost")
    except Exception:
        owner_mode = "eventhost"

    mode = BOT_MODE

    if owner_mode == "disabled":
        return False, "autogames disabled by setting"

    # This bot's mode IS the configured owner
    if owner_mode == mode:
        return True, f"this bot is the autogames owner ({mode})"

    # Blocked modes — never run autogames unless explicitly set as owner
    if mode in _NEVER_AUTOGAMES_MODES:
        return False, f"{mode} bot never runs autogames (owner={owner_mode})"

    # BOT_MODE=all: run only if the owner bot and all split bots are offline
    if mode == "all":
        try:
            from modules.multi_bot import _is_mode_online
            if _is_mode_online(owner_mode):
                return False, f"owner bot ({owner_mode}) is online; all mode defers"
            split_modes = ("blackjack", "poker", "miner", "banker",
                           "shopkeeper", "security", "dj", "eventhost", "host")
            active_splits = [m for m in split_modes if _is_mode_online(m)]
            if active_splits:
                return False, f"split bots online ({', '.join(active_splits)})"
            return True, "all mode — no split bots online"
        except Exception:
            return True, "all mode — online check unavailable"

    # Host bot: may run if owner is offline and fallback is ON
    if mode == "host":
        try:
            from modules.multi_bot import _is_mode_online, _fallback_enabled
            if _is_mode_online(owner_mode):
                return False, f"owner bot ({owner_mode}) is online; host defers"
            if _fallback_enabled():
                return True, f"host fallback (owner={owner_mode} offline)"
            return False, f"owner ({owner_mode}) offline but fallback OFF"
        except Exception:
            return False, "host mode — fallback check failed"

    return False, f"bot mode '{mode}' is not the autogames owner ('{owner_mode}')"


# ---------------------------------------------------------------------------
# Answer timer
# ---------------------------------------------------------------------------

_answer_timer_task: asyncio.Task | None = None
_answer_timer_game_id: int = 0
_game_counter: int = 0


def _next_game_id() -> int:
    global _game_counter
    _game_counter += 1
    return _game_counter


def cancel_answer_timer() -> None:
    """Cancel any running answer timer without revealing the answer."""
    global _answer_timer_task
    if _answer_timer_task and not _answer_timer_task.done():
        _answer_timer_task.cancel()
    _answer_timer_task = None


async def _answer_timer_coro(
    bot: BaseBot, game_type: str, answer: str, game_id: int, seconds: int
) -> None:
    try:
        await asyncio.sleep(seconds)
    except asyncio.CancelledError:
        return

    # Stale-timer guard: if a new game started, IDs won't match
    if _answer_timer_game_id != game_id:
        return

    # Check the correct module's _active reference
    if game_type == "trivia"   and trivia._active   is None:
        return
    if game_type == "scramble" and scramble._active is None:
        return
    if game_type == "riddle"   and riddle._active   is None:
        return

    # Clear the active game
    if game_type == "trivia":
        trivia._active   = None
    elif game_type == "scramble":
        scramble._active = None
    elif game_type == "riddle":
        riddle._active   = None

    print(f"[AUTO_GAMES] Timer expired for {game_type}. Answer: {answer}")
    try:
        await bot.highrise.chat(
            f"⏰ Time's up! The answer was: {answer}"
        )
    except Exception as e:
        print(f"[AUTO_GAMES] Timer chat error: {e}")


def start_answer_timer(bot: BaseBot, game_type: str, answer: str) -> None:
    """Start (or restart) the answer countdown for the current game."""
    global _answer_timer_task, _answer_timer_game_id
    cancel_answer_timer()
    game_id = _next_game_id()
    _answer_timer_game_id = game_id
    settings = db.get_auto_game_settings()
    seconds  = settings["game_answer_timer"]
    _answer_timer_task = asyncio.create_task(
        _answer_timer_coro(bot, game_type, answer, game_id, seconds)
    )
    print(f"[AUTO_GAMES] Answer timer: {seconds}s for {game_type}.")


# ---------------------------------------------------------------------------
# Auto mini game loop
# ---------------------------------------------------------------------------

_auto_game_task: asyncio.Task | None = None


def _any_game_active() -> bool:
    return trivia.is_active() or scramble.is_active() or riddle.is_active()


async def _auto_game_loop(bot: BaseBot) -> None:
    print("[AUTO_GAMES] Auto mini game loop started.")
    try:
        while True:
            settings = db.get_auto_game_settings()
            interval = max(5, settings["auto_minigame_interval"]) * 60
            await asyncio.sleep(interval)

            settings = db.get_auto_game_settings()
            if not settings["auto_minigames_enabled"]:
                print("[AUTO_GAMES] Auto games OFF, skipping.")
                continue
            if is_maintenance():
                print("[AUTO_GAMES] Maintenance ON, skipping auto game.")
                continue
            if _any_game_active():
                print("[AUTO_GAMES] Game already active, skipping auto game.")
                continue

            # Re-check ownership (setting may have changed while sleeping)
            should_run, reason = should_this_bot_run_autogames()
            if not should_run:
                print(f"[AUTOGAMES] Ownership changed mid-loop; {reason}. Stopping loop.")
                return

            # Acquire module lock to prevent duplicate game starts across bots
            if not db.acquire_module_lock("autogames", BOT_ID, ttl_seconds=120):
                print("[AUTOGAMES] Lock held by another bot; skipping this iteration.")
                continue

            game_type = random.choice(["trivia", "scramble", "riddle"])
            print(f"[AUTO_GAMES] Auto-starting {game_type}.")

            try:
                await bot.highrise.chat("🎲 Random mini game starting!")
            except Exception as e:
                print(f"[AUTO_GAMES] Announce error: {e}")

            try:
                if game_type == "trivia":
                    await trivia.auto_start(bot)
                    if trivia.is_active():
                        ans = trivia.get_current_answer() or ""
                        start_answer_timer(bot, "trivia", ans)
                elif game_type == "scramble":
                    await scramble.auto_start(bot)
                    if scramble.is_active():
                        ans = scramble.get_current_answer() or ""
                        start_answer_timer(bot, "scramble", ans)
                elif game_type == "riddle":
                    await riddle.auto_start(bot)
                    if riddle.is_active():
                        ans = riddle.get_current_answer() or ""
                        start_answer_timer(bot, "riddle", ans)
            except Exception as e:
                print(f"[AUTO_GAMES] Error starting auto {game_type}: {e}")

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[AUTO_GAMES] Auto game loop crashed: {e}")
    finally:
        print("[AUTO_GAMES] Auto mini game loop ended.")


def start_auto_game_loop(bot: BaseBot) -> None:
    """Start the auto mini game background loop — only if this bot is the owner."""
    global _auto_game_task
    if _auto_game_task and not _auto_game_task.done():
        print("[AUTOGAMES] Auto game loop already running.")
        return
    should_run, reason = should_this_bot_run_autogames()
    if not should_run:
        print(f"[AUTOGAMES] Skipped on {BOT_MODE}; {reason}.")
        return
    print(f"[AUTOGAMES] Running on {BOT_MODE} bot.")
    _auto_game_task = asyncio.create_task(_auto_game_loop(bot))


# ---------------------------------------------------------------------------
# Auto event loop
# ---------------------------------------------------------------------------

_auto_event_loop_task: asyncio.Task | None = None


async def _auto_event_loop(bot: BaseBot) -> None:
    from modules.events import (
        _cancel_event_task, _event_timer, EVENTS, _MINING_EVENT_IDS,
    )
    import modules.events as events_mod

    print("[AUTO_GAMES] Auto event loop started.")
    try:
        while True:
            # ── Heartbeat tick ───────────────────────────────────────────────
            try:
                db.set_auto_event_setting_str(
                    "last_scheduler_tick",
                    datetime.now(timezone.utc).isoformat(),
                )
            except Exception:
                pass

            settings = db.get_auto_event_settings()
            interval = max(30, settings["auto_event_interval"]) * 60  # secs

            # Store next_event_at so /aenext / /autoeventstatus can show it
            next_at = (
                datetime.now(timezone.utc) + timedelta(seconds=interval)
            ).isoformat()
            try:
                db.set_auto_event_setting_str("next_event_at", next_at)
            except Exception:
                pass

            await asyncio.sleep(interval)

            # ── Post-sleep checks ────────────────────────────────────────────
            settings = db.get_auto_event_settings()
            if not settings["auto_events_enabled"]:
                print("[AUTO_GAMES] Auto events OFF, skipping.")
                continue
            if is_maintenance():
                print("[AUTO_GAMES] Maintenance ON, skipping auto event.")
                continue
            if db.is_event_active() or db.get_active_mining_event():
                print("[AUTO_GAMES] Event already active, skipping.")
                continue

            # Re-check ownership (setting may have changed while sleeping)
            should_run, reason = should_this_bot_run_autogames()
            if not should_run:
                print(f"[AUTOGAMES] Event ownership changed; {reason}. Stopping loop.")
                return

            # Acquire module lock to prevent duplicate event starts across bots
            if not db.acquire_module_lock("autogames_event", BOT_ID, ttl_seconds=120):
                print("[AUTOGAMES] Event lock held by another bot; skipping.")
                continue

            # ── Weighted pool selection with cooldowns ───────────────────────
            eligible = db.get_eligible_pool_events()
            if not eligible:
                print("[AUTO_GAMES] No eligible events in pool; skipping.")
                continue

            total_weight = sum(row["weight"] for row in eligible)
            if total_weight <= 0:
                event_id = random.choice(eligible)["event_id"]
            else:
                r   = random.uniform(0, total_weight)
                acc = 0.0
                event_id = eligible[-1]["event_id"]
                for row in eligible:
                    acc += row["weight"]
                    if r <= acc:
                        event_id = row["event_id"]
                        break

            ev      = EVENTS.get(event_id, {})
            name    = ev.get("name", event_id)
            dur_min = max(5, settings["auto_event_duration"])
            duration = dur_min * 60

            # ── Start the event ──────────────────────────────────────────────
            ev_type = ev.get("event_type", "room")
            if ev_type == "mining" or event_id in _MINING_EVENT_IDS:
                db.start_mining_event(event_id, "auto", dur_min)
                try:
                    msg = (
                        f"⛏️ Auto Mining Event: {name} for {dur_min}m!\n"
                        f"{ev.get('desc','')[:80]}"
                    )
                    await bot.highrise.chat(msg[:249])
                except Exception as exc:
                    print(f"[AUTO_GAMES] Mining event announce error: {exc}")
            else:
                expires_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=duration)
                ).isoformat()
                db.set_active_event(event_id, expires_at)
                _cancel_event_task()
                events_mod._event_task = asyncio.create_task(
                    _event_timer(bot, event_id, duration)
                )
                try:
                    msg = (
                        f"🎉 Auto Event: {name} for {dur_min}m!\n"
                        f"{ev.get('desc','')[:80]}"
                    )
                    await bot.highrise.chat(msg[:249])
                except Exception as exc:
                    print(f"[AUTO_GAMES] Room event announce error: {exc}")

            # ── Post-start bookkeeping ───────────────────────────────────────
            try:
                db.update_pool_last_started(event_id)
                db.add_event_history_entry(event_id, name, "auto", True, duration)
            except Exception as exc:
                print(f"[AUTO_GAMES] History/pool update error: {exc}")
            try:
                db.set_auto_event_setting_str("next_event_id", "")
                db.set_auto_event_setting_str("next_event_at", "")
            except Exception:
                pass

            print(f"[AUTO_GAMES] Auto event: {event_id} for {dur_min}m.")

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[AUTO_GAMES] Auto event loop crashed: {e}")
    finally:
        print("[AUTO_GAMES] Auto event loop ended.")


def start_auto_event_loop(bot: BaseBot) -> None:
    """Start the auto event background loop — only if this bot is the owner."""
    global _auto_event_loop_task
    if _auto_event_loop_task and not _auto_event_loop_task.done():
        print("[AUTOGAMES] Auto event loop already running.")
        return
    should_run, reason = should_this_bot_run_autogames()
    if not should_run:
        print(f"[AUTOGAMES] Event loop skipped on {BOT_MODE}; {reason}.")
        return
    _auto_event_loop_task = asyncio.create_task(_auto_event_loop(bot))


# ---------------------------------------------------------------------------
# /resetgame
# ---------------------------------------------------------------------------

async def handle_resetgame(bot: BaseBot, user: User) -> None:
    """/resetgame — mod+; cancel timer, reveal answer, clear game."""
    if not can_moderate(user.username):
        await bot.highrise.send_whisper(user.id, "Staff only.")
        return

    cancel_answer_timer()

    answer:    str | None = None
    game_type: str | None = None
    if trivia.is_active():
        answer    = trivia.get_current_answer()
        game_type = "trivia"
    elif scramble.is_active():
        answer    = scramble.get_current_answer()
        game_type = "scramble"
    elif riddle.is_active():
        answer    = riddle.get_current_answer()
        game_type = "riddle"

    trivia._active   = None
    scramble._active = None
    riddle._active   = None

    if answer and game_type:
        try:
            await bot.highrise.chat(
                f"🔧 Game reset! ({game_type}) Answer: {answer}"
            )
        except Exception:
            pass
    try:
        await bot.highrise.send_whisper(user.id, "✅ Active mini game reset.")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /gameconfig
# ---------------------------------------------------------------------------

async def handle_gameconfig(bot: BaseBot, user: User) -> None:
    """/gameconfig — manager+; display all automation settings."""
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Manager/admin/owner only.")
        return

    gs = db.get_auto_game_settings()
    es = db.get_auto_event_settings()
    ag = "ON"  if gs["auto_minigames_enabled"] else "OFF"
    ae = "ON"  if es["auto_events_enabled"]    else "OFF"

    msg = (
        f"⚙️ Game Config\n"
        f"Answer timer: {gs['game_answer_timer']}s\n"
        f"Auto games: {ag} / {gs['auto_minigame_interval']}m\n"
        f"Auto events: {ae} / {es['auto_event_interval']}m\n"
        f"Event duration: {es['auto_event_duration']}m"
    )
    await bot.highrise.send_whisper(user.id, msg[:249])


# ---------------------------------------------------------------------------
# /setgametimer <seconds>
# ---------------------------------------------------------------------------

async def handle_setgametimer(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: /setgametimer <15–180>")
        return
    try:
        secs = int(args[1])
    except ValueError:
        await bot.highrise.send_whisper(user.id, "Must be a number (15–180).")
        return
    if secs < 15 or secs > 180:
        await bot.highrise.send_whisper(user.id, "Timer must be 15–180 seconds.")
        return
    db.set_auto_game_setting("game_answer_timer", secs)
    await bot.highrise.send_whisper(
        user.id, f"✅ Game answer timer set to {secs}s."
    )


# ---------------------------------------------------------------------------
# /autogames on|off|status
# ---------------------------------------------------------------------------

async def handle_autogames(bot: BaseBot, user: User, args: list[str]) -> None:
    sub = args[1].lower() if len(args) > 1 else "status"

    if sub == "status":
        gs         = db.get_auto_game_settings()
        status     = "ON" if gs["auto_minigames_enabled"] else "OFF"
        owner      = db.get_room_setting("autogames_owner_bot_mode", "eventhost")
        owner_name = _AG_MODE_NAMES.get(owner, owner.title())
        loop_on    = _auto_game_task is not None and not _auto_game_task.done()
        lock_holder = _get_autogames_lock_holder()
        running    = lock_holder or (BOT_MODE if loop_on else "none")
        conflict   = (
            loop_on
            and BOT_MODE != owner
            and owner not in ("disabled", "all")
        )
        msg = (
            f"🎲 AutoGames: {status} | Owner: {owner_name}"
            f" | Running: {running}"
            f" | Every {gs['auto_minigame_interval']}m"
        )
        if conflict:
            msg += " ⚠️ /fixautogames"
        await bot.highrise.send_whisper(user.id, msg[:249])
        return

    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Manager/admin/owner only.")
        return

    if sub == "on":
        db.set_auto_game_setting("auto_minigames_enabled", 1)
        owner      = db.get_room_setting("autogames_owner_bot_mode", "eventhost")
        owner_name = _AG_MODE_NAMES.get(owner, owner.title())
        await bot.highrise.send_whisper(user.id, f"✅ Auto-games ON. Owner: {owner_name}.")
    elif sub == "off":
        db.set_auto_game_setting("auto_minigames_enabled", 0)
        await bot.highrise.send_whisper(user.id, "⛔ Auto-games OFF.")
    else:
        await bot.highrise.send_whisper(
            user.id, "Usage: /autogames on|off|status"
        )


# ---------------------------------------------------------------------------
# /setautogameinterval <minutes>
# ---------------------------------------------------------------------------

async def handle_setautogameinterval(
    bot: BaseBot, user: User, args: list[str]
) -> None:
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(
            user.id, "Usage: /setautogameinterval <5–120>"
        )
        return
    try:
        mins = int(args[1])
    except ValueError:
        await bot.highrise.send_whisper(user.id, "Must be a number (5–120).")
        return
    if mins < 5 or mins > 120:
        await bot.highrise.send_whisper(user.id, "Interval must be 5–120 min.")
        return
    db.set_auto_game_setting("auto_minigame_interval", mins)
    await bot.highrise.send_whisper(
        user.id, f"✅ Auto game interval set to {mins}m."
    )


# ---------------------------------------------------------------------------
# /autoevents on|off|status
# ---------------------------------------------------------------------------

async def handle_autoevents(bot: BaseBot, user: User, args: list[str]) -> None:
    sub = args[1].lower() if len(args) > 1 else "status"

    if sub == "status":
        es     = db.get_auto_event_settings()
        status = "ON" if es["auto_events_enabled"] else "OFF"
        await bot.highrise.send_whisper(
            user.id,
            f"🎉 Auto events: {status} | Every {es['auto_event_interval']}m"
            f" | Duration: {es['auto_event_duration']}m"
        )
        return

    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Manager/admin/owner only.")
        return

    if sub == "on":
        db.set_auto_event_setting("auto_events_enabled", 1)
        await bot.highrise.send_whisper(user.id, "✅ Auto events enabled.")
    elif sub == "off":
        db.set_auto_event_setting("auto_events_enabled", 0)
        await bot.highrise.send_whisper(user.id, "✅ Auto events disabled.")
    else:
        await bot.highrise.send_whisper(
            user.id, "Usage: /autoevents on|off|status"
        )


# ---------------------------------------------------------------------------
# /setautoeventinterval <minutes>
# ---------------------------------------------------------------------------

async def handle_setautoeventinterval(
    bot: BaseBot, user: User, args: list[str]
) -> None:
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(
            user.id, "Usage: /setautoeventinterval <30–1440>"
        )
        return
    try:
        mins = int(args[1])
    except ValueError:
        await bot.highrise.send_whisper(user.id, "Must be a number (30–1440).")
        return
    if mins < 30 or mins > 1440:
        await bot.highrise.send_whisper(
            user.id, "Interval must be 30–1440 minutes."
        )
        return
    db.set_auto_event_setting("auto_event_interval", mins)
    await bot.highrise.send_whisper(
        user.id, f"✅ Auto event interval set to {mins}m."
    )


# ---------------------------------------------------------------------------
# /setautoeventduration <minutes>
# ---------------------------------------------------------------------------

async def handle_setautoeventduration(
    bot: BaseBot, user: User, args: list[str]
) -> None:
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(
            user.id, "Usage: /setautoeventduration <5–180>"
        )
        return
    try:
        mins = int(args[1])
    except ValueError:
        await bot.highrise.send_whisper(user.id, "Must be a number (5–180).")
        return
    if mins < 5 or mins > 180:
        await bot.highrise.send_whisper(
            user.id, "Duration must be 5–180 minutes."
        )
        return
    db.set_auto_event_setting("auto_event_duration", mins)
    await bot.highrise.send_whisper(
        user.id, f"✅ Auto event duration set to {mins}m."
    )


# ---------------------------------------------------------------------------
# /autogamesowner [mode]
# ---------------------------------------------------------------------------

_VALID_OWNER_MODES = {"eventhost", "host", "all", "disabled"}


async def handle_autogamesowner(bot: BaseBot, user: User, args: list[str]) -> None:
    """/autogamesowner [mode] — view or set which bot mode runs auto-games."""
    if len(args) < 2:
        owner      = db.get_room_setting("autogames_owner_bot_mode", "eventhost")
        owner_name = _AG_MODE_NAMES.get(owner, owner.title())
        await bot.highrise.send_whisper(user.id, f"Auto-games owner: {owner_name}")
        return

    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Owner/admin/manager only.")
        return

    new_mode = args[1].lower()
    if new_mode not in _VALID_OWNER_MODES:
        await bot.highrise.send_whisper(
            user.id, "Valid modes: eventhost host all disabled"
        )
        return

    db.set_room_setting("autogames_owner_bot_mode", new_mode)
    if new_mode == "disabled":
        await bot.highrise.send_whisper(user.id, "⛔ Auto-games owner disabled.")
    else:
        owner_name = _AG_MODE_NAMES.get(new_mode, new_mode.title())
        await bot.highrise.send_whisper(
            user.id, f"✅ Auto-games owner set to {owner_name} Bot."
        )


# ---------------------------------------------------------------------------
# /stopautogames (/killautogames) — emergency stop
# ---------------------------------------------------------------------------

async def handle_stopautogames(bot: BaseBot, user: User) -> None:
    """/stopautogames — owner/admin only; disable auto-games and clear locks."""
    if not (is_admin(user.username) or is_owner(user.username)):
        await bot.highrise.send_whisper(user.id, "Owner/admin only.")
        return

    global _auto_game_task, _auto_event_loop_task

    # Disable in DB so all bots' loops will stop on next iteration check
    db.set_auto_game_setting("auto_minigames_enabled", 0)
    db.set_auto_event_setting("auto_events_enabled", 0)

    # Clear autogames module locks from DB
    try:
        conn = db.get_connection()
        conn.execute(
            "DELETE FROM bot_module_locks WHERE module IN ('autogames', 'autogames_event')"
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    # Cancel running tasks on this bot
    cancelled = 0
    if _auto_game_task and not _auto_game_task.done():
        _auto_game_task.cancel()
        _auto_game_task = None
        cancelled += 1
    if _auto_event_loop_task and not _auto_event_loop_task.done():
        _auto_event_loop_task.cancel()
        _auto_event_loop_task = None
        cancelled += 1

    await bot.highrise.send_whisper(
        user.id,
        f"🛑 Auto-games stopped on all bots. (Cancelled {cancelled} task(s) here.)"[:249]
    )


# ---------------------------------------------------------------------------
# /fixautogames — reset autogames owner + clear stale locks
# ---------------------------------------------------------------------------

async def handle_fixautogames(bot: BaseBot, user: User) -> None:
    """/fixautogames — reset autogames owner to Event Bot, clear stale locks (admin/owner)."""
    if not (is_admin(user.username) or is_owner(user.username)):
        await bot.highrise.send_whisper(user.id, "Admin/owner only.")
        return

    db.set_room_setting("autogames_owner_bot_mode", "eventhost")

    try:
        conn = db.get_connection()
        conn.execute(
            "DELETE FROM bot_module_locks "
            "WHERE module IN ('autogames', 'autogames_event')"
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    await bot.highrise.send_whisper(
        user.id, "✅ AutoGames fixed. Owner: Event Bot."
    )
