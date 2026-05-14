"""
modules/ai_location_parser.py — Location/timezone keyword parser (3.3A).

Maps user-mentioned location names to IANA timezone strings and display names.
Order matters — longer/more-specific keywords must come before shorter ones.
"""
from __future__ import annotations

# (keyword_substring, iana_tz, display_name, country_code)
_LOCATIONS: list[tuple[str, str, str, str]] = [
    # Philippines (also default, but listed explicitly)
    ("philippines",         "Asia/Manila",          "Philippines",              "ph"),
    (" ph ",                "Asia/Manila",          "Philippines",              "ph"),
    ("manila",              "Asia/Manila",          "Manila, Philippines",      "ph"),
    ("cebu",                "Asia/Manila",          "Cebu, Philippines",        "ph"),
    ("davao",               "Asia/Manila",          "Davao, Philippines",       "ph"),
    # Japan
    ("japan",               "Asia/Tokyo",           "Japan",                    "jp"),
    ("tokyo",               "Asia/Tokyo",           "Tokyo, Japan",             "jp"),
    ("osaka",               "Asia/Tokyo",           "Osaka, Japan",             "jp"),
    ("kyoto",               "Asia/Tokyo",           "Kyoto, Japan",             "jp"),
    # South Korea
    ("south korea",         "Asia/Seoul",           "South Korea",              "kr"),
    ("korea",               "Asia/Seoul",           "South Korea",              "kr"),
    ("seoul",               "Asia/Seoul",           "Seoul, South Korea",       "kr"),
    # Singapore
    ("singapore",           "Asia/Singapore",       "Singapore",                "sg"),
    # UAE / Dubai
    ("united arab emirates","Asia/Dubai",           "UAE",                      "ae"),
    ("uae",                 "Asia/Dubai",           "UAE",                      "ae"),
    ("dubai",               "Asia/Dubai",           "Dubai, UAE",               "ae"),
    # India
    ("india",               "Asia/Kolkata",         "India",                    "in"),
    ("mumbai",              "Asia/Kolkata",         "Mumbai, India",            "in"),
    ("delhi",               "Asia/Kolkata",         "Delhi, India",             "in"),
    ("new delhi",           "Asia/Kolkata",         "New Delhi, India",         "in"),
    ("bangalore",           "Asia/Kolkata",         "Bangalore, India",         "in"),
    # China
    ("china",               "Asia/Shanghai",        "China",                    "cn"),
    ("beijing",             "Asia/Shanghai",        "Beijing, China",           "cn"),
    ("shanghai",            "Asia/Shanghai",        "Shanghai, China",          "cn"),
    # Indonesia
    ("indonesia",           "Asia/Jakarta",         "Indonesia",                "id"),
    ("jakarta",             "Asia/Jakarta",         "Jakarta, Indonesia",       "id"),
    ("bali",                "Asia/Makassar",        "Bali, Indonesia",          "id"),
    # Thailand
    ("thailand",            "Asia/Bangkok",         "Thailand",                 "th"),
    ("bangkok",             "Asia/Bangkok",         "Bangkok, Thailand",        "th"),
    # Vietnam
    ("vietnam",             "Asia/Ho_Chi_Minh",     "Vietnam",                  "vn"),
    ("ho chi minh",         "Asia/Ho_Chi_Minh",     "Ho Chi Minh City, VN",     "vn"),
    ("hanoi",               "Asia/Ho_Chi_Minh",     "Hanoi, Vietnam",           "vn"),
    # Malaysia
    ("malaysia",            "Asia/Kuala_Lumpur",    "Malaysia",                 "my"),
    ("kuala lumpur",        "Asia/Kuala_Lumpur",    "Kuala Lumpur, Malaysia",   "my"),
    # Australia
    ("australia",           "Australia/Sydney",     "Australia (Sydney)",       "au"),
    ("sydney",              "Australia/Sydney",     "Sydney, Australia",        "au"),
    ("melbourne",           "Australia/Melbourne",  "Melbourne, Australia",     "au"),
    ("brisbane",            "Australia/Brisbane",   "Brisbane, Australia",      "au"),
    ("perth",               "Australia/Perth",      "Perth, Australia",         "au"),
    # New Zealand
    ("new zealand",         "Pacific/Auckland",     "New Zealand",              "nz"),
    ("auckland",            "Pacific/Auckland",     "Auckland, New Zealand",    "nz"),
    # UK / London
    ("united kingdom",      "Europe/London",        "United Kingdom",           "gb"),
    ("england",             "Europe/London",        "England, UK",              "gb"),
    (" uk ",                "Europe/London",        "United Kingdom",           "gb"),
    ("london",              "Europe/London",        "London, UK",               "gb"),
    # France
    ("france",              "Europe/Paris",         "France",                   "fr"),
    ("paris",               "Europe/Paris",         "Paris, France",            "fr"),
    # Germany
    ("germany",             "Europe/Berlin",        "Germany",                  "de"),
    ("berlin",              "Europe/Berlin",        "Berlin, Germany",          "de"),
    # Spain
    ("spain",               "Europe/Madrid",        "Spain",                    "es"),
    ("madrid",              "Europe/Madrid",        "Madrid, Spain",            "es"),
    # Italy
    ("italy",               "Europe/Rome",          "Italy",                    "it"),
    ("rome",                "Europe/Rome",          "Rome, Italy",              "it"),
    # Netherlands
    ("netherlands",         "Europe/Amsterdam",     "Netherlands",              "nl"),
    ("amsterdam",           "Europe/Amsterdam",     "Amsterdam, Netherlands",   "nl"),
    # Russia
    ("russia",              "Europe/Moscow",        "Russia (Moscow)",          "ru"),
    ("moscow",              "Europe/Moscow",        "Moscow, Russia",           "ru"),
    # Turkey
    ("turkey",              "Europe/Istanbul",      "Turkey",                   "tr"),
    ("istanbul",            "Europe/Istanbul",      "Istanbul, Turkey",         "tr"),
    # Canada
    ("canada",              "America/Toronto",      "Canada (Eastern)",         "ca"),
    ("toronto",             "America/Toronto",      "Toronto, Canada",          "ca"),
    ("vancouver",           "America/Vancouver",    "Vancouver, Canada",        "ca"),
    ("montreal",            "America/Toronto",      "Montreal, Canada",         "ca"),
    # USA — Eastern
    ("new york city",       "America/New_York",     "New York City, USA",       "us"),
    ("new york",            "America/New_York",     "New York, USA",            "us"),
    (" nyc ",               "America/New_York",     "NYC, USA",                 "us"),
    ("washington",          "America/New_York",     "Washington D.C., USA",     "us"),
    ("miami",               "America/New_York",     "Miami, USA",               "us"),
    ("chicago",             "America/Chicago",      "Chicago, USA",             "us"),
    ("texas",               "America/Chicago",      "Texas, USA",               "us"),
    ("houston",             "America/Chicago",      "Houston, USA",             "us"),
    ("dallas",              "America/Chicago",      "Dallas, USA",              "us"),
    # USA — Mountain
    ("denver",              "America/Denver",       "Denver, USA",              "us"),
    # USA — Pacific
    ("san francisco",       "America/Los_Angeles",  "San Francisco, USA",       "us"),
    ("los angeles",         "America/Los_Angeles",  "Los Angeles, USA",         "us"),
    ("california",          "America/Los_Angeles",  "California, USA",          "us"),
    ("seattle",             "America/Los_Angeles",  "Seattle, USA",             "us"),
    ("portland",            "America/Los_Angeles",  "Portland, USA",            "us"),
    # USA generic — Eastern default
    ("united states",       "America/New_York",     "USA (Eastern)",            "us"),
    (" usa ",               "America/New_York",     "USA (Eastern)",            "us"),
    # Brazil
    ("brazil",              "America/Sao_Paulo",    "Brazil",                   "br"),
    ("sao paulo",           "America/Sao_Paulo",    "São Paulo, Brazil",        "br"),
    # Mexico
    ("mexico",              "America/Mexico_City",  "Mexico",                   "mx"),
    ("mexico city",         "America/Mexico_City",  "Mexico City, Mexico",      "mx"),
    # Middle East
    ("saudi arabia",        "Asia/Riyadh",          "Saudi Arabia",             "sa"),
    ("riyadh",              "Asia/Riyadh",          "Riyadh, Saudi Arabia",     "sa"),
    ("israel",              "Asia/Jerusalem",       "Israel",                   "il"),
    ("egypt",               "Africa/Cairo",         "Egypt",                    "eg"),
    ("cairo",               "Africa/Cairo",         "Cairo, Egypt",             "eg"),
    # Africa
    ("south africa",        "Africa/Johannesburg",  "South Africa",             "za"),
    ("johannesburg",        "Africa/Johannesburg",  "Johannesburg, South Africa","za"),
    ("nigeria",             "Africa/Lagos",         "Nigeria",                  "ng"),
    ("kenya",               "Africa/Nairobi",       "Kenya",                    "ke"),
    ("nairobi",             "Africa/Nairobi",       "Nairobi, Kenya",           "ke"),
]


def parse_location(text: str) -> tuple[str, str, str] | tuple[None, None, None]:
    """
    Return (iana_tz, display_name, country_code) for first location found in text.
    Returns (None, None, None) if no location found.

    Pads text with spaces so keyword boundaries like " uk " work correctly.
    """
    low = " " + text.lower() + " "
    for keyword, tz, display, cc in _LOCATIONS:
        if keyword in low:
            return tz, display, cc
    return None, None, None


def parse_country(text: str) -> tuple[str, str] | tuple[None, None]:
    """
    Return (display_name, country_code) only, or (None, None) if not found.
    Used by holiday lookup (doesn't need timezone).
    """
    _, display, cc = parse_location(text)
    return (display, cc) if cc else (None, None)
