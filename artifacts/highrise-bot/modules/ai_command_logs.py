"""
modules/ai_command_logs.py — Audit log for AI Command Control Layer (3.3F).

Keeps the last 200 AI command events in memory (non-persistent).
Each entry is printed so it appears in bot console logs.
"""
from __future__ import annotations

import time
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_LOG: deque[dict] = deque(maxlen=200)


def log_ai_command(
    username:  str,
    user_id:   str,
    command:   str,
    args:      list[str],
    status:    str,        # "executed", "prepared", "denied", "safety_blocked", "cancelled"
    reason:    str = "",
) -> None:
    """Record an AI command event."""
    entry = {
        "ts":       time.time(),
        "username": username,
        "user_id":  user_id,
        "command":  command,
        "args":     args,
        "status":   status,
        "reason":   reason,
    }
    _LOG.append(entry)
    args_str = " ".join(args) if args else "(none)"
    print(
        f"[AI CMD] user={username!r} cmd={command!r} args={args_str!r} "
        f"status={status!r} reason={reason!r}"
    )


def get_recent_log(limit: int = 20) -> list[dict]:
    """Return the most recent log entries (newest first)."""
    entries = list(_LOG)
    entries.reverse()
    return entries[:limit]


def format_log_summary(limit: int = 5) -> str:
    """Return a short human-readable summary of recent AI commands."""
    recent = get_recent_log(limit)
    if not recent:
        return "No AI commands logged yet."
    lines = []
    for e in recent:
        ts = time.strftime("%H:%M", time.localtime(e["ts"]))
        args = " ".join(e["args"]) if e["args"] else ""
        cmd  = f"!{e['command']}" + (f" {args}" if args else "")
        lines.append(f"{ts} {e['username']}: {cmd} [{e['status']}]")
    return "\n".join(lines)
