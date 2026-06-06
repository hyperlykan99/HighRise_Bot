"""Radio service layer for the rebuilt direct-URL pipeline."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone

import database as root_db
from modules import permissions
from modules import luxe
from modules.radio import autodj_sync
from modules.radio import cleanup
from modules.radio import azura
from modules.radio import db as radio_db
from modules.radio import icecast
from modules.radio import liquidsoap_queue
from modules.radio import music_discs
from modules.radio import renderer
from modules.radio import search
from modules.radio import sources
from modules.radio import settings as radio_settings


def ensure_ready() -> None:
    radio_db.ensure_schema()
    radio_settings.ensure_radio_settings()


def recover_stale_ready_submitted_requests() -> None:
    ensure_ready()
    for row in radio_db.stale_ready_submitted_requests(minutes=30):
        filename = str(row.get("temp_filename") or "").strip()
        if not filename or not liquidsoap_queue.safe_request_filename(filename):
            continue
        request_path = _request_path_for_playlist(row)
        if request_path:
            continue
        radio_db.mark_status(
            int(row["id"]),
            "failed",
            error="startup_recovery_missing_request_file",
            finish_reason="startup_recovery_missing_request_file",
        )
        print(
            f"[RADIO_CONTROLLER_RECOVERY] event=startup_recovery_stale_request_failed "
            f"request_id={row['id']} status={row.get('status')!r} filename={filename!r}"
        )
    _inject_next_ready_request_sync("startup_recovery")


def _renderer_settings() -> dict:
    return {
        "now_show_progress_bar": radio_settings.get_bool_setting("now_show_progress_bar", True),
        "now_show_likes_dislikes": radio_settings.get_bool_setting("now_show_likes_dislikes", True),
        "now_show_request_play_count": radio_settings.get_bool_setting("now_show_request_play_count", True),
        "now_footer_text": radio_settings.get_setting("now_footer_text", "🎶 !play to request a song"),
    }


def _safe_generated_title(title: str) -> bool:
    normalized = radio_db.normalize_generated_request_name(title)
    return str(title or "").strip().startswith(azura.SAFE_PREFIXES) or normalized.startswith(
        ("000 priority request ", "radio request ", "radio yt ", "radio local ", "radio req ")
    )


def _display_track_for_request(track: dict, request: dict | None) -> dict:
    if not request or not _safe_generated_title(str(track.get("title") or "")):
        return track
    display = dict(track)
    display["title"] = request.get("title") or track.get("title") or "Unknown Track"
    display["artist"] = request.get("artist") or track.get("artist") or "Unknown Artist"
    display["track_key"] = azura.track_key(display)
    return display


def user_role(username: str) -> str:
    if permissions.is_owner(username):
        return "owner"
    if permissions.is_staff(username):
        return "staff"
    try:
        if username.lower() in {u.lower() for u in root_db.get_vip_list()}:
            return "vip"
    except Exception:
        pass
    return "normal"


def request_cost_for_role(role: str) -> int:
    return max(0, radio_settings.get_int_setting(f"request_disc_cost_{role}", 1 if role in {"normal", "vip"} else 0))


def priority_luxe_cost() -> int:
    return radio_settings.priority_luxe_cost()


def max_duration_for_role(role: str) -> int:
    defaults = {"normal": 300, "vip": 480, "staff": 600, "owner": 0}
    return max(0, radio_settings.get_int_setting(f"max_song_duration_{role}_secs", defaults.get(role, 300)))


def _shorten(value: str, limit: int = 42) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _favorite_song_line(index: int, row: dict) -> str:
    title = _shorten(row.get("title") or "YouTube Request", 42)
    artist = _shorten(row.get("artist") or "YouTube", 24)
    return f"{index}. {title} — {artist}"


def _render_search_results(results: list[dict]) -> str:
    lines = ["🎵 YouTube Results"]
    for idx, item in enumerate(results[:5], 1):
        title = _shorten(item.get("title") or "YouTube Result", 36)
        channel = _shorten(item.get("channel") or "YouTube", 20)
        duration = renderer.format_duration(item.get("duration") or 0)
        lines.append(f"{idx}. {title} — {channel} — {duration}")
    lines.append("")
    lines.append(f"Use !pick 1-{len(results[:5])} to request.")
    return "\n".join(lines)


def favorite_now(user) -> str:
    ensure_ready()
    if not radio_settings.get_bool_setting("radio_favorites_enabled", True):
        return "🔒 Radio favorites are currently disabled."
    raw_id = radio_db.get_runtime_state("current_request_id", "")
    try:
        request_id = int(raw_id)
    except (TypeError, ValueError):
        return "⚠️ I can only favorite songs requested through DJ_DUDU."
    request = radio_db.get_request(request_id)
    if not request or str(request.get("source_type") or "") != "youtube":
        return "⚠️ I can only favorite songs requested through DJ_DUDU."
    source_url = str(request.get("source_ref") or "").strip()
    if not source_url:
        return "⚠️ I can only favorite songs requested through DJ_DUDU."
    try:
        clean_url = sources.validate_youtube_url(source_url)
    except sources.SourceError:
        return "⚠️ I can only favorite songs requested through DJ_DUDU."
    video_id = sources.youtube_video_id(clean_url)
    if radio_db.favorite_exists(user.id, clean_url, video_id):
        return "⚠️ That song is already in your favorites."
    max_favs = radio_settings.radio_favorites_max_per_user()
    if radio_db.favorite_count(user.id) >= max_favs:
        return f"⚠️ You can save up to {max_favs} favorites."
    try:
        favorite_id = radio_db.add_favorite(
            user.id,
            user.username,
            request.get("title") or "YouTube Request",
            request.get("artist") or "YouTube",
            clean_url,
            video_id,
            0,
        )
    except Exception:
        return "⚠️ That song is already in your favorites."
    print(f"[RADIO_PHASE7] event=favorite_saved favorite_id={favorite_id} user={user.username!r} request_id={request_id}")
    return f"✅ Saved favorite\n{request.get('title') or 'YouTube Request'}"


def favorites_list(user) -> str:
    ensure_ready()
    rows = radio_db.list_favorites(user.id, limit=10)
    if not rows:
        return "🎵 Your Favorites\nNo saved songs yet.\nUse !favnow while your request is playing."
    lines = ["🎵 Your Favorites"]
    lines.extend(_favorite_song_line(index, row) for index, row in enumerate(rows, 1))
    lines.append("Use !playfav <number> to request.")
    return "\n".join(lines)


async def play_favorite(bot, user, number: int) -> str:
    ensure_ready()
    if not radio_settings.get_bool_setting("radio_favorites_enabled", True):
        return "🔒 Radio favorites are currently disabled."
    favorite = radio_db.favorite_by_number(user.id, int(number))
    if not favorite:
        return "⚠️ Pick a favorite number from !favorites."
    source_url = str(favorite.get("source_url") or "").strip()
    message = await submit_direct_youtube_request(bot, user, source_url)
    if message.startswith("✅ Added to queue"):
        radio_db.mark_favorite_requested(int(favorite["id"]))
    return message


def remove_favorite(user, number: int) -> str:
    ensure_ready()
    removed = radio_db.remove_favorite_by_number(user.id, int(number))
    if not removed:
        return "⚠️ Pick a favorite number from !favorites."
    print(f"[RADIO_PHASE7] event=favorite_removed favorite_id={removed.get('id')} user={user.username!r}")
    return f"✅ Removed favorite\n{removed.get('title') or 'YouTube Request'}"


async def search_youtube_request(user, query: str, priority: bool = False) -> str:
    ensure_ready()
    if priority:
        print(f"[RADIO_PRIORITY_REQUEST_START] username={getattr(user, 'username', '')!r} source='search'")
    if not radio_settings.get_bool_setting("radio_enabled", True):
        return "📻 Music system is currently disabled."
    if not radio_settings.get_bool_setting("youtube_search_enabled", True):
        return "🔒 YouTube search is currently disabled."
    query = str(query or "").strip()
    if not query:
        return "Use: !play <song name>"
    limit = radio_settings.youtube_search_result_count()
    try:
        results = await asyncio.to_thread(search.search_youtube, query, limit)
    except search.SearchError as exc:
        return f"⚠️ {exc}"
    if not results:
        return "⚠️ No YouTube results found. Try another search."
    radio_db.save_search_session(
        user.id,
        user.username,
        query,
        results,
        radio_settings.youtube_search_session_timeout_secs(),
        priority=priority,
    )
    message = _render_search_results(results)
    if priority:
        message += f"\nPriority cost: {priority_luxe_cost()} Luxe Tickets 🎫"
    return message


async def pick_youtube_search_result(bot, user, pick_number: int) -> str:
    ensure_ready()
    session = radio_db.get_search_session(user.id)
    if not session:
        return "⚠️ Search first with !play <song name>."
    if session.get("expired"):
        radio_db.clear_search_session(user.id)
        return "⚠️ Your search expired. Search again with !play <song name>."
    results = session.get("results") or []
    if pick_number < 1 or pick_number > len(results):
        return "⚠️ Pick a number from the search results: !pick 1"
    selected = results[pick_number - 1]
    url = str(selected.get("webpage_url") or "")
    if not url:
        return "⚠️ Pick a number from the search results: !pick 1"
    message = await submit_direct_youtube_request(bot, user, url, priority=bool(session.get("priority")))
    if message.startswith("✅ Added to queue"):
        radio_db.clear_search_session(user.id)
    return message


def now_playing_card(bot=None) -> tuple[str | None, dict | None, str]:
    ensure_ready()
    if not radio_settings.get_bool_setting("radio_enabled", True):
        return "📻 Music system is currently disabled.", None, ""
    track = icecast.extract_mount_track(icecast.fetch_status_json())
    if not track:
        radio_db.mark_last_poll(False, "icecast_nowplaying_unavailable")
        return "📻 Radio is live, but I can't read the current track right now.", None, "nowplaying_unavailable"
    request = _liquidsoap_nowplaying_request(track)
    source = "autodj"
    requester = None
    display_track = track
    if request:
        if str(request.get("status") or "") == "released":
            request_path = _request_path_for_playlist(request)
            radio_db.mark_status(
                int(request["id"]),
                "playing",
                current_path=request_path or str(request.get("current_path") or ""),
                released_path=request_path or str(request.get("released_path") or ""),
                azura_path=request_path or str(request.get("azura_path") or ""),
            )
            request = radio_db.get_request(int(request["id"])) or request
            print(
                f"[RADIO_REQUEST_STARTED] request_id={request['id']} "
                f"path={request_path!r} title={request.get('title')!r}"
            )
        source = "request"
        requester = request.get("username") or None
        display_track = dict(track)
        display_track["title"] = request.get("title") or track.get("title") or "Unknown Track"
        display_track["artist"] = request.get("artist") or track.get("artist") or "Unknown Artist"
        display_track["track_key"] = f"request:{request.get('id')}"
        duration = int(request.get("duration_secs") or 0)
        elapsed = _elapsed_from_playing_at(request.get("playing_at"), duration)
        if duration > 0 and elapsed is not None:
            display_track["duration"] = duration
            display_track["elapsed"] = elapsed
            display_track["live"] = False
            print(
                f"[RADIO_LIQUIDSOAP_PROGRESS_RENDER] request_id={request['id']} "
                f"elapsed_secs={elapsed} duration_secs={duration}"
            )
        else:
            print(
                f"[RADIO_LIQUIDSOAP_PROGRESS_FALLBACK] request_id={request['id']} "
                f"playing_at={request.get('playing_at')!r} duration_secs={duration}"
            )
        radio_db.set_runtime_state("current_request_id", request["id"])
        print(
            f"[RADIO_LIQUIDSOAP_NOWPLAYING_REQUEST_MATCH] "
            f"request_id={request['id']} title={display_track.get('title')!r} "
            f"filename={request.get('temp_filename')!r}"
        )
    else:
        radio_db.set_runtime_state("current_request_id", "")
        autodj_progress = autodj_sync.autodj_nowplaying_progress(track)
        if autodj_progress:
            display_track = dict(track)
            display_track["duration"] = int(autodj_progress.get("duration") or 0)
            display_track["elapsed"] = int(autodj_progress.get("elapsed") or 0)
            display_track["live"] = False
            display_track["track_key"] = f"autodj:{autodj_progress.get('vibe')}:{autodj_progress.get('filename')}"
        print(f"[RADIO_LIQUIDSOAP_NOWPLAYING_AUTODJ] title={track.get('title')!r}")
    stats = radio_db.read_track_stats(display_track.get("track_key", ""), display_track.get("title", ""), display_track.get("artist", ""))
    radio_db.mark_last_poll(True)
    radio_db.set_runtime_state("current_track", display_track)
    return renderer.render_now_playing_card(display_track, source, requester=requester, stats=stats, settings=_renderer_settings()), display_track, ""


def reject_current_autodj_track(reason: str = "") -> str:
    ensure_ready()
    track = icecast.extract_mount_track(icecast.fetch_status_json())
    if not track:
        return "⚠️ I can't read the current AutoDJ track right now."
    request = _liquidsoap_nowplaying_request(track)
    if request:
        return "⚠️ Current song is a user request. Use request controls instead."
    ok, message = autodj_sync.reject_current_track(track, reason=reason)
    return message


def _elapsed_from_playing_at(playing_at: str | None, duration: int) -> int | None:
    raw = str(playing_at or "").strip()
    if not raw or int(duration or 0) <= 0:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    elapsed = int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
    return max(0, min(int(duration), elapsed))


def _age_from_timestamp(raw_value: str | None) -> int | None:
    raw = str(raw_value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))


def _request_path_for_playlist(row: dict) -> str:
    filename = str(row.get("temp_filename") or "").strip()
    for key in ("current_path", "released_path", "prepared_path", "azura_path", "temp_filename"):
        raw = str(row.get(key) or "").strip()
        if not raw:
            continue
        target = liquidsoap_queue.request_path_in_library(raw)
        if not target or not target.exists() or not target.is_file():
            continue
        if filename and not liquidsoap_queue.safe_request_filename(filename):
            continue
        if not liquidsoap_queue.safe_request_filename(target.name):
            continue
        return str(target)
    return ""


def _inject_next_ready_request_sync(reason: str) -> int:
    playing_rows = radio_db.playing_requests(limit=10)
    released_rows = radio_db.released_requests(limit=10)
    ready_rows = radio_db.ready_requests_for_release(limit=1)
    print(
        f"[RADIO_LIQUIDSOAP_REQUEST_SOURCE_INIT] reason={reason!r} "
        f"playing_count={len(playing_rows)} released_count={len(released_rows)} ready_count={len(ready_rows)}"
    )
    if playing_rows or released_rows:
        return 0
    if not ready_rows:
        print(f"[RADIO_REQUEST_QUEUE_EMPTY_AUTODJ] reason={reason!r}")
        return 0
    row = ready_rows[0]
    request_id = int(row.get("id") or 0)
    request_path = _request_path_for_playlist(row)
    is_priority = bool(int(row.get("priority") or 0))
    print(
        f"[RADIO_REQUEST_NEXT_READY] reason={reason!r} request_id={request_id} "
        f"priority={int(is_priority)}"
    )
    if not request_path:
        print(
            f"[RADIO_REQUEST_INJECT_FAILED] request_id={request_id} "
            f"reason={reason!r} error='prepared_file_missing_or_unsafe'"
        )
        return 0
    radio_db.mark_status(
        request_id,
        "released",
        released_path=request_path,
        current_path=request_path,
        azura_path=request_path,
    )
    print(
        f"[RADIO_REQUEST_INJECT_START] request_id={request_id} "
        f"path={request_path!r} priority={int(is_priority)} reason={reason!r}"
    )
    ok, liquidsoap_request_id, response = liquidsoap_queue.push_request_to_liquidsoap(request_path)
    if not ok:
        latest = radio_db.get_request(request_id) or row
        radio_db.mark_status(
            request_id,
            "failed",
            error=f"liquidsoap_inject_failed:{response}"[:500],
            finish_reason="liquidsoap_inject_failed",
        )
        _refund_request_payment_once(latest)
        cleanup.cleanup_request_media(request_id, reason="liquidsoap_inject_failed")
        print(
            f"[RADIO_REQUEST_INJECT_FAILED] request_id={request_id} "
            f"path={request_path!r} error={response!r}"
        )
        return 0
    print(
        f"[RADIO_REQUEST_INJECT_OK] request_id={request_id} "
        f"liquidsoap_request_id={liquidsoap_request_id!r} path={request_path!r}"
    )
    if is_priority:
        print(f"[RADIO_PRIORITY_HANDOFF_ORDER] request_id={request_id} filename={os.path.basename(request_path)!r}")
    return 1


def _liquidsoap_nowplaying_request(track: dict) -> dict | None:
    title = str((track or {}).get("title") or "").strip()
    title_is_unknown = title.lower() in {"", "unknown", "unknown title"}
    normalized_title = radio_db.normalize_generated_request_name(title)
    generated_title = normalized_title.startswith(("radio request ", "000 priority request ", "radio yt ", "radio local ", "radio req "))
    candidates = radio_db.active_liquidsoap_request_candidates(limit=50)
    playing_rows = [row for row in candidates if str(row.get("status") or "") == "playing"]
    released_rows = [row for row in candidates if str(row.get("status") or "") == "released"]

    def match_generated(rows: list[dict]) -> dict | None:
        for row in rows:
            path = str(row.get("current_path") or row.get("prepared_path") or row.get("azura_path") or "")
            filename = str(row.get("temp_filename") or "")
            if not liquidsoap_queue.request_path_in_library(path):
                continue
            if generated_title and radio_db.normalize_generated_request_name(filename) == normalized_title:
                return row
            if generated_title and (
                normalized_title.startswith(f"radio request {row.get('id')}")
                or normalized_title.startswith(f"000 priority request {row.get('id')}")
            ):
                return row
        return None

    matched = match_generated(playing_rows)
    if matched:
        return matched
    matched = match_generated(released_rows)
    if matched:
        return matched

    playing_candidate = None
    for row in playing_rows:
        path = str(row.get("current_path") or row.get("prepared_path") or row.get("azura_path") or "")
        if liquidsoap_queue.request_path_in_library(path):
            if not playing_candidate:
                playing_candidate = row
    if title and title.lower() not in {"unknown", "unknown title", "auto dj", "autodj"}:
        for row in playing_rows:
            filename = str(row.get("temp_filename") or "")
            if generated_title and radio_db.normalize_generated_request_name(filename) == normalized_title:
                return row
    if playing_candidate and (title_is_unknown or generated_title):
        return playing_candidate
    return None


def _finalize_previous_request_if_changed(track: dict) -> None:
    """L8 controller owns played cleanup until Liquidsoap callbacks exist."""
    return


def _schedule_request_prequeue(bot, reason: str, playing_request_id: int = 0) -> None:
    if not bot:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(
        drain_ready_requests_to_liquidsoap(bot, reason=reason, playing_request_id=playing_request_id),
        name=f"radio_prequeue_{reason}_{playing_request_id}",
    )


async def prequeue_next_ready_requests(bot, playing_request_id: int = 0, allow_initial: bool = False) -> int:
    return await drain_ready_requests_to_liquidsoap(
        bot,
        reason="legacy_prequeue",
        playing_request_id=playing_request_id,
        allow_initial=allow_initial,
    )


async def drain_ready_requests_to_liquidsoap(
    bot,
    reason: str,
    playing_request_id: int = 0,
    allow_initial: bool = False,
) -> int:
    ensure_ready()
    return await cleanup_played_liquidsoap_requests(reason=reason)


async def cleanup_played_liquidsoap_requests(reason: str = "poll_tick") -> int:
    ensure_ready()
    return await asyncio.to_thread(_cleanup_played_liquidsoap_requests_sync, reason)


def _cleanup_played_liquidsoap_requests_sync(reason: str = "poll_tick") -> int:
    buffer_secs = radio_settings.liquidsoap_cleanup_buffer_secs()
    fallback_duration = radio_settings.liquidsoap_cleanup_fallback_duration_secs()
    next_files = liquidsoap_queue.list_request_files("next")
    playing_files = liquidsoap_queue.list_request_files("playing")
    ready_rows = radio_db.ready_requests_for_release(limit=10)
    released_rows = radio_db.released_requests(limit=10)
    playing_rows = radio_db.playing_requests(limit=10)
    print(
        f"[RADIO_CONTROLLER_SCAN] reason={reason!r} ready_count={len(ready_rows)} "
        f"released_count={len(released_rows)} playing_count={len(playing_rows)} "
        f"legacy_next_files={len(next_files)} legacy_playing_files={len(playing_files)} "
        f"buffer_secs={buffer_secs} fallback_duration_secs={fallback_duration}"
    )

    if next_files or playing_files:
        print(
            f"[RADIO_LEGACY_QUEUE_FILES_FOUND] next_files={[path.name for path in next_files]!r} "
            f"playing_files={[path.name for path in playing_files]!r}"
        )

    if len(released_rows) > 1 or len(playing_rows) > 1:
        print(
            f"[RADIO_QUEUE_INVARIANT_VIOLATION] reason='multiple_active_db_rows' "
            f"released_count={len(released_rows)} playing_count={len(playing_rows)}"
        )

    if playing_rows:
        row = playing_rows[0]
        request_id = int(row.get("id") or 0)
        path = str(row.get("current_path") or row.get("prepared_path") or row.get("released_path") or row.get("azura_path") or "")
        request_path = liquidsoap_queue.request_path_in_library(path)
        filename = str(row.get("temp_filename") or "")
        if not request_path or not request_path.exists() or not request_path.is_file() or not liquidsoap_queue.safe_request_filename(filename):
            print(
                f"[RADIO_CONTROLLER_PLAYED_FAILED] request_id={request_id} "
                f"path={path!r} error='request_file_missing_or_unsafe'"
            )
            return 0
        age_secs = _age_from_timestamp(row.get("playing_at"))
        if age_secs is None:
            print(f"[RADIO_CONTROLLER_PLAYED_FAILED] request_id={request_id} error='missing_playing_at'")
            return 0
        duration = int(row.get("duration_secs") or 0)
        if duration <= 0:
            duration = fallback_duration
        threshold = max(1, int(duration))
        if age_secs < threshold:
            return 0
        print(
            f"[RADIO_REQUEST_ENDED] request_id={request_id} "
            f"path={str(request_path)!r} age_secs={age_secs} threshold_secs={threshold}"
        )
        retention_mode = radio_settings.request_file_retention_mode()
        ok, archived_path, error = liquidsoap_queue.archive_or_delete_request_file(
            str(request_path),
            reason="played",
            mode=retention_mode,
        )
        if not ok:
            print(
                f"[RADIO_CONTROLLER_PLAYED_FAILED] request_id={request_id} "
                f"source={str(request_path)!r} target={archived_path!r} error={error!r}"
            )
            return 0
        final_path = archived_path if archived_path else str(request_path)
        radio_db.mark_status(
            request_id,
            "played",
            current_path=final_path,
            azura_path=final_path,
            finish_reason="controller_played_cleanup",
        )
        print(
            f"[RADIO_CONTROLLER_PLAYED_OK] request_id={request_id} "
            f"source={str(request_path)!r} target={archived_path!r} retention_mode={retention_mode!r}"
        )
        return 1 + _inject_next_ready_request_sync("request_ended")

    if released_rows:
        return 1

    if not ready_rows:
        print(f"[RADIO_REQUEST_QUEUE_EMPTY_AUTODJ] reason={reason!r}")
        return 0

    return _inject_next_ready_request_sync(reason)


def queue_display(limit: int = 10) -> str:
    rows = radio_db.queue_rows(limit)
    rows = [row for row in rows if not liquidsoap_queue.request_path_in_playing(str(row.get("azura_path") or ""))]
    if not rows:
        return "🎶 QUEUE\nEmpty\nSong requests coming soon."
    lines = ["🎶 QUEUE"]
    icons = {
        "pending": "⏳",
        "preparing": "⏳",
        "ready": "✅",
        "released": "▶️",
        "submitted": "✅",
    }
    labels = {
        "pending": "preparing",
        "preparing": "preparing",
        "ready": "ready",
        "released": "up next",
    }
    for idx, row in enumerate(rows, 1):
        status = str(row.get("status") or "")
        icon = icons.get(status, "⏳")
        label = labels.get(status, status or "queued")
        user = row.get("username") or "Unknown Player"
        title = row.get("title") or "Untitled request"
        marker = "🎟️ " if int(row.get("priority") or 0) else ""
        lines.append(f"{idx}. {icon} {marker}@{user} — {title} ({label})")
    return "\n".join(lines)


def _request_line(row: dict) -> str:
    title = row.get("title") or "Untitled request"
    status = row.get("status") or "unknown"
    return f"#{row.get('id')} — {title} ({status})"


def _request_status_line(row: dict) -> str:
    title = row.get("title") or "Untitled request"
    status = str(row.get("status") or "unknown")
    marker = "priority — " if int(row.get("priority") or 0) else ""
    state = "cancellable" if status in {"pending", "pending_search", "preparing", "ready"} and not str(row.get("submitted_at") or "").strip() else "locked"
    return f"#{row.get('id')} — {title} — {marker}{status} — {state}"


def _is_staff_user(username: str) -> bool:
    return permissions.is_owner(username) or permissions.is_staff(username)


def _refund_request_discs_once(job: dict) -> int:
    amount = int(job.get("disc_cost_charged") or 0)
    if amount <= 0:
        return 0
    if str(job.get("submitted_at") or "").strip() or str(job.get("status") or "") in {"submitted", "playing"}:
        return 0
    music_discs.refund_discs(
        job.get("user_id") or "",
        job.get("username") or "",
        amount,
        music_discs.REFUND_REASON,
        actor="radio",
    )
    radio_db.update_request(int(job["id"]), disc_cost_charged=0)
    return amount


def _refund_request_luxe_once(job: dict) -> int:
    if str(job.get("payment_type") or "").lower() != "luxe":
        return 0
    amount = int(job.get("payment_amount") or 0)
    if amount <= 0:
        return 0
    if str(job.get("submitted_at") or "").strip() or str(job.get("status") or "") in {"submitted", "playing"}:
        return 0
    luxe.add_luxe_balance(job.get("user_id") or "", job.get("username") or "", amount)
    luxe.log_luxe_transaction(
        job.get("user_id") or "",
        job.get("username") or "",
        "Priority Song Request 🎟️ Refund",
        amount,
        "luxe",
        f"radio_request:{job.get('id')}",
    )
    radio_db.update_request(int(job["id"]), payment_amount=0)
    print(f"[RADIO_PRIORITY_REQUEST_REFUND] request_id={job.get('id')} amount={amount}")
    return amount


def _refund_request_payment_once(job: dict) -> tuple[int, int]:
    return _refund_request_discs_once(job), _refund_request_luxe_once(job)


def is_request_cancelled_or_terminal(request_id: int) -> bool:
    job = radio_db.get_request(request_id)
    return not job or str(job.get("status") or "") in {"cancelled", "cleaned", "failed", "played", "removed", "skipped", "refunded"}


def _abort_if_cancelled_or_terminal(request_id: int) -> bool:
    job = radio_db.get_request(request_id)
    status = str((job or {}).get("status") or "")
    if not job or status in {"cancelled", "cleaned", "failed", "played", "removed", "skipped", "refunded"}:
        print(f"[RADIO_PHASE6] event=request_prepare_aborted_cancelled request_id={request_id} status={status!r}")
        return True
    return False


def cancel_request(user, request_id: int | None = None) -> str:
    ensure_ready()
    is_staff = _is_staff_user(user.username)
    if request_id is None:
        rows = radio_db.cancelable_requests_for_user(user.id)
        if not rows:
            return "⚠️ You have no cancellable requests."
        if len(rows) > 1:
            print(
                f"[RADIO_PHASE6] event=request_cancel_multiple_requires_id "
                f"username={getattr(user, 'username', '')!r} count={len(rows)}"
            )
            return "⚠️ You have multiple requests. Use !requeststatus then !cancelrequest <id>."
        job = rows[0]
    else:
        job = radio_db.get_request(int(request_id))
        if not job:
            return "⚠️ Request not found."
        if not is_staff and str(job.get("user_id") or "") != str(user.id):
            return "⚠️ You can only cancel your own request."
        print(
            f"[RADIO_PHASE6] event=request_cancel_by_id "
            f"request_id={job.get('id')} username={getattr(user, 'username', '')!r}"
        )
    status = str(job.get("status") or "")
    if not is_staff and (status == "submitted" or str(job.get("submitted_at") or "").strip()):
        print(
            f"[RADIO_PHASE6] event=request_cancel_blocked_late request_id={job.get('id')} "
            f"status={status!r} submitted_at={job.get('submitted_at')!r} username={getattr(user, 'username', '')!r}"
        )
        return "⚠️ This request was already sent to the radio and can no longer be cancelled."
    if not is_staff and status == "playing":
        print(
            f"[RADIO_PHASE6] event=request_cancel_blocked_late request_id={job.get('id')} "
            f"status={status!r} submitted_at={job.get('submitted_at')!r} username={getattr(user, 'username', '')!r}"
        )
        return "⚠️ This request is already playing and can’t be cancelled."
    allowed = {"pending", "pending_search", "preparing", "ready"}
    if status not in allowed:
        if status == "playing":
            return "⚠️ This request is already playing and can’t be cancelled."
        return "⚠️ That request can no longer be cancelled."
    refunded, refunded_luxe = _refund_request_payment_once(job)
    radio_db.mark_status(int(job["id"]), "cancelled", finish_reason="cancelled_by_staff" if is_staff else "cancelled_by_user")
    cleanup.cleanup_request_media(int(job["id"]), reason="cancelled")
    _inject_next_ready_request_sync("request_cancelled")
    if status == "ready":
        print(f"[RADIO_CONTROLLER_CANCEL_READY] request_id={job['id']} username={getattr(user, 'username', '')!r}")
    print(
        f"[RADIO_PHASE6] event=request_cancelled request_id={job['id']} "
        f"by={getattr(user, 'username', '')!r} refunded={refunded} refunded_luxe={refunded_luxe}"
    )
    suffix = f"\nRefunded: {refunded} Song Request 💽" if refunded else ""
    if refunded_luxe:
        suffix += f"\nRefunded: {refunded_luxe} Luxe Tickets 🎫"
    return f"✅ Cancelled request #{job['id']}.\nTitle: {job.get('title') or 'Untitled request'}{suffix}"


def request_status(user) -> str:
    ensure_ready()
    rows = radio_db.active_requests_for_user(user.id, limit=10)
    if not rows:
        return "🎵 You have no active radio requests."
    lines = ["🎵 Your active requests"]
    lines.extend(_request_status_line(row) for row in rows)
    lines.append("Cancel with: !cancelrequest <id>")
    return "\n".join(lines)


def clear_failed_requests(user) -> str:
    ensure_ready()
    if not _is_staff_user(user.username):
        return "This command is staff-only."
    rows = radio_db.failed_requests_for_cleanup(limit=100)
    cleaned = 0
    for row in rows:
        if cleanup.cleanup_request_media(int(row["id"]), reason="staff_clear_failed"):
            cleaned += 1
    _inject_next_ready_request_sync("clear_failed_requests")
    print(f"[RADIO_PHASE6] event=clear_failed_requests by={user.username!r} rows={len(rows)} cleaned={cleaned}")
    return f"🧹 Cleared failed radio requests: {cleaned}/{len(rows)}."


def clear_stuck_requests(user) -> str:
    ensure_ready()
    if not _is_staff_user(user.username):
        return "This command is staff-only."
    rows = radio_db.stuck_requests_for_cleanup(minutes=30, limit=100)
    cleaned = 0
    refunded = 0
    refunded_luxe = 0
    for row in rows:
        row_refund, row_luxe = _refund_request_payment_once(row)
        refunded += row_refund
        refunded_luxe += row_luxe
        radio_db.mark_status(int(row["id"]), "cancelled", finish_reason="staff_clear_stuck")
        if cleanup.cleanup_request_media(int(row["id"]), reason="staff_clear_stuck"):
            cleaned += 1
    _inject_next_ready_request_sync("clear_stuck_requests")
    print(
        f"[RADIO_PHASE6] event=clear_stuck_requests by={user.username!r} "
        f"rows={len(rows)} cleaned={cleaned} refunded={refunded} refunded_luxe={refunded_luxe}"
    )
    return f"🧹 Cleared stuck radio requests: {cleaned}/{len(rows)}.\nRefunded: {refunded} Song Request 💽\nRefunded: {refunded_luxe} Luxe Tickets 🎫"


def health_snapshot() -> dict:
    ensure_ready()
    api = azura.test_api()
    sftp = azura.test_sftp()
    playlist = azura.requests_playlist_snapshot()
    card, track, err = now_playing_card()
    return {
        "radio_enabled": radio_settings.get_bool_setting("radio_enabled", True),
        "api": api,
        "sftp": sftp,
        "requests_playlist": playlist,
        "nowplaying_ok": bool(track),
        "track": track,
        "queue_size": radio_db.active_queue_count(),
        "last_error": err or radio_db.get_runtime_state("last_error", "none"),
        "card": card,
    }


def track_dedupe_key(track: dict | None) -> str:
    if not track:
        return ""
    return str(track.get("track_key") or "")


def _has_duplicate_active_youtube_request(user_id: str, clean_url: str) -> bool:
    target_video_id = sources.youtube_video_id(clean_url)
    for row in radio_db.active_requests_for_user(user_id):
        if str(row.get("source_type") or "") != "youtube":
            continue
        source_ref = str(row.get("source_ref") or "")
        if source_ref == clean_url:
            return True
        if target_video_id and sources.youtube_video_id(source_ref) == target_video_id:
            return True
    return False


async def submit_direct_youtube_request(bot, user, url: str, priority: bool = False) -> str:
    ensure_ready()
    if priority:
        print(f"[RADIO_PRIORITY_REQUEST_START] username={getattr(user, 'username', '')!r} source='direct'")
    if not radio_settings.get_bool_setting("radio_enabled", True):
        return "📻 Music system is currently disabled."
    if not radio_settings.get_bool_setting("youtube_direct_url_enabled", True):
        return "🔒 Direct YouTube URL requests are currently disabled."
    try:
        clean_url = sources.validate_youtube_url(url)
    except sources.SourceError as exc:
        return f"⚠️ {exc}"
    if _has_duplicate_active_youtube_request(user.id, clean_url):
        return "⚠️ You already have this song in the queue."
    try:
        meta = await asyncio.to_thread(sources.fetch_metadata, clean_url)
    except sources.SourceError as exc:
        return f"⚠️ {exc}"
    role = user_role(user.username)
    max_duration = max_duration_for_role(role)
    duration = int(meta.get("duration") or 0)
    if max_duration and duration > max_duration:
        return f"⚠️ That video is too long ({renderer.format_duration(duration)}). Your limit is {renderer.format_duration(max_duration)}."
    cost = request_cost_for_role(role)
    payment_type = "disc"
    payment_amount = cost
    payment_reason = music_discs.REQUEST_REASON if cost else ""
    if priority:
        cost = 0
        payment_type = "luxe"
        payment_amount = priority_luxe_cost()
        payment_reason = "Priority Song Request 🎟️" if payment_amount else ""
        if payment_amount > 0 and not luxe.deduct_luxe_balance(user.id, user.username, payment_amount):
            print(
                f"[RADIO_PRIORITY_REQUEST_PAYMENT_FAILED] username={user.username!r} "
                f"amount={payment_amount} balance={luxe.get_luxe_balance(user.id)}"
            )
            return f"⚠️ You need {payment_amount} Luxe Tickets 🎫 for a priority request."
        if payment_amount > 0:
            luxe.log_luxe_transaction(user.id, user.username, "Priority Song Request 🎟️", payment_amount, "luxe", clean_url)
        print(f"[RADIO_PRIORITY_REQUEST_PAYMENT_OK] username={user.username!r} amount={payment_amount}")
    elif cost > 0 and not music_discs.deduct_discs(user.id, user.username, cost, music_discs.REQUEST_REASON, actor="radio"):
        return f"⚠️ You need {cost} Song Request 💽 to request this song."
    request_id = radio_db.create_request(
        user.id,
        user.username,
        "youtube",
        clean_url,
        duration,
        meta.get("title") or "YouTube Request",
        meta.get("artist") or "YouTube",
        cost,
        payment_reason,
        is_staff_free=(not priority) and cost == 0 and role in {"staff", "owner"},
        is_vip=role == "vip",
        priority=priority,
        payment_type=payment_type,
        payment_amount=payment_amount,
    )
    asyncio.create_task(process_request_job(bot, request_id), name=f"radio_request_{request_id}")
    _schedule_request_prequeue(bot, "request_added", 0)
    return (
        "✅ Added to queue\n"
        f"Title: {meta.get('title') or 'YouTube Request'}\n"
        f"Position: #{len(radio_db.queue_rows(50))}\n"
        f"Cost: {payment_amount} {'Luxe Tickets 🎫' if priority else 'Song Request 💽'}\n"
        "Please wait…"
    )


async def process_request_job(bot, request_id: int) -> None:
    job = radio_db.get_request(request_id)
    if not job:
        return
    local_path = None
    try:
        if _abort_if_cancelled_or_terminal(request_id):
            return
        radio_db.mark_status(request_id, "preparing")
        staging_filename = sources.safe_youtube_filename(request_id)
        is_priority = bool(int(job.get("priority") or 0))
        final_filename = liquidsoap_queue.request_filename(request_id, job.get("title") or "request", priority=is_priority)
        radio_db.update_request(request_id, temp_filename=final_filename)
        if _abort_if_cancelled_or_terminal(request_id):
            return
        local_path = await asyncio.to_thread(sources.download_youtube_mp3, job["source_ref"], staging_filename)
        if _abort_if_cancelled_or_terminal(request_id):
            return
        print(
            f"[RADIO_LIQUIDSOAP_HANDOFF_START] request_id={request_id} "
            f"source={str(local_path)!r} filename={final_filename!r}"
        )
        ok, target_path, error = await asyncio.to_thread(
            liquidsoap_queue.prepare_to_library,
            str(local_path),
            request_id,
            job.get("title") or "request",
            is_priority,
        )
        if not ok:
            print(
                f"[RADIO_LIQUIDSOAP_HANDOFF_FAILED] request_id={request_id} "
                f"target={target_path!r} error={error!r}"
            )
            raise RuntimeError(f"liquidsoap_handoff_failed:{error}")
        radio_db.mark_status(
            request_id,
            "ready",
            temp_filename=os.path.basename(target_path),
            prepared_path=target_path,
            azura_file_id="",
            azura_song_id="",
            azura_path=target_path,
        )
        print(
            f"[RADIO_REQUEST_PREPARED] request_id={request_id} "
            f"path={target_path!r} priority={int(is_priority)}"
        )
        await asyncio.to_thread(_inject_next_ready_request_sync, "request_ready")
        _schedule_request_prequeue(bot, "request_ready", 0)
    except Exception as exc:
        await _fail_request_before_play(bot, request_id, repr(exc))
    finally:
        if local_path:
            try:
                os.remove(local_path)
            except Exception:
                pass


async def _fail_request_before_play(bot, request_id: int, error: str) -> None:
    job = radio_db.get_request(request_id)
    if not job or job.get("status") == "playing":
        return
    if is_request_cancelled_or_terminal(request_id):
        print(
            f"[RADIO_PHASE6] event=request_fail_ignored_terminal "
            f"request_id={request_id} status={str(job.get('status') or '')!r}"
        )
        return
    radio_db.set_runtime_state("last_error", error[:500])
    radio_db.mark_status(request_id, "failed", error=error, finish_reason="prepare_failed")
    refunded_discs, refunded_luxe = _refund_request_payment_once(job)
    cleanup.cleanup_request_media(request_id, reason="failed_before_play")
    await asyncio.to_thread(_inject_next_ready_request_sync, "request_failed_before_play")
    try:
        if refunded_luxe:
            await bot.highrise.send_whisper(job.get("user_id"), "❌ Could not prepare that priority song. Your Luxe Tickets 🎫 were refunded.")
        elif refunded_discs:
            await bot.highrise.send_whisper(job.get("user_id"), "❌ Could not prepare that song. Your Song Request 💽 was refunded.")
        else:
            await bot.highrise.send_whisper(job.get("user_id"), "❌ Could not prepare that song.")
    except Exception:
        pass
