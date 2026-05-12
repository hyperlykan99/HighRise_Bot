"""
modules/economy.py
------------------
Economy panel, rarity cap management, and mining payout logs.
All commands are manager+ only.
"""
from __future__ import annotations

import database as db
from modules.permissions import can_manage_economy
from modules.mining_weights import (
    _DEFAULT_RARITY_CAPS,
    VALID_CAP_RARITIES,
    get_rarity_cap,
    set_rarity_cap,
    reset_rarity_caps,
    get_multiplier_cap,
)


async def _w(bot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg)


def _fc(n: int) -> str:
    return f"{n:,}"


# ---------------------------------------------------------------------------
# /economypanel  /economybalance  /miningeconomy
# ---------------------------------------------------------------------------

async def handle_economypanel(bot, user) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "❌ Manager+ only.")
        return
    try:
        from modules.events import get_event_effect
        eff = get_event_effect()
    except Exception:
        eff = {}
    surge     = eff.get("ore_value_multiplier", 1.0)
    norm_cap  = get_multiplier_cap(False)
    bless_cap = get_multiplier_cap(True)
    ex_cap    = get_rarity_cap("exotic")
    pris_cap  = get_rarity_cap("prismatic")
    myth_cap  = get_rarity_cap("mythic")
    leg_cap   = get_rarity_cap("legendary")
    try:
        scale = float(db.get_mine_setting("weight_value_multiplier_scale", "1.0"))
    except Exception:
        scale = 1.0
    top        = db.get_biggest_payouts(1)
    top_payout = _fc(top[0]["final_value"]) if top else "N/A"
    await _w(bot, user.id,
        f"<#66CCFF>⚖️ Economy Panel<#FFFFFF>\n"
        f"Value Scale: {scale} | Mult Cap: {norm_cap}x | Blessing Cap: {bless_cap}x\n"
        f"Active Event Surge: {surge}x")
    await _w(bot, user.id,
        f"Rarity Caps:\n"
        f"Legendary: {_fc(leg_cap)} | Mythic: {_fc(myth_cap)}\n"
        f"Prismatic: {_fc(pris_cap)} | Exotic: {_fc(ex_cap)}")
    await _w(bot, user.id,
        f"Recent Top Payout: {top_payout}\n"
        f"/economycaps — all caps | /biggestpayouts — payout log")


# ---------------------------------------------------------------------------
# /economysettings
# ---------------------------------------------------------------------------

async def handle_economysettings(bot, user) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "❌ Manager+ only.")
        return
    norm_cap  = get_multiplier_cap(False)
    bless_cap = get_multiplier_cap(True)
    try:
        scale = float(db.get_mine_setting("weight_value_multiplier_scale", "1.0"))
    except Exception:
        scale = 1.0
    await _w(bot, user.id,
        f"<#66CCFF>⚙️ Economy Settings<#FFFFFF>\n"
        f"Weight Value Scale: {scale}\n"
        f"Normal Mult Cap: {norm_cap}x | Blessing Cap: {bless_cap}x\n"
        f"/setraritycap <rarity> <amount> to adjust caps")


# ---------------------------------------------------------------------------
# /economycap  /economycaps
# ---------------------------------------------------------------------------

async def handle_economycap(bot, user) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "❌ Manager+ only.")
        return
    order = [
        "common", "uncommon", "rare", "epic",
        "legendary", "mythic", "ultra_rare", "prismatic", "exotic",
    ]
    lines = ["<#66CCFF>💰 Rarity Value Caps<#FFFFFF>"]
    for r in order:
        cap   = get_rarity_cap(r)
        label = r.replace("_", " ").title()
        lines.append(f"{label}: {_fc(cap)}")
    await _w(bot, user.id, "\n".join(lines[:5]))
    await _w(bot, user.id, "\n".join(lines[5:]))


# ---------------------------------------------------------------------------
# /setraritycap <rarity> <amount>
# ---------------------------------------------------------------------------

async def handle_setraritycap(bot, user, args: str) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "❌ Manager+ only.")
        return
    parts = args.strip().split()
    if len(parts) < 2:
        await _w(bot, user.id,
            "Usage: !setraritycap <rarity> <amount>\n"
            "Rarities: common rare epic legendary mythic prismatic exotic")
        return
    rarity = parts[0].lower().replace(" ", "_")
    if rarity not in VALID_CAP_RARITIES:
        await _w(bot, user.id,
            f"❌ Unknown rarity: {parts[0]}\n"
            "Valid: common uncommon rare epic legendary mythic ultra_rare prismatic exotic")
        return
    try:
        amount = int(parts[1].replace(",", "").replace("_", ""))
        if amount < 0:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "❌ Amount must be a positive integer.")
        return
    set_rarity_cap(rarity, amount)
    await _w(bot, user.id, f"✅ {rarity.replace('_',' ').title()} cap → {_fc(amount)} coins.")


# ---------------------------------------------------------------------------
# /resetraritycaps
# ---------------------------------------------------------------------------

async def handle_resetraritycaps(bot, user) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "❌ Manager+ only.")
        return
    reset_rarity_caps()
    await _w(bot, user.id, "✅ All rarity caps reset to defaults.")


# ---------------------------------------------------------------------------
# /payoutlogs  /minepayoutlogs
# ---------------------------------------------------------------------------

async def handle_payoutlogs(bot, user, args: str = "") -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "❌ Manager+ only.")
        return
    username = args.strip().lstrip("@") or None
    logs     = db.get_payout_logs(10, username)
    if not logs:
        label = f" for {username}" if username else ""
        await _w(bot, user.id, f"No payout logs found{label}.")
        return
    label = f" ({username})" if username else ""
    lines = [f"<#66CCFF>📋 Recent Payouts{label}<#FFFFFF>"]
    for row in logs:
        cap_tag = " [CAP]" if row["cap_applied"] else ""
        lines.append(
            f"{row['username']} | {row['ore_name']} ({row['rarity']}) "
            f"| {_fc(row['final_value'])}{cap_tag}"
        )
    await _w(bot, user.id, "\n".join(lines[:6]))
    if len(lines) > 6:
        await _w(bot, user.id, "\n".join(lines[6:]))


# ---------------------------------------------------------------------------
# /biggestpayouts
# ---------------------------------------------------------------------------

async def handle_biggestpayouts(bot, user) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "❌ Manager+ only.")
        return
    logs = db.get_biggest_payouts(10)
    if not logs:
        await _w(bot, user.id, "No payout logs recorded yet.")
        return
    lines = ["<#66CCFF>🏆 Biggest Payouts<#FFFFFF>"]
    for i, row in enumerate(logs, 1):
        cap_tag = " ⚡" if row["cap_applied"] else ""
        lines.append(
            f"#{i} {row['username']} | {row['ore_name']} "
            f"| {_fc(row['final_value'])}{cap_tag}"
        )
    await _w(bot, user.id, "\n".join(lines[:6]))
    if len(lines) > 6:
        await _w(bot, user.id, "\n".join(lines[6:]))
