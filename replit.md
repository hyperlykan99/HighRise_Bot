# Highrise Hangout Room Bot

A modular Python bot for Highrise using the `highrise-bot-sdk`. Runs 24/7 with casino games (BJ, RBJ, Poker), economy, DJ queue, events, shop, public player profiles, room utility system, bot mode/outfit personas, and a clean module system.

## Run & Operate

```
cd artifacts/highrise-bot && python3 bot.py
```

**Single bot** (current): set only `BOT_TOKEN` + `ROOM_ID` → runs one bot in `all` mode.  
**Multi-bot** (add later): set any split-bot token secrets alongside `BOT_TOKEN`/`MAIN_BOT_TOKEN`:

| Secret | Bot | Mode |
|---|---|---|
| `MAIN_BOT_TOKEN` | Main | `all` |
| `HOST_BOT_TOKEN` | Host | `host` |
| `BLACKJACK_BOT_TOKEN` | Blackjack | `blackjack` |
| `POKER_BOT_TOKEN` | Poker | `poker` |
| `MINER_BOT_TOKEN` | Miner | `miner` |
| `BANKER_BOT_TOKEN` | Banker | `banker` |
| `SHOP_BOT_TOKEN` | Shop | `shopkeeper` |
| `SECURITY_BOT_TOKEN` | Security | `security` |
| `DJ_BOT_TOKEN` | DJ | `dj` |
| `EVENT_BOT_TOKEN` | Event | `eventhost` |

Each token key also has `_ID`, `_MODE`, `_USERNAME` variants for overrides (e.g. `BLACKJACK_BOT_ID`).  
`SHARED_DB_PATH` sets the shared SQLite file (default: `highrise_hangout.db`).

Required secrets always: `BOT_TOKEN` (or `MAIN_BOT_TOKEN`), `ROOM_ID`

## Stack

- Python 3.11, `highrise-bot-sdk` 25.1.0, `aiohttp`, `sqlite3` (built-in)
- Entry: `artifacts/highrise-bot/bot.py` (runner) → `main.py` (bot logic)
- DB: `artifacts/highrise-bot/highrise_hangout.db` (SQLite, auto-created, shared across all bots)

## Where things live

```
artifacts/highrise-bot/
├── bot.py              # Runner — single-bot shim or multi-bot subprocess orchestrator
├── main.py             # Bot logic — HangoutBot class, on_chat routing, all command sets
├── config.py           # Central config (costs, rewards, admin IDs, thresholds)
├── database.py         # All SQLite logic — schema, migrations, helpers
└── modules/
    ├── blackjack.py        # BJ — simultaneous action timer, split, double, multi-hand
    ├── realistic_blackjack.py  # RBJ — same + persistent shoe (_Shoe class)
    ├── poker.py            # Poker game module
    ├── profile.py          # 6-page public profile system + privacy controls
    ├── admin_cmds.py       # Admin power commands (setcoins, addxp, givetitle, resetstats…)
    ├── dj.py               # DJ request queue
    ├── economy.py          # Token balance, daily rewards, limits
    ├── cards.py            # Shared card helpers (make_deck, make_shoe, hand_value…)
    ├── casino_settings.py  # /casinosettings, /casinolimits, /casinotoggles
    ├── subscribers.py      # Subscription/notification system
    ├── badge_market.py     # Emoji badge shop + player marketplace + admin badge commands
    ├── bot_modes.py        # Bot persona/outfit system (8 default modes, prefix routing)
    ├── multi_bot.py        # Multi-bot gating, heartbeat, command ownership, staff controls
    ├── room_utils.py       # Room utility core (teleport, spawns, emotes, social, hearts,
    │                       #   follow, alerts, welcome, intervals, repeat, moderation ext.)
    ├── mining.py           # Mining game (ores, pickaxes, crafting, events)
    └── …                   # events, shop, trivia, scramble, bank, etc.
```

DB schema source of truth: `database.py` (`_MIGRATIONS` list + `init_db()`)

## Architecture decisions

