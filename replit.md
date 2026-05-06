# Highrise Hangout Room Bot

A modular Python bot for Highrise that offers casino games, an economy, DJ queue, events, a shop, public profiles, and room utility, all running 24/7.

## Run & Operate

```bash
cd artifacts/highrise-bot && python3 bot.py
```

Required environment variables: `BOT_TOKEN` (or `MAIN_BOT_TOKEN`), `ROOM_ID`.
Optional multi-bot specific environment variables: `HOST_BOT_TOKEN`, `BLACKJACK_BOT_TOKEN`, `POKER_BOT_TOKEN`, `MINER_BOT_TOKEN`, `BANKER_BOT_TOKEN`, `SHOP_BOT_TOKEN`, `SECURITY_BOT_TOKEN`, `DJ_BOT_TOKEN`, `EVENT_BOT_TOKEN` (each with `_ID`, `_MODE`, `_USERNAME` variants).
`SHARED_DB_PATH` (default: `highrise_hangout.db`).

## Stack

- Python 3.11, `highrise-bot-sdk` 25.1.0, `aiohttp`, `sqlite3`
- Entry point: `artifacts/highrise-bot/bot.py`
- Database: `artifacts/highrise-bot/highrise_hangout.db` (SQLite)

## Where things live

```
artifacts/highrise-bot/
├── bot.py              # Runner / Multi-bot orchestrator
├── main.py             # Core bot logic, command routing
├── config.py           # Central configuration
├── database.py         # SQLite schema, migrations, helpers
└── modules/            # Individual bot features
    ├── blackjack.py        # Blackjack game
    ├── realistic_blackjack.py  # Realistic Blackjack game
    ├── poker.py            # Poker game
    ├── profile.py          # Player profiles
    ├── admin_cmds.py       # Admin commands
    ├── dj.py               # DJ queue system
    ├── economy.py          # Token economy
    ├── cards.py            # Shared card utilities
    ├── casino_settings.py  # Casino configuration
    ├── subscribers.py      # Subscription system
    ├── badge_market.py     # Emoji badge marketplace
    ├── bot_modes.py        # Bot persona/outfit system
    ├── multi_bot.py        # Multi-bot management
    ├── room_utils.py       # Room utility commands
    ├── mining.py           # Mining game
    └── assistant.py        # Personal Assistant AI (rule-based intent router)
```

DB schema source of truth: `database.py` (`_MIGRATIONS` list + `init_db()`).

## Architecture decisions

- **Simultaneous action model for BJ/RBJ**: Players act freely during a shared `asyncio.Task` timer.
- **RBJ shoe persistence**: The `_Shoe` object is serialized to JSON in `rbj_game_state` and restored on recovery.
- **DB migrations**: An append-only `_MIGRATIONS` list in `database.py` handles schema changes with idempotent `ALTER TABLE` statements.
- **Bot modes**: A system of `bot_modes` and `bot_mode_assignments` tables allows for different bot personas and message prefixes.
- **Multi-bot system**: Features `bot_instances` for heartbeats, `bot_command_ownership` for command routing, and `bot_module_locks` for preventing race conditions in games. Auto-game ownership is managed via `autogames_owner_bot_mode` room setting.
- **Module ownership guard**: `should_this_bot_run_module(module)` in `multi_bot.py` gates all startup recovery calls and room announce messages — only the owning bot mode runs them. `_MODULE_OWNER_MODES` defines the mapping (poker→poker, blackjack→blackjack/rbj, events→eventhost, etc.). Dedupe lock table `module_announcement_locks` provides a 5-minute cross-bot dedupe safety net via `db.acquire_module_announce_lock()`.
- **Personal Assistant AI**: Rule-based intent router in `modules/assistant.py`. Only Host Bot (`BOT_MODE=="host"`) processes natural language. Wake phrases trigger intent detection → existing handler dispatch. Safety levels: 1=SAFE (execute), 2=STAFF (suggest in strict mode, execute in assistant/autopilot mode), 3=DANGEROUS (always confirm with 4-digit code). DB tables: `ai_settings`, `ai_action_logs`, `ai_pending_actions`. No background loops. All wrapped in try/except.

## Product

Casino games (Blackjack, Realistic Blackjack, Poker), DJ queue, token economy, daily rewards, in-game shop, quests, achievements, events, subscriber DMs, leaderboards, staff management, public player profiles with privacy controls, emoji badge market, mining game, a comprehensive room utility system, a bot mode/outfit system with multiple personas, and a multi-bot system for distributed command handling and high availability. It also includes a Casino Integrity Checker for verifying game logic and card visibility, and a Personal Assistant AI that responds to natural-language wake phrases.

