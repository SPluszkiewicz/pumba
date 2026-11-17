# Speeding up the indexing of a large repository

> **Status**: informational. None of the speed-up leads is wired into the code, and the repository measures no index duration. Written on 2026-11-10; only the inventory of the services recreated by `index` was completed. · **Last verified**: 2026-11-12

Research note of 2026-11-10. The guides describe usage. The code, Compose and the contracts own the behavior. When a guide and the code diverge, both are cited separately.

The subject is this repository's `indexer` service: tree walk, tree-sitter chunking, embeddings via `llama-server`, Qdrant upsert. The operator path is `python scripts/deploy.py index` and `python scripts/deploy.py workspace`. It is not Cursor's indexing.

## Question

Indexing a large repository takes a fairly long time. How can it be sped up?

## Short answer

On the first pass, the collection has nothing to recognize: every retained file is read, parsed, sent to the embedder, then upserted, and the next file waits. `embed_texts` is called once per file; the synchronous OpenAI client then splits that list again into HTTP calls of at most 16 texts, one after the other. No pool embeds several files in parallel. The repository does not time this path, so we cannot say how much belongs to the parse, to `llama-server` or to Qdrant. The structure puts all three on the same thread, the HTTP call in the middle.

The levers already exposed to the operator are the placement of the embedder and, on CPU, its threads. The profiles `24g`, `24g-30b`, `48g`, `48g-30b` and `48g-32b` build the CUDA image and pass `-ngl 999`. `16g` and `evo-x5` leave the embedder on CPU (`EMBEDDER_GPU_ARG=0`, `EMBEDDER_NGL=0`). `EMBEDDER_THREADS` and `EMBEDDER_NUMA`, if non-empty, become `-t` and `--numa`. When empty, these flags are not passed. None of these levers has a quantified gain in the repository. The guide only says that CPU is slower.

What is not a setting of this path: `INDEXER_EMBED_BATCH` (default 16 in the process, absent from Compose and from `.env.example`), `EMBEDDER_CTX` and `EMBEDDER_UBATCH` (8192 written in the entrypoint, not passed to the `embedder` service), `--parallel` / `-np` (the entrypoint does not pass it), the list of excluded directories, and `--watch` (it first runs the same full batch).

On the second pass, a file whose `git_blob_hash` is already linked to this checkout is neither parsed, nor embedded, nor upserted. Another checkout of the same remote can attach a blob already present. That changes the picture after the first fill. It is not a speed-up of the first pass.

## First pass: where the code goes

`python scripts/deploy.py index` recreates `tools`, `patch`, `agent` and `ui-proxy`, then launches the one-shot container. `scripts/host/deploy/stack.py`, `cmd_index`:

> d._recreate_mount_dependents()
> d._run(["podman", "compose", "--profile", "index", "run", "--rm", "indexer"])

This recreate step comes before the loop. The repository does not time it. `serena` also mounts `CODEBASE_PATH` read-write but is not in `_MOUNT_DEPENDENT_SERVICES` (`scripts/host/deploy/__init__.py`: `tools`, `patch`, `agent`, `ui-proxy`): `index` does not recreate it. `cmd_workspace` enters the indexer only if the remote has not yet been marked indexed, if the checkout is new, or if `--update` is passed, and it takes `IndexLock` (`workspaces.py`: "One indexer at a time"). `cmd_index` does not take this lock. The guide `docs/guides/reindexing-the-codebase.md` says, without distinguishing the two commands:

> One indexer runs at a time.

`index_source_root` does a `snapshot()` (a scroll, vectors excluded, pages of 256), then a `for` loop over `chunker.iter_source_files`. Each file calls `process_file` and waits for its return. `services/indexer/main.py` imports `threading` only for the debounce of `--watch`. There is no pool. At the first fill, the snapshot finds no hash: the `skipped` branch and the `attach` branch do not apply.

For a new file, `process_file` reads the bytes, computes the hash, calls `chunk_file`, then once `embed_texts` with all the file's texts, then `upsert_chunks`, then only after that the next file. The excerpt:

> vectors = embeddings_client.embed_texts([c.text for c in chunks])
> written = store.upsert_chunks(chunks, vectors)

