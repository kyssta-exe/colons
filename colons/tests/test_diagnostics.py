"""Tests for the doctor diagnostics and the observability/logging module."""
import asyncio
import logging

import pytest

from colons_core.config import ColonsConfig
from colons_core.diagnostics import FAIL, PASS, WARN, run_doctor
from colons_core.observability import (
    RingBufferHandler,
    process_stats,
    redact,
    redacted_env,
    setup_logging,
)


# --------------------------------------------------------------------------- #
# Doctor
# --------------------------------------------------------------------------- #

@pytest.fixture
def config(tmp_path):
    cfg = ColonsConfig()
    cfg.data_dir = str(tmp_path / "data")
    cfg.memory.store_url = str(tmp_path / "memory.db")
    cfg.tools.workspace = str(tmp_path)
    cfg.provider.name = "ollama"
    cfg.provider.base_url = "http://127.0.0.1:59999"  # deliberately unreachable
    cfg.server.port = 59998  # free port
    return cfg


@pytest.mark.asyncio
async def test_doctor_reports_required_checks(config):
    report = await run_doctor(config=config, manager=None, check_provider=False)
    names = [c.name for c in report.checks]
    assert "python" in names
    assert "dependencies.required" in names
    assert "config" in names
    assert "config.data_dir" in names
    assert "config.workspace" in names
    assert "provider" in names
    assert "memory.store" in names
    assert "server.port" in names
    assert "system.disk" in names


@pytest.mark.asyncio
async def test_doctor_python_check_passes(config):
    report = await run_doctor(config=config, check_provider=False)
    python_check = next(c for c in report.checks if c.name == "python")
    assert python_check.status == PASS


@pytest.mark.asyncio
async def test_doctor_data_dir_check_tracks_writability(config, tmp_path):
    report = await run_doctor(config=config, check_provider=False)
    data_check = next(c for c in report.checks if c.name == "config.data_dir")
    assert data_check.status == PASS


@pytest.mark.asyncio
async def test_doctor_warns_without_api_keys(config):
    report = await run_doctor(config=config, check_provider=False)
    auth = next(c for c in report.checks if c.name == "security.auth")
    assert auth.status == WARN


@pytest.mark.asyncio
async def test_doctor_flags_unreachable_local_provider(config):
    report = await run_doctor(config=config, manager=None, check_provider=False)
    provider = next(c for c in report.checks if c.name == "provider")
    # Local base URL not reachable -> failure with a hint
    assert provider.status == FAIL
    assert "cannot reach" in provider.detail


@pytest.mark.asyncio
async def test_doctor_with_live_manager(config, tmp_path):
    from colons_api.manager import AgentManager

    config.scheduler.enabled = False
    manager = AgentManager(config)
    report = await run_doctor(config=config, manager=manager, check_provider=False)

    store_check = next(c for c in report.checks if c.name == "memory.store")
    assert store_check.status == PASS
    assert "SQLiteStore" in store_check.detail

    cache_check = next(c for c in report.checks if c.name == "memory.cache")
    assert cache_check.status == PASS


@pytest.mark.asyncio
async def test_doctor_report_serializes(config):
    report = await run_doctor(config=config, check_provider=False)
    data = report.to_dict()
    assert "ok" in data
    assert "summary" in data
    assert isinstance(data["checks"], list)
    assert all({"name", "status", "detail"} <= set(c) for c in data["checks"])


# --------------------------------------------------------------------------- #
# Logging / observability
# --------------------------------------------------------------------------- #

def test_ring_buffer_captures_records():
    ring = RingBufferHandler(capacity=10)
    ring.setLevel(logging.DEBUG)
    logger = logging.getLogger("test.ring")
    logger.addHandler(ring)
    logger.setLevel(logging.DEBUG)

    logger.info("hello world")
    logger.error("something broke")

    records = ring.tail(limit=10)
    assert len(records) == 2
    assert records[0]["message"] == "something broke"  # newest first
    assert records[0]["level"] == "ERROR"

    logger.removeHandler(ring)


def test_ring_buffer_level_filter():
    ring = RingBufferHandler(capacity=10)
    logger = logging.getLogger("test.ring.filter")
    logger.addHandler(ring)
    logger.setLevel(logging.DEBUG)

    logger.debug("noise")
    logger.warning("warning here")
    logger.error("error here")

    warnings_up = ring.tail(level=logging.WARNING, limit=10)
    assert len(warnings_up) == 2
    assert all(r["level"] in ("WARNING", "ERROR") for r in warnings_up)

    logger.removeHandler(ring)


def test_ring_buffer_capacity_and_clear():
    ring = RingBufferHandler(capacity=3)
    logger = logging.getLogger("test.ring.cap")
    logger.addHandler(ring)
    logger.setLevel(logging.DEBUG)
    for i in range(10):
        logger.info(f"message {i}")
    assert len(ring.tail(limit=100)) == 3
    ring.clear()
    assert ring.tail(limit=100) == []
    logger.removeHandler(ring)


def test_ring_buffer_logger_filter():
    ring = RingBufferHandler(capacity=50)
    a = logging.getLogger("app.alpha")
    b = logging.getLogger("app.beta")
    for logger in (a, b):
        logger.addHandler(ring)
        logger.setLevel(logging.DEBUG)
    a.info("from alpha")
    b.info("from beta")

    only_alpha = ring.tail(logger_prefix="app.alpha")
    assert len(only_alpha) == 1
    assert only_alpha[0]["logger"] == "app.alpha"

    for logger in (a, b):
        logger.removeHandler(ring)


def test_setup_logging_installs_ring():
    ring = setup_logging(level="DEBUG", buffer_size=50)
    assert ring is not None
    assert ring.capacity == 50


def test_process_stats_shape():
    stats = process_stats()
    assert "pid" in stats
    assert "uptime_seconds" in stats
    assert "threads" in stats


def test_json_formatter_outputs_valid_json():
    import json as _json

    from colons_core.observability import JsonFormatter

    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="colons.test", level=logging.WARNING, pathname=__file__, lineno=1,
        msg="hello %s", args=("world",), exc_info=None,
    )
    parsed = _json.loads(formatter.format(record))
    assert parsed["level"] == "WARNING"
    assert parsed["message"] == "hello world"
    assert parsed["logger"] == "colons.test"
    assert "iso" in parsed


def test_redact():
    assert redact("") == ""
    assert redact("abc") == "***"
    assert redact("sk-1234567890") == "sk-1***"


def test_redacted_env_includes_provider_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-provider-secret")
    env = redacted_env()
    assert "OPENAI_API_KEY" in env
    assert env["OPENAI_API_KEY"].endswith("***")


def test_redacted_env(monkeypatch):
    monkeypatch.setenv("COLONS_PROVIDER", "ollama")
    monkeypatch.setenv("COLONS_API_KEY", "sk-secret-value")
    env = redacted_env()
    assert env["COLONS_PROVIDER"] == "ollama"
    assert env["COLONS_API_KEY"].endswith("***")
    assert "secret" not in env["COLONS_API_KEY"]


def test_logging_reconfiguration_resizes_existing_ring():
    ring = setup_logging(buffer_size=100)
    ring.emit(logging.LogRecord('colons.resize', logging.INFO, __file__, 1, 'keep me', (), None))
    resized = setup_logging(buffer_size=20)
    assert resized is ring
    assert resized.capacity == 20
    assert resized.records.maxlen == 20
    assert any(record['message'] == 'keep me' for record in resized.tail())
