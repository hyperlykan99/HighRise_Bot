"""
bot.py
------
Main bot entry point.

Wires everything together:
  - Connects to Highrise using BOT_TOKEN and ROOM_ID from environment variables
  - Initialises the SQLite database
  - Listens for chat messages and routes them to the correct module

How to add a new module (e.g. trivia):
  1. Create  modules/trivia.py  with a handle_trivia_command() function
  2. Import it here
  3. Add its command names to the sets below
  4. Add a routing branch in on_chat()
"""

import asyncio
from highrise import BaseBot, User
from highrise.__main__ import BotDefinition, main as highrise_main

import database as db
import config

from modules.dj import handle_dj_command, handle_dj_admin_command
from modules.economy import handle_economy_command, handle_economy_admin_command

# ---------------------------------------------------------------------------
# Command routing tables
# ---------------------------------------------------------------------------

DJ_COMMANDS            = {"dj", "request", "priority", "queue", "now", "skipvote"}
ECONOMY_COMMANDS       = {"balance", "daily"}
ADMIN_DJ_COMMANDS      = {"skip", "remove", "clearqueue"}
ADMIN_ECONOMY_COMMANDS = {"addtokens", "refund"}

# Help text is split into two messages to stay within Highrise's character limit.
HELP_TEXT_1 = (
    "-- DJ System --\n"
    f"/request <song>  - {config.SONG_REQUEST_COST} tokens, adds to queue\n"
    f"/priority <song> - {config.PRIORITY_REQUEST_COST} tokens, jumps to #2\n"
    "/queue - next 5 songs\n"
    "/now - current song\n"
    "/skipvote - vote to skip"
)

HELP_TEXT_2 = (
    "-- Tokens --\n"
    "/balance - your balance\n"
    f"/daily - claim {config.DAILY_REWARD} free tokens (once/day)\n"
    "-- Admin --\n"
    "/skip  /remove <#>  /clearqueue\n"
    "/addtokens <user> <amt>\n"
    "/refund <user> <amt>"
)


class HangoutBot(BaseBot):
    """
    Main bot class.
    Inherits from BaseBot and overrides event hooks.
    """

    async def on_start(self, session_metadata) -> None:
        """Called once when the bot successfully connects to the room."""
        db.init_db()
        print(f"[Bot] Connected to room {config.ROOM_ID}")
        await self.highrise.chat("Bot is online! Type /help to see commands.")

    async def on_chat(self, user: User, message: str) -> None:
        """
        Called for every public chat message.
        Only acts on messages that start with '/'.
        """
        message = message.strip()

        if not message.startswith("/"):
            return

        # e.g. "/request Blinding Lights" → ["request", "Blinding", "Lights"]
        parts = message[1:].split()
        if not parts:
            return

        cmd  = parts[0].lower()
        args = parts

        # /help — two whispers to stay within Highrise's message size limit
        if cmd == "help":
            await self.highrise.send_whisper(user.id, HELP_TEXT_1)
            await self.highrise.send_whisper(user.id, HELP_TEXT_2)
            return

        # Admin-only commands — check username (case-insensitive)
        if cmd in ADMIN_DJ_COMMANDS or cmd in ADMIN_ECONOMY_COMMANDS:
            if user.username.lower() not in config.ADMIN_USERS:
                await self.highrise.send_whisper(user.id, "That command is for admins only.")
                return

            if cmd in ADMIN_DJ_COMMANDS:
                await handle_dj_admin_command(self, user, args)
            else:
                await handle_economy_admin_command(self, user, args)
            return

        # Public commands
        if cmd in DJ_COMMANDS:
            await handle_dj_command(self, user, args)

        elif cmd in ECONOMY_COMMANDS:
            await handle_economy_command(self, user, args)

        else:
            await self.highrise.send_whisper(
                user.id, "Unknown command. Type /help to see all commands."
            )

    async def on_user_join(self, user: User, position) -> None:
        """Register new users and greet them."""
        db.ensure_user(user.id, user.username)
        await self.highrise.chat(f"Welcome, @{user.username}! Type /help to see commands.")

    async def on_user_leave(self, user: User) -> None:
        """Log when a user leaves."""
        print(f"[Bot] {user.username} left the room.")


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
