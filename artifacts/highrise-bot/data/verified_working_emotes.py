"""data/verified_working_emotes.py
-----------------------------------
Confirmed emote catalog — source of truth is ALL_EMOTES below.

  ALL_EMOTES                — full (display_name, emote_id) pairs (220 entries)
  VERIFIED_WORKING_EMOTES   — flat list of all confirmed IDs
  WORKING_EMOTE_MAP         — normalized key → emote_id lookup
  _ALIAS_OVERRIDES          — explicit name → emote_id corrections (highest priority)
  get_verified_set()        — live set (base + runtime adds/removes)
  get_verified_list()       — sorted list from live set
  add_verified_emote(eid)   — persist a new addition
  remove_verified_emote(eid) — persist a removal

IDs come from the confirmed Highrise SDK list — NOT invented.
Prefixes in use: emote-  emoji-  dance-  idle-  idle_  sit-
"""
from __future__ import annotations
import json
import os
import re as _re

# ---------------------------------------------------------------------------
# Source of truth — 220 confirmed (display_name, emote_id) pairs
# ---------------------------------------------------------------------------
ALL_EMOTES: list[tuple[str, str]] = [
    # ── Idle / sitting ────────────────────────────────────────────────────
    ("Rest",                  "sit-idle-cute"),
    ("Zombie",                "idle_zombie"),
    ("Relaxed",               "idle_layingdown2"),
    ("Attentive",             "idle_layingdown"),
    ("Sleepy",                "idle-sleep"),
    ("Pouty Face",            "idle-sad"),
    ("Posh",                  "idle-posh"),
    ("Tired",                 "idle-loop-tired"),
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
    ("UwU",                   "idle-uwu"),
    ("Nervous",               "idle-nervous"),
    ("Toilet",                "idle-toilet"),
    ("Singing",               "idle_singing"),
    ("Fighter Idle",          "idle-fighter"),
    ("Casual Dance",          "idle-dance-casual"),
    ("TikTok Dance 4",        "idle-dance-tiktok4"),
    ("TikTok 7",              "idle-dance-tiktok7"),
    ("Scritchy",              "idle-wild"),
    ("Air Guitar",            "idle-guitar"),
    # ── Emotes (emote- prefix) ────────────────────────────────────────────
    ("Yes",                   "emote-yes"),
    ("I Believe I Can Fly",   "emote-wings"),
    ("The Wave",              "emote-wave"),
    ("Sleepy",                "emote-tired"),
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
    ("Laugh Alt",             "emote-lagughing"),
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
    ("Greedy",                "emote-greedy"),
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
    ("Hyped",                 "emote-hyped"),
    ("Attention",             "emote-attention"),
    ("Astronaut",             "emote-astronaut"),
    ("Heart Eyes",            "emote-hearteyes"),
    ("Swordfight",            "emote-swordfight"),
    ("Time Jump",             "emote-timejump"),
    ("Snake",                 "emote-snake"),
    ("Heart Fingers",         "emote-heartfingers"),
    ("Heart Shape",           "emote-heartshape"),
    ("Hug",                   "emote-hug"),
    ("Eyeroll",               "emote-eyeroll"),
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
    ("Surprise Big",          "emote-pose6"),
    ("Celebration Step",      "emote-celebrationstep"),
    ("Creepy Cute",           "emote-creepycute"),
    ("Frustrated",            "emote-frustrated"),
    ("Pose 10",               "emote-pose10"),
    ("Star Gazing",           "emote-stargaze"),
    ("Slap",                  "emote-slap"),
    ("Boxer",                 "emote-boxer"),
    ("Head Blowup",           "emote-headblowup"),
    ("Kawaii Go Go",          "emote-kawaiigogo"),
    ("Repose",                "emote-repose"),
    ("Shrink",                "emote-shrink"),
    ("Ditzy Pose",            "emote-pose9"),
    ("Teleporting",           "emote-teleporting"),
    ("This Is For You",       "emote-gift"),
    # ── Emoji (emoji- prefix) ─────────────────────────────────────────────
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
    ("Cursing",               "emoji-cursing"),
    ("Sob",                   "emoji-crying"),
    ("Clap",                  "emoji-clapping"),
    ("Raise The Roof",        "emoji-celebrate"),
    ("Arrogance",             "emoji-arrogance"),
    ("Angry",                 "emoji-angry"),
    ("Ghost",                 "emoji-ghost"),
    ("Eyeroll",               "emoji-eyeroll"),
    # ── Dance (dance- prefix) ─────────────────────────────────────────────
    ("Vogue Hands",           "dance-voguehands"),
    ("Savage Dance",          "dance-tiktok8"),
    ("Don't Start Now",       "dance-tiktok2"),
    ("Yoga Flow",             "dance-spiritual"),
    ("Smoothwalk",            "dance-smoothwalk"),
    ("Ring On It",            "dance-singleladies"),
    ("Let's Go Shopping",     "dance-shoppingcart"),
    ("Russian Dance",         "dance-russian"),
    ("Robotic",               "dance-robotic"),
    ("Penny's Dance",         "dance-pennywise"),
    ("Orange Juice Dance",    "dance-orangejustice"),
    ("Rock Out",              "dance-metal"),
    ("Karate",                "dance-martial-artist"),
    ("Macarena",              "dance-macarena"),
    ("Hands In The Air",      "dance-handsup"),
    ("Floss",                 "dance-floss"),
    ("Duck Walk",             "dance-duckwalk"),
    ("Breakdance",            "dance-breakdance"),
    ("K-Pop Dance",           "dance-blackpink"),
    ("Push Ups",              "dance-aerobics"),
    ("Jinglebell",            "dance-jinglebell"),
    ("Dance Zombie",          "dance-zombie"),
    ("Sexy Dance",            "dance-sexy"),
    ("Penguin Dance",         "dance-pinguin"),
    ("Creepy Puppet",         "dance-creepypuppet"),
    ("TikTok Dance 9",        "dance-tiktok9"),
    ("Weird Dance",           "dance-weird"),
    ("TikTok Dance 10",       "dance-tiktok10"),
    ("Ice Cream Dance",       "dance-icecream"),
    ("Wrong Dance",           "dance-wrong"),
    ("Anime Dance",           "dance-anime"),
    ("Kawaii Dance",          "dance-kawai"),
    ("Touch",                 "dance-touch"),
    ("Push It",               "dance-employee"),
    # ── Sit poses (sit- prefix) ───────────────────────────────────────────
    ("Relaxed Sit",           "sit-relaxed"),
    ("Laid Back",             "sit-open"),
]

