# Pumba

Pumba is a fully containerized, offline, retrieval-augmented coding assistant built
around a C++/CMake project. Every process runs in its own container (Podman
by default): a local chat model and embedding model (`llama.cpp`), a vector
database (`qdrant`) holding a structurally-chunked index of the codebase, a
FastAPI orchestrator (`agent`) that ties retrieval and tool-calling together,
a sandboxed build/test/lint runner (`tools`), a code-intelligence sidecar
(`serena`: clangd, file reads, symbol-level edits), a small C++ CLI (`app`),
and an MCP front (`mcp`) so Cursor / Claude Code / OpenCode can ask for facts
(`lookup`, `check`, `calls`, `types`) or a short cited `ask`. On the EVO-X5
profile the chat model is the exception: it runs as `llama-server` on the host.

The manual is `podman compose --profile docs up`, then
<http://localhost:8000>, or the pages under `docs/` up to Architecture.
`CONTRACTS.md` is the contract. Reading notes of it are in
`docs/notes/contracts/`. Checks and research are in
`docs/notes/verification/`.
New to the ideas? Start with
[Plain-language overview](docs/concepts/en-clair.md). Otherwise
[Architecture overview](docs/architecture/overview.md), or jump straight to
[First run](docs/getting-started/first-run.md).
The concrete ports/env vars/API schemas every component is built against
live in [`CONTRACTS.md`](CONTRACTS.md); the vocabulary (checkout, named tool,
agent, editing, index, sidecar) is in [`GLOSSARY.md`](GLOSSARY.md).

## Quick start

From the repo root. The first `up` copies `.env.example` to `.env` and writes
a random `AGENT_BEARER_TOKEN` into it (not printed; read it from `.env`).

```bash
python scripts/deploy.py up
python scripts/deploy.py ask "How does the indexer chunk C++ files?"
python scripts/deploy.py status
python scripts/deploy.py down
```

`up` fetches the models, builds, starts the stack, waits until it is healthy,
and indexes `CODEBASE_PATH` (default `./sample`). It needs only Python 3.9+
(standard library) and a container engine: `CONTAINER_RUNTIME` in `.env` is
`auto` (a working Podman, else a working Docker), `podman`, or `docker`.
On Windows, if the Podman CLI has no machine and Docker is not usable, the
same command continues inside the WSL distro `local-llm`
([Prerequisites](docs/getting-started/prerequisites.md)). A virtualenv and a
permissions pass are not part of this step. Cursor, Claude Code, and OpenCode
can call the assistant at `http://127.0.0.1:8092/mcp` ([MCP](docs/notes/contracts/mcp.md)).
The chat page is `http://127.0.0.1:8090/ui`.

`python scripts/deploy.py --help` lists every command; `<command> --help`
lists its flags.

| Command | What it does |
|---|---|
| `up`, `down`, `status`, `logs <service>` | Lifecycle. `down --volumes` also removes the `qdrant-data` volume. |
| `ask`, `chat` | One question (string, `--file`, or `-` for stdin) or an interactive REPL, through the `app` container. |
| `index`, `workspace <path>`, `eval <path>` | Index `CODEBASE_PATH`; register another checkout; score retrieval on it ([Reindexing](docs/guides/reindexing-the-codebase.md), [Retrieval baseline](docs/notes/verification/retrieval-baseline.md)). |
| `gpu-profile <name>`, `fetch-extra <name>` | Write one GPU case into `.env`; download its optional GGUF ([Troubleshooting GPU](docs/guides/troubleshooting-gpu.md)). |
| `train` | QLoRA on `CODEBASE_PATH` ([Fine-tuning](docs/guides/fine-tuning-the-codebase.md)). |
| `test`, `check` | Tiny-model smoke test; live functional checks ([Testing](docs/guides/testing.md)). |
| `doctor` | Diagnose the stack (below). |
| `rotate-token` | New `AGENT_BEARER_TOKEN`, then recreate `agent`, `app`, `mcp`, and `ui-proxy` (`--skip-recreate` only rewrites `.env`). |
| `export` | Write `pumba-export.zip` in the repo root: source and docs, without models, logs, `state/`, virtualenvs, or `.env` (`--include-env` adds it). |
| `expose --allow <ipv4>` | Relay the loopback doors to allowlisted machines (below). |

