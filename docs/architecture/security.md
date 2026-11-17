# Security

This is the reader-friendly summary; `SECURITY.md` at the repo root is the
authoritative checklist.

## Threat model

The main thing this architecture defends against isn't an external attacker
- it's the **LLM itself**. `agent` lets a model decide when to call
`tools`, and a model's tool-call arguments are, from the system's
perspective, just as untrusted as user input from the open internet. Every
control below follows from taking that seriously.

## Layer 1: network segmentation

Three internal-only Podman networks (`client-net`/`core-net`/`tools-net`)
mean a compromised service can only reach the peers its segment allows.
See [Networking](networking.md). `tools-net` holds `agent`, `tools`,
and the `serena` sidecar; `agent` is the only client of the other two, and
none of them is published. They are unreachable from `llm`/`embedder`/`qdrant`
and vice versa, in both directions.

## Layer 2: the `tools` service's allow-list

There is no generic shell/exec endpoint anywhere in this system. `tools`
exposes a fixed allow-list (`/build`, `/build-step`, `/test`, `/compile`,
`/git/*`, `/svn/*`, `/lsp/outline`, `/lsp/calls`, `/lsp/types`, `/lint/clang-tidy`, `/format/check`,
`/static-analysis`, `/read-file`, plus `/cancel`,
`/health`, and `/metrics`). The model can only name the 16 specs of
`TOOL_SPECS`; the other routes are agent-only, so
the allow-list is the boundary. Every
subprocess it runs is invoked as an argv list with `shell=False`. There
is no code path where a string gets handed to a shell for interpretation.
Every path a caller supplies is re-validated server-side against the
mounted project root, resolving symlinks *before* checking containment
(so a symlink planted inside the mount can't be used to escape it). CMake
preset names and git refs are validated against allow-lists before any
subprocess starts.

## Layer 2b: the Serena sidecar (reads and edits)

The model reads and edits code through the `serena` sidecar, not through
`tools`. It has no authentication and a read-write checkout mount, so the
agent filters first: `services/agent/serena_policy.py` refuses absolute paths,
`..`, `.git`, `.svn`, `.env*` segments, paths resolving outside the registered
checkout, and an oversized `max_answer_chars`. Only 23 named Serena tools are
in the model's schema (11 of them write), and `config/serena/serena_config.yml`
removes shell and dashboard tools. A successful write opens the *editing* phase:
`build`/`test`/`compile_file` are refused until one `build_step` succeeds. This
is a workflow guard, not a permission boundary. See
[Tool calling](../concepts/tool-calling.md) and `SECURITY.md` (including a
known limitation about registered checkouts).

## Layer 3: isolation between requests

Every `/build` and `/test` call gets a fresh, disposable scratch directory
(`tempfile.mkdtemp()`), always cleaned up whether the command succeeded or
failed. Concurrent requests never share a build directory. `/compile` and
`/build-step` reuse a cache keyed by (project root, preset), so a registered
checkout never shares a `CMakeCache.txt` with the default tree.

## Layer 4: container hardening

Applied per service in `compose.yaml`: non-root users, `cap_drop: ["ALL"]`,
`no-new-privileges` on every runtime service, and read-only root filesystems
on `agent`, `ui-proxy`, `mcp`, `tools`, and `app` (with narrowly-scoped
`tmpfs` mounts only where a service genuinely needs to write, e.g. `tools`'
scratch directory). The pulled `serena` image keeps a writable root and its
user is not verified. Only `serena` mounts the checkout read-write.

## Layer 5: authentication on the exposed APIs

Every `agent` route except `GET /ui`, `/health`, and `/metrics` requires a static
bearer token (`AGENT_BEARER_TOKEN`; an empty token is `401`, not open access), defense in depth even though `client-net` already
limits who can reach it. `mcp` lives on the same network, holds that token,
and is the only other host-facing surface: Streamable HTTP on
`127.0.0.1:8092` (never `0.0.0.0`). Its tools are `lookup`, `check`,
`calls`, `types`, and a short `ask`. `ask` forwards to `/chat`
with no tool schema. `mcp` does not join `tools-net`. `ui-proxy` forwards every
path of the agent on `127.0.0.1:8090`, so the unauthenticated `/metrics` is
reachable there too. The NVIDIA `llm` container is also on
loopback (`127.0.0.1:8080`) so an editor can call the local model
directly. On `evo-x5` the host `llama-server` listens on `0.0.0.0:8080`
so the agent container can reach it; the page and MCP stay on loopback.
Embedder and Qdrant stay unpublished.

## Layer 6: reproducibility

Every base image, the Serena image tag, the exact `llama.cpp` commit, and every model file are
pinned in `CONTRACTS.md`. See the pinned-version audit in
[Testing](../guides/testing.md) for how that's kept from drifting.

## What this does *not* claim to defend against

This is a local development tool, not a multi-tenant production service:
there's no per-user authorization (one bearer token for the whole `agent`
API). Concurrent `/chat` calls are capped by `AGENT_MAX_CONCURRENT`.
`tools` has process timeouts and compose cgroup limits. See `SECURITY.md`
for the full checklist and known limitations.
