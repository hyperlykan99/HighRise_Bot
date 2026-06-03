"""Radio V2 YouTube source acquisition."""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile

from modules.radio_v2 import azura as v2_azura
from modules.radio_v2 import cleanup
from modules.radio_v2 import diagnostics as diag
from modules.radio_v2 import payments
from modules.radio_v2 import queue


def is_playlist_url(url: str) -> bool:
    u = (url or "").lower()
    return "youtube.com/playlist" in u or "list=" in u or "/mix" in u or "start_radio=1" in u


async def prepare_and_submit(bot, request_id: int, url: str) -> None:
    row = queue.get_request(request_id)
    if not row:
        return
    tmpdir = tempfile.mkdtemp(prefix="radio_v2_yt_")
    staged = ""
    try:
        queue.mark_status(request_id, "preparing")
        from modules import yt_request as v1_yt

        loop = asyncio.get_running_loop()
        info, mp3_path = await loop.run_in_executor(None, v1_yt._download_step, url, tmpdir)
        title = (info.get("title") or row.get("title") or "Unknown")[:160]
        artist = (info.get("artist") or info.get("creator") or info.get("uploader") or row.get("artist") or "")[:100]
        video_id = (info.get("id") or "")[:32]
        safe_name = f"request_v2_{request_id}_{video_id or os.path.basename(mp3_path)[:16]}.mp3"
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in safe_name)
        staged = os.path.join(v1_yt.STAGING_DIR, safe_name)
        os.makedirs(v1_yt.STAGING_DIR, exist_ok=True)
        shutil.move(mp3_path, staged)
        queue.update_request(request_id, title=title, artist=artist, temp_filename=safe_name)
        diag.log("file_source_ready", request_id=request_id, source_type="youtube", temp_filename=safe_name)

        await loop.run_in_executor(None, v1_yt._sftp_step, staged, lambda: None)
        result = await loop.run_in_executor(None, v2_azura.finalize_uploaded_request, request_id, safe_name, title)
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "native_request_submit_failed")
        queue.mark_status(
            request_id,
            "submitted",
            azura_file_id=result.get("media_id", ""),
            azura_song_id=result.get("song_id", ""),
            azura_request_id=result.get("requestable_id", ""),
            temp_filename=safe_name,
        )
    except Exception as exc:
        diag.log("request_failed", request_id=request_id, source_type="youtube", error=repr(exc))
        row = queue.get_request(request_id)
        if row and row.get("status") not in ("cancelled", "played", "cleaned"):
            if payments.refund_if_needed(row):
                queue.update_request(request_id, song_play_refunded=1)
            queue.mark_status(request_id, "failed", error=str(exc)[:160])
            try:
                cleanup.cleanup_request_media(queue.get_request(request_id), reason="source_youtube_failed")
            except Exception:
                pass
            try:
                await bot.highrise.send_whisper(row.get("user_id"), "❌ Couldn't prepare that song. Try another version.")
            except Exception:
                pass
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
        try:
            if staged and os.path.isfile(staged):
                os.unlink(staged)
        except Exception:
            pass