`embed_texts` prefixes each text and goes through `_embed_batched`. If the list exceeds `EMBED_BATCH`, it is cut into slices, and each slice is a synchronous `sdk.embeddings.create`. The module says so:

> built on the official `openai` SDK (sync client. This is a batch job).

`services/indexer/requirements.txt` pins `openai==2.54.0` and `httpx==0.28.1`. The HTTP client is the OpenAI SDK (`httpx` transport), not `urllib`. `EMBED_BATCH` is 16 unless the process has `INDEXER_EMBED_BATCH`. The client's comment explains the cut by the 60 s timeout and by `exceed_context_size`, not by a measured throughput: a slice that is still too big is cut in two and sent again, still in series. Compose does not pass `INDEXER_EMBED_BATCH` to the `indexer` service. Putting it in `.env` does not change the container. The key is neither in `.env.example` nor in `CONTRACTS.md`.

`--watch` runs this same `index_source_root` before listening. `main.py`:

> `--watch` is best-effort and Linux-preferred: it runs the same full batch
> first

The guide `docs/guides/reindexing-the-codebase.md` repeats that the one-shot is the supported path. The debounce `threading.Timer`s can overlap if several paths reach the end of their delay at the same time. That is not a pool over the tree, and the one-shot first pass has none.

The guide `docs/notes/verification/retrieval-baseline.md` qualifies the first pass, without a duration:

> The first pass is the long one.

