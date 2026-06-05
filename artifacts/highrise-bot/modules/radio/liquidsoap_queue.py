"""Safe handoff helpers for the Liquidsoap request queue."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from modules.radio import settings as radio_settings


SAFE_REQUEST_RE = re.compile(r"^radio_request_\d+_[a-z0-9][a-z0-9_-]{0,60}\.mp3$")


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def queue_next_dir() -> Path:
    raw = str(radio_settings.get_setting("liquidsoap_queue_next_path", "liquidsoap/queue/next") or "").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = project_root() / path
    return path.resolve()


def _safe_slug(value: str, limit: int = 48) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    text = re.sub(r"_+", "_", text)
    return (text[:limit].strip("_") or "request")


def request_filename(request_id: int, title: str = "") -> str:
    return f"radio_request_{int(request_id)}_{_safe_slug(title)}.mp3"


def safe_request_filename(filename: str) -> bool:
    name = os.path.basename(str(filename or ""))
    return bool(name) and name == filename and bool(SAFE_REQUEST_RE.match(name))


def handoff_to_next(local_mp3: str | os.PathLike, request_id: int, title: str = "") -> tuple[bool, str, str]:
    source = Path(local_mp3)
    filename = request_filename(request_id, title)
    if not source.exists() or not source.is_file():
        return False, filename, "source_missing"
    if not safe_request_filename(filename):
        return False, filename, "unsafe_filename"
    target_dir = queue_next_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    temp_target = target.with_suffix(".tmp")
    try:
        shutil.copyfile(source, temp_target)
        os.replace(temp_target, target)
    except Exception as exc:
        try:
            if temp_target.exists():
                temp_target.unlink()
        except Exception:
            pass
        return False, str(target), repr(exc)
    return True, str(target), ""


def remove_request_file(path_or_filename: str) -> bool:
    raw = str(path_or_filename or "").strip()
    if not raw:
        return True
    path = Path(raw)
    if path.is_absolute():
        target = path.resolve()
        try:
            target.relative_to(queue_next_dir())
        except ValueError:
            return False
    else:
        filename = os.path.basename(raw)
        if not safe_request_filename(filename):
            return False
        target = queue_next_dir() / filename
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return True
    except Exception:
        return False
