"""
Colons Agent Harness
The core agentic loop: reasoning, tool calling, memory, tasks, proactive work.

Design goals:
- Provider-agnostic (any adapter implementing ProviderAdapter)
- Tool-calling loop with iteration budget and approval gating
- Streaming typed events for UI/CLI consumption
- Short-term conversation + long-term semantic memory
- Background proactive research
- Lightweight: no framework dependencies
"""
import asyncio
import base64
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from ..audio.tts import TTSConfig, TTSService
from ..avatars.manager import AvatarManager
from ..browser import BrowserTools
from ..memory.cache import BaseCache
from ..memory.embeddings import EmbeddingService
from ..memory.store import BaseStore, MemoryEntry
from ..providers.base import Message, ProviderAdapter, ProviderError, Usage
from ..tools.base import PermissionLevel
from ..tools.builtin import register_all_builtin_tools
from ..tools.manager import ApprovalRequest, ToolManager, ToolRegistry
from .attachments import validate_attachments
from .session_store import SessionMeta, SessionStore

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Events streamed to clients
# --------------------------------------------------------------------------- #

class EventType(str, Enum):
    START = "start"
    STATUS = "status"
    DELTA = "delta"                 # token chunk
    REASONING = "reasoning"         # model reasoning tokens (if any)
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    APPROVAL_REQUIRED = "approval_required"
    TASK_UPDATE = "task_update"
    MEMORY = "memory"
    USAGE = "usage"
    ERROR = "error"
    DONE = "done"


def event(type: EventType, **data) -> Dict:
    return {"type": type.value, "timestamp": time.time(), **data}


class AgentState(str, Enum):
    INITIALIZING = "initializing"
    IDLE = "idle"
    THINKING = "thinking"
    TOOL_CALLING = "tool_calling"
    RESPONDING = "responding"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


# --------------------------------------------------------------------------- #
# Task model
# --------------------------------------------------------------------------- #

@dataclass
class Task:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    user_id: str = "default"
    agent_id: str = "default"
    description: str = ""
    status: str = "pending"  # pending|planning|running|completed|failed|cancelled
    priority: int = 1
    plan: List[str] = field(default_factory=list)
    steps: List[Dict] = field(default_factory=list)
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    def to_dict(self) -> Dict:
        return {
            "id": self.id, "user_id": self.user_id, "agent_id": self.agent_id,
            "description": self.description, "status": self.status,
            "priority": self.priority, "plan": self.plan, "steps": self.steps,
            "result": self.result, "error": self.error, "created_at": self.created_at,
            "started_at": self.started_at, "completed_at": self.completed_at,
        }


# --------------------------------------------------------------------------- #
# Conversation session
# --------------------------------------------------------------------------- #

DEFAULT_SYSTEM_PROMPT = """You are {name}, a personal AI assistant in the Colons workspace.

You work on behalf of your user. You can use tools to accomplish real tasks:
reading and writing files, running commands, searching the web, executing code,
and remembering information in long-term memory.

Guidelines:
- Be proactive but careful. For anything destructive or irreversible, ask first.
- Think step by step. Use tools when they help; don't use them when they don't.
- When you learn something important about the user or their work, store it with `remember`.
- Before starting a substantial task, briefly outline your plan.
- Keep responses clear and concise. Prefer concrete results over chatter.
- If a task is ambiguous, ask a focused clarifying question.
- You are running in {mode} mode.
"""


