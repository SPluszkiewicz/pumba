"""Read-only git tools. Every invocation is an argv list; refs are allow-listed."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from services.tools.handlers.build import TIMEOUT_EXIT_CODE, timeout_text
from services.tools.handlers.path_safety import validate_path

__all__ = [
    "GitValidationError",
    "run_git_log",
    "run_git_blame",
    "run_git_diff",
    "run_git_diff_names",
    "run_git_show",
    "run_git_status",
    "MAX_GIT_LINES",
]

MAX_GIT_LINES = 200
# Commit / HEAD~N / simple branch or tag names. No `..` range, no spaces, no options.
_REF = re.compile(r"^(?:HEAD(?:~\d+)?|[0-9a-f]{7,40}|[A-Za-z][A-Za-z0-9._/-]*)$")


class GitValidationError(ValueError):
    """Raised before any git subprocess runs."""


def validate_ref(ref: str) -> str:
    if not isinstance(ref, str) or not _REF.match(ref) or ".." in ref or ref.startswith("-"):
        raise GitValidationError(f"invalid git ref {ref!r}")
    return ref


def _git_root(project_root: Path) -> Path:
    git_dir = project_root / ".git"
    if not git_dir.exists():
        raise GitValidationError(
            f"{project_root} is not a git work tree (.git is missing). "
            "Mount the project's .git at $TOOLS_PROJECT_ROOT/.git (compose bind) "
            "or point CODEBASE_PATH at a repository root."
        )
    return project_root


def _run(argv: list[str], cwd: Path, timeout: int) -> dict:
    try:
        result = subprocess.run(
            argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, shell=False, check=False
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "stdout": timeout_text(exc.stdout),
            "stderr": timeout_text(exc.stderr) + f"\n[tools] command timed out after {timeout}s",
            "exit_code": TIMEOUT_EXIT_CODE,
        }
    stdout = result.stdout or ""
    lines = stdout.splitlines()
    truncated = len(lines) > MAX_GIT_LINES
    if truncated:
        stdout = "\n".join(lines[:MAX_GIT_LINES]) + "\n"
    return {
        "ok": result.returncode == 0,
        "stdout": stdout,
        "stderr": (result.stderr or "")
        + ("\n[tools] git output truncated; narrow the path or refs.\n" if truncated else ""),
        "exit_code": result.returncode,
        "truncated": truncated,
    }


def _optional_path(raw: Optional[str], root: Path) -> Optional[Path]:
    if raw is None or not raw.strip():
        return None
    return validate_path(raw, root)


def run_git_log(project_root: Path, timeout: int, *, raw_path: Optional[str], max_count: int) -> dict:
    root = _git_root(project_root)
    path = _optional_path(raw_path, root)
    argv = [
        "git",
        "-c",
        "safe.directory=*",
        "log",
        f"-n{max_count}",
        "--date=short",
        "--format=%h %ad %an %s",
        "--",
    ]
    if path is not None:
        argv.append(str(path.relative_to(root).as_posix()))
    return _run(argv, root, timeout)


def run_git_blame(
    project_root: Path,
    timeout: int,
    *,
    raw_path: str,
    start_line: Optional[int],
    end_line: Optional[int],
) -> dict:
    root = _git_root(project_root)
    path = validate_path(raw_path, root)
    argv = ["git", "-c", "safe.directory=*", "blame", "--date=short"]
    if start_line is not None or end_line is not None:
        start = start_line or 1
        end = end_line or start
        if end < start:
            raise GitValidationError("end_line must be >= start_line")
        argv.extend([f"-L{start},{end}"])
    argv.extend(["--", str(path.relative_to(root).as_posix())])
    return _run(argv, root, timeout)


def run_git_status(project_root: Path, timeout: int) -> dict:
    """Uncommitted names. Used to overlay a checkout; not an LLM tool."""
    root = _git_root(project_root)
    argv = ["git", "-c", "safe.directory=*", "status", "--porcelain=v1", "-u"]
    return _run(argv, root, timeout)


def run_git_diff_names(project_root: Path, timeout: int, *, ref_a: str, ref_b: str) -> dict:
    """`git diff --name-only`. Used by agent `/review`, not exposed to the LLM."""
    root = _git_root(project_root)
    a, b = validate_ref(ref_a), validate_ref(ref_b)
    argv = ["git", "-c", "safe.directory=*", "diff", "--name-only", "--diff-filter=ACMR", a, b]
    return _run(argv, root, timeout)


def run_git_diff(project_root: Path, timeout: int, *, ref_a: str, ref_b: str, raw_path: Optional[str]) -> dict:
    root = _git_root(project_root)
    a, b = validate_ref(ref_a), validate_ref(ref_b)
    path = _optional_path(raw_path, root)
    argv = ["git", "-c", "safe.directory=*", "diff", "--no-color", a, b, "--"]
    if path is not None:
        argv.append(str(path.relative_to(root).as_posix()))
    return _run(argv, root, timeout)


def run_git_show(project_root: Path, timeout: int, *, ref: str, raw_path: Optional[str]) -> dict:
    root = _git_root(project_root)
    validated = validate_ref(ref)
    path = _optional_path(raw_path, root)
    if path is None:
        argv = ["git", "-c", "safe.directory=*", "show", "--no-color", "--stat", validated]
    else:
        shown = f"{validated}:{path.relative_to(root).as_posix()}"
        argv = ["git", "-c", "safe.directory=*", "show", "--no-color", shown]
    return _run(argv, root, timeout)
