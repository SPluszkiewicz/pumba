# Parallel clients: what is missing

> **Status**: informational. Gaps observed, none is filled (one secret, one `llama-server` slot, no conversation owner, copies shared by path). Written on 2026-11-07 (file creation date); the passages on tree writing and on MCP are updated for the `serena` sidecar. · **Last verified**: 2026-11-12

**Verdict:** the first blocker is the generation slot. The `llm` container runs `llama-server` with `--parallel` at 1, yet the agent admits two `/chat` at a time, and port 8080 has neither a token nor this semaphore. There is no client concept: a single secret, a history with no owner, files and an index shared by repository path. The ports published on `127.0.0.1` prevent another machine from opening a socket until a tunnel or `expose` is launched; once the socket is open, the relay does not separate the clients.

Research note. The guides in `docs/` describe usage. The code, Compose and the contracts own the behavior. When a guide and the code diverge, both are cited separately.

## Question

What is missing so that several workstations or sessions send requests at the same time (`GET /ui`, `POST /chat`, `POST /chat/stream`, MCP) to the same stack, and each gets an answer without stepping on the others (conversation, code copy, GPU time, token)?

Three levels:

1. Several concurrent requests on the same stack (slots, queue, 503).
2. Several distinct clients at the same time (identity, token, conversation, workspace, files, history).
3. Reaching this stack from several machines, only where the network prevents this parallelism.

## Short answer

| Need | Already there | Missing |
| --- | --- | --- |
| Several generations at the same time | The agent admits `AGENT_MAX_CONCURRENT` `/chat` turns (default **2**). Beyond that, HTTP 503, no queue. | `LLM_PARALLEL` default **1**. The repository places no queue in front of `llama-server`. Port 8080 ignores the semaphore. Only one chat GGUF loaded (`LLM_MODELS_MAX` default **1**). |
| Several reads (lookup, check, LSP) | These routes do not take the `/chat` slot. Two `/lookup` in parallel are a trial of the functional check. | The `tools` cgroup is global (2 CPUs, 4 GiB, 256 processes). The CMake cache and the clangd process are one per tree, not one per client. |
| Two clients that do not share the conversation | A different `conversation_id` has its own thread. The same id waits, it does not get 409. A missing id is a UUID. | No user key. Anyone with the token can continue or cancel any id. The history is in memory, with a TTL. |
| Two clients that do not share the code | Several copies register. `X-Workspace` chooses an already mounted path. An unknown path is HTTP 400. | No copy per person. Same remote URL: one Qdrant collection. The `/ui` page and the C++ client send no workspace: they read the default tree. `patch` and `serena` mount this tree read-write; since the removal of `edit` / `apply`, `serena` is what the model calls to write. |
| Two tokens | One secret `AGENT_BEARER_TOKEN`. `rotate-token` replaces it for `agent`, `app` and `mcp`. | No token per workstation. MCP and port 8080 do not ask the client for it. |
| Two machines talking at the same time | `expose` accepts a list of IPv4 addresses and one thread per connection. nginx holds 64 connections on the page. | Without a tunnel or `expose`, the Compose ports are `127.0.0.1`. `up` does not launch `expose`. |

## 1. Concurrent requests on the same stack

### Already in place

`services/agent/main.py`, `_SlotGate`:

> Reject extra /chat callers instead of queueing them behind the cap.

`MAX_CONCURRENT = max(1, int(os.getenv("AGENT_MAX_CONCURRENT", "2")))`. Below 1, the code goes back up to 1. `compose.yaml`, `agent` service, injects `AGENT_MAX_CONCURRENT=${AGENT_MAX_CONCURRENT:-2}`. `CONTRACTS.md`, environment variables:

> `AGENT_MAX_CONCURRENT=2`, in-flight /chat cap; extras get 503 without queueing

`_claim_turn` takes a slot only after waiting for the same id. The docstring says the same id does not get 409, and that a distinct conversation gets 503 when all slots are taken. The wait is `_ConversationQueue.acquire` then `Turns.wait_until_free` (`services/agent/turns.py`). It happens before the slot. There is no timeout on this lock: the HTTP request stays open. nginx, `containers/host-proxy/nginx.conf`, sets `proxy_read_timeout 172800s` and `proxy_send_timeout 172800s`. A turn therefore holds its slot for the whole `execute_turn`, and a second message with the same id can wait that long.

