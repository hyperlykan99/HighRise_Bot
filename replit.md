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
    ├── admin_cmds.py       # Admin power commands (setcoins, addxp, givetitle, resetstats…)
    ├── dj.py               # DJ request queue
    ├── economy.py          # Token balance, daily rewards, limits
    ├── cards.py            # Shared card helpers (make_deck, make_shoe, hand_value…)
    ├── casino_settings.py  # /casinosettings, /casinolimits, /casinotoggles
    ├── subscribers.py      # Subscription/notification system
    ├── badge_market.py     # Emoji badge shop + player marketplace + admin badge commands
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

Casino games (BJ, RBJ with split/double/shoe, Poker), DJ queue, token economy, daily rewards, bank/send, shop (titles/emoji badges), quests, achievements, events, subscriber DM system, leaderboards, staff management tiers, 6-page public player profiles with privacy controls, **emoji badge market** (player-to-player trading with atomic SQLite transactions and configurable market fee), **Mining game** (ore drops, pickaxe upgrades, crafting, leaderboards, daily bonus, mining events).

**Admin power commands** (admin+, logged to `admin_action_logs`): `setcoins/editcoins/resetcoins`, `addeventcoins/removeeventcoins/seteventcoins/reseteventcoins`, `addxp/removexp/setxp/resetxp/setlevel/addlevel`, `setrep/resetrep`, `givetitle/removetitle`, `givebadge/removebadge/giveemojibadge`, `addvip/removevip/vips`, `resetbjstats/resetrbjstats/resetpokerstats/resetcasinostats`, `adminpanel`, `adminlogs`, `addbadge/editbadgeprice/setbadgepurchasable/setbadgetradeable/setbadgesellable`, `badgecatalog/badgeadmin/setbadgemarketfee/badgemarketlogs`.

**Help system** (paged, role-aware): all help pages rebuilt with `/command - description` format; `mycommands` shows role-filtered command list; `helpsearch <term>` searches across all known commands; `coinhelp`, `bjhelp`, `rbjhelp`, `bankhelp`, `shophelp`, `modhelp`, `managerhelp`, `adminhelp`, `ownerhelp` are all paged.

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

## Emoji Badge Market (modules/badge_market.py)

**Tables**: `emoji_badges` (catalog, 65 default badges seeded), `user_badges` (ownership), `badge_market_listings`, `badge_market_logs`  
**Player**: `/shop badges [page]` · `/buy badge <id>` · `/equip badge <id>` · `/unequip badge` · `/mybadges` · `/badgemarket [page]` · `/badgelist <id> <price>` · `/badgebuy <id>` · `/badgecancel <id>` · `/mybadgelistings` · `/badgeprices <id>`  
**Admin**: `/addbadge <id> <emoji> <name> <rarity> <price>` · `/editbadgeprice` · `/setbadgepurchasable/tradeable/sellable` · `/giveemojibadge <user> <emoji> <name>` · `/badgecatalog` · `/badgeadmin <id>` · `/setbadgemarketfee <pct>` · `/badgemarketlogs [user]`  
**Rarities**: common(500c) uncommon(2.5K) rare(10K) epic(50K) legendary(150K) mythic(500K) exclusive(0, not purchasable)  
**Market**: Atomic SQLite transaction (check→deduct buyer→credit seller→transfer badge→mark sold→log). Fee from `bot_settings.badge_market_fee_percent` (default 5%). Seller notified via whisper.  
**Backward compat**: Old BADGES dict + `owned_items` table still used for legacy badges. New system uses `user_badges` table. `/buy badge` and `/equip badge` route to new handler; `/buy title` still uses old shop.py.

## Numbered Shop System (modules/numbered_shop.py)

**Tables**: `shop_view_sessions` (username PK, shop_type, page, items_json, expires 10 min), `pending_shop_purchases` (code PK, expires 5 min)  
**Session saved by**: `handle_shop_badges` (badges), `_send_catalog_page` (titles), `handle_eventshop` (event), `handle_badgemarket` (market_badges)  
**Player commands**: `/buy <#>` · `/buyitem <#>` · `/purchase <#>` — buy from last viewed shop  
**Market numbers**: `/badgebuy <#>` and `/marketbuy <#>` — buy from last viewed badge market page  
**Navigation**: `/shop next` · `/shop prev` · `/shop page <n>` · `/badgemarket next/prev` · `/eventshop next/prev`  
**Confirmation**: items ≥ 100,000c (or ≥ 500 EC for event) prompt `/confirmbuy <code>`. Cancel with `/cancelbuy <code>`. Thresholds: `/setshopconfirm <n>` · `/seteventconfirm <n>`  
**Admin**: `/shopadmin [badges|titles|event]` · `/shoptest <user> <type>` (simulates shop for any player)  
**Ownership markers**: ✅ shown inline when player already owns the item  
**Legacy unchanged**: `/buy badge <id>` · `/buy title <id>` · `/buyevent <id>` · `/eventshop buy <id>` all still work

