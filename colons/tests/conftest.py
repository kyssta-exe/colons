"""
Shared test fixtures: a scripted fake provider that can mimic tool calls and streams.
"""
import asyncio
import json
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

import pytest

from colons_core.providers.base import (
    ChatCompletion, ChatCompletionChunk, Message, ProviderAdapter, Usage,
)


class ScriptedProvider(ProviderAdapter):
    """
    Returns pre-scripted responses in order.
    Each script item is either:
      {"content": "...", "tool_calls": [...]}  -> assistant message
      {"content": "...", "stream": True}       -> streamed in chunks
    """

    name = "scripted"

    def __init__(self, script: List[Dict]):
        super().__init__(api_key="test")
        self.script = list(script)
        self.calls: List[List[Message]] = []
        self.embed_calls = 0

    def _next(self) -> Dict:
        if not self.script:
            return {"content": "(script exhausted)"}
        return self.script.pop(0)

    async def chat(
        self,
        messages,
        model: str = "scripted",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = True,
        tools: Optional[List[Dict]] = None,
        tool_choice: Union[str, Dict, None] = None,
        **kwargs,
    ):
        self.calls.append(list(messages))
        item = self._next()
        tool_calls = item.get("tool_calls")
        content = item.get("content", "")

        if stream and item.get("stream", True):
            return self._stream(content, tool_calls)
        return self._completion(content, tool_calls)

    async def _stream(self, content: str, tool_calls) -> AsyncGenerator[ChatCompletionChunk, None]:
        chunk_id = self.stable_id()
        yield ChatCompletionChunk(
            id=chunk_id,
            choices=[{"index": 0, "delta": {"role": "assistant", "content": ""}}],
            created=int(time.time()), model="scripted",
        )
        if content:
            for piece in [content[i:i + 8] for i in range(0, len(content), 8)]:
                yield ChatCompletionChunk(
                    id=chunk_id,
                    choices=[{"index": 0, "delta": {"content": piece}}],
                    created=int(time.time()), model="scripted",
                )
        if tool_calls:
            for i, tc in enumerate(tool_calls):
                yield ChatCompletionChunk(
                    id=chunk_id,
                    choices=[{"index": 0, "delta": {"tool_calls": [{
                        "index": i, "id": tc["id"], "type": "function",
                        "function": {"name": tc["name"], "arguments": ""},
                    }]}}],
                    created=int(time.time()), model="scripted",
                )
                args = json.dumps(tc.get("arguments", {}))
                for piece in [args[j:j + 10] for j in range(0, len(args), 10)]:
                    yield ChatCompletionChunk(
                        id=chunk_id,
                        choices=[{"index": 0, "delta": {"tool_calls": [{
                            "index": i, "function": {"arguments": piece},
                        }]}}],
                        created=int(time.time()), model="scripted",
                    )
        usage = Usage(prompt_tokens=10, completion_tokens=len(content) // 4, total_tokens=10 + len(content) // 4)
        self.record_usage(usage)
        yield ChatCompletionChunk(
            id=chunk_id,
            choices=[{"index": 0, "delta": {}, "finish_reason": "tool_calls" if tool_calls else "stop"}],
            created=int(time.time()), model="scripted", usage=usage,
        )

    def _completion(self, content: str, tool_calls) -> ChatCompletion:
        usage = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        self.record_usage(usage)
        return ChatCompletion(
            id=self.stable_id(),
            choices=[{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {"id": tc["id"], "type": "function",
                         "function": {"name": tc["name"], "arguments": json.dumps(tc.get("arguments", {}))}}
                        for tc in (tool_calls or [])
                    ] or None,
                },
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }],
            created=int(time.time()), model="scripted", usage=usage,
        )

    async def embed(self, text: str, model: str = "test") -> List[float]:
        self.embed_calls += 1
        import hashlib
        digest = hashlib.sha256(text.encode()).digest()
        return [((b / 255.0) * 2 - 1) for b in digest[:32]]

    def count_tokens(self, text: str, model: str = "scripted") -> int:
        return max(1, len(text) // 4)


@pytest.fixture
def provider():
    return ScriptedProvider([])


@pytest.fixture
def store(tmp_path):
    from colons_core.memory import SQLiteStore
    return SQLiteStore(path=str(tmp_path / "test.db"))


@pytest.fixture
def cache():
    from colons_core.memory import InMemoryCache
    return InMemoryCache(default_ttl=60)
