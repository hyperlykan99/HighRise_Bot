"""
modules/multi_bot.py
Multi-bot system — command ownership gating, heartbeat, staff controls.

BOT_MODE=all (default) → always handles everything (backwards-compatible).
BOT_MODE=blackjack    → BJ + RBJ commands only.
BOT_MODE=poker        → Poker commands only.
BOT_MODE=dealer       → Legacy casino fallback if dedicated bots offline.
BOT_MODE=host         → Help, profiles, room utilities, unknown-cmd fallback.
All other modes handle their own module commands.
"""

from __future__ import annotations
import asyncio
import time
from datetime import datetime, timezone

import database as db
from config import BOT_ID, BOT_MODE, BOT_USERNAME, BOT_EXTRA_MODES
from modules.permissions import can_manage_economy

# ---------------------------------------------------------------------------
# Default command → bot_mode ownership map
# bot_command_ownership DB table overrides any entry at runtime.
# ---------------------------------------------------------------------------

_DEFAULT_COMMAND_OWNERS: dict[str, str] = {
    # ── host ────────────────────────────────────────────────────────────────
    "help": "host", "mycommands": "host", "helpsearch": "host",
    "start": "host", "tutorial": "host", "guide": "host", "newbiehelp": "host",
    "profile": "host", "me": "host", "whois": "host", "pinfo": "host",
    "privacy": "host", "rules": "host", "roleshelp": "host",
    "players": "host", "roomlist": "host", "online": "host",
    "roomhelp": "host", "teleporthelp": "host", "emotehelp": "host",
    "alerthelp": "host", "welcomehelp": "host", "socialhelp": "host",
    "control": "host", "status": "host", "roomstatus": "host",
    "botmodehelp": "host", "multibothelp": "host",
    "howtoplay": "host", "gameguide": "host", "games": "host",
    # ── display format settings ───────────────────────────────────────────────
    "displaybadges": "host", "displaytitles": "host", "displayformat": "host",
    "displaytest": "host",
    "botmessageformat": "host", "setbotmessageformat": "host",
    "msgtest": "host", "msgboxtest": "host", "msgsplitpreview": "host",
    # ── time-in-room EXP admin commands ──────────────────────────────────────
    "settimeexp": "host", "settimeexpcap": "host",
    "settimeexptick": "host", "settimeexpbonus": "host",
    "timeexpstatus": "host",
    # General casino info pages — host owns so only one bot replies
    "casino": "host", "casinohelp": "host",
    "casinosettings": "host", "casinolimits": "host",
    "casinotoggles": "host", "mycasino": "host",
    # ── banker ──────────────────────────────────────────────────────────────
    "bal": "banker", "balance": "banker", "b": "banker",
    "wallet": "banker", "w": "banker",
    "coins": "banker", "coin": "banker", "money": "banker",
    "send": "banker", "bank": "banker",
    "transactions": "banker", "bankstats": "banker",
    "bankhelp": "banker", "banknotify": "banker",
    "daily": "banker", "leaderboard": "banker", "lb": "banker",
    "addcoins": "banker", "setcoins": "banker", "removecoins": "banker",
    "resetcoins": "banker", "editcoins": "banker",
    "viewtx": "banker", "ledger": "banker",
    "bankblock": "banker", "bankunblock": "banker",
    "coinhelp": "banker", "bankadminhelp": "banker",
    "bankerhelp": "banker",
    "economydbcheck": "banker", "economyrepair": "banker",
    "dash": "banker", "dashboard": "banker",
    # ── blackjack (Casual BJ + RBJ) ─────────────────────────────────────────
    "bj": "blackjack", "bjoin": "blackjack",
    "bh": "blackjack", "bs": "blackjack", "bd": "blackjack", "bsp": "blackjack",
    "bjh": "blackjack", "bjs": "blackjack", "bjd": "blackjack", "bjsp": "blackjack",
    "bt": "blackjack", "bhand": "blackjack", "bjhand": "blackjack",
    "blimits": "blackjack", "bstats": "blackjack", "bjhelp": "blackjack",
    "setbjlimits": "blackjack", "resetbjlimits": "blackjack",
    "setbjactiontimer": "blackjack", "setbjmaxsplits": "blackjack",
    "setbj": "blackjack",
    "rbj": "blackjack", "rjoin": "blackjack",
    "rh": "blackjack", "rs": "blackjack", "rd": "blackjack", "rsp": "blackjack",
    "rbjh": "blackjack", "rbjs": "blackjack", "rbjd": "blackjack", "rbjsp": "blackjack",
    "rt": "blackjack", "rhand": "blackjack", "rbjhand": "blackjack",
    "rshoe": "blackjack", "rlimits": "blackjack", "rstats": "blackjack",
    "rbjhelp": "blackjack",
    "setrbjlimits": "blackjack", "resetrbjlimits": "blackjack",
    "setrbjactiontimer": "blackjack", "setrbjmaxsplits": "blackjack",
    "setrbj": "blackjack",
    # ── Easy BJ / universal shortcuts (all map to RBJ) ──────────────────────
    "blackjack": "blackjack", "bjbet": "blackjack",
    "bet": "blackjack", "hit": "blackjack", "stand": "blackjack",
    "double": "blackjack", "split": "blackjack",
    "insurance": "blackjack", "surrender": "blackjack",
    "shoe": "blackjack", "bjshoe": "blackjack",
    # ── poker ───────────────────────────────────────────────────────────────
    "poker": "poker", "p": "poker",
    "pj": "poker", "pt": "poker", "ptable": "poker",
    "ph": "poker", "pcards": "poker", "po": "poker", "podds": "poker",
    "check": "poker", "ch": "poker",
    "call": "poker", "ca": "poker",
    "raise": "poker", "r": "poker",
    "fold": "poker", "f": "poker",
    "allin": "poker", "ai": "poker", "shove": "poker",
    "pp": "poker", "pplayers": "poker", "pstats": "poker",
    "plb": "poker", "pleaderboard": "poker", "pokerlb": "poker",
    "pokerhelp": "poker", "pokerstats": "poker",
    "sitout": "poker", "sitin": "poker", "rebuy": "poker",
    "pstacks": "poker", "mystack": "poker", "stack": "poker",
    "pokerhistory": "poker", "pokerdebug": "poker",
    "pokerfix": "poker", "pokercleanup": "poker",
    "confirmclosepoker": "poker",
    "casinointegrity": "host", "integritylogs": "host", "carddeliverycheck": "host",
    # ── AI assistant ─────────────────────────────────────────────────────────
    "ask": "host", "ai": "host", "assistant": "host",
    "pendingaction": "host", "confirm": "host", "aidebug": "host", "aicapabilities": "host",
    "setpokercardmarker": "poker",
    "resendcards": "poker", "cards": "poker",
    "pokerdealstatus": "poker", "pokerplayers": "poker",
    "pokerdashboard": "poker", "pdash": "poker", "pokeradmin": "poker",
    "pokerpause": "poker", "pokerresume": "poker",
    "pokerforceadvance": "poker", "pokerforceresend": "poker",
    "pokerturn": "poker", "pokerpots": "poker", "pokeractions": "poker",
    "pokerstylepreview": "poker",
    "pokerresetturn": "poker", "pokerresethand": "poker", "pokerresettable": "poker",
    "setpokertimer": "poker", "setpokerturntimer": "poker",
    "setpokerlobbytimer": "poker", "setpokernexthandtimer": "poker",
    "setpokerblinds": "poker", "setpokerante": "poker",
    "setpokerraise": "poker", "setpokerminplayers": "poker",
    "setpokermaxplayers": "poker",
    # ── miner ───────────────────────────────────────────────────────────────
    "mine": "miner", "m": "miner", "dig": "miner",
    "ores": "miner", "mineinv": "miner",
    "tool": "miner", "pickaxe": "miner",
    "upgradetool": "miner", "upick": "miner",
    "sellores": "miner", "sellore": "miner",
    "mineprofile": "miner", "mp": "miner",
    "minelb": "miner", "minerank": "miner",
    "mineshop": "miner", "minebuy": "miner",
    "craft": "miner", "minedaily": "miner",
    "minehelp": "miner", "miningadmin": "miner",
    "useluckboost": "miner", "usexpboost": "miner", "useenergy": "miner",
    "mining": "miner",
    "miningevent": "miner", "miningevents": "miner",
    "startminingevent": "miner", "stopminingevent": "miner",
    "setminecooldown": "miner", "setmineenergycost": "miner", "setminingenergy": "miner",
    "addore": "miner", "removeore": "miner",
    "settoollevel": "miner", "setminelevel": "miner",
    "addminexp": "miner", "setminexp": "miner",
    "resetmining": "miner", "miningroomrequired": "miner",
    "orelist": "miner",
    "oreprices": "miner", "orevalues": "miner", "orevalue": "miner",
    "simannounce": "miner",
    "forcedrop": "miner", "forcedropore": "miner",
    "forcedropstatus": "miner", "clearforcedrop": "miner",
    "oreinfo": "miner", "oredetail": "miner", "oredetails": "miner",
    "orebook": "miner", "oremastery": "miner", "claimoremastery": "miner", "orestats": "miner",
    "contracts": "miner", "miningjobs": "miner",
    "job": "miner", "deliver": "miner", "claimjob": "miner", "rerolljob": "miner",
    # ── shopkeeper ──────────────────────────────────────────────────────────
    "shop": "shopkeeper", "buy": "shopkeeper",
    "vipshop": "shopkeeper", "buyvip": "shopkeeper",
    "badges": "shopkeeper", "titles": "shopkeeper",
    "mybadges": "shopkeeper", "badgeinfo": "shopkeeper",
    "badgemarket": "shopkeeper", "badgelist": "shopkeeper",
    "badgebuy": "shopkeeper", "badgecancel": "shopkeeper",
    "mybadgelistings": "shopkeeper", "badgeprices": "shopkeeper",
    "equip": "shopkeeper", "myitems": "shopkeeper",
    "titleinfo": "shopkeeper", "shophelp": "host",
    "shopadmin": "shopkeeper", "vipstatus": "shopkeeper",
    # ── security ────────────────────────────────────────────────────────────
    "report": "security", "reports": "security",
    "bug": "security", "myreports": "security", "reporthelp": "security",
    "warn": "security", "warnings": "security",
    "mute": "security", "unmute": "security", "mutes": "security",
    "kick": "security", "ban": "security",
    "tempban": "security", "unban": "security", "bans": "security",
    "modlog": "security", "roomlogs": "security",
    "modhelp": "security", "staffhelp": "security",
    "automod": "security", "setrules": "security",
    # ── dj ──────────────────────────────────────────────────────────────────
    "emote": "dj", "emotes": "dj",
    "stopemote": "dj", "dance": "dj", "wave": "dj",
    "sit": "dj", "clap": "dj",
    "loopemote": "dj", "stoploop": "dj", "stopallloops": "dj",
    "forceemote": "dj", "forceemoteall": "dj",
    "syncdance": "dj", "synchost": "dj", "stopsync": "dj",
    "hug": "dj", "kiss": "dj", "slap": "dj", "punch": "dj",
    "highfive": "dj", "boop": "dj", "waveat": "dj", "cheer": "dj",
    "heart": "dj", "hearts": "dj", "heartlb": "dj",
    "social": "dj", "blocksocial": "dj", "unblocksocial": "dj",
    # ── eventhost ───────────────────────────────────────────────────────────
    "events": "eventhost", "event": "eventhost",
    "eventhelp": "eventhost", "eventstatus": "eventhost",
    "startevent": "eventhost", "stopevent": "eventhost",
    "eventpoints": "eventhost", "eventshop": "eventhost",
    "buyevent": "eventhost",
    "alert": "eventhost", "staffalert": "eventhost",
    "vipalert": "eventhost", "roomalert": "eventhost",
    "announce_subs": "eventhost", "dmnotify": "eventhost",
    "announce": "eventhost", "announce_vip": "eventhost",
    "announce_staff": "eventhost",
    "eventstart": "eventhost", "eventstop": "eventhost",
    "addeventcoins": "eventhost", "removeeventcoins": "eventhost",
    "seteventcoins": "eventhost", "editeventcoins": "eventhost",
    "reseteventcoins": "eventhost",
    "autogames": "eventhost", "autoevents": "eventhost",
    "gameconfig": "eventhost", "fixautogames": "eventhost",
    "autogamesowner": "eventhost",
    "stopautogames": "eventhost", "killautogames": "eventhost",
    "goldtip": "eventhost", "goldrain": "eventhost",
    "goldrainall": "eventhost", "goldrefund": "eventhost",
    "goldraineligible": "eventhost", "goldrainrole": "eventhost",
    "goldrainvip": "eventhost", "goldraintitle": "eventhost",
    "goldrainbadge": "eventhost", "goldrainlist": "eventhost",
    "goldhelp": "eventhost", "goldwallet": "eventhost",
    "goldtips": "eventhost", "goldtx": "eventhost",
    "pendinggold": "eventhost", "confirmgoldtip": "eventhost",
    "setgoldrainstaff": "eventhost", "setgoldrainmax": "eventhost",
    # ── security (additions) ─────────────────────────────────────────────────
    "clearwarnings": "security",
    "reportinfo": "security", "closereport": "security",
    "reportwatch": "security",
    "audit": "security", "audithelp": "security",
    "auditbank": "security", "auditcasino": "security",
    "auditeconomy": "security",
    # ── banker (additions) ───────────────────────────────────────────────────
    "tip": "banker", "gift": "banker",
    "addcoins": "banker", "removecoins": "banker",
    "bankstats": "banker", "banknotify": "banker",
    "bankhelp": "banker", "coinhelp": "banker",
    # ── host (audit / status commands) ───────────────────────────────────────
    "checkcommands": "host", "checkhelp": "host",
    "missingcommands": "host", "routecheck": "host",
    "silentcheck": "host", "commandtest": "host",
    "fixcommands": "host", "testcommands": "host",
    "deploymentcheck": "host", "bothealth": "host",
    "botconflicts": "host", "botmodules": "host",
    "modulehealth": "host", "botheartbeat": "host",
    "botstatus": "host", "botstatus_cluster": "host",
    "taskowners": "host", "activetasks": "host",
    "taskconflicts": "host", "fixtaskowners": "host",
    "restorestatus": "host", "restoreannounce": "host",
    "startupannounce": "host", "modulestartup": "host",
    "startupstatus": "host", "setmainmode": "host",
    "dblockcheck": "host", "routerstatus": "host",
    "bots": "host", "commandintegrity": "host", "commandrepair": "host",
    # ── visible-help additions (help pages) ──────────────────────────────────
    # host — navigation / admin tools
    "gamehelp": "host", "profilehelp": "host", "progresshelp": "host",
    "adminhelp": "host", "managerhelp": "host", "ownerhelp": "host",
    "adminpanel": "host", "adminlogs": "host", "adminloginfo": "host",
    "quicktoggles": "host", "ownerpanel": "host",
    "stats": "host", "allstaff": "host",
    "addmanager": "host", "removemanager": "host",
    "addrep": "host", "removerep": "host",
    "setrep": "host", "resetrep": "host",
    "addxp": "host", "removexp": "host",
    "setxp": "host", "setlevel": "host",
    "addlevel": "host", "removelevel": "host",
    "resetcasinostats": "host", "dbstats": "host", "maintenance": "host",
    # banker — bank notifications / settings
    "notifications": "banker", "clearnotifications": "banker",
    "bankwatch": "banker", "banksettings": "banker",
    # shopkeeper — shop misc
    "confirmbuy": "shopkeeper",
    "givetitle": "shopkeeper", "settitle": "shopkeeper",
    "givebadge": "shopkeeper", "setbadge": "shopkeeper",
    "addvip": "shopkeeper", "setvipprice": "shopkeeper",
    # security — mod role commands
    "addmoderator": "security", "removemoderator": "security",
    # eventhost — games / quests / events
    "trivia": "eventhost", "scramble": "eventhost",
    "riddle": "eventhost", "answer": "eventhost",
    "coinflip": "eventhost", "setgametimer": "eventhost",
    "quests": "eventhost", "dailyquests": "eventhost",
    "weeklyquests": "eventhost", "claimquest": "eventhost",
    "achievements": "eventhost", "claimachievements": "eventhost",
    "setautoeventinterval": "eventhost", "setautoeventduration": "eventhost",
    # blackjack — casino resets
    "resetbjstats": "blackjack", "resetrbjstats": "blackjack",
    # poker — casino resets
    "resetpokerstats": "poker",
    # ── full-registry additions (from /checkcommands all cleanup) ─────────────
    # host — room utilities
    "tp": "host", "tpme": "host", "tphere": "host", "bring": "host",
    "bringall": "host", "tpall": "host", "tprole": "host", "tpvip": "host",
    "tpstaff": "host", "selftp": "host", "goto": "host", "groupteleport": "host",
    "spawns": "host", "spawn": "host", "setspawn": "host", "savepos": "host",
    "delspawn": "host", "spawninfo": "host", "setspawncoords": "host",
    "staffonline": "host", "vipsinroom": "host", "rolelist": "host",
    "players": "host", "online": "host",
    "roomsettings": "host", "setroomsetting": "host", "roomlogs": "host",
    "roomstatus": "host", "roomboost": "host", "boostroom": "host",
    "toggle": "host", "managerpanel": "host",
    "welcome": "host", "setwelcome": "host", "welcometest": "host",
    "resetwelcome": "host", "welcomeinterval": "host",
    "intervals": "host", "addinterval": "host", "delinterval": "host",
    "interval": "host", "intervaltest": "host",
    "repeatmsg": "host", "stoprepeat": "host", "repeatstatus": "host",
    "alert": "host", "clearalerts": "host",
    # host — admin / debug / bot management
    "addadmin": "host", "removeadmin": "host", "admins": "host",
    "addowner": "host", "removeowner": "host", "owners": "host",
    "allcommands": "host", "assignbotmode": "host",
    "autohelp": "host", "backup": "host",
    "botfallback": "host", "botinfo": "host", "botlocks": "host",
    "botmode": "host", "botmodes": "host",
    "botoutfit": "host", "botoutfitlogs": "host", "botoutfits": "host",
    "botprefix": "host", "botprofile": "host", "botstartupannounce": "host",
    "broadcasttest": "host", "casinoadminhelp": "host",
    "categoryprefix": "host", "cleanup": "host",
    "clearpendingnotify": "host", "clearstalebotlocks": "host",
    "commandaudit": "host", "commandowners": "host",
    "confirmcasinoreset": "host", "crashlogs": "host", "createbotmode": "host",
    "dailyadmin": "host", "debugnotify": "host", "debugsub": "host",
    "debugtips": "host", "deletebotmode": "host",
    "delivernotifications": "host", "disablebot": "host", "dressbot": "host",
    "editlevel": "host", "editrep": "host", "editxp": "host",
    "enablebot": "host", "fixbotowners": "host",
    "healthcheck": "host", "level": "host",
    "maintenancehelp": "host", "managers": "host",
    "missingbots": "host", "moduleowners": "host",
    "pendingnotifications": "host", "pendingnotify": "host",
    "profileadmin": "host", "promotelevel": "host", "demotelevel": "host",
    "reloadsettings": "host", "rep": "host",
    "rephelp": "host", "repleaderboard": "host", "repstats": "host",
    "reputation": "host", "resetgame": "host", "resetprofileprivacy": "host",
    "resetxp": "host", "restartbot": "host", "restarthelp": "host",
    "restartstatus": "host", "savebotoutfit": "host",
    "setbotdesc": "host", "setbotmodule": "host", "setbotoutfit": "host",
    "setbotprefix": "host", "setcommandowner": "host",
    "setspawn": "host", "setspawncoords": "host", "setwelcome": "host",
    "softrestart": "host", "subhelp": "host", "subscribe": "host",
    "substatus": "host", "testnotify": "host", "testnotifyall": "host",
    "toprep": "host", "unsubscribe": "host",
    "xpleaderboard": "host",
    "botmodehelp": "host", "bots": "host",
    "commandissues": "host",
    # security — mod-only
    "moderators": "security", "profileprivacy": "security", "replog": "security",
    # banker — notify / tips / economy settings
    "casinodash": "banker", "economysettings": "banker",
    "notify": "banker", "notifyhelp": "banker", "notifyprefs": "banker",
    "notifysettings": "banker", "notifystats": "banker",
    "tipleaderboard": "banker", "tiprate": "banker", "tipstats": "banker",
    "tiphelp": "banker",
    "setdailycoins": "banker", "settransferfee": "banker",
    "sethighriskblocks": "banker", "setmaxbalance": "banker",
    "setmaxsend": "banker", "setmindailyclaims": "banker",
    "setminlevelsend": "banker", "setminsend": "banker",
    "setmintotalearned": "banker", "setnewaccountdays": "banker",
    "setsendlimit": "banker", "setsendtax": "banker",
    "settipautosub": "banker", "settipcap": "banker", "settiprate": "banker",
    "settipresubscribe": "banker", "settiptier": "banker",
    # shopkeeper — badge market / shop admin
    "addbadge": "shopkeeper", "badgeadmin": "shopkeeper", "badgecatalog": "shopkeeper",
    "badgemarketlogs": "shopkeeper", "buyitem": "shopkeeper", "cancelbuy": "shopkeeper",
    "clearbadge": "shopkeeper", "cleartitle": "shopkeeper",
    "editbadgeprice": "shopkeeper", "giveemojibadge": "shopkeeper",
    "marketbuy": "shopkeeper", "purchase": "shopkeeper",
    "removebadge": "shopkeeper", "removebadgefrom": "shopkeeper",
    "removetitle": "shopkeeper", "removevip": "shopkeeper",
    "setbadgemarketfee": "shopkeeper", "setbadgepurchasable": "shopkeeper",
    "setbadgesellable": "shopkeeper", "setbadgetradeable": "shopkeeper",
    "seteventconfirm": "shopkeeper", "setshopconfirm": "shopkeeper",
    "shopadmin": "shopkeeper", "shoptest": "shopkeeper",
    "unequip": "shopkeeper", "viphelp": "shopkeeper", "vips": "shopkeeper",
    # eventhost — autogames / quests / subs
    "fixautogames": "eventhost", "questhelp": "eventhost",
    "setautogameinterval": "eventhost", "setgamereward": "eventhost",
    "subscribers": "eventhost",
    # poker — admin / leaderboard / refund / limits
    "phelp": "poker", "pokerleaderboard": "poker", "pokerrefundall": "poker",
    "resetpokerlimits": "poker",
    "setpokerbuyin": "poker", "setpokerdailylosslimit": "poker",
    "setpokerdailywinlimit": "poker", "setpokeridlestrikes": "poker",
    "setpokerlimits": "poker", "setpokermaxstack": "poker", "setpokerplayers": "poker",
    # blackjack / rbj — all set* commands
    "setbjcountdown": "blackjack", "setbjdailylosslimit": "blackjack",
    "setbjdailywinlimit": "blackjack", "setbjmaxbet": "blackjack",
    "setbjminbet": "blackjack", "setbjturntimer": "blackjack",
    "setrbjblackjackpayout": "blackjack", "setrbjcountdown": "blackjack",
    "setrbjdailylosslimit": "blackjack", "setrbjdailywinlimit": "blackjack",
    "setrbjdecks": "blackjack", "setrbjmaxbet": "blackjack", "setrbjminbet": "blackjack",
    "setrbjshuffle": "blackjack", "setrbjturntimer": "blackjack",
    "setrbjwinpayout": "blackjack",
    # dj — emotes / social / movement
    "follow": "dj", "followme": "dj", "followstatus": "dj", "stopfollow": "dj",
    "forceemotes": "dj", "giveheart": "dj", "micstart": "dj", "micstatus": "dj",
    "publicemotes": "dj", "reactheart": "dj", "startmic": "dj",
    "setemoteloopinterval": "dj",
    # poker — all-in alias
    "all-in": "poker",
    # ── New commands added in 7-part upgrade (missing from registry) ──────────
    # host — AI delegations + bot outfit management
    "aidelegations": "host",
    "botoutfitstatus": "host",
    "copyoutfit": "host", "wearuseroutfit": "host",
    "renamebotoutfit": "host", "clearbotoutfit": "host",
    "copymyoutfit": "host", "copyoutfitfrom": "host",
    "savemyoutfit": "host", "wearoutfit": "host",
    "myoutfits": "host", "myoutfitstatus": "host",
    "directoutfittest": "host",
    # host — bot spawn management
    "setbotspawn": "host", "setbotspawnhere": "host",
    "botspawns": "host", "clearbotspawn": "host",
    "mypos": "host", "positiondebug": "host",
    # host — command registry repair alias
    "fixcommandregistry": "host",
    # dj — emote info
    "emoteinfo": "dj",
    # eventhost — events / autogames (new)
    "adminsblessing": "eventhost", "adminblessing": "eventhost",
    "eventresume": "eventhost",
    "autogamestatus": "eventhost", "autogameresume": "eventhost",
    # miner — mining admin (new)
    "mineconfig": "miner", "mineeventstatus": "miner",
    # miner — weight leaderboards (new)
    "oreweightlb": "miner", "weightlb": "miner", "heaviest": "miner",
    "myheaviest": "miner", "oreweights": "miner", "topweights": "miner",
    "setweightlbmode": "miner",
    # miner — announce settings (new)
    "mineannounce": "miner", "setmineannounce": "miner",
    "setoreannounce": "miner", "oreannounce": "miner",
    "mineannouncesettings": "miner",
    # miner — weight admin settings (new)
    "mineweights": "miner", "setmineweights": "miner",
    "setweightscale": "miner", "setrarityweightrange": "miner",
    "oreweightsettings": "miner",
    # miner — mining panel (new)
    "minepanel": "miner", "miningpanel": "miner", "mineadmin": "miner",
    # host — bulk command testing (new)
    "commandtestall": "host", "ctall": "host",
    "commandtestgroup": "host", "ctgroup": "host",
    # host — time EXP bot exclusion (new)
    "setallowbotxp": "host",
    # host — per-bot welcome messages (new)
    "botwelcome": "host", "setbotwelcome": "host",
    "resetbotwelcome": "host", "previewbotwelcome": "host",
    "botwelcomes": "host",
    # banker — gold tip commands (new)
    "goldtipsettings": "banker", "setgoldrate": "banker",
    "goldtiplogs": "banker", "mygoldtips": "banker", "goldtipstatus": "banker",
    # poker — pace / stack / deal status (new)
    "pokermode": "poker",
    "pokerpace": "poker", "setpokerpace": "poker",
    "pokerstacks": "poker", "setpokerstack": "poker",
    "dealstatus": "poker",
    # eventhost — new mining events (B-project)
    "mineevents": "eventhost", "mineboosts": "eventhost",
    "luckstatus": "eventhost",
    "miningblessing": "eventhost", "luckevent": "eventhost",
    "miningeventstart": "eventhost",
    "eventmanager": "eventhost", "eventpanel": "eventhost",
    "eventeffects": "eventhost",
    "autoeventstatus": "eventhost", "autoeventadd": "eventhost",
    "autoeventremove": "eventhost", "autoeventinterval": "eventhost",
    # miner — ore chance commands (A2)
    "orechances": "miner", "orechance": "miner",
    "setorechance": "miner", "setraritychance": "miner",
    "reloadorechances": "miner",
}

