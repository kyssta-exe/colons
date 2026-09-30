"""Saved policies gate real file changes and update current/future agents."""
from types import SimpleNamespace
import pytest
from colons_core.config import ColonsConfig
from colons_core.tools import ToolManager, ToolRegistry
from colons_core.tools.builtin import FileSystemTools
from colons_core.tools.settings import PermissionSettings
from fastapi.testclient import TestClient


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['auto', 'approval', 'full_access'])
async def test_real_file_tools_in_each_mode(tmp_path, mode):
    registry = ToolRegistry()
    FileSystemTools(str(tmp_path)).register(registry)
    tools = ToolManager(registry)
    tools.set_permission_mode(mode)
    approvals = []
    async def approve(request):
        approvals.append(request.tool_name)
        return True
    assert (await tools.execute('write_file', {'path': 'nested/example.txt', 'content': 'before'}, approval_callback=approve)).success
    assert (tmp_path / 'nested/example.txt').read_text() == 'before'
    assert (await tools.execute('find_and_replace', {'path': 'nested/example.txt', 'find': 'before', 'replace': 'after'}, approval_callback=approve)).success
    assert (await tools.execute('read_file', {'path': 'nested/example.txt'}, approval_callback=approve)).output == 'after'
    assert approvals == ([] if mode == 'full_access' else ['write_file', 'find_and_replace'] + (['read_file'] if mode == 'approval' else []))
    result = await tools.execute('read_file', {'path': '../outside'}, approval_callback=approve)
    assert not result.success and 'escapes workspace' in result.error


@pytest.mark.asyncio
async def test_declined_write_changes_nothing(tmp_path):
    registry = ToolRegistry()
    FileSystemTools(str(tmp_path)).register(registry)
    tools = ToolManager(registry)
    for mode in ('auto', 'approval'):
        tools.set_permission_mode(mode)
        result = await tools.execute('write_file', {'path': 'blocked.txt', 'content': 'no'}, approval_callback=lambda r: False)
        assert result.status.value == 'denied'
        assert not (tmp_path / 'blocked.txt').exists()


def test_api_auth_and_persisted_mode_for_existing_and_future_agents(monkeypatch, tmp_path):
    import colons_api.main as main
    config = ColonsConfig()
    config.data_dir = str(tmp_path)
    policy = PermissionSettings(config)
    agent = SimpleNamespace(tool_manager=ToolManager())
    monkeypatch.setattr(main, 'manager', SimpleNamespace(permission_settings=policy, agents={'one': agent}))
    monkeypatch.setattr(main.CONFIG.server, 'api_keys', ['permission-key'])
    client = TestClient(main.app)
    headers = {'Authorization': 'Bearer permission-key'}
    assert client.put('/api/settings/permissions', json={'mode': 'full_access'}).status_code == 401
    response = client.put('/api/settings/permissions', headers=headers, json={'mode': 'approval'})
    assert response.status_code == 200 and agent.tool_manager.permission_mode == 'approval'
    future = SimpleNamespace(tool_manager=ToolManager())
    PermissionSettings(config).apply(future)
    assert future.tool_manager.permission_mode == 'approval'
    assert client.get('/api/settings/permissions', headers=headers).json()['mode'] == 'approval'
    assert client.put('/api/settings/permissions', headers=headers, json={'mode': 'invalid'}).status_code == 422
    client.put('/api/settings/permissions', headers=headers, json={'mode': 'full_access'})
    assert agent.tool_manager.auto_approve
