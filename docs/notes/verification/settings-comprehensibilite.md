# Settings legibility

> **Status**: informational, recommendations not applied (none of the proposed label fixes is made in `.env.example`). Written on 2026-11-10; the inventory was extended for the `serena` sidecar (`SERENA_*` keys, `config/serena/`). · **Last verified**: 2026-11-12

Research note of 2026-11-10. The guides in `docs/` describe how to use the stack. The code, Compose and the contracts own the behavior. When a guide and the code diverge, both are cited separately.

An earlier architecture review (HTML report `%LOCALAPPDATA%\Temp\architecture-review-20261110-1000.html`, conversation of 2026-11-10) raised a neighboring question: model choices should fit in a single `.env`, without some forty places to edit. That report is a secondary source. The facts below are re-read in the files it names. Where a sentence of the report does not hold, this is stated.

## Question

Can the settings in `.env` or elsewhere be made more user friendly and easier to understand?

And, against that review: would clearer text be enough, or does the `.env` lie, with a key that is present but does not drive the behavior?

## Short answer

Yes for the keys the process really reads: the example file is already grouped, and it tells the operator to fill in only three, but there remain obscure names (`NGL`, `28544`, `999`), ports that are not the listening port, a Grafana secret silently rewritten, and `DEPLOY_WSL_DISTRO` documented next to `.env` although the script reads it only from the process environment. This is fixed by labels, comments, and a list of what is read.

That is not enough for models. The `llm` container is the covered case: `LLM_MODEL_PATH` chooses the file, and the agent sends the name of that file. The host server `evo-x5` ignores most chat keys of `.env` and publishes the alias `mistral-small-4`, while the agent sends the file stem. The embedder loads the file of `EMBEDDER_MODEL_PATH`, then the agent and the index still send the fixed name `nomic-embed-text-v1.5`, the dimension 768 and the nomic prefixes, all written in the code. The download is a catalog (`ModelSpec`, nickname), not a key. This is not forty files. It is a few keys that look like they set everything, and choices that are not settings.

## Inventory

### `.env` at the root

Who edits it: the operator. The first `up` copies `.env.example` if `.env` is missing, and replaces the example token. `docs/getting-started/installation.md`, "2. Start the stack":

> The first `up` copies `.env.example` to `.env` if needed and writes a random `AGENT_BEARER_TOKEN` into it. [...] Every other value in `.env.example` has a sensible default. See `CONTRACTS.md` for what each one does.

The header of `.env.example` tightens this further:

> You set three values: CONTAINER_RUNTIME, CODEBASE_PATH, AGENT_BEARER_TOKEN.
> `python scripts/deploy.py gpu-profile <name>` rewrites the GPU block.
> [...] Ports, *_BASE_URL, and *_PROJECT_ROOT are the wiring between containers. Leave them unless a profile just wrote them.

Where it is documented: `.env.example` (comments), `CONTRACTS.md` section "Environment variables", `README.md` "Quick start", `docs/getting-started/prerequisites.md` and `installation.md`, `docs/notes/contracts/compose-services.md`, `docs/guides/troubleshooting-gpu.md`, `docs/guides/swapping-the-model.md`.

Where it is read: `scripts/host/deploy/envfile.py`, `_parse_env_file`, for the deployment (runtime, path, profile). Compose interpolates `${...}` in `compose.yaml` and passes to the container only the keys listed in `environment:`.

`CONTRACTS.md`, end of that section:

> Every service reads its config exclusively from env vars with these exact names, no hardcoded hosts/ports/paths in source.

This sentence describes a contract. The code below has constants and fixed ports. `tests/scripts/test_contracts_sync.py` checks that the `KEY=` assignments of `CONTRACTS.md` are in `.env.example`, and that a handful of keys appear in `compose.yaml`. It does not check that every line of the example reaches a process.

Keys the operator is expected to touch, and that are read:

