"""modules/securitybot_jail.py — SecurityBot-specific jail behavior (3.4A).

Only SecurityBot (BOT_MODE=security) runs the announcement / guard teleport /
return-to-idle sequence. Other bots still enforce jail blocks and send the
room announcement if SecurityBot is absent.
"""
from __future__ import annotations
import asyncio
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot


def is_security_bot() -> bool:
    """True when the current process is running as SecurityBot (KeanuShield)."""
    from config import SECURITY_BOT_NAME
    mode = os.getenv("BOT_MODE", "").lower()
    result = mode in ("security", "securitybot")
    current = os.getenv("BOT_USERNAME", os.getenv("BOT_MODE", "unknown"))
    if result:
        print(f"[JAIL SECURITY] security_bot_name={SECURITY_BOT_NAME} "
              f"current_bot_name={current} current_mode={mode} runtime_allowed=true")
    return result


async def _tp_bot_to_spawn(bot: "BaseBot", spawn_key: str) -> bool:
    """Teleport SecurityBot to a named spawn. Falls back to walk_to."""
    try:
        import database as db
        from highrise.models import Position
        spawn = db.get_spawn(spawn_key)
        if not spawn:
            print(f"[SECBOT JAIL] spawn '{spawn_key}' not found")
            return False
        pos = Position(spawn["x"], spawn["y"], spawn["z"], spawn["facing"])
        await bot.highrise.teleport(bot.highrise.my_id, pos)
        return True
    except Exception as e:
        print(f"[SECBOT JAIL] teleport to {spawn_key!r} failed: {e!r}")
        try:
            import database as db
            from highrise.models import Position
            spawn = db.get_spawn(spawn_key)
            if spawn:
                pos = Position(spawn["x"], spawn["y"], spawn["z"], spawn["facing"])
                await bot.highrise.walk_to(pos)
        except Exception:
            pass
        return False


async def teleport_player_to_jail(
    bot: "BaseBot", target_uid: str, target_uname: str
) -> bool:
    """Teleport the jailed player to the saved jail spawn. Returns True on success."""
    try:
        import database as db
        from highrise.models import Position
        from modules.jail_config import jail_spot_name
        spawn = db.get_spawn(jail_spot_name())
        if not spawn:
            print(f"[SECBOT JAIL] jail spawn '{jail_spot_name()}' not found")
            return False
        pos = Position(spawn["x"], spawn["y"], spawn["z"], spawn["facing"])
        await bot.highrise.teleport(target_uid, pos)
        print(f"[SECBOT JAIL] teleported {target_uname!r} to jail")
        return True
    except Exception as e:
        print(f"[SECBOT JAIL] teleport_player_to_jail err: {e!r}")
        return False


async def announce_jail(
    bot: "BaseBot",
    target_uname: str,
    by_uname: str,
    minutes: int,
    bail_cost: int,
    reason: str = "No reason given",
) -> None:
    reason_short = reason[:50]
    msg = (
        f"\U0001f6a8 {target_uname} jailed by {by_uname} for {minutes} min. "
        f"Reason: {reason_short}. Bail: {bail_cost} \U0001f3ab. Type !bail."
    )[:249]
    try:
        await bot.highrise.chat(msg)
        print(f"[JAIL ANNOUNCER] bot=KeanuShield message=jail_start sent=true target={target_uname!r}")
    except Exception as e:
        print(f"[SECBOT JAIL] announce_jail err: {e!r}")


async def brief_and_return(
    bot: "BaseBot",
    target_uname: str,
    by_uname: str,
    minutes: int,
    bail_cost: int,
    reason: str = "No reason given",
) -> None:
    """
    SecurityBot sequence after a jail is purchased:
    1. Teleport to jail_guard spot
    2. Announce the jail publicly
    3. Wait 4 seconds
    4. Return to security_idle spot
    """
    from modules.jail_config import guard_spot_name, idle_spot_name
    print(f"[JAIL ANNOUNCER] bot=KeanuShield message=brief_start target={target_uname!r}")
    await _tp_bot_to_spawn(bot, guard_spot_name())
    await asyncio.sleep(0.5)
    await announce_jail(bot, target_uname, by_uname, minutes, bail_cost, reason)
    await asyncio.sleep(4)
    await _tp_bot_to_spawn(bot, idle_spot_name())
    print(f"[JAIL ANNOUNCER] bot=KeanuShield message=brief_complete returned_to_idle=true")


def verify_jail_spots() -> list[str]:
    """Return list of spot names that are not yet saved in the DB."""
    import database as db
    from modules.jail_config import jail_spot_name, guard_spot_name, idle_spot_name
    missing = []
    for name in (jail_spot_name(), guard_spot_name(), idle_spot_name()):
        if not db.get_spawn(name):
            missing.append(name)
    return missing
