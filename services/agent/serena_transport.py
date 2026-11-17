"""MCP streamable HTTP to the Serena sidecar.

The agent does not import Serena. A named tool becomes one `tools/call` on
the sidecar the agent reaches inside Compose, `http://serena:9121/mcp`.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

_DEFAULT_BASE_URL = "http://serena:9121"
_HANDSHAKE_VERSION = "2025-11-25"
_TIMEOUT_SECONDS = float(os.getenv("AGENT_REQUEST_TIMEOUT_SECONDS", "14400"))


class McpSidecar:
    """One Serena session. `call` returns the named-tool result shape."""

    def __init__(self, base_url: str | None = None) -> None:
        chosen = os.getenv("SERENA_BASE_URL", _DEFAULT_BASE_URL) if base_url is None else base_url
        root = chosen.rstrip("/")
        self.endpoint = root if root.endswith("/mcp") else f"{root}/mcp"
        self._session_id: str | None = None
        self._protocol_version = _HANDSHAKE_VERSION
        self._next_id = 0

    async def call(self, name: str, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as http:
            try:
                message = await self._tools_call(http, name, arguments, request_id)
            except ConnectionError as exc:
                if self._session_id is None or "404" not in str(exc):
                    raise
                self._session_id = None
                message = await self._tools_call(http, name, arguments, request_id)
            if _session_rejected(message) and self._session_id is not None:
                self._session_id = None
                message = await self._tools_call(http, name, arguments, request_id)
            return _named_result(message)

    async def _tools_call(
        self,
        http: httpx.AsyncClient,
        name: str,
        arguments: dict[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        await self._ensure_session(http, request_id)
        return await self._post(
            http,
            {
                "jsonrpc": "2.0",
                "id": self._allocate(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            request_id,
        )

    async def _ensure_session(self, http: httpx.AsyncClient, request_id: str) -> None:
        if self._session_id:
            return
        opened = await self._post(
            http,
            {
                "jsonrpc": "2.0",
                "id": self._allocate(),
                "method": "initialize",
                "params": {
                    "protocolVersion": _HANDSHAKE_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "pumba-agent", "version": "0"},
                },
            },
            request_id,
        )
        result = opened.get("result")
        if isinstance(result, dict) and result.get("protocolVersion"):
            self._protocol_version = str(result["protocolVersion"])
        if not self._session_id:
            raise ConnectionError("Serena did not open a session")
        await self._post(
            http,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            request_id,
        )

    def _allocate(self) -> int:
        self._next_id += 1
        return self._next_id

    async def _post(self, http: httpx.AsyncClient, payload: dict[str, Any], request_id: str) -> dict[str, Any]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "X-Request-Id": request_id,
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
            headers["mcp-protocol-version"] = self._protocol_version
        async with http.stream("POST", self.endpoint, json=payload, headers=headers) as response:
            session = response.headers.get("mcp-session-id")
            if session:
                self._session_id = session
            if response.status_code == 202:
                await response.aread()
                return {}
            content_type = response.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                message = await _read_sse(response)
            else:
                body = (await response.aread()).decode("utf-8", errors="replace")
                message = _json_object(body)
            if response.status_code >= 400 and "error" not in message:
                detail = json.dumps(message) if message else f"Serena HTTP {response.status_code}"
                raise ConnectionError(detail)
            return message


def _session_rejected(message: dict[str, Any]) -> bool:
    error = message.get("error")
    if not isinstance(error, dict):
        return False
    return "session" in str(error.get("message") or "").lower()


def _named_result(message: dict[str, Any]) -> dict[str, Any]:
    error = message.get("error")
    if error is not None:
        text = error.get("message") if isinstance(error, dict) else error
        return {"ok": False, "stdout": "", "stderr": str(text or "Serena tool failed"), "exit_code": 1}
    result = message.get("result")
    text = _result_text(result)
    if isinstance(result, dict) and result.get("isError") is True:
        return {"ok": False, "stdout": "", "stderr": text or "Serena tool failed", "exit_code": 1}
    return {"ok": True, "stdout": text, "stderr": "", "exit_code": 0}


def _result_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return ""
    content = result.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "\n".join(part for part in parts if part)


def _json_object(body: str) -> dict[str, Any]:
    stripped = body.strip()
    if not stripped:
        return {}
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ConnectionError(stripped) from exc
    if not isinstance(parsed, dict):
        raise ConnectionError(stripped)
    return parsed


def _event_payload(block: str) -> dict[str, Any] | None:
    data_lines: list[str] = []
    for line in block.split("\n"):
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    data = "\n".join(data_lines).strip()
    if not data:
        return None
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict) and ("result" in parsed or "error" in parsed):
        return parsed
    return None


async def _read_sse(response: httpx.Response) -> dict[str, Any]:
    buffer = ""
    pending_cr = False
    async for chunk in response.aiter_text():
        if pending_cr:
            chunk = "\r" + chunk
            pending_cr = False
        if chunk.endswith("\r"):
            pending_cr = True
            chunk = chunk[:-1]
        buffer += chunk.replace("\r\n", "\n").replace("\r", "\n")
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            message = _event_payload(block)
            if message is not None:
                return message
    message = _event_payload(buffer)
    if message is not None:
        return message
    raise ConnectionError("Serena returned no tool result")
