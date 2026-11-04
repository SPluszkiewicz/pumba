"""Qdrant collection and embedding prefixes. Indexer and agent must stay aligned.

CONTRACTS.md is the human copy of these names. Changing a value here changes
both the write path (indexer) and the query path (agent).
"""

from __future__ import annotations

DEFAULT_URL = "http://qdrant:6333"
DEFAULT_COLLECTION = "code_chunks"
DEFAULT_CHECKOUT_ID = "default"

VECTOR_SIZE = 768
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "bm25"
PAYLOAD_INDEX_FIELDS = ("file_path", "git_blob_hash", "checkouts")

DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "
