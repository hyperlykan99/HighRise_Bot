"""
modules/radio_achievements.py
------------------------------
Radio engagement achievements — 21 achievements across 6 categories.
Uses existing radio_rewards.get_user_stats() for backfill-safe stat data.
Stores / checks unlock state in the shared `achievements` table via
db.unlock_achievement() / db.get_unlocked_achievements().

Achievement IDs (stored in `achievements` table):
  radio_fan_{bronze|silver|gold|legend}
  taste_maker_{bronze|silver|gold|legend}
  hit_maker_{bronze|silver|gold|legend}
  playlist_curator_{bronze|silver|gold|legend}
  collector_{bronze|silver|gold|legend}
  radio_legend

Anti-spam: db.unlock_achievement() is a no-op if already unlocked — no
           duplicate notifications possible.
"""
from __future__ import annotations
import asyncio
from typing import TYPE_CHECKING

import database as db
import modules.radio_rewards as rr

if TYPE_CHECKING:
    from highrise import BaseBot
    from highrise.models import User

_LOG = "[RADIO_ACH]"

# ─── Tier constants ───────────────────────────────────────────────────────────

_TIERS = ("bronze", "silver", "gold", "legend")
_TIER_ICON = {"bronze": "🥉", "silver": "🥈", "gold": "🥇", "legend": "👑"}

# ─── Category catalog ─────────────────────────────────────────────────────────

_CATEGORIES: list[dict] = [
    {
        "key":   "radio_fan",
        "icon":  "🎧",
        "name":  "Radio Fan",
        "field": "requests",
        "tiers": [25, 100, 250, 500],
        "unit":  "requests",
    },
    {
        "key":   "taste_maker",
        "icon":  "⭐",
        "name":  "Taste Maker",
        "field": "likes",
        "tiers": [25, 100, 300, 750],
        "unit":  "likes",
    },
    {
        "key":   "hit_maker",
        "icon":  "🔥",
        "name":  "Hit Maker",
        "field": "hit_maker",          # special: computed in _get_progress()
        "tiers": [10, 25, 50, 100],
        "unit":  "likes on 1 song",
    },
    {
        "key":      "playlist_curator",
        "icon":     "📂",
        "name":     "Playlist Curator",
        "field":    "pl_songs",
        "tiers":    [25, 100, 250, 500],
        "unit":     "playlist songs",
        "pl_gates": [3, 5, 10, 0],    # min playlists required per tier; 0 = no gate
    },
    {
        "key":   "collector",
        "icon":  "💿",
        "name":  "Collector",
        "field": "favorites",
        "tiers": [25, 100, 250, 500],
        "unit":  "favorites",
    },
]

_LEGEND_ID = "radio_legend"


# ─── Hit Maker: max likes on any single song this user has requested ──────────

def _hit_maker_max(user_id: str) -> int:
    """
    Joins yt_request_jobs (user's requests) with dj_ratings (all likes) on
    normalised title to find the max like count for any single song this user
    has ever requested.  Returns 0 on any error.
    """
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT COALESCE(MAX(cnt), 0) FROM ("
            "  SELECT COUNT(*) AS cnt"
            "  FROM yt_request_jobs rj"
            "  JOIN dj_ratings dr"
            "    ON LOWER(SUBSTR(rj.title, 1, 150)) = dr.song_key"
            "    AND dr.rating = 'like'"
            "  WHERE rj.user_id = ?"
            "  GROUP BY LOWER(SUBSTR(rj.title, 1, 150))"
            ")",
            (user_id,),
        ).fetchone()
        conn.close()
        return int((row or (0,))[0] or 0)
    except Exception as exc:
        print(f"{_LOG} hit_maker_max error: {exc!r}")
        try:
            conn.close()
        except Exception:
            pass
        return 0


def _get_progress(user_id: str) -> dict:
    """Backfill-safe stats dict, augmented with 'hit_maker' count."""
    s = rr.get_user_stats(user_id)
    s["hit_maker"] = _hit_maker_max(user_id)
    return s


# ─── Tier evaluation ──────────────────────────────────────────────────────────

def _tier_idx(cat: dict, s: dict) -> int:
    """Highest met tier index (0=bronze … 3=legend), or -1 if none met."""
    val    = s.get(cat["field"], 0)
    gates  = cat.get("pl_gates")
    pl_cnt = s.get("playlists", 0) if gates else 0
    best   = -1
    for i, threshold in enumerate(cat["tiers"]):
        if val < threshold:
            continue
        if gates:
            min_pl = gates[i]
            if min_pl > 0 and pl_cnt < min_pl:
                continue
        best = i
    return best


def _is_legend_met(s: dict) -> bool:
    return (
        s.get("requests", 0) >= 500
        and (s.get("likes", 0) + s.get("favorites", 0)) >= 500
        and s.get("playlists", 0) >= 10
        and s.get("hit_maker", 0)  >= 100
    )


# ─── Post-action unlock check ────────────────────────────────────────────────

