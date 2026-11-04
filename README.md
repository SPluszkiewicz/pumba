# Pumba

Pumba is a fully containerized, offline, retrieval-augmented coding assistant built
around a C++/CMake project. Every process runs in its own Podman container:
a local chat model and embedding model (`llama.cpp`), a vector database
(`qdrant`) holding a structurally-chunked index of the codebase, a FastAPI
orchestrator (`agent`) that ties retrieval and tool-calling together, a
sandboxed build/test/lint runner (`tools`), a small C++ CLI (`app`), and an
MCP front (`mcp`) so Cursor / Claude Code / OpenCode can ask for facts
(`lookup`, `check`, `outline`, `calls`, `types`) or a short cited `ask`.

Full documentation: `podman compose --profile docs up` then browse
<http://localhost:8000>, or read the pages directly under `docs/`.
New to the ideas? Start with
[Comprendre sans bagage](docs/concepts/en-clair.md). Otherwise
[Architecture overview](docs/architecture/overview.md), or jump straight to
[First run](docs/getting-started/first-run.md).
The concrete ports/env vars/API schemas every component is built against
live in [`CONTRACTS.md`](CONTRACTS.md).

## Quick start

From the repo root, after copying `.env.example` to `.env` and setting a real
`AGENT_BEARER_TOKEN`:

```bash
python scripts/deploy.py up
python scripts/deploy.py ask "How does the indexer chunk C++ files?"
python scripts/deploy.py status
python scripts/deploy.py down
```

That fetches the models, builds, starts the stack, waits until it is healthy,
and indexes `sample/`. On Windows, if the Podman CLI has no machine, the same
command continues inside the WSL distro `local-llm`. Set `CONTAINER_RUNTIME=docker`
in `.env` to use Docker Desktop instead of Podman (`auto` tries Podman first).
A virtualenv and a permissions pass are not part of this step. Cursor, Claude
Code, and OpenCode can call the assistant at `http://127.0.0.1:8092/mcp`
([MCP](docs/reference/mcp.md)).

`python scripts/deploy.py --help` lists the rest (`workspace`, `gpu-profile`,
`fetch-extra`, `check`, `index`, `train`, `logs`, `test`, `doctor`,
`export`, `down --volumes`). `export` writes `pumba-export.zip` next to
the repo: source and docs, without models, logs, `state/`, or virtualenvs.

The steps above are also the raw Compose commands, if you want to run them
yourself where the engine already works:

```bash
# 1. One-time: fetch model weights (needs internet, runs in a container)
podman compose --profile setup run --rm fetch-models

# 2. Build and start the core services
podman compose up -d qdrant llm embedder reranker tools patch agent ui-proxy mcp

# 3. Index the codebase (one-shot job, safe to re-run when the indexed tree changes)
podman compose --profile index run --rm indexer

# 4. Ask it something
podman compose run --rm app "How does the indexer chunk C++ files?"
```

The indexed tree is `sample/` (a small calculator), not `app/` (the client).
To index another host directory, set `CODEBASE_PATH` in `.env` (e.g.
`d:/svn/mon_projet`) and run `python scripts/deploy.py index` - see
[Reindexing the codebase](docs/guides/reindexing-the-codebase.md). The
calculator's known bugs are listed in
[Review fixture](docs/guides/review-fixture.md).

See [First run](docs/getting-started/first-run.md) for the full walkthrough
with expected output, and [Troubleshooting GPU](docs/guides/troubleshooting-gpu.md)
if `llm` doesn't come up healthy.

Host unit tests can use a virtualenv created by
`python scripts/host/install_deps.py`. That script is not required to deploy.
`python scripts/host/fix_permissions.py` only matters on a native Linux
filesystem; on `D:` seen from WSL as `/mnt/d`, chmod is refused and the
script does not prepare the mount.

If anything doesn't come up healthy, `python scripts/deploy.py doctor`
diagnoses the whole stack in one shot: the selected engine, `.env` and its
`AGENT_BEARER_TOKEN`, whether the pinned model files exist (add
`--verify-checksums` to also re-hash them), free disk space, and each core
service's health. It prints a plain-text report and exits non-zero if
anything actually failed; add `--html doctor-report.html` for a shareable
static snapshot, or `--gpu` to also run the GPU probe from
[Troubleshooting GPU](docs/guides/troubleshooting-gpu.md).

## Repository layout

```
app/                  C++ client (llm-client) that calls the agent
config/               GPU profiles (config/profiles/), compose overlays (config/compose/), observability
sample/               calculator fixture indexed by default (known bugs)
services/             agent, indexer, mcp, patch, tools, trainer (Python/FastAPI)
containers/           one Containerfile per service, plus docs/devtools
docs/                 mkdocs-material documentation site
scripts/deploy.py     the command you run; implementation is scripts/host/deploy/
scripts/host/         host scripts (deploy package, functional_check, workspaces, evo_x5_serve, ...)
scripts/container/    fetch_models, convert_lora, eval_rag, eval_tools (Compose only)
state/                runtime state (workspaces.json, compose.workspaces.yaml, locks, pid)
tests/                pytest suites per service + integration smoke test
.devcontainer/        reuses the devtools compose service for editor integration
compose.yaml          all networking/volumes/healthchecks/profiles
CONTRACTS.md          the single source of truth for cross-service contracts
```

## Development

Everything - the C++ toolchain, Python, every linter, `pytest` - lives in
`containers/devtools/Containerfile`. Open this folder in an editor with Dev
Containers support (pointed at Podman, e.g. Cursor/VS Code's
`dev.containers.dockerPath` setting), or run ad hoc commands directly:

```bash
podman compose --profile dev run --rm devtools bash
```

See [Testing](docs/guides/testing.md) for how to run the full test suite
(per-service unit tests, the C++ `ctest` suite, the RAG retrieval
evaluation, and the end-to-end smoke test), all inside containers.

## Security

`tools` is the highest-risk service - an LLM decides when to call it. See
[`SECURITY.md`](SECURITY.md) for the full hardening checklist and
[docs/architecture/security.md](docs/architecture/security.md) for the
reader-friendly version.
