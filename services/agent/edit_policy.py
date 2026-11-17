"""When a refactor campaign is open, compilation waits until the step is done."""

from __future__ import annotations

from contextvars import ContextVar

from serena_named_tools import SERENA_WRITE_NAMES

PHASE: ContextVar[str] = ContextVar("edit_phase", default="idle")

_OPEN = SERENA_WRITE_NAMES
_BLOCKED = frozenset({"build", "test", "compile_file"})

_WAIT = (
    "Edits are in progress. Call build_step once. "
    "Do not compile each file, and do not commit."
)


def reset_edit_phase() -> None:
    PHASE.set("idle")


def gate_tool(name: str) -> str | None:
    """Error text when this tool is not allowed, otherwise None."""
    if name in _BLOCKED and PHASE.get() == "editing":
        return _WAIT
    return None


def observe_tool(name: str, *, ok: bool = True) -> None:
    """Apply a phase transition for one executed tool. A failure changes nothing."""
    if not ok:
        return
    if name in _OPEN:
        PHASE.set("editing")
    elif name == "build_step":
        PHASE.set("idle")
