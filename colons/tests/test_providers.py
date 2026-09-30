"""Tests for the provider layer."""
import json
import pytest

from colons_core.providers import ProviderFactory, PROVIDER_CATALOG
from colons_core.providers.base import Message, ProviderError
from colons_core.providers.openai_compat import OpenAIAdapter
from colons_core.providers.anthropic import AnthropicAdapter
from colons_core.providers.google import GeminiAdapter
from colons_core.providers.ollama import OllamaAdapter


def test_catalog_has_many_providers():
    assert len(PROVIDER_CATALOG) >= 20
    for required in ["openai", "anthropic", "google", "ollama", "openrouter",
                     "groq", "together", "deepseek", "xai", "mistral", "lmstudio", "custom"]:
        assert required in PROVIDER_CATALOG


def test_factory_creates_correct_adapter_kinds():
    assert isinstance(ProviderFactory.create("openai", api_key="x"), OpenAIAdapter)
    assert isinstance(ProviderFactory.create("anthropic", api_key="x"), AnthropicAdapter)
    assert isinstance(ProviderFactory.create("google", api_key="x"), GeminiAdapter)
    assert isinstance(ProviderFactory.create("ollama"), OllamaAdapter)
    # Aggregators reuse the OpenAI-compatible adapter
    assert isinstance(ProviderFactory.create("groq", api_key="x"), OpenAIAdapter)
    assert isinstance(ProviderFactory.create("openrouter", api_key="x"), OpenAIAdapter)


def test_factory_unknown_provider_falls_back_to_custom_endpoint():
    adapter = ProviderFactory.create("some-new-service", base_url="http://example.com/v1", api_key="k")
    assert isinstance(adapter, OpenAIAdapter)
    assert adapter.base_url == "http://example.com/v1"


def test_list_providers_shape():
    providers = ProviderFactory.list_providers()
    assert all({"name", "display", "kind", "default_model"} <= set(p) for p in providers)
    ollama = next(p for p in providers if p["name"] == "ollama")
    assert ollama["local"] is True


def test_message_normalization():
    msgs = [
        Message(role="user", content="hi"),
        {"role": "assistant", "content": "hello", "tool_calls": [{"x": 1}]},
    ]
    norm = OpenAIAdapter.normalize_messages(msgs)
    assert norm[0] == {"role": "user", "content": "hi"}
    assert norm[1]["tool_calls"] == [{"x": 1}]


def test_usage_accumulation():
    adapter = OpenAIAdapter(api_key="k")
    from colons_core.providers.base import Usage
    adapter.record_usage(Usage(prompt_tokens=5, completion_tokens=3, total_tokens=8))
    adapter.record_usage(Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2))
    u = adapter.get_usage()
    assert u.prompt_tokens == 6
    assert u.total_tokens == 10
    adapter.reset_usage()
    assert adapter.get_usage().total_tokens == 0


def test_anthropic_message_translation():
    adapter = AnthropicAdapter(api_key="k")
    system, converted = adapter._split_system([
        Message(role="system", content="be nice"),
        Message(role="user", content="hello"),
        Message(role="assistant", content="", tool_calls=[
            {"id": "t1", "type": "function", "function": {"name": "calc", "arguments": '{"x":1}'}}
        ]),
        Message(role="tool", content="42", tool_call_id="t1"),
    ])
    assert system == "be nice"
    assert converted[0]["role"] == "user"
    assert converted[1]["content"][0]["type"] == "tool_use"
    assert converted[2]["content"][0]["type"] == "tool_result"


def test_anthropic_tool_conversion():
    tools = [{"type": "function", "function": {
        "name": "calc", "description": "math", "parameters": {"type": "object", "properties": {}},
    }}]
    converted = AnthropicAdapter._convert_tools(tools)
    assert converted[0]["name"] == "calc"
    assert "input_schema" in converted[0]


def test_gemini_message_translation():
    adapter = GeminiAdapter(api_key="k")
    system, contents = adapter._to_gemini([
        Message(role="system", content="sys"),
        Message(role="user", content="hi"),
        Message(role="assistant", content="yo"),
    ])
    assert system == "sys"
    assert contents[0]["role"] == "user"
    assert contents[1]["role"] == "model"


def test_token_counting_fallback():
    adapter = OpenAIAdapter(api_key="k")
    assert adapter.count_tokens("hello world this is a test", "unknown-model") > 0


def test_stream_options_flag_controls_payload():
    adapter = OpenAIAdapter(api_key="k")
    payload = adapter._build_payload(
        [Message(role="user", content="hi")], "m", 0.7, None, True, None, None,
    )
    assert payload["stream_options"] == {"include_usage": True}

    adapter_no = OpenAIAdapter(api_key="k", supports_stream_options=False)
    payload2 = adapter_no._build_payload(
        [Message(role="user", content="hi")], "m", 0.7, None, True, None, None,
    )
    assert "stream_options" not in payload2


def test_stream_options_rejection_detection():
    assert OpenAIAdapter._is_stream_options_rejection(400, '{"error":"unknown field stream_options"}')
    assert OpenAIAdapter._is_stream_options_rejection(400, "Invalid parameter: stream_options")
    assert not OpenAIAdapter._is_stream_options_rejection(400, "invalid model")
    assert not OpenAIAdapter._is_stream_options_rejection(500, "stream_options exploded")


@pytest.mark.asyncio
async def test_transcribe_not_supported_by_default():
    from colons_core.providers.base import ProviderAdapter

    class Minimal(ProviderAdapter):
        name = "minimal"

        async def chat(self, *a, **k):
            raise NotImplementedError

        def count_tokens(self, text, model):
            return 0

    with pytest.raises(NotImplementedError):
        await Minimal().transcribe(b"audio")


def test_provider_capability_flags():
    assert OpenAIAdapter.supports_transcription is True
    from colons_core.providers.anthropic import AnthropicAdapter as A
    from colons_core.providers.google import GeminiAdapter as G
    from colons_core.providers.ollama import OllamaAdapter as O
    assert A.supports_transcription is False
    assert G.supports_transcription is False
    assert O.supports_transcription is False
