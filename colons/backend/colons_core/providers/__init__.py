"""
Provider factory and registry
Huge provider catalog - most reuse the OpenAI-compatible adapter with a base_url.
"""
from typing import Dict, List, Optional, Type

from .anthropic import AnthropicAdapter
from .base import ProviderAdapter, ProviderError
from .google import GeminiAdapter
from .ollama import OllamaAdapter
from .openai_compat import OpenAIAdapter

# --------------------------------------------------------------------------- #
# Provider catalog
# --------------------------------------------------------------------------- #
# kind: openai_compat | anthropic | google | ollama
# env: environment variables that can supply the key / base url
PROVIDER_CATALOG: Dict[str, Dict] = {
    # ---- First-party ---
    "openai": {
        "kind": "openai_compat",
        "base_url": "https://api.openai.com/v1",
        "env": ["OPENAI_API_KEY"],
        "default_model": "gpt-4o",
        "display": "OpenAI",
    },
    "anthropic": {
        "kind": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "env": ["ANTHROPIC_API_KEY"],
        "default_model": "claude-3-5-sonnet-latest",
        "display": "Anthropic",
    },
    "google": {
        "kind": "google",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "default_model": "gemini-1.5-pro",
        "display": "Google Gemini",
    },
    "azure-openai": {
        "kind": "openai_compat",
        "base_url": "",  # customer-specific deployment endpoint
        "env": ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"],
        "default_model": "gpt-4o",
        "display": "Azure OpenAI",
    },
    # ---- Aggregators ---
    "openrouter": {
        "kind": "openai_compat",
        "base_url": "https://openrouter.ai/api/v1",
        "env": ["OPENROUTER_API_KEY"],
        "default_model": "openai/gpt-4o",
        "display": "OpenRouter",
    },
    "together": {
        "kind": "openai_compat",
        "base_url": "https://api.together.xyz/v1",
        "env": ["TOGETHER_API_KEY"],
        "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "display": "Together AI",
    },
    "groq": {
        "kind": "openai_compat",
        "base_url": "https://api.groq.com/openai/v1",
        "env": ["GROQ_API_KEY"],
        "default_model": "llama-3.3-70b-versatile",
        "display": "Groq",
    },
    "fireworks": {
        "kind": "openai_compat",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "env": ["FIREWORKS_API_KEY"],
        "default_model": "accounts/fireworks/models/llama-v3p3-70b-instruct",
        "display": "Fireworks AI",
    },
    "deepseek": {
        "kind": "openai_compat",
        "base_url": "https://api.deepseek.com/v1",
        "env": ["DEEPSEEK_API_KEY"],
        "default_model": "deepseek-chat",
        "display": "DeepSeek",
    },
    "xai": {
        "kind": "openai_compat",
        "base_url": "https://api.x.ai/v1",
        "env": ["XAI_API_KEY"],
        "default_model": "grok-2-latest",
        "display": "xAI",
    },
    "mistral": {
        "kind": "openai_compat",
        "base_url": "https://api.mistral.ai/v1",
        "env": ["MISTRAL_API_KEY"],
        "default_model": "mistral-large-latest",
        "display": "Mistral",
    },
    "perplexity": {
        "kind": "openai_compat",
        "base_url": "https://api.perplexity.ai",
        "env": ["PERPLEXITY_API_KEY"],
        "default_model": "sonar-pro",
        "display": "Perplexity",
    },
    "cerebras": {
        "kind": "openai_compat",
        "base_url": "https://api.cerebras.ai/v1",
        "env": ["CEREBRAS_API_KEY"],
        "default_model": "llama-3.3-70b",
        "display": "Cerebras",
    },
    "sambanova": {
        "kind": "openai_compat",
        "base_url": "https://api.sambanova.ai/v1",
        "env": ["SAMBANOVA_API_KEY"],
        "default_model": "Meta-Llama-3.3-70B-Instruct",
        "display": "SambaNova",
    },
    "nvidia": {
        "kind": "openai_compat",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "env": ["NVIDIA_API_KEY"],
        "default_model": "meta/llama-3.3-70b-instruct",
        "display": "NVIDIA NIM",
    },
    "github-models": {
        "kind": "openai_compat",
        "base_url": "https://models.inference.ai.azure.com",
        "env": ["GITHUB_TOKEN"],
        "default_model": "gpt-4o",
        "display": "GitHub Models",
    },
    "ollama-cloud": {
        "kind": "openai_compat",
        "base_url": "https://api.ollama.com/v1",
        "env": ["OLLAMA_API_KEY"],
        "default_model": "llama3.3",
        "display": "Ollama Cloud",
    },
    # ---- Local runtimes ---
    "ollama": {
        "kind": "ollama",
        "base_url": "http://localhost:11434",
        "env": [],
        "default_model": "llama3.1",
        "display": "Ollama (local)",
    },
    "lmstudio": {
        "kind": "openai_compat",
        "base_url": "http://localhost:1234/v1",
        "env": [],
        "default_model": "local-model",
        "display": "LM Studio",
    },
    "vllm": {
        "kind": "openai_compat",
        "base_url": "http://localhost:8000/v1",
        "env": ["VLLM_API_KEY"],
        "default_model": "local-model",
        "display": "vLLM",
    },
    "llamacpp": {
        "kind": "openai_compat",
        "base_url": "http://localhost:8080/v1",
        "env": [],
        "default_model": "local-model",
        "display": "llama.cpp server",
    },
    "textgen": {
        "kind": "openai_compat",
        "base_url": "http://localhost:5000/v1",
        "env": [],
        "default_model": "local-model",
        "display": "text-generation-webui",
    },
    "localai": {
        "kind": "openai_compat",
        "base_url": "http://localhost:8080/v1",
        "env": ["LOCALAI_API_KEY"],
        "default_model": "local-model",
        "display": "LocalAI",
    },
    "jan": {
        "kind": "openai_compat",
        "base_url": "http://localhost:1337/v1",
        "env": [],
        "default_model": "local-model",
        "display": "Jan",
    },
    "koboldcpp": {
        "kind": "openai_compat",
        "base_url": "http://localhost:5001/v1",
        "env": [],
        "default_model": "local-model",
        "display": "KoboldCpp",
    },
    # ---- Generic escape hatch ---
    "custom": {
        "kind": "openai_compat",
        "base_url": "",
        "env": ["CUSTOM_API_KEY", "CUSTOM_BASE_URL"],
        "default_model": "",
        "display": "Custom OpenAI-compatible endpoint",
    },
}


