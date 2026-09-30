"""
Tool registry and manager - registration, permission gating, execution,
timeouts, and approval callbacks.
"""
import asyncio
import inspect
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from .base import (
    PermissionLevel,
    ToolResult,
    ToolSpec,
    ToolStatus,
    spec_from_func,
)

logger = logging.getLogger(__name__)


class ApprovalRequest:
    """Represents a pending approval for a tool call."""

    def __init__(self, tool_name: str, arguments: Dict, spec: ToolSpec, reason: str = ""):
        self.tool_name = tool_name
        self.arguments = arguments
        self.spec = spec
        self.reason = reason

    def to_dict(self) -> Dict:
        return {
            "tool": self.tool_name,
            "arguments": self.arguments,
            "permission": self.spec.permission.value,
            "danger": self.spec.danger,
            "reason": self.reason,
        }


# Callback signature: async (ApprovalRequest) -> bool
ApprovalCallback = Callable[[ApprovalRequest], Any]


class ToolRegistry:
    """Holds all known tools."""

    def __init__(self):
        self._specs: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec):
        self._specs[spec.name] = spec
        logger.debug(f"Registered tool: {spec.name}")

    def register_func(self, func: Callable, **overrides):
        spec = spec_from_func(func)
        for key, value in overrides.items():
            if hasattr(spec, key):
                setattr(spec, key, value)
        self.register(spec)

    def unregister(self, name: str) -> bool:
        return self._specs.pop(name, None) is not None

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._specs.get(name)

    def list(self, category: Optional[str] = None) -> List[ToolSpec]:
        specs = list(self._specs.values())
        if category:
            specs = [s for s in specs if s.category == category]
        return sorted(specs, key=lambda s: s.name)

    def openai_schemas(self, names: Optional[List[str]] = None) -> List[Dict]:
        specs = self.list()
        if names:
            specs = [s for s in specs if s.name in names]
        return [s.to_openai_schema() for s in specs]

    def describe(self) -> List[Dict]:
        return [s.to_dict() for s in self.list()]

    def __contains__(self, name: str) -> bool:
        return name in self._specs

    def __len__(self) -> int:
        return len(self._specs)


