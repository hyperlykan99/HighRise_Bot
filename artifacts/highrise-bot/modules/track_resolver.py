"""
modules/track_resolver.py
--------------------------
SINGLE source of truth for current radio playback state.

Two public functions that every caller must use:

  resolve_current_track(np_data=None) -> dict
      Determines what is currently playing and whether it is a user request
      or AutoDJ.  Accepts pre-fetched AzuraCast NP data so callers that
      already have it avoid a redundant HTTP fetch.

  render_now_playing(track) -> str
      Formats the track dict into the canonical ≤249-char display string.
      ALL systems (announcer, !now, overlays, queue) must use this — never
      build their own rendering logic.

Resolution order for request detection:
  1. engine.get_live_request()     — in-memory _live_req (fastest, set by poller)
  2. engine.match_and_recover()    — 7-strategy DB match against NP identifiers
  3. Direct DB playing sweep       — any job with status='playing' (race-condition guard)
  4. Requests/ path safety net     — if NP path starts with Requests/, it IS a request;
                                     try filename DB lookup, fall back to "Requested"

Debug logs:
  [NOW_RESOLVE] step=…  result=…  (per step)
  [NOW_RESOLVE] title=… media_id=… path=… matched_request=… source=… vibe=…
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

import database as db

if TYPE_CHECKING:
    pass


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _get_ratings(song_key: str) -> dict:
    """Return {'likes': N, 'dislikes': N} for a song_key from dj_ratings."""
    if not song_key:
        return {"likes": 0, "dislikes": 0}
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT rating, COUNT(*) FROM dj_ratings WHERE song_key=? GROUP BY rating",
                (song_key,),
            ).fetchall()
        result: dict = {"likes": 0, "dislikes": 0}
        for r in rows:
            if r[0] == "like":
                result["likes"] = r[1]
            elif r[0] == "dislike":
                result["dislikes"] = r[1]
        return result
    except Exception:
        return {"likes": 0, "dislikes": 0}


def _db_playing_sweep() -> "dict | None":
    """
    Step 3 safety net: direct DB query for any job with status='playing'.
    This catches the case where engine._db_find_playing() succeeded but
    get_live_request() somehow returned None (cross-bot memory isolation,
    restart timing, etc.).
    Returns a minimal dict with keys: id, username, title, artist=''.
    """
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                "SELECT id, username, title "
                "FROM yt_request_jobs "
                "WHERE status='playing' AND played_at IS NULL "
                "ORDER BY id DESC LIMIT 1",
            ).fetchone()
        if row:
            return {"id": row[0], "username": row[1] or "", "title": row[2] or "", "artist": ""}
    except Exception as exc:
        print(f"[NOW_RESOLVE] step=db_playing_sweep error={exc!r}")
    return None


def _db_request_by_filename(filename: str, np_title: str) -> "dict | None":
    """
    Step 4 helper: find a request job matching filename or title.
    Looks at active + recently played rows (played_at within last 10 min) so
    we don't miss a request that was cleaned up slightly before this call.
    Returns a minimal dict with keys: id, username, title, artist=''.
    """
    if not filename and not np_title:
        return None
    try:
        fn = filename.lower()
        tl = np_title.lower()
        with db.db_conn() as conn:
            row = conn.execute(
                "SELECT id, username, title "
                "FROM yt_request_jobs "
                "WHERE ("
                "  (played_at IS NULL) OR "
                "  (played_at >= datetime('now', '-10 minutes'))"
                ") AND ("
                "  lower(filename)=? OR lower(filename) LIKE ? OR lower(title)=?"
                ") "
                "ORDER BY id DESC LIMIT 1",
                (fn, f"%{fn}%", tl),
            ).fetchone()
        if row:
            return {"id": row[0], "username": row[1] or "", "title": row[2] or "", "artist": ""}
    except Exception as exc:
        print(f"[NOW_RESOLVE] step=db_filename_lookup error={exc!r}")
    return None


def _resolve_vibe(active_vibe: str, playlist_name: str) -> str:
    """
    Vibe resolution order per spec:
      1. Active vibe from !vibe command (config_store.vibe())
      2. AzuraCast playlist name  (skip 'Requests' playlist)
      3. 'AutoDJ' fallback

    Never returns empty string.
    """
    v = (active_vibe or "").strip()
    if v and v.lower() not in ("", "autodj"):
        return v

    p = (playlist_name or "").strip()
    if p and p.lower() not in ("requests", ""):
        return p

    return "AutoDJ"


# ─── Core resolver ────────────────────────────────────────────────────────────

def resolve_current_track(np_data: "dict | None" = None) -> dict:
    """
    Determine the current playback state and return a fully-populated dict.

    Return keys:
      title, artist, duration, elapsed,
      media_id, song_id, unique_id, path, filename, playlist,
      source ('request' | 'autodj'),
      requester (str | None), request_id (int | None),
      vibe (str), started_at (float epoch),
      likes (int), dislikes (int)

    Detection order (request wins if ANY step matches):
      1. engine.get_live_request()  — in-memory _live_req
      2. engine.match_and_recover() — 7-strategy DB match vs NP identifiers
      3. Direct DB playing sweep    — any status='playing' job (cross-bot guard)
      4. Requests/ path safety net  — NP path is definitively a user request
    """
    import modules.azuracast_controller as azura
    import modules.config_store         as cs
    import modules.playback_engine      as engine

    # ── 1. Obtain NP data ─────────────────────────────────────────────────────
    if np_data is None:
        np_data = azura.fetch_nowplaying() or {}

    np_obj   = np_data.get("now_playing") or {}
    song     = np_obj.get("song")  or {}
    media    = np_obj.get("media") or {}

    title    = (song.get("title")     or "").strip() or "Unknown"
    artist   = (song.get("artist")    or "").strip()
    # duration: prefer np_obj["duration"] (int seconds from AzuraCast),
    # fall back to song["length"] which may be an "M:SS" string.
    _raw_dur  = np_obj.get("duration") or 0
    try:
        duration = int(_raw_dur)
    except (ValueError, TypeError):
        duration = 0
    if not duration:
        _raw_len = song.get("length") or ""
        if isinstance(_raw_len, str) and ":" in _raw_len:
            try:
                _p = _raw_len.split(":")
                duration = int(_p[0]) * 60 + int(_p[-1])
            except (ValueError, IndexError):
                duration = 0
        elif _raw_len:
            try:
                duration = int(_raw_len)
            except (ValueError, TypeError):
                duration = 0
    try:
        elapsed = int(np_obj.get("elapsed") or 0)
    except (ValueError, TypeError):
        elapsed = 0
    song_id  = (song.get("id")        or "").strip()
    song_uid = (song.get("unique_id") or "").strip()
    media_id = str(media.get("id")    or "").strip()
    path     = (media.get("path")     or "").strip()
    filename = path.rsplit("/", 1)[-1] if path else ""
    playlist = (np_obj.get("playlist") or "").strip()

    # ── Emit spec-required NP field logs ─────────────────────────────────────
    print(f"[NOW_RESOLVE] title={title!r}")
    print(f"[NOW_RESOLVE] media_id={media_id!r}")
    print(f"[NOW_RESOLVE] path={path!r}")

    # ── Step 1: in-memory live request cache ──────────────────────────────────
    cp = engine.get_live_request()
    print(f"[NOW_RESOLVE] step=get_live_request result={'found' if cp else 'none'}")

    # ── Step 2: multi-strategy DB match ───────────────────────────────────────
    if cp is None:
        cp = engine.match_and_recover(
            song_id    = song_id,
            song_uid   = song_uid,
            np_title   = title,
            media_id   = media_id,
            media_path = path,
            np_artist  = artist,
        )
        print(f"[NOW_RESOLVE] step=match_and_recover result={'found' if cp else 'none'}")

    # ── Step 3: direct DB playing sweep (cross-bot / race-condition guard) ────
    if cp is None:
        cp = _db_playing_sweep()
        print(f"[NOW_RESOLVE] step=db_playing_sweep result={'found' if cp else 'none'}")

    # ── Step 4: Requests/ path safety net ─────────────────────────────────────
    # AzuraCast serves user request files from the Requests/ folder.
    # If NP media path starts with Requests/, this IS a user request by
    # definition — show REQUEST LIVE even if DB match fails.
    if cp is None and path.lower().startswith("requests/"):
        cp = _db_request_by_filename(filename, title)
        if cp is None:
            # Path proves it's a request; requester unknown
            cp = {"id": None, "username": "", "title": title, "artist": artist}
        print(f"[NOW_RESOLVE] step=path_safety_net path={path!r}"
              f" result={'found' if cp else 'none'}")

    # ── Build and return track dict ───────────────────────────────────────────
    if cp:
        req_title  = (cp.get("title")    or "").strip() or title
        req_artist = (cp.get("artist")   or artist      or "").strip()
        req_uname  = (cp.get("username") or "").strip()
        job_id     = cp.get("job_id") or cp.get("id")
        started    = float(cp.get("started_at") or 0.0)
        song_key   = req_title.lower()[:150]
        counts     = _get_ratings(song_key)

        print(f"[NOW_RESOLVE] matched_request=True")
        print(f"[NOW_RESOLVE] source=request")
        print(f"[NOW_RESOLVE] vibe=n/a")
        return {
            "title":      req_title,
            "artist":     req_artist,
            "duration":   duration,
            "elapsed":    elapsed,
            "media_id":   media_id,
            "song_id":    song_id,
            "unique_id":  song_uid,
            "path":       path,
            "filename":   filename,
            "playlist":   playlist,
            "source":     "request",
            "requester":  req_uname or None,
            "request_id": job_id,
            "vibe":       "",
            "started_at": started,
            "likes":      counts["likes"],
            "dislikes":   counts["dislikes"],
        }
    else:
        vibe     = _resolve_vibe(cs.vibe(), playlist)
        song_key = title.lower()[:150] if title != "Unknown" else ""
        counts   = _get_ratings(song_key)
        started  = (time.time() - elapsed) if elapsed > 0 else 0.0

        print(f"[NOW_RESOLVE] matched_request=False")
        print(f"[NOW_RESOLVE] source=autodj")
        print(f"[NOW_RESOLVE] vibe={vibe!r}")
        return {
            "title":      title,
            "artist":     artist,
            "duration":   duration,
            "elapsed":    elapsed,
            "media_id":   media_id,
            "song_id":    song_id,
            "unique_id":  song_uid,
            "path":       path,
            "filename":   filename,
            "playlist":   playlist,
            "source":     "autodj",
            "requester":  None,
            "request_id": None,
            "vibe":       vibe,
            "started_at": started,
            "likes":      counts["likes"],
            "dislikes":   counts["dislikes"],
        }


# ─── Renderer helpers (canonical source: modules/radio_renderer) ──────────────
# Re-exported here for backward-compatibility with existing importers.
from modules.radio_renderer import _fmt_secs, _progress_bar, render_now_playing
