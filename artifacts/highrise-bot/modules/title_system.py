"""
modules/title_system.py
-----------------------
Title V2 — Full title system: shop, achievement, seasonal, admin, secret.

Player commands:
  !titles [category]   !titleshop [page]   !alltitles [page]   !mytitles [page]
  !titleinfo <id>      !buytitle <id>       !equiptitle <id|#>  !unequiptitle
  !titlesearch <name>  !titleprogress [cat] !claimtitles
  !myboosts            !perks               !titleperks
  !mystats             !prestige [@user]    !titlehelp
  !loadout save|equip <name>               !loadouts
  !besttitle <cat>     !equipbest <cat>     !titlelb [cat]
  !temporarytitles     !seasontitles

Admin commands:
  !givetitle @user id  !removetitle @user id  !settitle @user id  !cleartitle @user
  !titleaudit @user    !titlelogs [@user|last]  !titlestats @user
  !boosts @user        !addtitle ...           !edittitle ...
  !settitlebuyable id on|off                  !settitleactive id on|off
  !awardseasonaltitle @user id 7d             !expiretitles
"""

from __future__ import annotations
import json
import asyncio
from datetime import datetime, timezone, timedelta
from highrise import BaseBot, User
import database as db

# ---------------------------------------------------------------------------
# Title catalog
# Each entry: display, tier, source, category, price, buyable, secret,
#             req_type, req_val, perks, description, announce (bool)
# ---------------------------------------------------------------------------

