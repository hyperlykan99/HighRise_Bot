"""
modules/bank.py
---------------
Bank system for the Highrise Mini Game Bot.

Player commands:
  /bank              — balance + bank overview
  /send <user> <amt> — send coins (with anti-abuse checks)
  /transactions [sent|received] [page]
  /bankstats         — full economy stats

Manager/admin commands:
  /viewtx <user> [sent|received] [page]
  /bankwatch <user>
  /bankblock <user>
  /bankunblock <user>
  /banksettings

Owner/admin-only settings:
  /setminsend <amt>          — minimum transfer amount
  /setmaxsend <amt>          — maximum per transfer
  /setsendlimit <amt>        — daily send cap
  /setnewaccountdays <days>  — account age gate
  /setminlevelsend <level>   — level gate
  /setmintotalearned <amt>   — earned-coins gate
  /setmindailyclaims <n>     — daily-claim count gate
  /setsendtax <pct>          — transfer tax %
  /sethighriskblocks on|off  — block HIGH-risk transfers

Ledger (manager+):
  /ledger <user> [page]
"""

import time
from datetime import datetime

from highrise import BaseBot, User

import database as db
from modules.permissions import is_admin, is_manager, can_moderate, can_manage_economy

# ---------------------------------------------------------------------------
# In-memory send cooldown  (10 s per user)
# ---------------------------------------------------------------------------
_send_cooldown: dict[str, float] = {}
_COOLDOWN_SECS = 10


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _w(bot: BaseBot, uid: str, msg: str):
    """Shorthand whisper — truncates to 249 chars for safety."""
    return bot.highrise.send_whisper(uid, msg[:249])


def _tx_line(tx: dict, viewer_id: str) -> str:
    """Single compact transaction line for a viewer."""
    direction = "→" if tx["sender_id"] == viewer_id else "←"
    other = tx["receiver_username"] if tx["sender_id"] == viewer_id else tx["sender_username"]
    amt   = tx["amount_sent"] if tx["sender_id"] == viewer_id else tx["amount_received"]
    fee   = f" fee:{tx['fee']}c" if tx["fee"] > 0 and tx["sender_id"] == viewer_id else ""
    risk  = tx["risk_level"][:3]  # LOW/MED/HIG
    ts    = tx["timestamp"][:10] if tx["timestamp"] else "?"
    status = "" if tx["status"] == "completed" else f" [{tx['status']}]"
    return f"{direction} @{other[:14]} {amt:,}c{fee} {risk} {ts}{status}"


def _compute_risk(sender_id: str, sender_balance: int, amount: int,
                  receiver_id: str) -> tuple[str, str]:
    """Return (risk_level, risk_reason). Uses DB helper queries."""
    flags = []
    score = 0  # 0=low 1=medium 2=high

    def bump(n, reason):
        nonlocal score
        score = max(score, n)
        flags.append(reason)

    # Flag: sends >80% of balance
    if sender_balance > 0 and amount > sender_balance * 0.8:
        bump(1, ">80% balance")

    # Flag: sent within 10 min of daily claim
    try:
        last_ts = db.get_last_daily_claim_ts(sender_id)
        if last_ts:
            elapsed = (datetime.utcnow() - datetime.fromisoformat(last_ts)).total_seconds()
            if elapsed < 600:
                bump(1, "<10min after daily")
    except Exception:
        pass

    # Flag: repeated sends to same receiver
    recent_to = db.get_recent_sends_count_to(sender_id, receiver_id, hours=24)
    if recent_to >= 5:
        bump(2, "5+ sends same user/24h")
    elif recent_to >= 3:
        bump(1, "3+ sends same user/24h")

    # Flag: many low-level senders funneling to same receiver
    low_senders = db.count_low_level_senders_to(receiver_id, hours=24)
    if low_senders >= 3:
        bump(1, "3+ low-lvl senders→recv")

    # Flag: forwarding recently received coins
    recent_recv = db.get_recent_received_amount(sender_id, hours=24)
    if recent_recv > 0 and amount >= recent_recv * 0.8:
        bump(1, "forwarding received coins")

    levels = ["LOW", "MEDIUM", "HIGH"]
    return levels[min(score, 2)], (", ".join(flags) if flags else "clean")


