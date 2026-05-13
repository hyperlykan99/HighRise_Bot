"""
modules/missions.py — 3.1J Player Retention Loop

Daily/weekly missions, collection milestones, XP level display,
seasonal leaderboard, !today dashboard, admin controls.

Track actions by calling track_mission(user_id, username, action) from
mining, fishing, trivia, and game modules.
"""
from __future__ import annotations

import random
from datetime import date

import database as db
from highrise import BaseBot, User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


def _daily_period() -> str:
    return str(date.today())


def _weekly_period() -> str:
    d = date.today()
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _season_key() -> str:
    return _weekly_period()


def _get_mining_total() -> int:
    try:
        return sum(db.get_mining_totals_by_rarity().values()) or 25
    except Exception:
        return 25


def _get_fishing_total() -> int:
    try:
        from modules.fishing import FISH_ITEMS
        return len(FISH_ITEMS)
    except Exception:
        return 50


def _pct(num: int, den: int) -> int:
    if den == 0:
        return 0
    return int(num * 100 / den)


# ---------------------------------------------------------------------------
# Mission definitions
# ---------------------------------------------------------------------------

DAILY_MISSIONS: list[dict] = [
    {"num": 1, "key": "daily_mine",   "label": "Mine 25",      "target": 25, "coins": 5000,  "icon": "⛏️"},
    {"num": 2, "key": "daily_fish",   "label": "Fish 25",      "target": 25, "coins": 5000,  "icon": "🎣"},
    {"num": 3, "key": "daily_trivia", "label": "Trivia x3",    "target": 3,  "coins": 3000,  "icon": "❓"},
    {"num": 4, "key": "daily_game",   "label": "Games x3",     "target": 3,  "coins": 3000,  "icon": "🎮"},
]

WEEKLY_MISSIONS: list[dict] = [
    {"num": 1, "key": "weekly_mine",    "label": "Mine 500",          "target": 500, "coins": 50000,             "icon": "⛏️"},
    {"num": 2, "key": "weekly_fish",    "label": "Fish 500",          "target": 500, "coins": 50000,             "icon": "🎣"},
    {"num": 3, "key": "weekly_rare3",   "label": "3 Legendary+ finds","target": 3,   "tickets": 50,  "coins": 75000, "icon": "💎"},
    {"num": 4, "key": "weekly_daily5",  "label": "5 daily sets",      "target": 5,   "tickets": 100, "coins": 75000, "icon": "📋"},
    {"num": 5, "key": "weekly_streak7", "label": "7-day streak",      "target": 7,   "chest": True,              "icon": "🔥"},
]

_DAILY_BY_KEY: dict[str, dict] = {m["key"]: m for m in DAILY_MISSIONS}
_WEEKLY_BY_KEY: dict[str, dict] = {m["key"]: m for m in WEEKLY_MISSIONS}

# ---------------------------------------------------------------------------
# Level titles (Part 11)
# ---------------------------------------------------------------------------

_LEVEL_TITLES: dict[int, str] = {
    1: "Newcomer",
    5: "Rookie Explorer",
    10: "Lucky Miner",
    15: "Skilled Angler",
    25: "Treasure Hunter",
    50: "ChillTopia Legend",
    100: "Mythic Collector",
}

def _level_title(level: int) -> str:
    title = "Newcomer"
    for threshold in sorted(_LEVEL_TITLES):
        if level >= threshold:
            title = _LEVEL_TITLES[threshold]
    return title


# ---------------------------------------------------------------------------
# Season categories
# ---------------------------------------------------------------------------

_SEASON_CATS: dict[str, str] = {
    "mining":     "⛏️ Weekly Miners",
    "fishing":    "🎣 Weekly Fishers",
    "collection": "📖 Weekly Collectors",
    "trivia":     "❓ Weekly Trivia",
    "casino":     "🎰 Weekly Casino",
    "tipper":     "💰 Weekly Tippers",
}


# ---------------------------------------------------------------------------
# track_mission — called from mining, fishing, trivia, game modules
# ---------------------------------------------------------------------------

