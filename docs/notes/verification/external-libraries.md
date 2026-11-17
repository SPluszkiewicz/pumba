# Roadmap, external libraries

Decision of 2026-11-06, recorded in
[ADR 0001. Serena as a sidecar](../../adr/0001-serena-sidecar.md). The client
is not the editor assembling MCP servers on the workstation. The product is the
`/ui` page (and the `app/` CLI): `agent` calls **sidecars** in
Compose. No Cursor. No bash. No commit.

The editor procedure (`.vscode/mcp.json` with Serena, Claude Context,
svn-mcp) is obsolete. See
[Migration to external MCP servers](../../guides/migration-mcp-externe.md).

## Status

Steps 1 to 4 below are done in the code: the `serena` sidecar
is in `compose.yaml`, the agent calls it
(`services/agent/serena_transport.py`), and the schema presented to the model no
longer contains `lsp_definition`, `lsp_references`, `lsp_hover`,
`lsp_symbols`, `lsp_outline`, `list_files`, `search`, `begin_plan`, `edit`
or `apply`. The `outline` MCP tool is removed.

**Not done yet:** the live test that was the condition for these
removals, `find_symbol`, `search_for_pattern` and `rename_symbol` succeeding
from `/ui` on a Git copy **and** on a Subversion copy, is the ticket
`.scratch/serena-sidecar/issues/08-live-gate-git-and-svn.md`
(`ready-for-human`). The removals were made before this test.

