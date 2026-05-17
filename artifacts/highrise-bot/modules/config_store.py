"""
modules/config_store.py
-----------------------
Single source of truth for every radio / request system setting.

All DB-backed settings use the "radio_" namespace in room_settings.
All env-var reads for AzuraCast / SFTP live here — no other module
should call os.environ directly for radio configuration.
"""
from __future__ import annotations
import os
import database as db

_NS = "radio_"


def _get(key: str, default: str) -> str:
    return (db.get_room_setting(_NS + key, default) or default).strip()


def _set(key: str, value: str) -> None:
    db.set_room_setting(_NS + key, value)


# ─── Request price ────────────────────────────────────────────────────────────

def request_price() -> int:
    """Chill Coins charged per request (0 = free for all). Default 500."""
    try:
        return max(0, int(_get("request_price", "500")))
    except Exception:
        return 500


def set_request_price(price: int) -> None:
    _set("request_price", str(max(0, int(price))))


# ─── Vibe ─────────────────────────────────────────────────────────────────────

def vibe() -> str:
    """Current room vibe: 'chill' or 'party'. Default 'chill'."""
    v = _get("vibe", "chill").lower()
    return v if v in ("chill", "party") else "chill"


def set_vibe(v: str) -> None:
    _set("vibe", v.lower())


# ─── Vote skip ────────────────────────────────────────────────────────────────

def voteskip_threshold() -> int:
    """Number of unique votes required to skip. Min 2, default 3."""
    try:
        return max(2, int(_get("voteskip_threshold", "3")))
    except Exception:
        return 3


def set_voteskip_threshold(n: int) -> None:
    _set("voteskip_threshold", str(max(2, int(n))))


# ─── System enabled ───────────────────────────────────────────────────────────

def request_system_enabled() -> bool:
    return _get("requests_enabled", "true").lower() in ("1", "true", "yes")


def set_request_system_enabled(b: bool) -> None:
    _set("requests_enabled", "true" if b else "false")


# ─── Cooldown ─────────────────────────────────────────────────────────────────

def cooldown_secs() -> int:
    try:
        return max(30, int(_get("request_cooldown", "300")))
    except Exception:
        return 300


def set_cooldown_secs(n: int) -> None:
    _set("request_cooldown", str(max(30, int(n))))


# ─── Leave / refund behaviour ─────────────────────────────────────────────────

def skip_if_requester_leaves() -> bool:
    return _get("skip_on_leave", "true").lower() in ("1", "true", "yes")


def refund_if_leaves() -> bool:
    return _get("refund_on_leave", "true").lower() in ("1", "true", "yes")


def admin_requests_ignore_leave() -> bool:
    return _get("admin_ignore_leave", "true").lower() in ("1", "true", "yes")


# ─── Hard limits (env-var overridable, not runtime-settable) ──────────────────

MAX_DURATION_SECS: int = int(os.environ.get("REQUEST_MAX_DURATION", "600") or "600")
DEDUP_WINDOW_SECS: int = 86400
MAX_ACTIVE_JOBS:   int = int(os.environ.get("REQUEST_MAX_QUEUE", "5") or "5")


# ─── Auto-behaviours ──────────────────────────────────────────────────────────

def auto_skip_on_request() -> bool:
    """Skip current song the moment a new request is uploaded. Default True."""
    return os.environ.get("AZURA_AUTO_SKIP_ON_REQUEST", "true").strip().lower() not in (
        "0", "false", "no"
    )


def auto_delete_after_play() -> bool:
    return os.environ.get("REQUEST_AUTO_DELETE_AFTER_PLAY", "true").strip().lower() not in (
        "0", "false", "no"
    )


# ─── AzuraCast REST API ───────────────────────────────────────────────────────

def azura_api_cfg() -> "dict | None":
    base = (os.environ.get("AZURA_BASE_URL") or "").rstrip("/").strip()
    key  = (os.environ.get("AZURA_API_KEY")  or "").strip()
    if not base or not key:
        return None
    return {
        "base_url":   base,
        "api_key":    key,
        "station_id": (os.environ.get("AZURA_STATION_ID") or "1").strip(),
        "media_dir":  (os.environ.get("AZURA_MEDIA_DIR")  or "").strip(),
    }


def azura_api_ready() -> bool:
    return azura_api_cfg() is not None


# ─── Playlist IDs ─────────────────────────────────────────────────────────────

def chill_playlist_id() -> str:
    return (os.environ.get("AZURA_PLAYLIST_CHILL_ID") or "").strip()


def party_playlist_id() -> str:
    return (os.environ.get("AZURA_PLAYLIST_PARTY_ID") or "").strip()


def requests_playlist_id() -> str:
    """The priority 'Requests' playlist that uploaded songs are added to."""
    return (os.environ.get("AZURA_PLAYLIST_ID") or "").strip()


# ─── SFTP ─────────────────────────────────────────────────────────────────────

_SFTP_REQUIRED    = ("AZURA_SFTP_HOST", "AZURA_SFTP_USER", "AZURA_SFTP_PASS")
_DEFAULT_SFTP_DIR = "Requests"


def sftp_cfg() -> dict:
    folder = (os.environ.get("AZURA_SFTP_PATH") or _DEFAULT_SFTP_DIR).strip()
    return {
        "host":   (os.environ.get("AZURA_SFTP_HOST") or "").strip(),
        "port":   int((os.environ.get("AZURA_SFTP_PORT") or "22").strip() or "22"),
        "user":   (os.environ.get("AZURA_SFTP_USER") or "").strip(),
        "passwd": (os.environ.get("AZURA_SFTP_PASS") or "").strip(),
        "folder": folder,
    }


def sftp_missing_vars() -> list:
    return [v for v in _SFTP_REQUIRED if not (os.environ.get(v) or "").strip()]


def sftp_ready() -> bool:
    return not sftp_missing_vars()
