"""Single source of truth for the `tools` service's argument contracts.

The pydantic models below are used in two places, so they can never drift
apart:

- `services/tools/main.py` validates every incoming request body / query
  string with them (FastAPI does this natively);
- `services/agent/tools_schema.py` derives the OpenAI-style function-calling
  schema the LLM sees from them with `model_json_schema()`, and
  `tool_loop.py` validates the LLM's arguments with them *before* calling
  the HTTP endpoint (a pydantic error becomes the corrective message sent
  back to the model).

Field descriptions are written for the LLM: they end up verbatim in the tool
schema. Bounds (`ge`/`le`) mirror the handlers' hard caps documented in
CONTRACTS.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional, Type

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ToolArgs",
    "BuildArgs",
    "BuildStepArgs",
    "TestArgs",
    "CompileArgs",
    "GitLogArgs",
    "GitBlameArgs",
    "GitDiffArgs",
    "GitShowArgs",
    "SvnStatusArgs",
    "SvnDiffArgs",
    "SvnLogArgs",
    "PathsArgs",
    "ReadFileArgs",
    "LspPositionArgs",
    "LspOutlineArgs",
    "LspCallsArgs",
    "LspTypesArgs",
    "ToolSpec",
    "TOOL_SPECS",
    "TOOL_SPECS_BY_NAME",
    "openai_tools_schema",
    "endpoint_map",
    "READ_FILE_MAX_LINES",
]

# Hard caps - the handlers import these so the schema and the enforcement
# can't disagree.
READ_FILE_MAX_LINES = 400


def _default_preset() -> str:
    # Read at call time (not import time) so tests can monkeypatch it and so
    # the `tools` container's env applies, not the `agent`'s.
    return os.environ.get("TOOLS_CMAKE_PRESET", "release")


class ToolArgs(BaseModel):
    """Base for all tool argument models: unknown keys are rejected, so a
    hallucinated parameter is caught by validation instead of silently
    ignored."""

    model_config = ConfigDict(extra="forbid")


class BuildArgs(ToolArgs):
    preset: str = Field(
        default_factory=_default_preset,
        description="CMake preset name to use (e.g. 'release', 'debug'). Omit for the project's default preset.",
    )


class TestArgs(BuildArgs):
    filter: Optional[str] = Field(
        default=None,
        description="Optional ctest regex (`ctest -R`). Example: 'json_round_trip' or 'throws_parse'.",
    )


class CompileArgs(ToolArgs):
    path: str = Field(description="Repo-relative C++ source file to compile (e.g. 'src/AgentClient.cpp').")
    preset: str = Field(
        default_factory=_default_preset,
        description="CMake preset name to use (e.g. 'release', 'debug'). Omit for the project's default preset.",
    )


class PathsArgs(ToolArgs):
    paths: list[str] = Field(description="Repo-relative file paths to check.")


class GitLogArgs(ToolArgs):
    path: Optional[str] = Field(default=None, description="Optional repo-relative path to limit the log.")
    max_count: int = Field(default=20, ge=1, le=50, description="Maximum commits to return (1-50, default 20).")


class GitBlameArgs(ToolArgs):
    path: str = Field(description="Repo-relative file to blame.")
    start_line: Optional[int] = Field(default=None, ge=1, description="Optional 1-based first line.")
    end_line: Optional[int] = Field(default=None, ge=1, description="Optional 1-based last line (inclusive).")


class GitDiffArgs(ToolArgs):
    ref_a: str = Field(description="From ref (commit, branch, or HEAD~N).")
    ref_b: str = Field(description="To ref (commit, branch, or HEAD).")
    path: Optional[str] = Field(default=None, description="Optional repo-relative path to limit the diff.")


class GitShowArgs(ToolArgs):
    ref: str = Field(description="Commit, tag, or HEAD.")
    path: Optional[str] = Field(default=None, description="Optional repo-relative path (`git show ref:path`).")


_SVN_REV = r"^(?:BASE|HEAD|COMMITTED|PREV|[0-9]+)$"


class SvnStatusArgs(ToolArgs):
    path: Optional[str] = Field(
        default=None,
        description="Optional repo-relative path. Omit for the whole working copy.",
    )


class SvnDiffArgs(ToolArgs):
    path: Optional[str] = Field(default=None, description="Optional repo-relative path.")
    rev_from: Optional[str] = Field(
        default=None,
        pattern=_SVN_REV,
        description=(
            "Optional start revision (a number, BASE, HEAD, COMMITTED, or PREV). "
            "Omit both revisions to diff uncommitted changes against BASE."
        ),
    )
    rev_to: Optional[str] = Field(
        default=None,
        pattern=_SVN_REV,
        description="End revision. Required when rev_from is set, forbidden when it is omitted.",
    )


class SvnLogArgs(ToolArgs):
    path: Optional[str] = Field(default=None, description="Optional repo-relative path to limit the log.")
    max_count: int = Field(default=20, ge=1, le=50, description="Maximum revisions to return (1-50, default 20).")


class LspPositionArgs(ToolArgs):
    path: str = Field(description="Repo-relative file (e.g. 'src/Widget.hpp').")
    line: int = Field(ge=1, description="1-based line of the symbol.")
    character: int = Field(ge=1, description="1-based column of the symbol.")
    preset: str = Field(
        default_factory=_default_preset,
        description="CMake preset whose compile_commands.json clangd should use.",
    )


class LspOutlineArgs(ToolArgs):
    path: str = Field(description="Repo-relative file whose symbol outline to list.")
    preset: str = Field(
        default_factory=_default_preset,
        description="CMake preset whose compile_commands.json clangd should use.",
    )


class LspCallsArgs(LspPositionArgs):
    direction: str = Field(
        default="both",
        pattern="^(in|out|both)$",
        description="in = callers, out = callees, both = the two lists one after the other.",
    )


class LspTypesArgs(LspPositionArgs):
    direction: str = Field(
        default="both",
        pattern="^(super|sub|both)$",
        description="super = base types, sub = derived types, both = the two lists.",
    )


class BuildStepArgs(ToolArgs):
    preset: str = Field(
        default_factory=_default_preset,
        description="CMake preset. The existing configure cache is reused.",
    )
    target: Optional[str] = Field(
        default=None,
        description=(
            "CMake target for this plan step. Omit only for the final solution build, "
            "and only when the plan touched more than one target. One parallel build, not one per file."
        ),
    )


class ReadFileArgs(ToolArgs):
    path: str = Field(description="Repo-relative path of the file to read.")
    start_line: Optional[int] = Field(default=None, ge=1, description="Optional 1-based first line to return.")
    end_line: Optional[int] = Field(default=None, ge=1, description="Optional 1-based last line to return (inclusive).")


@dataclass(frozen=True)
class ToolSpec:
    """One tool = one LLM function name = one HTTP endpoint = one argument model."""

    name: str
    method: str
    path: str
    description: str
    model: Type[ToolArgs]


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "build",
        "POST",
        "/build",
        "Build the C++ project with CMake using the given preset in a fresh scratch "
        "directory. Use this to verify the code actually compiles before claiming it does.",
        BuildArgs,
    ),
    ToolSpec(
        "test",
        "POST",
        "/test",
        "Build and run the project's test suite for the given CMake preset. Use this to "
        "verify behavioral claims rather than asserting they are true from memory. "
        "Pass `filter` to run only matching cases (`ctest -R`, e.g. a Catch2 TEST_CASE name).",
        TestArgs,
    ),
    ToolSpec(
        "compile_file",
        "POST",
        "/compile",
        "Configure the project once (cached per preset) and compile a single source file. "
        "Prefer this over `build` when checking one translation unit - it is much faster.",
        CompileArgs,
    ),
    ToolSpec(
        "lint_clang_tidy",
        "POST",
        "/lint/clang-tidy",
        "Run clang-tidy against the given file paths and report warnings/errors. Use this to "
        "check for likely bugs, UB, or Core Guidelines violations before recommending a change.",
        PathsArgs,
    ),
    ToolSpec(
        "format_check",
        "POST",
        "/format/check",
        "Check whether the given file paths conform to the project's clang-format style, "
        "without modifying them.",
        PathsArgs,
    ),
    ToolSpec(
        "static_analysis",
        "POST",
        "/static-analysis",
        "Run static analysis (cppcheck) against the given file paths to find likely defects "
        "(leaks, UB, out-of-bounds access).",
        PathsArgs,
    ),
    ToolSpec(
        "git_log",
        "POST",
        "/git/log",
        "Read-only git log for a path (or the whole tree). Use this for 'who changed this "
        "and why'. Output is capped at `max_count` commits.",
        GitLogArgs,
    ),
    ToolSpec(
        "git_blame",
        "POST",
        "/git/blame",
        "Read-only git blame for a file, optionally a line window. Use this to see who last "
        "touched each line and in which commit.",
        GitBlameArgs,
    ),
    ToolSpec(
        "git_diff",
        "POST",
        "/git/diff",
        "Read-only git diff between two refs, optionally limited to one path. Use this to "
        "inspect a change before commenting on it.",
        GitDiffArgs,
    ),
    ToolSpec(
        "git_show",
        "POST",
        "/git/show",
        "Read-only git show of a commit (or one file at that commit). Output is truncated "
        "if it exceeds the line cap.",
        GitShowArgs,
    ),
    ToolSpec(
        "svn_status",
        "POST",
        "/svn/status",
        "Read-only svn status of the working copy (needs .svn at the project root). "
        "Use this to see the uncommitted changelist without reindexing.",
        SvnStatusArgs,
    ),
    ToolSpec(
        "svn_diff",
        "POST",
        "/svn/diff",
        "Read-only svn diff. Omit both revisions for uncommitted changes against BASE. "
        "Set rev_from and rev_to together for a revision range. Output is capped.",
        SvnDiffArgs,
    ),
    ToolSpec(
        "svn_log",
        "POST",
        "/svn/log",
        "Read-only svn log, capped at max_count revisions. May need the repository server. "
        "Use this for who changed a path and why.",
        SvnLogArgs,
    ),
    ToolSpec(
        "lsp_calls",
        "POST",
        "/lsp/calls",
        "List callers (in) and/or callees (out) of the symbol at a 1-based position, via clangd. "
        "Each line is `name path:line`. Facts only.",
        LspCallsArgs,
    ),
    ToolSpec(
        "lsp_types",
        "POST",
        "/lsp/types",
        "List base types (super) and/or derived types (sub) of the symbol at a 1-based position, "
        "via clangd. Each line is `name path:line`. Facts only.",
        LspTypesArgs,
    ),
    ToolSpec(
        "build_step",
        "POST",
        "/build-step",
        "Parallel cmake --build on the configure cache. Call it once when a plan step's edits are done, "
        "with that step's target. Do not call it after every file. "
        "Omit target only for a final solution build when more than one target was built. "
        "On failure, the repair field says compile_file (exactly one .cpp in the log) or build_step.",
        BuildStepArgs,
    ),
)

TOOL_SPECS_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}


def _simplify(schema: Any) -> Any:
    """Make pydantic's JSON Schema friendlier for function calling.

    - drop `title` (noise for the model) and `default: null`;
    - collapse `anyOf: [X, {type: null}]` (pydantic's rendering of
      `Optional[X]`) into plain `X` - the field is already not `required`,
      and llama.cpp's grammar converter handles the flat form better.
    """
    if isinstance(schema, dict):
        out: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "title" or (key == "default" and value is None):
                continue
            if key == "anyOf" and isinstance(value, list):
                non_null = [v for v in value if not (isinstance(v, dict) and v.get("type") == "null")]
                if len(non_null) == 1 and isinstance(non_null[0], dict):
                    out.update(_simplify(non_null[0]))
                    continue
            out[key] = _simplify(value)
        return out
    if isinstance(schema, list):
        return [_simplify(v) for v in schema]
    return schema


def openai_tools_schema() -> list[dict[str, Any]]:
    """OpenAI-style `tools=[...]` list derived from the argument models."""
    tools: list[dict[str, Any]] = []
    for spec in TOOL_SPECS:
        parameters = _simplify(spec.model.model_json_schema())
        parameters.setdefault("required", [])
        tools.append(
            {
                "type": "function",
                "function": {"name": spec.name, "description": spec.description, "parameters": parameters},
            }
        )
    return tools


def endpoint_map() -> dict[str, dict[str, str]]:
    """`{name: {"method", "path"}}` - what `tool_loop.py` needs to execute a call."""
    return {spec.name: {"method": spec.method, "path": spec.path} for spec in TOOL_SPECS}
