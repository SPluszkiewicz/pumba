"""Bag-of-words sparse vectors shared by the indexer and the agent.

Qdrant applies IDF (`Modifier.IDF` on the `bm25` sparse vector). Both sides
must hash tokens the same way: there is no stored vocabulary. The index is
`crc32(token) & 0x7fffffff` so values stay in the positive int range Qdrant
accepts.

`m_bufferSize` yields the whole identifier plus `m`, `buffer`, and `size`,
so an exact name and its pieces both match.
"""

from __future__ import annotations

import re
import zlib

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def _camel_pieces(token: str) -> list[str]:
    pieces = [part.lower() for part in _CAMEL.findall(token) if part]
    raw = token.lower()
    if len(pieces) <= 1:
        return []
    return [piece for piece in pieces if piece != raw]


def tokens(text: str) -> list[str]:
    """Lowercased tokens: the raw identifier, snake pieces, and camel pieces.

    Each snake piece is camel-split too, so `m_bufferSize` yields
    `m_buffersize`, `m`, `buffersize`, `buffer`, and `size`.
    """
    found: list[str] = []
    for match in _IDENT.findall(text):
        raw = match.lower()
        found.append(raw)
        parts = [part for part in match.split("_") if part] if "_" in match else [match]
        for part in parts:
            if part.lower() != raw:
                found.append(part.lower())
            found.extend(_camel_pieces(part))
    return found


def encode_sparse(text: str) -> tuple[list[int], list[float]]:
    """Term frequencies as `(indices, values)`, sorted by index.

    Empty text still returns one dummy term so Qdrant accepts the vector.
    """
    counts: dict[int, float] = {}
    for token in tokens(text):
        index = zlib.crc32(token.encode("utf-8")) & 0x7FFFFFFF
        counts[index] = counts.get(index, 0.0) + 1.0
    if not counts:
        counts[0] = 1.0
    indices = sorted(counts)
    return indices, [counts[index] for index in indices]
