"""
Colons API server
FastAPI application with REST + WebSocket, agent management, auth, rate limiting.
"""
import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import aclosing, asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Literal, Optional

from colons_core import __version__
from colons_core.agents import EventType
from colons_core.agents.attachments import validate_attachments
from colons_core.audio import POPULAR_VOICES
from colons_core.config import ColonsConfig
from colons_core.diagnostics import run_doctor
from colons_core.observability import (
    process_stats,
    redacted_env,
    request_id_var,
    setup_logging,
)
from colons_core.providers import ProviderFactory
from colons_core.providers.base import ProviderError
from colons_core.scheduler import CronError, Schedule, SchedulerService
from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .manager import AgentManager

CONFIG: ColonsConfig = ColonsConfig.load()
LOG_RING = setup_logging(
    level=CONFIG.logging.level,
    file_path=CONFIG.logging.file or None,
    max_bytes=CONFIG.logging.max_bytes,
    backups=CONFIG.logging.backups,
    buffer_size=CONFIG.logging.buffer_size,
    json_format=CONFIG.logging.json_format,
)
logger = logging.getLogger("colons.api")
manager: Optional[AgentManager] = None


# --------------------------------------------------------------------------- #
# Lifespan
# --------------------------------------------------------------------------- #

@asynccontextmanager
async def lifespan(app: FastAPI):
    global manager
    manager = AgentManager(CONFIG)
    logger.info(f"Colons v{__version__} | provider={CONFIG.provider.name} "
                f"| store={CONFIG.memory.store_url} | scheduler={'on' if manager.scheduler else 'off'} "
                f"| messaging={len(manager.messaging.adapters) if manager.messaging else 0} adapters")
    await manager.start_all()
    try:
        yield
    finally:
        if manager:
            await manager.stop_all()


app = FastAPI(
    title="Colons",
    description="Open-source, self-hostable always-on AI agent",
    version=__version__,
    lifespan=lifespan,
)

# Credentials + wildcard origin is invalid per the CORS spec; we use bearer
# headers rather than cookies, so credentials are only allowed for explicit origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CONFIG.server.cors_origins,
    allow_credentials=CONFIG.server.cors_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Auth & rate limiting
# --------------------------------------------------------------------------- #

_rate_buckets: Dict[str, List[float]] = {}


async def require_auth(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
):
    """No keys configured => open (local use). Otherwise require a valid key."""
    if not CONFIG.server.api_keys:
        return True
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_api_key:
        token = x_api_key.strip()
    if token not in CONFIG.server.api_keys:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return True


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Assign a request id, track timing, and enforce the rate limit."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    token = request_id_var.set(request_id)

    limit = CONFIG.server.rate_limit_per_minute
    if limit > 0:
        ip = request.client.host if request.client else "unknown"
        now = time.time()
        # Periodically evict stale buckets so the map can't grow forever
        if len(_rate_buckets) > 1000:
            stale = [key for key, stamps in _rate_buckets.items()
                     if not stamps or now - stamps[-1] > 60]
            for key in stale:
                _rate_buckets.pop(key, None)
        bucket = [t for t in _rate_buckets.get(ip, []) if now - t < 60]
        if len(bucket) >= limit:
            request_id_var.reset(token)
            return JSONResponse({"detail": "Rate limit exceeded", "request_id": request_id},
                                status_code=429)
        bucket.append(now)
        _rate_buckets[ip] = bucket

    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(f"{request.method} {request.url.path} failed")
        request_id_var.reset(token)
        raise
    duration_ms = (time.perf_counter() - started) * 1000
    response.headers["x-request-id"] = request_id
    response.headers["x-response-time-ms"] = f"{duration_ms:.1f}"

    if not request.url.path.startswith(("/health", "/assets")):
        level = logging.WARNING if response.status_code >= 400 else logging.INFO
        logger.log(level, f"{request.method} {request.url.path} -> {response.status_code} "
                          f"({duration_ms:.0f}ms)")

    request_id_var.reset(token)
    return response


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class ChatRequest(BaseModel):
    message: str
    attachments: List[Dict] = Field(default_factory=list)
    session_id: Optional[str] = None
    stream: bool = True
    agent_id: Optional[str] = None
    user_id: str = "default"


class TaskRequest(BaseModel):
    description: str
    priority: int = 1
    agent_id: Optional[str] = None
    user_id: str = "default"


class ProviderSwitchRequest(BaseModel):
    name: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None


class MemoryStoreRequest(BaseModel):
    content: str
    kind: str = "note"
    agent_id: Optional[str] = None
    user_id: str = "default"


class AvatarSelectRequest(BaseModel):
    agent_id: str
    avatar_id: str
    user_id: str = "default"


class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = None
    rate: Optional[str] = None
    volume: Optional[str] = None
    pitch: Optional[str] = None


class ToolExecuteRequest(BaseModel):
    tool: str
    arguments: Dict = Field(default_factory=dict)
    agent_id: Optional[str] = None
    user_id: str = "default"


class TerminalCommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=10000)
    cwd: Optional[str] = None
    user_id: str = "default"
    agent_id: Optional[str] = None


class OpenAIChatRequest(BaseModel):
    model: str = ""
    messages: List[Dict]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    tools: Optional[List[Dict]] = None


# --------------------------------------------------------------------------- #
# Meta endpoints
# --------------------------------------------------------------------------- #

