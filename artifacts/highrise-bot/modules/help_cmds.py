"""
modules/help_cmds.py  —  3.1P Global Help System
-------------------------------------------------
!commands [category]          — category browser
!commands search [term]       — command search
!commands find [term]         — alias for search
!command [cmd]                — per-command detail

Category list (player):
  earn, mining, fishing, collection, games, casino,
  economy, shop, luxe, profile, missions, events, seasons, help

Category list (staff+):
  staff, admin, owner

All output is whisper only. Messages ≤ 249 chars.
Currencies: 🪙 ChillCoins | 🎫 Luxe Tickets
"""

from __future__ import annotations

from highrise import BaseBot, User

from modules.permissions import (
    can_moderate, is_admin, is_owner, can_manage_economy,
)


# ── helpers ──────────────────────────────────────────────────────────────────

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


# ── Category text blocks (≤249 chars each) ───────────────────────────────────

_CAT: dict[str, str] = {

    "earn": (
        "💰 Earn Commands\n"
        "!mine — mine once\n"
        "!fish — fish once\n"
        "!daily — daily reward\n"
        "!missions — daily missions\n"
        "!weekly — weekly missions\n"
        "!coinflip [heads|tails] [bet]"
    ),

    "mining": (
        "⛏️ Mining\n"
        "!mine  (!m  !dig)\n"
        "!automine — timer auto\n"
        "!automine luxe — Luxe timer\n"
        "!automine off — stop\n"
        "!mineluck — luck stack\n"
        "!orebook — discovery book\n"
        "!lastminesummary"
    ),

    "mining2": (
        "⛏️ Mining 2\n"
        "!minelb !mineshop !minedaily\n"
        "!mineprofile !minechances\n"
        "!orelist [rarity] !oreprices\n"
        "!oreinfo [ore] !oremastery\n"
        "!topweights !myheaviest"
    ),

    "fishing": (
        "🎣 Fishing\n"
        "!fish\n"
        "!autofish — timer auto\n"
        "!autofish luxe — Luxe timer\n"
        "!autofish off — stop\n"
        "!fishluck — luck stack\n"
        "!fishbook — discovery book\n"
        "!lastfishsummary"
    ),

    "fishing2": (
        "🎣 Fishing 2\n"
        "!fishinv !sellfish\n"
        "!myrod !rods !rodshop\n"
        "!fishchances !fishlist\n"
        "!topfish !topweightfish\n"
        "!topfishcollectors"
    ),

    "collection": (
        "📚 Collection\n"
        "!orebook — ore discoveries\n"
        "!fishbook — fish discoveries\n"
        "!collection — full overview\n"
        "!rarelog — rare finds log\n"
        "!topcollectors ore\n"
        "!topfishcollectors"
    ),

    "games": (
        "🎮 Games\n"
        "!trivia  !scramble  !riddle\n"
        "!answer [answer]\n"
        "!coinflip [heads|tails] [bet]\n"
        "!bjhelp — blackjack\n"
        "!pokerhelp — poker\n"
        "!autogames status"
    ),

    "casino": (
        "🎰 Casino\n"
        "!bet [amount] — blackjack\n"
        "!hit  !stand  !double  !split\n"
        "!bjstatus  !bjhelp  !bjrules\n"
        "!poker join [buyin]\n"
        "!check  !call  !raise  !fold\n"
        "!pokerhelp"
    ),

    "economy": (
        "💰 Economy\n"
        "!balance — check 🪙\n"
        "!tickets — Luxe balance 🎫\n"
        "!buycoins — coin packs\n"
        "!luxeshop — Luxe shop\n"
        "!send [user] [amount]\n"
        "!vip — VIP info\n"
        "!daily — daily reward"
    ),

    "shop": (
        "🛍️ Shop\n"
        "!shop — browse shop\n"
        "!buy [#] — buy item\n"
        "!myitems — your items\n"
        "!shop badges — badge shop\n"
        "!luxeshop — Luxe shop\n"
        "!buyluxe [#] — buy with 🎫"
    ),

    "luxe": (
        "🎫 Luxe Tickets\n"
        "!tickets — Luxe balance\n"
        "!luxeshop — Luxe shop\n"
        "!buyluxe [#] — buy Luxe item\n"
        "!buycoins — coin packs\n"
        "!vip — VIP with 🎫\n"
        "!autotime — Luxe auto time"
    ),

    "profile": (
        "👤 Profile\n"
        "!profile — your profile\n"
        "!xp  (!rank) — XP & level\n"
        "!achievements — achievements\n"
        "!badges — your badges\n"
        "!rep — reputation\n"
        "!flex — flex card\n"
        "!privacy — privacy settings"
    ),

    "missions": (
        "📋 Missions\n"
        "!missions — daily missions\n"
        "!weekly — weekly missions\n"
        "!today  (!progress) — all progress\n"
        "!daily — daily reward\n"
        "!streak — streak status\n"
        "!season — season progress"
    ),

    "events": (
        "🎉 Events\n"
        "!events — schedule\n"
        "!eventstatus — active event\n"
        "!nextevent — next event\n"
        "!eventhelp — event help\n"
        "!season — season progress\n"
        "!topseason — season lb"
    ),

    "seasons": (
        "🏆 Seasons\n"
        "!season — your season points\n"
        "!topseason — leaderboard\n"
        "!seasonrewards — pending rewards\n"
        "!xp — XP and level\n"
        "!today — daily progress"
    ),

    "help": (
        "📘 Help Pages\n"
        "!help basic  !help games\n"
        "!help bank  !help vip\n"
        "!help mining  !help fishing\n"
        "!help casino  !help blackjack\n"
        "!help room  !help party\n"
        "!help events  !help economy"
    ),

    # ── Staff / admin / owner (gated) ─────────────────────────────────────────

    "staff": (
        "🛡️ Staff Commands\n"
        "!modhelp — full mod guide\n"
        "!warn [user] [reason]\n"
        "!warnings [user]\n"
        "!mute [user] [mins]\n"
        "!unmute [user]\n"
        "!modlog — recent mod log"
    ),

    "admin": (
        "🛠️ Admin Commands\n"
        "!eventadmin — event config\n"
        "!missionadmin — mission config\n"
        "!safetyadmin — safety config\n"
        "!luxeadmin — Luxe config\n"
        "!currencycheck — currency scan\n"
        "!ownerdash — analytics\n"
        "!adminhelp — full reference"
    ),

    "owner": (
        "👑 Owner Commands\n"
        "!ownerdash — analytics dash\n"
        "!economydash  !luxedash\n"
        "!audit [user] — player audit\n"
        "!currencycheck — currency scan\n"
        "!commandissues — command check\n"
        "!launchcheck — launch health\n"
        "!bothealth  !stability"
    ),
}

