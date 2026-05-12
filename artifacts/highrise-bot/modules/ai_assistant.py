"""
modules/ai_assistant.py
-----------------------
EmceeBot AI assistant — natural language command dispatcher.

Trigger names (case-insensitive, word boundary):
  EmceeBot, @EmceeBot, Emcee, @Emcee, MC, @MC

Parts implemented:
  1  Execute safe commands directly (not just suggest them)
  2  Smart "cannot do that yet" responses (5 failure modes)
  3  Case-insensitive / flexible username matching
  4  Outfit AI — cross-bot delegation (dress/copy/save) for other bots
  5  Delegated task system via ai_delegated_tasks DB table
  6  Profile AI — execute directly
  7  Settings AI — admin-confirm, existing handlers
  8  Pre-execute validation (route/handler/owner/permission)
  9  yes/no confirmation flow
 10  /aicapabilities
 11  /aidebug
 12  Safety (never expose tokens/secrets)

All messages ≤ 249 chars.  No external API calls — pattern-based matching only.
"""

from __future__ import annotations

import importlib
import inspect
import re
import types
from dataclasses import dataclass, field

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
# ---------------------------------------------------------------------------

_RISK: dict[str, str] = {
    # ── SAFE ──────────────────────────────────────────────────────────────
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
    # ── CONFIRM ───────────────────────────────────────────────────────────
    "send": CONFIRM, "buy": CONFIRM, "sellores": CONFIRM,
    "sellore": CONFIRM, "minebuy": CONFIRM, "equip": CONFIRM,
    "eventshop": CONFIRM, "buyevent": CONFIRM,
    "tpme": CONFIRM,
    # ── ADMIN_CONFIRM ──────────────────────────────────────────────────────
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
    "setbotspawnhere": ADMIN_CONFIRM, "setbotspawn": ADMIN_CONFIRM,
    "clearbotspawn": ADMIN_CONFIRM, "botspawns": SAFE,
    "mypos": SAFE, "positiondebug": ADMIN_CONFIRM,
    # Teleport
    "tp": ADMIN_CONFIRM, "tphere": ADMIN_CONFIRM, "bring": ADMIN_CONFIRM,
    "bringall": ADMIN_CONFIRM, "tpall": ADMIN_CONFIRM,
    # Outfit
    "dressbot": ADMIN_CONFIRM, "copyoutfit": ADMIN_CONFIRM,
    "wearuseroutfit": ADMIN_CONFIRM, "savebotoutfit": ADMIN_CONFIRM,
    # Per-bot self-managing outfit commands
    "copymyoutfit": ADMIN_CONFIRM, "copyoutfitfrom": ADMIN_CONFIRM,
    "savemyoutfit": ADMIN_CONFIRM, "wearoutfit": ADMIN_CONFIRM,
    "myoutfits": SAFE, "myoutfitstatus": SAFE, "outfitredirect": SAFE,
    # Poker settings
    "poker": ADMIN_CONFIRM,
    "setpokerdailywinlimit": ADMIN_CONFIRM, "setpokerdailylosslimit": ADMIN_CONFIRM,
}


def _risk_for(cmd: str) -> str:
    return _RISK.get(cmd, SAFE)


# ---------------------------------------------------------------------------
# Intent category table  (command → category string)
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
    "copymyoutfit": "outfit", "copyoutfitfrom": "outfit",
    "savemyoutfit": "outfit", "wearoutfit": "outfit",
    "myoutfits": "outfit", "myoutfitstatus": "outfit",
    "me": "profile", "profile": "profile", "stats": "profile",
    "quests": "profile", "questhelp": "profile", "dailyquests": "profile",
    "poker": "poker",
    "setpokerdailywinlimit": "poker", "setpokerdailylosslimit": "poker",
    "help": "help", "mycommands": "help", "aicapabilities": "help",
    "bothealth": "system", "modulehealth": "system", "botheartbeat": "system",
}


# ---------------------------------------------------------------------------
# Commands that may require cross-bot delegation (outfit on another bot's account)
# ---------------------------------------------------------------------------

_DELEGATABLE_CMDS: frozenset[str] = frozenset()
# Delegation removed — each bot manages its own outfit directly.

# Sentinel for "use the requesting user's own username"
_ME_SENTINEL = "__ME__"


# ---------------------------------------------------------------------------
# Intent result
# ---------------------------------------------------------------------------

@dataclass
class IntentResult:
    command:        str
    args_str:       str
    human_readable: str
    risk_level:     str = field(default="")

    def __post_init__(self) -> None:
        if not self.risk_level:
            self.risk_level = _risk_for(self.command)


# ---------------------------------------------------------------------------
# Blocked keyword guard
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
# Intent patterns — evaluated top-to-bottom, first match wins.
# Lambda args: (re.Match, original_text) → str
# ---------------------------------------------------------------------------

def _k(v: str):
    """Constant-valued lambda."""
    return lambda m, t: v


