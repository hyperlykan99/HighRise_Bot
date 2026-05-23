# Phase 8 Radio Legacy Isolation Report

Scope: read-only architecture audit plus documentation. No runtime behavior was
changed.

Goal: isolate old `dj_music.py` / legacy DJ queue behavior from the current
AzuraCast radio request pipeline before any deletion or migration work.

## Current Architecture Map

### Current AzuraCast Radio Pipeline

- `main.py`
  - Imports radio modules only for `BOT_MODE in ("dj", "all")`.
  - Preserves the multi-bot guard: non-DJ bots ignore commands in `DJ_COMMANDS`.
  - Delegates core radio commands to `modules.radio_command_registry`.
  - Still routes many secondary radio/DJ aliases through the old elif chain.
- `modules/radio_command_registry.py`
  - Registry-dispatches core live commands:
    - `!play`, `!request`, `!sr`, `!req`, `!song`, `!requesy`
    - `!playfav`
    - `!queue`, `!q`, `!djqueue`
    - `!skip`, `!djskip`
    - `!remove`, `!djremove`, `!radioremove`
    - `!cancel`
    - `!radiohelp`
- `modules/radio_commands.py`
  - Player/staff UX for the AzuraCast request system.
  - Uses `modules.request_queue` as the queue facade.
  - Owns favorites, ratings, queue display, playfav, staff skip/remove, and user cancel.
- `modules/request_queue.py`
  - Formal lifecycle API for `yt_request_jobs`.
  - Owns active queue reads, lifecycle writes, queue cancel, and DB-backed queue state.
- `modules/yt_request.py`
  - YouTube search/download/upload/prepare worker.
  - Still contains some old compatibility/admin handlers.
  - Cleanup loop is passive after Phase 4.
- `modules/playback_engine.py`
  - Owns Now Playing matching, played transitions, skip consumption, and cleanup.
- `modules/local_replay.py`
  - Prepares local/Azura media replay and stages it as `yt_request_jobs`.
- `modules/azuracast_controller.py`
  - AzuraCast REST/SFTP wrapper.

### Legacy DJ/Music Pipeline

- `modules/dj_music.py`
  - Separate queue engine using `dj_requests`.
  - Separate state machine: `pending -> playing -> played | skipped | cancelled`.
  - Separate queue/search/favorites/ratings/report/ban/settings helpers.
  - `NullBackend` remains the active playback backend.
  - Some handlers are still live through `main.py`.
  - Some handlers are deprecated wrappers that delegate to AzuraCast radio modules.

## Dependency Map

### Live AzuraCast Request Tables

- `yt_request_jobs`
  - Read/write owner: `request_queue.py`.
  - Pipeline writers: `yt_request.py` through request_queue helpers.
  - Playback owner: `playback_engine.py`.
  - Local replay staging: `local_replay.py` through `request_queue.create_request`.

### Legacy DJ Tables

- `dj_requests`
  - Owner: `dj_music.py`.
  - Used by legacy queue, stats, reports, health, cancel request, dedication,
    priority queue, and debug commands.
- `dj_bans`, `dj_song_bans`, `dj_reports`, legacy DJ favorites/ratings/settings
  - Owner: `dj_music.py`.
  - Still queried by live legacy commands routed from `main.py`.

### Routing Ownership

- `DJ_COMMANDS` in `main.py` remains the multi-bot ownership guard.
- `radio_command_registry.py` owns only core AzuraCast radio commands today.
- `main.py` still owns secondary radio/DJ routing after registry dispatch.
- Handler-level permissions remain inside handlers.

## Overlap And Conflict Points

### Duplicate Command Meanings

- `!request`, `!play`, `!queue`, `!skip`, `!remove`, `!cancel`
  - Current live behavior: registry -> `radio_commands.py` / `request_queue.py`.
  - Legacy equivalents still exist in `dj_music.py` but are no longer primary for these aliases.
- `!pick`
  - Current behavior is hybrid:
    - If a YouTube/radio pending search exists, use `radio_commands.handle_pick`.
    - Otherwise fall back to `dj_music.handle_dj_pick`.
  - This is a deliberate compatibility bridge and should not be deleted yet.
- `!ytrequest`, `!ytqueue`, `!ytstatus`, `!ytnow`, `!ytcooldown`, `!setytcooldown`
  - Legacy YouTube-specific interface remains in `yt_request.py`.
  - Some are aliases or admin/debug views over the current pipeline.
