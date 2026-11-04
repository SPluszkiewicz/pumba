"""Minimal LSP framing over clangd's stdio JSON-RPC.

Notifications (no `id`) are skipped until the response that matches the
request id, which is what clangd does between `initialize` and a query.
"""

from __future__ import annotations

import json
import subprocess
import threading
from typing import IO, Any, Optional
from urllib.parse import unquote, urlsplit

__all__ = [
    "ClangdError",
    "ClangdSession",
    "encode_message",
    "MessageReader",
    "format_locations",
    "format_document_symbols",
    "format_related_symbols",
]

MAX_LOCATIONS = 40


class ClangdError(RuntimeError):
    """clangd exited, timed out, or returned a JSON-RPC error."""


def encode_message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


class MessageReader:
    """Buffered reader for `Content-Length` framed JSON-RPC."""

    def __init__(self, stream: IO[bytes]) -> None:
        self._stream = stream
        self._buf = b""

    def read(self) -> Optional[dict[str, Any]]:
        header = self._read_until(b"\r\n\r\n")
        if header is None:
            return None
        length = _content_length(header[: -len(b"\r\n\r\n")])
        body = self._read_exact(length)
        if body is None:
            return None
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise ClangdError("clangd message is not an object")
        return parsed

    def _read_some(self, size: int) -> bytes:
        read1 = getattr(self._stream, "read1", None)
        if callable(read1):
            return read1(size)
        return self._stream.read(size)

    def _read_until(self, marker: bytes) -> Optional[bytes]:
        while marker not in self._buf:
            chunk = self._read_some(4096)
            if not chunk:
                return None
            self._buf += chunk
            if len(self._buf) > 1_000_000:
                raise ClangdError("clangd header exceeded 1 MB")
        index = self._buf.index(marker) + len(marker)
        out, self._buf = self._buf[:index], self._buf[index:]
        return out

    def _read_exact(self, length: int) -> Optional[bytes]:
        while len(self._buf) < length:
            chunk = self._read_some(max(length - len(self._buf), 4096))
            if not chunk:
                return None
            self._buf += chunk
        out, self._buf = self._buf[:length], self._buf[length:]
        return out


def _content_length(header: bytes) -> int:
    for line in header.decode("ascii", "replace").split("\r\n"):
        if line.lower().startswith("content-length:"):
            return int(line.split(":", 1)[1].strip())
    raise ClangdError("clangd message has no Content-Length")


