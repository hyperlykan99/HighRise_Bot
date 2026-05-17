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

_LOG     = "[DJ_ANN]"
_STATION = "ChillTopia Radio"
_DIV     = "━━━━━━━━━━━━━"   # 13-char divider line


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


def _track_label(title: str, artist: str) -> str:
    if artist and artist.lower() not in title.lower():
        return f"{artist} — {title}"
    return title or "Unknown"


# ─── Now Playing (vibe tracks) ────────────────────────────────────────────────

async def announce_now_playing(
    bot: "BaseBot",
    title: str,
    artist: str = "",
    requester: "str | None" = None,
    vibe: str = "chill",
) -> None:
    """
    Polished multi-line room announcement for a new track.

    If `requester` is provided the REQUEST LIVE format is used.
    Otherwise the chill / party vibe format is used.
    """
    track = _track_label(title, artist)[:70]

    if requester:
        await announce_request_live(bot, title, artist, requester)
        return

    if vibe == "party":
        msg = (
            f"🎙️  NOW PLAYING  🎙️\n"
            f"{_DIV}\n"
            f"🔥 Vibe: Party Mode\n"
            f"🎵 {track}\n"
            f"📻 {_STATION}\n"
            f"{_DIV}\n"
            f"Turn it up — party vibes 🪩"
        )
    else:
        msg = (
            f"🎙️  NOW PLAYING  🎙️\n"
            f"{_DIV}\n"
            f"🌙 Vibe: Chill\n"
            f"🎵 {track}\n"
            f"📻 {_STATION}\n"
            f"{_DIV}\n"
            f"Relax & vibe ✨"
        )

    await _say(bot, msg)


# ─── Request confirmed playing ────────────────────────────────────────────────

async def announce_request_live(
    bot: "BaseBot",
    title: str,
    artist: str = "",
    requester: str = "",
) -> None:
    """
    Polished REQUEST LIVE room announcement fired when skip-verify confirms
    the requested song is actually playing on AzuraCast.
    """
    track    = _track_label(title, artist)[:70]
    req_line = f"🙋 Requested by: @{requester[:20]}\n" if requester else ""
    msg = (
        f"🎧  REQUEST LIVE  🎧\n"
        f"{_DIV}\n"
        f"🎵 {track}\n"
        f"{req_line}"
        f"📻 {_STATION}\n"
        f"{_DIV}\n"
        f"Your request is on air 🔊"
    )
    await _say(bot, msg)


# ─── Request queued (fallback when skip couldn't confirm) ─────────────────────

async def announce_request_queued_next(bot: "BaseBot") -> None:
    """
    Fired when the skip-verify task cannot confirm the request is playing
    within the poll window.  Lets the room know it will play next.
    """
    await _say(bot, "🎧 Request queued and ready. It will play next.")


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
    """Legacy helper kept for compatibility — prefer announce_request_live."""
    await _say(bot, f"🎵 Added to radio: {title[:80]} — requested by @{username}")


async def announce_queue_cleared(
    bot: "BaseBot", count: int, total_refunded: int
) -> None:
    note = f" | {total_refunded:,} coins refunded" if total_refunded else ""
    await _say(bot, f"🧹 Queue cleared — {count} request(s) removed{note}.")
