"""Tests for messaging: chunking, markdown conversion, adapters, and the bridge."""
import asyncio

import pytest

from colons_core.messaging import (
    BaseMessagingAdapter,
    IncomingMessage,
    MessagingBridge,
    WebhookAdapter,
    chunk_text,
    markdown_to_telegram_html,
    strip_markdown,
)
from colons_core.messaging.discord import DiscordAdapter
from colons_core.messaging.slack import SlackAdapter
from colons_core.messaging.telegram import TelegramAdapter


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def test_chunk_text_short():
    assert chunk_text("hello", 100) == ["hello"]


def test_chunk_text_splits_on_paragraphs():
    text = "para one\n\n" + ("x" * 50) + "\n\n" + ("y" * 50)
    chunks = chunk_text(text, 60)
    assert all(len(c) <= 60 for c in chunks)
    assert len(chunks) >= 2
    assert "".join(chunks).replace("\n", "").replace(" ", "").count("x") == 50


def test_chunk_text_no_data_loss():
    text = "word " * 500
    chunks = chunk_text(text, 100)
    # No characters are lost (whitespace at boundaries is trimmed intentionally)
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")


def test_chunk_text_long_word_hard_split():
    text = "z" * 250
    chunks = chunk_text(text, 100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks) == text


def test_markdown_to_telegram_html_bold_and_code():
    html = markdown_to_telegram_html("**bold** and `code`")
    assert "<b>bold</b>" in html
    assert "<code>code</code>" in html


def test_markdown_to_telegram_html_code_block():
    html = markdown_to_telegram_html("```python\nprint('hi')\n```")
    assert "<pre><code" in html
    assert "print(" in html
    # code must be escaped inside the block
    assert "&lt;" not in html or "print" in html


def test_markdown_to_telegram_html_escapes_user_html():
    html = markdown_to_telegram_html("danger <script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_markdown_to_telegram_html_links():
    html = markdown_to_telegram_html("see [docs](https://example.com)")
    assert '<a href="https://example.com">docs</a>' in html


def test_strip_markdown():
    stripped = strip_markdown("**bold** and `code` and [link](http://x)")
    assert stripped == "bold and code and link (http://x)"


# --------------------------------------------------------------------------- #
# Adapters (unit level, no network)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_telegram_requires_token():
    adapter = TelegramAdapter({}, lambda m: None)
    assert not adapter.is_configured()
    with pytest.raises(ValueError):
        await adapter.start()


def test_discord_requires_token():
    adapter = DiscordAdapter({}, lambda m: None)
    assert not adapter.is_configured()


def test_slack_config_detection():
    assert not SlackAdapter({}, lambda m: None).is_configured()
    assert SlackAdapter({"webhook_url": "https://hooks.slack.com/x"}, lambda m: None).is_configured()
    assert SlackAdapter({"bot_token": "xoxb-1"}, lambda m: None).is_configured()


def test_adapter_allowlist():
    adapter = WebhookAdapter("test", {"allowed_users": ["u1"]}, lambda m: None)
    assert adapter.allowed("u1", "any")
    assert not adapter.allowed("u2", "any")


def test_telegram_markdown_length_limit():
    assert TelegramAdapter.max_message_length <= 4096
    assert DiscordAdapter.max_message_length <= 2000


# --------------------------------------------------------------------------- #
# Slack signature verification
# --------------------------------------------------------------------------- #

def test_slack_signature_verification():
    import hashlib
    import hmac
    import time

    secret = "shhh"
    adapter = SlackAdapter({"signing_secret": secret}, lambda m: None)
    body = b'{"type":"event_callback"}'
    ts = str(int(time.time()))
    expected = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:{body.decode()}".encode(),
                                hashlib.sha256).hexdigest()

    assert adapter.verify_signature(body, ts, expected)
    assert not adapter.verify_signature(body, ts, "v0=wrong")
    # Stale timestamps rejected
    old_ts = str(int(time.time()) - 4000)
    old_sig = "v0=" + hmac.new(secret.encode(), f"v0:{old_ts}:{body.decode()}".encode(),
                               hashlib.sha256).hexdigest()
    assert not adapter.verify_signature(body, old_ts, old_sig)


