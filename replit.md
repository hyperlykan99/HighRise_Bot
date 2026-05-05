# Highrise Hangout Room Bot

A modular Python bot for Highrise using the `highrise-bot-sdk`. Runs 24/7 with casino games (BJ, RBJ, Poker), economy, DJ queue, events, shop, public player profiles, and a clean module system.

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
    ├── dj.py               # DJ request queue
    ├── economy.py          # Token balance, daily rewards, limits
    ├── cards.py            # Shared card helpers (make_deck, make_shoe, hand_value…)
    ├── casino_settings.py  # /casinosettings, /casinolimits, /casinotoggles
    ├── subscribers.py      # Subscription/notification system
    └── …                   # events, shop, trivia, scramble, bank, etc.
```

DB schema source of truth: `database.py` (`_MIGRATIONS` list + `init_db()`)

## Architecture decisions

- **Simultaneous action model for BJ/RBJ**: all players act freely during a shared action-timer (`asyncio.Task`); no per-player turn order. Timer set via `/setbjactiontimer` / `/setrbjactiontimer`.
- **Per-hand split support**: `_Player` holds a `hands` list; `active_hand_idx` tracks which hand is being played. DB stores `hand_json = {"hands":[…], "split_count":N}`, repurposes `doubled` column for `active_hand_idx`.
- **RBJ shoe persistence**: `_Shoe` is serialized to `shoe_json` in `rbj_game_state` and restored on recovery.
- **Short command aliases**: `bjoin bh bs bd bsp bt bhand blimits bstats` and `rjoin rh rs rd rsp rt rhand rshoe rlimits rstats` all route to `handle_bj` / `handle_rbj` in `main.py`.
- **DB migrations**: append-only `_MIGRATIONS` list in `database.py`; each ALTER TABLE is idempotent (wrapped in try/except).
- **Profile privacy**: stored in `profile_privacy` table (keyed by lowercase username). All 4 flags default ON (visible). Staff always bypass privacy. Privacy page accessible to muted players.

## Product

Casino games (BJ, RBJ with split/double/shoe, Poker), DJ queue, token economy, daily rewards, bank/send, shop (titles/badges), quests, achievements, events, subscriber DM system, leaderboards, staff management tiers, 6-page public player profiles with privacy controls.

## Profile system (modules/profile.py)

Pages: 1=Identity 2=Economy 3=Casino 4=Inventory 5=Achievements 6=Social  
Commands: `/profile [user] [1-6]` · `/me` · `/whois <user>` · `/pinfo <user>` · `/stats [user]` · `/badges [user]` · `/titles [user]` · `/privacy [field] [on/off]`  
Staff: `/profileadmin <user> [page]` (admin+) · `/profileprivacy <user>` (mod+) · `/resetprofileprivacy <user>` (admin+)  
Casino page shortcut: `/casino <username>` (when arg is not modes/on/off/reset/leaderboard)

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
- `profile_privacy` is keyed by `username.lower()` — always lowercase before DB calls.

## Pointers

- Adding a new setting: add ALTER TABLE to `_MIGRATIONS`, add key to `_BJ_SETTING_COLS` / `_RBJ_SETTING_COLS`, add fallback value in `get_bj_settings()` / `get_rbj_settings()`, add routing in `main.py` `setbj*` block, add to `MANAGER_ONLY_CMDS` + `ALL_KNOWN_COMMANDS`.
- Adding a new module: create `modules/yourmodule.py`, import in `main.py`, add command set, add routing branch in `on_chat()`.
