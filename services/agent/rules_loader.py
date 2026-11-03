"""Load versioned glob rules from `rules/*.md` using `pathspec` (gitignore syntax).

Image rules live next to this module. Project rules, when
`AGENT_PROJECT_ROOT` is set, are the `*.md` files in
`<root>/.llm/rules`. Each file is capped, and so is the combined text.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

import pathspec

RULES_DIR = Path(__file__).resolve().parent / "rules"
MAX_RULE_FILE_BYTES = 8_192
MAX_RULES_CHARS = 16_000


def parse_rule(text: str) -> tuple[list[str], str]:
    if not text.startswith("---"):
        return ["**/*"], text.strip()
    parts = text.split("---", 2)
    if len(parts) < 3:
        return ["**/*"], text.strip()
    globs: list[str] = []
    for line in parts[1].splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            globs.append(stripped[2:].strip().strip("'\""))
    return globs or ["**/*"], parts[2].strip()


def project_rules_dir() -> Path | None:
    """`<AGENT_PROJECT_ROOT>/.llm/rules` when that directory exists."""
    raw = os.environ.get("AGENT_PROJECT_ROOT", "").strip()
    if not raw:
        return None
    root = Path(raw).resolve()
    path = (root / ".llm" / "rules").resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if not path.is_dir():
        return None
    return path


def _read_bounded(path: Path, root: Path) -> str | None:
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    if not resolved.is_file():
        return None
    try:
        if resolved.stat().st_size > MAX_RULE_FILE_BYTES:
            return None
        return resolved.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _load_dir(root: Path, prefix: str) -> list[tuple[pathspec.PathSpec, str, str]]:
    loaded: list[tuple[pathspec.PathSpec, str, str]] = []
    if not root.is_dir():
        return loaded
    root_resolved = root.resolve()
    total = 0
    for path in sorted(root.glob("*.md")):
        text = _read_bounded(path, root_resolved)
        if text is None:
            continue
        if total + len(text) > MAX_RULES_CHARS:
            break
        total += len(text)
        globs, body = parse_rule(text)
        spec = pathspec.PathSpec.from_lines("gitwildmatch", globs)
        loaded.append((spec, body, f"{prefix}{path.name}"))
    return loaded


def load_rules(directory: Path | None = None) -> list[tuple[pathspec.PathSpec, str, str]]:
    """Each item is (spec, body, filename). One directory, image rules by default."""
    return _load_dir(directory or RULES_DIR, "")


def rules_for(paths: Iterable[str], directory: Path | None = None) -> str:
    """Concatenate rule bodies.

    Image rules (or an explicit `directory`) apply only when a retrieved path
    matches. Project rules under `.llm/rules` are always included: they are
    the working copy's own notes, including for a changelist that is not in
    the index yet.
    """
    unique_paths = list(dict.fromkeys(p.replace("\\", "/") for p in paths if p))
    sections: list[str] = []
    if directory is not None:
        sources = [(directory, "", True)]
    else:
        sources = [(RULES_DIR, "", True)]
        project = project_rules_dir()
        if project is not None:
            sources.append((project, ".llm/rules/", False))
    for root, prefix, require_match in sources:
        for spec, body, name in _load_dir(root, prefix):
            if require_match and not any(spec.match_file(p) for p in unique_paths):
                continue
            sections.append(f"### Rule: {name}\n{body}")
    return "\n\n".join(sections)
