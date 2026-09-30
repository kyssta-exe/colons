"""Persist the server's default tool permission policy."""
import json
import os
import tempfile
from pathlib import Path

MODES = ('auto', 'approval', 'full_access')


class PermissionSettings:
    def __init__(self, config):
        self.config = config
        self.path = Path(config.data_dir) / 'permissions.json'
        self.mode = 'full_access' if config.tools.auto_approve else 'auto'
        if self.path.exists():
            mode = json.loads(self.path.read_text()).get('mode')
            if mode not in MODES:
                raise ValueError('Invalid saved permission mode')
            self.mode = mode

    def public(self):
        return {'mode': self.mode,
                'workspace': str(Path(self.config.tools.workspace or '.').resolve()),
                'disabled_tools': self.config.tools.disabled}

    def apply(self, agent):
        agent.tool_manager.set_permission_mode(self.mode)

    def save(self, mode, agents):
        if mode not in MODES:
            raise ValueError('Invalid permission mode')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.permissions-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump({'mode': mode}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.mode = mode
        for agent in agents:
            self.apply(agent)
        return self.public()