def _parse_tx_args(args: list[str], offset: int = 1):
    """Parse [sent|received] [page] from args starting at offset.
    Returns (direction, page) where direction is None/'sent'/'received'."""
    direction = None
    page = 1
    for a in args[offset:]:
        al = a.lower()
        if al in ("sent", "send"):
            direction = "sent"
        elif al in ("received", "receive", "recv"):
            direction = "received"
        elif al.isdigit():
            page = max(1, int(a))
    return direction, page


# ---------------------------------------------------------------------------
# Player commands
# ---------------------------------------------------------------------------

async def handle_bank(bot: BaseBot, user: User, _args: list[str]):
    """/bank — show overview."""
    db.ensure_user(user.id, user.username)
    db.ensure_bank_user(user.id)

    profile = db.get_profile(user.id)
    bus     = db.get_bank_user_stats(user.id)
    settings = db.get_bank_settings()

    bal        = profile.get("balance", 0)
    earned     = profile.get("total_coins_earned", 0)
    tip_earned = profile.get("tip_coins_earned", 0)
    organic    = earned - tip_earned
    sent    = bus.get("total_sent", 0)
    recv    = bus.get("total_received", 0)
    daily_limit = int(settings.get("daily_send_limit", 3000))
    daily_used  = db.get_daily_sent_today(user.id)
    daily_left  = max(0, daily_limit - daily_used)

    blocked = bool(bus.get("bank_blocked", 0))
    if blocked:
        status = "❌ Blocked"
    else:
        elig = db.check_send_eligibility(user.id, settings)
        status = "✅ Eligible" if elig["eligible"] else f"🔒 {elig['reason'][:40]}"

    display = db.get_display_name(user.id, user.username)
    msg = (
        f"🏦 {display}\n"
        f"Bal: {bal:,}c | Daily left: {daily_left:,}c\n"
        f"Sent: {sent:,}c | Recv: {recv:,}c\n"
        f"Earned: {organic:,}c | Tips: {tip_earned:,}c\n"
        f"Status: {status}"
    )
    await _w(bot, user.id, msg)


