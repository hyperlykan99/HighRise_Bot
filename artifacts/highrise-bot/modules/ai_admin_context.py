"""
modules/ai_admin_context.py — Admin-level knowledge context (3.3A).

Access: admin / owner only.

Provides:
- Event settings summary (safe, non-sensitive)
- VIP price setting
- Assistant settings
- Shop status overview

Does NOT expose: odds, raw economy formulas, tokens, passwords.
"""
from __future__ import annotations

import database as db


def _safe_setting(key: str, default: str = "not set") -> str:
    try:
        return db.get_room_setting(key, default)
    except Exception:
        return default


def get_event_settings_summary() -> str:
    auto_interval = _safe_setting("autoevent_interval_minutes", "60")
    auto_enabled  = _safe_setting("autoevent_enabled", "off")
    active_event  = _safe_setting("active_event_type", "none")
    duration      = _safe_setting("default_event_duration", "60")
    return (
        f"🎉 Event Settings:\n"
        f"• Active event: {active_event}\n"
        f"• Auto events: {auto_enabled}\n"
        f"• Auto interval: {auto_interval} min\n"
        f"• Default duration: {duration} min"
    )[:249]


def get_vip_settings_summary() -> str:
    vip_price = _safe_setting("vip_price", "not set")
    return (
        f"💎 VIP Settings:\n"
        f"• VIP price: {vip_price} 🎫 Luxe Tickets\n"
        "Use 'ai set VIP price to [amount] tickets' to change."
    )[:249]


def get_assistant_settings_summary() -> str:
    ai_setting  = _safe_setting("ai_assistant_setting", "default")
    announce    = _safe_setting("ai_announcement_text", "(not set)")
    return (
        f"🤖 Assistant Settings:\n"
        f"• Mode: {ai_setting}\n"
        f"• Announcement: {announce[:60]}"
    )[:249]


def get_admin_panel_summary() -> str:
    vip_price    = _safe_setting("vip_price", "not set")
    active_event = _safe_setting("active_event_type", "none")
    auto_enabled = _safe_setting("autoevent_enabled", "off")
    return (
        f"⚙️ Admin Panel Summary:\n"
        f"• VIP price: {vip_price} 🎫\n"
        f"• Active event: {active_event}\n"
        f"• Auto events: {auto_enabled}\n"
        "Use specific 'ai show [setting]' for details."
    )[:249]
