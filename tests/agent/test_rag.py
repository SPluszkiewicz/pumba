"""Tests for rag.py, focused on the resilience requirement: normal
retrieval, a missing Qdrant collection (404-equivalent), and an empty
collection must all degrade gracefully to "no context available" rather than
raising.
"""

from __future__ import annotations

import json

import httpx
import pytest
import rag

EMBEDDING = [0.1, 0.2, 0.3]


@pytest.fixture(autouse=True)
def _reranker_off_unless_a_test_opts_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RERANKER_BASE_URL", raising=False)


def _embedding_response(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.read())
    assert payload["input"] == "search_query: how does main work"
    return httpx.Response(200, json={"data": [{"embedding": EMBEDDING}]})


def _client_with_handler(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_embed_query_uses_search_query_prefix() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        assert payload["input"] == "search_query: hello world"
        assert request.headers["x-request-id"] == "req-embed"
        return httpx.Response(200, json={"data": [{"embedding": EMBEDDING}]})

    async with _client_with_handler(handler) as client:
        embedding = await rag.embed_query(client, "hello world", "req-embed")

    assert embedding == EMBEDDING


@pytest.mark.asyncio
async def test_embed_query_returns_none_on_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with _client_with_handler(handler) as client:
        embedding = await rag.embed_query(client, "hello world", "req-embed-fail")

    assert embedding is None


@pytest.mark.asyncio
async def test_retrieve_context_normal_retrieval_with_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/embeddings"):
            return _embedding_response(request)
        if request.url.path.endswith("/points/query"):
            body = json.loads(request.read())
            assert body["query"] == {"fusion": "rrf"}
            assert {leg["using"] for leg in body["prefetch"]} == {"dense", "bm25"}
            return httpx.Response(
                200,
                json={
                    "result": {
                        "points": [
                            {
                                "id": 1,
                                "score": 0.93,
                                "payload": {
                                    "file_path": "app/src/main.cpp",
                                    "symbol_name": "main",
                                    "start_line": 12,
                                    "end_line": 40,
                                    "text": "int main() { return 0; }",
                                },
                            }
                        ]
                    }
                },
            )
        raise AssertionError(f"unexpected request to {request.url}")

    async with _client_with_handler(handler) as client:
        result = await rag.retrieve_context(client, "how does main work", "req-1")

    assert result.used_context is True
    assert result.sources == [
        {"file_path": "app/src/main.cpp", "start_line": 12, "end_line": 40, "symbol_name": "main"}
    ]
    assert "[1] app/src/main.cpp:12-40" in result.context_text
    assert "int main() { return 0; }" in result.context_text


@pytest.mark.asyncio
async def test_retrieve_context_missing_collection_degrades_gracefully() -> None:
    """Qdrant returns 404 when the collection doesn't exist yet - must not raise."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/embeddings"):
            return _embedding_response(request)
        if request.url.path.endswith("/points/query"):
            return httpx.Response(
                404,
                json={"status": {"error": "Collection `code_chunks` doesn't exist"}, "time": 0.0},
            )
        raise AssertionError(f"unexpected request to {request.url}")

    async with _client_with_handler(handler) as client:
        result = await rag.retrieve_context(client, "how does main work", "req-2")

    assert result.used_context is False
    assert result.sources == []
    assert result.context_text == ""


@pytest.mark.asyncio
async def test_retrieve_context_empty_collection_degrades_gracefully() -> None:
    """Collection exists but has zero points - also not an error."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/embeddings"):
            return _embedding_response(request)
        if request.url.path.endswith("/points/query"):
            return httpx.Response(200, json={"result": {"points": []}})
        raise AssertionError(f"unexpected request to {request.url}")

    async with _client_with_handler(handler) as client:
        result = await rag.retrieve_context(client, "how does main work", "req-3")

    assert result.used_context is False
    assert result.sources == []
    assert result.context_text == ""


@pytest.mark.asyncio
async def test_retrieve_context_degrades_when_embedder_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/embeddings"):
            raise httpx.ConnectError("connection refused", request=request)
        raise AssertionError("qdrant should not be called if embedding failed")

    async with _client_with_handler(handler) as client:
        result = await rag.retrieve_context(client, "how does main work", "req-4")

    assert result.used_context is False
    assert result.sources == []


@pytest.mark.asyncio
async def test_retrieve_context_degrades_on_qdrant_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/embeddings"):
            return _embedding_response(request)
        if request.url.path.endswith("/points/query"):
            raise httpx.ConnectError("connection refused", request=request)
        raise AssertionError(f"unexpected request to {request.url}")

    async with _client_with_handler(handler) as client:
        result = await rag.retrieve_context(client, "how does main work", "req-5")

    assert result.used_context is False
    assert result.sources == []


@pytest.mark.asyncio
async def test_retrieve_context_falls_back_to_dense_search_on_query_400() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/embeddings"):
            return _embedding_response(request)
        if request.url.path.endswith("/points/query"):
            return httpx.Response(400, json={"status": {"error": "sparse vector missing"}})
        if request.url.path.endswith("/points/search"):
            body = json.loads(request.read())
            assert body["vector"]["name"] == "dense"
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "id": 1,
                            "score": 0.4,
                            "payload": {
                                "file_path": "src/main.cpp",
                                "symbol_name": "main",
                                "start_line": 1,
                                "end_line": 4,
                                "text": "int main() {}",
                            },
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected request to {request.url}")

    async with _client_with_handler(handler) as client:
        result = await rag.retrieve_context(client, "how does main work", "req-fallback")

    assert result.used_context is True
    assert result.sources[0]["file_path"] == "src/main.cpp"


