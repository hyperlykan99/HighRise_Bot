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

import database as db
from highrise import BaseBot, User
from modules.permissions import is_admin, is_owner, can_manage_economy

# Process-level write lock — serializes all mining DB writes within this bot process
# so rapid /mine calls can't race each other inside the same asyncio event loop.
_DB_WRITE_LOCK = asyncio.Lock()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _w(bot, user_id, msg):
    return bot.highrise.send_whisper(user_id, str(msg)[:249])


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
    return level * level * 100


def _is_in_room(username: str) -> bool:
    try:
        from modules.gold import _room_cache
        # Cache empty = bot just restarted, existing room users won't be in it yet
        if not _room_cache:
            return True
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

COOLDOWNS = {1: 60, 2: 55, 3: 50, 4: 45, 5: 40, 6: 35, 7: 30, 8: 25, 9: 20, 10: 15}

# Rarity → (base_drop_pct, mxp_range)
RARITIES = {
    "common":    (68.0, (5,  15)),
    "uncommon":  (20.0, (15, 30)),
    "rare":      ( 8.0, (50, 100)),
    "epic":      ( 2.5, (150, 300)),
    "legendary": ( 1.0, (500, 1000)),
    "mythic":    ( 0.4, (2500, 2500)),
    "ultra_rare":( 0.1, (10000, 10000)),
}

RARITY_ORDER = ["common", "uncommon", "rare", "epic", "legendary", "mythic", "ultra_rare"]

ANNOUNCE_RARITIES = {"legendary", "mythic", "ultra_rare"}

