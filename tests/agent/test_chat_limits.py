"""Concurrency cap, /metrics, and optional reflection field on /chat."""

from __future__ import annotations

import main
import pytest
from fastapi.testclient import TestClient


def test_chat_returns_503_when_slots_are_full(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BEARER_TOKEN", "secret-token")
    monkeypatch.setattr(main, "_CHAT_SLOTS", main._SlotGate(0))

    with TestClient(main.app) as client:
        resp = client.post(
            "/chat",
            json={"query": "hello"},
            headers={"Authorization": "Bearer secret-token"},
        )
    assert resp.status_code == 503
    assert "concurrent" in resp.json()["detail"]


def test_metrics_does_not_require_auth() -> None:
    with TestClient(main.app) as client:
        resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "http_request" in resp.text or "http_requests" in resp.text
