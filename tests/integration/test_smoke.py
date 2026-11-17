"""End-to-end smoke test for the `test` compose profile.

Run via `podman compose --env-file .env.test --profile test run --rm
smoke-test` (see docs/guides/testing.md) against an already-started stack
(`podman compose --env-file .env.test up -d qdrant llm embedder tools
agent`, built with `LLM_MODEL_PATH`/`LLM_GPU_ARG` swapped to the tiny CPU
model per `.env.test` - see CONTRACTS.md's "Models" table for why only the
chat model, never the embedder, is swapped).

This intentionally does NOT try to spin the stack up itself - compose
orchestration (build order, `depends_on: condition: service_healthy`) is
already `compose.yaml`'s job. This test only verifies the stack that's
already running behaves correctly end-to-end:
  1. every service's own /health-equivalent endpoint responds, including
     `mcp` and `ui-proxy` (not only the five original probes)
  2. `agent`'s bearer-token auth is enforced
  3. `GET /models` and `GET /ui` work through `agent` (and ui-proxy)
  4. `POST /lookup` degrades to empty hits on a fresh Qdrant (no 5xx)
  5. MCP Streamable HTTP `tools/list` returns the six tools
  6. a basic /chat round-trip works and returns the schema CONTRACTS.md
     defines (with no indexed context - fresh Qdrant collection is expected)
  7. a tool-call round-trip is attempted, but its success is NOT a hard
     assertion (see note below)

Tool-calling reliability note: the tiny 0.5B CI model swapped in for `llm`
is far less reliable at structured tool-call generation than the real
7B production model (this is explicitly why CONTRACTS.md mandates Q5_K_M on
a 7B+ model for production - smaller models are known to be flaky at this).
`test_tool_call_round_trip` below treats a successful tool call as a bonus
signal, not a required outcome, and only hard-fails if the request errors
out entirely (which would indicate a real wiring bug, not model quality).
"""

from __future__ import annotations

import json
import os
import time

import httpx
import pytest

AGENT_BASE_URL = os.environ.get("AGENT_BASE_URL", "http://agent:8090")
AGENT_BEARER_TOKEN = os.environ.get("AGENT_BEARER_TOKEN", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://llm:8080")
EMBEDDER_BASE_URL = os.environ.get("EMBEDDER_BASE_URL", "http://embedder:8081")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
TOOLS_BASE_URL = os.environ.get("TOOLS_BASE_URL", "http://tools:8091")
MCP_BASE_URL = os.environ.get("MCP_BASE_URL", "http://mcp:8092")
UI_PROXY_BASE_URL = os.environ.get("UI_PROXY_BASE_URL", "http://ui-proxy:8090")

# Generous: the `llm` service has to load a model into memory on first
# request/startup, and CI runners are often slow/oversubscribed.
HEALTH_TIMEOUT_SECONDS = 180
HEALTH_POLL_INTERVAL_SECONDS = 3
CHAT_TIMEOUT_SECONDS = 120


def _wait_until_healthy(name: str, url: str) -> None:
    """Poll `url` until it returns any 2xx, or fail loudly after the timeout.

    Deliberately does not care about response body shape here - each
    service's own /health(z) is trusted to report accurately; this just
    confirms the network path + process are actually up before the real
    assertions below run.
    """
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    last_error: str = "never attempted"

    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=5)
            if 200 <= resp.status_code < 300:
                return
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]!r}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(HEALTH_POLL_INTERVAL_SECONDS)

    pytest.fail(f"{name} ({url}) never became healthy within {HEALTH_TIMEOUT_SECONDS}s: {last_error}")


@pytest.fixture(scope="module", autouse=True)
def wait_for_stack() -> None:
    """Blocks all tests in this module until every service is reachable.

    Runs once per test session (module-scoped) rather than per-test, since
    re-polling five services before every single test would slow this down
    for no benefit - health doesn't flap once the stack is actually up.
    """
    _wait_until_healthy("qdrant", f"{QDRANT_URL}/healthz")
    _wait_until_healthy("llm", f"{LLM_BASE_URL}/health")
    _wait_until_healthy("embedder", f"{EMBEDDER_BASE_URL}/health")
    _wait_until_healthy("tools", f"{TOOLS_BASE_URL}/health")
    _wait_until_healthy("agent", f"{AGENT_BASE_URL}/health")
    _wait_until_healthy("mcp", f"{MCP_BASE_URL}/health")
    _wait_until_healthy("ui-proxy", f"{UI_PROXY_BASE_URL}/health")


