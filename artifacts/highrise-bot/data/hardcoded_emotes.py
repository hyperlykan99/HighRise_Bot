"""data/hardcoded_emotes.py
----------------------------
Two hardcoded catalogs. No SDK scan, no auto-prefixing, no guessed IDs.

BOT_SELF_EMOTES   — every emote bots can loop.
                    send_emote(emote_id)           — NO user_id

PLAYER_EMOTES     — subset players can trigger by name in chat.
                    send_emote(emote_id, user.id)  — WITH user_id

PLAYER_EMOTE_ALIASES — explicit trigger-name → emote_id overrides.
                    Applied on top of auto-resolution from BOT_SELF_EMOTES.
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
# BOT_SELF_EMOTES — all confirmed emotes bots can use
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
    ("Nervous",               "idle-nervous"),
    ("Toilet",                "idle-toilet"),
    ("UwU",                   "idle-uwu"),
    ("Scritchy",              "idle-wild"),
    ("Fighter Idle",          "idle-fighter"),
    ("Air Guitar",            "idle-guitar"),
    ("Singing",               "idle_singing"),
    ("TikTok Dance 4",        "idle-dance-tiktok4"),
    ("Tiktok7",               "idle-dance-tiktok7"),
    ("Casual Dance",          "idle-dance-casual"),
    ("Boogie Swing",          "idle-dance-swinging"),
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
    ("Attention",             "emote-attention"),
    ("Astronaut",             "emote-astronaut"),
    ("Heart Eyes",            "emote-hearteyes"),
    ("Swordfight",            "emote-swordfight"),
    ("TimeJump",              "emote-timejump"),
    ("Snake",                 "emote-snake"),
    ("Heart Fingers",         "emote-heartfingers"),
    ("Heart Shape",           "emote-heartshape"),
    ("Hug",                   "emote-hug"),
    ("Laugh Alt",             "emote-lagughing"),
    ("Embarrassed",           "emote-embarrassed"),
    ("Float",                 "emote-float"),
    ("Telekinesis",           "emote-telekinesis"),
    ("Puppet",                "emote-puppet"),
    ("Sleigh",                "emote-sleigh"),
    ("Maniac",                "emote-maniac"),
    ("Energy Ball",           "emote-energyball"),
    ("Frog",                  "emote-frog"),
    ("Superpose",             "emote-superpose"),
    ("Cute",                  "emote-cute"),
    ("Pose 7",                "emote-pose7"),
    ("Pose 8",                "emote-pose8"),
    ("Pose 1",                "emote-pose1"),
    ("Pose 3",                "emote-pose3"),
    ("Pose 5",                "emote-pose5"),
    ("Cutey",                 "emote-cutey"),
    ("Punk Guitar",           "emote-punkguitar"),
    ("Zombie Run",            "emote-zombierun"),
    ("Fashionista",           "emote-fashionista"),
    ("Gravity",               "emote-gravity"),
    ("Advanced Shy",          "emote-shy2"),
    ("Ice Skating",           "emote-iceskating"),
    ("SurpriseBig",           "emote-pose6"),
    ("Celebration Step",      "emote-celebrationstep"),
    ("Creepycute",            "emote-creepycute"),
    ("Frustrated",            "emote-frustrated"),
    ("Pose 10",               "emote-pose10"),
    ("Star Gazing",           "emote-stargaze"),
    ("Slap",                  "emote-slap"),
    ("Boxer",                 "emote-boxer"),
    ("Head Blowup",           "emote-headblowup"),
    ("KawaiiGoGo",            "emote-kawaiigogo"),
    ("Repose",                "emote-repose"),
    ("Shrink",                "emote-shrink"),
    ("Ditzy Pose",            "emote-pose9"),
    ("Teleporting",           "emote-teleporting"),
    ("This Is For You",       "emote-gift"),
    ("Hyped",                 "emote-hyped"),
    ("Hyped",                 "emote-hyped"),
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
    ("Ghost",                 "emoji-ghost"),
    ("Eyeroll",               "emoji-eyeroll"),
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
    ("Jinglebell",            "dance-jinglebell"),
    ("Dance Zombie",          "dance-zombie"),
    ("Sexy Dance",            "dance-sexy"),
    ("Penguin Dance",         "dance-pinguin"),
    ("Creepy Puppet",         "dance-creepypuppet"),
    ("Anime Dance",           "dance-anime"),
    ("Kawaii",                "dance-kawai"),
    ("TikTok Dance 9",        "dance-tiktok9"),
    ("Weird Dance",           "dance-weird"),
    ("TikTok Dance 10",       "dance-tiktok10"),
    ("Ice Cream Dance",       "dance-icecream"),
    ("Wrong Dance",           "dance-wrong"),
    ("Touch",                 "dance-touch"),
    ("Push It",               "dance-employee"),
    # ── Sit poses ─────────────────────────────────────────────────────────
    ("Relaxed Sit",           "sit-relaxed"),
    ("Laid Back",             "sit-open"),
    # ── New confirmed 2025 entries ─────────────────────────────────────────
    ("Sugar Stun",            "emote-outfit3"),
    ("Diva Moment",           "emote-threadexchange-posing"),
    ("Yoga Surprise",         "emote-yogaSurprise"),
    ("Spring Sun",            "sit-idle-springSun"),
    ("Silently Judging",      "emote-threadexchange-floating"),
    ("Spooky Swagger",        "emote-littlemonsters-dance"),
    ("Just Vibing",           "emote-jewelrise-vibing"),
    ("Storm Mood",            "emote-rainstruck-fail"),
    ("Storm Groove",          "emote-rainstruck-success"),
    ("Bloom Flutter",         "emote-bloomify-pose1"),
    ("Bloom Charm",           "emote-bloomify-pose2"),
    ("Bloom Radiance",        "emote-bloomify-pose3"),
    # ── Alias-resolved entries (IDs from PLAYER_EMOTE_ALIASES not above) ──
    ("Fairy Twirl",           "emote-looping"),
    ("Fairy Float",           "idle-floating"),
    ("Launch",                "emote-launch"),
    ("Cute Salute",           "emote-cutesalute"),
    ("At Attention",          "emote-salute"),
    ("Tiktok 11",             "dance-tiktok11"),
    ("Smooch",                "emote-kissing"),
    ("Party Time",            "emote-celebrate"),
    ("Hip Shake",             "dance-hipshake"),
    ("Fruity",                "dance-fruity"),
    ("Cheerleader",           "dance-cheerleader"),
    ("Magnetic",              "dance-tiktok14"),
    ("Nocturnal",             "idle-howl"),
    ("Trampoline",            "emote-trampoline"),
    ("Karma Dance",           "dance-wild"),
]

# Deduplicate BOT_SELF_EMOTES by emote_id (keep first occurrence)
_seen_bot_ids: set[str] = set()
_deduped: list[tuple[str, str]] = []
for _d, _e in BOT_SELF_EMOTES:
    if _e not in _seen_bot_ids:
        _seen_bot_ids.add(_e)
        _deduped.append((_d, _e))
BOT_SELF_EMOTES = _deduped

# ---------------------------------------------------------------------------
# Build internal BOT lookup: normalized key → emote_id
# Priority: first-occurrence wins across all key forms.
# ---------------------------------------------------------------------------
_BOT_LOOKUP: dict[str, str] = {}

for _display, _eid in BOT_SELF_EMOTES:
    _fk = _norm(_eid)                # full normalized ID
    if _fk not in _BOT_LOOKUP:
        _BOT_LOOKUP[_fk] = _eid
    _dk = _norm(_display)            # display name
    if _dk and _dk not in _BOT_LOOKUP:
        _BOT_LOOKUP[_dk] = _eid
    _sk = _norm(_strip_prefix(_eid)) # short stripped form
    if _sk and _sk not in _BOT_LOOKUP:
        _BOT_LOOKUP[_sk] = _eid

# ---------------------------------------------------------------------------
# PLAYER_EMOTE_ALIASES — explicit trigger-name → emote_id overrides.
# These names are what players type.  Applied on top of auto-resolution.
# ---------------------------------------------------------------------------
PLAYER_EMOTE_ALIASES: dict[str, str] = {
    "fairytwirl":       "emote-looping",
    "fairyfloat":       "idle-floating",
    "launch":           "emote-launch",
    "cutesalute":       "emote-cutesalute",
    "atattention":      "emote-salute",
    "tiktok":           "dance-tiktok11",
    "smooch":           "emote-kissing",
    "jingle":           "dance-jinglebell",
    "gottago":          "idle-toilet",
    "bitnervous":       "idle-nervous",
    "partytime":        "emote-celebrate",
    "arabesque":        "emote-pose10",
    "bashful":          "emote-shy2",
    "revelations":      "emote-headblowup",
    "watchyourback":    "emote-creepycute",
    "saunter":          "dance-anime",
    "sauntersway":      "dance-anime",
    "surprise":         "emote-peekaboo",
    "ditzy":            "emote-pose9",
    "sayso":            "idle-dance-tiktok4",
    "punk":             "emote-punkguitar",
    "zerogravity":      "emote-astronaut",
    "beautiful":        "emote-pose7",
    "wink":             "emote-pose1",
    "fightme":          "emote-pose3",
    "icon":             "emote-pose5",
    "viralgroove":      "dance-tiktok9",
    "shuffle":          "dance-tiktok10",
    "raise":            "emoji-celebrate",
    "savage":           "dance-tiktok8",
    "singalong":        "idle_singing",
    "shakehead":        "emote-no",
    "nod":              "emote-yes",
    "hipshake":         "dance-hipshake",
    "fruity":           "dance-fruity",
    "cheerleader":      "dance-cheerleader",
    "magnetic":         "dance-tiktok14",
    "nocturnal":        "idle-howl",
    "trampoline":       "emote-trampoline",
    "orangejuice":      "dance-orangejustice",
    "orangejuicedance": "dance-orangejustice",
    "splits":           "emote-splitsdrop",
    "ibelieve":         "emote-wings",
    "wiggledance":      "dance-sexy",
    "renegade":         "idle-dance-tiktok7",
    "sweetsmooch":      "emote-kissing",
    "karmadance":       "dance-wild",
}

# ---------------------------------------------------------------------------
# Names intentionally kept unresolved — do not map these.
# ---------------------------------------------------------------------------
_DO_NOT_MAP: frozenset[str] = frozenset({
    "foryou", "celebration", "omg", "miningfail", "fishingpull",
    "rough", "fishingidle", "dropped", "miningsuccess", "receivehappy",
    "fishingcast", "shuffledance", "receivesad", "moonlit",
})

# ---------------------------------------------------------------------------
# All player trigger names (what players type in chat)
# ---------------------------------------------------------------------------
_PLAYER_TRIGGER_NAMES: list[str] = [
    "fairytwirl", "fairyfloat", "launch", "cutesalute", "atattention",
    "tiktok", "smooch", "pushit", "foryou", "touch", "kawaii", "repose",
    "sleigh", "hyped", "jingle", "gottago", "timejump", "scritchy",
    "bitnervous", "iceskating", "partytime", "arabesque", "bashful",
    "revelations", "watchyourback", "creepypuppet", "saunter", "sauntersway",
    "surprise", "penguin", "boxer", "airguitar", "stargaze", "ditzy",
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
    "orangejuice", "orangejuicedance", "ringonit", "smoothwalk", "voguehands",
    "arrogance", "giveup", "fireball", "levitate", "lying", "naughty",
    "stinky", "pray", "punch", "sick", "smirk", "sneeze", "point",
    "collapse", "disco", "ghostfloat", "handstand", "superkick", "panic",
    "splits", "attentive", "relaxed", "fallingapart", "homerun", "boo",
    "bunnyhop", "revival", "faintdrop", "elbowbump", "fall", "clumsy",
    "faint", "hugyourself", "jetpack", "judochop", "jump", "amused",
    "levelup", "monsterfail", "nightfever", "ninjarun", "peace", "peekaboo",
    "proposing", "rainbow", "robot", "rofl", "roll", "ropepull",
    "secrethandshake", "sumofight", "superpunch", "superrun", "theatrical",
    "ibelieve", "irritated", "cozynap", "relaxing", "heropose", "ponder",
    "posh", "poutyface", "dab", "gangnamstyle", "sob", "taploop", "sleepy",
    "wiggledance", "eyeroll", "moonwalk", "fighter", "renegade", "facepalm",
    "feelthebeat", "happy", "hug", "slap", "clap", "exasperated",
    "sweetsmooch", "tapdance", "thumbsuck", "harlemshake", "heartfingers",
    "aerobics", "heartshape", "hearteyes", "karmadance", "gasp", "think",
    "stunned", "embarrassed", "blastoff", "annoyed", "dancezombie",
    "chillin", "frustrated", "bummed", "ghost", "mindblown", "zombierun",
    # new player emotes (5)
    "stormmood", "stormgroove", "bloomflutter", "bloomcharm", "bloomradiance",
]

# ---------------------------------------------------------------------------
# Build PLAYER_EMOTES: trigger_name → emote_id
# Step 1: auto-resolve from BOT_SELF_EMOTES lookup
# Step 2: apply explicit PLAYER_EMOTE_ALIASES (override/extend)
# DO_NOT_MAP names are skipped in both steps.
# ---------------------------------------------------------------------------
PLAYER_EMOTES: dict[str, str] = {}

# Step 1 — auto-resolve
for _name in _PLAYER_TRIGGER_NAMES:
    _key = _norm(_name)
    if _key in _DO_NOT_MAP:
        continue
    if _key in _BOT_LOOKUP:
        PLAYER_EMOTES[_key] = _BOT_LOOKUP[_key]

# Step 2 — explicit aliases override/extend
for _alias, _eid in PLAYER_EMOTE_ALIASES.items():
    _key = _norm(_alias)
    if _key in _DO_NOT_MAP:
        continue
    PLAYER_EMOTES[_key] = _eid   # always write — aliases are authoritative

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lookup_bot_emote(name: str) -> str | None:
    """Return the exact emote_id from BOT_SELF_EMOTES, or None."""
    return _BOT_LOOKUP.get(_norm(name))


def lookup_player_emote(name: str) -> str | None:
    """Return the exact emote_id from PLAYER_EMOTES, or None."""
    return PLAYER_EMOTES.get(_norm(name))


def get_bot_emote_list() -> list[tuple[str, str]]:
    """Return (display_name, emote_id) pairs for BOT_SELF_EMOTES."""
    return BOT_SELF_EMOTES


def get_player_trigger_names() -> list[str]:
    """Return sorted player trigger names (what players type) for display."""
    return sorted(PLAYER_EMOTES.keys())


def get_player_emote_list() -> list[tuple[str, str]]:
    """Return sorted (display_name, emote_id) pairs for PLAYER_EMOTES."""
    _eid_to_display: dict[str, str] = {}
    for disp, eid in BOT_SELF_EMOTES:
        _eid_to_display.setdefault(eid, disp)
    return sorted(
        ((_eid_to_display.get(eid, trigger), eid)
         for trigger, eid in PLAYER_EMOTES.items()),
        key=lambda t: t[0].lower(),
    )
