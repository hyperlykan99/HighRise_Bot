"""modules/jail_bail.py — Bail purchase flow for the Luxe Jail system (3.4A)."""
from __future__ import annotations
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot, User

import database as db

# {payer_user_id: {sentence_id, bail_cost, target_uid, target_uname, expires_at}}
_BAIL_PENDING: dict[str, dict] = {}
BAIL_CONFIRM_TTL = 60


def set_bail_pending(
    payer_uid: str,
    sentence_id: int,
    bail_cost: int,
    target_uid: str,
    target_uname: str,
) -> None:
    _BAIL_PENDING[payer_uid] = {
        "sentence_id":  sentence_id,
        "bail_cost":    bail_cost,
        "target_uid":   target_uid,
        "target_uname": target_uname,
        "expires_at":   time.time() + BAIL_CONFIRM_TTL,
    }


def get_bail_pending(payer_uid: str) -> dict | None:
    p = _BAIL_PENDING.get(payer_uid)
    if p and time.time() < p["expires_at"]:
        return p
    _BAIL_PENDING.pop(payer_uid, None)
    return None


def clear_bail_pending(payer_uid: str) -> None:
    _BAIL_PENDING.pop(payer_uid, None)


async def initiate_bail(bot: "BaseBot", payer: "User", target_uname: str) -> None:
    """Look up the sentence for target, show the bail confirm prompt."""
    from modules.jail_store import get_active_sentence, mark_expired
    conn = db.get_connection()
    row = conn.execute(
        "SELECT * FROM jail_sentences WHERE target_username=? AND status='active' "
        "ORDER BY id DESC LIMIT 1",
        (target_uname.lower(),),
    ).fetchone()
    conn.close()

    if not row:
        await bot.highrise.send_whisper(payer.id, f"{target_uname} is not currently jailed.")
        return
    s = dict(row)
    if time.time() >= s["end_ts"]:
        mark_expired(s["id"])
        await bot.highrise.send_whisper(payer.id, f"{target_uname}'s jail time has already expired.")
        return

    bail_cost = s["bail_cost"]
    set_bail_pending(payer.id, s["id"], bail_cost, s["target_user_id"], s["target_username"])
    secs = int(s["end_ts"] - time.time())
    mins = secs // 60; sec_ = secs % 60
    time_str = f"{mins}m {sec_}s" if mins else f"{sec_}s"
    msg = (
        f"\U0001f6a8 Bail {s['target_username']} ({time_str} left)? "
        f"Cost: {bail_cost} \U0001f3ab. "
        "Reply confirm to pay, or cancel. (60s)"
    )[:249]
    await bot.highrise.send_whisper(payer.id, msg)


async def complete_bail(bot: "BaseBot", payer: "User") -> None:
    """Execute bail after the payer confirms."""
    from modules.jail_store import mark_bailed, log_jail_action
    from modules.luxe import get_luxe_balance, deduct_luxe_balance

    p = get_bail_pending(payer.id)
    if not p:
        await bot.highrise.send_whisper(payer.id, "No pending bail. Use !bail @user.")
        return

    bal = get_luxe_balance(payer.id)
    if bal < p["bail_cost"]:
        clear_bail_pending(payer.id)
        await bot.highrise.send_whisper(
            payer.id,
            f"Need {p['bail_cost']} \U0001f3ab to bail. You have {bal}."[:249],
        )
        return

    ok = deduct_luxe_balance(payer.id, payer.username, p["bail_cost"])
    if not ok:
        clear_bail_pending(payer.id)
        await bot.highrise.send_whisper(payer.id, "Bail deduction failed. Try again.")
        return

    mark_bailed(p["sentence_id"])
    clear_bail_pending(payer.id)
    log_jail_action(
        "bail", p["target_uid"], p["target_uname"],
        payer.id, payer.username, p["bail_cost"], "",
    )
    print(
        f"[JAIL BAIL] {p['target_uname']!r} bailed by {payer.username!r} "
        f"cost={p['bail_cost']}"
    )

    # Teleport to default spawn
    try:
        spawn = db.get_spawn("default") or db.get_spawn("main") or db.get_spawn("lobby")
        if spawn:
            from highrise.models import Position
            pos = Position(spawn["x"], spawn["y"], spawn["z"], spawn["facing"])
            await bot.highrise.teleport(p["target_uid"], pos)
    except Exception:
        pass

    try:
        await bot.highrise.send_whisper(
            p["target_uid"],
            f"\u2705 Bailed out by {payer.username}! You're free."[:249],
        )
    except Exception:
        pass

    by_str = "" if payer.id == p["target_uid"] else f" by {payer.username}"
    pub_msg = (
        f"\u2705 {p['target_uname']} bailed out{by_str} for {p['bail_cost']} \U0001f3ab."
    )[:249]
    try:
        await bot.highrise.chat(pub_msg)
    except Exception:
        pass
