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
