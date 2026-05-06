"""
economy.py
----------
Shared coin-economy module for ALL bot modes.

Any future bot (GameBot, DJBot, BlackjackBot, HostBot) should import
the functions it needs from here so player balances and daily rewards
are consistent across bots.

User-facing commands handled here:
  /balance /bal /coins /coin /money — whisper coin balance (self or target)
  /daily        — claim free coins once every 24 hours
  /leaderboard  — whisper the top 10 richest players

Admin coin commands (/addcoins, /removecoins) live in admin.py so that
economy.py stays purely about the player-facing experience.
"""

from highrise import BaseBot, User
import database as db
import config
import modules.leveling as leveling
from modules.shop         import get_player_benefits
from modules.achievements import check_achievements
from modules.quests       import track_quest
from modules.reputation   import get_rank
from modules.permissions  import can_moderate, is_admin


# ---------------------------------------------------------------------------
# Coin formatter
# ---------------------------------------------------------------------------

def fmt_coins(amount: int) -> str:
    """
    Return a human-readable coin string.
    Under 1 000: just '999c'.
    1 000+:      '1,250,000c / 1.25M' (both comma form and short form).
    """
    s = f"{amount:,}c"
    if amount >= 1_000_000_000_000:
        k = round(amount / 1_000_000_000_000, 2)
        return f"{s} / {k:g}T"
    if amount >= 1_000_000_000:
        k = round(amount / 1_000_000_000, 2)
        return f"{s} / {k:g}B"
    if amount >= 1_000_000:
        k = round(amount / 1_000_000, 2)
        return f"{s} / {k:g}M"
    if amount >= 1_000:
        k = round(amount / 1_000, 2)
        return f"{s} / {k:g}K"
    return s


# ---------------------------------------------------------------------------
# Balance command
# ---------------------------------------------------------------------------

async def handle_balance(bot: BaseBot, user: User, args: list | None = None):
    """
    Whisper the player's current coin balance privately.
    /bal             → self balance
    /bal <username>  → other player balance (privacy-aware)
    """
    try:
        db.ensure_user(user.id, user.username)
    except Exception as _eu:
        print(f"[ECONOMY] ensure_user failed for {user.username}: {_eu}")
        # Continue — user row may already exist; get_balance will handle it

    raw_target = args[1].lstrip("@").strip() if args and len(args) > 1 else None

    if not raw_target:
        balance = db.get_balance(user.id)
        await bot.highrise.send_whisper(user.id, f"💰 Balance: {fmt_coins(balance)}")
        return

    # Looking up another player
    target = db.get_user_by_username(raw_target)
    if not target:
        await bot.highrise.send_whisper(
            user.id, "User not found. They may need to chat first."
        )
        return

    t_id   = target["user_id"]
    t_name = target["username"]

    # Privacy check
    is_staff = can_moderate(user.username) or is_admin(user.username)
    is_self  = (t_id == user.id)
    if not is_self and not is_staff:
        privacy = db.get_profile_privacy(t_name.lower())
        if not bool(privacy.get("show_money", 1)):
            await bot.highrise.send_whisper(user.id, f"@{t_name}'s balance is private.")
            return

    balance = db.get_balance(t_id)
    await bot.highrise.send_whisper(user.id, f"💰 @{t_name}: {fmt_coins(balance)}")


# ---------------------------------------------------------------------------
# Daily command
# ---------------------------------------------------------------------------

