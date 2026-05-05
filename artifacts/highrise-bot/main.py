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
from modules.poker               import handle_poker, reset_table as poker_reset_table
from modules.permissions         import (
    is_owner, is_admin, can_moderate,
    can_manage_games, can_manage_economy,
)
from modules.audit import (
    handle_audithelp, handle_audit,
    handle_auditbank, handle_auditcasino, handle_auditeconomy,
)
from modules.economy_settings import (
    handle_economysettings,
    handle_setdailycoins, handle_setgamereward,
    handle_setmaxbalance, handle_settransferfee,
)
from modules.quests import (
    handle_questhelp, handle_quests,
    handle_dailyquests, handle_weeklyquests, handle_claimquest,
)
from modules.events import (
    handle_eventpoints, handle_eventshop, handle_buyevent,
    handle_eventstart, handle_eventstop,
)
from modules.reports import (
    handle_report, handle_bug, handle_myreports,
    handle_reports, handle_reportinfo, handle_closereport, handle_reportwatch,
)
from modules.moderation import (
    handle_mute, handle_unmute, handle_mutes,
    handle_warn, handle_warnings, handle_clearwarnings,
)
from modules.reputation import (
    handle_rep, handle_reputation, handle_toprep,
    handle_replog, handle_addrep, handle_removerep,
)
from modules.bank import (
    handle_bank, handle_send, handle_transactions, handle_bankstats,
    handle_banknotify,
    handle_viewtx, handle_bankwatch, handle_bankblock, handle_banksettings,
    handle_bank_set, handle_ledger,
)


# ---------------------------------------------------------------------------
# Command sets
# ---------------------------------------------------------------------------

ECONOMY_COMMANDS     = {"balance", "daily", "leaderboard"}
PROFILE_COMMANDS     = {"profile", "level", "xpleaderboard"}
GAME_COMMANDS        = {"trivia", "scramble", "riddle", "coinflip"}
SHOP_COMMANDS        = {"shop", "buy", "equip", "myitems", "badgeinfo", "titleinfo"}
ACHIEVEMENT_COMMANDS = {"achievements", "claimachievements"}
BJ_COMMANDS          = {"bj", "rbj"}
BANK_PLAYER_COMMANDS = {"bank", "send", "transactions", "bankstats", "banknotify"}

BANK_ADMIN_SET_CMDS = {
    "setsendlimit", "setnewaccountdays", "setminlevelsend",
    "setmintotalearned", "setsendtax",
}

# Staff command tiers
MOD_ONLY_CMDS = {
    "resetgame", "announce", "viewtx", "bankwatch",
    "audit", "auditbank", "auditcasino", "auditeconomy", "audithelp",
    "economysettings",
    "reports", "reportinfo", "closereport", "reportwatch",
    "warn", "warnings",
    "replog",
}

MANAGER_ONLY_CMDS = {
    "mute", "unmute", "mutes",
    "banksettings",
    "setbjminbet", "setbjmaxbet", "setbjcountdown", "setbjturntimer",
    "setbjdailywinlimit", "setbjdailylosslimit",
    "setrbjdecks", "setrbjminbet", "setrbjmaxbet", "setrbjcountdown",
    "setrbjshuffle", "setrbjblackjackpayout", "setrbjwinpayout", "setrbjturntimer",
    "setrbjdailywinlimit", "setrbjdailylosslimit",
}

ADMIN_ONLY_CMDS = {
    "addcoins", "removecoins",
    "addmanager", "removemanager",
    "addmoderator", "removemoderator",
    "bankblock", "bankunblock",
    "ledger",
    "allcommands",
    "setdailycoins", "setgamereward", "settransferfee",
    "eventstart", "eventstop",
    "clearwarnings",
    "addrep", "removerep",
} | BANK_ADMIN_SET_CMDS

OWNER_ONLY_CMDS = {"addadmin", "removeadmin", "admins", "setmaxbalance"}

STAFF_CMDS = MOD_ONLY_CMDS | MANAGER_ONLY_CMDS | ADMIN_ONLY_CMDS | OWNER_ONLY_CMDS

ALL_KNOWN_COMMANDS = (
    {
        "help", "answer",
        "casinohelp", "gamehelp", "coinhelp", "profilehelp",
        "shophelp", "progresshelp", "bankhelp",
        "staffhelp", "modhelp", "managerhelp", "adminhelp", "ownerhelp",
        "casino", "managers", "moderators",
        "quests", "claimquest",
        "dailyquests", "weeklyquests", "questhelp",
        "eventpoints", "eventshop", "buyevent",
        "report", "bug", "myreports",
        "rep", "reputation", "repstats", "toprep", "repleaderboard",
        "poker",
    }
    | ECONOMY_COMMANDS | PROFILE_COMMANDS | GAME_COMMANDS
    | SHOP_COMMANDS | ACHIEVEMENT_COMMANDS | BJ_COMMANDS
    | BANK_PLAYER_COMMANDS | STAFF_CMDS
)


