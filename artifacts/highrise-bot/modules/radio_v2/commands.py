"""Radio V2 command handlers."""
from __future__ import annotations

import asyncio
import re
import time

from modules.msg_utils import safe_send as _safe_send
from modules.radio_v2 import diagnostics as diag
from modules.radio_v2 import payments
from modules.radio_v2 import playback
from modules.radio_v2 import queue
from modules.radio_v2 import renderer
from modules.radio_v2 import settings
from modules.radio_v2 import sources_local
from modules.radio_v2 import sources_youtube

_pending_search: dict[str, list[dict]] = {}
_cooldowns: dict[str, float] = {}
_YT_RE = re.compile(
    r"^https?://(?:www\.)?(?:youtube\.com/watch\?(?:.*&)?v=[\w\-]{11}|youtu\.be/[\w\-]{11}|youtube\.com/shorts/[\w\-]{11})"
)


async def _w(bot, user_id: str, msg: str) -> None:
    try:
        await _safe_send(bot, msg, whisper_target=user_id, max_chars=249)
    except Exception:
        pass


def _is_url(text: str) -> bool:
    return text.lower().startswith(("http://", "https://"))


def _is_youtube_url(text: str) -> bool:
    return bool(_YT_RE.match(text or ""))


async def _send_pages(bot, user_id: str, pages: list[str]) -> None:
    for idx, page in enumerate(pages):
        await _w(bot, user_id, page)
        if idx < len(pages) - 1:
            await asyncio.sleep(0.1)


def _search_pages(results: list[dict]) -> list[str]:
    lines = ["🎵 Search results"]
    for idx, row in enumerate(results[:5], 1):
        title = (row.get("title") or "Unknown")[:42]
        dur = row.get("duration") or ""
        lines.append(f"{idx}. {title} {dur}")
    lines.append("Reply !pick <#>")
    return ["\n".join(lines)[:249]]


async def _accept_request(
    bot,
    user,
    *,
    title: str,
    artist: str = "",
    source_type: str,
    source_ref: str,
    priority: int = 0,
    favorite: dict | None = None,
) -> int:
    if not settings.requests_enabled():
        await _w(bot, user.id, "📻 Song requests are currently disabled.")
        return 0
    if sources_youtube.is_playlist_url(source_ref):
        await _w(bot, user.id, "⚠️ Please use one YouTube song URL, not a playlist or mix.")
        return 0

    staff = payments.is_staff(user.username)
    if not staff:
        elapsed = time.time() - float(_cooldowns.get(user.id, 0))
        cd = settings.cooldown_secs()
        if elapsed < cd:
            await _w(bot, user.id, f"⏳ Cooldown: {int(cd - elapsed)}s remaining. Please wait.")
            return 0
        if queue.active_count() >= settings.max_queue_size():
            await _w(bot, user.id, "📋 Queue is full. Please wait.")
            return 0
        per_user = settings.max_per_user()
        if per_user > 0 and queue.user_active_count(user.id) >= per_user:
            await _w(bot, user.id, f"📋 You already have {per_user} song(s) queued.")
            return 0

    ok, payment_type, plays_left = payments.charge_request(user.id, user.username, priority=priority)
    if not ok:
        await _w(bot, user.id, "❌ Out of 💿 Song Plays! Use !musicshop to buy more.")
        return 0

    request_id = queue.create_request(
        user_id=user.id,
        username=user.username,
        title=title or "Preparing…",
        artist=artist,
        source_type=source_type,
        source_ref=source_ref,
        payment_type=payment_type,
        priority=priority,
    )
    if not request_id:
        if payment_type == "music_credit":
            payments.refund_if_needed({"user_id": user.id, "username": user.username, "payment_type": payment_type})
        await _w(bot, user.id, "❌ Could not queue that request. Try again.")
        return 0

    _cooldowns[user.id] = time.time()
    await _w(
        bot,
        user.id,
        renderer.queue_confirmation(
            title=title,
            artist=artist,
            position=queue.future_count(),
            priority=priority,
            staff_free=staff,
            plays_left=plays_left,
        ),
    )
    diag.log("queue_confirmation_sent_once", request_id=request_id, title=title)
    playback.start(bot)

    if source_type == "youtube":
        asyncio.create_task(sources_youtube.prepare_and_submit(bot, request_id, source_ref), name=f"radio_v2_yt_{request_id}")
    else:
        asyncio.create_task(sources_local.prepare_and_submit(bot, request_id, favorite or {}), name=f"radio_v2_local_{request_id}")
    return request_id