_INTENTS: list[tuple] = [

    # ────────────────────────────────────────────────────────────────────────
    # MINING
    # ────────────────────────────────────────────────────────────────────────
    (re.compile(r"how.{0,20}(do\s+i\s+|can\s+i\s+|to\s+)?mine|mine\s*help|mining\s*help|help.*min[ei]ng?\b", re.I),
     "minehelp", _k(""), _k("show mining help")),

    (re.compile(r"^mine$|^(start|do)\s+min[ei]|mine\s+for\s+me|let\s+me\s+mine", re.I),
     "mine", _k(""), _k("start mining")),

    (re.compile(r"(my\s+|show\s+|check\s+|view\s+)?(\bores?\b|\bore\s+list|\bore\s+inv)", re.I),
     "ores", _k(""), _k("show your ores")),

    (re.compile(r"(my\s+|show\s+|check\s+)?(pick.?axe(?!\s+shop)|mining\s+tool)\b|^tool$", re.I),
     "tool", _k(""), _k("show your mining tool")),

    (re.compile(r"sell\s+(my\s+|all\s+|the\s+)?ores?\b", re.I),
     "sellores", _k(""), _k("sell all your ores")),

    (re.compile(
        r"(buy|purchase|get)\s+.*(energy|fuel|mine\s*upgrade|pick.?axe|mining\s*item)"
        r"|mine.*(buy|upgrade)", re.I),
     "minebuy", _k(""), _k("open the mining shop")),

    # Intent tests: "mining shop"->mineshop, "mine shop"->mineshop,
    #   "pickaxe shop"->mineshop, "mining store"->mineshop,
    #   "buy mining upgrade"->mineshop, "show shop"->shop (NOT mineshop)
    (re.compile(
        r"(mining|mine|pickaxe|tool)\s+(shop|store|market)\b"
        r"|^mineshop$|mining\s+store\b|buy\s+mining\s+upgrade",
        re.I),
     "mineshop", _k(""), _k("show the mining shop")),

    # ────────────────────────────────────────────────────────────────────────
    # ADMIN — bank settings (checked BEFORE send/transfer to prevent mismatch)
    # Intent tests:
    #   "set max send to 50000"        -> setmaxsend (NEVER /send)
    #   "set maximum per send to 50000" -> setmaxsend
    #   "set max send limit to 50000"  -> setmaxsend
    #   "change max send to 50000"     -> setmaxsend
    #   "set daily send limit to 100000" -> setsendlimit
    #   "set send limit to 100000"     -> setsendlimit
    # ────────────────────────────────────────────────────────────────────────
    (re.compile(
        r"(?:set|change|update|adjust)\s+(?:max|maximum)\s*(?:per\s+)?"
        r"(?:send|transfer)\s*(?:limit\s*)?(?:to\s*)?([\.\d,]+)",
        re.I),
     "setmaxsend",
     lambda m, t: m.group(1).replace(",", ""),
     lambda m, t: f"set maximum send limit to {int(m.group(1).replace(',', '')):,} coins"),

    (re.compile(
        r"(?:set|change|update|adjust)\s+(?:daily|day)\s+(?:send|transfer)"
        r"\s*(?:limit\s*)?(?:to\s*)?([\.\d,]+)"
        r"|(?:set|change|update|adjust)\s+(?:send|transfer)\s+limit\s*(?:to\s*)?([\.\d,]+)",
        re.I),
     "setsendlimit",
     lambda m, t: (m.group(1) or m.group(2) or "").replace(",", ""),
     lambda m, t: f"set daily send limit to {int((m.group(1) or m.group(2) or '0').replace(',', '')):,} coins"),

    (re.compile(
        r"(?:set|change|update|adjust)\s+(?:min|minimum)\s*(?:send|transfer)"
        r"\s*(?:limit\s*)?(?:to\s*)?([\.\d,]+)",
        re.I),
     "setminsend",
     lambda m, t: m.group(1).replace(",", ""),
     lambda m, t: f"set minimum send limit to {int(m.group(1).replace(',', '')):,} coins"),

    (re.compile(
        r"(?:set|change|update|adjust)\s+(?:send\s*)?(?:tax|fee)\s*(?:to\s*)?([\.\d.]+)\s*(?:%|percent)?",
        re.I),
     "setsendtax",
     lambda m, t: m.group(1),
     lambda m, t: f"set send tax to {m.group(1)}%"),

    (re.compile(
        r"turn\s+(on|off)\s+high.?risk\s*block|set\s+high.?risk\s*block\s+(on|off)",
        re.I),
     "sethighriskblocks",
     lambda m, t: (m.group(1) or m.group(2) or "on").lower(),
     lambda m, t: f"set high-risk blocks {(m.group(1) or m.group(2) or 'on').lower()}"),

    # ────────────────────────────────────────────────────────────────────────
    # SEND / TRANSFER — checked AFTER settings to prevent "set max send" -> /send
    # Intent tests:
    #   "send 50000 coins to testuser" -> send testuser 50000
    #   "send testuser 50000"          -> send testuser 50000
    #   "Emcee, send 50000 to testuser" -> send testuser 50000
    #   "set max send to 50000" must NEVER reach this section
    # ────────────────────────────────────────────────────────────────────────
    # Variant A: "send 1000 coins to testuser"
    (re.compile(
        r"(send|transfer|give|pay)\s+([\d,]+)\s*(coins?|tokens?)?\s*(?:to|for|->)\s+@?([A-Za-z]\w*)",
        re.I),
     "send",
     lambda m, t: f"{m.group(4)} {m.group(2).replace(',', '')}",
     lambda m, t: f"send {int(m.group(2).replace(',', '')):,} coins to @{m.group(4)}"),

    # Variant B: "send testuser 1000 coins" — guard: excludes setting keywords as target
    (re.compile(
        r"(send|transfer|give|pay)\s+@?(?!(?:to|for|max|maximum|min|minimum|daily|limit|set|per)\b)"
        r"([A-Za-z]\w*)\s+([\d,]+)\s*(coins?|tokens?)?",
        re.I),
     "send",
     lambda m, t: f"{m.group(2)} {m.group(3).replace(',', '')}",
     lambda m, t: f"send {int(m.group(3).replace(',', '')):,} coins to @{m.group(2)}"),

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
    # EVENTS — specific names first, then generic
    # ────────────────────────────────────────────────────────────────────────
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
    (re.compile(
        r"(show|list|see|view)\s+(all\s+)?spawns?\b|what\s+spawns?\s+(are there|exist)\b"
        r"|spawns\s+(list|available)\b",
        re.I),
     "spawns", _k(""), _k("show all saved spawns")),

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
    # TELEPORT
    # ────────────────────────────────────────────────────────────────────────
    # Teleport everyone to spawn
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

    # Teleport self to spawn
    (re.compile(r"(teleport|tp)\s+me\s+to\s+(\w+)\b|go\s+to\s+spawn\s+(\w+)\b", re.I),
     "tpme",
     lambda m, t: (m.group(2) or m.group(3)).lower(),
     lambda m, t: f"teleport you to spawn '{(m.group(2) or m.group(3)).lower()}'"),

    # Teleport user to @user (explicit @mention → goto suggestion + note)
    (re.compile(r"(teleport|tp|move)\s+@?(\w+)\s+to\s+@(\w+)\b", re.I),
     "goto",
     lambda m, t: m.group(3),
     lambda m, t: (f"go to @{m.group(3)}'s location "
                   f"(then /tphere {m.group(2)} to bring them)")),

    # Bring user to me
    (re.compile(r"(bring|tphere)\s+@?(\w+)(\s+to\s+(me|here))?\b", re.I),
     "tphere",
     lambda m, t: m.group(2).lstrip("@"),
     lambda m, t: f"bring @{m.group(2).lstrip('@')} to your position"),

    # Teleport user to spawn
    (re.compile(r"(teleport|tp)\s+@?(\w+)\s+to\s+(\w+)\b", re.I),
     "tp",
     lambda m, t: f"{m.group(2).lstrip('@')} {m.group(3).lower()}",
     lambda m, t: f"teleport @{m.group(2).lstrip('@')} to spawn '{m.group(3).lower()}'"),

    # Go to user
    (re.compile(r"(goto|go\s+to)\s+@?(\w+)\b|teleport\s+me\s+to\s+@(\w+)\b", re.I),
     "goto",
     lambda m, t: (m.group(2) or m.group(3)).lstrip("@"),
     lambda m, t: f"teleport you to @{(m.group(2) or m.group(3)).lstrip('@')}"),

    # ────────────────────────────────────────────────────────────────────────
    # OUTFIT — each bot manages its own outfit directly.
    # Cross-bot patterns redirect the user to talk to the target bot.
    # Self-targeting patterns run on THIS bot only.
    # ────────────────────────────────────────────────────────────────────────

    # -- Cross-bot redirect: "dress @KeanuShield as security"
    (re.compile(r"(dress|outfit)\s+@?(\w+)\s+(as|using|with)\s+(\w+)\b", re.I),
     "outfitredirect",
     lambda m, t: f"{m.group(2).lstrip('@').lower()} wearoutfit",
     lambda m, t: (
         f"redirect: tell @{m.group(2).lstrip('@')} to wear '{m.group(4).lower()}' outfit"
     )),

    # -- Cross-bot redirect: "make @KeanuShield look like/wear security"
    (re.compile(
        r"make\s+@?(\w+)\s+(look\s+like|wear)\s+(?!my\b|your\b|@)(\w+)(?:\s+outfit)?\b",
        re.I),
     "outfitredirect",
     lambda m, t: f"{m.group(1).lstrip('@').lower()} wearoutfit",
     lambda m, t: (
         f"redirect: tell @{m.group(1).lstrip('@')} to wear '{m.group(3).lower()}' outfit"
     )),

    # -- Cross-bot redirect: "copy my outfit to @KeanuShield" /
    #    "make @KeanuShield wear my outfit" / "have @KeanuShield wear my outfit"
    (re.compile(
        r"copy\s+my\s+outfit\s+to\s+@?(\w+)\b"
        r"|make\s+@?(\w+)\s+wear\s+my\s+outfit\b"
        r"|have\s+@?(\w+)\s+wear\s+my\s+outfit\b",
        re.I),
     "outfitredirect",
     lambda m, t: (
         f"{(m.group(1) or m.group(2) or m.group(3)).lstrip('@').lower()} copymyoutfit"
     ),
     lambda m, t: (
         f"redirect: tell @{(m.group(1) or m.group(2) or m.group(3)).lstrip('@')} "
         f"to copy your outfit"
     )),

    # -- Cross-bot redirect: "copy @testuser outfit to @KeanuShield"
    (re.compile(
        r"copy\s+@?([A-Za-z]\w+)[''s]*\s+outfit\s+to\s+@?(\w+)\b"
        r"|copy\s+@?([A-Za-z]\w+)\s+to\s+@?(\w+)\b(?!.*spawn)",
        re.I),
     "outfitredirect",
     lambda m, t: (
         f"{(m.group(2) or m.group(4)).lstrip('@').lower()} copyoutfitfrom"
     ),
     lambda m, t: (
         f"redirect: tell @{(m.group(2) or m.group(4)).lstrip('@')} "
         f"to copy @{(m.group(1) or m.group(3)).lstrip('@')}'s outfit"
     )),

    # -- Cross-bot redirect: "save @KeanuShield's outfit as security"
    # Negative lookahead prevents matching "save this/my/current/bot outfit as ..."
    (re.compile(
        r"save\s+@?(?!this\b|my\b|current\b|bot\b|the\b)(\w+)[''s]*"
        r"\s+(?:current\s+)?outfit\s+(?:as|to|for)\s+(\w+)\b",
        re.I),
     "outfitredirect",
     lambda m, t: f"{m.group(1).lstrip('@').lower()} savemyoutfit",
     lambda m, t: (
         f"redirect: tell @{m.group(1).lstrip('@')} "
         f"to save their outfit as '{m.group(2).lower()}'"
     )),

    # -- Self: "copy my outfit" / "wear my outfit" / "copy my outfit to the bot" /
    #    "make bot wear my outfit"
    (re.compile(
        r"copy\s+my\s+outfit(?:\s+to\s+(?:the\s+)?bot)?\s*$"
        r"|wear\s+my\s+outfit\b"
        r"|make\s+(?:the\s+)?bot\s+wear\s+my\s+outfit\b",
        re.I),
     "copymyoutfit", _k(""), _k("copy your outfit onto this bot")),

    # -- Self: "copy @user's outfit" / "make bot wear @user outfit"
    (re.compile(
        r"copy\s+@?([A-Za-z]\w+)[''s]*\s+outfit\b"
        r"|make\s+(?:the\s+)?bot\s+wear\s+@?([A-Za-z]\w+)[''s]*\s+outfit\b",
        re.I),
     "copyoutfitfrom",
     lambda m, t: (m.group(1) or m.group(2)).lstrip("@").lower(),
     lambda m, t: f"copy @{(m.group(1) or m.group(2)).lstrip('@')}'s outfit to this bot"),

    # -- Self: "save this/current/my/bot outfit as <name>" /
    #    "remember this outfit as <name>"
    (re.compile(
        r"save\s+(?:this|current|my|bot)[''s]?\s+outfit\s+as\s+(\w+)\b"
        r"|remember\s+(?:this|current)?\s+outfit\s+as\s+(\w+)\b"
        r"|save\s+bot\s+outfit\s+(?:as|to)\s+(\w+)\b",
        re.I),
     "savemyoutfit",
     lambda m, t: (m.group(1) or m.group(2) or m.group(3)).lower(),
     lambda m, t: (
         f"save this bot's current outfit as "
         f"'{(m.group(1) or m.group(2) or m.group(3)).lower()}'"
     )),

    # -- Self: "wear <name> outfit" / "dress as <name>" / "switch to <name> outfit" /
    #    "use <name> outfit"
    (re.compile(
        r"wear\s+(?:the\s+)?(\w+)\s+outfit\b"
        r"|dress\s+(?:as|like)\s+(\w+)\b"
        r"|switch\s+to\s+(?:the\s+)?(\w+)\s+(?:outfit|look)\b"
        r"|use\s+(?:the\s+)?(\w+)\s+outfit\b",
        re.I),
     "wearoutfit",
     lambda m, t: (m.group(1) or m.group(2) or m.group(3) or m.group(4)).lower(),
     lambda m, t: (
         f"apply the '{(m.group(1) or m.group(2) or m.group(3) or m.group(4)).lower()}'"
         f" saved outfit on this bot"
     )),

    # -- Self: "show outfits" / "outfit status" / "list outfits" / "what am I wearing"
    (re.compile(
        r"(show|list|view)\s+(?:my\s+|bot\s+)?(outfit|outfits|looks?)\b"
        r"|bot\s+outfit\s+(status|list|info)\b"
        r"|outfit\s+(status|list|info)\b"
        r"|what.*(bot|bots?)\s+(wearing|dressed|outfit)\b"
        r"|what\s+(?:outfit\s+am\s+i|am\s+i\s+wearing)\b",
        re.I),
     "myoutfitstatus", _k(""), _k("show this bot's outfit status")),

    # ────────────────────────────────────────────────────────────────────────
    # PROFILE — specific user first, then generic "me"
    # ────────────────────────────────────────────────────────────────────────
    (re.compile(
        r"(show|view|see|check|display)\s+(?!my\b|me\b|your\b)@?([A-Za-z]\w+)[''s]*\s+"
        r"(profile|pinfo|whois|info)\b",
        re.I),
     "profile",
     lambda m, t: m.group(2).lstrip("@"),
     lambda m, t: f"show @{m.group(2).lstrip('@')}'s profile"),

    (re.compile(r"^profile\s+@?([A-Za-z]\w+)\b", re.I),
     "profile",
     lambda m, t: m.group(1).lstrip("@"),
     lambda m, t: f"show @{m.group(1).lstrip('@')}'s profile"),

    (re.compile(r"(who\s+is|whois)\s+@?([A-Za-z]\w+)\b", re.I),
     "profile",
     lambda m, t: m.group(2).lstrip("@"),
     lambda m, t: f"show @{m.group(2).lstrip('@')}'s profile"),

    (re.compile(
        r"(show|check|see)\s+(?!my\b|me\b|your\b)@?([A-Za-z]\w+)[''s]*\s+stats\b",
        re.I),
     "stats",
     lambda m, t: m.group(2).lstrip("@"),
     lambda m, t: f"show @{m.group(2).lstrip('@')}'s stats"),

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
    # POKER SETTINGS
    # ────────────────────────────────────────────────────────────────────────
    (re.compile(r"(enable|turn\s+on)\s+poker\s+win\s*(limit)?\b", re.I),
     "poker", _k("winlimit on"), _k("turn poker win limit ON")),

    (re.compile(r"(disable|turn\s+off)\s+poker\s+win\s*(limit)?\b", re.I),
     "poker", _k("winlimit off"), _k("turn poker win limit OFF")),

    (re.compile(r"(enable|turn\s+on)\s+poker\s+loss\s*(limit)?\b", re.I),
     "poker", _k("losslimit on"), _k("turn poker loss limit ON")),

    (re.compile(r"(disable|turn\s+off)\s+poker\s+loss\s*(limit)?\b", re.I),
     "poker", _k("losslimit off"), _k("turn poker loss limit OFF")),

    (re.compile(
        r"(enable|turn\s+on)\s+poker\s+(win[\s/]+loss|loss[\s/]+win)\s*(limit)?\b",
        re.I),
     "poker", _k("winlimit on"), _k("turn poker win/loss limits ON")),

    (re.compile(
        r"(disable|turn\s+off)\s+poker\s+(win[\s/]+loss|loss[\s/]+win)\s*(limit)?\b",
        re.I),
     "poker", _k("winlimit off"), _k("turn poker win/loss limits OFF")),

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
        r"|what\s+can\s+(emceebot|emcee|mc|you)\s+(do|understand|help)\b"
        r"|emcee(bot)?\s+(features?|help|overview)",
        re.I),
     "aicapabilities", _k(""), _k("show AI capabilities")),

    # ────────────────────────────────────────────────────────────────────────
    # UNSUPPORTED / SDK-LIMITED catch-alls
    # ────────────────────────────────────────────────────────────────────────
    (re.compile(r"\bfly\b|\bspeed\s+hack\b|\bwall\s+hack\b|\bjump\s+hack\b|\bno.?clip\b", re.I),
     "__sdk_limit__", _k(""),
     _k("make someone fly or hack movement (SDK doesn't support this)")),

    (re.compile(r"ban\s+@?(\w+)|kick\s+@?(\w+)", re.I),
     "__no_cmd__", _k(""),
     lambda m, t: f"ban/kick @{(m.group(1) or m.group(2) or '?')} (use room moderation directly)"),

    # ────────────────────────────────────────────────────────────────────────
    # EMOTES (new)
    # ────────────────────────────────────────────────────────────────────────
    (re.compile(r"(show|list|what|see).*(emotes?)\b|^emotes?$", re.I),
     "emotes", _k(""), _k("list available emotes")),

    (re.compile(r"(what|info|about|describe|show).*(emote[-\s]\w+|emote\s+\w+)\b"
                r"|emoteinfo\s+(\w+)", re.I),
     "emoteinfo",
     lambda m, t: (m.group(2) or m.group(3) or "").replace("emote ", "emote-").strip(),
     lambda m, t: f"show info for emote '{(m.group(2) or m.group(3) or '').strip()}'"),

    # ────────────────────────────────────────────────────────────────────────
    # BOT SPAWN (new)
    # ────────────────────────────────────────────────────────────────────────
    (re.compile(r"(show|list|see).*(bot\s+spawn|bot\s+location|bot\s+pos)\b|botspawns?\b", re.I),
     "botspawns", _k(""), _k("list all bot spawn locations")),

    # "set @Bot spawn here" / "save bot spawn here for @Bot"
    (re.compile(
        r"(set|save|put|place)\s+@?(\w+)[''s]*\s+spawn\s+here\b"
        r"|(set|save)\s+spawn\s+here\s+(for\s+)?@?(\w+)\b",
        re.I),
     "setbotspawnhere",
     lambda m, t: (m.group(2) or m.group(5) or "").lstrip("@").lower(),
     lambda m, t: (
         f"set @{(m.group(2) or m.group(5) or '').lstrip('@')} spawn to your current position"
     )),

    # "set @Bot spawn to <spawn_name>" / "save bot spawn for @Bot as bank"
    (re.compile(
        r"(set|save|create)\s+(bot\s+spawn|spawn\s+for|position\s+for)\s+@?(\w+)\s+(at\s+|to\s+)?(\w+)",
        re.I),
     "setbotspawn",
     lambda m, t: f"{m.group(3).lower()} {m.group(5).lower()}",
     lambda m, t: f"set spawn for @{m.group(3)} to '{m.group(5).lower()}'"),

    # ────────────────────────────────────────────────────────────────────────
    # ADMIN'S BLESSING (new)
    # ────────────────────────────────────────────────────────────────────────
    (re.compile(
        r"(start|begin|launch|enable)\s+(admin.?s?\s+blessing|blessing\s+event|all\s+boosts?)\b"
        r"(?:\s+(?:for\s+)?(\d+)\s*min)?",
        re.I),
     "adminsblessing",
     lambda m, t: m.group(3) or "60",
     lambda m, t: f"start Admin's Blessing event for {m.group(3) or '60'} minutes"),

    # ────────────────────────────────────────────────────────────────────────
    # POKER PACE / STACK (new)
    # ────────────────────────────────────────────────────────────────────────
    (re.compile(r"(show|what.?s?|check|see).*(poker\s+pace|poker\s+mode|poker\s+speed)\b|pokerpace\b|pokermode\b",
                re.I),
     "pokerpace", _k(""), _k("show poker pace settings")),

    (re.compile(r"(set|change|switch)\s+poker\s+(mode|pace|speed)\s+(fast|normal|long)\b", re.I),
     "pokermode",
     lambda m, t: m.group(3).lower(),
     lambda m, t: f"set poker mode to {m.group(3).lower()}"),

    (re.compile(r"(show|check|see|list).*(poker\s+stacks?|buy[\-\s]in\s+settings?)\b|pokerstacks?\b", re.I),
     "pokerstacks", _k(""), _k("show poker stack settings")),

    # ────────────────────────────────────────────────────────────────────────
    # MINE CONFIG / STATUS (new)
    # ────────────────────────────────────────────────────────────────────────
    (re.compile(r"(show|check|view).*(mine\s+config|mining\s+config|mine\s+settings?)\b|mineconfig\b", re.I),
     "mineconfig", _k(""), _k("show mining configuration")),

    (re.compile(r"(show|check|active).*(mine\s+event|mining\s+event|ore\s+event)\b|mineeventstatus\b", re.I),
     "mineeventstatus", _k(""), _k("show active mining event status")),

    # ────────────────────────────────────────────────────────────────────────
    # AI DELEGATIONS (new)
    # ────────────────────────────────────────────────────────────────────────
    (re.compile(r"(show|list|see|check).*(delegat|ai\s+task|pending\s+task)\b|aidelegations?\b", re.I),
     "aidelegations", _k(""), _k("show recent AI delegated tasks")),
]


