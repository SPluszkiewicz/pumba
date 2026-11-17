# Three models on the deploy: identity, metrics, where to plug in

> **Status**: informational, plan not applied. Neither Qwen3.8-27B nor Qwen3.8-Flash-Next is in `EXTRA_MODELS`, `PROFILE_EXTRA` or a profile; the llama.cpp pin stays at `b10216`. The hook point described, `scripts/host/profile_campaign.py`, exists. Dated 2026-11-10; the model identities and sizes are those read that day on Hugging Face. · **Last verified**: 2026-11-12

**Verdict:** the three names as written match official models. "qwen 3.8 27BB" is `Qwen/Qwen3.8-27B` (hybrid dense, 27B, the trailing B is extra). "qwen 38 flash next" is `Qwen/Qwen3.8-Flash-Next` (MoE, 125B with 6B active, plus 51B of n-gram embeddings and 4B of MTP). "mistral small 4" is still `mistralai/Mistral-Small-4-119B-2603`, host alias `mistral-small-4`, Unsloth UD-Q5_K_M GGUF whose LFS fingerprints have not moved. Only the 27B has a weights file that fits in a 24 GB or 48 GB GPU profile. Flash-Next and Mistral Small 4 fit, one at a time, on the EVO-X5 host. The pinned `b10216` binary names the 27B's `qwen35` architecture and does not name `qwen4_exp`. The `deploy.py eval` command does not do this comparison: it scores retrieval. The hook point for tasks and timings is `scripts/host/profile_campaign.py`.

Research note of 2026-11-10. The guides in `docs/` describe usage. The code, Compose and the tests own the behavior. When a guide and the code diverge, both are cited separately.

## Question

Which models hide behind "qwen 3.8 27BB", "qwen 38 flash next" and "mistral small 4", and how do we run the same tasks through all three and then read quality and timing metrics, with what this repository already serves?

## Short answer

The identifiers retained are `Qwen/Qwen3.8-27B`, `Qwen/Qwen3.8-Flash-Next` and `mistralai/Mistral-Small-4-119B-2603`. The third is already the GGUF of the `evo-x5` profile. The two Qwen are official safetensors weights; the GGUF files that `llama-server` can serve are at Unsloth, like the Mistral already pinned. The 27B in UD-Q5_K_M is 18.414 GiB and comes in under the 20.2 GiB file that the `24g-30b` profile already places on a 24 GB card. The smallest Flash-Next found, UD-IQ1_S, is 61.820 GiB: it exceeds 24 GB and 48 GB, and it fits within the EVO-X5's 176 GiB assumption. Mistral UD-Q5_K_M stays at 83.037 GiB, already on that host.

This repository measures the file retrieved, the first tool called, and five binary checks on `sample/`, plus a generation throughput on a 16-token ping. It does not produce a table of the three models. To get one, the `EXTRA_MODELS` catalog, a profile, and the profile campaign are the three places already written. Flash-Next additionally needs a `llama-server` newer than the `b10216` tag.

## The names, resolved

The strings in the question are not Hugging Face identifiers. Each one lands on a card in the official organization.

### "qwen 3.8 27BB" → `Qwen/Qwen3.8-27B`

The number 3.8 and the size 27B are those of the card [Qwen/Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B). The last B is an extra letter on "27B". The repository API, queried on 2026-11-10, gives `id` = `Qwen/Qwen3.8-27B`, `createdAt` = 2026-08-05, `pipeline_tag` = `image-text-to-text`, license `apache-2.0`. The card's `LICENSE` file is Apache 2.0, with the notice "Copyright 2026 Alibaba Cloud".

