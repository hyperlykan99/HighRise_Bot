# Highrise Hangout Room Bot

A modular Python bot for Highrise using the `highrise-bot-sdk`. Runs 24/7 with casino games (BJ, RBJ, Poker), economy, DJ queue, events, shop, public player profiles, room utility system, bot mode/outfit personas, and a clean module system.

## Run & Operate

```
cd artifacts/highrise-bot && python3 bot.py
```

Required secrets: `BOT_TOKEN`, `ROOM_ID`

## Stack

- Python 3.11, `highrise-bot-sdk` 25.1.0, `aiohttp`, `sqlite3` (built-in)
- Entry: `artifacts/highrise-bot/main.py`
- DB: `artifacts/highrise-bot/highrise_hangout.db` (SQLite, auto-created)

## Where things live

```
artifacts/highrise-bot/
├── main.py             # Entry point — on_chat routing, all command sets, help pages
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
- **Multi-bot gate**: `should_this_bot_handle(cmd)` in `multi_bot.py` checked immediately after cmd parsing. When `BOT_MODE=all` (default) it always returns True — fully backwards-compatible. In specialized mode it checks `bot_command_ownership` DB (overrides) → `_DEFAULT_COMMAND_OWNERS` map → online status cache → fallback setting.
- **Heartbeat**: 30 s asyncio loop upserts this bot's row into `bot_instances` with status/last_seen_at. Other bots in the cluster read this to know if a mode is online. Cache TTL: 30 s for online status, 60 s for ownership overrides.

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

**Identity env vars**: `BOT_ID` (default `main`), `BOT_MODE` (default `all`), `BOT_USERNAME`, `SHARED_DB_PATH`  
**Public**: `/bots` `/botmodules` `/multibothelp`  
**Admin**: `/commandowners` `/enablebot <id>` `/disablebot <id>` `/setbotmodule <id> <mode>` `/setcommandowner <cmd> <mode>` `/botfallback on|off` `/botstartupannounce on|off`  
**DB tables**: `bot_instances` (heartbeat rows), `bot_command_ownership` (per-command overrides)  
**Room settings**: `multibot_fallback_enabled` (default `true`), `bot_startup_announce_enabled` (default `false`)  
**Gate**: `should_this_bot_handle(cmd)` — no-op when `BOT_MODE=all`; in specialized mode, yields to the online owner or falls back to host mode.

## Pointers

- Adding a new room setting: `db.set_room_setting(key, value)` / `db.get_room_setting(key, default)`.
- Adding a new bot mode: `INSERT INTO bot_modes` or use `/createbotmode`.
- Adding a new module: create `modules/yourmodule.py`, import in `main.py`, add routing in `on_chat()`, add to `ALL_KNOWN_COMMANDS`.
- To prefix a bot message by category: `from modules.bot_modes import format_bot_message` → `format_bot_message(msg, "mining")`.
- To add a command to the multi-bot ownership map: edit `_DEFAULT_COMMAND_OWNERS` in `multi_bot.py`, or use `/setcommandowner` at runtime.
