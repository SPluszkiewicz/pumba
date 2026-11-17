"""Checks the agent applies before a Serena call. Serena is not the policy."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

_FORBIDDEN_SEGMENTS = {".git", ".svn", ".env"}
# Same bound as a file read (256 KiB), applied to a Serena answer.
MAX_ANSWER_CHARS = 256 * 1024
# File arguments. `reference_file` is Serena's name on get_diagnostics_for_symbol.
# The search globs are paths too: a pattern can name `.git` or leave the checkout.
_PATH_ARGUMENTS = ("relative_path", "reference_file", "paths_include_glob", "paths_exclude_glob")


def serena_refusal(arguments: dict[str, Any]) -> dict[str, Any] | None:
    """A failed named-tool result, or None when the call may reach the sidecar."""
    root, problem = _open_checkout()
    if problem is not None:
        return _refused(problem)
    for key in _PATH_ARGUMENTS:
        path = _path_refusal(arguments.get(key), root)
        if path is not None:
            return path
    return _answer_cap(arguments)


def _path_refusal(raw: Any, root: Path | None) -> dict[str, Any] | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    if "\x00" in raw:
        return _refused("path must not contain null bytes")
    forbidden = _forbidden_segment(raw)
    if forbidden is not None:
        return _refused(forbidden)
    posix = raw.replace("\\", "/")
    if _drive_anchor(posix):
        return _refused(f"path must not be an absolute/drive-anchored path: {raw!r}")
    pure = PurePosixPath(posix)
    if pure.is_absolute():
        return _refused(f"path must be relative to the project root, not absolute: {raw!r}")
    if any(part == ".." for part in pure.parts):
        return _refused(f"path must not contain '..' traversal: {raw!r}")
    segments = [segment for segment in pure.parts if segment not in ("/", ".")]
    if any(":" in segment for segment in segments):
        return _refused(f"path segments must not contain ':': {raw!r}")
    if root is None:
        return None
    try:
        resolved = root.joinpath(*segments).resolve()
    except (OSError, ValueError):
        return _refused(f"path escapes the project root after resolution: {raw!r}")
    if not resolved.is_relative_to(root):
        return _refused(f"path escapes the project root after resolution: {raw!r}")
    return None


def _open_checkout() -> tuple[Path | None, str | None]:
    """(root, None) for a registered checkout. (None, None) when the turn has no override.

    (None, message) when the host path is not a registered checkout.
    """
    text = _project_root()
    if not text:
        return None, None
    path = Path(text)
    if not path.is_absolute():
        return None, "X-Project-Root must be an absolute path under /checkouts"
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None, "X-Project-Root is not a mounted checkout"
    # Compare resolved parents so a symlinked `/checkouts` still counts.
    if resolved.parent != Path("/checkouts").resolve():
        return None, "X-Project-Root must be a direct child of /checkouts"
    return resolved, None


def _drive_anchor(posix_form: str) -> bool:
    return len(posix_form) >= 2 and posix_form[0].isalpha() and posix_form[1] == ":"


def _answer_cap(arguments: dict[str, Any]) -> dict[str, Any] | None:
    value = arguments.get("max_answer_chars")
    if isinstance(value, int) and not isinstance(value, bool) and value > MAX_ANSWER_CHARS:
        return _refused(f"max_answer_chars exceeds the {MAX_ANSWER_CHARS} character cap")
    return None


def _forbidden_segment(raw: str) -> str | None:
    posix = raw.replace("\\", "/")
    for part in posix.split("/"):
        if part in _FORBIDDEN_SEGMENTS or part.startswith(".env"):
            return f"path must not pass through .git, .svn, or .env: {raw!r}"
    return None


def _project_root() -> str:
    from scope import project_root_header

    return project_root_header.get().strip()


def _refused(message: str) -> dict[str, Any]:
    return {"ok": False, "stdout": "", "stderr": message, "exit_code": 2}
