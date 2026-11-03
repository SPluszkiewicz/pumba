"""Qdrant wrapper for the `code_chunks` collection.

Schema and collection name are pinned in CONTRACTS.md - this module doesn't
invent anything, it just implements that contract:

- Collection: `QDRANT_COLLECTION` env var (`code_chunks`).
- Named dense vector `dense`: size 768, Cosine.
- Named sparse vector `bm25`: term frequencies, IDF applied by Qdrant.
  A collection that lacks either name is deleted and recreated.
- Payload per point: file_path, symbol_name, start_line, end_line, language,
  git_blob_hash, text.
- Point ID: deterministic function of (file_path, symbol_name, start_line) so
  re-indexing the same chunk *upserts in place* instead of duplicating -
  Qdrant point IDs must be an unsigned int or a UUID string, so we use a
  stable `uuid.uuid5` under a fixed, project-local namespace.
"""

from __future__ import annotations

import os
import uuid
from typing import List, Optional, Sequence

from chunker_types import Chunk
from common.logging import get_logger
from common.sparse_text import encode_sparse
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    Modifier,
    PointStruct,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

DEFAULT_QDRANT_URL = "http://qdrant:6333"
DEFAULT_COLLECTION = "code_chunks"

VECTOR_SIZE = 768
DISTANCE = Distance.COSINE
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "bm25"

log = get_logger("vector_store")

# Fixed, arbitrary namespace UUID for this project's point IDs. Must never
# change - changing it would make every future re-index produce different
# IDs than a prior run, breaking the "same chunk -> same point" guarantee.
_POINT_ID_NAMESPACE = uuid.UUID("c9f1b7f0-2a3f-4a3e-9b7a-6f1b6a9c2b1e")

# Separator between the fields fed into the point-ID hash. Unlikely to occur
# in a file path or symbol name, which avoids field-boundary collisions that
# plain string concatenation would allow (e.g. ("ab", "1") vs ("a", "b1")).
_ID_FIELD_SEPARATOR = "\x1f"


def _sparse_for_chunk(chunk: Chunk) -> SparseVector:
    """Symbol name plus body, so an exact identifier matches even inside a long chunk."""
    indices, values = encode_sparse(f"{chunk.symbol_name}\n{chunk.text}")
    return SparseVector(indices=indices, values=values)


def point_id(file_path: str, symbol_name: str, start_line: int) -> str:
    """Deterministic point ID for (file_path, symbol_name, start_line)."""
    key = _ID_FIELD_SEPARATOR.join([file_path, symbol_name, str(start_line)])
    return str(uuid.uuid5(_POINT_ID_NAMESPACE, key))


class VectorStore:
    """Thin wrapper around `qdrant_client.QdrantClient` for `code_chunks`."""

    def __init__(
        self,
        client: Optional[QdrantClient] = None,
        url: Optional[str] = None,
        collection: Optional[str] = None,
    ) -> None:
        self.url = url or os.environ.get("QDRANT_URL", DEFAULT_QDRANT_URL)
        self.collection = collection or os.environ.get("QDRANT_COLLECTION", DEFAULT_COLLECTION)
        self.client = client or QdrantClient(url=self.url)

    def _has_hybrid_schema(self) -> bool:
        """True when the live collection already has `dense` and `bm25`."""
        try:
            info = self.client.get_collection(self.collection)
            params = info.config.params
            vectors = params.vectors
            sparse = getattr(params, "sparse_vectors", None) or {}
        except Exception:  # noqa: BLE001 - missing collection or unexpected client shape
            return False
        named_dense = isinstance(vectors, dict) and DENSE_VECTOR_NAME in vectors
        return named_dense and SPARSE_VECTOR_NAME in sparse

    def ensure_collection(self) -> None:
        """Create `dense` + `bm25`, or recreate a pre-hybrid collection."""
        if self.client.collection_exists(self.collection):
            if self._has_hybrid_schema():
                return
            log.warning("qdrant_collection_recreate", collection=self.collection, reason="missing_hybrid_schema")
            self.client.delete_collection(self.collection)
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config={DENSE_VECTOR_NAME: VectorParams(size=VECTOR_SIZE, distance=DISTANCE)},
            sparse_vectors_config={
                SPARSE_VECTOR_NAME: SparseVectorParams(modifier=Modifier.IDF),
            },
        )

    def upsert_chunks(self, chunks: Sequence[Chunk], vectors: Sequence[List[float]]) -> int:
        """Upsert one point per (chunk, vector) pair. Returns the number of points written."""
        if len(chunks) != len(vectors):
            raise ValueError(
                f"chunks/vectors length mismatch: {len(chunks)} chunks vs {len(vectors)} vectors"
            )
        if not chunks:
            return 0

        points = [
            PointStruct(
                id=point_id(chunk.file_path, chunk.symbol_name, chunk.start_line),
                vector={
                    DENSE_VECTOR_NAME: list(vector),
                    SPARSE_VECTOR_NAME: _sparse_for_chunk(chunk),
                },
                payload={
                    "file_path": chunk.file_path,
                    "symbol_name": chunk.symbol_name,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "language": chunk.language,
                    "git_blob_hash": chunk.git_blob_hash,
                    "text": chunk.text,
                },
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        self.client.upsert(collection_name=self.collection, points=points)
        return len(points)

    def listed_file_hashes(self) -> dict[str, str]:
        """`file_path` → `git_blob_hash` for every distinct file in the collection.

        All chunks of a file share the same hash (it is the whole-file blob).
        Scrolls payload only - no vectors. Missing collection → empty dict.
        """
        if not self.client.collection_exists(self.collection):
            return {}

        hashes: dict[str, str] = {}
        offset = None
        while True:
            records, offset = self.client.scroll(
                collection_name=self.collection,
                limit=256,
                offset=offset,
                with_payload=["file_path", "git_blob_hash"],
                with_vectors=False,
            )
            for record in records:
                payload = record.payload or {}
                file_path = payload.get("file_path")
                if file_path and file_path not in hashes:
                    hashes[file_path] = str(payload.get("git_blob_hash") or "")
            if offset is None:
                break
        return hashes

    def delete_by_file_paths(self, paths: Sequence[str]) -> int:
        """Drop every point whose `file_path` is in `paths`. Returns `len(paths)`
        (the number of files asked, not the number of points removed)."""
        unique = [p for p in dict.fromkeys(paths) if p]
        if not unique or not self.client.collection_exists(self.collection):
            return 0
        # MatchAny payloads stay small when a re-index drops thousands of paths.
        batch = 128
        for start in range(0, len(unique), batch):
            chunk = unique[start : start + batch]
            self.client.delete(
                collection_name=self.collection,
                points_selector=Filter(must=[FieldCondition(key="file_path", match=MatchAny(any=chunk))]),
            )
        return len(unique)


__all__ = [
    "VectorStore",
    "point_id",
    "VECTOR_SIZE",
    "DISTANCE",
    "DENSE_VECTOR_NAME",
    "SPARSE_VECTOR_NAME",
]
