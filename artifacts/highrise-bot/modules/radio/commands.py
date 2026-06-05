"""Phase 3 radio skeleton commands."""

from __future__ import annotations

from modules import permissions
from modules.radio import autodj_sync
from modules.radio import service
from modules.radio import settings as radio_settings


async def _w(bot, user_id: str, message: str) -> None:
    try:
        await bot.highrise.send_whisper(user_id, message[:900])
    except Exception:
        pass


async def handle_radiohelp(bot, user, args=None) -> None:
    await _w(
        bot,
        user.id,
        "\n".join(
            [
                "🎵 Radio Help",
                "!musicshop — buy Song Request 💽",
                "!discs — check your 💽 balance",
                "!q / !queue — view queue",
                "!now / !np — current song",
                "!play <youtube_url> — request a direct YouTube link",
                "!play <song name> — search YouTube",
                "!pick 1-5 — request a search result",
                "!favnow — save the current request",
                "!favorites — list saved songs",
                "!playfav <number> — request a saved song",
                "!cancelrequest — cancel your queued request",
                "!requeststatus — view your active requests",
                "!syncvibe <vibe> <spotify_playlist_url> — owner AutoDJ import",
                "!syncstatus [job_id] — owner AutoDJ import status",
                "!vibe — owner active AutoDJ vibe",
                "!setvibe <vibe_name> — owner set active AutoDJ vibe",
                "!badtrack [reason] — owner reject current AutoDJ track",
                "!radiohelp — this help",
                "Favorites are still being rebuilt.",
            ]
        ),
    )


async def handle_queue(bot, user, args=None) -> None:
    await _w(bot, user.id, service.queue_display())


async def handle_now(bot, user, args=None) -> None:
    card, _track, _error = service.now_playing_card(bot)
    message = card or "📻 Radio is live, but I can't read the current track right now."
    if str(radio_settings.get_setting("now_command_response_mode", "whisper")).lower() == "chat":
        try:
            await bot.highrise.chat(message[:900])
            return
        except Exception:
            pass
    await _w(bot, user.id, message)


async def handle_musicstatus(bot, user, args=None) -> None:
    if not permissions.is_staff(user.username):
        await _w(bot, user.id, "This command is staff-only.")
        return
    snap = service.health_snapshot()
    playlist = snap.get("requests_playlist") or {}
    lines = [
        "🎛 Music Status",
        f"Radio: {'enabled' if snap['radio_enabled'] else 'disabled'}",
        f"Azura API: {'ok' if snap['api'].get('ok') else 'fail'}",
        f"SFTP: {'ok' if snap['sftp'].get('ok') else 'fail'}",
        f"Requests Playlist: {playlist.get('playlist_id') or 'not configured'} HTTP {playlist.get('status') or 0}",
        f"Playlist Source: {playlist.get('playlist_id_source') or 'missing'}",
        f"Requests Enabled: {playlist.get('requests_enabled', playlist.get('include_in_requests', 'unknown'))}",
        f"NowPlaying: {'ok' if snap['nowplaying_ok'] else 'fail'}",
        f"Queue: {snap['queue_size']}",
        f"Last error: {snap['last_error'] or 'none'}",
    ]
    await _w(bot, user.id, "\n".join(lines))


async def handle_play(bot, user, args=None) -> None:
    args = list(args or [])
    if args and str(args[0]).lower() == "play":
        args = args[1:]
    if not args:
        await _w(bot, user.id, "Use: !play <youtube_url or song name>")
        return
    query = " ".join(args).strip()
    if query.startswith(("http://", "https://")):
        message = await service.submit_direct_youtube_request(bot, user, query)
    else:
        message = await service.search_youtube_request(user, query)
    await _w(bot, user.id, message)


async def handle_pick(bot, user, args=None) -> None:
    args = list(args or [])
    if args and str(args[0]).lower() in {"pick", "djpick"}:
        args = args[1:]
    if not args:
        await _w(bot, user.id, "⚠️ Pick a number from the search results: !pick 1")
        return
    try:
        pick_number = int(str(args[0]).strip())
    except (TypeError, ValueError):
        await _w(bot, user.id, "⚠️ Pick a number from the search results: !pick 1")
        return
    message = await service.pick_youtube_search_result(bot, user, pick_number)
    await _w(bot, user.id, message)


async def handle_cancelrequest(bot, user, args=None) -> None:
    args = list(args or [])
    if args and str(args[0]).lower() == "cancelrequest":
        args = args[1:]
    request_id = None
    if args:
        try:
            request_id = int(str(args[0]).strip())
        except (TypeError, ValueError):
            await _w(bot, user.id, "Use: !cancelrequest or !cancelrequest <id>")
            return
    await _w(bot, user.id, service.cancel_request(user, request_id))


async def handle_requeststatus(bot, user, args=None) -> None:
    await _w(bot, user.id, service.request_status(user))


async def handle_clearfailedrequests(bot, user, args=None) -> None:
    await _w(bot, user.id, service.clear_failed_requests(user))


