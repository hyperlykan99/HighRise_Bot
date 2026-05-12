"""
modules/quests.py
-----------------
Daily and weekly quest system.

Daily quests reset every calendar day (UTC).
Weekly quests reset every ISO week (Monday–Sunday).

Commands:
  /questhelp
  /quests          — short overview
  /dailyquests     — daily progress
  /weeklyquests    — weekly progress
  /claimquest      — claim all completed quests
  /claimquest <id> — claim one quest

Hook:
  Call track_quest(user_id, action, amount=1) from game/economy modules.

Action keys → quest mapping:
  "game_win"    — daily_play_3, weekly_win_25
  "daily_claim" — daily_claim
  "coinflip"    — daily_coinflip_3
  "bj_round"    — daily_bj_2, weekly_bj_10
  "answer"      — daily_answer_5
  "earn_coins"  — weekly_earn_5000  (pass amount=coins_earned)
  "bank_send"   — weekly_bank_3
  "shop_buy"    — weekly_shop_1
"""

from datetime import date
from highrise import BaseBot, User

import database as db
import modules.leveling as leveling


# ---------------------------------------------------------------------------
# Quest definitions
# ---------------------------------------------------------------------------

DAILY_QUESTS: list[dict] = [
    {"id": "daily_play_3",     "label": "games",    "desc": "Win 3 mini games",      "target": 3,    "coins": 100, "xp": 25},
    {"id": "daily_claim",      "label": "daily",    "desc": "Claim /daily",           "target": 1,    "coins": 50,  "xp": 10},
    {"id": "daily_coinflip_3", "label": "coinflip", "desc": "Play coinflip 3x",       "target": 3,    "coins": 75,  "xp": 15},
    {"id": "daily_bj_2",       "label": "BJ",       "desc": "Play 2 BJ rounds",       "target": 2,    "coins": 100, "xp": 25},
    {"id": "daily_answer_5",   "label": "ans",      "desc": "Submit 5 /answer tries", "target": 5,    "coins": 75,  "xp": 15},
]

WEEKLY_QUESTS: list[dict] = [
    {"id": "weekly_win_25",    "label": "wins",  "desc": "Win 25 mini games",       "target": 25,   "coins": 1000, "xp": 200},
    {"id": "weekly_earn_5000", "label": "earn",  "desc": "Earn 5000 coins",         "target": 5000, "coins": 750,  "xp": 150},
    {"id": "weekly_bj_10",     "label": "BJ",    "desc": "Play 10 BJ rounds",       "target": 10,   "coins": 1000, "xp": 200},
    {"id": "weekly_bank_3",    "label": "bank",  "desc": "Send coins 3 times",      "target": 3,    "coins": 500,  "xp": 100},
    {"id": "weekly_shop_1",    "label": "shop",  "desc": "Buy 1 shop item",         "target": 1,    "coins": 750,  "xp": 150},
]

_ALL_QUESTS: dict[str, dict] = {q["id"]: q for q in DAILY_QUESTS + WEEKLY_QUESTS}

_DAILY_MAP: dict[str, list[str]] = {
    "game_win":    ["daily_play_3"],
    "daily_claim": ["daily_claim"],
    "coinflip":    ["daily_coinflip_3"],
    "bj_round":    ["daily_bj_2"],
    "answer":      ["daily_answer_5"],
}

_WEEKLY_MAP: dict[str, list[str]] = {
    "game_win":    ["weekly_win_25"],
    "earn_coins":  ["weekly_earn_5000"],
    "bj_round":    ["weekly_bj_10"],
    "bank_send":   ["weekly_bank_3"],
    "shop_buy":    ["weekly_shop_1"],
}


# ---------------------------------------------------------------------------
# Period keys
# ---------------------------------------------------------------------------

def get_daily_period() -> str:
    return str(date.today())


def get_weekly_period() -> str:
    d   = date.today()
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


# ---------------------------------------------------------------------------
# Progress tracker  (called from game/economy modules — never raises)
# ---------------------------------------------------------------------------

def track_quest(user_id: str, action: str, amount: int = 1) -> None:
    """
    Increment quest progress for all quests mapped to `action`.
    Safe to call anywhere — exceptions are swallowed so quests never
    break gameplay.
    """
    try:
        dk = get_daily_period()
        wk = get_weekly_period()
        for qid in _DAILY_MAP.get(action, []):
            q = _ALL_QUESTS.get(qid)
            if not q:
                continue
            cur = db.get_quest_progress(user_id, qid, dk)
            if cur < q["target"]:
                db.increment_quest_progress(user_id, qid, dk,
                                            min(amount, q["target"] - cur))
        for qid in _WEEKLY_MAP.get(action, []):
            q = _ALL_QUESTS.get(qid)
            if not q:
                continue
            cur = db.get_quest_progress(user_id, qid, wk)
            if cur < q["target"]:
                db.increment_quest_progress(user_id, qid, wk,
                                            min(amount, q["target"] - cur))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