async def check_radio_achievements(bot: "BaseBot", user_id: str, username: str) -> None:
    """
    Called (as asyncio.create_task) after any qualifying radio action.
    Evaluates all radio tiers, unlocks newly-met ones, and whispers the player.
    Accepts user_id / username strings so it works from _submit_url too.
    """
    try:
        s     = _get_progress(user_id)
        newly: list[str] = []

        for cat in _CATEGORIES:
            idx = _tier_idx(cat, s)
            for i in range(idx + 1):
                ach_id = f"{cat['key']}_{_TIERS[i]}"
                if db.unlock_achievement(user_id, ach_id):
                    tier_str = f"{_TIER_ICON[_TIERS[i]]} {_TIERS[i].title()}"
                    newly.append(f"{cat['icon']} {cat['name']} {tier_str}")

        if _is_legend_met(s) and db.unlock_achievement(user_id, _LEGEND_ID):
            newly.append("🏆 Radio Legend")

        for i in range(0, len(newly), 3):
            msg = "🏅 Achievement unlocked: " + ", ".join(newly[i:i + 3])
            await _w(bot, user_id, msg[:249])
            if i + 3 < len(newly):
                await asyncio.sleep(0.15)

    except Exception as exc:
        print(f"{_LOG} check error: {exc!r}")


# ─── !radioachievements command handler ───────────────────────────────────────

async def handle_radioachievements(bot: "BaseBot", user: "User", _args: list) -> None:
    """!radioachievements — show radio achievement progress + next-tier hints."""
    uid = user.id
    try:
        s        = _get_progress(uid)
        unlocked = set(db.get_unlocked_achievements(uid))

        rows:  list[str] = []
        hints: list[str] = []

        for cat in _CATEGORIES:
            idx  = _tier_idx(cat, s)
            key  = cat["key"]
            icon = cat["icon"]
            name = cat["name"]
            val  = s.get(cat["field"], 0)

            if idx == 3:
                rows.append(f"{icon} {name}: 👑 Legend ✅")

            elif idx >= 0:
                label = f"{_TIER_ICON[_TIERS[idx]]} {_TIERS[idx].title()}"
                rows.append(f"{icon} {name}: {label}")
                # next-tier progress hint
                nxt     = idx + 1
                nxt_thr = cat["tiers"][nxt]
                nxt_lbl = _TIERS[nxt].title()
                if key == "playlist_curator":
                    gate   = cat["pl_gates"][nxt]
                    pl_cnt = s.get("playlists", 0)
                    if gate > 0 and pl_cnt < gate:
                        hints.append(
                            f"{icon} {name} {nxt_lbl}: "
                            f"{pl_cnt}/{gate} playlists + {val}/{nxt_thr} songs"
                        )
                    else:
                        hints.append(
                            f"{icon} {name} {nxt_lbl}: {val}/{nxt_thr} songs"
                        )
                else:
                    hints.append(
                        f"{icon} {name} {nxt_lbl}: {val}/{nxt_thr} {cat['unit']}"
                    )

            else:
                # no tier met — show locked with progress toward Bronze
                if key == "hit_maker":
                    if val == 0:
                        rows.append(f"{icon} {name}: Tracking soon")
                    else:
                        rows.append(f"{icon} {name}: Locked ({val}/{cat['tiers'][0]})")
                        hints.append(
                            f"{icon} {name} Bronze: {val}/{cat['tiers'][0]} {cat['unit']}"
                        )
                elif key == "playlist_curator":
                    pl_cnt = s.get("playlists", 0)
                    rows.append(
                        f"{icon} {name}: Locked ({pl_cnt}/3 pl, {val}/25 songs)"
                    )
                    hints.append(
                        f"{icon} {name} Bronze: {pl_cnt}/3 playlists + {val}/25 songs"
                    )
                else:
                    rows.append(
                        f"{icon} {name}: Locked ({val}/{cat['tiers'][0]})"
                    )
                    hints.append(
                        f"{icon} {name} Bronze: {val}/{cat['tiers'][0]} {cat['unit']}"
                    )

        # Radio Legend row
        if _LEGEND_ID in unlocked:
            rows.append("🏆 Radio Legend: UNLOCKED ✅")
        else:
            reqs = s.get("requests", 0)
            lf   = s.get("likes", 0) + s.get("favorites", 0)
            pl   = s.get("playlists", 0)
            hm   = s.get("hit_maker", 0)
            rows.append(
                f"🏆 Legend: {reqs}/500 req · {lf}/500 lf · {pl}/10 pl · {hm}/100 hm"
            )

        # ── paginate: 3 rows per page ─────────────────────────────────────────
        chunks    = [rows[i:i + 3] for i in range(0, len(rows), 3)]
        total_pgs = len(chunks)
        for pg, chunk in enumerate(chunks, 1):
            hdr = (
                f"🏅 Radio Achievements {pg}/{total_pgs}"
                if total_pgs > 1 else "🏅 Radio Achievements"
            )
            await _w(bot, uid, (hdr + "\n" + "\n".join(chunk))[:249])
            if pg < total_pgs:
                await asyncio.sleep(0.25)

        # ── next-progress hints (top 2 most actionable) ───────────────────────
        if hints:
            await asyncio.sleep(0.25)
            await _w(bot, uid, ("⏭ Next:\n" + "\n".join(hints[:2]))[:249])

    except Exception as exc:
        print(f"{_LOG} handle error: {exc!r}")
        await _w(bot, uid, "🏅 Could not load achievements. Try again.")


# ─── Whisper helper ───────────────────────────────────────────────────────────

async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception as exc:
        print(f"{_LOG} whisper error: {exc!r}")
