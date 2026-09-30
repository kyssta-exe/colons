"""
Built-in tool bundle for Colons.
Grouped by category; dangerous tools are permission-gated.
"""
import ast
import asyncio
import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from .base import PermissionLevel, tool
from .manager import ToolRegistry

# --------------------------------------------------------------------------- #
# Filesystem
# --------------------------------------------------------------------------- #

class FileSystemTools:
    def __init__(self, root: Optional[str] = None, max_read_bytes: int = 1_000_000):
        self.root = Path(root).resolve() if root else Path.cwd().resolve()
        self.max_read_bytes = max_read_bytes

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self.root / p
        p = p.resolve()
        # Prevent escaping the sandbox root
        if self.root not in p.parents and p != self.root:
            raise PermissionError(f"Path escapes workspace root: {path}")
        return p

    async def read_file(self, path: str, max_bytes: Optional[int] = None) -> str:
        p = self._resolve(path)
        limit = max_bytes or self.max_read_bytes
        data = p.read_bytes()[:limit]
        return data.decode("utf-8", errors="replace")

    async def write_file(self, path: str, content: str, create_dirs: bool = True) -> str:
        p = self._resolve(path)
        if create_dirs:
            p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {p}"

    async def append_file(self, path: str, content: str) -> str:
        p = self._resolve(path)
        with p.open("a", encoding="utf-8") as f:
            f.write(content)
        return f"Appended {len(content)} bytes to {p}"

    async def list_dir(self, path: str = ".", recursive: bool = False) -> List[Dict]:
        p = self._resolve(path)
        items: List[Dict] = []
        if recursive:
            for child in sorted(p.rglob("*")):
                rel = child.relative_to(self.root)
                items.append({
                    "path": str(rel),
                    "type": "dir" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else 0,
                })
                if len(items) >= 500:
                    break
        else:
            for child in sorted(p.iterdir()):
                items.append({
                    "path": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else 0,
                })
        return items

    async def delete_file(self, path: str) -> str:
        p = self._resolve(path)
        if p.is_dir():
            raise IsADirectoryError(f"{p} is a directory; refusing to delete")
        p.unlink()
        return f"Deleted {p}"

    async def move_file(self, source: str, destination: str) -> str:
        src = self._resolve(source)
        dst = self._resolve(destination)
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        return f"Moved {src} -> {dst}"

    async def find_and_replace(self, path: str, find: str, replace: str,
                               count: int = 0) -> str:
        """Replace occurrences of a literal string inside a file (count=0 => all)."""
        p = self._resolve(path)
        text = p.read_text(encoding="utf-8")
        occurrences = text.count(find)
        if occurrences == 0:
            return "No occurrences found"
        new_text = text.replace(find, replace) if count == 0 else text.replace(find, replace, count)
        p.write_text(new_text, encoding="utf-8")
        replaced = occurrences if count == 0 else min(count, occurrences)
        return f"Replaced {replaced} occurrence(s) in {p}"

    async def search_files(self, pattern: str, path: str = ".", max_results: int = 100) -> List[str]:
        base = self._resolve(path)
        regex = re.compile(pattern)
        results: List[str] = []
        for child in base.rglob("*"):
            if child.is_file():
                try:
                    text = child.read_text(errors="ignore")
                except Exception:
                    continue
                if regex.search(text):
                    results.append(str(child.relative_to(self.root)))
                    if len(results) >= max_results:
                        break
        return results

    def register(self, registry: ToolRegistry):
        tools = [
            tool(
                name="read_file",
                description="Read a text file from the workspace.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path (relative to workspace)"},
                        "max_bytes": {"type": "integer", "description": "Maximum bytes to read"},
                    },
                    "required": ["path"],
                },
                category="filesystem",
                returns="string",
            )(self.read_file),
            tool(
                name="write_file",
                description="Write content to a file, creating parent directories if needed.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "create_dirs": {"type": "boolean", "default": True},
                    },
                    "required": ["path", "content"],
                },
                permission=PermissionLevel.PRE_APPROVED,
                category="filesystem",
                returns="string",
            )(self.write_file),
            tool(
                name="append_file",
                description="Append content to a file.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
                permission=PermissionLevel.PRE_APPROVED,
                category="filesystem",
                returns="string",
            )(self.append_file),
            tool(
                name="list_dir",
                description="List files and folders in a directory.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "default": "."},
                        "recursive": {"type": "boolean", "default": False},
                    },
                },
                category="filesystem",
                returns="array",
            )(self.list_dir),
            tool(
                name="search_files",
                description="Search for a regex pattern inside workspace files.",
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string", "default": "."},
                        "max_results": {"type": "integer", "default": 100},
                    },
                    "required": ["pattern"],
                },
                category="filesystem",
                returns="array",
            )(self.search_files),
            tool(
                name="delete_file",
                description="Delete a file from the workspace. Dangerous, irreversible.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                permission=PermissionLevel.ASK,
                danger=True,
                category="filesystem",
                returns="string",
            )(self.delete_file),
            tool(
                name="move_file",
                description="Move or rename a file.",
                parameters={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "destination": {"type": "string"},
                    },
                    "required": ["source", "destination"],
                },
                permission=PermissionLevel.PRE_APPROVED,
                category="filesystem",
                returns="string",
            )(self.move_file),
            tool(
                name="find_and_replace",
                description="Replace a literal string inside a file.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "find": {"type": "string"},
                        "replace": {"type": "string"},
                        "count": {"type": "integer", "default": 0,
                                  "description": "Max replacements (0 = all)"},
                    },
                    "required": ["path", "find", "replace"],
                },
                permission=PermissionLevel.PRE_APPROVED,
                category="filesystem",
                returns="string",
            )(self.find_and_replace),
        ]
        for t in tools:
            registry.register_func(t)


