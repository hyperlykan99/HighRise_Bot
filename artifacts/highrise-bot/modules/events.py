"""
modules/events.py
-----------------
Limited-time event reward system for the Highrise Mini Game Bot.

Event types (1 hour each):
  double_xp     — 2x XP from all games
  double_coins  — 2x coins from all games
  casino_hour   — +1 event pt per BJ/RBJ round
  tax_free_bank — no tax on /send transfers
  trivia_party  — bonus event pts for trivia wins
  shop_sale     — 20% off all shop items

Event points are a separate currency earned only during active events.
Casino Hour: +1 pt per BJ/RBJ round.
Trivia Party: +1 bonus pt per trivia/scramble/riddle win.

Commands (public):
  /event          — show active event & time left, or "No event active."
  /events         — list available event IDs
  /eventhelp      — show event commands
  /eventstatus    — detailed active event status
  /eventpoints    — your event point balance
  /eventshop      — event shop catalog
  /buyevent <id>  — purchase an event shop item

Commands (manager/admin/owner):
  /startevent <event_id>  — start named event for 1 hour
  /stopevent              — stop the active event immediately
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

from highrise import BaseBot, User

import database as db
from modules.permissions import can_manage_economy, is_owner


# ---------------------------------------------------------------------------
# Event catalog
# ---------------------------------------------------------------------------

EVENTS: dict[str, dict] = {
    "double_xp": {
        "name": "Double XP",
        "desc": "Earn 2x XP from all games for 1 hour.",
    },
    "double_coins": {
        "name": "Double Coins",
        "desc": "Earn 2x coins from all games for 1 hour.",
    },
    "casino_hour": {
        "name": "Casino Hour",
        "desc": "+1 event point per BJ/RBJ round during this hour.",
    },
    "tax_free_bank": {
        "name": "Tax-Free Banking",
        "desc": "No tax on /send transfers for 1 hour.",
    },
    "trivia_party": {
        "name": "Trivia Party",
        "desc": "Bonus event points for trivia/scramble/riddle wins.",
    },
    "shop_sale": {
        "name": "Shop Sale",
        "desc": "20% off all shop items for 1 hour.",
    },
    "admins_blessing": {
        "name": "Admin's Blessing",
        "desc": "2x XP, 2x Coins, 2x Mining, Tax-Free, 20% Shop Sale, +1 event pt/round. "
                "All boosts active simultaneously!",
    },
    # ── 3.1K Room event types ────────────────────────────────────────────────
    "mining_rush": {
        "name":       "Mining Rush",
        "emoji":      "⛏️",
        "desc":       "+2 mining luck! Better ore drops for all miners.",
        "event_type": "room",
        "boost_desc": "+2 mining luck",
    },
    "fishing_frenzy": {
        "name":       "Fishing Frenzy",
        "emoji":      "🎣",
        "desc":       "+2 fishing luck! Better catches for all fishers.",
        "event_type": "room",
        "boost_desc": "+2 fishing luck",
    },
    "collection_hunt": {
        "name":       "Collection Hunt",
        "emoji":      "📖",
        "desc":       "+2 mining & fishing luck! Hunt for rare collection items.",
        "event_type": "room",
        "boost_desc": "+2 mining & fishing luck",
    },
    "casino_night": {
        "name":       "Casino Night",
        "emoji":      "🎰",
        "desc":       "Bonus season points for casino activity. No odds changes!",
        "event_type": "room",
        "boost_desc": "+season pts for casino",
    },
    "trivia_rush": {
        "name":       "Trivia Rush",
        "emoji":      "🎯",
        "desc":       "Bonus XP and season points for every trivia win!",
        "event_type": "room",
        "boost_desc": "+XP & season pts for trivia",
    },
    # ── Mining-specific events (EventHost owned, stored in mining event table) ──
    "lucky_rush": {
        "name": "Lucky Rush",
        "desc": "Mining luck +25% — better Rare+ drops for all miners.",
        "event_type": "mining",
    },
    "heavy_ore_rush": {
        "name": "Heavy Ore Rush",
        "desc": "+25% heavier ore weights — bigger ores, higher values.",
        "event_type": "mining",
    },
    "ore_value_surge": {
        "name": "Ore Value Surge",
        "desc": "Ore value 1.5x — sell big!",
        "event_type": "mining",
    },
    "double_mxp": {
        "name": "Double Mining XP",
        "desc": "Mining XP 2x — level up faster.",
        "event_type": "mining",
    },
    "mining_haste": {
        "name": "Mining Haste",
        "desc": "Mine cooldown -25% — mine faster.",
        "event_type": "mining",
    },
    "legendary_rush": {
        "name": "Legendary Rush",
        "desc": "+50% Legendary+ drop chance.",
        "event_type": "mining",
    },
    "prismatic_hunt": {
        "name": "Prismatic Hunt",
        "desc": "+100% Prismatic drop chance — chase the rainbow.",
        "event_type": "mining",
    },
    "exotic_hunt": {
        "name": "Exotic Hunt",
        "desc": "+100% Exotic drop chance — rarest finds await.",
        "event_type": "mining",
    },
    "admins_mining_blessing": {
        "name": "Admin's Mining Blessing",
        "desc": (
            "All mining boosts: +50% luck & weight, 2x value & MXP, "
            "-25% cooldown, +50% Leg+, +100% Pris & Exotic."
        ),
        "event_type": "mining",
    },
    # ── New numbered events (12-event catalog) ──────────────────────────────
    "ultimate_mining_rush": {
        "name": "Ultimate Mining Rush",
        "desc": (
            "All mining boosts: +50% luck & weight, 2x value & MXP, "
            "-25% cooldown, +50% Leg+, 2x Pris & Exotic."
        ),
        "event_type": "mining",
    },
    "time_exp_boost": {
        "name": "Time EXP Boost",
        "desc": "2x time-in-room EXP for all players.",
        "event_type": "room",
    },
    "reward_drop": {
        "name": "Reward Drop",
        "desc": "Random coin/EXP rewards for active players.",
        "event_type": "room",
    },
    "event_points_boost": {
        "name": "Event Points Boost",
        "desc": "2x event points earned during this event.",
        "event_type": "room",
    },
    # ── Fishing-specific events (EventHost owned, stored in mining_events table) ─
    "lucky_tide": {
        "name": "Lucky Tide",
        "desc": "+25% fishing luck — better chance for higher rarity fish.",
        "event_type": "fishing",
    },
    "heavy_catch": {
        "name": "Heavy Catch",
        "desc": "+25% fish weight luck — bigger fish, heavier catches.",
        "event_type": "fishing",
    },
    "fish_value_surge": {
        "name": "Fish Value Surge",
        "desc": "1.5x fish sell value — sell big!",
        "event_type": "fishing",
    },
    "double_fxp": {
        "name": "Double FXP",
        "desc": "2x Fishing EXP — level up faster.",
        "event_type": "fishing",
    },
    "fishing_haste": {
        "name": "Fishing Haste",
        "desc": "-25% fishing cooldown — cast more often.",
        "event_type": "fishing",
    },
    "legendary_tide": {
        "name": "Legendary Tide",
        "desc": "+50% Legendary+ fish chance (Legendary/Mythic/Prismatic/Exotic).",
        "event_type": "fishing",
    },
    "prismatic_tide": {
        "name": "Prismatic Tide",
        "desc": "2x Prismatic fish chance — still very rare.",
        "event_type": "fishing",
    },
    "exotic_tide": {
        "name": "Exotic Tide",
        "desc": "2x Exotic fish chance — still extremely rare.",
        "event_type": "fishing",
    },
    "ultimate_fishing_rush": {
        "name": "Ultimate Fishing Rush",
        "desc": (
            "All fishing boosts: +50% luck & weight, 2x value & FXP, "
            "-25% cd, +50% Leg+, 2x Pris & Exotic."
        ),
        "event_type": "fishing",
    },
}

EVENT_DURATION = 3600  # seconds (1 hour)


# ---------------------------------------------------------------------------
# Event catalog — numbered list (12 events)
# ---------------------------------------------------------------------------

EVENT_CATALOG: list[dict] = [
    {"number": 1,  "event_id": "lucky_rush",
     "emoji": "🍀", "name": "Lucky Rush",          "event_type": "mining",
     "effect_desc": "+25% mining luck, better Rare+ drops",
     "default_duration": 30, "manual_only": False, "default_weight": 20, "cooldown_minutes": 60},
    {"number": 2,  "event_id": "heavy_ore_rush",
     "emoji": "⚖️", "name": "Heavy Ore Rush",       "event_type": "mining",
     "effect_desc": "+25% weight luck, heavier ores",
     "default_duration": 30, "manual_only": False, "default_weight": 20, "cooldown_minutes": 60},
    {"number": 3,  "event_id": "ore_value_surge",
     "emoji": "💰", "name": "Ore Value Surge",      "event_type": "mining",
     "effect_desc": "1.5x ore sell value",
     "default_duration": 30, "manual_only": False, "default_weight": 25, "cooldown_minutes": 90},
    {"number": 4,  "event_id": "double_mxp",
     "emoji": "⭐", "name": "Double MXP",           "event_type": "mining",
     "effect_desc": "2x mining EXP",
     "default_duration": 30, "manual_only": False, "default_weight": 30, "cooldown_minutes": 60},
    {"number": 5,  "event_id": "mining_haste",
     "emoji": "⏳", "name": "Mining Haste",         "event_type": "mining",
     "effect_desc": "-25% mine cooldown",
     "default_duration": 30, "manual_only": False, "default_weight": 25, "cooldown_minutes": 60},
    {"number": 6,  "event_id": "legendary_rush",
     "emoji": "🟡", "name": "Legendary Rush",       "event_type": "mining",
     "effect_desc": "+50% Legendary+ drop chance",
     "default_duration": 30, "manual_only": False, "default_weight": 10, "cooldown_minutes": 120},
    {"number": 7,  "event_id": "prismatic_hunt",
     "emoji": "🌈", "name": "Prismatic Hunt",       "event_type": "mining",
     "effect_desc": "2x Prismatic drop chance",
     "default_duration": 30, "manual_only": False, "default_weight": 5,  "cooldown_minutes": 180},
    {"number": 8,  "event_id": "exotic_hunt",
     "emoji": "🚨", "name": "Exotic Hunt",          "event_type": "mining",
     "effect_desc": "2x Exotic drop chance",
     "default_duration": 30, "manual_only": False, "default_weight": 2,  "cooldown_minutes": 360},
    {"number": 9,  "event_id": "time_exp_boost",
     "emoji": "⏰", "name": "Time EXP Boost",       "event_type": "room",
     "effect_desc": "2x time-in-room EXP",
     "default_duration": 30, "manual_only": False, "default_weight": 20, "cooldown_minutes": 90},
    {"number": 10, "event_id": "reward_drop",
     "emoji": "🎁", "name": "Reward Drop",          "event_type": "room",
     "effect_desc": "Random reward drops for players",
     "default_duration": 30, "manual_only": False, "default_weight": 15, "cooldown_minutes": 90},
    {"number": 11, "event_id": "event_points_boost",
     "emoji": "🏆", "name": "Event Points Boost",   "event_type": "room",
     "effect_desc": "2x event points earned",
     "default_duration": 30, "manual_only": False, "default_weight": 15, "cooldown_minutes": 90},
    {"number": 12, "event_id": "ultimate_mining_rush",
     "emoji": "🔥", "name": "Ultimate Mining Rush", "event_type": "mining",
     "effect_desc": "All mining boosts combined",
     "default_duration": 30, "manual_only": True,  "default_weight": 0,  "cooldown_minutes": 0},
    # ── Fishing events (13-21) ────────────────────────────────────────────────
    {"number": 13, "event_id": "lucky_tide",
     "emoji": "🌊", "name": "Lucky Tide",           "event_type": "fishing",
     "effect_desc": "+25% fishing luck, better Rare+ fish",
     "default_duration": 30, "manual_only": False, "default_weight": 20, "cooldown_minutes": 60},
    {"number": 14, "event_id": "heavy_catch",
     "emoji": "⚖️", "name": "Heavy Catch",           "event_type": "fishing",
     "effect_desc": "+25% fish weight luck, heavier fish",
     "default_duration": 30, "manual_only": False, "default_weight": 20, "cooldown_minutes": 60},
    {"number": 15, "event_id": "fish_value_surge",
     "emoji": "💰", "name": "Fish Value Surge",      "event_type": "fishing",
     "effect_desc": "1.5x fish sell value",
     "default_duration": 30, "manual_only": False, "default_weight": 25, "cooldown_minutes": 90},
    {"number": 16, "event_id": "double_fxp",
     "emoji": "⭐", "name": "Double FXP",            "event_type": "fishing",
     "effect_desc": "2x Fishing EXP",
     "default_duration": 30, "manual_only": False, "default_weight": 30, "cooldown_minutes": 60},
    {"number": 17, "event_id": "fishing_haste",
     "emoji": "⏳", "name": "Fishing Haste",         "event_type": "fishing",
     "effect_desc": "-25% fishing cooldown",
     "default_duration": 30, "manual_only": False, "default_weight": 25, "cooldown_minutes": 60},
    {"number": 18, "event_id": "legendary_tide",
     "emoji": "🐉", "name": "Legendary Tide",        "event_type": "fishing",
     "effect_desc": "+50% Legendary+ fish chance",
     "default_duration": 30, "manual_only": False, "default_weight": 10, "cooldown_minutes": 120},
    {"number": 19, "event_id": "prismatic_tide",
     "emoji": "🌈", "name": "Prismatic Tide",        "event_type": "fishing",
     "effect_desc": "2x Prismatic fish chance",
     "default_duration": 30, "manual_only": False, "default_weight": 5,  "cooldown_minutes": 180},
    {"number": 20, "event_id": "exotic_tide",
     "emoji": "🚨", "name": "Exotic Tide",           "event_type": "fishing",
     "effect_desc": "2x Exotic fish chance",
     "default_duration": 30, "manual_only": False, "default_weight": 2,  "cooldown_minutes": 360},
    {"number": 21, "event_id": "ultimate_fishing_rush",
     "emoji": "🔥", "name": "Ultimate Fishing Rush", "event_type": "fishing",
     "effect_desc": "All fishing boosts combined",
     "default_duration": 30, "manual_only": True,  "default_weight": 0,  "cooldown_minutes": 0},
]

_CATALOG_BY_ID:  dict[str, dict] = {e["event_id"]: e for e in EVENT_CATALOG}
_CATALOG_BY_NUM: dict[int,  dict] = {e["number"]:   e for e in EVENT_CATALOG}
_DEFAULT_AUTO_POOL: list[str] = [e["event_id"] for e in EVENT_CATALOG
                                  if not e["manual_only"]]


def _resolve_event_arg(arg: str) -> str | None:
    """Resolve staff arg (number or event_id) → event_id string, or None."""
    if arg.isdigit():
        entry = _CATALOG_BY_NUM.get(int(arg))
        return entry["event_id"] if entry else None
    return arg if arg in _CATALOG_BY_ID else None


# Short display names for 249-char safe output
_SHORT_DISPLAY: dict[str, str] = {
    "lucky_rush":             "Lucky Rush",
    "heavy_ore_rush":         "Heavy Ore",
    "ore_value_surge":        "Value Surge",
    "double_mxp":             "Double MXP",
    "mining_haste":           "Mine Haste",
    "legendary_rush":         "Legend Rush",
    "prismatic_hunt":         "Prism Hunt",
    "exotic_hunt":            "Exotic Hunt",
    "time_exp_boost":         "Time EXP",
    "reward_drop":            "Reward Drop",
    "event_points_boost":     "Points Boost",
    "ultimate_mining_rush":   "Ult Mine Rush",
    # Fishing events
    "lucky_tide":             "Lucky Tide",
    "heavy_catch":            "Heavy Catch",
    "fish_value_surge":       "Fish Value",
    "double_fxp":             "Double FXP",
    "fishing_haste":          "Fish Haste",
    "legendary_tide":         "Legend Tide",
    "prismatic_tide":         "Prism Tide",
    "exotic_tide":            "Exotic Tide",
    "ultimate_fishing_rush":  "Ult Fish Rush",
    # 3.1K room events
    "mining_rush":            "Mining Rush",
    "fishing_frenzy":         "Fishing Frenzy",
    "collection_hunt":        "Collection Hunt",
    "casino_night":           "Casino Night",
    "trivia_rush":            "Trivia Rush",
}


def _get_all_active_events() -> list[dict]:
    """
    Single source of truth for active events.
    Returns list of dicts with keys: event_id, name, emoji, ends_at, source.
    source is 'mining', 'fishing', or 'room'.
    """
    active: list[dict] = []
    try:
        mine_ev = db.get_active_mining_event()
        if mine_ev:
            eid    = mine_ev.get("event_id", "")
            info   = _CATALOG_BY_ID.get(eid, {})
            source = info.get("event_type", "mining")  # 'mining' or 'fishing'
            active.append({
                "event_id": eid,
                "name":     info.get("name") or EVENTS.get(eid, {}).get("name", eid),
                "emoji":    info.get("emoji", "🎣" if source == "fishing" else "⛏️"),
                "ends_at":  mine_ev.get("ends_at", ""),
                "source":   source,
            })
    except Exception:
        pass
    try:
        gen_ev = db.get_active_event()
        if gen_ev:
            eid  = gen_ev["event_id"]
            info = _CATALOG_BY_ID.get(eid, {})
            active.append({
                "event_id": eid,
                "name":     info.get("name") or EVENTS.get(eid, {}).get("name", eid),
                "emoji":    info.get("emoji", "🎪"),
                "ends_at":  gen_ev.get("expires_at", ""),
                "source":   "room",
            })
    except Exception:
        pass
    return active


# ---------------------------------------------------------------------------
# Event shop catalog  (kept from original)
# ---------------------------------------------------------------------------

EVENT_BADGES: dict[str, dict] = {
    "event_star_badge": {
        "display":     "🌟",
        "event_cost":  50,
        "item_type":   "badge",
        "description": "Event cosmetic",
        "price":       0,
    },
    "party_badge": {
        "display":     "🎉",
        "event_cost":  75,
        "item_type":   "badge",
        "description": "Event cosmetic",
        "price":       0,
    },
}

EVENT_TITLES: dict[str, dict] = {
    "casino_night_title": {
        "display":     "[Casino Night]",
        "event_cost":  100,
        "item_type":   "title",
        "description": "Event cosmetic",
        "price":       0,
    },
    "trivia_champ_title": {
        "display":     "[Trivia Champ]",
        "event_cost":  100,
        "item_type":   "title",
        "description": "Event cosmetic",
        "price":       0,
    },
    "og_guest_title": {
        "display":     "[OG Guest]",
        "event_cost":  250,
        "item_type":   "title",
        "description": "Event cosmetic",
        "price":       0,
    },
}

ALL_EVENT_ITEMS: dict[str, dict] = {**EVENT_BADGES, **EVENT_TITLES}


# ---------------------------------------------------------------------------
# Event effect helper  (imported by games, bank, shop, blackjack)
# ---------------------------------------------------------------------------

def _apply_mining_event_effects(base: dict, event_id: str) -> None:
    """
    Populate mining-specific effect keys in *base* for a given mining event_id.
    All mining keys must already exist in *base* with defaults before calling.
    """
    if event_id == "lucky_rush":
        base["mining_luck_boost"] = max(base["mining_luck_boost"], 0.25)
    elif event_id == "heavy_ore_rush":
        base["weight_luck_boost"] = max(base["weight_luck_boost"], 0.25)
    elif event_id == "ore_value_surge":
        base["ore_value_multiplier"] = max(base["ore_value_multiplier"], 1.5)
    elif event_id in ("double_mxp", "mining_double_mxp"):
        base["mxp_multiplier"] = max(base["mxp_multiplier"], 2.0)
    elif event_id == "mining_haste":
        base["cooldown_reduction"] = max(base["cooldown_reduction"], 0.25)
    elif event_id == "legendary_rush":
        base["legendary_plus_chance_boost"] = max(
            base["legendary_plus_chance_boost"], 0.50
        )
    elif event_id == "prismatic_hunt":
        base["prismatic_chance_boost"] = max(base["prismatic_chance_boost"], 1.0)
    elif event_id == "exotic_hunt":
        base["exotic_chance_boost"] = max(base["exotic_chance_boost"], 1.0)
    elif event_id in ("admins_mining_blessing", "ultimate_mining_rush"):
        base["mining_luck_boost"]            = max(base["mining_luck_boost"], 0.50)
        base["weight_luck_boost"]            = max(base["weight_luck_boost"], 0.50)
        base["ore_value_multiplier"]         = max(base["ore_value_multiplier"], 2.0)
        base["mxp_multiplier"]               = max(base["mxp_multiplier"], 2.0)
        base["cooldown_reduction"]           = max(base["cooldown_reduction"], 0.25)
        base["legendary_plus_chance_boost"]  = max(
            base["legendary_plus_chance_boost"], 0.50
        )
        base["prismatic_chance_boost"]       = max(base["prismatic_chance_boost"], 1.0)
        base["exotic_chance_boost"]          = max(base["exotic_chance_boost"], 1.0)


def _apply_fishing_event_effects(base: dict, event_id: str) -> None:
    """
    Populate fishing-specific effect keys in *base* for a given fishing event_id.
    All fishing keys must already exist in *base* with defaults before calling.
    """
    if event_id == "lucky_tide":
        base["fish_luck_boost"] = max(base["fish_luck_boost"], 0.25)
    elif event_id == "heavy_catch":
        base["fish_weight_luck_boost"] = max(base["fish_weight_luck_boost"], 0.25)
    elif event_id == "fish_value_surge":
        base["fish_value_multiplier"] = max(base["fish_value_multiplier"], 1.5)
    elif event_id == "double_fxp":
        base["fxp_multiplier"] = max(base["fxp_multiplier"], 2.0)
    elif event_id == "fishing_haste":
        base["fishing_cooldown_reduction"] = max(base["fishing_cooldown_reduction"], 0.25)
    elif event_id == "legendary_tide":
        base["legendary_plus_fish_chance_boost"] = max(
            base["legendary_plus_fish_chance_boost"], 0.50
        )
    elif event_id == "prismatic_tide":
        base["prismatic_fish_chance_boost"] = max(base["prismatic_fish_chance_boost"], 1.0)
    elif event_id == "exotic_tide":
        base["exotic_fish_chance_boost"] = max(base["exotic_fish_chance_boost"], 1.0)
    elif event_id == "ultimate_fishing_rush":
        base["fish_luck_boost"]                    = max(base["fish_luck_boost"], 0.50)
        base["fish_weight_luck_boost"]             = max(base["fish_weight_luck_boost"], 0.50)
        base["fish_value_multiplier"]              = max(base["fish_value_multiplier"], 2.0)
        base["fxp_multiplier"]                     = max(base["fxp_multiplier"], 2.0)
        base["fishing_cooldown_reduction"]         = max(base["fishing_cooldown_reduction"], 0.25)
        base["legendary_plus_fish_chance_boost"]   = max(
            base["legendary_plus_fish_chance_boost"], 0.50
        )
        base["prismatic_fish_chance_boost"]        = max(base["prismatic_fish_chance_boost"], 1.0)
        base["exotic_fish_chance_boost"]           = max(base["exotic_fish_chance_boost"], 1.0)


def get_event_effect() -> dict:
    """
    Return active event multipliers. Safe to call from any module.

    General keys (defaults when no event is active):
      coins                     float  1.0   — multiply game coin rewards
      xp                        float  1.0   — multiply game XP awards
      trivia_coins_pct          float  0.0   — extra % on trivia/scramble/riddle
      tax_free                  bool   False — zero /send tax when True
      shop_discount             float  0.0   — fraction off shop prices
      casino_bet_mult           float  1.0   — multiply BJ/RBJ max_bet

    Mining-specific keys:
      mining_luck_boost         float  0.0   — relative bonus to rare+ drop rates
      weight_luck_boost         float  0.0   — relative bonus to ore weight roll
      ore_value_multiplier      float  1.0   — multiply final ore value
      mxp_multiplier            float  1.0   — multiply MXP earned
      cooldown_reduction        float  0.0   — fraction to reduce cooldown (0.25=25%)
      legendary_plus_chance_boost float 0.0 — relative boost to legendary+ drops
      prismatic_chance_boost    float  0.0   — relative boost to prismatic drops
      exotic_chance_boost       float  0.0   — relative boost to exotic drops
      mining_boost              bool   False — legacy: Admin's Blessing 2x mining
    """
    info = db.get_active_event()
    base: dict = {
        "coins":                       1.0,
        "xp":                          1.0,
        "trivia_coins_pct":            0.0,
        "tax_free":                    False,
        "shop_discount":               0.0,
        "casino_bet_mult":             1.0,
        # Mining-specific keys
        "mining_luck_boost":           0.0,
        "weight_luck_boost":           0.0,
        "ore_value_multiplier":        1.0,
        "mxp_multiplier":              1.0,
        "cooldown_reduction":          0.0,
        "legendary_plus_chance_boost": 0.0,
        "prismatic_chance_boost":      0.0,
        "exotic_chance_boost":         0.0,
        "mining_boost":                False,
        # Room event effects
        "time_exp_multiplier":         1.0,
        "event_points_multiplier":     1.0,
        "reward_drop_active":          False,
        # Fishing-specific keys
        "fish_luck_boost":                   0.0,
        "fish_weight_luck_boost":            0.0,
        "fish_value_multiplier":             1.0,
        "fxp_multiplier":                    1.0,
        "fishing_cooldown_reduction":        0.0,
        "legendary_plus_fish_chance_boost":  0.0,
        "prismatic_fish_chance_boost":       0.0,
        "exotic_fish_chance_boost":          0.0,
        # 3.1K room event flags
        "casino_night_active":               False,
        "trivia_rush_active":                False,
    }
    if info:
        eid = info["event_id"]
        if eid == "double_xp":
            base["xp"] = 2.0
        elif eid == "double_coins":
            base["coins"] = 2.0
        elif eid == "casino_hour":
            base["casino_bet_mult"] = 2.0
        elif eid == "tax_free_bank":
            base["tax_free"] = True
        elif eid == "trivia_party":
            base["trivia_coins_pct"] = 0.5
        elif eid == "shop_sale":
            base["shop_discount"] = 0.20
        elif eid == "admins_blessing":
            base["xp"]               = 2.0
            base["coins"]            = 2.0
            base["tax_free"]         = True
            base["shop_discount"]    = 0.20
            base["casino_bet_mult"]  = 2.0
            base["trivia_coins_pct"] = 0.50
            base["mining_boost"]     = True
        elif eid == "mining_rush":
            base["mining_luck_boost"] = 0.2          # → event_luck = 2
        elif eid == "fishing_frenzy":
            base["fish_luck_boost"]   = 0.2
        elif eid == "collection_hunt":
            base["mining_luck_boost"] = 0.2
            base["fish_luck_boost"]   = 0.2
        elif eid == "casino_night":
            base["casino_night_active"] = True
        elif eid == "trivia_rush":
            base["trivia_rush_active"]  = True
            base["xp"]                  = max(base.get("xp", 1.0), 1.5)
        elif eid == "time_exp_boost":
            base["time_exp_multiplier"] = 2.0
        elif eid == "event_points_boost":
            base["event_points_multiplier"] = 2.0
        elif eid == "reward_drop":
            base["reward_drop_active"] = True

    # Apply active mining or fishing event effects
    try:
        mine_ev = db.get_active_mining_event()
        if mine_ev:
            eid = mine_ev.get("event_id", "")
            if eid in _FISHING_EVENT_IDS:
                _apply_fishing_event_effects(base, eid)
            else:
                _apply_mining_event_effects(base, eid)
    except Exception:
        pass

    return base


# ---------------------------------------------------------------------------
# Asyncio timer
# ---------------------------------------------------------------------------

_event_task: asyncio.Task | None = None


def _cancel_event_task() -> None:
    global _event_task
    if _event_task and not _event_task.done():
        _event_task.cancel()
    _event_task = None


async def _event_timer(
    bot: BaseBot, event_id: str, sleep_seconds: float = EVENT_DURATION
) -> None:
    """
    Auto-stop the event after sleep_seconds.
    Fires a halfway reminder and a 10-minute warning (if event is long enough).
    Accepts a shorter duration when resuming after a bot restart.
    """
    name = EVENTS.get(event_id, {}).get("name", event_id)
    try:
        remaining = sleep_seconds

        # Halfway reminder (only if event is longer than 20 minutes)
        if remaining > 1200:
            half = remaining / 2
            await asyncio.sleep(half)
            remaining -= half
            half_m = max(1, int(remaining // 60))
            try:
                await bot.highrise.chat(
                    f"⏰ {name} — halfway! {half_m}m left. Keep playing!"[:249]
                )
            except Exception:
                pass

        # 10-minute warning (only if more than 11 min remain to avoid double-fire)
        if remaining > 660:
            await asyncio.sleep(remaining - 600)
            remaining = 600
            try:
                await bot.highrise.chat(
                    f"⏰ {name} ends in 10 minutes! Don't miss out!"[:249]
                )
            except Exception:
                pass

        await asyncio.sleep(remaining)
        db.clear_active_event()
        print(f"[EVENTS] Timer expired: '{event_id}' ended naturally.")
        try:
            await bot.highrise.chat(
                f"🎉 {name} has ended. Thanks for playing! Use !events for next."[:249]
            )
        except Exception as exc:
            print(f"[EVENTS] timer chat error: {exc}")
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


def _time_remaining(expires_at_iso: str) -> str:
    try:
        expires = datetime.fromisoformat(expires_at_iso)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        secs = max(0, int((expires - datetime.now(timezone.utc)).total_seconds()))
        m, s = divmod(secs, 60)
        h, m = divmod(m, 60)
        return f"{h}h {m}m" if h else f"{m}m {s}s"
    except Exception:
        return "?"


# ---------------------------------------------------------------------------
# Public commands
# ---------------------------------------------------------------------------

async def _handle_event_active(bot: BaseBot, user: User) -> None:
    """Show currently active event in the polished 3.2E format."""
    active = _get_all_active_events()
    if not active:
        await _w(bot, user.id,
                 "🎉 Active Event\nNo event active right now.\n"
                 "Use !event schedule for upcoming events.")
        return
    for ev in active[:2]:
        left      = _time_remaining(ev["ends_at"]) if ev["ends_at"] else "?"
        info      = EVENTS.get(ev["event_id"], {})
        boost_txt = info.get("boost_desc") or info.get("desc", "")
        boost_txt = boost_txt[:60] if boost_txt else "Active"
        emoji     = ev.get("emoji", "🎪")
        await _w(bot, user.id,
                 (f"🎉 Active Event\n"
                  f"{emoji} {ev['name']}\n"
                  f"Bonus: {boost_txt}\n"
                  f"Ends in: {left}\n"
                  f"Try !event schedule for upcoming.")[:249])


async def _handle_event_schedule(bot: BaseBot, user: User) -> None:
    """Show the weekly event schedule stored in room_settings."""
    sched_str = db.get_room_setting(
        "event_weekly_schedule",
        "Mon:mining_rush,Wed:fishing_frenzy,Fri:double_xp,Sun:collection_hunt",
    )
    lines = ["📅 Event Schedule"]
    for entry in sched_str.split(","):
        entry = entry.strip()
        if ":" not in entry:
            continue
        day, rest = entry.split(":", 1)
        rest = rest.strip()
        # rest may be "event_id" or "event_id@HH:MM"
        if "@" in rest:
            eid, time_part = rest.split("@", 1)
        else:
            eid, time_part = rest, ""
        eid  = eid.strip()
        name = EVENTS.get(eid, {}).get("name", None)
        if not name:
            # skip raw internal keys that aren't in EVENTS
            name = _SHORT_DISPLAY.get(eid, eid)
        time_disp = f" {time_part.strip()}" if time_part.strip() else ""
        lines.append(f"{day.strip()}{time_disp} — {name}")
    if len(lines) == 1:
        lines.append("No schedule set yet.\nAdmin: !eventadmin set schedule <day> <event>")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_event(bot: BaseBot, user: User,
                       args: list[str] | None = None) -> None:
    """/event [active|schedule|next|number [mins]] — show or start events."""
    sub = (args[1].lower() if args and len(args) >= 2 else "")
    if sub == "active":
        await _handle_event_active(bot, user)
        return
    if sub == "schedule":
        await _handle_event_schedule(bot, user)
        return
    if sub in ("next", "nextevent"):
        await handle_nextevent(bot, user)
        return

    # /event <number> [mins] — staff shortcut to start by catalog number
    if args and len(args) >= 2 and args[1].isdigit():
        if can_manage_economy(user.username):
            await handle_startevent(bot, user, args)
        else:
            await _w(bot, user.id, "Manager/admin/owner only to start events.")
        return

    # Public: show current active events
    mine_ev = db.get_active_mining_event()
    gen_ev  = db.get_active_event()

    if mine_ev:
        ev   = EVENTS.get(mine_ev["event_id"], {})
        name = ev.get("name", mine_ev["event_id"])
        left = _time_remaining(mine_ev["ends_at"])
        await _w(bot, user.id,
                 f"⛏️ Mining Event: {name}\n"
                 f"{ev.get('desc','')[:80]}\n"
                 f"⏰ Ends in: {left}")
        return

    if gen_ev:
        ev   = EVENTS.get(gen_ev["event_id"], {})
        name = ev.get("name", gen_ev["event_id"])
        left = _time_remaining(gen_ev["expires_at"])
        await _w(bot, user.id,
                 f"🎪 Active: {name}\n"
                 f"{ev.get('desc','')[:80]}\n"
                 f"⏰ Time left: {left}")
        return

    await _w(bot, user.id,
             "No event active. Staff can start one with /startevent <#>.")


async def handle_events(bot: BaseBot, user: User) -> None:
    """!events — overview of active and upcoming events."""
    active      = _get_all_active_events()
    active_left = ""
    if active:
        ev          = active[0]
        active_str  = f"{ev.get('emoji','🎪')} {ev['name']}"
        if ev["ends_at"]:
            active_left = f" ({_time_remaining(ev['ends_at'])} left)"
    else:
        active_str = "None"

    next_id   = db.get_auto_event_setting_str("next_event_id", "")
    next_at   = db.get_auto_event_setting_str("next_event_at", "")
    next_name = ""
    if next_id:
        next_name = (
            _CATALOG_BY_ID.get(next_id, {}).get("name", "")
            or EVENTS.get(next_id, {}).get("name", "")
        )
    if next_at and not active:
        left     = _time_remaining(next_at)
        next_str = f"{next_name} in {left}" if next_name else f"in {left}"
    elif next_name and active:
        next_str = f"{next_name} (after current)"
    else:
        next_str = "see !event schedule"

    lines = [
        "🎉 Room Events",
        f"Active: {active_str}{active_left}",
        f"Next: {next_str}",
        "!event active | !event schedule",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_nextevent(bot: BaseBot, user: User) -> None:
    """!nextevent / !event next — show the next planned or active event."""
    active = _get_all_active_events()
    if active:
        ev   = active[0]
        left = _time_remaining(ev["ends_at"]) if ev["ends_at"] else "?"
        await _w(bot, user.id, (
            f"⏭️ Next Event\n"
            f"Active now: {ev.get('emoji','🎪')} {ev['name']}\n"
            f"Ends in: {left}\n"
            f"!event schedule for upcoming."
        )[:249])
        return

    next_id = db.get_auto_event_setting_str("next_event_id", "")
    next_at = db.get_auto_event_setting_str("next_event_at", "")
    if next_at:
        left      = _time_remaining(next_at)
        next_name = ""
        if next_id:
            entry     = _CATALOG_BY_ID.get(next_id, {})
            next_name = entry.get("name") or EVENTS.get(next_id, {}).get("name", "")
        if next_name:
            await _w(bot, user.id, (
                f"⏭️ Next Event\n"
                f"{next_name}\n"
                f"Starts in: {left}\n"
                f"Watch chat for the announcement!"
            )[:249])
        else:
            await _w(bot, user.id, (
                f"⏭️ Next Event\n"
                f"Starts in: {left}\n"
                f"Watch chat for the announcement!"
            )[:249])
        return

    try:
        ag = db.get_auto_game_settings()
        if ag.get("auto_minigames_enabled", 1):
            mg_min = ag.get("auto_minigame_interval", 10)
            await _w(bot, user.id, (
                f"⏭️ Next Event\n"
                f"🎲 Mini Game coming soon (~{mg_min}m)\n"
                f"Type !answer when trivia starts."
            )[:249])
            return
    except Exception:
        pass

    await _w(bot, user.id, (
        "⏭️ Next Event\n"
        "No event scheduled right now.\n"
        "Use !event schedule to see the weekly plan."
    )[:249])


async def handle_eventhelp(bot: BaseBot, user: User) -> None:
    """/eventhelp — show event command reference."""
    await _w(bot, user.id,
             "🎉 Events\n"
             "!events — overview\n"
             "!event active — current boost\n"
             "!event schedule — upcoming\n"
             "!event next — next event\n"
             "Events boost luck, XP, or season points.")


# ---------------------------------------------------------------------------
# Friendly name resolver  (spec Part 18 — no raw event_id keys in output)
# ---------------------------------------------------------------------------

_FRIENDLY_INPUT_MAP: dict[str, str] = {
    "miningrush":      "mining_rush",
    "fishingfrenzy":   "fishing_frenzy",
    "doublexp":        "double_xp",
    "collectionhunt":  "collection_hunt",
    "casinonight":     "casino_night",
    "triviarush":      "trivia_rush",
    "doublemxp":       "double_mxp",
    "doublefxp":       "double_fxp",
    "luckyrush":       "lucky_rush",
    "luckyRush":       "lucky_rush",
    "heavyorerush":    "heavy_ore_rush",
    "orevaluesurge":   "ore_value_surge",
    "mininghaste":     "mining_haste",
    "legendaryrush":   "legendary_rush",
    "prismatichunt":   "prismatic_hunt",
    "exotichunt":      "exotic_hunt",
    "luckytide":       "lucky_tide",
    "heavycatch":      "heavy_catch",
    "fishvaluesurge":  "fish_value_surge",
    "fishinghaste":    "fishing_haste",
    "legendarytide":   "legendary_tide",
    "prismatictide":   "prismatic_tide",
    "exotictide":      "exotic_tide",
}


def _resolve_friendly_event(arg: str) -> str | None:
    """Resolve a friendly/compact arg to an event_id or None."""
    clean = arg.lower().replace(" ", "").replace("-", "").replace("_", "")
    # Try compact map first
    if clean in _FRIENDLY_INPUT_MAP:
        return _FRIENDLY_INPUT_MAP[clean]
    # Try direct EVENTS key
    if arg in EVENTS:
        return arg
    # Try catalog resolver (number or event_id)
    return _resolve_event_arg(arg)


# ---------------------------------------------------------------------------
# Schedule helper
# ---------------------------------------------------------------------------

_DAY_MAP: dict[str, str] = {
    "mon": "Mon", "monday": "Mon",
    "tue": "Tue", "tuesday": "Tue",
    "wed": "Wed", "wednesday": "Wed",
    "thu": "Thu", "thursday": "Thu",
    "fri": "Fri", "friday": "Fri",
    "sat": "Sat", "saturday": "Sat",
    "sun": "Sun", "sunday": "Sun",
}


async def _handle_set_schedule(
    bot: BaseBot, user: User, parts: list[str]
) -> None:
    """Process: !eventadmin set schedule <day> <event_id> [HH:MM]"""
    if len(parts) < 2:
        await _w(bot, user.id,
                 "Usage: !eventadmin set schedule <day> <event> [HH:MM]\n"
                 "Days: mon/tue/wed/thu/fri/sat/sun")
        return

    day_raw = parts[0].lower()
    eid_raw = parts[1]
    time_raw = parts[2].strip() if len(parts) > 2 else ""

    day = _DAY_MAP.get(day_raw)
    if not day:
        await _w(bot, user.id,
                 f"Unknown day: {day_raw}\nUse mon/tue/wed/thu/fri/sat/sun.")
        return

    event_id = _resolve_friendly_event(eid_raw)
    if not event_id or event_id not in EVENTS:
        await _w(bot, user.id,
                 f"Unknown event: {eid_raw}\nUse !eventlist to see options.")
        return

    # Validate time if provided (require HH:MM format)
    time_str = ""
    if time_raw:
        import re as _re
        if _re.match(r"^\d{1,2}:\d{2}$", time_raw):
            time_str = time_raw
        else:
            await _w(bot, user.id,
                     "Time must be HH:MM (24h format), e.g. 19:00.")
            return

    # Read existing schedule, update entry for this day
    sched = db.get_room_setting("event_weekly_schedule", "")
    entries: dict[str, str] = {}
    for e in sched.split(","):
        e = e.strip()
        if ":" in e:
            d, rest = e.split(":", 1)
            entries[d.strip()] = rest.strip()

    entry_val = f"{event_id}@{time_str}" if time_str else event_id
    entries[day] = entry_val
    new_sched = ",".join(f"{d}:{v}" for d, v in entries.items())
    db.set_room_setting("event_weekly_schedule", new_sched)

    name = EVENTS.get(event_id, {}).get("name", event_id)
    time_disp = f" {time_str}" if time_str else ""
    await _w(bot, user.id, f"✅ Schedule Updated\n{day}{time_disp} — {name}")


# ---------------------------------------------------------------------------
# !eventadmin — unified admin command
# ---------------------------------------------------------------------------

async def handle_eventadmin(
    bot: BaseBot, user: User, args: list[str]
) -> None:
    """!eventadmin [status|start|stop|schedule|set schedule <day> <event> [HH:MM]]"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "🔒 Manager/admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else ""

    # Menu
    if not sub or sub in ("help", "menu"):
        await _w(bot, user.id, (
            "🎉 Event Admin\n"
            "!eventadmin status\n"
            "!eventadmin start <id|#> [mins]\n"
            "!eventadmin stop\n"
            "!eventadmin schedule\n"
            "!eventadmin set schedule <day> <event>"
        )[:249])
        return

    if sub == "status":
        active = _get_all_active_events()
        if not active:
            await _w(bot, user.id,
                     "🎉 Event Admin\nNo active event.\n"
                     "Use !eventadmin start <id> to start one.")
        else:
            ev    = active[0]
            left  = _time_remaining(ev["ends_at"]) if ev["ends_at"] else "?"
            info  = EVENTS.get(ev["event_id"], {})
            boost = info.get("boost_desc") or info.get("desc", "")
            boost = boost[:55] if boost else "Active"
            await _w(bot, user.id, (
                f"🎉 Event Admin\n"
                f"Active: {ev.get('emoji','🎪')} {ev['name']}\n"
                f"Boost: {boost}\n"
                f"Ends in: {left}\n"
                f"Stop: !eventadmin stop"
            )[:249])
        return

    if sub == "start":
        # !eventadmin start <id|#> [mins]
        new_args = [args[0]] + args[2:]
        await handle_startevent(bot, user, new_args)
        return

    if sub == "stop":
        await handle_stopevent(bot, user, args)
        return

    if sub == "schedule":
        # !eventadmin schedule  OR  !eventadmin schedule set <day> <event> [time]
        if len(args) > 2 and args[2].lower() == "set":
            await _handle_set_schedule(bot, user, args[3:])
        else:
            await _handle_event_schedule(bot, user)
        return

    if sub == "set" and len(args) > 2 and args[2].lower() == "schedule":
        # !eventadmin set schedule <day> <event> [time]
        await _handle_set_schedule(bot, user, args[3:])
        return

    await _w(bot, user.id,
             "Unknown subcommand.\nUse !eventadmin for the menu.")


