"""
modules/sys_dashboard.py
------------------------
/botdashboard /botsystem — system health overview for managers+.

Shows: bot online status, systems, active events, race, pending rewards,
next auto-event, BJ/RBJ phase.
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


def _is_online(hb_str: str) -> bool:
    """Return True if last_heartbeat_at was within 120 seconds."""
    if not hb_str:
        return False
    try:
        hb = datetime.datetime.fromisoformat(hb_str)
        if hb.tzinfo is None:
            hb = hb.replace(tzinfo=datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        return (now - hb).total_seconds() <= 120
    except Exception:
        return False


def _mins_until(dt_str: str) -> str:
    """Return human-readable time until dt_str."""
    if not dt_str:
        return "?"
    try:
        dt = datetime.datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        secs = max(0, int((dt - now).total_seconds()))
        if secs <= 0:
            return "now"
        mins = secs // 60
        hrs, m = divmod(mins, 60)
        if hrs:
            return f"{hrs}h{m}m"
        return f"{m}m"
    except Exception:
        return "?"


async def handle_sys_dashboard(bot, user, args=None) -> None:
    """/botdashboard — full system health overview. Manager+ only."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/owner only.")
        return

    # ── Bot online status ──────────────────────────────────────────────────
    try:
        instances = db.get_bot_instances()
        bot_parts = []
        for inst in instances[:6]:
            ok   = "✅" if _is_online(inst.get("last_heartbeat_at", "")) else "❌"
            name = inst.get("bot_username", inst.get("bot_mode", "?"))[:10]
            bot_parts.append(f"{name}{ok}")
        bots_line = " | ".join(bot_parts) if bot_parts else "No bots registered"
    except Exception:
        bots_line = "Unknown"

    # ── Systems ────────────────────────────────────────────────────────────
    try:
        mining_on  = db.get_room_setting("mining_enabled",  "1") == "1"
        fishing_on = db.get_room_setting("fishing_enabled", "1") == "1"
    except Exception:
        mining_on = fishing_on = True

    # ── Active events ──────────────────────────────────────────────────────
    ev_name = "None"
    try:
        gen_ev  = db.get_active_event()
        mine_ev = db.get_active_mining_event()
        if gen_ev or mine_ev:
            from modules.events import EVENTS
            if gen_ev:
                ev_name = EVENTS.get(gen_ev["event_id"], {}).get("name", gen_ev["event_id"])
            elif mine_ev:
                eid     = mine_ev.get("event_id", "")
                ev_name = EVENTS.get(eid, {}).get("name", eid) or "Active"
    except Exception:
        pass

    # ── First-find race ────────────────────────────────────────────────────
    race_str = "None"
    try:
        race = db.get_active_first_find_race()
        if race:
            cat      = race.get("category", "?").title()
            tv       = race.get("target_value", "?")
            race_str = f"🏁 {cat} — {tv}"
    except Exception:
        pass

    # ── Pending rewards ────────────────────────────────────────────────────
    try:
        pending_count = len(db.get_pending_race_winners_for_banker(limit=50))
    except Exception:
        pending_count = 0

    # ── Next auto event ────────────────────────────────────────────────────
    next_ev_str = "None scheduled"
    try:
        next_id = db.get_auto_event_setting_str("next_event_id", "")
        next_at = db.get_auto_event_setting_str("next_event_at", "")
        if next_id and next_at:
            from modules.events import EVENTS
            en          = EVENTS.get(next_id, {}).get("name", next_id)
            next_ev_str = f"{en} in {_mins_until(next_at)}"
    except Exception:
        pass

    # ── BJ / RBJ phase from dashboard helpers ────────────────────────────
    bj_ph = rbj_ph = "?"
    try:
        from modules.dashboard import _bj_phase, _rbj_phase
        bj_ph  = _bj_phase()
        rbj_ph = _rbj_phase()
    except Exception:
        pass

    # ── Assemble ───────────────────────────────────────────────────────────
    lines = [
        "📊 Bot Dashboard",
        f"Bots: {bots_line}",
        (f"Mine:{'✅' if mining_on else '❌'} "
         f"Fish:{'✅' if fishing_on else '❌'} "
         f"BJ:{bj_ph} RBJ:{rbj_ph}"),
        f"Event: {ev_name}",
        f"Race: {race_str}",
        f"Pending Gold: {pending_count}",
        f"Next AutoEvent: {next_ev_str}",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])
