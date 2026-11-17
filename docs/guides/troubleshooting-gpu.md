# Troubleshooting GPU

## Automatic CDI setup on `deploy up`

`python scripts/deploy.py up` checks NVIDIA CDI **before** starting the stack:

1. If `.env` has `LLM_GPU_ARG=0` and `EMBEDDER_GPU_ARG=0` (a missing value counts
   as `1`; the `evo-x5` profile sets both to `0`), it uses the CPU overlay
   (`config/compose/cpu.yaml`) and skips GPU devices.
2. Otherwise it probes `nvidia.com/gpu=all` (via `nvidia-ctk cdi list` or a short
   `podman run --device ...`).
3. If CDI is missing, it tries `sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml`.
4. If that still fails, it falls back to CPU automatically: it adds
   `-f config/compose/cpu.yaml` so Podman does not request CDI devices, and
   sets `LLM_GPU_ARG`, `EMBEDDER_GPU_ARG`, `LLM_NGL`, and `EMBEDDER_NGL` to `0`
   for that run only. `.env` is not rewritten, so the next `up` probes again.
   The same fallback applies if `compose up` itself fails while CDI is still
   unavailable.

When `CONTAINER_RUNTIME` resolves to `docker`, the CDI probe is skipped.
`deploy.py` stacks `config/compose/docker.yaml` (`gpus: all`, CDI devices replaced)
and probes with `docker run --gpus all`. The CPU fallback stacks
`config/compose/cpu.yaml` and `config/compose/docker.cpu.yaml`, so Docker is not asked for
a GPU.

On **Fedora** (native Podman, not WSL), install the toolkit once:

```bash
sudo dnf install -y nvidia-container-toolkit
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
nvidia-ctk cdi list   # should list nvidia.com/gpu=all
podman run --rm --device nvidia.com/gpu=all ubuntu:24.04 nvidia-smi
```

Then re-run `python scripts/deploy.py up` (or let step 3 above generate CDI for you).

## Symptom: request times out with no answer (often on CPU / no GPU)

On a machine without GPU, **Qwen2.5-Coder-14B on CPU** can take a long time
per call, and a large checkout makes clangd and cmake slower still. The
defaults are already a backstop, not a latency target:

- `AGENT_REQUEST_TIMEOUT_SECONDS=14400` (one call to llm, embedder, or tools)
- `TOOLS_BUILD_TIMEOUT_SECONDS=14400` (every tools subprocess)
- `APP_REQUEST_TIMEOUT_SECONDS=172800` and `MCP_REQUEST_TIMEOUT_SECONDS=172800`
  (the whole question)

CPU fallback does not shorten these. Stop a question from the UI. Recreate
`agent`, `tools`, and `mcp` after changing the values in `.env`. One more
backstop is not in `.env`: `serena` aborts a single tool call after
`tool_timeout: 240` in `config/serena/serena_config.yml` (seconds, Serena's
own setting).

Check whether you are on CPU:

```bash
podman compose exec llm env | grep LLM_GPU_BUILD
podman compose exec llm env | grep LLM_NGL
```

If `LLM_GPU_BUILD=0` or `LLM_NGL=0`, expect slow responses, or fix CDI/GPU
(sections above) for normal speed.

## First step: run `doctor`

Before digging into GPU-specific steps below, run:

```bash
python scripts/deploy.py doctor --gpu
```

`--gpu` adds the same `nvidia-smi`-in-a-container probe used in the next
section to the usual checks (Podman/Compose, `.env`, model files, disk
space, per-service health). It may point straight at the problem (or rule
out everything except the GPU, telling you to start here).

## Symptom: `llm` never becomes healthy, logs mention CUDA/device errors

Most commonly, the Podman machine VM (on Windows/macOS) doesn't have GPU
passthrough configured yet. This is a one-time, host-level, Podman-only
operation. There's no cross-platform Python script for it, because it's
inherently specific to the Podman machine's VM backend. Run these commands
once, from the host:

```bash
podman machine ssh
```

Inside the Podman machine VM:

