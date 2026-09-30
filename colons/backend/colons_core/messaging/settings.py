"""Private persisted settings and per-service live reconfiguration."""
import asyncio
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

from .bridge import MessagingBridge
from .discord import DiscordAdapter
from .slack import SlackAdapter
from .telegram import TelegramAdapter

SERVICES = {'telegram': TelegramAdapter, 'discord': DiscordAdapter, 'slack': SlackAdapter}
SECRETS = ('bot_token', 'signing_secret', 'webhook_url')


class MessagingSettings:
    def __init__(self, config):
        self.config = config
        self.path = Path(config.data_dir) / 'messaging.json'
        self.lock = asyncio.Lock()
        self.saved = {}
        if self.path.exists():
            self.saved = json.loads(self.path.read_text())
            for name in SERVICES:
                if name in self.saved:
                    setattr(config.messaging, name,
                            {**getattr(config.messaging, name), **self.saved[name]})

    def redact(self, text):
        text = str(text or '')
        for name in SERVICES:
            for key in SECRETS:
                value = getattr(self.config.messaging, name).get(key)
                if value:
                    text = text.replace(str(value), '[redacted]')
        return text

    def statuses(self, bridge):
        return [{**s, 'last_error': self.redact(s.get('last_error'))}
                for s in bridge.status()] if bridge else []

    def public(self, bridge):
        states = {s['name']: s for s in self.statuses(bridge)}
        services = []
        for name in SERVICES:
            cfg = getattr(self.config.messaging, name)
            services.append({
                'name': name, 'enabled': bool(cfg.get('enabled')),
                'saved': name in self.saved,
                'credentials': {key: bool(cfg.get(key)) for key in SECRETS},
                'allowed_users': cfg.get('allowed_users', []),
                'allowed_chats': cfg.get('allowed_chats', []),
                'default_channel': cfg.get('default_channel', ''),
                'status': states.get(name, {'connected': False, 'last_error': ''}),
            })
        return {'services': services}

    def _persist(self, saved):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.messaging-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(saved, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    async def update(self, manager, name, changes):
        async with self.lock:
            cfg = deepcopy(getattr(self.config.messaging, name))
            for key, value in changes.items():
                # Blank credentials retain a saved/environment credential.
                if key in SECRETS and not (value or '').strip():
                    continue
                cfg[key] = value.strip() if isinstance(value, str) else value
            if cfg.get('enabled'):
                if name != 'slack' and not cfg.get('bot_token'):
                    raise ValueError('A bot token is required to connect.')
                if name == 'slack':
                    if not (cfg.get('bot_token') or cfg.get('webhook_url')):
                        raise ValueError('Enter a bot token or an incoming webhook URL.')
                    if cfg.get('bot_token') and not cfg.get('signing_secret'):
                        raise ValueError('A signing secret is required for Slack bot messages.')
                    if cfg.get('webhook_url'):
                        url = urlparse(cfg['webhook_url'])
                        if url.scheme != 'https' or url.hostname not in ('hooks.slack.com', 'hooks.slack-gov.com'):
                            raise ValueError('Use an HTTPS Slack incoming webhook URL.')
            saved = {**self.saved, name: cfg}
            await asyncio.to_thread(self._persist, saved)
            self.saved = saved
            if manager.messaging is None:
                manager.messaging = MessagingBridge(manager, self.config)
            bridge = manager.messaging
            old = bridge.adapters.pop(name, None)
            if old:
                await old.stop()
            setattr(self.config.messaging, name, cfg)
            if cfg.get('enabled'):
                runtime_cfg = {**cfg, 'state_dir': str(self.path.parent)} if name == 'telegram' else cfg
                adapter = SERVICES[name](runtime_cfg, bridge.handle_incoming)
                bridge.adapters[name] = adapter
                try:
                    await asyncio.wait_for(bridge._start_adapter(adapter), timeout=30)
                except Exception as error:
                    await adapter.stop()
                    adapter.last_error = self.redact(str(error)) or 'Connection timed out. Check your credentials and retry.'
            return self.public(bridge)
