"""local_media_map.py — Local AzuraCast media library cache.

Commands (owner/admin only):
  !localmediascan     — full recursive library scan → local_media_map table
  !localmediastatus   — show indexed count + last scan time
  !localmediafind <q> — search map, show top 5 matches

Used internally by local_replay.py to match/backfill favorites when
azura_file_id is missing or stale.
"""

import os
import re
import sqlite3
from datetime import datetime

_LOG = "[LOCAL_MEDIA_MAP]"

# ── Normalization ─────────────────────────────────────────────────────────────

_STOP_WORDS = re.compile(
    r"\b(official|video|lyrics?|lyric|audio|hd|4k|hq|"
    r"music video|official video|official audio|"
    r"sped up|slowed|reverb|remix|ft\.?|feat\.?|featuring)\b",
    re.IGNORECASE,
)
_PUNCT  = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


def _normalize(text: str) -> str:
    text = text.lower()
    text = _STOP_WORDS.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    text = _SPACES.sub(" ", text).strip()
    return text


# ── DB helpers ────────────────────────────────────────────────────────────────

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS local_media_map (
    azura_file_id       TEXT PRIMARY KEY,
    unique_id           TEXT NOT NULL DEFAULT '',
    title               TEXT NOT NULL DEFAULT '',
    artist              TEXT NOT NULL DEFAULT '',
    path                TEXT NOT NULL DEFAULT '',
    filename            TEXT NOT NULL DEFAULT '',
    normalized_title    TEXT NOT NULL DEFAULT '',
    normalized_artist   TEXT NOT NULL DEFAULT '',
    normalized_filename TEXT NOT NULL DEFAULT '',
    updated_at          TEXT NOT NULL DEFAULT ''
)
"""


def _get_conn() -> sqlite3.Connection:
    from database import get_connection
    return get_connection()


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_TABLE)
    conn.commit()


# ── Favorites helpers (full meta — used instead of radio_commands._fav_get) ──

def _fav_get_full(user_id: str, limit: int = 20) -> list:
    """Like radio_commands._fav_get but also returns azura metadata columns."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, title, youtube_url, COALESCE(artist,'') AS artist, "
            "COALESCE(azura_file_id,'') AS azura_file_id, "
            "COALESCE(azura_song_id,'') AS azura_song_id, "
            "COALESCE(source_type,'')  AS source_type "
            "FROM dj_favorites WHERE user_id=? ORDER BY favorited_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [
            {
                "id":           r[0],
                "title":        r[1],
                "url":          r[2],
                "youtube_url":  r[2],
                "artist":       r[3],
                "azura_file_id": r[4],
                "azura_song_id": r[5],
                "source_type":  r[6],
            }
            for r in rows
        ]
    except Exception:
        return []
    finally:
        conn.close()


def _fav_backfill(fav_id: int, azura_file_id: str, unique_id: str) -> None:
    """Update a dj_favorites row with map-matched AzuraCast IDs."""
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE dj_favorites "
            "SET azura_file_id=?, azura_song_id=?, source_type='local' "
            "WHERE id=?",
            (azura_file_id, unique_id, fav_id),
        )
        conn.commit()
        print(f"{_LOG} backfilled fav id={fav_id} fid={azura_file_id!r}")
    except Exception as exc:
        print(f"{_LOG} backfill error: {exc!r}")
    finally:
        conn.close()


# ── !localmediascan ───────────────────────────────────────────────────────────