TITLE_CATALOG: dict[str, dict] = {
    # ── Shop titles ──────────────────────────────────────────────────────────
    "rookie": {
        "display": "[Rookie]", "tier": "Common", "source": "Shop",
        "category": "shop", "price": 3_000, "buyable": True,
        "req_type": "", "req_val": 0,
        "perks": {"daily_coins_bonus": 5},
        "description": "+5 daily coins",
    },
    "lucky": {
        "display": "[Lucky]", "tier": "Common", "source": "Shop",
        "category": "shop", "price": 7_500, "buyable": True,
        "req_type": "", "req_val": 0,
        "perks": {"game_reward_pct": 2.0},
        "description": "+2% game rewards",
    },
    "grinder": {
        "display": "[Grinder]", "tier": "Rare", "source": "Shop",
        "category": "shop", "price": 12_000, "buyable": True,
        "req_type": "", "req_val": 0,
        "perks": {"game_reward_pct": 3.0},
        "description": "+3% game rewards",
    },
    "regular": {
        "display": "[Regular]", "tier": "Rare", "source": "Shop",
        "category": "shop", "price": 25_000, "buyable": True,
        "req_type": "", "req_val": 0,
        "perks": {"daily_coins_bonus": 10},
        "description": "+10 daily coins",
    },
    "trivia_king": {
        "display": "[Trivia King]", "tier": "Epic", "source": "Achievement",
        "category": "games", "price": 0, "buyable": False,
        "req_type": "trivia_wins", "req_val": 100,
        "perks": {"trivia_bonus": 10},
        "description": "Win 100 trivia games | +10 trivia coins",
        "announce": True,
    },
    "word_master": {
        "display": "[Word Master]", "tier": "Epic", "source": "Achievement",
        "category": "games", "price": 0, "buyable": False,
        "req_type": "scramble_wins", "req_val": 100,
        "perks": {"scramble_bonus": 10},
        "description": "Win 100 word scramble games | +10 scramble coins",
        "announce": True,
    },
    "riddle_lord": {
        "display": "[Riddle Lord]", "tier": "Epic", "source": "Achievement",
        "category": "games", "price": 0, "buyable": False,
        "req_type": "riddle_wins", "req_val": 100,
        "perks": {"riddle_bonus": 10},
        "description": "Win 100 riddle games | +10 riddle coins",
        "announce": True,
    },
    "casino_rat": {
        "display": "[Casino Rat]", "tier": "Rare", "source": "Legacy",
        "category": "shop", "price": 0, "buyable": False,
        "req_type": "", "req_val": 0,
        "perks": {"casino_reward_pct": 3.0},
        "description": "Discontinued shop title",
    },
    "high_roller": {
        "display": "[High Roller]", "tier": "Epic", "source": "Legacy",
        "category": "casino", "price": 0, "buyable": False,
        "req_type": "casino_lifetime_wagered", "req_val": 1_000_000,
        "perks": {"casino_reward_pct": 5.0},
        "description": "Wager 1M coins in casino games | +5% casino",
    },
    "millionaire": {
        "display": "[Millionaire]", "tier": "Legendary", "source": "Legacy",
        "category": "wealth", "price": 0, "buyable": False,
        "req_type": "balance_milestone", "req_val": 1_000_000,
        "perks": {"daily_coins_bonus": 25, "game_reward_pct": 3.0},
        "description": "Hold 1M ChillCoins balance | +25 daily, +3% game",
    },
    "elite": {
        "display": "[Elite]", "tier": "Legendary", "source": "Shop",
        "category": "shop", "price": 300_000, "buyable": True,
        "req_type": "", "req_val": 0,
        "perks": {"game_reward_pct": 8.0},
        "description": "+8% game rewards",
    },
    "immortal": {
        "display": "[Immortal]", "tier": "Mythic", "source": "Shop",
        "category": "shop", "price": 750_000, "buyable": True,
        "req_type": "", "req_val": 0,
        "perks": {"game_reward_pct": 12.0, "daily_coins_bonus": 50},
        "description": "+12% game rewards, +50 daily coins",
    },
    "chilltopia_royalty": {
        "display": "[ChillTopia Royalty]", "tier": "Mythic", "source": "Shop",
        "category": "shop", "price": 1_500_000, "buyable": True,
        "req_type": "", "req_val": 0,
        "perks": {"game_reward_pct": 15.0, "daily_coins_bonus": 100},
        "description": "+15% game rewards, +100 daily coins",
    },
    "vip_player": {
        "display": "[VIP Player]", "tier": "Epic", "source": "Shop",
        "category": "shop", "price": 50_000, "buyable": True,
        "req_type": "", "req_val": 0,
        "perks": {"shop_discount_pct": 3.0},
        "description": "+3% shop discount",
    },
    "casino_regular": {
        "display": "[Casino Regular]", "tier": "Epic", "source": "Shop",
        "category": "shop", "price": 75_000, "buyable": True,
        "req_type": "", "req_val": 0,
        "perks": {"casino_reward_pct": 3.0},
        "description": "+3% casino rewards",
    },
    "chill_elite": {
        "display": "[Chill Elite]", "tier": "Legendary", "source": "Shop",
        "category": "shop", "price": 150_000, "buyable": True,
        "req_type": "", "req_val": 0,
        "perks": {"game_reward_pct": 5.0, "daily_coins_bonus": 25},
        "description": "+5% game rewards, +25 daily coins",
    },
    # ── Fishing achievement ───────────────────────────────────────────────────
    "new_angler": {
        "display": "[New Angler]", "tier": "Common", "source": "Achievement",
        "category": "fishing", "price": 0, "buyable": False,
        "req_type": "fish_caught", "req_val": 100,
        "perks": {"fishing_coin_pct": 2.0},
        "description": "Catch 100 fish | +2% fishing coins",
        "announce": False,
    },
    "skilled_fisher": {
        "display": "[Skilled Fisher]", "tier": "Rare", "source": "Achievement",
        "category": "fishing", "price": 0, "buyable": False,
        "req_type": "fish_caught", "req_val": 1_000,
        "perks": {"fishing_coin_pct": 3.0, "fishing_rare_pct": 1.0},
        "description": "Catch 1,000 fish | +3% fishing, +1% rare",
        "announce": False,
    },
    "sea_hunter": {
        "display": "[Sea Hunter]", "tier": "Epic", "source": "Achievement",
        "category": "fishing", "price": 0, "buyable": False,
        "req_type": "fish_caught", "req_val": 10_000,
        "perks": {"fishing_coin_pct": 5.0, "fishing_rare_pct": 2.0},
        "description": "Catch 10,000 fish | +5% fishing, +2% rare",
        "announce": True,
    },
    "deep_sea_legend": {
        "display": "[Deep Sea Legend]", "tier": "Legendary", "source": "Achievement",
        "category": "fishing", "price": 0, "buyable": False,
        "req_type": "fish_caught", "req_val": 100_000,
        "perks": {"fishing_coin_pct": 10.0, "fishing_rare_pct": 5.0},
        "description": "Catch 100,000 fish | +10% fishing, +5% rare",
        "announce": True,
    },
    "ocean_god": {
        "display": "[Ocean God]", "tier": "Mythic", "source": "Achievement",
        "category": "fishing", "price": 0, "buyable": False,
        "req_type": "fish_caught", "req_val": 1_000_000,
        "perks": {"fishing_coin_pct": 15.0, "fishing_rare_pct": 8.0,
                  "fishing_cooldown_pct": 10.0},
        "description": "Catch 1M fish | +15% fishing, +8% rare, -10% cooldown",
        "announce": True,
    },
    # ── Mining achievement ────────────────────────────────────────────────────
    "stone_miner": {
        "display": "[Stone Miner]", "tier": "Common", "source": "Achievement",
        "category": "mining", "price": 0, "buyable": False,
        "req_type": "ores_mined", "req_val": 1_000,
        "perks": {"mining_coin_pct": 2.0},
        "description": "Mine 1,000 ores | +2% mining coins",
        "announce": False,
    },
    "iron_breaker": {
        "display": "[Iron Breaker]", "tier": "Rare", "source": "Achievement",
        "category": "mining", "price": 0, "buyable": False,
        "req_type": "ores_mined", "req_val": 10_000,
        "perks": {"mining_coin_pct": 5.0},
        "description": "Mine 10,000 ores | +5% mining coins",
        "announce": False,
    },
    "crystal_baron": {
        "display": "[Crystal Baron]", "tier": "Epic", "source": "Achievement",
        "category": "mining", "price": 0, "buyable": False,
        "req_type": "ores_mined", "req_val": 100_000,
        "perks": {"mining_coin_pct": 10.0, "mining_rare_pct": 3.0},
        "description": "Mine 100,000 ores | +10% mining, +3% rare",
        "announce": True,
    },
    "mine_lord": {
        "display": "[Mine Lord]", "tier": "Legendary", "source": "Achievement",
        "category": "mining", "price": 0, "buyable": False,
        "req_type": "ores_mined", "req_val": 500_000,
        "perks": {"mining_coin_pct": 12.0, "mining_rare_pct": 5.0},
        "description": "Mine 500,000 ores | +12% mining, +5% rare",
        "announce": True,
    },
    "core_breaker": {
        "display": "[Core Breaker]", "tier": "Mythic", "source": "Achievement",
        "category": "mining", "price": 0, "buyable": False,
        "req_type": "ores_mined", "req_val": 1_000_000,
        "perks": {"mining_coin_pct": 15.0, "mining_rare_pct": 8.0,
                  "mining_cooldown_pct": 10.0},
        "description": "Mine 1M ores | +15% mining, +8% rare, -10% cooldown",
        "announce": True,
    },
    # ── Casino achievement ────────────────────────────────────────────────────
    "card_rookie": {
        "display": "[Card Rookie]", "tier": "Common", "source": "Achievement",
        "category": "casino", "price": 0, "buyable": False,
        "req_type": "casino_hands_played", "req_val": 25,
        "perks": {"casino_reward_pct": 1.0},
        "description": "Play 25 casino hands | +1% casino",
        "announce": False,
    },
    "table_regular": {
        "display": "[Table Regular]", "tier": "Rare", "source": "Achievement",
        "category": "casino", "price": 0, "buyable": False,
        "req_type": "casino_hands_played", "req_val": 100,
        "perks": {"casino_reward_pct": 2.0},
        "description": "Play 100 casino hands | +2% casino",
        "announce": False,
    },
    "sharp_player": {
        "display": "[Sharp Player]", "tier": "Epic", "source": "Achievement",
        "category": "casino", "price": 0, "buyable": False,
        "req_type": "casino_hands_won", "req_val": 100,
        "perks": {"casino_reward_pct": 3.0},
        "description": "Win 100 casino hands | +3% casino",
        "announce": True,
    },
    "high_roller_ach": {
        "display": "[High Roller]", "tier": "Epic", "source": "Achievement",
        "category": "casino", "price": 0, "buyable": False,
        "req_type": "casino_lifetime_wagered", "req_val": 1_000_000,
        "perks": {"casino_reward_pct": 5.0},
        "description": "Wager 1M coins lifetime | +5% casino",
        "announce": True,
    },
    "casino_boss": {
        "display": "[Casino Boss]", "tier": "Legendary", "source": "Achievement",
        "category": "casino", "price": 0, "buyable": False,
        "req_type": "casino_lifetime_won", "req_val": 5_000_000,
        "perks": {"casino_reward_pct": 7.0},
        "description": "Win 5M coins from casino | +7% casino",
        "announce": True,
    },
    "jackpot_king": {
        "display": "[Jackpot King]", "tier": "Legendary", "source": "Achievement",
        "category": "casino", "price": 0, "buyable": False,
        "req_type": "casino_biggest_win", "req_val": 1_000_000,
        "perks": {"casino_reward_pct": 5.0, "daily_coins_bonus": 3},
        "description": "Win 1M+ in one hand | +5% casino, +3 daily",
        "announce": True,
    },
    "royal_flush_legend": {
        "display": "[Royal Flush Legend]", "tier": "Mythic", "source": "Achievement",
        "category": "casino", "price": 0, "buyable": False,
        "req_type": "poker_royal_flush_wins", "req_val": 1,
        "perks": {"poker_reward_pct": 10.0},
        "description": "Win poker with royal flush | +10% poker",
        "announce": True,
    },
    "blackjack_god": {
        "display": "[Blackjack God]", "tier": "Mythic", "source": "Achievement",
        "category": "casino", "price": 0, "buyable": False,
        "req_type": "blackjack_wins", "req_val": 500,
        "perks": {"blackjack_reward_pct": 8.0},
        "description": "Win 500 BJ hands | +8% blackjack",
        "announce": True,
    },
    "all_in_demon": {
        "display": "[All-In Demon]", "tier": "Mythic", "source": "Achievement",
        "category": "casino", "price": 0, "buyable": False,
        "req_type": "poker_allin_wins", "req_val": 100,
        "perks": {"poker_reward_pct": 10.0},
        "description": "Win 100 all-in poker hands | +10% poker",
        "announce": True,
    },
    "casino_billionaire": {
        "display": "[Casino Billionaire]", "tier": "Mythic", "source": "Achievement",
        "category": "casino", "price": 0, "buyable": False,
        "req_type": "casino_lifetime_won", "req_val": 1_000_000_000,
        "perks": {"casino_reward_pct": 15.0, "daily_coins_bonus": 100,
                  "shop_discount_pct": 5.0},
        "description": "Win 1B from casino | +15% casino, +100 daily",
        "announce": True,
    },
    # ── Wealth achievement ────────────────────────────────────────────────────
    "big_spender": {
        "display": "[Big Spender]", "tier": "Rare", "source": "Achievement",
        "category": "wealth", "price": 0, "buyable": False,
        "req_type": "lifetime_chillcoins_spent", "req_val": 100_000,
        "perks": {"shop_discount_pct": 2.0},
        "description": "Spend 100K coins | +2% shop discount",
        "announce": False,
    },
    "money_maker": {
        "display": "[Money Maker]", "tier": "Epic", "source": "Achievement",
        "category": "wealth", "price": 0, "buyable": False,
        "req_type": "lifetime_chillcoins_earned", "req_val": 500_000,
        "perks": {"daily_coins_bonus": 3},
        "description": "Earn 500K coins lifetime | +3 daily coins",
        "announce": True,
    },
    "millionaire_ach": {
        "display": "[Millionaire]", "tier": "Legendary", "source": "Achievement",
        "category": "wealth", "price": 0, "buyable": False,
        "req_type": "balance_milestone", "req_val": 1_000_000,
        "perks": {"game_reward_pct": 5.0},
        "description": "Hold 1M coins balance | +5% game rewards",
        "announce": True,
    },
    "multi_millionaire": {
        "display": "[Multi-Millionaire]", "tier": "Legendary", "source": "Achievement",
        "category": "wealth", "price": 0, "buyable": False,
        "req_type": "balance_milestone", "req_val": 10_000_000,
        "perks": {"game_reward_pct": 8.0, "shop_discount_pct": 5.0},
        "description": "Hold 10M coins | +8% game, +5% shop discount",
        "announce": True,
    },
    "billionaire": {
        "display": "[Billionaire]", "tier": "Mythic", "source": "Achievement",
        "category": "wealth", "price": 0, "buyable": False,
        "req_type": "balance_milestone", "req_val": 1_000_000_000,
        "perks": {"game_reward_pct": 15.0, "shop_discount_pct": 10.0,
                  "daily_coins_bonus": 100},
        "description": "Hold 1B coins | +15% game, +10% discount",
        "announce": True,
    },
    "chilltopia_tycoon": {
        "display": "[ChillTopia Tycoon]", "tier": "Mythic", "source": "Achievement",
        "category": "wealth", "price": 0, "buyable": False,
        "req_type": "lifetime_chillcoins_earned", "req_val": 5_000_000_000,
        "perks": {"game_reward_pct": 20.0, "shop_discount_pct": 15.0,
                  "daily_coins_bonus": 150},
        "description": "Earn 5B coins lifetime | +20% game, +15% shop",
        "announce": True,
    },
    # ── Social / room activity ────────────────────────────────────────────────
    "chill_regular": {
        "display": "[Chill Regular]", "tier": "Common", "source": "Achievement",
        "category": "social", "price": 0, "buyable": False,
        "req_type": "room_visit_days", "req_val": 7,
        "perks": {"daily_coins_bonus": 5},
        "description": "Visit 7 days | +5 daily coins",
        "announce": False,
    },
    "chilltopia_citizen": {
        "display": "[ChillTopia Citizen]", "tier": "Rare", "source": "Achievement",
        "category": "social", "price": 0, "buyable": False,
        "req_type": "room_visit_days", "req_val": 30,
        "perks": {"daily_coins_bonus": 25},
        "description": "Visit 30 days | +25 daily coins",
        "announce": False,
    },
    "room_legend": {
        "display": "[Room Legend]", "tier": "Epic", "source": "Achievement",
        "category": "social", "price": 0, "buyable": False,
        "req_type": "room_visit_days", "req_val": 100,
        "perks": {"daily_coins_bonus": 50},
        "description": "Visit 100 days | +50 daily coins",
        "announce": True,
    },
    "chilltopia_og": {
        "display": "[ChillTopia OG]", "tier": "Legendary", "source": "Achievement",
        "category": "social", "price": 0, "buyable": False,
        "req_type": "room_visit_days", "req_val": 365,
        "perks": {"daily_coins_bonus": 100},
        "description": "Visit 365 days | +100 daily coins",
        "announce": True,
    },
    # ── Supporter / tipping ───────────────────────────────────────────────────
    "supporter": {
        "display": "[Supporter]", "tier": "Rare", "source": "Achievement",
        "category": "supporter", "price": 0, "buyable": False,
        "req_type": "lifetime_gold_tipped", "req_val": 1_000,
        "perks": {"luxe_ticket_bonus_pct": 2.0},
        "description": "Tip 1,000 gold | +2% Luxe Ticket bonus",
        "announce": False,
    },
    "vip_supporter": {
        "display": "[VIP Supporter]", "tier": "Epic", "source": "Achievement",
        "category": "supporter", "price": 0, "buyable": False,
        "req_type": "lifetime_gold_tipped", "req_val": 10_000,
        "perks": {"luxe_ticket_bonus_pct": 5.0},
        "description": "Tip 10,000 gold | +5% Luxe Ticket bonus",
        "announce": True,
    },
    "whale": {
        "display": "[Whale]", "tier": "Legendary", "source": "Achievement",
        "category": "supporter", "price": 0, "buyable": False,
        "req_type": "lifetime_gold_tipped", "req_val": 100_000,
        "perks": {"luxe_ticket_bonus_pct": 8.0},
        "description": "Tip 100,000 gold | +8% Luxe Ticket bonus",
        "announce": True,
    },
    "chilltopia_patron": {
        "display": "[ChillTopia Patron]", "tier": "Mythic", "source": "Achievement",
        "category": "supporter", "price": 0, "buyable": False,
        "req_type": "lifetime_gold_tipped", "req_val": 1_000_000,
        "perks": {"luxe_ticket_bonus_pct": 10.0},
        "description": "Tip 1M gold | +10% Luxe Ticket bonus",
        "announce": True,
    },
    # ── Game master ───────────────────────────────────────────────────────────
    "game_rookie": {
        "display": "[Game Rookie]", "tier": "Common", "source": "Achievement",
        "category": "games", "price": 0, "buyable": False,
        "req_type": "minigames_played", "req_val": 50,
        "perks": {"game_reward_pct": 2.0},
        "description": "Play 50 mini-games | +2% game rewards",
        "announce": False,
    },
    "game_addict": {
        "display": "[Game Addict]", "tier": "Rare", "source": "Achievement",
        "category": "games", "price": 0, "buyable": False,
        "req_type": "minigames_played", "req_val": 500,
        "perks": {"game_reward_pct": 5.0},
        "description": "Play 500 mini-games | +5% game rewards",
        "announce": False,
    },
    "arcade_master": {
        "display": "[Arcade Master]", "tier": "Epic", "source": "Achievement",
        "category": "games", "price": 0, "buyable": False,
        "req_type": "minigames_won", "req_val": 1_000,
        "perks": {"game_reward_pct": 8.0},
        "description": "Win 1,000 mini-games | +8% game rewards",
        "announce": True,
    },
    "chilltopia_champion": {
        "display": "[ChillTopia Champion]", "tier": "Legendary", "source": "Achievement",
        "category": "games", "price": 0, "buyable": False,
        "req_type": "minigames_won", "req_val": 10_000,
        "perks": {"game_reward_pct": 12.0},
        "description": "Win 10,000 mini-games | +12% game rewards",
        "announce": True,
    },
    # ── Jail / bounty ─────────────────────────────────────────────────────────
    "jailbird": {
        "display": "[Jailbird]", "tier": "Common", "source": "Achievement",
        "category": "jail", "price": 0, "buyable": False,
        "req_type": "times_jailed", "req_val": 10,
        "perks": {"bail_discount_pct": 5.0},
        "description": "Get jailed 10 times | -5% bail cost",
        "announce": False,
    },
    "most_wanted": {
        "display": "[Most Wanted]", "tier": "Rare", "source": "Achievement",
        "category": "jail", "price": 0, "buyable": False,
        "req_type": "players_jailed", "req_val": 100,
        "perks": {"jail_reward_pct": 5.0},
        "description": "Jail 100 players | +5% jail reward",
        "announce": False,
    },
    "warden": {
        "display": "[Warden]", "tier": "Epic", "source": "Achievement",
        "category": "jail", "price": 0, "buyable": False,
        "req_type": "players_jailed", "req_val": 1_000,
        "perks": {"jail_reward_pct": 10.0},
        "description": "Jail 1,000 players | +10% jail reward",
        "announce": True,
    },
    "escape_artist": {
        "display": "[Escape Artist]", "tier": "Rare", "source": "Achievement",
        "category": "jail", "price": 0, "buyable": False,
        "req_type": "bails_paid", "req_val": 100,
        "perks": {"bail_discount_pct": 10.0},
        "description": "Bail out 100 times | -10% bail cost",
        "announce": False,
    },
    # ── Collector ─────────────────────────────────────────────────────────────
    "badge_collector": {
        "display": "[Badge Collector]", "tier": "Rare", "source": "Achievement",
        "category": "collector", "price": 0, "buyable": False,
        "req_type": "badges_owned", "req_val": 10,
        "perks": {"shop_discount_pct": 2.0},
        "description": "Own 10 badges | +2% shop discount",
        "announce": False,
    },
    "badge_hoarder": {
        "display": "[Badge Hoarder]", "tier": "Epic", "source": "Achievement",
        "category": "collector", "price": 0, "buyable": False,
        "req_type": "badges_owned", "req_val": 50,
        "perks": {"shop_discount_pct": 5.0},
        "description": "Own 50 badges | +5% shop discount",
        "announce": True,
    },
    "legend_collector": {
        "display": "[Legend Collector]", "tier": "Epic", "source": "Achievement",
        "category": "collector", "price": 0, "buyable": False,
        "req_type": "legendary_badges_owned", "req_val": 10,
        "perks": {"shop_discount_pct": 8.0},
        "description": "Own 10 legendary/mythic badges | +8% discount",
        "announce": True,
    },
    "completionist": {
        "display": "[Completionist]", "tier": "Legendary", "source": "Achievement",
        "category": "collector", "price": 0, "buyable": False,
        "req_type": "completionist", "req_val": 1,
        "perks": {"shop_discount_pct": 10.0, "daily_coins_bonus": 50},
        "description": "Own 100 badges + 25 titles | +10% discount",
        "announce": True,
    },
    # ── Seasonal ─────────────────────────────────────────────────────────────
    "weekly_poker_king": {
        "display": "[Weekly Poker King]", "tier": "Seasonal", "source": "Seasonal",
        "category": "seasonal", "price": 0, "buyable": False,
        "req_type": "seasonal", "req_val": 0,
        "perks": {"poker_reward_pct": 5.0},
        "description": "#1 weekly poker profit | +5% poker",
        "announce": True,
    },
    "weekly_fisher": {
        "display": "[Weekly Fisher]", "tier": "Seasonal", "source": "Seasonal",
        "category": "seasonal", "price": 0, "buyable": False,
        "req_type": "seasonal", "req_val": 0,
        "perks": {"fishing_coin_pct": 5.0},
        "description": "#1 weekly fish caught | +5% fishing",
        "announce": True,
    },
    "weekly_miner": {
        "display": "[Weekly Miner]", "tier": "Seasonal", "source": "Seasonal",
        "category": "seasonal", "price": 0, "buyable": False,
        "req_type": "seasonal", "req_val": 0,
        "perks": {"mining_coin_pct": 5.0},
        "description": "#1 weekly ores mined | +5% mining",
        "announce": True,
    },
    "weekly_whale": {
        "display": "[Weekly Whale]", "tier": "Seasonal", "source": "Seasonal",
        "category": "seasonal", "price": 0, "buyable": False,
        "req_type": "seasonal", "req_val": 0,
        "perks": {"luxe_ticket_bonus_pct": 3.0},
        "description": "#1 weekly gold tipper | +3% Luxe Tickets",
        "announce": True,
    },
    "monthly_champion": {
        "display": "[Monthly Champion]", "tier": "Seasonal", "source": "Seasonal",
        "category": "seasonal", "price": 0, "buyable": False,
        "req_type": "seasonal", "req_val": 0,
        "perks": {"game_reward_pct": 10.0, "daily_coins_bonus": 50},
        "description": "#1 monthly overall | +10% game, +50 daily",
        "announce": True,
    },
    # ── Secret ────────────────────────────────────────────────────────────────
    "lucky_devil": {
        "display": "[Lucky Devil]", "tier": "Secret", "source": "Secret",
        "category": "secret", "price": 0, "buyable": False, "secret": True,
        "req_type": "secret", "req_val": 0,
        "perks": {"casino_reward_pct": 3.0},
        "description": "Hidden requirement | +3% casino",
        "secret_hint": "Win a very low-odds casino event",
    },
    "comeback_king": {
        "display": "[Comeback King]", "tier": "Secret", "source": "Secret",
        "category": "secret", "price": 0, "buyable": False, "secret": True,
        "req_type": "secret", "req_val": 0,
        "perks": {"casino_reward_pct": 5.0},
        "description": "Hidden requirement | +5% casino",
        "secret_hint": "Win after being nearly broke",
    },
    "whale_whisperer": {
        "display": "[Whale Whisperer]", "tier": "Secret", "source": "Secret",
        "category": "secret", "price": 0, "buyable": False, "secret": True,
        "req_type": "secret", "req_val": 0,
        "perks": {"fishing_coin_pct": 5.0},
        "description": "Hidden requirement | +5% fishing",
        "secret_hint": "Catch an ultra rare fish",
    },
    "vault_breaker": {
        "display": "[Vault Breaker]", "tier": "Secret", "source": "Secret",
        "category": "secret", "price": 0, "buyable": False, "secret": True,
        "req_type": "secret", "req_val": 0,
        "perks": {"mining_coin_pct": 5.0},
        "description": "Hidden requirement | +5% mining",
        "secret_hint": "Mine an ultra rare ore",
    },
    # ── Craft/rep titles (keep compat with existing grants) ───────────────────
    "lounge_miner": {
        "display": "[Lounge Miner]", "tier": "Rare", "source": "Achievement",
        "category": "mining", "price": 0, "buyable": False,
        "req_type": "craft", "req_val": 0,
        "perks": {"mining_coin_pct": 3.0},
        "description": "Craft reward | +3% mining coins",
    },
    "master_miner": {
        "display": "[Master Miner]", "tier": "Epic", "source": "Achievement",
        "category": "mining", "price": 0, "buyable": False,
        "req_type": "craft", "req_val": 0,
        "perks": {"mining_coin_pct": 5.0},
        "description": "Craft reward | +5% mining coins",
    },
    "starfinder": {
        "display": "[Starfinder]", "tier": "Legendary", "source": "Achievement",
        "category": "mining", "price": 0, "buyable": False,
        "req_type": "craft", "req_val": 0,
        "perks": {"mining_coin_pct": 8.0, "mining_rare_pct": 3.0},
        "description": "Craft reward | +8% mining, +3% rare",
    },
}

