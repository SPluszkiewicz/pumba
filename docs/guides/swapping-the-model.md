# Swapping the model

## Changing the chat model

1. Add a `ModelSpec` for it in `scripts/container/fetch_models.py` - Hugging Face
   `repo_id`, `filename` and the file's SHA256 (the Git LFS `oid` shown on
   the repo's file page) - or manually place a `.gguf` file under
   `./models/`. The transfer is done by `huggingface_hub.hf_hub_download`
   (resumable), the pinned SHA256 is then verified by the script itself.
2. Reload the agent page. The model list is every chat `.gguf` in `./models/`
   (embedder and reranker files are hidden). Choose one and press **Charger**.
   `llama-server` unloads the current model and loads the new one
   (`POST /models/load`). No image rebuild.
3. `LLM_MODEL_PATH` in `.env` is only the model loaded on the next container
   start. Change it when the choice should survive a recreate of `llm`.

The router id is the file name without `.gguf`. `LLM_ROUTER=1` and
`LLM_MODELS_MAX=1` are the defaults: the parent `llama-server` has no
`-m`, and a second model is loaded only after the first is unloaded.
`python scripts/host/context_limits.py` shows which context length fits
this GPU for that file.

**Keep Q5_K_M or better.** `CONTRACTS.md` pins the chat model's minimum
quantization for a specific reason: sub-Q5 quants measurably increase the
rate of malformed tool-call JSON from `llama.cpp`'s grammar-constrained
sampling, which `agent`'s tool loop has to detect and recover from (see
[Tool calling](../concepts/tool-calling.md)). A smaller/more-quantized model
will work, but expect the tool-calling retry/degrade path to trigger more
often.

**Verify the chat template supports tool calling.** `llm` is started with
`--jinja`, which requires the GGUF's embedded chat template to actually
render tool definitions the way `agent`'s OpenAI-style `tools` schema
expects. Not every instruction-tuned model's template does this correctly -
check the model card, or test with a simple tool-calling request via
`curl` against `llm`'s `/v1/chat/completions` directly before wiring it into
the full stack.

## Changing the embedding model

Don't, casually. `EMBEDDER_MODEL_PATH` and the embedding dimension are
load-bearing: the indexer hardcodes `VECTOR_SIZE = 768` when creating
Qdrant's `code_chunks` collection to match nomic-embed-text-v1.5's output
dimension. Swapping to a model with a different embedding dimension
requires:

1. Dropping and recreating the Qdrant collection (see
   [Reindexing the codebase](reindexing-the-codebase.md)).
2. Updating `VECTOR_SIZE` in `services/indexer/vector_store.py`.
3. Re-checking the prefixing convention - `search_document: `/`search_query:
   ` is specific to nomic-embed-text-v1.5's training; a different model may
   need different (or no) prefixes, and getting this wrong silently
   degrades retrieval rather than erroring.
4. Re-running the full indexing pipeline from scratch.

## Cases

`config/profiles/` holds one file per case. `python scripts/deploy.py gpu-profile <name>` writes it into `.env`. The larger GGUF files are optional:

```bash
python scripts/deploy.py fetch-extra 24g-30b
python scripts/deploy.py gpu-profile 24g-30b
```

`16g`, `24g`, and `48g` stay on the 14B. `24g-30b` and `48g-30b` are Qwen3-Coder-30B-A3B (Q5 and Q8). `48g-32b` is Qwen2.5-Coder-32B Q5. The table is in [Troubleshooting GPU](troubleshooting-gpu.md).

`evo-x5` is the GMKtec EVO-X5 Pro (Ryzen AI Max+ PRO 495, 192 Go). It pins Mistral Small 4 Q5 and sets `LLM_GPU_ARG=0` so Compose does not ask for an NVIDIA device. `python scripts/deploy.py up` then starts llama-server on the host and does not start the CUDA `llm` container. The steps are in [Déployer sur l'EVO-X5](evo-x5.md).

## VRAM considerations

On a **16 GB** GPU the chat model is ~9.8 GiB (Qwen2.5-Coder-14B Q5_K_M).
The embedder adds ~0.3 GiB. If that OOMs, leave the embedder on CPU
(below).

An **RTX 6000** (Quadro 24 Go, or Ada 48 Go) holds that 14B Q5 at
`LLM_CTX_SIZE=32768` with a `q8_0` cache, plus the embedder and the
reranker. Leave `LLM_NGL=999` so leftover layers are not spilled onto
the Xeon. Do not raise the 14B context: 32 768 is the length that model was
trained for. The `evo-x5` profile is 262144, the Mistral Small 4 window.
Set `LLM_THREADS` to the Xeon's physical core count.

On **8–12 GB** cards, leave `embedder` on CPU:

```bash
EMBEDDER_GPU_ARG=0
EMBEDDER_NGL=0
```

Then rebuild and recreate:

```bash
podman compose build embedder
podman compose up -d --force-recreate embedder
```

If both `llm` and `embedder` run on GPU simultaneously, their VRAM
requirements add up — see [Troubleshooting GPU](troubleshooting-gpu.md) if
you hit OOM under load.
