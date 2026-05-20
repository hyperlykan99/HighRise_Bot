"""data/hardcoded_emotes.py
----------------------------
Two hardcoded catalogs — no invented IDs, no SDK scan, no auto-prefix logic.

BOT_SELF_EMOTES   — used for bot self-emotes (!botemote).
                    Sent with:  await bot.highrise.send_emote(emote_id)
                    (no user_id — bot animates itself)

PLAYER_EMOTES     — resolved subset used for player chat triggers.
                    Sent with:  await bot.highrise.send_emote(emote_id, user.id)

UNRESOLVED_PLAYER_EMOTES — PLAYER_EMOTE_NAMES entries that could not map
                    to any ID in BOT_SELF_EMOTES.  Shown by !unresolvedplayeremotes.
"""
from __future__ import annotations
import re as _re

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
_PREFIXES = (
    "idle-loop-", "idle-dance-", "idle_", "idle-",
    "dance-", "emoji-", "emote-", "sit-",
)


def _norm(s: str) -> str:
    """Lowercase, strip all non-alphanumeric chars (incl. curly apostrophes)."""
    return _re.sub(r"[^a-z0-9]", "", s.lower().replace("\u2019", ""))


def _strip_prefix(eid: str) -> str:
    """Strip longest matching prefix to get a short searchable form."""
    for p in _PREFIXES:
        if eid.startswith(p):
            return eid[len(p):]
    return eid


