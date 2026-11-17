# Agent API reference

Reading note of [`CONTRACTS.md`](../../../CONTRACTS.md). If this page disagrees with that file, the file wins.

The `agent` service is what `app` and `mcp` call (see
[Networking](../../architecture/networking.md)). It exposes `POST /chat`,
`POST /chat/stream`, `POST /chat/cancel`, `POST /review`, `POST /lookup`, `POST /read`,
`POST /check`, `POST /lsp/outline`, `POST /lsp/calls`, `POST /lsp/types`,
`GET /models`, `POST /models/select`, `GET /ui`, `GET /health`, and `GET /metrics`.

Base URL inside the compose network: `http://agent:8090` (`AGENT_PORT`,
default `8090` per `CONTRACTS.md`). The host publish is **loopback only**:
`127.0.0.1:8090`. Open `http://127.0.0.1:8090/ui`, paste `AGENT_BEARER_TOKEN`
(kept in `sessionStorage`), and the page posts to `/chat/stream` on the same
origin. `GET /ui`, `GET /health`, and `GET /metrics` have no bearer; every
other route needs it. `ui-proxy` forwards every path (request body limit
1 MiB), so `/metrics` is also reachable on that loopback port.

## `POST /chat`

Ask the assistant a question about the codebase. The agent will retrieve
relevant code context (if any has been indexed), optionally call tools to
verify claims (build/test/lint/etc.), and return a final answer.

### Authentication

Required on every request:

```
Authorization: Bearer <AGENT_BEARER_TOKEN>
```

`AGENT_BEARER_TOKEN` is set in `.env` (see `.env.example`). Requests with a
missing or incorrect header receive `401 Unauthorized` and are never
forwarded to `llm`/`embedder`/`qdrant`/`tools`. If the token is unset or
empty on the server, every request gets `401`.

### Request

```json
{
  "query": "How does the indexer chunk C++ files?"
}
```

| Field   | Type   | Required | Description                          |
|---------|--------|----------|--------------------------------------|
| `query` | string | yes      | The natural-language question to ask. |
| `conversation_id` | string | no | Prior turn's id to keep history (in-memory, TTL). At most 80 characters. |
| `workspace` | string | no | Host path of a registered checkout (at most 1000 characters). The `X-Workspace` header fills it when the body field is empty. Selects that repository's collection and `/checkouts/<id>` mount. An unregistered path is HTTP 400 (`workspace is not registered: ...`); `/chat/stream` returns that 400 before the stream opens. |
| `instructions` | string | no | Extra system-prompt text for this request only, at most 4000 characters. |
| `max_reply_tokens` | integer | no | Reply cap, 32 to 1024. |
| `tools` | boolean | no | Default `true`. `false` sends no tool schema (MCP `ask` uses this). |

### Response `200 OK`

```json
{
  "answer": "The indexer walks the mounted tree with a tree-sitter-based C++ parser and splits each file into function/class-level chunks (see [1] services/indexer/chunker.py). Each chunk is embedded and upserted into Qdrant keyed by a hash of file path + symbol name + start line, so re-indexing is idempotent.",
  "sources": [
    {
      "file_path": "indexer/chunker.py",
      "start_line": 40,
      "end_line": 88,
      "symbol_name": "chunk_file"
    }
  ],
  "used_context": true,
  "tool_calls": []
}
```

| Field          | Type      | Description                                                                                   |
|----------------|-----------|-------------------------------------------------------------------------------------------------|
| `answer`       | string    | The model's final answer.                                                                       |
| `sources`      | array     | Retrieved code chunks the answer may cite. `[]` if `used_context` is `false`.                   |
| `used_context` | boolean   | Whether repository-specific RAG context was available and used for this answer.                 |
| `tool_calls`   | array     | Every tool invocation made while answering, in order. `[]` if no tools were called.              |
| `conversation_id` | string | Pass this back on the next request to continue the thread. |
| `route`        | string    | `direct` or `code` (default `code`).                                                            |
| `reflection`   | object    | Optional, `{removed: N}`; see below.                                                            |
| `nudges`       | integer   | Optional: how many times the loop refused a prose reply while a failure was open or edits were unverified. Omitted at 0. |

