# Model for about twenty users on the EVO-X5

> **Status**: recommendation not applied. Calculation dated 2026-11-07 (file creation date): the `config/profiles/evo-x5.env` profile still serves Mistral Small 4 (`-c 262144`, no `--parallel`) and the fallback described here is the current setting; Qwen3-Coder-30B-A3B is not served on this host. The external figures (AMD and GMKtec datasheets, Hugging Face, llama.cpp `b10216`) are those collected that day. · **Last verified**: 2026-11-12

Research note.

## Question

Which AI model is best suited to the EVO machine, so that about twenty users can use it in parallel?

The target service is the local coding assistant: OpenAI-compatible `llama-server`, tool calls, RAG. Only one chat model is loaded (`LLM_MODELS_MAX=1` in `CONTRACTS.md`). The embedder and the reranker stay on the CPU (`EMBEDDER_NGL=0`, `RERANKER_NGL=0` in `config/profiles/evo-x5.env`).

## Short answer

**Qwen3-Coder-30B-A3B-Instruct in Q4_K_M, pool `-c 655360` (20 × 32,768), `--parallel 20`, K and V cache in `q8_0`.** The weights are **17.282 GiB**, the KV cache for these 20 slots is **31.875 GiB**, and the memory-bandwidth share is about **6.8 tok/s per user**. Twenty in-flight generations fit in memory. They fall below the 10 tok/s floor. That floor holds for **13** slots of the same model (about 10.5 tok/s each).

The fallback is the already pinned Mistral Small 4, UD-Q5_K_M. Twenty 256k windows also fit in memory (weights 83.037 GiB, KV 59.766 GiB, sum 142.803 GiB), because llama.cpp `b10216` stores only the MLA latent. Bandwidth leaves only about **2.8 tok/s** for each user. The 10 tok/s floor holds for **5** slots.

## Machine

The repository states "GMKtec EVO-X5 Pro, Ryzen AI Max+ PRO 495, Radeon 8065S, 192 GB LPDDR5X, P-MODE Performance 120 W sustained" (`config/profiles/evo-x5.env`, `docs/guides/evo-x5.md`). The vendor datasheets confirm this SKU. The Ryzen AI Max+ 395 is a different processor.