| Key | Reader |
| --- | --- |
| `CONTAINER_RUNTIME` | `scripts/host/deploy/runtime.py`, `_requested_runtime`: process, else the `.env` file, else `auto` |
| `CODEBASE_PATH` | `envfile.py` (the directory must exist) and the `compose.yaml` volumes |
| `AGENT_BEARER_TOKEN` | `compose.yaml` to `agent`, `mcp`, `app`; `services/agent/main.py` |
| `GRAFANA_ADMIN_PASSWORD` | `config/compose/observability.yaml`, `GF_SECURITY_ADMIN_PASSWORD`, required (`?set ...`) |
| GPU block (`LLM_MODEL_PATH`, `LLM_CTX_SIZE`, `LLM_NGL`, caches, margins, `EMBEDDER_*`, `RERANKER_NGL`, and on `evo-x5` `LLM_SERVE`, `LLM_BASE_URL`, `AGENT_CONTEXT_BUDGET_TOKENS`) | `gpu-profile` copies the file from `config/profiles/` into `.env` (`scripts/host/deploy/gpu.py`, `apply_profile_text`). The `llm` container reads them in `containers/llm/entrypoint.sh`. The agent reads `LLM_CTX_SIZE` and `AGENT_CONTEXT_BUDGET_TOKENS` |
| `LLM_THREADS`, `LLM_THREADS_BATCH`, `LLM_NUMA`, and the same for the embedder | `llm` and `embedder` entrypoints, only if the value is not empty |
| `TRAIN_*` of the QLoRA block | `trainer` service in `compose.yaml`, profile `train` |
| empty `RERANKER_BASE_URL` | `services/agent/rag.py` skips the pass |
| `AGENT_REFLECT`, `AGENT_GATE_CLASSIFY`, loop caps, timeouts, `LOG_LEVEL`, `TOOLS_CMAKE_PRESET`, `TOOLS_HEALTH_STRICT`, `QDRANT_COLLECTION`, `INDEXER_CHECKOUT_ID`, `MCP_TRANSPORT` | named services, via Compose |

`GLOSSARY.md` defines none of these names. It defines **Checkout**, **Named tool**, **Agent**, **Editing**, **Index**, **Sidecar**.

### Profiles `config/profiles/*.env`

Seven files: `16g`, `24g`, `24g-30b`, `48g`, `48g-30b`, `48g-32b`, `evo-x5`. Who edits them: nobody, day to day. `docs/guides/swapping-the-model.md`, "Cases":

> `config/profiles/` holds one file per case. `python scripts/deploy.py gpu-profile <name>` writes it into `.env`.

`scripts/host/deploy/cli.py` restricts the name to `GPU_PROFILES`. The command is not a flag of `up`.

Read exception: `scripts/host/evo_x5_serve.py` opens `config/profiles/evo-x5.env` when the host binary starts (`PROFILE`, `profile_map`). `scripts/host/deploy/host_llm.py`, `_start_host_llm`, passes it only `--port`, taken from `LLM_PORT` in `.env`. The profile file is therefore a runtime source for this one case, in addition to being the template copied into `.env`.

### `atlassian.env`

Who edits it: the operator, four secrets plus two SSL flags. `atlassian.env.example`: copy to `atlassian.env`, do not commit it. Jira and Confluence Server, not Cloud. `READ_ONLY_MODE=true`. `ENABLED_TOOLS` lists four tools.

Read by the editor-side server `uvx mcp-atlassian`, not by a Compose service. `scripts/host/workspaces.py`, `ATLASSIAN_MCP_SERVER`, passes `--env-file atlassian.env`. `.cursor/mcp.json` does the same. `scripts/host/export_repo.py` omits this file, unless `--include-env`.

### Flags of `python scripts/deploy.py`

They last for the duration of the command. `scripts/host/deploy/cli.py`: `up` (`--skip-models`, `--skip-index`, `--timeout`), `gpu-profile <name>`, `fetch-extra <name>`, `workspace <path>`, `train`, `ask`, `doctor`, `rotate-token`, `expose --allow`. The durable model choice is `gpu-profile`, which writes `.env`. `fetch-extra` downloads an optional GGUF; it does not modify `.env`.

