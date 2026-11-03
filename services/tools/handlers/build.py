"""`POST /build` handler, plus the shared CMake pipeline `test.py` reuses.

CMake preset + scratch-dir design note (read this before touching anything)
-----------------------------------------------------------------------------
The C++ source tree is bind-mounted **read-only** at `project_root`
(default `/workspace/app` - see `main.py`'s `TOOLS_PROJECT_ROOT`). CMake
must never be allowed to write inside it. `app/CMakePresets.json` sets
`binaryDir` to `${sourceDir}/build/<preset>`, i.e. *inside* the read-only
mount, so naively running `cmake --preset <name>` from that tree fails
outright (mkdir under a read-only bind mount).

We need every request to build into its own disposable, writable
`tempfile.mkdtemp()` scratch directory instead. The obvious-looking fix -
`cmake --preset <name> -B <scratch>/build` to relocate the preset's
binaryDir from the command line - does **not** reliably work:

  - Combining a *configure* preset with an explicit `-B` to override
    `binaryDir` has a long, messy history (see
    https://gitlab.kitware.com/cmake/cmake/-/issues/22276) and its exact
    behavior is version-dependent.
  - Overriding a *build* preset's directory from the CLI only became
    possible in a recent CMake (merge request !11425), newer than the
    3.28 that Ubuntu 24.04 (the `tools` base image) ships.

Depending on that fragile, version-sensitive interaction inside a
security-sensitive service is not acceptable. So we take the SAFEST
APPROACH instead: bypass `CMakePresets.json` entirely for the actual
invocation. `_PRESET_CACHE_VARIABLES` below translates each allow-listed
preset name into the equivalent explicit `-D` cache variables (kept in sync
with `app/CMakePresets.json` by hand - today that's just
`CMAKE_BUILD_TYPE`), and we drive `cmake` directly with
`-S <project_root> -B <scratch>/build`. This has no version-dependent
behavior and works on any CMake new enough to have presets at all.

If `app/CMakePresets.json` grows additional `cacheVariables` beyond
`CMAKE_BUILD_TYPE` in the future, `_PRESET_CACHE_VARIABLES` must be updated
to match by hand, since we intentionally never parse that file.

`/build` and `/test` use a fresh `mkdtemp()` per request. `/compile` and
`/lsp/*` reuse a configured tree under `<scratch>/cmake-cache/<root>/<preset>`,
where `<root>` is `root_cache_key(project_root)`: the default mount and the
registered checkouts share one scratch tmpfs, so the cache is keyed by tree.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional, Tuple

__all__ = [
    "PresetValidationError",
    "validate_preset",
    "run_build",
    "run_cmake_pipeline",
    "root_cache_key",
    "scratch_base",
]

# Allow-list of known preset names -> the explicit CMake cache variables that
# reproduce what `app/CMakePresets.json` configures for that preset name.
# CMake preset names are "just names", but we still never pass a
# caller-supplied string straight into a subprocess argument - validate
# against this small fixed allow-list first, always.
_PRESET_CACHE_VARIABLES: dict[str, dict[str, str]] = {
    "release": {"CMAKE_BUILD_TYPE": "Release"},
    "debug": {"CMAKE_BUILD_TYPE": "Debug"},
}

# Conventional "command timed out" exit code (matches what the POSIX `timeout`
# utility reports), used so callers can distinguish a timeout from a normal
# tool failure just by looking at `exit_code`.
TIMEOUT_EXIT_CODE = 124


def timeout_text(stream: "str | bytes | None") -> str:
    """`subprocess.TimeoutExpired.stdout/stderr` are typed `bytes | str | None`;
    every handler here runs with `text=True`, so they are always `str` in
    practice. This narrows the type in one place (and decodes defensively if
    a future caller ever drops `text=True`)."""
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream


# Preferred base directory for scratch dirs: compose.yaml provisions a
# writable tmpfs specifically at this path for the `tools` service (whose
# root filesystem is otherwise `read_only: true`). Fall back to the system
# default temp dir (e.g. when running outside that container, such as in
# unit tests) if it isn't present.
_PREFERRED_SCRATCH_BASE = "/workspace/scratch"


class PresetValidationError(ValueError):
    """Raised when a caller-supplied preset name is not on the allow-list."""


def validate_preset(preset: str) -> dict[str, str]:
    """Validate `preset` against the fixed allow-list and return its cache vars.

    Raises `PresetValidationError` (a `ValueError`) before any subprocess is
    ever started - callers of this module must call this (or a function that
    calls this) first.
    """
    if not isinstance(preset, str) or preset not in _PRESET_CACHE_VARIABLES:
        raise PresetValidationError(
            f"unknown preset {preset!r}; must be one of {sorted(_PRESET_CACHE_VARIABLES)}"
        )
    return _PRESET_CACHE_VARIABLES[preset]


def _scratch_base() -> Optional[str]:
    return _PREFERRED_SCRATCH_BASE if Path(_PREFERRED_SCRATCH_BASE).is_dir() else None


def scratch_base() -> Optional[str]:
    """Writable scratch root when the container provides one."""
    return _scratch_base()


def configure_argv(project_root: Path, build_dir: Path, cache_vars: dict[str, str]) -> list[str]:
    argv = [
        "cmake",
        "-S",
        str(project_root),
        "-B",
        str(build_dir),
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
    ]
    for key, value in cache_vars.items():
        argv.append(f"-D{key}={value}")
    return argv


def root_cache_key(project_root: Path) -> str:
    """Stable, filesystem-safe name for one mounted tree.

    The default mount (`/workspace/app`) and every registered checkout
    (`/checkouts/<id>`) share the same scratch tmpfs, so anything cached
    across requests must be keyed by the tree it was produced from. A
    `CMakeCache.txt` configured for one source tree must never be reused
    for another: CMake would refuse (or worse, silently build the wrong
    project).
    """
    resolved = str(project_root.resolve())
    digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:12]
    name = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in project_root.name) or "root"
    return f"{name}-{digest}"


def persistent_build_dir(preset: str, project_root: Path) -> Path:
    """Reusable out-of-source tree for `compile_file`, one per (tree, preset)."""
    base = Path(_scratch_base() or tempfile.gettempdir()) / "cmake-cache" / root_cache_key(project_root) / preset
    base.mkdir(parents=True, exist_ok=True)
    return base


def ensure_configured(
    preset: str, project_root: Path, timeout_seconds: int
) -> tuple[Path, subprocess.CompletedProcess]:
    """Configure `persistent_build_dir(preset, project_root)` if it has no CMakeCache yet."""
    cache_vars = validate_preset(preset)
    build_dir = persistent_build_dir(preset, project_root)
    if (build_dir / "CMakeCache.txt").exists():
        return build_dir, subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    result = run_step(
        configure_argv(project_root, build_dir, cache_vars),
        cwd=project_root,
        timeout=timeout_seconds,
    )
    return build_dir, result


def run_step(argv: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    """Run `argv` (never a shell string) and capture output, text-mode.

    This is the single seam every handler in this module goes through to
    invoke a subprocess, which is what unit tests patch
    (`services.tools.handlers.build.subprocess.run`) to exercise timeout and
    argv-construction behavior without needing a real toolchain installed.
    """
    try:
        return subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            args=argv,
            returncode=TIMEOUT_EXIT_CODE,
            stdout=timeout_text(exc.stdout),
            stderr=timeout_text(exc.stderr) + f"\n[tools] command timed out after {timeout}s: {' '.join(argv)}",
        )


def format_response(preset: str, steps: list[Tuple[str, subprocess.CompletedProcess]]) -> dict:
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    exit_code = -1
    for name, result in steps:
        stdout_parts.append(f"----- {name} -----\n{result.stdout}")
        stderr_parts.append(f"----- {name} -----\n{result.stderr}")
        exit_code = result.returncode
        if result.returncode != 0:
            break
    return {
        "ok": bool(steps) and exit_code == 0,
        "stdout": "\n".join(stdout_parts),
        "stderr": "\n".join(stderr_parts),
        "exit_code": exit_code,
        "preset": preset,
    }


def run_cmake_pipeline(
    preset: str,
    project_root: Path,
    timeout_seconds: int,
    *,
    scratch_prefix: str,
    extra_step: Optional[Tuple[str, Callable[[Path], list[str]]]] = None,
) -> dict:
    """Configure (+ build, + optional extra step) in a fresh scratch dir.

    `extra_step`, if given, is `(name, argv_builder)` where `argv_builder`
    receives the scratch build dir and returns an argv list; it only runs if
    configure AND build both succeeded. `test.py` uses this to bolt on
    `ctest` without duplicating the configure/build plumbing.

    The scratch directory is always removed before returning, success or
    failure (try/finally) - never leaked, never reused across requests.
    """
    cache_vars = validate_preset(preset)  # raises before any subprocess runs

    scratch_dir = Path(tempfile.mkdtemp(prefix=scratch_prefix, dir=_scratch_base()))
    try:
        build_dir = scratch_dir / "build"
        steps: list[Tuple[str, subprocess.CompletedProcess]] = []

        configure_result = run_step(
            configure_argv(project_root, build_dir, cache_vars),
            cwd=project_root,
            timeout=timeout_seconds,
        )
        steps.append(("configure", configure_result))

        if configure_result.returncode == 0:
            build_result = run_step(
                ["cmake", "--build", str(build_dir), "-j", str(os.cpu_count() or 1)],
                cwd=project_root,
                timeout=timeout_seconds,
            )
            steps.append(("build", build_result))

            if extra_step is not None and build_result.returncode == 0:
                extra_name, argv_builder = extra_step
                extra_result = run_step(
                    argv_builder(build_dir),
                    cwd=project_root,
                    timeout=timeout_seconds,
                )
                steps.append((extra_name, extra_result))

        response = format_response(preset, steps)
        # Informational only (the directory is gone by the time the caller
        # sees this) - useful for debugging/log correlation and for tests
        # asserting per-request scratch-dir isolation.
        response["scratch_dir"] = str(scratch_dir)
        return response
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


def run_build(preset: str, project_root: Path, timeout_seconds: int) -> dict:
    return run_cmake_pipeline(preset, project_root, timeout_seconds, scratch_prefix="tools-build-")


_TARGET_NAME = re.compile(r"^[A-Za-z0-9_.:/+-]{1,200}$")
_LOG_SOURCE = re.compile(r"([\w./\\-]+\.(?:cpp|cc|cxx|c))\b", re.IGNORECASE)


def repair_from_log(log: str) -> dict:
    """One cited .cpp -> compile that file. Several, or none -> rebuild the target."""
    found: list[str] = []
    for match in _LOG_SOURCE.finditer(log):
        path = match.group(1).replace("\\", "/")
        if path not in found:
            found.append(path)
    if len(found) == 1:
        return {"kind": "compile_file", "path": found[0]}
    return {"kind": "build_step"}


def run_cached_build(
    preset: str,
    project_root: Path,
    timeout_seconds: int,
    target: Optional[str] = None,
) -> dict:
    """Build on the configure cache, in parallel. Does not start a fresh tree."""
    if target is not None and target != "":
        if ".." in target or _TARGET_NAME.fullmatch(target) is None:
            raise PresetValidationError(f"target name is not allowed: {target!r}")
    else:
        target = None
    build_dir, configure_result = ensure_configured(preset, project_root, timeout_seconds)
    steps: list[Tuple[str, subprocess.CompletedProcess]] = []
    if configure_result.args and configure_result.returncode != 0:
        steps.append(("configure", configure_result))
        response = format_response(preset, steps)
        response["repair"] = {"kind": "build_step"}
        response["target"] = target or ""
        return response
    argv = ["cmake", "--build", str(build_dir), "--parallel"]
    if target:
        argv.extend(["--target", target])
    result = run_step(argv, cwd=build_dir, timeout=timeout_seconds)
    steps.append(("build", result))
    response = format_response(preset, steps)
    response["target"] = target or ""
    response["build_dir"] = str(build_dir)
    if response["ok"]:
        response["repair"] = {"kind": "none"}
    else:
        response["repair"] = repair_from_log(f"{response['stdout']}\n{response['stderr']}")
    return response
