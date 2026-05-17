"""
modules/dj_announcer.py
-----------------------
Room-wide announcement helpers for the radio system.

All async functions are non-fatal (never raise) and respect the 249-char limit.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot

_LOG = "[DJ_ANN]"


async def _say(bot: "BaseBot", msg: str) -> None:
    try:
        await bot.highrise.chat(msg[:249])
    except Exception as exc:
        print(f"{_LOG} chat error (non-fatal): {exc}")


async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception as exc:
        print(f"{_LOG} whisper error (non-fatal): {exc}")


# ─── Now Playing ──────────────────────────────────────────────────────────────

async def announce_now_playing(
    bot: "BaseBot",
    title: str,
    artist: str = "",
    requester: "str | None" = None,
    vibe: str = "chill",
) -> None:
    """Announce a track to the room. Uses requester tag if provided, else vibe label."""
    if artist and artist.lower() not in title.lower():
        song = f"{artist} — {title}"
    else:
        song = title or "Unknown"

    if requester:
        msg = f"🎧 Request: {song[:80]} | @{requester}"
    elif vibe == "party":
        msg = f"🔥 Party: {song[:120]}"
    else:
        msg = f"🎶 Chill: {song[:120]}"

    await _say(bot, msg)


# ─── Vibe ─────────────────────────────────────────────────────────────────────

async def announce_vibe_changed(bot: "BaseBot", vibe: str) -> None:
    if vibe == "party":
        await _say(bot, "🔥 Switching to PARTY mode! Let's gooo! 🔥")
    else:
        await _say(bot, "🎶 Switching to CHILL vibes. Sit back and relax. 🎶")


# ─── Skip ─────────────────────────────────────────────────────────────────────

async def announce_skip(bot: "BaseBot", title: str = "") -> None:
    suffix = f": {title[:60]}" if title else ""
    await _say(bot, f"⏭ Skipping{suffix}")


# ─── Vote skip ────────────────────────────────────────────────────────────────

async def announce_voteskip_progress(
    bot: "BaseBot",
    username: str,
    votes: int,
    threshold: int,
    title: str = "",
) -> None:
    remaining = threshold - votes
    suffix    = f" — {title[:35]}" if title else ""
    await _say(
        bot,
        f"👎 @{username[:15]} voted to skip{suffix}. {remaining} more vote(s) needed.",
    )


async def announce_voteskip_passed(
    bot: "BaseBot",
    votes: int,
    threshold: int,
    title: str = "",
) -> None:
    suffix = f": {title[:50]}" if title else ""
    await _say(bot, f"👎 Vote skip passed ({votes}/{threshold})! Skipping{suffix}")


# ─── Queue ────────────────────────────────────────────────────────────────────

async def announce_request_queued(bot: "BaseBot", title: str, username: str) -> None:
    await _say(bot, f"🎵 Added to radio: {title[:80]} — requested by @{username}")


async def announce_queue_cleared(
    bot: "BaseBot", count: int, total_refunded: int
) -> None:
    note = f" | {total_refunded:,} coins refunded" if total_refunded else ""
    await _say(bot, f"🧹 Queue cleared — {count} request(s) removed{note}.")
