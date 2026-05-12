"""
modules/bot_state.py — Shared runtime state for bot health tracking.

Imported by both main.py and bot_health.py to avoid circular imports.
All fields are updated at runtime; never persisted to DB.
"""
from __future__ import annotations
import datetime as _dt

PROC_START:   _dt.datetime = _dt.datetime.now(_dt.timezone.utc)
RESTART_COUNT: int  = 0   # incremented each time on_start fires
LAST_ERROR:    str  = ""  # last on_chat unhandled exception summary
LAST_DISCONNECT: str = "" # last disconnect note (if SDK exposes it)