# ---------------------------------------------------------------------------
# Help texts  (all ≤ 249 chars)
# ---------------------------------------------------------------------------

HELP_TEXT = (
    "🤖 Help\n"
    "🎮 /gamehelp\n"
    "🎰 /casinohelp\n"
    "💰 /coinhelp\n"
    "🏦 /bankhelp\n"
    "🛒 /shophelp\n"
    "⭐ /profilehelp\n"
    "Staff: /staffhelp"
)

GAME_HELP = (
    "🎮 Games\n"
    "/trivia /scramble /riddle\n"
    "/answer <answer>\n"
    "/coinflip heads/tails <bet>"
)

CASINO_HELP = (
    "🎰 Casino\n"
    "/casino modes\n"
    "/bj join <bet> | /rbj join <bet>\n"
    "/bj hit/stand/table\n"
    "/poker join <buyin> (100-5000c)\n"
    "/poker hand | /poker table\n"
    "/poker check | call | raise <amt> | fold"
)

COIN_HELP = (
    "💰 Coins\n"
    "/daily /balance\n"
    "/leaderboard\n"
    "/send <user> <amount>\n"
    "/bank /transactions"
)

BANK_HELP = (
    "🏦 Bank\n"
    "/send <user> <amt>\n"
    "/bank\n"
    "/bankstats\n"
    "/transactions\n"
    "/banknotify on/off"
)

SHOP_HELP = (
    "🛒 Shop\n"
    "/shop titles\n"
    "/shop badges\n"
    "/titleinfo <id>\n"
    "/badgeinfo <id>\n"
    "/buy title/badge <id>\n"
    "/equip title/badge <id>"
)

PROFILE_HELP = (
    "⭐ Profile\n"
    "/profile\n"
    "/level\n"
    "/xpleaderboard\n"
    "/myitems\n"
    "/rep <user>\n"
    "/reputation\n"
    "/toprep"
)

PROGRESS_HELP = (
    "🏆 Progress\n"
    "/quests\n"
    "/dailyquests\n"
    "/weeklyquests\n"
    "/claimquest\n"
    "/achievements"
)

MOD_HELP = (
    "🛡️ Moderator\n"
    "/announce <msg>\n"
    "/resetgame\n"
    "/casino reset\n"
    "/bj cancel\n"
    "/rbj cancel\n"
    "/bankwatch <user>\n"
    "/viewtx <user>"
)

MANAGER_HELP_1 = (
    "🧰 Manager\n"
    "/bj on/off\n"
    "/rbj on/off\n"
    "/casino on/off\n"
    "/casino modes\n"
    "/setbjturntimer <sec>"
)

MANAGER_HELP_2 = (
    "🧰 Manager 2\n"
    "/setrbjturntimer <sec>\n"
    "/setbjdailywinlimit <amt>\n"
    "/setbjdailylosslimit <amt>\n"
    "/bankwatch <user>"
)

ADMIN_HELP_1 = (
    "👑 Admin\n"
    "/addcoins <user> <amt>\n"
    "/removecoins <user> <amt>\n"
    "/bankblock <user>\n"
    "/bankunblock <user>"
)

ADMIN_HELP_2 = (
    "👑 Admin 2\n"
    "/setsendlimit <amt>\n"
    "/setnewaccountdays <days>\n"
    "/setminlevelsend <lvl>\n"
    "/setsendtax <percent>"
)

ADMIN_HELP_3 = (
    "👑 Admin 3\n"
    "/addmanager <user>\n"
    "/removemanager <user>\n"
    "/addmoderator <user>\n"
    "/removemoderator <user>"
)

OWNER_HELP_1 = (
    "👑 Owner\n"
    "/addadmin <user>\n"
    "/removeadmin <user>\n"
    "/admins\n"
    "/managers\n"
    "/moderators"
)

OWNER_HELP_2 = (
    "👑 Owner 2\n"
    "/casino reset\n"
    "/ledger <user>\n"
    "/allcommands\n"
    "Full control over bot settings."
)