# ── Main menu texts ───────────────────────────────────────────────────────────

_MENU_1 = (
    "📜 Commands\n"
    "!commands earn\n"
    "!commands games\n"
    "!commands shop\n"
    "!commands profile\n"
    "!commands events\n"
    "!commands staff"
)

_MENU_2 = (
    "More:\n"
    "!commands mining\n"
    "!commands fishing\n"
    "!commands casino\n"
    "!commands economy\n"
    "!commands seasons\n"
    "Search: !commands search [term]"
)

# ── Searchable flat list: (trigger_words, display_line, perm) ─────────────────
# perm: "player" | "staff" | "admin" | "owner"

_SEARCHABLE: list[tuple[list[str], str, str]] = [
    (["mine", "mining", "dig"],          "!mine — mine once",               "player"),
    (["automine", "auto mine", "autom"], "!automine — timer auto mine",     "player"),
    (["automine luxe", "luxe mine"],     "!automine luxe — Luxe auto time", "player"),
    (["mineluck", "luck"],               "!mineluck — luck stack",          "player"),
    (["orebook", "ore book"],            "!orebook — ore discoveries",      "player"),
    (["mineprofile", "mine profile"],    "!mineprofile — mining stats",     "player"),
    (["minechances", "ore chances"],     "!minechances — ore drop rates",   "player"),
    (["orelist"],                        "!orelist [rarity] — ore list",    "player"),
    (["oreinfo"],                        "!oreinfo [ore] — ore details",    "player"),
    (["lastminesummary", "mine summary"],"!lastminesummary — last session", "player"),
    (["fish", "fishing"],                "!fish — fish once",               "player"),
    (["autofish", "auto fish"],          "!autofish — timer auto fish",     "player"),
    (["autofish luxe", "luxe fish"],     "!autofish luxe — Luxe auto time", "player"),
    (["fishluck"],                       "!fishluck — fishing luck stack",  "player"),
    (["fishbook"],                       "!fishbook — fish discoveries",    "player"),
    (["lastfishsummary"],                "!lastfishsummary — last session", "player"),
    (["fishinv"],                        "!fishinv — fish inventory",       "player"),
    (["sellfish"],                       "!sellfish — sell all fish",       "player"),
    (["myrod", "rod"],                   "!myrod — equipped rod",           "player"),
    (["rods", "rodshop"],                "!rods / !rodshop — rod store",    "player"),
    (["balance", "bal", "coins"],        "!balance — check 🪙 coins",       "player"),
    (["tickets", "luxe", "ticket"],      "!tickets — Luxe 🎫 balance",      "player"),
    (["buycoins", "coin pack"],          "!buycoins — buy coin packs",      "player"),
    (["luxeshop", "luxe shop"],          "!luxeshop — Luxe Ticket shop",    "player"),
    (["buyluxe"],                        "!buyluxe [#] — buy Luxe item",    "player"),
    (["autotime", "auto time"],          "!autotime — Luxe auto time",      "player"),
    (["send", "transfer"],               "!send [user] [amount] — send 🪙", "player"),
    (["daily", "daily reward"],          "!daily — daily reward",           "player"),
    (["streak"],                         "!streak — streak status",         "player"),
    (["vip", "vipperks"],                "!vip — VIP info & perks",         "player"),
    (["buyvip"],                         "!buyvip 1d/7d/30d — buy VIP",     "player"),
    (["myvip"],                          "!myvip — your VIP status",        "player"),
    (["shop"],                           "!shop — browse the shop",         "player"),
    (["buy"],                            "!buy [#] — buy shop item",        "player"),
    (["myitems"],                        "!myitems — your items",           "player"),
    (["bet", "blackjack", "bj"],         "!bet [amount] — play blackjack",  "player"),
    (["hit"],                            "!hit — draw a card",              "player"),
    (["stand"],                          "!stand — hold hand",              "player"),
    (["double"],                         "!double — double down",           "player"),
    (["split"],                          "!split — split hand",             "player"),
    (["bjhelp", "bj help"],              "!bjhelp — blackjack help",        "player"),
    (["bjstatus", "bj status"],          "!bjstatus — blackjack status",    "player"),
    (["bjrules", "bj rules"],            "!bjrules — BJ rules & payouts",   "player"),
    (["bjshoe", "shoe"],                 "!bjshoe — shoe status",           "player"),
    (["poker"],                          "!poker — poker info / join",      "player"),
    (["pokerhelp"],                      "!pokerhelp — poker help",         "player"),
    (["coinflip", "flip"],               "!coinflip [h|t] [bet] — flip",    "player"),
    (["trivia"],                         "!trivia — trivia question",        "player"),
    (["scramble"],                       "!scramble — word scramble",        "player"),
    (["riddle"],                         "!riddle — riddle game",            "player"),
    (["answer"],                         "!answer [answer] — answer game",  "player"),
    (["profile", "me"],                  "!profile — your profile",         "player"),
    (["xp", "rank"],                     "!xp / !rank — XP and level",      "player"),
    (["achievements"],                   "!achievements — achievements",     "player"),
    (["badges"],                         "!badges — badge list",            "player"),
    (["rep"],                            "!rep — reputation",               "player"),
    (["flex"],                           "!flex — flex card",               "player"),
    (["privacy"],                        "!privacy — privacy settings",     "player"),
    (["missions", "quest"],              "!missions — daily missions",      "player"),
    (["weekly"],                         "!weekly — weekly missions",       "player"),
    (["today", "progress"],              "!today / !progress — all progress","player"),
    (["events", "event schedule"],       "!events — event schedule",        "player"),
    (["eventstatus"],                    "!eventstatus — active event",     "player"),
    (["nextevent"],                      "!nextevent — next event",         "player"),
    (["season"],                         "!season — season points",         "player"),
    (["topseason"],                      "!topseason — season leaderboard", "player"),
    (["tele", "teleport"],               "!tele list / !tele [spot]",       "player"),
    (["notif", "subscribe"],             "!notif / !subscribe — alerts",    "player"),
    (["start", "guide", "newbie"],       "!start / !guide — new player",    "player"),
    (["collection"],                     "!collection — collection book",   "player"),
    (["rarelog"],                        "!rarelog — rare finds log",       "player"),
    # Staff+
    (["modhelp", "mod help"],            "!modhelp — moderator guide",      "staff"),
    (["warn"],                           "!warn [user] [reason]",           "staff"),
    (["warnings"],                       "!warnings [user]",                "staff"),
    (["mute"],                           "!mute [user] [mins] [reason]",    "staff"),
    (["modlog"],                         "!modlog — recent mod log",        "staff"),
    # Admin+
    (["eventadmin"],                     "!eventadmin — event config",      "admin"),
    (["missionadmin"],                   "!missionadmin — mission config",  "admin"),
    (["safetyadmin"],                    "!safetyadmin — safety config",    "admin"),
    (["luxeadmin"],                      "!luxeadmin — Luxe config",        "admin"),
    (["vipadmin"],                       "!vipadmin — VIP config",          "admin"),
    (["adminhelp"],                      "!adminhelp — full admin help",    "admin"),
    (["currencycheck", "currency"],      "!currencycheck — currency scan",  "admin"),
    # Owner+
    (["ownerdash", "analytics"],         "!ownerdash — owner analytics",    "owner"),
    (["economydash"],                    "!economydash — economy dashboard","owner"),
    (["luxedash"],                       "!luxedash — Luxe analytics",      "owner"),
    (["audit"],                          "!audit [user] — player audit",    "owner"),
    (["commandissues"],                  "!commandissues — command check",  "owner"),
    (["launchcheck"],                    "!launchcheck — launch health",    "owner"),
]


