"""Radio V3 bot-owned request player."""
from __future__ import annotations

import asyncio
import time

import modules.azuracast_controller as azura
from modules.radio_renderer import render_now_playing
from modules.radio_v3 import azura as v3_azura
from modules.radio_v3 import cleanup
from modules.radio_v3 import diagnostics as diag
from modules.radio_v3 import queue
from modules.radio_v3 import settings

_started = False
_task: "asyncio.Task | None" = None
_current_request_id = 0
_loading_request_id = 0
_last_song_key = ""
_last_transition_at = 0.0
_vote_skip: dict[str, set[str]] = {}


def _song_key(np: dict) -> str:
    now = (np or {}).get("now_playing") or {}
    song = now.get("song") or {}
    media = now.get("media") or {}
    return (
        str(media.get("id") or "")
        or str(song.get("unique_id") or "")
        or str(song.get("id") or "")
        or f"{song.get('artist') or ''}|{song.get('title') or ''}"
    )


def _extract(np: dict) -> tuple[dict, dict, int, int]:
    now = (np or {}).get("now_playing") or {}
    song = now.get("song") or {}
    media = now.get("media") or {}
    elapsed = int(now.get("elapsed") or 0)
    duration = int(now.get("duration") or song.get("duration") or 0)
    return song, media, elapsed, duration


async def _say(bot, msg: str) -> None:
    try:
        await bot.highrise.chat(msg[:249])
    except Exception as exc:
        diag.log("chat_error", error=repr(exc))


def current_request() -> dict:
    if _current_request_id:
        return queue.get_request(_current_request_id)
    return queue.currently_playing()


def match_nowplaying(np: dict) -> dict:
    song, media, _elapsed, _duration = _extract(np or {})
    media_id = str(media.get("id") or "").strip()
    song_id = (song.get("unique_id") or song.get("id") or "").strip()
    path = (media.get("path") or "").strip()
    title = (song.get("title") or "").strip()
    return queue.match_nowplaying(media_id=media_id, song_id=song_id, path=path, title=title)


async def announce_now(bot, row: dict, song: dict, elapsed: int, duration: int) -> None:
    msg = render_now_playing(
        {
            "title": row.get("title") or song.get("title") or "Unknown",
            "artist": row.get("artist") or song.get("artist") or "",
            "requester": row.get("username") or "",
            "source": "request",
            "elapsed": elapsed,
            "duration": duration,
            "likes": 0,
            "dislikes": 0,
        }
    )
    await _say(bot, msg)


async def _handle_request_start(bot, row: dict, song: dict, media: dict, elapsed: int, duration: int) -> None:
    global _current_request_id, _loading_request_id
    request_id = int(row.get("id") or 0)
    if not request_id:
        return
    _current_request_id = request_id
    if _loading_request_id == request_id:
        _loading_request_id = 0
    queue.mark_status(
        request_id,
        "playing",
        azura_file_id=str(media.get("id") or row.get("azura_file_id") or ""),
        azura_song_id=(song.get("unique_id") or song.get("id") or row.get("azura_song_id") or ""),
    )
    diag.log("request_started", request_id=request_id, filename=row.get("temp_filename") or "")
    try:
        azura.remove_queue_items_matching(
            media_id=str(media.get("id") or row.get("azura_file_id") or ""),
            song_id=(song.get("unique_id") or song.get("id") or row.get("azura_song_id") or ""),
            filename=row.get("temp_filename") or "",
            path=f"Requests/{row.get('temp_filename') or ''}",
        )
        azura.clear_file_playlists(str(media.get("id") or row.get("azura_file_id") or ""))
    except Exception as exc:
        diag.log("request_start_detach_failed", request_id=request_id, error=repr(exc))
    fresh = queue.get_request(request_id)
    if fresh.get("announced_at") and fresh.get("status") == "playing":
        diag.log("duplicate_now_playing_suppressed", request_id=request_id)
    else:
        await announce_now(bot, fresh or row, song, elapsed, duration)
        queue.mark_announced(request_id)


async def _finish_current(request_id: int, terminal_status: str = "played") -> None:
    global _current_request_id
    row = queue.get_request(request_id)
    if not row:
        _current_request_id = 0
        return
    if row.get("status") == "playing":
        queue.mark_status(request_id, terminal_status)
    diag.log("request_finished", request_id=request_id, status=terminal_status)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, cleanup.cleanup_request_media, queue.get_request(request_id), terminal_status)
    _current_request_id = 0


