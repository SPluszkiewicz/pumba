"""GET /ui is the loopback chat page. It must not embed the bearer token."""

from __future__ import annotations

import main
import pytest
from common.product import NAME as PRODUCT_NAME
from common.product import SLUG as PRODUCT_SLUG
from fastapi.testclient import TestClient


def test_ui_is_served_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BEARER_TOKEN", "secret-token")
    with TestClient(main.app) as client:
        resp = client.get("/ui")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert 'connect-src \'self\'' in resp.headers["content-security-policy"]
    assert "/chat/stream" in resp.text
    assert "sessionStorage" in resp.text
    assert "Déroulement" in resp.text
    assert "Analyse de la question" in resp.text
    assert "renderMarkdown" in resp.text
    assert "Copier" in resp.text
    assert "navigator.clipboard" in resp.text
    assert 'id="instructions"' in resp.text
    assert "payload.instructions" in resp.text
    assert "secret-token" not in resp.text
    assert PRODUCT_NAME in resp.text
    assert f"{PRODUCT_SLUG}_token" in resp.text
    assert "{{PRODUCT_NAME}}" not in resp.text
    assert "{{PRODUCT_SLUG}}" not in resp.text


def test_chat_stream_still_requires_the_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BEARER_TOKEN", "secret-token")
    with TestClient(main.app) as client:
        resp = client.post("/chat/stream", json={"query": "hello"})
    assert resp.status_code == 401
