"""
Base provider adapter for Colons
Defines the common interface all providers must implement
"""
import abc
import json
from typing import AsyncGenerator, Dict, List, Optional, Union
from dataclasses import dataclass

@dataclass
class Usage:
    """Token usage statistics"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

@dataclass
class Message:
    """Standard message format"""
    role: str  # system, user, assistant, tool
    content: str
    name: Optional[str] = None
    tool_calls: Optional[List[Dict]] = None
    tool_call_id: Optional[str] = None

@dataclass
class ChatCompletionChunk:
    """Streaming chunk response"""
    id: str
    choices: List[Dict]
    created: int
    model: str
    usage: Optional[Usage] = None

@dataclass
class ChatCompletion:
    """Non-streaming response"""
    id: str
    choices: List[Dict]
    created: int
    model: str
    usage: Usage
    object: str = "chat.completion"

class ProviderAdapter(abc.ABC):
    """Base class for all provider adapters"""

    def __init__(self, api_key: str, base_url: Optional[str] = None, **kwargs):
        self.api_key = api_key
        self.base_url = base_url
        self.kwargs = kwargs
        self.usage = Usage()

    @abc.abstractmethod
    async def chat(
        self,
        messages: List[Message],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = True,
        tools: Optional[List[Dict]] = None,
        tool_choice: Union[str, Dict, None] = None,
        **kwargs
    ) -> Union[ChatCompletion, AsyncGenerator[ChatCompletionChunk, None]]:
        """
        Send chat completion request

        Returns:
            Either ChatCompletion (non-stream) or AsyncGenerator of ChatCompletionChunk (stream)
        """
        pass

    @abc.abstractmethod
    async def embed(self, text: str, model: str) -> List[float]:
        """Generate embeddings for text"""
        pass

    @abc.abstractmethod
    def count_tokens(self, text: str, model: str) -> int:
        """Count tokens in text"""
        pass

    def get_usage(self) -> Usage:
        """Get accumulated usage statistics"""
        return self.usage

    def reset_usage(self):
        """Reset usage statistics"""
        self.usage = Usage()

    def _update_usage(self, usage: Usage):
        """Update internal usage counter"""
        self.usage.prompt_tokens += usage.prompt_tokens
        self.usage.completion_tokens += usage.completion_tokens
        self.usage.total_tokens += usage.total_tokens