It judges `llvm-project` (about 50,000 files, the guide's figure) too big for a first index on the CPU embedder, and takes OpenCV (about 3,700 C++ files) as the trial corpus. These are tree sizes, not timings.

## What the operator can already change

| Setting | Who reads it | Effect on duration |
| --- | --- | --- |
| `EMBEDDER_GPU_ARG` | `GPU` build argument of the `embedder` service in `compose.yaml`; `gpu-profile` copies it from `config/profiles/` | `0`: Ubuntu image without CUDA. `1`: CUDA image. `scripts/host/deploy/gpu.py` warns that a change rebuilds at the next `up`. No gain factor in the repository. |
| `EMBEDDER_NGL` | `containers/embedder/entrypoint.sh` → `llama-server -ngl` | `0` on `16g` and `evo-x5`. `999` on `24g`, `24g-30b`, `48g`, `48g-30b`, `48g-32b`. The llama.cpp README (tag `b10216`, the one in `CONTRACTS.md`) defines `-ngl` as the maximum number of layers in VRAM (`auto` or `all` are also accepted; the binary's default is `auto`). `999` is an integer, not the word `all`. No figure. |
| `EMBEDDER_THREADS` | the same entrypoint, `-t`, only if the variable is not empty | Empty in `.env.example` and in the NVIDIA profiles. The flag is then not added. The README gives `-t` the default `-1` and does not define `-1` on that line. |
| `EMBEDDER_NUMA` | `--numa`, same condition | Empty: not passed. |
| `INDEXER_SOURCE_ROOT`, `INDEXER_CHECKOUT_ID` | `main.py`, `vector_store.py`, via Compose | Choose the tree and the `checkouts` label. They do not parallelize the loop. |
| `INDEXER_EMBED_BATCH`, `EMBEDDER_CTX`, `EMBEDDER_UBATCH`, `-np` | see below | Not keys of the `.env` → embedder / indexer container path. |

`CONTRACTS.md`:

> EMBEDDER_GPU_ARG=0                    # compose build arg; 16g profile is CPU. 24g/48g set 1
> EMBEDDER_NGL=0                        # runtime -ngl; 24g/48g set 999
> EMBEDDER_THREADS=                     # runtime -t; empty = auto

The files `config/profiles/24g.env`, `24g-30b.env`, `48g.env`, `48g-30b.env` and `48g-32b.env` set `EMBEDDER_GPU_ARG=1` and `EMBEDDER_NGL=999`. `16g.env` and `evo-x5.env` set both to `0`. The comment in `24g.env` says the `q8_0` KV leaves room for the embedder on the same card as the chat model. Nothing unloads `llm` before `index`. The gain from sharing the GPU or not is not measured.

`docs/guides/troubleshooting-gpu.md`:

> Everything in this project works without a GPU at all. It's just slower.

The same page says the thread count chosen by `llama-server` "is usually not the fastest setting" on a hyper-threaded or dual-socket machine, and that `EMBEDDER_THREADS` follows the same semantics as `LLM_THREADS`. The measurement recipe that follows (`predicted_per_second`, `prompt_per_second`, `deploy.py logs llm`) targets the chat server, not the indexer. The repository therefore does not say by how much a `-t` shortens an index.

`compose.yaml` gives `nvidia.com/gpu=all` to `embedder` in the base file. `config/compose/cpu.yaml`, used when the NVIDIA CDI is missing, forces `GPU: "0"`, removes the device, and sets `EMBEDDER_NGL=0`. The `Containerfile` comments a default `EMBEDDER_GPU_ARG=1` and a Compose `${EMBEDDER_GPU_ARG:-1}`. The actual `compose.yaml` is `${EMBEDDER_GPU_ARG:-0}`, aligned with `.env.example` and with `CONTRACTS.md`. With the example `.env`, the embedder that runs is the CPU image.

The `indexer` service in `compose.yaml` has no CPU limit, no GPU, and no `depends_on`. It only does the walk and the HTTP clients. `cmd_workspace` launches it with `--no-deps`.

## Second pass and another checkout

This is not the first pass. `index_source_root` documents it:

> Unchanged files (same `git_blob_hash` as already stored) are skipped.
> Blobs already embedded by another branch/checkout are linked without
> re-embedding.

`process_file` returns `skipped` when the current hash is already among the hashes of this checkout, before `chunk_file` and before `embed_texts`. It returns `attach` when the `(path, hash)` pair is already in the snapshot, again before the parse. The attach is grouped at the end of the batch (`attach_checkouts`), then the old hashes of the same path are detached. The file is still read: `read_bytes` is used to compute the hash. The initial scroll re-reads the payloads (`file_path`, `git_blob_hash`, `checkouts`), not the vectors.

`docs/concepts/indexing-pipeline.md` describes the same skip. The guide `docs/guides/reindexing-the-codebase.md` too, and it adds that the second `workspace` of another ticket of the same remote attaches the blobs already there and embeds only the files that differ. The code of `cmd_workspace` does launch the indexer for a new checkout (`new_checkout`), even if the remote is already marked indexed. The docstring of `cmd_workspace` says the opposite ("Another ticket of the same remote only records the path"). The docstring of `register_checkout` says "Index only the first time this remote is seen". The condition executed is `needs_index or new_checkout or args.update`.

The skip is per file, not per symbol. One changed byte in a function changes the blob hash: `process_file` then re-embeds all the chunks of the file. `docs/concepts/indexing-pipeline.md` nevertheless writes:

> running it after editing one function only ever touches that function's points.

`docs/guides/reindexing-the-codebase.md` omits `git_blob_hash` from the identifier formula:

> Point IDs stay a deterministic function of `(file_path, symbol_name, start_line)`

`CONTRACTS.md`, `vector_store.point_id` and the concepts page include `git_blob_hash`. The two guides and the code do not say the same thing. The behavior that avoids the embedder is that of `process_file`.

## What the walk embeds, and what it leaves

`SKIPPED_DIR_NAMES` in `services/indexer/chunker.py`:

> ".git", ".svn", ".llm", ".vs", ".cache", ".venv", "build", "out", "dist", "CMakeFiles", "node_modules", "__pycache__"

`_skip_directory` adds any name that starts with `cmake-build-`. The comparison is case-sensitive: a `Build` directory is not in the set. There is no environment key to extend the list. A build tree whose folder is named exactly `build`, `out`, `dist` or `CMakeFiles` is not walked. A `build/` at any depth is excluded, because the test applies to the directory name, not to a path.

Everything else is walked if the extension or the name is retained. C++: `.cpp`, `.cc`, `.cxx`, `.hpp`, `.hh`, `.hxx`, `.h`. Text: `.md`, `.markdown`, `.cmake`, `.yaml`, `.yml`, `.json`, `.toml`, `.ini`, `.cfg`, `.txt`, `.sh`, plus `CMakeLists.txt`, `CMakePresets.json`, `CMakeUserPresets.json`, `Containerfile`, `Dockerfile`, `.clang-tidy`, `.clang-format`. A `.pb.h` or a `.pb.cc` outside these directories is a source file: it is parsed and embedded. `third_party`, `external`, `generated`, `_deps` are not excluded names.

C++ files have no size cap. `text_chunker.chunk_text_source` returns an empty list beyond 1 MiB or if the content contains a NUL byte:

> MAX_TEXT_FILE_BYTES = 1024 * 1024

`docs/concepts/indexing-pipeline.md` says so for text ("Files over 1 MiB or containing NUL bytes are skipped") and for directories (`build/`, `.git/`, `node_modules/`). The guide `docs/guides/reindexing-the-codebase.md`:

> A Python or TypeScript repo still indexes almost empty.

The C++ parse is tree-sitter (`tree-sitter-cpp`), on the same thread, before the HTTP call. The walk is iterative "so a deep generated header does not blow the stack" (`chunker.py`). The repository contains no timing that would say whether this parse dominates the first pass. We therefore do not rank it. Unknown extensions return before the parse (`is_source_file`).

Long chunks are re-cut by `fit_chunk`: 120 lines (`2 * WINDOW_LINES`, `WINDOW_LINES = 60`) or 1800 characters (`MAX_EMBED_CHARS`). The comment in `text_chunker.py` ties 1800 to the 2048-token context, and cites an internal excerpt (3998 characters, 3125 tokens in `modules/calib3d/src/dls.cpp`). It is a justification written next to the constant, not an indexing duration. This cut multiplies the vectors of a big function. The repository does not say whether fewer, longer vectors would go faster.

## nomic context and embedder flags

Three texts do not give the same window.

`text_chunker.py` and `docs/concepts/indexing-pipeline.md`: nomic-embed-text-v1.5 rejects an input beyond 2048 tokens. The entrypoint, for its part:

> nomic embed text v1.5 accepts 8192 tokens
> EMBEDDER_CTX=8192
> EMBEDDER_UBATCH=8192

then `-c`, `-b` and `-ub` all three at this value. The `embedder` service in `compose.yaml` passes neither `EMBEDDER_CTX` nor `EMBEDDER_UBATCH`. The reranker, which reuses the entrypoint, sets `EMBEDDER_CTX=2048` in Compose. The entrypoint passes neither `--rope-scaling` nor `--rope-freq-scale`, and it does not pass `-np`.

The model card, [nomic-ai/nomic-embed-text-v1.5](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5), asks for the prefix and announces a length of 8192. It writes: "the text prompt must include a task instruction prefix", and the index example is `search_document: `. The "Adjusting Dimensionality" table gives `SeqLen` 8192 for `nomic-embed-text-v1.5`. The same page says the model "natively supports scaling of the sequence length past 2048 tokens" with a dynamic RoPE (`model_max_length=8192`, `rope_type: dynamic`, `factor: 2.0`). This repository's code prefixes anyway, in `embeddings_client.py`, via `DOCUMENT_PREFIX` of `services/common/qdrant_schema.py`:

> DOCUMENT_PREFIX = "search_document: "

The GGUF card actually served, [nomic-ai/nomic-embed-text-v1.5-GGUF](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF), says something else for llama.cpp: "llama.cpp will default to 2048 tokens of context with these files. For the full 8192 token context length, you will have to choose a context extension method." The example command adds `-c 8192 -b 8192 --rope-scaling yarn --rope-freq-scale .75`. These two RoPE flags are not in the entrypoint. Lengthening the chunks up to 8192 is therefore not a lever already wired, and the GGUF card does not present it as a free default of llama.cpp.

The server README at the pinned tag, [tools/server/README.md at `b10216`](https://github.com/ggml-org/llama.cpp/blob/b10216/tools/server/README.md), defines the flags without giving an embedding throughput:

- `-c, --ctx-size`: context size (default 0, taken from the model).
- `-b, --batch-size`: maximum logical batch (default 2048).
- `-ub, --ubatch-size`: maximum physical batch (default 512).
- `-np, --parallel`: number of server slots (default `-1`, "-1 = auto"). The line does not say what `auto` resolves to, nor that one more slot speeds up a single request.
- `-t, --threads`: CPU threads during generation (default `-1`).
- `--embedding`: restrict the server to the embedding case.

The entrypoint enables `--embedding` and `--pooling` (`mean` by default). It forces `-b` and `-ub` to 8192, so the README's defaults of 2048 and 512 are not the container's. The entrypoint's comment says the default physical batch is 512 and that embedding mode brings the logical batch down to that value, to the point that a chunk above it is rejected. That is why the container aligns `-b` and `-ub` on 8192. The tag's README does not quantify what this raise changes for an embedding server. The indexer's client has only one HTTP request in flight anyway: a larger `-np` would have no second request to place in another slot. The README describes the slot as the unit that processes requests ("total_slots […] defined by `--parallel`"). It does not say that an `input` array of `/v1/embeddings` occupies several slots.

## Qdrant during ingestion

`upsert_chunks` sends all the file's points in a single `client.upsert`. `_WRITE_BATCH = 128` does not apply to this upsert. It groups `set_payload` and `delete`. `create_collection` passes neither `optimizers_config`, nor `indexing_threshold`, nor a number of shards. The payload indexes `file_path`, `git_blob_hash` and `checkouts` are created in `ensure_collection`, before the loop (`PAYLOAD_INDEX_FIELDS`).

[Bulk Uploading Data to Qdrant](https://qdrant.tech/documentation/operate-and-maintain/bulk-data-import/) says that, during the upload, the optimizer builds the HNSW indexes of the new segments in the background, and that this work competes with ingestion for CPU and memory. If nothing has to query the collection before the end, the page says to create it with `indexing_threshold` at `0`, upload, then restore the threshold. It writes: "The default `indexing_threshold` is 10,000 KB." While the index is deferred, searches are scans. [Optimizer](https://qdrant.tech/documentation/ops-optimization/optimizer/) describes the same mechanism: the appendable segment grows up to the threshold, then the optimizer builds an HNSW for it and a new appendable segment receives the rest. The API page [update collection](https://api.qdrant.tech/api-reference/collections/update-collection) still writes "Default value is 20,000" for `indexing_threshold`. The two official pages do not give the same default. The code sends neither: it is the default of the `qdrant/qdrant:v1.19.0` binary (`CONTRACTS.md`) that applies. This repository does not re-read it.

The bulk page also says that one point per request pays the request cost every time, and that a batch of 64 to 256 points is a starting range, without a gain factor. It says that two to four workers help when a single stream does not saturate writing. Here the stream is one upsert per file, after the embedder returns, on the same thread. The page does not say that this range shortens a pipeline that waits for a model between two upserts. It does not quantify it either.

## What the primary sources describe and this code does not wire

Two mechanisms are written at the vendor and absent from the call:

Qdrant, bulk page: `indexing_threshold: 0` during a load that nobody queries, then return to the threshold. `VectorStore.ensure_collection` does not do it. The guide `reindexing-the-codebase.md` recalls that the agent answers with what is already in Qdrant; a collection in the middle of its first fill can therefore be queried. The Qdrant page reserves the zero threshold for the case where one does not need to search during the upload.

Qdrant, same page: parallel batches and workers, for a client that already sends fast. This client sends the file's batch once the embedding is back, and it does not send the next file in the meantime. Wiring workers without measuring would leave the embedder and Qdrant sharing the same CPU or the same GPU. The page gives no factor. The llama.cpp README describes slots for concurrent requests; this client emits only one at a time, and the entrypoint does not pass `-np`. Opening slots without opening requests does not match what the README calls a slot.

None of these texts says that the tree-sitter parse is the item to cut. The repository does not either.

## What is not measured

No test, guide or comment in this repository gives an index duration, a number of chunks per second, or a ratio between CPU and GPU, between a `-t` and the default, between a grouped upsert and the current upsert, or between deferred HNSW and HNSW built on the fly. We therefore do not rank the levers by gain. The only order the code imposes is the order of the thread: read, hash, parse, embedder HTTP, upsert, next file, and, on the first pass, no skip.
