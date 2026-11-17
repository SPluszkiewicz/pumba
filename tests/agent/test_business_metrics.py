"""Business series registered next to the HTTP instrumentator."""

from __future__ import annotations

from prometheus_client import generate_latest

from common.metrics import (
    observe_chat_rejected,
    observe_chat_slots,
    observe_context,
    observe_context_overflow,
    observe_rag_retrieval,
    observe_reflection,
    observe_tool_duration,
    observe_turn,
    set_chat_slot_limit,
)


def test_turn_rag_and_tool_series_are_exported() -> None:
    observe_turn("code", "ok", 1.5, 3, 2)
    observe_context(4096, 0.42, compressed=True)
    observe_context_overflow()
    observe_rag_retrieval(0.3, 5, reranker_used=True)
    observe_tool_duration("build", 2.0)
    observe_reflection(2)
    set_chat_slot_limit(2)
    observe_chat_slots(1)
    observe_chat_rejected()

    text = generate_latest().decode()
    for name in (
        "agent_turns_total",
        "agent_turn_duration_seconds",
        "agent_turn_iterations",
        "agent_turn_tool_calls",
        "agent_context_tokens",
        "agent_context_window_ratio",
        "agent_context_compressed_total",
        "agent_context_overflow_total",
        "rag_retrieval_duration_seconds",
        "rag_sources",
        "rag_reranker_total",
        "tool_call_duration_seconds",
        "agent_reflection_sentences_removed_total",
        "agent_chat_slots_in_use",
        "agent_chat_slots_limit",
        "agent_chat_rejected_total",
    ):
        assert name in text