# ── Per-command detail dict ───────────────────────────────────────────────────

_CMD_DETAIL: dict[str, tuple[str, str]] = {
    # (emoji + name, usage line + description)
    "mine":     ("⛏️ !mine",     "Usage: !mine\nMine once. Cooldown applies."),
    "automine": ("⛏️ !automine", "Usage: !automine | !automine luxe | !automine off\nTimer-based auto mining. Luxe uses 🎫 for longer sessions."),
    "automine luxe": ("⛏️ !automine luxe", "Usage: !automine luxe\nSpend 🎫 for extended auto-mine time."),
    "fish":     ("🎣 !fish",     "Usage: !fish\nFish once. Cooldown applies."),
    "autofish": ("🎣 !autofish", "Usage: !autofish | !autofish luxe | !autofish off\nTimer-based auto fishing. Luxe uses 🎫."),
    "balance":  ("💰 !balance",  "Usage: !balance (!bal)\nCheck your 🪙 ChillCoin balance."),
    "tickets":  ("🎫 !tickets",  "Usage: !tickets\nCheck your 🎫 Luxe Ticket balance."),
    "buycoins": ("🪙 !buycoins", "Usage: !buycoins [small|medium|large|max]\nConvert 🎫 Luxe Tickets to 🪙 ChillCoins."),
    "buyluxe":  ("🎫 !buyluxe",  "Usage: !buyluxe [#]\nBuy an item from !luxeshop by number."),
    "luxeshop": ("🎫 !luxeshop", "Usage: !luxeshop\nBrowse items purchasable with 🎫 Luxe Tickets."),
    "daily":    ("🎁 !daily",    "Usage: !daily\nClaim your daily 🪙 reward. Streak bonus applies."),
    "missions": ("📋 !missions", "Usage: !missions\nView today's daily missions and progress."),
    "weekly":   ("📋 !weekly",   "Usage: !weekly\nView weekly mission progress."),
    "today":    ("📈 !today",    "Usage: !today (!progress)\nSee all daily/weekly progress at once."),
    "vip":      ("👑 !vip",      "Usage: !vip | !vipperks | !buyvip [1d|7d|30d] | !myvip\nVIP gives auto-mine/fish time boosts."),
    "bet":      ("🃏 !bet",      "Usage: !bet [amount]\nJoin the blackjack table with your bet in 🪙."),
    "bjhelp":   ("🃏 !bjhelp",   "Usage: !bjhelp\nFull blackjack command reference."),
    "bjrules":  ("🃏 !bjrules",  "Usage: !bjrules\nBlackjack rules, payouts, and bonuses."),
    "bjshoe":   ("🃏 !bjshoe",   "Usage: !bjshoe (!shoe)\nCheck shoe status and cards remaining."),
    "bjstatus": ("🃏 !bjstatus", "Usage: !bjstatus\nCheck current blackjack game phase and players."),
    "poker":    ("♠️ !poker",    "Usage: !poker | !poker join [buyin]\nJoin or view the poker table."),
    "profile":  ("👤 !profile",  "Usage: !profile [!me]\nView your player profile."),
    "xp":       ("⭐ !xp",       "Usage: !xp (!rank)\nCheck your XP and level."),
    "shop":     ("🛍️ !shop",     "Usage: !shop | !shop badges | !shop titles\nBrowse the item shop."),
    "buy":      ("🛍️ !buy",      "Usage: !buy [#]\nBuy the item shown by that number in !shop."),
    "season":   ("🏆 !season",   "Usage: !season\nView your current season points and rank."),
    "events":   ("🎉 !events",   "Usage: !events\nView the event schedule and active event."),
    "tele":     ("🌀 !tele",     "Usage: !tele list | !tele [spot]\nTeleport to a room location."),
    "start":    ("🌱 !start",    "Usage: !start\nNew player quick-start guide."),
    "guide":    ("📘 !guide",    "Usage: !guide\nRoom guide for new players."),
    "orebook":  ("📖 !orebook",  "Usage: !orebook\nView your ore discovery book."),
    "fishbook": ("📖 !fishbook", "Usage: !fishbook\nView your fish discovery book."),
    "orelist":  ("📋 !orelist",  "Usage: !orelist [rarity]\nList all ores with chances and values."),
    "mineluck": ("🍀 !mineluck", "Usage: !mineluck\nView your current mining luck stack."),
    "fishluck": ("🍀 !fishluck", "Usage: !fishluck\nView your current fishing luck stack."),
    "coinflip": ("🪙 !coinflip", "Usage: !coinflip [heads|tails] [bet]\nFlip for double or nothing."),
    "send":     ("💸 !send",     "Usage: !send [user] [amount]\nSend 🪙 to another player."),
    "collection": ("📚 !collection", "Usage: !collection\nView your full ore and fish collection book."),
    "streak":   ("🔥 !streak",   "Usage: !streak\nCheck your daily claim streak status."),
    "myitems":  ("🎒 !myitems",  "Usage: !myitems\nView your owned shop items."),
}


