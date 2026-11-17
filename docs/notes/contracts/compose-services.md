# Compose services reference

Reading note of [`CONTRACTS.md`](../../../CONTRACTS.md) and `compose.yaml`. If this page disagrees with either, those files win.

See `compose.yaml` (repo root) for the authoritative, always-up-to-date
definitions. This page is a summary for orientation.

| Service | Image / build | Networks | Published port | Profile | Healthcheck |
|---|---|---|---|---|---|
| `qdrant` | `qdrant/qdrant:v1.19.0` | `core-net` | none | *(default)* | bash `/dev/tcp` connect check on 6333 |
| `llm` | `containers/llm/Containerfile` | `core-net`, `publish-net` | `127.0.0.1:8080` (`LLM_PORT`) | *(default)*; `container-llm` (so not started) on `evo-x5` | `curl` on `/health` (8080) |
| `embedder` | `containers/embedder/Containerfile` | `core-net` | none | *(default)* | `curl` on `/health` (8081) |
| `reranker` | same Containerfile as `embedder` | `core-net` | none | *(default)* | `curl` on `/health` (8082) |
| `agent` | `containers/agent/Containerfile` | `client-net`, `core-net`, `tools-net` (plus `publish-net` on `evo-x5`) | none (see `ui-proxy`) | *(default)* | `curl` on `/health` (8090) |
| `ui-proxy` | `docker.io/nginxinc/nginx-unprivileged:1.27-alpine` | `client-net`, `publish-net` | `127.0.0.1:8090` (`/ui`, `AGENT_PORT`) | *(default)* | `wget` on `/health` (8090, proxied to agent) |
| `mcp` | `containers/mcp/Containerfile` | `client-net`, `publish-net` | `127.0.0.1:8092` (`MCP_PORT`) | *(default)* | `curl` on `/health` (8092) |
| `tools` | `containers/tools/Containerfile` | `tools-net` | none | *(default)* | `wget` on `/health` (8091) |
| `serena` | `ghcr.io/oraios/serena:2.1.0` (pulled, no Containerfile) | `tools-net` | none | *(default)*, pulled in by `agent`'s `depends_on` | bash `/dev/tcp` connect check on 9121 when `SERENA_HEALTH_STRICT=1`, else always healthy |
| `app` | `containers/app/Containerfile` | `client-net` | none | *(default)* | none (interactive CLI) |
| `indexer` | `containers/indexer/Containerfile` | `core-net` | none | `index` | none (one-shot job) |
| `docs` | `containers/docs/Containerfile` | *(default bridge)* | `8000:8000` (all interfaces) | `docs` | none |
| `devtools` | `containers/devtools/Containerfile` | *(default bridge)* | none | `dev` | none |
| `fetch-models` | `containers/devtools/Containerfile` | *(default bridge, non-internal)* | none | `setup` | none (one-shot job) |
| `smoke-test` | `containers/devtools/Containerfile` | `client-net`, `core-net`, `tools-net` | none | `test` | none (one-shot job) |
| `trainer` | `containers/trainer/Containerfile` | *(default bridge)* | none | `train` | none (one-shot job, GPU via CDI) |
| `prometheus` | `prom/prometheus:v2.55.1` | `core-net`, `tools-net`, `obs-net` | none | `obs` | none |
| `grafana` | `grafana/grafana:11.3.0` | `obs-net` | `127.0.0.1:3000` | `obs` | none |

`prometheus` and `grafana` come from
`config/compose/observability.yaml`. They are not started by `deploy.py up`.
How to read the dashboards: [Observability](../../guides/observability.md).

`*(default)*` means the service starts with a bare `podman compose up` (no
`--profile` flag needed). Services on `*(default bridge)*` have no explicit
`networks:` entry, so they get the engine's ordinary (non-internal) per-project
network. This is intentional for `fetch-models` (needs real internet
access) and harmless for `docs`/`devtools` (dev-only, never part of the
secured runtime path). `client-net`, `core-net`, and `tools-net` are
`internal`; `publish-net` is not (so the engine can bind the host port) and
has no outbound route through the overlay for the engine in use
(`config/compose/docker.yaml` or `podman.yaml`).

