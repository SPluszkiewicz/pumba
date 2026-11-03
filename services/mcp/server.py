"""FastMCP server: facts from this stack, plus one short cited judgment."""

import os
from contextvars import ContextVar

from agent_client import (
    AgentError,
    ask_agent,
    check_agent,
    format_chat_result,
    format_check,
    format_fact,
    format_lookup,
    lookup_agent,
    lsp_fact_agent,
)
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse

MCP_PORT = int(os.getenv("MCP_PORT", "8092"))
workspace_header: ContextVar[str] = ContextVar("workspace_header", default="")

mcp = FastMCP(
    "local-llm",
    instructions=(
        "Tools for the checkout named by X-Workspace. "
        "lookup, check, outline, calls, and types return facts and must be used for those jobs. "
        "ask only judges text already retrieved. It does not search, build, test, compile, or list symbols. "
        "Paste each tool result unchanged."
    ),
    host="0.0.0.0",
    port=MCP_PORT,
    json_response=True,
    stateless_http=True,
)


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


_ASK_NOTE = (
    "At most 12 lines. Every claim cites path:line from the retrieved code or from a tool result. "
    "Quote the code. If the code does not show it, say so. "
    "Do not invent a signature, a path, or a behavior. Do not repeat a sentence."
)


def _workspace() -> str:
    return workspace_header.get() or os.getenv("MCP_WORKSPACE", "")


def _tool_error(exc: AgentError) -> ToolError:
    return ToolError(str(exc))


@mcp.tool()
async def lookup(query: str) -> str:
    """Locate a symbol, file, or behavior in this checkout.

    Returns path, line range, symbol name, and a short excerpt from the index.
    No chat model and no opinion. Paste the result unchanged.
    """
    try:
        payload = await lookup_agent(query, workspace=_workspace())
    except AgentError as exc:
        raise _tool_error(exc) from exc
    return format_lookup(payload)


@mcp.tool()
async def check(action: str, path: str = "", preset: str = "", filter: str = "") -> str:
    """Run build, test, or compile and return the log.

    action is build, test, or compile. compile requires path (repo-relative).
    test may set filter (ctest -R). No opinion on the result. Paste the log unchanged.
    """
    try:
        payload = await check_agent(
            action,
            path=path or None,
            preset=preset or None,
            filter=filter or None,
            workspace=_workspace(),
        )
    except AgentError as exc:
        raise _tool_error(exc) from exc
    return format_check(payload)


@mcp.tool()
async def outline(path: str) -> str:
    """List symbols declared in one file via clangd.

    path is repo-relative. Each line is a name, a kind, and a line number.
    Paste the result unchanged.
    """
    try:
        payload = await lsp_fact_agent(path, route="/lsp/outline", workspace=_workspace())
    except AgentError as exc:
        raise _tool_error(exc) from exc
    return format_fact(payload)


@mcp.tool()
async def calls(path: str, line: int, character: int, direction: str = "both") -> str:
    """List callers and callees of the symbol at a 1-based position.

    direction is in, out, or both. Each line is name and path:line.
    Paste the result unchanged.
    """
    try:
        payload = await lsp_fact_agent(
            path,
            route="/lsp/calls",
            line=line,
            character=character,
            direction=direction,
            workspace=_workspace(),
        )
    except AgentError as exc:
        raise _tool_error(exc) from exc
    return format_fact(payload)


@mcp.tool()
async def types(path: str, line: int, character: int, direction: str = "both") -> str:
    """List base and derived types of the symbol at a 1-based position.

    direction is super, sub, or both. Each line is name and path:line.
    Paste the result unchanged.
    """
    try:
        payload = await lsp_fact_agent(
            path,
            route="/lsp/types",
            line=line,
            character=character,
            direction=direction,
            workspace=_workspace(),
        )
    except AgentError as exc:
        raise _tool_error(exc) from exc
    return format_fact(payload)


@mcp.tool()
async def ask(query: str, conversation_id: str = "") -> str:
    """Short judgment on excerpts already in front of you. Cite path:line or say the code does not show it.

    Do not use ask to search, build, test, compile, or list symbols.
    Those are lookup, check, outline, calls, and types.
    Paste the tool text to the user unchanged. Do not rewrite it.
    """
    try:
        payload = await ask_agent(
            query,
            conversation_id,
            instructions=_ASK_NOTE,
            workspace=_workspace(),
            max_reply_tokens=400,
        )
    except AgentError as exc:
        raise _tool_error(exc) from exc
    return format_chat_result(payload)
