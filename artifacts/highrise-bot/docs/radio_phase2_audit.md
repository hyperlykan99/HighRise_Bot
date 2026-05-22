# Radio System Phase 2 Audit — DJ_DUDU

> **Scope:** Audit and dependency mapping only. No behavior changes made.  
> **Date:** May 2026  
> **Bot:** DJ_DUDU (`BOT_MODE=dj`)

---

## 1. Module Inventory

| Module | Lines | Role | Background Tasks | Startup Hook |
|--------|-------|------|-----------------|--------------|
| `radio_commands.py` | 2,860 | Primary command dispatcher — all `!play`, `!queue`, `!np`, ratings, playlists, favorites, shop | `_cleanup_poll_task` | `startup_radio()` |
| `yt_request.py` | 3,797 | YouTube download pipeline — yt-dlp, SFTP upload, job management | `_cleanup_loop`, `_run_job` | `startup_yt_cleanup_task()` |
| `playback_engine.py` | 1,423 | AzuraCast playback tracker — polls AzuraCast, manages now-playing state | `_poll_loop`, `_verified_skip_task` | None (self-starts on import) |
| `azuracast_controller.py` | 1,195 | AzuraCast HTTP + SFTP client — pure API wrapper, no bot logic | None | None |
| `dj_music.py` | 3,429 | Legacy DJ command layer — 10+ deprecated delegates to `radio_commands` | None | None |
| `radio_rewards.py` | 725 | Radio points accounting — `record_reward`, stats, leaderboards | None | `_bootstrap()` (lazy) |
| `request_queue.py` | 582 | DB-backed job queue — `submit_job`, `cancel_job`, status queries | None | None |
| `track_resolver.py` | 385 | Now-playing resolver + renderer — `resolve_current_track`, `render_now_playing` | None | None |
| `dj_announcer.py` | 228 | Room announcement helpers — `announce_now_playing`, skip, voteskip | None | None |
| `music_credits.py` | 210 | Request credit wallet — buy/consume/refund credits | None | None |
| `radio_achievements.py` | 300 | Radio badge system — 21 achievements, hit-maker query | None | None |

---

## 2. Dependency Graph

```
main.py
 ├── radio_commands.py
 │    ├── azuracast_controller.py
 │    ├── config_store.py
 │    ├── dj_announcer.py  ──────────────────── (see below)
 │    ├── music_credits.py
 │    ├── payment_service.py
 │    ├── request_queue.py ──────────────────── (see below)
 │    ├── playback_engine.py ─────────────────── (see below)
 │    ├── radio_rewards.py ──────────────────── (no further radio deps)
 │    ├── radio_achievements.py
 │    │    └── radio_rewards.py
 │    ├── permissions.py
 │    ├── luxe.py
 │    └── msg_utils.py
 │
 ├── yt_request.py (separate pipeline)
 │    ├── config.py
 │    ├── database.py (direct sqlite3 + db module)
 │    ├── permissions.py
 │    └── msg_utils.py
 │
 ├── playback_engine.py
 │    ├── azuracast_controller.py
 │    ├── config_store.py
 │    └── dj_announcer.py
 │
 ├── request_queue.py
 │    └── database.py
 │
 ├── dj_announcer.py
 │    └── (receives track dicts — no module imports except `time`)
 │
 └── dj_music.py (legacy layer)
      ├── radio_commands.py  (delegates 10+ commands here)
      └── dj_requests table  (separate legacy queue table)
```

**No circular dependencies found.** Import flow is strictly one-directional.

---

## 3. Duplicate Systems

### 3a. Whisper helper `_w(bot, uid, msg)`

Identical 3-line function defined independently in **5 modules**:

| Module | Line | Call sites |
|--------|------|-----------|
| `radio_commands.py` | 316 | **208** |
| `yt_request.py` | 74 | 112 |
| `radio_rewards.py` | 540 | 11 |
| `radio_achievements.py` | 296 | ~8 |
| `dj_announcer.py` | 63 | 1 |

> **Future action:** Extract to `modules/msg_utils.py` or a `radio/utils.py`. Already has `safe_send` there — `_w` is just a thin wrapper around it.

---

### 3b. `_fmt_secs` + `_progress_bar` renderer helpers

Identical implementations in **3 modules**:

| Module | Lines |
|--------|-------|
| `track_resolver.py` | 305–314 (canonical, used by `render_now_playing`) |
| `radio_commands.py` | 285–295 (local copy — NOT delegating to `track_resolver`) |
| `dj_music.py` | 817 (`_fmt_secs` only — legacy) |

`radio_commands.py` defines `_fmt_secs`/`_progress_bar` but also imports `render_now_playing` from `track_resolver.py` (which has its own copy). The local `radio_commands` copies are used in queue/status card builders separate from `!np`.

