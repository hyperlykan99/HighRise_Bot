"""
modules/ai_assistant.py
-----------------------
AI assistant layer — natural language command matching and confirmation system.

Supports:
- /ask, /ai, /assistant <message>
- Natural language triggers: "emceebot" word or @emceebot mention
- yes/no natural confirmation for pending actions
- Risk-classified command matching (SAFE / CONFIRM / ADMIN_CONFIRM / BLOCKED)
- Per-bot-mode personality responses
- Pending action storage in ai_pending_actions DB table

Intent categories (Parts 1–9):
  Mining, Economy/Bank, Shop, Games, Events, Room/Spawn,
  Teleport, Outfit, Profile, Poker Settings, Help

All messages ≤ 249 chars.  No external API calls — pattern-based matching only.
"""

from __future__ import annotations

import importlib
import inspect
import re
from dataclasses import dataclass

import database as db
from modules.permissions import is_admin, is_owner

# ---------------------------------------------------------------------------
# Risk level constants
# ---------------------------------------------------------------------------

SAFE          = "SAFE"
CONFIRM       = "CONFIRM"
ADMIN_CONFIRM = "ADMIN_CONFIRM"
BLOCKED       = "BLOCKED"

# ---------------------------------------------------------------------------
# Risk level table  (command → risk level)
# Unlisted commands default to SAFE.
# ---------------------------------------------------------------------------

_RISK: dict[str, str] = {
    # ── SAFE — informational; AI suggests the command but does not run it ──
    "help": SAFE, "mycommands": SAFE, "start": SAFE, "guide": SAFE,
    "bal": SAFE, "balance": SAFE, "bank": SAFE, "transactions": SAFE,
    "minehelp": SAFE, "mine": SAFE, "ores": SAFE, "tool": SAFE,
    "orebook": SAFE, "orestats": SAFE, "mineshop": SAFE,
    "pokerhelp": SAFE, "bjhelp": SAFE, "rbjhelp": SAFE,
    "shop": SAFE, "eventhelp": SAFE, "event": SAFE, "events": SAFE,
    "eventstatus": SAFE, "eventpoints": SAFE,
    "me": SAFE, "profile": SAFE, "stats": SAFE,
    "leaderboard": SAFE, "lb": SAFE, "daily": SAFE,
    "quests": SAFE, "questhelp": SAFE, "dailyquests": SAFE,
    "bothealth": SAFE, "modulehealth": SAFE, "botheartbeat": SAFE,
    "spawns": SAFE, "spawninfo": SAFE,
    "botoutfits": SAFE, "botoutfit": SAFE,
    "goto": SAFE, "aicapabilities": SAFE,
    # ── CONFIRM — ask user before executing ──────────────────────────────
    "send": CONFIRM, "buy": CONFIRM, "sellores": CONFIRM,
    "sellore": CONFIRM, "minebuy": CONFIRM, "equip": CONFIRM,
    "eventshop": CONFIRM, "buyevent": CONFIRM,
    "tpme": CONFIRM,
    # ── ADMIN_CONFIRM — admin/owner only + must confirm ───────────────────
    "setmaxsend": ADMIN_CONFIRM, "setsendlimit": ADMIN_CONFIRM,
    "setminsend": ADMIN_CONFIRM, "setnewaccountdays": ADMIN_CONFIRM,
    "setmindailyclaims": ADMIN_CONFIRM, "setminlevelsend": ADMIN_CONFIRM,
    "setmintotalearned": ADMIN_CONFIRM, "setsendtax": ADMIN_CONFIRM,
    "sethighriskblocks": ADMIN_CONFIRM,
    "setcoins": ADMIN_CONFIRM, "addcoins": ADMIN_CONFIRM,
    "removecoins": ADMIN_CONFIRM, "resetcoins": ADMIN_CONFIRM,
    "bankblock": ADMIN_CONFIRM, "bankunblock": ADMIN_CONFIRM,
    "startevent": ADMIN_CONFIRM, "stopevent": ADMIN_CONFIRM,
    "restartbot": ADMIN_CONFIRM, "pokerrefundall": ADMIN_CONFIRM,
    "resetpokerstats": ADMIN_CONFIRM, "resetbjstats": ADMIN_CONFIRM,
    "resetrbjstats": ADMIN_CONFIRM,
    # Room / spawn
    "setspawn": ADMIN_CONFIRM, "setspawncoords": ADMIN_CONFIRM, "delspawn": ADMIN_CONFIRM,
    # Teleport
    "tp": ADMIN_CONFIRM, "tphere": ADMIN_CONFIRM, "bring": ADMIN_CONFIRM,
    "bringall": ADMIN_CONFIRM, "tpall": ADMIN_CONFIRM,
    # Outfit
    "dressbot": ADMIN_CONFIRM, "copyoutfit": ADMIN_CONFIRM,
    "wearuseroutfit": ADMIN_CONFIRM, "savebotoutfit": ADMIN_CONFIRM,
    # Poker settings
    "poker": ADMIN_CONFIRM,
    "setpokerdailywinlimit": ADMIN_CONFIRM, "setpokerdailylosslimit": ADMIN_CONFIRM,
}


def _risk_for(cmd: str) -> str:
    return _RISK.get(cmd, SAFE)


# ---------------------------------------------------------------------------
# Intent category table  (command → category string)
# Used by /aidebug output.
# ---------------------------------------------------------------------------

_CATEGORY: dict[str, str] = {
    "minehelp": "mining", "mine": "mining", "ores": "mining",
    "tool": "mining", "orebook": "mining", "orestats": "mining",
    "mineshop": "mining", "sellores": "mining", "sellore": "mining", "minebuy": "mining",
    "send": "economy", "bal": "economy", "balance": "economy",
    "bank": "economy", "transactions": "economy", "daily": "economy",
    "leaderboard": "economy", "lb": "economy",
    "setmaxsend": "economy", "setsendlimit": "economy", "setminsend": "economy",
    "setsendtax": "economy", "sethighriskblocks": "economy",
    "setcoins": "admin", "addcoins": "admin", "removecoins": "admin",
    "resetcoins": "admin", "bankblock": "admin", "bankunblock": "admin",
    "shop": "shop", "buy": "shop", "equip": "shop",
    "pokerhelp": "games", "bjhelp": "games", "rbjhelp": "games",
    "eventhelp": "events", "event": "events", "events": "events",
    "eventstatus": "events", "eventpoints": "events",
    "startevent": "events", "stopevent": "events",
    "eventshop": "events", "buyevent": "events",
    "setspawn": "room", "setspawncoords": "room", "delspawn": "room", "spawns": "room",
    "spawninfo": "room",
    "tpme": "room", "tp": "room", "tphere": "room", "bring": "room",
    "bringall": "room", "tpall": "room", "goto": "room",
    "dressbot": "outfit", "copyoutfit": "outfit",
    "wearuseroutfit": "outfit", "savebotoutfit": "outfit",
    "botoutfits": "outfit", "botoutfit": "outfit",
    "me": "profile", "profile": "profile", "stats": "profile",
    "quests": "profile", "questhelp": "profile", "dailyquests": "profile",
    "poker": "poker",
    "setpokerdailywinlimit": "poker", "setpokerdailylosslimit": "poker",
    "help": "help", "mycommands": "help", "aicapabilities": "help",
    "bothealth": "system", "modulehealth": "system", "botheartbeat": "system",
}


