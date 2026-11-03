# Tools API reference

The `tools` service (`services/tools/`) exposes a **fixed, curated set of
endpoints** for building/testing/linting/searching/reading the C++ project -
there is no generic shell/exec passthrough anywhere. This matters because an
LLM decides when to call these endpoints: every input is treated as
untrusted, every subprocess is invoked as an argv list with `shell=False`,
and every caller-supplied path is re-validated server-side regardless of
where it came from. See [Security](../architecture/security.md) and
[Tool calling](../concepts/tool-calling.md) for the bigger picture.

- **Base URL:** `TOOLS_BASE_URL` (`http://tools:8091` inside compose)
- **Network:** `tools-net` only - reachable from `agent`, nowhere else
  (see [Networking](../architecture/networking.md))
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

!!! note "Confirmed consistent with the indexer - no adjustment needed"
    `indexer` computes Qdrant's `file_path` payload field as the path
    *relative to `INDEXER_SOURCE_ROOT`* (default `/workspace/app` - see
    `services/indexer/main.py`), which is the exact same root this service
    resolves paths against. So `file_path` values coming back from a Qdrant
    search (e.g. `src/main.cpp`) are already directly usable in a `tools`
    call with no `app/`-stripping or any other adjustment - `agent` can pass
    them straight through to `/read-file` et al. This was flagged as an
    open question during initial development and has since been verified
    against the indexer's actual implementation and recorded in
    `CONTRACTS.md`.

Every response includes the same four base fields, plus endpoint-specific
ones:

```json
{ "ok": true, "stdout": "...", "stderr": "...", "exit_code": 0 }
```

`exit_code: -1` is a reserved sentinel meaning **the request was rejected
before any subprocess ever ran** (bad preset, bad path, empty `paths`/
`pattern`, etc.) - as opposed to a real `0`-`255` exit code from a tool that
actually executed. `exit_code: 124` means a command hit its timeout (see
below), matching the conventional exit code the POSIX `timeout` utility
uses.

| HTTP status | Meaning |
|---|---|
| `200` | request processed (check `ok`/`exit_code` for the tool's own result - a failing build is still a `200`) |
| `400` | invalid input (bad preset name, empty `paths`, empty `pattern`, path traversal/absolute path/symlink escape, etc.) |
| `404` | `/read-file`, `/list-files`: path doesn't exist |
| `413` | `/read-file` only: file exceeds the size cap |
| `422` | request validation (e.g. `max_results` outside its 1..N range, `start_line` < 1) |
| `500` | unexpected server-side error (e.g. a required tool binary is missing from the image) |

## `GET /health`

```bash
curl http://tools:8091/health
```

```json
{ "ok": true, "service": "tools", "missing": [] }
```

With `TOOLS_HEALTH_STRICT=1` (compose default) a missing required binary
(`cmake`, `ctest`, `clang-tidy`, `clang-format`, `clangd`, `cppcheck`, `rg`, `git`)
turns this into HTTP **503**. Unit tests leave the flag off so `/health`
stays 200 and lists `missing`.

`GET /metrics` is Prometheus text (internal networks only).

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

Allow-listed presets (validated **before** any subprocess runs - an
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
the time you see the response) - useful for correlating with server logs.

!!! note "How the scratch directory actually gets used - read this before touching `handlers/build.py`"
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
subprocess step independently (configure, then build).

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
`ctest --test-dir <scratch>/build --output-on-failure`) when configure and
build both succeeded. Optional `"filter": "json_round_trip"` is appended
as `ctest -R` (allow-listed regex).

## `POST /compile`

Configure once per (project root, preset) - the cache lives under
`/workspace/scratch/cmake-cache/<root>/<preset>`, where `<root>` is derived
from the resolved mount so the default tree and each `/checkouts/<id>`
never share a `CMakeCache.txt` - then compile a single C/C++ source via
`compile_commands.json`. The `/lsp/*` endpoints reuse the same cache.

```bash
curl -X POST http://tools:8091/compile \
  -H 'Content-Type: application/json' \
  -d '{"path": "src/AgentClient.cpp", "preset": "release"}'
```

Non-source paths and unknown presets are 400 before any subprocess.

## `POST /git/log`, `/git/blame`, `/git/diff`, `/git/show`

Read-only git on the mounted project (needs a `.git` at
`TOOLS_PROJECT_ROOT`). Refs must match `HEAD`, `HEAD~N`, a hex sha, or a
simple branch/tag name - no `..` ranges, no option-looking strings.
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

## `POST /lsp/definition`, `/lsp/references`, `/lsp/hover`, `/lsp/symbols`, `/lsp/outline`, `/lsp/calls`, `/lsp/types`

