"""Owner-triggered SpotDL playlist sync helpers for Liquidsoap AutoDJ."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from modules.radio import liquidsoap_queue
from modules.radio import settings as radio_settings


AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}
SAFE_VIBE_RE = re.compile(r"^[a-z0-9_]{1,48}$")
SPOTDL_DEFAULT = "/opt/highrise-bots/spotdl-venv/bin/spotdl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _autodj_root() -> Path:
    return (liquidsoap_queue.project_root() / "liquidsoap" / "autodj").resolve()


def _sync_jobs_dir() -> Path:
    return (_autodj_root() / "sync_jobs").resolve()


def _rejected_dir(safe_vibe_name: str) -> Path:
    return (_autodj_root() / "rejected" / safe_vibe_name).resolve()


def vibe_dir(safe_vibe_name: str) -> Path:
    return (_autodj_root() / "vibes" / safe_vibe_name).resolve()


def safe_vibe_name(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:48]


def _valid_playlist_url(value: str) -> bool:
    raw = str(value or "").strip()
    return raw.startswith("https://open.spotify.com/playlist/") or raw.startswith("spotify:playlist:")


def _audio_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file() and item.suffix.lower() in AUDIO_EXTS)


def _job_path(job_id: str) -> Path:
    return _sync_jobs_dir() / f"job_{job_id}.json"


def _log_path(job_id: str) -> Path:
    return _sync_jobs_dir() / f"job_{job_id}.log"


def _write_job(job: dict) -> None:
    path = _job_path(str(job["job_id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(job, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def _read_job(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _update_job(job_id: str, **fields) -> dict | None:
    job = _read_job(_job_path(job_id))
    if not job:
        return None
    job.update(fields)
    _write_job(job)
    return job


def _latest_job_path() -> Path | None:
    if not _sync_jobs_dir().exists():
        return None
    paths = sorted(_sync_jobs_dir().glob("job_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return paths[0] if paths else None


def _recent_log_line(log_file: str) -> str:
    try:
        lines = Path(log_file).read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    for line in reversed(lines):
        line = line.strip()
        if line:
            return line[-240:]
    return ""


def _format_status(job: dict | None) -> str:
    if not job:
        return "🎛 AutoDJ Sync\nNo sync jobs found."
    output_dir = Path(str(job.get("output_dir") or ""))
    current_files = _audio_count(output_dir)
    status = str(job.get("status") or "unknown")
    lines = [
        "🎛 AutoDJ Sync",
        f"Job: {job.get('job_id')}",
        f"Status: {status}",
        f"Vibe: {job.get('vibe_name') or job.get('safe_vibe_name')}",
        f"Audio files: {current_files}",
        f"Downloaded: {job.get('downloaded_count', 0)}",
        f"Existing: {job.get('existing_count', 0)}",
        f"Failed: {job.get('failed_count', 0)}",
    ]
    recent = str(job.get("current_line") or "").strip() or _recent_log_line(str(job.get("log_file") or ""))
    if recent:
        lines.append(f"Recent: {recent[:160]}")
    lines.append(f"Log: {job.get('log_file')}")
    print(f"[AUTODJ_SYNC_STATUS] job_id={job.get('job_id')} status={status!r} files={current_files}")
    return "\n".join(lines)


def _run_spotdl_job(job_id: str, command: list[str]) -> None:
    job = _read_job(_job_path(job_id))
    if not job:
        return
    log_file = Path(str(job["log_file"]))
    output_dir = Path(str(job["output_dir"]))
    failed_count = 0
    try:
        with log_file.open("a", encoding="utf-8") as log:
            log.write(f"[{_now_iso()}] Starting: {' '.join(command)}\n")
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            _update_job(job_id, process_id=proc.pid)
            print(f"[AUTODJ_SYNC_PROCESS_STARTED] job_id={job_id} pid={proc.pid}")
            assert proc.stdout is not None
            for line in proc.stdout:
                clean = line.rstrip()
                log.write(clean + "\n")
                log.flush()
                lowered = clean.lower()
                if "failed" in lowered or "error" in lowered:
                    failed_count += 1
                _update_job(
                    job_id,
                    current_line=clean[-500:],
                    downloaded_count=max(0, _audio_count(output_dir) - int(job.get("existing_count") or 0)),
                    failed_count=failed_count,
                )
            code = proc.wait()
            final_count = _audio_count(output_dir)
            status = "completed" if code == 0 else "failed"
            _update_job(
                job_id,
                status=status,
                finished_at=_now_iso(),
                downloaded_count=max(0, final_count - int(job.get("existing_count") or 0)),
                failed_count=failed_count if failed_count else (0 if code == 0 else 1),
                current_line=f"spotdl exited with code {code}",
            )
            if code != 0:
                print(f"[AUTODJ_SYNC_FAILED] job_id={job_id} returncode={code}")
    except Exception as exc:
        _update_job(job_id, status="failed", finished_at=_now_iso(), current_line=repr(exc), failed_count=1)
        print(f"[AUTODJ_SYNC_FAILED] job_id={job_id} error={exc!r}")


def start_sync(vibe_name: str, playlist_url: str) -> tuple[bool, str]:
    raw_vibe = str(vibe_name or "").strip()
    safe = safe_vibe_name(raw_vibe)
    if not safe or not SAFE_VIBE_RE.match(safe) or safe != raw_vibe.lower():
        return False, "⚠️ Use a safe vibe name: lowercase letters, numbers, and underscores only."
    if not _valid_playlist_url(playlist_url):
        return False, "Use: !syncvibe <vibe_name> <spotify_playlist_url>"
    spotdl = str(radio_settings.get_setting("spotdl_bin_path", SPOTDL_DEFAULT) or SPOTDL_DEFAULT).strip()
    job_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    out_dir = vibe_dir(safe)
    jobs_dir = _sync_jobs_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    _rejected_dir(safe).mkdir(parents=True, exist_ok=True)
    log_file = _log_path(job_id)
    output_template = str(out_dir / "{artists} - {title}.{output-ext}")
    existing = _audio_count(out_dir)
    command = [spotdl, str(playlist_url).strip(), "--output", output_template]
    job = {
        "job_id": job_id,
        "vibe_name": raw_vibe,
        "safe_vibe_name": safe,
        "playlist_url": str(playlist_url).strip(),
        "status": "running",
        "started_at": _now_iso(),
        "finished_at": "",
        "total_tracks": 0,
        "downloaded_count": 0,
        "existing_count": existing,
        "failed_count": 0,
        "current_line": "",
        "current_track": "",
        "output_dir": str(out_dir),
        "log_file": str(log_file),
        "command": command,
    }
    _write_job(job)
    print(f"[AUTODJ_SYNC_START] job_id={job_id} vibe={safe!r} playlist_url={playlist_url!r}")
    thread = threading.Thread(target=_run_spotdl_job, args=(job_id, command), daemon=True)
    thread.start()
    return True, f"✅ AutoDJ sync started\nJob: {job_id}\nVibe: {safe}\nCheck: !syncstatus {job_id}"


def status(job_id: str = "") -> str:
    _sync_jobs_dir().mkdir(parents=True, exist_ok=True)
    raw = str(job_id or "").strip()
    path = _job_path(raw) if raw else _latest_job_path()
    if not path or not path.exists():
        return _format_status(None)
    return _format_status(_read_job(path))