async def handle_clearstuckrequests(bot, user, args=None) -> None:
    await _w(bot, user.id, service.clear_stuck_requests(user))


async def handle_favnow(bot, user, args=None) -> None:
    await _w(bot, user.id, service.favorite_now(user))


async def handle_favorites(bot, user, args=None) -> None:
    await _w(bot, user.id, service.favorites_list(user))


async def handle_playfav(bot, user, args=None) -> None:
    args = list(args or [])
    if args and str(args[0]).lower() == "playfav":
        args = args[1:]
    if not args:
        await _w(bot, user.id, "Use: !playfav <number>")
        return
    try:
        number = int(str(args[0]).strip())
    except (TypeError, ValueError):
        await _w(bot, user.id, "Use: !playfav <number>")
        return
    await _w(bot, user.id, await service.play_favorite(bot, user, number))


async def handle_removefavorite(bot, user, args=None) -> None:
    args = list(args or [])
    if args and str(args[0]).lower() in {"removefavorite", "delfav"}:
        args = args[1:]
    if not args:
        await _w(bot, user.id, "Use: !removefavorite <number>")
        return
    try:
        number = int(str(args[0]).strip())
    except (TypeError, ValueError):
        await _w(bot, user.id, "Use: !removefavorite <number>")
        return
    await _w(bot, user.id, service.remove_favorite(user, number))


async def handle_syncvibe(bot, user, args=None) -> None:
    if not permissions.is_owner(user.username):
        await _w(bot, user.id, "This command is owner-only.")
        return
    args = list(args or [])
    if args and str(args[0]).lower() == "syncvibe":
        args = args[1:]
    if len(args) < 2:
        await _w(bot, user.id, "Use: !syncvibe <vibe_name> <spotify_playlist_url>")
        return
    _ok, message = autodj_sync.start_sync(str(args[0]), str(args[1]))
    await _w(bot, user.id, message)


async def handle_autodj_syncstatus(bot, user, args=None) -> None:
    if not permissions.is_owner(user.username):
        await _w(bot, user.id, "This command is owner-only.")
        return
    args = list(args or [])
    if args and str(args[0]).lower() == "syncstatus":
        args = args[1:]
    await _w(bot, user.id, autodj_sync.status(str(args[0]) if args else ""))


async def handle_vibe(bot, user, args=None) -> None:
    if not permissions.is_owner(user.username):
        await _w(bot, user.id, "This command is owner-only.")
        return
    await _w(bot, user.id, autodj_sync.vibe_status())


async def handle_setvibe(bot, user, args=None) -> None:
    if not permissions.is_owner(user.username):
        await _w(bot, user.id, "This command is owner-only.")
        return
    args = list(args or [])
    if args and str(args[0]).lower() == "setvibe":
        args = args[1:]
    if not args:
        await _w(bot, user.id, "Use: !setvibe <vibe_name>")
        return
    _ok, message = autodj_sync.set_active_vibe(" ".join(str(part) for part in args))
    await _w(bot, user.id, message)


async def handle_badtrack(bot, user, args=None) -> None:
    if not permissions.is_owner(user.username):
        await _w(bot, user.id, "This command is owner-only.")
        return
    args = list(args or [])
    if args and str(args[0]).lower() == "badtrack":
        args = args[1:]
    await _w(bot, user.id, service.reject_current_autodj_track(" ".join(str(part) for part in args)))


async def dispatch_radio_command(bot, user, cmd: str, args: list[str]) -> None:
    if cmd == "play":
        await handle_play(bot, user, args)
    elif cmd in {"pick", "djpick"}:
        await handle_pick(bot, user, args)
    elif cmd == "cancelrequest":
        await handle_cancelrequest(bot, user, args)
    elif cmd == "requeststatus":
        await handle_requeststatus(bot, user, args)
    elif cmd == "clearfailedrequests":
        await handle_clearfailedrequests(bot, user, args)
    elif cmd == "clearstuckrequests":
        await handle_clearstuckrequests(bot, user, args)
    elif cmd in {"favnow", "favorite"}:
        await handle_favnow(bot, user, args)
    elif cmd in {"favorites", "favs"}:
        await handle_favorites(bot, user, args)
    elif cmd == "playfav":
        await handle_playfav(bot, user, args)
    elif cmd in {"removefavorite", "delfav"}:
        await handle_removefavorite(bot, user, args)
    elif cmd == "syncvibe":
        await handle_syncvibe(bot, user, args)
    elif cmd == "syncstatus":
        await handle_autodj_syncstatus(bot, user, args)
    elif cmd == "vibe":
        await handle_vibe(bot, user, args)
    elif cmd == "setvibe":
        await handle_setvibe(bot, user, args)
    elif cmd == "badtrack":
        await handle_badtrack(bot, user, args)
    elif cmd == "radiohelp":
        await handle_radiohelp(bot, user, args)
    elif cmd in {"q", "queue"}:
        await handle_queue(bot, user, args)
    elif cmd in {"now", "np"}:
        await handle_now(bot, user, args)
    elif cmd in {"radiotest", "musicstatus"}:
        await handle_musicstatus(bot, user, args)
