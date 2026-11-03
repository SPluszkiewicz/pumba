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
mean a compromised service can only reach the peers its segment allows -
see [Networking](networking.md). `tools` is unreachable from `llm`/
`embedder`/`qdrant` and vice versa, in both directions.

## Layer 2: the `tools` service's allow-list

There is no generic shell/exec endpoint anywhere in this system. `tools`
exposes a fixed allow-list (`/build`, `/test`, `/compile`, `/git/*`,
`/svn/*`, `/lsp/*`, `/lint/clang-tidy`, `/format/check`,
`/static-analysis`, `/search`, `/list-files`, `/read-file`, plus `/health`
and `/metrics`). Every
subprocess it runs is invoked as an argv list with `shell=False` - there
is no code path where a string gets handed to a shell for interpretation.
Every path a caller supplies is re-validated server-side against the
mounted project root, resolving symlinks *before* checking containment
(so a symlink planted inside the mount can't be used to escape it). CMake
preset names and git refs are validated against allow-lists before any
subprocess starts.

## Layer 3: isolation between requests

Every `/build` and `/test` call gets a fresh, disposable scratch directory
(`tempfile.mkdtemp()`), always cleaned up whether the command succeeded or
failed. Concurrent requests never share a build directory.

## Layer 4: container hardening

Applied per service in `compose.yaml`: non-root users, `cap_drop: ["ALL"]`,
`no-new-privileges`, and read-only root filesystems (with narrowly-scoped
`tmpfs` mounts only where a service genuinely needs to write - e.g. `tools`'
scratch directory).

## Layer 5: authentication on the exposed APIs

`agent`'s `/chat` (and `/chat/stream`) requires a static bearer token
(`AGENT_BEARER_TOKEN`) - defense in depth even though `client-net` already
limits who can reach it. `mcp` lives on the same network, holds that token,
and is the only other host-facing surface: Streamable HTTP on
`127.0.0.1:8092` (never `0.0.0.0`). Its tools are `lookup`, `check`,
`outline`, `calls`, `types`, and a short `ask`. `ask` forwards to `/chat`
with no tool schema. `mcp` does not join `tools-net`. `llm` is also on
loopback (`127.0.0.1:8080`) so an editor can call the local model
directly. Embedder and Qdrant stay unpublished.

## Layer 6: reproducibility

Every base image, the exact `llama.cpp` commit, and every model file are
pinned in `CONTRACTS.md` - see the pinned-version audit in
[Testing](../guides/testing.md) for how that's kept from drifting.

## What this does *not* claim to defend against

This is a local development tool, not a multi-tenant production service:
there's no per-user authorization (one bearer token for the whole `agent`
API). Concurrent `/chat` calls are capped by `AGENT_MAX_CONCURRENT`.
`tools` has process timeouts and compose cgroup limits. See `SECURITY.md`
for the full checklist and known limitations.
