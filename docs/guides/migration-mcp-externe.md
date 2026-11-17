# Migration to external MCP servers (obsolete)

> **Plan superseded (2026-11-06). Do not execute it.** The client is not
> the editor assembling MCP servers on the workstation. The product is the
> `/ui` page (and the `llm-client` CLI): Serena runs as a Compose **sidecar**
> behind the agent, git and svn stay read-only named tools,
> Claude Context, svn-mcp and the Filesystem and Git MCP servers are
> ruled out. The decision is in
> [ADR 0001. Serena as a sidecar](../adr/0001-serena-sidecar.md). The
> choice of libraries is in
> [Roadmap, external libraries](../notes/verification/external-libraries.md). This page
> only keeps the history of the abandoned track and the mapping
> to the actual state.

## The abandoned track

The plan proposed that the client (Cursor, VS Code, Claude Code) assemble
several MCP servers launched on the workstation, in the copy's folder:
Serena, Filesystem, Git, svn-mcp and Claude Context, written to
`.vscode/mcp.json` by `deploy.py workspace`. The `pumba` MCP would no longer
have kept anything but `check` and `ask`.

It contradicts the decision: the product is `/ui`, not an editor; an
MCP server declared in the editor configuration does not serve the page.
`deploy.py workspace` therefore only writes `pumba` and `atlassian` to
`.vscode/mcp.json` (`install_vscode_mcp` in `scripts/host/workspaces.py`).

## What each role became

| Role | Abandoned track | Actual state |
|---|---|---|
| Symbols, definitions, references, outline of a file | Serena on the workstation | Serena as a sidecar, named in the model schema: `find_symbol`, `find_declaration`, `find_referencing_symbols`, `find_implementations`, `get_symbols_overview`, `get_diagnostics_for_file`, `get_diagnostics_for_symbol`, `restart_language_server`. `lsp_definition`, `lsp_references`, `lsp_hover`, `lsp_symbols`, `lsp_outline` and the `outline` MCP tool are no longer exposed. |
| Callers, callees, base and derived types | Serena | Unchanged: `lsp_calls` and `lsp_types` (clangd in `tools`), and the `calls` / `types` MCP tools. |
| Read, list, search | Filesystem | Serena: `read_file`, `list_dir`, `find_file`, `search_for_pattern`. `list_files` and `search` are no longer exposed. |
| Write | (editor) | Serena: `rename_symbol`, `replace_symbol_body`, `insert_before_symbol`, `insert_after_symbol`, `safe_delete_symbol`, `replace_content`, `replace_in_files`, `replace_lines`, `insert_at_line`, `delete_lines`, `create_text_file`. A write opens the "editing" phase: `build_step` required. `begin_plan`, `edit` and `apply` are no longer exposed. Never a commit. |
| Git / Subversion history | Git MCP server, svn-mcp | Unchanged: `git_log`, `git_blame`, `git_diff`, `git_show`, `svn_status`, `svn_diff`, `svn_log`, read-only. |
| Search by meaning | Claude Context (Milvus) | Unchanged: Qdrant index, `lookup` and the agent's context. A single index. |
| Build, tests, compilation | `pumba` `check` | Unchanged: `build`, `test`, `compile_file`, `build_step` in `tools`; the `check` and `ask` MCP tools stay. |

The `pumba` MCP therefore exposes `lookup`, `check`, `calls`, `types` and `ask`
(`services/mcp/server.py`). It only loses `outline`.

## What stayed

`/read-file` and `/lsp/outline` stay on `tools`: the agent uses them for
`POST /read`, the working-copy overlay, and `POST /lsp/outline`.
`/lsp/calls` and `/lsp/types` stay because Serena does not expose call or
type hierarchy. `/search`, `/list-files`, `/lsp/definition`,
`/lsp/references`, `/lsp/hover`, and `/lsp/symbols` are gone, and so is
the `patch` service.

The live check that Serena answers on a real Git checkout and a real
Subversion checkout is still
`.scratch/serena-sidecar/issues/08-live-gate-git-and-svn.md`, status
`ready-for-human`.