async def _start_ready_head() -> None:
    global _loading_request_id, _last_transition_at
    if _current_request_id or _loading_request_id:
        return
    head = queue.oldest_active_request()
    if not head:
        diag.log("autodj_allowed", reason="queue_empty")
        return
    status = (head.get("status") or "").strip().lower()
    request_id = int(head.get("id") or 0)
    if status not in ("ready", "loading"):
        diag.log("autodj_allowed", reason="head_not_ready", request_id=request_id, status=status)
        return
    if status == "loading":
        _loading_request_id = request_id
        return
    loop = asyncio.get_running_loop()
    loaded = await loop.run_in_executor(None, v3_azura.load_current_request, head)
    if not loaded:
        queue.mark_status(request_id, "failed", error="v3_load_current_failed")
        diag.log("request_load_failed", request_id=request_id)
        return
    queue.mark_status(request_id, "loading")
    _loading_request_id = request_id
    now = time.time()
    if now - _last_transition_at >= settings.transition_cooldown_secs():
        await loop.run_in_executor(None, v3_azura.transition_to_loaded_request, request_id)
        _last_transition_at = now
    else:
        diag.log("controlled_transition_skipped", request_id=request_id, reason="cooldown")


async def _handle_stale_request_media(song: dict, media: dict) -> None:
    path = (media.get("path") or "").strip()
    filename = path.rsplit("/", 1)[-1].strip()
    media_id = str(media.get("id") or "").strip()
    song_id = (song.get("unique_id") or song.get("id") or "").strip()
    diag.log("stale_request_media", path=path, media_id=media_id)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, cleanup.cleanup_request_media, {
        "id": 0,
        "temp_filename": filename,
        "azura_file_id": media_id,
        "azura_song_id": song_id,
    }, "stale_request_media")


async def _poll(bot) -> None:
    global _last_song_key
    diag.log("player_started")
    while True:
        try:
            loop = asyncio.get_running_loop()
            np = await loop.run_in_executor(None, azura.fetch_nowplaying)
            if not np:
                await asyncio.sleep(1)
                continue
            key = _song_key(np)
            song, media, elapsed, duration = _extract(np)
            row = match_nowplaying(np)
            if key and key != _last_song_key:
                previous_request = _current_request_id
                if previous_request and (not row or int(row.get("id") or 0) != previous_request):
                    await _finish_current(previous_request)
                if row:
                    await _handle_request_start(bot, row, song, media, elapsed, duration)
                else:
                    path = (media.get("path") or "").strip()
                    if path.lower().lstrip("/").startswith("requests/"):
                        await _handle_stale_request_media(song, media)
                _last_song_key = key
            elif row and not _current_request_id:
                await _handle_request_start(bot, row, song, media, elapsed, duration)

            if not _current_request_id:
                await _start_ready_head()
        except Exception as exc:
            diag.log("player_error", error=repr(exc))
        await asyncio.sleep(1)


def start(bot) -> None:
    global _started, _task
    if _started or not settings.enabled():
        return
    queue.ensure_schema()
    try:
        loop = asyncio.get_running_loop()
    except Exception:
        return
    _task = loop.create_task(_poll(bot), name="radio_v3_player")
    _started = True


async def skip_current_request(bot, user_id: str, staff: bool = False) -> tuple[bool, str]:
    row = current_request()
    if not row:
        return False, "Nothing is playing right now."
    if not staff and (row.get("user_id") or "") != user_id:
        return False, "That is not your request. Use !voteskip to vote skip."
    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(None, azura.skip_current)
    if ok:
        request_id = int(row.get("id") or 0)
        title = (row.get("title") or "request")[:60]
        await _say(bot, f"⏭️ Skipped: {title}")
        await _finish_current(request_id, terminal_status="played")
        return True, "⏭️ Skipped."
    return False, "❌ Skip failed. Try again."


async def vote_skip(bot, user_id: str) -> tuple[bool, str]:
    loop = asyncio.get_running_loop()
    np = await loop.run_in_executor(None, azura.fetch_nowplaying)
    if not np:
        return False, "🎵 Nothing is playing right now."
    key = _song_key(np)
    if not key:
        return False, "🎵 Nothing is playing right now."
    threshold = settings.voteskip_threshold()
    voters = _vote_skip.setdefault(key, set())
    if user_id in voters:
        return False, f"👎 Already voted. {max(0, threshold - len(voters))} more vote(s) needed."
    voters.add(user_id)
    if len(voters) >= threshold:
        await loop.run_in_executor(None, azura.skip_current)
        _vote_skip.pop(key, None)
        return True, f"👎 Vote skip passed ({threshold}/{threshold})."
    return True, f"👎 Vote skip: {len(voters)}/{threshold}"