# ---------------------------------------------------------------------------
# Flat ID list — all confirmed emote IDs
# ---------------------------------------------------------------------------
VERIFIED_WORKING_EMOTES: list[str] = [eid for _, eid in ALL_EMOTES]

# ---------------------------------------------------------------------------
# Runtime override persistence
# ---------------------------------------------------------------------------
_OVERRIDES_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "verified_overrides.json")
)


def _load_overrides() -> tuple[set[str], set[str]]:
    try:
        data = json.loads(open(_OVERRIDES_PATH).read())
        return set(data.get("added", [])), set(data.get("removed", []))
    except Exception:
        return set(), set()


def _save_overrides(added: set[str], removed: set[str]) -> None:
    try:
        with open(_OVERRIDES_PATH, "w") as f:
            json.dump(
                {"added": sorted(added), "removed": sorted(removed)},
                f, indent=2,
            )
    except Exception as exc:
        print(f"[VERIFIED_EMOTES] save error: {exc}")


# ---------------------------------------------------------------------------
# Live mutable set — base + runtime overrides
# ---------------------------------------------------------------------------
_overrides_added:   set[str] = set()
_overrides_removed: set[str] = set()
_VERIFIED_SET: set[str] = set(VERIFIED_WORKING_EMOTES)


def _rebuild_set() -> None:
    global _overrides_added, _overrides_removed
    _overrides_added, _overrides_removed = _load_overrides()
    _VERIFIED_SET.clear()
    _VERIFIED_SET.update(VERIFIED_WORKING_EMOTES)
    _VERIFIED_SET.update(_overrides_added)
    _VERIFIED_SET.difference_update(_overrides_removed)


_rebuild_set()


def get_verified_set() -> set[str]:
    return _VERIFIED_SET


def get_verified_list() -> list[str]:
    return sorted(_VERIFIED_SET)


def add_verified_emote(eid: str) -> bool:
    if eid in _VERIFIED_SET:
        return False
    _VERIFIED_SET.add(eid)
    _overrides_added.add(eid)
    _overrides_removed.discard(eid)
    _save_overrides(_overrides_added, _overrides_removed)
    return True


