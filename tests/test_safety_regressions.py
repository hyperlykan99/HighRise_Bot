import asyncio
import importlib
import os
import sqlite3
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "artifacts" / "highrise-bot"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


@pytest.fixture(autouse=True)
def _fresh_app_imports():
    for name in list(sys.modules):
        if name in {"config", "database"} or name.startswith("modules."):
            sys.modules.pop(name, None)
    yield


def _install_env(monkeypatch, tmp_path, bot_mode="host"):
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.setenv("ROOM_ID", "test-room")
    monkeypatch.setenv("BOT_MODE", bot_mode)
    monkeypatch.setenv("BOT_ID", bot_mode)
    monkeypatch.setenv("SHARED_DB_PATH", str(tmp_path / "test.db"))


def _install_highrise_stub(monkeypatch):
    highrise = types.ModuleType("highrise")
    highrise.BaseBot = object
    highrise.User = object
    models = types.ModuleType("highrise.models")
    models.Position = object
    monkeypatch.setitem(sys.modules, "highrise", highrise)
    monkeypatch.setitem(sys.modules, "highrise.models", models)


class FakeHighrise:
    def __init__(self):
        self.whispers = []

    async def send_whisper(self, uid, message):
        self.whispers.append((uid, message))


class FakeBot:
    def __init__(self):
        self.highrise = FakeHighrise()


class FakeUser:
    id = "user-1"
    username = "normal_user"


def test_radio_cleanup_task_guard(monkeypatch, tmp_path):
    _install_env(monkeypatch, tmp_path, bot_mode="dj")
    rc = importlib.import_module("modules.radio_commands")
    media_cleanup = importlib.import_module("modules.media_cleanup")
    diag = importlib.import_module("modules.radio_diagnostics")

    async def fake_cleanup_poll():
        await asyncio.Event().wait()

    async def fake_media_start(_bot):
        return None

    async def fake_health(_bot):
        return None

    monkeypatch.setattr(rc, "_cleanup_poll_task_handle", None)
    monkeypatch.setattr(rc, "_cleanup_poll_task", fake_cleanup_poll)
    monkeypatch.setattr(media_cleanup, "start", fake_media_start)
    monkeypatch.setattr(diag, "log_startup_health", fake_health)

    async def run_test():
        await rc.startup_radio(FakeBot())
        first = rc._cleanup_poll_task_handle
        assert first is not None and not first.done()

        await rc.startup_radio(FakeBot())
        assert rc._cleanup_poll_task_handle is first

        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

    asyncio.run(run_test())


def test_radio_registry_aliases_are_dj_owned(monkeypatch, tmp_path):
    _install_env(monkeypatch, tmp_path, bot_mode="dj")
    fake_rc = types.ModuleType("modules.radio_commands")
    for name in (
        "handle_request",
        "handle_playfav",
        "handle_queue",
        "handle_skip",
        "handle_remove",
        "handle_cancel",
        "handle_radiohelp",
        "handle_radiostatus",
        "handle_queuelimit",
    ):
        async def _handler(*_args, **_kwargs):
            return None

        setattr(fake_rc, name, _handler)
    monkeypatch.setitem(sys.modules, "modules.radio_commands", fake_rc)

    registry = importlib.reload(importlib.import_module("modules.radio_command_registry"))
    multi_bot_path = APP / "modules" / "multi_bot.py"
    source = multi_bot_path.read_text()

    for entry in registry.entries():
        for command in (entry.command, *entry.aliases):
            assert f'"{command}": "dj"' in source
    assert "radiohealth" in registry.registry()
    assert registry.command_names() == frozenset(registry.registry().keys())
    assert not registry.find_duplicate_commands()


def test_main_dj_commands_include_radio_registry_aliases():
    main_source = (APP / "main.py").read_text()

    assert "RADIO_REGISTRY_COMMANDS: frozenset[str] = radio_command_names()" in main_source
    assert "DJ_COMMANDS = DJ_COMMANDS | RADIO_REGISTRY_COMMANDS" in main_source