Also not done: the code behind the removed names (`tools` routes,
`patch` service, see [Files](#files)).

## Constraints

- Offline: a cloud-by-default candidate is not a replacement.
- No generic shell: a missing gesture becomes a named action. `execute_shell_command` and the Serena Python REPL stay **off** (`excluded_tools` in `config/serena/serena_config.yml`).
- Serena (GPL-3.0-or-later since v2) as a **separate process**, not `import serena`. Internal product, never distributed: the license would allow it; the sidecar is the "maintained elsewhere" mode. The image is pinned by tag (`ghcr.io/oraios/serena:2.1.0`): upgrading Serena is a tag change.
- `/ui` uses the same mechanism as the CLI. No two tool stacks.
- We wire up Serena's clangd + files + editing surface listed below (including `rename_symbol`). Not the JetBrains plugin, not the browser dashboard, not the on-disk memories.

## What gets replaced

| Here (removed from the schema) | Replaced by | Status |
|---|---|---|
| `lsp_definition`, `lsp_references`, `lsp_hover`, `lsp_symbols`, `lsp_outline`, `outline` MCP tool | Serena: `find_symbol`, `find_declaration`, `find_referencing_symbols`, `find_implementations`, `get_symbols_overview`, `get_diagnostics_for_file`, `get_diagnostics_for_symbol`, `restart_language_server` | Done. `lsp_calls` and `lsp_types` stay (below). The `/lsp/*` routes of `tools` still exist. `compile_commands` volume shared with `tools`: in place (`cmake-cache`, read-only on the Serena side). Live Git + Subversion test: to do. |
| `read_file`, `list_files`, `search` | `read_file` (now on Serena), `list_dir`, `find_file`, `search_for_pattern` | Done. The size cap and `path_safety` are in **agent** (`services/agent/serena_policy.py`). |
| `begin_plan` / `edit` / `apply` | Serena editing: `rename_symbol`, `replace_symbol_body`, `insert_before_symbol`, `insert_after_symbol`, `safe_delete_symbol`, `replace_content`, `replace_in_files`, `replace_lines`, `insert_at_line`, `delete_lines`, `create_text_file` | Done. Checkout **read-write** on Serena. A Serena write opens the `editing` phase → `build_step` required. Still no commit. The `patch` service still exists but no model name leads to it. |

`lsp_calls` and `lsp_types` stay in `tools` as long as Serena's clangd
backend does not expose the call / type hierarchy (only
JetBrains has it). The `calls` / `types` MCP tools stay with them.

## What does not get replaced

| Piece | Why |
|---|---|
| `llm`, `agent`, `GET /ui` | Local model and turn policy |
| `build`, `test`, `compile_file`, `build_step` | CMake/ctest in the `tools` image. No open-source product does it without a shell |
| `lint_clang_tidy`, `format_check`, `static_analysis` | apt binaries; the wrappers are the policy |
| Qdrant, indexer, embedder, reranker, `lookup` / `ask` | C++ tree-sitter chunking, checkout filter, working copy overlay, local rerank. Semble / cocoindex are more generic. Claude Context is cloud by default |
| `git_*` read | The maintained API is the `git` binary. `mcp-server-git` exposes `git_commit` |
| `svn_*` read | The maintained API is the `svn` binary. [svn-mcp](https://github.com/WillBrock/svn-mcp/) is not a maintained project |
| Gate, loop, cancel, budget, `path_safety` | Policy |

Qdrant, `llama.cpp`, tree-sitter, clangd, cmake, git, svn, FastMCP and
Serena are external projects. Changing their pins does not remove policy code
in `services/`.

## What is ruled out

| Candidate | License | Reason |
|---|---|---|
| Claude Context | MIT | OpenAI embeddings + Zilliz Cloud by default |
| mcp-server-git | MIT / Apache-2.0 | Commit / add / merge |
| mcp-server-filesystem | MIT / Apache-2.0 | Write by default; a demo, not a product |
| svn-mcp | MIT | Almost no activity |
| OpenHands, Aider, Continue | MIT / Apache-2.0 | Shell and/or cloud LLM; Continue is no longer maintained |
| Semble / cocoindex-code as the `/ui` index | MIT / Apache-2.0 | Acceptable offline, but it throws away the C++ indexer already there. Not in parallel with Qdrant |

Continue stays usable as a client of the local model
([VS Code and Continue.dev](../../guides/vscode.md)); it is not a dependency of the
product.

## Order

Condition set originally: nothing is removed until Serena has
answered **from `/ui`** on Git and on Subversion. The removals are done,
the live test is not yet (see the Status section above).

```text
1  [done]      Serena in Compose: checkout rw, CMake cache read-only,
               clangd+files+editing surface on;
               shell, REPL, JetBrains, dashboard, memories: off
2  [done]      agent MCP client; /ui exposes these tools;
               a Serena write opens the editing phase
   [to do]     live Git + Subversion test from /ui (ticket 08)
3  [done]      lsp_* covered and outline MCP tool removed from the schema
               (calls/types kept as long as Serena clangd lacks them)
4  [done]      read_file / list_files / search, then edit/apply
               removed from the schema (read_file goes through Serena)
5  [unchanged] git/svn wrappers, Qdrant, CMake tools
```

Step 1 does not modify the tool schema. Each schema removal (3, 4)
is a single change (`tool_models.py`, prompts, routes, tests,
`CONTRACTS.md`).

## Files

- Compose / deploy: `serena` service in `compose.yaml` (`tools-net` network, not published, checkout read-write, CMake cache volume read-only at `/workspace/scratch/cmake-cache`). `.git`, `.svn` and `.env` are ignored in the Serena config and refused by the agent before the call. `deploy.py workspace` also mounts the registered copies read-write on `serena` (`compose_mounts_text`). The sidecar nonetheless starts on a single project, `/workspace/app` (`CODEBASE_PATH`).
- `services/agent/`: MCP client (`serena_transport.py`), names (`serena_named_tools.py`), guard (`serena_policy.py`), `editing` phase (`edit_policy.py`); no Serena import.
- `services/mcp/server.py`: `outline` is gone; `lookup`, `check`, `calls`, `types` and `ask` remain.
- To do: `services/tools/handlers/lsp.py` and `clangd_rpc.py` now only serve `lsp_calls`, `lsp_types` and the `/lsp/*` routes still present; the `/search` and `/list-files` routes, the `/lsp/definition|references|hover|symbols` handlers and the `patch` service no longer have a model name leading to them.
- Tests `tests/mcp/`, `tests/tools/`, `tests/agent/`, `CONTRACTS.md`.
