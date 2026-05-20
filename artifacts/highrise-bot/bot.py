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
import collections
import os
import signal
import sys
import threading
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).parent

# ─── STARTUP DOCTOR ──────────────────────────────────────────────────────────
# Runs at the very top — before database, config, or SDK imports.
# Never prints token values — only presence booleans.

# Apply safe runtime defaults first so the prints reflect what will actually run.
# Any existing env var (set in Replit Secrets) wins over these defaults.
os.environ.setdefault("BOTS_ENABLED",                 "eventhost,dj")
os.environ.setdefault("BOT_DISABLE_ON_FAST_EXIT",     "false")
os.environ.setdefault("BOT_RECONNECT_MAX_FAST_EXITS", "999")

def _yn(key: str) -> str:
    """Return 'true'/'false' for whether env var is set (never leaks the value)."""
    return "true" if os.environ.get(key) else "false"

print(f"[STARTUP_DOCTOR] run_command=python3 bot.py (confirmed)",    flush=True)
print(f"[STARTUP_DOCTOR] cwd={Path.cwd()}",                         flush=True)
print(f"[STARTUP_DOCTOR] python={sys.executable}",                  flush=True)
print(f"[STARTUP_DOCTOR] ROOM_ID present={_yn('ROOM_ID')}",         flush=True)
print(f"[STARTUP_DOCTOR] BOT_TOKEN present={_yn('BOT_TOKEN')}",     flush=True)
print(f"[STARTUP_DOCTOR] MAIN_BOT_TOKEN present={_yn('MAIN_BOT_TOKEN')}", flush=True)
print(f"[STARTUP_DOCTOR] DJ_BOT_TOKEN present={_yn('DJ_BOT_TOKEN')}", flush=True)
print(f"[STARTUP_DOCTOR] BOTS_ENABLED={os.environ.get('BOTS_ENABLED', '(not set)')}", flush=True)

