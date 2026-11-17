# CONTRACTS.md - Shared source of truth

This file is the single source of truth for anything that spans more than one
component (ports, env var names, pinned versions, API schemas, network names).
Every component reads this file before writing code. If something isn't
specified here, don't invent a convention that conflicts with another
component - add it here first.

Full architectural rationale lives in the plan; this file only has the
concrete, load-bearing facts implementers need.

## Pinned versions (as of 2026-08-11 - do not silently bump)

| Component | Pin |
|---|---|
| llama.cpp | tag `b10216` (github.com/ggml-org/llama.cpp, 2026-07-31) |
| Qdrant | image `qdrant/qdrant:v1.19.0` |
| ui-proxy (nginx) | image `docker.io/nginxinc/nginx-unprivileged:1.27-alpine` (non-root, `cap_drop: ALL`) |
| Python (all services) | `python:3.11-slim-bookworm` |
| C++ toolchain base (app, tools, devtools) | `ubuntu:24.04` (gcc/g++ 13 default, cmake 3.28.3 default via apt - matches the target deployment OS of the C++ project this stack builds/tests) |
| Catch2 (app/tests) | Ubuntu 24.04 apt `catch2` (v3) - `find_package(Catch2 3)`, not FetchContent (tools-net is isolated) |
| CUDA base (llm, embedder GPU variant) | `nvidia/cuda:12.6.3-devel-ubuntu24.04` (build stage), `nvidia/cuda:12.6.3-runtime-ubuntu24.04` (runtime stage) |
| clang-tidy / clang-format / cppcheck | Ubuntu 24.04 apt defaults (clang-tools-18, cppcheck 2.13.0) |
| ripgrep | Ubuntu 24.04 apt package `ripgrep` |
| Serena sidecar (`serena`) | image `ghcr.io/oraios/serena:2.1.0` (pulled, not built; the tag is the upgrade - see `docs/adr/0001-serena-sidecar.md`) |

