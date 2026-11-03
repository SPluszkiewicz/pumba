"""Retrieval-augmented generation helpers.

Embeds the incoming query via the `embedder` service and searches Qdrant
(plain HTTP REST, per CONTRACTS.md) for similar code chunks. Resilience is
the whole point of this module: a missing collection, an empty collection,
or any transport error must degrade to "no context available" rather than
raising - `main.py` relies on that to set `used_context: false` and let the
model answer without RAG context.

The embedder is called through the `openai` SDK (`llm_client.embed_query`,
same `httpx.AsyncClient`). Qdrant is called via `httpx` (not the `qdrant-client`
package) so that the `X-Request-Id` correlation header can be forwarded on
every outbound call, same as the embedder/llm/tools calls, and so tests can
mock a single HTTP transport for everything.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
import llm_client
from cited_files import is_noise_path, paths_in_query
from common.logging import get_logger
from common.sparse_text import encode_sparse
from scope import current_collection

QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "code_chunks")

# Number of similar chunks returned to the prompt. Each leg of the hybrid
# query (dense and BM25) prefetches PREFETCH_K candidates; Qdrant fuses them
# with reciprocal rank fusion. When RERANKER_BASE_URL is set, fusion returns
# RERANK_CANDIDATES and POST /v1/rerank keeps TOP_K. Otherwise fusion limit
# is TOP_K and no rerank call is made.
TOP_K = 5
PREFETCH_K = 20
RERANK_CANDIDATES = 20
RERANK_DOC_CHARS = 4000
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "bm25"

# nomic-embed-text-v1.5 requires this exact prefix for queries (the indexer
# uses "search_document: " for chunks - see CONTRACTS.md). Getting this
# wrong silently degrades retrieval quality without erroring, so don't touch
# it casually.
QUERY_PREFIX = "search_query: "


@dataclass
class RetrievedChunk:
    file_path: str
    start_line: int
    end_line: int
    text: str
    symbol_name: Optional[str] = None
    score: float = 0.0


@dataclass
class RagResult:
    used_context: bool
    context_text: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    chunks: list[RetrievedChunk] = field(default_factory=list)
    # Prefetch (about 20) before the reranker, then the reranked window
    # before the noise filter. `chunks` is what a clean lookup shows.
    candidates: list[RetrievedChunk] = field(default_factory=list)
    reranked: list[RetrievedChunk] = field(default_factory=list)
    reranker_used: bool = False


log = get_logger("rag")


def _log(event: str, request_id: str, **fields: Any) -> None:
    # request_id is also bound in the request context by main.py; passing it
    # explicitly keeps these helpers usable (and testable) outside a request.
    log.info(event, request_id=request_id, **fields)


async def embed_query(http_client: httpx.AsyncClient, query: str, request_id: str) -> Optional[list[float]]:
    """Embeds the query (with the search_query prefix) via the `openai` SDK
    client in `llm_client.py`.

    Returns None (instead of raising) on any failure so callers can degrade
    gracefully.
    """
    try:
        return await llm_client.embed_query(http_client, QUERY_PREFIX + query, request_id)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, this must never crash the request
        _log("embed_query_failed", request_id, error=str(exc))
        return None


def hybrid_query_body(embedding: list[float], query_text: str, top_k: int) -> dict[str, Any]:
    """Prefetch dense + BM25, fuse with RRF. See CONTRACTS.md."""
    indices, values = encode_sparse(query_text)
    return {
        "prefetch": [
            {"query": embedding, "using": DENSE_VECTOR_NAME, "limit": PREFETCH_K},
            {
                "query": {"indices": indices, "values": values},
                "using": SPARSE_VECTOR_NAME,
                "limit": PREFETCH_K,
            },
        ],
        "query": {"fusion": "rrf"},
        "limit": top_k,
        "with_payload": True,
    }


def _points_from_body(body: dict[str, Any]) -> list[dict[str, Any]]:
    """`/points/query` returns `{result: {points: [...]}}`; `/points/search` returns a list."""
    result = body.get("result")
    if isinstance(result, dict):
        points = result.get("points") or []
    elif isinstance(result, list):
        points = result
    else:
        points = []
    return [point for point in points if isinstance(point, dict)]


def _chunks_from_points(points: list[dict[str, Any]]) -> list[RetrievedChunk]:
    chunks: list[RetrievedChunk] = []
    for point in points:
        payload = point.get("payload") or {}
        chunks.append(
            RetrievedChunk(
                file_path=payload.get("file_path", "unknown"),
                start_line=payload.get("start_line", 0),
                end_line=payload.get("end_line", 0),
                text=payload.get("text", ""),
                symbol_name=payload.get("symbol_name"),
                score=float(point.get("score") or 0.0),
            )
        )
    return chunks


async def search_chunks(
    http_client: httpx.AsyncClient,
    embedding: list[float],
    request_id: str,
    query_text: str,
    top_k: int = TOP_K,
) -> list[RetrievedChunk]:
    """Hybrid search (dense + BM25, RRF). Any failure, including a missing
    collection (404), is "no results", never re-raised.

    A 400 from `/points/query` means the collection has no named sparse
    vector yet. Fall back to a dense-only `/points/search` so an index that
    has not been rebuilt still answers.
    """
    headers = {"X-Request-Id": request_id}
    query_url = f"{QDRANT_URL}/collections/{current_collection()}/points/query"
    try:
        resp = await http_client.post(
            query_url,
            json=hybrid_query_body(embedding, query_text, top_k),
            headers=headers,
        )
        if resp.status_code == 404:
            _log("qdrant_search_degraded", request_id, reason="collection_missing")
            return []
        if resp.status_code == 400:
            _log("qdrant_search_dense_fallback", request_id, reason="query_rejected")
            return await _search_dense_only(http_client, embedding, request_id, top_k)
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:  # noqa: BLE001 - connection errors, timeouts, bad JSON, etc.
        _log("qdrant_search_degraded", request_id, reason="exception", detail=str(exc))
        return []

    return _chunks_from_points(_points_from_body(body))


async def _search_dense_only(
    http_client: httpx.AsyncClient,
    embedding: list[float],
    request_id: str,
    top_k: int,
) -> list[RetrievedChunk]:
    url = f"{QDRANT_URL}/collections/{current_collection()}/points/search"
    try:
        resp = await http_client.post(
            url,
            json={
                "vector": {"name": DENSE_VECTOR_NAME, "vector": embedding},
                "limit": top_k,
                "with_payload": True,
            },
            headers={"X-Request-Id": request_id},
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        _log("qdrant_search_degraded", request_id, reason="dense_fallback_failed", detail=str(exc))
        return []
    return _chunks_from_points(_points_from_body(body))


def _reranker_base_url() -> str:
    return os.getenv("RERANKER_BASE_URL", "").strip().rstrip("/")


def order_by_rerank(chunks: list[RetrievedChunk], body: Any, top_k: int) -> list[RetrievedChunk]:
    """Reorder `chunks` by `results[].index`. A bad body keeps the incoming order."""
    results = body.get("results") if isinstance(body, dict) else None
    if not isinstance(results, list):
        return chunks[:top_k]
    ranked: list[RetrievedChunk] = []
    seen: set[int] = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if not isinstance(index, int) or index < 0 or index >= len(chunks) or index in seen:
            continue
        seen.add(index)
        chunk = chunks[index]
        score = item.get("relevance_score")
        if isinstance(score, (int, float)):
            chunk.score = float(score)
        ranked.append(chunk)
        if len(ranked) >= top_k:
            break
    if not ranked:
        return chunks[:top_k]
    return ranked


async def rerank_chunks(
    http_client: httpx.AsyncClient,
    query: str,
    request_id: str,
    chunks: list[RetrievedChunk],
    top_k: int,
) -> tuple[list[RetrievedChunk], bool]:
    """Cross-encoder pass. Any failure keeps the RRF order, truncated to top_k.

    The boolean is true only when the reranker answered. A configured but
    unreachable reranker is false, so a baseline can see which stage ran.
    """
    base = _reranker_base_url()
    if not base or len(chunks) <= 1:
        return chunks[:top_k], False
    documents = [chunk.text[:RERANK_DOC_CHARS] for chunk in chunks]
    try:
        resp = await http_client.post(
            f"{base}/v1/rerank",
            json={"query": query, "documents": documents, "top_n": top_k},
            headers={"X-Request-Id": request_id},
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:  # noqa: BLE001 - rerank is optional; RRF order still answers
        _log("rerank_degraded", request_id, reason="exception", detail=str(exc))
        return chunks[:top_k], False
    return order_by_rerank(chunks, body, top_k), True


def freeze_hits(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Copy path, lines, symbol, and score. Drops chunk text.

    The reranker overwrites `score` on the live objects. The prefetch list
    has to keep the score it had before that pass.
    """
    return [
        RetrievedChunk(
            file_path=chunk.file_path,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            text="",
            symbol_name=chunk.symbol_name,
            score=chunk.score,
        )
        for chunk in chunks
    ]