ALLCMDS = [
    "Commands 1\n/help /gamehelp /casinohelp\n/coinhelp /bankhelp /shophelp\n/profilehelp /progresshelp",
    "Commands 2\n/trivia /scramble /riddle\n/answer\n/daily /balance /leaderboard\n/profile /level /myitems",
    "Commands 3\n/bj join /bj hit /bj stand\n/rbj join /rbj hit /rbj stand\n/bj table /rbj table\n/bj rules /rbj rules",
    "Commands 4\n/send /bank /transactions\n/shop titles /shop badges\n/titleinfo /badgeinfo\n/buy /equip",
    "Staff Commands\n/staffhelp /modhelp\n/managerhelp /adminhelp\n/ownerhelp\n/casino reset /viewtx /bankwatch",
]


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
        if not can_moderate(user.username):
            await bot.highrise.send_whisper(user.id, "Staff only.")
            return
        bj_reset_table()
        rbj_reset_table()
        poker_reset_table()
        await bot.highrise.chat("✅ Casino tables reset (BJ, RBJ, Poker).")

    elif sub == "leaderboard":
        await _send_casino_leaderboard(bot, user)

    else:
        await bot.highrise.send_whisper(
            user.id,
            "Usage: /casino modes | /casino on | /casino off\n"
            "/casino reset | /casino leaderboard"
        )


async def _send_casino_leaderboard(bot, user):
    """Send BJ then RBJ top-5 leaderboards as two whispers."""
    for rows, header in [
        (db.get_bj_leaderboard(),  "-- BJ Top 5 (Net Profit) --"),
        (db.get_rbj_leaderboard(), "-- RBJ Top 5 (Net Profit) --"),
    ]:
        if not rows:
            await bot.highrise.send_whisper(user.id, f"{header}\nNo data yet.")
        else:
            lines = [header]
            for i, r in enumerate(rows, 1):
                name = db.get_display_name(r["user_id"], r["username"])
                net  = r["net"]
                sign = "+" if net >= 0 else ""
                lines.append(f"{i}. {name}  {sign}{net:,}c")
            await bot.highrise.send_whisper(user.id, "\n".join(lines))


async def _handle_staffhelp(bot, user):
    if not can_moderate(user.username):
        await bot.highrise.send_whisper(user.id, "Staff help is for staff only.")
        return
    await bot.highrise.send_whisper(
        user.id, "⚙️ Staff Help\n/modhelp\n/managerhelp\n/adminhelp\n/ownerhelp"
    )


async def _handle_modhelp(bot, user):
    if not can_moderate(user.username):
        await bot.highrise.send_whisper(user.id, "Staff only.")
        return
    await bot.highrise.send_whisper(user.id, MOD_HELP)


async def _handle_managerhelp(bot, user):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return
    await bot.highrise.send_whisper(user.id, MANAGER_HELP_1)
    await bot.highrise.send_whisper(user.id, MANAGER_HELP_2)


async def _handle_adminhelp(bot, user):
    if not can_manage_economy(user.username):
        await bot.highrise.send_whisper(user.id, "Admins and owners only.")
        return
    await bot.highrise.send_whisper(user.id, ADMIN_HELP_1)
    await bot.highrise.send_whisper(user.id, ADMIN_HELP_2)
    await bot.highrise.send_whisper(user.id, ADMIN_HELP_3)


async def _handle_ownerhelp(bot, user):
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Owner only.")
        return
    await bot.highrise.send_whisper(user.id, OWNER_HELP_1)
    await bot.highrise.send_whisper(user.id, OWNER_HELP_2)


async def _handle_staff_cmd(bot, user, cmd, args):
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, f"Usage: /{cmd} <username>")
        return
    target = args[1].lstrip("@").lower()
    if cmd == "addmanager":
        r = db.add_manager(target)
        msg = f"@{target} is already a manager." if r == "exists" else f"✅ @{target} is now a manager."
    elif cmd == "removemanager":
        r = db.remove_manager(target)
        msg = f"@{target} is not a manager." if r == "not_found" else f"❌ @{target} removed as manager."
    elif cmd == "addmoderator":
        r = db.add_moderator(target)
        msg = f"@{target} is already a moderator." if r == "exists" else f"✅ @{target} is now a moderator."
    elif cmd == "removemoderator":
        r = db.remove_moderator(target)
        msg = f"@{target} is not a moderator." if r == "not_found" else f"❌ @{target} removed as moderator."
    elif cmd == "addadmin":
        r = db.add_admin_user(target)
        msg = f"@{target} is already an admin." if r == "exists" else f"✅ @{target} is now an admin."
    elif cmd == "removeadmin":
        r = db.remove_admin_user(target)
        msg = f"@{target} is not an admin." if r == "not_found" else f"❌ @{target} removed as admin."
    else:
        return
    await bot.highrise.send_whisper(user.id, msg)


