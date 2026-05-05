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
from modules.maintenance         import (
    handle_botstatus, handle_dbstats, handle_backup,
    handle_maintenance, handle_reloadsettings, handle_cleanup,
    is_maintenance,
)
from modules.permissions         import (
    is_owner, is_admin, is_manager, is_moderator,
    can_moderate, can_manage_games, can_manage_economy, can_audit,
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
    handle_event, handle_events, handle_eventhelp, handle_eventstatus,
    handle_startevent, handle_stopevent,
    handle_eventpoints, handle_eventshop, handle_buyevent,
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
    "allstaff",
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
    "checkcommands",
    "setdailycoins", "setgamereward", "settransferfee",
    "eventstart", "eventstop",
    "clearwarnings",
    "addrep", "removerep",
} | BANK_ADMIN_SET_CMDS

OWNER_ONLY_CMDS = {
    "addadmin", "removeadmin", "admins", "setmaxbalance",
    "addowner", "removeowner",
}

STAFF_CMDS = MOD_ONLY_CMDS | MANAGER_ONLY_CMDS | ADMIN_ONLY_CMDS | OWNER_ONLY_CMDS

ALL_KNOWN_COMMANDS = (
    {
        "help", "answer",
        "owners",
        "casinohelp", "gamehelp", "coinhelp", "profilehelp",
        "shophelp", "progresshelp", "bankhelp",
        "staffhelp", "modhelp", "managerhelp", "adminhelp", "ownerhelp",
        "casino", "managers", "moderators",
        "quests", "claimquest",
        "dailyquests", "weeklyquests", "questhelp",
        "event", "events", "eventhelp", "eventstatus",
        "startevent", "stopevent",
        "eventpoints", "eventshop", "buyevent",
        "report", "bug", "myreports",
        "rep", "reputation", "repstats", "toprep", "repleaderboard",
        "poker",
        "botstatus", "dbstats", "backup",
        "maintenance", "reloadsettings", "cleanup",
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
    "🏆 /progresshelp\n"
    "🎉 /eventhelp\n"
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
    "/bj join <bet>\n"
    "/rbj join <bet>\n"
    "/bj hit | /rbj hit\n"
    "/bj stand | /rbj stand\n"
    "/bj table | /rbj table\n"
    "/bj rules | /rbj rules"
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
    "/reputation /toprep"
)

PROGRESS_HELP = (
    "🏆 Progress\n"
    "/quests /dailyquests\n"
    "/weeklyquests\n"
    "/claimquest\n"
    "/achievements\n"
    "/claimachievements"
)

# ── Staff help texts ──────────────────────────────────────────────────────────

STAFF_HELP_TEXT = (
    "⚙️ Staff Menus\n"
    "/modhelp\n"
    "/managerhelp\n"
    "/adminhelp\n"
    "/ownerhelp\n"
    "/allstaff"
)

MOD_HELP_PAGES = [
    (
        "🔨 Mod 1\n"
        "/announce <msg>\n"
        "/resetgame\n"
        "/casino reset\n"
        "/bj cancel\n"
        "/rbj cancel"
    ),
    (
        "🔨 Mod 2\n"
        "/reports\n"
        "/reportinfo <id>\n"
        "/closereport <id>\n"
        "/reportwatch <user>"
    ),
    (
        "🔨 Mod 3\n"
        "/viewtx <user>\n"
        "/bankwatch <user>\n"
        "/ledger <user>\n"
        "/audit <user>"
    ),
    (
        "🔨 Mod 4\n"
        "/warn <user> <reason>\n"
        "/warnings <user>\n"
        "/mute <user> <min>\n"
        "/unmute <user>\n"
        "/mutes"
    ),
]

MANAGER_HELP_PAGES = [
    (
        "🧰 Manager 1\n"
        "/bj on/off\n"
        "/rbj on/off\n"
        "/casino on/off\n"
        "/casino modes\n"
        "/casino reset"
    ),
    (
        "🧰 Manager 2\n"
        "/setbjturntimer <sec>\n"
        "/setrbjturntimer <sec>\n"
        "/bj settings\n"
        "/rbj settings"
    ),
    (
        "🧰 Manager 3\n"
        "/setbjdailywinlimit <amt>\n"
        "/setbjdailylosslimit <amt>\n"
        "/setrbjdailywinlimit <amt>\n"
        "/setrbjdailylosslimit <amt>"
    ),
    (
        "🧰 Manager 4\n"
        "/startevent <id>\n"
        "/stopevent\n"
        "/eventstatus\n"
        "/announce <msg>"
    ),
]

ADMIN_HELP_PAGES = [
    (
        "🛡️ Admin 1\n"
        "/addcoins <user> <amt>\n"
        "/removecoins <user> <amt>\n"
        "/bankblock <user>\n"
        "/bankunblock <user>"
    ),
    (
        "🛡️ Admin 2\n"
        "/setsendlimit <amt>\n"
        "/setnewaccountdays <days>\n"
        "/setminlevelsend <lvl>\n"
        "/setmintotalearned <amt>\n"
        "/setsendtax <percent>"
    ),
    (
        "🛡️ Admin 3\n"
        "/addmanager <user>\n"
        "/removemanager <user>\n"
        "/managers\n"
        "/addmoderator <user>\n"
        "/removemoderator <user>\n"
        "/moderators"
    ),
    (
        "🛡️ Admin 4\n"
        "/dbstats\n"
        "/maintenance on/off\n"
        "/reloadsettings\n"
        "/cleanup"
    ),
]

OWNER_HELP_PAGES = [
    (
        "👑 Owner 1\n"
        "/addowner <user>\n"
        "/removeowner <user>\n"
        "/owners\n"
        "/addadmin <user>\n"
        "/removeadmin <user>\n"
        "/admins"
    ),
    (
        "👑 Owner 2\n"
        "/allstaff\n"
        "/allcommands\n"
        "/backup\n"
        "/dbstats\n"
        "/maintenance on/off"
    ),
    (
        "👑 Owner 3\n"
        "Full access:\n"
        "economy, bank, casino,\n"
        "events, reports,\n"
        "staff, backup."
    ),
]

ALLCMDS = [
    "Cmds 1 Player\n/help /gamehelp /casinohelp\n/coinhelp /bankhelp\n/shophelp /profilehelp",
    "Cmds 2 Games\n/trivia /scramble /riddle\n/answer\n/coinflip\n/daily /balance",
    "Cmds 3 Casino\n/bj join/hit/stand/table\n/rbj join/hit/stand/table\n/bj rules /rbj rules",
    "Cmds 4 Bank/Shop\n/send /bank\n/transactions\n/shop titles/badges\n/titleinfo /badgeinfo\n/buy /equip",
    "Cmds 5 Progress\n/profile /level\n/xpleaderboard\n/quests /claimquest\n/achievements /reputation",
    "Cmds 6 Staff\n/staffhelp /modhelp\n/managerhelp /adminhelp\n/ownerhelp /allstaff",
    "Cmds 7 Owner/Admin\n/addowner /removeowner\n/addadmin /removeadmin\n/addmanager /addmoderator\n/backup /dbstats",
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
    await bot.highrise.send_whisper(user.id, STAFF_HELP_TEXT)


async def _handle_modhelp(bot, user, args):
    if not can_moderate(user.username):
        await bot.highrise.send_whisper(user.id, "Staff only.")
        return
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
    n = len(MOD_HELP_PAGES)
    if page == 0:
        # Send all pages
        for p in MOD_HELP_PAGES:
            await bot.highrise.send_whisper(user.id, p)
    elif 1 <= page <= n:
        await bot.highrise.send_whisper(user.id, MOD_HELP_PAGES[page - 1])
    else:
        await bot.highrise.send_whisper(user.id, f"Mod help pages 1-{n}.")


async def _handle_managerhelp(bot, user, args):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
    n = len(MANAGER_HELP_PAGES)
    if page == 0:
        for p in MANAGER_HELP_PAGES:
            await bot.highrise.send_whisper(user.id, p)
    elif 1 <= page <= n:
        await bot.highrise.send_whisper(user.id, MANAGER_HELP_PAGES[page - 1])
    else:
        await bot.highrise.send_whisper(user.id, f"Manager help pages 1-{n}.")


async def _handle_adminhelp(bot, user, args):
    if not can_manage_economy(user.username):
        await bot.highrise.send_whisper(user.id, "Admins and owners only.")
        return
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
    n = len(ADMIN_HELP_PAGES)
    if page == 0:
        for p in ADMIN_HELP_PAGES:
            await bot.highrise.send_whisper(user.id, p)
    elif 1 <= page <= n:
        await bot.highrise.send_whisper(user.id, ADMIN_HELP_PAGES[page - 1])
    else:
        await bot.highrise.send_whisper(user.id, f"Admin help pages 1-{n}.")


async def _handle_ownerhelp(bot, user, args):
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Owner only.")
        return
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
    n = len(OWNER_HELP_PAGES)
    if page == 0:
        for p in OWNER_HELP_PAGES:
            await bot.highrise.send_whisper(user.id, p)
    elif 1 <= page <= n:
        await bot.highrise.send_whisper(user.id, OWNER_HELP_PAGES[page - 1])
    else:
        await bot.highrise.send_whisper(user.id, f"Owner help pages 1-{n}.")


async def _handle_staff_cmd(bot, user, cmd, args):
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, f"Usage: /{cmd} <username>")
        return
    target = args[1].lstrip("@").lower()
    if cmd == "addowner":
        r = db.add_owner_user(target)
        msg = (f"@{target} is already an owner."
               if r == "exists" else f"👑 @{target} is now an owner.")
    elif cmd == "removeowner":
        r = db.remove_owner_user(target)
        if r == "last_owner":
            msg = "Cannot remove the final owner."
        elif r == "not_found":
            msg = f"@{target} is not an owner."
        else:
            msg = f"❌ @{target} is no longer an owner."
    elif cmd == "addmanager":
        r = db.add_manager(target)
        msg = (f"@{target} is already a manager."
               if r == "exists" else f"✅ @{target} is now a manager.")
    elif cmd == "removemanager":
        r = db.remove_manager(target)
        msg = (f"@{target} is not a manager."
               if r == "not_found" else f"❌ @{target} removed as manager.")
    elif cmd == "addmoderator":
        r = db.add_moderator(target)
        msg = (f"@{target} is already a moderator."
               if r == "exists" else f"✅ @{target} is now a moderator.")
    elif cmd == "removemoderator":
        r = db.remove_moderator(target)
        msg = (f"@{target} is not a moderator."
               if r == "not_found" else f"❌ @{target} removed as moderator.")
    elif cmd == "addadmin":
        r = db.add_admin_user(target)
        msg = (f"@{target} is already an admin."
               if r == "exists" else f"✅ @{target} is now an admin.")
    elif cmd == "removeadmin":
        r = db.remove_admin_user(target)
        msg = (f"@{target} is not an admin."
               if r == "not_found" else f"❌ @{target} removed as admin.")
    else:
        return
    await bot.highrise.send_whisper(user.id, msg)


async def _cmd_owners(bot, user):
    cfg_owners = [u.lower() for u in config.OWNER_USERS]
    db_owners  = db.get_owner_users()
    all_owners = sorted(set(cfg_owners + db_owners))
    msg = "Owners: " + ", ".join(f"@{o}" for o in all_owners) if all_owners else "No owners set."
    await bot.highrise.send_whisper(user.id, msg[:245])


async def _cmd_admins(bot, user):
    owner_set     = {u.lower() for u in config.OWNER_USERS}
    config_admins = [u.lower() for u in config.ADMIN_USERS if u.lower() not in owner_set]
    dynamic       = db.get_admin_users()
    all_admins    = sorted(set(config_admins + dynamic))
    msg = "Admins: " + ", ".join(f"@{a}" for a in all_admins) if all_admins else "No admins set."
    await bot.highrise.send_whisper(user.id, msg[:245])


async def _cmd_allstaff(bot, user, args):
    """Show all staff grouped by role, with optional filter/page."""
    sub = args[1].lower() if len(args) > 1 else ""

    cfg_owners  = [u.lower() for u in config.OWNER_USERS]
    db_owners   = db.get_owner_users()
    owners      = sorted(set(cfg_owners + db_owners))

    cfg_admins  = [u.lower() for u in config.ADMIN_USERS
                   if u.lower() not in {o.lower() for o in owners}]
    db_admins   = db.get_admin_users()
    admins      = sorted(set(cfg_admins + db_admins))

    managers    = db.get_managers()
    moderators  = db.get_moderators()

    def fmt(label, emoji, names):
        line = f"{emoji} {label}: " + (", ".join(f"@{n}" for n in names) if names else "None")
        return line[:245]

    if sub in ("owners", "1"):
        await bot.highrise.send_whisper(user.id, fmt("Owners", "👑", owners))
    elif sub in ("admins", "2"):
        await bot.highrise.send_whisper(user.id, fmt("Admins", "🛡️", admins))
    elif sub in ("managers", "3"):
        await bot.highrise.send_whisper(user.id, fmt("Managers", "🧰", managers))
    elif sub in ("moderators", "mods", "4"):
        await bot.highrise.send_whisper(user.id, fmt("Mods", "🔨", moderators))
    else:
        # Show all sections (each as a separate whisper to stay under 249 chars)
        await bot.highrise.send_whisper(user.id, fmt("Owners",   "👑", owners))
        await bot.highrise.send_whisper(user.id, fmt("Admins",   "🛡️", admins))
        await bot.highrise.send_whisper(user.id, fmt("Managers", "🧰", managers))
        await bot.highrise.send_whisper(user.id, fmt("Mods",     "🔨", moderators))


async def _cmd_allcommands(bot, user, args):
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
    n = len(ALLCMDS)
    if page == 0:
        await bot.highrise.send_whisper(user.id, f"Use /allcommands 1-{n}.")
    elif 1 <= page <= n:
        await bot.highrise.send_whisper(user.id, ALLCMDS[page - 1])
    else:
        await bot.highrise.send_whisper(user.id, f"Use /allcommands 1-{n}.")


async def _cmd_checkcommands(bot, user):
    """Show which command modules are installed and routed."""
    _g = globals()

    def ok(name: str) -> str:
        try:
            obj = _g.get(name)
            return "✅" if (obj is not None) else "❌"
        except Exception:
            return "❌"

    checks = [
        ("Help",    "HELP_TEXT"),
        ("Games",   "handle_game_command"),
        ("Economy", "handle_balance"),
        ("Shop",    "handle_shop"),
        ("Bank",    "handle_bank"),
        ("Casino",  "handle_bj"),
        ("RBJ",     "handle_rbj"),
        ("Events",  "handle_event"),
        ("Quests",  "handle_quests"),
        ("Achieve", "handle_achievements"),
        ("Rep",     "handle_rep"),
        ("Reports", "handle_report"),
        ("Staff",   "is_owner"),
        ("Maint",   "handle_botstatus"),
    ]

    parts = [f"{ok(sym)} {label}" for label, sym in checks]
    msg = " ".join(parts)
    await bot.highrise.send_whisper(user.id, msg[:245])


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
            if can_moderate(user.username):
                await self.highrise.send_whisper(
                    user.id,
                    "Staff menus: /modhelp /managerhelp /adminhelp /ownerhelp /allstaff"
                )
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
            elif cmd in {"addowner", "removeowner",
                         "addmanager", "removemanager",
                         "addmoderator", "removemoderator",
                         "addadmin", "removeadmin"}:
                await _handle_staff_cmd(self, user, cmd, args)
            elif cmd == "admins":
                await _cmd_admins(self, user)
            elif cmd == "allstaff":
                await _cmd_allstaff(self, user, args)
            elif cmd == "allcommands":
                await _cmd_allcommands(self, user, args)
            elif cmd == "checkcommands":
                await _cmd_checkcommands(self, user)
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

        # ── Maintenance gate — block gameplay/economy during maintenance ──────
        _MAINT_BLOCKED = (
            GAME_COMMANDS | BJ_COMMANDS
            | {"poker", "daily", "send", "buy",
               "claimachievements", "claimquest", "buyevent"}
        )
        if is_maintenance() and cmd in _MAINT_BLOCKED:
            if not can_moderate(user.username):
                await self.highrise.send_whisper(
                    user.id,
                    "🔧 Maintenance mode is ON. This feature is temporarily unavailable."
                )
                return

        # ── Mute gate — block muted players from economy/game commands ────────
        _MUTE_EXEMPT = {
            "help", "casinohelp", "gamehelp", "coinhelp", "profilehelp",
            "shophelp", "progresshelp", "bankhelp", "staffhelp", "modhelp",
            "managerhelp", "adminhelp", "ownerhelp", "questhelp",
            "profile", "level", "balance", "myitems",
            "myreports", "report", "bug",
            "botstatus", "maintenance",
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
        elif cmd == "event":
            await handle_event(self, user)

        elif cmd == "events":
            await handle_events(self, user)

        elif cmd == "eventhelp":
            await handle_eventhelp(self, user)

        elif cmd == "eventstatus":
            await handle_eventstatus(self, user)

        elif cmd == "startevent":
            await handle_startevent(self, user, args)

        elif cmd == "stopevent":
            await handle_stopevent(self, user)

        elif cmd == "eventpoints":
            await handle_eventpoints(self, user)

        elif cmd == "eventshop":
            await handle_eventshop(self, user)

        elif cmd == "buyevent":
            await handle_buyevent(self, user, args)

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
            await _handle_modhelp(self, user, args)

        elif cmd == "managerhelp":
            await _handle_managerhelp(self, user, args)

        elif cmd == "adminhelp":
            await _handle_adminhelp(self, user, args)

        elif cmd == "ownerhelp":
            await _handle_ownerhelp(self, user, args)

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

        elif cmd == "owners":
            await _cmd_owners(self, user)

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

        # ── Maintenance tools ─────────────────────────────────────────────────
        elif cmd == "botstatus":
            await handle_botstatus(self, user)

        elif cmd == "dbstats":
            await handle_dbstats(self, user)

        elif cmd == "backup":
            await handle_backup(self, user)

        elif cmd == "maintenance":
            await handle_maintenance(self, user, args)

        elif cmd == "reloadsettings":
            await handle_reloadsettings(self, user)

        elif cmd == "cleanup":
            await handle_cleanup(self, user)

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
