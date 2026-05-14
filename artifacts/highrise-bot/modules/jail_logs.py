"""modules/jail_logs.py — Thin logging helpers for the Luxe Jail system (3.4A)."""
from modules.jail_store import log_jail_action


def log_jail_purchase(
    target_uid: str, target_uname: str,
    by_uid: str, by_uname: str,
    minutes: int, cost: int,
) -> None:
    log_jail_action(
        "jail_purchase", target_uid, target_uname,
        by_uid, by_uname, cost, f"duration_mins={minutes}",
    )


def log_jail_release(
    target_uid: str, target_uname: str,
    by_uid: str, by_uname: str,
) -> None:
    log_jail_action(
        "jail_release", target_uid, target_uname,
        by_uid, by_uname, 0, "staff_release",
    )


def log_bail(
    target_uid: str, target_uname: str,
    payer_uid: str, payer_uname: str,
    cost: int,
) -> None:
    log_jail_action(
        "bail", target_uid, target_uname,
        payer_uid, payer_uname, cost, "",
    )