async def handle_eventstatus(bot: BaseBot, user: User) -> None:
    """!eventstatus — show event system status and any active events."""
    try:
        ae    = db.get_auto_event_settings()
        ag    = db.get_auto_game_settings()
        ae_on = bool(ae.get("auto_events_enabled", 1))
        mg_on = bool(ag.get("auto_minigames_enabled", 1))
    except Exception:
        ae_on = mg_on = None
    try:
        pt_on = db.get_room_setting("party_tip_enabled", "1") == "1"
    except Exception:
        pt_on = None
    ae_str = "ON"  if ae_on else ("OFF"     if ae_on is not None else "Unknown")
    mg_str = "ON"  if mg_on else ("OFF"     if mg_on is not None else "Unknown")
    pt_str = "ON"  if pt_on else ("OFF"     if pt_on is not None else "Unknown")
    next_id = db.get_auto_event_setting_str("next_event_id", "")
    next_at = db.get_auto_event_setting_str("next_event_at", "")
    active  = _get_all_active_events()
    if active:
        next_str = f"{active[0]['name']} (now)"
    elif next_at:
        left      = _time_remaining(next_at)
        next_name = _CATALOG_BY_ID.get(next_id, {}).get("name", "") if next_id else ""
        next_str  = f"{next_name} in {left}" if next_name else f"in {left}"
    else:
        next_str = "none"
    await _w(bot, user.id, (
        f"📡 Event Status\n"
        f"Activity Loop: {ae_str}\n"
        f"Mini Games: {mg_str}\n"
        f"Party Tips: {pt_str}\n"
        f"Next: {next_str}"
    )[:249])
    if active:
        pts   = db.get_event_points(user.id)
        lines = ["✨ Active Events"]
        for ev in active:
            left = _time_remaining(ev["ends_at"]) if ev["ends_at"] else "?"
            lines.append(f"{ev['emoji']} {ev['name']} — {left}")
        lines.append(f"Your pts: {pts}")
        await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_eventloop(bot: BaseBot, user: User, args: list[str]) -> None:
    """!eventloop [on|off|status] — view or toggle the auto-event loop."""
    sub = args[1].strip().lower() if len(args) > 1 else "status"
    if sub == "status":
        try:
            ae       = db.get_auto_event_settings()
            ae_on    = bool(ae.get("auto_events_enabled", 1))
            interval = ae.get("auto_event_interval", 60)
            next_at  = db.get_auto_event_setting_str("next_event_at", "")
            next_str = _time_remaining(next_at) if next_at else "soon"
            await _w(bot, user.id, (
                f"📡 Event Loop\n"
                f"Status: {'ON' if ae_on else 'OFF'}\n"
                f"Interval: {interval}m\n"
                f"Next: {next_str}"
            )[:249])
        except Exception as exc:
            await _w(bot, user.id, f"📡 Event Loop — unable to read status: {exc}"[:249])
        return
    if not is_owner(user.username):
        await _w(bot, user.id, "🔒 Owner only.")
        return
    if sub in ("on", "enable"):
        db.set_auto_event_setting("auto_events_enabled", 1)
        await _w(bot, user.id, "✅ Event loop enabled.")
    elif sub in ("off", "disable"):
        db.set_auto_event_setting("auto_events_enabled", 0)
        await _w(bot, user.id, "✅ Event loop disabled.")
    else:
        await _w(bot, user.id, "Usage: !eventloop [on|off|status]")


