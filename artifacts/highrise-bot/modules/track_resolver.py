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

Debug output:
  [NOW_RESOLVE] title=… media_id=… path=… matched_request=… source=… vibe=…
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

import database as db

if TYPE_CHECKING:
    pass


# ─── Ratings helper (mirrors radio_commands._ratings, avoids circular import) ──

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


# ─── Vibe resolution ──────────────────────────────────────────────────────────

def _resolve_vibe(active_vibe: str, playlist_name: str) -> str:
    """
    Resolve the current vibe label.

    Order (per spec):
      1. Active vibe explicitly set by !vibe  (config_store.vibe())
      2. AzuraCast playlist name              (from NP API, skip 'Requests')
      3. Fallback "AutoDJ"

    Never returns empty string.
    """
    v = (active_vibe or "").strip()
    if v and v.lower() not in ("", "autodj"):
        return v

    # Step 2: AzuraCast NP playlist name
    p = (playlist_name or "").strip()
    if p and p.lower() not in ("requests", ""):
        return p

    return "AutoDJ"


# ─── Core resolver ────────────────────────────────────────────────────────────

def resolve_current_track(np_data: "dict | None" = None) -> dict:
    """
    Determine the current playback state and return a fully-populated dict.

    Fields:
      title, artist, duration, elapsed,
      media_id, song_id, unique_id, path, filename, playlist,
      source ('request' | 'autodj'),
      requester (str | None), request_id (int | None),
      vibe (str),
      started_at (float epoch),
      likes (int), dislikes (int)

    Request detection order (mirroring _db_match_request):
      1. media_id     — azura_file_id match
      2. song_id      — AzuraCast song.id / unique_id
      3. path starts with Requests/
      4. filename match
      5. youtube_id in path
      6. normalised title+artist substring
      7. fuzzy title fallback
    All strategies are handled by the existing engine.get_live_request() →
    engine.match_and_recover() chain, which wraps _db_match_request.

    Logs:
      [NOW_RESOLVE] title= media_id= path= matched_request= source= vibe=
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
    duration = int(np_obj.get("duration") or song.get("length") or 0)
    elapsed  = int(np_obj.get("elapsed")  or 0)
    song_id  = (song.get("id")        or "").strip()
    song_uid = (song.get("unique_id") or "").strip()
    media_id = str(media.get("id")    or "").strip()
    path     = (media.get("path")     or "").strip()
    filename = path.rsplit("/", 1)[-1] if path else ""
    playlist = (np_obj.get("playlist") or "").strip()

    # ── 2. Try in-memory live request cache ───────────────────────────────────
    cp = engine.get_live_request()

    # ── 3. Self-correct: multi-strategy DB match if cache is empty ───────────
    if cp is None:
        cp = engine.match_and_recover(
            song_id    = song_id,
            song_uid   = song_uid,
            np_title   = title,
            media_id   = media_id,
            media_path = path,
            np_artist  = artist,
        )

    # ── 4. Build track dict ───────────────────────────────────────────────────
    if cp:
        req_title  = (cp.get("title")    or "").strip() or title
        req_artist = (cp.get("artist")   or artist or "").strip()
        req_uname  = (cp.get("username") or "").strip()
        job_id     = cp.get("job_id") or cp.get("id")
        started    = float(cp.get("started_at") or 0.0)
        song_key   = req_title.lower()[:150]
        counts     = _get_ratings(song_key)

        print(
            f"[NOW_RESOLVE] title={req_title!r}"
            f" media_id={media_id!r}"
            f" path={path!r}"
            f" matched_request=True"
            f" source=request"
            f" vibe=n/a"
        )
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
        import modules.config_store as cs  # re-import to avoid name shadowing
        vibe     = _resolve_vibe(cs.vibe(), playlist)
        song_key = title.lower()[:150] if title != "Unknown" else ""
        counts   = _get_ratings(song_key)
        started  = (time.time() - elapsed) if elapsed > 0 else 0.0

        print(
            f"[NOW_RESOLVE] title={title!r}"
            f" media_id={media_id!r}"
            f" path={path!r}"
            f" matched_request=False"
            f" source=autodj"
            f" vibe={vibe!r}"
        )
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


# ─── Single renderer ──────────────────────────────────────────────────────────

def render_now_playing(track: dict, *, station: str = "ChillTopia Radio") -> str:
    """
    Canonical renderer for all now-playing displays.
    Returns a UTF-8 string of ≤249 chars.

    source == 'request':
      ▶ REQUEST LIVE
      Title: {title}
      Artist: {artist}          ← omitted if empty
      👤 @{requester}           ← '👤 Requested' if requester unknown
      👍 {likes} 👎 {dislikes}
      📻 {station}

    source == 'autodj':
      ▶ NOW PLAYING
      Title: {title}
      Artist: {artist}          ← omitted if empty
      {vibe_line}               ← "🌙 AutoDJ • Chill" etc.
      👍 {likes} 👎 {dislikes}
      📻 {station}
    """
    from modules.dj_announcer import _VIBE_LINE

    title    = (track.get("title")  or "Unknown")[:42]
    artist   = (track.get("artist") or "").strip()[:38]
    likes    = int(track.get("likes",    0))
    dislikes = int(track.get("dislikes", 0))
    source   = track.get("source", "autodj")

    if source == "request":
        requester = (track.get("requester") or "")[:20]
        lines = ["▶ REQUEST LIVE", f"Title: {title}"]
        if artist:
            lines.append(f"Artist: {artist}")
        lines.append(f"👤 @{requester}" if requester else "👤 Requested")
        lines += [f"👍 {likes} 👎 {dislikes}", f"📻 {station}"]
    else:
        vibe = (track.get("vibe") or "AutoDJ").strip()
        if not vibe or vibe.lower() == "autodj":
            vibe_line = "🌙 AutoDJ"
        else:
            # Never fall back to "Chill" — always use the actual vibe name
            vibe_line = _VIBE_LINE.get(vibe, f"🌙 AutoDJ • {vibe.title()}")
        lines = ["▶ NOW PLAYING", f"Title: {title}"]
        if artist:
            lines.append(f"Artist: {artist}")
        lines += [vibe_line, f"👍 {likes} 👎 {dislikes}", f"📻 {station}"]

    return "\n".join(lines)[:249]