### Other surfaces

| Surface | Who edits it | Role |
| --- | --- | --- |
| `state/workspaces.json` | the `workspace` command (`scripts/host/workspaces.py`) | registry of checkouts. `CONTRACTS.md`: an unregistered path is refused |
| `state/compose.workspaces.yaml` | the same command | generated mounts |
| `.vscode/mcp.json` | `install_vscode_mcp`, `X-Workspace` header | editor client, unversioned file |
| `.cursor/mcp.json` | versioned | URL of `pumba` and launch of `atlassian` |
| `~/.continue/config.yaml` | the operator, outside the repository | `docs/guides/vscode.md`: the model identifier is written there by hand |
| `config/compose/*.yaml`, `compose.yaml` | the repository | overlays chosen by `deploy.py`. The GPU guide says to set `LLM_GPU_ARG=0` in `.env` rather than edit Compose by hand |
| `config/observability/` | the repository, mounted as is | Prometheus and Grafana dashboards. The operator supplies the password, not the YAML |
| `config/serena/serena_config.yml` | the repository | configuration of the `serena` sidecar: excluded tools (shell, REPL, JetBrains), ignored paths (`.git`, `.svn`, `.env`), modes. `compose.yaml` mounts it read-only and copies it to a writable path at startup. The image tag `ghcr.io/oraios/serena:2.1.0` is written in `compose.yaml`, not in `.env` |
| `mkdocs.yml`, `pyproject.toml`, Cursor rules, CMake presets of `sample/` and `app/` | the repository | not the stack startup. `TOOLS_CMAKE_PRESET` chooses `release` or `debug` |
| `.env.test` | the CI | see below. Not an operator file |
| variables set in `tests/**/conftest.py` | the tests | dummy tokens and URLs. Not for the operator |

`docs/adr/0001-serena-sidecar.md` decides that the index stays Qdrant. It does not decide the shape of the keys.

## Legibility, with the files

### What the header says, and what the file contains

The "three values" header is right for a first `up` on the default model: the script invents the token, and the GPU profile is applied by a command. It falls short for the rest. `GRAFANA_ADMIN_PASSWORD` is a second secret, required by the `obs` overlay. `LLM_THREADS` is a setting that the Xeon comment asks to fill in. `atlassian.env` is a fourth surface, absent from this header.

The sections `--- agent ---`, `--- llm ---`, `--- trainer ---` exist. The agent keys nearly all have a comment. The trainer block has none under the heading "QLoRA; profile `train` only". `docs/guides/fine-tuning-the-codebase.md` names `TRAIN_MAX_STEPS` (default 200) and `TRAIN_LORA_RANK`, then "etc.". Rank, alpha and sequence length remain without a sentence in the example.

Two boolean dialects coexist. `.env.example` uses `0` and `1` (`AGENT_REFLECT`, `LLM_ROUTER`, `TOOLS_HEALTH_STRICT`). `atlassian.env.example` uses `true` (`READ_ONLY_MODE`, `JIRA_SSL_VERIFY`).

Units are in the name when it is `_SECONDS`, `_TOKENS`, `_CHARS`, `_MIB`. `LLM_CTX_SIZE` does not say so in its name; the comment above it speaks of a token window. `LLM_NGL` neither. The comment in `.env.example` says "GPU layer offload" and "999 (every layer on the GPU)". The profiles `24g.env`, `48g.env` and the variants set `LLM_NGL=999` without repeating that sentence. `24g-30b.env` explains its numbers (weights ~20.2 GiB, KV ~1.5 GiB, margin 512). `16g.env` says `16 Go (RTX 4080)` (16 GB) and lists the keys. `docs/guides/troubleshooting-gpu.md` translates `16g` as "~16376 MiB". The same neighborhood mixes GB, GiB and MiB.

`AGENT_CONTEXT_BUDGET_TOKENS=28544` is the result of 32768 − 3200 − 1024. `services/agent/token_budget.py` carries both subtractions, and the comment of the `evo-x5.env` profile cites them. The example shows 28544 without this subtraction.