# ─── Web Dashboard flag ───────────────────────────────────────────────────────
# Set ENABLE_WEB_DASHBOARD=true to start a lightweight HTTP status server
# alongside the bot (useful when deploying as a web application).
# Default is false — the bot runs as a pure background WebSocket process.
_ENABLE_WEB_DASHBOARD = (
    os.environ.get("ENABLE_WEB_DASHBOARD", "false").strip().lower() == "true"
)


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
    extra_modes:  tuple[str, ...] = ()  # extra modes merged from duplicate-token bots


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
    ("FISHING_BOT_TOKEN",   "Fishing Bot",   "FISHING_BOT_ID",   "fisher",    "FISHING_BOT_MODE",   "fisher",     "FISHING_BOT_USERNAME"),
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
            if token_env == "FISHING_BOT_TOKEN":
                print("[RUNNER] FishingBot token missing — skipping MasterAngler startup.")
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
                   "shopkeeper", "security", "dj", "eventhost", "fisher"}
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

    # ── Deduplicate bots that share the same Highrise account token ──────────
    # Two subprocesses with the same token cause Highrise multilogin errors:
    # each new connection kicks the existing one, creating an infinite crash loop.
    # When duplicates are detected, only the first spec survives; the duplicate's
    # bot_mode is stored in extra_modes so the surviving subprocess writes
    # heartbeats for both modes and handles both command sets via BOT_EXTRA_MODES.
    seen_tokens: dict[str, int] = {}   # token value → index in deduped list
    deduped: list[_BotSpec] = []
    for spec in specs:
        if spec.token in seen_tokens:
            idx = seen_tokens[spec.token]
            old = deduped[idx]
            deduped[idx] = _BotSpec(
                token_env    = old.token_env,
                label        = old.label,
                token        = old.token,
                bot_id       = old.bot_id,
                bot_mode     = old.bot_mode,
                bot_username = old.bot_username,
                extra_modes  = old.extra_modes + (spec.bot_mode,),
            )
            print(
                f"[TOKEN_COLLISION] {spec.token_env} / {old.token_env} share the same token. "
                f"Merging mode '{spec.bot_mode}' into {old.label}. "
                f"Only ONE subprocess will run for this account — "
                f"duplicate skipped to prevent session-kick loop."
            )
        else:
            seen_tokens[spec.token] = len(deduped)
            deduped.append(spec)
    specs = deduped

    # Re-scan and report any remaining token collisions after merges
    if len(specs) < len([s for s in specs if s]):
        pass  # all clear after dedup
    for spec in specs:
        if spec.extra_modes:
            print(
                f"[TOKEN_COLLISION] {spec.label} ({spec.token_env}) token is shared with "
                f"merged mode(s) {spec.extra_modes}. ONE subprocess handles all modes. "
                f"Confirm these env vars intentionally share the same token."
            )

    # ── Staged rollout filter ─────────────────────────────────────────────────
    # Set BOTS_ENABLED=<comma-separated modes or ids> to start only a subset.
    #
    # Examples:
    #   BOTS_ENABLED=main,dj      — ChillTopiaMC (main/host) + DJ_DUDU only
    #   BOTS_ENABLED=host,dj      — same (use "host" if main auto-demoted)
    #   BOTS_ENABLED=main,dj,miner,banker  — staged 4-bot rollout
    #   (unset)                   — all configured bots start (default)
    #
    # "main" is a special alias that always matches the primary bot (index 0)
    # regardless of whether it was auto-demoted to host mode.
    _enabled_raw = os.environ.get("BOTS_ENABLED", "").strip()
    if _enabled_raw:
        _allowed = {m.strip().lower() for m in _enabled_raw.split(",") if m.strip()}
        _include_primary = "main" in _allowed
        # "main" alias only matches the genuine primary bot (BOT_TOKEN / MAIN_BOT_TOKEN).
        # Never substitute a split bot just because it happens to be at index 0.
        _has_real_primary = (
            bool(specs)
            and specs[0].token_env in ("BOT_TOKEN", "MAIN_BOT_TOKEN")
        )
        if _include_primary and not _has_real_primary:
            print(
                "[STARTUP_BLOCKED] main requested in BOTS_ENABLED but "
                "BOT_TOKEN / MAIN_BOT_TOKEN is not set — "
                "will not substitute another bot as 'main'. "
                "Use the bot mode name directly (e.g. BOTS_ENABLED=eventhost,dj)."
            )
            _include_primary = False   # prevent false index-0 inclusion
        filtered: list[_BotSpec] = []
        for i, s in enumerate(specs):
            keep = (i == 0 and _include_primary and _has_real_primary) or bool(
                {s.bot_id.lower(), s.bot_mode.lower()} & _allowed
            )
            if keep:
                filtered.append(s)
            else:
                print(f"[RUNNER] BOTS_ENABLED: skipping {s.label} (mode={s.bot_mode})")
        specs = filtered
        print(f"[RUNNER] BOTS_ENABLED={_enabled_raw!r} — {len(specs)} bot(s) will start")
        if not specs:
            print("[RUNNER] WARN: BOTS_ENABLED excluded all bots — check its value.")

    return specs


# ---------------------------------------------------------------------------
# Optional web dashboard (status HTTP server)
# ---------------------------------------------------------------------------

async def _run_web_dashboard() -> None:
    """
    Minimal bot-status HTTP server.  Only started when ENABLE_WEB_DASHBOARD=true.
    Serves GET /, /status, and /healthz — returns a JSON health payload.
    Useful as a deployment health-check endpoint so the bot can publish as
    a web application without serving a full dashboard.
    The full DJ/radio dashboard runs as the separate dj-status artifact.
    """
    try:
        from aiohttp import web as _web  # type: ignore[import]
    except ImportError:
        print(
            "[DASHBOARD] stage=dashboard_startup enabled=true "
            "error=aiohttp_missing — install aiohttp to use the web dashboard."
        )
        return

    port = int(os.environ.get("PORT", "8080"))

    async def _health(request: "_web.Request") -> "_web.Response":  # noqa: ARG001
        return _web.Response(
            text='{"status":"ok","service":"highrise-bot"}',
            content_type="application/json",
        )

    app = _web.Application()
    app.router.add_get("/",       _health)
    app.router.add_get("/status", _health)
    app.router.add_get("/healthz",_health)

    runner = _web.AppRunner(app)
    await runner.setup()
    site = _web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[DASHBOARD] stage=dashboard_startup enabled=true port={port}")

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        await runner.cleanup()
        raise


