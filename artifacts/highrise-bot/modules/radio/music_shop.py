"""Music Disc shop commands for the rebuilt DJ system."""

from __future__ import annotations

import database as db
from modules import permissions
from modules.luxe import deduct_luxe_balance, get_luxe_balance, log_luxe_transaction
from modules.radio import music_discs as discs
from modules.radio import settings as rs


async def _w(bot, user_id: str, message: str) -> None:
    try:
        await bot.highrise.send_whisper(user_id, message)
    except Exception:
        pass


def _display_name() -> str:
    return str(rs.get_setting("music_disc_display_name", "Song Request 💽"))


def _staff(user) -> bool:
    return permissions.is_staff(user.username)


def _owner(user) -> bool:
    return permissions.is_owner(user.username)


def _positive_int(raw, default: int = 1) -> int:
    try:
        return max(0, int(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def _strip_command_arg(args, command: str) -> list[str]:
    cleaned = list(args or [])
    if cleaned and str(cleaned[0]).strip().lower() == command:
        return cleaned[1:]
    return cleaned


async def handle_musicshop(bot, user, args=None) -> None:
    rs.ensure_radio_settings()
    discs.ensure_schema()
    name = _display_name()
    enabled = rs.get_bool_setting("music_shop_enabled", True)
    coins_enabled = rs.get_bool_setting("music_disc_purchase_coins_enabled", True)
    luxe_enabled = rs.get_bool_setting("music_disc_purchase_luxe_enabled", True)
    coin_price = rs.get_int_setting("music_disc_price_coins", 500)
    luxe_price = rs.get_int_setting("music_disc_price_luxe", 50)
    normal_cost = rs.get_int_setting("request_disc_cost_normal", 1)
    vip_cost = rs.get_int_setting("request_disc_cost_vip", 1)
    balance = discs.get_disc_balance(user.id)
    lines = [
        "🎵 Music Shop",
        f"Balance: {balance} {name}",
        f"Normal song request: {normal_cost} {name}",
        f"VIP song request: {vip_cost} {name}",
    ]
    if not enabled:
        lines.append("🔒 Purchases are currently disabled.")
    else:
        if coins_enabled:
            lines.append(f"Buy with coins: !buydisc coins ({coin_price} coins)")
        if luxe_enabled:
            lines.append(f"Buy with Luxe: !buydisc luxe ({luxe_price} 🎫)")
        if not coins_enabled and not luxe_enabled:
            lines.append("🔒 No purchase methods are enabled right now.")
    lines.append("Check balance: !discs")
    await _w(bot, user.id, "\n".join(lines))


async def handle_discs(bot, user, args=None) -> None:
    discs.ensure_schema()
    balance = discs.get_disc_balance(user.id)
    await _w(bot, user.id, f"💽 You have {balance} {_display_name()}.")


async def handle_buydisc(bot, user, args=None) -> None:
    rs.ensure_radio_settings()
    discs.ensure_schema()
    args = _strip_command_arg(args, "buydisc")
    method = (args[0].lower() if args else "").strip()
    if method not in {"coins", "coin", "luxe", "tickets", "ticket"}:
        await _w(bot, user.id, "Use: !buydisc coins OR !buydisc luxe")
        return
    if not rs.get_bool_setting("music_shop_enabled", True):
        await _w(bot, user.id, "🔒 Music shop purchases are currently disabled.")
        return

    per_command = rs.get_int_setting("music_disc_max_purchase_per_command", 10)
    daily_limit = rs.get_int_setting("music_disc_daily_purchase_limit", 50)
    if len(args) > 1:
        try:
            qty = int(str(args[1]).strip())
        except (TypeError, ValueError):
            await _w(bot, user.id, "⚠️ Amount must be a whole number.")
            return
    else:
        qty = 1
    if qty <= 0:
        await _w(bot, user.id, "⚠️ Amount must be greater than 0.")
        return
    if qty > per_command:
        await _w(bot, user.id, f"⚠️ You can buy at most {per_command} Music Discs per command.")
        return
    purchased_today = discs.get_daily_purchased(user.id)
    if daily_limit > 0 and purchased_today + qty > daily_limit:
        await _w(bot, user.id, f"⚠️ Daily purchase limit reached ({daily_limit} Music Discs).")
        return

    if method in {"coins", "coin"}:
        if not rs.get_bool_setting("music_disc_purchase_coins_enabled", True):
            await _w(bot, user.id, "🔒 Coin purchases are currently disabled.")
            return
        unit_price = rs.get_int_setting("music_disc_price_coins", 500)
        price = unit_price * qty
        balance = db.get_balance(user.id)
        if balance < price:
            await _w(bot, user.id, f"⚠️ You need {price} coins for {qty} {_display_name()}.")
            return
        db.adjust_balance(user.id, -price)
        new_balance = discs.add_discs(user.id, user.username, qty, discs.BUY_REASON, actor=user.username)
        await _w(bot, user.id, f"✅ Bought {qty} {_display_name()} for {price} coins.\nBalance: {new_balance} {_display_name()}")
        return

    if not rs.get_bool_setting("music_disc_purchase_luxe_enabled", True):
        await _w(bot, user.id, "🔒 Luxe purchases are currently disabled.")
        return
    unit_price = rs.get_int_setting("music_disc_price_luxe", 50)
    price = unit_price * qty
    if get_luxe_balance(user.id) < price:
        await _w(bot, user.id, f"⚠️ You need {price} Luxe Tickets for {qty} {_display_name()}.")
        return
    if not deduct_luxe_balance(user.id, user.username, price):
        await _w(bot, user.id, "⚠️ Luxe ticket deduction failed. Try again.")
        return
    try:
        log_luxe_transaction(user.id, user.username, "music_disc_purchase", price, "luxe", discs.BUY_REASON)
    except Exception:
        pass
    new_balance = discs.add_discs(user.id, user.username, qty, discs.BUY_REASON, actor=user.username)
    await _w(bot, user.id, f"✅ Bought {qty} {_display_name()} for {price} Luxe Tickets.\nBalance: {new_balance} {_display_name()}")


async def handle_discprice(bot, user, args=None) -> None:
    if not _staff(user):
        await _w(bot, user.id, "This command is staff-only.")
        return
    await _w(
        bot,
        user.id,
        "\n".join(
            [
                "💽 Music Disc Prices",
                f"Coins: {rs.get_int_setting('music_disc_price_coins', 500)}",
                f"Luxe: {rs.get_int_setting('music_disc_price_luxe', 50)}",
                "Set: !setdiscprice coins <amount>",
                "Set: !setdiscprice luxe <amount>",
            ]
        ),
    )


async def handle_setdiscprice(bot, user, args=None) -> None:
    if not _staff(user):
        await _w(bot, user.id, "This command is staff-only.")
        return
    args = _strip_command_arg(args, "setdiscprice")
    if len(args) < 2 or args[0].lower() not in {"coins", "coin", "luxe", "tickets"}:
        await _w(bot, user.id, "Use: !setdiscprice coins <amount> OR !setdiscprice luxe <amount>")
        return
    amount = _positive_int(args[1], -1)
    if amount < 0:
        await _w(bot, user.id, "Amount must be a whole number.")
        return
    key = "music_disc_price_coins" if args[0].lower() in {"coins", "coin"} else "music_disc_price_luxe"
    rs.set_setting(key, amount, "int", updated_by=user.username)
    await _w(bot, user.id, f"✅ Updated {key} to {amount}.")


async def handle_requestdiscprice(bot, user, args=None) -> None:
    if not _staff(user):
        await _w(bot, user.id, "This command is staff-only.")
        return
    await _w(
        bot,
        user.id,
        "\n".join(
            [
                "🎵 Request Disc Costs",
                f"Normal: {rs.get_int_setting('request_disc_cost_normal', 1)}",
                f"VIP: {rs.get_int_setting('request_disc_cost_vip', 1)}",
                f"Staff: {rs.get_int_setting('request_disc_cost_staff', 0)}",
                f"Owner: {rs.get_int_setting('request_disc_cost_owner', 0)}",
                "Set: !setrequestdisc normal <amount>",
            ]
        ),
    )


async def handle_setrequestdisc(bot, user, args=None) -> None:
    args = _strip_command_arg(args, "setrequestdisc")
    if not _staff(user):
        await _w(bot, user.id, "This command is staff-only.")
        return
    if len(args) < 2 or args[0].lower() not in {"normal", "vip", "staff", "owner"}:
        await _w(bot, user.id, "Use: !setrequestdisc normal|vip|staff|owner <amount>")
        return
    tier = args[0].lower()
    if tier == "owner" and not _owner(user):
        await _w(bot, user.id, "Only the owner can set owner request cost.")
        return
    amount = _positive_int(args[1], -1)
    if amount < 0:
        await _w(bot, user.id, "Amount must be a whole number.")
        return
    key = f"request_disc_cost_{tier}"
    rs.set_setting(key, amount, "int", updated_by=user.username)
    await _w(bot, user.id, f"✅ Updated {tier} request cost to {amount} {_display_name()}.")


async def dispatch_music_disc_command(bot, user, cmd: str, args: list[str]) -> None:
    if cmd == "musicshop":
        await handle_musicshop(bot, user, args)
    elif cmd == "buydisc":
        await handle_buydisc(bot, user, args)
    elif cmd == "discs":
        await handle_discs(bot, user, args)
    elif cmd == "discprice":
        await handle_discprice(bot, user, args)
    elif cmd == "setdiscprice":
        await handle_setdiscprice(bot, user, args)
    elif cmd == "requestdiscprice":
        await handle_requestdiscprice(bot, user, args)
    elif cmd == "setrequestdisc":
        await handle_setrequestdisc(bot, user, args)
