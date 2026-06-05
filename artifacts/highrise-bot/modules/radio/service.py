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
from modules.radio import search
from modules.radio import sources
from modules.radio import settings as radio_settings


_PREQUEUE_LOCK = asyncio.Lock()


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
    normalized = radio_db.normalize_generated_request_name(title)
    return str(title or "").strip().startswith(azura.SAFE_PREFIXES) or normalized.startswith(
        ("radio yt ", "radio local ", "radio req ")
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


async def search_youtube_request(user, query: str) -> str:
    ensure_ready()
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
    )
    return _render_search_results(results)


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
    message = await submit_direct_youtube_request(bot, user, url)
    if message.startswith("✅ Added to queue"):
        radio_db.clear_search_session(user.id)
    return message


def now_playing_card(bot=None) -> tuple[str | None, dict | None, str]:
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
            _schedule_request_prequeue(bot, int(request["id"]))
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
    normalized_title = radio_db.normalize_generated_request_name(title)
    normalized_filename = radio_db.normalize_generated_request_name(filename)
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
    if normalized_title.startswith(("radio yt ", "radio local ", "radio req ")) and normalized_filename and normalized_title == normalized_filename:
        same = True
    if normalized_title.startswith(tuple(f"{prefix}{request_id}" for prefix in ("radio yt ", "radio local ", "radio req "))):
        same = True
    if same:
        return
    radio_db.mark_status(request_id, "played", finish_reason="song_changed")
    cleanup.cleanup_request_media(request_id, reason="played")
    radio_db.set_runtime_state("current_request_id", "")


def _schedule_request_prequeue(bot, playing_request_id: int) -> None:
    if not bot:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(
        prequeue_next_ready_requests(bot, playing_request_id=playing_request_id),
        name=f"radio_prequeue_after_playing_{playing_request_id}",
    )


async def prequeue_next_ready_requests(bot, playing_request_id: int = 0, allow_initial: bool = False) -> int:
    ensure_ready()
    enabled = radio_settings.get_bool_setting("radio_request_prequeue_enabled", True)
    max_submitted = radio_settings.radio_request_prequeue_count() if enabled else 0
    async with _PREQUEUE_LOCK:
        submitted_count = radio_db.submitted_request_count()
        current_request_id = radio_db.get_runtime_state("current_request_id", "")
        if max_submitted <= 0:
            if not allow_initial or current_request_id or submitted_count:
                print(
                    f"[RADIO_PHASE8] event=request_prequeue_skip reason=disabled "
                    f"playing_request_id={playing_request_id}"
                )
                return 0
            max_submitted = 1
        ready_rows = radio_db.ready_requests_for_prequeue(limit=max_submitted + 1)
        print(
            f"[RADIO_PHASE8] event=request_prequeue_check "
            f"playing_request_id={playing_request_id} ready_count={len(ready_rows)} "
            f"submitted_count={submitted_count}"
        )
        if submitted_count >= max_submitted:
            print(
                f"[RADIO_PHASE8] event=request_prequeue_skip reason=already_submitted "
                f"playing_request_id={playing_request_id} submitted_count={submitted_count}"
            )
            return 0
        if not ready_rows:
            print(
                f"[RADIO_PHASE8] event=request_prequeue_skip reason=no_ready_requests "
                f"playing_request_id={playing_request_id}"
            )
            return 0
        slots = max_submitted - submitted_count
        submitted = 0
        attempts = 0
        for row in ready_rows:
            if submitted >= slots:
                break
            if attempts >= slots + 1:
                break
            attempts += 1
            request_id = int(row["id"])
            print(
                f"[RADIO_PHASE8] event=request_prequeue_submit "
                f"request_id={request_id} position={submitted_count + submitted + 1}"
            )
            if await _submit_ready_request_to_azura(bot, request_id, reason="prequeue"):
                submitted += 1
            else:
                print(f"[RADIO_PHASE8] event=request_prequeue_failed request_id={request_id}")
        return submitted