class ClangdSession:
    """One clangd process. `stdin`/`stdout` can be injected in tests."""

    def __init__(self, stdin: IO[bytes], stdout: IO[bytes]) -> None:
        self._stdin = stdin
        self._reader = MessageReader(stdout)
        self._lock = threading.Lock()
        self._next_id = 0
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self.opened_docs: dict[str, int] = {}

    @classmethod
    def spawn(cls, argv: list[str], cwd: str) -> ClangdSession:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        assert proc.stdin is not None and proc.stdout is not None
        session = cls(proc.stdin, proc.stdout)
        session._proc = proc
        return session

    def close(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
        try:
            self._stdin.close()
        except OSError:
            pass

    def request(self, method: str, params: Any) -> Any:
        with self._lock:
            self._next_id += 1
            msg_id = self._next_id
            self._write({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
            while True:
                message = self._reader.read()
                if message is None:
                    raise ClangdError("clangd closed the stream")
                if message.get("id") != msg_id:
                    continue
                if "error" in message:
                    raise ClangdError(str(message["error"]))
                return message.get("result")

    def notify(self, method: str, params: Any) -> None:
        with self._lock:
            self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, payload: dict[str, Any]) -> None:
        self._stdin.write(encode_message(payload))
        self._stdin.flush()


def format_locations(result: Any, project_root_uri_prefix: str, *, limit: int = MAX_LOCATIONS) -> tuple[str, bool]:
    """Render definition/references/symbol locations as `path:line:col` lines.

    `project_root_uri_prefix` is `Path.as_uri()` plus `/` so only files inside
    the mounted tree are shown. Returns `(text, truncated)`.
    """
    lines: list[str] = []
    for uri, line, col in _iter_locations(result):
        rel = _relative_uri(uri, project_root_uri_prefix)
        if rel is None:
            continue
        lines.append(f"{rel}:{line}:{col}")
    truncated = len(lines) > limit
    shown = lines[:limit]
    text = "\n".join(shown)
    if truncated:
        text += f"\n[tools] truncated to {limit} locations; narrow the query.\n"
    return text, truncated


_SYMBOL_KIND = {
    5: "class",
    6: "method",
    8: "field",
    10: "enum",
    11: "interface",
    12: "function",
    23: "struct",
    26: "typeParameter",
}


def format_document_symbols(result: Any, *, limit: int = MAX_LOCATIONS) -> tuple[str, bool]:
    """Flatten a documentSymbol tree to `name kind line` lines."""
    lines: list[str] = []

    def walk(nodes: Any, depth: int) -> None:
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            name = str(node.get("name") or "?")
            raw_kind = node.get("kind")
            kind = _SYMBOL_KIND.get(raw_kind, "") if isinstance(raw_kind, int) else ""
            raw_loc = node.get("location")
            loc: dict[str, Any] = raw_loc if isinstance(raw_loc, dict) else {}
            range_ = loc.get("range") or node.get("selectionRange") or node.get("range") or {}
            start = range_.get("start") if isinstance(range_, dict) else {}
            line_no = int((start or {}).get("line") or 0) + 1
            indent = "  " * depth
            label = f"{indent}{name} {kind} {line_no}".rstrip()
            lines.append(label)
            walk(node.get("children"), depth + 1)

    walk(result, 0)
    truncated = len(lines) > limit
    shown = lines[:limit]
    text = "\n".join(shown)
    if truncated:
        text += f"\n[tools] truncated to {limit} symbols.\n"
    return text, truncated


def format_related_symbols(
    result: Any,
    project_root_uri_prefix: str,
    *,
    role: str,
    limit: int = MAX_LOCATIONS,
) -> tuple[str, bool]:
    """Render call or type hierarchy items. `role` is `from` or `to`."""
    lines: list[str] = []
    items = result if isinstance(result, list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        node = item.get(role) if isinstance(item.get(role), dict) else item
        if not isinstance(node, dict):
            continue
        name = str(node.get("name") or "?")
        uri = node.get("uri")
        range_ = node.get("selectionRange") or node.get("range") or {}
        start = range_.get("start") if isinstance(range_, dict) else {}
        line_no = int((start or {}).get("line") or 0) + 1
        if isinstance(uri, str):
            rel = _relative_uri(uri, project_root_uri_prefix) or _external_header_uri(uri)
            if not rel:
                continue
            where = f"{rel}:{line_no}"
        else:
            where = str(line_no)
        lines.append(f"{name} {where}")
    truncated = len(lines) > limit
    text = "\n".join(lines[:limit])
    if truncated:
        text += f"\n[tools] truncated to {limit} symbols.\n"
    return text, truncated


def _iter_locations(result: Any):
    if result is None:
        return
    items = result if isinstance(result, list) else [result]
    for item in items:
        if not isinstance(item, dict):
            continue
        if "location" in item and isinstance(item["location"], dict):
            item = item["location"]
        uri = item.get("uri") or item.get("targetUri")
        range_ = item.get("range") or item.get("targetSelectionRange") or item.get("targetRange") or {}
        start = range_.get("start") or {}
        if not isinstance(uri, str) or not isinstance(start, dict):
            continue
        # LSP positions are 0-based; the rest of this stack is 1-based.
        yield uri, int(start.get("line") or 0) + 1, int(start.get("character") or 0) + 1


def _relative_uri(uri: str, prefix: str) -> Optional[str]:
    if not uri.startswith(prefix):
        return None
    rel = uri[len(prefix) :].lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    return rel


def _external_header_uri(uri: str) -> Optional[str]:
    """Basename of a compiler/system header. Other absolute paths stay hidden."""
    if not uri.startswith("file:"):
        return None
    path = unquote(urlsplit(uri).path).replace("\\", "/")
    lowered = path.lower()
    if "/include/" not in lowered and "/c++/" not in lowered and "/c%2b%2b/" not in lowered:
        return None
    name = path.rsplit("/", 1)[-1]
    return name or None
