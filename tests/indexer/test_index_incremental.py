"""Skip-by-hash and tombstoning in the indexer batch pass."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_INDEXER = Path(__file__).resolve().parents[2] / "services" / "indexer"
sys.path.insert(0, str(_INDEXER))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services"))

import chunker
import embeddings_client
import pytest


def _load_indexer_main():
    # Agent tests also import a module named `main`; load this one from file
    # so collection order cannot bind us to the agent's FastAPI app.
    spec = importlib.util.spec_from_file_location("indexer_batch_main", _INDEXER / "main.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


indexer_main = _load_indexer_main()


class _Store:
    def __init__(self, hashes: dict[str, str] | None = None) -> None:
        self.hashes = dict(hashes or {})
        self.deleted: list[list[str]] = []
        self.upserted: list[str] = []

    def listed_file_hashes(self) -> dict[str, str]:
        return dict(self.hashes)

    def delete_by_file_paths(self, paths: list[str]) -> int:
        self.deleted.append(list(paths))
        for path in paths:
            self.hashes.pop(path, None)
        return len(paths)

    def upsert_chunks(self, chunks, vectors) -> int:
        if chunks:
            self.upserted.append(chunks[0].file_path)
            self.hashes[chunks[0].file_path] = chunks[0].git_blob_hash
        return len(chunks)


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "keep.cpp").write_text("int keep() { return 1; }\n")
    return tmp_path


def test_unchanged_file_is_skipped_without_embedding(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keep = tree / "src" / "keep.cpp"
    blob = chunker.compute_blob_hash(keep.read_bytes(), tree)
    store = _Store({"src/keep.cpp": blob, "src/gone.cpp": "stale"})

    def _boom(*_a, **_k):
        raise AssertionError("embedder must not be called for an unchanged file")

    monkeypatch.setattr(embeddings_client, "embed_texts", _boom)
    stats = indexer_main.index_source_root(tree, store)

    assert stats["files_skipped"] >= 1
    assert "src/keep.cpp" not in store.upserted
    assert any("src/gone.cpp" in batch for batch in store.deleted)
    assert stats["files_deleted"] >= 1


def test_changed_file_is_reindexed_after_deleting_old_points(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store({"src/keep.cpp": "old-hash-not-matching"})
    monkeypatch.setattr(embeddings_client, "embed_texts", lambda texts: [[0.1] * 4 for _ in texts])

    stats = indexer_main.index_source_root(tree, store)

    assert stats["files_indexed"] >= 1
    assert "src/keep.cpp" in store.upserted
    assert any("src/keep.cpp" in batch for batch in store.deleted)


def test_new_file_is_indexed_without_a_prior_delete(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store()
    monkeypatch.setattr(embeddings_client, "embed_texts", lambda texts: [[0.1] * 4 for _ in texts])

    indexer_main.index_source_root(tree, store)

    assert "src/keep.cpp" in store.upserted
    assert store.deleted == [] or all("src/keep.cpp" not in batch for batch in store.deleted)