# ---------------------------------------------------------------------------
# Staff commands
# ---------------------------------------------------------------------------

async def handle_startevent(bot: BaseBot, user: User, args: list[str]) -> None:
    """/startevent <id|number> [minutes] — manager+; optional custom duration."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    if len(args) < 2:
        await _w(bot, user.id,
                 "Usage: !startevent <id or #> [mins]\n"
                 "Use !eventlist to see numbered catalog.")
        return

    raw      = args[1].lower()
    event_id = _resolve_event_arg(raw) or raw  # fallback to raw for legacy IDs
    if event_id not in EVENTS:
        await _w(bot, user.id,
                 f"Unknown event: {raw}\n"
                 "Use !eventlist to see the catalog.")
        return

    dur_mins = 30
    if len(args) >= 3 and args[2].isdigit():
        dur_mins = int(args[2])
        if not (1 <= dur_mins <= 480):
            await _w(bot, user.id, "Duration must be 1-480 minutes.")
            return
    duration = dur_mins * 60

    ev        = EVENTS[event_id]
    name      = ev["name"]
    dur_label = f"{dur_mins}min"

    # Route mining events through mining_events table
    ev_type = ev.get("event_type", "room")
    if ev_type in ("mining", "fishing") or event_id in (_MINING_EVENT_IDS | _FISHING_EVENT_IDS):
        db.start_mining_event(event_id, user.username, dur_mins)
        ann_emoji = "🎣" if ev_type == "fishing" else "⛏️"
        try:
            await bot.highrise.chat(
                f"{ann_emoji} {name} for {dur_label}! {ev.get('desc','')[:80]}"[:249]
            )
        except Exception as exc:
            print(f"[EVENTS] startevent ({ev_type}) announce error: {exc}")
        await _w(bot, user.id, f"✅ Started {ev_type} event: {name} for {dur_label}.")
        # Log to history
        try:
            db.add_event_history_entry(event_id, name, user.username, False, duration)
        except Exception:
            pass
        return

    # Room event: goes through event_settings
    _cancel_event_task()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=duration)
    ).isoformat()
    db.set_active_event(event_id, expires_at)

    global _event_task
    _event_task = asyncio.create_task(_event_timer(bot, event_id, duration))

    try:
        boost_desc = ev.get("boost_desc", "")
        ev_emoji   = ev.get("emoji", "🎪")
        if boost_desc:
            await bot.highrise.chat(
                (f"🎉 Event Started!\n"
                 f"{ev_emoji} {name} is active for {dur_label}.\n"
                 f"Bonus: {boost_desc}.\nUse !events.")[:249]
            )
        else:
            await bot.highrise.chat(
                (f"🎪 {name} is LIVE for {dur_label}! "
                 f"{ev.get('desc','')[:80]} "
                 "Use !eventshop!")[:249]
            )
    except Exception as exc:
        print(f"[EVENTS] startevent announce error: {exc}")

    # Log to history
    try:
        db.add_event_history_entry(event_id, name, user.username, False, duration)
    except Exception:
        pass


async def handle_adminsblessing(bot: BaseBot, user: User, args: list[str]) -> None:
    """/adminsblessing [minutes] — shortcut for /startevent admins_blessing."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    mins_arg = args[1] if len(args) >= 2 else "60"
    await handle_startevent(bot, user, [args[0], "admins_blessing", mins_arg])


