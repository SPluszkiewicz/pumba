# Installation

## 1. Clone the repository

```bash
git clone <this-repo-url>
cd pumba
```

## 2. Start the stack

```bash
python scripts/deploy.py up
```

The first `up` copies `.env.example` to `.env` if needed and writes a random
`AGENT_BEARER_TOKEN` into it. The token is not printed; copy it from `.env`
into the UI **Jeton** field. Every other value in `.env.example` has a
sensible default - see `CONTRACTS.md` for what each one does.

To pick a GPU case first (no container engine required):

```bash
python scripts/deploy.py gpu-profile evo-x5
python scripts/deploy.py up
```

That fetches the pinned models, builds the images, starts the core services,
waits until they are healthy, and indexes `sample/`. From PowerShell, a
Podman CLI with no machine makes this continue inside the WSL distro
`local-llm`. `CONTAINER_RUNTIME=docker` uses Docker Desktop. Do not chmod
the tree first, and do not create a virtualenv first.

On the GMKtec EVO-X5 Pro, follow [Déployer sur l'EVO-X5](../guides/evo-x5.md).
`up` starts `llama-server` on the host and does not start the `llm`
container. The hand-written compose line below is the NVIDIA path: it
starts that container.

The same work, run by hand where the engine already works:

```bash
podman compose --profile setup run --rm fetch-models
podman compose build
podman compose up -d qdrant llm embedder reranker tools patch agent ui-proxy mcp
podman compose --profile index run --rm indexer
```

## Optional: host unit tests

```bash
python scripts/host/install_deps.py
```

That installs pytest, ruff, and mypy into a virtualenv in the repository.
The supported toolchain for the services themselves remains the `devtools`
container. `scripts/host/fix_permissions.py` is for a native Linux filesystem
only.

## Next

[First run](first-run.md) - starting the stack, indexing the codebase, and
asking your first question.
