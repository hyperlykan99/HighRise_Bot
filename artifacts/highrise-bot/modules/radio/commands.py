"""Phase 3 radio skeleton commands."""

from __future__ import annotations

from modules import permissions
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
                "!radiohelp — this help",
                "Favorites are still being rebuilt.",
            ]
        ),
    )


async def handle_queue(bot, user, args=None) -> None:
    await _w(bot, user.id, service.queue_display())


async def handle_now(bot, user, args=None) -> None:
    card, _track, _error = service.now_playing_card()
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


async def dispatch_radio_command(bot, user, cmd: str, args: list[str]) -> None:
    if cmd == "play":
        await handle_play(bot, user, args)
    elif cmd in {"pick", "djpick"}:
        await handle_pick(bot, user, args)
    elif cmd == "radiohelp":
        await handle_radiohelp(bot, user, args)
    elif cmd in {"q", "queue"}:
        await handle_queue(bot, user, args)
    elif cmd in {"now", "np"}:
        await handle_now(bot, user, args)
    elif cmd in {"radiotest", "musicstatus"}:
        await handle_musicstatus(bot, user, args)