async def handle_stopevent(bot: BaseBot, user: User,
                          args: list[str] | None = None) -> None:
    """/stopevent [id|number|all] — manager+, stop the active event."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    target = (args[1].lower() if args and len(args) >= 2 else "all")

    stopped_any = False

    # Stop mining event if target is "all", "mine", "mining", or a mining event id/number
    mine_ev = db.get_active_mining_event()
    if mine_ev:
        mine_eid = mine_ev.get("event_id", "")
        resolved = _resolve_event_arg(target) or target
        should_stop_mine = (
            target in ("all", "mine", "mining")
            or resolved == mine_eid
            or target == mine_eid
        )
        if should_stop_mine:
            db.stop_mining_event()
            mine_name = EVENTS.get(mine_eid, {}).get("name", mine_eid)
            try:
                await bot.highrise.chat(f"🛑 {mine_name} stopped by staff.")
            except Exception:
                pass
            stopped_any = True

    # Stop room event if target is "all" or a matching room event id/number
    gen_ev = db.get_active_event()
    if gen_ev:
        gen_eid  = gen_ev["event_id"]
        resolved = _resolve_event_arg(target) or target
        should_stop_gen = (
            target == "all"
            or resolved == gen_eid
            or target == gen_eid
        )
        if should_stop_gen:
            _cancel_event_task()
            db.clear_active_event()
            gen_name = EVENTS.get(gen_eid, {}).get("name", gen_eid)
            try:
                await bot.highrise.chat(f"🛑 {gen_name} stopped by staff.")
            except Exception:
                pass
            stopped_any = True

    if stopped_any:
        await _w(bot, user.id, "✅ Event(s) stopped.")
    else:
        await _w(bot, user.id, "No active event to stop.")


# ---------------------------------------------------------------------------
# /aeskip — skip/cancel current event, keep scheduler running  (manager+)
# ---------------------------------------------------------------------------

async def handle_aeskip(
    bot: BaseBot, user: User, args: list[str] | None = None
) -> None:
    """/aeskip — skip/cancel the current active event. Scheduler keeps running."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    gen_ev  = db.get_active_event()
    mine_ev = db.get_active_mining_event()

    if not gen_ev and not mine_ev:
        await _w(bot, user.id, "⏭️ Auto Event Skip\nNo active event to skip.")
        return

    skipped_name: str | None = None

    if gen_ev:
        eid = gen_ev["event_id"]
        skipped_name = EVENTS.get(eid, {}).get("name", eid)
        _cancel_event_task()
        db.clear_active_event()
        # Mark most recent active history row as skipped
        hist = db.get_event_history(limit=1)
        if hist and hist[0]["status"] == "active":
            db.close_event_history_as_skipped(hist[0]["id"], user.username)
        try:
            await bot.highrise.chat(
                f"⏭️ {skipped_name} ended early by staff."[:249]
            )
        except Exception:
            pass

    if mine_ev:
        mine_eid  = mine_ev.get("event_id", "")
        mine_name = EVENTS.get(mine_eid, {}).get("name", mine_eid)
        if not skipped_name:
            skipped_name = mine_name
        db.stop_mining_event()
        try:
            await bot.highrise.chat(
                f"⏭️ {mine_name} ended early by staff."[:249]
            )
        except Exception:
            pass

    next_id   = db.get_auto_event_setting_str("next_event_id", "")
    next_at   = db.get_auto_event_setting_str("next_event_at", "")
    next_name = _CATALOG_BY_ID.get(next_id, {}).get("name", next_id) if next_id else "?"
    time_str  = _time_remaining(next_at) if next_at else "?"

    await _w(
        bot, user.id,
        f"⏭️ Auto Event Skipped\n"
        f"Skipped: {skipped_name}\n"
        f"Auto Events remain ON.\n"
        f"Next Event: {next_name} in {time_str}"
    )


# ---------------------------------------------------------------------------
# /aeskipnext — skip current event and immediately start the next  (manager+)
# ---------------------------------------------------------------------------

