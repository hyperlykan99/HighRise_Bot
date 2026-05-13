"""
modules/mining.py
-----------------
Complete realistic ore-based Mining game for Highrise Hangout Room.

Players mine realistic ores, upgrade pickaxes, sell gems, craft rewards,
earn Mining XP (MXP), and compete on leaderboards.
"""

import asyncio
import random
from datetime import datetime, timezone

import config as _cfg
import database as db
from filelock import FileLock, Timeout as _FileLockTimeout
from highrise import BaseBot, User
from modules.permissions import is_admin, is_owner, can_manage_economy
from modules.big_announce import send_big_mine_announce
from modules.first_find   import check_race_win
from modules.mining_colors import (
    format_mining_rarity,
    format_ore_name,
    get_rarity_display_name,
    get_default_weight_range,
    rainbow_text,
    rarity_sort_key,
)
from modules.mining_weights import (
    generate_weight,
    compute_final_value,
    weights_enabled,
    get_rarity_cap,
    get_multiplier_cap,
    add_weight_record,
    should_announce,
)

# Cross-process write lock: prevents SQLite "database is locked" under multi-bot
_MINE_WRITE_LOCK = FileLock(_cfg.DB_PATH + ".write.lock", timeout=3)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_clip(msg: str, limit: int = 240) -> str:
    """Clip to limit chars without cutting a <#RRGGBB> color tag in half."""
    if len(msg) <= limit:
        return msg
    cut = limit
    for i in range(cut - 1, max(cut - 10, -1), -1):
        if msg[i] == '<':
            if i + 1 < len(msg) and msg[i + 1] == '#':
                close_pos = msg.find('>', i)
                if close_pos < 0 or close_pos >= cut:
                    cut = i
            break
    return msg[:cut]


def _w(bot, user_id, msg):
    return bot.highrise.send_whisper(user_id, _safe_clip(str(msg)))


def _fmt(val: int) -> str:
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f}M"
    if val >= 1_000:
        return f"{val // 1_000}K"
    return f"{val:,}"