- **Simultaneous action model for BJ/RBJ**: all players act freely during a shared action-timer (`asyncio.Task`); no per-player turn order.
- **Per-hand split support**: `_Player` holds a `hands` list; `active_hand_idx` tracks which hand is being played.
- **RBJ shoe persistence**: `_Shoe` is serialized to `shoe_json` in `rbj_game_state` and restored on recovery.
- **DB migrations**: append-only `_MIGRATIONS` list in `database.py`; each ALTER TABLE is idempotent (wrapped in try/except).
- **Room settings**: `room_settings` key/value table; access via `db.get_room_setting(key, default)` / `db.set_room_setting(key, value)`.
- **Bot modes**: `bot_modes` + `bot_mode_assignments` tables. `format_bot_message(msg, category)` in `bot_modes.py` prefixes messages by mode/category. 8 default modes seeded on startup.
- **Position cache**: `_user_positions` dict in `room_utils.py` updated from `on_user_move` in `main.py` — used for teleport/follow.
- **Room-ban gate**: `is_room_banned()` checked in `on_chat` after mute gate; bot-level ban blocks all non-exempt commands.
- **Multi-bot gate**: `should_this_bot_handle(cmd)` checked immediately after cmd parsing. `BOT_MODE=all` always returns True. `blackjack` handles BJ/RBJ only. `poker` handles Poker only. `dealer` is legacy fallback for casino if dedicated bots offline. Host/all sends offline message when fallback=off.
- **Heartbeat**: 30 s asyncio loop upserts this bot's row into `bot_instances`. Cache TTL: 30 s online status, 60 s ownership overrides.
- **Module locks**: `bot_module_locks` table with TTL-based acquire/release. Use `db.acquire_module_lock("blackjack", BOT_ID)` before game-state writes to prevent dual payouts in multi-bot runs.
- **Auto-games ownership**: `should_this_bot_run_autogames()` in `auto_games.py` gates both `start_auto_game_loop()` and `start_auto_event_loop()`. Room setting `autogames_owner_bot_mode` (default `eventhost`) determines which bot mode runs the loops. Blocked modes (blackjack/poker/miner/banker/shopkeeper/security/dj) never run. Host falls back if owner offline + fallback ON. Module locks `autogames`/`autogames_event` (120 s TTL) prevent duplicate starts in race conditions. Configure with `/autogamesowner [eventhost|host|all|disabled]`; emergency stop: `/stopautogames`.
- **Poker recovery (non-circular)**: `get_poker_recovery_recommendation()` in `poker.py` returns one of `forcefinish|hardrefund|clearhand|closeforce|no_action` — never two commands pointing at each other. `_hard_refund_hand(actor)` does priority-order pot resolution: contrib-proportional → equal-split unpaid → equal-split seated → log-as-lost. `/poker closeforce` generates a 60-second confirmation code; `/confirmclosepoker <code>` executes the full close + wallet refund.

## Product

Casino games (BJ, RBJ with split/double/shoe, Poker), DJ queue, token economy, daily rewards, bank/send, shop (titles/emoji badges), quests, achievements, events, subscriber DM system, leaderboards, staff management tiers, 6-page public player profiles with privacy controls, emoji badge market, mining game, **Room Utility Core**, **Bot Mode/Outfit system** (8 personas), **Multi-Bot System** (command ownership, heartbeat, live status, fallback routing, staff controls).

## Room Utility (modules/room_utils.py)

**Teleport**: `/tpme` `/tp` `/tphere` `/goto` `/bring` `/bringall` `/tpall` `/tprole` `/tpvip` `/tpstaff` `/selftp` `/groupteleport`  
**Spawns**: `/spawns` `/spawn` `/setspawn` `/savepos` `/delspawn` `/spawninfo` `/setspawncoords`  
**Emotes**: `/emote` `/emotes` `/stopemote` `/dance` `/wave` `/sit` `/clap` · Staff: `/forceemote` `/forceemoteall` `/loopemote` `/stoploop` `/stopallloops` `/synchost` `/syncdance` `/stopsync`  
**Hearts**: `/heart` `/hearts` `/heartlb` · **Social**: `/hug` `/kiss` `/slap` `/punch` `/highfive` `/boop` `/waveat` `/cheer` `/social on|off` `/blocksocial`  
**Follow**: `/followme` `/follow` `/stopfollow` `/followstatus`  
**Alerts**: `/alert` `/staffalert` `/vipalert` `/roomalert`  
**Welcome**: `/welcome on|off` `/setwelcome` `/welcometest` `/resetwelcome` `/welcomeinterval`  
**Intervals**: `/intervals` `/addinterval` `/delinterval` `/interval on|off` `/intervaltest`  
**Repeat**: `/repeatmsg <count> <sec> <msg>` (owner only, max 5 msgs, min 10s) `/stoprepeat` `/repeatstatus`  
**Extended mod**: `/kick` `/ban` `/tempban` `/unban` `/bans` `/modlog`  
**Settings/logs**: `/roomsettings [2]` `/setroomsetting` `/roomlogs`  
**Help pages**: `/roomhelp` `/teleporthelp` `/emotehelp` `/alerthelp` `/welcomehelp` `/socialhelp`

## Bot Mode System (modules/bot_modes.py)

**8 default modes**: `host` `miner` `banker` `dj` `dealer` `security` `shopkeeper` `eventhost`  
**Public**: `/botmode` `/botmodes` `/botprofile` `/bots` `/botinfo` `/botoutfit` `/botoutfits` `/botmodehelp`  
**Staff**: `/botmode <id>` `/botprefix on|off` `/categoryprefix on|off` `/setbotprefix` `/setbotdesc` `/setbotoutfit` `/dressbot <id>` `/savebotoutfit` `/createbotmode` `/deletebotmode` `/assignbotmode` `/botoutfitlogs`  
**Category prefix map**: mining→miner, bank/economy→banker, casino/BJ/RBJ→dealer, events→eventhost, moderation→security, shop→shopkeeper, welcome/help→host, emotes/dance→dj  
**Helper**: `format_bot_message(msg, category)` — import from `modules.bot_modes` in any module to prefix messages.