async def handle_localmediascan(bot, user, args):
    import asyncio

    async def _w(msg: str) -> None:
        try:
            await bot.highrise.send_whisper(user.id, msg[:249])
        except Exception:
            pass

    try:
        from modules.admin_cmds import is_owner, is_admin
        if not (is_owner(user.username) or is_admin(user.username)):
            await _w("❌ Owner/admin only.")
            return
    except Exception:
        pass

    await _w("🔍 Scanning AzuraCast library… This may take a moment.")

    loop = asyncio.get_running_loop()
    try:
        from modules.azuracast_controller import list_all_media
        rows = await loop.run_in_executor(None, list_all_media)
    except Exception as exc:
        print(f"{_LOG} scan error: {exc!r}")
        await _w(f"❌ Scan failed: {exc}")
        return

    if not rows:
        await _w("⚠️ No media rows returned. Check AzuraCast API config.")
        return

    conn = _get_conn()
    try:
        _ensure_table(conn)
        now      = datetime.utcnow().isoformat()
        inserted = 0
        updated  = 0

        for row in rows:
            # AzuraCast file records nest song metadata under a "media" or "song" dict
            media  = row.get("media") or row.get("song") or {}
            fid    = str(row.get("id") or row.get("file_id") or "").strip()
            uid    = str(row.get("unique_id") or media.get("song_id") or "").strip()
            path   = str(row.get("path") or "").strip()
            fname  = os.path.basename(path) if path else ""

            title  = str(
                media.get("title") or row.get("title") or
                fname.rsplit(".", 1)[0] or ""
            ).strip()
            artist = str(
                media.get("artist") or row.get("artist") or ""
            ).strip()

            if not fid and not path:
                continue

            pk             = fid or path
            norm_title     = _normalize(title)
            norm_artist    = _normalize(artist)
            norm_filename  = _normalize(fname.rsplit(".", 1)[0])

            existing = conn.execute(
                "SELECT 1 FROM local_media_map WHERE azura_file_id=?", (pk,)
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE local_media_map "
                    "SET unique_id=?, title=?, artist=?, path=?, filename=?, "
                    "normalized_title=?, normalized_artist=?, normalized_filename=?, "
                    "updated_at=? WHERE azura_file_id=?",
                    (uid, title, artist, path, fname,
                     norm_title, norm_artist, norm_filename, now, pk),
                )
                updated += 1
            else:
                conn.execute(
                    "INSERT INTO local_media_map "
                    "(azura_file_id, unique_id, title, artist, path, filename, "
                    "normalized_title, normalized_artist, normalized_filename, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (pk, uid, title, artist, path, fname,
                     norm_title, norm_artist, norm_filename, now),
                )
                inserted += 1

        conn.commit()
    except Exception as exc:
        print(f"{_LOG} DB write error: {exc!r}")
        await _w(f"❌ DB error during scan: {exc}")
        return
    finally:
        conn.close()

    total = inserted + updated
    await _w(
        f"✅ Media map updated!\n"
        f"New: {inserted}  Updated: {updated}  Total: {total}"
    )
    print(f"{_LOG} scan complete — {inserted} new, {updated} updated, {total} total")


# ── !localmediastatus ─────────────────────────────────────────────────────────