async def handle_aeskipnext(
    bot: BaseBot, user: User, args: list[str] | None = None
) -> None:
    """/aeskipnext — skip current event and start the next saved event immediately."""
    import random as _random

    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    gen_ev  = db.get_active_event()
    mine_ev = db.get_active_mining_event()
    skipped_name: str | None = None

    # ── Stop current event ──────────────────────────────────────────────────
    if gen_ev:
        eid = gen_ev["event_id"]
        skipped_name = EVENTS.get(eid, {}).get("name", eid)
        _cancel_event_task()
        db.clear_active_event()
        hist = db.get_event_history(limit=1)
        if hist and hist[0]["status"] == "active":
            db.close_event_history_as_skipped(hist[0]["id"], user.username)

    if mine_ev:
        mine_eid  = mine_ev.get("event_id", "")
        mine_name = EVENTS.get(mine_eid, {}).get("name", mine_eid)
        if not skipped_name:
            skipped_name = mine_name
        db.stop_mining_event()

    # ── Find next event ─────────────────────────────────────────────────────
    settings = db.get_auto_event_settings()
    dur      = settings.get("auto_event_duration", 30)
    next_id  = db.get_auto_event_setting_str("next_event_id", "")

    if not next_id:
        eligible = db.get_eligible_pool_events()
        if eligible:
            next_id = _random.choice(eligible)["event_id"]

    if not next_id:
        msg = f"⏭️ Skipped: {skipped_name or 'None'}\nNo event available in pool."
        await _w(bot, user.id, msg)
        return

    next_entry = _CATALOG_BY_ID.get(next_id, {})
    next_name  = next_entry.get("name", next_id)

    # ── Start the next event ────────────────────────────────────────────────
    await handle_startevent(bot, user, ["startevent", next_id, str(dur)])

    # ── Pick a new next event for the queue ─────────────────────────────────
    eligible2     = db.get_eligible_pool_events()
    new_next_name = "?"
    if eligible2:
        candidates   = [e for e in eligible2 if e["event_id"] != next_id] or eligible2
        new_next_id  = _random.choice(candidates)["event_id"]
        db.set_auto_event_setting_str("next_event_id", new_next_id)
        db.set_auto_event_setting_str("next_event_source", "auto")
        new_next_name = _CATALOG_BY_ID.get(new_next_id, {}).get("name", new_next_id)
    else:
        db.set_auto_event_setting_str("next_event_id", "")

    await _w(
        bot, user.id,
        f"⏭️ Auto Event Skip Next\n"
        f"Skipped: {skipped_name or 'None'}\n"
        f"Started: {next_name}\n"
        f"New Next: {new_next_name}"
    )


# ---------------------------------------------------------------------------
# Event resume / autogame status helpers
# ---------------------------------------------------------------------------

async def handle_eventresume(bot: BaseBot, user: User) -> None:
    """/eventresume — re-announce the active event if one is running."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    info = db.get_active_event()
    if not info:
        await _w(bot, user.id, "No event is currently active.")
        return
    ev   = EVENTS.get(info["event_id"], {})
    name = ev.get("name", info["event_id"])
    desc = ev.get("desc", "")
    try:
        await bot.highrise.chat(
            f"🎪 {name} event is ACTIVE! {desc[:100]} "
            "Use !eventshop for rewards!"[:249]
        )
    except Exception as exc:
        print(f"[EVENTS] eventresume announce error: {exc}")
    await _w(bot, user.id, f"✅ Re-announced: {name}")


async def handle_autogamestatus(bot: BaseBot, user: User) -> None:
    """/autogamestatus — show auto-game loop settings."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    bj_on  = db.get_room_setting("auto_bj_enabled", "0")
    rbj_on = db.get_room_setting("auto_rbj_enabled", "0")
    pk_on  = db.get_room_setting("auto_poker_enabled", "0")
    ev_on  = db.get_room_setting("auto_events_enabled", "0")
    ev_int = db.get_room_setting("auto_event_interval_hours", "2")
    await _w(bot, user.id,
             f"🎮 AutoGames: BJ={bj_on} RBJ={rbj_on} Poker={pk_on} | "
             f"AutoEvents={ev_on} every {ev_int}h")


async def handle_autogameresume(bot: BaseBot, user: User) -> None:
    """/autogameresume — trigger auto-game and auto-event loops to re-check."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    from modules.auto_games import start_auto_game_loop, start_auto_event_loop
    start_auto_game_loop(bot)
    start_auto_event_loop(bot)
    await _w(bot, user.id, "✅ Auto-game and auto-event loops restarted.")


# ---------------------------------------------------------------------------
# Startup recovery — called from HangoutBot.on_start
# ---------------------------------------------------------------------------

async def startup_event_check(bot: BaseBot) -> None:
    """
    Called once at bot startup (after DB is initialised).

    Reads raw event_settings from SQLite and decides:
      - If no event was flagged active → nothing to do.
      - If active but expires_at has already passed → clear DB, log.
      - If active and time remains → restart the async timer for the
        remaining seconds so the event ends cleanly.

    Does NOT call get_active_event() because that auto-clears the DB
    before we can compute how much time is left.
    """
    global _event_task

    conn = db.get_connection()
    rows = {
        r["key"]: r["value"]
        for r in conn.execute("SELECT key, value FROM event_settings").fetchall()
    }
    conn.close()

    if rows.get("event_active") != "1":
        return  # no lingering event

    event_id   = rows.get("event_name", "")
    expires_at = rows.get("event_expires_at", "")

    if not event_id or not expires_at:
        db.clear_active_event()
        print("[EVENTS] Startup: malformed event record, cleared.")
        return

    try:
        exp = datetime.fromisoformat(expires_at)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
    except Exception:
        db.clear_active_event()
        print("[EVENTS] Startup: unparseable expires_at, cleared.")
        return

    now       = datetime.now(timezone.utc)
    remaining = (exp - now).total_seconds()

    if remaining <= 0:
        # Event expired during downtime
        db.clear_active_event()
        name = EVENTS.get(event_id, {}).get("name", event_id)
        print(f"[EVENTS] Startup: '{event_id}' expired during downtime, cleared.")
        try:
            await bot.highrise.chat(
                f"🎉 Event ended: {name}. Use !eventshop to spend your pts!"
            )
        except Exception as exc:
            print(f"[EVENTS] Startup announce error: {exc}")
        return

    # Event is still live — restart the countdown timer
    _cancel_event_task()
    _event_task = asyncio.create_task(
        _event_timer(bot, event_id, remaining)
    )
    name = EVENTS.get(event_id, {}).get("name", event_id)
    m, s = divmod(int(remaining), 60)
    h, m = divmod(m, 60)
    left = f"{h}h {m}m" if h else f"{m}m {s}s"
    print(f"[EVENTS] Startup: resumed '{event_id}' — {left} remaining.")


# ---------------------------------------------------------------------------
# /eventpoints
# ---------------------------------------------------------------------------

async def handle_eventpoints(bot: BaseBot, user: User, args: list[str] | None = None) -> None:
    from modules.permissions import is_manager, can_manage_economy
    db.ensure_user(user.id, user.username)

    # /eventpoints <username>  — manager/admin/owner can view others
    if args and len(args) >= 2:
        if not (is_manager(user.username) or can_manage_economy(user.username)):
            await _w(bot, user.id, "Manager, admin, or owner only to view other players.")
            return
        target_name = args[1].lstrip("@").strip()
        pts = db.get_event_points_for_user(target_name)
        if pts is None:
            await _w(bot, user.id, f"❌ @{target_name} not found.")
            return
        active = db.is_event_active()
        status = "🟢 Event ON" if active else "🔴 No event"
        await _w(bot, user.id, f"🎟️ @{target_name} event coins: {pts:,} | {status}")
        return

    pts    = db.get_event_points(user.id)
    active = db.is_event_active()
    status = "🟢 Event ON" if active else "🔴 No event"
    await _w(bot, user.id, f"🎟️ Event coins: {pts:,} | {status}")


# ---------------------------------------------------------------------------
# /eventshop
# ---------------------------------------------------------------------------

_SHOP_MSG = (
    "🎪 Event Shop:\n"
    "🌟 event_star_badge 50pts\n"
    "🎉 party_badge 75pts\n"
    "[Casino Night] casino_night_title 100pts\n"
    "[Trivia Champ] trivia_champ_title 100pts\n"
    "[OG Guest] og_guest_title 250pts\n"
    "Use !buyevent <id>"
)


async def handle_eventshop(bot: BaseBot, user: User) -> None:
    """Show numbered event shop and save session."""
    import database as _db

    active = _db.is_event_active()
    status = "🟢 Active" if active else "🔴 No event"

    session_items = []
    lines = [f"🎪 Event Shop — {status}"]

    for num, (item_id, item) in enumerate(ALL_EVENT_ITEMS.items(), 1):
        display = item["display"]
        cost    = item["event_cost"]
        lines.append(f"{num} {display} {item_id} {cost}EC")
        session_items.append({
            "num":       num,
            "item_id":   item_id,
            "name":      display,
            "emoji":     display,
            "price":     cost,
            "currency":  "event_coins",
            "shop_type": "event",
        })

    lines.append("Buy: !buy <#>")
    msg = "\n".join(lines)
    if len(msg) > 249:
        msg = msg[:249]

    _db.save_shop_session(user.username, "event", 1, session_items)
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# /buyevent <item_id>
# ---------------------------------------------------------------------------

async def handle_buyevent(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !buyevent <item_id>  |  See !eventshop")
        return

    db.ensure_user(user.id, user.username)
    item_id = args[1].lower()
    item    = ALL_EVENT_ITEMS.get(item_id)

    if item is None:
        await _w(bot, user.id, f"Unknown item: {item_id}  |  See !eventshop")
        return

    if not db.is_event_active():
        await _w(bot, user.id, "No event is active right now!")
        return

    cost = item["event_cost"]
    pts  = db.get_event_points(user.id)

    if pts < cost:
        await _w(bot, user.id,
                 f"Not enough pts! Need {cost}, you have {pts}.")
        return

    result = db.buy_event_item(user.id, user.username, item_id, item["item_type"], cost)

    if result == "duplicate":
        await _w(bot, user.id,
                 f"Already own {item['display']} {item_id}. "
                 f"Equip: /equip {item['item_type']} {item_id}")
    elif result == "ok":
        new_pts = db.get_event_points(user.id)
        await _w(bot, user.id,
                 f"✅ Bought {item['display']} {item_id}! "
                 f"Pts left: {new_pts}. "
                 f"Equip: /equip {item['item_type']} {item_id}")
    elif result == "no_points":
        await _w(bot, user.id,
                 f"Not enough pts! Need {cost}, you have {pts}.")
    else:
        await _w(bot, user.id, "Purchase failed. Try again!")


# ---------------------------------------------------------------------------
# Mining event helpers
# ---------------------------------------------------------------------------

_MINING_EVENT_IDS = {
    "lucky_rush", "heavy_ore_rush", "ore_value_surge", "double_mxp",
    "mining_haste", "legendary_rush", "prismatic_hunt", "exotic_hunt",
    "admins_mining_blessing", "ultimate_mining_rush",
}

_FISHING_EVENT_IDS = {
    "lucky_tide", "heavy_catch", "fish_value_surge", "double_fxp",
    "fishing_haste", "legendary_tide", "prismatic_tide", "exotic_tide",
    "ultimate_fishing_rush",
}

# Also include legacy mining events that live in VALID_MINING_EVENTS
_ALL_MINE_IDS = _MINING_EVENT_IDS | {
    "double_ore", "lucky_hour", "energy_free", "meteor_rush",
}


def _start_mining_event(event_id: str, started_by: str, duration_mins: int) -> None:
    """Write a mining event via the existing db.start_mining_event interface."""
    db.start_mining_event(event_id, started_by, duration_mins)


def _format_mining_event_effects(event_id: str) -> str:
    """Return a readable one-liner showing what a mining event does."""
    _map = {
        "lucky_rush":            "🍀 Luck +25%",
        "heavy_ore_rush":        "⚖️ Weight +25%",
        "ore_value_surge":       "💰 Value 1.5x",
        "double_mxp":            "⭐ MXP 2x",
        "mining_haste":          "⏳ Cooldown -25%",
        "legendary_rush":        "💎 Leg+ chance +50%",
        "prismatic_hunt":        "🌈 Prismatic chance +100%",
        "exotic_hunt":           "🔴 Exotic chance +100%",
        "admins_mining_blessing": (
            "🍀+50% ⚖️+50% 💰2x ⭐2x ⏳-25% 💎+50% 🌈+100% 🔴+100%"
        ),
        "double_ore":   "Ore qty 2x",
        "lucky_hour":   "Rare chance +50%",
        "energy_free":  "0 energy cost",
        "meteor_rush":  "Ultra rare chance 2x",
    }
    return _map.get(event_id, event_id)


def _format_fishing_event_effects(event_id: str) -> str:
    """Return a readable one-liner showing what a fishing event does."""
    _map = {
        "lucky_tide":            "🌊 Fish luck +25%",
        "heavy_catch":           "⚖️ Weight +25%",
        "fish_value_surge":      "💰 Value 1.5x",
        "double_fxp":            "⭐ FXP 2x",
        "fishing_haste":         "⏳ Cooldown -25%",
        "legendary_tide":        "🐉 Leg+ chance +50%",
        "prismatic_tide":        "🌈 Prismatic chance +100%",
        "exotic_tide":           "🚨 Exotic chance +100%",
        "ultimate_fishing_rush": (
            "🌊+50% ⚖️+50% 💰2x ⭐2x ⏳-25% 🐉+50% 🌈+100% 🚨+100%"
        ),
    }
    return _map.get(event_id, event_id)


# ---------------------------------------------------------------------------
# /mineevents  /mineboosts  /luckstatus  — public mining event status
# ---------------------------------------------------------------------------

async def handle_mineevents(bot: BaseBot, user: User) -> None:
    """/mineevents — list all available mining events."""
    lines = ["<#66CCFF>⛏️ Mining Events<#FFFFFF>"]
    for eid in sorted(_ALL_MINE_IDS):
        ev = EVENTS.get(eid, {})
        name = ev.get("name", eid)
        eff  = _format_mining_event_effects(eid)
        lines.append(f"• {eid} — {eff}"[:80])
    lines.append("Start: /startevent <id> <mins>")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_mineboosts(bot: BaseBot, user: User) -> None:
    """/mineboosts — show active mining event boosts."""
    mine_ev = db.get_active_mining_event()
    effect  = get_event_effect()
    boost_lines: list[str] = []

    if mine_ev:
        eid  = mine_ev.get("event_id", "?")
        ev   = EVENTS.get(eid, {})
        name = ev.get("name", eid)
        boost_lines.append(f"Event: {name}")
        boost_lines.append(_format_mining_event_effects(eid))

    if effect.get("mining_boost"):
        boost_lines.append("Admin's Blessing: 2x mining qty active")

    # Show individual active boosts
    if effect.get("mining_luck_boost", 0) > 0:
        pct = int(effect["mining_luck_boost"] * 100)
        boost_lines.append(f"🍀 Luck +{pct}%")
    if effect.get("weight_luck_boost", 0) > 0:
        pct = int(effect["weight_luck_boost"] * 100)
        boost_lines.append(f"⚖️ Weight +{pct}%")
    if effect.get("ore_value_multiplier", 1.0) > 1.0:
        boost_lines.append(f"💰 Value {effect['ore_value_multiplier']}x")
    if effect.get("mxp_multiplier", 1.0) > 1.0:
        boost_lines.append(f"⭐ MXP {effect['mxp_multiplier']}x")
    if effect.get("cooldown_reduction", 0) > 0:
        pct = int(effect["cooldown_reduction"] * 100)
        boost_lines.append(f"⏳ Cooldown -{pct}%")
    if effect.get("legendary_plus_chance_boost", 0) > 0:
        pct = int(effect["legendary_plus_chance_boost"] * 100)
        boost_lines.append(f"💎 Leg+ +{pct}%")
    if effect.get("prismatic_chance_boost", 0) > 0:
        pct = int(effect["prismatic_chance_boost"] * 100)
        boost_lines.append(f"🌈 Prismatic +{pct}%")
    if effect.get("exotic_chance_boost", 0) > 0:
        pct = int(effect["exotic_chance_boost"] * 100)
        boost_lines.append(f"🔴 Exotic +{pct}%")

    if not boost_lines:
        await _w(bot, user.id, "⛏️ No active mining boosts right now.")
        return
    header = "<#66CCFF>⛏️ Mining Boosts<#FFFFFF>"
    await _w(bot, user.id, (header + "\n" + " | ".join(boost_lines))[:249])


async def handle_luckstatus(bot: BaseBot, user: User) -> None:
    """/luckstatus — shorthand for /mineboosts."""
    await handle_mineboosts(bot, user)


# ---------------------------------------------------------------------------
# /miningblessing  /luckevent  /miningevent  — event start shortcuts
# ---------------------------------------------------------------------------

async def handle_miningblessing(bot: BaseBot, user: User, args: list[str]) -> None:
    """/miningblessing [minutes] — start Admin's Mining Blessing."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    mins = 30
    if len(args) >= 2 and args[1].isdigit():
        mins = max(1, min(480, int(args[1])))
    _start_mining_event("admins_mining_blessing", user.username, mins)
    eff = _format_mining_event_effects("admins_mining_blessing")
    await _w(bot, user.id,
             f"✅ Admin's Mining Blessing started ({mins}m)!\n{eff}"[:249])
    try:
        await bot.highrise.chat(
            f"🔥 Admin's Mining Blessing is LIVE for {mins}m! "
            "All mining boosts active. !mine now!"[:249]
        )
    except Exception:
        pass


