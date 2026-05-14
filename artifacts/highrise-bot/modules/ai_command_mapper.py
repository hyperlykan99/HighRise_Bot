"""
modules/ai_command_mapper.py — Natural language → command mapping (3.3F).

map_command(text) returns (command_key, args_list) or (None, None).
The whitelist registry defines permissions and confirmation requirements.
"""
from __future__ import annotations

import re

# ── Whitelist registry ────────────────────────────────────────────────────────
# Each entry: command_key → config dict
AI_COMMAND_WHITELIST: dict[str, dict] = {
    "balance": {
        "category":           "SAFE_PLAYER_DIRECT",
        "aliases":            ["bal", "coins", "wallet", "my coins", "my balance"],
        "requires_permission":"player",
        "requires_confirmation": False,
        "description":        "Check your coin balance",
    },
    "tickets": {
        "category":           "SAFE_PLAYER_DIRECT",
        "aliases":            ["luxe tickets", "ticket balance", "my tickets"],
        "requires_permission":"player",
        "requires_confirmation": False,
        "description":        "Check your Luxe Ticket balance",
    },
    "daily": {
        "category":           "SAFE_PLAYER_DIRECT",
        "aliases":            ["claim daily", "daily reward", "my daily"],
        "requires_permission":"player",
        "requires_confirmation": False,
        "description":        "Claim your daily reward",
    },
    "mine": {
        "category":           "SAFE_PLAYER_DIRECT",
        "aliases":            ["go mine", "start mining", "dig"],
        "requires_permission":"player",
        "requires_confirmation": False,
        "description":        "Mine for ores",
    },
    "fish": {
        "category":           "SAFE_PLAYER_DIRECT",
        "aliases":            ["go fish", "start fishing", "cast", "fishing"],
        "requires_permission":"player",
        "requires_confirmation": False,
        "description":        "Cast your fishing rod",
    },
    "profile": {
        "category":           "SAFE_PLAYER_DIRECT",
        "aliases":            ["my profile", "show profile", "view profile"],
        "requires_permission":"player",
        "requires_confirmation": False,
        "description":        "View your player profile",
    },
    "events": {
        "category":           "SAFE_PUBLIC_INFO",
        "aliases":            ["show events", "list events", "event list", "what events"],
        "requires_permission":"player",
        "requires_confirmation": False,
        "description":        "Show active/upcoming events",
    },
    "nextevent": {
        "category":           "SAFE_PUBLIC_INFO",
        "aliases":            ["next event", "when is next event", "upcoming event"],
        "requires_permission":"player",
        "requires_confirmation": False,
        "description":        "Show the next scheduled event",
    },
    "shop": {
        "category":           "SAFE_PLAYER_DIRECT",
        "aliases":            ["open shop", "show shop", "item shop"],
        "requires_permission":"player",
        "requires_confirmation": False,
        "description":        "Browse the item shop",
    },
    "luxeshop": {
        "category":           "SAFE_PLAYER_DIRECT",
        "aliases":            ["open luxe shop", "luxe shop", "premium shop", "luxe store"],
        "requires_permission":"player",
        "requires_confirmation": False,
        "description":        "Browse the Luxe/premium shop",
    },
    "vipstatus": {
        "category":           "SAFE_PLAYER_DIRECT",
        "aliases":            ["my vip", "vip status", "check vip", "am i vip"],
        "requires_permission":"player",
        "requires_confirmation": False,
        "description":        "Check your VIP status",
    },
    "buyvip": {
        "category":           "SAFE_PLAYER_DIRECT",
        "aliases":            ["buy vip", "get vip", "purchase vip", "subscribe vip"],
        "requires_permission":"player",
        "requires_confirmation": True,
        "description":        "Purchase VIP status",
    },
    "buy": {
        "category":           "SAFE_PLAYER_DIRECT",
        "aliases":            ["buy item", "purchase item", "get item"],
        "requires_permission":"player",
        "requires_confirmation": True,
        "description":        "Buy an item from the shop by number (e.g. buy item 2)",
    },
    "tele": {
        "category":           "SAFE_PLAYER_DIRECT",
        "aliases":            ["teleport me to", "tele me to", "take me to", "go to"],
        "requires_permission":"player",
        "requires_confirmation": False,
        "self_only":          True,
        "description":        "Teleport yourself to a saved spawn",
    },
    "mute": {
        "category":           "STAFF_DIRECT",
        "aliases":            ["mute player", "silence player"],
        "requires_permission":"staff",
        "requires_confirmation": True,
        "description":        "Mute a player (staff+)",
    },
    "warn": {
        "category":           "STAFF_DIRECT",
        "aliases":            ["warn player", "give warning"],
        "requires_permission":"staff",
        "requires_confirmation": True,
        "description":        "Warn a player (staff+)",
    },
    "startevent": {
        "category":           "ADMIN_CONFIRM",
        "aliases":            ["start event", "launch event", "begin event", "run event"],
        "requires_permission":"admin",
        "requires_confirmation": True,
        "description":        "Start a game event (admin+)",
    },
    "stopevent": {
        "category":           "ADMIN_CONFIRM",
        "aliases":            ["stop event", "end event", "cancel event"],
        "requires_permission":"admin",
        "requires_confirmation": True,
        "description":        "Stop the current event (admin+)",
    },
    "setvipprice": {
        "category":           "ADMIN_CONFIRM",
        "aliases":            ["set vip price", "change vip price", "vip price"],
        "requires_permission":"admin",
        "requires_confirmation": True,
        "description":        "Change the VIP price (admin+)",
    },
    "addcoins": {
        "category":           "OWNER_CONFIRM",
        "aliases":            ["add coins to", "give coins to"],
        "requires_permission":"owner",
        "requires_confirmation":   True,
        "blocked_if_economy_lock": True,
        "description":        "Add coins to a player (owner only)",
    },
    "setcoins": {
        "category":           "OWNER_CONFIRM",
        "aliases":            ["set coins for", "set coins to"],
        "requires_permission":"owner",
        "requires_confirmation":   True,
        "blocked_if_economy_lock": True,
        "description":        "Set a player's coins (owner only)",
    },
}


