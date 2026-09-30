"""
FastAPI backend for Colons
REST API with WebSocket support, usage tracking
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import time
import json
import asyncio
from contextlib import asynccontextmanager

from providers import ProviderFactory, ProviderAdapter
from providers.base import Message, Usage
from memory import MemoryStore
from memory.cache import CacheStore
from agents import AgentHarness, AgentConfig, Task

# Global state
agent: Optional[AgentHarness] = None
cache: Optional[CacheStore] = None
memory: Optional[MemoryStore] = None

# Configuration - in production, load from env vars
PROVIDER_NAME = "ollama"
PROVIDER_API_KEY = "ollama"  # Ollama doesn't need a key
PROVIDER_BASE_URL = "http://localhost:11434"
DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/colons"
REDIS_URL = "redis://localhost:6379/0"
DEFAULT_MODEL = "llama3.1"

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    global agent, cache, memory

    # Initialize components
    cache = CacheStore(REDIS_URL, default_ttl=3600)
    memory = MemoryStore(DATABASE_URL, redis_url=REDIS_URL)

    # Create provider
    provider = ProviderFactory.create(
        provider=PROVIDER_NAME,
        api_key=PROVIDER_API_KEY,
        base_url=PROVIDER_BASE_URL
    )

    # Create agent
    config = AgentConfig(
        user_id="default",
        name="Colons Agent",
        model=DEFAULT_MODEL,
        provider=PROVIDER_NAME,
        enable_proactive=True
    )

    agent = AgentHarness(
        provider=provider,
        memory=memory,
        cache=cache,
        config=config
    )

    yield

    # Cleanup
    if agent:
        agent.reset()

app = FastAPI(
    title="Colons API",
    description="Open-source Dots alternative - self-hostable AI agent",
    version="0.1.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
security = HTTPBearer(auto_error=False)

# === Pydantic Models ===

class ChatRequest(BaseModel):
    message: str
    stream: bool = True
    model: Optional[str] = None
    tools: Optional[List[Dict]] = None

class ChatResponse(BaseModel):
    content: str
    usage: Dict[str, int]
    model: str
    task_id: Optional[str] = None

class CreateTaskRequest(BaseModel):
    description: str = Field(..., min_length=1)

class TaskResponse(BaseModel):
    id: str
    user_id: str
    description: str
    status: str
    result: Optional[Any] = None
    error: Optional[str] = None
    steps: List[Dict] = []

class MemorySearchRequest(BaseModel):
    query: str
    limit: int = 5

class ProviderConfig(BaseModel):
    name: str
    api_key: str
    base_url: Optional[str] = None
    model: str = "gpt-4"

class UsageResponse(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost: float

# === API Routes ===

@app.get("/")
async def root():
    """Health check"""
    return {
        "status": "ok",
        "service": "Colons",
        "version": "0.1.0",
        "provider": PROVIDER_NAME,
        "model": DEFAULT_MODEL,
        "timestamp": time.time()
    }

@app.get("/health")
async def health():
    """Detailed health check"""
    health_status = {
        "api": True,
        "provider": False,
        "memory": False,
        "cache": False
    }

    # Check provider
    try:
        if agent and agent.provider:
            health_status["provider"] = True
    except Exception:
        pass

    # Check memory
    try:
        if memory:
            health_status["memory"] = True
    except Exception:
        pass

    # Check cache
    try:
        if cache:
            health_status["cache"] = cache.exists("health_check") or True
    except Exception:
        pass

    all_healthy = all(health_status.values())
    return {
        "healthy": all_healthy,
        "components": health_status,
        "timestamp": time.time()
    }

@app.post("/chat")
async def chat(request: ChatRequest):
    """Send a message to the agent (non-streaming)"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    response = await agent.chat_non_stream(
        message=request.message,
        tools=request.tools
    )

    return ChatResponse(
        content=response["content"],
        usage=response["usage"],
        model=response["model"]
    )

@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """WebSocket endpoint for streaming chat"""
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_text()
            request = ChatRequest.model_validate_json(data)

            if not agent:
                await websocket.send_json({"error": "Agent not initialized"})
                continue

            # Stream response
            full_content = ""
            async for chunk in agent.chat(
                message=request.message,
                stream=True,
                tools=request.tools
            ):
                await websocket.send_json({
                    "type": "chunk",
                    "content": chunk,
                    "done": False
                })
                full_content += chunk

            await websocket.send_json({
                "type": "done",
                "content": full_content,
                "usage": agent.provider.get_usage(),
                "done": True
            })

    except WebSocketDisconnect:
        pass
    except Exception as e:
        await websocket.send_json({"error": str(e)})
        await websocket.close()

