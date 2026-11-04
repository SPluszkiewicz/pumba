# Indexing pipeline

The `indexer` service turns the mounted tree into searchable vectors in
Qdrant. By default that tree is `sample/` (`CODEBASE_PATH`). A registered
checkout is indexed into its own collection, shared by every working copy
of the same remote URL. `app/` is the C++ client, not the default corpus.

The `agent` service later embeds a user's question the same way and does a
similarity search against these vectors to find relevant code before asking
the LLM to answer (that retrieval half of the story is covered in
[RAG, explained](rag-explained.md); this page is about how the vectors get
*into* Qdrant in the first place).

If you've never built a RAG indexing pipeline before, the mental model is:

```
walk source tree -> chunk each file -> embed each chunk -> upsert into Qdrant
```

Four steps, five modules: `chunker.py` (C++, tree-sitter) and
`text_chunker.py` (CMake / Markdown / YAML / ...), `embeddings_client.py`,
`vector_store.py`, and `main.py` wiring them together as a one-shot batch
job. The rest of this page explains the interesting decision in each step.

## Why structural chunking instead of naive line-splitting

The naive way to chunk source code for embedding is "split into fixed-size
blocks of N lines" (or N tokens). It's simple, but it's also the single
biggest source of bad retrieval quality in home-grown RAG systems, for a
concrete reason: **embedding models produce one vector for a block of text,
and that vector is only meaningful if the text is a coherent unit of
meaning.**

If you split every 50 lines regardless of what's on those lines, you get
chunks like "the last third of `parse_arguments`, then a blank line, then
the first two lines of `run_server`". The resulting embedding is a blurry
average of two unrelated concepts. When a user later asks "how does
`parse_arguments` handle missing flags?", the query embedding has to match
against that blurry vector - and it often loses to some other blurry chunk
that happens to share more surface-level words.

