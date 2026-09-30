"""The user terminal executes only explicit shell submissions with server auth."""
from types import SimpleNamespace

import pytest
from colons_core.tools.builtin import ShellTools
from colons_core.tools.manager import ToolManager, ToolRegistry
from fastapi.testclient import TestClient


@pytest.fixture
def terminal_api(monkeypatch, tmp_path):
    import colons_api.main as main
    registry = ToolRegistry()
    ShellTools(cwd=str(tmp_path)).register(registry)
    tools = ToolManager(registry)
    scopes = []

    def get_agent(user_id, agent_id):
        scopes.append((user_id, agent_id))
        return SimpleNamespace(tool_manager=tools)

    monkeypatch.setattr(main, 'manager', SimpleNamespace(get_or_create=get_agent))
    monkeypatch.setattr(main.CONFIG.server, 'api_keys', ['terminal-secret'])
    return TestClient(main.app), scopes, tools, tmp_path


def test_terminal_auth_and_scope(terminal_api):
    client, scopes, tools, _ = terminal_api
    body = {'command': 'printf terminal-ok', 'user_id': 'owner', 'agent_id': 'bot-builder'}
    assert client.post('/api/terminal/command', json=body).status_code == 401
    assert scopes == []
    response = client.post('/api/terminal/command', json=body,
                           headers={'Authorization': 'Bearer terminal-secret'})
    assert response.status_code == 200
    assert response.json()['output']['stdout'] == 'terminal-ok'
    assert scopes == [('owner', 'bot-builder')]
    assert tools.history[-1]['tool'] == 'run_command'


def test_terminal_directory_and_failed_command(terminal_api):
    client, _, _, workspace = terminal_api
    directory = workspace / 'nested'
    directory.mkdir()
    headers = {'Authorization': 'Bearer terminal-secret'}
    result = client.post('/api/terminal/command', headers=headers,
                         json={'command': 'pwd', 'cwd': str(directory)}).json()
    assert result['output']['stdout'].strip() == str(directory)
    result = client.post('/api/terminal/command', headers=headers,
                         json={'command': 'printf failure >&2; exit 7'}).json()
    assert result['output']['stderr'] == 'failure'
    assert result['output']['returncode'] == 7
    assert client.post('/api/terminal/command', headers=headers,
                       json={'command': '   '}).status_code == 400
    assert client.post('/api/terminal/command', headers=headers,
                       json={'command': ''}).status_code == 422


def test_terminal_submission_does_not_approve_other_tool_requests(terminal_api):
    client, _, tools, _ = terminal_api
    headers = {'Authorization': 'Bearer terminal-secret'}
    body = {'tool': 'run_command', 'arguments': {'command': 'printf denied'}}
    result = client.post('/api/tools/execute', json=body, headers=headers).json()
    assert result['status'] == 'denied'
    client.post('/api/terminal/command', json={'command': 'printf approved'}, headers=headers)
    result = client.post('/api/tools/execute', json=body, headers=headers).json()
    assert result['status'] == 'denied'
    assert tools.approval_callback is None