# --------------------------------------------------------------------------- #
# Shell
# --------------------------------------------------------------------------- #

class ShellTools:
    def __init__(self, cwd: Optional[str] = None, allowlist: Optional[List[str]] = None,
                 timeout: float = 60.0):
        self.cwd = cwd or os.getcwd()
        self.allowlist = allowlist  # None => any command allowed (gated by permission)
        self.timeout = timeout

    async def run_command(self, command: str, cwd: Optional[str] = None,
                          timeout: Optional[float] = None) -> Dict:
        if self.allowlist:
            first = shlex.split(command)[0]
            if first not in self.allowlist:
                raise PermissionError(f"Command '{first}' not in allowlist")

        def _run():
            proc = subprocess.run(
                command,
                shell=True,
                cwd=cwd or self.cwd,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
            )
            return proc

        proc = await asyncio.get_event_loop().run_in_executor(None, _run)
        return {
            "stdout": proc.stdout[-20000:],
            "stderr": proc.stderr[-5000:],
            "returncode": proc.returncode,
        }

    def register(self, registry: ToolRegistry):
        registry.register_func(
            tool(
                name="run_command",
                description="Run a shell command in the workspace and return stdout/stderr.",
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "cwd": {"type": "string"},
                        "timeout": {"type": "number", "default": 60},
                    },
                    "required": ["command"],
                },
                permission=PermissionLevel.ASK,
                danger=True,
                category="shell",
                returns="object",
            )(self.run_command)
        )


# --------------------------------------------------------------------------- #
# Web
# --------------------------------------------------------------------------- #

class WebTools:
    def __init__(self, user_agent: str = "Colons/0.1", search_provider: Optional[callable] = None,
                 timeout: float = 30.0):
        self.user_agent = user_agent
        self.search_provider = search_provider
        self.timeout = timeout

    async def fetch_url(self, url: str, max_chars: int = 50000) -> str:
        async with httpx.AsyncClient(
            timeout=self.timeout,
            headers={"User-Agent": self.user_agent},
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            text = resp.text
            # Strip crude HTML if present
            if "<html" in text.lower():
                text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
                text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
            return text[:max_chars]

    async def http_request(self, method: str, url: str, headers: Optional[Dict] = None,
                           body: Optional[Any] = None, params: Optional[Dict] = None) -> Dict:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            resp = await client.request(
                method.upper(), url, headers=headers, json=body, params=params
            )
            try:
                data = resp.json()
            except Exception:
                data = resp.text[:20000]
            return {"status_code": resp.status_code, "body": data}

    async def web_search(self, query: str, max_results: int = 5) -> List[Dict]:
        if self.search_provider:
            return await self.search_provider(query, max_results)
        # Fallback: DuckDuckGo Instant Answer API (no key required)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1},
            )
            data = resp.json()
            results = []
            for topic in data.get("RelatedTopics", [])[:max_results]:
                if "Text" in topic and "FirstURL" in topic:
                    results.append({"title": topic["Text"][:100], "url": topic["FirstURL"],
                                    "snippet": topic["Text"]})
            if data.get("AbstractText"):
                results.insert(0, {"title": data.get("Heading", query),
                                   "url": data.get("AbstractURL", ""),
                                   "snippet": data["AbstractText"]})
            return results

    def register(self, registry: ToolRegistry):
        tools = [
            tool(
                name="web_search",
                description="Search the web for information.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
                category="web",
                returns="array",
            )(self.web_search),
            tool(
                name="fetch_url",
                description="Fetch a URL and return its text content (HTML stripped).",
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "max_chars": {"type": "integer", "default": 50000},
                    },
                    "required": ["url"],
                },
                category="web",
                returns="string",
            )(self.fetch_url),
            tool(
                name="http_request",
                description="Perform an arbitrary HTTP request (GET/POST/PUT/DELETE).",
                parameters={
                    "type": "object",
                    "properties": {
                        "method": {"type": "string", "default": "GET"},
                        "url": {"type": "string"},
                        "headers": {"type": "object"},
                        "body": {},
                        "params": {"type": "object"},
                    },
                    "required": ["method", "url"],
                },
                permission=PermissionLevel.PRE_APPROVED,
                category="web",
                returns="object",
            )(self.http_request),
        ]
        for t in tools:
            registry.register_func(t)


