# Using VS Code

What the two doors are for, in plain language:
[Comprendre sans bagage](../concepts/en-clair.md).

Two doors, one stack. Copilot writes when it calls MCP. The local GGUF writes
when a Continue-style client talks to `llama-server`. Do not chain them so
that each rewrites the other's answer.

Register the checkout first (`python scripts/deploy.py workspace <path>`).
That writes `X-Workspace` into `.vscode/mcp.json` and indexes the remote once.

## Door A — Copilot reads facts

`http://127.0.0.1:8092/mcp` after `python scripts/deploy.py up`.

| Tool | What you get |
| --- | --- |
| `lookup` | Index excerpt: path, lines, symbol. Dirty files from `svn`/`git status` come first, marked `[working copy]`. No chat model. |
| `check` | `build`, `test`, or `compile` log. No opinion. |
| `outline` | Symbols in one file (`name kind line`). |
| `calls` | Callers and callees (`name path:line`). |
| `types` | Base and derived types (`name path:line`). |
| `ask` | Short cited judgment (400 tokens). No build, no symbol list. Paste it unchanged. |

File text, text search, and git history come from the
[Filesystem](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem)
and [Git](https://github.com/modelcontextprotocol/servers/tree/main/src/git)
MCP servers. Subversion status comes from a read-only
[svn-mcp](https://github.com/WillBrock/svn-mcp/). This stack does not
reimplement those.

Jira and Confluence are a second MCP server,
[mcp-atlassian](https://github.com/sooperset/mcp-atlassian), started with
`uvx` on the machine that can reach the server. It is not inside compose
and it does not index tickets. Copy `atlassian.env.example` to
`atlassian.env`, fill `JIRA_URL`, `JIRA_PERSONAL_TOKEN`, and the two
Confluence lines if you use it. The editor entries are `.vscode/mcp.json`
and `.cursor/mcp.json` (`atlassian`). Leave a Confluence line empty to
skip that product. `READ_ONLY_MODE=true` keeps the four read tools
(`jira_search`, `jira_get_issue`, `confluence_search`, `confluence_get_page`).

Example prompt: ask Copilot to call `lookup` with the question unchanged,
then paste the tool text.

## Door B — the local model writes

`llama-server` is published on loopback only:

```
http://127.0.0.1:8080/v1
```

The model id is the GGUF file name without `.gguf`. With the default
`.env` that is `Qwen2.5-Coder-14B-Instruct-Q5_K_M`. The 0.5B file is only
for a connectivity check.

Continue (`~/.continue/config.json`), OpenAI-compatible provider:

```json
{
  "models": [
    {
      "title": "Local 14B",
      "provider": "openai",
      "model": "Qwen2.5-Coder-14B-Instruct-Q5_K_M",
      "apiBase": "http://127.0.0.1:8080/v1",
      "apiKey": "local"
    }
  ]
}
```

Any non-empty `apiKey` is enough. Embedder and Qdrant stay inside the
compose network. Do not publish them, and do not change the bind from
`127.0.0.1` to `0.0.0.0`.

Questions about the shape of the repository (architecture, overview,
how the tree is organized) also receive a ranked signature map in the
agent prompt. Fuzzy search stays in Qdrant.