The card says: causal model with a vision encoder, **27B** parameters for the language model, 64 layers, native context of **262,144** tokens, extensible up to 1,000,000. The layout is `16 × (3 × (Gated DeltaNet → FFN) → 1 × (Gated Attention → FFN))`. The `config.json` has `model_type` = `qwen3_5`, `architectures` = `Qwen3_5ForConditionalGeneration`, `num_hidden_layers` = 64, `hidden_size` = 5120, `intermediate_size` = 17408, `num_attention_heads` = 24, `num_key_value_heads` = 4, `head_dim` = 256, `max_position_embeddings` = 262144. This `config.json` has no `num_experts` field. It is a hybrid dense model (linear attention, then full attention, one full layer every four), with a vision tower. It is not an MoE.

The safetensors API counts **27,781,427,952** parameters in BF16 for the whole repository, vision included. The card rounds the language model to 27B. It does not publish the vision tower's share separately. Thinking mode is on by default. The card says so, and the Jinja template returned by the API treats `enable_thinking` as true when it is absent, with `reasoning_effort` defaulting to `xhigh` (`xhigh`, `medium`, `low`).

The cards `Qwen/Qwen3.5-27B` and `Qwen/Qwen3.6-27B` are the previous dense generations, same size order (27B, 64 layers, context 262,144 on their cards). The "3.8" written in the question selects the 2026-08 card.

The official card says the artifacts target Transformers, vLLM, SGLang and TokenSpeed. It mentions neither a GGUF file nor llama.cpp. The GGUF used below is Unsloth's, `unsloth/Qwen3.8-27B-GGUF`, the same quantizer as the Mistral already in `fetch_models.py`. The Unsloth `README` reproduces the Qwen card and declares `base_model: Qwen/Qwen3.8-27B`, Apache 2.0 license.

The pages `https://qwen.ai/blog?id=qwen3.8` and `https://qwen.ai/blog?id=qwen3.8-flash-next`, cited in the bibliographies of the two cards, responded without article text (page empty on the client side). The facts above come from the cards and the `config.json` files, not from these posts.

### "qwen 38 flash next" → `Qwen/Qwen3.8-Flash-Next`

