"""
Agent core harness for Colons
Manages agent lifecycle, task execution, tool use, and memory
"""
import asyncio
import uuid
from typing import AsyncGenerator, List, Dict, Optional, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime

from providers.base import ProviderAdapter, Message, Usage
from memory import MemoryStore
from memory.cache import CacheStore

@dataclass
class Task:
    """Represents a unit of work for the agent"""
    id: str = ""
    user_id: str = ""
    description: str = ""
    status: str = "pending"  # pending, running, completed, failed
    result: Any = None
    error: Optional[str] = None
    steps: List[Dict] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        if self.created_at == 0.0:
            self.created_at = datetime.now().timestamp()
        self.updated_at = self.created_at

@dataclass
class AgentConfig:
    """Configuration for an agent"""
    user_id: str = ""
    name: str = "Colons Agent"
    model: str = "gpt-4"
    provider: str = "openai"
    temperature: float = 0.7
    max_tokens: Optional[int] = 4096
    memory_limit: int = 100  # Max memory entries to keep
    enable_proactive: bool = True  # Enable proactive research
    tools: List[Dict] = field(default_factory=list)  # Available tools/functions

class AgentHarness:
    """Core agent harness - manages everything"""

    def __init__(
        self,
        provider: ProviderAdapter,
        memory: MemoryStore,
        cache: Optional[CacheStore] = None,
        config: Optional[AgentConfig] = None
    ):
        self.provider = provider
        self.memory = memory
        self.cache = cache or CacheStore("redis://localhost:6379/0", default_ttl=3600)
        self.config = config or AgentConfig()

        # State tracking
        self.tasks: Dict[str, Task] = {}
        self.context: List[Message] = []
        self.is_running = False

        # Tool registry
        self.tools: Dict[str, Callable] = {}
        for tool_def in config.tools if config else []:
            self.register_tool(tool_def)

    def register_tool(self, tool_def: Dict):
        """Register a tool the agent can use"""
        name = tool_def.get("name", "unknown")
        self.tools[name] = tool_def

    async def add_message(self, role: str, content: str, **kwargs):
        """Add a message to conversation context"""
        msg = Message(role=role, content=content, **kwargs)
        self.context.append(msg)

        # Store in memory
        await self.memory.store(
            user_id=self.config.user_id,
            content=content,
            metadata={"role": role, "type": "message"}
        )

    async def create_task(self, description: str) -> Task:
        """Create a new task for the agent"""
        task = Task(
            user_id=self.config.user_id,
            description=description
        )
        self.tasks[task.id] = task
        return task

    async def execute_task(self, task: Task) -> Any:
        """Execute a task autonomously"""
        task.status = "running"
        task.updated_at = datetime.now().timestamp()

        try:
            # Add task description to context
            await self.add_message("system", f"Task: {task.description}")

            # Plan execution steps
            plan_msg = await self._generate_plan(task.description)

            # Execute plan
            result = await self._execute_plan(plan_msg, task)

            task.status = "completed"
            task.result = result

        except Exception as e:
            task.status = "failed"
            task.error = str(e)

        task.updated_at = datetime.now().timestamp()
        return task.result

    async def chat(
        self,
        message: str,
        stream: bool = True,
        tools: Optional[List[Dict]] = None
    ) -> AsyncGenerator[str, None]:
        """Send a message and stream the response"""
        await self.add_message("user", message)

        # Prepare messages for provider
        messages = self.context[-20:]  # Context window

        # Get available tools
        available_tools = tools or self.config.tools

        # Make the request
        response = await self.provider.chat(
            messages=messages,
            model=self.config.model,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            stream=True,
            tools=available_tools
        )

        # Stream response
        full_content = ""
        async for chunk in response:
            delta = chunk.choices[0].get("delta", {})
            content = delta.get("content", "")
            if content:
                full_content += content
                yield content

        # Store assistant response
        await self.add_message("assistant", full_content)

        # Update usage
        usage = self.provider.get_usage()

    async def chat_non_stream(
        self,
        message: str,
        tools: Optional[List[Dict]] = None
    ) -> Dict:
        """Send a message and get full response"""
        await self.add_message("user", message)

        messages = self.context[-20:]
        available_tools = tools or self.config.tools

        response = await self.provider.chat(
            messages=messages,
            model=self.config.model,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            stream=False,
            tools=available_tools
        )

        content = response.choices[0]["message"]["content"]
        await self.add_message("assistant", content)

        return {
            "content": content,
            "usage": self.provider.get_usage(),
            "model": response.model
        }

    async def search_memory(self, query: str, limit: int = 5) -> List[Dict]:
        """Search agent's memory for relevant information"""
        # Check cache first
        cache_key = f"memory_search:{query}:{limit}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        # Generate query embedding
        # In production, you'd use the provider's embed method
        # For now, use a simple hash-based approach
        query_embedding = self._simple_embed(query)

        # Search memory
        results = await self.memory.search(
            user_id=self.config.user_id,
            query_embedding=query_embedding,
            limit=limit
        )

        # Format results
        formatted = [
            {"content": entry.content, "similarity": score, "metadata": entry.metadata}
            for entry, score in results
        ]

        # Cache results
        self.cache.set(cache_key, formatted, ttl=600)

        return formatted

    async def run_proactive(self):
        """Run proactive research in background"""
        if not self.config.enable_proactive:
            return

        # Look for patterns, changes, opportunities
        recent_memories = await self.memory.get_history(
            user_id=self.config.user_id,
            limit=10
        )

        # Analyze for proactive suggestions
        for memory in recent_memories:
            # Check for pending items, reminders, patterns
            pass  # Implement specific proactive logic

    async def _generate_plan(self, task_description: str) -> str:
        """Generate execution plan for a task"""
        plan_prompt = f"""Generate a step-by-step plan for: {task_description}

Return a JSON plan with:
- steps: array of steps to execute
- estimated_time: approximate completion time
- resources_needed: any tools or data required"""

        response = await self.provider.chat(
            messages=self.context + [Message(role="user", content=plan_prompt)],
            model=self.config.model,
            stream=False
        )

        return response.choices[0]["message"]["content"]

    async def _execute_plan(self, plan: str, task: Task) -> Any:
        """Execute a generated plan"""
        steps = self._parse_plan(plan)

        for i, step in enumerate(steps):
            task.steps.append({"step": i, "action": step, "status": "running"})

            try:
                # Check if step requires a tool
                if self._needs_tool(step):
                    result = await self._execute_tool(step)
                else:
                    result = await self._execute_step(step)

                task.steps[-1]["status"] = "completed"
                task.steps[-1]["result"] = result

            except Exception as e:
                task.steps[-1]["status"] = "failed"
                task.steps[-1]["error"] = str(e)
                raise

        return task.steps

    def _parse_plan(self, plan: str) -> List[str]:
        """Parse plan JSON into steps"""
        try:
            import json
            data = json.loads(plan)
            return data.get("steps", [])
        except json.JSONDecodeError:
            return [plan]

    def _needs_tool(self, step: str) -> bool:
        """Check if a step requires a tool"""
        for tool_name in self.tools:
            if tool_name.lower() in step.lower():
                return True
        return False

    async def _execute_tool(self, step: str) -> Any:
        """Execute a tool-based step"""
        for tool_name, tool_def in self.tools.items():
            if tool_name.lower() in step.lower():
                func = tool_def.get("func")
                if func:
                    return await func(step)
        return None

    async def _execute_step(self, step: str) -> Any:
        """Execute a plain LLM step"""
        response = await self.provider.chat(
            messages=self.context + [Message(role="user", content=f"Execute: {step}")],
            model=self.config.model,
            stream=False
        )
        return response.choices[0]["message"]["content"]

    def _simple_embed(self, text: str) -> List[float]:
        """Simple embedding for demo purposes"""
        # In production, use actual embeddings
        import hashlib
        hash_val = hashlib.md5(text.encode()).digest()
        return [float(b) / 255.0 for b in hash_val[:1536]]

    def get_status(self) -> Dict:
        """Get agent status"""
        return {
            "name": self.config.name,
            "model": self.config.model,
            "provider": self.config.provider,
            "tasks_active": sum(1 for t in self.tasks.values() if t.status == "running"),
            "tasks_completed": sum(1 for t in self.tasks.values() if t.status == "completed"),
            "context_size": len(self.context),
            "usage": self.provider.get_usage()
        }

    def reset(self):
        """Reset agent state"""
        self.context = []
        self.tasks = {}
        self.provider.reset_usage()