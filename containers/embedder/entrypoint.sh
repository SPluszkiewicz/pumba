#!/bin/sh
# entrypoint for the `embedder` container - resolves env vars at container start
# (Containerfile ENTRYPOINT exec form can't expand $VARS itself, so we do it here).
set -eu

: "${EMBEDDER_MODEL_PATH:=/models/nomic-embed-text-v1.5.Q8_0.gguf}"
: "${EMBEDDER_PORT:=8081}"

# EMBEDDER_NGL lets the operator override GPU offload explicitly at runtime.
# If unset, fall back to a value baked in at build time from the GPU build ARG
# (see Containerfile: EMBEDDER_GPU_BUILD=1 -> 999 layers offloaded, =0 -> CPU-only).
# NOTE: the build arg defaults to GPU=1 per CONTRACTS.md (`EMBEDDER_GPU_ARG=1`);
# compose sets EMBEDDER_NGL explicitly, and the reranker sets RERANKER_NGL=0.
if [ -z "${EMBEDDER_NGL:-}" ]; then
  if [ "${EMBEDDER_GPU_BUILD:-1}" = "1" ]; then
    EMBEDDER_NGL=999
  else
    EMBEDDER_NGL=0
  fi
fi

if [ ! -f "$EMBEDDER_MODEL_PATH" ]; then
  echo "warning: EMBEDDER_MODEL_PATH ($EMBEDDER_MODEL_PATH) does not exist yet - check the models bind mount" >&2
fi

# --embedding switches llama-server into embedding/rerank serving mode.
# --pooling mean: nomic-embed-text-v1.5. The reranker container sets
# EMBEDDER_POOLING=rank and EMBEDDER_RERANK=1 (bge-reranker, tag b10216).
: "${EMBEDDER_POOLING:=mean}"
# NOTE: --jinja (listed as a shared requirement across llm/embedder in the
# task for tool-call parsing) is deliberately omitted here: it has no purpose
# in --embedding mode, and nomic-embed-text-v1.5's GGUF typically has no chat
# template, which can make llama-server refuse to start with --jinja set.
# Re-add it only if a real build proves it's harmless/needed.
set -- llama-server \
  -m "$EMBEDDER_MODEL_PATH" \
  --host 0.0.0.0 \
  --port "$EMBEDDER_PORT" \
  --embedding \
  --pooling "$EMBEDDER_POOLING" \
  -ngl "$EMBEDDER_NGL" \
  "$@"

# The reranker service reuses this entrypoint. --reranking exposes POST /v1/rerank.
if [ "${EMBEDDER_RERANK:-0}" = "1" ]; then
  set -- "$@" --reranking
fi
# llama.cpp's default physical batch is 512, and embedding mode then shrinks
# the logical batch to match. A single chunk above that is rejected. nomic
# embed text v1.5 accepts 8192 tokens; the reranker sets EMBEDDER_CTX itself.
if [ -z "${EMBEDDER_CTX:-}" ]; then
  EMBEDDER_CTX=8192
fi
if [ -z "${EMBEDDER_UBATCH:-}" ]; then
  EMBEDDER_UBATCH=8192
fi
set -- "$@" -c "$EMBEDDER_CTX" -b "$EMBEDDER_UBATCH" -ub "$EMBEDDER_UBATCH"

# CPU tuning, same semantics as the llm entrypoint (EMBEDDER_THREADS -> -t,
# EMBEDDER_NUMA -> --numa). Empty = llama.cpp auto-detection.
if [ -n "${EMBEDDER_THREADS:-}" ]; then
  set -- "$@" -t "$EMBEDDER_THREADS"
fi
if [ -n "${EMBEDDER_NUMA:-}" ]; then
  set -- "$@" --numa "$EMBEDDER_NUMA"
fi

exec "$@"