# Friendly display names for modes
_MODE_NAMES: dict[str, str] = {
    "host": "Host", "banker": "Banker", "blackjack": "Blackjack",
    "poker": "Poker", "dealer": "Dealer", "miner": "Miner",
    "shopkeeper": "Shop", "security": "Security",
    "dj": "DJ", "eventhost": "Events", "all": "Main",
}

# ---------------------------------------------------------------------------
# In-memory cache for DB ownership overrides and online status
# ---------------------------------------------------------------------------

_owner_cache: dict[str, str] = {}
_owner_cache_ts: float = 0.0
_OWNER_CACHE_TTL = 60.0

_online_cache: dict[str, bool] = {}
_online_cache_ts: float = 0.0
_ONLINE_CACHE_TTL = 30.0

# Runtime effective-mode cache (allows /setmainmode to change behaviour without restart)
_effective_mode_cache: str | None = None
_effective_mode_ts: float = 0.0
_EFFECTIVE_MODE_TTL = 30.0


def _refresh_owner_cache() -> None:
    global _owner_cache, _owner_cache_ts
    try:
        rows = db.get_all_command_owners()
        _owner_cache = {r["command"]: r["owner_bot_mode"] for r in rows}
        _owner_cache_ts = time.monotonic()
    except Exception:
        pass


