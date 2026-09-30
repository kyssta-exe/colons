"""Tests for the tool system."""
import pytest

from colons_core.tools import (
    PermissionLevel, ToolManager, ToolRegistry, ToolStatus, tool, register_all_builtin_tools,
)
from colons_core.tools.manager import ApprovalRequest


def test_decorator_registers_schema():
    @tool(description="Add numbers", category="math")
    async def add(a: int, b: int) -> int:
        return a + b

    registry = ToolRegistry()
    registry.register_func(add)
    spec = registry.get("add")
    assert spec is not None
    assert spec.description == "Add numbers"
    assert spec.parameters["properties"]["a"]["type"] == "integer"
    assert set(spec.parameters["required"]) == {"a", "b"}
    assert spec.category == "math"


def test_openai_schema_shape():
    @tool(description="d")
    async def ping() -> str:
        return "pong"

    registry = ToolRegistry()
    registry.register_func(ping)
    schema = registry.openai_schemas()[0]
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "ping"


@pytest.mark.asyncio
async def test_manager_executes_tool():
    @tool(description="double")
    async def double(x: int) -> int:
        return x * 2

    registry = ToolRegistry()
    registry.register_func(double)
    manager = ToolManager(registry)
    result = await manager.execute("double", {"x": 21})
    assert result.success
    assert result.output == 42
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_manager_unknown_tool():
    manager = ToolManager(ToolRegistry())
    result = await manager.execute("nope", {})
    assert result.status == ToolStatus.ERROR
    assert "Unknown tool" in result.error


@pytest.mark.asyncio
async def test_manager_type_error_handled():
    @tool(description="needs x")
    async def needs_x(x: int) -> int:
        return x

    registry = ToolRegistry()
    registry.register_func(needs_x)
    manager = ToolManager(registry)
    result = await manager.execute("needs_x", {"wrong": 1})
    assert result.status == ToolStatus.ERROR


@pytest.mark.asyncio
async def test_manager_timeout():
    import asyncio

    @tool(description="slow", timeout=0.1)
    async def slow() -> str:
        await asyncio.sleep(2)
        return "done"

    registry = ToolRegistry()
    registry.register_func(slow)
    manager = ToolManager(registry)
    result = await manager.execute("slow", {})
    assert result.status == ToolStatus.TIMEOUT


@pytest.mark.asyncio
async def test_permission_ask_denied_without_callback():
    @tool(description="danger", permission=PermissionLevel.ASK, danger=True)
    async def rm(path: str) -> str:
        return f"deleted {path}"

    registry = ToolRegistry()
    registry.register_func(rm)
    manager = ToolManager(registry, approval_callback=None)
    result = await manager.execute("rm", {"path": "/tmp/x"})
    assert result.status == ToolStatus.DENIED


@pytest.mark.asyncio
async def test_permission_ask_approved_via_callback():
    @tool(description="danger", permission=PermissionLevel.ASK, danger=True)
    async def rm(path: str) -> str:
        return f"deleted {path}"

    async def approve(request: ApprovalRequest) -> bool:
        return request.tool_name == "rm"

    registry = ToolRegistry()
    registry.register_func(rm)
    manager = ToolManager(registry, approval_callback=approve)
    result = await manager.execute("rm", {"path": "/tmp/x"})
    assert result.success


@pytest.mark.asyncio
@pytest.mark.parametrize('auto_approve', [False, True])
async def test_permission_handoff_never_runs(auto_approve):
    @tool(description="handoff", permission=PermissionLevel.HANDOFF)
    async def change_password(password: str) -> str:
        return "changed"

    registry = ToolRegistry()
    registry.register_func(change_password)
    manager = ToolManager(registry, approval_callback=lambda r: True, auto_approve=auto_approve)
    result = await manager.execute("change_password", {"password": "x"})
    assert result.status == ToolStatus.DENIED


@pytest.mark.asyncio
async def test_pre_approved_requires_user_request():
    @tool(description="write", permission=PermissionLevel.PRE_APPROVED)
    async def write_file(path: str, content: str) -> str:
        return "ok"

    registry = ToolRegistry()
    registry.register_func(write_file)
    manager = ToolManager(registry)
    # Agent-initiated (not user requested) -> denied
    assert (await manager.execute("write_file", {"path": "a", "content": "b"})).status == ToolStatus.DENIED
    # Explicit user request -> allowed
    assert (await manager.execute("write_file", {"path": "a", "content": "b"}, user_requested=True)).success


@pytest.mark.asyncio
async def test_builtin_filesystem_tools(tmp_path):
    registry = register_all_builtin_tools(ToolRegistry(), workspace=str(tmp_path))
    manager = ToolManager(registry, auto_approve=True)

    assert (await manager.execute("write_file", {"path": "notes.txt", "content": "hi"})).success
    read = await manager.execute("read_file", {"path": "notes.txt"})
    assert read.success and read.output == "hi"
    listing = await manager.execute("list_dir", {"path": "."})
    assert any(item["path"] == "notes.txt" for item in listing.output)


@pytest.mark.asyncio
async def test_builtin_calculate():
    registry = register_all_builtin_tools(ToolRegistry())
    manager = ToolManager(registry, auto_approve=True)
    result = await manager.execute("calculate", {"expression": "2 + 3 * 4"})
    assert result.success and result.output == 14


@pytest.mark.asyncio
async def test_calculate_rejects_code_execution():
    registry = register_all_builtin_tools(ToolRegistry())
    manager = ToolManager(registry, auto_approve=True)
    result = await manager.execute("calculate", {"expression": "__import__('os').system('echo hi')"})
    assert not result.success


@pytest.mark.asyncio
async def test_filesystem_blocks_escape(tmp_path):
    registry = register_all_builtin_tools(ToolRegistry(), workspace=str(tmp_path))
    manager = ToolManager(registry, auto_approve=True)
    result = await manager.execute("read_file", {"path": "../../etc/passwd"})
    assert not result.success


@pytest.mark.asyncio
async def test_tool_stats_tracked():
    @tool(description="ok")
    async def ok() -> str:
        return "y"

    registry = ToolRegistry()
    registry.register_func(ok)
    manager = ToolManager(registry)
    await manager.execute("ok", {})
    await manager.execute("ok", {})
    stats = manager.get_stats()
    assert stats["ok"]["calls"] == 2
    assert len(manager.recent_calls()) == 2
