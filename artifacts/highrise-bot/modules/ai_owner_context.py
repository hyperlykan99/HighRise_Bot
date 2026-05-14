"""
modules/ai_owner_context.py — Owner-level knowledge context (3.3A).

Access: owner only.

Provides:
- Economy health summary (counts, not raw data)
- Analytics overview
- Configuration summary

Does NOT expose:
- Raw database records
- API keys, bot tokens, passwords
- Environment variables
- Internal file paths
- Exploit-sensitive anti-abuse logic
"""
from __future__ import annotations

import database as db


def _safe_setting(key: str, default: str = "not set") -> str:
    try:
        return db.get_room_setting(key, default)
    except Exception:
        return default


def _count_table(table: str, where: str = "") -> int:
    try:
        conn = db.get_connection()
        q = f"SELECT COUNT(*) FROM {table}"
        if where:
            q += f" WHERE {where}"
        row = conn.execute(q).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return -1


def get_economy_health_summary() -> str:
    total_players = _count_table("players")
    active_30d    = _count_table("players", "last_seen >= datetime('now','-30 days')")
    open_bugs     = _count_table("reports", "report_type='bug_report' AND status='open'")
    return (
        f"📊 Economy Health (Owner Summary):\n"
        f"• Registered players: {total_players}\n"
        f"• Active last 30d: {active_30d}\n"
        f"• Open bug reports: {open_bugs}\n"
        "Use !bothealth or staff dashboards for full analytics."
    )[:249]


def get_analytics_summary() -> str:
    total_players = _count_table("players")
    total_reports = _count_table("reports", "status='open'")
    return (
        f"📈 Analytics Summary (Owner):\n"
        f"• Total players: {total_players}\n"
        f"• Open reports: {total_reports}\n"
        "Detailed analytics: use bot dashboards or owner commands."
    )[:249]


def get_config_summary() -> str:
    vip_price    = _safe_setting("vip_price", "not set")
    auto_events  = _safe_setting("autoevent_enabled", "off")
    version      = _safe_setting("release_version", "unknown")
    return (
        f"⚙️ Owner Config Summary:\n"
        f"• Release: {version}\n"
        f"• VIP price: {vip_price} 🎫\n"
        f"• Auto events: {auto_events}\n"
        "Sensitive settings are not shown through AI."
    )[:249]


NEVER_EXPOSE_REPLY = (
    "⛔ I can't reveal that — it contains API keys, tokens, passwords, "
    "or other protected system secrets."
)

_NEVER_EXPOSE_PATTERNS = (
    "database", "db dump", "raw data", "json records",
    "bot token", "api key", "password", "env var",
    "environment variable", "secret", "private key",
    "file path", "backend path",
)


def is_never_expose_request(text: str) -> bool:
    low = text.lower()
    return any(p in low for p in _NEVER_EXPOSE_PATTERNS)
