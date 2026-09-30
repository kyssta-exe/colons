"""Exercise Hermes MCP tools against the real Colons API and task worker."""

import asyncio
import json
import sys
from types import SimpleNamespace

import colons_api.main as api_main
import httpx
import pytest
from colons_adapters.hermes import create_server
from colons_cli.client import ColonsClient
from colons_core.agents import AgentHarness
from colons_core.memory import EmbeddingService
from colons_core.tools.builtin import FileSystemTools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def payload(result):
    # FastMCP returns text content plus optional structured content.
    if isinstance(result, tuple):
        return result[1]
    return json.loads(result[0].text)


@pytest.fixture
def bridge(monkeypatch, provider, store, cache):
    agent = AgentHarness(provider, store, cache,
                         embeddings=EmbeddingService(provider=provider, cache=cache, dim=32),
                         agent_id="bot-researcher", user_id="owner")
    requested = []
    room_posts = []
    bots = []

    def create_bot(**fields):
        bot = {"id": "researcher", **fields}
        bots.append(bot)
        return SimpleNamespace(to_dict=lambda: bot)

    async def send_room(room_id, message, speaker):
        entry = {"room_id": room_id, "message": message, "speaker": speaker}
        room_posts.append(entry)
        return [SimpleNamespace(to_dict=lambda: entry)]

    def get_agent(user_id, agent_id):
        requested.append((user_id, agent_id))
        return agent

    monkeypatch.setattr(api_main, "manager", SimpleNamespace(
        get_or_create=get_agent, mention_hint=lambda *args, **kwargs: "",
        create_bot=create_bot, list_bots=lambda owner=None: bots,
        list_agents=lambda: [{"agent_id": agent.agent_id}],
        room_service=SimpleNamespace(
            list=lambda owner: [{"id": "room1", "owner": owner}],
            get=lambda room_id: room_id == "room1",
            history=lambda room_id, **kwargs: room_posts,
            send=send_room)))
    monkeypatch.setattr(api_main.CONFIG.server, "api_keys", ["adapter-key"])
    monkeypatch.setattr(api_main.CONFIG.server, "rate_limit_per_minute", 0)
    client = ColonsClient("http://testserver", api_key="adapter-key")
    original = client.client
    client.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=api_main.app),
                                    base_url="http://testserver", headers=client._headers)
    return create_server(client, user_id="owner"), agent, requested, client, original


@pytest.mark.asyncio
async def test_discovery_creation_chat_and_room_collaboration(bridge, provider):
    server, agent, _, client, original = bridge
    try:
        created = payload(await server.call_tool("create_colon", {"name": "Researcher"}))
        assert created["owner"] == "owner"
        assert created["agent_id"] == "bot-researcher"
        found = payload(await server.call_tool("list_colons", {}))
        assert found["bots"][0]["name"] == "Researcher"
        assert found["agents"][0]["agent_id"] == created["agent_id"]
        provider.script = [{"content": "Hello Hermes"}, {"content": "Follow-up reply"}]
        reply = payload(await server.call_tool("chat_with_colon", {
            "message": "Hello", "agent_id": created["agent_id"]}))
        assert reply["response"] == "Hello Hermes"
        followup = payload(await server.call_tool("chat_with_colon", {
            "message": "Continue", "agent_id": created["agent_id"],
            "session_id": reply["session_id"]}))
        assert followup["session_id"] == reply["session_id"]
        rooms = payload(await server.call_tool("list_rooms", {}))
        assert rooms["rooms"][0]["owner"] == "owner"
        sent = payload(await server.call_tool("send_to_room", {
            "room_id": "room1", "message": "Team update"}))
        assert sent["messages"][0]["speaker"] == "Hermes"
        history = payload(await server.call_tool("read_room", {"room_id": "room1"}))
        assert history == sent
    finally:
        await agent.stop()
        await client.close()
        await original.aclose()


@pytest.mark.asyncio
async def test_assignment_executes_in_background_and_result_is_scoped(bridge, provider):
    server, agent, requested, client, original = bridge
    provider.script = [{"content": "1. Research the question"}, {"content": "Concrete research result"}]
    try:
        result = payload(await server.call_tool("assign_work", {
            "description": "Research a question", "agent_id": "bot-researcher"}))
        task_id = result["task"]["id"]
        assert result["agent_id"] == "bot-researcher"
        for _ in range(100):
            task = payload(await server.call_tool("get_work", {
                "task_id": task_id, "agent_id": result["agent_id"]}))
            if task["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.01)
        assert task["status"] == "completed"
        assert task["result"] == "Concrete research result"
        listed = payload(await server.call_tool("list_work", {"agent_id": "bot-researcher"}))
        assert len(listed["tasks"]) == 1
        assert set(requested) == {("owner", "bot-researcher")}
    finally:
        await agent.stop()
        await client.close()
        await original.aclose()


@pytest.mark.asyncio
async def test_authentication_failure_is_actionable_and_redacted(bridge):
    server, agent, _, client, original = bridge
    client.client.headers["Authorization"] = "Bearer wrong-key"
    try:
        with pytest.raises(Exception, match="HTTP 401.*COLONS_ADAPTER_API_KEY") as error:
            await server.call_tool("assign_work", {"description": "Do work"})
        assert "wrong-key" not in str(error.value)
        assert "adapter-key" not in str(error.value)
        assert not agent.tasks
    finally:
        await client.close()
        await original.aclose()


@pytest.mark.asyncio
async def test_chat_does_not_bypass_tool_approval(bridge, provider, tmp_path):
    server, agent, _, client, original = bridge
    FileSystemTools(str(tmp_path)).register(agent.registry)
    agent.tool_manager.set_permission_mode("approval")
    provider.script = [
        {"tool_calls": [{"id": "write", "name": "write_file", "arguments": {
            "path": "approval-required.txt", "content": "must not be written"}}]},
        {"content": "Writing requires approval."},
    ]
    try:
        await server.call_tool("chat_with_colon", {"message": "Write a file"})
        assert not (tmp_path / "approval-required.txt").exists()
        assert agent.tool_manager.permission_mode == "approval"
    finally:
        await agent.stop()
        await client.close()
        await original.aclose()


@pytest.mark.asyncio
async def test_failed_work_is_reported_without_false_completion(bridge, provider, monkeypatch):
    server, agent, _, client, original = bridge

    async def fail(**kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(provider, "chat", fail)
    try:
        result = payload(await server.call_tool("assign_work", {"description": "Do work"}))
        for _ in range(100):
            task = payload(await server.call_tool("get_work", {"task_id": result["task"]["id"]}))
            if task["status"] == "failed":
                break
            await asyncio.sleep(0.01)
        assert task["status"] == "failed"
        assert task["result"] is None
        assert "provider unavailable" in task["error"]
    finally:
        await agent.stop()
        await client.close()
        await original.aclose()


@pytest.mark.asyncio
async def test_stdio_handshake_and_discovery():
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "colons_adapters.hermes"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "Colons"
            tools = {t.name: t for t in (await session.list_tools()).tools}
            assert len(tools) == 9
            assert tools["get_work"].annotations.readOnlyHint
            assert not tools["assign_work"].annotations.readOnlyHint
            denied = await session.call_tool("assign_work", {"description": ""})
            assert denied.isError
