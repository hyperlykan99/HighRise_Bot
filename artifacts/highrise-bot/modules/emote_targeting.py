"""
Targeted emote send helper.

Player/social/sync/dancefloor emotes should resolve an explicit target so
upgraded rooms do not default the animation to the bot.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot


async def send_targeted_emote(
    bot: "BaseBot",
    emote_id: str,
    target_id: str,
    *,
    command: str,
    sender_id: str = "",
) -> None:
    print(
        f"[EMOTE_TARGET] command={command} sender={sender_id} "
        f"target={target_id} emote={emote_id}"
    )
    try:
        await bot.highrise.send_emote(emote_id, target_user_id=target_id)
    except TypeError:
        await bot.highrise.send_emote(emote_id, target_id)
