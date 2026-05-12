"""
modules/room_assistant.py
--------------------------
Friendly room assistant for ChillTopiaMC (host bot).

Behaviour model (Update 3.0M Hotfix v2):
- No per-user or per-topic cooldowns for Q&A or greetings.
  Strict intent matching prevents unwanted replies instead.
- Public chat: replies only when a Q&A pattern clearly matches, or when a
  restricted action is detected. Greetings are NOT sent to public chat
  (AI intercept already handles bot-name mentions; plain "hi" is casual).
- Whisper: more permissive — replies to greetings, Q&A, and restricted
  requests, all via send_whisper (never leaks to public).
- Known bot accounts are silently ignored.
- Unknown command handler keeps a 3 s per-user cooldown to prevent spam.
- All messages ≤ 220 chars (well within the 249 cap).
- No slash commands in any output.
"""

from __future__ import annotations

import re
import time
from difflib import get_close_matches

from highrise import BaseBot, User


# ---------------------------------------------------------------------------
# Bot-exclusion list
# Hardcoded as a safe fallback; supplemented by a live DB cache (see below).
# ---------------------------------------------------------------------------

_HARDCODED_BOT_NAMES: frozenset[str] = frozenset({
    "bankingbot", "greatestprospector", "masterangler",
    "acesinatra", "chipsoprano", "keanuashield", "keanuashield",
    "dj_dudu", "chilltopiamc", "arcadiaradio",
})

# Runtime cache — populated lazily from bot_instances table.
_bot_username_cache: set[str] = set()
_bot_cache_loaded: bool = False


def _load_bot_cache() -> None:
    global _bot_username_cache, _bot_cache_loaded
    if _bot_cache_loaded:
        return
    try:
        import database as db
        instances = db.get_bot_instances()
        _bot_username_cache = {
            (r.get("bot_username") or "").lower()
            for r in instances
            if r.get("bot_username")
        }
    except Exception:
        pass
    finally:
        _bot_cache_loaded = True


def _is_bot_user(username: str) -> bool:
    """Return True if `username` belongs to a known bot account."""
    low = username.lower()
    if low in _HARDCODED_BOT_NAMES:
        return True
    _load_bot_cache()
    return low in _bot_username_cache


# ---------------------------------------------------------------------------
# Unknown-command cooldown (only cooldown kept — prevents whisper spam)
# ---------------------------------------------------------------------------

_UNKNOWN_CD: dict[str, float] = {}
_UNKNOWN_SECS = 3.0


def _on_cd(table: dict[str, float], uid: str, secs: float) -> bool:
    return (time.monotonic() - table.get(uid, 0.0)) < secs


def _set_cd(table: dict[str, float], uid: str) -> None:
    table[uid] = time.monotonic()


# ---------------------------------------------------------------------------
# Reply helper — routes to whisper or public chat based on source
# ---------------------------------------------------------------------------

async def send_assistant_reply(
    bot: BaseBot,
    user: User,
    msg: str,
    source: str,          # "whisper" | "chat"
) -> None:
    """
    Send `msg` back through the same channel the user used.
    Caps at 220 chars. Silently logs errors without crashing.
    """
    msg = msg[:220]
    try:
        if source == "whisper":
            await bot.highrise.send_whisper(user.id, msg)
        else:
            await bot.highrise.chat(msg)
    except Exception as _e:
        print(f"[ROOM_ASSIST] reply failed (source={source}): {_e!r}")


# ---------------------------------------------------------------------------
# Greeting patterns + rotating replies
# Used only for whisper responses (public greetings handled by AI intercept).
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
    "👋 Hey there! Mine, fish, play blackjack/poker and more. Type !help games or !help mining to begin.",
)
_greet_idx = 0


def _next_greet() -> str:
    global _greet_idx
    msg = _GREET_REPLIES[_greet_idx % len(_GREET_REPLIES)]
    _greet_idx += 1
    return msg


# ---------------------------------------------------------------------------
# Room guide Q&A table  (topic_key, pattern, answer)
# Patterns act as the intent gate — casual messages never match these.
# ---------------------------------------------------------------------------

