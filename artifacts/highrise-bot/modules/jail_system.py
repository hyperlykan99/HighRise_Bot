"""modules/jail_system.py — Main command handler for the Luxe Jail system (3.4A).

Owned by SecurityBot. All other bots:
  - enforce jail block on !tele / !goto / !bring attempts
  - call handle_jail_confirm / handle_jail_cancel when a player types confirm/cancel
  - call enforce_jail_on_rejoin from on_user_join

Commands:
  Player:     !jail @user [mins], !bail [@user], !jailstatus, !jailtime, !jailhelp
  Staff/Admin: !unjail @user, !jailrelease @user, !jailadmin, !jailactive,
               !jailsetcost, !jailsetmax, !jailsetmin, !jailsetbailmultiplier,
               !jailprotectstaff on|off, !jaildebug
  Owner setup: !setjailspot, !setjailguardspot, !setsecurityidle
"""
from __future__ import annotations
import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot, User

import database as db
from modules.permissions import is_owner, is_admin, is_manager, is_moderator
from modules.jail_config import (
    is_jail_enabled, cost_per_minute, min_minutes, max_minutes, default_minutes,
    bail_multiplier, cooldown_seconds, daily_limit, confirm_required,
    jail_spot_name, guard_spot_name, idle_spot_name,
    set_cost_per_minute, set_max_minutes, set_min_minutes, set_bail_multiplier,
    set_protect_staff, set_confirm_required, set_jail_enabled,
)
from modules.jail_pricing import cost_for_jail, bail_cost_for
from modules.jail_permissions import can_actor_jail_target
from modules.jail_store import (
    create_sentence, get_active_sentence, get_all_active_sentences,
    mark_released, count_today_jails_by, get_last_jail_by, log_jail_action,
)
from modules.jail_enforcement import is_jailed, remaining_seconds, jail_block_message
from modules.jail_bail import (
    initiate_bail, complete_bail, get_bail_pending, clear_bail_pending,
)

# ── In-memory jail purchase pending ──────────────────────────────────────────
# user_id → {target_uid, target_uname, minutes, cost, bail_cost, expires_at}
_JAIL_PENDING: dict[str, dict] = {}
JAIL_CONFIRM_TTL = 60


def _set_jail_pending(
    user_id: str,
    target_uid: str,
    target_uname: str,
    minutes: int,
    cost: int,
    bail: int,
) -> None:
    _JAIL_PENDING[user_id] = {
        "target_uid":   target_uid,
        "target_uname": target_uname,
        "minutes":      minutes,
        "cost":         cost,
        "bail_cost":    bail,
        "expires_at":   time.time() + JAIL_CONFIRM_TTL,
    }


def get_jail_pending(user_id: str) -> dict | None:
    p = _JAIL_PENDING.get(user_id)
    if p and time.time() < p["expires_at"]:
        return p
    _JAIL_PENDING.pop(user_id, None)
    return None


def clear_jail_pending(user_id: str) -> None:
    _JAIL_PENDING.pop(user_id, None)


# ── Permission helpers ────────────────────────────────────────────────────────

def _perm_level(username: str) -> str:
    low = username.lower()
    if is_owner(low):     return "owner"
    if is_admin(low):     return "admin"
    if is_manager(low):   return "manager"
    if is_moderator(low): return "moderator"
    return "player"


def _is_staff_plus(username: str) -> bool:
    low = username.lower()
    return is_owner(low) or is_admin(low) or is_manager(low)


# ── Room user lookup ──────────────────────────────────────────────────────────

async def _find_user_in_room(bot: "BaseBot", username: str):
    """Return (User, Position) or None if the player is not in the room."""
    target_low = username.lower().lstrip("@")
    try:
        resp = await bot.highrise.get_room_users()
        users = resp.content if hasattr(resp, "content") else resp
        for u, pos in users:
            if u.username.lower() == target_low:
                return u, pos
    except Exception as e:
        print(f"[JAIL SYSTEM] _find_user_in_room err: {e!r}")
    return None


# ── Confirm / Cancel dispatch (called from main.py on_chat) ──────────────────

