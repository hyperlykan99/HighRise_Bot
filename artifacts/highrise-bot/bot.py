"""
bot.py — Multi-bot runner / workflow entry point.
Command: python3 bot.py

SINGLE BOT (current default):
  Set only:  BOT_TOKEN  (or MAIN_BOT_TOKEN)  +  ROOM_ID
  Runs one bot in BOT_MODE=all — identical to before.

SPLIT BOTS (add tokens as you go):
  Set any subset of split-bot token secrets.  Each token present
  starts an independent bot process with the correct mode.

  MAIN_BOT_TOKEN   / MAIN_BOT_USERNAME   / MAIN_BOT_ID=main    / MAIN_BOT_MODE=all
  HOST_BOT_TOKEN   / HOST_BOT_USERNAME   / HOST_BOT_ID=host    / HOST_BOT_MODE=host
  BLACKJACK_BOT_TOKEN / BLACKJACK_BOT_USERNAME / BLACKJACK_BOT_ID=blackjack / BLACKJACK_BOT_MODE=blackjack
  POKER_BOT_TOKEN  / POKER_BOT_USERNAME  / POKER_BOT_ID=poker  / POKER_BOT_MODE=poker
  MINER_BOT_TOKEN  / MINER_BOT_USERNAME  / MINER_BOT_ID=miner  / MINER_BOT_MODE=miner
  BANKER_BOT_TOKEN / BANKER_BOT_USERNAME / BANKER_BOT_ID=banker / BANKER_BOT_MODE=banker
  SHOP_BOT_TOKEN   / SHOP_BOT_USERNAME   / SHOP_BOT_ID=shop    / SHOP_BOT_MODE=shopkeeper
  SECURITY_BOT_TOKEN / SECURITY_BOT_USERNAME / SECURITY_BOT_ID=security / SECURITY_BOT_MODE=security
  DJ_BOT_TOKEN     / DJ_BOT_USERNAME     / DJ_BOT_ID=dj        / DJ_BOT_MODE=dj
  EVENT_BOT_TOKEN  / EVENT_BOT_USERNAME  / EVENT_BOT_ID=event  / EVENT_BOT_MODE=eventhost

  Shared database: SHARED_DB_PATH  (default: highrise_hangout.db)

Each bot process is isolated — one crash does not kill the others.
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
    label:        str
    token:        str
    bot_id:       str
    bot_mode:     str
    bot_username: str


def _collect_bots() -> list[_BotSpec]:
    """
    Read all bot token env vars and return a spec for each configured bot.
    Order: main first, then split bots in the order they appear in SPLIT.
    """
    specs: list[_BotSpec] = []

    # Primary bot — original BOT_TOKEN or explicit MAIN_BOT_TOKEN
    primary_token = (
        os.environ.get("MAIN_BOT_TOKEN")
        or os.environ.get("BOT_TOKEN", "")
    )
    if primary_token:
        specs.append(_BotSpec(
            label        = "Main Bot",
            token        = primary_token,
            bot_id       = os.environ.get("MAIN_BOT_ID",       "main"),
            bot_mode     = os.environ.get("MAIN_BOT_MODE",     "all"),
            bot_username = os.environ.get("MAIN_BOT_USERNAME", ""),
        ))

    # Split bots — only included when their token secret is set
    SPLIT = [
        # (token_env,          label,          id_env,              default_id,   mode_env,             default_mode,  user_env)
        ("HOST_BOT_TOKEN",      "Host Bot",      "HOST_BOT_ID",      "host",       "HOST_BOT_MODE",      "host",        "HOST_BOT_USERNAME"),
        ("BLACKJACK_BOT_TOKEN", "Blackjack Bot", "BLACKJACK_BOT_ID", "blackjack",  "BLACKJACK_BOT_MODE", "blackjack",   "BLACKJACK_BOT_USERNAME"),
        ("POKER_BOT_TOKEN",     "Poker Bot",     "POKER_BOT_ID",     "poker",      "POKER_BOT_MODE",     "poker",       "POKER_BOT_USERNAME"),
        ("MINER_BOT_TOKEN",     "Miner Bot",     "MINER_BOT_ID",     "miner",      "MINER_BOT_MODE",     "miner",       "MINER_BOT_USERNAME"),
        ("BANKER_BOT_TOKEN",    "Banker Bot",    "BANKER_BOT_ID",    "banker",     "BANKER_BOT_MODE",    "banker",      "BANKER_BOT_USERNAME"),
        ("SHOP_BOT_TOKEN",      "Shop Bot",      "SHOP_BOT_ID",      "shop",       "SHOP_BOT_MODE",      "shopkeeper",  "SHOP_BOT_USERNAME"),
        ("SECURITY_BOT_TOKEN",  "Security Bot",  "SECURITY_BOT_ID",  "security",   "SECURITY_BOT_MODE",  "security",    "SECURITY_BOT_USERNAME"),
        ("DJ_BOT_TOKEN",        "DJ Bot",        "DJ_BOT_ID",        "dj",         "DJ_BOT_MODE",        "dj",          "DJ_BOT_USERNAME"),
        ("EVENT_BOT_TOKEN",     "Event Bot",     "EVENT_BOT_ID",     "event",      "EVENT_BOT_MODE",     "eventhost",   "EVENT_BOT_USERNAME"),
    ]

    primary_token_val = specs[0].token if specs else None

    for token_env, label, id_env, default_id, mode_env, default_mode, user_env in SPLIT:
        token = os.environ.get(token_env, "")
        if not token:
            continue
        if primary_token_val and token == primary_token_val:
            print(f"[RUNNER] WARNING: {label} token matches main bot — skipping duplicate.")
            continue
        specs.append(_BotSpec(
            label        = label,
            token        = token,
            bot_id       = os.environ.get(id_env,   default_id),
            bot_mode     = os.environ.get(mode_env, default_mode),
            bot_username = os.environ.get(user_env, ""),
        ))

    return specs


# ---------------------------------------------------------------------------
# Subprocess runner (multi-bot mode)
# ---------------------------------------------------------------------------

async def _run_bot_forever(spec: _BotSpec) -> None:
    """
    Run one bot as a subprocess.  Auto-restarts on exit/crash with a 10 s delay.
    Each subprocess gets its own BOT_TOKEN / BOT_ID / BOT_MODE env vars so that
    config.py reads the correct identity on import.
    """
    env = dict(os.environ)
    env["BOT_TOKEN"]    = spec.token
    env["BOT_ID"]       = spec.bot_id
    env["BOT_MODE"]     = spec.bot_mode
    env["BOT_USERNAME"] = spec.bot_username
    main_path = str(HERE / "main.py")

    print(f"[RUNNER] Starting {spec.label} | ID:{spec.bot_id} Mode:{spec.bot_mode}")

    while True:
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, main_path,
                env=env,
                cwd=str(HERE),
            )
            code = await proc.wait()
            print(f"[RUNNER] {spec.label} exited (code {code}). Restarting in 10s...")
        except asyncio.CancelledError:
            if proc and proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
            raise
        except Exception as exc:
            print(f"[RUNNER] {spec.label} error: {exc}. Restarting in 10s...")
        await asyncio.sleep(10)


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
            "  Single-bot: set BOT_TOKEN in Replit Secrets.\n"
            "  Multi-bot:  also set MAIN_BOT_TOKEN + any split-bot tokens.\n"
        )
        sys.exit(1)

    if len(specs) == 1:
        # ── Single-bot mode ──────────────────────────────────────────────────
        # Ensure the resolved token and identity are in env before main.py
        # imports config.py (which reads these at module level).
        spec = specs[0]
        os.environ["BOT_TOKEN"]    = spec.token
        os.environ["BOT_ID"]       = spec.bot_id
        os.environ["BOT_MODE"]     = spec.bot_mode
        os.environ["BOT_USERNAME"] = spec.bot_username
        print(f"[RUNNER] Starting single bot mode: {spec.bot_id}/{spec.bot_mode}")
        from main import run as _main_run
        _main_run()

    else:
        # ── Multi-bot mode ───────────────────────────────────────────────────
        print(f"[RUNNER] Starting {len(specs)} bots in multi-bot mode")
        for s in specs:
            print(f"  • {s.label} | ID:{s.bot_id} Mode:{s.bot_mode}")
        asyncio.run(_run_all(specs))


if __name__ == "__main__":
    run()