- `!djclear`, `!radioclear`, `!clearqueue`
  - `!clearqueue` uses AzuraCast queue clearing through `radio_commands.py`.
  - `!djclear` / `!radioclear` route to `dj_music.handle_dj_clear`, which now delegates to `request_queue.queue_clear_all`.
- `!myrequests`
  - Live route uses `radio_commands.handle_myrequests`.
  - `dj_music.handle_dj_myrequests` is marked deprecated and delegates to the radio handler.

### Duplicate Data Models

- Current request queue: `yt_request_jobs`.
- Legacy queue: `dj_requests`.
- Risk: commands like `!djhealth`, `!djstats`, `!priorityqueue`, `!moveup`,
  `!bump`, `!dedicate`, `!djreport`, `!songinfo`, and some ban/report tools can
  still talk about `dj_requests`, not the AzuraCast queue.

### Duplicate State Machines

- Current: AzuraCast controls order; bot tracks lifecycle through `request_queue`
  and `playback_engine`.
- Legacy: `dj_music.py` promotes rows internally and calls a `PlaybackBackend`.
- Risk: any future command wired to legacy promotion helpers can drift from
  AzuraCast reality.

### Duplicate Settings

- Current radio settings use request/radio room settings such as request cost,
  priority cost, queue limit, vibe, AzuraCast config, and cleanup flags.
- Legacy DJ settings include `dj_queue_max`, `dj_cooldown_secs`, `dj_user_max`,
  `dj_skipvote_threshold`, `dj_lock`, `dj_repeat`, `dj_autoplay`,
  `dj_priority_price`, and legacy radio URLs.
- Risk: users/staff can see or change legacy DJ settings that no longer affect
  the main AzuraCast request path.

## Dead Code Candidates

Do not delete these yet. Marked candidates need one migration phase and one
release cycle with command telemetry or manual smoke testing.

### Likely Dead Or Quarantinable

- `dj_music.PlaybackBackend`, `NullBackend`, `_backend`, and `set_playback_backend`
  - Reason: current architecture uses AzuraCast, not a bot playback backend.
  - Risk if removed now: `!djcheck` and legacy diagnostics reference `_backend`.
- Legacy queue state helpers over `dj_requests`:
  - `_pending_count`, `_user_pending_count`, `_user_cooldown_secs`,
    `_remove_request_by_pos`, `_add_request`, `_get_queue`, `_promote_front`,
    `_advance_queue`, `_clear_queue`, `_total_active`.
  - Reason: current request queue is `yt_request_jobs`.
  - Risk if removed now: several live legacy `handle_dj_*` commands still call them.
- Legacy search/session helpers in `dj_music.py`:
  - `_yt_search_sync`, `_store_search`, `_pop_search`, `_peek_search`,
    `_search_request_type`.
  - Reason: current search path is `radio_commands.py` -> `request_queue.py` -> `yt_request.py`.
  - Risk if removed now: `!pick` still falls back to `handle_dj_pick` when no radio search exists.
- Legacy favorites/ratings helpers in `dj_music.py`.
  - Reason: current favorites/ratings are in `radio_commands.py`.
  - Risk if removed now: some legacy `handle_dj_*` wrappers may still reference them.

### Already Compatibility-Oriented

- `dj_music.handle_dj_stopmusic`
  - Marked deprecated and delegates to `handle_dj_clear`.
- `dj_music.handle_dj_myrequests`
  - Marked deprecated and delegates to `radio_commands.handle_myrequests`.
- `yt_request.startup_yt_cleanup_task`
  - Passive compatibility hook after Phase 4.
- `yt_request._cleanup_loop`
  - Passive compatibility no-op after Phase 4.
- `request_queue.insert_request_job`
  - Backward-compatible alias for `create_request`.
- `request_queue.mark_played_if_unplayed`, `mark_playing_if_not_terminal`,
  `set_playback_status`, `mark_failed_if_unplayed`
  - Compatibility helpers retained under the formal lifecycle API.

## Duplicate Handler Candidates

### Registry-Owned Core Commands

These command branches were moved out of `main.py` fallback routing in Phase 7
and are now registry-owned:

- `!play`, `!request`, `!sr`, `!req`, `!song`, `!requesy`
- `!playfav`
- `!queue`, `!q`, `!djqueue`
- `!skip`, `!djskip`
- `!remove`, `!djremove`, `!radioremove`
- `!cancel`
- `!radiohelp`

