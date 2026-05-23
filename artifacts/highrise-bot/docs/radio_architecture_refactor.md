# Radio Architecture Refactor Notes

Scope: docs-only Phase 1. This file records the intended radio ownership model
before code refactors begin. It does not change runtime behavior.

Priority: radio stability first. Keep each follow-up change small enough to
review, test, revert, and ship independently.

## Current Ownership Map

- `main.py`: Runtime shell, startup wiring, and command dispatch. Radio imports
  are DJ-bot guarded. Target state is thin routing only.
- `multi_bot.py`: Bot ownership, module routing, heartbeat, and fallback
  decisions. It should decide which bot handles a command, not radio lifecycle.
- `modules/radio_commands.py`: Player and staff radio command UX. It should
  render messages, validate command input, and call service modules.
- `modules/request_queue.py`: Source of truth facade for request rows in
  `yt_request_jobs`. It should own queue reads, inserts, cancellation, and
  queue-facing status normalization.
- `modules/yt_request.py`: YouTube request preparation, download, upload,
  and AzuraCast request submission. It should not own long-term playback
  lifecycle once a job is queued.
- `modules/local_replay.py`: Local favorite replay preparation and temp-file
  upload staging. It should not mark a request played or clean files before
  playback is confirmed.
- `modules/playback_engine.py`: Now Playing polling, request matching,
  lifecycle transitions, skip consumption, refund-on-failure decisions, and
  confirmed cleanup.
- `modules/azuracast_controller.py`: AzuraCast REST and SFTP calls only. It
  should not make database lifecycle decisions.
- `modules/payment_service.py`: Coin charge and refund operations.
- `modules/music_credits.py`: Song credit consume and refund operations.
- `database.py`: Schema and migration owner for shared tables.

## Refactor Guardrails

- Do not touch emotes, socials, dancefloor, sync, games, mining, fishing, or
  help pages for radio refactors.
- AzuraCast controls playback order. The bot creates requests, tracks
  lifecycle, displays queue state, refunds failures, and cleans up only after
  confirmed playback.
- `!q` and `!queue` must remain display-only. They must not mutate database
  state, mark songs played, refund, or clean temp files.
- Refunds must be idempotent before deeper cleanup or cancel/remove changes.
- Do not remove legacy cleanup behavior until `playback_engine.py` clearly owns
  confirmed playback cleanup and tests cover the handoff.
- Local replay timeout is not the same as failure. Pending local replays must
  survive AutoDJ song changes until playback is confirmed or the request is
  explicitly failed.

## Safe Edit Set

Radio refactor work should stay inside these files unless a small shared helper
is truly required:

- `modules/radio_commands.py`
- `modules/request_queue.py`
- `modules/playback_engine.py`
- `modules/local_replay.py`
- `modules/azuracast_controller.py`
- `modules/yt_request.py`
- `modules/payment_service.py`
- `modules/music_credits.py`
- `database.py`
- `artifacts/highrise-bot/docs/*`

## Do Not Touch Set

- Emote systems
- Social systems
- Dancefloor systems
- Sync systems
- Games
- Mining
- Fishing
- Help pages

## PR-Sized Phase Order

| Phase | Goal | Expected Risk |
| --- | --- | --- |
| 1 | Record radio ownership and refactor guardrails in docs only. | Very low |
| 2 | Add shared radio status constants without changing behavior. | Low |
| 3 | Migrate queue reads to shared status constants. | Low |
| 4 | Move lazy radio schema creation into `database.py`. | Medium |
| 5 | Add a single idempotent refund guard. | Medium |
| 6 | Route cancel/remove cleanup through one queue API. | Medium |
| 7 | Make `yt_request.py` cleanup passive after handoff. | High |
| 8 | Move radio command routing toward a registry. | Medium |
| 9 | Retire player-facing `!playfavlocal`. | Medium |
| 10 | Quarantine or remove overlapping legacy DJ music behavior. | High |

## Phase 1 Verification

Commands:

```bash
git diff --check
git status --short --untracked-files=all
```

Optional HighRise smoke check:

- `!q`
- `!queue`
- `!play <song>`
- `!playfav 1`
- Staff `!skip` during a request

Expected result: no runtime behavior changes.

## Rollback Plan

Revert the Phase 1 docs commit. Since Phase 1 is documentation-only, rollback
does not require database migration, service restart ordering, or cache cleanup.
