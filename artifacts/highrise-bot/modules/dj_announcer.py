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

_VIBE_LABELS: "dict[str, str]" = {
    "chill":      "Chill",
    "party":      "Party Remixes",
    "afrobeats":  "Afrobeats",
    "edm":        "EDM",
    "house":      "House",
    "kpop":       "KPop",
    "opm":        "OPM",
    "lofi":       "LoFi",
    "rnb":        "RNB",
    "hiphop":     "HipHop",
    "nightdrive": "NightDrive",
}

_VIBE_LINE: "dict[str, str]" = {
    "chill":      "🌙 AutoDJ • Chill",
    "party":      "🔥 AutoDJ • Party Remixes",
    "afrobeats":  "🎺 AutoDJ • Afrobeats",
    "edm":        "⚡ AutoDJ • EDM",
    "house":      "🏠 AutoDJ • House",
    "kpop":       "🌸 AutoDJ • KPop",
    "opm":        "🎤 AutoDJ • OPM",
    "lofi":       "☕ AutoDJ • LoFi",
    "rnb":        "💜 AutoDJ • RNB",
    "hiphop":     "🎵 AutoDJ • HipHop",
    "nightdrive": "🌙 AutoDJ • NightDrive",
}


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
    Room announcement for a new AutoDJ track (separate Title/Artist lines).

    If `requester` is provided the REQUEST LIVE format is used instead.
    """
    if requester:
        await announce_request_live(bot, title, artist, requester)
        return

    t = (title or "Unknown")[:60]
    a = (artist or "").strip()[:40]
    vibe_line = _VIBE_LINE.get(vibe, "🌙 AutoDJ • Chill")
    lines = ["▶ NOW PLAYING", f"Title: {t}"]
    if a:
        lines.append(f"Artist: {a}")
    lines.extend([vibe_line, f"📻 {_STATION}"])
    await _say(bot, "\n".join(lines))


# ─── Request confirmed playing ────────────────────────────────────────────────

async def announce_request_live(
    bot: "BaseBot",
    title: str,
    artist: str = "",
    requester: str = "",
) -> None:
    """
    REQUEST LIVE room announcement with separate Title/Artist lines.
    Fired when a queued request starts playing.
    """
    t = (title or "Unknown")[:60]
    a = (artist or "").strip()[:40]
    lines = ["▶ REQUEST LIVE", f"Title: {t}"]
    if a:
        lines.append(f"Artist: {a}")
    if requester:
        lines.append(f"🙋 @{requester[:20]}")
    lines.append(f"📻 {_STATION}")
    await _say(bot, "\n".join(lines))


# ─── Request queued (fallback when skip couldn't confirm) ─────────────────────

async def announce_request_queued_next(bot: "BaseBot") -> None:
    """
    Fired when the skip-verify task cannot confirm the request is playing
    within the poll window.  Lets the room know it will play next.
    """
    await _say(bot, "🎧 Request queued and ready. Up next!")


# ─── Vibe ─────────────────────────────────────────────────────────────────────

async def announce_vibe_changed(bot: "BaseBot", vibe: str) -> None:
    label = _VIBE_LABELS.get(vibe, vibe.title())
    await _say(bot, f"🎶 Vibe changed\nMode: {label}\nRequests: ON")


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
