"""Named files are read from the tree. Preset and editor files do not crowd them out."""

from __future__ import annotations

from cited_files import is_noise_path, named_file_block, paths_in_query
from rag import RetrievedChunk, drop_noise_chunks


def test_paths_in_query_keep_repo_relative_files() -> None:
    found = paths_in_query("Compare src/calculator.hpp:9-17 with src/calculator.cpp and ignore .. /etc/passwd.")
    assert found == ["src/calculator.hpp", "src/calculator.cpp"]


def test_paths_in_query_keeps_dotfiles_and_strips_only_dot_slash() -> None:
    found = paths_in_query("why does ./src/main.cpp ignore .github/workflows/ci.yml and .vscode/mcp.json ?")
    assert found == ["src/main.cpp", ".github/workflows/ci.yml", ".vscode/mcp.json"]


def test_named_file_block_quotes_the_header(tmp_path, monkeypatch) -> None:
    header = tmp_path / "src"
    header.mkdir()
    (header / "calculator.hpp").write_text("class CalcError {};\n", encoding="utf-8")
    monkeypatch.setattr("repo_map.map_root", lambda: tmp_path)
    text = named_file_block("que promet src/calculator.hpp ?")
    assert "[file] src/calculator.hpp" in text
    assert "class CalcError {};" in text


def test_noise_chunks_lose_their_slot_unless_named() -> None:
    chunks = [
        RetrievedChunk("CMakePresets.json", 1, 10, "presets"),
        RetrievedChunk(".vscode/mcp.json", 1, 8, "mcp"),
        RetrievedChunk("src/calculator.hpp", 9, 17, "contract"),
        RetrievedChunk("src/calculator.cpp", 24, 47, "parse"),
    ]
    kept = drop_noise_chunks(chunks, "que fait 2+3*4 ?", 5)
    paths = [chunk.file_path for chunk in kept]
    assert paths == ["src/calculator.hpp", "src/calculator.cpp"]
    assert is_noise_path("CMakePresets.json")
    named = drop_noise_chunks(chunks, "montre CMakePresets.json", 5)
    assert any(chunk.file_path == "CMakePresets.json" for chunk in named)
