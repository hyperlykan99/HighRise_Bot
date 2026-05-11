"""
modules/player_cmds.py
-----------------------
Public player utility commands owned by host / EmceeBot.

/menu        — category help overview (2 whispers)
/cooldowns   — show personal cooldown status
/rewards     — player reward inbox
/wherebots   — where the bots spawn in the room
/updates     — latest room updates list
/rankup      — how to level up / rank up guide
"""
from __future__ import annotations

import datetime

import database as db


async def _w(bot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


# Latest room updates — edit to announce changes
_ROOM_UPDATES: list[str] = [
    "BlackJack rebuilt with simultaneous action model.",
    "First Hunt Race: win gold for rare firsts!",
    "Fish inventory system added (/myfish, /sellfish).",
    "Subscriber notification preferences added (/notif).",
    "Weekly Leaderboard added (/weeklylb).",
    "Bot tip leaderboard added (/tiplb).",
    "Big-announce system routing fixed.",
]

# Bot spawn labels — update to match room layout
_BOT_SPAWNS: dict[str, str] = {
    "EmceeBot":           "Stage",
    "BankingBot":         "Bank Desk",
    "GreatestProspector": "Mine Entrance",
    "MasterAngler":       "Fishing Dock",
    "AceSinatra":         "Casino Table",
    "ChipSoprano":        "Poker Table",
    "KeanuShield":        "Entrance",
    "DJ_DUDU":            "DJ Booth",
}


# ---------------------------------------------------------------------------
# /menu
# ---------------------------------------------------------------------------

async def handle_menu(bot, user) -> None:
    """/menu — category help overview."""
    await _w(bot, user.id,
             "📋 Menu\n"
             "Main: /start /profile /bal /rewards\n"
             "Mine: /mine /automine /orebag /mineprofile\n"
             "Fish: /fish /autofish /myfish /fishautosell")
    await _w(bot, user.id,
             "Games: /bj help /pokerhelp /coinflip\n"
             "Events: /events /active /firstfindstatus\n"
             "Economy: /shop /bank /daily /weeklylb\n"
             "Help: /mycommands /rules /wherebots")


# ---------------------------------------------------------------------------
# /cooldowns
# ---------------------------------------------------------------------------

async def handle_cooldowns_cmd(bot, user) -> None:
    """/cooldowns — show personal cooldown status."""
    lines = ["⏳ Your Cooldowns"]

    # Mine cooldown
    try:
        profile = db.get_or_create_mine_profile(user.id, user.username)
        last_mine = profile.get("last_mine_at") or profile.get("last_mine")
        if last_mine:
            last = datetime.datetime.fromisoformat(last_mine)
            if last.tzinfo is None:
                last = last.replace(tzinfo=datetime.timezone.utc)
            elapsed = (
                datetime.datetime.now(datetime.timezone.utc) - last
            ).total_seconds()
            cd = int(db.get_room_setting("mine_cooldown", "30"))
            remain = max(0, cd - int(elapsed))
            lines.append(f"Mine: {'ready' if remain == 0 else f'{remain}s'}")
        else:
            lines.append("Mine: ready")
    except Exception:
        lines.append("Mine: ready")

    # Fish cooldown
    try:
        from modules.fishing import FISHING_RODS
        fp = db.get_or_create_fish_profile(user.id, user.username)
        rod_name = fp.get("equipped_rod") or "Driftwood Rod"
        rod_cd = FISHING_RODS.get(rod_name, FISHING_RODS["Driftwood Rod"])["cooldown"]
        last_fish = fp.get("last_fish_at")
        if last_fish:
            last = datetime.datetime.fromisoformat(last_fish)
            if last.tzinfo is None:
                last = last.replace(tzinfo=datetime.timezone.utc)
            elapsed = (
                datetime.datetime.now(datetime.timezone.utc) - last
            ).total_seconds()
            remain = max(0, int(rod_cd - elapsed))
            lines.append(f"Fish: {'ready' if remain == 0 else f'{remain}s'}")
        else:
            lines.append("Fish: ready")
    except Exception:
        lines.append("Fish: ready")

    # Daily
    try:
        rec = db.get_daily_status(user.id)
        if rec and rec.get("claimed_today"):
            lines.append("Daily: claimed ✅")
        else:
            lines.append("Daily: available /daily")
    except Exception:
        lines.append("Daily: unknown")

    # AutoMine session
    try:
        from modules.mining import _auto_mine_sessions
        if user.id in _auto_mine_sessions:
            lines.append("AutoMine: active session")
    except Exception:
        pass

    # AutoFish session
    try:
        from modules.fishing import _auto_fish_sessions
        if user.id in _auto_fish_sessions:
            lines.append("AutoFish: active session")
    except Exception:
        pass

    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /rewards
# ---------------------------------------------------------------------------

async def handle_rewards_inbox(bot, user) -> None:
    """/rewards — show player reward inbox."""
    lines = ["🎁 Your Rewards"]
    found = False

    # Pending race rewards
    try:
        pending = db.get_pending_race_winners_by_username(user.username, limit=5)
        for r in pending:
            tv  = r.get("target_value") or "?"
            amt = r.get("gold_amount", 0)
            lines.append(f"• Race ({tv}): {amt:g}g pending")
            found = True
    except Exception:
        pass

    # Recently paid race rewards
    try:
        recent = db.get_recent_race_winners_for_user(user.id, limit=3)
        for r in recent:
            if r.get("payout_status") in ("paid_manual", "paid_gold"):
                amt = r.get("gold_amount", 0)
                lines.append(f"• Race reward: {amt:g}g paid ✅")
                found = True
    except Exception:
        pass

    # Weekly snapshot (if top ranked)
    try:
        snap = db.get_latest_weekly_snapshot()
        if snap and snap.get("user_id") == user.id:
            lines.append(
                f"• Weekly {snap.get('category','?')}: "
                f"#{snap.get('rank',1)}"
            )
            found = True
    except Exception:
        pass

    if not found:
        lines.append("No rewards pending.")
        lines.append("Play events and races to earn!")

    lines.append("Staff: /rewardpending")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /wherebots
# ---------------------------------------------------------------------------

async def handle_wherebots(bot, user) -> None:
    """/wherebots — show bot locations in the room."""
    lines = ["📍 Bot Locations"]
    try:
        instances = db.get_bot_instances()
        if instances:
            for inst in instances:
                uname = inst.get("bot_username") or inst.get("bot_mode", "?")
                label = _BOT_SPAWNS.get(uname, "Room")
                lines.append(f"{uname}: {label}")
        else:
            raise ValueError("no instances")
    except Exception:
        for uname, label in list(_BOT_SPAWNS.items())[:6]:
            lines.append(f"{uname}: {label}")
    await _w(bot, user.id, "\n".join(lines[:8])[:249])


# ---------------------------------------------------------------------------
# /updates
# ---------------------------------------------------------------------------

async def handle_updates(bot, user) -> None:
    """/updates — latest room updates."""
    lines = ["📢 Latest Updates"]
    for upd in _ROOM_UPDATES[-6:]:
        lines.append(f"• {upd}")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /rankup
# ---------------------------------------------------------------------------

async def handle_rankup(bot, user) -> None:
    """/rankup — guide on how to level up and improve rank."""
    await _w(bot, user.id,
             "📈 How to Rank Up\n"
             "• Mine (/mine) for MXP and ore value\n"
             "• Fish (/fish) for FXP and coin rewards\n"
             "• Claim daily coins (/daily)\n"
             "• Win First Hunt races (/firstfind)\n"
             "• Complete quests (/quests)\n"
             "• Play events (/events)\n"
             "• Check your stats: /profile /mineprofile")