Base images are pinned by tag now; pin to `@sha256:<digest>` once each image
has actually been pulled once locally (record the digest in this file when
you do - don't leave tag-only pins in the final Containerfiles).

## Models

| Role | Repo | File | Notes |
|---|---|---|---|
| Chat (`llm`) | `bartowski/Qwen2.5-Coder-14B-Instruct-GGUF` | `Qwen2.5-Coder-14B-Instruct-Q5_K_M.gguf` | Code model for review. Q5_K_M (~9.8 GiB). On a 16 GB GPU, put the embedder on CPU if VRAM is tight. Sub-Q5 quants degrade tool-call JSON. A LoRA trained on Mistral Small 4 does not load on this file |
| Embedding (`embedder`) | `nomic-ai/nomic-embed-text-v1.5-GGUF` | `nomic-embed-text-v1.5.Q8_0.gguf` | requires `search_query: `/`search_document: ` prefixing, mean pooling |
| Reranker (`reranker`) | `gpustack/bge-reranker-v2-m3-GGUF` | `bge-reranker-v2-m3-Q8_0.gguf` | cross-encoder; `llama-server --embedding --pooling rank --reranking`. Not fetched by `--test` |
| Test/CI chat model (`llm` only, under the `test` compose profile) | `bartowski/Qwen2.5-0.5B-Instruct-GGUF` | `Qwen2.5-0.5B-Instruct-Q4_K_M.gguf` | tiny, CPU-only, used only by `tests/integration/test_smoke.py` |
| QLoRA train base (`trainer`, `setup --hf-train-base`) | `mistralai/Mistral-Small-4-119B-2603` (HF safetensors) | `./models/hf/Mistral-Small-4-119B-2603/` | Same model as the evo-x5 GGUF. ~242 GiB. An adapter loads on `mistral-small-4`, not on the 14B Qwen |

Optional chat files for the GPU cases (`16g-gptoss`, `24g-30b`, `48g-30b`, `48g-32b`, `evo-x5`) live in `scripts/container/fetch_models.py` as `EXTRA_MODELS`. `deploy.py up` does not download them. The default pin stays the 14B Q5 above. `evo-x5` is Mistral Small 4 (`unsloth/Mistral-Small-4-119B-2603-GGUF`, UD-Q5_K_M, three shards, alias `mistral-small-4` on the host). Its window is 262144, the model's trained context. The CUDA `llm` image does not serve it.

**Important:** the `test` profile swaps only the **chat** model (`llm`) for
the tiny one above - `embedder` always runs the real
`nomic-embed-text-v1.5.Q8_0.gguf`, in both prod and test. It's already small
and CPU-only, and swapping it would change the embedding vector dimension,
breaking the hardcoded `VECTOR_SIZE = 768` assumption in the indexer's
Qdrant collection (and everywhere else that assumes 768-dim vectors). Never
point `EMBEDDER_MODEL_PATH` at a non-nomic-embed-text-v1.5 model.

Model files land in `./models/` on the host (bind-mounted read-only into
containers), gitignored, downloaded by `scripts/container/fetch_models.py` (run inside a
container - see "Host prerequisites" below).

## Directory layout

See "Repository layout" in `README.md`. Key point for parallel work: each
component owns its own subtree and should not need to edit files outside it
except where explicitly noted below (mostly: adding entries to `.env.example`
if you introduce a new env var - append, don't restructure).

## Networks (defined in compose.yaml, all `internal: true` except noted)

- `client-net`: `app` + `mcp` + `ui-proxy` <-> `agent` only.
- `core-net`: `agent`, `llm`, `embedder`, `reranker`, `qdrant`, `indexer`.
- `tools-net`: `agent`, `tools`, and `serena`. Nothing here is published. The agent is the only client of the other two. `llm`/`embedder`/`qdrant` are never on this network and `tools` is never on `core-net`.
- default/bridge (not internal): used only by the `setup` profile's `fetch-models` job, which is the one place real internet access is needed.
- `docs` service is not on any of the above networks; it's standalone with its port published for local viewing.
- `mcp` sits on `client-net` (same trust tier as `app`) plus `publish-net` for
  its loopback host port: it forwards to `agent` `/chat` and never joins
  `core-net` or `tools-net`.
- `publish-net` (not internal, no outbound route): `llm`, `mcp`, and `ui-proxy`.
  Docker Desktop does not publish a host port for a container that is only on
  an internal network. `config/compose/docker.yaml` turns masquerade off;
  `config/compose/podman.yaml` sets `no_default_route` (Podman rejects the Docker
  option). `agent` stays off this network and has no egress, except on
  `evo-x5`: `config/compose/host-llm.yaml` adds it to `publish-net` so it can
  call the host `llama-server`. That network still has no default route.

## Ports (container-internal; host publishes `docs`, plus `llm`, `mcp`, and `agent` on loopback)

| Service | Port | Protocol |
|---|---|---|
| `llm` | 8080 | HTTP, OpenAI-compatible (`/v1/chat/completions`, `/props`, `/health`). Published **`127.0.0.1:8080` only** (`LLM_PORT`). On `evo-x5` this container is not started: the host process listens on `0.0.0.0:8080`. Embedder and Qdrant stay unpublished. |
| `embedder` | 8081 | HTTP, OpenAI-compatible (`/v1/embeddings`, `/health`) |
| `reranker` | 8082 | HTTP (`POST /v1/rerank`, `/health`). Same `llama-server` image as `embedder`, different model and pooling. Not published to the host. |
| `qdrant` | 6333 | HTTP REST (`/collections/...`, `/healthz`) |
| `qdrant` | 6334 | gRPC (unused for now, kept for future) |
| `agent` | 8090 | HTTP (`/chat`, `/chat/stream`, `/chat/cancel`, `/review`, `/lookup`, `/read`, `/check`, `/lsp/outline`, `/lsp/calls`, `/lsp/types`, `GET /models`, `/models/select`, `GET /ui`, `/health`, `/metrics`). Listens on 8090 inside the container. The host reaches it through `ui-proxy` at **`127.0.0.1:8090` only**, so `/ui` and `/chat/stream` stay on the same origin. `ui-proxy` forwards every path (body limit 1 MiB), `/metrics` included. `GET /ui`, `/health`, and `/metrics` are unauthenticated; the bearer stays in the browser (`sessionStorage`) and is required on every other route. |
| `tools` | 8091 | HTTP. Model-facing (`TOOL_SPECS`): `/build`, `/build-step`, `/test`, `/compile`, `/git/log`, `/git/blame`, `/git/diff`, `/git/show`, `/svn/status`, `/svn/diff`, `/svn/log`, `/lsp/calls`, `/lsp/types`, `/lint/clang-tidy`, `/format/check`, `/static-analysis`. Agent-only (no tool name): `/git/diff-names`, `/git/status`, `/read-file`, `/lsp/outline`, `/cancel`. Plus `/health`, `/metrics`. `/build-step` reuses the configure cache and builds in parallel. |
| `serena` | 9121 | MCP Streamable HTTP (`/mcp`) of the Serena sidecar, pinned image. Not published, `tools-net` only, no authentication. Compose fixes the port in the start command; `SERENA_BASE_URL` only tells the agent where to dial. Checkout mount is read-write (the only writer); `agent` and `tools` stay `:ro`. The CMake cache volume is `:ro`. See "`serena` sidecar" below. |
| `mcp` | 8092 | Streamable HTTP MCP (`/mcp`) + `GET /health`. Published **`127.0.0.1:8092` only**. `MCP_TRANSPORT=stdio` skips the bind (Cursor `podman compose run --rm -T mcp`). |
| `docs` | 8000 | HTTP (published to host as `8000:8000`, `serve` mode, `docs` profile; unlike the others it has no `127.0.0.1` bind) |

## Environment variables (all defined once in `.env.example`, consumed via compose `environment:`)

```
# --- host (compose bind mount; not read by any service process) ---
CODEBASE_PATH=./sample   # host dir mounted at /workspace/app: :ro in indexer, agent, tools; read-write in serena

# --- agent ---
AGENT_PORT=8090
AGENT_BEARER_TOKEN=changeme-generate-a-real-secret   # required header: Authorization: Bearer <token>
AGENT_TOOL_LOOP_MAX_ITERATIONS=48   # deployed value; code default 8 (see "Deployed default vs code default")
AGENT_LOOP_NUDGES=3              # prose refusals while a failure is open or edits are unverified
AGENT_LOOP_MALFORMED_LIMIT=3     # consecutive malformed tool batches before a plain answer
AGENT_LOOP_REPEAT_LIMIT=3        # identical tool batches tolerated; the next one is not executed
AGENT_REQUEST_TIMEOUT_SECONDS=14400   # one hop; a large tree may use most of it
AGENT_LLM_MAX_RETRIES=2     # openai SDK retries (backoff) on connection errors / 429 / 5xx toward llm + embedder
AGENT_MAX_CONCURRENT=2      # in-flight /chat cap; extras get 503 without queueing
AGENT_CONTEXT_BUDGET_TOKENS=28544  # measured tokens before compression; room under LLM_CTX_SIZE
AGENT_REPO_MAP_CHARS=16000          # sketch pasted on architecture questions; the walk itself is uncapped
AGENT_REFLECT=0             # 1 = second LLM pass (json_schema) that only deletes contradicted sentences
AGENT_GATE_CLASSIFY=1       # 1 = one short LLM call when rules do not already choose direct vs code
AGENT_CONVERSATION_TTL_SECONDS=28800   # deployed value; code default 3600
AGENT_CONVERSATION_MAX_TURNS=24         # deployed value; code default 8
LOG_LEVEL=INFO              # structlog minimum level (agent, indexer, trainer); JSON lines on stdout
APP_REQUEST_TIMEOUT_SECONDS=172800   # whole POST /chat (app, deploy ask)

# --- llm ---
LLM_PORT=8080
LLM_SERVE=container                   # container = compose llm; host = evo-x5 llama-server
LLM_BASE_URL=http://llm:8080
LLM_MODEL_PATH=/models/Qwen2.5-Coder-14B-Instruct-Q5_K_M.gguf
LLM_ROUTER=1                          # 1 = --models-dir router; POST /models/load swaps without restart
LLM_MODELS_MAX=1                      # simultaneous loaded chat models
LLM_LORA_PATH=                        # optional; e.g. /models/adapters/latest/adapter.gguf
LLM_GPU_ARG=1                         # compose build arg; 0 = CPU-only image (.env.test)
LLM_NGL=auto                          # 16g profile. 24g and 48g set 999 (see config/profiles/)
LLM_CTX_SIZE=32768                    # llama-server -c for the 14B. evo-x5 sets 262144
LLM_CACHE_TYPE_K=q8_0                 # 16g and 24g. The 48g profile uses f16. 14B CTX stays 32768
LLM_CACHE_TYPE_V=q8_0                 # --cache-type-v
LLM_PARALLEL=1                        # llama-server --parallel; one slot so the KV cache stays small
LLM_GPU_MARGIN_MIB=2048               # --fit-target; MiB of VRAM left free. Extra layers go to CPU
LLM_CACHE_RAM_MIB=4096                # --cache-ram; max MiB of system RAM for the KV / prompt cache
LLM_THREADS=                          # runtime -t (physical cores); empty = llama.cpp auto
LLM_THREADS_BATCH=                    # runtime -tb (prompt processing); empty = same as -t
LLM_NUMA=                             # runtime --numa distribute|isolate|numactl; empty = off

# --- trainer (profile `train`; not read at runtime by serving services) ---
TRAIN_BASE_MODEL=/models/hf/Mistral-Small-4-119B-2603
TRAIN_ADAPTER_OUT=/models/adapters
TRAIN_DATASET_DIR=/models/training
TRAIN_MAX_STEPS=200
TRAIN_LORA_RANK=16
TRAIN_LORA_ALPHA=32
TRAIN_MAX_SEQ_LENGTH=2048

# --- embedder ---
EMBEDDER_PORT=8081
EMBEDDER_BASE_URL=http://embedder:8081
EMBEDDER_MODEL_PATH=/models/nomic-embed-text-v1.5.Q8_0.gguf
EMBEDDER_GPU_ARG=0                    # compose build arg; 16g profile is CPU. 24g/48g set 1
EMBEDDER_NGL=0                        # runtime -ngl; 24g/48g set 999
EMBEDDER_THREADS=                     # runtime -t; empty = auto
EMBEDDER_NUMA=                        # runtime --numa; empty = off

# --- reranker (core-net; empty RERANKER_BASE_URL disables the second pass) ---
RERANKER_PORT=8082
RERANKER_BASE_URL=http://reranker:8082
RERANKER_MODEL_PATH=/models/bge-reranker-v2-m3-Q8_0.gguf
RERANKER_NGL=0                        # 16g profile. 24g and 48g set 999
RERANKER_THREADS=                     # runtime -t; empty = auto
RERANKER_NUMA=                        # runtime --numa; empty = off

# --- qdrant ---
QDRANT_URL=http://qdrant:6333
QDRANT_COLLECTION=code_chunks   # default when a request has no workspace
# Registered checkouts live in state/workspaces.json. A request field
# `workspace` (or header X-Workspace) selects collection repo_<sha1 of the
# remote URL>. Unregistered paths are refused.

# --- tools ---
TOOLS_PORT=8091
TOOLS_BASE_URL=http://tools:8091
SERENA_BASE_URL=http://serena:9121   # agent -> sidecar MCP; the client appends /mcp. Not published. compose.yaml does not forward it to agent: the code default is the same URL, so changing .env alone has no effect
TOOLS_BUILD_TIMEOUT_SECONDS=14400   # every tools subprocess, not only cmake
TOOLS_CMAKE_PRESET=release
TOOLS_PROJECT_ROOT=/workspace/app   # must match compose.yaml's `${CODEBASE_PATH:-./sample}:/workspace/app:ro` mount
TOOLS_HEALTH_STRICT=1               # compose default (code default 0); /health is 503 if cmake/ctest/git/rg/svn/... missing
SERENA_HEALTH_STRICT=1              # read by the serena container's healthcheck only; a dead sidecar fails the stack the way a dead tools does

# --- indexer ---
INDEXER_SOURCE_ROOT=/workspace/app
INDEXER_CHECKOUT_ID=default         # payload checkouts[]; deploy workspace sets co_<hash>

# --- mcp (Cursor / Claude Code / OpenCode; client-net only) ---
MCP_PORT=8092
MCP_TRANSPORT=http          # http = streamable-http /mcp ; stdio = compose run -T
MCP_REQUEST_TIMEOUT_SECONDS=172800   # mcp -> agent /chat (matches APP_REQUEST_TIMEOUT_SECONDS)
AGENT_BASE_URL=http://agent:8090  # also used by `app`

# --- observability (compose overlay, profile `obs`; not started by `up`) ---
GRAFANA_ADMIN_PASSWORD=changeme-generate-a-real-secret   # loopback :3000; first volume init only
```

Every service reads its config exclusively from env vars with these exact
names - no hardcoded hosts/ports/paths in source. One port is fixed by
`compose.yaml` instead (`serena` 9121), and the third-party
`serena` image takes its settings from `config/serena/serena_config.yml`.

**Deployed default vs code default.** `.env.example` and `compose.yaml` win
when the stack is started by `deploy.py up`. The code default only applies
when the variable is absent from the process (bare `python`, unit tests).

| Variable | Deployed default | Code default |
|---|---|---|
| `AGENT_TOOL_LOOP_MAX_ITERATIONS` | 48 | 8 |
| `AGENT_CONVERSATION_TTL_SECONDS` | 28800 | 3600 |
| `AGENT_CONVERSATION_MAX_TURNS` | 24 | 8 |
| `TOOLS_HEALTH_STRICT` | 1 | 0 |

**Code-only knobs** (read by the code, not templated in `.env.example`, not
forwarded by compose unless noted): `AGENT_CONTEXT_OVERHEAD_TOKENS` (3200) and
`AGENT_CONTEXT_REPLY_RESERVE` (1024) feed the token budget;
`AGENT_MAP_ROOT` (`/workspace/app`) and `AGENT_PROJECT_ROOT` (compose sets
`/workspace/app`) locate the repository sketch and `.llm/rules`;
`WORKSPACES_REGISTRY` (`/workspace/workspaces.json`, compose mounts
`state/workspaces.json` read-only there); `TOOLS_CLANG_TIDY_CONFIG_FILE` and
`TOOLS_CLANG_FORMAT_STYLE_FILE` (`/etc/tools-config/...`).

## API contracts

### `agent`: `POST /chat`

Request:
```json
{ "query": "How does the indexer chunk C++ files?", "conversation_id": "optional, at most 80 characters", "instructions": "optional, this question only, max 4000 characters", "workspace": "optional host path of a registered checkout", "max_reply_tokens": 400, "tools": true }
```
Headers: `Authorization: Bearer <AGENT_BEARER_TOKEN>` (required, 401 if missing/wrong, and always 401 when the token is unset). `X-Workspace` is the header form of `workspace`; the body field wins.

Response (200):
```json
{
  "answer": "string, the final answer",
  "sources": [
    { "file_path": "app/src/main.cpp", "start_line": 12, "end_line": 40, "symbol_name": "main" }
  ],
  "used_context": true,
  "tool_calls": [
    { "name": "build", "arguments": {"preset": "release"}, "result_summary": "build succeeded" }
  ],
  "reflection": { "removed": 1 },
  "nudges": 2,
  "conversation_id": "uuid",
  "route": "code"
}
```
- `route` is `direct` or `code`. `direct` skips retrieval and does not attach tool schemas (arithmetic, greetings, and other non-repository questions). `code` is the repository path: retrieval, then the tool loop. Rules decide the obvious cases. If they do not, an ongoing conversation stays on `code`. A first message is classified by one LLM call when `AGENT_GATE_CLASSIFY=1`; any failure or an unclear label stays on `code`.
- `used_context: false` + empty `sources` is a normal, valid response (`route: direct`, or Qdrant collection missing/empty on `route: code`). On the code path with an empty index, `answer` should say no repository context was used. A `direct` answer does not.
- `tool_calls` is `[]` if no tools were invoked.
- `reflection` is omitted unless `AGENT_REFLECT=1` and the second pass removed at least one sentence.
- `nudges` counts prose refusals while a failure is still open or an edit is not verified. It is omitted when it is 0.
- `conversation_id` is always returned. Pass it on the next request to keep history (in-memory, TTL `AGENT_CONVERSATION_TTL_SECONDS`, at most `AGENT_CONVERSATION_MAX_TURNS` pairs). No disk.
- `instructions`, when set, is appended to the system prompt for this request only. It does not replace the built-in conventions or the rules. At most 4000 characters.
- `workspace` selects a registered Checkout (its Qdrant collection and its `/checkouts/<id>` mount, sent to `tools` as `X-Project-Root`). An unregistered path is `400`. Empty means the default tree (`CODEBASE_PATH`).
- `max_reply_tokens` (32..1024) caps the chat model's reply; `tools: false` answers from the excerpts only, with no tool schema. MCP `ask` sets both (400, `false`).
- Project rules are the `*.md` files in `<CODEBASE_PATH>/.llm/rules` (mounted read-only, `AGENT_PROJECT_ROOT=/workspace/app`). Each file is at most 8 KiB and a directory is capped at 16,000 characters. They are included on the code path even when retrieval returns nothing, and they come from the default tree whatever `workspace` says. The indexer skips `.llm`.
- `503` when `AGENT_MAX_CONCURRENT` `/chat` slots are already taken. A second `POST /chat` or `POST /chat/stream` for a `conversation_id` that already has a turn waits until that turn has stored its trace, then runs. It does not return `409`. The wait happens before a slot is taken. Pass `conversation_id` if you want `POST /chat/cancel` to stop a non-stream turn.

### `agent`: `POST /chat/stream`

Same body and auth as `/chat`. An unregistered `workspace` is `400` before the stream opens. Response is `text/event-stream`; `docs/notes/contracts/agent-api.md` has the per-event table:

```
event: meta
data: {"conversation_id": "uuid"}

event: route
data: {"route": "direct", "by": "rules"}

event: rag
data: {"used_context": true, "num_sources": 2, "sources": [...]}

event: context
data: {"used": 9120, "window": 32768, "ratio": 0.278, "compressed": false}

event: activity
data: {"phase": "generating"}

event: tool_delta
data: {"index": 0, "name": "build_step", "arguments": "{\"pre"}

event: tool_call
data: {"name": "build", "arguments": {"preset": "release"}}

event: tool_result
data: {"name": "build", "result_summary": "build succeeded", "ok": true}

event: token
data: {"text": "The "}

event: nudge
data: {"reason": "tool_failed", "tool": "build_step", "remaining": 2}

event: done
data: { ...same object as POST /chat... }
```

`event: nudge` is sent when a prose reply is refused and the loop continues. `reason` is `tool_failed`, `needs_build`, or `no_tool`. `tool` is the open tool name for `tool_failed`, the string `build_step` for `needs_build`, and absent for `no_tool`. `remaining` is the number of refusals still allowed after this one: 2, then 1, then 0. `event: error` + `{"message"}` if the turn fails after the stream has started. `/chat` (non-stream) remains for scripts.

### `agent`: `POST /chat/cancel`, `GET /models`, `POST /models/select`

Same bearer. `POST /chat/cancel {"conversation_id"}` stops the running turn of that conversation and returns `{"stopped": true|false}`; the turn's answer is `Stopped.`. `GET /models` returns `{"models": [{"id", "status", "failed"}], "active"}` (503 when the llama-server router is unreachable). `POST /models/select {"model": id}` loads a model and returns `{"model": {"id", "status", "failed"}}` (404 unknown id, 503 on failure or timeout). Field-level detail: `docs/notes/contracts/agent-api.md`.

### `agent`: `POST /review`

Same bearer as `/chat`. Same `AGENT_MAX_CONCURRENT` slot (503 when full). The body has no `workspace` field and the route ignores `X-Workspace`: it always reviews the default tree (`CODEBASE_PATH`), and only when that tree is a git work tree (the diff goes through `tools` `/git/diff`; there is no Subversion variant).

```json
{ "from": "HEAD~1", "to": "HEAD" }
```

`from` / `to` are git refs, allow-listed by `tools` (`HEAD`, `HEAD~N`, 7-40 hex sha, or a branch/tag name starting with a letter; no `..` range). Response:

```json
{
  "ok": true,
  "from": "HEAD~1",
  "to": "HEAD",
  "files": ["src/a.cpp"],
  "truncated": false,
  "dropped": 1,
  "comments": [
    {"path": "src/a.cpp", "line": 21, "snippet": "return leaked;", "text": "the pointer is not freed"}
  ]
}
```

At most 8 files. One LLM call per file. A comment is kept only when `snippet` occurs on a new-file line of that file's diff (`line` is 1-based in the new file). Unanchored comments are counted in `dropped` and omitted. `ok: false` plus `error` when `git diff --name-only` fails. `/git/diff-names` is not an LLM tool.

### clangd (inside `tools`, not a separate container)

`compile_commands.json` is produced by `/compile` and `/build-step` in the `cmake-cache` volume that `tools` writes (`/workspace/scratch/cmake-cache`). clangd cannot run elsewhere without sharing that directory: `serena` mounts the same volume read-only for its own clangd.

- `POST /lsp/outline` body `{"path", "preset?"}`. `stdout` is one symbol per line (`name kind line`), at most 40. Reached through `agent` `POST /lsp/outline`; not a named tool, and MCP no longer has `outline`.
- `POST /lsp/calls` body `{"path", "line", "character", "direction?", "preset?"}`. `direction` is `in`, `out`, or `both` (default). `stdout` is `name path:line`. Named tool `lsp_calls`. clangd answers `-32601` to outgoing calls and the handler maps that to an empty list, so `out` returns nothing.
- `POST /lsp/types` body `{"path", "line", "character", "direction?", "preset?"}`. `direction` is `super`, `sub`, or `both` (default). Named tool `lsp_types`.
- Responses use `{ok, stdout, stderr, exit_code}` plus `truncated`. Location lines stay inside the mounted project, at most 40. A missing `clangd` or compile database is `ok: false`, `exit_code: -1`.
- The image installs the `clangd` apt package. `TOOLS_HEALTH_STRICT` treats a missing `clangd` binary as 503.

### `mcp`: Streamable HTTP facts plus a short `ask`

Transport in front of `agent`. File read, ripgrep, and SVN stay on the
official Filesystem / Git servers and a read-only svn-mcp. Official Python SDK
`mcp` 1.x (`FastMCP`), pin `mcp==1.12.4` (last 1.x that stays on
pydantic 2.10.4 / starlette 0.41 with the agent image).

- `GET /health` → `{"status":"ok"}` (compose healthcheck).
- Streamable HTTP endpoint: `POST /mcp` (SDK default path), stateless, JSON responses (`FastMCP(json_response=True, stateless_http=True)`). The process listens on `0.0.0.0:MCP_PORT` inside the container; compose publishes it on loopback.
- Header `X-Workspace` (stdio: `MCP_WORKSPACE`) selects the registered checkout.
- Tool `lookup`: `{query}` → `POST /lookup`. Index excerpts. No chat model.
- Tool `check`: `{action: build|test|compile, path?, preset?, filter?}` → `POST /check`. Bounded log, no opinion.
- Tool `calls`: `{path, line, character, direction?}` → `POST /lsp/calls`.
- Tool `types`: `{path, line, character, direction?}` → `POST /lsp/types`.
- Tool `ask`: `{query, conversation_id?}` → `POST /chat` with `max_reply_tokens` 400 and `tools: false`. The model cites the excerpts. It does not receive the tool schema. Result text is the answer and `Sources:` lines. The conversation id is not in that text. Search, build, test, compile, and symbol lists are the other tools, not `ask`.
- Each tool description tells the caller to paste the text unchanged.
- agent `401` / `503` / any other HTTP error / timeout / unreachable → MCP tool error (`isError`), not a process crash. An empty `query` or an unset `AGENT_BEARER_TOKEN` fails the same way before any call.
- There is no `outline` tool any more (Serena's symbol tools replaced it). The agent route `POST /lsp/outline` stays.
- `MCP_TRANSPORT=stdio`: same tools, no listen port (stdin/stdout JSON-RPC).
- Bind on the host is loopback only (`127.0.0.1:8092`). Do not publish
  `0.0.0.0`. Cursor example: `url: http://127.0.0.1:8092/mcp`.

### `agent` fact routes (no chat model)

Same bearer as `/chat`. `workspace` in the body or the `X-Workspace` header.

- `POST /read` `{"path", "start_line?", "end_line?", "workspace?"}` → `GET /read-file` on `tools` for that checkout; returns `{path, start_line, end_line, total_lines, truncated, text}` (400 when `tools` rejects the path, 503 when it is unreachable, 502 when it answers non-JSON). No MCP tool uses it.
- `POST /lookup` `{"query", "workspace?", "include_candidates?"}` → `{"hits": [{file_path, start_line, end_line, symbol_name, excerpt, source}]}`. `source` is `index` or `working_copy`; `excerpt` is clipped to 400 characters. A registered checkout runs `svn status` or `git status` and re-reads up to 6 dirty C/C++ files through `/read-file` (80 lines; a checkout with no VCS only re-reads the lines of each hit). Those hits come first and replace the indexed excerpt for the same path. `include_candidates` defaults to false. When true, the body also has `retrieval`: `reranker_used`, `candidates` (hybrid prefetch, before the reranker), `reranked` (the reranker window, before the noise filter), and `index_hits` (what a clean tree shows in `hits`, with no working-copy overlay and no excerpt). Each of those rows is `{file_path, start_line, end_line, symbol_name, score}`. MCP `lookup` leaves the flag false.
- `POST /check` `{"action": "build"|"test"|"compile", "preset?", "path?", "filter?", "workspace?"}` → `{action, ok, exit_code, stdout, stderr, truncated}`. `compile` requires `path`. Logs are clipped at 4000 characters.
- `POST /lsp/outline` `{"path", "preset?", "workspace?"}` and `POST /lsp/calls` / `POST /lsp/types` `{"path", "line", "character", "direction?", "preset?", "workspace?"}` forward to `tools` with `X-Project-Root` and return the same clipped `{ok, exit_code, stdout, stderr, truncated}`.

### `tools` endpoints

All requests/responses are JSON. All paths given by the caller are relative
to the mounted project root and are re-validated server-side (never trust the
caller). Every response includes `{"ok": bool, "stdout": str, "stderr": str, "exit_code": int}` plus endpoint-specific fields.

- `GET /health`, `GET /metrics`, and `POST /cancel` (204, no body; kills the in-flight subprocess group of the request id given in `X-Request-Id`) are always there. `agent` calls `/cancel` on `tools` when the user stops a turn.
- `POST /build {"preset": "release"}` -> runs in a fresh `tempfile.mkdtemp()` scratch dir, `cmake --build -j <cpu count>`.
- `POST /build-step {"preset": "release", "target": "app"}` -> `target` is optional (`^[A-Za-z0-9_.:/+-]{1,200}$`, no `..`). Configures once, then `cmake --build <cache> --parallel [--target T]` in the persistent cache, so a second call only rebuilds what changed. The response adds `target`, `build_dir`, and `repair`: `{"kind": "none"}` on success; on failure `{"kind": "compile_file", "path": ...}` when the log cites exactly one `.cpp`, else `{"kind": "build_step"}`. A configure failure is `{"kind": "build_step"}`.
- `POST /test {"preset": "release", "filter": "json_round_trip"}` -> optional `filter` becomes `ctest -R` (`[A-Za-z0-9_.:*?+|\^ -]`, may not start with `-`). Runs `ctest --output-on-failure --no-tests=error`, so a filter that matches nothing is a failure.
- `POST /compile {"path": "src/foo.cpp", "preset": "release"}` -> `.cpp`, `.cc`, `.cxx`, or `.c` only. Configure once per (project root, preset) under `/workspace/scratch/cmake-cache/<root>/<preset>` (`<root>` is derived from the resolved mount, so the default tree and each `/checkouts/<id>` never share a `CMakeCache.txt`), then compile that translation unit (`compile_commands.json`). `/build-step` and `/lsp/*` reuse the same cache. The response adds `path` and `build_dir`. `/workspace/scratch/cmake-cache` is the `cmake-cache` named volume (shared read-only with `serena`), nested in the scratch tmpfs.
- `POST /git/log {"path": "src/foo.cpp", "max_count": 20}` / `/git/blame` / `/git/diff` / `/git/show` -> read-only git; refs allow-listed (`HEAD`, `HEAD~N`, sha, branch/tag); output capped at 200 lines.
- `POST /svn/status {"path": "src/foo.cpp"}` / `/svn/diff` / `/svn/log` -> read-only Subversion. Needs `.svn` at the working-copy root. `svn diff` with no revisions is the uncommitted changelist; `rev_from` and `rev_to` are a pair of `BASE`, `HEAD`, `COMMITTED`, `PREV`, or a revision number. Output capped at 200 lines. `svn log` may contact the repository.
- `POST /git/status` (no body) -> `git status --porcelain=v1 -u` for the agent's working-copy overlay. Not a named tool.
- `POST /git/diff-names {"ref_a", "ref_b"}` -> file names for `agent` `/review`. Not a named tool.
- `POST /lint/clang-tidy {"paths": ["src/main.cpp"]}` -> `--config-file` is passed only when `TOOLS_CLANG_TIDY_CONFIG_FILE` exists.
- `POST /format/check {"paths": [...]}` -> style from `TOOLS_CLANG_FORMAT_STYLE_FILE` when it exists.
- `POST /static-analysis {"paths": [...]}`
- `GET /read-file?path=src/main.cpp&start_line=1&end_line=400` -> window optional, 1-based inclusive; **at most 400 lines per call**; response adds `start_line`, `end_line`, `total_lines`, `truncated`. Byte cap 256 KiB (413); a directory or special file is 400, a missing file 404. Used by `agent` (`/read`, working-copy overlay); not model-facing (Serena `read_file`).
- Every route reads `X-Project-Root` (a direct child of `/checkouts`, else 400); absent means the default tree.

**Bounded outputs.** Every HTTP observation returned to the LLM is capped
and reports `truncated: true` plus a `stderr` hint telling the model how to
narrow or page. Never add a read-type endpoint without an equivalent cap.
`GET /read-file` caps a window at 400 lines. Git and Subversion tools cap
at 200 lines.
Subversion revisions are `BASE`, `HEAD`, `COMMITTED`, `PREV`, or a number,
and `rev_from` / `rev_to` are set together. The Serena tools are bounded
by the sidecar itself (`default_max_tool_answer_chars: 150000` in
`config/serena/serena_config.yml`; the agent refuses a model-supplied
`max_answer_chars` above 262144).

`GET /metrics` (Prometheus text) has no authentication on `agent` and
`tools`. On `tools` it is internal; on `agent` it is also reachable through
`ui-proxy` on `127.0.0.1:8090`. The optional `obs` profile scrapes those endpoints plus `llm`,
`embedder`, `reranker`, and `qdrant`. Business series and the Grafana
dashboards are documented in `docs/guides/observability.md`. The chat-model
scrape stem is the basename of `LLM_MODEL_PATH`. `GET /health` on `tools` lists
missing binaries; with `TOOLS_HEALTH_STRICT=1` a missing binary is HTTP 503.

### Named tools (what the chat model can call)

The model-facing schema has **39 names**, merged by `services/agent/tools_schema.py` into `TOOLS_OPENAI_SCHEMA`:

- **16 HTTP names** from `TOOL_SPECS` (`services/common/tool_models.py`), each bound to a `tools` route: `build`, `build_step`, `compile_file`, `format_check`, `git_blame`, `git_diff`, `git_log`, `git_show`, `lint_clang_tidy`, `lsp_calls`, `lsp_types`, `static_analysis`, `svn_diff`, `svn_log`, `svn_status`, `test`. `tests/common/test_tool_models.py` checks them against the OpenAPI of `tools`. The agent maps them through `TOOL_ENDPOINT_MAP` (HTTP only).
- **23 Serena names** from `services/agent/serena_named_tools.py`, forwarded to the sidecar: reads `find_declaration`, `find_file`, `find_implementations`, `find_referencing_symbols`, `find_symbol`, `get_diagnostics_for_file`, `get_diagnostics_for_symbol`, `get_symbols_overview`, `list_dir`, `read_file`, `restart_language_server`, `search_for_pattern`; and 11 writes `create_text_file`, `delete_lines`, `insert_after_symbol`, `insert_at_line`, `insert_before_symbol`, `rename_symbol`, `replace_content`, `replace_in_files`, `replace_lines`, `replace_symbol_body`, `safe_delete_symbol`.

`begin_plan`, `edit`, `apply`, `lsp_definition`, `lsp_references`, `lsp_hover`, `lsp_symbols`, `lsp_outline`, `list_files`, and `search` are no longer names, and `tools` no longer routes `/search`, `/list-files`, `/lsp/definition`, `/lsp/references`, `/lsp/hover`, or `/lsp/symbols`. A successful Serena write opens the **editing** phase (`services/agent/edit_policy.py`, one `ContextVar` per turn): while it is on, `build`, `test`, and `compile_file` are refused (`ok: false`, `exit_code: 2`) until a `build_step` succeeds. Details: `docs/concepts/tool-calling.md`.

Every tool result is normalized to `{ok, stdout, stderr, exit_code}`; `exit_code` is `-1` for a transport failure and `2` for an agent-side refusal or a `400` from `tools`.

### `serena` sidecar (agent -> Serena MCP)

- Service `serena`, image `ghcr.io/oraios/serena:2.1.0`, started with `--transport streamable-http --host 0.0.0.0 --port 9121 --agent-interface tools --language-backend LSP --project /workspace/app --enable-web-dashboard false`. `tools-net` only, no published port, no authentication. Config `config/serena/serena_config.yml` is copied in at start: it ignores `.git`, `.svn`, `.env`, `.env.*` for writes, excludes `execute_shell_command`, `serena_repl`, `open_dashboard`, and every `jet_brains_*` tool, and keeps memories under `/tmp`.
- Mounts: the checkout read-write (`/workspace/app`), extra checkouts read-write at `/checkouts/<id>` (generated by `scripts/host/workspaces.py`), the `cmake-cache` volume read-only. The root filesystem is not read-only.
- Transport (`services/agent/serena_transport.py`): `POST <SERENA_BASE_URL>/mcp` (code default `http://serena:9121`). JSON-RPC `initialize` (protocol `2025-11-25`), `notifications/initialized`, then `tools/call`; the session id travels in `mcp-session-id`. Replies may be JSON or SSE. A session error or `404` reopens the session once. `X-Request-Id` is forwarded. Timeout `AGENT_REQUEST_TIMEOUT_SECONDS`.
- Policy (`services/agent/serena_policy.py`) runs before the call; a refusal is `exit_code: 2` and nothing reaches Serena. Paths in `relative_path`, `reference_file`, `paths_include_glob`, and `paths_exclude_glob` must be relative, free of null bytes, `..`, `:`, `.git`, `.svn`, and any `.env*` segment. For a registered checkout, the resolved path must stay under that checkout. `max_answer_chars` above 262144 is refused.
- Cancel: `agent` posts `/cancel` to `tools` only. A Serena call in flight is abandoned client-side and keeps running in the sidecar.
- Known limitation (code reading, not exercised live): the sidecar is started on `--project /workspace/app` and the agent does not send an activate-project call, so with a registered `workspace` the paths are validated against `/checkouts/<id>` but Serena itself still resolves them in the default tree.
- Decision record: `docs/adr/0001-serena-sidecar.md`.

### `embedder`: `POST /v1/embeddings` (OpenAI-compatible shape)

Callers MUST prefix input text: `"search_document: " + chunk_text` when
indexing, `"search_query: " + user_query` when querying (nomic-embed-text-v1.5
convention - silently wrong prefixing degrades retrieval without erroring).

### Qdrant collection `code_chunks`

Two named vectors on every point:

| Name | Kind | Config |
|---|---|---|
| `dense` | float, size 768 | Cosine (nomic-embed-text-v1.5) |
| `bm25` | sparse | `modifier: idf`. Indices are `crc32(token) & 0x7fffffff` from `services/common/sparse_text.py` (same function at index time and query time). Values are term frequencies. |

A collection that exists without both names is **dropped and recreated** by the indexer (`ensure_collection`). Re-run `deploy.py index` after this change; the old unnamed 768-d collection cannot be queried in hybrid mode.

Query (`agent`): `POST /collections/code_chunks/points/query` with two prefetches (`dense` limit 20, `bm25` limit 20) and `query.fusion = rrf`. If that call returns 400 (collection not yet rebuilt), fall back to `POST /points/search` on the named `dense` vector only.

When `RERANKER_BASE_URL` is set, the fusion `limit` is 20. `agent` then `POST /v1/rerank` with `{query, documents, top_n: 5}` and keeps the returned order (`results[].index`, `relevance_score`). A transport error, a non-2xx, or a body without `results` keeps the RRF order truncated to 5. When `RERANKER_BASE_URL` is empty, the fusion `limit` is 5 and no rerank call is made. The reranker is not a hard dependency of `agent`: a missing GGUF leaves retrieval on RRF.

Payload schema per point:
```json
{
  "file_path": "src/main.cpp",
  "symbol_name": "main",
  "start_line": 12,
  "end_line": 40,
  "language": "cpp",
  "git_blob_hash": "<sha1 of blob content, for content-addressed dedupe>",
  "text": "<the chunk source text>",
  "checkouts": ["default"]
}
```
`language` is `cpp` for tree-sitter chunks and one of `markdown`, `cmake`,
`yaml`, `json`, `toml`, `ini`, `shell`, `containerfile`, `text` for the
non-C++ files chunked by `text_chunker.py` with `tree-sitter-language-pack`
grammars (Markdown sections, top-level CMake commands, top-level YAML/JSON/
TOML keys; 60-line overlapping windows for formats without a grammar).
`symbol_name` is then the heading / target / key name (several names joined
with `, ` when small units are packed together), `<preamble>`, or
`<file-level>` / `<lines a-b>` for windowed content.
`file_path` is relative to `INDEXER_SOURCE_ROOT` (`/workspace/app`), i.e. it
never has an `app/` prefix - this is exactly the same root the `tools`
service resolves its own paths against (`TOOLS_PROJECT_ROOT`, same default),
so a `file_path` value from a Qdrant search result can be passed straight
into a `tools` call (e.g. `/read-file?path=src/main.cpp`) with no
adjustment. Don't reintroduce an `app/` prefix in either component.
Point ID is derived deterministically from `hash(file_path + git_blob_hash + symbol_name + start_line)` so unchanged files across branches/checkouts share the exact same points (embedded once), while divergent versions of a file coexist in the collection and are filtered by `checkouts` (`INDEXER_CHECKOUT_ID`).

## Host prerequisites

Podman or Docker Compose runs the containers. The only command to bring the
stack up is `python scripts/deploy.py up`.

CONTAINER_RUNTIME=auto

`auto` uses the first of `podman compose` and `docker compose` that works.
On Windows, a Podman CLI with no machine makes that command continue inside
the WSL distro `local-llm`, unless Docker was selected. `scripts/host/` holds
the other manual scripts (`install_deps.py` for an optional host virtualenv,
`fix_permissions.py` for a native Linux filesystem). `scripts/container/`
holds `fetch_models.py` and the eval scripts; Compose starts them.
`containers/devtools/Containerfile` carries the C++ toolchain and the service
environments, and backs both `.devcontainer/devcontainer.json` and the `dev`
compose profile.
