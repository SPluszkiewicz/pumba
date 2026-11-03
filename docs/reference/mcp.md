# MCP server

The `mcp` service is a thin [Model Context Protocol](https://modelcontextprotocol.io/)
front for `agent`. Cursor, Claude Code, or OpenCode talk MCP. Facts
(`lookup`, `check`, `outline`, `calls`, `types`) do not call the chat model.
`ask` calls `POST /chat` with `AGENT_BEARER_TOKEN`, with no tool schema and
a 400-token cap. The token stays in the container.

See `CONTRACTS.md` for ports and env vars.

## What it exposes

| MCP primitive | Name | Forwards to |
|---|---|---|
| tool | `lookup` | `POST {AGENT_BASE_URL}/lookup` (index only, no chat model) |
| tool | `check` | `POST {AGENT_BASE_URL}/check` (build, test, or compile log) |
| tool | `outline` | `POST {AGENT_BASE_URL}/lsp/outline` |
| tool | `calls` | `POST {AGENT_BASE_URL}/lsp/calls` |
| tool | `types` | `POST {AGENT_BASE_URL}/lsp/types` |
| tool | `ask` | `POST {AGENT_BASE_URL}/chat` (short cited judgment, 400 tokens) |
| — | `GET /health` | local liveness (not MCP) |

`lookup` takes `query` and returns path, line range, symbol, and a short excerpt.
Dirty C/C++ files from `svn status` or `git status` are re-read and listed
first, marked `[working copy]`.
`check` takes `action` (`build`, `test`, or `compile`). `compile` needs `path`.
`outline`, `calls`, and `types` return clangd lines (`name`, path, line), not a paragraph.
`ask` takes `query` and an optional `conversation_id`. It judges excerpts already
retrieved. It does not search, build, test, compile, or list symbols, and the
agent does not give it the tool schema. Its text is the answer and a `Sources:`
list. The conversation id is not part of that text. Paste each result unchanged.

Reading a file, searching text, and SVN status are not tools here. Use the
Filesystem or Git MCP server, and a read-only [svn-mcp](https://github.com/WillBrock/svn-mcp/).
The local model on `127.0.0.1:8080` is documented in
[Using VS Code](../guides/vscode.md).

## From the host

After `python scripts/deploy.py up`, the Streamable HTTP endpoint is bound
to loopback only:

```
http://127.0.0.1:8092/mcp
```

VS Code (`.vscode/mcp.json` in this repo):

```json
{
  "servers": {
    "local-llm": {
      "type": "http",
      "url": "http://127.0.0.1:8092/mcp",
      "headers": {
        "X-Workspace": "D:/svn/ticket-142"
      }
    }
  }
}
```

`python scripts/deploy.py workspace <checkout>` writes that header when
`.vscode/mcp.json` is missing, and sets it on an existing `local-llm`
server. The header selects the checkout. Without it, `ask` uses the
default tree (`CODEBASE_PATH`, usually `sample/`).

Open Copilot Chat, pick the `local-llm` tools, and call `ask`. The token
stays inside the `mcp` container; the editor does not send
`AGENT_BEARER_TOKEN`.

`mcp` is on `client-net` and `publish-net`. The second network is what
makes Docker Desktop actually bind `127.0.0.1:8092`. An internal-only
container keeps the host port empty.

Cursor (`mcp.json`):

```json
{
  "mcpServers": {
    "local-llm": {
      "url": "http://127.0.0.1:8092/mcp",
      "headers": {
        "X-Workspace": "D:/svn/ticket-142"
      }
    }
  }
}
```

Claude Code:

```bash
claude mcp add --transport http local-llm http://127.0.0.1:8092/mcp
```

On Windows + WSL2, the publish is inside the WSL distro. If the IDE runs on
Windows and cannot reach `127.0.0.1:8092`, use the WSL address or stdio:

```bash
podman compose run --rm -T -e MCP_TRANSPORT=stdio -e MCP_WORKSPACE=D:/svn/ticket-142 mcp
```

Stdio is the same tool, no listen port. Point the IDE `command`/`args` at
that compose invocation (working directory = repo root).

## What it does not do

- It does not stream tokens (uses `/chat`, not `/chat/stream`).
- It does not read files or run `git` / `svn`. Those servers are separate.
- It does not talk to Jira or Confluence. That server is `atlassian` in
  `.vscode/mcp.json` and `.cursor/mcp.json`, configured by `atlassian.env`.
- `check` is the build, test, or compile log. It does not judge the log.
- It is not published on `0.0.0.0`. Do not change the compose `ports:` bind.
