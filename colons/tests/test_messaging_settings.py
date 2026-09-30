"""UI messaging settings persist privately and reconfigure only the chosen adapter."""
import json
from types import SimpleNamespace

import pytest
from colons_core.config import ColonsConfig
from colons_core.messaging.base import BaseMessagingAdapter
from colons_core.messaging.settings import MessagingSettings
from fastapi.testclient import TestClient


class FakeAdapter(BaseMessagingAdapter):
    name = 'telegram'

    async def send_message(self, chat_id, text, **kwargs):
        return True

    async def start(self):
        if self.config.get('bot_token') == 'bad-secret':
            raise RuntimeError('Invalid token bad-secret')
        self.connected = True


@pytest.fixture
def settings_api(monkeypatch, tmp_path):
    import colons_api.main as main
    from colons_core.messaging import settings
    config = ColonsConfig()
    config.data_dir = str(tmp_path)
    store = MessagingSettings(config)
    manager = SimpleNamespace(config=config, messaging=None, messaging_settings=store)
    monkeypatch.setattr(main, 'manager', manager)
    monkeypatch.setattr(main.CONFIG.server, 'api_keys', ['settings-secret'])
    monkeypatch.setitem(settings.SERVICES, 'telegram', FakeAdapter)
    return TestClient(main.app), manager, tmp_path


HEADERS = {'Authorization': 'Bearer settings-secret'}


def test_save_connect_private_reload_and_disconnect(settings_api):
    client, manager, directory = settings_api
    assert client.get('/api/messaging/config').status_code == 401
    assert client.put('/api/messaging/config/telegram', json={'enabled': True}).status_code == 401
    result = client.put('/api/messaging/config/telegram', headers=HEADERS,
                        json={'enabled': True, 'bot_token': 'private-token', 'allowed_users': ['123']})
    assert result.status_code == 200
    assert 'private-token' not in result.text
    assert result.json()['services'][0]['status']['connected']
    path = directory / 'messaging.json'
    assert path.stat().st_mode & 0o777 == 0o600
    config = ColonsConfig()
    config.data_dir = str(directory)
    reloaded = MessagingSettings(config)
    assert config.messaging.telegram['bot_token'] == 'private-token'
    assert reloaded.public(None)['services'][0]['saved']
    response = client.put('/api/messaging/config/telegram', headers=HEADERS,
                          json={'enabled': False})
    assert not response.json()['services'][0]['enabled']
    assert not manager.messaging.adapters
    client.put('/api/messaging/config/telegram', headers=HEADERS,
               json={'enabled': True, 'bot_token': ''})
    assert manager.messaging.adapters['telegram'].config['bot_token'] == 'private-token'
    assert 'private-token' not in client.get('/api/messaging/config', headers=HEADERS).text


def test_validation_failure_and_error_redaction(settings_api):
    client, manager, directory = settings_api
    assert client.put('/api/messaging/config/telegram', headers=HEADERS,
                      json={'enabled': True}).status_code == 400
    assert not (directory / 'messaging.json').exists()
    assert client.put('/api/messaging/config/slack', headers=HEADERS,
                      json={'enabled': True, 'bot_token': 'xoxb-token'}).status_code == 400
    assert client.put('/api/messaging/config/slack', headers=HEADERS,
                      json={'enabled': True, 'webhook_url': 'http://example.com'}).status_code == 400
    response = client.put('/api/messaging/config/telegram', headers=HEADERS,
                          json={'enabled': True, 'bot_token': 'bad-secret'})
    assert response.status_code == 200
    assert 'bad-secret' not in response.text
    status = response.json()['services'][0]['status']
    assert not status['connected']
    assert status['last_error'] == 'Invalid token [redacted]'
    assert 'bad-secret' not in client.get('/api/messaging/status', headers=HEADERS).text
    assert json.loads((directory / 'messaging.json').read_text())['telegram']['enabled']


def test_service_update_keeps_other_adapters_running(settings_api):
    client, manager, _ = settings_api
    client.put('/api/messaging/config/telegram', headers=HEADERS,
               json={'enabled': True, 'bot_token': 'private-token'})
    original = manager.messaging.adapters['telegram']
    result = client.put('/api/messaging/config/discord', headers=HEADERS,
                        json={'enabled': False})
    assert result.status_code == 200
    assert manager.messaging.adapters['telegram'] is original
    assert original.connected
    assert client.put('/api/messaging/config/unknown', headers=HEADERS,
                      json={'enabled': False}).status_code == 422


@pytest.mark.asyncio
async def test_restart_connection_failure_redacts_credentials(settings_api, monkeypatch):
    from colons_core.messaging import bridge
    _, manager, _ = settings_api
    manager.config.messaging.telegram = {'enabled': True, 'bot_token': 'bad-secret'}
    monkeypatch.setattr(bridge, 'TelegramAdapter', FakeAdapter)
    restored = bridge.MessagingBridge(manager, manager.config)
    await restored.start()
    assert restored.adapters['telegram'].last_error == 'Invalid token [redacted]'
    assert not restored.adapters['telegram'].connected
    await restored.stop()
