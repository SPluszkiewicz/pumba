"""`POST /search` handler - `ripgrep` scoped to the fixed project root.

Unlike the other handlers, there is no caller-supplied *path* here at all -
the search root is always `project_root`, chosen by us, never by the
caller, so there is no path-traversal risk to guard against. What we do
still guard against: never shell-interpolating the pattern (it's passed as
a single, distinct argv element, so something like `; rm -rf /` or
`$(...)` is inert - see tests/tools/test_argv_construction.py) and never
letting the caller widen the search root.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from services.common.tool_models import SEARCH_DEFAULT_MAX_RESULTS, SEARCH_HARD_MAX_RESULTS
from services.tools.handlers.build import TIMEOUT_EXIT_CODE, timeout_text

__all__ = ["run_search", "DEFAULT_MAX_RESULTS", "HARD_MAX_RESULTS", "MAX_LINE_COLUMNS"]

# Bounded output (CONTRACTS.md): a search for a common token (`std::`,
# `#include`) can return thousands of lines, which is enough on its own to
# saturate a small model's context window. Callers may ask for up to
# HARD_MAX_RESULTS matches; anything beyond is dropped and reported via
# `truncated` / `total_matches` so the model can narrow the pattern instead.
# The values live in services/common/tool_models.py so the schema the LLM
# sees and this enforcement can't disagree.
DEFAULT_MAX_RESULTS = SEARCH_DEFAULT_MAX_RESULTS
HARD_MAX_RESULTS = SEARCH_HARD_MAX_RESULTS
# Long minified/generated lines are cut by ripgrep itself (`--max-columns`).
MAX_LINE_COLUMNS = 300


class SearchValidationError(ValueError):
    """Raised when the `pattern` / `max_results` arguments fail validation."""


def _build_argv(pattern: str, regex: bool, project_root: Path) -> list[str]:
    argv = [
        "rg",
        "--line-number",
        "--no-heading",
        "--color=never",
        f"--max-columns={MAX_LINE_COLUMNS}",
    ]
    if not regex:
        argv.append("--fixed-strings")
    # `pattern` and the search root are each their own argv element - never
    # concatenated into a shell string - so this is safe even if `pattern`
    # contains shell metacharacters.
    argv.append(pattern)
    argv.append(str(project_root))
    return argv


def _bound_results(stdout: str, max_results: int) -> tuple[str, int, bool]:
    """Keeps at most `max_results` match lines; returns (text, total, truncated)."""
    lines = stdout.splitlines()
    total = len(lines)
    if total <= max_results:
        return stdout, total, False
    return "\n".join(lines[:max_results]) + "\n", total, True


def run_search(
    pattern: str,
    regex: bool,
    project_root: Path,
    timeout_seconds: int,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict:
    if not isinstance(pattern, str) or not pattern.strip():
        raise SearchValidationError("pattern must not be empty")
    if not isinstance(max_results, int) or isinstance(max_results, bool) or max_results < 1:
        raise SearchValidationError(f"max_results must be a positive integer, got {max_results!r}")
    max_results = min(max_results, HARD_MAX_RESULTS)

    try:
        result = subprocess.run(
            _build_argv(pattern, regex, project_root),
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "stdout": timeout_text(exc.stdout),
            "stderr": timeout_text(exc.stderr) + f"\n[tools] rg timed out after {timeout_seconds}s",
            "exit_code": TIMEOUT_EXIT_CODE,
            "pattern": pattern,
            "regex": regex,
        }

    stdout, total_matches, truncated = _bound_results(result.stdout, max_results)
    stderr = result.stderr
    if truncated:
        stderr = (
            (stderr + "\n" if stderr else "")
            + f"[tools] output truncated to {max_results} of {total_matches} matches; "
            "narrow the pattern or raise max_results (hard cap "
            f"{HARD_MAX_RESULTS})"
        )

    # ripgrep exit codes: 0 = matches found, 1 = no matches (not an error),
    # >=2 = a real error (bad pattern, I/O error, etc.).
    return {
        "ok": result.returncode in (0, 1),
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": result.returncode,
        "pattern": pattern,
        "regex": regex,
        "total_matches": total_matches,
        "truncated": truncated,
    }
