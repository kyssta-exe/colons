"""Tests for the agent harness - the full agentic loop."""
import json

import pytest

from colons_core.agents import AgentHarness, AgentState, EventType
from colons_core.memory import EmbeddingService
from tests.conftest import ScriptedProvider


def make_agent(provider, store, cache, **kwargs):
    return AgentHarness(
        provider=provider,
        store=store,
        cache=cache,
        embeddings=EmbeddingService(provider=provider, cache=cache, dim=32),
        agent_id="test-agent",
        user_id="tester",
        name="Test",
        **kwargs,
    )


async def collect_events(agent, message, **kwargs):
    events = []
    async for ev in agent.chat(message, **kwargs):
        events.append(ev)
    return events


@pytest.mark.asyncio
async def test_simple_response_streams(provider, store, cache):
    provider.script = [{"content": "Hello there, friend."}]
    agent = make_agent(provider, store, cache)
    events = await collect_events(agent, "hi")

    deltas = [e for e in events if e["type"] == EventType.DELTA.value]
    assert "".join(d["content"] for d in deltas).strip() == "Hello there, friend."

    done = [e for e in events if e["type"] == EventType.DONE.value]
    assert len(done) == 1
    assert done[0]["content"] == "Hello there, friend."
    assert agent.state == AgentState.IDLE


@pytest.mark.asyncio
async def test_tool_call_loop(provider, store, cache):
    provider.script = [
        {"content": "", "tool_calls": [{"id": "c1", "name": "calculate", "arguments": {"expression": "6*7"}}]},
        {"content": "The answer is 42."},
    ]
    agent = make_agent(provider, store, cache, auto_approve_tools=False)
    agent.tool_manager.auto_approve = True  # calculate is AUTO anyway

    events = await collect_events(agent, "what is 6*7?")

    tool_calls = [e for e in events if e["type"] == EventType.TOOL_CALL.value]
    tool_results = [e for e in events if e["type"] == EventType.TOOL_RESULT.value]
    assert tool_calls[0]["tool"] == "calculate"
    assert tool_results[0]["success"] is True
    assert tool_results[0]["output"] == 42

    done = next(e for e in events if e["type"] == EventType.DONE.value)
    assert done["content"] == "The answer is 42."

    # Second provider call must include the tool result in the transcript
    second_call = provider.calls[-1]
    assert any(getattr(m, "role", None) == "tool" or (isinstance(m, dict) and m.get("role") == "tool")
               for m in second_call)


@pytest.mark.asyncio
async def test_usage_events_reported(provider, store, cache):
    provider.script = [{"content": "ok"}]
    agent = make_agent(provider, store, cache)
    events = await collect_events(agent, "hi")
    usage = next(e for e in events if e["type"] == EventType.USAGE.value)
    assert usage["total_tokens"] > 0
    assert usage["iterations"] == 1


@pytest.mark.asyncio
async def test_memory_persisted_across_messages(provider, store, cache):
    provider.script = [{"content": "first reply"}, {"content": "second reply"}]
    agent = make_agent(provider, store, cache)
    await collect_events(agent, "first message")
    await collect_events(agent, "second message")

    history = store.history("tester", agent_id="test-agent")
    contents = [e.content for e in history]
    assert "first message" in contents
    assert "first reply" in contents
    assert "second message" in contents


@pytest.mark.asyncio
async def test_recall_finds_relevant_memory(provider, store, cache):
    provider.script = [{"content": "noted"}]
    agent = make_agent(provider, store, cache)
    await collect_events(agent, "My favorite language is Rust and I live in Berlin")

    results = await agent.recall("favorite language Rust Berlin")
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_sessions_isolated(provider, store, cache):
    provider.script = [{"content": "a"}, {"content": "b"}]
    agent = make_agent(provider, store, cache)
    s1 = agent.create_session("first")
    s2 = agent.create_session("second")
    await collect_events(agent, "hello from one", session_id=s1.id)
    await collect_events(agent, "hello from two", session_id=s2.id)
    assert any(m.content == "hello from one" for m in agent.get_session(s1.id).messages)
    assert not any(m.content == "hello from one" for m in agent.get_session(s2.id).messages)


@pytest.mark.asyncio
async def test_error_event_on_script_exhaustion(provider, store, cache):
    # Script exhausts and returns a fallback message - should still complete
    agent = make_agent(provider, store, cache)
    events = await collect_events(agent, "hi")
    assert any(e["type"] == EventType.DONE.value for e in events)


@pytest.mark.asyncio
async def test_denied_tool_yields_approval_required(provider, store, cache):
    provider.script = [
        {"content": "", "tool_calls": [{"id": "c1", "name": "run_command",
                                        "arguments": {"command": "echo hi"}}]},
        {"content": "I could not run that."},
    ]
    agent = make_agent(provider, store, cache)  # no approval callback -> denied
    events = await collect_events(agent, "run echo")

    approvals = [e for e in events if e["type"] == EventType.APPROVAL_REQUIRED.value]
    results = [e for e in events if e["type"] == EventType.TOOL_RESULT.value]
    assert approvals and approvals[0]["tool"] == "run_command"
    assert results and results[0]["success"] is False


