# Installation

## 1. Clone the repository

```bash
git clone <this-repo-url>
cd local_llm
```

## 2. Configure environment variables

Copy the example env file and fill in a real bearer token:

```bash
cp .env.example .env
```

Edit `.env` and replace `AGENT_BEARER_TOKEN`'s placeholder value with a real
random secret (e.g. `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
run inside `devtools`, or any password generator). Every other value in
`.env.example` has a sensible default - see `CONTRACTS.md` for what each one
does.

## 3. Start the stack

```bash
python scripts/deploy.py up
```

That fetches the pinned models, builds the images, starts the core services,
waits until they are healthy, and indexes `sample/`. From PowerShell, a
Podman CLI with no machine makes this continue inside the WSL distro
`local-llm`. `CONTAINER_RUNTIME=docker` uses Docker Desktop. Do not chmod
the tree first, and do not create a virtualenv first.

The same work, run by hand where the engine already works:

```bash
podman compose --profile setup run --rm fetch-models
podman compose build
podman compose up -d qdrant llm embedder reranker tools agent ui-proxy mcp
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
