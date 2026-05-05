"""
modules/achievements.py
-----------------------
Achievement system — 25 achievements across 6 categories.

Commands:
  /achievements         — show your unlocked achievements
  /achievements all     — browse all 25 with lock/unlock status
  /claimachievements    — collect rewards for completed achievements

Rules:
  - Each achievement can only be claimed once.
  - Rewards are flat (not affected by badge/title bonuses).
  - Newly unlocked achievements trigger a whisper notification.
"""

from highrise import BaseBot, User
import database as db


# ---------------------------------------------------------------------------
# Achievement catalog
# ---------------------------------------------------------------------------

ACHIEVEMENTS: dict[str, dict] = {
    # ── Beginner ──────────────────────────────────────────────────────────
    "first_win":        {"name": "First Win",          "desc": "Win any mini game once",        "coins": 50,     "xp": 10,    "cat": "Beginner"},
    "getting_started":  {"name": "Getting Started",    "desc": "Claim /daily 3 times",          "coins": 100,    "xp": 25,    "cat": "Beginner"},
    "first_purchase":   {"name": "First Purchase",     "desc": "Buy any shop item",             "coins": 100,    "xp": 25,    "cat": "Beginner"},
    # ── Game ──────────────────────────────────────────────────────────────
    "trivia_rookie":    {"name": "Trivia Rookie",      "desc": "Win 10 trivia games",           "coins": 250,    "xp": 50,    "cat": "Game"},
    "trivia_master":    {"name": "Trivia Master",      "desc": "Win 50 trivia games",           "coins": 1_000,  "xp": 150,   "cat": "Game"},
    "trivia_legend":    {"name": "Trivia Legend",      "desc": "Win 200 trivia games",          "coins": 3_000,  "xp": 500,   "cat": "Game"},
    "word_solver":      {"name": "Word Solver",        "desc": "Win 25 scramble games",         "coins": 500,    "xp": 100,   "cat": "Game"},
    "word_master_ach":  {"name": "Word Master",        "desc": "Win 100 scramble games",        "coins": 2_000,  "xp": 300,   "cat": "Game"},
    "riddle_brain":     {"name": "Riddle Brain",       "desc": "Win 25 riddle games",           "coins": 500,    "xp": 100,   "cat": "Game"},
    "riddle_lord_ach":  {"name": "Riddle Lord",        "desc": "Win 100 riddle games",          "coins": 2_000,  "xp": 300,   "cat": "Game"},
    # ── Daily / Streak ────────────────────────────────────────────────────
    "daily_grinder":    {"name": "Daily Grinder",      "desc": "Reach a 7-day daily streak",    "coins": 500,    "xp": 100,   "cat": "Daily"},
    "loyal_player":     {"name": "Loyal Player",       "desc": "Reach a 30-day daily streak",   "coins": 3_000,  "xp": 500,   "cat": "Daily"},
    "room_addict":      {"name": "Room Addict",        "desc": "Reach a 100-day daily streak",  "coins": 10_000, "xp": 1_500, "cat": "Daily"},
    # ── Economy ───────────────────────────────────────────────────────────
    "coin_collector":   {"name": "Coin Collector",     "desc": "Reach 10,000 coins balance",    "coins": 500,    "xp": 100,   "cat": "Economy"},
    "rich_player":      {"name": "Rich Player",        "desc": "Reach 50,000 coins balance",    "coins": 2_000,  "xp": 300,   "cat": "Economy"},
    "millionaire_mind": {"name": "Millionaire Mindset","desc": "Reach 250,000 coins balance",   "coins": 5_000,  "xp": 1_000, "cat": "Economy"},
    # ── Shop ──────────────────────────────────────────────────────────────
    "shop_collector":   {"name": "Shop Collector",     "desc": "Own 5 shop items",              "coins": 750,    "xp": 150,   "cat": "Shop"},
    "cosmetic_hunter":  {"name": "Cosmetic Hunter",    "desc": "Own 10 shop items",             "coins": 2_000,  "xp": 300,   "cat": "Shop"},
    "badge_collector":  {"name": "Badge Collector",    "desc": "Own 8 badges",                  "coins": 2_500,  "xp": 400,   "cat": "Shop"},
    "title_collector":  {"name": "Title Collector",    "desc": "Own 5 titles",                  "coins": 5_000,  "xp": 750,   "cat": "Shop"},
    # ── Casino ────────────────────────────────────────────────────────────
    "coinflip_rookie":  {"name": "Coinflip Rookie",    "desc": "Win 25 coinflips",              "coins": 500,    "xp": 100,   "cat": "Casino"},
    "coinflip_grinder": {"name": "Coinflip Grinder",   "desc": "Win 100 coinflips",             "coins": 2_000,  "xp": 300,   "cat": "Casino"},
    "coinflip_pro":     {"name": "High Roller",        "desc": "Win 250 coinflips",             "coins": 7_500,  "xp": 1_000, "cat": "Casino"},
    # ── Elite ─────────────────────────────────────────────────────────────
    "elite_status":     {"name": "Elite Status",       "desc": "Buy the [Elite] title",         "coins": 5_000,  "xp": 1_000, "cat": "Elite"},
    "immortal_status":  {"name": "Immortal Status",    "desc": "Buy the [Immortal] title",      "coins": 10_000, "xp": 2_000, "cat": "Elite"},
}

