"""
modules/ai_global_holidays.py — Multi-country holiday data (3.3A).

Static holiday data for: Philippines, United States, Japan, United Kingdom,
Canada, Australia, Singapore, South Korea.

Holiday dates are fixed/calculated for 2025–2026.
Variable holidays (Eid, Easter-dependent, lunar) are noted as approximate.
Default country: Philippines.
"""
from __future__ import annotations

import datetime
import re

from modules.ai_location_parser import parse_country

# ── Holiday tables ─────────────────────────────────────────────────────────
# Format: (month, day, name, notes)  — year assumed current or next occurrence
# notes: "" = fixed, "~" = approximate/variable

_HOLIDAYS_BY_COUNTRY: dict[str, list[tuple[int, int, str, str]]] = {

    "ph": [  # Philippines
        (1,  1,  "New Year's Day", ""),
        (2,  25, "EDSA Revolution Anniversary", ""),
        (4,  2,  "Maundy Thursday", "~"),
        (4,  3,  "Good Friday", "~"),
        (4,  4,  "Black Saturday", "~"),
        (4,  9,  "Araw ng Kagitingan (Day of Valor)", ""),
        (5,  1,  "Labor Day", ""),
        (6,  12, "Independence Day", ""),
        (8,  21, "Ninoy Aquino Day", ""),
        (8,  25, "National Heroes Day", "~"),
        (11, 1,  "All Saints' Day", ""),
        (11, 2,  "All Souls' Day", ""),
        (11, 30, "Bonifacio Day", ""),
        (12, 8,  "Feast of the Immaculate Conception", ""),
        (12, 25, "Christmas Day", ""),
        (12, 30, "Rizal Day", ""),
        (12, 31, "New Year's Eve", ""),
    ],

    "us": [  # United States
        (1,  1,  "New Year's Day", ""),
        (1,  19, "Martin Luther King Jr. Day", "~"),  # 3rd Mon Jan
        (2,  16, "Presidents' Day", "~"),             # 3rd Mon Feb
        (4,  3,  "Good Friday", "~"),
        (5,  25, "Memorial Day", "~"),                # Last Mon May
        (6,  19, "Juneteenth National Independence Day", ""),
        (7,  4,  "Independence Day (4th of July)", ""),
        (9,  7,  "Labor Day", "~"),                   # 1st Mon Sep
        (10, 12, "Columbus Day", "~"),                # 2nd Mon Oct
        (11, 11, "Veterans Day", ""),
        (11, 26, "Thanksgiving Day", "~"),            # 4th Thu Nov
        (12, 25, "Christmas Day", ""),
    ],

    "jp": [  # Japan
        (1,  1,  "New Year's Day (Gantan-sai)", ""),
        (1,  12, "Coming-of-Age Day (Seijin no Hi)", "~"),  # 2nd Mon Jan
        (2,  11, "National Foundation Day (Kenkoku Kinen)", ""),
        (2,  17, "Chinese/Lunar New Year (informal)", "~"),
        (2,  23, "The Emperor's Birthday", ""),
        (3,  20, "Vernal Equinox Day", "~"),
        (4,  29, "Showa Day", ""),
        (5,  3,  "Constitution Day", ""),
        (5,  4,  "Greenery Day (Midori no Hi)", ""),
        (5,  5,  "Children's Day (Kodomo no Hi)", ""),
        (7,  20, "Marine Day (Umi no Hi)", "~"),       # 3rd Mon Jul
        (8,  11, "Mountain Day (Yama no Hi)", ""),
        (9,  21, "Respect for the Aged Day", "~"),     # 3rd Mon Sep
        (9,  23, "Autumnal Equinox Day", "~"),
        (10, 12, "Sports Day (Taiiku no Hi)", "~"),    # 2nd Mon Oct
        (11, 3,  "Culture Day (Bunka no Hi)", ""),
        (11, 23, "Labour Thanksgiving Day", ""),
        (12, 25, "Christmas (not a national holiday, widely observed)", "~"),
    ],

    "gb": [  # United Kingdom
        (1,  1,  "New Year's Day", ""),
        (4,  3,  "Good Friday", "~"),
        (4,  6,  "Easter Monday", "~"),
        (5,  4,  "Early May Bank Holiday", "~"),      # 1st Mon May
        (5,  25, "Spring Bank Holiday", "~"),          # Last Mon May
        (8,  31, "Summer Bank Holiday", "~"),          # Last Mon Aug
        (12, 25, "Christmas Day", ""),
        (12, 26, "Boxing Day", ""),
    ],

    "ca": [  # Canada
        (1,  1,  "New Year's Day", ""),
        (2,  16, "Family Day", "~"),                  # Varies by province
        (4,  3,  "Good Friday", "~"),
        (4,  6,  "Easter Monday", "~"),
        (5,  18, "Victoria Day", "~"),                # Mon before May 25
        (7,  1,  "Canada Day", ""),
        (9,  7,  "Labour Day", "~"),                  # 1st Mon Sep
        (10, 12, "Thanksgiving", "~"),                # 2nd Mon Oct
        (11, 11, "Remembrance Day", ""),
        (12, 25, "Christmas Day", ""),
        (12, 26, "Boxing Day", ""),
    ],

    "au": [  # Australia
        (1,  1,  "New Year's Day", ""),
        (1,  26, "Australia Day", ""),
        (4,  3,  "Good Friday", "~"),
        (4,  4,  "Easter Saturday", "~"),
        (4,  5,  "Easter Sunday", "~"),
        (4,  6,  "Easter Monday", "~"),
        (4,  25, "Anzac Day", ""),
        (6,  8,  "Queen's/King's Birthday", "~"),     # Varies by state
        (12, 25, "Christmas Day", ""),
        (12, 26, "Boxing Day", ""),
    ],

    "sg": [  # Singapore
        (1,  1,  "New Year's Day", ""),
        (1,  29, "Chinese New Year (Day 1)", "~"),
        (1,  30, "Chinese New Year (Day 2)", "~"),
        (3,  31, "Hari Raya Puasa (Eid al-Fitr)", "~"),
        (4,  3,  "Good Friday", "~"),
        (5,  1,  "Labour Day", ""),
        (5,  12, "Vesak Day", "~"),
        (6,  7,  "Hari Raya Haji (Eid al-Adha)", "~"),
        (8,  9,  "National Day", ""),
        (10, 20, "Deepavali", "~"),
        (12, 25, "Christmas Day", ""),
    ],

    "kr": [  # South Korea
        (1,  1,  "New Year's Day", ""),
        (2,  17, "Seollal (Lunar New Year, Day 1)", "~"),
        (2,  18, "Seollal (Lunar New Year, Day 2)", "~"),
        (2,  19, "Seollal (Lunar New Year, Day 3)", "~"),
        (3,  1,  "Independence Movement Day (Samiljeol)", ""),
        (5,  5,  "Children's Day", ""),
        (5,  12, "Buddha's Birthday (Seokga Tansinil)", "~"),
        (6,  6,  "Memorial Day (Hyeonchung-il)", ""),
        (8,  15, "Liberation Day (Gwangbokjeol)", ""),
        (9,  25, "Chuseok (Korean Thanksgiving, Day 1)", "~"),
        (9,  26, "Chuseok (Day 2)", "~"),
        (9,  27, "Chuseok (Day 3)", "~"),
        (10, 3,  "National Foundation Day (Gaecheon-jeol)", ""),
        (10, 9,  "Hangul Day", ""),
        (12, 25, "Christmas Day", ""),
    ],
}

