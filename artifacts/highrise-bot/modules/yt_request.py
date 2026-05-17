"""
modules/yt_request.py
---------------------
!ytrequest <youtube_url>
    Download YouTube audio, convert to mp3 via ffmpeg, and upload via the
    AzuraCast REST API to the Requests folder so AzuraCast adds it to the
    playlist.

    No audio is streamed or re-broadcast by the bot itself.
    AzuraCast's own playback engine serves the file to listeners.

!ytqueue   (admin+) — show active/pending jobs and recent results
!ytstatus  (admin+) — show API config readiness and session stats

BOT_MODE = dj only.  DJ_DUDU is the sole owner of these commands.

Per-user cooldown: configurable via room_setting yt_request_cooldown
                   (default 300 s).  Set with !djset ytcooldown <secs>.
                   Minimum enforced: 30 s.

Required env vars:
    AZURA_BASE_URL          Base URL of your AzuraCast instance
                            e.g. https://xyz.trycloudflare.com
    AZURA_API_KEY           AzuraCast API key (X-API-Key header)
    AZURA_STATION_ID        Station numeric ID (default: 1)
    AZURA_REQUESTS_FOLDER   Subfolder inside station media (default: Requests)

Pipeline per request:
    1. Validate YouTube URL
    2. Check per-user cooldown
    3. yt-dlp download + ffmpeg mp3 conversion  (temp dir, deleted after)
    4. aiohttp multipart POST → /api/station/{id}/files
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
from typing import TYPE_CHECKING

import aiohttp

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

_REQUIRED_API_VARS = ("AZURA_BASE_URL", "AZURA_API_KEY")

def _api_cfg() -> dict:
    return {
        "base_url":   (os.environ.get("AZURA_BASE_URL") or "").rstrip("/").strip(),
        "api_key":    (os.environ.get("AZURA_API_KEY") or "").strip(),
        "station_id": (os.environ.get("AZURA_STATION_ID") or "1").strip(),
        "folder":     (os.environ.get("AZURA_REQUESTS_FOLDER") or "Requests").strip(),
    }

def _api_missing_vars() -> list[str]:
    missing = []
    for var in _REQUIRED_API_VARS:
        raw = os.environ.get(var)
        if not raw or not raw.strip():
            missing.append(var)
    return missing

def _api_ready() -> bool:
    return len(_api_missing_vars()) == 0

def _log_api_env() -> None:
    """Log API config at startup. Base URL is printed as-is; API key shows length only."""
    cfg = _api_cfg()

    base_url = cfg["base_url"]
    if base_url:
        print(f"[YT_REQUEST] AZURA_BASE_URL = {base_url}")
    else:
        print("[YT_REQUEST] AZURA_BASE_URL: NOT SET  ← required, YT requests disabled")

    api_key_raw = (os.environ.get("AZURA_API_KEY") or "").strip()
    if api_key_raw:
        print(f"[YT_REQUEST] AZURA_API_KEY: SET (len={len(api_key_raw)})")
    else:
        print("[YT_REQUEST] AZURA_API_KEY: NOT SET  ← required, YT requests disabled")

    print(f"[YT_REQUEST] AZURA_STATION_ID = {cfg['station_id']}")
    print(f"[YT_REQUEST] AZURA_REQUESTS_FOLDER = {cfg['folder']}")

# Log API config readiness once at import (visible in workflow console)
_log_api_env()

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
# Async API upload step
# ─────────────────────────────────────────────────────────────────────────────

_API_UPLOAD_TIMEOUT = aiohttp.ClientTimeout(
    total=300,        # 5 min max for the full upload
    connect=30,       # 30 s to establish connection
    sock_connect=30,
    sock_read=180,    # 3 min for read chunks
)

async def _api_upload_step(mp3_path: str, jid: int) -> None:
    """
    Async multipart POST to AzuraCast /api/station/{id}/files.

    Log lines (in order for a successful upload):
      [YT_API] Uploading → {url}  file={filename} ({size} bytes)
      [YT_API] Upload finished ✓ — HTTP {status}
      [YT_API] Upload failed — HTTP {status}: {body}   (on non-2xx)
    """
    cfg        = _api_cfg()
    filename   = os.path.basename(mp3_path)
    remote_path = f"{cfg['folder'].rstrip('/')}/{filename}"
    file_size  = os.path.getsize(mp3_path)
    endpoint   = f"{cfg['base_url']}/api/station/{cfg['station_id']}/files"

    print(
        f"[YT_API] Uploading → {endpoint}"
        f"  file={filename} ({file_size:,} bytes)"
        f"  path={remote_path}"
    )

    headers = {"X-API-Key": cfg["api_key"]}

    with open(mp3_path, "rb") as fh:
        form = aiohttp.FormData()
        form.add_field(
            "file",
            fh,
            filename=filename,
            content_type="audio/mpeg",
        )
        form.add_field("path", remote_path)

        async with aiohttp.ClientSession(timeout=_API_UPLOAD_TIMEOUT) as session:
            async with session.post(endpoint, data=form, headers=headers) as resp:
                body = await resp.text()
                if resp.status in (200, 201):
                    print(
                        f"[YT_API] Upload finished ✓ — HTTP {resp.status}"
                        f"  {file_size:,} bytes → {remote_path}"
                    )
                else:
                    print(
                        f"[YT_API] Upload failed — HTTP {resp.status}: {body[:200]}"
                    )
                    raise RuntimeError(
                        f"AzuraCast API returned HTTP {resp.status}: {body[:120]}"
                    )

# ─────────────────────────────────────────────────────────────────────────────
# Async pipeline orchestrator
# ─────────────────────────────────────────────────────────────────────────────

async def _run_job(bot: "BaseBot", job: dict) -> None:
    """
    Orchestrate the full pipeline for one job.

    Download step  — run_in_executor (blocks until audio file is ready).
    Upload step    — async aiohttp POST directly from the event loop.
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

        # ── Step 2: API upload ───────────────────────────────────────────────
        _update_job(jid, status="uploading")
        await _w(bot, uid, f"📤 Uploading to AzuraCast…\n🎵 {title}")
        print(f"[YT_REQUEST] Job #{jid} — API upload starting: {title[:80]}")

        upload_start = time.time()
        await _api_upload_step(mp3_path, jid)
        upload_secs  = time.time() - upload_start

        # ── Done ─────────────────────────────────────────────────────────────
        _update_job(jid, status="done", finished_at=time.time())
        print(f"[YT_REQUEST] Job #{jid} — success in {upload_secs:.1f}s: {title[:80]}")
        await _w(bot, uid, f"✅ Added to Requests playlist!\n🎵 {title}")

    except Exception as exc:
        err = str(exc)[:120]
        _update_job(jid, status="error", error=err, finished_at=time.time())
        print(f"[YT_REQUEST] Job #{jid} — FAILED: {exc}")
        await _w(bot, uid, f"❌ YT Request failed:\n{err}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

# ─────────────────────────────────────────────────────────────────────────────
# Command handlers
# ─────────────────────────────────────────────────────────────────────────────

async def handle_ytrequest(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!ytrequest <youtube_url> — download and add to AzuraCast Requests playlist."""

    # API readiness check
    missing = _api_missing_vars()
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

    cfg   = _api_cfg()
    ready = _api_ready()
    cd    = _cooldown_secs()

    with _jobs_lock:
        total  = len(_jobs)
        active = sum(1 for j in _jobs.values() if j["status"] in ("pending", "downloading", "uploading"))
        done   = sum(1 for j in _jobs.values() if j["status"] == "done")
        errors = sum(1 for j in _jobs.values() if j["status"] == "error")

    api_ok    = "✅" if ready else "❌ missing vars"
    url_disp  = cfg["base_url"][:40] if cfg["base_url"] else "(not set)"

    await _w(
        bot, user.id,
        (f"📻 YT Request System:\n"
         f"API: {api_ok} | {url_disp}\n"
         f"Station: {cfg['station_id']} | Folder: {cfg['folder']} | CD: {cd}s\n"
         f"Active: {active} | Done: {done} | Errors: {errors} | Total: {total}")[:249],
    )