AMD processor datasheet, [Ryzen AI Max+ PRO 495](https://www.amd.com/en/products/processors/laptop/ryzen-pro/ai-max-pro-400-series/amd-ryzen-ai-max-plus-pro-495.html):

| Field | Value |
| --- | --- |
| Cores / threads | 16 / 32, Zen 5 |
| Clock | base 3.1 GHz, boost up to 5.2 GHz |
| Default TDP | 55 W |
| cTDP | 45 to 120 W |
| iGPU | Radeon 8065S, 40 compute units, 3000 MHz |
| Memory | LPDDR5x 256-bit, maximum 192 GB, LPDDR5x-8533 |
| NPU | up to 55 TOPS |

The same datasheet for the [Ryzen AI Max+ 395](https://www.amd.com/en/products/processors/laptop/ryzen/ai-300-series/amd-ryzen-ai-max-plus-395.html) gives a Radeon 8060S, a maximum of 128 GB and LPDDR5x-8000, cTDP 45 to 120 W, default TDP 55 W, 16 cores.

The AMD [Ryzen AI Halo platform with the PRO 495](https://www.amd.com/en/products/processors/desktops/ryzen/ryzen-ai-halo/ryzen-ai-max-plus-pro-495.html) lists 16 cores, 32 threads, Radeon 8065S, 40 CU, 192 GB, 8533 MT/s, **273 GB/s**, TDP 120 W.

The GMKtec [EVO-X5 Pro](https://www.gmktec.com/products/gmktec-evo-x5-pro-amd-ryzen-ai-max-pro-495-ai-mini-pc) product page lists the PRO 495, the Radeon 8065S, 40 CU, up to 3000 MHz, 16 cores / 32 threads, base 3.1 GHz, boost up to 5.2 GHz, and **192 GB LPDDR5X-8533** (8 × 24 GB, soldered). The Performance mode of the P-MODE button is written there as "120 W sustained, 160 W peak". Silent is 54 W there, Balanced 85 W. The same page says "Up to 160 GB VRAM". It does not publish a throughput in GB/s.

The throughput used below is that of the processor bus at the chassis speed. AMD publishes a 256-bit bus and LPDDR5x-8533. 8533 × 10^6 transfers/s × 32 bytes = **273.056 × 10^9 bytes/s**. The Halo page rounds this product to 273 GB/s for the same chip, the same capacity and the same speed. The tokens/s calculations use 273.056 × 10^9.

`scripts/host/evo_x5_serve.py`, `THREADS = "16"`, comments "Physical cores of the Ryzen AI Max+ PRO 495". The 16 cores are those of the AMD datasheet. `serve_env()` comments "about 176 Go on the 192 Go chassis". That is a repository comment, not a measurement of the Vulkan heap. The GMKtec page, for its part, caps the graphics allocation at 160 GB.

Below, **GiB = 2^30 bytes**, the unit in which llama.cpp prints its buffers. Vendors write "192 GB" and "160 GB". 8 × 24 GB modules of this class show up to the OS as 192 GiB. If the datasheet's "192 GB" were decimal, the machine would have 178.8 GiB. The recommended totals (about 49 GiB) fit under both readings, and under GMKtec's 160 GB graphics allocation.

Reserve taken for the OS, the Vulkan driver, the embedder and the reranker: **16 GiB**. This is an assumption. It coincides with `LLM_GPU_MARGIN_MIB=16384` in the profile and with the "176 Go" comment, and neither of the two is a measurement. The pinned embedder, `nomic-embed-text-v1.5.Q8_0.gguf`, is 146,146,432 bytes (0.136 GiB) on [nomic-ai/nomic-embed-text-v1.5-GGUF](https://huggingface.co/api/models/nomic-ai/nomic-embed-text-v1.5-GGUF/tree/main). The reranker `bge-reranker-v2-m3-Q8_0.gguf` is 635,676,416 bytes (0.592 GiB) on [gpustack/bge-reranker-v2-m3-GGUF](https://huggingface.co/api/models/gpustack/bge-reranker-v2-m3-GGUF/tree/main). Both stay on the CPU. The usable budget retained for weights + KV is therefore **176 GiB**, remembering that GMKtec also publishes a graphics cap of 160 GB.

## Concurrency in llama-server `b10216`

Tag pinned by `LLAMA_CPP_TAG = "b10216"` in `scripts/host/evo_x5_serve.py`. The files cited are those of this tag in [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp).

`--parallel` / `-np` is the number of server slots. In `common/arg.cpp`, the server example forces `params.n_parallel = -1` before argument parsing, and the help text says "number of server slots (default: -1, -1 = auto)". `tools/server/server.cpp` handles this value:

```text
if (params.n_parallel < 0) {
    params.n_parallel = 4;
    params.kv_unified = true;
}
```

Omitting `--parallel` therefore starts **4 slots** and enables the unified KV. The struct default, in `common/common.h`, is `n_parallel = 1` and `kv_unified = false`. The server replaces this pair as soon as the argument is absent. An explicit `--parallel 20` leaves `kv_unified` at false.

`-c` / `--ctx-size` is the pool size. `src/llama-context.cpp` aligns `n_ctx` to a multiple of 256, then:

- Unified KV: `n_ctx_seq = n_ctx`. Each sequence sees the whole pool.
- Separate KV: `n_ctx_seq = n_ctx / n_seq_max`, realigned to 256. If the product does not land back on `n_ctx`, `n_ctx` is lowered to `n_ctx_seq × n_seq_max`.

`src/llama-model.cpp` builds the cache with `kv_size = cparams.n_ctx_seq`. `src/llama-kv-cache.cpp` sets `n_stream = unified ? 1 : n_seq_max` and allocates a tensor `(n_embd_k_gqa, kv_size, n_stream)`. The number of resident cells is `n_ctx_seq × n_stream`, hence the requested `-c` (after alignment), whether the slots are unified or separate. Twenty users at C tokens each require a pool of at least `20 × C` cells. Passing `-c 262144 --parallel 20` without unified gives each slot `262144 / 20`, realigned, and not 262144.

`tools/server/server-context.cpp` copies `llama_n_ctx_seq()` into `slot.n_ctx`. In unified mode, this number is the whole pool for each slot, while the cells remain shared. The server README (`tools/server/README.md`) describes `--kv-unified` as: "use single unified KV buffer shared across all sequences (default: enabled if number of slots is auto)".

Continuous batching is on by default (`-cb` / `--cont-batching`, "default: enabled", same README). `update_slots()` adds prompt tokens as long as the batch has not reached `n_batch` (`server-context.cpp`). The default `n_batch` is 2048 (`common/common.h`). A long prefill occupies this batch and delays decoding of the other slots. This tag does not publish a prefill throughput for this APU. No prefill tok/s is therefore put forward here.

When all slots are taken, `process_single_task` defers the task (`"no slot is available, defer task"`) and leaves it in the server queue. The HTTP request stays open until a slot frees up. The 503 documented in the README is that of `/health` while the model is loading, and that of `GET /slots?fail_on_no_slot=1` when no slot is free.

The quantized cache is in the README: `-ctk` / `-ctv` accept `f32`, `f16`, `bf16`, `q8_0`, `q4_0`, `q4_1`, `iq4_nl`, `q5_0`, `q5_1`, default `f16`. `-fa` is `on`, `off` or `auto`, default `auto`. `src/llama-context.cpp` enables Flash Attention as soon as V is quantized, and refuses to load if Flash Attention is forced off with a quantized V. The block sizes are in `ggml/src/ggml-common.h` (`ggml_half` = 2 bytes):

| Type | Block | Block bytes | Bytes per value |
| --- | --- | --- | --- |
| `q8_0` | 32 | 2 + 32 = 34 | 34/32 = 1.0625 |
| `q4_0` | 32 | 2 + 16 = 18 | 18/32 = 0.5625 |
| `q5_1` | 32 | 4 + 4 + 16 = 24 | 24/32 = 0.75 |
| `q4_K` | 256 | 4 + 12 + 128 = 144 | 144/256 = 0.5625 (the comment says 4.5 bits) |
| `q5_K` | 256 | 4 + 12 + 32 + 128 = 176 | 176/256 = 0.6875 (the comment says 5.5 bits) |

`Q4_K_M` and `Q5_K_M` are mixes of these blocks, plus a few wider tensors. The GGUF file size is the resident size of the weights. The decoding throughput below takes `file_size × (active parameters / total parameters)`. This assumes that the tensors actually read have the same average bytes per parameter as the whole file. For a MoE, the experts dominate the file. This is a throughput ceiling: the KV read at each token comes on top, and the ggml comment does not measure this APU.

Tool calls go through `--jinja`. The server README says that the default template comes from the model metadata, and that OpenAI function calling is served with `--jinja` (already the default at this tag). `serve_argv()` passes `--jinja` explicitly.

## Memory budget

Cache formula, read in `llama-hparams.cpp` and `llama-kv-cache.cpp`:

```text
n_embd_k_gqa = n_embd_head_k × n_head_kv
cells        = n_ctx_seq × n_stream
bytes_K      = layers × cells × n_embd_k_gqa × (block_bytes / block_size)
```

Same product for V when the V tensor is allocated. The two products add up. For an MLA model, `has_v = !is_mla`: the V tensor is not allocated.

### Mistral Small 4, current setting

Model card [mistralai/Mistral-Small-4-119B-2603](https://huggingface.co/mistralai/Mistral-Small-4-119B-2603): "119B parameters, with 6.5B activated per token", 128 experts of which 4 active, advertised 256k window, native function calls, per-request `reasoning_effort`. The vLLM example on the same card serves `--max-model-len 262144`. The `config.json` carries `max_position_embeddings` = 1,048,576, `original_max_position_embeddings` = 8192 and `factor` = 128 (8192 × 128 = 1,048,576). The repository and Mistral's vLLM example use 262,144.

Useful `config.json` fields: 36 layers, `kv_lora_rank` 256, `qk_rope_head_dim` 64, `qk_nope_head_dim` 64, `v_head_dim` 128, `num_key_value_heads` 32, `head_dim` 128, `sliding_window` null.

At `b10216`, `conversion/mistral3.py` sends `model_type == "mistral4"` to `DeepseekV2Model`. `conversion/deepseek.py`, `set_gguf_parameters`, forces `num_key_value_heads = 1` and writes:

```text
key_length     = kv_lora_rank + qk_rope_head_dim = 256 + 64 = 320
value_length   = kv_lora_rank = 256
key_length_mla = 64 + 64 = 128
value_length_mla = 128
```

`is_mla()` is true as soon as the two MLA lengths are non-zero. The allocated cache is therefore K only, dimension `320 × 1 = 320`, over 36 layers. 320 is divisible by 32.

```text
bytes per token (q8_0) = 36 × 320 × 34 / 32 = 36 × 340 = 12,240
```

UD-Q5_K_M weights, Unsloth quantizer, not the model author. The API [unsloth/Mistral-Small-4-119B-2603-GGUF, UD-Q5_K_M tree](https://huggingface.co/api/models/unsloth/Mistral-Small-4-119B-2603-GGUF/tree/main/UD-Q5_K_M):

| Shard | Bytes |
| --- | --- |
| `…-00001-of-00003.gguf` | 7,875,616 |
| `…-00002-of-00003.gguf` | 49,748,260,960 |
| `…-00003-of-00003.gguf` | 39,404,333,952 |
| Sum | **89,160,470,528 = 83.037 GiB** |

The LFS hashes are those pinned in `scripts/container/fetch_models.py`. The guide's "~83 GB" lands on this sum. Mistral does not publish this GGUF. The card points to the Unsloth repository for llama.cpp.

Current pool, `-c 262144`, `--parallel` absent. 262,144 is already a multiple of 256. The server goes to auto: 4 slots, unified KV, `n_ctx_seq = 262144`, a single stream. The resident cells are those of **one** pool of 262,144, shared by the 4 slots. `LLM_PARALLEL=1` is in the profile and is not read by `serve_argv()`.

```text
KV (1 × 262,144, q8_0) = 262,144 × 12,240 = 3,208,335,360 bytes = 2.988 GiB
Weights + KV = 83.037 + 2.988 = 86.025 GiB
```

Twenty simultaneously occupied windows, each at 262,144 tokens, require twenty times these cells (`--parallel 20`, separate KV, `-c` = 20 × 262,144 = 5,242,880, a multiple of 256 and of 20):

```text
KV = 20 × 3,208,335,360 = 64,166,707,200 bytes = 59.766 GiB
Weights + KV = 83.037 + 59.766 = 142.803 GiB
```

142.803 GiB fits within the 176 GiB of the assumption, and under a binary reading of GMKtec's 160 GB graphics cap. A calculation that multiplied the 32 heads of the `config.json`, K and V, would describe a different cache: `36 × 2 × 32 × 128 × 1.0625 = 313,344` bytes per token, that is 82.1 GiB for a single 262,144 window and 1,643 GiB for twenty. This tag does not allocate that tensor. It allocates the 320 latent.

The weights file plus the KV is a lower bound. The graph compute buffer comes on top. Its size is not in the model card.

### Twenty slots at 8,192, 16,384 and 32,768

Same MLA formula, q8_0, 12,240 bytes per token, weights 83.037 GiB:

| Context per slot | Pool cells | KV | Weights + KV |
| --- | --- | --- | --- |
| 8,192 | 163,840 | 1.868 GiB | 84.905 GiB |
| 16,384 | 327,680 | 3.735 GiB | 86.772 GiB |
| 32,768 | 655,360 | 7.471 GiB | 90.508 GiB |
| 262,144 | 5,242,880 | 59.766 GiB | 142.803 GiB |

All four fit in 176 GiB. Bandwidth, next section, rules out the 256k window as a setting for twenty simultaneous decodes.

## Bandwidth

Decoding rereads the active weights at each token. Aggregate ceiling:

```text
tok/s ≈ 273.056 × 10^9 / bytes_read_per_token
bytes_read_per_token ≈ file_size × (active / total)
per-user share = aggregate / number of in-flight generations
```

Mistral Small 4, UD-Q5_K_M, 6.5 / 119 of the file:

```text
active bytes  = 89,160,470,528 × 6.5 / 119 = 4,870,109,437
aggregate     = 273.056e9 / 4,870,109,437 = 56.07 tok/s
twenty in flight = 56.07 / 20 = 2.80 tok/s
10 floor      = 56.07 / 10 = 5 slots (11.2 tok/s at 5, 9.3 tok/s at 6)
```

Qwen3-Coder-30B-A3B-Instruct, Unsloth Q4_K_M file of 18,556,689,568 bytes (17.282 GiB), Qwen card "30.5B in total and 3.3B activated":

```text
active bytes  = 18,556,689,568 × 3.3 / 30.5 = 2,007,838,215
aggregate     = 273.056e9 / 2,007,838,215 = 136.0 tok/s
twenty in flight = 136.0 / 20 = 6.80 tok/s
10 floor      = 13 slots (10.5 tok/s). At 14 slots: 9.7 tok/s
```

A pure Q4_K at 4.5 bits, `3.3 × 10^9 × 144 / 256`, would give 147 tok/s aggregate and 7.4 tok/s per user. The Q4_K_M file is the figure retained, because the mix exceeds the `q4_K` block alone.

A pure Q4_K block applied to Qwen3-Next's "3B activated" would give 162 tok/s aggregate and 8.1 tok/s for twenty. That is a block ceiling, not a file size. Real decoding is lower once the KV and the quantization mix are added. None of these ceilings reaches 10 tok/s × 20. About 2.4 × 10^9 active parameters in pure Q4_K would be needed (`273.056e9 / 10 / 20 / 0.5625`). The cards examined are above that.

Prefill remains the other bottleneck: a prompt of several thousand tokens fills the 2048 batch before the other slots decode. Twenty users pasting a file at the same time serialize on this batch, even when the KV fits.

## Models examined

All parameter counts come from the card of the organization that published the weights. The GGUF file sizes come from Unsloth, identified as the quantizer. Mistral and Qwen do not publish these GGUFs.

| Model | Card | Total / active | Layers, KV, head | Advertised window | Quant and size | 20 × 32,768 in q8_0 | tok/s at 20 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Mistral Small 4 | [mistralai](https://huggingface.co/mistralai/Mistral-Small-4-119B-2603) | 119 B / 6.5 B | 36, MLA, K = 320, no V | 256k on the card, 262,144 in the vLLM example | UD-Q5_K_M Unsloth, 83.037 GiB | 90.5 GiB, fits | 2.8 |
| Qwen3-Coder-30B-A3B-Instruct | [Qwen](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct) | 30.5 B / 3.3 B | 48, 4 KV heads, head 128 | 262,144 native | Q4_K_M Unsloth, 17.282 GiB | 49.2 GiB, fits | 6.8 |
| Qwen3-30B-A3B | [Qwen](https://huggingface.co/Qwen/Qwen3-30B-A3B) | 30.5 B / 3.3 B | same 48 / 4 / 128 | 32,768 native, 131,072 with YaRN | same KV geometry as the coder | the KV fits like the coder | same order if the file is a Q4_K_M of similar size |
| Qwen3-Next-80B-A3B-Instruct | [Qwen](https://huggingface.co/Qwen/Qwen3-Next-80B-A3B-Instruct) | 80 B / 3 B | 48 layers, layout 12 × (3 DeltaNet + 1 attention), attention 2 KV × 256 | 262,144 native | no publisher GGUF found here | hybrid KV, recurrent state not computed | pure Q4_K ceiling 8.1 |
| gpt-oss-20b | [OpenAI](https://huggingface.co/openai/gpt-oss-20b) | 21 B / 3.6 B | 24 layers, 8 KV, head 64, sliding window 128 on every other layer | 131,072, YaRN from 4096 | MXFP4 checkpoint, Harmony format mandatory | not computed (sliding window) | pure Q4_K ceiling 6.7 |
| gpt-oss-120b | same card | 117 B / 5.1 B | none | none | none | none | pure Q4_K ceiling 4.8 |
| Qwen3-32B | [Qwen](https://huggingface.co/Qwen/Qwen3-32B) | 32.8 B, dense | 64, 8 KV, head 128 | 32,768 native, 131,072 with YaRN | pure Q4_K computed: 17.2 GiB | KV 63.8 GiB, sum ~81 GiB, fits | 0.74 |
| Qwen3-14B | [Qwen](https://huggingface.co/Qwen/Qwen3-14B) | 14.8 B, dense | 40, 8 KV, head 128 | 32,768 native, 131,072 with YaRN | pure Q4_K computed: 7.8 GiB | KV 39.8 GiB, fits | 1.6 |
| Qwen2.5-Coder-32B-Instruct | [Qwen](https://huggingface.co/Qwen/Qwen2.5-Coder-32B-Instruct) | 32.5 B, dense | 64, 8 KV, `head_dim` absent, 5120/40 = 128 | card: 131,072. `config.json`: `max_position_embeddings` 32,768, `sliding_window` 131,072 | same order as a dense 32 B | fits in memory | about 0.7 |
| Mistral Small 3.1 24B | [mistralai](https://huggingface.co/mistralai/Mistral-Small-3.1-24B-Instruct-2503) | 24 B, dense | 40, 8 KV, head 128 | 128k | pure Q4_K computed: 12.6 GiB | KV 39.8 GiB, fits | 1.0 |
| Devstral Small 1.1 | [mistralai](https://huggingface.co/mistralai/Devstral-Small-2507) | 24 B, fine-tune of Small 3.1, text only | same family | 128k, Mistral function calling | same order as Small 3.1 | fits | about 1.0 |

Dense KV in q8_0: `2 × layers × KV_heads × head × 34/32` bytes per token, then × 20 × 32,768. For Qwen3-32B: `2 × 64 × 8 × 128 × 1.0625 = 139,264` bytes per token, × 655,360 = 63.8 GiB. For Qwen3-14B and Small 3.1: `2 × 40 × 8 × 128 × 1.0625 = 87,040` bytes per token, × 655,360 = 39.8 GiB. Dense throughput rereads all parameters, hence `parameters × 144/256` for a pure Q4_K.

Gemma 3 27B (`google/gemma-3-27b-it`) and Llama 4 Scout (`meta-llama/Llama-4-Scout-17B-16E-Instruct`) answered HTTP 401 on their `config.json`. They are not in the arithmetic. `b10216` does have a `GEMMA3` arch and, when `swa_type` is not null, a `llama_kv_cache_iswa` cache whose sliding layers are bounded by `n_swa` (`src/llama-kv-cache-iswa.cpp`). Without the `config.json`, the number of full layers remains unknown.

Qwen3-Coder is the retained model. The card describes it for agentic coding, with a function-call format, 3.3 B active and 262,144 native tokens. Twenty slots of 32,768 are inside that window, without YaRN. Q4_K_M is the smallest llama.cpp quantization among Q4_K_M, Q5_K_M and Q8_0 on the Unsloth repository (Q5_K_M = 21,725,584,544 bytes, Q8_0 = 32,483,935,392 bytes). The Q5_K_M of the same file would give `21,725,584,544 × 3.3 / 30.5 = 2,350,799,966` active bytes, 116 tok/s aggregate, 5.8 tok/s at twenty, and 11 slots at the 10 floor.

Qwen3-Next has fewer active parameters (3 B) and few full-attention layers. Its linear state is not in the GQA formula, and no publisher GGUF size was found. gpt-oss-20b activates 3.6 B, requires the Harmony format, and its published checkpoint is MXFP4. The dense 14 B to 32 B models fit in memory at 32,768 × 20 and land between 0.7 and 1.6 tok/s per user.

The fallback remains Mistral Small 4 UD-Q5_K_M: the card positions it as an agent, coding and function-calling model, and the repository already serves it. Twenty slots fit. The usable throughput is that of 5 in-flight generations.

## Flags, today and for twenty slots

`serve_argv()` in `scripts/host/evo_x5_serve.py` sends: `-m` the first shard, `--alias mistral-small-4`, `--host 0.0.0.0`, `--port`, `--jinja`, `--chat-template-kwargs` with `reasoning_effort`, `--temp`, `-ngl 999`, `-fa on`, `-c` the value of `LLM_CTX_SIZE` (262,144), `-t 16`, `-tb 16`, `--cache-type-k q8_0`, `--cache-type-v q8_0`, `--metrics`. `--fit` is absent, as the guide says. `--parallel` is absent, so the `b10216` binary takes 4 slots and the unified KV. `LLM_PARALLEL=1`, `LLM_GPU_MARGIN_MIB` and `LLM_CACHE_RAM_MIB` are not read by this argument builder.

For twenty equal slots of the coder, at 32,768 tokens each:

```text
llama-server
  -m Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf
  --alias qwen3-coder-30b
  --jinja
  -ngl 999
  -fa on
  -c 655360
  --parallel 20
  --no-kv-unified
  --cache-type-k q8_0
  --cache-type-v q8_0
  -t 16
  -tb 16
```

`-c 655360` is the pool. `--parallel 20` with `--no-kv-unified` gives `n_ctx_seq = 32768` (655,360 / 20, already a multiple of 256). `-fa on` is required by the `q8_0` V. `--jinja` serves the template embedded in the GGUF.

The same model at the 10 tok/s floor is served with `--parallel 13` and `-c 425984` (13 × 32,768). 425,984 is divisible by 256 and by 13.

The Mistral fallback, five slots at the 262,144 window, is served with the already pinned GGUF, `-c 1310720` (5 × 262,144), `--parallel 5`, `--no-kv-unified`, `-fa on`, `q8_0` caches. 1,310,720 is divisible by 256 and by 5. KV = 5 × 2.988 GiB = 14.941 GiB. Weights + KV = 97.978 GiB. Throughput ≈ 56.07 / 5 = 11.2 tok/s.

`LLM_MODELS_MAX=1` (`CONTRACTS.md`, `compose.yaml`) loads only one chat GGUF. On this profile the `llm` container is not started (`LLM_SERVE=host`): the host process loads the file passed to `-m`.

The agent, for its part, bounds `/chat` at `AGENT_MAX_CONCURRENT` (default 2, `services/agent/main.py`, `CONTRACTS.md`). Beyond that, HTTP 503, with no queue. The OpenAI clients plugged into port 8080 (Continue, in `docs/guides/evo-x5.md`) talk to `llama-server` and go through the slots above. The agent page, for its part, stays at two turns until this variable is raised.

## Twenty users in parallel

"In parallel" means twenty in-flight generations, not twenty connected people of whom only one request is active.

On this machine, at 273.056 GB/s:

- The Q4_K_M coder holds twenty KV caches of 32,768 and the weights (49.2 GiB). Each in-flight generation receives about 6.8 tok/s. This is a shared queue, under the 10 tok/s floor.
- The largest number of coder generations that stays above 10 tok/s is **13**.
- Mistral Small 4 holds twenty 256k windows in memory (142.8 GiB) and gives about 2.8 tok/s to each. The 10 tok/s floor holds for **5** generations.
- A dense 14 B to 32 B model holds the KV of twenty short contexts and stays between one and two generations at the 10 tok/s floor.

The model to load for this service is therefore the coder in Q4_K_M, with a pool of 655,360 tokens and 20 slots if the goal is for twenty contexts of 32,768 to reside together, knowing that interactive throughput stops at 13 in-flight generations.

## Sources

Pages opened:

- https://www.amd.com/en/products/processors/laptop/ryzen-pro/ai-max-pro-400-series/amd-ryzen-ai-max-plus-pro-495.html
- https://www.amd.com/en/products/processors/laptop/ryzen/ai-300-series/amd-ryzen-ai-max-plus-395.html
- https://www.amd.com/en/products/processors/desktops/ryzen/ryzen-ai-halo/ryzen-ai-max-plus-pro-495.html
- https://www.gmktec.com/products/gmktec-evo-x5-pro-amd-ryzen-ai-max-pro-495-ai-mini-pc
- https://github.com/ggml-org/llama.cpp/blob/b10216/tools/server/README.md
- https://github.com/ggml-org/llama.cpp/blob/b10216/tools/server/server.cpp
- https://github.com/ggml-org/llama.cpp/blob/b10216/tools/server/server-context.cpp
- https://github.com/ggml-org/llama.cpp/blob/b10216/common/arg.cpp
- https://github.com/ggml-org/llama.cpp/blob/b10216/common/common.h
- https://github.com/ggml-org/llama.cpp/blob/b10216/src/llama-context.cpp
- https://github.com/ggml-org/llama.cpp/blob/b10216/src/llama-model.cpp
- https://github.com/ggml-org/llama.cpp/blob/b10216/src/llama-hparams.h
- https://github.com/ggml-org/llama.cpp/blob/b10216/src/llama-hparams.cpp
- https://github.com/ggml-org/llama.cpp/blob/b10216/src/llama-kv-cache.cpp
- https://github.com/ggml-org/llama.cpp/blob/b10216/src/llama-kv-cache-iswa.cpp
- https://github.com/ggml-org/llama.cpp/blob/b10216/ggml/src/ggml-common.h
- https://github.com/ggml-org/llama.cpp/blob/b10216/conversion/mistral3.py
- https://github.com/ggml-org/llama.cpp/blob/b10216/conversion/deepseek.py
- https://huggingface.co/mistralai/Mistral-Small-4-119B-2603
- https://huggingface.co/mistralai/Mistral-Small-4-119B-2603/raw/main/config.json
- https://huggingface.co/mistralai/Mistral-Small-4-119B-2603/raw/main/README.md
- https://huggingface.co/api/models/unsloth/Mistral-Small-4-119B-2603-GGUF/tree/main/UD-Q5_K_M
- https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct
- https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct/raw/main/config.json
- https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct/raw/main/README.md
- https://huggingface.co/api/models/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF/tree/main
- https://huggingface.co/Qwen/Qwen3-30B-A3B
- https://huggingface.co/Qwen/Qwen3-30B-A3B/raw/main/config.json
- https://huggingface.co/Qwen/Qwen3-30B-A3B/raw/main/README.md
- https://huggingface.co/Qwen/Qwen3-32B/raw/main/config.json
- https://huggingface.co/Qwen/Qwen3-32B/raw/main/README.md
- https://huggingface.co/Qwen/Qwen3-14B/raw/main/config.json
- https://huggingface.co/Qwen/Qwen3-14B/raw/main/README.md
- https://huggingface.co/Qwen/Qwen2.5-Coder-32B-Instruct/raw/main/config.json
- https://huggingface.co/Qwen/Qwen2.5-Coder-32B-Instruct/raw/main/README.md
- https://huggingface.co/Qwen/Qwen3-Next-80B-A3B-Instruct/raw/main/config.json
- https://huggingface.co/Qwen/Qwen3-Next-80B-A3B-Instruct/raw/main/README.md
- https://huggingface.co/openai/gpt-oss-20b/raw/main/config.json
- https://huggingface.co/openai/gpt-oss-20b/raw/main/README.md
- https://huggingface.co/mistralai/Mistral-Small-3.1-24B-Instruct-2503/raw/main/config.json
- https://huggingface.co/mistralai/Mistral-Small-3.1-24B-Instruct-2503/raw/main/README.md
- https://huggingface.co/mistralai/Devstral-Small-2507/raw/main/README.md
- https://huggingface.co/api/models/nomic-ai/nomic-embed-text-v1.5-GGUF/tree/main
- https://huggingface.co/api/models/gpustack/bge-reranker-v2-m3-GGUF/tree/main
- https://huggingface.co/google/gemma-3-27b-it/raw/main/config.json (HTTP 401)
- https://huggingface.co/meta-llama/Llama-4-Scout-17B-16E-Instruct/raw/main/config.json (HTTP 401)

Repository files read:

- `config/profiles/evo-x5.env`
- `docs/guides/evo-x5.md`
- `scripts/host/evo_x5_serve.py`
- `scripts/container/fetch_models.py`
- `CONTRACTS.md`
- `compose.yaml`
- `services/agent/main.py`
