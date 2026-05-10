"""
modules/first_find.py
--------------------
First-find reward system for mining and fishing.

Flow:
  1. Mining/fishing bot detects first drop → check_first_find()
  2. Claim recorded, player whispered by detecting bot
  3. Pending row written to first_find_announce_pending
  4. EmceeBot poller picks it up → public room chat announce
  5. BankerBot poller picks it up → whispers player with gold log + logs status

BankerBot must manually tip gold (SDK does not allow bots to send gold to players).
"""
from __future__ import annotations
import asyncio
from difflib import get_close_matches

import database as db
from modules.permissions import can_manage_economy

_RARITY_ORDER = [
    "common", "uncommon", "rare", "epic",
    "legendary", "mythic", "ultra_rare", "prismatic", "exotic",
]

_CATEGORY_LABELS = {
    "mining":  "Mining",
    "fishing": "Fishing",
}

_RANK_LABELS: dict[int, str] = {1: "1st", 2: "2nd", 3: "3rd"}

_USAGE = (
    "🏆 First Find Setup\n"
    "Usage: /setfirstfind <mining|fishing> <rarity> <players_count> <gold_amount>\n"
    "Example: /setfirstfind mining prismatic 1 5"
)

_SELF_CMDS = frozenset({
    "setfirstfind", "setfirstfindreward",
    "firstfindstatus", "firstfindcheck",
    "resetfirstfind",
})


async def _w(bot, uid: str, msg: str) -> None:
    """Whisper helper — enforces ≤249 char limit, swallows SDK errors."""
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


def _strip_cmd(args: list[str]) -> list[str]:
    """Return args with the leading command name removed if present."""
    if args and args[0].lower() in _SELF_CMDS:
        return args[1:]
    return list(args)


# ---------------------------------------------------------------------------
# Core trigger — called from mining.py / fishing.py after every drop
# ---------------------------------------------------------------------------

async def check_first_find(bot, user_id: str, username: str,
                           category: str, rarity: str) -> None:
    """Check and potentially award a first-find reward. Call after every drop."""
    try:
        reward = db.get_first_find_reward(category, rarity)
        if not reward:
            return
        reward_id = reward["id"]
        count     = db.get_first_find_claim_count(reward_id)
        if count >= reward["players_count"]:
            return
        if db.has_first_find_claimed(reward_id, user_id):
            return

        claim_rank  = count + 1
        gold_amount = reward["gold_amount"]
        status      = "pending_manual_gold" if gold_amount > 0 else "acknowledged"

        db.add_first_find_claim(
            reward_id, user_id, username, category, rarity,
            claim_rank, status
        )

        rar_label  = rarity.replace("_", " ").upper()
        rank_label = _RANK_LABELS.get(claim_rank, f"#{claim_rank}")
        verb       = "catch" if category == "fishing" else "mine"
        find_word  = "First Catch" if category == "fishing" else "First Find"

        emcee_msg = (
            f"🏆 {find_word} Reward!\n"
            f"@{username} is {rank_label} to {verb} [{rar_label}]!\n"
            f"Reward: {gold_amount:g} gold"
        )
        banker_msg = (
            f"💰 First Find Gold Logged\n"
            f"@{username} earned {gold_amount:g} gold.\n"
            f"Status: pending manual tip."
        )

        # Whisper the finder from whoever detected it (miner/fisher bot)
        w_msg = (
            f"🏆 You are {rank_label} to {verb} [{rar_label}]! "
            f"{gold_amount:g} gold reward logged — BankerBot will confirm soon."
        )
        await _w(bot, user_id, w_msg)

        # Queue public EmceeBot announce + BankerBot gold log
        try:
            db.add_first_find_pending(
                reward_id, category, rarity, username, user_id,
                claim_rank, gold_amount, emcee_msg, banker_msg
            )
        except Exception as exc:
            print(f"[FIRSTFIND] pending insert error: {exc}")

    except Exception as exc:
        print(f"[FIRSTFIND] check_first_find error: {exc}")


# ---------------------------------------------------------------------------
# EmceeBot background poller — public room announcement
# ---------------------------------------------------------------------------

async def startup_firstfind_announcer(bot) -> None:
    """Background task: EmceeBot polls first_find_announce_pending and announces."""
    await asyncio.sleep(12)
    while True:
        try:
            rows = db.get_pending_firstfind_for_emcee(limit=3)
            for row in rows:
                try:
                    await bot.highrise.chat(row["emcee_msg"][:249])
                except Exception:
                    pass
                try:
                    db.mark_firstfind_emcee_done(row["id"])
                except Exception:
                    pass
                await asyncio.sleep(2)
        except Exception as exc:
            print(f"[FIRSTFIND] announcer error: {exc}")
        await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# BankerBot background poller — gold log whisper
# ---------------------------------------------------------------------------

async def startup_firstfind_banker(bot) -> None:
    """Background task: BankerBot polls first_find_announce_pending and logs gold."""
    await asyncio.sleep(15)
    while True:
        try:
            rows = db.get_pending_firstfind_for_banker(limit=3)
            for row in rows:
                try:
                    await _w(bot, row["user_id"], row["banker_msg"])
                except Exception:
                    pass
                try:
                    db.mark_firstfind_banker_done(row["id"])
                except Exception:
                    pass
                await asyncio.sleep(2)
        except Exception as exc:
            print(f"[FIRSTFIND] banker poller error: {exc}")
        await asyncio.sleep(7)