def retrieval_trace(result: RagResult) -> dict[str, Any]:
    """What a baseline needs, and what `/lookup` hides by default."""

    def row(chunk: RetrievedChunk) -> dict[str, Any]:
        return {
            "file_path": chunk.file_path,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "symbol_name": chunk.symbol_name,
            "score": chunk.score,
        }

    return {
        "reranker_used": result.reranker_used,
        "candidates": [row(chunk) for chunk in result.candidates],
        "reranked": [row(chunk) for chunk in result.reranked],
        "index_hits": [row(chunk) for chunk in result.chunks],
    }


def clip_excerpt(text: str, limit: int = 400) -> str:
    """Short quote for a lookup hit. The chat model is not involved."""
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "…"


def drop_noise_chunks(chunks: list[RetrievedChunk], query: str, top_k: int) -> list[RetrievedChunk]:
    """Drop editor and preset files unless the question names them."""
    named = set(paths_in_query(query))
    kept = [
        chunk
        for chunk in chunks
        if not is_noise_path(chunk.file_path) or chunk.file_path.replace("\\", "/") in named
    ]
    if not kept:
        kept = list(chunks)
    return kept[:top_k]


def assemble_context(chunks: list[RetrievedChunk]) -> tuple[str, list[dict[str, Any]]]:
    """Builds a citation-annotated context string plus a `sources` list
    matching the /chat response shape from CONTRACTS.md."""
    if not chunks:
        return "", []

    parts: list[str] = []
    sources: list[dict[str, Any]] = []
    for i, chunk in enumerate(chunks, start=1):
        citation = f"[{i}] {chunk.file_path}:{chunk.start_line}-{chunk.end_line}"
        parts.append(f"{citation}\n{chunk.text}")
        sources.append(
            {
                "file_path": chunk.file_path,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "symbol_name": chunk.symbol_name,
            }
        )
    return "\n\n".join(parts), sources