@pytest.mark.asyncio
async def test_task_planning_and_execution(provider, store, cache):
    provider.script = [
        {"content": "1. Check the file\n2. Report findings"},   # planning
        {"content": "Task complete: checked."},                  # execution
    ]
    agent = make_agent(provider, store, cache)
    task = await agent.create_task("Check the file")
    result = await agent.execute_task(task)

    assert task.status == "completed"
    assert len(task.plan) == 2
    assert "checked" in result.lower()


@pytest.mark.asyncio
async def test_agent_status_reports_tools(provider, store, cache):
    agent = make_agent(provider, store, cache)
    status = agent.status()
    assert status["tools"] >= 21
    assert status["provider"] == "scripted"
    assert "usage" in status


@pytest.mark.asyncio
async def test_max_iterations_guard(provider, store, cache):
    # Always returns another tool call -> must stop at max_iterations
    provider.script = [
        {"content": "", "tool_calls": [{"id": f"c{i}", "name": "calculate",
                                        "arguments": {"expression": "1+1"}}]}
        for i in range(10)
    ]
    agent = make_agent(provider, store, cache, max_iterations=3)
    events = await collect_events(agent, "loop please")
    assert any(e["type"] == EventType.ERROR.value and "max iterations" in e.get("message", "").lower()
               for e in events)


@pytest.mark.asyncio
async def test_chat_closed_after_start_cleans_cancellation_state(provider, store, cache):
    agent = make_agent(provider, store, cache)
    stream = agent.chat('hello')
    start = await anext(stream)
    assert start['type'] == 'start'
    await stream.aclose()
    assert not agent._cancel_flags
    assert agent.state == AgentState.IDLE


@pytest.mark.asyncio
async def test_browser_capture_is_forwarded_as_vision_input(provider, store, cache, tmp_path):
    agent = make_agent(provider, store, cache, workspace=str(tmp_path))
    provider.supports_vision = True
    provider.script = [
        {'tool_calls': [{'id': 'capture', 'name': 'browser_screenshot', 'arguments': {}}]},
        {'content': 'The page contains a form.'},
    ]
    path = tmp_path / '.colons' / 'browser' / agent.browser.namespace / 'capture.png'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'\x89PNG\r\n\x1a\nimage')

    async def screenshot():
        return {'path': str(path), 'bytes': path.stat().st_size}

    agent.registry.get('browser_screenshot').func = screenshot
    events = await collect_events(agent, 'Inspect the page visually')
    second_call = provider.calls[-1]
    vision_message = next(m for m in second_call if m.attachments)
    assert vision_message.attachments[0]['data_url'].startswith('data:image/png;base64,')
    assert 'data:image' not in json.dumps(next(e for e in events if e['type'] == 'tool_result'))
    assert events[-1]['content'] == 'The page contains a form.'


@pytest.mark.asyncio
async def test_colons_identity_reaches_provider_with_configured_model(provider, store, cache, monkeypatch):
    monkeypatch.setattr(provider, 'name', 'deepseek')
    provider.script = [{'content': 'identity response'}]
    agent = AgentHarness(provider=provider, store=store, cache=cache,
                         model='deepseek-v4.1-flash')
    await collect_events(agent, 'who are you?', stream=False)
    prompt = provider.calls[-1][0].content
    assert "I'm Colons, your personal AI assistant in Colons." in prompt
    assert 'Configured model: deepseek-v4.1-flash' in prompt
    assert 'Configured provider: deepseek' in prompt
    assert 'the underlying model is its engine, not your assistant name' in prompt


@pytest.mark.asyncio
async def test_identity_updates_after_runtime_provider_and_model_switch(provider, store, cache):
    agent = make_agent(provider, store, cache, model='old-model')
    session = agent.create_session()
    session.add('assistant', 'I am old-model.')
    first = await agent._build_context(session, 'who are you?')
    assert 'Configured model: old-model' in first[0].content
    replacement = ScriptedProvider([])
    replacement.name = 'new-provider'
    agent.provider = replacement
    agent.model = 'new-model'
    next_context = await agent._build_context(session, 'who are you now?')
    assert 'Configured model: new-model' in next_context[0].content
    assert 'Configured provider: new-provider' in next_context[0].content
    assert 'Configured model: old-model' not in next_context[0].content
    assert next_context[-1].content == 'I am old-model.'


@pytest.mark.asyncio
async def test_custom_role_keeps_colons_identity_layer(provider, store, cache):
    agent = make_agent(provider, store, cache,
                       system_prompt='You help draft poems. Keep a gentle tone.')
    session = agent.create_session()
    context = await agent._build_context(session, 'introduce yourself')
    assert 'You help draft poems. Keep a gentle tone.' in context[0].content
    assert 'Your assistant name is Test.' in context[0].content
    assert 'Keep your assigned role and personality.' in context[0].content
