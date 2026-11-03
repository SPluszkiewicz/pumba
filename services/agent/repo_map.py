"""Ranked signature sketch of a checkout.

tree-sitter-cpp when the agent image has it; a line scan otherwise.
Qdrant stays the fuzzy search. This is not a second index.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_SKIP_DIRS = {
    ".git",
    ".svn",
    ".vs",
    ".cache",
    ".venv",
    "node_modules",
    "__pycache__",
    "build",
    "CMakeFiles",
    "out",
    "dist",
    "third_party",
    "thirdparty",
    "3rdparty",
    "external",
    "externals",
}
_EXTS = {".h", ".hpp", ".hh", ".hxx", ".cpp", ".cc", ".cxx", ".c"}
_SKIP_FN = {"if", "for", "while", "switch", "return", "catch", "sizeof", "defined"}
_TYPE = re.compile(r"\b(class|struct|enum|namespace)\s+([A-Za-z_]\w*)")
_FN = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
_HINTS = (
    "architecture",
    "vue d'ensemble",
    "structure du",
    "structure de",
    "organisé",
    "organise",
    "carte du",
    "repo map",
    "repository map",
    "how is the code",
    "how is this",
    "comment est organisé",
    "modules principaux",
    "overview of",
    "layout of",
)
# About 4 000 tokens. The 32 768 window still holds retrieval beside it.
# A longer sketch makes prefill slower; that is the accepted tradeoff.
_DEFAULT_BUDGET = int(os.getenv("AGENT_REPO_MAP_CHARS", "16000"))
_KIND_ORDER = {"namespace": 0, "class": 1, "struct": 2, "enum": 3, "fn": 4}


@dataclass(frozen=True)
class Signature:
    name: str
    path: str
    line: int
    kind: str


def wants_repo_map(query: str) -> bool:
    folded = query.casefold()
    return any(hint in folded for hint in _HINTS)


def map_root() -> Optional[Path]:
    from scope import project_root_header

    raw = project_root_header.get().strip()
    candidate = Path(raw) if raw else Path(os.environ.get("AGENT_MAP_ROOT", "/workspace/app"))
    return candidate if candidate.is_dir() else None


def iter_source_files(root: Path) -> list[Path]:
    """Every C and C++ file under `root`. No count cap: ranking needs the whole tree."""
    found: list[Path] = []

    def walk(directory: Path) -> None:
        try:
            children = list(directory.iterdir())
        except OSError:
            return
        for child in children:
            if child.is_dir():
                if child.name in _SKIP_DIRS or child.name.startswith("cmake-build-"):
                    continue
                walk(child)
            elif child.is_file() and child.suffix.lower() in _EXTS:
                found.append(child)

    walk(root)
    return sorted(found)


def regex_signatures(root: Path) -> list[Signature]:
    found: list[Signature] = []
    for path in iter_source_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for number, line in enumerate(text.splitlines(), 1):
            hit = _regex_line(line)
            if hit is not None:
                name, kind = hit
                found.append(Signature(name, rel, number, kind))
    return found


def _regex_line(line: str) -> Optional[tuple[str, str]]:
    stripped = line.strip()
    if not stripped or stripped.startswith(("//", "#", "*", "/*")):
        return None
    typed = _TYPE.search(stripped)
    if typed is not None:
        return typed.group(2), typed.group(1)
    if "(" not in stripped:
        return None
    if not re.search(r"[;{]\s*$", stripped):
        return None
    named = _FN.search(stripped)
    if named is None or named.group(1) in _SKIP_FN:
        return None
    return named.group(1), "fn"


def tree_sitter_signatures(root: Path) -> list[Signature]:
    import tree_sitter_cpp as tscpp
    from tree_sitter import Language, Parser

    language = Language(tscpp.language())
    parser = Parser(language)
    kinds = {
        "class_specifier": "class",
        "struct_specifier": "struct",
        "enum_specifier": "enum",
        "namespace_definition": "namespace",
        "function_definition": "fn",
    }
    found: list[Signature] = []
    for path in iter_source_files(root):
        try:
            source = path.read_bytes()
        except OSError:
            continue
        tree = parser.parse(source)
        rel = path.relative_to(root).as_posix()

        def walk(node, source: bytes = source, rel: str = rel) -> None:
            label = kinds.get(node.type)
            if label is not None:
                name = _ts_name(node, source)
                if name:
                    found.append(Signature(name, rel, node.start_point[0] + 1, label))
            for child in node.children:
                walk(child)

        walk(tree.root_node)
    return found


def _ts_name(node, source: bytes) -> str:
    named = node.child_by_field_name("name")
    if named is not None:
        return source[named.start_byte : named.end_byte].decode("utf-8", "replace")
    declarator = node.child_by_field_name("declarator")
    if declarator is not None:
        return _ts_name(declarator, source)
    for child in node.children:
        if child.type in {"type_identifier", "identifier", "namespace_identifier"}:
            return source[child.start_byte : child.end_byte].decode("utf-8", "replace")
    return ""


def collect_signatures(root: Path) -> list[Signature]:
    try:
        return tree_sitter_signatures(root)
    except ImportError:
        return regex_signatures(root)


_WORD = re.compile(r"\b[A-Za-z_]\w+\b")
_map_cache: dict[str, tuple[str, str, int]] = {}
_map_cache_lock = threading.Lock()


def clear_repo_map_cache() -> None:
    with _map_cache_lock:
        _map_cache.clear()


def source_fingerprint(root: Path) -> str:
    """Changes when a walked source file is edited. `svn update` updates mtimes."""
    parts: list[str] = []
    for path in iter_source_files(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        parts.append(f"{rel}:{stat.st_mtime_ns}:{stat.st_size}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def reference_counts(root: Path, signatures: list[Signature]) -> dict[str, int]:
    """How many other files mention each defined name. One read per file."""
    defining: dict[str, set[str]] = {}
    for sig in signatures:
        defining.setdefault(sig.name, set()).add(sig.path)
    names = {name for name in defining if len(name) >= 3}
    counts = {name: 0 for name in names}
    for path in iter_source_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name in set(_WORD.findall(text)) & names:
            if rel not in defining[name]:
                counts[name] += 1
    return counts


def rank_signatures(
    signatures: list[Signature], counts: dict[str, int], defining: dict[str, set[str]]
) -> list[Signature]:
    """Types first. A name defined everywhere (`get`, `init`) does not outrank a project type."""

    def key(sig: Signature) -> tuple[int, float, int, str, int, str]:
        sites = max(1, len(defining.get(sig.name, ())))
        specificity = counts.get(sig.name, 0) / sites
        return (_KIND_ORDER.get(sig.kind, 5), -specificity, sites, sig.path, sig.line, sig.name)

    return sorted(signatures, key=key)


def render_repo_map(root: Path, signatures: list[Signature], *, budget_chars: int = _DEFAULT_BUDGET) -> str:
    defining: dict[str, set[str]] = {}
    for sig in signatures:
        defining.setdefault(sig.name, set()).add(sig.path)
    counts = reference_counts(root, signatures)
    ordered = rank_signatures(signatures, counts, defining)
    lines: list[str] = []
    used = 0
    seen: set[tuple[str, str]] = set()
    for sig in ordered:
        key = (sig.name, sig.path)
        if key in seen:
            continue
        seen.add(key)
        line = f"{sig.path}:{sig.line} {sig.name} {sig.kind} ({counts.get(sig.name, 0)})"
        if used + len(line) + 1 > budget_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def build_repo_map(root: Path, *, budget_chars: int = _DEFAULT_BUDGET) -> str:
    """Cached sketch of the whole checkout, capped only when pasted to the model.

    Aider's repo map is the same idea (Apache-2.0 `grep-ast` tags plus
    PageRank). This service already pins `tree-sitter` 0.23, which `grep-ast`
    does not, so the scan stays here and the result is reused until a source
    file's mtime or size changes. The first build reads every source file.
    """
    fingerprint = source_fingerprint(root)
    key = str(root)
    with _map_cache_lock:
        cached = _map_cache.get(key)
        if cached is not None and cached[0] == fingerprint and cached[2] == budget_chars:
            return cached[1]
    text = render_repo_map(root, collect_signatures(root), budget_chars=budget_chars)
    with _map_cache_lock:
        _map_cache[key] = (fingerprint, text, budget_chars)
    return text
