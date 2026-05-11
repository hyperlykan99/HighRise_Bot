"""
modules/fishing.py
------------------
Fishing Bot system for Highrise (MasterAngler / fisher bot mode).

Fish are auto-credited on catch (coins added immediately).
Catches are recorded in fish_catch_records for stats and leaderboards.
Fish profile tracks FXP, level, total catches, best catch, equipped rod.
AutoFish: per-user asyncio tasks with session limits and room-leave detection.
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone, timedelta

from highrise import BaseBot, User

import database as db
from modules.big_announce import send_big_fish_announce
from modules.first_find   import check_race_win
from modules.permissions import can_manage_economy, can_moderate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


def _is_in_room(username: str) -> bool:
    try:
        from modules.gold import _room_cache
        return username.lower() in _room_cache
    except Exception:
        return True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seconds_since(iso_ts: str | None) -> float:
    if not iso_ts:
        return 9999.0
    try:
        t = datetime.fromisoformat(iso_ts)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds()
    except Exception:
        return 9999.0


def _fish_level_threshold(level: int) -> int:
    """FXP needed to reach the NEXT level from this level."""
    return level * level * 200


def _fmt(n: int) -> str:
    return f"{n:,}"


# ---------------------------------------------------------------------------
# Fish Rarities
# ---------------------------------------------------------------------------

FISH_RARITIES: dict[str, dict] = {
    "common":    {"label": "<#AAAAAA>[COMMON]<#FFFFFF>",    "color": "#AAAAAA", "announce": False, "order": 0},
    "rare":      {"label": "<#3399FF>[RARE]<#FFFFFF>",      "color": "#3399FF", "announce": False, "order": 1},
    "epic":      {"label": "<#B266FF>[EPIC]<#FFFFFF>",      "color": "#B266FF", "announce": False, "order": 2},
    "legendary": {"label": "<#FFD700>[LEGENDARY]<#FFFFFF>", "color": "#FFD700", "announce": True,  "order": 3},
    "mythic":    {"label": "<#FF66CC>[MYTHIC]<#FFFFFF>",    "color": "#FF66CC", "announce": True,  "order": 4},
    "prismatic": {
        "label": ("<#FF0000>[P<#FF9900>R<#FFFF00>I<#00FF00>S"
                  "<#00CCFF>M<#3366FF>A<#9933FF>T<#FF66CC>I<#FF0000>C]<#FFFFFF>"),
        "color": "#FF66CC", "announce": True, "order": 5,
    },
    "exotic":    {"label": "<#FF0000>[EXOTIC]<#FFFFFF>",    "color": "#FF0000", "announce": True,  "order": 6},
}

RARITY_ORDER = ["common", "rare", "epic", "legendary", "mythic", "prismatic", "exotic"]

_RARITY_ALIASES: dict[str, str] = {
    "c": "common",    "common": "common",
    "r": "rare",      "rare": "rare",
    "e": "epic",      "epic": "epic",
    "l": "legendary", "leg": "legendary",  "legendary": "legendary",
    "m": "mythic",    "mythic": "mythic",
    "p": "prismatic", "prism": "prismatic", "prismatic": "prismatic",
    "x": "exotic",    "exotic": "exotic",
}


def _rarity_label(rarity: str) -> str:
    return FISH_RARITIES.get(rarity, FISH_RARITIES["common"])["label"]


def _name_colored(rarity: str, name: str) -> str:
    if rarity == "prismatic":
        return f"<#FF66CC>{name}<#FFFFFF>"
    c = FISH_RARITIES.get(rarity, {}).get("color")
    if c and rarity not in ("common", "rare"):
        return f"<{c}>{name}<#FFFFFF>"
    return name


# ---------------------------------------------------------------------------
# Fish Catalog  (68 fish across 7 rarities)
# ---------------------------------------------------------------------------

FISH_CATALOG: list[dict] = [
    # ── COMMON (drop_weight 55 each) ──────────────────────────────────────
    {"fish_id": "minnow",        "name": "Minnow",        "emoji": "🐟", "rarity": "common",    "base_value": 10,     "base_fxp": 5,    "min_weight": 0.1,  "max_weight": 2.0,   "drop_weight": 55,   "announce_default": False},
    {"fish_id": "sardine",       "name": "Sardine",       "emoji": "🐠", "rarity": "common",    "base_value": 15,     "base_fxp": 5,    "min_weight": 0.1,  "max_weight": 2.0,   "drop_weight": 55,   "announce_default": False},
    {"fish_id": "tilapia",       "name": "Tilapia",       "emoji": "🐡", "rarity": "common",    "base_value": 20,     "base_fxp": 6,    "min_weight": 0.2,  "max_weight": 3.0,   "drop_weight": 55,   "announce_default": False},
    {"fish_id": "carp",          "name": "Carp",          "emoji": "🐟", "rarity": "common",    "base_value": 18,     "base_fxp": 5,    "min_weight": 0.3,  "max_weight": 4.0,   "drop_weight": 55,   "announce_default": False},
    {"fish_id": "anchovy",       "name": "Anchovy",       "emoji": "🐟", "rarity": "common",    "base_value": 12,     "base_fxp": 4,    "min_weight": 0.1,  "max_weight": 1.5,   "drop_weight": 55,   "announce_default": False},
    {"fish_id": "mackerel",      "name": "Mackerel",      "emoji": "🐠", "rarity": "common",    "base_value": 22,     "base_fxp": 6,    "min_weight": 0.2,  "max_weight": 2.5,   "drop_weight": 55,   "announce_default": False},
    {"fish_id": "pond_perch",    "name": "Pond Perch",    "emoji": "🐟", "rarity": "common",    "base_value": 16,     "base_fxp": 5,    "min_weight": 0.2,  "max_weight": 2.0,   "drop_weight": 55,   "announce_default": False},
    {"fish_id": "mudfish",       "name": "Mudfish",       "emoji": "🐡", "rarity": "common",    "base_value": 14,     "base_fxp": 4,    "min_weight": 0.3,  "max_weight": 3.0,   "drop_weight": 55,   "announce_default": False},
    {"fish_id": "river_shrimp",  "name": "River Shrimp",  "emoji": "🦐", "rarity": "common",    "base_value": 10,     "base_fxp": 3,    "min_weight": 0.05, "max_weight": 0.5,   "drop_weight": 55,   "announce_default": False},
    {"fish_id": "small_crab",    "name": "Small Crab",    "emoji": "🦀", "rarity": "common",    "base_value": 25,     "base_fxp": 6,    "min_weight": 0.1,  "max_weight": 1.0,   "drop_weight": 55,   "announce_default": False},
    # ── RARE (drop_weight 2.0 each) — ~1 in 29 ────────────────────────────────────────
    {"fish_id": "bluegill",      "name": "Bluegill",      "emoji": "🐟", "rarity": "rare",      "base_value": 80,     "base_fxp": 15,   "min_weight": 0.3,  "max_weight": 3.0,   "drop_weight": 2.0,   "announce_default": False},
    {"fish_id": "salmon",        "name": "Salmon",        "emoji": "🐟", "rarity": "rare",      "base_value": 120,    "base_fxp": 20,   "min_weight": 1.0,  "max_weight": 8.0,   "drop_weight": 2.0,   "announce_default": False},
    {"fish_id": "tuna",          "name": "Tuna",          "emoji": "🐠", "rarity": "rare",      "base_value": 150,    "base_fxp": 22,   "min_weight": 2.0,  "max_weight": 12.0,  "drop_weight": 2.0,   "announce_default": False},
    {"fish_id": "catfish",       "name": "Catfish",       "emoji": "🐱", "rarity": "rare",      "base_value": 100,    "base_fxp": 18,   "min_weight": 1.0,  "max_weight": 10.0,  "drop_weight": 2.0,   "announce_default": False},
    {"fish_id": "red_snapper",   "name": "Red Snapper",   "emoji": "🐟", "rarity": "rare",      "base_value": 130,    "base_fxp": 20,   "min_weight": 0.5,  "max_weight": 5.0,   "drop_weight": 2.0,   "announce_default": False},
    {"fish_id": "sea_bass",      "name": "Sea Bass",      "emoji": "🐠", "rarity": "rare",      "base_value": 140,    "base_fxp": 22,   "min_weight": 0.5,  "max_weight": 6.0,   "drop_weight": 2.0,   "announce_default": False},
    {"fish_id": "clownfish",     "name": "Clownfish",     "emoji": "🐠", "rarity": "rare",      "base_value": 200,    "base_fxp": 25,   "min_weight": 0.2,  "max_weight": 1.5,   "drop_weight": 2.0,   "announce_default": False},
    {"fish_id": "lobster",       "name": "Lobster",       "emoji": "🦞", "rarity": "rare",      "base_value": 250,    "base_fxp": 30,   "min_weight": 0.5,  "max_weight": 4.0,   "drop_weight": 2.0,   "announce_default": False},
    {"fish_id": "stingray",      "name": "Stingray",      "emoji": "🌊", "rarity": "rare",      "base_value": 300,    "base_fxp": 30,   "min_weight": 2.0,  "max_weight": 20.0,  "drop_weight": 2.0,   "announce_default": False},
    {"fish_id": "silver_trout",  "name": "Silver Trout",  "emoji": "🐟", "rarity": "rare",      "base_value": 180,    "base_fxp": 25,   "min_weight": 0.5,  "max_weight": 5.0,   "drop_weight": 2.0,   "announce_default": False},
    # ── EPIC (drop_weight 0.2 each) — ~1 in 286 ────────────────────────────────────────
    {"fish_id": "swordfish",        "name": "Swordfish",       "emoji": "⚔️",  "rarity": "epic",      "base_value": 800,    "base_fxp": 50,   "min_weight": 5.0,  "max_weight": 30.0,  "drop_weight": 0.2,   "announce_default": False},
    {"fish_id": "golden_koi",       "name": "Golden Koi",      "emoji": "🟡",  "rarity": "epic",      "base_value": 1200,   "base_fxp": 60,   "min_weight": 1.0,  "max_weight": 8.0,   "drop_weight": 0.2,   "announce_default": False},
    {"fish_id": "electric_eel",     "name": "Electric Eel",    "emoji": "⚡",  "rarity": "epic",      "base_value": 900,    "base_fxp": 55,   "min_weight": 1.0,  "max_weight": 10.0,  "drop_weight": 0.2,   "announce_default": False},
    {"fish_id": "giant_squid",      "name": "Giant Squid",     "emoji": "🦑",  "rarity": "epic",      "base_value": 1500,   "base_fxp": 65,   "min_weight": 5.0,  "max_weight": 40.0,  "drop_weight": 0.2,   "announce_default": False},
    {"fish_id": "puffer_king",      "name": "Puffer King",     "emoji": "🐡",  "rarity": "epic",      "base_value": 1000,   "base_fxp": 55,   "min_weight": 0.5,  "max_weight": 5.0,   "drop_weight": 0.2,   "announce_default": False},
    {"fish_id": "crystal_trout",    "name": "Crystal Trout",   "emoji": "💎",  "rarity": "epic",      "base_value": 2000,   "base_fxp": 70,   "min_weight": 1.0,  "max_weight": 8.0,   "drop_weight": 0.2,   "announce_default": False},
    {"fish_id": "firefin_snapper",  "name": "Firefin Snapper", "emoji": "🔥",  "rarity": "epic",      "base_value": 1800,   "base_fxp": 65,   "min_weight": 2.0,  "max_weight": 15.0,  "drop_weight": 0.2,   "announce_default": False},
    {"fish_id": "icefin_tuna",      "name": "Icefin Tuna",     "emoji": "❄️",  "rarity": "epic",      "base_value": 1600,   "base_fxp": 65,   "min_weight": 3.0,  "max_weight": 18.0,  "drop_weight": 0.2,   "announce_default": False},
    {"fish_id": "shadow_carp",      "name": "Shadow Carp",     "emoji": "🌑",  "rarity": "epic",      "base_value": 1400,   "base_fxp": 60,   "min_weight": 2.0,  "max_weight": 12.0,  "drop_weight": 0.2,   "announce_default": False},
    {"fish_id": "storm_eel",        "name": "Storm Eel",       "emoji": "⚡",  "rarity": "epic",      "base_value": 1700,   "base_fxp": 65,   "min_weight": 2.0,  "max_weight": 15.0,  "drop_weight": 0.2,   "announce_default": False},
    # ── LEGENDARY (drop_weight 0.03 each) — ~1 in 1,907 ────────────────────────────────────
    {"fish_id": "dragonfish",       "name": "Dragonfish",      "emoji": "🐉",  "rarity": "legendary", "base_value": 8000,   "base_fxp": 150,  "min_weight": 5.0,  "max_weight": 50.0,  "drop_weight": 0.03,    "announce_default": True},
    {"fish_id": "ancient_bass",     "name": "Ancient Bass",    "emoji": "🐟",  "rarity": "legendary", "base_value": 6000,   "base_fxp": 120,  "min_weight": 5.0,  "max_weight": 40.0,  "drop_weight": 0.03,    "announce_default": True},
    {"fish_id": "crystal_marlin",   "name": "Crystal Marlin",  "emoji": "💎",  "rarity": "legendary", "base_value": 12000,  "base_fxp": 180,  "min_weight": 10.0, "max_weight": 80.0,  "drop_weight": 0.03,    "announce_default": True},
    {"fish_id": "golden_shark",     "name": "Golden Shark",    "emoji": "🦈",  "rarity": "legendary", "base_value": 15000,  "base_fxp": 200,  "min_weight": 20.0, "max_weight": 100.0, "drop_weight": 0.03,    "announce_default": True},
    {"fish_id": "royal_koi",        "name": "Royal Koi",       "emoji": "👑",  "rarity": "legendary", "base_value": 10000,  "base_fxp": 160,  "min_weight": 3.0,  "max_weight": 20.0,  "drop_weight": 0.03,    "announce_default": True},
    {"fish_id": "thunder_tuna",     "name": "Thunder Tuna",    "emoji": "⚡",  "rarity": "legendary", "base_value": 11000,  "base_fxp": 170,  "min_weight": 10.0, "max_weight": 60.0,  "drop_weight": 0.03,    "announce_default": True},
    {"fish_id": "frostbite_marlin", "name": "Frostbite Marlin","emoji": "❄️",  "rarity": "legendary", "base_value": 13000,  "base_fxp": 185,  "min_weight": 10.0, "max_weight": 70.0,  "drop_weight": 0.03,    "announce_default": True},
    {"fish_id": "sunscale_fish",    "name": "Sunscale Fish",   "emoji": "☀️",  "rarity": "legendary", "base_value": 9000,   "base_fxp": 155,  "min_weight": 5.0,  "max_weight": 40.0,  "drop_weight": 0.03,    "announce_default": True},
    {"fish_id": "ocean_crownfish",  "name": "Ocean Crownfish", "emoji": "👑",  "rarity": "legendary", "base_value": 14000,  "base_fxp": 195,  "min_weight": 8.0,  "max_weight": 60.0,  "drop_weight": 0.03,    "announce_default": True},
    {"fish_id": "pearlback_whale",  "name": "Pearlback Whale", "emoji": "🐋",  "rarity": "legendary", "base_value": 20000,  "base_fxp": 220,  "min_weight": 50.0, "max_weight": 200.0, "drop_weight": 0.03,    "announce_default": True},
    # ── MYTHIC (drop_weight 0.003 each) — ~1 in 19,077 ─────────────────────────────────────
    {"fish_id": "kraken_fry",           "name": "Kraken Fry",           "emoji": "🦑", "rarity": "mythic", "base_value": 40000,  "base_fxp": 300,  "min_weight": 5.0,   "max_weight": 50.0,  "drop_weight": 0.003, "announce_default": True},
    {"fish_id": "moonlight_leviathan",  "name": "Moonlight Leviathan",  "emoji": "🌙", "rarity": "mythic", "base_value": 60000,  "base_fxp": 400,  "min_weight": 30.0,  "max_weight": 150.0, "drop_weight": 0.003, "announce_default": True},
    {"fish_id": "phantom_shark",        "name": "Phantom Shark",        "emoji": "🦈", "rarity": "mythic", "base_value": 50000,  "base_fxp": 350,  "min_weight": 20.0,  "max_weight": 100.0, "drop_weight": 0.003, "announce_default": True},
    {"fish_id": "abyssal_tuna",         "name": "Abyssal Tuna",         "emoji": "🌊", "rarity": "mythic", "base_value": 45000,  "base_fxp": 320,  "min_weight": 15.0,  "max_weight": 80.0,  "drop_weight": 0.003, "announce_default": True},
    {"fish_id": "celestial_koi",        "name": "Celestial Koi",        "emoji": "✨", "rarity": "mythic", "base_value": 55000,  "base_fxp": 380,  "min_weight": 5.0,   "max_weight": 30.0,  "drop_weight": 0.003, "announce_default": True},
    {"fish_id": "spirit_marlin",        "name": "Spirit Marlin",        "emoji": "👻", "rarity": "mythic", "base_value": 65000,  "base_fxp": 420,  "min_weight": 20.0,  "max_weight": 120.0, "drop_weight": 0.003, "announce_default": True},
    {"fish_id": "voidfin_eel",          "name": "Voidfin Eel",          "emoji": "🌌", "rarity": "mythic", "base_value": 48000,  "base_fxp": 340,  "min_weight": 10.0,  "max_weight": 60.0,  "drop_weight": 0.003, "announce_default": True},
    {"fish_id": "angel_whale",          "name": "Angel Whale",          "emoji": "🐋", "rarity": "mythic", "base_value": 70000,  "base_fxp": 450,  "min_weight": 80.0,  "max_weight": 300.0, "drop_weight": 0.003, "announce_default": True},
    {"fish_id": "demon_ray",            "name": "Demon Ray",            "emoji": "😈", "rarity": "mythic", "base_value": 58000,  "base_fxp": 390,  "min_weight": 15.0,  "max_weight": 80.0,  "drop_weight": 0.003, "announce_default": True},
    {"fish_id": "starborn_salmon",      "name": "Starborn Salmon",      "emoji": "⭐", "rarity": "mythic", "base_value": 52000,  "base_fxp": 360,  "min_weight": 8.0,   "max_weight": 40.0,  "drop_weight": 0.003, "announce_default": True},
    # ── PRISMATIC (drop_weight 0.0004 each) — ~1 in 178,853 ─────────────────────────────────
    {"fish_id": "rainbow_leviathan",       "name": "Rainbow Leviathan",       "emoji": "🌈", "rarity": "prismatic", "base_value": 200000, "base_fxp": 500,  "min_weight": 50.0,  "max_weight": 200.0, "drop_weight": 0.0004, "announce_default": True},
    {"fish_id": "aurora_koi",              "name": "Aurora Koi",              "emoji": "🌈", "rarity": "prismatic", "base_value": 180000, "base_fxp": 500,  "min_weight": 5.0,   "max_weight": 30.0,  "drop_weight": 0.0004, "announce_default": True},
    {"fish_id": "prismfin",               "name": "Prismfin",               "emoji": "🌈", "rarity": "prismatic", "base_value": 150000, "base_fxp": 500,  "min_weight": 10.0,  "max_weight": 60.0,  "drop_weight": 0.0004, "announce_default": True},
    {"fish_id": "spectrum_marlin",         "name": "Spectrum Marlin",         "emoji": "🌈", "rarity": "prismatic", "base_value": 220000, "base_fxp": 500,  "min_weight": 20.0,  "max_weight": 100.0, "drop_weight": 0.0004, "announce_default": True},
    {"fish_id": "chroma_shark",            "name": "Chroma Shark",            "emoji": "🌈", "rarity": "prismatic", "base_value": 250000, "base_fxp": 500,  "min_weight": 30.0,  "max_weight": 150.0, "drop_weight": 0.0004, "announce_default": True},
    {"fish_id": "aurora_whale",            "name": "Aurora Whale",            "emoji": "🌈", "rarity": "prismatic", "base_value": 300000, "base_fxp": 500,  "min_weight": 80.0,  "max_weight": 300.0, "drop_weight": 0.0004, "announce_default": True},
    {"fish_id": "opalfin_tuna",            "name": "Opalfin Tuna",            "emoji": "🌈", "rarity": "prismatic", "base_value": 160000, "base_fxp": 500,  "min_weight": 8.0,   "max_weight": 50.0,  "drop_weight": 0.0004, "announce_default": True},
    {"fish_id": "celestial_rainbow_fish",  "name": "Celestial Rainbow Fish",  "emoji": "🌈", "rarity": "prismatic", "base_value": 350000, "base_fxp": 500,  "min_weight": 5.0,   "max_weight": 25.0,  "drop_weight": 0.0004, "announce_default": True},
    # ── EXOTIC (drop_weight 0.000025 each) — ~1 in 2.86M ────────────────────────────────────
    {"fish_id": "bloodfin_leviathan",  "name": "Bloodfin Leviathan",  "emoji": "🚨", "rarity": "exotic", "base_value": 500000, "base_fxp": 1000, "min_weight": 80.0,  "max_weight": 400.0, "drop_weight": 0.000025, "announce_default": True},
    {"fish_id": "abyssal_king",        "name": "Abyssal King",        "emoji": "👑", "rarity": "exotic", "base_value": 450000, "base_fxp": 1000, "min_weight": 40.0,  "max_weight": 200.0, "drop_weight": 0.000025, "announce_default": True},
    {"fish_id": "hellscale_kraken",    "name": "Hellscale Kraken",    "emoji": "🔥", "rarity": "exotic", "base_value": 600000, "base_fxp": 1000, "min_weight": 60.0,  "max_weight": 300.0, "drop_weight": 0.000025, "announce_default": True},
    {"fish_id": "crimson_megalodon",   "name": "Crimson Megalodon",   "emoji": "🦈", "rarity": "exotic", "base_value": 700000, "base_fxp": 1000, "min_weight": 200.0, "max_weight": 800.0, "drop_weight": 0.000025, "announce_default": True},
    {"fish_id": "forbidden_whale",     "name": "Forbidden Whale",     "emoji": "🚨", "rarity": "exotic", "base_value": 550000, "base_fxp": 1000, "min_weight": 100.0, "max_weight": 500.0, "drop_weight": 0.000025, "announce_default": True},
    {"fish_id": "demonfin_shark",      "name": "Demonfin Shark",      "emoji": "😈", "rarity": "exotic", "base_value": 480000, "base_fxp": 1000, "min_weight": 50.0,  "max_weight": 250.0, "drop_weight": 0.000025, "announce_default": True},
    {"fish_id": "scarlet_leviathan",   "name": "Scarlet Leviathan",   "emoji": "🚨", "rarity": "exotic", "base_value": 620000, "base_fxp": 1000, "min_weight": 100.0, "max_weight": 500.0, "drop_weight": 0.000025, "announce_default": True},
    {"fish_id": "infernal_koi",        "name": "Infernal Koi",        "emoji": "🔥", "rarity": "exotic", "base_value": 400000, "base_fxp": 1000, "min_weight": 5.0,   "max_weight": 25.0,  "drop_weight": 0.000025, "announce_default": True},
]

_FISH_BY_ID:    dict[str, dict] = {f["fish_id"]:        f for f in FISH_CATALOG}
_FISH_BY_NAME:  dict[str, dict] = {f["name"].lower():   f for f in FISH_CATALOG}
_TOTAL_WEIGHT:  float            = sum(f["drop_weight"] for f in FISH_CATALOG)


def _norm_uname(name: str) -> str:
    """Remove @-prefix, strip whitespace, lowercase."""
    return name.lstrip("@").strip().lower()


def _resolve_forced_fish(
    user_id: str, username: str, rod_name: str, eff: dict
) -> tuple[dict | None, str]:
    """
    Check for a pending forced fish drop and apply it.

    Returns:
        (fish_dict, "")    — drop found & applied; drop marked used.
        (None, error_msg)  — drop found but failed; error stored on drop row.
        (None, "")         — no pending drop for this player.

    Username is normalized before lookup so '@'-prefix, casing, and
    extra whitespace never cause a miss.
    """
    norm = _norm_uname(username)
    try:
        forced = db.get_active_forced_fish_drop(user_id, norm)
        if not forced:
            return None, ""
        ft  = forced["forced_type"]
        fv  = forced["forced_value"].lower()
        fid = forced["id"]
        if ft == "rarity":
            pool = [f for f in FISH_CATALOG if f["rarity"].lower() == fv]
            if pool:
                db.mark_forced_fish_drop_used(fid)
                return random.choice(pool), ""
            err = f"No fish in catalog with rarity '{fv}'"
        elif ft == "fish":
            item = (
                _FISH_BY_ID.get(fv)
                or _FISH_BY_ID.get(fv.replace(" ", "_"))
                or _FISH_BY_NAME.get(fv)
            )
            if item:
                db.mark_forced_fish_drop_used(fid)
                return item, ""
            err = f"Fish '{fv}' not found in catalog"
        else:
            err = f"Unknown forced_type '{ft}'"
        db.set_forced_fish_drop_error(fid, err)
        print(f"[FISH] forced drop id={fid} failed: {err}")
        return None, err
    except Exception as exc:
        print(f"[FISH] _resolve_forced_fish error: {exc!r}")
        return None, str(exc)


def _lookup_fish(query: str) -> dict | None:
    """Case-insensitive fish lookup by name or partial name."""
    q = query.lower().strip()
    if q in _FISH_BY_NAME:
        return _FISH_BY_NAME[q]
    if q in _FISH_BY_ID:
        return _FISH_BY_ID[q]
    matches = [f for f in FISH_CATALOG if q in f["name"].lower()]
    return matches[0] if len(matches) == 1 else None


def _display_chance(drop_weight: float) -> str:
    ratio = _TOTAL_WEIGHT / drop_weight if drop_weight else 9999
    r = int(round(ratio))
    return f"1 in {r:,}"


# ---------------------------------------------------------------------------
# Fishing Rods
# ---------------------------------------------------------------------------

FISHING_RODS: dict[str, dict] = {
    "Driftwood Rod":   {"price": 0,        "cooldown": 30, "luck": 0.00, "weight_luck": 0.00, "value_bonus": 0.00, "fxp_bonus": 0.00, "desc": "Starter rod. Free."},
    "Bamboo Tide Rod": {"price": 5000,     "cooldown": 28, "luck": 0.03, "weight_luck": 0.03, "value_bonus": 0.00, "fxp_bonus": 0.05, "desc": "A sturdy bamboo rod."},
    "Copperline Rod":  {"price": 15000,    "cooldown": 26, "luck": 0.05, "weight_luck": 0.05, "value_bonus": 0.00, "fxp_bonus": 0.08, "desc": "Copper-reinforced line."},
    "Sailor's Rod":    {"price": 40000,    "cooldown": 24, "luck": 0.08, "weight_luck": 0.08, "value_bonus": 0.00, "fxp_bonus": 0.10, "desc": "Trusted by old sailors."},
    "Deepwater Rod":   {"price": 100000,   "cooldown": 22, "luck": 0.12, "weight_luck": 0.10, "value_bonus": 0.10, "fxp_bonus": 0.15, "desc": "Built for the deep."},
    "Stormcast Rod":   {"price": 250000,   "cooldown": 20, "luck": 0.18, "weight_luck": 0.15, "value_bonus": 0.15, "fxp_bonus": 0.20, "desc": "Casts through storms."},
    "Moonhook Rod":    {"price": 600000,   "cooldown": 18, "luck": 0.25, "weight_luck": 0.20, "value_bonus": 0.20, "fxp_bonus": 0.25, "desc": "Moonlit fishing magic."},
    "Leviathan Rod":   {"price": 1500000,  "cooldown": 16, "luck": 0.35, "weight_luck": 0.30, "value_bonus": 0.25, "fxp_bonus": 0.35, "desc": "Forged to hook leviathans."},
    "Abyss King Rod":  {"price": 5000000,  "cooldown": 14, "luck": 0.50, "weight_luck": 0.40, "value_bonus": 0.35, "fxp_bonus": 0.50, "desc": "The ultimate fishing rod."},
}

ROD_NAMES = list(FISHING_RODS.keys())


def _resolve_rod(query: str) -> str | None:
    """Resolve partial/case-insensitive rod name → exact key."""
    q = query.strip()
    if q in FISHING_RODS:
        return q
    ql = q.lower()
    for name in FISHING_RODS:
        if ql == name.lower():
            return name
    matches = [n for n in FISHING_RODS if ql in n.lower()]
    return matches[0] if len(matches) == 1 else None


# ---------------------------------------------------------------------------
# Roll mechanics
# ---------------------------------------------------------------------------

_FISHING_EFFECT_KEYS = frozenset({
    "fish_luck_boost",
    "fish_weight_luck_boost",
    "fish_value_multiplier",
    "fxp_multiplier",
    "fishing_cooldown_reduction",
    "legendary_plus_fish_chance_boost",
    "prismatic_fish_chance_boost",
    "exotic_fish_chance_boost",
})


def _get_fishing_event_effects() -> dict:
    """Return active fishing event effects from the Event Manager."""
    try:
        from modules.events import get_event_effect
        eff = get_event_effect()
        return {k: v for k, v in eff.items() if k in _FISHING_EFFECT_KEYS}
    except Exception:
        return {}


def _roll_fish(rod_name: str = "Driftwood Rod", event_eff: dict | None = None) -> dict:
    """Roll a random fish, applying rod luck and event effects."""
    rod  = FISHING_RODS.get(rod_name, FISHING_RODS["Driftwood Rod"])
    eff  = event_eff or {}
    luck            = rod["luck"] + eff.get("fish_luck_boost", 0.0)
    leg_plus_boost  = eff.get("legendary_plus_fish_chance_boost", 0.0)
    prismatic_boost = eff.get("prismatic_fish_chance_boost", 0.0)
    exotic_boost    = eff.get("exotic_fish_chance_boost", 0.0)
    # order: common=0, rare=1, epic=2, legendary=3, mythic=4, prismatic=5, exotic=6
    _LEG_PLUS_ORDERS = {3, 4, 5, 6}

    pool: list[tuple[dict, float]] = []
    for fish in FISH_CATALOG:
        w     = fish["drop_weight"]
        order = FISH_RARITIES[fish["rarity"]]["order"]
        if order >= 3 and luck > 0:
            w = w * (1 + luck * (1 + order * 0.4))
        if order in _LEG_PLUS_ORDERS and leg_plus_boost > 0:
            w = w * (1 + leg_plus_boost)
        if order == 5 and prismatic_boost > 0:
            w = w * (1 + prismatic_boost)
        if order == 6 and exotic_boost > 0:
            w = w * (1 + exotic_boost)
        pool.append((fish, w))

    total = sum(w for _, w in pool)
    r_val = random.uniform(0, total)
    cumul = 0.0
    for fish, w in pool:
        cumul += w
        if r_val <= cumul:
            return fish
    return FISH_CATALOG[-1]


def _roll_weight(fish: dict, rod_name: str = "Driftwood Rod",
                 event_eff: dict | None = None) -> float:
    eff  = event_eff or {}
    rod  = FISHING_RODS.get(rod_name, FISHING_RODS["Driftwood Rod"])
    wl   = rod["weight_luck"] + eff.get("fish_weight_luck_boost", 0.0)
    lo, hi = fish["min_weight"], fish["max_weight"]
    base = random.uniform(lo, hi)
    if wl > 0:
        bonus = (hi - base) * wl
        base  = min(hi, base + random.uniform(0, bonus))
    return round(base, 1)


def _calc_value(fish: dict, weight: float, rod_name: str = "Driftwood Rod",
                event_eff: dict | None = None) -> int:
    eff  = event_eff or {}
    rod  = FISHING_RODS.get(rod_name, FISHING_RODS["Driftwood Rod"])
    vb   = rod["value_bonus"]
    lo, hi = fish["min_weight"], fish["max_weight"]
    span   = max(0.01, hi - lo)
    wt_mult  = 1.0 + ((weight - lo) / span)
    val_mult = eff.get("fish_value_multiplier", 1.0)
    return max(1, int(fish["base_value"] * wt_mult * (1 + vb) * val_mult))


def _calc_fxp(fish: dict, rod_name: str = "Driftwood Rod",
              event_eff: dict | None = None) -> int:
    eff = event_eff or {}
    rod = FISHING_RODS.get(rod_name, FISHING_RODS["Driftwood Rod"])
    fb       = rod["fxp_bonus"]
    fxp_mult = eff.get("fxp_multiplier", 1.0)
    return max(1, int(fish["base_fxp"] * (1 + fb) * fxp_mult))


def _do_level_up(profile: dict, new_fxp: int) -> tuple[int, bool]:
    """Return (new_level, leveled_up)."""
    level = profile.get("fishing_level", 1)
    while new_fxp >= _fish_level_threshold(level):
        new_fxp -= _fish_level_threshold(level)
        level   += 1
    return level, level != profile.get("fishing_level", 1)


# ---------------------------------------------------------------------------
# Core /fish handler
# ---------------------------------------------------------------------------

async def handle_fish(bot: BaseBot, user: User) -> None:
    """/fish /f /cast /reel — catch a fish."""
    uname = user.username

    if not _is_in_room(uname):
        await _w(bot, user.id, "🎣 You must be in the room to fish.")
        return

    db.ensure_user(user.id, uname)
    profile  = db.get_or_create_fish_profile(user.id, uname)
    rod_name = profile.get("equipped_rod") or "Driftwood Rod"
    rod      = FISHING_RODS.get(rod_name, FISHING_RODS["Driftwood Rod"])
    cooldown = rod["cooldown"]

    eff      = _get_fishing_event_effects()
    cd_red   = eff.get("fishing_cooldown_reduction", 0.0)
    eff_cd   = max(5.0, cooldown * (1 - cd_red))
    secs_ago = _seconds_since(profile.get("last_fish_at"))
    if secs_ago < eff_cd:
        wait = int(eff_cd - secs_ago)
        await _w(bot, user.id, f"🎣 Cooldown: Fish again in {wait}s.")
        return

    fish   = _roll_fish(rod_name, eff)

    # Owner-forced fish drop override — applied before payout
    _forced_fish, _forced_err = _resolve_forced_fish(user.id, uname, rod_name, eff)
    if _forced_fish is not None:
        fish = _forced_fish
    elif _forced_err:
        print(f"[FISH] forced drop error for @{uname}: {_forced_err}")

    weight = _roll_weight(fish, rod_name, eff)
    value  = _calc_value(fish, weight, rod_name, eff)
    fxp    = _calc_fxp(fish, rod_name, eff)

    db.save_fish_catch(fish["name"], fish["rarity"], weight,
                       fish["base_value"], value, fxp, user.id, uname)
    new_fxp  = profile.get("fishing_xp", 0) + fxp
    new_lvl, leveled = _do_level_up(profile, new_fxp)
    leftover = new_fxp - sum(_fish_level_threshold(l)
                             for l in range(1, new_lvl))
    db.update_fish_profile(user.id, uname,
                           fishing_level=new_lvl,
                           fishing_xp=max(0, leftover),
                           total_catches=profile.get("total_catches", 0) + 1,
                           last_fish_at=_now_iso(),
                           best_fish_name=fish["name"]
                               if (weight > (profile.get("best_fish_weight") or 0))
                               else (profile.get("best_fish_name") or fish["name"]),
                           best_fish_weight=max(weight,
                                                profile.get("best_fish_weight") or 0),
                           best_fish_value=max(value,
                                               profile.get("best_fish_value") or 0))

    # Determine auto-sell setting (default ON for backward compat)
    _as = db.get_fish_auto_sell_settings(user.id)
    _auto_sell_on   = bool(_as.get("auto_sell_enabled", 1))
    _auto_sell_rare = bool(_as.get("auto_sell_rare_enabled", 0))
    # Rare protection: if auto_sell_rare is OFF, high rarities go to inventory
    _high_rarity = fish["rarity"] in ("legendary", "mythic", "prismatic", "exotic")
    _sold_now = _auto_sell_on and (not _high_rarity or _auto_sell_rare)

    # Always save to inventory; mark sold if auto-selling
    db.save_fish_to_inventory(
        user.id, uname,
        fish["name"], fish["rarity"],
        weight, value,
        sold=1 if _sold_now else 0,
    )

    if _sold_now:
        db.adjust_balance(user.id, value)

    rlabel = _rarity_label(fish["rarity"])
    nclr   = _name_colored(fish["rarity"], fish["name"])
    if _sold_now:
        msg = (f"🎣 Fishing\n"
               f"You caught {rlabel} {fish['emoji']} {nclr} | "
               f"⚖️ {weight}lb | 💰 {_fmt(value)}c | ⭐ +{fxp} FXP")
    else:
        msg = (f"🎣 Fishing\n"
               f"You caught {rlabel} {fish['emoji']} {nclr} | "
               f"⚖️ {weight}lb | 📦 Saved to bag | ⭐ +{fxp} FXP\n"
               f"Use /sellfish to sell")
    await _w(bot, user.id, msg[:249])

    if leveled:
        await _w(bot, user.id,
                 f"🎣 Level Up! Fishing Lv {new_lvl}! Keep casting!")

    try:
        await check_race_win(bot, user.id, uname, "fishing", fish["rarity"], fish.get("name", ""))
    except Exception as _ffe:
        print(f"[FISH] race_win error: {_ffe}")
    rarity_info = FISH_RARITIES.get(fish["rarity"], {})
    if rarity_info.get("announce"):
        extra = f" — {weight}lb, {_fmt(value)}c"
        try:
            await send_big_fish_announce(
                bot, fish["rarity"], uname,
                fish["name"], fish.get("emoji", "🐟"), extra)
        except Exception as _bae:
            print(f"[FISH] big_announce error: {_bae}")


# ---------------------------------------------------------------------------
# /fishlist
# ---------------------------------------------------------------------------

async def handle_fishlist(bot: BaseBot, user: User, args: list[str]) -> None:
    """/fishlist [rarity] [page]"""
    if len(args) >= 2:
        raw = args[1].lower()
        rarity = _RARITY_ALIASES.get(raw)
        if not rarity:
            await _w(bot, user.id,
                     "Usage: /fishlist [rarity] — e.g. /fishlist common")
            return
        page = 1
        if len(args) >= 3 and args[2].isdigit():
            page = max(1, int(args[2]))
        pool = [f for f in FISH_CATALOG if f["rarity"] == rarity]
        total_pages = max(1, (len(pool) + 4) // 5)
        page = min(page, total_pages)
        start = (page - 1) * 5
        chunk = pool[start: start + 5]
        hdr   = FISH_RARITIES[rarity]["label"]
        lines = [f"{hdr} p{page}/{total_pages}"]
        for f in chunk:
            lines.append(f"{f['emoji']} {f['name']} — {_display_chance(f['drop_weight'])}")
        await _w(bot, user.id, "\n".join(lines)[:249])
    else:
        lines = ["🎣 Fish List"]
        for r in RARITY_ORDER:
            cnt = sum(1 for f in FISH_CATALOG if f["rarity"] == r)
            lines.append(f"[{r.upper()}] — {cnt} fish")
        lines.append("Use /fishlist common to view Common fish.")
        lines.append("/fishprices common for prices & weights.")
        await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /fishprices
# ---------------------------------------------------------------------------

async def handle_fishprices(bot: BaseBot, user: User, args: list[str]) -> None:
    """/fishprices [rarity] [page]"""
    if len(args) < 2:
        await _w(bot, user.id,
                 "Usage: /fishprices <rarity> [page]\n"
                 "e.g. /fishprices common")
        return
    rarity = _RARITY_ALIASES.get(args[1].lower())
    if not rarity:
        await _w(bot, user.id, "Unknown rarity. Try: common rare epic legendary mythic")
        return
    page = 1
    if len(args) >= 3 and args[2].isdigit():
        page = max(1, int(args[2]))
    pool       = [f for f in FISH_CATALOG if f["rarity"] == rarity]
    total_pages = max(1, (len(pool) + 4) // 5)
    page = min(page, total_pages)
    start = (page - 1) * 5
    chunk = pool[start: start + 5]
    lines = [f"💰 {rarity.title()} Prices p{page}/{total_pages}"]
    for f in chunk:
        lines.append(
            f"{f['emoji']} {f['name']} — {_fmt(f['base_value'])}c"
            f" | {f['min_weight']}–{f['max_weight']}lb"
        )
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /fishinfo
# ---------------------------------------------------------------------------

async def handle_fishinfo(bot: BaseBot, user: User, args: list[str]) -> None:
    """/fishinfo <fish name>"""
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /fishinfo <fish name>")
        return
    query = " ".join(args[1:])
    fish  = _lookup_fish(query)
    if not fish:
        matches = [f["name"] for f in FISH_CATALOG
                   if query.lower() in f["name"].lower()][:4]
        if matches:
            await _w(bot, user.id,
                     f"Did you mean: {', '.join(matches)}?")
        else:
            await _w(bot, user.id, f"Fish not found: {query}")
        return
    rlabel = _rarity_label(fish["rarity"])
    lines = [
        f"💎 Fish Info",
        f"Name: {fish['name']}",
        f"Rarity: {rlabel}",
        f"Chance: {_display_chance(fish['drop_weight'])}",
        f"Base Value: {_fmt(fish['base_value'])}c",
        f"Weight: {fish['min_weight']}–{fish['max_weight']}lb",
        f"FXP: +{fish['base_fxp']}",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /myfish  /fishinv
# ---------------------------------------------------------------------------

async def handle_myfish(bot: BaseBot, user: User) -> None:
    """/myfish /fishinv /fishbag — show inventory (unsold fish) and stats."""
    db.ensure_user(user.id, user.username)
    profile  = db.get_or_create_fish_profile(user.id, user.username)
    inv      = db.get_fish_inventory(user.id, limit=5, sold=0)
    lvl      = profile.get("fishing_level", 1)
    xp       = profile.get("fishing_xp", 0)
    total    = profile.get("total_catches", 0)
    best_n   = profile.get("best_fish_name") or "None"
    best_w   = profile.get("best_fish_weight") or 0
    rod      = profile.get("equipped_rod") or "Driftwood Rod"
    _as      = db.get_fish_auto_sell_settings(user.id)
    as_state = "ON" if _as.get("auto_sell_enabled", 1) else "OFF"
    lines = [
        f"🎣 Fish Bag | Auto-Sell: {as_state}",
        f"Lv {lvl} | FXP {_fmt(xp)}/{_fmt(_fish_level_threshold(lvl))}",
        f"Catches: {total} | Rod: {rod}",
        f"Best: {best_n} {best_w}lb",
    ]
    if inv:
        total_val = sum(r["value"] for r in inv)
        lines.append(f"Unsold ({len(inv)}+) | Value: {_fmt(total_val)}c:")
        for r in inv[:3]:
            rl = _rarity_label(r["rarity"])
            lines.append(f"  {rl} {r['fish_name']} {r['weight']}lb")
        lines.append("→ /sellfish to sell all")
    else:
        lines.append("Bag empty. Fish to fill it! /fish")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /sellfish /sellallfish  (coins already credited on catch)
# ---------------------------------------------------------------------------

async def handle_sellfish(bot: BaseBot, user: User) -> None:
    """/sellfish — sell all unsold fish in inventory."""
    db.ensure_user(user.id, user.username)
    count, coins = db.sell_all_fish_inventory(user.id)
    if count == 0:
        await _w(bot, user.id,
                 "🎣 No unsold fish in your bag.\n"
                 "Go fishing: /fish  |  View bag: /myfish")
        return
    db.adjust_balance(user.id, coins)
    await _w(bot, user.id,
             f"🎣 Sold {count} fish for {_fmt(coins)} coins!\n"
             f"Balance updated. Keep fishing: /fish")


async def handle_sellallfish(bot: BaseBot, user: User) -> None:
    """/sellallfish — alias for /sellfish."""
    await handle_sellfish(bot, user)


async def handle_sellfishrarity(bot: BaseBot, user: User, args: list[str]) -> None:
    """/sellfishrarity <rarity> — sell all unsold fish of a rarity."""
    db.ensure_user(user.id, user.username)
    if len(args) < 2:
        await _w(bot, user.id,
                 "Usage: /sellfishrarity <rarity>\n"
                 "e.g. /sellfishrarity common")
        return
    raw = args[1].lower()
    rarity = _RARITY_ALIASES.get(raw)
    if not rarity:
        await _w(bot, user.id,
                 "Unknown rarity. Use: common/rare/epic/legendary/mythic/prismatic/exotic")
        return
    count, coins = db.sell_fish_inventory_by_rarity(user.id, rarity)
    if count == 0:
        await _w(bot, user.id,
                 f"🎣 No unsold {rarity} fish in your bag.")
        return
    db.adjust_balance(user.id, coins)
    await _w(bot, user.id,
             f"🎣 Sold {count} {rarity} fish for {_fmt(coins)} coins!")


async def handle_fishbag(bot: BaseBot, user: User) -> None:
    """/fishbag /fishinventory — alias for /myfish."""
    await handle_myfish(bot, user)


async def handle_fishbook(bot: BaseBot, user: User) -> None:
    """/fishbook — show distinct fish species caught."""
    db.ensure_user(user.id, user.username)
    species = db.get_fish_book(user.id)
    if not species:
        await _w(bot, user.id,
                 "📖 Fish Book empty.\n"
                 "Catch fish to discover species! /fish")
        return
    lines = [f"📖 Fish Book ({len(species)} species)"]
    for sp in species[:8]:
        rl    = _rarity_label(sp["rarity"])
        total = sp.get("total_caught", 0)
        best  = sp.get("best_weight", 0)
        lines.append(f"{rl} {sp['fish_name']} | {total}x | Best: {best}lb")
    if len(species) > 8:
        lines.append(f"... and {len(species) - 8} more")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_fishautosell(bot: BaseBot, user: User, args: list[str]) -> None:
    """/fishautosell on|off|status — toggle fish auto-sell mode."""
    db.ensure_user(user.id, user.username)
    sub = args[1].lower() if len(args) > 1 else "status"
    if sub == "on":
        db.set_fish_auto_sell(user.id, user.username, 1)
        await _w(bot, user.id,
                 "🎣 Fish Auto-Sell: ON\n"
                 "Fish are sold immediately on catch.")
    elif sub == "off":
        db.set_fish_auto_sell(user.id, user.username, 0)
        await _w(bot, user.id,
                 "🎣 Fish Auto-Sell: OFF\n"
                 "Fish go to your bag (/myfish).\n"
                 "Sell with /sellfish when ready.")
    else:
        _as = db.get_fish_auto_sell_settings(user.id)
        on      = bool(_as.get("auto_sell_enabled", 1))
        rare_on = bool(_as.get("auto_sell_rare_enabled", 0))
        await _w(bot, user.id,
                 f"🎣 Fish Auto-Sell: {'ON' if on else 'OFF'}\n"
                 f"Rare Auto-Sell: {'ON' if rare_on else 'OFF'}\n"
                 f"/fishautosell on|off\n"
                 f"/fishautosellrare on|off")


async def handle_fishautosellrare(bot: BaseBot, user: User, args: list[str]) -> None:
    """/fishautosellrare on|off — toggle auto-sell for rare+ fish."""
    db.ensure_user(user.id, user.username)
    sub = args[1].lower() if len(args) > 1 else "status"
    if sub == "on":
        db.set_fish_auto_sell_rare(user.id, user.username, 1)
        await _w(bot, user.id,
                 "🎣 Rare Auto-Sell: ON\n"
                 "Legendary/Mythic/Prismatic/Exotic fish also auto-sell.")
    elif sub == "off":
        db.set_fish_auto_sell_rare(user.id, user.username, 0)
        await _w(bot, user.id,
                 "🎣 Rare Auto-Sell: OFF\n"
                 "Rare+ fish are protected and saved to bag.")
    else:
        _as = db.get_fish_auto_sell_settings(user.id)
        rare_on = bool(_as.get("auto_sell_rare_enabled", 0))
        await _w(bot, user.id,
                 f"🎣 Rare Auto-Sell: {'ON' if rare_on else 'OFF'}\n"
                 "/fishautosellrare on|off")


# ---------------------------------------------------------------------------
# /fishlevel  /fishxp  /fishstats
# ---------------------------------------------------------------------------

async def handle_fishlevel(bot: BaseBot, user: User) -> None:
    """/fishlevel /fishxp — fishing level and FXP."""
    db.ensure_user(user.id, user.username)
    profile  = db.get_or_create_fish_profile(user.id, user.username)
    lvl      = profile.get("fishing_level", 1)
    xp       = profile.get("fishing_xp", 0)
    needed   = _fish_level_threshold(lvl)
    total    = profile.get("total_catches", 0)
    rod      = profile.get("equipped_rod") or "Driftwood Rod"
    best_n   = profile.get("best_fish_name") or "None"
    best_w   = profile.get("best_fish_weight") or 0.0
    lines = [
        f"🎣 Fishing Level",
        f"Level: {lvl} | FXP: {_fmt(xp)}/{_fmt(needed)}",
        f"Total catches: {_fmt(total)}",
        f"Rod: {rod}",
        f"Best catch: {best_n} {best_w}lb",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_fishstats(bot: BaseBot, user: User, args: list[str]) -> None:
    """/fishstats [username] — fishing stats."""
    target_name = args[1] if len(args) >= 2 else user.username
    target_name = target_name.lstrip("@")
    conn = db.get_connection()
    row  = conn.execute(
        "SELECT * FROM fish_profiles WHERE lower(username)=?",
        (target_name.lower(),)
    ).fetchone()
    conn.close()
    if not row:
        await _w(bot, user.id, f"No fishing stats for @{target_name}.")
        return
    r = dict(row)
    lines = [
        f"🎣 @{r['username']} Fishing",
        f"Level: {r['fishing_level']} | FXP: {_fmt(r['fishing_xp'])}",
        f"Catches: {_fmt(r['total_catches'])}",
        f"Best: {r.get('best_fish_name') or 'None'} "
        f"{r.get('best_fish_weight') or 0}lb",
        f"Rod: {r.get('equipped_rod') or 'Driftwood Rod'}",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /fishboosts /fishingevents
# ---------------------------------------------------------------------------

async def handle_fishboosts(bot: BaseBot, user: User) -> None:
    """/fishboosts /fishingevents — active fishing event boosts."""
    try:
        from modules.events import (
            get_event_effect, _FISHING_EVENT_IDS,
            _format_fishing_event_effects, EVENTS, _time_remaining,
        )
        import database as _db
        mine_ev = _db.get_active_mining_event()
        eff     = get_event_effect()
        has_boost = any([
            eff.get("fish_luck_boost", 0) > 0,
            eff.get("fish_weight_luck_boost", 0) > 0,
            eff.get("fish_value_multiplier", 1.0) > 1.0,
            eff.get("fxp_multiplier", 1.0) > 1.0,
            eff.get("fishing_cooldown_reduction", 0) > 0,
            eff.get("legendary_plus_fish_chance_boost", 0) > 0,
            eff.get("prismatic_fish_chance_boost", 0) > 0,
            eff.get("exotic_fish_chance_boost", 0) > 0,
        ])
        if not has_boost:
            await _w(bot, user.id,
                     "🎣 Fishing Boosts\nNo fishing boosts active.")
            return
        lines = ["🎣 Fishing Boosts"]
        if mine_ev and mine_ev.get("event_id", "") in _FISHING_EVENT_IDS:
            eid      = mine_ev["event_id"]
            name     = EVENTS.get(eid, {}).get("name", eid)
            eff_str  = _format_fishing_event_effects(eid)
            left     = _time_remaining(mine_ev.get("ends_at", ""))
            lines.append(f"{name}: {eff_str}"[:80])
            lines.append(f"Ends in: {left}")
        else:
            if eff.get("fish_luck_boost", 0) > 0:
                lines.append(f"🌊 Fish luck +{int(eff['fish_luck_boost']*100)}%")
            if eff.get("fish_weight_luck_boost", 0) > 0:
                lines.append(f"⚖️ Weight +{int(eff['fish_weight_luck_boost']*100)}%")
            if eff.get("fish_value_multiplier", 1.0) > 1.0:
                lines.append(f"💰 Value {eff['fish_value_multiplier']}x")
            if eff.get("fxp_multiplier", 1.0) > 1.0:
                lines.append(f"⭐ FXP {eff['fxp_multiplier']}x")
            if eff.get("fishing_cooldown_reduction", 0) > 0:
                lines.append(f"⏳ Cooldown -{int(eff['fishing_cooldown_reduction']*100)}%")
            if eff.get("legendary_plus_fish_chance_boost", 0) > 0:
                lines.append(f"🐉 Leg+ +{int(eff['legendary_plus_fish_chance_boost']*100)}%")
            if eff.get("prismatic_fish_chance_boost", 0) > 0:
                lines.append(f"🌈 Prismatic +{int(eff['prismatic_fish_chance_boost']*100)}%")
            if eff.get("exotic_fish_chance_boost", 0) > 0:
                lines.append(f"🚨 Exotic +{int(eff['exotic_fish_chance_boost']*100)}%")
        await _w(bot, user.id, "\n".join(lines)[:249])
    except Exception as exc:
        print(f"[FISHBOOSTS] error: {exc}")
        await _w(bot, user.id, "🎣 Fishing Boosts\nNo fishing boosts active.")


async def handle_fishingevents(bot: BaseBot, user: User) -> None:
    """/fishingevents — alias for /fishboosts."""
    await handle_fishboosts(bot, user)


# ---------------------------------------------------------------------------
# /fishhelp /fishinghelp
# ---------------------------------------------------------------------------

async def handle_fishchances(bot: BaseBot, user: User) -> None:
    """!fishchances — show base rarity drop % for fishing."""
    _BASE_FISH_CHANCES = [
        ("Common",    82.000),
        ("Rare",      13.000),
        ("Epic",       4.500),
        ("Legendary",  0.470),
        ("Mythic",     0.025),
        ("Prismatic",  0.004),
        ("Exotic",     0.001),
    ]
    lines = ["🎣 Fishing Chances"]
    for label, pct in _BASE_FISH_CHANCES:
        pct_str = f"{pct}%" if pct >= 0.01 else f"{pct:.4f}%"
        lines.append(f"{label}: {pct_str}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_fishhelp(bot: BaseBot, user: User) -> None:
    """/fishhelp — fishing command reference."""
    await _w(bot, user.id,
             "🎣 Fishing Commands\n"
             "!fish !cast !reel — catch a fish\n"
             "!fishlist [rarity] — fish by rarity\n"
             "!fishprices [rarity] — prices & weights\n"
             "!fishinfo [name] — fish details\n"
             "!myfish — your catches\n"
             "!fishlevel — FXP & level\n"
             "!rods !rodshop !buyrod !equiprod")


# ---------------------------------------------------------------------------
# Leaderboards
# ---------------------------------------------------------------------------

async def handle_topfish(bot: BaseBot, user: User) -> None:
    """/topfish /topfishing /fishlb — top fishers by catch count."""
    rows = db.get_top_fishers(limit=8)
    if not rows:
        await _w(bot, user.id, "🏆 No fishing data yet.")
        return
    lines = ["🏆 Top Fishers"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. @{r['username']} — Lv {r['fishing_level']} | "
                     f"{_fmt(r['total_catches'])} catches")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_topweightfish(bot: BaseBot, user: User) -> None:
    """/topweightfish /biggestfish — biggest fish by weight."""
    rows = db.get_biggest_fish_catches(limit=8)
    if not rows:
        await _w(bot, user.id, "⚖️ No catch records yet.")
        return
    lines = ["⚖️ Biggest Fish"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. @{r['username']} — {r['fish_name']} {r['weight']}lb")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# Rod commands
# ---------------------------------------------------------------------------

async def handle_rods(bot: BaseBot, user: User) -> None:
    """/rods — list all rods."""
    lines = ["🎣 Fishing Rods"]
    for name, r in FISHING_RODS.items():
        price = f"{_fmt(r['price'])}c" if r["price"] else "Free"
        lines.append(f"• {name} — {price} | {r['cooldown']}s cd")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_myrod(bot: BaseBot, user: User) -> None:
    """/myrod — show equipped rod stats."""
    db.ensure_user(user.id, user.username)
    profile  = db.get_or_create_fish_profile(user.id, user.username)
    rod_name = profile.get("equipped_rod") or "Driftwood Rod"
    rod      = FISHING_RODS.get(rod_name, FISHING_RODS["Driftwood Rod"])
    lines = [
        f"🎣 Equipped Rod",
        f"Rod: {rod_name}",
        f"Cooldown: {rod['cooldown']}s",
        f"Luck: +{int(rod['luck']*100)}%",
        f"Weight: +{int(rod['weight_luck']*100)}%",
        f"Value: +{int(rod['value_bonus']*100)}%",
        f"FXP: +{int(rod['fxp_bonus']*100)}%",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_rodshop(bot: BaseBot, user: User) -> None:
    """/rodshop — show rod shop."""
    lines = ["🎣 Rod Shop"]
    for i, (name, r) in enumerate(FISHING_RODS.items(), 1):
        if r["price"] == 0:
            continue
        lines.append(f"{i}. {name} — {_fmt(r['price'])}c | {r['cooldown']}s cd")
    lines.append("Buy: /buyrod <name>")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_buyrod(bot: BaseBot, user: User, args: list[str]) -> None:
    """/buyrod <rod name>"""
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /buyrod <rod name>  (see /rodshop)")
        return
    query    = " ".join(args[1:])
    rod_name = _resolve_rod(query)
    if not rod_name:
        await _w(bot, user.id,
                 f"Rod not found: {query}\nSee /rodshop for names.")
        return
    rod = FISHING_RODS[rod_name]
    if rod["price"] == 0:
        await _w(bot, user.id,
                 f"🎣 {rod_name} is free! Use /equiprod {rod_name}.")
        return
    db.ensure_user(user.id, user.username)
    if db.player_owns_rod(user.id, rod_name):
        await _w(bot, user.id,
                 f"🎣 You already own {rod_name}.\nUse /equiprod to equip it.")
        return
    bal = db.get_balance(user.id)
    if bal < rod["price"]:
        await _w(bot, user.id,
                 f"🎣 {rod_name} costs {_fmt(rod['price'])}c.\n"
                 f"You have {_fmt(bal)}c. Not enough coins.")
        return
    db.adjust_balance(user.id, -rod["price"])
    db.add_player_rod(user.id, user.username, rod_name)
    await _w(bot, user.id,
             f"🎣 Purchased {rod_name}!\n"
             f"Use /equiprod to equip it.")


async def handle_equiprod(bot: BaseBot, user: User, args: list[str]) -> None:
    """/equiprod <rod name>"""
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /equiprod <rod name>")
        return
    query    = " ".join(args[1:])
    rod_name = _resolve_rod(query)
    if not rod_name:
        await _w(bot, user.id, f"Rod not found: {query}")
        return
    db.ensure_user(user.id, user.username)
    if FISHING_RODS[rod_name]["price"] > 0 and not db.player_owns_rod(user.id, rod_name):
        await _w(bot, user.id,
                 f"🎣 You don't own {rod_name}. Buy it at /rodshop.")
        return
    db.equip_rod(user.id, user.username, rod_name)
    rod = FISHING_RODS[rod_name]
    await _w(bot, user.id,
             f"🎣 Equipped: {rod_name}\n"
             f"Cooldown: {rod['cooldown']}s | Luck: +{int(rod['luck']*100)}%")


async def handle_rodinfo(bot: BaseBot, user: User, args: list[str]) -> None:
    """/rodinfo <rod name>"""
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /rodinfo <rod name>")
        return
    query    = " ".join(args[1:])
    rod_name = _resolve_rod(query)
    if not rod_name:
        await _w(bot, user.id, f"Rod not found: {query}")
        return
    r = FISHING_RODS[rod_name]
    price = f"{_fmt(r['price'])}c" if r["price"] else "Free"
    lines = [
        f"🎣 {rod_name}",
        f"Price: {price}",
        r["desc"],
        f"Cooldown: {r['cooldown']}s",
        f"Luck: +{int(r['luck']*100)}% | Weight: +{int(r['weight_luck']*100)}%",
        f"Value: +{int(r['value_bonus']*100)}% | FXP: +{int(r['fxp_bonus']*100)}%",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_rodupgrade(bot: BaseBot, user: User) -> None:
    """/rodupgrade — direct purchase system."""
    await _w(bot, user.id,
             "🎣 Rod Upgrade\nBuy better rods at /rodshop.\n"
             "Each rod is a direct purchase — no upgrade chain.")


async def handle_rodstats(bot: BaseBot, user: User) -> None:
    """/rodstats — rod stat explanation."""
    await _w(bot, user.id,
             "🎣 Rod Stats Guide\n"
             "Cooldown: time between /fish casts\n"
             "Luck: boosts chance of rare fish\n"
             "Weight: shifts fish toward heavier end\n"
             "Value: % bonus to coin reward\n"
             "FXP: % bonus to fishing XP")


# ---------------------------------------------------------------------------
# AutoFish — per-user asyncio tasks
# ---------------------------------------------------------------------------

_autofish_tasks: dict[str, asyncio.Task] = {}


def _get_af_setting(key: str, default: str) -> str:
    try:
        return db.get_auto_activity_setting(key, default)
    except Exception:
        return default


async def _autofish_loop(bot: BaseBot, user: User) -> None:
    """Background AutoFish loop for one player."""
    uid       = user.id
    uname     = user.username
    max_att   = int(_get_af_setting("autofish_max_attempts",   "30"))
    max_mins  = int(_get_af_setting("autofish_duration_minutes", "30"))
    start_t   = datetime.now(timezone.utc)
    attempts  = 0

    await _w(bot, uid,
             f"🎣 AutoFish Started\n"
             f"Limit: {max_att} catches or {max_mins}m.\n"
             f"Stops if you leave. /autofish off to stop.")

    _af_started_at = datetime.now(timezone.utc).isoformat()
    try:
        db.save_auto_fish_session(uid, uname, _af_started_at, max_att, max_mins)
    except Exception:
        pass

    try:
        while True:
            # Check global flag
            if _get_af_setting("autofish_enabled", "1") != "1":
                await _w(bot, uid,
                         "🎣 AutoFish Stopped\nReason: Disabled by staff.")
                break

            # Room presence check
            if not _is_in_room(uname):
                await _w(bot, uid,
                         f"🎣 AutoFish Stopped\n"
                         f"Reason: You left the room.\n"
                         f"Catches: {attempts}/{max_att}")
                break

            # Session limits
            elapsed_mins = (datetime.now(timezone.utc) - start_t).total_seconds() / 60
            if attempts >= max_att:
                await _w(bot, uid,
                         f"🎣 AutoFish Stopped\n"
                         f"Reason: Session limit reached.\n"
                         f"Catches: {attempts}/{max_att}")
                break
            if elapsed_mins >= max_mins:
                await _w(bot, uid,
                         f"🎣 AutoFish Stopped\n"
                         f"Reason: Time limit reached.\n"
                         f"Catches: {attempts}/{max_att}")
                break

            # Attempt a fish — respect cooldown
            profile  = db.get_or_create_fish_profile(uid, uname)
            rod_name = profile.get("equipped_rod") or "Driftwood Rod"
            rod      = FISHING_RODS.get(rod_name, FISHING_RODS["Driftwood Rod"])
            cooldown = rod["cooldown"]
            eff      = _get_fishing_event_effects()
            cd_red   = eff.get("fishing_cooldown_reduction", 0.0)
            eff_cd   = max(5.0, cooldown * (1 - cd_red))
            secs_ago = _seconds_since(profile.get("last_fish_at"))

            if secs_ago < eff_cd:
                wait = max(1, int(eff_cd - secs_ago) + 1)
                await asyncio.sleep(wait)
                continue
            fish   = _roll_fish(rod_name, eff)

            # Owner-forced fish drop override — applied before payout
            _af_forced, _af_err = _resolve_forced_fish(uid, uname, rod_name, eff)
            if _af_forced is not None:
                fish = _af_forced
            elif _af_err:
                print(f"[AUTOFISH] forced drop error for @{uname}: {_af_err}")

            weight = _roll_weight(fish, rod_name, eff)
            value  = _calc_value(fish, weight, rod_name, eff)
            fxp    = _calc_fxp(fish, rod_name, eff)

            db.save_fish_catch(fish["name"], fish["rarity"], weight,
                               fish["base_value"], value, fxp, uid, uname)
            new_xp  = profile.get("fishing_xp", 0) + fxp
            new_lvl, leveled = _do_level_up(profile, new_xp)
            leftover = new_xp - sum(_fish_level_threshold(l)
                                    for l in range(1, new_lvl))
            db.update_fish_profile(uid, uname,
                                   fishing_level=new_lvl,
                                   fishing_xp=max(0, leftover),
                                   total_catches=profile.get("total_catches", 0) + 1,
                                   last_fish_at=_now_iso(),
                                   best_fish_name=fish["name"]
                                       if weight > (profile.get("best_fish_weight") or 0)
                                       else (profile.get("best_fish_name") or fish["name"]),
                                   best_fish_weight=max(weight, profile.get("best_fish_weight") or 0),
                                   best_fish_value=max(value, profile.get("best_fish_value") or 0))
            db.adjust_balance(uid, value)
            attempts += 1
            try:
                db.update_auto_fish_attempts(uid, attempts)
            except Exception:
                pass

            rlabel = _rarity_label(fish["rarity"])
            nclr   = _name_colored(fish["rarity"], fish["name"])
            msg    = (f"🎣 AutoFish #{attempts}\n"
                      f"{rlabel} {fish['emoji']} {nclr} | "
                      f"⚖️ {weight}lb | 💰 {_fmt(value)}c | ⭐ +{fxp} FXP")
            await _w(bot, uid, msg[:249])

            if leveled:
                await _w(bot, uid,
                         f"🎣 Level Up! Fishing Lv {new_lvl}!")

            try:
                await check_race_win(bot, uid, uname, "fishing", fish["rarity"], fish.get("name", ""))
            except Exception as _ffe:
                print(f"[AUTOFISH] race_win error: {_ffe}")
            rinfo = FISH_RARITIES.get(fish["rarity"], {})
            if rinfo.get("announce"):
                extra = f" — {weight}lb, {_fmt(value)}c"
                try:
                    await send_big_fish_announce(
                        bot, fish["rarity"], uname,
                        fish["name"], fish["emoji"], extra)
                except Exception as _bae:
                    print(f"[AUTOFISH] big_announce error: {_bae}")

            await asyncio.sleep(cooldown + 1)

    except asyncio.CancelledError:
        pass
    except Exception as exc:
        print(f"[AUTOFISH] Loop error for {uname}: {exc}")
    finally:
        _autofish_tasks.pop(uid, None)
        try:
            db.stop_auto_fish_session(uid)
        except Exception:
            pass


def stop_autofish_for_user(user_id: str, username: str,
                           reason: str = "player_left") -> bool:
    """Cancel the AutoFish task for a user. Returns True if one was running."""
    task = _autofish_tasks.pop(user_id, None)
    try:
        db.stop_auto_fish_session(user_id)
    except Exception:
        pass
    if task and not task.done():
        task.cancel()
        return True
    return False


async def handle_autofish(bot: BaseBot, user: User, args: list[str]) -> None:
    """/autofish on|off|status"""
    sub = args[1].lower() if len(args) >= 2 else "status"

    if sub in ("on", "start"):
        if _get_af_setting("autofish_enabled", "1") != "1":
            await _w(bot, user.id,
                     "🎣 AutoFish is currently disabled by staff.")
            return
        if user.id in _autofish_tasks and not _autofish_tasks[user.id].done():
            await _w(bot, user.id,
                     "🎣 AutoFish is already running.\n"
                     "Use /autofish off to stop it first.")
            return
        if not _is_in_room(user.username):
            await _w(bot, user.id,
                     "🎣 You must be in the room to start AutoFish.")
            return
        task = asyncio.create_task(_autofish_loop(bot, user))
        _autofish_tasks[user.id] = task

    elif sub in ("off", "stop"):
        if stop_autofish_for_user(user.id, user.username, "user_stopped"):
            await _w(bot, user.id,
                     "🎣 AutoFish stopped.")
        else:
            await _w(bot, user.id,
                     "🎣 No active AutoFish session.")

    else:
        await handle_autofishstatus(bot, user)


async def handle_autofishstatus(bot: BaseBot, user: User) -> None:
    """/autofishstatus /afstatus"""
    running  = user.id in _autofish_tasks and not _autofish_tasks[user.id].done()
    enabled  = _get_af_setting("autofish_enabled", "1") == "1"
    max_att  = _get_af_setting("autofish_max_attempts",    "30")
    max_mins = _get_af_setting("autofish_duration_minutes", "30")
    lines = [
        "🎣 AutoFish Status",
        f"Status: {'ON' if running else 'OFF'}",
        f"Global: {'Enabled' if enabled else 'Disabled by staff'}",
        f"Limit: {max_att} catches or {max_mins}m",
        "Use /autofish on to start | /autofish off to stop",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# AutoFish staff settings
# ---------------------------------------------------------------------------

async def handle_autofishsettings(bot: BaseBot, user: User) -> None:
    """/autofishsettings — staff view."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    en   = _get_af_setting("autofish_enabled",           "1")
    dur  = _get_af_setting("autofish_duration_minutes",  "30")
    att  = _get_af_setting("autofish_max_attempts",      "30")
    cap  = _get_af_setting("autofish_daily_cap_minutes", "120")
    lines = [
        "🎣 AutoFish Settings",
        f"Enabled: {'YES' if en=='1' else 'NO'}",
        f"Session duration: {dur}m",
        f"Session attempts: {att}",
        f"Daily cap: {cap}m",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_setautofish(bot: BaseBot, user: User, args: list[str]) -> None:
    """/setautofish on|off"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        await _w(bot, user.id, "Usage: /setautofish on|off")
        return
    val = "1" if args[1].lower() == "on" else "0"
    db.set_auto_activity_setting("autofish_enabled", val)
    await _w(bot, user.id,
             f"🎣 AutoFish {'enabled' if val=='1' else 'disabled'}.")


async def handle_setautofishduration(bot: BaseBot, user: User, args: list[str]) -> None:
    """/setautofishduration <minutes>"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: /setautofishduration <minutes>")
        return
    val = max(5, min(120, int(args[1])))
    db.set_auto_activity_setting("autofish_duration_minutes", str(val))
    await _w(bot, user.id, f"🎣 AutoFish session duration: {val}m.")