async def handle_luckevent(bot: BaseBot, user: User, args: list[str]) -> None:
    """/luckevent [minutes] — start Lucky Rush mining event."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    mins = 30
    if len(args) >= 2 and args[1].isdigit():
        mins = max(1, min(480, int(args[1])))
    _start_mining_event("lucky_rush", user.username, mins)
    await _w(bot, user.id, f"✅ Lucky Rush started ({mins}m)! 🍀 Luck +25%.")
    try:
        await bot.highrise.chat(
            f"🍀 Lucky Rush is LIVE for {mins}m! Mine Rare+ ore easier. !mine"[:249]
        )
    except Exception:
        pass


async def handle_miningevent_start(
    bot: BaseBot, user: User, args: list[str]
) -> None:
    """/miningevent <event_id> [minutes] — start any mining event."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        ids = ", ".join(sorted(_ALL_MINE_IDS))
        await _w(bot, user.id, f"Usage: !miningevent <id> [mins]\nIDs: {ids}"[:249])
        return
    eid = args[1].lower()
    if eid not in _ALL_MINE_IDS:
        ids = ", ".join(sorted(_ALL_MINE_IDS))
        await _w(bot, user.id,
                 f"Unknown mining event: {eid}\nValid: {ids}"[:249])
        return
    mins = 30
    if len(args) >= 3 and args[2].isdigit():
        mins = max(1, min(480, int(args[2])))
    _start_mining_event(eid, user.username, mins)
    ev   = EVENTS.get(eid, {})
    name = ev.get("name", eid)
    eff  = _format_mining_event_effects(eid)
    await _w(bot, user.id, f"✅ {name} started ({mins}m)!\n{eff}"[:249])
    try:
        await bot.highrise.chat(
            f"⛏️ {name} is LIVE for {mins}m! {eff} !mine now!"[:249]
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /eventmanager  /eventpanel  /eventeffects  — Event Manager panel
# ---------------------------------------------------------------------------

async def handle_eventmanager(bot: BaseBot, user: User) -> None:
    """/eventmanager — show event manager overview."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    gen_ev   = db.get_active_event()
    mine_ev  = db.get_active_mining_event()
    gen_name = "None"
    gen_left = ""
    if gen_ev:
        ev       = EVENTS.get(gen_ev["event_id"], {})
        gen_name = ev.get("name", gen_ev["event_id"])
        gen_left = " (" + _time_remaining(gen_ev["expires_at"]) + ")"
    mine_name = "None"
    if mine_ev:
        ev        = EVENTS.get(mine_ev.get("event_id", ""), {})
        mine_name = ev.get("name", mine_ev.get("event_id", "?"))

    await _w(bot, user.id,
             f"<#FFD700>🎪 Event Manager<#FFFFFF>\n"
             f"Room event: {gen_name}{gen_left}\n"
             f"Mining event: {mine_name}\n"
             f"/eventpanel for full detail | /eventeffects for active boosts"[:249])


async def handle_eventpanel(bot: BaseBot, user: User) -> None:
    """/eventpanel — detailed event panel for staff."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    gen_ev  = db.get_active_event()
    mine_ev = db.get_active_mining_event()
    ev_on   = db.get_room_setting("auto_events_enabled", "0")
    ev_int  = db.get_room_setting("auto_event_interval_hours", "2")

    lines = ["<#FFD700>🎪 Event Panel<#FFFFFF>"]
    if gen_ev:
        ev   = EVENTS.get(gen_ev["event_id"], {})
        name = ev.get("name", gen_ev["event_id"])
        left = _time_remaining(gen_ev["expires_at"])
        lines.append(f"Room: {name} | {left} left")
    else:
        lines.append("Room event: OFF")

    if mine_ev:
        eid  = mine_ev.get("event_id", "")
        ev   = EVENTS.get(eid, {})
        name = ev.get("name", eid)
        if eid in _FISHING_EVENT_IDS:
            eff   = _format_fishing_event_effects(eid)
            label = "Fishing"
        else:
            eff   = _format_mining_event_effects(eid)
            label = "Mining"
        lines.append(f"{label}: {name} | {eff}"[:80])
    else:
        lines.append("Mining/Fishing event: OFF")

    lines.append(
        f"AutoEvents: {'ON' if ev_on == '1' else 'OFF'} every {ev_int}h"
    )
    lines.append(
        "!startevent [id] [mins] | !stopevent\n"
        "!miningevent [id] [mins]"
    )
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_eventeffects(bot: BaseBot, user: User) -> None:
    """/eventeffects — show all currently active event effects."""
    eff = get_event_effect()
    lines = ["<#FFD700>✨ Active Event Effects<#FFFFFF>"]
    if eff["coins"] != 1.0:
        lines.append(f"💰 Coins: {eff['coins']}x")
    if eff["xp"] != 1.0:
        lines.append(f"⭐ XP: {eff['xp']}x")
    if eff["tax_free"]:
        lines.append("🏦 Tax-Free Banking")
    if eff["shop_discount"] > 0:
        lines.append(f"🛍️ Shop -{int(eff['shop_discount']*100)}%")
    if eff["casino_bet_mult"] != 1.0:
        lines.append(f"🎰 Casino bet {eff['casino_bet_mult']}x")
    if eff["mining_luck_boost"] > 0:
        lines.append(f"🍀 Mining luck +{int(eff['mining_luck_boost']*100)}%")
    if eff["weight_luck_boost"] > 0:
        lines.append(f"⚖️ Weight +{int(eff['weight_luck_boost']*100)}%")
    if eff["ore_value_multiplier"] > 1.0:
        lines.append(f"💎 Ore value {eff['ore_value_multiplier']}x")
    if eff["mxp_multiplier"] > 1.0:
        lines.append(f"⛏️ MXP {eff['mxp_multiplier']}x")
    if eff["cooldown_reduction"] > 0:
        lines.append(f"⏳ Cooldown -{int(eff['cooldown_reduction']*100)}%")
    if eff["legendary_plus_chance_boost"] > 0:
        lines.append(f"👑 Leg+ +{int(eff['legendary_plus_chance_boost']*100)}%")
    if eff["prismatic_chance_boost"] > 0:
        lines.append(f"🌈 Prismatic +{int(eff['prismatic_chance_boost']*100)}%")
    if eff["exotic_chance_boost"] > 0:
        lines.append(f"🔴 Exotic +{int(eff['exotic_chance_boost']*100)}%")
    if eff.get("mining_boost"):
        lines.append("⚒️ Admin's Blessing: 2x ore qty")
    # Fishing boosts
    if eff.get("fish_luck_boost", 0) > 0:
        lines.append(f"🌊 Fish luck +{int(eff['fish_luck_boost']*100)}%")
    if eff.get("fish_weight_luck_boost", 0) > 0:
        lines.append(f"⚖️ Fish weight +{int(eff['fish_weight_luck_boost']*100)}%")
    if eff.get("fish_value_multiplier", 1.0) > 1.0:
        lines.append(f"💰 Fish value {eff['fish_value_multiplier']}x")
    if eff.get("fxp_multiplier", 1.0) > 1.0:
        lines.append(f"⭐ FXP {eff['fxp_multiplier']}x")
    if eff.get("fishing_cooldown_reduction", 0) > 0:
        lines.append(f"⏳ Fish cd -{int(eff['fishing_cooldown_reduction']*100)}%")
    if eff.get("legendary_plus_fish_chance_boost", 0) > 0:
        lines.append(f"🐉 Fish Leg+ +{int(eff['legendary_plus_fish_chance_boost']*100)}%")
    if eff.get("prismatic_fish_chance_boost", 0) > 0:
        lines.append(f"🌈 Pris fish +{int(eff['prismatic_fish_chance_boost']*100)}%")
    if eff.get("exotic_fish_chance_boost", 0) > 0:
        lines.append(f"🚨 Exotic fish +{int(eff['exotic_fish_chance_boost']*100)}%")
    if len(lines) == 1:
        lines.append("No active effects.")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /autoeventstatus  /aeadd  /aeremove  /autoeventinterval  (upgraded)
# ---------------------------------------------------------------------------

async def handle_autoeventstatus(bot: BaseBot, user: User) -> None:
    """/autoeventstatus — full auto-event scheduler status."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    settings  = db.get_auto_event_settings()
    enabled   = settings["auto_events_enabled"]
    interval  = settings["auto_event_interval"]
    pool      = db.get_event_pool()
    eligible  = db.get_eligible_pool_events()
    last_tick = db.get_auto_event_setting_str("last_scheduler_tick", "")
    next_at   = db.get_auto_event_setting_str("next_event_at", "")
    next_id   = db.get_auto_event_setting_str("next_event_id", "")

    # Unified current event source
    active  = _get_all_active_events()
    cur_str = " + ".join(ev["name"] for ev in active) if active else "None"

    # Tick age
    tick_str = "never"
    if last_tick:
        try:
            lt = datetime.fromisoformat(last_tick)
            if lt.tzinfo is None:
                lt = lt.replace(tzinfo=timezone.utc)
            secs = int((datetime.now(timezone.utc) - lt).total_seconds())
            tick_str = f"{secs}s ago"
        except Exception:
            tick_str = "?"

    # Next event display
    if not enabled:
        next_str = "N/A"
    elif next_at:
        next_str = _time_remaining(next_at)
    elif active:
        # Active event running — estimate next after it ends + interval
        ends_at = active[0]["ends_at"]
        try:
            et = datetime.fromisoformat(ends_at)
            if et.tzinfo is None:
                et = et.replace(tzinfo=timezone.utc)
            est = (et + timedelta(minutes=interval) - datetime.now(timezone.utc)).total_seconds()
            est = max(0, int(est))
            m, s = divmod(est, 60)
            next_str = f"~{m}m {s}s"
        except Exception:
            next_str = f"~{interval}m"
    else:
        next_str = f"~{interval}m"

    next_name = (
        _CATALOG_BY_ID.get(next_id, {}).get("name", next_id)
        if next_id else "selected at runtime"
    )

    dur = settings.get("auto_event_duration", 30)
    scheduler_up = False
    if last_tick:
        try:
            _lt = datetime.fromisoformat(last_tick)
            if _lt.tzinfo is None:
                _lt = _lt.replace(tzinfo=timezone.utc)
            scheduler_up = int((datetime.now(timezone.utc) - _lt).total_seconds()) < 150
        except Exception:
            pass

    lines = [
        "⚙️ Auto Event Status",
        f"Auto Events: {'ON' if enabled else 'OFF'}",
        f"Scheduler: {'RUNNING' if scheduler_up else 'STOPPED'}",
        f"Interval: {interval}m | Duration: {dur}m",
        f"Pool: {len(pool)} | Eligible: {len(eligible)}",
        f"Current: {cur_str}",
        f"Next: {next_name} | In: {next_str}",
        f"Tick: {tick_str}",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_autoeventadd(bot: BaseBot, user: User, args: list[str]) -> None:
    """/autoeventadd <event_id_or_number> — add event to auto pool (legacy alias)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !autoeventadd <id or #>  (see !eventlist)")
        return
    raw = args[1].lower()
    eid = _resolve_event_arg(raw) or raw
    if eid not in EVENTS:
        await _w(bot, user.id, f"Unknown event: {raw}")
        return
    entry = _CATALOG_BY_ID.get(eid, {})
    weight = entry.get("default_weight", 1)
    cd     = entry.get("cooldown_minutes", 60)
    db.add_to_event_pool(eid, weight, cd)
    await _w(bot, user.id,
             f"✅ {entry.get('emoji','•')} {entry.get('name', eid)} added to auto pool.")


async def handle_autoeventremove(bot: BaseBot, user: User, args: list[str]) -> None:
    """/autoeventremove <event_id_or_number> — remove event from auto pool (legacy alias)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !autoeventremove <id or #>")
        return
    raw = args[1].lower()
    eid = _resolve_event_arg(raw) or raw
    removed = db.remove_from_event_pool(eid)
    entry   = _CATALOG_BY_ID.get(eid, {})
    name    = entry.get("name", eid)
    if removed:
        await _w(bot, user.id, f"✅ {name} removed from auto pool.")
    else:
        await _w(bot, user.id, f"{name} was not in the pool.")


async def handle_autoeventinterval(bot: BaseBot, user: User, args: list[str]) -> None:
    """/autoeventinterval <minutes> — set auto-event interval in minutes."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !autoeventinterval <minutes>  (1-2880)")
        return
    mins = max(1, min(2880, int(args[1])))
    db.set_auto_event_setting("auto_event_interval", mins)
    await _w(bot, user.id, f"✅ Auto-event interval set to {mins}m.")


