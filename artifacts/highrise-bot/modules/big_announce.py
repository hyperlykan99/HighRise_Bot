"""
modules/big_announce.py
-----------------------
Per-category, per-rarity Big Find / Big Catch routing.

Routing modes:
  miner_only   — only GreatestProspector/MiningBot announces
  fishing_only — only MasterAngler/FishingBot announces
  all_bots     — activity bot sends full message; other enabled bots repeat details
  off          — no public announcement

Commands:
  !setbigannounce <mining|fishing> <rarity> <mode>
  !setbotbigreact <botname> <on|off>
  !bigannouncestatus
  !bigannounce (help)
  !previewannounce <mining|fishing> <rarity> [item_name]
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

_BIG_REACT_POLL_INTERVAL = 8  # seconds between reaction polls

# Colored rarity labels for announcements
_ANN_LBLS: dict[str, str] = {
    "common":     "<#AAAAAA>[COMMON]<#FFFFFF>",
    "uncommon":   "<#66BBAA>[UNCOMMON]<#FFFFFF>",
    "rare":       "<#3399FF>[RARE]<#FFFFFF>",
    "epic":       "<#B266FF>[EPIC]<#FFFFFF>",
    "legendary":  "<#FFD700>[LEGENDARY]<#FFFFFF>",
    "mythic":     "<#FF66CC>[MYTHIC]<#FFFFFF>",
    "ultra_rare": "<#FF66CC>[ULTRA RARE]<#FFFFFF>",
    "prismatic":  ("<#FF0000>[P<#FF9900>R<#FFFF00>I<#00FF00>S"
                   "<#00CCFF>M<#3366FF>A<#9933FF>T<#FF66CC>I<#FF0000>C]<#FFFFFF>"),
    "exotic":     "<#FF0000>[EXOTIC]<#FFFFFF>",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_clip(msg: str, limit: int = 220) -> str:
    """Clip message safely, backing up before any partial <#XXXXXX> color tag."""
    if len(msg) <= limit:
        return msg
    cut = limit
    for back in range(min(12, cut)):
        if msg[cut - 1 - back] == "<":
            close = msg.find(">", cut - 1 - back)
            if close > cut:
                cut = cut - 1 - back
                break
    return msg[:cut].rstrip()


def _display_name(rarity: str, name: str) -> str:
    """Return colored name: pink for prismatic, red for exotic, plain otherwise."""
    if rarity == "prismatic":
        return f"<#FF66CC>{name}<#FFFFFF>"
    if rarity == "exotic":
        return f"<#FF0000>{name}<#FFFFFF>"
    return name


def _fmt_num(v: object) -> str:
    """Format a numeric value with comma separators."""
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_ann(category: str, username: str, rar_label: str, disp_name: str,
             emoji: str, weight_str: str, value_str: str, xp_str: str) -> list[str]:
    """
    Build announcement message(s). Returns [one_msg] if ≤220 chars,
    else [header+name_line, stats_line] — always safe to send.
    """
    unit = "MXP" if category == "mining" else "FXP"
    verb = "mined" if category == "mining" else "caught"
    hdr  = "⛏️ BIG FIND!" if category == "mining" else "🎣 BIG CATCH!"

    line2 = f"{emoji} @{username} {verb} {rar_label} {disp_name}"

    if weight_str and value_str:
        if xp_str:
            line3 = f"⚖️ {weight_str} | 💰 {value_str}c | ⭐ +{xp_str} {unit}"
        else:
            line3 = f"⚖️ {weight_str} | 💰 {value_str}c"
        full = f"{hdr}\n{line2}\n{line3}"
        if len(full) <= 220:
            return [full]
        msg1 = f"{hdr}\n{line2}"
        return [_safe_clip(msg1, 220), line3]
    else:
        full = f"{hdr}\n{line2}"
        return [_safe_clip(full, 220)]


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
    _DEFAULTS: dict[tuple[str, str], str] = {
        ("mining",  "legendary"):  "miner_only",
        ("mining",  "mythic"):     "miner_only",
        ("mining",  "ultra_rare"): "miner_only",
        ("mining",  "prismatic"):  "all_bots",
        ("mining",  "exotic"):     "all_bots",
        ("fishing", "legendary"):  "fishing_only",
        ("fishing", "mythic"):     "fishing_only",
        ("fishing", "ultra_rare"): "fishing_only",
        ("fishing", "prismatic"):  "all_bots",
        ("fishing", "exotic"):     "all_bots",
    }
    return _DEFAULTS.get((category, rarity), "off")


# ---------------------------------------------------------------------------
# Public announce functions (called from mining.py / fishing.py)
# ---------------------------------------------------------------------------

async def send_big_mine_announce(bot, rarity: str, username: str,
                                  item_name: str, item_emoji: str = "💎",
                                  extra: str = "",
                                  weight: object = None,
                                  value: object = None,
                                  xp: object = None) -> None:
    """Room-announce a big mining find based on per-rarity routing mode."""
    mode = _get_routing("mining", rarity)
    if mode == "off":
        return

    rar_label  = _ANN_LBLS.get(rarity, f"[{rarity.replace('_', ' ').upper()}]")
    disp_name  = _display_name(rarity, item_name)
    weight_str = f"{weight}kg" if weight is not None else ""
    value_str  = _fmt_num(value) if value is not None else ""
    xp_str     = str(int(xp)) if xp is not None else ""

    msgs = _fmt_ann("mining", username, rar_label, disp_name, item_emoji,
                    weight_str, value_str, xp_str)

    if mode in ("miner_only", "all_bots"):
        for i, m in enumerate(msgs):
            try:
                await bot.highrise.chat(_safe_clip(m, 220))
            except Exception:
                pass
            if i < len(msgs) - 1:
                await asyncio.sleep(0.5)

    if mode == "all_bots":
        try:
            db.add_big_announce_pending(
                "mining", rarity, item_name, "", username,
                weight_str=weight_str, value_str=value_str,
                xp_str=xp_str, item_emoji=item_emoji,
            )
        except Exception:
            pass