def _can_see(perm: str, user: User) -> bool:
    if perm == "player":
        return True
    if perm == "staff":
        return can_moderate(user.username)
    if perm == "admin":
        return can_manage_economy(user.username)
    if perm == "owner":
        return is_owner(user.username)
    return False


# ── Public handlers ───────────────────────────────────────────────────────────

async def handle_commands(bot: BaseBot, user: User, args: list[str]) -> None:
    """!commands [category|search term|find term]"""
    sub = args[1].lower().strip() if len(args) > 1 else ""

    # ── No arg → main menu ───────────────────────────────────────────────────
    if not sub:
        await _w(bot, user.id, _MENU_1)
        await _w(bot, user.id, _MENU_2)
        if is_owner(user.username):
            await _w(bot, user.id,
                     "Owner: !commands owner\n"
                     "Admin: !commands admin\n"
                     "Audit: !commandissues  !launchcheck")
        elif can_manage_economy(user.username):
            await _w(bot, user.id, "Admin: !commands admin")
        elif can_moderate(user.username):
            await _w(bot, user.id, "Staff: !commands staff")
        return

    # ── Search ───────────────────────────────────────────────────────────────
    if sub in ("search", "find"):
        term = " ".join(args[2:]).lower().strip() if len(args) > 2 else ""
        await _handle_search(bot, user, term)
        return

    # If they write "!commands search term" in one shot already dispatched above.
    # But also allow "!commands <term>" as implicit search if no category matches.

    # ── Category blocks ───────────────────────────────────────────────────────
    if sub in ("staff",):
        if not can_moderate(user.username):
            await _w(bot, user.id, "🔒 Staff only.")
            return
        await _w(bot, user.id, _CAT["staff"])
        return

    if sub in ("admin",):
        if not can_manage_economy(user.username):
            await _w(bot, user.id, "🔒 Admin only.")
            return
        await _w(bot, user.id, _CAT["admin"])
        return

    if sub in ("owner",):
        if not is_owner(user.username):
            await _w(bot, user.id, "🔒 Owner only.")
            return
        await _w(bot, user.id, _CAT["owner"])
        return

    # Player categories
    cat_aliases = {
        "earn":       "earn",
        "mining":     "mining",
        "mine":       "mining",
        "fishing":    "fishing",
        "fish":       "fishing",
        "collection": "collection",
        "collect":    "collection",
        "games":      "games",
        "game":       "games",
        "casino":     "casino",
        "blackjack":  "casino",
        "bj":         "casino",
        "poker":      "casino",
        "economy":    "economy",
        "coins":      "economy",
        "money":      "economy",
        "shop":       "shop",
        "store":      "shop",
        "luxe":       "luxe",
        "tickets":    "luxe",
        "ticket":     "luxe",
        "profile":    "profile",
        "me":         "profile",
        "missions":   "missions",
        "mission":    "missions",
        "quests":     "missions",
        "events":     "events",
        "event":      "events",
        "seasons":    "seasons",
        "season":     "seasons",
        "help":       "help",
    }

    cat_key = cat_aliases.get(sub)
    if cat_key:
        txt = _CAT.get(cat_key, "")
        if txt:
            await _w(bot, user.id, txt)
            # Send extra page for mining/fishing
            extra = _CAT.get(cat_key + "2")
            if extra:
                await _w(bot, user.id, extra)
            return

    # Implicit search: treat the whole sub as a search term
    await _handle_search(bot, user, " ".join(args[1:]).lower().strip())


