"""When a refactor campaign is open, compilation waits until the step is done."""

from __future__ import annotations

from contextvars import ContextVar

PHASE: ContextVar[str] = ContextVar("edit_phase", default="idle")

_OPEN = frozenset({"begin_plan", "edit", "apply"})
_BLOCKED = frozenset({"build", "test", "compile_file"})

_WAIT = (
    "Edits are in progress. Finish this plan step, then call build_step once. "
    "Do not compile each file, and do not commit."
)


def reset_edit_phase() -> None:
    PHASE.set("idle")


def gate_tool(name: str) -> str | None:
    """Error text when this tool is not allowed, otherwise None."""
    if name in _BLOCKED and PHASE.get() == "editing":
        return _WAIT
    return None


def observe_tool(name: str) -> None:
    if name in _OPEN:
        PHASE.set("editing")
    elif name == "build_step":
        PHASE.set("idle")
