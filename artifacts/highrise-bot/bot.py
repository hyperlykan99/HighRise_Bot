"""
bot.py — Multi-bot runner / workflow entry point.
Command: python3 bot.py

SINGLE BOT (current default):
  Set only:  BOT_TOKEN  +  ROOM_ID
  Runs one bot in BOT_MODE=all — identical behaviour to before.

SPLIT BOTS (add tokens as you go — all share BOT_TOKEN as the main bot):
  BOT_TOKEN               Main bot   BOT_ID=main        BOT_MODE=all
  BLACKJACK_BOT_TOKEN     Blackjack  BOT_ID=blackjack   BOT_MODE=blackjack
  POKER_BOT_TOKEN         Poker      BOT_ID=poker        BOT_MODE=poker
  HOST_BOT_TOKEN          Host       BOT_ID=host         BOT_MODE=host
  MINER_BOT_TOKEN         Miner      BOT_ID=miner        BOT_MODE=miner
  BANKER_BOT_TOKEN        Banker     BOT_ID=banker       BOT_MODE=banker
  SHOP_BOT_TOKEN          Shop       BOT_ID=shop         BOT_MODE=shopkeeper
  SECURITY_BOT_TOKEN      Security   BOT_ID=security     BOT_MODE=security
  DJ_BOT_TOKEN            DJ         BOT_ID=dj           BOT_MODE=dj
  EVENT_BOT_TOKEN         Event      BOT_ID=event        BOT_MODE=eventhost

  Optionally use MAIN_BOT_TOKEN instead of BOT_TOKEN for the main bot.
  Each token key also accepts _ID / _MODE / _USERNAME overrides,
    e.g.  BLACKJACK_BOT_ID=ace  BLACKJACK_BOT_USERNAME=AceSinatra

  Shared database: SHARED_DB_PATH  (default: highrise_hangout.db)

Each bot runs as an isolated subprocess — one crash never kills the others.
All bots share the same SQLite file for coins, games, and profiles.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).parent


# ---------------------------------------------------------------------------
# Bot specification
# ---------------------------------------------------------------------------

class _BotSpec(NamedTuple):
    token_env:    str   # name of the env var that provided the token
    label:        str
    token:        str
    bot_id:       str
    bot_mode:     str
    bot_username: str


# Ordered list of all supported split bots.
# (token_env, label, id_env, default_id, mode_env, default_mode, user_env)
_SPLIT_BOTS = [
    ("BLACKJACK_BOT_TOKEN", "Blackjack Bot", "BLACKJACK_BOT_ID", "blackjack", "BLACKJACK_BOT_MODE", "blackjack",  "BLACKJACK_BOT_USERNAME"),
    ("POKER_BOT_TOKEN",     "Poker Bot",     "POKER_BOT_ID",     "poker",     "POKER_BOT_MODE",     "poker",      "POKER_BOT_USERNAME"),
    ("HOST_BOT_TOKEN",      "Host Bot",      "HOST_BOT_ID",      "host",      "HOST_BOT_MODE",      "host",       "HOST_BOT_USERNAME"),
    ("MINER_BOT_TOKEN",     "Miner Bot",     "MINER_BOT_ID",     "miner",     "MINER_BOT_MODE",     "miner",      "MINER_BOT_USERNAME"),
    ("BANKER_BOT_TOKEN",    "Banker Bot",    "BANKER_BOT_ID",    "banker",    "BANKER_BOT_MODE",    "banker",     "BANKER_BOT_USERNAME"),
    ("SHOP_BOT_TOKEN",      "Shop Bot",      "SHOP_BOT_ID",      "shop",      "SHOP_BOT_MODE",      "shopkeeper", "SHOP_BOT_USERNAME"),
    ("SECURITY_BOT_TOKEN",  "Security Bot",  "SECURITY_BOT_ID",  "security",  "SECURITY_BOT_MODE",  "security",   "SECURITY_BOT_USERNAME"),
    ("DJ_BOT_TOKEN",        "DJ Bot",        "DJ_BOT_ID",        "dj",        "DJ_BOT_MODE",        "dj",         "DJ_BOT_USERNAME"),
    ("EVENT_BOT_TOKEN",     "Event Bot",     "EVENT_BOT_ID",     "eventhost", "EVENT_BOT_MODE",     "eventhost",  "EVENT_BOT_USERNAME"),
]


def _collect_bots() -> list[_BotSpec]:
    """
    Read all bot token env vars and return a spec for every configured bot.
    Logs each detected token by env-var name (never prints the value).
    """
    specs: list[_BotSpec] = []

    # ── Primary / main bot ────────────────────────────────────────────────
    # Accept MAIN_BOT_TOKEN as an explicit override; otherwise fall back to BOT_TOKEN.
    if os.environ.get("MAIN_BOT_TOKEN"):
        primary_env   = "MAIN_BOT_TOKEN"
        primary_token = os.environ["MAIN_BOT_TOKEN"]
    elif os.environ.get("BOT_TOKEN"):
        primary_env   = "BOT_TOKEN"
        primary_token = os.environ["BOT_TOKEN"]
    else:
        primary_env   = None
        primary_token = ""

    if primary_token:
        spec = _BotSpec(
            token_env    = primary_env,
            label        = "Main Bot",
            token        = primary_token,
            bot_id       = os.environ.get("MAIN_BOT_ID",       "main"),
            bot_mode     = os.environ.get("MAIN_BOT_MODE",     "all"),
            bot_username = os.environ.get("MAIN_BOT_USERNAME", ""),
        )
        print(f"[RUNNER] {primary_env} set -> starting {spec.label} mode {spec.bot_mode}")
        specs.append(spec)

    # ── Split bots ────────────────────────────────────────────────────────
    for token_env, label, id_env, default_id, mode_env, default_mode, user_env in _SPLIT_BOTS:
        token = os.environ.get(token_env, "")
        if not token:
            continue
        spec = _BotSpec(
            token_env    = token_env,
            label        = label,
            token        = token,
            bot_id       = os.environ.get(id_env,   default_id),
            bot_mode     = os.environ.get(mode_env, default_mode),
            bot_username = os.environ.get(user_env, ""),
        )
        print(f"[RUNNER] {token_env} set -> starting {label} mode {spec.bot_mode}")
        specs.append(spec)

    # ── Auto-switch main bot to host mode when split bots exist ─────────────
    # If MAIN_BOT_MODE was not explicitly set and any game-module bot is present,
    # the main bot demotes itself to host so it never duplicates game replies.
    _game_modes = {"blackjack", "poker", "miner", "banker",
                   "shopkeeper", "security", "dj", "eventhost"}
    has_game_split = any(s.bot_mode in _game_modes for s in specs[1:])
    has_host_split = any(s.bot_mode == "host" for s in specs[1:])

    if (has_game_split
            and specs                         # main bot present
            and specs[0].bot_mode == "all"    # currently all-mode
            and not has_host_split            # don't demote if dedicated host exists
            and not os.environ.get("MAIN_BOT_MODE")):   # not explicitly overridden
        old = specs[0]
        specs[0] = _BotSpec(
            token_env    = old.token_env,
            label        = "Host Bot",
            token        = old.token,
            bot_id       = "host",
            bot_mode     = "host",
            bot_username = old.bot_username,
        )
        print("[RUNNER] Split bots detected. Main bot set to host mode.")

    return specs


# ---------------------------------------------------------------------------
# Subprocess runner (multi-bot mode)
# ---------------------------------------------------------------------------

async def _run_bot_forever(spec: _BotSpec) -> None:
    """
    Keep one bot alive as a subprocess.
    Sets BOT_TOKEN / BOT_ID / BOT_MODE / BOT_USERNAME in the child env so that
    config.py (imported inside the subprocess) reads the correct identity.
    Uses exponential backoff (10s → 20s → 40s → 80s → cap 120s) when the bot
    exits within 60 s of starting, to avoid log spam from invalid tokens.
    """
    env = dict(os.environ)
    env["BOT_TOKEN"]    = spec.token
    env["BOT_ID"]       = spec.bot_id
    env["BOT_MODE"]     = spec.bot_mode
    env["BOT_USERNAME"] = spec.bot_username
    main_path = str(HERE / "main.py")

    consecutive_fast_exits = 0

    while True:
        proc: asyncio.subprocess.Process | None = None
        started_at = asyncio.get_event_loop().time()
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, main_path,
                env=env,
                cwd=str(HERE),
            )
            code = await proc.wait()
            uptime = asyncio.get_event_loop().time() - started_at

            if uptime < 60:
                # Fast exit — likely a bad token or immediate connection error
                consecutive_fast_exits += 1
                delay = min(10 * (2 ** (consecutive_fast_exits - 1)), 120)
                if consecutive_fast_exits == 1:
                    print(
                        f"[RUNNER] {spec.label} exited after {uptime:.0f}s (code {code}).\n"
                        f"         Check that {spec.token_env} is a valid Highrise API token.\n"
                        f"         Retrying in {delay}s..."
                    )
                else:
                    print(
                        f"[RUNNER] {spec.label} fast-exit #{consecutive_fast_exits} "
                        f"(code {code}). Retrying in {delay}s..."
                    )
            else:
                # Stable run — reset backoff
                consecutive_fast_exits = 0
                delay = 10
                print(
                    f"[RUNNER] {spec.label} (ID:{spec.bot_id}) "
                    f"exited (code {code}). Restarting in {delay}s..."
                )

        except asyncio.CancelledError:
            if proc and proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
            raise
        except Exception as exc:
            consecutive_fast_exits += 1
            delay = min(10 * (2 ** (consecutive_fast_exits - 1)), 120)
            print(f"[RUNNER] {spec.label} error: {exc}. Retrying in {delay}s...")

        await asyncio.sleep(delay)


async def _run_all(specs: list[_BotSpec]) -> None:
    tasks = [asyncio.create_task(_run_bot_forever(s)) for s in specs]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    specs = _collect_bots()

    if not specs:
        print(
            "\n[RUNNER] ERROR: No bot token found.\n"
            "  Set BOT_TOKEN in Replit Secrets for single-bot mode.\n"
            "  Add BLACKJACK_BOT_TOKEN, POKER_BOT_TOKEN, etc. for split bots.\n"
        )
        sys.exit(1)

    if len(specs) == 1:
        # ── Single-bot mode ──────────────────────────────────────────────────
        # The env vars are already set (BOT_TOKEN was read from os.environ).
        # We set BOT_ID/BOT_MODE/BOT_USERNAME in case they differ from defaults.
        spec = specs[0]
        os.environ["BOT_TOKEN"]    = spec.token
        os.environ["BOT_ID"]       = spec.bot_id
        os.environ["BOT_MODE"]     = spec.bot_mode
        os.environ["BOT_USERNAME"] = spec.bot_username
        print(f"[RUNNER] Single bot mode — ID:{spec.bot_id} Mode:{spec.bot_mode}")
        from main import run as _main_run
        _main_run()

    else:
        # ── Multi-bot mode ───────────────────────────────────────────────────
        print(
            f"[RUNNER] Multi-bot mode — {len(specs)} bots starting as subprocesses"
        )
        asyncio.run(_run_all(specs))


if __name__ == "__main__":
    run()