async def handle_jail_confirm(bot: "BaseBot", user: "User") -> bool:
    """
    Handle a 'confirm' message for any pending jail or bail action.
    Returns True if an action was found and processed (consumes the event).
    """
    bail_p = get_bail_pending(user.id)
    if bail_p:
        await complete_bail(bot, user)
        return True
    jp = get_jail_pending(user.id)
    if jp:
        await _execute_jail(bot, user, jp)
        clear_jail_pending(user.id)
        return True
    return False


async def handle_jail_cancel(bot: "BaseBot", user: "User") -> bool:
    """Cancel any pending jail or bail for this user. Returns True if something was cancelled."""
    found = False
    if get_bail_pending(user.id):
        clear_bail_pending(user.id)
        await bot.highrise.send_whisper(user.id, "Bail cancelled.")
        found = True
    if get_jail_pending(user.id):
        clear_jail_pending(user.id)
        await bot.highrise.send_whisper(user.id, "Jail request cancelled.")
        found = True
    return found


# ── Core jail execution ───────────────────────────────────────────────────────

async def _execute_jail(bot: "BaseBot", actor: "User", jp: dict) -> None:
    """Deduct tickets, create sentence, teleport, announce. Called after confirm."""
    from modules.luxe import get_luxe_balance, deduct_luxe_balance, add_luxe_balance
    from modules.securitybot_jail import (
        teleport_player_to_jail, brief_and_return, is_security_bot,
    )
    target_uid   = jp["target_uid"]
    target_uname = jp["target_uname"]
    minutes      = jp["minutes"]
    cost         = jp["cost"]
    bail         = jp["bail_cost"]

    # Re-validate target still in room
    found = await _find_user_in_room(bot, target_uname)
    if not found:
        await bot.highrise.send_whisper(actor.id, f"{target_uname} is no longer in the room.")
        return

    # Re-check already jailed
    if is_jailed(target_uid):
        await bot.highrise.send_whisper(actor.id, f"{target_uname} is already jailed.")
        return

    # Deduct tickets
    bal = get_luxe_balance(actor.id)
    if bal < cost:
        await bot.highrise.send_whisper(
            actor.id,
            f"Need {cost} \U0001f3ab to jail {target_uname}. You have {bal}."[:249],
        )
        return
    ok = deduct_luxe_balance(actor.id, actor.username, cost)
    if not ok:
        await bot.highrise.send_whisper(actor.id, "Ticket deduction failed. Try again.")
        return

    # Create sentence
    sid = create_sentence(
        target_uid, target_uname,
        actor.id, actor.username,
        minutes * 60, bail,
        reason="luxe_jail",
    )
    log_jail_action(
        "jail_purchase", target_uid, target_uname,
        actor.id, actor.username, cost, f"mins={minutes}",
    )
    print(
        f"[JAIL] sentence_id={sid} target={target_uname!r} "
        f"by={actor.username!r} mins={minutes} cost={cost} bail={bail}"
    )

    # Teleport target to jail
    jail_ok = False
    if is_security_bot():
        jail_ok = await teleport_player_to_jail(bot, target_uid, target_uname)
    else:
        try:
            spawn = db.get_spawn(jail_spot_name())
            if spawn:
                from highrise.models import Position
                pos = Position(spawn["x"], spawn["y"], spawn["z"], spawn["facing"])
                await bot.highrise.teleport(target_uid, pos)
                jail_ok = True
        except Exception as e:
            print(f"[JAIL] fallback teleport err: {e!r}")

    if not jail_ok:
        from modules.jail_store import mark_released
        mark_released(sid)
        add_luxe_balance(actor.id, actor.username, cost)
        await bot.highrise.send_whisper(
            actor.id,
            "Jail spot not set. Tickets refunded. Ask owner to use !setjailspot."[:249],
        )
        return

    # Whisper jailed player
    try:
        await bot.highrise.send_whisper(
            target_uid,
            (
                f"\U0001f6a8 Jailed by {actor.username} for {minutes} min. "
                f"Bail: {bail} \U0001f3ab. Type !bail."
            )[:249],
        )
    except Exception:
        pass

    # Whisper actor
    await bot.highrise.send_whisper(
        actor.id,
        f"\u2705 {target_uname} jailed for {minutes} min. {cost} \U0001f3ab deducted."[:249],
    )

    # SecurityBot: guard spot → announce → return to idle
    if is_security_bot():
        asyncio.create_task(brief_and_return(bot, target_uname, actor.username, minutes, bail))
    else:
        try:
            await bot.highrise.chat(
                (
                    f"\U0001f6a8 {target_uname} jailed by {actor.username} for {minutes} min. "
                    f"Bail: {bail} \U0001f3ab. Type !bail."
                )[:249]
            )
        except Exception:
            pass


