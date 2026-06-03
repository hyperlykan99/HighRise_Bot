"""Read-only AzuraCast helpers for the rebuilt radio skeleton."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from modules import config_store as cs


SAFE_PREFIXES = ("radio_yt_", "radio_local_", "radio_req_")


def _api_request(path: str, timeout: int = 8, method: str = "GET", payload: dict | None = None) -> dict | list | None:
    cfg = cs.azura_api_cfg()
    if not cfg:
        return None
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    url = f"{cfg['base_url']}{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        data=body,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw else {}


def fetch_nowplaying() -> dict | None:
    cfg = cs.azura_api_cfg()
    if not cfg:
        return None
    try:
        data = _api_request(f"/api/nowplaying/{cfg['station_id']}")
        return data if isinstance(data, dict) else None
    except Exception as exc:
        print(f"[RADIO_SKELETON] event=azura_nowplaying_failed error={exc!r}")
        return None


def test_api() -> dict:
    try:
        data = fetch_nowplaying()
        if data:
            return {"ok": True, "error": ""}
        return {"ok": False, "error": "no_nowplaying_response"}
    except Exception as exc:
        return {"ok": False, "error": repr(exc)}


def test_sftp() -> dict:
    missing = cs.sftp_missing_vars()
    if missing:
        return {"ok": False, "error": f"missing:{','.join(missing)}"}
    cfg = cs.sftp_cfg()
    try:
        with socket.create_connection((cfg["host"], int(cfg["port"])), timeout=5):
            return {"ok": True, "error": ""}
    except Exception as exc:
        return {"ok": False, "error": repr(exc)}


def safe_request_filename(filename: str) -> bool:
    name = os.path.basename(str(filename or ""))
    return (
        bool(name)
        and name == filename
        and not any(part in name for part in ("/", "\\", ".."))
        and name.startswith(SAFE_PREFIXES)
    )


def upload_request_file(local_path: str, remote_filename: str) -> bool:
    if not safe_request_filename(remote_filename):
        print(f"[RADIO_PHASE4] event=upload_refused unsafe_filename={remote_filename!r}")
        return False
    if not os.path.exists(local_path):
        print(f"[RADIO_PHASE4] event=upload_missing local_path={local_path!r}")
        return False
    try:
        import paramiko
    except Exception as exc:
        print(f"[RADIO_PHASE4] event=upload_failed reason=paramiko_missing error={exc!r}")
        return False
    cfg = cs.sftp_cfg()
    if not cfg.get("host") or not cfg.get("user"):
        return False
    remote_path = f"{cfg['folder'].rstrip('/')}/{remote_filename}"
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sftp = None
    try:
        ssh.connect(
            hostname=cfg["host"],
            port=int(cfg["port"]),
            username=cfg["user"],
            password=cfg["passwd"],
            timeout=30,
            look_for_keys=False,
            allow_agent=False,
        )
        sftp = ssh.open_sftp()
        try:
            sftp.stat(cfg["folder"].rstrip("/"))
        except IOError:
            sftp.mkdir(cfg["folder"].rstrip("/"))
        sftp.put(local_path, remote_path)
        print(f"[RADIO_PHASE4] event=upload_request_file remote={remote_path!r}")
        return True
    except Exception as exc:
        print(f"[RADIO_PHASE4] event=upload_failed error={exc!r}")
        return False
    finally:
        try:
            if sftp:
                sftp.close()
        finally:
            ssh.close()


def rescan_requests_folder() -> bool:
    folder = os.path.basename(cs.sftp_cfg().get("folder", "Requests").rstrip("/")) or "Requests"
    try:
        _api_request(
            f"/api/station/{cs.azura_api_cfg()['station_id']}/files/batch",
            method="POST",
            payload={"do": "rescan", "currentDirectory": folder},
            timeout=30,
        )
        return True
    except Exception as exc:
        print(f"[RADIO_PHASE4] event=rescan_failed error={exc!r}")
        return False


def find_uploaded_media(remote_filename: str) -> dict | None:
    if not safe_request_filename(remote_filename):
        return None
    cfg = cs.azura_api_cfg()
    if not cfg:
        return None
    try:
        phrase = urllib.parse.quote(remote_filename)
        data = _api_request(f"/api/station/{cfg['station_id']}/files?searchPhrase={phrase}", timeout=15)
        rows = data if isinstance(data, list) else (data or {}).get("rows", [])
        for row in rows:
            path = str(row.get("path") or "")
            if os.path.basename(path) == remote_filename:
                return row
    except Exception as exc:
        print(f"[RADIO_PHASE4] event=find_uploaded_media_failed error={exc!r}")
    return None


def attach_requests_playlist(file_id: str) -> bool:
    playlist_id = cs.requests_playlist_id()
    cfg = cs.azura_api_cfg()
    if not cfg or not file_id or not playlist_id:
        return True
    try:
        _api_request(
            f"/api/station/{cfg['station_id']}/file/{file_id}",
            method="PUT",
            payload={"playlists": [int(playlist_id) if str(playlist_id).isdigit() else playlist_id]},
            timeout=15,
        )
        return True
    except Exception as exc:
        print(f"[RADIO_PHASE4] event=attach_playlist_failed file_id={file_id!r} error={exc!r}")
        return False


def submit_request(azura_song_id: str) -> bool:
    cfg = cs.azura_api_cfg()
    if not cfg or not azura_song_id:
        return False
    try:
        _api_request(
            f"/api/station/{cfg['station_id']}/request/{urllib.parse.quote(str(azura_song_id))}",
            method="POST",
            timeout=15,
        )
        return True
    except urllib.error.HTTPError as exc:
        print(f"[RADIO_PHASE4] event=submit_request_failed status={exc.code} error={exc!r}")
    except Exception as exc:
        print(f"[RADIO_PHASE4] event=submit_request_failed error={exc!r}")
    return False


def clear_file_playlists(azura_file_id: str) -> bool:
    cfg = cs.azura_api_cfg()
    if not cfg or not azura_file_id:
        return False
    try:
        _api_request(
            f"/api/station/{cfg['station_id']}/file/{azura_file_id}",
            method="PUT",
            payload={"playlists": []},
            timeout=15,
        )
        return True
    except Exception as exc:
        print(f"[RADIO_PHASE4] event=clear_playlists_failed file_id={azura_file_id!r} error={exc!r}")
    return False


def delete_request_file(remote_filename: str = "", azura_file_id: str = "") -> bool:
    ok = False
    cfg = cs.azura_api_cfg()
    if azura_file_id and cfg:
        try:
            _api_request(
                f"/api/station/{cfg['station_id']}/file/{azura_file_id}",
                method="DELETE",
                timeout=15,
            )
            ok = True
        except urllib.error.HTTPError as exc:
            ok = exc.code == 404
        except Exception as exc:
            print(f"[RADIO_PHASE4] event=delete_media_failed file_id={azura_file_id!r} error={exc!r}")
    if remote_filename and safe_request_filename(remote_filename):
        ok = _sftp_delete(remote_filename) or ok
    return ok


def verify_request_file_gone(remote_filename: str) -> bool:
    if not remote_filename or not safe_request_filename(remote_filename):
        return True
    return find_uploaded_media(remote_filename) is None


def _sftp_delete(remote_filename: str) -> bool:
    try:
        import paramiko
    except Exception:
        return False
    cfg = cs.sftp_cfg()
    if not cfg.get("host") or not cfg.get("user"):
        return False
    remote_path = f"{cfg['folder'].rstrip('/')}/{remote_filename}"
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sftp = None
    try:
        ssh.connect(
            hostname=cfg["host"],
            port=int(cfg["port"]),
            username=cfg["user"],
            password=cfg["passwd"],
            timeout=30,
            look_for_keys=False,
            allow_agent=False,
        )
        sftp = ssh.open_sftp()
        try:
            sftp.remove(remote_path)
        except IOError as exc:
            if "No such file" not in str(exc):
                raise
        return True
    except Exception as exc:
        print(f"[RADIO_PHASE4] event=sftp_delete_failed filename={remote_filename!r} error={exc!r}")
        return False
    finally:
        try:
            if sftp:
                sftp.close()
        finally:
            ssh.close()


def _song_dict(np_data: dict) -> dict:
    song = np_data.get("now_playing") or {}
    if isinstance(song, dict):
        inner = song.get("song")
        if isinstance(inner, dict):
            return inner
    return {}


def extract_nowplaying_track(np_data: dict | None) -> dict | None:
    if not isinstance(np_data, dict):
        return None
    now_playing = np_data.get("now_playing") or {}
    song = _song_dict(np_data)
    media = {}
    if isinstance(now_playing, dict):
        media = now_playing.get("media") or now_playing.get("song_media") or {}
        if not isinstance(media, dict):
            media = {}
    station = np_data.get("station") if isinstance(np_data.get("station"), dict) else {}
    elapsed = now_playing.get("elapsed") if isinstance(now_playing, dict) else None
    duration = now_playing.get("duration") if isinstance(now_playing, dict) else None
    raw_title = song.get("title") or song.get("text") or ""
    artist = song.get("artist") or ""
    title = raw_title
    if not artist and " - " in raw_title:
        maybe_artist, maybe_title = raw_title.split(" - ", 1)
        artist, title = maybe_artist.strip(), maybe_title.strip()
    track = {
        "title": title or "Unknown Track",
        "artist": artist or "Unknown Artist",
        "elapsed": _int_or_none(elapsed),
        "duration": _int_or_none(duration or song.get("duration")),
        "song_id": song.get("id") or song.get("song_id") or "",
        "unique_id": song.get("unique_id") or "",
        "media_id": media.get("id") or song.get("media_id") or "",
        "path": media.get("path") or song.get("path") or "",
        "art": song.get("art") or station.get("art") or "",
        "raw": song,
    }
    track["filename"] = str(track["path"] or "").rsplit("/", 1)[-1]
    track["track_key"] = track_key(track)
    return track


def track_key(track: dict[str, Any]) -> str:
    for key in ("unique_id", "song_id", "media_id", "path"):
        value = str(track.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    return f"title:{track.get('title','').strip().lower()}|artist:{track.get('artist','').strip().lower()}"


def _int_or_none(value) -> int | None:
    try:
        if value is None or value == "":
            return None
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None
