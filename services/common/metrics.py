"""Prometheus counters shared by `agent` and `tools`.

HTTP latency/status come from `prometheus-fastapi-instrumentator` (see
`instrument_app`). The series here are the business signals: tool name and
outcome, malformed tool calls, RAG, turn duration, context budget, and the
chat concurrency cap. `/metrics` is not published to the host. It is
reachable on the internal compose networks (core-net / tools-net).

This file is imported as `common.metrics` inside the agent container and as
`services.common.metrics` inside `tools` / host pytest. Both names must
share one CollectorRegistry entry.
"""

from __future__ import annotations

import sys
from typing import Any

from fastapi import FastAPI
from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

_ALIASES = ("services.common.metrics", "common.metrics")
_EXPORTS = (
    "TOOL_CALLS_TOTAL",
    "TOOL_CALL_MALFORMED_TOTAL",
    "TOOL_CALL_DURATION",
    "RAG_USED_CONTEXT_TOTAL",
    "RAG_RETRIEVAL_DURATION",
    "RAG_SOURCES",
    "RAG_RERANKER_TOTAL",
    "AGENT_TURNS_TOTAL",
    "AGENT_TURN_DURATION",
    "AGENT_TURN_ITERATIONS",
    "AGENT_TURN_TOOL_CALLS",
    "AGENT_CONTEXT_TOKENS",
    "AGENT_CONTEXT_WINDOW_RATIO",
    "AGENT_CONTEXT_COMPRESSED_TOTAL",
    "AGENT_CONTEXT_OVERFLOW_TOTAL",
    "AGENT_REFLECTION_SENTENCES_TOTAL",
    "AGENT_CHAT_SLOTS_IN_USE",
    "AGENT_CHAT_SLOTS_LIMIT",
    "AGENT_CHAT_REJECTED_TOTAL",
    "observe_tool",
    "observe_tool_duration",
    "observe_malformed",
    "observe_rag",
    "observe_rag_retrieval",
    "observe_turn",
    "observe_context",
    "observe_context_overflow",
    "observe_reflection",
    "observe_chat_slots",
    "set_chat_slot_limit",
    "observe_chat_rejected",
    "instrument_app",
)


def _already_loaded() -> Any | None:
    me = sys.modules.get(__name__)
    for name in _ALIASES:
        mod = sys.modules.get(name)
        if mod is not None and mod is not me and hasattr(mod, "TOOL_CALLS_TOTAL"):
            return mod
    return None


