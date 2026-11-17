"""find_symbol is a named tool on the sidecar, beside tools and patch.

/ui and the CLI share the one schema this loop sends. The tests inject a
fake sidecar at the dispatch and never speak MCP.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import httpx
import pytest
import tool_loop

REQUEST_ID = "req-find-symbol"
SYMBOL_TEXT = "Widget declared in src/widget.hpp:4"

_FIND_SYMBOL_ARGUMENTS = {
    "name_path_pattern",
    "depth",
    "relative_path",
    "include_body",
    "include_info",
    "include_kinds",
    "exclude_kinds",
    "substring_matching",
    "max_answer_chars",
}


def _assistant_tool_call_message(calls: list[tuple[str, str, str]]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}
            for call_id, name, args in calls
        ],
    }


def _assistant_final_message(content: str) -> dict[str, Any]:
    return {"role": "assistant", "content": content, "tool_calls": []}


def _completion(message: dict[str, Any]) -> dict[str, Any]:
    return {"choices": [{"message": message}]}


class ScriptedTransport:
    """LLM completions plus the tools and patch HTTP the loop already uses."""

    def __init__(
        self,
        llm_completions: list[dict[str, Any]],
        tool_responses: Optional[dict[str, dict[str, Any]]] = None,
    ) -> None:
        self.llm_completions = llm_completions
        self.tool_responses = tool_responses or {}
        self.llm_call_count = 0
        self.llm_payloads: list[dict[str, Any]] = []
        self.destinations: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/v1/chat/completions"):
            raw = request.content or b""
            if isinstance(raw, str):
                raw = raw.encode()
            self.llm_payloads.append(json.loads(raw.decode()) if raw else {})
            idx = min(self.llm_call_count, len(self.llm_completions) - 1)
            self.llm_call_count += 1
            return httpx.Response(200, json=self.llm_completions[idx])
        if path.endswith("/tokenize"):
            return httpx.Response(200, json={"tokens": [1]})
        self.destinations.append(f"{request.url.scheme}://{request.url.host}:{request.url.port}{path}")
        body = self.tool_responses.get(path, {"ok": True, "stdout": "", "stderr": "", "exit_code": 0})
        return httpx.Response(200, json=body)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _offered_tools(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return list(payload["tools"])


def _listen() -> tuple[list[tuple[str, dict[str, Any]]], Any]:
    events: list[tuple[str, dict[str, Any]]] = []

    async def on_event(name: str, payload: dict[str, Any]) -> None:
        events.append((name, payload))

    return events, on_event


class RecordingSidecar:
    """The third destination. Records each call and returns one fixed result."""

    def __init__(self, result: Optional[dict[str, Any]] = None, error: Optional[BaseException] = None) -> None:
        self.result = result or {"ok": True, "stdout": SYMBOL_TEXT, "stderr": "", "exit_code": 0}
        self.error = error
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    async def call(self, name: str, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        self.calls.append((name, arguments, request_id))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_find_symbol_is_offered_under_serenas_name() -> None:
    """The page and the CLI share this one schema. The name is Serena's."""
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_final_message("No symbol yet.")),
            _completion(_assistant_final_message("Still no symbol.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Where is Widget?", REQUEST_ID)

    assert result["answer"] == "Still no symbol."
    offered = _offered_tools(scripted.llm_payloads[0])
    names = [tool["function"]["name"] for tool in offered]
    assert "build" in names
    assert names.count("find_symbol") == 1
    assert "serena_find_symbol" not in names
    spec = next(tool["function"] for tool in offered if tool["function"]["name"] == "find_symbol")
    assert spec["parameters"]["required"] == ["name_path_pattern"]
    assert set(spec["parameters"]["properties"]) == _FIND_SYMBOL_ARGUMENTS
    for prop in spec["parameters"]["properties"].values():
        assert "description" in prop
        assert "title" not in prop
    assert scripted.destinations == []


@pytest.mark.asyncio
async def test_find_symbol_runs_on_the_sidecar_and_streams_its_text(monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    events, on_event = _listen()
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "find_symbol", '{"name_path_pattern": "Widget"}')])),
            _completion(_assistant_final_message("Widget is declared in the header.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "Where is Widget?", REQUEST_ID, on_event=on_event
        )

    assert sidecar.calls == [("find_symbol", {"name_path_pattern": "Widget"}, REQUEST_ID)]
    assert scripted.destinations == []
    assert result["tool_calls"] == [
        {
            "name": "find_symbol",
            "arguments": {"name_path_pattern": "Widget"},
            "result_summary": "find_symbol succeeded: Widget declared in src/widget.hpp:4",
            "ok": True,
            "line": "Widget declared in src/widget.hpp:4",
        }
    ]
    assert ("tool_call", {"name": "find_symbol", "arguments": {"name_path_pattern": "Widget"}}) in events
    assert (
        "tool_result",
        {
            "name": "find_symbol",
            "result_summary": "find_symbol succeeded: Widget declared in src/widget.hpp:4",
            "ok": True,
        },
    ) in events
    assert result["answer"] == "Widget is declared in the header."


@pytest.mark.asyncio
async def test_build_still_runs_on_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "build", '{"preset": "release"}')])),
            _completion(_assistant_final_message("It builds.")),
        ],
        tool_responses={"/build": {"ok": True, "stdout": "build succeeded", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Does it build?", REQUEST_ID)

    assert result["tool_calls"][0]["name"] == "build"
    assert result["tool_calls"][0]["ok"] is True
    assert scripted.destinations == ["http://tools-test:8091/build"]
    assert sidecar.calls == []


@pytest.mark.asyncio
async def test_unreachable_sidecar_fails_the_named_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LOOP_NUDGES", "1")
    monkeypatch.setattr(tool_loop, "sidecar", RecordingSidecar(error=ConnectionError("connection refused")))
    events, on_event = _listen()
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "find_symbol", '{"name_path_pattern": "Widget"}')])),
            _completion(_assistant_final_message("too early")),
            _completion(_assistant_final_message("The sidecar is down.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "Where is Widget?", REQUEST_ID, on_event=on_event
        )

    assert result["answer"] == "The sidecar is down."
    assert result["tool_calls"][0]["ok"] is False
    assert result["tool_calls"][0]["line"] == "connection refused"
    assert scripted.destinations == []
    assert [payload for name, payload in events if name == "nudge"] == [
        {"reason": "tool_failed", "tool": "find_symbol", "remaining": 0}
    ]


@pytest.mark.asyncio
async def test_cancel_stops_the_sidecar_call_and_stores_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    import main

    started = asyncio.Event()
    stopped = asyncio.Event()

    class BlockingSidecar:
        async def call(self, name: str, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
            assert (name, arguments, request_id) == ("find_symbol", {"name_path_pattern": "Widget"}, "req-find-symbol")
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                stopped.set()
                raise
            return {"ok": True, "stdout": SYMBOL_TEXT, "stderr": "", "exit_code": 0}

    monkeypatch.setattr(tool_loop, "sidecar", BlockingSidecar())

    class CancelClient:
        def __init__(self) -> None:
            self.posts: list[dict[str, object]] = []

        async def post(self, url: str, headers: dict[str, str] | None = None, timeout: float | None = None, **_kwargs):
            self.posts.append({"url": url, "headers": headers or {}, "timeout": timeout})
            return httpx.Response(204)

    client = CancelClient()

    async def loop(_client, *_args, **kwargs):
        flag = kwargs["cancel_event"]

        async def trip() -> None:
            await started.wait()
            flag.set()

        asyncio.create_task(trip())
        await tool_loop.execute_tool_call(_client, "find_symbol", {"name_path_pattern": "Widget"}, "req-find-symbol")

    monkeypatch.setattr(main, "run_tool_loop", loop)
    response = await asyncio.wait_for(
        main.execute_turn(
            main.ChatRequest(query="hello", conversation_id="find-symbol-cancel"),
            "req-find-symbol",
            client,  # type: ignore[arg-type]
            cancel_event=asyncio.Event(),
        ),
        timeout=3,
    )
    await asyncio.wait_for(stopped.wait(), timeout=1)
    assert response.answer == "Stopped."
    history = main.STORE.history("find-symbol-cancel")
    assert history[-1] == {"role": "assistant", "content": "Stopped."}
    urls = {post["url"] for post in client.posts}
    assert urls == {f"{main.TOOLS_BASE_URL.rstrip('/')}/cancel"}
    main.STORE._items.pop("find-symbol-cancel", None)  # noqa: SLF001


@pytest.mark.asyncio
async def test_repeated_find_symbol_is_replayed(monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "find_symbol", '{"name_path_pattern": "Widget"}')])),
            _completion(_assistant_tool_call_message([("call_2", "find_symbol", '{"name_path_pattern": "Widget"}')])),
            _completion(_assistant_final_message("Same declaration.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Where is Widget?", REQUEST_ID)

    assert sidecar.calls == [("find_symbol", {"name_path_pattern": "Widget"}, REQUEST_ID)]
    assert scripted.destinations == []
    assert [call["ok"] for call in result["tool_calls"]] == [True, True]
    replayed = [
        message["content"]
        for message in scripted.llm_payloads[2]["messages"]
        if message.get("role") == "tool"
        and str(message.get("content", "")).startswith("Same arguments as an earlier call. The result is unchanged.")
    ]
    assert len(replayed) == 1
    assert "Widget declared in src/widget.hpp:4" in replayed[0]


@pytest.mark.parametrize(
    "name",
    ["execute_shell_command", "serena_repl", "jet_brains_find_symbol"],
)
@pytest.mark.asyncio
async def test_disabled_serena_name_rejects_the_batch(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(
                _assistant_tool_call_message(
                    [
                        ("bad", name, "{}"),
                        ("good", "find_symbol", '{"name_path_pattern": "Widget"}'),
                    ]
                )
            ),
            _completion(_assistant_final_message("not yet")),
            _completion(_assistant_final_message("Nothing ran.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Where is Widget?", REQUEST_ID, max_iterations=4)

    offered = [tool["function"]["name"] for tool in _offered_tools(scripted.llm_payloads[0])]
    assert name not in offered
    assert not any(offered_name.startswith("jet_brains") for offered_name in offered)
    assert sidecar.calls == []
    assert scripted.destinations == []
    assert result["tool_calls"] == []
    assert result["answer"] == "Nothing ran."
    note = next(
        message["content"]
        for message in scripted.llm_payloads[1]["messages"]
        if message.get("role") == "system" and "unknown function" in (message.get("content") or "")
    )
    assert f"unknown function {name!r}" in note
    assert "Valid calls not executed: find_symbol" in note


@pytest.mark.asyncio
async def test_bad_find_symbol_arguments_reject_the_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(
                _assistant_tool_call_message(
                    [
                        ("bad", "find_symbol", '{"name_path": "Widget"}'),
                        ("good", "build", '{"preset": "release"}'),
                    ]
                )
            ),
            _completion(_assistant_final_message("not yet")),
            _completion(_assistant_final_message("Nothing ran.")),
        ],
        tool_responses={"/build": {"ok": True, "stdout": "build succeeded", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Where is Widget?", REQUEST_ID, max_iterations=4)

    assert sidecar.calls == []
    assert scripted.destinations == []
    assert result["tool_calls"] == []
    assert result["answer"] == "Nothing ran."
    note = next(
        message["content"]
        for message in scripted.llm_payloads[1]["messages"]
        if message.get("role") == "system" and "invalid arguments" in (message.get("content") or "")
    )
    assert "invalid arguments for find_symbol" in note
    assert "Valid calls not executed: build" in note
