"""`GET /list-files` handler - `rg --files` scoped to the project root.

Gives the LLM a way to discover the tree instead of guessing paths or
abusing `/search` with an empty pattern. Like `/search`, the walk root is
always inside `project_root`: the optional `path` is validated through
`path_safety.validate_path` (so `..`, absolute paths and symlink escapes are
rejected) and must resolve to a directory. The optional `glob` is passed to
ripgrep as its own argv element (never shell-interpolated).

Output is bounded (CONTRACTS.md): at most `max_results` paths (default 200,
hard cap 500), sorted, project-root-relative with forward slashes. `.gitignore`
rules are honoured by ripgrep, which is what we want - build outputs and
vendored trees are noise for the model.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from services.common.tool_models import LIST_FILES_DEFAULT_MAX_RESULTS, LIST_FILES_HARD_MAX_RESULTS
from services.tools.handlers.build import TIMEOUT_EXIT_CODE, timeout_text
from services.tools.handlers.path_safety import validate_path

__all__ = [
    "run_list_files",
    "ListFilesValidationError",
    "NotADirectoryError",
    "DEFAULT_MAX_RESULTS",
    "HARD_MAX_RESULTS",
]

# Shared with the LLM-facing schema (services/common/tool_models.py).
DEFAULT_MAX_RESULTS = LIST_FILES_DEFAULT_MAX_RESULTS
HARD_MAX_RESULTS = LIST_FILES_HARD_MAX_RESULTS


class ListFilesValidationError(ValueError):
    """Raised when `glob` / `max_results` fail validation."""


class NotADirectoryError(ValueError):
    """Raised when the validated `path` exists but is not a directory."""


def _build_argv(relative_dir: str, glob: Optional[str]) -> list[str]:
    argv = ["rg", "--files", "--color=never"]
    if glob:
        argv.extend(["--glob", glob])
    argv.append(relative_dir)
    return argv


def _normalise(line: str) -> str:
    line = line.replace("\\", "/")
    while line.startswith("./"):
        line = line[2:]
    return line


def run_list_files(
    project_root: Path,
    timeout_seconds: int,
    raw_path: Optional[str] = None,
    glob: Optional[str] = None,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict:
    if glob is not None and (not isinstance(glob, str) or not glob.strip()):
        raise ListFilesValidationError("glob must be a non-empty string when given")
    if not isinstance(max_results, int) or isinstance(max_results, bool) or max_results < 1:
        raise ListFilesValidationError(f"max_results must be a positive integer, got {max_results!r}")
    max_results = min(max_results, HARD_MAX_RESULTS)

    root_resolved = Path(project_root).resolve(strict=True)
    if raw_path is None or not raw_path.strip() or raw_path.strip() in (".", "./"):
        relative_dir = "."
        display_path = "."
    else:
        resolved = validate_path(raw_path, root_resolved)  # raises PathSafetyError
        if not resolved.exists():
            raise FileNotFoundError(f"not found: {raw_path!r}")
        if not resolved.is_dir():
            raise NotADirectoryError(f"not a directory: {raw_path!r}")
        relative_dir = resolved.relative_to(root_resolved).as_posix()
        display_path = relative_dir

    argv = _build_argv(relative_dir, glob)
    try:
        result = subprocess.run(
            argv,
            cwd=str(root_resolved),
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
            "stderr": timeout_text(exc.stderr) + f"\n[tools] rg --files timed out after {timeout_seconds}s",
            "exit_code": TIMEOUT_EXIT_CODE,
            "path": display_path,
            "glob": glob,
            "files": [],
            "total_files": 0,
            "truncated": False,
        }

    # rg --files: 0 = files listed, 1 = nothing matched (normal), >=2 = error.
    files = sorted(_normalise(line) for line in result.stdout.splitlines() if line.strip())
    total = len(files)
    truncated = total > max_results
    files = files[:max_results]

    stderr = result.stderr
    if truncated:
        stderr = (stderr + "\n" if stderr else "") + (
            f"[tools] output truncated to {max_results} of {total} files; "
            f"narrow `path` or `glob` (hard cap {HARD_MAX_RESULTS})"
        )

    return {
        "ok": result.returncode in (0, 1),
        "stdout": "\n".join(files) + ("\n" if files else ""),
        "stderr": stderr,
        "exit_code": result.returncode,
        "path": display_path,
        "glob": glob,
        "files": files,
        "total_files": total,
        "truncated": truncated,
    }
