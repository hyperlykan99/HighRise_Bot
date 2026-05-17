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
_OPTIONAL_SFTP_VARS = ("AZURA_SFTP_PORT", "AZURA_REQUESTS_FOLDER")

def _sftp_cfg() -> dict:
    return {
        "host":   (os.environ.get("AZURA_SFTP_HOST") or "").strip(),
        "port":   int((os.environ.get("AZURA_SFTP_PORT") or "22").strip() or "22"),
        "user":   (os.environ.get("AZURA_SFTP_USER") or "").strip(),
        "passwd": (os.environ.get("AZURA_SFTP_PASS") or "").strip(),
        "folder": (os.environ.get("AZURA_REQUESTS_FOLDER") or "Requests").strip(),
    }

def _sftp_missing_vars() -> list[str]:
    """Return list of required SFTP env var names that are unset or empty."""
    missing = []
    for var in _REQUIRED_SFTP_VARS:
        raw = os.environ.get(var)
        if not raw or not raw.strip():
            missing.append(var)
    return missing

def _sftp_ready() -> bool:
    return len(_sftp_missing_vars()) == 0

def _log_sftp_env() -> None:
    """Log SFTP env var presence at startup (never logs values)."""
    for var in _REQUIRED_SFTP_VARS:
        raw = os.environ.get(var)
        if raw and raw.strip():
            print(f"[YT_REQUEST] {var}: SET (len={len(raw.strip())})")
        else:
            status = "EMPTY" if raw is not None else "NOT SET"
            print(f"[YT_REQUEST] {var}: {status}  ← required, YT requests will be disabled")
    for var in _OPTIONAL_SFTP_VARS:
        raw = os.environ.get(var)
        val_hint = f"SET (len={len(raw.strip())})" if raw and raw.strip() else "not set (using default)"
        print(f"[YT_REQUEST] {var}: {val_hint}")

# Log SFTP config readiness once at import (visible in workflow console)
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


_SFTP_CONNECT_TIMEOUT  = 30   # seconds — TCP connect + SSH handshake + auth
_SFTP_TRANSFER_TIMEOUT = 180  # seconds — per-channel socket timeout for file transfer

def _sftp_step(
    mp3_path: str,
    on_put_done: Callable[[], None],
) -> None:
    """
    Blocking SFTP upload.  Runs inside a daemon thread started by _run_job.

    Design contract
    ───────────────
    • on_put_done() is called the INSTANT sftp.put() returns without raising.
      The caller uses this to signal the async layer and whisper success
      immediately — before this function does any cleanup.
    • on_put_done() is NOT called if upload fails; the exception propagates
      normally and the thread wrapper signals the error instead.
    • sftp.close() / ssh.close() happen after on_put_done() in the same thread
      and are fully fire-and-forget: they are wrapped in try/except and can
      never propagate an exception or block the bot.

    Timeouts
    ────────
    • TCP connect + SSH handshake + auth : _SFTP_CONNECT_TIMEOUT  (30 s)
    • sftp.put() transfer channel        : _SFTP_TRANSFER_TIMEOUT (180 s)
    • post-upload cleanup close          : 5 s  (channel timeout shortened)

    Log lines (always in order for a successful upload)
    ────────────────────────────────────────────────────
      [YT_SFTP] Connecting → …
      [YT_SFTP] SFTP connected
      [YT_SFTP] Upload started → <path>
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

        # ── 4. PUT RETURNED — file is on the server ───────────────────────────
        #    Signal success to the async layer RIGHT NOW before any cleanup.
        print(f"[YT_SFTP] Upload finished ✓ — {file_size:,} bytes")
        on_put_done()

        # ── 5. Cleanup (fire-and-forget; never raises) ────────────────────────
        #    Shorten channel timeout so close() doesn't wait long for ACK.
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
        # Upload failed before on_put_done() was called.
        # Best-effort cleanup, then re-raise so the thread wrapper can signal error.
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
# Async pipeline orchestrator
# ─────────────────────────────────────────────────────────────────────────────

async def _run_job(bot: "BaseBot", job: dict) -> None:
    """
    Orchestrate the full pipeline for one job.

    Download step   — run_in_executor (blocks until audio file is ready).
    Upload step     — daemon thread + asyncio.Event so the bot whispers
                      success the instant sftp.put() returns; cleanup of the
                      SSH connection happens in the background without ever
                      blocking the event loop or delaying the success whisper.
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

        # asyncio.Event signalled by the upload thread the moment sftp.put()
        # returns.  _run_job unblocks HERE and whispers success immediately;
        # the thread keeps running to close the connection in the background.
        upload_done: asyncio.Event        = asyncio.Event()
        upload_exc:  list[BaseException | None] = [None]

        def _on_put_done() -> None:
            """Called by _sftp_step from the upload thread after put() succeeds."""
            loop.call_soon_threadsafe(upload_done.set)

        def _upload_thread() -> None:
            try:
                _sftp_step(mp3_path, _on_put_done)
            except Exception as exc:
                # Upload failed; store error and unblock the waiter.
                upload_exc[0] = exc
                loop.call_soon_threadsafe(upload_done.set)

        t = threading.Thread(
            target=_upload_thread,
            daemon=True,
            name=f"ytr_upload_{jid}",
        )
        upload_start = time.time()
        t.start()

        # Wait only until sftp.put() finishes — NOT until ssh.close() finishes.
        await upload_done.wait()
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
        # but it holds no reference to tmpdir so this is safe.
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
    host_disp = cfg["host"][:40] if cfg["host"] else "(not set)"

    await _w(
        bot, user.id,
        (f"📻 YT Request System:\n"
         f"SFTP: {sftp_ok} | {host_disp}:{cfg['port']}\n"
         f"Folder: {cfg['folder']} | Cooldown: {cd}s\n"
         f"Active: {active} | Done: {done} | Errors: {errors} | Total: {total}")[:249],
    )
