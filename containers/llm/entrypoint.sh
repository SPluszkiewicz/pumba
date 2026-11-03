#!/bin/sh
# entrypoint for the `llm` container - resolves env vars at container start
# (Containerfile ENTRYPOINT exec form can't expand $VARS itself, so we do it here).
set -eu

: "${LLM_MODEL_PATH:=/models/Qwen2.5-Coder-14B-Instruct-Q5_K_M.gguf}"
: "${LLM_PORT:=8080}"

# LLM_NGL lets the operator override GPU offload explicitly at runtime.
# If unset, fall back to a value baked in at build time from the GPU build ARG
# (see Containerfile: LLM_GPU_BUILD=1 -> 999 layers offloaded, =0 -> 0/CPU-only).
if [ -z "${LLM_NGL:-}" ]; then
  if [ "${LLM_GPU_BUILD:-1}" = "1" ]; then
    LLM_NGL=999
  else
    LLM_NGL=0
  fi
fi

if [ ! -f "$LLM_MODEL_PATH" ]; then
  echo "warning: LLM_MODEL_PATH ($LLM_MODEL_PATH) does not exist yet - check the models bind mount" >&2
fi

# LLM_NGL: a layer count, "auto", or "all". "auto" keeps as many layers as
# fit in VRAM; the rest run on CPU. --fit uses LLM_GPU_MARGIN_MIB as the
# VRAM that must stay free. --cache-ram caps the KV cache in system RAM.
# LLM_ROUTER=1 starts without -m so /models/load can swap GGUFs. Children
# inherit the context and cache flags. Only one model stays loaded.
NGL="${LLM_NGL:-auto}"
# Router ids are the file stem (no .gguf). A section named with the suffix
# shows up as a second model and never matches the file the router discovered.
MODEL_NAME="$(basename "$LLM_MODEL_PATH" .gguf)"
set -- llama-server \
  --host 0.0.0.0 \
  --port "$LLM_PORT" \
  --jinja \
  --metrics \
  -ngl "$NGL" \
  -c "${LLM_CTX_SIZE:-32768}" \
  --parallel "${LLM_PARALLEL:-1}" \
  --cache-type-k "${LLM_CACHE_TYPE_K:-q8_0}" \
  --cache-type-v "${LLM_CACHE_TYPE_V:-q8_0}" \
  --fit on \
  --fit-target "${LLM_GPU_MARGIN_MIB:-2048}" \
  --cache-ram "${LLM_CACHE_RAM_MIB:-4096}"

if [ "${LLM_ROUTER:-1}" = "1" ]; then
  PRESET="${LLM_MODELS_PRESET:-/tmp/llm-models.ini}"
  cat > "$PRESET" <<EOF
version = 1

[$MODEL_NAME]
load-on-startup = true
EOF
  set -- "$@" \
    --models-dir "${LLM_MODELS_DIR:-/models}" \
    --models-max "${LLM_MODELS_MAX:-1}" \
    --models-preset "$PRESET"
else
  set -- "$@" -m "$LLM_MODEL_PATH"
fi

# CPU tuning (matters on CPU-only / partial-offload hosts, harmless on GPU):
#   LLM_THREADS  -> -t N   (generation threads; use the number of *physical*
#                          cores, hyper-threads are usually slower for llama.cpp)
#   LLM_THREADS_BATCH -> -tb N (prompt-processing threads; defaults to -t)
#   LLM_NUMA     -> --numa {distribute|isolate|numactl} on multi-socket boxes
# All empty by default = llama.cpp's own auto-detection.
if [ -n "${LLM_THREADS:-}" ]; then
  set -- "$@" -t "$LLM_THREADS"
fi
if [ -n "${LLM_THREADS_BATCH:-}" ]; then
  set -- "$@" -tb "$LLM_THREADS_BATCH"
fi
if [ -n "${LLM_NUMA:-}" ]; then
  set -- "$@" --numa "$LLM_NUMA"
fi

if [ -n "${LLM_LORA_PATH:-}" ] && [ -f "$LLM_LORA_PATH" ]; then
  set -- "$@" --lora "$LLM_LORA_PATH"
fi

exec "$@"