# ---------------------------------------------------------------------------
# /setfirstfind  /setfirstfindreward
# ---------------------------------------------------------------------------

async def handle_setfirstfind(bot, user, args: list[str]) -> None:
    """/setfirstfind <mining|fishing> <rarity> <players_count> <gold_amount>"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    # Support both arg styles:
    # Style A: args = ["setfirstfind", "mining", "prismatic", "1", "5"]
    # Style B: args = ["mining", "prismatic", "1", "5"]
    a = _strip_cmd(args)

    if len(a) < 4:
        await _w(bot, user.id, _USAGE[:249])
        return

    category = a[0].lower()
    if category not in ("mining", "fishing"):
        await _w(bot, user.id, "Category must be: mining or fishing")
        return

    rarity = a[1].lower()
    if rarity not in _RARITY_ORDER:
        m = get_close_matches(rarity, _RARITY_ORDER, n=1, cutoff=0.6)
        if m:
            await _w(bot, user.id, f"Did you mean: {m[0]}?")
        else:
            await _w(bot, user.id,
                     f"Valid rarities: {', '.join(_RARITY_ORDER)}"[:249])
        return

    if not a[2].isdigit() or int(a[2]) < 1:
        await _w(bot, user.id, "Players count must be 1 or more.")
        return
    players_count = int(a[2])

    try:
        gold_amount = float(a[3])
        if gold_amount <= 0:
            raise ValueError
    except (ValueError, IndexError):
        await _w(bot, user.id, "Gold amount must be a positive number.")
        return

    db.set_first_find_reward(category, rarity, players_count, gold_amount, 0)

    rar_label  = rarity.replace("_", " ").title()
    winner_txt = f"First {players_count} player{'s' if players_count > 1 else ''}"
    reply = (
        f"✅ First Find Reward Set\n"
        f"Category: {category.title()}\n"
        f"Rarity: {rar_label}\n"
        f"Winners: {winner_txt}\n"
        f"Reward: {gold_amount:g} gold each"
    )
    await _w(bot, user.id, reply[:249])


# ---------------------------------------------------------------------------
# /firstfindrewards  /firstfindlist
# ---------------------------------------------------------------------------

async def handle_firstfindrewards(bot, user) -> None:
    """/firstfindrewards — list all configured first-find rewards."""
    rewards = db.get_all_first_find_rewards()
    if not rewards:
        await _w(bot, user.id,
                 "🏆 First Find Rewards\nNo active first-find rewards set.")
        return
    lines = ["🏆 First Find Rewards"]
    for r in rewards:
        count     = db.get_first_find_claim_count(r["id"])
        enabled   = "✅" if r["enabled"] else "❌"
        rar_label = r["rarity"].replace("_", " ").title()
        reward_txt = (f"{r['gold_amount']:g}g" if r["gold_amount"] > 0
                      else f"{r['coin_fallback_amount']:,}c")
        lines.append(
            f"{enabled} {r['category'].title()} {rar_label}: "
            f"first {r['players_count']} | {reward_txt} each | "
            f"{count}/{r['players_count']} claimed"
        )
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /firstfindstatus [mining|fishing] [rarity]
# ---------------------------------------------------------------------------

async def handle_firstfindstatus(bot, user, args: list[str]) -> None:
    """/firstfindstatus [mining|fishing] [rarity]"""
    a = _strip_cmd(args)

    if len(a) >= 2:
        category  = a[0].lower()
        rarity    = a[1].lower()
        reward    = db.get_first_find_reward(category, rarity)
        if not reward:
            await _w(bot, user.id,
                     f"No first-find reward set for {category} {rarity}.")
            return
        claims    = db.get_first_find_claims(category, rarity)
        rar_label = rarity.replace("_", " ").title()
        count     = len(claims)
        gold_txt  = (f"{reward['gold_amount']:g}g" if reward["gold_amount"] > 0
                     else f"{reward['coin_fallback_amount']:,}c")
        lines = [
            f"🏆 First Find: {category.title()} {rar_label}",
            f"{gold_txt} each | {count}/{reward['players_count']} claimed",
        ]
        for cl in claims:
            rank = _RANK_LABELS.get(cl["claim_rank"], f"#{cl['claim_rank']}")
            lines.append(f"  {rank} @{cl['username']} — {cl['reward_status']}")
        remaining = reward["players_count"] - count
        if remaining > 0:
            lines.append(f"({remaining} spot{'s' if remaining > 1 else ''} remaining)")
        await _w(bot, user.id, "\n".join(lines)[:249])
    else:
        await handle_firstfindrewards(bot, user)


# ---------------------------------------------------------------------------
# /resetfirstfind <mining|fishing> <rarity>
# ---------------------------------------------------------------------------

async def handle_resetfirstfind(bot, user, args: list[str]) -> None:
    """/resetfirstfind <mining|fishing> <rarity>"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    a = _strip_cmd(args)

    if len(a) < 2:
        await _w(bot, user.id,
                 "Usage: /resetfirstfind <mining|fishing> <rarity>")
        return
    category  = a[0].lower()
    rarity    = a[1].lower()
    deleted   = db.reset_first_find(category, rarity)
    rar_label = rarity.replace("_", " ").title()
    await _w(bot, user.id,
             f"♻️ First Find Reset\n{category.title()} {rar_label} claims cleared.")