async def handle_setautofishattempts(bot: BaseBot, user: User, args: list[str]) -> None:
    """/setautofishattempts <amount>"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: /setautofishattempts <amount>")
        return
    val = max(5, min(200, int(args[1])))
    db.set_auto_activity_setting("autofish_max_attempts", str(val))
    await _w(bot, user.id, f"🎣 AutoFish max attempts: {val}.")


async def handle_setautofishdailycap(bot: BaseBot, user: User, args: list[str]) -> None:
    """/setautofishdailycap <minutes>"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: /setautofishdailycap <minutes>")
        return
    val = max(30, min(480, int(args[1])))
    db.set_auto_activity_setting("autofish_daily_cap_minutes", str(val))
    await _w(bot, user.id, f"🎣 AutoFish daily cap: {val}m.")


# ---------------------------------------------------------------------------
# AutoFish restart recovery
# ---------------------------------------------------------------------------

async def startup_autofish_recovery(bot: BaseBot) -> None:
    """Restart AutoFish sessions for players still in room after a bot restart."""
    await asyncio.sleep(5)  # let room cache populate first
    try:
        sessions = db.get_all_active_auto_fish_sessions()
    except Exception as exc:
        print(f"[AUTOFISH] startup_recovery DB error: {exc}")
        return
    if not sessions:
        return
    print(f"[AUTOFISH] Recovering {len(sessions)} session(s)")

    # Fetch live room user list
    room_names: set[str] = set()
    try:
        resp = await bot.highrise.get_room_users()
        if hasattr(resp, "content"):
            for _u, _ in resp.content:
                room_names.add(_u.username.lower())
    except Exception as _re:
        print(f"[AUTOFISH] Could not get room users: {_re}")

    class _FakeUser:
        def __init__(self, uid: str, uname: str) -> None:
            self.id       = uid
            self.username = uname

    for s in sessions:
        uid   = s["user_id"]
        uname = s["username"]
        if room_names and uname.lower() not in room_names:
            try:
                db.stop_auto_fish_session(uid, "restart_not_in_room")
            except Exception:
                pass
            continue
        if uid in _autofish_tasks and not _autofish_tasks[uid].done():
            continue
        fake_user = _FakeUser(uid, uname)
        task = asyncio.create_task(_autofish_loop(bot, fake_user))
        _autofish_tasks[uid] = task
        print(f"[AUTOFISH] Resumed session for @{uname}")