The routes that take `_CHAT_SLOTS` are `POST /chat`, `POST /chat/stream` (`_claim_turn`) and `POST /review` (`review` calls `_CHAT_SLOTS.acquire` without a conversation queue). `CONTRACTS.md`, "agent: POST /review": same token, same slot, 503 if full.

`POST /lookup`, `POST /read`, `POST /check`, `POST /lsp/outline`, `POST /lsp/calls` and `POST /lsp/types` do not call `_CHAT_SLOTS` (`services/agent/main.py`, these handlers). `scripts/host/functional_check.py`, step "24. Two lookups at once", comments that this case targets the RAG path, not the `/chat` cap, and posts two `/lookup` via `ThreadPoolExecutor(max_workers=2)`.

`/build` and `/test` have a fresh temporary directory per request. `SECURITY.md`, section "The tools service":

> `/build` and `/test` each get a fresh `tempfile.mkdtemp()` scratch directory per request […] concurrent requests never share build state.

`services/tools/handlers/build.py`, module header, says the same, and reserves the durable cache for `/compile` and `/lsp/*`.

The workspace of a request is a `ContextVar` (`services/agent/scope.py`: `project_root_header`, `qdrant_collection`, `active_checkout_id`). `tools` and `patch` bind `X-Project-Root` in the request's task (`services/tools/main.py`, `services/patch/main.py`). Two requests do not swap this header in memory.

`containers/host-proxy/nginx.conf`: `worker_processes 1` and `worker_connections 64`. This is the ceiling of simultaneous connections to the page and to `/chat` that go through `ui-proxy` (host port 8090). MCP (8092) and the model (8080) do not go through this file.

### Gaps

**One `llama-server` slot, two agent turns.** `containers/llm/entrypoint.sh` passes `--parallel "${LLM_PARALLEL:-1}"`. `compose.yaml`, `llm` service: `LLM_PARALLEL=${LLM_PARALLEL:-1}`. `CONTRACTS.md`:

> `LLM_PARALLEL=1`, llama-server --parallel; one slot so the KV cache stays small

The reason written is the size of the KV cache, next to `LLM_CTX_SIZE=32768`, `LLM_CACHE_TYPE_K=q8_0`, `LLM_CACHE_TYPE_V=q8_0`, `LLM_GPU_MARGIN_MIB=2048` and `LLM_CACHE_RAM_MIB=4096`. The same file, on the chat model line, says a 16 GB GPU puts the embedder on the CPU if VRAM is tight. Nothing in the entrypoint refuses a larger value: the operator can set `LLM_PARALLEL`. The default shipped is 1. No file in `containers/llm/` or `services/agent/` adds a wait queue or a clean 503 in front of this slot. `services/agent/llm_client.py` sends the call to the OpenAI SDK, with `AGENT_LLM_MAX_RETRIES` (default 2) on connection error, 408, 409, 429 or 5xx. These are retries, not a turn queue. The repository does not describe what `llama-server` answers to a second completion while this single slot is busy.

**Port 8080 bypasses the semaphore.** `containers/llm/entrypoint.sh` passes no API key. `compose.yaml` publishes `127.0.0.1:${LLM_PORT:-8080}:8080`. A client that speaks `/v1/chat/completions` on this port does not go through `_CHAT_SLOTS`. `docs/guides/vscode.md`, Continue.dev section, gives `apiBase: http://127.0.0.1:8080/v1` and says `apiKey` only has to be non-empty (`apiKey: local`).

**One model loaded for the whole stack.** `CONTRACTS.md`: `LLM_MODELS_MAX=1`, simultaneous loaded chat models. `compose.yaml` injects `LLM_MODELS_MAX=${LLM_MODELS_MAX:-1}`. The entrypoint adds `--models-max` when `LLM_ROUTER` is not `0` (default `1`), and the comment says "Only one model stays loaded". `services/agent/model_catalog.py`, `select_model`: "Unload whatever is resident, load `model_id`". `services/agent/llm_client.py`, `set_active_model`, writes a global variable `_active_model`. `POST /models/select` (`main.py`) uses the same token for everyone. The page calls this POST (`services/agent/ui/index.html`, click on the model button). A client that changes GGUF unloads the others' model.

**EVO-X5 profile, the host binary does not receive `--parallel`.** `config/profiles/evo-x5.env` contains `LLM_PARALLEL=1`. `scripts/host/evo_x5_serve.py`, `serve_argv`, does not read this variable and does not pass `--parallel`. The container, for its part, passes it. The two paths therefore do not set the same slot. This file does not say what default `llama-server` applies when the flag is absent.

