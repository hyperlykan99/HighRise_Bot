"""
modules/ai_usage_logs.py — Billing and LLM usage audit log (3.3G).

Prints structured [AI BILLING] and [AI LLM USAGE] lines to console.
Keeps last 200 entries in-memory (non-persistent, lightweight).
"""
from __future__ import annotations

import time
from collections import deque

_LOG: deque[dict] = deque(maxlen=200)


def log_billing(
    username: str,
    cost:     int,
    charged:  bool,
    reason:   str = "",
) -> None:
    """Print and store a billing event."""
    entry = {
        "ts":       time.time(),
        "type":     "billing",
        "username": username,
        "cost":     cost,
        "charged":  charged,
        "reason":   reason,
    }
    _LOG.append(entry)
    reason_str = f" reason={reason!r}" if reason else ""
    print(f"[AI BILLING] user={username} cost={cost} charged={charged}{reason_str}")


def log_llm_call(
    username: str,
    intent:   str,
    success:  bool,
    model:    str = "",
) -> None:
    """Print and store an LLM call event."""
    entry = {
        "ts":       time.time(),
        "type":     "llm_call",
        "username": username,
        "intent":   intent,
        "success":  success,
        "model":    model,
    }
    _LOG.append(entry)
    model_str = f" model={model}" if model else ""
    print(f"[AI LLM USAGE] user={username} intent={intent!r} success={success}{model_str}")


def get_recent(limit: int = 20) -> list[dict]:
    """Return most recent log entries (newest first)."""
    entries = list(_LOG)
    entries.reverse()
    return entries[:limit]


def billing_summary(limit: int = 5) -> str:
    """Return a short human-readable billing summary (≤249 chars)."""
    recent = [e for e in get_recent(20) if e["type"] == "billing"][:limit]
    if not recent:
        return "No AI billing events yet."
    lines = []
    for e in recent:
        ts = time.strftime("%H:%M", time.localtime(e["ts"]))
        status = "charged" if e["charged"] else "free"
        lines.append(f"{ts} {e['username']}: {e['cost']}🎫 [{status}]")
    return "\n".join(lines)[:249]