async def handle_send(bot: BaseBot, user: User, args: list[str]):
    """/send <username> <amount>"""
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /send <username> <amount>")
        return

    # Normalize username immediately (strips @, trims spaces)
    target_name = args[1].lstrip("@").strip()
    try:
        amount = int(args[2])
    except (ValueError, IndexError):
        await _w(bot, user.id, "❌ Amount must be a whole number.")
        return

    if amount <= 0:
        await _w(bot, user.id, "❌ Amount must be positive.")
        return

    if not target_name:
        await _w(bot, user.id, "❌ Invalid username.")
        return

    # Self-send (compare normalized names)
    if target_name.lower() == user.username.lower():
        await _w(bot, user.id, "❌ You cannot send coins to yourself.")
        return

    # Cooldown
    now = time.monotonic()
    if now - _send_cooldown.get(user.id, 0) < _COOLDOWN_SECS:
        wait = int(_COOLDOWN_SECS - (now - _send_cooldown.get(user.id, 0)))
        await _w(bot, user.id, f"⏳ Wait {wait}s before sending again.")
        return

    db.ensure_user(user.id, user.username)
    db.ensure_bank_user(user.id)

    settings = db.get_bank_settings()
    min_amt  = int(settings.get("min_send_amount", 10))
    max_amt  = int(settings.get("max_send_amount", 1000))

    if amount < min_amt:
        await _w(bot, user.id, f"❌ Minimum send is {min_amt}c.")
        return
    if amount > max_amt:
        await _w(bot, user.id, f"❌ Maximum per send is {max_amt:,}c.")
        return

    # Bank blocked?
    bus = db.get_bank_user_stats(user.id)
    if bus.get("bank_blocked"):
        await _w(bot, user.id, "❌ Transfer blocked. Check /bank.")
        return

    # Eligibility
    elig = db.check_send_eligibility(user.id, settings)
    if not elig["eligible"]:
        await _w(bot, user.id, f"❌ {elig['reason']}")
        return

    # Daily limit
    daily_limit = int(settings.get("daily_send_limit", 3000))
    daily_used  = db.get_daily_sent_today(user.id)
    if daily_used + amount > daily_limit:
        remaining = max(0, daily_limit - daily_used)
        await _w(bot, user.id, f"❌ Daily limit reached. Remaining: {remaining:,}c.")
        return

    # Balance
    sender_bal = db.get_balance(user.id)
    if sender_bal < amount:
        await _w(bot, user.id, "❌ Not enough coins.")
        return

    # Find receiver — normalize username, auto-create placeholder if offline/unknown
    target_name = target_name.lstrip("@").strip()
    receiver = db.resolve_or_create_user(target_name)
    if receiver is None:
        await _w(bot, user.id, "❌ Invalid username.")
        return
    if receiver["user_id"] == user.id:
        await _w(bot, user.id, "❌ You cannot send coins to yourself.")
        return
    db.ensure_bank_user(receiver["user_id"])

    # Risk scoring
    risk_level, risk_reason = _compute_risk(user.id, sender_bal, amount, receiver["user_id"])

    high_risk_blocks = settings.get("high_risk_blocks", "true").lower() == "true"
    if risk_level == "HIGH" and high_risk_blocks:
        db.record_blocked_transaction(
            user.id, user.username,
            receiver["user_id"], receiver["username"],
            amount, risk_level, risk_reason
        )
        db.increment_suspicious_count(user.id)
        await _w(bot, user.id, "❌ Transfer blocked by bank safety rules.")
        print(f"[BANK] HIGH RISK blocked: {user.username}→{receiver['username']} "
              f"{amount}c | {risk_reason}")
        return

    # Fee & atomic transfer  (tax_free_bank event waives the fee entirely)
    from modules.events import get_event_effect
    _ev = get_event_effect()
    if _ev["tax_free"]:
        fee = 0
    else:
        tax_pct = int(settings.get("send_tax_percent", 5))
        fee     = max(0, round(amount * tax_pct / 100))
    amount_received = amount - fee

    _send_cooldown[user.id] = time.monotonic()

    result = db.do_bank_transfer(
        user.id, user.username,
        receiver["user_id"], receiver["username"],
        amount, fee, risk_level, risk_reason
    )

    if not result["success"]:
        reason = result.get("reason", "error")
        if reason == "insufficient_funds":
            await _w(bot, user.id, "❌ Not enough coins.")
        else:
            await _w(bot, user.id, "❌ Transfer failed. Try again.")
        return

    from modules.quests import track_quest
    track_quest(user.id, "bank_send")

    recv_display   = db.get_display_name(receiver["user_id"], receiver["username"])
    sender_display = db.get_display_name(user.id, user.username)

    # ── Notify sender ────────────────────────────────────────────────────────
    if fee > 0:
        await _w(bot, user.id, f"✅ Sent {amount_received:,}c to {recv_display}. Fee: {fee}c.")
    else:
        await _w(bot, user.id, f"✅ Sent {amount_received:,}c to {recv_display}.")

    # ── Notify receiver (whisper if online, else queue for later) ────────────
    recv_bus = db.get_bank_user_stats(receiver["user_id"])
    if recv_bus.get("bank_notify", 1):
        fee_note = f" Fee: {fee}c." if fee > 0 else ""
        recv_msg = (
            f"🏦 You received {amount_received:,}c from @{user.username}.{fee_note}"
        )[:249]
        delivered = False
        try:
            await bot.highrise.send_whisper(receiver["user_id"], recv_msg)
            delivered = True
        except Exception:
            print(f"[BANK] Receiver @{receiver['username']} offline; trying subscriber DM...")

        # Try inbox DM via conversation if whisper failed and user is subscribed
        if not delivered:
            sub = db.get_subscriber(receiver["username"])
            if (sub and sub.get("conversation_id")
                    and sub.get("subscribed") and sub.get("dm_available")):
                from modules.subscribers import add_unsubscribe_footer
                dm_msg = add_unsubscribe_footer(
                    f"🏦 You received {amount_received:,}c from @{user.username}. "
                    "Use /transactions."
                )
                try:
                    await bot.highrise.send_message(sub["conversation_id"], dm_msg)
                    delivered = True
                    db.set_subscriber_last_dm(receiver["username"])
                    print(f"[BANK] DM delivered to subscriber @{receiver['username']}.")
                except Exception as exc:
                    db.set_dm_available(receiver["username"], False)
                    print(f"[BANK] Subscriber DM to @{receiver['username']} failed: {exc}")

        if not delivered:
            db.add_bank_notification(
                receiver["username"], user.username, amount_received, fee
            )
            # Rewrite sender message to mention offline delivery
            recv_display_name = recv_display
            if fee > 0:
                offline_msg = (
                    f"✅ Sent {amount_received:,}c to {recv_display_name}. "
                    f"Fee: {fee}c. They'll be notified when they return."
                )[:249]
            else:
                offline_msg = (
                    f"✅ Sent {amount_received:,}c to {recv_display_name}. "
                    f"They'll be notified when they return."
                )[:249]
            # Replace the sender confirmation that was already sent
            await _w(bot, user.id, offline_msg)

    if risk_level == "MEDIUM":
        print(f"[BANK] MEDIUM: {user.username}→{receiver['username']} "
              f"{amount}c | {risk_reason}")


