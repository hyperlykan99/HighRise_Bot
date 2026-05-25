"""
modules/config_store.py
-----------------------
Single source of truth for every radio / request system setting.

All DB-backed settings use the "radio_" namespace in room_settings.
All env-var reads for AzuraCast / SFTP live here — no other module
should call os.environ directly for radio configuration.
"""
from __future__ import annotations
import json as _json
import os
import time as _time
import database as db
from modules import dashboard_settings as _dash

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

VIBE_NAMES = (
    "chill", "party", "afrobeats", "edm", "house",
    "kpop", "opm", "lofi", "rnb", "hiphop", "nightdrive", "phonk",
)


def vibe() -> str:
    """Current room vibe. Default 'chill'. Allows dynamic (folder-based) vibe names."""
    v = (_get("vibe", "chill") or "chill").lower().strip()
    return v if v else "chill"


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
    if not _dash.dashboard_gate_open("radio", "requests_enabled"):
        return False
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
MAX_ACTIVE_JOBS:   int = int(os.environ.get("REQUEST_MAX_QUEUE", "20") or "20")
MAX_PER_USER_JOBS: int = int(os.environ.get("REQUEST_MAX_PER_USER", "3") or "3")


# ─── Queue limits (DB-backed, admin-editable) ─────────────────────────────────

def max_active_queue_limit() -> int:
    """Max active room-wide request workload. Default 20, clamped 1..50."""
    try:
        return min(50, max(1, int(_get("max_active_queue", str(MAX_ACTIVE_JOBS)))))
    except Exception:
        return 20


def set_max_active_queue_limit(n: int) -> int:
    val = min(50, max(1, int(n)))
    _set("max_active_queue", str(val))
    return val


def per_user_queue_limit() -> int:
    """Max pending songs per player (0 = unlimited). Default from env/3."""
    try:
        return max(0, int(_get("per_user_queue_limit", str(MAX_PER_USER_JOBS))))
    except Exception:
        return MAX_PER_USER_JOBS


def set_per_user_queue_limit(n: int) -> None:
    _set("per_user_queue_limit", str(max(0, int(n))))


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

_VIBE_ENV: "dict[str, str]" = {
    "chill":      "AZURA_PLAYLIST_CHILL_ID",
    "party":      "AZURA_PLAYLIST_PARTY_ID",
    "afrobeats":  "AZURA_PLAYLIST_AFROBEATS_ID",
    "edm":        "AZURA_PLAYLIST_EDM_ID",
    "house":      "AZURA_PLAYLIST_HOUSE_ID",
    "kpop":       "AZURA_PLAYLIST_KPOP_ID",
    "opm":        "AZURA_PLAYLIST_OPM_ID",
    "lofi":       "AZURA_PLAYLIST_LOFI_ID",
    "rnb":        "AZURA_PLAYLIST_RNB_ID",
    "hiphop":     "AZURA_PLAYLIST_HIPHOP_ID",
    "nightdrive": "AZURA_PLAYLIST_NIGHTDRIVE_ID",
}


def vibe_playlist_id(vibe_name: str) -> str:
    """Return the AzuraCast playlist ID for the given vibe name (read from env)."""
    env_key = _VIBE_ENV.get(vibe_name.lower(), "")
    return (os.environ.get(env_key) or "").strip() if env_key else ""


def chill_playlist_id() -> str:
    return vibe_playlist_id("chill")


def party_playlist_id() -> str:
    return vibe_playlist_id("party")


def requests_playlist_id() -> str:
    """The priority 'Requests' playlist that uploaded songs are added to."""
    return (os.environ.get("AZURA_PLAYLIST_ID") or "").strip()


# ─── Dynamic vibe playlist IDs (DB-backed, for folder-discovered vibes) ────────

def set_dynamic_vibe_playlist(vibe_name: str, playlist_id: str) -> None:
    """Store a discovered AzuraCast playlist ID for a folder-based vibe."""
    _set(f"dynamic_pl_{vibe_name.lower()}", playlist_id)


def get_dynamic_vibe_playlist(vibe_name: str) -> str:
    """Return a previously stored dynamic playlist ID, or '' if not set."""
    return _get(f"dynamic_pl_{vibe_name.lower()}", "")


# ─── Vibe discovery cache (auto-scan results) ─────────────────────────────────

_VIBE_SCAN_TTL = 3600.0  # seconds; !vibescan forces an immediate refresh


def get_vibe_scan_cache() -> dict:
    """Load the vibe discovery cache from DB. Returns {} if missing or corrupt."""
    raw = _get("vibe_scan_cache", "")
    if not raw:
        return {}
    try:
        return _json.loads(raw)
    except Exception:
        return {}


def set_vibe_scan_cache(data: dict) -> None:
    """Persist a vibe scan result dict to DB."""
    try:
        _set("vibe_scan_cache", _json.dumps(data))
    except Exception:
        pass


def clear_vibe_scan_cache() -> None:
    """Invalidate the cache so the next !vibe / !vibes triggers a fresh scan."""
    _set("vibe_scan_cache", "")


def vibe_scan_is_fresh() -> bool:
    """Return True if the cache exists and is younger than _VIBE_SCAN_TTL seconds."""
    cache = get_vibe_scan_cache()
    if not cache.get("vibes"):
        return False
    age = _time.time() - cache.get("scanned_at", 0)
    return age < _VIBE_SCAN_TTL


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
