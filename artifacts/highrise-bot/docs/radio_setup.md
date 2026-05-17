# Radio Infrastructure Setup — DJ_DUDU

## Overview

DJ_DUDU's radio infrastructure is **metadata and queue only**. The bot:
- Searches YouTube for song metadata (title, artist, URL) — **no audio downloaded**
- Maintains a song request queue in SQLite
- Can push "now-playing" metadata to a radio provider via HTTP (Icecast or AzuraCast)
- **Never streams, rips, downloads, or re-encodes audio**

Audio is served by your own Icecast/AzuraCast instance, which you control and license separately.

---

## Supported Provider Types

| Type | Description |
|------|-------------|
| `icecast` | Self-hosted Icecast 2.x server. Bot pushes metadata via `/admin/metadata`. |
| `azuracast` | Self-hosted AzuraCast instance. Metadata push placeholder (Phase 2). |
| `external` | Third-party stream (e.g. Shoutcast SaaS, Mixcloud). Bot does nothing — provider manages its own metadata. |

---

## In-Room Configuration Commands

All commands are DJ_DUDU–only (`BOT_MODE=dj`). Manager+ required to view; admin required to change.

```
!radioconfig                          — view full radio infrastructure status
!setradiotype icecast|azuracast|external  — set provider type
!setradiomount /live                  — set mount point (Icecast/AzuraCast)
!setradiometadata on|off              — enable/disable metadata push
!setradio <url>                       — set public stream URL (shown to players)
```

---

## Environment Variables

### Icecast

| Variable | Required | Description |
|----------|----------|-------------|
| `ICECAST_HOST` | Yes | Full base URL, e.g. `http://radio.example.com:8000` |
| `ICECAST_ADMIN_USER` | No | Admin username (default: `admin`) |
| `ICECAST_ADMIN_PASS` | Yes | Icecast admin password |

The bot calls:
```
GET {ICECAST_HOST}/admin/metadata?mount=/live&mode=updinfo&charset=utf-8&song=Artist+-+Title
```
with HTTP Basic Auth. **No audio data is sent** — only the song title string.

### AzuraCast

| Variable | Required | Description |
|----------|----------|-------------|
| `AZURACAST_HOST` | Yes | Full base URL, e.g. `https://radio.example.com` |
| `AZURACAST_API_KEY` | Yes | AzuraCast API key (read from Station API Keys) |
| `AZURACAST_STATION` | Yes | Station short name or numeric ID |

AzuraCast metadata push is a **Phase 2 placeholder** — the function is wired but the HTTP call is not yet implemented. Set `!setradiometadata off` until Phase 2.

---

## How to Connect an Icecast Server

1. Deploy Icecast 2.x on a VPS or use a managed service (Centova Cast, etc.).
2. Configure a mount point (e.g. `/live`) with a source password for your streaming client (Butt, MIXXX, Traktor, etc.).
3. In Replit Secrets, add `ICECAST_HOST`, `ICECAST_ADMIN_PASS`.
4. In-room, run:
   ```
   !setradiotype icecast
   !setradiomount /live
   !setradiometadata on
   !setradio http://radio.example.com:8000/live
   ```
5. Verify with `!radioconfig` — should show both env vars as ✅.
6. When a song plays, the bot will push `"Artist - Title"` to Icecast automatically.

---

## How to Connect an AzuraCast Server

1. Deploy AzuraCast (Docker recommended: https://www.azuracast.com/docs/install/).
2. Generate an API key under **My Account → API Keys**.
3. Note your station's short name from the station URL.
4. Add `AZURACAST_HOST`, `AZURACAST_API_KEY`, `AZURACAST_STATION` to Replit Secrets.
5. In-room: `!setradiotype azuracast` — metadata push will activate in Phase 2.

---

## Legal / Safe Streaming Note

- **DJ_DUDU never downloads or rips audio.** `yt-dlp` is used exclusively for YouTube metadata search (`skip_download: True`, `download=False`).
- Audio streaming to listeners is performed entirely by your Icecast/AzuraCast instance and your DJ client software (Butt, MIXXX, etc.).
- You are responsible for ensuring your broadcast complies with applicable music licensing laws (ASCAP/BMI/SESAC in the US, PPL/PRS in the UK, etc.).
- Playing songs from YouTube requests in a live stream may require a streaming license. Consult a music licensing provider.
- The web status page and queue system are read-only displays — no audio passes through them.

---

## Web Status Page Fields

The DJ status API (`/api/dj/status`) now includes:

| Field | Type | Description |
|-------|------|-------------|
| `radio_type` | `string` | `"icecast"` \| `"azuracast"` \| `"external"` |
| `radio_mount` | `string\|null` | Mount point, e.g. `/live` |
| `radio_metadata_enabled` | `boolean` | Whether metadata push is active |
| `listener_count` | `null` | Phase 2 — will reflect live listener count |
| `stream_live` | `null` | Phase 2 — will reflect provider health check |

---

## Future Work (Phase 2)

- [ ] Live listener count from Icecast `/status-json.xsl`
- [ ] AzuraCast metadata push via `/api/station/{id}/backend/custom_metadata`
- [ ] Stream health check (HTTP probe to stream URL)
- [ ] Auto-announce listener milestones in room chat
