"""
modules/ai_live_router.py — Live internet query orchestrator (3.3D).

Pipeline per live request:
  1. Safety check  — block dangerous queries
  2. Type detection — weather / exchange / crypto / news / sports / general
  3. Rate limit    — per-user + global window
  4. Cache check   — return cached answer if still fresh
  5. Source call   — free API or OpenAI web search
  6. Cache store   — save result
  7. Log           — debug output

Reply mode: weather/news/sports/general → respects SMART mode
            sensitive queries           → always whisper (caught by safety)
"""
from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import User

from modules.ai_live_safety import is_blocked_live_query
from modules.ai_live_cache  import get_cached, set_cached
from modules.ai_live_sources import (
    get_weather_answer, get_exchange_answer, get_crypto_answer,
)
from modules.ai_web_search import web_search_answer, has_openai_key

# ── Live type detection ───────────────────────────────────────────────────────

_WEATHER_PAT = re.compile(
    r"\b(weather|forecast|temperature|temp\s+in|how\s+hot|how\s+cold|is\s+it\s+raining"
    r"|rain(ing)?|sunny|cloudy|wind\s+speed|humidity)\b"
    r"|\b(typhoon|storm|cyclone|tornado)\b",
    re.I,
)
_EXCHANGE_PAT = re.compile(
    r"\b(exchange\s+rate|forex|currency|usd\s+(to|vs|in)\s+\w+"
    r"|\w+\s+(to|vs)\s+(usd|php|eur|jpy|gbp|krw|sgd|myr|thb|vnd|idr|inr|brl|cny|mxn)"
    r"|current\s+rate|money\s+(exchange|rate)|conversion\s+rate"
    r"|1\s+usd|1\s+dollar|how\s+much\s+is\s+\d+\s+(dollar|peso|eur))\b",
    re.I,
)
_CRYPTO_PAT = re.compile(
    r"\b(bitcoin|btc|ethereum|eth|solana|sol|bnb|xrp|ripple|cardano|ada"
    r"|dogecoin|doge|crypto(\s+price)?|coin\s+price|shiba|shib|pepe"
    r"|tron|trx|litecoin|ltc|polkadot|dot|tether|usdt|usdc)\b"
    r".*\b(price|worth|value|cost|rate|now|today)\b"
    r"|\b(price|value)\s+of\s+(bitcoin|btc|ethereum|eth|solana|sol|bnb|xrp)\b"
    r"|\b(btc|eth|sol|bnb|xrp|doge|ada|trx)\s+price\b",
    re.I,
)
_NEWS_PAT = re.compile(
    r"\b(latest\s+news|breaking\s+news|current\s+news|today.?s\s+news"
    r"|what.?s\s+happening\s+(in|at|with)|news\s+(in|about|on)\s+\w+"
    r"|latest\s+update\s+(for|on|in|about)|recent\s+news"
    r"|latest\s+(highrise|roblox|fortnite|minecraft)\s+update"
    r"|new\s+(update|patch|feature)\s+(for|in|on|released)"
    r"|current\s+(event|situation|issue)\s+(in|at)"
    r"|latest\s+(law|rule|policy|regulation)\s+(in|on)"
    r"|active\s+promo\s+codes?|promo\s+codes?\s+(for|in))\b",
    re.I,
)
_SPORTS_PAT = re.compile(
    r"\b(who\s+won|sports\s+score|game\s+score|basketball\s+score|football\s+score"
    r"|soccer\s+score|nba\s+(score|game|result)|nfl\s+(score|game|result)"
    r"|pba\s+(score|game|result)|uaap|ncaa\s+(game|score|result)"
    r"|world\s+cup\s+(score|result)|premier\s+league\s+(score|result)"
    r"|champions\s+league\s+(score|result)"
    r"|(lakers|warriors|celtics|nets|bulls|heat|spurs|thunder)\s+(game|score|result|today|won|lost)"
    r"|latest\s+game|yesterday.?s\s+game|today.?s\s+game"
    r"|standing[s]?\s+(in|for|of)\s+\w+)\b",
    re.I,
)
_LIVE_DETECT_PAT = re.compile(
    r"\b(weather|forecast|temperature|exchange\s+rate|forex|usd\s+to|bitcoin|btc\s+price"
    r"|ethereum|eth\s+price|solana|crypto\s+price|stock\s+price|latest\s+news"
    r"|breaking\s+news|current\s+news|sports\s+score|who\s+won|live\s+score"
    r"|current\s+president|prime\s+minister\s+of|promo\s+code"
    r"|latest\s+(highrise|roblox|update)|today.?s\s+(news|price|update)"
    r"|right\s+now\s+in|lotto\s+result|lottery\s+result|current\s+(rate|price|score)"
    r"|flight\s+price|movie\s+schedule|cinema\s+schedule"
    r"|current\s+law|active\s+promo|new\s+patch)\b",
    re.I,
)


