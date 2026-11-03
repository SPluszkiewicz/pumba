"""Chat-model filtering and the in-memory active name used on completions."""

from __future__ import annotations

import llm_client
import model_catalog


def test_chat_filter_drops_embedder_and_reranker() -> None:
    payload = {
        "data": [
            {"id": "Qwen2.5-Coder-14B-Instruct-Q5_K_M", "status": {"value": "loaded"}},
            {"id": "Qwen2.5-Coder-14B-Instruct-Q5_K_M.gguf", "status": {"value": "unloaded"}},
            {"id": "Qwen2.5-0.5B-Instruct-Q4_K_M", "status": {"value": "unloaded"}},
            {"id": "nomic-embed-text-v1.5.Q8_0", "status": {"value": "unloaded"}},
            {"id": "bge-reranker-v2-m3-Q8_0", "status": {"value": "unloaded"}},
            {"id": "README.md", "status": {"value": "unloaded"}},
        ]
    }
    parsed = model_catalog.parse_models(payload)
    assert [item["id"] for item in parsed] == [
        "Qwen2.5-Coder-14B-Instruct-Q5_K_M",
        "Qwen2.5-0.5B-Instruct-Q4_K_M",
    ]
    assert parsed[0]["status"] == "loaded"


def test_sync_active_follows_the_loaded_model() -> None:
    llm_client.set_active_model("other.gguf")
    model_catalog.sync_active(
        [
            {"id": "small.gguf", "status": "unloaded", "failed": False},
            {"id": "Qwen2.5-Coder-14B-Instruct-Q5_K_M.gguf", "status": "loaded", "failed": False},
        ]
    )
    assert llm_client.active_model() == "Qwen2.5-Coder-14B-Instruct-Q5_K_M.gguf"