# ---------------------------------------------------------------------------
# Intent result
# ---------------------------------------------------------------------------

@dataclass
class IntentResult:
    command:        str   # e.g. "send"
    args_str:       str   # e.g. "claire 100"  (space-sep, after the command word)
    human_readable: str   # e.g. "send 100 coins to @claire"
    risk_level:     str


# ---------------------------------------------------------------------------
# Blocked keyword guard — refuse requests that look dangerous or abusive
# ---------------------------------------------------------------------------

_BLOCKED_RE = re.compile(
    r"\b(api[_\-\s]?key|bot[_\-\s]?token|room[_\-\s]?id|drop\s+table|"
    r"delete\s+all\s+(user|token|coin|data)|wipe\s+.*(data|economy|all)|"
    r"reset\s+all\s+economy|bypass\s+perm|sql\s+inject|"
    r"fly\s*hack|speed\s*hack|wall\s*hack|cheat\s+mode|"
    r"make\s+me\s+(owner|admin)|clear\s+all\s+(coins|token)|"
    r"delete\s+all\s+tokens|grant\s+myself)\b",
    re.I,
)


def _is_blocked(text: str) -> bool:
    return bool(_BLOCKED_RE.search(text))


# ---------------------------------------------------------------------------
# Intent patterns
# Each entry: (compiled_regex, command, args_fn(match,text)->str, human_fn(match,text)->str)
# Evaluated top-to-bottom; first match wins.
# ---------------------------------------------------------------------------

def _k(v: str):
    """Constant-valued lambda for args_fn / human_fn."""
    return lambda m, t: v


