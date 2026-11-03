"""Files named in the question, read from the checkout and shown to the model.

Qdrant stays the fuzzy search. A path written in the question is not left
to the top-5 ranking.
"""

from __future__ import annotations

import re

_PATH = re.compile(r"(?<![\w./])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9]{1,8})")
_MAX_FILES = 3
_MAX_LINES = 120


def paths_in_query(query: str) -> list[str]:
    found: list[str] = []
    for match in _PATH.finditer(query):
        rel = match.group(1).replace("\\", "/")
        # Drop a leading "./" only. `lstrip("./")` would also eat the dot of
        # a dotfile such as `.clang-format`, turning it into `clang-format`.
        while rel.startswith("./"):
            rel = rel[2:]
        rel = rel.lstrip("/")
        if not rel or ".." in rel.split("/"):
            continue
        if rel not in found:
            found.append(rel)
    return found


def is_noise_path(path: str) -> bool:
    """Config the index keeps, and that should not crowd out a C++ question."""
    norm = path.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm == "CMakePresets.json" or norm.startswith(".vscode/")


def named_file_block(query: str) -> str:
    from repo_map import map_root

    root = map_root()
    if root is None:
        return ""
    blocks: list[str] = []
    for rel in paths_in_query(query)[:_MAX_FILES]:
        path = (root / rel).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            continue
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        body = "\n".join(lines[:_MAX_LINES])
        if len(lines) > _MAX_LINES:
            body += "\n…"
        blocks.append(f"[file] {rel}\n{body}")
    if not blocks:
        return ""
    return "Files named in the question. Quote these lines. Do not claim a named file is missing.\n" + "\n\n".join(
        blocks
    )