def track_mission(
    user_id: str,
    username: str,
    action: str,
    amount: int = 1,
) -> None:
    """
    action values: "mine", "fish", "trivia", "game", "legendary_find"
    Exceptions are swallowed so this never breaks gameplay.
    """
    try:
        dk = _daily_period()
        wk = _weekly_period()
        sk = _season_key()

        if action == "mine":
            db.increment_mission_progress(user_id, username, "daily_mine",  dk, amount, 25)
            db.increment_mission_progress(user_id, username, "weekly_mine", wk, amount, 500)
            db.add_season_points(user_id, username, sk, "mining", amount)

        elif action == "fish":
            db.increment_mission_progress(user_id, username, "daily_fish",  dk, amount, 25)
            db.increment_mission_progress(user_id, username, "weekly_fish", wk, amount, 500)
            db.add_season_points(user_id, username, sk, "fishing", amount)

        elif action == "trivia":
            db.increment_mission_progress(user_id, username, "daily_trivia", dk, amount, 3)
            db.add_season_points(user_id, username, sk, "trivia", amount)

        elif action == "game":
            db.increment_mission_progress(user_id, username, "daily_game", dk, amount, 3)
            db.add_season_points(user_id, username, sk, "casino", amount)

        elif action == "legendary_find":
            db.increment_mission_progress(user_id, username, "weekly_rare3", wk, amount, 3)
            db.add_season_points(user_id, username, sk, "collection", amount)

    except Exception:
        pass


# ---------------------------------------------------------------------------
# !missions / !dailymissions / !dailygoals  (Part 2)
# ---------------------------------------------------------------------------

