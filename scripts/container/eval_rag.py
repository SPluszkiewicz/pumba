#!/usr/bin/env python3
"""Golden-question retrieval check for the RAG pipeline.

Runs only INSIDE a container that reaches `agent` and has AGENT_BEARER_TOKEN:
the `smoke-test` service (`podman compose --profile test run --rm smoke-test
python scripts/container/eval_rag.py`). `devtools` has neither. It needs an
already-running, already-indexed stack (`podman compose up` + `podman compose
--profile index run --rm indexer` must have happened first).

This deliberately checks RETRIEVAL quality (did the expected file/symbol show
up in `sources`), not exact free-text answer correctness - LLM output is not
byte-stable, but "did we retrieve the right chunk" is a meaningful,
checkable signal and is what actually breaks when someone changes the
chunker, the embedding prefixing, or the Qdrant schema.

Exit code is non-zero if any golden question fails to retrieve its expected
source, so this can be wired into CI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

AGENT_BASE_URL = os.environ.get("AGENT_BASE_URL", "http://agent:8090")
AGENT_BEARER_TOKEN = os.environ.get("AGENT_BEARER_TOKEN", "")

DEFAULT_FIXTURE = Path(__file__).parent / "eval_rag_questions.json"


@dataclass(frozen=True)
class GoldenQuestion:
    query: str
    # A source is considered a match if `expected_file_substring` is a
    # substring of the returned source's file_path AND (if given)
    # `expected_symbol_substring` is a substring of its symbol_name.
    expected_file_substring: str
    expected_symbol_substring: str | None = None


def load_fixture(path: Path) -> list[GoldenQuestion]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        GoldenQuestion(
            query=item["query"],
            expected_file_substring=item["expected_file_substring"],
            expected_symbol_substring=item.get("expected_symbol_substring"),
        )
        for item in raw
    ]


def call_chat(query: str) -> dict:
    # Retrieval is what this check scores. Leaving tools on lets the tiny CI
    # model start a cmake build, which holds a /chat slot until the client
    # gives up and the next question gets 503.
    body = json.dumps({"query": query, "tools": False}).encode("utf-8")
    request = urllib.request.Request(
        f"{AGENT_BASE_URL}/chat",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {AGENT_BEARER_TOKEN}",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def check(question: GoldenQuestion, response: dict) -> tuple[bool, str]:
    sources = response.get("sources", [])
    for source in sources:
        file_ok = question.expected_file_substring in source.get("file_path", "")
        symbol_ok = (
            question.expected_symbol_substring is None
            or question.expected_symbol_substring in source.get("symbol_name", "")
        )
        if file_ok and symbol_ok:
            return True, "matched"
    return False, (
        f"no source matched file~='{question.expected_file_substring}' "
        f"symbol~='{question.expected_symbol_substring}' (got: {sources!r})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    args = parser.parse_args()

    if not AGENT_BEARER_TOKEN:
        print("[eval_rag] FATAL: AGENT_BEARER_TOKEN is not set in the environment", file=sys.stderr)
        return 1

    questions = load_fixture(args.fixture)
    failures = 0

    for question in questions:
        try:
            response = call_chat(question.query)
        except Exception as exc:  # noqa: BLE001 - report and continue to the next question
            print(f"[eval_rag] ERROR calling /chat for '{question.query}': {exc}", file=sys.stderr)
            failures += 1
            continue

        ok, detail = check(question, response)
        status = "PASS" if ok else "FAIL"
        print(f"[eval_rag] {status}: '{question.query}' -> {detail}")
        if not ok:
            failures += 1

    print(f"[eval_rag] {len(questions) - failures}/{len(questions)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
