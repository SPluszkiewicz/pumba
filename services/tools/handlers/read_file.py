"""`GET /read-file` handler.

Validates the caller-supplied path via `path_safety.validate_path`, then
enforces a hard size cap before reading anything into memory - a 256 KiB
cap comfortably covers any single source file in this project while
bounding memory/response size regardless of what a caller asks for.

On top of the byte cap, the *returned* content is bounded to
`MAX_READ_FILE_LINES` lines per call. A single tool observation that dumps
thousands of lines into the LLM's context window is the fastest way to
derail a small model, so callers page through large files with the optional
`start_line` / `end_line` (1-based, inclusive) window instead. When the
window is omitted and the file is longer than the cap, the first
`MAX_READ_FILE_LINES` lines are returned with `truncated: true` and a hint in
`stderr` telling the caller how to fetch the rest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from services.common.tool_models import READ_FILE_MAX_LINES
from services.tools.handlers.path_safety import validate_path

__all__ = [
    "ReadFileError",
    "ReadFileNotFoundError",
    "FileTooLargeError",
    "NotAFileError",
    "LineRangeError",
    "MAX_READ_FILE_BYTES",
    "MAX_READ_FILE_LINES",
    "run_read_file",
]

# 256 KiB - documented cap; comfortably above any single source file in this
# repo while bounding worst-case response size/memory regardless of what a
# caller asks for.
MAX_READ_FILE_BYTES = 256 * 1024

# Maximum number of lines returned by a single call (documented in
# CONTRACTS.md). Roughly 4-6k tokens of dense C++ - large enough to show a
# whole function with context, small enough to never saturate an 8k window
# on its own. Shared with the LLM-facing schema (services/common/tool_models.py).
MAX_READ_FILE_LINES = READ_FILE_MAX_LINES


class LineRangeError(ValueError):
    """Raised when `start_line` / `end_line` are not a valid 1-based window."""


class ReadFileError(Exception):
    """Base class for `/read-file`-specific errors (path safety errors are
    raised separately as `path_safety.PathSafetyError`)."""


class ReadFileNotFoundError(ReadFileError):
    """Raised when the validated path does not exist."""


class NotAFileError(ReadFileError):
    """Raised when the validated path exists but is not a regular file
    (e.g. a directory)."""


class FileTooLargeError(ReadFileError):
    """Raised when the file exceeds `MAX_READ_FILE_BYTES`."""

    def __init__(self, size_bytes: int, max_bytes: int) -> None:
        super().__init__(f"file is {size_bytes} bytes, exceeds the {max_bytes}-byte cap")
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes


def _resolve_window(
    total_lines: int,
    start_line: Optional[int],
    end_line: Optional[int],
) -> tuple[int, int, bool]:
    """Returns the 1-based inclusive `(start, end, truncated)` window to return.

    - No window given: `1..min(total, MAX)`; truncated if the file is longer.
    - `start_line` only: `start..min(total, start + MAX - 1)`.
    - Both given: as requested, but clamped to `MAX` lines from `start`.
    """
    if start_line is not None and start_line < 1:
        raise LineRangeError(f"start_line must be >= 1, got {start_line}")
    if end_line is not None and end_line < 1:
        raise LineRangeError(f"end_line must be >= 1, got {end_line}")
    if start_line is not None and end_line is not None and end_line < start_line:
        raise LineRangeError(f"end_line ({end_line}) must be >= start_line ({start_line})")

    start = start_line or 1
    if start > max(total_lines, 1):
        raise LineRangeError(f"start_line ({start}) is past the end of the file ({total_lines} lines)")

    requested_end = end_line if end_line is not None else total_lines
    requested_end = min(requested_end, total_lines)
    hard_end = start + MAX_READ_FILE_LINES - 1
    end = min(requested_end, hard_end)
    truncated = requested_end > hard_end
    return start, end, truncated


def run_read_file(
    raw_path: str,
    project_root: Path,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> dict:
    resolved = validate_path(raw_path, project_root)  # raises PathSafetyError

    if not resolved.exists():
        raise ReadFileNotFoundError(f"not found: {raw_path!r}")

    if not resolved.is_file():
        raise NotAFileError(f"not a regular file: {raw_path!r}")

    size_bytes = resolved.stat().st_size
    if size_bytes > MAX_READ_FILE_BYTES:
        raise FileTooLargeError(size_bytes, MAX_READ_FILE_BYTES)

    # `errors="replace"` avoids a hard crash on non-UTF-8 (e.g. binary)
    # files - the caller gets replacement characters instead of a 500.
    content = resolved.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines(keepends=True)
    total_lines = len(lines)

    start, end, truncated = _resolve_window(total_lines, start_line, end_line)
    window = "".join(lines[start - 1 : end]) if total_lines else ""

    stderr = ""
    if truncated:
        stderr = (
            f"[tools] output truncated to {MAX_READ_FILE_LINES} lines "
            f"({start}-{end} of {total_lines}); call again with "
            f"start_line={end + 1} to continue"
        )

    return {
        "ok": True,
        "stdout": window,
        "stderr": stderr,
        "exit_code": 0,
        # Echo back the caller's own relative path, not the resolved
        # absolute container path - no reason to leak internal mount
        # layout to callers.
        "path": raw_path,
        "size_bytes": size_bytes,
        "start_line": start if total_lines else 0,
        "end_line": end if total_lines else 0,
        "total_lines": total_lines,
        "truncated": truncated,
    }
