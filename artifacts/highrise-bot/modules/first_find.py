"""
modules/first_find.py
--------------------
First-find reward system for mining and fishing.
Tracks who finds each rarity first and queues rewards.
BankingBot sends gold tips; other bots credit coin fallbacks.
"""

import database as db
from modules.permissions import can_manage_economy

_RARITY_ORDER = [
    "common", "uncommon", "rare", "epic",
    "legendary", "mythic", "ultra_rare", "prismatic", "exotic",
]

_CATEGORY_LABELS = {
    "mining":  "⛏️ Mining",
    "fishing": "🎣 Fishing",
}


async def check_first_find(bot, user_id: str, username: str,
                           category: str, rarity: str) -> None:
    """Check and potentially award a first-find reward. Call after every rare+ drop."""
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

        claim_rank    = count + 1
        gold_amount   = reward["gold_amount"]
        coin_fallback = reward["coin_fallback_amount"]

        if gold_amount > 0:
            reward_status = "pending_gold_tip"
        elif coin_fallback > 0:
            reward_status = "coin_awarded"
        else:
            reward_status = "acknowledged"

        db.add_first_find_claim(
            reward_id, user_id, username, category, rarity,
            claim_rank, reward_status
        )

        if coin_fallback > 0:
            db.adjust_balance(user_id, coin_fallback)

        cat_label = _CATEGORY_LABELS.get(category, category.capitalize())
        rar_label = rarity.replace("_", " ").title()

        if claim_rank == 1:
            rank_txt = "FIRST"
        elif claim_rank == 2:
            rank_txt = "2nd"
        elif claim_rank == 3:
            rank_txt = "3rd"
        else:
            rank_txt = f"{claim_rank}th"

        parts = [f"🏆 First Find! @{username} is {rank_txt} to find {cat_label} {rar_label}!"]
        if coin_fallback > 0:
            parts.append(f"+{coin_fallback:,}c reward!")
        elif gold_amount > 0:
            parts.append(f"BankingBot will tip {gold_amount}g!")

        try:
            await bot.highrise.chat(" ".join(parts)[:249])
        except Exception:
            pass

        try:
            w_parts = [f"🏆 You are {rank_txt} to find {rar_label}!"]
            if coin_fallback > 0:
                w_parts.append(f"+{coin_fallback:,}c credited!")
            elif gold_amount > 0:
                w_parts.append(f"BankingBot will tip you {gold_amount}g soon!")
            await bot.highrise.send_whisper(user_id, " ".join(w_parts)[:249])
        except Exception:
            pass

    except Exception as exc:
        print(f"[FIRSTFIND] check_first_find error: {exc}")


async def handle_firstfindrewards(bot, user) -> None:
    """/firstfindrewards — list all configured first-find rewards."""
    rewards = db.get_all_first_find_rewards()
    if not rewards:
        await bot.highrise.send_whisper(user.id,
            "🏆 No first-find rewards set. Use /setfirstfind.")
        return
    lines = ["🏆 First-Find Rewards"]
    for r in rewards:
        count      = db.get_first_find_claim_count(r["id"])
        enabled    = "✅" if r["enabled"] else "❌"
        rar_label  = r["rarity"].replace("_", " ").title()
        reward_txt = (f"{r['gold_amount']}g" if r["gold_amount"] > 0
                      else f"{r['coin_fallback_amount']:,}c")
        lines.append(
            f"{enabled} {r['category'].capitalize()} {rar_label}: "
            f"{reward_txt} ×{r['players_count']} ({count}/{r['players_count']} claimed)"
        )
    await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])


async def handle_setfirstfind(bot, user, args: list[str]) -> None:
    """/setfirstfind <mining|fishing> <rarity> <players> <gold|coins> <amount>"""
    if not can_manage_economy(user.username):
        await bot.highrise.send_whisper(user.id, "Manager/admin/owner only.")
        return
    if len(args) < 6:
        await bot.highrise.send_whisper(user.id,
            "Usage: /setfirstfind <mining|fishing> <rarity> <players> <gold|coins> <amount>")
        return
    category = args[1].lower()
    if category not in ("mining", "fishing"):
        await bot.highrise.send_whisper(user.id, "Category: mining or fishing")
        return
    rarity = args[2].lower()
    if rarity not in _RARITY_ORDER:
        await bot.highrise.send_whisper(user.id,
            f"Rarities: {', '.join(_RARITY_ORDER)}")
        return
    if not args[3].isdigit() or int(args[3]) < 1:
        await bot.highrise.send_whisper(user.id, "Players count must be >= 1.")
        return
    players_count = int(args[3])
    currency = args[4].lower()
    if currency not in ("gold", "coins"):
        await bot.highrise.send_whisper(user.id, "Currency: gold or coins")
        return
    if not args[5].isdigit():
        await bot.highrise.send_whisper(user.id, "Amount must be a number.")
        return
    amount    = int(args[5])
    rar_label = rarity.replace("_", " ").title()
    if currency == "gold":
        db.set_first_find_reward(category, rarity, players_count, float(amount), 0)
    else:
        db.set_first_find_reward(category, rarity, players_count, 0.0, amount)
    await bot.highrise.send_whisper(user.id,
        f"✅ First-find: {category} {rar_label} → {amount} {currency} "
        f"for top {players_count} player(s).")


async def handle_firstfindstatus(bot, user, args: list[str]) -> None:
    """/firstfindstatus [mining|fishing] [rarity]"""
    if len(args) >= 3:
        category  = args[1].lower()
        rarity    = args[2].lower()
        reward    = db.get_first_find_reward(category, rarity)
        if not reward:
            await bot.highrise.send_whisper(user.id,
                f"No first-find reward: {category} {rarity}.")
            return
        claims    = db.get_first_find_claims(category, rarity)
        rar_label = rarity.replace("_", " ").title()
        lines     = [f"🏆 First Find: {category.capitalize()} {rar_label}"]
        for cl in claims:
            lines.append(f"#{cl['claim_rank']} @{cl['username']} — {cl['reward_status']}")
        remaining = reward["players_count"] - len(claims)
        if remaining > 0:
            lines.append(f"({remaining} spot(s) remaining)")
        await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])
    else:
        await handle_firstfindrewards(bot, user)


async def handle_resetfirstfind(bot, user, args: list[str]) -> None:
    """/resetfirstfind <mining|fishing> <rarity>"""
    if not can_manage_economy(user.username):
        await bot.highrise.send_whisper(user.id, "Manager/admin/owner only.")
        return
    if len(args) < 3:
        await bot.highrise.send_whisper(user.id,
            "Usage: /resetfirstfind <mining|fishing> <rarity>")
        return
    category  = args[1].lower()
    rarity    = args[2].lower()
    deleted   = db.reset_first_find(category, rarity)
    rar_label = rarity.replace("_", " ").title()
    await bot.highrise.send_whisper(user.id,
        f"✅ Reset {category} {rar_label} first-find. {deleted} claim(s) cleared.")