async def _handle_search(bot: BaseBot, user: User, term: str) -> None:
    if not term:
        await _w(bot, user.id,
                 "🔎 Usage: !commands search [term]\n"
                 "Example: !commands search mine")
        return

    hits: list[str] = []
    for triggers, line, perm in _SEARCHABLE:
        if not _can_see(perm, user):
            continue
        if any(term in t or t in term for t in triggers):
            hits.append(line)

    if not hits:
        await _w(bot, user.id,
                 f"🔎 No commands found for '{term[:20]}'.\n"
                 f"Try !commands for all categories.")
        return

    # Cap at 8 results to stay within message limits
    display = hits[:8]
    header  = f"🔎 Commands: {term[:18]}"
    lines   = [header] + display
    chunk   = ""
    for line in lines:
        candidate = (chunk + "\n" + line).lstrip("\n") if chunk else line
        if len(candidate) > 249:
            await bot.highrise.send_whisper(user.id, chunk[:249])
            chunk = line
        else:
            chunk = candidate
    if chunk:
        await bot.highrise.send_whisper(user.id, chunk[:249])


# ── Currency scanner ─────────────────────────────────────────────────────────

import ast
import os
import re

_BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SCAN_FILES = [
    "main.py",
    "modules/mining.py",
    "modules/fishing.py",
    "modules/economy.py",
    "modules/shop.py",
    "modules/onboarding.py",
    "modules/blackjack.py",
    "modules/realistic_blackjack.py",
    "modules/poker.py",
    "modules/profile.py",
    "modules/dj.py",
    "modules/room_utils.py",
    "modules/admin_cmds.py",
    "modules/badge_market.py",
    "modules/quests.py",
    "modules/achievements.py",
    "modules/events.py",
    "modules/seasons.py",
    "modules/audit.py",
    "modules/analytics.py",
]

