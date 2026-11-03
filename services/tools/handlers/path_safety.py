"""Path validation - the security foundation for every handler in this service.

This module is the ONLY code allowed to translate a caller-supplied,
untrusted path string into a real filesystem path. Every handler that
touches the filesystem (lint/format/static-analysis/read-file) MUST route
paths through :func:`validate_path` before doing anything else with them.
`build`/`test` don't take caller paths at all (only an allow-listed preset
name), so they don't use this module.

Threat model / what we defend against:
  - `..` traversal sequences (e.g. ``../../etc/passwd``)
  - absolute paths, on any OS spelling (``/etc/passwd``, ``C:\\Windows``) -
    we never trust a caller's absolute prefix; every caller path is treated
    as relative to the project root, or rejected
  - symlinks *inside* the project root that resolve (after following all
    links) to somewhere outside the project root
  - null bytes and empty/whitespace-only input

We resolve the real path with `Path.resolve()` (which follows symlinks) and
re-check containment strictly AFTER resolution - checking containment on the
*unresolved* path would miss a symlink escape entirely.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

__all__ = ["PathSafetyError", "validate_path"]


class PathSafetyError(ValueError):
    """Raised when a caller-supplied path fails validation.

    Always carries a message safe to return to the caller (i.e. it never
    embeds real filesystem internals beyond the caller's own input).
    """


# A caller path that, once split on '/', still contains a segment matching
# this is almost certainly an attempt to smuggle a drive letter / UNC-style
# anchor (e.g. "C:", "\\\\server") past the "must be relative" check below.
# POSIX filenames may legally contain ':' but none exist in this codebase,
# so we reject it outright as a paranoid, defense-in-depth simplification.
_DISALLOWED_SEGMENT_CHAR = ":"


def validate_path(raw_path: str, root: Path | str) -> Path:
    """Validate `raw_path` (caller-supplied, untrusted) against `root`.

    Args:
        raw_path: the path string as supplied by the caller, intended to be
            interpreted as relative to `root` (the mounted, read-only
            project root, e.g. ``/workspace/app``).
        root: the project root directory. Must already exist.

    Returns:
        The resolved, absolute `Path`, guaranteed to be `root` itself or a
        descendant of `root` on the real (post-symlink-resolution)
        filesystem.

    Raises:
        PathSafetyError: if `raw_path` is empty/whitespace, contains a null
            byte, contains a `..` traversal segment, looks like an absolute
            path (POSIX- or Windows-style), contains a disallowed segment
            character, or resolves (after following symlinks) to a location
            outside `root`.
    """
    if not isinstance(raw_path, str):
        raise PathSafetyError(f"path must be a string, got {type(raw_path).__name__}")

    if "\x00" in raw_path:
        raise PathSafetyError("path must not contain null bytes")

    if not raw_path.strip():
        raise PathSafetyError("path must not be empty or whitespace-only")

    root_resolved = Path(root).resolve(strict=True)

    # Interpret the untrusted string using strict, host-independent POSIX
    # path semantics (this service only ever runs in Linux containers) -
    # normalize Windows-style separators first so the same rules apply no
    # matter how the caller wrote the path, and so behavior is identical
    # whether these tests run on a Linux CI box or a Windows dev machine.
    posix_form = raw_path.replace("\\", "/")

    if _looks_like_drive_anchor(posix_form):
        raise PathSafetyError(f"path must not be an absolute/drive-anchored path: {raw_path!r}")

    pure = PurePosixPath(posix_form)

    if pure.is_absolute():
        raise PathSafetyError(f"path must be relative to the project root, not absolute: {raw_path!r}")

    segments = [segment for segment in pure.parts if segment not in ("/", ".")]

    if any(segment == ".." for segment in segments):
        raise PathSafetyError(f"path must not contain '..' traversal: {raw_path!r}")

    if any(_DISALLOWED_SEGMENT_CHAR in segment for segment in segments):
        raise PathSafetyError(f"path segments must not contain {_DISALLOWED_SEGMENT_CHAR!r}: {raw_path!r}")

    # `joinpath(*segments)` appends each segment as a single path component.
    # Because every segment came from splitting on '/' above, none of them
    # can individually be absolute or contain a separator, so this can never
    # fall victim to pathlib's "joining with an absolute path discards the
    # left side" behavior.
    candidate = root_resolved.joinpath(*segments)

    # This is the load-bearing check: resolve() follows symlinks, so a
    # symlink living inside root_resolved that points outside of it will
    # show up here as a `resolved` path outside root_resolved, even though
    # `candidate` (pre-resolution) looked perfectly safe.
    resolved = candidate.resolve()

    if not resolved.is_relative_to(root_resolved):
        raise PathSafetyError(f"path escapes the project root after resolution: {raw_path!r}")

    return resolved


def _looks_like_drive_anchor(posix_form: str) -> bool:
    """True if `posix_form` starts with a Windows-style drive letter anchor.

    `PurePosixPath` has no concept of drive letters, so ``"C:/Windows"``
    would otherwise sail through the `is_absolute()` check above as a
    perfectly ordinary (and, worse, seemingly relative) path. Reject it
    explicitly instead.
    """
    return len(posix_form) >= 2 and posix_form[0].isalpha() and posix_form[1] == ":"
