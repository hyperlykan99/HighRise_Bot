"""
modules/big_announce.py
-----------------------
Per-category, per-rarity Big Find / Big Catch routing.

Routing modes:
  miner_only   — only GreatestProspector/MiningBot announces
  fishing_only — only MasterAngler/FishingBot announces
  all_bots     — activity bot sends full message; other enabled bots react
  off          — no public announcement

Commands:
  /setbigannounce <mining|fishing> <rarity> <mode>
  /setbotbigreact <botname> <on|off>
  /bigannouncestatus
  /bigannounce (help)
"""
from __future__ import annotations
import asyncio
import database as db
import config as _cfg
from modules.permissions import can_manage_economy

_RARITY_ORDER = [
    "common", "rare", "epic", "legendary",
    "mythic", "ultra_rare", "prismatic", "exotic",
]
_VALID_MODES = {"miner_only", "fishing_only", "all_bots", "off"}

# bot_mode → friendly name stored in big_announcement_bot_reactions
_MODE_TO_FRIENDLY: dict[str, str] = {
    "banker":    "bankingbot",
    "host":      "chilltopiamc",
    "eventhost": "eventbot",
    "security":  "securitybot",
    "poker":     "pokerbot",
    "blackjack": "blackjackbot",
    "miner":     "miningbot",
    "fisher":    "fishingbot",
    "dj":        "djbot",
    "all":       "allbot",
}
_FRIENDLY_TO_MODE: dict[str, str] = {v: k for k, v in _MODE_TO_FRIENDLY.items()}

# Short reaction messages per category × bot_mode  ({rar} = RARITY, {u} = username)
_REACT_MSGS: dict[str, dict[str, str]] = {
    "mining": {
        "banker":    "💰 {rar} reward check for @{u}.",
        "eventhost": "🔥 Big Find! Room energy activated.",
        "host":      "🎉 Congrats @{u} on the big find!",
        "dj":        "🎵 Big Find vibes from @{u}!",
        "security":  "🛡️ {rar} find verified.",
        "poker":     "🃏 Lucky energy from @{u}!",
        "blackjack": "♠️ Big Find energy! Watch out @{u}.",
    },
    "fishing": {
        "banker":    "💰 Big Catch reward for @{u}.",
        "eventhost": "🌊 Big Catch! Room energy activated.",
        "host":      "🎉 Huge catch by @{u}!",
        "dj":        "🎵 Big Catch vibes from @{u}!",
        "security":  "🛡️ {rar} catch verified.",
        "poker":     "🃏 Lucky fisher @{u}!",
        "blackjack": "♠️ Great catch @{u}!",
    },
}

_BIG_REACT_POLL_INTERVAL = 8  # seconds between reaction polls


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _rarity_idx(rarity: str) -> int:
    try:
        return _RARITY_ORDER.index(rarity)
    except ValueError:
        return 0


def _get_routing(category: str, rarity: str) -> str:
    """Return the routing mode for a category+rarity pair."""
    try:
        row = db.get_big_announce_setting(category, rarity)
        if row:
            return row.get("routing_mode", "off")
    except Exception:
        pass
    # Hard-coded fallbacks if DB not seeded yet
    _DEFAULTS: dict[tuple[str, str], str] = {
        ("mining",  "legendary"): "miner_only",
        ("mining",  "mythic"):    "miner_only",
        ("mining",  "ultra_rare"): "miner_only",
        ("mining",  "prismatic"): "all_bots",
        ("mining",  "exotic"):    "all_bots",
        ("fishing", "legendary"): "fishing_only",
        ("fishing", "mythic"):    "fishing_only",
        ("fishing", "ultra_rare"): "fishing_only",
        ("fishing", "prismatic"): "all_bots",
        ("fishing", "exotic"):    "all_bots",
    }
    return _DEFAULTS.get((category, rarity), "off")


# ---------------------------------------------------------------------------
# Public announce functions (called from mining.py / fishing.py)
# ---------------------------------------------------------------------------

