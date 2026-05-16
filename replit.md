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
    ├── mining.py           # Mining game (7 rarities incl. Prismatic/Exotic, 90+ ores)
    ├── mining_colors.py    # Rarity color labels & rainbow/prismatic formatting
    ├── mining_weights.py   # Ore weight system, weight LB, announce settings
    ├── bot_welcome.py      # Per-bot configurable welcome whispers (D)
    ├── gold_tips.py        # Gold tip migration to BankingBot (E)
    ├── time_exp.py         # Time-EXP system incl. bot exclusion (/setallowbotxp) (C)
    ├── cmd_audit.py        # Command audit tools incl. commandtestall/group (B)
    ├── first_find.py       # BankingBot first-rarity reward system (gold/coin prizes per rarity)
    └── big_announce.py     # Global big-find/big-catch room announcement routing
```

DB schema source of truth: `database.py` (`_MIGRATIONS` list + `init_db()`).

## Architecture decisions

- **Simultaneous action model for BJ/RBJ**: Players act freely during a shared `asyncio.Task` timer.
- **RBJ shoe persistence**: The `_Shoe` object is serialized to JSON in `rbj_game_state` and restored on recovery.
- **DB migrations**: An append-only `_MIGRATIONS` list in `database.py` handles schema changes with idempotent `ALTER TABLE` statements.
- **Bot modes**: A system of `bot_modes` and `bot_mode_assignments` tables allows for different bot personas and message prefixes.
- **Multi-bot system**: Features `bot_instances` for heartbeats, `bot_command_ownership` for command routing, and `bot_module_locks` for preventing race conditions in games. Auto-game ownership is managed via `autogames_owner_bot_mode` room setting.
- **Module ownership guard**: `should_this_bot_run_module(module)` in `multi_bot.py` gates all startup recovery calls and room announce messages — only the owning bot mode runs them. `_MODULE_OWNER_MODES` defines the mapping (poker→poker, blackjack→blackjack/rbj, events→eventhost, etc.). Dedupe lock table `module_announcement_locks` provides a 5-minute cross-bot dedupe safety net via `db.acquire_module_announce_lock()`.
- **AutoMine/AutoFish persistence**: Active sessions are written to `auto_mine_sessions` / `auto_fish_sessions` tables. On restart, `startup_automine_recovery` / `startup_autofish_recovery` (owned by miner/fisher bots respectively) wait 5s, fetch live room users, then resume sessions for players still present. Players who left are marked `restart_not_in_room`.
- **BJ pair bonus**: On deal, if the player's first two cards form a pair, a bonus is paid: pair (10%), color pair (25%), perfect pair (50%), all capped by `bj_bonus_cap`. Settings stored in `bj_settings` table; configurable via `/setbjbonus on|off`, `/setbjbonuscap <coins>`, `/setbjbonuspair/color/perfect <pct>`.
- **BJ card colors + post-game whisper**: Cards display with suit-color symbols (♥♦ red, ♠♣ black). Action-phase whisper shows colored hand + dealer upcard + current bet. Post-game whisper shows full colored hand vs dealer hand + net result.
- **Mining energy removed**: Energy system fully stripped from mining. All energy-gated paths, the energy shop item, and energy display in `/mineprofile`/`/minepanel` are gone. Existing energy purchases are auto-refunded.
- **Rarity difficulty rescale**: Mining exotic ~1-in-2.5M (0.004%), prismatic ~1-in-5k (0.02%). Fishing exotic ~1-in-40k (0.0025%), rare weight 2.0. Scales reflect genuine rarity without being unreachable.
- **First-find rewards**: `first_find.py` tracks the first player per rarity per reset to find/catch that tier. BankingBot issues configurable gold or coin rewards; other bots send a congratulatory whisper only. Claims stored in `first_find_claims`; reward config in `first_find_rewards`.
- **Big announce routing**: `big_announce.py` routes room-wide announcements for exceptional drops (mining + fishing) through configurable thresholds (`big_announce_threshold`, `big_announce_bot_react_threshold`). Bot reactions (emote/wave) only fire above the react threshold.

## Product

Casino games (Blackjack, Realistic Blackjack, Poker), DJ queue, token economy, daily rewards, in-game shop, quests, achievements, events, subscriber DMs, leaderboards, staff management, public player profiles with privacy controls, emoji badge market, mining game (7 rarities: Common/Uncommon/Rare/Epic/Legendary/Mythic/Ultra-Rare/Prismatic/Exotic with 90+ ores, per-ore weight system, weight leaderboards, configurable rare-ore room announcements, `/minepanel` staff dashboard), per-bot personalized welcome whispers (configurable per bot), gold tip migration to BankingBot (coins_per_gold conversion, event log, dedup), Time EXP bot exclusion (`/setallowbotxp`), a comprehensive room utility system, a bot mode/outfit system with multiple personas, and a multi-bot system for distributed command handling and high availability. It also includes a Casino Integrity Checker for verifying game logic and card visibility, plus bulk command testing tools (/commandtestall, /commandtestgroup).

## Notification System Lock

The DM notification pipeline is locked. Do not modify the following without updating the regression suite (`!qatest notify`) and confirming all 19 tests still pass.

### Locked architecture

| Layer | File | Rule |
|---|---|---|
| DM entry point | `main.py on_message` | Uses `messages[0]` (SDK newest-first). Never `messages[-1]`, never reversed scan. |
| Hard gate | `main.py on_message` | `is_valid_notify_dm_command(content)` called **before any other handler**. Non-matching DMs return immediately with `[DM HARD IGNORE]`. |
| DM parser | `notify_system.py` | Exact frozenset match only: `content.strip().lower() in VALID_DM_NOTIFY_COMMANDS`. No prefix stripping, no startswith, no fuzzy match. |
| Old DM handler | `subscribers.process_incoming_dm` | Disabled stub — delegates only exact keywords to `notify_system`, drops everything else. No upsert, no pending delivery, no slash routing. |
| Sole DM reply owner | `notify_system.py` | Only `_dm_subscribe` sends "Alerts: ON…" and only `_dm_unsubscribe` sends "Alerts: OFF". No other module may send these strings via DM. |

### Locked behaviors (must not regress)

1. **Random DMs ignored** — "Hello", ".", "Ok", "?", "!notifysettings", "!notify tips on" and all other non-keyword DMs receive no reply and create no DB row.
2. **DM `!sub` subscribes** — exact match (after strip+lower) saves `user_id` + `conversation_id`, sets `subscribed=1`, replies "Alerts: ON…".
3. **DM `!unsub` unsubscribes** — exact match sets `subscribed=0`, replies "Alerts: OFF".
4. **Room `!sub` requires existing `conversation_id`** — if the user has never DM'd the bot, room `!sub` prompts them to DM first. Subscription is only stored after a DM `!sub`.
5. **Settings are room-only** — `!notifysettings`, `!notify <cat> on/off` work only from room chat; same commands via DM are silently ignored.
6. **Broadcasts respect subscribed + category** — `get_notify_users_for_broadcast(cat)` returns only rows where `subscribed=1`, `conversation_id` is set, and `<cat>=1`.
7. **First-time DM `!sub` works** — user does not need a prior room join row; `user_id` + `conversation_id` alone is sufficient for consent.
8. **`messages[0]` is always the newest** — the SDK returns newest-first; scanning history for an older user message is explicitly forbidden.

### Regression test command

Run `!qatest notify` in the room (owner/admin only). Expected: **Failed: 0** across all 19 checks.

### Do not modify

- `main.py on_message` hard gate and `messages[0]` extraction
- `notify_system.VALID_DM_NOTIFY_COMMANDS` / `is_valid_notify_dm_command()`
- `notify_system._DM_SUB_CMDS` / `_DM_UNSUB_CMDS` frozensets
- `notify_system.process_dm_notify` exact-match parser
- `notify_system._dm_subscribe` / `_dm_unsubscribe` (sole reply owners)
- `notify_system._OWNS_NOTIFY_ROUTING = True` marker
- The `_notify_logic_checks()` function in `modules/qa_test.py` (19 regression tests)

## User preferences

- All chat messages must be ≤ 249 characters.
- New settings commands follow pattern `/setbj<thing>` / `/setrbj<thing>` and are manager-only.
- Short aliases preferred for in-room play; full `/bj <sub>` commands still supported.
- Rep rank cap: Celebrity (500+). No "Legend" rep rank. Level rank Legend = 50+.

## Gotchas

- Never hardcode ports; the bot uses Highrise WebSocket.
- The `_MIGRATIONS` list in `database.py` is append-only; do not reorder or remove entries.
- `doubled` DB column in BJ/RBJ is repurposed as `active_hand_idx`; `bet` stores `total_bet()` sum.
- Use `cards.py` for `make_shoe` to ensure consistent card deck generation.
- Usernames for privacy settings and badge/room/social tables are stored lowercase.
- Seeding functions (`seed_emoji_badges`, `seed_mining_items`, etc.) use `INSERT OR IGNORE` in `_migrate_db()`.
- New `room_mutes`/`room_bans`/`room_warnings` tables are separate from older moderation tables.
- The `_user_positions` dictionary in `room_utils.py` is volatile and resets on bot restart.
- `RARITIES` in `mining.py` includes "prismatic" and "exotic" — always use `RARITIES.get(r, RARITIES["common"])` when looking up arbitrary rarity values.
- Gold tip detection uses `getattr(tip, 'type', '') == 'gold'` in `on_tip`; if SDK changes tip model, update that check in `main.py`.
- `bot_welcome_seen` records are keyed by `(bot_username, user_id)` — one record per bot per player.
- Gold tip dedup uses SHA-1 hash of `(from_uid, receiving_bot, gold_amount, minute_bucket)`; SDK event IDs can override.

## Pointers

- Adding a new room setting: Use `db.set_room_setting(key, value)` and `db.get_room_setting(key, default)`.
- Adding a new bot mode: `INSERT INTO bot_modes` or use `/createbotmode`.
- Adding a new module: Create `modules/yourmodule.py`, import in `main.py`, add routing in `on_chat()`, and add to `ALL_KNOWN_COMMANDS`.
- To prefix a bot message by category: Import `format_bot_message` from `modules.bot_modes` and call `format_bot_message(msg, "category")`.
- To manage multi-bot command ownership: Edit `_DEFAULT_COMMAND_OWNERS` in `multi_bot.py` or use `/setcommandowner`.
- To guard a new module's startup tasks: call `should_this_bot_run_module("modulename")` before any `asyncio.create_task(startup_...)` in `on_start`. Add the module to `_MODULE_OWNER_MODES` in `multi_bot.py`.
- New task/restore commands: `/taskowners`, `/activetasks`, `/taskconflicts`, `/fixtaskowners`, `/restoreannounce on|off`, `/restorestatus` (all admin/owner-only).
- Per-bot welcome: set via `/setbotwelcome <botname> <msg>`, preview with `/previewbotwelcome <botname>`, toggle globally via `/botwelcomes on|off`. Default messages live in `bot_welcome.py:_DEFAULT_MESSAGES`.
- Gold tips: rate set via `/setgoldrate <coins>` (default 1000 coins/gold). Only BankingBot credits balances; other bots acknowledge only. Logs in `gold_tip_events` table.
- Time EXP bot exclusion: `/setallowbotxp on|off` (admin+). Status shown in `/timeexpstatus`.
- Mining panel: `/minepanel` (alias `/miningpanel`, `/mineadmin`) shows full mining config — status, cooldown, weights, announce, events, rare finds today.
- New ore rarities "prismatic" (0.05%) and "exotic" (0.03%) added to `RARITIES`, `RARITY_ORDER`, `ANNOUNCE_RARITIES` in `mining.py`. 90+ new ores across all 7+ rarities added to `_MINING_ITEMS_SEED` in `database.py`.
- **B-project mining events**: 9 new mining event types (lucky_rush, heavy_ore_rush, ore_value_surge, double_mxp, mining_haste, legendary_rush, prismatic_hunt, exotic_hunt, admins_mining_blessing) wired through `EVENTS` dict and `get_event_effect()` in `events.py`. `_roll_drop` in `mining.py` accepts `event_effects` dict for rarity chance boosts. `handle_mine` applies cooldown_reduction, mxp_multiplier, ore_value_multiplier, weight_luck_boost from active events.
- **A2 ore chance commands**: `/orechances`, `/orechance <id>`, `/setorechance <id> <pct>`, `/setraritychance <rar> <pct>`, `/reloadorechances` — all owned by miner bot.
- **A11 orelist rewrite**: `/orelist [rarity]` now shows rarity-colored header, per-ore colored name, 1-in-X chance, sell value, weight range. Accepts optional rarity filter.
- **Event Manager merge**: `/eventmanager`, `/eventpanel`, `/eventeffects`, `/mineevents`, `/mineboosts`, `/luckstatus`, `/miningblessing`, `/luckevent`, `/miningevent <id> [mins]`, `/autoeventstatus`, `/autoeventadd`, `/autoeventremove`, `/autoeventinterval` — all owned by eventhost bot.