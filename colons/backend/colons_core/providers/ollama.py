"""
Ollama adapter - local models via Ollama server
"""
import json
import time
from typing import AsyncGenerator, Dict, List, Optional, Union

import httpx

from .base import (
    ChatCompletion,
    ChatCompletionChunk,
    ProviderAdapter,
    ProviderError,
    Usage,
)


class OllamaAdapter(ProviderAdapter):
    """Adapter for local models served by Ollama."""

    name = "ollama"
    supports_vision = True
    supports_tools = True
    supports_embeddings = True

    def __init__(
        self,
        api_key: str = "ollama",
        base_url: Optional[str] = None,
        timeout: float = 600.0,
        keep_alive: str = "30m",
        **kwargs,
    ):
        super().__init__(api_key=api_key, base_url=base_url, **kwargs)
        self.base_url = (base_url or "http://localhost:11434").rstrip("/")
        self.timeout = timeout
        self.keep_alive = keep_alive
        self._client: Optional[httpx.AsyncClient] = None

    def _client_instance(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=15.0),
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _build_payload(self, messages, model, temperature, max_tokens, stream, tools, **kwargs) -> Dict:
        payload: Dict = {
            "model": model,
            "messages": self.normalize_messages(messages),
            "stream": stream,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": temperature,
            },
        }
        for message in payload["messages"]:
            content = message.get("content")
            if isinstance(content, list):
                message["content"] = "\n".join(p["text"] for p in content if p.get("type") == "text")
                message["images"] = [p["image_url"]["url"].split(",", 1)[1]
                                     for p in content if p.get("type") == "image_url"]
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens
        for k in ("top_p", "top_k", "seed", "stop", "num_ctx", "repeat_penalty"):
            if k in kwargs:
                payload["options"][k] = kwargs.pop(k)
        if tools:
            payload["tools"] = tools
        return payload

    async def chat(
        self,
        messages,
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = True,
        tools: Optional[List[Dict]] = None,
        tool_choice: Union[str, Dict, None] = None,
        **kwargs,
    ):
        payload = self._build_payload(messages, model, temperature, max_tokens, stream, tools, **kwargs)
        if stream:
            return self._stream(payload, model)
        return await self._chat_once(payload, model)

    async def _chat_once(self, payload: Dict, model: str) -> ChatCompletion:
        client = self._client_instance()
        try:
            resp = await client.post("/api/chat", json=payload)
        except httpx.HTTPError as e:
            raise ProviderError(f"Cannot reach Ollama: {e}", self.name, retryable=True) from e

        if resp.status_code >= 400:
            raise ProviderError(
                f"Ollama error {resp.status_code}: {resp.text[:500]}",
                self.name, status_code=resp.status_code,
            )

        data = resp.json()
        msg = data.get("message", {})
        usage = Usage(
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            total_tokens=data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
        )
        self.record_usage(usage)

        tool_calls = msg.get("tool_calls") or None
        if tool_calls:
            for tc in tool_calls:
                if "id" not in tc:
                    tc["id"] = self.stable_id("call")
                tc.setdefault("type", "function")

        return ChatCompletion(
            id=self.stable_id("ollama"),
            choices=[{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    "tool_calls": tool_calls,
                },
                "finish_reason": "stop" if data.get("done") else "length",
            }],
            created=int(time.time()),
            model=data.get("model", model),
            usage=usage,
        )

    async def _stream(self, payload: Dict, model: str) -> AsyncGenerator[ChatCompletionChunk, None]:
        client = self._client_instance()
        try:
            async with client.stream("POST", "/api/chat", json=payload) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise ProviderError(
                        f"Ollama error {resp.status_code}: {body.decode()[:500]}",
                        self.name, status_code=resp.status_code,
                    )

                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue

                    msg = data.get("message", {})
                    usage = None
                    if data.get("done"):
                        usage = Usage(
                            prompt_tokens=data.get("prompt_eval_count", 0),
                            completion_tokens=data.get("eval_count", 0),
                            total_tokens=data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
                        )
                        self.record_usage(usage)

                    yield ChatCompletionChunk(
                        id=self.stable_id("ollama"),
                        choices=[{
                            "index": 0,
                            "delta": {
                                "role": msg.get("role", "assistant"),
                                "content": msg.get("content", ""),
                                "tool_calls": msg.get("tool_calls"),
                            },
                            "finish_reason": "stop" if data.get("done") else None,
                        }],
                        created=int(time.time()),
                        model=data.get("model", model),
                        usage=usage,
                    )
                    if data.get("done"):
                        break

        except ProviderError:
            raise
        except httpx.HTTPError as e:
            raise ProviderError(f"Stream error: {e}", self.name, retryable=True) from e

    async def embed(self, text: str, model: str = "nomic-embed-text") -> List[float]:
        client = self._client_instance()
        resp = await client.post("/api/embeddings", json={"model": model, "prompt": text})
        if resp.status_code >= 400:
            raise ProviderError(f"Embedding error {resp.status_code}", self.name)
        return resp.json().get("embedding", [])

    async def list_models(self) -> List[str]:
        try:
            client = self._client_instance()
            resp = await client.get("/api/tags")
            if resp.status_code < 400:
                return [m.get("name", "") for m in resp.json().get("models", [])]
        except Exception:
            pass
        return []

    def count_tokens(self, text: str, model: str = "") -> int:
        return max(1, len(text) // 4)