_GUIDE: list[tuple[str, re.Pattern, str]] = [
    # ── General overview ─────────────────────────────────────────────────────
    ("room_help",
     re.compile(
         r"what\s+(?:can\s+i\s+do|to\s+do|is\s+this\s+room"
         r"|games?\s+are\s+(?:here|available)|do\s+i\s+do\s+here)",
         re.I),
     "🎮 In ChillTopia you can mine, fish, play blackjack/poker, join mini games, and use party tips during events. Start with !help games."),

    # ── Mining ───────────────────────────────────────────────────────────────
    ("mining",
     re.compile(
         r"how\s+(?:do\s+i\s+|to\s+)?mine\b"
         r"|how\s+(?:does\s+)?mining\s+(?:work|help)"
         r"|\bmine\s+help\b|\bmining\s+help\b"
         r"|\bhelp\s+(?:with\s+)?mining\b",
         re.I),
     "⛏️ Type !mine to mine once. Use !automine for auto mining, !mineinv for inventory, and !minechances for odds."),

    # ── Fishing ──────────────────────────────────────────────────────────────
    ("fishing",
     re.compile(
         r"how\s+(?:do\s+i\s+|to\s+)?fish\b"
         r"|how\s+(?:does\s+)?fishing\s+(?:work|help)"
         r"|\bfish\s+help\b|\bfishing\s+help\b"
         r"|\bhelp\s+(?:with\s+)?fishing\b",
         re.I),
     "🎣 Type !fish to fish once. Use !autofish for auto fishing, !fishinv for inventory, and !fishchances for odds."),

    # ── Blackjack ────────────────────────────────────────────────────────────
    ("blackjack",
     re.compile(
         r"how\s+(?:do\s+i\s+play|to\s+play)\s+blackjack"
         r"|how\s+to\s+play\s+bj\b",
         re.I),
     "🃏 Blackjack: !bet [amount] to start, !hit to draw, !stand to hold. Use !bjhelp for all commands."),

    # ── Poker ────────────────────────────────────────────────────────────────
    ("poker",
     re.compile(r"how\s+(?:do\s+i\s+play|to\s+play)\s+poker", re.I),
     "♠️ Poker: Use !poker or !pokerhelp for rules and commands. Check your balance with !balance."),

    # ── VIP ──────────────────────────────────────────────────────────────────
    ("vip",
     re.compile(
         r"how\s+(?:do\s+i\s+(?:get|buy)|to\s+(?:get|buy))\s+vip"
         r"|what\s+is\s+vip"
         r"|\bvip\s+(?:perks|help|info|benefits|work[s]?)\b"
         r"|\bhow\s+(?:does\s+)?vip\s+work",
         re.I),
     "💎 VIP gives convenience perks like longer automine/autofish. Use !vip or !vipperks to learn more."),

    # ── Notifications ─────────────────────────────────────────────────────────
    ("notifications",
     re.compile(
         r"how\s+(?:do\s+i\s+)?subscri"
         r"|get\s+notif|how\s+notif|room\s+notif"
         r"|\bnotif\s+help\b|\bnotifications?\b",
         re.I),
     "🔔 To receive room notifications, DM me !subscribe. Use !notif to manage categories and !unsub to stop."),

    # ── Teleport ─────────────────────────────────────────────────────────────
    ("teleport",
     re.compile(
         r"how\s+(?:do\s+i\s+)?(?:teleport|tele)\b"
         r"|where\s+can\s+i\s+go"
         r"|\bteleport\s+help\b",
         re.I),
     "🏠 Use !tele list to see all spots. Then type !tele [spot], example: !tele games."),

    # ── Party mode ────────────────────────────────────────────────────────────
    ("party",
     re.compile(
         r"what\s+is\s+party\s+mode"
         r"|party\s+tip\b"
         r"|how\s+(?:does\s+)?party\s+(?:tip[s]?\s+work|mode)",
         re.I),
     "🎉 Party Mode lets approved Party Tippers send gold from the Party Wallet. Use !help party for details."),

    # ── Shop ─────────────────────────────────────────────────────────────────
    ("shop",
     re.compile(
         r"how\s+(?:do\s+i\s+)?(?:use|open)\s+(?:the\s+)?shop"
         r"|how\s+(?:do\s+i\s+)?buy\s+items?"
         r"|\bwhere\s+(?:do\s+i\s+)?buy\b"
         r"|\bshop\s+help\b",
         re.I),
     "🛍️ Use !shop to browse items and !buy [item_id] to buy. Use !inv to view your items."),

    # ── Restricted: give/send everyone gold ───────────────────────────────────
    ("restricted_gold",
     re.compile(
         r"(?:give|send)\s+everyone\s+gold"
         r"|rain\s+gold\s+on|goldrain\s+all"
         r"|start\s+gold\s+rain",
         re.I),
     "I can't do that from chat. Owner tools are protected."),

    # ── Restricted: grant VIP / admin / balance ───────────────────────────────
    ("restricted_action",
     re.compile(
         r"(?:give|grant)\s+(?:me|\w+)\s+vip"
         r"|make\s+me\s+admin"
         r"|change\s+(?:my|the)?\s*balance"
         r"|summon\s+(?:all\s+)?bots?"
         r"|set\s+bot\s+spawn"
         r"|turn\s+stability\s+off"
         r"|fix\s+commands",
         re.I),
     "I can't do that from chat. Owner tools are protected."),
]


