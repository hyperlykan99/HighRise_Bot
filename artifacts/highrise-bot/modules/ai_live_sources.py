"""
modules/ai_live_sources.py — Free-API live data sources (3.3D).

Sources (no API key required):
  Weather  : wttr.in        — current conditions for any city
  Exchange : frankfurter.app — major currency pairs
  Crypto   : coingecko.com  — BTC, ETH, SOL, BNB, XRP prices

News, sports, and general web queries fall back to
ai_web_search.py (OpenAI web_search_preview).

All functions are async and return str ≤249 chars.
"""
from __future__ import annotations

import re

import aiohttp

_TIMEOUT = aiohttp.ClientTimeout(total=8)

# ── City extraction ───────────────────────────────────────────────────────────
_CITY_PATS = [
    re.compile(r"\bin\s+([a-zA-Z][a-zA-Z\s]{1,28?)(?:\s+today|\s+now|\s+right\s+now)?", re.I),
    re.compile(r"(?:weather|forecast|temperature)\s+(?:in\s+|for\s+)?([a-zA-Z][a-zA-Z\s]{1,28})", re.I),
    re.compile(r"([a-zA-Z][a-zA-Z\s]{1,24})\s+weather", re.I),
]
_SKIP_WORDS = {"the", "a", "an", "my", "some", "today", "now", "for", "of"}
# Words that trail behind a real city name and should be stripped
_TRAIL_WORDS = re.compile(
    r"\s+(today|now|right\s+now|tonight|this\s+week|tomorrow|at\s+night|this\s+morning|forecast)$",
    re.I,
)


def _extract_city(text: str) -> str:
    for pat in _CITY_PATS:
        m = pat.search(text)
        if m:
            city = m.group(1).strip().rstrip("?.,!").strip()
            city = _TRAIL_WORDS.sub("", city).strip()
            if city.lower() not in _SKIP_WORDS and len(city) >= 2:
                return city
    return "Manila"


# ── Currency extraction ───────────────────────────────────────────────────────
_CUR_MAP = {
    "php": "PHP", "peso": "PHP", "pesos": "PHP",
    "usd": "USD", "dollar": "USD", "dollars": "USD",
    "eur": "EUR", "euro": "EUR", "euros": "EUR",
    "jpy": "JPY", "yen": "JPY",
    "krw": "KRW", "won": "KRW",
    "gbp": "GBP", "pound": "GBP", "pounds": "GBP",
    "aud": "AUD", "cad": "CAD", "sgd": "SGD",
    "myr": "MYR", "ringgit": "MYR",
    "thb": "THB", "baht": "THB",
    "vnd": "VND", "idr": "IDR", "inr": "INR", "rupee": "INR",
    "brl": "BRL", "mxn": "MXN", "cny": "CNY", "yuan": "CNY", "rmb": "CNY",
}
_FROM_PAT = re.compile(
    r"\b(usd|eur|gbp|jpy|krw|php|aud|cad|sgd|myr|thb|vnd|idr|inr|brl|cny|mxn)\b",
    re.I,
)
_TO_PAT = re.compile(
    r"(?:to|in)\s+(usd|eur|gbp|jpy|krw|php|aud|cad|sgd|myr|thb|vnd|idr|inr|brl|cny|mxn"
    r"|peso|dollar|euro|yen|won|pound|rupee|baht|yuan|ringgit)\b",
    re.I,
)


def _extract_currencies(text: str) -> tuple[str, str]:
    low = text.lower()
    from_cur, to_cur = "USD", "PHP"
    frm = _FROM_PAT.search(low)
    if frm:
        from_cur = frm.group(1).upper()
    tom = _TO_PAT.search(low)
    if tom:
        raw = tom.group(1).strip().lower()
        to_cur = _CUR_MAP.get(raw, raw.upper())
    if from_cur == to_cur:
        to_cur = "PHP" if from_cur != "PHP" else "USD"
    return from_cur, to_cur


# ── Crypto coin extraction ────────────────────────────────────────────────────
_COIN_IDS: dict[str, str] = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum",
    "solana": "solana", "sol": "solana",
    "bnb": "binancecoin", "binance": "binancecoin",
    "xrp": "ripple", "ripple": "ripple",
    "cardano": "cardano", "ada": "cardano",
    "dogecoin": "dogecoin", "doge": "dogecoin",
    "tron": "tron", "trx": "tron",
    "litecoin": "litecoin", "ltc": "litecoin",
    "polkadot": "polkadot", "dot": "polkadot",
    "usdt": "tether", "tether": "tether",
    "usdc": "usd-coin",
    "shiba": "shiba-inu", "shib": "shiba-inu",
    "pepe": "pepe",
}
_COIN_PAT = re.compile(
    r"\b(bitcoin|btc|ethereum|eth|solana|sol|bnb|binance|xrp|ripple"
    r"|cardano|ada|dogecoin|doge|tron|trx|litecoin|ltc|polkadot|dot"
    r"|usdt|tether|usdc|shiba|shib|pepe)\b",
    re.I,
)


