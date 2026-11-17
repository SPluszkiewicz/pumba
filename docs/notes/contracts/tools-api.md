# Tools API reference

Reading note of [`CONTRACTS.md`](../../../CONTRACTS.md). If this page disagrees with that file, the file wins.

The `tools` service (`services/tools/`) exposes a **fixed, curated set of
endpoints** for building, testing, linting, and the agent's own file read.
There is no generic shell/exec passthrough anywhere. This matters because an
LLM decides when to call these endpoints: every input is treated as
untrusted, every subprocess is invoked as an argv list with `shell=False`,
and every caller-supplied path is re-validated server-side regardless of
where it came from. See [Security](../../architecture/security.md) and
[Tool calling](../../concepts/tool-calling.md) for the bigger picture.

!!! note "Which routes the model can name"
    Only 16 routes are bound to a named tool (`TOOL_SPECS` in
    `services/common/tool_models.py`): `/build`, `/build-step`, `/test`,
    `/compile`, `/git/log|blame|diff|show`, `/svn/status|diff|log`,
    `/lsp/calls`, `/lsp/types`, `/lint/clang-tidy`, `/format/check`,
    `/static-analysis`. Reading, searching, listing, and symbol navigation
    moved to the Serena sidecar. `/read-file`, `/lsp/outline`, `/git/status`,
    and `/git/diff-names` are used by `agent` itself (fact routes, working-copy
    overlay, `/review`). Each section below says which case it is.

- **Base URL:** `TOOLS_BASE_URL` (`http://tools:8091` inside compose)
- **Network:** `tools-net` only (shared with `agent` and `serena`).
  Only `agent` calls it (see [Networking](../../architecture/networking.md))
