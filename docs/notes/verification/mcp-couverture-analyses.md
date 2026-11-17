# MCP coverage: analyses exposed to the model

> **Status**: plan partly outdated. Written on 2026-11-07, before the Serena migration ([ADR 0001](../../adr/0001-serena-sidecar.md)). The MCP no longer has `outline`, the `serena` sidecar is in Compose, and the `/ui` tool names have changed; the affected passages are dated below. The "improvable" leads remain open. · **Last verified**: 2026-11-12

**Verdict:** the MCP of this repository (`pumba`) is consistent with the tool list it publishes, and that list does not cover the matrix. It can be improved by relaying to handlers that already exist, not by rewriting it. The `/ui` loop already sees Git, clangd, and CMake; `ask` cuts that loop off.

Research note of 2026-11-07. The guides in `docs/` describe how to use the product. The code, Compose, and the tests own the behavior. When a guide and the code diverge, both are cited separately.

## Question

Is the MCP correct, and can it be improved? A rich MCP could expose to the model the diff, history, blame, branches, and commits; the AST, symbols, references, calls, and inheritance; CMake, compilation, dependencies, and flags; the affected tests, their results, and coverage; the Jira ticket, its acceptance criteria, and its history; the architecture, ADRs, and specifications; SonarQube, Jenkins logs, and benchmarks. Do we have these analyses in the MCP?

## Short answer

Correct for its narrow contract. On 2026-11-07: six tools (`lookup`, `check`, `outline`, `calls`, `types`, `ask`), the same ones in `CONTRACTS.md`, `docs/notes/contracts/mcp.md`, and `tests/mcp/test_server.py`. On 2026-11-12: `outline` was removed along with the home-made clangd tools (ticket 09 of the Serena sidecar) and `tests/mcp/test_server.py` expects five tools, `lookup`, `check`, `calls`, `types`, `ask`. Improvable: the matrix, for its part, is mostly absent from this server. What already exists (read-only Git, clangd references, file reading, CMake/ctest log) is a named tool of the `POST /chat` loop on `/ui`, or a log returned by `check`. `ask` sends `tools: false`, so the local model called by the MCP does not receive that schema. The tools specific to the Cursor editor (git, grep, read) are not this server. `.cursor/mcp.json` adds a second server, `atlassian`, which is not implemented in this repository.

The protocol only gives the model the tools the server lists. The MCP specification, Tools page (revision 2025-06-18), says that a client discovers tools through `tools/list`, then calls them through `tools/call`. Here that list is fixed by the test (as of 2026-11-12; the 2026-11-07 note also read `outline` there, between `check` and `calls`):

`tests/mcp/test_server.py`, `test_list_tools_exposes_facts_and_ask`:

> assert names == ["lookup", "check", "calls", "types", "ask"]

## Which MCP this is about

Three surfaces carry the name MCP. Only the first is the server of this repository.

| Surface | Where | What the model gets from it |
| --- | --- | --- |
| `pumba` server | `services/mcp/server.py`, published at `127.0.0.1:8092/mcp` | The five tools above. Facts via `agent`, except `ask`, which calls `POST /chat`. |
| `/ui` tool loop | `services/common/tool_models.py`, `TOOL_SPECS`; `services/agent/serena_named_tools.py`; `services/agent/tools_schema.py`; `services/agent/tool_loop.py` | On 2026-11-12, thirty-nine named tools: sixteen `TOOL_SPECS` (Git, SVN, `lsp_calls`, `lsp_types`, CMake, quality) and twenty-three Serena names (symbols, files, writes). The 2026-11-07 note counted twenty-seven, including the `begin_plan` / `edit` / `apply` editing. `docs/concepts/tool-calling.md`: "MCP `ask` does not receive this schema." |
| Editor config | `.cursor/mcp.json` | `pumba` (URL) and `atlassian` (`uvx mcp-atlassian`). `scripts/host/workspaces.py`, `install_vscode_mcp`, writes the same pair into `.vscode/mcp.json`, an unversioned file. |

`GLOSSARY.md` separates the first two: a **named tool** is "a bounded action with a name and checked arguments", and the word to avoid is "MCP tool (when you mean the agent schema)". A **sidecar** is not an "MCP server on the editor host".