The language of the copied file is English. `evo-x5.env` starts in French (`Le 119B ne passe pas par l'image CUDA`, meaning "The 119B does not go through the CUDA image") and continues in English. `GLOSSARY.md` asks to avoid "workspace" for a checkout. The command, the file and the header are called `workspace`, `state/workspaces.json` and `X-Workspace`.

### Two lines, one placeholder

`.env.example` gives the same value to `AGENT_BEARER_TOKEN` and to `GRAFANA_ADMIN_PASSWORD`: `changeme-generate-a-real-secret`. `scripts/host/deploy/__init__.py` names this string `PLACEHOLDER_TOKEN`. `envfile.py`, `_ensure_env_file`, does a `content.replace` of every occurrence, then prints only that the agent token was written. On the first `up`, the Grafana password becomes the same secret, without a sentence. `docs/guides/observability.md` says that Grafana reads this password once, when the `grafana-data` volume is created.

### Defaults that are not those of the example

The container, when Compose injects the key, follows `.env`. The Python default is seen only if the variable is absent. These defaults do not match the example:

| Key | `.env.example` and Compose | Default in the service |
| --- | --- | --- |
| `AGENT_CONVERSATION_TTL_SECONDS` | 28800 (`compose.yaml` `:-28800`) | `services/agent/conversations.py`: `"3600"` |
| `AGENT_CONVERSATION_MAX_TURNS` | 24 | the same file: `"8"` |
| `AGENT_TOOL_LOOP_MAX_ITERATIONS` | 48, and Compose has no `:-` | `services/agent/tool_loop.py`: `"8"` |
| `TOOLS_HEALTH_STRICT` | 1 (`:-1`) | `services/tools/main.py`: `"0"` |

`containers/embedder/entrypoint.sh` comments "the build arg defaults to GPU=1 per CONTRACTS.md (`EMBEDDER_GPU_ARG=1`)". `CONTRACTS.md` and `.env.example` set `EMBEDDER_GPU_ARG=0`. `compose.yaml` interpolates `${EMBEDDER_GPU_ARG:-0}`.

`containers/llm/entrypoint.sh`: if `LLM_NGL` is empty, a GPU image takes `999`, a CPU image takes `0`. The `:-auto` further down no longer applies. Deleting the line `LLM_NGL=auto` from the example does not keep `auto`. `scripts/host/deploy/gpu.py`, `_env_gpu_build_requested`, treats an absent `EMBEDDER_GPU_ARG` as `1`. Compose, for its part, falls back to `0`.

### Keys present that do not change the listening port

`compose.yaml` publishes `127.0.0.1:${LLM_PORT:-8080}:8080` and `127.0.0.1:${AGENT_PORT:-8090}:8090`. The container port is fixed. `LLM_PORT` is not in the `environment:` of `llm`. `AGENT_PORT` is not in the `environment:` of `agent`. `containers/agent/Containerfile` first says that `AGENT_PORT` "is read at runtime so it stays configurable", then:

> Port is hardcoded to match CONTRACTS.md's AGENT_PORT default (8090); shells don't expand $AGENT_PORT in exec-form CMD

and the `CMD` launches uvicorn on `8090`.

`EMBEDDER_PORT` and `TOOLS_PORT` appear in `.env.example`. No `${EMBEDDER_PORT}` or `${TOOLS_PORT}` in `compose.yaml`. The embedder entrypoint falls back to 8081. The `tools` image sets `ENV TOOLS_PORT=8091`. `PATCH_PORT` is in the example; the `patch` service hardcodes `PATCH_PORT=8093`. `MCP_PORT`, for its part, is passed to the container and to both sides of the mapping. `SERENA_BASE_URL` is in the example and in `CONTRACTS.md`, but `compose.yaml` does not inject it into `agent`: the default `http://serena:9121` of `services/agent/serena_transport.py` applies, and a `SERENA_BASE_URL` modified in `.env` changes nothing for the `agent` container. `SERENA_HEALTH_STRICT`, for its part, is read by the healthcheck of the `serena` service.