Each `sources[]` entry:

| Field         | Type            | Description                                    |
|---------------|-----------------|-------------------------------------------------|
| `file_path`   | string          | Repo-relative path of the source file.          |
| `start_line`  | integer         | First line of the chunk (1-indexed).            |
| `end_line`    | integer         | Last line of the chunk.                         |
| `symbol_name` | string \| null  | Function/class/symbol name the chunk covers, if known. |

Each `tool_calls[]` entry:

| Field            | Type   | Description                                              |
|------------------|--------|-----------------------------------------------------------|
| `name`           | string | One of the registered tool names (see [Tool calling](../../concepts/tool-calling.md)). |
| `arguments`      | object | The arguments the model passed to the tool.                |
| `result_summary` | string | A short human-readable summary of the tool's result.       |

### `used_context: false` is a normal response, not an error

`route` is `direct` or `code` on every successful response. `direct` means
the question was not about the repository: retrieval is skipped, tool
schemas are not attached, and the answer does not mention an empty index.
`code` is the repository path.

If a `code` question finds nothing indexed yet, or the search comes back
empty, the agent does not fail the request. It answers from the model's
general knowledge, sets `used_context: false`, returns an empty `sources`
array, and the answer text says explicitly that no repository context was
used. See [RAG, explained](../../concepts/rag-explained.md).

### Example: no indexed context available

```json
{
  "answer": "I don't have any indexed context for this repository yet, so I can't point to specific files. In general, a C++ code indexer typically walks the source tree, parses each file (often with a real parser like tree-sitter or libclang rather than regex), and splits it into function- or class-sized chunks for embedding.",
  "sources": [],
  "used_context": false,
  "tool_calls": []
}
```

### Example: with a tool call

```json
{
  "answer": "Yes - I ran the release build and it compiled cleanly with no warnings.",
  "sources": [],
  "used_context": false,
  "tool_calls": [
    {
      "name": "build",
      "arguments": { "preset": "release" },
      "result_summary": "build succeeded"
    }
  ]
}
```

### Error responses

| Status | When                                                        |
|--------|-------------------------------------------------------------|
| `400`  | `workspace` (body or `X-Workspace`) is not a registered checkout. |
| `401`  | Missing or incorrect `Authorization` header (or no token configured). |
| `422`  | Request body doesn't match the schema (e.g. missing `query`, `max_reply_tokens` out of range, `conversation_id` over 80 characters).|
| `503`  | `AGENT_MAX_CONCURRENT` `/chat` slots are already taken.     |

When `AGENT_REFLECT=1`, a successful response may include
`"reflection": {"removed": N}` after a second LLM pass that only deletes
sentences contradicted by the cited chunks. The field is omitted when
nothing was removed.

`GET /metrics` (no auth) is Prometheus text. The agent listens on `core-net`,
`client-net`, and `tools-net`, and `ui-proxy` forwards it to
`127.0.0.1:8090` as well.

## `POST /chat/stream`

Same JSON body and bearer token as `/chat`. The response is
`text/event-stream` (SSE):

| Event | Data |
|-------|------|
| `meta` | `{conversation_id}` first, so the client can cancel before `done`. |
| `route` | `{route, by}` before retrieval. `route` is `direct` or `code`. `by` is `rules` or `model`. `direct` skips the index and the tool schemas. |
| `rag` | `{used_context, num_sources, sources}` after retrieval. Omitted when `route` is `direct`. |
| `context` | `{used, window, ratio, compressed}` after the token budget. The page draws the fill meter from this. |
| `activity` | `{phase}`, `generating` while the model is writing. |
| `tool_delta` | `{index, name, arguments}` as a tool call's arguments stream in. |
| `tool_call` | `{name, arguments}` before the call to `tools` or to the Serena sidecar |
| `tool_result` | `{name, result_summary, ok}` after |
| `token` | `{text}`, one model token (or chunk) of the final answer |
| `nudge` | `{reason, tool?, remaining}` when a prose reply is refused and the loop continues. `reason` is `tool_failed`, `needs_build`, or `no_tool`; `tool` is set for the first two; `remaining` counts down 2, 1, 0. |
| `done` | the same object as `POST /chat` |
| `error` | `{message}` if the turn fails after the stream opened |

