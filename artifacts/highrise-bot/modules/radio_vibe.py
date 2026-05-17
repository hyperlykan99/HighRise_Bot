"""
modules/radio_vibe.py
---------------------
AzuraCast vibe / playlist switching for DJ_DUDU.

Commands (all owned by "dj" bot mode):
  !vibe chill    — enable default/chill playlist, disable party remixes
  !vibe party    — enable party remixes, disable chill
  !vibe status   — show current vibe + playlist config

Environment variables:
  AZURA_BASE_URL             AzuraCast base URL
  AZURA_API_KEY              AzuraCast API key
  AZURA_STATION_ID           Station ID (default "1")
  AZURA_PLAYLIST_CHILL_ID    Playlist ID for default/chill music
  AZURA_PLAYLIST_PARTY_ID    Playlist ID for party remixes

Vibe state is saved in room_settings key "radio_current_vibe".
The Requests playlist is always kept enabled regardless of vibe.
"""
from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import database as db
from modules.permissions import is_admin

if TYPE_CHECKING:
    from highrise import BaseBot, User

_CFG_VIBE = "radio_current_vibe"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


def _api_cfg() -> "dict | None":
    base_url = (os.environ.get("AZURA_BASE_URL") or "").rstrip("/").strip()
    api_key  = (os.environ.get("AZURA_API_KEY")  or "").strip()
    if not base_url or not api_key:
        return None
    return {
        "base_url":   base_url,
        "api_key":    api_key,
        "station_id": (os.environ.get("AZURA_STATION_ID") or "1").strip(),
    }


def get_current_vibe() -> str:
    """Return 'chill' or 'party' (persisted in room_settings; default 'chill')."""
    return (db.get_room_setting(_CFG_VIBE, "chill") or "chill").lower().strip()


def set_current_vibe(vibe: str) -> None:
    db.set_room_setting(_CFG_VIBE, vibe)


# ─────────────────────────────────────────────────────────────────────────────
# AzuraCast playlist toggle
# ─────────────────────────────────────────────────────────────────────────────

def _azura_set_playlist_enabled(playlist_id: str, enabled: bool) -> bool:
    """
    Blocking: PUT /api/station/{station_id}/playlist/{playlist_id}
    Body: {"is_enabled": <bool>}
    Returns True on HTTP 200 or 204.
    """
    import requests as req_lib

    cfg = _api_cfg()
    if not cfg or not playlist_id:
        return False
    try:
        resp = req_lib.put(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/playlist/{playlist_id}",
            json={"is_enabled": enabled},
            headers={
                "X-API-Key":    cfg["api_key"],
                "Content-Type": "application/json",
                "Accept":       "application/json",
            },
            timeout=10,
        )
        print(
            f"[VIBE] Playlist {playlist_id} enabled={enabled}"
            f" → HTTP {resp.status_code}: {resp.text[:200]}"
        )
        return resp.status_code in (200, 204)
    except Exception as exc:
        print(f"[VIBE] Playlist toggle error (non-fatal): {exc}")
    return False


def _apply_vibe_blocking(vibe: str) -> tuple[bool, bool]:
    """
    Apply a vibe by toggling the two background playlists.
    The Requests playlist is intentionally left untouched (always active).

    chill → enable CHILL, disable PARTY
    party → enable PARTY, disable CHILL

    Returns (chill_ok, party_ok) — True means the API call succeeded
    (or the env var was not set, so nothing to toggle).
    """
    chill_id = (os.environ.get("AZURA_PLAYLIST_CHILL_ID") or "").strip()
    party_id = (os.environ.get("AZURA_PLAYLIST_PARTY_ID") or "").strip()

    if vibe == "party":
        chill_ok = _azura_set_playlist_enabled(chill_id, False) if chill_id else True
        party_ok = _azura_set_playlist_enabled(party_id, True)  if party_id else True
    else:
        chill_ok = _azura_set_playlist_enabled(chill_id, True)  if chill_id else True
        party_ok = _azura_set_playlist_enabled(party_id, False) if party_id else True

    return chill_ok, party_ok


# ─────────────────────────────────────────────────────────────────────────────
# Command handler
# ─────────────────────────────────────────────────────────────────────────────

async def handle_vibe(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!vibe chill|party|status — switch music vibe or check current vibe."""
    sub = args[1].lower() if len(args) > 1 else "status"

    # ── Status (public) ───────────────────────────────────────────────────────
    if sub == "status":
        vibe     = get_current_vibe()
        chill_id = (os.environ.get("AZURA_PLAYLIST_CHILL_ID") or "").strip()
        party_id = (os.environ.get("AZURA_PLAYLIST_PARTY_ID") or "").strip()
        api_ok   = "✅ ready" if _api_cfg() else "❌ not configured"
        await _w(
            bot, user.id,
            (f"📻 Radio Vibe: {'🎶 Chill' if vibe == 'chill' else '🔥 Party'}\n"
             f"Chill ID: {chill_id or '(not set)'}\n"
             f"Party ID: {party_id or '(not set)'}\n"
             f"AzuraCast: {api_ok}")[:249],
        )
        return

    # ── Switch vibe (admin only) ──────────────────────────────────────────────
    if sub not in ("chill", "party"):
        await _w(bot, user.id, "⚠️ Usage: !vibe chill|party|status")
        return

    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only. Use !vibe status to see the current vibe.")
        return

    chill_ok, party_ok = await asyncio.get_running_loop().run_in_executor(
        None, _apply_vibe_blocking, sub
    )
    set_current_vibe(sub)

    if sub == "chill":
        room_msg = "🎶 Switching to Chill vibes! Sit back and relax. 🎶"
        priv_msg = (
            f"✅ Vibe set to Chill.\n"
            f"Chill playlist: {'✅ enabled' if chill_ok else '⚠️ API error'} | "
            f"Party playlist: {'✅ disabled' if party_ok else '⚠️ API error'}"
        )
    else:
        room_msg = "🔥 Switching to Party mode! Let's gooo! 🔥"
        priv_msg = (
            f"✅ Vibe set to Party.\n"
            f"Party playlist: {'✅ enabled' if party_ok else '⚠️ API error'} | "
            f"Chill playlist: {'✅ disabled' if chill_ok else '⚠️ API error'}"
        )

    await _w(bot, user.id, priv_msg[:249])
    try:
        await bot.highrise.chat(room_msg[:249])
    except Exception as exc:
        print(f"[VIBE] Room chat error (non-fatal): {exc}")