async def handle_transactions(bot: BaseBot, user: User, args: list[str]):
    """/transactions [sent|received] [page]"""
    db.ensure_user(user.id, user.username)
    direction, page = _parse_tx_args(args, offset=1)
    rows = db.get_transactions_for(user.id, direction=direction, page=page)

    label = {"sent": "Sent", "received": "Recv", None: "All"}.get(direction, "All")
    if not rows:
        await _w(bot, user.id, f"No {label.lower()} transactions yet.")
        return

    lines = [f"-- {label} Transfers (p{page}) --"]
    for tx in rows:
        lines.append(_tx_line(tx, user.id))
    msg = "\n".join(lines)
    if len(msg) > 248:
        msg = msg[:245] + "..."
    await _w(bot, user.id, msg)


async def handle_bankstats(bot: BaseBot, user: User):
    """/bankstats — full economy breakdown."""
    db.ensure_user(user.id, user.username)
    db.ensure_bank_user(user.id)

    profile  = db.get_profile(user.id)
    bus      = db.get_bank_user_stats(user.id)
    bj_row   = db.get_bj_stats(user.id)
    rbj_row  = db.get_rbj_stats(user.id)

    bal        = profile.get("balance", 0)
    earned     = profile.get("total_coins_earned", 0)
    tip_earned = profile.get("tip_coins_earned", 0)
    organic    = earned - tip_earned
    sent    = bus.get("total_sent", 0)
    recv    = bus.get("total_received", 0)

    bj_net  = bj_row.get("bj_total_won", 0) - bj_row.get("bj_total_bet", 0)
    rbj_net = rbj_row.get("rbj_total_won", 0) - rbj_row.get("rbj_total_bet", 0)

    bj_sign  = "+" if bj_net  >= 0 else ""
    rbj_sign = "+" if rbj_net >= 0 else ""

    display = db.get_display_name(user.id, user.username)
    msg = (
        f"📊 {display}\n"
        f"Bal: {bal:,}c | Gameplay: {organic:,}c | Tips: {tip_earned:,}c\n"
        f"Sent: {sent:,}c | Recv: {recv:,}c\n"
        f"BJ net: {bj_sign}{bj_net:,}c | RBJ: {rbj_sign}{rbj_net:,}c"
    )
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# Staff commands
# ---------------------------------------------------------------------------

