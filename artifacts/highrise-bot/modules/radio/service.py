"""Radio service layer for the rebuilt direct-URL pipeline."""

from __future__ import annotations

import asyncio
import os

import database as root_db
from modules import permissions
from modules.radio import cleanup
from modules.radio import azura
from modules.radio import db as radio_db
from modules.radio import music_discs
from modules.radio import renderer
from modules.radio import sources
from modules.radio import settings as radio_settings


def ensure_ready() -> None:
    radio_db.ensure_schema()
    radio_settings.ensure_radio_settings()


def recover_stale_ready_submitted_requests() -> None:
    ensure_ready()
    for row in radio_db.stale_ready_submitted_requests(minutes=30):
        filename = str(row.get("temp_filename") or "")
        if not filename or not azura.safe_request_filename(filename):
            continue
        if azura.find_uploaded_media(filename):
            continue
        radio_db.mark_status(
            int(row["id"]),
            "failed",
            error="startup_recovery_missing_azura_media",
            finish_reason="startup_recovery_missing_azura_media",
        )
        print(
            f"[RADIO_PHASE4] event=startup_recovery_stale_request_failed "
            f"request_id={row['id']} status={row.get('status')!r} filename={filename!r}"
        )


def _renderer_settings() -> dict:
    return {
        "now_show_progress_bar": radio_settings.get_bool_setting("now_show_progress_bar", True),
        "now_show_likes_dislikes": radio_settings.get_bool_setting("now_show_likes_dislikes", True),
        "now_show_request_play_count": radio_settings.get_bool_setting("now_show_request_play_count", True),
        "now_footer_text": radio_settings.get_setting("now_footer_text", "🎶 !play to request a song"),
    }


def _safe_generated_title(title: str) -> bool:
    return str(title or "").strip().startswith(azura.SAFE_PREFIXES)


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


def max_duration_for_role(role: str) -> int:
    defaults = {"normal": 300, "vip": 480, "staff": 600, "owner": 0}
    return max(0, radio_settings.get_int_setting(f"max_song_duration_{role}_secs", defaults.get(role, 300)))


def now_playing_card() -> tuple[str | None, dict | None, str]:
    ensure_ready()
    if not radio_settings.get_bool_setting("radio_enabled", True):
        return "📻 Music system is currently disabled.", None, ""
    np_data = azura.fetch_nowplaying()
    if not np_data:
        radio_db.mark_last_poll(False, "nowplaying_unavailable")
        return "📻 Radio is live, but I can't read the current track right now.", None, "nowplaying_unavailable"
    track = azura.extract_nowplaying_track(np_data)
    if not track:
        radio_db.mark_last_poll(False, "track_parse_failed")
        return "📻 Radio is live, but I can't read the current track right now.", None, "track_parse_failed"
    _finalize_previous_request_if_changed(track)
    request = radio_db.match_request_for_track(track)
    source = "autodj"
    requester = None
    display_track = track
    if request:
        source = "request"
        requester = request.get("username") or None
        display_track = _display_track_for_request(track, request)
        if request.get("status") != "playing":
            radio_db.mark_status(request["id"], "playing")
            radio_db.increment_request_play_count(
                display_track.get("track_key", ""),
                display_track.get("title", ""),
                display_track.get("artist", ""),
            )
            print(f"[RADIO_PHASE4] event=request_detected_playing request_id={request['id']} filename={request.get('temp_filename')!r}")
        radio_db.set_runtime_state("current_request_id", request["id"])
    stats = radio_db.read_track_stats(display_track.get("track_key", ""), display_track.get("title", ""), display_track.get("artist", ""))
    radio_db.mark_last_poll(True)
    radio_db.set_runtime_state("current_track", display_track)
    return renderer.render_now_playing_card(display_track, source, requester=requester, stats=stats, settings=_renderer_settings()), display_track, ""


