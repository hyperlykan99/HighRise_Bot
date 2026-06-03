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


def _renderer_settings() -> dict:
    return {
        "now_show_progress_bar": radio_settings.get_bool_setting("now_show_progress_bar", True),
        "now_show_likes_dislikes": radio_settings.get_bool_setting("now_show_likes_dislikes", True),
        "now_show_request_play_count": radio_settings.get_bool_setting("now_show_request_play_count", True),
        "now_footer_text": radio_settings.get_setting("now_footer_text", "🎶 !play to request a song"),
    }


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
    if request:
        source = "request"
        requester = request.get("username") or None
        if request.get("status") != "playing":
            radio_db.mark_status(request["id"], "playing")
            radio_db.increment_request_play_count(track.get("track_key", ""), track.get("title", ""), track.get("artist", ""))
            radio_db.set_runtime_state("current_request_id", request["id"])
            print(f"[RADIO_PHASE4] event=request_detected_playing request_id={request['id']} filename={request.get('temp_filename')!r}")
    stats = radio_db.read_track_stats(track.get("track_key", ""), track.get("title", ""), track.get("artist", ""))
    radio_db.mark_last_poll(True)
    radio_db.set_runtime_state("current_track", track)
    return renderer.render_now_playing_card(track, source, requester=requester, stats=stats, settings=_renderer_settings()), track, ""


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
    same = False
    if job.get("azura_file_id") and str(job.get("azura_file_id")) == str(track.get("media_id") or ""):
        same = True
    if job.get("azura_song_id") and str(job.get("azura_song_id")) in {str(track.get("unique_id") or ""), str(track.get("song_id") or "")}:
        same = True
    if filename and filename in {str(track.get("filename") or ""), str(track.get("path") or "").rsplit("/", 1)[-1]}:
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
    card, track, err = now_playing_card()
    return {
        "radio_enabled": radio_settings.get_bool_setting("radio_enabled", True),
        "api": api,
        "sftp": sftp,
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
        radio_db.update_request(request_id, temp_filename=filename, azura_path=f"Requests/{filename}")
        local_path = await asyncio.to_thread(sources.download_youtube_mp3, job["source_ref"], filename)
        if not await asyncio.to_thread(azura.upload_request_file, str(local_path), filename):
            raise RuntimeError("upload_failed")
        await asyncio.to_thread(azura.rescan_requests_folder)
        media = None
        for _ in range(6):
            media = await asyncio.to_thread(azura.find_uploaded_media, filename)
            if media:
                break
            await asyncio.sleep(2)
        if not media:
            raise RuntimeError("azura_media_lookup_failed")
        file_id = str(media.get("id") or media.get("media_id") or "")
        song_id = str(media.get("unique_id") or media.get("song_id") or media.get("id") or "")
        if file_id:
            await asyncio.to_thread(azura.attach_requests_playlist, file_id)
        radio_db.mark_status(
            request_id,
            "ready",
            azura_file_id=file_id,
            azura_song_id=song_id,
            azura_path=media.get("path") or f"Requests/{filename}",
        )
        if not await asyncio.to_thread(azura.submit_request, song_id):
            raise RuntimeError("azura_request_submit_failed")
        radio_db.mark_status(request_id, "submitted")
        print(f"[RADIO_PHASE4] event=request_submitted request_id={request_id} song_id={song_id!r}")
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