# ── Natural Language → Command patterns ───────────────────────────────────────
# Each tuple: (command_key, pattern, arg_extractor_fn | None)
# arg_extractor_fn(match, text) → list[str]

def _no_args(_m, _t):
    return []


def _buy_args(m, text):
    # "buy item 2", "buy #3", "purchase item 5" → ["2"], ["3"], ["5"]
    found = re.search(r"(?:item\s+|#\s*)?(\d+)", text)
    return [found.group(1)] if found else []


def _tele_args(m, text):
    # Extract spawn name from text: "tele me to bar" → ["bar"]
    found = re.search(
        r"\bto\s+(?:the\s+)?([a-z][\w\s]{0,25}?)\s*$",
        text, re.I,
    )
    if found:
        dest = found.group(1).strip().lower()
        for noise in ("room", "area", "spot", "zone", "place"):
            if dest.endswith(f" {noise}"):
                dest = dest[: -(len(noise) + 1)].strip()
        return [dest] if dest else []
    return []


def _mute_args(m, text):
    # "mute player for 5 minutes spam" or "mute @username spam"
    found = re.search(
        r"\bmute\s+@?(\w+)(?:\s+for\s+(\d+)\s*(?:min(?:utes?)?)?)?"
        r"(?:\s+(.+))?$",
        text, re.I,
    )
    if not found:
        return []
    player  = found.group(1) or ""
    mins    = found.group(2) or "5"
    reason  = (found.group(3) or "").strip() or "unspecified"
    return [player, mins, reason]


def _warn_args(m, text):
    found = re.search(r"\bwarn\s+@?(\w+)(?:\s+(.+))?$", text, re.I)
    if not found:
        return []
    return [found.group(1), (found.group(2) or "unspecified").strip()]


