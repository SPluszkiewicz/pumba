"""Tests for services/common/logging.py (structlog configuration)."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.common import logging as common_logging  # noqa: E402


@pytest.fixture()
def stream() -> io.StringIO:
    buf = io.StringIO()
    common_logging.configure(level="INFO", stream=buf)
    common_logging.clear_request_context()
    yield buf
    common_logging.clear_request_context()
    common_logging.configure(level="INFO")  # back to stdout for other tests


def _lines(buf: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def test_one_json_object_per_line_with_event_component_level_timestamp(stream: io.StringIO) -> None:
    log = common_logging.get_logger("rag")
    log.info("qdrant_search_degraded", reason="collection_missing")

    (record,) = _lines(stream)
    assert record["event"] == "qdrant_search_degraded"
    assert record["component"] == "rag"
    assert record["reason"] == "collection_missing"
    assert record["level"] == "info"
    assert record["timestamp"].endswith("Z")


def test_request_id_bound_in_context_appears_on_every_line(stream: io.StringIO) -> None:
    common_logging.bind_request_id("req-42")
    common_logging.get_logger("a").info("first")
    common_logging.get_logger("b").info("second", n=2)

    records = _lines(stream)
    assert [r["request_id"] for r in records] == ["req-42", "req-42"]
    assert [r["component"] for r in records] == ["a", "b"]


def test_clear_request_context_drops_request_id(stream: io.StringIO) -> None:
    common_logging.bind_request_id("req-1")
    common_logging.clear_request_context()
    common_logging.get_logger("x").info("after_clear")
    (record,) = _lines(stream)
    assert "request_id" not in record


def test_explicit_request_id_kwarg_wins_over_context(stream: io.StringIO) -> None:
    # rag/tool_loop pass request_id explicitly; it must not be shadowed.
    common_logging.bind_request_id("ctx")
    common_logging.get_logger("x").info("evt", request_id="explicit")
    (record,) = _lines(stream)
    assert record["request_id"] == "explicit"


def test_level_filtering() -> None:
    buf = io.StringIO()
    common_logging.configure(level="WARNING", stream=buf)
    try:
        log = common_logging.get_logger("x")
        log.info("hidden")
        log.error("shown", detail="boom")
        (record,) = _lines(buf)
        assert record["event"] == "shown" and record["level"] == "error"
    finally:
        common_logging.configure(level="INFO")


def test_unknown_level_falls_back_to_info() -> None:
    buf = io.StringIO()
    common_logging.configure(level="NOT_A_LEVEL", stream=buf)
    try:
        common_logging.get_logger("x").info("still_logged")
        assert _lines(buf)[0]["event"] == "still_logged"
    finally:
        common_logging.configure(level="INFO")