- **Auth:** none at the HTTP layer; isolation is via network segmentation,
  not a bearer token (unlike `agent`'s `/chat`)
- **Content type:** all `POST` bodies and all responses are JSON

## The mounted project root

Every path-taking endpoint resolves caller paths **relative to the project
root for that request**. With no extra header, that root is
`TOOLS_PROJECT_ROOT` (default `/workspace/app`), the read-only mount of
`CODEBASE_PATH` (default `./sample`):

```yaml
volumes:
  - ${CODEBASE_PATH:-./sample}:/workspace/app:ro
```

A registered checkout is mounted at `/checkouts/<id>`. `agent` sends
`X-Project-Root` set to that mount. The header is accepted only when the
path is a direct child of `/checkouts` and the directory exists. Any other
value is HTTP 400. Paths in the body stay relative to that root, for
example `src/main.cpp`.

!!! note "Confirmed consistent with the indexer, no adjustment needed"
    `indexer` computes Qdrant's `file_path` payload field as the path
    *relative to `INDEXER_SOURCE_ROOT`* (default `/workspace/app`, see
    `services/indexer/main.py`), which is the exact same root this service
    resolves paths against. A `file_path` coming back from Qdrant
    (for example `src/main.cpp`) is already a `tools` path. `agent` passes
    it to `/read-file` with no `app/` prefix to strip. This was flagged as an
    open question during initial development and has since been verified
    against the indexer's actual implementation and recorded in
    `CONTRACTS.md`.

Every response includes the same four base fields, plus endpoint-specific
ones:

```json
{ "ok": true, "stdout": "...", "stderr": "...", "exit_code": 0 }
```

`exit_code: -1` is a reserved sentinel meaning **the request was rejected
before any subprocess ever ran** (bad preset, bad path, empty `paths`,
etc.), as opposed to a real `0`-`255` exit code from a tool that
actually executed. `exit_code: 124` means a command hit its timeout (see
below), matching the conventional exit code the POSIX `timeout` utility
uses.

| HTTP status | Meaning |
|---|---|
| `200` | request processed (check `ok`/`exit_code` for the tool's own result, a failing build is still a `200`) |
| `400` | invalid input (bad preset name, empty `paths`, path traversal/absolute path/symlink escape, etc.) |
| `404` | `/read-file`: path doesn't exist |
| `413` | `/read-file` only: file exceeds the size cap |
| `422` | request validation (e.g. `max_count` outside its range, `start_line` < 1) |
| `500` | unexpected server-side error (e.g. a required tool binary is missing from the image) |

## `GET /health`

```bash
curl http://tools:8091/health
```

```json
{ "ok": true, "service": "tools", "missing": [] }
```

With `TOOLS_HEALTH_STRICT=1` (deployed default from compose; the code
default is `0`) a missing required binary
(`cmake`, `ctest`, `clang-tidy`, `clang-format`, `clangd`, `cppcheck`, `rg`, `git`, `svn`)
turns this into HTTP **503**. Unit tests leave the flag off so `/health`
stays 200 and lists `missing`.

`GET /metrics` is Prometheus text (internal networks only).

## `POST /cancel`

No body. Kills the process group of the request whose `X-Request-Id` header
matches, and answers `204` whether or not something was running. `agent`
calls it on `tools` when the user stops a turn. Serena has
no such hook.

## `POST /build`

Runs `cmake` configure + build in a **fresh `tempfile.mkdtemp()` scratch
directory**, isolated per request and always cleaned up afterwards (success
or failure).

```bash
curl -X POST http://tools:8091/build \
  -H 'Content-Type: application/json' \
  -d '{"preset": "release"}'
```

Request body:

| Field | Type | Required | Notes |
|---|---|---|---|
| `preset` | string | no | one of the allow-listed preset names below; defaults to `TOOLS_CMAKE_PRESET` (`release`) if omitted |

Allow-listed presets (validated **before** any subprocess runs, an
unrecognized name never touches a subprocess argument):

| Preset | Equivalent `-D` flag |
|---|---|
| `release` | `-DCMAKE_BUILD_TYPE=Release` |
| `debug` | `-DCMAKE_BUILD_TYPE=Debug` |

Response (endpoint-specific fields beyond the base four):

```json
{
  "ok": true,
  "stdout": "----- configure -----\n...\n----- build -----\n...",
  "stderr": "----- configure -----\n\n----- build -----\n",
  "exit_code": 0,
  "preset": "release",
  "scratch_dir": "/workspace/scratch/tools-build-a1b2c3/"
}
```

`scratch_dir` is informational only (the directory is already removed by
the time you see the response), useful for correlating with server logs.

!!! note "How the scratch directory actually gets used, read this before touching `handlers/build.py`"
    `app/CMakePresets.json`'s `binaryDir` points *inside* the read-only
    `/workspace/app` mount, so it can't be used directly for an
    out-of-source, per-request build. Combining `--preset <name>` with an
    explicit `-B <scratch>` to relocate it is fragile and
    version-dependent (see the long comment at the top of
    `services/tools/handlers/build.py`), so this service bypasses
    `CMakePresets.json` for its own invocations entirely: it translates the
    allow-listed preset name into the equivalent `-D` cache variable and
    runs `cmake -S /workspace/app -B <scratch>/build -DCMAKE_BUILD_TYPE=...`
    directly. If `app/CMakePresets.json` ever gains cache variables beyond
    `CMAKE_BUILD_TYPE`, mirror them in `_PRESET_CACHE_VARIABLES`.

Timeout: `TOOLS_BUILD_TIMEOUT_SECONDS` (default `14400`), applied to each
subprocess step independently (configure, then build). The build step runs
with `-j <cpu count>`.

## `POST /build-step`

The incremental build the model uses after edits. Unlike `/build` it works in
the **persistent** cache (`/workspace/scratch/cmake-cache/<root>/<preset>`,
the same one as `/compile`): it configures once, then runs
`cmake --build <cache> --parallel [--target T]`, so a second call only
rebuilds what changed.

```bash
curl -X POST http://tools:8091/build-step \
  -H 'Content-Type: application/json' \
  -d '{"preset": "release", "target": "app"}'
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `preset` | string | no | same allow-list as `/build` |
| `target` | string | no | `^[A-Za-z0-9_.:/+-]{1,200}$`, no `..`; omitted builds the default target |

The response adds `target`, `build_dir`, and `repair`, a hint for the next
call: `{"kind": "none"}` on success; on failure
`{"kind": "compile_file", "path": "src/x.cpp"}` when the log cites exactly
one `.cpp`, otherwise `{"kind": "build_step"}` (also used when configure
itself fails). `agent` closes a failed `build_step` only with a later
successful `build_step`, and a successful one ends the editing phase (see
[Tool calling](../../concepts/tool-calling.md)).

## `POST /test`

Same scratch-dir/preset/timeout model as `/build`, with a `ctest` step
bolted on after a successful configure + build (a fresh scratch dir per
request means there's never an already-built tree lying around to test
against, so `/test` always builds first).

```bash
curl -X POST http://tools:8091/test \
  -H 'Content-Type: application/json' \
  -d '{"preset": "release"}'
```

Request/response shape is identical to `/build`, except the `stdout`/
`stderr` include a third `----- test -----` section (from
`ctest --test-dir <scratch>/build --output-on-failure --no-tests=error`) when
configure and build both succeeded. Optional `"filter": "json_round_trip"` is
appended as `ctest -R`. It must match `^[A-Za-z0-9_.:*?+|\\^ -]+$` (spaces
allowed) and may not start with `-`. Because of `--no-tests=error`, a filter
that matches no test is a failure, not a pass.

## `POST /compile`

Configure once per (project root, preset), the cache lives under
`/workspace/scratch/cmake-cache/<root>/<preset>`, where `<root>` is derived
from the resolved mount so the default tree and each `/checkouts/<id>`
never share a `CMakeCache.txt`, then compile a single C/C++ source via
`compile_commands.json`. `/build-step` and the `/lsp/*` endpoints reuse the
same cache. The cache directory is the `cmake-cache` named volume (nested in
the `/workspace/scratch` tmpfs), which `serena` mounts read-only so its
clangd sees the same `compile_commands.json`. The response adds `path` and
`build_dir`.

```bash
curl -X POST http://tools:8091/compile \
  -H 'Content-Type: application/json' \
  -d '{"path": "src/AgentClient.cpp", "preset": "release"}'
```

Only `.cpp`, `.cc`, `.cxx`, and `.c` paths are accepted. Other paths and
unknown presets are 400 before any subprocess.

## `POST /git/log`, `/git/blame`, `/git/diff`, `/git/show`

Read-only git on the mounted project (needs a `.git` at
`TOOLS_PROJECT_ROOT`). Refs must match `HEAD`, `HEAD~N`, a hex sha, or a
simple branch/tag name, no `..` ranges, no option-looking strings.
Output is capped at 200 lines (`truncated: true` when clipped).

```bash
curl -X POST http://tools:8091/git/log \
  -H 'Content-Type: application/json' \
  -d '{"path": "src/AgentClient.cpp", "max_count": 20}'
```

## `POST /git/diff-names`

Same body as `/git/diff` (`ref_a`, `ref_b`; `path` is ignored). Returns
`git diff --name-only --diff-filter=ACMR`. `agent` uses it for `POST /review`.
It is not an LLM tool.

## `POST /git/status`

No body. Runs `git status --porcelain=v1 -u` on the active root. `agent` uses
it for the working-copy overlay of `POST /lookup` on a git checkout (the
Subversion equivalent is `/svn/status`). It is not an LLM tool.

## `POST /svn/status`, `/svn/diff`, `/svn/log`

Read-only Subversion on the mounted working copy (needs a `.svn` directory
at `TOOLS_PROJECT_ROOT`). `svn status` lists uncommitted changes. `svn diff`
with no revisions is that changelist against `BASE`; `rev_from` and `rev_to`
must be sent together and each is `BASE`, `HEAD`, `COMMITTED`, `PREV`, or a
revision number. `svn log` is capped by `max_count` (1-50) and may contact
the repository. Output is capped at 200 lines. There is no commit or update.

```bash
curl -X POST http://tools:8091/svn/diff \
  -H 'Content-Type: application/json' \
  -d '{"path": "src/foo.cpp"}'
```

## `POST /lsp/outline`, `/lsp/calls`, `/lsp/types`

clangd runs in this container, pointed at the `/compile` cache
(`compile_commands.json`). Positions in the request are 1-based.
`/lsp/outline` lists `name kind line`. `/lsp/calls` (`direction`: `in`,
`out`, `both`) and `/lsp/types` (`direction`: `super`, `sub`, `both`) list
`name path:line`, at most 40, and only for URIs under the mounted project.
A missing `clangd` binary or a missing compile database returns `ok: false`
and `exit_code: -1`.

Who calls what: only `/lsp/calls` and `/lsp/types` are named tools
(`lsp_calls`, `lsp_types`); `/lsp/outline` is reached through `agent`'s
`POST /lsp/outline`. Serena's `find_declaration`,
`find_referencing_symbols`, and `find_symbol` are the model's symbol reads.

!!! warning "`direction: out` returns nothing"
    clangd answers `-32601` (method not found) to outgoing-call requests, and
    the handler maps that to an empty result. So `lsp_calls` with `out` yields
    no lines and `both` yields only callers.

```bash
curl -X POST http://tools:8091/lsp/calls \
  -H 'Content-Type: application/json' \
  -d '{"path": "src/AgentClient.cpp", "line": 12, "character": 8, "direction": "in"}'
```

## `POST /lint/clang-tidy`

```bash
curl -X POST http://tools:8091/lint/clang-tidy \
  -H 'Content-Type: application/json' \
  -d '{"paths": ["src/main.cpp", "src/AgentClient.cpp"]}'
```

Request body:

| Field | Type | Required | Notes |
|---|---|---|---|
| `paths` | array of strings | yes, non-empty | each validated independently via `path_safety.validate_path` |

Response:

```json
{
  "ok": false,
  "stdout": "src/main.cpp:12:5: warning: ...",
  "stderr": "",
  "exit_code": 1,
  "paths": ["/workspace/app/src/main.cpp", "/workspace/app/src/AgentClient.cpp"]
}
```

Runs the repo's own `.clang-tidy` config (baked into the image at
`/etc/tools-config/.clang-tidy` at build time, since the container only
mounts the project tree (`CODEBASE_PATH`) and never sees this repo's
`.clang-tidy` at runtime, passed explicitly via `--config-file=`). The path
comes from `TOOLS_CLANG_TIDY_CONFIG_FILE`, and the flag is passed only when
that file exists; otherwise clang-tidy falls back to its own lookup.

## `POST /format/check`

```bash
curl -X POST http://tools:8091/format/check \
  -H 'Content-Type: application/json' \
  -d '{"paths": ["src/main.cpp"]}'
```

Same request shape as `/lint/clang-tidy`. Runs
`clang-format --dry-run --Werror --style=file:/etc/tools-config/.clang-format <paths>`
(the style file is `TOOLS_CLANG_FORMAT_STYLE_FILE`; the `--style` flag is dropped when it is missing)
(non-zero exit = at least one file isn't correctly formatted; the diagnostic
detail is in `stderr`, per `clang-format`'s own behavior).

## `POST /static-analysis`

```bash
curl -X POST http://tools:8091/static-analysis \
  -H 'Content-Type: application/json' \
  -d '{"paths": ["src/main.cpp"]}'
```

Same request shape as `/lint/clang-tidy`. Runs
`cppcheck --quiet --enable=warning,style,performance,portability <paths>`.

## `GET /read-file`

!!! info "Agent-only"
    Not a named tool any more (the model uses Serena's `read_file`). `agent`
    calls it for `POST /read` and for the working-copy overlay of
    `POST /lookup`.

```bash
curl 'http://tools:8091/read-file?path=src/main.cpp'
curl 'http://tools:8091/read-file?path=src/main.cpp&start_line=401&end_line=800'
```

Query parameters:

| Field | Type | Required | Notes |
|---|---|---|---|
| `path` | string | yes | validated via `path_safety.validate_path` |
| `start_line` | int | no | 1-based first line to return |
| `end_line` | int | no | 1-based last line (inclusive); clamped to EOF and to 400 lines from `start_line` |

Response:

```json
{
  "ok": true,
  "stdout": "int main() { ... }\n",
  "stderr": "",
  "exit_code": 0,
  "path": "src/main.cpp",
  "size_bytes": 842,
  "start_line": 1,
  "end_line": 24,
  "total_lines": 24,
  "truncated": false
}
```

- Line cap: **400 lines per call**. Without a window, a longer file returns
  lines `1..400` with `truncated: true` and a `stderr` hint
  (`call again with start_line=401`). An explicit window wider than 400
  lines is clamped the same way. `start_line > total_lines` or
  `end_line < start_line` is a `400`.
- Size cap: **256 KiB** (`262144` bytes). Larger files are rejected with
  `413` rather than truncated silently, comfortably above any single
  source file in this project while bounding worst-case response size.
- `path` in the response echoes the caller's own input, not the resolved
  absolute container path, so internal mount layout is never leaked.
- Non-UTF-8 bytes are decoded with `errors="replace"` rather than crashing
  (so binary files come back with replacement characters instead of a
  `500`).
- A missing file returns `404`; a path that resolves to a directory or any
  other non-regular file returns `400`.

## Further reading

- [Adding a new tool](../../guides/adding-a-new-tool.md), how to extend this
  allow-list.
- [Agent API](agent-api.md), the only caller of these routes.
- [Security](../../architecture/security.md), the threat model this service
  is built around.
- [Tool calling](../../concepts/tool-calling.md), how `agent` decides when to
  call these endpoints.
