"""Body and line edits are named tools on the sidecar and join editing.

The tests inject a fake sidecar at tool_loop.sidecar. They check the
model-facing name, ok, and the phase after the call. They do not speak MCP.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Optional

import edit_policy
import httpx
import pytest
import tool_loop
from scope import project_root_header

REQUEST_ID = "req-other-writes"
_STILL_PRESENT = ("begin_plan", "edit", "apply")

# Serena 2.1 argument names (ghcr.io/oraios/serena:2.1.0). Required lists are
# the parameters that have no default. `safe_delete_symbol` takes
# `name_path_pattern`, not `name_path`.
_WRITES: dict[str, dict[str, Any]] = {
    "replace_symbol_body": {
        "required": ["name_path", "relative_path", "body"],
        "properties": ["name_path", "relative_path", "body"],
        "arguments": {
            "name_path": "Widget",
            "relative_path": "src/widget.hpp",
            "body": "class Widget {};\n",
        },
    },
    "insert_before_symbol": {
        "required": ["name_path", "relative_path", "body"],
        "properties": ["name_path", "relative_path", "body"],
        "arguments": {
            "name_path": "Widget",
            "relative_path": "src/widget.hpp",
            "body": "class Gadget {};\n",
        },
    },
    "insert_after_symbol": {
        "required": ["name_path", "relative_path", "body"],
        "properties": ["name_path", "relative_path", "body"],
        "arguments": {
            "name_path": "Widget",
            "relative_path": "src/widget.hpp",
            "body": "class Gadget {};\n",
        },
    },
    "safe_delete_symbol": {
        "required": ["name_path_pattern", "relative_path"],
        "properties": ["name_path_pattern", "relative_path"],
        "arguments": {"name_path_pattern": "Widget", "relative_path": "src/widget.hpp"},
    },
    "replace_content": {
        "required": ["relative_path", "needle", "repl", "mode"],
        "properties": ["relative_path", "needle", "repl", "mode", "allow_multiple_occurrences"],
        "enums": {"mode": ["literal", "regex"]},
        "arguments": {
            "relative_path": "src/widget.hpp",
            "needle": "Widget",
            "repl": "Gadget",
            "mode": "literal",
        },
    },
    "replace_in_files": {
        "required": ["needle", "repl", "mode"],
        "properties": [
            "needle",
            "repl",
            "mode",
            "relative_path",
            "paths_include_glob",
            "paths_exclude_glob",
            "dry_run",
            "occurrence_ids",
            "expected_count",
            "max_answer_chars",
        ],
        "enums": {"mode": ["literal", "regex"]},
        "arguments": {"needle": "Widget", "repl": "Gadget", "mode": "regex"},
    },
    "replace_lines": {
        "required": ["relative_path", "start_line", "end_line", "content"],
        "properties": ["relative_path", "start_line", "end_line", "content"],
        "arguments": {
            "relative_path": "src/widget.hpp",
            "start_line": 0,
            "end_line": 1,
            "content": "class Gadget {};\n",
        },
    },
    "insert_at_line": {
        "required": ["relative_path", "line", "content"],
        "properties": ["relative_path", "line", "content"],
        "arguments": {"relative_path": "src/widget.hpp", "line": 0, "content": "// header\n"},
    },
    "delete_lines": {
        "required": ["relative_path", "start_line", "end_line"],
        "properties": ["relative_path", "start_line", "end_line"],
        "arguments": {"relative_path": "src/widget.hpp", "start_line": 2, "end_line": 3},
    },
    "create_text_file": {
        "required": ["relative_path", "content"],
        "properties": ["relative_path", "content"],
        "arguments": {"relative_path": "src/gadget.hpp", "content": "#pragma once\n"},
    },
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
        tool_responses: Optional[dict[str, Any]] = None,
    ) -> None:
        self.llm_completions = llm_completions
        self.tool_responses = tool_responses or {}
        self.llm_call_count = 0
        self.llm_payloads: list[dict[str, Any]] = []
        self.destinations: list[str] = []
        self.phases: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/v1/chat/completions"):
            self.phases.append(edit_policy.PHASE.get())
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


class RecordingSidecar:
    """The third destination. Records each call and returns one fixed result."""

    def __init__(
        self,
        result: Optional[dict[str, Any]] = None,
        results: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        self.result = result or {"ok": True, "stdout": "wrote src/widget.hpp", "stderr": "", "exit_code": 0}
        self._results = list(results) if results is not None else None
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    async def call(self, name: str, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        self.calls.append((name, arguments, request_id))
        if self._results is not None:
            return self._results.pop(0)
        return self.result


def _offered_functions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [tool["function"] for tool in payload["tools"]]


@pytest.mark.asyncio
async def test_body_and_line_writes_run_on_the_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each Serena write name is on the shared schema and the call runs on the sidecar."""
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            *[
                _completion(
                    _assistant_tool_call_message([(f"c{index}", name, json.dumps(spec["arguments"]))])
                )
                for index, (name, spec) in enumerate(_WRITES.items(), start=1)
            ],
            _completion(_assistant_final_message("The edits are written.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "Edit Widget.", REQUEST_ID, max_iterations=16
        )

    offered = _offered_functions(scripted.llm_payloads[0])
    names = [tool["name"] for tool in offered]
    for name in _STILL_PRESENT:
        assert name not in names
    for name, spec in _WRITES.items():
        assert names.count(name) == 1
        tool = next(item for item in offered if item["name"] == name)
        assert tool["parameters"]["required"] == spec["required"]
        assert set(tool["parameters"]["properties"]) == set(spec["properties"])
        for prop in tool["parameters"]["properties"].values():
            assert "description" in prop
            assert "title" not in prop
        for field, enum in spec.get("enums", {}).items():
            assert tool["parameters"]["properties"][field]["enum"] == enum
    assert sidecar.calls == [(name, spec["arguments"], REQUEST_ID) for name, spec in _WRITES.items()]
    assert scripted.destinations == []
    assert [call["name"] for call in result["tool_calls"]] == list(_WRITES)
    assert [call["ok"] for call in result["tool_calls"]] == [True] * len(_WRITES)


@pytest.mark.asyncio
async def test_successful_replace_symbol_body_blocks_verification_until_build_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful body replacement enters editing. build, test, and compile_file wait for build_step."""
    arguments = _WRITES["replace_symbol_body"]["arguments"]
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", "replace_symbol_body", json.dumps(arguments))])),
            _completion(
                _assistant_tool_call_message(
                    [
                        ("c2", "build", '{"preset": "release"}'),
                        ("c3", "test", '{"preset": "release"}'),
                        ("c4", "compile_file", '{"path": "src/widget.cpp"}'),
                    ]
                )
            ),
            _completion(_assistant_tool_call_message([("c5", "build_step", '{"target": "pumba"}')])),
            _completion(_assistant_tool_call_message([("c6", "build", '{"preset": "release"}')])),
            _completion(_assistant_final_message("The edit builds.")),
        ],
        tool_responses={
            "/build-step": {"ok": True, "stdout": "built", "stderr": "", "exit_code": 0},
            "/build": {"ok": True, "stdout": "build succeeded", "stderr": "", "exit_code": 0},
        },
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "Replace the body of Widget.", REQUEST_ID, max_iterations=8
        )

    assert sidecar.calls == [("replace_symbol_body", arguments, REQUEST_ID)]
    assert [call["name"] for call in result["tool_calls"]] == [
        "replace_symbol_body",
        "build",
        "test",
        "compile_file",
        "build_step",
        "build",
    ]
    assert result["tool_calls"][0]["ok"] is True
    for call in result["tool_calls"][1:4]:
        assert call["ok"] is False
        assert "build_step" in call["result_summary"]
    assert result["tool_calls"][4]["ok"] is True
    assert result["tool_calls"][5]["ok"] is True
    assert scripted.phases[1:] == ["editing", "editing", "idle", "idle"]
    assert edit_policy.PHASE.get() == "idle"
    assert scripted.destinations == [
        "http://tools-test:8091/build-step",
        "http://tools-test:8091/build",
    ]


@pytest.mark.parametrize("name", list(_WRITES))
@pytest.mark.asyncio
async def test_a_successful_write_sets_editing(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A success on any of these writes enters editing. One phase rule covers the set."""
    arguments = _WRITES[name]["arguments"]
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", name, json.dumps(arguments))])),
            _completion(_assistant_final_message("Written.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Edit the checkout.", REQUEST_ID, max_iterations=4)

    assert sidecar.calls == [(name, arguments, REQUEST_ID)]
    assert result["tool_calls"][0]["name"] == name
    assert result["tool_calls"][0]["ok"] is True
    assert scripted.phases[1] == "editing"
    assert edit_policy.PHASE.get() == "editing"


@pytest.mark.asyncio
async def test_failed_replace_symbol_body_does_not_change_the_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    """A body replacement the sidecar rejects leaves the turn idle, so build still runs."""
    arguments = _WRITES["replace_symbol_body"]["arguments"]
    sidecar = RecordingSidecar(
        result={"ok": False, "stdout": "", "stderr": "clangd refused the edit", "exit_code": 1}
    )
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", "replace_symbol_body", json.dumps(arguments))])),
            _completion(_assistant_tool_call_message([("c2", "build", '{"preset": "release"}')])),
            _completion(_assistant_final_message("The edit did not land.")),
        ],
        tool_responses={"/build": {"ok": True, "stdout": "build succeeded", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "Replace the body of Widget.", REQUEST_ID, max_iterations=8
        )

    assert sidecar.calls == [("replace_symbol_body", arguments, REQUEST_ID)]
    assert result["tool_calls"][0]["name"] == "replace_symbol_body"
    assert result["tool_calls"][0]["ok"] is False
    assert result["tool_calls"][0]["line"] == "clangd refused the edit"
    assert result["tool_calls"][1]["name"] == "build"
    assert result["tool_calls"][1]["ok"] is True
    assert scripted.phases[1] == "idle"
    assert edit_policy.PHASE.get() == "idle"
    assert scripted.destinations == ["http://tools-test:8091/build"]


@pytest.mark.asyncio
async def test_replayed_replace_symbol_body_does_not_change_the_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replaying a body replacement after build_step stays idle and does not call the sidecar again."""
    arguments = _WRITES["replace_symbol_body"]["arguments"]
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", "replace_symbol_body", json.dumps(arguments))])),
            _completion(_assistant_tool_call_message([("c2", "build_step", '{"target": "pumba"}')])),
            _completion(_assistant_tool_call_message([("c3", "replace_symbol_body", json.dumps(arguments))])),
            _completion(_assistant_tool_call_message([("c4", "build", '{"preset": "release"}')])),
            _completion(_assistant_final_message("Idle again.")),
        ],
        tool_responses={
            "/build-step": {"ok": True, "stdout": "built", "stderr": "", "exit_code": 0},
            "/build": {"ok": True, "stdout": "build succeeded", "stderr": "", "exit_code": 0},
        },
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "Replace the body of Widget.", REQUEST_ID, max_iterations=8
        )

    assert sidecar.calls == [("replace_symbol_body", arguments, REQUEST_ID)]
    assert [call["ok"] for call in result["tool_calls"]] == [True, True, True, True]
    assert result["tool_calls"][2]["name"] == "replace_symbol_body"
    replayed = [
        message.get("content") or ""
        for message in scripted.llm_payloads[3]["messages"]
        if message.get("role") == "tool"
        and str(message.get("content", "")).startswith("Same arguments as an earlier call. The result is unchanged.")
    ]
    assert len(replayed) == 1
    assert scripted.phases[1] == "editing"
    assert scripted.phases[3] == "idle"
    assert edit_policy.PHASE.get() == "idle"
    assert scripted.destinations == [
        "http://tools-test:8091/build-step",
        "http://tools-test:8091/build",
    ]


_FIND_ARGUMENTS = {"name_path_pattern": "Widget"}
_FIND_BEFORE = "class Widget {};"
_FIND_AFTER = "class Gadget {};"
_WROTE = "Replaced the body of Widget in src/widget.hpp"


@pytest.mark.asyncio
async def test_successful_replace_symbol_body_clears_the_success_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """A later read of the same symbol is not the cached text from before the write."""
    arguments = _WRITES["replace_symbol_body"]["arguments"]
    sidecar = RecordingSidecar(
        results=[
            {"ok": True, "stdout": _FIND_BEFORE, "stderr": "", "exit_code": 0},
            {"ok": True, "stdout": _WROTE, "stderr": "", "exit_code": 0},
            {"ok": True, "stdout": _FIND_AFTER, "stderr": "", "exit_code": 0},
        ]
    )
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", "find_symbol", json.dumps(_FIND_ARGUMENTS))])),
            _completion(_assistant_tool_call_message([("c2", "replace_symbol_body", json.dumps(arguments))])),
            _completion(_assistant_tool_call_message([("c3", "find_symbol", json.dumps(_FIND_ARGUMENTS))])),
            _completion(_assistant_tool_call_message([("c4", "build_step", '{"target": "pumba"}')])),
            _completion(_assistant_final_message("Gadget is the body now.")),
        ],
        tool_responses={"/build-step": {"ok": True, "stdout": "built", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "Replace the body of Widget.", REQUEST_ID, max_iterations=8
        )

    assert [name for name, _arguments, _request_id in sidecar.calls] == [
        "find_symbol",
        "replace_symbol_body",
        "find_symbol",
    ]
    assert result["tool_calls"][0]["name"] == "find_symbol"
    assert result["tool_calls"][0]["ok"] is True
    assert result["tool_calls"][0]["line"] == _FIND_BEFORE
    assert result["tool_calls"][1]["name"] == "replace_symbol_body"
    assert result["tool_calls"][1]["ok"] is True
    assert result["tool_calls"][2]["name"] == "find_symbol"
    assert result["tool_calls"][2]["ok"] is True
    assert result["tool_calls"][2]["line"] == _FIND_AFTER
    assert scripted.destinations == ["http://tools-test:8091/build-step"]


def _body_arguments(relative_path: str) -> dict[str, Any]:
    arguments = dict(_WRITES["replace_symbol_body"]["arguments"])
    arguments["relative_path"] = relative_path
    return arguments


@pytest.fixture
def registered_checkout():
    """A direct child of /checkouts, the only mount the turn may use."""
    root = (Path("/checkouts") / "co_serena_06").resolve()
    root.mkdir(parents=True, exist_ok=True)
    token = project_root_header.set(str(root))
    try:
        yield root
    finally:
        project_root_header.reset(token)
        shutil.rmtree(root, ignore_errors=True)
        try:
            root.parent.rmdir()
        except OSError:
            pass


@pytest.mark.parametrize(
    ("relative_path", "refusal"),
    [
        ("../outside.cpp", "path must not contain '..' traversal: '../outside.cpp'"),
        (".git/config", "path must not pass through .git, .svn, or .env: '.git/config'"),
        ("src/.svn/entries", "path must not pass through .git, .svn, or .env: 'src/.svn/entries'"),
        (".env", "path must not pass through .git, .svn, or .env: '.env'"),
        (
            "secrets/.env.local",
            "path must not pass through .git, .svn, or .env: 'secrets/.env.local'",
        ),
    ],
)
@pytest.mark.asyncio
async def test_replace_symbol_body_outside_the_checkout_never_reaches_the_sidecar(
    monkeypatch: pytest.MonkeyPatch,
    registered_checkout: Path,
    relative_path: str,
    refusal: str,
) -> None:
    """A write through a forbidden path fails closed. The sidecar is not called."""
    assert registered_checkout.is_dir()
    monkeypatch.setenv("AGENT_LOOP_NUDGES", "0")
    arguments = _body_arguments(relative_path)
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", "replace_symbol_body", json.dumps(arguments))])),
            _completion(_assistant_final_message("The edit was refused.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Replace the body of Widget.", REQUEST_ID)

    assert sidecar.calls == []
    assert scripted.destinations == []
    assert result["tool_calls"] == [
        {
            "name": "replace_symbol_body",
            "arguments": arguments,
            "result_summary": f"replace_symbol_body failed: {refusal}",
            "ok": False,
            "line": refusal,
        }
    ]
    assert scripted.phases[1] == "idle"
    assert edit_policy.PHASE.get() == "idle"


@pytest.mark.asyncio
async def test_replace_symbol_body_inside_the_checkout_reaches_the_sidecar(
    monkeypatch: pytest.MonkeyPatch, registered_checkout: Path
) -> None:
    """A file inside the registered checkout still runs on the sidecar and enters editing."""
    assert registered_checkout.is_dir()
    arguments = _body_arguments("src/widget.hpp")
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", "replace_symbol_body", json.dumps(arguments))])),
            _completion(_assistant_final_message("The body is replaced.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Replace the body of Widget.", REQUEST_ID)

    assert sidecar.calls == [("replace_symbol_body", arguments, REQUEST_ID)]
    assert scripted.destinations == []
    assert result["tool_calls"][0]["name"] == "replace_symbol_body"
    assert result["tool_calls"][0]["ok"] is True
    assert scripted.phases[1] == "editing"
