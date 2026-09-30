"""
Anthropic provider adapter for Colons
"""
import anthropic
from typing import AsyncGenerator, List, Union
from .base import ProviderAdapter, Message, Usage, ChatCompletion, ChatCompletionChunk

class AnthropicAdapter(ProviderAdapter):
    """Anthropic Claude API adapter"""

    def __init__(self, api_key: str, base_url: Optional[str] = None, **kwargs):
        super().__init__(api_key, base_url, **kwargs)
        self.client = anthropic.AsyncAnthropic(
            api_key=api_key,
            **kwargs
        )

    async def chat(
        self,
        messages: List[Message],
        model: str = "claude-3-5-sonnet-20241022",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None = None,
        stream: bool = True,
        tools: Optional[List[Dict]] = None,
        tool_choice: Union[str, Dict, None] = None,
        **kwargs
    ):
        # Convert messages to Anthropic format
        anthropic_messages = []
        system_content = ""

        for msg in messages:
            if msg.role == "system":
                system_content = msg.content
            else:
                anthropic_msg = {
                    "role": msg.role,
                    "content": msg.content
                }
                anthropic_messages.append(anthropic_msg)

        # Prepare request
        request_params = {
            "model": model,
            "messages": anthropic_messages,
            "temperature": temperature,
            "stream": stream,
            "system": system_content if system_content else None,
        }

        if max_tokens is not None:
            request_params["max_tokens"] = max_tokens
        if tools:
            request_params["tools"] = tools

        request_params.update(kwargs)

        # Remove None values
        request_params = {k: v for k, v in request_params.items() if v is not None}

        # Make request
        if stream:
            return self._stream_chat(**request_params)
        else:
            return await self._chat_non_stream(**request_params)

    async def _chat_non_stream(self, **kwargs) -> ChatCompletion:
        response = await self.client.messages.create(**kwargs)

        # Update usage (Anthropic uses input_tokens/output_tokens)
        usage = Usage(
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens
        )
        self._update_usage(usage)

        # Convert to our format
        return ChatCompletion(
            id=response.id,
            choices=[{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response.content[0].text if response.content else "",
                    "tool_calls": None  # Anthropic tool calls handled differently
                },
                "finish_reason": response.stop_reason
            }],
            created=response.created_at,
            model=response.model,
            usage=usage
        )

    async def _stream_chat(self, **kwargs) -> AsyncGenerator[ChatCompletionChunk, None]:
        async with self.client.messages.stream(**kwargs) as stream:
            async for chunk in stream.text_stream:
                # For simplicity, we'll yield chunks as they come
                # In production, you'd want to properly parse Anthropic's streaming format
                yield ChatCompletionChunk(
                    id=f"chunk_{hash(str(chunk))}",
                    choices=[{
                        "index": 0,
                        "delta": {
                            "content": chunk
                        }
                    }],
                    created=0,  # Anthropic doesn't provide timestamp in stream
                    model=kwargs.get("model", "claude-3-5-sonnet-20241022")
                )

    async def embed(self, text: str, model: str) -> List[float]:
        # Anthropic doesn't have a public embedding API as of 2024
        # Fallback to a simple hash-based embedding or raise NotImplemented
        raise NotImplementedError("Anthropic does not currently offer embedding API")

    def count_tokens(self, text: str, model: str = "claude-3-5-sonnet-20241022") -> int:
        # Anthropic doesn't provide token counting utility
        # Rough approximation
        return len(text) // 4