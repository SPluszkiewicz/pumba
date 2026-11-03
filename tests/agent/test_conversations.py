from __future__ import annotations

from conversations import ConversationStore


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


def test_unknown_id_is_empty() -> None:
    assert ConversationStore().history("missing") == []
