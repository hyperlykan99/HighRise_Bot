"""
modules/first_find.py
--------------------
First-find reward system for mining and fishing.

Flow:
  1. Mining/fishing bot detects first drop → check_first_find()
  2. Claim recorded (status=pending_manual_gold), player whispered by detecting bot
  3. Pending row written to first_find_announce_pending
  4. EmceeBot poller picks it up → public room chat announce
  5. BankerBot poller picks it up → calls process_gold_tip_reward()
       • If SDK tip succeeds  → status updated to paid_gold, player whispered
       • If SDK tip fails     → status stays pending_manual_gold, player whispered
       • /firstfindpending and /paypendingfirstfind let staff view/retry

SDK note:
  bot.highrise.tip_user() IS supported.  BankerBot must hold sufficient gold.
  If the player has left the room before the poller fires, the tip may fail
  and the claim stays pending_manual_gold until /paypendingfirstfind is used.
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

_CATEGORY_LABELS = {"mining": "Mining", "fishing": "Fishing"}
_RANK_LABELS: dict[int, str] = {1: "1st", 2: "2nd", 3: "3rd"}

_USAGE = (
    "🏆 First Find Setup\n"
    "Usage: /setfirstfind <mining|fishing> <rarity> <players_count> <gold_amount>\n"
    "Example: /setfirstfind mining prismatic 1 5"
)

_SELF_CMDS = frozenset({
    "setfirstfind", "setfirstfindreward",
    "firstfindstatus", "firstfindcheck",
    "resetfirstfind", "firstfindpending", "paypendingfirstfind",
    "retryfirstfind",
})


async def _w(bot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


def _strip_cmd(args: list[str]) -> list[str]:
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

        db.add_first_find_claim(
            reward_id, user_id, username, category, rarity,
            claim_rank, "pending_manual_gold"
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

        # Whisper the finder immediately from whichever bot detected the drop
        w_msg = (
            f"🏆 You are {rank_label} to {verb} [{rar_label}]! "
            f"{gold_amount:g} gold reward — BankerBot is processing..."
        )
        await _w(bot, user_id, w_msg)

        # Queue cross-bot pending row for EmceeBot (announce) + BankerBot (pay)
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
# BankerBot background poller — attempts actual gold payout via tip_user
# ---------------------------------------------------------------------------

async def startup_firstfind_banker(bot) -> None:
    """
    Background task: BankerBot polls first_find_announce_pending and pays gold.

    Uses process_gold_tip_reward() — the same SDK path as /goldtip.
    On success  → updates claim to paid_gold, whispers player.
    On failure  → keeps pending_manual_gold, whispers player, staff can /paypendingfirstfind.
    """
    from modules.gold import process_gold_tip_reward  # lazy import avoids circular dep
    await asyncio.sleep(15)
    while True:
        try:
            rows = db.get_pending_firstfind_for_banker(limit=3)
            for row in rows:
                user_id     = row["user_id"]
                username    = row["username"]
                gold_amount = int(row["gold_amount"])
                reward_id   = row["reward_id"]

                if gold_amount > 0:
                    status, err = await process_gold_tip_reward(
                        bot, user_id, username, gold_amount,
                        reason="First Find reward",
                        source="first_find",
                        created_by="BankerBot",
                    )
                else:
                    status, err = "acknowledged", ""

                # Update claim payout status in DB
                try:
                    db.update_first_find_claim_payout_status(reward_id, user_id, status)
                except Exception:
                    pass

                # Whisper the player with the result
                if status == "paid_gold":
                    msg = f"💰 First Find Gold Sent\n@{username} received {gold_amount:g} gold."
                elif status == "acknowledged":
                    msg = f"🏆 First Find confirmed! Reward noted."
                else:
                    msg = (
                        f"💰 First Find Gold Logged\n"
                        f"@{username} earned {gold_amount:g} gold.\n"
                        f"Status: pending manual tip."
                    )
                    if err:
                        msg = (msg + f"\nReason: {err[:60]}")[:249]

                await _w(bot, user_id, msg)

                # Mark banker done regardless — staff uses /paypendingfirstfind for retries
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
# /firstfindpending — show claims still pending manual gold payout
# ---------------------------------------------------------------------------

async def handle_firstfindpending(bot, user) -> None:
    """/firstfindpending — show all pending manual first-find gold rewards (manager+)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return
    rows = db.get_first_find_pending_manual()
    if not rows:
        await _w(bot, user.id, "🏆 No pending first-find gold rewards.")
        return
    lines = ["💰 Pending First Find Tips"]
    for r in rows:
        rar_label = r["rarity"].replace("_", " ").title()
        lines.append(
            f"@{r['username']} — {r['gold_amount']:g}g "
            f"— {r['category'].title()} {rar_label}"
        )
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /paypendingfirstfind [username] — retry gold payout for pending claims
# ---------------------------------------------------------------------------

async def handle_paypendingfirstfind(bot, user, args: list[str]) -> None:
    """/paypendingfirstfind [username] — attempt to pay pending first-find rewards (admin+)."""
    from modules.gold import process_gold_tip_reward  # lazy import

    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    a = _strip_cmd(args)
    target_filter = a[0].lower().lstrip("@") if a else None

    rows = db.get_first_find_pending_manual()
    if target_filter:
        rows = [r for r in rows if r["username"].lower() == target_filter]

    if not rows:
        label = f" for @{target_filter}" if target_filter else ""
        await _w(bot, user.id, f"No pending first-find rewards{label}.")
        return

    paid = failed = 0
    for r in rows:
        gold_amount = int(r["gold_amount"])
        if gold_amount < 1:
            continue
        status, err = await process_gold_tip_reward(
            bot,
            r["user_id"], r["username"], gold_amount,
            reason="First Find reward (manual retry)",
            source="first_find_manual",
            created_by=user.username,
        )
        try:
            db.update_first_find_claim_payout_status(r["reward_id"], r["user_id"], status)
        except Exception:
            pass
        if status == "paid_gold":
            paid += 1
            await _w(bot, r["user_id"],
                     f"💰 First Find Gold Sent\n@{r['username']} received {gold_amount:g} gold.")
        else:
            failed += 1

    reply = f"💰 First Find Pay: {paid} sent, {failed} still pending."
    await _w(bot, user.id, reply[:249])


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
