"""
modules/room_assistant.py
--------------------------
Friendly room assistant for ChillTopiaMC (host bot).

Features:
- Greeting detection with 60s per-user cooldown (public chat reply)
- Room guide Q&A with 30s per-user cooldown (public chat reply)
- Unknown command handler with fuzzy suggestion + 3s cooldown (whisper)

Rules:
- All responses ≤ 249 chars.
- No slash commands in output.
- Only active when BOT_MODE is "host" or "all".
- Greetings and Q&A go to public chat (short and useful).
- Unknown command suggestions are whispered.
- AI/known commands are never intercepted here.
"""

from __future__ import annotations

import re
import time
from difflib import get_close_matches

from highrise import BaseBot, User


# ---------------------------------------------------------------------------
# Cooldown tables  (user_id → last_reply monotonic time)
# ---------------------------------------------------------------------------

_GREET_CD:   dict[str, float] = {}
_GUIDE_CD:   dict[str, float] = {}
_UNKNOWN_CD: dict[str, float] = {}

_GREET_SECS   = 60.0
_GUIDE_SECS   = 30.0
_UNKNOWN_SECS =  3.0


def _on_cd(table: dict[str, float], uid: str, secs: float) -> bool:
    return (time.monotonic() - table.get(uid, 0.0)) < secs


def _set_cd(table: dict[str, float], uid: str) -> None:
    table[uid] = time.monotonic()


# ---------------------------------------------------------------------------
# Greeting patterns + rotating replies
# ---------------------------------------------------------------------------

_GREET_RE = re.compile(
    r"""^(
        hi+|hello+|hey+|yo+|hola|sup|what'?s\s+up|
        howdy|greetings|ello+|ey+|ayy+|
        good\s+(?:morning|afternoon|evening|day)
    )\W*$""",
    re.IGNORECASE | re.VERBOSE,
)

_GREET_REPLIES: tuple[str, ...] = (
    "👋 Welcome to ChillTopia! Type !help games to play, !tele list to explore, or ask me what you can do here.",
    "Hey! 👋 You can mine, fish, play casino games, or check VIP perks. Type !help games to start.",
    "👋 Hey there! Mine, fish, play blackjack/poker, and more. Type !help games or !help mining to begin.",
)
_greet_idx = 0


def _next_greet() -> str:
    global _greet_idx
    msg = _GREET_REPLIES[_greet_idx % len(_GREET_REPLIES)]
    _greet_idx += 1
    return msg


# ---------------------------------------------------------------------------
# Room guide Q&A table
# pattern → short public chat response (≤ 220 chars each)
# ---------------------------------------------------------------------------

_GUIDE: list[tuple[re.Pattern, str]] = [
    # General overview
    (re.compile(
        r"what\s+(?:can\s+i\s+do|to\s+do|is\s+this\s+room|games?\s+are\s+(?:here|available)|do\s+i\s+do\s+here)",
        re.I,
     ),
     "🎮 In ChillTopia you can mine, fish, play blackjack/poker, join mini games, and use party tips during events. Start with !help games."),

    # Mining
    (re.compile(r"how\s+(?:do\s+i|to)\s+mine\b|how\s+(?:does\s+)?mining\s+work", re.I),
     "⛏️ Type !mine to mine once. Use !automine for auto mining, !mineinv for your inventory, and !orechances for drop odds."),

    # Fishing
    (re.compile(r"how\s+(?:do\s+i|to)\s+fish\b|how\s+(?:does\s+)?fishing\s+work", re.I),
     "🎣 Type !fish to fish once. Use !autofish for auto fishing, !fishinv for your inventory, and !fishchances for odds."),

    # Blackjack
    (re.compile(r"how\s+(?:do\s+i\s+play|to\s+play)\s+blackjack|how\s+to\s+play\s+bj\b", re.I),
     "🃏 Blackjack: !bet [amount] to start, !hit to draw, !stand to hold. Use !bjhelp for all commands."),

    # Poker
    (re.compile(r"how\s+(?:do\s+i\s+play|to\s+play)\s+poker", re.I),
     "♠️ Poker: Use !poker or !pokerhelp for rules and commands. Check your balance with !balance."),

    # VIP
    (re.compile(r"how\s+(?:do\s+i\s+(?:get|buy)|to\s+get)\s+vip|what\s+is\s+vip|vip\s+perks", re.I),
     "💎 VIP gives perks like longer automine/autofish. Use !vipperks to see what you get."),

    # Notifications / subscribe
    (re.compile(r"how\s+(?:do\s+i\s+)?subscri|get\s+notif|how\s+notif|room\s+notif", re.I),
     "🔔 To receive room notifications, DM me !subscribe. Use !notif to manage categories and !unsub to stop."),

    # Teleport
    (re.compile(r"how\s+(?:do\s+i\s+)?(?:teleport|tele)\b|where\s+can\s+i\s+go", re.I),
     "🏠 Use !tele list to see all spots. Then type !tele [spot], example: !tele games."),

    # Party mode / party tip
    (re.compile(r"what\s+is\s+party\s+mode|party\s+tip\b|how\s+(?:does\s+)?party\s+(?:mode|tip)", re.I),
     "🎉 Party Mode lets approved Party Tippers send gold from the Party Wallet. Use !help party for details."),

    # Shop / how to buy
    (re.compile(r"how\s+(?:do\s+i\s+)?(?:use|open)\s+(?:the\s+)?shop|how\s+(?:do\s+i\s+)?buy\s+items?", re.I),
     "🛍️ Use !shop to browse items and !buy [item_id] to purchase. Use !inv to view what you own."),

    # Restricted: give/send everyone gold
    (re.compile(r"(?:give|send)\s+everyone\s+gold|rain\s+gold\s+on|goldrain\s+all", re.I),
     "I can't send gold from chat. Gold tools use !goldtip, or party tips use !tip during Party Mode."),

    # Restricted: grant VIP
    (re.compile(r"(?:give|grant)\s+\w+\s+vip", re.I),
     "🔒 VIP grants are owner tools. Use !addvip [username]."),
]


