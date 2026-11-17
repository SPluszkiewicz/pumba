"""Tests for the bounded LLM <-> tools loop in tool_loop.py.

Covers: a successful single tool-call round trip, a successful multi-call
round trip, the malformed-then-retry-then-succeed path, the
malformed-twice-then-degrade path, and hitting the max-iteration bound
without ever getting a final answer.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx
import pytest
import tool_loop

REQUEST_ID = "req-tool-loop"


def _assistant_tool_call_message(calls: list[tuple[str, str, str]]) -> dict[str, Any]:
    """calls: list of (call_id, function_name, raw_arguments_json_string)."""
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


def _completion(message: dict[str, Any], finish: Optional[str] = None) -> dict[str, Any]:
    choice: dict[str, Any] = {"message": message}
    if finish is not None:
        choice["finish_reason"] = finish
    return {"choices": [choice]}


class ScriptedTransport:
    """Serves a scripted sequence of `/v1/chat/completions` responses and a
    fixed map of tool endpoint responses, recording what was called."""

    def __init__(
        self,
        llm_completions: list[dict[str, Any]],
        tool_responses: Optional[dict[str, dict[str, Any]]] = None,
    ) -> None:
        self.llm_completions = llm_completions
        self.tool_responses = tool_responses or {}
        self.llm_call_count = 0
        self.tool_paths_called: list[str] = []
        self.llm_payloads: list[dict[str, Any]] = []
        self.tool_ordinal: dict[str, int] = {}

    def _tool_body(self, path: str) -> dict[str, Any]:
        spec = self.tool_responses.get(path, {"ok": True, "stdout": "", "stderr": "", "exit_code": 0})
        if isinstance(spec, list):
            index = self.tool_ordinal.get(path, 0)
            self.tool_ordinal[path] = index + 1
            if not spec:
                return {"ok": True, "stdout": "", "stderr": "", "exit_code": 0}
            return spec[min(index, len(spec) - 1)]
        return spec

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-request-id") == REQUEST_ID
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

        self.tool_paths_called.append(path)
        return httpx.Response(200, json=self._tool_body(path))

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


@pytest.mark.asyncio
async def test_repeated_identical_tool_call_is_not_executed_again() -> None:
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "build", '{"preset": "release"}')])),
            _completion(_assistant_tool_call_message([("call_2", "build", '{"preset": "release"}')])),
            _completion(_assistant_final_message("Answer from the build result.")),
        ],
        tool_responses={"/build": {"ok": True, "stdout": "build succeeded", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Does it build?", REQUEST_ID)

    assert scripted.tool_paths_called == ["/build"]
    assert result["answer"] == "Answer from the build result."
    assert result["outcome"] == "ok"
    # The second call is a replay: recorded, not sent, and the turn still has tools.
    assert [call["name"] for call in result["tool_calls"]] == ["build", "build"]
    assert result["iterations"] == 3
    assert "tools" in scripted.llm_payloads[1]
    replayed = [
        message["content"]
        for message in scripted.llm_payloads[2]["messages"]
        if message.get("role") == "tool"
        and str(message.get("content", "")).startswith("Same arguments as an earlier call. The result is unchanged.")
    ]
    assert len(replayed) == 1


@pytest.mark.asyncio
async def test_duplicate_calls_in_one_reply_are_not_executed() -> None:
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(
                _assistant_tool_call_message(
                    [
                        ("call_1", "build", '{"preset": "release"}'),
                        ("call_2", "build", '{"preset": "release"}'),
                    ]
                )
            ),
            _completion(_assistant_final_message("No second build.")),
        ],
        tool_responses={"/build": {"ok": True, "stdout": "build succeeded", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Does it build?", REQUEST_ID)

    assert scripted.tool_paths_called == ["/build"]
    assert result["answer"] == "No second build."
    assert [call["name"] for call in result["tool_calls"]] == ["build", "build"]
    assert "tools" in scripted.llm_payloads[1]
    contents = [
        message.get("content") or ""
        for message in scripted.llm_payloads[1]["messages"]
        if message.get("role") == "tool"
    ]
    assert len(contents) == 2
    assert contents[0].startswith("{")
    assert contents[1].startswith("Same arguments as an earlier call. The result is unchanged.")


@pytest.mark.asyncio
async def test_successful_single_tool_call_round_trip() -> None:
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "build", '{"preset": "release"}')])),
            _completion(_assistant_final_message("It builds fine.")),
        ],
        tool_responses={"/build": {"ok": True, "stdout": "build succeeded", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system prompt", "does it build?", REQUEST_ID)

    assert result["answer"] == "It builds fine."
    assert result["tool_calls"] == [
        {
            "name": "build",
            "arguments": {"preset": "release"},
            "result_summary": "build succeeded: build succeeded",
            "ok": True,
            "line": "build succeeded",
        }
    ]
    assert scripted.tool_paths_called == ["/build"]
    assert scripted.llm_call_count == 2
    assert result["outcome"] == "ok"
    assert result["iterations"] == 2


@pytest.mark.asyncio
async def test_successful_multi_call_round_trip() -> None:
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(
                _assistant_tool_call_message(
                    [
                        ("call_1", "build", '{"preset": "release"}'),
                        ("call_2", "test", '{"preset": "release"}'),
                    ]
                )
            ),
            _completion(_assistant_final_message("Build and tests both pass.")),
        ],
        tool_responses={
            "/build": {"ok": True, "stdout": "build succeeded", "stderr": "", "exit_code": 0},
            "/test": {"ok": True, "stdout": "3 passed", "stderr": "", "exit_code": 0},
        },
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system prompt", "build and test", REQUEST_ID)

    assert result["answer"] == "Build and tests both pass."
    assert [tc["name"] for tc in result["tool_calls"]] == ["build", "test"]
    assert scripted.tool_paths_called == ["/build", "/test"]


@pytest.mark.asyncio
async def test_malformed_then_retry_then_succeed() -> None:
    scripted = ScriptedTransport(
        llm_completions=[
            # invalid JSON arguments
            _completion(_assistant_tool_call_message([("call_1", "build", "{not valid json")])),
            # retried with the corrective note -> valid this time
            _completion(_assistant_tool_call_message([("call_2", "build", '{"preset": "release"}')])),
            _completion(_assistant_final_message("Build verified after a retry.")),
        ],
        tool_responses={"/build": {"ok": True, "stdout": "build succeeded", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system prompt", "does it build?", REQUEST_ID, max_iterations=5)

    assert result["answer"] == "Build verified after a retry."
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["name"] == "build"
    assert scripted.llm_call_count == 3


@pytest.mark.asyncio
async def test_malformed_twice_does_not_drop_tools() -> None:
    """Two malformed batches are not enough. Tools stay, and a later valid call runs."""
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "build", "{not valid json")])),
            _completion(_assistant_tool_call_message([("call_2", "not_a_real_tool", "{}")])),
            _completion(_assistant_tool_call_message([("call_3", "build", '{"preset": "release"}')])),
            _completion(_assistant_final_message("Build verified after two malformed batches.")),
        ],
        tool_responses={"/build": {"ok": True, "stdout": "build succeeded", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system prompt", "does it build?", REQUEST_ID, max_iterations=6)

    assert result["answer"] == "Build verified after two malformed batches."
    assert scripted.tool_paths_called == ["/build"]
    assert scripted.llm_call_count == 4
    assert all("tools" in payload for payload in scripted.llm_payloads)


@pytest.mark.asyncio
async def test_hits_max_iterations_without_final_answer_and_still_returns() -> None:
    # The LLM keeps calling `search` and never stops on its own. The arguments
    # change so this is not the repeated-call stop.
    search_foo = _completion(
        _assistant_tool_call_message([("call_n", "git_log", '{}')])
    )
    search_bar = _completion(
        _assistant_tool_call_message([("call_n", "git_log", '{"max_count": 2}')])
    )
    fallback_answer = _completion(_assistant_final_message("Ran out of iterations; here is a best-effort answer."))

    scripted = ScriptedTransport(
        llm_completions=[search_foo, search_bar, fallback_answer],
        tool_responses={"/git/log": {"ok": True, "stdout": "no matches", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system prompt", "find foo", REQUEST_ID, max_iterations=2)

    assert result["answer"] == "Ran out of iterations; here is a best-effort answer."
    assert len(result["tool_calls"]) == 2
    assert scripted.llm_call_count == 3  # 2 loop iterations + 1 fallback call


@pytest.mark.asyncio
async def test_llm_unreachable_returns_graceful_answer_without_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await tool_loop.run_tool_loop(client, "system prompt", "hello", REQUEST_ID)

    assert isinstance(result["answer"], str) and result["answer"]
    assert result["tool_calls"] == []


@pytest.mark.asyncio
async def test_arguments_rejected_by_model_trigger_retry_with_precise_reason() -> None:
    # `max_results` above the hard cap and a hallucinated parameter: both are
    # caught by the shared pydantic model before any HTTP call is made.
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(
                _assistant_tool_call_message(
                    [("c1", "git_log", '{"max_count": 5000, "recursive": true}')]
                )
            ),
            _completion(_assistant_tool_call_message([("c2", "git_log", '{}')])),
            _completion(_assistant_final_message("done")),
        ],
        tool_responses={"/git/log": {"ok": True, "stdout": "", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "sys", "q", REQUEST_ID, max_iterations=5)

    assert result["answer"] == "done"
    assert scripted.tool_paths_called == ["/git/log"]
    # Only what the model said is forwarded (no server-side defaults injected).
    assert result["tool_calls"][0]["arguments"] == {}


class TestParseToolCall:
    def _call(self, name: str, args: str) -> dict[str, Any]:
        return {"id": "x", "type": "function", "function": {"name": name, "arguments": args}}

    def test_unknown_function_lists_available_ones(self) -> None:
        with pytest.raises(tool_loop.MalformedToolCall, match="unknown function 'nope'.*read_file"):
            tool_loop._parse_tool_call(self._call("nope", "{}"))

    def test_invalid_json(self) -> None:
        with pytest.raises(tool_loop.MalformedToolCall, match="not valid JSON"):
            tool_loop._parse_tool_call(self._call("build", "{oops"))

    def test_non_object_arguments(self) -> None:
        with pytest.raises(tool_loop.MalformedToolCall, match="must be a JSON object"):
            tool_loop._parse_tool_call(self._call("build", "[1, 2]"))

    def test_validation_error_names_the_field(self) -> None:
        with pytest.raises(tool_loop.MalformedToolCall, match=r"git_log: max_count"):
            tool_loop._parse_tool_call(self._call("git_log", '{"max_count": 0}'))

    def test_valid_call_keeps_only_supplied_fields(self) -> None:
        call_id, name, args = tool_loop._parse_tool_call(self._call("git_log", '{"max_count": 5}'))
        assert (call_id, name, args) == ("x", "git_log", {"max_count": 5})

    def test_dict_arguments_accepted(self) -> None:
        call = {"function": {"name": "build", "arguments": {"preset": "debug"}}}
        assert tool_loop._parse_tool_call(call) == ("build", "build", {"preset": "debug"})


def test_tool_400_returns_the_service_error() -> None:
    import asyncio

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "error": "the plan names no source file"})

    async def run() -> dict[str, Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await tool_loop.execute_tool_call(client, "build", {"preset": "release"}, REQUEST_ID)

    result = asyncio.run(run())
    assert result["ok"] is False
    assert result["stderr"] == "the plan names no source file"
    assert result["exit_code"] == 2


def test_execute_tool_call_transport_failure_does_not_raise() -> None:
    import asyncio

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async def run() -> dict[str, Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await tool_loop.execute_tool_call(client, "build", {"preset": "release"}, REQUEST_ID)

    result = asyncio.run(run())
    assert result["ok"] is False
    assert result["exit_code"] == -1


def test_summarize_result_success_and_failure() -> None:
    ok_result = {"ok": True, "stdout": "all good", "stderr": ""}
    failed_result = {"ok": False, "stdout": "", "stderr": "compile error"}
    assert tool_loop.summarize_result("build", ok_result) == "build succeeded: all good"
    assert tool_loop.summarize_result("build", failed_result) == "build failed: compile error"


def test_call_key_sorts_fields_and_keeps_unicode() -> None:
    left = tool_loop._call_key("build", {"b": 1, "a": "é"})
    right = tool_loop._call_key("build", {"a": "é", "b": 1})
    assert left == right == 'build|{"a":"é","b":1}'


def test_disguised_call_shapes() -> None:
    fenced = '\n'.join(
        [
            "```json",
            '{"name": "rename_symbol", "arguments": {"relative_path": "src/calculator.hpp"}}',
            "```",
        ]
    )
    assert tool_loop._is_disguised_call(' {"name": "build"} ')
    assert tool_loop._is_disguised_call('read_file({"path": "a.cpp"})')
    assert tool_loop._is_disguised_call("rename_symbol: path")
    assert tool_loop._is_disguised_call(fenced)
    assert not tool_loop._is_disguised_call('{"answer": "four"}')
    assert not tool_loop._is_disguised_call("```json\n{\"answer\": \"four\"}\n```")
    assert not tool_loop._is_disguised_call("build something")
    assert not tool_loop._is_disguised_call("I would call build.")


def _listen() -> tuple[list[tuple[str, dict[str, Any]]], Any]:
    events: list[tuple[str, dict[str, Any]]] = []

    async def on_event(name: str, payload: dict[str, Any]) -> None:
        events.append((name, payload))

    return events, on_event


def _nudge_events(events: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [payload for name, payload in events if name == "nudge"]


def _system_texts(payload: dict[str, Any]) -> list[str]:
    return [message.get("content") or "" for message in payload.get("messages", []) if message.get("role") == "system"]


_FAILURE = (
    "A tool failed and is still open. Follow repair if it is present. "
    "After you change a file, call build_step again. "
    "Do not resend a call whose result was unchanged."
)
_NEEDS_BUILD = "Edits are written and not verified. Call build_step once. Do not answer yet."
_NO_TOOL = (
    "You have not called a tool yet. If this is about the repository, call one now. "
    "If you can answer without a tool, answer."
)
_DISGUISED = "Emit a structured tool call. A tool call written in the answer text does not run."
_LENGTH = "The reply was cut off. Send a shorter tool call or a shorter answer."


def _failure_notes(payload: dict[str, Any]) -> list[str]:
    return [text for text in _system_texts(payload) if text.startswith(_FAILURE)]


@pytest.mark.asyncio
async def test_open_failure_survives_a_green_read_and_keeps_the_build_tail() -> None:
    stderr = "HEAD_MARKER_" + ("A" * 400) + "TAIL_MARKER"
    events, on_event = _listen()
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", "build", '{"preset": "release"}')])),
            _completion(_assistant_tool_call_message([("c2", "git_log", '{}')])),
            _completion(_assistant_final_message("too early")),
            _completion(_assistant_tool_call_message([("c3", "build", '{"preset": "release"}')])),
            _completion(_assistant_final_message("checked")),
        ],
        tool_responses={
            "/build": [
                {"ok": False, "stdout": "", "stderr": stderr, "exit_code": 1, "repair": "compile_file"},
                {"ok": True, "stdout": "built", "stderr": "", "exit_code": 0},
            ],
            "/git/log": {"ok": True, "stdout": "int main() {}", "stderr": "", "exit_code": 0},
        },
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "does it build?", REQUEST_ID, max_iterations=8, on_event=on_event
        )

    assert result["answer"] == "checked"
    assert scripted.tool_paths_called == ["/build", "/git/log", "/build"]
    note = _failure_notes(scripted.llm_payloads[3])[0]
    assert note.splitlines()[1] == "build"
    assert "TAIL_MARKER" in note
    assert "HEAD_MARKER" not in note
    assert "compile_file" in note
    sent = "\n".join(message.get("content") or "" for message in scripted.llm_payloads[3]["messages"])
    assert "too early" not in sent
    assert _nudge_events(events)[0] == {"reason": "tool_failed", "tool": "build", "remaining": 2}


@pytest.mark.asyncio
async def test_green_lint_does_not_close_a_red_test() -> None:
    events, on_event = _listen()
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", "test", '{"filter": "sanity"}')])),
            _completion(_assistant_tool_call_message([("c2", "lint_clang_tidy", '{"paths": ["src/a.cpp"]}')])),
            _completion(_assistant_final_message("lint is enough")),
            _completion(_assistant_tool_call_message([("c3", "test", '{"filter": "sanity"}')])),
            _completion(_assistant_final_message("final")),
        ],
        tool_responses={
            "/test": [
                {"ok": False, "stdout": "", "stderr": "test failed badly", "exit_code": 1},
                {"ok": True, "stdout": "passed", "stderr": "", "exit_code": 0},
            ],
            "/lint/clang-tidy": {"ok": True, "stdout": "clean", "stderr": "", "exit_code": 0},
        },
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "run the tests", REQUEST_ID, max_iterations=8, on_event=on_event
        )

    assert result["answer"] == "final"
    assert scripted.tool_paths_called == ["/test", "/lint/clang-tidy", "/test"]
    note = _failure_notes(scripted.llm_payloads[3])[0]
    assert note.splitlines()[1] == "test"
    assert "test failed badly" in note
    assert _nudge_events(events)[0]["tool"] == "test"


class _RenamingSidecar:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call(self, name: str, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        self.calls.append(name)
        return {"ok": True, "stdout": "renamed", "stderr": "", "exit_code": 0}


_RENAME = '{"name_path": "calc/Evaluate", "relative_path": "src/calculator.hpp", "new_name": "Run"}'
_RENAME_AGAIN = '{"name_path": "calc/Evaluate", "relative_path": "src/calculator.hpp", "new_name": "Compute"}'


@pytest.mark.asyncio
async def test_second_write_after_a_green_build_step_needs_build(monkeypatch: pytest.MonkeyPatch) -> None:
    events, on_event = _listen()
    monkeypatch.setattr(tool_loop, "sidecar", _RenamingSidecar())
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", "rename_symbol", _RENAME)])),
            _completion(_assistant_tool_call_message([("c2", "build_step", '{"target": "pumba"}')])),
            _completion(_assistant_tool_call_message([("c3", "rename_symbol", _RENAME_AGAIN)])),
            _completion(_assistant_final_message("done")),
            _completion(_assistant_tool_call_message([("c4", "build_step", '{"target": "pumba"}')])),
            _completion(_assistant_final_message("verified")),
        ],
        tool_responses={
            "/build-step": {"ok": True, "stdout": "built", "stderr": "", "exit_code": 0},
        },
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "rename the symbol", REQUEST_ID, max_iterations=8, on_event=on_event
        )

    assert result["answer"] == "verified"
    assert scripted.tool_paths_called == ["/build-step", "/build-step"]
    assert _system_texts(scripted.llm_payloads[4])[-1] == _NEEDS_BUILD
    assert "done" not in "\n".join(message.get("content") or "" for message in scripted.llm_payloads[4]["messages"])
    assert _nudge_events(events) == [{"reason": "needs_build", "tool": "build_step", "remaining": 2}]


@pytest.mark.asyncio
async def test_identical_write_is_replayed_and_clears_the_read_cache_once(monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar = _RenamingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", "git_log", "{}")])),
            _completion(_assistant_tool_call_message([("c2", "rename_symbol", _RENAME)])),
            _completion(_assistant_tool_call_message([("c3", "rename_symbol", _RENAME)])),
            _completion(_assistant_tool_call_message([("c4", "git_log", "{}")])),
            _completion(_assistant_tool_call_message([("c5", "build_step", '{"target": "pumba"}')])),
            _completion(_assistant_final_message("verified")),
        ],
        tool_responses={
            "/git/log": {"ok": True, "stdout": "int main() {}", "stderr": "", "exit_code": 0},
            "/build-step": {"ok": True, "stdout": "built", "stderr": "", "exit_code": 0},
        },
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "rename", REQUEST_ID, max_iterations=8)

    assert result["answer"] == "verified"
    assert scripted.tool_paths_called == ["/git/log", "/git/log", "/build-step"]
    assert sidecar.calls == ["rename_symbol"]
    replayed = [
        message.get("content") or ""
        for message in scripted.llm_payloads[3]["messages"]
        if message.get("role") == "tool"
        and str(message.get("content", "")).startswith("Same arguments as an earlier call. The result is unchanged.")
    ]
    assert len(replayed) == 1


@pytest.mark.asyncio
async def test_build_during_editing_is_not_executed_and_does_not_replace_the_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, on_event = _listen()
    monkeypatch.setattr(tool_loop, "sidecar", _RenamingSidecar())
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", "rename_symbol", _RENAME)])),
            _completion(_assistant_tool_call_message([("c2", "build_step", '{"target": "pumba"}')])),
            _completion(_assistant_tool_call_message([("c3", "build", '{"preset": "release"}')])),
            _completion(_assistant_final_message("ship")),
            _completion(_assistant_tool_call_message([("c4", "build_step", '{"target": "pumba"}')])),
            _completion(_assistant_final_message("ok")),
        ],
        tool_responses={
            "/build-step": [
                {"ok": False, "stdout": "", "stderr": "step broke", "exit_code": 1, "repair": "build_step"},
                {"ok": True, "stdout": "built", "stderr": "", "exit_code": 0},
            ],
        },
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "rename", REQUEST_ID, max_iterations=8, on_event=on_event
        )

    assert result["answer"] == "ok"
    assert scripted.tool_paths_called == ["/build-step", "/build-step"]
    note = _failure_notes(scripted.llm_payloads[4])[0]
    assert note.splitlines()[1] == "build_step"
    assert "step broke" in note
    assert "build_step" in note
    assert not note.startswith("Edits are in progress")
    assert _nudge_events(events)[0] == {"reason": "tool_failed", "tool": "build_step", "remaining": 2}
    blocked = next(call for call in result["tool_calls"] if call["name"] == "build")
    assert blocked["ok"] is False


@pytest.mark.asyncio
async def test_mixed_batch_is_not_executed() -> None:
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(
                _assistant_tool_call_message(
                    [
                        ("c1", "git_log", "{}"),
                        ("c2", "build", "{not valid json"),
                    ]
                )
            ),
            _completion(_assistant_tool_call_message([("c3", "git_log", "{}")])),
            _completion(_assistant_final_message("read it")),
        ],
        tool_responses={"/git/log": {"ok": True, "stdout": "text", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "read", REQUEST_ID, max_iterations=6)

    assert result["answer"] == "read it"
    assert scripted.tool_paths_called == ["/git/log"]
    note = "\n".join(_system_texts(scripted.llm_payloads[1]))
    assert "not valid JSON" in note
    assert "Valid calls not executed: git_log. Send them again." in note
    assert not any(message.get("role") == "tool" for message in scripted.llm_payloads[1]["messages"])
    assert "tools" in scripted.llm_payloads[1]


@pytest.mark.asyncio
async def test_three_malformed_batches_drop_tools() -> None:
    bad = _completion(_assistant_tool_call_message([("c", "build", "{not valid json")]))
    scripted = ScriptedTransport(
        llm_completions=[
            bad,
            bad,
            bad,
            _completion(_assistant_final_message("best effort")),
        ],
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "build", REQUEST_ID, max_iterations=8)

    assert result["answer"] == "best effort"
    assert result["outcome"] == "malformed_giveup"
    assert scripted.tool_paths_called == []
    assert scripted.llm_call_count == 4
    assert "tools" not in scripted.llm_payloads[3]
    assert all("tools" in payload for payload in scripted.llm_payloads[:3])


@pytest.mark.asyncio
async def test_malformed_then_valid_then_malformed_resets_the_counter() -> None:
    bad = _completion(_assistant_tool_call_message([("c", "git_log", "{not json")]))
    good_foo = _completion(_assistant_tool_call_message([("c", "git_log", '{}')]))
    good_bar = _completion(_assistant_tool_call_message([("c", "git_log", '{"max_count": 2}')]))
    scripted = ScriptedTransport(
        llm_completions=[
            bad,
            good_foo,
            bad,
            bad,
            good_bar,
            _completion(_assistant_final_message("found")),
        ],
        tool_responses={"/git/log": {"ok": True, "stdout": "hit", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "find", REQUEST_ID, max_iterations=8)

    assert result["answer"] == "found"
    assert scripted.tool_paths_called == ["/git/log", "/git/log"]
    assert all("tools" in payload for payload in scripted.llm_payloads)


@pytest.mark.asyncio
async def test_fourth_identical_batch_is_a_completion_without_tools() -> None:
    search = _completion(_assistant_tool_call_message([("c", "git_log", '{}')]))
    scripted = ScriptedTransport(
        llm_completions=[
            search,
            search,
            search,
            search,
            _completion(_assistant_final_message("I am stopping.")),
        ],
        tool_responses={"/git/log": {"ok": False, "stdout": "", "stderr": "nope", "exit_code": 1}},
    )
    events, on_event = _listen()

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "find", REQUEST_ID, max_iterations=8, on_event=on_event
        )

    assert result["answer"] == "I am stopping."
    assert result["outcome"] == "repeated_tool"
    assert scripted.tool_paths_called == ["/git/log", "/git/log", "/git/log"]
    assert scripted.llm_call_count == 5
    assert "tools" not in scripted.llm_payloads[4]
    assert _nudge_events(events) == []


@pytest.mark.asyncio
async def test_empty_forced_prose_is_the_fixed_sentence() -> None:
    search = _completion(_assistant_tool_call_message([("c", "git_log", '{}')]))
    empty = _completion(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "z", "type": "function", "function": {"name": "build", "arguments": "{}"}}],
        }
    )
    scripted = ScriptedTransport(
        llm_completions=[search, search, search, search, empty],
        tool_responses={"/git/log": {"ok": False, "stdout": "", "stderr": "nope", "exit_code": 1}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "find", REQUEST_ID, max_iterations=8)

    assert result["answer"] == "Could not finish the tool loop."
    assert scripted.tool_paths_called == ["/git/log", "/git/log", "/git/log"]


@pytest.mark.asyncio
async def test_swapped_batch_order_is_the_same_identity() -> None:
    def batch(order: tuple[str, str]) -> dict[str, Any]:
        calls = [(f"c{index}", name, '{"preset": "release"}') for index, name in enumerate(order)]
        return _completion(_assistant_tool_call_message(calls))

    scripted = ScriptedTransport(
        llm_completions=[
            batch(("build", "test")),
            batch(("test", "build")),
            batch(("build", "test")),
            batch(("test", "build")),
            _completion(_assistant_final_message("stopped")),
        ],
        tool_responses={
            "/build": {"ok": False, "stdout": "", "stderr": "build boom", "exit_code": 1},
            "/test": {"ok": False, "stdout": "", "stderr": "test boom", "exit_code": 1},
        },
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "check", REQUEST_ID, max_iterations=8)

    assert result["answer"] == "stopped"
    assert result["outcome"] == "repeated_tool"
    assert scripted.tool_paths_called == ["/build", "/test", "/test", "/build", "/build", "/test"]
    assert "tools" not in scripted.llm_payloads[4]


@pytest.mark.asyncio
async def test_malformed_batch_does_not_reset_the_repeat_counter() -> None:
    search = _completion(_assistant_tool_call_message([("c", "git_log", '{}')]))
    bad = _completion(_assistant_tool_call_message([("c", "git_log", "{not json")]))
    scripted = ScriptedTransport(
        llm_completions=[
            search,
            search,
            bad,
            search,
            search,
            _completion(_assistant_final_message("done")),
        ],
        tool_responses={"/git/log": {"ok": False, "stdout": "", "stderr": "nope", "exit_code": 1}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "find", REQUEST_ID, max_iterations=8)

    assert result["answer"] == "done"
    assert result["outcome"] == "repeated_tool"
    assert scripted.tool_paths_called == ["/git/log", "/git/log", "/git/log"]
    assert "tools" not in scripted.llm_payloads[5]


@pytest.mark.asyncio
async def test_tool_written_in_prose_is_malformed() -> None:
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_final_message('{"name": "read_file", "path": "a.cpp"}')),
            _completion(_assistant_tool_call_message([("c", "git_log", '{}')])),
            _completion(_assistant_final_message("found")),
        ],
        tool_responses={"/git/log": {"ok": True, "stdout": "hit", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "read", REQUEST_ID, max_iterations=6)

    assert result["answer"] == "found"
    assert scripted.tool_paths_called == ["/git/log"]
    assert _DISGUISED in _system_texts(scripted.llm_payloads[1])
    assert "tools" in scripted.llm_payloads[1]


@pytest.mark.asyncio
async def test_fenced_tool_json_is_malformed() -> None:
    fenced = "\n".join(
        [
            "```json",
            "{",
            '  "name": "rename_symbol",',
            '  "arguments": {"relative_path": "src/calculator.hpp"}',
            "}",
            "```",
        ]
    )
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_final_message(fenced)),
            _completion(_assistant_tool_call_message([("c", "git_log", '{}')])),
            _completion(_assistant_final_message("found")),
        ],
        tool_responses={"/git/log": {"ok": True, "stdout": "hit", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "read", REQUEST_ID, max_iterations=6)

    assert result["answer"] == "found"
    assert scripted.tool_paths_called == ["/git/log"]
    note = _system_texts(scripted.llm_payloads[1])[-1]
    assert "invalid arguments for rename_symbol" in note
    assert "name_path" in note
    assert "tools" in scripted.llm_payloads[1]


@pytest.mark.asyncio
async def test_tools_tag_call_is_executed() -> None:
    prose = "\n".join(
        [
            "<tools>",
            '{"name": "git_log", "arguments": {}}',
            "</tools>",
        ]
    )
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_final_message(prose)),
            _completion(_assistant_final_message("found")),
        ],
        tool_responses={"/git/log": {"ok": True, "stdout": "hit", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "find", REQUEST_ID, max_iterations=4)

    assert result["answer"] == "found"
    assert scripted.tool_paths_called == ["/git/log"]
    assert result["tool_calls"][0]["name"] == "git_log"
    assert result["tool_calls"][0]["ok"] is True


@pytest.mark.asyncio
async def test_tool_line_written_in_prose_is_malformed() -> None:
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_final_message('read_file({"path": "a.cpp"})')),
            _completion(_assistant_final_message("plain answer")),
            _completion(_assistant_final_message("kept")),
        ],
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "read", REQUEST_ID, max_iterations=6)

    assert result["answer"] == "kept"
    assert scripted.tool_paths_called == []
    assert _system_texts(scripted.llm_payloads[1])[-1] == _DISGUISED
    assert _system_texts(scripted.llm_payloads[2])[-1] == _NO_TOOL


@pytest.mark.asyncio
async def test_finish_reason_length_is_malformed() -> None:
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_final_message("partial answer"), finish="length"),
            _completion(_assistant_tool_call_message([("c", "git_log", '{}')])),
            _completion(_assistant_final_message("found")),
        ],
        tool_responses={"/git/log": {"ok": True, "stdout": "hit", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "find", REQUEST_ID, max_iterations=6)

    assert result["answer"] == "found"
    assert scripted.tool_paths_called == ["/git/log"]
    assert _system_texts(scripted.llm_payloads[1])[-1] == _LENGTH
    assert "tools" in scripted.llm_payloads[1]


@pytest.mark.asyncio
async def test_no_tool_is_sent_once() -> None:
    events, on_event = _listen()
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_final_message("draft one")),
            _completion(_assistant_final_message("final answer")),
        ],
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "what is this?", REQUEST_ID, max_iterations=6, on_event=on_event
        )

    assert result["answer"] == "final answer"
    assert result["nudges"] == 1
    assert scripted.llm_call_count == 2
    assert _nudge_events(events) == [{"reason": "no_tool", "remaining": 2}]
    assert "tool" not in _nudge_events(events)[0]
    joined = "\n".join(message.get("content") or "" for message in scripted.llm_payloads[1]["messages"])
    assert "draft one" not in joined
    assert _NO_TOOL in joined


@pytest.mark.asyncio
async def test_json_without_a_tool_name_is_not_a_disguised_call() -> None:
    events, on_event = _listen()
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_final_message('{"answer": "four"}')),
            _completion(_assistant_final_message("four")),
        ],
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "what is 2+2?", REQUEST_ID, max_iterations=4, on_event=on_event
        )

    assert result["answer"] == "four"
    assert _nudge_events(events)[0]["reason"] == "no_tool"
    assert _DISGUISED not in "\n".join(_system_texts(scripted.llm_payloads[1]))


@pytest.mark.asyncio
async def test_fourth_prose_with_an_open_failure_is_the_answer() -> None:
    events, on_event = _listen()
    fail = _completion(_assistant_tool_call_message([("c", "build", '{"preset": "release"}')]))
    scripted = ScriptedTransport(
        llm_completions=[
            fail,
            _completion(_assistant_final_message("p1")),
            _completion(_assistant_final_message("p2")),
            _completion(_assistant_final_message("p3")),
            _completion(_assistant_final_message("p4")),
        ],
        tool_responses={"/build": {"ok": False, "stdout": "", "stderr": "boom", "exit_code": 1}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "build", REQUEST_ID, max_iterations=8, on_event=on_event
        )

    assert result["answer"] == "p4"
    assert result["nudges"] == 3
    assert [item["remaining"] for item in _nudge_events(events)] == [2, 1, 0]
    joined = "\n".join(message.get("content") or "" for message in scripted.llm_payloads[4]["messages"])
    assert "p1" not in joined and "p2" not in joined and "p3" not in joined


@pytest.mark.asyncio
async def test_last_iteration_keeps_prose_even_when_a_failure_is_open() -> None:
    events, on_event = _listen()
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c", "build", '{"preset": "release"}')])),
            _completion(_assistant_final_message("keep me")),
        ],
        tool_responses={"/build": {"ok": False, "stdout": "", "stderr": "boom", "exit_code": 1}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "build", REQUEST_ID, max_iterations=2, on_event=on_event
        )

    assert result["answer"] == "keep me"
    assert "nudges" not in result
    assert result["outcome"] == "ok"
    assert scripted.llm_call_count == 2
    assert _nudge_events(events) == []


@pytest.mark.asyncio
async def test_tool_message_keeps_name_for_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[dict[str, Any]]] = []
    real = tool_loop.apply_budget

    async def spy(http_client: httpx.AsyncClient, messages: list[dict[str, Any]], request_id: str, **kwargs: Any):
        seen.append([dict(message) for message in messages])
        return await real(http_client, messages, request_id, **kwargs)

    monkeypatch.setattr(tool_loop, "apply_budget", spy)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", "build", '{"preset": "release"}')])),
            _completion(_assistant_final_message("ok")),
        ],
        tool_responses={"/build": {"ok": True, "stdout": "built", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        await tool_loop.run_tool_loop(client, "system", "build", REQUEST_ID)

    named = [message for message in seen[1] if message.get("role") == "tool"]
    assert named and named[0]["name"] == "build"
    assert all("name" not in message for message in scripted.llm_payloads[1]["messages"])