@app.post("/tasks")
async def create_task(request: CreateTaskRequest):
    """Create a new task for the agent"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    task = await agent.create_task(description=request.description)
    return TaskResponse(
        id=task.id,
        user_id=task.user_id,
        description=task.description,
        status=task.status,
        result=task.result,
        error=task.error,
        steps=task.steps
    )

@app.post("/tasks/{task_id}/execute")
async def execute_task(task_id: str):
    """Execute a task autonomously"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    task = agent.tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    result = await agent.execute_task(task)

    return TaskResponse(
        id=task.id,
        user_id=task.user_id,
        description=task.description,
        status=task.status,
        result=result,
        error=task.error,
        steps=task.steps
    )

@app.get("/tasks")
async def list_tasks(status: Optional[str] = None):
    """List tasks, optionally filtered by status"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    tasks = agent.tasks.values()
    if status:
        tasks = [t for t in tasks if t.status == status]

    return [
        TaskResponse(
            id=t.id,
            user_id=t.user_id,
            description=t.description,
            status=t.status,
            result=t.result,
            error=t.error,
            steps=t.steps
        )
        for t in tasks
    ]

@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Get a specific task"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    task = agent.tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return TaskResponse(
        id=task.id,
        user_id=task.user_id,
        description=task.description,
        status=task.status,
        result=task.result,
        error=task.error,
        steps=task.steps
    )

@app.post("/memory/search")
async def search_memory(request: MemorySearchRequest):
    """Search agent's memory"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    results = await agent.search_memory(query=request.query, limit=request.limit)
    return {"results": results}

@app.get("/memory/history")
async def get_memory_history(limit: int = 20, offset: int = 0):
    """Get conversation history"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    history = await agent.memory.get_history(
        user_id=agent.config.user_id,
        limit=limit,
        offset=offset
    )

    return {
        "history": [entry.to_dict() for entry in history],
        "total": len(history)
    }

@app.post("/memory/store")
async def store_memory(content: str, metadata: Optional[Dict] = None):
    """Store a memory entry"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    entry = await agent.memory.store(
        user_id=agent.config.user_id,
        content=content,
        metadata=metadata
    )

    return {"id": entry.id, "created_at": entry.created_at}

@app.delete("/memory")
async def clear_memory(memory_id: Optional[int] = None):
    """Clear memory"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    await agent.memory.delete(user_id=agent.config.user_id, memory_id=memory_id)
    return {"status": "cleared"}

@app.get("/usage")
async def get_usage():
    """Get current usage statistics"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    usage = agent.provider.get_usage()

    # Estimate cost (rough)
    estimated_cost = (usage.prompt_tokens * 0.000015) + (usage.completion_tokens * 0.00003)

    return UsageResponse(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        estimated_cost=estimated_cost
    )

@app.post("/provider/switch")
async def switch_provider(config: ProviderConfig):
    """Switch to a different AI provider"""
    global agent

    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    provider = ProviderFactory.create(
        provider=config.name,
        api_key=config.api_key,
        base_url=config.base_url
    )

    agent.provider = provider
    agent.config.model = config.model
    agent.config.provider = config.name

    return {
        "status": "switched",
        "provider": config.name,
        "model": config.model
    }

@app.get("/providers")
async def list_providers():
    """List all available providers"""
    return ProviderFactory.list_providers()

@app.post("/agent/config")
async def update_config(model: Optional[str] = None, temperature: Optional[float] = None):
    """Update agent configuration"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    if model:
        agent.config.model = model
    if temperature is not None:
        agent.config.temperature = temperature

    return agent.config

@app.get("/agent/status")
async def get_agent_status():
    """Get agent status"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    return agent.get_status()

@app.post("/agent/reset")
async def reset_agent():
    """Reset agent state"""
    global agent

    if agent:
        agent.reset()

    return {"status": "reset"}

@app.post("/cache/clear")
async def clear_cache(pattern: str = "colons:*"):
    """Clear cache"""
    if not cache:
        raise HTTPException(status_code=503, detail="Cache not initialized")

    cache.clear_pattern(pattern)
    return {"status": "cleared", "pattern": pattern}