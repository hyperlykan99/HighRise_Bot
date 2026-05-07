"""
modules/ai_assistant.py
-----------------------
AI assistant layer — natural language command matching and confirmation system.

Supports:
- /ask, /ai, /assistant <message>
- Natural language triggers: "bot," prefix, @botname mention
- yes/no natural confirmation for pending actions
- Risk-classified command matching (SAFE / CONFIRM / ADMIN_CONFIRM / BLOCKED)
- Per-bot-mode personality responses
- Pending action storage in ai_pending_actions DB table

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
    # SAFE — informational only, suggest the command
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
    # CONFIRM — ask user before executing
    "send": CONFIRM, "buy": CONFIRM, "sellores": CONFIRM,
    "sellore": CONFIRM, "minebuy": CONFIRM, "equip": CONFIRM,
    "eventshop": CONFIRM, "buyevent": CONFIRM,
    # ADMIN_CONFIRM — admin/owner only + must confirm
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
}


def _risk_for(cmd: str) -> str:
    return _RISK.get(cmd, SAFE)


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
# Each entry: (compiled_regex, command, args_fn(match, text)->str, human_fn(match, text)->str)
# Evaluated top-to-bottom; first match wins.
# ---------------------------------------------------------------------------

def _k(v: str):
    """Constant-valued lambda for args_fn / human_fn."""
    return lambda m, t: v


_INTENTS: list[tuple] = [
    # ── Mining ────────────────────────────────────────────────────────────────
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

    # ── Economy ───────────────────────────────────────────────────────────────
    (re.compile(
        r"(show\s+|check\s+|see\s+|my\s+|what.?s\s+(my\s+)?)?"
        r"(balance|coins|tokens|wallet|money)\b"
        r"|how\s+many\s+coins|how\s+much\s+(do\s+i|have\s+i)", re.I),
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

    # send: "send 100 coins to Claire" / "transfer 500 to @Marion"
    (re.compile(
        r"(send|transfer|give)\s+([\d,]+)\s*(coins?|tokens?)?\s*(?:to|for)\s+@?(\w+)",
        re.I),
     "send",
     lambda m, t: f"{m.group(4)} {m.group(2).replace(',', '')}",
     lambda m, t: f"send {m.group(2).replace(',', '')} coins to @{m.group(4)}"),

    # ── Shop ──────────────────────────────────────────────────────────────────
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

    # ── Games ─────────────────────────────────────────────────────────────────
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

    # ── Events ────────────────────────────────────────────────────────────────
    (re.compile(
        r"(events?\s*(help)?\s*$|what.*events?|show.*events?|event\s+(info|guide|help))",
        re.I),
     "eventhelp", _k(""), _k("show event help")),

    (re.compile(
        r"(current\s+|active\s+)?(event|events)\s+(status|now|active|running)", re.I),
     "eventstatus", _k(""), _k("show current event status")),

    # ── Help / commands ───────────────────────────────────────────────────────
    (re.compile(
        r"what\s+commands?|(show|see|list)\s+(all\s+)?commands?"
        r"|help\s+me$|what\s+can\s+(you|i)\s+do|what\s+do\s+you\s+do|^guide$", re.I),
     "help", _k(""), _k("show available commands")),

    (re.compile(r"(my\s+commands?|commands?\s+(for\s+me|i\s+can\s+use|available))", re.I),
     "mycommands", _k(""), _k("show your commands")),

    # ── Profile ───────────────────────────────────────────────────────────────
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

    # ── Admin — bank settings ─────────────────────────────────────────────────
    (re.compile(
        r"set\s+(max|maximum)\s*(send|transfer)\s*(limit)?\s*(to\s*)?([\d,]+)", re.I),
     "setmaxsend",
     lambda m, t: m.group(5).replace(",", ""),
     lambda m, t: f"set maximum send limit to {int(m.group(5).replace(',', '')):,} coins"),

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

    # start X event — order matters: match "start event X" before generic
    (re.compile(r"start\s+event\s+(\w+)", re.I),
     "startevent",
     lambda m, t: m.group(1).lower(),
     lambda m, t: f"start {m.group(1).lower()} event"),

    (re.compile(r"start\s+(a?n?\s*)?(\w+)\s+event\b", re.I),
     "startevent",
     lambda m, t: m.group(2).lower(),
     lambda m, t: f"start {m.group(2).lower()} event"),

    (re.compile(r"(stop|end|cancel)\s+(the\s+|current\s+)?(event|game)\b", re.I),
     "stopevent", _k(""), _k("stop the current event")),

    # ── Admin — coins ─────────────────────────────────────────────────────────
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
]


# ---------------------------------------------------------------------------
# Static response templates for SAFE commands (avoids executing them)
# ---------------------------------------------------------------------------

_SAFE_RESPONSES: dict[str, str] = {
    "minehelp":     "⛏️ Use /minehelp for the full mining guide. /mine to dig, /ores for inventory.",
    "mine":         "⛏️ Use /mine to mine. Check /tool for pickaxe stats, /ores for your haul.",
    "ores":         "⛏️ Use /ores to view your ore inventory.",
    "tool":         "⛏️ Use /tool to check your pickaxe stats.",
    "mineshop":     "⛏️ Use /mineshop to browse mining upgrades and items.",
    "bal":          "💰 Use /bal to check your coin balance.",
    "bank":         "🏦 Use /bank to view your bank info.",
    "transactions": "📋 Use /transactions to view recent transactions.",
    "daily":        "🎁 Use /daily to claim your daily coin reward.",
    "leaderboard":  "🏆 Use /leaderboard or /lb to see the top coin holders.",
    "shop":         "🛒 Use /shop to open the main shop, or /shop badges for emoji badges.",
    "help":         "❓ Use /help for all commands, /mycommands for your personal list.",
    "mycommands":   "📋 Use /mycommands to see commands you can use.",
    "pokerhelp":    "♠️ Use /pokerhelp to learn poker rules and how to join a table.",
    "bjhelp":       "🃏 Use /bjhelp for blackjack rules. /rbjhelp for Realistic Blackjack.",
    "rbjhelp":      "🃏 Use /rbjhelp for Realistic Blackjack rules.",
    "eventhelp":    "🎉 Use /eventhelp for event info, /event to see the active event.",
    "eventstatus":  "🎉 Use /eventstatus to check the current event.",
    "me":           "👤 Use /me to view your profile stats.",
    "quests":       "📜 Use /quests to see your active quests, /dailyquests for today's.",
    "bothealth":    "🤖 Use /bothealth to see bot status.",
    "modulehealth": "🤖 Use /modulehealth to check module status.",
    "botheartbeat": "🤖 Use /botheartbeat to see live heartbeats.",
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


def _is_ai_trigger(message: str, bot_username: str) -> bool:
    """Return True if the message is a natural-language AI trigger."""
    low = message.lower().strip()
    if low.startswith("bot,") or low.startswith("bot "):
        return True
    if bot_username and f"@{bot_username.lower()}" in low:
        return True
    return False


def _strip_trigger(message: str, bot_username: str) -> str:
    """Remove the trigger prefix and return the clean question text."""
    low = message.lower().strip()
    if low.startswith("bot,"):
        return message[4:].strip()
    if low.startswith("bot "):
        return message[4:].strip()
    if bot_username:
        tag = f"@{bot_username}"
        if low.startswith(tag.lower()):
            return message[len(tag):].strip().lstrip(",").strip()
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
# Uses lazy importlib to avoid circular imports; inspect to handle
# handlers that take (bot, user) vs (bot, user, args).
# ---------------------------------------------------------------------------

_HANDLER_MAP: dict[str, tuple[str, str]] = {
    # Economy / bank
    "send":        ("modules.bank",       "handle_send"),
    "bank":        ("modules.bank",       "handle_bank"),
    "transactions":("modules.bank",       "handle_transactions"),
    # Mining
    "mine":        ("modules.mining",     "handle_mine"),
    "sellores":    ("modules.mining",     "handle_sellores"),
    "sellore":     ("modules.mining",     "handle_sellore"),
    "minebuy":     ("modules.mining",     "handle_minebuy"),
    "mineshop":    ("modules.mining",     "handle_mineshop"),
    # Shop
    "buy":         ("modules.shop",       "handle_buy"),
    "equip":       ("modules.shop",       "handle_equip"),
    "shop":        ("modules.shop",       "handle_shop"),
    # Events
    "startevent":  ("modules.events",     "handle_startevent"),
    "stopevent":   ("modules.events",     "handle_stopevent"),
    "eventshop":   ("modules.events",     "handle_eventshop"),
    "buyevent":    ("modules.events",     "handle_buyevent"),
    # Admin — coins
    "setcoins":    ("modules.admin_cmds", "handle_setcoins"),
    "resetcoins":  ("modules.admin_cmds", "handle_resetcoins"),
}


async def _execute_confirmed(bot, user, command: str, args_str: str) -> None:
    """Execute a confirmed command by lazy-importing and calling its handler."""
    args_list = [command] + (args_str.split() if args_str.strip() else [])
    fallback  = f"✅ Type /{command}{(' ' + args_str) if args_str else ''} to execute."

    if command not in _HANDLER_MAP:
        await _w(bot, user.id, fallback)
        return

    module_path, fn_name = _HANDLER_MAP[command]
    try:
        mod = importlib.import_module(module_path)
        fn  = getattr(mod, fn_name)
        # Some handlers take (bot, user, args); others only (bot, user)
        sig    = inspect.signature(fn)
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
        await _w(bot, user.id, "I can't help with that. Try /help for available commands.")
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
# Called for ANY chat message (slash or not) when user has a pending action.
# Returns True if the message was consumed.
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
        return False  # No pending action — let message flow normally

    if _is_no(message):
        db.cancel_pending_ai_action(user.id)
        await _w(bot, user.id, "❌ Cancelled.")
        db.log_ai_action(
            user.username, action["proposed_command"],
            action["proposed_command"], action["risk_level"], "cancelled",
        )
        return True

    # User said yes
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

    Handles:
    1. yes/no responses to pending AI actions (any message, any bot in room)
    2. Natural-language triggers: "bot," prefix, @botname mention (non-slash)
    """
    from config import BOT_USERNAME

    # ── 1. yes/no pending confirmation (any bot checks; anti-spam via _should_answer_ai) ──
    if _is_yes(message) or _is_no(message):
        # Only respond if this bot owns AI; prevents double-replies
        if _should_answer_ai():
            return await handle_natural_confirmation(bot, user, message)
        # Non-AI bot: consume if pending action exists so the yes/no doesn't
        # fall through as an unrecognised command.
        if db.get_pending_ai_action(user.id) is not None:
            return True
        return False

    # ── 2. Natural language AI trigger (non-slash messages only) ──────────────
    if message.startswith("/"):
        return False  # slash commands handled by normal routing

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
        return  # anti-spam: only host/eventhost/all reply

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
