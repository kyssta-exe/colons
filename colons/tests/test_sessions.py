"""Tests for persistent session storage and harness session resumption."""
import asyncio

import pytest

from colons_core.agents import AgentHarness
from colons_core.agents.session_store import SessionMeta, SessionStore
from colons_core.memory import EmbeddingService
from colons_core.providers.base import Message

from tests.conftest import ScriptedProvider


# --------------------------------------------------------------------------- #
# SessionStore unit tests
# --------------------------------------------------------------------------- #

@pytest.fixture
def session_store(tmp_path):
    return SessionStore(str(tmp_path / "sessions.db"))


def test_session_roundtrip(session_store):
    session_store.save(SessionMeta(id="s1", agent_id="a1", user_id="u1", title="Hello"))
    meta = session_store.get_meta("s1")
    assert meta is not None
    assert meta.title == "Hello"
    assert meta.user_id == "u1"


def test_session_upsert(session_store):
    session_store.save(SessionMeta(id="s1", title="First"))
    session_store.save(SessionMeta(id="s1", title="Renamed"))
    assert session_store.get_meta("s1").title == "Renamed"
    assert session_store.count() == 1


def test_session_listing_and_filters(session_store):
    session_store.save(SessionMeta(id="s1", user_id="alice", agent_id="a1"))
    session_store.save(SessionMeta(id="s2", user_id="alice", agent_id="a2"))
    session_store.save(SessionMeta(id="s3", user_id="bob", agent_id="a1"))

    assert len(session_store.list()) == 3
    assert len(session_store.list(user_id="alice")) == 2
    assert len(session_store.list(user_id="alice", agent_id="a1")) == 1
    assert session_store.list(user_id="alice", agent_id="a1")[0].id == "s1"


def test_message_append_and_load(session_store):
    session_store.save(SessionMeta(id="s1"))
    session_store.append_message("s1", Message(role="user", content="hello"))
    session_store.append_message("s1", Message(role="assistant", content="hi there",
                                                tool_calls=[{"id": "x", "type": "function",
                                                             "function": {"name": "f", "arguments": "{}"}}]))

    messages = session_store.messages("s1")
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[1].tool_calls[0]["function"]["name"] == "f"


def test_message_pruning(session_store, tmp_path):
    store = SessionStore(str(tmp_path / "pruned.db"), max_messages_per_session=5)
    store.save(SessionMeta(id="s1"))
    for i in range(20):
        store.append_message("s1", Message(role="user", content=f"m{i}"))
    messages = store.messages("s1")
    assert len(messages) == 5
    # Newest messages are kept
    assert messages[-1].content == "m19"
    assert messages[0].content == "m15"


def test_session_delete_removes_messages(session_store):
    session_store.save(SessionMeta(id="s1"))
    session_store.append_message("s1", Message(role="user", content="bye"))
    assert session_store.delete("s1") is True
    assert session_store.get_meta("s1") is None
    assert session_store.messages("s1") == []
    assert session_store.count_messages("s1") == 0


def test_touch_updates_title_and_timestamp(session_store):
    import time
    session_store.save(SessionMeta(id="s1", title="Old"))
    before = session_store.get_meta("s1").updated_at
    time.sleep(0.01)
    session_store.touch("s1", title="New")
    meta = session_store.get_meta("s1")
    assert meta.title == "New"
    assert meta.updated_at > before


def test_persistence_across_instances(tmp_path):
    path = str(tmp_path / "persist.db")
    store1 = SessionStore(path)
    store1.save(SessionMeta(id="s1", title="Persistent"))
    store1.append_message("s1", Message(role="user", content="remember me"))

    store2 = SessionStore(path)
    assert store2.get_meta("s1").title == "Persistent"
    assert store2.messages("s1")[0].content == "remember me"


# --------------------------------------------------------------------------- #
# Harness integration
# --------------------------------------------------------------------------- #

