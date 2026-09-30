"""
Configuration for Colons.
Loads from environment variables and an optional YAML/JSON config file.
Zero hard dependencies: YAML is optional.
"""
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG_PATHS = [
    "./colons.yaml",
    "./colons.yml",
    "./colons.json",
    "~/.config/colons/config.yaml",
    "~/.colons/config.yaml",
]


def _load_file(path: str) -> Dict:
    p = Path(path).expanduser()
    if not p.exists():
        return {}
    text = p.read_text(encoding="utf-8")
    if p.suffix in (".yaml", ".yml"):
        try:
            import yaml
            return yaml.safe_load(text) or {}
        except ImportError as e:
            raise RuntimeError("PyYAML is required to read YAML config. `pip install pyyaml`") from e
    return json.loads(text)


def _env(key: str, default: Any = None) -> Any:
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    val = os.environ.get(key)
    try:
        return int(val) if val is not None else default
    except ValueError:
        return default


@dataclass
class ProviderConfig:
    name: str = "ollama"
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096


@dataclass
class MemoryConfig:
    # SQLite path by default; a postgres:// URL switches to pgvector backend
    store_url: str = "colons.db"
    cache_url: str = ""  # empty => in-memory; redis://... for Redis
    embedding_model: str = ""
    embedding_dim: int = 1536
    prune_after: int = 10000


@dataclass
class ToolsConfig:
    workspace: str = "."
    shell_allowlist: List[str] = field(default_factory=list)
    auto_approve: bool = False
    global_timeout: float = 120.0
    disabled: List[str] = field(default_factory=list)


@dataclass
class VoiceConfig:
    enabled: bool = False
    tts_engine: str = "edge"  # edge | pyttsx3 | null
    voice: str = "en-US-AriaNeural"
    rate: str = "+0%"
    volume: str = "+0%"
    pitch: str = "+0Hz"
    autospeak: bool = False


@dataclass
class AgentConfig:
    name: str = "Colons"
    avatar: str = ""  # empty => deterministic assignment
    system_prompt: str = ""
    proactive: bool = True
    proactive_interval: int = 300
    max_concurrent_tasks: int = 3
    max_iterations: int = 15
    context_messages: int = 40


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: List[str] = field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])
    api_keys: List[str] = field(default_factory=list)  # empty => no auth (local use)
    rate_limit_per_minute: int = 120


@dataclass
class UIConfig:
    title: str = "Colons"
    primary_color: str = "#10a37f"
    theme: str = "dark"  # dark | light | system


@dataclass
class SchedulerConfig:
    enabled: bool = True
    tick_interval: int = 15          # seconds between due-schedule checks
    timezone: str = "UTC"            # default timezone for cron expressions
    max_concurrent: int = 3


@dataclass
class MessagingConfig:
    # Each adapter is a dict; see .env.example / README for keys.
    telegram: Dict[str, Any] = field(default_factory=dict)
    discord: Dict[str, Any] = field(default_factory=dict)
    slack: Dict[str, Any] = field(default_factory=dict)
    webhooks: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    user_prefix: str = "msg"         # maps platform users to Colons user ids


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = ""                   # optional rotating log file path
    max_bytes: int = 10_000_000
    backups: int = 3
    buffer_size: int = 1000          # in-memory ring for /api/debug/logs
    json_format: bool = False


