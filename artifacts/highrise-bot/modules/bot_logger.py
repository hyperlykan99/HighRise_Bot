"""
modules/bot_logger.py
---------------------
Structured, file-based observability for the multi-bot system.

Log layout
----------
  logs/
    supervisor/
      supervisor.log   — runner / registry / startup timeline events
      audit.log        — owner+admin command audit trail
    bots/
      {mode}.log       — per-bot connect / disconnect / reconnect events
    crashes/
      {mode}_{ts}.json — crash snapshot JSON (last N per bot, auto-rotated)

All plain-text logs are capped at _MAX_BYTES and rotated to .1 copy on overflow.
Crash JSONs are capped at _MAX_CRASHES per bot (oldest pruned automatically).

Public API
----------
  categorize_reason(reason)          → str   (normalised category)
  bot_log(mode, message)             → None
  supervisor_log(message)            → None
  audit_log(username, cmd, detail)   → None
  write_crash_snapshot(...)          → None
  get_recent_crashes(mode, n)        → list[dict]
  read_recent_bot_log(mode, lines)   → list[str]
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

_HERE      = Path(__file__).parent.parent          # artifacts/highrise-bot/
_LOG_DIR   = _HERE / "logs"
_MAX_BYTES  = 5 * 1024 * 1024    # 5 MB per plain-text log before rotation
_MAX_CRASHES = 5                  # keep last N crash snapshots per bot


# ---------------------------------------------------------------------------
# Directory bootstrap (lazy, called on first write)
# ---------------------------------------------------------------------------

def _ensure_dirs() -> None:
    for sub in ("supervisor", "bots", "crashes"):
        (_LOG_DIR / sub).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Reason categorization
# ---------------------------------------------------------------------------

_REASON_MAP: list[tuple[str, str]] = [
    ("multilogin",        "multilogin"),
    ("duplicate session", "multilogin"),
    ("rate limit",        "rate_limit"),
    ("rate_limit",        "rate_limit"),
    ("ratelimit",         "rate_limit"),
    ("too many",          "rate_limit"),
    ("websocket",         "websocket_close"),
    ("connection closed", "websocket_close"),
    ("connection reset",  "websocket_close"),
    ("eof",               "websocket_close"),
    ("timeout",           "timeout"),
    ("timed out",         "timeout"),
    ("sigterm",           "manual_restart"),
    ("sigkill",           "manual_restart"),
    ("signal -",          "manual_restart"),
    ("clean exit",        "clean_exit"),
    ("python exception",  "crash"),
    ("usage/os error",    "crash"),
    ("code 1",            "crash"),
]


def categorize_reason(reason: str) -> str:
    """Map a raw disconnect reason string → a normalised category string."""
    low = reason.lower()
    for pattern, category in _REASON_MAP:
        if pattern in low:
            return category
    return "unknown"


# ---------------------------------------------------------------------------
# Low-level rotating writer
# ---------------------------------------------------------------------------

def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(path: Path, line: str) -> None:
    """Append one timestamped line; rotate if file exceeds _MAX_BYTES."""
    try:
        _ensure_dirs()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > _MAX_BYTES:
            rotated = path.with_name(path.stem + ".1" + path.suffix)
            try:
                rotated.unlink(missing_ok=True)
            except Exception:
                pass
            path.rename(rotated)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{_utc_ts()}  {line}\n")
    except Exception:
        pass   # never crash the bot over logging


# ---------------------------------------------------------------------------
# Public write functions
# ---------------------------------------------------------------------------

def bot_log(mode: str, message: str) -> None:
    """Append one line to logs/bots/{mode}.log."""
    safe = (mode or "unknown").lower().replace("/", "_")
    _write(_LOG_DIR / "bots" / f"{safe}.log", message)


def supervisor_log(message: str) -> None:
    """Append one line to logs/supervisor/supervisor.log."""
    _write(_LOG_DIR / "supervisor" / "supervisor.log", message)


def audit_log(username: str, cmd: str, detail: str = "") -> None:
    """Append one line to logs/supervisor/audit.log."""
    tail = f"  {detail}" if detail else ""
    _write(_LOG_DIR / "supervisor" / "audit.log",
           f"[AUDIT] {username}: !{cmd}{tail}")


# ---------------------------------------------------------------------------
# Crash / disconnect snapshot
# ---------------------------------------------------------------------------

def _hm(secs: float) -> str:
    m, _ = divmod(max(0, int(secs)), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m}m" if h else f"{m}m"


def write_crash_snapshot(
    mode: str,
    label: str,
    uptime: float,
    reconnect_count: int,
    reason: str,
    traceback_text: str = "",
    ts: str = "",
) -> None:
    """
    Write a JSON disconnect/crash snapshot to logs/crashes/{mode}_{ts}.json.
    Prunes old files so only _MAX_CRASHES remain per bot.
    """
    try:
        _ensure_dirs()
        crash_dir = _LOG_DIR / "crashes"
        safe_mode = (mode or "unknown").lower().replace("/", "_")
        now_iso   = datetime.now(timezone.utc).isoformat()
        safe_ts   = (ts or _utc_ts()).replace(":", "").replace(" ", "_")

        snap = {
            "bot_name":          label,
            "mode":              mode,
            "uptime_secs":       round(uptime, 1),
            "uptime_human":      _hm(uptime),
            "reconnect_count":   reconnect_count,
            "disconnect_reason": reason,
            "reason_category":   categorize_reason(reason),
            "traceback":         traceback_text,
            "timestamp":         now_iso,
        }
        path = crash_dir / f"{safe_mode}_{safe_ts}.json"
        path.write_text(json.dumps(snap, indent=2), encoding="utf-8")

        # Prune oldest snapshots for this mode
        old = sorted(crash_dir.glob(f"{safe_mode}_*.json"))
        for stale in old[:-_MAX_CRASHES]:
            try:
                stale.unlink()
            except Exception:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Read helpers (used by !botdiag)
# ---------------------------------------------------------------------------

def get_recent_crashes(mode: str, n: int = 3) -> list[dict]:
    """Return the last n crash snapshots for a bot mode, newest first."""
    try:
        crash_dir = _LOG_DIR / "crashes"
        safe = (mode or "unknown").lower().replace("/", "_")
        files = sorted(crash_dir.glob(f"{safe}_*.json"))[-n:]
        results: list[dict] = []
        for f in reversed(files):
            try:
                results.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
        return results
    except Exception:
        return []


def read_recent_bot_log(mode: str, tail_lines: int = 20) -> list[str]:
    """Return last tail_lines from logs/bots/{mode}.log, newest last."""
    try:
        safe = (mode or "unknown").lower().replace("/", "_")
        path = _LOG_DIR / "bots" / f"{safe}.log"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-tail_lines:]
    except Exception:
        return []
