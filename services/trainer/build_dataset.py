#!/usr/bin/env python3
"""CLI: build QLoRA training JSONL from CODEBASE_PATH / TRAIN_SOURCE_ROOT."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dataset import resolve_commit_sha, write_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        default=os.environ.get("TRAIN_SOURCE_ROOT", "/workspace/app"),
        help="C++ project root (default: TRAIN_SOURCE_ROOT or /workspace/app)",
    )
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("TRAIN_DATASET_OUT", "/models/training"),
        help="Parent dir for models/training/{commit_sha}/ (default: TRAIN_DATASET_OUT)",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Only files changed since --since-commit (or last_trained_commit from manifest)",
    )
    parser.add_argument(
        "--since-commit",
        default="",
        help="Git commit to diff from (incremental mode)",
    )
    args = parser.parse_args()

    source_root = Path(args.source_root)
    output_dir = Path(args.output_dir)
    since_commit = args.since_commit or None

    if args.incremental and not since_commit:
        latest_manifest = Path(os.environ.get("TRAIN_ADAPTER_OUT", "/models/adapters")) / "latest" / "manifest.json"
        if latest_manifest.exists():
            try:
                since_commit = json.loads(latest_manifest.read_text(encoding="utf-8")).get("commit_sha")
            except (json.JSONDecodeError, OSError):
                since_commit = None

    full = not args.incremental
    try:
        dataset_path = write_dataset(
            source_root,
            output_dir,
            full=full,
            since_commit=since_commit,
        )
    except FileNotFoundError as exc:
        print(f"[build_dataset] FATAL: {exc}", file=sys.stderr)
        return 1

    commit_sha = resolve_commit_sha(source_root)
    print(
        json.dumps(
            {
                "event": "dataset_built",
                "commit_sha": commit_sha,
                "dataset_path": str(dataset_path),
                "mode": "full" if full else "incremental",
                "since_commit": since_commit,
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
