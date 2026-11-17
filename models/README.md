# models/

GGUF model weights land here. This directory is gitignored except for this
file - never commit model weights.

Fetch them (inside a container, not on the host - see `CONTRACTS.md`):

```
podman compose --profile setup run --rm fetch-models
```

Add `--test` to fetch the tiny CPU-only model used by the `test` compose
profile / `tests/integration/test_smoke.py` instead of the full-size models.

Expected files after fetching (see `CONTRACTS.md` for exact repos/filenames):

- `Qwen2.5-Coder-14B-Instruct-Q5_K_M.gguf` - chat model (Qwen2.5-Coder-14B-Instruct), used by the `llm` service
- `nomic-embed-text-v1.5.Q8_0.gguf` - embedding model, used by the `embedder` service
- `bge-reranker-v2-m3-Q8_0.gguf` - reranker, used by the `reranker` service (not fetched by `--test`)
- `Qwen2.5-0.5B-Instruct-Q4_K_M.gguf` - tiny model, `test` profile only