_INTENTS: list[tuple] = [

    # ────────────────────────────────────────────────────────────────────────
    # MINING
    # ────────────────────────────────────────────────────────────────────────
    (re.compile(r"how.{0,20}(do\s+i\s+|can\s+i\s+|to\s+)?mine|mine\s*help|mining\s*help|help.*min[ei]ng?\b", re.I),
     "minehelp", _k(""), _k("show mining help")),

    (re.compile(r"^mine$|^(start|do)\s+min[ei]|mine\s+for\s+me|let\s+me\s+mine", re.I),
     "mine", _k(""), _k("mine for you")),

    (re.compile(r"(my\s+|show\s+|check\s+|view\s+)?(ores?\b|ore\s+list|ore\s+inv)", re.I),
     "ores", _k(""), _k("show your ores")),

    (re.compile(r"(my\s+|show\s+|check\s+)?(pick.?axe|mining\s+tool)\b|^tool$", re.I),
     "tool", _k(""), _k("show your mining tool")),

    (re.compile(r"sell\s+(my\s+|all\s+|the\s+)?ores?\b", re.I),
     "sellores", _k(""), _k("sell all your ores")),

    (re.compile(
        r"(buy|purchase|get)\s+.*(energy|fuel|mine\s*upgrade|pick.?axe|mining\s*item)"
        r"|mine.*(buy|shop|upgrade|store)", re.I),
     "minebuy", _k(""), _k("open the mining shop")),

    (re.compile(r"(mining\s+|mine\s+)?(shop|store|market)\s*$|^mineshop$", re.I),
     "mineshop", _k(""), _k("show the mining shop")),

    # ────────────────────────────────────────────────────────────────────────
    # SEND / TRANSFER  — checked BEFORE balance so "send 100 coins to X"
    # does NOT accidentally match the balance keyword in /bal pattern
    # ────────────────────────────────────────────────────────────────────────
    # Variant A: "send 1000 coins to testuser" / "transfer 500 to @Marion"
    (re.compile(
        r"(send|transfer|give|pay)\s+([\d,]+)\s*(coins?|tokens?)?\s*(?:to|for|->)\s+@?(\w+)",
        re.I),
     "send",
     lambda m, t: f"{m.group(4)} {m.group(2).replace(',', '')}",
     lambda m, t: f"send {m.group(2).replace(',', '')} coins to @{m.group(4)}"),

    # Variant B: "send testuser 1000 coins" / "pay @testuser 500"
    (re.compile(
        r"(send|transfer|give|pay)\s+@?([A-Za-z]\w*)\s+([\d,]+)\s*(coins?|tokens?)?",
        re.I),
     "send",
     lambda m, t: f"{m.group(2)} {m.group(3).replace(',', '')}",
     lambda m, t: f"send {m.group(3).replace(',', '')} coins to @{m.group(2)}"),

    # ────────────────────────────────────────────────────────────────────────
    # ECONOMY
    # ────────────────────────────────────────────────────────────────────────
    (re.compile(
        r"(show\s+|check\s+|see\s+|my\s+|what.?s\s+(my\s+)?)?"
        r"(balance|wallet|money)\b"
        r"|how\s+many\s+coins|how\s+much\s+(do\s+i|have\s+i)"
        r"|(show|check|my)\s+coins?\s*$", re.I),
     "bal", _k(""), _k("show your balance")),

    (re.compile(
        r"(my\s+|show\s+|check\s+)?"
        r"(bank(ing)?\b|bank\s+(info|details|stats|status))\s*$|^bank$", re.I),
     "bank", _k(""), _k("show your bank info")),

    (re.compile(
        r"(my\s+|show\s+|view\s+)?"
        r"(transactions?\b|transaction\s+history|tx\s+history)\s*$", re.I),
     "transactions", _k(""), _k("show your transaction history")),

    (re.compile(r"(my\s+|show\s+)?(daily\b|daily\s+(reward|bonus|claim))\s*$", re.I),
     "daily", _k(""), _k("claim your daily reward")),

    (re.compile(
        r"(show\s+|my\s+|check\s+)?(leaderboard|top\s+players|rankings?|leader\s*board)\s*$",
        re.I),
     "leaderboard", _k(""), _k("show the leaderboard")),

    # ────────────────────────────────────────────────────────────────────────
    # SHOP
    # ────────────────────────────────────────────────────────────────────────
    (re.compile(
        r"(show\s+|open\s+|view\s+|see\s+)?(badge|emoji\s+badge|badges).*(shop)?"
        r"|badge\s+shop|shop.*badges?", re.I),
     "shop", _k("badges"), _k("show the badge shop")),

    (re.compile(
        r"(show\s+|open\s+|view\s+|see\s+)?(titles?).*(shop)?|title\s+shop|shop.*titles?",
        re.I),
     "shop", _k("titles"), _k("show the title shop")),

    (re.compile(
        r"(open\s+|show\s+|go\s+to\s+|view\s+|see\s+)?(shop|store|market)\s*$|^shop$",
        re.I),
     "shop", _k(""), _k("open the shop")),

    (re.compile(r"(buy|purchase|get)\s+(badge|title)\s+(\w+)", re.I),
     "buy",
     lambda m, t: f"{m.group(2)} {m.group(3)}",
     lambda m, t: f"buy {m.group(2)} {m.group(3)}"),

    (re.compile(r"equip\s+(\w+)\b|wear\s+(\w+)\s+(badge|title)", re.I),
     "equip",
     lambda m, t: (m.group(1) or m.group(2) or "").strip(),
     lambda m, t: f"equip {m.group(1) or m.group(2)}"),

    # ────────────────────────────────────────────────────────────────────────
    # GAMES — help text
    # ────────────────────────────────────────────────────────────────────────
    (re.compile(
        r"how.{0,20}(play|start|join|do|use)\s+poker"
        r"|poker.{0,20}(help|rules?|guide|how)\b|poker\s+help", re.I),
     "pokerhelp", _k(""), _k("show poker help")),

    (re.compile(
        r"how.{0,20}(play|start|join|do)\s+black.?jack"
        r"|black.?jack.{0,20}(help|rules?|guide|how)\b|bj\s+help|bjhelp", re.I),
     "bjhelp", _k(""), _k("show blackjack help")),

    (re.compile(
        r"how.{0,20}(play|start|join)\s+realistic"
        r"|realistic.{0,20}(help|guide|rules?|how)\b|rbj\s+help|rbjhelp", re.I),
     "rbjhelp", _k(""), _k("show realistic blackjack help")),

    # ────────────────────────────────────────────────────────────────────────
    # EVENTS — specific event names first, then generic patterns
    # ────────────────────────────────────────────────────────────────────────
    # Specific event IDs (natural name → event_id)
    (re.compile(
        r"(start|turn\s+on|enable|begin|launch)\s+(double[\s_]xp|double[\s_]exp|2x[\s_]xp|2x[\s_]exp)\s*(event)?\b",
        re.I),
     "startevent", _k("double_xp"), _k("start Double XP event")),

    (re.compile(
        r"(start|turn\s+on|enable|begin|launch)\s+(double[\s_]coins?|2x[\s_]coins?)\s*(event)?\b",
        re.I),
     "startevent", _k("double_coins"), _k("start Double Coins event")),

    (re.compile(
        r"(start|turn\s+on|enable|begin|launch)\s+(casino[\s_]hour)\s*(event)?\b",
        re.I),
     "startevent", _k("casino_hour"), _k("start Casino Hour event")),

    (re.compile(
        r"(start|turn\s+on|enable|begin|launch)\s+(tax.?free[\s_]bank(ing)?|no[\s_]tax)\s*(event)?\b",
        re.I),
     "startevent", _k("tax_free_bank"), _k("start Tax-Free Banking event")),

    (re.compile(
        r"(start|turn\s+on|enable|begin|launch)\s+(trivia[\s_]party)\s*(event)?\b",
        re.I),
     "startevent", _k("trivia_party"), _k("start Trivia Party event")),

    (re.compile(
        r"(start|turn\s+on|enable|begin|launch)\s+(shop[\s_]sale)\s*(event)?\b",
        re.I),
     "startevent", _k("shop_sale"), _k("start Shop Sale event")),

    # Generic: "start event X" / "start X event"
    (re.compile(r"start\s+event\s+(\w+)", re.I),
     "startevent",
     lambda m, t: m.group(1).lower(),
     lambda m, t: f"start {m.group(1).lower()} event"),

    (re.compile(r"(start|begin|launch)\s+(a?n?\s*)?(\w+)\s+event\b", re.I),
     "startevent",
     lambda m, t: m.group(3).lower(),
     lambda m, t: f"start {m.group(3).lower()} event"),

    (re.compile(r"(stop|end|cancel|turn\s+off|disable)\s+(the\s+|current\s+)?(event|game)\b", re.I),
     "stopevent", _k(""), _k("stop the current event")),

    (re.compile(r"(events?\s*(help)?\s*$|what.*events?|show.*events?|event\s+(info|guide|help))", re.I),
     "eventhelp", _k(""), _k("show event help")),

    (re.compile(r"(current\s+|active\s+)?(event|events)\s+(status|now|active|running)", re.I),
     "eventstatus", _k(""), _k("show current event status")),

    # ────────────────────────────────────────────────────────────────────────
    # ROOM / SPAWN
    # ────────────────────────────────────────────────────────────────────────
    # List spawns
    (re.compile(
        r"(show|list|see|view)\s+(all\s+)?spawns?\b|what\s+spawns?\s+(are there|exist)\b"
        r"|spawns\s+(list|available)\b",
        re.I),
     "spawns", _k(""), _k("show all saved spawns")),

    # Save / create spawn at current position
    (re.compile(
        r"(save|create|set)\s+(this\s+|my\s+|current\s+)?(spot|pos|position|location|spawn)\s+as\s+(\w+)\b",
        re.I),
     "setspawn",
     lambda m, t: m.group(4).lower(),
     lambda m, t: f"save your current location as spawn '{m.group(4).lower()}'"),

    (re.compile(r"(create|set|add|make)\s+spawn\s+(\w+)\b", re.I),
     "setspawn",
     lambda m, t: m.group(2).lower(),
     lambda m, t: f"create spawn '{m.group(2).lower()}' at your position"),

    # ────────────────────────────────────────────────────────────────────────
    # TELEPORT — ordered most-specific first
    # ────────────────────────────────────────────────────────────────────────
    # Teleport everyone / all
    (re.compile(
        r"(teleport|tp)\s+(everyone|all\s+players|all)\s+to\s+(\w+)\b"
        r"|tpall\s+(\w+)\b",
        re.I),
     "tpall",
     lambda m, t: (m.group(3) or m.group(4)).lower(),
     lambda m, t: f"teleport everyone to spawn '{(m.group(3) or m.group(4)).lower()}'"),

    # Bring all
    (re.compile(r"(bring\s+all|bring\s+everyone|bringall)\b", re.I),
     "bringall", _k(""), _k("bring all players to your position")),

    # Teleport self to spawn: "teleport me to lounge" / "tp me to stage"
    (re.compile(r"(teleport|tp)\s+me\s+to\s+(\w+)\b|go\s+to\s+spawn\s+(\w+)\b", re.I),
     "tpme",
     lambda m, t: (m.group(2) or m.group(3)).lower(),
     lambda m, t: f"teleport you to spawn '{(m.group(2) or m.group(3)).lower()}'"),

    # Teleport user to @user (explicit @mention → goto + note)
    (re.compile(r"(teleport|tp|move)\s+@?(\w+)\s+to\s+@(\w+)\b", re.I),
     "goto",
     lambda m, t: m.group(3),
     lambda m, t: (f"go to @{m.group(3)}'s location "
                   f"(then use /tphere {m.group(2)} to bring them)")),

    # Bring user to me: "bring testuser to me" / "tphere testuser"
    (re.compile(r"(bring|tphere)\s+@?(\w+)(\s+to\s+(me|here))?\b", re.I),
     "tphere",
     lambda m, t: m.group(2).lstrip("@"),
     lambda m, t: f"bring @{m.group(2).lstrip('@')} to your position"),

    # Teleport user to spawn: "teleport testuser to lounge" / "tp testuser stage"
    (re.compile(r"(teleport|tp)\s+@?(\w+)\s+to\s+(\w+)\b", re.I),
     "tp",
     lambda m, t: f"{m.group(2).lstrip('@')} {m.group(3).lower()}",
     lambda m, t: f"teleport @{m.group(2).lstrip('@')} to spawn '{m.group(3).lower()}'"),

    # Go to user (teleport self to a user)
    (re.compile(r"(goto|go\s+to)\s+@?(\w+)\b|teleport\s+me\s+to\s+@(\w+)\b", re.I),
     "goto",
     lambda m, t: (m.group(2) or m.group(3)).lstrip("@"),
     lambda m, t: f"teleport you to @{(m.group(2) or m.group(3)).lstrip('@')}"),

    # ────────────────────────────────────────────────────────────────────────
    # OUTFIT
    # ────────────────────────────────────────────────────────────────────────
    # Copy user's outfit into a bot mode record
    (re.compile(
        r"copy\s+@?(\w+)[''s]*\s+outfit\s+to\s+(\w+)\b"
        r"|copy\s+@?(\w+)\s+to\s+(\w+)\b(?!.*spawn)",
        re.I),
     "copyoutfit",
     lambda m, t: f"{(m.group(1) or m.group(3)).lstrip('@')} {(m.group(2) or m.group(4)).lower()}",
     lambda m, t: (f"copy @{(m.group(1) or m.group(3)).lstrip('@')}'s outfit "
                   f"→ '{(m.group(2) or m.group(4)).lower()}' mode")),

    # Make bot wear user's outfit (wearuseroutfit)
    (re.compile(
        r"make\s+(\w+bot|\w+\s+bot)\s+wear\s+(my|@?(\w+))\s+outfit\b"
        r"|(?:have|get)\s+(\w+bot)\s+wear\s+@?(\w+)\b",
        re.I),
     "wearuseroutfit",
     lambda m, t: (m.group(3) or m.group(5) or "").lstrip("@"),
     lambda m, t: (f"make bot wear @{(m.group(3) or m.group(5) or 'your').lstrip('@')}'s outfit")),

    # Make bot wear MY outfit
    (re.compile(r"make\s+(\w+bot|\w+\s+bot)\s+wear\s+my\s+outfit\b", re.I),
     "wearuseroutfit",
     lambda m, t: "",   # args_str will be built from user.username at execution
     lambda m, t: "make the bot wear your outfit"),

    # Dress bot as mode: "dress SecurityBot as security"
    (re.compile(
        r"(dress|outfit)\s+(\w+bot|\w+\s+bot|\w+)\s+as\s+(\w+)\b"
        r"|make\s+(\w+bot)\s+look\s+like\s+(\w+)\b",
        re.I),
     "dressbot",
     lambda m, t: (m.group(3) or m.group(5) or "").lower(),
     lambda m, t: f"dress bot using saved '{(m.group(3) or m.group(5) or '?').lower()}' outfit"),

    # Save bot outfit as mode: "save SecurityBot outfit as security"
    (re.compile(
        r"save\s+(\w+bot|\w+\s+bot|\w+)[''s]*\s+outfit\s+(as|for)\s+(\w+)\b"
        r"|save\s+bot\s+outfit\s+(as|to)\s+(\w+)\b",
        re.I),
     "savebotoutfit",
     lambda m, t: (m.group(3) or m.group(5) or "").lower(),
     lambda m, t: f"save bot's current outfit as '{(m.group(3) or m.group(5) or '?').lower()}' mode"),

    # Show bot outfit status
    (re.compile(
        r"(show|list|view)\s+bot\s+(outfit|outfits|looks?)\b"
        r"|bot\s+outfit\s+(status|list|info)\b"
        r"|what.*(bot|bots?)\s+(wearing|dressed|outfit)\b",
        re.I),
     "botoutfits", _k(""), _k("show all bot saved outfits")),

    # ────────────────────────────────────────────────────────────────────────
    # PROFILE — specific user first, then generic "me"
    # ────────────────────────────────────────────────────────────────────────
    # Show specific user's profile (guard against "my", "me", "your")
    (re.compile(
        r"(show|view|see|check|display)\s+(?!my\b|me\b|your\b)@?([A-Za-z]\w+)[''s]*\s+"
        r"(profile|pinfo|whois|info)\b",
        re.I),
     "profile",
     lambda m, t: m.group(2).lstrip("@"),
     lambda m, t: f"show @{m.group(2).lstrip('@')}'s profile"),

    # /profile username explicit
    (re.compile(r"^profile\s+@?([A-Za-z]\w+)\b", re.I),
     "profile",
     lambda m, t: m.group(1).lstrip("@"),
     lambda m, t: f"show @{m.group(1).lstrip('@')}'s profile"),

    # Who is username
    (re.compile(r"(who\s+is|whois)\s+@?([A-Za-z]\w+)\b", re.I),
     "profile",
     lambda m, t: m.group(2).lstrip("@"),
     lambda m, t: f"show @{m.group(2).lstrip('@')}'s profile"),

    # Show specific user's stats
    (re.compile(
        r"(show|check|see)\s+(?!my\b|me\b|your\b)@?([A-Za-z]\w+)[''s]*\s+stats\b",
        re.I),
     "stats",
     lambda m, t: m.group(2).lstrip("@"),
     lambda m, t: f"show @{m.group(2).lstrip('@')}'s stats"),

    # Generic "my profile / my stats / me"
    (re.compile(
        r"(my\s+|show\s+|view\s+)?(profile|stats|profile\s+stats)\s*$|^(me|stats)$",
        re.I),
     "me", _k(""), _k("show your profile")),

    (re.compile(r"(check\s+|show\s+|my\s+)?(level|xp|experience)\s*$", re.I),
     "me", _k(""), _k("show your level and XP")),

    (re.compile(
        r"(show\s+|my\s+|check\s+)?(quests?|daily\s+quests?|weekly\s+quests?)\s*$",
        re.I),
     "quests", _k(""), _k("show your quests")),

    # ────────────────────────────────────────────────────────────────────────
    # ADMIN — bank settings
    # ────────────────────────────────────────────────────────────────────────
    (re.compile(
        r"set\s+(max|maximum)\s*(send|transfer)\s*(limit)?\s*(to\s*)?([\d,]+)", re.I),
     "setmaxsend",
     lambda m, t: m.group(5).replace(",", ""),
     lambda m, t: f"set maximum send to {int(m.group(5).replace(',', '')):,} coins"),

    (re.compile(
        r"set\s+(daily|day)\s*(send|transfer)\s*(limit)?\s*(to\s*)?([\d,]+)", re.I),
     "setsendlimit",
     lambda m, t: m.group(5).replace(",", ""),
     lambda m, t: f"set daily send limit to {int(m.group(5).replace(',', '')):,} coins"),

    (re.compile(
        r"set\s+(min|minimum)\s*(send|transfer)\s*(limit)?\s*(to\s*)?([\d,]+)", re.I),
     "setminsend",
     lambda m, t: m.group(5).replace(",", ""),
     lambda m, t: f"set minimum send limit to {int(m.group(5).replace(',', '')):,} coins"),

    (re.compile(
        r"set\s+(send\s*)?(tax|fee)\s*(to\s*)?([\d.]+)\s*(%|percent)?", re.I),
     "setsendtax",
     lambda m, t: m.group(4),
     lambda m, t: f"set send tax to {m.group(4)}%"),

    # ────────────────────────────────────────────────────────────────────────
    # POKER SETTINGS
    # ────────────────────────────────────────────────────────────────────────
    # Toggle win limit
    (re.compile(r"(enable|turn\s+on)\s+poker\s+win\s*(limit)?\b", re.I),
     "poker", _k("winlimit on"), _k("turn poker win limit ON")),

    (re.compile(r"(disable|turn\s+off)\s+poker\s+win\s*(limit)?\b", re.I),
     "poker", _k("winlimit off"), _k("turn poker win limit OFF")),

    # Toggle loss limit
    (re.compile(r"(enable|turn\s+on)\s+poker\s+loss\s*(limit)?\b", re.I),
     "poker", _k("losslimit on"), _k("turn poker loss limit ON")),

    (re.compile(r"(disable|turn\s+off)\s+poker\s+loss\s*(limit)?\b", re.I),
     "poker", _k("losslimit off"), _k("turn poker loss limit OFF")),

    # Toggle win/loss together
    (re.compile(
        r"(enable|turn\s+on)\s+poker\s+(win[\s/]+loss|loss[\s/]+win)\s*(limit)?\b",
        re.I),
     "poker", _k("winlimit on"), _k("turn poker win limit ON")),

    (re.compile(
        r"(disable|turn\s+off)\s+poker\s+(win[\s/]+loss|loss[\s/]+win)\s*(limit)?\b",
        re.I),
     "poker", _k("winlimit off"), _k("turn poker win/loss limits OFF")),

    # Set specific amounts
    (re.compile(r"set\s+poker\s+(daily\s+)?win\s*(limit)?\s*(to\s*)?([\d,]+)", re.I),
     "setpokerdailywinlimit",
     lambda m, t: m.group(4).replace(",", ""),
     lambda m, t: f"set poker daily win limit to {int(m.group(4).replace(',', '')):,} coins"),

    (re.compile(r"set\s+poker\s+(daily\s+)?loss\s*(limit)?\s*(to\s*)?([\d,]+)", re.I),
     "setpokerdailylosslimit",
     lambda m, t: m.group(4).replace(",", ""),
     lambda m, t: f"set poker daily loss limit to {int(m.group(4).replace(',', '')):,} coins"),

    # ────────────────────────────────────────────────────────────────────────
    # ADMIN — coins
    # ────────────────────────────────────────────────────────────────────────
    (re.compile(
        r"(add|give)\s+([\d,]+)\s*(coins?|tokens?)?\s*(to|for)\s+@?(\w+)", re.I),
     "addcoins",
     lambda m, t: f"{m.group(5)} {m.group(2).replace(',', '')}",
     lambda m, t: f"add {m.group(2).replace(',', '')} coins to @{m.group(5)}"),

    (re.compile(
        r"(remove|take|deduct)\s+([\d,]+)\s*(coins?|tokens?)?\s*(from|of)\s+@?(\w+)",
        re.I),
     "removecoins",
     lambda m, t: f"{m.group(5)} {m.group(2).replace(',', '')}",
     lambda m, t: f"remove {m.group(2).replace(',', '')} coins from @{m.group(5)}"),

    (re.compile(
        r"set\s+(coins?|balance|tokens?)\s*(of|for)?\s*@?(\w+)\s*(to\s*)?([\d,]+)",
        re.I),
     "setcoins",
     lambda m, t: f"{m.group(3)} {m.group(5).replace(',', '')}",
     lambda m, t: f"set @{m.group(3)}'s balance to {int(m.group(5).replace(',', '')):,} coins"),

    # ────────────────────────────────────────────────────────────────────────
    # HELP / COMMANDS
    # ────────────────────────────────────────────────────────────────────────
    (re.compile(
        r"what\s+commands?|(show|see|list)\s+(all\s+)?commands?"
        r"|help\s+me$|what\s+can\s+(you|i)\s+do|what\s+do\s+you\s+do|^guide$", re.I),
     "help", _k(""), _k("show available commands")),

    (re.compile(r"(my\s+commands?|commands?\s+(for\s+me|i\s+can\s+use|available))", re.I),
     "mycommands", _k(""), _k("show your commands")),

    (re.compile(
        r"(show\s+|list\s+|what\s+are\s+)?(ai\s+)?capabilit(y|ies)\b"
        r"|what\s+can\s+(emceebot|you)\s+(do|understand|help)\b"
        r"|emceebot\s+(features?|help|overview)",
        re.I),
     "aicapabilities", _k(""), _k("show AI capabilities")),
]


