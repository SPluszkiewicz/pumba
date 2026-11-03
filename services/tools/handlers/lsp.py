"""clangd queries: definition, references, hover, workspace symbols.

clangd runs inside `tools`. It prefers a `compile_commands.json` already
in the checkout (`build/`, `out/`, or a `build*` directory). Host paths in
that file are rewritten onto the mount. If none is usable, `compile_file`'s
per-preset cache is configured instead. A separate container cannot see
that scratch directory.
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any, Optional

from services.tools.handlers.build import ensure_configured, root_cache_key, scratch_base
from services.tools.handlers.clangd_rpc import (
    MAX_LOCATIONS,
    ClangdError,
    ClangdSession,
    format_document_symbols,
    format_locations,
    format_related_symbols,
)
from services.tools.handlers.compdb import find_compile_commands, prepare_compile_commands
from services.tools.handlers.path_safety import validate_path

__all__ = [
    "run_definition",
    "run_references",
    "run_hover",
    "run_symbols",
    "run_outline",
    "run_calls",
    "run_types",
    "reset_sessions",
]

_MAX_FILE_BYTES = 256 * 1024
_HOVER_MAX_CHARS = 4000
_sessions: dict[str, ClangdSession] = {}
_sessions_lock = threading.Lock()


class LspError(ValueError):
    """Raised before clangd runs (bad path, missing compile database)."""


def reset_sessions() -> None:
    """Tests drop any cached clangd process."""
    with _sessions_lock:
        for session in _sessions.values():
            session.close()
        _sessions.clear()


def run_definition(path: str, line: int, character: int, preset: str, project_root: Path, timeout: int) -> dict:
    return _position("textDocument/definition", path, line, character, preset, project_root, timeout)


def run_references(path: str, line: int, character: int, preset: str, project_root: Path, timeout: int) -> dict:
    extra = {"context": {"includeDeclaration": True}}
    return _position("textDocument/references", path, line, character, preset, project_root, timeout, extra=extra)


def run_hover(path: str, line: int, character: int, preset: str, project_root: Path, timeout: int) -> dict:
    opened = _open(path, line, character, preset, project_root, timeout)
    if "ok" in opened and opened.get("ok") is False:
        return opened
    session, uri = opened
    try:
        result = session.request(
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": {"line": line - 1, "character": character - 1}},
        )
    except ClangdError as exc:
        return _fail(str(exc))
    full = _hover_text(result)
    text = full[:_HOVER_MAX_CHARS]
    return {"ok": True, "stdout": text, "stderr": "", "exit_code": 0, "truncated": len(full) > _HOVER_MAX_CHARS}


def run_symbols(query: str, preset: str, project_root: Path, timeout: int) -> dict:
    session = _session(preset, project_root, timeout)
    if isinstance(session, dict):
        return session
    try:
        result = session.request("workspace/symbol", {"query": query})
    except ClangdError as exc:
        return _fail(str(exc))
    text, truncated = format_locations(result, _uri_prefix(project_root), limit=MAX_LOCATIONS)
    return {"ok": True, "stdout": text, "stderr": "", "exit_code": 0, "truncated": truncated}


def run_outline(path: str, preset: str, project_root: Path, timeout: int) -> dict:
    """Symbol outline of one file. No chat model."""
    opened = _open(path, 1, 1, preset, project_root, timeout)
    if isinstance(opened, dict):
        return opened
    session, uri = opened
    try:
        result = session.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
    except ClangdError as exc:
        return _fail(str(exc))
    text, truncated = format_document_symbols(result)
    return {"ok": True, "stdout": text, "stderr": "", "exit_code": 0, "truncated": truncated}


def run_calls(
    path: str, line: int, character: int, direction: str, preset: str, project_root: Path, timeout: int
) -> dict:
    """Incoming and/or outgoing calls for the symbol at this position."""
    return _hierarchy(
        "textDocument/prepareCallHierarchy",
        "callHierarchy/incomingCalls",
        "callHierarchy/outgoingCalls",
        "from",
        "to",
        path,
        line,
        character,
        direction,
        preset,
        project_root,
        timeout,
    )


def run_types(
    path: str, line: int, character: int, direction: str, preset: str, project_root: Path, timeout: int
) -> dict:
    """Supertypes and/or subtypes for the symbol at this position."""
    mapped = {"super": "in", "sub": "out", "both": "both"}.get(direction, "both")
    return _hierarchy(
        "textDocument/prepareTypeHierarchy",
        "typeHierarchy/supertypes",
        "typeHierarchy/subtypes",
        "to",
        "to",
        path,
        line,
        character,
        mapped,
        preset,
        project_root,
        timeout,
    )


def _hierarchy(
    prepare: str,
    incoming: str,
    outgoing: str,
    in_role: str,
    out_role: str,
    path: str,
    line: int,
    character: int,
    direction: str,
    preset: str,
    project_root: Path,
    timeout: int,
) -> dict:
    opened = _open(path, line, character, preset, project_root, timeout)
    if isinstance(opened, dict):
        return opened
    session, uri = opened
    position = {"line": line - 1, "character": character - 1}
    try:
        items = session.request(prepare, {"textDocument": {"uri": uri}, "position": position})
    except ClangdError as exc:
        return _fail(str(exc))
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return {"ok": True, "stdout": "", "stderr": "", "exit_code": 0, "truncated": False}
    item = items[0]
    want_in = direction in {"in", "both"}
    want_out = direction in {"out", "both"}
    parts: list[str] = []
    truncated = False
    prefix = _uri_prefix(project_root)
    try:
        if want_in:
            found = session.request(incoming, {"item": item})
            text, cut = format_related_symbols(found, prefix, role=in_role)
            if text:
                parts.append(text)
            truncated = truncated or cut
        if want_out:
            found = session.request(outgoing, {"item": item})
            text, cut = format_related_symbols(found, prefix, role=out_role)
            if text:
                parts.append(text)
            truncated = truncated or cut
    except ClangdError as exc:
        return _fail(str(exc))
    return {"ok": True, "stdout": "\n".join(parts), "stderr": "", "exit_code": 0, "truncated": truncated}


def _position(
    method: str,
    path: str,
    line: int,
    character: int,
    preset: str,
    project_root: Path,
    timeout: int,
    extra: Optional[dict[str, Any]] = None,
) -> dict:
    opened = _open(path, line, character, preset, project_root, timeout)
    if isinstance(opened, dict):
        return opened
    session, uri = opened
    params: dict[str, Any] = {
        "textDocument": {"uri": uri},
        "position": {"line": line - 1, "character": character - 1},
    }
    if extra:
        params.update(extra)
    try:
        result = session.request(method, params)
    except ClangdError as exc:
        return _fail(str(exc))
    text, truncated = format_locations(result, _uri_prefix(project_root))
    return {"ok": True, "stdout": text, "stderr": "", "exit_code": 0, "truncated": truncated}


def _open(path: str, line: int, character: int, preset: str, project_root: Path, timeout: int):
    del line, character
    resolved = validate_path(path, project_root)
    if not resolved.is_file():
        raise LspError(f"not a file: {path}")
    data = resolved.read_bytes()
    if len(data) > _MAX_FILE_BYTES:
        raise LspError(f"{path} exceeds {_MAX_FILE_BYTES} bytes")
    session = _session(preset, project_root, timeout)
    if isinstance(session, dict):
        return session
    uri = resolved.as_uri()
    session.notify(
        "textDocument/didOpen",
        {
            "textDocument": {
                "uri": uri,
                "languageId": "cpp",
                "version": 1,
                "text": data.decode("utf-8", "replace"),
            }
        },
    )
    return session, uri


def _commands_dir(preset: str, project_root: Path, timeout: int) -> tuple[Path | None, str]:
    found = find_compile_commands(project_root)
    if found is not None:
        # One rewritten database per mounted tree: the default mount and the
        # registered checkouts share this scratch tmpfs.
        scratch = Path(scratch_base() or "/tmp") / "host-compdb" / root_cache_key(project_root)
        prepared = prepare_compile_commands(found, project_root, scratch)
        if prepared is not None:
            return prepared, ""
    build_dir, configured = ensure_configured(preset, project_root, timeout)
    if configured.returncode != 0:
        return None, configured.stderr or "cmake configure failed"
    if not (build_dir / "compile_commands.json").is_file():
        return None, "compile_commands.json is missing; run compile_file once for this preset"
    return build_dir, ""


def _session(preset: str, project_root: Path, timeout: int) -> ClangdSession | dict:
    if shutil.which("clangd") is None:
        return _fail("clangd is not installed in the tools image")
    commands_dir, error = _commands_dir(preset, project_root, timeout)
    if commands_dir is None:
        return _fail(error)
    try:
        stamp = (commands_dir / "compile_commands.json").stat().st_mtime_ns
    except OSError:
        stamp = 0
    key = f"{commands_dir}:{stamp}:{project_root}"
    root_key = str(project_root)
    with _sessions_lock:
        current = _sessions.get(key)
        if current is not None and (current._proc is None or current._proc.poll() is None):  # noqa: SLF001
            return current
        for old, previous in list(_sessions.items()):
            if old.endswith(root_key) and old != key:
                previous.close()
                del _sessions[old]
        session = ClangdSession.spawn(
            ["clangd", f"--compile-commands-dir={commands_dir}", "--background-index=0", "--header-insertion=never"],
            cwd=str(project_root),
        )
        try:
            session.request(
                "initialize",
                {
                    "processId": None,
                    "rootUri": project_root.as_uri(),
                    "capabilities": {},
                    "initializationOptions": {"compilationDatabasePath": str(commands_dir)},
                },
            )
            session.notify("initialized", {})
        except ClangdError as exc:
            session.close()
            return _fail(str(exc))
        _sessions[key] = session
        return session


def _uri_prefix(project_root: Path) -> str:
    return project_root.resolve().as_uri().rstrip("/") + "/"


def _hover_text(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    contents = result.get("contents")
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        return str(contents.get("value") or "")
    if isinstance(contents, list):
        parts = []
        for item in contents:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("value") or ""))
        return "\n".join(parts)
    return ""


def _fail(message: str) -> dict:
    return {"ok": False, "stdout": "", "stderr": message, "exit_code": -1, "truncated": False}
