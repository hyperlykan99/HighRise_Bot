"""
bot.py
------
Main entry point for the Highrise Mini Game Bot.

Responsibilities:
  - Connects to Highrise using BOT_TOKEN and ROOM_ID from environment variables
  - Initialises the SQLite database on startup
  - Listens for public chat messages and routes them to the right module

How to add a new module (e.g. blackjack):
  1. Create  modules/blackjack.py  with your game logic
  2. Import the handler function(s) here
  3. Add the command name(s) to the routing sets below
  4. Add a routing branch in on_chat()
"""

import asyncio
from highrise import BaseBot, User
from highrise.__main__ import BotDefinition, main as highrise_main

import database as db
import config

# Import all game modules
from modules.economy  import (handle_balance, handle_daily, handle_leaderboard,
                               handle_addcoins, handle_removecoins)
from modules.trivia   import start_game as start_trivia,   handle_answer as trivia_answer,   is_active as trivia_active
from modules.scramble import start_game as start_scramble, handle_answer as scramble_answer, is_active as scramble_active
from modules.riddle   import start_game as start_riddle,   handle_answer as riddle_answer,   is_active as riddle_active
from modules.coinflip import handle_coinflip

# Also import the module objects so /resetgame can clear their state
import modules.trivia   as trivia_module
import modules.scramble as scramble_module
import modules.riddle   as riddle_module


# ---------------------------------------------------------------------------
# Command routing sets
# Adding a command name here makes the bot recognise it.
# ---------------------------------------------------------------------------

# Commands any player can use
USER_COMMANDS  = {"help", "balance", "daily", "leaderboard",
                  "trivia", "scramble", "riddle", "answer", "coinflip"}

# Commands only admins (listed in config.ADMIN_USERS) can use
ADMIN_COMMANDS = {"addcoins", "removecoins", "resetgame", "announce"}


# ---------------------------------------------------------------------------
# Help text
# Split into two whispers to stay within Highrise's message size limit.
# ---------------------------------------------------------------------------

HELP_TEXT_1 = (
    "-- Mini Game Bot --\n"
    "/trivia   - answer a trivia question, win 25 coins\n"
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
    Main bot class. Inherits from Highrise's BaseBot and overrides event hooks.
    """

    async def on_start(self, session_metadata) -> None:
        """Called once when the bot successfully connects to the room."""
        db.init_db()
        print(f"[Bot] Connected to room {config.ROOM_ID}")
        await self.highrise.chat("Mini Game Bot is online! Type /help to see commands.")

    async def on_chat(self, user: User, message: str) -> None:
        """
        Called for every public chat message in the room.
        Only acts on messages that start with '/'.
        """
        message = message.strip()

        # Ignore messages that aren't commands
        if not message.startswith("/"):
            return

        # Split "/request Blinding Lights" into ["request", "Blinding", "Lights"]
        parts = message[1:].split()
        if not parts:
            return

        cmd  = parts[0].lower()   # the command name in lowercase
        args = parts              # full args list including the command name

        # ── /help ────────────────────────────────────────────────────────────
        # Sent as two whispers to stay within Highrise's character limit
        if cmd == "help":
            await self.highrise.send_whisper(user.id, HELP_TEXT_1)
            await self.highrise.send_whisper(user.id, HELP_TEXT_2)
            return

        # ── Admin commands ───────────────────────────────────────────────────
        if cmd in ADMIN_COMMANDS:
            # Check the username against the admin list (case-insensitive)
            if user.username.lower() not in config.ADMIN_USERS:
                await self.highrise.send_whisper(user.id, "That command is for admins only.")
                return

            await self._handle_admin(user, cmd, args)
            return

        # ── Economy commands ─────────────────────────────────────────────────
        if cmd == "balance":
            await handle_balance(self, user)

        elif cmd == "daily":
            await handle_daily(self, user)

        elif cmd == "leaderboard":
            await handle_leaderboard(self, user)

        # ── Game start commands ──────────────────────────────────────────────
        elif cmd == "trivia":
            await start_trivia(self, user)

        elif cmd == "scramble":
            await start_scramble(self, user)

        elif cmd == "riddle":
            await start_riddle(self, user)

        # ── /answer — routed to whichever game is currently active ────────────
        elif cmd == "answer":
            await self._handle_answer(user, args)

        # ── /coinflip ────────────────────────────────────────────────────────
        elif cmd == "coinflip":
            await handle_coinflip(self, user, args)

        # ── Unknown command ───────────────────────────────────────────────────
        elif cmd in USER_COMMANDS:
            pass   # already handled above; this branch is a safety fallback

        else:
            await self.highrise.send_whisper(
                user.id, "Unknown command. Type /help to see all commands."
            )

    async def on_user_join(self, user: User, position) -> None:
        """
        Called when a player enters the room.
        Register them in the database and send a welcome message.
        """
        db.ensure_user(user.id, user.username)
        await self.highrise.chat(
            f"Welcome, @{user.username}! Type /help to see what you can do. "
            "Use /daily to get your free coins!"
        )

    async def on_user_leave(self, user: User) -> None:
        """Called when a player leaves the room."""
        print(f"[Bot] {user.username} left the room.")

    # -------------------------------------------------------------------------
    # Private routing helpers
    # -------------------------------------------------------------------------

    async def _handle_answer(self, user: User, args: list[str]):
        """
        Route an /answer command to whichever mini-game is currently active.
        If no game is active, whisper a helpful message.
        """
        # Combine everything after "/answer" into one string
        answer_text = " ".join(args[1:]).strip()

        if not answer_text:
            await self.highrise.send_whisper(
                user.id, "Usage: /answer <your answer>"
            )
            return

        # Check each game in order — only one should be active at a time
        if trivia_active():
            await trivia_answer(self, user, answer_text)
        elif scramble_active():
            await scramble_answer(self, user, answer_text)
        elif riddle_active():
            await riddle_answer(self, user, answer_text)
        else:
            await self.highrise.send_whisper(
                user.id,
                "No game is active right now! Try /trivia, /scramble, or /riddle."
            )

    async def _handle_admin(self, user: User, cmd: str, args: list[str]):
        """Route admin-only commands."""

        if cmd == "addcoins":
            await handle_addcoins(self, user, args)

        elif cmd == "removecoins":
            await handle_removecoins(self, user, args)

        elif cmd == "resetgame":
            # Clear any active mini-game (useful if a game got stuck)
            trivia_module._active  = None
            scramble_module._active = None
            riddle_module._active   = None
            await self.highrise.chat("[Admin] All active games have been reset.")

        elif cmd == "announce":
            # Post a custom message to the room on behalf of the bot
            message_text = " ".join(args[1:]).strip()
            if not message_text:
                await self.highrise.send_whisper(user.id, "Usage: /announce <message>")
                return
            await self.highrise.chat(f"[Announcement] {message_text}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run():
    """Start the bot using the Highrise SDK."""
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