# ---------------------------------------------------------------------------
# /eventlist  (public)
# ---------------------------------------------------------------------------

async def handle_eventlist(bot: BaseBot, user: User,
                           args: list[str] | None = None) -> None:
    """/eventlist [2] — event catalog. Page 1=Mining/Room (1-12), Page 2=Fishing (13-21)."""
    page = 1
    if args and len(args) >= 2 and args[1].isdigit():
        page = max(1, min(2, int(args[1])))

    if page == 1:
        lines1 = ["<#FFD700>📋 Events 1-6<#FFFFFF>"]
        for ev in EVENT_CATALOG[:6]:
            mo = " [manual]" if ev["manual_only"] else ""
            lines1.append(f"{ev['number']}. {ev['emoji']} {ev['name']}{mo}")
        await _w(bot, user.id, "\n".join(lines1)[:249])

        lines2 = ["<#FFD700>📋 Events 7-12<#FFFFFF>"]
        for ev in EVENT_CATALOG[6:12]:
            mo = " [manual]" if ev["manual_only"] else ""
            lines2.append(f"{ev['number']}. {ev['emoji']} {ev['name']}{mo}")
        lines2.append("!eventlist 2 for Fishing events")
        await _w(bot, user.id, "\n".join(lines2)[:249])
    else:
        lines1 = ["<#00CCFF>📋 Events 13-17 (Fishing)<#FFFFFF>"]
        for ev in EVENT_CATALOG[12:17]:
            mo = " [manual]" if ev["manual_only"] else ""
            lines1.append(f"{ev['number']}. {ev['emoji']} {ev['name']}{mo}")
        await _w(bot, user.id, "\n".join(lines1)[:249])

        lines2 = ["<#00CCFF>📋 Events 18-21 (Fishing)<#FFFFFF>"]
        for ev in EVENT_CATALOG[17:]:
            mo = " [manual]" if ev["manual_only"] else ""
            lines2.append(f"{ev['number']}. {ev['emoji']} {ev['name']}{mo}")
        lines2.append("/event <#> <mins>  |  /eventpreview <#>")
        await _w(bot, user.id, "\n".join(lines2)[:249])


# ---------------------------------------------------------------------------
# /eventpreview <number>  (public)
# ---------------------------------------------------------------------------

async def handle_eventpreview(bot: BaseBot, user: User, args: list[str]) -> None:
    """/eventpreview <number> — show detail for one event."""
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !eventpreview <number 1-21>")
        return
    eid = _resolve_event_arg(args[1].lower())
    if eid is None:
        await _w(bot, user.id, "Unknown event. Use !eventlist to see the catalog.")
        return
    entry      = _CATALOG_BY_ID[eid]
    pool_entry = db.get_event_pool_entry(eid)
    in_pool    = "YES" if pool_entry else "NO"
    weight     = pool_entry["weight"] if pool_entry else entry["default_weight"]
    cd         = pool_entry["cooldown_minutes"] if pool_entry else entry["cooldown_minutes"]
    mo         = "YES" if entry["manual_only"] else "NO"
    msg = (
        f"{entry['emoji']} {entry['name']}\n"
        f"#{entry['number']} | {eid}\n"
        f"Effect: {entry['effect_desc']}\n"
        f"Pool: {in_pool} | Manual: {mo}\n"
        f"Weight: {weight} | Cooldown: {cd}m"
    )
    await _w(bot, user.id, msg[:249])


# ---------------------------------------------------------------------------
# /aepool / /autoeventpool  (manager+)
# ---------------------------------------------------------------------------

async def handle_aepool(bot: BaseBot, user: User) -> None:
    """/aepool — show current auto-event pool."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    pool = db.get_event_pool()
    if not pool:
        await _w(bot, user.id,
                 "📋 Auto pool is empty. Use !aeadd <#> to add events.")
        return
    lines = [f"<#FFD700>📋 Auto Pool ({len(pool)})<#FFFFFF>"]
    for row in pool:
        eid   = row["event_id"]
        entry = _CATALOG_BY_ID.get(eid, {})
        emoji = entry.get("emoji", "•")
        name  = entry.get("name", eid)[:14]
        lines.append(
            f"{emoji} {name} w:{row['weight']} cd:{row['cooldown_minutes']}m"[:48]
        )
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /aeadd <number>  (manager+)
# ---------------------------------------------------------------------------

async def handle_aeadd(bot: BaseBot, user: User, args: list[str]) -> None:
    """/aeadd <number> — add event to auto pool by catalog number."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !aeadd <number>  (see !eventlist)")
        return
    eid = _resolve_event_arg(args[1].lower())
    if eid is None:
        await _w(bot, user.id, "Unknown event. Use !eventlist for numbers.")
        return
    entry = _CATALOG_BY_ID[eid]
    db.add_to_event_pool(eid, entry["default_weight"], entry["cooldown_minutes"])
    await _w(bot, user.id,
             f"✅ {entry['emoji']} {entry['name']} added to auto pool.")


# ---------------------------------------------------------------------------
# /aeremove <number>  (manager+)
# ---------------------------------------------------------------------------

async def handle_aeremove(bot: BaseBot, user: User, args: list[str]) -> None:
    """/aeremove <number> — remove event from auto pool."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !aeremove <number>  (see !eventlist)")
        return
    eid     = _resolve_event_arg(args[1].lower())
    if eid is None:
        await _w(bot, user.id, "Unknown event. Use !eventlist for numbers.")
        return
    removed = db.remove_from_event_pool(eid)
    name    = _CATALOG_BY_ID.get(eid, {}).get("name", eid)
    if removed:
        await _w(bot, user.id, f"✅ {name} removed from auto pool.")
    else:
        await _w(bot, user.id, f"{name} was not in the auto pool.")


# ---------------------------------------------------------------------------
# /aequeue / /autoeventqueue  (manager+)
# ---------------------------------------------------------------------------

async def handle_aequeue(bot: BaseBot, user: User) -> None:
    """/aequeue — show upcoming auto event queue (up to 3 slots)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    pool     = db.get_event_pool()
    next_id  = db.get_auto_event_setting_str("next_event_id", "")
    next_at  = db.get_auto_event_setting_str("next_event_at", "")
    q2       = db.get_auto_event_setting_str("ae_queue_2", "")
    q3       = db.get_auto_event_setting_str("ae_queue_3", "")
    eligible = db.get_eligible_pool_events()
    lines    = ["<#FFD700>📋 Auto Event Queue<#FFFFFF>"]
    if next_id:
        e1       = _CATALOG_BY_ID.get(next_id, {})
        time_str = _time_remaining(next_at) if next_at else "?"
        lines.append(f"1. {e1.get('emoji','•')} {e1.get('name', next_id)} — in {time_str}")
    else:
        lines.append("1. (random at runtime)")
    if q2:
        e2 = _CATALOG_BY_ID.get(q2, {})
        lines.append(f"2. {e2.get('emoji','•')} {e2.get('name', q2)} — queued")
    if q3:
        e3 = _CATALOG_BY_ID.get(q3, {})
        lines.append(f"3. {e3.get('emoji','•')} {e3.get('name', q3)} — queued")
    lines.append(f"Pool: {len(pool)} | Eligible: {len(eligible)}")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /aenext / /autoeventnext  (manager+)
# ---------------------------------------------------------------------------

async def handle_aenext(bot: BaseBot, user: User) -> None:
    """/aenext — show the next scheduled auto event."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    next_id  = db.get_auto_event_setting_str("next_event_id", "")
    next_at  = db.get_auto_event_setting_str("next_event_at", "")
    settings = db.get_auto_event_settings()
    enabled  = settings["auto_events_enabled"]
    interval = settings["auto_event_interval"]
    dur      = settings.get("auto_event_duration", 30)
    if next_id:
        entry    = _CATALOG_BY_ID.get(next_id, {})
        name     = entry.get("name", next_id)
        emoji    = entry.get("emoji", "")
        time_str = _time_remaining(next_at) if next_at else "?"
        source   = "Manual override" if db.get_auto_event_setting_str("next_event_source", "") == "manual" else "Auto queue"
        await _w(bot, user.id,
                 f"⏭️ Next Auto Event\n"
                 f"{emoji} {name}\n"
                 f"Starts in: {time_str}\n"
                 f"Duration: {dur}m | {source}")
    elif enabled:
        await _w(bot, user.id,
                 f"⏭️ Next event: random from pool\n"
                 f"Interval: every {interval}m | Duration: {dur}m")
    else:
        await _w(bot, user.id, "⏭️ Auto events are OFF. /autoevents on to enable.")


# ---------------------------------------------------------------------------
# /eventheartbeat / /eventscheduler  (manager+)
# ---------------------------------------------------------------------------

async def handle_eventheartbeat(bot: BaseBot, user: User) -> None:
    """/eventheartbeat — show scheduler heartbeat status."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    settings   = db.get_auto_event_settings()
    enabled    = settings["auto_events_enabled"]
    interval   = settings["auto_event_interval"]
    last_tick  = db.get_auto_event_setting_str("last_scheduler_tick", "")
    next_tick  = db.get_auto_event_setting_str("next_scheduler_tick", "")
    next_at    = db.get_auto_event_setting_str("next_event_at", "")
    next_id    = db.get_auto_event_setting_str("next_event_id", "")
    pool       = db.get_event_pool()

    tick_str        = "never"
    scheduler_up    = False
    if last_tick:
        try:
            lt = datetime.fromisoformat(last_tick)
            if lt.tzinfo is None:
                lt = lt.replace(tzinfo=timezone.utc)
            secs        = int((datetime.now(timezone.utc) - lt).total_seconds())
            tick_str    = f"{secs}s ago"
            scheduler_up = secs < 150  # running if tick within 2.5 min
        except Exception:
            tick_str = "?"

    next_tick_str = _time_remaining(next_tick) if next_tick else "?"
    next_name     = _CATALOG_BY_ID.get(next_id, {}).get("name", next_id or "auto")
    next_str      = _time_remaining(next_at) if next_at else f"~{interval}m"

    status_icon = "🟢" if scheduler_up else "🔴"
    sched_label = "RUNNING" if scheduler_up else "STOPPED"
    if not enabled:
        sched_label = "IDLE (auto events OFF)"

    lines = [
        f"{status_icon} Scheduler Heartbeat",
        f"Auto Events: {'ON' if enabled else 'OFF'}",
        f"Scheduler: {sched_label}",
        f"Interval: {interval}m | Pool: {len(pool)}",
        f"Last Tick: {tick_str}",
        f"Next Tick: {next_tick_str}",
        f"Next Event: {next_name} in {next_str}",
    ]
    if not scheduler_up and enabled:
        lines.append("Fix: restart EventBot or /eventscheduler restart")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /eventcooldowns  (manager+)
# ---------------------------------------------------------------------------