def is_live_question(text: str) -> bool:
    return bool(_LIVE_DETECT_PAT.search(text))


def detect_live_type(text: str) -> str:
    if _WEATHER_PAT.search(text):
        return "weather"
    if _EXCHANGE_PAT.search(text):
        return "exchange"
    if _CRYPTO_PAT.search(text):
        return "crypto"
    if _SPORTS_PAT.search(text):
        return "sports"
    if _NEWS_PAT.search(text):
        return "news"
    return "general"


# ── Rate limiter ──────────────────────────────────────────────────────────────

_PERM_LIMITS = {
    0: (5,  600),   # player  — 5 per 10 min
    1: (8,  600),   # vip     — 8 per 10 min
    2: (15, 600),   # staff   — 15 per 10 min
    3: (20, 600),   # admin   — 20 per 10 min
    4: (30, 600),   # owner   — 30 per 10 min
}
_GLOBAL_LIMIT = (30, 600)   # 30 per 10 min room-wide

_user_windows:   dict[str, list[float]] = {}
_global_window:  list[float] = []


def _rate_check(user_id: str, perm: int) -> bool:
    """Return True if allowed, False if rate limited."""
    now = time.time()
    max_req, window = _PERM_LIMITS.get(perm, _PERM_LIMITS[0])

    # global window
    global _global_window
    _global_window = [t for t in _global_window if now - t < window]
    if len(_global_window) >= _GLOBAL_LIMIT[0]:
        return False
    _global_window.append(now)

    # per-user window
    hist = _user_windows.get(user_id, [])
    hist = [t for t in hist if now - t < window]
    if len(hist) >= max_req:
        _global_window.pop()
        return False
    hist.append(now)
    _user_windows[user_id] = hist
    return True


# ── Main entry point ──────────────────────────────────────────────────────────

_NO_OPENAI = (
    "🌐 Live internet is not connected yet. "
    "Set OPENAI_API_KEY in Replit Secrets to enable news/sports/general searches."
)
_RATE_LIMITED = "⏳ Live search is cooling down. Try again in a moment."


async def handle_live_question(user: "User", request: str, perm: int) -> str:
    """
    Main async handler for live/current questions.
    Returns a reply string ≤249 chars.
    """
    # 1. Safety check
    block = is_blocked_live_query(request)
    if block:
        print(f"[AI LIVE] BLOCKED request={request!r}")
        return block

    # 2. Detect type
    live_type = detect_live_type(request)
    print(f"[AI LIVE] request={request!r}")
    print(f"[AI LIVE] detected_type={live_type!r}")

    # 3. Rate limit
    if not _rate_check(user.id, perm):
        print(f"[AI LIVE] rate_limited user={user.id}")
        return _RATE_LIMITED

    # 4. Cache check
    cached = get_cached(live_type, request)
    print(f"[AI LIVE] cache_hit={cached is not None}")
    if cached:
        return cached

    # 5. Source dispatch
    answer = await _dispatch(live_type, request)

    # 6. Cache store (only real answers, not errors)
    if answer and not answer.startswith("⏱️") and not answer.startswith("🚫"):
        set_cached(live_type, request, answer)

    print(f"[AI LIVE] source={live_type!r} success={bool(answer)}")
    return (answer or "🌐 No answer available right now.")[:249]


async def _dispatch(live_type: str, request: str) -> str:
    if live_type == "weather":
        return await get_weather_answer(request)

    if live_type == "exchange":
        return await get_exchange_answer(request)

    if live_type == "crypto":
        return await get_crypto_answer(request)

    if live_type in ("news", "sports", "general"):
        if not has_openai_key():
            return _NO_OPENAI
        return await web_search_answer(request)

    return _NO_OPENAI