def make_agent(provider, store, cache, session_store=None, **kwargs):
    return AgentHarness(
        provider=provider,
        store=store,
        cache=cache,
        embeddings=EmbeddingService(provider=provider, cache=cache, dim=32),
        agent_id="test-agent",
        user_id="tester",
        name="Test",
        session_store=session_store,
        **kwargs,
    )


async def collect(agent, message, **kwargs):
    return [ev async for ev in agent.chat(message, **kwargs)]


@pytest.mark.asyncio
async def test_harness_persists_session_and_messages(provider, store, cache, tmp_path):
    session_store = SessionStore(str(tmp_path / "s.db"))
    provider.script = [{"content": "first answer"}]
    agent = make_agent(provider, store, cache, session_store=session_store)

    events = await collect(agent, "first question")
    session_id = next(e["session_id"] for e in events if e["type"] == "start")

    # Stored on disk immediately
    assert session_store.get_meta(session_id) is not None
    messages = session_store.messages(session_id)
    assert [m.content for m in messages] == ["first question", "first answer"]


@pytest.mark.asyncio
async def test_harness_resumes_session_after_restart(provider, store, cache, tmp_path):
    path = str(tmp_path / "resume.db")
    provider.script = [{"content": "answer one"}]
    agent1 = make_agent(provider, store, cache, session_store=SessionStore(path))
    events = await collect(agent1, "question one")
    session_id = next(e["session_id"] for e in events if e["type"] == "start")
    session_title = agent1.get_session(session_id).title

    # Simulate a restart: brand new store + harness sharing the same file
    provider2 = ScriptedProvider([{"content": "answer two"}])
    agent2 = make_agent(provider2, store, cache, session_store=SessionStore(path))

    # Session listing sees the old session with its message count
    listed = agent2.list_sessions()
    assert any(s["id"] == session_id and s["message_count"] == 2 for s in listed)

    # Loading the session restores messages
    restored = agent2.get_session(session_id)
    assert restored is not None
    assert restored.title == session_title
    assert [m.content for m in restored.messages] == ["question one", "answer one"]

    # Continuing the conversation appends to the same stored session
    await collect(agent2, "question two", session_id=session_id)
    stored = SessionStore(path).messages(session_id)
    assert [m.content for m in stored] == ["question one", "answer one",
                                           "question two", "answer two"]


@pytest.mark.asyncio
async def test_harness_delete_session_removes_from_store(provider, store, cache, tmp_path):
    session_store = SessionStore(str(tmp_path / "del.db"))
    provider.script = [{"content": "ok"}]
    agent = make_agent(provider, store, cache, session_store=session_store)

    agent.create_session("to delete")
    session_id = list(agent.sessions.keys())[0]
    assert session_store.get_meta(session_id) is not None

    assert agent.delete_session(session_id) is True
    assert session_store.get_meta(session_id) is None
    assert agent.get_session(session_id) is None


@pytest.mark.asyncio
async def test_harness_without_store_still_works(provider, store, cache):
    provider.script = [{"content": "no persistence"}]
    agent = make_agent(provider, store, cache)  # session_store=None
    events = await collect(agent, "hi")
    assert any(e["type"] == "done" for e in events)
    assert len(agent.list_sessions()) == 1


@pytest.mark.parametrize('user_id,agent_id', [('other', 'test-agent'), ('tester', 'other-agent')])
def test_session_ownership_cannot_be_read_deleted_or_overwritten(provider, store, cache, session_store, user_id, agent_id):
    session_store.save(SessionMeta(id='private', user_id=user_id, agent_id=agent_id))
    session_store.append_message('private', Message(role='user', content='private message'))
    agent = make_agent(provider, store, cache, session_store=session_store)
    assert agent.get_session('private') is None
    assert not agent.delete_session('private')
    with pytest.raises(ValueError, match='another user or agent'):
        agent._get_or_create_session('private')
    assert session_store.messages('private')[0].content == 'private message'
    assert session_store.get_meta('private').user_id == user_id
