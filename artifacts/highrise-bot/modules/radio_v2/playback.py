"""Radio V2 playback watcher and cleanup lifecycle."""
from __future__ import annotations

import asyncio
import time

import modules.azuracast_controller as azura
import modules.config_store as cs
from modules.radio_renderer import render_now_playing
from modules.radio_v2 import azura as v2_azura
from modules.radio_v2 import cleanup
from modules.radio_v2 import diagnostics as diag
from modules.radio_v2 import queue
from modules.radio_v2 import settings

_started = False
_task: "asyncio.Task | None" = None
_current_request_id = 0
_last_song_key = ""
_last_autodj_skip_ts = 0.0
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


async def _say(bot, msg: str) -> None:
    try:
        await bot.highrise.chat(msg[:249])
    except Exception as exc:
        print(f"[RADIO_V2] chat_error={exc!r}")


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
    global _current_request_id
    request_id = int(row.get("id") or 0)
    if not request_id:
        return
    _current_request_id = request_id
    queue.mark_status(
        request_id,
        "playing",
        azura_file_id=str(media.get("id") or row.get("azura_file_id") or ""),
        azura_song_id=(song.get("unique_id") or song.get("id") or row.get("azura_song_id") or ""),
    )
    diag.log(
        "request_detected_playing",
        request_id=request_id,
        filename=row.get("temp_filename") or "",
        media_id=media.get("id") or "",
    )
    try:
        azura.remove_queue_items_matching(
            media_id=str(media.get("id") or row.get("azura_file_id") or ""),
            song_id=(song.get("unique_id") or song.get("id") or row.get("azura_song_id") or ""),
            filename=row.get("temp_filename") or "",
            path=f"Requests/{row.get('temp_filename') or ''}",
        )
        azura.clear_file_playlists(str(media.get("id") or row.get("azura_file_id") or ""))
    except Exception:
        pass
    await announce_now(bot, row, song, elapsed, duration)


async def _finish_current(bot, request_id: int) -> None:
    global _current_request_id
    row = queue.get_request(request_id)
    if not row:
        _current_request_id = 0
        return
    if row.get("status") == "playing":
        queue.mark_status(request_id, "played")
    diag.log("request_done", request_id=request_id, title=row.get("title") or "")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, cleanup.cleanup_request_media, queue.get_request(request_id), "played")
    _current_request_id = 0


async def _handle_stale_requests_media(song: dict, media: dict) -> None:
    path = (media.get("path") or "").strip()
    filename = path.rsplit("/", 1)[-1].strip()
    media_id = str(media.get("id") or "").strip()
    song_id = (song.get("unique_id") or song.get("id") or "").strip()
    diag.log("stale_requests_media_skip", path=path, media_id=media_id)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, azura.skip_current)
    await loop.run_in_executor(
        None,
        cleanup.cleanup_request_media,
        {
            "id": 0,
            "temp_filename": filename,
            "azura_file_id": media_id,
            "azura_song_id": song_id,
        },
        "stale_requests_media",
    )


