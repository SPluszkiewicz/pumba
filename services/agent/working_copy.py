"""Put the checkout's uncommitted C/C++ text in front of the shared index.

`svn status` and `git status` name the files. `/read-file` on `tools` reads
them. The index stays the shared revision; these excerpts are this ticket.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx
from checkouts import TOOLS_HTTP_TIMEOUT, Checkout, parse_status_paths
from rag import RetrievedChunk, assemble_context, clip_excerpt

MAX_OVERLAY_FILES = 6
MAX_OVERLAY_LINES = 80


def chunks_with_live(
    chunks: list[RetrievedChunk], live: dict[str, str]
) -> tuple[str, list[dict[str, Any]], list[RetrievedChunk]]:
    """Caller files replace the same path and are cited from line 1."""
    refreshed: list[RetrievedChunk] = []
    seen: set[str] = set()
    for chunk in chunks:
        path = chunk.file_path.replace("\\", "/")
        if path in live:
            text = _first_lines(live[path])
            refreshed.append(
                RetrievedChunk(path, 1, _line_count(text), text, chunk.symbol_name, chunk.score)
            )
            seen.add(path)
        else:
            refreshed.append(chunk)
    extras = [
        RetrievedChunk(path, 1, _line_count(text), text, None, 0.0)
        for path, raw in live.items()
        if path not in seen
        for text in [_first_lines(raw)]
    ]
    refreshed = extras + refreshed
    context_text, sources = assemble_context(refreshed)
    return context_text, sources, refreshed


def _first_lines(text: str) -> str:
    lines = text.replace("\r\n", "\n").splitlines()
    kept = lines[:MAX_OVERLAY_LINES]
    return "\n".join(kept).strip()


def _line_count(text: str) -> int:
    if not text:
        return 1
    return text.count("\n") + 1


def merge_hits(hits: list[dict[str, Any]], live: dict[str, str]) -> list[dict[str, Any]]:
    """Working-copy excerpts first. An indexed hit for a dirty path is replaced."""
    refreshed: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hit in hits:
        path = str(hit.get("file_path") or "").replace("\\", "/")
        if path in live:
            refreshed.append({**hit, "excerpt": clip_excerpt(live[path]), "source": "working_copy"})
            seen.add(path)
        else:
            rest.append({**hit, "source": hit.get("source") or "index"})
    extras: list[dict[str, Any]] = []
    for path, text in live.items():
        if path in seen:
            continue
        line_count = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
        extras.append(
            {
                "file_path": path,
                "start_line": 1,
                "end_line": max(1, min(MAX_OVERLAY_LINES, line_count or 1)),
                "symbol_name": None,
                "excerpt": clip_excerpt(text),
                "source": "working_copy",
            }
        )
    return refreshed + extras + rest


async def overlay_hits(
    http_client: httpx.AsyncClient,
    request_id: str,
    checkout: Optional[Checkout],
    hits: list[dict[str, Any]],
    *,
    tools_base_url: str,
) -> list[dict[str, Any]]:
    if checkout is None:
        return [{**hit, "source": hit.get("source") or "index"} for hit in hits]
    if checkout.vcs == "none":
        live = await _live_texts_without_vcs(
            http_client,
            request_id,
            checkout,
            hits,
            tools_base_url=tools_base_url,
        )
    else:
        live = await _live_texts(
            http_client,
            request_id,
            checkout,
            tools_base_url=tools_base_url,
            windows=_windows_for_hits(hits),
        )
    if not live:
        return [{**hit, "source": "index"} for hit in hits]
    return merge_hits(hits, live)


async def overlay_chunks(
    http_client: httpx.AsyncClient,
    request_id: str,
    checkout: Optional[Checkout],
    chunks: list[RetrievedChunk],
    *,
    tools_base_url: str,
) -> tuple[str, list[dict[str, Any]], list[RetrievedChunk]]:
    """Refresh retrieved chunks from the working copy and rebuild the prompt block."""
    if checkout is None or not chunks:
        text, sources = assemble_context(chunks)
        return text, sources, chunks
    windows = {chunk.file_path.replace("\\", "/"): (chunk.start_line, chunk.end_line) for chunk in chunks}
    live = await _live_texts(
        http_client,
        request_id,
        checkout,
        tools_base_url=tools_base_url,
        windows=windows,
    )
    if not live:
        text, sources = assemble_context(chunks)
        return text, sources, chunks
    refreshed: list[RetrievedChunk] = []
    seen: set[str] = set()
    for chunk in chunks:
        path = chunk.file_path.replace("\\", "/")
        if path in live:
            refreshed.append(
                RetrievedChunk(
                    path,
                    chunk.start_line,
                    chunk.end_line,
                    live[path],
                    chunk.symbol_name,
                    chunk.score,
                )
            )
            seen.add(path)
        else:
            refreshed.append(chunk)
    extras = [
        RetrievedChunk(path, 1, min(MAX_OVERLAY_LINES, text.count("\n") + 1), text, None, 0.0)
        for path, text in live.items()
        if path not in seen
    ]
    refreshed = extras + refreshed
    context_text, sources = assemble_context(refreshed)
    return context_text, sources, refreshed


def _windows_for_hits(hits: list[dict[str, Any]]) -> dict[str, tuple[int, int]]:
    windows: dict[str, tuple[int, int]] = {}
    for hit in hits:
        path = str(hit.get("file_path") or "").replace("\\", "/")
        if not path:
            continue
        end = int(hit.get("end_line") or 1)
        windows[path] = (1, max(MAX_OVERLAY_LINES, end))
    return windows


async def _live_texts_without_vcs(
    http_client: httpx.AsyncClient,
    request_id: str,
    checkout: Checkout,
    hits: list[dict[str, Any]],
    *,
    tools_base_url: str,
) -> dict[str, str]:
    """For a checkout without `.git`/`.svn` (e.g. `sample/`), compare each hit's indexed lines with disk."""
    live: dict[str, str] = {}
    seen: set[str] = set()
    for hit in hits[:MAX_OVERLAY_FILES]:
        path = str(hit.get("file_path") or "").replace("\\", "/")
        if not path or path in seen:
            continue
        seen.add(path)
        start = int(hit.get("start_line") or 1)
        end = int(hit.get("end_line") or start)
        indexed_excerpt = str(hit.get("excerpt") or "").replace("\r\n", "\n").strip()
        raw_span = await _read_raw_window(
            http_client,
            request_id,
            checkout.mount,
            path,
            start,
            end,
            tools_base_url=tools_base_url,
        )
        if raw_span is None:
            continue
        if clip_excerpt(raw_span) != indexed_excerpt:
            full_window = await _read_raw_window(
                http_client,
                request_id,
                checkout.mount,
                path,
                1,
                max(MAX_OVERLAY_LINES, end),
                tools_base_url=tools_base_url,
            )
            if full_window and full_window.strip():
                live[path] = full_window.replace("\r\n", "\n").strip()
    return live