> **Future action:** Centralise in `track_resolver.py` (or future `radio/renderer.py`). `radio_commands.py` local copies can be deleted once queue cards use the shared versions.

---

### 3c. Dual queue tables

Two separate DB tables serve overlapping purposes:

| Table | Managed By | Status |
|-------|-----------|--------|
| `yt_request_jobs` | `yt_request.py`, `request_queue.py`, `playback_engine.py`, `radio_commands.py` | **Active** — current system |
| `dj_requests` | `dj_music.py` only | **Legacy** — migrations 3.2M+3.2N; still queried in `dj_music.py` |

`dj_music.py` still performs live `SELECT`/`INSERT`/`DELETE` against `dj_requests` in its non-deprecated code paths (e.g., the `!dj` command family). Commands marked `Deprecated` in `dj_music.py` delegate to `radio_commands.py` which uses `yt_request_jobs` exclusively.

> **Risk:** Requests submitted via old `dj_music.py` paths write to `dj_requests`; requests via `radio_commands.py` write to `yt_request_jobs`. These two queues are invisible to each other. In practice the deprecated paths just forward, so no actual split-write risk unless someone bypasses the delegate.

---

### 3d. Deprecated delegation chain in `dj_music.py`

10+ functions documented as `"Deprecated — delegates to radio_commands.*"`:

```python
handle_dj_request       → radio_commands.handle_request
handle_dj_pick          → radio_commands.handle_pick (implied)
handle_dj_queue         → radio_commands.handle_queue
handle_dj_nowplaying    → radio_commands.handle_nowplaying
handle_dj_voteskip      → radio_commands.handle_voteskip
handle_dj_clear         → handle_dj_clear (yt_request_jobs queue)
handle_dj_remove        → radio_commands.handle_remove
handle_dj_favorite      → radio_commands.handle_favorite
handle_dj_favorites     → radio_commands.handle_favorites
handle_dj_unfavorite    → radio_commands.handle_unfavorite
handle_dj_like          → radio_commands.handle_like
handle_dj_dislike       → radio_commands.handle_dislike
handle_dj_myrequests    → radio_commands.handle_myrequests
```

These wrapper functions exist only to preserve old routing entries. They add one call frame of overhead and obscure the real handler.

---

### 3e. Duplicate request handling overlap: `yt_request.py` vs `radio_commands.py`

Both modules define a `handle_request` function:

| Module | `handle_request` role |
|--------|-----------------------|
| `radio_commands.py` | User-facing `!play` — validates, charges coins, calls `yt_request.py` pipeline |
| `yt_request.py` | Also exports `handle_request` — older entry point with own coin-charging logic |

`yt_request.py` also duplicates `_charge_coins` logic independently from `payment_service.py`. The two `handle_request` symbols are both registered in `main.py` but the `yt_request` one is mapped to `ytrequest` command, not `play` — so in practice they don't conflict, but the naming is confusing and the coin logic is duplicated.

`yt_request.py` also uses **raw `sqlite3`** directly (bypassing the `db` module helpers) in several places, unlike every other module which uses `db.get_connection()`.

---

## 4. Startup / Crash Risk Audit

### 4a. Startup hooks and their guards

| Startup Function | Module | Guard |
|-----------------|--------|-------|
| `startup_radio(bot)` | `radio_commands.py` | `should_this_bot_run_module("yt_request")` |
| `startup_yt_cleanup_task(bot)` | `yt_request.py` | `should_this_bot_run_module("yt_request")` |
| `_poll_loop` (internal) | `playback_engine.py` | **None — starts on module import** |

> **Risk: `playback_engine.py` self-starts its polling loop** when the module is first imported, not inside a `should_this_bot_run_module` guard. If any non-DJ bot's import chain touches `playback_engine.py`, it will silently start the AzuraCast polling loop on that bot. Currently only `radio_commands.py` and `dj_music.py` import it, and those are only imported on the DJ bot — but this is fragile.

### 4b. Modules imported globally vs DJ-only

All radio modules are imported at the top level of `main.py` without `BOT_MODE` guards. The `should_this_bot_run_module()` guard only applies to *startup task creation*, not to *import*. This means:

- `azuracast_controller.py` is imported on all bot instances (including the BankingBot, PokerBot, etc.)  
- `yt_request.py` (3,797 lines, loads `yt_dlp` and `paramiko`) is imported on every bot  
- `playback_engine.py` (which starts `_poll_loop`) is imported on every bot

If `paramiko` or `yt_dlp` have import-time side effects, they run on every bot process.

> **Recommendation:** Wrap radio-only module imports in `main.py` with a `if _config.BOT_MODE == "dj":` guard, or use lazy imports inside the routing `elif` branches.

### 4c. `radio_achievements.py` startup safety