def _finalize_previous_request_if_changed(track: dict) -> None:
    raw_id = radio_db.get_runtime_state("current_request_id", "")
    if not raw_id:
        return
    try:
        request_id = int(raw_id)
    except ValueError:
        radio_db.set_runtime_state("current_request_id", "")
        return
    job = radio_db.get_request(request_id)
    if not job or job.get("status") != "playing":
        radio_db.set_runtime_state("current_request_id", "")
        return
    filename = str(job.get("temp_filename") or "")
    stem = os.path.splitext(filename)[0] if filename else ""
    title = str(track.get("title") or "")
    same = False
    if job.get("azura_file_id") and str(job.get("azura_file_id")) == str(track.get("media_id") or ""):
        same = True
    if job.get("azura_song_id") and str(job.get("azura_song_id")) in {str(track.get("unique_id") or ""), str(track.get("song_id") or "")}:
        same = True
    if filename and filename in {str(track.get("filename") or ""), str(track.get("path") or "").rsplit("/", 1)[-1]}:
        same = True
    if title.startswith(azura.SAFE_PREFIXES) and filename and title in {filename, stem}:
        same = True
    if title.startswith(azura.SAFE_PREFIXES) and stem and title.startswith(stem):
        same = True
    if title.startswith(azura.SAFE_PREFIXES) and title.startswith(tuple(f"{prefix}{request_id}_" for prefix in azura.SAFE_PREFIXES)):
        same = True
    if same:
        return
    radio_db.mark_status(request_id, "played", finish_reason="song_changed")
    cleanup.cleanup_request_media(request_id, reason="played")
    radio_db.set_runtime_state("current_request_id", "")


def queue_display(limit: int = 10) -> str:
    rows = radio_db.queue_rows(limit)
    if not rows:
        return "🎶 QUEUE\nEmpty\nSong requests coming soon."
    lines = ["🎶 QUEUE"]
    icons = {
        "pending": "⏳",
        "preparing": "⏳",
        "ready": "✅",
        "submitted": "✅",
    }
    for idx, row in enumerate(rows, 1):
        icon = icons.get(str(row.get("status") or ""), "⏳")
        user = row.get("username") or "Unknown Player"
        title = row.get("title") or "Untitled request"
        lines.append(f"{idx}. {icon} @{user} — {title}")
    return "\n".join(lines)


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


async def submit_direct_youtube_request(bot, user, url: str) -> str:
    ensure_ready()
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
    if radio_settings.get_bool_setting("block_requests_when_azura_unhealthy", True):
        api = azura.test_api()
        sftp = azura.test_sftp()
        if not api.get("ok") or not sftp.get("ok"):
            return "⚠️ Radio requests are blocked while AzuraCast is unhealthy."
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
    if cost > 0 and not music_discs.deduct_discs(user.id, user.username, cost, music_discs.REQUEST_REASON, actor="radio"):
        return f"⚠️ You need {cost} Song Request 💽 to request this song."
    request_id = radio_db.create_request(
        user.id,
        user.username,
        "youtube",
        clean_url,
        meta.get("title") or "YouTube Request",
        meta.get("artist") or "YouTube",
        cost,
        music_discs.REQUEST_REASON if cost else "",
        is_staff_free=cost == 0 and role in {"staff", "owner"},
        is_vip=role == "vip",
    )
    asyncio.create_task(process_request_job(bot, request_id), name=f"radio_request_{request_id}")
    return (
        "✅ Added to queue\n"
        f"Title: {meta.get('title') or 'YouTube Request'}\n"
        f"Position: #{len(radio_db.queue_rows(50))}\n"
        f"Cost: {cost} Song Request 💽\n"
        "Please wait…"
    )


