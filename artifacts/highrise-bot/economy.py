"""
economy.py
----------
Shared coin-economy module for ALL bot modes.

Any future bot (GameBot, DJBot, BlackjackBot, HostBot) should import
the functions it needs from here so player balances and daily rewards
are consistent across bots.

User-facing commands handled here:
  /balance /bal /coins /coin /money — whisper coin balance (self or target)
  /daily        — claim free coins once every 24 hours
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
from modules.permissions  import can_moderate, is_admin


# ---------------------------------------------------------------------------
# Coin formatter
# ---------------------------------------------------------------------------

def fmt_coins(amount: int) -> str:
    """
    Return a human-readable coin string with 🪙 icon.
    Under 1 000: just '999 🪙'.
    1 000+:      '1,250,000 🪙 / 1.25M' (both comma form and short form).
    """
    s = f"{amount:,} 🪙"
    if amount >= 1_000_000_000_000:
        k = round(amount / 1_000_000_000_000, 2)
        return f"{s} / {k:g}T"
    if amount >= 1_000_000_000:
        k = round(amount / 1_000_000_000, 2)
        return f"{s} / {k:g}B"
    if amount >= 1_000_000:
        k = round(amount / 1_000_000, 2)
        return f"{s} / {k:g}M"
    if amount >= 1_000:
        k = round(amount / 1_000, 2)
        return f"{s} / {k:g}K"
    return s


# ---------------------------------------------------------------------------
# Balance command
# ---------------------------------------------------------------------------

async def handle_balance(bot: BaseBot, user: User, args: list | None = None):
    """
    Whisper the player's current coin balance privately.
    /bal             → self balance
    /bal <username>  → other player balance (privacy-aware)
    """
    try:
        db.ensure_user(user.id, user.username)

        raw_target = args[1].lstrip("@").strip() if args and len(args) > 1 else None

        if not raw_target:
            balance = db.get_balance(user.id)
            is_vip  = db.owns_item(user.id, "vip")
            # VIP status with time remaining
            try:
                from modules.vip import _calc_vip_remaining as _cvr
                vip_exp = db.get_room_setting(f"vip_expires_{user.id}", "")
                if is_vip:
                    rem = _cvr(vip_exp)
                    vip_str = (f"Active — {rem} left"
                               if rem and rem != "Expired" else "Active")
                else:
                    vip_str = "Inactive"
            except Exception:
                vip_str = "Active" if is_vip else "Inactive"
            # Luxe balance — plain number, ticket icon only (no coin suffix)
            try:
                from modules.luxe import get_luxe_balance as _glb
                luxe_line = f"\n🎟️ Luxe Tickets: {_glb(user.id):,}"
            except Exception:
                luxe_line = ""
            await bot.highrise.send_whisper(
                user.id,
                f"💰 Balance\nCoins: {balance:,}{luxe_line}\n💎 VIP: {vip_str}"
            )
            return

        # Looking up another player
        target = db.get_user_by_username(raw_target)
        if not target:
            await bot.highrise.send_whisper(
                user.id, "User not found. They may need to chat first."
            )
            return

        t_id   = target["user_id"]
        t_name = target["username"]

        # Privacy check
        is_staff = can_moderate(user.username) or is_admin(user.username)
        is_self  = (t_id == user.id)
        if not is_self and not is_staff:
            privacy = db.get_profile_privacy(t_name.lower())
            if not bool(privacy.get("show_money", 1)):
                await bot.highrise.send_whisper(user.id, f"@{t_name}'s balance is private.")
                return

        balance = db.get_balance(t_id)
        await bot.highrise.send_whisper(user.id, f"💰 @{t_name}: {balance:,} coins")
    except Exception as e:
        print(f"[BALANCE] ERROR user={user.username} args={args} error={e}")
        try:
            await bot.highrise.send_whisper(
                user.id, "Balance check failed. Please try again."
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Daily command
# ---------------------------------------------------------------------------

async def handle_daily(bot: BaseBot, user: User):
    """Daily coin reward with streak tracking. Once per calendar day (UTC)."""
    from datetime import date, datetime, timedelta
    db.ensure_user(user.id, user.username)

    if not db.can_claim_daily(user.id):
        stats   = db.get_daily_stats(user.id)
        streak  = stats["streak"]
        now     = datetime.utcnow()
        midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        secs    = (midnight - now).total_seconds()
        hours   = int(secs // 3600)
        mins    = int((secs % 3600) // 60)
        s_label = "day" if streak == 1 else "days"
        await bot.highrise.send_whisper(user.id,
            f"⏳ Daily already claimed.\n"
            f"Streak: {streak} {s_label}\n"
            f"Next claim: ~{hours}h {mins}m"
        )
        return

    # Determine what the new streak will be (peek without writing)
    yesterday = str(date.today() - timedelta(days=1))
    _conn = db.get_connection()
    _row  = _conn.execute(
        "SELECT last_claim, streak FROM daily_claims WHERE user_id = ?", (user.id,)
    ).fetchone()
    _conn.close()
    if _row is None:
        new_streak = 1
    elif _row["last_claim"] == yesterday:
        new_streak = (_row["streak"] or 1) + 1
    else:
        new_streak = 1

    # 3.1J — Structured streak rewards (Days 1-7, then flat after 7)
    _STREAK_COIN_BONUS   = {1: 0, 2: 1000, 3: 2000, 4: 4000, 5: 0,    6: 4000, 7: 0}
    _STREAK_TICKET_BONUS = {5: 10, 7: 25}

    benefits     = get_player_benefits(user.id)
    bonus_coins  = benefits["daily_coins_bonus"]
    bonus_xp     = benefits["daily_xp_bonus"]
    # V2 title/badge/VIP/event daily perks (separate from old shop TITLES system)
    try:
        from modules.title_system import get_active_boosts as _gab
        _v2p = _gab(user.id)["perks"]
        bonus_coins += int(_v2p.get("daily_coins_bonus", 0))
    except Exception:
        pass
    base_daily   = db.get_economy_settings()["daily_coins"]
    _day         = min(new_streak, 7)
    streak_bonus  = _STREAK_COIN_BONUS.get(_day, (_day - 1) * 25)
    bonus_tickets = _STREAK_TICKET_BONUS.get(_day, 0)
    chest_bonus   = 25000 if new_streak == 7 else 0
    actual_coins  = max(base_daily + bonus_coins + streak_bonus + chest_bonus, 1)
    actual_xp     = config.XP_DAILY + bonus_xp

    db.adjust_balance(user.id, actual_coins)
    if bonus_tickets:
        try:
            db.adjust_luxe_balance(user.id, bonus_tickets)
        except Exception:
            pass
    if new_streak == 7:
        import random as _rand
        if _rand.random() < 0.40:
            _extra = _rand.randint(10, 25)
            try:
                db.adjust_luxe_balance(user.id, _extra)
                bonus_tickets += _extra
            except Exception:
                pass

    db.record_daily_claim(user.id)
    track_quest(user.id, "daily_claim")
    track_quest(user.id, "earn_coins", actual_coins)
    await leveling.award_xp(bot, user, actual_xp, actual_coins, is_game_win=False)
    await check_achievements(bot, user, "daily")

    extra_txt = ""
    if bonus_tickets:
        extra_txt = f" +{bonus_tickets} 🎫"
    if new_streak == 7:
        extra_txt += " 🎁 Streak Chest!"
    s_label = "day" if new_streak == 1 else "days"
    print(f"[DAILY] @{user.username} claimed {actual_coins} coins streak={new_streak}")
    boost_note = f"\n(+{bonus_coins:,} boost)" if bonus_coins > 0 else ""
    await bot.highrise.send_whisper(user.id,
        (f"🎁 Daily Reward Claimed!\n"
         f"Reward: {actual_coins:,} 🪙{extra_txt}{boost_note}\n"
         f"Streak: {new_streak} {s_label}\n"
         f"Next claim: tomorrow")[:249]
    )


async def handle_streak(bot: BaseBot, user: User):
    """Show the player's daily streak status."""
    from datetime import datetime, timedelta
    db.ensure_user(user.id, user.username)
    stats  = db.get_daily_stats(user.id)
    streak = stats["streak"]
    best   = stats["best_streak"]
    total  = stats["total_claims"]

    if total == 0:
        await bot.highrise.send_whisper(user.id,
            "🔥 Daily Streak\n"
            "Current: 0 days\n"
            "Claim your first reward with !daily."
        )
        return

    now      = datetime.utcnow()
    midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    secs      = (midnight - now).total_seconds()
    hours     = int(secs // 3600)
    mins      = int((secs % 3600) // 60)
    can_claim = db.can_claim_daily(user.id)
    next_str  = "Ready! Type !daily" if can_claim else f"~{hours}h {mins}m"

    s_label = "day" if streak == 1 else "days"
    b_label = "day" if best   == 1 else "days"
    await bot.highrise.send_whisper(user.id,
        f"🔥 Daily Streak\n"
        f"Current: {streak} {s_label}\n"
        f"Best: {best} {b_label}\n"
        f"Total Claims: {total}\n"
        f"Next Daily: {next_str}"
    )


async def handle_dailystatus(bot: BaseBot, user: User):
    """Show daily claim status: available/claimed, streak, next reset."""
    from datetime import datetime, timedelta
    db.ensure_user(user.id, user.username)
    stats  = db.get_daily_stats(user.id)
    streak = stats["streak"]
    total  = stats["total_claims"]

    if total == 0:
        await bot.highrise.send_whisper(user.id,
            "🎁 Daily Status\n"
            "Today: Available\n"
            "Streak: 0 days\n"
            "Type !daily to claim."
        )
        return

    now      = datetime.utcnow()
    midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    secs      = (midnight - now).total_seconds()
    hours     = int(secs // 3600)
    mins      = int((secs % 3600) // 60)
    can_claim = db.can_claim_daily(user.id)
    today_str = "Available" if can_claim else "Claimed"
    next_str  = "Now!" if can_claim else f"~{hours}h {mins}m"
    s_label   = "day" if streak == 1 else "days"
    await bot.highrise.send_whisper(user.id,
        f"🎁 Daily Status\n"
        f"Today: {today_str}\n"
        f"Streak: {streak} {s_label}\n"
        f"Next Reset: {next_str}"
    )


# ---------------------------------------------------------------------------
# Leaderboard command
# ---------------------------------------------------------------------------

async def handle_leaderboard(bot: BaseBot, user: User):
    """!leaderboard / !lb / !top — leaderboard navigation menu."""
    await bot.highrise.send_whisper(user.id, (
        "🏆 Leaderboards\n"
        "!toprich — richest\n"
        "!topminers — mining\n"
        "!topfishers — fishing\n"
        "!topstreaks — streaks\n"
        "!topdonators — gold supporters\n"
        "!toptippers — P2P senders\n"
        "!toptipped — P2P receivers\n"
        "!profile — your stats"
    )[:249])


# ---------------------------------------------------------------------------
# Legacy profile / level commands (still used by main.py routing)
# ---------------------------------------------------------------------------

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
        f"💰 {fmt_coins(p['balance'])} | Lv {level}\n"
        f"✨ {xp} XP (need {xp_needed} for Lv {level + 1})\n"
        f"🏆 {p['total_games_won']} wins | {p['total_coins_earned']:,} 🪙 earned\n"
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