async def handle_viewtx(bot: BaseBot, user: User, args: list[str]):
    """/viewtx <username> [sent|received] [page]  — moderator+"""
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /viewtx <username> [sent|received] [page]")
        return

    target_name = args[1].lstrip("@").strip()
    target = db.resolve_or_create_user(target_name)
    if target is None:
        await _w(bot, user.id, "❌ Invalid username.")
        return

    direction, page = _parse_tx_args(args, offset=2)
    rows = db.get_transactions_for(target["user_id"], direction=direction, page=page)

    label = {"sent": "Sent", "received": "Recv", None: "All"}.get(direction, "All")
    if not rows:
        await _w(bot, user.id, f"No {label.lower()} TX for @{target_name}.")
        return

    lines = [f"-- @{target_name} {label} TX (p{page}) --"]
    for tx in rows:
        lines.append(_tx_line(tx, target["user_id"]))
    msg = "\n".join(lines)
    if len(msg) > 248:
        msg = msg[:245] + "..."
    await _w(bot, user.id, msg)


async def handle_bankwatch(bot: BaseBot, user: User, args: list[str]):
    """/bankwatch <username>  — moderator+"""
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /bankwatch <username>")
        return

    clean_name = args[1].lstrip("@").strip()
    db.resolve_or_create_user(clean_name)   # ensure user exists first
    info = db.get_bank_watch_info(clean_name)
    if info is None:
        await _w(bot, user.id, f"❌ @{clean_name} not found.")
        return

    blocked   = "Yes" if info["bank_blocked"] else "No"
    org_e     = info.get("organic_earned", info["total_earned"])
    tip_e     = info.get("tip_earned", 0)
    msg = (
        f"-- 👀 @{info['username']} --\n"
        f"Bal: {info['balance']:,}c Lvl: {info['level']}\n"
        f"Sent: {info['total_sent']:,}c Recv: {info['total_received']:,}c\n"
        f"Daily: {info['daily_sent']:,}c Org: {org_e:,}c Tip: {tip_e:,}c\n"
        f"1st: {info['first_seen']} Claims: {info['total_claims']}\n"
        f"Flags: {info['suspicious_count']} Blocked: {blocked}"
    )
    await _w(bot, user.id, msg)


async def handle_bankblock(bot: BaseBot, user: User, args: list[str], block: bool = True):
    """/bankblock or /bankunblock <username>  — admin+"""
    if not is_admin(user.username):
        await _w(bot, user.id, "Admins only.")
        return
    if len(args) < 2:
        cmd = "bankblock" if block else "bankunblock"
        await _w(bot, user.id, f"Usage: /{cmd} <username>")
        return

    target = db.resolve_or_create_user(args[1].lstrip("@").strip())
    if target is None:
        await _w(bot, user.id, "❌ Invalid username.")
        return

    db.ensure_bank_user(target["user_id"])
    db.set_bank_blocked(target["user_id"], block)
    state = "BLOCKED" if block else "UNBLOCKED"
    await _w(bot, user.id, f"✅ @{target['username']} is now bank {state}.")


