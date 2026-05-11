"""
modules/vip.py
--------------
VIP status, perks, gifting, and donation/sponsorship commands.
All player-facing commands.  Manager+ commands (grant/remove) are also here.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot
    from highrise.models import User

import database as db
from modules.permissions import (
    can_manage_economy, can_moderate, is_owner,
)

_w = lambda bot, uid, msg: bot.highrise.send_whisper(uid, msg[:249])


# ---------------------------------------------------------------------------
# VIP perks text (centralised so !vip / !vipperks / !perks all share it)
# ---------------------------------------------------------------------------

_VIP_PERKS = (
    "💎 VIP Perks:\n"
    "• Gold Rain eligibility\n"
    "• Priority event entry\n"
    "• Bonus daily coins (+50%)\n"
    "• Exclusive VIP badge\n"
    "• VIP spawn point\n"
    "• !giftvip — gift VIP to a friend\n"
    "!setvipprice to see price."
)

_SUPPORTER_PERKS = (
    "🌟 Supporter Perks:\n"
    "• Gold tip multiplier\n"
    "• Supporter badge\n"
    "• Priority DM notifications\n"
    "• Name in !topdonors list\n"
    "Gold tip BankingBot to support!"
)


# ---------------------------------------------------------------------------
# !vip  — VIP overview
# ---------------------------------------------------------------------------

async def handle_vip(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!vip  — show VIP overview and how to get VIP."""
    price = db.get_room_setting("vip_price", "5000")
    is_vip = db.owns_item(user.id, "vip")
    status = "💎 You ARE VIP!" if is_vip else f"Price: {price} coins. Contact staff."
    await _w(bot, user.id,
             f"💎 VIP Status: {status}\n"
             f"Perks: gold rain, bonus daily, priority events.\n"
             f"!vipperks  !myvip  !giftvip")


# ---------------------------------------------------------------------------
# !vipperks  — perks list
# ---------------------------------------------------------------------------

async def handle_vipperks(bot: "BaseBot", user: "User") -> None:
    """!vipperks  — show all VIP perks."""
    await _w(bot, user.id, _VIP_PERKS)


# ---------------------------------------------------------------------------
# !myvip  — personal VIP status
# ---------------------------------------------------------------------------

async def handle_myvip(bot: "BaseBot", user: "User") -> None:
    """!myvip  — check your own VIP status."""
    is_vip = db.owns_item(user.id, "vip")
    if is_vip:
        await _w(bot, user.id,
                 f"💎 @{user.username}: You are VIP!\n"
                 f"Enjoy gold rain, priority events, and bonus daily coins.")
    else:
        price = db.get_room_setting("vip_price", "5000")
        await _w(bot, user.id,
                 f"@{user.username}: Not VIP yet.\n"
                 f"Cost: {price} coins. Contact staff or !giftvip from a VIP friend.")


# ---------------------------------------------------------------------------
# !giftvip <user>  — gift VIP to someone (VIP or manager required)
# ---------------------------------------------------------------------------

