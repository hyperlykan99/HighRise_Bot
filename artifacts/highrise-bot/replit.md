# Highrise Hangout Room Bot

A modular Python bot for Highrise using the `highrise-bot-sdk`. Runs 24/7 with casino games (BJ, RBJ, persistent-table Texas Hold'em Poker), economy, DJ queue, events, shop, public player profiles, and a clean module system.

## Run & Operate

```
cd artifacts/highrise-bot && python3 bot.py
```

Required secrets: `BOT_TOKEN`, `ROOM_ID`

## Stack

- Python 3.11, `highrise-bot-sdk` 25.1.0, `aiohttp`, `sqlite3` (built-in)
- Entry: `artifacts/highrise-bot/bot.py`
- DB: `artifacts/highrise-bot/highrise_hangout.db` (SQLite, auto-created)

## Where things live

```
artifacts/highrise-bot/
├── bot.py              # Entry point
├── main.py             # on_chat routing, all command sets, help pages
├── config.py           # Central config (costs, rewards, admin IDs, thresholds)
├── database.py         # All SQLite logic — schema, migrations, helpers
└── modules/
    ├── blackjack.py        # BJ — simultaneous action timer, split, double, multi-hand
    ├── realistic_blackjack.py  # RBJ — same + persistent shoe (_Shoe class)
    ├── poker.py            # Texas Hold'em — persistent table, blinds, auto-hands
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

- **Simultaneous action model for BJ/RBJ**: all players act freely during a shared action-timer (`asyncio.Task`); no per-player turn order.
- **Persistent Poker Table**: `poker_seated_players` table stores the roster between hands. Coins go wallet→table_stack on join, table_stack→wallet on leave only. Per-hand win/loss updates `table_stack`, never the wallet directly. Auto-start next hand via `_next_hand_countdown` asyncio task.
- **Poker blinds & dealer button**: SB/BB posted from in-hand stacks each hand. Dealer rotates via `dealer_button_index` in `poker_active_table`. Heads-up: dealer=SB acts first preflop; BB acts first post-flop. 3+ players: standard UTG-first preflop, SB-first post-flop.
- **Per-hand split support (BJ/RBJ)**: `_Player` holds a `hands` list; `active_hand_idx` tracks which hand is being played. DB stores `hand_json = {"hands":[…], "split_count":N}`.
- **RBJ shoe persistence**: `_Shoe` is serialized to `shoe_json` in `rbj_game_state` and restored on recovery.
- **DB migrations**: append-only `_MIGRATIONS` list in `database.py`; each ALTER TABLE is idempotent (wrapped in try/except).
- **Profile privacy**: stored in `profile_privacy` table (keyed by lowercase username). All 4 flags default ON (visible). Staff always bypass privacy.

## Product

Casino games (BJ, RBJ with split/double/shoe, persistent Hold'em Poker with blinds/rebuy/sit-out/auto-next-hand), DJ queue, token economy, daily rewards, bank/send, shop (titles/badges), quests, achievements, events, subscriber DM system, leaderboards, staff management tiers, 6-page public player profiles with privacy controls.

## Poker system (modules/poker.py)

**Player flow**: `/p <buyin>` → wallet deducted → `poker_seated_players` record created → auto-start countdown → hands dealt in loop → `/poker leave` → `table_stack` returned to wallet.

**Persistent table state**:
- `poker_seated_players`: roster between hands (username PK, table_stack, status, seat_number, leaving_after_hand, idle_strikes)
- `poker_active_table`: per-hand state (round_id, phase, deck, community, pot, dealer_button_index, hand_number, SB/BB usernames)
- `poker_active_players`: per-hand per-player rows (created at hand start, deleted at hand end)

**Commands**: `/p <buyin>` `/poker leave` `/sitout` `/sitin` `/rebuy <amt>` `/mystack` `/pstacks` `/poker close` `/poker start` + all action aliases (check/call/raise/fold/allin).

**Manager settings**: `/setpokerblinds <SB> <BB>` `/setpokerante <amt>` `/setpokernexthandtimer <sec>` `/setpokermaxstack <amt>` `/setpokeridlestrikes <n>` `/setpokerbuyin` `/setpokerplayers` `/setpokertimer` `/setpokerraise`.

## Profile system (modules/profile.py)

Pages: 1=Identity 2=Economy 3=Casino 4=Inventory 5=Achievements 6=Social
Commands: `/profile [user] [1-6]` · `/me` · `/whois <user>` · `/privacy [field] [on/off]`
Staff: `/profileadmin <user> [page]` · `/profileprivacy <user>` · `/resetprofileprivacy <user>`

## User preferences

- All chat messages must be ≤ 249 characters.
- New settings commands follow pattern `/setpokerblinds` etc. and are manager-only.
- Short aliases preferred for in-room play; full `/poker <sub>` commands still supported.
- Rep rank cap: Celebrity (500+). No "Legend" rep rank. Level rank Legend = 50+.

## Gotchas

- Never hardcode ports — bot uses Highrise WebSocket, not HTTP.
- `database.py` `_MIGRATIONS` list is append-only; never reorder or remove entries.
- BJ/RBJ `doubled` DB column repurposed as `active_hand_idx` (int); `bet` column stores `total_bet()` sum across all hands.
- `make_shoe` is in `cards.py` — import from there.
- `profile_privacy` is keyed by `username.lower()` — always lowercase before DB calls.
- `_pay_seated` updates `poker_seated_players.table_stack` only (never wallet). Wallet only changes on join/rebuy (deduct) and leave/close/refundall (return).
- Poker `poker_seated_players` is ordered by `seat_number` for consistent dealer rotation.

## Pointers

- **Adding new poker setting**: add ALTER TABLE to `_MIGRATIONS`, add INSERT OR IGNORE to `init_db()` poker_settings block, add `_s()` call in poker.py, add handler in poker.py, add routing in main.py MANAGER_ONLY_CMDS + ALL_KNOWN_COMMANDS + on_chat.
- **Adding a new module**: create `modules/yourmodule.py`, import in `main.py`, add command set, add routing branch in `on_chat()`.