clangd runs in this container, pointed at the `/compile` cache
(`compile_commands.json`). Positions in the request are 1-based. `stdout`
is `path:line:col` lines (hover is plain text), at most 40 locations, and
only for URIs under the mounted project. `/lsp/outline` lists `name kind line`.
`/lsp/calls` (`direction`: `in`, `out`, `both`) and `/lsp/types`
(`direction`: `super`, `sub`, `both`) list `name path:line`. A missing
`clangd` binary or a missing compile database returns `ok: false` and
`exit_code: -1`.

```bash
curl -X POST http://tools:8091/lsp/definition \
  -H 'Content-Type: application/json' \
  -d '{"path": "src/AgentClient.cpp", "line": 12, "character": 8}'
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
`.clang-tidy` at runtime - passed explicitly via `--config-file=`).

## `POST /format/check`

```bash
curl -X POST http://tools:8091/format/check \
  -H 'Content-Type: application/json' \
  -d '{"paths": ["src/main.cpp"]}'
```

Same request shape as `/lint/clang-tidy`. Runs
`clang-format --dry-run --Werror --style=file:/etc/tools-config/.clang-format <paths>`
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

## `POST /search`

Ripgrep, scoped **only** to `TOOLS_PROJECT_ROOT` - the search root is always
chosen by the service, never the caller, so there's no path-traversal risk
here (unlike the other endpoints, `/search` doesn't call `path_safety` at
all).

```bash
curl -X POST http://tools:8091/search \
  -H 'Content-Type: application/json' \
  -d '{"pattern": "AgentClient", "regex": false}'
```

Request body:

| Field | Type | Required | Notes |
|---|---|---|---|
| `pattern` | string | yes, non-empty | passed as a single argv element - never shell-interpolated |
| `regex` | bool | no, default `false` | `false` adds `--fixed-strings`; `true` treats `pattern` as a ripgrep regex |
| `max_results` | int | no, default `50` | 1..100; matching lines beyond this are dropped and reported via `truncated` |

Response:

```json
{
  "ok": true,
  "stdout": "/workspace/app/src/AgentClient.hpp:10:class AgentClient {\n...",
  "stderr": "",
  "exit_code": 0,
  "pattern": "AgentClient",
  "regex": false,
  "total_matches": 3,
  "truncated": false
}
```

`exit_code: 1` (no matches found) is treated as `ok: true` - it's a normal,
successful search outcome, not an error. Only `exit_code >= 2` (a genuine
`rg` error, e.g. an invalid regex) is `ok: false`.

Bounded output: lines are cut at 300 columns (`rg --max-columns`), and when
more than `max_results` lines match, `stdout` keeps the first `max_results`,
`truncated` is `true`, `total_matches` carries the real count and `stderr`
tells the caller to narrow the pattern. This is what keeps a search for
`std::` from flooding the model's context window.

## `GET /list-files`

```bash
curl 'http://tools:8091/list-files?path=src&glob=*.hpp'
```

`rg --files` scoped to the project root (so `.gitignore` rules apply - build
outputs and vendored trees are skipped). Gives the model a way to discover
real paths before calling `/read-file`.

Query parameters:

| Field | Type | Required | Notes |
|---|---|---|---|
| `path` | string | no, default root | sub-directory, validated via `path_safety.validate_path`; must be a directory (`400` otherwise, `404` if missing) |
| `glob` | string | no | ripgrep glob (`*.hpp`, `src/**/*.cpp`), passed as its own argv element |
| `max_results` | int | no, default `200` | 1..500 |

Response:

```json
{
  "ok": true,
  "stdout": "include/AgentClient.hpp\nsrc/AgentClient.cpp\nsrc/main.cpp\n",
  "stderr": "",
  "exit_code": 0,
  "path": ".",
  "glob": null,
  "files": ["include/AgentClient.hpp", "src/AgentClient.cpp", "src/main.cpp"],
  "total_files": 3,
  "truncated": false
}
```

Paths are sorted, project-root-relative, forward-slash only.

## `GET /read-file`

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
  `413` rather than truncated silently - comfortably above any single
  source file in this project while bounding worst-case response size.
- `path` in the response echoes the caller's own input, not the resolved
  absolute container path, so internal mount layout is never leaked.
- Non-UTF-8 bytes are decoded with `errors="replace"` rather than crashing
  (so binary files come back with replacement characters instead of a
  `500`).
- A missing file returns `404`; a path that resolves to a directory returns
  `400`.

## Further reading

- [Adding a new tool](../guides/adding-a-new-tool.md) - how to extend this
  allow-list.
- [Security](../architecture/security.md) - the threat model this service
  is built around.
- [Tool calling](../concepts/tool-calling.md) - how `agent` decides when to
  call these endpoints.
