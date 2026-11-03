"""Tests for services/indexer/embeddings_client.py.

The HTTP layer is a mocked `httpx` transport handed to the `openai` SDK
client (no real network calls, no dependency on a running `embedder`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "indexer"))

import embeddings_client  # noqa: E402
from embeddings_client import EmbedderRequestError, embed_texts  # noqa: E402


class Recorder:
    """Serves one canned response (or raises) and records the request."""

    def __init__(
        self,
        status_code: int = 200,
        body: Any = None,
        raw: Optional[bytes] = None,
        exc: Optional[Callable[[httpx.Request], Exception]] = None,
    ) -> None:
        self.status_code = status_code
        self.body = body
        self.raw = raw
        self.exc = exc
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.exc is not None:
            raise self.exc(request)
        if self.raw is not None:
            return httpx.Response(self.status_code, content=self.raw, headers={"content-type": "application/json"})
        return httpx.Response(self.status_code, json=self.body)

    def client(self, base_url: str = "http://embedder:8081") -> Any:
        return embeddings_client.make_client(
            base_url, http_client=httpx.Client(transport=httpx.MockTransport(self.handler))
        )

    @property
    def last_json(self) -> dict:
        return json.loads(self.requests[-1].content)


def _embeddings_body(n: int, dim: int = 4) -> dict:
    return {
        "object": "list",
        "model": "nomic-embed-text-v1.5",
        "data": [{"object": "embedding", "index": i, "embedding": [float(i)] * dim} for i in range(n)],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


@pytest.fixture(autouse=True)
def _no_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings_client, "MAX_RETRIES", 0)


class TestPrefixing:
    def test_search_document_prefix_is_applied_to_every_input(self) -> None:
        rec = Recorder(body=_embeddings_body(2))
        embed_texts(["foo", "bar"], client=rec.client())
        assert rec.last_json["input"] == ["search_document: foo", "search_document: bar"]
        assert rec.last_json["encoding_format"] == "float"
        assert rec.last_json["model"] == embeddings_client.EMBEDDING_MODEL

    def test_prefix_constant_matches_contract(self) -> None:
        assert embeddings_client.SEARCH_DOCUMENT_PREFIX == "search_document: "

    def test_empty_input_list_short_circuits_without_calling_http(self) -> None:
        rec = Recorder(body=_embeddings_body(0))
        assert embed_texts([], client=rec.client()) == []
        assert rec.requests == []


class TestResponseHandling:
    def test_returns_embeddings_in_original_order_even_if_response_is_shuffled(self) -> None:
        body = _embeddings_body(2, dim=2)
        body["data"].reverse()  # index 1 first
        rec = Recorder(body=body)
        assert embed_texts(["first", "second"], client=rec.client()) == [[0.0, 0.0], [1.0, 1.0]]

    def test_uses_configured_base_url_and_path(self) -> None:
        rec = Recorder(body=_embeddings_body(1))
        embed_texts(["x"], client=rec.client("http://embedder:8081/"))
        assert str(rec.requests[-1].url) == "http://embedder:8081/v1/embeddings"

    def test_base_url_from_env_when_no_client_given(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EMBEDDER_BASE_URL", "http://custom:9999/")
        assert str(embeddings_client.make_client().base_url) == "http://custom:9999/v1/"


class TestErrorHandling:
    def test_connection_error_raises_embedder_request_error(self) -> None:
        rec = Recorder(exc=lambda req: httpx.ConnectError("refused", request=req))
        with pytest.raises(EmbedderRequestError, match="failed"):
            embed_texts(["x"], client=rec.client())

    def test_timeout_raises_embedder_request_error(self) -> None:
        rec = Recorder(exc=lambda req: httpx.ReadTimeout("timed out", request=req))
        with pytest.raises(EmbedderRequestError):
            embed_texts(["x"], client=rec.client())

    def test_non_200_status_raises_embedder_request_error(self) -> None:
        rec = Recorder(status_code=500, body={"error": "boom"})
        with pytest.raises(EmbedderRequestError, match="500"):
            embed_texts(["x"], client=rec.client())

    def test_malformed_json_raises_embedder_request_error(self) -> None:
        rec = Recorder(raw=b"not json at all")
        with pytest.raises(EmbedderRequestError):
            embed_texts(["x"], client=rec.client())

    def test_missing_data_key_raises_embedder_request_error(self) -> None:
        rec = Recorder(body={"oops": []})
        with pytest.raises(EmbedderRequestError):
            embed_texts(["x"], client=rec.client())

    def test_mismatched_embedding_count_raises(self) -> None:
        rec = Recorder(body=_embeddings_body(1))
        with pytest.raises(EmbedderRequestError, match="1 embeddings for 2 inputs"):
            embed_texts(["one", "two"], client=rec.client())

    def test_connection_error_is_not_split_into_smaller_batches(self) -> None:
        rec = Recorder(exc=lambda req: httpx.ConnectError("refused", request=req))
        with pytest.raises(EmbedderRequestError):
            embed_texts(["one", "two"], client=rec.client())
        assert len(rec.requests) == 1

    def test_error_is_not_silently_swallowed(self) -> None:
        """The exception must propagate - never be caught-and-logged inside embed_texts."""
        rec = Recorder(exc=lambda req: httpx.ConnectError("refused", request=req))
        with pytest.raises(EmbedderRequestError):
            embed_texts(["x"], client=rec.client())


class TestBatching:
    def test_a_long_input_is_sent_in_groups(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(embeddings_client, "EMBED_BATCH", 2)
        sizes: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            sizes.append(len(payload["input"]))
            return httpx.Response(200, json=_embeddings_body(len(payload["input"])))

        client = embeddings_client.make_client(
            "http://embedder:8081",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        result = embed_texts(["a", "b", "c", "d", "e"], client=client)
        assert sizes == [2, 2, 1]
        assert len(result) == 5

    def test_a_timeout_retries_each_half_until_a_single_chunk_lands(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(embeddings_client, "EMBED_BATCH", 8)

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            if len(payload["input"]) > 1:
                raise httpx.ReadTimeout("timed out", request=request)
            return httpx.Response(200, json=_embeddings_body(1))

        client = embeddings_client.make_client(
            "http://embedder:8081",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        result = embed_texts(["a", "b"], client=client)
        assert result == [[0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0]]
