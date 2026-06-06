"""Safe handoff helpers for the Liquidsoap request queue."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from modules.radio import settings as radio_settings


SAFE_REQUEST_RE = re.compile(r"^(?:000_priority_request|radio_request)_\d+_[a-z0-9][a-z0-9_-]{0,60}\.mp3$")


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def queue_next_dir() -> Path:
    return _configured_dir("liquidsoap_queue_next_path", "liquidsoap/queue/next")


def queue_played_dir() -> Path:
    return _configured_dir("liquidsoap_played_path", "liquidsoap/queue/played")


def queue_playing_dir() -> Path:
    return _configured_dir("liquidsoap_playing_path", "liquidsoap/queue/playing")


def _configured_dir(setting_key: str, default: str) -> Path:
    raw = str(radio_settings.get_setting(setting_key, default) or "").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = project_root() / path
    return path.resolve()


def _safe_slug(value: str, limit: int = 48) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    text = re.sub(r"_+", "_", text)
    return (text[:limit].strip("_") or "request")


def request_filename(request_id: int, title: str = "", priority: bool = False) -> str:
    prefix = "000_priority_request" if priority else "radio_request"
    return f"{prefix}_{int(request_id)}_{_safe_slug(title)}.mp3"


def safe_request_filename(filename: str) -> bool:
    name = os.path.basename(str(filename or ""))
    return bool(name) and name == filename and bool(SAFE_REQUEST_RE.match(name))


def request_path_in_next(path_or_filename: str) -> Path | None:
    return _request_path_in_dir(path_or_filename, queue_next_dir())


def request_path_in_playing(path_or_filename: str) -> Path | None:
    return _request_path_in_dir(path_or_filename, queue_playing_dir())


def _request_path_in_dir(path_or_filename: str, base_dir: Path) -> Path | None:
    raw = str(path_or_filename or "").strip()
    if not raw:
        return None
    path = Path(raw)
    filename = os.path.basename(raw)
    if not safe_request_filename(filename):
        return None
    target = path.resolve() if path.is_absolute() else (base_dir / filename).resolve()
    try:
        target.relative_to(base_dir)
    except ValueError:
        return None
    return target


def handoff_to_next(local_mp3: str | os.PathLike, request_id: int, title: str = "", priority: bool = False) -> tuple[bool, str, str]:
    source = Path(local_mp3)
    filename = request_filename(request_id, title, priority=priority)
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
    target = request_path_in_next(path_or_filename) or request_path_in_playing(path_or_filename)
    if target is None:
        return not str(path_or_filename or "").strip()
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return True
    except Exception:
        return False


def move_request_to_played(path_or_filename: str) -> tuple[bool, str, str]:
    source = request_path_in_playing(path_or_filename) or request_path_in_next(path_or_filename)
    if source is None:
        return False, "", "unsafe_or_outside_request_queue"
    filename = source.name
    if not safe_request_filename(filename):
        return False, str(source), "unsafe_filename"
    if not source.exists() or not source.is_file():
        return False, str(source), "source_missing"
    target_dir = queue_played_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = (target_dir / filename).resolve()
    try:
        target.relative_to(target_dir)
    except ValueError:
        return False, str(target), "target_outside_played"
    try:
        os.replace(source, target)
    except Exception as exc:
        return False, str(target), repr(exc)
    return True, str(target), ""


def move_request_to_playing(path_or_filename: str) -> tuple[bool, str, str]:
    source = request_path_in_next(path_or_filename)
    if source is None:
        return False, "", "unsafe_or_outside_next"
    filename = source.name
    if not safe_request_filename(filename):
        return False, str(source), "unsafe_filename"
    if not source.exists() or not source.is_file():
        return False, str(source), "source_missing"
    target_dir = queue_playing_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = (target_dir / filename).resolve()
    try:
        target.relative_to(target_dir)
    except ValueError:
        return False, str(target), "target_outside_playing"
    try:
        os.replace(source, target)
    except Exception as exc:
        return False, str(target), repr(exc)
    return True, str(target), ""
