"""Unit tests for services/trainer/dataset.py (no GPU, no tree-sitter files on disk)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

TRAINER_DIR = Path(__file__).resolve().parents[2] / "services" / "trainer"
INDEXER_DIR = Path(__file__).resolve().parents[2] / "services" / "indexer"
for path in (TRAINER_DIR, INDEXER_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import chunker  # noqa: E402
import dataset  # noqa: E402

SAMPLE_CPP = b"""
int foo() { return 1; }
class Bar {
public:
  void baz() {}
};
"""


def test_examples_for_chunk_explain_and_locate():
    chunks = chunker.chunk_source(SAMPLE_CPP, "src/demo.cpp", Path("/tmp/root"))
    fn_chunk = next(c for c in chunks if c.symbol_name == "foo")
    examples = dataset.examples_for_chunk(fn_chunk)
    kinds = {e.kind for e in examples}
    assert "explain_symbol" in kinds
    assert "locate_symbol" in kinds
    assert all(len(e.messages) == 2 for e in examples)


def test_write_dataset_creates_jsonl_and_manifest(tmp_path):
    src = tmp_path / "app"
    src.mkdir()
    (src / "main.cpp").write_bytes(SAMPLE_CPP)

    out = tmp_path / "training"
    path = dataset.write_dataset(src, out, full=True)

    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 3
    row = json.loads(lines[0])
    assert "messages" in row

    manifest = json.loads((path.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["num_examples"] == len(lines)
    assert manifest["mode"] == "full"
