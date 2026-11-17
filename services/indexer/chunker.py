"""Structural chunking of C++ source files for the indexer.

Uses `tree-sitter` + the official `tree-sitter-cpp` grammar package to parse
each source file into a real syntax tree and slice it into chunks at
function/class/method granularity, instead of splitting on blank lines or a
fixed number of lines (see docs/concepts/indexing-pipeline.md for the
rationale).

Package choice: `tree-sitter-cpp` (not `tree_sitter_languages` /
`tree-sitter-language-pack`) - it publishes prebuilt ABI3 wheels
(manylinux/musllinux/macOS/Windows) so `pip install` needs no C compiler at
container-build time, and `tree_sitter_languages` is unmaintained and breaks
against modern `tree-sitter` releases. See requirements.txt for the full note.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Iterator, List, Optional

import text_chunker
import tree_sitter_cpp as tscpp
from chunker_types import Chunk
from tree_sitter import Language, Node, Parser

# .cxx/.hh/.hxx are accepted too even though the task's canonical list is
# .cpp/.hpp/.h/.cc - harmless to be a little more permissive here since
# `chunk_file` skips (rather than crashes on) anything else.
SOURCE_EXTENSIONS = {".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".h"}

# Directories never worth indexing (build outputs, VCS metadata, deps).
SKIPPED_DIR_NAMES = {
    ".git",
    ".svn",
    ".llm",
    ".vs",
    ".cache",
    ".venv",
    "build",
    "out",
    "dist",
    "CMakeFiles",
    "node_modules",
    "__pycache__",
}

FILE_LEVEL_SYMBOL = "<file-level>"
ANONYMOUS_SYMBOL = "<anonymous>"

_FUNCTION_NODE_TYPES = {"function_definition"}
_CLASS_NODE_TYPES = {"class_specifier", "struct_specifier"}
_NAME_NODE_TYPES = {
    "identifier",
    "field_identifier",
    "qualified_identifier",
    "destructor_name",
    "operator_name",
    "template_function",
}
_DECLARATOR_WRAPPER_TYPES = {
    "function_declarator",
    "pointer_declarator",
    "reference_declarator",
    "parenthesized_declarator",
    "array_declarator",
}

_CPP_LANGUAGE = Language(tscpp.language())


@lru_cache(maxsize=1)
def _get_parser() -> Parser:
    return Parser(_CPP_LANGUAGE)


def is_cpp_file(path: Path) -> bool:
    return path.suffix.lower() in SOURCE_EXTENSIONS


def is_text_file(path: Path) -> bool:
    """Non-C++ file we still index (CMake, Markdown, YAML, ...) - see text_chunker."""
    return text_chunker.language_for(path) is not None


def is_source_file(path: Path) -> bool:
    """Anything the indexer will chunk: C++ structurally, or a supported text format."""
    return is_cpp_file(path) or is_text_file(path)


def _skip_directory(name: str) -> bool:
    return name in SKIPPED_DIR_NAMES or name.startswith("cmake-build-")


def iter_source_files(root: Path) -> Iterator[Path]:
    """Yield indexable files under `root`, sorted, without entering build or VCS trees."""
    found: list[Path] = []

    root_resolved = root.resolve()
    seen_dirs: set[Path] = set()

    def walk(directory: Path) -> None:
        try:
            real = directory.resolve()
        except OSError:
            return
        try:
            real.relative_to(root_resolved)
        except ValueError:
            return
        if real in seen_dirs:
            return
        seen_dirs.add(real)
        try:
            children = list(directory.iterdir())
        except OSError:
            return
        for child in children:
            try:
                is_dir = child.is_dir()
                is_file = child.is_file()
            except OSError:
                continue
            if is_dir:
                if not _skip_directory(child.name):
                    walk(child)
            elif is_file and is_source_file(child):
                try:
                    child.resolve().relative_to(root_resolved)
                except (OSError, ValueError):
                    continue
                found.append(child)

    walk(root)
    yield from sorted(found)


@lru_cache(maxsize=None)
def _is_git_repo(root: str) -> bool:
    if shutil.which("git") is None:
        return False
    try:
        result = subprocess.run(
            ["git", "-C", root, "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def _git_style_blob_hash(content: bytes) -> str:
    """Reproduce `git hash-object`'s algorithm: sha1("blob {len}\\0" + content).

    This is a pure hash-function computation (no `git` subprocess needed to
    produce the *value* itself - only to decide whether it's meaningful to
    call it a "git blob hash" in the first place, see `compute_blob_hash`).
    """
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def compute_blob_hash(content: bytes, source_root: Path) -> str:
    """Compute the payload's `git_blob_hash` field.

    - If `source_root` is inside a git working tree (and `git` is on PATH),
      compute the actual git blob hash (identical to `git hash-object`).
    - Otherwise (no git repo, or git not installed) fall back to a plain
      `sha1(content)` - documented fallback, not a git blob hash, but still
      a stable content fingerprint suitable for idempotent re-indexing.
    """
    if _is_git_repo(str(source_root)):
        return _git_style_blob_hash(content)
    return hashlib.sha1(content).hexdigest()


def _node_text(node: Optional[Node], source: bytes) -> Optional[str]:
    if node is None:
        return None
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _has_function_declarator(node: Optional[Node]) -> bool:
    if node is None:
        return False
    if node.type == "function_declarator":
        return True
    if node.type in _DECLARATOR_WRAPPER_TYPES:
        return _has_function_declarator(node.child_by_field_name("declarator"))
    return False


def _find_name_node(node: Optional[Node]) -> Optional[Node]:
    """Walk a declarator chain down to the innermost name-bearing node.

    Handles pointer/reference return types (`int* foo()`), out-of-line
    qualified definitions (`Foo::bar()`), destructors, operator overloads,
    and plain identifiers/field identifiers (in-class methods).
    """
    if node is None:
        return None
    if node.type in _NAME_NODE_TYPES:
        return node
    if node.type in _DECLARATOR_WRAPPER_TYPES:
        return _find_name_node(node.child_by_field_name("declarator"))
    return None


def _ancestor_class_names(node: Node, source: bytes) -> List[str]:
    """Collect enclosing class/struct names, outermost first (for nested classes/methods)."""
    names: List[str] = []
    parent = node.parent
    while parent is not None:
        if parent.type in _CLASS_NODE_TYPES:
            name_node = parent.child_by_field_name("name")
            if name_node is not None:
                names.append(_node_text(name_node, source) or ANONYMOUS_SYMBOL)
        parent = parent.parent
    names.reverse()
    return names


def _qualify(node: Node, own_name: Optional[str], source: bytes) -> str:
    if own_name is None:
        return ANONYMOUS_SYMBOL
    if "::" in own_name:
        # Already qualified (e.g. an out-of-line `Foo::bar` definition) - the
        # source text already encodes the right scope, don't double-prefix it.
        return own_name
    ancestors = _ancestor_class_names(node, source)
    if ancestors:
        return "::".join([*ancestors, own_name])
    return own_name


def _effective_range_node(node: Node) -> Node:
    """Templates: use the enclosing `template_declaration`'s range so the
    chunk includes the `template <...>` header, not just the inner
    function/class body."""
    parent = node.parent
    if parent is not None and parent.type == "template_declaration":
        return parent
    return node


def _make_chunk(
    rel_path: str,
    symbol_name: str,
    range_node: Node,
    source: bytes,
    blob_hash: str,
) -> Chunk:
    start_line = range_node.start_point[0] + 1
    end_line = range_node.end_point[0] + 1
    text = source[range_node.start_byte : range_node.end_byte].decode(
        "utf-8", errors="replace"
    )
    return Chunk(
        file_path=rel_path,
        symbol_name=symbol_name,
        start_line=start_line,
        end_line=end_line,
        language="cpp",
        text=text,
        git_blob_hash=blob_hash,
    )


def _fit_embedder_context(chunk: Chunk) -> List[Chunk]:
    """Split a chunk the embedder would reject.

    nomic-embed-text-v1.5 is capped at its 2048-token context. Line count
    is not enough: a function under 120 lines, or a 60-line window of long
    lines, still goes over. `fit_chunk` applies the line window and the
    character cap. The symbol keeps its name and gains a `<lines a-b>` suffix.
    """
    return text_chunker.fit_chunk(chunk)


def _walk(node: Node) -> Iterator[Node]:
    """Preorder walk. Iterative so a deep generated header does not blow the stack."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def chunk_source(
    content: bytes, rel_path: str, source_root: Path, blob_hash: Optional[str] = None
) -> List[Chunk]:
    """Chunk already-read `content` (bytes) as if it lived at `rel_path`.

    Split out from `chunk_file` so tests can exercise the parser against
    small synthetic snippets without touching the filesystem.
    """
    if not content.strip():
        return []

    if blob_hash is None:
        blob_hash = compute_blob_hash(content, source_root)

    tree = _get_parser().parse(content)
    root = tree.root_node

    chunks: List[Chunk] = []
    for node in _walk(root):
        if node.type in _FUNCTION_NODE_TYPES:
            declarator = node.child_by_field_name("declarator")
            name_node = _find_name_node(declarator)
            own_name = _node_text(name_node, content)
            symbol_name = _qualify(node, own_name, content)
            range_node = _effective_range_node(node)
            chunks.extend(
                _fit_embedder_context(_make_chunk(rel_path, symbol_name, range_node, content, blob_hash))
            )
        elif node.type in _CLASS_NODE_TYPES:
            name_node = node.child_by_field_name("name")
            own_name = _node_text(name_node, content)
            symbol_name = _qualify(node, own_name, content)
            range_node = _effective_range_node(node)
            chunks.extend(
                _fit_embedder_context(_make_chunk(rel_path, symbol_name, range_node, content, blob_hash))
            )
        elif node.type == "declaration" and not _ancestor_class_names(node, content):
            declarator = node.child_by_field_name("declarator")
            if _has_function_declarator(declarator):
                name_node = _find_name_node(declarator)
                own_name = _node_text(name_node, content)
                if own_name is not None:
                    symbol_name = _qualify(node, own_name, content)
                    range_node = _effective_range_node(node)
                    chunks.extend(
                        _fit_embedder_context(
                            _make_chunk(rel_path, symbol_name, range_node, content, blob_hash)
                        )
                    )

    if not chunks:
        # File has real content (e.g. only macros/globals/typedefs, no
        # function/class bodies) but nothing at function/class granularity -
        # fall back to a single whole-file chunk rather than dropping it.
        text = content.decode("utf-8", errors="replace")
        line_count = text.count("\n") + (0 if text.endswith("\n") or text == "" else 1)
        chunks.extend(
            _fit_embedder_context(
                Chunk(
                    file_path=rel_path,
                    symbol_name=FILE_LEVEL_SYMBOL,
                    start_line=1,
                    end_line=max(line_count, 1),
                    language="cpp",
                    text=text,
                    git_blob_hash=blob_hash,
                )
            )
        )

    return chunks


def chunk_file(path: Path, source_root: Path, content: bytes | None = None) -> List[Chunk]:
    """Chunk a single file on disk. Returns [] for unsupported or empty files.

    C++ goes through tree-sitter (`chunk_source`); supported text formats
    (CMake, Markdown, YAML, ...) go through `text_chunker`. Never raises on
    an unsupported file - it is skipped, and unreadable files are treated as
    empty, so a batch run over a mixed tree never crashes on a binary asset.
    Pass `content` when the caller already read the bytes used for the hash.
    """
    if not is_source_file(path):
        return []
    if content is None:
        try:
            content = path.read_bytes()
        except OSError:
            return []
    try:
        rel_path = path.relative_to(source_root).as_posix()
    except ValueError:
        rel_path = path.as_posix()
    if is_cpp_file(path):
        return chunk_source(content, rel_path, source_root)
    language = text_chunker.language_for(path)
    assert language is not None  # guaranteed by is_source_file
    return text_chunker.chunk_text_source(content, rel_path, language, compute_blob_hash(content, source_root))
