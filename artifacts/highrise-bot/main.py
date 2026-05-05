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
from economy import (
    handle_balance, handle_daily, handle_leaderboard,
    handle_profile, handle_level, handle_xp_leaderboard,
)
from games        import handle_game_command, handle_answer as games_handle_answer
from admin        import handle_admin_command
from modules.shop         import (
    handle_shop, handle_buy, handle_equip, handle_myitems,
    handle_badgeinfo, handle_titleinfo,
)
from modules.achievements import handle_achievements, handle_claim_achievements
from modules.blackjack           import handle_bj, handle_bj_set, reset_table as bj_reset_table
from modules.realistic_blackjack import handle_rbj, handle_rbj_set, reset_table as rbj_reset_table
from modules.permissions         import can_manage_games, can_manage_economy


# ---------------------------------------------------------------------------
# Command sets
# Adding a name here makes the bot recognise it in on_chat().
# ---------------------------------------------------------------------------

# Commands any player can use
ECONOMY_COMMANDS = {"balance", "daily", "leaderboard"}
PROFILE_COMMANDS = {"profile", "level", "xpleaderboard"}
GAME_COMMANDS    = {"trivia", "scramble", "riddle", "coinflip"}
SHOP_COMMANDS        = {"shop", "buy", "equip", "myitems", "badgeinfo", "titleinfo"}
ACHIEVEMENT_COMMANDS = {"achievements", "claimachievements"}
BJ_COMMANDS          = {"bj", "rbj"}

# /answer is handled separately (routes to whichever game is active)

# Commands only players in config.ADMIN_USERS can use
ADMIN_COMMANDS = {
    "addcoins", "removecoins", "resetgame", "announce",
    "addmanager", "removemanager",
    "setbjminbet", "setbjmaxbet", "setbjcountdown", "setbjturntimer",
    "setrbjdecks", "setrbjminbet", "setrbjmaxbet", "setrbjcountdown",
    "setrbjshuffle", "setrbjblackjackpayout", "setrbjwinpayout", "setrbjturntimer",
}

ALL_KNOWN_COMMANDS = (
    {
        "help", "answer",
        "casinohelp", "gamehelp", "coinhelp", "profilehelp",
        "shophelp", "progresshelp", "adminhelp",
        "casino", "managers",
        "quests", "claimquest",
    }
    | ECONOMY_COMMANDS
    | PROFILE_COMMANDS
    | GAME_COMMANDS
    | SHOP_COMMANDS
    | ACHIEVEMENT_COMMANDS
    | BJ_COMMANDS
    | ADMIN_COMMANDS
)


# ---------------------------------------------------------------------------
# Help texts
# ---------------------------------------------------------------------------

HELP_TEXT = (
    "🤖 Help Menu\n"
    "🎮 Games: /gamehelp\n"
    "🎰 Casino: /casinohelp\n"
    "💰 Coins: /coinhelp\n"
    "⭐ Profile: /profilehelp\n"
    "🛒 Shop: /shophelp\n"
    "🏆 Progress: /progresshelp"
)

GAME_HELP = (
    "🎮 Games\n"
    "/trivia\n"
    "/scramble\n"
    "/riddle\n"
    "/answer <answer>\n"
    "Win games to earn coins + XP."
)

CASINO_HELP = (
    "🎰 Casino\n"
    "/casino modes\n"
    "/bj join <bet> - casual BJ\n"
    "/rbj join <bet> - realistic BJ\n"
    "/bj table | /rbj table\n"
    "/bj hit | /rbj hit\n"
    "/bj stand | /rbj stand"
)

COIN_HELP = (
    "💰 Coins\n"
    "/daily - claim coins\n"
    "/balance - check coins\n"
    "/leaderboard - richest players\n"
    "/coinflip heads/tails <bet>"
)

PROFILE_HELP = (
    "⭐ Profile\n"
    "/profile\n"
    "/level\n"
    "/xpleaderboard\n"
    "/myitems\n"
    "Equip badges/titles to flex."
)

