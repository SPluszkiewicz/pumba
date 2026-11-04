"""Compilation stays blocked until the plan step calls build_step."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_AGENT = _REPO / "services" / "agent"
for _path in (str(_REPO), str(_AGENT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import edit_policy
import tool_loop


def setup_function() -> None:
    edit_policy.reset_edit_phase()


def teardown_function() -> None:
    edit_policy.reset_edit_phase()


def test_build_is_blocked_while_edits_are_open() -> None:
    edit_policy.observe_tool("begin_plan")
    assert "build_step" in (edit_policy.gate_tool("build") or "")
    assert "build_step" in (edit_policy.gate_tool("compile_file") or "")
    assert edit_policy.gate_tool("edit") is None
    edit_policy.observe_tool("build_step")
    assert edit_policy.gate_tool("compile_file") is None


def test_execute_tool_call_does_not_call_build_during_edits() -> None:
    edit_policy.observe_tool("edit")

    class _Client:
        def __init__(self) -> None:
            self.called = False

        async def post(self, *_args, **_kwargs):
            self.called = True
            raise AssertionError("build must not be sent")

    client = _Client()
    result = asyncio.run(tool_loop.execute_tool_call(client, "build", {"preset": "release"}, "req"))
    assert result["ok"] is False
    assert client.called is False
    assert "build_step" in result["stderr"]
