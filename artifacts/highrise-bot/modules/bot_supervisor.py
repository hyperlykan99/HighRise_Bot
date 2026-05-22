"""
modules/bot_supervisor.py
--------------------------
Central supervisor: registry loading, health snapshots, per-reason cooldowns.

This module runs in the PARENT bot.py process only.
Subprocesses (main.py) read health via the DB key _supervisor_health.

Public API
----------
  load_registry()                     → dict   (registry keyed by mode or username)
  is_bot_enabled(key, registry)       → bool
  get_priority(key, registry)         → int
  get_display_name(key, registry)     → str
  cooldown_for_reason(reason)         → int    (seconds)
  update_health(bot_username, ...)    → None
  persist_health_to_db()             → None   (called after each reconnect)
  load_health_from_db()              → dict   (used by !botstatus subprocess side)
  get_health(bot_username)           → dict
  get_all_health()                   → dict[str, dict]
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

_HERE         = Path(__file__).parent.parent          # artifacts/highrise-bot/
_REGISTRY     = _HERE / "config" / "bot_registry.json"

# ── Per-reason reconnect cooldowns ────────────────────────────────────────────
# The runner uses max(normal_delay, _REASON_COOLDOWNS[matched_key]).
# Matching is substring (case-insensitive) against the disconnect reason string.
_REASON_COOLDOWNS: dict[str, int] = {
    "multilogin":        120,   # kicked for duplicate session → long wait
    "rate limit":         60,   # platform rate-limiting → wait before hammering
    "rate_limit":         60,
    "ratelimit":          60,
    "too many":           60,
    "websocket closed":   20,   # clean WS teardown → modest wait
    "websocket_closed":   20,
    "connection closed":  20,
    "connection reset":   20,
    "eof":                15,
}
_DEFAULT_COOLDOWN = 10   # seconds (same as existing _BACKOFF[0])

# ── In-memory health snapshots ─────────────────────────────────────────────────
# Keyed by bot_username (lowercase).  Written by bot.py, read by !botstatus
# indirectly through the DB.
_health: dict[str, dict] = {}


# ─── Registry helpers ──────────────────────────────────────────────────────────

def load_registry() -> dict:
    """
    Load config/bot_registry.json.
    Returns {} silently if the file doesn't exist (graceful degradation).
    """
    try:
        raw = _REGISTRY.read_text()
        data = json.loads(raw)
        # Strip comment keys
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        print(f"[BOT_SUPERVISOR] registry load error: {exc!r}")
        return {}


def _lookup(key: str, registry: dict) -> dict:
    """Look up a bot entry by mode or username (case-insensitive)."""
    return (
        registry.get(key)
        or registry.get(key.lower())
        or {}
    )


def is_bot_enabled(key: str, registry: dict) -> bool:
    """Return True if the bot is enabled (default: True when key absent)."""
    entry = _lookup(key, registry)
    return bool(entry.get("enabled", True))


def get_priority(key: str, registry: dict) -> int:
    """Return startup priority (lower = earlier).  Default 99 (last)."""
    entry = _lookup(key, registry)
    return int(entry.get("priority", 99))


def get_display_name(key: str, registry: dict) -> str:
    """Return human-readable display name, falling back to the key itself."""
    entry = _lookup(key, registry)
    return str(entry.get("display_name") or key.title())


def get_role(key: str, registry: dict) -> str:
    entry = _lookup(key, registry)
    return str(entry.get("role", ""))


# ─── Reconnect cooldowns ───────────────────────────────────────────────────────

def cooldown_for_reason(reason: str) -> int:
    """
    Return the minimum reconnect delay (seconds) for a given disconnect reason.
    Matches by substring, case-insensitive.  Returns _DEFAULT_COOLDOWN if no
    match — same as the existing hardcoded 10 s base.
    """
    low = reason.lower()
    for pattern, secs in _REASON_COOLDOWNS.items():
        if pattern in low:
            return secs
    return _DEFAULT_COOLDOWN


# ─── Health snapshots (parent-process side) ───────────────────────────────────

def update_health(
    bot_username: str,
    *,
    bot_mode: str = "",
    connected: bool,
    uptime: float = 0.0,
    reconnect_count: int = 0,
    last_disconnect_reason: str = "",
    room: str = "",
    process_started_at: Optional[float] = None,
    connected_since: Optional[float] = None,
) -> None:
    """
    Update the in-memory health snapshot for one bot.

    process_started_at — wall-clock time the _run_bot_forever task began.
                         Set once and never overwritten (persists across reconnects).
    connected_since    — wall-clock time the current subprocess was spawned.
                         Updated every reconnect cycle.
    """
    now = time.time()
    snap = _health.setdefault(bot_username.lower(), {})

    # process_started_at is only set the first time — it measures true process age.
    if process_started_at is not None:
        snap.setdefault("process_started_at", process_started_at)

    # connected_since is updated every cycle (current connection age).
    if connected_since is not None:
        snap["connected_since"] = connected_since

    snap.update({
        "bot_username":           bot_username,
        "bot_mode":               bot_mode,
        "room":                   room,
        "connected":              connected,
        "last_seen":              now if connected else snap.get("last_seen", now),
        "uptime":                 uptime,
        "reconnect_count":        reconnect_count,
        "last_disconnect_reason": last_disconnect_reason,
    })


def get_health(bot_username: str) -> dict:
    return dict(_health.get(bot_username.lower(), {}))


def get_all_health() -> dict[str, dict]:
    return {k: dict(v) for k, v in _health.items()}


def persist_health_to_db() -> None:
    """
    Write current health snapshots to the shared DB so that !botstatus
    (running inside a subprocess) can read them without direct memory access.
    """
    try:
        import database as _db  # type: ignore[import]
        _db.set_room_setting("_supervisor_health", json.dumps(_health))
    except Exception:
        pass   # non-fatal — !botstatus will fall back to bot_instances table


def load_health_from_db() -> dict:
    """
    Read supervisor health from the DB.  Called by !botstatus in the subprocess.
    Returns {} if no data has been persisted yet.
    """
    try:
        import database as _db  # type: ignore[import]
        raw = _db.get_room_setting("_supervisor_health", "")
        return json.loads(raw) if raw else {}
    except Exception:
        return {}
