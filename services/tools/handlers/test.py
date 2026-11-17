"""`POST /test` handler.

Same fresh-scratch-dir, no-shell, allow-listed-preset design as `build.py`
(see that module's docstring for the full CMake preset/scratch-dir
rationale) - this just bolts a `ctest` step onto the shared
configure+build pipeline, since a fresh scratch dir per request means there
is never an already-built tree lying around to test against.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from services.tools.handlers.build import run_cmake_pipeline

__all__ = ["run_test", "TestFilterError"]

# Catch2 names contain spaces. The value is one argv element of `ctest -R`,
# never a shell string. `$()` and leading options stay rejected.
_FILTER = re.compile(r"^[A-Za-z0-9_.:*?+|\\^ -]+$")


class TestFilterError(ValueError):
    """Raised when `filter` is not a safe ctest regex."""


def validate_filter(raw: Optional[str]) -> Optional[str]:
    if raw is None or not str(raw).strip():
        return None
    value = str(raw).strip()
    if value.startswith("-") or not _FILTER.match(value):
        raise TestFilterError(f"invalid ctest filter {raw!r}")
    return value


def _ctest_argv(build_dir: Path, filt: Optional[str] = None) -> list[str]:
    # `--test-dir` (CMake >= 3.20, well below the 3.28 in ubuntu:24.04) lets ctest
    # target an out-of-source build dir without needing a `cd`.
    argv = ["ctest", "--test-dir", str(build_dir), "--output-on-failure", "--no-tests=error"]
    if filt:
        argv.extend(["-R", filt])
    return argv


def run_test(preset: str, project_root: Path, timeout_seconds: int, filt: Optional[str] = None) -> dict:
    validated = validate_filter(filt)
    return run_cmake_pipeline(
        preset,
        project_root,
        timeout_seconds,
        scratch_prefix="tools-test-",
        extra_step=("test", lambda build_dir: _ctest_argv(build_dir, validated)),
    )
