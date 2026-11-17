# VS Code and Continue.dev

What each piece does, in plain terms:
[Plain-language overview](../concepts/en-clair.md). Getting started on
the EVO-X5 is in [Deploy on the EVO-X5](evo-x5.md).

The editor is an optional gate. The product is the page
`http://127.0.0.1:8090/ui`, where the agent reads, searches and modifies the
working copy with its named tools
([Using the assistant](using-the-assistant.md)). This guide connects
the editor to two things only: the local model (Continue) and the
`pumba` MCP server, which returns facts. Serena is **not** a
server to declare in the editor: it runs as a Compose sidecar, behind
the agent ([ADR 0001](../adr/0001-serena-sidecar.md)).

## Before opening the editor

The stack is running (`python scripts/deploy.py up`), then the working copy
is registered:

```text
python scripts/deploy.py workspace D:/svn/ticket-142
```

This command registers the copy by its remote repository, mounts it in
the containers, writes `.vscode/mcp.json` in that copy with the
`X-Workspace` header, and indexes the remote repository the first time (another
copy of the same repository does not redo the index; `--update` refreshes it).
If `.vscode/mcp.json` already exists and contains a `pumba` server, only its
`X-Workspace` header is updated. A file that is not JSON, or
has no `pumba` server, is left as is.

If the editor is on a different machine from the one serving the model,
open an SSH tunnel from the workstation where VS Code runs:

```text
ssh -L 8090:127.0.0.1:8090 -L 8092:127.0.0.1:8092 -L 8080:127.0.0.1:8080 user@server-machine
```

All the addresses below then stay `127.0.0.1`.

## 1. Continue.dev: the local model and the MCP

Continue reads `~/.continue/config.yaml` (on Windows:
`%USERPROFILE%\.continue\config.yaml`), or `.continue/assistants/` in
the open folder. The current format is YAML.

On the EVO-X5, the model identifier is `mistral-small-4`. On an
NVIDIA 14B profile, it is `Qwen2.5-Coder-14B-Instruct-Q5_K_M`. The exact
list that `llm` serves is at `http://127.0.0.1:8080/v1/models`.

```yaml
name: pumba
version: 1.0.0
schema: v1

models:
  - name: Mistral Small 4
    provider: openai
    model: mistral-small-4
    apiBase: http://127.0.0.1:8080/v1
    apiKey: local
    roles:
      - chat
      - edit
      - apply
    capabilities:
      - tool_use

mcpServers:
  - name: pumba
    type: streamable-http
    url: http://127.0.0.1:8092/mcp
    requestOptions:
      headers:
        X-Workspace: D:/svn/ticket-142
```

`apiKey` only needs to be non-empty. `X-Workspace` is the path of the
copy on the machine where the stack runs; it must have been registered by
`deploy.py workspace`, otherwise the agent answers HTTP 400 ("workspace is not
registered") and the MCP tool fails. MCP tools are only called in Continue's **Agent**
mode. It is Continue that modifies the files in the editor; the MCP
only returns facts to it.

If an old installation still has `~/.continue/config.json`, keep
only one of the two files: `config.yaml` takes precedence.

## 2. VS Code (Copilot): the MCP only

`.vscode/mcp.json` is already written by `deploy.py workspace`:

```json
{
  "servers": {
    "pumba": {
      "type": "http",
      "url": "http://127.0.0.1:8092/mcp",
      "headers": {
        "X-Workspace": "D:/svn/ticket-142"
      }
    },
    "atlassian": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--directory", "${workspaceFolder}", "--env-file", "atlassian.env", "mcp-atlassian"]
    }
  }
}
```

The `atlassian` server (Jira, Confluence) is a separate project, configured
by `atlassian.env` (copy of `atlassian.env.example`). Pumba does not index
tickets.

The `pumba` MCP exposes five tools. Copilot writes and applies its diffs;
it calls `pumba` to read the index and the C++ facts:

| Tool | Arguments | What it returns |
| --- | --- | --- |
| `lookup` | `query` | Chunk from the index: path, lines, symbol. Modified files (`svn`/`git status`) come first, marked `[working copy]`. |
| `check` | `action` (`build`, `test` or `compile`), `path` (required for `compile`), `preset`, `filter` (`ctest -R`) | Command log, preceded by `<action> exit <code>`. |
| `calls` | `path`, `line`, `character` (1-based), `direction` (`in`, `out`, `both`) | Callers and callees (`name path:line`). |
| `types` | `path`, `line`, `character` (1-based), `direction` (`super`, `sub`, `both`) | Base and derived types (`name path:line`). |
| `ask` | `query`, `conversation_id` | Short, cited judgment (400 tokens), without tools. Paste it as is. |

There is no `outline` tool any more: the list of a file's symbols goes
through the `/ui` page (`get_symbols_overview`). Details of the routes and
responses: [MCP](../notes/contracts/mcp.md).

Pumba does not read files, does not search text and does not serve
Git or Subversion history over MCP: the editor has its own tools
for that. On `/ui`, the agent does it itself with the Serena names
(`read_file`, `search_for_pattern`, …) and the read-only `git_*` / `svn_*`
tools.

## 3. Who modifies the files

One write per change:

- In VS Code (Continue or Copilot), the editor applies the diff. The `pumba`
  MCP exposes no write tool.
- On the page `http://127.0.0.1:8090/ui`, the agent writes to the working
  copy of the default folder (`CODEBASE_PATH`) with Serena's write tools
  (`rename_symbol`, `replace_symbol_body`, `replace_content`, …),
  on the machine where the stack runs. A write opens the
  "editing" phase: `build_step` is required before answering. There is
  no commit and no push. `begin_plan`, `edit` and `apply` no longer exist.

The embedder and Qdrant stay inside the compose network and are not
published. The NVIDIA `llm` container stays on `127.0.0.1`. On `evo-x5`,
the host process listens on `0.0.0.0` so that the `agent` container can
call it; the editor still uses `127.0.0.1`.
