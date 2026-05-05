"""
modules/leveling.py
-------------------
XP and levelling system for the Mini Game Bot.

Call award_xp() any time a player earns XP — after a game win,
a coinflip win, or a daily claim.

It records XP (with equipped-badge XP bonus for game wins), optionally
tracks coins earned toward the lifetime total, recomputes level, and
announces publicly if they leveled up.

All exceptions are caught so a levelling bug can never crash the bot.
"""

import database as db
from modules.shop import get_player_benefits


async def award_xp(
    bot,
    user,
    xp_amount: int,
    coins_earned: int = 0,
    is_game_win: bool = True,
) -> None:
    """
    Award XP to a player.

    Parameters
    ----------
    bot          : Highrise BaseBot instance
    user         : Highrise User who earned the XP
    xp_amount    : base XP to award (> 0)
    coins_earned : coins just won — added to the lifetime total in /profile
                   (pass 0 for events with no coin reward)
    is_game_win  : True for trivia/scramble/riddle/coinflip wins — applies the
                   player's equipped XP bonus.
                   False for /daily and any non-game event.
    """
    try:
        if coins_earned > 0:
            db.add_coins_earned(user.id, coins_earned)

        effective_xp = xp_amount
        if is_game_win:
            benefits = get_player_benefits(user.id)
            effective_xp += benefits["xp_bonus"]

        total_xp, old_level, new_level = db.add_xp(user.id, effective_xp)

        if new_level > old_level:
            display = db.get_display_name(user.id, user.username)
            await bot.highrise.chat(
                f"🎉 {display} leveled up to Level {new_level}! 🌟"
            )
    except Exception as exc:
        print(f"[LEVELING] Error awarding XP to {user.username}: {exc}")