**Poker upgrade (fault-safe spec):** Leave-room auto-fold, 3-strike AFK tracking with auto-removal/refund, speed modes (`/pokermode`), buy-in/stakes modes (`/pokerstakes`), rule modes (`/pokerrules`), AI simulation (`/poker ai`), owner debug card reveal, AFK remove/sitout toggles, 8-page help. DB tables: `poker_player_presence`, `poker_afk_tracking`, `poker_ai_logs`, `poker_debug_logs`.

**Poker pause/resume/waitlist/tablelock/spectate/notes spec:** `/poker pause` (⏸️ sets `poker_paused=true`, blocks betting actions), `/poker resume` (▶️ with state validation), `/poker tablelock on|off` (🔒/🔓, blocks joins, notifies waitlist next), `/poker waitlist [buyin]` + `/waitpoker` + `/leavewaitlist` (queue with auto-notify), `/poker spectate on|off` + `/spectatepoker` + `/spectators` (watch-only, no hole cards), `/poker notes|addnote|clearnotes [user]` (staff note log). Auto-notes logged for AFK removal, leave-room, hardrefund, clearhand, closeforce, cleanup. DB tables: `poker_waitlist`, `poker_spectators`, `poker_notes`. Settings: `poker_paused`, `poker_table_locked`, `poker_waitlist_enabled`, `poker_spectate_enabled`.

## User preferences

- All chat messages must be ≤ 249 characters.
- New settings commands follow pattern `/setbj<thing>` / `/setrbj<thing>` and are manager-only.
- Short aliases preferred for in-room play; full `/bj <sub>` commands still supported.
- Rep rank cap: Celebrity (500+). No "Legend" rep rank. Level rank Legend = 50+.

## SAFE_BOOT system (applied)

**SAFE_BOOT is ON by default.** All background loops are disabled at startup. Bots connect, heartbeat, and handle commands — nothing else runs until explicitly enabled.

- `config.SAFE_BOOT`: env var `SAFE_BOOT=true` (default). Set `SAFE_BOOT=false` to disable.
- `safeboot` DB setting: checked on every `on_start`. Both must be `false` for loops to run.
- `on_start` fully crash-proofed: each step in its own try/except; `[BOT START]`/`[BOT ONLINE]`/`[TASK START]`/`[TASK SKIP]`/`[BOT CRASH]` console logging.
- `on_user_join` DB errors gracefully caught — no longer crashes the bot.

**Safe boot commands (admin/owner only):**
- `/safeboot on|off|status` — toggle safe boot; restart recommended after change
- `/recoverbots` — mark stale bot_instances offline, clear expired locks, fix room presence
- `/enablepokerloops` — turn on poker AFK tracking + leave-fold (poker bot/manager)
- `/enableautogames` — turn on autogames loop (event bot/manager)
- `/enablewelcomeintervals` — turn on welcome/interval messages (host bot/manager)
- `/enablebotspawn` — turn on bot auto-spawn on startup (admin only)

**Crash-safe poker fix (applied):**
- `poker_leaveremove_enabled=false` gates leave-fold; `poker_afk_enabled` reset to `false`.
- `on_user_leave` guarded by `should_this_bot_run_module("poker")`.
- `/poker leaveremove on|off`, `/poker safemode`, `/emergencystop`, `/roomcount`, `/fixroomcount`.

## Gotchas

- Never hardcode ports; the bot uses Highrise WebSocket.
- The `_MIGRATIONS` list in `database.py` is append-only; do not reorder or remove entries.
- `doubled` DB column in BJ/RBJ is repurposed as `active_hand_idx`; `bet` stores `total_bet()` sum.
- Use `cards.py` for `make_shoe` to ensure consistent card deck generation.
- Usernames for privacy settings and badge/room/social tables are stored lowercase.
- Seeding functions (`seed_emoji_badges`, `seed_mining_items`, etc.) use `INSERT OR IGNORE` in `_migrate_db()`.
- New `room_mutes`/`room_bans`/`room_warnings` tables are separate from older moderation tables.
- The `_user_positions` dictionary in `room_utils.py` is volatile and resets on bot restart.
- `get_connection()` now enables WAL mode + busy_timeout=10000ms + connection timeout=20s. Use `_execute_with_retry()` for hot-path writes.
- `safe_mode_enabled=true` skips autogames, interval, and emote loops at startup. Use `/safemode on` if bots keep crashing.
- **SAFE_BOOT=true (env) + safeboot=true (DB)** — both must be `false` for background loops to start. Change DB setting with `/safeboot off` then restart.
- All startup tasks in `on_start` are individually try/except wrapped — one task failure never kills the bot.
- `on_user_join` `ensure_user` DB errors are caught and logged — never crash the bot on join storm.
- Duplicate tokens and bot_ids are now detected and skipped in `bot.py` before launching subprocesses.
- `_is_mode_online(BOT_MODE)` in `multi_bot.py` always returns `True` (self-online guard) — a bot never marks itself offline due to its own heartbeat DB failure.
- `_db_init_complete` flag in `main.py` gates `ensure_user` writes until host's `init_db()` finishes, preventing init_db vs ensure_user deadlock.
- `init_db()` is host-only; all other bots do a lightweight read-verify at startup (`[DB INIT] X DB verified`).
- `ensure_user` (on_user_join) and `auto_subscribe_whisper` (on_whisper) are gated to host/all only and to `_db_init_complete=True`.
- Heartbeat writes are staggered per mode (host=0s, banker=3s, blackjack=6s, poker=9s, dj=12s, miner=15s, shop=18s, security=21s, event=24s) + random jitter.

