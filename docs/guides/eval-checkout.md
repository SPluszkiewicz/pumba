# Retrieval baseline on a real checkout

`sample/` is too small to show whether the top 5 still find the right file.
This baseline measures that on a registered checkout. It does not call the
chat model, and a low score is a result, not a failed command.

The checkout used for the first run is the local OpenCV tree,
`D:/rest/opencv`: about 3 700 C++ files, CMake, Git. `fmt` (69 files) is
too close to `sample/`. `llvm-project` (about 50 000 files) is too large
for a first index on the CPU embedder.

## What the numbers mean

`POST /lookup` normally returns 5 hits. With `include_candidates: true` it
also returns:

| Field | Stage |
|---|---|
| `retrieval.candidates` | Hybrid prefetch, before the reranker (about 20) |
| `retrieval.reranked` | After the reranker, before the noise filter |
| `retrieval.index_hits` | What a clean tree shows. No working-copy overlay |

| Note in the report | Meaning |
|---|---|
| `in the 5` | The expected file survived |
| `dropped by the noise filter` | It was in the reranked window, then removed |
| `in the prefetch, not in the 5` | Recall found it; the reranker did not keep it |
| `absent from the prefetch` | It was never in the 20 |

Dirty files are read live and placed in front of `hits`. They are not in
`index_hits`. Run the baseline on a clean `git status`. The report prints
`dirty_files` and `--require-clean` exits non-zero when that count is not
zero.

## Run

Register and index first. The first pass is the long one. A second
`workspace --update` with no edits should skip almost every file
(`files_skipped` in the indexer summary). `errors` in that summary is the
file-error budget: a finished job with a large `errors` count is not a
healthy index.

```bash
python scripts/deploy.py workspace D:/rest/opencv
python scripts/deploy.py workspace --update D:/rest/opencv
python scripts/deploy.py eval D:/rest/opencv --questions scripts/host/eval_opencv_questions.json
```

The report prints the git revision, the Qdrant collection, whether the
reranker answered, and one row per question. It always finishes the list.
Exit code 0 means every lookup returned. It does not mean the hit rate is
high enough to sign off.

Clangd (`outline`, `calls`, `types`) and `compile` of one file are a
separate check. They need a `compile_commands.json` or a configure that
succeeds in the Ubuntu tools image. If that configure fails, the C++
navigation cases are not signed off. `svn log` can contact the repository
server; keep that case out of an offline run.

Questions live in `scripts/host/eval_opencv_questions.json`. Paths were
checked to exist in that tree, and each expected symbol occurs in that
file. They were not picked from a retrieval run. The file covers named
symbols (declaration and definition, across modules), paraphrases that
do not name the function, a pasted path, CMake (`ocv_add_module`), the
root `README.md`, and invented names. `expected_file_exact` means the
indexed path must be that file: `README.md` must not match
`3rdparty/protobuf/README.md`. Add a question only when you already know
the file, and keep the revision in the report next to the score.

`--allow-unfinished` scores a collection the registry has not marked
finished. The report still prints `indexed: false`.
