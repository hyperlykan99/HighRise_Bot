"""
modules/mining_weights.py
-------------------------
Ore weight system for the Mining game.

Generates a weight per mine action, records it in ore_weight_records,
and provides leaderboard, announcement-settings, and admin commands.

The existing quantity-based inventory and sell system is UNCHANGED.
Weights are for display, history, and leaderboards only.
"""
from __future__ import annotations

import json
import random

import database as db
from highrise import BaseBot, User
from modules.mining_colors import (
    format_mining_rarity,
    format_ore_name,
    RARITY_WEIGHT_RANGES,
    LBL_WEIGHT,
)
from modules.permissions import is_admin, is_owner, can_manage_economy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _w(bot: BaseBot, uid: str, msg: str):
    return bot.highrise.send_whisper(uid, str(msg)[:249])


def _can_weight_admin(username: str) -> bool:
    return is_admin(username) or is_owner(username) or can_manage_economy(username)


def _fmt(val: int) -> str:
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f}M"
    if val >= 1_000:
        return f"{val // 1_000}K"
    return f"{val:,}"


# ---------------------------------------------------------------------------
# Weight settings DB helpers  (mining_weight_settings table)
# ---------------------------------------------------------------------------

def get_weight_setting(key: str, default: str = "") -> str:
    conn = db.get_connection()
    row  = conn.execute(
        "SELECT value FROM mining_weight_settings WHERE key=?", (key,)
    ).fetchone()
    conn.close()
    return row["value"] if row else default


def set_weight_setting(key: str, value: str) -> None:
    conn = db.get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO mining_weight_settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Ore weight records DB helpers  (ore_weight_records table)
# ---------------------------------------------------------------------------

