"""
modules/cmd_audit.py
--------------------
Command audit and silent-command checker.  Owner / admin only.

Commands
--------
/checkcommands              — quick route-coverage summary
/checkhelp                  — help vs routed diff
/missingcommands [page]     — in help but not explicitly routed
/routecheck [page]          — explicitly routed but not in help
/silentcheck                — commands with no guaranteed reply
/commandtest <cmd>          — check whether a command is routed + help-listed

All messages ≤ 249 chars.
"""

from highrise import BaseBot, User
from modules.permissions import is_admin, is_owner


# ---------------------------------------------------------------------------
# Permission helper
# ---------------------------------------------------------------------------

def _can_audit(username: str) -> bool:
    return is_admin(username) or is_owner(username)


async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


# ---------------------------------------------------------------------------
# HIDDEN_CMDS — debug/internal only; excluded from /routecheck missing-help
# ---------------------------------------------------------------------------

HIDDEN_CMDS: frozenset[str] = frozenset({
    "debugtips", "debugsub", "debugnotify", "testnotify", "testnotifyall",
    "pendingnotify", "clearpendingnotify", "pendingnotifications",
    "delivernotifications", "broadcasttest", "notifyuser",
    "pokerdebug", "pokerfix", "pokerrefundall",
    "restartstatus", "restarthelp", "softrestart", "restartbot",
    "backup", "dbstats", "botstatus", "reloadsettings", "cleanup",
    "bankwatch", "viewtx", "replog",
    "allcommands", "admins",
    "goldtip", "goldrefund", "goldrain", "goldrainall", "goldraineligible",
    "goldrainrole", "goldrainvip", "goldraintitle", "goldrainbadge",
    "goldrainlist", "goldwallet", "goldtips", "goldtx", "pendinggold",
    "confirmgoldtip", "setgoldrainstaff", "setgoldrainmax",
    "setpokerblinds", "setpokerante", "setpokernexthandtimer",
    "setpokermaxstack", "setpokeridlestrikes",
    "notifystats", "notifyprefs",
    "dailyadmin",
    "confirmcasinoreset",
    "auditbank", "auditcasino", "auditeconomy",
})


# ---------------------------------------------------------------------------
# ROUTED_COMMANDS
# Every command that has an explicit if/elif branch in on_chat(), or is
# dispatched through handle_admin_command() (which always replies).
# ---------------------------------------------------------------------------

ROUTED_COMMANDS: frozenset[str] = frozenset({
    # ── help ─────────────────────────────────────────────────────────────────
    "help",
    # ── setbj* / setrbj* ─────────────────────────────────────────────────────
    "setbjminbet", "setbjmaxbet", "setbjcountdown", "setbjturntimer",
    "setbjactiontimer", "setbjmaxsplits",
    "setbjdailywinlimit", "setbjdailylosslimit",
    "setrbjdecks", "setrbjminbet", "setrbjmaxbet", "setrbjcountdown",
    "setrbjshuffle", "setrbjblackjackpayout", "setrbjwinpayout",
    "setrbjturntimer", "setrbjactiontimer", "setrbjmaxsplits",
    "setrbjdailywinlimit", "setrbjdailylosslimit",
    "setrbjlimits", "setbjlimits",
    # ── roles ────────────────────────────────────────────────────────────────
    "addowner", "removeowner",
    "addmanager", "removemanager",
    "addmoderator", "removemoderator",
    "addadmin", "removeadmin",
    # ── staff misc ───────────────────────────────────────────────────────────
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
    "replog", "addrep", "removerep", "setrep", "resetrep",
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
    "announce_subs", "dmnotify",
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
    "setpokerblinds", "setpokerante", "setpokernexthandtimer",
    "setpokermaxstack", "setpokeridlestrikes",
    "pokerdebug", "pokerfix", "pokerrefundall", "pokercleanup",
    "setcoins", "editcoins", "resetcoins",
    "addeventcoins", "removeeventcoins", "seteventcoins", "reseteventcoins",
    "addxp", "removexp", "setxp", "resetxp", "setlevel", "addlevel",
    "givetitle", "removetitle", "givebadge", "removebadge",
    "addvip", "removevip", "vips",
    "resetbjstats", "resetrbjstats", "resetpokerstats", "resetcasinostats",
    "adminpanel", "adminlogs",
    "checkcommands", "checkhelp",
    "missingcommands", "routecheck", "silentcheck", "commandtest",
    # ── via handle_admin_command (always replies) ─────────────────────────────
    "addcoins", "removecoins", "announce", "resetgame",
    # ── event aliases ─────────────────────────────────────────────────────────
    "eventstart", "eventstop",
    # ── public ───────────────────────────────────────────────────────────────
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
    "subhelp",
    "bankhelp", "casinohelp", "gamehelp", "coinhelp", "profilehelp",
    "shophelp", "progresshelp", "bjhelp", "rbjhelp",
    "rephelp", "autohelp", "vipstatus", "vipshop", "buyvip", "viphelp",
    "tiphelp", "roleshelp", "maintenancehelp",
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
    "botstatus", "dbstats", "backup",
    "maintenance", "reloadsettings", "cleanup",
    "trivia", "scramble", "riddle", "coinflip",
    "reporthelp",
})

