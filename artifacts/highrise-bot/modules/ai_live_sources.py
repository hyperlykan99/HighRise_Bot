"""
modules/ai_live_sources.py — Live data source layer (3.3B).

Detects questions that need real-time internet data.
If no live API is connected, returns an honest "I can't guarantee
the latest answer" notice.

Future: hook live APIs (weather, exchange rates, news) here.
"""
from __future__ import annotations

import re

_LIVE_PATTERNS = re.compile(
    r"\b(weather|forecast|temperature\s+in"
    r"|latest\s+news|breaking\s+news|current\s+news"
    r"|live\s+score|who\s+won\s+the\s+game|current\s+score"
    r"|usd\s+(to|vs)\s+php|exchange\s+rate|forex\s+rate"
    r"|bitcoin\s+price|crypto\s+price|eth\s+price|stock\s+price"
    r"|promo\s+code|latest\s+update\s+for"
    r"|current\s+president|prime\s+minister\s+of\s+\w+"
    r"|flight\s+price|bus\s+schedule|train\s+schedule"
    r"|cinema\s+schedule|movie\s+schedule"
    r"|lotto\s+result|lottery\s+result"
    r"|today.s\s+(news|update|price)"
    r"|right\s+now\s+in\s+the\s+news)\b",
    re.I,
)

_LIVE_UNAVAILABLE = (
    "🌐 That needs live internet access, so I can't guarantee the latest answer "
    "from inside the room. Try checking a browser for real-time info."
)


def is_live_question(text: str) -> bool:
    """Return True if the question likely needs real-time data."""
    return bool(_LIVE_PATTERNS.search(text))


def get_live_unavailable_reply() -> str:
    """Standard reply when a live data source is needed but not connected."""
    return _LIVE_UNAVAILABLE


def get_live_answer(text: str) -> str | None:
    """
    Attempt to fetch a live answer.
    Currently no live APIs are connected — returns None to fall through.
    Wire in aiohttp-based API calls here when ready.
    """
    return None
