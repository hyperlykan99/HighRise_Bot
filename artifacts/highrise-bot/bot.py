"""
bot.py
------
Main bot entry point.

This file wires everything together:
  - Connects to Highrise using BOT_TOKEN and ROOM_ID from environment variables
  - Initialises the SQLite database
  - Listens for chat messages and routes them to the correct module

How to add a new module (e.g. trivia):
  1. Create  modules/trivia.py  with a handle_trivia_command() function
  2. Import it here
  3. Add its command names to the sets below
  4. Add a routing branch in on_chat()
  5. That's it — no other files need to change
"""

import asyncio
from highrise import BaseBot, User
from highrise.__main__ import BotDefinition, main as highrise_main

import database as db
import config

# Import each feature module
from modules.dj import handle_dj_command, handle_dj_admin_command
from modules.economy import handle_economy_command, handle_economy_admin_command

# ---------------------------------------------------------------------------
# Command routing tables
# ---------------------------------------------------------------------------
# List the slash-command names each module owns (without the /).

DJ_COMMANDS            = {"dj", "request", "queue", "now", "skipvote"}
ECONOMY_COMMANDS       = {"balance", "daily"}
ADMIN_DJ_COMMANDS      = {"skip", "remove"}
ADMIN_ECONOMY_COMMANDS = {"addtokens", "refund"}

HELP_TEXT = (
    "Bot Commands\n"
    "-- DJ System --\n"
    "  /dj                      - about the DJ system\n"
    f"  /request <song>          - request a song ({config.SONG_REQUEST_COST} tokens)\n"
    "  /queue                   - show next 5 songs\n"
    "  /now                     - show current song\n"
    "  /skipvote                - vote to skip current song\n"
    "-- Tokens --\n"
    "  /balance                 - check your token balance\n"
    f"  /daily                   - claim {config.DAILY_REWARD} free tokens (once/day)\n"
    "-- Admin only --\n"
    "  /skip  /remove <#>  /addtokens <user> <amt>  /refund <user> <amt>"
)


class HangoutBot(BaseBot):
    """
    Main bot class.

    Inherits from Highrise BaseBot and overrides the event hooks we care about.
    Add new on_* methods here as you need more Highrise events.
    """

    async def on_start(self, session_metadata) -> None:
        """Called once when the bot successfully connects to the room."""
        db.init_db()  # create tables if they don't exist
        print(f"[Bot] Connected to room {config.ROOM_ID}")
        await self.highrise.chat("Bot is online! Type /help to see commands.")

    async def on_chat(self, user: User, message: str) -> None:
        """
        Called every time someone sends a public chat message in the room.
        We only react to messages that start with '/'.
        """
        message = message.strip()

        if not message.startswith("/"):
            return  # not a command — ignore it

        # Split "/request Blinding Lights" into ["request", "Blinding", "Lights"]
        parts = message[1:].split()
        if not parts:
            return

        cmd  = parts[0].lower()
        args = parts  # pass full list; handlers use args[0] as the command name

        # ── /help ──────────────────────────────────────────────────────────
        if cmd == "help":
            await self.highrise.send_whisper(user.id, HELP_TEXT)
            return

        # ── Admin-only commands ────────────────────────────────────────────
        if cmd in ADMIN_DJ_COMMANDS or cmd in ADMIN_ECONOMY_COMMANDS:
            if user.id not in config.ADMIN_IDS:
                await self.highrise.send_whisper(user.id, "That command is for admins only.")
                return

            if cmd in ADMIN_DJ_COMMANDS:
                await handle_dj_admin_command(self, user, args)
            else:
                await handle_economy_admin_command(self, user, args)
            return

        # ── Public commands ────────────────────────────────────────────────
        if cmd in DJ_COMMANDS:
            await handle_dj_command(self, user, args)

        elif cmd in ECONOMY_COMMANDS:
            await handle_economy_command(self, user, args)

        else:
            await self.highrise.send_whisper(
                user.id, "Unknown command. Type /help to see all commands."
            )

    async def on_user_join(self, user: User, position) -> None:
        """Called when a user enters the room. Register them in the DB."""
        db.ensure_user(user.id, user.username)
        await self.highrise.chat(f"Welcome, @{user.username}! Type /help to see what I can do.")

    async def on_user_leave(self, user: User) -> None:
        """Called when a user leaves the room."""
        print(f"[Bot] {user.username} left the room.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run():
    """Start the bot using the Highrise SDK BotDefinition runner."""
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
