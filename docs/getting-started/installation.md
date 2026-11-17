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
into the UI `Jeton` (Token) field. Every other value in `.env.example` has a
sensible default. See `CONTRACTS.md` for what each one does. Three values are
yours to set: `CONTAINER_RUNTIME`, `CODEBASE_PATH` (default `./sample`), and
the token.

To pick a GPU case first (no container engine required):

```bash
python scripts/deploy.py gpu-profile evo-x5
python scripts/deploy.py up
```

`up` checks the engine and `CODEBASE_PATH`, prepares the GPU (or falls back
to CPU), fetches the pinned models, builds the images, starts the services,
waits until they are healthy, and indexes `CODEBASE_PATH` (`sample/` by
default). From PowerShell, a Podman CLI with no machine makes this continue
inside the WSL distro `local-llm`; `CONTAINER_RUNTIME=docker` uses Docker
Desktop (see [Prerequisites](prerequisites.md#container-engine)). Do not
chmod the tree first, and do not create a virtualenv first. Useful flags:
`--skip-models`, `--skip-index`, `--timeout <seconds>` (default 300 for the
health wait; the host `llama-server` waits at least 900).

`up` waits for `qdrant`, `llm`, `embedder`, `reranker`, `tools`,
`agent`, `mcp`, and `ui-proxy`. `serena`, the code-intelligence sidecar, is
not in that list: Compose starts it because `agent` `depends_on` it, and
`agent` only starts once it is healthy. Check it with
`python scripts/deploy.py status`.

On the GMKtec EVO-X5 Pro, follow [Deploy on the EVO-X5](../guides/evo-x5.md).
`up` starts `llama-server` on the host and does not start the `llm`
container. The raw Compose lines below are the NVIDIA/CPU path: they start
that container.

## By hand (raw Compose commands)

The same work, run by hand where the engine already works. Add the overlay
files `deploy.py` adds (`config/compose/docker.yaml` on Docker,
`config/compose/podman.yaml` on Podman, `config/compose/cpu.yaml` for the
CPU fallback) with `-f` if you need them. `state/compose.workspaces.yaml`
(checkout mounts) only exists once you ran `workspace`.

```bash
podman compose --profile setup run --rm fetch-models
podman compose --profile index build
podman compose up -d qdrant llm embedder reranker tools agent ui-proxy mcp
podman compose --profile index run --rm indexer
podman compose run --rm app "How does the indexer chunk C++ files?"
```

The `up -d` line has no `serena`: Compose adds it through `agent`'s
`depends_on` (do not pass `--no-deps`). What each profile and service is:
[Compose services](../notes/contracts/compose-services.md).

## Optional: host unit tests

```bash
python scripts/host/install_deps.py
```

That installs the service dependencies and pytest, ruff, and mypy into a
virtualenv in the repository ([details](prerequisites.md#python-virtualenv-optional)).
The supported toolchain for the services themselves remains the `devtools`
container. `scripts/host/fix_permissions.py` is for a native Linux filesystem
only.

## Next

[First run](first-run.md), starting the stack, indexing the codebase, and
asking your first question.