Safe next step: expand the registry to cover more live AzuraCast radio commands,
then remove their `main.py` branches in small batches.

### Legacy Handlers That Overlap Current Radio

- `dj_music.handle_dj_request`
  - Overlaps current `radio_commands.handle_request`.
  - Not routed directly by `main.py` anymore for `!play`/`!request`.
- `dj_music.handle_dj_queue`
  - Overlaps current `radio_commands.handle_queue`.
  - Not routed directly by `main.py` for `!queue`.
- `dj_music.handle_dj_skip`
  - Overlaps current `radio_commands.handle_skip`.
  - Not routed directly by `main.py` for `!skip`.
- `dj_music.handle_dj_remove`
  - Overlaps current `radio_commands.handle_remove`.
  - Not routed directly by `main.py` for `!remove`.
- `yt_request.handle_radio_queue`, `handle_radio_remove`,
  `handle_radio_clearqueue`, `handle_radio_voteskip`, `handle_radiohelp`
  - Old radio command handlers inside `yt_request.py`.
  - Main routing uses `radio_commands.py` versions, not these.
  - Safe-to-delete candidates after confirming no direct imports.

## Unreachable Or Legacy-Only Paths

### Likely Unreachable From `main.py`

- `dj_music.handle_dj_request`
- `dj_music.handle_dj_queue`
- `dj_music.handle_dj_nowplaying`
- `dj_music.handle_dj_skip`
- `dj_music.handle_dj_skipvote`
- `dj_music.handle_dj_remove`
- `yt_request.handle_radio_queue`
- `yt_request.handle_radio_remove`
- `yt_request.handle_radio_clearqueue`
- `yt_request.handle_radio_voteskip`
- `yt_request.handle_radiohelp`

These are not guaranteed safe to delete until import/reference checks are run in
the deletion phase and any docs/tests are updated.

### Live Legacy-Only Routes

These still route from `main.py` into `dj_music.py` and should be preserved until
replaced or intentionally retired:

- `!pick` fallback through `handle_dj_pick`
- `!stopmusic`, `!djstop`
- `!djconfig`, `!djsettings`, `!djset`, `!djdebug`
- `!djannounce`, `!announcequeue`
- `!djlimits`, `!setrequestcooldown`, `!setmaxuserqueue`, `!setmaxqueue`
- `!djcleanup`
- `!djlock`, `!radiolock`
- `!djclear`, `!radioclear`
- `!radio`, `!djstatus`, `!djhistory`, `!toprequests`, `!upnext`, `!djstats`
- `!repeat`, `!shuffle`, `!autoplay`, `!djvibes`
- `!djprice`, `!setdjprice`
- `!priorityrequest`, `!priorityreq`, `!pr`, `!viprequest`, `!vipreq`
- `!tipdj`, `!djleaderboard`, `!djtop`, `!priorityqueue`, `!pqueue`
- `!moveup`, `!bump`, `!dedicate`, `!shoutout`
- `!djban`, `!djunban`, `!djbanlist`, `!songban`, `!songunban`, `!songbanlist`
- `!djreport`, `!djreports`
- `!setradio`, `!radiostatus`, `!radioconfig`, `!setradiotype`,
  `!setradiomount`, `!setradiometadata`
- `!webplayer`, `!setwebplayer`, `!nowpage`, `!setnowpage`
- `!songinfo`, `!recent`, `!cancelrequest`, `!requeststatus`
- `!djcheck`, `!djhealth`, `!djresetstate`, `!djbackup`, `!djtestall`, `!djhelp`

## Compatibility Layer Map

- `main.py`
  - Compatibility fallback remains for DJ/radio commands not yet migrated to
    the registry.
  - Non-DJ guard remains before registry dispatch.
- `radio_command_registry.py`
  - New registry for core live commands.
  - Should become the only route for live AzuraCast radio commands over time.
- `dj_music.py`
  - Mix of live legacy commands, deprecated wrappers, and old queue internals.
  - Treat as quarantine module until each route is migrated or retired.
- `yt_request.py`
  - Keep YouTube prep and admin request tools.
  - Quarantine old `handle_radio_*` command handlers and passive cleanup loop.
- `request_queue.py`
  - Compatibility aliases exist to avoid breaking older call sites.

## Safe-To-Delete Candidates

Safe means "safe candidate after a dedicated deletion PR", not safe to remove in
Phase 8.

### First Candidate Group: Unrouted Old `yt_request` Radio Handlers

