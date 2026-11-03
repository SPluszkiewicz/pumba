"""Working-copy overlay. Status text is parsed; no svn or git binary."""

from __future__ import annotations

from checkouts import parse_status_paths
from working_copy import merge_hits


def test_svn_status_keeps_modified_sources_and_skips_deletes() -> None:
    stdout = "\n".join(
        [
            "M       src/calculator.cpp",
            "?       src/new.hpp",
            "D       src/gone.cpp",
            " M      README.md",
            "?       notes.txt",
        ]
    )
    assert parse_status_paths("svn", stdout) == ["src/calculator.cpp", "src/new.hpp"]


def test_git_status_uses_the_new_name_of_a_rename() -> None:
    stdout = " M src/calculator.cpp\nR  src/old.cpp -> src/renamed.cpp\nD  src/gone.cpp\n"
    assert parse_status_paths("git", stdout) == ["src/calculator.cpp", "src/renamed.cpp"]


def test_merge_puts_the_working_copy_ahead_of_the_index() -> None:
    hits = [
        {
            "file_path": "src/calculator.cpp",
            "start_line": 10,
            "end_line": 12,
            "symbol_name": "Evaluate",
            "excerpt": "stale",
            "source": "index",
        },
        {
            "file_path": "src/other.cpp",
            "start_line": 1,
            "end_line": 2,
            "symbol_name": "Other",
            "excerpt": "indexed",
            "source": "index",
        },
    ]
    merged = merge_hits(hits, {"src/calculator.cpp": "int live();\n", "src/new.hpp": "class New {};\n"})
    assert merged[0]["file_path"] == "src/calculator.cpp"
    assert merged[0]["source"] == "working_copy"
    assert merged[0]["excerpt"] == "int live();"
    assert merged[1]["file_path"] == "src/new.hpp"
    assert merged[1]["source"] == "working_copy"
    assert merged[2]["file_path"] == "src/other.cpp"
    assert merged[2]["source"] == "index"