_CURRENCY_PATS: list[tuple[str, re.Pattern]] = [
    ("c",       re.compile(r'\d{2,}c\b|\{[^}]+\}c\b')),   # "100c" or "{n}c"
    ("credits", re.compile(r'\bcredits?\b', re.IGNORECASE)),
    ("gems",    re.compile(r'\bgems\b', re.IGNORECASE)),
]


def _scan_currency_issues() -> list[tuple[str, str, str]]:
    """Scan string literals in player-facing files for old currency text.
    Returns list of (location, snippet, label).
    Uses AST so only real string literals are checked — no false positives
    from variable names, DB column names, or code comments.
    """
    results: list[tuple[str, str, str]] = []

    for rel_path in _SCAN_FILES:
        full_path = os.path.join(_BOT_DIR, rel_path)
        if not os.path.exists(full_path):
            continue
        try:
            with open(full_path, encoding="utf-8") as fh:
                source = fh.read()
            tree = ast.parse(source)
        except Exception:
            continue

        short = rel_path.replace("modules/", "").replace(".py", "")

        # Collect module-level docstring linenos to skip (not player-facing)
        _module_doc_lines: set[int] = set()
        if (tree.body
                and isinstance(tree.body[0], ast.Expr)
                and isinstance(tree.body[0].value, ast.Constant)):
            _module_doc_lines.add(tree.body[0].value.lineno)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant):
                continue
            val = node.value
            if not isinstance(node, ast.Constant) or not isinstance(val, str):
                continue
            if len(val) < 4:
                continue
            # Skip module-level docstrings (developer docs, not player-facing)
            if node.lineno in _module_doc_lines:
                continue
            # Skip identifier-like strings (setting keys, column names)
            if val.isidentifier():
                continue
            # Skip short strings with no spaces/newlines — likely keys
            if " " not in val and "\n" not in val and len(val) <= 20:
                continue

            for label, pat in _CURRENCY_PATS:
                m = pat.search(val)
                if m:
                    start = max(0, m.start() - 8)
                    raw = val[start:m.end() + 12].replace("\n", " ").strip()
                    snippet = raw[:35]
                    loc = f"{short}:{node.lineno}"
                    results.append((loc, snippet, label))
                    break  # one hit per string node

    return results


