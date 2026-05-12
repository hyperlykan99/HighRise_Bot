"""
modules/rate_limiter.py
-----------------------
Async token-bucket rate limiter for Highrise SDK send_whisper / chat calls.

Installed once per connection in on_start() via install_rate_limiter().
Wraps bot.highrise.send_whisper and bot.highrise.chat so that all existing
call sites are protected with zero code changes at those sites.

The SDK always creates a fresh Highrise() instance before calling on_start,
so this is safe to call on every reconnect — old buckets are simply discarded.

Default limits (used when the server doesn't include the key in rate_limits):
  chat    : 10 messages per 5 seconds
  whisper : 10 messages per 5 seconds

When the server supplies rate_limits, we use 80% of the stated limit as
headroom so we never hit the hard cap.

All messages <= 249 chars.
"""
from __future__ import annotations

import asyncio
import time


class _TokenBucket:
    """Async leaky token bucket — sleeps until a token is available."""

    def __init__(self, tokens: float, per_seconds: float) -> None:
        self._capacity = float(tokens)
        self._tokens   = float(tokens)
        self._per      = float(per_seconds)
        self._rate     = tokens / per_seconds   # tokens per second
        self._last     = time.monotonic()
        self._lock     = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until a send token is available, then consume one."""
        async with self._lock:
            now = time.monotonic()
            self._tokens = min(
                self._capacity,
                self._tokens + (now - self._last) * self._rate,
            )
            self._last = now
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0

    def __repr__(self) -> str:
        return f"{self._capacity:.0f}/{self._per:.1f}s"


def install_rate_limiter(
    hr,
    rate_limits: dict,
    *,
    default_rate: float = 10.0,
    default_per: float  = 5.0,
) -> None:
    """Monkey-patch bot.highrise so all send_whisper / chat calls are throttled.

    Args:
        hr:           bot.highrise (Highrise SDK instance)
        rate_limits:  session_metadata.rate_limits dict from the SDK handshake
        default_rate: fallback bucket capacity if key absent from rate_limits
        default_per:  fallback bucket window (seconds) if key absent
    """

    def _bucket(key: str) -> _TokenBucket:
        if key in rate_limits:
            limit, period = rate_limits[key]
            # 80% of the hard server limit gives comfortable headroom
            safe = max(1.0, limit * 0.8)
            return _TokenBucket(safe, period)
        return _TokenBucket(default_rate, default_per)

    chat_bucket    = _bucket("chat")
    whisper_bucket = _bucket("whisper")

    _orig_chat    = hr.chat
    _orig_whisper = hr.send_whisper

    async def _guarded_chat(message: str) -> None:
        await chat_bucket.acquire()
        await _orig_chat(message)

    async def _guarded_whisper(user_id: str, message: str) -> None:
        await whisper_bucket.acquire()
        await _orig_whisper(user_id, message)

    hr.chat         = _guarded_chat
    hr.send_whisper = _guarded_whisper

    print(
        f"[RATE] Guards active — "
        f"chat {chat_bucket} | whisper {whisper_bucket} | "
        f"server_keys={list(rate_limits.keys())}"
    )