`radio_achievements.py` imports `radio_rewards.py` which calls `_bootstrap()` lazily (on first real use, not on import). No background tasks. No startup hooks. Safe on all bots.  
However, because it is imported in `main.py` unconditionally, `radio_rewards._bootstrap()` will run table-creation SQL on first `get_user_stats()` call regardless of bot mode — benign but wasteful.

### 4d. `yt_request.py` blast radius

Highest-risk module due to:
- 3,797 lines — largest in the radio stack  
- Spawns `threading.Thread` for each download job (`_run_job`)  
- Manages temp directories and SFTP connections in threads  
- Uses raw `sqlite3` directly (separate connection lifecycle from `db` module)  
- On uncaught exception inside `_run_job`, the thread dies silently — no bot crash, but the job is stuck in `pending`

---

## 5. Renderer Duplication Analysis

The following card/format rendering is currently scattered across multiple modules:

| Rendered output | Current location | Uses shared helper? |
|----------------|-----------------|---------------------|
| `!np` now-playing card | `track_resolver.render_now_playing()` | ✅ centralised |
| `!queue` queue card | `radio_commands._build_queue_card()` | ❌ local `_fmt_secs`/`_progress_bar` copies |
| `!history` history lines | `radio_commands.handle_history()` inline | ❌ inline formatting |
| `!toplisteners` leaderboard | `radio_rewards.handle_toplisteners()` inline | ❌ inline formatting |
| `!topliked` / `!topdisliked` song lines | `radio_rewards._fmt_song_line()` | ✅ shared within module |
| `!radioachievements` tier display | `radio_achievements.handle_radioachievements()` inline | ❌ inline formatting |
| Room announcements | `dj_announcer.announce_*()` | ❌ builds strings locally |

**Proposed future `radio/renderer.py`** should own:
- `render_now_playing(track)` (move from `track_resolver`)
- `render_queue_card(jobs, current)` (move from `radio_commands`)
- `render_history_lines(jobs)` (extract from `handle_history`)
- `render_leaderboard(rows, label)` (shared by `toplisteners`, `topliked`, `topdisliked`, `toprequesters`)
- `render_achievement_row(cat, idx, val)` (extract from `radio_achievements`)
- `_fmt_secs(secs)` and `_progress_bar(elapsed, total)` (deduplicated)

---

## 6. Dead Code Inventory

| Location | Dead Code | Notes |
|----------|-----------|-------|
| `dj_music.py` | `NullBackend`, `PlaybackBackend` protocol classes | Pre-AzuraCast legacy — never instantiated |
| `dj_music.py` | `_is_duplicate()`, `_normalise()` title logic | Duplicates `track_resolver` approach; diverged |
| `dj_music.py` | 13 deprecated delegate functions | Thin wrappers; can be removed once routing updated |
| `dj_music.py` | All `dj_requests` table queries | Superseded by `yt_request_jobs`; migrations still present |
| `yt_request.py` | "Option A / Option B" staging comments | Old SFTP strategy comments never cleaned up |
| `yt_request.py` | `handle_radio_queue`, `handle_radio_remove`, `handle_radio_clearqueue`, `handle_radio_voteskip` | Aliases that just forward to `radio_commands` equivalents |
| `radio_commands.py` | `_fmt_secs`, `_progress_bar` at module level | Duplicate of `track_resolver` — only used in queue card; should import from `track_resolver` |

---

## 7. Recommended Migration Order

### Phase 2a — Zero-risk extractions (no routing changes required)

1. **`azuracast_controller.py`** → `radio/azuracast.py`  
   No bot logic, pure HTTP/SFTP. Update 3 importers (`radio_commands`, `playback_engine`, `yt_request`).

2. **`music_credits.py`** → `radio/credits.py`  
   Single table, no background tasks, no startup. Update 2 importers.

3. **`track_resolver.py`** → `radio/renderer.py` (seed of future renderer)  
   Pure rendering, no startup. Add deduplicated `_fmt_secs`/`_progress_bar` here; delete local copies in `radio_commands`.

4. **`radio_rewards.py`** → `radio/rewards.py`  
   DB-only, lazy bootstrap, no startup hooks. Update 3 importers.

5. **`radio_achievements.py`** → `radio/achievements.py`  
   No startup, no background tasks. Update 2 importers.

### Phase 2b — Low-risk wrappers

6. **`dj_announcer.py`** → `radio/announcer.py`  
   Pure messaging, safe once importers updated.

7. **`request_queue.py`** → `radio/queue.py`  
   DB-only, no background tasks. Core queue API.

8. **`radio_commands.py`** → `radio/commands.py`  
   Largest refactor surface. Do last in this phase. Requires all above to be completed first.

### Phase 2c — High-risk core (do separately, with regression testing)

9. **`playback_engine.py`** → `radio/playback.py`  
   **Caution:** `_poll_loop` self-starts on import. Must add explicit start/stop lifecycle. Requires guard fix before move.