SHOP_HELP = (
    "🛒 Shop\n"
    "/shop titles\n"
    "/shop badges\n"
    "/titleinfo <id>\n"
    "/badgeinfo <id>\n"
    "/buy title <id>\n"
    "/buy badge <id>\n"
    "/equip title <id>\n"
    "/equip badge <id>"
)

PROGRESS_HELP = (
    "🏆 Progress\n"
    "/achievements\n"
    "/achievements all\n"
    "/claimachievements\n"
    "/quests\n"
    "/claimquest"
)

MANAGER_HELP = (
    "⚙️ Manager\n"
    "/casino modes\n"
    "/bj on/off\n"
    "/rbj on/off\n"
    "/casino on/off\n"
    "/bj cancel\n"
    "/rbj cancel\n"
    "/announce <msg>"
)

ADMIN_HELP_EXTRA = (
    "👑 Admin\n"
    "/addcoins <user> <amt>\n"
    "/removecoins <user> <amt>\n"
    "/addmanager <user>\n"
    "/removemanager <user>\n"
    "/managers"
)


# ---------------------------------------------------------------------------
# Module-level helpers for casino and manager commands
# ---------------------------------------------------------------------------

async def _handle_casino_cmd(bot, user, args):
    sub = args[1].lower() if len(args) > 1 else ""
    if sub == "modes":
        s_bj  = db.get_bj_settings()
        s_rbj = db.get_rbj_settings()
        bj_on  = "ON"  if int(s_bj.get("bj_enabled",  1)) else "OFF"
        rbj_on = "ON"  if int(s_rbj.get("rbj_enabled", 1)) else "OFF"
        await bot.highrise.send_whisper(
            user.id,
            f"🎰 Modes: Casual BJ {bj_on} | Realistic BJ {rbj_on}"
        )
    elif sub == "on":
        if not can_manage_games(user.username):
            await bot.highrise.send_whisper(user.id, "Admins and managers only.")
            return
        db.set_bj_setting("bj_enabled", 1)
        db.set_rbj_setting("rbj_enabled", 1)
        await bot.highrise.chat("✅ Casino is now OPEN. Both BJ modes enabled.")
    elif sub == "off":
        if not can_manage_games(user.username):
            await bot.highrise.send_whisper(user.id, "Admins and managers only.")
            return
        db.set_bj_setting("bj_enabled", 0)
        db.set_rbj_setting("rbj_enabled", 0)
        await bot.highrise.chat("⛔ Casino is now CLOSED. Both BJ modes disabled.")

    elif sub == "reset":
        if not can_manage_games(user.username):
            await bot.highrise.send_whisper(user.id, "Admins and managers only.")
            return
        bj_reset_table()
        rbj_reset_table()
        await bot.highrise.chat("✅ Casino tables reset.")

    else:
        await bot.highrise.send_whisper(
            user.id,
            "Usage: /casino modes | /casino on | /casino off | /casino reset"
        )


async def _handle_adminhelp(bot, user):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Admin help is for staff only.")
        return
    await bot.highrise.send_whisper(user.id, MANAGER_HELP)
    if can_manage_economy(user.username):
        await bot.highrise.send_whisper(user.id, ADMIN_HELP_EXTRA)


