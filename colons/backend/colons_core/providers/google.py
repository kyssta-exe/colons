"""
Google Gemini adapter - native generateContent REST API
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


class GeminiAdapter(ProviderAdapter):
    """Native Google Gemini adapter (REST, no SDK dependency)."""

    name = "google"
    supports_tools = True
    supports_vision = True
    supports_embeddings = True

    def __init__(
        self,
        api_key: str = "",
        base_url: Optional[str] = None,
        timeout: float = 300.0,
        **kwargs,
    ):
        super().__init__(api_key=api_key, base_url=base_url, **kwargs)
        self.base_url = (base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    def _client_instance(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=15.0),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------ #
    # Translation
    # ------------------------------------------------------------------ #

    def _to_gemini(self, messages: List[Union[Message, Dict]]):
        norm = self.normalize_messages(messages)
        system_parts: List[str] = []
        contents: List[Dict] = []

        for m in norm:
            role = m.get("role")
            content = m.get("content", "")

            if role == "system":
                system_parts.append(content)
                continue

            if role == "tool":
                contents.append({
                    "role": "user",
                    "parts": [{
                        "functionResponse": {
                            "name": m.get("name") or m.get("tool_call_id", "tool"),
                            "response": {"result": content},
                        }
                    }],
                })
                continue

            if role == "assistant" and m.get("tool_calls"):
                parts = []
                if content:
                    parts.append({"text": content})
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {})
                    try:
                        args = json.loads(fn.get("arguments", "{}") or "{}")
                    except Exception:
                        args = {}
                    parts.append({"functionCall": {"name": fn.get("name", ""), "args": args}})
                contents.append({"role": "model", "parts": parts})
                continue

            gem_role = "model" if role == "assistant" else "user"
            parts = []
            if isinstance(content, list):
                for part in content:
                    if part.get("type") == "text":
                        parts.append({"text": part["text"]})
                    elif part.get("type") == "image_url":
                        header, encoded = part["image_url"]["url"].split(",", 1)
                        parts.append({"inlineData": {"mimeType": header[5:].split(";")[0], "data": encoded}})
            else:
                parts = [{"text": content or ""}]
            contents.append({"role": gem_role, "parts": parts})

        return "\n\n".join(system_parts), contents

    @staticmethod
    def _convert_tools(tools: Optional[List[Dict]]) -> Optional[List[Dict]]:
        if not tools:
            return None
        declarations = []
        for t in tools:
            if t.get("type") == "function":
                fn = t["function"]
                declarations.append({
                    "name": fn.get("name"),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                })
            else:
                declarations.append(t)
        return [{"functionDeclarations": declarations}]

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
        system, contents = self._to_gemini(messages)

        generation_config: Dict = {"temperature": temperature}
        if max_tokens is not None:
            generation_config["maxOutputTokens"] = max_tokens
        for k in ("topP", "topK", "stopSequences"):
            if k in kwargs:
                generation_config[k] = kwargs.pop(k)

        payload: Dict = {"contents": contents, "generationConfig": generation_config}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        converted_tools = self._convert_tools(tools)
        if converted_tools:
            payload["tools"] = converted_tools

        model_name = model if model.startswith("models/") else f"models/{model}"
        method = "streamGenerateContent" if stream else "generateContent"
        url = f"/{model_name}:{method}"
        params = {"key": self.api_key}
        if stream:
            params["alt"] = "sse"

        if stream:
            return self._stream(url, params, payload, model)
        return await self._chat_once(url, params, payload, model)

    async def _chat_once(self, url: str, params: Dict, payload: Dict, model: str) -> ChatCompletion:
        client = self._client_instance()
        try:
            resp = await client.post(url, params=params, json=payload)
        except httpx.HTTPError as e:
            raise ProviderError(f"HTTP error: {e}", self.name, retryable=True) from e

        if resp.status_code >= 400:
            raise ProviderError(
                f"API error {resp.status_code}: {resp.text[:500]}",
                self.name,
                status_code=resp.status_code,
                retryable=resp.status_code in (408, 409, 429, 500, 502, 503, 504),
            )

        data = resp.json()
        usage_data = data.get("usageMetadata", {})
        usage = Usage(
            prompt_tokens=usage_data.get("promptTokenCount", 0),
            completion_tokens=usage_data.get("candidatesTokenCount", 0),
            total_tokens=usage_data.get("totalTokenCount", 0),
            cached_tokens=usage_data.get("cachedContentTokenCount", 0),
        )
        self.record_usage(usage)

        candidate = (data.get("candidates") or [{}])[0]
        parts = (candidate.get("content") or {}).get("parts", [])
        text = ""
        tool_calls: List[Dict] = []
        for p in parts:
            if "text" in p:
                text += p["text"]
            elif "functionCall" in p:
                fc = p["functionCall"]
                tool_calls.append({
                    "id": self.stable_id("call"),
                    "type": "function",
                    "function": {
                        "name": fc.get("name", ""),
                        "arguments": json.dumps(fc.get("args", {})),
                    },
                })

        finish = {
            "STOP": "stop",
            "MAX_TOKENS": "length",
            "SAFETY": "content_filter",
        }.get(candidate.get("finishReason"), "stop")

        return ChatCompletion(
            id=self.stable_id("gemini"),
            choices=[{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": text,
                    "tool_calls": tool_calls or None,
                },
                "finish_reason": finish,
            }],
            created=int(time.time()),
            model=model,
            usage=usage,
        )

    async def _stream(self, url: str, params: Dict, payload: Dict, model: str) -> AsyncGenerator[ChatCompletionChunk, None]:
        client = self._client_instance()
        try:
            async with client.stream("POST", url, params=params, json=payload) as resp:
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
                    try:
                        data = json.loads(data_str)
                    except Exception:
                        continue

                    usage_data = data.get("usageMetadata")
                    usage = None
                    if usage_data:
                        usage = Usage(
                            prompt_tokens=usage_data.get("promptTokenCount", 0),
                            completion_tokens=usage_data.get("candidatesTokenCount", 0),
                            total_tokens=usage_data.get("totalTokenCount", 0),
                        )
                        self.record_usage(usage)

                    candidate = (data.get("candidates") or [{}])[0]
                    parts = (candidate.get("content") or {}).get("parts", [])
                    for p in parts:
                        if "text" in p:
                            yield ChatCompletionChunk(
                                id=self.stable_id("gemini"),
                                choices=[{"index": 0, "delta": {"content": p["text"]}}],
                                created=int(time.time()), model=model, usage=usage,
                            )
                        elif "functionCall" in p:
                            fc = p["functionCall"]
                            yield ChatCompletionChunk(
                                id=self.stable_id("gemini"),
                                choices=[{"index": 0, "delta": {"tool_calls": [{
                                    "index": 0,
                                    "id": self.stable_id("call"),
                                    "type": "function",
                                    "function": {
                                        "name": fc.get("name", ""),
                                        "arguments": json.dumps(fc.get("args", {})),
                                    },
                                }]}}],
                                created=int(time.time()), model=model,
                            )

        except ProviderError:
            raise
        except httpx.HTTPError as e:
            raise ProviderError(f"Stream error: {e}", self.name, retryable=True) from e

    async def embed(self, text: str, model: str = "text-embedding-004") -> List[float]:
        client = self._client_instance()
        url = f"/models/{model}:embedContent"
        resp = await client.post(
            url, params={"key": self.api_key},
            json={"model": f"models/{model}", "content": {"parts": [{"text": text}]}},
        )
        if resp.status_code >= 400:
            raise ProviderError(f"Embedding error {resp.status_code}: {resp.text[:300]}", self.name)
        return resp.json().get("embedding", {}).get("values", [])

    def count_tokens(self, text: str, model: str = "gemini-1.5-pro") -> int:
        return max(1, len(text) // 4)
