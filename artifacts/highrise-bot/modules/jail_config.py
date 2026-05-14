"""modules/jail_config.py — Room settings for the Luxe Jail system (3.4A)."""
import database as db

_DEFAULTS: dict[str, str] = {
    "jail_enabled":                "1",
    "jail_cost_per_minute":        "2",
    "jail_min_minutes":            "1",
    "jail_max_minutes":            "10",
    "jail_default_minutes":        "3",
    "jail_bail_multiplier":        "2",
    "jail_cooldown_seconds":       "300",
    "jail_daily_limit_per_player": "5",
    "jail_protect_staff":          "1",
    "jail_allow_staff_jail_staff": "0",
    "jail_allow_owner_override":   "1",
    "jail_confirm_required":       "1",
    "jail_prevent_teleport":       "1",
    "jail_rejoin_enforce":         "1",
    "jail_location_name":          "jail",
    "securitybot_jail_brief_spawn":"jail_guard",
    "securitybot_idle_spawn":      "security_idle",
}


def _get(key: str) -> str:
    return db.get_room_setting(key, _DEFAULTS.get(key, ""))


def _set(key: str, value: str) -> None:
    db.set_room_setting(key, value)


def is_jail_enabled() -> bool:
    return _get("jail_enabled") == "1"

def cost_per_minute() -> int:
    try:
        return max(1, int(_get("jail_cost_per_minute")))
    except ValueError:
        return 2

def min_minutes() -> int:
    try:
        return max(1, int(_get("jail_min_minutes")))
    except ValueError:
        return 1

def max_minutes() -> int:
    try:
        return max(1, int(_get("jail_max_minutes")))
    except ValueError:
        return 10

def default_minutes() -> int:
    try:
        return max(1, int(_get("jail_default_minutes")))
    except ValueError:
        return 3

def bail_multiplier() -> int:
    try:
        return max(1, int(_get("jail_bail_multiplier")))
    except ValueError:
        return 2

def cooldown_seconds() -> int:
    try:
        return max(0, int(_get("jail_cooldown_seconds")))
    except ValueError:
        return 300

def daily_limit() -> int:
    try:
        return max(1, int(_get("jail_daily_limit_per_player")))
    except ValueError:
        return 5

def protect_staff() -> bool:
    return _get("jail_protect_staff") == "1"

def allow_owner_override() -> bool:
    return _get("jail_allow_owner_override") == "1"

def confirm_required() -> bool:
    return _get("jail_confirm_required") == "1"

def rejoin_enforce() -> bool:
    return _get("jail_rejoin_enforce") == "1"

def jail_spot_name() -> str:
    return _get("jail_location_name") or "jail"

def guard_spot_name() -> str:
    return _get("securitybot_jail_brief_spawn") or "jail_guard"

def idle_spot_name() -> str:
    return _get("securitybot_idle_spawn") or "security_idle"

def release_spot_name() -> str:
    return _get("jail_release_spawn") or "jail_release"


def set_jail_enabled(val: bool) -> None:
    _set("jail_enabled", "1" if val else "0")

def set_cost_per_minute(val: int) -> None:
    _set("jail_cost_per_minute", str(max(1, val)))

def set_max_minutes(val: int) -> None:
    _set("jail_max_minutes", str(max(1, val)))

def set_min_minutes(val: int) -> None:
    _set("jail_min_minutes", str(max(1, val)))

def set_bail_multiplier(val: int) -> None:
    _set("jail_bail_multiplier", str(max(1, val)))

def set_protect_staff(val: bool) -> None:
    _set("jail_protect_staff", "1" if val else "0")

def set_confirm_required(val: bool) -> None:
    _set("jail_confirm_required", "1" if val else "0")