def test_slack_no_secret_accepts_all():
    adapter = SlackAdapter({}, lambda m: None)
    assert adapter.verify_signature(b"{}", "0", "anything")


# --------------------------------------------------------------------------- #
# Bridge routing (with a fake adapter and a fake agent manager)
# --------------------------------------------------------------------------- #

class FakeAdapter(BaseMessagingAdapter):
    name = "fake"

    def __init__(self, config=None, on_message=None):
        super().__init__(config or {"enabled": True}, on_message or (lambda m: None))
        self.sent = []
        self.typing_calls = 0

    async def start(self):
        self.connected = True

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return True

    async def send_typing(self, chat_id):
        self.typing_calls += 1


class FakeAgent:
    name = "Fake"

    def __init__(self):
        self.chats = []
        self.deleted_sessions = []
        self.started = False

    async def start(self):
        self.started = True

    def delete_session(self, session_id):
        self.deleted_sessions.append(session_id)
        return True

    async def chat(self, message, session_id=None, stream=False, **kwargs):
        self.chats.append((message, session_id))
        yield {"type": "delta", "content": "hello "}
        yield {"type": "delta", "content": "world"}
        yield {"type": "done", "content": "hello world", "session_id": session_id}

    def status(self):
        return {"state": "idle", "model": "fake-model", "provider": "fake",
                "tools": 21, "memory_entries": 3,
                "usage": {"total_tokens": 1234}}


class FakeManager:
    def __init__(self):
        self.agent = FakeAgent()
        self.requested = []

    def get_or_create(self, user_id="default", agent_id=None):
        self.requested.append(user_id)
        return self.agent


class FakeMessagingConfig:
    telegram = {}
    discord = {}
    slack = {}
    webhooks = {}
    user_prefix = "msg"


class FakeConfig:
    messaging = FakeMessagingConfig()


@pytest.fixture
def bridge():
    manager = FakeManager()
    b = MessagingBridge(manager, FakeConfig())
    b.adapters["fake"] = FakeAdapter(on_message=b.handle_incoming)
    return b


@pytest.mark.asyncio
async def test_bridge_routes_message_to_agent_and_replies(bridge):
    incoming = IncomingMessage(adapter="fake", chat_id="chat1", user_id="user1",
                               text="what's up?", user_name="Tester")
    await bridge.handle_incoming(incoming)

    agent = bridge.manager.agent
    assert agent.started
    assert agent.chats[0][0] == "what's up?"
    assert agent.chats[0][1] == "msg-fake-chat1"          # session key
    assert bridge.manager.requested == ["msg-fake-user1"] # user mapping
    adapter = bridge.adapters["fake"]
    assert adapter.sent[-1] == ("chat1", "hello world")


@pytest.mark.asyncio
async def test_bridge_new_command_resets_session(bridge):
    incoming = IncomingMessage(adapter="fake", chat_id="c", user_id="u", text="/new")
    await bridge.handle_incoming(incoming)
    assert bridge.manager.agent.deleted_sessions == ["msg-fake-c"]
    assert "fresh" in bridge.adapters["fake"].sent[-1][1].lower()


@pytest.mark.asyncio
async def test_bridge_status_command(bridge):
    incoming = IncomingMessage(adapter="fake", chat_id="c", user_id="u", text="/status")
    await bridge.handle_incoming(incoming)
    reply = bridge.adapters["fake"].sent[-1][1]
    assert "fake-model" in reply
    assert "1,234" in reply