async def handle_localmediastatus(bot, user, args):
    async def _w(msg: str) -> None:
        try:
            await bot.highrise.send_whisper(user.id, msg[:249])
        except Exception:
            pass

    try:
        from modules.admin_cmds import is_owner, is_admin
        if not (is_owner(user.username) or is_admin(user.username)):
            await _w("❌ Owner/admin only.")
            return
    except Exception:
        pass

    conn = _get_conn()
    try:
        _ensure_table(conn)
        count   = conn.execute("SELECT COUNT(*) FROM local_media_map").fetchone()[0]
        last_row = conn.execute(
            "SELECT updated_at FROM local_media_map ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    last = last_row[0][:16] if last_row else "never"
    await _w(
        f"📊 Local Media Map\n"
        f"Songs indexed: {count}\n"
        f"Last scan: {last} UTC\n"
        f"Run !localmediascan to refresh."
    )


# ── !localmediafind ───────────────────────────────────────────────────────────

async def handle_localmediafind(bot, user, args):
    async def _w(msg: str) -> None:
        try:
            await bot.highrise.send_whisper(user.id, msg[:249])
        except Exception:
            pass

    try:
        from modules.admin_cmds import is_owner, is_admin
        if not (is_owner(user.username) or is_admin(user.username)):
            await _w("❌ Owner/admin only.")
            return
    except Exception:
        pass

    query_parts = [
        a for a in args
        if a.lower() not in ("localmediafind",)
    ]
    if not query_parts:
        await _w("Usage: !localmediafind <search text>")
        return

    query  = " ".join(query_parts)
    norm_q = _normalize(query)

    conn = _get_conn()
    try:
        _ensure_table(conn)
        all_rows = conn.execute(
            "SELECT title, artist, path, "
            "normalized_title, normalized_artist, normalized_filename "
            "FROM local_media_map"
        ).fetchall()
    finally:
        conn.close()

    if not all_rows:
        await _w("❌ Map is empty. Run !localmediascan first.")
        return

    scored: list[tuple] = []
    for row in all_rows:
        title, artist, path, nt, na, nf = row
        score = 0
        if norm_q == nt:
            score += 10
        elif norm_q in nt:
            score += 5
        if norm_q == na:
            score += 7
        elif norm_q in na:
            score += 3
        if norm_q in nf:
            score += 4
        if nt.startswith(norm_q[:6]) if len(norm_q) >= 6 else nt == norm_q:
            score += 1
        if score > 0:
            scored.append((score, title, artist, path))

    scored.sort(key=lambda x: -x[0])
    top = scored[:5]

    if not top:
        await _w(f"❌ No matches for '{query}'. Run !localmediascan first.")
        return

    header = f"🔎 Results for '{query}':"
    page   = header
    for i, (_, title, artist, path) in enumerate(top, 1):
        display_path = path or "?"
        entry = f"\n{i}. {title}"
        if artist:
            entry += f" — {artist}"
        entry += f"\n   Path: {display_path}"
        candidate = page + entry
        if len(candidate) > 249:
            await _w(page)
            page = entry.lstrip("\n")
        else:
            page = candidate
    if page:
        await _w(page)


# ── match_from_map — used by local_replay.py ─────────────────────────────────

def match_from_map(
    title: str, artist: str = ""
) -> "tuple[dict | None, str]":
    """
    Search local_media_map by normalized title + artist.

    Returns (row_dict, status) where status is:
      "ok"       — single strong match found  →  row_dict is populated
      "multiple" — ambiguous matches          →  row_dict is None
      "none"     — no match                  →  row_dict is None
    """
    norm_t = _normalize(title)
    norm_a = _normalize(artist)

    conn = _get_conn()
    try:
        _ensure_table(conn)
        all_rows = conn.execute(
            "SELECT azura_file_id, unique_id, title, artist, path, filename, "
            "normalized_title, normalized_artist, normalized_filename "
            "FROM local_media_map"
        ).fetchall()
    finally:
        conn.close()

    if not all_rows:
        return None, "none"

    scored: list[tuple] = []
    for row in all_rows:
        fid, uid, t, a, path, fname, nt, na, nf = row
        score = 0

        if norm_t:
            if norm_t == nt:
                score += 10
            elif norm_t in nt:
                score += 5
            elif len(norm_t) >= 5 and nt.startswith(norm_t[:5]):
                score += 2
        if norm_a:
            if norm_a == na:
                score += 5
            elif norm_a in na:
                score += 2
        if norm_t and norm_t in nf:
            score += 3

        if score >= 5:
            scored.append((score, {
                "azura_file_id": fid,
                "unique_id":     uid,
                "title":         t,
                "artist":        a,
                "path":          path,
                "filename":      fname,
            }))

    scored.sort(key=lambda x: -x[0])

    if not scored:
        return None, "none"
    if len(scored) == 1 or scored[0][0] >= scored[1][0] + 4:
        return scored[0][1], "ok"
    return None, "multiple"
