# Using the assistant

This is the day-to-day guide. If the words "model", "index", and "tool" are
new, read [Plain-language overview](../concepts/en-clair.md) first.
Install and the first question are in
[First run](../getting-started/first-run.md). Shapes of HTTP requests are
in [Agent API](../notes/contracts/agent-api.md). The editor doors are in
[VS Code et Continue.dev](vscode.md).

Three doors reach the same agent:

| Door | How | Checkout it works on |
|---|---|---|
| Page `/ui` | `http://127.0.0.1:8090/ui` | The default one (`CODEBASE_PATH`) |
| CLI `llm-client` | `python scripts/deploy.py ask` / `chat` | The default one (`CODEBASE_PATH`) |
| Editor (MCP, HTTP) | `X-Workspace` header or `workspace` field | The registered one it names |

The page and the CLI send no `workspace`. Only the MCP and HTTP doors can
pick a registered checkout. See [A repository of thousands of files](#a-repository-of-thousands-of-files).

## What a question does

The agent classifies the question, then either answers directly or works
on the repository. The page shows the route first.

| Route | When | What runs |
|---|---|---|
| `direct` | Plain arithmetic, a greeting, or a first message the classifier calls general knowledge | One chat completion. No index, no tools. |
| `code` | A file name, a code word or identifier, a build or test, any follow-up in a conversation, or anything undecided | Retrieval, then up to `AGENT_TOOL_LOOP_MAX_ITERATIONS` tool rounds (48 in `.env.example`). |

The rules decide first (`by: rules`). A code signal always wins, so a
greeting that names a symbol is `code`. A first message that no rule
decides goes to one short classification call (`by: model`), made only while `AGENT_GATE_CLASSIFY=1` (the default);
otherwise, or if the call fails, the question stays on `code`.

Retrieval embeds the question, searches Qdrant (dense + BM25, fused by
RRF), reranks when the reranker answers, and keeps 5 chunks. Rules in
`services/agent/rules/*.md` whose globs match those paths are appended, up
to 16,000 characters.

### Named tools

The model has no shell. It calls **named tools** with checked arguments;
an unknown argument is rejected and sent back to the model. The
model-facing schema is the HTTP specs in `services/common/tool_models.py`
plus the Serena names in `services/agent/serena_named_tools.py`. The loop
is described in [Tool calling](../concepts/tool-calling.md).

| Family | Names | Runs on |
|---|---|---|
| Verify | `build`, `test` (optional `filter`, a `ctest -R` regex), `compile_file`, `build_step`, `lint_clang_tidy`, `format_check`, `static_analysis` | `tools` |
| History, read-only | `git_log`, `git_blame`, `git_diff`, `git_show`, `svn_status`, `svn_diff`, `svn_log` | `tools` |
| Call and type hierarchy | `lsp_calls`, `lsp_types` | `tools` (clangd) |
| Read and search | `find_symbol`, `find_declaration`, `find_referencing_symbols`, `find_implementations`, `get_symbols_overview`, `get_diagnostics_for_file`, `get_diagnostics_for_symbol`, `restart_language_server`, `read_file`, `list_dir`, `find_file`, `search_for_pattern` | Serena sidecar |
| Write | `rename_symbol`, `replace_symbol_body`, `insert_before_symbol`, `insert_after_symbol`, `safe_delete_symbol`, `replace_content`, `replace_in_files`, `replace_lines`, `insert_at_line`, `delete_lines`, `create_text_file` | Serena sidecar |

`begin_plan`, `edit`, `apply`, `lsp_definition`, `lsp_references`,
`lsp_hover`, `lsp_symbols`, `lsp_outline`, `list_files`, and `search` are
no longer offered to the model. See
[ADR 0001](../adr/0001-serena-sidecar.md).

- The system prompt tells the model to prefer `find_symbol`,
  `find_declaration`, and `find_referencing_symbols` for C++ names, and to
  keep `search_for_pattern` for comments and strings.
- Serena reads use `relative_path` and 0-based `start_line` / `end_line`
  (the `lsp_*` and git tools use 1-based lines). The agent refuses any
  `max_answer_chars` above 262,144; the sidecar's own default answer cap is
  150,000 characters.
- Before a Serena call the agent refuses a path that is absolute, has
  `..`, or passes through `.git`, `.svn`, or `.env`. The sidecar's config
  ignores the same names.
- Git and SVN tools are read-only. There is no commit and no push.

### Editing

A successful Serena write puts the turn in **editing**. While it lasts,
`build`, `test`, and `compile_file` are refused with "Edits are in
progress. Call build_step once." A successful `build_step` ends it. If the
model tries to answer while still editing, the loop sends it back
(`needs_build`, shown on the page as `La boucle reprend : l'édition n'est
pas vérifiée.`, meaning "The loop resumes: the edit is not verified."). A failed
write or a replayed call does not change the
phase. The write lands in the checkout's working copy; review it with
`git diff` or `svn diff` yourself.

The same nudge mechanism also covers a tool failure that is still open
and a first answer given without any tool call. Each turn allows
`AGENT_LOOP_NUDGES` of them (3).

## A repository of thousands of files

The window does not hold the tree (32768 on the 14B, 262144 on `evo-x5`). The index does.

1. From this repo: `python scripts/deploy.py workspace d:/svn/ticket-142`.
   The checkout is registered by its remote URL (git origin, or the
   Subversion repository root). The first checkout of that URL embeds the
   tree. A second checkout or branch of the same URL attaches its
   `checkout_id` to unchanged `git_blob_hash` points without re-embedding,
   and only embeds the files that differ on that branch. `--update`
   refreshes that checkout's blobs in the shared collection. The command
   also writes `.vscode/mcp.json` in that copy.
2. Open that folder in VS Code. `.vscode/mcp.json` sends `X-Workspace` with
   the checkout path to the MCP door. Each `lookup`, `check`, `calls`,
   `types`, and `ask` then uses that copy's files and the shared index.
   Uncommitted edits are read live and are not written into the index.
3. Ask about a symbol or a file. The model should open that file, not
   list the repository.

The page `/ui` and `llm-client` do not send `X-Workspace`. They answer
about the default checkout, the folder `CODEBASE_PATH` points to
(`./sample` by default), and the Serena sidecar is started on that same
folder (`--project /workspace/app` in `compose.yaml`). The agent does not
tell the sidecar which registered checkout a turn names, so do not rely on
the Serena names for a registered checkout other than the default one.

The indexer skips `build/`, `CMakeFiles/`, `cmake-build-*`, `out/`,
`dist/`, VCS metadata, `.llm/`, `.vs/`, `.cache/`, `.venv/`,
`__pycache__/`, and `node_modules/`. A file whose content hash is
unchanged is not embedded again. The first pass is slow because the
embedder stays on the CPU on a 16 GB card so the chat model can use the GPU.
On an RTX 6000 (24 GB or 48 GB) the embedder and the reranker stay on the GPU.

`python scripts/host/context_limits.py` prints, for the GGUF in `.env`,
how many tokens fit on the GPU for each KV-cache type, capped by the
model's trained context. The 14B Q5 on a 16 GB card fits 32,768 tokens
with a `q8_0` cache. That is also the length the model was trained for.

## The page `/ui`

Open `http://127.0.0.1:8090/ui`. The page is served without a token; the
token is only sent with each API call.

- **Token.** Paste `AGENT_BEARER_TOKEN` and press `Mémoriser` (Remember). It is kept
  in this tab's `sessionStorage`; `Oublier` (Forget) removes it.
- **Model picker.** `Modèle de chat` (Chat model) lists the chat GGUFs from
  `GET /models` and marks the loaded one `(chargé)` (loaded). `Charger` (Load) calls
  `POST /models/select`: the router unloads the resident model and loads
  the chosen one, then the page prints `Modèle chargé.` (Model loaded.) or the router's
  error. The list appears once a token is saved.
- **Question and notes.** `Consignes pour cette question` (Instructions for this question) is an optional
  note (4,000 characters at most) added after the built-in conventions for
  that one question; the field is emptied on send.
- **Trace (`Déroulement`, "Progress").** One line per step: the route (and whether the
  rules or the model chose it), the retrieved chunks, each tool being
  prepared, running, then `réussi` (succeeded) or `en échec` (failed) with its log, and the
  loop restarts (nudges). Text the model wrote before a tool call is kept
  under `Raisonnement` (Reasoning).
- **Answer.** Rendered as Markdown as the tokens stream in. Each code
  block has `Copier` (Copy). A proposed fix is a diff block to copy; the page
  does not apply it.
- **Context meter.** `Contexte N % (used / window tokens)` (Context N%) and a bar,
  redrawn from each `context` event. When old results or turns were
  shortened, the trace says so.
- **Conversation.** The conversation id is shown under the token field
  and reused for each question. `Nouvelle conversation` (New conversation) clears the page
  and starts a new id. The agent keeps a conversation for
  `AGENT_CONVERSATION_TTL_SECONDS` (28,800, 8 h) and the last
  `AGENT_CONVERSATION_MAX_TURNS` turns (24).
- **Stop.** `Arrêter l'affichage` (Stop display; enabled while a turn runs) sends
  `POST /chat/cancel` with the conversation id, then closes the stream.
  The page shows `Calcul arrêté.` (Computation stopped.) and keeps what was already written. The
  agent drops the in-flight model request, tells `tools` to cancel, and
  stores `Stopped.` as that turn's answer in the conversation.
- **Capacity.** `AGENT_MAX_CONCURRENT` (2) turns run at once; a turn from
  another conversation beyond that is refused with `503` (the page shows
  it in the status line, it is not queued). A second question in the same
  conversation waits for the first.
- There is no checkout picker (see above).

## The command-line client

`app/` builds `llm-client` (CMake target `llm-client`, C++20, libcurl and
nlohmann_json; `containers/app/Containerfile` builds it with
`cmake --preset release` and runs `ctest --preset release`). In the stack
it is the `app` service, whose entrypoint is `llm-client`. Run it through
`deploy.py`:

```text
python scripts/deploy.py ask "Where is Evaluate declared?"
python scripts/deploy.py ask --file question.md
python scripts/deploy.py ask -          # prompt from stdin
python scripts/deploy.py chat           # multi-turn REPL
```

Arguments (only the first argument is read as an option):

| Invocation | Effect |
|---|---|
| `llm-client <question words…>` | The remaining arguments are joined with spaces. Streams from `POST /chat/stream`. |
| `llm-client --no-stream <question…>` | Single `POST /chat`; prints the finished answer. |
| `llm-client --file <path>` or `-f <path>` | Prompt read from a file. |
| `llm-client -` | Prompt read from stdin. |
| `llm-client --repl` | Prompts on stdin, one per line, same conversation; streams. `/quit` or `/exit` (or end of input) leaves. |
| `llm-client --help` or `-h` | Usage on stderr, exit 0. |

Trailing newlines are trimmed. The CLI sends `query` and `conversation_id`
only: no `workspace`, `instructions`, or `tools` field.

Environment (set by `compose.yaml` for the `app` service):

| Variable | Meaning |
|---|---|
| `AGENT_BASE_URL` | Required. `http://agent:8090` in the stack. |
| `AGENT_BEARER_TOKEN` | Required. The CLI refuses to send an unauthenticated request. |
| `APP_REQUEST_TIMEOUT_SECONDS` | Optional positive integer. Total time allowed for one request. Default 172800 (48 h). |

Output, in streaming mode (the default):

- Tokens are written to stdout as they arrive.
- Each tool call prints `[tool <name>]` on stderr.
- A newline, then, when retrieval found context, `Sources:` and one line
  per chunk, `  path:start-end (symbol)` (the symbol is omitted when none).
- When tools ran, `Tool calls:` and one line per call,
  `  <name>: <result summary>`.

With `--no-stream` the first line block is the answer, followed by the
same `Sources:` and `Tool calls:` blocks.

Exit codes:

| Code | Cause |
|---|---|
| `0` | Answer printed, or `--help`. |
| `1` | Unreadable prompt file; `AGENT_BEARER_TOKEN` or `AGENT_BASE_URL` missing; `APP_REQUEST_TIMEOUT_SECONDS` not a positive integer; agent HTTP status other than 200 (the body is printed); transport failure; unparsable response; an `error` event, or no `done`, on the stream. In `--repl`, the first failing question ends the session with `1`. |
| `2` | Usage: no argument, `--file` without a path, `--no-stream` without a question, or an empty prompt. |

`sample/` is not part of the client: it is the calculator fixture that
`CODEBASE_PATH` points to by default (`sample/CMakeLists.txt`, Catch2
tests, deliberate `known_bug:` cases). See
[First run](../getting-started/first-run.md) and
[Testing](testing.md).

## Context pressure

Each call measures the prompt. About 3,200 tokens are reserved for the
chat template and the tool schemas, and about 1,024 for the reply. Past
the remaining budget the agent shortens old tool results, then folds
older turns into one summary note. If the server
still rejects the size, it compresses once more and retries. The page
shows the fill rate from the `context` event.

The loaded chat model writes that summary (paths, symbols, decisions,
tool outcomes). If the model cannot be reached, the agent falls back to
a shortened note so the turn still fits.

## Models

Chat GGUFs in `./models/` can be loaded from the page without rebuilding
the image. See [Swapping the model](swapping-the-model.md). Only one model
stays resident: loading unloads the previous one first, so if the load
fails (for example the context does not fit), the page shows the router
error and no chat model is loaded until you load one that fits.

The LoRA trainer targets Mistral Small 4
([Fine-tuning](fine-tuning-the-codebase.md)). An adapter from that run
loads on `mistral-small-4` and does not load on the 14B coder.

## Where to change things

| Goal | Where |
|---|---|
| Add an HTTP tool the model can call | [Adding a new tool](adding-a-new-tool.md) |
| Enable or disable a Serena tool | `services/agent/serena_named_tools.py` (schema) and `config/serena/serena_config.yml` (sidecar) |
| Point retrieval at another tree | [Reindexing](reindexing-the-codebase.md) |
| Change the chat GGUF or the KV cache | [Swapping the model](swapping-the-model.md) and `containers/llm/entrypoint.sh` |
| Change prompts and rules | `services/agent/prompts.py`, `services/agent/rules/*.md` |
| Change the HTTP contract | `CONTRACTS.md`, then `.env.example` and `compose.yaml` (a test checks they stay aligned) |
| GPU will not load | [Troubleshooting GPU](troubleshooting-gpu.md) |
| See latency, tool success, context fill | [Observability](observability.md) |

`deploy.py up` may start a CPU overlay for that one command if its GPU
probe fails. It does not write `LLM_GPU_ARG=0` back into `.env`.

## Limits

- From an editor, MCP exposes facts (`lookup`, `check`, `calls`, `types`)
  and a short `ask`. `ask` does not search or build. There is no MCP
  `outline`: use the page, or ask for `get_symbols_overview` there.
  Register the checkout first (`python scripts/deploy.py workspace <path>`),
  which writes `X-Workspace`. See [VS Code et Continue.dev](vscode.md).
  A campaign that checks those tools one by one is
  [Tests fonctionnels](../notes/verification/functional-checks.md).
- `--watch` on the indexer does not see edits made from the Windows host
  through the Podman/Docker bind mount. Re-run the indexer.
- Images are pinned by tag, not by digest.
