"""Repo-map ranking on a tiny tree. No tree-sitter required."""

from __future__ import annotations

from repo_map import (
    build_repo_map,
    clear_repo_map_cache,
    iter_source_files,
    regex_signatures,
    render_repo_map,
    wants_repo_map,
)


def test_wants_repo_map_matches_shape_questions() -> None:
    assert wants_repo_map("Comment est organisé ce dépôt ?")
    assert wants_repo_map("Give an overview of the architecture")
    assert not wants_repo_map("Que déclare src/calculator.hpp ?")


def test_rank_puts_the_mentioned_type_first(tmp_path) -> None:
    (tmp_path / "calc.hpp").write_text("class Calculator {\npublic:\n  int add(int, int);\n};\n", encoding="utf-8")
    (tmp_path / "use.cpp").write_text("void use() {\n  Calculator c;\n}\n", encoding="utf-8")
    (tmp_path / "main.cpp").write_text("int main() {\n  Calculator c;\n}\n", encoding="utf-8")
    text = render_repo_map(tmp_path, regex_signatures(tmp_path), budget_chars=400)
    first = text.splitlines()[0]
    assert first.startswith("calc.hpp:")
    assert "Calculator" in first
    assert "(2)" in first


def test_second_build_reuses_the_cached_sketch(tmp_path, monkeypatch) -> None:
    header = tmp_path / "calc.hpp"
    header.write_text("class Calculator {};\n", encoding="utf-8")
    clear_repo_map_cache()
    calls = {"n": 0}

    def fake(root):
        calls["n"] += 1
        return regex_signatures(root)

    monkeypatch.setattr("repo_map.collect_signatures", fake)
    first = build_repo_map(tmp_path)
    second = build_repo_map(tmp_path)
    assert first == second
    assert calls["n"] == 1
    header.write_text("class Calculator {\npublic:\n};\n", encoding="utf-8")
    build_repo_map(tmp_path)
    assert calls["n"] == 2


def test_walk_includes_every_source_file(tmp_path) -> None:
    for index in range(40):
        folder = tmp_path / f"mod{index}"
        folder.mkdir()
        (folder / "unit.cpp").write_text("void unit() {}\n", encoding="utf-8")
    assert len(iter_source_files(tmp_path)) == 40


def test_a_project_type_outranks_a_common_function_name(tmp_path) -> None:
    (tmp_path / "engine.hpp").write_text("class Engine {};\n", encoding="utf-8")
    (tmp_path / "use.cpp").write_text("Engine e;\n", encoding="utf-8")
    (tmp_path / "call.cpp").write_text("void run() { get(); }\n", encoding="utf-8")
    for index in range(6):
        (tmp_path / f"f{index}.cpp").write_text("int get() { return 0; }\n", encoding="utf-8")
    text = render_repo_map(tmp_path, regex_signatures(tmp_path), budget_chars=80)
    assert text.splitlines()[0].startswith("engine.hpp:")