async def handle_banksettings(bot: BaseBot, user: User):
    """/banksettings  — manager+"""
    if not is_manager(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    s  = db.get_bank_settings()
    hr = "ON" if s.get("high_risk_blocks", "true").lower() == "true" else "OFF"
    msg = (
        "🏦 Bank Settings\n"
        f"Min/Max: {s.get('min_send_amount','10')}c/{s.get('max_send_amount','1000')}c\n"
        f"Daily: {int(s.get('daily_send_limit','3000')):,}c\n"
        f"Age: {s.get('new_account_days','3')}d | "
        f"Lvl: {s.get('min_level_to_send','3')}\n"
        f"Earned: {int(s.get('min_total_earned_to_send','500')):,}c | "
        f"Claims: {s.get('min_daily_claim_days_to_send','2')}\n"
        f"Tax: {s.get('send_tax_percent','5')}% | Risk block: {hr}"
    )
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# Owner/admin setting commands  (/set…)
# ---------------------------------------------------------------------------

async def handle_bank_set(bot: BaseBot, user: User, cmd: str, args: list[str]):
    """
    Route all /set<bank…> commands. All require admin+.

    Cross-field validation rules:
      setminsend  ≤ max_send_amount
      setmaxsend  ≥ min_send_amount  AND  ≤ daily_send_limit
      setsendlimit ≥ max_send_amount
    """
    if not is_admin(user.username):
        await _w(bot, user.id, "Admins only.")
        return

    # ── on/off toggle ───────────────────────────────────────────────────────
    if cmd == "sethighriskblocks":
        if len(args) < 2 or args[1].lower() not in ("on", "off"):
            await _w(bot, user.id, "Usage: /sethighriskblocks on|off")
            return
        val_str = "true" if args[1].lower() == "on" else "false"
        db.set_bank_setting("high_risk_blocks", val_str)
        state = "ON" if val_str == "true" else "OFF"
        await _w(bot, user.id, f"✅ High risk blocking: {state}.")
        return

    # ── integer commands ─────────────────────────────────────────────────────
    # (db_key, min, max, unit_fmt, label)
    _INT_CMDS = {
        "setminsend":        ("min_send_amount",              1, 1_000_000, "{:,}c",   "Min send amount"),
        "setmaxsend":        ("max_send_amount",              1, 1_000_000, "{:,}c",   "Max send amount"),
        "setsendlimit":      ("daily_send_limit",             1, 10_000_000,"{:,}c",   "Daily send limit"),
        "setnewaccountdays": ("new_account_days",             0,  365,      "{} days", "New account wait"),
        "setminlevelsend":   ("min_level_to_send",            0,  100,      "lvl {}",  "Min level to send"),
        "setmintotalearned": ("min_total_earned_to_send",     0, 10_000_000,"{:,}c",   "Min total earned"),
        "setmindailyclaims": ("min_daily_claim_days_to_send", 0,  365,      "{} days", "Min daily claims"),
        "setsendtax":        ("send_tax_percent",             0,   50,      "{}%",     "Send tax"),
    }

    if cmd not in _INT_CMDS:
        await _w(bot, user.id, f"❌ Unknown bank setting: /{cmd}")
        return

    db_key, mn, mx, unit_fmt, label = _INT_CMDS[cmd]

    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, f"Usage: /{cmd} <number>")
        return

    val = int(args[1])
    if not (mn <= val <= mx):
        await _w(bot, user.id, f"❌ Value must be {mn:,}–{mx:,}.")
        return

    # Cross-field validation
    s = db.get_bank_settings()
    if cmd == "setminsend":
        cur_max = int(s.get("max_send_amount", 1000))
        if val > cur_max:
            await _w(bot, user.id, f"❌ Min ({val:,}c) must be ≤ max send ({cur_max:,}c).")
            return
    elif cmd == "setmaxsend":
        cur_min   = int(s.get("min_send_amount", 10))
        cur_limit = int(s.get("daily_send_limit", 3000))
        if val < cur_min:
            await _w(bot, user.id, f"❌ Max ({val:,}c) must be ≥ min send ({cur_min:,}c).")
            return
        if val > cur_limit:
            await _w(bot, user.id, f"❌ Max ({val:,}c) must be ≤ daily limit ({cur_limit:,}c).")
            return
    elif cmd == "setsendlimit":
        cur_max = int(s.get("max_send_amount", 1000))
        if val < cur_max:
            await _w(bot, user.id, f"❌ Daily limit ({val:,}c) must be ≥ max send ({cur_max:,}c).")
            return

    db.set_bank_setting(db_key, str(val))
    unit_str = unit_fmt.format(val)
    await _w(bot, user.id, f"✅ {label} set to {unit_str}.")