async def _handle_manager_cmd(bot, user, cmd, args):
    if cmd == "addmanager":
        if len(args) < 2:
            await bot.highrise.send_whisper(user.id, "Usage: /addmanager <username>")
            return
        target = args[1].lstrip("@").lower()
        result = db.add_manager(target)
        if result == "exists":
            await bot.highrise.send_whisper(user.id, f"@{target} is already a manager.")
        else:
            await bot.highrise.send_whisper(user.id, f"✅ @{target} is now a manager.")
    elif cmd == "removemanager":
        if len(args) < 2:
            await bot.highrise.send_whisper(user.id, "Usage: /removemanager <username>")
            return
        target = args[1].lstrip("@").lower()
        result = db.remove_manager(target)
        if result == "not_found":
            await bot.highrise.send_whisper(user.id, f"@{target} is not a manager.")
        else:
            await bot.highrise.send_whisper(user.id, f"❌ @{target} is no longer a manager.")


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
            await self.highrise.send_whisper(user.id, HELP_TEXT)
            return

        # ── Admin gate ────────────────────────────────────────────────────────
        if cmd in ADMIN_COMMANDS:
            _eco_only = {
                "addcoins", "removecoins", "resetgame", "announce",
                "addmanager", "removemanager",
            }
            if cmd in _eco_only:
                if not can_manage_economy(user.username):
                    await self.highrise.send_whisper(
                        user.id, "Admins and owners only."
                    )
                    return
            else:
                if not can_manage_games(user.username):
                    await self.highrise.send_whisper(
                        user.id, "Admins and managers only."
                    )
                    return

            if cmd.startswith("setrbj"):
                await handle_rbj_set(self, user, cmd, args)
            elif cmd.startswith("setbj"):
                await handle_bj_set(self, user, cmd, args)
            elif cmd in {"addmanager", "removemanager"}:
                await _handle_manager_cmd(self, user, cmd, args)
            else:
                await handle_admin_command(self, user, cmd, args)
            return

        # ── Economy commands ──────────────────────────────────────────────────
        if cmd == "balance":
            await handle_balance(self, user)

        elif cmd == "daily":
            await handle_daily(self, user)

        elif cmd == "leaderboard":
            await handle_leaderboard(self, user)

        elif cmd == "profile":
            await handle_profile(self, user)

        elif cmd == "level":
            await handle_level(self, user)

        elif cmd == "xpleaderboard":
            await handle_xp_leaderboard(self, user)

        # ── Shop commands ─────────────────────────────────────────────────────
        elif cmd == "shop":
            await handle_shop(self, user, args)

        elif cmd == "buy":
            await handle_buy(self, user, args)

        elif cmd == "equip":
            await handle_equip(self, user, args)

        elif cmd == "myitems":
            await handle_myitems(self, user)

        elif cmd == "badgeinfo":
            await handle_badgeinfo(self, user, args)

        elif cmd == "titleinfo":
            await handle_titleinfo(self, user, args)

        # ── Achievement commands ───────────────────────────────────────────────
        elif cmd == "achievements":
            await handle_achievements(self, user, args)

        elif cmd == "claimachievements":
            await handle_claim_achievements(self, user)

        # ── Blackjack ─────────────────────────────────────────────────────────
        elif cmd == "bj":
            await handle_bj(self, user, args)

        elif cmd == "rbj":
            await handle_rbj(self, user, args)

        elif cmd == "casinohelp":
            await self.highrise.send_whisper(user.id, CASINO_HELP)

        elif cmd == "gamehelp":
            await self.highrise.send_whisper(user.id, GAME_HELP)

        elif cmd == "coinhelp":
            await self.highrise.send_whisper(user.id, COIN_HELP)

        elif cmd == "profilehelp":
            await self.highrise.send_whisper(user.id, PROFILE_HELP)

        elif cmd == "shophelp":
            await self.highrise.send_whisper(user.id, SHOP_HELP)

        elif cmd == "progresshelp":
            await self.highrise.send_whisper(user.id, PROGRESS_HELP)

        elif cmd == "adminhelp":
            await _handle_adminhelp(self, user)

        elif cmd in {"quests", "claimquest"}:
            await self.highrise.send_whisper(user.id, "Quests are coming soon! 🏆")

        elif cmd == "casino":
            await _handle_casino_cmd(self, user, args)

        elif cmd == "managers":
            mgrs = db.get_managers()
            if mgrs:
                msg = "Managers: " + ", ".join(f"@{m}" for m in mgrs)
                if len(msg) > 245:
                    msg = msg[:242] + "..."
            else:
                msg = "No managers set."
            await self.highrise.send_whisper(user.id, msg)

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
