#!/usr/bin/env python3
"""Batch indexing job: walk a C++ source tree into embedded chunks in Qdrant.

One-shot mode (default, no flags) is the primary supported path: walk
`INDEXER_SOURCE_ROOT` once, chunk every `.cpp`/`.hpp`/`.h`/`.cc` file at
function/class/method granularity, embed each chunk via the `embedder`
service, and upsert into the `code_chunks` Qdrant collection. This is exactly
what the `index` compose profile runs (`podman compose --profile index run
indexer`), and it exits when done.

`--watch` is best-effort and Linux-preferred. It uses the `watchdog` package,
which relies on inotify on Linux. Through the
Windows host -> Podman machine -> WSL2 -> bind-mount path, inotify events do
not propagate reliably (or at all, depending on the Podman machine's mount
backend), so edits made from a Windows editor may never trigger a re-index
while running that stack. Don't depend on `--watch` there - re-run the
one-shot batch job instead. On native Linux hosts (bind-mounting a real
filesystem, no VM hop), `--watch` works as expected.

Every line written to stdout is a single JSON object (structured logging via
`structlog`, see services/common/logging.py: one line per file processed plus
a final summary line).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional

import chunker
import embeddings_client
import vector_store
from common.logging import get_logger

DEFAULT_SOURCE_ROOT = "/workspace/app"


log = get_logger("indexer")


def _log(event: str, **fields) -> None:
    (log.error if event in ("file_error", "fatal") else log.info)(event, **fields)


def process_file(
    path: Path,
    root: Path,
    store: vector_store.VectorStore,
    *,
    known_hash: str | None = None,
    shared_blobs: set[tuple[str, str]] | None = None,
) -> tuple[int, str, str]:
    """Chunk, embed, and upsert a single file.

    Returns `(chunk_count, outcome, blob_hash)` where outcome is `indexed`,
    `skipped` (already linked to this checkout), `attach` (same blob already
    embedded by another checkout — the caller batches the tag write), or
    `empty` (no chunks). A changed file detaches this checkout from its
    previous points first so renamed/moved symbols do not leave orphans.
    """
    rel_path = path.relative_to(root).as_posix()
    current_hash = chunker.compute_blob_hash(path.read_bytes(), root)
    if known_hash is not None and known_hash == current_hash:
        return 0, "skipped", current_hash

    if known_hash is not None:
        store.delete_by_file_paths([rel_path])

    if shared_blobs is not None and (rel_path, current_hash) in shared_blobs:
        return 0, "attach", current_hash

    chunks = chunker.chunk_file(path, root)
    if not chunks:
        return 0, "empty", current_hash
    vectors = embeddings_client.embed_texts([c.text for c in chunks])
    return store.upsert_chunks(chunks, vectors), "indexed", current_hash


def index_source_root(root: Path, store: vector_store.VectorStore) -> dict:
    """One full batch pass over `root`. Returns summary stats.

    Unchanged files (same `git_blob_hash` as already stored) are skipped.
    Blobs already embedded by another branch/checkout are linked without
    re-embedding. Files present for this checkout in the collection but
    absent from this walk are detached/deleted at the end.
    """
    snap = store.snapshot()
    existing = snap.checkout_hashes
    shared_blobs = snap.blobs
    seen: set[str] = set()
    to_attach: list[tuple[str, str]] = []
    stats = {
        "files_scanned": 0,
        "files_indexed": 0,
        "files_skipped": 0,
        "files_deleted": 0,
        "chunks_indexed": 0,
        "errors": 0,
    }

    for path in chunker.iter_source_files(root):
        stats["files_scanned"] += 1
        rel_path = path.relative_to(root).as_posix()
        seen.add(rel_path)
        try:
            chunk_count, outcome, blob_hash = process_file(
                path,
                root,
                store,
                known_hash=existing.get(rel_path),
                shared_blobs=shared_blobs,
            )
        except embeddings_client.EmbedderError as exc:
            stats["errors"] += 1
            _log("file_error", file=rel_path, error=str(exc))
            continue
        except Exception as exc:  # noqa: BLE001 - one bad file must not abort the batch
            stats["errors"] += 1
            _log("file_error", file=rel_path, error=repr(exc))
            continue

        if outcome in ("skipped", "attach"):
            if outcome == "attach":
                to_attach.append((rel_path, blob_hash))
            stats["files_skipped"] += 1
            _log("file_skipped", file=rel_path, reason="git_blob_hash")
            continue
        if chunk_count:
            stats["files_indexed"] += 1
            stats["chunks_indexed"] += chunk_count
        _log("file_processed", file=rel_path, chunks=chunk_count)

    if to_attach:
        store.attach_checkouts(to_attach, snapshot=snap)

    stale = [path for path in existing if path not in seen]
    if stale:
        stats["files_deleted"] = store.delete_by_file_paths(stale)
        _log("files_tombstoned", count=stats["files_deleted"])

    _log("summary", **stats)
    return stats


def _run_watch(root: Path, store: vector_store.VectorStore) -> None:
    # Imported lazily so the one-shot path (the primary supported mode) never
    # pays for importing watchdog if it isn't installed/needed.
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    class _Handler(FileSystemEventHandler):
        def _handle(self, src_path: str) -> None:
            path = Path(src_path)
            if not chunker.is_source_file(path):
                return
            rel_path = path.relative_to(root).as_posix() if path.is_absolute() else src_path
            try:
                snap = store.snapshot()
                chunk_count, outcome, blob_hash = process_file(
                    path,
                    root,
                    store,
                    known_hash=snap.checkout_hashes.get(rel_path),
                    shared_blobs=snap.blobs,
                )
                if outcome == "attach":
                    store.attach_checkouts([(rel_path, blob_hash)], snapshot=snap)
                if outcome in ("skipped", "attach"):
                    _log("file_skipped", file=rel_path, reason="git_blob_hash", trigger="watch")
                else:
                    _log("file_processed", file=rel_path, chunks=chunk_count, trigger="watch")
            except Exception as exc:  # noqa: BLE001 - keep the watcher alive
                _log("file_error", file=rel_path, error=repr(exc), trigger="watch")

        def on_created(self, event) -> None:
            if not event.is_directory:
                self._handle(event.src_path)

        def on_modified(self, event) -> None:
            if not event.is_directory:
                self._handle(event.src_path)

        def on_deleted(self, event) -> None:
            if event.is_directory:
                return
            path = Path(event.src_path)
            rel_path = path.as_posix()
            try:
                rel_path = path.relative_to(root).as_posix()
            except ValueError:
                pass
            store.delete_by_file_paths([rel_path])
            _log("file_tombstoned", file=rel_path, trigger="watch")

    _log(
        "watch_started",
        root=str(root),
        note="best-effort, Linux-preferred (see --help)",
    )
    observer = Observer()
    observer.schedule(_Handler(), str(root), recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source-root",
        default=None,
        help=f"Override INDEXER_SOURCE_ROOT (default env var, falling back to {DEFAULT_SOURCE_ROOT}).",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help=(
            "Best-effort, Linux-preferred: keep running and re-index files as they "
            "change (via `watchdog`/inotify). Not reliable through the Windows -> "
            "Podman-machine -> WSL2 bind-mount path. The default one-shot batch mode "
            "(no flag) is the primary supported path."
        ),
    )
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    root_str = args.source_root or os.environ.get("INDEXER_SOURCE_ROOT", DEFAULT_SOURCE_ROOT)
    root = Path(root_str)
    if not root.is_dir():
        _log("fatal", error=f"INDEXER_SOURCE_ROOT does not exist or is not a directory: {root}")
        return 1

    store = vector_store.VectorStore()
    store.ensure_collection()

    if args.watch:
        _run_watch(root, store)
        return 0

    stats = index_source_root(root, store)
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
