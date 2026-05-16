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
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


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
    "goldtip", "tipgold", "goldreward", "rewardgold",
    "goldrefund", "goldrain", "goldrainall", "goldraineligible",
    "goldrainrole", "goldrainvip", "goldraintitle", "goldrainbadge",
    "goldrainlist", "goldwallet", "goldtips", "goldtx", "pendinggold",
    "confirmgoldtip", "setgoldrainstaff", "setgoldrainmax",
    "goldtipbots", "goldtipall",
    "setpokerblinds", "setpokerante", "setpokernexthandtimer",
    "setpokermaxstack", "setpokeridlestrikes",
    "notifystats", "notifyprefs",
    "dailyadmin",
    "confirmcasinoreset",
    "auditbank", "auditcasino", "auditeconomy",
    # Not yet implemented — hidden so they don't appear as active issues
    "pokerhandlog", "pokerlogs", "pokertest", "pokerverify",
    "returnbots", "setcoinpack", "sitback",
    "ticketlog", "tiplogs",
})

# ---------------------------------------------------------------------------
# DEPRECATED_CMDS — commands still in ALL_KNOWN_COMMANDS but no longer
# actively used.  Excluded from active-route and active-owner checks in
# /checkcommands all and /commandissues.
# ---------------------------------------------------------------------------