# ---------------------------------------------------------------------------
# Static response templates for SAFE commands
# ---------------------------------------------------------------------------

_SAFE_RESPONSES: dict[str, str] = {
    "minehelp":      "⛏️ Use /minehelp for the full mining guide. /mine to dig, /ores for inventory.",
    "mine":          "⛏️ Use /mine to mine. Check /tool for pickaxe stats, /ores for your haul.",
    "ores":          "⛏️ Use /ores to view your ore inventory.",
    "tool":          "⛏️ Use /tool to check your pickaxe stats.",
    "mineshop":      "⛏️ Use /mineshop to browse mining upgrades and items.",
    "bal":           "💰 Use /bal to check your coin balance.",
    "bank":          "🏦 Use /bank to view your bank info.",
    "transactions":  "📋 Use /transactions to view recent transactions.",
    "daily":         "🎁 Use /daily to claim your daily coin reward.",
    "leaderboard":   "🏆 Use /leaderboard or /lb to see the top coin holders.",
    "shop":          "🛒 Use /shop to open the main shop, or /shop badges for emoji badges.",
    "help":          "❓ Use /help for all commands, /mycommands for your personal list.",
    "mycommands":    "📋 Use /mycommands to see commands you can use.",
    "aicapabilities":"🤖 Use /aicapabilities to see what EmceeBot can understand.",
    "pokerhelp":     "♠️ Use /pokerhelp to learn poker rules and how to join a table.",
    "bjhelp":        "🃏 Use /bjhelp for blackjack rules. /rbjhelp for Realistic Blackjack.",
    "rbjhelp":       "🃏 Use /rbjhelp for Realistic Blackjack rules.",
    "eventhelp":     "🎉 Use /eventhelp for event info, /event to see the active event.",
    "eventstatus":   "🎉 Use /eventstatus to check the current event.",
    "me":            "👤 Use /me to view your profile stats.",
    "profile":       "👤 Use /profile <username> to view a player's profile.",
    "stats":         "📊 Use /stats <username> to view a player's game stats.",
    "quests":        "📜 Use /quests to see your active quests, /dailyquests for today's.",
    "spawns":        "📍 Use /spawns to list all saved spawn points.",
    "spawninfo":     "📍 Use /spawninfo <name> to view spawn coordinates.",
    "botoutfits":    "👗 Use /botoutfits to view all saved bot outfit profiles.",
    "botoutfit":     "👗 Use /botoutfit to view this bot's current saved outfit.",
    "goto":          "🗺️ Use /goto <username> to teleport to that player's location.",
    "bothealth":     "🤖 Use /bothealth to see bot status.",
    "modulehealth":  "🤖 Use /modulehealth to check module status.",
    "botheartbeat":  "🤖 Use /botheartbeat to see live heartbeats.",
}


