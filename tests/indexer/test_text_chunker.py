"""Tests for services/indexer/text_chunker.py (non-C++ chunking via
tree-sitter-language-pack grammars + this module's packing/windowing policy)."""

from __future__ import annotations

from pathlib import Path

import pytest
import text_chunker

BLOB = "0" * 40
FENCE = "```"


def _chunk(text: str, language: str, rel_path: str = "doc.md"):
    return text_chunker.chunk_text_source(text.encode("utf-8"), rel_path, language, BLOB)


class TestLanguageDetection:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("README.md", "markdown"),
            ("CMakeLists.txt", "cmake"),
            ("cmake/Deps.cmake", "cmake"),
            ("CMakePresets.json", "json"),
            ("compose.yaml", "yaml"),
            (".clang-tidy", "yaml"),
            ("notes.txt", "text"),
            ("pyproject.toml", "toml"),
            ("Containerfile", "containerfile"),
            ("run.sh", "shell"),
        ],
    )
    def test_supported(self, name, expected):
        assert text_chunker.language_for(Path(name)) == expected

    @pytest.mark.parametrize("name", ["main.cpp", "logo.png", "archive.zip", "a.o", "Makefile"])
    def test_unsupported(self, name):
        assert text_chunker.language_for(Path(name)) is None


class TestMarkdown:
    def test_one_chunk_per_heading_with_heading_as_symbol(self):
        src = "# Title\nintro\n\n## Build\nrun cmake\n\n## Test\nrun ctest\n"
        chunks = _chunk(src, "markdown")
        assert [c.symbol_name for c in chunks] == ["Title", "Build", "Test"]
        assert [(c.start_line, c.end_line) for c in chunks] == [(1, 3), (4, 6), (7, 8)]
        assert chunks[1].text == "## Build\nrun cmake\n\n"
        assert all(c.language == "markdown" and c.file_path == "doc.md" for c in chunks)

    def test_parent_section_chunk_excludes_child_sections(self):
        src = "# A\na-text\n## B\nb-text\n### C\nc-text\n## D\nd-text\n"
        chunks = _chunk(src, "markdown")
        assert [(c.symbol_name, c.start_line, c.end_line) for c in chunks] == [
            ("A", 1, 2),
            ("B", 3, 4),
            ("C", 5, 6),
            ("D", 7, 8),
        ]

    def test_preamble_before_first_heading_is_kept(self):
        chunks = _chunk("badge line\n\n# Title\nbody\n", "markdown")
        assert chunks[0].symbol_name == text_chunker.PREAMBLE_SYMBOL
        assert (chunks[0].start_line, chunks[0].end_line) == (1, 2)
        assert chunks[1].symbol_name == "Title"
        assert chunks[1].start_line == 3

    def test_setext_heading_is_recognised(self):
        chunks = _chunk("Title\n=====\ntext\n\nSub\n---\nmore\n", "markdown")
        assert [(c.symbol_name, c.start_line, c.end_line) for c in chunks] == [("Title", 1, 4), ("Sub", 5, 7)]

    def test_hash_inside_fenced_code_is_not_a_heading(self):
        src = f"# Real\n{FENCE}bash\n# not a heading\necho hi\n{FENCE}\ntext\n"
        chunks = _chunk(src, "markdown")
        assert [c.symbol_name for c in chunks] == ["Real"]
        assert chunks[0].end_line == 6

    def test_no_heading_falls_back_to_file_level(self):
        chunks = _chunk("just prose\nmore prose\n", "markdown")
        assert len(chunks) == 1
        assert chunks[0].symbol_name == text_chunker.PREAMBLE_SYMBOL

    def test_long_section_is_windowed_with_absolute_line_numbers(self):
        body = "".join(f"line {i}\n" for i in range(1, 200))
        chunks = _chunk("# Big\n" + body, "markdown")
        assert len(chunks) > 1
        assert chunks[0].start_line == 1
        assert chunks[-1].end_line == 200
        assert all(c.symbol_name.startswith("Big <lines ") for c in chunks)
        for prev, nxt in zip(chunks, chunks[1:], strict=False):
            assert prev.start_line < nxt.start_line <= prev.end_line  # overlapping windows

    def test_empty_binary_or_oversized_returns_nothing(self):
        assert _chunk("", "markdown") == []
        assert _chunk("  \n\n", "markdown") == []
        assert text_chunker.chunk_text_source(b"abc\x00def", "x.txt", "text", BLOB) == []
        big = b"a" * (text_chunker.MAX_TEXT_FILE_BYTES + 1)
        assert text_chunker.chunk_text_source(big, "x.txt", "text", BLOB) == []