def remove_verified_emote(eid: str) -> bool:
    if eid not in _VERIFIED_SET:
        return False
    _VERIFIED_SET.discard(eid)
    _overrides_removed.add(eid)
    _overrides_added.discard(eid)
    _save_overrides(_overrides_added, _overrides_removed)
    return True


# ---------------------------------------------------------------------------
# Normalization helper
# ---------------------------------------------------------------------------
def _norm(s: str) -> str:
    return _re.sub(r"[^a-z0-9]", "", s.lower().replace("\u2019", ""))


def _strip_prefix(eid: str) -> str:
    """Strip the longest matching common prefix to get a searchable short form."""
    for prefix in (
        "idle-loop-", "idle-dance-", "idle_", "idle-",
        "dance-", "emoji-", "emote-", "sit-",
    ):
        if eid.startswith(prefix):
            return eid[len(prefix):]
    return eid


# ---------------------------------------------------------------------------
# Alias overrides — highest priority in resolve_emote_full().
# Keys are already normalized (lowercase, alphanumeric only).
# Add entries where the short ID or display name is ambiguous or non-obvious.
# ---------------------------------------------------------------------------
_ALIAS_OVERRIDES: dict[str, str] = {
    # ── Duplicate display names — pick the preferred canonical ────────────
    "laugh":           "emote-laughing",       # over emote-lagughing (typo ID)
    "relaxed":         "sit-relaxed",          # over idle_layingdown2
    "sleepy":          "idle-sleep",           # over idle-loop-tired
    "shy":             "emote-shy",            # over idle-loop-shy
    # ── IDs where display name ≠ short ID (non-obvious mappings) ──────────
    "moonwalk":        "emote-gordonshuffle",  # display "Moonwalk"
    "gordonshuffle":   "emote-gordonshuffle",
    "facepalm":        "emote-exasperatedb",   # display "Face Palm"
    "blastoff":        "emote-disappear",      # display "Blast Off"
    "faintdrop":       "emote-deathdrop",      # display "Faint Drop"
    "collapse":        "emote-death2",         # display "Collapse"
    "revival":         "emote-death",          # display "Revival"
    "clumsy":          "emote-fail2",          # display "Clumsy"
    "fall":            "emote-fail1",          # display "Fall"
    "frolic":          "emote-frollicking",    # display "Frolic"
    "frollicking":     "emote-frollicking",
    "amused":          "emote-laughing2",      # display "Amused"
    "jump":            "emote-jumpb",          # display "Jump"
    "superkick":       "emote-kicking",        # display "Super Kick"
    "flirtywave":      "emote-lust",           # display "Flirty Wave"
    "homerun":         "emote-baseball",       # display "Home Run"
    "fallingapart":    "emote-apart",          # display "Falling Apart"
    "ghostfloat":      "emote-ghost-idle",
    "surprisebig":     "emote-pose6",
    "kawaiigogo":      "emote-kawaiigogo",
    "headblowup":      "emote-headblowup",
    # ── Emoji IDs with non-obvious short forms ────────────────────────────
    "point":           "emoji-there",          # "there" ≠ "point"
    "thumbsup":        "emoji-thumbsup",
    "clap":            "emoji-clapping",       # "clapping" ≠ "clap"
    "raise":           "emoji-celebrate",      # display "Raise The Roof"
    "levitate":        "emoji-halo",           # "halo" ≠ "levitate"
    "fireball":        "emoji-hadoken",        # "hadoken" ≠ "fireball"
    "fireballlunge":   "emoji-hadoken",
    "mindblown":       "emoji-mind-blown",     # hyphenated
    "giveup":          "emoji-give-up",        # hyphenated
    "sob":             "emoji-crying",         # "crying" ≠ "sob"
    "stinky":          "emoji-poop",           # "poop" ≠ "stinky"
    "gasp":            "emoji-scared",         # "scared" ≠ "gasp"
    "tummyache":       "emoji-gagging",
    "stunned":         "emoji-dizzy",          # "dizzy" ≠ "stunned"
    "cursing":         "emoji-cursing",
    "ghost":           "emoji-ghost",          # prefer ghost emoji over ghost-idle
    # ── Dance IDs with non-obvious mappings ───────────────────────────────
    "dontstartnow":    "dance-tiktok2",
    "savage":          "dance-tiktok8",
    "savadance":       "dance-tiktok8",
    "tiktok":          "dance-tiktok8",        # default tiktok = savage dance
    "tiktok2":         "dance-tiktok2",
    "tiktok9":         "dance-tiktok9",
    "tiktok10":        "dance-tiktok10",
    "yoga":            "dance-spiritual",
    "ringonit":        "dance-singleladies",
    "shopping":        "dance-shoppingcart",
    "letsgoshopping":  "dance-shoppingcart",
    "russian":         "dance-russian",
    "penguin":         "dance-pinguin",        # "pinguin" typo in ID
    "penguindance":    "dance-pinguin",
    "orangejuice":     "dance-orangejustice",  # ID has "justice" not "juice"
    "rockout":         "dance-metal",          # "metal" ≠ "rockout"
    "metal":           "dance-metal",
    "karate":          "dance-martial-artist", # hyphenated
    "martialartist":   "dance-martial-artist",
    "voguehands":      "dance-voguehands",
    "vogue":           "dance-voguehands",
    "kpop":            "dance-blackpink",      # "blackpink" ≠ "kpop"
    "pushit":          "dance-employee",       # "employee" ≠ "pushit"
    "kawaii":          "dance-kawai",          # "kawai" typo in ID
    "kawaiidance":     "dance-kawai",
    "sexy":            "dance-sexy",
    "sexydance":       "dance-sexy",
    "creepypuppet":    "dance-creepypuppet",
    "weirdance":       "dance-weird",
    "wrongdance":      "dance-wrong",
    "icecreamdance":   "dance-icecream",
    "animedance":      "dance-anime",
    "dancezombie":     "dance-zombie",
    # ── Idle IDs with non-obvious mappings ────────────────────────────────
    "aerobics":        "idle-loop-aerobics",   # main aerobics idle
    "pushups":         "dance-aerobics",       # push ups = dance-aerobics
    "sitfloor":        "idle-loop-sitfloor",
    "sit":             "idle-loop-sitfloor",
    "chillin":         "idle-loop-happy",
    "boogie":          "idle-dance-swinging",
    "boogieswing":     "idle-dance-swinging",
    "headbob":         "idle-dance-headbobbing",
    "feelthebeat":     "idle-dance-headbobbing",
    "zombie":          "idle_zombie",
    "airguitar":       "idle-guitar",
    "uwu":             "idle-uwu",
    "posh":            "idle-posh",
    "nervous":         "idle-nervous",
    "toilet":          "idle-toilet",
    "scritchy":        "idle-wild",
    "wild":            "idle-wild",
    "singing":         "idle_singing",
    "fighteridle":     "idle-fighter",
    "casualdance":     "idle-dance-casual",
    "tiktok4":         "idle-dance-tiktok4",
    "tiktok7":         "idle-dance-tiktok7",
    # ── Sit poses ─────────────────────────────────────────────────────────
    "rest":            "sit-idle-cute",
    "laidback":        "sit-open",
    "relaxedsit":      "sit-relaxed",
}