# ---------------------------------------------------------------------------
# HELP_CMDS
# Commands referenced in any help page or help text in main.py.
# ---------------------------------------------------------------------------

HELP_CMDS: frozenset[str] = frozenset({
    "help", "gamehelp", "casinohelp", "coinhelp", "bankhelp",
    "shophelp", "profilehelp", "progresshelp", "eventhelp",
    "bjhelp", "rbjhelp", "rephelp", "autohelp", "viphelp", "tiphelp",
    "roleshelp", "staffhelp", "modhelp", "managerhelp", "adminhelp",
    "ownerhelp", "casinoadminhelp", "bankadminhelp", "audithelp",
    "reporthelp", "maintenancehelp", "questhelp", "goldhelp",
    "trivia", "scramble", "riddle", "answer", "coinflip",
    "autogames", "setgametimer", "setautogameinterval", "gameconfig",
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
    "setpokerbuyin", "setpokerplayers", "setpokertimer", "setpokerraise",
    "setpokerdailywinlimit", "setpokerdailylosslimit", "resetpokerlimits",
    "poker", "pokerhelp", "pokercleanup",
    "send", "bank", "bankstats", "transactions", "banknotify",
    "viewtx", "bankwatch", "ledger", "auditbank", "banksettings",
    "bankblock", "bankunblock",
    "setminsend", "setmaxsend", "setsendlimit", "setminlevelsend",
    "setmintotalearned", "setmindailyclaims", "setsendtax", "sethighriskblocks",
    "notifications", "clearnotifications",
    "balance", "bal", "daily", "wallet", "leaderboard", "tiprate",
    "tipstats", "tipleaderboard",
    "profile", "whois", "me", "stats", "badges", "titles", "privacy", "dashboard",
    "shop", "titleinfo", "badgeinfo", "buy", "equip", "myitems",
    "vipstatus", "vipshop", "buyvip",
    "quests", "dailyquests", "weeklyquests", "claimquest",
    "achievements", "claimachievements",
    "rep", "reputation", "repstats", "toprep", "repleaderboard",
    "event", "events", "eventstatus", "eventpoints", "eventshop",
    "startevent", "stopevent", "autoevents",
    "setautoeventinterval", "setautoeventduration",
    "report", "bug", "myreports",
    "reports", "reportinfo", "closereport", "reportwatch",
    "addvip", "removevip", "vips", "goldrainvip",
    "settiprate", "settipcap", "settiptier",
    "goldtip", "goldrain", "goldrainall", "goldrefund",
    "addcoins", "removecoins", "setcoins", "resetcoins",
    "addxp", "removexp", "setxp", "setlevel", "addlevel",
    "addrep", "removerep", "setrep", "resetrep", "addeventcoins",
    "givetitle", "removetitle", "givebadge", "removebadge",
    "addmanager", "removemanager", "addmoderator", "removemoderator",
    "allstaff",
    "adminlogs", "adminpanel", "checkhelp", "checkcommands",
    "missingcommands", "routecheck", "silentcheck", "commandtest",
    "dbstats", "maintenance", "bankblock",
    "announce", "resetgame", "setrules", "rules", "dailyadmin",
    "warn", "warnings", "mute", "unmute", "mutes",
    "audit", "healthcheck",
    "addowner", "removeowner", "owners",
    "addadmin", "removeadmin",
    "setmaxbalance",
    "automod",
    "eventstart", "eventstop",
    "announce_subs", "dmnotify",
})