# Context string → which achievement IDs to check
_TRIGGERS: dict[str, set[str]] = {
    "game_win":     {"first_win"},
    "trivia_win":   {"trivia_rookie", "trivia_master", "trivia_legend",
                     "coin_collector", "rich_player", "millionaire_mind"},
    "scramble_win": {"word_solver", "word_master_ach",
                     "coin_collector", "rich_player", "millionaire_mind"},
    "riddle_win":   {"riddle_brain", "riddle_lord_ach",
                     "coin_collector", "rich_player", "millionaire_mind"},
    "coinflip_win": {"coinflip_rookie", "coinflip_grinder", "coinflip_pro",
                     "coin_collector", "rich_player", "millionaire_mind"},
    "daily":        {"getting_started", "daily_grinder", "loyal_player", "room_addict",
                     "coin_collector", "rich_player", "millionaire_mind"},
    "purchase":     {"first_purchase", "shop_collector", "cosmetic_hunter",
                     "badge_collector", "title_collector",
                     "elite_status", "immortal_status",
                     "coin_collector", "rich_player", "millionaire_mind"},
}


# ---------------------------------------------------------------------------
# Requirement checker
# ---------------------------------------------------------------------------

def _is_met(ach_id: str, user_id: str) -> bool:
    """Return True if the player currently satisfies the requirement."""
    if ach_id == "first_win":
        return (db.get_profile(user_id).get("total_games_won") or 0) >= 1
    if ach_id == "getting_started":
        return db.get_daily_stats(user_id)["total_claims"] >= 3
    if ach_id == "first_purchase":
        return len(db.get_owned_items(user_id)) >= 1
    if ach_id == "trivia_rookie":
        return db.get_game_wins(user_id, "trivia") >= 10
    if ach_id == "trivia_master":
        return db.get_game_wins(user_id, "trivia") >= 50
    if ach_id == "trivia_legend":
        return db.get_game_wins(user_id, "trivia") >= 200
    if ach_id == "word_solver":
        return db.get_game_wins(user_id, "scramble") >= 25
    if ach_id == "word_master_ach":
        return db.get_game_wins(user_id, "scramble") >= 100
    if ach_id == "riddle_brain":
        return db.get_game_wins(user_id, "riddle") >= 25
    if ach_id == "riddle_lord_ach":
        return db.get_game_wins(user_id, "riddle") >= 100
    if ach_id == "daily_grinder":
        return db.get_daily_stats(user_id)["streak"] >= 7
    if ach_id == "loyal_player":
        return db.get_daily_stats(user_id)["streak"] >= 30
    if ach_id == "room_addict":
        return db.get_daily_stats(user_id)["streak"] >= 100
    if ach_id == "coin_collector":
        return db.get_balance(user_id) >= 10_000
    if ach_id == "rich_player":
        return db.get_balance(user_id) >= 50_000
    if ach_id == "millionaire_mind":
        return db.get_balance(user_id) >= 250_000
    if ach_id == "shop_collector":
        return db.get_owned_item_counts(user_id)["total"] >= 5
    if ach_id == "cosmetic_hunter":
        return db.get_owned_item_counts(user_id)["total"] >= 10
    if ach_id == "badge_collector":
        return db.get_owned_item_counts(user_id)["badges"] >= 8
    if ach_id == "title_collector":
        return db.get_owned_item_counts(user_id)["titles"] >= 5
    if ach_id == "coinflip_rookie":
        return db.get_game_wins(user_id, "coinflip") >= 25
    if ach_id == "coinflip_grinder":
        return db.get_game_wins(user_id, "coinflip") >= 100
    if ach_id == "coinflip_pro":
        return db.get_game_wins(user_id, "coinflip") >= 250
    if ach_id == "elite_status":
        return db.owns_item(user_id, "elite")
    if ach_id == "immortal_status":
        return db.owns_item(user_id, "immortal")
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def check_achievements(bot, user, context: str) -> None:
    """
    Called after any relevant game event.
    Checks all achievements linked to the context, unlocks newly met ones,
    and whispers the player about new unlocks.
    """
    try:
        to_check = _TRIGGERS.get(context, set())
        already  = set(db.get_unlocked_achievements(user.id))
        newly: list[str] = []

        for ach_id in to_check:
            if ach_id in already:
                continue
            if _is_met(ach_id, user.id):
                if db.unlock_achievement(user.id, ach_id):
                    newly.append(ACHIEVEMENTS[ach_id]["name"])

        if newly:
            await bot.highrise.send_whisper(
                user.id,
                f"🏅 Achievement unlocked: {', '.join(newly)}\n"
                f"Type /claimachievements to collect your reward!"
            )
    except Exception as exc:
        print(f"[ACHIEVEMENTS] check error for {user.username} ctx={context}: {exc}")