_KIND_MAP: Dict[str, Type[ProviderAdapter]] = {
    "openai_compat": OpenAIAdapter,
    "anthropic": AnthropicAdapter,
    "google": GeminiAdapter,
    "ollama": OllamaAdapter,
}

# Aliases
PROVIDER_CATALOG["claude"] = {**PROVIDER_CATALOG["anthropic"], "display": "Claude (alias)"}
PROVIDER_CATALOG["gemini"] = {**PROVIDER_CATALOG["google"], "display": "Gemini (alias)"}
PROVIDER_CATALOG["local"] = {**PROVIDER_CATALOG["ollama"], "display": "Local (alias)"}


class ProviderFactory:
    """Create and cache provider adapter instances."""

    _registry: Dict[str, Type[ProviderAdapter]] = dict(_KIND_MAP)
    _instances: Dict[str, ProviderAdapter] = {}

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #

    @classmethod
    def register_kind(cls, kind: str, adapter_class: Type[ProviderAdapter]):
        cls._registry[kind.lower()] = adapter_class

    @classmethod
    def register_provider(cls, name: str, config: Dict):
        PROVIDER_CATALOG[name.lower()] = config

    # ------------------------------------------------------------------ #
    # Creation
    # ------------------------------------------------------------------ #

    @classmethod
    def create(
        cls,
        provider: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        cache: bool = False,
        **kwargs,
    ) -> ProviderAdapter:
        provider = (provider or "openai").lower()
        cfg = PROVIDER_CATALOG.get(provider)
        if cfg is None:
            # Unknown name: treat as a custom OpenAI-compatible endpoint
            cfg = {
                "kind": "openai_compat",
                "base_url": base_url or "",
                "default_model": kwargs.pop("model", ""),
                "display": provider,
            }

        kind = cfg.get("kind", "openai_compat")
        adapter_class = cls._registry.get(kind)
        if adapter_class is None:
            raise ProviderError(f"Unknown provider kind: {kind}", provider)

        effective_base = base_url or cfg.get("base_url") or None
        effective_key = api_key if api_key is not None else cls._env_key(cfg)

        if cache:
            cache_key = f"{provider}:{effective_base}:{effective_key[:6] if effective_key else ''}"
            if cache_key in cls._instances:
                return cls._instances[cache_key]
            instance = adapter_class(api_key=effective_key or "", base_url=effective_base, **kwargs)
            cls._instances[cache_key] = instance
            return instance

        return adapter_class(api_key=effective_key or "", base_url=effective_base, **kwargs)

    @staticmethod
    def _env_key(cfg: Dict) -> Optional[str]:
        import os
        for env in cfg.get("env", []):
            val = os.environ.get(env)
            if val:
                return val
        return None

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    @classmethod
    def list_providers(cls) -> List[Dict]:
        out = []
        for name, cfg in sorted(PROVIDER_CATALOG.items()):
            out.append({
                "name": name,
                "display": cfg.get("display", name),
                "kind": cfg.get("kind", "openai_compat"),
                "base_url": cfg.get("base_url", ""),
                "default_model": cfg.get("default_model", ""),
                "env": cfg.get("env", []),
                "configured": bool(cls._env_key(cfg)) or cfg.get("kind") in ("ollama",),
                "local": cfg.get("kind") in ("ollama",) or "localhost" in (cfg.get("base_url") or ""),
            })
        return out

    @classmethod
    def default_model(cls, provider: str) -> str:
        cfg = PROVIDER_CATALOG.get((provider or "").lower(), {})
        return cfg.get("default_model", "")

    @classmethod
    def clear_cache(cls):
        cls._instances.clear()


__all__ = [
    "ProviderFactory",
    "PROVIDER_CATALOG",
    "ProviderAdapter",
    "ProviderError",
    "OpenAIAdapter",
    "AnthropicAdapter",
    "GeminiAdapter",
    "OllamaAdapter",
]
