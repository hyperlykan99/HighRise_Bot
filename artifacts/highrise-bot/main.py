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
from modules.blackjack           import (
    handle_bj, handle_bj_set,
    reset_table as bj_reset_table,
    soft_reset_table as bj_soft_reset_table,
    startup_bj_recovery,
)
from modules.realistic_blackjack import (
    handle_rbj, handle_rbj_set,
    reset_table as rbj_reset_table,
    soft_reset_table as rbj_soft_reset_table,
    startup_rbj_recovery,
)
from modules.poker               import handle_poker, reset_table as poker_reset_table
from modules.casino_settings     import (
    handle_casinosettings, handle_casinolimits, handle_casinotoggles,
    handle_setbjlimits, handle_setrbjlimits,
)
from modules.maintenance         import (
    handle_botstatus, handle_dbstats, handle_backup,
    handle_maintenance, handle_reloadsettings, handle_cleanup,
    handle_softrestart,
    handle_restartbot,
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
    startup_event_check,
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
    handle_notifications, handle_clearnotifications,
)
from modules.tips import (
    process_tip_event,
    handle_tiprate, handle_tipstats, handle_tipleaderboard,
    handle_settiprate, handle_settipcap, handle_settiptier,
    handle_debugtips,
    record_debug_any_event,
    _SDK_VERSION as _TIP_SDK_VERSION,
)
from modules.auto_games import (
    start_auto_game_loop, start_auto_event_loop,
    handle_autogames, handle_autoevents,
    handle_setgametimer, handle_setautogameinterval,
    handle_setautoeventinterval, handle_setautoeventduration,
    handle_gameconfig,
)
from modules.gold import (
    handle_goldtip, handle_goldrefund,
    handle_goldrain, handle_goldrainall,
    handle_goldwallet, handle_goldtips, handle_goldtx,
    handle_pendinggold, handle_confirmgoldtip,
    handle_setgoldrainstaff, handle_setgoldrainmax,
    handle_goldhelp,
    set_bot_identity, get_bot_user_id, add_to_room_cache, remove_from_room_cache,
    refresh_room_cache,
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
    "setminsend", "setmaxsend",
    "setsendlimit", "setnewaccountdays", "setminlevelsend",
    "setmintotalearned", "setmindailyclaims",
    "setsendtax", "sethighriskblocks",
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
    "setgametimer", "setautogameinterval",
    "setautoeventinterval", "setautoeventduration",
    "gameconfig",
    "casinosettings", "casinolimits", "casinotoggles",
    "setbjlimits", "setrbjlimits",
}

TIP_COMMANDS = {"tiprate", "tipstats", "tipleaderboard"}
TIP_ADMIN_CMDS = {"settiprate", "settipcap", "settiptier"}

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
} | BANK_ADMIN_SET_CMDS | TIP_ADMIN_CMDS

OWNER_ONLY_CMDS = {
    "addadmin", "removeadmin", "admins", "setmaxbalance",
    "addowner", "removeowner",
    "goldtip", "goldrefund", "goldrain", "goldrainall",
    "goldwallet", "goldtips", "goldtx", "pendinggold",
    "confirmgoldtip", "setgoldrainstaff", "setgoldrainmax",
    "debugtips",
    "softrestart",
    "restartbot",
}

STAFF_CMDS = MOD_ONLY_CMDS | MANAGER_ONLY_CMDS | ADMIN_ONLY_CMDS | OWNER_ONLY_CMDS