async def _cmd_admins(bot, user):
    owner_set = {u.lower() for u in config.OWNER_USERS}
    config_admins = [u.lower() for u in config.ADMIN_USERS if u.lower() not in owner_set]
    dynamic = db.get_admin_users()
    all_admins = sorted(set(config_admins + dynamic))
    msg = "Admins: " + ", ".join(f"@{a}" for a in all_admins) if all_admins else "No admins set."
    await bot.highrise.send_whisper(user.id, msg[:245])


async def _cmd_allcommands(bot, user, args):
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
    if 1 <= page <= len(ALLCMDS):
        await bot.highrise.send_whisper(user.id, ALLCMDS[page - 1])
    else:
        await bot.highrise.send_whisper(user.id, f"Usage: /allcommands 1-{len(ALLCMDS)}")


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

        # ── Staff gate ────────────────────────────────────────────────────────
        if cmd in STAFF_CMDS:
            if cmd in OWNER_ONLY_CMDS:
                if not is_owner(user.username):
                    await self.highrise.send_whisper(user.id, "Owner only.")
                    return
            elif cmd in ADMIN_ONLY_CMDS:
                if not can_manage_economy(user.username):
                    await self.highrise.send_whisper(user.id, "Admins and owners only.")
                    return
            elif cmd in MANAGER_ONLY_CMDS:
                if not can_manage_games(user.username):
                    await self.highrise.send_whisper(user.id, "Managers and above only.")
                    return
            elif cmd in MOD_ONLY_CMDS:
                if not can_moderate(user.username):
                    await self.highrise.send_whisper(user.id, "Staff only.")
                    return

            if cmd.startswith("setrbj"):
                await handle_rbj_set(self, user, cmd, args)
            elif cmd.startswith("setbj"):
                await handle_bj_set(self, user, cmd, args)
            elif cmd in {"addmanager", "removemanager", "addmoderator",
                         "removemoderator", "addadmin", "removeadmin"}:
                await _handle_staff_cmd(self, user, cmd, args)
            elif cmd == "admins":
                await _cmd_admins(self, user)
            elif cmd == "allcommands":
                await _cmd_allcommands(self, user, args)
            elif cmd == "bankblock":
                await handle_bankblock(self, user, args, block=True)
            elif cmd == "bankunblock":
                await handle_bankblock(self, user, args, block=False)
            elif cmd == "banksettings":
                await handle_banksettings(self, user)
            elif cmd == "ledger":
                await handle_ledger(self, user, args)
            elif cmd == "viewtx":
                await handle_viewtx(self, user, args)
            elif cmd == "bankwatch":
                await handle_bankwatch(self, user, args)
            elif cmd in BANK_ADMIN_SET_CMDS:
                await handle_bank_set(self, user, cmd, args)
            elif cmd == "audithelp":
                await handle_audithelp(self, user)
            elif cmd == "audit":
                await handle_audit(self, user, args)
            elif cmd == "auditbank":
                await handle_auditbank(self, user, args)
            elif cmd == "auditcasino":
                await handle_auditcasino(self, user, args)
            elif cmd == "auditeconomy":
                await handle_auditeconomy(self, user, args)
            elif cmd == "economysettings":
                await handle_economysettings(self, user)
            elif cmd == "setdailycoins":
                await handle_setdailycoins(self, user, args)
            elif cmd == "setgamereward":
                await handle_setgamereward(self, user, args)
            elif cmd == "setmaxbalance":
                await handle_setmaxbalance(self, user, args)
            elif cmd == "settransferfee":
                await handle_settransferfee(self, user, args)
            elif cmd == "mute":
                await handle_mute(self, user, args)
            elif cmd == "unmute":
                await handle_unmute(self, user, args)
            elif cmd == "mutes":
                await handle_mutes(self, user)
            elif cmd == "warn":
                await handle_warn(self, user, args)
            elif cmd == "warnings":
                await handle_warnings(self, user, args)
            elif cmd == "clearwarnings":
                await handle_clearwarnings(self, user, args)
            elif cmd == "reports":
                await handle_reports(self, user)
            elif cmd == "reportinfo":
                await handle_reportinfo(self, user, args)
            elif cmd == "closereport":
                await handle_closereport(self, user, args)
            elif cmd == "reportwatch":
                await handle_reportwatch(self, user, args)
            elif cmd == "replog":
                await handle_replog(self, user, args)
            elif cmd == "addrep":
                await handle_addrep(self, user, args)
            elif cmd == "removerep":
                await handle_removerep(self, user, args)
            else:
                await handle_admin_command(self, user, cmd, args)
            return

        # ── Mute gate — block muted players from economy/game commands ────────
        _MUTE_EXEMPT = {
            "help", "casinohelp", "gamehelp", "coinhelp", "profilehelp",
            "shophelp", "progresshelp", "bankhelp", "staffhelp", "modhelp",
            "managerhelp", "adminhelp", "ownerhelp", "questhelp",
            "profile", "level", "balance", "myitems",
            "myreports", "report", "bug",
        }
        if cmd not in _MUTE_EXEMPT:
            _mute = db.get_active_mute(user.id)
            if _mute:
                await self.highrise.send_whisper(
                    user.id,
                    f"🔇 You are muted for {_mute['mins_left']} more min."
                )
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

        # ── Event commands ────────────────────────────────────────────────────
        elif cmd == "eventpoints":
            await handle_eventpoints(self, user)

        elif cmd == "eventshop":
            await handle_eventshop(self, user)

        elif cmd == "buyevent":
            await handle_buyevent(self, user, args)

        elif cmd == "eventstart":
            await handle_eventstart(self, user)

        elif cmd == "eventstop":
            await handle_eventstop(self, user)

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

        # ── Bank player commands ───────────────────────────────────────────────
        elif cmd == "bank":
            await handle_bank(self, user, args)

        elif cmd == "send":
            await handle_send(self, user, args)

        elif cmd == "transactions":
            await handle_transactions(self, user, args)

        elif cmd == "bankstats":
            await handle_bankstats(self, user)

        elif cmd == "banknotify":
            await handle_banknotify(self, user, args)

        elif cmd == "bankhelp":
            await self.highrise.send_whisper(user.id, BANK_HELP)

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

        elif cmd == "staffhelp":
            await _handle_staffhelp(self, user)

        elif cmd == "modhelp":
            await _handle_modhelp(self, user)

        elif cmd == "managerhelp":
            await _handle_managerhelp(self, user)

        elif cmd == "adminhelp":
            await _handle_adminhelp(self, user)

        elif cmd == "ownerhelp":
            await _handle_ownerhelp(self, user)

        elif cmd == "questhelp":
            await handle_questhelp(self, user)

        elif cmd == "quests":
            await handle_quests(self, user)

        elif cmd == "dailyquests":
            await handle_dailyquests(self, user)

        elif cmd == "weeklyquests":
            await handle_weeklyquests(self, user)

        elif cmd == "claimquest":
            await handle_claimquest(self, user, args)

        elif cmd == "casino":
            await _handle_casino_cmd(self, user, args)

        elif cmd == "managers":
            mgrs = db.get_managers()
            msg = "Managers: " + ", ".join(f"@{m}" for m in mgrs) if mgrs else "No managers set."
            await self.highrise.send_whisper(user.id, msg[:245])

        elif cmd == "moderators":
            mods = db.get_moderators()
            msg = "Moderators: " + ", ".join(f"@{m}" for m in mods) if mods else "No moderators set."
            await self.highrise.send_whisper(user.id, msg[:245])

        # ── /answer ───────────────────────────────────────────────────────────
        elif cmd == "answer":
            answer_text = " ".join(args[1:]).strip()
            if not answer_text:
                await self.highrise.send_whisper(user.id, "Usage: /answer <your answer>")
                return
            await games_handle_answer(self, user, answer_text)

        # ── Report commands ───────────────────────────────────────────────────
        elif cmd == "report":
            await handle_report(self, user, args)

        elif cmd == "bug":
            await handle_bug(self, user, args)

        elif cmd == "myreports":
            await handle_myreports(self, user)

        elif cmd == "reports":
            await handle_reports(self, user)

        elif cmd == "reportinfo":
            await handle_reportinfo(self, user, args)

        elif cmd == "closereport":
            await handle_closereport(self, user, args)

        elif cmd == "reportwatch":
            await handle_reportwatch(self, user, args)

        # ── Reputation commands ───────────────────────────────────────────────
        elif cmd == "rep":
            await handle_rep(self, user, args)

        elif cmd in {"reputation", "repstats"}:
            await handle_reputation(self, user)

        elif cmd in {"toprep", "repleaderboard"}:
            await handle_toprep(self, user)

        # ── Poker ─────────────────────────────────────────────────────────────
        elif cmd == "poker":
            await handle_poker(self, user, args)

        # ── Game commands ─────────────────────────────────────────────────────
        elif cmd in GAME_COMMANDS:
            await handle_game_command(self, user, cmd, args)

        # ── Unknown command ───────────────────────────────────────────────────
        else:
            await self.highrise.send_whisper(user.id, "Unknown command. Type /help.")

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
