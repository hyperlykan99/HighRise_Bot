# DJ_DUDU Deployment Checklist

DJ_DUDU is the dedicated DJ bot. It runs as a subprocess with `BOT_MODE=dj`.
It owns all music queue, radio, web player, and DJ-utility commands.
ChillTopiaMC (all-mode bot) will never handle these commands.

---

## 1. Required Environment Variables

| Variable | Required | Notes |
|---|---|---|
| `ROOM_ID` | **Yes** | Shared by all bots. Same room ID for every bot. |
| `BOT_TOKEN` or `MAIN_BOT_TOKEN` | **Yes** | Token for ChillTopiaMC (all-mode). Not DJ_DUDU's token. |
| `DJ_BOT_TOKEN` | **Yes** | Highrise account token for DJ_DUDU specifically. |

Without `DJ_BOT_TOKEN` the runner will not start a DJ subprocess.
Without `ROOM_ID` no bot can connect.

---

## 2. Optional Environment Variables

| Variable | Default | Notes |
|---|---|---|
| `DJ_BOT_ID` | `dj` | Internal bot identifier. Leave as default unless you run multiple DJ bots. |
| `DJ_BOT_MODE` | `dj` | Must remain `dj`. Only change if you know what you are doing. |
| `DJ_BOT_USERNAME` | *(blank)* | Sets `BOT_USERNAME` hint (e.g. `DJ_DUDU`). Used in welcome messages and logs. |
| `YOUTUBE_API_KEY` | *(blank)* | Enables YouTube metadata lookup for `!request`. Without it, requests still queue but YT search is disabled. `!djtestall` will warn `⚠️ no YT key` if absent. |
| `RADIO_STREAM_URL` | *(blank)* | Default radio stream shown by `!radio`. Room setting (set via `!setradio`) overrides this at runtime. |
| `DJ_WEBPLAYER_URL` | *(blank)* | Default web player link shown by `!webplayer`. Room setting (set via `!setwebplayer`) overrides this at runtime. |
| `SHARED_DB_PATH` | `highrise_hangout.db` | SQLite database file path. All bots share the same file. |

---

## 3. BOT_MODE Must Stay `dj`

DJ_DUDU's subprocess always runs with `BOT_MODE=dj`. This is set automatically
by `bot.py` when `DJ_BOT_TOKEN` is present. Do not override it.

`BOT_MODE=dj` is how the routing guard works:
- `should_this_bot_handle()` returns `True` only for commands owned by `"dj"`.
- `_HARD_OWNER_MODES` in `multi_bot.py` includes `"dj"`, which permanently
  prevents ChillTopiaMC from handling DJ commands even when DJ_DUDU is offline.

---

## 4. Startup Sequence

When `bot.py` runs it reads all `*_BOT_TOKEN` env vars and spawns one
subprocess per unique Highrise account. DJ_DUDU appears in the logs as:

```
[RUNNER] DJ_BOT_TOKEN set -> starting DJ Bot mode dj
[PROCESS START] DJ Bot mode=dj id=dj @ HH:MM:SS UTC
[SDK] bot mode=dj ready
[BOT] dj online | id=dj | mode=dj
```

If `DJ_BOT_TOKEN` is missing, those four lines will not appear and DJ_DUDU
will not be running.

---

## 5. How to Test with `!djtestall`

Send `!djtestall` in the Highrise room **as an admin or owner**.
DJ_DUDU will whisper you a report covering 5 checks:

| Check | What it verifies |
|---|---|
| 1. DB tables | All 6 DJ tables exist: `dj_requests`, `dj_bans`, `dj_song_bans`, `dj_reports`, `dj_economy_log`, `dj_favorites` |
| 2. Routing | 23 key commands are owned by this bot (not falling through to another bot) |
| 3. YT key | `YOUTUBE_API_KEY` is set and returns a valid response |
| 4. Config | `max_queue_size`, `request_cost`, `priority_cost` are sensible values |
| 5. Audio safety | No downloading/ripping tools are accessible |

All checks pass → `✅ All X checks passed`.
Any failure → `❌` prefix on that line with the specific problem.

The 23 commands checked in routing (check 2) are:

```
request, pick, queue, np, skip, djhelp,
priorityrequest, viprequest, djban, songban,
djcheck, djhealth, cancelrequest, requeststatus, djtestall,
radio, setradio, radiostatus, webplayer, setwebplayer,
recent, myrequests, songinfo
```

---

## 6. How to Verify Routing Is Working

Run `!djcheck` (admin+) in the room. DJ_DUDU will whisper:
- DB status (tables present / missing)
- Queue length and active song
- Whether DJ mode is active (`BOT_MODE=dj`)

Run `!djconfig` (manager+) to see all current room settings for the DJ
module (queue size, costs, cooldowns, radio URL, web player URL, vote-skip
threshold, etc.).

Run `!djhealth` (manager+) for a live status snapshot: uptime, queue depth,
active bans, song-ban count, and whether YouTube search is available.

---

## 7. Recovering If Routing Breaks

**Symptom:** `!request`, `!queue`, `!radio` etc. get no response, or
ChillTopiaMC replies with an unknown-command message.

**Step 1 — Check DJ_DUDU is online**

Look in the Highrise room for DJ_DUDU's account. If it is not present,
the bot subprocess crashed or never started.

Check the runner logs for:
```
[PROCESS START] DJ Bot mode=dj id=dj
```
If this line is missing, `DJ_BOT_TOKEN` is not set or is invalid.

**Step 2 — Check the heartbeat**

Run `!botstatus` (admin+) from ChillTopiaMC. Look for `dj` in the list.
If it shows `offline` or is absent, DJ_DUDU's heartbeat has lapsed
(heartbeat writes every 30 s; a bot is considered offline after 90 s).

**Step 3 — Restart the workflow**

In the Replit console, restart the `Highrise Bot` workflow. All 8 bots
restart simultaneously. DJ_DUDU will reconnect and re-register its heartbeat.

**Step 4 — Run `!djtestall`**

After DJ_DUDU reconnects (~30 s after restart), run `!djtestall` to confirm
all 5 checks pass. If routing check 2 lists any commands as unowned, that
means `multi_bot.py`'s `_DEFAULT_COMMAND_OWNERS` no longer maps them to `"dj"`.
Fix: re-add the command to `_DEFAULT_COMMAND_OWNERS` and restart.

**Step 5 — Force command ownership (last resort)**

If a specific command was reassigned in the DB, use:
```
!setcommandowner <command> dj
```
This writes the override to `bot_command_ownership` and takes effect immediately
without a restart.

---

## 8. What DJ_DUDU Never Does

- Never downloads or rips audio files.
- Never streams audio directly.
- Handles metadata, search (YouTube API), queue management, and link display only.
- `RADIO_STREAM_URL` and `DJ_WEBPLAYER_URL` are URLs that DJ_DUDU *displays*;
  it does not fetch or play them.

---

## 9. Quick Reference — DJ Commands by Role

| Role | Key Commands |
|---|---|
| Everyone | `!request`, `!queue`, `!np`, `!recent`, `!myrequests`, `!songinfo`, `!djhelp` |
| Everyone | `!radio`, `!webplayer`, `!radiostatus` |
| Everyone | `!cancelrequest`, `!requeststatus` |
| VIP / Subscriber | `!viprequest`, `!priorityrequest` |
| Admin+ | `!djban`, `!songban`, `!djcheck`, `!djhealth`, `!djconfig`, `!djtestall` |
| Owner | `!djresetstate`, `!djbackup` |

Full command list: send `!djhelp` in the room. DJ_DUDU whispers 4 sections
covering all 58 commands.
