"""
modules/cmd_audit.py
--------------------
Command audit and silent-command checker.  Owner / admin only.

Commands
--------
/checkcommands          — quick route-coverage summary
/checkhelp              — help vs routed diff
/missingcommands        — in help but not explicitly routed
/routecheck             — explicitly routed but not mentioned in help
/silentcheck            — commands at risk of giving no reply
/commandtest <cmd>      — check whether one command is routed + help-listed

All messages ≤ 249 chars.
"""

from highrise import BaseBot, User
from modules.permissions import is_admin, is_owner


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _w(bot, uid: str, msg: str):
    return bot.highrise.send_whisper(uid, msg[:249])


def _can_audit(username: str) -> bool:
    return is_admin(username) or is_owner(username)


def _fmt_list(title: str, items, prefix: str = "/") -> str:
    """Return up to 249-char string listing items with a count suffix."""
    sorted_items = sorted(items)
    parts: list[str] = []
    base = f"{title}: "
    budget = 248 - len(base)
    for item in sorted_items:
        token = f"{prefix}{item}"
        if parts:
            token = ", " + token
        if len("".join(parts)) + len(token) > budget:
            remaining = len(sorted_items) - len(parts)
            parts.append(f" +{remaining} more")
            break
        parts.append(token if not parts else f", {prefix}{item}")
    if not parts:
        parts = ["none"]
    return (base + "".join(parts))[:249]


# ---------------------------------------------------------------------------
# ROUTED_COMMANDS
# Every command that has an explicit if/elif branch in on_chat().
# Keep this in sync with main.py whenever new commands are added.
# ---------------------------------------------------------------------------

