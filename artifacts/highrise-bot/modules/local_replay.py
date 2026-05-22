"""
modules/local_replay.py
-----------------------
Local/AzuraCast library song replay via SFTP copy → Requests queue.

Flow:
  1. Find original media record in AzuraCast
       priority: azura_file_id → azura_song_id match → title+artist search
  2. Derive absolute SFTP source path from the media root
       = AZURA_MEDIA_ROOT env var  OR  parent-dir of AZURA_SFTP_PATH
  3. SFTP-copy original → Requests/<lr_<hex8>_<basename>>
       source file is NEVER moved or deleted
  4. Rescan Requests folder so AzuraCast registers the copy
  5. Poll up to 20 s for AzuraCast to assign a new file_id to the copy
  6. Add copy to Requests playlist
  7. Insert yt_request_jobs row (status='ready', source_type='local_copy')
       playback_engine picks it up → plays as "NOW PLAYING" → cleans up copy

Cleanup safety: only the copy's azura_file_id is stored in the job row,
so _delete_request_file in playback_engine.py deletes the copy only —
the original AutoDJ library file is always untouched.

Logging prefix: [LOCAL_REPLAY]
"""
from __future__ import annotations
import os
import time
import uuid

import database as db
from modules.config_store import sftp_cfg, requests_playlist_id
import modules.azuracast_controller as azura

_LLOG          = "[LOCAL_REPLAY]"
_POLL_TIMEOUT  = 20.0   # seconds to wait for AzuraCast to register the copy
_POLL_INTERVAL = 2.0


# ─── Blocking implementation ──────────────────────────────────────────────────

