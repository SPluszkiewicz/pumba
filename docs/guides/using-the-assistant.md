# Using the assistant

This is the day-to-day guide. If the words "model", "index", and "tool" are
new, read [Comprendre sans bagage](../concepts/en-clair.md) first (French).
Install and the first question are in
[First run](../getting-started/first-run.md). Shapes of HTTP requests are
in [Agent API](../reference/agent-api.md). The editor doors are in
[VS Code et Continue.dev](vscode.md).

## What a question does

The agent classifies the question, then either answers directly or works
on the repository.

| Route | When | What runs |
|---|---|---|
| `direct` | Greeting, arithmetic, general wording | One chat completion. No index, no tools. |
| `code` | A path, a symbol, build, test, CMake, or a follow-up in a code conversation | Retrieval, then up to `AGENT_TOOL_LOOP_MAX_ITERATIONS` tool rounds (48). |

Retrieval embeds the question, searches Qdrant (dense + BM25), optionally
reranks, and keeps 5 chunks. Rules whose globs match those paths are
appended, up to 16 000 characters.

Tools the model can call on the `code` path: `build`, `test`,
`compile_file`, `lint_clang_tidy`, `format_check`, `static_analysis`,
`search`, `list_files`, `read_file`, `git_log`, `git_blame`, `git_diff`,
`git_show`, `svn_status`, `svn_diff`, `svn_log`, `lsp_definition`,
`lsp_references`, `lsp_hover`, `lsp_symbols`, `lsp_outline`, `lsp_calls`,
`lsp_types`, `build_step`, `begin_plan`, `edit`, and `apply`. `read_file` returns at most
400 lines. `search` and `list_files` are capped. For C++ names, the model
is told to prefer clangd over text search.

Git and SVN tools are read-only. `edit` returns a diff and does not write.
`apply` writes that copy through `patch`. There is no commit and no push.

## A repository of thousands of files

The window does not hold the tree (32768 on the 14B, 262144 on `evo-x5`). The index does.

1. From this repo: `python scripts/deploy.py workspace d:/svn/ticket-142`.
   The checkout is registered by its remote URL (git origin, or the
   Subversion repository root). The first checkout of that URL embeds the
   tree. A second checkout or branch of the same URL attaches its
   `checkout_id` to unchanged `git_blob_hash` points without re-embedding,
   and only embeds the files that differ on that branch. `--update`
   refreshes that checkout's blobs in the shared collection.
2. Open that folder in VS Code. `.vscode/mcp.json` sends `X-Workspace` with
   the checkout path. Each question uses that copy's files and the shared
   index. Uncommitted edits are read live and are not written into the index.
3. Ask about a symbol or a file. The model should open that file, not
   list the repository.

The indexer skips `build/`, `CMakeFiles/`, `cmake-build-*`, `out/`,
`dist/`, VCS metadata, and `node_modules/`. A file whose content hash is
unchanged is not embedded again. The first pass is slow because the
embedder stays on the CPU on a 16 Go card so the chat model can use the GPU.
On an RTX 6000 (24 Go or 48 Go) the embedder and the reranker stay on the GPU.

`python scripts/host/context_limits.py` prints, for the GGUF in `.env`,
how many tokens fit on the GPU for each KV-cache type, capped by the
model's trained context. The 14B Q5 on a 16 Go card fits 32 768 tokens
with a `q8_0` cache. That is also the length the model was trained for.

## Context pressure

Each call measures the prompt. About 3 200 tokens are reserved for the
chat template and the tool schemas, and about 1 024 for the reply. Past
the remaining budget the agent shortens old tool results, then folds
older turns into one summary note. If the server
still rejects the size, it compresses once more and retries. The page
shows the fill rate from the `context` event.

The loaded chat model writes that summary (paths, symbols, decisions,
tool outcomes). If the model cannot be reached, the agent falls back to
a shortened note so the turn still fits.

## Models

Chat GGUFs in `./models/` can be loaded from the page without rebuilding
the image. See [Swapping the model](swapping-the-model.md). A Q4 model
fits more easily and calls tools less reliably. The 7B Q5 at a 32 768
context may not leave the margin the 14B was tuned for; if the load
fails, the page shows the router error and the previous model stays
unloaded until you load one that fits.

The LoRA trainer still targets `Qwen2.5-7B-Instruct`. An adapter from
that run does not load on the 14B coder.

## Changing the tool

| Goal | Where |
|---|---|
| Add a tool the model can call | [Adding a new tool](adding-a-new-tool.md) |
| Point retrieval at another tree | [Reindexing](reindexing-the-codebase.md) |
| Change the chat GGUF or the KV cache | [Swapping the model](swapping-the-model.md) and `containers/llm/entrypoint.sh` |
| Change prompts and rules | `services/agent/prompts.py`, `services/agent/rules/*.md` |
| Change the HTTP contract | `CONTRACTS.md`, then `.env.example` and `compose.yaml` (a test checks they stay aligned) |
| GPU will not load | [Troubleshooting GPU](troubleshooting-gpu.md) |

`deploy.py up` may start a CPU overlay for that one command if its GPU
probe fails. It does not write `LLM_GPU_ARG=0` back into `.env`.

## Limits

- From Cursor or VS Code, MCP exposes facts (`lookup`, `check`, `outline`,
  `calls`, `types`) and a short `ask`. `ask` does not search or build.
  Register the checkout first (`python scripts/deploy.py workspace <path>`),
  which writes `X-Workspace`. See [VS Code et Continue.dev](vscode.md).
  A campaign that checks those tools one by one is
  [Tests fonctionnels](prompt-tests-fonctionnels.md).
- **Arrêter** calls `POST /chat/cancel`. That drops the in-flight model
  request and stops the tool loop. The answer for that turn is `Stopped.`
- `--watch` on the indexer does not see edits made from the Windows host
  through the Podman/Docker bind mount. Re-run the indexer.
- Images are pinned by tag, not by digest.
