# Retrieval baseline on a real checkout

`sample/` is too small to show whether the top 5 still find the right file.
This baseline measures that on a registered checkout. It does not call the
chat model, and a low score is a result, not a failed command.

The checkout used for the first run is the local OpenCV tree,
`D:/rest/opencv`: about 3,700 C++ files, CMake, Git. `fmt` (69 files) is
too close to `sample/`. `llvm-project` (about 50,000 files) is too large
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

The report prints the git revision (`(none)` for a tree that is not git),
`dirty_files`, the Qdrant collection, whether the index is marked finished,
whether the reranker answered, the `hit@5` score, and one row per question.
`--agent` defaults to `$AGENT_BASE_URL`, else `http://127.0.0.1:8090`. The
token is read from `AGENT_BEARER_TOKEN` in `.env`. `--out report.json`
also writes the report as JSON.

Exit code 0 means every lookup returned. It does not mean the hit rate is
high enough to sign off. A non-zero exit means the run did not produce a
score: no token in `.env`, the path is not a registered checkout, the index
is not marked finished (without `--allow-unfinished`), a lookup failed
(agent unreachable or HTTP error, which stops the list and prints no
report), or `--require-clean` found uncommitted files.

Clangd (`calls`, `types`) and `compile` of one file are a
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
finished (without it the run stops with an error). The report still prints
`indexed: False`. The file holds 59 questions (41 `symbol`, 9 `behavior`,
3 `path`, 3 `negative`, 2 `cmake`, 1 `docs`); an invented name counts as
"kept out" when it does not show up in the candidates or the 5.