@app.get("/api/info")
async def api_info():
    return {
        "name": "Colons",
        "version": __version__,
        "description": "Open-source, self-hostable always-on AI agent",
        "provider": CONFIG.provider.name,
        "model": CONFIG.provider.model or ProviderFactory.default_model(CONFIG.provider.name),
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/", include_in_schema=False)
async def root():
    """Serve the SPA when built; otherwise return API info JSON."""
    if _WEB_DIST is not None and (_WEB_DIST / "index.html").is_file():
        return FileResponse(_WEB_DIST / "index.html")
    return await api_info()


@app.get("/health")
async def health():
    ok = manager is not None
    return {
        "healthy": ok,
        "version": __version__,
        "provider": CONFIG.provider.name,
        "agents": len(manager.agents) if manager else 0,
        "bots": manager.bots.count() if manager else 0,
        "rooms": len(manager.rooms.list()) if manager else 0,
        "memory_entries": manager.store.count() if manager else 0,
        "cache": manager.cache.stats() if manager else {},
        "scheduler": manager.scheduler.status() if manager and manager.scheduler else {"running": False},
        "messaging": manager.messaging.status() if manager and manager.messaging else [],
        "timestamp": time.time(),
    }


# --------------------------------------------------------------------------- #
# Chat
# --------------------------------------------------------------------------- #

@app.post("/api/chat", dependencies=[Depends(require_auth)])
async def chat(req: ChatRequest):
    """Non-streaming chat. Returns the final response plus metadata."""
    try:
        attachments = validate_attachments(req.attachments)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    agent = manager.get_or_create(req.user_id, req.agent_id)
    await agent.start()
    hint = manager.mention_hint(req.message, owner=req.user_id) if manager else ""
    final = ""
    usage = {}
    events = []
    session_id = req.session_id
    async for ev in agent.chat(req.message, session_id=req.session_id, stream=False,
                               system_hint=hint or None, attachments=attachments):
        events.append({"type": ev["type"]})
        if ev["type"] == EventType.START.value:
            session_id = ev["session_id"]
        if ev["type"] == EventType.DONE.value:
            final = ev.get("content", "")
        elif ev["type"] == EventType.USAGE.value:
            usage = ev
        elif ev["type"] == EventType.ERROR.value:
            raise HTTPException(status_code=502, detail=ev.get("message", "agent error"))
    return {"response": final, "usage": usage, "session_id": session_id,
            "event_count": len(events)}


@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket):
    """
    Streaming chat over WebSocket.
    Client sends JSON: {message, session_id?, user_id?, agent_id?, type?}
    Server streams typed events as JSON.
    """
    await websocket.accept()

    # Auth (via query param) if keys are configured
    if CONFIG.server.api_keys:
        token = websocket.query_params.get("api_key")
        if token not in CONFIG.server.api_keys:
            await websocket.send_json({"type": "error", "message": "Unauthorized"})
            await websocket.close(code=4401)
            return

    active_task = None
    active_request_id = None
    active_agent = None
    send_lock = asyncio.Lock()
    pending_approvals = {}

    async def send(payload):
        async with send_lock:
            await websocket.send_json(_jsonable(payload))

    async def stream_reply(agent, message, data, request_id):
        async def approve(request):
            approval_id = uuid.uuid4().hex[:12]
            future = asyncio.get_running_loop().create_future()
            pending_approvals[approval_id] = future
            try:
                await send({"type": "approval_required", "approval_id": approval_id,
                            "request_id": request_id, "timestamp": time.time(), **request.to_dict()})
                return await asyncio.wait_for(future, timeout=120)
            except asyncio.TimeoutError:
                return False
            finally:
                pending_approvals.pop(approval_id, None)

        session_id = data.get("session_id")
        try:
            hint = manager.mention_hint(message, owner=data.get("user_id", "default"))
            async with aclosing(agent.chat(
                message, session_id=session_id,
                stream=data.get("stream", True), request_id=request_id,
                system_hint=hint or None, attachments=data.get("attachments"), approval_callback=approve,
            )) as events:
                async for ev in events:
                    if ev["type"] == EventType.START.value:
                        session_id = ev["session_id"]
                    await send(ev)
        except asyncio.CancelledError:
            await send({"type": "status", "state": "cancelled", "timestamp": time.time()})
            await send({"type": "done", "session_id": session_id,
                        "request_id": request_id, "content": "", "timestamp": time.time()})
        except Exception as exc:
            logger.exception("WebSocket chat failed")
            await send({"type": "error", "message": str(exc)})

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await send({"type": "error", "message": "Invalid JSON"})
                continue
            if not isinstance(data, dict):
                await send({"type": "error", "message": "Expected a JSON object"})
                continue

            msg_type = data.get("type", "chat")
            if msg_type == "ping":
                await send({"type": "pong", "timestamp": time.time()})
                continue
            if msg_type == "approval":
                future = pending_approvals.get(data.get("approval_id"))
                if future and not future.done() and isinstance(data.get("approved"), bool):
                    future.set_result(data["approved"])
                continue
            if msg_type == "cancel":
                # Only cancel this connection's active turn, including named bots.
                if active_task and not active_task.done() and data.get("request_id") == active_request_id:
                    active_agent.cancel(active_request_id)
                    active_task.cancel()
                    await asyncio.gather(active_task, return_exceptions=True)
                continue
            if msg_type != "chat":
                await send({"type": "error", "message": f"Unknown type: {msg_type}"})
                continue
            if active_task and not active_task.done():
                await send({"type": "error", "message": "A chat is already running on this connection"})
                continue

            message = data.get("message", "")
            try:
                data["attachments"] = validate_attachments(data.get("attachments"))
            except ValueError as exc:
                await send({"type": "error", "message": str(exc)})
                continue
            if not isinstance(message, str) or (not message.strip() and not data["attachments"]):
                await send({"type": "error", "message": "Empty or invalid message"})
                continue
            active_agent = manager.get_or_create(data.get("user_id", "default"), data.get("agent_id"))
            await active_agent.start()
            active_request_id = uuid.uuid4().hex[:12]
            active_task = asyncio.create_task(stream_reply(active_agent, message.strip(), data, active_request_id))

    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected")
    except Exception as exc:
        logger.exception("WebSocket error")
        try:
            await send({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        if active_task:
            if not active_task.done():
                active_task.cancel()
            await asyncio.gather(active_task, return_exceptions=True)


def _jsonable(obj):
    """Best-effort JSON-safe conversion."""
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return json.loads(json.dumps(obj, default=str))


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #

@app.get("/api/sessions", dependencies=[Depends(require_auth)])
async def list_sessions(user_id: str = "default", agent_id: Optional[str] = None):
    agent = manager.get_or_create(user_id, agent_id)
    return {"sessions": agent.list_sessions()}


@app.post("/api/sessions", dependencies=[Depends(require_auth)])
async def create_session(user_id: str = "default", agent_id: Optional[str] = None,
                         title: str = "New chat"):
    agent = manager.get_or_create(user_id, agent_id)
    session = agent.create_session(title=title)
    return session.to_dict()


@app.get("/api/sessions/{session_id}", dependencies=[Depends(require_auth)])
async def get_session(session_id: str, user_id: str = "default", agent_id: Optional[str] = None):
    agent = manager.get_or_create(user_id, agent_id)
    session = agent.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        **session.to_dict(),
        "messages": [m.to_dict() for m in session.messages],
    }


@app.delete("/api/sessions/{session_id}", dependencies=[Depends(require_auth)])
async def delete_session(session_id: str, user_id: str = "default", agent_id: Optional[str] = None):
    agent = manager.get_or_create(user_id, agent_id)
    if not agent.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": True}


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #

@app.post("/api/tasks", dependencies=[Depends(require_auth)])
async def create_task(req: TaskRequest):
    agent = manager.get_or_create(req.user_id, req.agent_id)
    await agent.start()
    task = await agent.create_task(req.description, req.priority)
    return task.to_dict()


@app.get("/api/tasks", dependencies=[Depends(require_auth)])
async def list_tasks(user_id: str = "default", agent_id: Optional[str] = None,
                     status: Optional[str] = None):
    agent = manager.get_or_create(user_id, agent_id)
    return {"tasks": agent.list_tasks(status)}


@app.get("/api/tasks/{task_id}", dependencies=[Depends(require_auth)])
async def get_task(task_id: str, user_id: str = "default", agent_id: Optional[str] = None):
    agent = manager.get_or_create(user_id, agent_id)
    task = agent.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.to_dict()


@app.post("/api/tasks/{task_id}/run", dependencies=[Depends(require_auth)])
async def run_task(task_id: str, user_id: str = "default", agent_id: Optional[str] = None):
    agent = manager.get_or_create(user_id, agent_id)
    task = agent.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status in ("planning", "running"):
        raise HTTPException(status_code=409, detail="Task is already running")
    try:
        result = await agent.execute_task(task)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"task": task.to_dict(), "result": result}


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #

