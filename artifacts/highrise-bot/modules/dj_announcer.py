"""
modules/dj_announcer.py
-----------------------
Room-wide announcement helpers for the radio system.

All async functions are non-fatal (never raise) and respect the 249-char limit.
"""
from __future__ import annotations
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot

_LOG     = "[DJ_ANN]"

# ── Startup/reconnect duplicate-announce guard ────────────────────────────────
# Tracks the last announced song key + timestamp so that a second announce for
# the same song within _ANN_DEBOUNCE_SECS is silently dropped.  Prevents the
# double card (0:00 + correct elapsed) observed on DJ bot restart.
_ANN_DEBOUNCE_SECS: float = 15.0
_last_ann_song_key:  str   = ""
_last_ann_ts:        float = 0.0
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
    Uses render_now_playing() — the single canonical renderer.
    Reads real like/dislike counts from dj_ratings (same key as !like/!dislike).
    Uses real AzuraCast elapsed time so restart announcements show correct progress.
    Deduplicates: same song within _ANN_DEBOUNCE_SECS is silently dropped.
    """
    global _last_ann_song_key, _last_ann_ts

    if requester:
        await announce_request_live(bot, title, artist, requester)
        return

    song_key = (title.lower().strip() + "|" + artist.lower().strip())[:150]
    now = time.monotonic()
    if song_key and song_key == _last_ann_song_key and (now - _last_ann_ts) < _ANN_DEBOUNCE_SECS:
        print(f"{_LOG} announce_now_playing dedup skip — same song within {_ANN_DEBOUNCE_SECS}s")
        return
    _last_ann_song_key = song_key
    _last_ann_ts       = now

    from modules.track_resolver  import attach_vote_counts, render_now_playing, vote_key
    from modules.playback_engine import get_cur_duration, get_cur_elapsed
    song_key = vote_key(title, artist)
    track = {
        "source":    "autodj",
        "title":     title,
        "artist":    artist,
        "vibe":      vibe,
        "elapsed":   get_cur_elapsed(),
        "duration":  get_cur_duration(),
        "vote_key":  song_key,
    }
    track = attach_vote_counts(track, source="room")
    await _say(bot, render_now_playing(track))


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
    Uses render_now_playing() — the single canonical renderer.
    Reads real like/dislike counts from dj_ratings (same key as !like/!dislike).
    Uses real AzuraCast elapsed time so restart announcements show correct progress.
    Deduplicates: same song within _ANN_DEBOUNCE_SECS is silently dropped.
    """
    global _last_ann_song_key, _last_ann_ts

    song_key = (title.lower().strip() + "|" + artist.lower().strip())[:150]
    now = time.monotonic()
    if song_key and song_key == _last_ann_song_key and (now - _last_ann_ts) < _ANN_DEBOUNCE_SECS:
        print(f"{_LOG} announce_request_live dedup skip — same song within {_ANN_DEBOUNCE_SECS}s")
        return
    _last_ann_song_key = song_key
    _last_ann_ts       = now

    from modules.track_resolver  import attach_vote_counts, render_now_playing, vote_key
    from modules.playback_engine import get_cur_duration, get_cur_elapsed
    song_key = vote_key(title, artist)
    track = {
        "source":    "request",
        "title":     title,
        "artist":    artist,
        "requester": requester,
        "elapsed":   get_cur_elapsed(),
        "duration":  get_cur_duration(),
        "vote_key":  song_key,
    }
    track = attach_vote_counts(track, source="room")
    await _say(bot, render_now_playing(track))


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
