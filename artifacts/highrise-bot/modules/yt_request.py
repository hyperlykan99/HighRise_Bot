"""
modules/yt_request.py
---------------------
!ytrequest <youtube_url>
    Download YouTube audio, convert to mp3 via ffmpeg, and upload via SFTP
    to the AzuraCast /Requests folder so AzuraCast adds it to the playlist.

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
    AZURA_SFTP_PORT            (default 22)
    AZURA_SFTP_USER            SFTP username
    AZURA_SFTP_PASS            SFTP password
    AZURA_REQUESTS_FOLDER      remote path of Requests folder (default Requests)

Pipeline per request:
    1. Validate YouTube URL
    2. Check per-user cooldown
    3. yt-dlp download + ffmpeg mp3 conversion  (temp dir, deleted after)
    4. paramiko SFTP upload to AZURA_REQUESTS_FOLDER/<id>.mp3
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

import database as db
from modules.permissions import is_admin

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

def _sftp_cfg() -> dict:
    return {
        "host":   os.environ.get("AZURA_SFTP_HOST", "").strip(),
        "port":   int(os.environ.get("AZURA_SFTP_PORT", "22") or "22"),
        "user":   os.environ.get("AZURA_SFTP_USER", "").strip(),
        "passwd": os.environ.get("AZURA_SFTP_PASS", "").strip(),
        "folder": os.environ.get("AZURA_REQUESTS_FOLDER", "Requests").strip(),
    }

def _sftp_ready() -> bool:
    cfg = _sftp_cfg()
    return bool(cfg["host"] and cfg["user"] and cfg["passwd"])

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
# Blocking pipeline steps  (run in thread executor — must not touch event loop)
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


def _sftp_step(mp3_path: str) -> None:
    """
    Upload mp3_path to AzuraCast via SFTP.
    Raises on connection/upload failure.
    """
    import paramiko  # installed at bot startup

    cfg = _sftp_cfg()
    remote_filename = os.path.basename(mp3_path)
    remote_path = f"{cfg['folder'].rstrip('/')}/{remote_filename}"

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(
            hostname=cfg["host"],
            port=cfg["port"],
            username=cfg["user"],
            password=cfg["passwd"],
            timeout=30,
            look_for_keys=False,
            allow_agent=False,
        )
        sftp = ssh.open_sftp()
        try:
            sftp.put(mp3_path, remote_path)
        finally:
            sftp.close()
    finally:
        ssh.close()

# ─────────────────────────────────────────────────────────────────────────────
# Async pipeline orchestrator
# ─────────────────────────────────────────────────────────────────────────────

async def _run_job(bot: "BaseBot", job: dict) -> None:
    """
    Orchestrate the full pipeline for one job, sending status whispers at each
    step.  Runs download and upload in the thread executor so the event loop is
    never blocked.
    """
    jid     = job["id"]
    uid     = job["user_id"]
    tmpdir  = tempfile.mkdtemp(prefix="ytr_")
    loop    = asyncio.get_running_loop()

    try:
        # ── Step 1: Download + convert ──────────────────────────────────────
        _update_job(jid, status="downloading")
        await _w(bot, uid, "⬇️ Downloading & converting audio… (~30–60s)")

        info, mp3_path = await loop.run_in_executor(
            None, _download_step, job["url"], tmpdir
        )
        title = (info.get("title") or "Unknown")[:160]
        _update_job(jid, title=title)

        # ── Step 2: SFTP upload ─────────────────────────────────────────────
        _update_job(jid, status="uploading")
        await _w(bot, uid, f"📤 Uploading to AzuraCast…\n🎵 {title}")

        await loop.run_in_executor(None, _sftp_step, mp3_path)

        # ── Done ────────────────────────────────────────────────────────────
        _update_job(jid, status="done", finished_at=time.time())
        await _w(
            bot, uid,
            f"✅ Added to Requests playlist!\n🎵 {title}"
        )

    except Exception as exc:
        err = str(exc)[:120]
        _update_job(jid, status="error", error=err, finished_at=time.time())
        await _w(bot, uid, f"❌ YT Request failed:\n{err}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

# ─────────────────────────────────────────────────────────────────────────────
# Command handlers
# ─────────────────────────────────────────────────────────────────────────────

async def handle_ytrequest(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!ytrequest <youtube_url> — download and add to AzuraCast Requests playlist."""

    # SFTP readiness check
    if not _sftp_ready():
        await _w(
            bot, user.id,
            "📻 YT Requests not set up yet.\n"
            "Admin: add AZURA_SFTP_HOST / AZURA_SFTP_USER / AZURA_SFTP_PASS env vars."
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

    # Per-user cooldown
    cd       = _cooldown_secs()
    last     = _cooldowns.get(user.id, 0.0)
    elapsed  = time.time() - last
    if elapsed < cd:
        remaining = int(cd - elapsed)
        await _w(bot, user.id, f"⏳ Cooldown: {remaining}s before your next YT request.")
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
    host_disp = cfg["host"][:40] if cfg["host"] else "(not set)"

    await _w(
        bot, user.id,
        (f"📻 YT Request System:\n"
         f"SFTP: {sftp_ok} | {host_disp}:{cfg['port']}\n"
         f"Folder: {cfg['folder']} | Cooldown: {cd}s\n"
         f"Active: {active} | Done: {done} | Errors: {errors} | Total: {total}")[:249],
    )
