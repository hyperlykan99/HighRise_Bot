# Highrise Hangout Room Bot

A modular Python bot for Highrise using the `highrise-bot-sdk`. Built to run 24/7 in a hangout room with a DJ request queue, token economy, and a clean module system for adding new features later.

## Project Structure

```
artifacts/highrise-bot/
├── bot.py              # Main entry point — connects to Highrise and routes all commands
├── config.py           # Central config: costs, rewards, admin IDs, thresholds
├── database.py         # All SQLite database logic (users, queue, history, daily claims)
├── requirements.txt    # Python dependencies
├── bot_data.db         # SQLite database (auto-created on first run)
└── modules/
    ├── __init__.py
    ├── dj.py           # DJ request system (active)
    ├── economy.py      # Token balance and daily rewards (active)
    ├── trivia.py       # Placeholder — ready to build out
    ├── blackjack.py    # Placeholder — ready to build out
    ├── pets.py         # Placeholder — ready to build out
    └── minigames.py    # Placeholder — ready to build out
```

## Environment Secrets

| Secret | Purpose |
|---|---|
| `BOT_TOKEN` | Highrise API token for the bot account |
| `ROOM_ID` | Highrise room ID the bot connects to |

## Running the Bot

The bot runs via the **Highrise Bot** workflow:
```
cd artifacts/highrise-bot && python3 bot.py
```

## Commands

### User Commands
| Command | Description | Cost |
|---|---|---|
| `/help` | Show all commands | Free |
| `/dj` | Explain the DJ system | Free |
| `/request <song>` | Add song to queue | 20 tokens |
| `/queue` | Show next 5 songs | Free |
| `/now` | Show current song | Free |
| `/skipvote` | Vote to skip (3 votes = auto skip) | Free |
| `/balance` | Show token balance | Free |
| `/daily` | Claim 10 free tokens (once/day) | Free |

### Admin Commands (add your user ID to `ADMIN_IDS` in `config.py`)
| Command | Description |
|---|---|
| `/skip` | Force skip current song |
| `/remove <#>` | Remove song by queue position |
| `/addtokens <user> <amount>` | Give tokens to a user |
| `/refund <user> <amount>` | Refund tokens to a user |

## Configuration (`config.py`)

```python
SONG_REQUEST_COST  = 20   # tokens per song request
DAILY_REWARD       = 10   # tokens from /daily
QUEUE_DISPLAY_SIZE = 5    # songs shown by /queue
SKIP_VOTE_THRESHOLD = 3   # votes needed to auto-skip
ADMIN_IDS          = []   # list your Highrise user IDs here
```

## Database (SQLite)

Tables:
- `users` — user IDs, usernames, token balances
- `song_queue` — current queue (in order)
- `request_history` — all past requests ever made
- `daily_claims` — tracks daily claim dates per user

## Adding a New Module

1. Create `modules/yourmodule.py` with a `handle_yourmodule_command(bot, user, args)` function
2. Import it at the top of `bot.py`
3. Add the command names to a new set (e.g. `YOURMODULE_COMMANDS = {"yourcommand"}`)
4. Add a routing branch in `on_chat()` in `bot.py`
5. Done — no other files need to change

Planned modules: trivia, blackjack, pets, mini games

## Dependencies

- Python 3.11
- `highrise-bot-sdk` 25.1.0
- `aiohttp`
- `sqlite3` (built-in)