async def handle_eventcooldowns(bot: BaseBot, user: User,
                               args: list[str] | None = None) -> None:
    """/eventcooldowns [page] — per-event cooldowns, 7 per page, 3 pages."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    page = 1
    if args and len(args) >= 2 and args[1].isdigit():
        page = max(1, min(3, int(args[1])))
    pool_map = {row["event_id"]: row for row in db.get_event_pool()}
    start_i  = (page - 1) * 7
    evs      = EVENT_CATALOG[start_i: start_i + 7]
    lines    = [f"<#FFD700>⏰ Cooldowns {page}/3<#FFFFFF>"]
    for ev in evs:
        eid     = ev["event_id"]
        short   = _SHORT_DISPLAY.get(eid, ev["name"][:12])
        in_pool = eid in pool_map
        if ev["manual_only"]:
            cd_str = "manual"
        elif in_pool:
            cd_str = f"{pool_map[eid]['cooldown_minutes']}m"
        else:
            cd_str = f"{ev['cooldown_minutes']}m"
        pool_str = "YES" if in_pool else "NO"
        lines.append(
            f"{ev['number']}. {ev['emoji']} {short} — {cd_str} | Pool:{pool_str}"
        )
    if page < 3:
        lines.append(f"→ /eventcooldowns {page + 1} for more")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /seteventcooldown <number> <minutes>  (manager+)
# ---------------------------------------------------------------------------

async def handle_seteventcooldown(bot: BaseBot, user: User, args: list[str]) -> None:
    """/seteventcooldown <number> <minutes> — set cooldown for a pool event."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !seteventcooldown <number> <minutes>")
        return
    eid = _resolve_event_arg(args[1].lower())
    if eid is None:
        await _w(bot, user.id, "Unknown event. Use !eventlist for numbers.")
        return
    if not args[2].isdigit():
        await _w(bot, user.id, "Minutes must be a positive number.")
        return
    cd    = max(0, int(args[2]))
    entry = _CATALOG_BY_ID[eid]
    if not db.get_event_pool_entry(eid):
        await _w(bot, user.id,
                 f"{entry['name']} is not in the pool. "
                 f"Use !aeadd {entry['number']} first.")
        return
    db.set_pool_cooldown(eid, cd)
    await _w(bot, user.id,
             f"✅ {entry['emoji']} {entry['name']} cooldown set to {cd}m.")


# ---------------------------------------------------------------------------
# /eventweights  (manager+)
# ---------------------------------------------------------------------------

async def handle_eventweights(bot: BaseBot, user: User,
                             args: list[str] | None = None) -> None:
    """/eventweights [page] — per-event weights, 7 per page, 3 pages."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    page = 1
    if args and len(args) >= 2 and args[1].isdigit():
        page = max(1, min(3, int(args[1])))
    pool_map = {row["event_id"]: row for row in db.get_event_pool()}
    start_i  = (page - 1) * 7
    evs      = EVENT_CATALOG[start_i: start_i + 7]
    lines    = [f"<#FFD700>⚖️ Weights {page}/3<#FFFFFF>"]
    for ev in evs:
        eid     = ev["event_id"]
        short   = _SHORT_DISPLAY.get(eid, ev["name"][:12])
        in_pool = eid in pool_map
        if ev["manual_only"]:
            w_str = "0"
        elif in_pool:
            w_str = str(pool_map[eid]["weight"])
        else:
            w_str = str(ev["default_weight"])
        pool_str = "YES" if in_pool else "NO"
        lines.append(
            f"{ev['number']}. {ev['emoji']} {short} — {w_str} | Pool:{pool_str}"
        )
    if page < 3:
        lines.append(f"→ /eventweights {page + 1} for more")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /seteventweight <number> <weight>  (manager+)
# ---------------------------------------------------------------------------

async def handle_seteventweight(bot: BaseBot, user: User, args: list[str]) -> None:
    """/seteventweight <number> <weight> — set selection weight for a pool event."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !seteventweight <number> <weight>")
        return
    eid = _resolve_event_arg(args[1].lower())
    if eid is None:
        await _w(bot, user.id, "Unknown event. Use !eventlist for numbers.")
        return
    if not args[2].isdigit():
        await _w(bot, user.id, "Weight must be 0 or a positive number.")
        return
    weight = max(0, int(args[2]))
    entry  = _CATALOG_BY_ID[eid]
    if not db.get_event_pool_entry(eid):
        await _w(bot, user.id,
                 f"{entry['name']} not in pool. "
                 f"Use !aeadd {entry['number']} first.")
        return
    db.set_pool_weight(eid, weight)
    await _w(bot, user.id,
             f"✅ {entry['emoji']} {entry['name']} weight set to {weight}.")


# ---------------------------------------------------------------------------
# /eventhistory  (manager+)
# ---------------------------------------------------------------------------

async def handle_eventhistory(bot: BaseBot, user: User) -> None:
    """/eventhistory — show recent event history (stale entries auto-closed)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    # Clean up stale 'active' rows before displaying
    try:
        db.cleanup_expired_history()
    except Exception:
        pass
    rows = db.get_event_history(limit=5)
    if not rows:
        await _w(bot, user.id, "📜 No event history yet.")
        return
    lines = ["<#FFD700>📜 Event History<#FFFFFF>"]
    for r in rows:
        name   = (r.get("event_name") or r.get("event_id", "?"))[:12]
        mins   = int(r.get("duration_seconds", 0)) // 60
        status = (r.get("status") or "?")[:8]
        sb     = r.get("started_by") or r.get("started_by_username") or ""
        skipped_by = r.get("skipped_by") or ""
        if r.get("auto_started") or sb in ("auto", ""):
            who = "AUTO"
        else:
            who = f"@{sb}"[:12]
        if status == "skipped" and skipped_by:
            line = f"{name} {mins}m [skipped] {who} by @{skipped_by}"[:48]
        else:
            line = f"{name} {mins}m [{status}] {who}"[:48]
        lines.append(line)
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# Startup: mining event check  (called from on_start)
# ---------------------------------------------------------------------------

async def startup_mining_event_check(bot: BaseBot) -> None:
    """Log active mining event at startup; mining.py reads the DB directly."""
    mine_ev = db.get_active_mining_event()
    if mine_ev:
        eid  = mine_ev.get("event_id", "")
        name = EVENTS.get(eid, {}).get("name", eid)
        print(f"[EVENTS] Startup: mining event '{eid}' ({name}) still active.")
    else:
        print("[EVENTS] Startup: no active mining event.")


# ---------------------------------------------------------------------------
# /eventpreset [name] — bundled event presets
# ---------------------------------------------------------------------------

_PRESETS: dict[str, dict] = {
    "chill": {
        "name":         "Chill",
        "events":       [("lucky_rush", 60), ("double_mxp", 60)],
        "suggest_race": None,
        "desc":         "Lucky Rush + Double MXP (60m)",
    },
    "hype": {
        "name":         "Hype",
        "events":       [("lucky_rush", 60), ("heavy_ore_rush", 60)],
        "suggest_race": "setfirstfind mining prismatic 1 5g",
        "desc":         "Lucky Rush + Heavy Ore Rush (60m)",
    },
    "jackpot": {
        "name":         "Jackpot",
        "events":       [("ore_value_surge", 60), ("prismatic_hunt", 60)],
        "suggest_race": "setfirstfind mining prismatic 1 5g",
        "desc":         "Ore Value Surge + Prismatic Hunt (60m)",
    },
    "fishing": {
        "name":         "Fishing Party",
        "events":       [("lucky_tide", 60), ("fish_value_surge", 60)],
        "suggest_race": "setfirstfind fishing exotic 1 5g",
        "desc":         "Lucky Tide + Fish Value Surge (60m)",
    },
    "mining": {
        "name":         "Mining Rush",
        "events":       [("mining_haste", 60), ("ore_value_surge", 60)],
        "suggest_race": "setfirstfind mining legendary 1 3g",
        "desc":         "Mining Haste + Ore Value Surge (60m)",
    },
}


async def handle_eventpreset(bot: BaseBot, user: User, args: list[str]) -> None:
    """/eventpreset [name] — start a preset event bundle. EventHost-owned."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    if len(args) < 2:
        keys   = list(_PRESETS.keys())
        menu   = "🎉 Event Presets\n"
        menu  += "\n".join(f"{i+1}. {k}" for i, k in enumerate(keys))
        menu  += "\nUse: !eventpreset <name>"
        await _w(bot, user.id, menu[:249])
        await _w(bot, user.id,
                 "Example: /eventpreset jackpot\n"
                 "Tip: /aestatus /aenext for event status")
        return

    preset_name = args[1].lower()
    preset      = _PRESETS.get(preset_name)
    if not preset:
        available = ", ".join(_PRESETS.keys())
        await _w(bot, user.id, f"Unknown preset. Options: {available}"[:249])
        return

    started = []
    for eid, mins in preset["events"]:
        if eid not in EVENTS:
            continue
        try:
            _start_mining_event(eid, user.username, mins)
            started.append(EVENTS[eid].get("name", eid))
        except Exception as exc:
            print(f"[PRESET] Failed to start {eid}: {exc}")

    if not started:
        await _w(bot, user.id, "No events started — check event IDs.")
        return

    db.log_staff_action(
        user.id, user.username, "event_preset_started",
        "", "", f"preset={preset_name}"
    )

    ev_list = "\n".join(f"- {e}" for e in started)
    msg1 = f"🎉 {preset['name']} Preset Started\nEvents:\n{ev_list}"
    await _w(bot, user.id, msg1[:249])

    msg2_parts = []
    if preset.get("suggest_race"):
        msg2_parts.append(f"Suggest: /{preset['suggest_race']}")
    msg2_parts.append("Tip: /aestatus to check active events.")
    await _w(bot, user.id, "\n".join(msg2_parts)[:249])

    try:
        msg = (
            f"🎉 {preset['name']} is LIVE! "
            + " + ".join(started)
            + " — !mine !fish now!"
        )[:249]
        await bot.highrise.chat(msg)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /setaeinterval <mins>  (manager+)
# ---------------------------------------------------------------------------

async def handle_setaeinterval(bot: BaseBot, user: User, args: list[str]) -> None:
    """/setaeinterval <minutes> — set auto-event interval (1-2880 mins)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !setaeinterval <minutes>  (1-2880)")
        return
    mins = max(1, min(2880, int(args[1])))
    db.set_auto_event_setting("auto_event_interval", mins)
    await _w(bot, user.id, f"✅ Auto-event interval set to {mins}m.")


# ---------------------------------------------------------------------------
# /setaeduration <mins>  (manager+)
# ---------------------------------------------------------------------------

async def handle_setaeduration(bot: BaseBot, user: User, args: list[str]) -> None:
    """/setaeduration <minutes> — set auto-event duration (1-480 mins)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !setaeduration <minutes>  (1-480)")
        return
    mins = max(1, min(480, int(args[1])))
    db.set_auto_event_setting("auto_event_duration", mins)
    await _w(bot, user.id, f"✅ Auto-event duration set to {mins}m.")


# ---------------------------------------------------------------------------
# /aeinterval  (manager+)
# ---------------------------------------------------------------------------

async def handle_aeinterval(bot: BaseBot, user: User) -> None:
    """/aeinterval — show current auto-event interval."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    settings = db.get_auto_event_settings()
    interval = settings["auto_event_interval"]
    await _w(bot, user.id, f"⏱️ Auto-event interval: {interval}m  (set with /setaeinterval)")


# ---------------------------------------------------------------------------
# /aeduration  (manager+)
# ---------------------------------------------------------------------------

async def handle_aeduration(bot: BaseBot, user: User) -> None:
    """/aeduration — show current auto-event duration."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    settings = db.get_auto_event_settings()
    dur = settings.get("auto_event_duration", 30)
    await _w(bot, user.id, f"⏱️ Auto-event duration: {dur}m  (set with /setaeduration)")


# ---------------------------------------------------------------------------
# /aererollnext  (manager+)
# ---------------------------------------------------------------------------

async def handle_aererollnext(bot: BaseBot, user: User) -> None:
    """/aererollnext — reroll the pre-selected next auto event."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    eligible = db.get_eligible_pool_events()
    if not eligible:
        await _w(bot, user.id, "No eligible events in pool to reroll from.")
        return
    total_weight = sum(r["weight"] for r in eligible)
    if total_weight > 0:
        rval = __import__("random").uniform(0, total_weight)
        acc  = 0.0
        chosen = eligible[-1]["event_id"]
        for row in eligible:
            acc += row["weight"]
            if rval <= acc:
                chosen = row["event_id"]
                break
    else:
        chosen = __import__("random").choice(eligible)["event_id"]
    db.set_auto_event_setting_str("next_event_id", chosen)
    db.set_auto_event_setting_str("next_event_source", "manual")
    entry = _CATALOG_BY_ID.get(chosen, {})
    name  = entry.get("name", chosen)
    emoji = entry.get("emoji", "")
    await _w(bot, user.id, f"🎲 Rerolled! Next event: {emoji} {name}")


# ---------------------------------------------------------------------------
# /setnextae <event_name_or_id>  (manager+)
# ---------------------------------------------------------------------------

async def handle_setnextae(bot: BaseBot, user: User, args: list[str]) -> None:
    """/setnextae <event_name_or_id> — manually set the next auto event."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !setnextae <event_name_or_id>")
        return
    query = " ".join(args[1:]).lower().strip()
    # Try exact ID match first, then name substring
    matched_id = None
    for eid, ev in _CATALOG_BY_ID.items():
        if eid.lower() == query:
            matched_id = eid
            break
    if not matched_id:
        for eid, ev in _CATALOG_BY_ID.items():
            if query in ev.get("name", "").lower() or query in eid.lower():
                matched_id = eid
                break
    if not matched_id:
        await _w(bot, user.id, f"No event found matching '{query[:30]}'. Try !eventlist.")
        return
    db.set_auto_event_setting_str("next_event_id", matched_id)
    db.set_auto_event_setting_str("next_event_source", "manual")
    entry = _CATALOG_BY_ID.get(matched_id, {})
    name  = entry.get("name", matched_id)
    emoji = entry.get("emoji", "")
    await _w(bot, user.id, f"✅ Next auto event set: {emoji} {name}")


# ---------------------------------------------------------------------------
# /aehistory / /autoeventhistory  (alias for /eventhistory with same display)
# ---------------------------------------------------------------------------

async def handle_aehistory(bot: BaseBot, user: User) -> None:
    """/aehistory — show recent auto event history (alias for /eventhistory)."""
    await handle_eventhistory(bot, user)
