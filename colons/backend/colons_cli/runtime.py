"""Manage a local server owned by a terminal or web session."""

import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx


def server_ready(url):
    try:
        response = httpx.get(url.rstrip("/") + "/health", timeout=0.5, trust_env=False)
        data = response.json()
        return response.is_success and data.get("healthy") is True and "provider" in data
    except (httpx.HTTPError, ValueError, AttributeError):
        return False


@contextmanager
def managed_server(url, cfg, auto_start=True):
    """Reuse existing servers; terminate only the process we launched."""
    if server_ready(url):
        yield None
        return
    target = urlparse(url)
    if not auto_start or target.hostname not in ("localhost", "127.0.0.1", "::1") or target.scheme != "http":
        raise RuntimeError(f"Cannot reach Colons at {url}. Start that server first.")
    port = target.port or 80
    log_path = Path(cfg.data_dir).expanduser() / "server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    child = None
    try:
        with os.fdopen(fd, "a") as log:
            child = subprocess.Popen(
                [sys.executable, "-m", "colons_cli.main", "start", "--host", target.hostname,
                 "--port", str(port)], stdout=log, stderr=subprocess.STDOUT,
            )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError(f"Colons could not start. See {log_path}")
            if server_ready(url):
                yield child
                return
            time.sleep(0.1)
        raise RuntimeError(f"Colons startup timed out. See {log_path}")
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