def _startevent_args(m, text):
    # "start mining rush for 1 hour" → ["mining_rush", "60"]
    # "start lucky_rush for 30 minutes" → ["lucky_rush", "30"]
    event_map = {
        "mining rush":    "mining_rush",
        "mining_rush":    "mining_rush",
        "lucky rush":     "lucky_rush",
        "lucky_rush":     "lucky_rush",
        "heavy ore":      "heavy_ore_rush",
        "ore value":      "ore_value_surge",
        "double xp":      "double_mxp",
        "mining haste":   "mining_haste",
        "legendary rush": "legendary_rush",
        "prismatic hunt": "prismatic_hunt",
        "exotic hunt":    "exotic_hunt",
    }
    low = text.lower()
    event_id = ""
    for phrase, eid in event_map.items():
        if phrase in low:
            event_id = eid
            break
    if not event_id:
        # Try bare event id
        found = re.search(r"\bstart\s+(?:event\s+)?(\w+)", low)
        event_id = found.group(1) if found else "mining_rush"

    # Duration
    dur_match = re.search(r"(\d+)\s*(hour|hr|minute|min)", low)
    if dur_match:
        val  = int(dur_match.group(1))
        unit = dur_match.group(2)
        mins = val * 60 if unit.startswith("h") else val
    else:
        mins = 60  # default

    return [event_id, str(mins)]


def _setvipprice_args(m, text):
    found = re.search(r"(\d[\d,]*)", text)
    return [found.group(1).replace(",", "")] if found else []


def _addcoins_args(m, text):
    found = re.search(
        r"\b(\d[\d,]*)\s+coins?\s+to\s+@?(\w+)"
        r"|\bto\s+@?(\w+)\s+(\d[\d,]*)\s+coins?",
        text, re.I,
    )
    if found:
        if found.group(1):
            return [found.group(2), found.group(1).replace(",", "")]
        return [found.group(3), found.group(4).replace(",", "")]
    return []


def _setcoins_args(m, text):
    found = re.search(
        r"\bset\s+@?(\w+)\s+(?:coins?\s+)?to\s+(\d[\d,]*)"
        r"|\bset\s+coins?\s+(?:for\s+)?@?(\w+)\s+(?:to\s+)?(\d[\d,]*)",
        text, re.I,
    )
    if found:
        if found.group(1):
            return [found.group(1), found.group(2).replace(",", "")]
        return [found.group(3), found.group(4).replace(",", "")]
    return []