`RERANKER_PORT` is passed to the reranker under the name `EMBEDDER_PORT`. The healthcheck of this service hardcodes `localhost:8082`. `RERANKER_BASE_URL` is a third copy of the same address (`http://reranker:8082`).

### Documented keys that `.env` does not carry

`docs/getting-started/prerequisites.md`:

> Set `DEPLOY_WSL_DISTRO` to use another distro.

`runtime.py`, `_activate_runtime`, reads `os.environ.get("DEPLOY_WSL_DISTRO", ...)`. The `.env` file is consulted, in this function, only for `CONTAINER_RUNTIME`. A line `DEPLOY_WSL_DISTRO=` in `.env` does not change the WSL relay. `DEPLOY_SKIP_WSL` is of the same kind: process environment, absent from the example.

`GRAFANA_ADMIN_USER` is named by `docs/guides/observability.md` and is `admin` via `${GRAFANA_ADMIN_USER:-admin}` in the overlay. It has no line in `.env.example`.

`CONTAINER_RUNTIME` is in `.env.example` and absent from the assignment block of `CONTRACTS.md`.

### `.env.test` says it is a copy

`.env.test`, header:

> A full, standalone copy of .env.example (not a partial override) [...] keep every value in sync with .env.example except the ones commented below.

The file omits, among others, `LLM_CTX_SIZE`, `LLM_NGL`, `AGENT_MAX_CONCURRENT`, `AGENT_CONTEXT_BUDGET_TOKENS`, `INDEXER_CHECKOUT_ID`, `TOOLS_HEALTH_STRICT`, `SERENA_BASE_URL`, `SERENA_HEALTH_STRICT`, `PATCH_PORT`, `PATCH_BASE_URL`, the `TRAIN_*` block and `GRAFANA_ADMIN_PASSWORD`. It is not the announced copy. The commented differences (0.5B model, `LLM_GPU_ARG=0`, short timeouts, `CODEBASE_PATH=./app`, empty `RERANKER_BASE_URL`) are, for their part, explained. Reserved for the CI (`docs/guides/testing.md`).

### Two names for the training set

`.env.example`, `compose.yaml` and `services/trainer/train.py` use `TRAIN_DATASET_DIR` (default `/models/training`). `services/trainer/build_dataset.py` takes the default of `--output-dir` from `TRAIN_DATASET_OUT`, same path, other name. This key is neither in the example nor in Compose. `cmd_train` (`scripts/host/deploy/ops.py`) runs `build_dataset.py` in the `devtools` service, which does not have the `environment:` block of the trainer.

## Parser

`scripts/host/deploy/envfile.py`, `_parse_env_file`:

> Trivial `KEY=VALUE` parser for `.env`-style files, skips blank lines and `#` comments. Not a full dotenv implementation (no quoting, no variable expansion) because compose.yaml itself doesn't need one; this only has to read back values `podman compose` would substitute.

The code: a line without `=` is ignored; `key.strip()` and `value.strip()`; no removal of quotes; no removal of a trailing `#` at the end of a line; no `export` prefix; a value fits on one line. `tests/scripts/test_doctor.py`, `test_parse_env_file_parses_key_value_pairs`, covers the full-line comment, the blank line and the line without `=`. It does not cover quotes.

`tests/scripts/test_contracts_sync.py`, `_assignment_map`, cuts at the first `#`. The deployment parser does not. A line copied from the block of `CONTRACTS.md`, where the comment is on the same line (`CODEBASE_PATH=./sample   # host dir...`), would give `deploy.py` a value that contains the comment. The comments of `.env.example` are on their own line, so the example as it stands passes.

`evo_x5_serve.py`, `profile_map`, is the same minimal parser on the profile file.

## Does the `.env` lie about models?

The review asked for a single `.env`. The count of places is not forty. The chat container is local: one key. The host and the embedder are not. Three candidates from the review, re-read.

### Chat container: the key covers the file and the name

