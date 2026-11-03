"""CONTRACTS.md env-var table must stay aligned with .env.example and compose.yaml."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _contract_keys() -> set[str]:
    text = (REPO / "CONTRACTS.md").read_text(encoding="utf-8")
    return set(re.findall(r"^([A-Z][A-Z0-9_]+)=", text, flags=re.M))


def _example_keys() -> set[str]:
    return {
        line.split("=", 1)[0]
        for line in (REPO / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    }


def _compose_mentions() -> str:
    return (REPO / "compose.yaml").read_text(encoding="utf-8")


def test_every_contract_env_is_in_env_example() -> None:
    missing = _contract_keys() - _example_keys()
    # Host-only / documented-but-not-templated names can be added here sparingly.
    allowed_gap: set[str] = set()
    assert missing <= allowed_gap, f"in CONTRACTS.md but not .env.example: {sorted(missing - allowed_gap)}"


def test_service_env_vars_appear_in_compose() -> None:
    compose = _compose_mentions()
    required = {
        "AGENT_BEARER_TOKEN",
        "AGENT_MAX_CONCURRENT",
        "AGENT_CONTEXT_BUDGET_TOKENS",
        "AGENT_REFLECT",
        "AGENT_GATE_CLASSIFY",
        "AGENT_CONVERSATION_TTL_SECONDS",
        "TOOLS_PROJECT_ROOT",
        "QDRANT_COLLECTION",
        "MCP_PORT",
        "MCP_TRANSPORT",
        "AGENT_BASE_URL",
        "RERANKER_BASE_URL",
        "RERANKER_PORT",
    }
    missing = [key for key in required if key not in compose]
    assert missing == [], missing