# ---------------------------------------------------------------------------
# /forcedropfish /forcedropfishitem /forcedropfishstatus /clearforcedropfish
# Aliases: /forcefish /forcefishdrop /forcefishdropfish /forcefishstatus /clearforcefish
# (owner-only)
# ---------------------------------------------------------------------------

_FISH_RARITY_LIST = "common, rare, epic, legendary, mythic, prismatic, exotic"


async def handle_forcedropfish(bot: BaseBot, user: User, args: list[str]) -> None:
    """
    /forcedropfish <username> <rarity>
    Forces a rarity for that player's next /fish (owner-only).
    Aliases: /forcefish /forcefishdrop
    """
    from modules.permissions import is_owner
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner-only command.")
        return
    if len(args) < 3:
        await _w(bot, user.id,
                 f"🎯 Force Fish Drop\n"
                 f"Usage: /forcedropfish <username> <rarity>\n"
                 f"Example: /forcedropfish @Marion exotic\n"
                 f"Rarities: {_FISH_RARITY_LIST}")
        return
    target = _norm_uname(args[1])
    raw_r  = args[2].lower()
    if raw_r not in RARITY_ORDER:
        await _w(bot, user.id, f"❌ Invalid rarity. Valid: {_FISH_RARITY_LIST}")
        return
    try:
        drop_id = db.set_forced_fish_drop(target, "rarity", raw_r, user.username)
        await _w(bot, user.id,
                 f"🎯 Forced Fish Drop Set\n"
                 f"Target: @{target}\n"
                 f"Type: rarity | Value: {raw_r}\n"
                 f"Status: pending | ID: {drop_id}\n"
                 f"Next /fish or /autofish consumes this.")
    except Exception as exc:
        await _w(bot, user.id, f"❌ Failed to save forced drop: {exc!r}"[:249])


