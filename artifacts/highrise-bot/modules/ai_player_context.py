"""
modules/ai_player_context.py — Player-private knowledge context (3.3A).

Access rules:
- Players may only see their OWN balance, level, and progress.
- Asking about another player's private data is always denied.
- Staff/admin/owner follow same rules here — no peeking at other player data via AI.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

import database as db

if TYPE_CHECKING:
    from highrise import User

_DENY_OTHER = "🔒 I can't show another player's private balance or info."
_DENY_SECRET = "⛔ I can't reveal secrets, tokens, passwords, or environment variables."


def _is_asking_about_self(text: str) -> bool:
    low = text.lower()
    return bool(re.search(
        r"\bmy\b|\bi\s+(have|got|own)\b|\bdo\s+i\s+(have|own)\b|\bme\b",
        low,
    ))


def _extract_other_username(text: str) -> str | None:
    """Return a mentioned username if asking about someone else's data."""
    m = re.search(
        r"\bdoes\s+@?(\w+)\s+(have|own)\b"
        r"|\b@?(\w+)'s\s+(balance|tickets?|coins?|level|inventory)\b"
        r"|\bhow\s+many.*does\s+@?(\w+)\b",
        text,
        re.I,
    )
    if m:
        return (m.group(1) or m.group(3) or m.group(5) or "").lower()
    return None


def _get_chillcoins(user_id: str) -> str:
    try:
        coins = db.get_balance(user_id)
        return f"🪙 Your ChillCoins: {coins:,}"
    except Exception:
        return "🪙 Use !balance to check your ChillCoins."


def _get_luxe_tickets(user_id: str) -> str:
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT balance FROM luxe_tickets WHERE user_id=? LIMIT 1",
            (user_id,),
        ).fetchone()
        conn.close()
        tickets = int(row[0]) if row else 0
        return f"🎫 Your Luxe Tickets: {tickets:,}"
    except Exception:
        return "🎫 Use !luxeshop to check your Luxe Tickets."


def _get_level(user_id: str) -> str:
    try:
        profile = db.get_profile(user_id)
        if profile:
            level = profile.get("level", 1)
            xp = profile.get("xp", 0)
            return f"📊 Your level: {level} | XP: {xp:,}"
        return "📊 Use !profile to check your level and XP."
    except Exception:
        return "📊 Use !profile to check your level."


def _get_summary(user_id: str, username: str) -> str:
    try:
        coins = db.get_balance(user_id)
        profile = db.get_profile(user_id)
        level = profile.get("level", "?") if profile else "?"
        return f"👤 {username} — Level {level} | 🪙 {coins:,} ChillCoins\nUse !profile for full stats."
    except Exception:
        return "👤 Use !profile to see your full stats."


def get_player_own_info(user: "User", text: str) -> str:
    """Return the requesting player's own private info based on what they asked for."""
    low = text.lower()

    # Check if asking about another player → deny
    other = _extract_other_username(text)
    if other and other != user.username.lower():
        return _DENY_OTHER

    # Route to specific data
    if re.search(r"\b(luxe|ticket)\b", low):
        return _get_luxe_tickets(user.id)
    if re.search(r"\b(coins?|balance|chillcoin)\b", low):
        return _get_chillcoins(user.id)
    if re.search(r"\b(level|xp|exp|experience|rank)\b", low):
        return _get_level(user.id)
    if re.search(r"\b(stats?|summary|info|status|profile)\b", low):
        return _get_summary(user.id, user.username)

    # Default: summary
    return _get_summary(user.id, user.username)


def deny_other_player_info() -> str:
    return _DENY_OTHER