_NL_PATTERNS: list[tuple[str, re.Pattern, callable]] = [
    # ── Teleport (must come before generic "to" patterns) ────────────────────
    ("tele", re.compile(
        r"\b(tele(?:port)?|take|bring|send)\s+(?:me\s+)?to\b",
        re.I,
    ), _tele_args),

    # ── Shop purchases ────────────────────────────────────────────────────────
    ("buyvip", re.compile(
        r"\b(buy|get|purchase|subscribe)\s+vip\b",
        re.I,
    ), _no_args),
    ("buy", re.compile(
        r"\b(buy|purchase|get)\s+(?:item\s+|#\s*)?(\d+)\b",
        re.I,
    ), _buy_args),

    # ── Staff moderation ──────────────────────────────────────────────────────
    ("mute", re.compile(r"\bmute\s+@?\w+", re.I), _mute_args),
    ("warn", re.compile(r"\bwarn\s+@?\w+", re.I), _warn_args),

    # ── Admin/Owner actions ───────────────────────────────────────────────────
    ("startevent", re.compile(
        r"\bstart\s+(?:a\s+)?(?:event|mining\s+rush|lucky\s+rush|heavy\s+ore|legendary\s+rush|prismatic\s+hunt|exotic\s+hunt)\b",
        re.I,
    ), _startevent_args),
    ("stopevent", re.compile(
        r"\b(stop|end|cancel)\s+(the\s+)?(?:current\s+)?event\b",
        re.I,
    ), _no_args),
    ("setvipprice", re.compile(
        r"\bset\s+vip\s+price\b|\bchange\s+vip\s+price\b|\bvip\s+price\s+to\s+\d",
        re.I,
    ), _setvipprice_args),
    ("addcoins", re.compile(
        r"\b(add|give)\s+\d[\d,]*\s+coins?\s+to\b",
        re.I,
    ), _addcoins_args),
    ("setcoins", re.compile(
        r"\bset\s+(coins?\s+for|@?\w+\s+(?:coins?\s+)?to)\b",
        re.I,
    ), _setcoins_args),

    # ── Player economy ────────────────────────────────────────────────────────
    ("balance", re.compile(
        r"\b(show|check|view|see|get|display)\s+(my\s+)?(balance|coins?|wallet|money)\b"
        r"|\bmy\s+(balance|coins?|wallet)\b"
        r"|\bhow\s+many\s+coins?\b",
        re.I,
    ), _no_args),
    ("tickets", re.compile(
        r"\b(show|check|view|how\s+many)\s+(my\s+)?(?:luxe\s+)?tickets?\b"
        r"|\bmy\s+(?:luxe\s+)?tickets?\b",
        re.I,
    ), _no_args),
    ("daily", re.compile(
        r"\b(claim|get|take|collect)\s+(my\s+)?daily\b"
        r"|\bmy\s+daily\b"
        r"|\bdaily\s+(reward|bonus)\b",
        re.I,
    ), _no_args),

    # ── Activities ───────────────────────────────────────────────────────────
    ("mine", re.compile(
        r"\bmine\s+(for\s+(me|us)|now)\b"
        r"|\b(go|start)\s+mine?(?:ing)?\b"
        r"|\bdig\s+for\s+me\b",
        re.I,
    ), _no_args),
    ("fish", re.compile(
        r"\bfish\s+(for\s+(me|us)|now)\b"
        r"|\b(go|start)\s+fish(?:ing)?\b"
        r"|\bcast\s+for\s+me\b",
        re.I,
    ), _no_args),

    # ── Info / browse ─────────────────────────────────────────────────────────
    ("profile", re.compile(
        r"\b(show|view|check|open|display)\s+(my\s+)?profile\b"
        r"|\bmy\s+profile\b",
        re.I,
    ), _no_args),
    ("events", re.compile(
        r"\b(show|list|view|display|check)\s+(all\s+)?events?\b"
        r"|\bwhat\s+events?\b"
        r"|\bevent\s+list\b",
        re.I,
    ), _no_args),
    ("nextevent", re.compile(
        r"\b(next|upcoming)\s+event\b"
        r"|\bwhen\s+is\s+(the\s+)?next\s+event\b",
        re.I,
    ), _no_args),
    ("shop", re.compile(
        r"\b(open|show|view|browse)\s+(the\s+)?(?:item\s+)?shop\b",
        re.I,
    ), _no_args),
    ("luxeshop", re.compile(
        r"\b(open|show|view|browse)\s+(the\s+)?(?:luxe|premium)\s+shop\b",
        re.I,
    ), _no_args),
    ("vipstatus", re.compile(
        r"\b(my\s+vip|check\s+vip|vip\s+status|am\s+i\s+vip)\b",
        re.I,
    ), _no_args),
]


def map_command(text: str) -> tuple[str | None, list[str] | None]:
    """
    Map natural language text to (command_key, args), or (None, None).
    Checks NL patterns first; falls back to alias matching.
    """
    low = text.strip().lower()

    # 1. NL pattern match
    for cmd_key, pattern, extractor in _NL_PATTERNS:
        m = pattern.search(low)
        if m:
            args = extractor(m, low)
            return cmd_key, args

    # 2. Alias match (case-insensitive exact)
    for cmd_key, cfg in AI_COMMAND_WHITELIST.items():
        for alias in cfg.get("aliases", []):
            if alias.lower() in low:
                return cmd_key, []

    return None, None


def get_command_config(cmd_key: str) -> dict | None:
    return AI_COMMAND_WHITELIST.get(cmd_key)
