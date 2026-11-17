# Fine-tuning the codebase (QLoRA)

The stack can **fine-tune** a lightweight LoRA adapter on your mounted C++
project so the chat model learns project-specific names and patterns. This
complements [RAG](../concepts/rag-explained.md): fine-tuning adjusts model
weights; RAG injects exact indexed snippets at query time.

| | RAG | QLoRA adapter |
|---|---|---|
| Updated by | `python scripts/deploy.py index` | `python scripts/deploy.py train` |
| Holds | Exact chunk text | Approximate project knowledge |
| Extra cost at query time | None (embedder only) | None: the container `llm` loads the adapter at start (`--lora`), the base GGUF is not modified |

## One-time setup

1. Set `CODEBASE_PATH` in `.env` (see [Reindexing the codebase](reindexing-the-codebase.md)).
2. Download Hugging Face base weights for training (~242 GiB, internet once):

```bash
podman compose --profile setup run --rm fetch-models \
  python scripts/container/fetch_models.py --hf-train-base
```

The trainer base is Mistral Small 4 (`mistralai/Mistral-Small-4-119B-2603`),
the same model as the evo-x5 GGUF. An adapter from `train` loads on
`mistral-small-4`. It does not load on the 14B Qwen.

!!! warning "Which `llm` can use the adapter"
    Only the container `llm` passes `--lora`: its entrypoint adds it when
    `LLM_LORA_PATH` points to an existing file. `train` sets that variable
    when it is empty, but a Mistral adapter does not load on the 14B Qwen
    GGUF (per `CONTRACTS.md`), so on a 14B profile clear it again.
    The `evo-x5` profile serves `mistral-small-4` with the host `llama-server`,
    which is started without `--lora`: `train` still runs and restarts that
    server, but the adapter is not applied to it.

## Train on the codebase

Full pass (first time or after large changes):

```bash
python scripts/deploy.py train
```

Incremental (git diff since last trained commit):

```bash
python scripts/deploy.py train --incremental
```

Dataset only (no GPU, and the HF base is not required):

```bash
python scripts/deploy.py train --dry-run
```

Other flags: `--skip-index` (do not re-index afterwards) and `--force`
(ignore an existing `models/training/.train.lock`).

What `train` does:

1. Build `models/training/{commit}/dataset.jsonl` from tree-sitter chunks
   (`services/trainer/build_dataset.py`, run in the `devtools` service).
2. Run QLoRA in the `train` compose profile (GPU).
3. Write `models/adapters/latest/`; if the trainer did not leave
   `adapter.gguf` there, convert it with `scripts/container/convert_lora.py` (inside the trainer image).
4. Set `LLM_LORA_PATH=/models/adapters/latest/adapter.gguf` in `.env` if unset.
5. Container `llm`: recreate it (it adds `--lora` at start). Host `llama-server`
   (`evo-x5`): restart it (no `--lora`, see the warning above). Then re-index
   (unless `--skip-index`).

Tune duration via `.env`: `TRAIN_MAX_STEPS` (default 200 in `.env.example` and
`compose.yaml`), `TRAIN_LORA_RANK` (16), `TRAIN_LORA_ALPHA` (32),
`TRAIN_MAX_SEQ_LENGTH` (2048).

## Optional: train after every commit

Install a post-commit hook into `CODEBASE_PATH` (it must be a git work tree;
the script copies the sample to `.git/hooks/post-commit`):

```bash
python scripts/host/install_train_hook.py
python scripts/host/install_train_hook.py --remove   # uninstall
```

The sample (`scripts/git-hooks/post-commit.train.sample`) is hard-wired: it
calls `wsl -d local-llm` and uses `/mnt/d/rest/pumba` as the repo path, then runs
`deploy.py train --incremental --skip-index`. Edit the sample *before*
installing if your WSL distro or repo path differs (the installer copies it),
and re-run `index` yourself afterwards. The hook skips when
`models/training/.train.lock` exists.

## Limitations

- **C++ only** (same chunker as the indexer).
- Does **not** replace RAG for precise, up-to-date code citations.
- Aggressive training can hurt tool-calling, run
  `python scripts/deploy.py check` (live functional checks against the running
  stack) after major train runs. `deploy.py test` does not help here: it uses
  the tiny CPU model, not your adapter.
- First training needs the HF base; serving needs `adapter.gguf` on the host
  under `./models/adapters/latest/`.

See also [Architecture overview](../architecture/overview.md).
