"""
modules/ai_command_executor.py — Calls existing bot handlers from AI Command Layer (3.3F).

Does NOT directly touch the database.
Does NOT eval or exec user text.
Does NOT import arbitrary modules from user input.
Only calls handlers from the whitelist registry.
Uses lazy per-handler imports to avoid circular dependencies.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot, User


async def execute_command(
    bot:     "BaseBot",
    user:    "User",
    cmd_key: str,
    args:    list[str],
) -> bool:
    """
    Execute a whitelisted command by calling its existing handler.
    Returns True if executed, False if the command_key is unknown.
    """
    handler = _DISPATCH.get(cmd_key)
    if handler is None:
        return False
    await handler(bot, user, args)
    return True


# ── Individual handler wrappers ───────────────────────────────────────────────

async def _balance(bot, user, args):
    try:
        from modules.admin_cmds import handle_balance
    except ImportError:
        from modules.economy import handle_balance
    await handle_balance(bot, user, ["balance"])


async def _tickets(bot, user, args):
    from modules.luxe import handle_tickets
    await handle_tickets(bot, user, ["tickets"])


async def _daily(bot, user, args):
    try:
        from modules.quests import handle_daily
    except ImportError:
        try:
            from modules.admin_cmds import handle_daily
        except ImportError:
            from modules.economy import handle_daily
    await handle_daily(bot, user)


async def _mine(bot, user, args):
    from modules.mining import handle_mine
    await handle_mine(bot, user)


async def _fish(bot, user, args):
    from modules.fishing import handle_fish
    await handle_fish(bot, user)


async def _profile(bot, user, args):
    from modules.profile import handle_profile_cmd
    await handle_profile_cmd(bot, user, ["profile"])


async def _events(bot, user, args):
    from modules.events import handle_events
    await handle_events(bot, user)


async def _nextevent(bot, user, args):
    from modules.events import handle_nextevent
    await handle_nextevent(bot, user)


async def _shop(bot, user, args):
    from modules.shop import handle_shop
    await handle_shop(bot, user, ["shop"])


async def _luxeshop(bot, user, args):
    from modules.luxe import handle_luxeshop
    await handle_luxeshop(bot, user, ["luxeshop"])


async def _vipstatus(bot, user, args):
    from modules.admin_cmds import handle_vipstatus
    await handle_vipstatus(bot, user, ["vipstatus"])


async def _tele(bot, user, args):
    from modules.room_utils import handle_tpme
    dest = args[0] if args else ""
    await handle_tpme(bot, user, ["tpme", dest] if dest else ["tpme"])


async def _tele_self(bot, user, args):
    from modules.room_utils import ai_teleport_to_spawn
    dest = args[0] if args else ""
    if not dest:
        await bot.highrise.send_whisper(user.id, "Destination required. Example: ai tele me to bar")
        return
    await ai_teleport_to_spawn(bot, user, dest)


async def _tele_other(bot, user, args):
    from modules.room_utils import handle_tp
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: teleport <username> to <spawn>")
        return
    await handle_tp(bot, user, ["tp", args[0], args[1]])


async def _goto_user(bot, user, args):
    from modules.room_utils import handle_goto
    if not args:
        await bot.highrise.send_whisper(user.id, "Usage: go to <username>")
        return
    await handle_goto(bot, user, ["goto", args[0]])


async def _bring_user(bot, user, args):
    from modules.room_utils import handle_tphere
    if not args:
        await bot.highrise.send_whisper(user.id, "Usage: bring <username>")
        return
    await handle_tphere(bot, user, ["tphere", args[0]])


async def _return_bot_spawn(bot, user, args):
    from modules.room_utils import teleport_bot_to_saved_spawn
    success = await teleport_bot_to_saved_spawn(bot, fallback_walk=True)
    if not success:
        await bot.highrise.send_whisper(
            user.id,
            "\u26a0\ufe0f No saved spawn found for this bot. "
            "Use !setbotspawnhere @BotName first.",
        )


async def _set_ai_cost_preview(bot, user, args):
    from modules.ai_cost_preview import (
        is_cost_preview_required, cost_preview_status_msg,
    )
    from modules.ai_confirmation_manager import set_pending, get_pending, preview_message
    from modules.ai_perms import get_perm_level, PERM_OWNER

    perm = get_perm_level(user.username)
    val  = args[0].lower() if args else ""

    if not val or val in ("status", "?"):
        await bot.highrise.send_whisper(user.id, cost_preview_status_msg())
        return

    if perm != PERM_OWNER:
        await bot.highrise.send_whisper(user.id, "\U0001f512 AI cost preview setting is owner only.")
        return

    new_val: str | None = None
    if val in ("on", "enable", "true", "1"):
        new_val = "on"
    elif val in ("off", "disable", "false", "0"):
        new_val = "off"

    if not new_val:
        await bot.highrise.send_whisper(user.id, cost_preview_status_msg())
        return

    current = "on" if is_cost_preview_required() else "off"
    if current == new_val:
        await bot.highrise.send_whisper(user.id, f"AI cost preview is already {new_val.upper()}.")
        return

    effect = (
        "Paid AI answers show cost first."
        if new_val == "on" else
        "1\u20133 \U0001f3ab answers auto-charge after success."
    )
    set_pending(
        user_id        = user.id,
        action_key     = "set_ai_cost_preview",
        label          = "AI Cost Preview",
        confirm_phrase = "CONFIRM AI COST PREVIEW",
        current_value  = current,
        new_value      = new_val,
        risk           = f"Medium \u2014 {effect}",
    )
    p = get_pending(user.id)
    if p:
        await bot.highrise.send_whisper(user.id, preview_message(p))


async def _buyvip(bot, user, args):
    from modules.vip import handle_buyvip
    await handle_buyvip(bot, user, ["buyvip"])


async def _buy(bot, user, args):
    from modules.shop import handle_buy
    full_args = ["buy"] + args
    await handle_buy(bot, user, full_args)


async def _mute(bot, user, args):
    # args: [target_username, duration_minutes, reason]
    from modules.moderation import handle_mute
    full_args = ["mute"] + args
    await handle_mute(bot, user, full_args)


async def _warn(bot, user, args):
    from modules.moderation import handle_warn
    full_args = ["warn"] + args
    await handle_warn(bot, user, full_args)


async def _startevent(bot, user, args):
    from modules.events import handle_startevent
    full_args = ["startevent"] + args
    await handle_startevent(bot, user, full_args)


async def _stopevent(bot, user, args):
    from modules.events import handle_stopevent
    await handle_stopevent(bot, user, ["stopevent"])


async def _setvipprice(bot, user, args):
    from modules.admin_cmds import handle_setvipprice
    full_args = ["setvipprice"] + args
    await handle_setvipprice(bot, user, full_args)


async def _addcoins(bot, user, args):
    # args: [target_username, amount]
    from modules.admin_cmds import handle_addcoins
    full_args = ["addcoins"] + args
    await handle_addcoins(bot, user, full_args)


async def _setcoins(bot, user, args):
    from modules.admin_cmds import handle_setcoins
    full_args = ["setcoins"] + args
    await handle_setcoins(bot, user, full_args)


# ── Dispatch table ────────────────────────────────────────────────────────────

_DISPATCH: dict[str, callable] = {
    "balance":    _balance,
    "tickets":    _tickets,
    "daily":      _daily,
    "mine":       _mine,
    "fish":       _fish,
    "profile":    _profile,
    "events":     _events,
    "nextevent":  _nextevent,
    "shop":       _shop,
    "luxeshop":   _luxeshop,
    "vipstatus":  _vipstatus,
    "tele":       _tele,
    "tele_self":  _tele_self,
    "tele_other": _tele_other,
    "goto_user":  _goto_user,
    "bring_user":          _bring_user,
    "return_bot_spawn":    _return_bot_spawn,
    "set_ai_cost_preview": _set_ai_cost_preview,
    "buyvip":     _buyvip,
    "buy":        _buy,
    "mute":       _mute,
    "warn":       _warn,
    "startevent": _startevent,
    "stopevent":  _stopevent,
    "setvipprice":_setvipprice,
    "addcoins":   _addcoins,
    "setcoins":   _setcoins,
}
