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
                "!radiohelp — this help",
                "Requests are being rebuilt.",
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
    lines = [
        "🎛 Music Status",
        f"Radio: {'enabled' if snap['radio_enabled'] else 'disabled'}",
        f"Azura API: {'ok' if snap['api'].get('ok') else 'fail'}",
        f"SFTP: {'ok' if snap['sftp'].get('ok') else 'fail'}",
        f"NowPlaying: {'ok' if snap['nowplaying_ok'] else 'fail'}",
        f"Queue: {snap['queue_size']}",
        f"Last error: {snap['last_error'] or 'none'}",
    ]
    await _w(bot, user.id, "\n".join(lines))


async def dispatch_radio_command(bot, user, cmd: str, args: list[str]) -> None:
    if cmd == "radiohelp":
        await handle_radiohelp(bot, user, args)
    elif cmd in {"q", "queue"}:
        await handle_queue(bot, user, args)
    elif cmd in {"now", "np"}:
        await handle_now(bot, user, args)
    elif cmd in {"radiotest", "musicstatus"}:
        await handle_musicstatus(bot, user, args)