async def send_big_mine_announce(bot, rarity: str, username: str,
                                  item_name: str, item_emoji: str = "💎",
                                  extra: str = "") -> None:
    """Room-announce a big mining find based on per-rarity routing mode."""
    mode = _get_routing("mining", rarity)
    if mode == "off":
        return
    rar_label = rarity.replace("_", " ").upper()
    ann = (f"📣 Big Find\n"
           f"{item_emoji} @{username} mined [{rar_label}] {item_name}{extra}")
    if mode in ("miner_only", "all_bots"):
        try:
            await bot.highrise.chat(ann[:249])
        except Exception:
            pass
    if mode == "all_bots":
        try:
            db.add_big_announce_pending("mining", rarity, item_name, "", username)
        except Exception:
            pass


async def send_big_fish_announce(bot, rarity: str, username: str,
                                  fish_name: str, fish_emoji: str = "🐟",
                                  extra: str = "") -> None:
    """Room-announce a big fishing catch based on per-rarity routing mode."""
    mode = _get_routing("fishing", rarity)
    if mode == "off":
        return
    rar_label = rarity.replace("_", " ").upper()
    ann = (f"📣 Big Catch\n"
           f"{fish_emoji} @{username} caught [{rar_label}] {fish_name}{extra}")
    if mode in ("fishing_only", "all_bots"):
        try:
            await bot.highrise.chat(ann[:249])
        except Exception:
            pass
    if mode == "all_bots":
        try:
            db.add_big_announce_pending("fishing", rarity, fish_name, "", username)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Background reaction poller (runs in each non-announcing bot)
# ---------------------------------------------------------------------------

async def startup_big_announce_reactor(bot) -> None:
    """
    Background task: polls big_announcement_logs for pending entries
    and sends short reactions from this bot if it is enabled.
    Should be started in on_start for all bot modes.
    Miner/fisher bots skip reactions for their own category (they sent the full message).
    """
    await asyncio.sleep(12)  # let room settle first
    bot_mode = _cfg.BOT_MODE
    friendly = _MODE_TO_FRIENDLY.get(bot_mode, bot_mode.lower() + "bot")
    while True:
        try:
            await _poll_react(bot, bot_mode, friendly)
        except Exception as exc:
            print(f"[BIG_REACT] poll error bot={bot_mode}: {exc}")
        await asyncio.sleep(_BIG_REACT_POLL_INTERVAL)


async def _poll_react(bot, bot_mode: str, friendly: str) -> None:
    """Process up to 2 pending big announce reactions for this bot."""
    # Skip if bot is the primary announcer for both categories (no reaction needed)
    if bot_mode == "miner" and bot_mode == "fisher":
        return
    try:
        enabled = db.get_big_announce_bot_reaction(friendly)
    except Exception:
        return
    if not enabled:
        return
    try:
        pending = db.get_pending_big_announce_reactions(friendly)
    except Exception:
        return
    if not pending:
        return
    for log in pending[:2]:
        category  = log.get("category", "mining")
        rarity    = log.get("rarity", "")
        username  = log.get("username", "")
        log_id    = log["id"]
        # Skip if this bot is the primary announcer for this category
        if bot_mode == "miner" and category == "mining":
            db.mark_big_announce_reacted(log_id, friendly)
            continue
        if bot_mode == "fisher" and category == "fishing":
            db.mark_big_announce_reacted(log_id, friendly)
            continue
        rar_label = rarity.replace("_", " ").upper()
        tmpl = _REACT_MSGS.get(category, {}).get(bot_mode, "")
        if tmpl:
            msg = tmpl.format(rar=rar_label, u=username)
            try:
                await asyncio.sleep(1)
                await bot.highrise.chat(msg[:249])
            except Exception:
                pass
        try:
            db.mark_big_announce_reacted(log_id, friendly)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def handle_setbigannounce(bot, user, args: list[str]) -> None:
    """/setbigannounce <mining|fishing> <rarity> <mode>"""
    if not can_manage_economy(user.username):
        await bot.highrise.send_whisper(user.id, "Manager/admin/owner only.")
        return
    if len(args) < 4:
        await bot.highrise.send_whisper(user.id,
            "Usage: !setbigannounce mining exotic all_bots\n"
            "Modes: miner_only|fishing_only|all_bots|off")
        return
    category = args[1].lower()
    rarity   = args[2].lower()
    mode     = args[3].lower()
    if category not in ("mining", "fishing"):
        await bot.highrise.send_whisper(user.id, "Category: mining or fishing")
        return
    if rarity not in _RARITY_ORDER:
        await bot.highrise.send_whisper(user.id,
            f"Rarities: {', '.join(_RARITY_ORDER)}")
        return
    if mode not in _VALID_MODES:
        await bot.highrise.send_whisper(user.id,
            "Modes: miner_only|fishing_only|all_bots|off")
        return
    try:
        db.set_big_announce_setting(category, rarity, mode)
        await bot.highrise.send_whisper(user.id,
            f"✅ {category.title()} [{rarity.upper()}] → {mode}")
    except Exception as exc:
        await bot.highrise.send_whisper(user.id, f"Error: {exc}")


