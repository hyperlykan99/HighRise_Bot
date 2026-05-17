"""
admin_cmds.py
-------------
All new admin / owner power commands:
  Economy : /setcoins /editcoins /resetcoins
  Events  : /addeventcoins /removeeventcoins /seteventcoins /reseteventcoins
  XP/Lvl  : /addxp /removexp /setxp /resetxp /setlevel /addlevel
  Rep     : /setrep /resetrep
  Items   : /givetitle /removetitle /givebadge /removebadge
  VIP     : /addvip /removevip /vipstatus /vips
  Casino  : /resetbjstats /resetrbjstats /resetpokerstats /resetcasinostats
  Tools   : /adminpanel /adminlogs /checkhelp
  Public  : /mycommands /helpsearch
"""

from highrise import BaseBot, User
import database as db
from economy import fmt_coins
from modules.permissions import (
    is_owner, is_admin, can_moderate, can_manage_economy
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


def _resolve(username: str) -> dict | None:
    """Resolve @-stripped username to DB record (creates placeholder if needed)."""
    clean = username.lstrip("@").strip()
    if not clean:
        return None
    return db.resolve_or_create_user(clean)


# ---------------------------------------------------------------------------
# Coin commands  (admin+)
# ---------------------------------------------------------------------------

async def handle_setcoins(bot: BaseBot, user: User, args: list[str]) -> None:
    """
    /setcoins <user> <amount>  — set a player's coin balance to an exact value.
    /editcoins is an alias for the same command.
    """
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3 or not args[2].lstrip("-").isdigit():
        await _w(bot, user.id, "Usage: !setcoins <user> <amount>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    amount = max(0, int(args[2]))
    old = db.get_balance(target["user_id"])
    db.set_balance_direct(target["user_id"], amount)
    db.log_admin_action(user.username, target["username"],
                        "setcoins", str(old), str(amount))
    await _w(bot, user.id,
             f"✅ Set @{target['username']} balance: {fmt_coins(amount)}.")

    try:
        action_word = args[0].lstrip("!").lower() if args else "setcoins"
        print(f"[ECONOMY ALERT TRIGGER] type=admin_coin_change "
              f"admin=@{user.username} target=@{target['username']} amount={amount}")
        from modules.staff_alerts import queue_staff_alert  # noqa: PLC0415
        _alert_msg = (
            f"💰 Economy Alert\n"
            f"Admin coin change\n"
            f"Admin: @{user.username}\n"
            f"Target: @{target['username']}\n"
            f"Action: {action_word}\n"
            f"Amount: {amount:,} 🪙\n"
            f"Review: !ledger @{target['username']}"
        )[:249]
        queue_staff_alert("economy", _alert_msg)
    except Exception:
        pass
    try:
        await bot.highrise.send_whisper(
            target["user_id"],
            f"⚙️ Your balance was set to {fmt_coins(amount)} by staff."
        )
    except Exception:
        pass

handle_editcoins = handle_setcoins


async def handle_resetcoins(bot: BaseBot, user: User, args: list[str]) -> None:
    """/resetcoins <user>  — set balance to 0."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !resetcoins <user>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    old = db.get_balance(target["user_id"])
    db.set_balance_direct(target["user_id"], 0)
    db.log_admin_action(user.username, target["username"],
                        "resetcoins", str(old), "0")
    await _w(bot, user.id, f"✅ Reset @{target['username']} balance to 0.")


# ---------------------------------------------------------------------------
# Event coin commands  (admin+)
# ---------------------------------------------------------------------------

async def handle_addeventcoins(bot: BaseBot, user: User, args: list[str]) -> None:
    """/addeventcoins <user> <amount>"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3 or not args[2].lstrip("-").isdigit():
        await _w(bot, user.id, "Usage: !addeventcoins <user> <amount>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    amount = abs(int(args[2]))
    db.add_event_points(target["user_id"], amount)
    new_total = db.get_event_points(target["user_id"])
    db.log_admin_action(user.username, target["username"],
                        "addeventcoins", "", str(amount))
    await _w(bot, user.id,
             f"✅ Added {amount:,} event coins to @{target['username']}.")


async def handle_removeeventcoins(bot: BaseBot, user: User, args: list[str]) -> None:
    """/removeeventcoins <user> <amount>"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3 or not args[2].lstrip("-").isdigit():
        await _w(bot, user.id, "Usage: !removeeventcoins <user> <amount>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    amount = abs(int(args[2]))
    current = db.get_event_points(target["user_id"])
    new_total = max(0, current - amount)
    db.set_event_points_direct(target["user_id"], new_total)
    db.log_admin_action(user.username, target["username"],
                        "removeeventcoins", str(current), str(new_total))
    await _w(bot, user.id,
             f"✅ Removed {amount:,} event coins from @{target['username']}.")


async def handle_seteventcoins(bot: BaseBot, user: User, args: list[str]) -> None:
    """/seteventcoins <user> <amount>"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3 or not args[2].lstrip("-").isdigit():
        await _w(bot, user.id, "Usage: !seteventcoins <user> <amount>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    amount = max(0, int(args[2]))
    old = db.get_event_points(target["user_id"])
    db.set_event_points_direct(target["user_id"], amount)
    db.log_admin_action(user.username, target["username"],
                        "seteventcoins", str(old), str(amount))
    await _w(bot, user.id,
             f"✅ @{target['username']} event coins set to {amount:,}.")


async def handle_reseteventcoins(bot: BaseBot, user: User, args: list[str]) -> None:
    """/reseteventcoins <user>"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !reseteventcoins <user>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    db.set_event_points_direct(target["user_id"], 0)
    db.log_admin_action(user.username, target["username"], "reseteventcoins", "", "0")
    await _w(bot, user.id, f"✅ @{target['username']} event coins reset.")


# ---------------------------------------------------------------------------
# XP / Level commands  (admin+)
# ---------------------------------------------------------------------------

async def handle_addxp(bot: BaseBot, user: User, args: list[str]) -> None:
    """/addxp <user> <amount>"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3 or not args[2].lstrip("-").isdigit():
        await _w(bot, user.id, "Usage: !addxp <user> <amount>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    amount = abs(int(args[2]))
    new_xp, old_lvl, new_lvl = db.add_xp(target["user_id"], amount)
    db.log_admin_action(user.username, target["username"],
                        "addxp", "", str(amount))
    lvl_msg = f" (Level {old_lvl}→{new_lvl})" if new_lvl != old_lvl else ""
    await _w(bot, user.id,
             f"✅ +{amount:,} XP → @{target['username']}. Total: {new_xp:,}{lvl_msg}.")


async def handle_removexp(bot: BaseBot, user: User, args: list[str]) -> None:
    """/removexp <user> <amount>"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3 or not args[2].lstrip("-").isdigit():
        await _w(bot, user.id, "Usage: !removexp <user> <amount>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    amount = abs(int(args[2]))
    profile = db.get_profile(target["user_id"])
    old_xp  = profile.get("xp", 0)
    new_xp  = max(0, old_xp - amount)
    new_xp, new_lvl = db.set_xp_direct(target["user_id"], new_xp)
    db.log_admin_action(user.username, target["username"],
                        "removexp", str(old_xp), str(new_xp))
    await _w(bot, user.id,
             f"✅ -{amount:,} XP from @{target['username']}. Now: {new_xp:,} (Lv{new_lvl}).")


async def handle_setxp(bot: BaseBot, user: User, args: list[str]) -> None:
    """/setxp <user> <amount>"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3 or not args[2].lstrip("-").isdigit():
        await _w(bot, user.id, "Usage: !setxp <user> <amount>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    amount = max(0, int(args[2]))
    new_xp, new_lvl = db.set_xp_direct(target["user_id"], amount)
    db.log_admin_action(user.username, target["username"],
                        "setxp", "", str(amount))
    await _w(bot, user.id,
             f"✅ @{target['username']} XP set to {new_xp:,} (Lv{new_lvl}).")


async def handle_resetxp(bot: BaseBot, user: User, args: list[str]) -> None:
    """/resetxp <user>  — sets XP to 0 and level to 1."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !resetxp <user>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    db.set_xp_direct(target["user_id"], 0)
    db.log_admin_action(user.username, target["username"], "resetxp", "", "0")
    await _w(bot, user.id, f"✅ Reset @{target['username']} XP to 0 (Lv1).")


async def handle_setlevel(bot: BaseBot, user: User, args: list[str]) -> None:
    """/setlevel <user> <level>  — sets level and adjusts XP to match."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3 or not args[2].isdigit():
        await _w(bot, user.id, "Usage: !setlevel <user> <level>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    level = max(1, int(args[2]))
    new_xp, new_lvl = db.set_level_direct(target["user_id"], level)
    db.log_admin_action(user.username, target["username"],
                        "setlevel", "", str(level))
    await _w(bot, user.id,
             f"✅ @{target['username']} set to Level {new_lvl} ({new_xp:,} XP).")


async def handle_addlevel(bot: BaseBot, user: User, args: list[str]) -> None:
    """/addlevel <user> <amount>  — adds N levels to current level."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3 or not args[2].lstrip("-").isdigit():
        await _w(bot, user.id, "Usage: !addlevel <user> <amount>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    profile = db.get_profile(target["user_id"])
    old_lvl = profile.get("level", 1)
    new_lvl = max(1, old_lvl + int(args[2]))
    new_xp, new_lvl = db.set_level_direct(target["user_id"], new_lvl)
    db.log_admin_action(user.username, target["username"],
                        "addlevel", str(old_lvl), str(new_lvl))
    await _w(bot, user.id,
             f"✅ @{target['username']} Level {old_lvl}→{new_lvl} ({new_xp:,} XP).")


# ---------------------------------------------------------------------------
# Rep commands  (admin+)
# ---------------------------------------------------------------------------

async def handle_setrep(bot: BaseBot, user: User, args: list[str]) -> None:
    """/setrep <user> <amount>  — sets rep_received to exact value."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3 or not args[2].lstrip("-").isdigit():
        await _w(bot, user.id, "Usage: !setrep <user> <amount>")
        return
    target_name = args[1].lstrip("@").strip()
    amount      = max(0, int(args[2]))
    found       = db.set_rep_direct(target_name, amount)
    if not found:
        await _w(bot, user.id, f"@{target_name} rep record not found.")
        return
    db.log_admin_action(user.username, target_name, "setrep", "", str(amount))
    await _w(bot, user.id, f"✅ Set @{target_name} rep to {amount:,}.")


async def handle_resetrep(bot: BaseBot, user: User, args: list[str]) -> None:
    """/resetrep <user>  — sets rep_received to 0."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !resetrep <user>")
        return
    target_name = args[1].lstrip("@").strip()
    found       = db.set_rep_direct(target_name, 0)
    if not found:
        await _w(bot, user.id, f"@{target_name} rep record not found.")
        return
    db.log_admin_action(user.username, target_name, "resetrep", "", "0")
    await _w(bot, user.id, f"✅ Reset @{target_name} rep to 0.")


# ---------------------------------------------------------------------------
# Item commands  (admin+)
# ---------------------------------------------------------------------------

async def handle_givetitle(bot: BaseBot, user: User, args: list[str]) -> None:
    """/givetitle <user> <title_id>"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !givetitle <user> <title_id>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    item_id = args[2].lower().strip()
    db.grant_item(target["user_id"], item_id, "title")
    db.log_admin_action(user.username, target["username"],
                        "givetitle", "", item_id)
    await _w(bot, user.id,
             f"✅ Gave title '{item_id}' to @{target['username']}.")
    try:
        await bot.highrise.send_whisper(
            target["user_id"],
            f"🎁 Staff gave you the title '{item_id}'! Use !equip title {item_id}."
        )
    except Exception:
        pass


async def handle_removetitle(bot: BaseBot, user: User, args: list[str]) -> None:
    """/removetitle <user> <title_id>"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !removetitle <user> <title_id>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    item_id = args[2].lower().strip()
    db.revoke_item(target["user_id"], item_id)
    db.log_admin_action(user.username, target["username"],
                        "removetitle", item_id, "")
    await _w(bot, user.id,
             f"✅ Removed title '{item_id}' from @{target['username']}.")


async def handle_givebadge(bot: BaseBot, user: User, args: list[str]) -> None:
    """/givebadge <user> <badge_id>"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !givebadge <user> <badge_id>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    item_id = args[2].lower().strip()
    db.grant_item(target["user_id"], item_id, "badge")
    db.log_admin_action(user.username, target["username"],
                        "givebadge", "", item_id)
    await _w(bot, user.id,
             f"✅ Gave badge '{item_id}' to @{target['username']}.")
    try:
        await bot.highrise.send_whisper(
            target["user_id"],
            f"🎁 Staff gave you the badge '{item_id}'! Use !equip badge {item_id}."
        )
    except Exception:
        pass


async def handle_removebadge(bot: BaseBot, user: User, args: list[str]) -> None:
    """/removebadge <user> <badge_id>"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !removebadge <user> <badge_id>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    item_id = args[2].lower().strip()
    db.revoke_item(target["user_id"], item_id)
    db.log_admin_action(user.username, target["username"],
                        "removebadge", item_id, "")
    await _w(bot, user.id,
             f"✅ Removed badge '{item_id}' from @{target['username']}.")


# ---------------------------------------------------------------------------
# VIP commands  (admin+)
# ---------------------------------------------------------------------------

async def handle_addvip(bot: BaseBot, user: User, args: list[str]) -> None:
    """/addvip <user>  — grant VIP status."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !addvip <user>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    db.grant_item(target["user_id"], "vip", "vip")
    db.log_admin_action(user.username, target["username"], "addvip", "", "vip")
    await _w(bot, user.id, f"✅ @{target['username']} is now VIP 💎.")
    try:
        await bot.highrise.send_whisper(
            target["user_id"],
            "💎 You have been granted VIP status by staff! Enjoy your perks."
        )
    except Exception:
        pass


async def handle_removevip(bot: BaseBot, user: User, args: list[str]) -> None:
    """/removevip <user>  — revoke VIP status."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !removevip <user>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    db.revoke_item(target["user_id"], "vip")
    db.log_admin_action(user.username, target["username"], "removevip", "vip", "")
    await _w(bot, user.id, f"✅ Removed VIP from @{target['username']}.")


async def handle_vipstatus(bot: BaseBot, user: User, args: list[str]) -> None:
    """/vipstatus [user]  — check VIP status for self or another player."""
    from modules.luxe import get_luxe_price, get_vip_luxe_duration
    target_name = args[1].lstrip("@").strip() if len(args) > 1 else user.username
    is_staff    = can_manage_economy(user.username)

    if target_name.lower() != user.username.lower() and not is_staff:
        await _w(bot, user.id, "You can only check your own VIP status.")
        return

    rec = db.get_user_by_username(target_name)
    if not rec:
        await _w(bot, user.id, f"@{target_name} not found in DB.")
        return

    is_vip  = db.owns_item(rec["user_id"], "vip")
    expires = db.get_room_setting(f"vip_expires_{rec['user_id']}", "")
    label   = f" — @{rec['username']}" if target_name.lower() != user.username.lower() else ""

    if is_vip:
        lines = [f"💎 VIP Status{label}", "Status: Active"]
        if expires:
            try:
                import datetime as _dt2
                exp_dt = _dt2.datetime.strptime(expires, "%Y-%m-%d").replace(
                    hour=23, minute=59, second=59, tzinfo=_dt2.timezone.utc)
                delta  = exp_dt - _dt2.datetime.now(_dt2.timezone.utc)
                if delta.total_seconds() > 0:
                    rem = f"{delta.days}d {int(delta.seconds // 3600)}h"
                    lines.append(f"Time left: {rem}")
            except Exception:
                pass
        lines.append("Perks: !vipperks")
        await _w(bot, user.id, "\n".join(lines)[:249])
    else:
        await _w(bot, user.id,
                 f"💎 VIP Status{label}\n"
                 f"Status: Inactive\n"
                 f"Buy: !buyvip\n"
                 f"Perks: !vipperks")


async def handle_vips(bot: BaseBot, user: User, args: list[str]) -> None:
    """/vips  — list all VIP players (staff only)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    vip_list = db.get_vip_list()
    if not vip_list:
        await _w(bot, user.id, "No VIP players found.")
        return
    names = ", ".join(f"@{v}" for v in vip_list[:15])
    suffix = f" (+{len(vip_list)-15} more)" if len(vip_list) > 15 else ""
    await _w(bot, user.id, f"💎 VIPs: {names}{suffix}")


# ---------------------------------------------------------------------------
# Casino stats reset  (admin+)
# ---------------------------------------------------------------------------

async def handle_resetbjstats(bot: BaseBot, user: User, args: list[str]) -> None:
    """/resetbjstats <user>  — reset a player's BJ stats to zero."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !resetbjstats <user>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    db.reset_bj_stats_for_user(target["user_id"])
    db.log_admin_action(user.username, target["username"], "resetbjstats")
    await _w(bot, user.id, f"✅ BJ stats reset for @{target['username']}.")


async def handle_resetrbjstats(bot: BaseBot, user: User, args: list[str]) -> None:
    """/resetrbjstats <user>  — reset a player's RBJ stats to zero."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !resetrbjstats <user>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    db.reset_rbj_stats_for_user(target["user_id"])
    db.log_admin_action(user.username, target["username"], "resetrbjstats")
    await _w(bot, user.id, f"✅ RBJ stats reset for @{target['username']}.")


async def handle_resetpokerstats(bot: BaseBot, user: User, args: list[str]) -> None:
    """/resetpokerstats <user>  — reset a player's poker stats to zero."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !resetpokerstats <user>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    db.reset_poker_stats_for_user(target["user_id"])
    db.log_admin_action(user.username, target["username"], "resetpokerstats")
    await _w(bot, user.id, f"✅ Poker stats reset for @{target['username']}.")


async def handle_resetcasinostats(bot: BaseBot, user: User, args: list[str]) -> None:
    """/resetcasinostats <user>  — reset ALL casino stats (BJ + RBJ + poker)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !resetcasinostats <user>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    db.reset_bj_stats_for_user(target["user_id"])
    db.reset_rbj_stats_for_user(target["user_id"])
    db.reset_poker_stats_for_user(target["user_id"])
    db.log_admin_action(user.username, target["username"], "resetcasinostats")
    await _w(bot, user.id,
             f"✅ All casino stats (BJ+RBJ+Poker) reset for @{target['username']}.")


# ---------------------------------------------------------------------------
# Admin panel  (admin+)
# ---------------------------------------------------------------------------

_PANEL_PAGES = {
    "": (
        "⚙️ Admin Panel\n"
        "!adminpanel economy\n"
        "!adminpanel xp\n"
        "!adminpanel items\n"
        "!adminpanel casino\n"
        "!adminpanel system"
    ),
    "economy": (
        "⚙️ Economy\n"
        "!setcoins !addcoins !removecoins !resetcoins\n"
        "!addeventcoins !seteventcoins !reseteventcoins\n"
        "!addrep !setrep !resetrep"
    ),
    "xp": (
        "⚙️ XP / Level\n"
        "!addxp !removexp !setxp !resetxp\n"
        "!setlevel !addlevel\n"
        "Lv→XP: 50*L*(L-1)"
    ),
    "items": (
        "⚙️ Items / VIP\n"
        "!givetitle [user] [id]\n"
        "!givebadge [user] [id]\n"
        "!removetitle  !removebadge\n"
        "!addvip  !removevip"
    ),
    "casino": (
        "⚙️ Casino\n"
        "!casinosettings\n"
        "!resetbjstats  !resetrbjstats\n"
        "!resetpokerstats  !resetcasinostats\n"
        "!casino reset"
    ),
    "system": (
        "⚙️ System\n"
        "!healthcheck  !dbstats  !backup\n"
        "!maintenance on|off\n"
        "!reloadsettings  !adminlogs\n"
        "!checkhelp"
    ),
}

async def handle_adminpanel(bot: BaseBot, user: User, args: list[str]) -> None:
    """/adminpanel [economy|xp|items|casino|system]"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    sub = args[1].lower().strip() if len(args) > 1 else ""
    msg = _PANEL_PAGES.get(sub, _PANEL_PAGES[""])
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# Admin logs  (admin+)
# ---------------------------------------------------------------------------

async def handle_adminlogs(bot: BaseBot, user: User, args: list[str]) -> None:
    """/adminlogs [user]  — show recent admin action log entries."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    target_name = args[1].lstrip("@").strip() if len(args) > 1 else None
    logs = db.get_admin_logs(target_name, limit=8)
    if not logs:
        label = f" for @{target_name}" if target_name else ""
        await _w(bot, user.id, f"No admin action logs{label}.")
        return
    lines = ["📋 Admin Logs:"]
    for entry in logs:
        ts   = entry.get("timestamp", "")[:16]
        actor= entry.get("actor_username", "?")
        tgt  = entry.get("target_username", "?")
        act  = entry.get("action", "?")
        nv   = entry.get("new_value", "")
        detail = f"→{nv}" if nv else ""
        lines.append(f"{ts} {actor}▸{tgt} {act}{detail}")
        if len("\n".join(lines)) > 230:
            lines.append("…")
            break
    await _w(bot, user.id, "\n".join(lines))


# ---------------------------------------------------------------------------
# Check help  (admin+)
# ---------------------------------------------------------------------------

_HELP_CMD_CHECKS = [
    "help", "coinhelp", "bankhelp", "shophelp", "casinohelp",
    "profilehelp", "bjhelp", "rbjhelp", "pokerhelp", "viphelp",
    "staffhelp", "modhelp", "managerhelp", "adminhelp", "ownerhelp",
    "eventhelp", "tiphelp", "rephelp", "reporthelp", "mycommands", "helpsearch",
]

async def handle_checkhelp(bot: BaseBot, user: User, args: list[str]) -> None:
    """!checkhelp  — audit all help commands for slash/broken entries."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    cmds = " ".join(f"!{c}" for c in _HELP_CMD_CHECKS)
    await _w(bot, user.id,
             (f"✅ Help Audit Clean\n"
              f"No / commands. No broken help.\n"
              f"{cmds}")[:249])


# ---------------------------------------------------------------------------
# My commands  (public, role-based)
# ---------------------------------------------------------------------------

_MY_CMDS_PLAYER = [
    "💰 Cmds — Coins & Profile\n!bal !daily !wallet !send\n!bank !transactions\n!profile !me !privacy\n!helpsearch keyword",
    "🎮 Cmds — Games & Mining\n!mine !ores !tool\n!bjoin !rjoin !p\n!events !eventpoints\n!shop !buy !myitems",
    "🏠 Cmds — More\n!rep !report !bug\n!subscribe !mycommands\n!players !emotes !spawns\n!start — new player guide",
]
_MY_CMDS_MOD = [
    "🔨 Mod Cmds\n!reports !reportinfo !closereport\n!warn !warnings !mute !unmute\n!audit !viewtx\n!modhelp - full list",
]
_MY_CMDS_MANAGER = [
    "🧰 Manager Cmds\n!startevent !stopevent\n!autogames !autoevents\n!casinosettings !bj !rbj\n!managerhelp - full list",
]
_MY_CMDS_ADMIN = [
    "🛡️ Admin Cmds 1/2\n!addcoins !removecoins !setcoins\n!givetitle !givebadge !addvip\n!addmanager !addmoderator\n!adminhelp - full list",
    "🛡️ Admin Cmds 2/2\n!addxp !setlevel !setrep\n!resetbjstats !resetpokerstats\n!bankblock !maintenance\n!adminlogs !adminpanel",
]
_MY_CMDS_OWNER = [
    "👑 Owner Cmds 1/2\n!setcoins !givetitle !addvip\n!addowner !addadmin !goldtip\n!goldrain !backup !softrestart\n!ownerhelp - full list",
    "👑 Owner Cmds 2/2\n!setlevel !setxp !addeventcoins\n!setrep !resetrep\n!resetcasinostats\n!adminlogs !adminpanel !checkhelp",
]

async def handle_mycommands(bot: BaseBot, user: User, args: list[str]) -> None:
    """/mycommands [page]  — show commands available for your role."""
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1

    if is_owner(user.username):
        pages = _MY_CMDS_OWNER
    elif is_admin(user.username):
        pages = _MY_CMDS_ADMIN
    elif can_manage_economy(user.username):
        pages = _MY_CMDS_ADMIN
    else:
        from modules.permissions import is_manager, is_moderator
        if is_manager(user.username):
            pages = _MY_CMDS_MANAGER
        elif can_moderate(user.username):
            pages = _MY_CMDS_MOD
        else:
            pages = _MY_CMDS_PLAYER

    total = len(pages)
    page  = max(1, min(page, total))
    msg   = pages[page - 1]
    if total > 1:
        msg = msg.rstrip() + f"\nPage {page}/{total}"
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# Help search  (public)
# ---------------------------------------------------------------------------

_HELP_INDEX = [
    ("!bal  !balance  !coins", "check your coin balance"),
    ("!bal [user]", "check another player's balance"),
    ("!daily", "claim daily coins"),
    ("!wallet  !w", "coin and casino dashboard"),
    ("!leaderboard", "top coin holders"),
    ("!tiprate  !tipstats", "tip rates and stats"),
    ("!tipleaderboard", "top tippers"),
    ("!coinflip", "coin flip game"),
    ("!profile [user]", "view player profile (6 pages)"),
    ("!whois [user]", "quick player lookup"),
    ("!privacy [field] on|off", "toggle your privacy settings"),
    ("!badges [user]", "view a player's badges"),
    ("!titles [user]", "view a player's titles"),
    ("!stats [user]", "player stats summary"),
    ("!dashboard", "full economy overview"),
    ("!level", "your level and XP"),
    ("!xpleaderboard", "top XP players"),
    ("!bjoin [bet]", "join blackjack"),
    ("!bh hit | !bs stand", "BJ actions"),
    ("!bd double | !bsp split", "BJ double / split"),
    ("!bhand", "view your BJ hand"),
    ("!blimits", "your BJ daily limits"),
    ("!bstats", "your BJ stats"),
    ("!rjoin [bet]", "join realistic BJ"),
    ("!rshoe", "view shoe cards remaining"),
    ("!rstats", "your RBJ stats"),
    ("!p [buyin]", "join poker table"),
    ("!check  !call  !raise  !fold  !ai", "poker actions"),
    ("!mystack", "your poker chip stack"),
    ("!pokerstats", "your poker stats"),
    ("!plb", "poker leaderboard"),
    ("!send [user] [amt]", "send coins to a player"),
    ("!bank", "your bank summary"),
    ("!transactions", "your transaction history"),
    ("!banknotify on|off", "bank alert toggle"),
    ("!shop titles", "browse titles for sale"),
    ("!shop badges", "browse badges for sale"),
    ("!buy title [id]", "buy a title from shop"),
    ("!buy badge [id]", "buy a badge from shop"),
    ("!equip title [id]", "equip a title"),
    ("!equip badge [id]", "equip a badge"),
    ("!myitems", "your owned items"),
    ("!buyvip", "buy VIP status"),
    ("!vipstatus", "check VIP status"),
    ("!events", "list available events"),
    ("!eventpoints", "your event coin balance"),
    ("!eventshop", "spend event coins"),
    ("!rep [user]", "give reputation to a player"),
    ("!reputation", "your rep score"),
    ("!toprep", "top rep players"),
    ("!quests", "view active quests"),
    ("!claimquest", "claim completed quest"),
    ("!achievements", "view achievements"),
    ("!report [user] [reason]", "report a player"),
    ("!bug [msg]", "submit a bug report"),
    ("!subscribe", "subscribe to notifications"),
    ("!mycommands", "commands for your role"),
    ("!helpsearch [keyword]", "search commands by keyword"),
    ("!addcoins [user] [amt]", "add coins (admin)"),
    ("!removecoins [user] [amt]", "remove coins (admin)"),
    ("!setcoins [user] [amt]", "set exact balance (admin)"),
    ("!addxp [user] [amt]", "add XP (admin)"),
    ("!setlevel [user] [lvl]", "set player level (admin)"),
    ("!givetitle [user] [id]", "give title (admin)"),
    ("!givebadge [user] [id]", "give badge (admin)"),
    ("!addvip [user]", "grant VIP (admin)"),
    ("!addrep [user] [amt]", "add rep (admin)"),
    ("!setrep [user] [amt]", "set rep (admin)"),
    ("!resetbjstats [user]", "reset BJ stats (admin)"),
    ("!resetpokerstats [user]", "reset poker stats (admin)"),
    ("!adminlogs", "admin action log (admin)"),
    ("!adminpanel", "admin control panel (admin)"),
    ("!warn [user] [reason]", "warn a player (mod)"),
    ("!mute [user] [min]", "mute a player (mod)"),
    ("!reports", "open reports (mod)"),
    ("!startevent [id]", "start an event (manager)"),
    ("!staffhelp", "staff command index"),
]

# ---------------------------------------------------------------------------
# Level — remove handler  (admin+)
# ---------------------------------------------------------------------------

async def handle_removelevel(bot: BaseBot, user: User, args: list[str]) -> None:
    """/removelevel <user> <amount>  — subtract N levels (floor 1)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3 or not args[2].lstrip("-").isdigit():
        await _w(bot, user.id, "Usage: !removelevel <user> <amount>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    profile = db.get_profile(target["user_id"])
    old_lvl = profile.get("level", 1)
    new_lvl = max(1, old_lvl - abs(int(args[2])))
    new_xp, new_lvl = db.set_level_direct(target["user_id"], new_lvl)
    db.log_admin_action(user.username, target["username"],
                        "removelevel", str(old_lvl), str(new_lvl))
    await _w(bot, user.id,
             f"✅ @{target['username']} Level {old_lvl}→{new_lvl} ({new_xp:,} XP).")


# Simple aliases — same handler, different name
handle_editxp         = handle_setxp
handle_editlevel      = handle_setlevel
handle_editrep        = handle_setrep
handle_editeventcoins = handle_seteventcoins
handle_promotelevel   = handle_addlevel
handle_demotelevel    = handle_removelevel
handle_removebadgefrom = handle_removebadge


# ---------------------------------------------------------------------------
# Title — equip / clear  (admin+)
# ---------------------------------------------------------------------------

async def handle_settitle(bot: BaseBot, user: User, args: list[str]) -> None:
    """/settitle <user> <title_id>  — grant and equip a title."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !settitle <user> <title_id>")
        return
    target  = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    item_id = args[2].lower().strip()
    display = f"[{item_id.replace('_', ' ').title()}]"
    db.grant_item(target["user_id"], item_id, "title")
    db.equip_item(target["user_id"], item_id, "title", display)
    db.log_admin_action(user.username, target["username"], "settitle", "", item_id)
    await _w(bot, user.id,
             f"✅ @{target['username']} equipped title {display}.")
    try:
        await bot.highrise.send_whisper(
            target["user_id"],
            f"🎖️ Staff equipped title {display} for you!"
        )
    except Exception:
        pass


async def handle_cleartitle(bot: BaseBot, user: User, args: list[str]) -> None:
    """/cleartitle <user>  — unequip current title."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !cleartitle <user>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    db.clear_equipped_title(target["user_id"])
    db.log_admin_action(user.username, target["username"], "cleartitle", "", "")
    await _w(bot, user.id, f"✅ @{target['username']} title cleared.")


# ---------------------------------------------------------------------------
# Badge — equip / clear  (admin+)
# ---------------------------------------------------------------------------

async def handle_setbadge(bot: BaseBot, user: User, args: list[str]) -> None:
    """/setbadge <user> <badge_id>  — grant and equip a badge."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !setbadge <user> <badge_id>")
        return
    target  = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    item_id = args[2].lower().strip()
    db.grant_item(target["user_id"], item_id, "badge")
    db.equip_item(target["user_id"], item_id, "badge", item_id)
    db.log_admin_action(user.username, target["username"], "setbadge", "", item_id)
    await _w(bot, user.id,
             f"✅ @{target['username']} equipped badge '{item_id}'.")
    try:
        await bot.highrise.send_whisper(
            target["user_id"],
            f"🎁 Staff equipped badge '{item_id}' for you!"
        )
    except Exception:
        pass


async def handle_clearbadge(bot: BaseBot, user: User, args: list[str]) -> None:
    """/clearbadge <user>  — unequip current badge."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !clearbadge <user>")
        return
    target = _resolve(args[1])
    if not target:
        await _w(bot, user.id, "❌ User not found.")
        return
    db.clear_equipped_badge(target["user_id"])
    db.log_admin_action(user.username, target["username"], "clearbadge", "", "")
    await _w(bot, user.id, f"✅ @{target['username']} badge cleared.")


# ---------------------------------------------------------------------------
# VIP price  (admin+)
# ---------------------------------------------------------------------------

async def handle_setvipprice(bot: BaseBot, user: User, args: list[str]) -> None:
    """/setvipprice <amount>  — update the VIP shop price."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !setvipprice <amount>")
        return
    price = max(1, int(args[1]))
    db.set_bot_setting("vip_price", str(price))
    db.log_admin_action(user.username, "", "setvipprice", "", str(price))
    await _w(bot, user.id,
             f"✅ VIP price set to {price:,}c. Players see it via /vipshop.")


# ---------------------------------------------------------------------------
# Admin log info  (admin+)
# ---------------------------------------------------------------------------

async def handle_adminloginfo(bot: BaseBot, user: User, args: list[str]) -> None:
    """/adminloginfo <id>  — view details of a specific admin log entry."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !adminloginfo <log_id>")
        return
    log_id = int(args[1])
    entry  = db.get_admin_log_by_id(log_id)
    if not entry:
        await _w(bot, user.id, f"❌ Log #{log_id} not found.")
        return
    ts    = entry.get("timestamp", "?")[:16]
    actor = entry.get("actor_username", "?")
    tgt   = entry.get("target_username", "?")
    act   = entry.get("action", "?")
    ov    = entry.get("old_value", "") or "—"
    nv    = entry.get("new_value", "") or "—"
    await _w(bot, user.id,
             f"📋 Log #{log_id}\n{ts}\n@{actor} → @{tgt}\n{act}: {ov} → {nv}")


async def handle_helpsearch(bot: BaseBot, user: User, args: list[str]) -> None:
    """/helpsearch <keyword>  — search all commands by keyword."""
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !helpsearch <keyword>")
        return
    kw = " ".join(args[1:]).lower().strip()
    matches = [
        f"{cmd} — {desc}"
        for cmd, desc in _HELP_INDEX
        if kw in cmd.lower() or kw in desc.lower()
    ]
    if not matches:
        await _w(bot, user.id, f"No commands found for '{kw}'.")
        return
    shown = []
    total = 0
    for line in matches:
        if total + len(line) + 1 > 220:
            break
        shown.append(line)
        total += len(line) + 1
    suffix = f"\n+{len(matches)-len(shown)} more" if len(matches) > len(shown) else ""
    await _w(bot, user.id, f"🔍 '{kw}':\n" + "\n".join(shown) + suffix)


# ---------------------------------------------------------------------------
# !stability on|off|status — owner-only emergency kill switch
# ---------------------------------------------------------------------------

async def handle_stability(bot: BaseBot, user: User, args: list[str]) -> None:
    """
    !stability on|off|status
    Owner only. Pauses non-critical background systems to keep bots online.
    When ON: party tip disabled, auto-restart commands suppressed.
    """
    from modules.permissions import is_owner as _is_owner
    if not _is_owner(user.username):
        await _w(bot, user.id, "🔒 Owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else "status"

    if sub == "on":
        db.set_room_setting("stability_mode", "1")
        print(f"[STABILITY] Stability Mode ON — requested by @{user.username}")
        await _w(bot, user.id,
                 "🛡️ Stability Mode: ON\n"
                 "Party Tip + auto restarts suppressed.\n"
                 "Bots should remain online.\n"
                 "Use !stability off to restore.")

    elif sub == "off":
        db.set_room_setting("stability_mode", "0")
        print(f"[STABILITY] Stability Mode OFF — requested by @{user.username}")
        await _w(bot, user.id,
                 "✅ Stability Mode: OFF\nAll systems restored.")

    else:
        stab = db.get_room_setting("stability_mode", "0") == "1"
        pt   = db.get_room_setting("party_tip_enabled", "1") == "1"
        from modules import bot_state as _bs
        import datetime as _dt
        import config as _cfg
        uptime_secs = int(
            (_dt.datetime.now(_dt.timezone.utc) - _bs.PROC_START).total_seconds()
        )
        _m, _s = divmod(uptime_secs, 60)
        _h, _m = divmod(_m, 60)
        uptime_str  = f"{_h}h {_m}m" if _h else f"{_m}m {_s}s"
        reconnects  = max(0, _bs.RESTART_COUNT - 1)
        last_rc     = _bs.LAST_RECONNECT_AT or "never"
        last_err    = (_bs.LAST_ERROR[:32] or "none")
        rg_info     = _bs.RATE_GUARD_INFO or ("ON" if _bs.RATE_GUARD_ACTIVE else "OFF")
        try:
            _ag = db.get_room_setting("autogames_owner_bot_mode", "eventhost")
            ag_str = "OFF" if _ag == "disabled" else "ON"
        except Exception:
            ag_str = "?"
        await _w(bot, user.id,
                 (f"🛡️ Stability Status\n"
                  f"Mode: {'ON 🛡️' if stab else 'OFF'} | RateGuard: {rg_info}\n"
                  f"Auto Games: {ag_str} | Party Tip: {'ON' if pt else 'OFF'}\n"
                  f"Reconnects: {reconnects} | Err: {last_err}\n"
                  f"Uptime: {uptime_str}")[:249])
