from __future__ import annotations

from conversations import ConversationStore
from turns import TURNS


def test_history_round_trip_and_max_turns() -> None:
    store = ConversationStore(ttl_seconds=60, max_turns=2)
    store.remember("c1", "q1", "a1")
    store.remember("c1", "q2", "a2")
    store.remember("c1", "q3", "a3")
    messages = store.history("c1")
    assert [m["content"] for m in messages] == ["q2", "a2", "q3", "a3"]


def test_expired_conversation_is_forgotten() -> None:
    store = ConversationStore(ttl_seconds=10, max_turns=8)
    store.remember("c1", "q", "a")
    store._items["c1"].updated_monotonic -= 30  # noqa: SLF001 - expire without sleeping
    assert store.history("c1") == []


def test_in_flight_conversation_survives_ttl_sweep() -> None:
    store = ConversationStore(ttl_seconds=10, max_turns=8)
    store.remember("live", "q", "a")
    store._items["live"].updated_monotonic -= 30  # noqa: SLF001
    TURNS.start("live")
    try:
        history = store.history("live")
        assert [m["content"] for m in history] == ["q", "a"]
    finally:
        TURNS.finish("live")


def test_unknown_id_is_empty() -> None:
    assert ConversationStore().history("missing") == []


def test_trace_is_replayed_without_replacement_text() -> None:
    store = ConversationStore(ttl_seconds=60, max_turns=4)
    store.remember(
        "c1",
        "edit foo",
        "done",
        [
            {
                "name": "edit",
                "ok": False,
                "line": "snippet was not unique",
                "arguments": {"path": "src/foo.cpp", "old_string": "x" * 300, "new_string": "y"},
            }
        ],
    )
    history = store.history("c1")
    assert history[1]["role"] == "system"
    assert "old_string" not in history[1]["content"]
    assert "new_string" not in history[1]["content"]
    assert "- edit path=src/foo.cpp ok=false: snippet was not unique" in history[1]["content"]


def test_latest_verification_failure_survives_later_reads() -> None:
    store = ConversationStore(ttl_seconds=60, max_turns=2)
    calls = [
        {"name": "build_step", "ok": False, "line": "error: unknown type", "arguments": {"target": "pumba"}}
    ]
    calls.extend(
        {"name": "read_file", "ok": True, "line": f"read {index}", "arguments": {"path": f"f{index}.cpp"}}
        for index in range(12)
    )
    store.remember("c1", "q", "a", calls)
    text = store.history("c1")[1]["content"]
    assert "build_step" in text
    assert "error: unknown type" in text
    assert "f0.cpp" not in text
    assert "f11.cpp" in text