# --------------------------------------------------------------------------- #
# Code / math / text
# --------------------------------------------------------------------------- #

class CodeTools:
    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    async def calculate(self, expression: str) -> float:
        """Safely evaluate a math expression (no names, no calls)."""
        allowed_bin = {
            ast.Add: lambda a, b: a + b,
            ast.Sub: lambda a, b: a - b,
            ast.Mult: lambda a, b: a * b,
            ast.Div: lambda a, b: a / b,
            ast.FloorDiv: lambda a, b: a // b,
            ast.Mod: lambda a, b: a % b,
            ast.Pow: lambda a, b: a ** b,
        }
        allowed_unary = {
            ast.UAdd: lambda a: +a,
            ast.USub: lambda a: -a,
        }

        def _eval(node):
            if isinstance(node, ast.Expression):
                return _eval(node.body)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return node.value
            if isinstance(node, ast.BinOp) and type(node.op) in allowed_bin:
                return allowed_bin[type(node.op)](_eval(node.left), _eval(node.right))
            if isinstance(node, ast.UnaryOp) and type(node.op) in allowed_unary:
                return allowed_unary[type(node.op)](_eval(node.operand))
            raise ValueError(f"Unsupported expression element: {ast.dump(node)[:60]}")

        tree = ast.parse(expression, mode="eval")
        return _eval(tree)

    async def run_python(self, code: str, timeout: Optional[float] = None) -> Dict:
        """Execute Python in a subprocess with a hard timeout (basic isolation)."""
        limit = timeout or self.timeout

        def _run():
            return subprocess.run(
                ["python3", "-I", "-c", code],
                capture_output=True, text=True, timeout=limit,
            )

        try:
            proc = await asyncio.get_event_loop().run_in_executor(None, _run)
            return {"stdout": proc.stdout[-20000:], "stderr": proc.stderr[-5000:],
                    "returncode": proc.returncode}
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": f"Execution timed out after {limit}s", "returncode": -1}

    async def extract_json(self, text: str) -> Any:
        """Find and parse the first JSON value in a blob of text."""
        candidates = re.findall(r"\{[\s\S]*\}|\[[\s\S]*\]", text)
        for cand in sorted(candidates, key=len, reverse=True):
            try:
                return json.loads(cand)
            except Exception:
                continue
        raise ValueError("No valid JSON found")

    async def regex_search(self, pattern: str, text: str, group: int = 0) -> List[str]:
        return [m.group(group) for m in re.finditer(pattern, text)]

    def register(self, registry: ToolRegistry):
        tools = [
            tool(
                name="calculate",
                description="Safely evaluate a mathematical expression.",
                parameters={
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                },
                category="code",
                returns="number",
            )(self.calculate),
            tool(
                name="run_python",
                description="Execute Python code in a subprocess and return stdout/stderr.",
                parameters={
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "timeout": {"type": "number", "default": 15},
                    },
                    "required": ["code"],
                },
                permission=PermissionLevel.ASK,
                danger=True,
                category="code",
                returns="object",
            )(self.run_python),
            tool(
                name="extract_json",
                description="Extract and parse the first JSON object or array from text.",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                category="code",
                returns="any",
            )(self.extract_json),
            tool(
                name="regex_search",
                description="Find all regex matches in text.",
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "text": {"type": "string"},
                        "group": {"type": "integer", "default": 0},
                    },
                    "required": ["pattern", "text"],
                },
                category="code",
                returns="array",
            )(self.regex_search),
        ]
        for t in tools:
            registry.register_func(t)