# Category → friendly label
_CAT_LABELS: dict[str, str] = {
    "shop": "Shop", "fishing": "Fishing", "mining": "Mining",
    "casino": "Casino", "wealth": "Wealth", "social": "Social",
    "supporter": "Supporter", "games": "Games", "jail": "Jail",
    "collector": "Collector", "seasonal": "Seasonal", "secret": "Secret",
    "legacy": "Legacy",
}

# Titles moved from Shop to Achievement/Legacy — old owners keep them as Legacy
_LEGACY_SHOP_TITLES: frozenset[str] = frozenset({
    "trivia_king", "word_master", "riddle_lord",
    "casino_rat", "high_roller", "millionaire",
})

# Tiers considered "public announce" on unlock
_ANNOUNCE_TIERS = {"Epic", "Legendary", "Mythic", "Exclusive", "Seasonal"}

# ---------------------------------------------------------------------------
# Perk caps (after stacking title + badge + event)
# ---------------------------------------------------------------------------
PERK_CAPS: dict[str, float] = {
    "game_reward_pct":       20.0,
    "casino_reward_pct":     15.0,
    "poker_reward_pct":      15.0,
    "blackjack_reward_pct":  15.0,
    "fishing_coin_pct":      20.0,
    "mining_coin_pct":       20.0,
    "fishing_rare_pct":      10.0,
    "mining_rare_pct":       10.0,
    "fishing_cooldown_pct":  20.0,
    "mining_cooldown_pct":   20.0,
    "daily_coins_bonus":     250.0,
    "shop_discount_pct":     15.0,
    "luxe_ticket_bonus_pct": 10.0,
    "bail_discount_pct":     15.0,
    "jail_reward_pct":       15.0,
    "trivia_bonus":          50.0,
    "scramble_bonus":        50.0,
    "riddle_bonus":          50.0,
}

# VIP perk values awarded to active VIP holders (stacked in get_combined_perks)
_VIP_BOOSTS: dict[str, float] = {
    "mining_cooldown_pct":   5.0,
    "fishing_cooldown_pct":  5.0,
}

