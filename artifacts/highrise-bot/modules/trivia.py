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
from modules.utils import check_answer
from modules.cooldowns import check_room_cooldown, set_room_cooldown


# ---------------------------------------------------------------------------
# Question bank
# Each question has a "question" string and a list of "answers".
# Multiple answers are accepted (e.g. short and long form both work).
# All comparisons are case-insensitive.
# ---------------------------------------------------------------------------

_QUESTIONS = [
    # ── Science & Nature ──────────────────────────────────────────────────────
    {"question": "What is the largest planet in our solar system?",              "answers": ["jupiter"]},
    {"question": "What gas do plants absorb from the air?",                      "answers": ["carbon dioxide", "co2"]},
    {"question": "What is the chemical symbol for water?",                       "answers": ["h2o"]},
    {"question": "What force keeps us on the ground?",                           "answers": ["gravity"]},
    {"question": "What planet is known as the Red Planet?",                      "answers": ["mars"]},
    {"question": "What is the fastest land animal?",                             "answers": ["cheetah"]},
    {"question": "How many colors are in a rainbow?",                            "answers": ["7", "seven"]},
    {"question": "What element does O stand for on the periodic table?",         "answers": ["oxygen"]},
    {"question": "What is the chemical symbol for gold?",                        "answers": ["au"]},
    {"question": "How many bones are in the adult human body?",                  "answers": ["206"]},

    # ── Geography ─────────────────────────────────────────────────────────────
    {"question": "What is the capital of France?",                               "answers": ["paris"]},
    {"question": "What is the largest ocean on Earth?",                          "answers": ["pacific", "pacific ocean"]},
    {"question": "How many continents are there on Earth?",                      "answers": ["7", "seven"]},
    {"question": "What is the longest river in the world?",                      "answers": ["nile", "nile river"]},
    {"question": "What is the smallest country in the world?",                   "answers": ["vatican", "vatican city"]},
    {"question": "What language is spoken in Brazil?",                           "answers": ["portuguese"]},
    {"question": "What is the capital of Japan?",                                "answers": ["tokyo"]},
    {"question": "What country has the largest population?",                     "answers": ["china"]},
    {"question": "What is the tallest mountain in the world?",                   "answers": ["everest", "mount everest"]},
    {"question": "What continent is Egypt in?",                                  "answers": ["africa"]},

    # ── Math ──────────────────────────────────────────────────────────────────
    {"question": "What is 7 times 8?",                                           "answers": ["56", "fifty-six", "fifty six"]},
    {"question": "What is the square root of 144?",                              "answers": ["12", "twelve"]},
    {"question": "How many hours are in a day?",                                 "answers": ["24", "twenty-four", "twenty four"]},
    {"question": "What is half of 200?",                                         "answers": ["100", "one hundred"]},
    {"question": "How many sides does a hexagon have?",                          "answers": ["6", "six"]},
    {"question": "What is 15 plus 27?",                                          "answers": ["42", "forty-two", "forty two"]},
    {"question": "What is 100 divided by 4?",                                    "answers": ["25", "twenty-five", "twenty five"]},
    {"question": "How many sides does a triangle have?",                         "answers": ["3", "three"]},

    # ── Music ─────────────────────────────────────────────────────────────────
    {"question": "Which band performed Bohemian Rhapsody?",                      "answers": ["queen"]},
    {"question": "Who is known as the King of Pop?",                             "answers": ["michael jackson", "mj"]},
    {"question": "What instrument has 88 keys?",                                 "answers": ["piano"]},
    {"question": "Which country does K-pop music come from?",                    "answers": ["south korea", "korea"]},
    {"question": "How many strings does a standard guitar have?",                "answers": ["6", "six"]},
    {"question": "Which artist is known for the song Shape of You?",             "answers": ["ed sheeran"]},

    # ── Movies & TV ───────────────────────────────────────────────────────────
    {"question": "What animated film features the lion cub Simba?",             "answers": ["lion king", "the lion king"]},
    {"question": "Who played Iron Man in the Marvel movies?",                    "answers": ["robert downey jr", "robert downey"]},
    {"question": "What is the highest-grossing movie of all time?",              "answers": ["avatar"]},
    {"question": "What Disney movie features a mermaid princess?",               "answers": ["the little mermaid", "little mermaid"]},
    {"question": "What movie is about a shark terrorizing a beach town?",        "answers": ["jaws"]},
    {"question": "What show features the chemistry teacher Walter White?",       "answers": ["breaking bad"]},

    # ── Gaming ────────────────────────────────────────────────────────────────
    {"question": "What is the best-selling video game of all time?",             "answers": ["minecraft"]},
    {"question": "What is the name of Marios main turtle villain?",              "answers": ["bowser"]},
    {"question": "What color is Sonic the Hedgehog?",                            "answers": ["blue"]},
    {"question": "What is the first Pokemon in the Pokedex?",                    "answers": ["bulbasaur"]},
    {"question": "What game features a soldier hero called Master Chief?",       "answers": ["halo"]},

    # ── Internet & Culture ────────────────────────────────────────────────────
    {"question": "What does LOL stand for?",                                     "answers": ["laugh out loud"]},
    {"question": "What does GG mean in gaming?",                                 "answers": ["good game"]},
    {"question": "What is the most watched YouTube video of all time?",          "answers": ["baby shark"]},
    {"question": "What does BRB stand for?",                                     "answers": ["be right back"]},
    {"question": "What app uses a ghost as its logo?",                           "answers": ["snapchat"]},

    # ── Food ──────────────────────────────────────────────────────────────────
    {"question": "What is the main ingredient in guacamole?",                    "answers": ["avocado"]},
    {"question": "What country is sushi originally from?",                       "answers": ["japan"]},
    {"question": "What fruit is used to make wine?",                             "answers": ["grape", "grapes"]},
    {"question": "What is the most popular pizza topping worldwide?",            "answers": ["cheese", "pepperoni"]},
    {"question": "What yellow fruit do monkeys famously love?",                  "answers": ["banana"]},

    # ── Animals ───────────────────────────────────────────────────────────────
    {"question": "What is the largest animal on Earth?",                         "answers": ["blue whale", "whale"]},
    {"question": "What is the only mammal that can truly fly?",                  "answers": ["bat"]},
    {"question": "What do you call a baby dog?",                                 "answers": ["puppy"]},
    {"question": "What do you call a baby cat?",                                 "answers": ["kitten"]},
    {"question": "How many legs does an insect have?",                           "answers": ["6", "six"]},
    {"question": "What animal is known as mans best friend?",                    "answers": ["dog"]},

    # ── Sports ────────────────────────────────────────────────────────────────
    {"question": "How many players are on a soccer team on the field?",          "answers": ["11", "eleven"]},
    {"question": "In which sport would you perform a slam dunk?",                "answers": ["basketball"]},
    {"question": "How many rings are on the Olympic flag?",                      "answers": ["5", "five"]},
    {"question": "What sport uses a shuttlecock?",                               "answers": ["badminton"]},
    {"question": "How many players are on each side in volleyball?",             "answers": ["6", "six"]},
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
            "❓ Trivia is already going! Type /answer to play."
        )
        return

    # Room-wide cooldown — prevents back-to-back games
    remaining = check_room_cooldown("trivia", config.TRIVIA_COOLDOWN)
    if remaining is not None:
        await bot.highrise.send_whisper(
            user.id, f"⏳ Trivia on cooldown! Try again in {remaining}s."
        )
        return

    db.ensure_user(user.id, user.username)

    # Pick a random question
    question_data = random.choice(_QUESTIONS)
    _active = question_data.copy()   # store a copy so the original list is unchanged
    set_room_cooldown("trivia")      # start the 30 s gap

    # Log the answer to the console for testing — never shown in the room
    print(f"[TRIVIA] Correct answer: {_active['answers'][0]}")

    # Announce it to the whole room
    await bot.highrise.chat(
        f"🎯 TRIVIA TIME!\n"
        f"{_active['question']}\n"
        f"Type /answer to win {config.TRIVIA_REWARD} coins! 🪙"
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

    # Use the shared flexible matcher — handles case, punctuation, and articles
    correct = check_answer(answer_text, _active["answers"])

    if correct:
        # Award the coins
        db.adjust_balance(user.id, config.TRIVIA_REWARD)
        db.record_game_win(user.id, user.username, "trivia")
        new_balance = db.get_balance(user.id)

        # Announce the win to the whole room
        await bot.highrise.chat(
            f"🎉 @{user.username} got it! Answer: {_active['answers'][0]} "
            f"| +{config.TRIVIA_REWARD} coins 🪙"
        )

        # Clear the active game
        _active = None
    else:
        # Wrong answer — whisper privately so we don't flood the room
        await bot.highrise.send_whisper(user.id, "❌ Not quite! Keep guessing.")
