"""The question gate: rules first, one model call only when they do not decide."""

from __future__ import annotations

import httpx
import main
import pytest
from gate import decide, decide_by_rules
from rag import RagResult


@pytest.mark.parametrize(
    ("query", "route", "reason"),
    [
        ("4+4", "direct", "arithmetic"),
        ("4 + 4", "direct", "arithmetic"),
        ("Combien fait 18 fois 2 ?", "direct", "arithmetic"),
        ("18 times 2", "direct", "arithmetic"),
        ("What is two plus two?", "direct", "arithmetic"),
        ("Dis seulement le mot bonjour.", "direct", "greeting"),
        ("hello", "direct", "greeting"),
        ("Does arithmetic_sanity pass?", "code", "code_signal"),
        ("Please build the C++ project with the release preset", "code", "code_signal"),
        ("Que fait la fonction dans AgentClient.cpp ?", "code", "code_signal"),
    ],
)
def test_rules_decide_obvious_questions(query: str, route: str, reason: str) -> None:
    decision = decide_by_rules(query, has_history=False)
    assert decision is not None
    assert decision.route == route
    assert decision.by == "rules"
    assert decision.reason == reason


def test_follow_up_without_a_signal_stays_on_code() -> None:
    decision = decide_by_rules("et celui-là ?", has_history=True)
    assert decision is not None
    assert decision.route == "code"
    assert decision.reason == "conversation"


def test_undecided_first_message_is_left_to_the_model() -> None:
    assert decide_by_rules("explique RAII", has_history=False) is None


@pytest.mark.asyncio
async def test_classify_can_choose_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(*_args, **_kwargs):
        return {"choices": [{"message": {"content": '{"route": "direct"}'}}]}

    monkeypatch.setattr("gate.chat_completion", fake)
    async with httpx.AsyncClient() as client:
        decision = await decide(client, "explique RAII", "req", has_history=False)
    assert decision.route == "direct"
    assert decision.by == "model"


@pytest.mark.asyncio
async def test_classify_failure_stays_on_code(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(*_args, **_kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr("gate.chat_completion", fake)
    async with httpx.AsyncClient() as client:
        decision = await decide(client, "explique RAII", "req", has_history=False)
    assert decision.route == "code"
    assert decision.reason == "default"


@pytest.mark.asyncio
async def test_classify_off_stays_on_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_GATE_CLASSIFY", "0")

    async def fake(*_args, **_kwargs):
        raise AssertionError("classifier must not run")

    monkeypatch.setattr("gate.chat_completion", fake)
    async with httpx.AsyncClient() as client:
        decision = await decide(client, "explique RAII", "req", has_history=False)
    assert decision.route == "code"
    assert decision.by == "rules"


@pytest.mark.asyncio
async def test_direct_turn_skips_retrieval_and_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    async def retrieve(*_args, **_kwargs):
        raise AssertionError("retrieve_context must not run")

    seen: dict[str, object] = {}

    async def loop(*args, **kwargs):
        seen["system"] = args[1]
        seen["user"] = args[2]
        seen["enable_tools"] = kwargs["enable_tools"]
        return {"answer": "8", "tool_calls": []}

    monkeypatch.setattr(main, "retrieve_context", retrieve)
    monkeypatch.setattr(main, "run_tool_loop", loop)
    events: list[tuple[str, dict]] = []

    async def on_event(name: str, data: dict) -> None:
        events.append((name, data))

    async with httpx.AsyncClient() as client:
        response = await main.execute_turn(
            main.ChatRequest(query="4+4"),
            "req",
            client,
            on_event=on_event,
        )
    assert response.route == "direct"
    assert response.answer == "8"
    assert response.used_context is False
    assert response.sources == []
    assert response.tool_calls == []
    assert seen["enable_tools"] is False
    assert seen["user"] == "4+4"
    assert "Do not call tools" in str(seen["system"])
    assert events == [("route", {"route": "direct", "by": "rules"})]


@pytest.mark.asyncio
async def test_code_turn_retrieves_and_enables_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    async def retrieve(*_args, **_kwargs):
        return RagResult(
            used_context=True,
            context_text="chunk",
            sources=[{"file_path": "a.cpp", "start_line": 1, "end_line": 2, "symbol_name": "f"}],
        )

    seen: dict[str, object] = {}

    async def loop(*args, **kwargs):
        seen["user"] = args[2]
        seen["enable_tools"] = kwargs["enable_tools"]
        return {"answer": "yes", "tool_calls": []}

    monkeypatch.setattr(main, "retrieve_context", retrieve)
    monkeypatch.setattr(main, "run_tool_loop", loop)
    events: list[str] = []

    async def on_event(name: str, _data: dict) -> None:
        events.append(name)

    async with httpx.AsyncClient() as client:
        response = await main.execute_turn(
            main.ChatRequest(query="Does arithmetic_sanity pass?"),
            "req",
            client,
            on_event=on_event,
        )
    assert response.route == "code"
    assert response.used_context is True
    assert seen["enable_tools"] is True
    assert "Retrieved context:" in str(seen["user"])
    assert events == ["route", "rag"]


@pytest.mark.asyncio
async def test_tools_false_keeps_retrieval_and_skips_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    async def retrieve(*_args, **_kwargs):
        return RagResult(used_context=True, context_text="chunk", sources=[])

    seen: dict[str, object] = {}

    async def loop(*_args, **kwargs):
        seen["enable_tools"] = kwargs["enable_tools"]
        return {"answer": "cited", "tool_calls": []}

    monkeypatch.setattr(main, "retrieve_context", retrieve)
    monkeypatch.setattr(main, "run_tool_loop", loop)
    async with httpx.AsyncClient() as client:
        response = await main.execute_turn(
            main.ChatRequest(query="Does arithmetic_sanity pass?", tools=False),
            "req",
            client,
        )
    assert response.route == "code"
    assert seen["enable_tools"] is False


@pytest.mark.asyncio
async def test_disabled_tools_are_not_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    import tool_loop

    async def complete(*_args, **kwargs):
        assert kwargs["tools"] is None
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "8",
                        "tool_calls": [{"id": "c", "function": {"name": "test", "arguments": "{}"}}],
                    }
                }
            ]
        }

    async def execute(*_args, **_kwargs):
        raise AssertionError("tool must not run")

    async def passthrough(_http_client, messages, _request_id):
        return messages, 1, {"used": 1, "window": 12288, "ratio": 0.0, "compressed": False}

    monkeypatch.setattr(tool_loop, "_complete", complete)
    monkeypatch.setattr(tool_loop, "execute_tool_call", execute)
    monkeypatch.setattr(tool_loop, "apply_budget", passthrough)
    async with httpx.AsyncClient() as client:
        result = await tool_loop.run_tool_loop(client, "sys", "4+4", "req", enable_tools=False)
    assert result == {"answer": "8", "tool_calls": []}