def _chunk(lines: list[str], max_chars: int = 310) -> list[list[str]]:
    """Split a list of lines into groups that fit within max_chars."""
    chunks: list[list[str]] = []
    cur: list[str] = []
    cur_len = 0
    for line in lines:
        cost = len(line) + 1
        if cur and cur_len + cost > max_chars:
            chunks.append(cur)
            cur = [line]
            cur_len = cost
        else:
            cur.append(line)
            cur_len += cost
    if cur:
        chunks.append(cur)
    return chunks


async def handle_achievements(bot, user, args: list[str]) -> None:
    """
    /achievements       — your unlocked achievements + claimable count
    /achievements all   — all 25 with lock/unlock status, grouped by category
    """
    try:
        sub       = args[1].lower() if len(args) > 1 else ""
        unlocked  = set(db.get_unlocked_achievements(user.id))
        claimable = set(db.get_claimable_achievements(user.id))

        if sub == "all":
            lines = [f"-- All Achievements ({len(unlocked)}/{len(ACHIEVEMENTS)} unlocked) --"]
            current_cat = None
            for ach_id, ach in ACHIEVEMENTS.items():
                if ach["cat"] != current_cat:
                    current_cat = ach["cat"]
                    lines.append(f"[ {current_cat} ]")
                status  = "✅" if ach_id in unlocked else "🔒"
                reward  = f"{ach['coins']}c+{ach['xp']}XP"
                lines.append(f"  {status} {ach['name']} ({reward}) — {ach['desc']}")
            for chunk in _chunk(lines):
                await bot.highrise.send_whisper(user.id, "\n".join(chunk))

        else:
            if not unlocked:
                await bot.highrise.send_whisper(
                    user.id,
                    f"No achievements yet!\n"
                    f"Play games, claim /daily, and visit the shop.\n"
                    f"Type /achievements all to see all {len(ACHIEVEMENTS)} achievements."
                )
                return

            lines = [f"-- Your Achievements ({len(unlocked)}/{len(ACHIEVEMENTS)}) --"]
            for ach_id in unlocked:
                if ach_id not in ACHIEVEMENTS:
                    continue
                tag = " ← CLAIM" if ach_id in claimable else ""
                lines.append(f"  🏅 {ACHIEVEMENTS[ach_id]['name']}{tag}")

            if claimable:
                lines.append(f"\n{len(claimable)} reward(s) ready! /claimachievements")
            else:
                lines.append("All rewards claimed. Keep playing!")

            for chunk in _chunk(lines):
                await bot.highrise.send_whisper(user.id, "\n".join(chunk))

    except Exception as exc:
        print(f"[ACHIEVEMENTS] handle error for {user.username}: {exc}")
        try:
            await bot.highrise.send_whisper(
                user.id, "Achievements had an error. Please tell the owner."
            )
        except Exception:
            pass


async def handle_claim_achievements(bot, user) -> None:
    """/claimachievements — collect all pending achievement rewards."""
    try:
        claimable = db.get_claimable_achievements(user.id)
        if not claimable:
            await bot.highrise.send_whisper(
                user.id,
                "No achievements ready to claim yet.\n"
                "Type /achievements all to see what's left to unlock."
            )
            return

        total_coins = 0
        total_xp    = 0
        names: list[str] = []

        for ach_id in claimable:
            if ach_id not in ACHIEVEMENTS:
                continue
            if db.claim_achievement(user.id, ach_id):
                ach = ACHIEVEMENTS[ach_id]
                total_coins += ach["coins"]
                total_xp    += ach["xp"]
                names.append(ach["name"])

        if not names:
            await bot.highrise.send_whisper(user.id, "Nothing to claim right now!")
            return

        # Flat reward — bypasses all equipped badge/title bonuses
        db.adjust_balance(user.id, total_coins)
        db.add_xp(user.id, total_xp)

        display   = db.get_display_name(user.id, user.username)
        names_str = ", ".join(names)

        await bot.highrise.send_whisper(
            user.id,
            f"🏆 Claimed {len(names)} achievement(s)!\n"
            f"{names_str}\n"
            f"+{total_coins} coins  +{total_xp} XP"
        )
        await bot.highrise.chat(
            f"🏆 {display} just claimed {len(names)} achievement(s)! ({names_str})"
        )

    except Exception as exc:
        print(f"[ACHIEVEMENTS] claim error for {user.username}: {exc}")
        try:
            await bot.highrise.send_whisper(
                user.id, "Claim had an error. Please tell the owner."
            )
        except Exception:
            pass