# ---------------------------------------------------------------------------
# Ledger command
# ---------------------------------------------------------------------------

async def handle_banknotify(bot: BaseBot, user: User, args: list[str]):
    """/banknotify [on|off]"""
    db.ensure_user(user.id, user.username)
    db.ensure_bank_user(user.id)
    sub = args[1].lower() if len(args) > 1 else ""
    if sub == "on":
        db.set_bank_notify(user.id, True)
        await _w(bot, user.id, "🏦 Bank notifications turned ON.")
    elif sub == "off":
        db.set_bank_notify(user.id, False)
        await _w(bot, user.id, "🏦 Bank notifications turned OFF.")
    else:
        bus = db.get_bank_user_stats(user.id)
        state = "ON" if bus.get("bank_notify", 1) else "OFF"
        await _w(bot, user.id, f"🏦 Bank notifications: {state}")


async def handle_notifications(bot: BaseBot, user: User):
    """/notifications — show pending and recent bank notifications."""
    db.ensure_user(user.id, user.username)
    rows = db.get_recent_bank_notifications(user.username, limit=10)
    if not rows:
        await _w(bot, user.id, "🏦 No bank notifications.")
        return
    pending = [r for r in rows if not r["delivered"]]
    if len(rows) == 1:
        r = rows[0]
        fee_note = f" Fee: {r['fee']}c." if r["fee"] else ""
        ts = r["timestamp"][:10] if r.get("timestamp") else ""
        status = "⏳" if not r["delivered"] else "✅"
        msg = (
            f"{status} 🏦 +{r['amount_received']:,}c from @{r['sender_username']}"
            f"{fee_note} {ts}"
        )[:249]
        await _w(bot, user.id, msg)
    elif pending:
        total = sum(r["amount_received"] for r in pending)
        if len(pending) <= 3:
            lines = [f"🏦 {len(pending)} pending deposit(s):"]
            for r in pending:
                fee_note = f" Fee:{r['fee']}c" if r["fee"] else ""
                lines.append(
                    f"+{r['amount_received']:,}c from @{r['sender_username']}{fee_note}"
                )
            msg = "\n".join(lines)[:249]
        else:
            msg = (
                f"🏦 You have {len(pending)} pending deposits. "
                f"Total: {total:,}c. More: /transactions"
            )[:249]
        await _w(bot, user.id, msg)
    else:
        r = rows[0]
        fee_note = f" Fee: {r['fee']}c." if r["fee"] else ""
        ts = r["timestamp"][:10] if r.get("timestamp") else ""
        msg = (
            f"✅ Last deposit: +{r['amount_received']:,}c from @{r['sender_username']}"
            f"{fee_note} {ts}. Use /transactions for full history."
        )[:249]
        await _w(bot, user.id, msg)


async def handle_clearnotifications(bot: BaseBot, user: User):
    """/clearnotifications — mark all pending bank notifications as read."""
    db.ensure_user(user.id, user.username)
    pending = db.get_pending_bank_notifications(user.username)
    if not pending:
        await _w(bot, user.id, "🏦 No pending bank notifications to clear.")
        return
    db.mark_bank_notifications_delivered(user.username)
    await _w(bot, user.id, f"✅ Cleared {len(pending)} pending bank notification(s).")