async def drain_ready_requests() -> dict:
    """Submit only the oldest active V2 request when it is ready."""
    active_submitted = queue.active_submitted_or_playing()
    if active_submitted:
        submitted_id = int(active_submitted.get("id") or 0)
        diag.log("request_drain_waiting_for_active", submitted_request_id=submitted_id)
        return {
            "submitted": 0,
            "candidates": 0,
            "ready_or_submitted": queue.ready_or_submitted_count(),
            "active": queue.active_count_for_drain(),
            "head_request_id": submitted_id,
            "drainable": True,
        }

    head = queue.oldest_active_request()
    if head:
        head_id = int(head.get("id") or 0)
        diag.log("request_drain_head_selected", request_id=head_id)
        status = (head.get("status") or "").strip().lower()
        if status not in ("uploaded", "ready"):
            return {
                "submitted": 0,
                "candidates": 0,
                "ready_or_submitted": queue.ready_or_submitted_count(),
                "active": queue.active_count_for_drain(),
                "head_request_id": head_id,
                "head_status": status,
                "drainable": False,
            }

    rows = queue.submit_ready_rows(limit=1)
    submitted = 0
    for row in rows:
        request_id = int(row.get("id") or 0)
        if not request_id:
            continue
        loop = asyncio.get_running_loop()
        ok, requestable_id = await loop.run_in_executor(
            None,
            lambda r=row: v2_azura.submit_native_request(
                int(r.get("id") or 0),
                media_id=(r.get("azura_file_id") or "").strip(),
                song_id=(r.get("azura_song_id") or "").strip(),
                filename=(r.get("temp_filename") or "").strip(),
                title=(r.get("title") or "").strip(),
            ),
        )
        if ok:
            queue.mark_status(request_id, "submitted", azura_request_id=requestable_id)
            submitted += 1
        else:
            diag.log("request_drain_submit_failed", request_id=request_id)
    if submitted or rows:
        diag.log("request_drain_submit_ready", count=submitted, candidates=len(rows))
    return {
        "submitted": submitted,
        "candidates": len(rows),
        "ready_or_submitted": queue.ready_or_submitted_count(),
        "active": queue.active_count_for_drain(),
        "head_request_id": int((head or {}).get("id") or 0),
        "drainable": bool(submitted or rows),
    }


async def _maybe_skip_autodj_for_drain() -> None:
    global _last_autodj_skip_ts
    if not settings.drain_mode():
        return
    active = queue.active_count_for_drain()
    if active <= 0:
        return
    diag.log("request_drain_autodj_gap_detected", active_requests=active)
    drain = await drain_ready_requests()
    if not drain.get("drainable"):
        return
    now = time.time()
    if now - _last_autodj_skip_ts < 10:
        diag.log("request_drain_autodj_skip", result="cooldown")
        return
    loop = asyncio.get_running_loop()
    head_id = int(drain.get("head_request_id") or 0)
    if head_id:
        diag.log("request_drain_autodj_skip_for_head", request_id=head_id)
    skipped = await loop.run_in_executor(None, azura.skip_current)
    _last_autodj_skip_ts = now
    diag.log("request_drain_autodj_skip", result="success" if skipped else "failed")


async def _poll(bot) -> None:
    global _last_song_key
    diag.log("mode_enabled")
    diag.log("requests_playlist_should_not_be_general_rotation")
    while True:
        try:
            loop = asyncio.get_running_loop()
            np = await loop.run_in_executor(None, azura.fetch_nowplaying)
            if not np:
                await asyncio.sleep(5)
                continue
            key = _song_key(np)
            if key and key != _last_song_key:
                previous_request = _current_request_id
                song, media, elapsed, duration = _extract(np)
                row = match_nowplaying(np)
                if previous_request and (not row or int(row.get("id") or 0) != previous_request):
                    await _finish_current(bot, previous_request)
                    remaining = queue.active_count_for_drain()
                    diag.log("request_done_drain_check", request_id=previous_request, remaining_active=remaining)
                    next_head = queue.oldest_active_request()
                    if next_head:
                        diag.log(
                            "request_drain_next_after_done",
                            previous_id=previous_request,
                            next_id=int(next_head.get("id") or 0),
                        )
                    await drain_ready_requests()
                if row:
                    await _handle_request_start(bot, row, song, media, elapsed, duration)
                else:
                    path = (media.get("path") or "").strip()
                    if path.lower().lstrip("/").startswith("requests/"):
                        await _handle_stale_requests_media(song, media)
                    else:
                        await _maybe_skip_autodj_for_drain()
                _last_song_key = key
            elif settings.drain_mode() and not _current_request_id:
                row = match_nowplaying(np)
                if not row:
                    await _maybe_skip_autodj_for_drain()
        except Exception as exc:
            diag.log("watcher_error", error=repr(exc))
        await asyncio.sleep(5)


def start(bot) -> None:
    global _started, _task
    if _started:
        return
    if not settings.enabled():
        return
    try:
        loop = asyncio.get_running_loop()
    except Exception:
        return
    _task = loop.create_task(_poll(bot), name="radio_v2_playback")
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
        await _finish_current(bot, int(row.get("id") or 0))
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
