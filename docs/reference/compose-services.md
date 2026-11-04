# Compose services reference

See `compose.yaml` (repo root) for the authoritative, always-up-to-date
definitions - this page is a summary for orientation.

| Service | Image / build | Networks | Published port | Profile | Healthcheck |
|---|---|---|---|---|---|
| `qdrant` | `qdrant/qdrant:v1.19.0` | `core-net` | none | *(default)* | bash `/dev/tcp` connect check on 6333 |
| `llm` | `containers/llm/Containerfile` | `core-net`, `publish-net` | `127.0.0.1:8080` | *(default)*; not started on `evo-x5` | `curl` on `/health` (8080) |
| `embedder` | `containers/embedder/Containerfile` | `core-net` | none | *(default)* | `curl` on `/health` (8081) |
| `reranker` | same Containerfile as `embedder` | `core-net` | none | *(default)* | `curl` on `/health` (8082) |
| `agent` | `containers/agent/Containerfile` | `client-net`, `core-net`, `tools-net` (plus `publish-net` on `evo-x5`) | none (see `ui-proxy`) | *(default)* | `curl` on `/health` (8090) |
| `ui-proxy` | `nginxinc/nginx-unprivileged:1.27-alpine` | `client-net`, `publish-net` | `127.0.0.1:8090` (`/ui`) | *(default)* | — |
| `mcp` | `containers/mcp/Containerfile` | `client-net`, `publish-net` | `127.0.0.1:8092` | *(default)* | `curl` on `/health` (8092) |
| `tools` | `containers/tools/Containerfile` | `tools-net` | none | *(default)* | `wget` on `/health` (8091) |
| `patch` | `containers/patch/Containerfile` | `tools-net` | none | *(default)* | `curl` on `/health` (8093) |
| `app` | `containers/app/Containerfile` | `client-net` | none | *(default)* | none (interactive CLI) |
| `indexer` | `containers/indexer/Containerfile` | `core-net` | none | `index` | none (one-shot job) |
| `docs` | `containers/docs/Containerfile` | *(default bridge)* | `8000:8000` | `docs` | none |
| `devtools` | `containers/devtools/Containerfile` | *(default bridge)* | none | `dev` | none |
| `fetch-models` | `containers/devtools/Containerfile` | *(default bridge, non-internal)* | none | `setup` | none (one-shot job) |
| `smoke-test` | `containers/devtools/Containerfile` | `client-net`, `core-net`, `tools-net` | none | `test` | none (one-shot job) |
| `trainer` | `containers/trainer/Containerfile` | *(default bridge)* | none | `train` | none (one-shot job, GPU via CDI) |

`*(default)*` means the service starts with a bare `podman compose up` (no
`--profile` flag needed). Services on `*(default bridge)*` have no explicit
`networks:` entry, so they get Podman's ordinary (non-internal) per-project
network - this is intentional for `fetch-models` (needs real internet
access) and harmless for `docs`/`devtools` (dev-only, never part of the
secured runtime path).

Every service reads its configuration from environment variables only - see
`.env.example` for the full list and `CONTRACTS.md` for what each one does.
`llm`/`embedder`'s `GPU` build arg is the one exception (a build-time
argument, not a runtime env var) - see
[Troubleshooting GPU](../guides/troubleshooting-gpu.md).

## Bringing services up selectively

```bash
# Just the core serving stack, no CLI (this is what `python scripts/deploy.py up` starts):
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
