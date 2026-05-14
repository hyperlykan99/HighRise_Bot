"""
modules/economy_settings.py
----------------------------
Economy protection settings commands.

Commands:
  /economysettings           — view all economy settings (mod+)
  /setdailycoins <amount>    — set daily coin reward (admin+)
  /setgamereward <game> <n>  — set trivia/scramble/riddle reward (admin+)
  /setmaxbalance <amount>    — set per-player max balance cap (owner only)
  /settransferfee <pct>      — set bank transfer fee % (admin+)

All outgoing messages are capped at 249 characters.
"""

from highrise import BaseBot, User

import database as db
from modules.permissions import can_moderate, can_manage_economy, is_owner

VALID_GAMES = {"trivia", "scramble", "riddle"}


async def _w(bot: BaseBot, uid: str, msg: str):
    await bot.highrise.send_whisper(uid, msg[:249])


# ---------------------------------------------------------------------------
# /economysettings  — view (mod+)
# ---------------------------------------------------------------------------

async def handle_economysettings(bot: BaseBot, user: User):
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return

    s   = db.get_economy_settings()
    fee = db.get_bank_setting("send_tax_percent")
    msg = (
        f"⚙️ Economy Settings\n"
        f"Daily: {s['daily_coins']:,} 🪙 | Fee: {fee}%\n"
        f"Trivia: {s['trivia_reward']:,} 🪙  "
        f"Scramble: {s['scramble_reward']:,} 🪙  "
        f"Riddle: {s['riddle_reward']:,} 🪙\n"
        f"Max Balance: {s['max_balance']:,} 🪙"
    )
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# /setdailycoins <amount>  — admin+
# ---------------------------------------------------------------------------

async def handle_setdailycoins(bot: BaseBot, user: User, args: list[str]):
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin+ only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !setdailycoins <1-10000>")
        return
    amount = int(args[1])
    if not (1 <= amount <= 10000):
        await _w(bot, user.id, "Amount must be 1–10,000.")
        return
    db.set_economy_setting("daily_coins", str(amount))
    await _w(bot, user.id, f"✅ Daily coins set to {amount:,} 🪙.")


# ---------------------------------------------------------------------------
# /setgamereward <trivia|scramble|riddle> <amount>  — admin+
# ---------------------------------------------------------------------------

async def handle_setgamereward(bot: BaseBot, user: User, args: list[str]):
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin+ only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !setgamereward <trivia|scramble|riddle> <amount>")
        return
    game = args[1].lower()
    if game not in VALID_GAMES:
        await _w(bot, user.id, "Game must be: trivia, scramble, or riddle.")
        return
    if not args[2].isdigit():
        await _w(bot, user.id, "Amount must be a whole number.")
        return
    amount = int(args[2])
    if not (1 <= amount <= 10000):
        await _w(bot, user.id, "Amount must be 1–10,000.")
        return
    db.set_economy_setting(f"{game}_reward", str(amount))
    await _w(bot, user.id, f"✅ {game.capitalize()} reward set to {amount:,} 🪙.")


# ---------------------------------------------------------------------------
# /setmaxbalance <amount>  — owner only
# ---------------------------------------------------------------------------

async def handle_setmaxbalance(bot: BaseBot, user: User, args: list[str]):
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !setmaxbalance <10000-1000000000>")
        return
    amount = int(args[1])
    if not (10_000 <= amount <= 1_000_000_000):
        await _w(bot, user.id, "Must be between 10,000 and 1,000,000,000.")
        return
    db.set_economy_setting("max_balance", str(amount))
    await _w(bot, user.id, f"✅ Max balance set to {amount:,} 🪙.")


# ---------------------------------------------------------------------------
# /settransferfee <percent>  — admin+
# ---------------------------------------------------------------------------

async def handle_settransferfee(bot: BaseBot, user: User, args: list[str]):
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin+ only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !settransferfee <0-50>")
        return
    pct = int(args[1])
    if not (0 <= pct <= 50):
        await _w(bot, user.id, "Fee must be 0–50%.")
        return
    db.set_bank_setting("send_tax_percent", str(pct))
    await _w(bot, user.id, f"✅ Transfer fee set to {pct}%.")
