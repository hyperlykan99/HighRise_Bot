"""
modules/ai_public_knowledge.py — Public knowledge layer for ChillTopiaMC AI (3.3A).

All answers are safe for any player. No private data. No sensitive settings.
Answers ≤ 220 chars for safe whisper delivery.
"""
from __future__ import annotations

_PUBLIC_KB: dict[str, str] = {
    "luxe_tickets": (
        "🎫 Luxe Tickets are premium reward tickets for Luxe Shop items, "
        "VIP perks, and special rewards. Earn them from events, seasons, "
        "and selected activities."
    ),
    "chillcoins": (
        "🪙 ChillCoins are the main ChillTopia currency used for regular "
        "activities, games, mining, fishing, and progression."
    ),
    "mining": (
        "⛏️ Mining lets you earn 🪙 ChillCoins and rare ores. "
        "Type !mine to start. Use !automine for auto sessions. "
        "Check !mineinv for your ores and !orelist for ore details."
    ),
    "fishing": (
        "🎣 Fishing lets you catch fish and earn rewards. "
        "Type !fish to start. Use !autofish for auto sessions. "
        "Check !fishinv for your catches."
    ),
    "casino": (
        "🎰 Casino games include Blackjack (!bet) and Poker (!poker). "
        "Play responsibly. Odds and rewards are controlled by the owner."
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
        "💎 VIP gives special perks depending on the current Luxe Shop setup. "
        "Type !vip to see current VIP details or !luxeshop to browse."
    ),
    "player_guidance": (
        "🎯 Try this next: finish your daily missions, then !mine or !fish "
        "for 🪙 ChillCoins, check !events for bonuses, or browse !luxeshop. "
        "Type !daily to claim your daily reward!"
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
    "room_overview": (
        "🏠 ChillTopia has: ⛏️ Mining, 🎣 Fishing, 🎰 Casino, "
        "📋 Missions, 🎉 Events, 👤 Profiles, 💎 VIP. "
        "Type !help or !start to begin."
    ),
    "ai_help": (
        "💬 Talk to me by starting your message with 'ai'.\n"
        "Examples:\n"
        "• ai what should I do next?\n"
        "• ai explain Luxe Tickets\n"
        "• ai how do I mine?\n"
        "• ai what events are active?\n"
        "• ai what date is today?\n"
        "• ai report bug: fishing broken\n"
        "Owner/Admin: ai set VIP price to 600 tickets"
    ),
}

_CMD_TOPIC_MAP: dict[str, str] = {
    "mine": "mining", "automine": "mining", "mineinv": "mining",
    "minehelp": "mining", "orelist": "mining", "mineprofile": "mining",
    "fish": "fishing", "autofish": "fishing", "fishinv": "fishing",
    "fishhelp": "fishing", "fishprofile": "fishing",
    "luxeshop": "luxe_tickets", "tickets": "luxe_tickets",
    "casino": "casino", "bet": "casino", "poker": "casino",
    "events": "events", "event": "events", "startevent": "events",
    "missions": "missions", "daily": "daily",
    "profile": "profile", "vip": "vip",
    "chillcoins": "chillcoins", "coins": "chillcoins", "balance": "chillcoins",
}


def get_public_answer(topic: str) -> str | None:
    return _PUBLIC_KB.get(topic)


def get_cmd_topic_answer(cmd_name: str) -> str | None:
    topic = _CMD_TOPIC_MAP.get(cmd_name.lower().lstrip("!/"))
    return _PUBLIC_KB.get(topic) if topic else None


def get_welcome() -> str:
    return (
        "👋 Hi! Ask me anything about ChillTopia.\n"
        "Topics: missions, Luxe Tickets, mining, fishing, events, casino, VIP, or what to do next.\n"
        "Say 'ai help' for examples."
    )[:249]
