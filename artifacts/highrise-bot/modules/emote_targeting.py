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
UPGRADED_ROOM_EMOTE_NOTICE = (
    "Upgraded rooms no longer allow bots to force your avatar to emote. "
    "I can only perform the emote toward you."
)


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


def _get_bot_user_id() -> str:
    try:
        from modules.gold import get_bot_user_id
        return str(get_bot_user_id() or "")
    except Exception:
        return ""


def _safe_attr(obj: object, name: str) -> str:
    try:
        return str(getattr(obj, name, "") or "")
    except Exception:
        return ""


def _room_context(bot: "BaseBot") -> str:
    fields: list[str] = []
    try:
        import config
        fields.append(f"room_id={getattr(config, 'ROOM_ID', '')!r}")
        fields.append(f"bot_mode={getattr(config, 'BOT_MODE', '')!r}")
    except Exception:
        pass
    for name in ("room_type", "is_upgraded_room", "upgraded_room"):
        value = getattr(bot, name, None)
        if value is not None:
            fields.append(f"{name}={value!r}")
    return " ".join(fields)


def log_emote_command_received(
    raw: str,
    user: object,
    handler_name: str,
    **extra: object,
) -> None:
    # Temporary route tracing is intentionally disabled now that upgraded-room
    # behavior is confirmed. Keep the call sites for cheap future reactivation.
    return


def warn_raw_player_emote_send(
    *,
    context: str,
    emote_id: str,
    target_id: str = "",
    sender_id: str = "",
) -> None:
    print(
        "[EMOTE_FATAL_RAW_SEND] "
        f"context={context!r} emote_id={emote_id!r} "
        f"target_id={target_id!r} sender_id={sender_id!r} "
        "message='player-facing emotes must use send_targeted_emote()'"
    )


async def send_targeted_emote(
    bot: "BaseBot",
    emote_id: str,
    target_id: str,
    *,
    command: str,
    sender_id: str = "",
    sender_username: str = "",
    sender_obj: object | None = None,
) -> None:
    style, signature = resolve_send_emote_target_style(bot)

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