# ---------------------------------------------------------------------------
# BOT_SELF_EMOTES — 219 confirmed Highrise SDK entries
# Format: (display_name, exact_emote_id)
# ---------------------------------------------------------------------------
BOT_SELF_EMOTES: list[tuple[str, str]] = [
    # ── Idle / loop poses ─────────────────────────────────────────────────
    ("Rest",                  "sit-idle-cute"),
    ("Zombie",                "idle_zombie"),
    ("Relaxed",               "idle_layingdown2"),
    ("Attentive",             "idle_layingdown"),
    ("Sleepy",                "idle-sleep"),
    ("Pouty Face",            "idle-sad"),
    ("Posh",                  "idle-posh"),
    ("Sleepy Tired",          "idle-loop-tired"),
    ("Tap Loop",              "idle-loop-tapdance"),
    ("Sit",                   "idle-loop-sitfloor"),
    ("Shy Loop",              "idle-loop-shy"),
    ("Bummed",                "idle-loop-sad"),
    ("Chillin",               "idle-loop-happy"),
    ("Annoyed",               "idle-loop-annoyed"),
    ("Aerobics",              "idle-loop-aerobics"),
    ("Ponder",                "idle-lookup"),
    ("Hero Pose",             "idle-hero"),
    ("Relaxing",              "idle-floorsleeping2"),
    ("Cozy Nap",              "idle-floorsleeping"),
    ("Enthused",              "idle-enthusiastic"),
    ("Boogie Swing",          "idle-dance-swinging"),
    ("Feel The Beat",         "idle-dance-headbobbing"),
    ("Irritated",             "idle-angry"),
    # ── Emote actions ─────────────────────────────────────────────────────
    ("Yes",                   "emote-yes"),
    ("I Believe I Can Fly",   "emote-wings"),
    ("The Wave",              "emote-wave"),
    ("Tired",                 "emote-tired"),
    ("Think",                 "emote-think"),
    ("Theatrical",            "emote-theatrical"),
    ("Tap Dance",             "emote-tapdance"),
    ("Super Run",             "emote-superrun"),
    ("Super Punch",           "emote-superpunch"),
    ("Sumo Fight",            "emote-sumo"),
    ("Thumb Suck",            "emote-suckthumb"),
    ("Splits Drop",           "emote-splitsdrop"),
    ("Snowball Fight",        "emote-snowball"),
    ("Snow Angel",            "emote-snowangel"),
    ("Shy",                   "emote-shy"),
    ("Secret Handshake",      "emote-secrethandshake"),
    ("Sad",                   "emote-sad"),
    ("Rope Pull",             "emote-ropepull"),
    ("Roll",                  "emote-roll"),
    ("ROFL",                  "emote-rofl"),
    ("Robot",                 "emote-robot"),
    ("Rainbow",               "emote-rainbow"),
    ("Proposing",             "emote-proposing"),
    ("Peekaboo",              "emote-peekaboo"),
    ("Peace",                 "emote-peace"),
    ("Panic",                 "emote-panic"),
    ("No",                    "emote-no"),
    ("Ninja Run",             "emote-ninjarun"),
    ("Night Fever",           "emote-nightfever"),
    ("Monster Fail",          "emote-monster_fail"),
    ("Model",                 "emote-model"),
    ("Flirty Wave",           "emote-lust"),
    ("Level Up",              "emote-levelup"),
    ("Amused",                "emote-laughing2"),
    ("Laugh",                 "emote-laughing"),
    ("Kiss",                  "emote-kiss"),
    ("Super Kick",            "emote-kicking"),
    ("Jump",                  "emote-jumpb"),
    ("Judo Chop",             "emote-judochop"),
    ("Imaginary Jetpack",     "emote-jetpack"),
    ("Hug Yourself",          "emote-hugyourself"),
    ("Sweating",              "emote-hot"),
    ("Hero Entrance",         "emote-hero"),
    ("Hello",                 "emote-hello"),
    ("Headball",              "emote-headball"),
    ("Harlem Shake",          "emote-harlemshake"),
    ("Happy",                 "emote-happy"),
    ("Handstand",             "emote-handstand"),
    ("Greedy Emote",          "emote-greedy"),
    ("Graceful",              "emote-graceful"),
    ("Moonwalk",              "emote-gordonshuffle"),
    ("Ghost Float",           "emote-ghost-idle"),
    ("Gangnam Style",         "emote-gangnam"),
    ("Frolic",                "emote-frollicking"),
    ("Faint",                 "emote-fainting"),
    ("Clumsy",                "emote-fail2"),
    ("Fall",                  "emote-fail1"),
    ("Face Palm",             "emote-exasperatedb"),
    ("Exasperated",           "emote-exasperated"),
    ("Elbow Bump",            "emote-elbowbump"),
    ("Disco",                 "emote-disco"),
    ("Blast Off",             "emote-disappear"),
    ("Faint Drop",            "emote-deathdrop"),
    ("Collapse",              "emote-death2"),
    ("Revival",               "emote-death"),
    ("Dab",                   "emote-dab"),
    ("Curtsy",                "emote-curtsy"),
    ("Confusion",             "emote-confused"),
    ("Cold",                  "emote-cold"),
    ("Charging",              "emote-charging"),
    ("Bunny Hop",             "emote-bunnyhop"),
    ("Bow",                   "emote-bow"),
    ("Boo",                   "emote-boo"),
    ("Home Run",              "emote-baseball"),
    ("Falling Apart",         "emote-apart"),
    # ── Emoji ─────────────────────────────────────────────────────────────
    ("Thumbs Up",             "emoji-thumbsup"),
    ("Point",                 "emoji-there"),
    ("Sneeze",                "emoji-sneeze"),
    ("Smirk",                 "emoji-smirking"),
    ("Sick",                  "emoji-sick"),
    ("Gasp",                  "emoji-scared"),
    ("Punch",                 "emoji-punch"),
    ("Pray",                  "emoji-pray"),
    ("Stinky",                "emoji-poop"),
    ("Naughty",               "emoji-naughty"),
    ("Mind Blown",            "emoji-mind-blown"),
    ("Lying",                 "emoji-lying"),
    ("Levitate",              "emoji-halo"),
    ("Fireball Lunge",        "emoji-hadoken"),
    ("Give Up",               "emoji-give-up"),
    ("Tummy Ache",            "emoji-gagging"),
    ("Flex",                  "emoji-flex"),
    ("Stunned",               "emoji-dizzy"),
    ("Cursing Emote",         "emoji-cursing"),
    ("Sob",                   "emoji-crying"),
    ("Clap",                  "emoji-clapping"),
    ("Raise The Roof",        "emoji-celebrate"),
    ("Arrogance",             "emoji-arrogance"),
    ("Angry",                 "emoji-angry"),
    # ── Dance ─────────────────────────────────────────────────────────────
    ("Vogue Hands",           "dance-voguehands"),
    ("Savage Dance",          "dance-tiktok8"),
    ("Dont Start Now",        "dance-tiktok2"),
    ("Yoga Flow",             "dance-spiritual"),
    ("Smoothwalk",            "dance-smoothwalk"),
    ("Ring on It",            "dance-singleladies"),
    ("Lets Go Shopping",      "dance-shoppingcart"),
    ("Russian Dance",         "dance-russian"),
    ("Robotic",               "dance-robotic"),
    ("Pennys Dance",          "dance-pennywise"),
    ("Orange Juice Dance",    "dance-orangejustice"),
    ("Rock Out",              "dance-metal"),
    ("Karate",                "dance-martial-artist"),
    ("Macarena",              "dance-macarena"),
    ("Hands in the Air",      "dance-handsup"),
    ("Floss",                 "dance-floss"),
    ("Duck Walk",             "dance-duckwalk"),
    ("Breakdance",            "dance-breakdance"),
    ("K Pop Dance",           "dance-blackpink"),
    ("Push Ups",              "dance-aerobics"),
    ("Hyped",                 "emote-hyped"),
    ("Jinglebell",            "dance-jinglebell"),
    ("Nervous",               "idle-nervous"),
    ("Toilet",                "idle-toilet"),
    ("Attention",             "emote-attention"),
    ("Astronaut",             "emote-astronaut"),
    ("Dance Zombie",          "dance-zombie"),
    ("Ghost",                 "emoji-ghost"),
    ("Heart Eyes",            "emote-hearteyes"),
    ("Swordfight",            "emote-swordfight"),
    ("TimeJump",              "emote-timejump"),
    ("Snake",                 "emote-snake"),
    ("Heart Fingers",         "emote-heartfingers"),
    ("Heart Shape",           "emote-heartshape"),
    ("Hug",                   "emote-hug"),
    ("Laugh Alt",             "emote-lagughing"),
    ("Eyeroll",               "emoji-eyeroll"),
    ("Embarrassed",           "emote-embarrassed"),
    ("Float",                 "emote-float"),
    ("Telekinesis",           "emote-telekinesis"),
    ("Sexy Dance",            "dance-sexy"),
    ("Puppet",                "emote-puppet"),
    ("Fighter Idle",          "idle-fighter"),
    ("Penguin Dance",         "dance-pinguin"),
    ("Creepy Puppet",         "dance-creepypuppet"),
    ("Sleigh",                "emote-sleigh"),
    ("Maniac",                "emote-maniac"),
    ("Energy Ball",           "emote-energyball"),
    ("Singing",               "idle_singing"),
    ("Frog",                  "emote-frog"),
    ("Superpose",             "emote-superpose"),
    ("Cute",                  "emote-cute"),
    ("TikTok Dance 9",        "dance-tiktok9"),
    ("Weird Dance",           "dance-weird"),
    ("TikTok Dance 10",       "dance-tiktok10"),
    ("Pose 7",                "emote-pose7"),
    ("Pose 8",                "emote-pose8"),
    ("Casual Dance",          "idle-dance-casual"),
    ("Pose 1",                "emote-pose1"),
    ("Pose 3",                "emote-pose3"),
    ("Pose 5",                "emote-pose5"),
    ("Cutey",                 "emote-cutey"),
    ("Punk Guitar",           "emote-punkguitar"),
    ("Zombie Run",            "emote-zombierun"),
    ("Fashionista",           "emote-fashionista"),
    ("Gravity",               "emote-gravity"),
    ("Ice Cream Dance",       "dance-icecream"),
    ("Wrong Dance",           "dance-wrong"),
    ("UwU",                   "idle-uwu"),
    ("TikTok Dance 4",        "idle-dance-tiktok4"),
    ("Advanced Shy",          "emote-shy2"),
    ("Anime Dance",           "dance-anime"),
    ("Kawaii",                "dance-kawai"),
    ("Scritchy",              "idle-wild"),
    ("Ice Skating",           "emote-iceskating"),
    ("SurpriseBig",           "emote-pose6"),
    ("Celebration Step",      "emote-celebrationstep"),
    ("Creepycute",            "emote-creepycute"),
    ("Frustrated",            "emote-frustrated"),
    ("Pose 10",               "emote-pose10"),
    ("Relaxed Sit",           "sit-relaxed"),
    ("Laid Back",             "sit-open"),
    ("Star Gazing",           "emote-stargaze"),
    ("Slap",                  "emote-slap"),
    ("Boxer",                 "emote-boxer"),
    ("Head Blowup",           "emote-headblowup"),
    ("KawaiiGoGo",            "emote-kawaiigogo"),
    ("Repose",                "emote-repose"),
    ("Tiktok7",               "idle-dance-tiktok7"),
    ("Shrink",                "emote-shrink"),
    ("Ditzy Pose",            "emote-pose9"),
    ("Teleporting",           "emote-teleporting"),
    ("Touch",                 "dance-touch"),
    ("Air Guitar",            "idle-guitar"),
    ("This Is For You",       "emote-gift"),
    ("Push It",               "dance-employee"),
]