_prior = _already_loaded()
if _prior is not None:
    for _name in _EXPORTS:
        globals()[_name] = getattr(_prior, _name)
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
    TOOL_CALL_DURATION = Histogram(
        "tool_call_duration_seconds",
        "Wall time of one tool execution, including a call blocked by edit policy",
        ["name"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1800),
    )
    RAG_USED_CONTEXT_TOTAL = Counter(
        "rag_used_context_total",
        "RAG retrievals that returned (or did not return) context",
        ["used"],
    )
    RAG_RETRIEVAL_DURATION = Histogram(
        "rag_retrieval_duration_seconds",
        "Wall time of embed, search, and optional rerank, before the working-copy overlay",
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
    )
    RAG_SOURCES = Histogram(
        "rag_sources",
        "Cited chunks handed to the model after overlay and noise filtering",
        buckets=(0, 1, 2, 3, 5, 8, 12),
    )
    RAG_RERANKER_TOTAL = Counter(
        "rag_reranker_total",
        "Retrievals that did or did not receive a reranker order",
        ["used"],
    )
    AGENT_TURNS_TOTAL = Counter(
        "agent_turns_total",
        "Chat turns finished by the agent",
        ["route", "outcome"],
    )
    AGENT_TURN_DURATION = Histogram(
        "agent_turn_duration_seconds",
        "Wall time of one chat turn, from route decision through reflection",
        ["route", "outcome"],
        buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1800, 3600),
    )
    AGENT_TURN_ITERATIONS = Histogram(
        "agent_turn_iterations",
        "LLM rounds used by one tool loop, not counting the plain-text fallback",
        buckets=(1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48),
    )
    AGENT_TURN_TOOL_CALLS = Histogram(
        "agent_turn_tool_calls",
        "Tool executions in one chat turn",
        buckets=(0, 1, 2, 3, 5, 8, 12, 20, 32),
    )
    AGENT_CONTEXT_TOKENS = Histogram(
        "agent_context_tokens",
        "Measured prompt tokens after one budget pass",
        buckets=(256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144),
    )
    AGENT_CONTEXT_WINDOW_RATIO = Histogram(
        "agent_context_window_ratio",
        "Share of the context window occupied after one budget pass",
        buckets=(0.1, 0.25, 0.5, 0.7, 0.85, 0.95, 1),
    )
    AGENT_CONTEXT_COMPRESSED_TOTAL = Counter(
        "agent_context_compressed_total",
        "Budget passes that shortened the prompt",
    )
    AGENT_CONTEXT_OVERFLOW_TOTAL = Counter(
        "agent_context_overflow_total",
        "LLM calls rejected because the prompt exceeded the context window",
    )
    AGENT_REFLECTION_SENTENCES_TOTAL = Counter(
        "agent_reflection_sentences_removed_total",
        "Sentences dropped by the optional reflection pass",
    )
    AGENT_CHAT_SLOTS_IN_USE = Gauge(
        "agent_chat_slots_in_use",
        "Chat turns currently holding a concurrency slot",
    )
    AGENT_CHAT_SLOTS_LIMIT = Gauge(
        "agent_chat_slots_limit",
        "AGENT_MAX_CONCURRENT",
    )
    AGENT_CHAT_REJECTED_TOTAL = Counter(
        "agent_chat_rejected_total",
        "Chat requests rejected because the concurrency cap was full",
    )

    def observe_tool(name: str, ok: bool) -> None:
        TOOL_CALLS_TOTAL.labels(name=name, ok="true" if ok else "false").inc()

    def observe_tool_duration(name: str, seconds: float) -> None:
        TOOL_CALL_DURATION.labels(name=name).observe(max(0.0, seconds))

    def observe_malformed() -> None:
        TOOL_CALL_MALFORMED_TOTAL.inc()

    def observe_rag(used: bool) -> None:
        RAG_USED_CONTEXT_TOTAL.labels(used="true" if used else "false").inc()

    def observe_rag_retrieval(seconds: float, sources: int, reranker_used: bool) -> None:
        RAG_RETRIEVAL_DURATION.observe(max(0.0, seconds))
        RAG_SOURCES.observe(max(0, sources))
        RAG_RERANKER_TOTAL.labels(used="true" if reranker_used else "false").inc()

    def observe_turn(route: str, outcome: str, seconds: float, iterations: int, tool_calls: int) -> None:
        AGENT_TURNS_TOTAL.labels(route=route, outcome=outcome).inc()
        AGENT_TURN_DURATION.labels(route=route, outcome=outcome).observe(max(0.0, seconds))
        AGENT_TURN_ITERATIONS.observe(max(0, iterations))
        AGENT_TURN_TOOL_CALLS.observe(max(0, tool_calls))

    def observe_context(measured_tokens: int, window_ratio: float, compressed: bool) -> None:
        AGENT_CONTEXT_TOKENS.observe(max(0, measured_tokens))
        AGENT_CONTEXT_WINDOW_RATIO.observe(min(1.0, max(0.0, window_ratio)))
        if compressed:
            AGENT_CONTEXT_COMPRESSED_TOTAL.inc()

    def observe_context_overflow() -> None:
        AGENT_CONTEXT_OVERFLOW_TOTAL.inc()

    def observe_reflection(removed: int) -> None:
        if removed > 0:
            AGENT_REFLECTION_SENTENCES_TOTAL.inc(removed)

    def observe_chat_slots(in_use: int) -> None:
        AGENT_CHAT_SLOTS_IN_USE.set(max(0, in_use))

    def set_chat_slot_limit(limit: int) -> None:
        AGENT_CHAT_SLOTS_LIMIT.set(max(0, limit))

    def observe_chat_rejected() -> None:
        AGENT_CHAT_REJECTED_TOTAL.inc()

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
