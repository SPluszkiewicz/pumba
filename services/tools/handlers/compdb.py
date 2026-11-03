"""Use a checkout's existing compilation database before configuring CMake.

clangd's public input is `compile_commands.json` (`--compile-commands-dir`).
A host build records absolute paths. The checkout is mounted at another
prefix, so entries are rewritten onto that mount when the original file
path is not visible here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

__all__ = ["find_compile_commands", "prepare_compile_commands"]

_WIN_PATH = re.compile(r"[A-Za-z]:[\\/](?:[^\\/\s\"]+[\\/])*[^\\/\s\"]*")

_NAMED = (
    "compile_commands.json",
    "build/compile_commands.json",
    "out/compile_commands.json",
)


def find_compile_commands(project_root: Path) -> Optional[Path]:
    """clangd's usual locations, plus one `build*` directory under the root."""
    for relative in _NAMED:
        candidate = project_root / relative
        if candidate.is_file():
            return candidate
    try:
        children = list(project_root.iterdir())
    except OSError:
        return None
    for child in children:
        if not child.is_dir():
            continue
        if child.name.startswith("build") or child.name.startswith("cmake-build"):
            candidate = child / "compile_commands.json"
            if candidate.is_file():
                return candidate
    return None


def prepare_compile_commands(database: Path, project_root: Path, dest_dir: Path) -> Optional[Path]:
    """Directory clangd should receive. None when no entry maps onto the mount."""
    try:
        payload = json.loads(database.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, list) or not payload:
        return None
    rewritten: list[dict] = []
    changed = False
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        raw_file = str(entry.get("file") or "")
        mapped = _map_path(raw_file, project_root, want_dir=False)
        if mapped is None:
            continue
        directory = _map_path(str(entry.get("directory") or ""), project_root, want_dir=True)
        if directory is None:
            directory = mapped.parent
        if mapped != Path(raw_file):
            changed = True
        updated = dict(entry)
        updated["file"] = mapped.as_posix()
        updated["directory"] = directory.as_posix()
        command = updated.get("command")
        if isinstance(command, str):
            new_command = _rewrite_paths(command, project_root)
            if new_command != command:
                changed = True
                updated["command"] = new_command
        arguments = updated.get("arguments")
        if isinstance(arguments, list):
            new_args = []
            for arg in arguments:
                if not isinstance(arg, str):
                    new_args.append(arg)
                    continue
                new_arg = _rewrite_paths(arg, project_root)
                if new_arg != arg:
                    changed = True
                new_args.append(new_arg)
            updated["arguments"] = new_args
        rewritten.append(updated)
    if not rewritten:
        return None
    if not changed:
        return database.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / "compile_commands.json"
    target.write_text(json.dumps(rewritten), encoding="utf-8")
    return dest_dir


def _rewrite_paths(text: str, project_root: Path) -> str:
    """Map host paths that land in this checkout. A Qt kit path outside the mount stays."""

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        mapped = _map_path(raw, project_root, want_dir=False) or _map_path(raw, project_root, want_dir=True)
        if mapped is None:
            return raw
        return mapped.as_posix()

    return _WIN_PATH.sub(replace, text)


def _map_path(raw: str, project_root: Path, *, want_dir: bool) -> Optional[Path]:
    if not raw.strip():
        return None
    direct = Path(raw)
    if _matches(direct, project_root, want_dir=want_dir):
        return direct
    parts = _host_parts(raw)
    for index in range(len(parts)):
        candidate = project_root.joinpath(*parts[index:])
        if _matches(candidate, project_root, want_dir=want_dir):
            return candidate
    return None


def _matches(candidate: Path, project_root: Path, *, want_dir: bool) -> bool:
    try:
        candidate.resolve().relative_to(project_root.resolve())
        if want_dir:
            return candidate.is_dir()
        return candidate.is_file()
    except (OSError, ValueError):
        return False


def _host_parts(raw: str) -> list[str]:
    text = raw.replace("\\", "/").strip()
    if len(text) >= 2 and text[1] == ":":
        text = text[2:]
    return [part for part in text.split("/") if part and part != "."]