# Country-code aliases for name lookup
_CC_ALIASES: dict[str, str] = {
    "philippines": "ph", "ph": "ph", "manila": "ph",
    "united states": "us", "usa": "us", "us": "us", "america": "us",
    "new york": "us", "california": "us",
    "japan": "jp", "tokyo": "jp", "osaka": "jp",
    "united kingdom": "gb", "uk": "gb", "england": "gb", "london": "gb",
    "canada": "ca", "toronto": "ca",
    "australia": "au", "sydney": "au",
    "singapore": "sg",
    "south korea": "kr", "korea": "kr", "seoul": "kr",
}

# Common holiday aliases for cross-country lookup
_HOLIDAY_SEARCH: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\bnew\s+year\b", re.I),        "New Year's Day", "all"),
    (re.compile(r"\bchristmas\b", re.I),          "Christmas Day", "all"),
    (re.compile(r"\bgood\s+friday\b", re.I),      "Good Friday", "all"),
    (re.compile(r"\beaster\b", re.I),             "Easter", "all"),
    (re.compile(r"\bthanksgiving\b", re.I),       "Thanksgiving", "us_ca"),
    (re.compile(r"\bindependence\s+day\b", re.I), "Independence Day", "ph_us"),
    (re.compile(r"\blabor\s+day\b", re.I),        "Labour Day", "all"),
    (re.compile(r"\bboxing\s+day\b", re.I),       "Boxing Day", "gb_ca_au"),
    (re.compile(r"\beid\b|hari\s+raya\b|iftar\b",re.I), "Eid", "sg_variable"),
    (re.compile(r"\bchinese\s+new\s+year\b|lunar\s+new\s+year\b", re.I), "Chinese New Year", "jp_sg_kr"),
    (re.compile(r"\bseollal\b", re.I),            "Seollal", "kr"),
    (re.compile(r"\bchuseok\b", re.I),            "Chuseok", "kr"),
    (re.compile(r"\bgolden\s+week\b", re.I),      "Golden Week", "jp"),
    (re.compile(r"\banzac\b", re.I),              "Anzac Day", "au"),
    (re.compile(r"\bcanada\s+day\b", re.I),       "Canada Day", "ca"),
    (re.compile(r"\baustralia\s+day\b", re.I),    "Australia Day", "au"),
    (re.compile(r"\bdeepa?vali\b|diwali\b", re.I),"Deepavali/Diwali", "sg_variable"),
    (re.compile(r"\bvesak\b|buddha'?s?\s+birthday\b", re.I), "Vesak Day", "sg_variable"),
    (re.compile(r"\bnational\s+day\b", re.I),     "National Day", "sg"),
    (re.compile(r"\brizal\b", re.I),              "Rizal Day", "ph"),
    (re.compile(r"\bbonifacio\b", re.I),          "Bonifacio Day", "ph"),
    (re.compile(r"\ball\s+saints\b", re.I),       "All Saints' Day", "ph"),
]