class TestCMake:
    SRC = """cmake_minimum_required(VERSION 3.20)
project(pumba_app LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)

add_executable(app src/main.cpp src/AgentClient.cpp)
target_link_libraries(app PRIVATE CURL::libcurl)

add_executable(agent_client_test tests/agent_client_test.cpp)
add_test(NAME agent_client_test COMMAND agent_client_test)
"""

    def test_small_commands_are_packed_and_named_after_naming_commands(self):
        chunks = _chunk(self.SRC, "cmake", "CMakeLists.txt")
        assert len(chunks) == 1
        assert chunks[0].language == "cmake"
        assert (chunks[0].start_line, chunks[0].end_line) == (1, 10)
        # project / add_executable / add_executable / add_test names, capped at 3 + ellipsis.
        assert chunks[0].symbol_name == "pumba_app, app, agent_client_test, …"

    def test_commands_split_when_window_is_exceeded(self):
        body = "".join(f"set(V{i} {i})\n" for i in range(19))
        src = "\n".join(f"add_library(lib{n} STATIC a.cpp)\n{body}" for n in range(5))
        chunks = _chunk(src, "cmake", "CMakeLists.txt")
        assert len(chunks) >= 2
        assert chunks[0].symbol_name.startswith("lib0")
        assert chunks[0].start_line == 1
        assert chunks[-1].end_line == src.count("\n")
        for prev, nxt in zip(chunks, chunks[1:], strict=False):
            assert nxt.start_line > prev.end_line

    def test_commands_without_target_get_positional_symbol(self):
        chunks = _chunk("set(A 1)\nset(B 2)\n", "cmake", "x.cmake")
        assert chunks[0].symbol_name == "<block 1>"

    def test_function_macro_and_if_blocks(self):
        src = (
            "function(my_helper ARG)\n  message(${ARG})\nendfunction()\n"
            "macro(my_macro)\nendmacro()\nif(WIN32)\n  set(A 1)\nendif()\n"
        )
        chunks = _chunk(src, "cmake", "h.cmake")
        assert len(chunks) == 1
        assert chunks[0].symbol_name == "my_helper, my_macro"
        assert chunks[0].end_line == 8

    def test_quoted_target_name_is_unquoted(self):
        assert _chunk('add_executable("quoted" a.cpp)\n', "cmake", "x.cmake")[0].symbol_name == "quoted"


class TestYamlJsonToml:
    def test_yaml_top_level_keys_become_symbols(self):
        src = "services:\n  llm:\n    image: x\n  agent:\n    image: y\nvolumes:\n  models: {}\n"
        chunks = _chunk(src, "yaml", "compose.yaml")
        assert len(chunks) == 1  # packed: whole file fits in one window
        assert chunks[0].symbol_name == "services, volumes"
        assert (chunks[0].start_line, chunks[0].end_line) == (1, 7)

    def test_yaml_large_key_is_windowed_under_its_own_name(self):
        big = "services:\n" + "".join(f"  svc{i}:\n    image: img{i}\n" for i in range(80))
        chunks = _chunk(big + "volumes: {}\n", "yaml", "compose.yaml")
        assert all(c.symbol_name.startswith("services <lines ") for c in chunks[:-1])
        assert chunks[-1].symbol_name == "volumes"
        assert chunks[-1].start_line == 162

    def test_yaml_multi_document(self):
        chunks = _chunk("a: 1\n---\nb: 2\n", "yaml", "multi.yaml")
        assert chunks[0].symbol_name == "a, b"

    def test_yaml_bare_list_is_one_positional_block(self):
        chunks = _chunk("- one\n- two\n", "yaml", "list.yaml")
        assert len(chunks) == 1
        assert chunks[0].symbol_name == "<block 1>"
        assert (chunks[0].start_line, chunks[0].end_line) == (1, 2)

    def test_json_top_level_keys(self):
        src = '{\n  "version": 3,\n  "configurePresets": [\n    {"name": "a"}\n  ]\n}\n'
        chunks = _chunk(src, "json", "CMakePresets.json")
        assert chunks[0].symbol_name == "version, configurePresets"
        assert (chunks[0].start_line, chunks[0].end_line) == (2, 5)

    def test_toml_tables_and_pairs(self):
        src = 'x = 1\n[tool.ruff]\nline-length = 120\n[tool.mypy]\nstrict = true\n[[tool.arr]]\nk = "v"\n'
        chunks = _chunk(src, "toml", "pyproject.toml")
        assert chunks[0].symbol_name == "x, tool.ruff, tool.mypy, …"
        assert chunks[0].end_line == 7


class TestWindowedFallback:
    def test_short_file_is_single_file_level_chunk(self):
        chunks = _chunk("[core]\nautocrlf = false\n", "ini", ".gitconfig")
        assert len(chunks) == 1
        assert chunks[0].symbol_name == text_chunker.FILE_LEVEL_SYMBOL
        assert chunks[0].language == "ini"
        assert chunks[0].text == "[core]\nautocrlf = false\n"

    def test_long_file_uses_overlapping_windows(self):
        src = "".join(f"echo {i}\n" for i in range(150))
        chunks = _chunk(src, "shell", "run.sh")
        assert chunks[0].symbol_name == "<lines 1-60>"
        assert chunks[0].end_line == text_chunker.WINDOW_LINES
        step = text_chunker.WINDOW_LINES - text_chunker.WINDOW_OVERLAP
        assert chunks[1].start_line == 1 + step
        assert chunks[-1].end_line == 150
        covered: set[int] = set()
        for c in chunks:
            covered.update(range(c.start_line, c.end_line + 1))
        assert covered == set(range(1, 151))

    def test_dense_short_file_is_split_on_characters(self):
        line = "k=" + ("v" * 200) + "\n"
        src = line * 30
        chunks = _chunk(src, "ini", "big.ini")
        assert len(chunks) > 1
        assert all(len(c.text) <= text_chunker.MAX_EMBED_CHARS for c in chunks)

    def test_windows_helper_edges(self):
        assert text_chunker._windows(0) == []
        assert text_chunker._windows(1) == [(1, 1)]
        assert text_chunker._windows(60) == [(1, 60)]
        assert text_chunker._windows(61) == [(1, 60), (51, 61)]
