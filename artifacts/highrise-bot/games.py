"""
games.py
--------
Mini-game coordinator for the HangoutBot.

This module is the single interface between main.py (or any future bot entry
point) and the individual game modules in modules/.

What it does:
  - Routes /trivia, /scramble, /riddle, /coinflip to the right module
  - Routes /answer to whichever game is currently active
  - Starts the answer timer whenever a new mini game begins
  - Cancels the timer when a game is answered correctly
  - Provides reset_all_games() so admin.py / auto_games.py can clear stuck games
  - Provides any_game_active() so the entry point can guard against
    starting a second game mid-round

Future bots that want to run games just import from here:
    from games import handle_game_command, handle_answer
"""

from highrise import BaseBot, User

import config
from modules.cooldowns import check_user_cooldown, set_user_cooldown
from modules.quests    import track_quest

# Import each game module so we can call their functions and read their state
import modules.trivia   as trivia
import modules.scramble as scramble
import modules.riddle   as riddle
from modules.coinflip import handle_coinflip

# Auto-games: answer timer management (imported after modules to avoid circular)
import modules.auto_games as auto_games


# ---------------------------------------------------------------------------
# Game command router
# ---------------------------------------------------------------------------

async def handle_game_command(bot: BaseBot, user: User, cmd: str, args: list[str]):
    """
    Route a game command to the correct module.

    Parameters
    ----------
    cmd  : the command name in lowercase ("trivia", "scramble", "riddle", "coinflip")
    args : the full parsed argument list (args[0] == cmd)
    """
    if cmd in {"trivia", "scramble", "riddle"}:
        # Global guard: only one mini game at a time
        if any_game_active():
            await bot.highrise.send_whisper(
                user.id,
                "🎮 A game is already active! Answer or wait for the timer."
            )
            return

    if cmd == "trivia":
        await trivia.start_game(bot, user)
        if trivia.is_active():
            ans = trivia.get_current_answer() or ""
            auto_games.start_answer_timer(bot, "trivia", ans)

    elif cmd == "scramble":
        await scramble.start_game(bot, user)
        if scramble.is_active():
            ans = scramble.get_current_answer() or ""
            auto_games.start_answer_timer(bot, "scramble", ans)

    elif cmd == "riddle":
        await riddle.start_game(bot, user)
        if riddle.is_active():
            ans = riddle.get_current_answer() or ""
            auto_games.start_answer_timer(bot, "riddle", ans)

    elif cmd == "coinflip":
        await handle_coinflip(bot, user, args)


# ---------------------------------------------------------------------------
# Answer router
# ---------------------------------------------------------------------------

async def handle_answer(bot: BaseBot, user: User, answer_text: str):
    """
    Route /answer to whichever game is currently active.
    Cancels the answer timer if the player guesses correctly.
    If no game is running, whisper the player.
    """
    # Per-user cooldown — prevents rapid-fire answer spam
    remaining = check_user_cooldown("answer", user.id, config.ANSWER_COOLDOWN)
    if remaining is not None:
        await bot.highrise.send_whisper(
            user.id, f"⏳ Wait {remaining}s before answering again."
        )
        return
    set_user_cooldown("answer", user.id)   # record this attempt immediately
    track_quest(user.id, "answer")

    if trivia.is_active():
        await trivia.handle_answer(bot, user, answer_text)
        if not trivia.is_active():          # correct answer → game just ended
            auto_games.cancel_answer_timer()

    elif scramble.is_active():
        await scramble.handle_answer(bot, user, answer_text)
        if not scramble.is_active():
            auto_games.cancel_answer_timer()

    elif riddle.is_active():
        await riddle.handle_answer(bot, user, answer_text)
        if not riddle.is_active():
            auto_games.cancel_answer_timer()

    else:
        await bot.highrise.send_whisper(
            user.id,
            "😴 No game running right now! Try /trivia, /scramble, or /riddle."
        )


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def any_game_active() -> bool:
    """Return True if any mini-game is currently waiting for an answer."""
    return trivia.is_active() or scramble.is_active() or riddle.is_active()


def reset_all_games():
    """
    Clear the active state for every game module.
    Called by admin.py / auto_games.py when a moderator uses /resetgame.
    """
    trivia._active   = None
    scramble._active = None
    riddle._active   = None