def _refresh_online_cache() -> None:
    global _online_cache, _online_cache_ts
    try:
        instances = db.get_bot_instances()
        now = datetime.now(timezone.utc)
        cache: dict[str, bool] = {}
        for inst in instances:
            mode = inst.get("bot_mode", "")
            if not mode:
                continue
            if not inst.get("enabled", 1):
                if mode not in cache:
                    cache[mode] = False
                continue
            last_hb = inst.get("last_heartbeat_at", "")
            if not last_hb:
                if mode not in cache:
                    cache[mode] = False
                continue
            try:
                ls = datetime.fromisoformat(last_hb.replace("Z", "+00:00"))
                if ls.tzinfo is None:
                    ls = ls.replace(tzinfo=timezone.utc)
                age = (now - ls).total_seconds()
                is_online = age < 90
                # When multiple bot_instances rows share the same bot_mode
                # (primary + extra-mode entry from a merged duplicate-token bot),
                # prefer the most optimistic value so one fresh heartbeat wins.
                if is_online or mode not in cache:
                    cache[mode] = is_online
            except Exception:
                pass
        _online_cache = cache
        _online_cache_ts = time.monotonic()
    except Exception:
        pass


def _resolve_command_owner(cmd: str) -> str | None:
    now = time.monotonic()
    if now - _owner_cache_ts > _OWNER_CACHE_TTL:
        _refresh_owner_cache()
    if cmd in _owner_cache:
        return _owner_cache[cmd]
    return _DEFAULT_COMMAND_OWNERS.get(cmd)


