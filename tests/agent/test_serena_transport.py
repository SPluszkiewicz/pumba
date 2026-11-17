"""The real sidecar speaks MCP. A fake at tool_loop.sidecar is not this test.

A local server stands in for the Compose sidecar. It speaks streamable HTTP
the way Serena 2.1 does: initialize, then tools/call, answers as SSE. The
assertion is the named-tool result, not the handshake.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
import tool_loop
from serena_transport import McpSidecar

TOOL_TEXT = "Widget declared in src/widget.hpp:4"
MISSING_DATABASE = "compile_commands.json is missing"
SESSION = "gate-session"


class _McpServer(ThreadingHTTPServer):
    def __init__(self, tool_result: dict[str, Any] | None = None, *, hold: bool = False) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.initialized = False
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.tool_result = tool_result or {
            "content": [{"type": "text", "text": TOOL_TEXT}],
            "isError": False,
        }
        self.hold = hold
        self.started = threading.Event()
        self.release = threading.Event()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        server = self.server
        assert isinstance(server, _McpServer)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            message = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            self._json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            return
        method = message.get("method")
        accept = self.headers.get("Accept", "")
        if "application/json" not in accept or "text/event-stream" not in accept:
            self._text(406, "Not Acceptable")
            return
        if self.path != "/mcp":
            self._text(404, "not the mcp endpoint")
            return
        if method == "initialize":
            server.initialized = True
            self._sse(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "result": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "serverInfo": {"name": "serena", "version": "2.1.0"},
                    },
                },
                session=SESSION,
            )
            return
        if method == "notifications/initialized":
            self._empty(202)
            return
        if method == "tools/call":
            if self.headers.get("mcp-session-id") != SESSION or not server.initialized:
                self._json(
                    400,
                    {
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "error": {"code": -32600, "message": "Missing session ID"},
                    },
                )
                return
            params = message.get("params") or {}
            name = str(params.get("name") or "")
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            server.calls.append((name, arguments))
            if server.hold:
                server.started.set()
                server.release.wait(5)
            self._sse(
                {"jsonrpc": "2.0", "id": message.get("id"), "result": server.tool_result},
                session=SESSION,
            )
            return
        self._json(
            400,
            {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {"code": -32601, "message": f"unknown method {method}"},
            },
        )

    def _sse(self, payload: dict[str, Any], *, session: str) -> None:
        # A priming event with empty data, then the JSON-RPC message. Serena's
        # streamable HTTP server sends both.
        body = ("id: prime\ndata:\n\n" f"event: message\ndata: {json.dumps(payload)}\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("mcp-session-id", session)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, status: int, text: str) -> None:
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.mark.asyncio
async def test_find_symbol_reads_the_sidecar_tool_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """A named tool with no test double returns the sidecar's tool text."""
    server = _McpServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    monkeypatch.setattr(tool_loop, "sidecar", McpSidecar(base_url=f"http://{host}:{port}"))
    try:
        result = await tool_loop.execute_tool_call(
            None,  # type: ignore[arg-type]
            "find_symbol",
            {"name_path_pattern": "Widget"},
            "req-transport",
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result["ok"] is True
    assert result["stdout"] == TOOL_TEXT
    assert server.calls == [("find_symbol", {"name_path_pattern": "Widget"})]


def test_the_unpatched_sidecar_targets_serena_on_port_9121(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no test double, the call goes to the Compose service name and port."""
    monkeypatch.delenv("SERENA_BASE_URL", raising=False)
    assert isinstance(tool_loop.sidecar, McpSidecar)
    assert McpSidecar().endpoint == "http://serena:9121/mcp"


@pytest.mark.asyncio
async def test_unreachable_sidecar_fails_the_named_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """A closed port is a failed tool. The loop still returns."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setattr(tool_loop, "sidecar", McpSidecar(base_url=f"http://127.0.0.1:{port}"))

    result = await tool_loop.execute_tool_call(
        None,  # type: ignore[arg-type]
        "find_symbol",
        {"name_path_pattern": "Widget"},
        "req-down",
    )

    assert result["ok"] is False
    assert result["stderr"]


@pytest.mark.asyncio
async def test_mcp_tool_error_is_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    """A sidecar tool error, such as a missing compile database, is ok: false with its text."""
    server = _McpServer(
        {"content": [{"type": "text", "text": MISSING_DATABASE}], "isError": True},
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    monkeypatch.setattr(tool_loop, "sidecar", McpSidecar(base_url=f"http://{host}:{port}"))
    try:
        result = await tool_loop.execute_tool_call(
            None,  # type: ignore[arg-type]
            "find_symbol",
            {"name_path_pattern": "Widget"},
            "req-missing-db",
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result["ok"] is False
    assert result["stderr"] == MISSING_DATABASE


@pytest.mark.asyncio
async def test_cancel_stops_the_in_flight_mcp_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stopping the turn cancels the HTTP call instead of waiting it out."""
    server = _McpServer(hold=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    monkeypatch.setattr(tool_loop, "sidecar", McpSidecar(base_url=f"http://{host}:{port}"))
    task = asyncio.create_task(
        tool_loop.execute_tool_call(
            None,  # type: ignore[arg-type]
            "find_symbol",
            {"name_path_pattern": "Widget"},
            "req-cancel-mcp",
        )
    )
    try:
        assert await asyncio.to_thread(server.started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
