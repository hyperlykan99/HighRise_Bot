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
from modules.cooldowns import check_room_cooldown, set_room_cooldown
import modules.leveling as leveling
from modules.shop         import get_player_benefits
from modules.achievements import check_achievements
from modules.quests       import track_quest


# ---------------------------------------------------------------------------
# Word bank
# Words should be common enough that players can unscramble them,
# but not so short that scrambling makes them trivially easy.
# ---------------------------------------------------------------------------

_WORDS = [
    # Animals
    "tiger",   "shark",   "horse",   "eagle",   "panda",
    "snake",   "whale",   "camel",   "koala",   "zebra",
    "parrot",  "rabbit",  "monkey",  "turtle",  "spider",
    # Food & Drink
    "pizza",   "mango",   "bread",   "grape",   "lemon",
    "apple",   "bacon",   "sushi",   "pasta",   "tacos",
    "coffee",  "butter",  "cheese",  "orange",  "melon",
    # Nature
    "cloud",   "ocean",   "storm",   "river",   "flame",
    "frost",   "bloom",   "jungle",  "forest",  "desert",
    "island",  "valley",  "canyon",  "breeze",
    # Objects / Vibes
    "music",   "dance",   "magic",   "dream",   "smile",
    "brave",   "castle",  "dragon",  "rocket",  "puzzle",
    "candle",  "mirror",  "bridge",  "planet",  "lantern",
    # Colors / Qualities
    "purple",  "silver",  "golden",  "shadow",  "crystal",
    # Fun / Pop Culture
    "anime",   "pixel",   "disco",   "remix",   "squad",
    "quest",   "level",   "bonus",   "spell",   "cheat",
    "swipe",   "theme",   "gamer",   "badge",   "emoji",
    # Longer Words
    "garden",  "winter",  "summer",  "spring",  "marble",
    "thunder", "balloon", "diamond", "blanket", "captain",
    "rainbow", "mystery", "weekend", "kitchen", "freedom",
    "pattern", "shelter", "tornado", "unicorn", "champion",
    "falcon",  "compass", "pilgrim", "sunrise", "harbor",
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
            "🔀 Scramble is already going! Type /answer to guess."
        )
        return

    # Room-wide cooldown — prevents back-to-back games
    remaining = check_room_cooldown("scramble", config.SCRAMBLE_COOLDOWN)
    if remaining is not None:
        await bot.highrise.send_whisper(
            user.id, f"⏳ Scramble on cooldown! Try again in {remaining}s."
        )
        return

    db.ensure_user(user.id, user.username)

    # Pick and scramble a word
    word      = random.choice(_WORDS)
    scrambled = _scramble_word(word)
    _active   = {"word": word, "scrambled": scrambled}
    set_room_cooldown("scramble")    # start the 30 s gap

    # Log the answer to the console for testing — never shown in the room
    print(f"[SCRAMBLE] Correct answer: {_active['word']}")

    # Post it publicly
    await bot.highrise.chat(
        f"🔀 WORD SCRAMBLE!\n"
        f"Unscramble this: {scrambled.upper()}\n"
        f"Type /answer to win {db.get_economy_settings()['scramble_reward']} coins! 🪙"
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
        # Compute reward with any equipped cosmetic bonuses
        benefits      = get_player_benefits(user.id)
        base_reward   = db.get_economy_settings()["scramble_reward"]
        actual_reward = (
            base_reward
            + int(base_reward * benefits["game_reward_pct"] / 100)
            + benefits["scramble_bonus"]
        )

        actual_reward = db.adjust_balance_capped(user.id, actual_reward)
        db.record_game_win(user.id, user.username, "scramble")
        track_quest(user.id, "game_win")
        track_quest(user.id, "earn_coins", actual_reward)
        if db.is_event_active():
            db.add_event_points(user.id, 1)
        await leveling.award_xp(bot, user, config.XP_SCRAMBLE, actual_reward)
        await check_achievements(bot, user, "scramble_win")
        await check_achievements(bot, user, "game_win")

        display = db.get_display_name(user.id, user.username)
        await bot.highrise.chat(
            f"🎉 {display} got it! "
            f"{_active['scrambled'].upper()} = {_active['word'].upper()} "
            f"| +{actual_reward} coins 🪙"
        )

        _active = None
    else:
        await bot.highrise.send_whisper(user.id, "❌ Nope! Keep unscrambling.")
