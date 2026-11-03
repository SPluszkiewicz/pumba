"""Entrypoint: Streamable HTTP (default) or stdio. See CONTRACTS.md."""

import os

import uvicorn
from server import mcp, workspace_header


class WorkspaceHeader:
    """Copy X-Workspace into the same task that runs `ask`."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        raw = ""
        for key, value in scope.get("headers") or []:
            if key.lower() == b"x-workspace":
                raw = value.decode("utf-8", errors="replace").strip()
                break
        token = workspace_header.set(raw)
        try:
            await self.app(scope, receive, send)
        finally:
            workspace_header.reset(token)


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "http").strip().lower()
    if transport == "stdio":
        mcp.run(transport="stdio")
        return
    app = mcp.streamable_http_app()
    app.add_middleware(WorkspaceHeader)
    uvicorn.run(app, host=mcp.settings.host, port=mcp.settings.port)


if __name__ == "__main__":
    main()
