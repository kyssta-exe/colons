"""
Google provider adapter for Colons
"""
import asyncio
import time
import google.generativeai as genai
from typing import AsyncGenerator, List, Union, Dict
from .base import ProviderAdapter, Message, Usage, ChatCompletion, ChatCompletionChunk

class GoogleAdapter(ProviderAdapter):
    """Google Gemini API adapter"""

    def __init__(self, api_key: str, base_url: Optional[str] = None, **kwargs):
        super().__init__(api_key, base_url, **kwargs)
        genai.configure(api_key=api_key)
        # Note: Google's API structure is different, we'll use the GenerativeModel approach

    async def chat(
        self,
        messages: List[Message],
        model: str = "gemini-1.5-pro",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None = None,
        stream: bool = True,
        tools: Optional[List[Dict]] = None,
        tool_choice: Union[str, Dict, None] = None,
        **kwargs
    ):
        # Convert messages to Google format
        # Google uses a different format: contents with parts
        # For simplicity, we'll concatenate or use the last user message
        # This is a simplified implementation - production would need proper conversion

        # Extract system instruction
        system_instruction = ""
        chat_messages = []

        for msg in messages:
            if msg.role == "system":
                system_instruction = msg.content
            else:
                chat_messages.append({
                    "role": "user" if msg.role == "user" else "model",
                    "parts": [{"text": msg.content}]
                })

        # Initialize model
        model_instance = genai.GenerativeModel(
            model_name=model,
            system_instruction=system_instruction if system_instruction else None,
            generation_config=genai.types.GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
        )

        # Prepare request
        if stream:
            return self._stream_chat(model_instance, chat_messages, **kwargs)
        else:
            return await self._chat_non_stream(model_instance, chat_messages, **kwargs)

    async def _chat_non_stream(self, model_instance, messages, **kwargs) -> ChatCompletion:
        # Google's API is synchronous, so we run it in thread pool for async
        import asyncio
        loop = asyncio.get_event_loop()

        # For chat, we need to start a chat session
        chat = model_instance.start_chat(history=[])
        response = await loop.run_in_executor(
            None, lambda: chat.send_message(messages[-1]["parts"][0]["text"] if messages else "")
        )

        # Update usage (Google doesn't provide detailed usage in same way)
        # Approximation based on text length
        prompt_text = " ".join([m["parts"][0]["text"] for m in messages]) if messages else ""
        completion_text = response.text

        usage = Usage(
            prompt_tokens=len(prompt_text) // 4,
            completion_tokens=len(completion_text) // 4,
            total_tokens=(len(prompt_text) + len(completion_text)) // 4
        )
        self._update_usage(usage)

        return ChatCompletion(
            id=f"gemini_{hash(response.text)}",
            choices=[{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response.text,
                    "tool_calls": None  # Google function calling handled differently
                },
                "finish_reason": "stop"
            }],
            created=int(asyncio.get_event_loop().time()),
            model=kwargs.get("model", "gemini-1.5-pro"),
            usage=usage
        )

    async def _stream_chat(self, model_instance, messages, **kwargs) -> AsyncGenerator[ChatCompletionChunk, None]:
        import asyncio
        loop = asyncio.get_event_loop()

        chat = model_instance.start_chat(history=[])

        # Google's streaming is different - we'll simulate for now
        response = await loop.run_in_executor(
            None, lambda: chat.send_message(messages[-1]["parts"][0]["text"] if messages else "", stream=True)
        )

        # If response is iterable (stream)
        if hasattr(response, '__iter__'):
            for chunk in response:
                                yield ChatCompletionChunk(
                    id=f"gemini_chunk_{hash(str(chunk))}",
                    choices=[{
                        "index": 0,
                        "delta": {
                            "content": chunk.text if hasattr(chunk, 'text') else str(chunk)
                        }
                    }],
                    created=int(time.time()),
                    model=kwargs.get("model", "gemini-1.5-pro")
                )
        else:
            # Non-stream fallback
            yield await self._chat_non_stream(model_instance, messages, **kwargs)

    async def embed(self, text: str, model: str = "models/embedding-001") -> List[float]:
        # Google's embedding API
        result = genai.embed_content(
            model=model,
            content=text,
            task_type="retrieval_document"
        )
        # Update usage approximation
        self._update_usage(Usage(
            prompt_tokens=len(text) // 4,
            total_tokens=len(text) // 4
        ))
        return result['embedding']

    def count_tokens(self, text: str, model: str = "gemini-1.5-pro") -> int:
        # Rough approximation
        return len(text) // 4