The fix is to chunk at **structural boundaries that already carry meaning**:
one function is one idea, one class is one idea, one method is one idea.
The indexer gets this by actually parsing the C++ with
[tree-sitter](https://tree-sitter.github.io/tree-sitter/) instead of
treating source files as plain text. `chunker.py` walks the real syntax
tree produced by the `tree-sitter-cpp` grammar and emits one chunk per
`function_definition`, free-function `declaration` (e.g. a header prototype
sitting beside a class), `class_specifier`, or `struct_specifier` node it
finds (with a couple of small refinements below).  A chunk that fails to
map onto a clean function/class boundary still has to go *somewhere*, so
whole files that contain nothing but macros, `#include`s, or global
constants fall back to a single chunk labeled `<file-level>` rather than
being silently dropped. A function or class longer than 120 lines, or any
excerpt over 1800 characters, is cut into overlapping windows
(`name <lines a-b>`). The character cap is the one that matters for the
embedder: nomic-embed-text-v1.5 rejects any input past its 2048-token
context, and a dense 80-line function already crosses that line.

A few structural details worth knowing if you're reading `chunker.py`:

- **Templates.** `template <typename T> class Box { ... }` is represented in
  the syntax tree as a `template_declaration` wrapping the `class_specifier`.
  The chunker walks up one level so the emitted chunk's line range includes
  the `template <...>` header - otherwise the chunk text would start
  mid-declaration and look confusing out of context.
- **Nested classes and methods.** Chunking happens at *every* matching node,
  not just the outermost one, so a nested class produces its own chunk (e.g.
  `Outer::Inner`) in addition to the outer class's chunk (which still
  contains the nested class's text too, in full). Overlap here is
  intentional: a broad "here's the whole class" chunk and a narrow "here's
  just this one nested method" chunk are both useful retrieval targets for
  different kinds of questions.
- **Out-of-line method definitions** (`void Foo::bar() { ... }` defined
  outside the class body) are already self-qualified by the C++ syntax
  itself, so the chunker uses that text as-is instead of re-deriving the
  class name from ancestor scopes.
- **Preprocessor macros** are parsed by `tree-sitter-cpp` as ordinary syntax
  nodes (not expanded), so a `#define` never crashes the parser - it's just
  not one of the node types the chunker looks for, so it never becomes its
  own chunk. Code that *uses* a macro is chunked normally around it.
- **Unsupported and empty files** are handled explicitly rather than left
  to throw: unknown extensions (binaries, archives) are skipped before
  parsing is even attempted, a file with no (or only whitespace) content
  produces zero chunks, and `build/`, `.git/`, `node_modules/`-style
  directories are never walked.

## Non-C++ files: `text_chunker.py`

The files people ask about most after the code itself are the build system
and the docs - "which target links libcurl?", "what does the README say
about presets?". Those are not C++ and tree-sitter-cpp cannot parse them, so
`chunker.py` hands them to `text_chunker.py`, which applies **the same
technique** with the community grammars bundled in
[`tree-sitter-language-pack`](https://github.com/Goldziher/tree-sitter-language-pack)
rather than re-implementing Markdown/CMake/YAML parsing by hand (the
grammars already handle the edge cases - fenced code blocks, setext
headings, bracket arguments, multi-document YAML - that a regex would get
wrong):

| Format | Detected by | Grammar unit used as a chunk | `symbol_name` |
|---|---|---|---|
| Markdown (`.md`) | extension | `section` - own content only, child sections are chunked separately; text before the first heading is a `<preamble>` | the heading text |
| CMake (`CMakeLists.txt`, `*.cmake`) | name / extension | top-level `normal_command`, `function_def`, `macro_def`, `if_condition`, ... | first `project` / `add_executable` / `add_library` / `function` name |
| YAML (`.yaml`, `.clang-tidy`, ...) | extension / name | top-level `block_mapping_pair` of each document | the key |
| JSON (`CMakePresets.json`, `.json`) | name / extension | top-level `pair` of the root object | the key |
| TOML (`.toml`) | extension | top-level `table` / `pair` | the table header / key |
| INI, shell, `Containerfile`, plain text | extension / name | *(no grammar)* fixed 60-line windows, 10-line overlap | `<file-level>` or `<lines a-b>` |

What the grammars do not decide - and is therefore the only policy written
in this module - is sizing: consecutive small units (CMake commands, YAML
keys, JSON pairs) are **packed** together up to 60 lines so we never embed a
one-line chunk (the symbol then lists the first names: `pumba_app, app,
agent_client_test, …`), and any unit longer than 120 lines or 1800
characters is cut into overlapping windows with the unit's name kept as a
prefix (`services <lines 1-60>`). Files over 1 MiB or containing NUL bytes are
skipped. Every chunk has the same payload as a C++ chunk; only `language`
differs (`markdown`, `cmake`, `yaml`, `json`, `toml`, ...), so a future
retrieval filter can restrict a search to code or to docs.

The trainer's dataset builder deliberately keeps using the C++ subset only
(`chunker.is_cpp_file`): its question templates are about functions and
classes and would produce nonsense for a README section.

## The embedding-prefix footgun

`nomic-embed-text-v1.5` (the embedding model behind the `embedder` service)
was trained to expect a short task instruction prepended to every input
string: `"search_document: "` for text being indexed, `"search_query: "` for
a user's question at retrieval time. This isn't optional metadata - it's
part of how the model was trained to place vectors in space, and it's also
one of the most dangerous kinds of bug you can ship in a RAG system, because
**getting it wrong doesn't produce an error.**

If you forget the prefix, or use the wrong one, the embedder happily returns
a 768-dimensional vector for every input, `qdrant-client` happily stores it,
and searches happily return *something*. Nothing crashes. Retrieval quality
just quietly degrades, because the query and document vectors are no longer
in the geometric relationship the model was trained to produce. This is easy
to miss in development (a few results still look plausible) and painful to
debug later (there's no stack trace pointing at the missing string).

The indexer's answer is to make prefixing structurally impossible to skip:
`embeddings_client.embed_texts()` prepends `search_document: ` to every
input *inside* the client, unconditionally, before it ever touches the
network (the network part itself is the official `openai` SDK pointed at
the embedder's `/v1` - retries and response typing come from it, only the
prefixing and the "one vector per input, in order" check are ours). Callers (`main.py`, and any future caller) pass raw chunk text and
get back correctly-prefixed embeddings; there's no code path that reaches
the embedder without going through this function. The equivalent
`search_query: ` prefixing for user questions lives in the `agent` service,
since that's the module that actually has a user query to prefix - see
[RAG, explained](rag-explained.md).

The same chunk is also stored as a sparse `bm25` vector (term frequencies;
Qdrant applies IDF). Tokens are hashed by `services/common/sparse_text.py`,
which the agent uses unchanged at query time. An existing collection that
only has the old unnamed 768-d vector is dropped on the next index run.

## Idempotent upserts: re-indexing shouldn't duplicate anything

The indexer is designed to be run over and over - every time the mounted tree changes,
someone (or the `--watch` flag, best-effort) re-runs it. A naive
"always insert a new point" design would mean every re-index of an unchanged
file doubles that file's points in Qdrant, and after a few weeks of CI runs
you'd be searching against thousands of duplicate/stale copies of the same
functions.

Qdrant solves this at the storage layer: `upsert` on a point ID that already
exists **replaces** that point instead of adding a new one. So the whole
problem reduces to one question: *what should a chunk's point ID be, such
that re-chunking the same logical unit of code always produces the same ID?*

The indexer's answer, per the pinned schema in `CONTRACTS.md`, is to derive
the ID deterministically from `(file_path, git_blob_hash, symbol_name, start_line)`:

```python
uuid.uuid5(FIXED_NAMESPACE, f"{file_path}\x1f{git_blob_hash}\x1f{symbol_name}\x1f{start_line}")
```

A few things make this work reliably:

- **`uuid5` is a pure function of its inputs** - same inputs always produce
  the same UUID, with no external state or randomness involved. Qdrant point
  IDs must be an unsigned integer or a UUID string, so `uuid5` (rather than,
  say, a raw hex digest) is what actually satisfies the storage layer's
  requirements while still being deterministic.
- **The namespace UUID is fixed and must never change.** `uuid5` mixes the
  namespace into the hash, so changing it would silently reassign every
  chunk a new ID on the next run - defeating the entire point.
- **A separator character joins the fields before hashing**, so that
  `("ab", "1", 0)` and `("a", "b1", 0)` can't collide by having their
  concatenated strings happen to match.
- **`git_blob_hash` is part of the point ID, and `checkouts` lists the working copies that hold that blob.**
  Point ID is `uuid5(file_path, git_blob_hash, symbol_name, start_line)`.
  When two branches or SVN checkouts share the same file content, they
  have the same `git_blob_hash`: the indexer simply attaches the second
  `checkout_id` (`co_<hash>`) to the existing points' `checkouts` array
  without re-embedding. When a file differs on another branch, its chunks
  get a distinct `git_blob_hash` and coexist in the same collection; at
  query time (`lookup` / `/chat`), Qdrant filters on `checkouts` for the
  active `X-Workspace`. When a file changes or is deleted on a checkout,
  that checkout is detached from the old points, and points with no
  remaining checkout are deleted.

The practical result: running the batch job twice over an unchanged
codebase leaves Qdrant in exactly the same state as running it once
(`listed_file_hashes` + skip by `git_blob_hash` avoids a second embed),
and running it after editing one function only ever touches that
function's points. Files gone from the walk are deleted at the end of
the run (tombstoning); the summary JSON line reports `files_skipped`
and `files_deleted`.

## Where each piece lives

| Concern | Module |
|---|---|
| Walk `INDEXER_SOURCE_ROOT`, parse into chunks | `services/indexer/chunker.py` |
| Call the `embedder` service, apply prefixing | `services/indexer/embeddings_client.py` |
| Talk to Qdrant, deterministic point IDs | `services/indexer/vector_store.py` |
| Batch job entrypoint, structured logging | `services/indexer/main.py` |

## Running it

The primary supported path is the one-shot batch job, run via the `index`
compose profile:

```bash
podman compose --profile index run --rm indexer
```

This walks `INDEXER_SOURCE_ROOT` (the default tree, bind-mounted read-only) once,
chunks and embeds every source file, upserts into Qdrant's `code_chunks`
collection, and exits. Each processed file and a final summary are logged as
one JSON object per line on stdout (`structlog`, via the shared
`services/common/logging.py`; `file_error`/`fatal` at `error` level, the
rest at `info`), so the output is easy to grep or pipe into `jq` - see
[Reindexing the codebase](../guides/reindexing-the-codebase.md) for
day-to-day usage.

`main.py` also accepts a `--watch` flag that keeps the process running and
re-indexes files as they change, using the `watchdog` package. **This is
best-effort and Linux-preferred.** `watchdog` relies on `inotify` on Linux,
and inotify events do not propagate reliably through the
Windows host → Podman machine → WSL2 → bind-mount chain this project runs
on by default. If you're on that stack, don't rely on `--watch` picking up
your edits - just re-run the one-shot job. On a native Linux host bind-mounting
a real filesystem (no VM hop in between), `--watch` behaves as expected.