# ---------------------------------------------------------------------------
# Handler map — (module_path, function_name) for every executable command.
# SAFE commands with handlers are executed directly.
# CONFIRM / ADMIN_CONFIRM commands require confirmation first.
# ---------------------------------------------------------------------------

_HANDLER_MAP: dict[str, tuple[str, str]] = {
    # ── SAFE — execute directly ───────────────────────────────────────────
    "bank":                  ("modules.bank",       "handle_bank"),
    "transactions":          ("modules.bank",       "handle_transactions"),
    "shop":                  ("modules.shop",       "handle_shop"),
    "mycommands":            ("modules.admin_cmds", "handle_mycommands"),
    "eventhelp":             ("modules.events",     "handle_eventhelp"),
    "eventstatus":           ("modules.events",     "handle_eventstatus"),
    "minehelp":              ("modules.mining",     "handle_minehelp"),
    "mine":                  ("modules.mining",     "handle_mine"),
    "tool":                  ("modules.mining",     "handle_tool"),
    "mineshop":              ("modules.mining",     "handle_mineshop"),
    "pokerhelp":             ("modules.poker",      "handle_pokerhelp"),
    "quests":                ("modules.quests",     "handle_quests"),
    "spawns":                ("modules.room_utils", "handle_spawns"),
    "botoutfits":            ("modules.bot_modes",  "handle_botoutfits"),
    "goto":                  ("modules.room_utils", "handle_goto"),
    "profile":               ("modules.profile",    "handle_profile_cmd"),
    "stats":                 ("modules.profile",    "handle_stats_cmd"),
    "me":                    ("modules.profile",    "handle_profile_cmd"),
    "bal":                   ("economy",            "handle_balance"),
    "balance":               ("economy",            "handle_balance"),
    "ores":                  ("modules.mining",     "handle_mineinv"),
    # ── CONFIRM — execute after confirmation ──────────────────────────────
    "send":                  ("modules.bank",       "handle_send"),
    "buy":                   ("modules.shop",       "handle_buy"),
    "equip":                 ("modules.shop",       "handle_equip"),
    "sellores":              ("modules.mining",     "handle_sellores"),
    "sellore":               ("modules.mining",     "handle_sellore"),
    "minebuy":               ("modules.mining",     "handle_minebuy"),
    "eventshop":             ("modules.events",     "handle_eventshop"),
    "buyevent":              ("modules.events",     "handle_buyevent"),
    "tpme":                  ("modules.room_utils", "handle_tpme"),
    # ── ADMIN_CONFIRM — execute after admin confirmation ──────────────────
    "setmaxsend":            ("modules.bank",       "handle_setmaxsend"),
    "setsendlimit":          ("modules.bank",       "handle_setsendlimit"),
    "setminsend":            ("modules.bank",       "handle_setminsend"),
    "setsendtax":            ("modules.bank",       "handle_setsendtax"),
    "sethighriskblocks":     ("modules.bank",       "handle_sethighriskblocks"),
    "setcoins":              ("modules.admin_cmds", "handle_setcoins"),
    "resetcoins":            ("modules.admin_cmds", "handle_resetcoins"),
    "startevent":            ("modules.events",     "handle_startevent"),
    "stopevent":             ("modules.events",     "handle_stopevent"),
    "setspawn":              ("modules.room_utils", "handle_setspawn"),
    "tp":                    ("modules.room_utils", "handle_tp"),
    "tphere":                ("modules.room_utils", "handle_tphere"),
    "bring":                 ("modules.room_utils", "handle_tphere"),
    "bringall":              ("modules.room_utils", "handle_bringall"),
    "tpall":                 ("modules.room_utils", "handle_tpall"),
    "dressbot":              ("modules.bot_modes",  "handle_dressbot"),
    "copyoutfit":            ("modules.bot_modes",  "handle_copyoutfit"),
    "wearuseroutfit":        ("modules.bot_modes",  "handle_wearuseroutfit"),
    "savebotoutfit":         ("modules.bot_modes",  "handle_savebotoutfit"),
    "botoutfitstatus":       ("modules.bot_modes",  "handle_botoutfits"),
    "poker":                 ("modules.poker",      "handle_poker"),
    "setpokerdailywinlimit": ("modules.poker",      "handle_setpokerdailywinlimit"),
    "setpokerdailylosslimit":("modules.poker",      "handle_setpokerdailylosslimit"),
    # ── Emote (new) ───────────────────────────────────────────────────────
    "emotes":                ("modules.room_utils", "handle_emotes"),
    "emoteinfo":             ("modules.room_utils", "handle_emoteinfo"),
    # ── AI delegations (new) ──────────────────────────────────────────────
    "aidelegations":         ("modules.ai_assistant", "handle_aidelegations"),
    # ── Bot spawn (new) ───────────────────────────────────────────────────
    "botspawns":             ("modules.room_utils", "handle_botspawns"),
    "setbotspawn":           ("modules.room_utils", "handle_setbotspawn"),
    "setbotspawnhere":       ("modules.room_utils", "handle_setbotspawnhere"),
    "clearbotspawn":         ("modules.room_utils", "handle_clearbotspawn"),
    "mypos":                 ("modules.room_utils", "handle_mypos"),
    "positiondebug":         ("modules.room_utils", "handle_positiondebug"),
    # ── Bot outfit (pre-existing) ─────────────────────────────────────────────────
    "renamebotoutfit":       ("modules.bot_modes",  "handle_renamebotoutfit"),
    "clearbotoutfit":        ("modules.bot_modes",  "handle_clearbotoutfit"),
    # ── Per-bot self-managing outfit commands ────────────────────────────────────
    "copymyoutfit":          ("modules.bot_modes",  "handle_copymyoutfit"),
    "copyoutfitfrom":        ("modules.bot_modes",  "handle_copyoutfitfrom"),
    "savemyoutfit":          ("modules.bot_modes",  "handle_savemyoutfit"),
    "wearoutfit":            ("modules.bot_modes",  "handle_wearoutfit"),
    "myoutfits":             ("modules.bot_modes",  "handle_myoutfits"),
    "myoutfitstatus":        ("modules.bot_modes",  "handle_myoutfitstatus"),
    "outfitredirect":        ("modules.bot_modes",  "handle_outfitredirect"),
    # ── Events (new) ──────────────────────────────────────────────────────
    "adminsblessing":        ("modules.events",     "handle_adminsblessing"),
    "eventresume":           ("modules.events",     "handle_eventresume"),
    "autogamestatus":        ("modules.events",     "handle_autogamestatus"),
    "autogameresume":        ("modules.events",     "handle_autogameresume"),
    # ── Mining (new) ──────────────────────────────────────────────────────
    "mineconfig":            ("modules.mining",     "handle_mineconfig"),
    "mineeventstatus":       ("modules.mining",     "handle_mineeventstatus"),
    # ── Poker pace / stack (new) ──────────────────────────────────────────
    "pokermode":             ("modules.poker",      "handle_pokermode"),
    "pokerpace":             ("modules.poker",      "handle_pokerpace"),
    "setpokerpace":          ("modules.poker",      "handle_setpokerpace"),
    "pokerstacks":           ("modules.poker",      "handle_pokerstacks"),
    "setpokerstack":         ("modules.poker",      "handle_setpokerstack"),
    "dealstatus":            ("modules.poker",      "handle_dealstatus"),
}