`services/mcp/server.py`, `FastMCP` instructions:

> Tools for the checkout named by X-Workspace. lookup, check, calls, and types return facts and must be used for those jobs. ask only judges text already retrieved. It does not search, build, test, compile, or list symbols.

(Text as of 2026-11-12; on 2026-11-07 the list also named `outline`.)

`services/mcp/agent_client.py`, `ask_agent`, passes `tools=False`. `services/agent/main.py`, `ChatRequest.tools`, default `True`, description: "When false, the model answers from the excerpts only. MCP ask sets this." In `chat`, the `code` route does `enable_tools = payload.tools` after the index search (`retrieve_context`). The `direct` route forces `enable_tools = False` and does not search.

The `mcp` container is on `client-net` and `publish-net` (`compose.yaml`, service `mcp`). `tools` is only on `tools-net`. `agent` is on both. A new MCP tool therefore goes through an `agent` route, like `check`, `calls`, and `types`, not through a direct call to `tools`.

## Matrix

`exposed` = a tool of the `pumba` server returns this information. `partial` = another channel of this stack (the `/ui` loop, the index, or the editor's `atlassian` server), not `pumba`. `absent` = none of these channels. `planned` = written down as a follow-up, not in the code.

| Domain | Information | Status | Where (tool / file) | Limit |
| --- | --- | --- | --- | --- |
| Git | Diff | partial | `/ui` loop: `git_diff` → `POST /git/diff`, `services/tools/handlers/git.py` `run_git_diff`. Not an MCP tool. | Two refs required (`GitDiffArgs.ref_a`, `ref_b`). Not the uncommitted diff. 200 lines (`MAX_GIT_LINES`). `sample/` has no `.git` (comment on the `tools` volume in `compose.yaml`). |
| Git | History | partial | `/ui` loop: `git_log` → `run_git_log`. Format `%h %ad %an %s`. | 20 commits by default, 50 at most (`GitLogArgs.max_count`). Commit subject, not the patch. |
| Git | Blame | partial | `/ui` loop: `git_blame` → `run_git_blame` (`git blame --date=short`, optional `-L` window). | One file. 200 lines. Absent from `pumba`. |
| Git | Branches | absent | No `git branch` in `services/tools/handlers/git.py`. | A `git_diff` / `git_show` ref can be a branch name (`_REF`), if the caller already knows it. Nothing lists the branches. |
| Git | Commits | partial | `/ui` loop: `git_log` (short list) and `git_show` → `run_git_show`. | Without a path, `git show --stat`. With a path, the file at that ref. 200 lines. `git_diff_names` serves `POST /review`, "not exposed to the LLM" (`run_git_diff_names`). |
| Code | AST | absent | `tree-sitter` splits the index (`services/indexer/chunker.py`) and, if the package is present, the `repo_map` sketch (`services/agent/repo_map.py`). | The model receives a text excerpt or a signature, never the tree. |
| Code | Symbols | partial | On 2026-11-12, `/ui` loop: `get_symbols_overview` and `find_symbol` (Serena). The MCP `outline` is removed (ticket 09); `POST /lsp/outline` remains a route of `agent` and `tools` (`run_outline`, `textDocument/documentSymbol`) that the model cannot name. `lookup` adds `symbol_name` and an index excerpt. | On 2026-11-07: `outline` was exposed, one file, 40 symbols (`MAX_LOCATIONS`). Today `pumba` has no symbols tool; the agent refuses a `max_answer_chars` above 262144 (`serena_policy.py`). `lsp_symbols` is no longer a named tool. |
| Code | References | partial | `/ui` loop: `find_referencing_symbols` (Serena); on 2026-11-07 it was `lsp_references` → `run_references`, removed from the schema with ticket 09. | `pumba` does not have this tool. `calls` is the call hierarchy, not references. |
| Code | Calls | exposed | MCP `calls` → `POST /lsp/calls` → `run_calls` (`prepareCallHierarchy`, incoming / outgoing). Line `name path:line`. | 40. `direction`: `in`, `out`, `both`. The callees can be empty: clangd `outgoingCalls` returning `-32601` is swallowed (`lsp.py`, `_hierarchy`). No preset on the MCP side. |
| Code | Inheritance | exposed | MCP `types` → `POST /lsp/types` → `run_types` (`prepareTypeHierarchy`, supertypes / subtypes). | `direction`: `super`, `sub`, `both` (mapped to `in` / `out` / `both`). 40. One 1-based position. No preset on the MCP side. |
| Build | CMake | partial | MCP `check` with `action=build` → `POST /check` → `POST /build` → `run_cmake_pipeline`. The text is the log, `format_check`: `{action} exit {code}` plus stdout/stderr. | `release` and `debug` presets only (`_PRESET_CACHE_VARIABLES`). Log cut at 4000 characters (`_LOG_LIMIT`). Fresh scratch directory. No target graph. |
| Build | Compilation | exposed | MCP `check` with `action=compile` and `path`. `compile` without a path: HTTP 400 (`check` in `services/agent/main.py`). | One file. Log of 4000 characters. `lint_clang_tidy`, `format_check`, `static_analysis` (cppcheck) stay on `/ui`. |
| Build | Dependencies | absent | No handler returns `find_package`, target links, or `compile_commands.json` as data. | The `build` log may mention them by chance. That is not an analysis. |
| Build | Flags | absent | `configure_argv` sets `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` and `CMAKE_BUILD_TYPE` according to the preset. | These flags are not a tool. The model cannot query them. |
| Tests | Affected tests | absent | The `filter` of `check` / `test` is a `-R` supplied by the caller (`validate_filter`, `ctest -R`). | No selection of the tests touched by a diff. |
| Tests | Results | exposed | MCP `check` with `action=test` → `POST /test` → cmake then `ctest --output-on-failure --no-tests=error`. | Log of 4000 characters, not a history of test runs. Fresh scratch: everything is rebuilt. Optional `filter`. |
| Tests | Coverage | absent | No occurrence of `gcov`, `lcov`, `coverage.xml`, or `cobertura` in the repository. | none |
| Jira | Ticket | partial | Not `pumba`. `.cursor/mcp.json` and `ATLASSIAN_MCP_SERVER` launch `uvx mcp-atlassian`. `atlassian.env.example`: `ENABLED_TOOLS=jira_search,jira_get_issue,confluence_search,confluence_get_page`, `READ_ONLY_MODE=true`. | Secrets empty in the example; `atlassian.env` is not versioned. Tickets are not indexed (comment in the file). Exact body of `jira_get_issue`: not described here. |
| Jira | Acceptance criteria | absent | Neither this repository nor the mcp-atlassian Tools Reference page (consulted) names this field. | Uncertain: a ticket's details might contain them in the description. That is not established. |
| Jira | History | absent | `jira_batch_get_changelogs` exists in mcp-atlassian (toolset `jira_issues`) and is not in `ENABLED_TOOLS`. | Neither is `confluence_get_page_history`. |
| Documentation | Architecture | partial | `lookup` queries the index. The `.md` files are split by heading (`services/indexer/text_chunker.py`, language `markdown`, `symbol_name` = heading). `ask` can prefix a sketch if the question contains a hint from `repo_map._HINTS` ("architecture", "overview of", …). | The default index is `sample/` (`CODEBASE_PATH`). `docs/architecture/` of this repository is not in it, unless the indexed checkout contains it. The sketch is C++ signatures, "not a proof of behavior". |
| Documentation | ADR | partial | Same channel: an indexed Markdown file, including the only ADR of the repository, `docs/adr/0001-serena-sidecar.md`. | No "ADR" tool. `.llm` is ignored by the indexer (`SKIPPED_DIR_NAMES`). |
| Documentation | Specifications | partial | Same channel for an indexed `.md` or text (`CONTRACTS.md` only if it is in the indexed tree). | No specification tool. The indexer skips `.git`, `build`, `CMakeFiles`. |
| CI | SonarQube | absent | No occurrence of `sonar` in the repository. | none |
| CI | Jenkins logs | absent | No occurrence of `jenkins`. | The workflow present is `.github/workflows/`, not exposed to the model. |
| CI | Benchmarks | absent | The word appears in guides (GPU, model comparison), not as a CI result nor as a tool. | none |

### What `pumba` actually returns

`docs/notes/contracts/mcp.md`, table "What it exposes", and `CONTRACTS.md`, section "`mcp`: Streamable HTTP facts plus a short `ask`", named the same six tools on 2026-11-07. On 2026-11-12 these two texts still cite `outline` although `server.py` no longer publishes it; the code prevails. The code:

- `lookup` (`server.py`) → `lookup_agent` → `POST /lookup`. `format_lookup`: `path:start-end (symbol)`, excerpt, and `[working copy]` if `source == working_copy`. `CONTRACTS.md`: at most 6 dirty C/C++ files, 80 lines, re-read via `/read-file` after `svn status` or `git status`. This is not a diff. `include_candidates` stays false: the MCP does not send the flag (`lookup_agent` only sets `query` and `workspace`).
- `check` → `check_agent` → `POST /check`. `action` ∈ `build`, `test`, `compile`. `format_check` concatenates the exit code and the logs, plus `(truncated)` if the flag is true.
- `calls`, `types` → `lsp_fact_agent` to `/lsp/calls`, `/lsp/types` (on 2026-11-07, `outline` to `/lsp/outline` too; `lsp_fact_agent` still accepts that route). `format_fact` concatenates `stdout` / `stderr`. `lsp_fact_agent` accepts `preset`; the MCP tools do not expose it. The effective preset is `TOOLS_CMAKE_PRESET` (default `release`, `services/tools/main.py`).
- `ask` → `ask_agent` with `max_reply_tokens=400`, `tools=False`, and the `_ASK_NOTE` note (12 lines, cite `path:line`). `format_chat_result`: the answer, then `Sources:`, without `conversation_id` (test `test_format_chat_result_includes_sources_and_id`).

`read_agent` and `format_read` are written in `agent_client.py` and `format_read` is tested. No `@mcp.tool` calls them. `docs/notes/contracts/mcp.md`: "Reading a file, searching text, and SVN status are not tools here."

### Serena: planned on 2026-11-07, delivered since

`docs/adr/0001-serena-sidecar.md` is in the present tense:

> The agent talks to Serena over MCP inside Compose. Serena owns clangd, file read/search, and symbol-level edits on a read-write checkout.

On 2026-11-07, no occurrence of `serena` in `compose.yaml`, `services/`, or `tests/`. `docs/notes/verification/external-libraries.md` (2026-11-06) made it step 1 of an order not yet started: "Nothing is removed until Serena has answered from `/ui`" (translated from French). Step 3 removed `outline` from the MCP and kept `calls` / `types` "as long as Serena's clangd backend does not expose call / type hierarchy" (translated from French). `docs/guides/migration-mcp-externe.md` opens with "Obsolete as a product plan (2026-11-06)" (translated from French): the Serena / filesystem / Git / svn-mcp assembly on the workstation does not run.

On 2026-11-12 the sidecar is delivered: `serena` service in `compose.yaml` (image `ghcr.io/oraios/serena:2.1.0`, network `tools-net`, not published), tool names in `services/agent/serena_named_tools.py`, transport in `services/agent/serena_transport.py`, path and size guardrails in `services/agent/serena_policy.py`. The three families of removals are done (tickets 09 to 11 of `.scratch/serena-sidecar/`): the `lsp_definition` / `lsp_references` / `lsp_hover` / `lsp_symbols` / `lsp_outline` tools and the MCP `outline`; `list_files` and `search`; `begin_plan` / `edit` / `apply`. `calls` and `types` remain. The live proof on a Git repository and a Subversion repository (ticket 08) is not done: the removals were decided on a Serena answer from `sample/`.

## Correct against the published contract

The tool list, the token, the transport, and the bind agree.

- Names and HTTP targets: `CONTRACTS.md` (tools `lookup`, `check`, `outline`, `calls`, `types`, `ask`) = decorators of `server.py` = assertion of `test_list_tools_exposes_facts_and_ask`. Each description contains `unchanged`.
- `ask`: `tools: false`, 400 tokens, no conversation id in the text. `agent_client.py` raises `AgentError` on 401, 503, and timeout; `server.py` `_tool_error` turns it into a `ToolError`. `CONTRACTS.md`: "agent `401` / `503` / timeout → MCP tool error (`isError`), not a process crash." The test `test_ask_tool_surfaces_agent_error` covers the 503.
- Bind: `compose.yaml` publishes `127.0.0.1:${MCP_PORT:-8092}`. `CONTRACTS.md` forbids `0.0.0.0`. The process listens on `0.0.0.0` inside the container (`FastMCP`, `host="0.0.0.0"`); the `ports:` line is what reaches the host.
- Health: `GET /health` → `{"status":"ok"}`, outside the MCP protocol.
- Pin: `services/mcp/requirements.txt`, `mcp==1.12.4`, like `CONTRACTS.md`.

The gaps are concrete (as of 2026-11-07; gaps 1 and 4 are restated up to date).

1. **Where Git, reading, and SVN live.** `CONTRACTS.md`, opening of the MCP section: "File read, ripgrep, and SVN stay on the official Filesystem / Git servers and a read-only svn-mcp." `docs/notes/contracts/mcp.md` and `docs/guides/vscode.md` point to the same repositories. The versioned JSON and `install_vscode_mcp` only register `pumba` and `atlassian`. The decision of 2026-11-06 (`roadmap-libs-externes.md`) rules out mcp-server-git, mcp-server-filesystem, and svn-mcp. On 2026-11-12, Git and SVN are named tools of `/ui` (`TOOL_SPECS`); reading and text search are the Serena names `read_file`, `list_dir`, `find_file`, and `search_for_pattern`. None of them is visible to a client that only has `pumba`. `CONTRACTS.md` (opening of the MCP section), `docs/notes/contracts/mcp.md`, and `docs/guides/vscode.md` still point to the Filesystem / Git servers and svn-mcp, which the decision of 2026-11-06 rules out.

2. **`ask` searches anyway.** The server instructions say that `ask` "only judges text already retrieved" and "does not search". Outside the `direct` route, `chat` calls `retrieve_context` (`services/agent/rag.py`) then, if the question matches `_HINTS`, `repo_map_block`, before `enable_tools = payload.tools`. `tools: false` removes the tool schema. It does not remove the index or the sketch. The `ask` model therefore cites excerpts that this call itself retrieved, not only an earlier paste from `lookup`.

3. **Silent callees.** `calls` "List callers and callees" (`server.py`). `lsp.py`, `_hierarchy`: if `callHierarchy/outgoingCalls` raises `-32601` or "method not found", the result becomes an empty list and `ok` stays true. A `direction=out` can therefore be an empty text without saying that the method is missing. The code comment attributes this to clangd.

4. **ADR in the present tense, sidecar absent (resolved).** On 2026-11-07, `docs/adr/0001-serena-sidecar.md` described an agent that already talked to Serena while the service was not in Compose. The `serena` service has been there since (see above).

5. **Checkout header missing from the versioned Cursor JSON.** `docs/notes/contracts/mcp.md` shows `X-Workspace` on `pumba`. `.cursor/mcp.json` only has `"url": "http://127.0.0.1:8092/mcp"`. The Compose `mcp` service does not set `MCP_WORKSPACE`. Without the header, `_workspace()` is empty and `agent` takes the default tree (`CODEBASE_PATH`, usually `sample/`). `install_vscode_mcp` writes the header into `.vscode/mcp.json` only, and that file is not in the repository.

`read_agent` without a tool is not a broken contract: the MCP page says that reading is not a tool here. It is an unused client.

## What can be improved

Order: what reuses a handler already there, at the branching point already used by `check` and `outline`. Not a rewrite. (The mention of Serena in the 2026-11-07 note is outdated: `outline` is already removed.)

The `/ui` loop already has the diff, history, blame, commits, references (`find_referencing_symbols`), declaration (`find_declaration`), reading (`read_file`), clang-tidy, and cppcheck. Adding them to the MCP only helps the `/ui` model if we talk to it again via `pumba`. It does help an MCP client (Cursor, Claude Code, OpenCode) whose only server from this repository is `pumba`, and to whom `ask` does not give `TOOL_SPECS`.

1. **Reading a file.** `read_agent` and `format_read` are already in `services/mcp/agent_client.py`. `POST /read` is already in `services/agent/main.py`. The wiring is one `@mcp.tool()` in `services/mcp/server.py`, then the sentence in `docs/notes/contracts/mcp.md` and `CONTRACTS.md` that points to the Filesystem server. `lookup` only returns an index excerpt, or 80 lines of a dirty file.

2. **Diff, history, blame, commits.** The handlers are `run_git_diff`, `run_git_log`, `run_git_blame`, `run_git_show` in `services/tools/handlers/git.py`. There is no route on the `agent` side for these handlers (only `/lookup`, `/read`, `/check`, `/lsp/outline`, `/lsp/calls`, `/lsp/types`). The model of `/check` in `services/agent/main.py` is the relay: a route that calls `_post_tools("/git/...")`, a function next to `check_agent`, a tool in `server.py`. `mcp` does not join `tools-net`. The uncommitted diff would remain a hole: `GitDiffArgs` requires two refs, and `run_git_status` is "not an LLM tool".

3. **References.** `run_references` and `run_definition` are still in `services/tools/handlers/lsp.py` and `tools` keeps the `/lsp/references` and `/lsp/definition` routes, but they are no longer `ToolSpec`s (removed in ticket 09) and `agent` has no route for them. The `/ui` model gets them through Serena (`find_referencing_symbols`, `find_declaration`), not through these handlers. An MCP relay would therefore need an `agent` route to these `tools` routes that still exist. `calls` / `types` show the pattern already carried through to the MCP.

4. **Saying that callees are missing.** In `services/tools/handlers/lsp.py`, `_hierarchy`, the `-32601` of `outgoingCalls` currently becomes `found = []`. A `stderr` line instead keeps `calls` honest, for the MCP and for `/ui`, without a new tool.

5. **Listing branches.** The only Git facet also absent from `/ui`. The smallest addition is a function next to `run_git_log` in `services/tools/handlers/git.py` (`git branch`, argv, same 200-line cap), a `ToolSpec` in `services/common/tool_models.py`, then the same MCP relay if the client of `pumba` must see it. `prompts.py` must name the tool: `tests/agent/test_prompts.py` requires it for every name in `TOOL_ENDPOINT_MAP`.

Lower priority, because there is no data to relay:

- **Dependencies and flags.** A new handler would be needed. The only fact already computed is the `_PRESET_CACHE_VARIABLES` list and the argv of `configure_argv` (`services/tools/handlers/build.py`): two presets, `CMAKE_BUILD_TYPE`, `CMAKE_EXPORT_COMPILE_COMMANDS`. This is not a dependency graph.
- **Affected tests and coverage.** `ctest -R` exists. Nothing computes the impact of a diff or a coverage. No binary planned in `REQUIRED_BINARIES`.
- **Jira.** The lever is not `server.py`. Ticket history, in mcp-atlassian, is `jira_batch_get_changelogs`, absent from `ENABLED_TOOLS` in `atlassian.env.example`. Acceptance criteria are not a named tool in the documentation consulted. `pumba` does not talk to Jira (`docs/notes/contracts/mcp.md`).
- **Architecture, ADRs, specifications.** `lookup` already returns the Markdown sections of the indexed checkout. There is no tool to add to "see an ADR": these files must be in the tree of `CODEBASE_PATH` or of a registered checkout. The default index is `sample/`.
- **SonarQube, Jenkins, benchmarks.** No log, no URL, no handler. An MCP tool would have nothing to call in this stack.

## Sources

Repository:

- `services/mcp/server.py` (`lookup`, `check`, `calls`, `types`, `ask`, `_ASK_NOTE`)
- `services/mcp/main.py` (`WorkspaceHeader`, `MCP_TRANSPORT`)
- `services/mcp/agent_client.py` (`ask_agent`, `lookup_agent`, `read_agent`, `check_agent`, `lsp_fact_agent`, `format_lookup`, `format_check`, `format_fact`, `format_chat_result`, `format_read`)
- `services/mcp/requirements.txt` (`mcp==1.12.4`)
- `services/common/product.py` (`MCP_SERVER = "pumba"`)
- `services/common/tool_models.py` (`TOOL_SPECS`, `GitLogArgs`, `GitBlameArgs`, `GitDiffArgs`, `GitShowArgs`)
- `services/agent/main.py` (`ChatRequest.tools`, `check`, `lsp_outline`, `lsp_calls`, `lsp_types`, `_bounded_tool`, `_LOG_LIMIT`)
- `services/agent/prompts.py` (tool list, `repo_map_block`)
- `services/agent/serena_named_tools.py`, `services/agent/serena_policy.py`, `services/agent/serena_transport.py`, `services/agent/tools_schema.py`
- `.scratch/serena-sidecar/issues/` (tickets 08 to 11)
- `services/agent/repo_map.py` (`wants_repo_map`, `_HINTS`)
- `services/agent/rag.py` (`retrieve_context`)
- `services/agent/tool_loop.py` (`enable_tools`)
- `services/tools/handlers/git.py` (`run_git_log`, `run_git_blame`, `run_git_diff`, `run_git_show`, `run_git_status`, `run_git_diff_names`, `MAX_GIT_LINES`)
- `services/tools/handlers/lsp.py` (`run_outline`, `run_calls`, `run_types`, `run_references`, `run_definition`, `_hierarchy`)
- `services/tools/handlers/clangd_rpc.py` (`format_document_symbols`, `format_related_symbols`, `MAX_LOCATIONS`)
- `services/tools/handlers/build.py` (`_PRESET_CACHE_VARIABLES`, `configure_argv`)
- `services/tools/handlers/test.py` (`_ctest_argv`, `validate_filter`)
- `services/tools/main.py` (`DEFAULT_PRESET`, `REQUIRED_BINARIES`)
- `services/indexer/chunker.py` (`SKIPPED_DIR_NAMES`, tree-sitter)
- `services/indexer/text_chunker.py` (Markdown, CMake)
- `tests/mcp/test_server.py`, `tests/mcp/test_agent_client.py`
- `CONTRACTS.md` (MCP section, done routes, `/git/*`, `/lsp/*`)
- `docs/notes/contracts/mcp.md`, `docs/notes/contracts/tools-api.md`, `docs/concepts/tool-calling.md`, `docs/concepts/indexing-pipeline.md`
- `docs/architecture/agent-loop.md`
- `docs/adr/0001-serena-sidecar.md`
- `docs/notes/verification/external-libraries.md`, `docs/guides/migration-mcp-externe.md`, `docs/guides/adding-a-new-tool.md`, `docs/guides/vscode.md`
- `GLOSSARY.md` (Named tool, Sidecar)
- `compose.yaml` (services `mcp`, `agent`, `tools`, `serena`)
- `.cursor/mcp.json`
- `scripts/host/workspaces.py` (`ATLASSIAN_MCP_SERVER`, `install_vscode_mcp`)
- `atlassian.env.example`

Outside the repository, pages read for this note:

- MCP specification, Tools, revision 2025-06-18, <https://modelcontextprotocol.io/specification/2025-06-18/server/tools>. A server exposes tools that the client discovers through `tools/list` and invokes through `tools/call`. Each tool has a `name`, a `description`, and an `inputSchema`.
- mcp-atlassian README, <https://github.com/sooperset/mcp-atlassian> (raw `main`). `jira_get_issue`: "Get issue details". `jira_search`: "Search with JQL". The page says "Not an official Atlassian product."
- mcp-atlassian Tools Reference, <https://mcp-atlassian.soomiles.com/docs/tools-reference>. `jira_batch_get_changelogs` is in the `jira_issues` toolset. `ENABLED_TOOLS` restricts the list. `READ_ONLY_MODE=true` only lets reads through. The page does not name acceptance criteria.

Search in the repository: `sonar`, `jenkins`, `gcov`, `lcov`, `coverage.xml`, `cobertura`: no hits (re-read on 2026-11-12, excluding `docs/research/`). `serena` on 2026-11-07: only `docs/notes/verification/external-libraries.md`, `docs/guides/migration-mcp-externe.md`, and `docs/adr/0001-serena-sidecar.md`; since then, also `compose.yaml`, `config/serena/`, `services/agent/`, `tests/`, and `CONTRACTS.md`.
