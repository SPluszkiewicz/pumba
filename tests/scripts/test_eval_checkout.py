"""Scorer for the checkout retrieval baseline. No network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from eval_checkout import Question, load_questions, render_report, score


def _rows(*paths: str) -> list[dict]:
    return [
        {"file_path": path, "symbol_name": path.rsplit("/", 1)[-1].split(".")[0], "score": 1.0}
        for path in paths
    ]


def test_in_the_final_five_counts_as_hit():
    question = Question(
        id="mat",
        family="symbol",
        query="Where is Mat?",
        expected_file_substring="core/mat.hpp",
        expected_symbol_substring="mat",
    )
    retrieval = {
        "candidates": _rows("other.cpp", "core/mat.hpp"),
        "reranked": _rows("core/mat.hpp"),
        "index_hits": _rows("core/mat.hpp"),
    }
    row = score(question, retrieval)
    assert row["in_index_hits"] is True
    assert row["note"] == "in the 5"


def test_prefetch_without_the_five_is_not_a_hit():
    question = Question(
        id="blur",
        family="behavior",
        query="Where is the kernel applied?",
        expected_file_substring="smooth.dispatch.cpp",
    )
    retrieval = {
        "candidates": _rows("smooth.dispatch.cpp", "other.cpp"),
        "reranked": _rows("other.cpp"),
        "index_hits": _rows("other.cpp"),
    }
    row = score(question, retrieval)
    assert row["in_index_hits"] is False
    assert row["in_candidates"] is True
    assert row["note"] == "in the prefetch, not in the 5"


def test_noise_filter_is_its_own_stage():
    question = Question(
        id="presets",
        family="symbol",
        query="Where is Mat?",
        expected_file_substring="mat.hpp",
    )
    retrieval = {
        "candidates": _rows("mat.hpp"),
        "reranked": _rows("mat.hpp"),
        "index_hits": _rows("other.cpp"),
    }
    row = score(question, retrieval)
    assert row["note"] == "dropped by the noise filter"


def test_invented_symbol_must_stay_absent():
    question = Question(
        id="absent",
        family="negative",
        query="Where is Zorglub42?",
        expect_absent_symbol="Zorglub42",
    )
    clean = score(question, {"candidates": _rows("mat.hpp"), "index_hits": _rows("mat.hpp")})
    assert clean["negative_ok"] is True
    leaked = score(
        question,
        {
            "candidates": [{"file_path": "a.cpp", "symbol_name": "Zorglub42"}],
            "index_hits": [],
        },
    )
    assert leaked["negative_ok"] is False


def test_exact_file_does_not_match_a_nested_copy():
    question = Question(
        id="readme",
        family="docs",
        query="Where does the README state the rule?",
        expected_file_substring="README.md",
        expected_file_exact=True,
    )
    nested = score(
        question,
        {"candidates": _rows("3rdparty/protobuf/README.md"), "index_hits": _rows("3rdparty/protobuf/README.md")},
    )
    assert nested["in_index_hits"] is False
    assert nested["note"] == "absent from the prefetch"
    root = score(
        question,
        {"candidates": _rows("README.md"), "index_hits": _rows("README.md")},
    )
    assert root["in_index_hits"] is True


def test_report_counts_each_family():
    text = render_report(
        {
            "checkout": "D:/rest/opencv",
            "revision": "abc",
            "dirty_files": 0,
            "collection": "repo",
            "indexed": True,
            "reranker_used": True,
        },
        [
            {
                "id": "mat",
                "family": "symbol",
                "query": "Where is Mat?",
                "negative": False,
                "negative_ok": False,
                "in_index_hits": True,
                "in_candidates": True,
                "in_reranked": True,
                "note": "in the 5",
            },
            {
                "id": "cmake-macro",
                "family": "cmake",
                "query": "Where is the macro?",
                "negative": False,
                "negative_ok": False,
                "in_index_hits": False,
                "in_candidates": False,
                "in_reranked": False,
                "note": "absent from the prefetch",
            },
        ],
    )
    assert "hit@5: 1/2" in text
    assert "symbol: 1/1 in the 5" in text
    assert "cmake: 0/1 in the 5" in text


def _opencv_questions_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "host" / "eval_opencv_questions.json"


def test_opencv_fixture_loads_and_names_real_families():
    path = _opencv_questions_path()
    questions = load_questions(path)
    families = {item.family for item in questions}
    assert {"symbol", "behavior", "path", "cmake", "docs", "negative"} <= families
    assert len(questions) >= 40
    assert len({item.id for item in questions}) == len(questions)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "opencv" in raw["checkout"].lower()
    exact = [item for item in questions if item.expected_file_exact]
    assert exact and all(item.expected_file_substring == "README.md" for item in exact)


def test_opencv_questions_match_the_local_tree():
    root = next(
        (
            candidate
            for candidate in (Path(r"D:/rest/opencv"), Path("/mnt/d/rest/opencv"))
            if (candidate / "modules" / "core" / "CMakeLists.txt").is_file()
        ),
        None,
    )
    if root is None:
        pytest.skip("local OpenCV checkout is not on this machine")
    for question in load_questions(_opencv_questions_path()):
        rel = question.expected_file_substring
        if not rel:
            continue
        path = root / rel
        assert path.is_file(), question.id
        if question.expected_symbol_substring:
            text = path.read_text(encoding="utf-8", errors="replace")
            assert question.expected_symbol_substring in text, question.id
