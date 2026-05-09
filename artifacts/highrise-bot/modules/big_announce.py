"""
modules/big_announce.py
-----------------------
Global big-find / big-catch announcement routing and bot-reaction control.
mining.py and fishing.py call send_big_announce() instead of bot.highrise.chat() directly
when a rare find or catch happens, so staff can configure thresholds in one place.
"""

import database as db
from modules.permissions import can_manage_economy

_RARITY_ORDER = [
    "common", "uncommon", "rare", "epic",
    "legendary", "mythic", "ultra_rare", "prismatic", "exotic",
]


def _rarity_idx(rarity: str) -> int:
    try:
        return _RARITY_ORDER.index(rarity)
    except ValueError:
        return 0


def should_big_announce(rarity: str) -> bool:
    """Return True if this rarity meets the global announce threshold."""
    enabled   = db.get_room_setting("big_announce_enabled", "1") == "1"
    if not enabled:
        return False
    threshold = db.get_room_setting("big_announce_threshold", "legendary")
    return _rarity_idx(rarity) >= _rarity_idx(threshold)


def should_bot_react(rarity: str) -> bool:
    """Return True if the bot should emote/react to this rarity."""
    threshold = db.get_room_setting("big_announce_bot_react_threshold", "prismatic")
    return _rarity_idx(rarity) >= _rarity_idx(threshold)


async def send_big_mine_announce(bot, rarity: str, username: str,
                                 item_name: str, item_emoji: str = "💎",
                                 extra: str = "") -> None:
    """Room-announce a big mining find if threshold is met."""
    if not should_big_announce(rarity):
        return
    rar_label = rarity.replace("_", " ").title()
    ann1 = "<#FFD700>📣 Big Find<#FFFFFF>"
    ann2 = f"{item_emoji} @{username} mined {rar_label} {item_name}{extra}"
    try:
        await bot.highrise.chat(f"{ann1}\n{ann2}"[:249])
    except Exception:
        pass
    if should_bot_react(rarity):
        try:
            await bot.highrise.emote("emoji_wow")
        except Exception:
            pass


async def send_big_fish_announce(bot, rarity: str, username: str,
                                 fish_name: str, fish_emoji: str = "🐟",
                                 extra: str = "") -> None:
    """Room-announce a big fishing catch if threshold is met."""
    if not should_big_announce(rarity):
        return
    rar_label = rarity.replace("_", " ").title()
    ann1 = "<#00CCFF>📣 Big Catch<#FFFFFF>"
    ann2 = f"{fish_emoji} @{username} caught {rar_label} {fish_name}{extra}"
    try:
        await bot.highrise.chat(f"{ann1}\n{ann2}"[:249])
    except Exception:
        pass
    if should_bot_react(rarity):
        try:
            await bot.highrise.emote("emoji_wow")
        except Exception:
            pass


async def handle_setbigannounce(bot, user, args: list[str]) -> None:
    """/setbigannounce on|off  OR  /setbigannounce threshold <rarity>"""
    if not can_manage_economy(user.username):
        await bot.highrise.send_whisper(user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id,
            "Usage: /setbigannounce on|off\n/setbigannounce threshold <rarity>")
        return
    sub = args[1].lower()
    if sub in ("on", "off"):
        db.set_room_setting("big_announce_enabled", "1" if sub == "on" else "0")
        await bot.highrise.send_whisper(user.id,
            f"✅ Big announce {'enabled' if sub == 'on' else 'disabled'}.")
    elif sub == "threshold" and len(args) >= 3:
        rarity = args[2].lower()
        if rarity not in _RARITY_ORDER:
            await bot.highrise.send_whisper(user.id,
                f"Rarities: {', '.join(_RARITY_ORDER)}")
            return
        db.set_room_setting("big_announce_threshold", rarity)
        await bot.highrise.send_whisper(user.id,
            f"✅ Big announce threshold: {rarity.replace('_',' ').title()}+")
    else:
        await bot.highrise.send_whisper(user.id,
            "Usage: /setbigannounce on|off\n/setbigannounce threshold <rarity>")


async def handle_setbigreact(bot, user, args: list[str]) -> None:
    """/setbigreact <rarity>"""
    if not can_manage_economy(user.username):
        await bot.highrise.send_whisper(user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id,
            "Usage: /setbigreact <rarity>  e.g. /setbigreact prismatic")
        return
    rarity = args[1].lower()
    if rarity not in _RARITY_ORDER:
        await bot.highrise.send_whisper(user.id,
            f"Rarities: {', '.join(_RARITY_ORDER)}")
        return
    db.set_room_setting("big_announce_bot_react_threshold", rarity)
    await bot.highrise.send_whisper(user.id,
        f"✅ Bot react threshold: {rarity.replace('_',' ').title()}+")


async def handle_bigannouncestatus(bot, user) -> None:
    """/bigannouncestatus"""
    enabled   = db.get_room_setting("big_announce_enabled", "1") == "1"
    threshold = db.get_room_setting("big_announce_threshold", "legendary")
    react_thr = db.get_room_setting("big_announce_bot_react_threshold", "prismatic")
    lines = [
        "📣 Big Announce Settings",
        f"Enabled: {'YES' if enabled else 'NO'}",
        f"Announce at: {threshold.replace('_',' ').title()}+",
        f"Bot react at: {react_thr.replace('_',' ').title()}+",
        "/setbigannounce threshold <rarity> | /setbigreact <rarity>",
    ]
    await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])
