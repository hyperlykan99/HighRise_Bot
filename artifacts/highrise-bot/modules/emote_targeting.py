"""
Targeted emote send helper.

Player/social/sync/dancefloor emotes should resolve an explicit target so
upgraded rooms do not default the animation to the bot.
"""
from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot

_TARGET_PARAM_CANDIDATES = ("target_user_id", "user_id", "receiver_id", "target")


def send_emote_signature(bot: "BaseBot") -> str:
    try:
        return str(inspect.signature(bot.highrise.send_emote))
    except Exception as exc:
        return f"<inspect failed: {type(exc).__name__}: {exc}>"


def send_emote_capabilities(bot: "BaseBot") -> dict[str, object]:
    signature = send_emote_signature(bot)
    caps: dict[str, object] = {
        "signature": signature,
        "target_user_id": False,
        "user_id": False,
        "receiver_id": False,
        "target": False,
        "positional_second": False,
    }
    try:
        params = inspect.signature(bot.highrise.send_emote).parameters
    except Exception:
        return caps

    for name in _TARGET_PARAM_CANDIDATES:
        caps[name] = name in params

    positional = [
        p for p in params.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    caps["positional_second"] = len(positional) >= 2
    return caps


def resolve_send_emote_target_style(bot: "BaseBot") -> tuple[str, str]:
    """Pick the targeted send style from the live SDK method signature."""
    signature = send_emote_signature(bot)
    try:
        params = inspect.signature(bot.highrise.send_emote).parameters
    except Exception:
        return "positional", signature

    for name in _TARGET_PARAM_CANDIDATES:
        if name in params:
            return name, signature

    positional = [
        p for p in params.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    if len(positional) >= 2:
        return "positional", signature
    return "unsupported", signature


async def send_targeted_emote(
    bot: "BaseBot",
    emote_id: str,
    target_id: str,
    *,
    command: str,
    sender_id: str = "",
) -> None:
    style, signature = resolve_send_emote_target_style(bot)
    print(
        f"[EMOTE_TARGET] command={command} sender={sender_id} "
        f"target={target_id} emote={emote_id} style={style} "
        f"signature={signature!r}"
    )

    if style == "target_user_id":
        await bot.highrise.send_emote(emote_id, target_user_id=target_id)
        return
    if style == "user_id":
        await bot.highrise.send_emote(emote_id, user_id=target_id)
        return
    if style == "receiver_id":
        await bot.highrise.send_emote(emote_id, receiver_id=target_id)
        return
    if style == "target":
        await bot.highrise.send_emote(emote_id, target=target_id)
        return
    if style == "positional":
        await bot.highrise.send_emote(emote_id, target_id)
        return

    raise RuntimeError(f"send_emote has no target parameter: {signature}")