# ---------------------------------------------------------------------------
# SILENT_RISK_CMDS — commands that may not always reply (post all-fixes)
# ---------------------------------------------------------------------------

SILENT_RISK_CMDS: frozenset[str] = frozenset({
    # handle_admin_command else-branch has no reply for unrecognised cmds;
    # any future command accidentally routed there would be silent.
    # Currently all known commands in ADMIN_ONLY_CMDS that fall to
    # handle_admin_command are covered: addcoins, removecoins, announce,
    # resetgame all reply.  This set is intentionally empty until a new
    # unhandled fallback is discovered.
})

# ---------------------------------------------------------------------------
# Pagination helper
# ---------------------------------------------------------------------------

_PAGE_SIZE = 10


def _paginate(title: str, items: list[str], page: int) -> tuple[str, int]:
    """Return (message, total_pages).  page is 1-indexed."""
    total = len(items)
    if total == 0:
        return f"{title}: none.", 1
    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * _PAGE_SIZE
    chunk = items[start:start + _PAGE_SIZE]
    body = ", ".join(f"/{c}" for c in chunk)
    suffix = f"  (p{page}/{total_pages})" if total_pages > 1 else ""
    return f"{title}{suffix}: {body}"[:249], total_pages


# ---------------------------------------------------------------------------
# /checkcommands
# ---------------------------------------------------------------------------