async def handle_daily(bot: BaseBot, user: User):
    """
    Give the player their daily coin reward.
    Claimable once per calendar day — resets at midnight UTC.
    """
    db.ensure_user(user.id, user.username)

    if not db.can_claim_daily(user.id):
        await bot.highrise.send_whisper(
            user.id,
            "⏰ Already claimed today! Come back tomorrow for more coins."
        )
        return

    benefits     = get_player_benefits(user.id)
    bonus_coins  = benefits["daily_coins_bonus"]
    bonus_xp     = benefits["daily_xp_bonus"]
    base_daily   = db.get_economy_settings()["daily_coins"]
    actual_coins = base_daily + bonus_coins
    actual_xp    = config.XP_DAILY + bonus_xp

    actual_coins = db.adjust_balance_capped(user.id, actual_coins)
    db.record_daily_claim(user.id)
    track_quest(user.id, "daily_claim")
    track_quest(user.id, "earn_coins", actual_coins)
    await leveling.award_xp(bot, user, actual_xp, actual_coins, is_game_win=False)
    await check_achievements(bot, user, "daily")
    new_balance = db.get_balance(user.id)

    msg = f"🎁 Daily reward! +{fmt_coins(actual_coins)}"
    if bonus_coins:
        msg += f" (incl. +{bonus_coins:,}c bonus)"
    msg += f"\nBalance: {fmt_coins(new_balance)}"
    await bot.highrise.send_whisper(user.id, msg[:249])


# ---------------------------------------------------------------------------
# Leaderboard command
# ---------------------------------------------------------------------------

async def handle_leaderboard(bot: BaseBot, user: User):
    """Whisper the top players sorted by coin balance."""
    top = db.get_leaderboard(config.LEADERBOARD_SIZE)

    if not top:
        await bot.highrise.send_whisper(user.id, "No players on the leaderboard yet!")
        return

    lines = [f"-- Top {len(top)} Players --"]
    for entry in top:
        lines.append(f"#{entry['rank']} {entry['username']} {fmt_coins(entry['balance'])}")

    await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# Legacy profile / level commands (still used by main.py routing)
# ---------------------------------------------------------------------------

async def handle_profile(bot: BaseBot, user: User):
    """Whisper the player's full profile: level, XP, coins, games won, coins earned."""
    db.ensure_user(user.id, user.username)
    p = db.get_profile(user.id)
    if not p:
        await bot.highrise.send_whisper(user.id, "Profile not found. Try again!")
        return
    level     = p["level"]
    xp        = p["xp"]
    xp_needed = db.xp_for_level(level + 1) - xp
    badge     = p.get("equipped_badge") or "none"
    title_    = p.get("equipped_title") or "none"
    rep_row   = db.get_reputation(user.id)
    rep       = rep_row["rep_received"] if rep_row else 0
    rank      = get_rank(rep)
    await bot.highrise.send_whisper(user.id, (
        f"-- {p['username']} --\n"
        f"💰 {fmt_coins(p['balance'])} | Lv {level}\n"
        f"✨ {xp} XP (need {xp_needed} for Lv {level + 1})\n"
        f"🏆 {p['total_games_won']} wins | 🪙 {p['total_coins_earned']:,}c earned\n"
        f"⭐ Rep: {rep} ({rank})\n"
        f"🎨 {badge} | 🏷️ {title_}"
    )[:249])


async def handle_level(bot: BaseBot, user: User):
    """Whisper the player's current level and XP progress toward the next level."""
    db.ensure_user(user.id, user.username)
    p = db.get_profile(user.id)
    if not p:
        await bot.highrise.send_whisper(user.id, "Profile not found. Try again!")
        return
    level    = p["level"]
    xp       = p["xp"]
    xp_this  = db.xp_for_level(level)
    xp_next  = db.xp_for_level(level + 1)
    progress = xp - xp_this
    needed   = xp_next - xp
    await bot.highrise.send_whisper(user.id,
        f"⭐ Level {level}  |  {xp} XP total\n"
        f"Progress: {progress} / {xp_next - xp_this} XP into this level\n"
        f"Need {needed} more XP to reach Level {level + 1}"
    )


