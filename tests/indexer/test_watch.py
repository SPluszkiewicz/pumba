"""`--watch` runs a batch first; filesystem bursts collapse to one upsert."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_INDEXER = Path(__file__).resolve().parents[2] / "services" / "indexer"


def _load_indexer_main():
    spec = importlib.util.spec_from_file_location("indexer_watch_main", _INDEXER / "main.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


indexer_main = _load_indexer_main()


def test_watch_flag_indexes_before_listening(tmp_path: Path, monkeypatch) -> None:
    order: list[str] = []

    monkeypatch.setattr(
        indexer_main, "index_source_root", lambda *_a, **_k: order.append("batch") or {"errors": 0}
    )
    monkeypatch.setattr(indexer_main, "_run_watch", lambda *_a, **_k: order.append("watch"))

    class _Store:
        def ensure_collection(self) -> None:
            order.append("ensure")

    monkeypatch.setattr(indexer_main.vector_store, "VectorStore", _Store)
    assert indexer_main.main(["--source-root", str(tmp_path), "--watch"]) == 0
    assert order == ["ensure", "batch", "watch"]


def test_debouncer_keeps_the_last_event() -> None:
    flushed: list[tuple[str, str]] = []
    debouncer = indexer_main.WatchDebouncer(0.5, lambda path, kind: flushed.append((path, kind)))
    debouncer.submit("src/a.cpp", "changed")
    debouncer.submit("src/a.cpp", "changed")
    debouncer.submit("src/a.cpp", "deleted")
    debouncer.submit("src/b.cpp", "changed")
    debouncer.flush_all()
    assert flushed == [("src/a.cpp", "deleted"), ("src/b.cpp", "changed")]


def test_zero_delay_flushes_immediately() -> None:
    flushed: list[tuple[str, str]] = []
    debouncer = indexer_main.WatchDebouncer(0, lambda path, kind: flushed.append((path, kind)))
    debouncer.submit("src/a.cpp", "changed")
    assert flushed == [("src/a.cpp", "changed")]


def test_stale_timer_generation_is_ignored() -> None:
    flushed: list[tuple[str, str]] = []
    debouncer = indexer_main.WatchDebouncer(0.5, lambda path, kind: flushed.append((path, kind)))
    debouncer.submit("src/a.cpp", "changed")
    debouncer._fire("src/a.cpp", 0)
    assert flushed == []
    debouncer.flush_all()
    assert flushed == [("src/a.cpp", "changed")]