# ---------------------------------------------------------------------------
# Bot personality intro per mode
# ---------------------------------------------------------------------------

_PERSONALITIES: dict[str, str] = {
    "host":       "🎙️ Hi! I'm your Lounge assistant.",
    "miner":      "⛏️ Hi! I'm your Mining Guide.",
    "banker":     "🏦 Hi! I'm your Banker assistant.",
    "blackjack":  "🃏 Hi! I'm your Blackjack Dealer.",
    "poker":      "♠️ Hi! I'm your Poker Dealer.",
    "shopkeeper": "🛒 Hi! I'm your Shopkeeper.",
    "eventhost":  "🎉 Hi! I'm your Event Host.",
    "security":   "🛡️ Hi! I'm your Security bot.",
    "dj":         "🎧 Hi! I'm your DJ.",
    "all":        "🤖 Hi! I'm your room assistant.",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _w(bot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, str(msg)[:249])


def _should_answer_ai() -> bool:
    """Only host / eventhost / all bots respond to AI requests (anti-spam)."""
    from config import BOT_MODE
    return BOT_MODE in ("host", "eventhost", "all")


def _persona() -> str:
    from config import BOT_MODE
    return _PERSONALITIES.get(BOT_MODE, _PERSONALITIES["all"])


_YES_WORDS: frozenset[str] = frozenset({
    "yes", "y", "confirm", "do it", "proceed", "go ahead",
    "sure", "ok", "okay", "yep", "yeah", "affirmative",
})
_NO_WORDS: frozenset[str] = frozenset({
    "no", "n", "cancel", "stop", "nevermind", "never mind",
    "dont", "do not", "nope", "nah", "abort",
})


def _is_yes(text: str) -> bool:
    return text.lower().strip() in _YES_WORDS


def _is_no(text: str) -> bool:
    return text.lower().strip() in _NO_WORDS


_AI_PRIMARY_NAME: str = "emceebot"


def _build_ai_names(bot_username: str) -> list[str]:
    """Return lowercase names this bot listens for (longest first)."""
    names: set[str] = {_AI_PRIMARY_NAME}
    if bot_username:
        names.add(bot_username.lower())
    return sorted(names, key=len, reverse=True)


def _is_ai_trigger(message: str, bot_username: str) -> bool:
    """
    Return True only when the message explicitly addresses EmceeBot.
    Triggers on:
      - @EmceeBot anywhere in the message
      - "EmceeBot" as a word anywhere (e.g. "EmceeBot, show my balance")
    Does NOT trigger on generic chat.
    """
    low = message.lower().strip()
    for name in _build_ai_names(bot_username):
        if f"@{name}" in low:
            return True
        if re.search(rf"\b{re.escape(name)}\b", low):
            return True
    return False


def _strip_trigger(message: str, bot_username: str) -> str:
    """
    Remove the EmceeBot trigger prefix/mention and return clean question text.
    E.g. "EmceeBot, can you show my balance?" → "can you show my balance?"
    """
    for name in _build_ai_names(bot_username):
        pat = re.compile(rf"^.*?@?{re.escape(name)}[,\s]*", re.I)
        cleaned = pat.sub("", message.strip(), count=1)
        if cleaned.lower() != message.strip().lower():
            return cleaned.strip()
    return message.strip()


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

def classify_intent(text: str) -> IntentResult | None:
    """
    Match natural language text to a known command intent.
    Evaluates patterns top-to-bottom; returns the first match, or None.
    """
    text = text.strip()
    for (pattern, cmd, args_fn, human_fn) in _INTENTS:
        m = pattern.search(text)
        if m:
            try:
                args_str = (args_fn(m, text) or "").strip()
                human    = human_fn(m, text) or f"run /{cmd}"
            except Exception:
                args_str = ""
                human    = f"run /{cmd}"
            return IntentResult(
                command        = cmd,
                args_str       = args_str,
                human_readable = human,
                risk_level     = _risk_for(cmd),
            )
    return None


# ---------------------------------------------------------------------------
# Command execution — called after user confirms a CONFIRM/ADMIN_CONFIRM action
# ---------------------------------------------------------------------------

_HANDLER_MAP: dict[str, tuple[str, str]] = {
    # Economy / bank
    "send":                  ("modules.bank",       "handle_send"),
    "bank":                  ("modules.bank",       "handle_bank"),
    "transactions":          ("modules.bank",       "handle_transactions"),
    # Mining
    "mine":                  ("modules.mining",     "handle_mine"),
    "sellores":              ("modules.mining",     "handle_sellores"),
    "sellore":               ("modules.mining",     "handle_sellore"),
    "minebuy":               ("modules.mining",     "handle_minebuy"),
    "mineshop":              ("modules.mining",     "handle_mineshop"),
    # Shop
    "buy":                   ("modules.shop",       "handle_buy"),
    "equip":                 ("modules.shop",       "handle_equip"),
    "shop":                  ("modules.shop",       "handle_shop"),
    # Events
    "startevent":            ("modules.events",     "handle_startevent"),
    "stopevent":             ("modules.events",     "handle_stopevent"),
    "eventshop":             ("modules.events",     "handle_eventshop"),
    "buyevent":              ("modules.events",     "handle_buyevent"),
    # Admin — coins
    "setcoins":              ("modules.admin_cmds", "handle_setcoins"),
    "resetcoins":            ("modules.admin_cmds", "handle_resetcoins"),
    # Room / spawn
    "setspawn":              ("modules.room_utils", "handle_setspawn"),
    "tpme":                  ("modules.room_utils", "handle_tpme"),
    "tp":                    ("modules.room_utils", "handle_tp"),
    "tphere":                ("modules.room_utils", "handle_tphere"),
    "bring":                 ("modules.room_utils", "handle_tphere"),
    "bringall":              ("modules.room_utils", "handle_bringall"),
    "tpall":                 ("modules.room_utils", "handle_tpall"),
    "goto":                  ("modules.room_utils", "handle_goto"),
    # Outfit (executed by the host/responding bot)
    "dressbot":              ("modules.bot_modes",  "handle_dressbot"),
    "copyoutfit":            ("modules.bot_modes",  "handle_copyoutfit"),
    "wearuseroutfit":        ("modules.bot_modes",  "handle_wearuseroutfit"),
    "savebotoutfit":         ("modules.bot_modes",  "handle_savebotoutfit"),
    "botoutfits":            ("modules.bot_modes",  "handle_botoutfits"),
    # Profile
    "profile":               ("modules.profile",    "handle_profile_cmd"),
    "stats":                 ("modules.profile",    "handle_stats_cmd"),
    # Poker settings
    "poker":                 ("modules.poker",      "handle_poker"),
    "setpokerdailywinlimit": ("modules.poker",      "handle_setpokerdailywinlimit"),
    "setpokerdailylosslimit":("modules.poker",      "handle_setpokerdailylosslimit"),
}


async def _execute_confirmed(bot, user, command: str, args_str: str) -> None:
    """Execute a confirmed command by lazy-importing and calling its handler."""
    # Special case: wearuseroutfit with empty args_str → use requester's username
    if command == "wearuseroutfit" and not args_str.strip():
        args_str = user.username

    args_list = [command] + (args_str.split() if args_str.strip() else [])
    fallback  = f"✅ Type /{command}{(' ' + args_str) if args_str else ''} to execute."

    if command not in _HANDLER_MAP:
        await _w(bot, user.id, fallback)
        return

    module_path, fn_name = _HANDLER_MAP[command]
    try:
        mod = importlib.import_module(module_path)
        fn  = getattr(mod, fn_name)
        sig     = inspect.signature(fn)
        nparams = len(sig.parameters)
        if nparams >= 3:
            await fn(bot, user, args_list)
        else:
            await fn(bot, user)
    except (ImportError, AttributeError) as exc:
        print(f"[AI] Handler import error for /{command}: {exc}")
        await _w(bot, user.id, fallback)
    except Exception as exc:
        print(f"[AI] Handler error for /{command}: {exc}")
        await _w(bot, user.id, f"Command failed. Try /{command} manually.")


# ---------------------------------------------------------------------------
# Core AI request handler
# ---------------------------------------------------------------------------

async def _handle_ai_text(bot, user, text: str) -> None:
    """
    Process a natural language AI request.
    Classifies intent → suggests (SAFE), confirms (CONFIRM/ADMIN_CONFIRM),
    or denies (BLOCKED / permission denied).
    """
    text = text.strip()
    if not text:
        hint = "Ask me anything! E.g. 'how do I mine?' or 'show my balance'."
        await _w(bot, user.id, f"{_persona()} {hint}")
        return

    if _is_blocked(text):
        await _w(bot, user.id, "I can't help with secrets or tokens. Try /help for available commands.")
        db.log_ai_action(user.username, text[:150], "BLOCKED", BLOCKED, "blocked")
        return

    intent = classify_intent(text)
    if intent is None:
        await _w(bot, user.id,
                 "I don't have a command for that yet. Try /help or /mycommands.")
        db.log_ai_action(user.username, text[:150], "unknown", SAFE, "no_match")
        return

    cmd  = intent.command
    risk = intent.risk_level

    # ── SAFE: suggest without executing ──────────────────────────────────────
    if risk == SAFE:
        response = _SAFE_RESPONSES.get(cmd)
        if not response:
            args_hint = f" {intent.args_str}" if intent.args_str else ""
            response  = f"Use /{cmd}{args_hint} to {intent.human_readable}."
        await _w(bot, user.id, response)
        db.log_ai_action(user.username, text[:150], cmd, risk, "suggested")
        return

    # ── CONFIRM: create pending action, ask user ──────────────────────────────
    if risk == CONFIRM:
        db.create_pending_ai_action(
            user_id        = user.id,
            username       = user.username,
            command        = cmd,
            args_str       = intent.args_str,
            human_readable = intent.human_readable,
            risk_level     = risk,
        )
        await _w(bot, user.id,
                 f"⚠️ Confirm: {intent.human_readable}. Reply yes or no.")
        db.log_ai_action(user.username, text[:150], cmd, risk, "pending_confirm")
        return

    # ── ADMIN_CONFIRM: check permissions first ────────────────────────────────
    if risk == ADMIN_CONFIRM:
        if not (is_admin(user.username) or is_owner(user.username)):
            await _w(bot, user.id,
                     "⛔ That is an admin command. You don't have permission.")
            db.log_ai_action(user.username, text[:150], cmd, risk, "denied_no_perm")
            return
        db.create_pending_ai_action(
            user_id        = user.id,
            username       = user.username,
            command        = cmd,
            args_str       = intent.args_str,
            human_readable = intent.human_readable,
            risk_level     = risk,
        )
        await _w(bot, user.id,
                 f"⚠️ Admin confirm: {intent.human_readable}. Reply yes or no.")
        db.log_ai_action(user.username, text[:150], cmd, risk, "pending_admin_confirm")
        return

    await _w(bot, user.id, "I can't help with that. Try /help or /mycommands.")


# ---------------------------------------------------------------------------
# Natural yes/no confirmation handler
# ---------------------------------------------------------------------------

async def handle_natural_confirmation(bot, user, message: str) -> bool:
    """
    If the user has a pending AI action and says yes/no, handle it.
    Returns True if the message was consumed as a confirmation/cancellation.
    """
    if not (_is_yes(message) or _is_no(message)):
        return False

    action = db.get_pending_ai_action(user.id)
    if action is None:
        return False

    if _is_no(message):
        db.cancel_pending_ai_action(user.id)
        await _w(bot, user.id, "❌ Cancelled.")
        db.log_ai_action(
            user.username, action["proposed_command"],
            action["proposed_command"], action["risk_level"], "cancelled",
        )
        return True

    confirmed = db.confirm_pending_ai_action(user.id)
    if confirmed is None:
        await _w(bot, user.id, "⏰ Your action expired. Please ask again.")
        return True

    cmd      = confirmed["proposed_command"]
    args_str = confirmed.get("proposed_args", "") or ""
    human    = confirmed["human_readable_action"]

    await _w(bot, user.id, f"✅ Executing: {human}")
    db.log_ai_action(user.username, human, cmd, confirmed["risk_level"], "confirmed")
    await _execute_confirmed(bot, user, cmd, args_str)
    return True


# ---------------------------------------------------------------------------
# Main intercept — called at the very top of on_chat
# ---------------------------------------------------------------------------

async def handle_ai_intercept(bot, user, message: str) -> bool:
    """
    Intercept AI-related messages before normal on_chat command routing.
    Returns True if this message was fully handled (caller should return early).
    """
    from config import BOT_USERNAME

    # ── 1. yes/no pending confirmation ────────────────────────────────────────
    if _is_yes(message) or _is_no(message):
        if _should_answer_ai():
            return await handle_natural_confirmation(bot, user, message)
        if db.get_pending_ai_action(user.id) is not None:
            return True
        return False

    # ── 2. Natural language AI trigger (non-slash messages only) ──────────────
    if message.startswith("/"):
        return False

    if not _is_ai_trigger(message, BOT_USERNAME):
        return False

    if not _should_answer_ai():
        return True  # consume silently — another bot will answer

    text = _strip_trigger(message, BOT_USERNAME)
    print(f"[AI] trigger: user={user.username} text={text!r}")
    await _handle_ai_text(bot, user, text)
    return True


# ---------------------------------------------------------------------------
# /ask  /ai  /assistant <message>
# ---------------------------------------------------------------------------

async def handle_ask_command(bot, user, args: list[str]) -> None:
    """Handle /ask, /ai, /assistant <message>."""
    if not _should_answer_ai():
        return

    text = " ".join(args[1:]).strip()
    if not text:
        hint = "Type /ask <question>. E.g. /ask how do I mine?"
        await _w(bot, user.id, f"{_persona()} {hint}")
        return

    await _handle_ai_text(bot, user, text)


# ---------------------------------------------------------------------------
# /pendingaction  — show user's current pending action
# ---------------------------------------------------------------------------

async def handle_pendingaction(bot, user) -> None:
    """Show the user their current pending AI action."""
    db.expire_old_ai_actions()
    action = db.get_pending_ai_action(user.id)
    if action is None:
        await _w(bot, user.id, "You have no pending AI action.")
        return

    from datetime import datetime
    now     = datetime.utcnow()
    expires = datetime.strptime(action["expires_at"], "%Y-%m-%d %H:%M:%S")
    secs    = max(0, int((expires - now).total_seconds()))
    msg     = (f"⏳ Pending: {action['human_readable_action']}. "
               f"Reply yes or no. Expires in {secs}s.")
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# /confirm yes|no  — explicit backup for yes/no
# ---------------------------------------------------------------------------

async def handle_confirm_cmd(bot, user, args: list[str]) -> None:
    """Handle /confirm yes or /confirm no as an explicit confirmation command."""
    sub = (args[1].lower() if len(args) > 1 else "").strip()
    if sub in ("yes", "y"):
        consumed = await handle_natural_confirmation(bot, user, "yes")
        if not consumed:
            await _w(bot, user.id, "You have no pending action to confirm.")
    elif sub in ("no", "n", "cancel"):
        consumed = await handle_natural_confirmation(bot, user, "no")
        if not consumed:
            await _w(bot, user.id, "You have no pending action to cancel.")
    else:
        await _w(bot, user.id, "Usage: /confirm yes  or  /confirm no")


# ---------------------------------------------------------------------------
# /aicapabilities — show what EmceeBot can understand
# ---------------------------------------------------------------------------

async def handle_aicapabilities(bot, user, args: list[str] = None) -> None:
    """Show the categories of natural language EmceeBot understands."""
    if not _should_answer_ai():
        return
    lines = [
        "🤖 EmceeBot understands:",
        "💰 Economy: balance, send coins, bank info, transactions, daily reward",
        "⛏️ Mining: mine, ores, tool, sell ores, mining shop",
        "🎰 Games: poker help, blackjack help, realistic BJ help",
        "📍 Room: save spawn, list spawns, teleport me/user/all, bring user/all",
        "👗 Outfit: copy outfit, dress bot, save outfit, show bot outfits",
        "👤 Profile: my profile, show user profile, stats, who is user",
        "🎉 Events: start/stop event, double XP, double coins, casino hour",
        "♠️ Poker: win/loss limit on/off, set poker limits (admin)",
        "🛒 Shop: open shop, buy badge/title, show event shop",
        "⚙️ Admin: set send limits, add/remove coins (admin only)",
        "❓ Help: /help, /mycommands, /aicapabilities",
    ]
    for line in lines:
        await _w(bot, user.id, line)


# ---------------------------------------------------------------------------
# /aidebug <message>  — admin-only: show AI analysis without executing
# ---------------------------------------------------------------------------

async def handle_aidebug(bot, user, args: list[str]) -> None:
    """
    /aidebug <message>
    Admin-only. Shows what the AI would do with a given message without executing.
    Output: trigger, category, command, owner mode, owner online,
            risk, route, handler, confirmation required, permission ok.
    Never shows tokens, secrets, or raw env vars.
    """
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /aidebug <message to test>")
        return

    from config import BOT_USERNAME
    raw = " ".join(args[1:])

    triggered = _is_ai_trigger(raw, BOT_USERNAME)
    text = _strip_trigger(raw, BOT_USERNAME) if triggered else raw

    if _is_blocked(text):
        await _w(bot, user.id,
                 f"trigger={str(triggered).lower()} | category=security | cmd=BLOCKED"
                 f" | risk=BLOCKED | confirm=false | handler=NO | perm=DENIED")
        return

    intent = classify_intent(text)
    if intent is None:
        await _w(bot, user.id,
                 f"trigger={str(triggered).lower()} | category=unknown | cmd=none"
                 f" | risk=SAFE | route=NO | handler=NO | confirm=false | perm=ok")
        return

    cmd      = intent.command
    risk     = intent.risk_level
    category = _CATEGORY.get(cmd, "other")

    # Route check (command in registry)
    route_ok = False
    owner_mode = "host"
    try:
        from modules.command_registry import get_entry as _reg_get
        entry = _reg_get(cmd)
        if entry:
            route_ok   = True
            owner_mode = entry[1].owner if hasattr(entry[1], "owner") else "host"
    except Exception:
        pass

    # Handler check
    handler_ok = cmd in _HANDLER_MAP

    # Owner online
    owner_online = True
    try:
        from modules.multi_bot import _is_mode_online
        owner_online = _is_mode_online(owner_mode)
    except Exception:
        pass

    # Permission check
    confirm_req = risk in (CONFIRM, ADMIN_CONFIRM)
    if risk == ADMIN_CONFIRM:
        perm_ok = is_admin(user.username) or is_owner(user.username)
    else:
        perm_ok = True

    deleg_req = owner_mode not in ("host", "eventhost", "all")

    line1 = (
        f"trigger={str(triggered).lower()} | cat={category} | cmd={cmd}"
        f" | owner={owner_mode} | online={str(owner_online).lower()}"
    )[:249]
    line2 = (
        f"risk={risk} | route={'YES' if route_ok else 'NO'}"
        f" | handler={'YES' if handler_ok else 'NO'}"
        f" | confirm={str(confirm_req).lower()}"
        f" | delegate={str(deleg_req).lower()}"
        f" | perm={'ok' if perm_ok else 'DENIED'}"
    )[:249]
    line3 = intent.human_readable[:249]

    await _w(bot, user.id, line1)
    await _w(bot, user.id, line2)
    await _w(bot, user.id, line3)
