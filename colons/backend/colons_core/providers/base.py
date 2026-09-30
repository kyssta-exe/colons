"""
Base provider adapter - common interface for all AI providers
Normalizes requests/responses to a unified OpenAI-compatible format
"""
import abc
import hashlib
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, List, Optional, Union


@dataclass
class Usage:
    """Token usage statistics"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0

    def add(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
        )


@dataclass
class Message:
    """Standard message format (OpenAI-compatible)"""
    role: str
    content: str = ""
    name: Optional[str] = None
    tool_calls: Optional[List[Dict]] = None
    tool_call_id: Optional[str] = None
    attachments: Optional[List[Dict]] = None

    def to_dict(self) -> Dict:
        d: Dict[str, Any] = {"role": self.role}
        if self.content:
            d["content"] = self.content
        if self.name:
            d["name"] = self.name
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.attachments:
            d["attachments"] = self.attachments
        return d

    def to_provider_dict(self) -> Dict:
        from ..agents.attachments import attachment_content
        data = self.to_dict()
        data.pop("attachments", None)
        if self.attachments:
            data["content"] = attachment_content(self.content, self.attachments)
        return data


@dataclass
class ChatCompletionChunk:
    """Streaming chunk, normalized to OpenAI format"""
    id: str
    choices: List[Dict]
    created: int
    model: str
    usage: Optional[Usage] = None


@dataclass
class ChatCompletion:
    """Non-streaming response, normalized to OpenAI format"""
    id: str
    choices: List[Dict]
    created: int
    model: str
    usage: Usage
    object: str = "chat.completion"

    def to_openai(self) -> Dict:
        return {
            "id": self.id,
            "object": self.object,
            "created": self.created,
            "model": self.model,
            "choices": self.choices,
            "usage": {
                "prompt_tokens": self.usage.prompt_tokens,
                "completion_tokens": self.usage.completion_tokens,
                "total_tokens": self.usage.total_tokens,
            },
        }


class ProviderError(Exception):
    """Base provider error"""

    def __init__(self, message: str, provider: str = "", status_code: int = 0, retryable: bool = False):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.retryable = retryable


class ProviderAdapter(abc.ABC):
    """
    Base class for all provider adapters.
    Each adapter translates the unified interface to a provider-specific API.
    """

    name: str = "base"
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_vision: bool = False
    supports_embeddings: bool = False
    supports_transcription: bool = False

    def __init__(self, api_key: str = "", base_url: Optional[str] = None, **kwargs):
        self.api_key = api_key
        self.base_url = base_url
        self.options = kwargs
        self._usage = Usage()
        # Optional callbacks for usage tracking
        self.on_usage: Optional[Any] = None

    # ------------------------------------------------------------------ #
    # Required interface
    # ------------------------------------------------------------------ #

    @abc.abstractmethod
    async def chat(
        self,
        messages: List[Union[Message, Dict]],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = True,
        tools: Optional[List[Dict]] = None,
        tool_choice: Union[str, Dict, None] = None,
        **kwargs,
    ) -> Union[ChatCompletion, AsyncGenerator[ChatCompletionChunk, None]]:
        """Send a chat completion request. Returns completion or async generator of chunks."""
        raise NotImplementedError

    @abc.abstractmethod
    def count_tokens(self, text: str, model: str) -> int:
        """Estimate the number of tokens in a text for a given model."""
        raise NotImplementedError

    async def embed(self, text: str, model: str) -> List[float]:
        """Generate an embedding. Providers without embedding support raise NotImplementedError."""
        raise NotImplementedError(f"{self.name} does not support embeddings")

    async def transcribe(self, audio: bytes, filename: str = "audio.webm",
                         model: str = "", language: Optional[str] = None) -> Dict:
        """Speech-to-text. Providers without support raise NotImplementedError."""
        raise NotImplementedError(f"{self.name} does not support transcription")

    async def list_models(self) -> List[str]:
        """List models available for this provider. Default: empty."""
        return []

    # ------------------------------------------------------------------ #
    # Usage tracking
    # ------------------------------------------------------------------ #

    def get_usage(self) -> Usage:
        return self._usage

    def reset_usage(self):
        self._usage = Usage()

    def record_usage(self, usage: Optional[Usage]):
        if not usage:
            return
        self._usage = self._usage.add(usage)
        if self.on_usage:
            try:
                self.on_usage(usage)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def normalize_messages(messages: List[Union[Message, Dict]]) -> List[Dict]:
        """Convert Message dataclasses / dicts into plain OpenAI-style dicts."""
        out: List[Dict] = []
        for m in messages:
            if isinstance(m, Message):
                out.append(m.to_provider_dict())
            elif isinstance(m, dict):
                msg = {"role": m.get("role", "user")}
                if m.get("content") is not None:
                    msg["content"] = m["content"]
                if m.get("name"):
                    msg["name"] = m["name"]
                if m.get("tool_calls"):
                    msg["tool_calls"] = m["tool_calls"]
                if m.get("tool_call_id"):
                    msg["tool_call_id"] = m["tool_call_id"]
                out.append(msg)
            else:
                raise TypeError(f"Unsupported message type: {type(m)}")
        return out

    @staticmethod
    def stable_id(prefix: str = "chatcmpl") -> str:
        basis = f"{prefix}-{time.time()}-{hashlib.md5(str(time.time_ns()).encode()).hexdigest()[:8]}"
        return basis

    def is_configured(self) -> bool:
        return bool(self.api_key) or self.name in ("ollama", "local")

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name} base_url={self.base_url}>"
