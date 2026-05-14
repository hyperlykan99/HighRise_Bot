"""
modules/ai_time_holidays.py — Philippines time and holiday helpers (3.3A rebuild).

Default timezone: Asia/Manila (UTC+8).
Holiday list covers 2025-2026. Extend _PH_HOLIDAYS as needed.
"""
from __future__ import annotations

import datetime

PH_TZ = datetime.timezone(datetime.timedelta(hours=8))

_PH_HOLIDAYS: list[tuple[datetime.date, str]] = [
    # 2025
    (datetime.date(2025,  1,  1), "New Year's Day"),
    (datetime.date(2025,  4, 17), "Maundy Thursday"),
    (datetime.date(2025,  4, 18), "Good Friday"),
    (datetime.date(2025,  4, 19), "Black Saturday"),
    (datetime.date(2025,  4,  9), "Day of Valor (Araw ng Kagitingan)"),
    (datetime.date(2025,  5,  1), "Labor Day"),
    (datetime.date(2025,  6, 12), "Independence Day"),
    (datetime.date(2025,  8, 25), "National Heroes Day"),
    (datetime.date(2025, 11,  1), "All Saints' Day"),
    (datetime.date(2025, 11, 30), "Bonifacio Day"),
    (datetime.date(2025, 12,  8), "Immaculate Conception Day"),
    (datetime.date(2025, 12, 25), "Christmas Day"),
    (datetime.date(2025, 12, 30), "Rizal Day"),
    (datetime.date(2025, 12, 31), "New Year's Eve (special)"),
    # 2026
    (datetime.date(2026,  1,  1), "New Year's Day"),
    (datetime.date(2026,  4,  2), "Maundy Thursday"),
    (datetime.date(2026,  4,  3), "Good Friday"),
    (datetime.date(2026,  4,  4), "Black Saturday"),
    (datetime.date(2026,  4,  9), "Day of Valor (Araw ng Kagitingan)"),
    (datetime.date(2026,  5,  1), "Labor Day"),
    (datetime.date(2026,  6, 12), "Independence Day"),
    (datetime.date(2026,  8, 24), "National Heroes Day"),
    (datetime.date(2026, 11,  1), "All Saints' Day"),
    (datetime.date(2026, 11, 30), "Bonifacio Day"),
    (datetime.date(2026, 12,  8), "Immaculate Conception Day"),
    (datetime.date(2026, 12, 25), "Christmas Day"),
    (datetime.date(2026, 12, 30), "Rizal Day"),
    (datetime.date(2026, 12, 31), "New Year's Eve (special)"),
]


def now_ph() -> datetime.datetime:
    return datetime.datetime.now(PH_TZ)


def today_ph() -> datetime.date:
    return now_ph().date()


def get_date_reply() -> str:
    n = now_ph()
    day_name = n.strftime("%A")
    date_str = n.strftime("%B %d, %Y")
    time_str = n.strftime("%I:%M %p")
    return f"📅 {day_name}, {date_str}\n🕐 {time_str} (Philippines)"


def get_time_reply() -> str:
    n = now_ph()
    return f"🕐 It's {n.strftime('%I:%M %p')} in the Philippines ({n.strftime('%A')})."


def get_next_holiday_reply() -> str:
    today = today_ph()
    upcoming = sorted(
        [(d, name) for d, name in _PH_HOLIDAYS if d >= today],
        key=lambda x: x[0],
    )
    if not upcoming:
        return "📅 No more Philippine holidays in my list for this year."
    d, name = upcoming[0]
    days_away = (d - today).days
    if days_away == 0:
        return f"🎉 Today is {name} in the Philippines! Happy holiday!"
    if days_away == 1:
        return f"📅 Tomorrow is {name}!"
    return f"📅 Next PH holiday: {name}\n{d.strftime('%B %d, %Y')} ({days_away} days away)"