def _start_web_dashboard_thread() -> None:
    """
    Launch the web dashboard in a dedicated daemon thread.
    Used in single-bot mode, where main.py owns the main asyncio loop and
    we cannot add tasks to it from outside.
    """
    def _thread_main() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run_web_dashboard())
        finally:
            loop.close()

    t = threading.Thread(target=_thread_main, daemon=True, name="web-dashboard")
    t.start()


# ---------------------------------------------------------------------------
# Subprocess runner (multi-bot mode)
# ---------------------------------------------------------------------------

async def _health_loop(label: str) -> None:
    """Print a keepalive line every 60 s so log tails confirm the process is alive."""
    while True:
        await asyncio.sleep(60)
        print(f"[BOT_HEALTH] {label} alive")


async def _stream_and_buffer(
    stream: asyncio.StreamReader,
    ring: "collections.deque[str]",
) -> None:
    """
    Read subprocess stdout/stderr line-by-line, echo each line immediately
    to the parent process stdout, and store in a fixed-size ring buffer so
    the last N lines can be reprinted before a restart.
    """
    while True:
        line = await stream.readline()
        if not line:
            break
        decoded = line.decode("utf-8", errors="replace").rstrip("\n")
        print(decoded, flush=True)
        ring.append(decoded)


def _utc_ts() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).strftime("%H:%M:%S UTC")


def _write_rc_stats(mode: str, rc: int, reason: str, ts: str) -> None:
    """Persist reconnect stats to the shared DB so !botstatus can read them."""
    try:
        import json as _j
        import database as _dbs
        _dbs.set_room_setting(
            f"_bot_rc_{mode}",
            _j.dumps({"rc": rc, "reason": reason, "ts": ts}),
        )
    except Exception:
        pass