ROUTED_COMMANDS: frozenset[str] = frozenset({
    # ── help ─────────────────────────────────────────────────────────────────
    "help",
    # ── staff gate — setbj* / setrbj* (startswith match) ────────────────────
    "setbjminbet", "setbjmaxbet", "setbjcountdown", "setbjturntimer",
    "setbjactiontimer", "setbjmaxsplits",
    "setbjdailywinlimit", "setbjdailylosslimit",
    "setrbjdecks", "setrbjminbet", "setrbjmaxbet", "setrbjcountdown",
    "setrbjshuffle", "setrbjblackjackpayout", "setrbjwinpayout",
    "setrbjturntimer", "setrbjactiontimer", "setrbjmaxsplits",
    "setrbjdailywinlimit", "setrbjdailylosslimit",
    "setrbjlimits", "setbjlimits",
    # ── staff gate — roles ───────────────────────────────────────────────────
    "addowner", "removeowner",
    "addmanager", "removemanager",
    "addmoderator", "removemoderator",
    "addadmin", "removeadmin",
    # ── staff gate — misc ────────────────────────────────────────────────────
    "admins", "allstaff", "allcommands",
    "bankblock", "bankunblock", "banksettings",
    "casinosettings", "casinolimits", "casinotoggles",
    "ledger", "viewtx", "bankwatch",
    "setminsend", "setmaxsend", "setsendlimit", "setnewaccountdays",
    "setminlevelsend", "setmintotalearned", "setmindailyclaims",
    "setsendtax", "sethighriskblocks",
    "audithelp", "audit", "auditbank", "auditcasino", "auditeconomy",
    "economysettings", "setdailycoins", "setgamereward",
    "setmaxbalance", "settransferfee",
    "mute", "unmute", "mutes",
    "warn", "warnings", "clearwarnings",
    "setrules", "automod",
    "reports", "reportinfo", "closereport", "reportwatch",
    "profileadmin", "profileprivacy", "resetprofileprivacy",
    "replog", "addrep", "removerep",
    "settiprate", "settipcap", "settiptier", "settipautosub", "settipresubscribe",
    "setgametimer", "setautogameinterval",
    "setautoeventinterval", "setautoeventduration", "gameconfig",
    "goldtip", "goldrefund", "goldrain", "goldrainall", "goldraineligible",
    "goldrainrole", "goldrainvip", "goldraintitle", "goldrainbadge", "goldrainlist",
    "goldwallet", "goldtips", "goldtx", "pendinggold", "confirmgoldtip",
    "setgoldrainstaff", "setgoldrainmax",
    "debugtips",
    "restarthelp", "restartstatus", "softrestart", "restartbot",
    "healthcheck",
    "announce_vip", "announce_staff",
    "debugsub",
    "notifyuser", "broadcasttest",
    "notifystats", "notifyprefs",
    "debugnotify", "testnotify", "testnotifyall",
    "pendingnotify", "clearpendingnotify",
    "dailyadmin",
    "setpokertimer", "setpokerturntimer", "setpokerlobbytimer",
    "setpokerbuyin", "setpokerplayers", "setpokerraise",
    "setpokerdailywinlimit", "setpokerdailylosslimit",
    "resetpokerlimits", "resetbjlimits", "resetrbjlimits",
    "setpokerlimits",
    "pokerdebug", "pokerfix", "pokerrefundall", "pokercleanup",
    "setcoins", "editcoins", "resetcoins",
    "addeventcoins", "removeeventcoins", "seteventcoins", "reseteventcoins",
    "addxp", "removexp", "setxp", "resetxp", "setlevel", "addlevel",
    "setrep", "resetrep",
    "givetitle", "removetitle", "givebadge", "removebadge",
    "addvip", "removevip", "vips",
    "resetbjstats", "resetrbjstats", "resetpokerstats", "resetcasinostats",
    "adminpanel", "adminlogs", "checkhelp",
    # ── new audit commands ───────────────────────────────────────────────────
    "checkcommands", "missingcommands", "routecheck", "silentcheck", "commandtest",
    # ── public block ─────────────────────────────────────────────────────────
    "rules",
    "balance", "bal", "b", "coins", "coin", "money",
    "wallet", "w",
    "casinodash", "mycasino",
    "dashboard", "dash",
    "daily",
    "leaderboard",
    "profile", "me", "whois", "pinfo",
    "stats",
    "badges", "titles",
    "privacy",
    "level",
    "xpleaderboard",
    "shop", "buy", "equip", "myitems", "badgeinfo", "titleinfo",
    "event", "events", "eventhelp", "eventstatus",
    "startevent", "stopevent",
    "eventpoints", "eventshop", "buyevent",
    "autogames", "autoevents",
    "achievements", "claimachievements",
    "bj", "rbj",
    "bjoin", "bt", "bh", "bs", "bd", "bsp", "blimits", "bstats", "bhand",
    "bjh", "bjs", "bjd", "bjsp", "bjhand",
    "rjoin", "rt", "rh", "rs", "rd", "rsp", "rshoe", "rlimits", "rstats", "rhand",
    "rbjh", "rbjs", "rbjd", "rbjsp", "rbjhand",
    "confirmcasinoreset",
    "bank", "send", "transactions", "bankstats", "banknotify",
    "notifications", "clearnotifications",
    "notifysettings", "notify", "notifyhelp",
    "delivernotifications", "pendingnotifications",
    "subscribe", "unsubscribe", "substatus", "subscribers",
    "dmnotify", "announce_subs", "subhelp",
    "bankhelp", "casinohelp", "gamehelp", "coinhelp", "profilehelp",
    "shophelp", "progresshelp", "bjhelp", "rbjhelp",
    "rephelp", "autohelp", "vipstatus", "viphelp", "tiphelp", "roleshelp",
    "maintenancehelp",
    "mycommands", "helpsearch",
    "casinoadminhelp", "bankadminhelp",
    "staffhelp", "modhelp", "managerhelp", "adminhelp", "ownerhelp",
    "goldhelp",
    "questhelp", "quests", "dailyquests", "weeklyquests", "claimquest",
    "casino",
    "owners", "managers", "moderators",
    "answer",
    "report", "bug", "myreports",
    "reputation", "repstats", "rep",
    "toprep", "repleaderboard",
    "tiprate", "tipstats", "tipleaderboard",
    "p", "pj", "pt", "ph", "po", "pcards", "podds", "ptable",
    "check", "ch", "call", "ca", "raise", "r", "fold", "f",
    "allin", "ai", "shove", "all-in",
    "pp", "pplayers",
    "pstats", "pokerstats",
    "plb", "pleaderboard", "pokerlb", "pokerleaderboard",
    "phelp",
    "sitout", "sitin", "rebuy", "pstacks", "mystack",
    "poker", "pokerhelp",
    "setpokerbuyin", "setpokerplayers", "setpokerlobbytimer", "setpokertimer",
    "setpokerraise", "setpokerdailywinlimit", "setpokerdailylosslimit",
    "resetpokerlimits", "setpokerturntimer",
    "setpokerblinds", "setpokerante", "setpokernexthandtimer",
    "setpokermaxstack", "setpokeridlestrikes",
    "botstatus", "dbstats", "backup",
    "maintenance", "reloadsettings", "cleanup",
    "trivia", "scramble", "riddle", "coinflip",
})

