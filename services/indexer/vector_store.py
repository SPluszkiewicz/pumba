"""Qdrant wrapper for the `code_chunks` collection.

Schema and collection name are pinned in CONTRACTS.md - this module doesn't
invent anything, it just implements that contract:

- Collection: `QDRANT_COLLECTION` env var (`code_chunks`).
- Named dense vector `dense`: size 768, Cosine.
- Named sparse vector `bm25`: term frequencies, IDF applied by Qdrant.
  A collection that lacks either name is deleted and recreated.
- Payload per point: file_path, symbol_name, start_line, end_line, language,
  git_blob_hash, text, checkouts.
- Point ID: deterministic function of (file_path, git_blob_hash, symbol_name,
  start_line) so unchanged files across branches/checkouts share the exact
  same points (embedded once), while divergent versions of a file coexist
  and are filtered by `checkouts`.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any, Iterator, List, Optional, Sequence

from chunker_types import Chunk
from common.logging import get_logger
from common.qdrant_schema import (
    DEFAULT_CHECKOUT_ID,
    DEFAULT_COLLECTION,
    DEFAULT_URL,
    DENSE_VECTOR_NAME,
    PAYLOAD_INDEX_FIELDS,
    SPARSE_VECTOR_NAME,
    VECTOR_SIZE,
)
from common.sparse_text import encode_sparse
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    Modifier,
    PayloadSchemaType,
    PointStruct,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

DISTANCE = Distance.COSINE

log = get_logger("vector_store")

# Fixed, arbitrary namespace UUID for this project's point IDs. Must never
# change - changing it would make every future re-index produce different
# IDs than a prior run, breaking the "same chunk -> same point" guarantee.
_POINT_ID_NAMESPACE = uuid.UUID("c9f1b7f0-2a3f-4a3e-9b7a-6f1b6a9c2b1e")

# Separator between the fields fed into the point-ID hash. Unlikely to occur
# in a file path or symbol name, which avoids field-boundary collisions that
# plain string concatenation would allow (e.g. ("ab", "1") vs ("a", "b1")).
_ID_FIELD_SEPARATOR = "\x1f"
_WRITE_BATCH = 128


@dataclass
class IndexSnapshot:
    """One scroll of the collection: hashes for this checkout, points per blob."""

    checkout_hashes: dict[str, str]
    blob_points: dict[tuple[str, str], list[tuple[Any, list[str]]]]

    @property
    def blobs(self) -> set[tuple[str, str]]:
        return set(self.blob_points)


def _owners(payload: dict[str, Any]) -> list[str]:
    owners = payload.get("checkouts")
    if isinstance(owners, list):
        return [str(item) for item in owners]
    return []


def _equals_filter(**fields: str) -> Filter:
    return Filter(
        must=[FieldCondition(key=key, match=MatchValue(value=value)) for key, value in fields.items()]
    )


def _paths_filter(paths: Sequence[str]) -> Filter:
    return Filter(must=[FieldCondition(key="file_path", match=MatchAny(any=list(paths)))])


def _sparse_for_chunk(chunk: Chunk) -> SparseVector:
    """Symbol name plus body, so an exact identifier matches even inside a long chunk."""
    indices, values = encode_sparse(f"{chunk.symbol_name}\n{chunk.text}")
    return SparseVector(indices=indices, values=values)


def point_id(
    file_path: str,
    symbol_name: str,
    start_line: int,
    git_blob_hash: str = "",
) -> str:
    """Deterministic point ID for (file_path, git_blob_hash, symbol_name, start_line)."""
    parts = [file_path, symbol_name, str(start_line)]
    if git_blob_hash:
        parts.insert(1, git_blob_hash)
    key = _ID_FIELD_SEPARATOR.join(parts)
    return str(uuid.uuid5(_POINT_ID_NAMESPACE, key))


class VectorStore:
    """Thin wrapper around `qdrant_client.QdrantClient` for `code_chunks`."""

    def __init__(
        self,
        client: Optional[QdrantClient] = None,
        url: Optional[str] = None,
        collection: Optional[str] = None,
        checkout_id: Optional[str] = None,
    ) -> None:
        self.url = url or os.environ.get("QDRANT_URL", DEFAULT_URL)
        self.collection = collection or os.environ.get("QDRANT_COLLECTION", DEFAULT_COLLECTION)
        raw_co = checkout_id if checkout_id is not None else os.environ.get("INDEXER_CHECKOUT_ID", "")
        self.checkout_id = raw_co.strip() or DEFAULT_CHECKOUT_ID
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
                self._ensure_payload_indexes()
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
        self._ensure_payload_indexes()

    def _ensure_payload_indexes(self) -> None:
        """Keyword indexes so attach/delete can filter instead of scanning every point."""
        for field_name in PAYLOAD_INDEX_FIELDS:
            try:
                self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field_name,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception:  # noqa: BLE001 - already indexed, or a stub client
                continue

    def _scroll(
        self,
        keys: Sequence[str],
        query_filter: Filter | None = None,
    ) -> Iterator[Any]:
        offset = None
        while True:
            kwargs: dict[str, Any] = {
                "collection_name": self.collection,
                "limit": 256,
                "offset": offset,
                "with_payload": list(keys),
                "with_vectors": False,
            }
            if query_filter is not None:
                kwargs["scroll_filter"] = query_filter
            records, offset = self.client.scroll(**kwargs)
            yield from records
            if offset is None:
                break

    def _set_checkouts(self, updates: dict[Any, list[str]]) -> int:
        groups: dict[tuple[str, ...], list[Any]] = {}
        for pid, owners in updates.items():
            groups.setdefault(tuple(owners), []).append(pid)
        for owners, pids in groups.items():
            for start in range(0, len(pids), _WRITE_BATCH):
                self.client.set_payload(
                    collection_name=self.collection,
                    payload={"checkouts": list(owners)},
                    points=pids[start : start + _WRITE_BATCH],
                )
        return len(updates)

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
                id=point_id(
                    chunk.file_path,
                    chunk.symbol_name,
                    chunk.start_line,
                    chunk.git_blob_hash,
                ),
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
                    "checkouts": [self.checkout_id],
                },
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        self.client.upsert(collection_name=self.collection, points=points)
        return len(points)

    def snapshot(self) -> IndexSnapshot:
        """One pass over the collection. The indexer reuses this instead of scrolling per file."""
        if not self.client.collection_exists(self.collection):
            return IndexSnapshot({}, {})
        checkout_hashes: dict[str, str] = {}
        blob_points: dict[tuple[str, str], list[tuple[Any, list[str]]]] = {}
        for record in self._scroll(["file_path", "git_blob_hash", "checkouts"]):
            payload = record.payload or {}
            file_path = payload.get("file_path")
            if not file_path:
                continue
            blob_hash = str(payload.get("git_blob_hash") or "")
            owners = _owners(payload)
            blob_points.setdefault((file_path, blob_hash), []).append((record.id, owners))
            in_checkout = self.checkout_id in owners or (
                not owners and self.checkout_id == DEFAULT_CHECKOUT_ID
            )
            if in_checkout and file_path not in checkout_hashes:
                checkout_hashes[file_path] = blob_hash
        return IndexSnapshot(checkout_hashes, blob_points)

    def listed_index_state(self) -> tuple[dict[str, str], set[tuple[str, str]]]:
        """Return `(checkout_hashes, all_blobs)`.

        - `checkout_hashes`: `file_path -> git_blob_hash` currently linked to `self.checkout_id`.
        - `all_blobs`: every `(file_path, git_blob_hash)` already present in the collection
          across all checkouts/branches, so a second branch can attach `self.checkout_id`
          without re-embedding unchanged blobs.
        """
        snap = self.snapshot()
        return snap.checkout_hashes, snap.blobs

    def listed_file_hashes(self) -> dict[str, str]:
        """`file_path` → `git_blob_hash` for `self.checkout_id`."""
        return self.snapshot().checkout_hashes

    def attach_checkouts(
        self,
        pairs: Sequence[tuple[str, str]],
        snapshot: IndexSnapshot | None = None,
    ) -> int:
        """Tag `self.checkout_id` on every point of the given `(path, blob)` pairs.

        One filtered scroll for a single pair (watch). One unfiltered scroll, or
        the caller's snapshot, when many files share blobs with another checkout.
        Payload writes are grouped by the resulting `checkouts` list.
        """
        unique = list(dict.fromkeys(pairs))
        if not unique or not self.client.collection_exists(self.collection):
            return 0
        wanted = set(unique)
        updates: dict[Any, list[str]] = {}

        def _consider(pid: Any, owners: list[str]) -> None:
            if self.checkout_id not in owners:
                updates[pid] = [*owners, self.checkout_id]

        if snapshot is not None:
            for pair in unique:
                for pid, owners in snapshot.blob_points.get(pair, []):
                    _consider(pid, owners)
        elif len(unique) == 1:
            path, blob = unique[0]
            query = _equals_filter(file_path=path, git_blob_hash=blob)
            for record in self._scroll(["checkouts"], query):
                _consider(record.id, _owners(record.payload or {}))
        else:
            for record in self._scroll(["file_path", "git_blob_hash", "checkouts"]):
                payload = record.payload or {}
                pair = (payload.get("file_path"), str(payload.get("git_blob_hash") or ""))
                if pair in wanted:
                    _consider(record.id, _owners(payload))
        return self._set_checkouts(updates)

    def attach_checkout(self, file_path: str, git_blob_hash: str, snapshot: IndexSnapshot | None = None) -> int:
        """Add `self.checkout_id` to every point of `(file_path, git_blob_hash)` without re-embedding."""
        return self.attach_checkouts([(file_path, git_blob_hash)], snapshot=snapshot)

    def delete_by_file_paths(self, paths: Sequence[str]) -> int:
        """Detach `self.checkout_id` from `paths`; delete points that no checkout owns anymore."""
        unique = [p for p in dict.fromkeys(paths) if p]
        if not unique or not self.client.collection_exists(self.collection):
            return 0
        to_delete: list[Any] = []
        to_shrink: dict[Any, list[str]] = {}
        for record in self._scroll(["file_path", "checkouts"], _paths_filter(unique)):
            payload = record.payload or {}
            owners = payload.get("checkouts")
            if not isinstance(owners, list):
                to_delete.append(record.id)
                continue
            if self.checkout_id not in owners:
                continue
            remaining = [co for co in owners if co != self.checkout_id]
            if remaining:
                to_shrink[record.id] = remaining
            else:
                to_delete.append(record.id)
        self._set_checkouts(to_shrink)
        for start in range(0, len(to_delete), _WRITE_BATCH):
            self.client.delete(
                collection_name=self.collection,
                points_selector=to_delete[start : start + _WRITE_BATCH],
            )
        return len(unique)


__all__ = [
    "IndexSnapshot",
    "VectorStore",
    "point_id",
    "DEFAULT_CHECKOUT_ID",
    "DEFAULT_COLLECTION",
    "DEFAULT_URL",
    "VECTOR_SIZE",
    "DISTANCE",
    "DENSE_VECTOR_NAME",
    "SPARSE_VECTOR_NAME",
    "PAYLOAD_INDEX_FIELDS",
]