`containers/llm/entrypoint.sh` takes `LLM_MODEL_PATH`, makes it the section name of the router (`basename` without `.gguf`), and passes `-c`, `--parallel`, the two `--cache-type-*`, `--fit-target`, `--cache-ram`, `-ngl`, `-t`, `-tb`, `--numa`, `--lora` when the variables are there. `LLM_ROUTER=1` starts without `-m`.

`services/agent/llm_client.py`, `active_model`, is the stem of `LLM_MODEL_PATH` (suffix `.gguf` removed), or `local` if the path is empty. `chat_completion` and `chat_completion_stream` send this name. The callers do not pass another model: `tool_loop.py` (the loop), `gate.py`, `reflect.py`, `review.py`, and `_model_summary` in `token_budget.py`. `model_catalog.py` can replace this name after a load from the page. `docs/getting-started/first-run.md`: `LLM_MODEL_PATH` is what comes back after a recreation of `llm`, not the last click.

This candidate from the review is verified for the container: the locality of the request name is `active_model()`. The flags of the sidecar are the keys of `.env`, read by the entrypoint. `docs/guides/swapping-the-model.md` describes this gesture in three steps, including the addition of a `ModelSpec` if the file must be downloaded by the script.

### Candidate 1, an embedding identity. Verified, with a gap in the report

The sidecar loads the file. `containers/embedder/entrypoint.sh` launches `llama-server -m "$EMBEDDER_MODEL_PATH"`. The default of the script is the same GGUF as `.env.example`: `/models/nomic-embed-text-v1.5.Q8_0.gguf`.

The agent and the index do not send this path. Both clients set the same constant:

`services/agent/llm_client.py`:

> EMBEDDING_MODEL = "nomic-embed-text-v1.5"

`embed_query` passes `model=EMBEDDING_MODEL`. `services/indexer/embeddings_client.py` has the same constant and the same argument in `embeddings.create`. `compose.yaml`, service `agent`, nonetheless injects `EMBEDDER_MODEL_PATH=${EMBEDDER_MODEL_PATH}`.

The report says that this key, in the agent environment, is ignored. It is not entirely. `services/agent/model_catalog.py`, `_excluded_names`, reads `EMBEDDER_MODEL_PATH` and `RERANKER_MODEL_PATH` to hide these files from the list of chat models. The embedding call does not read it. The sentence of the report that says "ignored" diverges on this point. The problem it targets, for its part, is in the code: changing the path loads another file and leaves the request name, the dimension, the prefixes and the cut unchanged.

These four are not keys:

- Request name: the two `EMBEDDING_MODEL` above.
- Dimension: `services/common/qdrant_schema.py`, `VECTOR_SIZE = 768`. `services/indexer/vector_store.py` imports it and re-exports it; it does not assign it. The guide `docs/guides/swapping-the-model.md` still says to modify `VECTOR_SIZE` in `services/indexer/vector_store.py`. The owner is `qdrant_schema.py`. The report is right to flag the guide. It does not claim that the constant is defined in `vector_store.py`.
- Prefixes: the same module, `DOCUMENT_PREFIX = "search_document: "` and `QUERY_PREFIX = "search_query: "`. The index prefixes at indexing time (`embeddings_client.py`). The agent prefixes at question time (`rag.py`).
- Cut: `services/indexer/text_chunker.py`, `MAX_EMBED_CHARS = 1800`, comment "nomic-embed-text-v1.5 rejects an input past 2048 tokens". `services/indexer/chunker.py`, `_fit_embedder_context`, refers to this cut. No environment variable.

`vector_store.py`, `ensure_collection`, recreates the collection only if the `dense` and `bm25` names are missing (`reason="missing_hybrid_schema"`). If both names are there, the size 768 is not re-read. The solution sentence of the report ("a dimension change still rewrites the Index") is not the current behavior. The guide, for its part, asks to destroy the collection by hand. ADR 0001 keeps Qdrant; nothing here reopens that choice.