ALL_KNOWN_COMMANDS = (
    {
        "help", "answer",
        "owners",
        "casinohelp", "gamehelp", "coinhelp", "profilehelp",
        "shophelp", "progresshelp", "bankhelp", "eventhelp",
        "bjhelp", "rbjhelp", "rephelp", "autohelp",
        "casinoadminhelp", "bankadminhelp",
        "audithelp", "reporthelp", "maintenancehelp",
        "viphelp", "tiphelp", "roleshelp",
        "staffhelp", "modhelp", "managerhelp", "adminhelp", "ownerhelp",
        "casino", "managers", "moderators",
        "quests", "claimquest",
        "dailyquests", "weeklyquests", "questhelp",
        "event", "events", "eventhelp", "eventstatus",
        "startevent", "stopevent",
        "eventpoints", "eventshop", "buyevent",
        "autogames", "autoevents", "gameconfig",
        "report", "bug", "myreports",
        "rep", "reputation", "repstats", "toprep", "repleaderboard",
        "poker",
        "botstatus", "dbstats", "backup",
        "maintenance", "reloadsettings", "cleanup", "softrestart", "restartbot",
        "casinosettings", "casinolimits", "casinotoggles",
        "setbjlimits", "setrbjlimits",
        "goldhelp", "confirmcasinoreset",
        "tiprate", "tipstats", "tipleaderboard", "debugtips",
        "vipshop", "buyvip", "vipstatus",
        "allstaff", "allcommands", "checkcommands",
        "notifications", "clearnotifications",
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
    "More: /progresshelp /eventhelp"
)

GAME_HELP_PAGES = [
    (
        "🎮 Games\n"
        "/trivia /scramble /riddle\n"
        "/answer <answer>\n"
        "/coinflip heads/tails <bet>\n"
        "Auto games: /autogames status"
    ),
    (
        "🎮 Game Timers\n"
        "Games have answer timers.\n"
        "Staff: /setgametimer <sec>\n"
        "Auto: /autogames on/off"
    ),
]
GAME_HELP = GAME_HELP_PAGES[0]

CASINO_HELP_PAGES = [
    (
        "🎰 Casino\n"
        "/bj join <bet> - casual BJ\n"
        "/rbj join <bet> - realistic BJ\n"
        "/bj hit | /rbj hit\n"
        "/bj stand | /rbj stand"
    ),
    (
        "🎰 Casino 2\n"
        "/bj table | /rbj table\n"
        "/bj rules | /rbj rules\n"
        "/bj stats | /rbj stats\n"
        "/bj limits | /rbj limits"
    ),
    (
        "🎰 Casino Settings\n"
        "Players: /casino modes\n"
        "Staff: /casinosettings\n"
        "Staff: /casinolimits\n"
        "Staff: /casinotoggles"
    ),
]
CASINO_HELP = CASINO_HELP_PAGES[0]

COIN_HELP = (
    "💰 Coins\n"
    "/daily\n"
    "/balance\n"
    "/leaderboard\n"
    "/tiprate\n"
    "/tipstats\n"
    "/tipleaderboard"
)

BANK_HELP_PAGES = [
    (
        "🏦 Bank\n"
        "/send <user> <amt>\n"
        "/bank\n"
        "/bankstats\n"
        "/transactions\n"
        "/banknotify on/off"
    ),
    (
        "🏦 Bank 2\n"
        "Players can send coins if eligible.\n"
        "Staff: /banksettings\n"
        "Staff: /viewtx <user>\n"
        "Staff: /bankwatch <user>"
    ),
]
BANK_HELP = BANK_HELP_PAGES[0]

SHOP_HELP_PAGES = [
    (
        "🛒 Shop\n"
        "/shop titles\n"
        "/shop badges\n"
        "/titleinfo <id>\n"
        "/badgeinfo <id>\n"
        "/buy title <id>\n"
        "/buy badge <id>"
    ),
    (
        "🛒 Shop 2\n"
        "/equip title <id>\n"
        "/equip badge <id>\n"
        "/myitems\n"
        "/vipshop\n"
        "/buyvip\n"
        "/vipstatus"
    ),
]
SHOP_HELP = SHOP_HELP_PAGES[0]

PROFILE_HELP = (
    "⭐ Profile\n"
    "/profile\n"
    "/level\n"
    "/xpleaderboard\n"
    "/myitems\n"
    "/reputation\n"
    "/toprep"
)

PROGRESS_HELP = (
    "🏆 Progress\n"
    "/quests /dailyquests\n"
    "/weeklyquests\n"
    "/claimquest\n"
    "/achievements\n"
    "/claimachievements"
)

EVENT_HELP_PAGES = [
    (
        "🎉 Events\n"
        "/event\n"
        "/events\n"
        "/eventstatus\n"
        "/eventpoints\n"
        "/eventshop\n"
        "Staff: /startevent <id>"
    ),
    (
        "🎉 Auto Events\n"
        "/autoevents status\n"
        "Staff: /autoevents on/off\n"
        "Staff: /setautoeventinterval <min>\n"
        "Staff: /setautoeventduration <min>"
    ),
]

BJ_HELP_PAGES = [
    (
        "🃏 BJ Help\n"
        "/bj join <bet>\n"
        "/bj hit\n"
        "/bj stand\n"
        "/bj double\n"
        "/bj table\n"
        "/bj rules\n"
        "/bj stats"
    ),
    (
        "🃏 BJ Limits\n"
        "/bj limits\n"
        "Staff: /setbjlimits\n"
        "Staff: /bj state\n"
        "Staff: /bj recover/refund"
    ),
]

RBJ_HELP_PAGES = [
    (
        "🃏 RBJ Help\n"
        "/rbj join <bet>\n"
        "/rbj hit\n"
        "/rbj stand\n"
        "/rbj double\n"
        "/rbj table\n"
        "/rbj rules\n"
        "/rbj shoe"
    ),
    (
        "🃏 RBJ 2\n"
        "/rbj stats\n"
        "/rbj limits\n"
        "Staff: /setrbjlimits\n"
        "Staff: /rbj state\n"
        "Staff: /rbj recover/refund"
    ),
]

CASINO_ADMIN_HELP_PAGES = [
    (
        "🎰 Casino Admin 1\n"
        "/casinosettings\n"
        "/casinolimits\n"
        "/casinotoggles\n"
        "/bj on/off\n"
        "/rbj on/off"
    ),
    (
        "🎰 Casino Admin 2\n"
        "/setbjlimits min max win loss\n"
        "/setrbjlimits min max win loss\n"
        "/setbjturntimer <sec>\n"
        "/setrbjturntimer <sec>"
    ),
    (
        "🎰 Casino Admin 3\n"
        "/bj state\n"
        "/rbj state\n"
        "/bj recover\n"
        "/rbj recover\n"
        "/bj refund\n"
        "/rbj refund"
    ),
    (
        "🎰 Casino Admin 4\n"
        "/bj forcefinish\n"
        "/rbj forcefinish\n"
        "/casino reset\n"
        "/confirmcasinoreset <code>"
    ),
]

BANK_ADMIN_HELP_PAGES = [
    (
        "🏦 Bank Staff 1\n"
        "/viewtx <user>\n"
        "/bankwatch <user>\n"
        "/ledger <user>\n"
        "/auditbank <user>"
    ),
    (
        "🏦 Bank Admin 2\n"
        "/bankblock <user>\n"
        "/bankunblock <user>\n"
        "/banksettings\n"
        "/setsendlimit <amt>"
    ),
    (
        "🏦 Bank Admin 3\n"
        "/setminsend <amt>\n"
        "/setmaxsend <amt>\n"
        "/setnewaccountdays <days>\n"
        "/setminlevelsend <lvl>"
    ),
    (
        "🏦 Bank Admin 4\n"
        "/setmintotalearned <amt>\n"
        "/setmindailyclaims <amt>\n"
        "/setsendtax <percent>\n"
        "/sethighriskblocks on/off"
    ),
]

REP_HELP = (
    "⭐ Reputation\n"
    "/rep <user>\n"
    "/reputation\n"
    "/repstats\n"
    "/toprep\n"
    "/repleaderboard"
)

AUTO_HELP = (
    "🤖 Auto Systems\n"
    "/autogames status\n"
    "/autoevents status\n"
    "/gameconfig\n"
    "Staff can enable/disable."
)

VIP_HELP_PAGES = [
    (
        "💎 VIP\n"
        "/vipshop\n"
        "/buyvip\n"
        "/vipstatus\n"
        "VIP can join VIP gold rain.\n"
        "Staff: /addvip /removevip /vips"
    ),
    (
        "💎 VIP Staff\n"
        "/addvip <user>\n"
        "/removevip <user>\n"
        "/vips\n"
        "Owner: /goldrainvip <amt>"
    ),
]

TIP_HELP = (
    "💰 Tips\n"
    "Tip the bot gold to get coins.\n"
    "/tiprate\n"
    "/tipstats\n"
    "/tipleaderboard\n"
    "Min tip reward: 10g"
)

ROLES_HELP = (
    "Roles\n"
    "Owner: all\n"
    "Admin: economy/staff\n"
    "Manager: games/events\n"
    "Mod: reports/reset\n"
    "Use /allstaff"
)

AUDIT_HELP_TEXT = (
    "🔍 Audit\n"
    "/audit <user>\n"
    "/auditbank <user>\n"
    "/auditcasino <user>\n"
    "/auditeconomy <user>\n"
    "/ledger <user>"
)

REPORT_HELP_PAGES = [
    (
        "🚩 Reports\n"
        "/report <user> <reason>\n"
        "/bug <message>\n"
        "/myreports"
    ),
    (
        "🚩 Staff Reports\n"
        "/reports\n"
        "/reportinfo <id>\n"
        "/closereport <id>\n"
        "/reportwatch <user>"
    ),
]

MAINTENANCE_HELP_TEXT = (
    "🛠️ Maintenance\n"
    "/botstatus\n"
    "/dbstats\n"
    "/backup\n"
    "/maintenance on/off\n"
    "/reloadsettings\n"
    "/cleanup\n"
    "/softrestart"
)

# ── Staff help texts ──────────────────────────────────────────────────────────

STAFF_HELP_TEXT = (
    "⚙️ Staff Menus\n"
    "/modhelp\n"
    "/managerhelp\n"
    "/adminhelp\n"
    "/ownerhelp\n"
    "/casinoadminhelp\n"
    "/bankadminhelp"
)

STAFF_HELP_TEXT_2 = (
    "⚙️ Staff 2\n"
    "Gold: /goldhelp\n"
    "Audit: /audithelp\n"
    "Reports: /reporthelp\n"
    "Maintenance: /maintenancehelp"
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
        "/casinosettings\n"
        "/casinolimits\n"
        "/casinotoggles\n"
        "/setbjturntimer <sec>\n"
        "/setrbjturntimer <sec>"
    ),
    (
        "🧰 Manager 3\n"
        "/setbjlimits min max win loss\n"
        "/setrbjlimits min max win loss\n"
        "/bj settings\n"
        "/rbj settings"
    ),
    (
        "🧰 Manager 4\n"
        "/startevent <id>\n"
        "/stopevent\n"
        "/autogames on/off\n"
        "/autoevents on/off\n"
        "/gameconfig"
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
        "/banksettings\n"
        "/setsendlimit <amt>\n"
        "/setnewaccountdays <days>\n"
        "/setminlevelsend <lvl>"
    ),
    (
        "🛡️ Admin 3\n"
        "/addmanager <user>\n"
        "/removemanager <user>\n"
        "/managers\n"
        "/addmoderator <user>\n"
        "/removemoderator <user>"
    ),
    (
        "🛡️ Admin 4\n"
        "/dbstats\n"
        "/maintenance on/off\n"
        "/reloadsettings\n"
        "/cleanup\n"
        "/audithelp"
    ),
]

