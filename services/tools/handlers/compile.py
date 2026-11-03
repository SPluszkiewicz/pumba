"""`POST /compile` - configure once per preset, compile one translation unit."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from services.tools.handlers.build import ensure_configured, format_response, run_step
from services.tools.handlers.path_safety import validate_path

__all__ = ["run_compile_file", "CompileValidationError"]


class CompileValidationError(ValueError):
    """Raised when the source path is not a compilable file we can find a command for."""


def _compile_commands(build_dir: Path) -> list[dict]:
    path = build_dir / "compile_commands.json"
    if not path.is_file():
        raise CompileValidationError(
            "compile_commands.json is missing; configure the project first (CMAKE_EXPORT_COMPILE_COMMANDS)"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise CompileValidationError("compile_commands.json is not a list")
    return data


def _entry_for(rel_path: str, project_root: Path, commands: list[dict]) -> dict:
    resolved = validate_path(rel_path, project_root)
    posix = resolved.as_posix()
    for entry in commands:
        file_path = str(entry.get("file") or "").replace("\\", "/")
        if file_path == posix or file_path.endswith("/" + rel_path.replace("\\", "/")):
            return entry
    raise CompileValidationError(f"no compile command for {rel_path!r}; is it a C++ source file in this project?")


def _compile_argv(entry: dict, build_dir: Path) -> list[str]:
    output = entry.get("output")
    if isinstance(output, str) and output.strip():
        # Native cmake object target (e.g. CMakeFiles/agent_client.dir/src/Foo.cpp.o).
        return ["cmake", "--build", str(build_dir), "--target", output, "--", "-j", "1"]
    arguments = entry.get("arguments")
    if isinstance(arguments, list) and arguments and all(isinstance(a, str) for a in arguments):
        return arguments
    command = entry.get("command")
    if isinstance(command, str) and command.strip():
        return shlex.split(command, posix=True)
    raise CompileValidationError("compile_commands entry has neither output, arguments, nor command")


def run_compile_file(rel_path: str, preset: str, project_root: Path, timeout_seconds: int) -> dict:
    if not rel_path.lower().endswith((".cpp", ".cc", ".cxx", ".c")):
        raise CompileValidationError(f"compile_file only accepts C/C++ sources, not {rel_path!r}")

    build_dir, configure_result = ensure_configured(preset, project_root, timeout_seconds)
    steps: list[tuple[str, object]] = []
    if configure_result.args:
        steps.append(("configure", configure_result))
        if configure_result.returncode != 0:
            response = format_response(preset, steps)  # type: ignore[arg-type]
            response["path"] = rel_path
            return response

    entry = _entry_for(rel_path, project_root, _compile_commands(build_dir))
    compile_result = run_step(
        _compile_argv(entry, build_dir),
        cwd=Path(entry.get("directory") or build_dir),
        timeout=timeout_seconds,
    )
    steps.append(("compile", compile_result))
    response = format_response(preset, steps)  # type: ignore[arg-type]
    response["path"] = rel_path
    response["build_dir"] = str(build_dir)
    return response
