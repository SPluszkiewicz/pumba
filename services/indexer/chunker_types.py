"""Shared `Chunk` dataclass for the C++ (`chunker.py`) and text
(`text_chunker.py`) chunkers - kept in its own module so neither imports the
other (the C++ chunker pulls in tree-sitter, which the text chunker must not
need)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    """One indexable unit of source code or documentation.

    Payload shape is pinned in CONTRACTS.md ("Qdrant collection code_chunks").
    """

    file_path: str
    symbol_name: str
    start_line: int
    end_line: int
    language: str
    text: str
    git_blob_hash: str