# ---------------------------------------------------------------------------
# Static suggestion fallback for SAFE commands without direct handlers
# ---------------------------------------------------------------------------

_SAFE_RESPONSES: dict[str, str] = {
    "minehelp":      "⛏️ Use !minehelp for the full mining guide.",
    "daily":         "🎁 Use !daily to claim your daily reward.",
    "leaderboard":   "🏆 Use !lb to see the top coin holders.",
    "help":          "❓ Use !help for all commands, /mycommands for your list.",
    "aicapabilities":"🤖 Use !aicapabilities to see what I can understand.",
    "bjhelp":        "🃏 Use !bjhelp for blackjack rules.",
    "rbjhelp":       "🃏 Use !rbjhelp for Realistic Blackjack rules.",
    "spawninfo":     "📍 Use !spawninfo <name> to view spawn coordinates.",
    "botoutfit":     "👗 Use !botoutfit to check this bot's saved outfit.",
    "orebook":       "📖 Use !orebook to read about ore types.",
    "orestats":      "📊 Use !orestats to see mining leaderboard.",
    "minebuy":       "⛏️ Use !minebuy <item> to buy mining supplies.",
}


# ---------------------------------------------------------------------------
# Part 2 — Smart "cannot do that yet" responses
# ---------------------------------------------------------------------------

