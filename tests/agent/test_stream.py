"""SSE assembly and /chat/stream wiring."""

from __future__ import annotations

from types import SimpleNamespace

import llm_client
import main
import pytest
from fastapi.testclient import TestClient


def test_assemble_stream_message_joins_tokens_and_tool_calls() -> None:
    deltas = [
        SimpleNamespace(content="Hel", tool_calls=None),
        SimpleNamespace(content="lo", tool_calls=None),
        SimpleNamespace(
            content=None,
            tool_calls=[
                SimpleNamespace(
                    index=0,
                    id="call_1",
                    function=SimpleNamespace(name="bu", arguments=""),
                )
            ],
        ),
        SimpleNamespace(
            content=None,
            tool_calls=[
                SimpleNamespace(
                    index=0,
                    id=None,
                    function=SimpleNamespace(name="ild", arguments='{"p'),
                )
            ],
        ),
        SimpleNamespace(
            content=None,
            tool_calls=[
                SimpleNamespace(
                    index=0,
                    id=None,
                    function=SimpleNamespace(name=None, arguments='reset":"release"}'),
                )
            ],
        ),
    ]
    message = llm_client.assemble_stream_message(deltas)
    assert message["content"] == "Hello"
    assert message["tool_calls"][0]["function"]["name"] == "build"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"preset":"release"}'
    slots: dict[int, dict[str, str]] = {}
    snapshots = []
    for delta in deltas:
        snapshots.extend(llm_client.tool_delta_snapshot(delta, slots))
    assert [item["name"] for item in snapshots] == ["bu", "build", "build"]
    assert snapshots[-1]["arguments"] == '{"preset":"release"}'


@pytest.mark.asyncio
async def test_tool_loop_emits_tool_events(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict]] = []

    async def capture(name: str, payload: dict) -> None:
        events.append((name, payload))

    async def fake_complete(*_a, **_k):
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {"name": "build", "arguments": '{"preset":"release"}'},
                            }
                        ],
                    }
                }
            ]
        }

    calls = {"n": 0}

    async def complete_then_answer(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return await fake_complete()
        return {"choices": [{"message": {"role": "assistant", "content": "ok", "tool_calls": []}}]}

    async def fake_exec(*_a, **_k):
        return {"ok": True, "stdout": "built", "stderr": "", "exit_code": 0}

    import tool_loop

    async def passthrough(_http_client, messages, _request_id):
        return messages, 1, {"used": 1, "window": 12288, "ratio": 0.0, "compressed": False}

    monkeypatch.setattr(tool_loop, "_complete", complete_then_answer)
    monkeypatch.setattr(tool_loop, "execute_tool_call", fake_exec)
    monkeypatch.setattr(tool_loop, "apply_budget", passthrough)

    async with __import__("httpx").AsyncClient() as client:
        result = await tool_loop.run_tool_loop(client, "sys", "user", "r", on_event=capture)

    assert result["answer"] == "ok"
    names = [e[0] for e in events if e[0] != "context"]
    assert names == ["tool_call", "tool_result"]
    tool_call = next(payload for name, payload in events if name == "tool_call")
    assert tool_call["name"] == "build"


def test_chat_stream_sse_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BEARER_TOKEN", "secret-token")

    async def fake_turn(*_a, **kwargs):
        on_event = kwargs.get("on_event")
        if on_event is not None:
            await on_event("rag", {"used_context": False, "num_sources": 0, "sources": []})
            await on_event("token", {"text": "Hi"})
        return main.ChatResponse(
            answer="Hi",
            sources=[],
            used_context=False,
            tool_calls=[],
            conversation_id="conv-1",
        )

    monkeypatch.setattr(main, "execute_turn", fake_turn)

    with TestClient(main.app) as client:
        resp = client.post(
            "/chat/stream",
            json={"query": "hello"},
            headers={"Authorization": "Bearer secret-token"},
        )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    assert "event: rag" in resp.text
    assert "event: token" in resp.text
    assert "event: done" in resp.text
    assert "conv-1" in resp.text


def test_chat_returns_conversation_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BEARER_TOKEN", "secret-token")

    async def fake_turn(*_a, **_k):
        return main.ChatResponse(
            answer="ok",
            sources=[],
            used_context=False,
            tool_calls=[],
            conversation_id="abc",
        )

    monkeypatch.setattr(main, "execute_turn", fake_turn)
    with TestClient(main.app) as client:
        resp = client.post(
            "/chat",
            json={"query": "hello", "conversation_id": "abc"},
            headers={"Authorization": "Bearer secret-token"},
        )
    assert resp.status_code == 200
    assert resp.json()["conversation_id"] == "abc"