@app.get("/api/memory/search", dependencies=[Depends(require_auth)])
async def memory_search(q: str, limit: int = 5, user_id: str = "default",
                        agent_id: Optional[str] = None):
    agent = manager.get_or_create(user_id, agent_id)
    return {"results": await agent.recall(q, limit=limit)}


@app.get("/api/memory/history", dependencies=[Depends(require_auth)])
async def memory_history(limit: int = 50, offset: int = 0, user_id: str = "default",
                         agent_id: Optional[str] = None):
    agent = manager.get_or_create(user_id, agent_id)
    entries = manager.store.history(user_id, limit=limit, offset=offset,
                                    agent_id=agent.agent_id)
    return {"entries": [e.to_dict() for e in entries]}


@app.post("/api/memory", dependencies=[Depends(require_auth)])
async def memory_store(req: MemoryStoreRequest):
    agent = manager.get_or_create(req.user_id, req.agent_id)
    from colons_core.memory.store import MemoryEntry
    vector = await agent.embeddings.embed(req.content)
    entry = MemoryEntry(
        user_id=req.user_id, agent_id=agent.agent_id, kind=req.kind,
        content=req.content, embedding=vector,
    )
    manager.store.store(entry)
    return {"stored": True, "id": entry.id}


@app.delete("/api/memory", dependencies=[Depends(require_auth)])
async def memory_delete(user_id: str = "default", agent_id: Optional[str] = None,
                        memory_id: Optional[int] = None):
    agent = manager.get_or_create(user_id, agent_id)
    count = agent.forget(memory_id)
    return {"deleted": count}


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #

