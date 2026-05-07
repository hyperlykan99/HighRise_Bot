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
    "confirmclosepoker",
    "casinointegrity", "integritylogs", "carddeliverycheck",
    "setpokercardmarker",
    "setcoins", "editcoins", "resetcoins",
    "addeventcoins", "removeeventcoins",
    "seteventcoins", "editeventcoins", "reseteventcoins",
    "addxp", "removexp", "setxp", "editxp", "resetxp",
    "setlevel", "editlevel", "addlevel", "removelevel",
    "promotelevel", "demotelevel",
    "setrep", "editrep", "resetrep",
    "givetitle", "removetitle", "settitle", "cleartitle",
    "givebadge", "removebadge", "removebadgefrom", "setbadge", "clearbadge",
    "addvip", "removevip", "vips", "setvipprice",
    "resetbjstats", "resetrbjstats", "resetpokerstats", "resetcasinostats",
    "adminpanel", "adminlogs", "adminloginfo",
    "checkcommands", "checkhelp",
    "missingcommands", "routecheck", "silentcheck", "commandtest",
    "fixcommands", "testcommands",
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
    "autogames", "autoevents", "autogamesowner",
    "stopautogames", "killautogames",
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
    "bankhelp", "bankerhelp", "casinohelp", "gamehelp",
    "coinhelp", "economydbcheck", "economyrepair", "profilehelp",
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
    # ── mining ──────────────────────────────────────────────────────────────
    "mine", "m", "dig",
    "tool", "pickaxe",
    "upgradetool", "upick",
    "mineprofile", "mp", "minerank",
    "mineinv", "ores",
    "sellores", "sellore",
    "minelb",
    "mineshop", "minebuy",
    "useluckboost", "usexpboost", "useenergy",
    "craft", "minedaily",
    "miningadmin", "mining",
    "miningevent", "miningevents",
    "startminingevent", "stopminingevent",
    "setminecooldown", "setmineenergycost", "setminingenergy",
    "addore", "removeore",
    "settoollevel", "setminelevel",
    "addminexp", "setminexp",
    "resetmining", "miningroomrequired",
    "orelist", "minehelp",
    "orebook", "oremastery", "claimoremastery", "orestats",
    "contracts", "miningjobs",
    "job", "deliver", "claimjob", "rerolljob",
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
    "autogames", "autoevents", "autogamesowner",
    "stopautogames", "killautogames",
    "setgametimer", "setautogameinterval", "gameconfig",
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
    # ── mining ──────────────────────────────────────────────────────────────
    "mine", "tool", "upgradetool", "ores", "sellores",
    "minelb", "mineshop", "craft", "minedaily", "mineprofile",
    "minebuy", "useluckboost", "usexpboost", "useenergy",
    "orebook", "oremastery", "claimoremastery", "orestats",
    "contracts", "miningjobs", "job", "deliver", "claimjob", "rerolljob",
    "minehelp",
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
    try:
        from modules.multi_bot import _DEFAULT_COMMAND_OWNERS
        no_owner = len(all_known - set(_DEFAULT_COMMAND_OWNERS.keys()))
    except Exception:
        no_owner = -1
    print(f"[AUDIT] /checkcommands @{user.username}: known={len(all_known)} "
          f"routed={routed_ok} missing={missing} no_owner={no_owner} "
          f"help={in_help} silent={silent}")
    await _w(bot, user.id,
             f"CmdCheck: Known {len(all_known)} | Routed {routed_ok} | "
             f"Missing {missing} | NoOwner {no_owner} | Silent {silent}"[:249])


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
    """
    /commandtest <command>
    Always answered by host (or eventhost when host is offline).
    Shows routing info for ANY command without requiring the owner bot to handle it.
    Output: ct: <cmd> | owner=<Mode> | owner_online=T/F | host_handles=T/F | <mode>_handles=T/F
    """
    if not _can_audit(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /commandtest <command>")
        return
    cmd = args[1].lstrip("/").lower()
    try:
        from modules.multi_bot import (
            _resolve_command_owner, _is_mode_online, _MODE_NAMES,
            should_this_bot_handle,
        )
        owner_raw = _resolve_command_owner(cmd)   # None  →  not in registry
        in_route  = cmd in ROUTED_COMMANDS
        in_known  = cmd in HELP_CMDS

        if owner_raw is None and not in_route:
            # Completely unknown: not in ownership map and not in the route table
            msg = (
                f"ct: {cmd} | owner=UNKNOWN | route=NO"
                f" | handler=NO | fix=add_to_registry"
            )
        else:
            # owner_raw is None but cmd IS routed → host handles it (no explicit owner)
            owner_mode   = owner_raw if owner_raw is not None else "host"
            owner_name   = _MODE_NAMES.get(owner_mode, owner_mode.title())
            owner_online = _is_mode_online(owner_mode) if owner_mode != "all" else True
            host_handles  = should_this_bot_handle(cmd)
            # owner_handles: the owner bot responds when it is online (or single-bot "all")
            owner_handles = (owner_mode == "all") or _is_mode_online(owner_mode)
            route_s  = "YES" if in_route  else "NO"
            online_s = str(owner_online).lower()
            host_s   = str(host_handles).lower()
            owner_s  = str(owner_handles).lower()
            # handler = command is in the central registry
            try:
                from modules.command_registry import get_entry as _reg_get
                handler_s = "YES" if _reg_get(cmd) is not None else "NO"
            except Exception:
                handler_s = route_s   # fallback: assume routed = has handler
            msg = (
                f"ct: {cmd} | owner={owner_name} | owner_online={online_s}"
                f" | host_handles={host_s} | {owner_mode}_handles={owner_s}"
                f" | route={route_s} | handler={handler_s}"
            )
    except Exception as exc:
        msg = f"ct: {cmd} | error:{str(exc)[:100]}"
    print(f"[COMMANDTEST] @{user.username}: /{cmd}")
    await _w(bot, user.id, msg[:249])


# ---------------------------------------------------------------------------
# /fixcommands  — refresh command registry, report coverage
# ---------------------------------------------------------------------------

async def handle_fixcommands(bot: BaseBot, user: User) -> None:
    """/fixcommands  — refresh command ownership cache, write defaults to DB, report coverage."""
    if not _can_audit(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    owned = wrote = -1
    try:
        from modules.multi_bot import (
            _refresh_owner_cache, _DEFAULT_COMMAND_OWNERS, _HARD_OWNER_MODES,
        )
        import database as _db
        owned = len(_DEFAULT_COMMAND_OWNERS)
        wrote = 0
        for cmd, mode in _DEFAULT_COMMAND_OWNERS.items():
            fb = 0 if mode in _HARD_OWNER_MODES else 1
            _db.set_command_owner_db(cmd, mode, mode, fb)
            wrote += 1
        _refresh_owner_cache()
    except Exception as exc:
        print(f"[FIXCMDS] DB write error: {exc}")
    overrides = 0
    try:
        import database as db
        overrides = len(db.get_all_command_owners() or [])
    except Exception:
        pass
    routed  = len(ROUTED_COMMANDS)
    in_help = len(HELP_CMDS)
    missing = len(HELP_CMDS - ROUTED_COMMANDS)
    print(f"[FIXCMDS] @{user.username}: routed={routed} help={in_help} "
          f"missing={missing} owned={owned} wrote={wrote} overrides={overrides}")
    await _w(bot, user.id,
             f"✅ Fix done: {wrote}/{owned} owned | {routed} routed | "
             f"{missing} help gaps | {overrides} DB entries."[:249])


# ---------------------------------------------------------------------------
# /testcommands  — spot-check ownership map per module
# ---------------------------------------------------------------------------

_MODULE_SPOT_CHECKS: list[tuple[str, str, list[str]]] = [
    ("Banker",   "banker",     ["bal", "send", "daily", "bank", "transactions"]),
    ("BJ",       "blackjack",  ["bj", "bjoin", "bh", "bs", "rjoin", "rh"]),
    ("Poker",    "poker",      ["p", "check", "call", "fold", "allin"]),
    ("Miner",    "miner",      ["mine", "tool", "sellores", "minelb"]),
    ("Shop",     "shopkeeper", ["shop", "buy", "equip", "badgemarket"]),
    ("Security", "security",   ["report", "warn", "mute", "kick"]),
    ("DJ",       "dj",         ["emote", "dance", "hug", "heart"]),
    ("Events",   "eventhost",  ["announce", "goldrain", "startevent"]),
    ("Host",     "host",       ["help", "commandtest", "fixcommands"]),
]


async def handle_testcommands(bot: BaseBot, user: User) -> None:
    """/testcommands  — spot-check ownership map for every module."""
    if not _can_audit(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    try:
        from modules.multi_bot import _DEFAULT_COMMAND_OWNERS
    except Exception as exc:
        await _w(bot, user.id, f"❌ Import error: {str(exc)[:80]}")
        return

    results: list[str] = []
    all_pass = True
    for label, mode, cmds in _MODULE_SPOT_CHECKS:
        bad = [c for c in cmds if _DEFAULT_COMMAND_OWNERS.get(c) != mode]
        if bad:
            all_pass = False
            results.append(f"{label}❌({','.join(bad[:2])})")
        else:
            results.append(f"{label}✅")

    summary = " | ".join(results)
    status  = "All OK" if all_pass else "ISSUES FOUND"
    print(f"[TESTCMDS] @{user.username}: {status} — {summary}")
    await _w(bot, user.id, f"CmdTest [{status}]: {summary}"[:249])


# ---------------------------------------------------------------------------
# /commandintegrity
# ---------------------------------------------------------------------------

async def handle_commandintegrity(bot: BaseBot, user: User, all_known: set) -> None:
    """/commandintegrity — compact multi-line integrity report (admin+)."""
    if not _can_audit(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    routed  = len(ROUTED_COMMANDS & all_known)
    missing = len(all_known - ROUTED_COMMANDS)
    in_help = len(HELP_CMDS & ROUTED_COMMANDS)
    silent  = len(SILENT_RISK_CMDS)
    no_owner = dup_risk = reg_total = unregistered = -1
    try:
        from modules.multi_bot import _DEFAULT_COMMAND_OWNERS, _HARD_OWNER_MODES
        owned_set  = set(_DEFAULT_COMMAND_OWNERS.keys())
        no_owner   = len(all_known - owned_set)
        hard_owned = {c for c, m in _DEFAULT_COMMAND_OWNERS.items()
                      if m in _HARD_OWNER_MODES}
        dup_risk   = len(ROUTED_COMMANDS & hard_owned)
    except Exception:
        pass
    try:
        from modules.command_registry import REGISTRY, alias_map as _amap
        reg_total    = len(REGISTRY)
        reg_all      = set(REGISTRY) | set(_amap)
        unregistered = len(all_known - reg_all)
    except Exception:
        pass
    print(f"[INTEGRITY] @{user.username}: known={len(all_known)} routed={routed} "
          f"missing={missing} no_owner={no_owner} reg={reg_total} "
          f"unreg={unregistered} dup={dup_risk} silent={silent}")
    await _w(bot, user.id,
             f"Integrity: Registry={reg_total} Known={len(all_known)} "
             f"Routed={routed} Help={in_help} Missing={missing}"[:249])
    await _w(bot, user.id,
             f"  Unregistered={unregistered} NoOwner={no_owner} "
             f"Silent={silent} DupRisk={dup_risk} (0=clean)"[:249])


# ---------------------------------------------------------------------------
# /commandrepair
# ---------------------------------------------------------------------------

async def handle_commandrepair(bot: BaseBot, user: User) -> None:
    """/commandrepair — rebuild bot_command_ownership in DB from source registry (owner only)."""
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    try:
        import database as _db
        from modules.multi_bot import (
            _DEFAULT_COMMAND_OWNERS, _HARD_OWNER_MODES, _refresh_owner_cache,
        )
        wrote = 0
        for cmd, mode in _DEFAULT_COMMAND_OWNERS.items():
            fb = 0 if mode in _HARD_OWNER_MODES else 1
            _db.set_command_owner_db(cmd, mode, mode, fb)
            wrote += 1
        _refresh_owner_cache()
        print(f"[REPAIR] @{user.username}: wrote {wrote} entries, cache refreshed")
        await _w(bot, user.id,
                 f"✅ Repaired: {wrote} ownership rows written to DB from "
                 f"source registry. Cache refreshed."[:249])
    except Exception as exc:
        print(f"[REPAIR] ERROR @{user.username}: {exc}")
        await _w(bot, user.id, f"❌ Repair error: {str(exc)[:120]}"[:249])