async def _submit_ready_request_to_azura(bot, request_id: int, reason: str = "ready") -> bool:
    job = radio_db.get_request(request_id)
    if not job:
        return False
    status = str(job.get("status") or "")
    if status != "ready":
        print(
            f"[RADIO_PHASE8] event=request_prequeue_skip reason=status_not_ready "
            f"request_id={request_id} status={status!r}"
        )
        return False
    song_id = str(job.get("azura_song_id") or "").strip()
    if not song_id:
        await _fail_request_before_play(bot, request_id, "missing_azura_song_id")
        return False
    ok, status_code, body = await asyncio.to_thread(azura.submit_request, song_id)
    if not ok:
        if "not requestable" in str(body).lower():
            playlist_snapshot = await asyncio.to_thread(azura.requests_playlist_snapshot)
            file_id = str(job.get("azura_file_id") or "").strip()
            media_after = await asyncio.to_thread(azura.get_media_file, file_id) if file_id else {}
            print(
                f"[RADIO_PHASE4] event=azura_not_requestable_after_playlist_assign "
                f"request_id={request_id} media_id={file_id!r} unique_id={song_id!r} "
                f"playlist_id={playlist_snapshot.get('playlist_id')!r} "
                f"playlist_config={playlist_snapshot!r} "
                f"media_playlists={(media_after or {}).get('playlists')!r}"
            )
        print(
            f"[RADIO_PHASE8] event=request_prequeue_failed request_id={request_id} "
            f"status={status_code} body={str(body)[:240]!r} reason={reason!r}"
        )
        await _fail_request_before_play(
            bot,
            request_id,
            f"azura_submit_failed status={status_code} body={str(body)[:200]!r}",
        )
        return False
    if _abort_if_cancelled_or_terminal(request_id):
        return False
    radio_db.mark_status(request_id, "submitted")
    print(
        f"[RADIO_PHASE8] event=request_prequeue_submitted request_id={request_id} "
        f"song_id={song_id!r} status={status_code} reason={reason!r}"
    )
    return True


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


def _request_line(row: dict) -> str:
    title = row.get("title") or "Untitled request"
    status = row.get("status") or "unknown"
    return f"#{row.get('id')} — {title} ({status})"


def _request_status_line(row: dict) -> str:
    title = row.get("title") or "Untitled request"
    status = str(row.get("status") or "unknown")
    state = "cancellable" if status in {"pending", "preparing", "ready"} and not str(row.get("submitted_at") or "").strip() else "locked"
    return f"#{row.get('id')} — {title} — {status} — {state}"


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


def is_request_cancelled_or_terminal(request_id: int) -> bool:
    job = radio_db.get_request(request_id)
    return not job or str(job.get("status") or "") in {"cancelled", "cleaned", "failed", "played"}


def _abort_if_cancelled_or_terminal(request_id: int) -> bool:
    job = radio_db.get_request(request_id)
    status = str((job or {}).get("status") or "")
    if not job or status in {"cancelled", "cleaned", "failed", "played"}:
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
    allowed = {"pending", "preparing", "ready"}
    if status not in allowed:
        if status == "playing":
            return "⚠️ This request is already playing and can’t be cancelled."
        return "⚠️ That request can no longer be cancelled."
    refunded = _refund_request_discs_once(job)
    radio_db.mark_status(int(job["id"]), "cancelled", finish_reason="cancelled_by_staff" if is_staff else "cancelled_by_user")
    cleanup.cleanup_request_media(int(job["id"]), reason="cancelled")
    print(
        f"[RADIO_PHASE6] event=request_cancelled request_id={job['id']} "
        f"by={getattr(user, 'username', '')!r} refunded={refunded}"
    )
    suffix = f"\nRefunded: {refunded} Song Request 💽" if refunded else ""
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
    print(f"[RADIO_PHASE6] event=clear_failed_requests by={user.username!r} rows={len(rows)} cleaned={cleaned}")
    return f"🧹 Cleared failed radio requests: {cleaned}/{len(rows)}."