def _is_mode_online(mode: str) -> bool:
    now = time.monotonic()
    if now - _online_cache_ts > _ONLINE_CACHE_TTL:
        _refresh_online_cache()
    return _online_cache.get(mode, False)


def _fallback_enabled() -> bool:
    try:
        return db.get_room_setting("multibot_fallback_enabled", "true") == "true"
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Module ownership — startup recovery & recurring task guards
# ---------------------------------------------------------------------------

# Default module → owner bot_mode mapping.
# Each module's startup recovery, room announcements, and loops must ONLY
# run on the bot whose mode matches the owner entry below.
_MODULE_OWNER_MODES: dict[str, str] = {
    "poker":        "poker",
    "blackjack":    "blackjack",
    "rbj":          "blackjack",
    "mining":       "miner",
    "bank":         "banker",
    "shop":         "shopkeeper",
    "security":     "security",
    "dj":           "dj",
    "events":       "eventhost",
    "host":         "host",
    "ai_assistant": "host",
    "timeexp":      "host",
}

# Split modes: dedicated bots that should never run another module's tasks
_SPLIT_BOT_MODES: frozenset[str] = frozenset({
    "poker", "blackjack", "miner", "banker",
    "shopkeeper", "security", "dj", "eventhost", "host",
})

# Game-module modes: host/all must never fall back to these command sets
_GAME_MODULE_MODES: frozenset[str] = frozenset({
    "miner", "poker", "blackjack", "banker",
    "dj", "shopkeeper", "eventhost", "security",
})

# Hard-owner modes: host must NEVER fall back for these, even when offline.
# These bots run on separate Highrise accounts; host staying silent is correct.
# (eventhost is excluded here because it shares a token with host — see soft
# fallback below in should_this_bot_handle.)
_HARD_OWNER_MODES: frozenset[str] = frozenset({
    "banker", "miner", "blackjack", "poker",
    "shopkeeper", "security", "dj",
})

# Audit/status commands that eventhost may cover when host is offline.
# host and eventhost share a Highrise account (multilogin alternates them),
# so exactly one is in the room at any time.
_HOST_AUDIT_CMDS: frozenset[str] = frozenset({
    # ── Bot health / audit (original set) ───────────────────────────────────
    "commandtest", "bothealth", "modulehealth", "deploymentcheck",
    "botheartbeat", "botmodules", "botstatus", "botconflicts",
    "checkcommands", "checkhelp", "routecheck", "silentcheck",
    "routerstatus", "taskowners", "activetasks", "taskconflicts",
    "fixtaskowners", "restorestatus",
    "bots", "startupstatus", "commandintegrity", "commandrepair",
    # ── Everyday host-owned help/info commands ───────────────────────────────
    # When host is multilogin-kicked by eventhost, eventhost covers these so
    # users never see silence on basic room commands.
    "help", "shophelp", "casinohelp", "gamehelp", "casino",
    "casinosettings", "casinolimits", "casinotoggles", "mycasino",
    "rules", "roleshelp",
    "coinhelp", "profilehelp", "progresshelp", "viphelp", "rephelp",
    "tiphelp", "autohelp", "questhelp", "subhelp", "notifyhelp",
    "roomhelp", "teleporthelp", "emotehelp", "alerthelp", "welcomehelp",
    "socialhelp", "botmodehelp", "multibothelp", "maintenancehelp",
    "casinoadminhelp",
    "managerhelp", "adminhelp", "ownerhelp",
    "profile", "me", "whois", "pinfo", "stats", "privacy",
    "level", "xpleaderboard",
    "players", "online", "roomlist", "owners", "managers", "moderators",
    "allstaff", "allcommands", "mycommands", "helpsearch", "start", "guide",
    "control", "status", "roomstatus",
    "rep", "reputation", "repstats", "toprep", "repleaderboard",
    "quests", "dailyquests", "weeklyquests", "claimquest", "questhelp",
    "subscribe", "unsubscribe", "substatus",
    "notifications", "clearnotifications",
    "casinointegrity", "integritylogs", "carddeliverycheck",
    # ── AI assistant (host-owned; eventhost covers when host is offline) ──────
    "ask", "ai", "assistant", "pendingaction", "confirm", "aidebug", "aicapabilities",
})

# Whitelist of eventhost-owned commands that host may handle as fallback
# when eventhost is offline.  Everything NOT in this list stays silent on
# host — action/purchase commands like /eventshop /buyevent /startevent
# are NOT included, so host never handles them even when eventhost is gone.
_HOST_SAFE_FALLBACK_CMDS: frozenset[str] = frozenset({
    # host-owned (listed for documentation clarity — they never hit this path)
    "help", "shophelp",
    "bothealth", "deploymentcheck", "modulehealth", "botheartbeat",
    "commandtest", "bots", "startupstatus", "routerstatus",
    # eventhost-owned safe help/status — host covers when eventhost offline
    "eventhelp", "goldhelp",
    "eventstatus", "eventpoints",
    "autogames", "autoevents",
    "event", "events",
})

