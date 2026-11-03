"""Unit tests for `scripts/eval_rag.py`.

`main()`'s network call (`call_chat`) is monkeypatched out; these tests
cover the parts with actual logic: fixture loading and the file/symbol
substring matching in `check()`.
"""

from __future__ import annotations

import json

import eval_rag
from eval_rag import GoldenQuestion, check, load_fixture


def test_load_fixture_parses_required_and_optional_fields(tmp_path):
    fixture = tmp_path / "questions.json"
    fixture.write_text(
        json.dumps(
            [
                {"query": "q1", "expected_file_substring": "src/main.cpp"},
                {
                    "query": "q2",
                    "expected_file_substring": "src/foo.cpp",
                    "expected_symbol_substring": "do_thing",
                },
            ]
        ),
        encoding="utf-8",
    )

    questions = load_fixture(fixture)

    assert questions == [
        GoldenQuestion(query="q1", expected_file_substring="src/main.cpp", expected_symbol_substring=None),
        GoldenQuestion(query="q2", expected_file_substring="src/foo.cpp", expected_symbol_substring="do_thing"),
    ]


def test_default_fixture_is_valid_and_nonempty():
    """The real fixture shipped in the repo must actually parse."""
    questions = load_fixture(eval_rag.DEFAULT_FIXTURE)
    assert len(questions) > 0
    for q in questions:
        assert q.query
        assert q.expected_file_substring


def test_check_matches_on_file_substring_alone():
    question = GoldenQuestion(query="q", expected_file_substring="src/main.cpp")
    response = {"sources": [{"file_path": "src/main.cpp", "symbol_name": "main"}]}

    ok, detail = check(question, response)

    assert ok is True
    assert detail == "matched"


def test_check_requires_symbol_substring_when_given():
    question = GoldenQuestion(query="q", expected_file_substring="src/foo.cpp", expected_symbol_substring="bar")
    response = {"sources": [{"file_path": "src/foo.cpp", "symbol_name": "totally_different"}]}

    ok, _detail = check(question, response)

    assert ok is False


def test_check_fails_when_no_source_matches_file():
    question = GoldenQuestion(query="q", expected_file_substring="src/nope.cpp")
    response = {"sources": [{"file_path": "src/other.cpp", "symbol_name": "x"}]}

    ok, detail = check(question, response)

    assert ok is False
    assert "no source matched" in detail


def test_check_handles_missing_sources_key():
    question = GoldenQuestion(query="q", expected_file_substring="src/main.cpp")

    ok, _detail = check(question, {})

    assert ok is False


def test_main_fails_fast_without_bearer_token(monkeypatch, capsys):
    # AGENT_BEARER_TOKEN is read once at import time into a module-level
    # constant, so patch that constant directly rather than the env var
    # (which the already-imported module would never re-read).
    monkeypatch.setattr(eval_rag, "AGENT_BEARER_TOKEN", "")
    monkeypatch.setattr("sys.argv", ["eval_rag.py"])

    assert eval_rag.main() == 1
    assert "AGENT_BEARER_TOKEN" in capsys.readouterr().err


def test_main_returns_zero_when_all_questions_pass(tmp_path, monkeypatch):
    fixture = tmp_path / "questions.json"
    fixture.write_text(
        json.dumps([{"query": "where is main", "expected_file_substring": "src/main.cpp"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(eval_rag, "AGENT_BEARER_TOKEN", "test-token")
    monkeypatch.setattr(
        eval_rag,
        "call_chat",
        lambda query: {"sources": [{"file_path": "src/main.cpp", "symbol_name": "main"}]},
    )
    monkeypatch.setattr("sys.argv", ["eval_rag.py", "--fixture", str(fixture)])

    assert eval_rag.main() == 0


def test_main_returns_one_when_a_question_fails(tmp_path, monkeypatch):
    fixture = tmp_path / "questions.json"
    fixture.write_text(
        json.dumps([{"query": "where is main", "expected_file_substring": "src/main.cpp"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(eval_rag, "AGENT_BEARER_TOKEN", "test-token")
    monkeypatch.setattr(eval_rag, "call_chat", lambda query: {"sources": []})
    monkeypatch.setattr("sys.argv", ["eval_rag.py", "--fixture", str(fixture)])

    assert eval_rag.main() == 1


def test_main_counts_network_errors_as_failures(tmp_path, monkeypatch):
    fixture = tmp_path / "questions.json"
    fixture.write_text(
        json.dumps([{"query": "where is main", "expected_file_substring": "src/main.cpp"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(eval_rag, "AGENT_BEARER_TOKEN", "test-token")

    def _raise(_query):
        raise ConnectionError("agent unreachable")

    monkeypatch.setattr(eval_rag, "call_chat", _raise)
    monkeypatch.setattr("sys.argv", ["eval_rag.py", "--fixture", str(fixture)])

    assert eval_rag.main() == 1
