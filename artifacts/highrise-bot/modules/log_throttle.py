"""Small runtime log dedupe helpers.

The bot fleet runs as multiple processes, so noisy room events need a tiny
cross-process TTL gate. These helpers only suppress repeated log lines; they do
not alter event processing or bot behavior.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from pathlib import Path

_LOCAL_LAST: dict[str, float] = {}
_LOCAL_ONCE: set[str] = set()
_DEDUP_DIR = Path(tempfile.gettempdir()) / "chilltopia_log_dedupe"


def verbose_logs_enabled() -> bool:
    return str(
        os.getenv("CHILLTOPIA_DEBUG")
        or os.getenv("VERBOSE_LOGS")
        or os.getenv("DEBUG")
        or ""
    ).strip().lower() in {"1", "true", "yes", "on", "debug"}


def _safe_path(key: str) -> Path:
    digest = hashlib.sha1(key.encode("utf-8", "ignore")).hexdigest()
    return _DEDUP_DIR / digest


def should_log(key: str, ttl_seconds: float = 60.0, *, cross_process: bool = False) -> bool:
    if verbose_logs_enabled():
        return True
    now = time.time()
    ttl = max(0.0, float(ttl_seconds or 0.0))
    last = _LOCAL_LAST.get(key, 0.0)
    if ttl and now - last < ttl:
        return False
    _LOCAL_LAST[key] = now
    if not cross_process:
        return True
    try:
        _DEDUP_DIR.mkdir(parents=True, exist_ok=True)
        path = _safe_path(key)
        if path.exists() and ttl and now - path.stat().st_mtime < ttl:
            return False
        path.write_text(str(now), encoding="utf-8")
    except Exception:
        return True
    return True


def log_cooldown(key: str, message: str, seconds: float = 60.0, *, cross_process: bool = False) -> bool:
    if should_log(key, seconds, cross_process=cross_process):
        print(message)
        return True
    return False


def log_once(key: str, message: str) -> bool:
    if verbose_logs_enabled():
        print(message)
        return True
    if key in _LOCAL_ONCE:
        return False
    _LOCAL_ONCE.add(key)
    print(message)
    return True