async def handle_missions(bot: BaseBot, user: User, args: list) -> None:
    db.ensure_user(user.id, user.username)
    dk   = _daily_period()
    uid  = user.id
    uname = user.username
    lines = [f"📋 Daily Missions [{dk}]"]
    all_done = True
    for m in DAILY_MISSIONS:
        prog    = db.get_mission_progress(uid, m["key"], dk)
        claimed = db.is_mission_claimed(uid, m["key"], dk)
        bar     = f"{min(prog, m['target'])}/{m['target']}"
        tick    = " ✓" if claimed else (" !" if prog >= m["target"] else "")
        rew     = f"{m['coins']:,} 🪙"
        lines.append(f"{m['num']}. {m['icon']} {m['label']}: {bar}{tick} — {rew}")
        if not claimed:
            all_done = False
    chest_txt = "All done: 🎁 Daily Chest!" if all_done else "Complete all: Daily Chest 🎁"
    lines.append(chest_txt)
    lines.append("!claimmission 1-4  !claimdaily")
    await _w(bot, uid, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# !weekly / !weeklymissions / !weeklygoals  (Part 4)
# ---------------------------------------------------------------------------

async def handle_weekly(bot: BaseBot, user: User, args: list) -> None:
    db.ensure_user(user.id, user.username)
    wk   = _weekly_period()
    uid  = user.id
    uname = user.username
    lines = [f"📅 Weekly Goals [{wk}]"]
    all_done = True
    for m in WEEKLY_MISSIONS:
        if m["key"] == "weekly_streak7":
            try:
                stats = db.get_daily_stats(uid)
                prog  = min(stats.get("streak", 0), 7)
            except Exception:
                prog = 0
        else:
            prog = db.get_mission_progress(uid, m["key"], wk)
        claimed = db.is_mission_claimed(uid, m["key"], wk)
        bar     = f"{min(prog, m['target'])}/{m['target']}"
        tick    = " ✓" if claimed else (" !" if prog >= m["target"] else "")
        if "tickets" in m:
            rew = f"{m['tickets']} 🎫"
        elif m.get("chest"):
            rew = "Weekly Chest"
        else:
            rew = f"{m['coins']:,} 🪙"
        lines.append(f"{m['num']}. {m['icon']} {m['label']}: {bar}{tick} — {rew}")
        if not claimed:
            all_done = False
    chest_txt = "All done: 🎁 Weekly Chest!" if all_done else "Complete all: Weekly Chest 🎁"
    lines.append(chest_txt)
    lines.append("!claimweekly")
    await _w(bot, uid, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# !claimmission [number]  (Part 7)
# ---------------------------------------------------------------------------

async def handle_claimmission(bot: BaseBot, user: User, args: list) -> None:
    db.ensure_user(user.id, user.username)
    uid   = user.id
    uname = user.username
    dk    = _daily_period()

    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, uid,
                 "Usage: !claimmission [1-4]\n"
                 "Or: !claimdaily to claim all daily missions.")
        return

    num = int(args[1])
    m   = next((x for x in DAILY_MISSIONS if x["num"] == num), None)
    if not m:
        await _w(bot, uid, f"Mission {num} not found. Valid: 1-{len(DAILY_MISSIONS)}")
        return

    prog = db.get_mission_progress(uid, m["key"], dk)
    if prog < m["target"]:
        await _w(bot, uid,
                 f"⚠️ Mission {num} not complete yet.\n"
                 f"Progress: {prog}/{m['target']}")
        return
    if db.is_mission_claimed(uid, m["key"], dk):
        await _w(bot, uid, "⚠️ Already claimed.")
        return

    db.claim_mission_db(uid, m["key"], dk)
    db.adjust_balance(uid, m["coins"])
    try:
        import modules.leveling as leveling
        from modules.utils import _make_user
        await leveling.award_xp(bot, user, 25, m["coins"], is_game_win=False)
    except Exception:
        pass
    await _w(bot, uid,
             f"✅ Mission {num} claimed!\n"
             f"Reward: {m['coins']:,} 🪙")


# ---------------------------------------------------------------------------
# !claimdaily — claim all completed daily missions + Daily Chest  (Part 7)
# ---------------------------------------------------------------------------

async def handle_claimdaily(bot: BaseBot, user: User, args: list) -> None:
    db.ensure_user(user.id, user.username)
    uid   = user.id
    uname = user.username
    dk    = _daily_period()
    wk    = _weekly_period()

    total_coins = 0
    claimed_n   = 0
    for m in DAILY_MISSIONS:
        prog = db.get_mission_progress(uid, m["key"], dk)
        if prog >= m["target"] and not db.is_mission_claimed(uid, m["key"], dk):
            db.claim_mission_db(uid, m["key"], dk)
            total_coins += m["coins"]
            claimed_n   += 1

    if claimed_n == 0:
        await _w(bot, uid, "⚠️ No completed missions to claim.\nUse !missions to check progress.")
        return

    # Check if ALL 4 daily missions are now claimed → award Daily Chest
    all_claimed = all(db.is_mission_claimed(uid, m["key"], dk) for m in DAILY_MISSIONS)
    chest_awarded = False
    chest_tickets = 0
    if all_claimed and not db.is_set_claimed(uid, "daily", dk):
        db.claim_mission_set_db(uid, uname, "daily", dk)
        total_coins   += 10000
        chest_awarded  = True
        if random.random() < 0.20:
            chest_tickets = random.randint(5, 10)
        # Increment weekly daily-sets counter
        try:
            new_cnt = db.increment_weekly_daily_sets(uid, uname, wk)
            db.increment_mission_progress(uid, uname, "weekly_daily5", wk, 1, 5)
        except Exception:
            pass

    if total_coins > 0:
        db.adjust_balance(uid, total_coins)
    if chest_tickets > 0:
        try:
            db.adjust_luxe_balance(uid, chest_tickets)
        except Exception:
            pass

    try:
        import modules.leveling as leveling
        await leveling.award_xp(bot, user, 25 * claimed_n, total_coins, is_game_win=False)
    except Exception:
        pass

    msg = f"✅ Claimed {claimed_n} mission(s): +{total_coins:,} 🪙"
    if chest_awarded:
        msg = f"🎁 Daily Chest!\nClaimed {claimed_n} missions + chest"
        extras = f"\n+10,000 🪙"
        if chest_tickets:
            extras += f" +{chest_tickets} 🎫"
        msg += extras
    await _w(bot, uid, msg[:249])


# ---------------------------------------------------------------------------
# !claimweekly — claim weekly missions + Weekly Chest  (Part 7)
# ---------------------------------------------------------------------------

async def handle_claimweekly(bot: BaseBot, user: User, args: list) -> None:
    db.ensure_user(user.id, user.username)
    uid   = user.id
    uname = user.username
    wk    = _weekly_period()

    total_coins   = 0
    total_tickets = 0
    claimed_n     = 0

    for m in WEEKLY_MISSIONS:
        if m["key"] == "weekly_streak7":
            try:
                stats = db.get_daily_stats(uid)
                prog  = stats.get("streak", 0)
            except Exception:
                prog = 0
        else:
            prog = db.get_mission_progress(uid, m["key"], wk)

        if prog >= m["target"] and not db.is_mission_claimed(uid, m["key"], wk):
            db.claim_mission_db(uid, m["key"], wk)
            claimed_n += 1
            if m.get("chest"):
                total_coins += 100000
                if random.random() < 0.30:
                    total_tickets += random.randint(25, 100)
            elif "tickets" in m:
                luxe_ok = False
                try:
                    db.adjust_luxe_balance(uid, m["tickets"])
                    total_tickets += m["tickets"]
                    luxe_ok = True
                except Exception:
                    pass
                if not luxe_ok:
                    total_coins += m.get("coins", 75000)
            else:
                total_coins += m.get("coins", 50000)

    if claimed_n == 0:
        await _w(bot, uid,
                 "⚠️ No completed weekly goals to claim.\n"
                 "Use !weekly to check progress.")
        return

    if total_coins > 0:
        db.adjust_balance(uid, total_coins)
    if total_tickets > 0:
        try:
            db.adjust_luxe_balance(uid, total_tickets)
        except Exception:
            pass

    try:
        import modules.leveling as leveling
        await leveling.award_xp(bot, user, 100 * claimed_n, total_coins, is_game_win=False)
    except Exception:
        pass

    parts = []
    if total_coins:
        parts.append(f"+{total_coins:,} 🪙")
    if total_tickets:
        parts.append(f"+{total_tickets} 🎫")
    reward_str = " ".join(parts) if parts else "Rewards issued."
    await _w(bot, uid,
             f"🎁 Weekly Goals claimed! ({claimed_n})\n{reward_str}"[:249])


# ---------------------------------------------------------------------------
# !milestones / !collectionrewards  (Part 9)
# ---------------------------------------------------------------------------

_MILESTONES = [25, 50, 75, 100]

_MILESTONE_REWARDS: dict[int, dict] = {
    25:  {"coins": 10000},
    50:  {"tickets": 25},
    75:  {"coins": 10000, "note": "Lucky Hour Boost (coins)"},
    100: {"tickets": 100, "title": "Collector"},
}

_MILESTONE_REWARDS_FISH: dict[int, dict] = {
    25:  {"coins": 10000},
    50:  {"tickets": 25},
    75:  {"coins": 10000, "note": "Lucky Hour Boost (coins)"},
    100: {"tickets": 100, "title": "Angler"},
}


async def handle_milestones(bot: BaseBot, user: User, args: list) -> None:
    db.ensure_user(user.id, user.username)
    uid    = user.id
    counts = db.get_collection_counts(uid)
    mine_d = counts.get("mining",  0)
    fish_d = counts.get("fishing", 0)
    mine_t = _get_mining_total()
    fish_t = _get_fishing_total()
    mine_p = _pct(mine_d, mine_t)
    fish_p = _pct(fish_d, fish_t)

    lines = ["📖 Collection Milestones"]
    lines.append(f"⛏️ Ore: {mine_p}% ({mine_d}/{mine_t})")
    for ms in _MILESTONES:
        claimed = db.is_milestone_claimed(uid, "mining", ms)
        st = "✓" if claimed else ("!" if mine_p >= ms else "·")
        lines.append(f"  {st} {ms}%")
    lines.append(f"🎣 Fish: {fish_p}% ({fish_d}/{fish_t})")
    for ms in _MILESTONES:
        claimed = db.is_milestone_claimed(uid, "fishing", ms)
        st = "✓" if claimed else ("!" if fish_p >= ms else "·")
        lines.append(f"  {st} {ms}%")
    lines.append("!claimmilestone ore 25  !claimmilestone fish 50")
    await _w(bot, uid, "\n".join(lines)[:249])


async def handle_claimmilestone(bot: BaseBot, user: User, args: list) -> None:
    db.ensure_user(user.id, user.username)
    uid   = user.id
    uname = user.username

    if len(args) < 3:
        await _w(bot, uid, "Usage: !claimmilestone ore|fish 25|50|75|100")
        return

    ctype = args[1].lower()
    if ctype in ("ore", "mining"):
        ctype = "mining"
    elif ctype in ("fish", "fishing"):
        ctype = "fishing"
    else:
        await _w(bot, uid, "Type must be: ore or fish")
        return

    if not args[2].isdigit():
        await _w(bot, uid, "Milestone must be: 25, 50, 75, or 100")
        return

    ms = int(args[2])
    if ms not in _MILESTONES:
        await _w(bot, uid, "Valid milestones: 25, 50, 75, 100")
        return

    if db.is_milestone_claimed(uid, ctype, ms):
        await _w(bot, uid, f"⚠️ {ms}% {ctype} milestone already claimed.")
        return

    counts = db.get_collection_counts(uid)
    disc   = counts.get(ctype, 0)
    total  = _get_mining_total() if ctype == "mining" else _get_fishing_total()
    pct    = _pct(disc, total)

    if pct < ms:
        await _w(bot, uid,
                 f"⚠️ Not reached yet.\n"
                 f"{ctype.title()}: {pct}% / {ms}% needed")
        return

    db.record_milestone_claim(uid, uname, ctype, ms)
    reward_table = _MILESTONE_REWARDS if ctype == "mining" else _MILESTONE_REWARDS_FISH
    rew = reward_table.get(ms, {"coins": 10000})

    coins   = rew.get("coins", 0)
    tickets = rew.get("tickets", 0)

    if coins:
        db.adjust_balance(uid, coins)
    if tickets:
        try:
            db.adjust_luxe_balance(uid, tickets)
        except Exception:
            coins += tickets * 100
            tickets = 0
            db.adjust_balance(uid, tickets * 100)

    parts = []
    if coins:
        parts.append(f"+{coins:,} 🪙")
    if tickets:
        parts.append(f"+{tickets} 🎫")
    reward_str = " | ".join(parts) if parts else "Reward issued."
    icon = "⛏️" if ctype == "mining" else "🎣"
    await _w(bot, uid,
             f"🏆 {icon} {ms}% Milestone claimed!\n{reward_str}"[:249])


# ---------------------------------------------------------------------------
# !level / !xp / !rank  (Part 10)
# ---------------------------------------------------------------------------

async def handle_level(bot: BaseBot, user: User, args: list) -> None:
    db.ensure_user(user.id, user.username)
    uid  = user.id
    try:
        row = db.get_player_xp_info(uid)
    except Exception:
        await _w(bot, uid, "⚠️ Could not load level info.")
        return

    level   = row.get("level", 1)
    total   = row.get("total_xp", 0)
    xp_for  = level * 100
    title   = _level_title(level)
    display = db.get_display_name(uid, user.username)
    await _w(bot, uid,
             f"⭐ Level\n"
             f"@{display}\n"
             f"Level: {level} | XP: {total:,}/{xp_for:,}\n"
             f"Title: {title}"[:249])


# ---------------------------------------------------------------------------
# !season  (Part 12)
# ---------------------------------------------------------------------------

async def handle_season(bot: BaseBot, user: User, args: list) -> None:
    sk    = _season_key()
    lines = [
        f"🏆 Current Season [{sk}]",
        "Categories:",
        "!topseason mining  !topseason fishing",
        "!topseason collection  !topseason trivia",
        "!topseason casino  !topseason tipper",
        "Resets weekly. !seasonrewards",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# !topseason [category]
# ---------------------------------------------------------------------------

async def handle_topseason(bot: BaseBot, user: User, args: list) -> None:
    sk  = _season_key()
    cat = args[1].lower() if len(args) > 1 else ""
    if not cat or cat not in _SEASON_CATS:
        cats = " | ".join(_SEASON_CATS.keys())
        await _w(bot, user.id, f"⚠️ Valid categories: {cats}")
        return

    header = _SEASON_CATS[cat]
    rows   = db.get_season_leaderboard(sk, cat, limit=5)
    if not rows:
        await _w(bot, user.id, f"{header}\nNo data yet this season.")
        return

    medals = ["🥇", "🥈", "🥉", "4.", "5."]
    lines  = [f"{header} [{sk}]"]
    for i, r in enumerate(rows):
        lines.append(f"{medals[i]} @{r['username']} — {r['points']:,}")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# !seasonrewards
# ---------------------------------------------------------------------------

async def handle_seasonrewards(bot: BaseBot, user: User, args: list) -> None:
    await _w(bot, user.id,
             "🏆 Season Rewards\n"
             "Season winners are recognized weekly.\n"
             "Top player per category gets a shout-out.\n"
             "Rewards are manual — admin announces.\n"
             "Use !topseason [category] to see standings.")


# ---------------------------------------------------------------------------
# !today / !progress  (Part 14)
# ---------------------------------------------------------------------------

async def handle_today(bot: BaseBot, user: User, args: list) -> None:
    db.ensure_user(user.id, user.username)
    uid  = user.id
    dk   = _daily_period()
    wk   = _weekly_period()

    # Daily missions progress
    d_done = sum(
        1 for m in DAILY_MISSIONS
        if db.get_mission_progress(uid, m["key"], dk) >= m["target"]
    )

    # Weekly missions progress
    w_done = 0
    for m in WEEKLY_MISSIONS:
        if m["key"] == "weekly_streak7":
            try:
                stats = db.get_daily_stats(uid)
                prog  = stats.get("streak", 0)
            except Exception:
                prog = 0
        else:
            prog = db.get_mission_progress(uid, m["key"], wk)
        if prog >= m["target"]:
            w_done += 1

    # Streak
    try:
        stats  = db.get_daily_stats(uid)
        streak = stats.get("streak", 0)
    except Exception:
        streak = 0

    # Level
    try:
        row   = db.get_player_xp_info(uid)
        level = row.get("level", 1)
    except Exception:
        level = 1

    # Collection %
    try:
        counts  = db.get_collection_counts(uid)
        mine_d  = counts.get("mining",  0)
        fish_d  = counts.get("fishing", 0)
        mine_t  = _get_mining_total()
        fish_t  = _get_fishing_total()
        total_d = mine_d + fish_d
        total_t = mine_t + fish_t
        col_pct = _pct(total_d, total_t)
    except Exception:
        col_pct = 0

    await _w(bot, uid,
             f"📌 Today\n"
             f"Daily: {d_done}/{len(DAILY_MISSIONS)} missions\n"
             f"Weekly: {w_done}/{len(WEEKLY_MISSIONS)} goals\n"
             f"Streak: Day {streak}\n"
             f"Level: {level}\n"
             f"Collection: {col_pct}%"[:249])


# ---------------------------------------------------------------------------
# !missionadmin  (Part 15)
# ---------------------------------------------------------------------------

async def handle_missionadmin(bot: BaseBot, user: User, args: list) -> None:
    try:
        from modules.permissions import is_admin, is_owner
        if not (is_admin(user.username) or is_owner(user.username)):
            await _w(bot, user.id, "Admin only.")
            return
    except Exception:
        pass

    sub = args[1].lower() if len(args) > 1 else "settings"

    if sub == "settings":
        lines = [
            "⚙️ Mission Admin",
            "Missions: enabled (always on)",
            f"Daily missions: {len(DAILY_MISSIONS)}",
            f"Weekly missions: {len(WEEKLY_MISSIONS)}",
            "!missionadmin resetdaily <uid>",
            "!missionadmin resetweekly <uid>",
            "!missionadmin stats",
        ]
        await _w(bot, user.id, "\n".join(lines)[:249])

    elif sub == "stats":
        dk = _daily_period()
        wk = _weekly_period()
        try:
            d_active = db.count_active_missions("daily",  dk)
            w_active = db.count_active_missions("weekly", wk)
        except Exception:
            d_active = w_active = 0
        await _w(bot, user.id,
                 f"📊 Mission Stats\n"
                 f"Daily period: {dk}\n"
                 f"Weekly period: {wk}\n"
                 f"Daily active: {d_active}\n"
                 f"Weekly active: {w_active}")

    elif sub == "resetdaily" and len(args) >= 3:
        target_id = args[2]
        dk = _daily_period()
        try:
            db.reset_missions_for_user(target_id, dk)
            await _w(bot, user.id, f"✅ Daily missions reset for {target_id}.")
        except Exception as e:
            await _w(bot, user.id, f"Error: {e}")

    elif sub == "resetweekly" and len(args) >= 3:
        target_id = args[2]
        wk = _weekly_period()
        try:
            db.reset_missions_for_user(target_id, wk)
            await _w(bot, user.id, f"✅ Weekly missions reset for {target_id}.")
        except Exception as e:
            await _w(bot, user.id, f"Error: {e}")

    else:
        await _w(bot, user.id,
                 "!missionadmin settings|stats\n"
                 "!missionadmin resetdaily <uid>\n"
                 "!missionadmin resetweekly <uid>")


# ---------------------------------------------------------------------------
# !retentionadmin  (Part 15)
# ---------------------------------------------------------------------------

async def handle_retentionadmin(bot: BaseBot, user: User, args: list) -> None:
    try:
        from modules.permissions import is_admin, is_owner
        if not (is_admin(user.username) or is_owner(user.username)):
            await _w(bot, user.id, "Admin only.")
            return
    except Exception:
        pass

    sub = args[1].lower() if len(args) > 1 else "settings"
    sk  = _season_key()

    if sub == "settings":
        await _w(bot, user.id,
                 f"⚙️ Retention Admin\n"
                 f"Season: {sk}\n"
                 f"Daily missions: {len(DAILY_MISSIONS)}\n"
                 f"Weekly missions: {len(WEEKLY_MISSIONS)}\n"
                 f"Milestones: 25/50/75/100%%\n"
                 f"!retentionadmin stats|resetseason|eventstatus")

    elif sub == "stats":
        dk = _daily_period()
        try:
            d_active = db.count_active_missions("daily",  dk)
        except Exception:
            d_active = 0
        lines = [
            f"📊 Retention Stats",
            f"Season: {sk}",
            f"Daily active players: {d_active}",
        ]
        try:
            top = db.get_season_leaderboard(sk, "mining", limit=3)
            if top:
                lines.append("Top miners: " + ", ".join(f"@{r['username']}" for r in top))
        except Exception:
            pass
        await _w(bot, user.id, "\n".join(lines)[:249])

    elif sub == "resetseason":
        try:
            from modules.permissions import is_owner
            if not is_owner(user.username):
                await _w(bot, user.id, "Owner only.")
                return
        except Exception:
            pass
        await _w(bot, user.id,
                 "ℹ️ Season resets automatically each week.\n"
                 "Points accumulate in season_points table.\n"
                 "Use !topseason to view current standings.")

    elif sub == "eventstatus":
        try:
            ev = db.get_active_event()
            if ev:
                await _w(bot, user.id,
                         f"🎉 Active Event: {ev.get('event_name','?')}\n"
                         f"Type: {ev.get('event_type','?')}")
            else:
                await _w(bot, user.id, "No active event.")
        except Exception:
            await _w(bot, user.id, "No event data available.")

    else:
        await _w(bot, user.id,
                 "!retentionadmin settings|stats\n"
                 "!retentionadmin resetseason\n"
                 "!retentionadmin eventstatus")
