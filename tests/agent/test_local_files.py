"""One checkout, two callers, different uncommitted files."""

from __future__ import annotations

from local_files import clear_all, publish, published
from rag import RetrievedChunk
from working_copy import chunks_with_live


def setup_function() -> None:
    clear_all()


def test_two_callers_keep_their_own_files() -> None:
    publish("co_server", "alice", {"src/calculator.hpp": "int AliceOffset();\n"})
    publish("co_server", "bob", {"src/calculator.hpp": "int BobOffset();\n"})
    assert "AliceOffset" in published("co_server", "alice")["src/calculator.hpp"]
    assert "BobOffset" not in published("co_server", "alice")["src/calculator.hpp"]
    assert "BobOffset" in published("co_server", "bob")["src/calculator.hpp"]
    assert published("co_server", "cara") == {}


def test_local_file_replaces_the_shared_chunk() -> None:
    chunks = [RetrievedChunk("src/calculator.hpp", 1, 4, "int Evaluate();", "Evaluate", 1.0)]
    _text, _sources, refreshed = chunks_with_live(
        chunks, {"src/calculator.hpp": "int AliceOffset();\nint Evaluate();\n"}
    )
    assert refreshed[0].file_path == "src/calculator.hpp"
    assert "AliceOffset" in refreshed[0].text
    assert "BobOffset" not in refreshed[0].text


def test_rejects_a_path_that_escapes_the_checkout() -> None:
    try:
        publish("co_server", "alice", {"../secret.hpp": "int x;\n"})
    except ValueError as exc:
        assert "inside" in str(exc)
    else:
        raise AssertionError("escaped path was accepted")
