# Security

This is a local development tool, not a multi-tenant production service -
but it deliberately lets an LLM decide when to invoke build/test/lint
commands, so it's built with that specific threat model in mind throughout.
See [docs/architecture/security.md](docs/architecture/security.md) for a
reader-friendly walkthrough; this file is the flat checklist.

## Network segmentation

- Three internal-only Podman networks: `client-net` (`app` + `mcp` <->
  `agent`), `core-net` (`agent`, `llm`, `embedder`, `reranker`, `qdrant`, `indexer`),
  `tools-net` (`agent` <-> `tools`). All three have `internal: true` - no
  route to the outside world at all.
- `tools` is unreachable from `llm`/`embedder`/`reranker`/`qdrant`, and vice versa, in
  both directions. `agent` is the only service on more than one *internal*
  network. `mcp`, `llm`, and `ui-proxy` also join `publish-net` (no outbound
  route) only so the engine binds their loopback host ports; `mcp` never
  joins `core-net` or `tools-net`.
- The only container with real internet access, ever, is `fetch-models`
  (the `setup` profile's one-off model-download job), and only while that
  specific job runs.

## The `tools` service (highest attack surface - an LLM decides when to call it)

- No generic shell/exec endpoint anywhere - a fixed allow-list of
  parameterized endpoints in `services/tools/main.py` (build/test/compile,
  four read-only git tools, three read-only svn tools, clangd definition/references/hover/symbols/outline/calls/types,
  lint/format/static-analysis, search/list/read). `/git/diff-names` is
  agent-only (not an LLM tool).
- Every read-type endpoint (`/read-file`, `/search`, `/list-files`, git_*)
  has a hard output cap (400 lines / 100 matches / 500 paths / 200 git
  lines) so a single tool observation can never saturate the model's
  context window - see `CONTRACTS.md`, "Bounded outputs".
- Git refs are allow-listed (`HEAD`, `HEAD~N`, sha, simple branch/tag);
  argv only, no shell, no `..` ranges.
- Every subprocess is invoked as an argv list with `shell=False` - never a
  shell string, anywhere in the codebase (`tests/tools/test_argv_construction.py`
  asserts this).
- Every caller-supplied path is re-validated server-side
  (`services/tools/handlers/path_safety.py`) against the mounted project
  root: rejects `..` traversal, absolute paths (POSIX- and Windows-style),
  null bytes, and - critically - resolves symlinks *before* checking
  containment, so a symlink planted inside the mount can't be used to
  escape it (`tests/tools/test_path_validation.py`).
- CMake preset names are validated against a small fixed allow-list before
  any subprocess ever starts.
- `/build` and `/test` each get a fresh `tempfile.mkdtemp()` scratch
  directory per request, always removed afterwards (success or failure) -
  concurrent requests never share build state. `/compile` reuses a
  per-(project root, preset) cache under `/workspace/scratch/cmake-cache/`,
  so a registered checkout never reuses the default tree's `CMakeCache.txt`.
- `tools` healthcheck is strict in compose (`TOOLS_HEALTH_STRICT=1`):
  missing `cmake`/`ctest`/`git`/`rg`/... is HTTP 503.
- `agent` admits at most `AGENT_MAX_CONCURRENT` simultaneous `/chat`
  (default 2); extras get 503. Rotate the bearer token with
  `python scripts/deploy.py rotate-token`.
- `tools` has cgroup limits in compose (`cpus: 2`, `memory: 4G`,
  `pids_limit: 256`).
- Every subprocess has a timeout (`TOOLS_BUILD_TIMEOUT_SECONDS`, default
  14400, for build, test, clangd, search, git, and svn).
- `/search` never lets the caller choose the search root - it's always the
  fixed mounted project root, so there's no path-traversal surface there at
  all, even without `path_safety`. `/list-files` accepts an optional
  sub-directory, validated through `path_safety` like every other path.
- `/read-file` enforces a 256 KiB size cap, returning `413` rather than
  truncating silently.

## Container hardening (applied per service in `compose.yaml`)

- Non-root user in every image (`containers/*/Containerfile`).
- `cap_drop: ["ALL"]` and `security_opt: ["no-new-privileges:true"]` on every
  service.
- Read-only root filesystem on `agent` and `tools`, with narrowly-scoped
  `tmpfs` mounts only where genuinely needed (`/tmp`, and `tools`'
  `/workspace/scratch`).
- `app`, `tools`, `agent`, and `indexer` only ever get read-only bind mounts
  of the source tree (`${CODEBASE_PATH:-./sample}:/workspace/app:ro`).
  `patch` (`tools-net`, not published) is the only process whose checkout
  mount is read-write: `edit` proposes an exact replacement and `apply`
  writes that copy. There is no commit and no push. Pointing `CODEBASE_PATH`
  at a larger tree widens what `tools` can read and search, still read-only.

## Authentication

- `agent`'s `/chat` requires a static bearer token (`AGENT_BEARER_TOKEN`,
  set in `.env` - **never commit a real value**, `.env` is gitignored).
- `mcp` is published on **loopback only** (`127.0.0.1:8092`) and forwards
  `ask` to that same `/chat` with the bearer. Do not publish `0.0.0.0`.
- `agent` is reached from the host through `ui-proxy` on **loopback only**
  (`127.0.0.1:8090`) for `GET /ui`. `agent` itself stays on internal networks
  (plus `publish-net` on `evo-x5` so it can call the host `llama-server`;
  that network has no default route).
  That page does not embed the token. `/chat`, `/chat/stream`, and `/review`
  still require the bearer. Do not publish `0.0.0.0`.
- `tools` and `patch` have no authentication of their own - they rely on network
  segmentation (only `agent` can reach them). Don't attach them to
  any other network without adding an equivalent control.

## Model integrity

- `scripts/container/fetch_models.py` verifies a SHA256 checksum after downloading
  each model file. All three pinned files (chat, embedding, test) have a
  real checksum sourced from the model repo's own Git LFS metadata (the LFS
  `oid` for an LFS-tracked file *is* its SHA256 - see the comments next to
  each `ModelSpec` in that script for the exact source URL). A checksum
  mismatch is fatal: the script deletes the file and exits non-zero rather
  than silently trusting it. `sha256=None` remains supported for a future
  model added without a pin yet, but degrades to a loud runtime warning
  instead of verification - not the steady state for any pinned model.

## Reproducibility / supply chain

- Every base image, the exact `llama.cpp` commit (`b10216`), and every model
  file are pinned by tag/commit in `CONTRACTS.md` - the single source of
  truth. Base images are pinned by tag today; pin to `@sha256:<digest>` once
  each has actually been pulled once (see `CONTRACTS.md`'s note on this).
- `llm`/`embedder` must always pin the identical `llama.cpp` tag;
  `app`/`tools`/`devtools` must always pin the identical Ubuntu base - see
  [Testing](docs/guides/testing.md)'s pinned-version audit section.

## Known limitations (be aware, not necessarily blockers for local use)

- No rate limiting or per-user authorization - one shared bearer token for
  the whole `agent` API.
- `tools` has both process timeouts and compose cgroup limits (`cpus: 2`,
  `memory: 4G`, `pids_limit: 256`). The cgroup numbers are a local default,
  not a hard guarantee on every Podman version.
- Deleted or renamed files are tombstoned on the next index. `--watch` on
  Windows → WSL2 bind mounts still misses some inotify events.

## Reporting a concern

This is a local, self-hosted project with no hosted service or user data
involved - if you find an issue, open one in this repository's issue
tracker.