These are both problems at once. The key looks like the identity, and the guide calls it "load-bearing" with the dimension: legibility, the file promises more than the embedding process delivers. The dimension, the prefixes, the request name and the cut have no key: the behavior lives in the code. One more comment on `EMBEDDER_MODEL_PATH` would state the limit. It would not create the missing setting.

The embedder entrypoint also fixes, outside `.env`, `-c` 8192 and a batch of 8192 when `EMBEDDER_CTX` is empty. The reranker, in `compose.yaml`, hardcodes `EMBEDDER_CTX=2048`. The index cut at 2048 tokens and the server window at 8192 are two numbers of the code, not two lines of the example.

### Candidate 2, a single argv for the chat sidecar. Verified

`LLM_SERVE=host` is read. `host_llm.py`, `_host_llm_selected`, looks at `.env`. `_start_host_llm` launches `evo_x5_serve.py --run --port <LLM_PORT>`.

`serve_argv` does not assemble the keys. It sets `--alias mistral-small-4` (`ALIAS`), `-ngl 999`, `-t` and `-tb` from the constant `THREADS = "16"`, `--cache-type-k q8_0`, `--cache-type-v q8_0`, `--temp` 0.1 or 0.7 depending on `--reasoning`. The docstring says that `--fit` is omitted. `--parallel`, `--lora`, `--numa` and the router are not there. `LLM_PARALLEL=1` is in `config/profiles/evo-x5.env` and is not read by this builder. Likewise `LLM_NGL`, `LLM_CACHE_TYPE_K`, `LLM_CACHE_TYPE_V`, `LLM_THREADS`, `LLM_THREADS_BATCH`, `LLM_GPU_MARGIN_MIB`, `LLM_CACHE_RAM_MIB`. The values written today coincide with the constants (999, q8_0, 16). Changing the line of `.env` or of the profile does not change the argument.

The path and the window come from the profile file, not from `.env`. `context_tokens` refuses an `LLM_CTX_SIZE` absent from `config/profiles/evo-x5.env`. `host_weight` requires that the `LLM_MODEL_PATH` of this file starts with `/models/`. `host_llm.py`, `_host_llm_weights_missing`, checks the `LLM_MODEL_PATH` of `.env`. After a `gpu-profile`, both files carry the same lines. Editing them separately separates them: the agent receives `.env` (Compose passes it `LLM_MODEL_PATH`, `LLM_CTX_SIZE`, `AGENT_CONTEXT_BUDGET_TOKENS`), the host binary re-reads the profile.

The published name and the sent name differ. The alias is `mistral-small-4`. The file stem of the profile is `Mistral-Small-4-119B-2603-UD-Q5_K_M-00001-of-00003`. `active_model()` sends this stem. `docs/guides/vscode.md` and `docs/getting-started/first-run.md` say that the identifier, on the EVO-X5, is `mistral-small-4`. `CONTRACTS.md`, Models table, says the same for the host alias. This repository does not say what the `llama-server` binary answers if the request name is not the alias. Both strings are written; acceptance by the binary is not tested here.

The report says "half the chat keys". The list above is the fact. The shape is right: the flags that the container entrypoint reads are not those that `serve_argv` reads. This candidate is a legibility problem: the keys are in `.env` and in the profile, and this path does not read them. The fixed alias is, in addition, a choice without a key.

### Candidate 3, the catalog is not `.env`. Verified

`scripts/container/fetch_models.py`, `ModelSpec`, carries repository, file name and SHA256. `CHAT_MODEL`, `EMBED_MODEL`, `RERANK_MODEL` are the default files. `EXTRA_MODELS` carries the optional GGUFs, keys `coder-30b-q5`, `coder-30b-q8`, `coder-32b-q5`, `mistral-small-4`. The comment says that `deploy.py up` does not download them.

`scripts/host/deploy/__init__.py`, `PROFILE_EXTRA`, links a profile name to this nickname: `24g-30b` → `coder-30b-q5`, `48g-30b` → `coder-30b-q8`, `48g-32b` → `coder-32b-q5`, `evo-x5` → `mistral-small-4`. `fetch-extra` takes the profile name (`cli.py`) and resolves the nickname.

