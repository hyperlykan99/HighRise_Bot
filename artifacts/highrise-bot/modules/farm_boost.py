"""
modules/farm_boost.py
---------------------
Monetized AutoMine / AutoFish farming with VIP-based duration limits.

Player commands:
  !farmstatus              — show current farm session status
  !stopfarm                — stop all farming (mine + fish)
  !buyfarmboost [15m|30m|60m] — buy extra farm time with gold
  !myfarmboost             — check active farm boost
  !giftfarmboost [user] [15m|30m|60m] — gift boost with gold

VIP-based session limits:
  Free player:   5 min max
  VIP (any):    30 min max
  Room Sponsor: 60 min max
  Farm boost:   +15/30/60 min on top of base limit

Boost prices (gold):
  15m — 10 gold
  30m — 20 gold
  60m — 35 gold
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from highrise import BaseBot, User

import database as db
from modules.permissions import is_manager


async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Farm boost config
# ---------------------------------------------------------------------------

_BOOST_OPTIONS: dict[str, dict] = {
    "15m": {"minutes": 15, "gold": 10},
    "30m": {"minutes": 30, "gold": 20},
    "60m": {"minutes": 60, "gold": 35},
}

# VIP base limits
_FREE_LIMIT     =  5   # minutes
_VIP_LIMIT      = 30
_SPONSOR_LIMIT  = 60


# ---------------------------------------------------------------------------
# VIP tier detection
# ---------------------------------------------------------------------------

def get_base_farm_limit(user_id: str) -> int:
    """Return base AutoMine/AutoFish limit (minutes) based on VIP status."""
    try:
        # Check sponsor title (room_sponsor item or room_settings)
        if db.owns_item(user_id, "room_sponsor") or db.owns_item(user_id, "sponsor"):
            return _SPONSOR_LIMIT
        # Check any VIP tier
        for vip_id in ("vip_30d", "vip_7d", "vip_1d", "vip"):
            if db.owns_item(user_id, vip_id):
                return _VIP_LIMIT
    except Exception:
        pass
    return _FREE_LIMIT


def get_farm_limit_with_boost(user_id: str) -> tuple[int, int]:
    """Return (total_limit_minutes, boost_minutes) for a user."""
    base  = get_base_farm_limit(user_id)
    boost = _get_active_boost_minutes(user_id)
    return base + boost, boost


# ---------------------------------------------------------------------------
# Farm boost DB helpers
# ---------------------------------------------------------------------------

def _get_active_boost(user_id: str) -> dict | None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT * FROM farm_boosts WHERE user_id=? AND expires_at>? LIMIT 1",
            (user_id, now),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def _get_active_boost_minutes(user_id: str) -> int:
    b = _get_active_boost(user_id)
    if not b:
        return 0
    try:
        expires = datetime.fromisoformat(b["expires_at"])
        remaining = (expires - datetime.now(timezone.utc)).total_seconds() / 60
        return max(0, int(remaining))
    except Exception:
        return 0


def _grant_boost(user_id: str, username: str,
                  minutes: int, granted_by: str = "purchase") -> None:
    now = datetime.now(timezone.utc)
    # Stack on top of existing boost if present
    b = _get_active_boost(user_id)
    if b:
        try:
            expires = datetime.fromisoformat(b["expires_at"])
            new_exp = (expires + timedelta(minutes=minutes)).isoformat()
        except Exception:
            new_exp = (now + timedelta(minutes=minutes)).isoformat()
        try:
            conn = db.get_connection()
            conn.execute(
                "UPDATE farm_boosts SET expires_at=?, boost_minutes=boost_minutes+? "
                "WHERE user_id=?",
                (new_exp, minutes, user_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
    else:
        exp = (now + timedelta(minutes=minutes)).isoformat()
        try:
            conn = db.get_connection()
            conn.execute(
                """INSERT INTO farm_boosts
                     (user_id, username, expires_at, boost_minutes, granted_by, granted_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     expires_at    = excluded.expires_at,
                     boost_minutes = boost_minutes + excluded.boost_minutes,
                     granted_by    = excluded.granted_by,
                     granted_at    = excluded.granted_at""",
                (user_id, username.lower(), exp, minutes, granted_by, now.isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Stop farm helpers (calls mining/fishing stop functions)
# ---------------------------------------------------------------------------

def _stop_mine(user_id: str, username: str) -> bool:
    try:
        from modules.mining import stop_automine_for_user
        return stop_automine_for_user(user_id, username, "user_stopped_farm")
    except Exception:
        return False


def _stop_fish(user_id: str, username: str) -> bool:
    try:
        from modules.fishing import stop_autofish_for_user
        return stop_autofish_for_user(user_id, username, "user_stopped_farm")
    except Exception:
        return False


def _mine_running(user_id: str) -> bool:
    try:
        from modules.mining import _automine_tasks
        t = _automine_tasks.get(user_id)
        return t is not None and not t.done()
    except Exception:
        return False


def _fish_running(user_id: str) -> bool:
    try:
        from modules.fishing import _autofish_tasks
        t = _autofish_tasks.get(user_id)
        return t is not None and not t.done()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def handle_farmstatus(bot: BaseBot, user: User) -> None:
    total, boost = get_farm_limit_with_boost(user.id)
    base         = get_base_farm_limit(user.id)
    mine_on      = _mine_running(user.id)
    fish_on      = _fish_running(user.id)
    if base == _SPONSOR_LIMIT:
        plan = "Room Sponsor"
    elif base == _VIP_LIMIT:
        plan = "VIP"
    else:
        plan = "Free"
    await _w(bot, user.id,
             f"🌾 Farm Status\n"
             f"AutoMine: {'ON' if mine_on else 'OFF'}\n"
             f"AutoFish: {'ON' if fish_on else 'OFF'}\n"
             f"Plan: {plan} | Max session: {total}m"
             + (f" (+{boost}m boost)" if boost else "") +
             f"\nStops if you leave room: YES")


async def handle_stopfarm(bot: BaseBot, user: User) -> None:
    mine_ok = _stop_mine(user.id, user.username)
    fish_ok = _stop_fish(user.id, user.username)
    if mine_ok or fish_ok:
        parts = []
        if mine_ok:
            parts.append("AutoMine")
        if fish_ok:
            parts.append("AutoFish")
        await _w(bot, user.id, f"🌾 Stopped: {', '.join(parts)}")
    else:
        await _w(bot, user.id, "🌾 No active AutoMine or AutoFish sessions.")


async def handle_buyfarmboost(bot: BaseBot, user: User, args: list[str]) -> None:
    option_key = args[1].lower() if len(args) >= 2 else ""
    opt = _BOOST_OPTIONS.get(option_key)
    if not opt:
        lines = ["⛏️ Farm Boost Options (costs gold):"]
        for k, v in _BOOST_OPTIONS.items():
            lines.append(f"  !buyfarmboost {k} — {v['minutes']}m for {v['gold']}g")
        await _w(bot, user.id, "\n".join(lines)[:249])
        return
    # Check gold balance (not implemented via API — use DB gold_tip_events total)
    # For now, inform that this uses the shop purchase flow
    await _w(bot, user.id,
             f"⛏️ Farm Boost: {opt['minutes']}m\n"
             f"Cost: {opt['gold']} gold\n"
             f"Send {opt['gold']} gold to BankingBot to activate your boost.\n"
             f"BankingBot will confirm and apply it automatically.")


async def handle_myfarmboost(bot: BaseBot, user: User) -> None:
    boost = _get_active_boost(user.id)
    if not boost:
        total, _ = get_farm_limit_with_boost(user.id)
        await _w(bot, user.id,
                 f"⛏️ No active farm boost.\n"
                 f"Base limit: {total}m\n"
                 f"Buy more: !buyfarmboost 15m|30m|60m")
        return
    remaining = _get_active_boost_minutes(user.id)
    total, _  = get_farm_limit_with_boost(user.id)
    await _w(bot, user.id,
             f"⛏️ Active Farm Boost\n"
             f"Boost remaining: ~{remaining}m\n"
             f"Total session limit: {total}m")


async def handle_giftfarmboost(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 3:
        await _w(bot, user.id,
                 "Usage: !giftfarmboost @user 15m|30m|60m")
        return
    target_name = args[1].lstrip("@")
    option_key  = args[2].lower()
    opt = _BOOST_OPTIONS.get(option_key)
    if not opt:
        await _w(bot, user.id, "Options: 15m | 30m | 60m")
        return
    await _w(bot, user.id,
             f"⛏️ Gift Farm Boost: {opt['minutes']}m to @{target_name}\n"
             f"Cost: {opt['gold']} gold — send to BankingBot.\n"
             f"BankingBot will apply the boost on confirmation.")


# ---------------------------------------------------------------------------
# Automine/autofish limit enforcement helper
# ---------------------------------------------------------------------------

def get_capped_duration(user_id: str, requested_minutes: int) -> tuple[int, str]:
    """
    Return (actual_minutes, message) respecting VIP/boost limits.
    Called from handle_automine / handle_autofish before starting the loop.
    """
    total, boost = get_farm_limit_with_boost(user_id)
    base         = get_base_farm_limit(user_id)

    if requested_minutes <= total:
        return requested_minutes, ""

    if base == _FREE_LIMIT and requested_minutes > _FREE_LIMIT:
        return _FREE_LIMIT, (
            f"Free AutoMine/AutoFish limit is {_FREE_LIMIT} minutes.\n"
            f"Upgrade to VIP for {_VIP_LIMIT}-minute sessions: !vip"
        )
    return total, f"Session capped at {total}m (your plan limit)."
