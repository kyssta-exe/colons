"""Command lifecycle, persistent setup, and full-screen terminal regressions."""

import asyncio
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from colons_cli import configuration
from colons_cli import main as entry
from colons_cli.main import build_parser
from colons_cli.runtime import managed_server, server_ready
from colons_cli.tui import ApprovalScreen, ColonsTUI
from textual.widgets import Input, Markdown


@pytest.fixture
def profile(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setenv("COLONS_CONFIG", str(path))
    monkeypatch.setenv("COLONS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("COLONS_WORKSPACE", str(tmp_path / "workspace"))
    return path


def test_provider_setup_preserves_other_sections_and_writes_private_file(profile, monkeypatch):
    configuration.save_config(profile, {"voice": {"voice": "my-voice"}, "provider": {"name": "custom"}})
    answers = iter(["custom", "https://models.example/v1", "my-model"])
    monkeypatch.setattr(configuration.Prompt, "ask", lambda *a, **k: next(answers))
    monkeypatch.setattr(configuration.getpass, "getpass", lambda *a: "private-provider-key")
    configuration.setup("provider", profile)
    data = json.loads(profile.read_text())
    assert data["provider"]["model"] == "my-model"
    assert data["provider"]["api_key"] == "private-provider-key"
    assert data["voice"]["voice"] == "my-voice"
    assert profile.stat().st_mode & 0o777 == 0o600


def test_messaging_setup_updates_the_shared_private_settings(profile, monkeypatch):
    answers = iter(["telegram", "123,456", "789"])
    monkeypatch.setattr(configuration.Prompt, "ask", lambda *a, **k: next(answers))
    monkeypatch.setattr(configuration.Confirm, "ask", lambda *a, **k: True)
    monkeypatch.setattr(configuration.getpass, "getpass", lambda *a: "bot-secret")
    configuration.setup("messaging", profile)
    cfg = configuration.ColonsConfig.load(str(profile))
    settings = configuration.MessagingSettings(cfg)
    assert cfg.messaging.telegram["bot_token"] == "bot-secret"
    assert cfg.messaging.telegram["allowed_users"] == ["123", "456"]
    assert settings.path.stat().st_mode & 0o777 == 0o600


def test_permissions_setup_uses_the_same_policy_as_web(profile, monkeypatch):
    answers = iter(["approval", "/tmp/colons-test-workspace"])
    monkeypatch.setattr(configuration.Prompt, "ask", lambda *a, **k: next(answers))
    configuration.setup("permissions", profile)
    cfg = configuration.ColonsConfig.load(str(profile))
    assert configuration.PermissionSettings(cfg).mode == "approval"


def test_start_and_serve_dispatch_configured_address(profile, monkeypatch):
    import colons_cli.main as cli_main
    configuration.save_config(profile, {"server": {"host": "127.0.0.1", "port": 8123}})
    called = []
    monkeypatch.setattr(cli_main, "run_server", lambda *args: called.append(args))
    entry(["start"])
    entry(["serve", "--port", "8124"])
    assert called == [("127.0.0.1", 8123, False), ("127.0.0.1", 8124, False)]
    assert build_parser().parse_args(["setup", "messaging"]).section == "messaging"


def test_managed_server_leaves_existing_server_alone(monkeypatch):
    import colons_cli.runtime as runtime
    monkeypatch.setattr(runtime, "server_ready", lambda url: True)
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **k: pytest.fail("Must reuse server"))
    with managed_server("http://127.0.0.1:8000", SimpleNamespace()) as child:
        assert child is None


def test_managed_server_starts_and_stops_only_its_local_process(profile):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    data = Path(profile.parent / "data")
    configuration.save_config(profile, {"data_dir": str(data),
        "memory": {"store_url": str(data / "memory.db")},
        "tools": {"workspace": str(profile.parent / "workspace")},
        "agent": {"proactive": False}, "scheduler": {"enabled": False}})
    _, cfg = configuration.ensure_profile(profile)
    url = f"http://127.0.0.1:{port}"
    with managed_server(url, cfg) as child:
        assert child is not None
        assert server_ready(url)
    assert child.poll() is not None
    assert not server_ready(url)


class TerminalClient:
    base_url = "http://127.0.0.1:8000"

    def __init__(self, approval=False):
        self.approval = approval
        self.approved = None
        self.closed = False

    async def health(self):
        return {"provider": "test"}

    async def list_bots(self):
        return [{"name": "Researcher", "agent_id": "bot-researcher"}]

    async def list_sessions(self, *args):
        return []

    async def chat_stream(self, message, session_id, user_id, agent_id, approval_handler=None):
        yield {"type": "start", "session_id": "session1"}
        if self.approval:
            self.approved = await approval_handler({"tool": "write_file", "arguments": {"path": "example.txt"}})
        yield {"type": "delta", "content": "Hello from Colons"}
        yield {"type": "done", "session_id": "session1"}

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_terminal_streaming_new_chat_and_sidebar():
    client = TerminalClient()
    app = ColonsTUI(client)
    async with app.run_test(size=(100, 32)) as pilot:
        await app.workers.wait_for_complete()
        app.query_one("#message", Input).value = "Hello"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        assert app.session_id == "session1"
        assert not app.busy
        assert any(widget._markdown == "Hello from Colons" for widget in app.query(Markdown))
        await pilot.press("ctrl+b")
        assert not app.query_one("#sidebar").display
        await pilot.press("ctrl+n")
        assert app.session_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("allow", [True, False])
async def test_terminal_approval_requires_an_explicit_choice(allow):
    client = TerminalClient(approval=True)
    app = ColonsTUI(client)
    async with app.run_test(size=(100, 32)) as pilot:
        await app.workers.wait_for_complete()
        app.query_one("#message", Input).value = "Write a file"
        await pilot.press("enter")
        for _ in range(50):
            if isinstance(app.screen, ApprovalScreen):
                break
            await asyncio.sleep(0.01)
        assert isinstance(app.screen, ApprovalScreen)
        assert client.approved is None
        await pilot.click("#allow" if allow else "#deny")
        await app.workers.wait_for_complete()
        assert client.approved is allow