DEPRECATED_CMDS: frozenset[str] = frozenset({
    # None currently — add here when retiring a command
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
    "setbjdailywinlimit", "setbjdailylosslimit", "setbjinsurance",
    "setrbjdecks", "setrbjminbet", "setrbjmaxbet", "setrbjcountdown",
    "setrbjshuffle", "setrbjblackjackpayout", "setrbjwinpayout",
    "setrbjturntimer", "setrbjactiontimer", "setrbjmaxsplits",
    "setrbjdailywinlimit", "setrbjdailylosslimit",
    "setrbjlimits", "setbjlimits",
    # ── easy BJ shortcuts ────────────────────────────────────────────────────
    "blackjack", "bjbet", "bet", "hit", "stand", "double", "split",
    "insurance", "bi", "surrender", "shoe", "bjshoe",
    # ── how-to-play / game guide ─────────────────────────────────────────────
    "howtoplay", "gameguide", "games",
    # ── time-in-room EXP admin commands ──────────────────────────────────────
    "settimeexp", "settimeexpcap", "settimeexptick", "settimeexpbonus",
    "timeexpstatus",
    # ── display format settings ───────────────────────────────────────────────
    "displaybadges", "displaytitles", "displayformat", "displaytest",
    "botmessageformat", "setbotmessageformat",
    "msgtest", "msgboxtest", "msgsplitpreview",
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
    "mute", "unmute", "mutes", "mutestatus", "forceunmute",
    "warn", "warnings", "clearwarnings",
    "softban", "unsoftban",
    "staffnote", "staffnotes",
    "permissioncheck", "rolecheck",
    # ── Luxe Jail system (3.4A) ───────────────────────────────────────────
    "jail", "bail", "jailstatus", "jailtime", "jailhelp",
    "unjail", "jailrelease", "jailadmin", "jailactive",
    "jailsetcost", "jailsetmax", "jailsetmin", "jailsetbailmultiplier",
    "jailprotectstaff", "jaildebug",
    "setjailspot", "setjailguardspot", "setsecurityidle", "setjailreleasespot",
    "jailconfirm", "jailcancel",
    "safetyadmin", "economysafety", "safetydash",
    "setrules", "automod",
    "reports", "reportinfo", "closereport", "reportwatch",
    "profileadmin", "profileprivacy", "resetprofileprivacy",
    "replog", "addrep", "removerep", "setrep", "resetrep",
    "settiprate", "settipcap", "settiptier", "settipautosub", "settipresubscribe",
    "setgametimer", "setautogameinterval",
    "setautoeventinterval", "setautoeventduration", "gameconfig",
    "goldtip", "tipgold", "goldreward", "rewardgold",
    "goldrefund", "goldrain", "goldrainall", "goldraineligible",
    "goldrainrole", "goldrainvip", "goldraintitle", "goldrainbadge", "goldrainlist",
    "goldwallet", "goldtips", "goldtx", "pendinggold", "confirmgoldtip",
    "setgoldrainstaff", "setgoldrainmax",
    "goldtipall", "goldtipbots",
    "tiplb", "tipleaderboard", "bottiplb", "bottipleaderboard",
    "roomtiplb", "roomtipleaderboard", "alltiplb", "alltipleaderboard",
    "tipreceiverlb", "topreceivers",
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
    "resendcards", "cards", "pokerdealstatus", "pokerplayers",
    "pokerdebug", "pokerfix", "pokerrefundall", "pokercleanup",
    "confirmclosepoker",
    "pokerdashboard", "pdash", "pokeradmin",
    "pokerpause", "pokerresume",
    "pokerforceadvance", "pokerforceresend",
    "pokerturn", "pokerpots", "pokeractions", "pokerstylepreview",
    "pokerresetturn", "pokerresethand", "pokerresettable",
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
    "checkcommands", "checkhelp", "launchcheck", "currencycheck",
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
    "leaderboard", "lb", "top",
    "toprich", "topminers", "topfishers", "topstreaks",
    "profile", "me", "whois", "pinfo",
    "stats",
    "badges", "titles",
    "privacy",
    "level",
    "xpleaderboard",
    "shop", "buy", "equip", "myitems", "inv", "inventory", "badgeinfo", "titleinfo",
    "event", "events", "eventhelp", "eventstatus",
    "nextevent", "next", "schedule", "eventloop",
    "eventadmin", "startevent", "stopevent",
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
    "announcement", "promo", "eventalert", "gamealert", "tipalert",
    "subcount", "unsubuser", "notifyadmin", "alerts", "notifyhelp2",
    "commandissues", "notifyaudit", "notifystatus",
    "qatest",
    # badge market commands routed in main.py
    "profilebadge", "rarebadges", "showbadge", "wishlist", "wishbadge",
    "removewishlist", "unwishlist", "unlockbadge", "setbadgeconfirm",
    "bankhelp", "bankerhelp", "casinohelp", "gamehelp",
    "coinhelp", "economydbcheck", "economyrepair", "profilehelp",
    "crashlogs", "missingbots", "commandaudit",
    "shophelp", "progresshelp", "bjhelp", "rbjhelp",
    "rephelp", "autohelp", "vipstatus", "vipshop", "buyvip", "viphelp",
    "tiphelp", "roleshelp", "maintenancehelp",
    "mycommands", "helpsearch", "start", "guide",
    "new", "activities", "roominfo", "newbie", "whatdoido",
    "streak", "dailystatus", "commandcheck",
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
    "allin", "shove", "all-in",
    "pp", "pplayers",
    "pstats", "pokerstats",
    "plb", "pleaderboard", "pokerlb", "pokerleaderboard",
    "phelp",
    "sitout", "sitin", "rebuy", "pstacks", "mystack",
    "poker", "pokerhelp", "pokerstatus",
    "bjstatus", "blackjackhelp", "bjforce", "bjtest",
    "bjadmin", "bjadminhelp", "staffbj", "staffbjhelp",
    "botstatus", "dbstats", "backup",
    "maintenance", "reloadsettings", "cleanup",
    "trivia", "scramble", "riddle", "coinflip",
    "reporthelp",
    # ── collection book (3.1H) ────────────────────────────────────────────────
    "collection", "mybook", "collectbook",
    "topcollectors", "topore", "toporecollectors",
    "topfishcollectors", "fishcollectors",
    "orebook", "fishbook",
    "rarelog",
    "lastminesummary", "lastfishsummary",
    "collectionhelp", "bookhelp",
    "enabledm", "summarydm",
    # ── shop / badge market (public elif branches) ───────────────────────────
    "mybadges", "confirmbuy",
    "badgemarket", "badgebuy", "badgelist", "badgecancel",
    "mybadgelistings", "badgeprices",
    "buybadge", "badgeshop", "marketbadges", "sellbadge", "cancelbadge",
    "equipbadge", "staffbadge", "emojitest", "disableemoji", "enableemoji",
    # ── 3.1F badge market / trade commands ──────────────────────────────────
    "mylistings", "marketsearch", "marketfilter", "marketaudit", "marketdebug",
    "forcelistingcancel", "clearbadgelocks",
    "trade", "tradeadd", "tradecoins", "tradeview", "tradeconfirm", "tradecancel",
    "helpmarket", "helptrade", "markethelp", "tradehelp",
    "badgehelp", "helpbadge", "helpbadges",
    # ── tip (alias for send) ─────────────────────────────────────────────────
    "tip",
    # ── control panel (own elif block, not via STAFF_CMDS) ───────────────────
    "control", "status", "roomstatus", "economystatus", "quicktoggles", "ownerpanel",
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
    "orelist", "oreprices", "orevalues", "orevalue",
    "oreinfo", "oredetail", "oredetails",
    "minehelp",
    "simannounce",
    "forcedrop", "forcedropore", "forcedropstatus", "clearforcedrop",
    "orebook", "oremastery", "claimoremastery", "orestats",
    "contracts", "miningjobs",
    "job", "deliver", "claimjob", "rerolljob",
    # A2: ore chance commands
    "orechances", "orechance",
    "setorechance", "setraritychance", "reloadorechances",
    # Economy panel + rarity caps
    "economypanel", "economybalance", "miningeconomy",
    "economysettings",
    "economycap", "economycaps",
    "setraritycap", "resetraritycaps",
    "payoutlogs", "minepayoutlogs", "biggestpayouts",
    # B-project: mining event status commands (public)
    "mineevents", "mineboosts", "luckstatus", "eventeffects",
    # B-project: mining event start commands (manager)
    "miningblessing", "luckevent", "miningeventstart",
    # B-project: event manager panel
    "eventmanager", "eventpanel",
    # B-project: auto-event management
    "autoeventstatus", "autoeventadd", "autoeventremove", "autoeventinterval",
    # Event Manager catalog + pool (new)
    "eventlist", "eventpreview",
    "aepool", "autoeventpool", "aeadd", "aeremove",
    "aequeue", "autoeventqueue", "aenext", "autoeventnext",
    "aestatus", "eventheartbeat", "eventscheduler",
    "eventcooldowns", "seteventcooldown",
    "eventweights", "seteventweight", "eventhistory",
    # ── room utility (teleport / emotes / social / alerts / intervals) ────────
    "tp", "tpme", "tphere", "bring", "bringall", "tpall",
    "tprole", "tpvip", "tpstaff", "selftp", "goto", "groupteleport",
    "spawns", "spawn", "setspawn", "savepos", "delspawn", "spawninfo", "setspawncoords",
    "tele", "create", "delete", "summon",
    "tag", "setrolespawn", "rolespawn", "rolespawns", "delrolespawn",
    "staffonline", "vipsinroom", "rolelist", "players", "online", "roomlist",
    "emotes", "emote", "stopemote", "dance", "wave", "sit", "clap",
    "forceemote", "forceemoteall", "loopemote", "stoploop", "stopallloops",
    "synchost", "syncdance", "stopsync",
    "publicemotes", "forceemotes", "setemoteloopinterval",
    "hug", "kiss", "slap", "punch", "highfive", "boop", "waveat", "cheer",
    "heart", "giveheart", "reactheart", "hearts", "heartlb",
    "social", "blocksocial", "unblocksocial", "socialhelp",
    "follow", "followme", "stopfollow", "followstatus",
    "alert", "alerthelp", "staffalert", "vipalert", "roomalert", "clearalerts",
    "welcome", "setwelcome", "welcometest", "resetwelcome", "welcomeinterval",
    "intervals", "addinterval", "delinterval", "interval", "intervaltest",
    "repeatmsg", "stoprepeat", "repeatstatus",
    "roomsettings", "setroomsetting", "roomlogs", "roomhelp", "teleporthelp",
    "emotehelp", "welcomehelp", "roomstatus", "roomboost", "boostroom",
    "kick", "ban", "tempban", "unban", "bans", "modlog",
    "startmic", "micstart", "micstatus",
    "toggle", "managerpanel",
    # ── bot-mode management ────────────────────────────────────────────────────
    "botmode", "botmodes", "botprofile", "botprefix", "categoryprefix",
    "setbotprefix", "setbotdesc", "setbotoutfit", "botoutfit", "botoutfits",
    "dressbot", "savebotoutfit",
    "createbotmode", "deletebotmode", "assignbotmode",
    "bots", "botinfo", "botoutfitlogs", "botmodehelp", "botmodules",
    "commandowners", "enablebot", "disablebot", "setbotmodule", "setcommandowner",
    "botfallback", "botstartupannounce", "startupannounce", "modulestartup",
    "startupstatus", "multibothelp", "setmainmode",
    "bothealth", "modulehealth", "deploymentcheck", "botheartbeat", "botconflicts",
    "stability",
    "moduleowners", "botlocks", "dblockcheck", "clearstalebotlocks", "fixbotowners",
    "taskowners", "activetasks", "taskconflicts", "fixtaskowners",
    "restoreannounce", "restorestatus",
    "commandintegrity", "commandrepair",
    # ── shop / badge market (admin-side) ──────────────────────────────────────
    "addbadge", "editbadgeprice",
    "setbadgepurchasable", "setbadgetradeable", "setbadgesellable",
    "giveemojibadge", "badgecatalog", "badgeadmin",
    "setbadgemarketfee", "badgemarketlogs",
    "unequip", "cancelbuy", "marketbuy",
    "shopadmin", "shoptest", "setshopconfirm", "seteventconfirm",
    "buyitem", "purchase",
    # ── misc ──────────────────────────────────────────────────────────────────
    "fixautogames",
    # ── /commandissues ────────────────────────────────────────────────────────
    "commandissues",
    # ── AI assistant (3.3A — ChillTopiaMC AI) ────────────────────────────────
    "ai",
    # ── Bot-mode outfit commands (pre-existing, routed but previously unregistered) ──
    "dressbot", "savebotoutfit", "botoutfitstatus",
    "copyoutfit", "wearuseroutfit", "renamebotoutfit", "clearbotoutfit",
    # ── Per-bot self-managing outfit commands ──────────────────────────────────────
    "copymyoutfit", "copyoutfitfrom", "savemyoutfit", "wearoutfit",
    "myoutfits", "myoutfitstatus", "directoutfittest",
    # ── Emote extensions ─────────────────────────────────────────────────────
    "emoteinfo",
    # ── Bot spawns ────────────────────────────────────────────────────────────
    "setbotspawn", "setbotspawnhere", "botspawns", "clearbotspawn",
    "mypos", "positiondebug",
    # ── Events (new) ──────────────────────────────────────────────────────────
    "adminsblessing", "adminblessing", "eventresume",
    "autogamestatus", "autogameresume",
    # ── Mining (new) ──────────────────────────────────────────────────────────
    "mineconfig", "mineeventstatus",
    # ── Mining weight leaderboards (new) ─────────────────────────────────────
    "oreweightlb", "weightlb", "heaviest",
    "myheaviest", "oreweights", "topweights",
    "setweightlbmode",
    # ── Mining announce settings (new) ───────────────────────────────────────
    "mineannounce", "setmineannounce",
    "setoreannounce", "oreannounce",
    "mineannouncesettings",
    # ── Mining weight admin settings (new) ───────────────────────────────────
    "mineweights", "setmineweights",
    "setweightscale", "setrarityweightrange",
    "oreweightsettings",
    # ── Bulk command testing (new) ────────────────────────────────────────────
    "commandtestall", "ctall",
    "commandtestgroup", "ctgroup",
    # ── Poker pace / stack / deal (new) ──────────────────────────────────────
    "pokermode", "pokerpace", "setpokerpace",
    "pokerstacks", "setpokerstack", "dealstatus",
    # ── Command registry repair ───────────────────────────────────────────────
    "fixcommandregistry",
    # ── Mining panel (new) ────────────────────────────────────────────────────
    "minepanel", "miningpanel",
    # ── 3.1I luck stack + admin commands ─────────────────────────────────────
    "mineadmin", "mineluck", "minestack",
    "fishadmin", "fishluck", "fishstack",
    "fishpanel", "fishingpanel",
    "setfishcooldown", "setfishweights", "setfishweightscale",
    "setfishannounce", "setfishrarityweightrange",
    "boostadmin", "luck", "myluck",
    # ── Time EXP bot exclusion (new) ─────────────────────────────────────────
    "setallowbotxp",
    # ── Per-bot welcome messages (new) ───────────────────────────────────────
    "botwelcome", "setbotwelcome", "resetbotwelcome",
    "previewbotwelcome", "botwelcomes",
    # ── Bio + onboarding audit (3.0C) ────────────────────────────────────────
    "bios", "checkbios", "checkonboarding",
    # ── 3.1M Onboarding & tutorial ───────────────────────────────────────────
    "tutorial", "newbiehelp", "starter", "startermissions",
    "roomguide", "onboardadmin",
    # ── Gold tip commands (new) ───────────────────────────────────────────────
    "goldtipsettings", "setgoldrate",
    "goldtiplogs", "mygoldtips", "goldtipstatus",
    "tipcoinrate", "settipcoinrate",
    "bottiplogs", "mingoldtip", "setmingoldtip",
    "tipgold", "goldreward", "rewardgold",
    "tiplb", "tipleaderboard", "bottiplb", "bottipleaderboard",
    "roomtiplb", "roomtipleaderboard", "alltiplb", "alltipleaderboard",
    "tipreceiverlb", "topreceivers",
    # ── Economy panel + rarity caps (new) ────────────────────────────────────
    "economypanel", "economybalance", "miningeconomy",
    "economysettings",
    "economycap", "economycaps",
    "setraritycap", "resetraritycaps",
    "payoutlogs", "minepayoutlogs", "biggestpayouts",
    # ── AutoMine (miner bot) ─────────────────────────────────────────────────
    "automine", "am",
    "autominestatus", "amstatus",
    "autominesettings",
    "setautomine", "setautomineduration", "setautomineattempts",
    "setautominedailycap",
    # ── Fishing (fisher bot) ─────────────────────────────────────────────────
    "fish", "cast", "reel",
    "fishlist", "fishrarity",
    "fishprices", "fishvalues",
    "fishinfo", "fishdetail",
    "myfish", "fishinv",
    "sellfish", "sellallfish",
    "fishlevel", "fishxp", "fishlvl",
    "fishstats",
    "fishboosts", "fishingevents",
    "fishhelp", "fishinghelp",
    "topfish", "topfishing", "fishlb",
    "topweightfish", "biggestfish", "heaviestfish",
    "rods", "myrod", "equippedrod",
    "rodshop", "fishrodshop",
    "buyrod", "purchaserod",
    "equiprod", "switchrod",
    "rodinfo", "roddetail",
    "rodstats", "rodupgrade",
    "autofish", "af",
    "autofishstatus", "afstatus",
    "autofishsettings",
    "setautofish",
    "setautofishduration", "setautofishattempts",
    "setautofishdailycap",
    # ── BJ cards / rules / bonus info ────────────────────────────────────────
    "bjcards", "blackjackcards", "cardmode", "bjcardmode",
    "bjrules",
    "bjbonus", "bjbonussetting", "bjbonussettings",
    # ── BJ bonus setter commands ─────────────────────────────────────────────
    "setbjbonus", "setbjbonuscap",
    "setbjbonuspair", "setbjbonuscolor", "setbjbonusperfect",
    "setbjinsurance",
    # ── Fishing force drop ───────────────────────────────────────────────────
    "forcedropfish", "forcedropfishitem", "forcedropfishstatus", "clearforcedropfish",
    "forcedropfishdebug", "clearforceddropfish",
    "forcefishdrop", "forcefish", "forcefishdropfish",
    "forcefishstatus", "clearforcefish",
    # ── First-find rewards ───────────────────────────────────────────────────
    "firstfindreward", "firstfindrewards", "firstfindstatus", "firstfindcheck",
    "firstfindwinners",
    "setfirstfind", "setfirstfinditem", "setfirstfindreward",
    "startfirstfind", "stopfirstfind", "resetfirstfind",
    "firstfindpending", "firstfindpay",
    "paypendingfirstfind", "retryfirstfind",
    # ── Big announcement ─────────────────────────────────────────────────────
    "bigannounce", "bigannouncestatus", "setbigannounce",
    "setbigreact", "setbotbigreact", "previewannounce",
    # ── BJ surrender + stay alias ────────────────────────────────────────────
    "bsurrender", "stay",
    # ── Auto-games hint/reveal (eventhost-owned) ─────────────────────────────
    "gamehint", "autogamehint",
    "revealanswer", "revealgameanswer", "autogamereveal",
    # ── System dashboard ─────────────────────────────────────────────────────
    "botdashboard", "botsystem",
    # ── Reward center (banker) ───────────────────────────────────────────────
    "rewardpending", "pendingrewards", "rewardlogs", "markrewardpaid",
    "economyreport",
    # ── Event presets ────────────────────────────────────────────────────────
    "eventpreset",
    # ── Player onboarding aliases ────────────────────────────────────────────
    "begin", "newplayer",
    # ── Daily quest aliases ──────────────────────────────────────────────────
    "dailies", "claimdaily",
    # ── Staff audit log ──────────────────────────────────────────────────────
    "auditlog",
    # ── Weekly leaderboard ───────────────────────────────────────────────────
    "weeklylb", "weeklyleaderboard", "weeklyreset",
    "weeklyrewards", "setweeklyreward", "weeklystatus",
    # ── Fish inventory ───────────────────────────────────────────────────────
    "fishbag", "fishinventory", "fishbook",
    "fishautosell", "fishautosellrare", "sellfishrarity",
    # ── Safe mode + diagnostic ───────────────────────────────────────────────
    "safemode", "active", "repair",
    # ── Player utility (host) ─────────────────────────────────────────────────
    "menu", "cooldowns", "rewards", "wherebots", "updates", "rankup",
    # ── Suggestions + bug reports ─────────────────────────────────────────────
    "suggest", "suggestions", "bugreport", "bugreports",
    # ── Event votes ───────────────────────────────────────────────────────────
    "eventvote", "voteevent",
    # ── Subscriber notification preferences ───────────────────────────────────
    "notif", "notifon", "notifoff", "notifall",
    "notifstatus", "notifpreview",
    "notifyon", "notifyoff",
    "sub", "unsub", "forcesub", "fixsub",
    "subnotify", "subnotifyinvite", "subnotifystatus",
    # ── Gold Rain (new system — BankerBot) ───────────────────────────────────
    "raingold", "goldstorm", "golddrop",
    "cancelgoldrain", "goldrainstatus",
    "goldrainhistory", "goldraininterval", "setgoldraininterval",
    "goldrainreplace", "goldrainpace", "setgoldrainpace",
    # ── Message cap testing (host / EmceeBot) ────────────────────────────────
    "msgcap", "setmsgcap",
    # ── Autospawn + role listing (host / security) ───────────────────────────
    "autospawn", "roles", "rolemembers",
    # ── BJ shoe management ───────────────────────────────────────────────────
    "bjshoereset", "bjforce", "bjtest",
    "bjadmin", "bjadminhelp", "staffbj", "staffbjhelp",
    # ── Economy audit ────────────────────────────────────────────────────────
    "economyaudit", "gameprices", "gameprice", "setgameprice",
    # ── Message audit + help audit ───────────────────────────────────────────
    "messageaudit", "helpaudit",
    # ── Staff help navigation ─────────────────────────────────────────────────
    "staffhelp",
    # ── Rarity chance commands ───────────────────────────────────────────────
    "minechances", "fishchances", "fishingchances", "raritychances",
    # ── RoleSpawn / AutoSpawn ────────────────────────────────────────────────
    "rolespawn", "rolespawns", "setrolespawn", "delrolespawn",
    # ── VIP — player + staff ─────────────────────────────────────────────────
    "vip", "vipperks", "myvip", "giftvip", "viplist", "grantvip",
    "removevip", "addvip", "vips", "setvipprice",
    # ── Donation / Sponsorship ───────────────────────────────────────────────
    "donate", "donationgoal", "topdonors", "topdonators", "donators",
    "donationdebug",
    "toptippers", "toptipped", "toptipreceivers", "p2pgolddebug",
    "sponsor", "sponsorgoldrain",
    "sponsorevent", "supporter", "perks",
    "setdonationgoal", "donationaudit", "setsponsorprice",
    # ── Party Tip Wallet (ChillTopiaMC / host) ────────────────────────────────
    "party", "tip", "tipall",
    "pton", "ptoff", "ptstatus", "ptenable", "ptdisable",
    "ptwallet", "ptadd", "ptremove", "ptclear",
    "ptlist", "ptlimits", "ptlimit",
    "partytipper",
    "partywallet", "setpartywallet",
    "partytipperadd", "partytipperremove", "partytipperclear",
    "partytippers", "partytipperlimits", "setpartytipperlimit",
    # ── Luxe Tickets premium economy (3.1I ADDON / UPDATE) ───────────────────
    "tickets", "luxe",
    "luxeshop", "premiumshop",
    "buyticket", "buyluxe",
    "buycoins",
    "use",
    "autotime", "minetime", "fishtime",
    "autoconvert",
    "tipadmin", "tipconfig",
    "economydefaults",
    "luxeadmin",
    "vipadmin",
    # ── Luxe Ticket admin commands ────────────────────────────────────────────
    "addtickets", "removetickets", "settickets",
    "sendtickets", "ticketbalance", "ticketlogs",
    "ticketadmin", "ticketrate",
    # ── Tip / conversion audit commands ──────────────────────────────────────
    "tipaudit", "tipauditdetails", "conversionlogs",
    # 3.1L profile + social
    "myprofile", "flex", "showoff", "card",
    "profilesettings", "profilehelp",
    # 3.1K event sub-command aliases
    "eventschedule", "eventactive", "eventnext",
    "seasonpayout", "payouthistory",
    # 3.1J missions
    "missions", "dailymissions", "dailygoals",
    "weekly", "weeklymissions", "weeklygoals",
    "claimmission",
    "claimdaily",
    "claimweekly",
    "milestones", "collectionrewards",
    "claimmilestone",
    "xp", "rank",
    "season", "topseason", "seasonrewards",
    "today", "progress",
    "missionadmin",
    "retentionadmin",
    # 3.1Q — Beta/Launch Readiness
    "betamode", "betacheck", "betadash",
    "staffdash", "stafftools",
    "testmenu", "betahelp", "quickstart",
    "issueadmin",
    "bugs",
    "errors",
    "launchready",
    "announce",
    "announceadmin",
    # 3.1R — Beta Review + Live Balancing
    "betatest",
    "topissues",
    "balanceaudit",
    "livebalance",
    "luxebalance",
    "retentionreview",
    "eventreview",
    "seasonreview",
    "funnel",
    "betareport",
    "launchblockers",
    "betastaff",
    # 3.1S — Release Candidate + Production Lock
    "rcmode", "production", "featurefreeze", "economylock", "registrylock",
    "releasenotes", "version", "botversion",
    "qastatus", "ownerqa", "ownertest",
    "stablecheck", "hotfixpolicy", "stablelock",
    "backup", "rollbackplan", "restorebackup",
    "ownerchecklist", "launchownercheck",
    "launchannounce",
    "whatsnew", "new", "v32", "knownissues",
    "releasedash", "finalaudit",
    # 3.2A — Public Launch + Post-Launch Monitoring
    "launchmode", "postlaunch", "livehealth",
    "bugdash", "feedbackdash", "dailyreview",
    "economymonitor", "luxemonitor", "retentionmonitor",
    "eventmonitor", "casinomonitor", "bjmonitor", "pokermonitor",
    "errordash", "hotfix", "hotfixlog", "launchlocks", "snapshot",
    # ── Badge market: extended commands (routed, previously missing from set) ──
    "allbadges", "badge", "badgesearch", "badgeprofile", "badgestatus",
    "badgewishlist", "badgeadminhelp", "badgeaudit", "badgelogs",
    "claimbadge", "commonbadges",
    # ── Notification / alert ────────────────────────────────────────────────────
    "alerts",
    # ── Announcement ───────────────────────────────────────────────────────────
    "announcement",
    # ── Shop / home navigation ──────────────────────────────────────────────────
    "botshome",
    # ── Coin pack / buy commands ────────────────────────────────────────────────
    "buypack", "packs", "buychillcoins", "coinpack", "coinpackadmin",
    # ── Event points shorthand ──────────────────────────────────────────────────
    "ep",
    "epicbadges", "legendarybadges", "mythicbadges",
    "flexbadge", "giftbadge", "lockbadge", "lockedbadges", "mywishlist",
    "eventalert", "gamealert", "notifyadmin", "notifyhelp2",
    "luxehelp",
    "handlog", "lasthand", "pokeraudit", "pokereconomy", "pokerguide",
    "confirmbuycoins", "cancelbuycoins",
    "boostaudit",
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
    "blackjack", "bjbet", "bet", "hit", "stand", "double", "split",
    "insurance", "bi", "surrender", "shoe", "bjshoe",
    "howtoplay", "gameguide", "games",
    "settimeexp", "settimeexpcap", "settimeexptick", "settimeexpbonus",
    "timeexpstatus",
    "displaybadges", "displaytitles", "displayformat", "displaytest",
    "botmessageformat", "setbotmessageformat",
    "msgtest", "msgboxtest", "msgsplitpreview",
    "casinosettings", "casinolimits", "casinotoggles",
    "setbjlimits", "setrbjlimits", "setbjactiontimer", "setrbjactiontimer",
    "setbjmaxsplits", "setrbjmaxsplits",
    "resetbjlimits", "resetrbjlimits",
    "resetbjstats", "resetrbjstats", "resetpokerstats", "resetcasinostats",
    "setpokerbuyin", "setpokerplayers", "setpokertimer", "setpokerraise",
    "setpokerdailywinlimit", "setpokerdailylosslimit", "resetpokerlimits",
    "poker", "pokerhelp", "pokerstatus", "pokercleanup",
    "bjstatus", "blackjackhelp", "bjforce", "bjtest",
    "bjadmin", "bjadminhelp", "staffbj", "staffbjhelp",
    "send", "bank", "bankstats", "transactions", "banknotify",
    "viewtx", "bankwatch", "ledger", "auditbank", "banksettings",
    "bankblock", "bankunblock",
    "setminsend", "setmaxsend", "setsendlimit", "setminlevelsend",
    "setmintotalearned", "setmindailyclaims", "setsendtax", "sethighriskblocks",
    "notifications", "clearnotifications",
    "balance", "bal", "daily", "wallet",
    "leaderboard", "lb", "top",
    "toprich", "topminers", "topfishers", "topstreaks",
    "tip", "tiprate", "tipstats", "tipleaderboard",
    "profile", "whois", "me", "stats", "badges", "titles", "privacy", "dashboard",
    "shop", "titleinfo", "badgeinfo", "buy", "equip", "myitems",
    "vipstatus", "vipshop", "buyvip",
    "quests", "dailyquests", "weeklyquests", "claimquest",
    "achievements", "claimachievements",
    "rep", "reputation", "repstats", "toprep", "repleaderboard",
    "event", "events", "nextevent", "next", "schedule",
    "eventstatus", "eventloop", "eventpoints", "eventshop",
    "startevent", "stopevent", "autoevents",
    "setautoeventinterval", "setautoeventduration",
    "report", "bug", "myreports",
    "reports", "reportinfo", "closereport", "reportwatch",
    "addvip", "removevip", "vips", "goldrainvip",
    "settiprate", "settipcap", "settiptier",
    "goldtip", "tipgold", "goldreward", "rewardgold",
    "goldrain", "raingold", "goldstorm", "golddrop",
    "goldrainall", "goldrefund",
    "cancelgoldrain", "goldrainstatus", "goldrainhistory",
    "goldraininterval", "setgoldraininterval",
    "goldrainreplace", "goldrainpace", "setgoldrainpace",
    "msgcap", "setmsgcap",
    "tipcoinrate", "settipcoinrate", "bottiplogs",
    "addtickets", "removetickets", "settickets",
    "sendtickets", "ticketbalance", "ticketlogs",
    "ticketadmin", "ticketrate",
    "tipaudit", "tipauditdetails", "conversionlogs",
    "mingoldtip", "setmingoldtip",
    "tiplb", "tipleaderboard", "bottiplb", "bottipleaderboard",
    "roomtiplb", "roomtipleaderboard", "alltiplb", "alltipleaderboard",
    "tipreceiverlb", "topreceivers",
    "addcoins", "removecoins", "setcoins", "resetcoins",
    "addxp", "removexp", "setxp", "setlevel", "addlevel",
    "addrep", "removerep", "setrep", "resetrep", "addeventcoins",
    "givetitle", "removetitle", "givebadge", "removebadge",
    "addmanager", "removemanager", "addmoderator", "removemoderator",
    "allstaff",
    "adminlogs", "adminpanel", "checkhelp", "checkcommands", "currencycheck",
    "missingcommands", "routecheck", "silentcheck", "commandtest",
    "dbstats", "maintenance", "bankblock",
    "announce", "resetgame", "setrules", "rules", "dailyadmin",
    "warn", "warnings", "mute", "unmute", "mutes", "mutestatus", "forceunmute",
    "softban", "unsoftban", "safetyadmin", "economysafety", "safetydash",
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
    # ── mining weight LB + settings (new) ────────────────────────────────────
    "oreweightlb", "myheaviest", "topweights", "oreweights",
    "mineannounce", "oreannounce",
})

