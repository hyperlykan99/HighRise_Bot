"""
modules/ai_logs.py — Ring-buffer AI event log for AceSinatra (3.3A rebuild).

Logs:
  - user question + detected intent + permission level
  - prepared actions
  - confirmed actions
  - denied / blocked actions
  - errors

Max 200 entries in memory. No external storage — lightweight and safe.
"""
from __future__ import annotations

import datetime
from collections import deque

_PH_OFFSET = datetime.timezone(datetime.timedelta(hours=8))
_LOG: deque = deque(maxlen=200)


def _ts() -> str:
    return datetime.datetime.now(_PH_OFFSET).strftime("%Y-%m-%d %H:%M:%S")


def log_event(
    username: str,
    perm: str,
    intent: str,
    text: str,
    action: str = "",
    outcome: str = "ok",
) -> None:
    _LOG.append({
        "ts":      _ts(),
        "user":    username,
        "perm":    perm,
        "intent":  intent,
        "text":    text[:80],
        "action":  action,
        "outcome": outcome,
    })


def recent_logs(n: int = 10) -> list[dict]:
    """Return the N most recent log entries."""
    return list(_LOG)[-n:]


def format_log_summary(n: int = 5) -> str:
    """Return a whisper-safe summary of the last N entries."""
    entries = recent_logs(n)
    if not entries:
        return "📋 No AI log entries yet."
    lines = ["📋 Recent AI activity:"]
    for e in reversed(entries):
        line = f"[{e['ts'][11:16]}] {e['user']} → {e['intent']} ({e['outcome']})"
        lines.append(line[:80])
    return "\n".join(lines)[:249]
