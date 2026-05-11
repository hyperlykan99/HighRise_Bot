"""
modules/onboarding.py
---------------------
New player onboarding system.

Player commands:
  !start      — welcome + quick-start guide
  !tutorial   — step-by-step tutorial
  !newplayer  — alias for !start
  !howtoplay  — alias for !tutorial

Auto-triggered on first join (one-time whisper, cooldown protected).
Never shows staff commands to regular players.
Uses ! commands only.
"""
from __future__ import annotations
from datetime import datetime, timezone
from highrise import BaseBot, User

import database as db
from modules.permissions import is_admin


async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _has_seen_onboarding(user_id: str) -> bool:
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT id FROM onboarding_seen WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return True   # fail-safe: don't spam


def _mark_onboarding_seen(user_id: str) -> None:
    try:
        conn = db.get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO onboarding_seen (user_id, seen_at) VALUES (?,?)",
            (user_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Auto-trigger (called from on_user_join)
# ---------------------------------------------------------------------------

async def send_onboarding_if_new(bot: BaseBot, user: User) -> None:
    """Called on every join. Sends onboarding whisper once per player."""
    if _has_seen_onboarding(user.id):
        return
    _mark_onboarding_seen(user.id)
    await _w(bot, user.id,
             f"👋 Welcome to the room, @{user.username}!\n"
             f"Type !help to see all commands.\n"
             f"Type !missions to earn rewards.\n"
             f"Type !notifon events for event alerts.")
    await _w(bot, user.id,
             "🎮 Quick Start:\n"
             "!mine — mine for ores\n"
             "!fish — catch fish\n"
             "!bj 100 — play blackjack\n"
             "!raffle — check the raffle\n"
             "!profile — see your stats")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def handle_start(bot: BaseBot, user: User) -> None:
    await _w(bot, user.id,
             f"👋 Welcome, @{user.username}!\n"
             f"!profile — see your stats\n"
             f"!missions — earn rewards\n"
             f"!mine — mine for ores\n"
             f"!fish — catch fish\n"
             f"!help — see more commands")


async def handle_tutorial(bot: BaseBot, user: User) -> None:
    await _w(bot, user.id,
             "📖 Tutorial — Part 1\n"
             "1. Type !mine to mine ores.\n"
             "2. Type !fish to catch fish.\n"
             "3. Sell items for coins: !sellore or !sellfish\n"
             "4. Check your coins: !balance\n"
             "5. Level up and unlock better tools!")
    await _w(bot, user.id,
             "📖 Tutorial — Part 2\n"
             "6. Play !bj [amount] for blackjack.\n"
             "7. Enter !raffle with earned tickets.\n"
             "8. Buy VIP for longer AutoMine/AutoFish: !vip\n"
             "9. Check daily missions: !missions\n"
             "10. Subscribe to alerts: !subscribe")


async def handle_newplayer(bot: BaseBot, user: User,
                            args: list[str]) -> None:
    """!newplayer — alias for !start; shows new-player onboarding."""
    await handle_start(bot, user)


async def on_user_join_onboarding(bot: BaseBot, user: User) -> None:
    """Check if this player is brand new and send the onboarding whisper."""
    try:
        import database as _db
        conn = _db.get_connection()
        row  = conn.execute(
            "SELECT 1 FROM onboarding_seen WHERE user_id=?",
            (user.id,),
        ).fetchone()
        conn.close()
        if row:
            return
        conn2 = _db.get_connection()
        conn2.execute(
            "INSERT OR IGNORE INTO onboarding_seen(user_id, seen_at) "
            "VALUES(?, datetime('now'))",
            (user.id,),
        )
        conn2.commit()
        conn2.close()
        await handle_start(bot, user)
    except Exception:
        pass