# ---------------------------------------------------------------------------
# Typo-tolerant topic detection
# Only fires on question-style messages (contains question word or ?).
# ---------------------------------------------------------------------------

_TYPO_TOPICS: list[tuple[list[str], str]] = [
    (["mine", "mining"],             "mining"),
    (["fish", "fishing"],            "fishing"),
    (["vip"],                        "vip"),
    (["blackjack", "bj"],            "blackjack"),
    (["poker"],                      "poker"),
    (["tele", "teleport"],           "teleport"),
    (["party"],                      "party"),
    (["shop", "buy"],                "shop"),
    (["notif", "subscribe", "sub"],  "notifications"),
]

_QUESTION_WORDS: frozenset[str] = frozenset({
    "how", "what", "where", "when", "help", "guide",
})
_SKIP_WORDS: frozenset[str] = _QUESTION_WORDS | {
    "the", "and", "for", "with", "from", "that", "this", "can",
}
_TYPO_CUTOFF = 0.75


def _topic_answer(topic: str) -> str | None:
    for t, _pat, ans in _GUIDE:
        if t == topic:
            return ans
    return None


def _typo_topic(low: str) -> tuple[str, str] | None:
    """
    Return (topic_key, answer) if a word in `low` is a near-typo for a topic
    keyword AND the message looks like a question (question word or ?).
    """
    words = re.findall(r"[a-z]+", low)
    if not any(w in _QUESTION_WORDS for w in words) and "?" not in low:
        return None
    all_keywords = [kw for kws, _ in _TYPO_TOPICS for kw in kws]
    for word in words:
        if len(word) < 3 or word in _SKIP_WORDS:
            continue
        matches = get_close_matches(word, all_keywords, n=1, cutoff=_TYPO_CUTOFF)
        if matches:
            for kws, topic in _TYPO_TOPICS:
                if matches[0] in kws:
                    ans = _topic_answer(topic)
                    if ans:
                        return topic, ans
    return None


# ---------------------------------------------------------------------------
# Fuzzy suggestion helper (unknown command handler)
# ---------------------------------------------------------------------------

_PUBLIC_CMDS: frozenset[str] = frozenset({
    "balance", "bank", "send", "transactions",
    "daily", "quests", "dailyquests", "claimquest",
    "leaderboard", "xpleaderboard",
    "profile", "me", "stats", "badges", "titles", "privacy",
    "shop", "buy", "inv", "inventory", "myitems",
    "vip", "vipperks", "vipstatus",
    "mine", "automine", "mineinv", "mineshop", "mineprofile",
    "minechances", "orechances", "orechance", "orelist", "minelb", "ores",
    "sellores", "minehelp",
    "fish", "autofish", "fishinv", "fishhelp", "fishrarity",
    "fishchances", "fishlist", "fishprices", "fishlb", "sellfish",
    "blackjack", "bet", "bj", "bjhelp", "blimits", "bstats", "bjrules",
    "poker", "pokerhelp", "pokerstats",
    "casino", "casinodash",
    "tele", "tp", "spawns",
    "rules", "help", "start", "guide", "howtoplay", "games",
    "notif", "notifon", "notifoff", "subscribe", "sub", "unsub",
    "events", "eventstatus", "eventhelp",
    "achievements", "reputation", "rep",
    "suggest", "bugreport", "dashboard", "tips", "tiprate",
})