def queue_local_copy_sync(
    user_id:       str,
    username:      str,
    title:         str,
    artist:        str = "",
    azura_file_id: str = "",
    azura_song_id: str = "",
) -> tuple[bool, str]:
    """
    Copy a local AzuraCast library file into the Requests pipeline.

    Blocking — must be called via loop.run_in_executor() from async code.

    Returns (ok, error_code):
      (True,  "")               — success; yt_request_jobs row inserted
      (False, "not_found")      — no media found matching the identifiers
      (False, "multi")          — ambiguous search results, no exact match
      (False, "sftp_fail")      — SFTP copy failed
      (False, "register_fail")  — AzuraCast did not register the copy in time
    """
    title_s  = (title  or "?")[:40]
    artist_s = (artist or "")[:20]

    print(f"{_LLOG} favorite replay requested title={title_s!r} artist={artist_s!r}")

    # ── 1. Locate the original file record ────────────────────────────────────
    rec: "dict | None" = None

    print(f"{_LLOG} checking metadata file_id={azura_file_id!r} song_id={azura_song_id!r}")

    if azura_file_id:
        rec = azura.get_media_file(azura_file_id)
        if rec:
            print(f"{_LLOG} found original media via file_id={azura_file_id!r}")
        else:
            print(f"{_LLOG} file_id lookup returned nothing id={azura_file_id!r}")

    if not rec and azura_song_id and title:
        print(f"{_LLOG} searching AzuraCast by song_id title={title_s!r}")
        candidates = azura.search_media_by_phrase(title)
        for r in candidates:
            uid = ((r.get("song") or {}).get("unique_id") or "").strip()
            if uid and uid == azura_song_id:
                rec = r
                azura_file_id = str(rec.get("id") or "")
                print(f"{_LLOG} found original media via song_id={azura_song_id!r}")
                break
        if not rec:
            print(f"{_LLOG} song_id scan found {len(candidates)} candidate(s), no uid match")

    if not rec:
        phrase  = f"{title} {artist}".strip() if artist else title.strip()
        print(f"{_LLOG} searching AzuraCast phrase={phrase!r}")
        results = azura.search_media_by_phrase(phrase) if phrase else []
        if not results and artist and title:
            print(f"{_LLOG} searching AzuraCast title-only={title.strip()!r}")
            results = azura.search_media_by_phrase(title.strip())

        print(f"{_LLOG} search returned {len(results)} result(s)")

        if len(results) == 1:
            rec = results[0]
            azura_file_id = str(rec.get("id") or "")
            print(f"{_LLOG} found original media via phrase search={phrase!r}")
        elif len(results) > 1:
            title_lc = title.lower()
            for r in results:
                rt = (
                    ((r.get("song") or {}).get("title") or "")
                    or os.path.splitext(os.path.basename(r.get("path", "")))[0]
                ).lower()
                if title_lc == rt or (title_lc and title_lc in rt):
                    rec = r
                    azura_file_id = str(rec.get("id") or "")
                    print(f"{_LLOG} found original media via exact title match={title_lc!r}")
                    break
            if not rec:
                print(f"{_LLOG} multiple matches for {phrase!r} — no exact match")
                return False, "multi"
        else:
            print(f"{_LLOG} unsupported after all fallbacks failed title={title_s!r}")
            return False, "not_found"

    # ── 2. Extract SFTP source path ───────────────────────────────────────────
    file_path = (rec.get("path") or "").strip()
    if not file_path:
        print(f"{_LLOG} media record has no path for title={title_s!r}")
        return False, "not_found"

    print(f"{_LLOG} match found id={azura_file_id!r} path={file_path!r}")

    cfg        = sftp_cfg()
    req_folder = cfg.get("folder", "").rstrip("/")
    # Media root: explicit env var override OR parent of the Requests folder.
    # Standard AzuraCast layout: /var/azuracast/stations/<id>/media/Requests
    #   → media root = /var/azuracast/stations/<id>/media
    media_root = (
        (os.environ.get("AZURA_MEDIA_ROOT") or "").strip()
        or os.path.dirname(req_folder)
    ).rstrip("/")
    src_abs_path = f"{media_root}/{file_path}"

    # ── 3. Generate a unique Requests-folder filename ─────────────────────────
    orig_base     = os.path.basename(file_path)
    uid_prefix    = uuid.uuid4().hex[:8]
    dest_filename = f"lr_{uid_prefix}_{orig_base}"

    print(f"{_LLOG} copying {file_path!r} → Requests/{dest_filename}")

    # ── 4. SFTP copy (source NEVER modified) ─────────────────────────────────
    if not azura.sftp_copy_to_requests(src_abs_path, dest_filename):
        return False, "sftp_fail"
    print(f"{_LLOG} copied to request folder")

    # ── 5. Rescan so AzuraCast picks up the new copy ──────────────────────────
    azura.rescan_requests_folder()

    # ── 6. Poll for AzuraCast registration ───────────────────────────────────
    new_file_id  = ""
    new_song_uid = ""
    deadline = time.time() + _POLL_TIMEOUT
    while time.time() < deadline:
        row = azura.search_media(dest_filename)
        if row:
            new_file_id  = str(row.get("id") or "")
            new_song_uid = ((row.get("song") or {}).get("unique_id") or "").strip()
            break
        time.sleep(_POLL_INTERVAL)

    if not new_file_id:
        print(f"{_LLOG} register_fail: AzuraCast did not register {dest_filename!r}")
        azura.sftp_delete_file(dest_filename)
        return False, "register_fail"

    print(f"{_LLOG} queued local copy file_id={new_file_id}")

    # ── 7. Add copy to Requests playlist ─────────────────────────────────────
    req_pid = requests_playlist_id()
    if req_pid:
        azura.add_file_to_playlist(new_file_id, req_pid)

    # ── 8. Insert yt_request_jobs row (status='ready') ────────────────────────
    # Cleanup in playback_engine uses this row's azura_file_id (the copy's ID),
    # so only the copy is ever deleted — original library file is untouched.
    print(f"{_LLOG} queueing copied request dest={dest_filename!r} file_id={new_file_id!r}")
    try:
        with db.db_conn() as conn:
            cur = conn.execute(
                "INSERT INTO yt_request_jobs "
                "  (user_id, username, url, title, artist, status, filename, "
                "   azura_file_id, azura_song_id, source_type, started_at) "
                "VALUES (?, ?, '', ?, ?, 'ready', ?, ?, ?, 'local_copy', datetime('now'))",
                (
                    user_id,
                    username.lower(),
                    title[:120],
                    artist[:80],
                    dest_filename,
                    new_file_id,
                    new_song_uid,
                ),
            )
            job_db_id = cur.lastrowid or 0
    except Exception as exc:
        print(f"{_LLOG} db insert error: {exc}")
        # Clean up the orphaned copy so it does not sit in /Requests forever
        try:
            azura.sftp_delete_file(dest_filename)
        except Exception:
            pass
        return False, "register_fail"

    print(f"{_LLOG} job created db_id={job_db_id} filename={dest_filename!r}")
    return True, ""


# ─── Async wrapper ────────────────────────────────────────────────────────────

async def queue_local_copy(
    user_id:       str,
    username:      str,
    title:         str,
    artist:        str = "",
    azura_file_id: str = "",
    azura_song_id: str = "",
) -> tuple[bool, str]:
    """
    Async wrapper around queue_local_copy_sync.
    Safe to await from any async handler in radio_commands.py.
    Returns (ok, error_code).
    """
    import asyncio
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: queue_local_copy_sync(
            user_id=user_id,
            username=username,
            title=title,
            artist=artist,
            azura_file_id=azura_file_id,
            azura_song_id=azura_song_id,
        ),
    )
