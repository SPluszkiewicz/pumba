"""rename_symbol is a named tool on the sidecar and opens editing.

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

REQUEST_ID = "req-rename-symbol"
RENAME_TEXT = "Renamed Widget to Gadget in src/widget.hpp"
RENAME_ARGUMENTS = {
    "name_path": "Widget",
    "relative_path": "src/widget.hpp",
    "new_name": "Gadget",
}
_STILL_PRESENT = ("begin_plan", "edit", "apply")
_NOT_ON_THIS_PATH = (
    "commit",
    "git_commit",
    "push",
    "git_push",
)


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
        self.result = result or {"ok": True, "stdout": RENAME_TEXT, "stderr": "", "exit_code": 0}
        self._results = list(results) if results is not None else None
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    async def call(self, name: str, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        self.calls.append((name, arguments, request_id))
        if self._results is not None:
            return self._results.pop(0)
        return self.result


@pytest.mark.asyncio
async def test_rename_symbol_runs_on_the_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    """The page and the CLI share Serena's name. The call runs on the sidecar and does not commit."""
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(
                _assistant_tool_call_message([("call_1", "rename_symbol", json.dumps(RENAME_ARGUMENTS))])
            ),
            _completion(_assistant_final_message("Widget is now Gadget.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Rename Widget to Gadget.", REQUEST_ID)

    offered = [tool["function"]["name"] for tool in scripted.llm_payloads[0]["tools"]]
    assert offered.count("rename_symbol") == 1
    spec = next(
        tool["function"]
        for tool in scripted.llm_payloads[0]["tools"]
        if tool["function"]["name"] == "rename_symbol"
    )
    assert spec["parameters"]["required"] == ["name_path", "relative_path", "new_name"]
    assert set(spec["parameters"]["properties"]) == {"name_path", "relative_path", "new_name"}
    for prop in spec["parameters"]["properties"].values():
        assert "description" in prop
        assert "title" not in prop
    for name in _STILL_PRESENT:
        assert name not in offered
    for name in _NOT_ON_THIS_PATH:
        assert name not in offered
    assert sidecar.calls == [("rename_symbol", RENAME_ARGUMENTS, REQUEST_ID)]
    assert scripted.destinations == []
    assert result["tool_calls"] == [
        {
            "name": "rename_symbol",
            "arguments": RENAME_ARGUMENTS,
            "result_summary": f"rename_symbol succeeded: {RENAME_TEXT}",
            "ok": True,
            "line": RENAME_TEXT,
        }
    ]


@pytest.mark.asyncio
async def test_successful_rename_blocks_verification_until_build_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful rename enters editing. build, test, and compile_file wait for build_step."""
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", "rename_symbol", json.dumps(RENAME_ARGUMENTS))])),
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
            _completion(_assistant_final_message("The rename builds.")),
        ],
        tool_responses={
            "/build-step": {"ok": True, "stdout": "built", "stderr": "", "exit_code": 0},
            "/build": {"ok": True, "stdout": "build succeeded", "stderr": "", "exit_code": 0},
        },
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "Rename Widget to Gadget.", REQUEST_ID, max_iterations=8
        )

    assert sidecar.calls == [("rename_symbol", RENAME_ARGUMENTS, REQUEST_ID)]
    assert [call["name"] for call in result["tool_calls"]] == [
        "rename_symbol",
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


@pytest.mark.asyncio
async def test_failed_rename_does_not_change_the_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rename the sidecar rejects leaves the turn idle, so build still runs."""
    sidecar = RecordingSidecar(
        result={"ok": False, "stdout": "", "stderr": "clangd refused the rename", "exit_code": 1}
    )
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", "rename_symbol", json.dumps(RENAME_ARGUMENTS))])),
            _completion(_assistant_tool_call_message([("c2", "build", '{"preset": "release"}')])),
            _completion(_assistant_final_message("The rename did not land.")),
        ],
        tool_responses={"/build": {"ok": True, "stdout": "build succeeded", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "Rename Widget to Gadget.", REQUEST_ID, max_iterations=8
        )

    assert sidecar.calls == [("rename_symbol", RENAME_ARGUMENTS, REQUEST_ID)]
    assert result["tool_calls"][0]["name"] == "rename_symbol"
    assert result["tool_calls"][0]["ok"] is False
    assert result["tool_calls"][0]["line"] == "clangd refused the rename"
    assert result["tool_calls"][1]["name"] == "build"
    assert result["tool_calls"][1]["ok"] is True
    assert scripted.phases[1] == "idle"
    assert edit_policy.PHASE.get() == "idle"
    assert scripted.destinations == ["http://tools-test:8091/build"]


@pytest.mark.asyncio
async def test_replayed_rename_does_not_change_the_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replaying a rename after build_step stays idle and does not call the sidecar again."""
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", "rename_symbol", json.dumps(RENAME_ARGUMENTS))])),
            _completion(_assistant_tool_call_message([("c2", "build_step", '{"target": "pumba"}')])),
            _completion(_assistant_tool_call_message([("c3", "rename_symbol", json.dumps(RENAME_ARGUMENTS))])),
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
            client, "system", "Rename Widget to Gadget.", REQUEST_ID, max_iterations=8
        )

    assert sidecar.calls == [("rename_symbol", RENAME_ARGUMENTS, REQUEST_ID)]
    assert [call["ok"] for call in result["tool_calls"]] == [True, True, True, True]
    assert result["tool_calls"][2]["name"] == "rename_symbol"
    replayed = [
        message.get("content") or ""
        for message in scripted.llm_payloads[3]["messages"]
        if message.get("role") == "tool"
        and str(message.get("content", "")).startswith("Same arguments as an earlier call. The result is unchanged.")
    ]
    assert len(replayed) == 1
    assert scripted.phases[3] == "idle"
    assert edit_policy.PHASE.get() == "idle"
    assert scripted.destinations == [
        "http://tools-test:8091/build-step",
        "http://tools-test:8091/build",
    ]


_FIND_ARGUMENTS = {"name_path_pattern": "Widget"}
_FIND_BEFORE = "Widget declared in src/widget.hpp:4"
_FIND_AFTER = "Gadget declared in src/widget.hpp:4"


@pytest.mark.asyncio
async def test_successful_rename_clears_the_success_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """A later read of the same symbol is not the cached text from before the rename."""
    sidecar = RecordingSidecar(
        results=[
            {"ok": True, "stdout": _FIND_BEFORE, "stderr": "", "exit_code": 0},
            {"ok": True, "stdout": RENAME_TEXT, "stderr": "", "exit_code": 0},
            {"ok": True, "stdout": _FIND_AFTER, "stderr": "", "exit_code": 0},
        ]
    )
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", "find_symbol", json.dumps(_FIND_ARGUMENTS))])),
            _completion(_assistant_tool_call_message([("c2", "rename_symbol", json.dumps(RENAME_ARGUMENTS))])),
            _completion(_assistant_tool_call_message([("c3", "find_symbol", json.dumps(_FIND_ARGUMENTS))])),
            _completion(_assistant_tool_call_message([("c4", "build_step", '{"target": "pumba"}')])),
            _completion(_assistant_final_message("Gadget is the name now.")),
        ],
        tool_responses={"/build-step": {"ok": True, "stdout": "built", "stderr": "", "exit_code": 0}},
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(
            client, "system", "Rename Widget to Gadget.", REQUEST_ID, max_iterations=8
        )

    assert [name for name, _arguments, _request_id in sidecar.calls] == [
        "find_symbol",
        "rename_symbol",
        "find_symbol",
    ]
    assert result["tool_calls"][0]["name"] == "find_symbol"
    assert result["tool_calls"][0]["ok"] is True
    assert result["tool_calls"][0]["line"] == _FIND_BEFORE
    assert result["tool_calls"][1]["name"] == "rename_symbol"
    assert result["tool_calls"][1]["ok"] is True
    assert result["tool_calls"][2]["name"] == "find_symbol"
    assert result["tool_calls"][2]["ok"] is True
    assert result["tool_calls"][2]["line"] == _FIND_AFTER
    assert scripted.destinations == ["http://tools-test:8091/build-step"]


def _rename_arguments(relative_path: str) -> dict[str, str]:
    return {"name_path": "Widget", "relative_path": relative_path, "new_name": "Gadget"}


@pytest.fixture
def registered_checkout():
    """A direct child of /checkouts, the only mount the turn may use."""
    root = (Path("/checkouts") / "co_serena_05").resolve()
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
async def test_rename_outside_the_checkout_never_reaches_the_sidecar(
    monkeypatch: pytest.MonkeyPatch,
    registered_checkout: Path,
    relative_path: str,
    refusal: str,
) -> None:
    """A rename through a forbidden path fails closed. The sidecar is not called."""
    assert registered_checkout.is_dir()
    monkeypatch.setenv("AGENT_LOOP_NUDGES", "0")
    arguments = _rename_arguments(relative_path)
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", "rename_symbol", json.dumps(arguments))])),
            _completion(_assistant_final_message("The rename was refused.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Rename Widget to Gadget.", REQUEST_ID)

    assert sidecar.calls == []
    assert scripted.destinations == []
    assert result["tool_calls"] == [
        {
            "name": "rename_symbol",
            "arguments": arguments,
            "result_summary": f"rename_symbol failed: {refusal}",
            "ok": False,
            "line": refusal,
        }
    ]
    assert scripted.phases[1] == "idle"
    assert edit_policy.PHASE.get() == "idle"


@pytest.mark.asyncio
async def test_rename_inside_the_checkout_reaches_the_sidecar(
    monkeypatch: pytest.MonkeyPatch, registered_checkout: Path
) -> None:
    """A file inside the registered checkout still runs on the sidecar."""
    assert registered_checkout.is_dir()
    arguments = _rename_arguments("src/widget.hpp")
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(_assistant_tool_call_message([("c1", "rename_symbol", json.dumps(arguments))])),
            _completion(_assistant_final_message("Widget is now Gadget.")),
        ]
    )

    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Rename Widget to Gadget.", REQUEST_ID)

    assert sidecar.calls == [("rename_symbol", arguments, REQUEST_ID)]
    assert scripted.destinations == []
    assert result["tool_calls"][0]["name"] == "rename_symbol"
    assert result["tool_calls"][0]["ok"] is True
    assert scripted.phases[1] == "editing"
