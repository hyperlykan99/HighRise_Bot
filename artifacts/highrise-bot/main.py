"""
main.py
-------
HangoutBot — all-in-one Highrise Mini Game Bot.

This is the entry point for the current single-bot setup.
When you're ready to split into separate bots, create a new entry point
file for each mode (e.g. game_bot.py, dj_bot.py, blackjack_bot.py) and
import from the shared root modules:

    from economy import handle_balance, handle_daily, handle_leaderboard
    from games   import handle_game_command, handle_answer
    from admin   import handle_admin_command
    import database as db
    import config

All bots share the same highrise_hangout.db database, so player coins,
stats, and daily rewards carry over automatically.

─────────────────────────────────────────────────────────────────────────────
Future bot layout (example):
─────────────────────────────────────────────────────────────────────────────
  game_bot.py         ← imports economy, games, admin
  dj_bot.py           ← imports economy, modules/dj.py, admin
  blackjack_bot.py    ← imports economy, modules/blackjack.py, admin
  host_bot.py         ← imports economy, admin, custom host logic
─────────────────────────────────────────────────────────────────────────────
"""

import asyncio
from highrise import BaseBot, User
from highrise.__main__ import BotDefinition, main as highrise_main

import database as db
import config

# Shared root-level modules (reusable by any future bot)
from economy import handle_balance, handle_daily, handle_leaderboard
from games   import handle_game_command, handle_answer as games_handle_answer
from admin   import handle_admin_command


# ---------------------------------------------------------------------------
# Command sets
# Adding a name here makes the bot recognise it in on_chat().
# ---------------------------------------------------------------------------

# Commands any player can use
ECONOMY_COMMANDS = {"balance", "daily", "leaderboard"}
GAME_COMMANDS    = {"trivia", "scramble", "riddle", "coinflip"}

# /answer is handled separately (routes to whichever game is active)

# Commands only players in config.ADMIN_USERS can use
ADMIN_COMMANDS = {"addcoins", "removecoins", "resetgame", "announce"}

ALL_KNOWN_COMMANDS = (
    {"help", "answer"} | ECONOMY_COMMANDS | GAME_COMMANDS | ADMIN_COMMANDS
)


# ---------------------------------------------------------------------------
# Help text — split into two messages to stay inside Highrise's size limit
# ---------------------------------------------------------------------------

HELP_TEXT_1 = (
    "-- Mini Game Bot --\n"
    "/trivia   - trivia question, win 25 coins\n"
    "/scramble - unscramble a word, win 25 coins\n"
    "/riddle   - solve a riddle, win 25 coins\n"
    "/coinflip <heads/tails> <bet> - flip a coin\n"
    "/answer <text> - answer the active game"
)

HELP_TEXT_2 = (
    "-- Economy --\n"
    f"/daily       - claim {config.DAILY_REWARD} free coins (once/day)\n"
    "/balance     - check your coins\n"
    "/leaderboard - top 10 richest players\n"
    "-- Admin --\n"
    "/addcoins <user> <amount>\n"
    "/removecoins <user> <amount>\n"
    "/resetgame  /announce <message>"
)


# ---------------------------------------------------------------------------
# Bot class
# ---------------------------------------------------------------------------

class HangoutBot(BaseBot):
    """
    Main bot class for the all-in-one HangoutBot.
    Inherits from Highrise's BaseBot and overrides event hooks.
    """

    async def on_start(self, session_metadata) -> None:
        """Called once when the bot successfully connects to the room."""
        db.init_db()
        print(f"[HangoutBot] Connected — room {config.ROOM_ID} | DB: {config.DB_PATH}")
        await self.highrise.chat("Mini Game Bot is online! Type /help for commands.")

    async def on_chat(self, user: User, message: str) -> None:
        """
        Called for every public chat message.
        Ignores anything that doesn't start with '/'.
        """
        message = message.strip()
        if not message.startswith("/"):
            return

        # Parse "/coinflip heads 50" → cmd="coinflip", args=["coinflip","heads","50"]
        parts = message[1:].split()
        if not parts:
            return

        cmd  = parts[0].lower()
        args = parts

        # ── /help ─────────────────────────────────────────────────────────────
        if cmd == "help":
            await self.highrise.send_whisper(user.id, HELP_TEXT_1)
            await self.highrise.send_whisper(user.id, HELP_TEXT_2)
            return

        # ── Admin gate ────────────────────────────────────────────────────────
        if cmd in ADMIN_COMMANDS:
            if user.username.lower() not in config.ADMIN_USERS:
                await self.highrise.send_whisper(user.id, "That command is for admins only.")
                return
            await handle_admin_command(self, user, cmd, args)
            return

        # ── Economy commands ──────────────────────────────────────────────────
        if cmd == "balance":
            await handle_balance(self, user)

        elif cmd == "daily":
            await handle_daily(self, user)

        elif cmd == "leaderboard":
            await handle_leaderboard(self, user)

        # ── /answer ───────────────────────────────────────────────────────────
        elif cmd == "answer":
            answer_text = " ".join(args[1:]).strip()
            if not answer_text:
                await self.highrise.send_whisper(user.id, "Usage: /answer <your answer>")
                return
            await games_handle_answer(self, user, answer_text)

        # ── Game commands ─────────────────────────────────────────────────────
        elif cmd in GAME_COMMANDS:
            await handle_game_command(self, user, cmd, args)

        # ── Unknown command ───────────────────────────────────────────────────
        else:
            await self.highrise.send_whisper(
                user.id, "Unknown command. Type /help to see all commands."
            )

    async def on_user_join(self, user: User, position) -> None:
        """Register new players and greet them when they enter the room."""
        db.ensure_user(user.id, user.username)
        await self.highrise.chat(
            f"Welcome, @{user.username}! Type /help to see what you can do. "
            "Use /daily to grab your free coins!"
        )

    async def on_user_leave(self, user: User) -> None:
        """Log when a player leaves."""
        print(f"[HangoutBot] {user.username} left.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run():
    """Connect the bot to Highrise and start the event loop."""
    asyncio.run(
        highrise_main(
            [BotDefinition(
                bot=HangoutBot(),
                room_id=config.ROOM_ID,
                api_token=config.BOT_TOKEN,
            )]
        )
    )


if __name__ == "__main__":
    run()
