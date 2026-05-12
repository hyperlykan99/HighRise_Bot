"""
modules/gold_rain.py
--------------------
Gold Rain system — owned by BankerBot.

Commands:
  /goldrain <group> <total_gold> <winners>
  /goldrain test <group> <total_gold> <winners>
  /goldrain slow <group> <total> <winners> <interval_seconds>
  /goldrain party <group> <total> <winners> <interval_seconds>
  /raingold | /goldstorm | /golddrop    (aliases for /goldrain)
  /goldrainstatus
  /cancelgoldrain
  /goldrainhistory
  /goldraininterval
  /setgoldraininterval <seconds>
  /goldrainreplace on|off

Groups: all, vip, subs, players, nonstaff, staff, managers, admins, owners
Owner: BankerBot (banker bot-mode)
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Optional

import database as db
import modules.gold as _gold_mod
from modules.gold import (
    refresh_room_cache,
    _send_gold_bars,
    decompose_gold,
    _register_bot_id,
    is_gold_tip_eligible_user,
)
from modules.permissions import is_owner, is_admin, is_manager, is_moderator

# ---------------------------------------------------------------------------
# In-memory active rain state
# ---------------------------------------------------------------------------
_active_rain: Optional[dict] = None
_active_task: Optional[asyncio.Task] = None

# ---------------------------------------------------------------------------
# Group definitions
# ---------------------------------------------------------------------------
_GROUP_ALIASES: dict[str, str] = {
    "everyone":   "all",
    "subscriber": "subs",
    "subscribers":"subs",
    "vips":       "vip",
}

_PLAYER_GROUPS = frozenset({"all", "vip", "subs", "players", "nonstaff"})
_STAFF_GROUPS  = frozenset({"staff", "managers", "admins", "owners"})
_ALL_GROUPS    = _PLAYER_GROUPS | _STAFF_GROUPS

_GROUP_LABEL: dict[str, str] = {
    "all":     "All",
    "vip":     "VIP",
    "subs":    "Subs",
    "players": "Players",
    "nonstaff":"Non-Staff",
    "staff":   "Staff",
    "managers":"Managers",
    "admins":  "Admins",
    "owners":  "Owners",
}

# ---------------------------------------------------------------------------
# Pace system
# ---------------------------------------------------------------------------
_PACE_INTERVALS: dict[str, int] = {
    "slow":   15,
    "normal":  5,
    "party":   1,
}
_VALID_PACES = frozenset({"slow", "normal", "party", "custom"})
_PACE_LABEL: dict[str, str] = {
    "slow":   "Slow",
    "normal": "Normal",
    "party":  "Party",
    "custom": "Custom",
}

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

async def _w(bot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


async def _chat(bot, msg: str) -> None:
    try:
        await bot.highrise.chat(str(msg)[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

def _can_use_goldrain(username: str, group: str) -> bool:
    """Manager+ for player groups; admin/owner only for staff groups."""
    if is_owner(username) or is_admin(username):
        return True
    if is_manager(username):
        return group in _PLAYER_GROUPS
    return False


def _can_manage_gr(username: str) -> bool:
    """Manager+ for settings / history / interval / replace."""
    return is_owner(username) or is_admin(username) or is_manager(username)


# ---------------------------------------------------------------------------
# Gold Rain settings (stored in gold_rain_settings table)
# ---------------------------------------------------------------------------

def _get_gr_setting(key: str, default: str = "") -> str:
    try:
        val = db.get_gold_rain_setting(key)
        return val if val is not None else default
    except Exception:
        return default


def _get_pace() -> tuple[str, int]:
    """Return (pace_name, interval_seconds) from saved settings."""
    pace = _get_gr_setting("goldrain_pace", "normal").lower()
    if pace not in _VALID_PACES:
        pace = "normal"
    if pace == "custom":
        try:
            secs = int(_get_gr_setting("goldrain_custom_interval", "10"))
        except (ValueError, TypeError):
            secs = 10
        return "custom", max(1, min(300, secs))
    return pace, _PACE_INTERVALS.get(pace, 5)


def _get_default_interval() -> int:
    _, secs = _get_pace()
    return secs


def _get_replacement_on() -> bool:
    return _get_gr_setting("replacement_enabled", "true").lower() != "false"


# ---------------------------------------------------------------------------
# Room / eligibility helpers
# ---------------------------------------------------------------------------

def _is_in_room(user_id: str) -> bool:
    """Check whether a player is currently in the room cache."""
    for _key, (uid, _) in _gold_mod._room_cache.items():
        if uid == user_id:
            return True
    return False


def _get_eligible_for_group(group: str) -> list[tuple[str, str]]:
    """Return [(user_id, username)] currently in room matching the group."""
    vip_set: Optional[set[str]] = None
    sub_set: Optional[set[str]] = None

    if group == "vip":
        try:
            vip_set = {u.lower() for u in db.get_vip_list()}
        except Exception:
            vip_set = set()

    if group == "subs":
        try:
            rows = db.get_all_subscribed_users_for_notify()
            sub_set = {r["username"].lower() for r in rows if r.get("subscribed")}
        except Exception:
            sub_set = set()

    result: list[tuple[str, str]] = []

    for _key, (uid, uname) in _gold_mod._room_cache.items():
        if not is_gold_tip_eligible_user(uid, uname):
            continue

        ul = uname.lower()

        if group == "all":
            result.append((uid, uname))

        elif group == "vip":
            if vip_set is not None and ul in vip_set:
                result.append((uid, uname))

        elif group == "subs":
            if sub_set is not None and ul in sub_set:
                result.append((uid, uname))

        elif group in ("players", "nonstaff"):
            if not (is_owner(uname) or is_admin(uname)
                    or is_manager(uname) or is_moderator(uname)):
                result.append((uid, uname))

        elif group == "staff":
            if is_owner(uname) or is_admin(uname) or is_manager(uname) or is_moderator(uname):
                result.append((uid, uname))

        elif group == "managers":
            if is_owner(uname) or is_admin(uname) or is_manager(uname):
                result.append((uid, uname))

        elif group == "admins":
            if is_owner(uname) or is_admin(uname):
                result.append((uid, uname))

        elif group == "owners":
            if is_owner(uname):
                result.append((uid, uname))

    return result


# ---------------------------------------------------------------------------
# Payout helper
# ---------------------------------------------------------------------------

async def _pay_one_winner(
    bot, user_id: str, username: str, gold_amount: int,
) -> tuple[str, str]:
    """
    Tip gold_amount gold to one winner.
    Returns (payout_status, error_msg).
      payout_status: 'paid' | 'pending_manual'
    """
    bars = decompose_gold(gold_amount)
    if bars is None:
        return "pending_manual", f"Cannot form exact {gold_amount}g"

    ok, err = await _send_gold_bars(bot, user_id, bars)

    if err == "bot_user":
        _register_bot_id(user_id, username)
        return "pending_manual", "Target is a bot"

    if ok:
        return "paid", ""

    if err == "insufficient_funds":
        return "pending_manual", "Insufficient bot gold"

    return "pending_manual", err[:80]


# ---------------------------------------------------------------------------
# DB persistence helpers
# ---------------------------------------------------------------------------

def _create_db_event(
    mode: str, group: str, total_gold: int, winners_count: int,
    gold_each: int, interval: int, replacement: bool,
    user_id: str, username: str,
) -> int:
    try:
        return db.log_gold_rain_event(
            mode=mode, target_group=group, total_gold=total_gold,
            winners_count=winners_count, gold_each=gold_each,
            interval_seconds=interval,
            replacement_enabled=int(replacement),
            status="running",
            created_by_user_id=user_id,
            created_by_username=username.lower(),
        )
    except Exception as exc:
        print(f"[GOLDRAIN] DB create_event error: {exc}")
        return 0


def _log_winner(
    event_id: int, user_id: str, username: str,
    gold_amount: int, rank: int,
    payout_status: str, payout_error: str = "",
) -> None:
    try:
        db.log_gold_rain_winner(
            event_id=event_id, user_id=user_id,
            username=username.lower(), gold_amount=gold_amount,
            rank=rank, payout_status=payout_status,
            payout_error=payout_error,
        )
    except Exception as exc:
        print(f"[GOLDRAIN] DB log_winner error: {exc}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

async def _parse_group_total_winners(
    bot, user, args: list[str],
) -> Optional[tuple[str, int, int]]:
    """
    Parse [<group>, <total_gold>, <winners>] from args list.
    Returns (group, gold_each, winners_req) or None (whisper sent on error).
    """
    if len(args) < 3:
        await _w(bot, user.id, "🌧️ Usage: !goldrain <group> <total_gold> <winners>")
        return None

    raw_group = args[0].lower()
    group = _GROUP_ALIASES.get(raw_group, raw_group)

    if group not in _ALL_GROUPS:
        await _w(
            bot, user.id,
            f"🌧️ Unknown group '{raw_group}'.\n"
            "Groups: all subs vip players nonstaff staff managers admins owners",
        )
        return None

    if not _can_use_goldrain(user.username, group):
        await _w(bot, user.id,
                 "🌧️ Gold Rain\nYou do not have permission to target this group.")
        return None

    try:
        total_gold  = int(args[1])
        winners_req = int(args[2])
    except (ValueError, IndexError):
        await _w(bot, user.id,
                 "🌧️ Gold Rain\nTotal gold and winners must be whole numbers.")
        return None

    if total_gold < 1 or winners_req < 1:
        await _w(bot, user.id,
                 "🌧️ Gold Rain\nTotal gold and winners must be at least 1.")
        return None

    if total_gold % winners_req != 0:
        await _w(
            bot, user.id,
            f"🌧️ Gold Rain\n{total_gold}g ÷ {winners_req} doesn't divide evenly.\n"
            "Choose amounts that divide exactly.",
        )
        return None

    gold_each = total_gold // winners_req
    return group, gold_each, winners_req


# ---------------------------------------------------------------------------
# /goldrain  main dispatcher  (also /raingold /goldstorm /golddrop)
# ---------------------------------------------------------------------------

async def handle_goldrain(bot, user, args: list[str]) -> None:
    if len(args) < 2:
        pace_name, pace_iv = _get_pace()
        await _w(
            bot, user.id,
            "🌧️ Gold Rain\n"
            "!goldrain [group] [total_gold] [winners]\n"
            "!goldrain slow [group] [total] [winners] [secs]\n"
            "!goldrain party [group] [total] [winners] [secs]\n"
            "!goldrain test [group] [total] [winners]\n"
            f"Pace: {_PACE_LABEL.get(pace_name, pace_name)}  Interval: {pace_iv}s\n"
            "Groups: all subs vip players nonstaff",
        )
        return

    sub = args[1].lower()

    if sub == "test":
        await _handle_test(bot, user, args[2:])
        return

    if sub in ("slow", "party"):
        await _handle_slow(bot, user, args[2:], mode=sub)
        return

    # /goldrain pace [<pace>] [<custom_secs>]  — inline alias
    if sub == "pace":
        if len(args) <= 2:
            await handle_goldrainpace(bot, user, args)
        else:
            await handle_setgoldrainpace(bot, user, ["setgoldrainpace"] + args[2:])
        return

    # Normal instant mode: /goldrain <group> <total_gold> <winners>
    await _handle_normal(bot, user, args[1:])


# ---------------------------------------------------------------------------
# Test / dry-run mode
# ---------------------------------------------------------------------------

async def _handle_test(bot, user, args: list[str]) -> None:
    if not _can_use_goldrain(user.username, "all"):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    result = await _parse_group_total_winners(bot, user, args)
    if result is None:
        return
    group, gold_each, winners_req = result
    label = _GROUP_LABEL.get(group, group)

    await refresh_room_cache(bot)
    eligible = _get_eligible_for_group(group)
    n_winners = min(winners_req, len(eligible))

    lines = [
        "🧪 Gold Rain Test",
        f"Target: {label}",
        f"Eligible: {len(eligible)}",
        f"Would Pick Winners: {n_winners}",
        f"Reward Each: {gold_each}g",
        "No payout sent.",
    ]
    if len(eligible) < winners_req:
        lines.append(
            f"⚠️ Only {len(eligible)} eligible — fewer than {winners_req} requested."
        )
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# Normal (instant) mode
# ---------------------------------------------------------------------------

async def _handle_normal(bot, user, args: list[str]) -> None:
    global _active_rain, _active_task

    if _active_rain is not None:
        await _w(bot, user.id,
                 "🌧️ Gold Rain already running. Use !cancelgoldrain first.")
        return

    result = await _parse_group_total_winners(bot, user, args)
    if result is None:
        return
    group, gold_each, winners_req = result
    total_gold = gold_each * winners_req
    label = _GROUP_LABEL.get(group, group)

    await refresh_room_cache(bot)
    eligible = _get_eligible_for_group(group)

    if len(eligible) < winners_req:
        await _w(
            bot, user.id,
            f"🌧️ Gold Rain Error\n"
            f"Only {len(eligible)} eligible {label} players are in the room.\n"
            f"Use !goldrain {group} {total_gold} {len(eligible)}",
        )
        return

    chosen = random.sample(eligible, winners_req)

    event_id = _create_db_event(
        mode="normal", group=group, total_gold=total_gold,
        winners_count=winners_req, gold_each=gold_each,
        interval=0, replacement=_get_replacement_on(),
        user_id=user.id, username=user.username,
    )

    await _chat(
        bot,
        f"🌧️ Gold Rain Started!\n"
        f"Target: {label}\n"
        f"Total: {total_gold}g\n"
        f"Winners: {winners_req}\n"
        f"Reward: {gold_each}g each",
    )

    paid = 0
    pending_manual = 0

    for rank, (uid, uname) in enumerate(chosen, 1):
        pstatus, perr = await _pay_one_winner(bot, uid, uname, gold_each)
        if pstatus == "paid":
            paid += 1
            await _chat(bot, f"💸 Tipped @{uname} {gold_each}g gold!")
        else:
            pending_manual += gold_each
            await _chat(bot, f"💸 Logged @{uname} for {gold_each}g gold!")
        _log_winner(event_id, uid, uname, gold_each, rank, pstatus, perr)

    try:
        db.update_gold_rain_event(event_id, status="complete")
    except Exception:
        pass

    try:
        db.log_gold_tx(
            "goldrain", user.username, f"[{label}:{winners_req}]", "",
            total_gold, f"group={group}", "success", "", "", "",
        )
    except Exception:
        pass

    logged = pending_manual > 0
    await _w(
        bot, user.id,
        f"🌧️ Gold Rain Complete!\n"
        f"{paid}/{winners_req} winners received {gold_each}g each.\n"
        f"Total: {total_gold}g\n"
        f"Logged: {'YES' if logged else 'NO'}\n"
        f"Pending manual gold: {pending_manual}g",
    )


# ---------------------------------------------------------------------------
# Slow / Party mode  (timed, one tip per interval)
# ---------------------------------------------------------------------------

async def _handle_slow(bot, user, args: list[str], mode: str = "slow") -> None:
    global _active_rain, _active_task

    if _active_rain is not None:
        await _w(bot, user.id,
                 "🌧️ Gold Rain already running. Use !cancelgoldrain first.")
        return

    if len(args) < 3:
        pace_name, pace_iv = _get_pace()
        await _w(
            bot, user.id,
            f"🌧️ Usage: !goldrain {mode} <group> <total> <winners> [secs]\n"
            f"Current pace: {_PACE_LABEL.get(pace_name, pace_name)} ({pace_iv}s)",
        )
        return

    result = await _parse_group_total_winners(bot, user, args[:3])
    if result is None:
        return
    group, gold_each, winners_req = result
    total_gold = gold_each * winners_req
    label = _GROUP_LABEL.get(group, group)

    # Resolve interval: explicit arg overrides saved pace
    pace_name, pace_iv = _get_pace()
    if len(args) > 3:
        try:
            interval = max(1, min(300, int(args[3])))
        except (ValueError, IndexError):
            interval = pace_iv
        display_pace = f"{_PACE_LABEL.get(pace_name, pace_name)} (override {interval}s)"
    else:
        interval = pace_iv
        display_pace = _PACE_LABEL.get(pace_name, pace_name)

    await refresh_room_cache(bot)
    eligible = _get_eligible_for_group(group)

    if len(eligible) < winners_req:
        await _w(
            bot, user.id,
            f"🌧️ Gold Rain Error\n"
            f"Only {len(eligible)} eligible {label} players in room.\n"
            f"Use !goldrain {mode} {group} {total_gold} {len(eligible)} {interval}",
        )
        return

    chosen = random.sample(eligible, winners_req)
    replacement = _get_replacement_on()

    event_id = _create_db_event(
        mode=mode, group=group, total_gold=total_gold,
        winners_count=winners_req, gold_each=gold_each,
        interval=interval, replacement=replacement,
        user_id=user.id, username=user.username,
    )

    mode_label = "Party" if mode == "party" else "Slow"
    _active_rain = {
        "event_id":       event_id,
        "mode":           mode_label,
        "group":          group,
        "label":          label,
        "total_gold":     total_gold,
        "gold_each":      gold_each,
        "winners_req":    winners_req,
        "chosen":         chosen,
        "replacement":    replacement,
        "interval":       interval,
        "pace_label":     display_pace,
        "paid_count":     0,
        "paid_gold":      0,
        "pending_manual": 0,
        "tipper_id":      user.id,
        "tipper_username":user.username,
        "cancelled":      False,
        "next_tip_at":    0.0,
    }

    await _chat(
        bot,
        f"🌧️ Gold Rain {mode_label} Started!\n"
        f"Target: {label}  Total: {total_gold}g\n"
        f"Winners: {winners_req}  Reward: {gold_each}g each\n"
        f"Pace: {display_pace}  Interval: {interval}s\n"
        "Stay in the room for the giveaway!",
    )

    _active_task = asyncio.create_task(
        _slow_rain_loop(
            bot, event_id, chosen, group, label,
            gold_each, interval, replacement,
            winners_req, user.id, user.username,
        )
    )


# ---------------------------------------------------------------------------
# Slow rain loop  (runs as asyncio.Task)
# ---------------------------------------------------------------------------

async def _slow_rain_loop(
    bot,
    event_id: int,
    chosen: list[tuple[str, str]],
    group: str,
    label: str,
    gold_each: int,
    interval: int,
    replacement: bool,
    winners_req: int,
    tipper_id: str,
    tipper_username: str,
) -> None:
    global _active_rain, _active_task

    paid = 0
    pending_manual = 0
    skipped = 0
    chosen_ids: set[str] = {uid for uid, _ in chosen}
    replacement_pool: list[tuple[str, str]] = []

    for rank, (uid, uname) in enumerate(chosen, 1):

        if _active_rain and _active_rain.get("cancelled"):
            break

        # Delay before every tip except the first
        if rank > 1:
            if _active_rain:
                _active_rain["next_tip_at"] = time.time() + interval
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

        if _active_rain:
            _active_rain["next_tip_at"] = 0.0

        if _active_rain and _active_rain.get("cancelled"):
            break

        tip_uid, tip_uname = uid, uname

        # --- Check winner is still in room ---
        if not _is_in_room(uid):
            if replacement:
                # Populate replacement pool lazily on first need
                if not replacement_pool:
                    await refresh_room_cache(bot)
                    all_elig = _get_eligible_for_group(group)
                    replacement_pool = [
                        (u, n) for u, n in all_elig if u not in chosen_ids
                    ]
                # Find a replacement currently in room
                avail = [(u, n) for u, n in replacement_pool if _is_in_room(u)]
                if avail:
                    repl = random.choice(avail)
                    tip_uid, tip_uname = repl
                    replacement_pool.remove(repl)
                    chosen_ids.add(tip_uid)
                    await _chat(
                        bot,
                        f"🌧️ @{uname} left before their turn. Picking another winner...",
                    )
                else:
                    await _w(
                        bot, tipper_id,
                        f"🌧️ Gold Rain Notice\n"
                        f"@{uname} left before their turn.\n"
                        "No replacement available.",
                    )
                    _log_winner(
                        event_id, uid, uname, gold_each, rank,
                        "skipped_left_room", "no replacement",
                    )
                    skipped += 1
                    if _active_rain:
                        _active_rain["paid_count"] = paid
                    continue
            else:
                _log_winner(
                    event_id, uid, uname, gold_each, rank,
                    "skipped_left_room", "replacement disabled",
                )
                skipped += 1
                if _active_rain:
                    _active_rain["paid_count"] = paid
                continue

        # --- Pay this winner ---
        pstatus, perr = await _pay_one_winner(bot, tip_uid, tip_uname, gold_each)
        if pstatus == "paid":
            paid += 1
            await _chat(bot, f"💸 Tipped @{tip_uname} {gold_each}g gold!")
        else:
            pending_manual += gold_each
            await _chat(bot, f"💸 Logged @{tip_uname} for {gold_each}g gold!")

        _log_winner(event_id, tip_uid, tip_uname, gold_each, rank, pstatus, perr)

        if _active_rain:
            _active_rain["paid_count"] = paid
            _active_rain["paid_gold"]  = paid * gold_each
            _active_rain["pending_manual"] = pending_manual

    # ---- Loop complete or cancelled ----
    cancelled  = bool(_active_rain and _active_rain.get("cancelled"))
    total_paid = paid * gold_each

    if cancelled:
        try:
            db.update_gold_rain_event(event_id, status="cancelled")
        except Exception:
            pass
        await _chat(bot, "🌧️ Gold Rain Cancelled.")
        await _w(
            bot, tipper_id,
            f"🌧️ Gold Rain Cancelled\n"
            f"Paid: {paid}/{winners_req}\n"
            f"Cancelled remaining: {winners_req - paid - skipped}\n"
            f"Total paid/logged: {total_paid}g",
        )
    else:
        try:
            db.update_gold_rain_event(event_id, status="complete")
        except Exception:
            pass
        logged = pending_manual > 0
        await _w(
            bot, tipper_id,
            f"🌧️ Gold Rain Complete!\n"
            f"{paid}/{winners_req} winners received {gold_each}g each.\n"
            f"Total: {total_paid}g\n"
            f"Logged: {'YES' if logged else 'NO'}\n"
            f"Pending manual gold: {pending_manual}g",
        )

    _active_rain = None
    _active_task = None


# ---------------------------------------------------------------------------
# /goldrainstatus
# ---------------------------------------------------------------------------

async def handle_goldrainstatus(bot, user, args=None) -> None:
    pace_name, pace_iv = _get_pace()
    pace_disp = _PACE_LABEL.get(pace_name, pace_name)
    if _active_rain is None:
        await _w(
            bot, user.id,
            f"🌧️ Gold Rain Status\n"
            f"No active Gold Rain right now.\n"
            f"Current Pace: {pace_disp}  Interval: {pace_iv}s",
        )
        return

    r         = _active_rain
    paid      = r.get("paid_count", 0)
    total     = r.get("winners_req", 0)
    repl      = "ON" if r.get("replacement") else "OFF"
    next_at   = r.get("next_tip_at", 0.0)
    secs_left = max(0, int(next_at - time.time())) if next_at > 0 else 0
    iv        = r.get("interval", 0)
    pl        = r.get("pace_label", pace_disp)

    parts = [
        "🌧️ Gold Rain Status",
        f"Mode: {r.get('mode', '?')}  Target: {r.get('label', '?')}",
        f"Total: {r.get('total_gold', '?')}g  Reward: {r.get('gold_each', '?')}g ea",
        f"Pace: {pl}  Interval: {iv}s",
        f"Paid: {paid}/{total}",
        (f"Next Tip In: {secs_left}s" if secs_left > 0
         else f"Remaining: {total - paid}"),
        f"Replacement: {repl}",
    ]
    await _w(bot, user.id, "\n".join(parts)[:249])


# ---------------------------------------------------------------------------
# /cancelgoldrain
# ---------------------------------------------------------------------------

async def handle_cancelgoldrain(bot, user, args=None) -> None:
    global _active_rain, _active_task

    if _active_rain is None:
        await _w(bot, user.id, "🌧️ No active Gold Rain to cancel.")
        return

    tipper_id = _active_rain.get("tipper_id", "")
    is_tipper = user.id == tipper_id

    if not (_can_manage_gr(user.username) or is_tipper):
        await _w(bot, user.id,
                 "Manager/admin/owner or the original tipper can cancel.")
        return

    paid      = _active_rain.get("paid_count", 0)
    total     = _active_rain.get("winners_req", 0)
    paid_gold = _active_rain.get("paid_gold", 0)
    eid       = _active_rain.get("event_id", 0)

    _active_rain["cancelled"] = True
    if _active_task and not _active_task.done():
        _active_task.cancel()

    try:
        db.update_gold_rain_event(eid, status="cancelled")
    except Exception:
        pass

    _active_rain = None
    _active_task = None

    await _chat(bot, "🌧️ Gold Rain Cancelled.")
    await _w(
        bot, user.id,
        f"🌧️ Gold Rain Cancelled\n"
        f"Paid: {paid}/{total}\n"
        f"Cancelled remaining: {total - paid}\n"
        f"Total paid/logged: {paid_gold}g",
    )


# ---------------------------------------------------------------------------
# /goldrainhistory
# ---------------------------------------------------------------------------

async def handle_goldrainhistory(bot, user, args=None) -> None:
    if not _can_manage_gr(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    rows = db.get_gold_rain_history(limit=8)
    if not rows:
        await _w(bot, user.id, "🌧️ Gold Rain History\nNo events yet.")
        return

    lines = ["🌧️ Gold Rain History"]
    for i, r in enumerate(rows, 1):
        mode   = r.get("mode", "?").capitalize()
        grp    = _GROUP_LABEL.get(r.get("target_group", "?"),
                                  r.get("target_group", "?"))
        total  = int(r.get("total_gold", 0))
        wins   = r.get("winners_count", 0)
        status = r.get("status", "?")
        lines.append(f"{i}. {mode} — {grp} — {total}g — {wins} winners — {status}")

    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /goldraininterval
# ---------------------------------------------------------------------------

async def handle_goldraininterval(bot, user, args=None) -> None:
    pace_name, interval = _get_pace()
    pace_disp = _PACE_LABEL.get(pace_name, pace_name)
    await _w(
        bot, user.id,
        f"🌧️ Gold Rain Interval\n"
        f"Pace: {pace_disp}  Interval: {interval}s\n"
        "Change: /setgoldraininterval <secs>  (manager+)\n"
        "Set pace: /setgoldrainpace slow|normal|party|custom",
    )


# ---------------------------------------------------------------------------
# /setgoldraininterval <seconds>
# ---------------------------------------------------------------------------

async def handle_setgoldraininterval(bot, user, args: list[str]) -> None:
    if not _can_manage_gr(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    if len(args) < 2:
        await _w(bot, user.id, "Usage: !setgoldraininterval <seconds>  (1–300)")
        return

    try:
        secs = int(args[1])
    except ValueError:
        await _w(bot, user.id, "Seconds must be a whole number.")
        return

    if secs < 1 or secs > 300:
        await _w(bot, user.id, "🌧️ Interval must be between 1 and 300 seconds.")
        return

    db.set_gold_rain_setting("goldrain_pace", "custom")
    db.set_gold_rain_setting("goldrain_custom_interval", str(secs))
    await _w(
        bot, user.id,
        f"🌧️ Gold Rain Interval Updated\nPace: Custom\nInterval: {secs}s",
    )


# ---------------------------------------------------------------------------
# /goldrainreplace on|off
# ---------------------------------------------------------------------------

async def handle_goldrainreplace(bot, user, args: list[str]) -> None:
    if not _can_manage_gr(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        current = "ON" if _get_replacement_on() else "OFF"
        await _w(
            bot, user.id,
            f"🌧️ Gold Rain Replacement\nCurrent: {current}\n"
            "Usage: !goldrainreplace on|off",
        )
        return

    enabled = args[1].lower() == "on"
    db.set_gold_rain_setting(
        "replacement_enabled", "true" if enabled else "false"
    )
    state = "ON" if enabled else "OFF"
    await _w(bot, user.id, f"🌧️ Gold Rain Replacement: {state}")


# ---------------------------------------------------------------------------
# /goldrainpace  — show current pace
# ---------------------------------------------------------------------------

async def handle_goldrainpace(bot, user, args=None) -> None:
    if not _can_manage_gr(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    pace_name, interval = _get_pace()
    pace_disp = _PACE_LABEL.get(pace_name, pace_name)
    await _w(
        bot, user.id,
        f"🌧️ Gold Rain Pace\n"
        f"Current: {pace_disp}  Interval: {interval}s\n"
        f"slow=15s  normal=5s  party=1s  custom=set your own\n"
        "Change: /setgoldrainpace slow|normal|party|custom [secs]",
    )


# ---------------------------------------------------------------------------
# /setgoldrainpace <pace> [seconds]
# ---------------------------------------------------------------------------

async def handle_setgoldrainpace(bot, user, args: list[str]) -> None:
    if not _can_manage_gr(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    if len(args) < 2:
        await _w(
            bot, user.id,
            "🌧️ Usage: !setgoldrainpace slow|normal|party|custom [secs]\n"
            "Example: /setgoldrainpace party\n"
            "Example: /setgoldrainpace custom 8",
        )
        return

    pace = args[1].lower()
    if pace not in _VALID_PACES:
        await _w(
            bot, user.id,
            f"🌧️ Unknown pace '{pace}'.\n"
            "Options: slow  normal  party  custom",
        )
        return

    if pace == "custom":
        if len(args) < 3:
            await _w(
                bot, user.id,
                "🌧️ Custom pace needs seconds.\n"
                "Usage: !setgoldrainpace custom <seconds>  (1–300)",
            )
            return
        try:
            secs = max(1, min(300, int(args[2])))
        except ValueError:
            await _w(bot, user.id, "Seconds must be a whole number.")
            return
        db.set_gold_rain_setting("goldrain_pace", "custom")
        db.set_gold_rain_setting("goldrain_custom_interval", str(secs))
        await _w(
            bot, user.id,
            f"🌧️ Gold Rain Pace Updated\nPace: Custom\nInterval: {secs}s",
        )
        return

    db.set_gold_rain_setting("goldrain_pace", pace)
    interval = _PACE_INTERVALS[pace]
    label    = _PACE_LABEL[pace]
    await _w(
        bot, user.id,
        f"🌧️ Gold Rain Pace Updated\nPace: {label}\nInterval: {interval}s",
    )
