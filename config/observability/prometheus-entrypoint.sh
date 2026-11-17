#!/bin/sh
# Prometheus image entrypoint. Substitutes the chat-model stem, then keeps
# 30 days of samples. Invoked with `sh` so the script need not be executable
# on a Windows checkout.
set -eu

path="${LLM_MODEL_PATH:-/models/Qwen2.5-Coder-14B-Instruct-Q5_K_M.gguf}"
stem=$(basename "$path" .gguf)
case "$stem" in
  *[!A-Za-z0-9._-]*)
    echo "LLM_MODEL_PATH stem must be letters, digits, dot, underscore or hyphen: $stem" >&2
    exit 1
    ;;
esac

sed "s/__LLM_METRICS_MODEL__/${stem}/g" /etc/prometheus/prometheus.yml > /tmp/prometheus.yml
exec /bin/prometheus \
  --config.file=/tmp/prometheus.yml \
  --storage.tsdb.path=/prometheus \
  --storage.tsdb.retention.time=30d