_CANNOT_YET_RESPONSES: dict[str, str] = {
    "no_cmd":     "I can't do that yet — I don't have a working command for that feature.",
    "no_handler": "I found that command, but it is not fully coded yet.",
    "offline":    "I can do that, but the required bot is offline right now.",
    "sdk_limit":  "I can't do that — the Highrise SDK doesn't support that action here.",
    "no_perm":    "You don't have permission to do that.",
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


# ---------------------------------------------------------------------------
# Part 3 — AI trigger names (case-insensitive word-boundary match)
# "emceebot", "emcee", "mc" + bot's own username
# ---------------------------------------------------------------------------

_AI_FIXED_NAMES: list[str] = [
    "emceebot", "emcee", "mc",
    "chilltopiamc", "chilltopia", "chill",
]


def _build_ai_names(bot_username: str) -> list[str]:
    names: set[str] = set(_AI_FIXED_NAMES)
    if bot_username:
        names.add(bot_username.lower())
    return sorted(names, key=len, reverse=True)


def _is_ai_trigger(message: str, bot_username: str) -> bool:
    """
    Return True when the message explicitly addresses EmceeBot / Emcee / MC.
    Matches:
      - @Name anywhere
      - Name as a word boundary match anywhere in the message
    "mc" uses a more careful check: must appear at start or after @, or on its own.
    """
    low = message.lower().strip()
    for name in _build_ai_names(bot_username):
        if f"@{name}" in low:
            return True
        if name == "mc":
            # Avoid false positives: require "mc" to start the message or be @mc
            if re.match(r"^mc\b", low, re.I):
                return True
        else:
            if re.search(rf"\b{re.escape(name)}\b", low):
                return True
    return False


def _strip_trigger(message: str, bot_username: str) -> str:
    """Remove the trigger prefix and return the clean question text."""
    for name in _build_ai_names(bot_username):
        pat = re.compile(rf"^.*?@?{re.escape(name)}[,\s:!]*", re.I)
        cleaned = pat.sub("", message.strip(), count=1)
        if cleaned.lower() != message.strip().lower():
            return cleaned.strip()
    return message.strip()


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

def classify_intent(text: str) -> IntentResult | None:
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
            )
    return None


# ---------------------------------------------------------------------------
# Part 8 — Pre-execute validation
# Returns an error message string, or None if all checks pass.
# ---------------------------------------------------------------------------