async def handle_xp_leaderboard(bot: BaseBot, user: User):
    """Whisper the top players sorted by XP."""
    top = db.get_xp_leaderboard(config.LEADERBOARD_SIZE)
    if not top:
        await bot.highrise.send_whisper(user.id, "No players on the XP leaderboard yet!")
        return
    lines = [f"-- Top {len(top)} by XP --"]
    for entry in top:
        lines.append(
            f"  #{entry['rank']}  {entry['username']}  —  Lv {entry['level']}  ({entry['xp']} XP)"
        )
    await bot.highrise.send_whisper(user.id, "\n".join(lines))


# ---------------------------------------------------------------------------
# Economy DB check
# ---------------------------------------------------------------------------

async def handle_economy_dbcheck(bot: BaseBot, user: User):
    """
    /economydbcheck — owner/admin only.
    Diagnoses economy DB: path, users table, balance column, read test.
    """
    from modules.permissions import is_owner as _io, is_admin as _ia
    if not _io(user.username) and not _ia(user.username):
        await bot.highrise.send_whisper(user.id, "Owner/admin only.")
        return
    issues: list[str] = []
    ok_parts: list[str] = []
    try:
        conn = db.get_connection()
        ok_parts.append(f"path={config.DB_PATH}")
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "users" not in tables:
            issues.append("missing table users")
        else:
            ok_parts.append("users OK")
            cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
            if "balance" not in cols:
                issues.append("missing users.balance")
            else:
                ok_parts.append("balance OK")
            try:
                conn.execute(
                    "SELECT balance FROM users WHERE user_id = ?", (user.id,)
                ).fetchone()
                ok_parts.append("read OK")
            except Exception as _re:
                issues.append(f"read fail: {_re}")
        conn.close()
    except Exception as _dbe:
        issues.append(f"connect fail: {_dbe}")
    if issues:
        await bot.highrise.send_whisper(
            user.id, f"Economy DB fail: {', '.join(issues)}"[:249])
    else:
        await bot.highrise.send_whisper(
            user.id, f"Economy DB: OK | {' | '.join(ok_parts)}"[:249])


# ---------------------------------------------------------------------------
# Economy DB repair
# ---------------------------------------------------------------------------

async def handle_economy_repair(bot: BaseBot, user: User):
    """
    /economyrepair — owner/admin only.
    Creates missing economy tables and adds missing columns.
    Does NOT wipe data or reset balances.
    """
    from modules.permissions import is_owner as _io, is_admin as _ia
    if not _io(user.username) and not _ia(user.username):
        await bot.highrise.send_whisper(user.id, "Owner/admin only.")
        return
    try:
        conn = db.get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id              TEXT PRIMARY KEY,
                username             TEXT NOT NULL,
                balance              INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_claims (
                user_id    TEXT PRIMARY KEY,
                last_claim TEXT NOT NULL
            )
        """)
        _add_cols = [
            ("xp",                "INTEGER NOT NULL DEFAULT 0"),
            ("level",             "INTEGER NOT NULL DEFAULT 1"),
            ("total_games_won",   "INTEGER NOT NULL DEFAULT 0"),
            ("total_coins_earned","INTEGER NOT NULL DEFAULT 0"),
            ("equipped_badge",    "TEXT"),
            ("equipped_title",    "TEXT"),
            ("equipped_badge_id", "TEXT"),
            ("equipped_title_id", "TEXT"),
            ("first_seen",        "TEXT"),
            ("tip_coins_earned",  "INTEGER NOT NULL DEFAULT 0"),
        ]
        existing = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        added: list[str] = []
        for col_name, col_def in _add_cols:
            if col_name not in existing:
                try:
                    conn.execute(
                        f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"
                    )
                    added.append(col_name)
                except Exception:
                    pass
        conn.commit()
        conn.close()
        if added:
            await bot.highrise.send_whisper(
                user.id,
                f"Economy DB repaired. Added: {', '.join(added)}"[:249])
        else:
            await bot.highrise.send_whisper(user.id, "Economy DB OK — no repairs needed.")
    except Exception as _e:
        await bot.highrise.send_whisper(
            user.id, f"Repair failed: {str(_e)[:180]}"[:249])
