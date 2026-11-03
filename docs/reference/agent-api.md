# Agent API reference

The `agent` service is what `app` and `mcp` call (see
[Networking](../architecture/networking.md)). It exposes `POST /chat`,
`POST /chat/stream`, `POST /chat/cancel`, `POST /review`, `POST /lookup`, `POST /read`,
`POST /check`, `POST /lsp/outline`, `POST /lsp/calls`, `POST /lsp/types`,
`GET /models`, `POST /models/select`, `GET /ui`, `GET /health`, and `GET /metrics`.

Base URL inside the compose network: `http://agent:8090` (`AGENT_PORT`,
default `8090` per `CONTRACTS.md`). The host publish is **loopback only**:
`127.0.0.1:8090`. Open `http://127.0.0.1:8090/ui`, paste `AGENT_BEARER_TOKEN`
(kept in `sessionStorage`), and the page posts to `/chat/stream` on the same
origin. `GET /ui` itself has no bearer.

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
forwarded to `llm`/`embedder`/`qdrant`/`tools`.

### Request

```json
{
  "query": "How does the indexer chunk C++ files?"
}
```

| Field   | Type   | Required | Description                          |
|---------|--------|----------|--------------------------------------|
| `query` | string | yes      | The natural-language question to ask. |
| `conversation_id` | string | no | Prior turn's id to keep history (in-memory, TTL). |
| `workspace` | string | no | Host path of a registered checkout. Same value as the `X-Workspace` header. Selects that repository's collection and mount. An unknown path is HTTP 400. |

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
| `name`           | string | One of the registered tool names (see below).             |
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
used. See [RAG, explained](../concepts/rag-explained.md).

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
| `401`  | Missing or incorrect `Authorization` header.                |
| `422`  | Request body doesn't match the schema (e.g. missing `query`).|
| `503`  | `AGENT_MAX_CONCURRENT` `/chat` slots are already taken.     |

When `AGENT_REFLECT=1`, a successful response may include
`"reflection": {"removed": N}` after a second LLM pass that only deletes
sentences contradicted by the cited chunks. The field is omitted when
nothing was removed.

`GET /metrics` (no auth) is Prometheus text, reachable on `core-net` only.

## `POST /chat/stream`

Same JSON body and bearer token as `/chat`. The response is
`text/event-stream` (SSE):

| Event | Data |
|-------|------|
| `meta` | `{conversation_id}` first, so the client can cancel before `done`. |
| `route` | `{route, by}` before retrieval. `route` is `direct` or `code`. `by` is `rules` or `model`. `direct` skips the index and the tool schemas. |
| `rag` | `{used_context, num_sources, sources}` after retrieval. Omitted when `route` is `direct`. |
| `context` | `{used, window, ratio, compressed}` after the token budget. The page draws the fill meter from this. |
| `activity` | `{phase}` — `generating` while the model is writing. |
| `tool_delta` | `{index, name, arguments}` as a tool call's arguments stream in. |
| `tool_call` | `{name, arguments}` before the HTTP call to `tools` |
| `tool_result` | `{name, result_summary, ok}` after |
| `token` | `{text}` — one model token (or chunk) of the final answer |
| `done` | the same object as `POST /chat` |
| `error` | `{message}` if the turn fails after the stream opened |

`llm-client` streams tokens by default; `--no-stream` uses `/chat`.
`--repl` (also `python scripts/deploy.py chat`) keeps `conversation_id`
across lines.

The agent is designed so that downstream failures (embedder/Qdrant/LLM/tools
being slow, empty, or unreachable) degrade the *content* of a `200` response
rather than surfacing as `5xx` - see [Tool calling](../concepts/tool-calling.md)
for the malformed-tool-call and max-iteration fallback behavior, and
[RAG, explained](../concepts/rag-explained.md) for the retrieval fallback
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
`status` is `loaded`. `404` if the id is not a chat model. `503` if the
load fails or times out (a 14B load is on the order of half a minute).

