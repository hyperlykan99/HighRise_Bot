"""data/verified_working_emotes.py
-----------------------------------
Manually verified production emote catalog.

  VERIFIED_WORKING_EMOTES   — base list of canonical emote-* IDs (224 entries)
  WORKING_EMOTE_MAP         — normalized shortname → emote_id lookup
  get_verified_set()        — live set (base + runtime adds/removes)
  get_verified_list()       — sorted list from live set
  add_verified_emote(eid)   — persist a new addition
  remove_verified_emote(eid) — persist a removal

Runtime additions/removals via !addworkingemote / !removeworkingemote are
persisted in data/verified_overrides.json and applied on top of the base list.
"""
from __future__ import annotations
import json
import os
import re as _re

# ---------------------------------------------------------------------------
# Base list — 224 manually verified canonical emote IDs
# ---------------------------------------------------------------------------
VERIFIED_WORKING_EMOTES: list[str] = [
    "emote-dance-aerobics",
    "emote-airguitar",
    "emote-amused",
    "emote-angry",
    "emote-annoyed",
    "emote-arabesque",
    "emote-arrogance",
    "emote-astronaut",
    "emote-atattention",
    "emote-attention",
    "emote-attentive",
    "emote-bashful",
    "emote-beautiful",
    "emote-bitnervous",
    "emote-blackpink",
    "emote-blastoff",
    "emote-boo",
    "emote-bow",
    "emote-boxer",
    "emote-bummed",
    "emote-bunnyhop",
    "emote-casual",
    "emote-celebration",
    "emote-charging",
    "emote-cheerleader",
    "emote-chillin",
    "emote-clap",
    "emote-clumsy",
    "emote-cold",
    "emote-collapse",
    "emote-confused",
    "emote-cozynap",
    "emote-creepypuppet",
    "emote-curtsy",
    "emote-cute",
    "emote-cutesalute",
    "emote-cutey",
    "emote-dab",
    "emote-dancezombie",
    "emote-disco",
    "emote-ditzy",
    "emote-dontstartnow",
    "emote-dropped",
    "emote-duckwalk",
    "emote-elbowbump",
    "emote-embarrassed",
    "emote-energyball",
    "emote-enthused",
    "emote-exasperated",
    "emote-eyeroll",
    "emote-facepalm",
    "emote-faint",
    "emote-faintdrop",
    "emote-fairyfloat",
    "emote-fairytwirl",
    "emote-fall",
    "emote-fallingapart",
    "emote-fashion",
    "emote-feelthebeat",
    "emote-fighter",
    "emote-fightme",
    "emote-fireball",
    "emote-fishingcast",
    "emote-fishingidle",
    "emote-fishingpull",
    "emote-float",
    "emote-foryou",
    "emote-frog",
    "emote-fruity",
    "emote-frustrated",
    "emote-gagging",
    "emote-gangnam",
    "emote-gasp",
    "emote-ghost",
    "emote-ghostfloat",
    "emote-giveup",
    "emote-gottago",
    "emote-greedy",
    "emote-handsintheair",
    "emote-handstand",
    "emote-happy",
    "emote-harlemshake",
    "emote-hearteyes",
    "emote-heartfingers",
    "emote-heartshape",
    "emote-hello",
    "emote-heropose",
    "emote-hipshake",
    "emote-homerun",
    "emote-hot",
    "emote-hug",
    "emote-hugyourself",
    "emote-hyped",
    "emote-ibelieve",
    "emote-icecream",
    "emote-iceskating",
    "emote-icon",
    "emote-idle_sitfloor",
    "emote-irritated",
    "emote-jetpack",
    "emote-jingle",
    "emote-judochop",
    "emote-jump",
    "emote-karmadance",
    "emote-kawaii",
    "emote-kiss",
    "emote-laidback",
    "emote-laughing",
    "emote-launch",
    "emote-letsgoshopping",
    "emote-levelup",
    "emote-levitate",
    "emote-lying",
    "emote-macarena",
    "emote-magnetic",
    "emote-maniac",
    "emote-mindblown",
    "emote-miningfail",
    "emote-miningsuccess",
    "emote-model",
    "emote-monsterfail",
    "emote-moonlit",
    "emote-moonwalk",
    "emote-naughty",
    "emote-nightfever",
    "emote-ninjarun",
    "emote-nocturnal",
    "emote-nod",
    "emote-omg",
    "emote-orangejuice",
    "emote-panic",
    "emote-partytime",
    "emote-peace",
    "emote-peekaboo",
    "emote-penguin",
    "emote-pennywise",
    "emote-point",
    "emote-ponder",
    "emote-posh",
    "emote-poutyface",
    "emote-pray",
    "emote-proposing",
    "emote-punch",
    "emote-punk",
    "emote-puppet",
    "emote-pushit",
    "emote-pushups",
    "emote-rainbow",
    "emote-raise",
    "emote-receivehappy",
    "emote-receivesad",
    "emote-relaxed",
    "emote-relaxing",
    "emote-renegade",
    "emote-repose",
    "emote-revelations",
    "emote-revival",
    "emote-ringonit",
    "emote-robot",
    "emote-rockout",
    "emote-rofl",
    "emote-roll",
    "emote-ropepull",
    "emote-rough",
    "emote-russian",
    "emote-sad",
    "emote-saunter",
    "emote-savage",
    "emote-sayso",
    "emote-scritchy",
    "emote-secrethandshake",
    "emote-shakehead",
    "emote-shrink",
    "emote-shuffle",
    "emote-shuffledance",
    "emote-shy",
    "emote-sick",
    "emote-singalong",
    "emote-slap",
    "emote-sleepy",
    "emote-sleigh",
    "emote-smirk",
    "emote-smoothwalk",
    "emote-sneeze",
    "emote-snowangel",
    "emote-snowball",
    "emote-sob",
    "emote-splits",
    "emote-stargaze",
    "emote-stinky",
    "emote-stunned",
    "emote-sumofight",
    "emote-superkick",
    "emote-superpunch",
    "emote-superrun",
    "emote-surprise",
    "emote-sweetsmooch",
    "emote-swordfight",
    "emote-tapdance",
    "emote-taploop",
    "emote-telekinesis",
    "emote-teleporting",
    "emote-theatrical",
    "emote-think",
    "emote-thumbsuck",
    "emote-thumbsup",
    "emote-tiktok",
    "emote-timejump",
    "emote-tired",
    "emote-touch",
    "emote-trampoline",
    "emote-uwu",
    "emote-viralgroove",
    "emote-voguehands",
    "emote-watchyourback",
    "emote-wave",
    "emote-weird",
    "emote-wiggledance",
    "emote-wink",
    "emote-worm",
    "emote-wrong",
    "emote-zerogravity",
    "emote-zombie",
    "emote-zombierun",
]

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
# Live mutable set — base + runtime overrides, built once at import
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
    """Return the live set of verified canonical emote IDs."""
    return _VERIFIED_SET


