"""
modules/scramble.py
-------------------
Word Scramble mini-game for the Mini Game Bot.

How it works:
  1. Any player types /scramble to start a game (public room announcement).
  2. The bot scrambles a word and posts the scrambled letters to the room.
  3. Players unscramble it with /answer <word>.
  4. The first player to type the correct word wins SCRAMBLE_REWARD coins.
  5. Only one scramble game can be active at a time.
"""

import random
from highrise import BaseBot, User
import database as db
import config
from modules.utils import check_answer


# ---------------------------------------------------------------------------
# Word bank
# Words should be common enough that players can unscramble them,
# but not so short that scrambling makes them trivially easy.
# ---------------------------------------------------------------------------

_WORDS = [
    "planet", "puzzle", "castle", "dragon", "magic",
    "ocean",  "tiger",  "flame",  "crystal", "dance",
    "music",  "cloud",  "brave",  "bloom",   "storm",
    "river",  "night",  "water",  "light",   "happy",
    "orange", "silver", "jungle", "candle",  "rocket",
    "bridge", "forest", "mirror", "garden",  "island",
    "falcon", "cobalt", "marble", "shadow",  "winter",
]


# ---------------------------------------------------------------------------
# Active game state
# ---------------------------------------------------------------------------

_active: dict | None = None   # holds {"word": "...", "scrambled": "..."}


# ---------------------------------------------------------------------------
# Helper: scramble a word
# ---------------------------------------------------------------------------

def _scramble_word(word: str) -> str:
    """
    Randomly shuffle the letters of a word.
    Keeps re-shuffling until the result is different from the original
    (so the answer is never obviously visible).
    """
    letters = list(word)
    for _ in range(20):           # try up to 20 times to get a different arrangement
        random.shuffle(letters)
        scrambled = "".join(letters)
        if scrambled != word:     # stop as soon as we get something different
            return scrambled
    # If we somehow never got a different arrangement (very short words),
    # just return whatever we have — the game still works.
    return "".join(letters)


# ---------------------------------------------------------------------------
# Public API (called from bot.py)
# ---------------------------------------------------------------------------

def is_active() -> bool:
    """Return True if a scramble game is currently waiting to be solved."""
    return _active is not None


async def start_game(bot: BaseBot, user: User):
    """
    Start a new word-scramble game.
    Picks a random word, scrambles it, and posts it to the room.
    """
    global _active

    # Only one game at a time
    if _active is not None:
        await bot.highrise.send_whisper(
            user.id,
            "A scramble game is already active! Type /answer <word> to play."
        )
        return

    db.ensure_user(user.id, user.username)

    # Pick and scramble a word
    word      = random.choice(_WORDS)
    scrambled = _scramble_word(word)
    _active   = {"word": word, "scrambled": scrambled}

    # Log the answer to the console for testing — never shown in the room
    print(f"[SCRAMBLE] Correct answer: {_active['word']}")

    # Post it publicly
    await bot.highrise.chat(
        f"[SCRAMBLE] Unscramble this word:  {scrambled.upper()}\n"
        "Type /answer <word> to win 25 coins!"
    )


async def handle_answer(bot: BaseBot, user: User, answer_text: str):
    """
    Check whether the player unscrambled the word correctly.
    Correct → award coins, end game, announce winner.
    Wrong   → private whisper so we don't spam the room.
    """
    global _active

    if _active is None:
        await bot.highrise.send_whisper(
            user.id, "No scramble game is active. Type /scramble to start one!"
        )
        return

    db.ensure_user(user.id, user.username)

    # Use the shared flexible matcher — handles case, punctuation, and whitespace
    if check_answer(answer_text, [_active["word"]]):
        # Correct!
        db.adjust_balance(user.id, config.SCRAMBLE_REWARD)
        db.record_game_win(user.id, user.username, "scramble")

        await bot.highrise.chat(
            f"[SCRAMBLE] Correct! @{user.username} unscrambled '{_active['scrambled'].upper()}' "
            f"→ '{_active['word']}' and wins {config.SCRAMBLE_REWARD} coins!"
        )

        _active = None
    else:
        await bot.highrise.send_whisper(user.id, "Not quite! Keep trying.")
