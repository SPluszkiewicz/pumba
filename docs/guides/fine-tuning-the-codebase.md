# Fine-tuning the codebase (QLoRA)

The stack can **fine-tune** a lightweight LoRA adapter on your mounted C++
project so the chat model learns project-specific names and patterns. This
complements [RAG](../concepts/rag-explained.md): fine-tuning adjusts model
weights; RAG injects exact indexed snippets at query time.

| | RAG | QLoRA adapter |
|---|---|---|
| Updated by | `python scripts/deploy.py index` | `python scripts/deploy.py train` |
| Holds | Exact chunk text | Approximate project knowledge |
| Needs GPU at query time | No (embedder only) | No (adapter baked into llm) |

## One-time setup

1. Set `CODEBASE_PATH` in `.env` (see [Reindexing the codebase](reindexing-the-codebase.md)).
2. Download Hugging Face base weights for training (~15 GiB, internet once):

```bash
podman compose --profile setup run --rm fetch-models \
  python scripts/container/fetch_models.py --hf-train-base
```

Inference uses `LLM_MODEL_PATH` (Qwen2.5-Coder-14B). The trainer base stays
Qwen2.5-7B-Instruct, so an adapter from `train` does not load on the 14B.

## Train on the codebase

Full pass (first time or after large changes):

```bash
python scripts/deploy.py train
```

Incremental (git diff since last trained commit):

```bash
python scripts/deploy.py train --incremental
```

Dataset only (no GPU):

```bash
python scripts/deploy.py train --dry-run
```

What `train` does:

1. Build `models/training/{commit}/dataset.jsonl` from tree-sitter chunks.
2. Run QLoRA in the `train` compose profile (GPU).
3. Write `models/adapters/latest/` and convert `adapter.gguf`.
4. Set `LLM_LORA_PATH` in `.env` if unset.
5. Recreate `llm` with `--lora`, then re-index (unless `--skip-index`).

Tune duration via `.env`: `TRAIN_MAX_STEPS` (default 200), `TRAIN_LORA_RANK`, etc.

## Optional: train after every commit

Install a post-commit hook into `CODEBASE_PATH`:

```bash
python scripts/host/install_train_hook.py
```

Edit `scripts/git-hooks/post-commit.train.sample` if your WSL distro or repo
path differs. The hook skips when `models/training/.train.lock` exists.

## Limitations

- **C++ only** (same chunker as the indexer).
- Does **not** replace RAG for precise, up-to-date code citations.
- Aggressive training can hurt tool-calling — run `python scripts/deploy.py test`
  after major train runs.
- First training needs the HF base; serving needs `adapter.gguf` on the host
  under `./models/adapters/latest/`.

See also [Architecture overview](../architecture/overview.md).