## Mining Game (modules/mining.py)

**Tables**: `mining_players` (stats, tool, energy, boosts, streaks), `mining_inventory` (ore qty per user), `mining_items` (25-ore catalog seeded), `mining_settings` (key/value), `mining_logs`, `mining_events`  
**Player commands**: `/mine` `/m` `/dig` — roll ore drop with cooldown + energy cost · `/tool` `/pickaxe` — view pickaxe · `/upgradetool` `/upick` — upgrade (coins + ore mats) · `/ores` `/mineinv [page]` · `/sellores` sell all · `/sellore <id> <qty>` · `/mineprofile` `/mp` `/minerank` — 2-page stats · `/minelb [mines|level|ores|rare|coins|meteorite]` · `/mineshop` — consumables · `/minebuy <#>` · `/craft [item_id]` — craft badges+titles · `/minedaily` — daily bonus (energy+coins+MXP) · `/minehelp`  
**Pickaxes**: 10 levels (Worn→Master). Upgrade requires coins + specific ores. Lv 10 = 30s cooldown, +4.5% rare boost.  
**Ores**: 25 items across 7 rarities (common→ultra_rare). Rare+ finds announced to room. Meteorite Fragment (ultra_rare) has dedicated leaderboard.  
**Boosts**: 🍀 Lucky Charm (+25% rare chance, 15m) · 🎵 Focus Music (2x MXP, 15m) — bought via /mineshop, timed via `luck_boost_until`/`xp_boost_until` ISO columns.  
**Mining events**: `double_ore` `double_mxp` `lucky_hour` `energy_free` `meteor_rush` — started by `/startminingevent <id> [mins]`, auto-expire by `ends_at`.  
**Crafting**: `miner_badge` `gem_hunter_badge` `lounge_miner` `master_miner` `starfinder` titles/badges from ore recipes.  
**Admin**: `/mining on|off` · `/setminecooldown` · `/setmineenergycost` · `/setminingenergy <user> <amt>` · `/addore <user> <ore> <qty>` · `/removeore` · `/settoollevel <user> <1-10>` · `/setminelevel` · `/addminexp` · `/setminexp` · `/resetmining <user>` · `/miningroomrequired on|off` · `/orelist`  
**DB helpers**: `get_or_create_miner` / `ensure_miner_row` (alias) · `update_miner(**kwargs)` · `add_ore` / `remove_ore` / `sell_all_ores` / `sell_ore_item` · `get_inventory` · `get_mine_leaderboard(field)` · `start/stop_mining_event`

## Gotchas

- Never hardcode ports — bot uses Highrise WebSocket, not HTTP.
- `database.py` `_MIGRATIONS` list is append-only; never reorder or remove entries.
- BJ/RBJ `doubled` DB column repurposed as `active_hand_idx` (int); `bet` column stores `total_bet()` sum across all hands.
- `make_shoe` is in `cards.py` — import from there, not redefined in module files.
- `profile_privacy` is keyed by `username.lower()` — always lowercase before DB calls.
- Emoji badge ownership lives in `user_badges` (keyed by `lower(username)`); `owned_items` is for legacy badges + titles only.
- `seed_emoji_badges()` and `seed_mining_items()` both called in `_migrate_db()` — all inserts use `INSERT OR IGNORE`.
- Mining inventory keyed by `lower(username)` in `mining_inventory`; all helper calls normalise case.
- `ensure_miner_row(username)` is an alias for `get_or_create_miner(username)` — use either.

## Pointers

- Adding a new setting: add ALTER TABLE to `_MIGRATIONS`, add key to `_BJ_SETTING_COLS` / `_RBJ_SETTING_COLS`, add fallback value in `get_bj_settings()` / `get_rbj_settings()`, add routing in `main.py` `setbj*` block, add to `MANAGER_ONLY_CMDS` + `ALL_KNOWN_COMMANDS`.
- Adding a new module: create `modules/yourmodule.py`, import in `main.py`, add command set, add routing branch in `on_chat()`.
