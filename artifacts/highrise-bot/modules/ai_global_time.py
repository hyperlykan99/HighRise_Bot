"""
modules/ai_global_time.py — Global date/time replies with timezone support (3.3A).

Uses stdlib zoneinfo (Python 3.9+) for accurate DST-aware conversions.
Falls back to fixed UTC offsets if timezone DB is unavailable.

Default: Philippines (Asia/Manila, UTC+8, no DST).
"""
from __future__ import annotations

import datetime

from modules.ai_location_parser import parse_location

_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday", "Sunday"]
_MONTHS   = ["", "January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]

_DEFAULT_TZ      = "Asia/Manila"
_DEFAULT_DISPLAY = "Philippines"

# Approximate fixed UTC offsets (fallback only — does not account for DST)
_FIXED_OFFSETS: dict[str, float] = {
    "Asia/Manila":          8,
    "Asia/Tokyo":           9,
    "Asia/Seoul":           9,
    "Asia/Singapore":       8,
    "Asia/Dubai":           4,
    "Asia/Kolkata":         5.5,
    "Asia/Shanghai":        8,
    "Asia/Jakarta":         7,
    "Asia/Makassar":        8,
    "Asia/Bangkok":         7,
    "Asia/Ho_Chi_Minh":     7,
    "Asia/Kuala_Lumpur":    8,
    "Asia/Riyadh":          3,
    "Asia/Jerusalem":       2,
    "Europe/London":        0,
    "Europe/Paris":         1,
    "Europe/Berlin":        1,
    "Europe/Madrid":        1,
    "Europe/Rome":          1,
    "Europe/Amsterdam":     1,
    "Europe/Moscow":        3,
    "Europe/Istanbul":      3,
    "America/New_York":    -5,
    "America/Chicago":     -6,
    "America/Denver":      -7,
    "America/Los_Angeles": -8,
    "America/Toronto":     -5,
    "America/Vancouver":   -8,
    "America/Sao_Paulo":   -3,
    "America/Mexico_City": -6,
    "Australia/Sydney":    10,
    "Australia/Melbourne": 10,
    "Australia/Brisbane":  10,
    "Australia/Perth":      8,
    "Pacific/Auckland":    12,
    "Africa/Cairo":         2,
    "Africa/Johannesburg":  2,
    "Africa/Lagos":         1,
    "Africa/Nairobi":       3,
}


def _get_dt(tz_name: str) -> datetime.datetime | None:
    """Return current datetime in the given timezone. Uses zoneinfo or fixed offset fallback."""
    # Try zoneinfo first
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            return datetime.datetime.now(tz=ZoneInfo(tz_name))
        except (ZoneInfoNotFoundError, Exception):
            pass
    except ImportError:
        pass

    # Fixed offset fallback
    offset_h = _FIXED_OFFSETS.get(tz_name)
    if offset_h is None:
        return None
    utc = datetime.datetime.utcnow()
    return utc + datetime.timedelta(hours=offset_h)


def _fmt(dt: datetime.datetime, display: str, dst_note: bool) -> str:
    wd  = _WEEKDAYS[dt.weekday()]
    mon = _MONTHS[dt.month]
    h, m = dt.hour, dt.minute
    am_pm = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    time_s = f"{h12}:{m:02d} {am_pm}"
    msg = f"🕐 {display}: {wd}, {mon} {dt.day}, {dt.year} — {time_s}"
    if dst_note:
        msg += " (±1h DST possible)"
    return msg[:249]


def get_global_time_reply(text: str, for_date: bool = False) -> str:
    """Return a date/time reply for the location mentioned in text, or Philippines default."""
    tz_name, display, cc = parse_location(text)
    if tz_name is None:
        tz_name, display = _DEFAULT_TZ, _DEFAULT_DISPLAY

    # Try zoneinfo (accurate DST)
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            dt = datetime.datetime.now(tz=ZoneInfo(tz_name))
            return _fmt(dt, display, dst_note=False)
        except (ZoneInfoNotFoundError, Exception):
            pass
    except ImportError:
        pass

    # Fixed offset fallback
    dt = _get_dt(tz_name)
    if dt is None:
        return f"⏰ I don't have timezone data for {display or 'that location'} right now."
    return _fmt(dt, display, dst_note=True)


def get_philippines_time_reply() -> str:
    """Return current time in Philippines (default)."""
    return get_global_time_reply("philippines")


def clarify_location() -> str:
    return "Which country or city do you mean? 🌍"
