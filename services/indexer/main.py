#!/usr/bin/env python3
"""Batch indexing job: walk a C++ source tree into embedded chunks in Qdrant.

One-shot mode (default, no flags) is the primary supported path: walk
`INDEXER_SOURCE_ROOT` once, chunk every `.cpp`/`.hpp`/`.h`/`.cc` file at
function/class/method granularity, embed each chunk via the `embedder`
service, and upsert into the `code_chunks` Qdrant collection. This is exactly
what the `index` compose profile runs (`podman compose --profile index run
indexer`), and it exits when done.

`--watch` is best-effort and Linux-preferred: it runs the same full batch
first, then listens with a 0.5s debounce so a save burst is one upsert. It
uses the `watchdog` package,
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
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import chunker
import embeddings_client
import vector_store
from common.logging import get_logger

DEFAULT_SOURCE_ROOT = "/workspace/app"
WATCH_DEBOUNCE_SECONDS = 0.5


log = get_logger("indexer")


def _log(event: str, **fields) -> None:
    (log.error if event in ("file_error", "fatal") else log.info)(event, **fields)


def process_file(
    path: Path,
    root: Path,
    store: vector_store.VectorStore,
    *,
    known_hash: str | set[str] | None = None,
    shared_blobs: set[tuple[str, str]] | None = None,
) -> tuple[int, str, str]:
    """Chunk, embed, and upsert a single file.

    Returns `(chunk_count, outcome, blob_hash)` where outcome is `indexed`,
    `skipped` (already linked to this checkout), `attach` (same blob already
    embedded by another checkout — the caller batches the tag write), or
    `empty` (no chunks). A changed file keeps its previous points until the
    new blob is attached or upserted, so an embedder failure does not punch
    a hole in RAG.
    """
    rel_path = path.relative_to(root).as_posix()
    try:
        content = path.read_bytes()
    except OSError:
        return 0, "empty", ""
    current_hash = chunker.compute_blob_hash(content, root)
    known = {known_hash} if isinstance(known_hash, str) else set(known_hash or ())
    extras = known - {current_hash}
    if known_hash is not None and current_hash in known:
        for old in extras:
            store.delete_by_file_paths([rel_path], git_blob_hash=old)
        return 0, "skipped", current_hash

    if shared_blobs is not None and (rel_path, current_hash) in shared_blobs:
        return 0, "attach", current_hash

    chunks = chunker.chunk_file(path, root, content=content)
    if not chunks:
        for old in extras or known:
            store.delete_by_file_paths([rel_path], git_blob_hash=old)
        return 0, "empty", current_hash
    vectors = embeddings_client.embed_texts([c.text for c in chunks])
    written = store.upsert_chunks(chunks, vectors)
    for old in extras:
        store.delete_by_file_paths([rel_path], git_blob_hash=old)
    return written, "indexed", current_hash


def index_source_root(root: Path, store: vector_store.VectorStore) -> dict:
    """One full batch pass over `root`. Returns summary stats.

    Unchanged files (same `git_blob_hash` as already stored) are skipped.
    Blobs already embedded by another branch/checkout are linked without
    re-embedding. Files present for this checkout in the collection but
    absent from this walk are detached/deleted at the end.
    """
    snap = store.snapshot()
    if snap.unstored_owners:
        store.persist_checkouts(snap.unstored_owners)
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
                known_hash=snap.hashes_for(rel_path) or None,
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
        for rel_path, new_hash in to_attach:
            for old_hash in snap.hashes_for(rel_path):
                if old_hash != new_hash:
                    store.delete_by_file_paths([rel_path], git_blob_hash=old_hash)

    stale = [path for path in existing if path not in seen]
    if stale:
        stats["files_deleted"] = store.delete_by_file_paths(stale)
        _log("files_tombstoned", count=stats["files_deleted"])

    _log("summary", **stats)
    return stats


class WatchDebouncer:
    """Last event per path wins after a quiet period. A save burst is one upsert."""

    def __init__(self, delay: float, on_flush: Callable[[str, str], None]) -> None:
        self.delay = delay
        self.on_flush = on_flush
        self._pending: dict[str, str] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._gen: dict[str, int] = {}
        self._seq = 0
        self._lock = threading.Lock()

    def submit(self, path: str, kind: str) -> None:
        flush: tuple[str, str] | None = None
        with self._lock:
            self._pending[path] = kind
            self._seq += 1
            gen = self._seq
            self._gen[path] = gen
            old = self._timers.pop(path, None)
            if old is not None:
                old.cancel()
            if self.delay <= 0:
                self._pending.pop(path, None)
                self._gen.pop(path, None)
                flush = (path, kind)
            else:
                timer = threading.Timer(self.delay, self._fire, args=(path, gen))
                timer.daemon = True
                self._timers[path] = timer
                timer.start()
        if flush is not None:
            self.on_flush(*flush)

    def _fire(self, path: str, gen: int) -> None:
        with self._lock:
            if self._gen.get(path) != gen:
                return
            self._timers.pop(path, None)
            self._gen.pop(path, None)
            kind = self._pending.pop(path, None)
        if kind is not None:
            self.on_flush(path, kind)

    def flush_all(self) -> None:
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            self._gen.clear()
            pending = dict(self._pending)
            self._pending.clear()
        for path, kind in pending.items():
            self.on_flush(path, kind)


def _watch_rel_path(path: Path, root: Path, fallback: str) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return fallback.replace("\\", "/")


def apply_watch_event(src_path: str, kind: str, root: Path, store: vector_store.VectorStore) -> None:
    path = Path(src_path)
    if kind != "deleted" and not chunker.is_source_file(path):
        return
    rel_path = _watch_rel_path(path, root, src_path)
    if kind == "deleted":
        store.delete_by_file_paths([rel_path])
        _log("file_tombstoned", file=rel_path, trigger="watch")
        return
    try:
        snap = store.snapshot()
        chunk_count, outcome, blob_hash = process_file(
            path,
            root,
            store,
                known_hash=snap.hashes_for(rel_path) or None,
            shared_blobs=snap.blobs,
        )
        if outcome == "attach":
            store.attach_checkouts([(rel_path, blob_hash)], snapshot=snap)
            for old_hash in snap.hashes_for(rel_path):
                if old_hash != blob_hash:
                    store.delete_by_file_paths([rel_path], git_blob_hash=old_hash)
        if outcome in ("skipped", "attach"):
            _log("file_skipped", file=rel_path, reason="git_blob_hash", trigger="watch")
        else:
            _log("file_processed", file=rel_path, chunks=chunk_count, trigger="watch")
    except Exception as exc:  # noqa: BLE001 - keep the watcher alive
        _log("file_error", file=rel_path, error=repr(exc), trigger="watch")


def _run_watch(
    root: Path,
    store: vector_store.VectorStore,
    *,
    debounce: float = WATCH_DEBOUNCE_SECONDS,
) -> None:
    # Imported lazily so the one-shot path (the primary supported mode) never
    # pays for importing watchdog if it isn't installed/needed.
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    debouncer = WatchDebouncer(
        debounce,
        lambda src, kind: apply_watch_event(src, kind, root, store),
    )

    class _Handler(FileSystemEventHandler):
        def on_created(self, event) -> None:
            if not event.is_directory:
                debouncer.submit(event.src_path, "changed")

        def on_modified(self, event) -> None:
            if not event.is_directory:
                debouncer.submit(event.src_path, "changed")

        def on_deleted(self, event) -> None:
            if not event.is_directory:
                debouncer.submit(event.src_path, "deleted")

        def on_moved(self, event) -> None:
            src = Path(event.src_path)
            dest = Path(event.dest_path)
            if event.is_directory:
                try:
                    old_rel = src.relative_to(root).as_posix()
                except ValueError:
                    old_rel = ""
                if old_rel:
                    snap = store.snapshot()
                    for rel in list(snap.checkout_hashes):
                        if rel == old_rel or rel.startswith(old_rel + "/"):
                            debouncer.submit(str(root / rel), "deleted")
                if dest.is_dir():
                    for child in chunker.iter_source_files(dest):
                        debouncer.submit(str(child), "changed")
                return
            debouncer.submit(event.src_path, "deleted")
            debouncer.submit(event.dest_path, "changed")

    _log(
        "watch_started",
        root=str(root),
        debounce_seconds=debounce,
        note="best-effort, Linux-preferred; a full batch ran first",
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
        debouncer.flush_all()


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
            "Best-effort, Linux-preferred: run one full batch, then keep running and "
            "re-index files as they change (watchdog/inotify, 0.5s debounce). Not "
            "reliable through the Windows -> Podman-machine -> WSL2 bind-mount path. "
            "The default one-shot batch mode (no flag) is the primary supported path."
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

    stats = index_source_root(root, store)
    if args.watch:
        _run_watch(root, store)
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
