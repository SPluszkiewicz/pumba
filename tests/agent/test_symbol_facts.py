"""Symbol facts are named tools on the sidecar, beside the clangd tools.

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

REQUEST_ID = "req-symbol-facts"
DECLARATION_TEXT = "Widget declared in src/widget.hpp:12"

_LSP_TOOLS = (
    "lsp_calls",
    "lsp_types",
)
_RETIRED_LSP = (
    "lsp_definition",
    "lsp_references",
    "lsp_hover",
    "lsp_symbols",
    "lsp_outline",
)

_NOT_NAMED = ("onboarding", "initial_instructions", "query_project")


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
    def __init__(self, llm_completions: list[dict[str, Any]]) -> None:
        self.llm_completions = llm_completions
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
        return httpx.Response(200, json={"ok": True, "stdout": "", "stderr": "", "exit_code": 0})

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

    def __init__(self, result: Optional[dict[str, Any]] = None) -> None:
        self.result = result or {"ok": True, "stdout": DECLARATION_TEXT, "stderr": "", "exit_code": 0}
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    async def call(self, name: str, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        self.calls.append((name, arguments, request_id))
        return self.result


async def test_find_declaration_is_offered_under_serenas_name() -> None:
    """The page and the CLI share this name and these argument names."""
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_final_message("No declaration yet.")),
            _completion(_assistant_final_message("Still no declaration.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Where is Widget declared?", REQUEST_ID)

    assert result["answer"] == "Still no declaration."
    offered = _offered_tools(scripted.llm_payloads[0])
    names = [tool["function"]["name"] for tool in offered]
    assert names.count("find_declaration") == 1
    assert "serena_find_declaration" not in names
    spec = next(tool["function"] for tool in offered if tool["function"]["name"] == "find_declaration")
    assert spec["parameters"]["required"] == ["relative_path", "regex"]
    assert set(spec["parameters"]["properties"]) == {
        "relative_path",
        "regex",
        "containing_symbol_name_path",
        "include_body",
        "include_info",
    }
    for prop in spec["parameters"]["properties"].values():
        assert "description" in prop
        assert "title" not in prop
    for name in _LSP_TOOLS:
        assert name in names
    for name in _RETIRED_LSP:
        assert name not in names
    for name in _NOT_NAMED:
        assert name not in names


@pytest.mark.asyncio
async def test_find_declaration_runs_on_the_sidecar_and_streams_its_text(monkeypatch: pytest.MonkeyPatch) -> None:
    arguments = {"relative_path": "src/widget.hpp", "regex": "class (Widget)"}
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    events, on_event = _listen()
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "find_declaration", json.dumps(arguments))])),
            _completion(_assistant_final_message("Widget is declared in the header.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "Where is Widget declared?", REQUEST_ID, on_event=on_event
        )

    assert sidecar.calls == [("find_declaration", arguments, REQUEST_ID)]
    assert scripted.destinations == []
    assert result["tool_calls"] == [
        {
            "name": "find_declaration",
            "arguments": arguments,
            "result_summary": "find_declaration succeeded: Widget declared in src/widget.hpp:12",
            "ok": True,
            "line": DECLARATION_TEXT,
        }
    ]
    assert ("tool_call", {"name": "find_declaration", "arguments": arguments}) in events
    assert (
        "tool_result",
        {
            "name": "find_declaration",
            "result_summary": "find_declaration succeeded: Widget declared in src/widget.hpp:12",
            "ok": True,
        },
    ) in events
    assert result["answer"] == "Widget is declared in the header."


async def test_find_referencing_symbols_is_offered_under_serenas_name() -> None:
    """References use Serena's name_path and relative_path, not an alias."""
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_final_message("No references yet.")),
            _completion(_assistant_final_message("Still no references.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Who uses Widget?", REQUEST_ID)

    assert result["answer"] == "Still no references."
    offered = _offered_tools(scripted.llm_payloads[0])
    names = [tool["function"]["name"] for tool in offered]
    assert names.count("find_referencing_symbols") == 1
    spec = next(tool["function"] for tool in offered if tool["function"]["name"] == "find_referencing_symbols")
    assert spec["parameters"]["required"] == ["name_path", "relative_path"]
    assert set(spec["parameters"]["properties"]) == {
        "name_path",
        "relative_path",
        "include_kinds",
        "exclude_kinds",
        "max_answer_chars",
    }
    for prop in spec["parameters"]["properties"].values():
        assert "description" in prop
        assert "title" not in prop


REFERENCES_TEXT = "Widget referenced in src/use.cpp:8"


@pytest.mark.asyncio
async def test_find_referencing_symbols_runs_on_the_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    arguments = {"name_path": "Widget", "relative_path": "src/widget.hpp"}
    sidecar = RecordingSidecar(result={"ok": True, "stdout": REFERENCES_TEXT, "stderr": "", "exit_code": 0})
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    events, on_event = _listen()
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "find_referencing_symbols", json.dumps(arguments))])),
            _completion(_assistant_final_message("Widget is used in use.cpp.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Who uses Widget?", REQUEST_ID, on_event=on_event)

    assert sidecar.calls == [("find_referencing_symbols", arguments, REQUEST_ID)]
    assert scripted.destinations == []
    assert result["tool_calls"][0]["name"] == "find_referencing_symbols"
    assert result["tool_calls"][0]["ok"] is True
    assert result["tool_calls"][0]["line"] == REFERENCES_TEXT
    assert ("tool_call", {"name": "find_referencing_symbols", "arguments": arguments}) in events
    assert (
        "tool_result",
        {
            "name": "find_referencing_symbols",
            "result_summary": f"find_referencing_symbols succeeded: {REFERENCES_TEXT}",
            "ok": True,
        },
    ) in events


async def test_find_implementations_is_offered_under_serenas_name() -> None:
    """Implementations use Serena's name_path and relative_path."""
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_final_message("No implementations yet.")),
            _completion(_assistant_final_message("Still no implementations.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "What implements Widget?", REQUEST_ID)

    assert result["answer"] == "Still no implementations."
    offered = _offered_tools(scripted.llm_payloads[0])
    names = [tool["function"]["name"] for tool in offered]
    assert names.count("find_implementations") == 1
    spec = next(tool["function"] for tool in offered if tool["function"]["name"] == "find_implementations")
    assert spec["parameters"]["required"] == ["name_path", "relative_path"]
    assert set(spec["parameters"]["properties"]) == {
        "name_path",
        "relative_path",
        "include_info",
        "include_kinds",
        "exclude_kinds",
        "max_answer_chars",
    }
    for prop in spec["parameters"]["properties"].values():
        assert "description" in prop
        assert "title" not in prop


IMPLEMENTATIONS_TEXT = "Widget implemented in src/button.cpp:15"


@pytest.mark.asyncio
async def test_find_implementations_runs_on_the_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    arguments = {"name_path": "Widget", "relative_path": "src/widget.hpp"}
    sidecar = RecordingSidecar(result={"ok": True, "stdout": IMPLEMENTATIONS_TEXT, "stderr": "", "exit_code": 0})
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    events, on_event = _listen()
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "find_implementations", json.dumps(arguments))])),
            _completion(_assistant_final_message("Button implements Widget.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "What implements Widget?", REQUEST_ID, on_event=on_event
        )

    assert sidecar.calls == [("find_implementations", arguments, REQUEST_ID)]
    assert scripted.destinations == []
    assert result["tool_calls"][0]["name"] == "find_implementations"
    assert result["tool_calls"][0]["ok"] is True
    assert result["tool_calls"][0]["line"] == IMPLEMENTATIONS_TEXT
    assert ("tool_call", {"name": "find_implementations", "arguments": arguments}) in events
    assert (
        "tool_result",
        {
            "name": "find_implementations",
            "result_summary": "find_implementations succeeded: Widget implemented in src/button.cpp:15",
            "ok": True,
        },
    ) in events


async def test_get_symbols_overview_is_offered_under_serenas_name() -> None:
    """A file overview uses Serena's relative_path, and depth defaults to -1."""
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_final_message("No overview yet.")),
            _completion(_assistant_final_message("Still no overview.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "What is in widget.hpp?", REQUEST_ID)

    assert result["answer"] == "Still no overview."
    offered = _offered_tools(scripted.llm_payloads[0])
    names = [tool["function"]["name"] for tool in offered]
    assert names.count("get_symbols_overview") == 1
    spec = next(tool["function"] for tool in offered if tool["function"]["name"] == "get_symbols_overview")
    assert spec["parameters"]["required"] == ["relative_path"]
    assert set(spec["parameters"]["properties"]) == {"relative_path", "depth", "max_answer_chars"}
    assert spec["parameters"]["properties"]["depth"]["default"] == -1
    for prop in spec["parameters"]["properties"].values():
        assert "description" in prop
        assert "title" not in prop


OVERVIEW_TEXT = "class Widget in src/widget.hpp"


@pytest.mark.asyncio
async def test_get_symbols_overview_runs_on_the_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    arguments = {"relative_path": "src/widget.hpp"}
    sidecar = RecordingSidecar(result={"ok": True, "stdout": OVERVIEW_TEXT, "stderr": "", "exit_code": 0})
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    events, on_event = _listen()
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "get_symbols_overview", json.dumps(arguments))])),
            _completion(_assistant_final_message("The header declares Widget.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "What is in widget.hpp?", REQUEST_ID, on_event=on_event
        )

    assert sidecar.calls == [("get_symbols_overview", arguments, REQUEST_ID)]
    assert scripted.destinations == []
    assert result["tool_calls"][0]["name"] == "get_symbols_overview"
    assert result["tool_calls"][0]["ok"] is True
    assert result["tool_calls"][0]["line"] == OVERVIEW_TEXT
    assert ("tool_call", {"name": "get_symbols_overview", "arguments": arguments}) in events
    assert (
        "tool_result",
        {
            "name": "get_symbols_overview",
            "result_summary": "get_symbols_overview succeeded: class Widget in src/widget.hpp",
            "ok": True,
        },
    ) in events


async def test_get_diagnostics_for_file_is_offered_under_serenas_name() -> None:
    """File diagnostics use Serena's line window and severity, not an alias."""
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_final_message("No diagnostics yet.")),
            _completion(_assistant_final_message("Still no diagnostics.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "What is wrong in widget.hpp?", REQUEST_ID)

    assert result["answer"] == "Still no diagnostics."
    offered = _offered_tools(scripted.llm_payloads[0])
    names = [tool["function"]["name"] for tool in offered]
    assert names.count("get_diagnostics_for_file") == 1
    spec = next(tool["function"] for tool in offered if tool["function"]["name"] == "get_diagnostics_for_file")
    properties = spec["parameters"]["properties"]
    assert spec["parameters"]["required"] == ["relative_path"]
    assert set(properties) == {"relative_path", "start_line", "end_line", "min_severity", "max_answer_chars"}
    assert properties["start_line"]["default"] == 0
    assert properties["end_line"]["default"] == -1
    assert properties["min_severity"]["default"] == 4
    for prop in properties.values():
        assert "description" in prop
        assert "title" not in prop


FILE_DIAGNOSTICS_TEXT = "error: unknown type name 'Widget' at src/widget.hpp:4"


@pytest.mark.asyncio
async def test_get_diagnostics_for_file_runs_on_the_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    arguments = {"relative_path": "src/widget.hpp"}
    sidecar = RecordingSidecar(result={"ok": True, "stdout": FILE_DIAGNOSTICS_TEXT, "stderr": "", "exit_code": 0})
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    events, on_event = _listen()
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "get_diagnostics_for_file", json.dumps(arguments))])),
            _completion(_assistant_final_message("The header has one error.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "What is wrong in widget.hpp?", REQUEST_ID, on_event=on_event
        )

    assert sidecar.calls == [("get_diagnostics_for_file", arguments, REQUEST_ID)]
    assert scripted.destinations == []
    assert result["tool_calls"][0]["name"] == "get_diagnostics_for_file"
    assert result["tool_calls"][0]["ok"] is True
    assert result["tool_calls"][0]["line"] == FILE_DIAGNOSTICS_TEXT
    assert ("tool_call", {"name": "get_diagnostics_for_file", "arguments": arguments}) in events
    assert (
        "tool_result",
        {
            "name": "get_diagnostics_for_file",
            "result_summary": (
                "get_diagnostics_for_file succeeded: error: unknown type name 'Widget' at src/widget.hpp:4"
            ),
            "ok": True,
        },
    ) in events


async def test_get_diagnostics_for_symbol_is_offered_under_serenas_name() -> None:
    """Symbol diagnostics take Serena's reference_file, not relative_path."""
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_final_message("No symbol diagnostics yet.")),
            _completion(_assistant_final_message("Still no symbol diagnostics.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "What is wrong with Widget?", REQUEST_ID)

    assert result["answer"] == "Still no symbol diagnostics."
    offered = _offered_tools(scripted.llm_payloads[0])
    names = [tool["function"]["name"] for tool in offered]
    assert names.count("get_diagnostics_for_symbol") == 1
    spec = next(tool["function"] for tool in offered if tool["function"]["name"] == "get_diagnostics_for_symbol")
    properties = spec["parameters"]["properties"]
    assert spec["parameters"]["required"] == ["name_path"]
    assert set(properties) == {
        "name_path",
        "reference_file",
        "check_symbol_references",
        "min_severity",
        "max_answer_chars",
    }
    assert "relative_path" not in properties
    assert properties["reference_file"]["default"] == ""
    assert properties["check_symbol_references"]["default"] is False
    assert properties["min_severity"]["default"] == 4
    for prop in properties.values():
        assert "description" in prop
        assert "title" not in prop


SYMBOL_DIAGNOSTICS_TEXT = "error: Widget has no member 'draw' at src/widget.hpp:9"


@pytest.mark.asyncio
async def test_get_diagnostics_for_symbol_runs_on_the_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    arguments = {"name_path": "Widget", "reference_file": "src/widget.hpp"}
    sidecar = RecordingSidecar(result={"ok": True, "stdout": SYMBOL_DIAGNOSTICS_TEXT, "stderr": "", "exit_code": 0})
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    events, on_event = _listen()
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(
                _assistant_tool_call_message([("call_1", "get_diagnostics_for_symbol", json.dumps(arguments))])
            ),
            _completion(_assistant_final_message("Widget has one error.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "What is wrong with Widget?", REQUEST_ID, on_event=on_event
        )

    assert sidecar.calls == [("get_diagnostics_for_symbol", arguments, REQUEST_ID)]
    assert scripted.destinations == []
    assert result["tool_calls"][0]["name"] == "get_diagnostics_for_symbol"
    assert result["tool_calls"][0]["ok"] is True
    assert result["tool_calls"][0]["line"] == SYMBOL_DIAGNOSTICS_TEXT
    assert ("tool_call", {"name": "get_diagnostics_for_symbol", "arguments": arguments}) in events
    assert (
        "tool_result",
        {
            "name": "get_diagnostics_for_symbol",
            "result_summary": (
                "get_diagnostics_for_symbol succeeded: error: Widget has no member 'draw' at src/widget.hpp:9"
            ),
            "ok": True,
        },
    ) in events


async def test_restart_language_server_is_offered_under_serenas_name() -> None:
    """Restart takes no arguments. It is not aliased."""
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_final_message("Not restarted yet.")),
            _completion(_assistant_final_message("Still not restarted.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Restart clangd.", REQUEST_ID)

    assert result["answer"] == "Still not restarted."
    offered = _offered_tools(scripted.llm_payloads[0])
    names = [tool["function"]["name"] for tool in offered]
    assert names.count("restart_language_server") == 1
    spec = next(tool["function"] for tool in offered if tool["function"]["name"] == "restart_language_server")
    assert spec["parameters"]["required"] == []
    assert spec["parameters"]["properties"] == {}


RESTART_TEXT = "language server restarted"


@pytest.mark.asyncio
async def test_restart_language_server_runs_on_the_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar = RecordingSidecar(result={"ok": True, "stdout": RESTART_TEXT, "stderr": "", "exit_code": 0})
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    events, on_event = _listen()
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "restart_language_server", "{}")])),
            _completion(_assistant_final_message("Clangd was restarted.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Restart clangd.", REQUEST_ID, on_event=on_event)

    assert sidecar.calls == [("restart_language_server", {}, REQUEST_ID)]
    assert scripted.destinations == []
    assert result["tool_calls"][0]["name"] == "restart_language_server"
    assert result["tool_calls"][0]["ok"] is True
    assert result["tool_calls"][0]["line"] == RESTART_TEXT
    assert ("tool_call", {"name": "restart_language_server", "arguments": {}}) in events
    assert (
        "tool_result",
        {
            "name": "restart_language_server",
            "result_summary": "restart_language_server succeeded: language server restarted",
            "ok": True,
        },
    ) in events


@pytest.fixture
def registered_checkout():
    """A direct child of /checkouts, the only mount the turn may use."""
    root = (Path("/checkouts") / "co_serena_03").resolve()
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
    ("reference_file", "refusal"),
    [
        (".git/config", "path must not pass through .git, .svn, or .env: '.git/config'"),
        ("src/.svn/entries", "path must not pass through .git, .svn, or .env: 'src/.svn/entries'"),
        (".env", "path must not pass through .git, .svn, or .env: '.env'"),
        ("../outside.cpp", "path must not contain '..' traversal: '../outside.cpp'"),
    ],
)
@pytest.mark.asyncio
async def test_forbidden_reference_file_never_reaches_the_sidecar(
    monkeypatch: pytest.MonkeyPatch, registered_checkout: Path, reference_file: str, refusal: str
) -> None:
    """Symbol diagnostics name the file reference_file. The same path rules still apply."""
    assert registered_checkout.is_dir()
    monkeypatch.setenv("AGENT_LOOP_NUDGES", "0")
    arguments = {"name_path": "Widget", "reference_file": reference_file}
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(
                _assistant_tool_call_message([("call_1", "get_diagnostics_for_symbol", json.dumps(arguments))])
            ),
            _completion(_assistant_final_message("The path was refused.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "What is wrong with Widget?", REQUEST_ID)

    assert sidecar.calls == []
    assert result["answer"] == "The path was refused."
    assert result["tool_calls"] == [
        {
            "name": "get_diagnostics_for_symbol",
            "arguments": arguments,
            "result_summary": f"get_diagnostics_for_symbol failed: {refusal}",
            "ok": False,
            "line": refusal,
        }
    ]


@pytest.mark.parametrize(
    ("relative_path", "refusal"),
    [
        (".git/config", "path must not pass through .git, .svn, or .env: '.git/config'"),
        ("../outside.cpp", "path must not contain '..' traversal: '../outside.cpp'"),
    ],
)
@pytest.mark.asyncio
async def test_forbidden_relative_path_on_find_declaration_never_reaches_the_sidecar(
    monkeypatch: pytest.MonkeyPatch, registered_checkout: Path, relative_path: str, refusal: str
) -> None:
    """Declaration takes relative_path. A forbidden path is refused before the sidecar."""
    assert registered_checkout.is_dir()
    monkeypatch.setenv("AGENT_LOOP_NUDGES", "0")
    arguments = {"relative_path": relative_path, "regex": "class (Widget)"}
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "find_declaration", json.dumps(arguments))])),
            _completion(_assistant_final_message("The path was refused.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Where is Widget declared?", REQUEST_ID)

    assert sidecar.calls == []
    assert result["tool_calls"][0]["name"] == "find_declaration"
    assert result["tool_calls"][0]["ok"] is False
    assert result["tool_calls"][0]["line"] == refusal


OVERSIZED_REFUSAL = "max_answer_chars exceeds the 262144 character cap"


@pytest.mark.asyncio
async def test_oversized_overview_is_refused_before_the_sidecar(
    monkeypatch: pytest.MonkeyPatch, registered_checkout: Path
) -> None:
    """The answer cap from the path checks applies to a symbol-fact call."""
    assert registered_checkout.is_dir()
    monkeypatch.setenv("AGENT_LOOP_NUDGES", "0")
    arguments = {"relative_path": "src/widget.hpp", "max_answer_chars": 262145}
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("call_1", "get_symbols_overview", json.dumps(arguments))])),
            _completion(_assistant_final_message("The answer was refused.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "What is in widget.hpp?", REQUEST_ID)

    assert sidecar.calls == []
    assert result["tool_calls"] == [
        {
            "name": "get_symbols_overview",
            "arguments": arguments,
            "result_summary": f"get_symbols_overview failed: {OVERSIZED_REFUSAL}",
            "ok": False,
            "line": OVERSIZED_REFUSAL,
        }
    ]