# Safe help-only commands owned by hard-owner modes that host may cover when
# the owner bot is offline.  These are read-only info pages with no economy,
# game, or inventory side-effects.  Economy action commands (send, buy, mine,
# etc.) are NOT included here — silence is correct for those if the owner
# bot is down.
_HARD_OWNER_SAFE_HELP_CMDS: frozenset[str] = frozenset({
    "coinhelp", "bankhelp", "bankerhelp",   # banker bot offline
    "minehelp",                              # miner bot offline
})


def _has_any_game_bot_registered() -> bool:
    """Return True if any game-module bot sent a heartbeat in the last 5 min.

    Used to detect multi-bot setups so that 'all' mode doesn't fall back
    for game commands when a dedicated bot is temporarily offline.
    """
    try:
        conn = db.get_connection()
        row  = conn.execute(
            "SELECT COUNT(*) FROM bot_instances "
            "WHERE bot_mode IN "
            "('miner','poker','blackjack','banker',"
            "'dj','shopkeeper','eventhost','security') "
            "AND last_heartbeat_at >= datetime('now', '-5 minutes')",
        ).fetchone()
        conn.close()
        return (row[0] or 0) > 0
    except Exception:
        return False


def should_this_bot_run_module(module: str) -> bool:
    """
    Returns True if this bot instance should run the given module's
    startup recovery, recurring loops, and room announcements.

    Rules:
    1. BOT_MODE matches owner_mode → always run.
    2. BOT_MODE is any other split mode → never run.
    3. BOT_MODE=all → run only if the owner bot is currently offline.
    4. BOT_MODE=host → run only if owner offline and fallback enabled.
    """
    try:
        owner_mode = (
            db.get_room_setting(f"module_owner_{module}", "")
            or _MODULE_OWNER_MODES.get(module, "")
        )
    except Exception:
        owner_mode = _MODULE_OWNER_MODES.get(module, "")

    if not owner_mode:
        return BOT_MODE == "all"

    mode = _effective_bot_mode() if BOT_ID != "main" else BOT_MODE

    # Rule 1: exact match
    if mode == owner_mode:
        return True

    # Rule 1b: extra merged mode (combined-account subprocess)
    if owner_mode in BOT_EXTRA_MODES:
        return True

    # Rule 2: split bot that doesn't own this module
    if mode in _SPLIT_BOT_MODES:
        print(f"[MODULE] {module} skipped — {mode} bot never runs {module} "
              f"(owner={owner_mode}).")
        return False

    # Rule 3: all-mode — only if owner bot is currently offline
    if mode == "all":
        try:
            if _is_mode_online(owner_mode):
                print(f"[MODULE] {module} skipped on all-mode; "
                      f"{owner_mode} bot is online.")
                return False
            return True
        except Exception:
            return True

    # Rule 4: host fallback — only if owner offline and fallback enabled
    if mode == "host":
        try:
            if _is_mode_online(owner_mode):
                return False
            return _fallback_enabled()
        except Exception:
            return False

    return False


async def send_module_room_message(
        bot, module: str, message: str,
        message_key: str = "") -> bool:
    """
    Send a room restore message for a module only if this bot owns it.
    Uses a 5-minute dedupe lock to prevent duplicate room announces.
    Returns True if message was sent, False if skipped.
    """
    if not should_this_bot_run_module(module):
        print(f"[{module.upper()}] Restore msg skipped (not owner, "
              f"mode={BOT_MODE}): {message[:60]}")
        return False

    try:
        enabled = db.get_room_setting("module_restore_announce_enabled", "true")
        if enabled != "true":
            print(f"[{module.upper()}] Restore announce disabled by setting.")
            return False
    except Exception:
        pass

    key = message_key or f"{module}_restored"
    try:
        if not db.acquire_module_announce_lock(module, key, BOT_ID, ttl_seconds=300):
            print(f"[{module.upper()}] Restore lock held by another bot (dedupe).")
            return False
    except Exception:
        pass

    try:
        await bot.highrise.chat(message[:249])
        return True
    except Exception as e:
        print(f"[{module.upper()}] Restore announce error: {e}")
        return False


# ---------------------------------------------------------------------------
# /taskowners  /activetasks  /taskconflicts  /fixtaskowners
# ---------------------------------------------------------------------------

async def handle_taskowners(bot, user) -> None:
    """/taskowners — show which bot mode owns each module (including AutoGames from DB)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return
    try:
        ag_owner = db.get_room_setting("autogames_owner_bot_mode", "eventhost")
    except Exception:
        ag_owner = "eventhost"
    parts = [
        f"{m}={_MODE_NAMES.get(owner, owner)}"
        for m, owner in _MODULE_OWNER_MODES.items()
        if m != "rbj"
    ]
    parts.append(f"autogames={_MODE_NAMES.get(ag_owner, ag_owner.title())}")
    await _w(bot, user.id, ("Tasks: " + " | ".join(parts))[:249])


async def handle_activetasks(bot, user) -> None:
    """/activetasks — show which modules this bot owns."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return
    mode = _effective_bot_mode() if BOT_ID != "main" else BOT_MODE
    owned = [m for m, owner in _MODULE_OWNER_MODES.items()
             if m != "rbj" and owner == mode]
    if owned:
        await _w(bot, user.id,
                 f"Mode:{mode} | Owns: {', '.join(owned)}"[:249])
    else:
        await _w(bot, user.id,
                 f"Mode:{mode} | No module tasks (not owner of any module)."[:249])


async def handle_taskconflicts(bot, user) -> None:
    """/taskconflicts — detect duplicate module task ownership across bots."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return
    conflicts: list[str] = []
    for module, owner_mode in _MODULE_OWNER_MODES.items():
        if module == "rbj":
            continue
        runners: list[str] = []
        if _is_mode_online(owner_mode):
            runners.append(owner_mode)
        if _is_mode_online("all"):
            runners.append("all")
        if len(runners) > 1:
            conflicts.append(f"{module}:{'+'.join(runners)}")
    # AutoGames: owner from DB; "host" is a known fallback mode (not a conflict)
    try:
        ag_owner = db.get_room_setting("autogames_owner_bot_mode", "eventhost")
        if ag_owner not in ("disabled",) and _is_mode_online(ag_owner):
            ag_runners = [ag_owner]
            if _is_mode_online("all"):
                ag_runners.append("all")
            # "host" intentionally defers to owner when online — not a conflict
            if len(ag_runners) > 1:
                conflicts.append(f"autogames:{'+'.join(ag_runners)}")
    except Exception:
        pass
    if not conflicts:
        await _w(bot, user.id, "✅ No task conflicts found.")
    else:
        await _w(bot, user.id,
                 ("⚠️ Task conflicts: " + " | ".join(conflicts))[:249])


async def handle_fixtaskowners(bot, user) -> None:
    """/fixtaskowners — restore task ownership defaults (admin/owner only)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return
    db.set_room_setting("autogames_owner_bot_mode", "eventhost")
    db.set_room_setting("module_restore_announce_enabled", "true")
    await _w(bot, user.id, "✅ Task owner defaults restored.")


# ---------------------------------------------------------------------------
# /restoreannounce  /restorestatus
# ---------------------------------------------------------------------------

async def handle_restoreannounce(bot, user, args: list[str]) -> None:
    """/restoreannounce on|off — control whether module owner bots send restore messages."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        cur   = db.get_room_setting("module_restore_announce_enabled", "true")
        label = "ON" if cur == "true" else "OFF"
        await _w(bot, user.id,
                 f"Restore announce: {label} | owner-only enforced. "
                 f"Usage: /restoreannounce on|off")
        return
    new   = "true" if args[1].lower() == "on" else "false"
    label = "ON" if new == "true" else "OFF"
    db.set_room_setting("module_restore_announce_enabled", new)
    await _w(bot, user.id, f"✅ Restore announce: {label}.")


async def handle_restorestatus(bot, user) -> None:
    """/restorestatus — show current restore announce settings."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return
    try:
        restore = db.get_room_setting("module_restore_announce_enabled", "true")
        host    = db.get_room_setting("bot_startup_announce_enabled",    "false")
        mod     = db.get_room_setting("module_startup_announce_enabled", "false")
    except Exception:
        await _w(bot, user.id, "DB error reading restore settings.")
        return
    rl = "ON" if restore == "true" else "OFF"
    hl = "ON" if host    == "true" else "OFF"
    ml = "ON" if mod     == "true" else "OFF"
    await _w(bot, user.id,
             f"Restore: {rl} | owner-only | Host startup: {hl} | Mod startup: {ml}")


