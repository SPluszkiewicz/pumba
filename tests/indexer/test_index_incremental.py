"""Skip-by-hash and tombstoning in the indexer batch pass."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_INDEXER = Path(__file__).resolve().parents[2] / "services" / "indexer"

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
        self.attached: list[tuple[str, str]] = []
        self.shared_blobs: set[tuple[str, str]] = set()

    def snapshot(self):
        from vector_store import IndexSnapshot

        blob_points = {blob: [] for blob in self.shared_blobs}
        for path, blob in self.hashes.items():
            blob_points.setdefault((path, blob), [])
        return IndexSnapshot(dict(self.hashes), blob_points)

    def listed_file_hashes(self) -> dict[str, str]:
        return dict(self.hashes)

    def delete_by_file_paths(self, paths: list[str], *, git_blob_hash: str | None = None) -> int:
        self.deleted.append(list(paths))
        for path in paths:
            if git_blob_hash is None or self.hashes.get(path) == git_blob_hash:
                self.hashes.pop(path, None)
        return len(paths)

    def persist_checkouts(self, updates) -> int:
        return len(updates)

    def attach_checkouts(self, pairs, snapshot=None) -> int:
        self.attached.extend(list(pairs))
        return len(pairs)

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


def test_embed_failure_keeps_existing_points(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store({"src/keep.cpp": "old-hash-not-matching"})
    monkeypatch.setattr(
        embeddings_client,
        "embed_texts",
        lambda *_a, **_k: (_ for _ in ()).throw(embeddings_client.EmbedderError("embedder down")),
    )

    stats = indexer_main.index_source_root(tree, store)

    assert stats["errors"] == 1
    assert store.deleted == []
    assert store.upserted == []
    assert store.hashes["src/keep.cpp"] == "old-hash-not-matching"


def test_new_file_is_indexed_without_a_prior_delete(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store()
    monkeypatch.setattr(embeddings_client, "embed_texts", lambda texts: [[0.1] * 4 for _ in texts])

    indexer_main.index_source_root(tree, store)

    assert "src/keep.cpp" in store.upserted
    assert store.deleted == [] or all("src/keep.cpp" not in batch for batch in store.deleted)


def test_second_branch_links_shared_blob_without_embedding(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keep = tree / "src" / "keep.cpp"
    blob = chunker.compute_blob_hash(keep.read_bytes(), tree)

    store = _Store()
    store.shared_blobs = {("src/keep.cpp", blob)}
    monkeypatch.setattr(
        embeddings_client,
        "embed_texts",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("shared blob must not be re-embedded")),
    )
    stats = indexer_main.index_source_root(tree, store)

    assert stats["files_skipped"] == 1
    assert store.attached == [("src/keep.cpp", blob)]


def test_shared_tree_attaches_in_one_call_without_embedding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second checkout must not rescan or re-embed once per file."""
    src = tmp_path / "src"
    src.mkdir()
    blobs: dict[str, str] = {}
    for i in range(12):
        path = src / f"f{i}.cpp"
        path.write_text(f"int f{i}() {{ return {i}; }}\n", encoding="utf-8")
        blobs[f"src/f{i}.cpp"] = chunker.compute_blob_hash(path.read_bytes(), tmp_path)

    store = _Store()
    store.shared_blobs = set(blobs.items())
    monkeypatch.setattr(
        embeddings_client,
        "embed_texts",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("shared blob must not be re-embedded")),
    )
    calls = {"snapshot": 0, "attach": 0}
    inner_snapshot = store.snapshot
    inner_attach = store.attach_checkouts

    def snapshot():
        calls["snapshot"] += 1
        return inner_snapshot()

    def attach_checkouts(pairs, snapshot=None):
        calls["attach"] += 1
        return inner_attach(pairs, snapshot)

    store.snapshot = snapshot  # type: ignore[method-assign]
    store.attach_checkouts = attach_checkouts  # type: ignore[method-assign]

    stats = indexer_main.index_source_root(tmp_path, store)

    assert stats["files_skipped"] == 12
    assert calls["snapshot"] == 1
    assert calls["attach"] == 1
    assert len(store.attached) == 12
    assert set(store.attached) == set(blobs.items())
    assert store.upserted == []


def test_matching_blob_detaches_stale_hashes(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keep = tree / "src" / "keep.cpp"
    blob = chunker.compute_blob_hash(keep.read_bytes(), tree)
    store = _Store({"src/keep.cpp": blob})
    monkeypatch.setattr(
        embeddings_client,
        "embed_texts",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not re-embed")),
    )
    chunk_count, outcome, _ = indexer_main.process_file(
        keep, tree, store, known_hash={blob, "stale-blob"}
    )
    assert outcome == "skipped"
    assert chunk_count == 0
    assert store.deleted == [["src/keep.cpp"]]
