"""Tests for services/indexer/vector_store.py.

Uses a small in-memory fake standing in for `qdrant_client.QdrantClient` so
these tests never require a real Qdrant server. The fake mimics just enough
of the real client's behavior (upsert-by-id overwrites, collection_exists)
to prove the idempotency and payload-schema guarantees.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "indexer"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services"))

import pytest
from chunker import Chunk
from vector_store import VectorStore, point_id


def _collection_info(schema: str):
    if schema == "hybrid":
        vectors = {"dense": object()}
        sparse = {"bm25": object()}
    else:
        vectors = object()
        sparse = {}
    return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=vectors, sparse_vectors=sparse)))


class _FakeQdrantClient:
    """In-memory stand-in for qdrant_client.QdrantClient, keyed by point id."""

    def __init__(self, existing_collections=None, schema="hybrid"):
        self.points = {}
        self._collections = set(existing_collections or [])
        self.schema = schema
        self.create_collection_calls = []
        self.deleted: list[str] = []

    def collection_exists(self, collection_name):
        return collection_name in self._collections

    def get_collection(self, collection_name):
        return _collection_info(self.schema)

    def delete_collection(self, collection_name):
        self._collections.discard(collection_name)
        self.deleted.append(collection_name)
        self.points = {}

    def create_collection(self, collection_name, vectors_config, sparse_vectors_config=None):
        self._collections.add(collection_name)
        self.schema = "hybrid"
        self.create_collection_calls.append(
            {
                "collection_name": collection_name,
                "vectors_config": vectors_config,
                "sparse_vectors_config": sparse_vectors_config,
            }
        )

    def upsert(self, collection_name, points):
        for point in points:
            # Real Qdrant upserts by ID: same ID replaces the prior point.
            self.points[point.id] = point

    def scroll(self, collection_name, limit=256, offset=None, with_payload=None, with_vectors=False):
        records = list(self.points.values())
        start = 0 if offset is None else int(offset)
        page = records[start : start + limit]
        next_offset = start + limit if start + limit < len(records) else None
        return page, next_offset

    def delete(self, collection_name, points_selector):
        wanted = set(points_selector.must[0].match.any)
        self.points = {pid: pt for pid, pt in self.points.items() if pt.payload.get("file_path") not in wanted}


def _make_chunk(**overrides):
    defaults = dict(
        file_path="src/widget.cpp",
        symbol_name="Widget::size",
        start_line=10,
        end_line=20,
        language="cpp",
        text="int size() const { return size_; }",
        git_blob_hash="deadbeef",
    )
    defaults.update(overrides)
    return Chunk(**defaults)


class TestPointId:
    def test_deterministic_for_same_inputs(self):
        a = point_id("src/widget.cpp", "Widget::size", 10)
        b = point_id("src/widget.cpp", "Widget::size", 10)
        assert a == b

    def test_differs_when_any_field_changes(self):
        base = point_id("src/widget.cpp", "Widget::size", 10)
        assert base != point_id("src/other.cpp", "Widget::size", 10)
        assert base != point_id("src/widget.cpp", "Widget::other", 10)
        assert base != point_id("src/widget.cpp", "Widget::size", 11)

    def test_is_a_valid_uuid_string(self):
        import uuid

        result = point_id("src/widget.cpp", "Widget::size", 10)
        uuid.UUID(result)  # raises ValueError if malformed

    def test_no_field_boundary_collision(self):
        # Without a separator, ("ab", "1", 0) and ("a", "b1", 0) could collide
        # under naive string concatenation.
        a = point_id("ab", "1", 0)
        b = point_id("a", "b1", 0)
        assert a != b


class TestEnsureCollection:
    def test_creates_collection_with_contract_schema_when_missing(self):
        fake = _FakeQdrantClient(existing_collections=set())
        store = VectorStore(client=fake, collection="code_chunks")

        store.ensure_collection()

        assert len(fake.create_collection_calls) == 1
        call = fake.create_collection_calls[0]
        assert call["collection_name"] == "code_chunks"
        assert call["vectors_config"]["dense"].size == 768
        assert call["vectors_config"]["dense"].distance == "Cosine"
        assert "bm25" in call["sparse_vectors_config"]

    def test_skips_creation_when_hybrid_collection_already_exists(self):
        fake = _FakeQdrantClient(existing_collections={"code_chunks"})
        store = VectorStore(client=fake, collection="code_chunks")

        store.ensure_collection()

        assert fake.create_collection_calls == []

    def test_recreates_a_legacy_dense_only_collection(self):
        fake = _FakeQdrantClient(existing_collections={"code_chunks"}, schema="legacy")
        store = VectorStore(client=fake, collection="code_chunks")

        store.ensure_collection()

        assert fake.deleted == ["code_chunks"]
        assert len(fake.create_collection_calls) == 1
        assert "bm25" in fake.create_collection_calls[0]["sparse_vectors_config"]


class TestUpsertIdempotency:
    def test_indexing_same_chunk_twice_results_in_exactly_one_point(self):
        fake = _FakeQdrantClient(existing_collections={"code_chunks"})
        store = VectorStore(client=fake, collection="code_chunks")
        chunk = _make_chunk()
        vector = [0.1] * 768

        store.upsert_chunks([chunk], [vector])
        store.upsert_chunks([chunk], [vector])

        assert len(fake.points) == 1

    def test_reindexing_with_changed_text_still_upserts_in_place(self):
        fake = _FakeQdrantClient(existing_collections={"code_chunks"})
        store = VectorStore(client=fake, collection="code_chunks")
        vector = [0.1] * 768

        store.upsert_chunks([_make_chunk(text="old body")], [vector])
        store.upsert_chunks([_make_chunk(text="new body", git_blob_hash="newhash")], [vector])

        assert len(fake.points) == 1
        (only_point,) = fake.points.values()
        assert only_point.payload["text"] == "new body"
        assert only_point.payload["git_blob_hash"] == "newhash"

    def test_different_symbols_produce_different_points(self):
        fake = _FakeQdrantClient(existing_collections={"code_chunks"})
        store = VectorStore(client=fake, collection="code_chunks")
        vector = [0.1] * 768

        store.upsert_chunks(
            [_make_chunk(symbol_name="Widget::size"), _make_chunk(symbol_name="Widget::grow")],
            [vector, vector],
        )

        assert len(fake.points) == 2


class TestPayloadSchema:
    def test_payload_matches_contracts_schema_exactly(self):
        fake = _FakeQdrantClient(existing_collections={"code_chunks"})
        store = VectorStore(client=fake, collection="code_chunks")
        chunk = _make_chunk()
        vector = [0.5] * 768

        store.upsert_chunks([chunk], [vector])

        (only_point,) = fake.points.values()
        assert set(only_point.payload.keys()) == {
            "file_path",
            "symbol_name",
            "start_line",
            "end_line",
            "language",
            "git_blob_hash",
            "text",
        }
        assert only_point.payload["file_path"] == chunk.file_path
        assert only_point.payload["symbol_name"] == chunk.symbol_name
        assert only_point.payload["start_line"] == chunk.start_line
        assert only_point.payload["end_line"] == chunk.end_line
        assert only_point.payload["language"] == chunk.language
        assert only_point.payload["git_blob_hash"] == chunk.git_blob_hash
        assert only_point.payload["text"] == chunk.text
        assert only_point.vector["dense"] == vector
        assert only_point.vector["bm25"].indices

    def test_upsert_returns_point_count(self):
        fake = _FakeQdrantClient(existing_collections={"code_chunks"})
        store = VectorStore(client=fake, collection="code_chunks")
        vector = [0.1] * 768

        count = store.upsert_chunks(
            [_make_chunk(symbol_name="a"), _make_chunk(symbol_name="b")], [vector, vector]
        )
        assert count == 2

    def test_upsert_rejects_mismatched_lengths(self):
        fake = _FakeQdrantClient(existing_collections={"code_chunks"})
        store = VectorStore(client=fake, collection="code_chunks")
        with pytest.raises(ValueError):
            store.upsert_chunks([_make_chunk()], [])

    def test_upsert_empty_is_a_noop(self):
        fake = _FakeQdrantClient(existing_collections={"code_chunks"})
        store = VectorStore(client=fake, collection="code_chunks")
        assert store.upsert_chunks([], []) == 0
        assert fake.points == {}


class TestListedFileHashesAndDelete:
    def test_listed_file_hashes_dedupes_chunks_of_the_same_file(self):
        fake = _FakeQdrantClient(existing_collections={"code_chunks"})
        store = VectorStore(client=fake, collection="code_chunks")
        vector = [0.1] * 768
        store.upsert_chunks(
            [
                _make_chunk(symbol_name="a", git_blob_hash="abc"),
                _make_chunk(symbol_name="b", git_blob_hash="abc"),
                _make_chunk(file_path="src/other.cpp", symbol_name="c", git_blob_hash="def"),
            ],
            [vector, vector, vector],
        )
        assert store.listed_file_hashes() == {"src/widget.cpp": "abc", "src/other.cpp": "def"}

    def test_listed_file_hashes_empty_when_collection_missing(self):
        fake = _FakeQdrantClient(existing_collections=set())
        store = VectorStore(client=fake, collection="code_chunks")
        assert store.listed_file_hashes() == {}

    def test_delete_by_file_paths_removes_every_chunk_of_those_files(self):
        fake = _FakeQdrantClient(existing_collections={"code_chunks"})
        store = VectorStore(client=fake, collection="code_chunks")
        vector = [0.1] * 768
        store.upsert_chunks(
            [
                _make_chunk(symbol_name="a"),
                _make_chunk(symbol_name="b"),
                _make_chunk(file_path="src/keep.cpp", symbol_name="c", git_blob_hash="x"),
            ],
            [vector, vector, vector],
        )
        assert store.delete_by_file_paths(["src/widget.cpp", "src/widget.cpp"]) == 1
        assert store.listed_file_hashes() == {"src/keep.cpp": "x"}

    def test_delete_empty_is_a_noop(self):
        fake = _FakeQdrantClient(existing_collections={"code_chunks"})
        store = VectorStore(client=fake, collection="code_chunks")
        assert store.delete_by_file_paths([]) == 0


class TestEnvDefaults:
    def test_reads_url_and_collection_from_env(self, monkeypatch):
        monkeypatch.setenv("QDRANT_URL", "http://qdrant-test:6333")
        monkeypatch.setenv("QDRANT_COLLECTION", "custom_chunks")
        fake = _FakeQdrantClient()
        store = VectorStore(client=fake)
        assert store.url == "http://qdrant-test:6333"
        assert store.collection == "custom_chunks"
