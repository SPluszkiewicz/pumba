from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import product
from agent_client import AgentError
from mcp.server.fastmcp.exceptions import ToolError
from server import ask, mcp


def test_mcp_server_name_matches_product() -> None:
    assert mcp.name == product.MCP_SERVER


@pytest.mark.asyncio
async def test_list_tools_exposes_facts_and_ask() -> None:
    tools = await mcp.list_tools()
    names = [tool.name for tool in tools]
    assert names == ["lookup", "check", "outline", "calls", "types", "ask"]
    for tool in tools:
        assert "unchanged" in (tool.description or "")
    ask_tool = next(tool for tool in tools if tool.name == "ask")
    assert "Do not use ask to search" in (ask_tool.description or "")
    assert "Build and tests stay" not in (ask_tool.description or "")


@pytest.mark.asyncio
async def test_ask_tool_formats_agent_payload() -> None:
    payload = {
        "answer": "Use compile_file.",
        "sources": [{"file_path": "src/foo.cpp", "start_line": 2, "end_line": 9}],
        "conversation_id": "c-9",
    }
    with patch("server.ask_agent", new=AsyncMock(return_value=payload)) as mocked:
        text = await ask(query="how do I compile one TU?")
    mocked.assert_awaited_once()
    assert "Use compile_file." in text
    assert "conversation_id" not in text


@pytest.mark.asyncio
async def test_ask_tool_surfaces_agent_error() -> None:
    with patch("server.ask_agent", new=AsyncMock(side_effect=AgentError("agent is busy", 503))):
        with pytest.raises(ToolError, match="busy"):
            await ask(query="hello")


@pytest.mark.asyncio
async def test_call_tool_through_fastmcp() -> None:
    payload: dict[str, Any] = {"answer": "ok", "sources": [], "conversation_id": "x"}
    with patch("server.ask_agent", new=AsyncMock(return_value=payload)):
        result = await mcp.call_tool("ask", {"query": "ping"})
    # FastMCP returns a list of content blocks or a CallToolResult.
    rendered = str(result)
    assert "ok" in rendered
    assert "conversation_id" not in rendered
