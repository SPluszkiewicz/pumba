"""`POST /lint/clang-tidy` handler.

Every path is re-validated via `path_safety.validate_path` before it ever
touches an argv list - never trust the caller, even though these paths came
from the same process that will run the tool.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

from services.tools.handlers.build import TIMEOUT_EXIT_CODE, timeout_text
from services.tools.handlers.path_safety import validate_path

__all__ = ["run_lint"]

# Baked into the container image at this fixed path (see
# containers/tools/Containerfile) from the repo-root `.clang-tidy`, because
# the tools container only bind-mounts the project tree (`CODEBASE_PATH`,
# default `./sample`) at `TOOLS_PROJECT_ROOT` - the real `.clang-tidy` at
# this repo's root is never visible inside the container, so clang-tidy's
# own upward directory search would silently fall back to built-in defaults
# without this. Overridable for local/dev testing.
DEFAULT_CONFIG_FILE = os.environ.get("TOOLS_CLANG_TIDY_CONFIG_FILE", "/etc/tools-config/.clang-tidy")


class LintValidationError(ValueError):
    """Raised when the `paths` argument fails validation."""


def _build_argv(resolved_paths: Sequence[Path]) -> list[str]:
    argv = ["clang-tidy"]
    if Path(DEFAULT_CONFIG_FILE).is_file():
        argv.append(f"--config-file={DEFAULT_CONFIG_FILE}")
    argv.extend(str(p) for p in resolved_paths)
    return argv


def run_lint(paths: Sequence[str], project_root: Path, timeout_seconds: int) -> dict:
    if not paths:
        raise LintValidationError("paths must be a non-empty list")

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
            "stderr": timeout_text(exc.stderr) + f"\n[tools] clang-tidy timed out after {timeout_seconds}s",
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