# ---------------------------------------------------------------------------
# Build internal lookup: normalized key → emote_id
# Priority: display name first-occurrence > short stripped form
# (first write wins — preserves list ordering for ambiguous names)
# ---------------------------------------------------------------------------
_BOT_LOOKUP: dict[str, str] = {}

for _display, _eid in BOT_SELF_EMOTES:
    # Full normalized ID (e.g. "emotewave" → "emote-wave")
    _fk = _norm(_eid)
    if _fk not in _BOT_LOOKUP:
        _BOT_LOOKUP[_fk] = _eid
    # Display name (e.g. "heartfingers" → "emote-heartfingers")
    _dk = _norm(_display)
    if _dk and _dk not in _BOT_LOOKUP:
        _BOT_LOOKUP[_dk] = _eid
    # Short stripped form (e.g. "wave" from "emote-wave")
    _sk = _norm(_strip_prefix(_eid))
    if _sk and _sk not in _BOT_LOOKUP:
        _BOT_LOOKUP[_sk] = _eid


# ---------------------------------------------------------------------------
# PLAYER_EMOTE_NAMES — names players can type in chat
# Resolved against BOT_SELF_EMOTES at import time.
# ---------------------------------------------------------------------------
PLAYER_EMOTE_NAMES: list[str] = [
    "fairytwirl", "fairyfloat", "launch", "cutesalute", "atattention",
    "tiktok", "smooch", "pushit", "foryou", "touch", "kawaii", "repose",
    "sleigh", "hyped", "jingle", "gottago", "timejump", "scritchy",
    "bitnervous", "iceskating", "partytime", "arabesque", "bashful",
    "revelations", "watchyourback", "creepypuppet", "saunter", "surprise",
    "celebration", "penguin", "boxer", "airguitar", "stargaze", "ditzy",
    "uwu", "wrong", "fashion", "icecream", "sayso", "zombie", "astronaut",
    "punk", "zerogravity", "beautiful", "omg", "casual", "wink", "fightme",
    "icon", "cute", "cutey", "greedy", "viralgroove", "weird", "shuffle",
    "gagging", "raise", "savage", "blackpink", "model", "dontstartnow",
    "pennywise", "bow", "russian", "curtsy", "snowball", "hot", "snowangel",
    "charging", "letsgoshopping", "confused", "enthused", "telekinesis",
    "float", "teleporting", "swordfight", "maniac", "energyball", "worm",
    "singalong", "frog", "macarena", "kiss", "shakehead", "sad", "nod",
    "laughing", "hello", "thumbsup", "miningfail", "shy", "fishingpull",
    "thewave", "angry", "rough", "fishingidle", "dropped", "miningsuccess",
    "receivehappy", "cold", "fishingcast", "sit", "shuffledance",
    "receivesad", "tired", "hipshake", "fruity", "cheerleader", "magnetic",
    "nocturnal", "moonlit", "trampoline", "attention", "laidback", "shrink",
    "puppet", "pushups", "duckwalk", "handsintheair", "rockout",
    "orangejuice", "ringonit", "smoothwalk", "voguehands", "arrogance",
    "giveup", "fireball", "levitate", "lying", "naughty", "stinky", "pray",
    "punch", "sick", "smirk", "sneeze", "point", "collapse", "disco",
    "ghostfloat", "handstand", "superkick", "panic", "splits", "attentive",
    "relaxed", "fallingapart", "homerun", "boo", "bunnyhop", "revival",
    "faintdrop", "elbowbump", "fall", "clumsy", "faint", "hugyourself",
    "jetpack", "judochop", "jump", "amused", "levelup", "monsterfail",
    "nightfever", "ninjarun", "peace", "peekaboo", "proposing", "rainbow",
    "robot", "rofl", "roll", "ropepull", "secrethandshake", "sumofight",
    "superpunch", "superrun", "theatrical", "ibelieve", "irritated",
    "cozynap", "relaxing", "heropose", "ponder", "posh", "poutyface",
    "dab", "gangnamstyle", "sob", "taploop", "sleepy", "wiggledance",
    "eyeroll", "moonwalk", "fighter", "renegade", "facepalm",
    "feelthebeat", "happy", "hug", "slap", "clap", "exasperated",
    "sweetsmooch", "tapdance", "thumbsuck", "harlemshake", "heartfingers",
    "aerobics", "heartshape", "hearteyes", "karmadance", "gasp", "think",
    "stunned", "embarrassed", "blastoff", "annoyed", "dancezombie",
    "chillin", "frustrated", "bummed", "ghost", "mindblown", "zombierun",
]

