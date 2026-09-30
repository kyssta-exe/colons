"""
OpenAI adapter - also serves as the generic OpenAI-compatible adapter
Works with: OpenAI, Azure OpenAI, OpenRouter, Together, Groq, Fireworks,
DeepSeek, xAI, Mistral, Perplexity, LM Studio, vLLM, llama.cpp server, etc.
"""
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


class OpenAIAdapter(ProviderAdapter):
    """Adapter for OpenAI and any OpenAI-compatible /chat/completions endpoint."""

    name = "openai"
    supports_tools = True
    supports_vision = True
    supports_embeddings = True
    supports_transcription = True

    def __init__(
        self,
        api_key: str = "",
        base_url: Optional[str] = None,
        timeout: float = 300.0,
        extra_headers: Optional[Dict[str, str]] = None,
        supports_stream_options: bool = True,
        **kwargs,
    ):
        super().__init__(api_key=api_key, base_url=base_url, **kwargs)
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.timeout = timeout
        self.extra_headers = extra_headers or {}
        # Some OpenAI-compatible servers reject `stream_options`; auto-disable on 400
        self.supports_stream_options = bool(supports_stream_options)
        self._client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------ #
    # HTTP plumbing
    # ------------------------------------------------------------------ #

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        headers.update(self.extra_headers)
        return headers

    def _client_instance(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=15.0),
                headers=self._headers(),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _build_payload(
        self,
        messages,
        model: str,
        temperature: float,
        max_tokens: Optional[int],
        stream: bool,
        tools: Optional[List[Dict]],
        tool_choice: Union[str, Dict, None],
        **kwargs,
    ) -> Dict:
        payload: Dict = {
            "model": model,
            "messages": self.normalize_messages(messages),
            "temperature": temperature,
            "stream": stream,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
        if stream and self.supports_stream_options:
            # Ask OpenAI-compatible endpoints for usage on the final chunk
            payload["stream_options"] = {"include_usage": True}
        payload.update(kwargs)
        return payload

    @staticmethod
    def _is_stream_options_rejection(status_code: int, body: str) -> bool:
        return status_code == 400 and "stream_options" in (body or "").lower()

    # ------------------------------------------------------------------ #
    # Chat
    # ------------------------------------------------------------------ #

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
        payload = self._build_payload(
            messages, model, temperature, max_tokens, stream, tools, tool_choice, **kwargs
        )
        if stream:
            return self._stream(payload)
        return await self._chat_once(payload)

    async def _chat_once(self, payload: Dict) -> ChatCompletion:
        client = self._client_instance()
        try:
            resp = await client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as e:
            raise ProviderError(f"Request timed out: {e}", self.name, retryable=True) from e
        except httpx.HTTPError as e:
            raise ProviderError(f"HTTP error: {e}", self.name, retryable=True) from e

        if self._is_stream_options_rejection(resp.status_code, resp.text):
            # Server doesn't understand stream_options; retry once without it
            self.supports_stream_options = False
            payload.pop("stream_options", None)
            resp = await client.post("/chat/completions", json=payload)

        if resp.status_code >= 400:
            raise ProviderError(
                f"API error {resp.status_code}: {resp.text[:500]}",
                self.name,
                status_code=resp.status_code,
                retryable=resp.status_code in (408, 409, 429, 500, 502, 503, 504),
            )

        data = resp.json()
        usage_data = data.get("usage") or {}
        usage = Usage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
            cached_tokens=(usage_data.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
        )
        self.record_usage(usage)

        choices = []
        for c in data.get("choices", []):
            msg = c.get("message", {})
            choices.append({
                "index": c.get("index", 0),
                "message": {
                    "role": msg.get("role", "assistant"),
                    "content": msg.get("content") or "",
                    "tool_calls": msg.get("tool_calls"),
                    "reasoning_content": msg.get("reasoning_content"),
                },
                "finish_reason": c.get("finish_reason"),
            })

        return ChatCompletion(
            id=data.get("id", self.stable_id()),
            choices=choices,
            created=data.get("created", int(time.time())),
            model=data.get("model", payload["model"]),
            usage=usage,
        )

    async def _stream(self, payload: Dict) -> AsyncGenerator[ChatCompletionChunk, None]:
        client = self._client_instance()
        try:
            async with client.stream("POST", "/chat/completions", json=payload) as resp:
                if self._is_stream_options_rejection(resp.status_code,
                                                     (await resp.aread()).decode()):
                    # Retry without stream_options (the body was consumed)
                    self.supports_stream_options = False
                    payload = {k: v for k, v in payload.items() if k != "stream_options"}
                    async for chunk in self._stream(payload):
                        yield chunk
                    return
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise ProviderError(
                        f"API error {resp.status_code}: {body.decode()[:500]}",
                        self.name,
                        status_code=resp.status_code,
                        retryable=resp.status_code in (408, 409, 429, 500, 502, 503, 504),
                    )

                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:"):].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = __import__("json").loads(data_str)
                    except Exception:
                        continue

                    usage = None
                    usage_data = data.get("usage")
                    if usage_data:
                        usage = Usage(
                            prompt_tokens=usage_data.get("prompt_tokens", 0),
                            completion_tokens=usage_data.get("completion_tokens", 0),
                            total_tokens=usage_data.get("total_tokens", 0),
                            cached_tokens=(usage_data.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
                        )
                        self.record_usage(usage)

                    choices = []
                    for c in data.get("choices", []):
                        delta = c.get("delta", {})
                        choices.append({
                            "index": c.get("index", 0),
                            "delta": {
                                "role": delta.get("role"),
                                "content": delta.get("content"),
                                "tool_calls": delta.get("tool_calls"),
                                "reasoning_content": delta.get("reasoning_content"),
                            },
                            "finish_reason": c.get("finish_reason"),
                        })

                    yield ChatCompletionChunk(
                        id=data.get("id", self.stable_id()),
                        choices=choices,
                        created=data.get("created", int(time.time())),
                        model=data.get("model", payload["model"]),
                        usage=usage,
                    )
        except ProviderError:
            raise
        except httpx.HTTPError as e:
            raise ProviderError(f"Stream error: {e}", self.name, retryable=True) from e

    # ------------------------------------------------------------------ #
    # Embeddings & tokens
    # ------------------------------------------------------------------ #

    async def embed(self, text: str, model: str = "text-embedding-3-small") -> List[float]:
        client = self._client_instance()
        resp = await client.post("/embeddings", json={"model": model, "input": text})
        if resp.status_code >= 400:
            raise ProviderError(f"Embedding error {resp.status_code}: {resp.text[:300]}", self.name)
        data = resp.json()
        usage_data = data.get("usage") or {}
        self.record_usage(Usage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        ))
        return data["data"][0]["embedding"]

    async def list_models(self) -> List[str]:
        try:
            client = self._client_instance()
            resp = await client.get("/models")
            if resp.status_code < 400:
                return [m.get("id", "") for m in resp.json().get("data", [])]
        except Exception:
            pass
        return []

    async def transcribe(self, audio: bytes, filename: str = "audio.webm",
                         model: str = "whisper-1", language: Optional[str] = None) -> Dict:
        """Speech-to-text via the OpenAI-compatible /audio/transcriptions endpoint."""
        client = self._client_instance()
        data = {"model": model or "whisper-1"}
        if language:
            data["language"] = language
        files = {"file": (filename, audio)}
        resp = await client.post("/audio/transcriptions", files=files, data=data)
        if resp.status_code >= 400:
            raise ProviderError(
                f"Transcription error {resp.status_code}: {resp.text[:300]}",
                self.name, status_code=resp.status_code,
            )
        payload = resp.json()
        return {
            "text": payload.get("text", ""),
            "language": payload.get("language", language or ""),
            "duration": payload.get("duration", 0),
            "model": data["model"],
        }

    def count_tokens(self, text: str, model: str = "gpt-4o") -> int:
        try:
            import tiktoken
            try:
                enc = tiktoken.encoding_for_model(model)
            except KeyError:
                enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            # Rough approximation: ~4 chars per token
            return max(1, len(text) // 4)
