"""
modules/ai_live_cache.py — In-memory TTL cache for live data (3.3D).

Prevents redundant API calls and controls cost.

TTLs (seconds):
  weather   : 600  (10 min)
  exchange  : 300  (5 min)
  crypto    : 60   (1 min)
  news      : 600  (10 min)
  sports    : 120  (2 min)
  general   : 300  (5 min)
"""
from __future__ import annotations

import time

_TTL: dict[str, int] = {
    "weather":  600,
    "exchange": 300,
    "crypto":   60,
    "news":     600,
    "sports":   120,
    "general":  300,
}

# {cache_key: (answer_str, expiry_epoch)}
_cache: dict[str, tuple[str, float]] = {}


def _key(live_type: str, query: str) -> str:
    normalized = " ".join(query.lower().split())
    return f"{live_type}::{normalized}"


def get_cached(live_type: str, query: str) -> str | None:
    k = _key(live_type, query)
    entry = _cache.get(k)
    if not entry:
        return None
    answer, expiry = entry
    if time.time() > expiry:
        del _cache[k]
        return None
    return answer


def set_cached(live_type: str, query: str, answer: str) -> None:
    ttl = _TTL.get(live_type, 300)
    _cache[_key(live_type, query)] = (answer, time.time() + ttl)


def cache_stats() -> dict:
    now = time.time()
    active = sum(1 for _, (_, exp) in _cache.items() if exp > now)
    return {"total_entries": len(_cache), "active": active}