Every service reads its configuration from environment variables only, with
one exception: `serena` also reads `config/serena/serena_config.yml`. See
`.env.example` for the variables and `CONTRACTS.md` for what each one does.
`llm`/`embedder`/`reranker`'s `GPU` build arg (`LLM_GPU_ARG`,
`EMBEDDER_GPU_ARG`) is the other exception (a build-time argument, not a
runtime env var). See [Troubleshooting GPU](../../guides/troubleshooting-gpu.md).

## Startup order

`agent` waits (`service_healthy`) for `llm`, `embedder`, `qdrant`, `tools`,
and `serena`. It does not wait for `reranker`: a missing reranker
GGUF leaves retrieval on the RRF order. `ui-proxy`, `mcp`, and `app` wait for
`agent`. `config/compose/host-llm.yaml` (selected by `LLM_SERVE=host`) drops
`llm` from that list and moves `llm` to the `container-llm` profile.

## The checkout mount

`CODEBASE_PATH` (default `./sample`) is mounted at `/workspace/app`. The
mode depends on who reads or writes it:

| Service | Mount |
|---|---|
| `indexer`, `agent`, `tools`, `trainer` | read-only |
| `serena` | read-write |

`tools` also owns the `cmake-cache` volume (CMake compile cache, under its
`/workspace/scratch` tmpfs); `serena` mounts the same volume read-only, so
it never writes what `tools` builds. `agent` additionally mounts
`state/workspaces.json` read-only. Each checkout added with
`python scripts/deploy.py workspace <path>` is mounted at `/checkouts/<id>`
through the generated `state/compose.workspaces.yaml` (read-only for `tools`
and `agent`, read-write for `serena`).

`serena` is the code-intelligence [sidecar](../../adr/0001-serena-sidecar.md):
Serena 2.1 over MCP (streamable HTTP, port 9121) with clangd. It is on
`tools-net` only, unpublished, with the agent as its only client. Its config
(`config/serena/serena_config.yml`) is copied into the container at start,
because Serena rewrites that file. It disables the shell tool, the REPL, the
JetBrains tools, the dashboard, and ignores `.git`, `.svn`, and `.env*`.
The agent finds it at `http://serena:9121`: that is the code default of
`SERENA_BASE_URL`, and `compose.yaml` does not forward the variable, so
changing it in `.env` has no effect on the agent. `SERENA_HEALTH_STRICT`
(compose default `1`) behaves like `TOOLS_HEALTH_STRICT`: a dead sidecar fails
its healthcheck and `agent` does not start. The image tag is the upgrade.

## Overlays

`deploy.py` stacks these `-f` files on `compose.yaml` as needed:

| File | When |
|---|---|
| `config/compose/docker.yaml` | Docker engine: `gpus: all` instead of CDI devices; publish-net masquerade off |
| `config/compose/podman.yaml` | Podman engine: publish-net without default route |
| `config/compose/cpu.yaml` (+ `docker.cpu.yaml` on Docker) | CPU fallback: no GPU devices, `*_NGL=0` |
| `config/compose/host-llm.yaml` | `LLM_SERVE=host` (`evo-x5`): `agent` joins `publish-net` to reach the host `llama-server` |
| `state/compose.workspaces.yaml` | generated by `workspace`: extra checkout mounts |
| `config/compose/observability.yaml` | by hand only: `prometheus` and `grafana` (profile `obs`) |

## Bringing services up selectively

```bash
# The serving stack, no CLI. `python scripts/deploy.py up` starts these nine
# (without `llm` when LLM_SERVE=host). Compose also starts `serena`, because
# `agent` depends on it - unless you pass --no-deps:
podman compose up -d qdrant llm embedder reranker tools agent ui-proxy mcp

# One-off jobs (each exits when done):
podman compose --profile setup run --rm fetch-models
podman compose --profile index run --rm indexer
podman compose --profile test run --rm smoke-test

# Interactive:
podman compose run --rm app "your question here"
# long prompt from a file:
python scripts/deploy.py ask --file my-question.md
podman compose --profile dev run --rm devtools bash
podman compose --profile docs up   # then browse http://localhost:8000
```

With `LLM_SERVE=host`, `deploy.py up` runs `compose up -d --no-deps` on that
list minus `llm`, so `serena` is **not** started by it: see
[Deploy on the EVO-X5](../../guides/evo-x5.md#serena).

The step-by-step first deployment is in
[Installation](../../getting-started/installation.md#by-hand-raw-compose-commands).
To reach the loopback doors from another machine, see
[`deploy.py expose`](../../../README.md#expose).
