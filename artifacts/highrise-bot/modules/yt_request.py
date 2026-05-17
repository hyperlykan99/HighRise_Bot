"""
modules/yt_request.py
---------------------
!ytrequest <youtube_url>
    Download YouTube audio, convert to mp3 via ffmpeg, and upload via SFTP
    to the AzuraCast Requests folder so AzuraCast adds it to the playlist.

    No audio is streamed or re-broadcast by the bot itself.
    AzuraCast's own playback engine serves the file to listeners.

!ytqueue   (admin+) — show active/pending jobs and recent results
!ytstatus  (admin+) — show SFTP config readiness and session stats

BOT_MODE = dj only.  DJ_DUDU is the sole owner of these commands.

Per-user cooldown: configurable via room_setting yt_request_cooldown
                   (default 300 s).  Set with !djset ytcooldown <secs>.
                   Minimum enforced: 30 s.

Required env vars:
    AZURA_SFTP_HOST            hostname or IP of AzuraCast SFTP
    AZURA_SFTP_USER            SFTP username
    AZURA_SFTP_PASS            SFTP password

Optional env vars:
    AZURA_SFTP_PORT            SFTP port (default: 22)
    AZURA_REQUESTS_FOLDER      remote subfolder (default: Requests)

Pipeline per request:
    1. Validate YouTube URL
    2. Check per-user cooldown
    3. yt-dlp download + ffmpeg mp3 conversion  (temp dir, deleted after)
    4. paramiko SFTP put → AZURA_REQUESTS_FOLDER/<id>.mp3
    5. Status whispers at each step; error whisper on failure
    6. Temp files cleaned up whether or not the upload succeeds
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import threading
import time
from typing import TYPE_CHECKING, Callable

import database as db
from modules.permissions import is_admin, is_owner

if TYPE_CHECKING:
    from highrise import BaseBot, User

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# URL validation
# ─────────────────────────────────────────────────────────────────────────────

_YT_RE = re.compile(
    r"^https?://(?:www\.)?"
    r"(?:"
    r"youtube\.com/watch\?(?:.*&)?v=[\w\-]{11}"
    r"|youtu\.be/[\w\-]{11}"
    r"|youtube\.com/shorts/[\w\-]{11}"
    r")"
)

def _is_youtube_url(url: str) -> bool:
    return bool(_YT_RE.match(url))

# ─────────────────────────────────────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_COOLDOWN = 300   # seconds
_MAX_ACTIVE_JOBS  = 5     # refuse new jobs when full

def _cooldown_secs() -> int:
    try:
        return max(30, int(db.get_room_setting("yt_request_cooldown", str(_DEFAULT_COOLDOWN))))
    except Exception:
        return _DEFAULT_COOLDOWN

_REQUIRED_SFTP_VARS = ("AZURA_SFTP_HOST", "AZURA_SFTP_USER", "AZURA_SFTP_PASS")
_DEFAULT_SFTP_PATH  = "/stations/chilltopia/media"

def _sftp_cfg() -> dict:
    # AZURA_SFTP_PATH — absolute remote directory on the AzuraCast server.
    # If unset, defaults to _DEFAULT_SFTP_PATH.
    # Note: AZURA_REQUESTS_FOLDER is intentionally NOT used here — it was the
    # old combined env var; use AZURA_SFTP_PATH for the upload path and
    # AZURA_MEDIA_DIR for the API rescan directory.
    sftp_path = (os.environ.get("AZURA_SFTP_PATH") or _DEFAULT_SFTP_PATH).strip()
    return {
        "host":   (os.environ.get("AZURA_SFTP_HOST") or "").strip(),
        "port":   int((os.environ.get("AZURA_SFTP_PORT") or "22").strip() or "22"),
        "user":   (os.environ.get("AZURA_SFTP_USER") or "").strip(),
        "passwd": (os.environ.get("AZURA_SFTP_PASS") or "").strip(),
        "folder": sftp_path,
    }

def _sftp_missing_vars() -> list[str]:
    missing = []
    for var in _REQUIRED_SFTP_VARS:
        raw = os.environ.get(var)
        if not raw or not raw.strip():
            missing.append(var)
    return missing

def _sftp_ready() -> bool:
    return len(_sftp_missing_vars()) == 0

def _log_sftp_env() -> None:
    """Log SFTP config at startup. Host printed as-is; credentials show length only."""
    host = (os.environ.get("AZURA_SFTP_HOST") or "").strip()
    port = (os.environ.get("AZURA_SFTP_PORT") or "22").strip()
    if host:
        print(f"[YT_REQUEST] AZURA_SFTP_HOST = {host}:{port}")
    else:
        print("[YT_REQUEST] AZURA_SFTP_HOST: NOT SET  ← required, YT requests disabled")
    for var in ("AZURA_SFTP_USER", "AZURA_SFTP_PASS"):
        raw = os.environ.get(var)
        if raw and raw.strip():
            print(f"[YT_REQUEST] {var}: SET (len={len(raw.strip())})")
        else:
            status = "EMPTY" if raw is not None else "NOT SET"
            print(f"[YT_REQUEST] {var}: {status}  ← required, YT requests disabled")
    sftp_path = (os.environ.get("AZURA_SFTP_PATH") or _DEFAULT_SFTP_PATH).strip()
    print(f"[YT_REQUEST] SFTP upload path = {sftp_path}")
    # API post-upload step (optional)
    base_url  = (os.environ.get("AZURA_BASE_URL") or "").strip()
    api_key   = (os.environ.get("AZURA_API_KEY") or "").strip()
    sid       = (os.environ.get("AZURA_STATION_ID") or "1").strip()
    media_dir = (os.environ.get("AZURA_MEDIA_DIR") or "").strip()
    if base_url and api_key:
        dir_disp = media_dir or "(root — full rescan)"
        print(f"[YT_REQUEST] Post-upload API: ENABLED → {base_url}  station={sid}  media_dir={dir_disp}")
    else:
        print("[YT_REQUEST] Post-upload API: DISABLED (AZURA_BASE_URL / AZURA_API_KEY not set)")

# Log SFTP + API config once at import (visible in workflow console)
_log_sftp_env()

# ─────────────────────────────────────────────────────────────────────────────
# In-memory job tracker
# ─────────────────────────────────────────────────────────────────────────────

_jobs_lock = threading.Lock()
_jobs: dict[int, dict] = {}
_next_job_id = 1

def _new_job(user_id: str, username: str, url: str) -> dict:
    global _next_job_id
    with _jobs_lock:
        jid = _next_job_id
        _next_job_id += 1
        job: dict = {
            "id":          jid,
            "user_id":     user_id,
            "username":    username,
            "url":         url,
            "status":      "pending",    # pending | downloading | uploading | done | error
            "title":       "",
            "error":       "",
            "started_at":  time.time(),
            "finished_at": None,
        }
        _jobs[jid] = job
    return job

def _update_job(jid: int, **kwargs: object) -> None:
    with _jobs_lock:
        if jid in _jobs:
            _jobs[jid].update(kwargs)

# ─────────────────────────────────────────────────────────────────────────────
# Per-user cooldown tracker
# ─────────────────────────────────────────────────────────────────────────────

_cooldowns: dict[str, float] = {}   # user_id → last_request_timestamp

# ─────────────────────────────────────────────────────────────────────────────
# Blocking download step  (run in thread executor — must not touch event loop)
# ─────────────────────────────────────────────────────────────────────────────

def _download_step(url: str, tmpdir: str) -> tuple[dict, str]:
    """
    Download best-audio from YouTube and convert to mp3 via ffmpeg.
    Returns (info_dict, local_mp3_path).
    Raises on any failure.
    No audio is streamed — file lands in tmpdir only.
    """
    import yt_dlp  # already installed globally

    ydl_opts: dict = {
        "format":      "bestaudio/best",
        "outtmpl":     os.path.join(tmpdir, "%(id)s.%(ext)s"),
        "noplaylist":  True,
        "quiet":       True,
        "no_warnings": True,
        "postprocessors": [{
            "key":              "FFmpegExtractAudio",
            "preferredcodec":   "mp3",
            "preferredquality": "192",
        }],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    # Unwrap playlist wrapper (shouldn't happen with noplaylist=True, but be safe)
    if isinstance(info, dict) and info.get("entries"):
        info = info["entries"][0]

    # Find the mp3 output file
    for fname in os.listdir(tmpdir):
        if fname.endswith(".mp3"):
            return info, os.path.join(tmpdir, fname)

    raise FileNotFoundError("mp3 file not found after yt-dlp conversion")

# ─────────────────────────────────────────────────────────────────────────────
# Blocking SFTP upload step  (runs inside a daemon thread started by _run_job)
# ─────────────────────────────────────────────────────────────────────────────

_SFTP_CONNECT_TIMEOUT  = 30   # seconds — TCP connect + SSH handshake + auth
_SFTP_TRANSFER_TIMEOUT = 180  # seconds — per-channel socket timeout during put()

def _sftp_step(
    mp3_path: str,
    on_put_done: "Callable[[], None]",
) -> None:
    """
    Blocking SFTP upload.  Runs inside a daemon thread started by _run_job.

    Design contract
    ───────────────
    • on_put_done() is called the INSTANT sftp.put() returns without raising.
      The caller uses this to signal the async layer and whisper success
      immediately — before this function does any cleanup.
    • on_put_done() is NOT called on failure; the exception propagates and the
      thread wrapper signals the error instead.
    • sftp.close() / ssh.close() run after on_put_done() and are fire-and-forget:
      wrapped in try/except, can never block or propagate.

    Log lines (always in order for a successful upload):
      [YT_SFTP] Connecting → host:port …
      [YT_SFTP] SFTP connected
      [YT_SFTP] Upload started → <remote path>
      [YT_SFTP] Upload 25 / 50 / 75 / 100 % …
      [YT_SFTP] Upload finished ✓ — <N> bytes
      [YT_SFTP] SFTP closed
    """
    import paramiko

    cfg             = _sftp_cfg()
    remote_filename = os.path.basename(mp3_path)
    remote_path     = f"{cfg['folder'].rstrip('/')}/{remote_filename}"
    file_size       = os.path.getsize(mp3_path)

    print(
        f"[YT_SFTP] Connecting → {cfg['host']}:{cfg['port']}"
        f" user={cfg['user']}"
        f" file={remote_filename} ({file_size:,} bytes)"
    )

    ssh  = paramiko.SSHClient()
    sftp = None
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        # ── 1. Connect + authenticate ────────────────────────────────────────
        try:
            ssh.connect(
                hostname=cfg["host"],
                port=cfg["port"],
                username=cfg["user"],
                password=cfg["passwd"],
                timeout=_SFTP_CONNECT_TIMEOUT,
                banner_timeout=_SFTP_CONNECT_TIMEOUT,
                auth_timeout=_SFTP_CONNECT_TIMEOUT,
                look_for_keys=False,
                allow_agent=False,
            )
        except paramiko.AuthenticationException as exc:
            print(f"[YT_SFTP] Auth failed — check AZURA_SFTP_USER / AZURA_SFTP_PASS: {exc}")
            raise
        except Exception as exc:
            print(f"[YT_SFTP] Connection failed — check AZURA_SFTP_HOST / AZURA_SFTP_PORT: {exc}")
            raise

        print("[YT_SFTP] SFTP connected")

        # ── 2. Open channel; set transfer timeout ────────────────────────────
        sftp = ssh.open_sftp()
        sftp.get_channel().settimeout(_SFTP_TRANSFER_TIMEOUT)

        # ── 3. Upload with 25 % progress milestones ──────────────────────────
        _milestone = [0]

        def _progress(transferred: int, total: int) -> None:
            if not total:
                return
            pct       = int(transferred * 100 / total)
            next_mark = ((_milestone[0] // 25) + 1) * 25
            if pct >= next_mark <= 100:
                _milestone[0] = next_mark
                print(f"[YT_SFTP] Upload {next_mark}% ({transferred:,} / {total:,} bytes)")

        print(f"[YT_SFTP] Upload started → {remote_path}")
        sftp.put(mp3_path, remote_path, callback=_progress)

        # ── 4. PUT returned — file is on server — signal success immediately ──
        print(f"[YT_SFTP] Upload finished ✓ — {file_size:,} bytes")
        on_put_done()

        # ── 5. Cleanup (fire-and-forget; never raises) ────────────────────────
        try:
            sftp.get_channel().settimeout(5)
            sftp.close()
        except Exception as exc:
            print(f"[YT_SFTP] sftp.close() warning (ignored): {exc}")
        try:
            ssh.close()
            print("[YT_SFTP] SFTP closed")
        except Exception as exc:
            print(f"[YT_SFTP] ssh.close() warning (ignored): {exc}")

    except Exception:
        if sftp is not None:
            try:
                sftp.get_channel().settimeout(5)
                sftp.close()
            except Exception:
                pass
        try:
            ssh.close()
        except Exception:
            pass
        raise

# ─────────────────────────────────────────────────────────────────────────────
# Optional AzuraCast API post-upload step
# ─────────────────────────────────────────────────────────────────────────────

_API_WAIT_SECS   = 10  # seconds to wait after SFTP before first rescan attempt
_API_RETRY_COUNT = 5   # how many times to search for the file if not indexed yet
_API_RETRY_DELAY = 5   # seconds between search retries

def _azura_api_cfg() -> "dict | None":
    """Return API config dict if AZURA_BASE_URL + AZURA_API_KEY are both set, else None."""
    base_url = (os.environ.get("AZURA_BASE_URL") or "").rstrip("/").strip()
    api_key  = (os.environ.get("AZURA_API_KEY") or "").strip()
    if not base_url or not api_key:
        return None
    # AZURA_MEDIA_DIR — currentDirectory sent to the AzuraCast rescan API.
    # This is a path relative to the station's media library root (NOT the
    # SFTP filesystem path).  Leave empty ("") to rescan the entire library.
    media_dir = (os.environ.get("AZURA_MEDIA_DIR") or "").strip()
    return {
        "base_url":   base_url,
        "api_key":    api_key,
        "station_id": (os.environ.get("AZURA_STATION_ID") or "1").strip(),
        "folder":     media_dir,
    }

def _azura_post_upload(filename: str) -> None:
    """
    Blocking post-SFTP API step.  Called from the upload daemon thread AFTER
    sftp.put() has returned and the success whisper has already been sent.

    Steps
    ─────
    1. Wait _API_WAIT_SECS (10 s) for AzuraCast to notice the new file.
    2. POST /api/station/{id}/files/batch  {"do":"rescan"}  — tell AzuraCast
       to index the Requests folder.
    3. GET  /api/station/{id}/files?searchPhrase={filename}  — look for the
       media record.  Retry up to _API_RETRY_COUNT (5) times (_API_RETRY_DELAY s
       apart) if the file isn't indexed yet.  Does NOT fail on first miss.
    4. POST /api/station/{id}/files/batch  {"do":"playlist","playlist":id}
       — add the confirmed file to the Requests playlist (if AZURA_PLAYLIST_ID
       is set).
    5. POST /api/station/{id}/request/{unique_id}
       — submit the song to the AzuraCast request queue.

    All HTTP responses are logged in full.  Every error is non-fatal; this
    function never raises.  Skipped silently when AZURA_BASE_URL / AZURA_API_KEY
    are not set.
    """
    import requests as req_lib

    cfg = _azura_api_cfg()
    if cfg is None:
        print("[YT_API] AZURA_BASE_URL / AZURA_API_KEY not configured — skipping post-upload step")
        return

    base      = cfg["base_url"]
    sid       = cfg["station_id"]
    folder    = cfg["folder"]
    headers   = {
        "X-API-Key":    cfg["api_key"],
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }
    batch_url  = f"{base}/api/station/{sid}/files/batch"
    search_url = f"{base}/api/station/{sid}/files"

    try:
        # ── 1. Wait ──────────────────────────────────────────────────────────
        print(f"[YT_API] Waiting {_API_WAIT_SECS}s for AzuraCast to notice new file…")
        time.sleep(_API_WAIT_SECS)

        # ── 2. Rescan ────────────────────────────────────────────────────────
        try:
            resp = req_lib.post(
                batch_url,
                json={"do": "rescan", "currentDirectory": folder},
                headers=headers,
                timeout=30,
            )
            print(f"[YT_API] Rescan → HTTP {resp.status_code}: {resp.text[:300]}")
        except Exception as exc:
            print(f"[YT_API] Rescan request error (non-fatal): {exc}")

        # ── 3. Search + retry (up to _API_RETRY_COUNT attempts) ─────────────
        file_id:   "int | None" = None
        unique_id: "str | None" = None
        for attempt in range(1, _API_RETRY_COUNT + 1):
            try:
                resp = req_lib.get(
                    search_url,
                    params={"searchPhrase": filename},
                    headers=headers,
                    timeout=15,
                )
                print(
                    f"[YT_API] Search {attempt}/{_API_RETRY_COUNT}"
                    f" → HTTP {resp.status_code}: {resp.text[:400]}"
                )
                if resp.status_code == 200:
                    data = resp.json()
                    rows = data if isinstance(data, list) else data.get("rows", [])
                    for row in rows:
                        if os.path.basename(row.get("path", "")) == filename:
                            file_id   = row.get("id")
                            unique_id = (
                                row.get("unique_id")
                                or row.get("song_unique_id")
                                or (row.get("song") or {}).get("id")
                                or ""
                            )
                            print(
                                f"[YT_API] File found — id={file_id}"
                                f"  unique_id={unique_id}  path={row.get('path')}"
                            )
                            break
            except Exception as exc:
                print(f"[YT_API] Search attempt {attempt} error: {exc}")

            if file_id is not None:
                break
            if attempt < _API_RETRY_COUNT:
                print(f"[YT_API] Not indexed yet — waiting {_API_RETRY_DELAY}s before retry…")
                time.sleep(_API_RETRY_DELAY)

        if file_id is None:
            print(
                f"[YT_API] '{filename}' not found after {_API_RETRY_COUNT} attempts"
                f" — skipping playlist/request steps"
            )
            return

        # ── 4. Add to Requests playlist ──────────────────────────────────────
        playlist_id = (os.environ.get("AZURA_PLAYLIST_ID") or "").strip()
        if playlist_id:
            try:
                resp = req_lib.post(
                    batch_url,
                    json={"do": "playlist", "playlist": playlist_id, "files": [file_id]},
                    headers=headers,
                    timeout=15,
                )
                print(f"[YT_API] Playlist add → HTTP {resp.status_code}: {resp.text[:300]}")
            except Exception as exc:
                print(f"[YT_API] Playlist add error (non-fatal): {exc}")
        else:
            print("[YT_API] AZURA_PLAYLIST_ID not set — skipping playlist add step")

        # ── 5. Submit to request queue ───────────────────────────────────────
        if unique_id:
            request_url = f"{base}/api/station/{sid}/request/{unique_id}"
            try:
                resp = req_lib.post(request_url, headers=headers, timeout=15)
                print(f"[YT_API] Request queue → HTTP {resp.status_code}: {resp.text[:300]}")
            except Exception as exc:
                print(f"[YT_API] Request queue error (non-fatal): {exc}")
        else:
            print("[YT_API] unique_id unavailable — skipping request queue step")

    except Exception as exc:
        print(f"[YT_API] Unexpected error in post-upload step (non-fatal): {exc}")

# ─────────────────────────────────────────────────────────────────────────────
# Async pipeline orchestrator
# ─────────────────────────────────────────────────────────────────────────────

async def _run_job(bot: "BaseBot", job: dict) -> None:
    """
    Orchestrate the full pipeline for one job.

    Download step  — run_in_executor (blocks until audio file is ready).
    Upload step    — daemon thread + asyncio.Event.  The bot whispers success
                     the instant sftp.put() returns; SSH cleanup runs in the
                     background thread without blocking the event loop.
    """
    jid    = job["id"]
    uid    = job["user_id"]
    tmpdir = tempfile.mkdtemp(prefix="ytr_")
    loop   = asyncio.get_running_loop()

    try:
        # ── Step 1: Download + convert ──────────────────────────────────────
        _update_job(jid, status="downloading")
        await _w(bot, uid, "⬇️ Downloading & converting audio… (~30–60s)")

        info, mp3_path = await loop.run_in_executor(
            None, _download_step, job["url"], tmpdir
        )
        title = (info.get("title") or "Unknown")[:160]
        _update_job(jid, title=title)

        # ── Step 2: SFTP upload (fire-and-forget after put completes) ────────
        _update_job(jid, status="uploading")
        await _w(bot, uid, f"📤 Uploading to AzuraCast…\n🎵 {title}")
        print(f"[YT_REQUEST] Job #{jid} — SFTP upload starting: {title[:80]}")

        # asyncio.Event is set by the upload thread the moment sftp.put()
        # returns.  _run_job unblocks HERE and whispers success immediately;
        # the thread keeps running to close the SSH connection in the background.
        upload_done: asyncio.Event               = asyncio.Event()
        upload_exc:  list[BaseException | None]  = [None]

        def _on_put_done() -> None:
            loop.call_soon_threadsafe(upload_done.set)

        def _upload_thread() -> None:
            try:
                _sftp_step(mp3_path, _on_put_done)
                # on_put_done() already fired → success whispered to user.
                # Now run API post-processing in this same background thread.
                _azura_post_upload(os.path.basename(mp3_path))
            except Exception as exc:
                # Only reached if _sftp_step raised BEFORE on_put_done() was called.
                upload_exc[0] = exc
                loop.call_soon_threadsafe(upload_done.set)

        t = threading.Thread(
            target=_upload_thread,
            daemon=True,
            name=f"ytr_upload_{jid}",
        )
        upload_start = time.time()
        t.start()

        await upload_done.wait()          # unblocks as soon as put() finishes
        upload_secs = time.time() - upload_start

        if upload_exc[0] is not None:
            raise upload_exc[0]

        # ── Done: whisper success immediately after put() ────────────────────
        _update_job(jid, status="done", finished_at=time.time())
        print(f"[YT_REQUEST] Job #{jid} — success in {upload_secs:.1f}s: {title[:80]}")
        await _w(bot, uid, f"✅ Added to Requests playlist!\n🎵 {title}")
        # Background thread is still closing the SSH connection — that's fine.

    except Exception as exc:
        err = str(exc)[:120]
        _update_job(jid, status="error", error=err, finished_at=time.time())
        print(f"[YT_REQUEST] Job #{jid} — FAILED: {exc}")
        await _w(bot, uid, f"❌ YT Request failed:\n{err}")

    finally:
        # Temp dir removed here; upload thread may still be running ssh.close()
        # but it holds no reference to tmpdir, so this is safe.
        shutil.rmtree(tmpdir, ignore_errors=True)

# ─────────────────────────────────────────────────────────────────────────────
# Command handlers
# ─────────────────────────────────────────────────────────────────────────────

async def handle_ytrequest(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!ytrequest <youtube_url> — download and add to AzuraCast Requests playlist."""

    # SFTP readiness check
    missing = _sftp_missing_vars()
    if missing:
        missing_str = ", ".join(missing)
        print(f"[YT_REQUEST] !ytrequest blocked — missing env vars: {missing_str}")
        await _w(
            bot, user.id,
            f"📻 YT Requests not configured.\n"
            f"Missing secret(s): {missing_str}"
        )
        return

    # Usage hint
    if len(args) < 2:
        cd = _cooldown_secs()
        await _w(
            bot, user.id,
            f"🎵 Usage: !ytrequest <youtube_url>\n"
            f"Adds audio to the AzuraCast Requests playlist.\n"
            f"Cooldown: {cd}s per user."
        )
        return

    url = args[1].strip().split("&list=")[0]   # strip playlist suffix

    # Validate URL
    if not _is_youtube_url(url):
        await _w(
            bot, user.id,
            "⚠️ Invalid YouTube URL.\n"
            "Supported: youtube.com/watch?v=... | youtu.be/... | youtube.com/shorts/..."
        )
        return

    # Per-user cooldown — owners bypass entirely, admins get 30 s flat
    _owner = is_owner(user.username)
    _admin = not _owner and is_admin(user.username)
    if _owner:
        cd = 0
    elif _admin:
        cd = 30
    else:
        cd = _cooldown_secs()

    if cd > 0:
        last    = _cooldowns.get(user.id, 0.0)
        elapsed = time.time() - last
        if elapsed < cd:
            remaining = int(cd - elapsed)
            role_hint = "admin" if _admin else "user"
            await _w(bot, user.id, f"⏳ Cooldown ({role_hint}): {remaining}s remaining.")
            return

    # Queue capacity check
    with _jobs_lock:
        active_count = sum(
            1 for j in _jobs.values()
            if j["status"] in ("pending", "downloading", "uploading")
        )
    if active_count >= _MAX_ACTIVE_JOBS:
        await _w(
            bot, user.id,
            f"📋 Request queue full ({_MAX_ACTIVE_JOBS} active). Try again in a moment."
        )
        return

    # Record cooldown and create job
    _cooldowns[user.id] = time.time()
    job = _new_job(user.id, user.username, url)

    await _w(
        bot, user.id,
        f"✅ YT Request received! (Job #{job['id']})\n"
        f"URL: {url[:100]}"
    )

    asyncio.create_task(_run_job(bot, job))