async def process_request_job(bot, request_id: int) -> None:
    job = radio_db.get_request(request_id)
    if not job:
        return
    local_path = None
    try:
        radio_db.mark_status(request_id, "preparing")
        filename = sources.safe_youtube_filename(request_id)
        remote_path = azura.build_requests_remote_path(filename)
        radio_db.update_request(request_id, temp_filename=filename, azura_path=remote_path)
        local_path = await asyncio.to_thread(sources.download_youtube_mp3, job["source_ref"], filename)
        if not await asyncio.to_thread(azura.upload_request_file, str(local_path), filename):
            raise RuntimeError("upload_failed")

        radio_db.update_request(request_id, azura_path=remote_path)
        media = None
        song_id = ""
        for attempt in range(15):
            if attempt == 0 or attempt % 5 == 0:
                await asyncio.to_thread(azura.rescan_requests_folder)
            media = await asyncio.to_thread(azura.find_uploaded_media, filename)
            song_id = azura.media_unique_id(media)
            if media and song_id:
                break
            await asyncio.sleep(2)
        if not media or not song_id:
            print(f"[RADIO_PHASE4] event=azura_index_timeout request_id={request_id} filename={filename!r}")
            raise RuntimeError("azura_index_timeout")

        file_id = azura.media_file_id(media)
        azura_path = media.get("path") or remote_path
        print(
            f"[RADIO_PHASE4] event=azura_media_found request_id={request_id} "
            f"media_id={file_id!r} unique_id={song_id!r} path={azura_path!r}"
        )
        if file_id:
            assigned = await asyncio.to_thread(azura.attach_requests_playlist, file_id)
            if not assigned:
                print(
                    f"[RADIO_PHASE4] event=azura_playlist_assign_failed "
                    f"request_id={request_id} media_id={file_id!r}"
                )
        radio_db.mark_status(
            request_id,
            "ready",
            azura_file_id=file_id,
            azura_song_id=song_id,
            azura_path=azura_path,
        )
        requestable = False
        for attempt in range(10):
            requestable = await asyncio.to_thread(
                azura.requestable_media_ready,
                filename,
                song_id,
                job.get("title") or "",
                job.get("artist") or "",
                file_id,
            )
            if requestable:
                break
            await asyncio.sleep(2)
        if not requestable:
            print(
                f"[RADIO_PHASE4] event=azura_requestable_not_confirmed request_id={request_id} "
                f"filename={filename!r} unique_id={song_id!r} action=submit_once"
            )
        ok, status, body = await asyncio.to_thread(azura.submit_request, song_id)
        if not ok:
            playlist_snapshot = await asyncio.to_thread(azura.requests_playlist_snapshot)
            media_after = await asyncio.to_thread(azura.get_media_file, file_id) if file_id else {}
            if "not requestable" in str(body).lower():
                print(
                    f"[RADIO_PHASE4] event=azura_not_requestable_after_playlist_assign "
                    f"request_id={request_id} media_id={file_id!r} unique_id={song_id!r} "
                    f"playlist_id={playlist_snapshot.get('playlist_id')!r} "
                    f"playlist_config={playlist_snapshot!r} "
                    f"media_playlists={(media_after or {}).get('playlists')!r}"
                )
            print(
                f"[RADIO_PHASE4] event=azura_submit_failed request_id={request_id} "
                f"status={status} body={body[:240]!r}"
            )
            raise RuntimeError(f"azura_submit_failed status={status} body={body[:200]!r}")
        radio_db.mark_status(request_id, "submitted")
        print(
            f"[RADIO_PHASE4] event=azura_submit_ok request_id={request_id} "
            f"song_id={song_id!r} status={status}"
        )
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
    radio_db.set_runtime_state("last_error", error[:500])
    radio_db.mark_status(request_id, "failed", error=error, finish_reason="prepare_failed")
    if int(job.get("disc_cost_charged") or 0) > 0:
        music_discs.refund_discs(
            job.get("user_id") or "",
            job.get("username") or "",
            int(job.get("disc_cost_charged") or 0),
            music_discs.REFUND_REASON,
            actor="radio",
        )
    cleanup.cleanup_request_media(request_id, reason="failed_before_play")
    try:
        await bot.highrise.send_whisper(job.get("user_id"), "❌ Could not prepare that song. Your Song Request 💽 was refunded.")
    except Exception:
        pass