# Resolve each player emote name against the bot lookup
PLAYER_EMOTES: dict[str, str] = {}        # normalized_name → emote_id
UNRESOLVED_PLAYER_EMOTES: list[str] = []  # names with no matching ID

for _name in PLAYER_EMOTE_NAMES:
    _key = _norm(_name)
    if _key in _BOT_LOOKUP:
        PLAYER_EMOTES[_key] = _BOT_LOOKUP[_key]
    else:
        UNRESOLVED_PLAYER_EMOTES.append(_name)

# ---------------------------------------------------------------------------
# Public lookup API
# ---------------------------------------------------------------------------

def lookup_bot_emote(name: str) -> str | None:
    """Return the exact emote_id for a bot self-emote name, or None."""
    return _BOT_LOOKUP.get(_norm(name))


def lookup_player_emote(name: str) -> str | None:
    """Return the exact emote_id for a player emote name, or None."""
    return PLAYER_EMOTES.get(_norm(name))


def get_bot_emote_list() -> list[tuple[str, str]]:
    """Return the full (display_name, emote_id) list for pagination."""
    return BOT_SELF_EMOTES


def get_player_emote_list() -> list[tuple[str, str]]:
    """Return resolved (display_name, emote_id) pairs, sorted by display name."""
    # Build display name from the bot list for resolved entries
    _eid_to_display: dict[str, str] = {}
    for disp, eid in BOT_SELF_EMOTES:
        _eid_to_display.setdefault(eid, disp)
    return sorted(
        (((_eid_to_display.get(eid, name)), eid)
         for name, eid in PLAYER_EMOTES.items()),
        key=lambda t: t[0].lower(),
    )