# ---------------------------------------------------------------------------
# HELP_CMDS
# Commands referenced in any help page or help text in main.py.
# ---------------------------------------------------------------------------

HELP_CMDS: frozenset[str] = frozenset({
    # help index
    "help", "gamehelp", "casinohelp", "coinhelp", "bankhelp",
    "shophelp", "profilehelp", "progresshelp", "eventhelp",
    "bjhelp", "rbjhelp", "rephelp", "autohelp", "viphelp", "tiphelp",
    "roleshelp", "staffhelp", "modhelp", "managerhelp", "adminhelp",
    "ownerhelp", "casinoadminhelp", "bankadminhelp", "audithelp",
    "reporthelp", "maintenancehelp", "questhelp", "goldhelp",
    # games
    "trivia", "scramble", "riddle", "answer", "coinflip",
    "autogames", "setgametimer", "setautogameinterval", "gameconfig",
    # casino
    "bjoin", "bh", "bs", "bd", "bsp", "bt", "bhand",
    "rjoin", "rh", "rs", "rd", "rsp", "rt", "rhand", "rshoe",
    "p", "casino", "mycasino",
    "bstats", "rstats", "rlimits", "blimits",
    "bj", "rbj",
    "casinosettings", "casinolimits", "casinotoggles",
    "setbjlimits", "setrbjlimits", "setbjactiontimer", "setrbjactiontimer",
    "setbjmaxsplits", "setrbjmaxsplits",
    "resetbjlimits", "resetrbjlimits",
    "resetbjstats", "resetrbjstats", "resetpokerstats", "resetcasinostats",
    # poker
    "setpokerbuyin", "setpokerplayers", "setpokertimer", "setpokerraise",
    "setpokerdailywinlimit", "setpokerdailylosslimit", "resetpokerlimits",
    "poker", "pokerhelp",
    # bank
    "send", "bank", "bankstats", "transactions", "banknotify",
    "viewtx", "bankwatch", "ledger", "auditbank", "banksettings",
    "bankblock", "bankunblock",
    "setminsend", "setmaxsend", "setsendlimit", "setminlevelsend",
    "setmintotalearned", "setmindailyclaims", "setsendtax", "sethighriskblocks",
    "notifications", "clearnotifications",
    # coin
    "balance", "bal", "daily", "wallet", "leaderboard", "tiprate",
    "tipstats", "tipleaderboard",
    # profile
    "profile", "whois", "me", "stats", "badges", "titles", "privacy", "dashboard",
    # shop
    "shop", "titleinfo", "badgeinfo", "buy", "equip", "myitems",
    "vipshop", "buyvip", "vipstatus",
    # progress
    "quests", "dailyquests", "weeklyquests", "claimquest",
    "achievements", "claimachievements",
    # rep
    "rep", "reputation", "repstats", "toprep", "repleaderboard",
    # events
    "event", "events", "eventstatus", "eventpoints", "eventshop",
    "startevent", "stopevent", "autoevents",
    "setautoeventinterval", "setautoeventduration",
    # report
    "report", "bug", "myreports",
    "reports", "reportinfo", "closereport", "reportwatch",
    # vip
    "addvip", "removevip", "vips", "goldrainvip",
    # tip
    "settiprate", "settipcap", "settiptier",
    # gold
    "goldtip", "goldrain", "goldrainall", "goldrefund", "goldrainvip",
    # admin power
    "addcoins", "removecoins", "setcoins", "resetcoins",
    "addxp", "removexp", "setxp", "setlevel", "addlevel",
    "addrep", "removerep", "setrep", "resetrep", "addeventcoins",
    "givetitle", "removetitle", "givebadge", "removebadge",
    "addmanager", "removemanager", "addmoderator", "removemoderator",
    "allstaff",
    "adminlogs", "adminpanel", "checkhelp",
    "dbstats", "maintenance", "bankblock",
    # staff/mod
    "announce", "resetgame", "setrules", "rules", "dailyadmin",
    "warn", "warnings", "mute", "unmute", "mutes",
    "audit", "healthcheck",
    "addowner", "removeowner", "owners",
    "addadmin", "removeadmin",
    # owner
    "setmaxbalance", "backup", "softrestart", "restartbot",
    # maintenance
    "botstatus", "reloadsettings", "cleanup",
    # auto
    "automod",
    # new audit
    "checkcommands", "checkhelp",
})

# ---------------------------------------------------------------------------
# SILENT_RISK_CMDS
# Commands in STAFF_CMDS that fall to handle_admin_command() fallback, or
# whose handlers may not always send a reply on bad input.
# ---------------------------------------------------------------------------

