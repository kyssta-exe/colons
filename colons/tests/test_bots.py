"""Tests for bots (roster), bot-to-bot messaging, and peers."""
import asyncio
import json

import pytest

from colons_core.bots import SILENCE_TOKENS, Bot, BotMessenger, BotStore, is_silence
from colons_core.bots.messenger import PeerError  # noqa: F401  (import check)
from colons_core.agents import AgentHarness
from colons_core.memory import EmbeddingService

from tests.conftest import ScriptedProvider


# --------------------------------------------------------------------------- #
# Bot model + store
# --------------------------------------------------------------------------- #

def test_bot_handle_and_slug():
    bot = Bot(name="Research Buddy", title="Deep Researcher")
    assert bot.id == "research-buddy"
    assert bot.handle == "@research-buddy"
    assert "@research-buddy" in bot.aliases()
    assert "research buddy" in bot.aliases()


def test_bot_explicit_id_wins():
    bot = Bot(id="researcher", name="Research Buddy")
    assert bot.handle == "@researcher"


@pytest.fixture
def bot_store(tmp_path):
    return BotStore(str(tmp_path / "bots.db"))


def test_bot_crud(bot_store):
    bot = bot_store.save(Bot(name="Researcher", title="Finds sources"))
    assert bot_store.get(bot.id).name == "Researcher"
    assert bot_store.count() == 1

    bot.title = "Deep Researcher"
    bot_store.save(bot)
    assert bot_store.get(bot.id).title == "Deep Researcher"

    assert bot_store.delete(bot.id) is True
    assert bot_store.get(bot.id) is None
    assert bot_store.count() == 0


def test_bot_persistence_across_instances(tmp_path):
    path = str(tmp_path / "p.db")
    BotStore(path).save(Bot(id="a", name="Alpha"))
    assert BotStore(path).get("a").name == "Alpha"


def test_bot_list_filters(bot_store):
    bot_store.save(Bot(id="a", name="A", owner="alice"))
    bot_store.save(Bot(id="b", name="B", owner="bob"))
    assert len(bot_store.list()) == 2
    assert len(bot_store.list(owner="alice")) == 1
    bot_store.save(Bot(id="c", name="C", owner="alice", enabled=False))
    assert len(bot_store.list(owner="alice")) == 2
    assert len(bot_store.list(owner="alice", enabled_only=True)) == 1


def test_resolve_by_id_name_handle_title(bot_store):
    bot_store.save(Bot(id="researcher", name="Research Buddy", title="Deep Researcher"))
    assert bot_store.resolve("researcher").id == "researcher"
    assert bot_store.resolve("@researcher").id == "researcher"
    assert bot_store.resolve("Research Buddy").id == "researcher"
    assert bot_store.resolve("deep-researcher").id == "researcher"


def test_resolve_unknown_and_ambiguous(bot_store):
    bot_store.save(Bot(id="a", name="Alpha"))
    bot_store.save(Bot(id="b", name="Beta"))
    with pytest.raises(KeyError) as e:
        bot_store.resolve("nobody")
    assert "Roster" in str(e.value)

    # Two bots sharing an alias -> ambiguous
    bot_store.save(Bot(id="c", name="Alpha", title=""))
    with pytest.raises(KeyError) as e:
        bot_store.resolve("alpha")
    assert "Ambiguous" in str(e.value)


# --------------------------------------------------------------------------- #
# Silence tokens
# --------------------------------------------------------------------------- #

def test_silence_tokens():
    for token in ("[SILENT]", "no_reply", "NO_REPLY", "<silent>", "  [silent]  ",
                  "*[SILENT]*", "no reply", "[no-reply]"):
        assert is_silence(token), token


def test_not_silence():
    for text in ("Here is the answer", "[SILENT] because I checked",
                 "I'll stay silent"):
        assert not is_silence(text), text
    # Empty output counts as "nothing to add"
    assert is_silence("")
    assert is_silence(None)


