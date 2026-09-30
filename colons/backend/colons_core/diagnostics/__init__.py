"""
Colons doctor: environment, configuration, dependency, provider, storage,
and integration diagnostics. Returns a structured report.
"""
import asyncio
import importlib.util
import os
import platform
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

PASS = "pass"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    hint: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "name": self.name, "status": self.status, "detail": self.detail,
            "hint": self.hint, "data": self.data,
        }


@dataclass
class DoctorReport:
    checks: List[Check] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    duration_ms: float = 0.0

    def add(self, check: Check):
        self.checks.append(check)

    @property
    def ok(self) -> bool:
        return not any(c.status == FAIL for c in self.checks)

    def summary(self) -> Dict[str, int]:
        out = {PASS: 0, WARN: 0, FAIL: 0, SKIP: 0}
        for c in self.checks:
            out[c.status] = out.get(c.status, 0) + 1
        return out

    def to_dict(self) -> Dict:
        return {
            "ok": self.ok,
            "summary": self.summary(),
            "duration_ms": round(self.duration_ms, 2),
            "checks": [c.to_dict() for c in self.checks],
        }


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


async def _tcp_check(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def run_doctor(
    config=None,
    manager=None,
    check_provider: bool = True,
    provider_timeout: float = 6.0,
) -> DoctorReport:
    """
    Run all diagnostics. `config` is a ColonsConfig; `manager` is optional
    (an AgentManager) to also verify live subsystems.
    """
    report = DoctorReport()

    # ---------------- Runtime ---------------- #
    py = sys.version_info
    report.add(Check(
        "python",
        PASS if py >= (3, 10) else FAIL,
        f"Python {platform.python_version()} on {platform.system()} {platform.release()}",
        "Colons requires Python 3.10+" if py < (3, 10) else "",
        {"version": platform.python_version(), "implementation": platform.python_implementation()},
    ))

    # ---------------- Dependencies ---------------- #
    required = {
        "httpx": "HTTP client (core)",
        "fastapi": "API server",
        "uvicorn": "ASGI server",
        "pydantic": "data models",
        "websockets": "streaming + messaging",
    }
    optional = {
        "rich": "pretty CLI output",
        "edge_tts": "Microsoft Edge TTS voices",
        "pyttsx3": "offline TTS fallback",
        "tiktoken": "accurate token counting",
        "yaml": "YAML config files",
        "redis": "Redis cache backend",
        "psycopg2": "Postgres memory backend",
    }

    missing_required = [m for m in required if not _has_module(m)]
    report.add(Check(
        "dependencies.required",
        PASS if not missing_required else FAIL,
        "all present" if not missing_required else f"missing: {', '.join(missing_required)}",
        f"pip install {' '.join(missing_required)}" if missing_required else "",
        {"present": [m for m in required if _has_module(m)], "missing": missing_required},
    ))

    missing_optional = [m for m in optional if not _has_module(m)]
    report.add(Check(
        "dependencies.optional",
        PASS if not missing_optional else WARN,
        "all present" if not missing_optional else f"missing: {', '.join(missing_optional)}",
        "pip install -e '.[full]' to enable the extras",
        {"present": [m for m in optional if _has_module(m)], "missing": missing_optional},
    ))

    # ---------------- Config ---------------- #
    if config is not None:
        report.add(Check(
            "config",
            PASS,
            f"provider={config.provider.name} model={config.provider.model or '(default)'}",
            "",
            config.to_dict(),
        ))
        # Data dir
        data_dir = Path(config.data_dir)
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            probe = data_dir / ".colons_write_test"
            probe.write_text("ok")
            probe.unlink()
            report.add(Check("config.data_dir", PASS, f"writable: {data_dir}"))
        except Exception as e:
            report.add(Check("config.data_dir", FAIL, str(e), f"make {data_dir} writable"))

        # Workspace
        ws = Path(config.tools.workspace).resolve()
        report.add(Check(
            "config.workspace",
            PASS if ws.is_dir() and os.access(ws, os.W_OK) else FAIL,
            f"{ws} ({'writable' if os.access(ws, os.W_OK) else 'not writable'})",
            "set COLONS_WORKSPACE to a writable directory",
        ))

        # Auth posture
        if config.server.api_keys:
            report.add(Check("security.auth", PASS,
                             f"{len(config.server.api_keys)} API key(s) configured"))
        else:
            report.add(Check("security.auth", WARN,
                             "no API keys configured - server is open",
                             "set COLONS_API_KEYS if the server is reachable beyond localhost"))

    # ---------------- Provider ---------------- #
    if config is not None:
        await _check_provider(config, report, check_provider, provider_timeout)

    # ---------------- Storage ---------------- #
    await _check_storage(config, manager, report)

    # ---------------- Cache ---------------- #
    _check_cache(manager, report)

    # ---------------- Voice ---------------- #
    if manager is not None and manager.tts is not None:
        report.add(Check(
            "voice.tts",
            PASS if manager.tts.available else WARN,
            f"engine={manager.tts.engine_name} available={manager.tts.available}",
            "pip install edge-tts for Microsoft voices",
        ))
    elif config is not None:
        report.add(Check(
            "voice.tts",
            SKIP,
            "voice disabled in config",
            "set COLONS_VOICE_ENABLED=true to enable",
        ))

    # ---------------- Scheduler / Messaging (live) ---------------- #
    if manager is not None:
        if getattr(manager, "scheduler", None) is not None:
            st = manager.scheduler.status()
            report.add(Check(
                "scheduler",
                PASS if st["running"] else WARN,
                f"{st['enabled']}/{st['total']} schedules enabled, running={st['running']}",
            ))
        if getattr(manager, "messaging", None) is not None:
            statuses = manager.messaging.status()
            connected = [a for a in statuses if a.get("connected")]
            configured = [a for a in statuses if a.get("configured")]
            status = PASS if (not configured or connected) else FAIL
            report.add(Check(
                "messaging",
                status,
                f"{len(connected)}/{len(configured)} configured adapters connected",
                "check bot tokens / network access",
                {"adapters": statuses},
            ))

    # ---------------- Network / port ---------------- #
    if config is not None:
        host = "127.0.0.1"
        port = config.server.port
        in_use = await _tcp_check(host, port, timeout=0.5)
        report.add(Check(
            "server.port",
            WARN if in_use else PASS,
            f"port {port} {'is in use (a Colons server may already be running)' if in_use else 'is free'}",
            "use COLONS_PORT to change it" if in_use else "",
        ))

    # ---------------- Disk ---------------- #
    if config is not None:
        try:
            usage = shutil.disk_usage(config.data_dir)
            free_gb = usage.free / (1024 ** 3)
            report.add(Check(
                "system.disk",
                PASS if free_gb > 1 else WARN,
                f"{free_gb:.1f} GB free",
                "free up disk space" if free_gb <= 1 else "",
            ))
        except Exception as e:
            report.add(Check("system.disk", WARN, str(e)))

    report.duration_ms = (time.time() - report.started_at) * 1000
    return report


async def _check_provider(config, report: DoctorReport, check_provider: bool, timeout: float):
    provider_cfg = config.provider
    name = provider_cfg.name
    base_url = provider_cfg.base_url

    data = {"name": name, "base_url": base_url, "model": provider_cfg.model}
    problems: List[str] = []
    hints: List[str] = []

    if not provider_cfg.api_key and name not in ("ollama", "local", "lmstudio", "vllm",
                                                 "llamacpp", "localai", "jan", "koboldcpp",
                                                 "textgen", "custom"):
        problems.append("no API key set")
        hints.append("set COLONS_API_KEY")

    # Local endpoint reachability
    if base_url:
        parsed = urlparse(base_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        reachable = await _tcp_check(host, port, timeout=2.0)
        data["reachable"] = reachable
        if not reachable and (host in ("localhost", "127.0.0.1") or "ollama" in name):
            problems.append(f"cannot reach {host}:{port}")

    # Optional: list models via the adapter
    if check_provider and not problems:
        try:
            from ..providers import ProviderFactory
            provider = ProviderFactory.create(
                provider=name, api_key=provider_cfg.api_key or None,
                base_url=provider_cfg.base_url or None,
            )
            models = await asyncio.wait_for(provider.list_models(), timeout=timeout)
            data["models_available"] = len(models)
            if models:
                data["sample_models"] = models[:5]
                if provider_cfg.model and provider_cfg.model not in models:
                    report.add(Check(
                        "provider", WARN,
                        f"{name} reachable, but configured model '{provider_cfg.model}' not in list",
                        "check the model name or pull it locally",
                        data,
                    ))
                    return
        except asyncio.TimeoutError:
            report.add(Check("provider", WARN, f"{name} model listing timed out",
                             "network may be slow or blocked", data))
            return
        except Exception as e:
            report.add(Check("provider", WARN, f"{name} configured but not reachable: {e}",
                             hints[0] if hints else "verify credentials and network", data))
            return

    if problems:
        report.add(Check("provider", FAIL,
                         f"{name}: " + "; ".join(problems),
                         " ".join(hints), data))
    else:
        report.add(Check("provider", PASS,
                         f"{name} configured"
                         + (f" ({data.get('models_available')} models)" if data.get("models_available") else ""),
                         "", data))


async def _check_storage(config, manager, report: DoctorReport):
    try:
        if manager is not None:
            store = manager.store
            count = store.count()
            backend = type(store).__name__
            entry_id = None
            if backend == "SQLiteStore":
                from ..memory.store import MemoryEntry
                entry = store.store(MemoryEntry(user_id="__doctor__", content="probe", embedding=[0.0] * 8))
                entry_id = entry.id
                store.delete("__doctor__", memory_id=entry_id)
            report.add(Check("memory.store", PASS, f"{backend} operational ({count} entries)",
                             "", {"backend": backend, "entries": count}))
            return
        if config is not None:
            from ..memory import create_store
            store = create_store(config.memory.store_url)
            count = store.count()
            report.add(Check("memory.store", PASS,
                             f"{type(store).__name__} operational ({count} entries)"))
    except Exception as e:
        report.add(Check("memory.store", FAIL, str(e),
                         "check COLONS_STORE_URL and database permissions"))


def _check_cache(manager, report: DoctorReport):
    if manager is None or manager.cache is None:
        report.add(Check("memory.cache", SKIP, "no cache initialized"))
        return
    try:
        cache = manager.cache
        cache.set("__doctor__", {"ok": True}, ttl=10)
        ok = cache.get("__doctor__") == {"ok": True}
        cache.delete("__doctor__")
        stats = cache.stats()
        report.add(Check("memory.cache", PASS if ok else FAIL,
                         f"{stats.get('backend')} backend operational",
                         "", stats))
    except Exception as e:
        report.add(Check("memory.cache", FAIL, str(e),
                         "check COLONS_CACHE_URL (Redis) or leave it empty"))
