"""
modules/reputation.py
---------------------
Reputation (social status) system for the Highrise Mini Game Bot.

Player commands (everyone):
  /rep <username>            — give +1 rep to a player (once per 24 h)
  /reputation | /repstats   — view your own rep stats and social rank
  /toprep | /repleaderboard — top 10 players by reputation

Staff commands:
  /replog <username>         — view rep log for a player (mod+)
  /addrep <username> <amt>   — manually add rep (admin+)
  /removerep <username> <amt> — manually remove rep (admin+)

Anti-abuse:
  - DB-stored 24 h cooldown per giver (survives restarts)
  - Self-rep blocked
  - Risk note logged if sender is below level 3

All messages ≤ 249 characters.
"""

from datetime import datetime, timezone as _tz

from highrise import BaseBot, User

import database as db
from modules.permissions import can_moderate, can_manage_economy


# ---------------------------------------------------------------------------
# Social rank ladder
# ---------------------------------------------------------------------------

_RANKS: list[tuple[int, str]] = [
    (1000, "Legend"),
    (500,  "Celebrity"),
    (250,  "Icon"),
    (100,  "Loved"),
    (50,   "Popular"),
    (25,   "Known"),
    (10,   "Friendly"),
    (0,    "New Face"),
]


def get_rank(rep: int) -> str:
    for threshold, name in _RANKS:
        if rep >= threshold:
            return name
    return "New Face"


def _fmt_secs(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}h {m}m" if h else f"{m}m"


async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


# ---------------------------------------------------------------------------
# /rep <username>
# ---------------------------------------------------------------------------

async def handle_rep(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /rep <username>")
        return

    target_name = args[1].lstrip("@").strip()
    if target_name.lower() == user.username.lower():
        await _w(bot, user.id, "You cannot rep yourself.")
        return

    target = db.get_user_by_username(target_name)
    if target is None:
        await _w(bot, user.id, f"@{target_name} not found.")
        return

    # 24-hour cooldown check (DB-stored — survives restarts)
    remaining = db.get_rep_cooldown_remaining(user.id)
    if remaining:
        await _w(bot, user.id, f"⏳ Next rep available in {_fmt_secs(remaining)}.")
        return

    # Anti-abuse: flag low-level senders in the log
    giver_profile = db.get_profile(user.id)
    risk_note = ""
    if giver_profile and giver_profile.get("level", 1) < 3:
        risk_note = "low-level sender"

    db.give_rep(
        giver_id          = user.id,
        giver_username    = user.username,
        receiver_id       = target["user_id"],
        receiver_username = target["username"],
        risk_note         = risk_note,
    )

    await _w(bot, user.id,    f"⭐ You gave +1 rep to @{target['username']}.")
    await _w(bot, target["user_id"], f"⭐ @{user.username} gave you +1 rep!")


# ---------------------------------------------------------------------------
# /reputation  /repstats  (self stats)
# ---------------------------------------------------------------------------

async def handle_reputation(bot: BaseBot, user: User) -> None:
    db.ensure_reputation(user.id, user.username)
    r = db.get_reputation(user.id)
    rep      = r["rep_received"]
    given    = r["rep_given"]
    rank     = get_rank(rep)
    remaining = db.get_rep_cooldown_remaining(user.id)
    cd_str   = f"⏳ {_fmt_secs(remaining)}" if remaining else "✅ ready"
    await _w(
        bot, user.id,
        f"⭐ @{user.username} — {rank}\n"
        f"Rep: {rep} received | {given} given\n"
        f"Daily rep: {cd_str}"
    )


# ---------------------------------------------------------------------------
# /toprep  /repleaderboard
# ---------------------------------------------------------------------------

async def handle_toprep(bot: BaseBot, user: User) -> None:
    rows = db.get_top_rep(limit=10)
    if not rows:
        await _w(bot, user.id, "🏆 No reputation data yet.")
        return

    entries = []
    for i, r in enumerate(rows, 1):
        name = r["username"][:10]
        entries.append(f"{i}.@{name}({r['rep_received']})")

    # Group 3 per line to stay compact
    lines = ["🏆 Top Rep:"]
    for i in range(0, len(entries), 3):
        lines.append(" ".join(entries[i : i + 3]))
    await _w(bot, user.id, "\n".join(lines))


# ---------------------------------------------------------------------------
# /replog <username>  (mod+)
# ---------------------------------------------------------------------------

async def handle_replog(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /replog <username>")
        return

    target_name = args[1].lstrip("@").strip()
    rows = db.get_rep_logs(target_name, limit=5)
    if not rows:
        await _w(bot, user.id, f"No rep log entries for @{target_name}.")
        return

    lines = [f"📋 @{target_name[:15]} rep log:"]
    for r in rows:
        ts   = r["timestamp"][:10]
        amt  = r["amount"]
        sign = "+" if amt > 0 else ""
        if r["giver_username"].lower() == target_name.lower():
            lines.append(f"{sign}{amt} to @{r['receiver_username'][:12]} {ts}")
        else:
            note = f" ⚠" if r.get("risk_note") else ""
            lines.append(f"{sign}{amt} from @{r['giver_username'][:12]}{note} {ts}")
    await _w(bot, user.id, "\n".join(lines))


# ---------------------------------------------------------------------------
# /addrep <username> <amount>  (admin+)
# ---------------------------------------------------------------------------

async def handle_addrep(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 3 or not args[2].lstrip("-").isdigit():
        await _w(bot, user.id, "Usage: /addrep <username> <amount>")
        return

    target_name = args[1].lstrip("@").strip()
    amount      = abs(int(args[2]))
    if amount < 1:
        await _w(bot, user.id, "Amount must be at least 1.")
        return

    new_total = db.add_rep_staff(target_name, amount, user.username)
    if new_total == -1:
        await _w(bot, user.id, f"@{target_name} not found.")
        return
    await _w(bot, user.id, f"✅ Added {amount} rep to @{target_name}. Now: {new_total}.")


# ---------------------------------------------------------------------------
# /removerep <username> <amount>  (admin+)
# ---------------------------------------------------------------------------

async def handle_removerep(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 3 or not args[2].lstrip("-").isdigit():
        await _w(bot, user.id, "Usage: /removerep <username> <amount>")
        return

    target_name = args[1].lstrip("@").strip()
    amount      = abs(int(args[2]))
    if amount < 1:
        await _w(bot, user.id, "Amount must be at least 1.")
        return

    new_total = db.remove_rep_staff(target_name, amount, user.username)
    if new_total == -1:
        await _w(bot, user.id, f"@{target_name} not found.")
        return
    await _w(bot, user.id, f"✅ Removed {amount} rep from @{target_name}. Now: {new_total}.")
