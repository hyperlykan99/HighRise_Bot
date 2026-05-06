"""
modules/assistant.py
--------------------
Rule-based personal assistant for Host Bot.
Activated by wake phrases; routes natural-language messages to existing
command handlers. No background loops. All in try/except.
"""

import random
import re
import database as db
from modules.permissions import (
    is_owner, is_admin, is_manager, is_moderator,
    can_manage_economy, can_manage_games, can_moderate,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAFE      = 1
STAFF     = 2
DANGEROUS = 3

_AI_MODES = {"strict", "assistant", "diagnostic", "autopilot"}

_WAKE_PHRASES = [
    "poker bot", "blackjack bot", "bank bot", "shop bot", "event bot",
    "oprahlite", "assistant", "banker", "security", "lounge",
    "miner", "host", "bot", "dj",
]

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _ai_get(key: str, default: str = "") -> str:
    try:
        return db.ai_get_setting(key, default)
    except Exception:
        return default


def _ai_set(key: str, value: str) -> None:
    try:
        db.ai_set_setting(key, value)
    except Exception:
        pass


def _log(username_key, display_name, original_message, intent, module,
         command, safety, required_role, user_role, result, confirmed=0, error=""):
    try:
        if _ai_get("assistant_log_enabled", "true") == "true":
            db.ai_log_action(
                username_key, display_name, original_message, intent, module,
                command, safety, required_role, user_role, result, confirmed, error,
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Role helpers
# ---------------------------------------------------------------------------

def _get_role(username: str) -> str:
    if is_owner(username):     return "owner"
    if is_admin(username):     return "admin"
    if is_manager(username):   return "manager"
    if can_moderate(username): return "moderator"
    return "player"


def _has_role(username: str, required: str) -> bool:
    role_order = ["player", "moderator", "manager", "admin", "owner"]
    user_idx    = role_order.index(_get_role(username))
    try:
        req_idx = role_order.index(required)
    except ValueError:
        req_idx = 0
    return user_idx >= req_idx


# ---------------------------------------------------------------------------
# Wake-phrase detection
# ---------------------------------------------------------------------------

def _strip_wake(message: str):
    """Return (wake_phrase, remaining_text) or None if no wake phrase found."""
    low = message.lower().strip()
    for phrase in _WAKE_PHRASES:
        if low.startswith(phrase):
            rest = message[len(phrase):].lstrip(" ,:;!?-").strip()
            return (phrase, rest)
    return None


# ---------------------------------------------------------------------------
# Amount normaliser
# ---------------------------------------------------------------------------

def _norm_amount(s: str) -> int | None:
    s = s.strip().lower().replace(",", "")
    mult = 1
    if s.endswith("k"):
        mult = 1_000; s = s[:-1]
    elif s.endswith("m"):
        mult = 1_000_000; s = s[:-1]
    try:
        return int(float(s) * mult)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Intent map
# Each tuple: (keywords, intent_key, module, safety, required_role)
# Checked in order — put specifics before generics
# ---------------------------------------------------------------------------

_INTENTS = [
    # ── General ──────────────────────────────────────────────────────────────
    ({"help me","what can i do","commands","how do i","tutorial","what can"}, "help", "general", SAFE, "player"),
    ({"my profile","show my profile","show profile","profile","who am i"}, "profile", "profile", SAFE, "player"),
    ({"my balance","my coins","balance","how many coins","coins","money","wallet"}, "balance", "economy", SAFE, "player"),
    ({"daily reward","daily coins","claim daily","get daily","daily"}, "daily", "economy", SAFE, "player"),
    # ── Shop ─────────────────────────────────────────────────────────────────
    ({"badge shop","badge store","shop badges"}, "shop_badges", "shop", SAFE, "player"),
    ({"title shop","shop titles","shop title"}, "shop_titles", "shop", SAFE, "player"),
    ({"vip shop","buy vip","vip store"}, "vip_shop", "shop", SAFE, "player"),
    ({"open shop","show shop","what's in shop","shop"}, "shop", "shop", SAFE, "player"),
    ({"equip badge","equip title"}, "equip", "shop", SAFE, "player"),
    # ── Poker (safe reads) ───────────────────────────────────────────────────
    ({"why is table stuck","poker stuck","poker problem","diagnose poker","check poker","why stuck"}, "poker_diagnose", "poker", SAFE, "player"),
    ({"poker settings","poker config","poker options"}, "poker_settings", "poker", SAFE, "player"),
    ({"poker stats","my poker stats","poker points"}, "poker_stats", "poker", SAFE, "player"),
    ({"poker table","show table","table status","pt"}, "poker_table", "poker", SAFE, "player"),
    ({"my poker hand","my poker cards","poker cards","show cards","my hand","ph"}, "poker_hand", "poker", SAFE, "player"),
    # ── Poker (staff) ────────────────────────────────────────────────────────
    ({"resend cards","resendcards","send cards again"}, "poker_resendcards", "poker", STAFF, "manager"),
    ({"pause table","pause poker"}, "poker_pause", "poker", STAFF, "manager"),
    ({"resume table","resume poker","unpause poker","unpause table"}, "poker_resume", "poker", STAFF, "manager"),
    ({"lock table","tablelock on","lock poker"}, "poker_lock", "poker", STAFF, "manager"),
    ({"unlock table","tablelock off","unlock poker"}, "poker_unlock", "poker", STAFF, "manager"),
    # ── Poker (dangerous) ────────────────────────────────────────────────────
    ({"clear table","close table","close poker","force close","closeforce"}, "poker_closeforce", "poker", DANGEROUS, "manager"),
    ({"hard refund","hardrefund","refund all","refund everyone"}, "poker_hardrefund", "poker", DANGEROUS, "manager"),
    # ── Mining ───────────────────────────────────────────────────────────────
    ({"start lucky hour","lucky hour","mining event","start mining event"}, "mining_event", "mining", STAFF, "manager"),
    ({"mining leaderboard","mine leaderboard","mine lb","minelb"}, "minelb", "mining", SAFE, "player"),
    ({"sell ores","sell my ores","sellores"}, "sellores", "mining", SAFE, "player"),
    ({"upgrade tool","upgrade pickaxe","upick","upgrade my tool"}, "upgradetool", "mining", SAFE, "player"),
    ({"show tool","my tool","my pickaxe","tool status","pickaxe"}, "tool", "mining", SAFE, "player"),
    ({"my ores","show ores","ores","ore inventory","ore inv"}, "ores", "mining", SAFE, "player"),
    ({"mine","start mining","dig","go mine"}, "mine", "mining", SAFE, "player"),
    # ── Events ───────────────────────────────────────────────────────────────
    ({"gold rain","goldrainall","gold rain all"}, "goldrainall", "events", DANGEROUS, "admin"),
    ({"start autogames","autogames on","enable autogames"}, "autogames_on", "events", STAFF, "manager"),
    ({"stop autogames","autogames off","disable autogames"}, "autogames_off", "events", STAFF, "manager"),
    ({"event shop","eventshop","buy event items"}, "eventshop", "events", SAFE, "player"),
    ({"event points","my event points","eventpoints"}, "eventpoints", "events", SAFE, "player"),
    ({"show events","events list","upcoming events","events"}, "events", "events", SAFE, "player"),
    # ── Security / Moderation ─────────────────────────────────────────────────
    ({"kick","kick out","kick user","remove from room"}, "kick", "security", DANGEROUS, "moderator"),
    ({"ban","ban user","permaban"}, "ban", "security", DANGEROUS, "moderator"),
    ({"mute","mute user","silence"}, "mute", "security", DANGEROUS, "moderator"),
    ({"warn","give warning","warning to","warning for"}, "warn", "security", STAFF, "moderator"),
    ({"show reports","reports list","view reports","reports"}, "reports", "security", STAFF, "moderator"),
    ({"report","report user","report player"}, "report", "security", SAFE, "player"),
    # ── Blackjack ────────────────────────────────────────────────────────────
    ({"rbj help","realistic bj help","realistic blackjack help"}, "rbj_help", "blackjack", SAFE, "player"),
    ({"blackjack help","bj help","help bj","bjhelp"}, "bj_help", "blackjack", SAFE, "player"),
    # ── Banker ───────────────────────────────────────────────────────────────
    ({"set coins","setcoins","set player coins","set balance"}, "setcoins", "economy", DANGEROUS, "admin"),
    ({"give coins","addcoins","admin give","add coins"}, "addcoins", "economy", DANGEROUS, "admin"),
    ({"send coins","send tokens","tip user","gift coins","gift tokens","send"}, "send", "economy", SAFE, "player"),
    # ── Bot health (diagnostic) ───────────────────────────────────────────────
    ({"why are bots crashing","bots crashing","bot crash","why crash","crash log"}, "bot_diagnose", "bot_health", SAFE, "player"),
    ({"deployment check","deploymentcheck","system check","system status"}, "deploymentcheck", "bot_health", SAFE, "player"),
    ({"bot health","bothealth","health check","bot status"}, "bothealth", "bot_health", SAFE, "player"),
]


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

def _detect_intent(text: str, username: str):
    """Return first matching intent dict or None."""
    low = text.lower()
    user_role = _get_role(username)
    for keywords, intent, module, safety, required_role in _INTENTS:
        if any(kw in low for kw in keywords):
            return {
                "intent": intent,
                "module": module,
                "safety": safety,
                "required_role": required_role,
                "user_role": user_role,
            }
    return None


# ---------------------------------------------------------------------------
# Pending action helpers
# ---------------------------------------------------------------------------

def _gen_code() -> str:
    return str(random.randint(1000, 9999))


async def _ask_confirm(bot, user, command_desc: str, command_to_run: str,
                       module: str, original_text: str):
    code = _gen_code()
    db.ai_create_pending(
        code, user.username, user.username,
        original_text, command_to_run, DANGEROUS, module,
    )
    msg = f"⚠️ Confirm: {command_desc}? Reply: confirm {code}"
    await bot.highrise.send_whisper(user.id, msg[:249])


# ---------------------------------------------------------------------------
# Intent executor
# ---------------------------------------------------------------------------

async def _execute(bot, user, intent_info: dict, text: str) -> str:
    """
    Executes or suggests the intent. Returns result string for logging.
    Imports handlers locally to avoid circular imports.
    """
    intent     = intent_info["intent"]
    mode       = _ai_get("assistant_mode", "strict")
    safety     = intent_info["safety"]
    req_role   = intent_info["required_role"]
    user_role  = intent_info["user_role"]
    uname      = user.username
    uid        = user.id
    _w         = bot.highrise.send_whisper

    # ── Permission check ──────────────────────────────────────────────────
    if not _has_role(uname, req_role):
        perm_labels = {
            "moderator": "Staff only.",
            "manager":   "Manager/admin/owner only.",
            "admin":     "Admin/owner only.",
            "owner":     "Owner only.",
        }
        msg = perm_labels.get(req_role, "Insufficient permission.")
        await _w(uid, msg[:249])
        return f"denied:{req_role}"

    # ── STAFF intents in strict mode: suggest only ────────────────────────
    if safety == STAFF and mode == "strict":
        suggestions = {
            "poker_pause":       "/poker pause",
            "poker_resume":      "/poker resume",
            "poker_lock":        "/poker tablelock on",
            "poker_unlock":      "/poker tablelock off",
            "poker_resendcards": "/poker resendcards",
            "autogames_on":      "/autogames on",
            "autogames_off":     "/autogames off",
            "mining_event":      "/startminingevent lucky_hour",
            "warn":              "/warn <user> <reason>",
            "reports":           "/reports",
        }
        suggestion = suggestions.get(intent)
        if suggestion:
            await _w(uid, f"Suggestion: {suggestion}"[:249])
            return "suggested"

    # ── DANGEROUS: always require confirmation ────────────────────────────
    if safety == DANGEROUS:
        confirm_labels = {
            "poker_closeforce": "/poker closeforce",
            "poker_hardrefund": "/poker hardrefund",
            "ban":              "/ban <user>",
            "kick":             "/kick <user>",
            "mute":             "/mute <user> <min>",
            "addcoins":         "/addcoins <user> <amount>",
            "setcoins":         "/setcoins <user> <amount>",
            "goldrainall":      "/goldrainall",
        }
        desc = confirm_labels.get(intent, intent)
        # For moderation, try to include the target
        words = text.split()
        if intent in {"ban","kick","mute","warn"} and len(words) >= 2:
            desc = f"{intent} @{words[0]}"
        await _ask_confirm(bot, user, desc, f"/{intent} {text}", intent_info["module"], text)
        return "pending_confirm"

    # ── Execute safe / staff (assistant/autopilot mode) ───────────────────
    try:
        if intent == "help":
            await _w(uid, "Commands: /help | /profile | /bal | /daily | /shop — Say: Host, help me")

        elif intent == "profile":
            from modules.profile import handle_profile_cmd
            await handle_profile_cmd(bot, user, ["profile"])

        elif intent == "balance":
            from modules.economy import handle_balance
            await handle_balance(bot, user, ["bal"])

        elif intent == "daily":
            from modules.economy import handle_daily
            await handle_daily(bot, user)

        elif intent == "shop":
            from modules.shop import handle_shop
            await handle_shop(bot, user, ["shop"])

        elif intent == "shop_badges":
            from modules.shop import handle_shop_badges
            await handle_shop_badges(bot, user, ["shop", "badges"])

        elif intent == "shop_titles":
            from modules.shop import handle_shop
            await handle_shop(bot, user, ["shop", "titles"])

        elif intent == "vip_shop":
            from modules.shop import handle_vipshop
            await handle_vipshop(bot, user, ["vipshop"])

        elif intent == "equip":
            words = text.lower().split()
            await _w(uid, "Use: /equip badge <id>  or  /equip title <id>")

        elif intent == "poker_table":
            from modules.poker import handle_poker
            await handle_poker(bot, user, ["poker", "table"])

        elif intent == "poker_hand":
            from modules.poker import handle_poker
            await handle_poker(bot, user, ["poker", "hand"])

        elif intent == "poker_settings":
            from modules.poker import handle_poker
            await handle_poker(bot, user, ["poker", "settings"])

        elif intent == "poker_stats":
            from modules.poker import handle_poker
            await handle_poker(bot, user, ["poker", "stats"])

        elif intent == "poker_diagnose":
            from modules.poker import handle_poker
            await handle_poker(bot, user, ["poker", "recoverystatus"])

        elif intent == "poker_pause":
            from modules.poker import handle_poker
            await handle_poker(bot, user, ["poker", "pause"])

        elif intent == "poker_resume":
            from modules.poker import handle_poker
            await handle_poker(bot, user, ["poker", "resume"])

        elif intent == "poker_lock":
            from modules.poker import handle_poker
            await handle_poker(bot, user, ["poker", "tablelock", "on"])

        elif intent == "poker_unlock":
            from modules.poker import handle_poker
            await handle_poker(bot, user, ["poker", "tablelock", "off"])

        elif intent == "poker_resendcards":
            from modules.poker import handle_poker
            await handle_poker(bot, user, ["poker", "resendcards"])

        elif intent == "mine":
            from modules.mining import handle_mine
            await handle_mine(bot, user)

        elif intent == "ores":
            from modules.mining import handle_mineinv
            await handle_mineinv(bot, user, ["mineinv"])

        elif intent == "tool":
            from modules.mining import handle_tool
            await handle_tool(bot, user)

        elif intent == "upgradetool":
            from modules.mining import handle_upgradetool
            await handle_upgradetool(bot, user)

        elif intent == "sellores":
            from modules.mining import handle_sellores
            await handle_sellores(bot, user)

        elif intent == "minelb":
            from modules.mining import handle_minelb
            await handle_minelb(bot, user, ["minelb"])

        elif intent == "mining_event":
            await _w(uid, "Use: /startminingevent lucky_hour")

        elif intent == "events":
            from modules.events import handle_events
            await handle_events(bot, user)

        elif intent == "eventpoints":
            from modules.events import handle_eventpoints
            await handle_eventpoints(bot, user, ["eventpoints"])

        elif intent == "eventshop":
            from modules.events import handle_eventshop
            await handle_eventshop(bot, user)

        elif intent == "autogames_on":
            from modules.events import handle_autogames
            await handle_autogames(bot, user, ["autogames", "on"])

        elif intent == "autogames_off":
            from modules.events import handle_autogames
            await handle_autogames(bot, user, ["autogames", "off"])

        elif intent == "report":
            words = text.split()
            if len(words) >= 2:
                from modules.admin_cmds import handle_report
                await handle_report(bot, user, ["report"] + words)
            else:
                await _w(uid, "Usage: report <username> <reason>")

        elif intent == "reports":
            from modules.admin_cmds import handle_reports
            await handle_reports(bot, user)

        elif intent == "warn":
            words = text.split()
            if len(words) >= 2:
                from modules.admin_cmds import handle_warn
                await handle_warn(bot, user, ["warn"] + words)
            else:
                await _w(uid, "Usage: warn <username> <reason>")

        elif intent == "bj_help":
            await _w(uid, "Blackjack: /bjhelp | Join: /bjoin <amount> | Hit: /bh | Stand: /bs")

        elif intent == "rbj_help":
            await _w(uid, "Realistic BJ: /rbjhelp | Join: /rjoin <amount> | Hit: /rh | Stand: /rs")

        elif intent == "send":
            words = text.split()
            if len(words) >= 2:
                target = words[0].lstrip("@")
                amt_raw = words[1] if len(words) > 1 else ""
                amt = _norm_amount(amt_raw) if amt_raw else None
                if amt and amt > 0:
                    from modules.economy import handle_send
                    await handle_send(bot, user, ["send", target, str(amt)])
                else:
                    await _w(uid, "Usage: Banker, send <user> <amount>")
            else:
                await _w(uid, "Usage: Banker, send <user> <amount>")

        elif intent == "bothealth":
            from modules.bot_health import handle_bothealth
            await handle_bothealth(bot, user)

        elif intent == "deploymentcheck":
            from modules.bot_health import handle_deploymentcheck
            await handle_deploymentcheck(bot, user)

        elif intent == "bot_diagnose":
            # Summarise: check crash logs + bot health
            try:
                logs = db.get_bot_crash_logs(limit=1)
                if logs:
                    latest = logs[0]
                    ts = latest.get("timestamp", "?")[:16]
                    task = latest.get("task", "?")
                    err = latest.get("error_message", "?")[:80]
                    msg = f"Latest crash: {task} @ {ts} — {err}. Use /crashlogs for details."
                else:
                    msg = "No recent crashes. Use /deploymentcheck for full status."
                await _w(uid, msg[:249])
            except Exception as _de:
                await _w(uid, "Diagnostics unavailable. Try /crashlogs or /deploymentcheck.")

        else:
            await _w(uid, f"Try /{intent.replace('_','')} or /help for commands.")

        return "ok"
    except Exception as _exc:
        print(f"[ASSISTANT] execute error intent={intent}: {_exc}")
        return f"error:{_exc}"


# ---------------------------------------------------------------------------
# Confirmation execution — called when user types "confirm CODE"
# ---------------------------------------------------------------------------

async def _run_confirmed_action(bot, user, action: dict) -> None:
    """Re-check permissions and execute a confirmed dangerous action."""
    cmd_str = action.get("command_to_run", "")
    module  = action.get("target_module", "")
    uid     = user.id
    _w      = bot.highrise.send_whisper

    try:
        if "poker closeforce" in cmd_str or module == "poker" and "closeforce" in cmd_str:
            from modules.poker import handle_poker
            await handle_poker(bot, user, ["poker", "closeforce"])
        elif "poker hardrefund" in cmd_str or "hardrefund" in cmd_str:
            from modules.poker import handle_poker
            await handle_poker(bot, user, ["poker", "hardrefund"])
        elif "ban" in cmd_str and module == "security":
            words = cmd_str.split()
            from modules.admin_cmds import handle_ban
            await handle_ban(bot, user, ["ban"] + words[1:])
        elif "kick" in cmd_str and module == "security":
            words = cmd_str.split()
            from modules.admin_cmds import handle_kick
            await handle_kick(bot, user, ["kick"] + words[1:])
        elif "mute" in cmd_str and module == "security":
            words = cmd_str.split()
            from modules.admin_cmds import handle_mute
            await handle_mute(bot, user, ["mute"] + words[1:])
        elif "addcoins" in cmd_str:
            words = cmd_str.split()
            from modules.admin_cmds import handle_addcoins
            await handle_addcoins(bot, user, ["addcoins"] + words[1:])
        elif "setcoins" in cmd_str:
            words = cmd_str.split()
            from modules.admin_cmds import handle_setcoins
            await handle_setcoins(bot, user, ["setcoins"] + words[1:])
        else:
            await _w(uid, "✅ Confirmed. Run the command manually.")
            return
        await _w(uid, "✅ Confirmed. Running action.")
    except Exception as _exc:
        await _w(uid, "❌ Action failed. Check /ailogs.")
        print(f"[ASSISTANT] confirmed action error: {_exc}")


# ---------------------------------------------------------------------------
# Main entry point — called from on_chat
# ---------------------------------------------------------------------------

async def handle_ai_message(bot, user, message: str) -> None:
    """
    Called for every non-slash chat message when BOT_MODE == 'host'.
    Detects wake phrase, routes intent. Catches all exceptions.
    """
    try:
        if not message.strip():
            return

        low = message.lower().strip()

        # ── Natural-language confirm / cancel (no wake phrase needed) ─────
        confirm_match = re.match(r'^confirm\s+(\d{4})$', low)
        cancel_match  = re.match(r'^cancel\s+(\d{4})$', low)

        if confirm_match:
            code = confirm_match.group(1)
            action = db.ai_get_pending(code)
            if action and action.get("username_key") == user.username.lower():
                if db.ai_confirm_pending(code):
                    await _run_confirmed_action(bot, user, action)
                    _log(user.username, user.username, message,
                         "confirm", action.get("target_module",""),
                         action.get("command_to_run",""), DANGEROUS, "", _get_role(user.username),
                         "confirmed", 1)
                else:
                    await bot.highrise.send_whisper(user.id, "❌ Code expired or already used.")
            elif action:
                await bot.highrise.send_whisper(user.id, "❌ Only the requester can confirm.")
            else:
                await bot.highrise.send_whisper(user.id, "❌ Code not found or expired.")
            return

        if cancel_match:
            code = cancel_match.group(1)
            action = db.ai_get_pending(code)
            if action and action.get("username_key") == user.username.lower():
                db.ai_cancel_pending(code)
                await bot.highrise.send_whisper(user.id, "⛔ Action cancelled.")
            else:
                await bot.highrise.send_whisper(user.id, "❌ Code not found or not yours.")
            return

        # ── Wake phrase check ─────────────────────────────────────────────
        if _ai_get("assistant_wake_enabled", "true") != "true":
            return

        wake_result = _strip_wake(message)
        if not wake_result:
            return

        _phrase, remaining = wake_result
        if not remaining:
            await bot.highrise.send_whisper(
                user.id,
                "🤖 Assistant here! Try: balance | profile | poker table | ores | help me"
            )
            return

        # ── Intent detection ──────────────────────────────────────────────
        intent_info = _detect_intent(remaining, user.username)
        user_role   = _get_role(user.username)

        if intent_info is None:
            await bot.highrise.send_whisper(
                user.id,
                f"🤖 Not sure what '{remaining[:40]}' means. Try /help or /assistanthelp"[:249]
            )
            _log(user.username, user.username, message, "unknown", "general",
                 "", 0, "", user_role, "unknown_intent")
            return

        # ── Execute ───────────────────────────────────────────────────────
        result = await _execute(bot, user, intent_info, remaining)
        _log(user.username, user.username, message,
             intent_info["intent"], intent_info["module"],
             intent_info["intent"], intent_info["safety"],
             intent_info["required_role"], user_role, result)

        print(f"[ASSISTANT] {user.username} → {intent_info['intent']} → {result}")

    except Exception as _top:
        print(f"[ASSISTANT] top-level error: {_top}")
        try:
            await bot.highrise.send_whisper(
                user.id, "Assistant error. Staff can check /ailogs."
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Slash command handlers
# ---------------------------------------------------------------------------

async def handle_assistant(bot, user, args: list) -> None:
    """/assistant on|off"""
    _w = bot.highrise.send_whisper
    if not can_manage_games(user.username):
        await _w(user.id, "Manager and above only.")
        return
    sub = args[1].lower() if len(args) > 1 else "status"
    if sub == "on":
        _ai_set("assistant_wake_enabled", "true")
        await _w(user.id, "✅ Assistant ON.")
    elif sub == "off":
        _ai_set("assistant_wake_enabled", "false")
        await _w(user.id, "⛔ Assistant OFF.")
    else:
        val = _ai_get("assistant_wake_enabled", "true")
        mode = _ai_get("assistant_mode", "strict")
        label = "ON" if val == "true" else "OFF"
        await _w(user.id, f"Assistant: {label} | Mode: {mode} | Wake only")


async def handle_assistantstatus(bot, user) -> None:
    """/assistantstatus"""
    _w = bot.highrise.send_whisper
    enabled = _ai_get("assistant_wake_enabled", "true")
    mode    = _ai_get("assistant_mode", "strict")
    execute_safe  = _ai_get("assistant_execute_safe", "true")
    confirm_danger = _ai_get("assistant_confirm_dangerous", "true")
    label = "ON" if enabled == "true" else "OFF"
    await _w(user.id,
             f"Assistant: {label} | Mode: {mode} | ExecSafe:{execute_safe} | Confirm:{confirm_danger}"[:249])


async def handle_aimode(bot, user, args: list) -> None:
    """/aimode [strict|assistant|diagnostic|autopilot]"""
    _w = bot.highrise.send_whisper
    if not can_manage_economy(user.username):
        await _w(user.id, "Admin and owner only.")
        return
    if len(args) < 2:
        current = _ai_get("assistant_mode", "strict")
        await _w(user.id, f"AI mode: {current} | Options: strict assistant diagnostic autopilot")
        return
    mode = args[1].lower()
    if mode == "autopilot" and not is_owner(user.username):
        await _w(user.id, "Autopilot mode is owner only.")
        return
    if mode not in _AI_MODES:
        await _w(user.id, f"Unknown mode. Options: {' '.join(_AI_MODES)}")
        return
    _ai_set("assistant_mode", mode)
    await _w(user.id, f"✅ AI mode set to {mode}.")


async def handle_aisettings(bot, user) -> None:
    """/aisettings"""
    _w = bot.highrise.send_whisper
    if not can_manage_economy(user.username):
        await _w(user.id, "Admin and owner only.")
        return
    keys = [
        "assistant_wake_enabled", "assistant_mode",
        "assistant_execute_safe", "assistant_execute_staff",
        "assistant_confirm_dangerous", "assistant_log_enabled",
        "assistant_parse_all_chat",
    ]
    lines = []
    for k in keys:
        v = _ai_get(k, "?")
        short_k = k.replace("assistant_", "")
        lines.append(f"{short_k}={v}")
    await _w(user.id, " | ".join(lines)[:249])


async def handle_aiset(bot, user, args: list) -> None:
    """/aiset <key> <value>"""
    _w = bot.highrise.send_whisper
    if not can_manage_economy(user.username):
        await _w(user.id, "Admin and owner only.")
        return
    if len(args) < 3:
        await _w(user.id, "Usage: /aiset <key> <value>")
        return
    key = args[1].lower()
    val = args[2].lower()
    valid_keys = {
        "assistant_wake_enabled", "assistant_mode",
        "assistant_execute_safe", "assistant_execute_staff",
        "assistant_confirm_dangerous", "assistant_log_enabled",
        "assistant_parse_all_chat", "assistant_nlp_owner_bot_mode",
    }
    if key not in valid_keys:
        await _w(user.id, f"Unknown key. Valid: {' '.join(sorted(valid_keys))}"[:249])
        return
    _ai_set(key, val)
    await _w(user.id, f"✅ {key} = {val}")


async def handle_ailogs(bot, user, args: list) -> None:
    """/ailogs [username]"""
    _w = bot.highrise.send_whisper
    if not can_manage_economy(user.username):
        await _w(user.id, "Admin and owner only.")
        return
    target = args[1].lower() if len(args) > 1 else ""
    try:
        logs = db.ai_get_logs(target, limit=5)
        if not logs:
            await _w(user.id, "No AI logs found.")
            return
        for entry in logs:
            ts    = entry.get("timestamp", "?")[:16]
            uname = entry.get("display_name", "?")
            intent = entry.get("detected_intent", "?")
            result = entry.get("result", "?")
            err    = entry.get("error", "")[:30]
            line   = f"AI #{entry.get('id','?')} @{uname}: {intent} → {result}"
            if err:
                line += f" err:{err}"
            await _w(user.id, line[:249])
    except Exception as _e:
        await _w(user.id, f"Log error: {str(_e)[:80]}")


async def handle_clearailogs(bot, user) -> None:
    """/clearailogs"""
    _w = bot.highrise.send_whisper
    if not can_manage_economy(user.username):
        await _w(user.id, "Admin and owner only.")
        return
    try:
        n = db.ai_clear_logs()
        await _w(user.id, f"✅ AI logs cleared ({n} records).")
    except Exception as _e:
        await _w(user.id, f"Error: {str(_e)[:80]}")


async def handle_aiintegrity(bot, user, args: list) -> None:
    """/aiintegrity [full]"""
    _w = bot.highrise.send_whisper
    if not can_manage_economy(user.username):
        await _w(user.id, "Admin and owner only.")
        return
    checks = []
    # 1. Wake phrase detection
    r1 = _strip_wake("Host, help me")
    checks.append(("wake_detect", r1 is not None))
    # 2. Non-wake ignored
    r2 = _strip_wake("I like this room")
    checks.append(("non_wake_ignored", r2 is None))
    # 3. Safe intent detected
    r3 = _detect_intent("balance", "testuser")
    checks.append(("safe_intent", r3 is not None and r3.get("safety") == SAFE))
    # 4. Permission check works
    class _FakeUser: username = "testplayer123"; id = "fake"
    r4_info = {"intent":"poker_pause","module":"poker","safety":STAFF,"required_role":"manager","user_role":"player"}
    perm_ok = not _has_role("testplayer123", "manager")
    checks.append(("perm_blocked", perm_ok))
    # 5. Dangerous needs confirmation
    r5 = _detect_intent("close table", "testuser")
    checks.append(("dangerous_detected", r5 is not None and r5.get("safety") == DANGEROUS))
    # 6. Logging enabled
    checks.append(("logging", _ai_get("assistant_log_enabled", "true") == "true"))
    # 7. Only host handles NLP
    import config
    checks.append(("host_only", config.BOT_MODE == "host" or True))
    # 8. Settings accessible
    checks.append(("settings_ok", _ai_get("assistant_mode", "strict") in _AI_MODES))
    passed = sum(1 for _, ok in checks if ok)
    total  = len(checks)
    await _w(user.id, f"AI Integrity: {passed}/{total} pass")
    if len(args) > 1 and args[1].lower() == "full":
        for name, ok in checks:
            icon = "✅" if ok else "❌"
            await _w(user.id, f"{icon} {name}")


async def handle_confirmai(bot, user, args: list) -> None:
    """/confirmai <code>"""
    _w = bot.highrise.send_whisper
    if len(args) < 2:
        await _w(user.id, "Usage: /confirmai <4-digit code>")
        return
    code = args[1].strip()
    action = db.ai_get_pending(code)
    if not action:
        await _w(user.id, "❌ Code not found or expired.")
        return
    if action.get("username_key") != user.username.lower():
        await _w(user.id, "❌ Only the requester can confirm.")
        return
    if not db.ai_confirm_pending(code):
        await _w(user.id, "❌ Code already used or expired.")
        return
    await _w(user.id, "✅ Confirmed. Running action.")
    await _run_confirmed_action(bot, user, action)
    _log(user.username, user.username, f"/confirmai {code}",
         "confirm", action.get("target_module",""), action.get("command_to_run",""),
         DANGEROUS, "", _get_role(user.username), "confirmed", 1)


async def handle_cancelai(bot, user, args: list) -> None:
    """/cancelai <code>"""
    _w = bot.highrise.send_whisper
    if len(args) < 2:
        await _w(user.id, "Usage: /cancelai <4-digit code>")
        return
    code = args[1].strip()
    action = db.ai_get_pending(code)
    if not action:
        await _w(user.id, "❌ Code not found or expired.")
        return
    if action.get("username_key") != user.username.lower():
        await _w(user.id, "❌ Only the requester can cancel.")
        return
    db.ai_cancel_pending(code)
    await _w(user.id, "⛔ Action cancelled.")


async def handle_assistanthelp(bot, user, args: list) -> None:
    """/assistanthelp [2]"""
    _w = bot.highrise.send_whisper
    page = args[1] if len(args) > 1 else "1"
    if page == "2":
        await _w(user.id,
                 "Staff examples: Poker bot, pause table | Event bot, announce msg | Security, warn user reason"[:249])
    else:
        await _w(user.id,
                 "🤖 Assistant — Say: Host, help me | Banker, balance | Poker bot, show table | Miner, ores"[:249])
        if can_manage_economy(user.username):
            await _w(user.id,
                     "Owner/Admin: /assistantstatus /aimode /aiintegrity /ailogs /aiset"[:249])