@pytest.mark.asyncio
async def test_bridge_help_command(bridge):
    incoming = IncomingMessage(adapter="fake", chat_id="c", user_id="u", text="/help")
    await bridge.handle_incoming(incoming)
    reply = bridge.adapters["fake"].sent[-1][1]
    assert "/new" in reply


@pytest.mark.asyncio
async def test_bridge_notify(bridge):
    ok = await bridge.notify("fake", "chat9", "heads up")
    assert ok
    assert bridge.adapters["fake"].sent[-1] == ("chat9", "heads up")


@pytest.mark.asyncio
async def test_webhook_adapter_roundtrip():
    received = []

    async def on_message(msg):
        received.append(msg)

    adapter = WebhookAdapter("generic", {"incoming_token": "tok"}, on_message)
    await adapter.start()

    ok = await adapter.handle_event({"text": "ping", "user_id": "u1", "chat_id": "c1"}, token="tok")
    assert ok
    assert received[0].text == "ping"
    assert received[0].adapter == "webhook:generic"

    with pytest.raises(PermissionError):
        await adapter.handle_event({"text": "nope"}, token="wrong")

    await adapter.stop()


@pytest.mark.asyncio
async def test_webhook_ignores_empty_text():
    adapter = WebhookAdapter("empty", {}, lambda m: None)
    await adapter.start()
    assert await adapter.handle_event({"text": ""}) is False
    await adapter.stop()


@pytest.mark.asyncio
async def test_bridge_typing_stops_after_reply(bridge):
    """The typing indicator task must be cancelled once the reply is sent."""
    adapter = bridge.adapters["fake"]
    incoming = IncomingMessage(adapter="fake", chat_id="c", user_id="u", text="hello")
    await bridge.handle_incoming(incoming)

    calls_after_reply = adapter.typing_calls
    await asyncio.sleep(0.2)  # typing interval far below this? loop is 5s, but task must be dead
    # Give the event loop a chance to run any lingering task
    await asyncio.sleep(0.05)
    assert adapter.typing_calls == calls_after_reply


def test_telegram_offset_persistence(tmp_path):
    from colons_core.messaging.telegram import TelegramAdapter

    config = {"bot_token": "t", "state_dir": str(tmp_path)}
    adapter = TelegramAdapter(config, lambda m: None)
    adapter.offset = 42
    adapter._save_offset()

    adapter2 = TelegramAdapter(config, lambda m: None)
    adapter2._load_offset()
    assert adapter2.offset == 42

    # No state dir -> no crash, offset stays None
    adapter3 = TelegramAdapter({"bot_token": "t"}, lambda m: None)
    adapter3._save_offset()
    adapter3._load_offset()
    assert adapter3.offset is None


def test_telegram_offset_file_is_json(tmp_path):
    import json as _json

    from colons_core.messaging.telegram import TelegramAdapter

    config = {"bot_token": "t", "state_dir": str(tmp_path)}
    adapter = TelegramAdapter(config, lambda m: None)
    adapter.offset = 7
    adapter._save_offset()
    data = _json.loads((tmp_path / "telegram_offset.json").read_text())
    assert data == {"offset": 7}


@pytest.mark.asyncio
async def test_bridge_start_handles_adapter_failures():
    """A broken adapter should not prevent others from starting."""
    manager = FakeManager()
    b = MessagingBridge(manager, FakeConfig())

    class BrokenAdapter(BaseMessagingAdapter):
        name = "broken"

        def __init__(self):
            super().__init__({"enabled": True}, lambda m: None)

        async def start(self):
            raise RuntimeError("no credentials")

        async def send_message(self, chat_id, text, **kwargs):
            return False

    good = FakeAdapter(on_message=b.handle_incoming)
    broken = BrokenAdapter()
    b.adapters = {"fake": good, "broken": broken}
    b.build_adapters = lambda: b.adapters  # keep the injected adapters

    await b.start()
    assert good.connected
    assert not broken.connected
    assert "no credentials" in broken.last_error
