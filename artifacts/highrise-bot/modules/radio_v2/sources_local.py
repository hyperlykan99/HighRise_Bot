"""Radio V2 local favorite source acquisition."""
from __future__ import annotations

import asyncio
import os
import shutil

import database as db
from modules.radio_v2 import azura as v2_azura
from modules.radio_v2 import cleanup
from modules.radio_v2 import diagnostics as diag
from modules.radio_v2 import payments
from modules.radio_v2 import queue


def favorite_rows(user_id: str, limit: int = 20) -> list[dict]:
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT id, title, youtube_url, COALESCE(artist,''), "
                "COALESCE(source_type,''), COALESCE(azura_file_id,''), COALESCE(azura_song_id,'') "
                "FROM dj_favorites WHERE user_id=? ORDER BY favorited_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [
            {
                "id": r[0],
                "title": r[1],
                "url": r[2],
                "artist": r[3],
                "source_type": r[4],
                "azura_file_id": r[5],
                "azura_song_id": r[6],
            }
            for r in rows
        ]
    except Exception:
        return []


def _local_map_for_favorite(fav: dict) -> dict:
    fid = str(fav.get("azura_file_id") or "")
    if not fid:
        return {}
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                "SELECT azura_file_id, unique_id, title, artist, path, filename "
                "FROM local_media_map WHERE azura_file_id=? LIMIT 1",
                (fid,),
            ).fetchone()
        if not row:
            return {}
        return {
            "azura_file_id": row[0],
            "unique_id": row[1],
            "title": row[2],
            "artist": row[3],
            "path": row[4],
            "filename": row[5],
        }
    except Exception:
        return {}


def resolve_local_source(fav: dict) -> str:
    mapped = _local_map_for_favorite(fav)
    rel = (mapped.get("path") or mapped.get("filename") or "").strip()
    roots = [
        os.getenv("AZURA_MEDIA_SFTP_PATH", "").strip(),
        "/var/lib/docker/volumes/azuracast_station_data/data/chilltopia/media",
        "/var/lib/docker/volumes/azuracast_station_data/_data/chilltopia/media",
    ]
    candidates = []
    if rel and os.path.isabs(rel):
        candidates.append(rel)
    for root in roots:
        if root and rel:
            candidates.append(os.path.join(root, rel))
        if root and mapped.get("filename"):
            candidates.append(os.path.join(root, mapped["filename"]))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return ""


async def prepare_and_submit(bot, request_id: int, fav: dict) -> None:
    row = queue.get_request(request_id)
    if not row:
        return
    staged = ""
    try:
        queue.mark_status(request_id, "preparing")
        source = resolve_local_source(fav)
        if not source:
            raise FileNotFoundError("local_source_missing")
        from modules import yt_request as v1_yt

        safe_name = f"local_request_v2_{request_id}_{os.path.basename(source)}"
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in safe_name)
        if not safe_name.endswith(".mp3"):
            safe_name += ".mp3"
        os.makedirs(v1_yt.STAGING_DIR, exist_ok=True)
        staged = os.path.join(v1_yt.STAGING_DIR, safe_name)
        shutil.copyfile(source, staged)
        title = (fav.get("title") or row.get("title") or os.path.basename(source))[:160]
        artist = (fav.get("artist") or row.get("artist") or "")[:100]
        queue.update_request(request_id, title=title, artist=artist, temp_filename=safe_name)
        diag.log("file_source_ready", request_id=request_id, source_type="local_favorite", temp_filename=safe_name)

        loop = asyncio.get_running_loop()
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
        diag.log("request_failed", request_id=request_id, source_type="local_favorite", error=repr(exc))
        row = queue.get_request(request_id)
        if row and row.get("status") not in ("cancelled", "played", "cleaned"):
            if payments.refund_if_needed(row):
                queue.update_request(request_id, song_play_refunded=1)
            queue.mark_status(request_id, "failed", error=str(exc)[:160])
            try:
                cleanup.cleanup_request_media(queue.get_request(request_id), reason="source_local_failed")
            except Exception:
                pass
            try:
                await bot.highrise.send_whisper(row.get("user_id"), "❌ Could not prepare that local favorite. Your Song Play was refunded.")
            except Exception:
                pass
    finally:
        try:
            if staged and os.path.isfile(staged):
                os.unlink(staged)
        except Exception:
            pass