def _validate_intent(intent: IntentResult, user_obj) -> str | None:
    """
    Check: command recognised, handler exists, owner online, permission ok.
    Returns an error string to whisper, or None if good to proceed.
    """
    cmd  = intent.command
    risk = intent.risk_level

    # Special sentinel commands
    if cmd == "__sdk_limit__":
        return _CANNOT_YET_RESPONSES["sdk_limit"]
    if cmd == "__no_cmd__":
        return _CANNOT_YET_RESPONSES["no_cmd"]

    # Permission check
    if risk == ADMIN_CONFIRM:
        if not (is_admin(user_obj.username) or is_owner(user_obj.username)):
            return _CANNOT_YET_RESPONSES["no_perm"]

    # Owner-bot online check for ADMIN_CONFIRM (non-safe, non-delegatable checked later)
    if risk == ADMIN_CONFIRM and cmd not in _DELEGATABLE_CMDS:
        try:
            from modules.command_registry import get_entry as _reg_get
            entry = _reg_get(cmd)
            if entry:
                owner_mode = entry[1].owner if hasattr(entry[1], "owner") else "host"
                if not db.is_bot_mode_online(owner_mode):
                    return _CANNOT_YET_RESPONSES["offline"]
        except Exception:
            pass

    # Send: reject reserved-word or numeric "usernames" like @to, @for, @50000
    if cmd == "send":
        parts = (intent.args_str or "").strip().split()
        _bad_targets = {"to", "for", "the", "a", "an", "max", "min",
                        "set", "limit", "daily", "per", "maximum", "minimum"}
        if not parts or parts[0].lower() in _bad_targets:
            return ("I'm not sure who to send coins to. "
                    "Please say: send <amount> coins to <username>.")
        try:
            int(parts[0])
            return ("I'm not sure who to send coins to. "
                    "Please say: send <amount> coins to <username>.")
        except ValueError:
            pass

    # Handler existence check (for non-SAFE or if SAFE wants direct execution)
    if cmd not in _HANDLER_MAP and cmd not in _SAFE_RESPONSES:
        return _CANNOT_YET_RESPONSES["no_cmd"]

    return None


# ---------------------------------------------------------------------------
# Delegation helper — resolve first word of args_str as a bot username
# ---------------------------------------------------------------------------

_DELEGATION_LOCAL_WORDS: frozenset[str] = frozenset(
    {"bot", "the", "my", "me", "your", "self", _ME_SENTINEL.lower()}
)


def _resolve_bot_target(candidate: str) -> tuple[str | None, str | None]:
    """
    Resolve a user-supplied name to (bot_username_lower, bot_mode).

    Resolution order:
      1. Direct bot_username match in bot_instances
         e.g. "keanushield" → bot_username=keanushield, bot_mode=security
      2. bot_mode match in bot_instances
         e.g. "security" → find bot with bot_mode='security' → return its username
      3. Strip trailing 'bot' suffix and retry both
         e.g. "securitybot" → "security" → step 2 above

    Returns (None, None) if no match is found or candidate is a generic local word.
    """
    c = candidate.lower().lstrip("@")
    if c in _DELEGATION_LOCAL_WORDS:
        return None, None

    # 1. Direct username match
    mode = db.get_bot_mode_for_username(c)
    if mode is not None:
        return c, mode

    # 2. Mode-name match (candidate IS the mode, e.g. "security")
    username = db.get_bot_username_for_mode(c)
    if username:
        return username.lower(), c

    # 3. Strip 'bot' suffix and retry
    if c.endswith("bot") and len(c) > 3:
        stripped = c[:-3]
        mode2 = db.get_bot_mode_for_username(stripped)
        if mode2 is not None:
            return stripped, mode2
        username2 = db.get_bot_username_for_mode(stripped)
        if username2:
            return username2.lower(), stripped

    return None, None


def _resolve_delegation(cmd: str, args_str: str) -> tuple[str | None, str]:
    """
    For delegatable commands, check if the first word of args_str resolves to
    a known bot.  Returns (target_bot_username_lower | None, local_args_str).
    local_args_str has the target removed (just the action args for the target bot).
    """
    if cmd not in _DELEGATABLE_CMDS:
        return None, args_str

    parts = args_str.strip().split(maxsplit=1)
    if not parts:
        return None, args_str

    candidate  = parts[0]
    local_args = parts[1] if len(parts) > 1 else ""

    if candidate.lower().lstrip("@") in _DELEGATION_LOCAL_WORDS:
        return None, args_str

    bot_username, _ = _resolve_bot_target(candidate)
    if bot_username is None:
        return None, args_str

    return bot_username, local_args


# ---------------------------------------------------------------------------
# Core command execution
# ---------------------------------------------------------------------------

async def _execute_handler(bot, user, cmd: str, args_list: list[str]) -> None:
    """Call a handler from _HANDLER_MAP with correct arity."""
    if cmd not in _HANDLER_MAP:
        await _w(bot, user.id,
                 f"✅ Try !{cmd}{(' ' + ' '.join(args_list[1:])) if len(args_list) > 1 else ''} manually.")
        return
    module_path, fn_name = _HANDLER_MAP[cmd]
    try:
        mod     = importlib.import_module(module_path)
        fn      = getattr(mod, fn_name)
        nparams = len(inspect.signature(fn).parameters)
        if nparams >= 3:
            await fn(bot, user, args_list)
        else:
            await fn(bot, user)
    except (ImportError, AttributeError) as exc:
        print(f"[AI] handler import error /{cmd}: {exc}")
        await _w(bot, user.id, f"Handler error for /{cmd}. Try it manually.")
    except Exception as exc:
        print(f"[AI] handler error /{cmd}: {exc}")
        await _w(bot, user.id, f"Command /{cmd} failed. Try it manually.")


async def _execute_confirmed(bot, user, command: str, args_str: str) -> None:
    """
    Execute a confirmed command.
    Handles:
    - __ME__ sentinel substitution
    - Cross-bot delegation for delegatable outfit commands
    - Normal local execution via _HANDLER_MAP
    """
    # Resolve __ME__ sentinel to the actual requesting user's username
    args_str = args_str.replace(_ME_SENTINEL, user.username)

    # Part 4+5: Check for cross-bot delegation
    if command in _DELEGATABLE_CMDS:
        target_bot, local_args = _resolve_delegation(command, args_str)
        if target_bot:
            # Check if target bot is online
            target_mode = db.get_bot_mode_for_username(target_bot)
            if target_mode and not db.is_bot_mode_online(target_mode):
                await _w(bot, user.id,
                         f"⚠️ @{target_bot} is offline. The outfit task will be queued "
                         f"and run when they reconnect (up to 90s).")

            # Build the command text the target bot will execute
            cmd_text = command
            if local_args.strip():
                cmd_text = f"{command} {local_args.strip()}"

            task_id = db.create_delegated_task(
                user_id               = user.id,
                username              = user.username,
                original_text         = f"/{command} {args_str}",
                command_text          = cmd_text,
                owner_mode            = target_mode or "host",
                target_bot_username   = target_bot,
                human_readable_action = f"/{cmd_text} on @{target_bot}",
                risk_level            = ADMIN_CONFIRM,
            )
            print(f"[AI] delegated task={task_id} cmd={cmd_text} target={target_bot}")
            await _w(bot, user.id,
                     f"📋 Task #{task_id} queued for @{target_bot}. "
                     f"Result will appear once @{target_bot} picks it up.")
            return

    # For delegatable commands where delegation target was not found, check
    # whether the first arg looks like a specific bot name the user intended.
    # If so, don't silently fall through to wrong local execution.
    if command in _DELEGATABLE_CMDS:
        parts = args_str.strip().split()
        if parts:
            _local_words = _DELEGATION_LOCAL_WORDS | {user.username.lower()}
            first = parts[0].lower().lstrip("@")
            if first not in _local_words:
                # User named a specific target, but we couldn't resolve it
                await _w(bot, user.id,
                         f"I don't recognize '@{parts[0].lstrip('@')}' as one of my bot"
                         f" accounts. Use !bots or /bothealth to see online bots.")
                return

    # Local execution
    args_list = [command] + (args_str.split() if args_str.strip() else [])
    await _execute_handler(bot, user, command, args_list)