# Active room/mining event -> perk-dict mapping used by get_active_boosts
_EVENT_PERK_MAP: dict[str, dict[str, float]] = {
    "lucky_rush":             {"mining_rare_pct": 5.0, "fishing_rare_pct": 5.0},
    "heavy_ore_rush":         {"mining_coin_pct": 8.0},
    "ore_value_surge":        {"mining_coin_pct": 15.0},
    "double_mxp":             {"mining_coin_pct": 5.0},
    "mining_haste":           {"mining_cooldown_pct": 10.0},
    "legendary_rush":         {"mining_rare_pct": 8.0},
    "prismatic_hunt":         {"mining_rare_pct": 10.0},
    "exotic_hunt":            {"mining_rare_pct": 10.0},
    "admins_mining_blessing": {"mining_coin_pct": 15.0, "mining_rare_pct": 10.0,
                               "mining_cooldown_pct": 10.0},
    "ultimate_mining_rush":   {"mining_coin_pct": 15.0, "mining_rare_pct": 10.0,
                               "mining_cooldown_pct": 10.0},
    "casino_night":           {"casino_reward_pct": 5.0},
    "trivia_rush":            {"trivia_bonus": 10.0},
    "collection_hunt":        {"mining_rare_pct": 5.0, "fishing_rare_pct": 5.0},
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expired(expires_at: str) -> bool:
    if not expires_at:
        return False
    try:
        dt = datetime.fromisoformat(expires_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > dt
    except Exception:
        return False


def _fmt(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1_000}K"
    return f"{n:,}"


async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


async def _say(bot: BaseBot, msg: str) -> None:
    try:
        await bot.highrise.chat(msg[:249])
    except Exception:
        pass


def _is_admin(username: str) -> bool:
    from modules.admin_cmds import is_admin
    return is_admin(username)


# ---------------------------------------------------------------------------
# Backward-compat mirror: copy old owned_items (type=title) → user_titles
# ---------------------------------------------------------------------------

def _mirror_old_titles(user_id: str, username: str) -> None:
    """Copy old shop.py owned titles → user_titles.
    Game-specific titles (trivia_king etc.) get source='Legacy'."""
    try:
        old_owned = db.get_owned_items(user_id)
        for item in old_owned:
            if item.get("item_type") != "title":
                continue
            tid = item["item_id"]
            if tid not in TITLE_CATALOG:
                continue
            # Determine source: legacy if moved from shop to achievement
            if tid in _LEGACY_SHOP_TITLES:
                src = "Legacy"
            else:
                src = "Shop"
            if not db.has_user_title(user_id, tid):
                db.add_user_title(user_id, username, tid, src)
            elif tid in _LEGACY_SHOP_TITLES:
                # Upgrade existing record from Shop → Legacy
                try:
                    db.update_user_title_source(user_id, tid, "Legacy")
                except Exception:
                    pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Perk computation
# ---------------------------------------------------------------------------

def get_title_v2_perks(user_id: str) -> dict:
    """Return perks dict for the player's equipped title in V2 system.
    Falls back to shop.py benefits for old-style keys."""
    try:
        eq = db.get_equipped_ids(user_id)
        tid = eq.get("title_id") or ""
        if not tid:
            return {}
        t = TITLE_CATALOG.get(tid)
        if not t:
            return {}
        # Check expiry for seasonal titles
        if t.get("source") == "Seasonal":
            user_t = db.get_user_title(user_id, tid)
            if user_t and _expired(user_t.get("expires_at", "")):
                return {}
        return dict(t.get("perks", {}))
    except Exception:
        return {}


def get_combined_perks(user_id: str) -> dict:
    """Return capped combined perks: equipped title + equipped badge + active VIP."""
    perks: dict[str, float] = {}
    try:
        t_perks = get_title_v2_perks(user_id)
        for k, v in t_perks.items():
            perks[k] = perks.get(k, 0.0) + v

        # Badge perks from badge_market equipped badge
        try:
            from modules.badge_market import _get_badge_perks
            b_perks = _get_badge_perks(user_id)
            for k, v in b_perks.items():
                perks[k] = perks.get(k, 0.0) + v
        except Exception:
            pass

        # VIP perks (small cooldown reduction for active VIP holders)
        try:
            if db.owns_item(user_id, "vip"):
                vip_exp = db.get_room_setting(f"vip_expires_{user_id}", "")
                from modules.vip import _calc_vip_remaining as _cvr
                rem = _cvr(vip_exp) if vip_exp else ""
                if rem and rem != "Expired":
                    for k, v in _VIP_BOOSTS.items():
                        perks[k] = perks.get(k, 0.0) + v
        except Exception:
            pass

    except Exception:
        pass

    # Apply caps
    for k, cap in PERK_CAPS.items():
        if k in perks:
            perks[k] = min(perks[k], cap)
    return perks


def get_active_boosts(user_id: str) -> dict:
    """Return {'perks': dict, 'sources': dict} stacking title+badge+event+VIP.

    sources keys: title (display|None), badge (id|None), event (name|None), vip (rem|None).
    """
    perks: dict[str, float] = {}
    sources: dict[str, str | None] = {
        "title": None, "badge": None, "event": None, "vip": None,
    }

    # Title perks
    try:
        eq  = db.get_equipped_ids(user_id)
        tid = eq.get("title_id") or ""
        t_perks = get_title_v2_perks(user_id)
        if t_perks and tid:
            t = TITLE_CATALOG.get(tid)
            sources["title"] = t.get("display", tid) if t else tid
        for k, v in t_perks.items():
            perks[k] = perks.get(k, 0.0) + v
    except Exception:
        pass

    # Badge perks
    try:
        from modules.badge_market import _get_badge_perks as _gbp
        b_perks = _gbp(user_id)
        if b_perks:
            eq2 = db.get_equipped_ids(user_id)
            sources["badge"] = eq2.get("badge_id") or None
        for k, v in b_perks.items():
            perks[k] = perks.get(k, 0.0) + v
    except Exception:
        pass

    # Room event perks
    try:
        gen_ev = db.get_active_event()
        if gen_ev:
            eid  = gen_ev.get("event_id", "")
            ev_p = _EVENT_PERK_MAP.get(eid, {})
            if ev_p:
                try:
                    from modules.events import EVENTS as _EVS
                    ename = _EVS.get(eid, {}).get("name", eid)
                except Exception:
                    ename = eid
                sources["event"] = ename
                for k, v in ev_p.items():
                    perks[k] = perks.get(k, 0.0) + v
    except Exception:
        pass

    # Mining event perks
    try:
        mine_ev = db.get_active_mining_event()
        if mine_ev:
            eid  = mine_ev.get("event_id", "")
            ev_p = _EVENT_PERK_MAP.get(eid, {})
            if ev_p:
                if not sources["event"]:
                    try:
                        from modules.events import EVENTS as _EVS2
                        sources["event"] = _EVS2.get(eid, {}).get("name", eid)
                    except Exception:
                        sources["event"] = eid
                for k, v in ev_p.items():
                    perks[k] = perks.get(k, 0.0) + v
    except Exception:
        pass

    # VIP perks
    try:
        if db.owns_item(user_id, "vip"):
            vip_exp = db.get_room_setting(f"vip_expires_{user_id}", "")
            from modules.vip import _calc_vip_remaining as _cvr
            rem = _cvr(vip_exp) if vip_exp else ""
            if rem and rem != "Expired":
                sources["vip"] = rem
                for k, v in _VIP_BOOSTS.items():
                    perks[k] = perks.get(k, 0.0) + v
    except Exception:
        pass

    # Apply caps
    for k, cap in PERK_CAPS.items():
        if k in perks:
            perks[k] = min(perks[k], cap)

    return {"perks": perks, "sources": sources}


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def increment_title_stat(user_id: str, username: str, stat: str,
                          amount: int = 1) -> None:
    """Increment a user_title_stats column and check for newly unlocked titles."""
    try:
        db.increment_title_stat(user_id, username, stat, amount)
    except Exception:
        pass


def _get_stats(user_id: str) -> dict:
    try:
        return db.get_title_stats(user_id) or {}
    except Exception:
        return {}


def _check_stat_titles(user_id: str, username: str,
                        stat: str | None = None) -> list[str]:
    """Return list of newly-unlocked title IDs (inserts into user_titles)."""
    stats = _get_stats(user_id)
    if not stats:
        return []

    newly: list[str] = []
    for tid, t in TITLE_CATALOG.items():
        if t.get("source") not in ("Achievement",):
            continue
        req = t.get("req_type", "")
        if stat and req != stat:
            continue
        if req in ("", "seasonal", "secret", "craft"):
            continue
        val = t.get("req_val", 0)
        if val <= 0:
            continue

        # Special computed checks
        if req == "balance_milestone":
            current = db.get_balance(user_id)
        elif req == "badges_owned":
            current = db.get_owned_item_counts(user_id).get("badges", 0)
        elif req == "legendary_badges_owned":
            try:
                from modules.badge_market import count_legendary_badges
                current = count_legendary_badges(user_id)
            except Exception:
                current = 0
        elif req == "completionist":
            counts = db.get_owned_item_counts(user_id)
            owned_t = len(db.get_user_titles(user_id))
            current = 1 if (counts.get("badges", 0) >= 100
                            and owned_t >= 25) else 0
        else:
            current = stats.get(req, 0)

        if current >= val and not db.has_user_title(user_id, tid):
            db.add_user_title(user_id, username, tid, t["source"])
            newly.append(tid)

    return newly


# ---------------------------------------------------------------------------
# Unlock announcement
# ---------------------------------------------------------------------------

async def announce_title_unlock(bot: BaseBot, user: User,
                                  title_id: str) -> None:
    t = TITLE_CATALOG.get(title_id)
    if not t:
        return
    display = t["display"]
    tier    = t.get("tier", "")
    if tier in _ANNOUNCE_TIERS:
        req_desc = t.get("description", "").split("|")[0].strip()
        await _say(bot,
            f"🏆 @{user.username} unlocked {display}! {req_desc}")
    await _w(bot, user.id,
        f"🏆 You unlocked title: {display}\nEquip: !equiptitle {title_id}")


# ---------------------------------------------------------------------------
# Title lookup helpers
# ---------------------------------------------------------------------------

def _get_title(tid: str) -> dict | None:
    t = TITLE_CATALOG.get(tid)
    if t:
        return t
    # Fallback: check DB catalog
    try:
        return db.get_catalog_title(tid)
    except Exception:
        return None


def _user_owns(user_id: str, tid: str) -> bool:
    # V2 table
    if db.has_user_title(user_id, tid):
        return True
    # Old owned_items fallback
    try:
        return bool(db.owns_item(user_id, tid))
    except Exception:
        return False


def _user_equipped_title(user_id: str) -> str:
    eq = db.get_equipped_ids(user_id)
    return eq.get("title_id") or ""


# ---------------------------------------------------------------------------
# Title session cache for number-equip
# ---------------------------------------------------------------------------
_title_session: dict[str, list[str]] = {}  # user_id → ordered list of title_ids


# ---------------------------------------------------------------------------
# PLAYER HANDLERS
# ---------------------------------------------------------------------------

async def handle_titles_menu(bot: BaseBot, user: User, args: list[str]) -> None:
    """!titles [category] — title menu or category browse."""
    if len(args) > 1:
        cat = args[1].lower()
        if cat in _CAT_LABELS:
            await _handle_category_browse(bot, user, cat, 1)
            return

    await _w(bot, user.id,
        "🏷️ Title Menu\n"
        "🛒 Shop: !titleshop\n"
        "🎒 Owned: !mytitles\n"
        "📈 Progress: !titleprogress\n"
        "⚡ Boosts: !myboosts\n"
        "📊 Stats: !mystats\n"
        "❓ Help: !titlehelp")


async def _handle_category_browse(bot: BaseBot, user: User,
                                    cat: str, page: int) -> None:
    titles = [(tid, t) for tid, t in TITLE_CATALOG.items()
              if t.get("category") == cat]
    if not titles:
        await _w(bot, user.id, f"No titles in category: {cat}")
        return
    PAGE = 5
    total = (len(titles) + PAGE - 1) // PAGE
    page  = max(1, min(page, total))
    start = (page - 1) * PAGE
    chunk = titles[start:start + PAGE]
    label = _CAT_LABELS.get(cat, cat.title())
    lines = [f"🏷️ {label} Titles {page}/{total}"]
    for tid, t in chunk:
        owned = _user_owns(user.id, tid)
        eq    = _user_equipped_title(user.id) == tid
        tag   = " [EQUIPPED]" if eq else (" [OWNED]" if owned else "")
        secret = t.get("secret") and not owned
        if secret:
            lines.append(f"🔒 [???] ID: {tid}")
        else:
            req = t.get("req_type", "")
            if req and req not in ("", "seasonal", "secret", "craft"):
                lines.append(f"{t['display']} | ID: {tid}{tag}")
            else:
                cost = f" {_fmt(t['price'])}c" if t.get("price", 0) > 0 else ""
                lines.append(f"{t['display']}{cost} | ID: {tid}{tag}")
    if page < total:
        lines.append(f"Next: !titles {cat} {page + 1}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_titleshop(bot: BaseBot, user: User, args: list[str]) -> None:
    """!titleshop [page] — shop titles."""
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
    _mirror_old_titles(user.id, user.username)
    shop_titles = [(tid, t) for tid, t in TITLE_CATALOG.items()
                   if t.get("source") == "Shop"]
    PAGE = 5
    total = (len(shop_titles) + PAGE - 1) // PAGE
    page  = max(1, min(page, total))
    start = (page - 1) * PAGE
    chunk = shop_titles[start:start + PAGE]

    # Apply shop discount from equipped perks
    perks    = get_combined_perks(user.id)
    disc_pct = perks.get("shop_discount_pct", 0.0)

    lines = [f"🏷️ Title Shop {page}/{total}"]
    for tid, t in chunk:
        owned = _user_owns(user.id, tid)
        eq    = _user_equipped_title(user.id) == tid
        price = t["price"]
        if disc_pct > 0:
            price = int(price * (1 - disc_pct / 100))
        tag  = " [EQUIPPED]" if eq else (" [OWNED]" if owned else "")
        lines.append(f"ID: {tid} {t['display']} — {_fmt(price)}c{tag}")
    lines.append(f"Buy: !buytitle <id>  Info: !titleinfo <id>")
    if page < total:
        lines.append(f"Next: !titleshop {page + 1}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_alltitles(bot: BaseBot, user: User, args: list[str]) -> None:
    """!alltitles [page] — all titles, grouped."""
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
    all_t = list(TITLE_CATALOG.items())
    PAGE  = 6
    total = (len(all_t) + PAGE - 1) // PAGE
    page  = max(1, min(page, total))
    start = (page - 1) * PAGE
    chunk = all_t[start:start + PAGE]
    lines = [f"🏷️ All Titles {page}/{total}"]
    for tid, t in chunk:
        owned  = _user_owns(user.id, tid)
        secret = t.get("secret") and not owned
        src    = t.get("source", "")[:3]
        if secret:
            lines.append(f"🔒 [???] ({src})")
        else:
            tag = " ✓" if owned else ""
            lines.append(f"{t['display']}{tag} ({src})")
    if page < total:
        lines.append(f"Next: !alltitles {page + 1}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_mytitles(bot: BaseBot, user: User, args: list[str]) -> None:
    """!mytitles — owned titles grouped by source (Shop / Achievement / Legacy / Seasonal)."""
    _mirror_old_titles(user.id, user.username)

    # Build full ordered ownership map: tid → source
    v2 = db.get_user_titles(user.id)
    owned_map: dict[str, str] = {r["title_id"]: r.get("source", "Shop") for r in v2}
    # Fallback: old owned_items not in V2 table
    for item in db.get_owned_items(user.id):
        if item["item_type"] == "title" and item["item_id"] not in owned_map:
            tid = item["item_id"]
            owned_map[tid] = "Legacy" if tid in _LEGACY_SHOP_TITLES else "Shop"

    if not owned_map:
        await _w(bot, user.id,
            "🏷️ No titles yet.\nShop: !titleshop\nProgress: !titleprogress")
        return

    eq_id = _user_equipped_title(user.id)

    # Cache all title IDs (numbered equip)
    all_ids = list(owned_map.keys())
    _title_session[user.id] = all_ids

    # Header whisper
    t_eq  = _get_title(eq_id) if eq_id else None
    eq_dsp = t_eq["display"] if t_eq else (eq_id or "None")
    await _w(bot, user.id,
        f"🏷️ Your Titles ({len(owned_map)} owned)\nEquipped: {eq_dsp}")

    # Group by source
    _GROUP_ORDER = ["Shop", "Achievement", "Legacy", "Seasonal", "Admin", "Secret"]
    groups: dict[str, list[str]] = {g: [] for g in _GROUP_ORDER}
    for i, (tid, src) in enumerate(owned_map.items(), 1):
        grp = src if src in groups else "Achievement"
        t   = _get_title(tid)
        dsp = t["display"] if t else f"[{tid}]"
        tag = " [E]" if tid == eq_id else ""
        groups[grp].append(f"{i}) {tid} {dsp}{tag}")

    for grp_name in _GROUP_ORDER:
        items = groups[grp_name]
        if not items:
            continue
        header = f"{grp_name}:\n"
        # Send up to 6 per group (fits in 249 chars)
        msg = header + "\n".join(items[:6])
        await _w(bot, user.id, msg[:249])

    await _w(bot, user.id,
        "Equip: !equiptitle <id|#>  Progress: !titleprogress")


async def handle_titleinfo_v2(bot: BaseBot, user: User, args: list[str]) -> None:
    """!titleinfo <id> — full title details."""
    _mirror_old_titles(user.id, user.username)
    tid = args[1].lower().strip() if len(args) > 1 else ""
    if not tid:
        await _w(bot, user.id, "Usage: !titleinfo <title_id>")
        return
    t = _get_title(tid)
    if not t:
        await _w(bot, user.id, f"Title '{tid}' not found. Try !titleshop")
        return
    owned = _user_owns(user.id, tid)
    eq    = _user_equipped_title(user.id) == tid

    # Determine effective display source (legacy check)
    owned_src = ""
    if owned:
        ut = db.get_user_title(user.id, tid)
        owned_src = (ut or {}).get("source", "")

    catalog_source = t.get("source", "?")
    is_legacy = owned and owned_src == "Legacy" and catalog_source == "Achievement"

    disp_source = "Legacy / Achievement" if is_legacy else catalog_source
    lines = [f"🏷️ {t['display']}",
             f"ID: {tid}",
             f"Source: {disp_source}  Tier: {t.get('tier','?')}"]

    if is_legacy:
        lines.append("This title is no longer sold in the shop.")

    if catalog_source == "Shop" and not is_legacy:
        price = t.get("price", 0)
        lines.append(f"Cost: {_fmt(price)} ChillCoins")
        if not owned:
            lines.append(f"Buy: !buytitle {tid}")
    else:
        req = t.get("req_type", "")
        val = t.get("req_val", 0)
        if req and req not in ("seasonal", "secret", "craft"):
            stats = _get_stats(user.id)
            cur   = stats.get(req, 0)
            lines.append(f"Requirement: {_fmt(val)} {req.replace('_',' ')}")
            lines.append(f"Progress: {_fmt(cur)} / {_fmt(val)}")
    perks = t.get("perks", {})
    if perks:
        perk_str = ", ".join(
            f"+{v}{'%' if 'pct' in k else ''} {k.replace('_',' ')}"
            if k not in ('fishing_cooldown_pct','mining_cooldown_pct')
            else f"-{v}% {k.replace('_cooldown_pct','').replace('_',' ')} cooldown"
            for k, v in perks.items()
        )
        lines.append(f"Perks: {perk_str[:100]}")
    if eq:
        lines.append("[EQUIPPED]")
    elif owned:
        lines.append(f"[OWNED] Equip: !equiptitle {tid}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_buytitle(bot: BaseBot, user: User, args: list[str]) -> None:
    """!buytitle <id> — buy a shop title."""
    _mirror_old_titles(user.id, user.username)
    tid = args[1].lower().strip() if len(args) > 1 else ""
    if not tid:
        await _w(bot, user.id, "Usage: !buytitle <title_id>\nShop: !titleshop")
        return
    t = _get_title(tid)
    if not t:
        await _w(bot, user.id, f"Title '{tid}' not found.")
        return
    if not t.get("buyable", False) or t.get("source") not in ("Shop",):
        src = t.get("source", "Achievement")
        req = t.get("req_type", "")
        cat = t.get("category", "")
        if req and req not in ("seasonal", "secret", "craft"):
            req_desc = t.get("description", "").split("|")[0].strip()
            await _w(bot, user.id,
                f"⚠️ {t['display']} is earned, not bought.\n"
                f"Requirement: {req_desc}\n"
                f"Progress: !titleprogress {cat}")
        elif src == "Legacy":
            await _w(bot, user.id,
                f"⚠️ {t['display']} is no longer sold in the shop.\n"
                f"It is now an earned title.")
        else:
            await _w(bot, user.id,
                f"⚠️ {t['display']} cannot be bought.\n"
                f"Progress: !titleprogress")
        return
    if _user_owns(user.id, tid):
        await _w(bot, user.id,
            f"You already own {t['display']}.\nEquip: !equiptitle {tid}")
        return

    # Apply shop discount
    perks    = get_combined_perks(user.id)
    disc_pct = perks.get("shop_discount_pct", 0.0)
    price    = t["price"]
    if disc_pct > 0:
        price = int(price * (1 - disc_pct / 100))

    balance = db.get_balance(user.id)
    if balance < price:
        await _w(bot, user.id,
            f"⚠️ Not enough ChillCoins.\nPrice: {_fmt(price)}\nBalance: {_fmt(balance)}")
        return

    db.adjust_balance(user.id, -price)
    db.add_owned_item(user.id, user.username, tid, "title")
    db.add_user_title(user.id, user.username, tid, "Shop")
    db.log_title_action("title_bought", user.id, user.username, tid,
                         details=f"price={price}")
    # Track spend stat
    increment_title_stat(user.id, user.username,
                          "lifetime_chillcoins_spent", price)
    disc_note = f" (disc {disc_pct:.0f}%)" if disc_pct > 0 else ""
    await _w(bot, user.id,
        f"✅ Bought title: {t['display']} for {_fmt(price)} coins{disc_note}.\n"
        f"Equip: !equiptitle {tid}")


async def handle_equiptitle(bot: BaseBot, user: User, args: list[str]) -> None:
    """!equiptitle <id|#> — equip an owned title."""
    _mirror_old_titles(user.id, user.username)
    raw = args[1].lower().strip() if len(args) > 1 else ""
    if not raw:
        await _w(bot, user.id,
            "Usage: !equiptitle <title_id>\nOwned: !mytitles")
        return

    # Number equip from !mytitles cache
    if raw.isdigit():
        cache = _title_session.get(user.id, [])
        idx   = int(raw) - 1
        if 0 <= idx < len(cache):
            raw = cache[idx]
        else:
            await _w(bot, user.id,
                "Invalid number. Use !mytitles first then !equiptitle <#>")
            return

    tid = raw
    t = _get_title(tid)
    if not t:
        await _w(bot, user.id, f"Title '{tid}' not found.")
        return
    if not _user_owns(user.id, tid):
        await _w(bot, user.id,
            f"⚠️ You do not own that title.\n"
            f"Progress: !titleprogress\nShop: !titleshop")
        return
    # Seasonal expiry check
    if t.get("source") == "Seasonal":
        user_t = db.get_user_title(user.id, tid)
        if user_t and _expired(user_t.get("expires_at", "")):
            await _w(bot, user.id,
                f"⚠️ That seasonal title has expired.")
            return

    eq_now = _user_equipped_title(user.id)
    if eq_now == tid:
        await _w(bot, user.id, "✅ That title is already equipped.")
        return

    db.set_equipped_item(user.id, "title", t["display"], tid)
    db.log_title_action("title_equipped", user.id, user.username, tid)
    await _w(bot, user.id,
        f"✅ Equipped title: {t['display']}\nBoosts: !myboosts")


async def handle_unequiptitle(bot: BaseBot, user: User) -> None:
    """!unequiptitle — remove current title."""
    db.clear_equipped_title(user.id)
    db.log_title_action("title_unequipped", user.id, user.username, "")
    await _w(bot, user.id, "✅ Title unequipped.")


async def handle_titlesearch(bot: BaseBot, user: User, args: list[str]) -> None:
    """!titlesearch <name> — search titles by display name or id."""
    query = " ".join(args[1:]).lower().strip() if len(args) > 1 else ""
    if not query:
        await _w(bot, user.id, "Usage: !titlesearch <name>")
        return
    results = [(tid, t) for tid, t in TITLE_CATALOG.items()
               if query in tid.lower() or query in t["display"].lower()]
    if not results:
        await _w(bot, user.id, f"No titles matching '{query}'.")
        return
    lines = [f"🔍 '{query}' results:"]
    for tid, t in results[:6]:
        owned = _user_owns(user.id, tid)
        tag   = " ✓" if owned else ""
        lines.append(f"{t['display']}{tag} — {tid}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_titleprogress(bot: BaseBot, user: User, args: list[str]) -> None:
    """!titleprogress [category] — show progress toward achievement titles."""
    _mirror_old_titles(user.id, user.username)
    cat = args[1].lower() if len(args) > 1 else ""
    stats = _get_stats(user.id)

    # Category filter
    cats = [cat] if cat in _CAT_LABELS else ["fishing", "mining", "casino",
                                               "wealth", "games", "social"]
    # Build next-title-per-category
    lines_out: list[str] = []
    for c in cats:
        c_titles = [(tid, t) for tid, t in TITLE_CATALOG.items()
                    if t.get("category") == c
                    and t.get("source") == "Achievement"
                    and t.get("req_type", "") not in ("", "seasonal","secret","craft")]
        c_titles.sort(key=lambda x: x[1].get("req_val", 0))

        # Find current highest owned
        cur_name = ""
        for tid, t in reversed(c_titles):
            if _user_owns(user.id, tid):
                cur_name = t["display"]
                break

        # Find next
        for tid, t in c_titles:
            if not _user_owns(user.id, tid):
                req = t.get("req_type", "")
                val = t.get("req_val", 0)
                cur = stats.get(req, 0)
                cat_label = _CAT_LABELS.get(c, c.title())
                if cat:
                    # Verbose single-category
                    lbl = f"🏷️ {cat_label} Titles"
                    if cur_name:
                        lbl += f"\nCurrent: {cur_name}"
                    lbl += (f"\nNext: {t['display']}\n"
                            f"Progress: {_fmt(cur)} / {_fmt(val)}")
                    await _w(bot, user.id, lbl[:249])
                    return
                else:
                    lines_out.append(
                        f"{cat_label}: {_fmt(cur)}/{_fmt(val)} → {t['display']}")
                break
        else:
            if c_titles:
                lbl = _CAT_LABELS.get(c, c.title())
                lines_out.append(f"{lbl}: ✅ All unlocked!")

    if not lines_out:
        await _w(bot, user.id, "No progress data yet. Start fishing/mining!")
        return
    header = "🏆 Title Progress"
    msg    = header + "\n" + "\n".join(lines_out[:6])
    await _w(bot, user.id, msg[:249])


async def handle_claimtitles(bot: BaseBot, user: User) -> None:
    """!claimtitles — claim all titles you qualify for."""
    _mirror_old_titles(user.id, user.username)
    newly = _check_stat_titles(user.id, user.username)
    if not newly:
        await _w(bot, user.id,
            "No new titles to claim.\nCheck progress: !titleprogress")
        return
    names = [_get_title(tid)["display"]
             for tid in newly if _get_title(tid)]
    msg = f"🏆 You unlocked {len(newly)} title(s):\n" + "\n".join(names[:8])
    await _w(bot, user.id, msg[:249])
    for tid in newly:
        t = _get_title(tid)
        if t and t.get("tier") in _ANNOUNCE_TIERS:
            await announce_title_unlock(bot, user, tid)


async def handle_myboosts(bot: BaseBot, user: User,
                           args: list[str] | None = None) -> None:
    """!myboosts / !perks / !titleperks — show active boosts from all sources."""
    target_user = user
    if args and len(args) > 1 and args[1].startswith("@") and _is_admin(user.username):
        uname = args[1].lstrip("@").lower()
        uid   = db.get_user_id_by_username(uname) or ""
        if not uid:
            await _w(bot, user.id, f"User @{uname} not found.")
            return
        class _FakeUser:
            id = uid
            username = uname
        target_user = _FakeUser()

    result  = get_active_boosts(target_user.id)
    perks   = result["perks"]
    sources = result["sources"]

    you    = "Your" if target_user.id == user.id else f"@{target_user.username}"
    lines  = [f"⚡ {you} Active Boosts",
              f"Title: {sources['title'] or 'None'}"]
    if sources["badge"]:
        lines.append(f"Badge: {sources['badge']}")
    if sources["event"]:
        lines.append(f"Event: {sources['event']}")
    if sources["vip"]:
        lines.append(f"💎 VIP: {sources['vip']} left")

    perk_lines: list[str] = []
    for k, v in perks.items():
        if not v:
            continue
        if k in ("fishing_cooldown_pct", "mining_cooldown_pct"):
            perk_lines.append(f"{k.replace('_pct','').replace('_',' ').title()}: -{v:.0f}%")
        elif "pct" in k:
            perk_lines.append(f"{k.replace('_pct','').replace('_',' ').title()}: +{v:.0f}%")
        else:
            perk_lines.append(f"{k.replace('_',' ').title()}: +{int(v)}")

    if perk_lines:
        lines.extend(perk_lines[:7])
    else:
        lines.append("No active boosts.")
        lines.append("Equip a title or badge!")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_boostaudit(bot: BaseBot, user: User,
                             args: list[str] | None = None) -> None:
    """!boostaudit [@user] — staff: full boost stack breakdown."""
    if not _is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return
    target_id   = user.id
    target_name = user.username
    if args and len(args) > 1:
        uname = args[1].lstrip("@").lower()
        uid   = db.get_user_id_by_username(uname) or ""
        if not uid:
            await _w(bot, user.id, f"User @{uname} not found.")
            return
        target_id, target_name = uid, uname

    result  = get_active_boosts(target_id)
    perks   = result["perks"]
    sources = result["sources"]

    lines = [f"🔍 Boost Audit: @{target_name}",
             f"Title: {sources['title'] or 'none'}",
             f"Badge: {sources['badge'] or 'none'}",
             f"Event: {sources['event'] or 'none'}",
             f"VIP: {sources['vip'] or 'inactive'}"]
    active = {k: v for k, v in perks.items() if v}
    if active:
        psum = ", ".join(
            f"{k}={'−' if 'cooldown' in k else '+'}{v:.0f}"
            for k, v in list(active.items())[:5]
        )
        lines.append(f"Perks: {psum}")
    else:
        lines.append("Perks: none")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_mystats(bot: BaseBot, user: User,
                          args: list[str] | None = None) -> None:
    """!mystats — show player title stats."""
    target_id   = user.id
    target_name = user.username
    if args and len(args) > 1 and args[1].startswith("@") and _is_admin(user.username):
        uname = args[1].lstrip("@").lower()
        uid   = db.get_user_id_by_username(uname) or ""
        if uid:
            target_id, target_name = uid, uname

    stats  = _get_stats(target_id)
    counts = db.get_owned_item_counts(target_id)
    n_titles = len(db.get_user_titles(target_id))

    you = "Your" if target_id == user.id else f"@{target_name}"
    await _w(bot, user.id,
        f"📊 {you} Stats\n"
        f"Fish Caught: {_fmt(stats.get('fish_caught',0))}\n"
        f"Ores Mined: {_fmt(stats.get('ores_mined',0))}\n"
        f"Casino Hands: {_fmt(stats.get('casino_hands_played',0))}\n"
        f"Casino Won: {_fmt(stats.get('casino_lifetime_won',0))}\n"
        f"Gold Tipped: {_fmt(stats.get('lifetime_gold_tipped',0))}\n"
        f"Badges: {counts.get('badges',0)} | Titles: {n_titles}")


async def handle_prestige(bot: BaseBot, user: User, args: list[str]) -> None:
    """!prestige [@user] — prestige flex page."""
    target_id   = user.id
    target_name = user.username
    if len(args) > 1 and args[1].startswith("@"):
        uname = args[1].lstrip("@").lower()
        uid   = db.get_user_id_by_username(uname) or ""
        if uid:
            target_id, target_name = uid, uname

    _mirror_old_titles(target_id, target_name)
    eq     = db.get_equipped_ids(target_id)
    tid    = eq.get("title_id") or ""
    t_disp = _get_title(tid)["display"] if (tid and _get_title(tid)) else "None"
    stats  = _get_stats(target_id)
    owned_t = db.get_user_titles(target_id)
    mythic_count = sum(1 for r in owned_t
                       if (_get_title(r["title_id"]) or {}).get("tier") == "Mythic")
    counts = db.get_owned_item_counts(target_id)
    await _w(bot, user.id,
        f"🌟 @{target_name} Prestige\n"
        f"Equipped: {t_disp}\n"
        f"Mythic Titles: {mythic_count}\n"
        f"Badges: {counts.get('badges',0)}\n"
        f"Casino Won: {_fmt(stats.get('casino_lifetime_won',0))}\n"
        f"Fish: {_fmt(stats.get('fish_caught',0))}\n"
        f"Ores: {_fmt(stats.get('ores_mined',0))}")


async def handle_titlehelp(bot: BaseBot, user: User) -> None:
    """!titlehelp — four message help with emojis."""
    await _w(bot, user.id,
        "🏷️ Title Commands\n"
        "🛒 Shop: !titleshop\n"
        "🌐 All: !alltitles\n"
        "🎒 Owned: !mytitles\n"
        "ℹ️ Info: !titleinfo rookie")
    await _w(bot, user.id,
        "💰 Buy: !buytitle rookie\n"
        "✅ Equip: !equiptitle rookie\n"
        "❌ Unequip: !unequiptitle\n"
        "📈 Progress: !titleprogress")
    await _w(bot, user.id,
        "⚡ Boosts: !myboosts\n"
        "📊 Stats: !mystats\n"
        "🏆 Claim: !claimtitles\n"
        "🌟 Prestige: !prestige")
    await _w(bot, user.id,
        "🔍 Search: !titlesearch lucky\n"
        "🎒 Loadouts: !loadouts\n"
        "⭐ Best: !besttitle casino\n"
        "🏅 LB: !titlelb")


async def handle_loadout(bot: BaseBot, user: User, args: list[str]) -> None:
    """!loadout save|equip <name> — save or equip a loadout."""
    _ALLOWED = {"fishing", "mining", "casino", "daily", "general"}
    sub  = args[1].lower() if len(args) > 1 else ""
    name = args[2].lower() if len(args) > 2 else ""
    if sub not in ("save", "equip") or name not in _ALLOWED:
        await _w(bot, user.id,
            "Usage: !loadout save|equip <name>\n"
            "Names: fishing mining casino daily general")
        return

    if sub == "save":
        eq    = db.get_equipped_ids(user.id)
        tid   = eq.get("title_id") or ""
        bid   = eq.get("badge_id") or ""
        t_disp = _get_title(tid)["display"] if (tid and _get_title(tid)) else "None"
        db.save_title_loadout(user.id, name, tid, bid)
        await _w(bot, user.id,
            f"✅ Saved {name} loadout.\nTitle: {t_disp}\nBadge: {bid or 'None'}")
    else:  # equip
        lo = db.get_title_loadout(user.id, name)
        if not lo:
            await _w(bot, user.id,
                f"No '{name}' loadout saved. Use: !loadout save {name}")
            return
        tid = lo.get("title_id", "")
        bid = lo.get("badge_id", "")
        if tid and _user_owns(user.id, tid):
            t = _get_title(tid)
            if t:
                db.set_equipped_item(user.id, "title", t["display"], tid)
        if bid:
            try:
                db.set_equipped_item(user.id, "badge", bid, bid)
            except Exception:
                pass
        await _w(bot, user.id, f"✅ Equipped {name} loadout.")


async def handle_loadouts(bot: BaseBot, user: User) -> None:
    """!loadouts — list saved loadouts."""
    loadouts = db.get_title_loadouts(user.id)
    if not loadouts:
        await _w(bot, user.id,
            "No loadouts saved.\nSave: !loadout save <fishing|mining|casino|daily|general>")
        return
    lines = ["💼 Your Loadouts"]
    for lo in loadouts:
        tid = lo.get("title_id", "")
        bid = lo.get("badge_id", "")
        t_disp = _get_title(tid)["display"] if (tid and _get_title(tid)) else "None"
        lines.append(f"{lo['name']}: {t_disp} / {bid or 'No badge'}")
    lines.append("Equip: !loadout equip <name>")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_besttitle(bot: BaseBot, user: User, args: list[str]) -> None:
    """!besttitle <category> — best title for a category."""
    cat = args[1].lower() if len(args) > 1 else ""
    if not cat:
        await _w(bot, user.id,
            "Usage: !besttitle <fishing|mining|casino|daily|shop|wealth|games>")
        return
    # Map 'daily' → find max daily_coins_bonus perk
    cat_key = {
        "fishing": "fishing_coin_pct", "mining": "mining_coin_pct",
        "casino": "casino_reward_pct", "daily": "daily_coins_bonus",
        "shop": "shop_discount_pct", "wealth": "game_reward_pct",
        "games": "game_reward_pct",
    }.get(cat, "game_reward_pct")

    cat_titles = [(tid, t) for tid, t in TITLE_CATALOG.items()
                  if t.get("category") == cat
                  or cat_key in t.get("perks", {})]
    cat_titles.sort(key=lambda x: x[1].get("perks", {}).get(cat_key, 0),
                    reverse=True)

    best_owned = next(((tid, t) for tid, t in cat_titles
                       if _user_owns(user.id, tid)), None)
    best_all   = cat_titles[0] if cat_titles else None

    lines = []
    if best_all:
        bid, bt = best_all
        perk_v  = bt.get("perks", {}).get(cat_key, 0)
        lines.append(f"Best {cat.title()} Title: {bt['display']}")
        lines.append(f"Perk: +{perk_v} {cat_key.replace('_',' ')}")
        if best_owned and best_owned[0] != bid:
            _, owt = best_owned
            lines.append(f"Best owned: {owt['display']}")
            # Progress to best
            req = bt.get("req_type", "")
            if req and req not in ("", "seasonal", "secret", "craft"):
                stats = _get_stats(user.id)
                cur   = stats.get(req, 0)
                val   = bt.get("req_val", 0)
                lines.append(f"Progress: {_fmt(cur)} / {_fmt(val)}")
            lines.append(f"Equip best: !equipbest {cat}")
        else:
            lines.append(f"Equip: !equiptitle {bid}")
    else:
        lines.append(f"No titles found for category: {cat}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_equipbest(bot: BaseBot, user: User, args: list[str]) -> None:
    """!equipbest <category> — equip best owned title for category."""
    cat = args[1].lower() if len(args) > 1 else ""
    cat_key = {
        "fishing": "fishing_coin_pct", "mining": "mining_coin_pct",
        "casino": "casino_reward_pct", "daily": "daily_coins_bonus",
        "shop": "shop_discount_pct", "games": "game_reward_pct",
    }.get(cat, "game_reward_pct")

    cat_titles = [(tid, t) for tid, t in TITLE_CATALOG.items()
                  if cat_key in t.get("perks", {})]
    cat_titles.sort(key=lambda x: x[1].get("perks", {}).get(cat_key, 0),
                    reverse=True)
    best = next(((tid, t) for tid, t in cat_titles
                 if _user_owns(user.id, tid)), None)
    if not best:
        await _w(bot, user.id,
            f"No owned title for {cat}.\nProgress: !titleprogress {cat}")
        return
    tid, t = best
    db.set_equipped_item(user.id, "title", t["display"], tid)
    await _w(bot, user.id,
        f"✅ Equipped best {cat} title: {t['display']}\nBoosts: !myboosts")


async def handle_titlelb(bot: BaseBot, user: User, args: list[str]) -> None:
    """!titlelb [category] — title leaderboard."""
    cat = args[1].lower() if len(args) > 1 else ""
    stat_map = {
        "fishing": "fish_caught", "mining": "ores_mined",
        "casino": "casino_lifetime_won", "wealth": "lifetime_chillcoins_earned",
    }
    stat = stat_map.get(cat, "")
    try:
        if stat:
            rows = db.get_title_stat_leaderboard(stat, limit=10)
            label = f"{cat.title()} Leaderboard"
        else:
            # General: most titles owned
            rows = db.get_title_count_leaderboard(limit=10)
            label = "Title Leaderboard"
        if not rows:
            await _w(bot, user.id, f"🏆 {label}\nNo data yet.")
            return
        lines = [f"🏆 {label}"]
        for i, r in enumerate(rows[:8], 1):
            uname = r.get("username", "?")
            val   = r.get("value") or r.get("count") or 0
            lines.append(f"{i}. @{uname} — {_fmt(int(val))}")
        await _w(bot, user.id, "\n".join(lines)[:249])
    except Exception as e:
        await _w(bot, user.id, f"Leaderboard unavailable. ({e})")


async def handle_temporarytitles(bot: BaseBot, user: User) -> None:
    """!temporarytitles / !seasontitles — list active seasonal titles."""
    rows = db.get_active_seasonal_titles()
    if not rows:
        await _w(bot, user.id, "No active seasonal titles right now.")
        return
    lines = ["⏳ Active Seasonal Titles"]
    for r in rows[:8]:
        t     = _get_title(r["title_id"])
        disp  = t["display"] if t else r["title_id"]
        uname = r.get("username", "?")
        exp   = r.get("expires_at", "")[:10]
        lines.append(f"{disp} — @{uname} expires {exp}")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# ADMIN HANDLERS
# ---------------------------------------------------------------------------

def _require_admin(bot, user) -> bool:
    if not _is_admin(user.username):
        return False
    return True


async def handle_givetitle_v2(bot: BaseBot, user: User, args: list[str]) -> None:
    """!givetitle @user title_id."""
    if not _require_admin(bot, user):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !givetitle @user title_id")
        return
    uname = args[1].lstrip("@").lower()
    tid   = args[2].lower()
    uid   = db.get_user_id_by_username(uname) or ""
    if not uid:
        await _w(bot, user.id, f"User @{uname} not found.")
        return
    t = _get_title(tid)
    if not t:
        await _w(bot, user.id, f"Title '{tid}' not found.")
        return
    db.add_owned_item(uid, uname, tid, "title")
    db.add_user_title(uid, uname, tid, "Admin")
    db.log_title_action("title_admin_given", user.id, user.username, tid,
                         uid, uname, f"by {user.username}")
    await _w(bot, user.id,
        f"✅ Gave title {t['display']} to @{uname}.\n"
        f"They can equip: !equiptitle {tid}")
    print(f"[TITLE ADMIN] owner=@{user.username} action=givetitle"
          f" target=@{uname} title={tid}")
    # Notify player if online
    try:
        target_id = uid
        await _w(bot, target_id,
            f"🏷️ You received title: {t['display']}.\nEquip: !equiptitle {tid}")
    except Exception:
        pass


async def handle_removetitle_v2(bot: BaseBot, user: User, args: list[str]) -> None:
    """!removetitle @user title_id."""
    if not _require_admin(bot, user):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !removetitle @user title_id")
        return
    uname = args[1].lstrip("@").lower()
    tid   = args[2].lower()
    uid   = db.get_user_id_by_username(uname) or ""
    if not uid:
        await _w(bot, user.id, f"User @{uname} not found.")
        return
    t = _get_title(tid)
    disp = t["display"] if t else tid
    # Unequip first if equipped
    if _user_equipped_title(uid) == tid:
        db.clear_equipped_title(uid)
    db.remove_owned_item(uid, tid, "title")
    db.remove_user_title(uid, tid)
    db.log_title_action("title_admin_removed", user.id, user.username, tid,
                         uid, uname)
    await _w(bot, user.id, f"✅ Removed title {disp} from @{uname}.")
    print(f"[TITLE ADMIN] owner=@{user.username} action=removetitle"
          f" target=@{uname} title={tid}")


async def handle_settitle_v2(bot: BaseBot, user: User, args: list[str]) -> None:
    """!settitle @user title_id — give + equip immediately."""
    if not _require_admin(bot, user):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !settitle @user title_id")
        return
    uname = args[1].lstrip("@").lower()
    tid   = args[2].lower()
    uid   = db.get_user_id_by_username(uname) or ""
    if not uid:
        await _w(bot, user.id, f"User @{uname} not found.")
        return
    t = _get_title(tid)
    if not t:
        await _w(bot, user.id, f"Title '{tid}' not found.")
        return
    db.add_owned_item(uid, uname, tid, "title")
    db.add_user_title(uid, uname, tid, "Admin")
    db.set_equipped_item(uid, "title", t["display"], tid)
    db.log_title_action("title_admin_set", user.id, user.username, tid,
                         uid, uname, f"by {user.username}")
    await _w(bot, user.id, f"✅ Set @{uname} title to {t['display']}.")
    try:
        await _w(bot, uid, f"🏷️ Your title was set to {t['display']}.")
    except Exception:
        pass


async def handle_cleartitle_v2(bot: BaseBot, user: User, args: list[str]) -> None:
    """!cleartitle @user — unequip title."""
    if not _require_admin(bot, user):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !cleartitle @user")
        return
    uname = args[1].lstrip("@").lower()
    uid   = db.get_user_id_by_username(uname) or ""
    if not uid:
        await _w(bot, user.id, f"User @{uname} not found.")
        return
    db.clear_equipped_title(uid)
    db.log_title_action("title_unequipped", user.id, user.username, "",
                         uid, uname, "admin_clear")
    await _w(bot, user.id, f"✅ Cleared title for @{uname}.")


async def handle_titleaudit(bot: BaseBot, user: User, args: list[str]) -> None:
    """!titleaudit @user — full audit."""
    if not _require_admin(bot, user):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !titleaudit @user")
        return
    uname = args[1].lstrip("@").lower()
    uid   = db.get_user_id_by_username(uname) or ""
    if not uid:
        await _w(bot, user.id, f"User @{uname} not found.")
        return
    _mirror_old_titles(uid, uname)
    v2_titles = db.get_user_titles(uid)
    eq        = db.get_equipped_ids(uid)
    tid       = eq.get("title_id") or "None"
    t         = _get_title(tid)
    t_disp    = t["display"] if t else tid
    perks     = get_combined_perks(uid)
    perk_str  = ", ".join(f"+{v}{' pct' if 'pct' in k else ''} {k}"
                           for k, v in perks.items() if v != 0)[:80]
    await _w(bot, user.id,
        f"🏷️ Title Audit: @{uname}\n"
        f"Equipped: {t_disp}\n"
        f"Titles owned: {len(v2_titles)}\n"
        f"Boosts: {perk_str or 'none'}")
    # Second message: recent logs
    logs = db.get_title_logs(uid, limit=5)
    if logs:
        lines = [f"📋 Recent title logs @{uname}"]
        for r in logs:
            lines.append(f"{r['action']} {r['title_id']} {r['created_at'][:10]}")
        await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_titlelogs(bot: BaseBot, user: User, args: list[str]) -> None:
    """!titlelogs @user|last — title action logs."""
    if not _require_admin(bot, user):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    arg  = args[1].lower() if len(args) > 1 else "last"
    uid  = None
    uname = ""
    if arg.startswith("@") or (arg not in ("last",) and arg):
        uname = arg.lstrip("@")
        uid   = db.get_user_id_by_username(uname) or None

    logs = db.get_title_logs(uid, limit=10)
    if not logs:
        await _w(bot, user.id, "No title logs found.")
        return
    header = f"📋 Title Logs {'@'+uname if uname else 'Recent'}"
    lines  = [header]
    for r in logs[:8]:
        lines.append(
            f"{r['action']} {r['title_id']} @{r['username']} {r['created_at'][:10]}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_titlestats_admin(bot: BaseBot, user: User, args: list[str]) -> None:
    """!titlestats @user — admin stats view."""
    if not _require_admin(bot, user):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    uname = args[1].lstrip("@").lower() if len(args) > 1 else ""
    if not uname:
        await _w(bot, user.id, "Usage: !titlestats @user")
        return
    uid = db.get_user_id_by_username(uname) or ""
    if not uid:
        await _w(bot, user.id, f"User @{uname} not found.")
        return
    # Reuse handle_mystats but pointed at target
    class _FakeUser:
        id = uid
        username = uname
    await handle_mystats(bot, _FakeUser(),
                          ["mystats", f"@{uname}"])


async def handle_addtitle(bot: BaseBot, user: User, args: list[str]) -> None:
    """!addtitle title_id "Display Name" tier source price."""
    if not _require_admin(bot, user):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    if len(args) < 6:
        await _w(bot, user.id,
            'Usage: !addtitle id "Display" tier source price')
        return
    tid, disp, tier, source, price_s = (args[1].lower(), args[2],
                                          args[3], args[4], args[5])
    try:
        price = int(price_s)
    except ValueError:
        await _w(bot, user.id, "Price must be a number.")
        return
    try:
        db.upsert_catalog_title(tid, disp, tier, source, price,
                                 buyable=(source == "Shop"),
                                 active=True)
        await _w(bot, user.id,
            f"✅ Added title '{disp}' ({tid}) — {tier} {source} {_fmt(price)}c")
    except Exception as e:
        await _w(bot, user.id, f"Error adding title: {e}")


async def handle_edittitle(bot: BaseBot, user: User, args: list[str]) -> None:
    """!edittitle title_id field value."""
    if not _require_admin(bot, user):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    if len(args) < 4:
        await _w(bot, user.id, "Usage: !edittitle id field value")
        return
    tid, field, val = args[1].lower(), args[2].lower(), args[3]
    allowed = {"display_name", "tier", "source", "price", "requirement_value"}
    if field not in allowed:
        await _w(bot, user.id,
            f"Editable fields: {', '.join(allowed)}")
        return
    try:
        db.edit_catalog_title(tid, field, val)
        await _w(bot, user.id, f"✅ Updated {tid} → {field}={val}")
    except Exception as e:
        await _w(bot, user.id, f"Error: {e}")


async def handle_settitlebuyable(bot: BaseBot, user: User, args: list[str]) -> None:
    """!settitlebuyable title_id on|off."""
    if not _require_admin(bot, user):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !settitlebuyable title_id on|off")
        return
    tid = args[1].lower()
    val = 1 if args[2].lower() == "on" else 0
    try:
        db.edit_catalog_title(tid, "buyable", val)
        await _w(bot, user.id,
            f"✅ {tid} buyable = {'on' if val else 'off'}")
    except Exception as e:
        await _w(bot, user.id, f"Error: {e}")


async def handle_settitleactive(bot: BaseBot, user: User, args: list[str]) -> None:
    """!settitleactive title_id on|off."""
    if not _require_admin(bot, user):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !settitleactive title_id on|off")
        return
    tid = args[1].lower()
    val = 1 if args[2].lower() == "on" else 0
    try:
        db.edit_catalog_title(tid, "active", val)
        await _w(bot, user.id,
            f"✅ {tid} active = {'on' if val else 'off'}")
    except Exception as e:
        await _w(bot, user.id, f"Error: {e}")


async def handle_awardseasonaltitle(bot: BaseBot, user: User,
                                     args: list[str]) -> None:
    """!awardseasonaltitle @user title_id 7d."""
    if not _require_admin(bot, user):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    if len(args) < 4:
        await _w(bot, user.id,
            "Usage: !awardseasonaltitle @user title_id 7d")
        return
    uname  = args[1].lstrip("@").lower()
    tid    = args[2].lower()
    dur_s  = args[3].lower()
    uid    = db.get_user_id_by_username(uname) or ""
    if not uid:
        await _w(bot, user.id, f"User @{uname} not found.")
        return
    t = _get_title(tid)
    if not t:
        await _w(bot, user.id, f"Title '{tid}' not found.")
        return
    # Parse duration
    try:
        days = int(dur_s.rstrip("d"))
    except ValueError:
        days = 7
    expires = (datetime.now(timezone.utc) +
               timedelta(days=days)).isoformat()
    db.add_user_title(uid, uname, tid, "Seasonal", expires_at=expires)
    db.log_title_action("title_admin_given", user.id, user.username, tid,
                         uid, uname, f"seasonal expires={expires[:10]}")
    await _w(bot, user.id,
        f"✅ Awarded seasonal title {t['display']} to @{uname}.\n"
        f"Expires: {expires[:10]}")
    try:
        await _w(bot, uid,
            f"🏷️ You received seasonal title: {t['display']}!\n"
            f"Expires: {expires[:10]}\nEquip: !equiptitle {tid}")
    except Exception:
        pass


async def handle_expiretitles(bot: BaseBot, user: User) -> None:
    """!expiretitles — expire all past-due seasonal titles."""
    if not _require_admin(bot, user):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    try:
        count = db.expire_seasonal_titles()
        await _w(bot, user.id,
            f"✅ Expired {count} seasonal title(s).")
    except Exception as e:
        await _w(bot, user.id, f"Error: {e}")


# ---------------------------------------------------------------------------
# Stat increment hooks (called from fishing.py / mining.py / etc.)
# ---------------------------------------------------------------------------

def on_fish_caught(user_id: str, username: str) -> list[str]:
    """Call from fishing module after a catch. Returns newly unlocked title IDs."""
    db.increment_title_stat(user_id, username, "fish_caught", 1)
    return _check_stat_titles(user_id, username, "fish_caught")


def on_ore_mined(user_id: str, username: str, qty: int = 1) -> list[str]:
    """Call from mining module after a mine. Returns newly unlocked title IDs."""
    db.increment_title_stat(user_id, username, "ores_mined", qty)
    return _check_stat_titles(user_id, username, "ores_mined")


def on_casino_hand(user_id: str, username: str,
                    won: bool, wagered: int, won_amount: int) -> list[str]:
    """Call from casino/BJ/Poker after each hand."""
    db.increment_title_stat(user_id, username, "casino_hands_played", 1)
    if won:
        db.increment_title_stat(user_id, username, "casino_hands_won", 1)
        db.increment_title_stat(user_id, username, "casino_lifetime_won", won_amount)
    db.increment_title_stat(user_id, username, "casino_lifetime_wagered", wagered)
    # biggest win
    try:
        stats = _get_stats(user_id)
        if won_amount > stats.get("casino_biggest_win", 0):
            db.set_title_stat(user_id, username, "casino_biggest_win", won_amount)
    except Exception:
        pass
    return _check_stat_titles(user_id, username, None)


def on_gold_tip(user_id: str, username: str, gold_amount: int) -> list[str]:
    """Call from gold tip handler."""
    db.increment_title_stat(user_id, username, "lifetime_gold_tipped", gold_amount)
    return _check_stat_titles(user_id, username, "lifetime_gold_tipped")


def on_balance_change(user_id: str, username: str,
                       earned: int = 0, spent: int = 0) -> list[str]:
    """Call after significant balance changes."""
    if earned > 0:
        db.increment_title_stat(user_id, username, "lifetime_chillcoins_earned", earned)
    if spent > 0:
        db.increment_title_stat(user_id, username, "lifetime_chillcoins_spent", spent)
    return _check_stat_titles(user_id, username, None)


def on_minigame(user_id: str, username: str, won: bool) -> list[str]:
    db.increment_title_stat(user_id, username, "minigames_played", 1)
    if won:
        db.increment_title_stat(user_id, username, "minigames_won", 1)
    return _check_stat_titles(user_id, username, "minigames_played")


def on_room_visit(user_id: str, username: str) -> list[str]:
    """Call on join."""
    db.increment_title_stat(user_id, username, "room_join_count", 1)
    # Visit-day tracking: compare last_visit_date
    try:
        stats = _get_stats(user_id)
        today = datetime.now(timezone.utc).date().isoformat()
        last  = stats.get("last_visit_date", "")
        if last != today:
            db.increment_title_stat(user_id, username, "room_visit_days", 1)
            db.set_title_stat(user_id, username, "last_visit_date", today)
    except Exception:
        pass
    return _check_stat_titles(user_id, username, "room_visit_days")


def on_trivia_win(user_id: str, username: str) -> list[str]:
    """Call from trivia module when a player wins a trivia game."""
    db.increment_title_stat(user_id, username, "trivia_wins", 1)
    db.increment_title_stat(user_id, username, "minigames_won", 1)
    db.increment_title_stat(user_id, username, "minigames_played", 1)
    return _check_stat_titles(user_id, username, "trivia_wins")


def on_scramble_win(user_id: str, username: str) -> list[str]:
    """Call from word scramble module when a player wins."""
    db.increment_title_stat(user_id, username, "scramble_wins", 1)
    db.increment_title_stat(user_id, username, "minigames_won", 1)
    db.increment_title_stat(user_id, username, "minigames_played", 1)
    return _check_stat_titles(user_id, username, "scramble_wins")


def on_riddle_win(user_id: str, username: str) -> list[str]:
    """Call from riddle module when a player wins."""
    db.increment_title_stat(user_id, username, "riddle_wins", 1)
    db.increment_title_stat(user_id, username, "minigames_won", 1)
    db.increment_title_stat(user_id, username, "minigames_played", 1)
    return _check_stat_titles(user_id, username, "riddle_wins")


def seed_title_catalog_startup() -> None:
    """Seed title_catalog DB table from TITLE_CATALOG dict. Called on startup."""
    try:
        seeded = 0
        for tid, t in TITLE_CATALOG.items():
            try:
                db.upsert_catalog_title(
                    tid,
                    t.get("display", ""),
                    t.get("tier", "Common"),
                    t.get("source", "Shop"),
                    t.get("price", 0),
                    buyable=t.get("buyable", False),
                    active=True,
                )
                seeded += 1
            except Exception:
                pass
        print(f"[TITLE V2] catalog_seeded count={seeded}")
        print("[TITLE V2] legacy_titles_supported=true")
        print("[TITLE V2] game_titles_removed_from_shop=true")
    except Exception as e:
        print(f"[TITLE V2] seed error: {e}")


async def handle_edittitleperk(bot: BaseBot, user: User,
                                args: list[str]) -> None:
    """!edittitleperk title_id perk_name value — admin: edit a title's perk."""
    if not _require_admin(bot, user):
        await _w(bot, user.id, "⚠️ Staff only.")
        return
    if len(args) < 4:
        await _w(bot, user.id,
            "Usage: !edittitleperk title_id perk_name value\n"
            "e.g.: !edittitleperk high_roller fishing_coin_pct 15")
        return
    tid      = args[1].lower()
    perk_key = args[2].lower()
    try:
        val = float(args[3])
    except ValueError:
        await _w(bot, user.id, "Value must be a number.")
        return

    t = _get_title(tid)
    if not t:
        await _w(bot, user.id, f"Title '{tid}' not found.")
        return

    # Enforce perk caps
    cap = PERK_CAPS.get(perk_key)
    if cap is not None:
        val = min(val, cap)

    # Mutate the in-memory catalog
    if "perks" not in TITLE_CATALOG[tid]:
        TITLE_CATALOG[tid]["perks"] = {}
    TITLE_CATALOG[tid]["perks"][perk_key] = val

    # Persist to DB catalog if upsert is available
    try:
        db.edit_catalog_title(tid, f"perk_{perk_key}", val)
    except Exception:
        pass

    db.log_title_action("title_perk_edited", user.id, user.username, tid,
                         details=f"{perk_key}={val}")
    await _w(bot, user.id,
        f"✅ Updated perk: {tid} → {perk_key}={val}"
        + (f" (capped at {cap})" if cap and float(args[3]) > cap else ""))
