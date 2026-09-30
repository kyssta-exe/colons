"""
Ollama provider adapter for Colons
Supports local LLMs via Ollama
"""
import httpx
import time
from typing import AsyncGenerator, List, Dict, Union, Optional
import httpx
from .base import ProviderAdapter, Message, Usage, ChatCompletion, ChatCompletionChunk

class OllamaAdapter(ProviderAdapter):
    """Ollama API adapter for local models"""

    def __init__(self, api_key: str = "ollama", base_url: str = "http://localhost:11434", **kwargs):
        # Ollama doesn't use API key in same way, but we keep interface consistent
        super().__init__(api_key, base_url, **kwargs)
        self.base_url = base_url.rstrip('/')
        self.client = httpx.AsyncClient(base_url=self.base_url)

    async def chat(
        self,
        messages: List[Message],
        model: str = "llama3.1",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None = None,
        stream: bool = True,
        tools: Optional[List[Dict]] = None,
        tool_choice: Union[str, Dict, None] = None,
        **kwargs
    ):
        # Convert messages to Ollama format
        ollama_messages = []
        for msg in messages:
            ollama_msg = {
                "role": msg.role,
                "content": msg.content
            }
            ollama_messages.append(ollama_msg)

        # Prepare request
        request_params = {
            "model": model,
            "messages": ollama_messages,
            "stream": stream,
            "options": {
                "temperature": temperature,
            }
        }

        if max_tokens is not None:
            request_params["options"]["num_predict"] = max_tokens
        # Note: Ollama tool calling is experimental and varies by model
        # For now, we'll pass tools through but note limited support
        if tools:
            request_params["tools"] = tools

        request_params.update(kwargs)

        # Make request
        if stream:
            return self._stream_chat(**request_params)
        else:
            return await self._chat_non_stream(**request_params)

    async def _chat_non_stream(self, **kwargs) -> ChatCompletion:
        response = await self.client.post("/api/chat", json=kwargs)
        response.raise_for_status()
        result = response.json()

        # Update usage (Ollama provides prompt_eval_count and eval_count)
        usage = Usage(
            prompt_tokens=result.get("prompt_eval_count", 0),
            completion_tokens=result.get("eval_count", 0),
            total_tokens=result.get("prompt_eval_count", 0) + result.get("eval_count", 0)
        )
        self._update_usage(usage)

        return ChatCompletion(
            id=result.get("model", "unknown"),
            choices=[{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": result.get("message", {}).get("content", ""),
                    "tool_calls": None  # Ollama tool support limited
                },
                "finish_reason": "stop" if result.get("done") else "length"
            }],
            created=int(time.time()),
            model=result.get("model", "unknown"),
            usage=usage
        )

    async def _stream_chat(self, **kwargs) -> AsyncGenerator[ChatCompletionChunk, None]:
        async with self.client.stream("POST", "/api/chat", json=kwargs) as response:
            response.raise_for_status()

            async for line in response.aiter_lines():
                if line.strip():
                    try:
                        chunk_data = httpx._models.Response.json.loads(line)
                        yield ChatCompletionChunk(
                            id=chunk_data.get("model", "unknown"),
                            choices=[{
                                "index": 0,
                                "delta": {
                                    "role": chunk_data.get("message", {}).get("role", "assistant"),
                                    "content": chunk_data.get("message", {}).get("content", ""),
                                    "tool_calls": None
                                }
                            }],
                            created=int(time.time()),
                            model=chunk_data.get("model", "unknown"),
                            usage=Usage(
                                prompt_tokens=chunk_data.get("prompt_eval_count", 0),
                                completion_tokens=chunk_data.get("eval_count", 0),
                                total_tokens=chunk_data.get("prompt_eval_count", 0) + chunk_data.get("eval_count", 0)
                            ) if "prompt_eval_count" in chunk_data else None
                        )

                        if chunk_data.get("done"):
                            break
                    except Exception:
                        # Skip malformed lines
                        continue

    async def embed(self, text: str, model: str = "nomic-embed-text") -> List[float]:
        response = await self.client.post("/api/embeddings", json={
            "model": model,
            "prompt": text
        })
        response.raise_for_status()
        result = response.json()

        # Update usage (approximation)
        self._update_usage(Usage(
            prompt_tokens=len(text) // 4,
            total_tokens=len(text) // 4
        ))
        return result.get("embedding", [])

    def count_tokens(self, text: str, model: str = "llama3.1") -> int:
        # Ollama doesn't provide token counting in API
        # Rough approximation
        return len(text) // 4

    async def list_models(self) -> List[Dict]:
        """List available models in Ollama"""
        response = await self.client.get("/api/tags")
        response.raise_for_status()
        return response.json().get("models", [])