async def _execute_safe(bot, user, command: str, args_str: str) -> None:
    """
    Execute a SAFE command directly without confirmation.
    No delegation needed for SAFE commands.
    """
    args_list = [command] + (args_str.split() if args_str.strip() else [])
    await _execute_handler(bot, user, command, args_list)


# ---------------------------------------------------------------------------
# Owner-bot label helper (for clearer confirmation messages)
# ---------------------------------------------------------------------------

_BOT_LABELS: dict[str, str] = {
    "banker":     "BankingBot",
    "host":       "HostBot",
    "eventhost":  "EventBot",
    "miner":      "MinerBot",
    "poker":      "PokerBot",
    "blackjack":  "BlackjackBot",
    "dj":         "DJ",
    "security":   "SecurityBot",
}


def _owner_bot_label(cmd: str) -> str:
    """Return a friendly bot name (e.g. BankingBot) for the command owner."""
    try:
        from modules.command_registry import get_entry as _reg_get
        entry = _reg_get(cmd)
        if entry and hasattr(entry[1], "owner"):
            return _BOT_LABELS.get(entry[1].owner, "")
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Core AI request handler
# ---------------------------------------------------------------------------

async def _handle_ai_text(bot, user, text: str) -> None:
    text = text.strip()
    if not text:
        hint = "Ask me anything! E.g. 'show my balance' or 'how do I mine?'"
        await _w(bot, user.id, f"{_persona()} {hint}")
        return

    if _is_blocked(text):
        await _w(bot, user.id,
                 "I can't help with secrets or tokens. Try !help for available commands.")
        db.log_ai_action(user.username, text[:150], "BLOCKED", BLOCKED, "blocked")
        return

    intent = classify_intent(text)
    if intent is None:
        await _w(bot, user.id,
                 "I don't have a command for that yet. "
                 "Try !help or /aicapabilities.")
        db.log_ai_action(user.username, text[:150], "unknown", SAFE, "no_match")
        return

    cmd  = intent.command
    risk = intent.risk_level

    # Part 8 — pre-execute validation
    err = _validate_intent(intent, user)
    if err:
        await _w(bot, user.id, err)
        db.log_ai_action(user.username, text[:150], cmd, risk, "validation_failed")
        return

    # ── Part 1: SAFE with handler → execute directly (no confirmation) ────
    if risk == SAFE:
        if cmd in _HANDLER_MAP:
            db.log_ai_action(user.username, text[:150], cmd, risk, "executed_direct")
            await _execute_safe(bot, user, cmd, intent.args_str)
        else:
            response = _SAFE_RESPONSES.get(cmd)
            if not response:
                args_hint = f" {intent.args_str}" if intent.args_str else ""
                response  = f"Use !{cmd}{args_hint} to {intent.human_readable}."
            await _w(bot, user.id, response)
            db.log_ai_action(user.username, text[:150], cmd, risk, "suggested")
        return

    # ── CONFIRM: pending action + ask ─────────────────────────────────────
    if risk == CONFIRM:
        db.create_pending_ai_action(
            user_id        = user.id,
            username       = user.username,
            command        = cmd,
            args_str       = intent.args_str,
            human_readable = intent.human_readable,
            risk_level     = risk,
        )
        _lbl = _owner_bot_label(cmd)
        _sfx = f" using {_lbl}" if _lbl else ""
        await _w(bot, user.id,
                 f"⚠️ Confirm: {intent.human_readable}{_sfx}. Reply yes or no.")
        db.log_ai_action(user.username, text[:150], cmd, risk, "pending_confirm")
        return

    # ── ADMIN_CONFIRM ──────────────────────────────────────────────────────
    if risk == ADMIN_CONFIRM:
        db.create_pending_ai_action(
            user_id        = user.id,
            username       = user.username,
            command        = cmd,
            args_str       = intent.args_str,
            human_readable = intent.human_readable,
            risk_level     = risk,
        )
        _lbl = _owner_bot_label(cmd)
        _sfx = f" using {_lbl}" if _lbl else ""
        await _w(bot, user.id,
                 f"⚠️ Confirm: {intent.human_readable}{_sfx}. Reply yes or no.")
        db.log_ai_action(user.username, text[:150], cmd, risk, "pending_admin_confirm")
        return

    await _w(bot, user.id, _CANNOT_YET_RESPONSES["no_cmd"])


# ---------------------------------------------------------------------------
# Natural yes/no confirmation handler
# ---------------------------------------------------------------------------

