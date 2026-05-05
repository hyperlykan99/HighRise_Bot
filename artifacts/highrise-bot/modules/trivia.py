"""
modules/trivia.py
-----------------
Trivia mini-game for the Mini Game Bot.

How it works:
  1. Any player types /trivia to start a question (public room announcement).
  2. The bot posts the question to the room so everyone can see it.
  3. Players answer with /answer <their answer>.
  4. The first player to type the correct answer wins TRIVIA_REWARD coins.
  5. Only one trivia question can be active at a time.
"""

import random
from highrise import BaseBot, User
import database as db
import config


# ---------------------------------------------------------------------------
# Question bank
# Each question has a "question" string and a list of "answers".
# Multiple answers are accepted (e.g. short and long form both work).
# All comparisons are case-insensitive.
# ---------------------------------------------------------------------------

_QUESTIONS = [
    {"question": "What is the capital of France?",                          "answers": ["paris"]},
    {"question": "How many sides does a hexagon have?",                     "answers": ["6", "six"]},
    {"question": "What planet is known as the Red Planet?",                 "answers": ["mars"]},
    {"question": "What is the largest ocean on Earth?",                     "answers": ["pacific", "pacific ocean"]},
    {"question": "Who painted the Mona Lisa?",                              "answers": ["leonardo da vinci", "da vinci", "leonardo"]},
    {"question": "What is 7 × 8?",                                          "answers": ["56", "fifty-six", "fifty six"]},
    {"question": "What is the chemical symbol for gold?",                   "answers": ["au"]},
    {"question": "In what year did World War II end?",                      "answers": ["1945"]},
    {"question": "What is the fastest land animal?",                        "answers": ["cheetah"]},
    {"question": "How many colors are in a rainbow?",                       "answers": ["7", "seven"]},
    {"question": "What is the largest planet in our solar system?",         "answers": ["jupiter"]},
    {"question": "What language is spoken in Brazil?",                      "answers": ["portuguese"]},
    {"question": "What is the square root of 144?",                         "answers": ["12", "twelve"]},
    {"question": "What year was the Eiffel Tower completed?",               "answers": ["1889"]},
    {"question": "How many continents are on Earth?",                       "answers": ["7", "seven"]},
    {"question": "What is the smallest country in the world?",              "answers": ["vatican", "vatican city"]},
    {"question": "How many strings does a standard guitar have?",           "answers": ["6", "six"]},
    {"question": "What is the hardest natural substance on Earth?",         "answers": ["diamond"]},
    {"question": "What gas do plants absorb from the air?",                 "answers": ["carbon dioxide", "co2"]},
    {"question": "How many players are on a basketball team on the court?", "answers": ["5", "five"]},
    {"question": "What is the capital of Japan?",                           "answers": ["tokyo"]},
    {"question": "How many hours are in a day?",                            "answers": ["24", "twenty-four", "twenty four"]},
    {"question": "What is the longest river in the world?",                 "answers": ["nile", "nile river"]},
    {"question": "What element does 'O' stand for on the periodic table?",  "answers": ["oxygen"]},
    {"question": "How many sides does a triangle have?",                    "answers": ["3", "three"]},
]


# ---------------------------------------------------------------------------
# Active game state (in-memory; resets when the bot restarts)
# ---------------------------------------------------------------------------

# When a trivia game is running, this holds the current question data.
# It is set to None when no game is active.
_active: dict | None = None


# ---------------------------------------------------------------------------
# Public API (called from bot.py)
# ---------------------------------------------------------------------------

def is_active() -> bool:
    """Return True if a trivia question is currently waiting to be answered."""
    return _active is not None


async def start_game(bot: BaseBot, user: User):
    """
    Start a new trivia game.
    Picks a random question and posts it publicly to the room.
    """
    global _active

    # Only one game at a time
    if _active is not None:
        await bot.highrise.send_whisper(
            user.id,
            "A trivia question is already active! Type /answer <your answer> to play."
        )
        return

    db.ensure_user(user.id, user.username)

    # Pick a random question
    question_data = random.choice(_QUESTIONS)
    _active = question_data.copy()   # store a copy so the original list is unchanged

    # Announce it to the whole room
    await bot.highrise.chat(
        f"[TRIVIA] {_active['question']}\n"
        "Type /answer <your answer> to win 25 coins!"
    )


async def handle_answer(bot: BaseBot, user: User, answer_text: str):
    """
    Check a player's answer against the active trivia question.
    If correct, award coins and end the game.
    If wrong, whisper a hint privately so only they know they were wrong.
    """
    global _active

    # This should not happen if bot.py checks is_active() first, but guard anyway
    if _active is None:
        await bot.highrise.send_whisper(
            user.id, "No trivia question is active right now. Type /trivia to start one!"
        )
        return

    db.ensure_user(user.id, user.username)

    # Compare case-insensitively, also strip extra spaces
    player_answer = answer_text.strip().lower()
    correct       = any(player_answer == a.lower() for a in _active["answers"])

    if correct:
        # Award the coins
        db.adjust_balance(user.id, config.TRIVIA_REWARD)
        db.record_game_win(user.id, user.username, "trivia")
        new_balance = db.get_balance(user.id)

        # Announce the win to the whole room
        await bot.highrise.chat(
            f"[TRIVIA] Correct! @{user.username} wins {config.TRIVIA_REWARD} coins! "
            f"Answer: {_active['answers'][0]}"
        )

        # Clear the active game
        _active = None
    else:
        # Wrong answer — whisper privately so we don't flood the room
        await bot.highrise.send_whisper(user.id, "Wrong answer! Keep trying.")