async def _live_texts(
    http_client: httpx.AsyncClient,
    request_id: str,
    checkout: Checkout,
    *,
    tools_base_url: str,
    windows: dict[str, tuple[int, int]],
) -> dict[str, str]:
    stdout = await _status_stdout(http_client, request_id, checkout, tools_base_url=tools_base_url)
    dirty = parse_status_paths(checkout.vcs, stdout)[:MAX_OVERLAY_FILES]
    live: dict[str, str] = {}
    for path in dirty:
        start, end = windows.get(path, (1, MAX_OVERLAY_LINES))
        text = await _read_lines(
            http_client,
            request_id,
            checkout.mount,
            path,
            start,
            end,
            tools_base_url=tools_base_url,
        )
        if text:
            live[path] = text
    return live


async def _status_stdout(
    http_client: httpx.AsyncClient,
    request_id: str,
    checkout: Checkout,
    *,
    tools_base_url: str,
) -> str:
    if checkout.vcs == "svn":
        route = "/svn/status"
    elif checkout.vcs == "git":
        route = "/git/status"
    else:
        return ""
    headers = {"X-Request-Id": request_id, "X-Project-Root": checkout.mount}
    try:
        response = await http_client.post(
            f"{tools_base_url.rstrip('/')}{route}",
            json={},
            headers=headers,
            timeout=TOOLS_HTTP_TIMEOUT,
        )
        body = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return ""
    if not isinstance(body, dict):
        return ""
    return str(body.get("stdout") or "")


async def _read_raw_window(
    http_client: httpx.AsyncClient,
    request_id: str,
    mount: str,
    path: str,
    start: int,
    end: int,
    *,
    tools_base_url: str,
) -> Optional[str]:
    headers = {"X-Request-Id": request_id, "X-Project-Root": mount}
    try:
        response = await http_client.get(
            f"{tools_base_url.rstrip('/')}/read-file",
            params={"path": path, "start_line": start, "end_line": end},
            headers=headers,
            timeout=TOOLS_HTTP_TIMEOUT,
        )
        body = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    if response.status_code >= 400 or not isinstance(body, dict) or not body.get("ok", True):
        return None
    return str(body.get("stdout") or "")


async def _read_lines(
    http_client: httpx.AsyncClient,
    request_id: str,
    mount: str,
    path: str,
    start: int,
    end: int,
    *,
    tools_base_url: str,
) -> Optional[str]:
    raw = await _read_raw_window(
        http_client,
        request_id,
        mount,
        path,
        start,
        end,
        tools_base_url=tools_base_url,
    )
    if raw is None:
        return None
    text = raw.strip()
    return text or None