def add_weight_record(
    username: str,
    user_id: str,
    ore_name: str,
    rarity: str,
    weight: float,
    base_value: int,
    final_value: int,
    mxp: int,
) -> None:
    conn = db.get_connection()
    conn.execute(
        """INSERT INTO ore_weight_records
           (ore_name, rarity, weight, base_value, final_value, mxp,
            user_id, username, mined_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (ore_name, rarity, weight, base_value, final_value, mxp,
         user_id, username.lower()),
    )
    conn.commit()
    conn.close()


def get_ore_weight_lb(ore_name: str, mode: str = "best", limit: int = 10) -> list[dict]:
    """Return top-weight records for a specific ore."""
    conn = db.get_connection()
    if mode == "best":
        rows = conn.execute(
            """SELECT username, MAX(weight) AS weight, base_value, final_value, mxp
               FROM ore_weight_records
               WHERE lower(ore_name)=lower(?)
               GROUP BY lower(username)
               ORDER BY weight DESC LIMIT ?""",
            (ore_name, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT username, weight, base_value, final_value, mxp
               FROM ore_weight_records
               WHERE lower(ore_name)=lower(?)
               ORDER BY weight DESC LIMIT ?""",
            (ore_name, limit),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_player_heaviest(username: str, ore_name: str | None = None) -> list[dict]:
    """Return player's heaviest finds, optionally filtered by ore."""
    conn = db.get_connection()
    if ore_name:
        rows = conn.execute(
            """SELECT ore_name, rarity, MAX(weight) AS weight, final_value
               FROM ore_weight_records
               WHERE lower(username)=lower(?) AND lower(ore_name)=lower(?)
               GROUP BY lower(ore_name)
               ORDER BY weight DESC LIMIT 1""",
            (username, ore_name),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT ore_name, rarity, MAX(weight) AS weight, final_value
               FROM ore_weight_records
               WHERE lower(username)=lower(?)
               GROUP BY lower(ore_name)
               ORDER BY weight DESC LIMIT 10""",
            (username,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_top_weights_overall(limit: int = 10) -> list[dict]:
    """Return overall heaviest records across all ores."""
    mode = get_weight_setting("weight_lb_mode", "best")
    conn = db.get_connection()
    if mode == "best":
        rows = conn.execute(
            """SELECT username, ore_name, rarity, MAX(weight) AS weight, final_value
               FROM ore_weight_records
               GROUP BY lower(username), lower(ore_name)
               ORDER BY weight DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT username, ore_name, rarity, weight, final_value
               FROM ore_weight_records
               ORDER BY weight DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_ores_with_records() -> list[str]:
    """Return list of ore names that have weight records."""
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT DISTINCT ore_name FROM ore_weight_records ORDER BY ore_name"
    ).fetchall()
    conn.close()
    return [r["ore_name"] for r in rows]


def _get_ore_rarity_from_records(ore_name: str) -> str:
    conn = db.get_connection()
    row  = conn.execute(
        "SELECT rarity FROM ore_weight_records WHERE lower(ore_name)=lower(?) LIMIT 1",
        (ore_name,),
    ).fetchone()
    conn.close()
    return row["rarity"] if row else "common"


# ---------------------------------------------------------------------------
# Weight generation
# ---------------------------------------------------------------------------

def _get_rarity_weight_range(rarity: str) -> tuple[float, float]:
    """Return (min, max) weight for a rarity, checking DB overrides first."""
    key = rarity.lower()
    raw = get_weight_setting("rarity_weight_ranges_json", "")
    if raw:
        try:
            overrides = json.loads(raw)
            if key in overrides:
                lo, hi = overrides[key]
                return float(lo), float(hi)
        except Exception:
            pass
    return RARITY_WEIGHT_RANGES.get(key, (0.50, 2.00))


def generate_weight(rarity: str) -> float:
    """Generate a random weight (kg) for a mined ore based on rarity."""
    lo, hi = _get_rarity_weight_range(rarity)
    return round(random.uniform(lo, hi), 2)


def compute_final_value(base_value: int, weight: float) -> int:
    """Compute weighted ore value: base × weight × scale."""
    try:
        scale = float(get_weight_setting("weight_value_multiplier_scale", "1.0"))
    except Exception:
        scale = 1.0
    return max(0, int(base_value * weight * scale))


def weights_enabled() -> bool:
    return get_weight_setting("weights_enabled", "1") == "1"


# ---------------------------------------------------------------------------
# Announcement decision helper
# ---------------------------------------------------------------------------

_ANNOUNCE_ORDER = [
    "common", "uncommon", "rare", "epic",
    "legendary", "mythic", "ultra_rare", "prismatic", "exotic",
]


def should_announce(rarity: str, ore_name: str) -> bool:
    """Return True if this mine result should be publicly announced."""
    if db.get_mine_setting("mining_announce_enabled", "1") != "1":
        return False
    overrides_raw = get_weight_setting("per_ore_announce_overrides", "")
    if overrides_raw:
        try:
            overrides = json.loads(overrides_raw)
            ore_key   = ore_name.lower().replace(" ", "_")
            if ore_key in overrides:
                return overrides[ore_key] == "on"
        except Exception:
            pass
    min_rar = db.get_mine_setting("mining_announce_min_rarity", "legendary").lower()
    try:
        threshold_idx = _ANNOUNCE_ORDER.index(min_rar)
    except ValueError:
        threshold_idx = _ANNOUNCE_ORDER.index("legendary")
    try:
        rarity_idx = _ANNOUNCE_ORDER.index(rarity.lower())
    except ValueError:
        rarity_idx = 0
    return rarity_idx >= threshold_idx


def _get_per_ore_overrides() -> dict[str, str]:
    raw = get_weight_setting("per_ore_announce_overrides", "")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return {}


def _set_per_ore_override(ore_name: str, setting: str) -> None:
    overrides = _get_per_ore_overrides()
    overrides[ore_name.lower()] = setting
    set_weight_setting("per_ore_announce_overrides", json.dumps(overrides))


# ---------------------------------------------------------------------------
# /oreweightlb  /weightlb  /heaviest  <ore_name>
# ---------------------------------------------------------------------------

async def handle_oreweightlb(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /oreweightlb <ore_name>  e.g. /oreweightlb gold_ore")
        return
    ore_raw = "_".join(args[1:]).strip().lower()
    mode    = get_weight_setting("weight_lb_mode", "best")
    rows    = get_ore_weight_lb(ore_raw, mode=mode, limit=10)
    if not rows:
        await _w(bot, user.id, f"No weight records for '{ore_raw}' yet. Mine some!")
        return
    rarity      = _get_ore_rarity_from_records(ore_raw)
    ore_colored = format_ore_name(ore_raw.replace("_", " ").title(), rarity)
    lines = [f"{LBL_WEIGHT} {ore_colored} LB"]
    for i, r in enumerate(rows, 1):
        disp    = db.get_display_name_by_username(r["username"])
        val_str = f" | {_fmt(r['final_value'])}c" if r.get("final_value") else ""
        lines.append(f"{i}. {disp} — {r['weight']}kg{val_str}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_myheaviest(bot: BaseBot, user: User, args: list[str]) -> None:
    ore_filter = "_".join(args[1:]).strip().lower() if len(args) > 1 else None
    rows = get_player_heaviest(user.username, ore_filter)
    if not rows:
        msg = f"No weight records for '{ore_filter}' yet." if ore_filter \
              else "No weight records yet. Mine some ores!"
        await _w(bot, user.id, msg)
        return
    lines = [f"{LBL_WEIGHT} Your Heaviest Finds"]
    for r in rows:
        rarity      = r.get("rarity", "common")
        ore_colored = format_ore_name(r["ore_name"].replace("_", " ").title(), rarity)
        lines.append(f"{ore_colored}: {r['weight']}kg")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_oreweights(bot: BaseBot, user: User) -> None:
    ores = get_ores_with_records()
    if not ores:
        await _w(bot, user.id, f"{LBL_WEIGHT} No ore weight records yet. Start mining!")
        return
    names  = ", ".join(o.replace("_", " ").title() for o in ores[:14])
    suffix = f" (+{len(ores) - 14} more)" if len(ores) > 14 else ""
    await _w(bot, user.id, f"{LBL_WEIGHT} Ores with records: {names}{suffix}"[:249])


async def handle_topweights(bot: BaseBot, user: User) -> None:
    rows = get_top_weights_overall(limit=10)
    if not rows:
        await _w(bot, user.id, "🏆 No weight records yet. Start mining!")
        return
    lines = ["🏆 Heaviest Ore Finds"]
    for i, r in enumerate(rows, 1):
        disp        = db.get_display_name_by_username(r["username"])
        rarity      = r.get("rarity", "common")
        ore_colored = format_ore_name(r["ore_name"].replace("_", " ").title(), rarity)
        lines.append(f"{i}. {disp} — {ore_colored} {r['weight']}kg")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_setweightlbmode(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_weight_admin(user.username):
        await _w(bot, user.id, "Admin only.")
        return
    if len(args) < 2 or args[1].lower() not in ("best", "all"):
        await _w(bot, user.id, "Usage: /setweightlbmode <best|all>")
        return
    mode = args[1].lower()
    set_weight_setting("weight_lb_mode", mode)
    await _w(bot, user.id, f"{LBL_WEIGHT} LB mode set to '{mode}'.")


# ---------------------------------------------------------------------------
# /mineannounce  /setmineannounce  /setoreannounce  /oreannounce
# /mineannouncesettings
# ---------------------------------------------------------------------------

_VALID_ANNOUNCE_VALS = [
    "off", "common", "uncommon", "rare", "epic",
    "legendary", "mythic", "prismatic", "ultra_rare", "exotic",
]


async def handle_mineannounce(bot: BaseBot, user: User) -> None:
    enabled     = db.get_mine_setting("mining_announce_enabled", "1") == "1"
    min_rar     = db.get_mine_setting("mining_announce_min_rarity", "legendary")
    state       = "ON" if enabled else "OFF"
    overrides   = _get_per_ore_overrides()
    over_str    = f" | {len(overrides)} per-ore override(s)" if overrides else ""
    colored_rar = format_mining_rarity(min_rar)
    await _w(bot, user.id,
             f"📣 Announce: {state} | Threshold: {colored_rar}{over_str}\n"
             f"/setmineannounce <off|rarity>  /setoreannounce <ore> <on|off>"[:249])


async def handle_setmineannounce(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_weight_admin(user.username):
        await _w(bot, user.id, "Admin only.")
        return
    if len(args) < 2 or args[1].lower() not in _VALID_ANNOUNCE_VALS:
        await _w(bot, user.id,
                 "Usage: /setmineannounce <off|common|rare|epic|legendary|mythic|prismatic|exotic>")
        return
    val = args[1].lower()
    if val == "off":
        db.set_mine_setting("mining_announce_enabled", "0")
        await _w(bot, user.id, "📣 Mining announcements OFF.")
    else:
        rar = "ultra_rare" if val == "prismatic" else val
        db.set_mine_setting("mining_announce_enabled", "1")
        db.set_mine_setting("mining_announce_min_rarity", rar)
        colored_rar = format_mining_rarity(rar)
        await _w(bot, user.id, f"📣 Announce threshold → {colored_rar} and above.")


async def handle_setoreannounce(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_weight_admin(user.username):
        await _w(bot, user.id, "Admin only.")
        return
    if len(args) < 3 or args[-1].lower() not in ("on", "off"):
        await _w(bot, user.id, "Usage: /setoreannounce <ore_name> <on|off>")
        return
    ore_name = "_".join(args[1:-1]).lower()
    setting  = args[-1].lower()
    _set_per_ore_override(ore_name, setting)
    await _w(bot, user.id, f"📣 {ore_name} announce → {setting.upper()}.")


async def handle_oreannounce(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /oreannounce <ore_name>")
        return
    ore_name  = "_".join(args[1:]).lower()
    overrides = _get_per_ore_overrides()
    if ore_name in overrides:
        setting = overrides[ore_name].upper()
        await _w(bot, user.id, f"📣 {ore_name}: per-ore override = {setting}")
    else:
        min_rar = db.get_mine_setting("mining_announce_min_rarity", "legendary")
        await _w(bot, user.id,
                 f"📣 {ore_name}: uses threshold ({min_rar}) — no override set.")


async def handle_mineannouncesettings(bot: BaseBot, user: User) -> None:
    if not _can_weight_admin(user.username):
        await _w(bot, user.id, "Admin only.")
        return
    enabled     = db.get_mine_setting("mining_announce_enabled", "1") == "1"
    min_rar     = db.get_mine_setting("mining_announce_min_rarity", "legendary")
    overrides   = _get_per_ore_overrides()
    colored_rar = format_mining_rarity(min_rar)
    state       = "ON" if enabled else "OFF"
    await _w(bot, user.id,
             f"📣 Announce Settings\n"
             f"Enabled: {state} | Min rarity: {colored_rar}\n"
             f"Per-ore overrides: {len(overrides)}"[:249])
    if overrides:
        items = " | ".join(f"{k}:{v}" for k, v in list(overrides.items())[:5])
        await _w(bot, user.id, f"Overrides: {items}"[:249])


# ---------------------------------------------------------------------------
# /mineweights  /setmineweights  /setweightscale
# /setrarityweightrange  /oreweightsettings
# ---------------------------------------------------------------------------

async def handle_mineweights(bot: BaseBot, user: User) -> None:
    enabled = get_weight_setting("weights_enabled", "1") == "1"
    scale   = get_weight_setting("weight_value_multiplier_scale", "1.0")
    mode    = get_weight_setting("weight_lb_mode", "best")
    state   = "ON" if enabled else "OFF"
    await _w(bot, user.id,
             f"{LBL_WEIGHT} System: {state} | Scale: {scale} | LB mode: {mode}\n"
             f"/setmineweights on|off  /setweightscale <n>"[:249])


async def handle_setmineweights(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_weight_admin(user.username):
        await _w(bot, user.id, "Admin only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        await _w(bot, user.id, "Usage: /setmineweights <on|off>")
        return
    val   = "1" if args[1].lower() == "on" else "0"
    label = "ON" if val == "1" else "OFF"
    set_weight_setting("weights_enabled", val)
    await _w(bot, user.id, f"{LBL_WEIGHT} Weight system {label}.")


async def handle_setweightscale(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_weight_admin(user.username):
        await _w(bot, user.id, "Admin only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /setweightscale <number>  e.g. /setweightscale 0.5")
        return
    try:
        scale = float(args[1])
        if scale < 0 or scale > 100:
            raise ValueError()
    except ValueError:
        await _w(bot, user.id, "Scale must be a number between 0 and 100.")
        return
    set_weight_setting("weight_value_multiplier_scale", str(round(scale, 4)))
    await _w(bot, user.id, f"{LBL_WEIGHT} Value scale set to {scale}.")


async def handle_setrarityweightrange(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_weight_admin(user.username):
        await _w(bot, user.id, "Admin only.")
        return
    if len(args) < 4:
        await _w(bot, user.id,
                 "Usage: /setrarityweightrange <rarity> <min_kg> <max_kg>")
        return
    rarity = args[1].lower()
    valid  = set(RARITY_WEIGHT_RANGES) | {"ultra_rare"}
    if rarity not in valid:
        await _w(bot, user.id,
                 "Valid: common, uncommon, rare, epic, legendary, mythic, "
                 "ultra_rare, exotic")
        return
    try:
        lo = float(args[2])
        hi = float(args[3])
        if lo < 0 or hi < lo:
            raise ValueError()
    except ValueError:
        await _w(bot, user.id, "min and max must be positive numbers with min ≤ max.")
        return
    raw = get_weight_setting("rarity_weight_ranges_json", "")
    try:
        overrides = json.loads(raw) if raw else {}
    except Exception:
        overrides = {}
    overrides[rarity] = [lo, hi]
    set_weight_setting("rarity_weight_ranges_json", json.dumps(overrides))
    await _w(bot, user.id, f"{LBL_WEIGHT} {rarity} range: {lo}–{hi}kg.")


async def handle_oreweightsettings(bot: BaseBot, user: User) -> None:
    if not _can_weight_admin(user.username):
        await _w(bot, user.id, "Admin only.")
        return
    enabled     = get_weight_setting("weights_enabled", "1") == "1"
    scale       = get_weight_setting("weight_value_multiplier_scale", "1.0")
    mode        = get_weight_setting("weight_lb_mode", "best")
    raw         = get_weight_setting("rarity_weight_ranges_json", "")
    state       = "ON" if enabled else "OFF"
    try:
        overrides   = json.loads(raw) if raw else {}
        range_count = len(overrides)
    except Exception:
        overrides   = {}
        range_count = 0
    await _w(bot, user.id,
             f"{LBL_WEIGHT} Detailed Settings\n"
             f"Enabled: {state} | Scale: {scale} | LB: {mode}\n"
             f"Custom rarity ranges: {range_count}"[:249])
    if range_count:
        items = " | ".join(
            f"{k}: {v[0]}-{v[1]}kg"
            for k, v in list(overrides.items())[:4]
        )
        await _w(bot, user.id, f"Ranges: {items}"[:249])
