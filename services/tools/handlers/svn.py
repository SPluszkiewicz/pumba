"""Read-only Subversion tools. Every invocation is an argv list."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from services.tools.handlers.build import TIMEOUT_EXIT_CODE, timeout_text
from services.tools.handlers.path_safety import validate_path

__all__ = [
    "SvnValidationError",
    "run_svn_status",
    "run_svn_diff",
    "run_svn_log",
    "MAX_SVN_LINES",
]

MAX_SVN_LINES = 200
_REV = re.compile(r"^(?:BASE|HEAD|COMMITTED|PREV|[0-9]+)$")


class SvnValidationError(ValueError):
    """Raised before any svn subprocess runs."""


def validate_rev(rev: str) -> str:
    if not isinstance(rev, str) or not _REV.match(rev) or rev.startswith("-"):
        raise SvnValidationError(f"invalid svn revision {rev!r}")
    return rev


def _svn_root(project_root: Path) -> Path:
    if not (project_root / ".svn").exists():
        raise SvnValidationError(
            f"{project_root} is not a Subversion working copy (.svn is missing). "
            "Point CODEBASE_PATH at the working copy root."
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
    truncated = len(lines) > MAX_SVN_LINES
    if truncated:
        stdout = "\n".join(lines[:MAX_SVN_LINES]) + "\n"
    return {
        "ok": result.returncode == 0,
        "stdout": stdout,
        "stderr": (result.stderr or "")
        + ("\n[tools] svn output truncated; narrow the path.\n" if truncated else ""),
        "exit_code": result.returncode,
        "truncated": truncated,
    }


def _optional_path(raw: Optional[str], root: Path) -> Optional[Path]:
    if raw is None or not raw.strip():
        return None
    return validate_path(raw, root)


def _rel(path: Optional[Path], root: Path) -> Optional[str]:
    if path is None:
        return None
    return path.relative_to(root).as_posix()


def run_svn_status(project_root: Path, timeout: int, *, raw_path: Optional[str]) -> dict:
    root = _svn_root(project_root)
    rel = _rel(_optional_path(raw_path, root), root)
    argv = ["svn", "status", "--non-interactive", "--ignore-externals"]
    if rel is not None:
        argv.extend(["--", rel])
    return _run(argv, root, timeout)


def run_svn_diff(
    project_root: Path,
    timeout: int,
    *,
    raw_path: Optional[str],
    rev_from: Optional[str],
    rev_to: Optional[str],
) -> dict:
    if (rev_from is None) != (rev_to is None):
        raise SvnValidationError("rev_from and rev_to must be set together")
    root = _svn_root(project_root)
    rel = _rel(_optional_path(raw_path, root), root)
    argv = ["svn", "diff", "--non-interactive", "--ignore-externals", "--git"]
    if rev_from is not None and rev_to is not None:
        argv.append(f"-r{validate_rev(rev_from)}:{validate_rev(rev_to)}")
    argv.append("--")
    if rel is not None:
        argv.append(rel)
    return _run(argv, root, timeout)


def run_svn_log(project_root: Path, timeout: int, *, raw_path: Optional[str], max_count: int) -> dict:
    root = _svn_root(project_root)
    if max_count < 1 or max_count > 50:
        raise SvnValidationError("max_count must be between 1 and 50")
    rel = _rel(_optional_path(raw_path, root), root)
    argv = ["svn", "log", "--non-interactive", "--limit", str(max_count)]
    if rel is not None:
        argv.extend(["--", rel])
    return _run(argv, root, timeout)
