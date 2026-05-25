"""
modules/dashboard_settings.py
-----------------------------
Small read-only bridge from the owner/staff web dashboard to bot runtime code.

The dashboard writes DB rows only. Bot modules import this helper when they want
to opt in to a dashboard flag without coupling to the web server.
"""
from __future__ import annotations

import database as db


def _ensure_tables() -> None:
    try:
        conn = db.get_connection()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS module_flags ("
            "module TEXT PRIMARY KEY, "
            "enabled INTEGER NOT NULL DEFAULT 1, "
            "reason TEXT NOT NULL DEFAULT '', "
            "updated_by TEXT NOT NULL DEFAULT '', "
            "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS bot_settings ("
            "key TEXT PRIMARY KEY, "
            "value TEXT NOT NULL DEFAULT '')"
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[DASHBOARD_SETTINGS] ensure_tables error={exc!r}")


def get_setting(key: str, default: str = "") -> str:
    """Read a dashboard bot_settings value without raising into command paths."""
    try:
        conn = db.get_connection()
        row = conn.execute("SELECT value FROM bot_settings WHERE key=?", (key,)).fetchone()
        conn.close()
        return str(row["value"]) if row else default
    except Exception as exc:
        print(f"[DASHBOARD_SETTINGS] get_setting key={key!r} error={exc!r}")
        return default


def setting_bool(key: str, default: bool = True) -> bool:
    val = get_setting(key, "true" if default else "false").strip().lower()
    return val in {"1", "true", "yes", "on", "enabled"}


def module_enabled(module: str, default: bool = True) -> bool:
    """Return false when dashboard module_flags explicitly disables a module."""
    try:
        _ensure_tables()
        conn = db.get_connection()
        row = conn.execute(
            "SELECT enabled FROM module_flags WHERE module=?",
            (module.lower().strip(),),
        ).fetchone()
        conn.close()
        if row is None:
            return default
        return int(row["enabled"]) != 0
    except Exception as exc:
        print(f"[DASHBOARD_SETTINGS] module_enabled module={module!r} error={exc!r}")
        return default


def dashboard_gate_open(module: str, setting_key: str | None = None) -> bool:
    """Combined module flag + optional bot_settings boolean check."""
    if not module_enabled(module, True):
        return False
    if setting_key and not setting_bool(setting_key, True):
        return False
    return True
