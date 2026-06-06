"""Safe helpers for the DB-backed Liquidsoap request controller."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from modules.radio import settings as radio_settings


SAFE_REQUEST_RE = re.compile(r"^(?:000_priority_request|radio_request)_\d+_[a-z0-9][a-z0-9_-]{0,60}\.mp3$")


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def request_library_dir() -> Path:
    return _configured_dir("radio_request_library_path", "data/radio_requests")


def current_request_playlist_path() -> Path:
    return _configured_file("radio_current_request_playlist_path", "liquidsoap/current_request.m3u")


def request_queue_playlist_path() -> Path:
    return _configured_file("radio_request_queue_playlist_path", "liquidsoap/request_queue.m3u")


def played_archive_dir() -> Path:
    return request_library_dir() / "archive" / "played"


def failed_archive_dir() -> Path:
    return request_library_dir() / "archive" / "failed"


def skipped_archive_dir() -> Path:
    return request_library_dir() / "archive" / "skipped"


def queue_next_dir() -> Path:
    return _configured_dir("liquidsoap_queue_next_path", "liquidsoap/queue/next")


def queue_playing_dir() -> Path:
    return _configured_dir("liquidsoap_playing_path", "liquidsoap/queue/playing")


def queue_played_dir() -> Path:
    return _configured_dir("liquidsoap_played_path", "liquidsoap/queue/played")


def staging_dir() -> Path:
    return _configured_dir("radio_staging_path", "data/radio_staging")


def _configured_dir(setting_key: str, default: str) -> Path:
    path = _configured_file(setting_key, default)
    return path.resolve()


def _configured_file(setting_key: str, default: str) -> Path:
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


def request_path_in_library(path_or_filename: str) -> Path | None:
    return _request_path_in_dir(path_or_filename, request_library_dir())


def request_path_in_next(path_or_filename: str) -> Path | None:
    return _request_path_in_dir(path_or_filename, queue_next_dir())


def request_path_in_playing(path_or_filename: str) -> Path | None:
    return _request_path_in_dir(path_or_filename, queue_playing_dir())


def request_path_in_staging(path_or_filename: str) -> Path | None:
    return _request_path_in_dir(path_or_filename, staging_dir())


def prepare_to_library(local_mp3: str | os.PathLike, request_id: int, title: str = "", priority: bool = False) -> tuple[bool, str, str]:
    source = Path(local_mp3)
    filename = request_filename(request_id, title, priority=priority)
    if not source.exists() or not source.is_file():
        return False, filename, "source_missing"
    if not safe_request_filename(filename):
        return False, filename, "unsafe_filename"
    target_dir = request_library_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = (target_dir / filename).resolve()
    try:
        target.relative_to(target_dir)
    except ValueError:
        return False, str(target), "target_outside_library"
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


def handoff_to_staging(local_mp3: str | os.PathLike, request_id: int, title: str = "", priority: bool = False) -> tuple[bool, str, str]:
    return prepare_to_library(local_mp3, request_id, title, priority=priority)


def write_current_request_playlist(request_path: str | os.PathLike) -> tuple[bool, str]:
    target = request_path_in_library(str(request_path))
    if target is None:
        return False, "request_path_outside_library"
    if not target.exists() or not target.is_file():
        return False, "request_file_missing"
    playlist = current_request_playlist_path()
    playlist.parent.mkdir(parents=True, exist_ok=True)
    tmp = playlist.with_suffix(".tmp")
    try:
        tmp.write_text(f"{str(target)}\n", encoding="utf-8")
        os.replace(tmp, playlist)
    except Exception as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False, repr(exc)
    return True, ""


def write_request_queue_playlist(request_paths: list[str] | tuple[str, ...]) -> tuple[bool, str, int]:
    playlist = request_queue_playlist_path()
    playlist.parent.mkdir(parents=True, exist_ok=True)
    safe_paths: list[str] = []
    seen: set[str] = set()
    for raw_path in request_paths or []:
        target = request_path_in_library(str(raw_path or ""))
        if target is None:
            return False, "request_path_outside_library", len(safe_paths)
        if not target.exists() or not target.is_file():
            return False, f"request_file_missing:{target}", len(safe_paths)
        if not safe_request_filename(target.name):
            return False, f"unsafe_request_filename:{target.name}", len(safe_paths)
        resolved = str(target.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        safe_paths.append(resolved)
    tmp = playlist.with_suffix(".tmp")
    try:
        tmp.write_text("".join(f"{path}\n" for path in safe_paths), encoding="utf-8")
        os.replace(tmp, playlist)
    except Exception as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False, repr(exc), len(safe_paths)
    return True, "", len(safe_paths)


def request_queue_playlist_targets() -> list[str]:
    playlist = request_queue_playlist_path()
    try:
        return [
            line.strip()
            for line in playlist.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    except FileNotFoundError:
        return []
    except Exception:
        return []


def request_queue_playlist_contains(request_path: str | os.PathLike) -> bool:
    target = request_path_in_library(str(request_path))
    if target is None:
        return False
    try:
        wanted = str(target.resolve())
        return any(str(Path(raw).resolve()) == wanted for raw in request_queue_playlist_targets())
    except Exception:
        return False


def clear_current_request_playlist() -> bool:
    playlist = current_request_playlist_path()
    playlist.parent.mkdir(parents=True, exist_ok=True)
    tmp = playlist.with_suffix(".tmp")
    try:
        tmp.write_text("", encoding="utf-8")
        os.replace(tmp, playlist)
        return True
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False


def current_request_playlist_target() -> str:
    playlist = current_request_playlist_path()
    try:
        for line in playlist.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if raw and not raw.startswith("#"):
                return raw
    except FileNotFoundError:
        return ""
    except Exception:
        return ""
    return ""


def playlist_points_to(request_path: str | os.PathLike) -> bool:
    target = request_path_in_library(str(request_path))
    if target is None:
        return False
    raw = current_request_playlist_target()
    if not raw:
        return False
    try:
        return Path(raw).resolve() == target.resolve()
    except Exception:
        return False


def remove_request_file(path_or_filename: str) -> bool:
    target = (
        request_path_in_library(path_or_filename)
        or request_path_in_staging(path_or_filename)
        or request_path_in_next(path_or_filename)
        or request_path_in_playing(path_or_filename)
    )
    if target is None:
        return not str(path_or_filename or "").strip()
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return True
    except Exception:
        return False


def archive_or_delete_request_file(path_or_filename: str, reason: str = "played", mode: str = "archive") -> tuple[bool, str, str]:
    source = request_path_in_library(path_or_filename)
    if source is None:
        return False, "", "unsafe_or_outside_library"
    if not source.exists() or not source.is_file():
        return True, str(source), "source_missing"
    if str(mode or "").strip().lower() == "delete":
        try:
            source.unlink()
            return True, "", ""
        except Exception as exc:
            return False, str(source), repr(exc)
    archive_dirs = {
        "played": played_archive_dir,
        "failed": failed_archive_dir,
        "skipped": skipped_archive_dir,
    }
    target_dir = archive_dirs.get(str(reason or "").strip().lower(), played_archive_dir)()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = (target_dir / source.name).resolve()
    try:
        target.relative_to(target_dir)
    except ValueError:
        return False, str(target), "target_outside_archive"
    if target.exists():
        stem = target.stem
        suffix = target.suffix
        for index in range(2, 1000):
            candidate = (target_dir / f"{stem}_{index}{suffix}").resolve()
            try:
                candidate.relative_to(target_dir)
            except ValueError:
                return False, str(candidate), "target_outside_archive"
            if not candidate.exists():
                target = candidate
                break
    try:
        os.replace(source, target)
    except Exception as exc:
        return False, str(target), repr(exc)
    return True, str(target), ""


def list_request_files(folder: str) -> list[Path]:
    dirs = {
        "next": queue_next_dir,
        "playing": queue_playing_dir,
        "played": queue_played_dir,
        "staging": staging_dir,
        "library": request_library_dir,
    }
    getter = dirs.get(str(folder or "").strip().lower())
    if not getter:
        return []
    base = getter()
    try:
        return sorted(
            [path for path in base.iterdir() if path.is_file() and safe_request_filename(path.name)],
            key=lambda path: path.name,
        )
    except FileNotFoundError:
        return []
    except Exception:
        return []
