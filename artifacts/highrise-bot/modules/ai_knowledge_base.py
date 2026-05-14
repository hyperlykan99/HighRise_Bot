"""
modules/ai_knowledge_base.py — Static knowledge base for AceSinatra (3.3A rebuild).

Covers: Luxe Tickets, ChillCoins, mining, fishing, casino, missions,
        events, VIP, player guidance, and basic room overview.
All answers ≤ 220 chars for safe whisper delivery.
"""
from __future__ import annotations

_KB: dict[str, str] = {
    "luxe_tickets": (
        "🎫 Luxe Tickets are premium reward tickets for Luxe Shop items, "
        "VIP perks, and special rewards. Earn them from events, seasons, "
        "and selected activities."
    ),
    "chillcoins": (
        "🪙 ChillCoins are the main room currency. Earn them by mining, "
        "fishing, completing missions, daily rewards, and casino games."
    ),
    "mining": (
        "⛏️ Mining earns 🪙 ChillCoins and rare ores. "
        "Type !mine to start. Use !automine for auto sessions. "
        "Check !mineinv for your ores and !orelist for ore info."
    ),
    "fishing": (
        "🎣 Fishing earns rewards through relaxing gameplay. "
        "Type !fish to start. Use !autofish for auto sessions. "
        "Check !fishinv for your catches."
    ),
    "casino": (
        "🎰 Casino includes Blackjack (!bet) and Poker (!poker). "
        "Play responsibly. Odds are set by the owner and are fixed."
    ),
    "missions": (
        "📋 Daily and weekly missions earn 🪙 by completing room activities. "
        "Type !missions to see yours. Complete them daily for best rewards."
    ),
    "events": (
        "🎉 Events are limited-time activities with bonus rewards. "
        "Type !events to see what's active right now."
    ),
    "vip": (
        "💎 VIP gives special perks like extended automine/autofish sessions. "
        "Check the Luxe Shop with !luxeshop or type !vip for details."
    ),
    "player_guidance": (
        "🎯 Try this next: check !missions, then !mine or !fish for 🪙, "
        "join active !events for bonuses, or browse !luxeshop. "
        "Type !daily to claim your daily reward!"
    ),
    "room_overview": (
        "🏠 ChillTopia has: ⛏️ Mining, 🎣 Fishing, 🎰 Casino, "
        "📋 Missions, 🎉 Events, 👤 Profiles, 💎 VIP. "
        "Type !help or !start to begin."
    ),
    "daily": (
        "🎁 Claim your daily reward with !daily. "
        "Log in every day for streak bonuses and extra 🪙 ChillCoins."
    ),
    "profile": (
        "👤 Your profile shows your level, rep, stats, and badges. "
        "Type !profile to see yours, or !profile [username] to view others."
    ),
    "seasons": (
        "📅 Seasons are limited-time progression tracks with exclusive "
        "rewards. Check !events or ask staff for current season info."
    ),
}


def get_answer(topic: str) -> str | None:
    """Return a knowledge base answer for the given topic key, or None."""
    return _KB.get(topic)


def get_all_topics() -> list[str]:
    return list(_KB.keys())
