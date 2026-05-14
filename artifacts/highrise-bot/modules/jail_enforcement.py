"""modules/jail_enforcement.py — Enforcement loop, rejoin re-jail, teleport block (3.4A)."""
from __future__ import annotations

TELEPORT_BLOCKED_CMDS: frozenset[str] = frozenset({
    "tele", "tp", "tpme", "goto", "bring", "tphere",
    "spawn", "rolespawn", "autospawn", "selftp", "groupteleport",
})
import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot

from modules.jail_store import get_active_sentence, get_all_active_sentences, mark_expired
from modules.jail_config import rejoin_enforce, jail_spot_name, release_spot_name


def is_jailed(user_id: str) -> bool:
    """Return True if the user has an unexpired active sentence."""
    s = get_active_sentence(user_id)
    if not s:
        return False
    if time.time() > s["end_ts"]:
        mark_expired(s["id"])
        return False
    return True


def remaining_seconds(user_id: str) -> float:
    s = get_active_sentence(user_id)
    if not s:
        return 0.0
    return max(0.0, s["end_ts"] - time.time())


def jail_block_message(user_id: str) -> str:
    """Short whisper message shown when a jailed player tries to escape."""
    secs = remaining_seconds(user_id)
    if secs <= 0:
        return "You're not jailed."
    mins = int(secs // 60)
    sec_ = int(secs % 60)
    s    = get_active_sentence(user_id)
    bail = s["bail_cost"] if s else 0
    time_str = f"{mins}m {sec_}s" if mins else f"{sec_}s"
    return (
        f"\U0001f6a8 Jailed for {time_str} more. Bail: {bail} \U0001f3ab. Type !bail."
    )[:249]


async def enforce_jail_on_rejoin(bot: "BaseBot", user_id: str, username: str) -> None:
    """
    Called from on_user_join (via SecurityBot ownership guard).
    Re-teleports player to jail if they still have time remaining.
    """
    if not rejoin_enforce():
        return
    s = get_active_sentence(user_id)
    if not s:
        return
    now = time.time()
    if now >= s["end_ts"]:
        mark_expired(s["id"])
        return
    await asyncio.sleep(2.0)
    try:
        import database as db
        from highrise.models import Position
        spawn = db.get_spawn(jail_spot_name())
        if spawn:
            pos = Position(spawn["x"], spawn["y"], spawn["z"], spawn["facing"])
            await bot.highrise.teleport(user_id, pos)
        secs = int(s["end_ts"] - time.time())
        bail = s["bail_cost"]
        m = secs // 60; sec_ = secs % 60
        time_str = f"{m}m {sec_}s" if m else f"{sec_}s"
        msg = (
            f"\U0001f6a8 You're still jailed for {time_str}. "
            f"Bail: {bail} \U0001f3ab. Type !bail."
        )[:249]
        await bot.highrise.send_whisper(user_id, msg)
        print(f"[JAIL ENFORCE] re-jailed on rejoin: {username!r} secs={secs}")
    except Exception as e:
        print(f"[JAIL ENFORCE REJOIN] error user={username!r} err={e!r}")


async def jail_expiry_loop(bot: "BaseBot") -> None:
    """Background loop — marks expired sentences and notifies players."""
    print("[JAIL EXPIRY LOOP] started")
    while True:
        try:
            now = time.time()
            for s in get_all_active_sentences():
                if now >= s["end_ts"]:
                    mark_expired(s["id"])
                    uid   = s["target_user_id"]
                    uname = s["target_username"]
                    print(f"[JAIL EXPIRED] {uname!r} sentence_id={s['id']}")
                    print(f"[JAIL RELEASE] target={uname!r} reason=expired")
                    # Teleport to jail_release, fallback to default spawn
                    try:
                        import database as db
                        from highrise.models import Position
                        release_key = release_spot_name()
                        spawn = db.get_spawn(release_key)
                        if not spawn:
                            spawn = (db.get_spawn("default")
                                     or db.get_spawn("main")
                                     or db.get_spawn("lobby"))
                        print(f"[JAIL RELEASE] release_spot={release_key} "
                              f"found={spawn is not None}")
                        if spawn:
                            pos = Position(spawn["x"], spawn["y"],
                                           spawn["z"], spawn["facing"])
                            await bot.highrise.teleport(uid, pos)
                            print(f"[JAIL RELEASE] teleport_success=true")
                        else:
                            print(f"[JAIL RELEASE] no release spot — unlocking in place")
                        print(f"[JAIL RELEASE] restrictions_removed=true")
                    except Exception as _re:
                        print(f"[JAIL RELEASE] teleport error: {_re!r}")
                    try:
                        await bot.highrise.send_whisper(
                            uid,
                            "\u2705 You served your jail time. You're free."[:249],
                        )
                    except Exception:
                        pass
                    try:
                        await bot.highrise.chat(
                            f"\u2705 {uname} has been released from jail."[:249]
                        )
                    except Exception:
                        pass
        except Exception as e:
            print(f"[JAIL EXPIRY LOOP] error: {e!r}")
        await asyncio.sleep(15)