async def send_big_fish_announce(bot, rarity: str, username: str,
                                  fish_name: str, fish_emoji: str = "🐟",
                                  extra: str = "",
                                  weight: object = None,
                                  value: object = None,
                                  xp: object = None) -> None:
    """Room-announce a big fishing catch based on per-rarity routing mode."""
    mode = _get_routing("fishing", rarity)
    if mode == "off":
        return

    rar_label  = _ANN_LBLS.get(rarity, f"[{rarity.replace('_', ' ').upper()}]")
    disp_name  = _display_name(rarity, fish_name)
    weight_str = f"{weight}lb" if weight is not None else ""
    value_str  = _fmt_num(value) if value is not None else ""
    xp_str     = str(int(xp)) if xp is not None else ""

    msgs = _fmt_ann("fishing", username, rar_label, disp_name, fish_emoji,
                    weight_str, value_str, xp_str)

    if mode in ("fishing_only", "all_bots"):
        for i, m in enumerate(msgs):
            try:
                await bot.highrise.chat(_safe_clip(m, 220))
            except Exception:
                pass
            if i < len(msgs) - 1:
                await asyncio.sleep(0.5)

    if mode == "all_bots":
        try:
            db.add_big_announce_pending(
                "fishing", rarity, fish_name, "", username,
                weight_str=weight_str, value_str=value_str,
                xp_str=xp_str, item_emoji=fish_emoji,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Background reaction poller (runs in each non-announcing bot)
# ---------------------------------------------------------------------------

async def startup_big_announce_reactor(bot) -> None:
    """
    Background task: polls big_announcement_logs for pending entries
    and sends the full detail message from this bot if it is enabled.
    Miner/fisher bots skip reactions for their own category.
    """
    await asyncio.sleep(12)
    bot_mode = _cfg.BOT_MODE
    friendly = _MODE_TO_FRIENDLY.get(bot_mode, bot_mode.lower() + "bot")
    while True:
        try:
            await _poll_react(bot, bot_mode, friendly)
        except Exception as exc:
            print(f"[BIG_REACT] poll error bot={bot_mode}: {exc}")
        await asyncio.sleep(_BIG_REACT_POLL_INTERVAL)


async def _poll_react(bot, bot_mode: str, friendly: str) -> None:
    """Process up to 2 pending big announce entries for this bot."""
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

        # Primary announcer skips re-sending its own category
        if bot_mode == "miner" and category == "mining":
            db.mark_big_announce_reacted(log_id, friendly)
            continue
        if bot_mode == "fisher" and category == "fishing":
            db.mark_big_announce_reacted(log_id, friendly)
            continue

        item_name  = log.get("item_name", "")
        weight_str = log.get("weight_str", "")
        value_str  = log.get("value_str", "")
        xp_str     = log.get("xp_str", "")
        emoji      = log.get("item_emoji", "") or ("💎" if category == "mining" else "🐟")

        rar_label  = _ANN_LBLS.get(rarity, f"[{rarity.replace('_', ' ').upper()}]")
        disp_name  = _display_name(rarity, item_name)
        msgs = _fmt_ann(category, username, rar_label, disp_name, emoji,
                        weight_str, value_str, xp_str)

        try:
            await asyncio.sleep(1)
            for i, m in enumerate(msgs):
                await bot.highrise.chat(_safe_clip(m, 220))
                if i < len(msgs) - 1:
                    await asyncio.sleep(0.5)
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
        "!bigannouncestatus\n"
        "!previewannounce mining prismatic")


async def handle_previewannounce(bot, user, args: list[str]) -> None:
    """/previewannounce <mining|fishing> <rarity> [item_name...]
    Whispers a preview of the formatted big announcement, then sends it
    to the room once so staff can verify colors and layout.
    """
    if not can_manage_economy(user.username):
        await bot.highrise.send_whisper(user.id, "Manager/admin/owner only.")
        return
    if len(args) < 3:
        await bot.highrise.send_whisper(user.id,
            "Usage: !previewannounce mining prismatic [item name]\n"
            "Example: !previewannounce fishing prismatic Aurora Koi")
        return
    category = args[1].lower()
    rarity   = args[2].lower()
    if category not in ("mining", "fishing"):
        await bot.highrise.send_whisper(user.id, "Category: mining or fishing")
        return
    if rarity not in _RARITY_ORDER:
        await bot.highrise.send_whisper(user.id,
            f"Valid rarities: {', '.join(_RARITY_ORDER)}")
        return

    item_name  = " ".join(args[3:]) if len(args) > 3 else (
        "Opal Prism Ore" if category == "mining" else "Aurora Koi"
    )
    emoji      = "💎" if category == "mining" else "🐟"
    weight_str = "2.5kg" if category == "mining" else "24.1lb"
    value_str  = "180,000"
    xp_str     = "500"

    rar_label  = _ANN_LBLS.get(rarity, f"[{rarity.replace('_', ' ').upper()}]")
    disp_name  = _display_name(rarity, item_name)
    msgs       = _fmt_ann(category, user.username, rar_label, disp_name,
                          emoji, weight_str, value_str, xp_str)

    await bot.highrise.send_whisper(user.id,
        f"📣 Preview — {rarity} {category} ({len(msgs)} msg):")
    for m in msgs:
        await bot.highrise.send_whisper(user.id, m[:249])

    for m in msgs:
        try:
            await asyncio.sleep(0.3)
            await bot.highrise.chat(_safe_clip(m, 220))
        except Exception:
            pass
