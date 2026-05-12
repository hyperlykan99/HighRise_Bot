"""
modules/time_exp.py
-------------------
Time-in-Room EXP System.

Players earn existing profile XP for staying in the room.
  - AFK/idle players earn the base tier rate (no penalty for being idle).
  - Active players (chatted or emoted within the last 5 minutes) earn a
    small bonus on top of the tier rate.
  - Scaled tiers reward longer continuous stays.
  - A daily cap prevents unlimited grinding.
  - Only the Host bot runs the award loop; all bots track session joins.
  - Level-up announcements use the existing public chat format.

All messages ≤ 249 chars.
"""

import asyncio
import time
from datetime import datetime, timezone

import database as db
from modules.permissions import is_owner, is_admin, is_manager


# ---------------------------------------------------------------------------
# EXP tier table: (minimum_stay_seconds, exp_per_minute)
# Evaluated top-down — first matching threshold wins.
# ---------------------------------------------------------------------------
_TIERS: list[tuple[int, float]] = [
    (8 * 3600, 3.0),   # 8+ hours
    (4 * 3600, 2.5),   # 4–8 hours
    (2 * 3600, 2.0),   # 2–4 hours
    (1 * 3600, 1.5),   # 1–2 hours
    (30 * 60,  1.25),  # 30–60 minutes
    (0,        1.0),   # 0–30 minutes
]


# ---------------------------------------------------------------------------
# In-memory session tracking: user_id → session dict
#   join_ts        : time.monotonic() when the player joined
#   last_award_ts  : time.monotonic() of the last tick that awarded EXP
#   last_active_ts : time.monotonic() of last chat/emote, or None
# Resets on bot restart — intentional; we never backpay offline time.
# ---------------------------------------------------------------------------
_sessions: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------
_SETTING_DEFAULTS: dict[str, str] = {
    "time_exp_enabled":              "true",
    "time_exp_cap":                  "1500",
    "time_exp_tick_seconds":         "60",
    "time_exp_active_bonus_enabled": "true",
    "time_exp_active_bonus":         "0.25",
    "time_exp_active_window_min":    "5",
    "time_exp_bot_exp_enabled":      "false",
}


def _setting(key: str) -> str:
    return db.get_room_setting(key, _SETTING_DEFAULTS[key])


def _setting_bool(key: str) -> bool:
    return _setting(key).lower() == "true"


def _setting_int(key: str) -> int:
    try:
        return int(_setting(key))
    except (ValueError, TypeError):
        return int(_SETTING_DEFAULTS[key])


def _setting_float(key: str) -> float:
    try:
        return float(_setting(key))
    except (ValueError, TypeError):
        return float(_SETTING_DEFAULTS[key])


# ---------------------------------------------------------------------------
# Session management — called from main.py event hooks
# ---------------------------------------------------------------------------

def record_join(user_id: str) -> None:
    """Register a player as entering the room."""
    now = time.monotonic()
    _sessions[user_id] = {
        "join_ts":       now,
        "last_award_ts": now,
        "last_active_ts": None,
    }


def record_leave(user_id: str) -> None:
    """Remove a player's session when they leave."""
    _sessions.pop(user_id, None)


def record_activity(user_id: str) -> None:
    """Mark a player as recently active (chat or emote)."""
    s = _sessions.get(user_id)
    if s is not None:
        s["last_active_ts"] = time.monotonic()


# ---------------------------------------------------------------------------
# EXP tier computation
# ---------------------------------------------------------------------------

def _tier_rate(stay_seconds: float) -> float:
    for min_secs, rate in _TIERS:
        if stay_seconds >= min_secs:
            return rate
    return 1.0


# ---------------------------------------------------------------------------
# Per-player tick award
# ---------------------------------------------------------------------------