async def handle_ytqueue(bot: "BaseBot", user: "User", _args: list[str]) -> None:
    """!ytqueue — show active and recent YT request jobs (admin+)."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return

    with _jobs_lock:
        snap = sorted(_jobs.values(), key=lambda j: j["started_at"], reverse=True)[:8]

    if not snap:
        await _w(bot, user.id, "📋 YT Request queue: no jobs this session.")
        return

    _STATUS_ICON = {
        "pending":     "⏳",
        "downloading": "⬇️",
        "uploading":   "📤",
        "done":        "✅",
        "error":       "❌",
    }
    lines = ["📋 YT Requests (newest first):"]
    for j in snap:
        age   = int(time.time() - j["started_at"])
        icon  = _STATUS_ICON.get(j["status"], "?")
        title = f" — {j['title'][:28]}" if j["title"] else ""
        err   = f" [{j['error'][:20]}]" if j["status"] == "error" else ""
        lines.append(f"{icon} #{j['id']} @{j['username']}{title}{err} ({age}s)")

    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_ytstatus(bot: "BaseBot", user: "User", _args: list[str]) -> None:
    """!ytstatus — show YT request system config and session stats (admin+)."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return

    cfg   = _sftp_cfg()
    ready = _sftp_ready()
    cd    = _cooldown_secs()

    with _jobs_lock:
        total  = len(_jobs)
        active = sum(1 for j in _jobs.values() if j["status"] in ("pending", "downloading", "uploading"))
        done   = sum(1 for j in _jobs.values() if j["status"] == "done")
        errors = sum(1 for j in _jobs.values() if j["status"] == "error")

    sftp_ok   = "✅" if ready else "❌ missing vars"
    host_disp = cfg["host"][:30] if cfg["host"] else "(not set)"
    api_cfg   = _azura_api_cfg()
    api_disp  = "✅ " + (api_cfg["base_url"][:25] if api_cfg else "") if api_cfg else "⬜ disabled"

    await _w(
        bot, user.id,
        (f"📻 YT Request System:\n"
         f"SFTP: {sftp_ok} | {host_disp}:{cfg['port']}\n"
         f"API: {api_disp}\n"
         f"Folder: {cfg['folder']} | CD: {cd}s\n"
         f"Active: {active} | Done: {done} | Err: {errors} | Total: {total}")[:249],
    )