OWNER_HELP_PAGES = [
    (
        "👑 Owner 1\n"
        "/addowner <user>\n"
        "/removeowner <user>\n"
        "/owners\n"
        "/addadmin <user>\n"
        "/removeadmin <user>"
    ),
    (
        "👑 Owner 2\n"
        "/admins\n"
        "/allstaff\n"
        "/allcommands\n"
        "/backup\n"
        "/softrestart\n"
        "/restartbot"
    ),
    (
        "👑 Owner 3\n"
        "/goldhelp\n"
        "/goldtip\n"
        "/goldrain\n"
        "/goldrefund\n"
        "Full bot control."
    ),
]

ALLCMDS = [
    "Cmds 1 Help\n/help /gamehelp /casinohelp\n/coinhelp /bankhelp\n/shophelp /profilehelp",
    "Cmds 2 Games\n/trivia /scramble /riddle\n/answer /coinflip\n/autogames status\n/gameconfig",
    "Cmds 3 Casino\n/bj join/hit/stand/table\n/rbj join/hit/stand/table\n/bj rules /rbj rules",
    "Cmds 4 Casino Staff\n/casinosettings\n/casinolimits\n/casinotoggles\n/setbjlimits\n/setrbjlimits",
    "Cmds 5 Bank\n/send /bank /bankstats\n/transactions\n/banknotify\n/tiprate /tipstats",
    "Cmds 6 Shop/Profile\n/shop titles/badges\n/titleinfo /badgeinfo\n/buy /equip\n/profile /level",
    "Cmds 7 Progress\n/quests /dailyquests\n/weeklyquests\n/claimquest\n/achievements\n/reputation",
    "Cmds 8 Events\n/event /events\n/eventstatus\n/startevent\n/stopevent\n/autoevents status",
    "Cmds 9 Staff\n/staffhelp /modhelp\n/managerhelp /adminhelp\n/ownerhelp /allstaff",
    "Cmds 10 Gold/Owner\n/goldhelp\n/goldtip\n/goldrain\n/goldrefund\n/backup\n/softrestart",
]