async def handle_giftvip(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!giftvip <user>  — gift VIP status (VIP players or manager+)."""
    is_vip    = db.owns_item(user.id, "vip")
    is_staff  = can_manage_economy(user.username)
    if not is_vip and not is_staff:
        await _w(bot, user.id, "💎 Only VIP members or managers can gift VIP.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !giftvip <username>")
        return
    target_name = args[1].lstrip("@").strip()
    rec = db.get_user_by_username(target_name)
    if not rec:
        await _w(bot, user.id, f"❌ @{target_name} not found in DB.")
        return
    if db.owns_item(rec["user_id"], "vip"):
        await _w(bot, user.id, f"@{rec['username']} is already VIP 💎.")
        return
    db.grant_item(rec["user_id"], "vip", "vip")
    db.log_admin_action(user.username, rec["username"], "giftvip", "", "vip")
    await _w(bot, user.id, f"💎 Gifted VIP to @{rec['username']}!")
    try:
        await bot.highrise.send_whisper(
            rec["user_id"],
            f"💎 @{user.username} gifted you VIP status! Enjoy your perks!"[:249]
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# !viplist  — list VIP members (player-accessible, public)
# ---------------------------------------------------------------------------

async def handle_viplist(bot: "BaseBot", user: "User") -> None:
    """!viplist  — show current VIP members."""
    vip_list = db.get_vip_list()
    if not vip_list:
        await _w(bot, user.id, "💎 No VIP members currently.")
        return
    names   = ", ".join(f"@{v}" for v in vip_list[:12])
    suffix  = f" (+{len(vip_list)-12} more)" if len(vip_list) > 12 else ""
    await _w(bot, user.id, f"💎 VIPs ({len(vip_list)}): {names}{suffix}")


# ---------------------------------------------------------------------------
# !grantvip <user>  — manager alias of addvip
# ---------------------------------------------------------------------------

async def handle_grantvip(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!grantvip <user>  — grant VIP (manager+)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !grantvip <username>")
        return
    target_name = args[1].lstrip("@").strip()
    rec = db.get_user_by_username(target_name)
    if not rec:
        await _w(bot, user.id, f"❌ @{target_name} not found.")
        return
    db.grant_item(rec["user_id"], "vip", "vip")
    db.log_admin_action(user.username, rec["username"], "grantvip", "", "vip")
    await _w(bot, user.id, f"✅ @{rec['username']} is now VIP 💎.")
    try:
        await bot.highrise.send_whisper(
            rec["user_id"],
            "💎 You have been granted VIP by staff! Enjoy your perks."[:249]
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# !setvipprice <coins>  — set VIP price (manager+)
# ---------------------------------------------------------------------------

async def handle_setvipprice_vip(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!setvipprice <coins>  — set VIP coin price (manager+)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !setvipprice <coins>")
        return
    try:
        price = int(args[1])
        if price < 0:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "❌ Price must be a positive number.")
        return
    db.set_room_setting("vip_price", str(price))
    await _w(bot, user.id, f"✅ VIP price set to {price} coins.")


# ---------------------------------------------------------------------------
# !donate  — donation info
# ---------------------------------------------------------------------------

async def handle_donate(bot: "BaseBot", user: "User") -> None:
    """!donate  — show donation info."""
    goal      = db.get_room_setting("donation_goal", "0")
    collected = db.get_room_setting("donation_collected", "0")
    msg = (
        "💛 Support the room by gold-tipping BankingBot!\n"
        f"Goal: {goal} gold  Collected: {collected} gold\n"
        "Top donors get the Supporter badge + perks.\n"
        "!topdonors  !donationgoal  !supporter"
    )
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# !donationgoal  — show current donation goal
# ---------------------------------------------------------------------------

async def handle_donationgoal(bot: "BaseBot", user: "User") -> None:
    """!donationgoal  — show donation goal progress."""
    goal      = db.get_room_setting("donation_goal", "0")
    collected = db.get_room_setting("donation_collected", "0")
    label     = db.get_room_setting("donation_goal_label", "Room Upgrades")
    try:
        pct = min(100, round(int(collected) / max(1, int(goal)) * 100))
    except Exception:
        pct = 0
    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
    await _w(bot, user.id,
             f"💛 Goal: {label}\n"
             f"{bar} {pct}%\n"
             f"Collected: {collected}/{goal} gold\n"
             f"Gold-tip BankingBot to contribute!")


# ---------------------------------------------------------------------------
# !topdonors  — top gold donors
# ---------------------------------------------------------------------------

async def handle_topdonors(bot: "BaseBot", user: "User") -> None:
    """!topdonors  — show top gold donors."""
    try:
        rows = db.get_top_gold_donors(10)
    except Exception:
        rows = []
    if not rows:
        await _w(bot, user.id,
                 "💛 No donations yet. Gold-tip BankingBot to be first!")
        return
    lines = [f"💛 Top Donors:"]
    for i, row in enumerate(rows[:8], 1):
        name  = row.get("username", "?")
        total = row.get("total_gold", 0)
        lines.append(f"{i}. @{name} — {total}g")
    await _w(bot, user.id, "\n".join(lines))


# ---------------------------------------------------------------------------
# !sponsor  — sponsorship info
# ---------------------------------------------------------------------------

async def handle_sponsor(bot: "BaseBot", user: "User") -> None:
    """!sponsor  — show sponsorship options."""
    price_rain  = db.get_room_setting("sponsor_goldrain_price", "50")
    price_event = db.get_room_setting("sponsor_event_price", "100")
    await _w(bot, user.id,
             f"🌟 Sponsorship Options:\n"
             f"• Gold Rain: {price_rain}g — !sponsorgoldrain\n"
             f"• Event: {price_event}g — !sponsorevent\n"
             f"Gold-tip BankingBot the amount to sponsor!")


# ---------------------------------------------------------------------------
# !sponsorgoldrain  — info on sponsoring a gold rain
# ---------------------------------------------------------------------------

async def handle_sponsorgoldrain(bot: "BaseBot", user: "User") -> None:
    """!sponsorgoldrain  — how to sponsor a gold rain event."""
    price = db.get_room_setting("sponsor_goldrain_price", "50")
    await _w(bot, user.id,
             f"🌟 Sponsor a Gold Rain!\n"
             f"Cost: {price}g — gold-tip BankingBot {price}g\n"
             f"A gold rain will be launched in your name.\n"
             f"DM a manager or staff to confirm.")


# ---------------------------------------------------------------------------
# !sponsorevent  — info on sponsoring an event
# ---------------------------------------------------------------------------

async def handle_sponsorevent(bot: "BaseBot", user: "User") -> None:
    """!sponsorevent  — how to sponsor a room event."""
    price = db.get_room_setting("sponsor_event_price", "100")
    await _w(bot, user.id,
             f"🌟 Sponsor a Room Event!\n"
             f"Cost: {price}g — gold-tip BankingBot {price}g\n"
             f"Choose the event type with a manager.\n"
             f"!eventhelp for event types.")


# ---------------------------------------------------------------------------
# !supporter  — show your supporter status
# ---------------------------------------------------------------------------

async def handle_supporter(bot: "BaseBot", user: "User") -> None:
    """!supporter  — check your supporter (donor) status."""
    try:
        rows   = db.get_top_gold_donors(1000)
        names  = [r.get("username", "").lower() for r in rows]
        rank   = names.index(user.username.lower()) + 1 if user.username.lower() in names else None
        total  = next((r.get("total_gold", 0) for r in rows
                       if r.get("username", "").lower() == user.username.lower()), 0)
    except Exception:
        rank, total = None, 0
    if rank:
        await _w(bot, user.id,
                 f"💛 @{user.username}: Supporter rank #{rank}\n"
                 f"Total donated: {total}g\n"
                 f"Thank you for supporting the room! 🌟")
    else:
        await _w(bot, user.id,
                 f"@{user.username}: Not a donor yet.\n"
                 f"Gold-tip BankingBot to become a Supporter!\n"
                 f"!donate  !topdonors")


# ---------------------------------------------------------------------------
# !perks  — all perks overview
# ---------------------------------------------------------------------------

async def handle_perks(bot: "BaseBot", user: "User") -> None:
    """!perks  — show all available perks."""
    await _w(bot, user.id,
             "🌟 Available Perks:\n"
             "• VIP: !vipperks\n"
             "• Supporter: !supporter\n"
             "• Subscriber: !substatus\n"
             "• Titles & Badges: !shop\n"
             "• Event Points: !eventshop")


# ---------------------------------------------------------------------------
# !setdonationgoal <amount> [label]  — manager+ set donation goal
# ---------------------------------------------------------------------------

async def handle_setdonationgoal(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!setdonationgoal <gold_amount> [label]  — set donation goal (manager+)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !setdonationgoal <gold> [label]")
        return
    try:
        goal = int(args[1])
        if goal < 0:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "❌ Amount must be a positive number.")
        return
    label = " ".join(args[2:]) if len(args) > 2 else "Room Upgrades"
    db.set_room_setting("donation_goal", str(goal))
    db.set_room_setting("donation_goal_label", label[:60])
    await _w(bot, user.id, f"✅ Donation goal set: {goal}g — {label}")


# ---------------------------------------------------------------------------
# !donationaudit [page]  — admin audit of gold donations
# ---------------------------------------------------------------------------

async def handle_donationaudit(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!donationaudit [page]  — show gold donation audit log (admin+)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and above only.")
        return
    try:
        rows = db.get_top_gold_donors(20)
    except Exception:
        rows = []
    if not rows:
        await _w(bot, user.id, "💛 No gold donations recorded yet.")
        return
    lines = ["💛 Donation Audit:"]
    for row in rows[:6]:
        name  = row.get("username", "?")
        total = row.get("total_gold", 0)
        lines.append(f"  @{name}: {total}g total")
    goal = db.get_room_setting("donation_goal", "0")
    coll = db.get_room_setting("donation_collected", "0")
    lines.append(f"Goal: {coll}/{goal}g")
    await _w(bot, user.id, "\n".join(lines))


# ---------------------------------------------------------------------------
# !setsponsorprice <rain|event> <gold>  — manager+ set sponsor price
# ---------------------------------------------------------------------------

async def handle_setsponsorprice(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!setsponsorprice <rain|event> <gold>  — set sponsor price (manager+)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager and above only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !setsponsorprice <rain|event> <gold>")
        return
    kind = args[1].lower()
    if kind not in ("rain", "event"):
        await _w(bot, user.id, "❌ Type must be 'rain' or 'event'.")
        return
    try:
        price = int(args[2])
        if price < 0:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "❌ Price must be a positive number.")
        return
    key = "sponsor_goldrain_price" if kind == "rain" else "sponsor_event_price"
    db.set_room_setting(key, str(price))
    label = "Gold Rain" if kind == "rain" else "Event"
    await _w(bot, user.id, f"✅ {label} sponsor price set to {price}g.")