10. **`yt_request.py`** → `radio/pipeline.py`  
    **Highest risk.** Threads, SFTP, raw sqlite3. Requires threading refactor, db-module alignment. Do last.

### Phase 2d — Cleanup (after all above)

11. **`dj_music.py` cleanup** — remove deprecated delegate functions, dead `NullBackend`/`PlaybackBackend`, `dj_requests` query paths. Convert to pure emote/social/custom-emote host (all non-radio functionality).

---

## 8. Wrapper Strategy

For each move, the safest pattern is:

```python
# Old location: modules/radio_commands.py
# Keep this shim alive until all importers are updated
from radio.commands import *  # re-export everything
```

This means routing in `main.py` doesn't need to change at all during migration. Once all importers are updated to the new path, the shim can be deleted.

For `playback_engine.py`, the shim must also **disable the auto-start** of `_poll_loop`:

```python
# modules/playback_engine.py (shim)
from radio.playback import *
# _poll_loop is now only started explicitly by startup_radio()
```

---

## 9. Proposed Final Structure

```
artifacts/highrise-bot/
├── modules/
│   ├── dj_music.py          # Emote/social/custom-emote only (radio dead code removed)
│   └── (all other non-radio modules unchanged)
│
└── radio/
    ├── __init__.py
    ├── router.py             # Command dispatch table (replaces radio routing in main.py)
    ├── commands.py           # All !play, !queue, !np, ratings, playlists, favorites, shop
    ├── renderer.py           # render_now_playing, queue card, history, leaderboard, _fmt_secs
    ├── playback.py           # AzuraCast poller, skip verification, now-playing state
    ├── pipeline.py           # yt-dlp download, SFTP upload, job lifecycle (from yt_request.py)
    ├── queue.py              # DB-backed job queue (from request_queue.py)
    ├── azuracast.py          # HTTP + SFTP client (from azuracast_controller.py)
    ├── announcer.py          # Room announcement helpers (from dj_announcer.py)
    ├── credits.py            # Request credit wallet (from music_credits.py)
    ├── rewards.py            # Points accounting + leaderboards (from radio_rewards.py)
    ├── achievements.py       # 21 radio achievements (from radio_achievements.py)
    ├── favorites.py          # dj_favorites helpers (extracted from radio_commands.py)
    ├── playlists.py          # radio_playlists + radio_playlist_songs (extracted)
    ├── history.py            # Request history queries + renderer (extracted)
    └── vibe.py               # Vibe switching + AzuraCast playlist management (extracted)
```

---

## 10. Summary Risk Table

| Module | Move Risk | Reason |
|--------|-----------|--------|
| `azuracast_controller.py` | 🟢 Low | No bot logic, no startup, pure API |
| `music_credits.py` | 🟢 Low | Tiny, single-table, no background tasks |
| `track_resolver.py` | 🟢 Low | Pure functions, no startup |
| `radio_rewards.py` | 🟢 Low | Lazy bootstrap, no background tasks |
| `radio_achievements.py` | 🟢 Low | No startup, no background tasks |
| `dj_announcer.py` | 🟡 Medium | Imported by `playback_engine` — must update both together |
| `request_queue.py` | 🟡 Medium | Imported by 4 modules — update all at once |
| `radio_commands.py` | 🟡 Medium | Large surface area; stable API; shim strategy safe |
| `playback_engine.py` | 🔴 High | Self-starts `_poll_loop` on import; must fix lifecycle first |
| `yt_request.py` | 🔴 High | Threads, raw sqlite3, SFTP, download pipeline; highest blast radius |
| `dj_music.py` cleanup | 🟡 Medium | Remove dead code only; radio live paths already delegate |

---

## 11. Immediate Recommendations (Before Any Migration)

These are low-effort fixes that reduce risk before Phase 3 begins:

1. **Guard `playback_engine.py` poll loop** — wrap `_poll_loop` start in an explicit `start_playback_engine(bot)` call rather than auto-starting on import. Add `should_this_bot_run_module("yt_request")` check.

2. **Guard radio imports in `main.py`** — wrap `from modules.yt_request import ...` and `from modules.playback_engine import ...` in a `if _config.BOT_MODE == "dj":` block (or use lazy imports inside the routing `elif` branches) to avoid loading `yt_dlp`/`paramiko`/`paramiko` on all bot instances.

3. **Delete `_fmt_secs` / `_progress_bar` from `radio_commands.py`** — import from `track_resolver` instead. Zero-risk, reduces duplication immediately.

4. **Delete dead `yt_request.py` aliases** (`handle_radio_queue`, `handle_radio_remove`, `handle_radio_clearqueue`, `handle_radio_voteskip`) — they are never called from `main.py` routing.

5. **Delete `NullBackend` / `PlaybackBackend` from `dj_music.py`** — purely dead code, no references outside the file.