# ── !jail @user [mins] ────────────────────────────────────────────────────────

async def handle_jail(bot: "BaseBot", user: "User", args: list[str]) -> None:
    from modules.luxe import get_luxe_balance
    if not is_jail_enabled():
        await bot.highrise.send_whisper(user.id, "Jail is currently disabled.")
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: !jail @user [minutes]")
        return

    target_name = args[1].lstrip("@")
    minutes = default_minutes()
    if len(args) >= 3:
        try:
            minutes = int(args[2])
        except ValueError:
            await bot.highrise.send_whisper(
                user.id, f"Invalid duration. Use {min_minutes()}–{max_minutes()} min."
            )
            return

    if not (min_minutes() <= minutes <= max_minutes()):
        await bot.highrise.send_whisper(
            user.id,
            f"Duration must be {min_minutes()}–{max_minutes()} minutes."[:249],
        )
        return

    actor_perm = _perm_level(user.username)
    allowed, reason = can_actor_jail_target(user.username, actor_perm, target_name)
    if not allowed:
        await bot.highrise.send_whisper(user.id, reason[:249])
        return

    found = await _find_user_in_room(bot, target_name)
    if not found:
        await bot.highrise.send_whisper(user.id, f"{target_name} is not in the room.")
        return
    target_user, _ = found

    if is_jailed(target_user.id):
        secs = remaining_seconds(target_user.id)
        m = int(secs // 60); s = int(secs % 60)
        await bot.highrise.send_whisper(
            user.id,
            f"{target_user.username} is already jailed ({m}m {s}s left)."[:249],
        )
        return

    # Cooldown
    cd = cooldown_seconds()
    if cd > 0:
        last    = get_last_jail_by(user.id)
        elapsed = time.time() - last
        if elapsed < cd:
            left = int(cd - elapsed)
            m = left // 60; s = left % 60
            await bot.highrise.send_whisper(
                user.id, f"Jail cooldown: {m}m {s}s remaining."[:249]
            )
            return

    # Daily limit
    dl = daily_limit()
    if dl > 0 and count_today_jails_by(user.id) >= dl:
        await bot.highrise.send_whisper(
            user.id, f"Daily jail limit reached ({dl} jails)."[:249]
        )
        return

    cost = cost_for_jail(minutes)
    bail = bail_cost_for(cost)
    bal  = get_luxe_balance(user.id)
    if bal < cost:
        await bot.highrise.send_whisper(
            user.id,
            f"Jailing {target_user.username} for {minutes} min = {cost} \U0001f3ab. "
            f"You have {bal}."[:249],
        )
        return

    if confirm_required():
        _set_jail_pending(user.id, target_user.id, target_user.username, minutes, cost, bail)
        await bot.highrise.send_whisper(
            user.id,
            (
                f"\U0001f6a8 Jail {target_user.username} for {minutes} min? "
                f"Cost: {cost} \U0001f3ab | Bail: {bail} \U0001f3ab. "
                "Reply confirm or cancel. (60s)"
            )[:249],
        )
    else:
        jp = {
            "target_uid": target_user.id, "target_uname": target_user.username,
            "minutes": minutes, "cost": cost, "bail_cost": bail,
        }
        await _execute_jail(bot, user, jp)


# ── !bail [@user] ─────────────────────────────────────────────────────────────

async def handle_bail(bot: "BaseBot", user: "User", args: list[str]) -> None:
    target = args[1].lstrip("@") if len(args) >= 2 else user.username
    await initiate_bail(bot, user, target)


# ── !jailstatus / !jailtime ───────────────────────────────────────────────────

async def handle_jailstatus(bot: "BaseBot", user: "User", args: list[str]) -> None:
    s = get_active_sentence(user.id)
    if not s:
        await bot.highrise.send_whisper(user.id, "You are not jailed.")
        return
    secs = int(remaining_seconds(user.id))
    if secs <= 0:
        await bot.highrise.send_whisper(user.id, "Your sentence expired. You're free.")
        return
    m = secs // 60; sec_ = secs % 60
    msg = (
        f"\U0001f6a8 Jailed by {s['jailed_by_username']} | "
        f"{m}m {sec_}s left | Bail: {s['bail_cost']} \U0001f3ab | !bail"
    )[:249]
    await bot.highrise.send_whisper(user.id, msg)


# ── !jailhelp ─────────────────────────────────────────────────────────────────

async def handle_jailtime(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """Alias for !jailstatus."""
    await handle_jailstatus(bot, user, args)


async def handle_jailhelp(bot: "BaseBot", user: "User", args: list[str]) -> None:
    cpm = cost_per_minute()
    bm  = bail_multiplier()
    msg = (
        f"\U0001f6a8 Luxe Jail | !jail @user [mins] | !bail | !bail @user | "
        f"!jailstatus | {cpm} \U0001f3ab/min | Bail: {bm}x cost | Staff/bots protected."
    )[:249]
    await bot.highrise.send_whisper(user.id, msg)


# ── !unjail / !jailrelease ────────────────────────────────────────────────────

async def handle_unjail(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _is_staff_plus(user.username):
        await bot.highrise.send_whisper(user.id, "\U0001f512 Admin+ only.")
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: !unjail @user")
        return
    target_name = args[1].lstrip("@")
    conn = db.get_connection()
    row = conn.execute(
        "SELECT * FROM jail_sentences WHERE target_username=? AND status='active' "
        "ORDER BY id DESC LIMIT 1",
        (target_name.lower(),),
    ).fetchone()
    conn.close()
    if not row:
        await bot.highrise.send_whisper(user.id, f"{target_name} is not jailed.")
        return
    s = dict(row)
    mark_released(s["id"])
    log_jail_action("unjail", s["target_user_id"], s["target_username"],
                    user.id, user.username, 0, "staff_release")
    try:
        spawn = db.get_spawn("default") or db.get_spawn("main")
        if spawn:
            from highrise.models import Position
            pos = Position(spawn["x"], spawn["y"], spawn["z"], spawn["facing"])
            await bot.highrise.teleport(s["target_user_id"], pos)
    except Exception:
        pass
    try:
        await bot.highrise.send_whisper(
            s["target_user_id"],
            f"\u2705 Released from jail by {user.username}."[:249],
        )
    except Exception:
        pass
    await bot.highrise.send_whisper(user.id, f"\u2705 {target_name} released from jail.")


# ── !jailactive ───────────────────────────────────────────────────────────────

async def handle_jailactive(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _is_staff_plus(user.username):
        await bot.highrise.send_whisper(user.id, "\U0001f512 Admin+ only.")
        return
    active = get_all_active_sentences()
    if not active:
        await bot.highrise.send_whisper(user.id, "No active jail sentences.")
        return
    now   = time.time()
    lines = []
    for s in active[:5]:
        left = max(0, int(s["end_ts"] - now))
        m = left // 60; sec_ = left % 60
        lines.append(f"{s['target_username']} ({m}m {sec_}s)")
    await bot.highrise.send_whisper(user.id, ("Jailed: " + ", ".join(lines))[:249])


# ── !jailadmin ────────────────────────────────────────────────────────────────

async def handle_jailadmin(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _is_staff_plus(user.username):
        await bot.highrise.send_whisper(user.id, "\U0001f512 Admin+ only.")
        return
    enabled = "ON" if is_jail_enabled() else "OFF"
    msg = (
        f"\U0001f6a8 Jail {enabled} | {cost_per_minute()}\U0001f3ab/min | "
        f"Min:{min_minutes()}m Max:{max_minutes()}m | "
        f"Bail:{bail_multiplier()}x | CD:{cooldown_seconds()//60}m | "
        f"DL:{daily_limit()}"
    )[:249]
    await bot.highrise.send_whisper(user.id, msg)


# ── Settings commands (admin+) ────────────────────────────────────────────────

async def handle_jailsetcost(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _is_staff_plus(user.username):
        await bot.highrise.send_whisper(user.id, "\U0001f512 Admin+ only.")
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(
            user.id, f"Usage: !jailsetcost <tickets/min> (current: {cost_per_minute()})"
        )
        return
    try:
        val = max(1, int(args[1]))
    except ValueError:
        await bot.highrise.send_whisper(user.id, "Invalid value.")
        return
    set_cost_per_minute(val)
    await bot.highrise.send_whisper(user.id, f"\u2705 Jail cost: {val} \U0001f3ab/min.")


async def handle_jailsetmax(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _is_staff_plus(user.username):
        await bot.highrise.send_whisper(user.id, "\U0001f512 Admin+ only.")
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(
            user.id, f"Usage: !jailsetmax <minutes> (current: {max_minutes()})"
        )
        return
    try:
        val = max(1, int(args[1]))
    except ValueError:
        await bot.highrise.send_whisper(user.id, "Invalid value.")
        return
    set_max_minutes(val)
    await bot.highrise.send_whisper(user.id, f"\u2705 Max jail: {val} min.")


async def handle_jailsetmin(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _is_staff_plus(user.username):
        await bot.highrise.send_whisper(user.id, "\U0001f512 Admin+ only.")
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(
            user.id, f"Usage: !jailsetmin <minutes> (current: {min_minutes()})"
        )
        return
    try:
        val = max(1, int(args[1]))
    except ValueError:
        await bot.highrise.send_whisper(user.id, "Invalid value.")
        return
    set_min_minutes(val)
    await bot.highrise.send_whisper(user.id, f"\u2705 Min jail: {val} min.")


async def handle_jailsetbailmultiplier(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _is_staff_plus(user.username):
        await bot.highrise.send_whisper(user.id, "\U0001f512 Admin+ only.")
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(
            user.id, f"Usage: !jailsetbailmultiplier <n> (current: {bail_multiplier()})"
        )
        return
    try:
        val = max(1, int(args[1]))
    except ValueError:
        await bot.highrise.send_whisper(user.id, "Invalid value.")
        return
    set_bail_multiplier(val)
    await bot.highrise.send_whisper(user.id, f"\u2705 Bail multiplier: {val}x.")


async def handle_jailprotectstaff(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not is_owner(user.username.lower()):
        await bot.highrise.send_whisper(user.id, "\U0001f512 Owner only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        await bot.highrise.send_whisper(user.id, "Usage: !jailprotectstaff on|off")
        return
    val = args[1].lower() == "on"
    set_protect_staff(val)
    state = "ON" if val else "OFF"
    await bot.highrise.send_whisper(user.id, f"\u2705 Staff jail protection: {state}.")


async def handle_jaildebug(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _is_staff_plus(user.username):
        await bot.highrise.send_whisper(user.id, "\U0001f512 Admin+ only.")
        return
    from modules.securitybot_jail import is_security_bot
    from modules.jail_config import (
        jail_spot_name, guard_spot_name, idle_spot_name, release_spot_name,
    )
    active = len(get_all_active_sentences())
    spots = {
        "jail":          jail_spot_name(),
        "jail_guard":    guard_spot_name(),
        "security_idle": idle_spot_name(),
        "jail_release":  release_spot_name(),
    }
    spot_cmd = {
        "jail":          "setjailspot",
        "jail_guard":    "setjailguardspot",
        "security_idle": "setsecurityidle",
        "jail_release":  "setjailreleasespot",
    }
    lines = []
    for label, key in spots.items():
        row = db.get_spawn(key)
        if row:
            lines.append(
                f"{label}: \u2705 ({row['x']:.1f},{row['y']:.1f},{row['z']:.1f})"
            )
        else:
            lines.append(f"{label}: \u274c not set (!{spot_cmd[label]})")
    enabled = "ON" if is_jail_enabled() else "OFF"
    header = (
        f"\U0001f6a8 Jail {enabled} | Active:{active} | SecBot:{is_security_bot()}"
    )
    # Send header then spots (two whispers to fit ≤249 chars each)
    await bot.highrise.send_whisper(user.id, header[:249])
    await bot.highrise.send_whisper(user.id, " | ".join(lines)[:249])


# ── Spawn-setup helpers ───────────────────────────────────────────────────────

async def _get_user_position(bot: "BaseBot", user_id: str):
    """
    Return the cached Position for user_id, or fetch live from the SDK.
    Returns None if the position cannot be determined.
    Same pattern as handle_setbotspawnhere in room_utils.py.
    """
    from highrise.models import Position as _Pos
    from modules.room_utils import _user_positions, _user_position_times
    pos = _user_positions.get(user_id)
    if pos:
        return pos
    # Live fallback via SDK
    try:
        resp = await bot.highrise.get_room_users()
        users = resp.content if hasattr(resp, "content") else []
        for ru, rp in users:
            if ru.id == user_id:
                if isinstance(rp, _Pos):
                    _user_positions[user_id]      = rp
                    _user_position_times[user_id] = time.time()
                    return rp
                break
    except Exception as e:
        print(f"[JAIL SETUP] live position fetch error: {e!r}")
    return None


# ── Spawn-setup commands ──────────────────────────────────────────────────────

async def handle_setjailspot(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _is_staff_plus(user.username):
        await bot.highrise.send_whisper(user.id, "\U0001f512 Admin+ only.")
        return
    from config import SECURITY_BOT_NAME
    import os as _os
    _mode = _os.getenv("BOT_MODE", "unknown")
    _bname = _os.getenv("BOT_USERNAME", _mode)
    print(f"[JAIL SECURITY] security_bot_name={SECURITY_BOT_NAME} "
          f"current_bot_name={_bname} current_mode={_mode} setup_allowed=true")
    pos = await _get_user_position(bot, user.id)
    print(f"[JAIL SETUP] command=setjailspot user={user.username} "
          f"position_found={pos is not None}")
    if not pos:
        await bot.highrise.send_whisper(
            user.id,
            "\u26a0\ufe0f I can't read your current position yet. "
            "Move one step and try again."[:249],
        )
        return
    name = jail_spot_name()
    facing = getattr(pos, "facing", "FrontRight")
    db.save_spawn(name, pos.x, pos.y, pos.z, facing, user.username)
    print(f"[JAIL SETUP] saved {name} x={pos.x} y={pos.y} z={pos.z} facing={facing}")
    await bot.highrise.send_whisper(user.id, "\u2705 Jail spot saved.")


async def handle_setjailguardspot(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _is_staff_plus(user.username):
        await bot.highrise.send_whisper(user.id, "\U0001f512 Admin+ only.")
        return
    from config import SECURITY_BOT_NAME
    import os as _os
    _mode = _os.getenv("BOT_MODE", "unknown")
    _bname = _os.getenv("BOT_USERNAME", _mode)
    print(f"[JAIL SECURITY] security_bot_name={SECURITY_BOT_NAME} "
          f"current_bot_name={_bname} current_mode={_mode} setup_allowed=true")
    pos = await _get_user_position(bot, user.id)
    print(f"[JAIL SETUP] command=setjailguardspot user={user.username} "
          f"position_found={pos is not None}")
    if not pos:
        await bot.highrise.send_whisper(
            user.id,
            "\u26a0\ufe0f I can't read your current position yet. "
            "Move one step and try again."[:249],
        )
        return
    name = guard_spot_name()
    facing = getattr(pos, "facing", "FrontRight")
    db.save_spawn(name, pos.x, pos.y, pos.z, facing, user.username)
    print(f"[JAIL SETUP] saved {name} x={pos.x} y={pos.y} z={pos.z} facing={facing}")
    await bot.highrise.send_whisper(user.id, "\u2705 Jail guard spot saved.")


async def handle_setsecurityidle(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _is_staff_plus(user.username):
        await bot.highrise.send_whisper(user.id, "\U0001f512 Admin+ only.")
        return
    from config import SECURITY_BOT_NAME
    import os as _os
    _mode = _os.getenv("BOT_MODE", "unknown")
    _bname = _os.getenv("BOT_USERNAME", _mode)
    print(f"[JAIL SECURITY] security_bot_name={SECURITY_BOT_NAME} "
          f"current_bot_name={_bname} current_mode={_mode} setup_allowed=true")
    pos = await _get_user_position(bot, user.id)
    print(f"[JAIL SETUP] command=setsecurityidle user={user.username} "
          f"position_found={pos is not None}")
    if not pos:
        await bot.highrise.send_whisper(
            user.id,
            "\u26a0\ufe0f I can't read your current position yet. "
            "Move one step and try again."[:249],
        )
        return
    name = idle_spot_name()
    facing = getattr(pos, "facing", "FrontRight")
    db.save_spawn(name, pos.x, pos.y, pos.z, facing, user.username)
    print(f"[JAIL SETUP] saved {name} x={pos.x} y={pos.y} z={pos.z} facing={facing}")
    await bot.highrise.send_whisper(
        user.id, f"\u2705 {SECURITY_BOT_NAME} idle spot saved."[:249]
    )


async def handle_setjailreleasespot(
    bot: "BaseBot", user: "User", args: list[str]
) -> None:
    if not _is_staff_plus(user.username):
        await bot.highrise.send_whisper(user.id, "\U0001f512 Admin+ only.")
        return
    from config import SECURITY_BOT_NAME
    import os as _os
    _mode = _os.getenv("BOT_MODE", "unknown")
    _bname = _os.getenv("BOT_USERNAME", _mode)
    print(f"[JAIL SECURITY] security_bot_name={SECURITY_BOT_NAME} "
          f"current_bot_name={_bname} current_mode={_mode} setup_allowed=true")
    pos = await _get_user_position(bot, user.id)
    print(f"[JAIL SETUP] command=setjailreleasespot user={user.username} "
          f"position_found={pos is not None}")
    if not pos:
        await bot.highrise.send_whisper(
            user.id,
            "\u26a0\ufe0f I can't read your current position yet. "
            "Move one step and try again."[:249],
        )
        return
    from modules.jail_config import release_spot_name
    name = release_spot_name()
    facing = getattr(pos, "facing", "FrontRight")
    db.save_spawn(name, pos.x, pos.y, pos.z, facing, user.username)
    print(f"[JAIL SETUP] saved {name} x={pos.x} y={pos.y} z={pos.z} facing={facing}")
    await bot.highrise.send_whisper(user.id, "\u2705 Jail release spot saved.")


# ── Startup recovery ──────────────────────────────────────────────────────────

async def startup_jail_recovery(bot: "BaseBot") -> None:
    """
    Owned by SecurityBot.
    On restart: expire overdue sentences, then start the expiry loop.
    """
    from modules.multi_bot import should_this_bot_run_module
    from modules.jail_enforcement import jail_expiry_loop
    from modules.jail_store import mark_expired as _mark_exp
    from modules.securitybot_jail import verify_jail_spots
    if not should_this_bot_run_module("jail"):
        return
    await asyncio.sleep(5)
    now = time.time()
    for s in get_all_active_sentences():
        if now >= s["end_ts"]:
            _mark_exp(s["id"])
            print(f"[JAIL RECOVERY] expired on restart: {s['target_username']!r}")
    asyncio.create_task(jail_expiry_loop(bot))
    print("[JAIL RECOVERY] expiry loop started")
    missing = verify_jail_spots()
    if missing:
        print(f"[JAIL] WARNING — missing spots: {missing}. "
              f"Owner needs !setjailspot / !setjailguardspot / !setsecurityidle")
