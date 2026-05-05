"""
modules/riddle.py
-----------------
Riddle mini-game for the Mini Game Bot.

How it works:
  1. Any player types /riddle to start a game (public room announcement).
  2. The bot posts a riddle to the room.
  3. Players answer with /answer <answer>.
  4. The first player to answer correctly wins RIDDLE_REWARD coins.
  5. Only one riddle can be active at a time.
"""

import random
from highrise import BaseBot, User
import database as db
import config
from modules.utils import check_answer


# ---------------------------------------------------------------------------
# Riddle bank
# Each riddle has a "riddle" string and a list of accepted "answers".
# Multiple answers are accepted (synonyms, short/long forms).
# All comparisons are case-insensitive.
# ---------------------------------------------------------------------------

_RIDDLES = [
    {
        "riddle":  "I have hands but I can't clap. What am I?",
        "answers": ["clock", "a clock", "watch", "a watch"],
    },
    {
        "riddle":  "The more you take, the more you leave behind. What am I?",
        "answers": ["footsteps", "steps", "a footstep"],
    },
    {
        "riddle":  "I speak without a mouth and hear without ears. I have no body but come alive with the wind. What am I?",
        "answers": ["echo", "an echo"],
    },
    {
        "riddle":  "I have a head and a tail but no body. What am I?",
        "answers": ["coin", "a coin"],
    },
    {
        "riddle":  "What gets wetter the more it dries?",
        "answers": ["towel", "a towel"],
    },
    {
        "riddle":  "I have cities but no houses. I have mountains but no trees. I have water but no fish. What am I?",
        "answers": ["map", "a map"],
    },
    {
        "riddle":  "What can you catch but not throw?",
        "answers": ["cold", "a cold"],
    },
    {
        "riddle":  "I have keys but no locks. I have space but no room. You can enter but can't go inside. What am I?",
        "answers": ["keyboard", "a keyboard", "piano", "a piano"],
    },
    {
        "riddle":  "What goes up but never comes down?",
        "answers": ["age", "your age"],
    },
    {
        "riddle":  "I have one eye but cannot see. What am I?",
        "answers": ["needle", "a needle"],
    },
    {
        "riddle":  "What is always in front of you but can never be seen?",
        "answers": ["future", "the future"],
    },
    {
        "riddle":  "The more of me you have, the less you can see. What am I?",
        "answers": ["darkness", "dark"],
    },
    {
        "riddle":  "I begin with E and end with E, but I only have one letter. What am I?",
        "answers": ["envelope", "an envelope"],
    },
    {
        "riddle":  "I run but have no legs. I have a mouth but never talk. What am I?",
        "answers": ["river", "a river"],
    },
    {
        "riddle":  "What has teeth but cannot bite?",
        "answers": ["comb", "a comb"],
    },
    {
        "riddle":  "I'm light as a feather, but even the strongest person can't hold me for more than a few minutes. What am I?",
        "answers": ["breath", "your breath", "air"],
    },
    {
        "riddle":  "What has a neck but no head?",
        "answers": ["bottle", "a bottle"],
    },
    {
        "riddle":  "I have branches but no leaves, no fruit, and no trunk. What am I?",
        "answers": ["bank", "a bank"],
    },
    {
        "riddle":  "What can fill a room but takes up no space?",
        "answers": ["light", "silence"],
    },
    {
        "riddle":  "What has four legs in the morning, two at noon, and three in the evening?",
        "answers": ["human", "a human", "man", "a man", "person"],
    },
]


# ---------------------------------------------------------------------------
# Active game state
# ---------------------------------------------------------------------------

_active: dict | None = None   # holds the current riddle dict, or None


# ---------------------------------------------------------------------------
# Public API (called from bot.py)
# ---------------------------------------------------------------------------

def is_active() -> bool:
    """Return True if a riddle is currently waiting to be answered."""
    return _active is not None


async def start_game(bot: BaseBot, user: User):
    """
    Start a new riddle game.
    Picks a random riddle and posts it to the room.
    """
    global _active

    # Only one game at a time
    if _active is not None:
        await bot.highrise.send_whisper(
            user.id,
            "🤔 A riddle is already out there! Type /answer to solve it."
        )
        return

    db.ensure_user(user.id, user.username)

    # Pick a random riddle
    _active = random.choice(_RIDDLES).copy()

    # Log the accepted answers to the console for testing — never shown in the room
    print(f"[RIDDLE] Accepted answers: {_active['answers']}")

    # Post publicly to the room
    await bot.highrise.chat(
        f"🤔 RIDDLE TIME!\n"
        f"{_active['riddle']}\n"
        f"Type /answer to win {config.RIDDLE_REWARD} coins! 🪙"
    )


async def handle_answer(bot: BaseBot, user: User, answer_text: str):
    """
    Check whether the player solved the riddle.
    Correct → award coins, end game, announce winner.
    Wrong   → private whisper.
    """
    global _active

    if _active is None:
        await bot.highrise.send_whisper(
            user.id, "No riddle is active right now. Type /riddle to start one!"
        )
        return

    db.ensure_user(user.id, user.username)

    # Use the shared flexible matcher — handles case, punctuation, and articles
    correct = check_answer(answer_text, _active["answers"])

    if correct:
        db.adjust_balance(user.id, config.RIDDLE_REWARD)
        db.record_game_win(user.id, user.username, "riddle")

        await bot.highrise.chat(
            f"🎉 @{user.username} cracked it! Answer: {_active['answers'][0]} "
            f"| +{config.RIDDLE_REWARD} coins 🪙"
        )

        _active = None
    else:
        await bot.highrise.send_whisper(user.id, "❌ Not it! Keep thinking. 💭")