**Embedder and reranker.** `containers/embedder/entrypoint.sh` runs `llama-server` with `--host 0.0.0.0`, `-c` and `-b`, without `--parallel`. The reranker reuses this entrypoint (`EMBEDDER_RERANK=1`). The repository does not set a number of embedding slots. Concurrent lookups are not refused by the agent.

**`tools` is a global budget.** `compose.yaml`, `tools` service: `cpus: "2.0"`, `memory: 4G`, `pids: 256`, and `pids_limit: 256`. `SECURITY.md` presents these numbers as the local default, not as a per-client quota.

**Compilation and clangd shared per tree.** `persistent_build_dir` (`build.py`) is `<scratch>/cmake-cache/<root>/<preset>`, one per mounted tree and per preset. `ensure_configured` checks whether `CMakeCache.txt` exists, then configures. There is no lock around this directory in `build.py`. `services/tools/handlers/lsp.py`, `_session`, keeps one clangd process per key `commands_dir:stamp:project_root`. `ClangdSession.request` (`clangd_rpc.py`) takes `self._lock` for one call. Two clients of the same checkout share this process and this cache. Two checkouts have two keys (`root_cache_key`).

## 2. Distinct clients at the same time

### Already in place

A `conversation_id` is a string of at most 80 characters (`ChatRequest` in `main.py`). If it is absent, `chat` and `chat_stream` set `str(uuid.uuid4())`. `CONTRACTS.md`, "agent: POST /chat": the id is always returned; sending it back on the next turn keeps the history. Two distinct ids do not share this thread. The same id is serialized (`_claim_turn`, `Turns.start` raises `TurnBusy` if the id is already registered, then the loop retries).

`services/agent/conversations.py`, docstring: "In-memory multi-turn history. No disk, agent's rootfs is read-only." The dictionary key is `conversation_id` only (`ConversationStore._items`). `compose.yaml` injects `AGENT_CONVERSATION_TTL_SECONDS=${AGENT_CONVERSATION_TTL_SECONDS:-28800}` and `AGENT_CONVERSATION_MAX_TURNS=${AGENT_CONVERSATION_MAX_TURNS:-24}`. `CONTRACTS.md` documents 28800 and 24. The module's fallback, if the variable is not in the environment, is 3600 seconds and 8 turns (`TTL_SECONDS`, `MAX_TURNS`). Under Compose, the injected values are 28800 and 24. `sweep` removes an expired conversation only if `TURNS.active` is false.

Several copies: `scripts/host/workspaces.py`, module docstring:

> Every checkout of that URL shares one Qdrant collection. A second ticket records a path and a mount; it does not start a new index.

`register_checkout` writes `state/workspaces.json`. `compose_mounts_text` adds one volume per copy: `tools` and `agent` as `:ro`, `patch` and `serena` without `:ro`. `services/agent/checkouts.py`, `resolve_workspace`, resolves only a path from the registry, otherwise `UnknownWorkspace`. `main.py`, `_checkout_or_400` and `chat`, answer HTTP 400, detail `workspace is not registered`. The `X-Workspace` header is copied into the body if there is not already a `workspace` field (`_with_workspace_header`). `CONTRACTS.md`, Qdrant variables: "Unregistered paths are refused."

`checkouts.py`, `local_changes_note`: uncommitted files are not written to the shared collection; they are re-read from the checkout that asked the question (`fetch_local_note` sends `X-Project-Root` to that mount). `apply_proposal` (`services/patch/edits.py`) refuses to write if the file has changed since the proposal (`file changed since the proposal`). This safeguard is no longer on the model's path: `begin_plan`, `edit` and `apply` are no longer named tools, and Serena's writes (`services/agent/serena_named_tools.py`) do not go through `patch`.

HTTP MCP keeps no session. `services/mcp/server.py`, `FastMCP` constructor: `stateless_http=True`, `host="0.0.0.0"`. `services/mcp/main.py`, `WorkspaceHeader`, copies `X-Workspace` into a `ContextVar` for the request's task. `ask` calls `ask_agent` (`server.py`). `ask_agent` (`services/mcp/agent_client.py`) posts `POST /chat` with `tools=False`. `CONTRACTS.md`, `ask` tool: `max_reply_tokens` 400, the model does not receive the tool schema, and the conversation id is not in the returned text (`format_chat_result`: "The conversation id stays out of the text."). `lookup`, `check`, `calls` and `types` call `/lookup`, `/check` or `/lsp/*`, so not `_CHAT_SLOTS` (`outline`, which called `/lsp/outline`, is no longer an MCP tool). `ask`, for its part, takes a `/chat` slot.

