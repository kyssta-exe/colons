"""
Anthropic adapter - native Messages API with streaming and tool support
"""
import json
import time
from typing import AsyncGenerator, Dict, List, Optional, Union

import httpx

from .base import (
    ChatCompletion,
    ChatCompletionChunk,
    Message,
    ProviderAdapter,
    ProviderError,
    Usage,
)


class AnthropicAdapter(ProviderAdapter):
    """Native Anthropic Claude adapter."""

    name = "anthropic"
    supports_tools = True
    supports_vision = True
    supports_embeddings = False

    def __init__(
        self,
        api_key: str = "",
        base_url: Optional[str] = None,
        timeout: float = 300.0,
        anthropic_version: str = "2023-06-01",
        **kwargs,
    ):
        super().__init__(api_key=api_key, base_url=base_url, **kwargs)
        self.base_url = (base_url or "https://api.anthropic.com/v1").rstrip("/")
        self.timeout = timeout
        self.anthropic_version = anthropic_version
        self._client: Optional[httpx.AsyncClient] = None

    def _client_instance(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=15.0),
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.api_key,
                    "anthropic-version": self.anthropic_version,
                },
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------ #
    # Translation helpers
    # ------------------------------------------------------------------ #

    def _split_system(self, messages: List[Union[Message, Dict]]):
        """Anthropic takes system prompt separately and merges tool results into user turns."""
        norm = self.normalize_messages(messages)
        system_parts: List[str] = []
        out: List[Dict] = []

        for m in norm:
            role = m.get("role")
            content = m.get("content", "")

            if role == "system":
                system_parts.append(content)
                continue

            if role == "tool":
                # Tool results must be user-role content blocks
                out.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id", ""),
                        "content": content or "",
                    }],
                })
                continue

            if role == "assistant" and m.get("tool_calls"):
                blocks = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {})
                    try:
                        args = json.loads(fn.get("arguments", "{}") or "{}")
                    except Exception:
                        args = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": args,
                    })
                out.append({"role": "assistant", "content": blocks})
                continue

            if isinstance(content, list):
                blocks = []
                for part in content:
                    if part.get("type") == "text":
                        blocks.append(part)
                    elif part.get("type") == "image_url":
                        header, encoded = part["image_url"]["url"].split(",", 1)
                        blocks.append({"type": "image", "source": {"type": "base64",
                            "media_type": header[5:].split(";")[0], "data": encoded}})
                out.append({"role": role, "content": blocks})
                continue

            # Collapse consecutive same-role messages (Anthropic requires alternation)
            if out and out[-1]["role"] == role and isinstance(out[-1]["content"], str):
                out[-1]["content"] += "\n\n" + (content or "")
            else:
                out.append({"role": role, "content": content or ""})

        # Anthropic requires first message to be user
        if out and out[0]["role"] == "assistant":
            out.insert(0, {"role": "user", "content": "(conversation start)"})

        return "\n\n".join(system_parts), out

    @staticmethod
    def _convert_tools(tools: Optional[List[Dict]]) -> Optional[List[Dict]]:
        """Convert OpenAI-format tools -> Anthropic tools."""
        if not tools:
            return None
        converted = []
        for t in tools:
            if t.get("type") == "function":
                fn = t["function"]
                converted.append({
                    "name": fn.get("name"),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                })
            else:
                converted.append(t)
        return converted

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
        system, converted = self._split_system(messages)
        payload: Dict = {
            "model": model,
            "messages": converted,
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
            "stream": stream,
        }
        if system:
            payload["system"] = system
        converted_tools = self._convert_tools(tools)
        if converted_tools:
            payload["tools"] = converted_tools
        for k in ("top_p", "top_k", "stop_sequences"):
            if k in kwargs:
                payload[k] = kwargs.pop(k)
        payload.update(kwargs)

        if stream:
            return self._stream(payload)
        return await self._chat_once(payload)

    async def _chat_once(self, payload: Dict) -> ChatCompletion:
        client = self._client_instance()
        try:
            resp = await client.post("/messages", json=payload)
        except httpx.HTTPError as e:
            raise ProviderError(f"HTTP error: {e}", self.name, retryable=True) from e

        if resp.status_code >= 400:
            raise ProviderError(
                f"API error {resp.status_code}: {resp.text[:500]}",
                self.name,
                status_code=resp.status_code,
                retryable=resp.status_code in (408, 409, 429, 500, 502, 503, 504, 529),
            )

        data = resp.json()
        usage_data = data.get("usage", {})
        usage = Usage(
            prompt_tokens=usage_data.get("input_tokens", 0),
            completion_tokens=usage_data.get("output_tokens", 0),
            total_tokens=usage_data.get("input_tokens", 0) + usage_data.get("output_tokens", 0),
            cached_tokens=usage_data.get("cache_read_input_tokens", 0),
        )
        self.record_usage(usage)

        text_content = ""
        tool_calls: List[Dict] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_content += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })

        finish = {
            "end_turn": "stop",
            "stop_sequence": "stop",
            "max_tokens": "length",
            "tool_use": "tool_calls",
        }.get(data.get("stop_reason"), data.get("stop_reason"))

        return ChatCompletion(
            id=data.get("id", self.stable_id("msg")),
            choices=[{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": text_content,
                    "tool_calls": tool_calls or None,
                },
                "finish_reason": finish,
            }],
            created=int(time.time()),
            model=data.get("model", payload["model"]),
            usage=usage,
        )

    async def _stream(self, payload: Dict) -> AsyncGenerator[ChatCompletionChunk, None]:
        client = self._client_instance()
        message_id = self.stable_id("msg")
        model = payload["model"]

        # Track tool_use blocks while streaming
        tool_blocks: Dict[int, Dict] = {}
        input_tokens = 0

        try:
            async with client.stream("POST", "/messages", json=payload) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise ProviderError(
                        f"API error {resp.status_code}: {body.decode()[:500]}",
                        self.name,
                        status_code=resp.status_code,
                        retryable=resp.status_code in (408, 409, 429, 500, 502, 503, 504, 529),
                    )

                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:"):].strip()
                    try:
                        event = json.loads(data_str)
                    except Exception:
                        continue

                    etype = event.get("type")

                    if etype == "message_start":
                        msg = event.get("message", {})
                        message_id = msg.get("id", message_id)
                        usage = msg.get("usage", {})
                        input_tokens = usage.get("input_tokens", 0)
                        self.record_usage(Usage(prompt_tokens=input_tokens, total_tokens=input_tokens))
                        yield ChatCompletionChunk(
                            id=message_id, choices=[{"index": 0, "delta": {"role": "assistant", "content": ""}}],
                            created=int(time.time()), model=model,
                        )

                    elif etype == "content_block_start":
                        block = event.get("content_block", {})
                        idx = event.get("index", 0)
                        if block.get("type") == "tool_use":
                            tool_blocks[idx] = {
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                                "json": "",
                            }
                            yield ChatCompletionChunk(
                                id=message_id,
                                choices=[{"index": 0, "delta": {"tool_calls": [{
                                    "index": idx,
                                    "id": block.get("id", ""),
                                    "type": "function",
                                    "function": {"name": block.get("name", ""), "arguments": ""},
                                }]}}],
                                created=int(time.time()), model=model,
                            )

                    elif etype == "content_block_delta":
                        delta = event.get("delta", {})
                        idx = event.get("index", 0)
                        if delta.get("type") == "text_delta":
                            yield ChatCompletionChunk(
                                id=message_id,
                                choices=[{"index": 0, "delta": {"content": delta.get("text", "")}}],
                                created=int(time.time()), model=model,
                            )
                        elif delta.get("type") == "input_json_delta":
                            partial = delta.get("partial_json", "")
                            if idx in tool_blocks:
                                tool_blocks[idx]["json"] += partial
                            yield ChatCompletionChunk(
                                id=message_id,
                                choices=[{"index": 0, "delta": {"tool_calls": [{
                                    "index": idx,
                                    "function": {"arguments": partial},
                                }]}}],
                                created=int(time.time()), model=model,
                            )

                    elif etype == "message_delta":
                        usage = event.get("usage", {})
                        output_tokens = usage.get("output_tokens", 0)
                        self.record_usage(Usage(
                            completion_tokens=output_tokens,
                            total_tokens=output_tokens,
                        ))
                        stop_reason = (event.get("delta") or {}).get("stop_reason")
                        finish = {
                            "end_turn": "stop",
                            "max_tokens": "length",
                            "tool_use": "tool_calls",
                        }.get(stop_reason, stop_reason)
                        yield ChatCompletionChunk(
                            id=message_id,
                            choices=[{"index": 0, "delta": {}, "finish_reason": finish}],
                            created=int(time.time()), model=model,
                        )

        except ProviderError:
            raise
        except httpx.HTTPError as e:
            raise ProviderError(f"Stream error: {e}", self.name, retryable=True) from e

    def count_tokens(self, text: str, model: str = "claude-3-5-sonnet") -> int:
        return max(1, len(text) // 4)