@dataclass
class ColonsConfig:
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    messaging: MessagingConfig = field(default_factory=MessagingConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    # Bot-to-bot messaging + cross-instance peers
    bot_messaging: bool = True
    peers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    peers_timeout: float = 120.0
    data_dir: str = "./data"

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls, path: Optional[str] = None) -> "ColonsConfig":
        config: Dict = {}
        candidates = [path] if path else [os.environ.get("COLONS_CONFIG")] + DEFAULT_CONFIG_PATHS
        for candidate in candidates:
            if not candidate:
                continue
            data = _load_file(candidate)
            if data:
                config = data
                break

        cfg = cls()

        # Provider
        p = config.get("provider", {})
        cfg.provider = ProviderConfig(
            name=_env("COLONS_PROVIDER", p.get("name", "ollama")),
            api_key=_env("COLONS_API_KEY", p.get("api_key", "")),
            base_url=_env("COLONS_BASE_URL", p.get("base_url", "")),
            model=_env("COLONS_MODEL", p.get("model", "")),
            temperature=float(p.get("temperature", 0.7)),
            max_tokens=_env_int("COLONS_MAX_TOKENS", int(p.get("max_tokens", 4096))),
        )

        # Memory
        m = config.get("memory", {})
        cfg.memory = MemoryConfig(
            store_url=_env("COLONS_STORE_URL", m.get("store_url", "colons.db")),
            cache_url=_env("COLONS_CACHE_URL", _env("REDIS_URL", m.get("cache_url", ""))),
            embedding_model=m.get("embedding_model", ""),
            embedding_dim=int(m.get("embedding_dim", 1536)),
            prune_after=int(m.get("prune_after", 10000)),
        )

        # Tools
        t = config.get("tools", {})
        cfg.tools = ToolsConfig(
            workspace=_env("COLONS_WORKSPACE", t.get("workspace", ".")),
            shell_allowlist=t.get("shell_allowlist", []),
            auto_approve=_env_bool("COLONS_AUTO_APPROVE", bool(t.get("auto_approve", False))),
            global_timeout=float(t.get("global_timeout", 120.0)),
            disabled=t.get("disabled", []),
        )

        # Voice
        v = config.get("voice", {})
        cfg.voice = VoiceConfig(
            enabled=_env_bool("COLONS_VOICE_ENABLED", bool(v.get("enabled", False))),
            tts_engine=_env("COLONS_TTS_ENGINE", v.get("tts_engine", "edge")),
            voice=_env("COLONS_TTS_VOICE", v.get("voice", "en-US-AriaNeural")),
            rate=v.get("rate", "+0%"),
            volume=v.get("volume", "+0%"),
            pitch=v.get("pitch", "+0Hz"),
            autospeak=_env_bool("COLONS_AUTOSPEAK", bool(v.get("autospeak", False))),
        )

        # Agent
        a = config.get("agent", {})
        cfg.agent = AgentConfig(
            name=a.get("name", "Colons"),
            avatar=a.get("avatar", ""),
            system_prompt=a.get("system_prompt", ""),
            proactive=_env_bool("COLONS_PROACTIVE", bool(a.get("proactive", True))),
            proactive_interval=int(a.get("proactive_interval", 300)),
            max_concurrent_tasks=int(a.get("max_concurrent_tasks", 3)),
            max_iterations=int(a.get("max_iterations", 15)),
            context_messages=int(a.get("context_messages", 40)),
        )

        # Server
        s = config.get("server", {})
        api_keys = _env("COLONS_API_KEYS", "")
        cfg.server = ServerConfig(
            host=_env("COLONS_HOST", s.get("host", "0.0.0.0")),
            port=_env_int("COLONS_PORT", int(s.get("port", 8000))),
            cors_origins=[o.strip() for o in _env("COLONS_CORS_ORIGINS", "").split(",") if o.strip()]
            or s.get("cors_origins", ["http://localhost:5173", "http://127.0.0.1:5173"]),
            api_keys=[k.strip() for k in api_keys.split(",") if k.strip()] or s.get("api_keys", []),
            rate_limit_per_minute=int(s.get("rate_limit_per_minute", 120)),
        )

        # UI
        u = config.get("ui", {})
        cfg.ui = UIConfig(
            title=u.get("title", "Colons"),
            primary_color=u.get("primary_color", "#10a37f"),
            theme=u.get("theme", "dark"),
        )

        # Scheduler
        sch = config.get("scheduler", {})
        cfg.scheduler = SchedulerConfig(
            enabled=_env_bool("COLONS_SCHEDULER_ENABLED", bool(sch.get("enabled", True))),
            tick_interval=_env_int("COLONS_SCHEDULER_TICK", int(sch.get("tick_interval", 15))),
            timezone=_env("COLONS_TIMEZONE", sch.get("timezone", "UTC")),
            max_concurrent=int(sch.get("max_concurrent", 3)),
        )

        # Messaging: config file values, overridable/wireable via environment
        msg = config.get("messaging", {})
        cfg.messaging = MessagingConfig(
            telegram=dict(msg.get("telegram", {})),
            discord=dict(msg.get("discord", {})),
            slack=dict(msg.get("slack", {})),
            webhooks=dict(msg.get("webhooks", {})),
            user_prefix=msg.get("user_prefix", "msg"),
        )
        tg_token = _env("TELEGRAM_BOT_TOKEN", "")
        if tg_token:
            cfg.messaging.telegram = {**cfg.messaging.telegram,
                                      "enabled": True, "bot_token": tg_token}
        dc_token = _env("DISCORD_BOT_TOKEN", "")
        if dc_token:
            cfg.messaging.discord = {**cfg.messaging.discord,
                                     "enabled": True, "bot_token": dc_token}
        slack_webhook = _env("SLACK_WEBHOOK_URL", "")
        slack_token = _env("SLACK_BOT_TOKEN", "")
        slack_secret = _env("SLACK_SIGNING_SECRET", "")
        if slack_webhook or slack_token:
            cfg.messaging.slack = {
                **cfg.messaging.slack, "enabled": True,
                **({"webhook_url": slack_webhook} if slack_webhook else {}),
                **({"bot_token": slack_token} if slack_token else {}),
                **({"signing_secret": slack_secret} if slack_secret else {}),
            }

        # Bots & peers
        cfg.bot_messaging = _env_bool("COLONS_BOT_MESSAGING",
                                      bool(config.get("bot_messaging", True)))
        cfg.peers = dict(config.get("peers", {}))
        cfg.peers_timeout = float(config.get("peers_timeout", 120.0))

        # Logging
        lg = config.get("logging", {})
        cfg.logging = LoggingConfig(
            level=_env("COLONS_LOG_LEVEL", lg.get("level", "INFO")),
            file=_env("COLONS_LOG_FILE", lg.get("file", "")),
            max_bytes=int(lg.get("max_bytes", 10_000_000)),
            backups=int(lg.get("backups", 3)),
            buffer_size=int(lg.get("buffer_size", 1000)),
            json_format=_env_bool("COLONS_LOG_JSON", bool(lg.get("json_format", False))),
        )

        cfg.data_dir = _env("COLONS_DATA_DIR", config.get("data_dir", "./data"))
        Path(cfg.data_dir).mkdir(parents=True, exist_ok=True)
        return cfg

    def to_dict(self, redact_secrets: bool = True) -> Dict:
        data = asdict(self)
        if redact_secrets:
            if data["provider"]["api_key"]:
                data["provider"]["api_key"] = "***"
            if data["server"]["api_keys"]:
                data["server"]["api_keys"] = ["***"] * len(data["server"]["api_keys"])
            for key in ("telegram", "discord", "slack"):
                adapter = data["messaging"].get(key, {})
                for secret in ("bot_token", "webhook_url", "signing_secret", "incoming_token"):
                    if adapter.get(secret):
                        adapter[secret] = "***"
            for _name, adapter in (data["messaging"].get("webhooks") or {}).items():
                for secret in ("outgoing_url", "incoming_token"):
                    if adapter.get(secret):
                        adapter[secret] = "***"
        return data

    def save(self, path: str):
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.suffix in (".yaml", ".yml"):
            try:
                import yaml
                p.write_text(yaml.safe_dump(self.to_dict(redact_secrets=False), sort_keys=False))
                return
            except ImportError:
                p = p.with_suffix(".json")
        p.write_text(json.dumps(self.to_dict(redact_secrets=False), indent=2))