# --------------------------------------------------------------------------- #
# Messenger
# --------------------------------------------------------------------------- #

class FakeManager:
    def __init__(self):
        self.deliveries = []
        self.reply_map = {}

    async def _deliver(self, sender_id, target_id, message, timeout):
        self.deliveries.append((sender_id, target_id, message))
        reply = self.reply_map.get(target_id, f"reply from {target_id}")
        if reply is None:
            raise TimeoutError("busy")
        return {"status": "ok", "reply": reply, "session_id": "bot-chat"}


@pytest.fixture
def messenger(bot_store):
    fake = FakeManager()
    m = BotMessenger(bot_store, fake._deliver, peers={}, default_timeout=5)
    return m, fake


@pytest.mark.asyncio
async def test_send_local_dm_attributed(bot_store, messenger):
    m, fake = messenger
    bot_store.save(Bot(id="alice", name="Alice"))
    bot_store.save(Bot(id="bob", name="Bob"))
    sender = bot_store.get("alice")

    result = await m.send(sender, "@bob", "Can you check the deploy?")
    assert result["status"] == "ok"
    assert result["reply"] == "reply from bob"

    _, target, message = fake.deliveries[0]
    assert target == "bob"
    assert message.startswith("Message from 🤖 Alice (@alice):")
    assert "Can you check the deploy?" in message


@pytest.mark.asyncio
async def test_send_silence_becomes_empty_reply(bot_store, messenger):
    m, fake = messenger
    bot_store.save(Bot(id="alice", name="Alice"))
    bot_store.save(Bot(id="bob", name="Bob"))
    fake.reply_map["bob"] = "[SILENT]"

    result = await m.send(bot_store.get("alice"), "bob", "fyi")
    assert result["status"] == "ok"
    assert result["reply"] == ""


@pytest.mark.asyncio
async def test_send_errors(bot_store, messenger):
    m, _ = messenger
    bot_store.save(Bot(id="alice", name="Alice"))

    result = await m.send(bot_store.get("alice"), "ghost", "hello")
    assert result["status"] == "error"
    assert result["reason"] == "unknown_target"

    result = await m.send(bot_store.get("alice"), "alice", "hello")
    assert result["status"] == "error"
    assert result["reason"] == "self_target"

    result = await m.send(bot_store.get("alice"), "", "hello")
    assert result["status"] == "error"
    assert result["reason"] == "invalid_target"


@pytest.mark.asyncio
async def test_send_timeout_queues(bot_store, messenger):
    m, fake = messenger
    bot_store.save(Bot(id="alice", name="Alice"))
    bot_store.save(Bot(id="bob", name="Bob"))
    fake.reply_map["bob"] = None  # raises TimeoutError

    result = await m.send(bot_store.get("alice"), "bob", "you there?")
    assert result["status"] == "queued"


def test_roster_block_lists_teammates(bot_store):
    bot_store.save(Bot(id="alice", name="Alice", title="Writer"))
    bot_store.save(Bot(id="bob", name="Bob", title="Reviewer"))
    m = BotMessenger(bot_store, _never, peers={"spark": {"url": "http://x", "agents": ["researcher"]}})
    block = m.roster_block("alice")
    assert "@bob" in block
    assert "Reviewer" in block
    assert "@alice" not in block
    assert "@spark/researcher" in block


async def _never(*args, **kwargs):
    raise RuntimeError("not used")


# --------------------------------------------------------------------------- #
# Harness integration: message_agent tool
# --------------------------------------------------------------------------- #