The served path is a third string, in the profile. Today they designate the same file. Example: `24g-30b.env` has `LLM_MODEL_PATH=/models/Qwen3-Coder-30B-A3B-Instruct-Q5_K_M.gguf`, and `EXTRA_MODELS["coder-30b-q5"].filename` is `Qwen3-Coder-30B-A3B-Instruct-Q5_K_M.gguf`. Nothing compares the three when `.env` is written. `cmd_gpu_profile` checks whether the profile file is already under `models/`, and otherwise suggests `fetch-extra <profile>`. The checksums stay in `ModelSpec.sha256`. Putting them in `.env` is not what the code does, and the report is right to leave them in the catalog.

This is an architecture problem in the sense of the review: three names can diverge, and there is no single key. It is not a lying key in the file the operator copies. The nickname does not appear in it. A label in `.env.example` can say "the download is `fetch-extra`, the served file is `LLM_MODEL_PATH`". It does not merge the catalog and the path.

The default 14B has the same shape, smaller: `CHAT_MODEL.filename` in `fetch_models.py`, `LLM_MODEL_PATH` in `.env.example`, and the Models table of `CONTRACTS.md`.

## What would help, without doing it

For the person who copies `.env.example` and runs `gpu-profile`, in order:

1. Mark the keys that are read and the keys that are ignored on the path actually taken. Today the most costly lie is the chat block of `evo-x5` (profile file re-read by the binary, constants in `serve_argv`, alias different from the stem) and `EMBEDDER_MODEL_PATH` (file loaded, embedding identity elsewhere).
2. Labels and comments on the keys that are really read: `NGL` and `999` repeated in the profiles that only have the number; the arithmetic of `28544` next to the key; one sentence on `TRAIN_LORA_RANK`, `TRAIN_LORA_ALPHA`, `TRAIN_MAX_SEQ_LENGTH`; GB / GiB / MiB on a single scale in the profile and the GPU guide.
3. Separate, in the example, the three first-start values, the secrets (`AGENT_BEARER_TOKEN`, `GRAFANA_ADMIN_PASSWORD`, with two placeholders), and the wiring not to be touched. Say which port lines change only the host publication (`LLM_PORT`, `AGENT_PORT`), which are not interpolated (`EMBEDDER_PORT`, `TOOLS_PORT`, `PATCH_PORT`), and that `MCP_PORT` changes both ends.
4. Make `DEPLOY_WSL_DISTRO` be read in the same place as `CONTAINER_RUNTIME`, or say in the guide that it is a process variable, not a line of `.env`.
5. Align the Python defaults with the example, or say that they only matter outside Compose. Align the `EMBEDDER_GPU_ARG=1` comment of the embedder entrypoint with the `0` of the contract.
6. Structure, later, and only where text cannot suffice: a host argv that would read the same keys as the entrypoint; an embedding identity read by both clients. This is not one more file split, and it is not an interface proposed here. The checksums stay in `fetch_models.py`.

## Already clear, to leave alone

- A single operator file for the stack, plus `atlassian.env` for Jira. The profiles are templates applied by `gpu-profile`, not seven `.env` files to maintain by hand. The `llm` container follows `LLM_MODEL_PATH` and the flags of `.env`.
- The header that says to leave the internal URLs and the mount roots alone. `QDRANT_URL`, `TOOLS_BASE_URL`, `AGENT_BASE_URL` are the wiring.
- The comments of the example that are already precise on the timeout (a hop, not a target latency), the concurrent budget (503), the router (`LLM_ROUTER=1`), and choosing the profile from `nvidia-smi` rather than from Task Manager.
- The token generated at the first `up`, not printed, to paste into the Token field. `installation.md` says so.
- The Qdrant index, the CMake `release` preset, and the download catalog with checksums in the script. `up` does not download the optional GGUFs; `fetch-extra` says so in its help.
- `.env.test` as a CI file, once its header is brought up to the level of what it contains. The `setdefault` calls of the tests stay outside the operator's concern.