- `yt_request.handle_radio_queue`
- `yt_request.handle_radio_remove`
- `yt_request.handle_radio_clearqueue`
- `yt_request.handle_radio_voteskip`
- `yt_request.handle_radiohelp`

Pre-delete checks:

- `rg "handle_radio_queue|handle_radio_remove|handle_radio_clearqueue|handle_radio_voteskip|handle_radiohelp"`
- Compile `yt_request.py`, `radio_commands.py`, `main.py`.
- Smoke `!q`, `!remove`, `!clearqueue`, `!voteskip`, `!radiohelp`.

### Second Candidate Group: Passive Cleanup Compatibility

- `yt_request._cleanup_loop`
- `yt_request.startup_yt_cleanup_task`

Pre-delete checks:

- Update `media_cleanup.py` startup wiring first.
- Confirm `playback_engine.startup_playback_engine` starts independently.
- Smoke bot startup on DJ mode.

### Third Candidate Group: Unrouted Legacy DJ Core Queue Handlers

- `dj_music.handle_dj_request`
- `dj_music.handle_dj_queue`
- `dj_music.handle_dj_nowplaying`
- `dj_music.handle_dj_skip`
- `dj_music.handle_dj_skipvote`
- `dj_music.handle_dj_remove`

Pre-delete checks:

- Confirm no routes/imports/reference these names.
- Confirm all equivalent commands are registry-owned.
- Preserve `handle_dj_pick` until `!pick` fallback is migrated.

### Fourth Candidate Group: Legacy `dj_requests` Internals

- `_add_request`, `_get_queue`, `_promote_front`, `_advance_queue`,
  `_clear_queue`, `_remove_request_by_pos`, `_total_active`, and helpers that
  exist only to support deleted handlers.

Pre-delete checks:

- Build a call graph with `rg`.
- Delete only when no live command uses the helper.
- Do not delete tables or migrations in the same PR.

## Recommended Migration Order

1. Expand `radio_command_registry.py` to all AzuraCast radio commands already
   implemented in `radio_commands.py`.
   - Example batch: `!now`, `!np`, `!clearqueue`, `!voteskip`, `!history`,
     `!vibe`, `!vibes`, `!vibescan`, `!queuelimit`, `!setqueuelimit`.
   - Risk: low if handler mapping is exact.

2. Remove matching `main.py` fallback branches after each registry batch.
   - Keep the `DJ_COMMANDS` non-DJ guard.
   - Keep old branches for commands not yet registry-owned.

3. Migrate `!pick` fully to radio registry with an explicit compatibility
   dispatcher.
   - Preserve current behavior: radio pending search first, legacy DJ pick
     fallback second.
   - After this, mark `dj_music.handle_dj_pick` for quarantine.

4. Move secondary AzuraCast admin/debug commands out of `dj_music.py` where they
   are really radio settings.
   - Candidates: `setradio`, `radiostatus`, `radioconfig`, `setradiotype`,
     `setradiomount`, `setradiometadata`, `webplayer`, `nowpage`.
   - Risk: medium because user-facing admin behavior may depend on existing
     wording.

5. Delete unrouted old `yt_request.handle_radio_*` handlers.
   - Keep YouTube prep pipeline and admin tools.

6. Quarantine `dj_music.py` under a legacy namespace or comment banner.
   - Goal: no new AzuraCast work should be added there.

7. Retire live legacy commands one category at a time.
   - Queue state commands first, then stats/reporting, then legacy settings.
   - Keep aliases alive by routing them to current radio equivalents where
     possible.

8. Only after all live commands stop reading `dj_requests`, plan database
   archival.
   - Do not drop tables immediately.
   - Keep read-only admin export/history for one release window.

## Files Safe To Edit For Isolation Work

- `main.py`
- `modules/radio_command_registry.py`
- `modules/radio_commands.py`
- `modules/request_queue.py`
- `modules/yt_request.py`
- `modules/dj_music.py`
- `modules/playback_engine.py` only when routing/lifecycle ownership requires it
- `docs/*`

## Files Not To Touch For This Work

- Emote systems
- Social systems
- Dancefloor systems
- Sync systems
- Games
- Mining
- Fishing
- Help pages unrelated to radio/DJ routing

## Phase 8 Result

No code was changed. The next implementation phase should be a small registry
expansion PR, not deletion. Deletion should begin only after each candidate group
has a focused reference check and HighRise smoke checklist.
