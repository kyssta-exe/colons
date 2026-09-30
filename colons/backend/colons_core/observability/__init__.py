"""
Observability: logging setup with an in-memory ring buffer, optional rotating
file logs, request-id propagation, and process stats for debugging.
"""
import contextvars
import json
import logging
import logging.handlers
import os
import resource
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("colons_request_id", default="-")

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class RingBufferHandler(logging.Handler):
    """Keeps the last N log records in memory for /api/debug/logs."""

    def __init__(self, capacity: int = 1000, level: int = logging.NOTSET):
        super().__init__(level)
        self.capacity = capacity
        self.records: Deque[Dict] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord):
        try:
            entry = {
                "time": record.created,
                "iso": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "request_id": getattr(record, "request_id", None) or request_id_var.get(),
            }
            if record.exc_info:
                entry["exception"] = self.format(record)
            if hasattr(record, "extra_data"):
                entry["data"] = record.extra_data
            with self._lock:
                self.records.append(entry)
        except Exception:
            self.handleError(record)

    def tail(self, level: int = logging.NOTSET, limit: int = 100,
             logger_prefix: Optional[str] = None) -> List[Dict]:
        with self._lock:
            records = list(self.records)
        out = []
        for r in reversed(records):
            if _LEVELS.get(r["level"], 0) < level:
                continue
            if logger_prefix and not r["logger"].startswith(logger_prefix):
                continue
            out.append(r)
            if len(out) >= limit:
                break
        return out

    def clear(self):
        with self._lock:
            self.records.clear()


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line - friendly for log shippers."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": record.created,
            "iso": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None) or request_id_var.get(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "extra_data"):
            entry["data"] = record.extra_data
        return json.dumps(entry, default=str)


_ring: Optional[RingBufferHandler] = None
_configured = False


def setup_logging(
    level: str = "INFO",
    file_path: Optional[str] = None,
    max_bytes: int = 10_000_000,
    backups: int = 3,
    buffer_size: int = 1000,
    json_format: bool = False,
    quiet_loggers: Optional[List[str]] = None,
) -> RingBufferHandler:
    """Configure root logging. Safe to call multiple times."""
    global _ring, _configured

    root = logging.getLogger()
    root.setLevel(_LEVELS.get(level.upper(), logging.INFO))

    if _configured and _ring is not None:
        # Reconfiguration keeps handler identity and the most recent records.
        with _ring._lock:
            _ring.capacity = buffer_size
            _ring.records = deque(_ring.records, maxlen=buffer_size)
        return _ring

    if json_format:
        formatter: logging.Formatter = JsonFormatter()
    else:
        fmt = "%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s"
        formatter = logging.Formatter(fmt, datefmt="%H:%M:%S")

    # Console
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    console.addFilter(RequestIdFilter())
    root.addHandler(console)

    # File (optional, rotating)
    if file_path:
        try:
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                file_path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            file_handler.addFilter(RequestIdFilter())
            root.addHandler(file_handler)
        except Exception as e:
            root.warning(f"Could not open log file {file_path}: {e}")

    # Ring buffer for the debug API
    _ring = RingBufferHandler(capacity=buffer_size)
    _ring.setFormatter(formatter)
    root.addHandler(_ring)

    for name in (quiet_loggers or ["httpx", "httpcore", "uvicorn.access"]):
        logging.getLogger(name).setLevel(logging.WARNING)

    _configured = True
    return _ring


def get_ring() -> Optional[RingBufferHandler]:
    return _ring


def set_level(level: str):
    logging.getLogger().setLevel(_LEVELS.get(level.upper(), logging.INFO))


def process_stats() -> Dict[str, Any]:
    """Process/runtime stats for the debug endpoint."""
    import asyncio

    usage = resource.getrusage(resource.RUSAGE_SELF)
    stats: Dict[str, Any] = {
        "pid": os.getpid(),
        "uptime_seconds": round(time.time() - _start_time, 1),
        "threads": threading.active_count(),
        "cpu_user_seconds": round(usage.ru_utime, 2),
        "cpu_system_seconds": round(usage.ru_stime, 2),
        "max_rss_mb": round(usage.ru_maxrss / 1024, 1),
    }
    try:
        loop = asyncio.get_running_loop()
        stats["asyncio_tasks"] = len(asyncio.all_tasks(loop))
    except RuntimeError:
        stats["asyncio_tasks"] = 0
    try:
        pid = os.getpid()
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    stats["rss_mb"] = round(int(line.split()[1]) / 1024, 1)
                    break
    except Exception:
        pass
    return stats


_start_time = time.time()


def redact(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return "***"
    return value[:keep] + "***"


RELEVANT_ENV_PREFIXES = (
    "COLONS_",
    "OPENAI_", "AZURE_OPENAI_", "ANTHROPIC_", "GEMINI_", "GOOGLE_",
    "OPENROUTER_", "GROQ_", "TOGETHER_", "DEEPSEEK_", "XAI_", "MISTRAL_",
    "PERPLEXITY_", "CEREBRAS_", "SAMBANOVA_", "NVIDIA_", "GITHUB_",
    "TELEGRAM_", "DISCORD_", "SLACK_", "REDIS_",
)


def redacted_env(prefixes: tuple = RELEVANT_ENV_PREFIXES) -> Dict[str, str]:
    """Environment variables with secrets redacted (provider keys included)."""
    secret_markers = ("KEY", "TOKEN", "SECRET", "PASSWORD", "WEBHOOK")
    out: Dict[str, str] = {}
    for key, value in sorted(os.environ.items()):
        if not any(key.startswith(p) for p in prefixes):
            continue
        if any(marker in key.upper() for marker in secret_markers) and value:
            out[key] = redact(value)
        else:
            out[key] = value
    return out
