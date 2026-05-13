"""
Centralised luck stack calculator for mining and fishing (Update 3.1I).

Provides get_mine_luck_stack() and get_fish_luck_stack() which return dicts
with every luck component, final totals, speed, and auto-duration.
"""
import database as db
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Tool luck tables  (tool_level 1-5)
# ---------------------------------------------------------------------------
_TOOL_LUCK_PTS     = {1: 1,  2: 3,  3: 5,  4: 8,  5: 12}
_TOOL_SPEED_BONUS  = {1: 0,  2: 1,  3: 2,  4: 3,  5: 4}   # seconds faster
_TOOL_DURATION_BONUS = {1: 0, 2: 1, 3: 2,  4: 3,  5: 5}   # extra minutes

# ---------------------------------------------------------------------------
# Rod luck tables  (by rod name)
# ---------------------------------------------------------------------------
_ROD_LUCK_PTS: dict[str, int] = {
    "Driftwood Rod":   1,
    "Bamboo Tide Rod": 3,
    "Copperline Rod":  5,
    "Sailor's Rod":    7,
    "Deepwater Rod":   10,
    "Stormcast Rod":   13,
    "Moonhook Rod":    18,
    "Leviathan Rod":   25,
    "Abyss King Rod":  35,
}
_ROD_SPEED_BONUS: dict[str, int] = {
    "Driftwood Rod":   0,
    "Bamboo Tide Rod": 1,
    "Copperline Rod":  2,
    "Sailor's Rod":    2,
    "Deepwater Rod":   3,
    "Stormcast Rod":   3,
    "Moonhook Rod":    4,
    "Leviathan Rod":   5,
    "Abyss King Rod":  6,
}
_ROD_DURATION_BONUS: dict[str, int] = {
    "Driftwood Rod":   0,
    "Bamboo Tide Rod": 1,
    "Copperline Rod":  2,
    "Sailor's Rod":    3,
    "Deepwater Rod":   4,
    "Stormcast Rod":   5,
    "Moonhook Rod":    6,
    "Leviathan Rod":   8,
    "Abyss King Rod":  10,
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _get_am_s(key: str, default: str) -> str:
    try:
        v = db.get_auto_activity_setting(key)
        return v if v else default
    except Exception:
        return default


def _get_af_s(key: str, default: str) -> str:
    try:
        v = db.get_auto_activity_setting(key)
        return v if v else default
    except Exception:
        return default


def _sum_active_player_boosts(uid: str, system: str, boost_type: str) -> int:
    try:
        boosts = db.get_active_player_boosts(uid, system)
        return sum(int(b["amount"]) for b in boosts if b["boost_type"] == boost_type)
    except Exception:
        return 0


def _sum_active_room_boosts(system: str, boost_type: str) -> int:
    try:
        boosts = db.get_active_room_boosts(system)
        return sum(int(b["amount"]) for b in boosts if b["boost_type"] == boost_type)
    except Exception:
        return 0


def _get_event_luck(system: str) -> int:
    """Return event luck points for 'mining' or 'fishing'."""
    try:
        from modules.events import get_event_effect as _gee
        eff = _gee()
        if system == "mining":
            el = eff.get("mining_luck_boost", 0.0)
        else:
            el = eff.get("fish_luck_boost", 0.0)
        return max(1, int(el * 10)) if el > 0 else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_mine_luck_stack(uid: str, uname: str) -> dict:
    """Calculate the full mining luck/speed/duration stack for a player."""
    base_dur  = int(_get_am_s("mine_base_duration",  "5"))
    base_intv = int(_get_am_s("mine_base_interval",  "12"))
    base_luck = int(_get_am_s("mine_base_luck",      "1"))
    vip_luck  = int(_get_am_s("mine_vip_luck",       "2"))
    vip_dur   = int(_get_am_s("mine_vip_duration",   "10"))
    vip_speed = int(_get_am_s("mine_vip_speed",      "1"))
    min_intv  = int(_get_am_s("mine_min_interval",   "5"))

    tool_lvl = 1
    try:
        miner = db.get_or_create_miner(uname)
        tool_lvl = miner.get("tool_level", 1)
    except Exception:
        pass

    tool_luck = _TOOL_LUCK_PTS.get(tool_lvl, 1)
    tool_spd  = _TOOL_SPEED_BONUS.get(tool_lvl, 0)
    tool_dur  = _TOOL_DURATION_BONUS.get(tool_lvl, 0)

    is_vip = False
    try:
        is_vip = bool(db.owns_item(uid, "vip"))
    except Exception:
        pass

    vip_luck_v = vip_luck if is_vip else 0
    vip_dur_v  = vip_dur  if is_vip else 0
    vip_spd_v  = vip_speed if is_vip else 0

    potion_luck = _sum_active_player_boosts(uid, "mining", "luck")
    room_luck   = _sum_active_room_boosts("mining", "luck")
    room_spd    = _sum_active_room_boosts("mining", "speed")
    event_luck  = _get_event_luck("mining")

    total_luck = base_luck + tool_luck + potion_luck + vip_luck_v + room_luck + event_luck
    total_spd  = tool_spd + vip_spd_v + room_spd
    total_dur  = base_dur + vip_dur_v + tool_dur
    interval   = max(min_intv, base_intv - total_spd)
    total_atts = max(1, int((total_dur * 60) / interval))

    return {
        "base_luck":    base_luck,
        "tool_luck":    tool_luck,
        "potion_luck":  potion_luck,
        "vip_luck":     vip_luck_v,
        "room_luck":    room_luck,
        "event_luck":   event_luck,
        "luck_total":   total_luck,
        "duration_mins": total_dur,
        "interval_secs": interval,
        "total_attempts": total_atts,
        "is_vip":       is_vip,
        "tool_lvl":     tool_lvl,
        "min_interval": min_intv,
        "base_interval": base_intv,
    }


def get_fish_luck_stack(uid: str, uname: str) -> dict:
    """Calculate the full fishing luck/speed/duration stack for a player."""
    base_dur  = int(_get_af_s("fish_base_duration",  "5"))
    base_intv = int(_get_af_s("fish_base_interval",  "12"))
    base_luck = int(_get_af_s("fish_base_luck",      "1"))
    vip_luck  = int(_get_af_s("fish_vip_luck",       "2"))
    vip_dur   = int(_get_af_s("fish_vip_duration",   "10"))
    vip_speed = int(_get_af_s("fish_vip_speed",      "1"))
    min_intv  = int(_get_af_s("fish_min_interval",   "5"))

    rod_name = "Driftwood Rod"
    try:
        profile  = db.get_or_create_fish_profile(uid, uname)
        rod_name = profile.get("equipped_rod") or "Driftwood Rod"
    except Exception:
        pass

    rod_luck = _ROD_LUCK_PTS.get(rod_name, 1)
    rod_spd  = _ROD_SPEED_BONUS.get(rod_name, 0)
    rod_dur  = _ROD_DURATION_BONUS.get(rod_name, 0)

    is_vip = False
    try:
        is_vip = bool(db.owns_item(uid, "vip"))
    except Exception:
        pass

    vip_luck_v = vip_luck if is_vip else 0
    vip_dur_v  = vip_dur  if is_vip else 0
    vip_spd_v  = vip_speed if is_vip else 0

    bait_luck   = 0  # placeholder for future bait system
    potion_luck = _sum_active_player_boosts(uid, "fishing", "luck")
    room_luck   = _sum_active_room_boosts("fishing", "luck")
    room_spd    = _sum_active_room_boosts("fishing", "speed")
    event_luck  = _get_event_luck("fishing")

    total_luck = base_luck + rod_luck + bait_luck + potion_luck + vip_luck_v + room_luck + event_luck
    total_spd  = rod_spd + vip_spd_v + room_spd
    total_dur  = base_dur + vip_dur_v + rod_dur
    interval   = max(min_intv, base_intv - total_spd)
    total_atts = max(1, int((total_dur * 60) / interval))

    return {
        "base_luck":     base_luck,
        "rod_luck":      rod_luck,
        "bait_luck":     bait_luck,
        "potion_luck":   potion_luck,
        "vip_luck":      vip_luck_v,
        "room_luck":     room_luck,
        "event_luck":    event_luck,
        "luck_total":    total_luck,
        "duration_mins": total_dur,
        "interval_secs": interval,
        "total_attempts": total_atts,
        "is_vip":        is_vip,
        "rod_name":      rod_name,
        "min_interval":  min_intv,
        "base_interval": base_intv,
    }
