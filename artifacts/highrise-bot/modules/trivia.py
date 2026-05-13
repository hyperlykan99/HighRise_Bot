"""
modules/trivia.py
-----------------
Trivia mini-game for the Mini Game Bot.

How it works:
  1. Any player types /trivia to start a question (public room announcement).
  2. The bot posts the question to the room so everyone can see it.
  3. Players answer with !answer <their answer>.
  4. The first player to type the correct answer wins TRIVIA_REWARD coins.
  5. Only one trivia question can be active at a time.
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
# Question bank
# Each question has a "question" string and a list of "answers".
# Multiple answers are accepted (e.g. short and long form both work).
# All comparisons are case-insensitive.
# ---------------------------------------------------------------------------

_QUESTIONS = [
    # ── Social Media & Internet ────────────────────────────────────────────────
    {"question": "Which app has a ghost as its logo?",                           "answers": ["snapchat"]},
    {"question": "Which app is famous for short dance videos?",                  "answers": ["tiktok", "tik tok"]},
    {"question": "What does LOL stand for?",                                     "answers": ["laugh out loud"]},
    {"question": "What does BRB stand for?",                                     "answers": ["be right back"]},
    {"question": "What does GG mean in gaming?",                                 "answers": ["good game"]},
    {"question": "What does AFK mean?",                                          "answers": ["away from keyboard"]},
    {"question": "What does OMG stand for?",                                     "answers": ["oh my god", "oh my gosh"]},
    {"question": "What platform do gamers mostly use to livestream?",            "answers": ["twitch"]},

    # ── Pop Music ─────────────────────────────────────────────────────────────
    {"question": "Which band performed Bohemian Rhapsody?",                      "answers": ["queen"]},
    {"question": "Who is known as the King of Pop?",                             "answers": ["michael jackson", "mj"]},
    {"question": "Which artist is known for the song Shape of You?",             "answers": ["ed sheeran"]},
    {"question": "Which country does K-pop come from?",                          "answers": ["south korea", "korea"]},
    {"question": "Which singer is nicknamed Queen Bey?",                         "answers": ["beyonce"]},
    {"question": "What K-pop group has members RM, Jimin, and Jungkook?",        "answers": ["bts"]},
    {"question": "What does DJ stand for?",                                      "answers": ["disc jockey", "disk jockey"]},
    {"question": "Which singer is known for wearing wild outfits and a meat dress?",  "answers": ["lady gaga"]},

    # ── Movies & TV ───────────────────────────────────────────────────────────
    {"question": "What animated film features the lion cub Simba?",             "answers": ["lion king", "the lion king"]},
    {"question": "What is the name of Harry Potters pet owl?",                   "answers": ["hedwig"]},
    {"question": "What color is Darth Vaders lightsaber?",                       "answers": ["red"]},
    {"question": "What Netflix show features kids and a creature called the Demogorgon?",  "answers": ["stranger things"]},
    {"question": "Who played Iron Man in the Marvel movies?",                    "answers": ["robert downey jr", "robert downey"]},
    {"question": "What Disney princess has super long magical hair?",            "answers": ["rapunzel"]},
    {"question": "What is the highest-grossing movie of all time?",              "answers": ["avatar"]},
    {"question": "What Disney movie features a mermaid princess?",               "answers": ["the little mermaid", "little mermaid"]},

    # ── Gaming ────────────────────────────────────────────────────────────────
    {"question": "What is the best-selling video game of all time?",             "answers": ["minecraft"]},
    {"question": "What is the name of Marios turtle villain?",                   "answers": ["bowser"]},
    {"question": "What color is Sonic the Hedgehog?",                            "answers": ["blue"]},
    {"question": "What is the first Pokemon in the Pokedex?",                    "answers": ["bulbasaur"]},
    {"question": "In Among Us, what is the role that secretly eliminates everyone?",  "answers": ["imposter", "impostor", "the imposter"]},
    {"question": "What is the name of the princess Mario usually rescues?",      "answers": ["peach", "princess peach"]},
    {"question": "What superhero wears a red and blue suit and shoots webs?",    "answers": ["spiderman", "spider-man", "spider man"]},
    {"question": "What color is the Incredible Hulk?",                           "answers": ["green"]},

    # ── Food & Cravings ───────────────────────────────────────────────────────
    {"question": "What is the main ingredient in guacamole?",                    "answers": ["avocado"]},
    {"question": "What country is sushi originally from?",                       "answers": ["japan"]},
    {"question": "What snack do people usually eat at the movies?",              "answers": ["popcorn"]},
    {"question": "What food is traditionally eaten on a birthday?",              "answers": ["cake", "birthday cake"]},
    {"question": "What yellow fruit is famous for being a monkeys favorite?",    "answers": ["banana"]},
    {"question": "What is the most popular fast food chain in the world?",       "answers": ["mcdonalds", "mcdonald's"]},
    {"question": "What drink comes in a red can and starts with C?",             "answers": ["coke", "coca cola", "coca-cola"]},
    {"question": "What is the most popular pizza topping worldwide?",            "answers": ["cheese", "pepperoni"]},

    # ── Animals ───────────────────────────────────────────────────────────────
    {"question": "What do you call a baby dog?",                                 "answers": ["puppy"]},
    {"question": "What do you call a baby cat?",                                 "answers": ["kitten"]},
    {"question": "What animal is known as mans best friend?",                    "answers": ["dog"]},
    {"question": "What sound does a cat make?",                                  "answers": ["meow"]},
    {"question": "What is the largest land animal?",                             "answers": ["elephant"]},

    # ── Jokes & Silly ─────────────────────────────────────────────────────────
    {"question": "What do you call cheese that isnt yours?",                     "answers": ["nacho cheese"]},
    {"question": "What do you call a sleeping dinosaur?",                        "answers": ["dinosnore", "a dinosnore"]},
    {"question": "What is brown and sticky?",                                    "answers": ["a stick", "stick"]},
    {"question": "If you have 3 apples and I take 2, how many do I have?",       "answers": ["2", "two"]},
    {"question": "What do you call it when you take a photo of yourself?",       "answers": ["selfie"]},
    {"question": "What is the most watched YouTube video of all time?",          "answers": ["baby shark"]},
    {"question": "What planet is known as the Red Planet?",                      "answers": ["mars"]},
    {"question": "What is the hardest natural substance on Earth?",              "answers": ["diamond"]},

    # ── Sports & Quick Facts ──────────────────────────────────────────────────
    {"question": "How many players are on a soccer team on the field?",          "answers": ["11", "eleven"]},
    {"question": "In which sport would you perform a slam dunk?",                "answers": ["basketball"]},
    {"question": "How many rings are on the Olympic flag?",                      "answers": ["5", "five"]},
    {"question": "What sport has players scoring love and deuce?",               "answers": ["tennis"]},
    {"question": "How many colors are in a rainbow?",                            "answers": ["7", "seven"]},
    {"question": "How many days are in a week?",                                 "answers": ["7", "seven"]},
    {"question": "How many months are in a year?",                               "answers": ["12", "twelve"]},
    {"question": "How many sides does a stop sign have?",                        "answers": ["8", "eight"]},
    {"question": "What planet has rings around it?",                             "answers": ["saturn"]},
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


def get_current_answer() -> str | None:
    """Return the first accepted answer for the active question, or None."""
    if _active is None:
        return None
    return _active["answers"][0]


async def _post_trivia(bot: BaseBot) -> None:
    """Post the active trivia question to the room."""
    settings = db.get_auto_game_settings()
    timer    = settings["game_answer_timer"]
    reward   = db.get_economy_settings()["trivia_reward"]
    await bot.highrise.chat(
        f"🎯 TRIVIA TIME! ({timer}s)\n"
        f"{_active['question']}\n"
        f"Type !answer to win {reward} coins! 🪙"
    )


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
            "❓ Trivia is already going! Type !answer to play."
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
    await _post_trivia(bot)


async def auto_start(bot: BaseBot) -> None:
    """Start trivia automatically (no user, no cooldown check)."""
    global _active
    if _active is not None:
        return
    question_data = random.choice(_QUESTIONS)
    _active = question_data.copy()
    set_room_cooldown("trivia")
    print(f"[TRIVIA][AUTO] Correct answer: {_active['answers'][0]}")
    await _post_trivia(bot)


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
            user.id, "No trivia question is active right now. Type !trivia to start one!"
        )
        return

    db.ensure_user(user.id, user.username)

    # Use the shared flexible matcher — handles case, punctuation, and articles
    correct = check_answer(answer_text, _active["answers"])

    if correct:
        # ── Compute intended reward ───────────────────────────────────────────
        from modules.events import get_event_effect
        benefits     = get_player_benefits(user.id)
        base_reward  = db.get_economy_settings()["trivia_reward"]
        cosm_bonus   = (
            int(base_reward * benefits["game_reward_pct"] / 100)
            + benefits["trivia_bonus"]
        )
        intended = base_reward + cosm_bonus

        _ev = get_event_effect()
        intended = int(intended * _ev["coins"])
        if _ev["trivia_coins_pct"] > 0:
            intended = int(intended * (1.0 + _ev["trivia_coins_pct"]))
        xp_amount = int(config.XP_TRIVIA * _ev["xp"])

        # ── Credit coins (reward caps removed in 3.1G) ───────────────────────
        db.adjust_balance(user.id, intended)
        credited = intended

        # ── Console logging ───────────────────────────────────────────────────
        print(
            f"[TRIVIA] @{user.username} reward:"
            f" base={base_reward}"
            f" cosm_bonus={cosm_bonus}"
            f" event_coins_mult={_ev['coins']}"
            f" event_tp_pct={_ev['trivia_coins_pct']}"
            f" intended={intended}"
            f" credited={credited}"
            f" xp={xp_amount}"
        )

        db.record_game_win(user.id, user.username, "trivia")
        track_quest(user.id, "game_win")
        track_quest(user.id, "earn_coins", credited)
        try:
            from modules.missions import track_mission
            track_mission(user.id, user.username, "trivia")
        except Exception:
            pass
        if db.is_event_active():
            db.add_event_points(user.id, 1)
        await leveling.award_xp(bot, user, xp_amount, credited)
        await check_achievements(bot, user, "trivia_win")
        await check_achievements(bot, user, "game_win")

        # ── Announce win ──────────────────────────────────────────────────────
        display   = db.get_display_name(user.id, user.username)
        xp_tag    = " Double XP!" if _ev["xp"] >= 2 else ""
        event_tag = xp_tag

        await bot.highrise.chat(
            f"🎉 {display} got it! Answer: {_active['answers'][0]} "
            f"| +{credited}c +{xp_amount}XP{event_tag}"
        )

        _active = None
    else:
        # Wrong answer — whisper privately so we don't flood the room
        await bot.highrise.send_whisper(user.id, "❌ Not quite! Keep guessing.")