SILENT_RISK_CMDS: frozenset[str] = frozenset({
    # These are in ADMIN_ONLY_CMDS / STAFF_CMDS but NOT explicitly routed
    # in the staff if/elif chain — they fall to handle_admin_command()
    "addcoins", "removecoins",
    "announce",
    "eventstart", "eventstop",
    "announce_subs",
    "dmnotify",
})


# ---------------------------------------------------------------------------
# /checkcommands
# ---------------------------------------------------------------------------

async def handle_checkcommands(bot: BaseBot, user: User, all_known: set) -> None:
    if not _can_audit(user.username):
        await _w(bot, user.id, "Owner/admin only.")
        return
    routes_ok = len(ROUTED_COMMANDS & all_known)
    missing   = len(all_known - ROUTED_COMMANDS)
    silent    = len(SILENT_RISK_CMDS & all_known)
    print(f"[AUDIT] /checkcommands by @{user.username}: "
          f"routed={routes_ok} missing={missing} silent={silent}")
    msg = (f"Cmd Check: Routes OK {routes_ok} | "
           f"Missing {missing} | Silent risk {silent}")
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# /checkhelp
# ---------------------------------------------------------------------------

async def handle_checkhelp_audit(bot: BaseBot, user: User, all_known: set) -> None:
    if not _can_audit(user.username):
        await _w(bot, user.id, "Owner/admin only.")
        return
    missing_routes  = HELP_CMDS - ROUTED_COMMANDS   # in help but not routed
    missing_help    = ROUTED_COMMANDS - HELP_CMDS    # routed but not in help
    print(f"[AUDIT] /checkhelp by @{user.username}: "
          f"missing_routes={len(missing_routes)} missing_help={len(missing_help)}")
    msg = (f"Help Check: {len(missing_routes)} missing routes | "
           f"{len(missing_help)} missing help entries")
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# /missingcommands
# ---------------------------------------------------------------------------

async def handle_missingcommands(bot: BaseBot, user: User) -> None:
    if not _can_audit(user.username):
        await _w(bot, user.id, "Owner/admin only.")
        return
    missing = HELP_CMDS - ROUTED_COMMANDS
    print(f"[AUDIT] /missingcommands by @{user.username}: {sorted(missing)}")
    if not missing:
        await _w(bot, user.id, "Missing: none — all help-listed commands are routed.")
        return
    await _w(bot, user.id, _fmt_list("Missing", missing))


# ---------------------------------------------------------------------------
# /routecheck
# ---------------------------------------------------------------------------

async def handle_routecheck(bot: BaseBot, user: User) -> None:
    if not _can_audit(user.username):
        await _w(bot, user.id, "Owner/admin only.")
        return
    unlisted = ROUTED_COMMANDS - HELP_CMDS
    print(f"[AUDIT] /routecheck by @{user.username}: {sorted(unlisted)}")
    if not unlisted:
        await _w(bot, user.id, "Unlisted: none — all routed commands are in help.")
        return
    await _w(bot, user.id, _fmt_list("Unlisted", unlisted))


# ---------------------------------------------------------------------------
# /silentcheck
# ---------------------------------------------------------------------------

async def handle_silentcheck(bot: BaseBot, user: User, all_known: set) -> None:
    if not _can_audit(user.username):
        await _w(bot, user.id, "Owner/admin only.")
        return
    silent = SILENT_RISK_CMDS & all_known
    print(f"[AUDIT] /silentcheck by @{user.username}: {sorted(silent)}")
    if not silent:
        await _w(bot, user.id, "Silent risk: none found.")
        return
    await _w(bot, user.id, _fmt_list("Silent risk", silent))


# ---------------------------------------------------------------------------
# /commandtest <command>
# ---------------------------------------------------------------------------

async def handle_commandtest(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_audit(user.username):
        await _w(bot, user.id, "Owner/admin only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /commandtest <command>")
        return
    cmd = args[1].lstrip("/").lower()
    routed = "YES" if cmd in ROUTED_COMMANDS else "NO"
    in_help = "YES" if cmd in HELP_CMDS else "NO"
    silent = " ⚠️ silent risk" if cmd in SILENT_RISK_CMDS else ""
    msg = f"/{cmd} route: {routed} | Help: {in_help}{silent}"
    print(f"[AUDIT] /commandtest {cmd!r} by @{user.username}: routed={routed} help={in_help}")
    await _w(bot, user.id, msg)