async def handle_forcedropfishitem(bot: BaseBot, user: User, args: list[str]) -> None:
    """
    /forcedropfishitem <username> <fish name>
    Forces a specific fish for that player's next /fish (owner-only).
    Alias: /forcefishdropfish
    """
    from modules.permissions import is_owner
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner-only command.")
        return
    if len(args) < 3:
        await _w(bot, user.id,
                 f"🎯 Force Fish Item\n"
                 f"Usage: /forcedropfishitem <username> <fish name>\n"
                 f"Example: /forcedropfishitem @Marion Aurora Koi")
        return
    target     = _norm_uname(args[1])
    fish_query = " ".join(args[2:]).strip().lower()
    fish_item  = _FISH_BY_ID.get(fish_query.replace(" ", "_"))
    if not fish_item:
        fish_item = _FISH_BY_NAME.get(fish_query)
    if not fish_item:
        matches = [f for f in FISH_CATALOG if fish_query in f["name"].lower()]
        if len(matches) == 1:
            fish_item = matches[0]
        elif len(matches) > 1:
            names = ", ".join(f["name"] for f in matches[:5])
            await _w(bot, user.id, f"Multiple matches: {names}")
            return
    if not fish_item:
        await _w(bot, user.id,
                 f"❌ Fish '{fish_query[:40]}' not found. Try /fishlist for names.")
        return
    fid = fish_item["fish_id"]
    try:
        drop_id = db.set_forced_fish_drop(target, "fish", fid, user.username)
        await _w(bot, user.id,
                 f"🎯 Forced Fish Drop Set\n"
                 f"Target: @{target}\n"
                 f"Type: fish | Value: {fish_item['name']}\n"
                 f"Status: pending | ID: {drop_id}\n"
                 f"Next /fish or /autofish consumes this.")
    except Exception as exc:
        await _w(bot, user.id, f"❌ Failed to save forced drop: {exc!r}"[:249])