`llm` must be in router mode (`LLM_ROUTER=1`, the default): no `-m` on
the parent process, `--models-dir /models`, `--models-max 1`.

## `POST /chat/cancel`

Bearer token required. Body: `{"conversation_id": "<id from meta or done>"}`.

Stops that conversation's in-flight turn: the model HTTP call is dropped
and the tool loop does not start another round. Response:
`{"stopped": true}` when a turn was running, `false` when nothing matched.
The stream then ends with `done.answer` equal to `Stopped.`

## `POST /lookup`

Bearer token required. Body:

```json
{ "query": "Where is Mat declared?", "workspace": "D:/rest/opencv", "include_candidates": false }
```

`workspace` selects a registered checkout (`X-Workspace` does the same).
The response is `hits`: path, line range, symbol, a short excerpt, and
`source` (`index` or `working_copy`). No chat model.

`include_candidates` defaults to false. MCP leaves it false. When true,
the body also has `retrieval`:

| Field | Stage |
|---|---|
| `candidates` | Hybrid prefetch, before the reranker |
| `reranked` | After the reranker, before the noise filter |
| `index_hits` | The indexed top 5, before the working-copy overlay |
| `reranker_used` | True only when the reranker answered |

Each of those rows is `file_path`, `start_line`, `end_line`, `symbol_name`,
`score`. The baseline in [Retrieval baseline](../guides/eval-checkout.md)
reads this trace.

## `POST /read`, `POST /check`, `POST /lsp/*`

Bearer token required. Fact routes that forward one `tools` call for the
selected checkout (`workspace` in the body or `X-Workspace`) and never
call the chat model:

| Route | Body | Returns |
|---|---|---|
| `POST /read` | `{path, start_line?, end_line?}` | `{path, start_line, end_line, total_lines, truncated, text}` (`GET /read-file` on `tools`, 400 lines max) |
| `POST /check` | `{action: build\|test\|compile, preset?, path?, filter?}` | `{action, ok, exit_code, stdout, stderr, truncated}`; logs clipped at 4000 characters |
| `POST /lsp/outline` | `{path, preset?}` | clangd `name kind line` lines, same clipped shape |
| `POST /lsp/calls`, `POST /lsp/types` | `{path, line, character, direction?, preset?}` | `name path:line` lines, same clipped shape |

MCP `check`, `outline`, `calls`, and `types` are thin wrappers over these.

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
{"component": "tool_loop", "request_id": "3f0c…", "iteration": 0, "reason": "invalid arguments for search: max_results: Input should be less than or equal to 100", "level": "info", "timestamp": "2026-09-22T16:02:10.034966Z", "event": "tool_call_malformed_retry"}
```

`LOG_LEVEL` (default `INFO`) sets the minimum level. If you need to trace a
single `/chat` call across services, grep the logs for that request ID.

## Available tools

The model can call the following tools while answering (see
[Tool calling](../concepts/tool-calling.md) for the bounded-loop mechanics
and [Tools API reference](tools-api.md) for the underlying HTTP endpoints):

`build`, `test`, `compile_file`, `lint_clang_tidy`, `format_check`,
`static_analysis`, `search`, `list_files`, `read_file`, `git_log`,
`git_blame`, `git_diff`, `git_show`, `svn_status`, `svn_diff`, `svn_log`,
`lsp_definition`, `lsp_references`, `lsp_hover`, `lsp_symbols`,
`lsp_outline`, `lsp_calls`, `lsp_types`.

That is 23 names, generated from `services/common/tool_models.py`. `ask`
on MCP does not receive this list.

## `POST /review`

Same bearer as `/chat`, and the same `AGENT_MAX_CONCURRENT` slot.

```json
{ "from": "HEAD~1", "to": "HEAD" }
```

The agent asks `tools` for `git diff --name-only` (`POST /git/diff-names`,
not an LLM tool), then one model call per file (at most 8). A comment is
kept only when `snippet` appears on a new-file line of that diff.

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