async def _award_player(bot, user_id: str, username: str, s: dict) -> None:
    """Compute and award time EXP for one player. Mutates s['last_award_ts']."""
    now       = time.monotonic()
    stay_secs = now - s["join_ts"]
    elapsed   = now - s["last_award_ts"]
    s["last_award_ts"] = now

    cap          = _setting_int("time_exp_cap")
    daily_earned = db.get_time_exp_daily(user_id)
    if daily_earned >= cap:
        return  # Daily cap reached — nothing to award

    rate     = _tier_rate(stay_secs)
    base_exp = rate * (elapsed / 60.0)

    bonus_exp = 0.0
    if _setting_bool("time_exp_active_bonus_enabled"):
        window_sec  = _setting_float("time_exp_active_window_min") * 60.0
        last_active = s.get("last_active_ts")
        if last_active is not None and (now - last_active) <= window_sec:
            bonus_exp = _setting_float("time_exp_active_bonus") * (elapsed / 60.0)

    raw_exp   = base_exp + bonus_exp
    remaining = cap - daily_earned
    final_exp = min(raw_exp, float(remaining))
    final_int = max(0, int(round(final_exp)))
    if final_int <= 0:
        return

    total_xp, old_level, new_level = db.add_xp(user_id, final_int)
    db.add_time_exp_daily(user_id, final_int)

    if new_level > old_level:
        display = db.get_display_name(user_id, username)
        try:
            await bot.highrise.chat(
                f"🎉 {display} leveled up to Level {new_level}! 🌟"
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Full room tick — awards all present, tracked players
# ---------------------------------------------------------------------------

async def _run_tick(bot) -> None:
    from modules.gold import _room_cache, _bot_user_id, _known_bot_ids
    bot_exp_enabled = _setting_bool("time_exp_bot_exp_enabled")
    for _uname_lower, (uid, uname) in list(_room_cache.items()):
        if uid == _bot_user_id:
            continue
        if not bot_exp_enabled and uid in _known_bot_ids:
            print(f"[TIME_EXP] skipped_bot user={uname}")
            continue
        db.ensure_user(uid, uname)
        s = _sessions.get(uid)
        if s is None:
            record_join(uid)
            continue  # skip this tick — no backpay
        try:
            await _award_player(bot, uid, uname, s)
        except Exception as exc:
            print(f"[TIME_EXP] Award error for {uname}: {exc}")


# ---------------------------------------------------------------------------
# Background loop — started only by the host bot
# ---------------------------------------------------------------------------

async def time_exp_loop(bot) -> None:
    """Main time-EXP loop. Must only be started on the host bot."""
    print("[TIME_EXP] Loop started.")
    await asyncio.sleep(30)  # allow room cache to populate before first tick
    while True:
        tick = max(30, _setting_int("time_exp_tick_seconds"))
        await asyncio.sleep(tick)
        if not _setting_bool("time_exp_enabled"):
            continue
        try:
            await _run_tick(bot)
        except Exception as exc:
            print(f"[TIME_EXP] Tick error: {exc}")


# ---------------------------------------------------------------------------
# Helper for status display
# ---------------------------------------------------------------------------

def get_session_count() -> int:
    return len(_sessions)


# ---------------------------------------------------------------------------
# Shared whisper helper (always ≤ 249 chars)
# ---------------------------------------------------------------------------

async def _w(bot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


def _is_manager_plus(username: str) -> bool:
    return is_manager(username) or is_admin(username) or is_owner(username)


def _is_admin_plus(username: str) -> bool:
    return is_admin(username) or is_owner(username)


# ---------------------------------------------------------------------------
# Admin command handlers
# ---------------------------------------------------------------------------

async def handle_settimeexp(bot, user, args: list[str]) -> None:
    """/settimeexp on|off — enable or disable the time-EXP system."""
    if not _is_manager_plus(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    sub = args[1].lower() if len(args) > 1 else ""
    if sub in ("on", "true", "enable", "1"):
        db.set_room_setting("time_exp_enabled", "true")
        await _w(bot, user.id,
            "✅ Time EXP ON. Players earn EXP for staying in the room.")
    elif sub in ("off", "false", "disable", "0"):
        db.set_room_setting("time_exp_enabled", "false")
        await _w(bot, user.id, "✅ Time EXP OFF.")
    else:
        cur = "ON" if _setting_bool("time_exp_enabled") else "OFF"
        await _w(bot, user.id,
            f"Time EXP is currently {cur}. Usage: !settimeexp on | off")


async def handle_settimeexpcap(bot, user, args: list[str]) -> None:
    """/settimeexpcap <amount> — set the daily time-EXP cap."""
    if not _is_manager_plus(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 2:
        cur = _setting_int("time_exp_cap")
        await _w(bot, user.id,
            f"Daily time EXP cap: {cur}. Usage: !settimeexpcap <amount>")
        return
    try:
        val = int(args[1])
        if val < 0:
            raise ValueError
    except ValueError:
        await _w(bot, user.id,
            "⚠️ Enter a valid positive number. Usage: !settimeexpcap <amount>")
        return
    db.set_room_setting("time_exp_cap", str(val))
    await _w(bot, user.id, f"✅ Daily time EXP cap set to {val} EXP.")


async def handle_settimeexptick(bot, user, args: list[str]) -> None:
    """/settimeexptick <seconds> — set the tick interval (admin+)."""
    if not _is_admin_plus(user.username):
        await _w(bot, user.id, "Admin+ only.")
        return
    if len(args) < 2:
        cur = _setting_int("time_exp_tick_seconds")
        await _w(bot, user.id,
            f"Current tick: {cur}s. Usage: !settimeexptick <seconds>")
        return
    try:
        val = int(args[1])
        if val < 30:
            await _w(bot, user.id, "⚠️ Minimum tick is 30 seconds.")
            return
    except ValueError:
        await _w(bot, user.id, "⚠️ Enter a valid number of seconds.")
        return
    db.set_room_setting("time_exp_tick_seconds", str(val))
    await _w(bot, user.id, f"✅ Time EXP tick set to {val} seconds.")


async def handle_settimeexpbonus(bot, user, args: list[str]) -> None:
    """/settimeexpbonus on|off — enable or disable the active-player bonus."""
    if not _is_manager_plus(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    sub = args[1].lower() if len(args) > 1 else ""
    if sub in ("on", "true", "enable", "1"):
        db.set_room_setting("time_exp_active_bonus_enabled", "true")
        await _w(bot, user.id,
            "✅ Active bonus ON. Activity gives +0.25 EXP/min bonus.")
    elif sub in ("off", "false", "disable", "0"):
        db.set_room_setting("time_exp_active_bonus_enabled", "false")
        await _w(bot, user.id, "✅ Active bonus OFF.")
    else:
        cur = "ON" if _setting_bool("time_exp_active_bonus_enabled") else "OFF"
        await _w(bot, user.id,
            f"Active bonus is {cur}. Usage: !settimeexpbonus on | off")


async def handle_timeexpstatus(bot, user, args: list[str]) -> None:
    """/timeexpstatus — show current time-EXP configuration."""
    if not _is_manager_plus(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    enabled  = "ON" if _setting_bool("time_exp_enabled") else "OFF"
    cap      = _setting_int("time_exp_cap")
    tick     = _setting_int("time_exp_tick_seconds")
    bonus    = "ON" if _setting_bool("time_exp_active_bonus_enabled") else "OFF"
    bval     = _setting_float("time_exp_active_bonus")
    win      = int(_setting_float("time_exp_active_window_min"))
    count    = get_session_count()
    bot_xp   = "ON" if _setting_bool("time_exp_bot_exp_enabled") else "OFF"
    await _w(bot, user.id,
        f"⏱ Time EXP: {enabled} | Cap: {cap}/day | Tick: {tick}s | "
        f"Sessions: {count}")
    await _w(bot, user.id,
        f"🎯 Active bonus: {bonus} (+{bval}/min within {win}-min window) | "
        f"Allow Bot XP: {bot_xp}")
    await _w(bot, user.id,
        "📈 0-30m=1 | 30-60m=1.25 | 1-2h=1.5 | 2-4h=2 | 4-8h=2.5 | 8h+=3 EXP/min")


async def handle_setallowbotxp(bot, user, args: list[str]) -> None:
    """/setallowbotxp on|off — allow or deny bots from earning Time EXP (admin+)."""
    if not _is_admin_plus(user.username):
        await _w(bot, user.id, "Admin+ only.")
        return
    sub = args[1].lower() if len(args) > 1 else ""
    if sub in ("on", "true", "enable", "1"):
        db.set_room_setting("time_exp_bot_exp_enabled", "true")
        await _w(bot, user.id,
            "✅ Allow Bot XP: ON. Bots will earn Time EXP.")
    elif sub in ("off", "false", "disable", "0"):
        db.set_room_setting("time_exp_bot_exp_enabled", "false")
        await _w(bot, user.id,
            "✅ Allow Bot XP: OFF. Bots are excluded from Time EXP.")
    else:
        cur = "ON" if _setting_bool("time_exp_bot_exp_enabled") else "OFF"
        await _w(bot, user.id,
            f"Allow Bot XP is {cur}. Usage: !setallowbotxp on | off")