@app.get("/api/tools", dependencies=[Depends(require_auth)])
async def list_tools(user_id: str = "default", agent_id: Optional[str] = None):
    agent = manager.get_or_create(user_id, agent_id)
    return {"tools": agent.tool_manager.list_tools(),
            "stats": agent.tool_manager.get_stats()}


@app.post("/api/tools/execute", dependencies=[Depends(require_auth)])
async def execute_tool(req: ToolExecuteRequest):
    agent = manager.get_or_create(req.user_id, req.agent_id)
    result = await agent.tool_manager.execute(req.tool, req.arguments, user_requested=True)
    return result.to_dict()


@app.post("/api/terminal/command", dependencies=[Depends(require_auth)])
async def terminal_command(req: TerminalCommandRequest):
    """Execute a command explicitly submitted by the user in the terminal panel."""
    if not req.command.strip():
        raise HTTPException(status_code=400, detail="Enter a command")
    agent = manager.get_or_create(req.user_id, req.agent_id)
    arguments = {"command": req.command, "timeout": 60}
    if req.cwd:
        arguments["cwd"] = req.cwd
    result = await agent.tool_manager.execute(
        "run_command", arguments, user_requested=True,
        approval_callback=lambda request: request.tool_name == "run_command",
    )
    return result.to_dict()


class PermissionModeRequest(BaseModel):
    mode: Literal['auto', 'approval', 'full_access']


@app.get("/api/settings/permissions", dependencies=[Depends(require_auth)])
async def permission_settings():
    return manager.permission_settings.public()


@app.put("/api/settings/permissions", dependencies=[Depends(require_auth)])
async def set_permission_settings(req: PermissionModeRequest):
    try:
        return manager.permission_settings.save(req.mode, list(manager.agents.values()))
    except OSError as error:
        raise HTTPException(status_code=500, detail="Could not save permission settings") from error


@app.get("/api/tools/history", dependencies=[Depends(require_auth)])
async def tool_history(limit: int = 50, user_id: str = "default",
                       agent_id: Optional[str] = None):
    agent = manager.get_or_create(user_id, agent_id)
    return {"calls": agent.tool_manager.recent_calls(limit)}


# --------------------------------------------------------------------------- #
# Native browser
# --------------------------------------------------------------------------- #

@app.get("/api/browser", dependencies=[Depends(require_auth)])
async def browser_status(user_id: str = "default", agent_id: Optional[str] = None):
    return manager.get_or_create(user_id, agent_id).browser.status()