"38" is "3.8" without the dot. The card [Qwen/Qwen3.8-Flash-Next](https://huggingface.co/Qwen/Qwen3.8-Flash-Next) carries exactly this name. The API gives `createdAt` = 2026-08-24, `model_type` in the `config.json` = `qwen4_exp`, `architectures` = `Qwen4ExpForConditionalGeneration`. The repository license is `qwen-community-1.0` (`cardData.license` = `other`, `license_name` = `qwen-community-1.0`). The `LICENSE` file grants use, modification and hosting, with two conditions: display of the model name beyond 100,000,000 monthly active users or 20,000,000 USD of monthly revenue, and a separate license from Qwen if the licensee runs a commercial "Model as a Service" or "AI Work Assistant". Internal use that does not make the model accessible to a third party is excluded from this second condition. The full text is on the card.

The card gives: **125B** with **6B** activated per token, plus **51B** of n-gram embeddings and **4B** of MTP. 125 + 51 + 4 = 180. The safetensors API counts **179,999,981,424** BF16 parameters (and 35 in I64), which matches this total. Native context 262,144, extensible to 1,000,000. 48 layers, 512 experts, 10 routed plus 1 shared (`num_experts` = 512, `num_experts_per_tok` = 10, `shared_expert_intermediate_size` = 640 in the `config.json`). `hidden_size` = 2560, `num_key_value_heads` = 2, `head_dim` = 256, `ngram_vocab_size_base` = 20,000,000, `hc_count` = 4. It is a multimodal MoE, not a dense model.

The same card separates the hosted product **Qwen3.8-Flash** (1M context by default, built-in tools, Qwen Cloud API) from the open weights **Qwen3.8-Flash-Next**. The name written contains "next": the open weights are the right repository. The API SKU is not a file in this repository.

As with the 27B, the official card cites Transformers, vLLM, SGLang and TokenSpeed, and does not publish a GGUF. The GGUF shards are on `unsloth/Qwen3.8-Flash-Next-GGUF`.

### "mistral small 4" → the card already pinned

The card [mistralai/Mistral-Small-4-119B-2603](https://huggingface.co/mistralai/Mistral-Small-4-119B-2603), re-read on 2026-11-10, still says: MoE, 128 experts with 4 active, **119B** parameters, **6.5B** activated per token, **256k** context, text and image input, function calling, per-request `reasoning_effort` (`none` or `high`), Apache 2.0 license. It recommends vLLM and points llama.cpp to the Unsloth GGUF. The card's vLLM example serves `--max-model-len 262144`.

The repository API, `lastModified` 2026-07-15, counts 119,401,317,376 parameters (`F8_E4M3` 117,879,865,344 and `BF16` 1,521,452,032). The `config` returned by this API carries `quant_method` = `fp8`. The repository's `usedStorage` is 241,885,986,991 bytes. The comment in `fetch_models.py` says "~242 GiB" for the QLoRA base: this figure matches the repository storage in decimal gigabytes (241.9 × 10^9), not a 2^30 GiB (225.274 GiB). The API file list contains both `consolidated-*.safetensors` and `model-*.safetensors`. The GGUF served here is not this repository.

The three UD-Q5_K_M shards, re-read from the API tree, have the same sizes and the same LFS oids as `scripts/container/fetch_models.py` (comment "checked 2026-11-03"):

| Shard | Bytes | LFS oid |
| --- | --- | --- |
| `…-00001-of-00003.gguf` | 7,875,616 | `6d3a3d24…40e8e30` |
| `…-00002-of-00003.gguf` | 49,748,260,960 | `f1340e0d…76f44142` |
| `…-00003-of-00003.gguf` | 39,404,333,952 | `b6181cde…1c9ac434` |
| Sum | **89,160,470,528 = 83.037 GiB** | |

89,160,470,528 / 2^30 = 83.037. This is the same total as `docs/notes/verification/evo-x5-modele-20-utilisateurs.md`. The host alias is the constant `ALIAS = "mistral-small-4"` in `scripts/host/evo_x5_serve.py`. `CONTRACTS.md`, Models table: "`evo-x5` is Mistral Small 4 (`unsloth/Mistral-Small-4-119B-2603-GGUF`, UD-Q5_K_M, three shards, alias `mistral-small-4` on the host). […] The CUDA `llm` image does not serve it."

## Weights, and what fits in a profile

**GiB = 2^30 bytes**, as in the EVO note. The GGUF sizes are the `lfs.size` fields of the Hugging Face tree API, on 2026-11-10. The GGUF file is the resident weights. The KV cache and the compute buffer come on top. This note does not recompute the KV for `qwen35` or `qwen4_exp`: the EVO note read the llama.cpp `b10216` cache for Mistral (MLA latent) and for Qwen3-Coder, not for these two architectures. The comparisons below are therefore the weights file, plus the `--fit-target` margin already written in the profile, before the KV.

Official BF16, API parameters × 2 bytes:

```text
Qwen3.8-27B     27,781,427,952 × 2 = 55,562,855,904 bytes = 51.747 GiB
                repository usedStorage = 55,618,036,073 bytes = 51.798 GiB
Qwen3.8-Flash   179,999,981,424 × 2 = 359,999,962,848 bytes = 335.276 GiB
                repository usedStorage = 360,065,103,050 bytes = 335.337 GiB
```

These BF16 files are not the format served here. The `llm` container and the EVO host run `llama-server` on a GGUF.

Unsloth GGUF, files whose sum is the resident size:

| Model | File | Bytes | GiB |
| --- | --- | --- | --- |
| 27B | `Qwen3.8-27B-UD-Q4_K_M.gguf` | 16,464,440,224 | 15.334 |
| 27B | `Qwen3.8-27B-UD-Q4_K_XL.gguf` | 17,559,178,144 | 16.353 |
| 27B | `Qwen3.8-27B-UD-Q5_K_M.gguf` | 19,771,509,664 | 18.414 |
| 27B | `Qwen3.8-27B-Q8_0.gguf` | 29,047,086,048 | 27.052 |
| Flash-Next | UD-IQ1_S, 3 shards | 66,379,180,698 | 61.820 |
| Flash-Next | UD-Q2_K_XL, 4 shards | 79,580,752,666 | 74.115 |
| Flash-Next | UD-Q4_K_XL, 4 shards | 111,334,654,778 | 103.688 |
| Mistral Small 4 | UD-Q5_K_M, 3 shards | 89,160,470,528 | 83.037 |

The first Flash-Next UD-Q4_K_XL shard is 10,946,618 bytes. It is the file that `-m` must name, following the model of the three Mistral shards (`-00001-of-00004`). The vision projector `mmproj-F16.gguf` is a separate file (927,607,488 bytes in the 27B repository, 904,004,000 bytes on Flash-Next). `containers/llm/entrypoint.sh` and `serve_argv()` do not pass `--mmproj`. A text task does not load this file.

Memory already written in the profiles:

| Profile | What the file says | `LLM_GPU_MARGIN_MIB` margin |
| --- | --- | --- |
| `16g` | `16 Go (RTX 4080)` (16 GB) | 2048 (2 GiB) |
| `24g` | `memory.total` about 24,576 MiB, i.e. 24 GiB | 2048 |
| `24g-30b` | same card, weights "~20.2 GiB" | 512 (0.5 GiB) |
| `48g` | about 46,068 MiB with ECC (44.988 GiB), or 49,152 MiB without (48 GiB) | 2048 |
| `evo-x5` | 192 GB unified, `LLM_GPU_MARGIN_MIB=16384` | 16 GiB |

The EVO note retains a weights + KV budget of **176 GiB** (assumption aligned with this margin and with the comment `about 176 Go` (about 176 GB) in `serve_env()`), and recalls the "Up to 160 GB VRAM" ceiling from the GMKtec page. These two figures are those of that note, with the sources it cites. They are not re-measured here.

Consequence, weights file alone:

- **27B UD-Q5_K_M, 18.414 GiB.** With the 0.5 GiB margin of the `24g-30b` profile, the sum is 18.914 GiB. It is under the 24 GiB of the card and under the 20.2 GiB that this profile already loads. The Q8, 27.052 GiB, exceeds 24 GiB. With the 2 GiB margin of the `48g` profile, 27.052 + 2 = 29.052 GiB, under the 44.988 GiB of the ECC reading. The 27B in Q5 is therefore a 24 GB or 48 GB profile case, of the same kind as `24g-30b`. On the `16g` profile, UD-Q4_K_M (15.334 GiB) plus the 2 GiB margin makes 17.334 GiB, beyond the "16 GB" written in `16g.env`, before the KV.
- **Flash-Next.** UD-IQ1_S at 61.820 GiB exceeds 24 GiB and 48 GiB. UD-Q4_K_XL at 103.688 GiB does too. Both are under the 176 GiB of the EVO assumption, and under a 160 GiB reading of the graphics ceiling. They fit only on the EVO-X5 host, one model at a time.
- **Mistral, 83.037 GiB.** Already the `evo-x5` profile. Above 24 GB and 48 GB. `CONTRACTS.md` says the CUDA `llm` image does not serve it.

The three comparable files (27B Q5 18.414 + Flash Q4 103.688 + Mistral Q5 83.037) total 205.139 GiB. Above 176 GiB. `LLM_MODELS_MAX=1` (`CONTRACTS.md`) loads only one anyway. The comparison is a sequence of loads, not three servers.

## What this repository serves

Two paths, one binary.

The `llm` container (`containers/llm/entrypoint.sh`) runs `llama-server` with `--jinja`, `--metrics`, `-c`, `--parallel`, the cache types, `--fit on` and `--fit-target`. If `LLM_ROUTER` is 1 (the default of `CONTRACTS.md`), there is no `-m`: `--models-dir`, `--models-max` (default 1) and a preset whose section is the file stem, without `.gguf`. The published identifier is that stem. The entrypoint does not pass `--alias`.

The EVO host (`scripts/host/evo_x5_serve.py`) runs the same tag, `LLAMA_CPP_TAG = "b10216"`, on Vulkan, with `-m`, `--alias mistral-small-4`, `--jinja`, `--chat-template-kwargs` for `reasoning_effort`, `--metrics`, `-c` read from the profile (262144), `-fa on`, `q8_0` caches. `LLM_PARALLEL=1` is in `config/profiles/evo-x5.env` and is not read by `serve_argv()`.

`CONTRACTS.md`, pinned versions: llama.cpp tag `b10216`, dated 2026-07-31 in that file. The `src/llama-arch.h` header of this tag contains `LLM_ARCH_QWEN35` and `LLM_ARCH_QWEN35MOE`. It does not contain a `qwen4exp` architecture. The 27B's `config.json` has `model_type` = `qwen3_5`, hence the architecture name that this tag already knows. Flash-Next's `config.json` has `model_type` = `qwen4_exp`. The llama.cpp pull request ["model: add Qwen3.8-Flash-Next (qwen4exp)"](https://github.com/ggml-org/llama.cpp/pull/27742) was merged on 2026-08-27, after the date written for `b10216`. The binary that `install_pinned_server()` downloads does not contain this graph. This note did not run `llama-server` on the 27B's GGUF: the presence of `LLM_ARCH_QWEN35` says the tag has a name for this family, it does not prove that the August Unsloth file loads.

The optional catalog is `EXTRA_MODELS` in `scripts/container/fetch_models.py`. The current keys are `coder-30b-q5`, `coder-30b-q8`, `coder-32b-q5`, `mistral-small-4`. `scripts/host/deploy/__init__.py` maps the profiles:

```text
PROFILE_EXTRA = {
    "24g-30b": "coder-30b-q5",
    "48g-30b": "coder-30b-q8",
    "48g-32b": "coder-32b-q5",
    "evo-x5": "mistral-small-4",
}
```

`tests/scripts/test_deploy.py`, `test_gpu_profiles_cover_the_vram_and_model_cases`, freezes this set of four keys and the GGUF paths. `deploy.py up` does not download these files. The command is `python scripts/deploy.py fetch-extra <profile>`.

The agent sends `model=active_model()` (`services/agent/llm_client.py`). At startup, this name is the stem of `LLM_MODEL_PATH`. In router mode, `sync_active()` replaces it with the identifier that `GET /models` marks as loaded (`services/agent/model_catalog.py`). On the host, `--alias` is `mistral-small-4` while the file stem is `Mistral-Small-4-119B-2603-UD-Q5_K_M-00001-of-00003`. `docs/notes/verification/settings-comprehensibilite.md` already describes this gap. A comparison that changes file must make the agent send the name that the server accepts.

## What the evaluation already measures

Four entry points, four different objects.

**`python scripts/deploy.py eval`.** `scripts/host/deploy/cli.py` describes the subcommand: "retrieval baseline on a registered checkout (lookup only, no chat model)". `cmd_eval` calls `eval_checkout.main`. `scripts/host/eval_checkout.py` posts `POST /lookup` with `include_candidates` and places the expected file in the prefetch, the reranked window, or the 5 hits (`in the 5`, `dropped by the noise filter`, `in the prefetch, not in the 5`, `absent from the prefetch`). The guide `docs/notes/verification/retrieval-baseline.md` says the same: "It does not call the chat model". Exit code 0 means every lookup answered. The guide and the code agree. This command does not distinguish two chat models.

**`scripts/container/eval_rag.py`.** The docstring says the script checks that the expected file and symbol are in `sources` of `POST /chat`, and that the LLM output is not the criterion ("LLM output is not byte-stable"). No duration, no token count.

**`scripts/container/eval_tools.py`.** Three scenarios in `eval_tools_scenarios.json` (`build` / preset `release`, `compile_file` on `src/AgentClient.cpp`, `test` with the filter `json_round_trip`). The score is the first tool and a subset of arguments. The docstring says "no LLM-as-judge". No stopwatch in the report.

**`scripts/host/profile_campaign.py`.** It is the only script that chains a model, a server ping, and scored tasks. Out of CI. `--run` applies each profile, recreates the `llm` container, waits for `GET /health`, checks that the file stem is in `GET /v1/models`, then:

- S0: `POST /v1/chat/completions`, message `Réponds exactement : pong` (the French sample prompt sent to the model; meaning: "Answer exactly: pong"), `max_tokens` 16, `temperature` 0. It records `s0_seconds` (wall clock) and, if the JSON contains `timings.predicted_per_second`, that throughput.
- S1 to S5: `POST /chat` on the agent, `tools: true`, workspace of `sample/`. S1 requires a `test` call whose arguments contain `known_bug`. S2 requires both paths `calculator_test.cpp` and `calculator.cpp` in the answer. S3 requires an absence formula and rejects a source path. S4 and S5 require a unified diff that passes `git apply --check` and touches the expected files. Each scenario is `ok` or `raté` (the program's literal; "failed"), with `seconds`.

The report is `state/profile-campaign.json`. `profiles_for_vram()` returns `24g` and `24g-30b` between 20,000 and 40,000 MiB, and `48g`, `48g-30b`, `48g-32b` above. It does not return `evo-x5`: this profile is run with `--profiles evo-x5`. That path does not recreate the `llm` container. It recreates `agent`, stops an `llm` container left there, and calls `_start_host_llm`. The questions for this profile are `scripts/host/evo_campaign_prompts.json`. An id other than `S1` to `S5` does not apply the sample's grid: the row keeps the clock, the `/metrics` delta, and the answer.

The agent's route can still discard the tools. `services/agent/main.py`: if `decide()` returns `direct`, `enable_tools` is forced to false and there is no retrieval. If the route is `code`, `enable_tools = payload.tools`, then retrieval and the tool loop. `CONTRACTS.md` describes the two routes the same way. A `tools: true` in the campaign does not guarantee the `code` route.

## Metrics already exposed

On the `llama-server` `b10216` side, README `tools/server/README.md` of this tag:

- `GET /health`: 503 while loading, 200 `{"status":"ok"}` when the model is ready. `profile_campaign._wait_healthy` and the host startup use it.
- `GET /metrics`, only if `--metrics` is passed. The container entrypoint and `serve_argv()` pass it. In router mode, the README requires the `?model=` parameter, otherwise HTTP 400. The container is in router mode by default. The EVO host, launched with `-m`, is not. The documented series: `llamacpp:prompt_tokens_total`, `llamacpp:prompt_seconds_total`, `llamacpp:prompt_tokens_seconds`, `llamacpp:tokens_predicted_total`, `llamacpp:tokens_predicted_seconds_total`, `llamacpp:predicted_tokens_seconds`, `llamacpp:requests_processing`, `llamacpp:requests_deferred`, plus the context high-water mark and the busy slots.
- The JSON of `POST /v1/chat/completions` contains `timings`: `cache_n`, `prompt_n`, `prompt_ms`, `prompt_per_token_ms`, `prompt_per_second`, `predicted_n`, `predicted_ms`, `predicted_per_token_ms`, `predicted_per_second`. And `usage` (`prompt_tokens`, `completion_tokens`, `total_tokens`). `prompt_ms` is the prefill time of that request. The repository does not publish a series named TTFT. The campaign copies only `predicted_per_second`, and only on the S0 ping.

On the agent side, `services/common/metrics.py` and `docs/guides/observability.md` expose `agent_turn_duration_seconds` (labels `route`, `outcome`: turn clock, from the route decision to after the reflection), `agent_turn_iterations`, `agent_turn_tool_calls`, `agent_context_tokens`, `rag_retrieval_duration_seconds`, `tool_call_duration_seconds`. The guide says the `obs` profile is not started by `deploy.py up`. These histograms have no model label.

For a task comparison, the numbers already produced are therefore:

| Question | Where to read it | What it covers |
| --- | --- | --- |
| Is the server ready | `GET /health` | Loading, not quality |
| Prefill and generation | `timings` of the OpenAI chat, or `/metrics` | Duration, tokens, tokens/s. S0 keeps only the generation throughput |
| The agent turn | `agent_turn_duration_seconds`, `seconds` of the campaign | Wall clock, route included |
| The right file | `eval` / `eval_rag` | Retrieval, independent of the chat model for `eval` |
| The right tool | `eval_tools`, S1 of the campaign | Name and arguments, not free text |
| The answer on `sample/` | S2 to S5 | Citations, absence, applicable diff |

A free-text judge, an exact match on an open answer, and a named TTFT are not in these scripts. The S1 to S5 checks and `eval_tools` are the task score already written. Aligning them on the three models is enough for a table. Adding an external benchmark framework is not this repository's entry point.

Two settings distort a duration comparison if they are left to diverge. The Qwen3.8 card leaves thinking on, effort `xhigh` by default, and recommends a long output. The Mistral template, in the card's API, sets `reasoning_effort` to `none` when it is absent. Only `serve_argv()` passes `--chat-template-kwargs`. The container entrypoint does not pass it. The S0 ping asks for `temperature` 0 and 16 tokens, which is not the "thinking" setting of the Qwen card. A comparable run fixes the same reasoning effort on the three servers, then reads `predicted_n` together with the duration.

## Where to plug in

Stay on the existing files. No new harness.

1. **Catalog.** Add two `EXTRA_MODELS` entries, following the model of `mistral-small-4`: Unsloth `repo_id`, `filename` of the first shard or of the single file, `sha256` equal to the LFS oid of the API tree (the file's comment says this oid is the SHA256). For a 27B comparable to the Q5 already chosen on 24 GB: `unsloth/Qwen3.8-27B-GGUF`, file `Qwen3.8-27B-UD-Q5_K_M.gguf`, oid `2de73110cb254cbf09b54b717578dadff12ef1194e7271527e68202f39ba4bfd`. For Flash-Next on the EVO, UD-Q4_K_XL, first shard `UD-Q4_K_XL/Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf`, oid of the first shard `acc44040f74d0e55fb89c3967056cce5dbda1bcc52e909c3ba6fc72b5e9c0858`, and the other three shards in `extra_files`. The oids of shards 2 to 4 are in the `UD-Q4_K_XL` tree cited in the sources. `deploy.py up` will not download them as long as no profile points `fetch-extra` at them.

2. **Profile.** A 24 GB or 48 GB profile whose `LLM_MODEL_PATH` ends with `Qwen3.8-27B-UD-Q5_K_M.gguf`, `LLM_SERVE=container`, and a key in `PROFILE_EXTRA`. The test `test_gpu_profiles_cover_the_vram_and_model_cases` will have to know this key: today it asserts the exact set. Flash-Next and the Mistral stay host paths: `LLM_SERVE=host`, `LLM_MODEL_PATH` pointing to the first shard, and `ALIAS` in `evo_x5_serve.py` (today the constant `mistral-small-4`). Short aliases of the same kind would be `qwen3.8-27b` and `qwen3.8-flash-next`. The container, for its part, publishes the file stem, not this alias, until the entrypoint gains `--alias`.

3. **Binary.** Flash-Next needs a `llama-server` that contains the graph merged on 2026-08-27. `install_pinned_server()` and `CONTRACTS.md` stay on `b10216` until this pin is changed. The 27B and the Mistral are the two that this tag can target: Mistral is already served, the 27B matches the `QWEN35` name in the same header. The actual loading of the 27B GGUF remains to be observed at the first `/health`.

4. **Tasks and table.** `profile_campaign.py --run --profiles evo-x5` is the host loop: it applies the profile, starts `llama-server`, pings `/v1/chat/completions`, then sends the prompts from the JSON file. The ping copies `timings` (`prompt_ms`, `predicted_n`, `predicted_per_second`). Each task adds `seconds`, the answer, and the delta of the `/metrics` counters. `deploy.py eval` remains the checkout's retrieval score. It does not become the models table. `LLM_MODELS_MAX=1` requires finishing one model before loading the next. Thinking must be fixed the same on the three, via the template kwargs that only the host server passes today. Flash-Next and the 27B are not in `EXTRA_MODELS`: this campaign measures the Mistral already served by `evo-x5`, on the prompts from the file.

## Sources

Pages opened on 2026-11-10:

- https://huggingface.co/Qwen/Qwen3.8-27B
- https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/config.json
- https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/LICENSE
- https://huggingface.co/api/models/Qwen/Qwen3.8-27B
- https://huggingface.co/Qwen/Qwen3.5-27B
- https://huggingface.co/Qwen/Qwen3.6-27B
- https://huggingface.co/Qwen/Qwen3.8-Flash-Next
- https://huggingface.co/Qwen/Qwen3.8-Flash-Next/raw/main/config.json
- https://huggingface.co/Qwen/Qwen3.8-Flash-Next/raw/main/LICENSE
- https://huggingface.co/api/models/Qwen/Qwen3.8-Flash-Next
- https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/raw/main/README.md
- https://huggingface.co/api/models/unsloth/Qwen3.8-27B-GGUF/tree/main
- https://huggingface.co/api/models/unsloth/Qwen3.8-Flash-Next-GGUF/tree/main
- https://huggingface.co/api/models/unsloth/Qwen3.8-Flash-Next-GGUF/tree/main/UD-Q4_K_XL
- https://huggingface.co/api/models/unsloth/Qwen3.8-Flash-Next-GGUF/tree/main/UD-Q2_K_XL
- https://huggingface.co/api/models/unsloth/Qwen3.8-Flash-Next-GGUF/tree/main/UD-IQ1_S
- https://huggingface.co/mistralai/Mistral-Small-4-119B-2603
- https://huggingface.co/mistralai/Mistral-Small-4-119B-2603/raw/main/README.md
- https://huggingface.co/api/models/mistralai/Mistral-Small-4-119B-2603
- https://huggingface.co/api/models/unsloth/Mistral-Small-4-119B-2603-GGUF/tree/main/UD-Q5_K_M
- https://github.com/ggml-org/llama.cpp/blob/b10216/src/llama-arch.h
- https://github.com/ggml-org/llama.cpp/blob/b10216/tools/server/README.md
- https://github.com/ggml-org/llama.cpp/pull/27742
- https://qwen.ai/blog?id=qwen3.8 (opened, empty body)
- https://qwen.ai/blog?id=qwen3.8-flash-next (opened, empty body)

Repository files read:

- `scripts/container/fetch_models.py`
- `scripts/deploy.py` (relay to `scripts/host/deploy/`)
- `scripts/host/deploy/cli.py`
- `scripts/host/deploy/ops.py`
- `scripts/host/deploy/__init__.py`
- `scripts/host/deploy/host_llm.py`
- `scripts/host/evo_x5_serve.py`
- `scripts/host/eval_checkout.py`
- `scripts/host/profile_campaign.py`
- `scripts/container/eval_rag.py`
- `scripts/container/eval_tools.py`
- `scripts/container/eval_tools_scenarios.json`
- `containers/llm/entrypoint.sh`
- `config/profiles/16g.env`
- `config/profiles/24g.env`
- `config/profiles/24g-30b.env`
- `config/profiles/48g.env`
- `config/profiles/48g-30b.env`
- `config/profiles/48g-32b.env`
- `config/profiles/evo-x5.env`
- `CONTRACTS.md`
- `docs/notes/verification/retrieval-baseline.md`
- `docs/guides/observability.md`
- `services/agent/main.py`
- `services/agent/llm_client.py`
- `services/agent/model_catalog.py`
- `services/common/metrics.py`
- `tests/scripts/test_deploy.py`
- `docs/notes/verification/evo-x5-modele-20-utilisateurs.md`
- `docs/notes/verification/settings-comprehensibilite.md`