# ---------------------------------------------------------------------------
# Fuzzy suggestion helper
# ---------------------------------------------------------------------------

_PUBLIC_CMDS: frozenset[str] = frozenset({
    # Economy
    "balance", "bank", "send", "transactions",
    # Daily / quests
    "daily", "quests", "dailyquests", "claimquest",
    # Leaderboard
    "leaderboard", "xpleaderboard",
    # Profile
    "profile", "me", "stats", "badges", "titles", "privacy",
    # Shop / items
    "shop", "buy", "inv", "inventory", "myitems",
    # VIP
    "vip", "vipperks", "vipstatus",
    # Mining
    "mine", "automine", "mineinv", "mineshop", "mineprofile",
    "orechances", "orechance", "orelist", "minelb", "ores",
    "sellores", "minehelp",
    # Fishing
    "fish", "autofish", "fishinv", "fishhelp", "fishrarity",
    "fishchances", "fishlist", "fishprices", "fishlb", "sellfish",
    # Blackjack / RBJ
    "blackjack", "bet", "bj", "bjhelp", "blimits", "bstats", "bjrules",
    # Poker
    "poker", "pokerhelp", "pokerstats",
    # Casino
    "casino", "casinodash",
    # Teleport
    "tele", "tp", "spawns",
    # Help / navigation
    "rules", "help", "start", "guide", "howtoplay", "games",
    # Notifications
    "notif", "notifon", "notifoff", "subscribe", "sub", "unsub",
    # Events
    "events", "eventstatus", "eventhelp",
    # Social
    "achievements", "reputation", "rep",
    # Misc
    "suggest", "bugreport", "dashboard", "tips", "tiprate",
})


def get_fuzzy_suggestion(cmd: str) -> str | None:
    """
    Return the closest public command to cmd, or None.
    Strategy:
      1. Exact prefix match — if cmd is a unique unambiguous prefix of exactly
         one known command (and the target is meaningfully longer), suggest it.
         Handles short abbreviations like 'bal' → 'balance'.
      2. Difflib fuzzy match (cutoff 0.72) for near-typos.
    Never suggests hidden, deprecated, or staff-only commands.
    """
    candidates = sorted(_PUBLIC_CMDS)

    # ── Step 1: prefix match ────────────────────────────────────────────────
    if len(cmd) >= 2:
        prefix_hits = [c for c in candidates if c.startswith(cmd) and c != cmd]
        if len(prefix_hits) == 1:
            return prefix_hits[0]
        # If multiple prefix hits, pick the shortest one only when it is
        # unambiguously the closest (i.e. no other hit starts the same way).
        if len(prefix_hits) == 2:
            # e.g. 'bala' → ['balance'] — accept if both share the same stem
            if prefix_hits[0].startswith(prefix_hits[1]) or prefix_hits[1].startswith(prefix_hits[0]):
                return min(prefix_hits, key=len)

    # ── Step 2: difflib fuzzy match ─────────────────────────────────────────
    matches = get_close_matches(cmd, candidates, n=1, cutoff=0.72)
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Unknown command handler (called from main.py else block)
# ---------------------------------------------------------------------------

async def handle_unknown_command(bot: BaseBot, user: User, cmd: str) -> None:
    """
    Whispers a fuzzy suggestion for truly unknown commands.
    Includes a 3s per-user cooldown to prevent spam.
    """
    if _on_cd(_UNKNOWN_CD, user.id, _UNKNOWN_SECS):
        return
    _set_cd(_UNKNOWN_CD, user.id)
    suggestion = get_fuzzy_suggestion(cmd)
    if suggestion:
        await bot.highrise.send_whisper(
            user.id,
            f"⚠️ Unknown command.\nDid you mean !{suggestion}?"
        )
    else:
        await bot.highrise.send_whisper(
            user.id,
            "⚠️ Unknown command.\nTry !help, !help games, !tele list, or !shop."
        )


# ---------------------------------------------------------------------------
# Main entry point — called from on_chat for non-! non-/ messages
# ---------------------------------------------------------------------------

async def handle_room_assistant_chat(bot: BaseBot, user: User, message: str) -> bool:
    """
    Handles greetings and room-guide Q&A for non-command chat messages.
    Returns True if handled (caller should return without passing to try_direct_answer).
    Only active when BOT_MODE is "host" or "all".
    """
    try:
        from config import BOT_MODE
    except Exception:
        return False
    if BOT_MODE not in ("host", "all"):
        return False

    low = message.strip().lower()
    uid = user.id

    # ── Greeting ──────────────────────────────────────────────────────────────
    if _GREET_RE.match(low):
        if not _on_cd(_GREET_CD, uid, _GREET_SECS):
            _set_cd(_GREET_CD, uid)
            await bot.highrise.chat(_next_greet())
        return True

    # ── Room guide Q&A ────────────────────────────────────────────────────────
    if _on_cd(_GUIDE_CD, uid, _GUIDE_SECS):
        return False
    for pattern, answer in _GUIDE:
        if pattern.search(low):
            _set_cd(_GUIDE_CD, uid)
            await bot.highrise.chat(answer[:249])
            return True

    return False
