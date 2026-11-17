"""find_symbol stays inside a registered checkout.

The agent checks the path before the sidecar transport. These tests inject
the fake sidecar at tool_loop.sidecar and never speak MCP.
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

REQUEST_ID = "req-refuse-path"
OUTSIDE_PATH = "../outside.cpp"
OUTSIDE_REFUSAL = "path must not contain '..' traversal: '../outside.cpp'"


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


class RecordingSidecar:
    def __init__(self, result: Optional[dict[str, Any]] = None) -> None:
        self.result = result or {
            "ok": True,
            "stdout": "Widget declared in src/widget.hpp:4",
            "stderr": "",
            "exit_code": 0,
        }
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    async def call(self, name: str, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        self.calls.append((name, arguments, request_id))
        return self.result


@pytest.fixture
def registered_checkout():
    """A direct child of /checkouts, the only mount the turn may use."""
    root = (Path("/checkouts") / "co_serena_02").resolve()
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


async def _ask(
    monkeypatch: pytest.MonkeyPatch,
    arguments: dict[str, Any],
    *,
    answer: str = "The path was refused.",
) -> tuple[RecordingSidecar, dict[str, Any]]:
    monkeypatch.setenv("AGENT_LOOP_NUDGES", "0")
    sidecar = RecordingSidecar()
    monkeypatch.setattr(tool_loop, "sidecar", sidecar)
    scripted = ScriptedTransport(
        llm_completions=[
            _completion(
                _assistant_tool_call_message([("call_1", "find_symbol", json.dumps(arguments))])
            ),
            _completion(_assistant_final_message(answer)),
        ]
    )
    async with scripted.client() as client:
        result = await tool_loop.run_tool_loop(client, "system", "Where is Widget?", REQUEST_ID)
    return sidecar, result


@pytest.mark.asyncio
async def test_path_outside_the_checkout_never_reaches_the_sidecar(
    monkeypatch: pytest.MonkeyPatch, registered_checkout: Path
) -> None:
    """A relative path that leaves the registered checkout is refused."""
    assert registered_checkout.is_dir()
    sidecar, result = await _ask(
        monkeypatch,
        {"name_path_pattern": "Widget", "relative_path": OUTSIDE_PATH},
    )

    _assert_refused(
        sidecar,
        result,
        {"name_path_pattern": "Widget", "relative_path": OUTSIDE_PATH},
        OUTSIDE_REFUSAL,
    )


def _assert_refused(
    sidecar: RecordingSidecar,
    result: dict[str, Any],
    arguments: dict[str, Any],
    refusal: str,
) -> None:
    assert sidecar.calls == []
    assert result["answer"] == "The path was refused."
    assert result["tool_calls"] == [
        {
            "name": "find_symbol",
            "arguments": arguments,
            "result_summary": f"find_symbol failed: {refusal}",
            "ok": False,
            "line": refusal,
        }
    ]


@pytest.mark.parametrize(
    ("kind", "refusal"),
    [
        ("existing", "X-Project-Root must be a direct child of /checkouts"),
        ("missing", "X-Project-Root is not a mounted checkout"),
    ],
)
@pytest.mark.asyncio
async def test_unknown_host_path_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str, refusal: str
) -> None:
    """A turn whose checkout is not a registered mount never calls Serena."""
    host = tmp_path if kind == "existing" else tmp_path / "missing-host"
    token = project_root_header.set(str(host))
    try:
        sidecar, result = await _ask(monkeypatch, {"name_path_pattern": "Widget"})
    finally:
        project_root_header.reset(token)

    _assert_refused(sidecar, result, {"name_path_pattern": "Widget"}, refusal)


@pytest.mark.parametrize(
    ("relative_path", "refusal"),
    [
        (".git/config", "path must not pass through .git, .svn, or .env: '.git/config'"),
        ("src/.svn/entries", "path must not pass through .git, .svn, or .env: 'src/.svn/entries'"),
        (".env", "path must not pass through .git, .svn, or .env: '.env'"),
        (
            "secrets/.env.local",
            "path must not pass through .git, .svn, or .env: 'secrets/.env.local'",
        ),
        (
            "src\\.git\\config",
            "path must not pass through .git, .svn, or .env: 'src\\\\.git\\\\config'",
        ),
    ],
)
@pytest.mark.asyncio
async def test_forbidden_segment_never_reaches_the_sidecar(
    monkeypatch: pytest.MonkeyPatch, registered_checkout: Path, relative_path: str, refusal: str
) -> None:
    """`.git`, `.svn`, and `.env` stay out of Serena even when they sit inside the checkout."""
    assert registered_checkout.is_dir()
    arguments = {"name_path_pattern": "Widget", "relative_path": relative_path}
    sidecar, result = await _ask(monkeypatch, arguments)
    _assert_refused(sidecar, result, arguments, refusal)


@pytest.mark.parametrize(
    ("relative_path", "refusal"),
    [
        ("/etc/passwd", "path must be relative to the project root, not absolute: '/etc/passwd'"),
        (
            "C:/Windows/system.ini",
            "path must not be an absolute/drive-anchored path: 'C:/Windows/system.ini'",
        ),
    ],
)
@pytest.mark.asyncio
async def test_absolute_path_never_reaches_the_sidecar(
    monkeypatch: pytest.MonkeyPatch, registered_checkout: Path, relative_path: str, refusal: str
) -> None:
    """An absolute or drive-anchored path is outside the checkout."""
    assert registered_checkout.is_dir()
    arguments = {"name_path_pattern": "Widget", "relative_path": relative_path}
    sidecar, result = await _ask(monkeypatch, arguments)
    _assert_refused(sidecar, result, arguments, refusal)


OVERSIZED_REFUSAL = "max_answer_chars exceeds the 262144 character cap"


@pytest.mark.asyncio
async def test_oversized_answer_is_refused_before_the_sidecar(
    monkeypatch: pytest.MonkeyPatch, registered_checkout: Path
) -> None:
    """A find_symbol that asks for more than the agent cap never reaches Serena."""
    assert registered_checkout.is_dir()
    arguments = {
        "name_path_pattern": "Widget",
        "relative_path": "src/widget.hpp",
        "max_answer_chars": 262145,
    }
    sidecar, result = await _ask(monkeypatch, arguments)
    _assert_refused(sidecar, result, arguments, OVERSIZED_REFUSAL)


@pytest.mark.asyncio
async def test_a_call_inside_the_checkout_reaches_the_sidecar(
    monkeypatch: pytest.MonkeyPatch, registered_checkout: Path
) -> None:
    """A path inside the checkout, at the answer cap, still runs on Serena."""
    assert registered_checkout.is_dir()
    arguments = {
        "name_path_pattern": "Widget",
        "relative_path": "src/widget.hpp",
        "max_answer_chars": 262144,
    }
    sidecar, result = await _ask(monkeypatch, arguments, answer="Widget is declared in the header.")

    assert sidecar.calls == [("find_symbol", arguments, REQUEST_ID)]
    assert result["answer"] == "Widget is declared in the header."
    assert result["tool_calls"] == [
        {
            "name": "find_symbol",
            "arguments": arguments,
            "result_summary": "find_symbol succeeded: Widget declared in src/widget.hpp:4",
            "ok": True,
            "line": "Widget declared in src/widget.hpp:4",
        }
    ]