# ---------------------------------------------------------------------------
# Module-level helpers for casino and manager commands
# ---------------------------------------------------------------------------

# Pending /casino reset confirmations: {user_id: {"code": str, "ts": float}}
_pending_casino_reset: dict = {}


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
        # If either BJ or RBJ has an active table, require confirmation
        from modules.blackjack           import _state as _bj_state
        from modules.realistic_blackjack import _state as _rbj_state
        bj_active  = _bj_state.phase  != "idle"
        rbj_active = _rbj_state.phase != "idle"
        if bj_active or rbj_active:
            import random, string
            code = "".join(random.choices(string.digits, k=4))
            _pending_casino_reset[user.id] = {
                "code": code,
                "ts":   asyncio.get_event_loop().time(),
            }
            tables = []
            if bj_active:
                tables.append(f"BJ ({_bj_state.phase})")
            if rbj_active:
                tables.append(f"RBJ ({_rbj_state.phase})")
            await bot.highrise.send_whisper(
                user.id,
                f"⚠️ Active tables: {', '.join(tables)}.\n"
                f"To confirm reset+refund, type:\n"
                f"/confirmcasinoreset {code}\n"
                "(expires in 60s)"
            )
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
    await bot.highrise.send_whisper(user.id, STAFF_HELP_TEXT_2)


