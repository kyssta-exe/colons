"""
Core Agentic Harness for Colons
Complete implementation with tool management, memory, autonomous execution
"""
import asyncio
import json
import uuid
import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Callable, AsyncGenerator
from dataclasses import dataclass, field
from enum import Enum
import logging

from ...providers.base import ProviderAdapter, Message, Usage
from ...memory import MemoryStore
from ...memory.cache import CacheStore

logger = logging.getLogger(__name__)

class AgentState(Enum):
    IDLE = "idle"
    THINKING = "thinking"
    EXECUTING = "executing"
    WAITING_FOR_INPUT = "waiting_for_input"
    SLEEPING = "sleeping"

@dataclass
class ToolResult:
    """Result from tool execution"""
    success: bool
    output: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class AgentAction:
    """Action the agent can take"""
    id: str
    name: str
    description: str
    parameters: Dict[str, Any]
    tool: Optional[Callable] = None
    requires_approval: bool = False
    auto_execute: bool = True

@dataclass
class Task:
    """Task managed by the agent"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = ""
    description: str = ""
    status: str = "pending"  # pending, planning, executing, reviewing, completed, failed
    priority: int = 1  # 1-5
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Any = None
    error: Optional[str] = None
    steps: List[Dict] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "description": self.description,
            "status": self.status,
            "priority": self.priority,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "error": self.error,
            "steps": self.steps,
            "metadata": self.metadata
        }

class ToolManager:
    """Manages all available tools for the agent"""

    def __init__(self):
        self.tools: Dict[str, Callable] = {}
        self.tool_schemas: Dict[str, Dict] = {}
        self._register_builtin_tools()

    def register_tool(self, name: str, func: Callable, schema: Dict,
                     requires_approval: bool = False, auto_execute: bool = True):
        """Register a new tool"""
        self.tools[name] = func
        self.tool_schemas[name] = {
            "name": name,
            "description": schema.get("description", ""),
            "parameters": schema.get("parameters", {}),
            "requires_approval": requires_approval,
            "auto_execute": auto_execute,
            "returns": schema.get("returns", "any")
        }
        logger.info(f"Registered tool: {name}")

    def unregister_tool(self, name: str):
        """Remove a tool"""
        self.tools.pop(name, None)
        self.tool_schemas.pop(name, None)
        logger.info(f"Unregistered tool: {name}")

    def get_tool(self, name: str) -> Optional[Callable]:
        """Get a tool by name"""
        return self.tools.get(name)

    def list_tools(self) -> List[Dict]:
        """List all available tools"""
        return list(self.tool_schemas.values())

    async def execute_tool(self, name: str, arguments: Dict[str, Any]) -> ToolResult:
        """Execute a tool with given arguments"""
        if name not in self.tools:
            return ToolResult(
                success=False,
                error=f"Tool '{name}' not found"
            )

        try:
            func = self.tools[name]
            if asyncio.iscoroutinefunction(func):
                result = await func(**arguments)
            else:
                result = func(**arguments)

            return ToolResult(
                success=True,
                output=result
            )
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            return ToolResult(
                success=False,
                error=str(e),
                metadata={"tool": name, "arguments": arguments}
            )

    def _register_builtin_tools(self):
        """Register built-in tools"""
        # File system tools
        self.register_tool(
            "read_file",
            self._read_file,
            {
                "description": "Read contents of a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to file"}
                    },
                    "required": ["path"]
                },
                "returns": "string"
            }
        )

        self.register_tool(
            "write_file",
            self._write_file,
            {
                "description": "Write content to a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to file"},
                        "content": {"type": "string", "description": "Content to write"}
                    },
                    "required": ["path", "content"]
                },
                "returns": "boolean"
            }
        )

        self.register_tool(
            "list_directory",
            self._list_directory,
            {
                "description": "List contents of a directory",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path"}
                    },
                    "required": ["path"]
                },
                "returns": "array"
            }
        )

        # Web search tool
        self.register_tool(
            "web_search",
            self._web_search,
            {
                "description": "Search the web for information",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "max_results": {"type": "integer", "description": "Maximum results to return", "default": 10}
                    },
                    "required": ["query"]
                },
                "returns": "array"
            },
            requires_approval=False
        )

        # Code execution tool
        self.register_tool(
            "execute_code",
            self._execute_code,
            {
                "description": "Execute Python code in sandbox",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Python code to execute"},
                        "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 30}
                    },
                    "required": ["code"]
                },
                "returns": "object"
            },
            requires_approval=True  # Requires approval for security
        )

        # Calculator
        self.register_tool(
            "calculate",
            self._calculate,
            {
                "description": "Perform mathematical calculations",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {"type": "string", "description": "Mathematical expression"}
                    },
                    "required": ["expression"]
                },
                "returns": "number"
            }
        )

    async def _read_file(self, path: str) -> str:
        """Read file contents"""
        import os
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()

    async def _write_file(self, path: str, content: str) -> bool:
        """Write content to file"""
        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True

    async def _list_directory(self, path: str) -> List[str]:
        """List directory contents"""
        import os
        if not os.path.exists(path):
            raise FileNotFoundError(f"Directory not found: {path}")
        return os.listdir(path)

    async def _web_search(self, query: str, max_results: int = 10) -> List[Dict]:
        """Search the web (placeholder - integrate with search API)"""
        # In production, integrate with Google Custom Search, Bing API, etc.
        return [
            {
                "title": f"Result for {query}",
                "url": f"https://example.com/result/{i}",
                "snippet": f"This is a sample result for query: {query}"
            }
            for i in range(min(max_results, 3))
        ]

    async def _execute_code(self, code: str, timeout: int = 30) -> Dict:
        """Execute code in sandbox (placeholder)"""
        # In production, use Docker container, Firejail, or similar sandbox
        return {
            "stdout": "",
            "stderr": "",
            "return_code": 0,
            "execution_time": 0
        }

    async def _calculate(self, expression: str) -> float:
        """Safe calculation"""
        import ast
        import operator

        # Only allow safe operations
        allowed_names = {
            'abs': abs, 'round': round, 'min': min, 'max': max,
            'sum': sum, 'len': len, 'pow': pow
        }

        allowed_operators = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.Pow: operator.pow,
            ast.USub: operator.neg,
            ast.UAdd: operator.pos
        }

        def _eval(node):
            if isinstance(node, ast.Num):
                return node.n
            elif isinstance(node, ast.Name):
                if node.id in allowed_names:
                    return allowed_names[node.id]
                raise ValueError(f"Unauthorized name: {node.id}")
            elif isinstance(node, ast.BinOp):
                left = _eval(node.left)
                right = _eval(node.right)
                op_type = type(node.op)
                if op_type in allowed_operators:
                    return allowed_operators[op_type](left, right)
                raise ValueError(f"Unauthorized operator: {op_type}")
            elif isinstance(node, ast.UnaryOp):
                operand = _eval(node.operand)
                op_type = type(node.op)
                if op_type in allowed_operators:
                    return allowed_operators[op_type](operand)
                raise ValueError(f"Unauthorized unary operator: {op_type}")
            elif isinstance(node, ast.Call):
                func = _eval(node.func)
                args = [_eval(arg) for arg in node.args]
                return func(*args)
            else:
                raise ValueError(f"Unauthorized AST node: {type(node)}")

        tree = ast.parse(expression, mode='eval')
        return _eval(tree.body)

class MemoryManager:
    """Manages agent memory - both short-term and long-term"""

    def __init__(self, db_store: MemoryStore, cache: CacheStore):
        self.db = db_store
        self.cache = cache
        self.short_term: List[Message] = []  # Conversation buffer
        self.max_short_term = 20

    async def add_to_short_term(self, message: Message):
        """Add message to short-term memory"""
        self.short_term.append(message)
        if len(self.short_term) > self.max_short_term:
            self.short_term.pop(0)

        # Also store in long-term memory
        await self.store_long_term(message)

    async def get_short_term(self) -> List[Message]:
        """Get short-term memory"""
        return self.short_term.copy()

    async def store_long_term(self, message: Message):
        """Store message in long-term memory"""
        # Generate embedding (simplified)
        embedding = self._simple_embedding(message.content)

        await self.db.store(
            user_id=message.metadata.get("user_id", "default"),
            content=message.content,
            embedding=embedding,
            metadata={
                "role": message.role,
                "timestamp": time.time(),
                "type": "conversation"
            }
        )

    async def search_long_term(self, query: str, limit: int = 5) -> List[Dict]:
        """Search long-term memory"""
        query_embedding = self._simple_embedding(query)
        results = await self.db.search(
            user_id="default",  # In production, get from context
            query_embedding=query_embedding,
            limit=limit
        )
        return [
            {
                "content": entry.content,
                "similarity": score,
                "metadata": entry.metadata
            }
            for entry, score in results
        ]

    async def get_recent_context(self, limit: int = 10) -> List[Message]:
        """Get recent conversation context"""
        return self.short_term[-limit:] if self.short_term else []

    def _simple_embedding(self, text: str) -> List[float]:
        """Simple embedding function (replace with actual embeddings)"""
        import hashlib
        # Create a deterministic hash-based vector
        hash_obj = hashlib.sha256(text.encode())
        hash_bytes = hash_obj.digest()
        # Normalize to [-1, 1] range
        return [(b / 255.0) * 2 - 1 for b in hash_bytes[:384]]  # 384-dim vector

class ProactiveResearcher:
    """Handles proactive background research"""

    def __init__(self, agent):
        self.agent = agent
        self.is_running = False
        self.research_tasks: List[asyncio.Task] = []

    async def start(self):
        """Start proactive research"""
        if self.is_running:
            return

        self.is_running = True
        self.research_tasks.append(
            asyncio.create_task(self._research_loop())
        )
        logger.info("Started proactive research")

    async def stop(self):
        """Stop proactive research"""
        self.is_running = False
        for task in self.research_tasks:
            task.cancel()
        await asyncio.gather(*self.research_tasks, return_exceptions=True)
        self.research_tasks.clear()
        logger.info("Stopped proactive research")

    async def _research_loop(self):
        """Background research loop"""
        while self.is_running:
            try:
                # Wait between research cycles
                await asyncio.sleep(30)  # Research every 30 seconds

                if not self.agent.state == AgentState.IDLE:
                    continue

                # Look for research opportunities
                await self._check_for_research_opportunities()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Proactive research error: {e}")
                await asyncio.sleep(5)

    async def _check_for_research_opportunities(self):
        """Check if there are things to research"""
        # Check recent messages for patterns
        recent = await self.agent.memory.get_recent_context(5)

        # Look for unresolved questions, TODO items, etc.
        for message in recent:
            if message.role == "user":
                content = message.content.lower()
                # Simple triggers for research
                if any(trigger in content for trigger in [
                    "research", "find out", "look into", "investigate",
                    "what is", "how does", "why does", "can you"
                ]):
                    # Queue for research
                    await self._queue_research_task(message.content)

class AvatarManager:
    """Manages agent avatars and visual representation"""

    def __init__(self):
        self.avatars = {
            "default": {
                "name": "Colons",
                "description": "Your AI assistant",
                "image": "/avatars/default.png",
                "color": "#3b82f6",
                "personality": "helpful and knowledgeable"
            },
            "assistant": {
                "name": "Assistant",
                "description": "Professional assistant",
                "image": "/avatars/assistant.png",
                "color": "#10b981",
                "personality": "efficient and precise"
            },
            "creator": {
                "name": "Creator",
                "description": "Creative partner",
                "image": "/avatars/creator.png",
                "color": "#8b5cf6",
                "personality": "imaginative and inspiring"
            },
            "analyst": {
                "name": "Analyst",
                "description": "Data-focused analyst",
                "image": "/avatars/analyst.png",
                "color": "#f59e0b",
                "personality": "logical and detail-oriented"
            }
        }
        self.current_avatar = "default"

    def get_avatar(self, name: str = None) -> Dict:
        """Get avatar details"""
        name = name or self.current_avatar
        return self.avatars.get(name, self.avatars["default"])

    def set_avatar(self, name: str):
        """Set current avatar"""
        if name in self.avatars:
            self.current_avatar = name
            logger.info(f"Set avatar to: {name}")
        else:
            logger.warning(f"Avatar not found: {name}")

    def list_avatars(self) -> List[Dict]:
        """List all available avatars"""
        return [
            {"id": k, **v} for k, v in self.avatars.items()
        ]

class AudioManager:
    """Handles text-to-speech and audio output"""

    def __init__(self):
        self.tts_engine = None
        self.is_speaking = False
        self._init_tts()

    def _init_tts(self):
        """Initialize TTS engine"""
        try:
            # Try to use Windows Edge TTS or similar
            import pyttsx3
            self.tts_engine = pyttsx3.init()
            self.tts_engine.setProperty('rate', 150)
            self.tts_engine.setProperty('volume', 0.9)
        except ImportError:
            logger.warning("TTS engine not available")
            self.tts_engine = None

    async def speak(self, text: str, voice: str = None, rate: int = 150):
        """Convert text to speech"""
        if not self.tts_engine or self.is_speaking:
            return

        self.is_speaking = True
        try:
            if voice:
                self.tts_engine.setProperty('voice', voice)
            self.tts_engine.setProperty('rate', rate)

            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.tts_engine.say(text) or self.tts_engine.runAndWait()
            )
        except Exception as e:
            logger.error(f"TTS error: {e}")
        finally:
            self.is_speaking = False

    def stop(self):
        """Stop current speech"""
        if self.tts_engine and self.is_speaking:
            try:
                self.tts_engine.stop()
            except:
                pass
        self.is_speaking = False

class ColonsAgent:
    """Main agentic harness - brings everything together"""

    def __init__(self,
                 provider: ProviderAdapter,
                 memory_store: MemoryStore,
                 cache: CacheStore,
                 user_id: str = "default"):

        self.user_id = user_id
        self.provider = provider
        self.memory_store = memory_store
        self.cache = cache

        # Initialize managers
        self.state = AgentState.IDLE
        self.tool_manager = ToolManager()
        self.memory_manager = MemoryManager(memory_store, cache)
        self.proactive_researcher = ProactiveResearcher(self)
        self.avatar_manager = AvatarManager()
        self.audio_manager = AudioManager()

        # State tracking
        self.tasks: Dict[str, Task] = {}
        self.conversation_id = str(uuid.uuid4())
        self.is_running = False
        self.metrics = {
            "messages_processed": 0,
            "tasks_completed": 0,
            "tools_used": 0,
            "start_time": time.time()
        }

        # Configuration
        self.config = {
            "model": "gpt-4",
            "temperature": 0.7,
            "max_tokens": 4096,
            "enable_proactive": True,
            "enable_tts": False,
            "auto_approve_tools": False,
            "max_concurrent_tasks": 5
        }

    async def start(self):
        """Start the agent"""
        if self.is_running:
            return

        self.is_running = True
        self.state = AgentState.IDLE

        # Start proactive research if enabled
        if self.config["enable_proactive"]:
            await self.proactive_researcher.start()

        logger.info("Colons Agent started")

    async def stop(self):
        """Stop the agent"""
        if not self.is_running:
            return

        self.is_running = False
        self.state = AgentState.IDLE

        # Stop proactive research
        await self.proactive_researcher.stop()

        # Stop any ongoing TTS
        self.audio_manager.stop()

        logger.info("Colons Agent stopped")

    async def process_message(self,
                            message: str,
                            stream: bool = True,
                            user_id: str = None) -> AsyncGenerator[Dict, None]:
        """Process a user message and yield responses"""
        user_id = user_id or self.user_id

        # Update state
        self.state = AgentState.THINKING
        self.metrics["messages_processed"] += 1

        # Create user message
        user_msg = Message(
            role="user",
            content=message,
            metadata={"user_id": user_id, "timestamp": time.time()}
        )

        # Add to memory
        await self.memory_manager.add_to_short_term(user_msg)

        # Yield thinking status
        yield {
            "type": "status",
            "state": self.state.value,
            "message": "Thinking..."
        }

        try:
            # Get conversation context
            context = await self.memory_manager.get_short_term()

            # Check if we need to use tools
            tool_calls = await self._determine_tool_usage(message, context)

            if tool_calls:
                self.state = AgentState.EXECUTING
                yield {
                    "type": "status",
                    "state": self.state.value,
                    "message": "Using tools..."
                }

                # Execute tools
                tool_results = []
                for tool_call in tool_calls:
                    result = await self.tool_manager.execute_tool(
                        tool_call["name"],
                        tool_call["arguments"]
                    )
                    tool_results.append({
                        "tool": tool_call["name"],
                        "result": result
                    })

                    if result.success:
                        self.metrics["tools_used"] += 1

                # Incorporate tool results into context
                tool_context = self._format_tool_results(tool_results)
                context.append(Message(
                    role="system",
                    content=f"Tool results: {tool_context}",
                    metadata={"timestamp": time.time()}
                ))

            # Generate response
            self.state = AgentState.THINKING
            yield {
                "type": "status",
                "state": self.state.value,
                "message": "Generating response..."
            }

            # Get LLM response
            response_chunks = []
            async for chunk in self._generate_llm_response(context, stream):
                response_chunks.append(chunk)
                if stream:
                    yield {
                        "type": "chunk",
                        "content": chunk,
                        "done": False
                    }

            full_response = "".join(response_chunks)

            # Create assistant message
            assistant_msg = Message(
                role="assistant",
                content=full_response,
                metadata={
                    "user_id": user_id,
                    "timestamp": time.time(),
                    "conversation_id": self.conversation_id
                }
            )

            # Store in memory
            await self.memory_manager.add_to_short_term(assistant_msg)

            # Update metrics
            self.metrics["tasks_completed"] += 1

            # Final response
            yield {
                "type": "chunk",
                "content": full_response,
                "done": True,
                "usage": self.provider.get_usage()
            }

            self.state = AgentState.IDLE

            # Speak response if TTS enabled
            if self.config["enable_tts"] and full_response:
                await self.audio_manager.speak(full_response)

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            self.state = AgentState.IDLE
            yield {
                "type": "error",
                "message": str(e)
            }

    async def _determine_tool_usage(self,
                                  message: str,
                                  context: List[Message]) -> List[Dict]:
        """Determine if and which tools to use"""
        # Use LLM to decide tool usage
        tool_prompt = f"""Based on the user message and conversation context,
        determine if any tools should be used to help answer the query.

        Available tools: {[t["name"] for t in self.tool_manager.list_tools()]}

        User message: {message}

        Return a JSON array of tool calls, or empty array if no tools needed.
        Each tool call should have: {{"name": "tool_name", "arguments": {{...}}}}
        """

        try:
            # Get LLM decision
            messages = context[-10:] + [
                Message(role="system", content=tool_prompt)
            ]

            response = await self.provider.chat(
                messages=messages,
                model=self.config["model"],
                temperature=0.3,  # Lower temp for tool decisions
                stream=False
            )

            content = response.choices[0]["message"]["content"]

            # Try to parse as JSON
            import re
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if json_match:
                tool_calls = json.loads(json_match.group())
                return tool_calls if isinstance(tool_calls, list) else []
            return []
        except Exception as e:
            logger.error(f"Error determining tool usage: {e}")
            return []

    def _format_tool_results(self, results: List[Dict]) -> str:
        """Format tool results for context"""
        formatted = []
        for result in results:
            if result["result"].success:
                formatted.append(
                    f"{result['tool']}: {result['result'].output}"
                )
            else:
                formatted.append(
                    f"{result['tool']} failed: {result['result'].error}"
                )
        return "\n".join(formatted)

    async def _generate_llm_response(self,
                                   context: List[Message],
                                   stream: bool) -> AsyncGenerator[str, None]:
        """Generate response from LLM"""
        # Convert messages to provider format
        provider_messages = []
        for msg in context:
            provider_messages.append({
                "role": msg.role,
                "content": msg.content,
                "name": msg.metadata.get("name") if msg.metadata else None
            })

        # Get response from provider
        response = await self.provider.chat(
            messages=provider_messages,
            model=self.config["model"],
            temperature=self.config["temperature"],
            max_tokens=self.config["max_tokens"],
            stream=stream
        )

        if stream:
            async for chunk in response:
                delta = chunk.choices[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    yield content
        else:
            yield response.choices[0]["message"]["content"]

    async def create_task(self,
                         description: str,
                         priority: int = 1,
                         user_id: str = None) -> str:
        """Create a new task"""
        user_id = user_id or self.user_id

        task = Task(
            user_id=user_id,
            description=description,
            priority=priority
        )

        self.tasks[task.id] = task
        logger.info(f"Created task: {task.id} - {description}")

        return task.id

    async def execute_task(self, task_id: str) -> Any:
        """Execute a task"""
        task = self.tasks.get(task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")

        if task.status != "pending":
            raise ValueError(f"Task is not pending: {task.status}")

        task.status = "executing"
        task.started_at = time.time()

        try:
            # Break down task into steps
            plan = await self._plan_task(task.description)

            # Execute each step
            for i, step in enumerate(plan):
                task.steps.append({
                    "step": i,
                    "description": step,
                    "status": "in_progress"
                })

                # Execute step
                result = await self._execute_task_step(step, task)

                task.steps[-1]["status"] = "completed"
                task.steps[-1]["result"] = result

            task.status = "completed"
            task.completed_at = time.time()
            task.result = plan

            self.metrics["tasks_completed"] += 1
            return task.result

        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            task.completed_at = time.time()
            logger.error(f"Task execution failed: {e}")
            raise

    async def _plan_task(self, description: str) -> List[str]:
        """Create a plan for executing a task"""
        prompt = f"""Create a step-by-step plan to accomplish: {description}

        Consider:
        1. What information is needed
        2. What tools might be required
        3. What the expected outcome should be
        4. Any potential obstacles

        Return as a JSON array of strings, each representing a step."""

        messages = await self.memory_manager.get_short_term()
        messages.append(Message(
            role="system",
            content=prompt,
            metadata={"timestamp": time.time()}
        ))

        provider_messages = []
        for msg in messages:
            provider_messages.append({
                "role": msg.role,
                "content": msg.content
            })

        response = await self.provider.chat(
            messages=provider_messages,
            model=self.config["model"],
            temperature=0.5,
            stream=False
        )

        content = response.choices[0]["message"]["content"]

        # Extract JSON array
        import re
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # Fallback: split by lines
        return [line.strip("- ").strip() for line in content.split("\n")
                if line.strip() and not line.strip().startswith("#")]

    async def _execute_task_step(self, step: str, task: Task) -> Any:
        """Execute a single task step"""
        # Check if step requires tools
        tool_calls = await self._determine_tool_usage(step, await self.memory_manager.get_short_term())

        if tool_calls:
            # Execute tools first
            for tool_call in tool_calls:
                result = await self.tool_manager.execute_tool(
                    tool_call["name"],
                    tool_call["arguments"]
                )
                if not result.success:
                    raise Exception(f"Tool {tool_call['name']} failed: {result.error}")

            # Then generate response based on tool results
            context = await self.memory_manager.get_short_term()
            context.append(Message(
                role="system",
                content=f"Tool results from step: {json.dumps([{
                    'tool': tc['name'],
                    'result': 'success' if tc['result'].success else 'failed'
                } for tc in tool_calls])}"
            ))

            response = await self._generate_llm_response(context, False)
            return response
        else:
            # Direct LLM execution
            context = await self.memory_manager.get_short_term()
            context.append(Message(
                role="user",
                content=f"Execute this step: {step}",
                metadata={"timestamp": time.time()}
            ))

            return await self._generate_llm_response(context, False)

    def get_status(self) -> Dict:
        """Get current agent status"""
        uptime = time.time() - self.metrics["start_time"]
        return {
            "state": self.state.value,
            "is_running": self.is_running,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "metrics": {
                **self.metrics,
                "uptime_seconds": uptime,
                "messages_per_minute": (self.metrics["messages_processed"] / uptime * 60) if uptime > 0 else 0
            },
            "active_tasks": len([t for t in self.tasks.values() if t.status == "executing"]),
            "pending_tasks": len([t for t in self.tasks.values() if t.status == "pending"]),
            "completed_tasks": len([t for t in self.tasks.values() if t.status == "completed"]),
            "available_tools": len(self.tool_manager.list_tools()),
            "avatar": self.avatar_manager.get_avatar(),
            "config": self.config
        }

    def reset(self):
        """Reset agent to initial state"""
        self.state = AgentState.IDLE
        self.tasks.clear()
        self.conversation_id = str(uuid.uuid4())
        self.memory_manager.short_term.clear()
        self.metrics = {
            "messages_processed": 0,
            "tasks_completed": 0,
            "tools_used": 0,
            "start_time": time.time()
        }
        logger.info("Agent reset")

# Factory function for easy instantiation
def create_colons_agent(provider: ProviderAdapter,
                       memory_store: MemoryStore,
                       cache: CacheStore,
                       user_id: str = "default") -> ColonsAgent:
    """Create a new Colons agent instance"""
    return ColonsAgent(provider, memory_store, cache, user_id)