async def handle_forcedropfishstatus(bot: BaseBot, user: User) -> None:
    """/forcedropfishstatus — list pending forced fish drops (owner-only)."""
    from modules.permissions import is_owner
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner-only command.")
        return
    try:
        drops = db.get_all_active_forced_fish_drops()
    except Exception as exc:
        await _w(bot, user.id, f"❌ DB error: {exc!r}"[:249])
        return
    if not drops:
        await _w(bot, user.id, "🎯 Forced Fish Drops\nNo pending forced fish drops.")
        return
    from datetime import datetime as _dt_fs, timezone as _tz_fs
    now   = _dt_fs.now(_tz_fs.utc)
    lines = [f"🎯 Forced Fish Drops ({len(drops)} pending)"]
    for i, d in enumerate(drops[:5], 1):
        exp_str = "no expiry"
        if d.get("expires_at"):
            try:
                exp_dt = _dt_fs.fromisoformat(d["expires_at"])
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=_tz_fs.utc)
                hrs = max(0, int((exp_dt - now).total_seconds() / 3600))
                exp_str = f"{hrs}h"
            except Exception:
                exp_str = d["expires_at"][:10]
        err_str = (d.get("last_error") or "none")[:25]
        lines.append(
            f"{i}. @{d['target_username']} | "
            f"{d['forced_type']}={d['forced_value']} | "
            f"id={d['id']} | exp={exp_str} | err={err_str}"
        )
    if len(drops) > 5:
        lines.append(f"+{len(drops) - 5} more. Use /forcedropfishdebug @user.")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_forcedropfishdebug(bot: BaseBot, user: User, args: list[str]) -> None:
    """/forcedropfishdebug <username> — forced drop debug info (owner-only)."""
    from modules.permissions import is_owner
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner-only command.")
        return
    if len(args) < 2:
        await _w(bot, user.id,
                 "Usage: /forcedropfishdebug <username>\n"
                 "Example: /forcedropfishdebug @Marion")
        return
    target = _norm_uname(args[1])
    try:
        active = db.get_active_forced_fish_drop("", target)
        lines  = [f"🎯 Force Fish Debug: @{target}", f"Norm: {target}"]
        if active:
            ft      = active["forced_type"]
            fv      = active["forced_value"]
            fid     = active["id"]
            uid_str = (active.get("target_user_id") or "—")[:20]
            exp_str = (active.get("expires_at") or "—")[:16]
            err_str = (active.get("last_error") or "none")[:50]
            lines.append(f"Pending by uname: YES (id={fid})")
            lines.append(f"Type: {ft} | Value: {fv}")
            lines.append(f"UID stored: {uid_str}")
            lines.append(f"Exp: {exp_str}")
            lines.append(f"Last error: {err_str}")
        else:
            lines.append("Pending by uname: NO")
            lines.append("Pending by uid: NO")
            lines.append("No active forced fish drop found.")
        await _w(bot, user.id, "\n".join(lines)[:249])
    except Exception as exc:
        await _w(bot, user.id, f"❌ Debug error: {exc!r}"[:249])


async def handle_clearforcedropfish(bot: BaseBot, user: User, args: list[str]) -> None:
    """/clearforcedropfish <username> — cancel pending forced fish drops (owner-only)."""
    from modules.permissions import is_owner
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner-only command.")
        return
    if len(args) < 2:
        await _w(bot, user.id,
                 f"🧹 Clear Force Fish\n"
                 f"Usage: /clearforcedropfish <username>\n"
                 f"Example: /clearforcedropfish @Marion")
        return
    target = _norm_uname(args[1])
    try:
        n = db.clear_forced_fish_drop_by_username(target, user.username)
        if n:
            await _w(bot, user.id,
                     f"🧹 Forced Fish Cleared\n"
                     f"Target: @{target}\n"
                     f"Cleared: {n} pending drop(s).")
        else:
            await _w(bot, user.id,
                     f"🧹 Forced Fish\n"
                     f"No pending forced fish drop found for @{target}.")
    except Exception as exc:
        await _w(bot, user.id, f"❌ Clear error: {exc!r}"[:249])