async def retrieve_context(
    http_client: httpx.AsyncClient,
    query: str,
    request_id: str,
    top_k: int = TOP_K,
) -> RagResult:
    """End-to-end retrieval: embed the query, search Qdrant, assemble context.

    Never raises. Returns `used_context=False` with empty context/sources on
    any failure mode (embedding failure, missing collection, empty
    collection).
    """
    embedding = await embed_query(http_client, query, request_id)
    if embedding is None:
        _log("rag_no_context", request_id, reason="embedding_failed")
        return RagResult(used_context=False, context_text="", sources=[])

    fetch_k = top_k + 8
    if _reranker_base_url():
        fetch_k = max(fetch_k, RERANK_CANDIDATES)
    prefetched = await search_chunks(http_client, embedding, request_id, query, top_k=fetch_k)
    candidates = freeze_hits(prefetched)
    reranked_live, reranker_used = await rerank_chunks(
        http_client, query, request_id, prefetched, top_k
    )
    reranked = freeze_hits(reranked_live)
    chunks = drop_noise_chunks(reranked_live, query, top_k)
    if not chunks:
        _log("rag_no_context", request_id, reason="no_results")
        return RagResult(
            used_context=False,
            context_text="",
            sources=[],
            candidates=candidates,
            reranked=reranked,
            reranker_used=reranker_used,
        )

    context_text, sources = assemble_context(chunks)
    _log("rag_context_found", request_id, num_chunks=len(chunks))
    return RagResult(
        used_context=True,
        context_text=context_text,
        sources=sources,
        chunks=chunks,
        candidates=candidates,
        reranked=reranked,
        reranker_used=reranker_used,
    )
