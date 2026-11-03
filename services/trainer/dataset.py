"""Build instruction-tuning JSONL from C++ chunks for QLoRA training.

Reuses the indexer's tree-sitter chunker - no duplicate parsing logic.
Examples are deterministic (no LLM required): explain/locate/describe tasks.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

# Import chunker from the indexer service (same pattern as tests/indexer).
_INDEXER_DIR = Path(__file__).resolve().parent.parent / "indexer"
if str(_INDEXER_DIR) not in sys.path:
    sys.path.insert(0, str(_INDEXER_DIR))

import chunker  # noqa: E402


@dataclass(frozen=True)
class DatasetExample:
    kind: str
    messages: list[dict[str, str]]


def _git_run(source_root: Path, *args: str) -> Optional[str]:
    if shutil.which("git") is None:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(source_root), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _is_git_worktree(source_root: Path) -> bool:
    return _git_run(source_root, "rev-parse", "--is-inside-work-tree") == "true"


def resolve_commit_sha(source_root: Path) -> str:
    """Return git HEAD sha, or a stable fallback when not a git repo."""
    sha = _git_run(source_root, "rev-parse", "HEAD")
    if sha:
        return sha
    return "no-git"


def changed_source_files_since(source_root: Path, since_commit: str) -> list[Path]:
    """Files changed between since_commit and HEAD (C++ sources only)."""
    if since_commit in ("", "no-git"):
        return []
    diff_out = _git_run(source_root, "diff", "--name-only", since_commit, "HEAD")
    if not diff_out:
        return []
    paths: list[Path] = []
    for line in diff_out.splitlines():
        rel = line.strip()
        if not rel:
            continue
        path = source_root / rel
        if path.is_file() and chunker.is_cpp_file(path):
            paths.append(path)
    return sorted(paths)


def _iter_cpp_files(source_root: Path) -> Iterator[Path]:
    # The indexer also chunks CMake/Markdown/YAML (text_chunker); the dataset
    # templates below are about functions/classes, so keep the trainer C++-only.
    for path in chunker.iter_source_files(source_root):
        if chunker.is_cpp_file(path):
            yield path


def iter_files_for_build(
    source_root: Path,
    *,
    full: bool,
    since_commit: Optional[str] = None,
) -> Iterator[Path]:
    if full or not since_commit or since_commit == "no-git":
        yield from _iter_cpp_files(source_root)
        return
    changed = changed_source_files_since(source_root, since_commit)
    if changed:
        yield from changed
        return
    # No diff (same commit or empty diff) - fall back to full tree for safety.
    yield from _iter_cpp_files(source_root)


def examples_for_chunk(chunk: chunker.Chunk) -> list[DatasetExample]:
    """Three deterministic chat examples per chunk."""
    symbol = chunk.symbol_name
    loc = f"{chunk.file_path}:{chunk.start_line}-{chunk.end_line}"
    excerpt = chunk.text.strip()
    if len(excerpt) > 4000:
        excerpt = excerpt[:4000] + "\n// ... truncated ..."

    out: list[DatasetExample] = []

    if symbol != chunker.FILE_LEVEL_SYMBOL:
        out.append(
            DatasetExample(
                kind="explain_symbol",
                messages=[
                    {
                        "role": "user",
                        "content": f"Explique le symbole `{symbol}` dans `{chunk.file_path}`.",
                    },
                    {
                        "role": "assistant",
                        "content": (
                            f"Le symbole `{symbol}` est défini dans {loc}.\n\n"
                            f"```{chunk.language}\n{excerpt}\n```"
                        ),
                    },
                ],
            )
        )
        out.append(
            DatasetExample(
                kind="locate_symbol",
                messages=[
                    {
                        "role": "user",
                        "content": f"Où est défini `{symbol}` ?",
                    },
                    {
                        "role": "assistant",
                        "content": f"`{symbol}` est dans {loc}.\n\n```{chunk.language}\n{excerpt}\n```",
                    },
                ],
            )
        )

    return out


def examples_for_file(chunks: list[chunker.Chunk]) -> list[DatasetExample]:
    if not chunks:
        return []
    file_path = chunks[0].file_path
    symbols = sorted(
        {c.symbol_name for c in chunks if c.symbol_name != chunker.FILE_LEVEL_SYMBOL}
    )
    if symbols:
        symbol_list = ", ".join(f"`{s}`" for s in symbols)
        body = f"Le fichier `{file_path}` contient : {symbol_list}."
    else:
        body = f"Le fichier `{file_path}` ne contient pas de fonctions/classes chunkables (contenu fichier entier)."
    return [
        DatasetExample(
            kind="describe_file",
            messages=[
                {"role": "user", "content": f"Que contient `{file_path}` ?"},
                {"role": "assistant", "content": body},
            ],
        )
    ]


def build_examples(
    source_root: Path,
    *,
    full: bool = True,
    since_commit: Optional[str] = None,
) -> list[DatasetExample]:
    source_root = source_root.resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(f"source root does not exist: {source_root}")

    examples: list[DatasetExample] = []
    file_chunks: dict[str, list[chunker.Chunk]] = defaultdict(list)

    for path in iter_files_for_build(source_root, full=full, since_commit=since_commit):
        for chunk in chunker.chunk_file(path, source_root):
            file_chunks[chunk.file_path].append(chunk)
            examples.extend(examples_for_chunk(chunk))

    for chunks in file_chunks.values():
        examples.extend(examples_for_file(chunks))

    return examples


def write_dataset(
    source_root: Path,
    output_dir: Path,
    *,
    full: bool = True,
    since_commit: Optional[str] = None,
) -> Path:
    """Write dataset.jsonl + manifest.json; returns path to dataset.jsonl."""
    commit_sha = resolve_commit_sha(source_root)
    examples = build_examples(source_root, full=full, since_commit=since_commit)

    run_dir = output_dir / commit_sha
    run_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = run_dir / "dataset.jsonl"

    with dataset_path.open("w", encoding="utf-8") as fh:
        for ex in examples:
            fh.write(json.dumps({"messages": ex.messages, "kind": ex.kind}, ensure_ascii=False))
            fh.write("\n")

    manifest = {
        "commit_sha": commit_sha,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "num_examples": len(examples),
        "dataset_path": str(dataset_path),
        "source_root": str(source_root),
        "mode": "full" if full else "incremental",
        "since_commit": since_commit,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return dataset_path
