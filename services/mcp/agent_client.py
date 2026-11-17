"""HTTP client from `mcp` to `agent` `POST /chat`.

Env names and the response shape are pinned in CONTRACTS.md. Re-reads env
on every call so tests can monkeypatch without reloading the module.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

_DEFAULT_AGENT = "http://agent:8090"
_DEFAULT_TIMEOUT = "172800"


class AgentError(Exception):
    """Agent refused the call or the hop failed. Shown as the MCP tool result."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _agent_base_url() -> str:
    return os.getenv("AGENT_BASE_URL", _DEFAULT_AGENT).rstrip("/")


def _bearer() -> str:
    return os.getenv("AGENT_BEARER_TOKEN", "")


def _timeout() -> float:
    return float(os.getenv("MCP_REQUEST_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT))


def _query_body(
    query: str,
    *,
    conversation_id: Optional[str] = None,
    instructions: Optional[str] = None,
    workspace: Optional[str] = None,
    max_reply_tokens: Optional[int] = None,
    tools: Optional[bool] = None,
) -> dict[str, Any]:
    cleaned = query.strip()
    if not cleaned:
        raise AgentError("query must not be empty")
    note = (instructions or "").strip()
    if len(note) > 4000:
        raise AgentError("instructions must be at most 4000 characters")
    body: dict[str, Any] = {"query": cleaned}
    cid = (conversation_id or "").strip()
    if cid:
        body["conversation_id"] = cid
    if note:
        body["instructions"] = note
    checkout = (workspace or "").strip()
    if checkout:
        body["workspace"] = checkout
    if max_reply_tokens is not None:
        body["max_reply_tokens"] = max_reply_tokens
    if tools is not None:
        body["tools"] = tools
    return body


async def _post_agent(
    path: str,
    body: dict[str, Any],
    *,
    caller: str = "",
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
    token = _bearer()
    if not token:
        raise AgentError("AGENT_BEARER_TOKEN is not set")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    name = caller.strip()
    if name:
        headers["X-Client"] = name
    url = f"{_agent_base_url()}{path}"
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=_timeout())
    try:
        try:
            response = await http.post(url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise AgentError(f"agent timed out after {_timeout():.0f}s") from exc
        except httpx.HTTPError as exc:
            raise AgentError(f"agent unreachable: {exc}") from exc
    finally:
        if own_client:
            await http.aclose()

    if response.status_code == 401:
        raise AgentError("agent rejected the bearer token", status_code=401)
    if response.status_code == 503:
        raise AgentError("agent is busy (too many concurrent /chat requests)", status_code=503)
    if response.status_code >= 400:
        detail = response.text[:300]
        raise AgentError(f"agent returned HTTP {response.status_code}: {detail}", status_code=response.status_code)
    try:
        payload = response.json()
    except ValueError as exc:
        raise AgentError("agent returned non-JSON") from exc
    if not isinstance(payload, dict):
        raise AgentError("agent returned a non-object JSON body")
    return payload


async def ask_agent(
    query: str,
    conversation_id: Optional[str] = None,
    *,
    instructions: Optional[str] = None,
    workspace: Optional[str] = None,
    max_reply_tokens: Optional[int] = None,
    caller: str = "",
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
    """POST /chat and return the parsed JSON object."""
    body = _query_body(
        query,
        conversation_id=conversation_id,
        instructions=instructions,
        workspace=workspace,
        max_reply_tokens=max_reply_tokens,
        tools=False,
    )
    return await _post_agent("/chat", body, caller=caller, client=client)


async def lookup_agent(
    query: str,
    *,
    workspace: Optional[str] = None,
    caller: str = "",
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
    """POST /lookup. Locations from the index, no chat model."""
    body = _query_body(query, workspace=workspace)
    return await _post_agent("/lookup", body, caller=caller, client=client)


async def read_agent(
    path: str,
    *,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    workspace: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
    """POST /read. File text from the checkout, no chat model."""
    cleaned = path.strip()
    if not cleaned:
        raise AgentError("path must not be empty")
    body: dict[str, Any] = {"path": cleaned}
    if start_line is not None:
        body["start_line"] = start_line
    if end_line is not None:
        body["end_line"] = end_line
    checkout = (workspace or "").strip()
    if checkout:
        body["workspace"] = checkout
    return await _post_agent("/read", body, client=client)


def format_chat_result(payload: dict[str, Any]) -> str:
    """Answer and citations. The conversation id stays out of the text."""
    answer = str(payload.get("answer") or "").rstrip()
    lines = [answer] if answer else ["(empty answer)"]

    sources = payload.get("sources") or []
    if isinstance(sources, list) and sources:
        lines.append("")
        lines.append("Sources:")
        for source in sources:
            if not isinstance(source, dict):
                continue
            path = source.get("file_path", "?")
            start = source.get("start_line", "?")
            end = source.get("end_line", "?")
            symbol = source.get("symbol_name")
            loc = f"{path}:{start}-{end}"
            lines.append(f"- {loc} ({symbol})" if symbol else f"- {loc}")
    return "\n".join(lines)


def format_lookup(payload: dict[str, Any]) -> str:
    hits = payload.get("hits") or []
    if not isinstance(hits, list) or not hits:
        return "No indexed match."
    blocks: list[str] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        path = hit.get("file_path", "?")
        start = hit.get("start_line", "?")
        end = hit.get("end_line", "?")
        symbol = hit.get("symbol_name")
        loc = f"{path}:{start}-{end}"
        header = f"{loc} ({symbol})" if symbol else loc
        if hit.get("source") == "working_copy":
            header = f"{header} [working copy]"
        excerpt = str(hit.get("excerpt") or "").strip()
        blocks.append(f"{header}\n{excerpt}" if excerpt else header)
    return "\n\n".join(blocks) if blocks else "No indexed match."


async def check_agent(
    action: str,
    *,
    preset: Optional[str] = None,
    path: Optional[str] = None,
    filter: Optional[str] = None,
    workspace: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
    """POST /check. Bounded build, test, or compile log. No chat model."""
    cleaned = action.strip()
    if cleaned not in {"build", "test", "compile"}:
        raise AgentError("action must be build, test, or compile")
    body: dict[str, Any] = {"action": cleaned}
    if preset:
        body["preset"] = preset
    if path:
        body["path"] = path
    if filter:
        body["filter"] = filter
    checkout = (workspace or "").strip()
    if checkout:
        body["workspace"] = checkout
    return await _post_agent("/check", body, client=client)


async def lsp_fact_agent(
    path: str,
    *,
    route: str,
    line: Optional[int] = None,
    character: Optional[int] = None,
    direction: Optional[str] = None,
    preset: Optional[str] = None,
    workspace: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
    """POST /lsp/outline, /lsp/calls, or /lsp/types. Facts only."""
    cleaned = path.strip()
    if not cleaned:
        raise AgentError("path must not be empty")
    body: dict[str, Any] = {"path": cleaned}
    if line is not None:
        body["line"] = line
    if character is not None:
        body["character"] = character
    if direction:
        body["direction"] = direction
    if preset:
        body["preset"] = preset
    checkout = (workspace or "").strip()
    if checkout:
        body["workspace"] = checkout
    return await _post_agent(route, body, client=client)


def format_check(payload: dict[str, Any]) -> str:
    action = payload.get("action", "check")
    code = payload.get("exit_code")
    parts = [f"{action} exit {code}"]
    out = str(payload.get("stdout") or "").rstrip()
    err = str(payload.get("stderr") or "").rstrip()
    if out:
        parts.append(out)
    if err:
        parts.append(err)
    if payload.get("truncated"):
        parts.append("(truncated)")
    return "\n".join(parts)


def format_fact(payload: dict[str, Any]) -> str:
    out = str(payload.get("stdout") or "").rstrip()
    err = str(payload.get("stderr") or "").rstrip()
    parts: list[str] = []
    if payload.get("ok") is False:
        parts.append(f"exit {payload.get('exit_code')}")
    if out:
        parts.append(out)
    if err:
        parts.append(err)
    if payload.get("truncated"):
        parts.append("(truncated)")
    return "\n".join(parts) if parts else "(empty)"


def format_read(payload: dict[str, Any]) -> str:
    path = payload.get("path", "?")
    start = payload.get("start_line", "?")
    end = payload.get("end_line", "?")
    text = str(payload.get("text") or "")
    lines = [f"{path}:{start}-{end}", text.rstrip()]
    if payload.get("truncated"):
        lines.append("(truncated)")
    return "\n".join(lines)