async def _run_bot_forever(spec: _BotSpec, startup_delay: float = 0.0) -> None:
    """
    Keep one bot alive as a subprocess — isolated per bot account.
    One bot crashing never affects the others; each runs in its own asyncio Task.

    Reconnect backoff: 10s → 20s → 30s (max).
    Counter resets to zero only after a stable run ≥ 300 s (5 min).
    """
    _BACKOFF = [10, 20, 30]   # seconds; last entry is the cap

    if startup_delay > 0:
        print(f"[BOT_START] waiting {startup_delay:.0f}s before {spec.label}")
        await asyncio.sleep(startup_delay)

    print(f"[BOT_START] starting {spec.label} mode={spec.bot_mode}")

    env = dict(os.environ)
    env["BOT_TOKEN"]       = spec.token
    env["BOT_ID"]          = spec.bot_id
    env["BOT_MODE"]        = spec.bot_mode
    env["BOT_USERNAME"]    = spec.bot_username
    env["BOT_EXTRA_MODES"] = ",".join(spec.extra_modes)
    main_path = str(HERE / "main.py")

    _MAX_FAST_EXITS      = int(os.environ.get("BOT_RECONNECT_MAX_FAST_EXITS", "999"))
    _DISABLE_ON_FAST_EXIT = (
        os.environ.get("BOT_DISABLE_ON_FAST_EXIT", "false").strip().lower() == "true"
    )
    _reconnect_count = 0
    _fast_exit_count = 0   # counts exits under 120 s; reset only on stable runs
    _last_reason     = "none"
    delay            = _BACKOFF[0]
    _log_ring: collections.deque[str] = collections.deque(maxlen=50)

    health_task = asyncio.create_task(_health_loop(spec.label))
    try:
        while True:
            proc: asyncio.subprocess.Process | None = None
            started_at = asyncio.get_event_loop().time()
            _ts = _utc_ts()
            print(f"[BOT_START] connected {spec.label} id={spec.bot_id} @ {_ts}")
            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, main_path,
                    env=env,
                    cwd=str(HERE),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                # Echo output in real-time AND buffer last 50 lines for pre-restart summary
                _reader = asyncio.create_task(
                    _stream_and_buffer(proc.stdout, _log_ring),  # type: ignore[arg-type]
                    name=f"log_reader_{spec.bot_id}",
                )
                code = await proc.wait()
                await _reader   # drain remaining buffered output
                uptime = asyncio.get_event_loop().time() - started_at
                _ts2 = _utc_ts()
                _last_reason = (
                    "clean exit"       if code == 0   else
                    "Python exception" if code == 1   else
                    "usage/OS error"   if code == 2   else
                    "SIGTERM"          if code == -15  else
                    "SIGKILL"          if code == -9   else
                    f"signal {-code}"  if code and code < 0 else
                    f"code {code}"
                )

                if uptime >= 300:
                    _reconnect_count = 0
                else:
                    _reconnect_count += 1

                delay = _BACKOFF[min(_reconnect_count - 1, len(_BACKOFF) - 1)] \
                        if _reconnect_count > 0 else _BACKOFF[0]

                print(
                    f"[RECONNECT] {spec.label} mode={spec.bot_mode}"
                    f" reason={_last_reason} uptime={uptime:.0f}s"
                    f" attempt={_reconnect_count} delay={delay}s @ {_ts2}"
                )

                _FAST_BACKOFF = [30, 60, 120]
                if uptime < 120:
                    _fast_exit_count += 1
                    delay = _FAST_BACKOFF[min(_fast_exit_count - 1, len(_FAST_BACKOFF) - 1)]
                    if _fast_exit_count >= _MAX_FAST_EXITS:
                        print(
                            f"[BOT_DISABLED] {spec.label} mode={spec.bot_mode}"
                            f" — {_fast_exit_count} fast exits (uptime<120s) in a row."
                            f" Check token / room ID / network."
                            + (" Stopping restarts." if _DISABLE_ON_FAST_EXIT
                               else f" Continuing with {delay}s delay"
                                    f" (set BOT_DISABLE_ON_FAST_EXIT=true to stop).")
                        )
                        _write_rc_stats(
                            spec.bot_mode, _reconnect_count,
                            f"DISABLED:{_last_reason}", _ts2
                        )
                        if _DISABLE_ON_FAST_EXIT:
                            return
                        # Reset counter so we keep cycling at max delay
                        _fast_exit_count = _MAX_FAST_EXITS - 1
                    else:
                        remaining = _MAX_FAST_EXITS - _fast_exit_count
                        print(
                            f"[RUNNER] {spec.label} fast-exit #{_fast_exit_count}"
                            f" uptime={uptime:.0f}s ({_last_reason})."
                            f" Retrying in {delay}s..."
                            + (f" ({remaining} until [BOT_DISABLED] warning)"
                               if _DISABLE_ON_FAST_EXIT else "")
                        )
                else:
                    _fast_exit_count = 0   # stable run — reset fast-exit counter
                    print(
                        f"[RUNNER] {spec.label} disconnected ({_last_reason})."
                        f" Reconnecting in {delay}s..."
                    )

                # Print last 50 subprocess log lines before restarting
                if _log_ring:
                    print(
                        f"[PRE_RESTART LOG] {spec.label} — last {len(_log_ring)} lines:"
                    )
                    for _ln in _log_ring:
                        print(f"  {_ln}")

                _write_rc_stats(spec.bot_mode, _reconnect_count, _last_reason, _ts2)

            except asyncio.CancelledError:
                if proc and proc.returncode is None:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        proc.kill()
                raise
            except Exception as exc:
                _reconnect_count += 1
                _last_reason = str(exc)[:80]
                delay = _BACKOFF[min(_reconnect_count - 1, len(_BACKOFF) - 1)]
                _ts2 = _utc_ts()
                print(
                    f"[RECONNECT] {spec.label} mode={spec.bot_mode}"
                    f" reason={_last_reason} attempt={_reconnect_count}"
                    f" delay={delay}s @ {_ts2}"
                )
                _write_rc_stats(spec.bot_mode, _reconnect_count, _last_reason, _ts2)

            print(f"[WATCHDOG] {spec.label} mode={spec.bot_mode}"
                  f" reconnect_attempt={_reconnect_count} delay={delay}s")
            await asyncio.sleep(delay)

    finally:
        health_task.cancel()
        try:
            await health_task
        except asyncio.CancelledError:
            pass


