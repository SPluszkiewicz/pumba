"""Token hashing must stay identical for the indexer and the agent."""

from __future__ import annotations

import sys
from pathlib import Path

_SERVICES = Path(__file__).resolve().parents[2] / "services"
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from common.sparse_text import encode_sparse, tokens  # noqa: E402


def test_identifier_keeps_the_whole_name_and_its_pieces() -> None:
    found = tokens("m_bufferSize")
    assert "m_buffersize" in found
    assert "buffer" in found
    assert "size" in found
    assert "m" in found


def test_encode_sparse_is_stable_and_sorted() -> None:
    first = encode_sparse("m_bufferSize = 4;")
    second = encode_sparse("m_bufferSize = 4;")
    assert first == second
    indices, values = first
    assert indices == sorted(indices)
    assert len(indices) == len(values)
    assert all(value >= 1.0 for value in values)


def test_empty_text_is_a_single_dummy_term() -> None:
    indices, values = encode_sparse("   ")
    assert indices == [0]
    assert values == [1.0]