def _pb(current: int, target: int) -> str:
    """Return 'current/target'."""
    return f"{min(current, target)}/{target}"


def _progress_line(quests: list[dict], period: str, user_id: str) -> list[str]:
    parts = []
    for q in quests:
        prog    = db.get_quest_progress(user_id, q["id"], period)
        claimed = db.is_quest_claimed(user_id, q["id"], period)
        bar     = _pb(prog, q["target"])
        tick    = "✓" if claimed else ("!" if prog >= q["target"] else "")
        parts.append(f"{q['label']} {bar}{tick}")
    return parts


# ---------------------------------------------------------------------------
# /questhelp
# ---------------------------------------------------------------------------

QUEST_HELP = (
    "📜 Quests\n"
    "!quests — overview\n"
    "!dailyquests — daily progress\n"
    "!weeklyquests — weekly progress\n"
    "!claimquest — claim all done\n"
    "!claimquest [id] — claim one"
)


async def handle_questhelp(bot: BaseBot, user: User) -> None:
    await _w(bot, user.id, QUEST_HELP)


# ---------------------------------------------------------------------------
# /quests
# ---------------------------------------------------------------------------

async def handle_quests(bot: BaseBot, user: User) -> None:
    await _w(bot, user.id, "📜 Quests: /dailyquests /weeklyquests /claimquest")


# ---------------------------------------------------------------------------
# /dailyquests
# ---------------------------------------------------------------------------

async def handle_dailyquests(bot: BaseBot, user: User) -> None:
    db.ensure_user(user.id, user.username)
    parts = _progress_line(DAILY_QUESTS, get_daily_period(), user.id)
    await _w(bot, user.id, "Daily: " + " | ".join(parts))


# ---------------------------------------------------------------------------
# /weeklyquests
# ---------------------------------------------------------------------------

async def handle_weeklyquests(bot: BaseBot, user: User) -> None:
    db.ensure_user(user.id, user.username)
    parts = _progress_line(WEEKLY_QUESTS, get_weekly_period(), user.id)
    await _w(bot, user.id, "Weekly: " + " | ".join(parts))


# ---------------------------------------------------------------------------
# /claimquest [quest_id]
# ---------------------------------------------------------------------------

async def handle_claimquest(bot: BaseBot, user: User, args: list[str]) -> None:
    db.ensure_user(user.id, user.username)
    dk = get_daily_period()
    wk = get_weekly_period()

    if len(args) >= 2:
        quest_id = args[1].lower()
        q = _ALL_QUESTS.get(quest_id)
        if not q:
            await _w(bot, user.id, f"Unknown quest: {quest_id}")
            return
        period = dk if quest_id.startswith("daily_") else wk
        prog   = db.get_quest_progress(user.id, quest_id, period)
        if prog < q["target"]:
            await _w(bot, user.id, f"Not done: {q['label']} {_pb(prog, q['target'])}")
            return
        if db.is_quest_claimed(user.id, quest_id, period):
            await _w(bot, user.id, "Already claimed.")
            return
        db.mark_quest_claimed(user.id, quest_id, period)
        db.adjust_balance(user.id, q["coins"])
        await leveling.award_xp(bot, user, q["xp"], q["coins"], is_game_win=False)
        await _w(bot, user.id, f"✅ Quest claimed: +{q['coins']}c +{q['xp']}XP.")
        return

    # ── Claim all completed ────────────────────────────────────────────────
    total_coins = 0
    total_xp    = 0
    claimed_n   = 0
    for q in DAILY_QUESTS:
        prog = db.get_quest_progress(user.id, q["id"], dk)
        if prog >= q["target"] and not db.is_quest_claimed(user.id, q["id"], dk):
            db.mark_quest_claimed(user.id, q["id"], dk)
            total_coins += q["coins"]
            total_xp    += q["xp"]
            claimed_n   += 1
    for q in WEEKLY_QUESTS:
        prog = db.get_quest_progress(user.id, q["id"], wk)
        if prog >= q["target"] and not db.is_quest_claimed(user.id, q["id"], wk):
            db.mark_quest_claimed(user.id, q["id"], wk)
            total_coins += q["coins"]
            total_xp    += q["xp"]
            claimed_n   += 1

    if claimed_n == 0:
        await _w(bot, user.id, "No completed quests yet.")
        return

    db.adjust_balance(user.id, total_coins)
    await leveling.award_xp(bot, user, total_xp, total_coins, is_game_win=False)
    await _w(bot, user.id, f"✅ Claimed {claimed_n} quest(s): +{total_coins}c +{total_xp}XP.")
