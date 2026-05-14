"""
modules/ai_reasoning_templates.py — Pre-written AI response templates (3.3B).

Short, human-like template strings used across the AI system.
All templates are ≤ 249 chars.
"""
from __future__ import annotations

# ── Status & help ─────────────────────────────────────────────────────────────
AI_STATUS = (
    "🤖 ChillTopiaMC AI is online! Ask me about guides, events, mining, fishing, "
    "casino, Luxe Tickets, science, geography, or global time."
)

AI_HELP = (
    "Say 'ai [question]'. Examples:\n"
    "ai what should I do next?\n"
    "ai explain Luxe Tickets\n"
    "ai what time is it in Japan?\n"
    "ai report bug: fishing broken"
)

AI_WELCOME = (
    "👋 Hi! I'm ChillTopiaMC AI.\n"
    "Ask me anything about ChillTopia, global time, science, math, and more.\n"
    "Type 'ai help' for examples."
)

# ── Reply mode ────────────────────────────────────────────────────────────────
REPLY_MODE_VIEW  = "📡 AI reply mode is currently '{mode}'."
REPLY_MODE_OWNER_ONLY = "🔒 Only the owner can change AI reply mode."
REPLY_MODE_SAME  = "✅ AI reply mode is already '{mode}'."
REPLY_MODE_DONE  = "✅ AI reply mode updated to '{mode}'."

# ── Refusals ──────────────────────────────────────────────────────────────────
CANNOT_GRANT_CURRENCY = (
    "🚫 I can't grant currency through AI. "
    "Earn 🎫 Luxe Tickets from events, seasons, and selected rewards."
)
CANNOT_SHOW_OTHERS_DATA = (
    "🔒 I can't show another player's private balance or stats."
)
CANNOT_WIPE = (
    "🚫 I can't wipe player data through AI. That action is blocked for safety."
)
DUPLICATE_BOTS = (
    "🚫 Only ChillTopiaMC should answer AI messages to prevent duplicate spam."
)
WHISPER_UNAVAILABLE = (
    "ℹ️ I need to whisper that because it has private info, but whisper isn't available right now."
)

# ── Debug summary template ─────────────────────────────────────────────────────
DEBUG_TEMPLATE = (
    "🔧 AI Debug:\n"
    "Host: {host} | Mode: {reply_mode}\n"
    "Rate: {rate_users} tracked | Memory: {memory} users\n"
    "Modules: 26+ | Live: not connected\n"
    "Pending confirms: {pending}"
)

# ── Moderation ────────────────────────────────────────────────────────────────
MOD_NO_AUTO = "I won't act automatically — use staff commands directly."

# ── Generic fallback ──────────────────────────────────────────────────────────
UNKNOWN_FALLBACK = (
    "🤖 I'm ChillTopiaMC AI! I can help with:\n"
    "• Room guides, mining, fishing, casino, events\n"
    "• Science, geography, fun facts, math, translations\n"
    "• Date/time anywhere in the world\n"
    "Say 'ai help' for examples."
)
