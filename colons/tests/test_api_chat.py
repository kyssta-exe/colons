"""Regressions for cancellation and session IDs with a blocking provider."""
import asyncio
import threading
from types import SimpleNamespace

import pytest
from colons_core.agents import AgentHarness, AgentState
from colons_core.memory import EmbeddingService
from colons_core.providers.base import ProviderError
from fastapi.testclient import TestClient


@pytest.fixture
def chat_api(monkeypatch, provider, store, cache):
    import colons_api.main as api_main
    agent = AgentHarness(provider, store, cache,
                         embeddings=EmbeddingService(provider=provider, cache=cache, dim=32),
                         agent_id='bot-researcher', user_id='owner')
    requested = []

    def get_agent(user_id, agent_id):
        requested.append((user_id, agent_id))
        return agent

    async def start():
        agent.state = AgentState.IDLE

    monkeypatch.setattr(agent, 'start', start)
    monkeypatch.setattr(api_main, 'manager', SimpleNamespace(
        get_or_create=get_agent, mention_hint=lambda *args, **kwargs: ''))
    monkeypatch.setattr(api_main.CONFIG.server, 'api_keys', [])
    return TestClient(api_main.app), agent, requested


def block_provider(monkeypatch, provider):
    started, cancelled = threading.Event(), threading.Event()

    async def chat(**kwargs):
        started.set()
        try:
            await asyncio.sleep(3600)
        finally:
            cancelled.set()

    monkeypatch.setattr(provider, 'chat', chat)
    return started, cancelled


def test_rest_returns_created_session_id(chat_api, provider):
    client, agent, _ = chat_api
    provider.script = [{'content': 'hello'}]
    response = client.post('/api/chat', json={'message': 'hi'})
    assert response.status_code == 200
    assert response.json()['session_id'] in agent.sessions
    assert response.json()['response'] == 'hello'


def test_cancel_and_ping_during_named_bot_reply(chat_api, provider, monkeypatch):
    client, agent, requested = chat_api
    started, cancelled = block_provider(monkeypatch, provider)
    with client.websocket_connect('/ws/chat') as ws:
        ws.send_json({'message': 'research', 'user_id': 'owner', 'agent_id': 'bot-researcher'})
        start = ws.receive_json()
        assert start['type'] == 'start'
        assert ws.receive_json()['type'] == 'status'
        assert started.wait(1)
        ws.send_json({'type': 'cancel', 'request_id': 'unknown'})
        ws.send_json({'type': 'ping'})
        assert ws.receive_json()['type'] == 'pong'
        assert not cancelled.is_set()
        ws.send_json({'type': 'cancel', 'request_id': start['request_id']})
        assert ws.receive_json()['state'] == 'cancelled'
        done = ws.receive_json()
        assert done['type'] == 'done'
        assert done['session_id'] == start['session_id']
        assert cancelled.wait(1)
        assert not agent._cancel_flags
        assert agent.state == AgentState.IDLE
        ws.send_json({'type': 'ping'})
        assert ws.receive_json()['type'] == 'pong'
    assert requested == [('owner', 'bot-researcher')]


def test_disconnect_cancels_provider_work(chat_api, provider, monkeypatch):
    client, agent, _ = chat_api
    started, cancelled = block_provider(monkeypatch, provider)
    with client.websocket_connect('/ws/chat') as ws:
        ws.send_json({'message': 'research'})
        assert ws.receive_json()['type'] == 'start'
        assert ws.receive_json()['type'] == 'status'
        assert started.wait(1)
    assert cancelled.wait(1)
    assert not agent._cancel_flags


