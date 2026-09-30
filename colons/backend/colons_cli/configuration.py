"""Guided, private configuration shared by terminal and web entry points."""

import getpass
import json
import os
import tempfile
from pathlib import Path

from colons_core.config import DEFAULT_CONFIG_PATHS, ColonsConfig, _load_file
from colons_core.messaging.settings import MessagingSettings
from colons_core.providers import PROVIDER_CATALOG
from colons_core.tools.settings import PermissionSettings
from rich.console import Console
from rich.prompt import Confirm, Prompt


def profile_path(explicit=None):
    if explicit or os.environ.get("COLONS_CONFIG"):
        return Path(explicit or os.environ["COLONS_CONFIG"]).expanduser().resolve()
    for candidate in DEFAULT_CONFIG_PATHS:
        path = Path(candidate).expanduser()
        if path.is_file():
            return path.resolve()
    return Path("~/.config/colons/config.json").expanduser()


def save_config(path, data):
    """Atomic owner-only writes, preserving unknown settings."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix in (".yaml", ".yml"):
        import yaml
        text = yaml.safe_dump(data, sort_keys=False)
    else:
        text = json.dumps(data, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=".colons-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def ensure_profile(explicit=None):
    path = profile_path(explicit)
    if not path.exists():
        data = Path("~/.local/share/colons").expanduser()
        workspace = data / "workspace"
        # Keep a source checkout's existing workspace when moving to the new CLI.
        for root in (Path.cwd(), Path.cwd() / "colons"):
            if (root / "data" / "sessions.db").exists():
                data = root / "data"
                workspace = root / "workspace"
                break
        save_config(path, {"data_dir": str(data),
                           "memory": {"store_url": str(data / "memory.db")},
                           "tools": {"workspace": str(workspace)},
                           "server": {"host": "127.0.0.1", "port": 8000}})
    os.environ["COLONS_CONFIG"] = str(path)
    cfg = ColonsConfig.load(str(path))
    Path(cfg.data_dir).expanduser().mkdir(parents=True, exist_ok=True)
    Path(cfg.tools.workspace).expanduser().mkdir(parents=True, exist_ok=True)
    return path, cfg


def secret(label, existing=""):
    value = getpass.getpass(f"{label} ({'Enter keeps saved value' if existing else 'hidden input'}): ")
    return value.strip() or existing


def setup(section=None, path=None):
    console = Console()
    path, cfg = ensure_profile(path)
    console.print("\n[bold]Colons setup[/bold]", markup=True)
    console.print(f"Configuration: {path}", markup=False)
    console.print("Secrets stay hidden. Changes are saved for the next server start.")
    choices = {"1": "provider", "2": "messaging", "3": "permissions", "4": "server", "5": "voice"}
    while True:
        selected = section
        if selected is None:
            console.print("\n1 Provider & model   2 Messaging   3 Permissions & workspace   4 Server   5 Voice   0 Done")
            choice = Prompt.ask("Choose a section", choices=["0", *choices], default="0")
            if choice == "0":
                break
            selected = choices[choice]
        data = _load_file(str(path))
        cfg = ColonsConfig.load(str(path))
        if selected == "provider":
            current = data.get("provider", {})
            console.print("Providers: " + ", ".join(PROVIDER_CATALOG), markup=False)
            while True:
                name = Prompt.ask("Provider", default=current.get("name", cfg.provider.name)).strip()
                if name in PROVIDER_CATALOG:
                    break
                console.print("Choose a provider from the list (custom endpoints use custom).")
            catalog = PROVIDER_CATALOG[name]
            same = name == current.get("name", cfg.provider.name)
            values = {**current, "name": name,
                      "base_url": Prompt.ask("API base URL", default=(current.get("base_url") if same else "")
                                             or catalog.get("base_url", "")),
                      "model": Prompt.ask("Model ID", default=(current.get("model") if same else "")
                                          or catalog.get("default_model", ""))}
            values["api_key"] = secret("Provider API key", current.get("api_key", "") if same else "")
            data["provider"] = values
        elif selected == "messaging":
            settings = MessagingSettings(cfg)
            name = Prompt.ask("Service", choices=["telegram", "discord", "slack"], default="telegram")
            guidance = {"telegram": "Create a bot with @BotFather on Telegram, then copy its token.",
                        "discord": "Discord Developer Portal → application → Bot → token. Enable Message Content Intent.",
                        "slack": "Slack API → Your Apps → OAuth bot token and Basic Information signing secret. A webhook supports notifications only."}
            console.print(guidance[name])
            values = dict(getattr(cfg.messaging, name))
            values["enabled"] = Confirm.ask("Enable this service", default=bool(values.get("enabled")))
            if values["enabled"]:
                values["bot_token"] = secret("Bot token", values.get("bot_token", ""))
                if name == "slack":
                    values["signing_secret"] = secret("Signing secret", values.get("signing_secret", ""))
                    values["webhook_url"] = secret("Incoming webhook URL (optional)", values.get("webhook_url", ""))
                    if not (values["bot_token"] or values["webhook_url"]):
                        raise ValueError("Slack needs a bot token or webhook URL")
                    if values["bot_token"] and not values["signing_secret"]:
                        raise ValueError("Slack bot messages need a signing secret")
                elif not values["bot_token"]:
                    raise ValueError("An enabled service needs a bot token")
                console.print("Optional platform IDs limit who can message the bot. Blank allows everyone who can reach it.")
                for key, label in [("allowed_users", "Allowed user IDs"), ("allowed_chats", "Allowed chat/channel IDs")]:
                    value = Prompt.ask(label + " (comma separated)", default=",".join(map(str, values.get(key, []))))
                    values[key] = [part.strip() for part in value.split(",") if part.strip()]
            settings._persist({**settings.saved, name: values})
        elif selected == "permissions":
            settings = PermissionSettings(cfg)
            console.print("auto: safe tools run; sensitive tools ask. approval: every tool asks. full_access: tools run without asking.")
            mode = Prompt.ask("Default permission mode", choices=["auto", "approval", "full_access"], default=settings.mode)
            console.print("Shell commands have the server account's access; permission prompts are not a sandbox.")
            if mode == "full_access" and not Confirm.ask("Enable Full access", default=False):
                console.print("No permission changes saved.")
                if section:
                    return
                continue
            workspace = Prompt.ask("Workspace directory", default=cfg.tools.workspace)
            data.setdefault("tools", {})["workspace"] = str(Path(workspace).expanduser().resolve())
            settings.save(mode, [])
        elif selected == "server":
            values = dict(data.get("server", {}))
            values["host"] = Prompt.ask("Bind address", default=cfg.server.host)
            while True:
                try:
                    port = int(Prompt.ask("Port", default=str(cfg.server.port)))
                    if 1 <= port <= 65535:
                        break
                except ValueError:
                    pass
                console.print("Enter a port between 1 and 65535.")
            values["port"] = port
            key = secret("Server API key (optional)", (values.get("api_keys") or [""])[0])
            if key:
                values["api_keys"] = [key, *(values.get("api_keys") or [])[1:]]
            data["server"] = values
        elif selected == "voice":
            values = dict(data.get("voice", {}))
            values["enabled"] = Confirm.ask("Enable voice", default=cfg.voice.enabled)
            values["voice"] = Prompt.ask("Voice ID", default=cfg.voice.voice)
            values["autospeak"] = Confirm.ask("Speak responses automatically", default=cfg.voice.autospeak)
            data["voice"] = values
        save_config(path, data)
        console.print(f"Saved {selected}. Restart a running Colons server to apply changes.")
        if any(key.startswith("COLONS_") and key not in ("COLONS_CONFIG",) for key in os.environ):
            console.print("Existing environment variables take precedence over config values.")
        if section:
            break