async def handle_request(bot, user, args: list[str]) -> None:
    playback.start(bot)
    if len(args) < 2:
        await _w(bot, user.id, "🎵 Usage: !play <song or YouTube URL>")
        return
    query = " ".join(args[1:]).strip()
    if _is_url(query):
        if not _is_youtube_url(query):
            await _w(bot, user.id, "🎵 Please use a single YouTube song URL.")
            return
        await _accept_request(bot, user, title="Preparing…", source_type="youtube", source_ref=query)
        return

    from modules import request_queue as v1_rq

    loop = asyncio.get_running_loop()
    try:
        results = await loop.run_in_executor(None, v1_rq.search_yt, query, 5)
    except Exception as exc:
        await _w(bot, user.id, f"❌ Search error: {str(exc)[:60]}")
        return
    if not results:
        await _w(bot, user.id, "❌ No results found. Try another search.")
        return
    _pending_search[user.id] = results[:5]
    await _send_pages(bot, user.id, _search_pages(results))


async def handle_pick(bot, user, args: list[str]) -> None:
    playback.start(bot)
    results = _pending_search.get(user.id) or []
    if not results:
        await _w(bot, user.id, "🎵 No pending search. Use !play <song> first.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, f"🎵 Reply !pick <1-{len(results)}>.")
        return
    idx = int(args[1]) - 1
    if idx < 0 or idx >= len(results):
        await _w(bot, user.id, f"⚠️ Pick 1-{len(results)}.")
        return
    picked = results[idx]
    _pending_search.pop(user.id, None)
    url = picked.get("url") or ""
    if not url:
        await _w(bot, user.id, "❌ Could not get that song URL.")
        return
    await _accept_request(
        bot,
        user,
        title=picked.get("title") or "Preparing…",
        artist=picked.get("artist") or picked.get("uploader") or "",
        source_type="youtube",
        source_ref=url,
    )


async def handle_queue(bot, user, _args: list[str]) -> None:
    playback.start(bot)
    await _send_pages(bot, user.id, renderer.queue_pages(queue.display_jobs()))


async def handle_nowplaying(bot, user, _args: list[str]) -> None:
    playback.start(bot)
    import modules.azuracast_controller as azura

    loop = asyncio.get_running_loop()
    np = await loop.run_in_executor(None, azura.fetch_nowplaying)
    if not np:
        await _w(bot, user.id, "📻 ChillTopia Radio is live, but I can't read the current track right now.")
        return
    song, media, elapsed, duration = playback._extract(np)
    row = playback.match_nowplaying(np)
    if row:
        msg = renderer.render_now_playing(
            {
                "title": row.get("title") or song.get("title") or "Unknown",
                "artist": row.get("artist") or song.get("artist") or "",
                "requester": row.get("username") or "",
                "source": "request",
                "elapsed": elapsed,
                "duration": duration,
            }
        )
    else:
        msg = renderer.render_now_playing(
            {
                "title": song.get("title") or "Unknown",
                "artist": song.get("artist") or "",
                "source": "autodj",
                "elapsed": elapsed,
                "duration": duration,
            }
        )
    await _w(bot, user.id, msg)


async def handle_cancel(bot, user, args: list[str]) -> None:
    playback.start(bot)
    jobs = queue.cancelable_for_user(user.id)
    if not jobs:
        current = playback.current_request()
        if current and (current.get("user_id") or "") == user.id:
            await _w(bot, user.id, "🎵 Your request is already playing. Use !skip to skip it.")
        else:
            await _w(bot, user.id, "📋 You have no pending requests to cancel.")
        return
    if len(jobs) > 1 and (len(args) < 2 or not args[1].isdigit()):
        lines = ["📋 Your requests (use !cancel <#>):"]
        for idx, row in enumerate(jobs, 1):
            lines.append(f"{idx}. {(row.get('title') or 'Preparing…')[:34]}")
        await _w(bot, user.id, "\n".join(lines)[:249])
        return
    idx = int(args[1]) - 1 if len(args) > 1 and args[1].isdigit() else 0
    if idx < 0 or idx >= len(jobs):
        await _w(bot, user.id, f"⚠️ You have {len(jobs)} request(s). Use !cancel 1–{len(jobs)}.")
        return
    row = jobs[idx]
    queue.mark_status(int(row["id"]), "cancelled", error="cancelled_by_user")
    refunded = payments.refund_if_needed(row)
    if refunded:
        queue.update_request(int(row["id"]), song_play_refunded=1)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, __import__("modules.radio_v2.cleanup", fromlist=["cleanup_request_media"]).cleanup_request_media, queue.get_request(int(row["id"])), "cancelled_by_user")
    note = "\n🎟 1 music request refunded." if refunded else ""
    await _w(bot, user.id, f"✅ Cancelled: {(row.get('title') or 'request')[:40]}{note}")


