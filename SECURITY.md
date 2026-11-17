# Security

This is a local development tool, not a multi-tenant production service -
but it deliberately lets an LLM decide when to invoke build/test/lint
commands, so it's built with that specific threat model in mind throughout.
See [docs/architecture/security.md](docs/architecture/security.md) for a
reader-friendly walkthrough; this file is the flat checklist.

## Network segmentation

- Three internal-only Podman networks: `client-net` (`app` + `mcp` +
  `ui-proxy` <-> `agent`), `core-net` (`agent`, `llm`, `embedder`, `reranker`,
  `qdrant`, `indexer`), `tools-net` (`agent`, `tools`, `serena`; only
  `agent` is a client of the other two). All three have `internal: true` - no
  route to the outside world at all.
- `tools` and `serena` are unreachable from `llm`/`embedder`/`reranker`/`qdrant`,
  and vice versa, in both directions. `agent` is the only service on more
  than one *internal* network. `mcp`, `llm`, and `ui-proxy` also join `publish-net` (no outbound
  route) only so the engine binds their loopback host ports; `mcp` never
  joins `core-net` or `tools-net`.
- The only container with real internet access, ever, is `fetch-models`
  (the `setup` profile's one-off model-download job), and only while that
  specific job runs.

## The `tools` service (highest attack surface - an LLM decides when to call it)

- No generic shell/exec endpoint anywhere - a fixed allow-list of
  parameterized endpoints in `services/tools/main.py` (build/build-step/test/compile,
  four read-only git tools, three read-only svn tools, clangd outline/calls/types,
  lint/format/static-analysis, `/read-file`, `/cancel`). The model can
  only name the 16 `TOOL_SPECS` entries of `services/common/tool_models.py`
  (`CONTRACTS.md`, "Named tools"); `/git/diff-names`, `/git/status`, `/read-file`,
  and `/lsp/outline` are agent-only. Reading, searching, and the other symbol
  queries go through Serena, not through `tools`.
- `/read-file` caps a window at 400 lines and git/svn output at 200 lines,
  so a single tool observation cannot fill the model's context window.
  See `CONTRACTS.md`, "Bounded outputs". The Serena tools
  are capped by the sidecar (150000 characters by default) and the agent refuses
  a `max_answer_chars` above 262144.
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
  concurrent requests never share build state. `/compile` and `/build-step` reuse a
  per-(project root, preset) cache under `/workspace/scratch/cmake-cache/`
  (the `cmake-cache` volume, which `serena` mounts read-only),
  so a registered checkout never reuses the default tree's `CMakeCache.txt`.
- `tools` healthcheck is strict in compose (`TOOLS_HEALTH_STRICT=1`; the code
  default is 0): missing `cmake`/`ctest`/`git`/`svn`/`rg`/... is HTTP 503.
- `agent` admits at most `AGENT_MAX_CONCURRENT` simultaneous `/chat`
  (default 2); extras get 503. Rotate the bearer token with
  `python scripts/deploy.py rotate-token`.
- `tools` has cgroup limits in compose (`cpus: 2`, `memory: 4G`,
  `pids_limit: 256`).
- Every subprocess has a timeout (`TOOLS_BUILD_TIMEOUT_SECONDS`, default
  14400, for build, test, clangd, git, and svn).
- `X-Project-Root` must be an existing direct child of `/checkouts`,
  otherwise `400`. Absent, the call uses the default tree.
- `/read-file` enforces a 256 KiB size cap, returning `413` rather than
  truncating silently.

## The `serena` sidecar (the model's reader and editor)

Reads, symbol navigation, and every edit go through Serena
(`ghcr.io/oraios/serena:2.1.0`, `tools-net`, port 9121, not published). It
has no authentication of its own and a **read-write checkout mount**, so the
controls sit in front of it and in its config:

- `services/agent/serena_policy.py` refuses a call before it leaves the
  agent (`exit_code: 2`): a path argument (`relative_path`, `reference_file`,
  `paths_include_glob`, `paths_exclude_glob`) that is absolute, drive-anchored, has a
  null byte, `..`, `:`, or a `.git`, `.svn`, or `.env*` segment; a path that
  resolves outside the registered checkout; a `max_answer_chars` above 262144.
- Only the 23 names in `services/agent/serena_named_tools.py` are in the
  model's schema; the rest of Serena's tool set is not reachable by name.
  `config/serena/serena_config.yml` also excludes `execute_shell_command`,
  `serena_repl`, `open_dashboard`, and every `jet_brains_*` tool, and ignores
  `.git`, `.svn`, `.env`, and `.env.*`. The dashboard is disabled.
- The 11 write names open the *editing* phase: `build`, `test`, and
  `compile_file` are refused until one `build_step` succeeds
  (`services/agent/edit_policy.py`). That is a workflow guard, not a
  permission boundary.
- Serena cannot be cancelled mid-call: `/cancel` only reaches `tools`;
  the agent drops the request. Known limitation, code reading only:
  for a registered `workspace`, the agent checks the path against
  `/checkouts/<id>` but Serena runs on `/workspace/app` with no activate-project
  call, so the check may not describe the tree Serena then writes. Verify on
  your setup before relying on Serena edits in a registered checkout.
- `serena` has no `read_only` root and no `user:` override in `compose.yaml`;
  the non-root claim below is not verified for this third-party image.

## Container hardening (applied per service in `compose.yaml`)

- Non-root user in every image built from `containers/*/Containerfile`. The
  pulled `serena` image is not built here and its user was not verified.
- `cap_drop: ["ALL"]` and `security_opt: ["no-new-privileges:true"]` on every
  runtime service (`qdrant`, `llm`, `embedder`, `reranker`, `indexer`, `agent`,
  `ui-proxy`, `mcp`, `serena`, `tools`, `app`, `trainer`). Not on the
  `docs`, `devtools`, `smoke-test`, and `fetch-models` helpers.
- Read-only root filesystem on `agent`, `ui-proxy`, `mcp`, `tools`, and
  `app`, with narrowly-scoped `tmpfs` mounts only where genuinely needed
  (`/tmp`, and `tools`' `/workspace/scratch`). `serena` and the model
  servers have a writable root.
- `app`, `tools`, `agent`, and `indexer` only ever get read-only bind mounts
  of the source tree (`${CODEBASE_PATH:-./sample}:/workspace/app:ro`).
  `serena` (`tools-net`, not published) is the only process whose checkout
  mount is read-write, including `/checkouts/<id>`. The model reaches it
  only through the named write tools above. There is no commit and no push.
  Pointing `CODEBASE_PATH` at a larger tree widens what `tools` can read,
  still read-only.
- The `docs` service (profile `docs`) publishes `8000:8000` on every interface,
  unlike the loopback-only ports above. Stop it when not reading the docs.

## Authentication

- Every `agent` route except `GET /ui`, `/health`, and `/metrics` requires a
  static bearer token (`AGENT_BEARER_TOKEN`, set in `.env` - **never commit a
  real value**, `.env` is gitignored; compared in constant time). An unset or
  empty token is `401` for everything, not open access.
- `mcp` is published on **loopback only** (`127.0.0.1:8092`) and forwards
  `ask` to that same `/chat` with the bearer. Do not publish `0.0.0.0`.
- Grafana (`obs` profile) is published on **loopback only** (`127.0.0.1:3000`).
  The admin password comes from `GRAFANA_ADMIN_PASSWORD` in `.env`, not from
  the compose file. Do not publish `0.0.0.0`.
- `agent` is reached from the host through `ui-proxy` on **loopback only**
  (`127.0.0.1:8090`) for `GET /ui`. `agent` itself stays on internal networks
  (plus `publish-net` on `evo-x5` so it can call the host `llama-server`;
  that network has no default route).
  That page does not embed the token. The proxy forwards every path, so the
  unauthenticated `/metrics` (Prometheus text, no secrets) is also reachable on
  that loopback port. Every other route still requires the bearer. Do not
  publish `0.0.0.0`.
- `tools` and `serena` have no authentication of their own - they
  rely on network segmentation (only `agent` can reach them). Don't attach
  them to any other network without adding an equivalent control.

## Model integrity

- `scripts/container/fetch_models.py` verifies a SHA256 checksum after downloading
  each model file. Every GGUF the script knows (chat, embedding, reranker,
  test, the optional `EXTRA_MODELS`, and the Mistral shards) has a real
  checksum sourced from the model repo's own Git LFS metadata (the LFS `oid`
  for an LFS-tracked file *is* its SHA256 - see the comments next to each
  `ModelSpec` in that script for the exact source URL). A checksum
  mismatch is fatal: the script deletes the file and exits non-zero rather
  than silently trusting it. `sha256=None` remains supported for a future
  model added without a pin yet, but degrades to a loud runtime warning
  instead of verification - not the steady state for any pinned model.
- Two gaps: a file already on disk is skipped without being re-hashed (use
  `--force` to re-download), and the Hugging Face QLoRA train base
  (`--hf-train-base`, `snapshot_download`) has no checksum at all.

## Reproducibility / supply chain

- Every base image, the Serena image (`ghcr.io/oraios/serena:2.1.0`), the exact
  `llama.cpp` commit (`b10216`), and every model
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
