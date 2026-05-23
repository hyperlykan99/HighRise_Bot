"""
modules/radio_renderer.py
--------------------------
Canonical rendering helpers for the DJ_DUDU radio system.

ALL display formatting — now-playing cards, progress bars, leaderboards,
history pages, and song list lines — must be sourced from here.
No other radio module should define its own _fmt_secs, _progress_bar,
or now-playing card builder.

Public API
──────────
  _fmt_secs(secs)                    → "M:SS" string
  _progress_bar(elapsed, total)      → ▰▱ bar string
  _prettify(raw)                     → readable title from song_key
  _trunc(s, maxlen)                  → truncate with ellipsis
  render_now_playing(track)          → canonical !np / announcement card (≤249)
  render_history_pages(history)      → list[str] of paginated history messages
  render_user_leaderboard(...)       → single ≤249-char leaderboard string
  render_song_line(i, row, label)    → single song row (topliked/topdisliked)
  render_song_pages(header, rows, label, per_page) → list[str] of song pages
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

_LOG = "[DJ_RENDER]"


# ─── Time / progress helpers (single canonical source) ────────────────────────

def _fmt_secs(secs: int) -> str:
    """Format integer seconds as M:SS. Never raises."""
    m, s = divmod(max(0, int(secs)), 60)
    return f"{m}:{s:02d}"


def _progress_bar(elapsed: int, total: int, cells: int = 10) -> str:
    """10-cell Unicode progress bar using ▰/▱."""
    if total <= 0:
        return "▱" * cells
    filled = round(cells * min(elapsed, total) / total)
    return "▰" * filled + "▱" * (cells - filled)


# ─── Title / string helpers ───────────────────────────────────────────────────

def _prettify(raw: str) -> str:
    """Lower-case song_key → readable title (hyphens/underscores → spaces, title-case)."""
    text = re.sub(r"[-_]", " ", raw or "")
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text.title() if text else ""


def _trunc(s: str, maxlen: int) -> str:
    """Truncate string with ellipsis if it exceeds maxlen."""
    s = s.strip()
    return s if len(s) <= maxlen else s[: maxlen - 1] + "…"


# ─── Now-playing card ─────────────────────────────────────────────────────────

def render_now_playing(track: dict, *, station: str = "") -> str:
    """
    Canonical renderer for ALL now-playing displays (room announcements + !np).

    Source-aware compact format — no blank lines, ≤249 chars.

    AutoDJ/vibe:
      🎵 NOW PLAYING
      Title: <title>
      🎤 Artist: <artist or 'Unknown Artist'>
      Source: Auto DJ
      0:31 ▰▰▱▱▱▱▱▱▱▱ 3:04
      👍 12 | 👎 2

    Live request:
      🎵 NOW PLAYING
      Title: <title>
      🎤 Artist: <artist or 'Unknown Artist'>
      Requested by: @user
      Source: Request
      0:31 ▰▰▱▱▱▱▱▱▱▱ 3:04
      👍 12 | 👎 2

    Falls back to "0:00 ▱▱▱▱▱▱▱▱▱▱ ?:??" when duration is unknown.
    Returns a UTF-8 string ≤249 chars.
    """
    title    = (track.get("title")  or "Unknown")[:34]
    artist   = (track.get("artist") or "").strip()[:24]
    likes    = int(track.get("likes",    0))
    dislikes = int(track.get("dislikes", 0))
    source   = track.get("source", "autodj")
    requester = (track.get("requester") or "").strip()[:18]
    elapsed  = int(track.get("elapsed",  0) or 0)
    duration = int(track.get("duration", 0) or 0)

    artist_line = f"Artist: {artist}" if artist else "Artist: Unknown Artist"

    if duration > 0:
        bar_line = (
            f"{_fmt_secs(elapsed)} {_progress_bar(elapsed, duration)}"
            f" {_fmt_secs(duration)}"
        )
    else:
        bar_line = f"0:00 {'▱' * 10} ?:??"

    if source == "request":
        lines = [
            "🎵 NOW PLAYING",
            f"Title: {title}",
            artist_line,
            f"Requested by: @{requester}" if requester else "Requested by: @unknown",
            "Source: Request",
            bar_line,
            f"👍 {likes} | 👎 {dislikes}",
        ]
    else:
        lines = [
            "🎵 NOW PLAYING",
            f"Title: {title}",
            artist_line,
            "Source: Auto DJ",
            bar_line,
            f"👍 {likes} | 👎 {dislikes}",
        ]

    return "\n".join(lines)[:249]


# ─── History page renderer ────────────────────────────────────────────────────

def render_history_pages(history: list, chunk_size: int = 4) -> "list[str]":
    """
    Convert a list of request-history dicts into paginated whisper strings.

    Each entry must have 'title' and 'username' keys.
    Returns a list of ready-to-send strings (one per page), each ≤249 chars.
    \\u200b after @ prevents Highrise turning @username into a clickable mention.
    """
    items: list[str] = []
    for i, row in enumerate(history, 1):
        t = (row.get("title")    or "?")[:32]
        u = (row.get("username") or "?")[:14]
        items.append(f"{i}. 🎧 {t} — @\u200b{u}")

    chunks      = [items[k : k + chunk_size] for k in range(0, len(items), chunk_size)]
    total_pages = len(chunks)
    pages: list[str] = []
    for pg, chunk in enumerate(chunks, 1):
        header = (
            f"📜 History {pg}/{total_pages}" if total_pages > 1 else "📜 Recent Requests"
        )
        pages.append((header + "\n" + "\n".join(chunk))[:249])
    return pages


# ─── User leaderboard renderer ────────────────────────────────────────────────

def render_user_leaderboard(
    rows: list,
    title: str,
    metric_key: str,
    metric_suffix: str,
    name_key: str = "username",
    limit: int = 5,
) -> str:
    """
    Build a compact ≤249-char leaderboard string for user-based rankings.

    Example output:
      🎧 Top Listeners
      1. @alice — 250 pts
      2. @bob — 180 pts

    Args:
        rows:          List of row dicts from DB.
        title:         Header line (e.g. "🎧 Top Listeners").
        metric_key:    Dict key for the numeric value (e.g. "points", "count").
        metric_suffix: Short label after the number (e.g. "pts", "requests").
        name_key:      Dict key for the display name (default "username").
        limit:         Max rows to render (default 5).
    """
    lines = [title]
    for i, r in enumerate(rows[:limit], 1):
        try:
            name = str(r.get(name_key) or "unknown")[:18]
            val  = int(r.get(metric_key) or 0)
            lines.append(f"{i}. @{name} — {val} {metric_suffix}")
        except Exception:
            lines.append(f"{i}. (data error)")
    return "\n".join(lines)[:249]


# ─── Song leaderboard helpers ─────────────────────────────────────────────────

def render_song_line(i: int, row: dict, label: str) -> str:
    """
    Format one song leaderboard row — fully defensive, ≤72 chars.

    Row must have 'title' (or 'song_key'), optional 'artist', and 'count'.
    label: plural noun for the metric (e.g. "likes", "dislikes", "plays").
    """
    try:
        raw_title  = str(row.get("title") or row.get("song_key") or "")
        raw_artist = str(row.get("artist") or "")
        n          = int(row.get("count") or 0)

        title  = _prettify(raw_title) or "Unknown Title"
        title  = _trunc(title, 28)
        artist = _prettify(raw_artist) or "Unknown"
        artist = _trunc(artist, 18)

        # singular vs plural  (1 like → "like", 2 likes → "likes")
        word = label[:-1] if (n == 1 and label.endswith("s")) else label
        return f"{i}. {title} — {artist} • {n} {word}"
    except Exception:
        return f"{i}. (data error)"


def render_song_pages(
    header: str,
    rows: list,
    label: str,
    per_page: int = 3,
) -> "list[str]":
    """
    Paginate song leaderboard rows into ready-to-send strings (≤249 chars each).

    header:   Page title (e.g. "👍 Top Liked Songs").
    rows:     List of song dicts (see render_song_line).
    label:    Metric label (e.g. "likes", "dislikes").
    per_page: Rows per page (default 3).

    Returns a list of strings, one per page.
    """
    items: list[str] = [render_song_line(i, row, label) for i, row in enumerate(rows, 1)]

    chunks      = [items[k : k + per_page] for k in range(0, len(items), per_page)]
    total_pages = len(chunks)
    pages: list[str] = []
    for pg, chunk in enumerate(chunks, 1):
        hdr  = f"{header} {pg}/{total_pages}" if total_pages > 1 else header
        pages.append(f"{hdr}\n{chr(10).join(chunk)}"[:249])
    return pages