async def handle_remove(bot, user, args: list[str]) -> None:
    if not payments.is_staff(user.username):
        await _w(bot, user.id, "🔒 Staff only. Use !cancel to remove your own queued songs.")
        return
    jobs = queue.display_jobs()
    if len(args) < 2 or not args[1].isdigit():
        if not jobs:
            await _w(bot, user.id, "📋 No pending requests to remove.")
            return
        lines = ["📋 Pending (use !remove <#>):"]
        for idx, row in enumerate(jobs, 1):
            lines.append(f"{idx}. @{(row.get('username') or '?')[:12]} — {(row.get('title') or 'Preparing…')[:28]}")
        await _w(bot, user.id, "\n".join(lines)[:249])
        return
    idx = int(args[1]) - 1
    if idx < 0 or idx >= len(jobs):
        await _w(bot, user.id, f"⚠️ No request #{args[1]}.")
        return
    row = jobs[idx]
    rid = int(row.get("id") or 0)
    queue.mark_status(rid, "cancelled", error="removed_by_staff")
    if payments.refund_if_needed(row):
        queue.update_request(rid, song_play_refunded=1)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, __import__("modules.radio_v2.cleanup", fromlist=["cleanup_request_media"]).cleanup_request_media, queue.get_request(rid), "removed_by_staff")
    await _w(bot, user.id, f"✅ Removed: {(row.get('title') or 'request')[:40]}")


async def handle_skip(bot, user, _args: list[str]) -> None:
    playback.start(bot)
    ok, msg = await playback.skip_current_request(bot, user.id, staff=payments.is_staff(user.username))
    await _w(bot, user.id, msg)


async def handle_voteskip(bot, user, _args: list[str]) -> None:
    playback.start(bot)
    _ok, msg = await playback.vote_skip(bot, user.id)
    await _w(bot, user.id, msg)


async def handle_myrequests(bot, user, _args: list[str]) -> None:
    rows = queue.recent_for_user(user.id, 6)
    await _send_pages(bot, user.id, renderer.request_history(rows))


async def handle_requesthistory(bot, user, _args: list[str]) -> None:
    if not payments.is_staff(user.username):
        await _w(bot, user.id, "🔒 Staff only.")
        return
    rows = queue.recent_room(10)
    await _send_pages(bot, user.id, renderer.request_history(rows, title="📜 Radio History"))


