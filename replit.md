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
    └── cmd_audit.py        # Command audit tools incl. commandtestall/group (B)
```

DB schema source of truth: `database.py` (`_MIGRATIONS` list + `init_db()`).

## Architecture decisions

- **Simultaneous action model for BJ/RBJ**: Players act freely during a shared `asyncio.Task` timer.
- **RBJ shoe persistence**: The `_Shoe` object is serialized to JSON in `rbj_game_state` and restored on recovery.
- **DB migrations**: An append-only `_MIGRATIONS` list in `database.py` handles schema changes with idempotent `ALTER TABLE` statements.
- **Bot modes**: A system of `bot_modes` and `bot_mode_assignments` tables allows for different bot personas and message prefixes.
- **Multi-bot system**: Features `bot_instances` for heartbeats, `bot_command_ownership` for command routing, and `bot_module_locks` for preventing race conditions in games. Auto-game ownership is managed via `autogames_owner_bot_mode` room setting.
- **Module ownership guard**: `should_this_bot_run_module(module)` in `multi_bot.py` gates all startup recovery calls and room announce messages — only the owning bot mode runs them. `_MODULE_OWNER_MODES` defines the mapping (poker→poker, blackjack→blackjack/rbj, events→eventhost, etc.). Dedupe lock table `module_announcement_locks` provides a 5-minute cross-bot dedupe safety net via `db.acquire_module_announce_lock()`.

## Product

Casino games (Blackjack, Realistic Blackjack, Poker), DJ queue, token economy, daily rewards, in-game shop, quests, achievements, events, subscriber DMs, leaderboards, staff management, public player profiles with privacy controls, emoji badge market, mining game (7 rarities: Common/Uncommon/Rare/Epic/Legendary/Mythic/Ultra-Rare/Prismatic/Exotic with 90+ ores, per-ore weight system, weight leaderboards, configurable rare-ore room announcements, `/minepanel` staff dashboard), per-bot personalized welcome whispers (configurable per bot), gold tip migration to BankingBot (coins_per_gold conversion, event log, dedup), Time EXP bot exclusion (`/setallowbotxp`), a comprehensive room utility system, a bot mode/outfit system with multiple personas, and a multi-bot system for distributed command handling and high availability. It also includes a Casino Integrity Checker for verifying game logic and card visibility, plus bulk command testing tools (/commandtestall, /commandtestgroup).

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