def make_agent(provider, store, cache, **kwargs):
    return AgentHarness(
        provider=provider,
        store=store,
        cache=cache,
        embeddings=EmbeddingService(provider=provider, cache=cache, dim=32),
        agent_id="test-agent",
        user_id="tester",
        name="Test",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_message_agent_tool_registered_and_works(provider, store, cache):
    calls = []

    async def router(target, message):
        calls.append((target, message))
        if not target and not message:
            return {"roster": "- @bob (Reviewer)"}
        return {"status": "ok", "reply": "deploy looks good", "target": target}

    agent = make_agent(provider, store, cache,
                       peer_router=router,
                       peer_roster="## Teammates\n- @bob (Reviewer)")

    assert "message_agent" in agent.registry
    assert "list_teammates" in agent.registry
    assert "@bob" in agent.system_prompt

    result = await agent.tool_manager.execute(
        "message_agent", {"target": "@bob", "message": "check deploy"}, user_requested=True,
    )
    assert result.success
    assert result.output == "deploy looks good"
    assert calls[-1] == ("@bob", "check deploy")

    result = await agent.tool_manager.execute("list_teammates", {}, user_requested=True)
    assert result.output == "- @bob (Reviewer)"


@pytest.mark.asyncio
async def test_message_agent_tool_error_and_queued(provider, store, cache):
    async def router(target, message):
        if target == "ghost":
            return {"status": "error", "message": "Unknown bot"}
        return {"status": "queued", "target": target}

    agent = make_agent(provider, store, cache, peer_router=router)

    result = await agent.tool_manager.execute(
        "message_agent", {"target": "ghost", "message": "hi"}, user_requested=True)
    assert "ERROR: Unknown bot" in result.output

    result = await agent.tool_manager.execute(
        "message_agent", {"target": "bob", "message": "hi"}, user_requested=True)
    assert "queued" in result.output


@pytest.mark.asyncio
async def test_no_peer_tools_without_router(provider, store, cache):
    agent = make_agent(provider, store, cache)
    assert "message_agent" not in agent.registry


def test_set_peer_roster_replaces_section(provider, store, cache):
    agent = make_agent(provider, store, cache,
                       peer_roster="## Teammates\n- @bob (Reviewer)")
    agent.set_peer_roster("## Teammates\n- @carol (Editor)")
    assert "@carol" in agent.system_prompt
    assert "@bob" not in agent.system_prompt


# --------------------------------------------------------------------------- #
# Peer delivery over HTTP
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_peer_http_delivery(bot_store):
    import uvicorn
    from fastapi import FastAPI, Request

    received = {}

    app = FastAPI()

    @app.post("/api/peer/message")
    async def inbound(request: Request):
        received.update(await request.json())
        auth = request.headers.get("authorization", "")
        return {"status": "ok", "reply": f"hello from peer (auth={auth})", "bot": "default"}

    config = uvicorn.Config(app, host="127.0.0.1", port=9961, log_level="error")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(1.2)

    try:
        messenger = BotMessenger(
            bot_store, _never,
            peers={"spark": {"url": "http://127.0.0.1:9961", "api_key": "peer-key"}},
        )
        bot_store.save(Bot(id="alice", name="Alice"))

        result = await messenger.send(bot_store.get("alice"), "spark/researcher",
                                      "any updates?")
        assert result["status"] == "ok"
        assert "hello from peer" in result["reply"]
        assert "Bearer peer-key" in result["reply"]  # key was sent
        assert received["target"] == "researcher"
        assert received["message"] == "any updates?"
        assert received["sender"]["id"] == "alice"
    finally:
        server.should_exit = True
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_peer_unreachable_and_missing_config(bot_store):
    bot_store.save(Bot(id="alice", name="Alice"))
    messenger = BotMessenger(bot_store, _never, peers={
        "dead": {"url": "http://127.0.0.1:59999"},
        "nourl": {},
    })
    result = await messenger.send(bot_store.get("alice"), "dead/agent", "hi")
    assert result["status"] == "error"
    assert result["reason"] == "peer_unreachable"

    result = await messenger.send(bot_store.get("alice"), "nourl", "hi")
    assert result["status"] == "error"
    assert result["reason"] == "peer_not_configured"