async def handle_radiolog(bot, user, args: list[str]) -> None:
    await handle_requesthistory(bot, user, args)


async def handle_playfav(bot, user, args: list[str]) -> None:
    playback.start(bot)
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !playfav <#>  (see !favs for your list)")
        return
    rows = sources_local.favorite_rows(user.id, 20)
    pos = int(args[1])
    if pos < 1 or pos > len(rows):
        await _w(bot, user.id, f"❌ Favorite #{pos} not found. You have {len(rows)}.")
        return
    fav = rows[pos - 1]
    if fav.get("url"):
        await _accept_request(
            bot,
            user,
            title=fav.get("title") or "Preparing…",
            artist=fav.get("artist") or "",
            source_type="youtube",
            source_ref=fav.get("url") or "",
        )
    elif fav.get("azura_file_id"):
        await _accept_request(
            bot,
            user,
            title=fav.get("title") or "Local favorite",
            artist=fav.get("artist") or "",
            source_type="local_favorite",
            source_ref=str(fav.get("id") or ""),
            favorite=fav,
        )
    else:
        await _w(bot, user.id, "⚠️ That favorite has no playable source saved.")


async def handle_playfavlocal(bot, user, args: list[str]) -> None:
    await handle_playfav(bot, user, args)


async def handle_requester_left(bot, user_id: str, username: str) -> None:
    if not settings.skip_if_requester_leaves():
        return
    for row in queue.active_for_user(user_id):
        if payments.is_staff(row.get("username") or username) and settings.admin_requests_ignore_leave():
            continue
        rid = int(row.get("id") or 0)
        if not rid:
            continue
        if row.get("status") == "playing":
            import modules.azuracast_controller as azura

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, azura.skip_current)
            if settings.refund_if_leaves() and payments.refund_if_needed(row):
                queue.update_request(rid, song_play_refunded=1)
            asyncio.create_task(playback.skip_current_request(bot, user_id, staff=True))
            diag.log("requester_left_skip_current", request_id=rid, user_id=user_id)
        else:
            queue.mark_status(rid, "cancelled", error="requester_left")
            if settings.refund_if_leaves() and payments.refund_if_needed(row):
                queue.update_request(rid, song_play_refunded=1)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, __import__("modules.radio_v2.cleanup", fromlist=["cleanup_request_media"]).cleanup_request_media, queue.get_request(rid), "requester_left")
            diag.log("requester_left_cancel", request_id=rid, user_id=user_id)


# Stable legacy-compatible delegates for commands that are not specific to V2
# request lifecycle.
async def handle_musicshop(bot, user, args: list[str]) -> None:
    from modules import radio_commands as v1
    await v1.handle_musicshop(bot, user, args)


async def handle_buyplays(bot, user, args: list[str]) -> None:
    from modules import radio_commands as v1
    await v1.handle_buyplays(bot, user, args)


async def handle_favorites(bot, user, args: list[str]) -> None:
    from modules import radio_commands as v1
    await v1.handle_favorites(bot, user, args)


async def handle_removefav(bot, user, args: list[str]) -> None:
    from modules import radio_commands as v1
    await v1.handle_removefav(bot, user, args)


async def handle_favorite(bot, user, args: list[str]) -> None:
    from modules import radio_commands as v1
    await v1.handle_favorite(bot, user, args)


async def handle_radiohelp(bot, user, _args: list[str]) -> None:
    await _w(bot, user.id, "📻 Radio V2\n!play <song> • !pick # • !q • !now\n!cancel • !skip • !voteskip • !myrequests")


async def handle_radiostatus(bot, user, _args: list[str]) -> None:
    await _w(bot, user.id, f"📻 Radio V2 active\nMode: {settings.playback_mode()}\nQueue: {queue.active_count()}")
