#!/usr/bin/env python3
"""Build a portable zip of this repository without models, logs, or local state.

    python scripts/deploy.py export
    python scripts/deploy.py export --out D:/rest/export.zip
    python scripts/deploy.py export --include-env
"""

from __future__ import annotations

import argparse
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# isort: off
# `repo` puts services/common on sys.path. `product` is not importable before that.
from repo import ROOT as REPO_ROOT
import product
# isort: on

ARCHIVE_ROOT = product.ARCHIVE_ROOT
DEFAULT_OUT = REPO_ROOT / f"{ARCHIVE_ROOT}-export.zip"

SKIP_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".pytest-tmp",
    ".ruff_cache",
    ".venv",
    ".venv-wsl",
    ".venv-win",
    ".wsl-setup",
    "__pycache__",
    "build",
    "out",
    "site",
    "wsl-distro",
    "qdrant_storage",
}

SKIP_DIR_RELATIVE = {
    "models/.cache",
    "models/hf",
    "models/adapters",
    "models/training",
    "state",
}

SKIP_SUFFIXES = {".gguf", ".pyc", ".o", ".obj", ".zip", ".vhdx"}
SKIP_NAMES = {".prompt.tmp", "Thumbs.db", ".DS_Store"}
ALWAYS_SKIP_RELATIVE = {".env", "atlassian.env"}


def _posix(path: Path) -> str:
    return path.as_posix()


def _skip_dir(rel_posix: str, name: str) -> bool:
    if name in SKIP_DIR_NAMES:
        return True
    if rel_posix in SKIP_DIR_RELATIVE:
        return True
    return any(rel_posix.startswith(prefix + "/") for prefix in SKIP_DIR_RELATIVE)


def should_skip(rel: Path, *, include_env: bool) -> bool:
    rel_posix = _posix(rel)
    if rel_posix in ALWAYS_SKIP_RELATIVE and not include_env:
        return True
    if rel.name in SKIP_NAMES or rel.name in SKIP_DIR_NAMES:
        return True
    if rel.suffix.lower() in SKIP_SUFFIXES:
        return True
    if rel.name.endswith(".tar.gz"):
        return True
    for part in rel.parts:
        if part in SKIP_DIR_NAMES:
            return True
    return any(rel_posix == prefix or rel_posix.startswith(prefix + "/") for prefix in SKIP_DIR_RELATIVE)


def iter_files(root: Path, *, include_env: bool) -> list[Path]:
    found: list[Path] = []
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        rel_dir = current.relative_to(root)
        rel_dir_posix = "" if rel_dir == Path(".") else _posix(rel_dir)
        kept: list[str] = []
        for name in dirnames:
            child = f"{rel_dir_posix}/{name}" if rel_dir_posix else name
            if not _skip_dir(child.replace("\\", "/"), name):
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            path = current / name
            rel = path.relative_to(root)
            if should_skip(rel, include_env=include_env):
                continue
            found.append(path)
    return sorted(found)


def archive_name(root: Path, path: Path) -> str:
    return f"{ARCHIVE_ROOT}/{_posix(path.relative_to(root))}"


def write_archive(out: Path, *, include_env: bool, root: Path | None = None) -> int:
    root = (root or REPO_ROOT).resolve()
    files = iter_files(root, include_env=include_env)
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    files = [path for path in files if path.resolve() != out]
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        zf.writestr(
            f"{ARCHIVE_ROOT}/EXPORT.txt",
            (
                f"Exported {stamp} UTC from {root}\n"
                "Models, logs, state/, virtualenvs, and caches are omitted.\n"
                "On the target machine: copy .env.example to .env, set AGENT_BEARER_TOKEN,\n"
                "then `python scripts/deploy.py fetch-extra evo-x5` and `python scripts/deploy.py up`.\n"
            ),
        )
        for path in files:
            zf.write(path, arcname=archive_name(root, path))
    return len(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help=f"destination zip path (default: {ARCHIVE_ROOT}-export.zip at the repo root)",
    )
    parser.add_argument(
        "--include-env",
        action="store_true",
        help="also pack .env and atlassian.env (secrets). Default is to omit them.",
    )
    args = parser.parse_args(argv)
    count = write_archive(Path(args.out), include_env=args.include_env)
    print(f"[export] {count} files -> {Path(args.out).resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
