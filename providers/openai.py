"""
OpenAI provider adapter for Colons
"""
import openai
from typing import AsyncGenerator, List, Union
from .base import ProviderAdapter, Message, Usage, ChatCompletion, ChatCompletionChunk

class OpenAIAdapter(ProviderAdapter):
    """OpenAI API adapter"""

    def __init__(self, api_key: str, base_url: Optional[str] = None, **kwargs):
        super().__init__(api_key, base_url, **kwargs)
        self.client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            **kwargs
        )

    async def chat(
        self,
        messages: List[Message],
        model: str = "gpt-4",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None = None,
        stream: bool = True,
        tools: Optional[List[Dict]] = None,
        tool_choice: Union[str, Dict, None] = None,
        **kwargs
    ):
        # Convert our Message format to OpenAI format
        openai_messages = []
        for msg in messages:
            openai_msg = {
                "role": msg.role,
                "content": msg.content
            }
            if msg.name:
                openai_msg["name"] = msg.name
            if msg.tool_calls:
                openai_msg["tool_calls"] = msg.tool_calls
            if msg.tool_call_id:
                openai_msg["tool_call_id"] = msg.tool_call_id
            openai_messages.append(openai_msg)

        # Prepare request
        request_params = {
            "model": model,
            "messages": openai_messages,
            "temperature": temperature,
            "stream": stream,
        }

        if max_tokens is not None:
            request_params["max_tokens"] = max_tokens
        if tools:
            request_params["tools"] = tools
            if tool_choice:
                request_params["tool_choice"] = tool_choice

        request_params.update(kwargs)

        # Make request
        if stream:
            return self._stream_chat(**request_params)
        else:
            return await self._chat_non_stream(**request_params)

    async def _chat_non_stream(self, **kwargs) -> ChatCompletion:
        response = await self.client.chat.completions.create(**kwargs)

        # Update usage
        usage = Usage(
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens
        )
        self._update_usage(usage)

        # Convert to our format
        return ChatCompletion(
            id=response.id,
            choices=[{
                "index": choice.index,
                "message": {
                    "role": choice.message.role,
                    "content": choice.message.content or "",
                    "tool_calls": choice.message.tool_calls
                },
                "finish_reason": choice.finish_reason
            } for choice in response.choices],
            created=response.created,
            model=response.model,
            usage=usage
        )

    async def _stream_chat(self, **kwargs) -> AsyncGenerator[ChatCompletionChunk, None]:
        stream = await self.client.chat.completions.create(**kwargs)

        async for chunk in stream:
            has_usage = chunk.usage is not None
            usage = Usage(**chunk.usage.model_dump()) if has_usage else None

            yield ChatCompletionChunk(
                id=chunk.id,
                choices=[{
                    "index": choice.index,
                    "delta": {
                        "role": choice.delta.role,
                        "content": choice.delta.content,
                        "tool_calls": choice.delta.tool_calls
                    }
                } for choice in chunk.choices],
                created=chunk.created,
                model=chunk.model,
                usage=usage
            )

    async def embed(self, text: str, model: str = "text-embedding-3-small") -> List[float]:
        response = await self.client.embeddings.create(input=[text], model=model)
        self._update_usage(Usage(
            prompt_tokens=response.usage.prompt_tokens,
            total_tokens=response.usage.total_tokens
        ))
        return response.data[0].embedding

    def count_tokens(self, text: str, model: str = "gpt-4") -> int:
        # Simple approximation - for production use tiktoken
        try:
            import tiktoken
            encoding = tiktoken.encoding_for_model(model)
            return len(encoding.encode(text))
        except ImportError:
            # Fallback approximation
            return len(text) // 4