```bash
sudo rpm-ostree install nvidia-container-toolkit   # or the equivalent for your machine's OS
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
nvidia-ctk cdi list   # should show nvidia.com/gpu=all
exit
```

Then restart the Podman machine:

```bash
podman machine stop
podman machine start
```

Verify the device is visible from a plain container:

```bash
podman run --rm --device nvidia.com/gpu=all ubuntu:24.04 nvidia-smi
```

If that command fails, the problem is at the Podman-machine/driver level,
before this project's containers are even involved, consult
[Podman's own GPU documentation](https://podman.io/docs) for your specific
host OS and GPU driver version.

## Symptom: `llm` builds and starts, but `-ngl` doesn't seem to offload anything

Check the actual `-ngl` value the entrypoint chose. It's baked from the
image's `GPU` build arg unless overridden:

```bash
podman compose exec llm env | grep -E "LLM_GPU_BUILD|LLM_NGL"
```

If `LLM_GPU_BUILD=0`, the image was built with `--build-arg GPU=0` (CPU-only
build), rebuild with `GPU=1` (the default) if you have a working GPU
passthrough:

```bash
podman compose build llm
podman compose up -d --force-recreate llm
```

## Symptom: `embedder` seems slow during indexing / RAG embed calls

Check GPU offload the same way:

```bash
podman compose exec embedder env | grep -E "EMBEDDER_GPU_BUILD|EMBEDDER_NGL"
```

If `EMBEDDER_GPU_BUILD=0` but `.env` has `EMBEDDER_GPU_ARG=1`, rebuild:

```bash
podman compose build embedder
podman compose up -d --force-recreate embedder
```

Or run `python scripts/deploy.py doctor`. It warns when build args and
running containers disagree.

## RTX 6000: pick a profile from `nvidia-smi`, not from Task Manager

Task Manager can show about 56 GB by adding the card's memory to shared system RAM. Shared RAM is the Xeon. Layers and KV that land there are the slow path.

Read `nvidia-smi` and use the `memory.total` line:

`16g`, `24g`, and `48g` keep `Qwen2.5-Coder-14B-Instruct-Q5_K_M`. The other three NVIDIA cases swap the chat model so the same question can be compared. Those six stay at context 32768. `deploy.py up` does not download the larger files. `evo-x5` is a different machine: context 262144, and `up` serves the model on the host.

| Case | Card (`memory.total`) | What changes | Command |
| --- | --- | --- | --- |
| `16g` | ~16376 MiB, this 4080 | 14B, embedder and reranker on CPU, `q8_0` KV | `python scripts/deploy.py gpu-profile 16g` |
| `16g-gptoss` | ~16376 MiB, this 4080 | gpt-oss-20b MXFP4 (~11.3 GiB), same settings as `16g`. Compare with `16g`. | `fetch-extra 16g-gptoss`, then `gpu-profile 16g-gptoss` |
| `24g` | ~24576 MiB | 14B, both side models on GPU, `q8_0` KV | `python scripts/deploy.py gpu-profile 24g` |
| `24g-30b` | ~24576 MiB | Qwen3-Coder-30B-A3B Q5 (~20 GiB). Compare with `24g`. | `fetch-extra 24g-30b`, then `gpu-profile 24g-30b` |
| `48g` | ~46068 MiB with ECC, or 49152 | 14B, `f16` KV. Compare with `24g` for the cache type. | `python scripts/deploy.py gpu-profile 48g` |
| `48g-30b` | same 48 GB card | same 30B-A3B at Q8. Compare with `48g` and with `24g-30b`. | `fetch-extra 48g-30b`, then `gpu-profile 48g-30b` |
| `48g-32b` | same 48 GB card | Qwen2.5-Coder-32B Q5, `f16` KV. Compare with `48g-30b`. | `fetch-extra 48g-32b`, then `gpu-profile 48g-32b` |
| `evo-x5` | GMKtec EVO-X5 Pro, 192 GB unified | Mistral Small 4 Q5, host `llama-server`, context 262144 | `gpu-profile evo-x5`, then `up` (fetches the GGUF if missing) |

The questions that separate these profiles are in [RTX 6000 scenarios](../notes/verification/rtx6000-scenarios.md). Run them on that machine. Until `nvidia-smi` is known, stay on `16g`. A profile rewrites only the keys it lists in `.env` and leaves the bearer token alone. That matters when you leave `evo-x5`: it sets `LLM_GPU_ARG=0`, `LLM_THREADS=16`, `LLM_THREADS_BATCH=16`, and `AGENT_CONTEXT_BUDGET_TOKENS=262144`, which the other profiles do not touch. Reset them by hand (see [EVO-X5](evo-x5.md#switching-back-to-an-nvidia-profile)), or `llm` is built CPU-only. If `EMBEDDER_GPU_ARG` changes, rebuild the embedder, then recreate `llm`, `embedder`, and `reranker`. The Qwen3-Coder profiles need one tool-call request after the first load: the chat template is not the Qwen2.5 one. If `llm` never becomes healthy on `24g-30b`, that Q5 does not fit the 24 GB card; switch back to `24g`.

## Symptom: it works, but VRAM is tight / OOMs under load

See [Swapping the model](swapping-the-model.md#vram-considerations). The
values in `.env.example` and the `16g` profile keep `embedder` and
`reranker` on the CPU (`EMBEDDER_GPU_ARG=0`, `RERANKER_NGL=0`). The `24g`
and `48g` profiles put **both** `llm` and `embedder` on the GPU
(`EMBEDDER_GPU_ARG=1`) and the reranker too (`RERANKER_NGL=999`), which is too
much for a 16 GB card. If you hit OOM after setting `EMBEDDER_GPU_ARG=1` by
hand, set it back to `0` and rebuild `embedder` and `reranker`.

## Fallback: just run on CPU

Everything in this project works without a GPU at all. It's just slower.

`deploy up` can switch to CPU automatically when CDI is missing (see above). To force
CPU from the start, set in `.env`:

```bash
LLM_GPU_ARG=0
EMBEDDER_GPU_ARG=0
LLM_NGL=0
EMBEDDER_NGL=0
```

Then run `python scripts/deploy.py up`. It applies `config/compose/cpu.yaml` without
needing to edit `compose.yaml` manually.

Manual equivalent:

```bash
podman compose -f compose.yaml -f config/compose/cpu.yaml build llm embedder reranker
podman compose -f compose.yaml -f config/compose/cpu.yaml up -d llm embedder reranker
```

### Tuning CPU threads (and NUMA on multi-socket hosts)

By default `llama-server` picks its own thread count, which on a machine
with hyper-threading or two CPU sockets is usually *not* the fastest
setting. Three optional variables in `.env` (empty in `.env.example`) are
passed straight through by `containers/llm/entrypoint.sh` (and
`EMBEDDER_THREADS` / `EMBEDDER_NUMA` by the embedder's; `RERANKER_THREADS` /
`RERANKER_NUMA` by the reranker's):

| Variable | llama-server flag | Recommendation |
|---|---|---|
| `LLM_THREADS` | `-t` | number of **physical** cores you want to use (not logical threads) |
| `LLM_THREADS_BATCH` | `-tb` | leave empty (defaults to `-t`); raise only if prompt processing is the bottleneck |
| `LLM_NUMA` | `--numa` | `distribute` on a dual-socket box; empty on single-socket |

Generation speed is memory-bandwidth-bound, so more threads than physical
cores rarely helps and often hurts. On a dual-socket host, benchmark the two
obvious configurations and keep the faster one:

```bash
# A: both sockets, physical cores only
LLM_THREADS=12  LLM_NUMA=distribute
# B: one socket, no cross-socket memory traffic
LLM_THREADS=6   LLM_NUMA=isolate
```

Measure with a fixed prompt and compare the `predicted_per_second` /
`prompt_per_second` figures that `llama-server` prints in its logs
(`python scripts/deploy.py logs llm`). Recreate `llm` after each change:
`podman compose up -d --force-recreate llm`.
