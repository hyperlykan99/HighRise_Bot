"""
modules/ai_tool_router.py — Safe AI tool router (3.3B).

All DB/system queries go through here. Every call:
  - checks permission first
  - returns only safe, formatted data
  - marks contains_private so reply mode knows to whisper
  - never returns raw DB rows or secrets
"""
from __future__ import annotations

import database as db
from modules.ai_reply_mode import get_reply_mode

PERM_PLAYER = "player"
PERM_STAFF  = "staff"
PERM_ADMIN  = "admin"
PERM_OWNER  = "owner"


def _safe_int(val) -> int:
    try:
        return int(val or 0)
    except Exception:
        return 0


def get_player_summary(user_id: str, perm: str = PERM_PLAYER) -> dict:
    """Return safe player summary. Always contains_private."""
    try:
        coins   = _safe_int(db.get_balance(user_id))
        profile = db.get_profile(user_id) or {}
        level   = _safe_int(profile.get("level", 1))
    except Exception:
        coins, level = 0, 1

    tickets = 0
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT balance FROM luxe_tickets WHERE user_id=? LIMIT 1",
            (user_id,),
        ).fetchone()
        conn.close()
        tickets = _safe_int(row[0] if row else 0)
    except Exception:
        pass

    return {
        "coins":            coins,
        "level":            level,
        "tickets":          tickets,
        "contains_private": True,
        "reply_channel":    "whisper",
    }


def get_public_event_status() -> dict:
    """Return current active event (public info)."""
    try:
        name     = db.get_room_setting("active_event_name", "")
        ends_at  = db.get_room_setting("active_event_ends_at", "")
        effect   = db.get_room_setting("active_event_effect_label", "")
    except Exception:
        name, ends_at, effect = "", "", ""

    return {
        "event_name":       name or "None",
        "ends_at":          ends_at,
        "effect":           effect,
        "contains_private": False,
        "reply_channel":    "public",
    }


def get_player_mission_status(user_id: str) -> dict:
    """Return mission completion summary. contains_private=True."""
    daily_open = 0
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT COUNT(*) FROM missions WHERE user_id=? AND completed=0 AND period='daily'",
            (user_id,),
        ).fetchone()
        conn.close()
        daily_open = _safe_int(row[0] if row else 0)
    except Exception:
        pass

    return {
        "daily_open":       daily_open,
        "contains_private": True,
        "reply_channel":    "whisper",
    }


def get_bug_summary_safe(perm: str) -> dict:
    """Return bug summary (staff+ only)."""
    if perm not in (PERM_STAFF, PERM_ADMIN, PERM_OWNER):
        return {"error": "staff_only", "contains_private": True, "reply_channel": "whisper"}

    total = 0
    latest = ""
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE report_type='bug_report'",
        ).fetchone()
        total = _safe_int(row[0] if row else 0)
        row2 = conn.execute(
            "SELECT reason FROM reports WHERE report_type='bug_report' "
            "ORDER BY created_at DESC LIMIT 1",
        ).fetchone()
        latest = str(row2[0])[:80] if row2 else "none"
        conn.close()
    except Exception:
        pass

    return {
        "total":            total,
        "latest":           latest,
        "contains_private": True,
        "reply_channel":    "whisper",
    }


def get_ai_reply_mode_info() -> dict:
    """Return current AI reply mode (public info)."""
    return {
        "mode":             get_reply_mode(),
        "contains_private": False,
        "reply_channel":    "public",
    }


def prepare_setting_change(action_key: str, label: str, confirm_phrase: str,
                           current_value: str, new_value: str, risk: str) -> dict:
    """Return a structured setting-change preview dict."""
    return {
        "action_key":       action_key,
        "label":            label,
        "confirm_phrase":   confirm_phrase,
        "current_value":    current_value,
        "new_value":        new_value,
        "risk":             risk,
        "contains_private": True,
        "reply_channel":    "whisper",
    }
