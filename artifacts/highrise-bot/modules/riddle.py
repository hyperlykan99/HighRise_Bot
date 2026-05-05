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
from modules.cooldowns import check_room_cooldown, set_room_cooldown
import modules.leveling as leveling
from modules.shop import get_player_benefits


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
        "answers": ["keyboard", "a keyboard"],
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
        "riddle":  "I am light as a feather, yet even the strongest person cannot hold me for more than a few minutes. What am I?",
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
        "riddle":  "What has four legs in the morning, two at noon, and three in the evening?",
        "answers": ["human", "a human", "man", "a man", "person"],
    },
    {
        "riddle":  "What can travel around the world while staying in a corner?",
        "answers": ["stamp", "a stamp"],
    },
    {
        "riddle":  "What goes up and down but never moves?",
        "answers": ["stairs", "a staircase"],
    },
    {
        "riddle":  "What comes down but never goes up?",
        "answers": ["rain"],
    },
    {
        "riddle":  "What gets bigger the more you take away from it?",
        "answers": ["hole", "a hole"],
    },
    {
        "riddle":  "I am full of holes but I can still hold water. What am I?",
        "answers": ["sponge", "a sponge"],
    },
    {
        "riddle":  "What word becomes shorter when you add two letters to it?",
        "answers": ["short"],
    },
    {
        "riddle":  "What invention lets you look right through a wall?",
        "answers": ["window", "a window"],
    },
    {
        "riddle":  "What is orange and sounds like a parrot?",
        "answers": ["carrot", "a carrot"],
    },
    {
        "riddle":  "What is so fragile that just saying its name breaks it?",
        "answers": ["silence"],
    },
    {
        "riddle":  "What has a thumb and four fingers but is not alive?",
        "answers": ["glove", "a glove"],
    },
    {
        "riddle":  "I have a ring but no finger. I have a screen but no windows. What am I?",
        "answers": ["phone", "a phone", "mobile", "mobile phone"],
    },
    {
        "riddle":  "What do you call a bear with no teeth?",
        "answers": ["gummy bear", "a gummy bear"],
    },
    {
        "riddle":  "I can be cracked, made, told, and played. What am I?",
        "answers": ["joke", "a joke"],
    },
    {
        "riddle":  "What has ears but cannot hear?",
        "answers": ["corn", "an ear of corn"],
    },
    {
        "riddle":  "What kind of room has no walls, floor, or ceiling?",
        "answers": ["mushroom", "a mushroom"],
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

    # Room-wide cooldown — prevents back-to-back games
    remaining = check_room_cooldown("riddle", config.RIDDLE_COOLDOWN)
    if remaining is not None:
        await bot.highrise.send_whisper(
            user.id, f"⏳ Riddle on cooldown! Try again in {remaining}s."
        )
        return

    db.ensure_user(user.id, user.username)

    # Pick a random riddle
    _active = random.choice(_RIDDLES).copy()
    set_room_cooldown("riddle")      # start the 30 s gap

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
        # Compute reward with any equipped cosmetic bonuses
        benefits      = get_player_benefits(user.id)
        actual_reward = (
            config.RIDDLE_REWARD
            + int(config.RIDDLE_REWARD * benefits["game_reward_pct"] / 100)
            + benefits["riddle_bonus"]
        )

        db.adjust_balance(user.id, actual_reward)
        db.record_game_win(user.id, user.username, "riddle")
        await leveling.award_xp(bot, user, config.XP_RIDDLE, actual_reward)

        display = db.get_display_name(user.id, user.username)
        await bot.highrise.chat(
            f"🎉 {display} cracked it! Answer: {_active['answers'][0]} "
            f"| +{actual_reward} coins 🪙"
        )

        _active = None
    else:
        await bot.highrise.send_whisper(user.id, "❌ Not it! Keep thinking. 💭")
