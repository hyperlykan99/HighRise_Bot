"""
economy.py
----------
Shared coin-economy module for ALL bot modes.

Any future bot (GameBot, DJBot, BlackjackBot, HostBot) should import
the functions it needs from here so player balances and daily rewards
are consistent across bots.

User-facing commands handled here:
  /balance      — whisper the player's current coin balance
  /daily        — claim 50 free coins once every 24 hours
  /leaderboard  — whisper the top 10 richest players

Admin coin commands (/addcoins, /removecoins) live in admin.py so that
economy.py stays purely about the player-facing experience.
"""

from highrise import BaseBot, User
import database as db
import config
import modules.leveling as leveling
from modules.shop         import get_player_benefits
from modules.achievements import check_achievements
from modules.quests       import track_quest
from modules.reputation   import get_rank


async def handle_balance(bot: BaseBot, user: User):
    """Whisper the player's current coin balance privately."""
    db.ensure_user(user.id, user.username)
    balance = db.get_balance(user.id)
    await bot.highrise.send_whisper(user.id, f"Your balance: {balance} coins.")


async def handle_daily(bot: BaseBot, user: User):
    """
    Give the player their daily coin reward.
    Claimable once per calendar day — resets at midnight UTC.
    """
    db.ensure_user(user.id, user.username)

    if not db.can_claim_daily(user.id):
        await bot.highrise.send_whisper(
            user.id,
            "⏰ Already claimed today! Come back tomorrow for more coins."
        )
        return

    benefits     = get_player_benefits(user.id)
    bonus_coins  = benefits["daily_coins_bonus"]
    bonus_xp     = benefits["daily_xp_bonus"]
    base_daily   = db.get_economy_settings()["daily_coins"]
    actual_coins = base_daily + bonus_coins
    actual_xp    = config.XP_DAILY + bonus_xp

    actual_coins = db.adjust_balance_capped(user.id, actual_coins)
    db.record_daily_claim(user.id)
    track_quest(user.id, "daily_claim")
    track_quest(user.id, "earn_coins", actual_coins)
    await leveling.award_xp(bot, user, actual_xp, actual_coins, is_game_win=False)
    await check_achievements(bot, user, "daily")
    new_balance = db.get_balance(user.id)

    msg = f"🎁 Daily reward! +{actual_coins}c"
    if bonus_coins:
        msg += f" (incl. +{bonus_coins} bonus)"
    msg += f". Balance: {new_balance}🪙"
    await bot.highrise.send_whisper(user.id, msg)


async def handle_leaderboard(bot: BaseBot, user: User):
    """Whisper the top players sorted by coin balance."""
    top = db.get_leaderboard(config.LEADERBOARD_SIZE)

    if not top:
        await bot.highrise.send_whisper(user.id, "No players on the leaderboard yet!")
        return

    lines = [f"-- Top {len(top)} Players --"]
    for entry in top:
        lines.append(f"#{entry['rank']} {entry['username']} {entry['balance']}c")

    msg = "\n".join(lines)
    await bot.highrise.send_whisper(user.id, msg[:249])


async def handle_profile(bot: BaseBot, user: User):
    """Whisper the player's full profile: level, XP, coins, games won, coins earned."""
    db.ensure_user(user.id, user.username)
    p = db.get_profile(user.id)
    if not p:
        await bot.highrise.send_whisper(user.id, "Profile not found. Try again!")
        return
    level     = p["level"]
    xp        = p["xp"]
    xp_needed = db.xp_for_level(level + 1) - xp
    badge     = p.get("equipped_badge") or "none"
    title_    = p.get("equipped_title") or "none"
    rep_row   = db.get_reputation(user.id)
    rep       = rep_row["rep_received"] if rep_row else 0
    rank      = get_rank(rep)
    await bot.highrise.send_whisper(user.id, (
        f"-- {p['username']} --\n"
        f"💰 {p['balance']} coins | Lv {level}\n"
        f"✨ {xp} XP (need {xp_needed} for Lv {level + 1})\n"
        f"🏆 {p['total_games_won']} wins | 🪙 {p['total_coins_earned']} earned\n"
        f"⭐ Rep: {rep} ({rank})\n"
        f"🎨 {badge} | 🏷️ {title_}"
    )[:249])


async def handle_level(bot: BaseBot, user: User):
    """Whisper the player's current level and XP progress toward the next level."""
    db.ensure_user(user.id, user.username)
    p = db.get_profile(user.id)
    if not p:
        await bot.highrise.send_whisper(user.id, "Profile not found. Try again!")
        return
    level    = p["level"]
    xp       = p["xp"]
    xp_this  = db.xp_for_level(level)
    xp_next  = db.xp_for_level(level + 1)
    progress = xp - xp_this
    needed   = xp_next - xp
    await bot.highrise.send_whisper(user.id,
        f"⭐ Level {level}  |  {xp} XP total\n"
        f"Progress: {progress} / {xp_next - xp_this} XP into this level\n"
        f"Need {needed} more XP to reach Level {level + 1}"
    )


async def handle_xp_leaderboard(bot: BaseBot, user: User):
    """Whisper the top players sorted by XP."""
    top = db.get_xp_leaderboard(config.LEADERBOARD_SIZE)
    if not top:
        await bot.highrise.send_whisper(user.id, "No players on the XP leaderboard yet!")
        return
    lines = [f"-- Top {len(top)} by XP --"]
    for entry in top:
        lines.append(
            f"  #{entry['rank']}  {entry['username']}  —  Lv {entry['level']}  ({entry['xp']} XP)"
        )
    await bot.highrise.send_whisper(user.id, "\n".join(lines))