@dataclass
class Session:
    """A conversation session with an agent."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    agent_id: str = "default"
    user_id: str = "default"
    title: str = "New chat"
    messages: List[Message] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add(self, role: str, content: str, **kwargs):
        self.messages.append(Message(role=role, content=content, **kwargs))
        self.updated_at = time.time()

    def to_dict(self) -> Dict:
        return {
            "id": self.id, "agent_id": self.agent_id, "user_id": self.user_id,
            "title": self.title, "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": len(self.messages),
            "metadata": self.metadata,
        }


# --------------------------------------------------------------------------- #
# The agent harness
# --------------------------------------------------------------------------- #

class AgentHarness:
    """
    The full agentic harness. One instance per (user, agent) pair, or many
    with an `agents` registry in the API server.
    """

    def __init__(
        self,
        provider: ProviderAdapter,
        store: BaseStore,
        cache: Optional[BaseCache] = None,
        embeddings: Optional[EmbeddingService] = None,
        tts: Optional[TTSService] = None,
        avatars: Optional[AvatarManager] = None,
        *,
        agent_id: str = "default",
        user_id: str = "default",
        name: str = "Colons",
        system_prompt: Optional[str] = None,
        workspace: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        max_iterations: int = 15,
        context_messages: int = 40,
        proactive: bool = False,
        proactive_interval: int = 300,
        auto_approve_tools: bool = False,
        shell_allowlist: Optional[List[str]] = None,
        disabled_tools: Optional[List[str]] = None,
        approval_callback: Optional[Callable[[ApprovalRequest], Any]] = None,
        voice_enabled: bool = False,
        voice_config: Optional[TTSConfig] = None,
        session_store: Optional[SessionStore] = None,
        tool_timeout: float = 120.0,
        memory_limit: int = 10000,
        peer_router: Optional[Callable[[str, str], Any]] = None,
        peer_roster: str = "",
    ):
        self.agent_id = agent_id
        self.user_id = user_id
        self.name = name
        self.provider = provider
        self.store = store
        self.cache = cache
        self.embeddings = embeddings or EmbeddingService(provider=provider, cache=cache)
        self.tts = tts
        self.avatars = avatars or AvatarManager()
        self.session_store = session_store

        self.model = model or self._default_model()
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_iterations = max_iterations
        self.context_messages = context_messages
        self.proactive_enabled = proactive
        self.proactive_interval = proactive_interval
        self.proactive_min_interval = 6 * 3600  # at most one proactive note per 6h
        self._last_proactive_review = time.time()
        self.voice_enabled = voice_enabled
        self.voice_config = voice_config or TTSConfig()

        self.state = AgentState.INITIALIZING
        self.sessions: Dict[str, Session] = {}
        self.tasks: Dict[str, Task] = {}
        self._task_queue: "asyncio.Queue[str]" = asyncio.Queue()
        self._proactive_task: Optional[asyncio.Task] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        self._paused = False
        self._cancel_flags: Dict[str, bool] = {}
        self.started_at = time.time()
        self.total_usage = Usage()

        # Deterministic avatar unless one was explicitly selected for this agent
        if self.avatars._selected.get(agent_id):
            self.avatar = self.avatars.current(agent_id)
        else:
            generated = self.avatars.generate_for(agent_id)
            self.avatars.select(agent_id, generated.id)
            self.avatar = generated

        # Tools
        self.registry = ToolRegistry()
        register_all_builtin_tools(
            self.registry,
            workspace=workspace,
            shell_allowlist=shell_allowlist,
            memory_store=store,
            embeddings=self.embeddings,
            user_id=user_id,
            agent_id=agent_id,
        )
        self.browser = BrowserTools(workspace, namespace=f"{user_id}:{agent_id}")
        self.browser.register(self.registry)
        for disabled in (disabled_tools or []):
            self.registry.unregister(disabled)

        # Bot-to-bot messaging tools (only when a router is wired up)
        self.peer_router = peer_router
        if peer_router is not None:
            self._register_peer_tools(peer_router)

        self.tool_manager = ToolManager(
            registry=self.registry,
            approval_callback=approval_callback,
            auto_approve=auto_approve_tools,
            global_timeout=tool_timeout,
        )
        self.memory_limit = memory_limit
        self._memory_writes = 0

        # System prompt
        if system_prompt is None:
            system_prompt = DEFAULT_SYSTEM_PROMPT.format(
                name=self.name,
                mode="proactive" if proactive else "on-demand",
            )
        if peer_roster:
            system_prompt = f"{system_prompt}\n\n{peer_roster}"
        self.system_prompt = system_prompt

        self.state = AgentState.IDLE

    # ------------------------------------------------------------------ #
    # Bot-to-bot messaging tools
    # ------------------------------------------------------------------ #

    def _register_peer_tools(self, router: Callable[[str, str], Any]):
        """Register message_agent + list_teammates against the messenger."""
        router_ref = router

        async def message_agent(target: str, message: str) -> str:
            """Send a message to a teammate bot (e.g. @researcher) and return their reply.
            Use for delegating questions or handing off work. If the teammate has
            nothing to add, the reply is empty. If the teammate is busy, the status
            is 'queued' and the message will be handled in their chat."""
            result = await router_ref(target, message)
            status = result.get("status", "ok")
            if status == "error":
                return f"ERROR: {result.get('message', 'delivery failed')}"
            reply = (result.get("reply") or "").strip()
            if status == "queued":
                return f"(queued for {result.get('target', target)} - they are busy; no reply yet)"
            if not reply:
                return f"({result.get('target', target)} had nothing to add)"
            return reply

        async def list_teammates() -> str:
            """List the teammate bots you can message and their roles."""
            result = await router_ref("", "")
            return result.get("roster", "") or "(no teammates)"

        self.registry.register_func(
            message_agent,
            permission=PermissionLevel.AUTO,
            category="agents",
            returns="string",
            timeout=180.0,
        )
        self.registry.register_func(
            list_teammates,
            permission=PermissionLevel.AUTO,
            category="agents",
            returns="string",
        )

    def set_peer_roster(self, block: str):
        """Refresh the teammate roster section of the system prompt."""
        base = self.system_prompt.split("\n\n## Teammates")[0]
        self.system_prompt = f"{base}\n\n{block}" if block else base

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def _default_model(self) -> str:
        from ..providers import ProviderFactory
        return ProviderFactory.default_model(getattr(self.provider, "name", "")) or "gpt-4o"

    async def start(self):
        """Start background workers and proactive loop."""
        if self._running:
            return
        self._running = True
        self.state = AgentState.IDLE
        self._worker_task = asyncio.create_task(self._task_worker())
        if self.proactive_enabled:
            self._proactive_task = asyncio.create_task(self._proactive_loop())
        logger.info(f"Agent {self.agent_id} started (model={self.model})")

    async def stop(self):
        """Stop all background activity."""
        self._running = False
        for task in (self._worker_task, self._proactive_task):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(
            *[t for t in (self._worker_task, self._proactive_task) if t],
            return_exceptions=True,
        )
        await self.browser.close()
        self.state = AgentState.STOPPED
        logger.info(f"Agent {self.agent_id} stopped")

    def pause(self):
        self._paused = True
        self.state = AgentState.PAUSED

    def resume(self):
        self._paused = False
        self.state = AgentState.IDLE

    def cancel(self, request_id: str):
        self._cancel_flags[request_id] = True

    # ------------------------------------------------------------------ #
    # Sessions
    # ------------------------------------------------------------------ #

    def create_session(self, title: str = "New chat", session_id: Optional[str] = None) -> Session:
        if session_id and self.session_store:
            meta = self.session_store.get_meta(session_id)
            if meta and (meta.user_id, meta.agent_id) != (self.user_id, self.agent_id):
                raise ValueError("Session belongs to another user or agent")
        session = Session(agent_id=self.agent_id, user_id=self.user_id, title=title)
        if session_id:
            session.id = session_id
        self.sessions[session.id] = session
        self._persist_session_meta(session)
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """In-memory session, loading (with messages) from the store if needed."""
        if session_id in self.sessions:
            return self.sessions[session_id]
        if self.session_store:
            meta = self.session_store.get_meta(session_id)
            if meta and (meta.user_id, meta.agent_id) == (self.user_id, self.agent_id):
                session = Session(
                    id=meta.id, agent_id=meta.agent_id, user_id=meta.user_id,
                    title=meta.title, created_at=meta.created_at,
                    updated_at=meta.updated_at, metadata=dict(meta.metadata),
                    messages=self.session_store.messages(session_id),
                )
                self.sessions[session_id] = session
                return session
        return None

    def list_sessions(self) -> List[Dict]:
        if self.session_store:
            metas = self.session_store.list(user_id=self.user_id, agent_id=self.agent_id)
            out = []
            for meta in metas:
                data = meta.to_dict()
                # Prefer the in-memory count when the session is loaded
                live = self.sessions.get(meta.id)
                data["message_count"] = len(live.messages) if live else self._stored_message_count(meta.id)
                out.append(data)
            return out
        sessions = sorted(self.sessions.values(), key=lambda s: s.updated_at, reverse=True)
        return [s.to_dict() for s in sessions]

    def delete_session(self, session_id: str) -> bool:
        if not self.get_session(session_id):
            return False
        self.sessions.pop(session_id, None)
        if self.session_store:
            return self.session_store.delete(session_id)
        return True

    def _get_or_create_session(self, session_id: Optional[str]) -> Session:
        if session_id:
            existing = self.get_session(session_id)
            if existing:
                return existing
        return self.create_session(session_id=session_id)

    def _persist_session_meta(self, session: Session):
        if not self.session_store:
            return
        self.session_store.save(SessionMeta(
            id=session.id, agent_id=session.agent_id, user_id=session.user_id,
            title=session.title, created_at=session.created_at,
            updated_at=session.updated_at, metadata=dict(session.metadata),
        ))

    def _append_message(self, session: Session, role: str, content: str, **kwargs):
        """Append to the in-memory session and persist to the session store."""
        session.add(role, content, **kwargs)
        if self.session_store:
            msg = session.messages[-1]
            self.session_store.append_message(session.id, msg)

    def _stored_message_count(self, session_id: str) -> int:
        if not self.session_store:
            return 0
        try:
            return self.session_store.count_messages(session_id)
        except Exception:
            return 0

    # ------------------------------------------------------------------ #
    # Main entry: chat
    # ------------------------------------------------------------------ #

    async def chat(
        self,
        message: str,
        session_id: Optional[str] = None,
        stream: bool = True,
        attachments: Optional[List[Dict]] = None,
        request_id: Optional[str] = None,
        system_hint: Optional[str] = None,
        approval_callback: Optional[Callable] = None,
    ) -> AsyncGenerator[Dict, None]:
        """
        Process a user message with the full agentic loop.
        Yields typed events (see EventType).
        """
        attachments = validate_attachments(attachments)
        request_id = request_id or uuid.uuid4().hex[:12]
        session = self._get_or_create_session(session_id)
        self._cancel_flags[request_id] = False

        if session.title == "New chat" and message:
            session.title = message[:60]
            if self.session_store:
                self.session_store.touch(session.id, title=session.title)

        self.state = AgentState.THINKING
        try:
            yield event(EventType.START, request_id=request_id, session_id=session.id)
            # Persist the user message within the cancellation cleanup scope.
            self._append_message(session, "user", message, attachments=attachments or None)
            await self._remember_message(session, "user", message)
            # Build initial context
            context = await self._build_context(session, message)
            if system_hint:
                context.append(Message(role="system", content=system_hint))

            # Agentic loop
            iterations = 0
            final_text = ""
            accumulated_tool_calls = 0

            while iterations < self.max_iterations:
                iterations += 1
                if self._cancel_flags.get(request_id):
                    yield event(EventType.STATUS, state="cancelled", message="Cancelled by user")
                    break

                self.state = AgentState.THINKING
                yield event(EventType.STATUS, state=self.state.value,
                            iteration=iterations, max_iterations=self.max_iterations)

                completion = await self._complete(context, stream=stream)

                # --- Streaming path: completion is an async generator ---
                if stream and hasattr(completion, "__aiter__"):
                    text_parts: List[str] = []
                    tool_call_buffers: Dict[int, Dict] = {}
                    finish_reason = None

                    async for chunk in completion:
                        if self._cancel_flags.get(request_id):
                            break
                        if not chunk.choices:
                            if chunk.usage:
                                await self._record_usage(chunk.usage)
                            continue
                        choice = chunk.choices[0]
                        delta = choice.get("delta", {})

                        reasoning = delta.get("reasoning_content")
                        if reasoning:
                            yield event(EventType.REASONING, content=reasoning)

                        content = delta.get("content")
                        if content:
                            text_parts.append(content)
                            yield event(EventType.DELTA, content=content)

                        for tc in (delta.get("tool_calls") or []):
                            idx = tc.get("index", 0)
                            buf = tool_call_buffers.setdefault(
                                idx, {"id": "", "name": "", "arguments": ""}
                            )
                            if tc.get("id"):
                                buf["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                buf["name"] += fn["name"]
                            if fn.get("arguments"):
                                buf["arguments"] += fn["arguments"]

                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
                        if chunk.usage:
                            await self._record_usage(chunk.usage)

                    text = "".join(text_parts)
                    tool_calls = [
                        {
                            "id": b["id"] or f"call_{i}",
                            "type": "function",
                            "function": {"name": b["name"], "arguments": b["arguments"] or "{}"},
                        }
                        for i, b in tool_call_buffers.items()
                    ]
                    finish_reason = finish_reason or ("tool_calls" if tool_calls else "stop")

                # --- Non-stream path: completion is a ChatCompletion ---
                else:
                    msg = completion.choices[0]["message"]
                    text = msg.get("content") or ""
                    tool_calls = msg.get("tool_calls") or []
                    finish_reason = completion.choices[0].get("finish_reason", "stop")
                    await self._record_usage(completion.usage)
                    if text and stream:
                        yield event(EventType.DELTA, content=text)

                # Record assistant turn (even if only tool calls)
                assistant_msg = Message(
                    role="assistant",
                    content=text,
                    tool_calls=tool_calls or None,
                )
                context.append(assistant_msg)

                if text:
                    final_text = text

                # No tool calls -> done
                if not tool_calls:
                    if final_text:
                        self._append_message(session, "assistant", final_text)
                        await self._remember_message(session, "assistant", final_text)
                    break

                # --- Execute tools ---
                self.state = AgentState.TOOL_CALLING
                for tc in tool_calls:
                    accumulated_tool_calls += 1
                    fn = tc.get("function", {})
                    tool_name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                        yield event(EventType.ERROR, message=f"Malformed arguments for {tool_name}")

                    yield event(EventType.TOOL_CALL, tool=tool_name, arguments=args,
                                tool_call_id=tc.get("id"))

                    result = await self.tool_manager.execute(tool_name, args, approval_callback=approval_callback)

                    if result.status.value == "denied":
                        yield event(EventType.APPROVAL_REQUIRED, tool=tool_name,
                                    arguments=args,
                                    reason=result.error)
                    yield event(EventType.TOOL_RESULT, tool=tool_name,
                                success=result.success,
                                output=result.to_dict().get("output"),
                                error=result.error,
                                duration_ms=result.duration_ms,
                                tool_call_id=tc.get("id"))

                    # Feed the result back into the conversation
                    context.append(Message(
                        role="tool",
                        content=json.dumps(result.to_dict(), default=str)[:20000],
                        tool_call_id=tc.get("id"),
                        name=tool_name,
                    ))
                    # Feed browser captures to vision-capable models as image input,
                    # keeping base64 out of tool-result text and the UI event payload.
                    if tool_name == "browser_screenshot" and result.success and self.provider.supports_vision:
                        capture = Path(result.output["path"])
                        expected = self.browser.workspace / ".colons" / "browser" / self.browser.namespace
                        if capture.resolve().parent == expected.resolve() and capture.stat().st_size <= 2 * 1024 * 1024:
                            encoded = base64.b64encode(capture.read_bytes()).decode()
                            context.append(Message(role="user", content="Inspect this browser screenshot.", attachments=[{
                                "name": capture.name, "mime_type": "image/png",
                                "data_url": f"data:image/png;base64,{encoded}",
                            }]))
                    # Persist a compact record of the tool result
                    await self._remember_message(
                        session, "tool",
                        f"[{tool_name}] {'OK' if result.success else 'FAILED'}: "
                        f"{str(result.output or result.error)[:500]}",
                        metadata={"tool": tool_name, "success": result.success},
                    )

            else:
                yield event(EventType.ERROR,
                            message=f"Reached max iterations ({self.max_iterations}) without finishing")

            # Final usage summary
            yield event(EventType.USAGE, **{
                "prompt_tokens": self.provider.get_usage().prompt_tokens,
                "completion_tokens": self.provider.get_usage().completion_tokens,
                "total_tokens": self.provider.get_usage().total_tokens,
                "cached_tokens": self.provider.get_usage().cached_tokens,
                "iterations": iterations,
                "tool_calls": accumulated_tool_calls,
            })

            self.state = AgentState.IDLE
            yield event(EventType.DONE, session_id=session.id,
                        content=final_text, request_id=request_id)

            # Optional autospeak
            if self.voice_enabled and final_text and self.tts and self.tts.available:
                try:
                    audio = await self.tts.speak_to_bytes(
                        final_text,
                        voice=self.voice_config.voice,
                        rate=self.voice_config.rate,
                    )
                    yield event(EventType.STATUS, state="voice", audio_bytes=len(audio))
                except Exception as e:
                    logger.warning(f"TTS failed: {e}")

        except ProviderError as e:
            self.state = AgentState.ERROR
            yield event(EventType.ERROR, message=str(e), retryable=e.retryable,
                        provider=e.provider)
        except asyncio.CancelledError:
            self.state = AgentState.IDLE
            raise
        except Exception as e:
            logger.exception("Agent chat failed")
            self.state = AgentState.ERROR
            yield event(EventType.ERROR, message=f"{type(e).__name__}: {e}")
        finally:
            self._cancel_flags.pop(request_id, None)
            if self.state != AgentState.ERROR:
                self.state = AgentState.PAUSED if self._paused else AgentState.IDLE

    # ------------------------------------------------------------------ #
    # Provider call helpers
    # ------------------------------------------------------------------ #

    async def _complete(self, context: List[Message], stream: bool):
        """Call the provider with tool schemas attached."""
        return await self.provider.chat(
            messages=context,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=stream,
            tools=self.registry.openai_schemas() or None,
            tool_choice="auto",
        )

    async def _record_usage(self, usage: Usage):
        if usage:
            self.total_usage = self.total_usage.add(usage)

    # ------------------------------------------------------------------ #
    # Context & memory
    # ------------------------------------------------------------------ #

    def _identity_prompt(self) -> str:
        provider = getattr(self.provider, "name", "") or "configured provider"
        introduction = f"I'm {self.name}, your personal AI assistant in Colons."
        return (
            "## Current assistant identity\n"
            f"Your assistant name is {self.name}. Colons is the assistant workspace; "
            "the underlying model is its engine, not your assistant name.\n"
            f"Configured model: {self.model}\n"
            f"Configured provider: {provider}\n"
            f"When asked who you are, introduce yourself naturally, for example: "
            f'"{introduction} I’m powered by {self.model} via {provider}."\n'
            "Keep your assigned role and personality. Mention the provider/model when "
            "asked about your identity or what powers you, rather than in every reply. "
            "For a simple greeting, respond warmly without a long introduction.\n"
            "Use these current runtime facts instead of earlier self-descriptions in "
            "conversation history. Do not invent a model version, developer affiliation, "
            "or training provenance. Describe only capabilities available in this workspace."
        )

    async def _build_context(self, session: Session, current_message: str) -> List[Message]:
        """System prompt + relevant memories + recent conversation."""
        # Build identity each turn so switching providers/models never leaves stale facts.
        identity = self._identity_prompt()
        context: List[Message] = [Message(
            role="system", content=f"{self.system_prompt}\n\n{identity}",
        )]

        # Semantic recall
        try:
            vector = await self.embeddings.embed(current_message)
            hits = self.store.search(
                self.user_id, vector, limit=5, threshold=0.35, agent_id=self.agent_id
            )
            if hits:
                memory_lines = [
                    f"- ({e.kind}) {e.content[:300]}" for e, _s in hits
                ]
                context.append(Message(
                    role="system",
                    content="Relevant memories:\n" + "\n".join(memory_lines),
                ))
        except Exception as e:
            logger.debug(f"Memory recall skipped: {e}")

        # Conversation window
        recent = session.messages[-self.context_messages:]
        context.extend(recent)
        return context

    async def _remember_message(self, session: Session, role: str, content: str,
                                metadata: Optional[Dict] = None):
        """Persist a message to long-term memory (with embedding)."""
        if not content or role == "tool":
            kind = "tool"
        else:
            kind = "message"
        try:
            vector = await self.embeddings.embed(content)
        except Exception:
            vector = None
        entry = MemoryEntry(
            user_id=self.user_id,
            agent_id=self.agent_id,
            kind=kind,
            role=role,
            content=content,
            embedding=vector,
            metadata={"session_id": session.id, **(metadata or {})},
        )
        try:
            self.store.store(entry)
            self._memory_writes += 1
            # Occasionally prune so memory can't grow unbounded per user
            if self.memory_limit > 0 and self._memory_writes % 100 == 0:
                prune = getattr(self.store, "prune", None)
                if prune and self.store.count(self.user_id) > self.memory_limit:
                    prune(self.user_id, keep=self.memory_limit)
        except Exception as e:
            logger.warning(f"Failed to store memory: {e}")

    async def recall(self, query: str, limit: int = 5) -> List[Dict]:
        vector = await self.embeddings.embed(query)
        hits = self.store.search(self.user_id, vector, limit=limit, threshold=0.1,
                                 agent_id=self.agent_id)
        return [
            {"content": e.content, "kind": e.kind, "role": e.role,
             "similarity": round(s, 3), "created_at": e.created_at}
            for e, s in hits
        ]

    async def forget(self, memory_id: Optional[int] = None):
        return self.store.delete(self.user_id, memory_id=memory_id, agent_id=self.agent_id)

    # ------------------------------------------------------------------ #
    # Tasks
    # ------------------------------------------------------------------ #

    async def create_task(self, description: str, priority: int = 1) -> Task:
        task = Task(user_id=self.user_id, agent_id=self.agent_id,
                    description=description, priority=priority)
        self.tasks[task.id] = task
        await self._task_queue.put(task.id)
        return task

    def list_tasks(self, status: Optional[str] = None) -> List[Dict]:
        tasks = list(self.tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return [t.to_dict() for t in tasks]

    def get_task(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)

    async def _task_worker(self):
        """Background worker that executes queued tasks."""
        while self._running:
            if self._paused:
                await asyncio.sleep(0.1)
                continue
            try:
                task_id = await asyncio.wait_for(self._task_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            while self._paused and self._running:
                await asyncio.sleep(0.1)
            if not self._running:
                break
            task = self.tasks.get(task_id)
            if not task or task.status not in ("pending",):
                continue
            try:
                await self.execute_task(task)
            except Exception as e:
                logger.exception(f"Task {task.id} failed")
                task.status = "failed"
                task.error = str(e)

    async def execute_task(self, task: Task) -> str:
        """Plan and execute a task, recording failures instead of false success."""
        if task.status in ("planning", "running"):
            raise ValueError("Task is already running")
        task.error = None
        task.result = None
        task.steps = []
        task.completed_at = None
        try:
            return await self._execute_task(task)
        except asyncio.CancelledError:
            task.status = "cancelled"
            task.completed_at = time.time()
            raise
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
            task.completed_at = time.time()
            raise

    async def _execute_task(self, task: Task) -> str:
        task.status = "planning"
        task.started_at = time.time()

        # One session per task: planning context carries into execution
        task_session = self.create_session(title=f"Task: {task.description[:40]}")
        plan_events: List[Dict] = []
        async for ev in self.chat(
            f"Create a concise numbered plan (max 7 steps) to accomplish this task. "
            f"Task: {task.description}\n\nRespond ONLY with the plan.",
            session_id=task_session.id,
        ):
            if ev["type"] == EventType.ERROR.value:
                raise RuntimeError(ev.get("message", "Task planning failed"))
            plan_events.append(ev)

        plan_text = next((e.get("content") for e in reversed(plan_events)
                          if e["type"] == EventType.DONE.value), "") or ""
        task.plan = [
            line.strip().lstrip("0123456789.-) ").strip()
            for line in plan_text.splitlines()
            if line.strip() and any(ch.isalnum() for ch in line)
        ][:7]

        # Execute the task as one instruction to the agent (same session as the plan)
        task.status = "running"
        final = ""
        async for ev in self.chat(
            f"Execute this task now, using tools as needed. Task: {task.description}\n"
            f"Plan:\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(task.plan)) +
            "\n\nWhen finished, report the concrete result.",
            session_id=task_session.id,
        ):
            if ev["type"] == EventType.ERROR.value:
                raise RuntimeError(ev.get("message", "Task execution failed"))
            if ev["type"] == EventType.TOOL_CALL.value:
                task.steps.append({"tool": ev.get("tool"), "arguments": ev.get("arguments"),
                                   "status": "called", "at": time.time()})
            elif ev["type"] == EventType.TOOL_RESULT.value:
                for step in reversed(task.steps):
                    if step.get("tool") == ev.get("tool") and step.get("status") == "called":
                        step["status"] = "ok" if ev.get("success") else "failed"
                        step["output"] = str(ev.get("output") or ev.get("error"))[:500]
                        break
            elif ev["type"] == EventType.DONE.value:
                final = ev.get("content", "")

        task.result = final
        task.status = "completed"
        task.completed_at = time.time()
        return final

    # ------------------------------------------------------------------ #
    # Proactive research
    # ------------------------------------------------------------------ #

    async def _proactive_loop(self):
        """Periodically review recent activity for anything worth surfacing."""
        while self._running:
            try:
                await asyncio.sleep(self.proactive_interval)
                if self._paused or not self._running:
                    continue
                await self._proactive_tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Proactive tick failed: {e}")

    async def _proactive_tick(self):
        """
        Lightweight background review. No model calls: scans memory written since
        the last review and records a compact summary only when something new was
        learned. Throttled to one entry per `proactive_min_interval` (6h default)
        so it never floods memory.
        """
        if self.state not in (AgentState.IDLE,):
            return

        now = time.time()
        if now - self._last_proactive_review < self.proactive_min_interval:
            return

        # Only look at memories created since the last review
        recent = self.store.history(self.user_id, limit=50, agent_id=self.agent_id)
        new_entries = [e for e in recent if e.created_at > self._last_proactive_review]
        self._last_proactive_review = now

        if len(new_entries) < 3:
            return

        by_kind: Dict[str, int] = {}
        for entry in new_entries:
            by_kind[entry.kind] = by_kind.get(entry.kind, 0) + 1

        summary = ", ".join(f"{count} {kind}" for kind, count in sorted(by_kind.items()))
        entry = MemoryEntry(
            user_id=self.user_id,
            agent_id=self.agent_id,
            kind="proactive",
            role="system",
            content=f"Background review: {len(new_entries)} new memories since last review ({summary}).",
            embedding=None,
            metadata={"reviewed": len(new_entries), "kinds": by_kind, "at": now},
        )
        self.store.store(entry)

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    def status(self) -> Dict:
        uptime = time.time() - self.started_at
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "state": self.state.value,
            "paused": self._paused,
            "model": self.model,
            "provider": getattr(self.provider, "name", "unknown"),
            "avatar": self.avatar.to_dict(),
            "uptime_seconds": round(uptime, 1),
            "sessions": (
                self.session_store.count(self.user_id) if self.session_store
                else len(self.sessions)
            ),
            "tasks": {
                "pending": len([t for t in self.tasks.values() if t.status == "pending"]),
                "running": len([t for t in self.tasks.values() if t.status == "running"]),
                "completed": len([t for t in self.tasks.values() if t.status == "completed"]),
                "failed": len([t for t in self.tasks.values() if t.status == "failed"]),
            },
            "tools": len(self.registry),
            "memory_entries": self.store.count(self.user_id),
            "usage": {
                "prompt_tokens": self.total_usage.prompt_tokens,
                "completion_tokens": self.total_usage.completion_tokens,
                "total_tokens": self.total_usage.total_tokens,
                "cached_tokens": self.total_usage.cached_tokens,
            },
            "voice": {
                "enabled": self.voice_enabled,
                "engine": self.tts.engine_name if self.tts else None,
            },
        }