The `GET /ui` page does not have the token (`ui_page`: "No bearer: the token is typed into the page and sent only on /chat/stream."). `index.html` stores the token in `sessionStorage` and keeps `conversationId` in a page variable. The `Nouvelle conversation` button (UI label; "New conversation") resets this variable to empty. Two tabs have two variables, hence two ids, unless they copy the same one.

`app/src/main.cpp` keeps a single `std::string conversation_id` for the process, read from `AGENT_BEARER_TOKEN` and `AGENT_BASE_URL`. `AgentClient::Chat` (`app/src/AgentClient.cpp`) sends `query` and, if non-empty, `conversation_id`. Nothing in `app/src/` sends `workspace` or `X-Workspace`. This binary does not lock the server: a second process can call the same URL. It carries no identity either.

`cmd_workspace` (`scripts/host/deploy/stack.py`) exits if the directory does not exist "on this machine". A remote workstation does not push its tree through the API.

### Gaps

**A single secret.** `verify_bearer_token` (`main.py`) re-reads `AGENT_BEARER_TOKEN` and compares `Authorization: Bearer <token>` with `secrets.compare_digest`. Empty, absent or wrong: 401. The routes `/lookup`, `/read`, `/check`, `/lsp/*`, `/models`, `/models/select`, `/chat`, `/review`, `/chat/cancel` and `/chat/stream` declare this dependency. `GET /ui` and `GET /health` do not declare it. `SECURITY.md`, "Authentication": the agent's `/chat` requires this static token. "Known limitations":

> No rate limiting or per-user authorization, one shared bearer token for the whole `agent` API.

`docs/architecture/security.md`, "What this does *not* claim to defend against", says the same: no per-user authorization, one token for the whole API, the concurrency cap is `AGENT_MAX_CONCURRENT`. `scripts/host/deploy/ops.py`, `cmd_rotate_token`, writes a new `secrets.token_urlsafe(32)` into `.env` and recreates `agent`, `app`, `mcp` and `ui-proxy` (both branches, with or without `--no-deps`). It is a replacement of the shared secret. The subcommand's help, in `scripts/host/deploy/cli.py`, names only `agent`, `app` and `mcp`: "regenerate AGENT_BEARER_TOKEN and recreate agent, app, and mcp". The `--skip-recreate` of the same function says to restart `agent`, `app` and `mcp` yourself.

**No owner on the conversation.** The store has no key other than `conversation_id`. `POST /chat/cancel` (`cancel_chat`) calls `TURNS.cancel(body.conversation_id)` for any token holder. Two clients that send the same id share the thread and the wait queue of that id. Nothing ties the id to the socket, the address or the token: the token is unique.

**No account, no quota, no tenant.** A search for `user_id`, `quota` and `tenant` in `services/`, `containers/` and `scripts/`: no occurrence. `app/src/` and `app/tests/` do not have these words either (the C++ client does not send them). The phrases "multi-tenant" and "per-user" are in `SECURITY.md` and `docs/architecture/security.md`, as a description of what the tool is not. `docs/notes/verification/roadmap.md` talks about "quotas" for the `tools` cgroup and the semaphore, not for a user.

**The workspace is not a person.** The isolation shipped is the registered path. `normalize_remote` (`workspaces.py`) strips the userinfo from the URL "so two accounts share one index". `repo_id_for` is `repo_` plus the SHA-1 of this URL. Two checkouts of the same remote, or two accounts on the same URL, have the same collection. `local_changes_note` tells the model: "This checkout shares the repository index with every other ticket. Retrieved chunks can come from another revision."

The page and the C++ client send no workspace. `_bind_checkout("")` leaves the `ContextVar`s empty (`main.py`). The collection used is then the default `QDRANT_COLLECTION=code_chunks` (`CONTRACTS.md`). Both read and, via Serena's write tools, can write the tree `${CODEBASE_PATH:-./sample}` mounted on `/workspace/app` (`compose.yaml`: `agent` and `tools` as `:ro`, `patch` and `serena` without `:ro`). Two tabs or two `app` processes share this tree and this index.

Two clients that send the same `X-Workspace` share the same mount. `patch` and `serena` have no per-client copy. `_CAMPAIGNS` (`services/patch/edits.py`) is a process dictionary of the `patch` service, key `campaign_id` (`secrets.token_hex(8)`), not a user key. The "file changed since the proposal" check used to detect a collision on the `patch` side; it is no longer on the model's path, and nothing equivalent is written in this repository for Serena's writes. Neither gives each client its own file.