async def handle_natural_confirmation(bot, user, message: str) -> bool:
    """
    If user has a pending AI action and says yes/no, handle it.
    Returns True if the message was consumed.
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
# Main intercept — top of on_chat
# ---------------------------------------------------------------------------

async def handle_ai_intercept(bot, user, message: str) -> bool:
    """
    Intercept AI-related messages before normal command routing.
    Returns True if fully handled (caller should return early).
    """
    from config import BOT_USERNAME

    # yes/no confirmation — check pending actions
    if _is_yes(message) or _is_no(message):
        if _should_answer_ai():
            return await handle_natural_confirmation(bot, user, message)
        if db.get_pending_ai_action(user.id) is not None:
            return True
        return False

    # Non-slash messages only
    if message.startswith("/"):
        return False

    # Known !commands must NEVER be intercepted by AI — let the router handle them.
    # e.g. !setbotspawnhere @ChillTopiaMC contains "@chilltopiamc" which would
    # otherwise match the AI trigger; we bypass here for any registered command.
    if message.startswith("!"):
        from modules.command_registry import get_entry as _get_entry
        _cmd_tok = message[1:].split()[0].lower() if len(message) > 1 else ""
        if _cmd_tok and _get_entry(_cmd_tok) is not None:
            return False

    if not _is_ai_trigger(message, BOT_USERNAME):
        return False

    if not _should_answer_ai():
        return True  # consume silently — another bot answers

    text = _strip_trigger(message, BOT_USERNAME)
    print(f"[AI] trigger: user={user.username} text={text!r}")
    await _handle_ai_text(bot, user, text)
    return True


# ---------------------------------------------------------------------------
# /ask  /ai  /assistant <message>
# ---------------------------------------------------------------------------

async def handle_ask_command(bot, user, args: list[str]) -> None:
    if not _should_answer_ai():
        return
    text = " ".join(args[1:]).strip()
    if not text:
        hint = "Type /ask <question>. E.g. /ask how do I mine?"
        await _w(bot, user.id, f"{_persona()} {hint}")
        return
    await _handle_ai_text(bot, user, text)


# ---------------------------------------------------------------------------
# /pendingaction
# ---------------------------------------------------------------------------

async def handle_pendingaction(bot, user) -> None:
    db.expire_old_ai_actions()
    action = db.get_pending_ai_action(user.id)
    if action is None:
        await _w(bot, user.id, "You have no pending AI action.")
        return
    from datetime import datetime
    now     = datetime.utcnow()
    expires = datetime.strptime(action["expires_at"], "%Y-%m-%d %H:%M:%S")
    secs    = max(0, int((expires - now).total_seconds()))
    await _w(bot, user.id,
             f"⏳ Pending: {action['human_readable_action']}. "
             f"Reply yes or no. Expires in {secs}s.")


# ---------------------------------------------------------------------------
# /confirm yes|no
# ---------------------------------------------------------------------------

async def handle_confirm_cmd(bot, user, args: list[str]) -> None:
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
        await _w(bot, user.id, "Usage: !confirm yes  or  /confirm no")


# ---------------------------------------------------------------------------
# /aicapabilities
# ---------------------------------------------------------------------------

async def handle_aicapabilities(bot, user, args: list[str] = None) -> None:
    if not _should_answer_ai():
        return
    lines = [
        "🎤 ChillTopiaMC understands (say 'Chill,' 'MC,' or 'ChillTopiaMC'):",
        "👤 Profile: show my profile, show testuser profile, who is testuser",
        "💰 Bank: my balance, send 100 coins to user, bank info, transactions",
        "⛏️ Mining: start mining, show ores, my tool, sell ores, mining shop",
        "🎰 Games: poker help, blackjack help, realistic BJ help",
        "📍 Room: save spawn, list spawns, teleport me/user/all, bring user",
        "👗 Outfit: dress @bot as mode, copy outfit to @bot, save bot outfit",
        "🎉 Events: start double XP/coins/casino hour, stop event",
        "♠️ Poker: win/loss limit on/off, set limits (admin only)",
        "🛒 Shop: show shop, buy badge/title, equip item",
        "⚙️ Settings: set send limits, add/remove coins (admin only)",
        "🔒 Safety: I never show tokens, secrets, or bypass permissions.",
    ]
    for line in lines:
        await _w(bot, user.id, line)


# ---------------------------------------------------------------------------
# /aidebug <message>
# ---------------------------------------------------------------------------

async def handle_aidebug(bot, user, args: list[str]) -> None:
    """
    /aidebug <message> — admin only.
    Shows full analysis of what the AI would do with a given message.
    Never shows tokens, secrets, or env vars.
    """
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !aidebug <message to test>")
        return

    from config import BOT_USERNAME
    raw = " ".join(args[1:])

    triggered = _is_ai_trigger(raw, BOT_USERNAME)
    text      = _strip_trigger(raw, BOT_USERNAME) if triggered else raw

    if _is_blocked(text):
        await _w(bot, user.id,
                 f"trigger={str(triggered).lower()} | cat=security | cmd=BLOCKED"
                 f" | risk=BLOCKED | confirm=false | handler=NO | perm=DENIED")
        await _w(bot, user.id, "Blocked: matches secret/token/hack keyword pattern.")
        return

    intent = classify_intent(text)
    if intent is None:
        await _w(bot, user.id,
                 f"trigger={str(triggered).lower()} | cat=unknown | cmd=none"
                 f" | risk=SAFE | route=NO | handler=NO | confirm=false | perm=ok")
        await _w(bot, user.id, "(no intent matched — would reply with 'I don't have a command for that')")
        return

    cmd      = intent.command
    risk     = intent.risk_level
    category = _CATEGORY.get(cmd, "other")

    route_ok   = False
    owner_mode = "host"
    try:
        from modules.command_registry import get_entry as _reg_get
        entry = _reg_get(cmd)
        if entry:
            route_ok   = True
            owner_mode = entry[1].owner if hasattr(entry[1], "owner") else "host"
    except Exception:
        pass

    handler_ok   = cmd in _HANDLER_MAP
    owner_online = db.is_bot_mode_online(owner_mode)
    confirm_req  = risk in (CONFIRM, ADMIN_CONFIRM)

    if risk == ADMIN_CONFIRM:
        perm_ok = is_admin(user.username) or is_owner(user.username)
    else:
        perm_ok = True

    # Delegation check
    deleg_needed = False
    deleg_target = ""
    if cmd in _DELEGATABLE_CMDS and intent.args_str:
        target_bot, _ = _resolve_delegation(cmd, intent.args_str)
        if target_bot:
            deleg_needed = True
            deleg_target = target_bot

    line1 = (
        f"trigger={str(triggered).lower()} | cat={category} | cmd={cmd}"
        f" | owner={owner_mode} | online={str(owner_online).lower()}"
    )[:249]
    line2 = (
        f"risk={risk} | route={'YES' if route_ok else 'NO'}"
        f" | handler={'YES' if handler_ok else 'NO'}"
        f" | confirm={str(confirm_req).lower()}"
        f" | delegate={'YES→' + deleg_target if deleg_needed else 'NO'}"
        f" | perm={'ok' if perm_ok else 'DENIED'}"
    )[:249]
    line3 = intent.human_readable[:249]
    if intent.args_str:
        line3 = (line3 + f" | args={intent.args_str!r}")[:249]

    await _w(bot, user.id, line1)
    await _w(bot, user.id, line2)
    await _w(bot, user.id, line3)

    # Outfit-specific extra line
    if cmd in ("dressbot", "wearuseroutfit", "savebotoutfit", "copyoutfit"):
        a_parts       = (intent.args_str or "").split()
        raw_target    = a_parts[0] if a_parts else "(none)"
        target_bot_d, local_args_d = _resolve_delegation(cmd, intent.args_str or "")
        deleg_d       = bool(target_bot_d)
        rest          = local_args_d.split() if local_args_d else []
        # Resolve mode from the resolved bot username
        _, tmode_d    = _resolve_bot_target(raw_target) if raw_target != "(none)" else (None, None)
        online_d      = db.is_bot_mode_online(tmode_d) if tmode_d else False
        resolved      = target_bot_d or "LOCAL"
        if cmd == "dressbot":
            mode_val = rest[0] if rest else (a_parts[1] if len(a_parts) > 1 else "(none)")
            line4 = (f"command={cmd} | raw={raw_target}"
                     f" | resolved_target_bot={resolved} | target_mode={tmode_d or 'none'}"
                     f" | mode={mode_val} | delegated={str(deleg_d).lower()}"
                     f" | executor={resolved} | target_online={str(online_d).lower()}")
        elif cmd == "wearuseroutfit":
            src_val = rest[0] if rest else (a_parts[1] if len(a_parts) > 1 else "(none)")
            line4 = (f"command={cmd} | raw={raw_target}"
                     f" | resolved_target_bot={resolved} | target_mode={tmode_d or 'none'}"
                     f" | source={src_val} | delegated={str(deleg_d).lower()}"
                     f" | executor={resolved} | target_online={str(online_d).lower()}")
        elif cmd == "savebotoutfit":
            mode_val = rest[0] if rest else (a_parts[1] if len(a_parts) > 1 else "(none)")
            line4 = (f"command={cmd} | raw={raw_target}"
                     f" | resolved_target_bot={resolved} | target_mode={tmode_d or 'none'}"
                     f" | mode_id={mode_val} | delegated={str(deleg_d).lower()}"
                     f" | executor={resolved} | target_online={str(online_d).lower()}")
        else:
            line4 = (f"command={cmd} | raw={raw_target}"
                     f" | resolved_target_bot={resolved}"
                     f" | delegated={str(deleg_d).lower()}")
        await _w(bot, user.id, line4[:249])


async def handle_aidelegations(bot, user, args: list[str]) -> None:
    """/aidelegations [limit] — show recent AI delegated task history."""
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin/owner only.")
        return
    limit = 8
    if len(args) >= 2 and args[1].isdigit():
        limit = min(20, max(1, int(args[1])))
    tasks = db.get_recent_delegated_tasks(limit)
    if not tasks:
        await _w(bot, user.id, "No AI delegated tasks recorded yet.")
        return
    await _w(bot, user.id, f"🤖 Last {len(tasks)} AI delegated tasks:")
    for t in tasks:
        st   = t["status"]
        icon = "✅" if st == "completed" else ("⏳" if st == "pending" else ("❌" if st == "failed" else "⏱️"))
        ts   = (t.get("created_at") or "")[:16]
        done = (t.get("completed_at") or "")
        err  = (t.get("error") or "")
        line = (f"{icon} #{t['id']} @{t['username']}"
                f"→@{t['target_bot_username']} | "
                f"{t['human_readable_action'][:45]} | {st}")
        if done:
            line += f" | done={done[11:16]}"
        if err:
            line += f" | err={err[:40]}"
        await _w(bot, user.id, line[:249])