async def _run_all(specs: list[_BotSpec]) -> None:
    loop = asyncio.get_running_loop()

    # Stagger bot logins 12 s apart to prevent simultaneous session collisions.
    if len(specs) > 1:
        print(
            f"[BOT_START] {len(specs)} bots will start 12s apart "
            f"to prevent simultaneous session collisions."
        )
    tasks = [
        asyncio.create_task(
            _run_bot_forever(s, startup_delay=float(i * 12)),
            name=s.label,
        )
        for i, s in enumerate(specs)
    ]

    if _ENABLE_WEB_DASHBOARD:
        tasks.append(asyncio.create_task(_run_web_dashboard(), name="web-dashboard"))
    else:
        print("[DASHBOARD] stage=dashboard_startup enabled=false")

    def _shutdown(sig: int) -> None:
        alive = sum(1 for t in tasks if not t.done())
        print(f"[SHUTDOWN] {signal.Signals(sig).name} received — "
              f"cancelling {alive}/{len(tasks)} bot task(s)...")
        for t in tasks:
            if not t.done():
                t.cancel()

    try:
        loop.add_signal_handler(signal.SIGTERM, _shutdown, signal.SIGTERM)
        loop.add_signal_handler(signal.SIGINT,  _shutdown, signal.SIGINT)
    except (NotImplementedError, OSError):
        pass  # Windows / restricted env fallback

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        print(f"[SHUTDOWN] All {len(tasks)} bot(s) stopped cleanly.")


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

    # ── Validate critical env vars before touching DB ─────────────────────────
    # ROOM_ID is always required — without it the bot can never join a room.
    if not os.environ.get("ROOM_ID"):
        print(
            "[STARTUP_BLOCKED] reason=missing ROOM_ID\n"
            "  Add ROOM_ID to Replit Secrets — it must be the numeric Highrise room ID."
        )
        sys.exit(1)
    # Note: BOT_TOKEN is NOT required if split-bot tokens are present.
    # The `if not specs` guard above already caught the zero-token case.
    # BOT_TOKEN is seeded from specs[0].token below for config.py / database.py.

    # Initialise the DB exactly once, before any subprocess or asyncio loop
    # starts — this avoids all concurrent-writer races at startup.
    # config.py requires BOT_TOKEN at import time; seed it from the first
    # spec so database can import cleanly even in split-token mode.
    os.environ.setdefault("BOT_TOKEN", specs[0].token)
    try:
        import database as _db
        _db.init_db()
        print("[RUNNER] DB initialised.")
    except Exception:
        import traceback as _tb_db
        print(
            f"[STARTUP_BLOCKED] database init failed — full traceback:\n"
            f"{_tb_db.format_exc()}"
        )
        sys.exit(1)

    if len(specs) == 1:
        # ── Single-bot mode ──────────────────────────────────────────────────
        # The env vars are already set (BOT_TOKEN was read from os.environ).
        # We set BOT_ID/BOT_MODE/BOT_USERNAME in case they differ from defaults.
        spec = specs[0]
        os.environ["BOT_TOKEN"]       = spec.token
        os.environ["BOT_ID"]          = spec.bot_id
        os.environ["BOT_MODE"]        = spec.bot_mode
        os.environ["BOT_USERNAME"]    = spec.bot_username
        os.environ["BOT_EXTRA_MODES"] = ",".join(spec.extra_modes)
        print(f"[RUNNER] Single bot mode — ID:{spec.bot_id} Mode:{spec.bot_mode}")
        # Dashboard runs in a daemon thread so it never blocks bot startup.
        # main.py owns the asyncio event loop; we can't inject tasks into it.
        if _ENABLE_WEB_DASHBOARD:
            _start_web_dashboard_thread()
        else:
            print("[DASHBOARD] stage=dashboard_startup enabled=false")
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