**MCP does not authenticate the workstation.** `WorkspaceHeader` reads only `X-Workspace`. `_bearer` in `agent_client.py` adds the container's `AGENT_BEARER_TOKEN`. `docs/notes/contracts/mcp.md`, opening: "The token stays in the container." Section "From the host": "The token stays inside the `mcp` container; the editor does not send `AGENT_BEARER_TOKEN`." `install_vscode_mcp` (`workspaces.py`) writes `http://127.0.0.1:8092/mcp` and an `X-Workspace` equal to the copy's path. Two editors open on the same copy send the same path. `ask` without `conversation_id` (default `""` in the tool, then empty string removed by `_query_body`) gets a fresh UUID on the agent side, and `format_chat_result` does not write this id into the text returned to the editor. The next `ask` turn therefore does not find the thread, unless the caller sends back an id it kept elsewhere. It is not a thread per workstation.

**`POST /models/select` is global.** See level 1: one client changes the GGUF for all the others.

## 3. Reaching the stack from several machines

This level covers only what prevents several machines from each having a request in flight. Identity and the GPU remain levels 1 and 2, including after the tunnel.

### What does not serialize the clients

`scripts/host/deploy/expose.py`, `serve`: `listen(16)` is the accept backlog. Each accepted connection whose IP is in the list goes into a thread (`_bridge`). There is no workstation counter. `parse_allowlist` keeps several distinct IPv4 addresses and refuses `*`, `0.0.0.0`, a network and a non-IPv4 address. `cmd_expose` opens nothing if the list is empty.

`docs/guides/vscode.md`, "Before opening the editor", shows an SSH `-L` tunnel for 8090, 8092 and 8080, for the editor's workstation (command cited in [`pumba-serveur-multi-clients.md`](pumba-serveur-multi-clients.md)). The guide does not describe a second workstation. The server code does not count the tunnels: nothing in `compose.yaml` or in `expose.py` refuses a second SSH forward to the same loopback ports.

nginx bounds the page to 64 connections (`nginx.conf`), not per client. MCP and the model have their own ports.

MCP is sessionless (`stateless_http=True`). Two concurrent POST `/mcp` do not share a server session. They share the container's token and, if they send the same `X-Workspace`, the same checkout.

### What prevents parallelism

**Without a relay, another machine has no socket.** `compose.yaml` publishes:

- `llm`: `127.0.0.1:${LLM_PORT:-8080}:8080`
- `ui-proxy`: `127.0.0.1:${AGENT_PORT:-8090}:8090`
- `mcp`: `127.0.0.1:${MCP_PORT:-8092}:${MCP_PORT:-8092}`

`agent` has no `ports:`. `CONTRACTS.md`, "Ports" table, repeats `127.0.0.1` for 8080, 8090 via `ui-proxy`, and 8092, and says not to publish MCP on `0.0.0.0`. Embedder (8081), reranker (8082), Qdrant (6333), `tools` (8091), `patch` (8093) and `serena` (9121, `tools-net`) are not published. A second machine cannot open these ports until an SSH tunnel or the `expose` process listens elsewhere.

**`up` does not launch the relay.** `expose` is a separate subcommand (`cli.py`). The docstring of `expose.py` says the containers stay on `127.0.0.1` and that this process copies to that loopback only if the source address is in the list. A machine absent from `--allow` or from `state/expose-allow.txt` is closed (`peer_allowed`). This is not a parallelism ceiling between the listed machines: those that are listed go through at the same time, within the limit of the level 1 slots.

**EVO-X5: the model is already on all interfaces, without the semaphore.** `scripts/host/evo_x5_serve.py`, docstring: llama-server listens "on every interface of this machine so the agent container can call it. The page and MCP stay on 127.0.0.1." `serve_argv` passes `--host 0.0.0.0`. `config/compose/host-llm.yaml` empties the `llm` container's ports (`ports: !override []`) and publishes neither 8090 nor 8092. A workstation on the network can therefore send completions to port 8080 of this machine without `expose` and without `_CHAT_SLOTS`. The page and MCP remain unreachable without a tunnel or relay. If `expose` also tries to bind 8080, `cmd_expose` catches `OSError` and marks this port "not forwarded"; the other ports continue. The model remains the `0.0.0.0` listener of the host process, still without `--parallel` in `serve_argv`.

Once the socket is obtained, nothing in the relay adds a token, a conversation or a copy per machine. The gaps of levels 1 and 2 apply as they are.