def get_verified_list() -> list[str]:
    """Return a sorted list of verified canonical emote IDs."""
    return sorted(_VERIFIED_SET)


def add_verified_emote(eid: str) -> bool:
    """Add eid to the verified set and persist. Returns True if newly added."""
    if eid in _VERIFIED_SET:
        return False
    _VERIFIED_SET.add(eid)
    _overrides_added.add(eid)
    _overrides_removed.discard(eid)
    _save_overrides(_overrides_added, _overrides_removed)
    return True


def remove_verified_emote(eid: str) -> bool:
    """Remove eid from the verified set and persist. Returns True if it was present."""
    if eid not in _VERIFIED_SET:
        return False
    _VERIFIED_SET.discard(eid)
    _overrides_removed.add(eid)
    _overrides_added.discard(eid)
    _save_overrides(_overrides_added, _overrides_removed)
    return True


# ---------------------------------------------------------------------------
# Alias overrides — explicit name → emote_id mappings that TAKE PRIORITY
# over the auto-generated WORKING_EMOTE_MAP.
# Add entries here when the correct SDK ID differs from "emote-<shortname>".
# ---------------------------------------------------------------------------
_ALIAS_OVERRIDES: dict[str, str] = {
    "aerobics":      "emote-dance-aerobics",   # correct SDK ID
    "danceaerobics": "emote-dance-aerobics",   # alternate spelling
}

# ---------------------------------------------------------------------------
# WORKING_EMOTE_MAP — normalized shortname / full-id → canonical emote_id
# Resolution priority:
#   1. _ALIAS_OVERRIDES (explicit corrections, applied last to always win)
#   2. auto-generated from VERIFIED_WORKING_EMOTES
# ---------------------------------------------------------------------------
def _norm(s: str) -> str:
    return _re.sub(r"[^a-z0-9]", "", s.lower().replace("\u2019", ""))


WORKING_EMOTE_MAP: dict[str, str] = {}
for _eid in VERIFIED_WORKING_EMOTES:
    _short = _eid.removeprefix("emote-")
    WORKING_EMOTE_MAP[_norm(_short)] = _eid   # e.g. "gangnam" → "emote-gangnam"
    WORKING_EMOTE_MAP[_norm(_eid)]   = _eid   # e.g. "emotegangnam" → "emote-gangnam"
# Alias overrides win over all auto-generated entries
WORKING_EMOTE_MAP.update(_ALIAS_OVERRIDES)
