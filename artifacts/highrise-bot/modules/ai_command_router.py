"""
modules/ai_command_router.py — Top-level AI Command Control Layer router (3.3F).

Flow for every "ai <action>" message:
  1. Safety check (NEVER_EXECUTE? → immediate refusal)
  2. Check if this is a pending confirmation/cancellation
  3. Map natural language → (command_key, args)
  4. Look up whitelist config
  5. Check economy lock if applicable
  6. Check user permission
  7. If confirmation required → prepare + prompt
  8. If direct allowed → execute via existing handler

Public API:
  handle_ai_command(bot, user, text, perm)  → None (sends whisper/chat reply)
  handle_ai_cmd_help(bot, user)             → None (sends command list whisper)
  is_confirm_or_cancel(user_id, text)       → bool (True if it consumed the text)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot, User

from modules.ai_command_safety        import safety_check, validate_coin_amount
from modules.ai_command_permissions   import (
    get_perm_level, has_permission, perm_display,
    PERM_PLAYER, PERM_STAFF, PERM_ADMIN, PERM_OWNER,
)
from modules.ai_command_mapper        import map_command, get_command_config, AI_COMMAND_WHITELIST
from modules.ai_command_confirmation  import (
    prepare_command, get_pending, clear_pending, has_pending,
    is_confirm, is_cancel, build_prompt, CONFIRM_PHRASE,
)
from modules.ai_command_executor      import execute_command
from modules.ai_command_logs          import log_ai_command


async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    """Whisper helper."""
    await bot.highrise.send_whisper(uid, msg[:249])


# ── Pending confirmation handler ──────────────────────────────────────────────

async def is_confirm_or_cancel(bot: "BaseBot", user: "User", raw_text: str) -> bool:
    """
    If user has a pending AI command and sends CONFIRM AI COMMAND or CANCEL,
    handle it and return True. Otherwise return False.
    """
    text = raw_text.strip()
    if not has_pending(user.id):
        return False

    if is_cancel(text):
        clear_pending(user.id)
        log_ai_command(user.username, user.id, "(pending)", [], "cancelled", "user cancelled")
        await _w(bot, user.id, "AI command cancelled.")
        return True

    if is_confirm(text):
        p = get_pending(user.id)
        if not p:
            return False
        clear_pending(user.id)
        cmd  = p["command"]
        args = p["args"]
        ok   = await execute_command(bot, user, cmd, args)
        if ok:
            log_ai_command(user.username, user.id, cmd, args, "executed", "confirmed")
        else:
            log_ai_command(user.username, user.id, cmd, args, "denied", "executor returned false")
            await _w(bot, user.id, f"AI couldn't run !{cmd}. Try the direct command instead.")
        return True

    return False


# ── Main command handler ──────────────────────────────────────────────────────

async def handle_ai_command(
    bot:  "BaseBot",
    user: "User",
    text: str,
    perm: str = "",
) -> None:
    """
    Route natural language AI command text through the full safety/permission/
    confirmation pipeline and either execute, confirm, or refuse.
    """
    if not perm:
        perm = get_perm_level(user.username)

    # 1. Safety gate — always runs first
    refusal = safety_check(text)
    if refusal:
        log_ai_command(user.username, user.id, "(blocked)", [], "safety_blocked", refusal[:80])
        await _w(bot, user.id, refusal[:249])
        return

    # 2. Map natural language → command
    cmd_key, args = map_command(text)
    if not cmd_key:
        await _w(
            bot, user.id,
            "I didn't recognize that command. Type 'ai commands' to see what I can run.",
        )
        return

    cfg = get_command_config(cmd_key)
    if not cfg:
        await _w(bot, user.id, "That command isn't in my whitelist yet.")
        return

    # 3. Economy lock check
    eco_locked = False
    if cfg.get("blocked_if_economy_lock"):
        try:
            from modules.release import is_economy_locked
            eco_locked = is_economy_locked()
        except Exception:
            eco_locked = False
        if eco_locked:
            msg = (
                "Economy lock is ON, so AI cannot edit currency. "
                "Use the direct owner command if you intentionally want to override."
            )
            log_ai_command(user.username, user.id, cmd_key, args or [], "denied", "economy_lock")
            await _w(bot, user.id, msg)
            return

    # 4. Permission check
    required_perm = cfg.get("requires_permission", PERM_PLAYER)
    if not has_permission(perm, required_perm):
        needed  = perm_display(required_perm)
        current = perm_display(perm)
        msg = f"You need {needed} to run AI !{cmd_key}. Your role: {current}."
        log_ai_command(user.username, user.id, cmd_key, args or [], "denied", f"need {needed}")
        await _w(bot, user.id, msg)
        return

    # 5. Validate args for economy commands
    if cmd_key in ("addcoins", "setcoins") and args and len(args) >= 2:
        ok, err = validate_coin_amount(args[1], perm)
        if not ok:
            await _w(bot, user.id, err)
            return

    # 6. Confirmation required?
    if cfg.get("requires_confirmation"):
        risk_map = {
            "STAFF_DIRECT":  "Moderation action",
            "ADMIN_CONFIRM": "Configuration change",
            "OWNER_CONFIRM": "Owner-level economy/config change",
        }
        risk = risk_map.get(cfg["category"], "Elevated risk")
        prepare_command(
            user_id    = user.id,
            command    = cmd_key,
            args       = args or [],
            risk       = risk,
            perm_label = perm_display(required_perm),
            economy    = bool(cfg.get("blocked_if_economy_lock")),
        )
        prompt = build_prompt(cmd_key, args or [], risk, perm_display(required_perm), eco_locked)
        log_ai_command(user.username, user.id, cmd_key, args or [], "prepared", risk)
        await _w(bot, user.id, prompt)
        return

    # 7. Execute directly
    ok = await execute_command(bot, user, cmd_key, args or [])
    if ok:
        log_ai_command(user.username, user.id, cmd_key, args or [], "executed", "direct")
    else:
        log_ai_command(user.username, user.id, cmd_key, args or [], "denied", "executor unknown cmd")
        await _w(bot, user.id, f"I couldn't run !{cmd_key}. Try the direct command instead.")


# ── Help handler ──────────────────────────────────────────────────────────────

async def handle_ai_cmd_help(bot: "BaseBot", user: "User") -> None:
    """Show available AI commands based on the user's permission level."""
    perm = get_perm_level(user.username)

    player_cmds  = "balance, tickets, daily, mine, fish, profile, events, nextevent, shop, luxeshop, vipstatus, tele"
    staff_cmds   = "mute, warn"
    admin_cmds   = "startevent, stopevent, setvipprice"
    owner_cmds   = "addcoins, setcoins"

    if has_permission(perm, PERM_OWNER):
        all_cmds = f"{player_cmds}, {staff_cmds}, {admin_cmds}, {owner_cmds}"
    elif has_permission(perm, PERM_ADMIN):
        all_cmds = f"{player_cmds}, {staff_cmds}, {admin_cmds}"
    elif has_permission(perm, PERM_STAFF):
        all_cmds = f"{player_cmds}, {staff_cmds}"
    else:
        all_cmds = player_cmds

    msg = (
        f"AI commands ({perm_display(perm)}): {all_cmds}. "
        "Staff/admin/owner actions need permission + confirmation. "
        "I can't wipe data, reveal secrets, or bypass locks."
    )
    await _w(bot, user.id, msg[:249])