def test_invalid_frames_do_not_break_connection(chat_api):
    client, _, _ = chat_api
    with client.websocket_connect('/ws/chat') as ws:
        for data in [None, [], {'message': 123}, {'message': ' '}]:
            ws.send_json(data)
            assert ws.receive_json()['type'] == 'error'
        ws.send_json({'type': 'ping'})
        assert ws.receive_json()['type'] == 'pong'


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['planning', 'execution'])
async def test_task_provider_failures_are_recorded(provider, store, cache, monkeypatch, phase):
    agent = AgentHarness(provider, store, cache)
    original = provider.chat
    calls = 0

    async def fail(**kwargs):
        nonlocal calls
        calls += 1
        if phase == 'planning' or calls == 2:
            raise ProviderError('provider unavailable', provider='scripted')
        return await original(**kwargs)

    provider.script = [{'content': '1. Read the file'}]
    monkeypatch.setattr(provider, 'chat', fail)
    task = await agent.create_task('Read a file')
    with pytest.raises(RuntimeError, match='provider unavailable'):
        await agent.execute_task(task)
    assert task.status == 'failed'
    assert 'provider unavailable' in task.error
    assert task.completed_at is not None
    assert task.result is None


@pytest.mark.parametrize('approved', [True, False])
def test_websocket_tool_approval_is_scoped_to_this_turn(chat_api, provider, approved):
    from colons_core.tools import PermissionLevel, ToolSpec
    client, agent, _ = chat_api
    executed = []

    async def action():
        executed.append(True)
        return 'approved action'

    agent.registry.register(ToolSpec(name='test_action', description='Test action',
        parameters={'type': 'object', 'properties': {}}, func=action, permission=PermissionLevel.ASK))
    provider.script = [
        {'tool_calls': [{'id': 'call-1', 'name': 'test_action', 'arguments': {}}]},
        {'content': 'Finished'},
    ]
    with client.websocket_connect('/ws/chat') as ws:
        ws.send_json({'message': 'do the action'})
        while True:
            event = ws.receive_json()
            if event['type'] == 'approval_required':
                break
        ws.send_json({'type': 'ping'})
        assert ws.receive_json()['type'] == 'pong'
        ws.send_json({'type': 'approval', 'approval_id': event['approval_id'], 'approved': approved})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event['type'] == 'done':
                break
        result = next(event for event in events if event['type'] == 'tool_result')
        assert result['success'] is approved
    assert bool(executed) is approved


@pytest.mark.parametrize('mode,expected_approvals', [('auto', 2), ('approval', 3), ('full_access', 0)])
def test_file_tool_chain_through_chat_approvals(chat_api, provider, tmp_path, mode, expected_approvals):
    from colons_core.tools.builtin import FileSystemTools
    client, agent, _ = chat_api
    FileSystemTools(str(tmp_path)).register(agent.registry)
    agent.tool_manager.set_permission_mode(mode)
    provider.script = [
        {'tool_calls': [{'id': 'write', 'name': 'write_file', 'arguments': {'path': 'chat-created.txt', 'content': 'before'}}]},
        {'tool_calls': [{'id': 'edit', 'name': 'find_and_replace', 'arguments': {'path': 'chat-created.txt', 'find': 'before', 'replace': 'after'}}]},
        {'tool_calls': [{'id': 'read', 'name': 'read_file', 'arguments': {'path': 'chat-created.txt'}}]},
        {'content': 'Created, edited, and read the file.'},
    ]
    approvals = []; results = []
    with client.websocket_connect('/ws/chat') as ws:
        ws.send_json({'message': 'Create and edit a file', 'stream': False})
        while True:
            event = ws.receive_json()
            if event['type'] == 'approval_required':
                approvals.append(event['tool'])
                if event['tool'] == 'write_file':
                    assert not (tmp_path / 'chat-created.txt').exists()
                ws.send_json({'type': 'approval', 'approval_id': event['approval_id'], 'approved': True})
            if event['type'] == 'tool_result': results.append(event)
            if event['type'] == 'done': break
    assert len(approvals) == expected_approvals
    assert len(results) == 3 and all(result['success'] for result in results)
    assert (tmp_path / 'chat-created.txt').read_text() == 'after'
    assert results[-1]['output'] == 'after'
