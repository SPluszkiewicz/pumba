"""File list and search are named tools on the sidecar, beside the tools routes.

/ui and the CLI share the one schema this loop sends. The tests inject a
fake sidecar at the dispatch and never speak MCP.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Optional

import httpx
import pytest
import tool_loop
from scope import project_root_header

REQUEST_ID = "req-file-list"
LIST_TEXT = "src/widget.hpp"


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
        self._tool_ordinal: dict[str, int] = {}

    def _tool_body(self, path: str) -> dict[str, Any]:
        spec = self.tool_responses.get(path, {"ok": True, "stdout": "", "stderr": "", "exit_code": 0})
        if isinstance(spec, list):
            index = self._tool_ordinal.get(path, 0)
            self._tool_ordinal[path] = index + 1
            if not spec:
                return {"ok": True, "stdout": "", "stderr": "", "exit_code": 0}
            return spec[min(index, len(spec) - 1)]
        return spec

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
        return httpx.Response(200, json=self._tool_body(path))

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _listen() -> tuple[list[tuple[str, dict[str, Any]]], Any]:
    events: list[tuple[str, dict[str, Any]]] = []

    async def on_event(name: str, payload: dict[str, Any]) -> None:
        events.append((name, payload))

    return events, on_event


class RecordingSidecar:
    """The third destination. Records each call and returns one fixed result."""

    def __init__(self, result: Optional[dict[str, Any]] = None) -> None:
        self.result = result or {"ok": True, "stdout": LIST_TEXT, "stderr": "", "exit_code": 0}
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    async def call(self, name: str, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        self.calls.append((name, arguments, request_id))
        return self.result


@pytest.mark.asyncio
async def test_list_dir_is_a_named_tool_on_the_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    """list_dir keeps Serena's name and argument names, and the call runs on the sidecar."""
    arguments = {"relative_path": "src", "recursive": False}
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    events, on_event = _listen()
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "list_dir", json.dumps(arguments))])),
            _completion(_assistant_final_message("The header is in src.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "What is in src?", REQUEST_ID, on_event=on_event)

    offered = [tool["function"] for tool in scripted.llm_payloads[0]["tools"]]
    names = [tool["name"] for tool in offered]
    assert names.count("list_dir") == 1
    assert "list_directory" not in names
    spec = next(tool for tool in offered if tool["name"] == "list_dir")
    assert spec["parameters"]["required"] == ["relative_path", "recursive"]
    properties = spec["parameters"]["properties"]
    assert set(properties) == {"relative_path", "recursive", "skip_ignored_files", "max_answer_chars"}
    assert properties["skip_ignored_files"]["default"] is False
    assert properties["max_answer_chars"]["default"] == -1
    for prop in properties.values():
        assert "description" in prop
        assert "title" not in prop
    assert sidecar.calls == [("list_dir", arguments, REQUEST_ID)]
    assert scripted.destinations == []
    assert result["tool_calls"] == [
        {
            "name": "list_dir",
            "arguments": arguments,
            "result_summary": "list_dir succeeded: src/widget.hpp",
            "ok": True,
            "line": LIST_TEXT,
        }
    ]
    assert ("tool_call", {"name": "list_dir", "arguments": arguments}) in events
    assert (
        "tool_result",
        {"name": "list_dir", "result_summary": "list_dir succeeded: src/widget.hpp", "ok": True},
    ) in events
    assert result["answer"] == "The header is in src."


@pytest.mark.asyncio
async def test_find_file_is_a_named_tool_on_the_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    """find_file keeps Serena's name and argument names, and the call runs on the sidecar."""
    arguments = {"file_mask": "widget.hpp", "relative_path": "src"}
    found = "src/widget.hpp"
    sidecar = RecordingSidecar(result={"ok": True, "stdout": found, "stderr": "", "exit_code": 0})
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    events, on_event = _listen()
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "find_file", json.dumps(arguments))])),
            _completion(_assistant_final_message("The header is src/widget.hpp.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Where is widget.hpp?", REQUEST_ID, on_event=on_event)

    offered = [tool["function"] for tool in scripted.llm_payloads[0]["tools"]]
    names = [tool["name"] for tool in offered]
    assert names.count("find_file") == 1
    assert "find_files" not in names
    spec = next(tool for tool in offered if tool["name"] == "find_file")
    assert spec["parameters"]["required"] == ["file_mask", "relative_path"]
    properties = spec["parameters"]["properties"]
    assert set(properties) == {"file_mask", "relative_path", "skip_ignored_files"}
    assert properties["skip_ignored_files"]["default"] is False
    for prop in properties.values():
        assert "description" in prop
        assert "title" not in prop
    assert sidecar.calls == [("find_file", arguments, REQUEST_ID)]
    assert scripted.destinations == []
    assert result["tool_calls"] == [
        {
            "name": "find_file",
            "arguments": arguments,
            "result_summary": f"find_file succeeded: {found}",
            "ok": True,
            "line": found,
        }
    ]
    assert ("tool_call", {"name": "find_file", "arguments": arguments}) in events
    assert (
        "tool_result",
        {"name": "find_file", "result_summary": f"find_file succeeded: {found}", "ok": True},
    ) in events
    assert result["answer"] == "The header is src/widget.hpp."


@pytest.mark.asyncio
async def test_search_for_pattern_is_a_named_tool_on_the_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    """search_for_pattern keeps Serena's name and argument names, and the call runs on the sidecar."""
    arguments = {"substring_pattern": "class Widget"}
    match = "src/widget.hpp:4:class Widget"
    sidecar = RecordingSidecar(result={"ok": True, "stdout": match, "stderr": "", "exit_code": 0})
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    events, on_event = _listen()
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "search_for_pattern", json.dumps(arguments))])),
            _completion(_assistant_final_message("Widget is declared in the header.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "Where is class Widget?", REQUEST_ID, on_event=on_event
        )

    offered = [tool["function"] for tool in scripted.llm_payloads[0]["tools"]]
    names = [tool["name"] for tool in offered]
    assert names.count("search_for_pattern") == 1
    assert "search_pattern" not in names
    spec = next(tool for tool in offered if tool["name"] == "search_for_pattern")
    assert spec["parameters"]["required"] == ["substring_pattern"]
    properties = spec["parameters"]["properties"]
    assert set(properties) == {
        "substring_pattern",
        "context_lines_before",
        "context_lines_after",
        "paths_include_glob",
        "paths_exclude_glob",
        "relative_path",
        "restrict_search_to_code_files",
        "skip_ignored_files",
        "multiline",
        "max_answer_chars",
    }
    assert properties["context_lines_before"]["default"] == 0
    assert properties["context_lines_after"]["default"] == 0
    assert properties["paths_include_glob"]["default"] == ""
    assert properties["paths_exclude_glob"]["default"] == ""
    assert properties["relative_path"]["default"] == ""
    assert properties["restrict_search_to_code_files"]["default"] is False
    assert properties["skip_ignored_files"]["default"] is True
    assert properties["multiline"]["default"] is True
    assert properties["max_answer_chars"]["default"] == -1
    for prop in properties.values():
        assert "description" in prop
        assert "title" not in prop
    assert sidecar.calls == [("search_for_pattern", arguments, REQUEST_ID)]
    assert scripted.destinations == []
    assert result["tool_calls"] == [
        {
            "name": "search_for_pattern",
            "arguments": arguments,
            "result_summary": f"search_for_pattern succeeded: {match}",
            "ok": True,
            "line": match,
        }
    ]
    assert ("tool_call", {"name": "search_for_pattern", "arguments": arguments}) in events
    assert (
        "tool_result",
        {
            "name": "search_for_pattern",
            "result_summary": f"search_for_pattern succeeded: {match}",
            "ok": True,
        },
    ) in events
    assert result["answer"] == "Widget is declared in the header."


@pytest.fixture
def registered_checkout():
    """A direct child of /checkouts, the only mount the turn may use."""
    root = (Path("/checkouts") / "co_serena_04").resolve()
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
    ("name", "arguments", "refusal"),
    [
        (
            "list_dir",
            {"relative_path": ".git/config", "recursive": False},
            "path must not pass through .git, .svn, or .env: '.git/config'",
        ),
        (
            "find_file",
            {"file_mask": "*.hpp", "relative_path": "../outside.cpp"},
            "path must not contain '..' traversal: '../outside.cpp'",
        ),
        (
            "search_for_pattern",
            {"substring_pattern": "Widget", "relative_path": ".env"},
            "path must not pass through .git, .svn, or .env: '.env'",
        ),
        (
            "list_dir",
            {"relative_path": "src", "recursive": False, "max_answer_chars": 262145},
            "max_answer_chars exceeds the 262144 character cap",
        ),
        (
            "search_for_pattern",
            {"substring_pattern": "Widget", "max_answer_chars": 262145},
            "max_answer_chars exceeds the 262144 character cap",
        ),
    ],
)
@pytest.mark.asyncio
async def test_forbidden_path_or_oversized_answer_never_reaches_the_sidecar(
    monkeypatch: pytest.MonkeyPatch,
    registered_checkout: Path,
    name: str,
    arguments: dict[str, Any],
    refusal: str,
) -> None:
    """These tools use relative_path and max_answer_chars. The existing checks refuse them."""
    assert registered_checkout.is_dir()
    monkeypatch.setenv("AGENT_LOOP_NUDGES", "0")
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", name, json.dumps(arguments))])),
            _completion(_assistant_final_message("The call was refused.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Look in the checkout.", REQUEST_ID)

    assert sidecar.calls == []
    assert scripted.destinations == []
    assert result["answer"] == "The call was refused."
    assert result["tool_calls"] == [
        {
            "name": name,
            "arguments": arguments,
            "result_summary": f"{name} failed: {refusal}",
            "ok": False,
            "line": refusal,
        }
    ]


@pytest.mark.parametrize(
    ("arguments", "refusal"),
    [
        (
            {"substring_pattern": "Widget", "paths_include_glob": ".git/**"},
            "path must not pass through .git, .svn, or .env: '.git/**'",
        ),
        (
            {"substring_pattern": "Widget", "paths_exclude_glob": "../outside/**"},
            "path must not contain '..' traversal: '../outside/**'",
        ),
    ],
)
@pytest.mark.asyncio
async def test_forbidden_search_glob_never_reaches_the_sidecar(
    monkeypatch: pytest.MonkeyPatch,
    registered_checkout: Path,
    arguments: dict[str, Any],
    refusal: str,
) -> None:
    """Search globs are paths. A forbidden one is refused before the sidecar."""
    assert registered_checkout.is_dir()
    monkeypatch.setenv("AGENT_LOOP_NUDGES", "0")
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "search_for_pattern", json.dumps(arguments))])),
            _completion(_assistant_final_message("The call was refused.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Search the checkout.", REQUEST_ID)

    assert sidecar.calls == []
    assert result["tool_calls"] == [
        {
            "name": "search_for_pattern",
            "arguments": arguments,
            "result_summary": f"search_for_pattern failed: {refusal}",
            "ok": False,
            "line": refusal,
        }
    ]


@pytest.mark.asyncio
async def test_ordinary_search_glob_reaches_the_sidecar(
    monkeypatch: pytest.MonkeyPatch, registered_checkout: Path
) -> None:
    """A checkout glob is not a forbidden path. The sidecar still runs the search."""
    assert registered_checkout.is_dir()
    arguments = {"substring_pattern": "Widget", "paths_include_glob": "src/**/*.hpp", "relative_path": "src"}
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "search_for_pattern", json.dumps(arguments))])),
            _completion(_assistant_final_message("The header matches.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Search headers.", REQUEST_ID)

    assert sidecar.calls == [("search_for_pattern", arguments, REQUEST_ID)]
    assert result["tool_calls"][0]["ok"] is True
    assert result["answer"] == "The header matches."


@pytest.mark.asyncio
async def test_read_file_runs_on_serena_and_the_old_file_tools_are_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """read_file is Serena's name. list_files and search are no longer named tools."""
    sidecar = RecordingSidecar(
        {"ok": True, "stdout": "int main() {}", "stderr": "", "exit_code": 0}
    )
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(
                _assistant_tool_call_message([("c1", "read_file", '{"relative_path": "src/a.cpp"}')])
            ),
            _completion(_assistant_final_message("Read from Serena.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Read the tree.", REQUEST_ID, max_iterations=4)

    offered = [tool["function"] for tool in scripted.llm_payloads[0]["tools"]]
    names = [tool["name"] for tool in offered]
    assert names.count("read_file") == 1
    assert "list_files" not in names
    assert "search" not in names
    read_file = next(tool for tool in offered if tool["name"] == "read_file")
    assert read_file["parameters"]["required"] == ["relative_path"]
    assert sidecar.calls == [("read_file", {"relative_path": "src/a.cpp"}, REQUEST_ID)]
    assert scripted.destinations == []
    assert [call["name"] for call in result["tool_calls"]] == ["read_file"]
    assert result["tool_calls"][0]["ok"] is True
    assert result["answer"] == "Read from Serena."


_FAILURE_NOTE = (
    "A tool failed and is still open. Follow repair if it is present. "
    "After you change a file, call build_step again. "
    "Do not resend a call whose result was unchanged."
)


def _failure_notes(payload: dict[str, Any]) -> list[str]:
    return [
        message.get("content") or ""
        for message in payload.get("messages", [])
        if message.get("role") == "system" and (message.get("content") or "").startswith(_FAILURE_NOTE)
    ]


@pytest.mark.parametrize(
    ("verify_name", "verify_arguments", "verify_path", "read_name", "read_arguments"),
    [
        ("build", {"preset": "release"}, "/build", "list_dir", {"relative_path": "src", "recursive": False}),
        ("test", {"filter": "sanity"}, "/test", "find_file", {"file_mask": "*.hpp", "relative_path": "src"}),
        (
            "compile_file",
            {"path": "src/a.cpp"},
            "/compile",
            "search_for_pattern",
            {"substring_pattern": "Widget"},
        ),
        (
            "build_step",
            {"target": "pumba"},
            "/build-step",
            "list_dir",
            {"relative_path": ".", "recursive": True},
        ),
    ],
)
@pytest.mark.asyncio
async def test_successful_serena_file_read_leaves_the_verify_failure_open(
    monkeypatch: pytest.MonkeyPatch,
    verify_name: str,
    verify_arguments: dict[str, Any],
    verify_path: str,
    read_name: str,
    read_arguments: dict[str, Any],
) -> None:
    """A failed build, test, compile_file, or build_step stays open after a Serena file read."""
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    events, on_event = _listen()
    verify_call = json.dumps(verify_arguments)
    read_call = json.dumps(read_arguments)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", verify_name, verify_call)])),
            _completion(_assistant_tool_call_message([("c2", read_name, read_call)])),
            _completion(_assistant_final_message("too early")),
            _completion(_assistant_tool_call_message([("c3", verify_name, verify_call)])),
            _completion(_assistant_final_message("checked")),
        ],
        tool_responses={
            verify_path: [
                {"ok": False, "stdout": "", "stderr": "compile error in src/a.cpp", "exit_code": 1},
                {"ok": True, "stdout": "verified", "stderr": "", "exit_code": 0},
            ]
        },
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "Does it build?", REQUEST_ID, max_iterations=8, on_event=on_event
        )

    assert result["answer"] == "checked"
    assert sidecar.calls == [(read_name, read_arguments, REQUEST_ID)]
    assert scripted.destinations == [
        f"http://tools-test:8091{verify_path}",
        f"http://tools-test:8091{verify_path}",
    ]
    note = _failure_notes(scripted.llm_payloads[3])[0]
    assert note.splitlines()[1] == verify_name
    assert "compile error in src/a.cpp" in note
    sent = "\n".join(message.get("content") or "" for message in scripted.llm_payloads[3]["messages"])
    assert "too early" not in sent
    nudges = [payload for name, payload in events if name == "nudge"]
    assert nudges[0] == {"reason": "tool_failed", "tool": verify_name, "remaining": 2}
    assert result["tool_calls"][1]["name"] == read_name
    assert result["tool_calls"][1]["ok"] is True
    assert ("tool_call", {"name": read_name, "arguments": read_arguments}) in events
    assert any(
        name == "tool_result" and payload.get("name") == read_name and payload.get("ok") is True
        for name, payload in events
    )
