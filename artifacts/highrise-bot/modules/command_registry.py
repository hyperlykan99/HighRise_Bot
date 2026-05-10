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
    "goldhelp":         Cmd("eventhost","help",True, True,False),

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
    "double":           Cmd("blackjack","casino",False,False,True),
    "split":            Cmd("blackjack","casino",False,False,True),
    "insurance":        Cmd("blackjack","casino",False,False,True),
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
    "autogames":        Cmd("eventhost","events",True, True, False),
    "autoevents":       Cmd("eventhost","events",True, True, False),
    "autogamesowner":   Cmd("eventhost","events",False,True, False, perm="manager"),
    "gameconfig":       Cmd("eventhost","events",False,True, False, perm="manager"),
    "stopautogames":    Cmd("eventhost","events",False,False,True, perm="manager",
                            aliases=("killautogames",)),
    "fixautogames":     Cmd("eventhost","events",False,False,True, perm="manager"),
    "announce":         Cmd("eventhost","events",False,False,True, perm="manager"),

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
    "myfish":           Cmd("fisher","fishing", True, True,  False, aliases=("fishinv",)),
    "sellfish":         Cmd("fisher","fishing", True, True,  False),
    "sellallfish":      Cmd("fisher","fishing", True, True,  False),
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
    # ── First-find rewards ────────────────────────────────────────────────────
    "firstfindrewards":     Cmd("banker","economy", True, True,  False, aliases=("firstfindlist","firstfindreward")),
    "setfirstfind":         Cmd("banker","economy", False,False, True,  perm="manager"),
    "setfirstfindreward":   Cmd("banker","economy", False,False, True,  perm="manager"),
    "firstfindstatus":      Cmd("banker","economy", True, True,  False, aliases=("firstfindcheck",)),
    "resetfirstfind":       Cmd("banker","economy", False,False, True,  perm="manager"),
    # ── Fishing force drop (owner-only) ──────────────────────────────────────
    "forcefishdrop":        Cmd("fisher","fishing", False, False, True,  perm="owner", aliases=("forcefish",)),
    "forcefishdropfish":    Cmd("fisher","fishing", False, False, True,  perm="owner"),
    "forcefishstatus":      Cmd("fisher","fishing", False, True,  False, perm="owner"),
    "clearforcefish":       Cmd("fisher","fishing", False, False, True,  perm="owner"),
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