def get_fuzzy_suggestion(cmd: str) -> str | None:
    """
    Return the closest public command to cmd (prefix or difflib), or None.
    Never suggests hidden, deprecated, or staff-only commands.
    """
    candidates = sorted(_PUBLIC_CMDS)
    if len(cmd) >= 2:
        hits = [c for c in candidates if c.startswith(cmd) and c != cmd]
        if len(hits) == 1:
            return hits[0]
        if len(hits) == 2 and (
            hits[0].startswith(hits[1]) or hits[1].startswith(hits[0])
        ):
            return min(hits, key=len)
    matches = get_close_matches(cmd, candidates, n=1, cutoff=0.72)
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Unknown command handler (called from main.py else block)
# ---------------------------------------------------------------------------

async def handle_unknown_command(bot: BaseBot, user: User, cmd: str) -> None:
    """
    Whispers a fuzzy suggestion for truly unknown commands.
    Keeps a 3 s per-user cooldown (only cooldown in this module).
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
# Public entry point 1 — called from on_chat for non-! non-/ messages
# ---------------------------------------------------------------------------

async def handle_room_assistant_chat(bot: BaseBot, user: User, message: str) -> bool:
    """
    Handles room-guide Q&A and restricted-action replies for PUBLIC chat.

    Intent is determined solely by pattern matching — casual messages that
    don't match a known Q&A or restricted pattern are silently ignored.
    Greetings ("hi", "hello") are NOT replied to in public chat; those are
    casual and the AI intercept already handles bot-name-mention cases.

    Returns True if the message was claimed (prevents try_direct_answer).
    Only active when BOT_MODE is "host" or "all".
    """
    try:
        from config import BOT_MODE
    except Exception:
        return False
    if BOT_MODE not in ("host", "all"):
        return False

    # Silently ignore messages from known bot accounts
    if _is_bot_user(user.username):
        return False

    low = message.strip().lower()

    # ── Q&A patterns ─────────────────────────────────────────────────────────
    for _topic, pattern, answer in _GUIDE:
        if pattern.search(low):
            await send_assistant_reply(bot, user, answer, "chat")
            return True

    # ── Typo-tolerant fallback ────────────────────────────────────────────────
    result = _typo_topic(low)
    if result:
        _topic_key, answer = result
        await send_assistant_reply(bot, user, answer, "chat")
        return True

    return False


# ---------------------------------------------------------------------------
# Public entry point 2 — called from on_whisper for non-command messages
# ---------------------------------------------------------------------------

async def handle_room_assistant_whisper(bot: BaseBot, user: User, message: str) -> None:
    """
    Handles greetings, room-guide Q&A, and restricted-action replies for
    WHISPER messages. Replies are always sent via send_whisper (never public).

    Whispers are more permissive — greetings are answered freely since the
    user deliberately directed the message at the bot.
    Only active when BOT_MODE is "host" or "all".
    """
    try:
        from config import BOT_MODE
    except Exception:
        return
    if BOT_MODE not in ("host", "all"):
        return

    if _is_bot_user(user.username):
        return

    low = message.strip().lower()

    # ── Greeting ──────────────────────────────────────────────────────────────
    if _GREET_RE.match(low):
        await send_assistant_reply(bot, user, _next_greet(), "whisper")
        return

    # ── Q&A patterns ─────────────────────────────────────────────────────────
    for _topic, pattern, answer in _GUIDE:
        if pattern.search(low):
            await send_assistant_reply(bot, user, answer, "whisper")
            return

    # ── Typo-tolerant fallback ────────────────────────────────────────────────
    result = _typo_topic(low)
    if result:
        _topic_key, answer = result
        await send_assistant_reply(bot, user, answer, "whisper")