## User preferences

- All chat messages must be ≤ 249 characters.
- New settings commands follow pattern `/setbj<thing>` / `/setrbj<thing>` and are manager-only.
- Short aliases preferred for in-room play; full `/bj <sub>` commands still supported.
- Rep rank cap: Celebrity (500+). No "Legend" rep rank. Level rank Legend = 50+.

## Gotchas

- Never hardcode ports — bot uses Highrise WebSocket, not HTTP.
- `database.py` `_MIGRATIONS` list is append-only; never reorder or remove entries.
- BJ/RBJ `doubled` DB column repurposed as `active_hand_idx` (int); `bet` column stores `total_bet()` sum across all hands.
- `make_shoe` is in `cards.py` — import from there, not redefined in module files.
- `profile_privacy` keyed by `username.lower()`; `user_badges`, `room_*`, `social_*` tables also lowercase usernames.
- `seed_emoji_badges()`, `seed_mining_items()`, `seed_room_settings()`, `seed_bot_modes()` all called in `_migrate_db()` — all inserts use `INSERT OR IGNORE`.
- `room_mutes`/`room_bans`/`room_warnings` are NEW tables separate from existing `mutes`/`warnings` tables (moderation.py uses old tables).
- `_user_positions` dict in `room_utils.py` is populated from `on_user_move` events in `main.py`. If bot restarts, position cache is empty until users move.

## Multi-Bot System (modules/multi_bot.py)

**Identity env vars**: `BOT_ID`, `BOT_MODE` (default `all`), `BOT_USERNAME`, `SHARED_DB_PATH`  
**Bot modes**: `all` `host` `blackjack` `poker` `dealer` `banker` `miner` `shopkeeper` `security` `dj` `eventhost`  
**Public**: `/bots` `/botstatus [id]` `/botmodules` `/multibothelp`  
**Admin**: `/commandowners` `/enablebot <id>` `/disablebot <id>` `/setbotmodule <id> <mode>` `/setcommandowner <cmd> <mode>` `/botfallback on|off` `/botstartupannounce on|off`  
**DB tables**: `bot_instances` (heartbeat), `bot_command_ownership` (overrides), `bot_module_locks` (per-game lock)  
**Room settings**: `multibot_fallback_enabled` (default `true`), `bot_startup_announce_enabled` (default `false`)  
**Gate**: `should_this_bot_handle(cmd)` → no-op on `BOT_MODE=all`. `blackjack` owns BJ/RBJ. `poker` owns Poker. `dealer` is legacy fallback for both. Host/all sends "X bot is currently offline." when fallback=off.  
**Module lock API**: `db.acquire_module_lock(module, bot_id)` / `db.release_module_lock(module, bot_id)` — prevents dual payout in multi-bot game runs.

### Recommended bot setup
| Bot | BOT_ID | BOT_MODE | BOT_USERNAME |
|---|---|---|---|
| Blackjack Bot | `blackjack` | `blackjack` | `AceSinatra` |
| Poker Bot | `poker` | `poker` | `ChipSoprano` |
| Host Bot | `host` | `host` | `LoungeHost` |
| Single bot | `main` | `all` | _(any)_ |

## Bot Health System (modules/bot_health.py)

**View (manager+)**: `/bothealth [bot_id]` `/modulehealth [module]` `/deploymentcheck [page]` `/botlocks` `/botheartbeat` `/moduleowners [page]` `/botconflicts`  
**Repair (admin+)**: `/clearstalebotlocks` `/fixbotowners [force]`  
**Module keys**: `blackjack` `rbj` `poker` `mining` `bank` `shop` `security` `dj` `events`  
`/deploymentcheck` runs 9 checks (DB, tables, bot online status, duplicate owners, stale locks, all+split conflict) paginated 4 per page.  
`/fixbotowners` fills missing owners from defaults; add `force` to overwrite custom settings.  
`check_startup_safety()` called on every bot start — logs console WARN if duplicate BOT_ID seen in last 60s, or if BOT_MODE=all runs alongside split bots.

## Pointers

- Adding a new room setting: `db.set_room_setting(key, value)` / `db.get_room_setting(key, default)`.
- Adding a new bot mode: `INSERT INTO bot_modes` or use `/createbotmode`.
- Adding a new module: create `modules/yourmodule.py`, import in `main.py`, add routing in `on_chat()`, add to `ALL_KNOWN_COMMANDS`.
- To prefix a bot message by category: `from modules.bot_modes import format_bot_message` → `format_bot_message(msg, "mining")`.
- To add a command to the multi-bot ownership map: edit `_DEFAULT_COMMAND_OWNERS` in `multi_bot.py`, or use `/setcommandowner` at runtime.
