"""`POST /format/check` handler - `clang-format --dry-run --Werror`.

Every path is re-validated via `path_safety.validate_path` before it ever
touches an argv list - never trust the caller.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

from services.tools.handlers.build import TIMEOUT_EXIT_CODE, timeout_text
from services.tools.handlers.path_safety import validate_path

__all__ = ["run_format_check"]

# Baked into the container image at this fixed path (see
# containers/tools/Containerfile) from the repo-root `.clang-format` - the
# tools container only bind-mounts the project tree (`CODEBASE_PATH`,
# default `./sample`), so clang-format's own upward directory search would
# never find this repo's `.clang-format` without this. Overridable for
# local/dev testing.
DEFAULT_STYLE_FILE = os.environ.get("TOOLS_CLANG_FORMAT_STYLE_FILE", "/etc/tools-config/.clang-format")


class FormatCheckValidationError(ValueError):
    """Raised when the `paths` argument fails validation."""


def _build_argv(resolved_paths: Sequence[Path]) -> list[str]:
    argv = ["clang-format", "--dry-run", "--Werror"]
    if Path(DEFAULT_STYLE_FILE).is_file():
        argv.append(f"--style=file:{DEFAULT_STYLE_FILE}")
    argv.extend(str(p) for p in resolved_paths)
    return argv


def run_format_check(paths: Sequence[str], project_root: Path, timeout_seconds: int) -> dict:
    if not paths:
        raise FormatCheckValidationError("paths must be a non-empty list")

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
            "stderr": timeout_text(exc.stderr) + f"\n[tools] clang-format timed out after {timeout_seconds}s",
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