@app.get("/api/browser/screenshot", dependencies=[Depends(require_auth)])
async def browser_screenshot(user_id: str = "default", agent_id: Optional[str] = None,
                             page_id: str = ""):
    agent = manager.get_or_create(user_id, agent_id)
    if "browser_screenshot" not in agent.registry:
        raise HTTPException(status_code=403, detail="Browser screenshots are disabled")
    try:
        image = await agent.browser.screenshot_bytes(page_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(image, media_type="image/png", headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------- #
# Avatars
# --------------------------------------------------------------------------- #

@app.get("/api/avatars")
async def list_avatars(kind: Optional[str] = None):
    return {"avatars": manager.avatars.list_avatars(kind)}


@app.post("/api/avatars/select", dependencies=[Depends(require_auth)])
async def select_avatar(req: AvatarSelectRequest):
    agent = manager.get_or_create(req.user_id, req.agent_id)
    try:
        avatar = manager.avatars.select(req.agent_id, req.avatar_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    agent.avatar = avatar
    return {"selected": avatar.to_dict()}


@app.get("/api/avatars/{avatar_id}/svg")
async def avatar_svg(avatar_id: str, size: int = 128):
    svg = manager.avatars.svg(avatar_id, size=min(max(size, 32), 512))
    return Response(content=svg, media_type="image/svg+xml")


# --------------------------------------------------------------------------- #
# Voice / TTS
# --------------------------------------------------------------------------- #

@app.get("/api/voice/voices")
async def list_voices(locale: Optional[str] = None):
    if manager.tts and manager.tts.available:
        return {"engine": manager.tts.engine_name,
                "voices": await manager.tts.list_voices(locale)}
    return {"engine": None, "available": False, "voices": POPULAR_VOICES}


@app.post("/api/voice/tts", dependencies=[Depends(require_auth)])
async def tts(req: TTSRequest):
    if not manager.tts or not manager.tts.available:
        raise HTTPException(status_code=503, detail="TTS unavailable. Install edge-tts.")
    data = await manager.tts.speak_to_bytes(
        req.text, voice=req.voice, rate=req.rate, volume=req.volume, pitch=req.pitch,
    )
    return Response(content=data, media_type="audio/mpeg",
                    headers={"Content-Disposition": "inline; filename=speech.mp3"})


@app.post("/api/voice/transcribe", dependencies=[Depends(require_auth)])
async def transcribe(
    file: "UploadFile" = File(...),
    model: str = "",
    language: Optional[str] = None,
):
    """Speech-to-text using the active provider's transcription endpoint."""
    provider = manager.provider if manager else None
    if provider is None:
        raise HTTPException(status_code=503, detail="No provider available")
    if not getattr(provider, "supports_transcription", False):
        raise HTTPException(
            status_code=501,
            detail=f"provider '{getattr(provider, 'name', '?')}' does not support transcription",
        )
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Empty audio file")
    try:
        return await provider.transcribe(
            audio, filename=file.filename or "audio.webm",
            model=model or "whisper-1", language=language,
        )
    except ProviderError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


# --------------------------------------------------------------------------- #
# Providers & usage
# --------------------------------------------------------------------------- #

@app.get("/api/providers")
async def list_providers():
    return {
        "providers": ProviderFactory.list_providers(),
        "active": {
            "name": CONFIG.provider.name,
            "model": CONFIG.provider.model or ProviderFactory.default_model(CONFIG.provider.name),
            "base_url": CONFIG.provider.base_url,
        },
    }


@app.post("/api/providers/switch", dependencies=[Depends(require_auth)])
async def switch_provider(req: ProviderSwitchRequest):
    try:
        manager.switch_provider(req.name, req.api_key, req.base_url, req.model)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"switched": True, "provider": req.name, "model": req.model}


@app.get("/api/providers/{name}/models", dependencies=[Depends(require_auth)])
async def provider_models(name: str):
    try:
        provider = ProviderFactory.create(provider=name)
        models = await provider.list_models()
        return {"provider": name, "models": models}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/usage", dependencies=[Depends(require_auth)])
async def usage(user_id: str = "default", agent_id: Optional[str] = None):
    agent = manager.get_or_create(user_id, agent_id)
    u = agent.total_usage
    return {
        "prompt_tokens": u.prompt_tokens,
        "completion_tokens": u.completion_tokens,
        "total_tokens": u.total_tokens,
        "cached_tokens": u.cached_tokens,
        "provider_usage": {
            "prompt_tokens": agent.provider.get_usage().prompt_tokens,
            "completion_tokens": agent.provider.get_usage().completion_tokens,
            "total_tokens": agent.provider.get_usage().total_tokens,
        },
        "tool_stats": agent.tool_manager.get_stats(),
    }


# --------------------------------------------------------------------------- #
# Agents
# --------------------------------------------------------------------------- #

@app.get("/api/agents")
async def list_agents():
    return {"agents": manager.list_agents()}


@app.get("/api/agents/{agent_id}/status", dependencies=[Depends(require_auth)])
async def agent_status(agent_id: str, user_id: str = "default"):
    agent = manager.get_or_create(user_id, agent_id)
    return agent.status()


@app.post("/api/agents/{agent_id}/pause", dependencies=[Depends(require_auth)])
async def agent_pause(agent_id: str, user_id: str = "default"):
    agent = manager.get_or_create(user_id, agent_id)
    agent.pause()
    return {"state": agent.state.value}


@app.post("/api/agents/{agent_id}/resume", dependencies=[Depends(require_auth)])
async def agent_resume(agent_id: str, user_id: str = "default"):
    agent = manager.get_or_create(user_id, agent_id)
    agent.resume()
    return {"state": agent.state.value}


@app.delete("/api/agents/{agent_id}", dependencies=[Depends(require_auth)])
async def agent_delete(agent_id: str):
    if not await manager.delete(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"deleted": True}


# --------------------------------------------------------------------------- #
# Bots (named agents / roster)
# --------------------------------------------------------------------------- #

class BotCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    title: str = ""
    description: str = ""
    persona: str = ""
    avatar: str = ""
    model: str = ""
    owner: str = "default"


class BotUpdateRequest(BaseModel):
    name: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    persona: Optional[str] = None
    avatar: Optional[str] = None
    model: Optional[str] = None
    enabled: Optional[bool] = None


@app.get("/api/bots")
async def list_bots(owner: Optional[str] = None):
    return {"bots": manager.list_bots(owner=owner)}


@app.post("/api/bots", dependencies=[Depends(require_auth)])
async def create_bot(req: BotCreateRequest):
    try:
        bot = manager.create_bot(
            name=req.name, title=req.title, description=req.description,
            persona=req.persona, avatar=req.avatar, model=req.model, owner=req.owner,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return bot.to_dict()


@app.get("/api/bots/{bot_id}")
async def get_bot(bot_id: str):
    bot = manager.bots.get(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    data = bot.to_dict()
    data["agent_id"] = f"bot-{bot.id}"
    return data


@app.patch("/api/bots/{bot_id}", dependencies=[Depends(require_auth)])
async def update_bot(bot_id: str, req: BotUpdateRequest):
    bot = manager.update_bot(bot_id, **req.model_dump(exclude_none=True))
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    return bot.to_dict()


@app.delete("/api/bots/{bot_id}", dependencies=[Depends(require_auth)])
async def delete_bot(bot_id: str):
    try:
        deleted = await manager.delete_bot(bot_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not deleted:
        raise HTTPException(status_code=404, detail="Bot not found")
    return {"deleted": True}


# --------------------------------------------------------------------------- #
# Group rooms
# --------------------------------------------------------------------------- #

class RoomCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    members: List[str] = Field(..., min_length=2, max_length=6)
    owner: str = "default"


class RoomUpdateRequest(BaseModel):
    name: Optional[str] = None
    members: Optional[List[str]] = None


class RoomMessageRequest(BaseModel):
    message: str = Field(..., min_length=1)
    speaker: str = "user"


@app.get("/api/rooms")
async def list_rooms(owner: str = "default"):
    return {"rooms": manager.room_service.list(owner=owner)}


@app.post("/api/rooms", dependencies=[Depends(require_auth)])
async def create_room(req: RoomCreateRequest):
    try:
        room = manager.room_service.create(req.name, req.members, owner=req.owner)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return room.to_dict()


@app.get("/api/rooms/{room_id}")
async def get_room(room_id: str):
    room = manager.room_service.get(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    data = room.to_dict()
    data["message_count"] = manager.rooms.message_count(room_id)
    return data


@app.patch("/api/rooms/{room_id}", dependencies=[Depends(require_auth)])
async def update_room(room_id: str, req: RoomUpdateRequest):
    try:
        room = manager.room_service.update(room_id, **req.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    return room.to_dict()


@app.delete("/api/rooms/{room_id}", dependencies=[Depends(require_auth)])
async def delete_room(room_id: str):
    if not manager.room_service.delete(room_id):
        raise HTTPException(status_code=404, detail="Room not found")
    return {"deleted": True}


@app.get("/api/rooms/{room_id}/messages")
async def room_messages(room_id: str, limit: int = 100, offset: int = 0):
    if not manager.room_service.get(room_id):
        raise HTTPException(status_code=404, detail="Room not found")
    return {"messages": manager.room_service.history(room_id, limit=limit, offset=offset)}


@app.post("/api/rooms/{room_id}/messages", dependencies=[Depends(require_auth)])
async def post_room_message(room_id: str, req: RoomMessageRequest):
    """Post a message and run up to 3 serial rounds of member turns."""
    try:
        added = await manager.room_service.send(room_id, req.message, speaker=req.speaker)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"messages": [m.to_dict() for m in added]}


@app.post("/api/rooms/{room_id}/seen", dependencies=[Depends(require_auth)])
async def room_seen(room_id: str):
    manager.room_service.mark_seen(room_id)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Peers (cross-instance bot messaging)
# --------------------------------------------------------------------------- #

class PeerMessageRequest(BaseModel):
    target: str = ""
    message: str
    sender: Dict[str, Any] = Field(default_factory=dict)


@app.get("/api/peers")
async def list_peers():
    return {
        "peers": [
            {"name": name, "url": cfg.get("url", ""),
             "agents": cfg.get("agents", [])}
            for name, cfg in (CONFIG.peers or {}).items()
        ],
        "timeout": CONFIG.peers_timeout,
    }


@app.post("/api/peer/message", dependencies=[Depends(require_auth)])
async def receive_peer_message(req: PeerMessageRequest):
    """Inbound bot DM from another Colons instance."""
    return await manager.receive_peer_message(req.model_dump())


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

@app.get("/api/config")
async def get_config():
    cfg = CONFIG.to_dict(redact_secrets=True)
    cfg["ui"] = {
        "title": CONFIG.ui.title,
        "primary_color": CONFIG.ui.primary_color,
        "theme": CONFIG.ui.theme,
    }
    return cfg


# --------------------------------------------------------------------------- #
# OpenAI-compatible passthrough (lets any OpenAI client talk to Colons)
# --------------------------------------------------------------------------- #

@app.post("/v1/chat/completions", dependencies=[Depends(require_auth)])
async def openai_compat(req: OpenAIChatRequest):
    """
    OpenAI-compatible endpoint. Runs a *single* provider completion with the
    requested messages (no agentic tool loop), so existing tools can point at Colons.
    Supports both streaming (SSE) and non-streaming responses.
    """
    from fastapi.responses import StreamingResponse

    provider = manager.provider
    model = req.model or CONFIG.provider.model or ProviderFactory.default_model(CONFIG.provider.name)

    if not req.stream:
        completion = await provider.chat(
            messages=req.messages,
            model=model,
            temperature=req.temperature if req.temperature is not None else CONFIG.provider.temperature,
            max_tokens=req.max_tokens or CONFIG.provider.max_tokens,
            stream=False,
            tools=req.tools,
        )
        return completion.to_openai()

    async def sse() -> AsyncGenerator[str, None]:
        try:
            stream = await provider.chat(
                messages=req.messages,
                model=model,
                temperature=req.temperature if req.temperature is not None else CONFIG.provider.temperature,
                max_tokens=req.max_tokens or CONFIG.provider.max_tokens,
                stream=True,
                tools=req.tools,
            )
            async for chunk in stream:
                delta: Dict[str, Any] = {}
                finish_reason = None
                for choice in chunk.choices:
                    d = choice.get("delta") or {}
                    delta = {k: v for k, v in d.items() if v is not None}
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
                frame: Dict[str, Any] = {
                    "id": chunk.id,
                    "object": "chat.completion.chunk",
                    "created": chunk.created,
                    "model": chunk.model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
                }
                if chunk.usage is not None:
                    frame["usage"] = {
                        "prompt_tokens": chunk.usage.prompt_tokens,
                        "completion_tokens": chunk.usage.completion_tokens,
                        "total_tokens": chunk.usage.total_tokens,
                    }
                yield f"data: {json.dumps(frame)}\n\n"
        except Exception as e:
            logger.warning(f"/v1 stream failed: {e}")
            error_frame = {
                "error": {"message": str(e), "type": "provider_error", "code": "provider_error"},
            }
            yield f"data: {json.dumps(error_frame)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/v1/models", dependencies=[Depends(require_auth)])
async def openai_models():
    models = await manager.provider.list_models()
    if not models:
        models = [CONFIG.provider.model or ProviderFactory.default_model(CONFIG.provider.name)]
    return {
        "object": "list",
        "data": [{"id": m, "object": "model", "owned_by": CONFIG.provider.name} for m in models if m],
    }


# --------------------------------------------------------------------------- #
# Schedules (cronjobs)
# --------------------------------------------------------------------------- #

class ScheduleRequest(BaseModel):
    cron: str = Field(..., description="Cron expression, @every 5m, or @at <iso>")
    prompt: str
    name: str = ""
    user_id: str = "default"
    agent_id: Optional[str] = None
    enabled: bool = True
    notify_adapter: Optional[str] = None
    notify_chat_id: Optional[str] = None
    timezone: Optional[str] = None
    continuity: bool = False
    notes: str = ""
    monitor_command: str = ""


class ScheduleUpdateRequest(BaseModel):
    name: Optional[str] = None
    cron: Optional[str] = None
    prompt: Optional[str] = None
    enabled: Optional[bool] = None
    notify_adapter: Optional[str] = None
    notify_chat_id: Optional[str] = None
    continuity: Optional[bool] = None
    notes: Optional[str] = None
    monitor_command: Optional[str] = None


def _require_scheduler():
    if not manager or not manager.scheduler:
        raise HTTPException(status_code=503, detail="Scheduler is disabled")
    return manager.scheduler


@app.get("/api/schedules", dependencies=[Depends(require_auth)])
async def list_schedules():
    scheduler = _require_scheduler()
    return {
        "schedules": [s.to_dict() for s in scheduler.list()],
        "status": scheduler.status(),
    }


@app.post("/api/schedules", dependencies=[Depends(require_auth)])
async def create_schedule(req: ScheduleRequest):
    scheduler = _require_scheduler()
    metadata = {}
    if req.timezone:
        metadata["timezone"] = req.timezone
    schedule = Schedule(
        name=req.name, cron=req.cron, prompt=req.prompt, user_id=req.user_id,
        agent_id=req.agent_id, enabled=req.enabled, metadata=metadata,
        notify_adapter=req.notify_adapter, notify_chat_id=req.notify_chat_id,
        continuity=req.continuity, notes=req.notes, monitor_command=req.monitor_command,
    )
    try:
        scheduler.add(schedule)
    except CronError as e:
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: {e}") from e
    return schedule.to_dict()


@app.get("/api/schedules/{schedule_id}", dependencies=[Depends(require_auth)])
async def get_schedule(schedule_id: str):
    scheduler = _require_scheduler()
    schedule = scheduler.get(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return schedule.to_dict()


@app.patch("/api/schedules/{schedule_id}", dependencies=[Depends(require_auth)])
async def update_schedule(schedule_id: str, req: ScheduleUpdateRequest):
    scheduler = _require_scheduler()
    try:
        schedule = scheduler.update(schedule_id, **req.model_dump(exclude_none=True))
    except CronError as e:
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: {e}") from e
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return schedule.to_dict()


@app.delete("/api/schedules/{schedule_id}", dependencies=[Depends(require_auth)])
async def delete_schedule(schedule_id: str):
    scheduler = _require_scheduler()
    if not scheduler.remove(schedule_id):
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"deleted": True}


@app.post("/api/schedules/{schedule_id}/run", dependencies=[Depends(require_auth)])
async def run_schedule_now(schedule_id: str):
    scheduler = _require_scheduler()
    if not scheduler.get(schedule_id):
        raise HTTPException(status_code=404, detail="Schedule not found")
    result = await scheduler.run_now(schedule_id)
    return {"ran": True, "result": result}


@app.get("/api/schedules/validate/{expression:path}", dependencies=[Depends(require_auth)])
async def validate_cron(expression: str):
    try:
        return SchedulerService.validate(expression)
    except CronError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/schedules/history/recent", dependencies=[Depends(require_auth)])
async def schedule_history(limit: int = 50):
    scheduler = _require_scheduler()
    return {"runs": scheduler.history(limit)}


# --------------------------------------------------------------------------- #
# Doctor (diagnostics)
# --------------------------------------------------------------------------- #

@app.get("/api/doctor", dependencies=[Depends(require_auth)])
async def doctor(check_provider: bool = True):
    report = await run_doctor(config=CONFIG, manager=manager, check_provider=check_provider)
    return report.to_dict()


# --------------------------------------------------------------------------- #
# Debug & logs
# --------------------------------------------------------------------------- #

@app.get("/api/debug/logs", dependencies=[Depends(require_auth)])
async def debug_logs(level: str = "INFO", limit: int = 100, logger_prefix: Optional[str] = None):
    import logging as _logging
    level_value = getattr(_logging, level.upper(), _logging.INFO)
    records = LOG_RING.tail(level=level_value, limit=min(limit, 1000),
                            logger_prefix=logger_prefix) if LOG_RING else []
    return {"records": records, "count": len(records), "level": level.upper()}


@app.delete("/api/debug/logs", dependencies=[Depends(require_auth)])
async def clear_debug_logs():
    if LOG_RING:
        LOG_RING.clear()
    return {"cleared": True}


@app.get("/api/debug/stats", dependencies=[Depends(require_auth)])
async def debug_stats():
    stats = process_stats()
    stats["agents"] = len(manager.agents) if manager else 0
    stats["schedules"] = len(manager.scheduler.list()) if manager and manager.scheduler else 0
    stats["messaging"] = len(manager.messaging.adapters) if manager and manager.messaging else 0
    stats["cache"] = manager.cache.stats() if manager else {}
    stats["memory_entries"] = manager.store.count() if manager else 0
    return stats


@app.get("/api/debug/config", dependencies=[Depends(require_auth)])
async def debug_config():
    return CONFIG.to_dict(redact_secrets=True)


@app.get("/api/debug/env", dependencies=[Depends(require_auth)])
async def debug_env():
    return {"env": redacted_env()}


@app.post("/api/debug/log-level", dependencies=[Depends(require_auth)])
async def set_log_level(level: str = "INFO"):
    from colons_core.observability import set_level
    set_level(level)
    return {"level": level.upper()}


# --------------------------------------------------------------------------- #
# Messaging integrations
# --------------------------------------------------------------------------- #

class MessagingConfigureRequest(BaseModel):
    model_config = {'extra': 'forbid'}

    enabled: bool
    bot_token: Optional[str] = Field(default=None, max_length=2048)
    signing_secret: Optional[str] = Field(default=None, max_length=2048)
    webhook_url: Optional[str] = Field(default=None, max_length=2048)
    default_channel: Optional[str] = Field(default=None, max_length=256)
    allowed_users: Optional[List[str]] = Field(default=None, max_length=100)
    allowed_chats: Optional[List[str]] = Field(default=None, max_length=100)


@app.get("/api/messaging/config", dependencies=[Depends(require_auth)])
async def messaging_config():
    return manager.messaging_settings.public(manager.messaging)


@app.put("/api/messaging/config/{service}", dependencies=[Depends(require_auth)])
async def configure_messaging(service: Literal['telegram', 'discord', 'slack'],
                              req: MessagingConfigureRequest):
    changes = req.model_dump(exclude_none=True)
    allowed = {'enabled', 'bot_token', 'allowed_users', 'allowed_chats'}
    if service == 'slack':
        allowed |= {'signing_secret', 'webhook_url', 'default_channel'}
    if changes.keys() - allowed:
        raise HTTPException(status_code=400, detail="Unsupported settings for this service")
    try:
        return await manager.messaging_settings.update(manager, service, changes)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail="Could not save messaging settings on the server") from error


class MessagingSendRequest(BaseModel):
    adapter: str
    chat_id: str
    text: str


@app.get("/api/messaging/status", dependencies=[Depends(require_auth)])
async def messaging_status():
    if not manager or not manager.messaging:
        return {"enabled": False, "adapters": []}
    return {"enabled": True, "adapters": manager.messaging_settings.statuses(manager.messaging)}


@app.post("/api/messaging/send", dependencies=[Depends(require_auth)])
async def messaging_send(req: MessagingSendRequest):
    if not manager or not manager.messaging:
        raise HTTPException(status_code=503, detail="Messaging is not enabled")
    ok = await manager.messaging.notify(req.adapter, req.chat_id, req.text)
    if not ok:
        raise HTTPException(status_code=400, detail=f"Send failed for adapter '{req.adapter}'")
    return {"sent": True}


@app.post("/api/messaging/slack/events")
async def slack_events(request: Request):
    """Slack Events API receiver (signature verified when a secret is set)."""
    if not manager or not manager.messaging:
        raise HTTPException(status_code=503, detail="Messaging is not enabled")
    adapter = manager.messaging.get("slack")
    if adapter is None:
        raise HTTPException(status_code=404, detail="Slack adapter not configured")

    body = await request.body()
    timestamp = request.headers.get("x-slack-request-timestamp", "")
    signature = request.headers.get("x-slack-signature", "")
    if not adapter.verify_signature(body, timestamp, signature):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail="Invalid JSON") from e

    challenge = await adapter.handle_event(payload)
    if challenge is not None:
        return PlainTextResponse(challenge)
    return JSONResponse({"ok": True})


@app.post("/api/messaging/webhook/{name}")
async def generic_webhook(name: str, request: Request, token: Optional[str] = None):
    """Generic incoming webhook -> agent."""
    if not manager or not manager.messaging:
        raise HTTPException(status_code=503, detail="Messaging is not enabled")
    adapter = manager.messaging.get(f"webhook:{name}")
    if adapter is None:
        raise HTTPException(status_code=404, detail=f"Webhook '{name}' not configured")
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid JSON") from e
    try:
        handled = await adapter.handle_event(payload, token=token or request.query_params.get("token"))
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    return {"handled": handled}


# --------------------------------------------------------------------------- #
# Static web UI (served from web/dist when built - single-binary experience)
# --------------------------------------------------------------------------- #

def _resolve_web_dist() -> Optional[Path]:
    """Find the built web UI across env var, source tree, and container layouts."""
    candidates = [
        os.environ.get("COLONS_WEB_DIST"),
        Path(__file__).resolve().parents[2] / "web" / "dist",  # source checkout
        Path(__file__).resolve().parents[3] / "web" / "dist",
        Path.cwd() / "web" / "dist",
        Path(__file__).resolve().parent / "static",  # installed wheel
        Path("/app/web/dist"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if (path / "index.html").is_file():
            return path
    return None


_WEB_DIST = _resolve_web_dist()

if _WEB_DIST is not None:
    app.mount("/assets", StaticFiles(directory=str(Path(_WEB_DIST) / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        """Serve the SPA; unknown paths fall back to index.html."""
        candidate = Path(_WEB_DIST) / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        index = Path(_WEB_DIST) / "index.html"
        if index.is_file():
            return FileResponse(index)
        raise HTTPException(status_code=404, detail="Not found")