# ---------------------------------------------------------------------------
# VISIBLE_CMDS — commands literally shown in the 12 public help pages
# (help / gamehelp / casinohelp / coinhelp / bankhelp / shophelp /
#  profilehelp / progresshelp / eventhelp / staffhelp / adminhelp / goldhelp)
# This is the set /checkcommands checks by default.
# ---------------------------------------------------------------------------

VISIBLE_CMDS: frozenset[str] = frozenset({
    # ── navigation ──────────────────────────────────────────────────────────
    "help",
    "gamehelp", "casinohelp", "coinhelp", "bankhelp", "shophelp",
    "profilehelp", "progresshelp", "eventhelp", "staffhelp", "adminhelp",
    "goldhelp",
    # ── /gamehelp ───────────────────────────────────────────────────────────
    "trivia", "scramble", "riddle", "answer", "coinflip",
    "autogames", "setgametimer",
    # ── /casinohelp ─────────────────────────────────────────────────────────
    "bjoin", "rjoin", "p",
    "bjhelp", "rbjhelp", "pokerhelp",
    "bt", "rt", "bhand", "rhand", "rshoe", "bstats", "rstats", "mycasino",
    "casinosettings", "casinolimits", "casinotoggles",
    "bj", "rbj", "poker",
    # ── /coinhelp ───────────────────────────────────────────────────────────
    "balance", "daily", "send", "tip",
    "leaderboard", "lb", "top",
    "toprich", "topminers", "topfishers", "topstreaks",
    # ── /bankhelp ───────────────────────────────────────────────────────────
    "bank", "bankstats", "transactions", "banknotify",
    "notifications", "clearnotifications", "viewtx", "bankwatch", "banksettings",
    # ── /shophelp ───────────────────────────────────────────────────────────
    "shop", "buy", "myitems", "equip", "confirmbuy", "vipstatus",
    "mybadges",
    "badgemarket", "badgebuy", "badgelist", "badgecancel",
    "mybadgelistings", "badgeprices",
    "buybadge", "badgeshop", "marketbadges", "sellbadge", "cancelbadge",
    "equipbadge", "staffbadge",
    "mylistings", "marketsearch", "marketfilter", "marketaudit", "marketdebug",
    "forcelistingcancel", "clearbadgelocks",
    "trade", "tradeadd", "tradecoins", "tradeview", "tradeconfirm", "tradecancel",
    "helpmarket", "helptrade", "markethelp", "tradehelp",
    "badgehelp", "helpbadge", "helpbadges",
    # ── /profilehelp ────────────────────────────────────────────────────────
    "profile", "whois", "me", "stats", "badges", "titles", "privacy", "dashboard",
    # ── /progresshelp ───────────────────────────────────────────────────────
    "quests", "dailyquests", "weeklyquests", "claimquest",
    "achievements", "claimachievements",
    # ── /eventhelp ──────────────────────────────────────────────────────────
    "events", "nextevent", "next", "schedule", "eventstatus", "eventloop",
    "eventshop", "eventpoints", "startevent",
    "autoevents", "setautoeventinterval", "setautoeventduration",
    "eventlist", "eventpreview",
    "aepool", "autoeventpool", "aeadd", "aeremove",
    "aequeue", "autoeventqueue", "aenext", "autoeventnext",
    "aestatus", "eventheartbeat", "eventscheduler",
    "eventcooldowns", "seteventcooldown",
    "eventweights", "seteventweight", "eventhistory",
    # ── /staffhelp ──────────────────────────────────────────────────────────
    "modhelp", "managerhelp", "ownerhelp",
    "mycommands", "adminpanel", "adminlogs", "status", "quicktoggles",
    "audithelp", "reporthelp",
    # ── /adminhelp ──────────────────────────────────────────────────────────
    "control",
    "addcoins", "removecoins", "setcoins", "editcoins", "resetcoins",
    "addxp", "removexp", "setxp", "setlevel", "addlevel", "removelevel",
    "addrep", "removerep", "setrep", "resetrep",
    "addeventcoins", "seteventcoins",
    "givetitle", "settitle", "givebadge", "setbadge",
    "addvip", "setvipprice",
    "addmanager", "removemanager", "addmoderator", "removemoderator",
    "allstaff",
    "resetbjstats", "resetrbjstats", "resetpokerstats", "resetcasinostats",
    "resetbjlimits",
    "adminloginfo", "dbstats", "maintenance", "bankblock",
    "checkcommands", "checkhelp",
    "missingcommands", "routecheck", "silentcheck", "commandtest",
    # ── /ownerhelp ──────────────────────────────────────────────────────────
    "ownerpanel",
    # ── /goldhelp ───────────────────────────────────────────────────────────
    "goldtip", "tipgold", "goldreward", "rewardgold",
    "goldrefund", "goldrain", "raingold", "goldstorm", "golddrop",
    "goldrainall", "cancelgoldrain", "goldrainstatus",
    "goldrainhistory", "goldraininterval", "setgoldraininterval",
    "goldrainreplace", "goldrainpace", "setgoldrainpace",
    "goldrainrole", "goldrainvip", "goldraintitle", "goldrainbadge",
    "goldraineligible", "goldrainlist",
    "setgoldrainstaff", "setgoldrainmax",
    "msgcap", "setmsgcap",
    "tiplb", "tipleaderboard", "bottiplb", "bottipleaderboard",
    "roomtiplb", "roomtipleaderboard", "alltiplb", "alltipleaderboard",
    "tipreceiverlb", "topreceivers",
    # ── New staff controls ───────────────────────────────────────────────────
    "autospawn", "roles", "bjshoereset", "bjforce", "bjtest",
    "bjadmin", "bjadminhelp", "staffbj", "staffbjhelp",
    "economyaudit", "gameprices", "gameprice", "setgameprice",
    "messageaudit", "helpaudit", "staffhelp",
    "bios", "checkbios", "checkonboarding",
    # ── Rarity chance commands ───────────────────────────────────────────────
    "minechances", "fishchances", "fishingchances", "raritychances",
    # ── RoleSpawn / AutoSpawn ────────────────────────────────────────────────
    "rolespawn", "rolespawns", "setrolespawn", "delrolespawn",
    # ── VIP — player + staff ─────────────────────────────────────────────────
    "vip", "vipperks", "myvip", "giftvip", "viplist", "grantvip",
    "removevip", "addvip", "vips", "setvipprice",
    # ── Donation / Sponsorship ───────────────────────────────────────────────
    "donate", "donationgoal", "topdonors", "topdonators", "donators",
    "donationdebug",
    "toptippers", "toptipped", "toptipreceivers", "p2pgolddebug",
    "sponsor", "sponsorgoldrain",
    "sponsorevent", "supporter", "perks",
    "setdonationgoal", "donationaudit", "setsponsorprice",
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

_PAGE_SIZE = 8


def _cmd_name(item) -> str | None:
    """Normalize a command record to a bare command name (no ! prefix)."""
    if isinstance(item, str):
        return item.lstrip("!/").strip() or None
    if isinstance(item, dict):
        for key in ("command", "name", "cmd"):
            val = item.get(key)
            if isinstance(val, str) and val.strip():
                return val.lstrip("!/").strip()
    return None


def _paginate(title: str, items: list, page: int) -> tuple[str, int]:
    """Return (message, total_pages).  page is 1-indexed.
    Each command shown on its own line with ! prefix."""
    safe: list[str] = []
    for it in items:
        n = _cmd_name(it)
        if n:
            safe.append(n)
    total = len(safe)
    if total == 0:
        return f"{title}: none.", 1
    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * _PAGE_SIZE
    chunk = safe[start:start + _PAGE_SIZE]
    suffix = f" {page}/{total_pages}" if total_pages > 1 else ""
    header = f"⚠️ {title}{suffix}"
    body = header + "\n" + "\n".join(f"!{c}" for c in chunk)
    return body[:249], total_pages


# ---------------------------------------------------------------------------
# /checkcommands
# ---------------------------------------------------------------------------

async def handle_checkcommands(bot: BaseBot, user: User, args: list | None = None) -> None:
    """
    /checkcommands        — visible-help commands only
    /checkcommands all    — full breakdown: active / hidden / deprecated
    """
    if not _can_audit(user.username):
        await _w(bot, user.id, "Staff only.")
        return

    use_all = args and len(args) > 1 and args[1].lower() == "all"

    if use_all:
        try:
            import sys, importlib
            _main = sys.modules.get("__main__") or importlib.import_module("__main__")
            all_set = getattr(_main, "ALL_KNOWN_COMMANDS", None) or VISIBLE_CMDS
        except Exception:
            all_set = VISIBLE_CMDS

        hidden  = HIDDEN_CMDS & all_set
        depr    = DEPRECATED_CMDS & all_set
        active  = all_set - hidden - depr

        try:
            from modules.multi_bot import _DEFAULT_COMMAND_OWNERS
            owners_set      = set(_DEFAULT_COMMAND_OWNERS.keys())
            active_no_owner = len(active - owners_set)
        except Exception:
            active_no_owner = -1

        active_missing_names = sorted(active - ROUTED_COMMANDS)
        active_missing       = len(active_missing_names)

        print(f"[AUDIT] /checkcommands all @{user.username}: "
              f"known={len(all_set)} active={len(active)} hidden={len(hidden)} "
              f"depr={len(depr)} missing={active_missing} names={active_missing_names} "
              f"noowner={active_no_owner}")
        await _w(bot, user.id,
                 (f"CmdCheck[All]: Known {len(all_set)} | Active {len(active)} | "
                  f"Hidden {len(hidden)} | Depr {len(depr)}")[:249])
        await _w(bot, user.id,
                 (f"  Active: Routed {len(active & ROUTED_COMMANDS)} | "
                  f"Missing {active_missing} | NoOwner {active_no_owner}"
                  f"  (!commandissues for details)")[:249])
        if active_missing_names:
            names_str = ", ".join(f"!{c}" for c in active_missing_names)
            await _w(bot, user.id, f"Missing: {names_str}"[:249])
    else:
        # Subtract hidden + deprecated so this scan agrees with the 'all' scan's
        # active-set definition (ALL_KNOWN_COMMANDS - hidden - depr).
        check_set = VISIBLE_CMDS - HIDDEN_CMDS - DEPRECATED_CMDS
        routed_ok = len(ROUTED_COMMANDS & check_set)
        missing_names = sorted(check_set - ROUTED_COMMANDS)
        missing   = len(missing_names)
        try:
            from modules.multi_bot import _DEFAULT_COMMAND_OWNERS
            no_owner = len(check_set - set(_DEFAULT_COMMAND_OWNERS.keys()))
        except Exception:
            no_owner = -1
        print(f"[AUDIT] /checkcommands(Visible) @{user.username}: "
              f"set={len(check_set)} routed={routed_ok} missing={missing} "
              f"names={missing_names} no_owner={no_owner}")
        await _w(bot, user.id,
                 (f"CmdCheck[Visible]: Known {len(check_set)} | Routed {routed_ok} | "
                  f"Missing {missing} | NoOwner {no_owner}"
                  f"  (!checkcommands all = full scan)")[:249])
        if missing_names:
            names_str = ", ".join(f"!{c}" for c in missing_names)
            await _w(bot, user.id, f"Missing: {names_str}"[:249])


# ---------------------------------------------------------------------------
# /commandissues <category> [page]
# ---------------------------------------------------------------------------

async def handle_commandissues(
    bot: BaseBot,
    user: User,
    args: list[str] | None = None,
    all_known: set | None = None,
) -> None:
    """
    /commandissues missing [page]    — active commands with no route
    /commandissues noowner [page]    — active commands with no owner
    /commandissues deprecated [page] — deprecated commands
    /commandissues hidden [page]     — hidden/internal commands
    """
    if not _can_audit(user.username):
        await _w(bot, user.id, "Staff only.")
        return

    _category_raw = args[1].lower() if args and len(args) >= 2 else ""
    # Normalise aliases
    _CAT_ALIASES = {
        "no-owner":  "noowner",
        "no_owner":  "noowner",
        "no-handler": "nohandler",
        "no_handler": "nohandler",
    }
    category = _CAT_ALIASES.get(_category_raw, _category_raw)

    page = 1
    if args and len(args) >= 3:
        try:
            page = int(args[2])
        except (ValueError, IndexError):
            page = 1
    if page < 1:
        page = 1

    if category and category not in (
        "missing", "noowner", "nohandler", "deprecated", "hidden"
    ):
        await _w(bot, user.id,
                 "⚠️ Unknown issue type.\n"
                 "Use: missing, noowner, nohandler, deprecated, hidden")
        return

    try:
        if all_known is None:
            try:
                import sys, importlib
                _main = sys.modules.get("__main__") or importlib.import_module("__main__")
                all_known = getattr(_main, "ALL_KNOWN_COMMANDS", None) or set()
            except Exception:
                all_known = set()

        hidden = HIDDEN_CMDS & all_known
        depr   = DEPRECATED_CMDS & all_known
        active = all_known - hidden - depr

        if not category:
            # No-arg: combined summary of broken commands
            try:
                from modules.multi_bot import _DEFAULT_COMMAND_OWNERS
                owners_set = set(_DEFAULT_COMMAND_OWNERS.keys())
            except Exception:
                owners_set = set()
            try:
                from modules.command_registry import get_entry as _reg_get
                no_handler = sorted(c for c in active if _reg_get(c) is None)
            except Exception:
                no_handler = []
            missing_set = sorted(active - ROUTED_COMMANDS)
            noowner_set = sorted(active - owners_set)
            combined = set(missing_set) | set(noowner_set) | set(no_handler)
            if not combined:
                await _w(bot, user.id,
                         "✅ No command issues.\nRoutes, owners, and handlers are clean.")
                return
            parts = []
            if missing_set:
                parts.append(f"{len(missing_set)} no-route")
            if noowner_set:
                parts.append(f"{len(noowner_set)} no-owner")
            if no_handler:
                parts.append(f"{len(no_handler)} no-handler")
            await _w(bot, user.id,
                     (f"⚠️ Issues: {', '.join(parts)}\n"
                      f"!commandissues missing|noowner|nohandler|deprecated|hidden [pg]")[:249])
            preview = sorted(combined)[:8]
            await _w(bot, user.id,
                     ("Examples: " + ", ".join(f"!{c}" for c in preview))[:249])
            return
        elif category == "missing":
            items = sorted(active - ROUTED_COMMANDS)
            title = "Missing routes"
        elif category == "nohandler":
            try:
                from modules.command_registry import get_entry as _reg_get
                items = sorted(c for c in active if _reg_get(c) is None)
            except Exception:
                items = []
            title = "No handler (not in registry)"
        elif category == "noowner":
            try:
                from modules.multi_bot import _DEFAULT_COMMAND_OWNERS
                owners_set = set(_DEFAULT_COMMAND_OWNERS.keys())
            except Exception:
                owners_set = set()
            items = sorted(active - owners_set)
            title = "No owner"
        elif category == "deprecated":
            items = sorted(depr)
            title = "Deprecated"
        else:
            items = sorted(hidden)
            title = "Hidden"

        print(f"[AUDIT] /commandissues {category} p{page} @{user.username}: {len(items)} items")
        if not items:
            await _w(bot, user.id, f"✅ {title}: none — all clean.")
            return
        msg, total_pages = _paginate(title, items, page)
        await _w(bot, user.id, msg)
        if page < total_pages:
            await _w(bot, user.id, f"More: !commandissues {category} {page + 1}")

    except Exception as _ci_err:
        print(
            f"[COMMAND AUDIT ERROR]\n"
            f"cmd=!commandissues\n"
            f"category={category}\n"
            f"page={page}\n"
            f"user=@{user.username}\n"
            f"error={_ci_err!r}"
        )
        await _w(bot, user.id, "⚠️ Command audit error. Bot stayed online.")


# ---------------------------------------------------------------------------
# /commandaudit [page]
# ---------------------------------------------------------------------------

async def handle_commandaudit(bot: BaseBot, user: User, args: list[str] | None = None) -> None:
    """
    /commandaudit [page]
    Lists help-visible commands that have no routing handler.  Admin+.
    """
    if not _can_audit(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    page = 1
    if args and len(args) >= 2:
        try:
            page = int(args[1])
        except ValueError:
            pass
    broken = sorted(HELP_CMDS - ROUTED_COMMANDS)
    print(f"[AUDIT] /commandaudit @{user.username}: {broken}")
    if not broken:
        await _w(bot, user.id,
                 f"Cmd Audit: {len(ROUTED_COMMANDS)} routed | 0 broken — all good.")
        return
    per_page = 10
    total_pages = max(1, (len(broken) + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    chunk = broken[(page - 1) * per_page: page * per_page]
    lines = ", ".join(f"/{c}" for c in chunk)
    await _w(bot, user.id, (f"Broken p{page}/{total_pages}: {lines}")[:249])


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
        await _w(bot, user.id, f"More: !missingcommands {page + 1}")


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
        await _w(bot, user.id, f"More: !routecheck {page + 1}")


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
        await _w(bot, user.id, "Usage: !commandtest <command>")
        return
    cmd = args[1].lstrip("!/").lower()
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
    ("Banker",   "banker",     ["bal", "send", "daily", "goldrain", "goldrainpace"]),
    ("BJ",       "blackjack",  ["bj", "bjoin", "bh", "bs", "rjoin", "rh"]),
    ("Poker",    "poker",      ["p", "check", "call", "fold", "allin"]),
    ("Miner",    "miner",      ["mine", "tool", "sellores", "minelb"]),
    ("Shop",     "shopkeeper", ["shop", "buy", "equip", "badgemarket"]),
    ("Security", "security",   ["report", "warn", "mute", "kick"]),
    ("DJ",       "dj",         ["emote", "dance", "hug", "heart"]),
    ("Events",   "eventhost",  ["announce", "startevent"]),
    ("Host",     "host",       ["help", "commandtest", "fixcommands", "msgcap"]),
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


# ---------------------------------------------------------------------------
# /commandtestall  /ctall  <cmd1> <cmd2> ...
# ---------------------------------------------------------------------------

async def handle_commandtestall(
    bot: BaseBot,
    user: User,
    args: list[str],
    all_known: set | None = None,
) -> None:
    """/commandtestall <cmd1> <cmd2> ... — test if commands are routed + owned."""
    if not _can_audit(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id,
                 "Usage: !commandtestall <cmd1> <cmd2> ...  (alias: /ctall)")
        return
    cmds = [a.lower().lstrip("/") for a in args[1:]]
    if all_known is None:
        try:
            import sys, importlib
            _main = sys.modules.get("__main__") or importlib.import_module("__main__")
            all_known = getattr(_main, "ALL_KNOWN_COMMANDS", None) or set()
        except Exception:
            all_known = set()
    try:
        from modules.multi_bot import _DEFAULT_COMMAND_OWNERS
        owners_set = set(_DEFAULT_COMMAND_OWNERS.keys())
    except Exception:
        owners_set = set()

    results: list[str] = []
    ok_count = 0
    for cmd in cmds[:20]:  # cap to prevent oversized output
        known  = cmd in all_known
        routed = cmd in ROUTED_COMMANDS
        owned  = cmd in owners_set
        if known and routed and owned:
            results.append(f"/{cmd}✅")
            ok_count += 1
        else:
            issues = []
            if not known:  issues.append("unknown")
            if not routed: issues.append("no-route")
            if not owned:  issues.append("no-owner")
            results.append(f"/{cmd}❌({','.join(issues)})")

    summary = " ".join(results)
    status  = f"{ok_count}/{len(cmds)} OK"
    print(f"[TESTALL] @{user.username}: {status}")
    await _w(bot, user.id, f"CmdTest {status}: {summary}"[:249])


# ---------------------------------------------------------------------------
# /commandtestgroup  /ctgroup  <group>
# ---------------------------------------------------------------------------

async def handle_commandtestgroup(
    bot: BaseBot,
    user: User,
    args: list[str],
    all_known: set | None = None,
) -> None:
    """/commandtestgroup <group> — test all commands in a registry category."""
    if not _can_audit(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id,
                 "Usage: !commandtestgroup <group>  e.g. /ctgroup mining")
        return
    group = args[1].lower()
    if all_known is None:
        try:
            import sys, importlib
            _main = sys.modules.get("__main__") or importlib.import_module("__main__")
            all_known = getattr(_main, "ALL_KNOWN_COMMANDS", None) or set()
        except Exception:
            all_known = set()
    try:
        from modules.command_registry import REGISTRY, alias_map as _amap
        reg_all      = {**REGISTRY,
                        **{a: REGISTRY[p] for a, p in _amap.items() if p in REGISTRY}}
        group_cmds   = {cmd for cmd, entry in reg_all.items() if entry.cat == group}
        valid_groups = sorted({e.cat for e in REGISTRY.values()})
    except Exception:
        group_cmds   = set()
        valid_groups = []

    if not group_cmds:
        groups_str = ", ".join(valid_groups[:12]) if valid_groups else "unknown"
        await _w(bot, user.id,
                 f"No commands in group '{group}'. Valid: {groups_str}"[:249])
        return

    try:
        from modules.multi_bot import _DEFAULT_COMMAND_OWNERS
        owners_set = set(_DEFAULT_COMMAND_OWNERS.keys())
    except Exception:
        owners_set = set()

    ok  = sorted(c for c in group_cmds if c in ROUTED_COMMANDS and c in owners_set)
    bad = sorted(c for c in group_cmds
                 if c not in ROUTED_COMMANDS or c not in owners_set)
    total   = len(group_cmds)
    summary = f"CmdGroup '{group}': {len(ok)}/{total} OK"
    if bad:
        summary += " | Issues: " + ", ".join(f"/{c}" for c in bad[:8])
    print(f"[TESTGROUP] @{user.username} group={group}: "
          f"{len(ok)}/{total} ok  bad={bad[:8]}")
    await _w(bot, user.id, summary[:249])

# ---------------------------------------------------------------------------
# /auditlog [type] — staff audit log viewer
# ---------------------------------------------------------------------------

async def handle_auditlog(bot, user, args: list[str]) -> None:
    """/auditlog [type] — view staff action audit log. Security-owned."""
    import database as _db
    from modules.permissions import can_moderate
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return

    filter_type: str | None = None
    if len(args) >= 2:
        filter_type = args[1].lower()

    rows = _db.get_staff_audit_logs(action_type=filter_type, limit=10)
    if not rows:
        if filter_type:
            await _w(bot, user.id, f"No audit logs for type: {filter_type}")
        else:
            await _w(bot, user.id, "📋 No staff audit logs yet.")
        return

    lines = ["📋 Staff Audit Log"]
    if filter_type:
        lines[0] += f" [{filter_type}]"
    for r in rows:
        ts  = str(r.get("created_at", ""))[:10]
        act = r.get("actor_username", "?")
        typ = r.get("action_type", "?")[:16]
        det = r.get("details", "")[:30]
        lines.append(f"{ts} @{act}: {typ} {det}")
    await _w(bot, user.id, "\n".join(lines)[:249])