def _can_mine_admin(username: str) -> bool:
    return is_admin(username) or is_owner(username) or can_manage_economy(username)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _seconds_since(iso_str: str | None) -> float:
    if not iso_str:
        return 999_999
    try:
        dt = datetime.strptime(iso_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return 999_999


def _boost_active(until_iso: str | None) -> bool:
    if not until_iso:
        return False
    try:
        dt = datetime.strptime(until_iso, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < dt
    except Exception:
        return False


def _boost_mins_left(until_iso: str | None) -> int:
    if not until_iso:
        return 0
    try:
        dt  = datetime.strptime(until_iso, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        sec = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(sec // 60))
    except Exception:
        return 0


def _xp_for_level(level: int) -> int:
    # Quadratic curve with multiplier 150 (was 100).
    # Lv1→2: 150 MXP (~4 mines) — quick intro milestone.
    # Lv5→6: 3 750 MXP (~104 mines, ~5 days) — mid-game pace.
    # Lv10→11: 15 000 MXP (~420 mines, ~21 days) — sustained grind.
    # Combined with reduced common/uncommon MXP, rare finds now feel
    # meaningfully faster than grinding common ores.
    return level * level * 150


def _is_in_room(username: str) -> bool:
    try:
        from modules.gold import _room_cache
        return username.lower() in _room_cache
    except Exception:
        return True  # if can't check, allow


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PICKAXE_NAMES = {
    1:  "Worn Pickaxe",
    2:  "Copper Pickaxe",
    3:  "Iron Pickaxe",
    4:  "Steel Pickaxe",
    5:  "Silver Pickaxe",
    6:  "Gold Pickaxe",
    7:  "Tungsten Pickaxe",
    8:  "Platinum Pickaxe",
    9:  "Titanium Pickaxe",
    10: "Master Pickaxe",
}

COOLDOWNS = {1: 30, 2: 55, 3: 50, 4: 45, 5: 40, 6: 35, 7: 30, 8: 25, 9: 20, 10: 15}

# Rarity → (base_drop_pct, mxp_range)
# Drop percentages are unchanged (well-tested 68/20/8/2.5/1/0.4/0.1 distribution).
# MXP ranges reduced on common/uncommon so rare+ finds feel significantly more
# rewarding; the XP curve multiplier was raised to 150 to keep overall pace healthy.
RARITIES = {
    "common":    (65.00,   (3,      8)),
    "uncommon":  (22.00,   (10,    18)),
    "rare":      (10.00,   (35,    70)),
    "epic":      ( 2.75,   (100,  200)),
    "legendary": ( 0.23,   (350,  650)),    # ~1 in 435
    "mythic":    ( 0.015,  (1800, 1800)),   # ~1 in 6,667
    "ultra_rare":( 0.004,  (7500, 7500)),   # ~1 in 25,000
    "prismatic": ( 0.0008, (12000, 12000)), # ~1 in 125,000
    "exotic":    ( 0.0002, (20000, 20000)), # ~1 in 500,000
}

RARITY_ORDER = [
    "common", "uncommon", "rare", "epic",
    "legendary", "mythic", "ultra_rare", "prismatic", "exotic",
]

ANNOUNCE_RARITIES = {"legendary", "mythic", "ultra_rare", "prismatic", "exotic"}

# Upgrade requirements: {target_level: (coins, [(ore_id, qty), ...])}
# Balance notes:
#   Lv2-4: coin costs raised slightly so the first few upgrades feel earned,
#           not trivially purchased after one day's mining.
#   Lv5-6: unchanged — platinum (1 %) and gold (2 %) are already the real gate.
#   Lv7-8: coin cost reduced and ore qty trimmed. Epic ores (jade/topaz/gems)
#           each have ~0.6 % effective drop chance, so 10 jade = ~1 600 mines
#           (~80 days). Reducing to 6 keeps Lv7 reachable in ~1-2 months.
#   Lv9:   coin 1.5M→1M; diamond+opal reduced to 1 each — mythic ores are
#           ~0.13 % each, so 2 = ~1 500 mines. 1 each = ~750 mines (~37 days).
#   Lv10:  coin 3M→2M; ultra-rare ore gate (meteorite ~0.05 %) is the true
#           bottleneck, so the coin ask was halved to balance that.
UPGRADE_REQS = {
    2:  (2_000,       [("copper_ore", 20)]),            # coins 1K→2K
    3:  (8_000,       [("iron_ore", 20)]),              # coins 5K→8K
    4:  (20_000,      [("silver_ore", 15)]),            # coins 15K→20K
    5:  (50_000,      [("gold_ore", 10)]),              # unchanged
    6:  (120_000,     [("platinum_ore", 5), ("quartz", 20)]),   # unchanged
    7:  (200_000,     [("jade", 6), ("topaz", 6)]),             # coins 300K→200K; ores 10→6 each
    8:  (500_000,     [("emerald", 2), ("ruby", 2), ("sapphire", 2)]),  # coins 750K→500K; ores 3→2
    9:  (1_000_000,   [("diamond", 1), ("opal", 1)]),           # coins 1.5M→1M; ores 2→1
    10: (2_000_000,   [("black_opal", 1), ("alexandrite", 1), ("meteorite_fragment", 1)]),  # coins 3M→2M
}

# Craft recipes: {item_id: {req: [(ore, qty)], reward_type, reward_id, display, emoji}}
CRAFT_RECIPES = {
    "miner_badge": {
        "display": "⛏️ Miner Badge",
        "emoji":   "⛏️",
        "req":     [("iron_ore", 50), ("silver_ore", 20)],
        "reward_type": "badge",
        "reward_id":   "miner_badge",
    },
    "gem_hunter_badge": {
        "display": "💎 Gem Hunter Badge",
        "emoji":   "💎",
        "req":     [("emerald", 1), ("ruby", 1), ("sapphire", 1)],
        "reward_type": "badge",
        "reward_id":   "gem_hunter_badge",
    },
    "lounge_miner": {
        "display": "[Lounge Miner]",
        "emoji":   "⛏️",
        "req":     [("gold_ore", 25), ("diamond", 1)],
        "reward_type": "title",
        "reward_id":   "lounge_miner",
    },
    "master_miner": {
        "display": "[Master Miner]",
        "emoji":   "⚙️",
        "req":     [("platinum_ore", 10), ("diamond", 2), ("black_opal", 1)],
        "reward_type": "title",
        "reward_id":   "master_miner",
    },
    "starfinder": {
        "display": "[Starfinder]",
        "emoji":   "☄️",
        "req":     [("meteorite_fragment", 1)],
        "reward_type": "title",
        "reward_id":   "starfinder",
    },
}

# Mine shop items: {item_id: {name, emoji, price, effect}}
# Prices rebalanced so purchasing energy is a genuine but fair trade-off.
# Old prices (5K/15K/25K/25K) cost more than you could earn back from the
# extra mines — players would never buy them.  New prices:
#   Energy Tea  1 500c → 25 energy (5 mines, ~1 600c EV) — slight convenience cost.
#   Smoothie    5 000c → 100 energy (full reset, ~6 600c EV) — worth it for grinders.
#   Lucky Charm 6 000c → 20 min luck boost (was 25K for 15 min) — now accessible.
#   Focus Music 6 000c → 20 min XP 2× boost (was 25K for 15 min) — now accessible.
MINE_SHOP_ITEMS = {
    "energy_tea":     {"name": "Energy Tea",     "emoji": "🍵", "price": 1_500,  "energy": 25,  "effect": "energy"},
    "energy_smoothie":{"name": "Energy Smoothie","emoji": "🥤", "price": 5_000,  "energy": 100, "effect": "energy"},
    "lucky_charm":    {"name": "Lucky Charm",    "emoji": "🍀", "price": 6_000,  "mins": 20,    "effect": "luck_boost"},
    "focus_music":    {"name": "Focus Music",    "emoji": "🎵", "price": 6_000,  "mins": 20,    "effect": "xp_boost"},
}
_MINE_SHOP_LIST = [k for k in MINE_SHOP_ITEMS.keys() if MINE_SHOP_ITEMS[k].get("effect") != "energy"]  # energy items removed

VALID_MINING_EVENTS = {
    "double_ore":             "Drop quantities x2",
    "double_mxp":             "Mining XP x2",
    "lucky_hour":             "Rare chance +50% relative",
    "energy_free":            "!mine costs 0 energy",
    "meteor_rush":            "Ultra rare chance x2",
    # New mining events (B-project)
    "lucky_rush":             "Luck +25% — better Rare+ drops",
    "heavy_ore_rush":         "+25% heavier ore weights",
    "ore_value_surge":        "Ore value 2x",
    "mining_haste":           "Cooldown -25%",
    "legendary_rush":         "+50% Legendary+ chance",
    "prismatic_hunt":         "+100% Prismatic chance",
    "exotic_hunt":            "+100% Exotic chance",
    "admins_mining_blessing": "All mining boosts active!",
}


# ---------------------------------------------------------------------------
# Drop logic
# ---------------------------------------------------------------------------

def _roll_drop(
    tool_level: int,
    is_vip: bool,
    has_luck_boost: bool,
    mine_event: dict | None,
    event_effects: dict | None = None,
) -> tuple[dict, int]:
    """Return (mining_item_dict, mxp) for one mine action."""
    items = db.get_all_mining_items(drop_enabled=True)
    by_rarity: dict[str, list] = {}
    for it in items:
        by_rarity.setdefault(it["rarity"], []).append(it)

    # Build weighted probabilities
    probs = {r: v[0] for r, v in RARITIES.items()}

    # Tool level boosts rare+ by 0.5% per level above 1 (redistributed from common)
    tool_bonus = (tool_level - 1) * 0.5
    if tool_bonus > 0:
        boost_targets = [
            "rare", "epic", "legendary", "mythic",
            "ultra_rare", "prismatic", "exotic",
        ]
        per = tool_bonus / len(boost_targets)
        for t in boost_targets:
            probs[t] += per
        probs["common"] = max(0, probs["common"] - tool_bonus)

    _rare_plus = ["rare", "epic", "legendary", "mythic", "ultra_rare", "prismatic", "exotic"]
    _leg_plus  = ["legendary", "mythic", "ultra_rare", "prismatic", "exotic"]

    # VIP: +10% relative on rare+
    if is_vip:
        for r in _rare_plus:
            probs[r] *= 1.10

    # Lucky Charm: +25% relative on rare+
    if has_luck_boost:
        for r in _rare_plus:
            probs[r] *= 1.25

    # Legacy mining event effects
    if mine_event:
        eid = mine_event.get("event_id", "")
        if eid == "lucky_hour":
            for r in _rare_plus:
                probs[r] *= 1.50
        elif eid == "meteor_rush":
            for r in ("ultra_rare", "prismatic", "exotic"):
                probs[r] *= 2.0

    # B-project mining event effects (from get_event_effect)
    if event_effects:
        ml = event_effects.get("mining_luck_boost", 0.0)
        if ml > 0:
            for r in _rare_plus:
                probs[r] *= (1 + ml)
        lp = event_effects.get("legendary_plus_chance_boost", 0.0)
        if lp > 0:
            for r in _leg_plus:
                probs[r] *= (1 + lp)
        pc = event_effects.get("prismatic_chance_boost", 0.0)
        if pc > 0:
            probs["prismatic"] *= (1 + pc)
        ec = event_effects.get("exotic_chance_boost", 0.0)
        if ec > 0:
            probs["exotic"] *= (1 + ec)

    # Mythic+ boost caps — prevent events/VIP/luck from making top rarities too easy
    _MYTHIC_CAPS = {
        "mythic":     0.05,
        "ultra_rare": 0.01,
        "prismatic":  0.002,
        "exotic":     0.0005,
    }
    for _r, _cap in _MYTHIC_CAPS.items():
        if probs.get(_r, 0) > _cap:
            probs[_r] = _cap

    # Normalize to 100
    total = sum(probs.values())
    cumulative = 0.0
    roll = random.uniform(0, total)
    chosen_rarity = "common"
    for r in RARITY_ORDER:
        cumulative += probs.get(r, 0)
        if roll <= cumulative:
            chosen_rarity = r
            break

    # Pick a random item in that rarity
    pool = by_rarity.get(chosen_rarity, by_rarity.get("common", []))
    if not pool:
        pool = items
    chosen_item = random.choice(pool)

    # MXP
    lo, hi = RARITIES.get(chosen_rarity, RARITIES["common"])[1]
    mxp = random.randint(lo, hi)

    return chosen_item, mxp


# ---------------------------------------------------------------------------
# /mine  /m  /dig
# ---------------------------------------------------------------------------

async def handle_mine(bot: BaseBot, user: User) -> None:
    uname = user.username

    # Mining enabled?
    if db.get_mine_setting("mining_enabled", "true") != "true":
        await _w(bot, user.id, "⛔ Mining is currently disabled.")
        return

    # Room presence
    if db.get_mine_setting("mining_requires_room", "true") == "true":
        if not _is_in_room(uname):
            await _w(bot, user.id, "Join the room to mine.")
            return

    miner = db.get_or_create_miner(uname)

    # Fetch active mining event + B-project event effects once
    mine_event = db.get_active_mining_event()
    _event_eff: dict = {}
    try:
        from modules.events import get_event_effect as _gee_b
        _event_eff = _gee_b()
    except Exception:
        pass

    # Cooldown (apply cooldown_reduction from B-project mining_haste event)
    base_cd  = int(db.get_mine_setting("base_cooldown_seconds", "30"))
    tool_cd  = COOLDOWNS.get(miner["tool_level"], 60)
    cooldown = min(base_cd, tool_cd)
    _cd_red  = _event_eff.get("cooldown_reduction", 0.0)
    if _cd_red > 0:
        cooldown = max(5, int(cooldown * (1 - _cd_red)))
    secs_ago = _seconds_since(miner["last_mine_at"])
    if secs_ago < cooldown:
        wait = int(cooldown - secs_ago)
        await _w(bot, user.id,
                 f"<#FFCC00>⏳ Cooldown<#FFFFFF>: Mine again in {wait}s.")
        return

    db.ensure_user(user.id, uname)
    is_vip = db.owns_item(user.id, "vip")

    # Roll drop (pass B-project event effects to improve rarity chances)
    has_luck = _boost_active(miner.get("luck_boost_until"))
    has_xp   = _boost_active(miner.get("xp_boost_until"))
    item, mxp = _roll_drop(miner["tool_level"], is_vip, has_luck, mine_event, _event_eff)

    # Owner-forced drop override — silent to player; owner sees status via /forcedropstatus
    _forced = db.get_active_forced_drop(uname, user.id)
    if _forced:
        if _forced["forced_type"] == "rarity":
            _f_pool = [
                it for it in db.get_all_mining_items(drop_enabled=False)
                if it["rarity"] == _forced["forced_value"]
            ]
            if _f_pool:
                item = random.choice(_f_pool)
                _lo2, _hi2 = RARITIES.get(item["rarity"], RARITIES["common"])[1]
                mxp = random.randint(_lo2, _hi2)
                db.mark_forced_drop_used(_forced["id"])
        elif _forced["forced_type"] == "ore":
            _f_item = db.get_mining_item(_forced["forced_value"])
            if _f_item:
                item = _f_item
                _lo2, _hi2 = RARITIES.get(item["rarity"], RARITIES["common"])[1]
                mxp = random.randint(_lo2, _hi2)
                db.mark_forced_drop_used(_forced["id"])

    # VIP: +10% MXP
    if is_vip:
        mxp = int(mxp * 1.10)

    # XP boost: 2x MXP
    if has_xp:
        mxp *= 2

    # Legacy event double_mxp
    if mine_event and mine_event.get("event_id") == "double_mxp":
        mxp *= 2

    # B-project mxp_multiplier (skip if legacy double_mxp already applied)
    _mxp_mult = _event_eff.get("mxp_multiplier", 1.0)
    if _mxp_mult > 1.0 and not (mine_event and mine_event.get("event_id") == "double_mxp"):
        mxp = int(mxp * _mxp_mult)

    # Admin's Blessing (legacy general event): 2x MXP + 2x quantity
    _blessing_active = _event_eff.get("mining_boost", False)
    if _blessing_active:
        mxp *= 2

    # Quantity (normally 1; double_ore event or Admin's Blessing gives 2)
    double_ore = (mine_event and mine_event.get("event_id") == "double_ore")
    qty = 2 if (double_ore or _blessing_active) else 1

    # Persist changes
    new_mines  = miner["total_mines"] + 1
    new_ores   = miner["total_ores"] + qty
    new_mxp    = miner["mining_xp"] + mxp

    # Level up check
    cur_lvl  = miner["mining_level"]
    new_lvl  = cur_lvl
    while new_mxp >= _xp_for_level(new_lvl):
        new_mxp -= _xp_for_level(new_lvl)
        new_lvl += 1

    is_rare  = item["rarity"] in ANNOUNCE_RARITIES
    new_rare = miner["rare_finds"] + (1 if is_rare else 0)

    # Weight generation (apply weight_luck_boost from B-project heavy_ore_rush)
    _w_enabled = weights_enabled()
    ore_weight = generate_weight(item["rarity"]) if _w_enabled else None
    if ore_weight is not None:
        _wl = _event_eff.get("weight_luck_boost", 0.0)
        if _wl > 0:
            ore_weight = round(ore_weight * (1 + _wl), 2)
    _base_val    = item.get("sell_value", 0)
    final_val    = (compute_final_value(_base_val, ore_weight)
                    if ore_weight is not None else 0)
    _weight_mult = ore_weight if ore_weight is not None else 1.0
    # Apply ore_value_multiplier with configurable stacking cap
    _val_mult    = _event_eff.get("ore_value_multiplier", 1.0)
    _is_blessing = _event_eff.get("mining_boost", False)
    _mult_cap    = get_multiplier_cap(_is_blessing)
    _val_mult    = min(_val_mult, _mult_cap)
    if _val_mult > 1.0 and final_val > 0:
        final_val = int(final_val * _val_mult)
    # Apply rarity value cap (jackpot ceiling, staff-configurable)
    _rarity_cap  = get_rarity_cap(item["rarity"])
    _cap_applied = False
    if _rarity_cap > 0 and final_val > _rarity_cap:
        _cap_applied = True
        final_val    = _rarity_cap

    try:
        with _MINE_WRITE_LOCK:
            db.update_miner(uname,
                total_mines=new_mines,
                total_ores=new_ores,
                mining_xp=new_mxp,
                mining_level=new_lvl,
                rare_finds=new_rare,
                last_mine_at=_now_iso(),
            )
            db.add_ore(uname, item["item_id"], qty)
            db.log_mine(uname, "mine", item["item_id"], qty, 0, item["rarity"])
            db.log_mining_payout(
                uname, item["item_id"], item["name"], item["rarity"],
                ore_weight, _base_val, _weight_mult, _val_mult, final_val,
                _cap_applied, _rarity_cap if _cap_applied else 0,
            )
    except _FileLockTimeout:
        await _w(bot, user.id, "⏳ Mining DB busy. Try !mine again.")
        return

    # ── Collection tracking ───────────────────────────────────────────────────
    _is_new_disc = False
    try:
        _is_new_disc = db.record_collection_item(
            user.id, uname, "mining",
            item["item_id"], item["name"], item["rarity"], final_val,
        )
    except Exception:
        pass
    # Announce first discovery (whisper only — big_announce handles public)
    if _is_new_disc:
        _am_s = _am_session_stats.get(user.id)
        if _am_s is None:  # manual mine only (not during automine)
            await _w(bot, user.id,
                     f"📖 New ore! {item['emoji']} {item['name']} "
                     f"added to your Ore Book!"[:249])
    # Feed session accumulator (automine)
    _am_s = _am_session_stats.get(user.id)
    if _am_s is not None:
        _am_s["value"]  += final_val
        _am_s["count"]  += 1
        if is_rare:
            _am_s["rare_finds"].append(item["name"])
        if _is_new_disc:
            _am_s["new_discoveries"].append(item["name"])
        if final_val > _am_s["best_value"]:
            _am_s["best_value"] = final_val
            _am_s["best_name"]  = f"{item['emoji']} {item['name']}"

    # Build reply — combined header + ore result format
    qty_str     = f" x{qty}" if qty > 1 else ""
    lvlup       = f" ⬆️ Lv {new_lvl}!" if new_lvl > cur_lvl else ""
    rar_label   = format_mining_rarity(item["rarity"])
    ore_colored = format_ore_name(f"{item['emoji']} {item['name']}", item["rarity"])

    if ore_weight is not None:
        # Try to fit rarity label + ore + weight + value in one message
        inline = (f"<#66CCFF>⛏️ Mining<#FFFFFF>\n"
                  f"You mined {rar_label} {ore_colored}{qty_str}"
                  f" | ⚖️ {ore_weight}kg | 💰 {_fmt(final_val)}c{lvlup}")
        if len(inline) <= 249:
            await _w(bot, user.id, inline)
            await _w(bot, user.id, f"⭐ MXP: +{_fmt(mxp)}")
        else:
            # Fallback two-message (e.g. prismatic rainbow ore names are ~90 chars)
            line1 = (f"<#66CCFF>⛏️ Mining<#FFFFFF>\n"
                     f"You mined {rar_label} {ore_colored}{qty_str}{lvlup}")
            await _w(bot, user.id, line1[:249])
            await _w(bot, user.id,
                     f"⚖️ {ore_weight}kg | 💰 {_fmt(final_val)}c | ⭐ MXP: +{_fmt(mxp)}")
        try:
            add_weight_record(uname, user.id, item["item_id"], item["rarity"],
                              ore_weight, item.get("sell_value", 0), final_val, mxp)
        except Exception:
            pass
    else:
        line1 = (f"<#66CCFF>⛏️ Mining<#FFFFFF>\n"
                 f"You mined {rar_label} {ore_colored}{qty_str}{lvlup}")
        await _w(bot, user.id, line1[:249])
        await _w(bot, user.id, f"⭐ MXP: +{_fmt(mxp)}")

    # Race win check — fires for every drop, returns fast if no active race
    try:
        await check_race_win(bot, user.id, uname, "mining", item["rarity"], item.get("name", ""))
    except Exception as _ffe:
        print(f"[MINING] race_win error: {_ffe}")
    if should_announce(item["rarity"], item["item_id"]):
        try:
            await send_big_mine_announce(bot, item["rarity"], uname,
                                         item["name"], item["emoji"],
                                         weight=ore_weight, value=final_val, xp=mxp)
        except Exception as _bae:
            print(f"[MINING] big_announce error: {_bae}")

    # Level up announce
    if new_lvl > cur_lvl:
        _disp = db.get_display_name(user.id, uname)
        try:
            await bot.highrise.chat(
                f"⛏️ {_disp} reached Mining Level {new_lvl}! Keep digging!"[:249]
            )
        except Exception:
            pass


def _maybe_reset_energy(miner: dict, username: str) -> None:
    """Reset energy daily if needed."""
    if db.get_mine_setting("daily_energy_reset", "true") != "true":
        return
    last = miner.get("last_energy_reset") or ""
    try:
        if last:
            dt   = datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            diff = (datetime.now(timezone.utc) - dt).total_seconds()
            if diff < 86400:
                return
    except Exception:
        pass
    max_e = miner.get("max_energy", 100)
    db.update_miner(username, energy=max_e, last_energy_reset=_now_iso())


# ---------------------------------------------------------------------------
# /tool  /pickaxe
# ---------------------------------------------------------------------------

async def handle_tool(bot: BaseBot, user: User) -> None:
    miner = db.get_or_create_miner(user.username)
    lvl   = miner["tool_level"]
    name  = PICKAXE_NAMES.get(lvl, "Unknown Pickaxe")
    cd    = COOLDOWNS.get(lvl, 60)
    luck  = (lvl - 1) * 0.5
    msg   = f"⛏️ Pickaxe Lv {lvl} {name} | Cooldown {cd}s | Luck +{luck:.1f}%"
    if lvl < 10:
        reqs  = UPGRADE_REQS.get(lvl + 1, (0, []))
        coins = reqs[0]
        msg   += f"\nUpgrade: !upgradetool | Cost {_fmt(coins)}c"
    else:
        msg   += "\n🏆 Max level!"
    await _w(bot, user.id, msg[:249])


# ---------------------------------------------------------------------------
# /upgradetool  /upick
# ---------------------------------------------------------------------------

async def handle_upgradetool(bot: BaseBot, user: User) -> None:
    db.ensure_user(user.id, user.username)
    miner = db.get_or_create_miner(user.username)
    lvl   = miner["tool_level"]
    if lvl >= 10:
        await _w(bot, user.id, "Your pickaxe is already max level.")
        return

    target_lvl = lvl + 1
    reqs       = UPGRADE_REQS.get(target_lvl)
    if not reqs:
        await _w(bot, user.id, "Upgrade data missing. Contact admin.")
        return

    coin_cost, ore_reqs = reqs
    balance = db.get_balance(user.id)

    # Check coins
    missing_parts = []
    if balance < coin_cost:
        missing_parts.append(f"{_fmt(coin_cost)}c (have {_fmt(balance)}c)")

    # Check ores
    for ore_id, qty in ore_reqs:
        have = db.get_ore_qty(user.username, ore_id)
        if have < qty:
            it = db.get_mining_item(ore_id)
            name = it["name"] if it else ore_id
            missing_parts.append(f"{name} x{qty} (have {have})")

    if missing_parts:
        need_str = ", ".join(missing_parts)
        await _w(bot, user.id,
                 (f"⛏️ Upgrade to Lv {target_lvl}: Not enough resources.\n"
                  f"Need: {need_str}")[:249])
        return

    # Deduct — atomic-ish (coins first, then ores)
    ok = db.buy_item(user.id, user.username, f"pickaxe_lv{target_lvl}", "upgrade", coin_cost)
    if not ok:
        await _w(bot, user.id, "Not enough coins. Try again.")
        return

    for ore_id, qty in ore_reqs:
        db.remove_ore(user.username, ore_id, qty)

    db.update_miner(user.username, tool_level=target_lvl)
    db.log_mine(user.username, "upgrade", f"pickaxe_lv{target_lvl}", 1, coin_cost, "tool upgrade")

    name = PICKAXE_NAMES.get(target_lvl, f"Lv{target_lvl} Pickaxe")
    await _w(bot, user.id, f"✅ Pickaxe upgraded to Lv {target_lvl} {name}.")
    try:
        _disp = db.get_display_name(user.id, user.username)
        await bot.highrise.chat(
            f"⛏️ {_disp} upgraded to {name}!"[:249]
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /mineprofile  /mp  /minerank
# ---------------------------------------------------------------------------

async def handle_mineprofile(bot: BaseBot, user: User, args: list[str]) -> None:
    page  = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
    miner = db.get_or_create_miner(user.username)
    lvl   = miner["mining_level"]
    mxp   = miner["mining_xp"]
    nxp   = _xp_for_level(lvl)
    pick  = miner["tool_level"]

    if page == 1:
        luck_left = f" 🍀{_boost_mins_left(miner.get('luck_boost_until'))}m" if _boost_active(miner.get("luck_boost_until")) else ""
        xp_left   = f" 🎵{_boost_mins_left(miner.get('xp_boost_until'))}m"  if _boost_active(miner.get("xp_boost_until"))   else ""
        msg = (
            f"⛏️ @{user.username} Mining Profile\n"
            f"Lv {lvl} | {_fmt(mxp)}/{_fmt(nxp)} MXP\n"
            f"Pickaxe Lv {pick} {PICKAXE_NAMES.get(pick,'?')}\n"
            f"Boosts:{luck_left}{xp_left}\n"
            f"Page 2: !mp 2"
        )
    else:
        event = db.get_active_mining_event()
        evt   = f" | Event: {event['event_id']}" if event else ""
        msg   = (
            f"⛏️ @{user.username} Mining Stats\n"
            f"Mines: {_fmt(miner['total_mines'])} | Ores: {_fmt(miner['total_ores'])}\n"
            f"Rare finds: {miner['rare_finds']}\n"
            f"Coins earned: {_fmt(miner['coins_earned'])}c\n"
            f"Streak: {miner['streak_days']} days{evt}"
        )
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# /mineinv  /ores
# ---------------------------------------------------------------------------

async def handle_mineinv(bot: BaseBot, user: User, args: list[str]) -> None:
    inv = db.get_inventory(user.username)
    if not inv:
        await _w(bot, user.id, "🎒 No ores yet. Use !mine to start mining!")
        return

    sub = args[1].lower() if len(args) > 1 else ""

    # Rarity filter: !ores prismatic → delegate to handle_orelist
    if sub and sub not in ("all",) and not sub.isdigit():
        rar_check = _resolve_rarity_arg(sub)
        if rar_check in RARITIES:
            await handle_orelist(bot, user, ["ores", sub] + list(args[2:]))
            return

    # Full paginated view: !mineinv all  or  !mineinv <page>
    if sub == "all" or sub.isdigit():
        page = max(1, int(sub)) if sub.isdigit() else 1
        per  = 4
        total_pages = max(1, (len(inv) + per - 1) // per)
        page = min(page, total_pages)
        chunk = inv[(page - 1) * per : page * per]
        lines = [f"🎒 Ores ({page}/{total_pages})"]
        for r in chunk:
            lbl = _rar_plain(r["rarity"])
            _n = (f"<#FF66CC>{r['name']}<#FFFFFF>" if r["rarity"] in ("prismatic", "ultra_rare") else
                  f"<#FF0000>{r['name']}<#FFFFFF>" if r["rarity"] == "exotic" else r["name"])
            lines.append(f"[{lbl}] {r['emoji']}{_n} x{r['quantity']}")
        if page < total_pages:
            lines.append(f"!mineinv {page + 1}")
        await _w(bot, user.id, "\n".join(lines))
        return

    # Default: top 5 by quantity
    top5        = sorted(inv, key=lambda r: r["quantity"], reverse=True)[:5]
    total_types = len(inv)
    total_qty   = sum(r["quantity"] for r in inv)
    lines = [f"🎒 Ores | {total_types} types | {_fmt(total_qty)} total"]
    for r in top5:
        lbl = _rar_plain(r["rarity"])
        _n = (f"<#FF66CC>{r['name']}<#FFFFFF>" if r["rarity"] in ("prismatic", "ultra_rare") else
              f"<#FF0000>{r['name']}<#FFFFFF>" if r["rarity"] == "exotic" else r["name"])
        lines.append(f"[{lbl}] {r['emoji']}{_n} x{r['quantity']}")
    if total_types > 5:
        lines.append("!mineinv all — full list | !sellores to sell")
    else:
        lines.append("!sellores to sell all")
    await _w(bot, user.id, "\n".join(lines))


# ---------------------------------------------------------------------------
# /sellores  /sellore <id> [qty]
# ---------------------------------------------------------------------------

async def handle_sellores(bot: BaseBot, user: User) -> None:
    db.ensure_user(user.id, user.username)
    result = db.sell_all_ores(user.username, user.id)
    if result["coins"] == 0:
        await _w(bot, user.id,
                 "⚠️ Nothing to sell.\nTry !mine or !automine first.")
        return
    db.update_miner(user.username, coins_earned=db.get_or_create_miner(user.username)["coins_earned"] + result["coins"])
    new_bal = db.get_balance(user.id)
    await _w(bot, user.id,
             f"💰 Sold Ores\n"
             f"Items sold: {result['count']}\n"
             f"Total: {_fmt(result['coins'])}c\n"
             f"New Balance: {_fmt(new_bal)}c")


async def handle_sellore(bot: BaseBot, user: User, args: list[str]) -> None:
    db.ensure_user(user.id, user.username)
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !sellore <ore_id> <amount>  or  !sellore all")
        return
    sub = args[1].lower()
    if sub == "all":
        await handle_sellores(bot, user)
        return

    ore_id = sub
    it     = db.get_mining_item(ore_id)
    if it is None:
        await _w(bot, user.id, f"Unknown ore: {ore_id}. Use !orelist.")
        return

    qty_raw = args[2] if len(args) > 2 else "1"
    if not qty_raw.isdigit() or int(qty_raw) < 1:
        await _w(bot, user.id, "Amount must be a positive number.")
        return
    qty = int(qty_raw)

    res = db.sell_ore_item(user.username, user.id, ore_id, qty)
    if not res["ok"]:
        if res["error"] == "unknown_item":
            await _w(bot, user.id, f"Unknown ore: {ore_id}.")
        elif res["error"] == "not_enough":
            await _w(bot, user.id,
                     f"You only have {res.get('have', 0)} {it['name']}.")
        else:
            await _w(bot, user.id, "Sale failed. Try again!")
        return

    miner = db.get_or_create_miner(user.username)
    db.update_miner(user.username, coins_earned=miner["coins_earned"] + res["coins"])
    await _w(bot, user.id,
             f"✅ Sold {it['emoji']} {it['name']} x{qty} for {_fmt(res['coins'])}c.")


# ---------------------------------------------------------------------------
# /minelb
# ---------------------------------------------------------------------------

async def handle_minelb(bot: BaseBot, user: User, args: list[str]) -> None:
    sub = args[1].lower() if len(args) > 1 else "mines"

    if sub == "level":
        rows  = db.get_mine_leaderboard("mining_level")
        title = "⛏️ Top Miners (Level)"
        fmt   = lambda r: f"Lv {r['val']}"
    elif sub == "ores":
        rows  = db.get_mine_leaderboard("total_ores")
        title = "⛏️ Most Ores Mined"
        fmt   = lambda r: _fmt(r["val"])
    elif sub == "rare":
        rows  = db.get_mine_leaderboard("rare_finds")
        title = "💎 Rare Finders"
        fmt   = lambda r: str(r["val"])
    elif sub == "coins":
        rows  = db.get_mine_leaderboard("coins_earned")
        title = "💰 Mining Coins Earned"
        fmt   = lambda r: f"{_fmt(r['val'])}c"
    elif sub == "meteorite":
        rows  = db.get_meteorite_leaderboard()
        title = "☄️ Meteorite Finds"
        fmt   = lambda r: str(r["val"])
    else:
        rows  = db.get_mine_leaderboard("total_mines")
        title = "⛏️ Total Mines"
        fmt   = lambda r: _fmt(r["val"])

    if not rows:
        await _w(bot, user.id, f"{title}: No data yet.")
        return

    lines = [title]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. @{r['username']} — {fmt(r)}")
    lines.append("!minelb level|ores|rare|coins")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /mineshop  /minebuy <number>
# ---------------------------------------------------------------------------

async def handle_mineshop(bot: BaseBot, user: User) -> None:
    lines = ["⛏️ Mine Shop"]
    session_items = []
    for i, (item_id, it) in enumerate(_MINE_SHOP_LIST_ITEMS(), 1):
        lines.append(f"{i} {it['emoji']} {it['name']} {_fmt(it['price'])}c")
        session_items.append({
            "num":       i,
            "item_id":   item_id,
            "name":      it["name"],
            "emoji":     it["emoji"],
            "price":     it["price"],
            "currency":  "coins",
            "shop_type": "mineshop",
        })
    lines.append("Buy: !minebuy <#>  or  !buy <#>")
    msg = "\n".join(lines)
    db.save_shop_session(user.username, "mineshop", 1, session_items)
    await _w(bot, user.id, msg[:249])


def _MINE_SHOP_LIST_ITEMS():
    return [(k, MINE_SHOP_ITEMS[k]) for k in _MINE_SHOP_LIST]


async def handle_minebuy(bot: BaseBot, user: User, args: list[str]) -> None:
    raw = args[1] if len(args) > 1 else ""
    if not raw.isdigit():
        await _w(bot, user.id, "Usage: !minebuy <number>  (see !mineshop)")
        return

    n       = int(raw)
    session = db.get_shop_session(user.username)

    # Try session first, fallback to direct number from shop list
    item_id = None
    if session and session["shop_type"] == "mineshop":
        item = next((i for i in session["items"] if i["num"] == n), None)
        if item:
            item_id = item["item_id"]
    if not item_id:
        items_list = _MINE_SHOP_LIST
        if 1 <= n <= len(items_list):
            item_id = items_list[n - 1]

    if not item_id or item_id not in MINE_SHOP_ITEMS:
        await _w(bot, user.id, "Invalid number. Use !mineshop to see items.")
        return

    it = MINE_SHOP_ITEMS[item_id]
    db.ensure_user(user.id, user.username)

    ok = db.buy_item(user.id, user.username, f"mine_{item_id}", "mine_consumable", it["price"])
    if not ok:
        bal = db.get_balance(user.id)
        await _w(bot, user.id,
                 f"Need {_fmt(it['price'])}c — you have {_fmt(bal)}c.")
        return

    miner = db.get_or_create_miner(user.username)

    if it["effect"] == "energy":
        await _w(bot, user.id,
                 f"⛏️ Energy items are no longer used. Try !minebuy 3 (Lucky Charm).")
        db.adjust_balance(user.id, it["price"])  # refund
        db.log_mine(user.username, "buy_shop_refund", item_id, 1, it["price"], "energy_removed")
        return

    elif it["effect"] == "luck_boost":
        from datetime import timedelta
        until = (datetime.now(timezone.utc) + timedelta(minutes=it["mins"])).strftime("%Y-%m-%d %H:%M:%S")
        db.update_miner(user.username, luck_boost_until=until)
        await _w(bot, user.id,
                 f"✅ Bought {it['emoji']} {it['name']}. 🍀 Active for {it['mins']}m.")

    elif it["effect"] == "xp_boost":
        from datetime import timedelta
        until = (datetime.now(timezone.utc) + timedelta(minutes=it["mins"])).strftime("%Y-%m-%d %H:%M:%S")
        db.update_miner(user.username, xp_boost_until=until)
        await _w(bot, user.id,
                 f"✅ Bought {it['emoji']} {it['name']}. 🎵 Active for {it['mins']}m.")

    db.log_mine(user.username, "buy_shop", item_id, 1, it["price"], it["effect"])


# ---------------------------------------------------------------------------
# /useenergy  /useluckboost  /usexpboost
# ---------------------------------------------------------------------------

async def handle_useenergy(bot: BaseBot, user: User, args: list[str]) -> None:
    await _w(bot, user.id,
             "⛏️ Energy is no longer used in mining. Mine freely! !mine")


async def handle_useluckboost(bot: BaseBot, user: User) -> None:
    miner = db.get_or_create_miner(user.username)
    if _boost_active(miner.get("luck_boost_until")):
        mins = _boost_mins_left(miner.get("luck_boost_until"))
        await _w(bot, user.id, f"🍀 Lucky Charm already active — {mins}m left.")
    else:
        await _w(bot, user.id, "No Lucky Charm active. Buy one: /minebuy 3")


async def handle_usexpboost(bot: BaseBot, user: User) -> None:
    miner = db.get_or_create_miner(user.username)
    if _boost_active(miner.get("xp_boost_until")):
        mins = _boost_mins_left(miner.get("xp_boost_until"))
        await _w(bot, user.id, f"🎵 Focus Music already active — {mins}m left.")
    else:
        await _w(bot, user.id, "No Focus Music active. Buy one: /minebuy 4")


# ---------------------------------------------------------------------------
# /craft  /craft <item_id>
# ---------------------------------------------------------------------------

async def handle_craft(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        # Show crafting menu
        lines = ["⚒️ Craftable Rewards"]
        for i, (rid, rec) in enumerate(CRAFT_RECIPES.items(), 1):
            reqs_str = " + ".join(f"{ore} x{qty}" for ore, qty in rec["req"])
            lines.append(f"{i} {rec['emoji']} {rec['display']} — {reqs_str}")
        lines.append("Craft: /craft <id>")
        msg = "\n".join(lines)
        await _w(bot, user.id, msg[:249])
        if len(msg) > 249:
            lines2 = ["⚒️ Craft IDs"]
            for rid in CRAFT_RECIPES:
                lines2.append(rid)
            await _w(bot, user.id, "\n".join(lines2)[:249])
        return

    item_id = args[1].lower().strip()
    rec     = CRAFT_RECIPES.get(item_id)
    if rec is None:
        await _w(bot, user.id,
                 f"Unknown craft: {item_id}. Valid: " + ", ".join(CRAFT_RECIPES))
        return

    db.ensure_user(user.id, user.username)

    # Check if already owned (for titles/badges)
    if rec["reward_type"] == "title":
        if db.owns_item(user.id, rec["reward_id"]):
            await _w(bot, user.id, f"You already own {rec['display']}.")
            return
    elif rec["reward_type"] == "badge":
        if db.owns_emoji_badge(user.username, rec["reward_id"]):
            await _w(bot, user.id, f"You already own {rec['display']}.")
            return

    # Check materials
    missing = []
    for ore_id, qty in rec["req"]:
        have = db.get_ore_qty(user.username, ore_id)
        if have < qty:
            it = db.get_mining_item(ore_id)
            name = it["name"] if it else ore_id
            missing.append(f"{name} x{qty} (have {have})")

    if missing:
        await _w(bot, user.id, "Missing: " + ", ".join(missing))
        return

    # Deduct materials
    for ore_id, qty in rec["req"]:
        db.remove_ore(user.username, ore_id, qty)

    # Grant reward
    if rec["reward_type"] == "title":
        db.grant_item(user.id, rec["reward_id"], "title")
    elif rec["reward_type"] == "badge":
        db.grant_emoji_badge(user.username, rec["reward_id"], source="craft")

    db.log_mine(user.username, "craft", item_id, 1, 0, rec["display"])
    await _w(bot, user.id, f"✅ Crafted {rec['emoji']} {rec['display']}!")
    try:
        _disp = db.get_display_name(user.id, user.username)
        await bot.highrise.chat(
            f"⚒️ {_disp} crafted {rec['emoji']} {rec['display']}!"[:249]
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /minedaily
# ---------------------------------------------------------------------------

async def handle_minedaily(bot: BaseBot, user: User) -> None:
    db.ensure_user(user.id, user.username)
    miner = db.get_or_create_miner(user.username)
    secs  = _seconds_since(miner.get("last_daily_bonus"))

    if secs < 86400:
        wait_h = int((86400 - secs) // 3600)
        wait_m = int((86400 - secs - wait_h * 3600) // 60)
        await _w(bot, user.id,
                 f"⏳ Daily mining bonus in {wait_h}h {wait_m}m.")
        return

    # Streak logic
    streak = miner["streak_days"]
    if secs < 172800:  # claimed within last 2 days
        streak += 1
    else:
        streak = 1  # reset

    # Base rewards
    coin_gain   = 500
    mxp_gain    = 50

    new_mxp    = miner["mining_xp"] + mxp_gain

    # Level up check from MXP
    cur_lvl  = miner["mining_level"]
    new_lvl  = cur_lvl
    while new_mxp >= _xp_for_level(new_lvl):
        new_mxp -= _xp_for_level(new_lvl)
        new_lvl += 1

    db.adjust_balance(user.id, coin_gain)
    db.update_miner(user.username,
        mining_xp=new_mxp,
        mining_level=new_lvl,
        streak_days=streak,
        last_daily_bonus=_now_iso(),
        coins_earned=miner["coins_earned"] + coin_gain,
    )
    db.log_mine(user.username, "minedaily", "", 0, coin_gain, f"streak={streak}")

    extra = ""
    # Streak bonuses
    if streak == 3:
        db.update_miner(user.username,
            luck_boost_until=(datetime.now(timezone.utc).__str__()[:19]))  # expire immediately — just note
        # Give lucky charm: add to shop session or just note
        extra = " 🍀 Streak 3: +Lucky Charm!"
    elif streak == 7:
        new_max = miner["max_energy"] + 100
        db.update_miner(user.username, max_energy=new_max)
        extra = f" 🥤 Streak 7: max energy +100!"
    elif streak == 30:
        db.grant_item(user.id, "chill_miner", "title")
        extra = " 🏆 Streak 30: [Chill Miner] title!"

    msg = (f"🎁 Mining daily: +{_fmt(coin_gain)}c, +{mxp_gain} MXP. "
           f"Streak {streak}.{extra}")
    await _w(bot, user.id, msg[:249])


# ---------------------------------------------------------------------------
# /miningevent  /miningevents  /startminingevent  /stopminingevent
# ---------------------------------------------------------------------------

async def handle_miningevent(bot: BaseBot, user: User) -> None:
    event = db.get_active_mining_event()
    if not event:
        await _w(bot, user.id,
                 "⛏️ No mining event active. Events: " +
                 ", ".join(VALID_MINING_EVENTS))
        return
    eid  = event["event_id"]
    desc = VALID_MINING_EVENTS.get(eid, eid)
    await _w(bot, user.id, f"⛏️ Mining Event: {eid} — {desc}")


async def handle_miningevents(bot: BaseBot, user: User) -> None:
    lines = ["⛏️ Mining Events"]
    for eid, desc in VALID_MINING_EVENTS.items():
        lines.append(f"• {eid}: {desc}")
    lines.append("Start: /startminingevent <id>")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_startminingevent(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_mine_admin(user.username):
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !startminingevent <event_id>")
        return
    eid = args[1].lower()
    if eid not in VALID_MINING_EVENTS:
        await _w(bot, user.id,
                 f"Unknown event. Valid: {', '.join(VALID_MINING_EVENTS)}")
        return
    dur = int(args[2]) if len(args) > 2 and args[2].isdigit() else 60
    db.start_mining_event(eid, user.username, dur)
    desc = VALID_MINING_EVENTS[eid]
    await _w(bot, user.id, f"✅ Mining Event '{eid}' started ({dur}m).")
    try:
        await bot.highrise.chat(f"⛏️ Mining Event: {eid}! {desc}"[:249])
    except Exception:
        pass


async def handle_stopminingevent(bot: BaseBot, user: User) -> None:
    if not _can_mine_admin(user.username):
        return
    db.stop_mining_event()
    await _w(bot, user.id, "✅ Mining event stopped.")
    try:
        await bot.highrise.chat("⛏️ Mining event ended.")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Admin commands
# ---------------------------------------------------------------------------

async def handle_miningadmin(bot: BaseBot, user: User) -> None:
    if not _can_mine_admin(user.username):
        return
    await _w(bot, user.id,
             "⛏️ Mining Admin\n"
             "!mining on|off — enable/disable\n"
             "!setminecooldown [sec]\n"
             "!addore [user] [ore] [amt]\n"
             "!settoollevel [user] [1-10]")


async def handle_mining_toggle(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_mine_admin(user.username):
        return
    sub = args[1].lower() if len(args) > 1 else ""
    if sub == "on":
        db.set_mine_setting("mining_enabled", "true")
        await _w(bot, user.id, "✅ Mining ON.")
    elif sub == "off":
        db.set_mine_setting("mining_enabled", "false")
        await _w(bot, user.id, "⛔ Mining OFF.")
    else:
        await _w(bot, user.id, "Usage: !mining on | /mining off")


async def handle_setminecooldown(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_mine_admin(user.username):
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !setminecooldown <seconds>")
        return
    db.set_mine_setting("base_cooldown_seconds", args[1])
    await _w(bot, user.id, f"✅ Mine cooldown set to {args[1]}s.")


async def handle_setmineenergycost(bot: BaseBot, user: User, args: list[str]) -> None:
    await _w(bot, user.id, "⛏️ Energy system removed. !mine has no energy cost.")


async def handle_mineconfig(bot: BaseBot, user: User) -> None:
    """/mineconfig — show all current mining configuration values (admin/manager)."""
    if not _can_mine_admin(user.username):
        await _w(bot, user.id, "Admin/manager only.")
        return
    cd   = db.get_mine_setting("base_cooldown_seconds", "30")
    en   = db.get_mine_setting("mining_enabled", "true")
    rr   = db.get_mine_setting("mining_requires_room", "true")
    ra   = db.get_mine_setting("rare_announce_enabled", "true")
    await _w(bot, user.id,
             f"⛏️ Mine Config\n"
             f"Cooldown: {cd}s | Mining: {'ON' if en == 'true' else 'OFF'}\n"
             f"Room req: {'YES' if rr == 'true' else 'NO'}\n"
             f"Rare announce: {'ON' if ra == 'true' else 'OFF'}")


async def handle_minepanel(bot: BaseBot, user: User) -> None:
    """/minepanel /miningpanel /mineadmin — mining staff configuration panel."""
    if not _can_mine_admin(user.username):
        await _w(bot, user.id, "Admin/manager only.")
        return
    cd     = db.get_mine_setting("base_cooldown_seconds", "30")
    en     = db.get_mine_setting("mining_enabled", "true") == "true"
    # Weight settings
    try:
        from modules.mining_weights import (
            weights_enabled as _wen, get_weight_setting as _gws,
        )
        w_on    = _wen()
        scale   = _gws("weight_value_multiplier_scale", "1.0")
        lb_mode = _gws("weight_lb_mode", "best")
    except Exception:
        w_on, scale, lb_mode = True, "1.0", "best"
    # Announce settings
    ann_on  = db.get_room_setting("mining_announce_enabled", "1") == "1"
    ann_min = db.get_room_setting("mining_announce_min_rarity", "legendary")
    # Override count
    try:
        import database as _db
        conn  = _db.get_connection()
        ovr_c = conn.execute(
            "SELECT COUNT(*) FROM mining_weight_settings WHERE key LIKE 'ore_announce_%'"
        ).fetchone()[0]
        conn.close()
    except Exception:
        ovr_c = 0
    # Mining event + Admin's Blessing
    mine_event = db.get_active_mining_event()
    ev_str     = mine_event.get("event_id", "none") if mine_event else "none"
    blessing   = False
    try:
        from modules.events import get_event_effect as _gee
        blessing = _gee().get("mining_boost", False)
    except Exception:
        pass
    if blessing:
        ev_str = f"{ev_str} + Blessing"
    # Rare finds today (recent from ore_weight_records)
    try:
        import database as _db2
        conn2 = _db2.get_connection()
        rare_today = conn2.execute(
            """SELECT COUNT(*) FROM ore_weight_records
               WHERE rarity IN ('legendary','mythic','ultra_rare','prismatic','exotic')
               AND date(mined_at)=date('now')"""
        ).fetchone()[0]
        conn2.close()
    except Exception:
        rare_today = 0

    await _w(bot, user.id, "<#66CCFF>⛏️ Mining Panel<#FFFFFF>")
    await _w(bot, user.id,
             f"Status: {'ON' if en else 'OFF'} | Cooldown: {cd}s | "
             f"Weights: {'ON' if w_on else 'OFF'}")
    await _w(bot, user.id,
             f"Scale: {scale} | LB: {lb_mode} | "
             f"Announce: {ann_min.capitalize()}+ {'ON' if ann_on else 'OFF'} "
             f"| Overrides: {ovr_c}")
    await _w(bot, user.id,
             f"Events: {ev_str} | Rare finds today: {rare_today}")
    await _w(bot, user.id,
             "!setminecooldown !setmineweights !setweightscale "
             "!setmineannounce !setrarityweightrange"[:249])


async def handle_mineeventstatus(bot: BaseBot, user: User) -> None:
    """/mineeventstatus — show active mining event if any."""
    mine_event = db.get_active_mining_event()
    effect     = {}
    try:
        from modules.events import get_event_effect
        effect = get_event_effect()
    except Exception:
        pass
    blessing = effect.get("mining_boost", False)

    if not mine_event and not blessing:
        await _w(bot, user.id, "⛏️ No mining event is active right now.")
        return

    parts: list[str] = []
    if mine_event:
        parts.append(f"Mine event: {mine_event.get('event_id','?')}")
    if blessing:
        parts.append("Admin's Blessing active (+2x ore qty)")
    await _w(bot, user.id, "⛏️ " + " | ".join(parts))


async def handle_setminingenergy(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_mine_admin(user.username):
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !setminingenergy <username> <amount>")
        return
    target = args[1].lstrip("@")
    amt    = args[2]
    if not amt.isdigit():
        await _w(bot, user.id, "Amount must be a positive integer.")
        return
    rec = db.get_user_by_username(target)
    if not rec:
        await _w(bot, user.id, f"@{target} not found.")
        return
    db.update_miner(target, energy=int(amt))
    await _w(bot, user.id, f"✅ @{target} energy set to {amt}.")


async def handle_addore(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_mine_admin(user.username):
        return
    if len(args) < 4:
        await _w(bot, user.id, "Usage: !addore <username> <ore_id> <amount>")
        return
    target = args[1].lstrip("@")
    ore_id = args[2].lower()
    amt    = args[3]
    if not amt.isdigit() or int(amt) < 1:
        await _w(bot, user.id, "Amount must be a positive integer.")
        return
    it = db.get_mining_item(ore_id)
    if not it:
        await _w(bot, user.id, f"Unknown ore: {ore_id}. Use !orelist.")
        return
    db.ensure_miner_row(target)
    db.add_ore(target, ore_id, int(amt))
    db.log_mine(target, "admin_addore", ore_id, int(amt), 0, f"by {user.username}")
    await _w(bot, user.id,
             f"✅ Added {it['emoji']} {it['name']} x{amt} to @{target}.")


async def handle_removeore(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_mine_admin(user.username):
        return
    if len(args) < 4:
        await _w(bot, user.id, "Usage: !removeore <username> <ore_id> <amount>")
        return
    target = args[1].lstrip("@")
    ore_id = args[2].lower()
    amt    = args[3]
    if not amt.isdigit() or int(amt) < 1:
        await _w(bot, user.id, "Amount must be a positive integer.")
        return
    it = db.get_mining_item(ore_id)
    if not it:
        await _w(bot, user.id, f"Unknown ore: {ore_id}.")
        return
    ok = db.remove_ore(target, ore_id, int(amt))
    if not ok:
        have = db.get_ore_qty(target, ore_id)
        await _w(bot, user.id, f"@{target} only has {have} {it['name']}.")
        return
    await _w(bot, user.id, f"✅ Removed {it['name']} x{amt} from @{target}.")


async def handle_settoollevel(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_mine_admin(user.username):
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !settoollevel <username> <1-10>")
        return
    target = args[1].lstrip("@")
    lvl    = args[2]
    if not lvl.isdigit() or not (1 <= int(lvl) <= 10):
        await _w(bot, user.id, "Tool level must be 1-10.")
        return
    db.ensure_miner_row(target)
    db.update_miner(target, tool_level=int(lvl))
    name = PICKAXE_NAMES.get(int(lvl), f"Lv{lvl}")
    await _w(bot, user.id, f"✅ @{target} pickaxe set to Lv {lvl} {name}.")


async def handle_setminelevel(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_mine_admin(user.username):
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !setminelevel <username> <level>")
        return
    target = args[1].lstrip("@")
    lvl    = args[2]
    if not lvl.isdigit() or int(lvl) < 1:
        await _w(bot, user.id, "Level must be >= 1.")
        return
    db.ensure_miner_row(target)
    db.update_miner(target, mining_level=int(lvl))
    await _w(bot, user.id, f"✅ @{target} mining level set to {lvl}.")


async def handle_addminexp(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_mine_admin(user.username):
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !addminexp <username> <amount>")
        return
    target = args[1].lstrip("@")
    amt    = args[2]
    if not amt.isdigit():
        await _w(bot, user.id, "Amount must be a positive integer.")
        return
    db.ensure_miner_row(target)
    miner = db.get_or_create_miner(target)
    new_mxp = miner["mining_xp"] + int(amt)
    lvl     = miner["mining_level"]
    while new_mxp >= _xp_for_level(lvl):
        new_mxp -= _xp_for_level(lvl)
        lvl += 1
    db.update_miner(target, mining_xp=new_mxp, mining_level=lvl)
    await _w(bot, user.id, f"✅ Added {_fmt(int(amt))} MXP to @{target}. Now Lv {lvl}.")


async def handle_setminexp(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_mine_admin(user.username):
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !setminexp <username> <amount>")
        return
    target = args[1].lstrip("@")
    amt    = args[2]
    if not amt.isdigit():
        await _w(bot, user.id, "Amount must be a positive integer.")
        return
    db.ensure_miner_row(target)
    db.update_miner(target, mining_xp=int(amt))
    await _w(bot, user.id, f"✅ @{target} mining XP set to {_fmt(int(amt))}.")


async def handle_resetmining(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_mine_admin(user.username):
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !resetmining <username>")
        return
    target = args[1].lstrip("@")
    conn   = db.get_connection()
    conn.execute(
        """UPDATE mining_players SET
           mining_level=1, mining_xp=0, tool_level=1,
           energy=100, max_energy=100,
           total_mines=0, total_ores=0, rare_finds=0, coins_earned=0,
           last_mine_at=NULL, streak_days=0, last_daily_bonus=NULL,
           luck_boost_until=NULL, xp_boost_until=NULL
           WHERE lower(username)=lower(?)""",
        (target,),
    )
    conn.execute(
        "DELETE FROM mining_inventory WHERE lower(username)=lower(?)", (target,)
    )
    conn.commit()
    conn.close()
    await _w(bot, user.id, f"✅ Mining data reset for @{target}.")


async def handle_miningroomrequired(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_mine_admin(user.username):
        return
    sub = args[1].lower() if len(args) > 1 else ""
    if sub == "on":
        db.set_mine_setting("mining_requires_room", "true")
        await _w(bot, user.id, "✅ Mining now requires room presence.")
    elif sub == "off":
        db.set_mine_setting("mining_requires_room", "false")
        await _w(bot, user.id, "⛔ Mining no longer requires room presence.")
    else:
        cur = db.get_mine_setting("mining_requires_room", "true")
        await _w(bot, user.id,
                 f"Room required: {cur}. Set: !miningroomrequired on | off")


# ---------------------------------------------------------------------------
# /orelist
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Shared ore-list / ore-prices pagination helpers  (A-project)
# ---------------------------------------------------------------------------

_ORE_PAGE_SIZE = 5

_ORELIST_MENU = (
    "<#66CCFF>⛏️ Ore List<#FFFFFF>\n"
    "!orelist [rarity] [page]\n"
    "common  uncommon  rare\n"
    "epic  legendary  mythic\n"
    "prismatic  exotic"
)

_OREPRICES_MENU = (
    "<#FFD700>💰 Ore Prices<#FFFFFF>\n"
    "!oreprices [rarity] [page]\n"
    "common  uncommon  rare\n"
    "epic  legendary  mythic\n"
    "prismatic  exotic"
)

# Capslocked colored rarity labels for /orelist and /oreprices headers.
# Prismatic uses the full rainbow caps label; handle_orelist auto-shrinks
# the ore-per-page count if the combined message exceeds 249 chars.
_SHORT_RAR_LABELS: dict[str, str] = {
    "common":     "<#AAAAAA>[COMMON]<#FFFFFF>",
    "uncommon":   "<#66BBAA>[UNCOMMON]<#FFFFFF>",
    "rare":       "<#3399FF>[RARE]<#FFFFFF>",
    "epic":       "<#B266FF>[EPIC]<#FFFFFF>",
    "legendary":  "<#FFD700>[LEGENDARY]<#FFFFFF>",
    "mythic":     "<#FF66CC>[MYTHIC]<#FFFFFF>",
    "ultra_rare": (
        "<#FF0000>[P<#FF9900>R<#FFFF00>I<#00FF00>S"
        "<#00CCFF>M<#3366FF>A<#9933FF>T<#FF66CC>I<#FF0000>C]<#FFFFFF>"
    ),
    "prismatic": (
        "<#FF0000>[P<#FF9900>R<#FFFF00>I<#00FF00>S"
        "<#00CCFF>M<#3366FF>A<#9933FF>T<#FF66CC>I<#FF0000>C]<#FFFFFF>"
    ),
    "exotic":     "<#FF0000>[EXOTIC]<#FFFFFF>",
}


def _ore_short_label(rarity: str) -> str:
    return _SHORT_RAR_LABELS.get(rarity.lower(), rarity.replace("_", " ").title())


_PLAIN_RAR_NAMES: dict[str, str] = {
    "common":     "Common",
    "uncommon":   "Uncommon",
    "rare":       "Rare",
    "epic":       "Epic",
    "legendary":  "Legendary",
    "mythic":     "Mythic",
    "ultra_rare": "Ultra Rare",
    "prismatic":  "Prismatic",
    "exotic":     "Exotic",
}


def _rar_plain(rarity: str) -> str:
    """Plain-text rarity name (no color tags) — safe for list/inventory display."""
    return _PLAIN_RAR_NAMES.get(rarity.lower(), rarity.replace("_", " ").title())


def _one_in_x(rarity: str, n_ores: int) -> int:
    """Return the 1-in-X whole number for an individual ore of the given rarity."""
    prob_pct = RARITIES.get(rarity, RARITIES["common"])[0]
    per_ore  = prob_pct / max(n_ores, 1)
    if per_ore <= 0:
        return 0
    return max(1, round(100 / per_ore))


def _compact_1in(n: int) -> str:
    """Format 1-in-N as compact string: 1:2.86M, 1:178K, 1:500"""
    if n >= 1_000_000:
        v = round(n / 1_000_000, 2)
        return f"1:{v:g}M"
    if n >= 10_000:
        return f"1:{round(n / 1_000):g}K"
    if n >= 1_000:
        v = round(n / 1_000, 1)
        return f"1:{v:g}K"
    return f"1:{n}"


def _safe_per_page(header: str, item_lines: list[str], limit: int = 220) -> int:
    """Return how many items from item_lines fit after header within limit chars."""
    count = 0
    msg   = header
    for line in item_lines[:8]:
        candidate = msg + "\n" + line
        if len(candidate) > limit:
            break
        msg = candidate
        count += 1
    return max(1, count)


def _by_rarity_map() -> dict[str, list]:
    items: list[dict] = db.get_all_mining_items(drop_enabled=False)
    by_rar: dict[str, list] = {}
    for it in items:
        by_rar.setdefault(it["rarity"], []).append(it)
    return by_rar


def _resolve_rarity_arg(arg: str) -> str:
    """Normalise player input to an internal rarity key."""
    a = arg.lower().replace("-", "_").replace(" ", "_")
    # Handle aliases
    _aliases = {
        "pris": "prismatic", "prism": "prismatic",
        "ultra": "ultra_rare", "ultrarare": "ultra_rare",
    }
    return _aliases.get(a, a)


async def handle_orelist(bot: BaseBot, user: User, args: list[str] | None = None) -> None:
    """
    !orelist [rarity] [page]
    Shows ore drop chances with compact format. Prismatic/exotic names colored.
    No prices or weights — use !oreprices for those.
    """
    args = args or []

    if len(args) < 2:
        by_rar = _by_rarity_map()
        lines  = ["⛏️ Ore List"]
        for r in RARITY_ORDER:
            cnt = len(by_rar.get(r, []))
            if cnt:
                lines.append(f"[{r.upper()}] — {cnt} ores")
        lines.append("Use !orelist common to view Common ores.")
        lines.append("!oreprices common for prices and weights.")
        await _w(bot, user.id, "\n".join(lines)[:249])
        return

    rar = _resolve_rarity_arg(args[1])
    if rar not in RARITIES:
        await _w(bot, user.id,
                 "Usage: !ores [rarity]\nExample: !ores rare\n"
                 "Rarities: common uncommon rare epic legendary mythic prismatic exotic")
        return

    page = 1
    if len(args) >= 3 and args[2].isdigit():
        page = max(1, int(args[2]))

    by_rar = _by_rarity_map()
    ores   = by_rar.get(rar, [])
    if not ores:
        await _w(bot, user.id, f"No ores found for rarity: {rar}.")
        return

    one_in    = _one_in_x(rar, len(ores))
    rar_label = _ore_short_label(rar)
    chance    = _compact_1in(one_in)

    def _line(it: dict) -> str:
        if rar == "prismatic":
            name = f"<#FF66CC>{it['name']}<#FFFFFF>"
        elif rar == "exotic":
            name = f"<#FF0000>{it['name']}<#FFFFFF>"
        else:
            name = it["name"]
        return f"{it['emoji']} {name} — {chance}"

    all_lines   = [_line(it) for it in ores]
    test_hdr    = f"⛏️ {rar_label} Ores p1/10"
    per_page    = _safe_per_page(test_hdr, all_lines)
    total_pages = max(1, (len(ores) + per_page - 1) // per_page)

    if page > total_pages:
        await _w(bot, user.id,
                 f"⚠️ Page not found.\nUse: !orelist {rar} 1")
        return

    header = f"⛏️ {rar_label} Ores p{page}/{total_pages}"
    start  = (page - 1) * per_page
    chunk  = list(all_lines[start:start + per_page])
    msg    = header + "\n" + "\n".join(chunk)

    # Safety: trim from end if somehow still over limit
    while len(msg) > 220 and len(chunk) > 1:
        chunk = chunk[:-1]
        msg = header + "\n" + "\n".join(chunk)

    await _w(bot, user.id, msg[:249])


# ---------------------------------------------------------------------------
# /oreprices [rarity] [page]  — price + weight, no chance  (A3)
# ---------------------------------------------------------------------------

async def handle_oreprices(bot: BaseBot, user: User, args: list[str] | None = None) -> None:
    """
    /oreprices [rarity] [page]
    Shows ore base sell value and weight range, 5 ores per page.
    """
    args = args or []

    if len(args) < 2:
        await _w(bot, user.id, _OREPRICES_MENU)
        return

    rar = _resolve_rarity_arg(args[1])
    if rar not in RARITIES:
        await _w(bot, user.id,
                 f"Unknown rarity: {args[1]}\n"
                 "Use: common uncommon rare epic legendary mythic prismatic exotic")
        return

    page = 1
    if len(args) >= 3 and args[2].isdigit():
        page = max(1, int(args[2]))

    by_rar = _by_rarity_map()
    ores   = by_rar.get(rar, [])
    if not ores:
        await _w(bot, user.id, f"No ores found for rarity: {rar}.")
        return

    total_pages = max(1, (len(ores) + _ORE_PAGE_SIZE - 1) // _ORE_PAGE_SIZE)
    if page > total_pages:
        await _w(bot, user.id,
                 f"Only {total_pages} page(s) for {rar}. !oreprices {rar} {total_pages}")
        return

    rar_disp  = get_rarity_display_name(rar)
    wlo, whi  = get_default_weight_range(rar)
    header    = f"{rar_disp} Prices — Page {page}/{total_pages}"

    start  = (page - 1) * _ORE_PAGE_SIZE
    subset = ores[start:start + _ORE_PAGE_SIZE]

    lines = [
        f"{it['emoji']} {it['name']} — {it.get('sell_value', 0):,}c | {wlo}–{whi}kg"
        for it in subset
    ]
    msg = header + "\n" + "\n".join(lines)
    await _w(bot, user.id, msg[:249])


# ---------------------------------------------------------------------------
# /oreinfo <ore_name>  — full details for one ore  (A4)
# ---------------------------------------------------------------------------

async def handle_oreinfo(bot: BaseBot, user: User, args: list[str] | None = None) -> None:
    """
    /oreinfo <ore_name>  — full details: rarity, chance, value, weight, announce.
    Case-insensitive. Accepts partial match. Spaces in ore name are OK.
    Examples: /oreinfo gold ore  |  /oreinfo aurora  |  /oreinfo 22 (by list index)
    """
    args = args or []
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !oreinfo <ore name>   e.g. /oreinfo gold ore")
        return

    query  = " ".join(args[1:]).lower().strip()
    items  = db.get_all_mining_items(drop_enabled=False)

    # Exact item_id match first, then name match, then partial
    target: dict | None = None
    for it in items:
        if it["item_id"].lower() == query:
            target = it
            break
    if not target:
        for it in items:
            if it["name"].lower() == query:
                target = it
                break
    if not target:
        matches = [it for it in items if query in it["name"].lower() or query in it["item_id"].lower()]
        if len(matches) == 1:
            target = matches[0]
        elif len(matches) > 1:
            names = ", ".join(it["name"] for it in matches[:8])
            await _w(bot, user.id, f"Multiple matches: {names}\nBe more specific.")
            return
    if not target:
        await _w(bot, user.id, f"Ore '{query}' not found. Use !orelist to browse.")
        return

    rar        = target["rarity"]
    by_rar     = _by_rarity_map()
    n_ores     = len(by_rar.get(rar, []))
    one_in     = _one_in_x(rar, n_ores)
    wlo, whi   = get_default_weight_range(rar)
    val        = target.get("sell_value", 0)
    announce   = "ON" if rar in ANNOUNCE_RARITIES else "OFF"

    # Use full rainbow name only for prismatic in /oreinfo (single ore, safe)
    if rar in ("prismatic", "ultra_rare"):
        ore_display = rainbow_text(f"{target['emoji']} {target['name']}")
    else:
        color = {
            "uncommon":  "#66BBAA", "rare": "#3399FF", "epic": "#B266FF",
            "legendary": "#FFD700", "mythic": "#FF66CC", "exotic": "#FF0000",
        }.get(rar, "")
        ore_display = (f"<{color}>{target['emoji']} {target['name']}<#FFFFFF>"
                       if color else f"{target['emoji']} {target['name']}")

    rar_name   = get_rarity_display_name(rar)
    msg = (
        f"{ore_display}\n"
        f"Rarity: {rar_name}\n"
        f"Chance: 1 in {one_in:,}\n"
        f"Value: {val:,}c\n"
        f"Weight: {wlo}–{whi}kg\n"
        f"Announce: {announce}"
    )
    await _w(bot, user.id, msg[:249])


def _split_to_chunks(text: str, limit: int) -> list[str]:
    """Split *text* into chunks ≤ limit chars, splitting on newlines where possible."""
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text.strip():
        chunks.append(text)
    return chunks


# ---------------------------------------------------------------------------
# /minehelp
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Ore mastery & contracts
# ---------------------------------------------------------------------------

# (threshold_ores, reward_coins, title)
_MASTERY_MILESTONES = [
    (50,   50,   "Rookie Miner"),
    (200,  150,  "Apprentice"),
    (500,  500,  "Journeyman"),
    (1000, 1000, "Expert Miner"),
    (2500, 2500, "Master Miner"),
    (5000, 5000, "Legendary Miner"),
]

# (contract_id, ore_id, qty_needed, reward_coins, display_name, emoji)
# Contract rewards must always exceed the direct sell value of the ores,
# otherwise no rational player would take the contract.
# With updated sell values: coal 18c×20=360c, copper 28c×15=420c.
# Coal reward raised 200→420c; Copper raised 350→500c.
# Iron (40c×10=400c, reward 450c) and all higher contracts were already
# above sell value and are left unchanged.
_CONTRACT_POOL = [
    (1, "coal",        20,  420, "Coal",        "⚫"),  # was 200c — now 60c above sell value
    (2, "copper_ore",  15,  500, "Copper Ore",  "🟠"),  # was 350c — now 80c above sell value
    (3, "iron_ore",    10,  450, "Iron Ore",    "⛓️"),
    (4, "tin_ore",      8,  550, "Tin Ore",     "◽"),
    (5, "quartz",       5,  750, "Quartz",      "🔹"),
    (6, "silver_ore",   4,  900, "Silver Ore",  "⚪"),
    (7, "gold_ore",     3, 1500, "Gold Ore",    "🟡"),
    (8, "amethyst",     2, 2000, "Amethyst",    "💜"),
]


_ORE_RARITY_ORDER = [
    "common", "uncommon", "rare", "epic", "legendary", "mythic", "ultra_rare",
]
_ORE_RAR_ABBR = {
    "common": "Com", "uncommon": "Unc", "rare": "Rar", "epic": "Epi",
    "legendary": "Leg", "mythic": "Myt", "ultra_rare": "UR",
}
_ORE_BOOK_PER = 5


async def handle_orebook(bot: BaseBot, user: User, args: list) -> None:
    """/orebook [rarity|missing|all|rarelog] [page] — ore collection book."""
    uid = user.id
    try:
        sub = args[1].lower().replace("-", "_") if len(args) > 1 else ""
        # Accept digit-only arg as page number for !orebook 2
        if sub.isdigit():
            page = max(1, int(sub))
            sub  = ""
        else:
            try:
                page = max(1, int(args[2])) if len(args) > 2 and args[2].isdigit() else 1
            except (ValueError, IndexError):
                page = 1

        totals  = db.get_mining_totals_by_rarity()
        grand_t = sum(totals.values())
        found   = db.get_player_collection(uid, "mining")
        found_d = {r["item_key"]: r for r in found}
        by_rar: dict = {}
        for r in found:
            by_rar.setdefault(r["rarity"], []).append(r)

        valid = set(_ORE_RARITY_ORDER) | {"missing", "all", "rarelog"}

        # ── Bad filter hint ────────────────────────────────────────────────────
        if sub and sub not in valid:
            await _w(bot, uid,
                     f"⚠️ Use: !orebook rare, uncommon, epic, legendary,\n"
                     f"  mythic, ultra_rare, prismatic, exotic, missing, all")
            return

        # ── Overview ──────────────────────────────────────────────────────────
        if not sub:
            if not found:
                await _w(bot, uid,
                         "⛏️ Ore Book\nNo mining discoveries yet.\nTry !mine.")
                return
            parts = [
                f"{_ORE_RAR_ABBR.get(r, r[:3])}:{len(by_rar.get(r,[]))}/{totals.get(r,0)}"
                for r in _ORE_RARITY_ORDER if totals.get(r, 0) > 0
            ]
            out = f"⛏️ Ore Book — {len(found)}/{grand_t}\n" + "  ".join(parts[:4])
            if parts[4:]:
                out += "\n" + "  ".join(parts[4:])
            out += "\n!orebook <rarity>  !orebook missing"
            await _w(bot, uid, out[:249])
            return

        # ── Rarelog ───────────────────────────────────────────────────────────
        if sub == "rarelog":
            _rset = {"legendary", "mythic", "ultra_rare", "prismatic", "exotic"}
            items = [r for r in found if r["rarity"] in _rset]
            if not items:
                await _w(bot, uid, "⛏️ Rare Log\nNo rare+ ores discovered yet.")
                return
            lines = [f"⛏️ Rare Log ({len(items)} types)"]
            for r in items[:6]:
                lines.append(f"✅ {r['item_name']} x{r['count']}")
            if len(items) > 6:
                lines.append(f"+{len(items)-6} more")
            await _w(bot, uid, "\n".join(lines)[:249])
            return

        # ── Build paginated list ───────────────────────────────────────────────
        conn = db.get_connection()
        try:
            if sub == "missing":
                all_items = [dict(r) for r in conn.execute(
                    "SELECT item_id, name FROM mining_items ORDER BY rarity, name"
                ).fetchall()]
                lst = [{"label": f"❔ {r['name']}"}
                       for r in all_items if r["item_id"] not in found_d]
                if not lst:
                    await _w(bot, uid, "⛏️ Missing Ores\nYou discovered all ores!")
                    return
                hdr = f"⛏️ Missing ({len(lst)}/{grand_t})"
            elif sub == "all":
                _key = lambda x: (_ORE_RARITY_ORDER.index(x["rarity"])
                                  if x["rarity"] in _ORE_RARITY_ORDER else 99,
                                  x["item_name"])
                lst = [{"label": f"✅ {r['item_name']} x{r['count']}"}
                       for r in sorted(found, key=_key)]
                hdr = f"⛏️ All Discovered ({len(lst)}/{grand_t})"
            else:
                all_rar = [dict(r) for r in conn.execute(
                    "SELECT item_id, name FROM mining_items WHERE rarity=? ORDER BY name",
                    (sub,)
                ).fetchall()]
                lst = []
                for it in all_rar:
                    if it["item_id"] in found_d:
                        lst.append({"label": f"✅ {it['name']} x{found_d[it['item_id']]['count']}"})
                    else:
                        lst.append({"label": f"❔ {it['name']}"})
                disc_r = len(by_rar.get(sub, []))
                hdr    = f"⛏️ {sub.replace('_', ' ').title()} ({disc_r}/{totals.get(sub, 0)})"
        finally:
            conn.close()

        total_p = max(1, (len(lst) + _ORE_BOOK_PER - 1) // _ORE_BOOK_PER)
        page    = min(page, total_p)
        chunk   = lst[(page - 1) * _ORE_BOOK_PER: page * _ORE_BOOK_PER]
        lines   = [hdr] + [it["label"] for it in chunk]
        if total_p > 1:
            nxt = f"  !orebook {sub} {page + 1}" if page < total_p else ""
            lines.append(f"Pg {page}/{total_p}{nxt}")
        await _w(bot, uid, "\n".join(lines)[:249])

    except Exception:
        import traceback; traceback.print_exc()
        await _w(bot, uid,
                 "⛏️ Ore Book\nCould not load book. Try again.\n"
                 "!orebook rare  !orebook missing  !orebook all")


async def handle_oremastery(bot: BaseBot, user: User) -> None:
    """/oremastery — mastery milestones and progress."""
    miner = db.get_or_create_miner(user.username)
    total = miner.get("total_ores", 0)
    claimed = db.get_ore_mastery_claimed(user.username)
    lines = [f"📘 Ore Mastery — {_fmt(total)} ores mined"]
    for i, (threshold, reward, title) in enumerate(_MASTERY_MILESTONES, 1):
        if threshold in claimed:
            state = "✅"
        elif total >= threshold:
            state = "🎁"
        else:
            state = f"{_fmt(total)}/{threshold}"
        lines.append(f"{i}. {state} {title} ({threshold}) → {reward}c")
    lines.append("!claimoremastery [1-6] to claim 🎁 rewards.")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_claimoremastery(bot: BaseBot, user: User, args: list[str]) -> None:
    """/claimoremastery <1-6> — claim a mastery milestone reward."""
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !claimoremastery <1-6>  (see !oremastery)")
        return
    idx = int(args[1]) - 1
    if idx < 0 or idx >= len(_MASTERY_MILESTONES):
        await _w(bot, user.id, f"Invalid milestone. Use 1-{len(_MASTERY_MILESTONES)}.")
        return
    threshold, reward, title = _MASTERY_MILESTONES[idx]
    miner = db.get_or_create_miner(user.username)
    total = miner.get("total_ores", 0)
    if total < threshold:
        await _w(bot, user.id, f"❌ Need {_fmt(threshold)} ores mined. You have {_fmt(total)}.")
        return
    claimed = db.get_ore_mastery_claimed(user.username)
    if threshold in claimed:
        await _w(bot, user.id, f"✅ '{title}' already claimed.")
        return
    db.claim_ore_mastery(user.username, threshold)
    user_row = db.get_user_by_username(user.username)
    if user_row:
        db.add_balance(user_row["user_id"], reward)
    db.update_miner(user.username, coins_earned=miner.get("coins_earned", 0) + reward)
    await _w(bot, user.id, f"🎉 Mastery '{title}' claimed! +{reward:,}c.")


async def handle_orestats(bot: BaseBot, user: User, args: list[str]) -> None:
    """/orestats [user] — detailed mining stats."""
    target = args[1].lstrip("@").strip() if len(args) > 1 else user.username
    miner = db.get_or_create_miner(target)
    inv = db.get_inventory(target)
    total_val = sum(r.get("quantity", 0) * r.get("sell_value", 0) for r in inv)
    lines = [
        f"⛏️ {target} Ore Stats",
        f"Total mined: {_fmt(miner.get('total_ores', 0))}",
        f"Rare finds: {_fmt(miner.get('rare_finds', 0))}",
        f"Coins earned: {_fmt(miner.get('coins_earned', 0))}c",
        f"Inv value: {_fmt(total_val)}c | Types: {len(inv)}",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_contracts(bot: BaseBot, user: User) -> None:
    """/contracts (/miningjobs) — browse available mining contracts."""
    lines = ["📋 Mining Contracts — /job <#> to accept"]
    for cid, ore_id, qty, reward, name, emoji in _CONTRACT_POOL:
        lines.append(f"{cid}. {emoji} {name} x{qty} → {reward:,}c")
    lines.append("Active contract: /job  |  Deliver: /deliver  |  Reroll: /rerolljob")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_job(bot: BaseBot, user: User, args: list[str]) -> None:
    """/job [contract_id] — assign or view active contract."""
    if len(args) > 1 and args[1].isdigit():
        cid = int(args[1])
        entry = next((c for c in _CONTRACT_POOL if c[0] == cid), None)
        if not entry:
            await _w(bot, user.id, f"❌ No contract #{cid}. See !contracts for the list.")
            return
        existing = db.get_miner_contract(user.username)
        if existing and existing.get("qty_delivered", 0) < existing.get("qty_needed", 1):
            await _w(bot, user.id, "You already have an active contract. /deliver or /rerolljob first.")
            return
        _, ore_id, qty, reward, name, emoji = entry
        import datetime as _dt
        expires = (_dt.datetime.utcnow() + _dt.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        db.set_miner_contract(user.username, cid, ore_id, qty, reward, expires)
        await _w(bot, user.id, f"📋 Contract: {emoji} {name} x{qty} → {reward:,}c (24h) | /deliver to submit.")
        return
    current = db.get_miner_contract(user.username)
    if not current:
        await _w(bot, user.id, "No active contract. /contracts to browse, /job <#> to accept.")
        return
    name = current["ore_id"].replace("_", " ").title()
    needed = current["qty_needed"]
    delivered = current.get("qty_delivered", 0)
    reward = current["reward_coins"]
    pct = int(delivered / needed * 100) if needed > 0 else 0
    await _w(bot, user.id,
             f"📋 Job: {name} x{needed}\n"
             f"Progress: {delivered}/{needed} ({pct}%)\n"
             f"Reward: {reward:,}c\n"
             f"/deliver to submit | /claimjob when done | /rerolljob to cancel")


async def handle_deliver(bot: BaseBot, user: User, args: list[str]) -> None:
    """/deliver — submit ores for your active contract."""
    current = db.get_miner_contract(user.username)
    if not current:
        await _w(bot, user.id, "No active contract. /contracts → /job <#> to start one.")
        return
    ore_id = current["ore_id"]
    needed = current["qty_needed"]
    delivered = current.get("qty_delivered", 0)
    remaining = max(0, needed - delivered)
    if remaining == 0:
        await _w(bot, user.id, "✅ Contract complete! Use !claimjob to collect your reward.")
        return
    have = db.get_ore_qty(user.username, ore_id)
    name = ore_id.replace("_", " ").title()
    if have == 0:
        await _w(bot, user.id, f"❌ You have no {name}. !mine to collect some!")
        return
    give = min(have, remaining)
    if not db.remove_ore(user.username, ore_id, give):
        await _w(bot, user.id, "❌ Error removing ores. Try again.")
        return
    db.update_contract_delivery(user.username, give)
    new_delivered = delivered + give
    if new_delivered >= needed:
        await _w(bot, user.id, f"✅ Delivered {give} {name}! Contract complete. /claimjob for reward.")
    else:
        await _w(bot, user.id, f"✅ Delivered {give} {name}. Progress: {new_delivered}/{needed}.")


async def handle_claimjob(bot: BaseBot, user: User) -> None:
    """/claimjob — collect reward for completed contract."""
    current = db.get_miner_contract(user.username)
    if not current:
        await _w(bot, user.id, "No active contract. /contracts to start one.")
        return
    needed = current["qty_needed"]
    delivered = current.get("qty_delivered", 0)
    if delivered < needed:
        await _w(bot, user.id, f"Contract not done: {delivered}/{needed}. /deliver first.")
        return
    reward = current["reward_coins"]
    db.clear_miner_contract(user.username)
    user_row = db.get_user_by_username(user.username)
    if user_row:
        db.add_balance(user_row["user_id"], reward)
    miner = db.get_or_create_miner(user.username)
    db.update_miner(user.username, coins_earned=miner.get("coins_earned", 0) + reward)
    await _w(bot, user.id, f"🎉 Contract complete! +{reward:,}c earned. /contracts for more jobs.")


async def handle_rerolljob(bot: BaseBot, user: User) -> None:
    """/rerolljob — cancel current contract."""
    current = db.get_miner_contract(user.username)
    if not current:
        await _w(bot, user.id, "No active contract. /contracts to start one.")
        return
    db.clear_miner_contract(user.username)
    await _w(bot, user.id, "🔄 Contract cancelled. /contracts to pick a new one.")


# ---------------------------------------------------------------------------
# A2: Ore chance commands
# /orechances /orechance /setorechance /setraritychance /reloadorechances
# ---------------------------------------------------------------------------

async def handle_minechances(bot: BaseBot, user: User) -> None:
    """!minechances — show base rarity drop % for mining (colored, 2 messages)."""
    _CL = {
        "common":     "<#AAAAAA>[COMMON]<#FFFFFF>",
        "uncommon":   "<#66BBAA>[UNCOMMON]<#FFFFFF>",
        "rare":       "<#3399FF>[RARE]<#FFFFFF>",
        "epic":       "<#B266FF>[EPIC]<#FFFFFF>",
        "legendary":  "<#FFD700>[LEGENDARY]<#FFFFFF>",
        "mythic":     "<#FF66CC>[MYTHIC]<#FFFFFF>",
        "ultra_rare": "<#FF66CC>[ULTRA RARE]<#FFFFFF>",
        "prismatic":  _ore_short_label("prismatic"),
        "exotic":     "<#FF0000>[EXOTIC]<#FFFFFF>",
    }
    def _pct(r: str) -> str:
        p = RARITIES[r][0]
        return f"{p}%" if p >= 0.01 else f"{p:.4f}%"
    def _ln(r: str) -> str:
        return f"{_CL[r]}: {_pct(r)}"
    await _w(bot, user.id, "\n".join([
        "⛏️ Mining Chances",
        _ln("common"), _ln("uncommon"), _ln("rare"),
        _ln("epic"),   _ln("legendary"), _ln("mythic"),
    ]))
    await _w(bot, user.id, "\n".join([
        "⛏️ Ultra Rare Ores",
        _ln("prismatic"), _ln("exotic"),
    ]))


async def handle_orechances(bot: BaseBot, user: User) -> None:
    """/orechances — show 1-in-X drop chance for every ore."""
    items = db.get_all_mining_items(drop_enabled=True)
    by_rarity: dict[str, list] = {}
    for it in items:
        by_rarity.setdefault(it["rarity"], []).append(it)
    probs = {r: v[0] for r, v in RARITIES.items()}
    lines = ["<#66CCFF>⛏️ Ore Drop Chances<#FFFFFF>"]
    for rar in sorted(by_rarity.keys(), key=rarity_sort_key):
        ores   = by_rarity[rar]
        n      = max(len(ores), 1)
        rp     = probs.get(rar, 0)
        per    = rp / n
        one_in = max(1, int(round(100 / per))) if per > 0 else 0
        rar_lbl = format_mining_rarity(rar)
        names  = ", ".join(f"{it['emoji']}{it['name']}" for it in ores)
        lines.append(f"{rar_lbl} 1-in-{one_in}: {names}"[:120])
    msg = "\n".join(lines)
    if len(msg) <= 249:
        await _w(bot, user.id, msg)
    else:
        await _w(bot, user.id, msg[:249])
        rest = msg[249:]
        if rest.strip():
            await _w(bot, user.id, rest.strip()[:249])


async def handle_orechance(bot: BaseBot, user: User, args: list[str]) -> None:
    """/orechance <ore_id> — show 1-in-X drop chance for a specific ore."""
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !orechance <ore_id>")
        return
    ore_id = args[1].lower()
    items  = db.get_all_mining_items(drop_enabled=False)
    target = next((it for it in items if it["item_id"].lower() == ore_id), None)
    if not target:
        await _w(bot, user.id, f"Ore '{ore_id}' not found. Use !orelist to browse.")
        return
    rar = target["rarity"]
    by_rarity: dict[str, list] = {}
    for it in items:
        by_rarity.setdefault(it["rarity"], []).append(it)
    probs  = {r: v[0] for r, v in RARITIES.items()}
    n      = max(len(by_rarity.get(rar, [])), 1)
    rp     = probs.get(rar, 0)
    per    = rp / n
    one_in = max(1, int(round(100 / per))) if per > 0 else 0
    rar_lbl = format_mining_rarity(rar)
    await _w(bot, user.id,
             f"⛏️ {target['emoji']} {target['name']}\n"
             f"Rarity: {rar_lbl}\n"
             f"Chance: 1-in-{one_in} per mine\n"
             f"Value: {target.get('sell_value', 0)}c"[:249])


async def handle_setorechance(
    bot: BaseBot, user: User, args: list[str]
) -> None:
    """/setorechance <ore_id> <note_%> — store a display-chance note (manager+).
    Stored in room_settings; shown in /orechance. Does not affect actual drop rolls
    (those use the RARITIES table). Use !setraritychance to adjust rarity weights.
    """
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !setorechance <ore_id> <note_pct>")
        return
    ore_id = args[1].lower()
    try:
        pct = float(args[2])
        if pct < 0 or pct > 100:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "Chance must be a number 0-100.")
        return
    items  = db.get_all_mining_items(drop_enabled=False)
    target = next((it for it in items if it["item_id"].lower() == ore_id), None)
    if not target:
        await _w(bot, user.id, f"Ore '{ore_id}' not found. Use !orelist.")
        return
    db.set_room_setting(f"mine_ore_displaychance_{ore_id}", str(pct))
    await _w(bot, user.id,
             f"✅ {target['name']} display chance note set to {pct}%. "
             "Shown in /orechance (display only).")


async def handle_setraritychance(
    bot: BaseBot, user: User, args: list[str]
) -> None:
    """/setraritychance <rarity> <chance_%> — store a rarity base-weight note (manager+).
    Stored in room_settings for display in /orechances. Actual drop weights use
    the RARITIES dict; this is a staff reference note.
    """
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id,
                 "Usage: !setraritychance <rarity> <chance_pct>\n"
                 "Rarities: common uncommon rare epic legendary mythic "
                 "ultra_rare prismatic exotic")
        return
    rar = args[1].lower().replace("-", "_")
    if rar not in RARITIES:
        await _w(bot, user.id,
                 f"Unknown rarity: {rar}. Valid: " +
                 ", ".join(RARITIES.keys()))
        return
    try:
        pct = float(args[2])
        if pct < 0 or pct > 100:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "Chance must be a number 0-100.")
        return
    db.set_room_setting(f"mine_rarity_displaychance_{rar}", str(pct))
    rar_lbl = format_mining_rarity(rar)
    await _w(bot, user.id,
             f"✅ {rar_lbl} display chance note set to {pct}%. "
             "Shown in /orechances (display only).")


async def handle_reloadorechances(bot: BaseBot, user: User) -> None:
    """/reloadorechances — reload ore chance weights from DB (manager+)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    # Force-reload by re-reading all items (no in-memory cache to bust)
    items = db.get_all_mining_items(drop_enabled=False)
    await _w(bot, user.id,
             f"✅ Ore chances reloaded. {len(items)} ore(s) in DB. "
             "Use !orechances to verify.")


MINE_HELP_PAGES = [
    (
        "⛏️ Mining\n"
        "!mine — mine once (!m !dig)\n"
        "!automine — auto mine\n"
        "!automine off — stop auto mine\n"
        "!mineinv — ores inventory\n"
        "!sellores — sell all ores\n"
        "!tool — view pickaxe\n"
        "!upgradetool — upgrade pickaxe"
    ),
    (
        "⛏️ Mining 2\n"
        "!minelb — leaderboards\n"
        "!mineshop — boosts\n"
        "!minedaily — daily bonus\n"
        "!mineprofile — your stats\n"
        "!minechances — drop rates\n"
        "!orelist [rarity] — ore list\n"
        "!oreinfo [ore] — ore details"
    ),
    (
        "📘 Ore Info\n"
        "!orelist [rarity] — ore chances\n"
        "!oreprices [rarity] — ore prices\n"
        "!oreinfo [ore] — full ore details\n"
        "Add [page] for page 2, 3...\n"
        "Example: !orelist rare 2"
    ),
    (
        "📘 Goals\n"
        "!orebook — ore collection\n"
        "!oremastery — mastery rewards\n"
        "!orestats — detailed stats\n"
        "!contracts — ore jobs\n"
        "!job [#] — accept a contract"
    ),
    (
        "⚖️ Ore Weights\n"
        "!topweights — all-time heaviest finds\n"
        "!oreweightlb [ore] — ore weight LB\n"
        "!myheaviest — your heaviest finds\n"
        "!oreweights — ores with records\n"
        "!mineannounce — announce settings"
    ),
    (
        "⛏️ Mining Staff\n"
        "!mining on|off\n"
        "!startminingevent [id]\n"
        "!setminecooldown [secs]\n"
        "!setmineannounce [rarity|off]\n"
        "!minepanel — mining dashboard\n"
        "!simannounce [rarity]"
    ),
]


async def handle_minehelp(bot: BaseBot, user: User, args: list[str]) -> None:
    # MINE_HELP_PAGES layout (6 pages):
    # 1-Mining  2-Mining2  3-OreInfo  4-Goals  5-Weights  6-Staff(admin)
    _STAFF_PAGE_IDX = len(MINE_HELP_PAGES) - 1   # last page is always staff
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
    if page == 0:
        for i in range(_STAFF_PAGE_IDX):          # send all non-staff pages
            await _w(bot, user.id, MINE_HELP_PAGES[i])
        if _can_mine_admin(user.username):
            await _w(bot, user.id, MINE_HELP_PAGES[_STAFF_PAGE_IDX])
    elif 1 <= page <= len(MINE_HELP_PAGES):
        if page == len(MINE_HELP_PAGES) and not _can_mine_admin(user.username):
            return
        await _w(bot, user.id, MINE_HELP_PAGES[page - 1])
    else:
        await _w(bot, user.id, f"Pages 1-{len(MINE_HELP_PAGES)}. Use !minehelp <page>.")


# ---------------------------------------------------------------------------
# /simannounce — simulate a rare-ore room announcement  (staff/manager only)
# ---------------------------------------------------------------------------


async def handle_simannounce(bot: BaseBot, user: User, args: list[str]) -> None:
    """
    /simannounce <rarity> [username]   — random ore from rarity, optional display name
    /simannounce random [username]     — random ore from any rarity
    /simannounce ore <ore_id_or_name>  — specific ore (display as admin)

    Sends the public announcement chat. No inventory/MXP/coin changes.
    Logged in console as [SIM].
    """
    if not _can_mine_admin(user.username):
        await _w(bot, user.id, "Admins and managers only.")
        return

    if len(args) < 2:
        await _w(bot, user.id,
                 "!simannounce [rarity] [user] | random [user] | ore [name]")
        return

    sub   = args[1].lower()
    items = db.get_all_mining_items(drop_enabled=False)
    if not items:
        await _w(bot, user.id, "No mining items found.")
        return

    target_item: dict | None = None
    display_name = db.get_display_name(user.id, user.username)

    if sub == "ore":
        if len(args) < 3:
            await _w(bot, user.id, "Usage: !simannounce ore <ore_name_or_id>")
            return
        query = " ".join(args[2:]).lower().strip()
        for it in items:
            if it["item_id"].lower() == query or it["name"].lower() == query:
                target_item = it
                break
        if not target_item:
            matches = [it for it in items if query in it["name"].lower()]
            if len(matches) == 1:
                target_item = matches[0]
        if not target_item:
            await _w(bot, user.id, f"Ore '{query[:40]}' not found. Try !orelist.")
            return

    elif sub == "random":
        target_item = random.choice(items)
        if len(args) >= 3:
            display_name = args[2]
    else:
        rar = _resolve_rarity_arg(sub)
        if rar not in RARITIES:
            await _w(bot, user.id,
                     f"Unknown rarity '{sub}'. Use common/rare/legendary/etc.")
            return
        rar_items = [it for it in items if it["rarity"] == rar]
        if not rar_items:
            await _w(bot, user.id, f"No ores for rarity '{rar}'.")
            return
        target_item = random.choice(rar_items)
        if len(args) >= 3:
            display_name = args[2]

    if not target_item:
        await _w(bot, user.id, "Could not select an ore for simulation.")
        return

    # Generate weight and value for the simulated ore
    _w_on  = weights_enabled()
    ore_wt = generate_weight(target_item["rarity"]) if _w_on else None
    final_v = (compute_final_value(target_item.get("sell_value", 0), ore_wt)
               if ore_wt is not None else target_item.get("sell_value", 0))

    rar       = target_item["rarity"]
    rar_label = format_mining_rarity(rar)
    ore_disp  = format_ore_name(
        f"{target_item['emoji']} {target_item['name']}", rar
    )
    val_str   = f" — {ore_wt}kg, {_fmt(final_v)}c!" if ore_wt is not None else "!"

    ann1 = "<#FFD700>📣 Big Find<#FFFFFF>"
    ann2 = f"💎 {display_name} mined {rar_label} {ore_disp}{val_str}"
    full = f"{ann1}\n{ann2}"

    await _w(bot, user.id,
             f"[SIM] Sending: {target_item['name']} ({rar})")
    try:
        await bot.highrise.chat(full[:249])
    except Exception as exc:
        await _w(bot, user.id, f"[SIM] Failed: {exc}")
        return
    print(f"[SIM] @{user.username} simulated announcement: "
          f"{target_item['item_id']} ({rar})")


# ---------------------------------------------------------------------------
# /forcedrop /forcedropore /forcedropstatus /clearforcedrop  (owner-only)
# ---------------------------------------------------------------------------


async def handle_forcedrop(bot: BaseBot, user: User, args: list[str]) -> None:
    """
    /forcedrop <username> <rarity>
    Sets a forced rarity for that player's next /mine (owner-only, silent).
    """
    from modules.permissions import is_owner
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner-only command.")
        return
    if len(args) < 3:
        await _w(bot, user.id,
                 "Usage: !forcedrop <username> <rarity>  e.g. /forcedrop marion legendary")
        return

    target = args[1].lstrip("@").lower()
    rar    = _resolve_rarity_arg(args[2])
    if rar not in RARITIES:
        valid = ", ".join(RARITIES.keys())
        await _w(bot, user.id,
                 f"Unknown rarity. Valid: {valid}")
        return

    drop_id = db.set_forced_drop(target, "rarity", rar, user.username)
    await _w(bot, user.id,
             f"⚙️ Forced drop set: @{target} → [{rar.upper()}] on next mine "
             f"(id={drop_id}, expires 24h)")


async def handle_forcedropore(bot: BaseBot, user: User, args: list[str]) -> None:
    """
    /forcedropore <username> <ore_id>
    Forces a specific ore for that player's next /mine (owner-only, silent).
    """
    from modules.permissions import is_owner
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner-only command.")
        return
    if len(args) < 3:
        await _w(bot, user.id,
                 "Usage: !forcedropore <username> <ore_id>  e.g. /forcedropore marion gold_ore")
        return

    target    = args[1].lstrip("@").lower()
    ore_query = " ".join(args[2:]).strip().lower()
    # Try exact item_id match first, then name search
    ore_row  = db.get_mining_item(ore_query.replace(" ", "_"))
    if not ore_row:
        ore_row = db.get_mining_item(ore_query)
    if not ore_row:
        items   = db.get_all_mining_items(drop_enabled=False)
        matches = [it for it in items if ore_query in it["name"].lower()]
        if len(matches) == 1:
            ore_row = matches[0]
        elif len(matches) > 1:
            names = ", ".join(it["name"] for it in matches[:5])
            await _w(bot, user.id, f"Multiple matches: {names}")
            return
    if not ore_row:
        await _w(bot, user.id,
                 f"Ore '{ore_query[:40]}' not found. "
                 f"Try !orelist or use the exact ore name.")
        return
    ore_id = ore_row["item_id"]

    drop_id = db.set_forced_drop(target, "ore", ore_id, user.username)
    await _w(bot, user.id,
             f"⚙️ Forced drop set: @{target} → "
             f"{ore_row['emoji']} {ore_row['name']} "
             f"(id={drop_id}, expires 24h)")


async def handle_forcedropstatus(bot: BaseBot, user: User) -> None:
    """/forcedropstatus — list all pending forced drops (owner-only)."""
    from modules.permissions import is_owner
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner-only command.")
        return

    drops = db.get_all_active_forced_drops()
    if not drops:
        await _w(bot, user.id, "No pending forced drops.")
        return

    from datetime import datetime as _dt_s, timezone as _tz_s
    lines = [f"⚙️ Mining Force Drops ({len(drops)} pending)"]
    now = _dt_s.now(_tz_s.utc)
    for d in drops[:6]:
        exp_str = "no expiry"
        if d.get("expires_at"):
            try:
                exp_dt = _dt_s.fromisoformat(d["expires_at"])
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=_tz_s.utc)
                hrs = max(0, int((exp_dt - now).total_seconds() / 3600))
                exp_str = f"exp in {hrs}h"
            except Exception:
                exp_str = d["expires_at"][:13]
        lines.append(
            f"@{d['target_username']} → "
            f"{d['forced_type']}={d['forced_value']} | {exp_str}"
        )
    if len(drops) > 6:
        lines.append(f"+{len(drops) - 6} more.")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_clearforcedrop(bot: BaseBot, user: User, args: list[str]) -> None:
    """/clearforcedrop <username> — cancel pending forced drops (owner-only)."""
    from modules.permissions import is_owner
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner-only command.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !clearforcedrop <username>")
        return

    target = args[1].lower()
    n      = db.clear_forced_drop_by_username(target, user.username)
    if n:
        await _w(bot, user.id, f"Cleared {n} pending forced drop(s) for {target}.")
    else:
        await _w(bot, user.id, f"No pending forced drops found for {target}.")


# ============================================================================
# AutoMine — per-user asyncio tasks
# ============================================================================

_automine_tasks:  dict[str, asyncio.Task] = {}
_am_session_stats: dict[str, dict]        = {}  # uid → in-memory accumulator for summary DM
# Per-user duration override (set by handle_automine when player requests minutes)
_automine_duration_override: dict[str, int] = {}


def _get_am_setting(key: str, default: str) -> str:
    try:
        return db.get_auto_activity_setting(key, default)
    except Exception:
        return default


async def _send_automine_summary(bot: BaseBot, uid: str, uname: str,
                                 stats: dict) -> None:
    """Whisper AutoMine session summary and save it for !lastminesummary."""
    mines   = stats.get("count", 0)
    value   = stats.get("value", 0)
    best    = stats.get("best_name", "—")
    new_cnt = len(stats.get("new_discoveries", []))
    rare_ct = len(stats.get("rare_finds", []))
    msg1 = (f"⛏️ Auto-Mining Complete\n"
            f"Mined: {mines}  Value: {_fmt(value)}c\n"
            f"Best: {best}\n"
            f"New: {new_cnt} | Rare: {rare_ct}")
    saved_text = msg1
    msg2 = ""
    if new_cnt > 0:
        disc  = stats["new_discoveries"][:3]
        names = ", ".join(disc)
        if len(stats["new_discoveries"]) > 3:
            names += f" +{len(stats['new_discoveries']) - 3}"
        total_ore = db.count_collection_items(uid, "mining")
        total_t   = sum(db.get_mining_totals_by_rarity().values()) or 25
        msg2 = (f"📖 Ore Book: {total_ore}/{total_t} discovered\n"
                f"New: {names}")
        saved_text = f"{msg1}\n{msg2}"
    try:
        db.save_auto_session_summary(uid, uname, "mining", saved_text[:500])
    except Exception:
        pass
    try:
        await bot.highrise.send_whisper(uid, msg1[:249])
    except Exception:
        pass
    if msg2:
        try:
            await bot.highrise.send_whisper(uid, msg2[:249])
        except Exception:
            pass


async def _automine_loop(bot: BaseBot, user: User) -> None:
    """Background AutoMine loop for one player."""
    uid      = user.id
    uname    = user.username
    max_att  = int(_get_am_setting("automine_max_attempts", "30"))
    # Use per-user override if set by handle_automine, else fall back to DB setting
    max_mins = _automine_duration_override.pop(uid, None)
    if max_mins is None:
        max_mins = int(_get_am_setting("automine_duration_minutes", "30"))
    start_t  = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    attempts = 0

    await _w(bot, uid,
             f"⛏️ AutoMine Started\n"
             f"Limit: {max_att} mines or {max_mins}m.\n"
             f"Stops if you leave. !automine off to stop.")

    from datetime import datetime as _dt2, timezone as _tz2
    _am_started_at = _dt2.now(_tz2.utc).isoformat()
    try:
        db.save_auto_mine_session(uid, uname, _am_started_at, max_att, max_mins)
    except Exception:
        pass

    _am_session_stats[uid] = {
        "count": 0, "value": 0, "best_value": 0,
        "best_name": "—", "rare_finds": [], "new_discoveries": [],
    }

    try:
        while True:
            if _get_am_setting("automine_enabled", "1") != "1":
                await _w(bot, uid,
                         "⛏️ AutoMine Stopped\nReason: Disabled by staff.")
                break

            if not _is_in_room(uname):
                await _w(bot, uid,
                         f"⛏️ AutoMine Stopped\n"
                         f"Reason: You left the room.\n"
                         f"Mines: {attempts}/{max_att}")
                break

            from datetime import datetime as _dt, timezone as _tz
            elapsed_mins = (_dt.now(_tz.utc) - start_t).total_seconds() / 60
            if attempts >= max_att:
                await _w(bot, uid,
                         f"⛏️ AutoMine Stopped\n"
                         f"Reason: Session limit reached.\n"
                         f"Mines: {attempts}/{max_att}")
                break
            if elapsed_mins >= max_mins:
                await _w(bot, uid,
                         f"⛏️ AutoMine Stopped\n"
                         f"Reason: Time limit reached.\n"
                         f"Mines: {attempts}/{max_att}")
                break

            # Respect the mine cooldown — wait until ready
            miner    = db.get_or_create_miner(uname)
            base_cd  = int(db.get_mine_setting("base_cooldown_seconds", "30"))
            tool_cd  = COOLDOWNS.get(miner["tool_level"], 60)
            cooldown = min(base_cd, tool_cd)
            secs_ago = _seconds_since(miner["last_mine_at"])

            if secs_ago < cooldown:
                wait = max(1, int(cooldown - secs_ago) + 1)
                await asyncio.sleep(wait)
                continue

            # Delegate to handle_mine — it handles events, whispers, etc.
            try:
                await handle_mine(bot, user)
                attempts += 1
                try:
                    db.update_auto_mine_attempts(uid, attempts)
                except Exception:
                    pass
            except Exception as exc:
                print(f"[AUTOMINE] handle_mine error for {uname}: {exc}")

            await asyncio.sleep(cooldown + 1)

    except asyncio.CancelledError:
        pass
    except Exception as exc:
        print(f"[AUTOMINE] Loop error for {uname}: {exc}")
    finally:
        _automine_tasks.pop(uid, None)
        _am_stats = _am_session_stats.pop(uid, None)
        if _am_stats and _am_stats.get("count", 0) > 0:
            try:
                await _send_automine_summary(bot, uid, uname, _am_stats)
            except Exception:
                pass
        try:
            db.stop_auto_mine_session(uid)
        except Exception:
            pass


def stop_automine_for_user(user_id: str, username: str,
                           reason: str = "player_left") -> bool:
    """Cancel the AutoMine task for a user. Returns True if one was running."""
    task = _automine_tasks.pop(user_id, None)
    try:
        db.stop_auto_mine_session(user_id)
    except Exception:
        pass
    if task and not task.done():
        task.cancel()
        return True
    return False


_AUTOMINE_REG_LIMIT = 15   # minutes — regular player max explicit duration
_AUTOMINE_VIP_LIMIT = 60   # minutes — VIP player max explicit duration


async def handle_automine(bot: BaseBot, user: User, args: list[str]) -> None:
    """/automine [minutes]|on|off|status"""
    sub = args[1].lower() if len(args) >= 2 else "on"

    # ── Numeric duration request: !automine <minutes> ─────────────────────
    if sub.isdigit():
        req_mins = int(sub)
        if req_mins < 1:
            await _w(bot, user.id, "⛏️ Duration must be at least 1 minute.")
            return
        is_vip  = db.owns_item(user.id, "vip")
        cap     = _AUTOMINE_VIP_LIMIT if is_vip else _AUTOMINE_REG_LIMIT
        if req_mins > cap:
            if not is_vip:
                await _w(bot, user.id,
                         f"⛏️ AutoMine limit: {_AUTOMINE_REG_LIMIT} minutes.\n"
                         f"VIP can auto mine up to {_AUTOMINE_VIP_LIMIT} minutes.\n"
                         f"Type !vip for perks.")
                return
            req_mins = _AUTOMINE_VIP_LIMIT
        if _get_am_setting("automine_enabled", "1") != "1":
            await _w(bot, user.id, "⛏️ AutoMine is currently disabled by staff.")
            return
        if user.id in _automine_tasks and not _automine_tasks[user.id].done():
            await _w(bot, user.id,
                     "⛏️ AutoMine already running. Use !automine off first.")
            return
        if not _is_in_room(user.username):
            await _w(bot, user.id, "⛏️ You must be in the room to start AutoMine.")
            return
        _automine_duration_override[user.id] = req_mins
        task = asyncio.create_task(_automine_loop(bot, user))
        _automine_tasks[user.id] = task
        return

    # ── on/start ───────────────────────────────────────────────────────────
    if sub in ("on", "start"):
        if _get_am_setting("automine_enabled", "1") != "1":
            await _w(bot, user.id,
                     "⛏️ AutoMine is currently disabled by staff.")
            return
        if user.id in _automine_tasks and not _automine_tasks[user.id].done():
            await _w(bot, user.id,
                     "⛏️ AutoMine is already running.\n"
                     "Use !automine off to stop it first.")
            return
        if not _is_in_room(user.username):
            await _w(bot, user.id,
                     "⛏️ You must be in the room to start AutoMine.")
            return
        task = asyncio.create_task(_automine_loop(bot, user))
        _automine_tasks[user.id] = task

    # ── off/stop ───────────────────────────────────────────────────────────
    elif sub in ("off", "stop"):
        if stop_automine_for_user(user.id, user.username, "user_stopped"):
            await _w(bot, user.id, "⛏️ AutoMine stopped.")
        else:
            await _w(bot, user.id, "⛏️ No active AutoMine session.")

    else:
        await handle_autominesettings(bot, user)


async def handle_autominestatus(bot: BaseBot, user: User) -> None:
    """/autominestatus — show current AutoMine status."""
    running  = user.id in _automine_tasks and not _automine_tasks[user.id].done()
    enabled  = _get_am_setting("automine_enabled", "1") == "1"
    max_att  = _get_am_setting("automine_max_attempts",    "30")
    max_mins = _get_am_setting("automine_duration_minutes", "30")
    is_vip   = db.owns_item(user.id, "vip")
    vip_str  = "Active" if is_vip else "Inactive"
    lines = [
        "⛏️ AutoMine Status",
        f"Status: {'ON' if running else 'OFF'}",
        f"Global: {'Enabled' if enabled else 'Disabled by staff'}",
        f"Limit: {max_att} mines or {max_mins}m",
        f"Duration cap: {_AUTOMINE_REG_LIMIT}m regular / {_AUTOMINE_VIP_LIMIT}m VIP",
        f"VIP: {vip_str}",
        "!automine on to start | !automine off to stop",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_autominesettings(bot: BaseBot, user: User) -> None:
    """/autominesettings — staff config view (also shows status)."""
    running  = user.id in _automine_tasks and not _automine_tasks[user.id].done()
    enabled  = _get_am_setting("automine_enabled",           "1")
    dur      = _get_am_setting("automine_duration_minutes",  "30")
    att      = _get_am_setting("automine_max_attempts",      "30")
    cap      = _get_am_setting("automine_daily_cap_minutes", "120")
    lines = [
        "⛏️ AutoMine Settings",
        f"Your session: {'Running' if running else 'Not running'}",
        f"Enabled: {'YES' if enabled=='1' else 'NO'}",
        f"Session duration: {dur}m",
        f"Session attempts: {att}",
        f"Daily cap: {cap}m (info only)",
        "!automine on | off | <mins>  to control your session",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_setautomine(bot: BaseBot, user: User, args: list[str]) -> None:
    """/setautomine on|off — enable or disable AutoMine globally (manager+)."""
    from modules.permissions import can_manage_economy
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        await _w(bot, user.id, "Usage: !setautomine on|off")
        return
    val = "1" if args[1].lower() == "on" else "0"
    db.set_auto_activity_setting("automine_enabled", val)
    await _w(bot, user.id,
             f"⛏️ AutoMine {'enabled' if val=='1' else 'disabled'}.")


async def handle_setautomineduration(bot: BaseBot, user: User,
                                     args: list[str]) -> None:
    """/setautomineduration <minutes> — max session length (manager+)."""
    from modules.permissions import can_manage_economy
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !setautomineduration <minutes>")
        return
    val = max(5, min(120, int(args[1])))
    db.set_auto_activity_setting("automine_duration_minutes", str(val))
    await _w(bot, user.id, f"⛏️ AutoMine session duration: {val}m.")


async def handle_setautomineattempts(bot: BaseBot, user: User,
                                     args: list[str]) -> None:
    """/setautomineattempts <amount> — max mines per session (manager+)."""
    from modules.permissions import can_manage_economy
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !setautomineattempts <amount>")
        return
    val = max(5, min(200, int(args[1])))
    db.set_auto_activity_setting("automine_max_attempts", str(val))
    await _w(bot, user.id, f"⛏️ AutoMine max attempts: {val}.")


async def handle_setautominedailycap(bot: BaseBot, user: User,
                                     args: list[str]) -> None:
    """/setautominedailycap <minutes> — daily AutoMine cap (manager+)."""
    from modules.permissions import can_manage_economy
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !setautominedailycap <minutes>")
        return
    val = max(30, min(480, int(args[1])))
    db.set_auto_activity_setting("automine_daily_cap_minutes", str(val))
    await _w(bot, user.id, f"⛏️ AutoMine daily cap: {val}m.")


# ---------------------------------------------------------------------------
# AutoMine restart recovery
# ---------------------------------------------------------------------------

async def startup_automine_recovery(bot: BaseBot) -> None:
    """Restart AutoMine sessions for players still in room after a bot restart."""
    await asyncio.sleep(5)  # let room cache populate first
    try:
        sessions = db.get_all_active_auto_mine_sessions()
    except Exception as exc:
        print(f"[AUTOMINE] startup_recovery DB error: {exc}")
        return
    if not sessions:
        return
    print(f"[AUTOMINE] Recovering {len(sessions)} session(s)")

    # Fetch live room user list
    room_names: set[str] = set()
    try:
        resp = await bot.highrise.get_room_users()
        if hasattr(resp, "content"):
            for _u, _ in resp.content:
                room_names.add(_u.username.lower())
    except Exception as _re:
        print(f"[AUTOMINE] Could not get room users: {_re}")

    class _FakeUser:
        def __init__(self, uid: str, uname: str) -> None:
            self.id       = uid
            self.username = uname

    for s in sessions:
        uid   = s["user_id"]
        uname = s["username"]
        if room_names and uname.lower() not in room_names:
            try:
                db.stop_auto_mine_session(uid, "restart_not_in_room")
            except Exception:
                pass
            continue
        if uid in _automine_tasks and not _automine_tasks[uid].done():
            continue
        fake_user = _FakeUser(uid, uname)
        task = asyncio.create_task(_automine_loop(bot, fake_user))
        _automine_tasks[uid] = task
        print(f"[AUTOMINE] Resumed session for @{uname}")
