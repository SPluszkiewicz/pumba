"""Prometheus counters shared by `agent` and `tools`.

HTTP latency/status come from `prometheus-fastapi-instrumentator` (see
`instrument_app`). The counters here are the business labels from roadmap
3.6: tool name/outcome, malformed tool-calls, RAG used-context.
`/metrics` is not published to the host - only reachable on the internal
compose networks (core-net / tools-net).

This file is imported as `common.metrics` inside the agent container and as
`services.common.metrics` inside `tools` / host pytest. Both names must
share one CollectorRegistry entry.
"""

from __future__ import annotations

import sys
from typing import Any

from fastapi import FastAPI
from prometheus_client import Counter
from prometheus_fastapi_instrumentator import Instrumentator

_ALIASES = ("services.common.metrics", "common.metrics")


def _already_loaded() -> Any | None:
    me = sys.modules.get(__name__)
    for name in _ALIASES:
        mod = sys.modules.get(name)
        if mod is not None and mod is not me and hasattr(mod, "TOOL_CALLS_TOTAL"):
            return mod
    return None


_prior = _already_loaded()
if _prior is not None:
    TOOL_CALLS_TOTAL = _prior.TOOL_CALLS_TOTAL
    TOOL_CALL_MALFORMED_TOTAL = _prior.TOOL_CALL_MALFORMED_TOTAL
    RAG_USED_CONTEXT_TOTAL = _prior.RAG_USED_CONTEXT_TOTAL
    observe_tool = _prior.observe_tool
    observe_malformed = _prior.observe_malformed
    observe_rag = _prior.observe_rag
    instrument_app = _prior.instrument_app
else:
    TOOL_CALLS_TOTAL = Counter(
        "tool_calls_total",
        "Tool executions against the tools service",
        ["name", "ok"],
    )
    TOOL_CALL_MALFORMED_TOTAL = Counter(
        "tool_call_malformed_total",
        "LLM tool calls rejected by pydantic validation before HTTP",
    )
    RAG_USED_CONTEXT_TOTAL = Counter(
        "rag_used_context_total",
        "RAG retrievals that returned (or did not return) context",
        ["used"],
    )

    def observe_tool(name: str, ok: bool) -> None:
        TOOL_CALLS_TOTAL.labels(name=name, ok="true" if ok else "false").inc()

    def observe_malformed() -> None:
        TOOL_CALL_MALFORMED_TOTAL.inc()

    def observe_rag(used: bool) -> None:
        RAG_USED_CONTEXT_TOTAL.labels(used="true" if used else "false").inc()

    _HTTP_INSTRUMENTED = False

    def instrument_app(app: FastAPI) -> None:
        """Attach default HTTP metrics and expose GET /metrics.

        `instrument()` registers process-global collectors, so in unit tests
        (agent + tools imported in one pytest process) it must run only once.
        `expose()` only adds a route and is safe per app.
        """
        global _HTTP_INSTRUMENTED
        inst = Instrumentator()
        if not _HTTP_INSTRUMENTED:
            inst.instrument(app)
            _HTTP_INSTRUMENTED = True
        inst.expose(app, endpoint="/metrics", include_in_schema=False)