class ToolManager:
    """
    Executes tools safely.
    - enforces permissions (auto / pre-approved / ask / handoff)
    - enforces per-tool timeouts
    - records execution history & stats
    - can notify an approval callback for gated tools
    """

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        approval_callback: Optional[ApprovalCallback] = None,
        auto_approve: bool = False,
        global_timeout: float = 120.0,
    ):
        self.registry = registry or ToolRegistry()
        self.approval_callback = approval_callback
        self.auto_approve = auto_approve
        self.permission_mode = "full_access" if auto_approve else "auto"
        self.global_timeout = global_timeout
        self.history: List[Dict] = []
        self.max_history = 500
        self.stats: Dict[str, Dict] = {}

    # ------------------------------------------------------------------ #
    # Registration passthrough
    # ------------------------------------------------------------------ #

    def register(self, spec: ToolSpec):
        self.registry.register(spec)

    def register_func(self, func: Callable, **overrides):
        self.registry.register_func(func, **overrides)

    def unregister(self, name: str):
        self.registry.unregister(name)

    def list_tools(self, category: Optional[str] = None) -> List[Dict]:
        return self.registry.describe() if not category else [s.to_dict() for s in self.registry.list(category)]

    def openai_schemas(self) -> List[Dict]:
        return self.registry.openai_schemas()

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #

    async def execute(
        self,
        name: str,
        arguments: Optional[Dict] = None,
        user_requested: bool = False,
        approval_callback: Optional[ApprovalCallback] = None,
    ) -> ToolResult:
        arguments = arguments or {}
        start = time.perf_counter()

        spec = self.registry.get(name)
        if spec is None:
            return ToolResult(
                status=ToolStatus.ERROR,
                error=f"Unknown tool: {name}",
                metadata={"available": [s.name for s in self.registry.list()]},
            )

        # ---- Permission gating ----
        allowed = await self._check_permission(spec, arguments, user_requested, approval_callback)
        if not allowed:
            return ToolResult(
                status=ToolStatus.DENIED,
                error=f"Permission denied for tool '{name}' (level={spec.permission.value})",
                metadata={"permission": spec.permission.value},
            )

        if spec.func is None:
            return ToolResult(status=ToolStatus.ERROR, error=f"Tool '{name}' has no implementation")

        # ---- Execute with timeout ----
        timeout = min(spec.timeout or self.global_timeout, self.global_timeout)
        try:
            if inspect.iscoroutinefunction(spec.func):
                coro = spec.func(**arguments)
            else:
                coro = asyncio.get_event_loop().run_in_executor(None, lambda: spec.func(**arguments))

            result = await asyncio.wait_for(coro, timeout=timeout)
            tool_result = ToolResult(
                status=ToolStatus.OK,
                output=result,
                metadata={"tool": name},
            )
        except asyncio.TimeoutError:
            tool_result = ToolResult(
                status=ToolStatus.TIMEOUT,
                error=f"Tool '{name}' timed out after {timeout}s",
                metadata={"tool": name, "timeout": timeout},
            )
        except TypeError as e:
            tool_result = ToolResult(
                status=ToolStatus.ERROR,
                error=f"Invalid arguments for '{name}': {e}",
                metadata={"tool": name, "arguments": arguments},
            )
        except Exception as e:
            logger.exception(f"Tool '{name}' failed")
            tool_result = ToolResult(
                status=ToolStatus.ERROR,
                error=str(e),
                metadata={"tool": name, "arguments": arguments, "type": type(e).__name__},
            )

        tool_result.duration_ms = (time.perf_counter() - start) * 1000
        self._record(name, arguments, tool_result)
        return tool_result

    def set_permission_mode(self, mode: str):
        if mode not in ("auto", "approval", "full_access"):
            raise ValueError("Invalid permission mode")
        self.permission_mode = mode
        self.auto_approve = mode == "full_access"

    async def _check_permission(self, spec: ToolSpec, arguments: Dict, user_requested: bool,
                                approval_callback: Optional[ApprovalCallback] = None) -> bool:
        """Returns True if the call may proceed."""
        if spec.permission == PermissionLevel.HANDOFF:
            return False
        if self.auto_approve:
            return True

        if spec.permission == PermissionLevel.AUTO and self.permission_mode != "approval":
            return True

        if (spec.permission == PermissionLevel.PRE_APPROVED and user_requested
                and self.permission_mode != "approval"):
            return True

        if (self.permission_mode == "approval"
                or spec.permission in (PermissionLevel.ASK, PermissionLevel.PRE_APPROVED)):
            callback = approval_callback or self.approval_callback
            if callback is None:
                return False
            request = ApprovalRequest(
                tool_name=spec.name,
                arguments=arguments,
                spec=spec,
                reason=f"Tool '{spec.name}' requires approval"
                       + (" (marked dangerous)" if spec.danger else ""),
            )
            try:
                decision = callback(request)
                if inspect.isawaitable(decision):
                    decision = await decision
                return bool(decision)
            except Exception as e:
                logger.error(f"Approval callback failed: {e}")
                return False

        return True

    # ------------------------------------------------------------------ #
    # History & stats
    # ------------------------------------------------------------------ #

    def _record(self, name: str, arguments: Dict, result: ToolResult):
        entry = {
            "tool": name,
            "arguments": arguments,
            "status": result.status.value,
            "error": result.error,
            "duration_ms": round(result.duration_ms, 2),
            "timestamp": time.time(),
        }
        self.history.append(entry)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

        stats = self.stats.setdefault(name, {"calls": 0, "errors": 0, "total_ms": 0.0})
        stats["calls"] += 1
        stats["total_ms"] += result.duration_ms
        if not result.success:
            stats["errors"] += 1

    def get_stats(self) -> Dict:
        out = {}
        for name, s in self.stats.items():
            calls = max(s["calls"], 1)
            out[name] = {
                "calls": s["calls"],
                "errors": s["errors"],
                "avg_ms": round(s["total_ms"] / calls, 2),
            }
        return out

    def recent_calls(self, limit: int = 20) -> List[Dict]:
        return self.history[-limit:]
