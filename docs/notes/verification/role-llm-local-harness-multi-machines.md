# Local LLM, harness, multiple machines

> **Status**: informational, partial state (one stack per machine; no accounts, isolation or quotas). Written on 2026-11-07 (file creation date); the tool count and the write routing are updated for the `serena` sidecar. · **Last verified**: 2026-11-12

**Verdict: partial.** The stack serves a chat model on the machine that hosts it, and the agent is a harness (bounded loop, named tools, code context, guardrails). Each host that runs `python scripts/deploy.py up` has its own stack. A stack reached from other workstations remains a loopback access, an SSH tunnel or an address relay; there are no accounts, isolation or quotas.

Research note. The detail of the relay and of the doors is already in `docs/notes/verification/pumba-serveur-multi-clients.md`; the network facts repeated here are re-read in Compose and the scripts, not in that note.

## Question

This repository must serve a local LLM (inference on the stack's machine, not a cloud API), provide a harness (agent loop, tools, code context, guardrails), and be usable from several machines. "Several machines" splits into three cases: one stack per machine, one shared stack reached from several workstations, several users at the same time (accounts, isolation, quotas, working copies).

## Short answer

| Need | Verdict | Where it stops |
| --- | --- | --- |
| Local LLM | Delivered | `llama-server` and GGUFs on the stack's host. No cloud provider in the default path. The one-time weights download goes out over the Internet. |
| Harness | Delivered | `POST /chat` chains gate, index, loop and 39 named tools (16 `TOOL_SPECS` plus 23 Serena names). MCP `ask` does not receive this schema. No shell. |
| One stack per machine | Delivered | `deploy.py up` starts the stack on the machine that runs the command. Nothing spreads it over a cluster. |
| One stack, several workstations | Partial | The Compose doors are `127.0.0.1`. An SSH tunnel is documented. `expose` relays to a list of IPv4 addresses. The code queried must already be a checkout registered on the stack's machine. |
| Several simultaneous users | Absent | One token, at most two `/chat`, one `llama-server` slot, in-memory history with no user key. No per-person account, quota or workspace. |

## Local LLM

Chat inference is a `llama-server` process on the stack's machine. The HTTP client is the OpenAI SDK, pointed at that URL, with a dummy key.

| Piece | What the repository does |
| --- | --- |
| Binary | `containers/llm/entrypoint.sh` does `exec llama-server` (`--host 0.0.0.0`, `--port`, `--jinja`, `-m` or the `--models-dir` router). `CONTRACTS.md`, "Pinned versions", pins llama.cpp to tag `b10216`. |
| Weights | `CONTRACTS.md`, "Models": chat `Qwen2.5-Coder-14B-Instruct-Q5_K_M.gguf` under `./models/`, read-only mount (`compose.yaml`, service `llm`, `./models:/models:ro`). `models/README.md` says not to commit the weights. |
| Agent client | `services/agent/llm_client.py`, `sdk_client`: `AsyncOpenAI(base_url=…/v1, api_key="local")`. The comment says `llama-server` ignores the key. `LLM_BASE_URL` defaults to `http://llm:8080` (`llm_client.py` and `CONTRACTS.md`, "Environment variables"). `chat_completion` posts to `/v1/chat/completions`. |
| Embeddings and rerank | Same local family. `CONTRACTS.md`: nomic embedder on 8081, bge reranker on 8082, not published. `docs/architecture/overview.md`, `embedder` and `reranker` paragraphs. |
| Other GPU machine | `config/profiles/evo-x5.env` sets `LLM_BASE_URL=http://host.containers.internal:8080`. `config/compose/host-llm.yaml` does not start the `llm` container. `scripts/host/evo_x5_serve.py`, `serve_argv`, launches the host binary with `--host 0.0.0.0`. `scripts/host/deploy/stack.py`, `cmd_up`, routes `LLM_SERVE=host` to `_start_host_llm` and removes `llm` from the health wait. |

`README.md`, first sentence, says "fully containerized, offline". Two gaps, set apart:

- The `evo-x5` profile does not put the chat in a container. `docs/architecture/overview.md`, "Each service", and `docs/guides/swapping-the-model.md`, "Cases", say so: `up` launches `llama-server` on the host. The README, "Quick start", does not name this case.
- Offline applies to inference. `CONTRACTS.md`, "Networks", reserves Internet access for the `fetch-models` job (`setup` profile). `scripts/host/deploy/stack.py`, `_fetch_weights` then `cmd_up`, launches this job unless `--skip-models`. `scripts/container/fetch_models.py` is the downloader named by `models/README.md`.

No module under `services/` calls a cloud API. A search for `api.openai`, `anthropic`, `openrouter` and a real API key finds only the local SDK (`services/agent/llm_client.py`, `api_key="local"`) and the docs that describe this client. `docs/notes/verification/external-libraries.md` rejects a "cloud-by-default" candidate as a replacement; that is not a code path.

Changing `LLM_BASE_URL` does not give an outbound route. `compose.yaml`, networks: `client-net`, `core-net` and `tools-net` are `internal: true`. `publish-net` is not internal, to publish the doors, and the overlay closes egress: `config/compose/podman.yaml` (`no_default_route: "true"`), `config/compose/docker.yaml` (masquerade set to `false`). `config/compose/host-llm.yaml` adds `agent` to `publish-net` to reach the host `llama-server`, and the comment says this network does not open the Internet.

Port 8080 published by Compose is `127.0.0.1` (`compose.yaml`, service `llm`). The `0.0.0.0` listen address of the entrypoint is that of the container. On `evo-x5`, `serve_argv` listens on `0.0.0.0` on the host: the model stays local, but the socket is no longer limited to the loopback. `docs/architecture/security.md`, "Layer 5", says the same thing.

`LLM_PARALLEL=1` (`CONTRACTS.md`; `entrypoint.sh`, `--parallel`) loads only one generation slot. `LLM_MODELS_MAX=1` (`compose.yaml`) keeps only one chat GGUF loaded. The router (`LLM_ROUTER=1`, `POST /models/load`) switches local files; `docs/guides/swapping-the-model.md`, "Changing the chat model", does not mention a remote endpoint.

## Harness

The harness is the `agent` service. `GLOSSARY.md`: the agent classifies the question, retrieves, and runs the named-tool loop; the model has no shell. `README.md` places around it the Qdrant index, `tools`, the `serena` sidecar and the MCP front.

### Loop

`services/agent/main.py`, `execute_turn`:

1. `decide` (`services/agent/gate.py`) chooses `direct` or `code`. Arithmetic and greetings are `direct`. A file, a symbol, a build or CMake is `code`. If the rules do not settle it, a first message is a short model call when `AGENT_GATE_CLASSIFY=1`; a failure stays on `code`.
2. `direct` does not call the index and sets `enable_tools = False`.
3. `code` calls `retrieve_context` (`services/agent/rag.py`), then, if a checkout is linked, `overlay_chunks` on the dirty files. The message may receive the signature map (`repo_map_block`) and the rules (`get_system_prompt`, `*.md` files from `<checkout>/.llm/rules`, `services/agent/rules_loader.py`).
4. `run_tool_loop` (`services/agent/tool_loop.py`) sends the turn to `chat_completion` or `chat_completion_stream`, with the tool schema if `payload.tools` is true (`ChatRequest.tools`, default `True`).

`docs/architecture/agent-loop.md` sets the policy: no shell, no commit, no push, no sub-agent. A prose reply stops the loop only if no failure is open and if a write has been followed by a successful `build_step`. Otherwise a system instruction restarts it, at most `AGENT_LOOP_NUDGES` times (3 in `compose.yaml`). The 4th identical batch and a repeated malformed batch force a completion without tools.

`CONTRACTS.md`, "agent: POST /chat", delivers 48 iterations (`AGENT_TOOL_LOOP_MAX_ITERATIONS`). `compose.yaml` passes the variable through with no default in the file. `tool_loop.py`, `MAX_ITERATIONS_DEFAULT`, falls back to `8` if the variable is absent. `docs/concepts/tool-calling.md`, "Why bound the loop", cites both.

The context is counted before each call (`services/agent/token_budget.py`, `apply_budget`, cap `AGENT_CONTEXT_BUDGET_TOKENS`, 28544 in `compose.yaml`) via `POST /tokenize` on the local `llama-server` (`llm_client.py`, `count_tokens`). The history is in memory (`services/agent/conversations.py`, docstring "No disk"). Compose sets a TTL of 28800 s and 24 turns (`AGENT_CONVERSATION_TTL_SECONDS`, `AGENT_CONVERSATION_MAX_TURNS`). `CONTRACTS.md` says the same: no disk.

### Tools and code context

As of 2026-11-12, the delivered names are the 39 of `TOOLS_OPENAI_SCHEMA` (`services/agent/tools_schema.py`). Sixteen are the `ToolSpec`s of `services/common/tool_models.py`, `TOOL_SPECS`: `build`, `build_step`, `test`, `compile_file`, `lint_clang_tidy`, `format_check`, `static_analysis`, four `git_*` (`git_log`, `git_blame`, `git_diff`, `git_show`), three `svn_*`, `lsp_calls`, `lsp_types`. Twenty-three are the Serena names of `services/agent/serena_named_tools.py`: symbols (`find_symbol`, `find_declaration`, `find_referencing_symbols`, `find_implementations`, `get_symbols_overview`, `get_diagnostics_for_file`, `get_diagnostics_for_symbol`, `restart_language_server`), files (`list_dir`, `find_file`, `search_for_pattern`, `read_file`) and writes (`rename_symbol`, `replace_symbol_body`, `insert_before_symbol`, `insert_after_symbol`, `safe_delete_symbol`, `replace_content`, `replace_in_files`, `replace_lines`, `insert_at_line`, `delete_lines`, `create_text_file`). The 2026-11-07 note counted twenty-seven `ToolSpec`s, including `search`, `list_files`, seven `lsp_*` and `begin_plan` / `edit` / `apply`, since removed (tickets 09 to 11 of `.scratch/serena-sidecar/`). `docs/architecture/overview.md` still says "27 named tools".

`execute_tool_call` (`tool_loop.py`) sends each Serena name to the sidecar (`McpSidecar`, `SERENA_BASE_URL`, default `http://serena:9121`) after `serena_refusal` (checkout, path, size), and any other name to `TOOLS_BASE_URL` (`http://tools:8091`), with `X-Project-Root` when a checkout is linked. `PATCH_BASE_URL` still exists but `_PATCH_TOOLS` is empty: the model can no longer name `patch` (the docstring of `execute_tool_call` still cites `begin_plan`, `edit` and `apply`). `compose.yaml`, services `patch` and `serena`: the only writable checkout mounts; `patch`: "No commit, no push". `agent` and `tools` mount `${CODEBASE_PATH:-./sample}:/workspace/app:ro`.

The default index is `sample/` (`README.md`, "Quick start"; `CODEBASE_PATH`). Another checkout is registered with `python scripts/deploy.py workspace`: `scripts/host/deploy/stack.py`, `cmd_workspace`, refuses a directory that is absent from **this** machine, then `scripts/host/workspaces.py`, `register_checkout`, writes `state/workspaces.json`. `services/agent/checkouts.py`, `resolve_workspace`, resolves only a path already in this registry; otherwise `UnknownWorkspace`. `main.py`, `chat` and `_checkout_or_400`, answer HTTP 400, detail `workspace is not registered`. The `X-Workspace` header is copied into the body if there is not already a `workspace` field (`_with_workspace_header`). The MCP does the same: `services/mcp/main.py`, `WorkspaceHeader`, copies only `X-Workspace`.

`docs/concepts/rag-explained.md` describes pasting chunks, not training. `retrieve_context` queries Qdrant (dense + BM25, then local reranker if `RERANKER_BASE_URL` is set). `CONTRACTS.md`, "Qdrant collection", fixes the size at 768 and the fallback if the reranker does not answer.

### Guardrails

| Control | Where it is |
| --- | --- |
| No shell | `SECURITY.md`, "The tools service": argv and `shell=False`. The handlers under `services/tools/handlers/` pass `shell=False`. `services/tools/main.py` recalls it at the top of the module. |
| Paths | `services/tools/handlers/path_safety.py`, `validate_path`: relative to the mounted root, rejects `..` and absolute paths, and resolves links before the containment test. |
| Bounded writes | `services/agent/edit_policy.py` blocks `build`, `test` and `compile_file` during `editing`, opened by a successful Serena write (`SERENA_WRITE_NAMES`) and closed by `build_step`. `prompts.py`, `SYSTEM_PROMPT`, says that git and svn are read-only and that there is no commit or push. |
| Network | Three `internal: true` networks (`compose.yaml`). `tools` is not on `core-net`. `SECURITY.md`, "Network segmentation". |
| Token | `verify_bearer_token` (`main.py`) requires `Authorization: Bearer <AGENT_BEARER_TOKEN>`, compared with `secrets.compare_digest`. Empty or wrong: 401. `GET /ui` does not have this dependency (`ui_page`: the token is entered in the page). |
| Concurrency | `_CHAT_SLOTS` on `AGENT_MAX_CONCURRENT`, default 2 (`main.py`, `compose.yaml`). Beyond that, 503 (`CONTRACTS.md`, `POST /chat`). |
| Narrower MCP | `services/mcp/server.py` exposes `lookup`, `check`, `calls`, `types`, `ask` (`outline` removed with sidecar ticket 09). `ask_agent` (`services/mcp/agent_client.py`) posts `/chat` with `tools=False` and `max_reply_tokens=400`. The token is read inside the container (`_bearer`), not sent by the editor. `CONTRACTS.md`, "mcp". |
| What is not a multi-tenant guardrail | `SECURITY.md`, "Known limitations": no rate limit, no per-user authorization, a single token. `docs/architecture/security.md`, "What this does not claim", says this is a local development tool. |

`docs/architecture/agent-loop.md` says that MCP `ask` is not the loop. The loop with tools is `POST /chat` and `POST /chat/stream` (the `GET /ui` page). The C++ `app` client calls the same `/chat` (`docs/architecture/overview.md`, `app` paragraph).

## Multiple machines

### One stack per machine

`CONTRACTS.md`, "Host prerequisites": Podman or Docker Compose, and the only start command is `python scripts/deploy.py up`. `scripts/host/deploy/stack.py`, `cmd_up`, builds and starts Compose on the machine that runs the script, waits for the services, and indexes `CODEBASE_PATH` unless `--skip-index`. `README.md`, "Quick start", describes the same gesture, with a WSL `local-llm` relay when the Windows Podman CLI has no machine, or `CONTAINER_RUNTIME=docker`.

Nothing in `cmd_up` registers a remote workstation or pushes the stack to another host. Two people on two machines have two stacks if each runs `up` at home, with their own weights, their own Qdrant and their own `AGENT_BEARER_TOKEN`.

### One stack, several workstations

The host doors of the page, the MCP and the model are on loopback: `compose.yaml`, `ui-proxy` `127.0.0.1:${AGENT_PORT:-8090}:8090`, `mcp` `127.0.0.1:${MCP_PORT:-8092}`, `llm` `127.0.0.1:${LLM_PORT:-8080}:8080`. `agent` has no `ports:`.

Two ways to reach this stack from another workstation, re-read in the sources:

- `docs/guides/vscode.md`, "Before opening the editor", gives an SSH `-L` tunnel for 8090, 8092 and 8080. The example URLs stay `127.0.0.1`. The guide shows a session for the editor's workstation. It does not describe a service that would stay open without SSH, nor an account per person.
- `scripts/host/deploy/expose.py`, docstring: the containers stay on `127.0.0.1`; the process listens on a LAN address and copies to the loopback only if the source IP is in the list. `DOORS` is 8080, 8090, 8092 and 3000. `up` does not launch `expose`.

The code seen by the agent is the code mounted on the stack's machine. `cmd_workspace` (`stack.py`) exits if the directory does not exist "on this machine". A remote workstation does not send its tree: `X-Workspace` selects an entry of `state/workspaces.json`.

### Several users at the same time

Absent as a product. A search in `services/` for `user_id`, `quota`, `tenant`, `account`: no occurrence.

What exists instead:

- A shared secret. `SECURITY.md`, "Authentication" and "Known limitations". `rotate-token` (`scripts/host/deploy/cli.py`, subcommand help) regenerates this secret and recreates `agent`, `app` and `mcp`.
- At most two `/chat` turns (`AGENT_MAX_CONCURRENT`). The same `conversation_id` waits (`_claim_turn`); another conversation gets 503 when the slots are taken. The id is a UUID generated if the client does not send one (`chat`). It is not a user.
- One generation slot (`LLM_PARALLEL=1`).
- Several registered checkouts (`workspaces.json`), which are paths on the stack's host, not per-person isolated spaces. Two checkouts of the same remote URL share the collection (`docs/architecture/overview.md`, `qdrant` paragraph; `register_checkout` indexes only the first time a remote is seen).
- The HTTP MCP is sessionless (`services/mcp/server.py`, `stateless_http=True`).

So: several people can, if the door is reached, send requests while sharing the same power and the same ceiling. The repository does not separate their files, their histories or their GPU time.

## Where it stops

The role "local LLM + harness on the machine that runs the stack" is the one the code implements. The role "people, on other machines, work on **their** code through a common stack" stops at the tunnel or the relay, and at a checkout already present on the server. The role "several simultaneous users" has not been started: no accounts, no quota, no isolation beyond the checkout path and the global semaphore.