## Pointers

- Adding a new room setting: Use `db.set_room_setting(key, value)` and `db.get_room_setting(key, default)`.
- Adding a new bot mode: `INSERT INTO bot_modes` or use `/createbotmode`.
- Adding a new module: Create `modules/yourmodule.py`, import in `main.py`, add routing in `on_chat()`, and add to `ALL_KNOWN_COMMANDS`.
- To prefix a bot message by category: Import `format_bot_message` from `modules.bot_modes` and call `format_bot_message(msg, "category")`.
- To manage multi-bot command ownership: Edit `_DEFAULT_COMMAND_OWNERS` in `multi_bot.py` or use `/setcommandowner`.
- To guard a new module's startup tasks: call `should_this_bot_run_module("modulename")` before any `asyncio.create_task(startup_...)` in `on_start`. Add the module to `_MODULE_OWNER_MODES` in `multi_bot.py`.
- New task/restore commands: `/taskowners`, `/activetasks`, `/taskconflicts`, `/fixtaskowners`, `/restoreannounce on|off`, `/restorestatus` (all admin/owner-only).
- Poker AFK/mode/stakes/rules settings are stored in `room_settings` via `db.get_room_setting()` / `db.set_room_setting()`.
- `poker_player_presence` tracks join/leave timestamps; `poker_afk_tracking` tracks strike counts per hand.
- `/poker ai` toggles `_ai_sim_state["enabled"]`; AI hands are logged to `poker_ai_logs` table.
- Crash logs: `db.log_bot_crash()` writes to `bot_crash_logs`. View with `/crashlogs`, clear with `/clearcrashlogs`.
- Safe mode: `/safemode on` sets `safe_mode_enabled=true` and disables autogames/spawn/outfit/emote/interval startup loops.
- New startup defaults (all `false`): `module_startup_announce_enabled`, `autogames_enabled`, `bot_auto_spawn_enabled`, `outfit_auto_apply_enabled`, `emote_loops_enabled_on_startup`, `safe_mode_enabled`.
- Pause/resume checks `poker_paused` room_setting. Betting actions blocked while paused.
- `poker_settings` page 1 now shows Mode/Stakes/Paused/Lock; page 2 shows Waitlist/Spectate/AFK/All-in.
- Waitlist helpers: `db.add_poker_waitlist()`, `db.get_poker_waitlist()`, `db.cancel_poker_waitlist()`, `db.get_poker_waitlist_next()`.
- Spectator helpers: `db.add_poker_spectator()`, `db.remove_poker_spectator()`, `db.get_poker_spectators()`, `db.is_poker_spectator()`.
- Note helpers: `db.add_poker_note()`, `db.get_poker_notes()`, `db.clear_poker_notes()`.
- AI assistant settings: `db.ai_get_setting(key, default)` / `db.ai_set_setting(key, value)`.
- AI log helpers: `db.ai_get_logs(username_key, limit)` / `db.ai_clear_logs()`.
- AI pending actions: `db.ai_create_pending()`, `db.ai_get_pending(code)`, `db.ai_confirm_pending(code)`, `db.ai_cancel_pending(code)`.
- AI commands (all owned by host bot): `/assistant on|off`, `/assistantstatus`, `/aimode [strict|assistant|diagnostic|autopilot]`, `/aisettings`, `/aiset <key> <value>`, `/ailogs [user]`, `/clearailogs`, `/aiintegrity [full]`, `/confirmai <code>`, `/cancelai <code>`, `/assistanthelp [2]`.
- Natural language: say "Host, help me" / "Banker, balance" / "Poker bot, show table" / "Miner, ores" — Host Bot only. Dangerous actions (ban, kick, poker closeforce, etc.) require `confirm CODE` reply within 2 minutes.