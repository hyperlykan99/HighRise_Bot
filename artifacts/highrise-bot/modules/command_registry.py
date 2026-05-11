"""
modules/command_registry.py
---------------------------
Central command registry — single source of truth for ownership, routing
flags, and fallback policy.

Pure data module: no handler functions are imported here.
Used by /commandtest, /commandintegrity, /checkcommands, /commandrepair.

Fields
------
owner    — bot mode that owns this command
cat      — category label
fallback — True → host/eventhost may cover when owner is offline
safe     — True → read-only / help page (no economy or game state change)
write    — True → modifies economy / game / inventory state
perm     — minimum caller permission: player | staff | manager | admin | owner
aliases  — alternative command spellings that map to this primary
"""
from __future__ import annotations
from typing import NamedTuple


class Cmd(NamedTuple):
    owner:    str
    cat:      str
    fallback: bool
    safe:     bool
    write:    bool
    perm:     str             = "player"
    aliases:  tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Registry  (primary command → Cmd)
# ---------------------------------------------------------------------------
REGISTRY: dict[str, Cmd] = {

    # ── Host: help pages ─────────────────────────────────────────────────────
    "help":             Cmd("host","help",    True, True, False, aliases=("answer",)),
    "shophelp":         Cmd("host","help",    True, True, False),
    "casinohelp":       Cmd("host","help",    True, True, False, aliases=("gamehelp",)),
    "coinhelp":         Cmd("banker","help",   True, True, False),
    "bankhelp":         Cmd("banker","help",   True, True, False),
    "bankerhelp":       Cmd("banker","help",   True, True, False),
    "economydbcheck":   Cmd("banker","admin",  False,True, False, perm="admin"),
    "economyrepair":    Cmd("banker","admin",  False,False,False, perm="owner"),
    "profilehelp":      Cmd("host","help",    True, True, False),
    "progresshelp":     Cmd("host","help",    True, True, False),
    "viphelp":          Cmd("host","help",    True, True, False),
    "rephelp":          Cmd("host","help",    True, True, False),
    "tiphelp":          Cmd("host","help",    True, True, False),
    "autohelp":         Cmd("host","help",    True, True, False),
    "roleshelp":        Cmd("host","help",    True, True, False),
    "questhelp":        Cmd("host","help",    True, True, False),
    "subhelp":          Cmd("host","help",    True, True, False),
    "notifyhelp":       Cmd("host","help",    True, True, False),
    "roomhelp":         Cmd("host","help",    True, True, False),
    "teleporthelp":     Cmd("host","help",    True, True, False),
    "emotehelp":        Cmd("host","help",    True, True, False),
    "alerthelp":        Cmd("host","help",    True, True, False),
    "welcomehelp":      Cmd("host","help",    True, True, False),
    "socialhelp":       Cmd("host","help",    True, True, False),
    "botmodehelp":      Cmd("host","help",    True, True, False),
    "multibothelp":     Cmd("host","help",    True, True, False, perm="staff"),
    "maintenancehelp":  Cmd("host","help",    True, True, False, perm="manager"),
    "casinoadminhelp":  Cmd("host","help",    True, True, False, perm="manager"),
    "bankadminhelp":    Cmd("banker","help",  False,True, False, perm="manager"),
    "audithelp":        Cmd("security","help",False,True, False, perm="staff"),
    "reporthelp":       Cmd("security","help",False,True, False),
    "staffhelp":        Cmd("security","help",False,True, False, perm="staff"),
    "modhelp":          Cmd("security","help",False,True, False, perm="staff"),
    "managerhelp":      Cmd("host","help",    True, True, False, perm="manager"),
    "adminhelp":        Cmd("host","help",    True, True, False, perm="admin"),
    "ownerhelp":        Cmd("host","help",    True, True, False, perm="owner"),
    "minehelp":         Cmd("miner","help",   False,True, False),
    "bjhelp":           Cmd("blackjack","help",False,True,False),
    "rbjhelp":          Cmd("blackjack","help",False,True,False),
    "pokerhelp":        Cmd("poker","help",   False,True, False, aliases=("phelp","ph")),
    "eventhelp":        Cmd("eventhost","help",True, True,False),
    "goldhelp":         Cmd("banker","goldrain",False,True, False),

    # ── Host: info / room status ─────────────────────────────────────────────
    "rules":            Cmd("host","info",    True, True, False),
    "howtoplay":        Cmd("host","info",    False,True, False),
    "gameguide":        Cmd("host","info",    False,True, False),
    "games":            Cmd("host","info",    False,True, False),
    # ── Display format settings ───────────────────────────────────────────────
    "displaybadges":       Cmd("host","admin",   True, True, False, perm="manager"),
    "displaytitles":       Cmd("host","admin",   True, True, False, perm="manager"),
    "displayformat":       Cmd("host","admin",   True, True, False, perm="manager"),
    "displaytest":         Cmd("host","admin",   True, True, False, perm="manager"),
    # ── Bot message format (BotName:\nBody prefix) ────────────────────────────
    "botmessageformat":    Cmd("host","admin",   False,True, False, perm="manager"),
    "setbotmessageformat": Cmd("host","admin",   False,False,True,  perm="manager"),
    # ── Message length testing tools ─────────────────────────────────────────
    "msgtest":             Cmd("host","admin",   False,False,True,  perm="manager"),
    "msgboxtest":          Cmd("host","admin",   False,False,True,  perm="manager"),
    "msgsplitpreview":     Cmd("host","admin",   False,False,True,  perm="manager"),
    # ── Time-in-Room EXP admin commands ──────────────────────────────────────
    "settimeexp":       Cmd("host","admin",   True, True, False, perm="manager"),
    "settimeexpcap":    Cmd("host","admin",   True, True, False, perm="manager"),
    "settimeexptick":   Cmd("host","admin",   True, True, False, perm="admin"),
    "settimeexpbonus":  Cmd("host","admin",   True, True, False, perm="manager"),
    "timeexpstatus":    Cmd("host","admin",   True, True, False, perm="manager"),
    "casino":           Cmd("host","info",    True, True, False),
    "casinosettings":   Cmd("host","admin",   True, True, False, perm="manager"),
    "casinolimits":     Cmd("host","admin",   True, True, False, perm="manager"),
    "casinotoggles":    Cmd("host","admin",   True, False,False, perm="manager"),
    "mycasino":         Cmd("host","info",    True, True, False, aliases=("casinodash",)),
    "profile":          Cmd("host","profile", True, True, False, aliases=("me","whois","pinfo")),
    "privacy":          Cmd("host","profile", True, False,True),
    "stats":            Cmd("host","profile", True, True, False),
    "level":            Cmd("host","profile", True, True, False),
    "xpleaderboard":    Cmd("host","profile", True, True, False),
    "rep":              Cmd("host","profile", True, True, False,
                            aliases=("reputation","repstats","toprep","repleaderboard")),
    "players":          Cmd("host","info",    True, True, False, aliases=("online","roomlist")),
    "owners":           Cmd("host","info",    True, True, False),
    "managers":         Cmd("host","info",    True, True, False),
    "moderators":       Cmd("host","info",    True, True, False),
    "allstaff":         Cmd("host","info",    True, True, False, perm="staff"),
    "allcommands":      Cmd("host","info",    True, True, False),
    "mycommands":       Cmd("host","info",    True, True, False),
    "helpsearch":       Cmd("host","info",    True, True, False),
    "control":          Cmd("host","info",    True, True, False),
    "status":           Cmd("host","info",    True, True, False),
    "roomstatus":       Cmd("host","info",    True, True, False),
    "notifications":    Cmd("host","profile", True, True, False,
                            aliases=("clearnotifications",)),
    "subscribe":        Cmd("host","profile", True, False,True,
                            aliases=("unsubscribe","substatus")),
    "quests":           Cmd("host","profile", True, True, False,
                            aliases=("dailyquests","weeklyquests","claimquest")),

    # ── Host: bot health / audit ─────────────────────────────────────────────
    "bots":             Cmd("host","status",  True, True, False),
    "bothealth":        Cmd("host","status",  True, True, False, perm="manager"),
    "modulehealth":     Cmd("host","status",  True, True, False, perm="manager"),
    "deploymentcheck":  Cmd("host","status",  True, True, False, perm="manager"),
    "botheartbeat":     Cmd("host","status",  True, True, False, perm="manager"),
    "botstatus":        Cmd("host","status",  True, True, False, perm="staff"),
    "botmodules":       Cmd("host","status",  True, True, False, perm="staff"),
    "botconflicts":     Cmd("host","status",  True, True, False, perm="staff"),
    "commandtest":      Cmd("host","audit",   True, True, False, perm="staff"),
    "checkcommands":    Cmd("host","audit",   True, True, False, perm="staff"),
    "checkhelp":        Cmd("host","audit",   True, True, False, perm="staff"),
    "missingcommands":  Cmd("host","audit",   True, True, False, perm="staff"),
    "routecheck":       Cmd("host","audit",   True, True, False, perm="staff"),
    "silentcheck":      Cmd("host","audit",   True, True, False, perm="staff"),
    "testcommands":     Cmd("host","audit",   True, True, False, perm="admin"),
    "commandintegrity": Cmd("host","audit",   True, True, False, perm="admin"),
    "commandrepair":    Cmd("host","audit",   True, False,False, perm="owner"),
    "fixcommands":      Cmd("host","audit",   True, False,False, perm="admin"),
    "routerstatus":     Cmd("host","status",  True, True, False, perm="manager"),
    "startupstatus":    Cmd("host","status",  True, True, False, perm="staff"),
    "taskowners":       Cmd("host","status",  True, True, False, perm="admin"),
    "activetasks":      Cmd("host","status",  True, True, False, perm="admin"),
    "taskconflicts":    Cmd("host","status",  True, True, False, perm="admin"),
    "fixtaskowners":    Cmd("host","admin",   True, False,False, perm="admin"),
    "restorestatus":    Cmd("host","status",  True, True, False, perm="admin"),
    "restoreannounce":  Cmd("host","admin",   True, False,False, perm="admin"),
    "dblockcheck":      Cmd("host","status",  True, True, False, perm="admin"),
    "casinointegrity":  Cmd("host","audit",   True, True, False, perm="admin"),

    # ── Banker ───────────────────────────────────────────────────────────────
    "bal":              Cmd("banker","economy",False,True, False,
                            aliases=("balance","b","wallet","w","coins","coin","money")),
    "send":             Cmd("banker","economy",False,False,True,  aliases=("tip","gift")),
    "daily":            Cmd("banker","economy",False,False,True),
    "bank":             Cmd("banker","economy",False,True, False),
    "transactions":     Cmd("banker","economy",False,True, False, aliases=("bankstats",)),
    "leaderboard":      Cmd("banker","economy",False,True, False, aliases=("lb",)),
    "dashboard":        Cmd("banker","economy",False,True, False, aliases=("dash",)),
    "tiprate":          Cmd("banker","economy",False,True, False,
                            aliases=("tipstats","tipleaderboard")),

    # ── Blackjack ────────────────────────────────────────────────────────────
    "bj":               Cmd("blackjack","casino",False,False,True, aliases=("bjoin",)),
    "bh":               Cmd("blackjack","casino",False,False,True),
    "bs":               Cmd("blackjack","casino",False,False,True),
    "bd":               Cmd("blackjack","casino",False,False,True),
    "bsp":              Cmd("blackjack","casino",False,False,True),
    "bhand":            Cmd("blackjack","casino",False,True, False),
    "blimits":          Cmd("blackjack","casino",False,True, False),
    "bstats":           Cmd("blackjack","casino",False,True, False),
    "rbj":              Cmd("blackjack","casino",False,False,True, aliases=("rjoin",)),
    "rh":               Cmd("blackjack","casino",False,False,True),
    "rs":               Cmd("blackjack","casino",False,False,True),
    "rd":               Cmd("blackjack","casino",False,False,True),
    "rsp":              Cmd("blackjack","casino",False,False,True),
    "rhand":            Cmd("blackjack","casino",False,True, False),
    "rshoe":            Cmd("blackjack","casino",False,True, False),
    "rlimits":          Cmd("blackjack","casino",False,True, False),
    "rstats":           Cmd("blackjack","casino",False,True, False),
    # ── Easy BJ / universal shortcuts (all map to Realistic Blackjack) ───────
    "blackjack":        Cmd("blackjack","casino",False,False,True),
    "bjbet":            Cmd("blackjack","casino",False,False,True),
    "bet":              Cmd("blackjack","casino",False,False,True),
    "hit":              Cmd("blackjack","casino",False,False,True),
    "stand":            Cmd("blackjack","casino",False,False,True),
    "stay":             Cmd("blackjack","casino",False,False,True),
    "double":           Cmd("blackjack","casino",False,False,True),
    "split":            Cmd("blackjack","casino",False,False,True),
    "insurance":        Cmd("blackjack","casino",False,False,True),
    "bi":               Cmd("blackjack","casino",False,False,True),
    "surrender":        Cmd("blackjack","casino",False,False,True),
    "shoe":             Cmd("blackjack","casino",False,True, False),
    "bjshoe":           Cmd("blackjack","casino",False,True, False),

    # ── Poker ────────────────────────────────────────────────────────────────
    "poker":            Cmd("poker","casino",False,False,True, aliases=("p","pj","pt","ptable")),
    "pokerstats":       Cmd("poker","casino",False,True, False, aliases=("pstats",)),
    "pokerlb":          Cmd("poker","casino",False,True, False,
                            aliases=("plb","pleaderboard","pokerleaderboard")),
    "check":            Cmd("poker","casino",False,False,True, aliases=("ch",)),
    "call":             Cmd("poker","casino",False,False,True, aliases=("ca",)),
    "raise":            Cmd("poker","casino",False,False,True, aliases=("r",)),
    "fold":             Cmd("poker","casino",False,False,True, aliases=("f",)),
    "allin":            Cmd("poker","casino",False,False,True,
                            aliases=("shove","all-in")),
    "sitout":           Cmd("poker","casino",False,False,False),
    "sitin":            Cmd("poker","casino",False,False,False),
    "rebuy":            Cmd("poker","casino",False,False,True),
    "mystack":          Cmd("poker","casino",False,True, False, aliases=("pstacks","stack")),
    "pcards":           Cmd("poker","casino",False,True, False, aliases=("po","podds")),
    "pplayers":         Cmd("poker","casino",False,True, False, aliases=("pp",)),

    # ── Miner ────────────────────────────────────────────────────────────────
    "mine":             Cmd("miner","mining",False,False,True, aliases=("m","dig")),
    "ores":             Cmd("miner","mining",False,True, False, aliases=("mineinv",)),
    "orelist":          Cmd("miner","mining",False,True, False),
    "tool":             Cmd("miner","mining",False,True, False, aliases=("pickaxe",)),
    "upgradetool":      Cmd("miner","mining",False,False,True, aliases=("upick",)),
    "sellores":         Cmd("miner","mining",False,False,True, aliases=("sellore",)),
    "mineprofile":      Cmd("miner","mining",False,True, False, aliases=("mp","minerank")),
    "minelb":           Cmd("miner","mining",False,True, False),
    "mineshop":         Cmd("miner","mining",False,True, False),
    "minebuy":          Cmd("miner","mining",False,False,True),
    "minedaily":        Cmd("miner","mining",False,False,True),
    "orebook":          Cmd("miner","mining",False,True, False,
                            aliases=("oremastery","orestats")),
    "contracts":        Cmd("miner","mining",False,True, False,
                            aliases=("miningjobs",)),
    "job":              Cmd("miner","mining",False,True, False,
                            aliases=("deliver","claimjob","rerolljob")),
    "craft":            Cmd("miner","mining",False,False,True),

    # ── Shopkeeper ───────────────────────────────────────────────────────────
    "shop":             Cmd("shopkeeper","shop",False,True, False),
    "buy":              Cmd("shopkeeper","shop",False,False,True),
    "equip":            Cmd("shopkeeper","shop",False,False,True),
    "myitems":          Cmd("shopkeeper","shop",False,True, False),
    "badges":           Cmd("shopkeeper","shop",False,True, False,
                            aliases=("mybadges","badgecatalog")),
    "badgeinfo":        Cmd("shopkeeper","shop",False,True, False),
    "badgelist":        Cmd("shopkeeper","shop",False,True, False),
    "badgemarket":      Cmd("shopkeeper","shop",False,True, False),
    "badgebuy":         Cmd("shopkeeper","shop",False,False,True),
    "badgeprices":      Cmd("shopkeeper","shop",False,True, False),
    "titles":           Cmd("shopkeeper","shop",False,True, False, aliases=("titleinfo",)),
    "vipshop":          Cmd("shopkeeper","shop",False,True, False, aliases=("buyvip",)),
    "vipstatus":        Cmd("shopkeeper","shop",False,True, False),

    # ── EventHost ────────────────────────────────────────────────────────────
    "event":            Cmd("eventhost","events",False,True, False, aliases=("events",)),
    "eventstatus":      Cmd("eventhost","events",True, True, False),
    "eventpoints":      Cmd("eventhost","events",True, True, False),
    "eventshop":        Cmd("eventhost","events",False,True, False),
    "buyevent":         Cmd("eventhost","events",False,False,True),
    "startevent":       Cmd("eventhost","events",False,False,True, perm="manager"),
    "stopevent":        Cmd("eventhost","events",False,False,True, perm="manager"),
    "stopae":           Cmd("eventhost","events",False,False,True, perm="manager"),
    "stopautoevent":    Cmd("eventhost","events",False,False,True, perm="manager"),
    "endevent":         Cmd("eventhost","events",False,False,True, perm="manager"),
    "endcurrentevent":  Cmd("eventhost","events",False,False,True, perm="manager"),
    "aeskip":           Cmd("eventhost","events",False,False,True, perm="manager"),
    "skipaevent":       Cmd("eventhost","events",False,False,True, perm="manager"),
    "skipae":           Cmd("eventhost","events",False,False,True, perm="manager"),
    "aeskipnext":       Cmd("eventhost","events",False,False,True, perm="manager"),
    "skipnextae":       Cmd("eventhost","events",False,False,True, perm="manager"),
    "autogames":        Cmd("eventhost","events",True, True, False),
    "autoevents":       Cmd("eventhost","events",True, True, False),
    "autogamesowner":   Cmd("eventhost","events",False,True, False, perm="manager"),
    "gameconfig":       Cmd("eventhost","events",False,True, False, perm="manager"),
    "stopautogames":    Cmd("eventhost","events",False,False,True, perm="manager",
                            aliases=("killautogames",)),
    "fixautogames":     Cmd("eventhost","events",False,False,True, perm="manager"),
    "announce":         Cmd("eventhost","events",False,False,True, perm="manager"),
    "gamehint":         Cmd("eventhost","events",False,False,True, perm="manager",
                            aliases=("autogamehint",)),
    "revealanswer":     Cmd("eventhost","events",False,False,True, perm="manager",
                            aliases=("revealgameanswer","autogamereveal")),

    # ── DJ ───────────────────────────────────────────────────────────────────
    "emote":            Cmd("dj","social",False,False,True, aliases=("emotes",)),
    "stopemote":        Cmd("dj","social",False,False,True,
                            aliases=("stoploop","stopallloops")),
    "dance":            Cmd("dj","social",False,False,True),
    "wave":             Cmd("dj","social",False,False,True),
    "sit":              Cmd("dj","social",False,False,True),
    "clap":             Cmd("dj","social",False,False,True),
    "loopemote":        Cmd("dj","social",False,False,True),
    "hug":              Cmd("dj","social",False,False,True),
    "kiss":             Cmd("dj","social",False,False,True),
    "slap":             Cmd("dj","social",False,False,True),
    "punch":            Cmd("dj","social",False,False,True),
    "highfive":         Cmd("dj","social",False,False,True,
                            aliases=("boop","cheer","waveat")),
    "heart":            Cmd("dj","social",False,False,True,
                            aliases=("hearts","heartlb")),
    "social":           Cmd("dj","social",False,True, False),

    # ── Security ─────────────────────────────────────────────────────────────
    "report":           Cmd("security","moderation",False,False,True),
    "reports":          Cmd("security","moderation",False,True, False,
                            aliases=("myreports","reportinfo")),
    "bug":              Cmd("security","moderation",False,False,True),
    "warn":             Cmd("security","moderation",False,False,True, perm="staff"),
    "warnings":         Cmd("security","moderation",False,True, False),
    "mute":             Cmd("security","moderation",False,False,True, perm="staff"),
    "unmute":           Cmd("security","moderation",False,False,True, perm="staff"),
    "mutestatus":       Cmd("security","moderation",False,True, False, perm="manager"),
    "forceunmute":      Cmd("security","moderation",False,False,True, perm="admin"),
    "kick":             Cmd("security","moderation",False,False,True, perm="staff"),
    "ban":              Cmd("security","moderation",False,False,True, perm="manager"),
    "tempban":          Cmd("security","moderation",False,False,True, perm="manager"),
    "unban":            Cmd("security","moderation",False,False,True, perm="manager"),
    "modlog":           Cmd("security","moderation",False,True, False, perm="staff",
                            aliases=("roomlogs",)),
    "audit":            Cmd("security","admin",     False,True, False, perm="admin"),
    "auditbank":        Cmd("security","admin",     False,True, False, perm="admin"),
    "auditcasino":      Cmd("security","admin",     False,True, False, perm="admin"),
    "auditeconomy":     Cmd("security","admin",     False,True, False, perm="admin"),

    # ── AI assistant ─────────────────────────────────────────────────────────
    "ask":              Cmd("host","ai",  True, True, False, aliases=("assistant",)),
    "ai":               Cmd("host","ai",  True, True, False),
    "pendingaction":    Cmd("host","ai",  True, True, False),
    "confirm":          Cmd("host","ai",  True, False,False),
    "aidebug":          Cmd("host","ai",  True, True, False, perm="admin"),
    "aicapabilities":   Cmd("host","ai",  True, True, False),
    "aidelegations":    Cmd("host","ai",  True, True, False, perm="admin"),

    # ── Host: audit (new) ────────────────────────────────────────────────────
    "commandissues":    Cmd("host","audit",  True, True, False, perm="admin"),
    "fixcommandregistry": Cmd("host","audit",True, False,False, perm="owner"),

    # ── Host: bot spawn management (new) ─────────────────────────────────────
    "setbotspawn":      Cmd("host","room_admin", True, False, True, perm="manager"),
    "setbotspawnhere":  Cmd("host","room_admin", True, False, True, perm="manager"),
    "botspawns":        Cmd("host","room_admin", True, True,  False, perm="staff"),
    "clearbotspawn":    Cmd("host","room_admin", True, False, True, perm="manager"),
    "mypos":            Cmd("host","room_admin", True, True,  False, perm="any"),
    "positiondebug":    Cmd("host","room_admin", True, True,  False, perm="admin"),

    # ── Host: bot outfit management (new) ────────────────────────────────────
    "dressbot":         Cmd("host","botmode", True, False, True, perm="admin"),
    "savebotoutfit":    Cmd("host","botmode", True, False, True, perm="admin"),
    "botoutfitstatus":  Cmd("host","botmode", True, True,  False, perm="staff",
                            aliases=("botoutfits",)),
    "botoutfitdebug":   Cmd("host","botmode", True, True,  False, perm="admin",
                            aliases=("outfitdebug",)),
    "copyoutfit":       Cmd("host","botmode", True, False, True, perm="manager"),
    "wearuseroutfit":   Cmd("host","botmode", True, False, True, perm="manager"),
    "renamebotoutfit":  Cmd("host","botmode", True, False, True, perm="manager"),
    "clearbotoutfit":   Cmd("host","botmode", True, False, True, perm="manager"),
    # Per-bot self-managing outfit commands
    "copymyoutfit":     Cmd("host","botmode", True, False, True, perm="admin"),
    "copyoutfitfrom":   Cmd("host","botmode", True, False, True, perm="admin"),
    "savemyoutfit":     Cmd("host","botmode", True, False, True, perm="admin"),
    "wearoutfit":       Cmd("host","botmode", True, False, True, perm="admin"),
    "myoutfits":        Cmd("host","botmode", True, True,  False, perm="admin"),
    "myoutfitstatus":   Cmd("host","botmode", True, True,  False, perm="admin"),
    "directoutfittest": Cmd("host","botmode", True, True,  False, perm="admin"),

    # ── DJ: emote info (new) ─────────────────────────────────────────────────
    "emoteinfo":        Cmd("dj","social", True, True, False),

    # ── EventHost: new event / autogame commands ─────────────────────────────
    "adminsblessing":   Cmd("eventhost","events", False, False, True,  perm="manager",
                            aliases=("adminblessing",)),
    "eventresume":      Cmd("eventhost","events", False, False, True,  perm="manager"),
    "autogamestatus":   Cmd("eventhost","events", True,  True,  False, perm="staff"),
    "autogameresume":   Cmd("eventhost","events", False, False, True,  perm="manager"),

    # ── Miner: config and event status (new) ─────────────────────────────────
    "mineconfig":       Cmd("miner","mining", False, True,  False, perm="manager"),
    "mineeventstatus":  Cmd("miner","mining", False, True,  False, perm="staff"),

    # ── Miner: ore weight leaderboards (new) ─────────────────────────────────
    "oreweightlb":      Cmd("miner","mining", False, True,  False,
                            aliases=("weightlb","heaviest")),
    "myheaviest":       Cmd("miner","mining", False, True,  False),
    "oreweights":       Cmd("miner","mining", False, True,  False),
    "topweights":       Cmd("miner","mining", False, True,  False),
    "setweightlbmode":  Cmd("miner","mining", False, False, False, perm="admin"),

    # ── Miner: mining announce settings (new) ────────────────────────────────
    "mineannounce":         Cmd("miner","mining", False, True,  False),
    "setmineannounce":      Cmd("miner","mining", False, False, False, perm="admin"),
    "setoreannounce":       Cmd("miner","mining", False, False, False, perm="admin"),
    "oreannounce":          Cmd("miner","mining", False, True,  False),
    "mineannouncesettings": Cmd("miner","mining", False, True,  False, perm="admin"),

    # ── Miner: weight admin settings (new) ───────────────────────────────────
    "mineweights":          Cmd("miner","mining", False, True,  False),
    "setmineweights":       Cmd("miner","mining", False, False, False, perm="admin"),
    "setweightscale":       Cmd("miner","mining", False, False, False, perm="admin"),
    "setrarityweightrange": Cmd("miner","mining", False, False, False, perm="admin"),
    "oreweightsettings":    Cmd("miner","mining", False, True,  False, perm="admin"),

    # ── Host: bulk command testing (new) ─────────────────────────────────────
    "commandtestall":   Cmd("host","audit",  True,  True,  False, perm="staff",
                            aliases=("ctall",)),
    "commandtestgroup": Cmd("host","audit",  True,  True,  False, perm="staff",
                            aliases=("ctgroup",)),

    # ── Poker: pace / stack / deal status (new) ──────────────────────────────
    "pokermode":        Cmd("poker","casino", False, True,  False, perm="manager"),
    "pokerpace":        Cmd("poker","casino", False, True,  False, perm="staff"),
    "setpokerpace":     Cmd("poker","casino", False, False, True,  perm="manager"),
    "pokerstacks":      Cmd("poker","casino", False, True,  False, perm="staff"),
    "setpokerstack":    Cmd("poker","casino", False, False, True,  perm="manager"),
    "dealstatus":       Cmd("poker","casino", False, True,  False, perm="staff"),
    "pokerdealstatus":  Cmd("poker","casino", False, True,  False, perm="staff",
                            aliases=()),
    "resendcards":      Cmd("poker","casino", False, True,  False),
    "cards":            Cmd("poker","casino", False, True,  False,
                            aliases=("resendcards",)),
    "pokerplayers":     Cmd("poker","casino", False, True,  False, perm="staff"),

    # ── Poker: dashboard + staff controls (new) ───────────────────────────────
    "pokerdashboard":    Cmd("poker","casino", False, True,  False, perm="staff",
                             aliases=("pdash","pokeradmin")),
    "pokerpause":        Cmd("poker","casino", False, False, True,  perm="staff"),
    "pokerresume":       Cmd("poker","casino", False, False, True,  perm="staff"),
    "pokerforceadvance": Cmd("poker","casino", False, False, True,  perm="manager"),
    "pokerforceresend":  Cmd("poker","casino", False, False, True,  perm="manager"),
    "pokerturn":         Cmd("poker","casino", False, True,  False, perm="staff"),
    "pokerpots":         Cmd("poker","casino", False, True,  False, perm="staff"),
    "pokeractions":      Cmd("poker","casino", False, True,  False, perm="staff"),
    "pokerstylepreview": Cmd("poker","casino", False, False, False, perm="all"),
    "pokerresetturn":    Cmd("poker","casino", False, False, True,  perm="manager"),
    "pokerresethand":    Cmd("poker","casino", False, False, True,  perm="manager"),
    "pokerresettable":   Cmd("poker","casino", False, False, True,  perm="manager"),

    # ── Miner: panel (new) ────────────────────────────────────────────────────
    "minepanel":    Cmd("miner","mining", False, True,  False, perm="manager",
                        aliases=("miningpanel","mineadmin")),

    # ── Host: Time EXP bot exclusion (new) ───────────────────────────────────
    "setallowbotxp": Cmd("host","timeexp", False, False, True, perm="admin"),

    # ── Host: per-bot welcome messages (new) ─────────────────────────────────
    "botwelcome":        Cmd("host","welcome", False, True,  False, perm="manager"),
    "setbotwelcome":     Cmd("host","welcome", False, False, True,  perm="manager"),
    "resetbotwelcome":   Cmd("host","welcome", False, False, True,  perm="manager"),
    "previewbotwelcome": Cmd("host","welcome", False, True,  False, perm="manager"),
    "botwelcomes":       Cmd("host","welcome", False, False, True,  perm="manager"),

    # ── Banker: gold tip commands (new) ──────────────────────────────────────
    "goldtipsettings": Cmd("banker","goldtip", False, True,  False, perm="staff"),
    "setgoldrate":     Cmd("banker","goldtip", False, False, True,  perm="admin"),
    "goldtiplogs":     Cmd("banker","goldtip", False, True,  False, perm="staff"),
    "mygoldtips":      Cmd("banker","goldtip", False, True,  False),
    "goldtipstatus":   Cmd("banker","goldtip", False, True,  False),
    "tipcoinrate":     Cmd("banker","goldtip", False, True,  False, perm="staff"),
    "settipcoinrate":  Cmd("banker","goldtip", False, False, True,  perm="admin"),
    "bottiplogs":      Cmd("banker","goldtip", False, True,  False, perm="staff"),
    "mingoldtip":      Cmd("banker","goldtip", False, True,  False, perm="staff"),
    "setmingoldtip":   Cmd("banker","goldtip", False, False, True,  perm="admin"),
    # ── Banker: gold rain commands ────────────────────────────────────────
    "goldrain":          Cmd("banker","goldrain",False,False,True,  perm="manager",
                             aliases=("raingold","goldstorm","golddrop")),
    "raingold":          Cmd("banker","goldrain",True, False,True,  perm="manager"),
    "goldstorm":         Cmd("banker","goldrain",True, False,True,  perm="manager"),
    "golddrop":          Cmd("banker","goldrain",True, False,True,  perm="manager"),
    "goldrainstatus":    Cmd("banker","goldrain",False,True, False, perm="manager"),
    "cancelgoldrain":    Cmd("banker","goldrain",False,False,True,  perm="manager"),
    "goldrainhistory":   Cmd("banker","goldrain",False,True, False, perm="manager"),
    "goldraininterval":  Cmd("banker","goldrain",False,True, False, perm="manager"),
    "setgoldraininterval":Cmd("banker","goldrain",False,False,True, perm="manager"),
    "goldrainreplace":   Cmd("banker","goldrain",False,False,True,  perm="manager"),
    "goldrainpace":      Cmd("banker","goldrain",False,True, False, perm="manager"),
    "setgoldrainpace":   Cmd("banker","goldrain",False,False,True,  perm="manager"),
    # ── Host: message cap testing ─────────────────────────────────────────
    "msgcap":            Cmd("host",  "msg_cap", False,True, False, perm="manager"),
    "setmsgcap":         Cmd("host",  "msg_cap", False,False,True,  perm="manager"),
    # ── Banker: legacy gold commands (owner-only) ─────────────────────────
    "goldrainall":       Cmd("banker","goldrain",False,False,True,  perm="owner"),
    "goldraineligible":  Cmd("banker","goldrain",False,True, False, perm="owner"),
    "goldrainrole":      Cmd("banker","goldrain",False,False,True,  perm="owner"),
    "goldrainvip":       Cmd("banker","goldrain",False,False,True,  perm="owner"),
    "goldraintitle":     Cmd("banker","goldrain",False,False,True,  perm="owner"),
    "goldrainbadge":     Cmd("banker","goldrain",False,False,True,  perm="owner"),
    "goldrainlist":      Cmd("banker","goldrain",False,True, False, perm="owner"),
    "goldtip":           Cmd("banker","goldrain",False,False,True,  perm="owner"),
    "goldrefund":        Cmd("banker","goldrain",False,False,True,  perm="owner"),
    "goldwallet":        Cmd("banker","goldrain",False,True, False, perm="owner"),
    "goldtips":          Cmd("banker","goldrain",False,True, False, perm="owner"),
    "goldtx":            Cmd("banker","goldrain",False,True, False, perm="owner"),
    "pendinggold":       Cmd("banker","goldrain",False,True, False, perm="owner"),
    "confirmgoldtip":    Cmd("banker","goldrain",False,False,True,  perm="owner"),
    "setgoldrainstaff":  Cmd("banker","goldrain",False,False,True,  perm="owner"),
    "setgoldrainmax":    Cmd("banker","goldrain",False,False,True,  perm="owner"),
    "tipgold":         Cmd("banker","goldtip", False, False, True,  perm="owner",
                           aliases=("goldreward", "rewardgold")),
    "goldreward":      Cmd("banker","goldtip", False, False, True,  perm="owner"),
    "rewardgold":      Cmd("banker","goldtip", False, False, True,  perm="owner"),
    "tiplb":           Cmd("banker","goldtip", False, True,  False,
                           aliases=("tipleaderboard", "bottiplb", "bottipleaderboard")),
    "tipleaderboard":  Cmd("banker","goldtip", False, True,  False),
    "bottiplb":        Cmd("banker","goldtip", False, True,  False),
    "bottipleaderboard": Cmd("banker","goldtip", False, True, False),
    "roomtiplb":       Cmd("banker","goldtip", False, True,  False,
                           aliases=("roomtipleaderboard", "alltiplb", "alltipleaderboard")),
    "roomtipleaderboard": Cmd("banker","goldtip", False, True, False),
    "alltiplb":        Cmd("banker","goldtip", False, True,  False),
    "alltipleaderboard": Cmd("banker","goldtip", False, True, False),
    "tipreceiverlb":   Cmd("banker","goldtip", False, True,  False,
                           aliases=("topreceivers",)),
    "topreceivers":    Cmd("banker","goldtip", False, True,  False),

    # ── EventHost: new mining events (B-project) ──────────────────────────────
    "mineevents":       Cmd("eventhost","events", False, True,  False),
    "mineboosts":       Cmd("eventhost","events", False, True,  False),
    "luckstatus":       Cmd("eventhost","events", False, True,  False),
    "miningblessing":   Cmd("eventhost","events", False, False, True,  perm="manager"),
    "luckevent":        Cmd("eventhost","events", False, False, True,  perm="manager"),
    "miningeventstart": Cmd("eventhost","events", False, False, True,  perm="manager",
                            aliases=("startminingevent2",)),
    "eventmanager":     Cmd("eventhost","events", False, True,  False, perm="manager"),
    "eventpanel":       Cmd("eventhost","events", False, True,  False, perm="manager"),
    "eventeffects":     Cmd("eventhost","events", False, True,  False),
    "autoeventstatus":  Cmd("eventhost","events", False, True,  False, perm="manager"),
    "autoeventadd":     Cmd("eventhost","events", False, False, True,  perm="manager"),
    "autoeventremove":  Cmd("eventhost","events", False, False, True,  perm="manager"),
    "autoeventinterval": Cmd("eventhost","events", False, False, True, perm="manager"),
    # Event Manager catalog + pool (new)
    "eventlist":         Cmd("eventhost","events", False, True,  False),
    "eventpreview":      Cmd("eventhost","events", False, True,  False),
    "aepool":            Cmd("eventhost","events", False, True,  False, perm="manager"),
    "autoeventpool":     Cmd("eventhost","events", False, True,  False, perm="manager"),
    "aeadd":             Cmd("eventhost","events", False, False, True,  perm="manager"),
    "aeremove":          Cmd("eventhost","events", False, False, True,  perm="manager"),
    "aequeue":           Cmd("eventhost","events", False, True,  False, perm="manager"),
    "autoeventqueue":    Cmd("eventhost","events", False, True,  False, perm="manager"),
    "aenext":            Cmd("eventhost","events", False, True,  False, perm="manager"),
    "autoeventnext":     Cmd("eventhost","events", False, True,  False, perm="manager"),
    "aestatus":          Cmd("eventhost","events", False, True,  False, perm="manager"),
    "eventheartbeat":    Cmd("eventhost","events", False, True,  False, perm="manager"),
    "eventscheduler":    Cmd("eventhost","events", False, True,  False, perm="manager"),
    "eventcooldowns":    Cmd("eventhost","events", False, True,  False, perm="manager"),
    "seteventcooldown":  Cmd("eventhost","events", False, False, True,  perm="manager"),
    "eventweights":      Cmd("eventhost","events", False, True,  False, perm="manager"),
    "seteventweight":    Cmd("eventhost","events", False, False, True,  perm="manager"),
    "eventhistory":      Cmd("eventhost","events", False, True,  False, perm="manager"),
    "aehistory":         Cmd("eventhost","events", False, True,  False, perm="manager"),
    "autoeventhistory":  Cmd("eventhost","events", False, True,  False, perm="manager"),
    "setaeinterval":     Cmd("eventhost","events", False, False, True,  perm="manager"),
    "setautoeventinterval": Cmd("eventhost","events", False, False, True, perm="manager"),
    "setaeduration":     Cmd("eventhost","events", False, False, True,  perm="manager"),
    "setautoeventduration": Cmd("eventhost","events", False, False, True, perm="manager"),
    "seteventduration":  Cmd("eventhost","events", False, False, True,  perm="manager"),
    "aeinterval":        Cmd("eventhost","events", False, True,  False, perm="manager"),
    "aeduration":        Cmd("eventhost","events", False, True,  False, perm="manager"),
    "aererollnext":      Cmd("eventhost","events", False, False, True,  perm="manager"),
    "rerollae":          Cmd("eventhost","events", False, False, True,  perm="manager"),
    "setnextae":         Cmd("eventhost","events", False, False, True,  perm="manager"),
    "setnextautoevent":  Cmd("eventhost","events", False, False, True,  perm="manager"),

    # ── Miner: ore chance commands (A2) ──────────────────────────────────────
    "orechances":       Cmd("miner","mining", False, True,  False),
    "orechance":        Cmd("miner","mining", False, True,  False),
    "setorechance":     Cmd("miner","mining", False, False, True,  perm="manager"),
    "setraritychance":  Cmd("miner","mining", False, False, True,  perm="manager"),
    "reloadorechances": Cmd("miner","mining", False, False, True,  perm="manager"),

    # ── Economy panel + rarity caps ──────────────────────────────────────────
    "economypanel":     Cmd("miner","economy", False, True,  False, perm="manager",
                            aliases=("economybalance","miningeconomy")),
    "economysettings":  Cmd("miner","economy", False, True,  False, perm="manager"),
    "economycap":       Cmd("miner","economy", False, True,  False, perm="manager",
                            aliases=("economycaps",)),
    "setraritycap":     Cmd("miner","economy", False, False, True,  perm="manager"),
    "resetraritycaps":  Cmd("miner","economy", False, False, True,  perm="manager"),
    "payoutlogs":       Cmd("miner","economy", False, True,  False, perm="manager",
                            aliases=("minepayoutlogs",)),
    "biggestpayouts":   Cmd("miner","economy", False, True,  False, perm="manager"),

    # ── Miner: ore info / price commands (A3/A4) ─────────────────────────────
    "oreprices":        Cmd("miner","mining", False, True,  False,
                            aliases=("orevalues", "orevalue")),
    "oreinfo":          Cmd("miner","mining", False, True,  False,
                            aliases=("oredetail", "oredetails")),

    # ── Miner: simulation + forced-drop tools ─────────────────────────────────
    "simannounce":      Cmd("miner","mining", False, False, True,  perm="manager"),
    "forcedrop":        Cmd("miner","mining", False, False, True,  perm="owner"),
    "forcedropore":     Cmd("miner","mining", False, False, True,  perm="owner"),
    "forcedropstatus":  Cmd("miner","mining", False, True,  False, perm="owner"),
    "clearforcedrop":   Cmd("miner","mining", False, False, True,  perm="owner"),

    # ── Fishing: public commands ──────────────────────────────────────────────
    "fish":             Cmd("fisher","fishing", True, False, True,  aliases=("cast","reel")),
    "fishlist":         Cmd("fisher","fishing", True, True,  False, aliases=("fishrarity",)),
    "fishprices":       Cmd("fisher","fishing", True, True,  False, aliases=("fishvalues",)),
    "fishinfo":         Cmd("fisher","fishing", True, True,  False, aliases=("fishdetail",)),
    "myfish":           Cmd("fisher","fishing", True, True,  False, aliases=("fishinv","fishbag","fishinventory")),
    "sellfish":         Cmd("fisher","fishing", True, True,  False),
    "sellallfish":      Cmd("fisher","fishing", True, True,  False),
    "sellfishrarity":   Cmd("fisher","fishing", True, False, True),
    "fishbook":         Cmd("fisher","fishing", True, True,  False),
    "fishautosell":     Cmd("fisher","fishing", True, True,  False),
    "fishautosellrare": Cmd("fisher","fishing", True, True,  False),
    "fishlevel":        Cmd("fisher","fishing", True, True,  False, aliases=("fishxp","fishlvl")),
    "fishstats":        Cmd("fisher","fishing", True, True,  False),
    "fishboosts":       Cmd("fisher","fishing", True, True,  False, aliases=("fishingevents",)),
    "fishhelp":         Cmd("fisher","help",    True, True,  False, aliases=("fishinghelp",)),
    "topfish":          Cmd("fisher","fishing", True, True,  False, aliases=("topfishing","fishlb")),
    "topweightfish":    Cmd("fisher","fishing", True, True,  False, aliases=("biggestfish","heaviestfish")),
    # ── Fishing: rod commands ─────────────────────────────────────────────────
    "rods":             Cmd("fisher","fishing", True, True,  False, aliases=("fishroads","listfishrods")),
    "myrod":            Cmd("fisher","fishing", True, True,  False, aliases=("equippedrod",)),
    "rodshop":          Cmd("fisher","fishing", True, True,  False, aliases=("fishrodshop",)),
    "buyrod":           Cmd("fisher","fishing", True, False, True,  aliases=("purchaserod",)),
    "equiprod":         Cmd("fisher","fishing", True, False, True,  aliases=("switchrod",)),
    "rodinfo":          Cmd("fisher","fishing", True, True,  False, aliases=("roddetail",)),
    "rodstats":         Cmd("fisher","fishing", True, True,  False),
    "rodupgrade":       Cmd("fisher","fishing", True, True,  False),
    # ── Fishing: AutoFish ─────────────────────────────────────────────────────
    "autofish":         Cmd("fisher","fishing", True, False, True,  aliases=("af",)),
    "autofishstatus":   Cmd("fisher","fishing", True, True,  False, aliases=("afstatus",)),
    "autofishsettings": Cmd("fisher","fishing", False,True,  False, perm="manager"),
    "setautofish":      Cmd("fisher","fishing", False,False, True,  perm="manager"),
    "setautofishduration":  Cmd("fisher","fishing", False,False,True,  perm="manager"),
    "setautofishattempts":  Cmd("fisher","fishing", False,False,True,  perm="manager"),
    "setautofishdailycap":  Cmd("fisher","fishing", False,False,True,  perm="manager"),
    # ── Mining: AutoMine ──────────────────────────────────────────────────────
    "automine":             Cmd("miner","mining", True, False, True,  aliases=("am",)),
    "autominestatus":       Cmd("miner","mining", True, True,  False, aliases=("amstatus",)),
    "autominesettings":     Cmd("miner","mining", False,True,  False, perm="manager"),
    "setautomine":          Cmd("miner","mining", False,False, True,  perm="manager"),
    "setautomineduration":  Cmd("miner","mining", False,False, True,  perm="manager"),
    "setautomineattempts":  Cmd("miner","mining", False,False, True,  perm="manager"),
    "setautominedailycap":  Cmd("miner","mining", False,False, True,  perm="manager"),
    # ── First-find race event ─────────────────────────────────────────────────
    "setfirstfind":          Cmd("banker","economy", False,False, True,  perm="admin"),
    "setfirstfinditem":      Cmd("banker","economy", False,False, True,  perm="admin"),
    "setfirstfindreward":    Cmd("banker","economy", False,False, True,  perm="admin"),
    "startfirstfind":        Cmd("banker","economy", False,False, True,  perm="admin"),
    "stopfirstfind":         Cmd("banker","economy", False,False, True,  perm="admin"),
    "firstfindstatus":       Cmd("banker","economy", True, True,  False, aliases=("firstfindcheck",)),
    "firstfindcheck":        Cmd("banker","economy", True, True,  False),
    "firstfindwinners":      Cmd("banker","economy", True, True,  False),
    "firstfindrewards":      Cmd("banker","economy", True, True,  False, aliases=("firstfindlist","firstfindreward")),
    "resetfirstfind":        Cmd("banker","economy", False,False, True,  perm="admin"),
    "firstfindpending":      Cmd("banker","economy", False, True, False, perm="admin",
                                 aliases=("firstfindpay",)),
    "firstfindpay":          Cmd("banker","economy", False, True, False, perm="admin"),
    "paypendingfirstfind":   Cmd("banker","economy", False,False, True,  perm="admin",
                                 aliases=("retryfirstfind",)),
    "retryfirstfind":        Cmd("banker","economy", False,False, True,  perm="admin"),
    # ── Fishing force drop (owner-only) ──────────────────────────────────────
    "forcedropfish":        Cmd("fisher","fishing", False, False, True,  perm="owner", aliases=("forcefishdrop","forcefish")),
    "forcedropfishitem":    Cmd("fisher","fishing", False, False, True,  perm="owner", aliases=("forcefishdropfish",)),
    "forcedropfishstatus":  Cmd("fisher","fishing", False, True,  False, perm="owner", aliases=("forcefishstatus",)),
    "forcedropfishdebug":   Cmd("fisher","fishing", False, True,  True,  perm="owner"),
    "clearforcedropfish":   Cmd("fisher","fishing", False, False, True,  perm="owner", aliases=("clearforcefish",)),
    "clearforceddropfish":  Cmd("fisher","fishing", False, False, False, perm="owner"),
    # ── Big announce ──────────────────────────────────────────────────────────
    "bigannounce":          Cmd("host","economy",   True, True,  False),
    "bigannouncestatus":    Cmd("host","economy",   True, True,  False),
    "setbigannounce":       Cmd("host","economy",   False,False, True,  perm="manager"),
    "setbigreact":          Cmd("host","economy",   False,False, True,  perm="manager"),
    "setbotbigreact":       Cmd("host","economy",   False,False, True,  perm="manager"),
    # ── BJ card display / rules / bonus viewer ────────────────────────────────
    "bjcards":          Cmd("blackjack","casino", True, True,  False, aliases=("blackjackcards","cardmode","bjcardmode")),
    "bjrules":          Cmd("blackjack","casino", True, True,  False),
    "bjbonussettings":  Cmd("blackjack","casino", True, True,  False, aliases=("bjbonus","bjbonussetting")),
    # ── BJ pair bonus settings ────────────────────────────────────────────────
    "setbjbonus":           Cmd("blackjack","casino",False,False,True, perm="manager"),
    "setbjbonuscap":        Cmd("blackjack","casino",False,False,True, perm="manager"),
    "setbjbonuspair":       Cmd("blackjack","casino",False,False,True, perm="manager"),
    "setbjbonuscolor":      Cmd("blackjack","casino",False,False,True, perm="manager"),
    "setbjbonusperfect":    Cmd("blackjack","casino",False,False,True, perm="manager"),
    "setbjinsurance":       Cmd("blackjack","casino",False,False,True, perm="manager"),

    # ── BJ surrender (blackjack bot) ─────────────────────────────────────────
    "bsurrender":       Cmd("blackjack","casino",  True,  False, True),

    # ── System dashboard (host) ───────────────────────────────────────────────
    "botdashboard":     Cmd("host",    "system",  False, True,  False, perm="manager",
                            aliases=("botsystem",)),

    # ── Reward center (banker) ────────────────────────────────────────────────
    "rewardpending":    Cmd("banker",  "rewards", False, True,  False, perm="manager",
                            aliases=("pendingrewards",)),
    "rewardlogs":       Cmd("banker",  "rewards", False, True,  False, perm="manager"),
    "markrewardpaid":   Cmd("banker",  "rewards", False, False, True,  perm="manager"),
    "economyreport":    Cmd("banker",  "economy", False, True,  False, perm="manager"),

    # ── Event presets (eventhost) ─────────────────────────────────────────────
    "eventpreset":      Cmd("eventhost","events", False, False, True,  perm="manager"),

    # ── Player onboarding aliases (host) ──────────────────────────────────────
    "begin":            Cmd("host",    "help",    True,  True,  False, aliases=("newplayer",)),

    # ── Daily quest aliases (banker) ──────────────────────────────────────────
    "dailies":          Cmd("banker",  "quests",  True,  True,  False),
    "claimdaily":       Cmd("banker",  "quests",  True,  False, True),

    # ── Staff audit log (security) ────────────────────────────────────────────
    "auditlog":         Cmd("security","audit",   False, True,  False, perm="staff"),

    # ── Weekly leaderboard (host + banker) ────────────────────────────────────
    "weeklylb":         Cmd("host",    "economy", True,  True,  False,
                            aliases=("weeklyleaderboard",)),
    "weeklyreset":      Cmd("banker",  "economy", False, False, True,  perm="manager"),
    "weeklyrewards":    Cmd("banker",  "economy", False, True,  False, perm="manager"),
    "setweeklyreward":  Cmd("banker",  "economy", False, False, True,  perm="manager"),
    "weeklystatus":     Cmd("banker",  "economy", False, True,  False, perm="manager"),

    # ── Safe mode + diagnostic (security) ─────────────────────────────────────
    "safemode":         Cmd("security","system",  False, True,  False, perm="manager"),
    "active":           Cmd("host",    "system",  True,  True,  False),
    "repair":           Cmd("security","system",  False, True,  False, perm="manager"),

    # ── Player utility commands (host) ────────────────────────────────────────
    "menu":             Cmd("host",    "help",    True,  True,  False),
    "cooldowns":        Cmd("host",    "help",    True,  True,  False),
    "rewards":          Cmd("host",    "rewards", True,  True,  False),
    "wherebots":        Cmd("host",    "help",    True,  True,  False),
    "updates":          Cmd("host",    "help",    True,  True,  False),
    "rankup":           Cmd("host",    "help",    True,  True,  False),

    # ── Suggestions / bug reports (host + security) ────────────────────────────
    "suggest":          Cmd("host",    "help",    True,  False, True),
    "suggestions":      Cmd("host",    "help",    False, True,  False, perm="manager"),
    "bugreport":        Cmd("security","system",  True,  False, True),
    "bugreports":       Cmd("security","system",  False, True,  False, perm="manager"),

    # ── Event votes (eventhost) ───────────────────────────────────────────────
    "eventvote":        Cmd("eventhost","events", True,  True,  False),
    "voteevent":        Cmd("eventhost","events", True,  False, True),

    # ── Subscriber notification preferences (host) ────────────────────────────
    "notif":            Cmd("host",    "help",    True,  True,  False),
    "notifon":          Cmd("host",    "help",    True,  False, True),
    "notifoff":         Cmd("host",    "help",    True,  False, True),
    "notifall":         Cmd("host",    "help",    True,  False, True),
    "notifdm":          Cmd("host",    "help",    True,  True,  False),
    "opennotifs":       Cmd("host",    "help",    True,  True,  False),
    "subnotify":        Cmd("host",    "help",    False, False, True,  perm="manager"),
    "subnotif":         Cmd("host",    "help",    False, False, True,  perm="manager"),
    "subnotifyinvite":  Cmd("host",    "help",    False, False, True,  perm="manager"),
    "subnotifystatus":  Cmd("host",    "help",    False, True,  False, perm="manager"),
    "testnotify":       Cmd("host",    "help",    False, False, True,  perm="manager"),
    "setsubnotifycooldown": Cmd("host","help",    False, False, True,  perm="manager"),

    # ── Notification / room debug (host) ──────────────────────────────────────
    "notifydebug":      Cmd("host",    "help",    False, True,  False, perm="manager"),
    "roomusers":        Cmd("host",    "help",    False, True,  False, perm="manager"),
    "testwhisper":      Cmd("host",    "help",    False, False, True,  perm="manager"),
    "notifrefresh":     Cmd("host",    "help",    False, False, True,  perm="manager"),

    # ── QoL / player support ──────────────────────────────────────────────────
    "quicktest":        Cmd("host",    "system",  False, True,  False, perm="manager"),
    "playercheck":      Cmd("security","system",  False, True,  False, perm="manager"),
    "claimrewards":     Cmd("banker",  "rewards", True,  True,  False, perm="player"),
    "eventcalendar":    Cmd("eventhost","events", True,  True,  False, perm="player",
                            aliases=("calendar",)),
    "lastupdate":       Cmd("host",    "help",    True,  True,  False, perm="player"),
    "knownissues":      Cmd("host",    "help",    True,  True,  False, perm="player",
                            aliases=("issues",)),
    "knownissue":       Cmd("host",    "help",    False, False, True,  perm="manager"),
    "feedback":         Cmd("host",    "help",    True,  False, True,  perm="player"),
    "feedbacks":        Cmd("host",    "help",    False, True,  False, perm="manager",
                            aliases=("feedbacklist",)),
    "todo":             Cmd("host",    "system",  False, True,  False, perm="manager"),

    # ── Auto event + system diagnostics ──────────────────────────────────────
    "aetest":           Cmd("eventhost","events", False, True,  False, perm="manager"),
    "ownercheck":       Cmd("security", "system", False, True,  False, perm="manager"),

    # ── RoleSpawn / AutoSpawn (security + host) ───────────────────────────────
    "rolespawn":        Cmd("security","teleport", True,  True,  False),
    "rolespawns":       Cmd("security","teleport", True,  True,  False),
    "setrolespawn":     Cmd("security","teleport", False, False, True,  perm="manager"),
    "delrolespawn":     Cmd("security","teleport", False, False, True,  perm="manager"),
    "autospawn":        Cmd("host",    "teleport", True,  True,  True,  perm="manager"),

    # ── Rarity chance display (miner / fisher / host) ─────────────────────────
    "minechances":      Cmd("miner",   "mining",  True,  True,  False),
    "fishchances":      Cmd("fisher",  "fishing", True,  True,  False,
                            aliases=("fishingchances",)),
    "raritychances":    Cmd("host",    "mining",  True,  True,  False),

    # ── VIP — player-facing (banker) ──────────────────────────────────────────
    "vip":              Cmd("banker",  "vip",     True,  True,  False),
    "vipperks":         Cmd("banker",  "vip",     True,  True,  False),
    "myvip":            Cmd("banker",  "vip",     True,  True,  False),
    "giftvip":          Cmd("banker",  "vip",     True,  False, True),
    "viplist":          Cmd("banker",  "vip",     True,  True,  False),
    "grantvip":         Cmd("banker",  "vip",     False, False, True,  perm="manager"),
    "removevip":        Cmd("banker",  "vip",     False, False, True,  perm="manager"),
    "addvip":           Cmd("banker",  "vip",     False, False, True,  perm="manager"),
    "vips":             Cmd("banker",  "vip",     False, True,  False, perm="manager"),
    "setvipprice":      Cmd("banker",  "vip",     False, False, True,  perm="manager"),

    # ── Donation / Sponsorship (banker) ───────────────────────────────────────
    "donate":           Cmd("banker",  "economy", True,  True,  False),
    "donationgoal":     Cmd("banker",  "economy", True,  True,  False),
    "topdonors":        Cmd("banker",  "economy", True,  True,  False),
    "sponsor":          Cmd("banker",  "economy", True,  True,  False),
    "sponsorgoldrain":  Cmd("banker",  "goldrain",True,  True,  False),
    "sponsorevent":     Cmd("eventhost","events", True,  True,  False),
    "supporter":        Cmd("banker",  "economy", True,  True,  False),
    "perks":            Cmd("banker",  "vip",     True,  True,  False),
    "setdonationgoal":  Cmd("banker",  "economy", False, False, True,  perm="manager"),
    "donationaudit":    Cmd("banker",  "economy", False, True,  False, perm="admin"),
    "setsponsorprice":  Cmd("banker",  "economy", False, False, True,  perm="manager"),
}

# ---------------------------------------------------------------------------
# Alias expansion: alias → primary command name
# Built automatically from REGISTRY at import time.
# ---------------------------------------------------------------------------
alias_map: dict[str, str] = {}
for _primary, _entry in REGISTRY.items():
    for _alias in _entry.aliases:
        alias_map[_alias] = _primary


def get_entry(cmd: str) -> tuple[str, Cmd] | None:
    """Return (primary_name, Cmd) for cmd or its alias, or None if unknown."""
    if cmd in REGISTRY:
        return cmd, REGISTRY[cmd]
    primary = alias_map.get(cmd)
    if primary:
        return primary, REGISTRY[primary]
    return None