def _chunk(path: str, text: str, score: float = 0.0) -> rag.RetrievedChunk:
    return rag.RetrievedChunk(file_path=path, start_line=1, end_line=2, text=text, score=score)


def test_order_by_rerank_keeps_rrf_order_when_the_body_is_unusable() -> None:
    chunks = [_chunk("a.cpp", "alpha", 0.9), _chunk("b.cpp", "beta", 0.2)]
    assert rag.order_by_rerank(chunks, {"results": "nope"}, top_k=1) == chunks[:1]
    assert rag.order_by_rerank(chunks, {}, top_k=5) == chunks


def test_order_by_rerank_reorders_and_stores_the_score() -> None:
    chunks = [_chunk("a.cpp", "alpha"), _chunk("b.cpp", "beta"), _chunk("c.cpp", "gamma")]
    body = {
        "results": [
            {"index": 2, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.1},
            {"index": 99, "relevance_score": 1.0},
        ]
    }
    ranked = rag.order_by_rerank(chunks, body, top_k=2)
    assert [chunk.file_path for chunk in ranked] == ["c.cpp", "a.cpp"]
    assert ranked[0].score == 0.9


@pytest.mark.asyncio
async def test_retrieve_context_reranks_when_the_url_is_set(monkeypatch) -> None:
    monkeypatch.setenv("RERANKER_BASE_URL", "http://reranker.test")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/embeddings"):
            return _embedding_response(request)
        if request.url.path.endswith("/points/query"):
            body = json.loads(request.read())
            assert body["limit"] == rag.RERANK_CANDIDATES
            points = []
            for name, score in (("a.cpp", 0.9), ("b.cpp", 0.2)):
                points.append(
                    {
                        "id": name,
                        "score": score,
                        "payload": {
                            "file_path": name,
                            "symbol_name": "f",
                            "start_line": 1,
                            "end_line": 2,
                            "text": name,
                        },
                    }
                )
            return httpx.Response(200, json={"result": {"points": points}})
        if request.url.path.endswith("/v1/rerank"):
            body = json.loads(request.read())
            assert body["documents"] == ["a.cpp", "b.cpp"]
            assert body["top_n"] == rag.TOP_K
            assert request.headers["x-request-id"] == "req-rerank"
            return httpx.Response(
                200,
                json={"results": [{"index": 1, "relevance_score": 0.8}, {"index": 0, "relevance_score": 0.1}]},
            )
        raise AssertionError(f"unexpected request to {request.url}")

    async with _client_with_handler(handler) as client:
        result = await rag.retrieve_context(client, "how does main work", "req-rerank")

    assert [source["file_path"] for source in result.sources] == ["b.cpp", "a.cpp"]
    assert [chunk.file_path for chunk in result.candidates] == ["a.cpp", "b.cpp"]
    assert result.candidates[0].score == 0.9
    assert [chunk.file_path for chunk in result.reranked] == ["b.cpp", "a.cpp"]
    assert result.reranker_used is True


@pytest.mark.asyncio
async def test_retrieve_context_keeps_rrf_order_when_rerank_fails(monkeypatch) -> None:
    monkeypatch.setenv("RERANKER_BASE_URL", "http://reranker.test")

    called = {"rerank": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/embeddings"):
            return _embedding_response(request)
        if request.url.path.endswith("/points/query"):
            points = []
            for name in ("kept.cpp", "other.cpp"):
                points.append(
                    {
                        "id": name,
                        "score": 0.5,
                        "payload": {
                            "file_path": name,
                            "symbol_name": "f",
                            "start_line": 1,
                            "end_line": 2,
                            "text": name,
                        },
                    }
                )
            return httpx.Response(200, json={"result": {"points": points}})
        if request.url.path.endswith("/v1/rerank"):
            called["rerank"] = True
            return httpx.Response(503, json={"error": "down"})
        raise AssertionError(f"unexpected request to {request.url}")

    async with _client_with_handler(handler) as client:
        result = await rag.retrieve_context(client, "how does main work", "req-rerank-down")

    assert called["rerank"] is True
    assert [source["file_path"] for source in result.sources] == ["kept.cpp", "other.cpp"]
    assert result.reranker_used is False
    assert [chunk.file_path for chunk in result.candidates] == ["kept.cpp", "other.cpp"]


def test_assemble_context_with_no_chunks_returns_empty() -> None:
    text, sources = rag.assemble_context([])
    assert text == ""
    assert sources == []


def test_assemble_context_builds_citation_markers() -> None:
    chunks = [
        rag.RetrievedChunk(file_path="a.cpp", start_line=1, end_line=5, text="foo", symbol_name="foo"),
        rag.RetrievedChunk(file_path="b.cpp", start_line=10, end_line=20, text="bar", symbol_name="bar"),
    ]
    text, sources = rag.assemble_context(chunks)
    assert "[1] a.cpp:1-5" in text
    assert "[2] b.cpp:10-20" in text
    assert len(sources) == 2