def _paged_send(pages):
    """Return a helper coroutine that whispers one or all pages."""
    async def _inner(bot, user, args):
        page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
        n = len(pages)
        if page == 0:
            for p in pages:
                await bot.highrise.send_whisper(user.id, p)
        elif 1 <= page <= n:
            await bot.highrise.send_whisper(user.id, pages[page - 1])
        else:
            await bot.highrise.send_whisper(user.id, f"Pages 1-{n}.")
    return _inner


_handle_gamehelp        = _paged_send(GAME_HELP_PAGES)
_handle_casinohelp      = _paged_send(CASINO_HELP_PAGES)
_handle_bankhelp        = _paged_send(BANK_HELP_PAGES)
_handle_shophelp        = _paged_send(SHOP_HELP_PAGES)
_handle_eventhelp_paged = _paged_send(EVENT_HELP_PAGES)
_handle_bjhelp          = _paged_send(BJ_HELP_PAGES)
_handle_rbjhelp         = _paged_send(RBJ_HELP_PAGES)


async def _handle_casinoadminhelp(bot, user, args):
    if not can_manage_games(user.username):
        await bot.highrise.send_whisper(user.id, "Managers and above only.")
        return
    await _paged_send(CASINO_ADMIN_HELP_PAGES)(bot, user, args)


async def _handle_bankadminhelp(bot, user, args):
    if not can_moderate(user.username):
        await bot.highrise.send_whisper(user.id, "Staff only.")
        return
    is_admin_plus = can_manage_economy(user.username)
    if not is_admin_plus:
        await bot.highrise.send_whisper(user.id, BANK_ADMIN_HELP_PAGES[0])
        return
    await _paged_send(BANK_ADMIN_HELP_PAGES)(bot, user, args)


async def _handle_maintenancehelp(bot, user):
    if not can_manage_economy(user.username):
        await bot.highrise.send_whisper(user.id, "Maintenance help is admin only.")
        return
    await bot.highrise.send_whisper(user.id, MAINTENANCE_HELP_TEXT)


async def _handle_viphelp(bot, user, args):
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
    n = len(VIP_HELP_PAGES)
    if page == 0:
        await bot.highrise.send_whisper(user.id, VIP_HELP_PAGES[0])
        if can_moderate(user.username):
            await bot.highrise.send_whisper(user.id, VIP_HELP_PAGES[1])
    elif 1 <= page <= n:
        if page == 2 and not can_moderate(user.username):
            await bot.highrise.send_whisper(user.id, "Staff only.")
        else:
            await bot.highrise.send_whisper(user.id, VIP_HELP_PAGES[page - 1])
    else:
        await bot.highrise.send_whisper(user.id, f"Pages 1-{n}.")


async def _handle_tiphelp(bot, user):
    await bot.highrise.send_whisper(user.id, TIP_HELP)


async def _handle_rephelp(bot, user):
    await bot.highrise.send_whisper(user.id, REP_HELP)


async def _handle_autohelp(bot, user):
    await bot.highrise.send_whisper(user.id, AUTO_HELP)


async def _handle_roleshelp(bot, user):
    await bot.highrise.send_whisper(user.id, ROLES_HELP)


async def _handle_audithelp_cmd(bot, user):
    if not can_moderate(user.username):
        await bot.highrise.send_whisper(user.id, "Staff only.")
        return
    await bot.highrise.send_whisper(user.id, AUDIT_HELP_TEXT)


async def _handle_reporthelp_cmd(bot, user, args):
    await bot.highrise.send_whisper(user.id, REPORT_HELP_PAGES[0])
    if can_moderate(user.username):
        await bot.highrise.send_whisper(user.id, REPORT_HELP_PAGES[1])


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
# Bank notification delivery helper
# ---------------------------------------------------------------------------

# Track per-user delivery to avoid spamming on every chat message
_notif_delivered_this_session: set[str] = set()