# --------------------------------------------------------------------------- #
# Time & misc
# --------------------------------------------------------------------------- #

class UtilityTools:
    async def now(self, timezone: Optional[str] = None) -> Dict:
        from datetime import datetime
        from datetime import timezone as tz
        if timezone:
            try:
                from zoneinfo import ZoneInfo
                dt = datetime.now(ZoneInfo(timezone))
            except Exception:
                dt = datetime.now(tz.utc)
        else:
            dt = datetime.now()
        return {
            "iso": dt.isoformat(),
            "date": dt.strftime("%Y-%m-%d"),
            "time": dt.strftime("%H:%M:%S"),
            "day_of_week": dt.strftime("%A"),
            "unix": dt.timestamp(),
        }

    async def sleep(self, seconds: float) -> str:
        await asyncio.sleep(min(seconds, 300))
        return f"Slept for {seconds}s"

    async def system_info(self) -> Dict:
        import platform
        import sys
        return {
            "os": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
            "cwd": os.getcwd(),
            "executable": sys.executable,
        }

    def register(self, registry: ToolRegistry):
        tools = [
            tool(
                name="now",
                description="Get the current date and time.",
                parameters={
                    "type": "object",
                    "properties": {"timezone": {"type": "string"}},
                },
                category="utility",
                returns="object",
            )(self.now),
            tool(
                name="sleep",
                description="Pause execution for a number of seconds (max 300).",
                parameters={
                    "type": "object",
                    "properties": {"seconds": {"type": "number"}},
                    "required": ["seconds"],
                },
                category="utility",
                returns="string",
            )(self.sleep),
            tool(
                name="system_info",
                description="Get basic information about the operating environment.",
                parameters={"type": "object", "properties": {}},
                category="utility",
                returns="object",
            )(self.system_info),
        ]
        for t in tools:
            registry.register_func(t)


# --------------------------------------------------------------------------- #
# Memory tools (bound at runtime with a store)
# --------------------------------------------------------------------------- #

class MemoryTools:
    def __init__(self, store, embeddings, user_id: str = "default", agent_id: str = "default"):
        self.store = store
        self.embeddings = embeddings
        self.user_id = user_id
        self.agent_id = agent_id

    async def remember(self, content: str, kind: str = "fact") -> str:
        from ..memory.store import MemoryEntry
        vector = await self.embeddings.embed(content)
        entry = MemoryEntry(
            user_id=self.user_id, agent_id=self.agent_id, kind=kind,
            content=content, embedding=vector, metadata={"source": "tool"},
        )
        self.store.store(entry)
        return f"Remembered ({kind}): {content[:100]}"

    async def recall(self, query: str, limit: int = 5) -> List[Dict]:
        vector = await self.embeddings.embed(query)
        hits = self.store.search(self.user_id, vector, limit=limit, threshold=0.1,
                                 agent_id=self.agent_id)
        return [
            {"content": e.content, "kind": e.kind, "similarity": round(s, 3),
             "created_at": e.created_at}
            for e, s in hits
        ]

    def register(self, registry: ToolRegistry):
        tools = [
            tool(
                name="remember",
                description="Store a fact or note in long-term memory.",
                parameters={
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "kind": {"type": "string", "default": "fact"},
                    },
                    "required": ["content"],
                },
                category="memory",
                returns="string",
            )(self.remember),
            tool(
                name="recall",
                description="Search long-term memory for relevant information.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
                category="memory",
                returns="array",
            )(self.recall),
        ]
        for t in tools:
            registry.register_func(t)


def register_all_builtin_tools(
    registry: ToolRegistry,
    workspace: Optional[str] = None,
    shell_allowlist: Optional[List[str]] = None,
    memory_store=None,
    embeddings=None,
    user_id: str = "default",
    agent_id: str = "default",
) -> ToolRegistry:
    """Register every built-in tool bundle."""
    FileSystemTools(root=workspace).register(registry)
    ShellTools(cwd=workspace, allowlist=shell_allowlist).register(registry)
    WebTools().register(registry)
    CodeTools().register(registry)
    UtilityTools().register(registry)
    if memory_store is not None and embeddings is not None:
        MemoryTools(memory_store, embeddings, user_id=user_id, agent_id=agent_id).register(registry)
    return registry
