"""
Tool primitives: spec, result, permission model.
"""
import inspect
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

ToolFunc = Callable[..., Any]


class PermissionLevel(str, Enum):
    """How much scrutiny a tool invocation needs."""
    AUTO = "auto"              # run without asking
    PRE_APPROVED = "pre_approved"  # run if the user explicitly asked for it
    ASK = "ask"                # require user confirmation
    HANDOFF = "handoff"        # agent must not do this; user does it


class ToolStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    DENIED = "denied"
    TIMEOUT = "timeout"


@dataclass
class ToolResult:
    status: ToolStatus = ToolStatus.OK
    output: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0

    @property
    def success(self) -> bool:
        return self.status == ToolStatus.OK

    def to_dict(self) -> Dict:
        return {
            "status": self.status.value,
            "output": self._stringify(self.output),
            "error": self.error,
            "metadata": self.metadata,
            "duration_ms": round(self.duration_ms, 2),
        }

    @staticmethod
    def _stringify(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool, list, dict)):
            return value
        return str(value)


@dataclass
class ToolSpec:
    """Everything the agent needs to know about a tool."""
    name: str
    description: str
    parameters: Dict[str, Any]
    func: Optional[ToolFunc] = None
    permission: PermissionLevel = PermissionLevel.AUTO
    danger: bool = False
    timeout: float = 60.0
    category: str = "general"
    returns: str = "any"

    def to_openai_schema(self) -> Dict:
        """OpenAI function-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "permission": self.permission.value,
            "danger": self.danger,
            "category": self.category,
            "returns": self.returns,
            "timeout": self.timeout,
        }


def tool(
    name: Optional[str] = None,
    description: str = "",
    parameters: Optional[Dict] = None,
    permission: PermissionLevel = PermissionLevel.AUTO,
    danger: bool = False,
    timeout: float = 60.0,
    category: str = "general",
    returns: str = "any",
):
    """
    Decorator to declare a tool. Schema is inferred from type hints when
    `parameters` is not provided.

    Usage:
        @tool(description="Add two numbers", category="math")
        async def add(a: int, b: int) -> int:
            return a + b
    """
    def decorator(func: ToolFunc) -> ToolFunc:
        target = getattr(func, "__func__", func)  # unwrap bound methods
        target.__tool_spec__ = {
            "name": name or target.__name__,
            "description": description or (inspect.getdoc(func) or ""),
            "parameters": parameters or infer_schema(func),
            "permission": permission,
            "danger": danger,
            "timeout": timeout,
            "category": category,
            "returns": returns,
        }
        return func
    return decorator


_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def infer_schema(func: ToolFunc) -> Dict:
    """Infer JSON schema from function signature + docstring."""
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return {"type": "object", "properties": {}}

    props: Dict[str, Any] = {}
    required: List[str] = []
    hints = {}
    try:
        hints = __import__("typing").get_type_hints(func)
    except Exception:
        pass

    for pname, param in sig.parameters.items():
        if pname in ("self", "cls", "*args", "**kwargs"):
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        annotation = hints.get(pname, param.annotation)
        prop: Dict[str, Any] = {"type": _TYPE_MAP.get(annotation, "string")}
        if prop["type"] == "array":
            prop["items"] = {"type": "string"}
        props[pname] = prop
        if param.default is inspect.Parameter.empty:
            required.append(pname)
        else:
            prop["default"] = param.default

    schema: Dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def spec_from_func(func: ToolFunc) -> ToolSpec:
    raw = getattr(func, "__tool_spec__", None)
    if raw is None:
        raw = getattr(getattr(func, "__func__", None), "__tool_spec__", None)  # bound method
    if raw is None:
        raw = {
            "name": getattr(func, "__name__", type(func).__name__),
            "description": inspect.getdoc(func) or "",
            "parameters": infer_schema(func),
            "permission": PermissionLevel.AUTO,
            "danger": False,
            "timeout": 60.0,
            "category": "general",
            "returns": "any",
        }
    return ToolSpec(
        name=raw["name"],
        description=raw["description"],
        parameters=raw["parameters"],
        func=func,
        permission=raw.get("permission", PermissionLevel.AUTO),
        danger=raw.get("danger", False),
        timeout=raw.get("timeout", 60.0),
        category=raw.get("category", "general"),
        returns=raw.get("returns", "any"),
    )