def _effective_bot_mode() -> str:
    """
    Returns this bot's effective operating mode.
    Normally BOT_MODE from config.  When BOT_ID == 'main', a /setmainmode
    command can store an override in room_settings that is respected here
    (cached 30 s to avoid per-message DB hits).
    """
    global _effective_mode_cache, _effective_mode_ts
    now = time.monotonic()
    if _effective_mode_cache is None or now - _effective_mode_ts > _EFFECTIVE_MODE_TTL:
        if BOT_ID == "main":
            try:
                override = db.get_room_setting("main_bot_mode_override", "")
            except Exception:
                override = ""
            _effective_mode_cache = override if override else BOT_MODE
        else:
            _effective_mode_cache = BOT_MODE
        _effective_mode_ts = now
    return _effective_mode_cache


def is_bot_mode_active(mode: str) -> bool:
    """Public helper — True if a bot with this mode heartbeated within 90 s."""
    return _is_mode_online(mode)


# ---------------------------------------------------------------------------
# Main gate — called in on_chat before any processing
# ---------------------------------------------------------------------------

def should_this_bot_handle(cmd: str) -> bool:
    """
    Returns True if this bot instance should respond to cmd.

    Key rules:
    - BOT_MODE=all: handle everything UNLESS a dedicated live bot owns the cmd.
    - BOT_MODE=host: handle help/room/unknown only; never game commands.
    - Dedicated modes (blackjack, poker, …): own their command set only.
    - Fallback: host/all may handle offline-owner commands if fallback ON.
    """
    mode = _effective_bot_mode()

    # ── all mode: defer to any dedicated online bot ──────────────────────────
    if mode == "all":
        owner_mode = _resolve_command_owner(cmd)
        if owner_mode is None:
            # Unknown / unowned command — defer to dedicated host bot if online
            return not _is_mode_online("host")
        if owner_mode not in ("all",) and _is_mode_online(owner_mode):
            return False    # dedicated bot is live — stay silent
        # Never fall back for game-module commands in multi-bot setups
        if owner_mode in _GAME_MODULE_MODES and _has_any_game_bot_registered():
            return False
        return True

    owner_mode = _resolve_command_owner(cmd)

    # Combined-mode process: this subprocess covers an extra merged mode because
    # it shares a Highrise account with another bot (deduplicated by bot.py).
    if BOT_EXTRA_MODES and owner_mode in BOT_EXTRA_MODES:
        return True

    # Hard owners — host/eventhost must NEVER respond to these, regardless of heartbeat.
    # Separate Highrise accounts; no fallback, no startup-window gap.
    # Exception: safe help-only pages from hard-owner modes may be covered by
    # host or eventhost when the owner bot is offline (no economy/game side-effects).
    # Both host and eventhost are checked because they share a Highrise account
    # and alternate via multilogin — only one is ever in the room at a time.
    if mode in ("host", "eventhost") and owner_mode in _HARD_OWNER_MODES:
        if not _is_mode_online(owner_mode) and cmd in _HARD_OWNER_SAFE_HELP_CMDS:
            return True
        return False

    # eventhost ↔ host cross-cover: they share a Highrise account and
    # alternate via multilogin.  When host is offline, eventhost answers
    # the audit/status commands so /commandtest, /bothealth etc. always work.
    if mode == "eventhost" and owner_mode == "host":
        if cmd in _HOST_AUDIT_CMDS and not _is_mode_online("host"):
            return True
        return False  # defer to host otherwise

    # Soft fallback for eventhost only (host covers when eventhost offline):
    # Restricted to a safe whitelist — action/purchase eventhost commands
    # (/eventshop, /buyevent, /startevent, …) stay silent on host.
    if mode == "host" and owner_mode == "eventhost":
        if not _is_mode_online("eventhost"):
            return cmd in _HOST_SAFE_FALLBACK_CMDS
        return False

    # Unowned / unknown command — only host or all handles it
    if owner_mode is None:
        return mode in ("host", "all")

    # This bot owns the command
    if owner_mode == mode:
        return True

    # Legacy dealer mode: handles BJ/RBJ/Poker if dedicated bots are offline
    if mode == "dealer" and owner_mode in ("blackjack", "poker"):
        return not _is_mode_online(owner_mode)

    # Owner mode is online — defer silently
    if _is_mode_online(owner_mode):
        return False

    # Owner mode offline — host/all may fall back
    if _fallback_enabled() and mode in ("host", "all"):
        return True

    return False


def get_offline_message(cmd: str) -> str | None:
    """
    Returns a user-facing message when the owning bot is offline and fallback is OFF.
    Only host/all mode should call this (others silently ignore).
    """
    mode = _effective_bot_mode()
    if mode not in ("host", "all"):
        return None
    owner_mode = _resolve_command_owner(cmd)
    if owner_mode is None:
        return None
    if owner_mode in ("host", "all"):
        return None
    if _is_mode_online(owner_mode):
        return None
    if _fallback_enabled():
        return None
    return "⚠️ That module is offline right now. Try again later."


# ---------------------------------------------------------------------------
# Heartbeat loop — updates bot_instances every 30 s
# ---------------------------------------------------------------------------

_heartbeat_task: asyncio.Task | None = None


async def start_heartbeat_loop(bot) -> None:
    global _heartbeat_task
    if _heartbeat_task and not _heartbeat_task.done():
        return

    async def _loop():
        # Wait 30 s before the first heartbeat write.
        # A bot that crashes immediately (bad token / multilogin) never writes
        # last_heartbeat_at, so other bots won't defer to it.
        await asyncio.sleep(30)
        while True:
            last_error = ""
            db_connected = 1
            try:
                mode_prefix = ""
                try:
                    from modules.bot_modes import get_current_mode_prefix
                    mode_prefix = get_current_mode_prefix()
                except Exception:
                    pass
                db.upsert_bot_instance(
                    bot_id=BOT_ID,
                    bot_username=BOT_USERNAME or BOT_ID,
                    bot_mode=BOT_MODE,
                    prefix=mode_prefix,
                    status="online",
                    db_connected=1,
                    last_error="",
                    write_heartbeat=True,
                )
                _refresh_online_cache()
                for _xmode in BOT_EXTRA_MODES:
                    db.upsert_bot_instance(
                        bot_id=f"{BOT_ID}+{_xmode}",
                        bot_username=BOT_USERNAME or BOT_ID,
                        bot_mode=_xmode,
                        prefix="",
                        status="online",
                        db_connected=1,
                        last_error="",
                        write_heartbeat=True,
                    )
            except Exception as exc:
                last_error = str(exc)[:80]
                print(f"[MULTIBOT] Heartbeat error: {exc}")
                try:
                    db.upsert_bot_instance(
                        bot_id=BOT_ID,
                        bot_username=BOT_USERNAME or BOT_ID,
                        bot_mode=BOT_MODE,
                        prefix="",
                        status="online",
                        db_connected=0,
                        last_error=last_error,
                    )
                except Exception:
                    pass
            await asyncio.sleep(30)

    _heartbeat_task = asyncio.create_task(_loop())
    print(f"[MULTIBOT] Heartbeat loop started | ID:{BOT_ID} Mode:{BOT_MODE}")