async def handle_checkcommands(bot: BaseBot, user: User, all_known: set) -> None:
    if not _can_audit(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    routed_ok = len(ROUTED_COMMANDS & all_known)
    missing   = len(all_known - ROUTED_COMMANDS)
    in_help   = len(HELP_CMDS & ROUTED_COMMANDS)
    silent    = len(SILENT_RISK_CMDS)
    print(f"[AUDIT] /checkcommands @{user.username}: routed={routed_ok} missing={missing} "
          f"help_coverage={in_help} silent={silent}")
    await _w(bot, user.id,
             f"Cmd Check: Routed {routed_ok} | Not routed {missing} | "
             f"Help coverage {in_help} | Silent risk {silent}")


# ---------------------------------------------------------------------------
# /checkhelp
# ---------------------------------------------------------------------------

async def handle_checkhelp_audit(bot: BaseBot, user: User, all_known: set) -> None:
    if not _can_audit(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    missing_routes = sorted(HELP_CMDS - ROUTED_COMMANDS)
    unlisted       = sorted((ROUTED_COMMANDS - HELP_CMDS) - HIDDEN_CMDS)
    print(f"[AUDIT] /checkhelp @{user.username}: "
          f"missing_routes={len(missing_routes)} unlisted={len(unlisted)}")
    await _w(bot, user.id,
             f"Help Check: {len(missing_routes)} in help but unrouted | "
             f"{len(unlisted)} routed but no help entry")


# ---------------------------------------------------------------------------
# /missingcommands [page]
# ---------------------------------------------------------------------------

async def handle_missingcommands(bot: BaseBot, user: User, args: list[str] | None = None) -> None:
    if not _can_audit(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    missing = sorted(HELP_CMDS - ROUTED_COMMANDS)
    print(f"[AUDIT] /missingcommands @{user.username}: {missing}")
    if not missing:
        await _w(bot, user.id, "Missing: none — all help-listed commands are routed.")
        return
    page = 1
    if args and len(args) >= 2:
        try:
            page = int(args[1])
        except ValueError:
            pass
    msg, total_pages = _paginate("Missing", missing, page)
    await _w(bot, user.id, msg)
    if page < total_pages:
        await _w(bot, user.id, f"More: /missingcommands {page + 1}")


# ---------------------------------------------------------------------------
# /routecheck [page | category]
# ---------------------------------------------------------------------------

_ROUTE_CATEGORIES: dict[str, set[str]] = {
    "poker": {"poker", "p", "pj", "pt", "ph", "po", "pp", "pplayers", "pstats",
              "pokerstats", "plb", "pleaderboard", "pokerlb", "pokerleaderboard",
              "phelp", "pokerhelp", "check", "ch", "call", "ca", "raise", "r",
              "fold", "f", "allin", "ai", "shove", "all-in", "sitout", "sitin",
              "rebuy", "pstacks", "mystack", "pcards", "podds", "ptable"},
    "casino": {"bj", "rbj", "bjoin", "bh", "bs", "bd", "bsp", "bt", "bhand",
               "bjh", "bjs", "bjd", "bjsp", "bjhand", "rjoin", "rh", "rs",
               "rd", "rsp", "rt", "rhand", "rshoe", "rlimits", "rstats", "rhand",
               "rbjh", "rbjs", "rbjd", "rbjsp", "rbjhand", "blimits", "bstats",
               "confirmcasinoreset"},
    "admin":  {"setcoins", "editcoins", "resetcoins", "addcoins", "removecoins",
               "addxp", "removexp", "setxp", "resetxp", "setlevel", "addlevel",
               "givetitle", "removetitle", "givebadge", "removebadge",
               "addvip", "removevip", "vips", "resetbjstats", "resetrbjstats",
               "resetpokerstats", "resetcasinostats", "adminpanel", "adminlogs",
               "checkcommands", "checkhelp", "missingcommands", "routecheck",
               "silentcheck", "commandtest", "announce", "resetgame",
               "eventstart", "eventstop", "announce_subs", "dmnotify"},
}


async def handle_routecheck(bot: BaseBot, user: User, args: list[str] | None = None) -> None:
    if not _can_audit(user.username):
        await _w(bot, user.id, "Staff only.")
        return

    unlisted_full = sorted((ROUTED_COMMANDS - HELP_CMDS) - HIDDEN_CMDS)
    arg = args[1].lower() if args and len(args) >= 2 else ""

    # Category filter
    if arg in _ROUTE_CATEGORIES:
        subset = sorted(_ROUTE_CATEGORIES[arg] - HELP_CMDS - HIDDEN_CMDS)
        msg, _ = _paginate(f"Unlisted [{arg}]", subset, 1)
        print(f"[AUDIT] /routecheck {arg} @{user.username}: {subset}")
        await _w(bot, user.id, msg)
        return

    # Page number
    page = 1
    if arg.isdigit():
        page = int(arg)

    print(f"[AUDIT] /routecheck p{page} @{user.username}: {len(unlisted_full)} unlisted")
    if not unlisted_full:
        await _w(bot, user.id, "Unlisted: none — all routed commands have help entries.")
        return
    msg, total_pages = _paginate("Unlisted", unlisted_full, page)
    await _w(bot, user.id, msg)
    if page < total_pages:
        await _w(bot, user.id, f"More: /routecheck {page + 1}")


# ---------------------------------------------------------------------------
# /silentcheck
# ---------------------------------------------------------------------------

async def handle_silentcheck(bot: BaseBot, user: User, all_known: set) -> None:
    if not _can_audit(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    silent = sorted(SILENT_RISK_CMDS & all_known)
    print(f"[AUDIT] /silentcheck @{user.username}: {silent}")
    if not silent:
        await _w(bot, user.id, "Silent risk: none. All known commands have guaranteed replies.")
        return
    msg, _ = _paginate("Silent risk", silent, 1)
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# /commandtest <command>
# ---------------------------------------------------------------------------

async def handle_commandtest(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_audit(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /commandtest <command>")
        return
    cmd = args[1].lstrip("/").lower()
    routed  = cmd in ROUTED_COMMANDS
    in_help = cmd in HELP_CMDS
    hidden  = cmd in HIDDEN_CMDS
    silent  = cmd in SILENT_RISK_CMDS
    route_s = "YES" if routed  else "NO"
    help_s  = "YES" if in_help else "NO"
    extra   = ""
    if hidden:
        extra += " [hidden/internal]"
    if silent:
        extra += " ⚠️ silent"
    print(f"[COMMANDTEST] /{cmd} route={route_s} help={help_s} hidden={hidden} silent={silent}")
    await _w(bot, user.id, f"/{cmd} route: {route_s} | help: {help_s}{extra}")
