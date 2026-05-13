"""
modules/onboarding.py — 3.1M New Player Tutorial & Room Guide
==============================================================
Handles:
  • New player detection & one-time welcome message
  • !start  — step-by-step tutorial with 🪙 rewards
  • !guide [topic]  — full room guide with 12 topics
  • !newbie / !tutorial  — aliases / progress view
  • !starter / !startermissions  — starter mission tracker
  • !onboardadmin — staff tools
  • Tutorial progress hooks (called from main.py)
  • Anti-spam cooldowns + onboarding reminders
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

import database as db
from highrise import BaseBot, User

# ---------------------------------------------------------------------------
# Tutorial definition
# ---------------------------------------------------------------------------

TUTORIAL_STEPS: list[tuple[str, str, str, int]] = [
    ("profile",  "!profile",  "Check your profile",       500),
    ("missions", "!missions", "Check your missions",      500),
    ("mine",     "!mine",     "Try mining",              1000),
    ("fish",     "!fish",     "Try fishing",             1000),
    ("today",    "!today",    "Check your progress",      500),
    ("shop",     "!shop",     "View the shop",            500),
    ("events",   "!events",   "View events",              500),
]
_STEP_KEYS   = [s[0] for s in TUTORIAL_STEPS]
_STEP_CMDS   = {s[0]: s[1] for s in TUTORIAL_STEPS}
_STEP_LABELS = {s[0]: s[2] for s in TUTORIAL_STEPS}
_STEP_COINS  = {s[0]: s[3] for s in TUTORIAL_STEPS}

COMPLETION_COINS  = 5_000
STARTER_KIT_COINS = 10_000

# ---------------------------------------------------------------------------
# Anti-spam state
# ---------------------------------------------------------------------------

_guide_cooldown:  dict[str, float] = {}   # user_id → last_used timestamp
_start_cooldown:  dict[str, float] = {}
_public_welcome_last: float = 0.0         # room-wide welcome throttle

_GUIDE_CD  = 5.0   # seconds
_START_CD  = 10.0
_WELCOME_CD = 30.0  # seconds between public welcome messages
_REMINDER_CD = 600  # 10 minutes between reminder whispers per player

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _gc() -> "sqlite3.Connection":
    return db.get_connection()


def _ensure_onboarding(user_id: str, username: str) -> None:
    try:
        c = _gc()
        c.execute(
            "INSERT OR IGNORE INTO player_onboarding (user_id, username) VALUES (?, ?)",
            (user_id, username.lower()),
        )
        c.commit()
        c.close()
    except Exception:
        pass


def _get_onboarding(user_id: str) -> dict:
    try:
        c   = _gc()
        row = c.execute(
            "SELECT * FROM player_onboarding WHERE user_id=?", (user_id,)
        ).fetchone()
        c.close()
        return dict(row) if row else {}
    except Exception:
        return {}


def _mark_welcome_sent(user_id: str) -> None:
    try:
        c = _gc()
        c.execute(
            "UPDATE player_onboarding SET welcome_sent=1, updated_at=datetime('now') WHERE user_id=?",
            (user_id,),
        )
        c.commit()
        c.close()
    except Exception:
        pass


def _mark_tutorial_started(user_id: str) -> None:
    try:
        c = _gc()
        c.execute(
            "UPDATE player_onboarding "
            "SET tutorial_started=1, updated_at=datetime('now') WHERE user_id=?",
            (user_id,),
        )
        c.commit()
        c.close()
    except Exception:
        pass


def _mark_tutorial_completed(user_id: str) -> None:
    try:
        c = _gc()
        c.execute(
            "UPDATE player_onboarding "
            "SET tutorial_completed=1, updated_at=datetime('now') WHERE user_id=?",
            (user_id,),
        )
        c.commit()
        c.close()
    except Exception:
        pass


def _is_step_done(user_id: str, step_key: str) -> bool:
    try:
        c   = _gc()
        row = c.execute(
            "SELECT completed FROM player_tutorial_steps WHERE user_id=? AND step_key=?",
            (user_id, step_key),
        ).fetchone()
        c.close()
        return bool(row and row["completed"])
    except Exception:
        return False


def _complete_step(user_id: str, username: str, step_key: str) -> bool:
    """Mark step complete. Returns True if it was newly completed (not already done)."""
    if _is_step_done(user_id, step_key):
        return False
    try:
        c = _gc()
        c.execute(
            """INSERT INTO player_tutorial_steps (user_id, username, step_key, completed, completed_at)
               VALUES (?, ?, ?, 1, datetime('now'))
               ON CONFLICT(user_id, step_key) DO UPDATE
               SET completed=1, completed_at=datetime('now')""",
            (user_id, username.lower(), step_key),
        )
        c.commit()
        c.close()
        return True
    except Exception:
        return False


def _claim_step_reward(user_id: str, username: str, step_key: str) -> bool:
    """Idempotent: grant coins and mark reward_claimed=1. Returns True if newly claimed."""
    try:
        c   = _gc()
        row = c.execute(
            "SELECT reward_claimed FROM player_tutorial_steps WHERE user_id=? AND step_key=?",
            (user_id, step_key),
        ).fetchone()
        c.close()
        if row and row["reward_claimed"]:
            return False
    except Exception:
        return False
    amount = _STEP_COINS.get(step_key, 0)
    if amount:
        try:
            db.adjust_balance(user_id, amount)
        except Exception:
            pass
    try:
        c = _gc()
        c.execute(
            """INSERT INTO player_tutorial_steps (user_id, username, step_key, reward_claimed)
               VALUES (?, ?, ?, 1)
               ON CONFLICT(user_id, step_key) DO UPDATE SET reward_claimed=1""",
            (user_id, username.lower(), step_key),
        )
        c.execute(
            """INSERT INTO onboarding_rewards_log (user_id, username, reward_key, amount, currency, details)
               VALUES (?, ?, ?, ?, 'coins', 'tutorial step reward')""",
            (user_id, username.lower(), f"step_{step_key}", amount),
        )
        c.commit()
        c.close()
        return True
    except Exception:
        return False


def _completed_steps(user_id: str) -> list[str]:
    try:
        c    = _gc()
        rows = c.execute(
            "SELECT step_key FROM player_tutorial_steps WHERE user_id=? AND completed=1",
            (user_id,),
        ).fetchall()
        c.close()
        return [r["step_key"] for r in rows]
    except Exception:
        return []


def _claim_starter_kit(user_id: str, username: str) -> bool:
    """Grant starter kit (coins). Idempotent — returns True if newly claimed."""
    ob = _get_onboarding(user_id)
    if ob.get("starter_reward_claimed"):
        return False
    try:
        db.adjust_balance(user_id, STARTER_KIT_COINS)
    except Exception:
        pass
    try:
        c = _gc()
        c.execute(
            "UPDATE player_onboarding SET starter_reward_claimed=1, updated_at=datetime('now') WHERE user_id=?",
            (user_id,),
        )
        c.execute(
            """INSERT INTO onboarding_rewards_log
               (user_id, username, reward_key, amount, currency, details)
               VALUES (?, ?, 'starter_kit', ?, 'coins', 'starter kit reward')""",
            (user_id, username.lower(), STARTER_KIT_COINS),
        )
        c.commit()
        c.close()
        return True
    except Exception:
        return False


def _update_reminder_ts(user_id: str) -> None:
    try:
        c = _gc()
        c.execute(
            "UPDATE player_onboarding SET last_reminder_at=datetime('now'), "
            "updated_at=datetime('now') WHERE user_id=?",
            (user_id,),
        )
        c.commit()
        c.close()
    except Exception:
        pass


def _get_onboarding_stats() -> dict:
    try:
        c = _gc()
        total   = c.execute("SELECT COUNT(*) AS n FROM player_onboarding").fetchone()["n"]
        started = c.execute("SELECT COUNT(*) AS n FROM player_onboarding WHERE tutorial_started=1").fetchone()["n"]
        done    = c.execute("SELECT COUNT(*) AS n FROM player_onboarding WHERE tutorial_completed=1").fetchone()["n"]
        kits    = c.execute("SELECT COUNT(*) AS n FROM player_onboarding WHERE starter_reward_claimed=1").fetchone()["n"]
        c.close()
        return {"total": total, "started": started, "done": done, "kits": kits}
    except Exception:
        return {}


def _reset_onboarding_for(user_id: str) -> None:
    try:
        c = _gc()
        c.execute("DELETE FROM player_onboarding WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM player_tutorial_steps WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM onboarding_rewards_log WHERE user_id=?", (user_id,))
        c.commit()
        c.close()
    except Exception:
        pass


def _force_complete_onboarding(user_id: str, username: str) -> None:
    _ensure_onboarding(user_id, username)
    for key in _STEP_KEYS:
        _complete_step(user_id, username, key)
        _claim_step_reward(user_id, username, key)
    _mark_tutorial_started(user_id)
    _mark_tutorial_completed(user_id)
    _claim_starter_kit(user_id, username)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


def _next_step(done: list[str]) -> Optional[tuple[str, str, str]]:
    """Return (key, cmd, label) of the first undone step, or None if all done."""
    done_set = set(done)
    for key, cmd, label, _ in TUTORIAL_STEPS:
        if key not in done_set:
            return key, cmd, label
    return None


def _is_on_cooldown(store: dict, uid: str, secs: float) -> bool:
    now = time.monotonic()
    if now - store.get(uid, 0) < secs:
        return True
    store[uid] = now
    return False


# ---------------------------------------------------------------------------
# New-player welcome (called from on_user_join)
# ---------------------------------------------------------------------------

def _is_bot_account(user_id: str, username: str) -> bool:
    """Return True if this user is a registered bot process (skip welcoming bots)."""
    try:
        c   = _gc()
        row = c.execute(
            "SELECT 1 FROM bot_instances WHERE bot_id=? OR LOWER(username)=? LIMIT 1",
            (user_id, username.lower()),
        ).fetchone()
        c.close()
        return row is not None
    except Exception:
        return False


async def on_join_welcome(bot: BaseBot, user: User) -> None:
    """Send a one-time new-player welcome. Safe to call on every join."""
    global _public_welcome_last
    # Skip welcoming other bots in the room
    if _is_bot_account(user.id, user.username):
        return
    _ensure_onboarding(user.id, user.username)
    ob = _get_onboarding(user.id)
    if ob.get("welcome_sent"):
        return

    # Public welcome (throttled room-wide to 1 per 30s)
    now = time.monotonic()
    if now - _public_welcome_last >= _WELCOME_CD:
        _public_welcome_last = now
        try:
            msg = (
                f"👋 Welcome @{user.username}!\n"
                f"Type !start for the quick guide.\n"
                f"Earn 🪙 ChillCoins by playing.\n"
                f"Use 🎫 Luxe Tickets for premium perks."
            )
            await bot.highrise.chat(msg[:220])
        except Exception:
            pass

    # Private whisper with more detail
    await _w(bot, user.id,
             f"🌟 Welcome to ChillTopia, @{user.username}!\n"
             f"Type !start to begin the tutorial & earn 🪙.\n"
             f"Use !guide for the full room guide.\n"
             f"Have fun!")

    _mark_welcome_sent(user.id)


# ---------------------------------------------------------------------------
# Tutorial step hook (called from main.py after each command)
# ---------------------------------------------------------------------------

async def check_tutorial_step(bot: BaseBot, user: User, step_key: str) -> None:
    """Call this after a player uses a tutorial command. Non-blocking, fail-safe."""
    try:
        _ensure_onboarding(user.id, user.username)
        ob = _get_onboarding(user.id)

        if ob.get("tutorial_completed"):
            return
        if not ob.get("tutorial_started"):
            return  # tutorial not started yet — don't auto-progress

        newly_done = _complete_step(user.id, user.username, step_key)
        newly_paid = _claim_step_reward(user.id, user.username, step_key) if newly_done else False

        if newly_done:
            coins = _STEP_COINS.get(step_key, 0)
            done  = _completed_steps(user.id)
            nxt   = _next_step(done)
            if nxt:
                nk, nc, nl = nxt
                await _w(bot, user.id,
                         f"✅ Step complete! +{coins:,} 🪙\n"
                         f"Step {_STEP_KEYS.index(nk)+1}: {nl}\n"
                         f"Type {nc}")
            else:
                # All steps done → completion
                _mark_tutorial_completed(user.id)
                await _w(bot, user.id,
                         f"✅ Step complete! +{coins:,} 🪙")
                await asyncio.sleep(0.4)
                await _grant_completion(bot, user)
    except Exception:
        pass


async def _grant_completion(bot: BaseBot, user: User) -> None:
    """Issue completion bonus and starter kit, announce finish."""
    comp_paid = False
    try:
        db.adjust_balance(user.id, COMPLETION_COINS)
        try:
            c = _gc()
            c.execute(
                """INSERT INTO onboarding_rewards_log
                   (user_id, username, reward_key, amount, currency, details)
                   VALUES (?, ?, 'completion_bonus', ?, 'coins', 'tutorial completion bonus')""",
                (user.id, user.username.lower(), COMPLETION_COINS),
            )
            c.commit()
            c.close()
        except Exception:
            pass
        comp_paid = True
    except Exception:
        pass

    kit_paid = _claim_starter_kit(user.id, user.username)

    total = (COMPLETION_COINS if comp_paid else 0) + (STARTER_KIT_COINS if kit_paid else 0)
    await _w(bot, user.id,
             f"🎉 Tutorial Complete!\n"
             f"Reward: {COMPLETION_COINS:,} 🪙 bonus + Starter Kit ({STARTER_KIT_COINS:,} 🪙)\n"
             f"Total earned: {total:,} 🪙\n"
             f"Use !guide anytime for help.")


# ---------------------------------------------------------------------------
# Onboarding reminder (call after mine/fish/etc. for incomplete-tutorial players)
# ---------------------------------------------------------------------------

async def maybe_send_reminder(bot: BaseBot, user: User) -> None:
    """Whisper a tutorial reminder after an action, max once per 10 minutes."""
    try:
        ob = _get_onboarding(user.id)
        if not ob or ob.get("tutorial_completed"):
            return
        if not ob.get("tutorial_started"):
            return
        last = ob.get("last_reminder_at")
        if last:
            import datetime as _dt
            ts = _dt.datetime.fromisoformat(last)
            age = (_dt.datetime.utcnow() - ts).total_seconds()
            if age < _REMINDER_CD:
                return
        _update_reminder_ts(user.id)
        await _w(bot, user.id,
                 "📘 Tip: Finish !start to unlock your Starter Chest.")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# !start
# ---------------------------------------------------------------------------

async def handle_start(bot: BaseBot, user: User, args: list[str]) -> None:
    if _is_on_cooldown(_start_cooldown, user.id, _START_CD):
        await _w(bot, user.id, "⚠️ Please wait a few seconds.")
        return

    _ensure_onboarding(user.id, user.username)
    ob = _get_onboarding(user.id)

    if ob.get("tutorial_completed"):
        await _w(bot, user.id, "✅ Tutorial already complete. Use !guide anytime.")
        return

    _mark_tutorial_started(user.id)
    done = _completed_steps(user.id)
    nxt  = _next_step(done)

    n_done = len(done)
    total  = len(TUTORIAL_STEPS)

    if not nxt:
        # All steps done but not marked complete yet
        _mark_tutorial_completed(user.id)
        await _grant_completion(bot, user)
        return

    nk, nc, nl = nxt
    step_num = _STEP_KEYS.index(nk) + 1

    if n_done == 0:
        await _w(bot, user.id,
                 f"🌟 Welcome to ChillTopia!\n"
                 f"Step 1 of {total}: {nl}\n"
                 f"Type {nc}\n"
                 f"Complete all steps for 🪙 rewards + Starter Kit!")
    else:
        await _w(bot, user.id,
                 f"📋 Tutorial Progress: {n_done}/{total} done\n"
                 f"Step {step_num}: {nl}\n"
                 f"Type {nc}")


# ---------------------------------------------------------------------------
# !tutorial — show progress
# ---------------------------------------------------------------------------

async def handle_tutorial(bot: BaseBot, user: User, args: list[str]) -> None:
    if _is_on_cooldown(_guide_cooldown, user.id, _GUIDE_CD):
        await _w(bot, user.id, "⚠️ Please wait a few seconds.")
        return

    _ensure_onboarding(user.id, user.username)
    ob   = _get_onboarding(user.id)
    done = _completed_steps(user.id)

    lines = ["📋 Tutorial Progress"]
    for i, (key, cmd, label, coins) in enumerate(TUTORIAL_STEPS, 1):
        tick = "✅" if key in done else "⬜"
        lines.append(f"{tick} {i}. {label} ({coins:,} 🪙)")

    if ob.get("tutorial_completed"):
        lines.append("🎉 Complete! Use !guide anytime.")
    elif not ob.get("tutorial_started"):
        lines.append("Type !start to begin.")
    else:
        remaining = len(TUTORIAL_STEPS) - len(done)
        lines.append(f"{remaining} step(s) left. Type !start.")

    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# !newbie — beginner orientation whisper
# ---------------------------------------------------------------------------

async def handle_newbie(bot: BaseBot, user: User, args: list[str]) -> None:
    if _is_on_cooldown(_guide_cooldown, user.id, _GUIDE_CD):
        await _w(bot, user.id, "⚠️ Please wait a few seconds.")
        return
    await _w(bot, user.id,
             "🌱 New here? Start with:\n"
             "!start — step tutorial\n"
             "!mine or !fish — earn 🪙\n"
             "!balance — check 🪙\n"
             "!shop — spend 🪙")
    await asyncio.sleep(0.3)
    await _w(bot, user.id,
             "More help:\n"
             "!guide — room guide\n"
             "!missions — daily goals\n"
             "!profile — your card\n"
             "!events — active boosts")


# ---------------------------------------------------------------------------
# !starter / !startermissions
# ---------------------------------------------------------------------------

async def handle_starter(bot: BaseBot, user: User, args: list[str]) -> None:
    if _is_on_cooldown(_guide_cooldown, user.id, _GUIDE_CD):
        await _w(bot, user.id, "⚠️ Please wait a few seconds.")
        return

    _ensure_onboarding(user.id, user.username)
    done = _completed_steps(user.id)
    ob   = _get_onboarding(user.id)

    starter_keys = ["profile", "mine", "fish", "missions"]
    lines = ["🌱 Starter Missions"]
    for key in starter_keys:
        label = _STEP_LABELS.get(key, key)
        tick  = "✅" if key in done else "⬜"
        lines.append(f"{tick} {label}")
    lines.append("Reward: Starter Kit 🎁")

    if ob.get("starter_reward_claimed"):
        lines.append("✅ Starter Kit claimed!")
    elif all(k in done for k in starter_keys):
        lines.append("Type !start to claim your kit.")
    else:
        remaining = [k for k in starter_keys if k not in done]
        lines.append(f"Tip: use {_STEP_CMDS.get(remaining[0], '!start')}")

    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# !guide [topic]
# ---------------------------------------------------------------------------

_GUIDE_TOPICS: dict[str, list[str]] = {
    "economy": [
        "💰 Economy\n"
        "🪙 ChillCoins: earned by playing.\n"
        "🎫 Luxe Tickets: from verified Gold tips.\n"
        "Use !balance, !shop, !luxeshop.",

        "Convert 🎫 to 🪙:\n"
        "!buycoins small/medium/large\n"
        "!buycoins max",
    ],
    "mining": [
        "⛏️ Mining\n"
        "!mine — mine once\n"
        "!automine — timer auto mine\n"
        "!automine luxe — use Luxe time\n"
        "!mineluck — luck stack\n"
        "!orebook — collection",

        "Buy tools in !shop.\n"
        "Luxe auto time stacks.\n"
        "Use !autotime to check time.",
    ],
    "fishing": [
        "🎣 Fishing\n"
        "!fish — fish once\n"
        "!autofish — timer auto fish\n"
        "!autofish luxe — use Luxe time\n"
        "!fishluck — luck stack\n"
        "!fishbook — collection",

        "Buy rods in !shop.\n"
        "Luxe auto time stacks.\n"
        "Use !autotime to check time.",
    ],
    "missions": [
        "📋 Missions\n"
        "!missions — daily goals\n"
        "!weekly — weekly goals\n"
        "!today — progress\n"
        "Complete goals for 🪙, 🎫, chests, XP.",
    ],
    "events": [
        "🎉 Events\n"
        "!events — schedule\n"
        "!event active — current boost\n"
        "!season — weekly season\n"
        "!topseason [category] — rankings",

        "Events boost luck, XP, or season points.\n"
        "Use !mineevents for mining boosts.",
    ],
    "profile": [
        "👤 Profile\n"
        "!profile — public card\n"
        "!stats — private details\n"
        "!flex — public showoff\n"
        "!profile settings — privacy",

        "Shows title, badges, level, VIP,\n"
        "collection, missions, season rank.\n"
        "Balance is private by default.",
    ],
    "shop": [
        "🛒 Shops\n"
        "!shop — ChillCoins shop\n"
        "!luxeshop — Luxe shop\n"
        "!buyluxe [#] — buy Luxe item\n"
        "!buycoins max — convert 🎫 to 🪙",
    ],
    "luxe": [
        "🎫 Luxe Tickets\n"
        "Earned from verified Gold tips.\n"
        "Use for VIP, Luxe auto time, boosts, 🪙 packs.\n"
        "Use !luxeshop.",

        "Luxe auto:\n"
        "!automine luxe\n"
        "!autofish luxe\n"
        "!autotime",
    ],
    "vip": [
        "👑 VIP\n"
        "Use !vip to view status.\n"
        "Buy with 🎫 in !luxeshop.\n"
        "VIP gives luck, faster auto, longer auto time.",
    ],
    "casino": [
        "🎰 Casino\n"
        "Blackjack: !bet [amount], !hit, !stand\n"
        "Rules: !bjrules\n"
        "Status: !bjstatus",

        "Poker and other games use 🪙.\n"
        "Play responsibly.",
    ],
    "commands": [
        "📜 Commands\n"
        "Progress: !today, !missions, !weekly\n"
        "Profile: !profile, !stats, !flex\n"
        "Mining/Fishing: !mine, !fish",

        "Shops: !shop, !luxeshop\n"
        "Events: !events, !season\n"
        "Help: !guide [topic]",
    ],
}

_GUIDE_ALIASES: dict[str, str] = {
    "mine": "mining", "dig": "mining",
    "fish": "fishing", "fishing": "fishing",
    "mission": "missions", "quest": "missions", "quests": "missions",
    "event": "events", "season": "events",
    "me": "profile", "card": "profile",
    "market": "shop", "store": "shop", "buy": "shop",
    "ticket": "luxe", "tickets": "luxe", "premium": "luxe",
    "casino": "casino", "blackjack": "casino", "bj": "casino",
    "poker": "casino", "games": "casino",
    "cmd": "commands", "cmds": "commands", "help": "commands",
}


async def handle_guide(bot: BaseBot, user: User, args: list[str]) -> None:
    if _is_on_cooldown(_guide_cooldown, user.id, _GUIDE_CD):
        await _w(bot, user.id, "⚠️ Please wait a few seconds.")
        return

    raw   = args[1].strip().lower() if len(args) > 1 else ""
    topic = _GUIDE_ALIASES.get(raw, raw) if raw else ""

    if not topic:
        await _w(bot, user.id,
                 "📘 Room Guide\n"
                 "1. !guide economy\n"
                 "2. !guide mining\n"
                 "3. !guide fishing\n"
                 "4. !guide missions\n"
                 "5. !guide events\n"
                 "6. !guide profile")
        await asyncio.sleep(0.3)
        await _w(bot, user.id,
                 "More:\n"
                 "!guide casino\n"
                 "!guide shop\n"
                 "!guide luxe\n"
                 "!guide vip\n"
                 "!guide commands")
        return

    msgs = _GUIDE_TOPICS.get(topic)
    if not msgs:
        await _w(bot, user.id,
                 f"⚠️ Unknown topic: {raw}\n"
                 f"Try: economy, mining, fishing,\n"
                 f"missions, events, profile, shop,\n"
                 f"luxe, vip, casino, commands")
        return

    for i, msg in enumerate(msgs):
        if i > 0:
            await asyncio.sleep(0.3)
        await _w(bot, user.id, msg[:220])

    await asyncio.sleep(0.3)
    await _w(bot, user.id,
             "🌟 New here?\n"
             "Use !start for tutorial.\n"
             "Use !guide for room guide.")


# ---------------------------------------------------------------------------
# !onboardadmin
# ---------------------------------------------------------------------------

async def handle_onboardadmin(bot: BaseBot, user: User, args: list[str]) -> None:
    from modules.permissions import is_admin, is_owner
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin only.")
        return

    sub      = args[1].lower() if len(args) >= 2 else "help"
    raw_name = args[2].lstrip("@").strip() if len(args) >= 3 else None

    if sub == "help" or sub not in ("status", "reset", "complete", "stats"):
        await _w(bot, user.id,
                 "🛠️ Onboarding Admin\n"
                 "!onboardadmin status @user\n"
                 "!onboardadmin reset @user\n"
                 "!onboardadmin complete @user\n"
                 "!onboardadmin stats")
        return

    if sub == "stats":
        s = _get_onboarding_stats()
        if not s:
            await _w(bot, user.id, "No onboarding data yet.")
            return
        pct = round(s["done"] / s["started"] * 100) if s["started"] else 0
        await _w(bot, user.id,
                 f"📊 Onboarding Stats\n"
                 f"Registered: {s['total']}\n"
                 f"Started: {s['started']}\n"
                 f"Completed: {s['done']} ({pct}%)\n"
                 f"Starter kits claimed: {s['kits']}")
        return

    if not raw_name:
        await _w(bot, user.id, f"Usage: !onboardadmin {sub} @user")
        return

    target = db.get_user_by_username(raw_name)
    if not target:
        await _w(bot, user.id, f"@{raw_name} not found.")
        return
    t_id, t_name = target["user_id"], target["username"]

    if sub == "status":
        _ensure_onboarding(t_id, t_name)
        ob   = _get_onboarding(t_id)
        done = _completed_steps(t_id)
        pct  = round(len(done) / len(TUTORIAL_STEPS) * 100)
        await _w(bot, user.id,
                 f"🔍 Onboarding — @{t_name}\n"
                 f"Welcome sent: {'yes' if ob.get('welcome_sent') else 'no'}\n"
                 f"Tutorial: {'done' if ob.get('tutorial_completed') else f'{len(done)}/{len(TUTORIAL_STEPS)} steps ({pct}%)'}\n"
                 f"Starter kit: {'claimed' if ob.get('starter_reward_claimed') else 'pending'}")
        return

    if sub == "reset":
        _reset_onboarding_for(t_id)
        await _w(bot, user.id, f"✅ Onboarding reset for @{t_name}.")
        return

    if sub == "complete":
        _force_complete_onboarding(t_id, t_name)
        await _w(bot, user.id, f"✅ Onboarding force-completed for @{t_name}.")
        return
