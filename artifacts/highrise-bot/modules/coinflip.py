"""
modules/coinflip.py
-------------------
Coin Flip mini-game for the Mini Game Bot.

How it works:
  1. A player types /coinflip <heads/tails> <bet amount>.
  2. The bot flips a coin randomly.
  3. If the player guessed right, they win the same amount they bet.
  4. If they guessed wrong, they lose the bet.
  5. The player must have enough coins to cover the bet.

Example:
  /coinflip heads 50   →  win 50 coins or lose 50 coins
"""

import random
from highrise import BaseBot, User
import database as db
import config
from modules.cooldowns import check_user_cooldown, set_user_cooldown
import modules.leveling as leveling
from modules.shop         import get_player_benefits
from modules.achievements import check_achievements


# Minimum and maximum bet amounts (feel free to tune in config.py later)
MIN_BET = 1
MAX_BET = 500


# ---------------------------------------------------------------------------
# Public API (called from bot.py)
# ---------------------------------------------------------------------------

async def handle_coinflip(bot: BaseBot, user: User, args: list[str]):
    """
    Process a /coinflip command.

    Expected format: /coinflip <heads|tails> <bet>
    args = ["coinflip", "heads", "50"]
    """
    db.ensure_user(user.id, user.username)

    # Per-user cooldown — prevents rapid-fire coinflips
    remaining = check_user_cooldown("coinflip", user.id, config.COINFLIP_COOLDOWN)
    if remaining is not None:
        await bot.highrise.send_whisper(
            user.id, f"⏳ Wait {remaining}s before flipping again."
        )
        return

    # ── Validate arguments ────────────────────────────────────────────────────

    # We need exactly 3 parts: the command name, the choice, and the bet amount
    if len(args) < 3:
        await bot.highrise.send_whisper(
            user.id, "Usage: /coinflip <heads/tails> <bet>\nExample: /coinflip heads 50"
        )
        return

    choice_raw = args[1].lower()
    bet_raw    = args[2]

    # Validate the choice
    if choice_raw not in ("heads", "tails"):
        await bot.highrise.send_whisper(
            user.id, "Choose heads or tails. Example: /coinflip heads 50"
        )
        return

    # Validate the bet is a number
    if not bet_raw.isdigit():
        await bot.highrise.send_whisper(
            user.id, "Your bet must be a whole number. Example: /coinflip tails 25"
        )
        return

    bet = int(bet_raw)

    # Validate bet is within allowed range
    if bet < MIN_BET:
        await bot.highrise.send_whisper(user.id, f"Minimum bet is {MIN_BET} coin.")
        return

    if bet > MAX_BET:
        await bot.highrise.send_whisper(user.id, f"Maximum bet is {MAX_BET} coins.")
        return

    # Validate the player has enough coins
    balance = db.get_balance(user.id)
    if balance < bet:
        await bot.highrise.send_whisper(
            user.id,
            f"💸 Not enough coins! You have {balance} but need {bet}. Try /daily!"
        )
        return

    # ── Flip the coin ─────────────────────────────────────────────────────────

    # random.choice picks "heads" or "tails" with equal probability
    result = random.choice(["heads", "tails"])
    won    = (result == choice_raw)

    # ── Apply the result ──────────────────────────────────────────────────────

    benefits    = get_player_benefits(user.id)
    bonus_coins = int(bet * benefits["coinflip_payout_pct"] / 100) if won else 0
    actual_win  = bet + bonus_coins

    if won:
        db.adjust_balance(user.id, actual_win)
        db.record_game_win(user.id, user.username, "coinflip")
        await leveling.award_xp(bot, user, config.XP_COINFLIP, actual_win)
        await check_achievements(bot, user, "coinflip_win")
        await check_achievements(bot, user, "game_win")
    else:
        db.adjust_balance(user.id, -bet)             # lose: remove the bet amount

    db.record_coinflip(
        user_id=user.id,
        username=user.username,
        choice=choice_raw,
        result=result,
        bet=bet,
        won=won,
    )
    set_user_cooldown("coinflip", user.id, reduction=benefits["cooldown_reduction"])

    new_balance = db.get_balance(user.id)

    # ── Announce the result publicly ─────────────────────────────────────────

    coin_emoji = "HEADS" if result == "heads" else "TAILS"

    display = db.get_display_name(user.id, user.username)
    if won:
        payout_str = f"+{actual_win} coins"
        if bonus_coins:
            payout_str += f" (+{bonus_coins} bonus)"
        await bot.highrise.chat(
            f"🪙 {display} chose {choice_raw.upper()} — {coin_emoji}! "
            f"WIN! {payout_str} 🎉  Balance: {new_balance}"
        )
    else:
        await bot.highrise.chat(
            f"🪙 {display} chose {choice_raw.upper()} — {coin_emoji}. "
            f"Lost {bet} coins 😬  Balance: {new_balance}"
        )
