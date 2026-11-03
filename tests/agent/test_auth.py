"""Tests for the `Authorization: Bearer <AGENT_BEARER_TOKEN>` dependency,
both in isolation and through the `/chat` route.
"""

from __future__ import annotations

import main
import pytest
from fastapi import HTTPException


def test_verify_bearer_token_raises_401_when_header_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BEARER_TOKEN", "secret-token")
    with pytest.raises(HTTPException) as exc_info:
        main.verify_bearer_token(authorization=None)
    assert exc_info.value.status_code == 401


def test_verify_bearer_token_raises_401_when_header_wrong(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BEARER_TOKEN", "secret-token")
    with pytest.raises(HTTPException) as exc_info:
        main.verify_bearer_token(authorization="Bearer wrong-token")
    assert exc_info.value.status_code == 401


def test_verify_bearer_token_raises_401_when_scheme_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BEARER_TOKEN", "secret-token")
    with pytest.raises(HTTPException) as exc_info:
        main.verify_bearer_token(authorization="secret-token")
    assert exc_info.value.status_code == 401


def test_verify_bearer_token_passes_with_correct_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BEARER_TOKEN", "secret-token")
    main.verify_bearer_token(authorization="Bearer secret-token")  # should not raise


def test_verify_bearer_token_rejects_everything_when_token_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BEARER_TOKEN", "")
    with pytest.raises(HTTPException) as exc_info:
        main.verify_bearer_token(authorization="Bearer ")
    assert exc_info.value.status_code == 401


def test_chat_endpoint_returns_401_without_header(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("AGENT_BEARER_TOKEN", "secret-token")
    with TestClient(main.app) as client:
        resp = client.post("/chat", json={"query": "hello"})
    assert resp.status_code == 401


def test_chat_endpoint_returns_401_with_wrong_header(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("AGENT_BEARER_TOKEN", "secret-token")
    with TestClient(main.app) as client:
        resp = client.post(
            "/chat",
            json={"query": "hello"},
            headers={"Authorization": "Bearer nope"},
        )
    assert resp.status_code == 401


def test_health_endpoint_does_not_require_auth() -> None:
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
