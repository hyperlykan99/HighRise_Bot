"""
modules/ai_action_executor.py — Safe confirmed action executor (3.3A rebuild).

Only actions that have passed the full confirmation flow reach this module.

Allowed prepared actions:
  set_vip_price, start_event, stop_event, set_event_duration,
  update_assistant_setting, update_announcement_text

Blocked (AI must never execute these):
  wipe_data, reset_economy, grant_currency, change_casino_odds,
  mass_ban, delete_profiles, edit_database_directly
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import database as db

if TYPE_CHECKING:
    from highrise import BaseBot

BLOCKED_MSG = (
    "⛔ I can't do that — it affects protected data or economy safety."
)

_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "set_vip_price",
    "start_event",
    "stop_event",
    "set_event_duration",
    "update_assistant_setting",
    "update_announcement_text",
})

_BLOCKED_ACTIONS: frozenset[str] = frozenset({
    "wipe_data",
    "reset_economy",
    "grant_currency",
    "change_casino_odds",
    "mass_ban",
    "delete_profiles",
    "edit_database_directly",
})


def is_allowed_action(action_key: str) -> bool:
    return action_key in _ALLOWED_ACTIONS


def is_blocked_action(action_key: str) -> bool:
    return action_key in _BLOCKED_ACTIONS


def _log_action(action_key: str, new_value: str, executed_by: str) -> None:
    try:
        import json
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO release_audits (audit_type, status, summary_json, created_by) "
            "VALUES (?,?,?,?)",
            (
                "ai_action",
                "ok",
                json.dumps({"action": action_key, "value": new_value[:100]}),
                executed_by,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


async def execute_action(
    bot: "BaseBot",
    user_id: str,
    action_key: str,
    new_value: str,
    executed_by: str,
) -> str:
    """Execute a confirmed safe action. Returns a result message ≤ 249 chars."""
    if not is_allowed_action(action_key):
        return BLOCKED_MSG

    if action_key == "set_vip_price":
        try:
            db.set_room_setting("vip_price", new_value)
            _log_action(action_key, new_value, executed_by)
            return f"✅ VIP price updated to {new_value}.\nChange logged."
        except Exception as e:
            return f"⚠️ Action failed: {str(e)[:80]}"

    elif action_key == "start_event":
        try:
            _log_action(action_key, new_value, executed_by)
            return (
                f"✅ Event start logged: {new_value}\n"
                f"Use !startevent {new_value} to start it in the room."
            )
        except Exception as e:
            return f"⚠️ Action failed: {str(e)[:80]}"

    elif action_key == "stop_event":
        try:
            _log_action(action_key, new_value, executed_by)
            return "✅ Stop event logged. Use !stopevent to stop the current event."
        except Exception as e:
            return f"⚠️ Action failed: {str(e)[:80]}"

    elif action_key == "set_event_duration":
        try:
            db.set_room_setting("default_event_duration", new_value)
            _log_action(action_key, new_value, executed_by)
            return f"✅ Default event duration set to {new_value} minutes."
        except Exception as e:
            return f"⚠️ Action failed: {str(e)[:80]}"

    elif action_key == "update_announcement_text":
        try:
            db.set_room_setting("ai_announcement_text", new_value[:200])
            _log_action(action_key, new_value, executed_by)
            return "✅ Announcement text updated."
        except Exception as e:
            return f"⚠️ Action failed: {str(e)[:80]}"

    elif action_key == "update_assistant_setting":
        try:
            db.set_room_setting("ai_assistant_setting", new_value[:100])
            _log_action(action_key, new_value, executed_by)
            return "✅ Assistant setting updated."
        except Exception as e:
            return f"⚠️ Action failed: {str(e)[:80]}"

    return BLOCKED_MSG
