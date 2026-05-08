"""
modules/msg_test.py
-------------------
Message character-limit testing tools.

/msgtest <length>         — whisper a test message of exactly N visible chars
/msgboxtest               — whisper 7 messages at 100/150/200/220/249/280/300 chars
/msgsplitpreview <text>   — show how safe_split would chunk a long message

Owner: host  |  Permission: manager+
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import database as db
from modules.permissions import is_owner, can_manage_economy

if TYPE_CHECKING:
    from highrise import BaseBot, User

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _w(bot, uid: str, msg: str):
    return bot.highrise.send_whisper(uid, str(msg)[:249])


def _can_test(username: str) -> bool:
    return is_owner(username) or can_manage_economy(username)


def _build_body(length: int) -> str:
    """Build a visible test body of exactly *length* chars using repeating digits."""
    digits = "0123456789"
    body = (digits * ((length // 10) + 1))[:length]
    return body


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_msgtest(bot, user, args: list[str]) -> None:
    """/msgtest <length> — whisper a test message of N visible chars."""
    if not _can_test(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: /msgtest <length>  e.g. /msgtest 200")
        return
    length = min(max(int(args[1]), 1), 400)
    body = _build_body(length)
    header = f"MsgTest | Len:{length}\n"
    full = header + body
    # Send raw (no truncation) to test Highrise cut-off point
    await bot.highrise.send_whisper(user.id, full[:400])


async def handle_msgboxtest(bot, user, args: list[str]) -> None:
    """/msgboxtest — send test messages at multiple lengths."""
    if not _can_test(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    lengths = [100, 150, 200, 220, 249, 280, 300]
    await _w(bot, user.id, f"Sending {len(lengths)} test msgs — watch for cutoffs:")
    for n in lengths:
        body = _build_body(n)
        # Header is 5 chars: "[NNN] "
        label = f"[{n}] "
        payload = label + body
        # Send raw to test actual Highrise limit
        await bot.highrise.send_whisper(user.id, payload[:400])
        await asyncio.sleep(0.4)
    await _w(bot, user.id, "Done. Note which lengths got cut.")


async def handle_msgsplitpreview(bot, user, args: list[str]) -> None:
    """/msgsplitpreview <text> — preview how safe_split would chunk a message."""
    if not _can_test(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /msgsplitpreview <text to preview>")
        return
    from modules.msg_utils import safe_split
    text = " ".join(args[1:])
    chunks = safe_split(text, max_chars=220)
    n = len(chunks)
    await _w(bot, user.id, f"safe_split → {n} chunk(s) at max 220 chars:")
    for i, chunk in enumerate(chunks, 1):
        preview = chunk[:180]
        await _w(bot, user.id, f"[{i}/{n}] ({len(chunk)}c): {preview}")
        if i < n:
            await asyncio.sleep(0.3)