async def handle_setbotbigreact(bot, user, args: list[str]) -> None:
    """/setbotbigreact <botname> <on|off>"""
    if not can_manage_economy(user.username):
        await bot.highrise.send_whisper(user.id, "Manager/admin/owner only.")
        return
    if len(args) < 3:
        await bot.highrise.send_whisper(user.id,
            "Usage: !setbotbigreact BankingBot on\n"
            "Bots: BankingBot EventBot ChillTopiaMC MiningBot FishingBot etc.")
        return
    raw_name = args[1].lower()
    val      = 1 if args[2].lower() in ("on", "1", "yes") else 0
    # Accept friendly name or mode name
    if raw_name in _FRIENDLY_TO_MODE:
        friendly = raw_name
    elif raw_name in _MODE_TO_FRIENDLY:
        friendly = _MODE_TO_FRIENDLY[raw_name]
    else:
        friendly = raw_name
    try:
        db.set_big_announce_bot_reaction(friendly, val)
        state = "ON" if val else "OFF"
        await bot.highrise.send_whisper(user.id, f"✅ {args[1]} reactions: {state}")
    except Exception as exc:
        await bot.highrise.send_whisper(user.id, f"Error: {exc}")


async def handle_bigannouncestatus(bot, user) -> None:
    """/bigannouncestatus — show per-category/rarity routing + enabled bot reactions."""
    lines = ["📣 Big Announcements"]
    for cat in ("mining", "fishing"):
        cat_lines = []
        for rar in ("legendary", "mythic", "prismatic", "exotic"):
            m = _get_routing(cat, rar)
            if m != "off":
                cat_lines.append(f"{rar.upper()} → {m}")
        if cat_lines:
            lines.append(f"\n{cat.title()}:")
            lines.extend(cat_lines)
    try:
        reactions = db.get_all_big_announce_bot_reactions()
        on_list  = [r["bot_name"].title() for r in reactions if r.get("enabled")]
        off_list = [r["bot_name"].title() for r in reactions if not r.get("enabled")]
        if on_list:
            lines.append(f"\nReact ON: {', '.join(on_list[:5])}")
        if off_list:
            lines.append(f"React OFF: {', '.join(off_list[:4])}")
    except Exception:
        pass
    await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])


async def handle_bigannounce_help(bot, user) -> None:
    """/bigannounce — show Big Announce help."""
    await bot.highrise.send_whisper(user.id,
        "📣 Big Announce Settings\n"
        "!setbigannounce mining exotic all_bots\n"
        "!setbigannounce fishing prismatic fishing_only\n"
        "!setbotbigreact BankingBot on\n"
        "!bigannouncestatus")