The same steps run by hand are in
[Installation](docs/getting-started/installation.md#by-hand-raw-compose-commands);
the services and their ports are in
[Compose services](docs/notes/contracts/compose-services.md).

The indexed tree is `sample/` (a small calculator), not `app/` (the client).
To index another host directory, set `CODEBASE_PATH` in `.env` (e.g.
`d:/svn/mon_projet`) and run `python scripts/deploy.py index` - see
[Reindexing the codebase](docs/guides/reindexing-the-codebase.md). The
calculator's known bugs are listed in
[Review fixture](docs/guides/review-fixture.md).

See [First run](docs/getting-started/first-run.md) for the walkthrough and
[Troubleshooting GPU](docs/guides/troubleshooting-gpu.md) if `llm` doesn't
come up healthy.

Host unit tests can use a virtualenv created by
`python scripts/host/install_deps.py`. That script is not required to deploy.
`python scripts/host/fix_permissions.py` only matters on a native Linux
filesystem; on `D:` seen from WSL as `/mnt/d`, chmod is refused and the
script does not prepare the mount.

### `doctor`

If anything doesn't come up healthy, `python scripts/deploy.py doctor`
diagnoses the stack in one shot: the selected engine, `.env` and its
`AGENT_BEARER_TOKEN`, `CODEBASE_PATH`, whether the model files named in
`.env` exist (add `--verify-checksums` to also re-hash them against the pins
in `scripts/container/fetch_models.py`), the QLoRA base and adapter, free
disk space (warns under 12 GiB), the health of `qdrant`, `llm`, `embedder`,
`reranker`, `tools`, `agent`, `mcp`, and `ui-proxy`, and whether the
running `llm`/`embedder` images were built with the GPU `.env` asks for.
It does not probe `serena`: check it with `python scripts/deploy.py status`
and `python scripts/deploy.py logs serena`. It prints a plain-text report and
exits non-zero if anything failed; add `--html doctor-report.html` for a
shareable static snapshot, or `--gpu` to also run the GPU probe from
[Troubleshooting GPU](docs/guides/troubleshooting-gpu.md).

### `expose`

Compose publishes only on `127.0.0.1`. `python scripts/deploy.py expose
--allow 192.168.1.40` (repeatable, or one IPv4 per line in
`state/expose-allow.txt`) runs a foreground relay on the host's LAN address
(`--bind <ipv4>` to choose it) that forwards ports 8080 (model, `/v1`),
8090 (page, `/ui`), 8092 (MCP, `/mcp`), and 3000 (Grafana) to the same
loopback port, only for the listed source addresses. A network, a wildcard, or
an empty list is refused. On Windows it adds a firewall rule per port
(`pumba-expose-<port>`; run it elevated if packets are dropped). `Ctrl+C` or
`expose --down` removes them. `up` does not start it, and the ports are
fixed: they do not follow `AGENT_PORT`, `LLM_PORT`, or `MCP_PORT`. The only
filter is the source IPv4 address.

## Repository layout

```
app/                  C++ client (llm-client) that calls the agent
config/               GPU profiles (profiles/), compose overlays (compose/), serena/, observability/
sample/               calculator fixture indexed by default (known bugs)
services/             agent, indexer, mcp, tools, trainer (Python/FastAPI), common/ (shared code)
containers/           one Containerfile per built service, plus host-proxy/nginx.conf (serena is a pulled image)
docs/                 mkdocs-material documentation site; adr/ holds the decisions
scripts/deploy.py     the command you run; implementation is scripts/host/deploy/
scripts/host/         host scripts (deploy package, functional_check, workspaces, evo_x5_serve, ...)
scripts/container/    fetch_models, convert_lora, eval_rag, eval_tools (Compose only)
state/                runtime state (workspaces.json, compose.workspaces.yaml, locks, pids, expose-allow.txt)
tests/                pytest suites per service + integration smoke test
.devcontainer/        reuses the devtools compose service for editor integration
compose.yaml          all networking/volumes/healthchecks/profiles
CONTRACTS.md          the single source of truth for cross-service contracts
GLOSSARY.md           the vocabulary the code and docs share
```

## Development

The C++ toolchain, Python 3.11, the service dependencies, `pytest`, and
`clang-format`/`clang-tidy`/`cppcheck` live in `containers/devtools/Containerfile`.
`ruff` and `mypy` are not in that image: CI installs them, and
`python scripts/host/install_deps.py` puts them in a host virtualenv. Open
this folder in an editor with Dev Containers support (pointed at Podman, e.g.
Cursor/VS Code's `dev.containers.dockerPath` setting), or run ad hoc commands
directly:

```bash
podman compose --profile dev run --rm devtools bash
```

See [Testing](docs/guides/testing.md) for how to run the full test suite
(per-service unit tests, the C++ `ctest` suite, the RAG retrieval
evaluation, and the end-to-end smoke test).

## Security

`tools` is the highest-risk service - an LLM decides when to call it - and
`serena` can write the checkout. See
[`SECURITY.md`](SECURITY.md) for the full hardening checklist and
[docs/architecture/security.md](docs/architecture/security.md) for the
reader-friendly version.