def test_blackjack_setting_handlers_reject_normal_users(monkeypatch, tmp_path):
    _install_env(monkeypatch, tmp_path, bot_mode="blackjack")
    _install_highrise_stub(monkeypatch)
    blackjack = importlib.import_module("modules.blackjack")
    rbj = importlib.import_module("modules.realistic_blackjack")
    monkeypatch.setattr(blackjack, "can_manage_games", lambda _username: False)
    monkeypatch.setattr(rbj, "can_manage_games", lambda _username: False)

    bot = FakeBot()
    user = FakeUser()

    async def run_test():
        await blackjack.handle_bj_set(bot, user, "setbjminbet", ["setbjminbet", "10"])
        await rbj.handle_rbj_set(bot, user, "setrbjminbet", ["setrbjminbet", "10"])

    asyncio.run(run_test())

    assert bot.highrise.whispers == [
        ("user-1", "Manager/admin/owner only."),
        ("user-1", "Manager/admin/owner only."),
    ]


def test_background_loop_startup_guards(monkeypatch, tmp_path):
    _install_env(monkeypatch, tmp_path, bot_mode="host")
    _install_highrise_stub(monkeypatch)
    dmq = importlib.import_module("modules.dm_queue")
    beta = importlib.import_module("modules.beta")
    big = importlib.import_module("modules.big_announce")

    async def assert_guard(func, attr_name):
        monkeypatch.setattr(sys.modules[func.__module__], attr_name, None)
        first = asyncio.create_task(func(FakeBot()))
        await asyncio.sleep(0)
        second = asyncio.create_task(func(FakeBot()))
        await asyncio.sleep(0.05)
        assert second.done()
        assert not first.done()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

    monkeypatch.setattr(beta, "_get", lambda key, default="": "0" if key == "rotating_enabled" else "10")

    async def run_test():
        await assert_guard(dmq.startup_host_dm_queue_loop, "_host_dm_queue_task")
        await assert_guard(beta.rotating_announcement_loop, "_rotating_announcement_task")
        await assert_guard(big.startup_big_announce_reactor, "_big_announce_reactor_task")

    asyncio.run(run_test())


def test_guarded_startup_task_contains_failures(monkeypatch, tmp_path, capsys):
    _install_env(monkeypatch, tmp_path, bot_mode="host")
    startup_tasks = importlib.import_module("modules.startup_tasks")

    async def failing_startup():
        raise RuntimeError("boom")

    async def run_test():
        task = startup_tasks.create_guarded_startup_task(
            failing_startup(),
            "failing_startup",
        )
        assert task.get_name() == "startup:failing_startup"
        await task

    asyncio.run(run_test())

    captured = capsys.readouterr()
    assert "[TASK ERROR] failing_startup failed: RuntimeError('boom')" in captured.out