async def handle_currencycheck(bot: BaseBot, user: User, args: list[str]) -> None:
    """!currencycheck [details] — scan player-facing strings for old currency text.
    Owner/Admin only. Not shown in public help.
    """
    if not (is_owner(user.username) or can_manage_economy(user.username)):
        await _w(bot, user.id, "🔒 Owner/Admin only.")
        return

    sub = args[1].lower() if len(args) > 1 else ""

    try:
        findings = _scan_currency_issues()
    except Exception as e:
        await _w(bot, user.id, f"Scan error: {str(e)[:100]}")
        return

    counts: dict[str, int] = {"c": 0, "credits": 0, "gems": 0}
    for _loc, _snip, label in findings:
        counts[label] = counts.get(label, 0) + 1

    if sub == "details":
        if not findings:
            await _w(bot, user.id,
                     "🔎 Currency Issues\nNo old currency text found. ✅")
            return
        header = ["🔎 Currency Issues"]
        for i, (loc, snip, label) in enumerate(findings[:12], 1):
            header.append(f'{i}. {loc}: "{snip}"')
        chunk = ""
        for line in header:
            candidate = (chunk + "\n" + line).lstrip("\n") if chunk else line
            if len(candidate) > 220:
                await bot.highrise.send_whisper(user.id, chunk[:249])
                chunk = line
            else:
                chunk = candidate
        if chunk:
            await bot.highrise.send_whisper(user.id, chunk[:249])
        if len(findings) > 12:
            await _w(bot, user.id,
                     f"...and {len(findings) - 12} more.\n"
                     f"Fix with !currencycheck details 2 (coming soon).")
        # Quick-fix suggestions
        if counts["c"] > 0:
            await _w(bot, user.id,
                     "💡 Fix: Replace 100c → 100 🪙, {n}c → {n} 🪙")
        if counts["credits"] > 0:
            await _w(bot, user.id,
                     "💡 Fix: Replace credits → ChillCoins 🪙")
        if counts["gems"] > 0:
            await _w(bot, user.id,
                     "💡 Fix: Replace gems → 🎫 Luxe Tickets or remove")
        return

    # Summary view
    all_ok = all(v == 0 for v in counts.values())
    lines = ["💰 Currency Check"]
    lines.append("🪙 ChillCoins: OK")
    lines.append("🎫 Luxe Tickets: OK")
    lines.append(f'Old "c": {counts["c"]}')
    lines.append(f'Old "credits": {counts["credits"]}')
    lines.append(f'Old "gems": {counts["gems"]}')
    msg = "\n".join(lines)
    await _w(bot, user.id, msg)
    if not all_ok:
        await _w(bot, user.id,
                 "⚠️ Issues found.\nUse !currencycheck details to see them.")


async def handle_command_detail(bot: BaseBot, user: User, args: list[str]) -> None:
    """!command [cmd] — per-command detail."""
    if len(args) < 2:
        await _w(bot, user.id,
                 "Usage: !command [cmd]\nExample: !command automine")
        return

    # Normalise: strip ! prefix, lowercase
    key = " ".join(args[1:]).lower().lstrip("!").strip()

    entry = _CMD_DETAIL.get(key)
    if not entry:
        # Try single-word prefix match
        for k in _CMD_DETAIL:
            if k.startswith(key) or key.startswith(k.split()[0]):
                entry = _CMD_DETAIL[k]
                break

    if not entry:
        await _w(bot, user.id,
                 f"No detail for '!{key[:20]}'.\n"
                 f"Try !commands search {key[:20]}")
        return

    name, detail = entry
    msg = f"{name}\n{detail}"
    await _w(bot, user.id, msg)