# ---------------------------------------------------------------------------
# WORKING_EMOTE_MAP — normalized key → emote_id
#
# Resolution:
#   1. Full ID  (e.g., "emotewave"        → "emote-wave")
#   2. Short ID  (e.g., "wave"            → "emote-wave")
#   3. Display name — first occurrence wins for duplicate names
#   4. _ALIAS_OVERRIDES applied last — always wins
# ---------------------------------------------------------------------------
WORKING_EMOTE_MAP: dict[str, str] = {}
_seen_display_keys: set[str] = set()

for _display, _eid in ALL_EMOTES:
    # Full normalized ID → eid  (e.g., "emotewave" → "emote-wave")
    _full_key = _norm(_eid)
    if _full_key not in WORKING_EMOTE_MAP:
        WORKING_EMOTE_MAP[_full_key] = _eid

    # Prefix-stripped short form → eid  (e.g., "wave" → "emote-wave")
    _short_key = _norm(_strip_prefix(_eid))
    if _short_key and _short_key not in WORKING_EMOTE_MAP:
        WORKING_EMOTE_MAP[_short_key] = _eid

    # Display name → eid (first occurrence wins for duplicates)
    _disp_key = _norm(_display)
    if _disp_key and _disp_key not in _seen_display_keys:
        WORKING_EMOTE_MAP[_disp_key] = _eid
        _seen_display_keys.add(_disp_key)

# Apply alias overrides last — these always win
WORKING_EMOTE_MAP.update({_norm(k): v for k, v in _ALIAS_OVERRIDES.items()})