async def mark_bot_offline() -> None:
    try:
        db.upsert_bot_instance(
            bot_id=BOT_ID,
            bot_username=BOT_USERNAME or BOT_ID,
            bot_mode=BOT_MODE,
            prefix="",
            status="offline",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _w(bot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


def _mode_icon(mode: str) -> str:
    icons = {
        "host": "🎙️", "banker": "🏦", "blackjack": "🃏",
        "poker": "♠️", "dealer": "🎰", "miner": "⛏️",
        "shopkeeper": "🛒", "security": "🛡️",
        "dj": "🎧", "eventhost": "🎉", "all": "🤖",
    }
    return icons.get(mode, "🤖")


# ---------------------------------------------------------------------------
# /bots — live cluster status
# ---------------------------------------------------------------------------

async def handle_bots_live(bot, user) -> None:
    instances = db.get_bot_instances()
    if not instances:
        await _w(bot, user.id,
                 f"🤖 Bots: {_MODE_NAMES.get(BOT_MODE, BOT_MODE)} ON (single-mode)")
        return
    now = datetime.now(timezone.utc)
    parts: list[str] = []
    for inst in instances:
        mode    = inst.get("bot_mode", "?")
        uname   = inst.get("bot_username", "")
        enabled = inst.get("enabled", 1)
        if not enabled:
            parts.append(f"{_MODE_NAMES.get(mode, mode)} DISABLED")
            continue
        last_seen = inst.get("last_seen_at", "")
        if last_seen:
            try:
                ls = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
                if ls.tzinfo is None:
                    ls = ls.replace(tzinfo=timezone.utc)
                age = (now - ls).total_seconds()
                state = "ON" if age < 120 and inst.get("status") == "online" else "OFF"
            except Exception:
                state = "?"
        else:
            state = "?"
        label = _MODE_NAMES.get(mode, mode)
        uname_part = f" @{uname}" if uname else ""
        parts.append(f"{label}{uname_part} {state}")
    await _w(bot, user.id, ("🤖 Bots: " + " | ".join(parts))[:249])


# ---------------------------------------------------------------------------
# /botstatus [bot_id]
# ---------------------------------------------------------------------------

async def handle_botstatus_cluster(bot, user, args: list[str]) -> None:
    if len(args) >= 2:
        target = args[1].lower()
        instances = db.get_bot_instances()
        found = next((i for i in instances
                      if i.get("bot_id", "").lower() == target
                      or i.get("bot_mode", "").lower() == target), None)
        if not found:
            await _w(bot, user.id, f"No bot found with ID or mode '{target}'.")
            return
        mode = found.get("bot_mode", "?")
        icon = _mode_icon(mode)
        enabled = "ON" if found.get("enabled", 1) else "DISABLED"
        last_seen = found.get("last_seen_at", "")
        age_str = "never"
        if last_seen:
            try:
                ls = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
                if ls.tzinfo is None:
                    ls = ls.replace(tzinfo=timezone.utc)
                age = int((datetime.now(timezone.utc) - ls).total_seconds())
                age_str = f"{age}s ago"
            except Exception:
                age_str = "?"
        name = _MODE_NAMES.get(mode, mode.title())
        await _w(bot, user.id,
                 f"{icon} {name} Bot: {enabled} | Mode {mode} | Last seen {age_str}"[:249])
    else:
        await handle_bots_live(bot, user)


# ---------------------------------------------------------------------------
# /botmodules
# ---------------------------------------------------------------------------

async def handle_botmodules(bot, user) -> None:
    mode = _effective_bot_mode()
    if mode == "all":
        await _w(bot, user.id, "🤖 Single bot mode — main handles all modules.")
        return
    _refresh_online_cache()
    module_rows = [
        ("BJ/RBJ", "blackjack"), ("Poker", "poker"),
        ("Bank",   "banker"),    ("Mining", "miner"),
        ("Shop",   "shopkeeper"), ("Mod",  "security"),
        ("Emotes", "dj"),        ("Events", "eventhost"),
        ("Help",   "host"),
    ]
    parts = []
    for label, m in module_rows:
        name = _MODE_NAMES.get(m, m.title())
        flag = "✅" if _is_mode_online(m) else "·"
        parts.append(f"{label}={name}{flag}")
    await _w(bot, user.id, ("Modules: " + " | ".join(parts))[:249])


# ---------------------------------------------------------------------------
# /commandowners
# ---------------------------------------------------------------------------

async def handle_commandowners(bot, user) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    rows = db.get_all_command_owners()
    if not rows:
        await _w(bot, user.id,
                 "No DB overrides. Defaults: /bj→Blackjack | /p→Poker | /bal→Banker")
        return
    parts = [f"/{r['command']}→{r['owner_bot_mode']}" for r in rows[:15]]
    await _w(bot, user.id, ("Owners: " + " | ".join(parts))[:249])


# ---------------------------------------------------------------------------
# /enablebot  /disablebot  /setbotmodule  /setcommandowner  /botfallback
# ---------------------------------------------------------------------------

async def handle_enablebot(bot, user, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /enablebot <bot_id>")
        return
    bid = args[1].lower()
    db.enable_bot_instance(bid, True)
    await _w(bot, user.id, f"✅ Bot '{bid}' enabled.")


async def handle_disablebot(bot, user, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /disablebot <bot_id>")
        return
    bid = args[1].lower()
    db.enable_bot_instance(bid, False)
    _refresh_online_cache()
    await _w(bot, user.id, f"✅ Bot '{bid}' disabled.")


async def handle_setbotmodule(bot, user, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /setbotmodule <bot_id> <mode>")
        return
    bid, mode = args[1].lower(), args[2].lower()
    db.set_bot_instance_module(bid, mode)
    await _w(bot, user.id, f"✅ Bot '{bid}' module set to '{mode}'.")


async def handle_setcommandowner(bot, user, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /setcommandowner <cmd> <bot_mode>")
        return
    cmd_name = args[1].lstrip("/").lower()
    bot_mode = args[2].lower()
    db.set_command_owner_db(cmd_name, "", bot_mode, fallback_allowed=1)
    _refresh_owner_cache()
    name = _MODE_NAMES.get(bot_mode, bot_mode.title())
    await _w(bot, user.id, f"✅ /{cmd_name} owner set to {name}.")


async def handle_setmainmode(bot, user, args: list[str]) -> None:
    """
    /setmainmode host|all
    Changes the effective operating mode of the main/all bot at runtime.
    Stored in room_settings so it survives the cache but NOT a full restart
    (bot.py re-applies env-var defaults on startup).
    Admin+ only.
    """
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2 or args[1].lower() not in ("host", "all"):
        await _w(bot, user.id, "Usage: /setmainmode host | all")
        return
    global _effective_mode_cache, _effective_mode_ts
    new_mode = args[1].lower()
    try:
        db.set_room_setting("main_bot_mode_override", new_mode)
    except Exception as e:
        await _w(bot, user.id, f"DB error: {str(e)[:40]}")
        return
    # Immediately apply in-process
    _effective_mode_cache = new_mode
    _effective_mode_ts = time.monotonic()
    await _w(bot, user.id, f"✅ Main bot mode set to {new_mode}.")


async def handle_botfallback(bot, user, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        cur = db.get_room_setting("multibot_fallback_enabled", "true")
        await _w(bot, user.id,
                 f"Fallback: {'ON' if cur == 'true' else 'OFF'}. Usage: /botfallback on|off")
        return
    new = "true" if args[1].lower() == "on" else "false"
    db.set_room_setting("multibot_fallback_enabled", new)
    label = "ON ✅" if new == "true" else "OFF ⛔"
    await _w(bot, user.id, f"✅ Bot command fallback {label}.")


# ---------------------------------------------------------------------------
# /botstartupannounce
# ---------------------------------------------------------------------------

async def handle_botstartupannounce(bot, user, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        cur = db.get_room_setting("bot_startup_announce_enabled", "false")
        await _w(bot, user.id,
                 f"Startup announce: {'ON' if cur == 'true' else 'OFF'}."
                 " Usage: /botstartupannounce on|off")
        return
    new = "true" if args[1].lower() == "on" else "false"
    db.set_room_setting("bot_startup_announce_enabled", new)
    label = "ON" if new == "true" else "OFF"
    await _w(bot, user.id, f"✅ Bot startup announce {label}.")


def should_announce_startup() -> bool:
    """Legacy helper kept for backward compat."""
    try:
        return db.get_room_setting("bot_startup_announce_enabled", "false") == "true"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Mode-specific startup messages
# ---------------------------------------------------------------------------

_MODULE_STARTUP_MSGS: dict[str, str] = {
    "blackjack":  "🃏 Blackjack Bot online.",
    "poker":      "♠️ Poker Bot online.",
    "miner":      "⛏️ Miner Bot online.",
    "banker":     "🏦 Banker Bot online.",
    "shopkeeper": "🛒 Shop Bot online.",
    "security":   "🛡️ Security Bot online.",
    "dj":         "🎧 DJ Bot online.",
    "eventhost":  "🎉 Event Bot online.",
}

_STARTUP_COOLDOWN_SECONDS = 600  # 10 minutes


async def send_startup_announce(bot) -> None:
    """
    Centralised startup-announce logic.  Called once per bot startup.

    Rules:
    - Console log always printed.
    - host/all mode: sends room message only if bot_startup_announce_enabled=true
      AND the 10-minute cooldown has not elapsed.
    - Module bots: sends room message only if module_startup_announce_enabled=true
      AND the per-bot 10-minute cooldown has not elapsed.
    - Old generic "Mini Game Bot is online!" is NEVER sent.
    """
    mode = _effective_bot_mode()
    uname = BOT_USERNAME or BOT_ID
    print(f"[BOT] {uname} online | id={BOT_ID} | mode={mode}")

    now = datetime.now(timezone.utc)

    def _cooldown_ok(key: str) -> bool:
        try:
            last = db.get_room_setting(key, "")
            if not last:
                return True
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            return (now - last_dt).total_seconds() >= _STARTUP_COOLDOWN_SECONDS
        except Exception:
            return True

    is_host = mode in ("host", "all")

    if is_host:
        try:
            host_enabled = db.get_room_setting("bot_startup_announce_enabled", "false") == "true"
        except Exception:
            host_enabled = False
        if host_enabled and _cooldown_ok("last_host_startup_announce_at"):
            try:
                await bot.highrise.chat("🎙️ Host online. Type /help or /tutorial.")
                db.set_room_setting("last_host_startup_announce_at", now.isoformat())
            except Exception as exc:
                print(f"[BOT] Startup announce error: {exc}")
    else:
        try:
            mod_enabled = db.get_room_setting("module_startup_announce_enabled", "false") == "true"
        except Exception:
            mod_enabled = False
        if mod_enabled:
            msg = _MODULE_STARTUP_MSGS.get(mode)
            cooldown_key = f"last_module_startup_announce_at_{BOT_ID}"
            if msg and _cooldown_ok(cooldown_key):
                try:
                    await bot.highrise.chat(msg)
                    db.set_room_setting(cooldown_key, now.isoformat())
                except Exception as exc:
                    print(f"[BOT] Module startup announce error: {exc}")


# ---------------------------------------------------------------------------
# /startupannounce  /modulestartup  /startupstatus
# ---------------------------------------------------------------------------

async def handle_startupannounce(bot, user, args: list[str]) -> None:
    """
    /startupannounce on|off
    Enables/disables the Host Bot room message on startup.
    """
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        cur = db.get_room_setting("bot_startup_announce_enabled", "false")
        label = "ON" if cur == "true" else "OFF"
        await _w(bot, user.id, f"Host startup announce: {label}. Usage: /startupannounce on|off")
        return
    new = "true" if args[1].lower() == "on" else "false"
    db.set_room_setting("bot_startup_announce_enabled", new)
    if new == "true":
        await _w(bot, user.id, "✅ Host startup announce ON.")
    else:
        await _w(bot, user.id, "⛔ Host startup announce OFF.")


async def handle_modulestartup(bot, user, args: list[str]) -> None:
    """
    /modulestartup on|off
    Enables/disables short role-specific startup messages for non-host bots.
    """
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin and owner only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        cur = db.get_room_setting("module_startup_announce_enabled", "false")
        label = "ON" if cur == "true" else "OFF"
        await _w(bot, user.id, f"Module startup announce: {label}. Usage: /modulestartup on|off")
        return
    new = "true" if args[1].lower() == "on" else "false"
    db.set_room_setting("module_startup_announce_enabled", new)
    if new == "true":
        await _w(bot, user.id, "✅ Module startup announce ON.")
    else:
        await _w(bot, user.id, "⛔ Module startup announce OFF.")


async def handle_startupstatus(bot, user) -> None:
    """
    /startupstatus
    Shows live bot presence — same data source as /bots and /bothealth.
    Host is ON when host or eventhost (they share an account) has heartbeated
    recently.  Modules are ON when any dedicated module bot is alive.
    """
    # Host is the host/eventhost pair (multilogin — one is always the active one).
    host_on = _is_mode_online("host") or _is_mode_online("eventhost")
    host_lbl = "ON" if host_on else "OFF"

    # Modules = any dedicated module bot alive in the last 90 s.
    module_modes = ("banker", "shopkeeper", "miner", "blackjack", "poker",
                    "security", "dj", "eventhost")
    modules_on = any(_is_mode_online(m) for m in module_modes)
    mod_lbl = "ON" if modules_on else "OFF"

    # Conflicts — bot IDs seen more than once in the last 90 s heartbeat window.
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT bot_id FROM bot_instances"
            "  WHERE last_heartbeat_at >= datetime('now', '-90 seconds')"
            "  GROUP BY bot_id HAVING COUNT(*) > 1"
            ")"
        ).fetchone()
        conn.close()
        conflicts = row[0] if row else 0
    except Exception:
        conflicts = 0

    await _w(bot, user.id,
             f"Startup: Host {host_lbl} | Modules {mod_lbl} | Conflicts {conflicts}")


# Keep old handler as an alias so any existing /botstartupannounce calls still work
async def handle_botstartupannounce(bot, user, args: list[str]) -> None:
    await handle_startupannounce(bot, user, args)


# ---------------------------------------------------------------------------
# /multibothelp
# ---------------------------------------------------------------------------

_MULTIBOT_HELP_PAGES = [
    (
        "🤖 Multi-Bot\n"
        "/bots - bot list\n"
        "/bothealth - health\n"
        "/modulehealth - modules\n"
        "/deploymentcheck - check setup\n"
        "/botstatus id - bot details"
    ),
    (
        "🔍 Diagnostics\n"
        "/botlocks - active locks\n"
        "/botconflicts - find conflicts\n"
        "/botheartbeat - this bot status\n"
        "/moduleowners - cmd owners\n"
        "/commandowners - DB overrides"
    ),
    (
        "👑 Owner Controls\n"
        "/setcommandowner cmd mode\n"
        "/enablebot id | /disablebot id\n"
        "/setbotmodule id mode\n"
        "/botfallback on|off\n"
        "/fixbotowners [force]\n"
        "/clearstalebotlocks"
    ),
]


async def handle_multibothelp(bot, user, args: list[str]) -> None:
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
    n = len(_MULTIBOT_HELP_PAGES)
    if page == 0:
        await _w(bot, user.id, _MULTIBOT_HELP_PAGES[0])
        if can_manage_economy(user.username):
            await _w(bot, user.id, _MULTIBOT_HELP_PAGES[1])
    elif 1 <= page <= n:
        if page == 2 and not can_manage_economy(user.username):
            await _w(bot, user.id, "Admin and owner only.")
        else:
            await _w(bot, user.id, _MULTIBOT_HELP_PAGES[page - 1])
    else:
        await _w(bot, user.id, f"Pages 1-{n}.")


# ---------------------------------------------------------------------------
# get_command_owner_for_audit  (used by cmd_audit.py)
# ---------------------------------------------------------------------------

def check_startup_safety() -> list[str]:
    """
    Run safety checks at bot startup. Returns a list of warning strings.
    Prints each warning to console; caller may also announce in-room.
    """
    warnings: list[str] = []
    try:
        instances = db.get_bot_instances()
        now_ts = __import__("time").time()

        # 1. Duplicate BOT_ID active recently
        for inst in instances:
            if inst.get("bot_id") != BOT_ID:
                continue
            last_seen = inst.get("last_seen_at", "")
            if not last_seen:
                continue
            from datetime import datetime as _dt, timezone as _tz
            try:
                ls = _dt.fromisoformat(last_seen.replace("Z", "+00:00"))
                if ls.tzinfo is None:
                    ls = ls.replace(tzinfo=_tz.utc)
                age = (_dt.now(_tz.utc) - ls).total_seconds()
                if age < 60 and inst.get("status") == "online":
                    w = f"[MULTIBOT] WARN: Another bot with ID '{BOT_ID}' was active {int(age)}s ago."
                    print(w)
                    warnings.append(w)
            except Exception:
                pass

        # 2. BOT_MODE=all with dedicated split bots active
        if BOT_MODE == "all":
            split_modes = ("blackjack", "poker", "miner", "banker",
                           "shopkeeper", "security", "dj", "eventhost")
            active_splits = []
            for inst in instances:
                m = inst.get("bot_mode", "")
                if m not in split_modes:
                    continue
                if not inst.get("enabled", 1):
                    continue
                last_seen = inst.get("last_seen_at", "")
                if not last_seen:
                    continue
                from datetime import datetime as _dt2, timezone as _tz2
                try:
                    ls = _dt2.fromisoformat(last_seen.replace("Z", "+00:00"))
                    if ls.tzinfo is None:
                        ls = ls.replace(tzinfo=_tz2.utc)
                    if (_dt2.now(_tz2.utc) - ls).total_seconds() < 90:
                        active_splits.append(m)
                except Exception:
                    pass
            if active_splits:
                w = (f"[MULTIBOT] WARN: BOT_MODE=all active alongside split bots: "
                     f"{', '.join(active_splits)}. Duplicate replies possible.")
                print(w)
                warnings.append(w)
    except Exception as e:
        print(f"[MULTIBOT] Startup safety check error: {e}")
    return warnings


def get_command_owner_for_audit(cmd: str) -> str:
    owner = _resolve_command_owner(cmd)
    if not owner:
        return "all"
    return _MODE_NAMES.get(owner, owner.title())


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

__all__ = [
    "BOT_ID", "BOT_MODE", "BOT_USERNAME",
    "should_this_bot_handle", "get_offline_message", "is_bot_mode_active",
    "start_heartbeat_loop", "mark_bot_offline",
    "should_announce_startup", "send_startup_announce",
    "should_this_bot_run_module", "send_module_room_message",
    "_MODULE_OWNER_MODES",
    "handle_bots_live", "handle_botstatus_cluster",
    "handle_botmodules", "handle_commandowners",
    "handle_enablebot", "handle_disablebot",
    "handle_setbotmodule", "handle_setcommandowner", "handle_botfallback",
    "handle_botstartupannounce",                   # backward-compat alias
    "handle_startupannounce", "handle_modulestartup", "handle_startupstatus",
    "handle_setmainmode", "handle_multibothelp",
    "handle_taskowners", "handle_activetasks",
    "handle_taskconflicts", "handle_fixtaskowners",
    "handle_restoreannounce", "handle_restorestatus",
    "get_command_owner_for_audit",
]
