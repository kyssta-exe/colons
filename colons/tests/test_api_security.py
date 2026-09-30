"""Browser-origin and static-file boundary regressions."""

import colons_api.main as api_main
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api_main.CONFIG.server, "api_keys", [])
    monkeypatch.setattr(api_main.CONFIG.server, "rate_limit_per_minute", 0)
    monkeypatch.setattr(api_main.CONFIG.server, "cors_origins", ["http://localhost:5173"])
    return TestClient(api_main.app)


def test_foreign_browser_cannot_execute_tools(client):
    response = client.post("/api/tools/execute", headers={"Origin": "https://evil.example"},
                           json={"tool": "shell", "arguments": {"command": "echo denied"}})
    assert response.status_code == 403
    assert response.json()["detail"] == "Untrusted browser origin"


@pytest.mark.parametrize("origin", ["http://testserver", "http://localhost:5173", None])
def test_same_origin_allowed_ui_and_cli_can_read_providers(client, origin):
    headers = {"Origin": origin} if origin else {}
    assert client.get("/api/providers", headers=headers).status_code == 200


def test_cli_still_requires_configured_api_key(client, monkeypatch):
    monkeypatch.setattr(api_main.CONFIG.server, "api_keys", ["test-secret"])
    assert client.get("/api/tools").status_code == 401


def test_foreign_browser_cannot_open_chat_socket(client):
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/ws/chat", headers={"Origin": "https://evil.example"}):
            pass
    assert error.value.code == 4403


@pytest.mark.parametrize("origin", ["http://testserver", "http://localhost:5173", None])
def test_allowed_chat_socket_can_ping(client, origin):
    headers = {"Origin": origin} if origin else {}
    with client.websocket_connect("/ws/chat", headers=headers) as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


@pytest.fixture
def static_client(tmp_path, monkeypatch):
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("Colons UI")
    (ui / "favicon.svg").write_text("safe icon")
    secret = tmp_path / "secret.txt"
    secret.write_text("private data")
    (ui / "escape.txt").symlink_to(secret)
    monkeypatch.setattr(api_main, "_WEB_DIST", str(ui))
    app = FastAPI()
    app.get("/{full_path:path}")(api_main.spa_fallback)
    return TestClient(app)


@pytest.mark.parametrize("path", ["/%2e%2e%2fsecret.txt", "/escape.txt"])
def test_static_server_rejects_traversal_and_escaping_symlinks(static_client, path):
    response = static_client.get(path)
    assert response.status_code == 404
    assert "private data" not in response.text


def test_static_server_serves_ui_files_and_client_routes(static_client):
    assert static_client.get("/favicon.svg").text == "safe icon"
    assert static_client.get("/settings/usage").text == "Colons UI"