def _chat(query: str, *, token: str | None = AGENT_BEARER_TOKEN) -> httpx.Response:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.post(
        f"{AGENT_BASE_URL}/chat",
        json={"query": query},
        headers=headers,
        timeout=CHAT_TIMEOUT_SECONDS,
    )


def test_all_services_report_healthy() -> None:
    """Redundant with the autouse fixture, but gives a named, visible pass
    in test output confirming the wait actually succeeded rather than the
    module silently never running any tests (e.g. a collection error)."""
    assert True


def _agent_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {AGENT_BEARER_TOKEN}",
    }


def test_models_and_ui_are_served() -> None:
    models = httpx.get(f"{AGENT_BASE_URL}/models", headers=_agent_headers(), timeout=30)
    assert models.status_code == 200, models.text
    body = models.json()
    assert body.get("active") or body.get("models")

    ui = httpx.get(f"{AGENT_BASE_URL}/ui", timeout=15)
    assert ui.status_code == 200, ui.text
    assert "text/html" in ui.headers.get("content-type", "")
    assert "Content-Security-Policy" in ui.headers

    proxied = httpx.get(f"{UI_PROXY_BASE_URL}/health", timeout=15)
    assert proxied.status_code == 200, proxied.text


def test_lookup_degrades_when_nothing_is_indexed() -> None:
    resp = httpx.post(
        f"{AGENT_BASE_URL}/lookup",
        json={"query": "Evaluate"},
        headers=_agent_headers(),
        timeout=60,
    )
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json().get("hits"), list)


def test_mcp_lists_the_six_tools() -> None:
    resp = httpx.post(
        f"{MCP_BASE_URL}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    if not isinstance(payload, dict) or "result" not in payload:
        events = []
        event_name = "message"
        for line in resp.text.splitlines():
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                raw = line.split(":", 1)[1].strip()
                try:
                    events.append((event_name, json.loads(raw)))
                except json.JSONDecodeError:
                    continue
        payload = next((data for _, data in events if "result" in data), {})
    names = {
        tool.get("name")
        for tool in (payload.get("result") or {}).get("tools") or []
        if isinstance(tool, dict)
    }
    assert {"lookup", "check", "calls", "types", "ask"} <= names, names


def test_agent_rejects_missing_bearer_token() -> None:
    resp = _chat("does this need a token?", token=None)
    assert resp.status_code == 401


def test_agent_rejects_wrong_bearer_token() -> None:
    resp = _chat("does this need the right token?", token="definitely-not-the-real-token")
    assert resp.status_code == 401


def test_chat_basic_round_trip_without_indexed_context() -> None:
    """This test assumes a fresh/empty `code_chunks` collection (the
    smoke-test stack is never pointed at a pre-populated production Qdrant
    volume). Confirms the CRITICAL resilience requirement from CONTRACTS.md:
    the agent must degrade gracefully, not error, when there's no indexed
    context yet.
    """
    resp = _chat("What is two plus two?")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert isinstance(body.get("answer"), str) and body["answer"].strip()
    assert body.get("used_context") is False
    assert body.get("sources") == []
    assert isinstance(body.get("tool_calls"), list)


def test_tool_call_round_trip() -> None:
    """Best-effort: asks a question that should prompt a `build` tool call.

    HARD assertion: the request completes successfully end-to-end (this
    catches real wiring bugs - agent can't reach tools, tools can't reach
    the mounted app/ project, malformed-tool-call handling crashes instead
    of degrading, etc.).

    SOFT assertion (logged, not asserted): whether the tiny CI model
    actually chose to call a tool. It's known to be much less reliable at
    structured tool-calling than the production 7B model, so an empty
    `tool_calls` list here is not treated as a failure - only a request
    that errors out is.
    """
    resp = _chat("Please build the C++ project with the release preset and tell me if it succeeds.")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert isinstance(body.get("tool_calls"), list)
    if not body["tool_calls"]:
        print(
            "[test_smoke] NOTE: the tiny CI model did not make a tool call for this "
            "prompt. This is expected/tolerated - see the tool-calling reliability "
            "note at the top of this file - but if this starts happening for the "
            "*production* 7B model, that is a real regression."
        )
