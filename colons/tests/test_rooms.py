"""Tests for group rooms: store, validation, rounds, mentions, passes, caps."""
import pytest

from colons_core.bots import Bot
from colons_core.rooms import (
    MAX_MESSAGES_PER_SEND,
    MAX_ROUNDS,
    Room,
    RoomMessage,
    RoomService,
    RoomStore,
)
from colons_core.rooms.service import MIN_MEMBERS


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #

@pytest.fixture
def room_store(tmp_path):
    return RoomStore(str(tmp_path / "rooms.db"))


def test_room_crud(room_store):
    room = room_store.save(Room(name="Standup", members=["a", "b"]))
    assert room_store.get(room.id).name == "Standup"
    assert len(room_store.list()) == 1
    assert room_store.delete(room.id) is True
    assert room_store.get(room.id) is None


def test_room_messages_roundtrip_and_prune(tmp_path):
    store = RoomStore(str(tmp_path / "prune.db"), max_messages=5)
    store.save(Room(id="r1", name="R", members=["a", "b"]))
    for i in range(12):
        store.append(RoomMessage(room_id="r1", speaker="user", content=f"m{i}"))
    messages = store.messages("r1", limit=50)
    assert len(messages) == 5
    assert messages[-1].content == "m11"
    assert store.message_count("r1") == 5


def test_room_delete_removes_messages(room_store):
    room_store.save(Room(id="r1", name="R", members=["a", "b"]))
    room_store.append(RoomMessage(room_id="r1", speaker="user", content="hi"))
    room_store.delete("r1")
    assert room_store.messages("r1") == []


# --------------------------------------------------------------------------- #
# Fake manager for orchestration tests
# --------------------------------------------------------------------------- #

class FakeBotAgent:
    def __init__(self, bot_id, manager):
        self.bot_id = bot_id
        self.manager = manager
        self.calls = []

    async def start(self):
        return None

    async def chat(self, prompt, session_id=None, stream=False, **kwargs):
        self.calls.append({"prompt": prompt, "session_id": session_id})
        script = self.manager.scripts.get(self.bot_id, [])
        reply = script.pop(0) if script else "[SILENT]"
        yield {"type": "done", "content": reply, "session_id": session_id}


class FakeBots:
    def __init__(self, bots):
        self._bots = {b.id: b for b in bots}

    def get(self, bot_id):
        return self._bots.get(bot_id)


class FakeManager:
    def __init__(self, bots, scripts):
        self.bots = FakeBots(bots)
        self.scripts = scripts
        self.agents = {}

    def get_bot_agent(self, bot_id):
        agent = self.agents.get(bot_id)
        if agent is None:
            agent = FakeBotAgent(bot_id, self)
            self.agents[bot_id] = agent
        return agent


def make_service(tmp_path, bots, scripts):
    store = RoomStore(str(tmp_path / "rooms.db"))
    manager = FakeManager(bots, scripts)
    return RoomService(store, manager), store, manager


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def test_create_room_validation(tmp_path):
    bots = [Bot(id="a", name="A"), Bot(id="b", name="B")]
    service, _, _ = make_service(tmp_path, bots, {})
    with pytest.raises(ValueError):
        service.create("Solo", ["a"])
    with pytest.raises(ValueError):
        service.create("Ghost", ["a", "ghost"])
    with pytest.raises(ValueError):
        service.create("Big", ["a", "b", "c", "d", "e", "f", "g"])
    room = service.create("Team", ["a", "b"])
    assert room.members == ["a", "b"]


# --------------------------------------------------------------------------- #
# Rounds
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_send_runs_one_round_when_no_mentions(tmp_path):
    bots = [Bot(id="a", name="Alpha"), Bot(id="b", name="Bravo")]
    scripts = {"a": ["I checked the logs."], "b": ["Deploy looks good."]}
    service, store, manager = make_service(tmp_path, bots, scripts)
    room = service.create("Team", ["a", "b"])

    added = await service.send(room.id, "How is the deploy?")
    speakers = [m.speaker for m in added]
    assert speakers == ["user", "a", "b"]  # one round, then next_targets empty
    assert manager.agents["a"].calls[0]["session_id"] == f"room-{room.id}-a"
    # Transcript was persisted
    assert len(store.messages(room.id)) == 3


