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
    "BeginPlanArgs",
    "EditArgs",
    "ApplyArgs",
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
    "SearchArgs",
    "ListFilesArgs",
    "ReadFileArgs",
    "LspPositionArgs",
    "LspSymbolsArgs",
    "LspOutlineArgs",
    "LspCallsArgs",
    "LspTypesArgs",
    "ToolSpec",
    "TOOL_SPECS",
    "TOOL_SPECS_BY_NAME",
    "openai_tools_schema",
    "endpoint_map",
    "SEARCH_DEFAULT_MAX_RESULTS",
    "SEARCH_HARD_MAX_RESULTS",
    "LIST_FILES_DEFAULT_MAX_RESULTS",
    "LIST_FILES_HARD_MAX_RESULTS",
    "READ_FILE_MAX_LINES",
]

# Hard caps - the handlers import these so the schema and the enforcement
# can't disagree.
SEARCH_DEFAULT_MAX_RESULTS = 50
SEARCH_HARD_MAX_RESULTS = 100
LIST_FILES_DEFAULT_MAX_RESULTS = 200
LIST_FILES_HARD_MAX_RESULTS = 500
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


class SearchArgs(ToolArgs):
    pattern: str = Field(description="Literal string or regex pattern to search for.")
    regex: bool = Field(default=False, description="Whether `pattern` should be treated as a regex.")
    max_results: int = Field(
        default=SEARCH_DEFAULT_MAX_RESULTS,
        ge=1,
        le=SEARCH_HARD_MAX_RESULTS,
        description=(
            f"Maximum number of matching lines to return (1-{SEARCH_HARD_MAX_RESULTS}, "
            f"default {SEARCH_DEFAULT_MAX_RESULTS})."
        ),
    )


class ListFilesArgs(ToolArgs):
    path: Optional[str] = Field(default=None, description="Repo-relative directory to list (omit for project root).")
    glob: Optional[str] = Field(default=None, description="Optional glob filter, e.g. '*.hpp' or 'src/**/*.cpp'.")
    max_results: int = Field(
        default=LIST_FILES_DEFAULT_MAX_RESULTS,
        ge=1,
        le=LIST_FILES_HARD_MAX_RESULTS,
        description=(
            f"Maximum number of paths to return (1-{LIST_FILES_HARD_MAX_RESULTS}, "
            f"default {LIST_FILES_DEFAULT_MAX_RESULTS})."
        ),
    )


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


class LspSymbolsArgs(ToolArgs):
    query: str = Field(description="Symbol name or prefix to look up (workspace/symbol).")
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


class BeginPlanArgs(ToolArgs):
    plan: str = Field(description="Repo-relative file that lists the sources to change.")
    focus: Optional[str] = Field(
        default=None,
        description="Optional extra source the user named, in addition to the paths in the plan.",
    )
    max_files: Optional[int] = Field(
        default=None,
        ge=1,
        le=500,
        description="File budget for this campaign. Default 200. Hard cap 500.",
    )


class EditArgs(ToolArgs):
    campaign_id: str = Field(description="Id returned by begin_plan.")
    path: str = Field(description="Repo-relative file. Must be named in the plan or in focus.")
    old: str = Field(description="Exact excerpt that occurs once in the current file.")
    new: str = Field(description="Replacement text. Does not write the file.")


class ApplyArgs(ToolArgs):
    campaign_id: str = Field(description="Id returned by begin_plan.")
    proposal_id: str = Field(description="Id returned by edit. This call writes the working copy. It does not commit.")


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
        "search",
        "POST",
        "/search",
        "Search the codebase for a literal string or regex pattern. Use this to find real "
        "usages/definitions instead of guessing at symbol names or call sites. Output is "
        f"capped at `max_results` lines (default {SEARCH_DEFAULT_MAX_RESULTS}, max "
        f"{SEARCH_HARD_MAX_RESULTS}); if `truncated` is true in the result, narrow the pattern.",
        SearchArgs,
    ),
    ToolSpec(
        "list_files",
        "GET",
        "/list-files",
        "List files under a repo-relative directory (default: the project root), optionally "
        "filtered by a glob such as '*.hpp' or 'src/**/*.cpp'. Use this to discover real paths "
        "before calling read_file, instead of guessing. Returns at most `max_results` sorted "
        f"paths (default {LIST_FILES_DEFAULT_MAX_RESULTS}).",
        ListFilesArgs,
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
        "lsp_definition",
        "POST",
        "/lsp/definition",
        "Jump to the definition of the symbol at a 1-based line and column, using clangd. "
        "Prefer this over search for overloads, templates, and using-aliases.",
        LspPositionArgs,
    ),
    ToolSpec(
        "lsp_references",
        "POST",
        "/lsp/references",
        "List references of the symbol at a 1-based line and column, using clangd. "
        f"At most {40} locations.",
        LspPositionArgs,
    ),
    ToolSpec(
        "lsp_hover",
        "POST",
        "/lsp/hover",
        "Show clangd's hover (type and brief docs) for the symbol at a 1-based line and column.",
        LspPositionArgs,
    ),
    ToolSpec(
        "lsp_symbols",
        "POST",
        "/lsp/symbols",
        "Find workspace symbols by name via clangd. Use this when search would match comments "
        "and overloads instead of the real declaration.",
        LspSymbolsArgs,
    ),
    ToolSpec(
        "lsp_outline",
        "POST",
        "/lsp/outline",
        "List the symbols declared in one file (clangd documentSymbol). "
        "Each line is `name kind line`. Facts only, no commentary.",
        LspOutlineArgs,
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
        "begin_plan",
        "POST",
        "/begin",
        "Read a plan file and open an edit campaign. Returns campaign_id and the source paths "
        "the plan names (plus focus, if given). Later edits are refused outside that list. "
        "Does not write. Does not commit.",
        BeginPlanArgs,
    ),
    ToolSpec(
        "edit",
        "POST",
        "/edit",
        "Propose a replacement of one exact excerpt. The excerpt must occur once. "
        "Returns a unified diff and checks that it applies. Does not write the file. "
        "Call apply with the returned proposal_id to write the working copy. "
        "Do not call build, test, or compile_file between edits.",
        EditArgs,
    ),
    ToolSpec(
        "apply",
        "POST",
        "/apply",
        "Write one accepted edit into the working copy. Does not commit, push, or run the compiler. "
        "The shared index is left unchanged.",
        ApplyArgs,
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
    ToolSpec(
        "read_file",
        "GET",
        "/read-file",
        f"Read a single repo-relative file, optionally a 1-based line window. At most "
        f"{READ_FILE_MAX_LINES} lines are returned per call; when the result has `truncated: true`, "
        "call again with `start_line` set to `end_line + 1`. Use this to confirm what code "
        "actually says before commenting on it.",
        ReadFileArgs,
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
