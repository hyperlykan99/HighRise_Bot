"""
modules/safe_mode.py
--------------------
/safemode on|off|status  — global safe mode gate (security, manager+)
/active                  — show running systems (public)
/repair                  — diagnostic check (security, manager+)
"""
from __future__ import annotations

import datetime

import database as db
from modules.permissions import can_manage_economy


async def _w(bot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Safe mode helpers
# ---------------------------------------------------------------------------

def is_safe_mode() -> bool:
    """Return True if safe mode is globally enabled."""
    return db.get_room_setting("safe_mode_enabled", "0") == "1"


# ---------------------------------------------------------------------------
# /safemode on|off|status
# ---------------------------------------------------------------------------

async def handle_safemode(bot, user, args=None) -> None:
    """/safemode on|off|status — toggle/view global safe mode (manager+)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/owner only.")
        return

    sub = (args[1].lower() if args and len(args) > 1 else "status")

    if sub == "on":
        db.set_room_setting("safe_mode_enabled", "1")
        try:
            db.log_staff_action(user.id, user.username, "safemode_on", "", "", "")
        except Exception:
            pass
        await _w(bot, user.id,
                 "🛡️ Safe Mode ON\n"
                 "Disabled: Auto Events, AutoMine/Fish\n"
                 "Gold Payouts, Force Drops, Casino Betting")

    elif sub == "off":
        db.set_room_setting("safe_mode_enabled", "0")
        try:
            db.log_staff_action(user.id, user.username, "safemode_off", "", "", "")
        except Exception:
            pass
        await _w(bot, user.id, "🛡️ Safe Mode OFF\nAll systems restored.")

    else:
        on = is_safe_mode()
        state = "ON" if on else "OFF"
        disabled = (
            "Auto Events | Gold Payouts\nCasino Betting | Force Drops"
            if on else "None — all systems active"
        )
        await _w(bot, user.id, f"🛡️ Safe Mode: {state}\nDisabled:\n{disabled}"[:249])


# ---------------------------------------------------------------------------
# /active
# ---------------------------------------------------------------------------

_UNKNOWN = "Unknown"


def _mins_left(ends_at: str | None) -> str:
    if not ends_at:
        return ""
    try:
        end = datetime.datetime.fromisoformat(ends_at.replace("Z", "+00:00"))
        if end.tzinfo is None:
            end = end.replace(tzinfo=datetime.timezone.utc)
        secs = max(0, int(
            (end - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        ))
        if secs <= 0:
            return ""
        return f", {secs // 60}m left"
    except Exception:
        return ""


async def handle_active(bot, user) -> None:
    """/active — show what systems are currently running (public)."""
    lines = ["🎮 Active Systems"]

    # Auto event
    try:
        ev = db.get_active_event()
        if ev:
            try:
                from modules.events import EVENTS
                name = EVENTS.get(ev["event_id"], {}).get("name", ev["event_id"])
            except Exception:
                name = ev["event_id"]
            left = _mins_left(ev.get("ends_at"))
            lines.append(f"Auto Event: {name}{left}")
        else:
            lines.append("Auto Event: None")
    except Exception:
        lines.append(f"Auto Event: {_UNKNOWN}")

    # First Hunt Race
    try:
        race = db.get_active_first_find_race()
        if race:
            cat  = race.get("category", "?").title()
            tv   = race.get("target_value", "?")
            left = _mins_left(race.get("ends_at"))
            lines.append(f"First Hunt: {cat} {tv}{left}")
        else:
            lines.append("First Hunt: None")
    except Exception:
        lines.append(f"First Hunt: {_UNKNOWN}")

    # AutoMine / AutoFish
    try:
        mine_on = db.get_room_setting("automine_global_enabled", "1") == "1"
        fish_on = db.get_room_setting("autofish_global_enabled", "1") == "1"
        lines.append(
            f"AutoMine: {'ON' if mine_on else 'OFF'} | "
            f"AutoFish: {'ON' if fish_on else 'OFF'}"
        )
    except Exception:
        lines.append("AutoMine/Fish: Unknown")

    # BJ / RBJ / Poker phase
    try:
        from modules.dashboard import _bj_phase, _rbj_phase, _poker_phase
        lines.append(f"BJ: {_bj_phase()} | RBJ: {_rbj_phase()} | Poker: {_poker_phase()}")
    except Exception:
        pass

    # Auto games
    try:
        from modules.auto_games import get_current_auto_game
        ag = get_current_auto_game()
        lines.append(f"Auto Game: {ag if ag else 'None'}")
    except Exception:
        pass

    # Safe mode
    lines.append(f"Safe Mode: {'ON 🛡️' if is_safe_mode() else 'OFF'}")

    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /repair
# ---------------------------------------------------------------------------

async def handle_repair(bot, user) -> None:
    """/repair — diagnostic check (manager+)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/owner only.")
        return

    lines = ["🛠️ Repair Check"]

    # Command registry orphans
    try:
        from modules.command_registry import REGISTRY
        issues = sum(
            1 for e in REGISTRY.values()
            if not e.owner or e.owner == "unknown"
        )
        lines.append(f"Cmd Issues: {issues}")
    except Exception:
        lines.append("Cmd Issues: Error")

    # Pending race rewards
    try:
        pending = db.get_pending_race_winners_for_banker(limit=50)
        lines.append(f"Pending Rewards: {len(pending)}")
    except Exception:
        lines.append("Pending Rewards: Error")

    # Event scheduler
    try:
        ae_on = db.get_auto_event_setting_str("auto_events_enabled", "1") == "1"
        lines.append(f"Event Scheduler: {'OK' if ae_on else 'Paused'}")
    except Exception:
        lines.append("Event Scheduler: Error")

    # DB health
    try:
        conn = db.get_connection()
        count = conn.execute(
            "SELECT COUNT(*) FROM user_profiles"
        ).fetchone()[0]
        conn.close()
        lines.append(f"Database: OK ({count} profiles)")
    except Exception:
        lines.append("Database: Error")

    # Safe mode
    lines.append(f"Safe Mode: {'ON' if is_safe_mode() else 'OFF'}")

    await _w(bot, user.id, "\n".join(lines)[:249])
