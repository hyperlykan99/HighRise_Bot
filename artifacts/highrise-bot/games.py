"""
games.py
--------
Mini-game coordinator for the HangoutBot.

This module is the single interface between main.py (or any future bot entry
point) and the individual game modules in modules/.

What it does:
  - Routes /trivia, /scramble, /riddle, /coinflip to the right module
  - Routes /answer to whichever game is currently active
  - Provides reset_all_games() so admin.py can clear stuck games
  - Provides any_game_active() so the entry point can guard against
    starting a second game mid-round

Future bots that want to run games just import from here:
    from games import handle_game_command, handle_answer

Individual game logic lives in:
  modules/trivia.py    — question bank + answer state
  modules/scramble.py  — word bank + scramble state
  modules/riddle.py    — riddle bank + answer state
  modules/coinflip.py  — coin-flip logic
"""

from highrise import BaseBot, User

# Import each game module so we can call their functions and read their state
import modules.trivia   as trivia
import modules.scramble as scramble
import modules.riddle   as riddle
from modules.coinflip import handle_coinflip


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
    if cmd == "trivia":
        await trivia.start_game(bot, user)

    elif cmd == "scramble":
        await scramble.start_game(bot, user)

    elif cmd == "riddle":
        await riddle.start_game(bot, user)

    elif cmd == "coinflip":
        await handle_coinflip(bot, user, args)


# ---------------------------------------------------------------------------
# Answer router
# ---------------------------------------------------------------------------

async def handle_answer(bot: BaseBot, user: User, answer_text: str):
    """
    Route /answer to whichever game is currently active.
    Only one game can be active at a time.
    If no game is running, whisper the player.
    """
    if trivia.is_active():
        await trivia.handle_answer(bot, user, answer_text)

    elif scramble.is_active():
        await scramble.handle_answer(bot, user, answer_text)

    elif riddle.is_active():
        await riddle.handle_answer(bot, user, answer_text)

    else:
        await bot.highrise.send_whisper(user.id, "No active game right now.")


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def any_game_active() -> bool:
    """Return True if any mini-game is currently waiting for an answer."""
    return trivia.is_active() or scramble.is_active() or riddle.is_active()


def reset_all_games():
    """
    Clear the active state for every game module.
    Called by admin.py when an admin uses /resetgame.
    """
    trivia._active  = None
    scramble._active = None
    riddle._active   = None
