"""
modules/party_tip.py
--------------------
ChillTopiaMC Party Tip Wallet system.

Owner-only management:
  !party [on|off]            — toggle party mode
  !ptwallet [amount]         — set party wallet amount
  !ptadd [user] [minutes]    — add temporary party tipper
  !ptremove [user]           — remove party tipper
  !ptclear                   — remove all party tippers
  !ptlimit [type] [amount]   — set tip limits

Owner or active Party Tipper:
  !tip [user] [amount]       — send party tip to one player
  !tip all [amount]          — tip all players in room

Public:
  !ptlist                    — show party tippers and mode
  !ptlimits                  — show limits

DB tables: party_tippers, party_tip_log  (room_settings stores wallet amount + limits).
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import database as db
from highrise import BaseBot, User
from modules.permissions import is_owner

# ---------------------------------------------------------------------------
# Async whisper helper
# ---------------------------------------------------------------------------

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Room-setting helpers (wallet lives in room_settings, limits too)
# ---------------------------------------------------------------------------

def _party_mode_on() -> bool:
    return db.get_room_setting("party_mode", "0") == "1"


def _get_wallet() -> int:
    try:
        return int(db.get_room_setting("party_tip_wallet", "0"))
    except (ValueError, TypeError):
        return 0


def _set_wallet(amount: int) -> None:
    db.set_room_setting("party_tip_wallet", str(max(0, amount)))


def _limit(key: str, default: int) -> int:
    try:
        return int(db.get_room_setting(f"pt_limit_{key}", str(default)))
    except (ValueError, TypeError):
        return default


def _set_limit(key: str, value: int) -> None:
    db.set_room_setting(f"pt_limit_{key}", str(value))


# Default limits
_DEF_SINGLE = 10
_DEF_ALL    = 5
_DEF_DAILY  = 100
_DEF_MAX    = 500
_DEF_DUR    = 120   # minutes

# Known bot usernames (lowercase) — excluded from room-wide tips
_BOT_NAMES: frozenset[str] = frozenset({
    "chilltopiamc", "bankingbot", "bankbot", "bankerbot",
    "acesinastra", "chipsoprano", "dj_dudu", "keanushield",
    "masterangler", "greatestprospector",
    "emceebot", "blackjackbot", "pokerbot", "securitybot",
    "eventbot", "djbot",
})


def _is_bot(username: str) -> bool:
    name = username.lower().strip()
    if name in _BOT_NAMES:
        return True
    # Exclude anything ending in "bot"
    if name.endswith("bot"):
        return True
    return False


# ---------------------------------------------------------------------------
# Party tippers DB helpers
# ---------------------------------------------------------------------------

def _utcnow_str() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_active_tipper(username: str) -> bool:
    """Return True if *username* is in party_tippers with a non-expired entry."""
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT expires_at FROM party_tippers WHERE lower(username)=lower(?)",
            (username,),
        ).fetchone()
        if not row:
            return False
        expires = row[0]
        if not expires:
            return True  # permanent entry
        try:
            exp_dt = datetime.fromisoformat(expires)
            return datetime.now(timezone.utc) < exp_dt
        except (ValueError, TypeError):
            return False
    finally:
        conn.close()


def _get_daily_used(username: str) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT daily_used, last_reset FROM party_tippers WHERE lower(username)=lower(?)",
            (username,),
        ).fetchone()
        if not row:
            return 0
        daily_used, last_reset = row
        if not last_reset or last_reset[:10] != today:
            return 0
        return daily_used or 0
    finally:
        conn.close()


def _add_daily_used(username: str, amount: int) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT id, daily_used, last_reset FROM party_tippers WHERE lower(username)=lower(?)",
            (username,),
        ).fetchone()
        if not row:
            conn.close()
            return
        row_id, daily_used, last_reset = row
        if not last_reset or last_reset[:10] != today:
            daily_used = 0
        conn.execute(
            "UPDATE party_tippers SET daily_used=?, last_reset=? WHERE id=?",
            ((daily_used or 0) + amount, today, row_id),
        )
        conn.commit()
    finally:
        conn.close()


def _add_tipper(user_id: str, username: str, minutes: int, added_by: str) -> None:
    expires_at = (
        (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
        if minutes > 0 else ""
    )
    conn = db.get_connection()
    try:
        conn.execute(
            """INSERT INTO party_tippers
               (user_id, username, added_by, added_at, expires_at, daily_used, last_reset)
               VALUES (?, ?, ?, ?, ?, 0, '')
               ON CONFLICT(username) DO UPDATE SET
                 user_id=excluded.user_id,
                 added_by=excluded.added_by,
                 added_at=excluded.added_at,
                 expires_at=excluded.expires_at,
                 daily_used=0,
                 last_reset=''""",
            (user_id, username.lower(), added_by.lower(), _utcnow_str(), expires_at),
        )
        conn.commit()
    finally:
        conn.close()


def _remove_tipper(username: str) -> bool:
    conn = db.get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM party_tippers WHERE lower(username)=lower(?)", (username,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _clear_tippers() -> int:
    conn = db.get_connection()
    try:
        cur = conn.execute("DELETE FROM party_tippers")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _list_tippers() -> list[dict]:
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT username, expires_at, daily_used, last_reset "
            "FROM party_tippers ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _log_party_tip(
    tipper_id: str, tipper_name: str,
    receiver_id: str, receiver_name: str,
    amount: int, wallet_before: int, wallet_after: int,
    result: str, note: str = "",
) -> None:
    conn = db.get_connection()
    try:
        conn.execute(
            """INSERT INTO party_tip_log
               (tipper_id, tipper_name, receiver_id, receiver_name,
                amount, wallet_before, wallet_after, party_mode, result, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'ON', ?, ?, datetime('now'))""",
            (tipper_id, tipper_name.lower(), receiver_id, receiver_name.lower(),
             amount, wallet_before, wallet_after, result, note),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Party tip enabled/disabled kill switch
# ---------------------------------------------------------------------------

def _party_tip_enabled() -> bool:
    """Returns False when disabled via !ptdisable OR when stability mode is ON."""
    if db.get_room_setting("stability_mode", "0") == "1":
        return False
    return db.get_room_setting("party_tip_enabled", "1") == "1"


# ---------------------------------------------------------------------------
# !ptenable / !ptdisable — instant kill switch (owner only)
# ---------------------------------------------------------------------------

async def handle_ptenable(bot: BaseBot, user: User) -> None:
    """!ptenable — re-enable party tipping (owner only)."""
    if not is_owner(user.username):
        await _w(bot, user.id, "🔒 Owner only.")
        return
    db.set_room_setting("party_tip_enabled", "1")
    await _w(bot, user.id,
             "✅ Party Tip System: ENABLED\n!tip commands are now active.")


async def handle_ptdisable(bot: BaseBot, user: User) -> None:
    """!ptdisable — instantly disable all party tipping (owner only)."""
    if not is_owner(user.username):
        await _w(bot, user.id, "🔒 Owner only.")
        return
    db.set_room_setting("party_tip_enabled", "0")
    await _w(bot, user.id,
             "🛑 Party Tip System: DISABLED\n"
             "!tip commands are paused. Use !ptenable to restore.")


# ---------------------------------------------------------------------------
# !pton / !ptoff / !ptstatus — simple party mode controls (manager+)
# ---------------------------------------------------------------------------

async def handle_pton(bot: BaseBot, user: User) -> None:
    """!pton — turn Party Mode ON (manager+)."""
    from modules.permissions import is_manager
    if not (is_owner(user.username) or is_manager(user.username)):
        await _w(bot, user.id, "🔒 Manager+ only.")
        return
    db.set_room_setting("party_mode", "1")
    wallet = _get_wallet()
    await _w(bot, user.id,
             f"🎉 Party Mode: ON\nParty tipping enabled.\nWallet: {wallet}g")


async def handle_ptoff(bot: BaseBot, user: User) -> None:
    """!ptoff — turn Party Mode OFF (manager+)."""
    from modules.permissions import is_manager
    if not (is_owner(user.username) or is_manager(user.username)):
        await _w(bot, user.id, "🔒 Manager+ only.")
        return
    db.set_room_setting("party_mode", "0")
    await _w(bot, user.id, "🎉 Party Mode: OFF\nParty tipping disabled.")


async def handle_ptstatus(bot: BaseBot, user: User) -> None:
    """!ptstatus — show party mode and wallet status (public)."""
    enabled = _party_tip_enabled()
    mode    = "ON" if _party_mode_on() else "OFF"
    wallet  = _get_wallet()
    count   = len(_list_tippers())
    system  = "ENABLED" if enabled else "DISABLED"
    await _w(bot, user.id,
             f"🎉 Party Status\nSystem: {system}\nMode: {mode}\n"
             f"Party Wallet: {wallet}g\nParty Tippers: {count}")


# ---------------------------------------------------------------------------
# !party [on|off|status]  — toggle party mode  (manager+ for on/off)
# ---------------------------------------------------------------------------

async def handle_party(bot: BaseBot, user: User, args: list[str]) -> None:
    from modules.permissions import is_manager
    if len(args) < 2:
        await handle_ptstatus(bot, user)
        return
    val = args[1].lower()
    if val == "on":
        await handle_pton(bot, user)
    elif val == "off":
        await handle_ptoff(bot, user)
    elif val in ("status", "info"):
        await handle_ptstatus(bot, user)
    else:
        await _w(bot, user.id, "Usage: !party on|off|status")


# ---------------------------------------------------------------------------
# !ptwallet [amount]  — set / view party wallet  (owner only)
# ---------------------------------------------------------------------------

async def handle_ptwallet(bot: BaseBot, user: User, args: list[str]) -> None:
    if not is_owner(user.username):
        await _w(bot, user.id, "🔒 Owner only.")
        return
    if len(args) < 2:
        wallet = _get_wallet()
        max_w  = _limit("max", _DEF_MAX)
        mode   = "ON" if _party_mode_on() else "OFF"
        await _w(bot, user.id,
                 f"🎉 Party Wallet: {wallet}g / {max_w}g max\n"
                 f"Mode: {mode}\nUsage: !ptwallet [amount]")
        return
    try:
        amount = int(args[1])
        if amount < 0:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "⚠️ Enter a valid positive amount.")
        return
    max_w = _limit("max", _DEF_MAX)
    if amount > max_w:
        await _w(bot, user.id,
                 f"⚠️ Exceeds wallet max of {max_w}g.\n"
                 f"Use !ptlimit max to raise it first.")
        return
    _set_wallet(amount)
    await _w(bot, user.id,
             f"🎉 Party Wallet Set\nWallet: {amount}g\n"
             f"Handled by: ChillTopiaMC\nBankerBot affected: NO")


# ---------------------------------------------------------------------------
# !ptadd [user] [minutes]  — add party tipper  (owner only)
# ---------------------------------------------------------------------------

async def handle_ptadd(bot: BaseBot, user: User, args: list[str]) -> None:
    if not is_owner(user.username):
        await _w(bot, user.id, "🔒 Owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id,
                 "Usage: !ptadd [user] [minutes]\n"
                 "Example: !ptadd @Player 60")
        return
    target = args[1].lstrip("@")
    try:
        minutes = int(args[2])
        if minutes < 1:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "⚠️ Duration must be at least 1 minute.")
        return
    minutes = min(minutes, _DEF_DUR)
    # Attempt to resolve user_id from room (best-effort)
    target_id = f"offline_{target.lower()}"
    try:
        from modules.room_utils import _get_all_room_users
        for u, _ in await _get_all_room_users(bot):
            if u.username.lower() == target.lower():
                target_id = u.id
                break
    except Exception:
        pass
    _add_tipper(target_id, target, minutes, user.username)
    await _w(bot, user.id,
             f"✅ Party Tipper Added\nUser: @{target}\nDuration: {minutes}m\n"
             f"Can use: !tip only\nParty Mode Required: YES")


# ---------------------------------------------------------------------------
# !ptremove [user]  — remove party tipper  (owner only)
# ---------------------------------------------------------------------------

async def handle_ptremove(bot: BaseBot, user: User, args: list[str]) -> None:
    if not is_owner(user.username):
        await _w(bot, user.id, "🔒 Owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !ptremove [user]")
        return
    target = args[1].lstrip("@")
    if _remove_tipper(target):
        await _w(bot, user.id, f"✅ Party Tipper Removed\nUser: @{target}")
    else:
        await _w(bot, user.id, f"@{target} is not a party tipper.")


# ---------------------------------------------------------------------------
# !ptclear  — remove all party tippers  (owner only)
# ---------------------------------------------------------------------------

async def handle_ptclear(bot: BaseBot, user: User) -> None:
    if not is_owner(user.username):
        await _w(bot, user.id, "🔒 Owner only.")
        return
    count = _clear_tippers()
    await _w(bot, user.id, f"✅ All Party Tippers Cleared\nRemoved: {count}")


# ---------------------------------------------------------------------------
# !ptlist  — show party tippers  (public)
# ---------------------------------------------------------------------------

async def handle_ptlist(bot: BaseBot, user: User) -> None:
    rows     = _list_tippers()
    mode_str = "ON" if _party_mode_on() else "OFF"
    wallet   = _get_wallet()
    if not rows:
        await _w(bot, user.id,
                 f"🎉 Party Tippers: none\nMode: {mode_str}  Wallet: {wallet}g")
        return
    daily_cap = _limit("daily", _DEF_DAILY)
    now       = datetime.now(timezone.utc)
    lines     = [f"🎉 Party Tippers | Mode: {mode_str} | Wallet: {wallet}g"]
    for r in rows:
        uname      = r["username"]
        expires    = r.get("expires_at", "") or ""
        daily_used = r.get("daily_used", 0) or 0
        last_reset = (r.get("last_reset", "") or "")[:10]
        if last_reset != now.strftime("%Y-%m-%d"):
            daily_used = 0
        if expires:
            try:
                exp_dt   = datetime.fromisoformat(expires)
                mins_left = int((exp_dt - now).total_seconds() / 60)
                exp_str  = "expired" if mins_left <= 0 else f"{mins_left}m left"
            except (ValueError, TypeError):
                exp_str = "?"
        else:
            exp_str = "permanent"
        lines.append(f"@{uname} — {exp_str} — {daily_used}g/{daily_cap}g used")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# !ptlimits  — show party tip limits  (public)
# ---------------------------------------------------------------------------

async def handle_ptlimits(bot: BaseBot, user: User) -> None:
    single  = _limit("single", _DEF_SINGLE)
    all_lim = _limit("all",    _DEF_ALL)
    daily   = _limit("daily",  _DEF_DAILY)
    max_w   = _limit("max",    _DEF_MAX)
    await _w(bot, user.id,
             f"🎉 Party Tip Limits\n"
             f"Single: {single}g\nAll: {all_lim}g each\n"
             f"Daily: {daily}g\nWallet Max: {max_w}g\n"
             f"Party Mode Required: YES")


# ---------------------------------------------------------------------------
# !ptlimit [single|all|daily|max] [amount]  — set limits  (owner only)
# ---------------------------------------------------------------------------

async def handle_ptlimit(bot: BaseBot, user: User, args: list[str]) -> None:
    if not is_owner(user.username):
        await _w(bot, user.id, "🔒 Owner only.")
        return
    valid = ("single", "all", "daily", "max")
    if len(args) < 3 or args[1].lower() not in valid:
        await _w(bot, user.id,
                 "Usage: !ptlimit [single|all|daily|max] [amount]\n"
                 "Example: !ptlimit single 10")
        return
    limit_type = args[1].lower()
    try:
        amount = int(args[2])
        if amount < 1:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "⚠️ Enter a positive whole number.")
        return
    _set_limit(limit_type, amount)
    await _w(bot, user.id,
             f"✅ Party Tip Limit Updated\n{limit_type} = {amount}g")


# ---------------------------------------------------------------------------
# !tip [user] [amount] / !tip all [amount]
# ---------------------------------------------------------------------------

async def handle_tip(bot: BaseBot, user: User, args: list[str]) -> None:
    """!tip — party tip from ChillTopiaMC Party Wallet.

    Formats:
      !tip @user [amount]      — tip one player
      !tip [count] [amount]    — tip N random players
      !tip all [amount]        — tip all eligible players
    """
    if not _party_tip_enabled():
        await _w(bot, user.id,
                 "🛑 Party Tip is temporarily disabled.\n"
                 "Ask the owner to use !ptenable to restore.")
        return
    owner  = is_owner(user.username)
    tipper = _is_active_tipper(user.username)
    if not owner and not tipper:
        await _w(bot, user.id,
                 "🔒 Party Tipper only.\n"
                 "Ask the owner for temporary party tipping access.")
        return
    if not _party_mode_on():
        await _w(bot, user.id,
                 "🎉 Party Mode is OFF.\n"
                 "Party tipping only works during Party Mode.")
        return
    if len(args) < 3:
        await _w(bot, user.id,
                 "Usage: !tip @user [amt] | !tip all [amt] | !tip [count] [amt]")
        return
    wallet = _get_wallet()
    if wallet <= 0:
        await _w(bot, user.id,
                 "⚠️ Party Wallet is empty.\nOwner must add party gold with !ptwallet")
        return

    target = args[1]

    # ── !tip all [amount] ────────────────────────────────────────────────
    if target.lower() == "all":
        await _handle_tip_all(bot, user, args, wallet)
        return

    # ── !tip [count] [amount]  (first arg is a positive integer) ─────────
    try:
        count = int(target)
        if count >= 1:
            await _handle_tip_random(bot, user, args, wallet, count)
            return
        else:
            await _w(bot, user.id, "⚠️ Player count must be at least 1.")
            return
    except ValueError:
        pass

    # ── !tip @user [amount] ───────────────────────────────────────────────
    target_raw = target.lstrip("@")
    try:
        amount = int(args[2])
        if amount < 1:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "Amount must be at least 1g.")
        return
    single_cap = _limit("single", _DEF_SINGLE)
    daily_cap  = _limit("daily",  _DEF_DAILY)
    if amount > single_cap:
        await _w(bot, user.id,
                 f"⚠️ Single tip limit: {single_cap}g\nDaily cap: {daily_cap}g")
        return
    if wallet < amount:
        await _w(bot, user.id,
                 f"⚠️ Not enough party gold.\nNeeded: {amount}g  Available: {wallet}g")
        return
    used = _get_daily_used(user.username)
    if used + amount > daily_cap:
        remaining = max(0, daily_cap - used)
        await _w(bot, user.id,
                 f"⚠️ Daily cap reached.\nUsed: {used}g / {daily_cap}g\n"
                 f"Remaining: {remaining}g")
        return
    if _is_bot(target_raw):
        await _w(bot, user.id, "🤖 Bots cannot receive gold tips.")
        return
    wallet_before = wallet
    wallet_after  = wallet - amount
    _set_wallet(wallet_after)
    _add_daily_used(user.username, amount)
    _log_party_tip(user.id, user.username, "", target_raw,
                   amount, wallet_before, wallet_after, "success")
    try:
        await bot.highrise.chat(f"💸 Tipped @{target_raw} {amount} gold!")
    except Exception:
        pass
    await _w(bot, user.id,
             f"🎉 Party Tip Sent\n"
             f"To: @{target_raw}\n"
             f"Amount: {amount}g\n"
             f"Party Wallet Left: {wallet_after}g")


async def _handle_tip_random(
    bot: BaseBot, user: User, args: list[str], wallet: int, count: int
) -> None:
    """Tip exactly *count* random eligible players with per-tip announcements."""
    import random as _random
    try:
        amount = int(args[2])
        if amount < 1:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "Amount must be at least 1g.")
        return
    all_cap   = _limit("all",   _DEF_ALL)
    daily_cap = _limit("daily", _DEF_DAILY)
    if amount > all_cap:
        await _w(bot, user.id,
                 f"⚠️ Per-player tip limit: {all_cap}g each\nDaily cap: {daily_cap}g")
        return
    used = _get_daily_used(user.username)
    if used >= daily_cap:
        await _w(bot, user.id,
                 f"⚠️ Daily cap reached.\nUsed: {used}g / {daily_cap}g")
        return
    try:
        from modules.room_utils import _get_all_room_users
        all_users = await _get_all_room_users(bot)
    except Exception:
        await _w(bot, user.id, "Could not fetch room users.")
        return
    eligible = [
        u for u, _ in all_users
        if not _is_bot(u.username) and u.id != user.id
    ]
    if not eligible:
        await _w(bot, user.id,
                 "⚠️ No eligible real players found.\n"
                 "Bots are excluded from all gold tipping.")
        return
    # Cap by daily remaining and eligible count
    daily_remaining = max(0, daily_cap - used)
    max_by_daily    = daily_remaining // max(amount, 1)
    actual_count    = min(count, len(eligible), max_by_daily)
    if actual_count <= 0:
        await _w(bot, user.id,
                 f"⚠️ Daily cap reached.\nUsed: {used}g / {daily_cap}g")
        return
    # Warn if we couldn't fill the requested count, then continue
    if actual_count < count:
        found = min(count, len(eligible))
        if found < count:
            await _w(bot, user.id,
                     f"⚠️ Only {found} eligible player(s) found.\n"
                     f"Tipping {actual_count} player(s) instead.")
        else:
            await _w(bot, user.id,
                     f"⚠️ Daily cap limits tip to {actual_count} player(s).")
    selected     = _random.sample(eligible, actual_count)
    total_needed = amount * actual_count
    if wallet < total_needed:
        await _w(bot, user.id,
                 f"⚠️ Not enough Party Wallet gold.\n"
                 f"Needed: {total_needed}g  Available: {wallet}g")
        return
    wallet_before = wallet
    wallet_after  = wallet - total_needed
    _set_wallet(wallet_after)
    _add_daily_used(user.username, total_needed)
    _log_party_tip(user.id, user.username, "[random]", "[random]",
                   total_needed, wallet_before, wallet_after,
                   "success_random", f"players={actual_count}")
    # Per-tip public announcements
    for u in selected:
        try:
            await bot.highrise.chat(f"💸 Tipped @{u.username} {amount} gold!")
        except Exception:
            pass
    await _w(bot, user.id,
             f"🎉 Party Tip Random\n"
             f"Players tipped: {actual_count}\n"
             f"Amount each: {amount}g\n"
             f"Total: {total_needed}g\n"
             f"Party Wallet Left: {wallet_after}g")


async def _handle_tip_all(bot: BaseBot, user: User, args: list[str], wallet: int) -> None:
    try:
        amount = int(args[2])
        if amount < 1:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "Amount must be at least 1g.")
        return
    all_cap   = _limit("all",   _DEF_ALL)
    daily_cap = _limit("daily", _DEF_DAILY)
    if amount > all_cap:
        await _w(bot, user.id,
                 f"⚠️ All-tip limit: {all_cap}g each\nDaily cap: {daily_cap}g")
        return
    used = _get_daily_used(user.username)
    if used >= daily_cap:
        await _w(bot, user.id,
                 f"⚠️ Daily cap reached.\nUsed: {used}g / {daily_cap}g")
        return
    try:
        from modules.room_utils import _get_all_room_users
        all_users = await _get_all_room_users(bot)
    except Exception:
        await _w(bot, user.id, "Could not fetch room users.")
        return
    eligible = [
        u for u, _ in all_users
        if not _is_bot(u.username) and u.id != user.id
    ]
    if not eligible:
        await _w(bot, user.id,
                 "⚠️ No eligible real players found.\n"
                 "Bots are excluded from all gold tipping.")
        return
    # Cap by daily remaining
    daily_remaining = max(0, daily_cap - used)
    max_players     = min(len(eligible), daily_remaining // max(amount, 1))
    if max_players <= 0:
        await _w(bot, user.id,
                 f"⚠️ Daily cap reached.\nUsed: {used}g / {daily_cap}g")
        return
    eligible        = eligible[:max_players]
    total_needed    = amount * len(eligible)
    if wallet < total_needed:
        await _w(bot, user.id,
                 f"⚠️ Not enough party gold.\n"
                 f"Needed: {total_needed}g  Available: {wallet}g")
        return
    wallet_before = wallet
    wallet_after  = wallet - total_needed
    _set_wallet(wallet_after)
    _add_daily_used(user.username, total_needed)
    _log_party_tip(user.id, user.username, "[all]", "[all]",
                   total_needed, wallet_before, wallet_after,
                   "success_all", f"players={len(eligible)}")
    # Per-tip public announcements
    for u in eligible:
        try:
            await bot.highrise.chat(f"💸 Tipped @{u.username} {amount} gold!")
        except Exception:
            pass
    await _w(bot, user.id,
             f"🎉 Party Tip All\n"
             f"Players tipped: {len(eligible)}\n"
             f"Amount each: {amount}g\n"
             f"Total: {total_needed}g\n"
             f"Party Wallet Left: {wallet_after}g")


# ---------------------------------------------------------------------------
# !tipall redirect — disabled alias, redirects to !tip all [amount]
# ---------------------------------------------------------------------------

async def handle_tipall_redirect(bot: BaseBot, user: User) -> None:
    """!tipall is disabled. Redirects users to !tip all [amount]."""
    await _w(bot, user.id,
             "⚠️ Use !tip all [amount] for Party Tips.\nExample: !tip all 5")