_VARIABLE_REPLY = (
    "📅 That holiday follows a lunar or religious calendar and the exact date "
    "may change each year. I don't have the live {year} calendar loaded."
)


def _cc_from_text(text: str) -> str:
    """Extract country code from text, default 'ph'."""
    low = text.lower()
    for alias, cc in sorted(_CC_ALIASES.items(), key=lambda x: -len(x[0])):
        if alias in low:
            return cc
    return "ph"


def _next_occurrence(month: int, day: int) -> tuple[int, int]:
    """Return (year, month) for the next occurrence of a fixed date."""
    today = datetime.date.today()
    year = today.year
    try:
        candidate = datetime.date(year, month, day)
    except ValueError:
        return year, month
    if candidate < today:
        year += 1
    return year, month


def _holiday_line(month: int, day: int, name: str, notes: str) -> str:
    year, _ = _next_occurrence(month, day)
    _MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    approx = " (approx.)" if notes == "~" else ""
    return f"• {name}: {_MONTHS[month]} {day}, {year}{approx}"


def get_country_holidays(cc: str, limit: int = 5) -> str:
    """Return a short list of upcoming holidays for a country."""
    holidays = _HOLIDAYS_BY_COUNTRY.get(cc)
    if not holidays:
        return "📅 I don't have holiday data for that country yet."

    today = datetime.date.today()
    upcoming = []
    for month, day, name, notes in holidays:
        year = today.year
        try:
            d = datetime.date(year, month, day)
        except ValueError:
            continue
        if d < today:
            try:
                d = datetime.date(year + 1, month, day)
            except ValueError:
                continue
        upcoming.append((d, month, day, name, notes))
    upcoming.sort(key=lambda x: x[0])

    if not upcoming:
        return "📅 No more holidays found for this year."

    _MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    lines = []
    for d, month, day, name, notes in upcoming[:limit]:
        approx = " ~" if notes == "~" else ""
        lines.append(f"• {name}: {_MONTHS[month]} {day}, {d.year}{approx}")

    country_names = {
        "ph": "Philippines", "us": "United States", "jp": "Japan",
        "gb": "United Kingdom", "ca": "Canada", "au": "Australia",
        "sg": "Singapore", "kr": "South Korea",
    }
    header = f"🗓️ Upcoming holidays ({country_names.get(cc, cc.upper())}):"
    return (header + "\n" + "\n".join(lines))[:249]


def get_global_holiday_reply(text: str) -> str:
    """Return holiday info for the country mentioned in text, or Philippines default."""
    low = text.lower()

    # Check for specific holiday search by name
    for pattern, holiday_name, country_hint in _HOLIDAY_SEARCH:
        if pattern.search(low):
            # Determine which country to use
            cc = _cc_from_text(text)

            # Special variable-date holidays
            if country_hint == "sg_variable":
                return (
                    f"📅 {holiday_name}: This holiday follows a lunar or religious "
                    "calendar. Exact date varies each year — check an up-to-date "
                    "calendar for the current year."
                )[:249]

            # Find the holiday in the country's data
            holidays = _HOLIDAYS_BY_COUNTRY.get(cc, [])
            for m, d, name, notes in holidays:
                if holiday_name.lower() in name.lower():
                    year_n, _ = _next_occurrence(m, d)
                    _MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                    approx = " (approximate)" if notes == "~" else ""
                    country_names = {
                        "ph": "Philippines", "us": "United States", "jp": "Japan",
                        "gb": "United Kingdom", "ca": "Canada", "au": "Australia",
                        "sg": "Singapore", "kr": "South Korea",
                    }
                    return (
                        f"📅 {name} in {country_names.get(cc, cc.upper())}: "
                        f"{_MONTHS[m]} {d}, {year_n}{approx}"
                    )[:249]

            # Holiday name not found in country data
            # Try a general note
            country_names = {
                "ph": "Philippines", "us": "US", "jp": "Japan",
                "gb": "UK", "ca": "Canada", "au": "Australia",
                "sg": "Singapore", "kr": "South Korea",
            }
            return (
                f"📅 {holiday_name} may not be an official public holiday in "
                f"{country_names.get(cc, cc.upper())}, or I may not have its date loaded."
            )[:249]

    # No specific holiday mentioned — show upcoming list for the country
    cc = _cc_from_text(text)
    return get_country_holidays(cc)
