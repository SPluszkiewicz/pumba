"""Client for the `embedder` service's OpenAI-compatible embeddings API,
built on the official `openai` SDK (sync client - this is a batch job).

Per CONTRACTS.md, nomic-embed-text-v1.5 requires callers to prefix input text
with a task convention: `"search_document: "` when indexing (this module),
`"search_query: "` when querying at answer-time (the `agent` service's job,
not ours). Getting this wrong doesn't error - it just silently degrades
retrieval quality - so the prefix is applied here, unconditionally, rather
than trusted to every caller.

The SDK gives us retries with backoff on connection errors / 5xx / 429 and
typed responses; what remains ours is the prefixing, the contract check
(one vector per input, in order) and a single exception type for callers.
"""

from __future__ import annotations

import os
from typing import List, Optional, Sequence

import httpx
from common.qdrant_schema import DOCUMENT_PREFIX, QUERY_PREFIX
from openai import APIError, OpenAI

DEFAULT_BASE_URL = "http://embedder:8081"
DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_RETRIES = int(os.environ.get("INDEXER_EMBEDDER_MAX_RETRIES", "2"))
# One HTTP call per file times out on a large header and the embedder then
# drops every chunk of that file. A short batch finishes inside the timeout,
# so the file still lands in the index. A batch that is still too big is
# split in half and retried; a connection error is not, because splitting
# it would just repeat the same failure.
EMBED_BATCH = int(os.environ.get("INDEXER_EMBED_BATCH", "16"))
EMBEDDING_MODEL = "nomic-embed-text-v1.5"

SEARCH_DOCUMENT_PREFIX = DOCUMENT_PREFIX
SEARCH_QUERY_PREFIX = QUERY_PREFIX  # not used here; documented for completeness


class EmbedderError(RuntimeError):
    """Base class for all embedder-client failures."""


class EmbedderRequestError(EmbedderError):
    """The embedder call failed or returned something we can't use.

    Deliberately a single, clear, retryable exception type: connection
    errors, timeouts, non-200 responses (after the SDK's own retries), and
    malformed response bodies all raise this (instead of being
    logged-and-swallowed) so callers such as `main.py`'s batch loop can catch
    it and fail the file loudly rather than silently skipping embeddings.
    """


def _embedder_base_url(base_url: Optional[str]) -> str:
    return (base_url or os.environ.get("EMBEDDER_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")


def make_client(
    base_url: Optional[str] = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    http_client: Optional[httpx.Client] = None,
) -> OpenAI:
    """SDK client pointed at the embedder. `http_client` lets tests inject a
    mocked `httpx` transport."""
    return OpenAI(
        base_url=f"{_embedder_base_url(base_url)}/v1",
        api_key="local",  # llama-server ignores it; the SDK requires a value
        timeout=timeout,
        max_retries=MAX_RETRIES,
        http_client=http_client,
    )


def _batch_is_too_large(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        "timed out" in text
        or "timeout" in text
        or "exceed_context_size" in text
        or "max context size" in text
    )


def _embed_once(texts: Sequence[str], sdk: OpenAI) -> List[List[float]]:
    prefixed_inputs = [SEARCH_DOCUMENT_PREFIX + text for text in texts]
    try:
        response = sdk.embeddings.create(
            model=EMBEDDING_MODEL,
            input=prefixed_inputs,
            encoding_format="float",  # explicit: don't rely on the SDK's base64 default with llama-server
        )
    except (APIError, ValueError) as exc:
        # APIConnectionError, APITimeoutError, APIStatusError (non-2xx) - all
        # after the SDK's retries; ValueError covers a 200 with a non-JSON body
        # (json.JSONDecodeError), which the SDK lets through.
        raise EmbedderRequestError(f"embedder request to {sdk.base_url} failed: {exc}") from exc

    try:
        ordered = sorted(response.data, key=lambda item: item.index)
        embeddings = [list(item.embedding) for item in ordered]
    except (AttributeError, TypeError) as exc:
        raise EmbedderRequestError(
            f"embedder returned an unexpected response shape from {sdk.base_url}: {exc}"
        ) from exc

    if len(embeddings) != len(texts) or any(not isinstance(v, list) for v in embeddings):
        raise EmbedderRequestError(
            f"embedder returned {len(embeddings)} embeddings for {len(texts)} inputs sent to {sdk.base_url}"
        )
    return embeddings


def _embed_batched(texts: Sequence[str], sdk: OpenAI) -> List[List[float]]:
    if len(texts) > EMBED_BATCH:
        merged: List[List[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            merged.extend(_embed_batched(texts[start : start + EMBED_BATCH], sdk))
        return merged
    try:
        return _embed_once(texts, sdk)
    except EmbedderRequestError as exc:
        if len(texts) == 1 or not _batch_is_too_large(exc):
            raise
        mid = len(texts) // 2
        return _embed_batched(texts[:mid], sdk) + _embed_batched(texts[mid:], sdk)


def embed_texts(
    texts: Sequence[str],
    *,
    base_url: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client: Optional[OpenAI] = None,
) -> List[List[float]]:
    """Embed chunk texts, returning one vector per input text (in order).

    Every text is prefixed with `search_document: ` before being sent, per
    the nomic-embed-text-v1.5 convention - callers should pass raw chunk
    text, not pre-prefixed text. Inputs go out in groups of `EMBED_BATCH`
    so one large file cannot time out the whole request and disappear from
    the index. The caller still upserts only after every group has succeeded.
    """
    if not texts:
        return []
    sdk = client or make_client(base_url, timeout=timeout)
    return _embed_batched(texts, sdk)