`llm-client` streams tokens by default; `--no-stream` uses `/chat`.
`--repl` (also `python scripts/deploy.py chat`) keeps `conversation_id`
across lines.

The agent is designed so that downstream failures (embedder/Qdrant/LLM/tools
being slow, empty, or unreachable) degrade the *content* of a `200` response
rather than surfacing as `5xx`. See [Tool calling](../../concepts/tool-calling.md)
for the malformed-tool-call and max-iteration fallback behavior, and
[RAG, explained](../../concepts/rag-explained.md) for the retrieval fallback
behavior.

### curl example

```bash
curl -s http://agent:8090/chat \
  -H "Authorization: Bearer $AGENT_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "How does the indexer chunk C++ files?"}' | jq
```

## `GET /models` and `POST /models/select`

Bearer token required. `GET /models` returns the chat GGUFs the router
knows about, each `{id, status, failed}`, plus `active` (the id the next
completion will name). Ids have no `.gguf` suffix. Embedder and reranker
files are omitted.

`POST /models/select` body: `{"model": "<id>"}`. The agent unloads any
other loaded model, asks llama-server to load this one, and waits until
`status` is `loaded`, then returns `{"model": {id, status, failed}}`. `404`
if the id is not a chat model. `503` if the load fails or times out (the
agent polls every 2 s for at most 900 polls; a 14B load is on the order of
half a minute). `GET /models` is also `503` when the router is unreachable.

`llm` must be in router mode (`LLM_ROUTER=1`, the default): no `-m` on
the parent process, `--models-dir /models`, `--models-max 1`.

## `POST /chat/cancel`

Bearer token required. Body: `{"conversation_id": "<id from meta or done>"}`.

Stops that conversation's in-flight turn: the model HTTP call is cancelled
and awaited (llama-server does not keep generating) and the tool loop does
not start another round. Works for `POST /chat` as well as `/chat/stream`
when the client sent `conversation_id`. Response: `{"stopped": true}` when
a turn was running, `false` when nothing matched. The stream then ends with
`done.answer` equal to `Stopped.` A second `/chat` or `/chat/stream` with
the same id waits until that turn has stored its trace, then runs. It does
not return `409`.

## `POST /lookup`

Bearer token required. Body:

```json
{ "query": "Where is Mat declared?", "workspace": "D:/rest/opencv", "include_candidates": false }
```

`workspace` selects a registered checkout (`X-Workspace` does the same).
The response is `hits`: path, line range, symbol, a short excerpt (clipped
to 400 characters), and `source` (`index` or `working_copy`). No chat model.
On a registered checkout, up to 6 dirty C/C++ files (from `git status` or
`svn status` through `tools`) are re-read and come first as `working_copy`
hits; a checkout with no VCS re-reads only the lines of each indexed hit.

`include_candidates` defaults to false. MCP leaves it false. When true,
the body also has `retrieval`:

| Field | Stage |
|---|---|
| `candidates` | Hybrid prefetch, before the reranker |
| `reranked` | After the reranker, before the noise filter |
| `index_hits` | What a clean tree would show in `hits` (the indexed top 5, no working-copy overlay, no excerpt) |
| `reranker_used` | True only when the reranker answered |

Each of those rows is `file_path`, `start_line`, `end_line`, `symbol_name`,
`score`. The baseline in [Retrieval baseline](../verification/retrieval-baseline.md)
reads this trace.

## `POST /read`, `POST /check`, `POST /lsp/*`

Bearer token required. Fact routes that forward one `tools` call for the
selected checkout (`workspace` in the body or `X-Workspace`) and never
call the chat model:

