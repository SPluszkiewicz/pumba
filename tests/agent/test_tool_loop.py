"""Tests for the bounded LLM <-> tools loop in tool_loop.py.

Covers: a successful single tool-call round trip, a successful multi-call
round trip, the malformed-then-retry-then-succeed path, the
malformed-twice-then-degrade path, and hitting the max-iteration bound
without ever getting a final answer.
"""

from __future__ import annotations

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


def _completion(message: dict[str, Any]) -> dict[str, Any]:
    return {"choices": [{"message": message}]}


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

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-request-id") == REQUEST_ID
        path = request.url.path
        if path.endswith("/v1/chat/completions"):
            idx = min(self.llm_call_count, len(self.llm_completions) - 1)
            self.llm_call_count += 1
            return httpx.Response(200, json=self.llm_completions[idx])

        if path.endswith("/tokenize"):
            return httpx.Response(200, json={"tokens": [1]})

        self.tool_paths_called.append(path)
        body = self.tool_responses.get(path, {"ok": True, "stdout": "", "stderr": "", "exit_code": 0})
        return httpx.Response(200, json=body)

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
    assert len(result["tool_calls"]) == 1


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

    assert scripted.tool_paths_called == []
    assert result["answer"] == "No second build."


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
        {"name": "build", "arguments": {"preset": "release"}, "result_summary": "build succeeded: build succeeded"}
    ]
    assert scripted.tool_paths_called == ["/build"]
    assert scripted.llm_call_count == 2


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
async def test_malformed_twice_then_degrades_to_plain_text() -> None:
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "build", "{not valid json")])),
            # still malformed on the retry -> unknown function name this time
            _completion(_assistant_tool_call_message([("call_2", "not_a_real_tool", "{}")])),
            # this is the forced plain-text fallback completion (no tools attached)
            _completion(_assistant_final_message("I could not verify this with tools, but here is my best answer.")),
        ],
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system prompt", "does it build?", REQUEST_ID, max_iterations=5)

    assert result["answer"] == "I could not verify this with tools, but here is my best answer."
    assert result["tool_calls"] == []
    assert scripted.tool_paths_called == []
    assert scripted.llm_call_count == 3


@pytest.mark.asyncio
async def test_hits_max_iterations_without_final_answer_and_still_returns() -> None:
    # The LLM keeps calling `search` and never stops on its own. The arguments
    # change so this is not the repeated-call stop.
    search_foo = _completion(
        _assistant_tool_call_message([("call_n", "search", '{"pattern": "foo", "regex": false}')])
    )
    search_bar = _completion(
        _assistant_tool_call_message([("call_n", "search", '{"pattern": "bar", "regex": false}')])
    )
    fallback_answer = _completion(_assistant_final_message("Ran out of iterations; here is a best-effort answer."))

    scripted = ScriptedTransport(
        llm_completions=[search_foo, search_bar, fallback_answer],
        tool_responses={"/search": {"ok": True, "stdout": "no matches", "stderr": "", "exit_code": 0}},
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
                    [("c1", "search", '{"pattern": "foo", "max_results": 5000, "recursive": true}')]
                )
            ),
            _completion(_assistant_tool_call_message([("c2", "search", '{"pattern": "foo"}')])),
            _completion(_assistant_final_message("done")),
        ],
        tool_responses={"/search": {"ok": True, "stdout": "", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "sys", "q", REQUEST_ID, max_iterations=5)

    assert result["answer"] == "done"
    assert scripted.tool_paths_called == ["/search"]
    # Only what the model said is forwarded (no server-side defaults injected).
    assert result["tool_calls"][0]["arguments"] == {"pattern": "foo"}


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
        with pytest.raises(tool_loop.MalformedToolCall, match=r"read_file: start_line"):
            tool_loop._parse_tool_call(self._call("read_file", '{"path": "a.cpp", "start_line": 0}'))

    def test_valid_call_keeps_only_supplied_fields(self) -> None:
        call_id, name, args = tool_loop._parse_tool_call(self._call("list_files", '{"glob": "*.hpp"}'))
        assert (call_id, name, args) == ("x", "list_files", {"glob": "*.hpp"})

    def test_dict_arguments_accepted(self) -> None:
        call = {"function": {"name": "build", "arguments": {"preset": "debug"}}}
        assert tool_loop._parse_tool_call(call) == ("build", "build", {"preset": "debug"})


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