def test_bot_spawn_restore_retries_until_verified(monkeypatch, tmp_path):
    _install_env(monkeypatch, tmp_path, bot_mode="host")
    _install_highrise_stub(monkeypatch)
    room = importlib.import_module("modules.room_utils")
    gold = importlib.import_module("modules.gold")
    gold.set_bot_identity("bot-1", "DJ_DUDU")

    class Pos:
        def __init__(self, x, y, z, facing="FrontRight"):
            self.x = x
            self.y = y
            self.z = z
            self.facing = facing

    positions = [Pos(0, 0, 0), Pos(9, 0, 9)]
    attempts = []

    async def fake_sleep(_seconds):
        return None

    async def fake_teleport(_bot, **_kwargs):
        attempts.append(len(attempts) + 1)
        return True, Pos(9, 0, 9), {"spawn_name": "custom"}, "teleport"

    async def fake_get_position(_bot, _uid):
        return positions.pop(0)

    monkeypatch.setattr(room.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(room, "teleport_bot_to_saved_spawn", fake_teleport)
    monkeypatch.setattr(room, "_get_bot_position", fake_get_position)

    asyncio.run(room.apply_bot_spawn(FakeBot(), "DJ_DUDU"))

    assert attempts == [1, 2]


def test_bot_spawn_restore_duplicate_task_guard(monkeypatch, tmp_path):
    _install_env(monkeypatch, tmp_path, bot_mode="host")
    _install_highrise_stub(monkeypatch)
    room = importlib.import_module("modules.room_utils")
    gold = importlib.import_module("modules.gold")
    gold.set_bot_identity("bot-1", "DJ_DUDU")
    started = asyncio.Event()
    release = asyncio.Event()
    attempts = []

    class Pos:
        x = 9
        y = 0
        z = 9
        facing = "FrontRight"

    async def fake_sleep(_seconds):
        return None

    async def fake_teleport(_bot, **_kwargs):
        attempts.append(len(attempts) + 1)
        started.set()
        await release.wait()
        return True, Pos(), {"spawn_name": "custom"}, "teleport"

    async def fake_get_position(_bot, _uid):
        return Pos()

    monkeypatch.setattr(room.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(room, "teleport_bot_to_saved_spawn", fake_teleport)
    monkeypatch.setattr(room, "_get_bot_position", fake_get_position)

    async def run_test():
        first = asyncio.create_task(room.apply_bot_spawn(FakeBot(), "DJ_DUDU"))
        await started.wait()
        await room.apply_bot_spawn(FakeBot(), "DJ_DUDU")
        release.set()
        await first

    asyncio.run(run_test())

    assert attempts == [1]


def test_bot_spawn_restore_stops_after_max_attempts(monkeypatch, tmp_path):
    _install_env(monkeypatch, tmp_path, bot_mode="host")
    _install_highrise_stub(monkeypatch)
    room = importlib.import_module("modules.room_utils")
    gold = importlib.import_module("modules.gold")
    gold.set_bot_identity("bot-1", "DJ_DUDU")
    attempts = []

    class Pos:
        def __init__(self, x, y, z, facing="FrontRight"):
            self.x = x
            self.y = y
            self.z = z
            self.facing = facing

    async def fake_sleep(_seconds):
        return None

    async def fake_teleport(_bot, **_kwargs):
        attempts.append(len(attempts) + 1)
        return True, Pos(9, 0, 9), {"spawn_name": "custom"}, "teleport"

    async def fake_get_position(_bot, _uid):
        return Pos(0, 0, 0)

    monkeypatch.setattr(room.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(room, "teleport_bot_to_saved_spawn", fake_teleport)
    monkeypatch.setattr(room, "_get_bot_position", fake_get_position)

    asyncio.run(room.apply_bot_spawn(FakeBot(), "DJ_DUDU"))

    assert attempts == [1, 2, 3]


def test_multibot_supervisor_prevents_duplicate_mode_tasks(monkeypatch, tmp_path):
    _install_env(monkeypatch, tmp_path, bot_mode="host")
    sys.modules.pop("bot", None)
    runner = importlib.import_module("bot")
    runner._bot_supervisor_tasks.clear()
    spec = runner._BotSpec("HOST_BOT_TOKEN", "Host Bot", "token", "host", "host", "HostBot")

    async def run_test():
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_run(_spec, startup_delay=0.0):
            started.set()
            await release.wait()

        monkeypatch.setattr(runner, "_run_bot_forever", fake_run)
        first, first_created = runner._start_bot_supervisor_task(spec)
        await started.wait()
        second, second_created = runner._start_bot_supervisor_task(spec)
        release.set()
        await first
        assert first is second
        assert first_created is True
        assert second_created is False

    try:
        asyncio.run(run_test())
    finally:
        runner._bot_supervisor_tasks.clear()


def test_multibot_supervisor_restarts_finished_task(monkeypatch, tmp_path):
    _install_env(monkeypatch, tmp_path, bot_mode="host")
    sys.modules.pop("bot", None)
    runner = importlib.import_module("bot")
    runner._bot_supervisor_tasks.clear()
    monkeypatch.setattr(runner, "_ENABLE_WEB_DASHBOARD", False)
    monkeypatch.setattr(runner, "_BOT_TASK_RESTART_DELAY", 0.0)
    spec = runner._BotSpec("HOST_BOT_TOKEN", "Host Bot", "token", "host", "host", "HostBot")
    calls = []

    async def run_test():
        restarted = asyncio.Event()
        release = asyncio.Event()

        async def fake_run(_spec, startup_delay=0.0):
            calls.append(startup_delay)
            if len(calls) == 1:
                return
            restarted.set()
            await release.wait()

        monkeypatch.setattr(runner, "_run_bot_forever", fake_run)
        supervisor = asyncio.create_task(runner._run_all([spec]))
        await asyncio.wait_for(restarted.wait(), timeout=1)
        release.set()
        supervisor.cancel()
        await supervisor

    try:
        asyncio.run(run_test())
    finally:
        runner._bot_supervisor_tasks.clear()

    assert len(calls) >= 2
    assert calls[0] == 0.0
    assert calls[1] == 0.0


def test_targeted_emote_uses_keyword_target(monkeypatch, tmp_path, capsys):
    _install_env(monkeypatch, tmp_path, bot_mode="dj")
    targeting = importlib.import_module("modules.emote_targeting")
    gold = importlib.import_module("modules.gold")
    gold.set_bot_identity("bot-999")
    calls = []

    class FakeHighriseEmotes:
        async def send_emote(self, emote_id, target_user_id=None):
            calls.append((emote_id, target_user_id))

    class FakeEmoteBot:
        highrise = FakeHighriseEmotes()

    async def run_test():
        await targeting.send_targeted_emote(
            FakeEmoteBot(),
            "emote-wave",
            "user-123",
            command="wave",
            sender_id="user-123",
        )

    asyncio.run(run_test())

    assert calls == [("emote-wave", "user-123")]
    out = capsys.readouterr().out
    assert "[EMOTE_DEBUG]" not in out
    assert "[EMOTE_TARGET]" not in out


def test_targeted_emote_uses_runtime_signature(monkeypatch, tmp_path):
    _install_env(monkeypatch, tmp_path, bot_mode="dj")
    targeting = importlib.import_module("modules.emote_targeting")
    calls = []

    class FakeHighriseEmotes:
        async def send_emote(self, emote_id, user_id=None):
            calls.append((emote_id, user_id))

    class FakeEmoteBot:
        highrise = FakeHighriseEmotes()

    async def run_test():
        await targeting.send_targeted_emote(
            FakeEmoteBot(),
            "emote-wave",
            "user-456",
            command="wave",
            sender_id="user-456",
        )

    asyncio.run(run_test())

    assert calls == [("emote-wave", "user-456")]


def test_host_dm_queue_rows_are_claimed_once(monkeypatch, tmp_path):
    _install_env(monkeypatch, tmp_path, bot_mode="host")
    dmq = importlib.import_module("modules.dm_queue")
    db_path = tmp_path / "dm_queue.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE host_dm_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT, username TEXT, conversation_id TEXT,
            dm_type TEXT, category TEXT, message TEXT,
            status TEXT, created_at TEXT, sent_at TEXT, error TEXT
        )"""
    )
    conn.execute(
        """INSERT INTO host_dm_queue
           (user_id, username, conversation_id, dm_type, category, message, status, created_at)
           VALUES ('u1', 'alice', 'conv1', 'test', 'cat', 'hello', 'pending', datetime('now'))"""
    )
    conn.commit()
    conn.close()

    def get_connection():
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        return con

    sent = []

    async def fake_send(_bot, conv_id, message, dm_type="direct"):
        sent.append((conv_id, message, dm_type))
        return True

    monkeypatch.setattr(dmq.db, "get_connection", get_connection)
    monkeypatch.setattr(dmq, "send_host_dm", fake_send)

    assert dmq._claim_queue_row(1) is True
    assert dmq._claim_queue_row(1) is False
    with get_connection() as con:
        con.execute("UPDATE host_dm_queue SET status='pending'")
        con.commit()

    asyncio.run(dmq.process_host_dm_queue(FakeBot()))
    assert sent == [("conv1", "hello", "test")]
    with get_connection() as con:
        row = con.execute("SELECT status FROM host_dm_queue WHERE id=1").fetchone()
    assert row["status"] == "sent"


def test_azura_request_cleanup_filename_guard(monkeypatch, tmp_path):
    _install_env(monkeypatch, tmp_path)
    azura = importlib.import_module("modules.azuracast_controller")

    for filename in ("song.mp3", "tmp_replay_1_song.mp3", "local_request_7_track.mp3"):
        assert azura._safe_request_basename(filename)

    for filename in ("", ".", "..", "../song.mp3", "Requests/song.mp3", "folder/song.mp3", "folder\\song.mp3"):
        assert not azura._safe_request_basename(filename)

    assert azura.sftp_delete_file("../song.mp3") is False
    assert azura.sftp_move_to_played("Requests/song.mp3") is False


def test_permission_alias_helpers_delegate_to_role_hierarchy(monkeypatch, tmp_path):
    _install_env(monkeypatch, tmp_path)
    permissions = importlib.import_module("modules.permissions")

    monkeypatch.setattr(permissions, "is_admin", lambda username: username == "admin")
    monkeypatch.setattr(permissions, "is_manager", lambda username: username == "manager")
    monkeypatch.setattr(permissions, "can_moderate", lambda username: username == "staff")

    assert permissions.is_admin_or_owner("admin") is True
    assert permissions.is_admin_or_owner("player") is False
    assert permissions.is_manager_or_higher("manager") is True
    assert permissions.is_manager_or_higher("player") is False
    assert permissions.is_staff("staff") is True
    assert permissions.is_staff("player") is False