| Route | Body | Returns |
|---|---|---|
| `POST /read` | `{path, start_line?, end_line?}` | `{path, start_line, end_line, total_lines, truncated, text}` (`GET /read-file` on `tools`, 400 lines max; errors: `400` when `tools` rejects the path, `503` when it is unreachable, `502` when it answers non-JSON) |
| `POST /check` | `{action: build\|test\|compile, preset?, path?, filter?}` | `{action, ok, exit_code, stdout, stderr, truncated}`; logs clipped at 4000 characters |
| `POST /lsp/outline` | `{path, preset?}` | clangd `name kind line` lines, same clipped shape |
| `POST /lsp/calls`, `POST /lsp/types` | `{path, line, character, direction?, preset?}` | `name path:line` lines, same clipped shape |

MCP `lookup`, `check`, `calls`, and `types` are thin wrappers over `/lookup`,
`/check`, `/lsp/calls`, and `/lsp/types`. MCP has no `outline` tool and no
`read` tool; `/lsp/outline` and `/read` are for other clients.

## `GET /health`

Liveness probe used by the compose healthcheck. No authentication required.

### Response `200 OK`

```json
{ "status": "ok" }
```

## Correlation / tracing

Every `/chat` call is assigned a fresh request ID (`uuid4`) that is:

- forwarded as an `X-Request-Id` header on every outbound call the agent
  makes to `llm`, `embedder`, `qdrant`, and `tools` for that request, and
- included in every structured JSON log line the agent emits for that
  request (one JSON object per line on stdout).

Logging is `structlog` (`services/common/logging.py`, shared with the
indexer and trainer). The request ID is bound once per `/chat` call with
`structlog.contextvars`, so every line emitted while serving it carries
`request_id` without each module passing it around. Each line has at least
`event`, `component` (`main`, `rag`, `tool_loop`, ...), `level` and an ISO
`timestamp`; extra keyword arguments become JSON fields:

```json
{"component": "tool_loop", "request_id": "3f0c…", "iteration": 0, "reason": "invalid arguments for git_log: max_count: Input should be less than or equal to 50", "level": "info", "timestamp": "2026-09-22T16:02:10.034966Z", "event": "tool_call_malformed_retry"}
```

`LOG_LEVEL` (default `INFO`) sets the minimum level. If you need to trace a
single `/chat` call across services, grep the logs for that request ID.

## Available tools

The model sees **39 named tools**: 16 HTTP specs from
`services/common/tool_models.py` (`build`, `build_step`, `compile_file`,
`format_check`, `git_*`, `lint_clang_tidy`, `lsp_calls`, `lsp_types`,
`static_analysis`, `svn_*`, `test`) that the agent forwards to
[`tools`](tools-api.md), and 23 Serena names from
`services/agent/serena_named_tools.py` (reads, symbol navigation, and 11
writes) that it forwards to the sidecar. The full list, the editing phase, and the
loop mechanics are in [Tool calling](../../concepts/tool-calling.md). `ask`
on MCP does not receive any of them (`tools: false`).

## `POST /review`

Same bearer as `/chat`, and the same `AGENT_MAX_CONCURRENT` slot.

```json
{ "from": "HEAD~1", "to": "HEAD" }
```

The body has no `workspace` field, and `X-Workspace` is not read: the review always runs on the
default tree (`CODEBASE_PATH`) and only works on a git tree (it uses
`/git/diff-names` and `/git/diff`, never `svn`). The agent asks `tools` for
`git diff --name-only` (`POST /git/diff-names`, not an LLM tool), then one
model call per file (at most 8, at most 5 comments per file). A comment is
kept only when `snippet` (at least 8 characters) appears on a new-file line of that diff.
If listing the files fails, the response is `ok: false` with an `error`.

```json
{
  "ok": true,
  "from": "HEAD~1",
  "to": "HEAD",
  "files": ["src/a.cpp"],
  "truncated": false,
  "dropped": 0,
  "comments": [
    {"path": "src/a.cpp", "line": 21, "snippet": "return leaked;", "text": "the pointer is not freed"}
  ]
}
```
