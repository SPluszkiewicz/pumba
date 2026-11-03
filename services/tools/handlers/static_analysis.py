"""`POST /static-analysis` handler - `cppcheck`.

Every path is re-validated via `path_safety.validate_path` before it ever
touches an argv list - never trust the caller.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence

from services.tools.handlers.build import TIMEOUT_EXIT_CODE, timeout_text
from services.tools.handlers.path_safety import validate_path

__all__ = ["run_static_analysis"]

# Conservative, non-noisy default check set. cppcheck has no repo-level
# config file in this project (unlike clang-tidy/clang-format), so there is
# nothing else to wire up here.
_DEFAULT_ENABLE = "warning,style,performance,portability"


class StaticAnalysisValidationError(ValueError):
    """Raised when the `paths` argument fails validation."""


def _build_argv(resolved_paths: Sequence[Path]) -> list[str]:
    return [
        "cppcheck",
        "--quiet",
        f"--enable={_DEFAULT_ENABLE}",
        *[str(p) for p in resolved_paths],
    ]


def run_static_analysis(paths: Sequence[str], project_root: Path, timeout_seconds: int) -> dict:
    if not paths:
        raise StaticAnalysisValidationError("paths must be a non-empty list")

    resolved_paths = [validate_path(p, project_root) for p in paths]

    try:
        result = subprocess.run(
            _build_argv(resolved_paths),
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
            "stderr": timeout_text(exc.stderr) + f"\n[tools] cppcheck timed out after {timeout_seconds}s",
            "exit_code": TIMEOUT_EXIT_CODE,
            "paths": [str(p) for p in resolved_paths],
        }

    return {
        "ok": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.returncode,
        "paths": [str(p) for p in resolved_paths],
    }
