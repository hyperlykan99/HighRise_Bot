"""
modules/leveling.py
-------------------
XP and levelling system for the Mini Game Bot.

Call award_xp() any time a player earns XP — after a game win,
a coinflip win, or a daily claim.

It records XP, optionally tracks coins earned toward the lifetime total,
recomputes the player's level, and announces publicly if they leveled up.

All exceptions are caught so a levelling bug can never crash the bot.
"""

import database as db


async def award_xp(bot, user, xp_amount: int, coins_earned: int = 0) -> None:
    """
    Award XP to a player.

    Parameters
    ----------
    bot          : the Highrise BaseBot instance
    user         : the Highrise User who earned the XP
    xp_amount    : how many XP to add (should be > 0)
    coins_earned : coins the player just won — tracked toward their lifetime
                   total shown in /profile (pass 0 for events with no coin reward)
    """
    try:
        if coins_earned > 0:
            db.add_coins_earned(user.id, coins_earned)

        total_xp, old_level, new_level = db.add_xp(user.id, xp_amount)

        if new_level > old_level:
            await bot.highrise.chat(
                f"🎉 @{user.username} leveled up to Level {new_level}! 🌟"
            )
    except Exception as exc:
        print(f"[LEVELING] Error awarding XP to {user.username}: {exc}")