def clear_stuck_requests(user) -> str:
    ensure_ready()
    if not _is_staff_user(user.username):
        return "This command is staff-only."
    rows = radio_db.stuck_requests_for_cleanup(minutes=30, limit=100)
    cleaned = 0
    refunded = 0
    for row in rows:
        refunded += _refund_request_discs_once(row)
        radio_db.mark_status(int(row["id"]), "cancelled", finish_reason="staff_clear_stuck")
        if cleanup.cleanup_request_media(int(row["id"]), reason="staff_clear_stuck"):
            cleaned += 1
    print(
        f"[RADIO_PHASE6] event=clear_stuck_requests by={user.username!r} "
        f"rows={len(rows)} cleaned={cleaned} refunded={refunded}"
    )
    return f"🧹 Cleared stuck radio requests: {cleaned}/{len(rows)}.\nRefunded: {refunded} Song Request 💽"


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
        if _abort_if_cancelled_or_terminal(request_id):
            return
        radio_db.mark_status(request_id, "preparing")
        filename = sources.safe_youtube_filename(request_id)
        remote_path = azura.build_requests_remote_path(filename)
        radio_db.update_request(request_id, temp_filename=filename, azura_path=remote_path)
        if _abort_if_cancelled_or_terminal(request_id):
            return
        local_path = await asyncio.to_thread(sources.download_youtube_mp3, job["source_ref"], filename)
        if _abort_if_cancelled_or_terminal(request_id):
            return
        if not await asyncio.to_thread(azura.upload_request_file, str(local_path), filename):
            raise RuntimeError("upload_failed")

        if _abort_if_cancelled_or_terminal(request_id):
            return
        radio_db.update_request(request_id, azura_path=remote_path)
        media = None
        song_id = ""
        if _abort_if_cancelled_or_terminal(request_id):
            return
        for attempt in range(15):
            if _abort_if_cancelled_or_terminal(request_id):
                return
            if attempt == 0 or attempt % 5 == 0:
                await asyncio.to_thread(azura.rescan_requests_folder)
            media = await asyncio.to_thread(azura.find_uploaded_media, filename)
            song_id = azura.media_unique_id(media)
            if media and song_id:
                break
            await asyncio.sleep(2)
        if _abort_if_cancelled_or_terminal(request_id):
            return
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
            if _abort_if_cancelled_or_terminal(request_id):
                return
            assigned = await asyncio.to_thread(azura.attach_requests_playlist, file_id)
            if not assigned:
                print(
                    f"[RADIO_PHASE4] event=azura_playlist_assign_failed "
                    f"request_id={request_id} media_id={file_id!r}"
                )
        if _abort_if_cancelled_or_terminal(request_id):
            return
        radio_db.mark_status(
            request_id,
            "ready",
            azura_file_id=file_id,
            azura_song_id=song_id,
            azura_path=azura_path,
        )
        requestable = False
        for attempt in range(10):
            if _abort_if_cancelled_or_terminal(request_id):
                return
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
        if _abort_if_cancelled_or_terminal(request_id):
            return
        if not requestable:
            print(
                f"[RADIO_PHASE4] event=azura_requestable_not_confirmed request_id={request_id} "
                f"filename={filename!r} unique_id={song_id!r} action=submit_once"
            )
        if _abort_if_cancelled_or_terminal(request_id):
            return
        await prequeue_next_ready_requests(bot, playing_request_id=0, allow_initial=True)
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
    if int(job.get("disc_cost_charged") or 0) > 0:
        music_discs.refund_discs(
            job.get("user_id") or "",
            job.get("username") or "",
            int(job.get("disc_cost_charged") or 0),
            music_discs.REFUND_REASON,
            actor="radio",
        )
        radio_db.update_request(request_id, disc_cost_charged=0)
    cleanup.cleanup_request_media(request_id, reason="failed_before_play")
    try:
        await bot.highrise.send_whisper(job.get("user_id"), "❌ Could not prepare that song. Your Song Request 💽 was refunded.")
    except Exception:
        pass
