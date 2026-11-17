"""Prometheus scrape config and the Grafana analysis dashboard stay aligned."""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OBS = REPO / "config" / "observability"


def test_prometheus_scrapes_the_runtime_and_substitutes_the_model_stem() -> None:
    text = (OBS / "prometheus.yml").read_text(encoding="utf-8")
    for job in ("agent", "tools", "llm", "embedder", "reranker", "qdrant"):
        assert f"job_name: {job}" in text
    assert "__LLM_METRICS_MODEL__" in text
    entry = (OBS / "prometheus-entrypoint.sh").read_text(encoding="utf-8")
    assert "LLM_MODEL_PATH" in entry
    assert "--storage.tsdb.retention.time=30d" in entry
    overlay = (REPO / "config" / "compose" / "observability.yaml").read_text(encoding="utf-8")
    assert "prometheus-entrypoint.sh" in overlay
    assert "LLM_MODEL_PATH=" in overlay


def test_analysis_dashboard_queries_the_business_series() -> None:
    path = OBS / "grafana" / "dashboards" / "pumba-analyse.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["uid"] == "pumba-analyse"
    encoded = path.read_text(encoding="utf-8")
    for expr in (
        "agent_turns_total",
        "agent_turn_duration_seconds_bucket",
        "agent_context_tokens_bucket",
        "agent_context_window_ratio_bucket",
        "rag_retrieval_duration_seconds_bucket",
        "rag_reranker_total",
        "tool_call_duration_seconds_bucket",
        "agent_chat_slots_in_use",
        "llamacpp:tokens_predicted_total",
        "collections_vector_total",
        'job=\\"embedder\\"',
        'job=\\"qdrant\\"',
    ):
        assert expr in encoded
    home = json.loads((OBS / "grafana" / "dashboards" / "pumba.json").read_text(encoding="utf-8"))
    assert any(link.get("url") == "/d/pumba-analyse" for link in home["links"])


def test_embedder_entrypoint_enables_metrics() -> None:
    text = (REPO / "containers" / "embedder" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "--metrics" in text