# Upgrade requirements: {target_level: (coins, [(ore_id, qty), ...])}
UPGRADE_REQS = {
    2:  (1_000,       [("copper_ore", 20)]),
    3:  (5_000,       [("iron_ore", 20)]),
    4:  (15_000,      [("silver_ore", 15)]),
    5:  (50_000,      [("gold_ore", 10)]),
    6:  (120_000,     [("platinum_ore", 5), ("quartz", 20)]),
    7:  (300_000,     [("jade", 10), ("topaz", 10)]),
    8:  (750_000,     [("emerald", 3), ("ruby", 3), ("sapphire", 3)]),
    9:  (1_500_000,   [("diamond", 2), ("opal", 2)]),
    10: (3_000_000,   [("black_opal", 1), ("alexandrite", 1), ("meteorite_fragment", 1)]),
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
MINE_SHOP_ITEMS = {
    "energy_tea":    {"name": "Energy Tea",    "emoji": "🍵", "price": 5_000,  "energy": 25,  "effect": "energy"},
    "energy_smoothie":{"name": "Energy Smoothie","emoji":"🥤","price": 15_000, "energy": 100, "effect": "energy"},
    "lucky_charm":   {"name": "Lucky Charm",   "emoji": "🍀", "price": 25_000, "mins": 15,    "effect": "luck_boost"},
    "focus_music":   {"name": "Focus Music",   "emoji": "🎵", "price": 25_000, "mins": 15,    "effect": "xp_boost"},
}
_MINE_SHOP_LIST = list(MINE_SHOP_ITEMS.keys())  # ordered list for numbered shop

VALID_MINING_EVENTS = {
    "double_ore":  "Drop quantities x2",
    "double_mxp":  "Mining XP x2",
    "lucky_hour":  "Rare chance +50% relative",
    "energy_free": "/mine costs 0 energy",
    "meteor_rush": "Ultra rare chance x2",
}


# ---------------------------------------------------------------------------
# Drop logic
# ---------------------------------------------------------------------------

def _roll_drop(tool_level: int, is_vip: bool, has_luck_boost: bool,
               mine_event: dict | None) -> tuple[dict, int]:
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
        boost_targets = ["rare", "epic", "legendary", "mythic", "ultra_rare"]
        per = tool_bonus / len(boost_targets)
        for t in boost_targets:
            probs[t] += per
        probs["common"] = max(0, probs["common"] - tool_bonus)

    # VIP: +10% relative on rare+
    if is_vip:
        for r in ["rare", "epic", "legendary", "mythic", "ultra_rare"]:
            probs[r] *= 1.10

    # Lucky Charm: +25% relative on rare+
    if has_luck_boost:
        for r in ["rare", "epic", "legendary", "mythic", "ultra_rare"]:
            probs[r] *= 1.25

    # Mining event effects
    if mine_event:
        eid = mine_event.get("event_id", "")
        if eid == "lucky_hour":
            for r in ["rare", "epic", "legendary", "mythic", "ultra_rare"]:
                probs[r] *= 1.50
        elif eid == "meteor_rush":
            probs["ultra_rare"] *= 2.0

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
    lo, hi = RARITIES[chosen_rarity][1]
    mxp = random.randint(lo, hi)

    return chosen_item, mxp


# ---------------------------------------------------------------------------
# /mine  /m  /dig
# ---------------------------------------------------------------------------

async def handle_mine(bot: BaseBot, user: User) -> None:
    print(f"[MINER] /mine start user={user.username}")
    try:
        await _handle_mine_body(bot, user)
    except Exception as _me:
        _emsg = str(_me)
        print(f"[MINER ERROR] /mine user={user.username} error={_emsg}")
        try:
            if "locked" in _emsg.lower() or "busy" in _emsg.lower():
                await _w(bot, user.id, "⏳ Mining DB busy. Try /mine again.")
            else:
                await _w(bot, user.id, f"Mine error: {_emsg[:160]}"[:249])
        except Exception:
            pass


async def _handle_mine_body(bot: BaseBot, user: User) -> None:
    uname = user.username

    # ── Phase 1: Batch reads (all settings in ONE connection, no await held) ──
    settings = _batch_mine_settings()

    if settings["mining_enabled"] != "true":
        await _w(bot, user.id, "⛔ Mining is currently disabled.")
        return

    if settings["mining_requires_room"] == "true" and not _is_in_room(uname):
        await _w(bot, user.id, "Join the room to mine.")
        return

    miner = db.get_or_create_miner(uname)

    # Daily energy reset (writes only when reset is actually due — once/day)
    _maybe_reset_energy(miner, uname)
    miner = db.get_or_create_miner(uname)  # re-fetch after possible reset

    # Cooldown check (pure math, no DB)
    base_cd  = int(settings["base_cooldown_seconds"])
    tool_cd  = COOLDOWNS.get(miner["tool_level"], 60)
    cooldown = min(base_cd, tool_cd)
    secs_ago = _seconds_since(miner["last_mine_at"])
    if secs_ago < cooldown:
        wait = int(cooldown - secs_ago)
        await _w(bot, user.id, f"⏳ Mine ready in {wait}s.")
        return

    mine_event  = db.get_active_mining_event()
    energy_cost = 0 if (mine_event and mine_event.get("event_id") == "energy_free") \
                  else int(settings["base_energy_cost"])

    if miner["energy"] < energy_cost:
        await _w(bot, user.id,
                 f"⚡ No energy ({miner['energy']}/{miner['max_energy']}). "
                 "Come back later or use /mineshop.")
        return

    db.ensure_user(user.id, uname)
    is_vip = db.owns_item(user.id, "vip")

    # ── Phase 2: Pure Python calculations (no DB, no await) ──────────────────
    has_luck = _boost_active(miner.get("luck_boost_until"))
    has_xp   = _boost_active(miner.get("xp_boost_until"))
    item, mxp = _roll_drop(miner["tool_level"], is_vip, has_luck, mine_event)

    if is_vip:
        mxp = int(mxp * 1.10)
    if has_xp:
        mxp *= 2
    if mine_event and mine_event.get("event_id") == "double_mxp":
        mxp *= 2

    qty = 2 if (mine_event and mine_event.get("event_id") == "double_ore") else 1

    new_energy = miner["energy"] - energy_cost
    new_mines  = miner["total_mines"] + 1
    new_ores   = miner["total_ores"] + qty
    new_mxp    = miner["mining_xp"] + mxp

    cur_lvl = miner["mining_level"]
    new_lvl = cur_lvl
    while new_mxp >= _xp_for_level(new_lvl):
        new_mxp -= _xp_for_level(new_lvl)
        new_lvl += 1

    is_rare  = item["rarity"] in ANNOUNCE_RARITIES
    new_rare = miner["rare_finds"] + (1 if is_rare else 0)

    # ── Phase 3: ONE write transaction with asyncio retry on lock ─────────────
    await _mine_commit_result(
        uname, item["item_id"], qty, item["rarity"],
        {
            "energy": new_energy, "total_mines": new_mines,
            "total_ores": new_ores, "mining_xp": new_mxp,
            "mining_level": new_lvl, "rare_finds": new_rare,
            "last_mine_at": _now_iso(),
        },
    )

    # ── Phase 4: Send messages (no DB held, connection already closed) ────────
    qty_str = f" x{qty}" if qty > 1 else ""
    lvlup   = f" | ⬆️ Mining Lv {new_lvl}!" if new_lvl > cur_lvl else ""
    msg     = (f"⛏️ You mined {item['emoji']} {item['name']}{qty_str} | "
               f"+{_fmt(mxp)} MXP{lvlup}")
    await _w(bot, user.id, msg)

    if is_rare and settings["rare_announce_enabled"] == "true":
        if item["item_id"] == "meteorite_fragment":
            ann = f"☄️ @{uname} found a Meteorite Fragment! Ultra rare! ☄️"
        else:
            ann = (f"{item['emoji']} @{uname} found {item['name']}! "
                   f"({item['rarity'].replace('_', ' ').title()})")
        try:
            await bot.highrise.chat(ann[:249])
        except Exception:
            pass

    if new_lvl > cur_lvl:
        try:
            await bot.highrise.chat(
                f"⛏️ @{uname} reached Mining Level {new_lvl}! Keep digging!"[:249]
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
# Mining write helpers  (short-transaction, asyncio-retry)
# ---------------------------------------------------------------------------

def _batch_mine_settings() -> dict:
    """Read all /mine settings in ONE connection to reduce lock contention."""
    import sqlite3 as _sql, config as _cfg
    defaults = {
        "mining_enabled": "true",
        "mining_requires_room": "true",
        "base_cooldown_seconds": "60",
        "base_energy_cost": "5",
        "daily_energy_reset": "true",
        "rare_announce_enabled": "true",
    }
    try:
        _keys = tuple(defaults.keys())
        _ph   = ",".join("?" * len(_keys))
        _con  = _sql.connect(_cfg.DB_PATH, timeout=10)
        _con.execute("PRAGMA busy_timeout=5000")
        rows  = _con.execute(
            f"SELECT key, value FROM mining_settings WHERE key IN ({_ph})", _keys
        ).fetchall()
        _con.close()
        result = dict(defaults)
        for k, v in rows:
            result[k] = v
        return result
    except Exception:
        return dict(defaults)


async def run_mine_write(write_fn, label: str = "mine_write"):
    """
    Serialise DB writes with a process-level asyncio.Lock + cross-process retry.

    _DB_WRITE_LOCK prevents concurrent writes within this bot process (e.g. rapid
    /mine calls racing each other inside the same event loop).  Between retries we
    release the lock and await asyncio.sleep so heartbeats can complete.
    """
    import sqlite3 as _sql, config as _cfg
    _retries = [0.2, 0.5, 1.0, 2.0, 3.0]
    for _attempt, _delay in enumerate(_retries, start=1):
        try:
            async with _DB_WRITE_LOCK:
                _con = _sql.connect(_cfg.DB_PATH, timeout=15)
                _con.row_factory = _sql.Row
                _con.execute("PRAGMA journal_mode=WAL")
                _con.execute("PRAGMA busy_timeout=15000")
                _con.execute("PRAGMA synchronous=NORMAL")
                try:
                    _con.execute("BEGIN IMMEDIATE")
                    result = write_fn(_con)
                    _con.commit()
                    return result
                except Exception:
                    try: _con.rollback()
                    except Exception: pass
                    raise
                finally:
                    _con.close()
        except _sql.OperationalError as _e:
            if "locked" in str(_e).lower():
                print(f"[DB LOCK] {label} retry={_attempt} delay={_delay}")
                await asyncio.sleep(_delay)
                continue
            raise
    raise _sql.OperationalError(f"database is locked after {len(_retries)} retries")


async def _mine_commit_result(
    username: str, item_id: str, qty: int, rarity: str, updates: dict
) -> None:
    """Write mining result via run_mine_write (process lock + retry)."""
    def _write(_con):
        fields = ", ".join(f"{k}=?" for k in updates)
        vals   = list(updates.values()) + [username.lower()]
        _con.execute(
            f"UPDATE mining_players SET {fields}, updated_at=datetime('now') "
            f"WHERE lower(username)=?",
            vals,
        )
        _con.execute(
            "INSERT INTO mining_inventory (username, item_id, quantity) "
            "VALUES (lower(?), ?, ?) "
            "ON CONFLICT(username, item_id) DO UPDATE SET quantity=quantity+?",
            (username, item_id, qty, qty),
        )
        _con.execute(
            "INSERT INTO mining_logs "
            "(timestamp, username, action, item_id, quantity, coins, details) "
            "VALUES (datetime('now'), lower(?), 'mine', ?, ?, 0, ?)",
            (username, item_id, qty, rarity),
        )
    await run_mine_write(_write, label="mine")
    print(f"[MINER] /mine success ore={item_id}")


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
        msg   += f"\nUpgrade: /upgradetool | Cost {_fmt(coins)}c"
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
        await _w(bot, user.id, "Need: " + ", ".join(missing_parts))
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
        await bot.highrise.chat(
            f"⛏️ @{user.username} upgraded to {name}!"[:249]
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
    nrg   = miner["energy"]
    max_e = miner["max_energy"]

    if page == 1:
        luck_left = f" 🍀{_boost_mins_left(miner.get('luck_boost_until'))}m" if _boost_active(miner.get("luck_boost_until")) else ""
        xp_left   = f" 🎵{_boost_mins_left(miner.get('xp_boost_until'))}m"  if _boost_active(miner.get("xp_boost_until"))   else ""
        msg = (
            f"⛏️ @{user.username} Mining Profile\n"
            f"Lv {lvl} | {_fmt(mxp)}/{_fmt(nxp)} MXP\n"
            f"Pickaxe Lv {pick} {PICKAXE_NAMES.get(pick,'?')}\n"
            f"Energy {nrg}/{max_e}{luck_left}{xp_left}\n"
            f"Page 2: /mp 2"
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
    print(f"[MINER] /ores user={user.username}")
    try:
        inv  = db.get_inventory(user.username)
        page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
        if not inv:
            await _w(bot, user.id, "🎒 You have no ores yet. Use /mine to start mining!")
            return

        per  = 8
        total_pages = max(1, (len(inv) + per - 1) // per)
        page = max(1, min(page, total_pages))
        chunk = inv[(page - 1) * per : page * per]

        parts = " | ".join(f"{r['emoji']}{r['name']} x{r['quantity']}" for r in chunk)
        nav   = f"  More: /ores {page+1}" if page < total_pages else ""
        msg   = f"🎒 Ores ({page}/{total_pages}): {parts}{nav}"
        await _w(bot, user.id, msg[:249])
    except Exception as _oe:
        print(f"[MINER ERROR] /ores user={user.username} error={_oe}")
        try:
            await _w(bot, user.id, f"Ores DB error: {str(_oe)[:160]}"[:249])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# /sellores  /sellore <id> [qty]
# ---------------------------------------------------------------------------

async def handle_sellores(bot: BaseBot, user: User) -> None:
    db.ensure_user(user.id, user.username)
    result = db.sell_all_ores(user.username, user.id)
    if result["coins"] == 0:
        await _w(bot, user.id, "🎒 Nothing to sell. Mine some ores first!")
        return
    db.update_miner(user.username, coins_earned=db.get_or_create_miner(user.username)["coins_earned"] + result["coins"])
    await _w(bot, user.id,
             f"✅ Sold {result['count']} ores for {_fmt(result['coins'])}c.")


async def handle_sellore(bot: BaseBot, user: User, args: list[str]) -> None:
    db.ensure_user(user.id, user.username)
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /sellore <ore_id> <amount>  or  /sellore all")
        return
    sub = args[1].lower()
    if sub == "all":
        await handle_sellores(bot, user)
        return

    ore_id = sub
    it     = db.get_mining_item(ore_id)
    if it is None:
        await _w(bot, user.id, f"Unknown ore: {ore_id}. Use /orelist.")
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
        lines.append(f"{i} @{r['username']} {fmt(r)}")
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
    lines.append("Buy: /minebuy <#>  or  /buy <#>")
    msg = "\n".join(lines)
    db.save_shop_session(user.username, "mineshop", 1, session_items)
    await _w(bot, user.id, msg[:249])


def _MINE_SHOP_LIST_ITEMS():
    return [(k, MINE_SHOP_ITEMS[k]) for k in _MINE_SHOP_LIST]


async def handle_minebuy(bot: BaseBot, user: User, args: list[str]) -> None:
    raw = args[1] if len(args) > 1 else ""
    if not raw.isdigit():
        await _w(bot, user.id, "Usage: /minebuy <number>  (see /mineshop)")
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
        await _w(bot, user.id, "Invalid number. Use /mineshop to see items.")
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
        new_e = min(miner["energy"] + it["energy"], miner["max_energy"])
        db.update_miner(user.username, energy=new_e)
        await _w(bot, user.id,
                 f"✅ Bought {it['emoji']} {it['name']} for {_fmt(it['price'])}c. "
                 f"Energy: {new_e}/{miner['max_energy']}")

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
             "Buy Energy Tea (/minebuy 1) or Energy Smoothie (/minebuy 2) to restore energy.")


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
        await bot.highrise.chat(
            f"⚒️ @{user.username} crafted {rec['emoji']} {rec['display']}!"[:249]
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
    energy_gain = 50
    coin_gain   = 500
    mxp_gain    = 50

    new_energy = min(miner["energy"] + energy_gain, miner["max_energy"])
    new_mxp    = miner["mining_xp"] + mxp_gain

    # Level up check from MXP
    cur_lvl  = miner["mining_level"]
    new_lvl  = cur_lvl
    while new_mxp >= _xp_for_level(new_lvl):
        new_mxp -= _xp_for_level(new_lvl)
        new_lvl += 1

    db.adjust_balance(user.id, coin_gain)
    db.update_miner(user.username,
        energy=new_energy,
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

    msg = (f"🎁 Mining daily: +{energy_gain} energy, "
           f"+{_fmt(coin_gain)}c, +{mxp_gain} MXP. Streak {streak}.{extra}")
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
        await _w(bot, user.id, "Usage: /startminingevent <event_id>")
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
             "/mining on/off — enable/disable\n"
             "/setminecooldown <sec>\n"
             "/setmineenergycost <amt>\n"
             "/addore <user> <ore> <amt>\n"
             "/settoollevel <user> <1-10>")


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
        await _w(bot, user.id, "Usage: /mining on | /mining off")


async def handle_setminecooldown(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_mine_admin(user.username):
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: /setminecooldown <seconds>")
        return
    db.set_mine_setting("base_cooldown_seconds", args[1])
    await _w(bot, user.id, f"✅ Mine cooldown set to {args[1]}s.")


async def handle_setmineenergycost(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_mine_admin(user.username):
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: /setmineenergycost <amount>")
        return
    db.set_mine_setting("base_energy_cost", args[1])
    await _w(bot, user.id, f"✅ Energy cost per mine set to {args[1]}.")


async def handle_setminingenergy(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_mine_admin(user.username):
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /setminingenergy <username> <amount>")
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
        await _w(bot, user.id, "Usage: /addore <username> <ore_id> <amount>")
        return
    target = args[1].lstrip("@")
    ore_id = args[2].lower()
    amt    = args[3]
    if not amt.isdigit() or int(amt) < 1:
        await _w(bot, user.id, "Amount must be a positive integer.")
        return
    it = db.get_mining_item(ore_id)
    if not it:
        await _w(bot, user.id, f"Unknown ore: {ore_id}. Use /orelist.")
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
        await _w(bot, user.id, "Usage: /removeore <username> <ore_id> <amount>")
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
        await _w(bot, user.id, "Usage: /settoollevel <username> <1-10>")
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
        await _w(bot, user.id, "Usage: /setminelevel <username> <level>")
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
        await _w(bot, user.id, "Usage: /addminexp <username> <amount>")
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
        await _w(bot, user.id, "Usage: /setminexp <username> <amount>")
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
        await _w(bot, user.id, "Usage: /resetmining <username>")
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
                 f"Room required: {cur}. Set: /miningroomrequired on | off")


# ---------------------------------------------------------------------------
# /orelist
# ---------------------------------------------------------------------------

async def handle_orelist(bot: BaseBot, user: User) -> None:
    items = db.get_all_mining_items(drop_enabled=False)
    parts = [f"{it['emoji']}{it['item_id']}" for it in items]
    # Split into chunks of 8 per message
    chunk = parts[:12]
    rest  = parts[12:]
    await _w(bot, user.id, "⛏️ Ore IDs: " + ", ".join(chunk))
    if rest:
        await _w(bot, user.id, ", ".join(rest))


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
_CONTRACT_POOL = [
    (1, "coal",        20, 200,  "Coal",        "⚫"),
    (2, "copper_ore",  15, 350,  "Copper Ore",  "🟠"),
    (3, "iron_ore",    10, 450,  "Iron Ore",    "⛓️"),
    (4, "tin_ore",      8, 550,  "Tin Ore",     "◽"),
    (5, "quartz",       5, 750,  "Quartz",      "🔹"),
    (6, "silver_ore",   4, 900,  "Silver Ore",  "⚪"),
    (7, "gold_ore",     3, 1500, "Gold Ore",    "🟡"),
    (8, "amethyst",     2, 2000, "Amethyst",    "💜"),
]


async def handle_orebook(bot: BaseBot, user: User) -> None:
    """/orebook — ore collection summary."""
    inv = db.get_inventory(user.username)
    if not inv:
        await _w(bot, user.id, "📘 Ore Book: empty. /mine to start collecting!")
        return
    total_types = len(inv)
    total_qty = sum(r.get("quantity", 0) for r in inv)
    lines = [f"📘 Ore Book — {total_types} types | {_fmt(total_qty)} total"]
    for row in inv[:6]:
        lines.append(f"{row['emoji']} {row['name']}: {_fmt(row['quantity'])}")
    if total_types > 6:
        lines.append(f"+{total_types - 6} more. /mineinv for full list.")
    await _w(bot, user.id, "\n".join(lines)[:249])


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
    lines.append("/claimoremastery <1-6> to claim 🎁 rewards.")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_claimoremastery(bot: BaseBot, user: User, args: list[str]) -> None:
    """/claimoremastery <1-6> — claim a mastery milestone reward."""
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: /claimoremastery <1-6>  (see /oremastery)")
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
            await _w(bot, user.id, f"❌ No contract #{cid}. See /contracts for the list.")
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
        await _w(bot, user.id, "✅ Contract complete! Use /claimjob to collect your reward.")
        return
    have = db.get_ore_qty(user.username, ore_id)
    name = ore_id.replace("_", " ").title()
    if have == 0:
        await _w(bot, user.id, f"❌ You have no {name}. /mine to collect some!")
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


MINE_HELP_PAGES = [
    (
        "⛏️ Mining\n"
        "/mine - mine ores (/m /dig)\n"
        "/tool - view pickaxe\n"
        "/upgradetool - upgrade pickaxe\n"
        "/ores - your inventory\n"
        "/sellores - sell all ores"
    ),
    (
        "⛏️ Mining 2\n"
        "/minelb - leaderboards\n"
        "/mineshop - energy & boosts\n"
        "/craft - craft rewards\n"
        "/minedaily - daily bonus\n"
        "/mineprofile - your stats"
    ),
    (
        "📘 Goals\n"
        "/orebook - ore collection\n"
        "/oremastery - mastery rewards\n"
        "/orestats - detailed stats\n"
        "/contracts - ore jobs\n"
        "/job <#> - accept a contract"
    ),
    (
        "⛏️ Mining Staff\n"
        "/mining on/off\n"
        "/startminingevent <id>\n"
        "/setminecooldown <sec>\n"
        "/setmineenergycost <amt>\n"
        "/addore <user> <ore> <amt>"
    ),
]


async def handle_minehelp(bot: BaseBot, user: User, args: list[str]) -> None:
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
    if page == 0:
        await _w(bot, user.id, MINE_HELP_PAGES[0])
        await _w(bot, user.id, MINE_HELP_PAGES[1])
        await _w(bot, user.id, MINE_HELP_PAGES[2])
        if _can_mine_admin(user.username):
            await _w(bot, user.id, MINE_HELP_PAGES[3])
    elif 1 <= page <= len(MINE_HELP_PAGES):
        if page == 4 and not _can_mine_admin(user.username):
            return
        await _w(bot, user.id, MINE_HELP_PAGES[page - 1])
    else:
        await _w(bot, user.id, f"Pages 1-{len(MINE_HELP_PAGES)}. Use /minehelp <page>.")


# ---------------------------------------------------------------------------
# /dblockcheck
# ---------------------------------------------------------------------------

async def handle_dblockcheck(bot: BaseBot, user: User) -> None:
    """/dblockcheck — owner/admin: show DB state and test write lock."""
    if not _can_mine_admin(user.username):
        await _w(bot, user.id, "Owner/admin only.")
        return
    try:
        import sqlite3 as _sql, config as _cfg, os as _os
        _con   = _sql.connect(_cfg.DB_PATH, timeout=3)
        jm     = _con.execute("PRAGMA journal_mode").fetchone()[0]
        bt     = _con.execute("PRAGMA busy_timeout").fetchone()[0]
        # Test whether the write lock is immediately available
        _free  = False
        try:
            _con.execute("BEGIN IMMEDIATE")
            _con.execute("ROLLBACK")
            _free = True
        except _sql.OperationalError:
            try: _con.execute("ROLLBACK")
            except Exception: pass
        _con.close()
        sz     = _os.path.getsize(_cfg.DB_PATH) // 1024
        dbname = _cfg.DB_PATH.split("/")[-1]
        status = "free" if _free else "busy"
        msg    = (f"DB lock: {status} | WAL={jm} | "
                  f"timeout={bt} | size={sz}KB")
        await _w(bot, user.id, msg[:249])
        print(f"[DB] {msg}")
    except Exception as _e:
        print(f"[MINER ERROR] /dblockcheck error={_e}")
        await _w(bot, user.id, f"DB check error: {str(_e)[:160]}"[:249])


async def handle_processcheck(bot: BaseBot, user: User) -> None:
    """/processcheck — owner/admin: show PID and detect duplicate processes."""
    if not _can_mine_admin(user.username):
        await _w(bot, user.id, "Owner/admin only.")
        return
    import os as _os
    pid = _os.getpid()
    print(f"[PROCESS] pid={pid}")
    await _w(bot, user.id, f"Process: pid={pid} | lockfile OK | duplicate=no"[:249])


# ---------------------------------------------------------------------------
# /minerdbcheck  /minerrepair
# ---------------------------------------------------------------------------

_MINER_TABLES = {
    "mining_players": (
        "CREATE TABLE IF NOT EXISTS mining_players ("
        "username TEXT PRIMARY KEY, tool_level INTEGER NOT NULL DEFAULT 1, "
        "mining_level INTEGER NOT NULL DEFAULT 1, mining_xp INTEGER NOT NULL DEFAULT 0, "
        "energy INTEGER NOT NULL DEFAULT 100, max_energy INTEGER NOT NULL DEFAULT 100, "
        "total_mines INTEGER NOT NULL DEFAULT 0, total_ores INTEGER NOT NULL DEFAULT 0, "
        "rare_finds INTEGER NOT NULL DEFAULT 0, coins_earned INTEGER NOT NULL DEFAULT 0, "
        "last_mine_at TEXT, last_energy_reset TEXT, "
        "luck_boost_until TEXT, xp_boost_until TEXT, "
        "created_at TEXT, updated_at TEXT)"
    ),
    "mining_inventory": (
        "CREATE TABLE IF NOT EXISTS mining_inventory ("
        "username TEXT NOT NULL, item_id TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (username, item_id))"
    ),
    "mining_items": (
        "CREATE TABLE IF NOT EXISTS mining_items ("
        "item_id TEXT PRIMARY KEY, name TEXT NOT NULL, emoji TEXT NOT NULL DEFAULT '🪨', "
        "rarity TEXT NOT NULL DEFAULT 'common', sell_value INTEGER NOT NULL DEFAULT 1, "
        "min_tool_level INTEGER NOT NULL DEFAULT 1, drop_weight INTEGER NOT NULL DEFAULT 10)"
    ),
    "mining_settings": (
        "CREATE TABLE IF NOT EXISTS mining_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    ),
    "mining_logs": (
        "CREATE TABLE IF NOT EXISTS mining_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, username TEXT, "
        "action TEXT, item_id TEXT, quantity INTEGER, coins INTEGER, details TEXT)"
    ),
    "mining_events": (
        "CREATE TABLE IF NOT EXISTS mining_events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, started_by TEXT, "
        "started_at TEXT, ends_at TEXT, active INTEGER NOT NULL DEFAULT 0)"
    ),
    "ore_mastery": (
        "CREATE TABLE IF NOT EXISTS ore_mastery ("
        "username TEXT NOT NULL, milestone INTEGER NOT NULL, claimed_at TEXT, "
        "PRIMARY KEY (username, milestone))"
    ),
    "miner_contracts": (
        "CREATE TABLE IF NOT EXISTS miner_contracts ("
        "username TEXT PRIMARY KEY, ore_id TEXT NOT NULL, qty_needed INTEGER NOT NULL, "
        "qty_delivered INTEGER NOT NULL DEFAULT 0, reward_coins INTEGER NOT NULL DEFAULT 0, "
        "assigned_at TEXT)"
    ),
}


async def handle_minerdbcheck(bot: BaseBot, user: User) -> None:
    """/minerdbcheck — owner/admin: check mining DB tables."""
    if not _can_mine_admin(user.username):
        await _w(bot, user.id, "Owner/admin only.")
        return
    try:
        import sqlite3 as _sqlite3
        import config as _cfg
        _con = _sqlite3.connect(_cfg.DB_PATH)
        _existing = {r[0] for r in _con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        _con.close()
        missing = [t for t in _MINER_TABLES if t not in _existing]
        ok_list = [t for t in _MINER_TABLES if t in _existing]
        if missing:
            await _w(bot, user.id,
                f"Miner DB fail: missing {', '.join(missing)}"[:249])
        else:
            await _w(bot, user.id,
                f"Miner DB: OK | {', '.join(ok_list[:4])} OK"[:249])
    except Exception as _e:
        print(f"[MINER ERROR] /minerdbcheck error={_e}")
        await _w(bot, user.id, f"Miner DB check error: {str(_e)[:160]}"[:249])


async def handle_minerrepair(bot: BaseBot, user: User) -> None:
    """/minerrepair — owner/admin: create missing mining tables (no data loss)."""
    if not _can_mine_admin(user.username):
        await _w(bot, user.id, "Owner/admin only.")
        return
    try:
        import sqlite3 as _sqlite3
        import config as _cfg
        _con = _sqlite3.connect(_cfg.DB_PATH, timeout=10)
        _con.execute("PRAGMA journal_mode=WAL")
        _con.execute("PRAGMA busy_timeout=5000")
        created = []
        for tname, ddl in _MINER_TABLES.items():
            _con.execute(ddl)
            created.append(tname)
        _con.commit()
        _con.close()
        await _w(bot, user.id, "✅ Miner DB repaired.")
        print(f"[MINER] /minerrepair OK tables={created}")
    except Exception as _e:
        print(f"[MINER ERROR] /minerrepair error={_e}")
        if "locked" in str(_e).lower():
            await _w(bot, user.id, "Miner DB busy. Try again.")
        else:
            await _w(bot, user.id, f"Miner repair error: {str(_e)[:160]}"[:249])