def _extract_coin(text: str) -> str:
    m = _COIN_PAT.search(text.lower())
    if m:
        return _COIN_IDS.get(m.group(1), "bitcoin")
    return "bitcoin"


# ── Weather ───────────────────────────────────────────────────────────────────

async def get_weather_answer(query: str) -> str:
    city = _extract_city(query)
    url = f"https://wttr.in/{city.replace(' ', '+')}?format=j1"
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as sess:
            async with sess.get(url, headers={"User-Agent": "ChillTopia-Bot/3.3D"}) as r:
                if r.status != 200:
                    return f"⛅ Couldn't fetch weather for {city} right now."
                data = await r.json(content_type=None)
        cur   = data["current_condition"][0]
        temp  = cur.get("temp_C", "?")
        desc  = cur.get("weatherDesc", [{}])[0].get("value", "unknown")
        feels = cur.get("FeelsLikeC", "?")
        humid = cur.get("humidity", "?")
        rain  = cur.get("precipMM", "0")
        reply = (
            f"🌤 {city}: {temp}°C, {desc}. "
            f"Feels {feels}°C, humidity {humid}%, rain {rain}mm. "
            f"(wttr.in)"
        )
        return reply[:249]
    except Exception as exc:
        print(f"[AI LIVE ERROR] weather: {exc!r}")
        return f"⛅ Couldn't fetch weather for {city} right now."


# ── Exchange rates ────────────────────────────────────────────────────────────

_AMOUNT_PAT = re.compile(r"\b(\d+(?:\.\d+)?)\b")


def _extract_amount(text: str) -> float:
    """Extract the first numeric amount from query; default 1.0."""
    m = _AMOUNT_PAT.search(text)
    if m:
        return float(m.group(1))
    return 1.0


async def get_exchange_answer(query: str) -> str:
    from_cur, to_cur = _extract_currencies(query)
    amount = _extract_amount(query)
    print(f"[AI DEBUG] intent=exchange_rate amount={amount} source_currency={from_cur!r} target_currency={to_cur!r}")
    url = f"https://api.frankfurter.app/latest?from={from_cur}&to={to_cur}"
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as sess:
            async with sess.get(url) as r:
                if r.status != 200:
                    return f"💱 Couldn't fetch {from_cur}/{to_cur} rate right now."
                data = await r.json()
        rate = data.get("rates", {}).get(to_cur)
        date = data.get("date", "today")
        if rate is None:
            return f"💱 {from_cur}/{to_cur} rate not found. Try a finance site."
        if amount != 1.0:
            converted = rate * amount
            amt_fmt = f"{amount:.0f}" if amount == int(amount) else f"{amount:.2f}"
            return (
                f"💱 {amt_fmt} {from_cur} = {converted:.2f} {to_cur} "
                f"(as of {date}). Rates update daily."
            )[:249]
        return (
            f"💱 1 {from_cur} = {rate:.4f} {to_cur} "
            f"(as of {date}). Rates update daily. (frankfurter.app)"
        )[:249]
    except Exception as exc:
        print(f"[AI LIVE ERROR] exchange: {exc!r}")
        return f"💱 Couldn't fetch {from_cur}/{to_cur} rate right now."


# ── Crypto prices ─────────────────────────────────────────────────────────────

async def get_crypto_answer(query: str) -> str:
    coin_id = _extract_coin(query)
    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        f"?ids={coin_id}&vs_currencies=usd&include_24hr_change=true"
    )
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as sess:
            async with sess.get(url, headers={"User-Agent": "ChillTopia-Bot/3.3D"}) as r:
                if r.status != 200:
                    return f"₿ Couldn't fetch {coin_id} price right now."
                data = await r.json()
        if coin_id not in data:
            return f"₿ {coin_id.title()} price not found. Check CoinGecko directly."
        info   = data[coin_id]
        price  = info.get("usd", 0)
        change = info.get("usd_24h_change")
        ch_str = f" ({change:+.1f}% 24h)" if change is not None else ""
        return (
            f"₿ {coin_id.title()}: ${price:,.2f} USD{ch_str}. "
            f"(CoinGecko — crypto prices change fast!)"
        )[:249]
    except Exception as exc:
        print(f"[AI LIVE ERROR] crypto: {exc!r}")
        return f"₿ Couldn't fetch {coin_id} price right now."


# ── Backwards-compat stubs (used by older ai_brain references) ─────────────────

def is_live_question(text: str) -> bool:
    return False  # detection now in ai_live_router


def get_live_unavailable_reply() -> str:
    return "🌐 Live internet is not connected yet."
