"""
!boostadmin — room-wide luck/speed boost management (admin+, Update 3.1I).

Commands:
  !boostadmin start mining luck <amount> <minutes>
  !boostadmin start mining speed <seconds> <minutes>
  !boostadmin start fishing luck <amount> <minutes>
  !boostadmin start fishing speed <seconds> <minutes>
  !boostadmin stop mining
  !boostadmin stop fishing
  !boostadmin status
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta

import database as db
from highrise import BaseBot
from highrise.models import User

from modules.permissions import is_admin, is_owner


def _w_sync(bot: BaseBot, uid: str, msg: str):
    import asyncio
    return bot.highrise.send_whisper(uid, msg[:249])


async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


def _require_admin(uname: str) -> bool:
    return is_admin(uname) or is_owner(uname)


async def handle_boostadmin(bot: BaseBot, user: User, args: list[str]) -> None:
    """!boostadmin — room boost management (admin+)."""
    if not _require_admin(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return

    sub = args[1].lower() if len(args) >= 2 else "status"

    # ── status ────────────────────────────────────────────────────────────────
    if sub == "status":
        try:
            mine_b  = db.get_active_room_boosts("mining")
            fish_b  = db.get_active_room_boosts("fishing")
        except Exception:
            await _w(bot, user.id, "⚠️ Could not load boost status.")
            return
        lines = ["🚀 Boost Status"]
        if mine_b:
            for b in mine_b:
                lines.append(f"⛏️ {b['boost_type']} +{b['amount']} "
                              f"(until {b['expires_at'][:16]})")
        else:
            lines.append("⛏️ Mining: no active boosts")
        if fish_b:
            for b in fish_b:
                lines.append(f"🎣 {b['boost_type']} +{b['amount']} "
                              f"(until {b['expires_at'][:16]})")
        else:
            lines.append("🎣 Fishing: no active boosts")
        await _w(bot, user.id, "\n".join(lines)[:249])
        return

    # ── stop <system> ──────────────────────────────────────────────────────────
    if sub == "stop" and len(args) >= 3:
        system = args[2].lower()
        if system not in ("mining", "fishing"):
            await _w(bot, user.id, "Usage: !boostadmin stop mining|fishing")
            return
        try:
            n = db.remove_room_boosts(system)
            emoji = "⛏️" if system == "mining" else "🎣"
            await _w(bot, user.id, f"{emoji} {system.title()} boosts cleared ({n} removed).")
        except Exception:
            await _w(bot, user.id, "⚠️ Could not remove boosts.")
        return

    # ── start <system> <type> <amount> <minutes> ──────────────────────────────
    if sub == "start" and len(args) >= 6:
        system     = args[2].lower()
        boost_type = args[3].lower()
        try:
            amount = int(args[4])
            mins   = int(args[5])
        except ValueError:
            await _w(bot, user.id,
                     "Usage: !boostadmin start mining|fishing luck|speed <amount> <minutes>")
            return

        if system not in ("mining", "fishing"):
            await _w(bot, user.id, "System must be: mining or fishing")
            return
        if boost_type not in ("luck", "speed"):
            await _w(bot, user.id, "Boost type must be: luck or speed")
            return
        if amount <= 0 or mins <= 0:
            await _w(bot, user.id, "Amount and minutes must be > 0.")
            return
        if mins > 1440:
            await _w(bot, user.id, "Max duration: 1440 minutes (24h).")
            return

        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=mins)).isoformat()
        try:
            db.add_room_boost(system, boost_type, amount, expires_at,
                              source="boostadmin", created_by=user.username)
        except Exception:
            await _w(bot, user.id, "⚠️ Could not save boost.")
            return

        emoji = "⛏️" if system == "mining" else "🎣"
        verb  = f"+{amount} luck" if boost_type == "luck" else f"-{amount}s speed"
        await _w(bot, user.id,
                 f"✅ {emoji} {system.title()} {boost_type} boost started: "
                 f"{verb} for {mins}m.")
        return

    # ── fallback help ─────────────────────────────────────────────────────────
    await _w(bot, user.id,
             "🚀 Boost Admin\n"
             "!boostadmin status\n"
             "!boostadmin start mining|fishing luck|speed <amt> <mins>\n"
             "!boostadmin stop mining|fishing")
