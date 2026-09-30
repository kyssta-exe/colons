"""
Provider factory for Colons
Creates and manages provider adapters
"""
from typing import Optional, Dict
from .base import ProviderAdapter
from .openai import OpenAIAdapter
from .anthropic import AnthropicAdapter
from .google import GoogleAdapter
from .ollama import OllamaAdapter

class ProviderFactory:
    """Factory for creating provider adapters"""

    _providers = {
        "openai": OpenAIAdapter,
        "anthropic": AnthropicAdapter,
        "google": GoogleAdapter,
        "ollama": OllamaAdapter,
        "local": OllamaAdapter,  # Alias for Ollama
    }

    @classmethod
    def register(cls, name: str, adapter_class):
        """Register a new provider adapter"""
        cls._providers[name.lower()] = adapter_class

    @classmethod
    def create(cls, provider: str, api_key: str, base_url: Optional[str] = None, **kwargs) -> ProviderAdapter:
        """
        Create a provider adapter instance

        Args:
            provider: Provider name (openai, anthropic, google, ollama)
            api_key: API key for the provider
            base_url: Optional base URL (for custom endpoints)
            **kwargs: Additional provider-specific options

        Returns:
            ProviderAdapter instance
        """
        provider = provider.lower()

        if provider not in cls._providers:
            available = ", ".join(cls._providers.keys())
            raise ValueError(f"Unknown provider: {provider}. Available: {available}")

        adapter_class = cls._providers[provider]
        return adapter_class(api_key=api_key, base_url=base_url, **kwargs)

    @classmethod
    def list_providers(cls) -> Dict[str, str]:
        """List all registered providers"""
        return {
            name: adapter.__name__
            for name, adapter in cls._providers.items()
        }