@pytest.mark.asyncio
async def test_mention_scopes_first_round(tmp_path):
    bots = [Bot(id="a", name="Alpha"), Bot(id="b", name="Bravo")]
    scripts = {"a": ["on it"], "b": ["should not speak"]}
    service, _, manager = make_service(tmp_path, bots, scripts)
    room = service.create("Team", ["a", "b"])

    added = await service.send(room.id, "@a please check")
    assert [m.speaker for m in added] == ["user", "a"]
    assert "b" not in manager.agents  # never invoked


@pytest.mark.asyncio
async def test_silence_ends_room(tmp_path):
    bots = [Bot(id="a", name="Alpha"), Bot(id="b", name="Bravo")]
    scripts = {"a": ["[SILENT]"], "b": ["NO_REPLY"]}
    service, _, _ = make_service(tmp_path, bots, scripts)
    room = service.create("Team", ["a", "b"])

    added = await service.send(room.id, "anything?")
    assert [m.speaker for m in added] == ["user"]  # everyone passed


@pytest.mark.asyncio
async def test_mention_in_reply_pulls_teammate_next_round(tmp_path):
    bots = [Bot(id="a", name="Alpha"), Bot(id="b", name="Bravo")]
    scripts = {"a": ["@b can you confirm?", "thanks"], "b": ["confirmed"]}
    service, _, _ = make_service(tmp_path, bots, scripts)
    room = service.create("Team", ["a", "b"])

    added = await service.send(room.id, "@a status?")
    speakers = [m.speaker for m in added]
    assert speakers[0] == "user"
    assert "a" in speakers
    assert "b" in speakers  # pulled in by a's mention
    assert len(added) <= MAX_MESSAGES_PER_SEND


@pytest.mark.asyncio
async def test_round_cap_and_message_cap(tmp_path):
    bots = [Bot(id="a", name="Alpha"), Bot(id="b", name="Bravo")]
    # Every reply mentions the other -> would ping-pong forever without caps
    scripts = {
        "a": ["@b ping"] * 20,
        "b": ["@a pong"] * 20,
    }
    service, _, _ = make_service(tmp_path, bots, scripts)
    room = service.create("Team", ["a", "b"])

    added = await service.send(room.id, "start")
    assert len(added) <= MAX_MESSAGES_PER_SEND
    # At most MAX_ROUNDS rounds of exchange after the user message
    assert len(added) - 1 <= MAX_ROUNDS * len(bots)


@pytest.mark.asyncio
async def test_user_escalation_sets_needs_user(tmp_path):
    bots = [Bot(id="a", name="Alpha"), Bot(id="b", name="Bravo")]
    scripts = {"a": ["@user I need your decision on scope."], "b": ["[SILENT]"]}
    service, store, _ = make_service(tmp_path, bots, scripts)
    room = service.create("Team", ["a", "b"])
    original_created = room.created_at

    await service.send(room.id, "kick off")
    updated = store.get(room.id)
    assert updated.metadata.get("needs_user") is True
    assert updated.created_at == original_created  # created_at preserved on save

    service.mark_seen(room.id)
    assert store.get(room.id).metadata.get("needs_user") is False


@pytest.mark.asyncio
async def test_history_includes_room_context(tmp_path):
    bots = [Bot(id="a", name="Alpha"), Bot(id="b", name="Bravo")]
    scripts = {"a": ["first answer", "second answer"], "b": ["[SILENT]", "[SILENT]"]}
    service, _, manager = make_service(tmp_path, bots, scripts)
    room = service.create("Team", ["a", "b"])

    await service.send(room.id, "first question")
    await service.send(room.id, "second question")

    second_prompt = manager.agents["a"].calls[-1]["prompt"]
    assert "first question" in second_prompt
    assert "first answer" in second_prompt
    assert "second question" in second_prompt


def test_room_persistence_across_instances(tmp_path):
    path = str(tmp_path / "persist.db")
    RoomStore(path).save(Room(id="r1", name="Persistent", members=["a", "b"]))
    store2 = RoomStore(path)
    assert store2.get("r1").name == "Persistent"
    assert store2.get("r1").members == ["a", "b"]


def test_update_room_members_validation(tmp_path):
    bots = [Bot(id="a", name="A"), Bot(id="b", name="B"), Bot(id="c", name="C")]
    service, _, _ = make_service(tmp_path, bots, {})
    room = service.create("Team", ["a", "b"])

    updated = service.update(room.id, members=["a", "b", "c"])
    assert updated.members == ["a", "b", "c"]
    with pytest.raises(ValueError):
        service.update(room.id, members=["a"])
    with pytest.raises(ValueError):
        service.update(room.id, members=["a", "ghost"])