async def _deliver_pending_bank_notifications(bot, user: User) -> None:
    """Deliver any queued bank notifications to *user* via whisper."""
    username = user.username.lower().strip()
    pending = db.get_pending_bank_notifications(username)
    if not pending:
        return
    try:
        if len(pending) == 1:
            r = pending[0]
            fee_note = f" Fee: {r['fee']}c." if r["fee"] else ""
            msg = (
                f"🏦 You received {r['amount_received']:,}c from @{r['sender_username']}."
                f"{fee_note}"
            )[:249]
            await bot.highrise.send_whisper(user.id, msg)
        elif len(pending) <= 3:
            total = sum(r["amount_received"] for r in pending)
            lines = [f"🏦 {len(pending)} deposits while you were away:"]
            for r in pending:
                fee_note = f" Fee:{r['fee']}c" if r["fee"] else ""
                lines.append(f"+{r['amount_received']:,}c from @{r['sender_username']}{fee_note}")
            await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])
        else:
            total = sum(r["amount_received"] for r in pending)
            msg = (
                f"🏦 You have {len(pending)} bank deposits. "
                f"Total: {total:,}c. Use /notifications or /transactions."
            )[:249]
            await bot.highrise.send_whisper(user.id, msg)
        db.mark_bank_notifications_delivered(username)
        _notif_delivered_this_session.add(username)
    except Exception as exc:
        print(f"[BANK] Could not deliver pending notification to @{username}: {exc}")


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
        print(f"[HangoutBot] SDK version: {_TIP_SDK_VERSION}")
        print(f"[HangoutBot] Run command: cd artifacts/highrise-bot && python3 bot.py")
        # Store bot identity so gold rain / tip receiver-check can use it
        set_bot_identity(session_metadata.user_id)
        print(f"[HangoutBot] Bot user ID: {session_metadata.user_id}")
        # Log which events this session is subscribed to (only overridden hooks)
        try:
            from highrise.__main__ import gather_subscriptions
            subs = gather_subscriptions(self)
            print(f"[HangoutBot] Event subscriptions: {subs or '(all)'}")
        except Exception:
            pass
        # Seed the room user cache from the live room list
        asyncio.create_task(refresh_room_cache(self))
        await self.highrise.chat("Mini Game Bot is online! Type /help for commands.")
        # Recover any event that was running before a bot restart
        asyncio.create_task(startup_event_check(self))
        # Recover any BJ/RBJ tables that were active before last shutdown
        asyncio.create_task(startup_bj_recovery(self))
        asyncio.create_task(startup_rbj_recovery(self))
        # Start background automation loops (idempotent — safe on reconnect)
        start_auto_game_loop(self)
        start_auto_event_loop(self)

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

        # ── Deliver queued bank notifications on first command this session ──
        _uname = user.username.lower().strip()
        if _uname not in _notif_delivered_this_session:
            asyncio.create_task(_deliver_pending_bank_notifications(self, user))

        # ── /help ─────────────────────────────────────────────────────────────
        if cmd == "help":
            await self.highrise.send_whisper(user.id, HELP_TEXT)
            if can_moderate(user.username):
                await self.highrise.send_whisper(
                    user.id,
                    "⚙️ Staff: /staffhelp\nOwner/Admin: /adminhelp\nGold: /goldhelp"
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

            if cmd == "setrbjlimits":
                await handle_setrbjlimits(self, user, args)
            elif cmd == "setbjlimits":
                await handle_setbjlimits(self, user, args)
            elif cmd.startswith("setrbj"):
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
            elif cmd == "casinosettings":
                await handle_casinosettings(self, user, args)
            elif cmd == "casinolimits":
                await handle_casinolimits(self, user)
            elif cmd == "casinotoggles":
                await handle_casinotoggles(self, user, args)
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
            elif cmd == "settiprate":
                await handle_settiprate(self, user, args)
            elif cmd == "settipcap":
                await handle_settipcap(self, user, args)
            elif cmd == "settiptier":
                await handle_settiptier(self, user, args)
            elif cmd == "setgametimer":
                await handle_setgametimer(self, user, args)
            elif cmd == "setautogameinterval":
                await handle_setautogameinterval(self, user, args)
            elif cmd == "setautoeventinterval":
                await handle_setautoeventinterval(self, user, args)
            elif cmd == "setautoeventduration":
                await handle_setautoeventduration(self, user, args)
            elif cmd == "gameconfig":
                await handle_gameconfig(self, user)
            elif cmd == "goldtip":
                await handle_goldtip(self, user, args)
            elif cmd == "goldrefund":
                await handle_goldrefund(self, user, args)
            elif cmd == "goldrain":
                await handle_goldrain(self, user, args)
            elif cmd == "goldrainall":
                await handle_goldrainall(self, user, args)
            elif cmd == "goldwallet":
                await handle_goldwallet(self, user, args)
            elif cmd == "goldtips":
                await handle_goldtips(self, user, args)
            elif cmd == "goldtx":
                await handle_goldtx(self, user, args)
            elif cmd == "pendinggold":
                await handle_pendinggold(self, user, args)
            elif cmd == "confirmgoldtip":
                await handle_confirmgoldtip(self, user, args)
            elif cmd == "setgoldrainstaff":
                await handle_setgoldrainstaff(self, user, args)
            elif cmd == "setgoldrainmax":
                await handle_setgoldrainmax(self, user, args)
            elif cmd == "debugtips":
                await handle_debugtips(self, user, args)
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

        # ── Auto game / event toggle commands (public status, manager+ on/off) ─
        elif cmd == "autogames":
            await handle_autogames(self, user, args)

        elif cmd == "autoevents":
            await handle_autoevents(self, user, args)

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

        elif cmd == "confirmcasinoreset":
            if not can_moderate(user.username):
                await self.highrise.send_whisper(user.id, "Staff only.")
            else:
                pending = _pending_casino_reset.get(user.id)
                if not pending:
                    await self.highrise.send_whisper(
                        user.id, "No pending casino reset. Use /casino reset first."
                    )
                elif asyncio.get_event_loop().time() - pending["ts"] > 60:
                    _pending_casino_reset.pop(user.id, None)
                    await self.highrise.send_whisper(
                        user.id, "Confirmation expired. Use /casino reset again."
                    )
                elif len(args) < 2 or args[1] != pending["code"]:
                    await self.highrise.send_whisper(
                        user.id, f"Wrong code. Expected: /confirmcasinoreset {pending['code']}"
                    )
                else:
                    _pending_casino_reset.pop(user.id, None)
                    bj_reset_table()
                    rbj_reset_table()
                    poker_reset_table()
                    await self.highrise.chat("✅ Casino tables reset (BJ, RBJ, Poker). Bets refunded.")

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

        elif cmd == "notifications":
            await handle_notifications(self, user)

        elif cmd == "clearnotifications":
            await handle_clearnotifications(self, user)

        elif cmd == "bankhelp":
            await _handle_bankhelp(self, user, args)

        elif cmd == "casinohelp":
            await _handle_casinohelp(self, user, args)

        elif cmd == "gamehelp":
            await _handle_gamehelp(self, user, args)

        elif cmd == "coinhelp":
            await self.highrise.send_whisper(user.id, COIN_HELP)

        elif cmd == "profilehelp":
            await self.highrise.send_whisper(user.id, PROFILE_HELP)

        elif cmd == "shophelp":
            await _handle_shophelp(self, user, args)

        elif cmd == "progresshelp":
            await self.highrise.send_whisper(user.id, PROGRESS_HELP)

        elif cmd == "eventhelp":
            await _handle_eventhelp_paged(self, user, args)

        elif cmd == "bjhelp":
            await _handle_bjhelp(self, user, args)

        elif cmd == "rbjhelp":
            await _handle_rbjhelp(self, user, args)

        elif cmd == "rephelp":
            await _handle_rephelp(self, user)

        elif cmd == "autohelp":
            await _handle_autohelp(self, user)

        elif cmd == "viphelp":
            await _handle_viphelp(self, user, args)

        elif cmd == "tiphelp":
            await _handle_tiphelp(self, user)

        elif cmd == "roleshelp":
            await _handle_roleshelp(self, user)

        elif cmd == "audithelp":
            await _handle_audithelp_cmd(self, user)

        elif cmd == "reporthelp":
            await _handle_reporthelp_cmd(self, user, args)

        elif cmd == "maintenancehelp":
            await _handle_maintenancehelp(self, user)

        elif cmd == "casinoadminhelp":
            await _handle_casinoadminhelp(self, user, args)

        elif cmd == "bankadminhelp":
            await _handle_bankadminhelp(self, user, args)

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

        elif cmd == "goldhelp":
            await handle_goldhelp(self, user, args)

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

        # ── Tip system ───────────────────────────────────────────────────────
        elif cmd == "tiprate":
            await handle_tiprate(self, user, args)

        elif cmd == "tipstats":
            await handle_tipstats(self, user, args)

        elif cmd == "tipleaderboard":
            await handle_tipleaderboard(self, user, args)

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

        elif cmd == "softrestart":
            await handle_softrestart(self, user)

        elif cmd == "restartbot":
            await handle_restartbot(self, user)

        # ── Game commands ─────────────────────────────────────────────────────
        elif cmd in GAME_COMMANDS:
            await handle_game_command(self, user, cmd, args)

        # ── Unknown command ───────────────────────────────────────────────────
        else:
            if cmd.startswith("gold") and not can_manage_economy(user.username):
                await self.highrise.send_whisper(user.id, "Gold commands are staff only.")
            elif cmd in STAFF_CMDS and not can_moderate(user.username):
                await self.highrise.send_whisper(user.id, "Staff command unavailable. Type /help.")
            else:
                await self.highrise.send_whisper(user.id, "Unknown command. Type /help.")

    async def on_user_join(self, user: User, position) -> None:
        """Register new players and greet them when they enter the room."""
        db.ensure_user(user.id, user.username)
        add_to_room_cache(user.id, user.username)
        await self.highrise.chat(
            f"Welcome, @{user.username}! Type /help to see what you can do. "
            "Use /daily to grab your free coins!"
        )
        # Deliver any queued bank notifications
        asyncio.create_task(_deliver_pending_bank_notifications(self, user))

    async def on_tip(self, sender: User, receiver: User, tip) -> None:
        """
        Official Highrise SDK tip handler.
        Maps to the 'tip_reaction' WebSocket event.
        SDK: on_tip(sender, receiver, tip: CurrencyItem | Item)

        If this NEVER prints, Highrise is not delivering tip_reaction to the bot.
        Nothing here is echoed to room chat.
        """
        # ABSOLUTE FIRST LINE — no try/except wrapping, runs unconditionally
        print("DEBUG EVENT FIRED: on_tip")
        print(f"  sender:    {sender.username} ({sender.id})")
        print(f"  receiver:  {receiver.username} ({receiver.id})")
        print(f"  tip raw:   {tip!r}")
        print(f"  tip class: {type(tip).__name__}")
        print(f"  tip.type:  {getattr(tip, 'type',   '?')}")
        print(f"  tip.amount:{getattr(tip, 'amount', '?')}")
        print(f"  tip.id:    {getattr(tip, 'id',     'n/a')}")

        record_debug_any_event("on_tip", repr(tip))

        # Only process tips directed at this bot
        bot_uid = get_bot_user_id()
        if bot_uid and receiver.id != bot_uid:
            print(
                f"  [TIP] Receiver {receiver.username}({receiver.id}) "
                f"!= bot ({bot_uid}) — ignoring (tip between players)."
            )
            return

        try:
            await process_tip_event(self, sender, receiver, tip)
        except Exception as exc:
            print(f"  [TIP] EXCEPTION in process_tip_event: {exc!r}")

    async def on_user_leave(self, user: User) -> None:
        """Log when a player leaves and remove from gold room cache."""
        remove_from_room_cache(user.id)
        print(f"[HangoutBot] {user.username} left.")

    async def on_reaction(self, user: User, reaction: str, receiver: User) -> None:
        """
        Debug hook — subscribed so the Highrise server sends reaction events.
        Logs every reaction so we can see if gold tips arrive here instead of on_tip.
        """
        raw = f"reaction={reaction!r} from=@{user.username}({user.id}) to=@{receiver.username}({receiver.id})"
        print(f"DEBUG EVENT FIRED: on_reaction | {raw}")
        record_debug_any_event("on_reaction", raw)

    async def on_channel(self, sender_id: str, message: str, tags: set) -> None:
        """
        Debug hook — subscribed so the Highrise server sends channel events.
        Only logs gold/tip/coin-related messages to avoid console spam.
        """
        raw = f"sender_id={sender_id} tags={tags} message={message[:80]!r}"
        record_debug_any_event("on_channel", raw)
        msg_lower = message.lower()
        if any(kw in msg_lower for kw in ("gold", "tip", "coin", "pay", "send", "reward")):
            print(f"DEBUG EVENT FIRED: on_channel | {raw}")

    async def on_emote(self, user: User, emote_id: str, receiver) -> None:
        """
        Debug hook — overriding BaseBot adds 'emote' to subscriptions.
        Logs emotes silently; only prints if emote ID looks tip-related.
        """
        raw = f"user=@{user.username}({user.id}) emote_id={emote_id!r} receiver={receiver!r}"
        record_debug_any_event("on_emote", raw)
        # Only log emote events to console; very high volume, keep quiet
        # unless explicitly tip-related
        if "tip" in emote_id.lower() or "gold" in emote_id.lower():
            print(f"DEBUG EVENT FIRED: on_emote | {raw}")

    async def on_whisper(self, user: User, message: str) -> None:
        """
        Debug hook — overriding BaseBot adds whisper to 'chat' subscription.
        Whispers do not go through on_chat; this is the dedicated handler.
        Does NOT process bot commands (whispers are not a command surface).
        """
        raw = f"from=@{user.username}({user.id}) message={message[:60]!r}"
        record_debug_any_event("on_whisper", raw)
        # Log whispers silently; only print if gold/tip related
        msg_lower = message.lower()
        if any(kw in msg_lower for kw in ("gold", "tip", "coin")):
            print(f"DEBUG EVENT FIRED: on_whisper | {raw}")


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
