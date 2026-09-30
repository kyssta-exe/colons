"""
End-to-end integration test:
spin up a fake OpenAI-compatible server, point Colons at it, run a full
agent chat over the WebSocket, verify streaming + tool calls + memory.
"""
import asyncio
import json
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("websockets")


class FakeOpenAIServer:
    """Minimal OpenAI-compatible /chat/completions server with scripted replies."""

    def __init__(self):
        self.requests = []
        self.responses = []

    def script(self, *responses):
        self.responses.extend(responses)

    def build_app(self):
        from fastapi import FastAPI, Request
        from fastapi.responses import StreamingResponse

        app = FastAPI()

        @app.post("/v1/chat/completions")
        async def completions(request: Request):
            body = await request.json()
            self.requests.append(body)
            idx = min(len(self.requests) - 1, len(self.responses) - 1)
            scripted = self.responses[idx] if self.responses else {"content": "ok"}

            if body.get("stream"):
                async def gen():
                    chunk_base = {"id": "chatcmpl-t", "object": "chat.completion.chunk",
                                  "created": int(time.time()), "model": body["model"]}
                    yield "data: " + json.dumps({**chunk_base, "choices": [
                        {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}) + "\n\n"

                    if scripted.get("tool_calls"):
                        for i, tc in enumerate(scripted["tool_calls"]):
                            yield "data: " + json.dumps({**chunk_base, "choices": [{"index": 0, "delta": {
                                "tool_calls": [{"index": i, "id": tc["id"], "type": "function",
                                                "function": {"name": tc["name"], "arguments": ""}}]},
                                "finish_reason": None}]}) + "\n\n"
                            args = json.dumps(tc.get("arguments", {}))
                            for j in range(0, len(args), 12):
                                yield "data: " + json.dumps({**chunk_base, "choices": [{"index": 0, "delta": {
                                    "tool_calls": [{"index": i, "function": {"arguments": args[j:j+12]}}]},
                                    "finish_reason": None}]}) + "\n\n"
                        finish = "tool_calls"
                    else:
                        for j in range(0, len(scripted.get("content", "")), 10):
                            piece = scripted["content"][j:j+10]
                            yield "data: " + json.dumps({**chunk_base, "choices": [
                                {"index": 0, "delta": {"content": piece}, "finish_reason": None}]}) + "\n\n"
                        finish = "stop"

                    yield "data: " + json.dumps({**chunk_base,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}}) + "\n\n"
                    yield "data: [DONE]\n\n"

                return StreamingResponse(gen(), media_type="text/event-stream")

            message = {"role": "assistant", "content": scripted.get("content", "")}
            if scripted.get("tool_calls"):
                message["tool_calls"] = [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": json.dumps(tc.get("arguments", {}))}}
                    for tc in scripted["tool_calls"]
                ]
            return {
                "id": "chatcmpl-t", "object": "chat.completion",
                "created": int(time.time()), "model": body["model"],
                "choices": [{"index": 0, "message": message,
                             "finish_reason": "tool_calls" if scripted.get("tool_calls") else "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            }

        @app.get("/v1/models")
        async def models():
            return {"object": "list", "data": [{"id": "fake-model", "object": "model"}]}

        @app.post("/v1/audio/transcriptions")
        async def transcriptions(request: Request):
            # Read (and ignore) the multipart body, return a canned transcript
            await request.body()
            return {"text": "transcribed by fake whisper", "language": "en", "duration": 1.5}

        return app


@pytest.mark.asyncio
async def test_full_stack_chat_over_websocket(tmp_path, monkeypatch):
    import uvicorn
    import websockets

    fake = FakeOpenAIServer()
    fake.script(
        {"tool_calls": [{"id": "call_1", "name": "calculate", "arguments": {"expression": "21*2"}}]},
        {"content": "The answer is 42, calculated for you."},
    )

    # 1. Start fake OpenAI server
    fake_config = uvicorn.Config(fake.build_app(), host="127.0.0.1", port=9911, log_level="error")
    fake_server = uvicorn.Server(fake_config)
    fake_task = asyncio.create_task(fake_server.serve())
    await asyncio.sleep(1.5)

    # 2. Configure Colons to use it as a custom OpenAI-compatible provider
    monkeypatch.setenv("COLONS_PROVIDER", "custom")
    monkeypatch.setenv("COLONS_BASE_URL", "http://127.0.0.1:9911/v1")
    monkeypatch.setenv("COLONS_API_KEY", "test-key")
    monkeypatch.setenv("COLONS_MODEL", "fake-model")
    monkeypatch.setenv("COLONS_STORE_URL", str(tmp_path / "e2e.db"))
    monkeypatch.setenv("COLONS_DATA_DIR", str(tmp_path))

    import importlib
    import colons_api.main as api_main
    importlib.reload(api_main)

    colons_config = uvicorn.Config(api_main.app, host="127.0.0.1", port=9912, log_level="error")
    colons_server = uvicorn.Server(colons_config)
    colons_task = asyncio.create_task(colons_server.serve())
    await asyncio.sleep(2.0)

    try:
        async with websockets.connect("ws://127.0.0.1:9912/ws/chat",
                                      ping_interval=20) as ws:
            await ws.send(json.dumps({"type": "chat", "message": "what is 21*2?",
                                      "user_id": "e2e", "stream": True}))
            events = []
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=15)
                ev = json.loads(raw)
                events.append(ev)
                if ev["type"] in ("done", "error"):
                    break

        types = [e["type"] for e in events]
        assert "start" in types
        assert "delta" in types
        assert "tool_call" in types
        assert "tool_result" in types
        assert "usage" in types
        assert "done" in types

        tool_call = next(e for e in events if e["type"] == "tool_call")
        assert tool_call["tool"] == "calculate"
        assert tool_call["arguments"] == {"expression": "21*2"}

        tool_result = next(e for e in events if e["type"] == "tool_result")
        assert tool_result["success"] is True
        assert tool_result["output"] == 42

        deltas = "".join(e.get("content", "") for e in events if e["type"] == "delta")
        assert "42" in deltas

        done = next(e for e in events if e["type"] == "done")
        assert "42" in done["content"]

        # Verify the second provider call included the tool result
        assert len(fake.requests) >= 2
        second_messages = fake.requests[1]["messages"]
        assert any(m.get("role") == "tool" for m in second_messages)
    finally:
        colons_server.should_exit = True
        fake_server.should_exit = True
        await asyncio.gather(colons_task, fake_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_schedules_via_api_end_to_end(tmp_path, monkeypatch):
    """Create, run and delete a cron schedule through the real API."""
    import uvicorn

    fake = FakeOpenAIServer()
    fake.script(
        {"content": "Standup summary: all systems nominal."},
        {"content": "Standup summary: all systems nominal."},
    )

    fake_config = uvicorn.Config(fake.build_app(), host="127.0.0.1", port=9921, log_level="error")
    fake_server = uvicorn.Server(fake_config)
    fake_task = asyncio.create_task(fake_server.serve())
    await asyncio.sleep(1.5)

    monkeypatch.setenv("COLONS_PROVIDER", "custom")
    monkeypatch.setenv("COLONS_BASE_URL", "http://127.0.0.1:9921/v1")
    monkeypatch.setenv("COLONS_API_KEY", "test-key")
    monkeypatch.setenv("COLONS_MODEL", "fake-model")
    monkeypatch.setenv("COLONS_STORE_URL", str(tmp_path / "sched-e2e.db"))
    monkeypatch.setenv("COLONS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COLONS_SCHEDULER_ENABLED", "true")

    import importlib

    import httpx
    import colons_api.main as api_main
    importlib.reload(api_main)

    colons_config = uvicorn.Config(api_main.app, host="127.0.0.1", port=9922, log_level="error")
    colons_server = uvicorn.Server(colons_config)
    colons_task = asyncio.create_task(colons_server.serve())
    await asyncio.sleep(2.0)

    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:9922", timeout=30) as client:
            # Create a schedule
            resp = await client.post("/api/schedules", json={
                "cron": "0 9 * * 1-5",
                "prompt": "Give me a standup summary",
                "name": "standup",
                "user_id": "e2e-sched",
            })
            assert resp.status_code == 200, resp.text
            schedule = resp.json()
            assert schedule["next_run"] is not None

            # List
            resp = await client.get("/api/schedules")
            assert len(resp.json()["schedules"]) == 1
            assert resp.json()["status"]["running"] is True

            # Validate an interval expression
            resp = await client.get("/api/schedules/validate/@every 10m")
            assert resp.json()["valid"] is True

            # Run it now -> goes through the agent -> fake provider
            resp = await client.post(f"/api/schedules/{schedule['id']}/run")
            assert resp.status_code == 200
            result = resp.json()["result"]
            assert "standup summary" in result.lower()

            # History should record the run
            resp = await client.get("/api/schedules/history/recent")
            runs = resp.json()["runs"]
            assert runs and runs[-1]["status"] == "ok"

            # Disable + delete
            resp = await client.patch(f"/api/schedules/{schedule['id']}",
                                      json={"enabled": False})
            assert resp.json()["enabled"] is False
            resp = await client.delete(f"/api/schedules/{schedule['id']}")
            assert resp.json()["deleted"] is True

            # Doctor should be healthy with a fake reachable provider
            resp = await client.get("/api/doctor?check_provider=false")
            report = resp.json()
            assert report["ok"] is True

            # Debug endpoints respond
            assert (await client.get("/api/debug/logs")).status_code == 200
            assert (await client.get("/api/debug/stats")).status_code == 200
    finally:
        colons_server.should_exit = True
        fake_server.should_exit = True
        await asyncio.gather(colons_task, fake_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_openai_compatible_streaming_and_transcribe(tmp_path, monkeypatch):
    """/v1/chat/completions streaming (SSE) and /api/voice/transcribe over HTTP."""
    import uvicorn

    fake = FakeOpenAIServer()
    fake.script({"content": "Streamed reply from the fake provider."})

    fake_config = uvicorn.Config(fake.build_app(), host="127.0.0.1", port=9931, log_level="error")
    fake_server = uvicorn.Server(fake_config)
    fake_task = asyncio.create_task(fake_server.serve())
    await asyncio.sleep(1.5)

    monkeypatch.setenv("COLONS_PROVIDER", "custom")
    monkeypatch.setenv("COLONS_BASE_URL", "http://127.0.0.1:9931/v1")
    monkeypatch.setenv("COLONS_API_KEY", "test-key")
    monkeypatch.setenv("COLONS_MODEL", "fake-model")
    monkeypatch.setenv("COLONS_STORE_URL", str(tmp_path / "v1.db"))
    monkeypatch.setenv("COLONS_DATA_DIR", str(tmp_path))

    import importlib

    import httpx
    import colons_api.main as api_main
    importlib.reload(api_main)

    colons_config = uvicorn.Config(api_main.app, host="127.0.0.1", port=9932, log_level="error")
    colons_server = uvicorn.Server(colons_config)
    colons_task = asyncio.create_task(colons_server.serve())
    await asyncio.sleep(2.0)

    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:9932", timeout=30) as client:
            # --- Streaming /v1 ---
            frames = []
            async with client.stream("POST", "/v1/chat/completions", json={
                "model": "fake-model",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            }) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers["content-type"]
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        frames.append(line[5:].strip())

            assert frames[-1] == "[DONE]"
            import json as _json
            parsed = [_json.loads(f) for f in frames[:-1]]
            assert all(f["object"] == "chat.completion.chunk" for f in parsed)
            text = "".join(
                c.get("delta", {}).get("content", "")
                for f in parsed for c in f["choices"]
            )
            assert "Streamed reply" in text

            # --- Non-streaming still works ---
            resp = await client.post("/v1/chat/completions", json={
                "model": "fake-model",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert resp.status_code == 200
            assert resp.json()["object"] == "chat.completion"

            # --- Transcription endpoint ---
            resp = await client.post(
                "/api/voice/transcribe",
                files={"file": ("clip.webm", b"\x00\x01fake-audio", "audio/webm")},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["text"] == "transcribed by fake whisper"

            # Empty file rejected
            resp = await client.post(
                "/api/voice/transcribe",
                files={"file": ("empty.webm", b"", "audio/webm")},
            )
            assert resp.status_code == 400
    finally:
        colons_server.should_exit = True
        fake_server.should_exit = True
        await asyncio.gather(colons_task, fake_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_sessions_survive_server_restart(tmp_path, monkeypatch):
    """Sessions written through the API are loadable by a fresh manager."""
    import uvicorn

    fake = FakeOpenAIServer()
    fake.script({"content": "persistent answer"})

    fake_config = uvicorn.Config(fake.build_app(), host="127.0.0.1", port=9941, log_level="error")
    fake_server = uvicorn.Server(fake_config)
    fake_task = asyncio.create_task(fake_server.serve())
    await asyncio.sleep(1.5)

    data_dir = str(tmp_path)
    monkeypatch.setenv("COLONS_PROVIDER", "custom")
    monkeypatch.setenv("COLONS_BASE_URL", "http://127.0.0.1:9941/v1")
    monkeypatch.setenv("COLONS_API_KEY", "test-key")
    monkeypatch.setenv("COLONS_MODEL", "fake-model")
    monkeypatch.setenv("COLONS_STORE_URL", str(tmp_path / "restart.db"))
    monkeypatch.setenv("COLONS_DATA_DIR", data_dir)

    import importlib

    import httpx
    import colons_api.main as api_main
    importlib.reload(api_main)

    colons_config = uvicorn.Config(api_main.app, host="127.0.0.1", port=9942, log_level="error")
    colons_server = uvicorn.Server(colons_config)
    colons_task = asyncio.create_task(colons_server.serve())
    await asyncio.sleep(2.0)

    session_id = None
    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:9942", timeout=30) as client:
            resp = await client.post("/api/chat", json={
                "message": "remember this conversation",
                "user_id": "restart-user",
            })
            assert resp.status_code == 200, resp.text

            resp = await client.get("/api/sessions", params={"user_id": "restart-user"})
            sessions = resp.json()["sessions"]
            assert len(sessions) == 1
            session_id = sessions[0]["id"]
            assert sessions[0]["message_count"] == 2

            resp = await client.get(f"/api/sessions/{session_id}",
                                    params={"user_id": "restart-user"})
            contents = [m["content"] for m in resp.json()["messages"]]
            assert contents == ["remember this conversation", "persistent answer"]
    finally:
        colons_server.should_exit = True
        fake_server.should_exit = True
        await asyncio.gather(colons_task, fake_task, return_exceptions=True)

    # --- Simulate a restart: fresh manager over the same data dir ---
    from colons_core.config import ColonsConfig
    from colons_api.manager import AgentManager

    fresh_config = ColonsConfig.load()
    fresh_config.data_dir = data_dir
    fresh_config.memory.store_url = str(tmp_path / "restart.db")
    fresh_config.scheduler.enabled = False
    fresh_manager = AgentManager(fresh_config)

    agent = fresh_manager.get_or_create(user_id="restart-user")
    listed = agent.list_sessions()
    assert any(s["id"] == session_id and s["message_count"] == 2 for s in listed), listed

    restored = agent.get_session(session_id)
    assert restored is not None
    assert [m.content for m in restored.messages] == ["remember this conversation",
                                                      "persistent answer"]
    await fresh_manager.stop_all()


async def _boot_colons(tmp_path, monkeypatch, port, script_builder, store_name):
    """Helper: start fake provider + Colons, return (client_ctx, servers, task list)."""
    import uvicorn

    fake = FakeOpenAIServer()
    script_builder(fake)

    fake_config = uvicorn.Config(fake.build_app(), host="127.0.0.1", port=port, log_level="error")
    fake_server = uvicorn.Server(fake_config)
    fake_task = asyncio.create_task(fake_server.serve())
    await asyncio.sleep(1.2)

    monkeypatch.setenv("COLONS_PROVIDER", "custom")
    monkeypatch.setenv("COLONS_BASE_URL", f"http://127.0.0.1:{port}/v1")
    monkeypatch.setenv("COLONS_API_KEY", "test-key")
    monkeypatch.setenv("COLONS_MODEL", "fake-model")
    monkeypatch.setenv("COLONS_STORE_URL", str(tmp_path / store_name))
    monkeypatch.setenv("COLONS_DATA_DIR", str(tmp_path))

    import importlib

    import colons_api.main as api_main
    importlib.reload(api_main)

    colons_config = uvicorn.Config(api_main.app, host="127.0.0.1", port=port + 1, log_level="error")
    colons_server = uvicorn.Server(colons_config)
    colons_task = asyncio.create_task(colons_server.serve())
    await asyncio.sleep(2.0)
    return fake, fake_server, fake_task, colons_server, colons_task, port + 1


@pytest.mark.asyncio
async def test_bots_and_bot_to_bot_dm_e2e(tmp_path, monkeypatch):
    """Create bots via API, chat with one, and have it DM another via message_agent."""
    import httpx

    def script(fake):
        fake.script(
            {"content": "Hi, researcher here."},
            {"tool_calls": [{"id": "c1", "name": "message_agent",
                             "arguments": {"target": "@writer", "message": "Write a short poem"}}]},
            {"content": "Roses are red, deploys are green."},
            {"content": "Writer says: Roses are red, deploys are green."},
        )

    fake, fake_server, fake_task, colons_server, colons_task, port = await _boot_colons(
        tmp_path, monkeypatch, 9971, script, "bots.db")

    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=60) as client:
            # Default bot exists
            resp = await client.get("/api/bots")
            bots = resp.json()["bots"]
            assert any(b["id"] == "default" for b in bots)

            # Create two bots
            for name, title in (("Researcher", "Deep Researcher"), ("Writer", "Copywriter")):
                resp = await client.post("/api/bots", json={"name": name, "title": title})
                assert resp.status_code == 200, resp.text
            resp = await client.get("/api/bots")
            ids = {b["id"] for b in resp.json()["bots"]}
            assert {"default", "researcher", "writer"} <= ids

            # Chat with the researcher bot directly
            resp = await client.post("/api/chat", json={
                "message": "hello", "agent_id": "bot-researcher", "user_id": "default",
            })
            assert resp.status_code == 200
            assert resp.json()["response"] == "Hi, researcher here."

            # Ask it to hand off -> it calls message_agent -> writer replies -> researcher reports
            resp = await client.post("/api/chat", json={
                "message": "delegate to @writer: write a short poem",
                "agent_id": "bot-researcher", "user_id": "default",
            })
            assert resp.status_code == 200, resp.text
            final = resp.json()["response"]
            assert "Writer says" in final

            # The writer's canonical Bot Chat holds the attributed DM + its reply
            resp = await client.get("/api/sessions",
                                    params={"user_id": "default", "agent_id": "bot-writer"})
            sessions = resp.json()["sessions"]
            bot_chat = next((s for s in sessions if s["id"] == "bot-chat-writer"), None)
            assert bot_chat is not None, sessions
            resp = await client.get("/api/sessions/bot-chat-writer",
                                    params={"user_id": "default", "agent_id": "bot-writer"})
            messages = resp.json()["messages"]
            assert len(messages) == 2
            assert messages[0]["content"].startswith("Message from 🤖 Researcher (@researcher):")
            assert messages[1]["content"] == "Roses are red, deploys are green."

            # Deleting a bot works and is reflected in the roster
            resp = await client.delete("/api/bots/writer")
            assert resp.json()["deleted"] is True
            resp = await client.get("/api/bots")
            assert "writer" not in {b["id"] for b in resp.json()["bots"]}
    finally:
        colons_server.should_exit = True
        fake_server.should_exit = True
        await asyncio.gather(colons_task, fake_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_rooms_e2e(tmp_path, monkeypatch):
    """Create a room via API and run a full multi-bot round."""
    import httpx

    def script(fake):
        fake.script(
            {"content": "I checked the logs; all green."},
            {"content": "Docs are updated too."},
        )

    fake, fake_server, fake_task, colons_server, colons_task, port = await _boot_colons(
        tmp_path, monkeypatch, 9981, script, "rooms.db")

    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=60) as client:
            await client.post("/api/bots", json={"name": "Alpha"})
            await client.post("/api/bots", json={"name": "Bravo"})

            resp = await client.post("/api/rooms", json={"name": "Standup",
                                                         "members": ["alpha", "bravo"]})
            assert resp.status_code == 200, resp.text
            room_id = resp.json()["id"]

            resp = await client.post(f"/api/rooms/{room_id}/messages",
                                     json={"message": "How is the release?"})
            assert resp.status_code == 200, resp.text
            added = resp.json()["messages"]
            speakers = [m["speaker"] for m in added]
            assert speakers == ["user", "alpha", "bravo"], added

            resp = await client.get(f"/api/rooms/{room_id}/messages")
            assert len(resp.json()["messages"]) == 3

            resp = await client.get("/api/rooms")
            room = next(r for r in resp.json()["rooms"] if r["id"] == room_id)
            assert room["message_count"] == 3
            assert room["needs_user"] is False

            # Several members posting alone is allowed; mention scoping works
            resp = await client.post(f"/api/rooms/{room_id}/messages",
                                     json={"message": "@alpha one more check?"})
            speakers = [m["speaker"] for m in resp.json()["messages"]]
            assert speakers == ["user", "alpha"]
    finally:
        colons_server.should_exit = True
        fake_server.should_exit = True
        await asyncio.gather(colons_task, fake_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_peer_message_endpoint_e2e(tmp_path, monkeypatch):
    """Inbound peer DMs land in the default bot's canonical chat."""
    import httpx

    def script(fake):
        fake.script({"content": "Acknowledged, peer."})

    fake, fake_server, fake_task, colons_server, colons_task, port = await _boot_colons(
        tmp_path, monkeypatch, 9991, script, "peer.db")

    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=60) as client:
            resp = await client.post("/api/peer/message", json={
                "target": "default",
                "message": "Can you review the PR?",
                "sender": {"id": "spark", "name": "Spark", "handle": "@spark"},
            })
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["status"] == "ok"
            assert data["reply"] == "Acknowledged, peer."

            resp = await client.get("/api/sessions/bot-chat-default",
                                    params={"user_id": "default", "agent_id": "bot-default"})
            messages = resp.json()["messages"]
            assert messages[0]["content"].startswith("Message from 🤖 Spark (@spark) [peer]:")

            # Unknown target -> typed error
            resp = await client.post("/api/peer/message", json={
                "target": "ghost", "message": "hi", "sender": {"id": "x"},
            })
            assert resp.json()["status"] == "error"
            assert resp.json()["reason"] == "unknown_target"
    finally:
        colons_server.should_exit = True
        fake_server.should_exit = True
        await asyncio.gather(colons_task, fake_task, return_exceptions=True)
