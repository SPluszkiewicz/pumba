"""Uncommitted files a caller holds on top of one shared checkout.

The server checkout stays the repository. Alice and Bob name that same
checkout. Each publishes their own files; a request sees only its caller.
Nothing here is written into the index or onto the server tree.
"""

from __future__ import annotations

import re
import threading
from contextvars import ContextVar

_CLIENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}")
_MAX_FILES = 6
_MAX_BYTES = 65_536

_caller: ContextVar[str] = ContextVar("local_caller", default="")
_files: dict[tuple[str, str], dict[str, str]] = {}
_lock = threading.Lock()


def bind_caller(raw: str | None):
    name = (raw or "").strip()
    if not _CLIENT.fullmatch(name):
        name = ""
    return _caller.set(name)


def reset_caller(token) -> None:
    _caller.reset(token)


def current_caller() -> str:
    return _caller.get()


def clean_files(files: dict[str, str]) -> dict[str, str]:
    if len(files) > _MAX_FILES:
        raise ValueError(f"at most {_MAX_FILES} local files")
    cleaned: dict[str, str] = {}
    for raw_path, text in files.items():
        path = _relative_path(raw_path)
        if not isinstance(text, str):
            raise ValueError(f"local file {path} must be text")
        if len(text.encode("utf-8")) > _MAX_BYTES:
            raise ValueError(f"local file {path} is too large")
        cleaned[path] = text.replace("\r\n", "\n")
    return cleaned


def publish(checkout_id: str, caller: str, files: dict[str, str]) -> int:
    name = caller.strip()
    if not _CLIENT.fullmatch(name):
        raise ValueError("client must be a short name")
    cleaned = clean_files(files)
    key = (checkout_id, name)
    with _lock:
        if not cleaned:
            _files.pop(key, None)
            return 0
        _files[key] = cleaned
        return len(cleaned)


def published(checkout_id: str, caller: str) -> dict[str, str]:
    name = caller.strip()
    if not checkout_id or not name:
        return {}
    with _lock:
        found = _files.get((checkout_id, name))
        return dict(found) if found else {}


def clear_all() -> None:
    with _lock:
        _files.clear()


def slice_local(text: str, start: int | None, end: int | None) -> dict[str, object]:
    lines = text.splitlines()
    total = len(lines)
    first = 1 if start is None else start
    last = total if end is None else end
    if total == 0:
        return {"start_line": 1, "end_line": 1, "total_lines": 0, "truncated": False, "text": ""}
    first = min(max(1, first), total)
    last = min(max(first, last), total)
    window = lines[first - 1 : last]
    body = "\n".join(window)
    if body:
        body += "\n"
    return {
        "start_line": first,
        "end_line": last,
        "total_lines": total,
        "truncated": False,
        "text": body,
    }


def _relative_path(raw: str) -> str:
    text = raw.strip().replace("\\", "/")
    if not text or text.startswith("/") or ":" in text:
        raise ValueError(f"local path must be relative: {raw}")
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"local path must stay inside the checkout: {raw}")
    return "/".join(parts)