async def handle_delivernotifications(bot: BaseBot, user: User, args: list[str]):
    """/delivernotifications <username> — staff: attempt delivery for an online user."""
    if not is_manager(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /delivernotifications <username>")
        return
    target_name = args[1].lstrip("@").lower().strip()
    pending = db.get_pending_notifications_for_staff(target_name)
    if not pending:
        await _w(bot, user.id, f"No pending notifications for @{target_name}.")
        return
    target = db.get_user_by_username(target_name)
    if target is None:
        await _w(bot, user.id, f"@{target_name} not found in DB.")
        return
    target_id = target["user_id"]
    total = sum(r["amount_received"] for r in pending)
    try:
        if len(pending) == 1:
            r = pending[0]
            fee_note = f" Fee: {r['fee']}c." if r["fee"] else ""
            msg = (
                f"🏦 You received {r['amount_received']:,}c "
                f"from @{r['sender_username']}.{fee_note}"
            )[:249]
        elif len(pending) <= 3:
            lines = [f"🏦 {len(pending)} deposits:"]
            for r in pending:
                fee_note = f" Fee:{r['fee']}c" if r["fee"] else ""
                lines.append(f"+{r['amount_received']:,}c from @{r['sender_username']}{fee_note}")
            msg = "\n".join(lines)[:249]
        else:
            msg = (
                f"🏦 You have {len(pending)} deposits. "
                f"Total: {total:,}c. Use /transactions."
            )[:249]
        await bot.highrise.send_whisper(target_id, msg)
        db.mark_bank_notifications_delivered(target_name)
        await _w(bot, user.id,
                 f"✅ Delivered {len(pending)} notification(s) to @{target_name}.")
    except Exception as exc:
        for r in pending:
            db.record_notification_attempt_failed(r["id"], str(exc))
        await _w(bot, user.id,
                 f"❌ Could not deliver to @{target_name}: {str(exc)[:100]}")


async def handle_pendingnotifications(bot: BaseBot, user: User, args: list[str]):
    """/pendingnotifications <username> — staff: view pending notifications."""
    if not is_manager(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /pendingnotifications <username>")
        return
    target_name = args[1].lstrip("@").lower().strip()
    pending = db.get_pending_notifications_for_staff(target_name)
    if not pending:
        await _w(bot, user.id, f"No pending notifications for @{target_name}.")
        return
    total = sum(r["amount_received"] for r in pending)
    lines = [f"📬 @{target_name}: {len(pending)} pending (total {total:,}c)"]
    for r in pending[:4]:
        fee_note = f" Fee:{r['fee']}c" if r["fee"] else ""
        ts = r["timestamp"][:10] if r.get("timestamp") else "?"
        attempts = r.get("delivery_attempts", 0)
        lines.append(
            f"+{r['amount_received']:,}c from @{r['sender_username']}{fee_note} "
            f"{ts} (attempts:{attempts})"
        )
    if len(pending) > 4:
        lines.append(f"...and {len(pending) - 4} more.")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_ledger(bot: BaseBot, user: User, args: list[str]):
    """/ledger <username> [page]  — manager+"""
    if not is_manager(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /ledger <username> [page]")
        return

    target_name = args[1]
    page = 1
    if len(args) >= 3 and args[2].isdigit():
        page = max(1, int(args[2]))

    target = db.get_user_by_username(target_name)
    if target is None:
        await _w(bot, user.id, f"@{target_name} not found.")
        return

    rows = db.get_ledger_for(target["user_id"], page=page)
    if not rows:
        await _w(bot, user.id, f"No ledger entries for @{target_name} (p{page}).")
        return

    lines = [f"-- 📒 @{target_name} Ledger (p{page}) --"]
    for r in rows:
        sign = "+" if r["change_amount"] >= 0 else ""
        ts   = r["timestamp"][:10] if r["timestamp"] else "?"
        rel  = f" ←@{r['related_user']}" if r["related_user"] else ""
        lines.append(f"{sign}{r['change_amount']:,}c {r['reason']}{rel} {ts}")
    msg = "\n".join(lines)
    if len(msg) > 248:
        msg = msg[:245] + "..."
    await _w(bot, user.id, msg)
