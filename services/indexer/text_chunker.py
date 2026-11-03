"""Chunking for the non-C++ files of a project (CMake, Markdown, YAML, ...).

`chunker.py` handles C++ structurally with `tree-sitter-cpp`. Everything else
that is still worth retrieving - `CMakeLists.txt`, `CMakePresets.json`,
READMEs, CI/compose YAML, `pyproject.toml` - goes through this module, which
reuses the *same* technique with the community-maintained grammars bundled in
`tree-sitter-language-pack` (no hand-written Markdown/CMake parsing):

| language | grammar unit used as a chunk                              | `symbol_name`            |
|----------|-----------------------------------------------------------|--------------------------|
| markdown | `section` (own content, child sections chunked separately)| heading text             |
| cmake    | top-level `normal_command` / `function_def` / `macro_def` / ... | first target/function name |
| yaml     | top-level `block_mapping_pair` of each document           | the key                  |
| json     | top-level `pair` of the root object                       | the key                  |
| toml     | top-level `table` / `pair`                                | the table header / key   |
| other (ini, shell, Containerfile, text) | no grammar: fixed windows            | `<file-level>` / `<lines a-b>` |

What the grammars do *not* decide - and is therefore policy in this module:
consecutive small units (CMake / YAML / JSON / TOML) are packed together up
to `WINDOW_LINES` so we don't embed one-line chunks. A unit longer than
`2 * WINDOW_LINES`, or longer than `MAX_EMBED_CHARS`, is cut into overlapping
windows (`<symbol> <lines a-b>`). The character cap is what keeps a dense
excerpt under the embedder's 2048-token context.
Every chunk has the payload shape pinned in CONTRACTS.md; only `language`
differs from C++ chunks, so a retrieval filter can separate code from docs.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from chunker_types import Chunk
from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_parser

__all__ = [
    "TEXT_LANGUAGE_BY_SUFFIX",
    "TEXT_LANGUAGE_BY_NAME",
    "GRAMMAR_LANGUAGES",
    "WINDOW_LINES",
    "WINDOW_OVERLAP",
    "MAX_EMBED_CHARS",
    "MAX_TEXT_FILE_BYTES",
    "fit_chunk",
    "FILE_LEVEL_SYMBOL",
    "PREAMBLE_SYMBOL",
    "language_for",
    "chunk_text_source",
]

# Extension -> language tag written into the payload.
TEXT_LANGUAGE_BY_SUFFIX: Dict[str, str] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".cmake": "cmake",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".txt": "text",
    ".sh": "shell",
}
# Exact file names that override the extension rule.
TEXT_LANGUAGE_BY_NAME: Dict[str, str] = {
    "CMakeLists.txt": "cmake",
    "CMakePresets.json": "json",
    "CMakeUserPresets.json": "json",
    "Containerfile": "containerfile",
    "Dockerfile": "containerfile",
    ".clang-tidy": "yaml",
    ".clang-format": "yaml",
}
# Languages for which we have a tree-sitter grammar (others are windowed).
GRAMMAR_LANGUAGES = frozenset({"markdown", "cmake", "yaml", "json", "toml"})

WINDOW_LINES = 60
WINDOW_OVERLAP = 10
# nomic-embed-text-v1.5 rejects an input past 2048 tokens. The indexer also
# prefixes `search_document: `. Typical headers are near 2.6 characters per
# token, but a dense function is much tighter: a 3998-character slice of
# modules/calib3d/src/dls.cpp was 3125 tokens (about 1.3 characters per
# token). 1800 characters stays under 2048 even at one token per character.
MAX_EMBED_CHARS = 1800
# Generated lock files, minified JSON, huge logs: not worth embedding.
MAX_TEXT_FILE_BYTES = 1024 * 1024

FILE_LEVEL_SYMBOL = "<file-level>"
PREAMBLE_SYMBOL = "<preamble>"

_CMAKE_NAMING_COMMANDS = frozenset(
    {"project", "add_executable", "add_library", "add_custom_target", "add_test", "add_subdirectory"}
)
_CMAKE_DEF_NODES = {"function_def": "function_command", "macro_def": "macro_command"}


def language_for(path: Path) -> Optional[str]:
    """Language tag for a non-C++ file, or None if it is not an indexable text file."""
    if path.name in TEXT_LANGUAGE_BY_NAME:
        return TEXT_LANGUAGE_BY_NAME[path.name]
    return TEXT_LANGUAGE_BY_SUFFIX.get(path.suffix.lower())


@lru_cache(maxsize=None)
def _parser(language: str) -> Parser:
    return get_parser(language)


# --------------------------------------------------------------------------- #
# Units: (start_line, end_line, symbol) - 1-based inclusive, produced per grammar
# --------------------------------------------------------------------------- #

Unit = tuple[int, int, str]


def _end_line(node: Node) -> int:
    """Block nodes in these grammars usually end at column 0 of the *next* line;
    don't count that line as part of the node."""
    row, col = node.end_point
    return row + 1 if col > 0 else max(row, node.start_point[0] + 1)


def _text(node: Optional[Node]) -> str:
    return node.text.decode("utf-8", errors="replace").strip() if node is not None and node.text else ""


def _first_child(node: Node, *types: str) -> Optional[Node]:
    for child in node.children:
        if child.type in types:
            return child
    return None


def _markdown_units(root: Node) -> List[Unit]:
    units: List[Unit] = []

    def visit(section: Node) -> None:
        # Walk the section's direct children in order. A heading opens a new
        # unit (tree-sitter-markdown folds text preceding the first heading
        # into the same section, and does not always open a nested `section`
        # for setext headings - so we cannot assume one heading per section);
        # a nested `section` closes the current unit and is visited on its own.
        start = section.start_point[0] + 1
        symbol = PREAMBLE_SYMBOL
        current_open = False

        def close(end_line: int) -> None:
            nonlocal current_open
            if current_open and end_line >= start:
                units.append((start, end_line, symbol))
            current_open = False

        for child in section.children:
            if child.type in ("atx_heading", "setext_heading"):
                close(child.start_point[0])
                start = child.start_point[0] + 1
                symbol = _text(_first_child(child, "inline", "paragraph")).replace("\n", " ") or "<untitled>"
                current_open = True
            elif child.type == "section":
                close(child.start_point[0])
                visit(child)
                start = _end_line(child) + 1
                symbol = PREAMBLE_SYMBOL
            else:
                current_open = True
        close(_end_line(section))

    for top in root.children:
        if top.type == "section":
            visit(top)
    return units


def _cmake_symbol(node: Node) -> Optional[str]:
    if node.type in _CMAKE_DEF_NODES:
        command = _first_child(node, _CMAKE_DEF_NODES[node.type])
        args = _first_child(command, "argument_list") if command else None
        first = args.children[0] if args is not None and args.children else None
        return _text(first) or None
    if node.type == "normal_command":
        if _text(_first_child(node, "identifier")).lower() in _CMAKE_NAMING_COMMANDS:
            args = _first_child(node, "argument_list")
            first = args.children[0] if args is not None and args.children else None
            return _text(first).strip('"') or None
    return None


def _cmake_units(root: Node) -> List[Unit]:
    return [(n.start_point[0] + 1, _end_line(n), _cmake_symbol(n) or "") for n in root.children if n.is_named]


def _yaml_units(root: Node) -> List[Unit]:
    units: List[Unit] = []
    for document in root.children:
        if document.type != "document":
            continue
        block = _first_child(document, "block_node")
        mapping = _first_child(block, "block_mapping") if block else None
        if mapping is None:
            units.append((document.start_point[0] + 1, _end_line(document), ""))
            continue
        for pair in mapping.children:
            if pair.type == "block_mapping_pair":
                key = _text(pair.children[0]) if pair.children else ""
                units.append((pair.start_point[0] + 1, _end_line(pair), key))
    return units


def _json_units(root: Node) -> List[Unit]:
    obj = _first_child(root, "object")
    if obj is None:
        return []
    return [
        (p.start_point[0] + 1, _end_line(p), _text(_first_child(p, "string")).strip('"'))
        for p in obj.children
        if p.type == "pair"
    ]


def _toml_units(root: Node) -> List[Unit]:
    units: List[Unit] = []
    for n in root.children:
        if n.type == "table" or n.type == "table_array_element":
            key = _first_child(n, "dotted_key", "bare_key", "quoted_key")
            units.append((n.start_point[0] + 1, _end_line(n), _text(key)))
        elif n.type == "pair":
            units.append((n.start_point[0] + 1, _end_line(n), _text(n.children[0]) if n.children else ""))
    return units


_UNIT_EXTRACTORS: Dict[str, Callable[[Node], List[Unit]]] = {
    "markdown": _markdown_units,
    "cmake": _cmake_units,
    "yaml": _yaml_units,
    "json": _json_units,
    "toml": _toml_units,
}
# Formats whose units are meaningful on their own and must not be packed together.
_NO_PACKING = frozenset({"markdown"})


# --------------------------------------------------------------------------- #
# Policy: packing small units, windowing large ones, emitting chunks
# --------------------------------------------------------------------------- #


def _base_symbol(symbol: str) -> str:
    marker = " <lines "
    idx = symbol.rfind(marker)
    if idx >= 0:
        return symbol[:idx].rstrip()
    if symbol.startswith("<lines "):
        return ""
    return symbol


def _with_lines(symbol: str, start: int, end: int, part: Optional[int] = None) -> str:
    base = _base_symbol(symbol)
    extra = f" #{part}" if part else ""
    if base:
        return f"{base} <lines {start}-{end}{extra}>"
    return f"<lines {start}-{end}{extra}>"


def _span_chunk(chunk: Chunk, lines: List[str], start: int, end: int) -> Chunk:
    """`start`/`end` are 0-based, `end` exclusive, into `lines`."""
    a = chunk.start_line + start
    b = chunk.start_line + end - 1
    return Chunk(
        file_path=chunk.file_path,
        symbol_name=_with_lines(chunk.symbol_name, a, b),
        start_line=a,
        end_line=b,
        language=chunk.language,
        text="".join(lines[start:end]),
        git_blob_hash=chunk.git_blob_hash,
    )


def _slice_line(chunk: Chunk, line: str, index: int) -> List[Chunk]:
    """One source line that is itself over the embedder budget."""
    a = chunk.start_line + index
    parts: List[Chunk] = []
    offset = 0
    part = 1
    while offset < len(line):
        parts.append(
            Chunk(
                file_path=chunk.file_path,
                symbol_name=_with_lines(chunk.symbol_name, a, a, part),
                start_line=a,
                end_line=a,
                language=chunk.language,
                text=line[offset : offset + MAX_EMBED_CHARS],
                git_blob_hash=chunk.git_blob_hash,
            )
        )
        offset += MAX_EMBED_CHARS
        part += 1
    return parts


def fit_chunk(chunk: Chunk) -> List[Chunk]:
    """Cut a chunk the embedder would reject. A short chunk is returned as-is.

    Two caps, both required: 120 lines (so a long function stays a sequence
    of overlapping windows) and `MAX_EMBED_CHARS` (so a shorter but dense
    excerpt still fits in 2048 tokens). A single line over the character
    cap is sliced, and each slice gets its own `#n` so the point ids differ.
    """
    lines = chunk.text.splitlines(keepends=True)
    if not lines:
        return [chunk]
    if len(lines) <= WINDOW_LINES * 2 and len(chunk.text) <= MAX_EMBED_CHARS:
        return [chunk]

    parts: List[Chunk] = []
    start = 0
    while start < len(lines):
        if len(lines[start]) > MAX_EMBED_CHARS:
            parts.extend(_slice_line(chunk, lines[start], start))
            start += 1
            continue
        end = start
        size = 0
        while (
            end < len(lines)
            and (end - start) < WINDOW_LINES
            and size + len(lines[end]) <= MAX_EMBED_CHARS
        ):
            size += len(lines[end])
            end += 1
        if end == start:
            end = start + 1
        parts.append(_span_chunk(chunk, lines, start, end))
        if end >= len(lines):
            break
        nxt = end - WINDOW_OVERLAP
        if nxt <= start:
            nxt = start + 1
        start = nxt
    return parts


def _windows(total: int, size: int = WINDOW_LINES, overlap: int = WINDOW_OVERLAP) -> List[tuple[int, int]]:
    """1-based inclusive (start, end) windows covering 1..total."""
    if total <= 0:
        return []
    if total <= size:
        return [(1, total)]
    step = max(size - overlap, 1)
    out: List[tuple[int, int]] = []
    start = 1
    while start <= total:
        end = min(start + size - 1, total)
        out.append((start, end))
        if end == total:
            break
        start += step
    return out


def _pack(units: Sequence[Unit]) -> List[Unit]:
    """Merge consecutive units while the merged span stays within WINDOW_LINES.
    The merged symbol lists the first names (max 3) so it stays searchable."""
    if not units:
        return []
    packed: List[tuple[int, int, List[str]]] = [(units[0][0], units[0][1], [units[0][2]])]
    for start, end, symbol in units[1:]:
        p_start, _p_end, names = packed[-1]
        if end - p_start + 1 <= WINDOW_LINES:
            packed[-1] = (p_start, end, [*names, symbol])
        else:
            packed.append((start, end, [symbol]))
    out: List[Unit] = []
    for start, end, names in packed:
        named = [n for n in names if n]
        symbol = ", ".join(named[:3]) + (", …" if len(named) > 3 else "")
        out.append((start, end, symbol))
    return out


def _make(rel_path: str, symbol: str, lines: List[str], start: int, end: int, language: str, blob: str) -> Chunk:
    return Chunk(
        file_path=rel_path,
        symbol_name=symbol,
        start_line=start,
        end_line=end,
        language=language,
        text="".join(lines[start - 1 : end]),
        git_blob_hash=blob,
    )


def _emit(rel_path: str, lines: List[str], units: Sequence[Unit], language: str, blob: str) -> List[Chunk]:
    chunks: List[Chunk] = []
    total = len(lines)
    for n, (start, end, symbol) in enumerate(units, start=1):
        start, end = max(1, start), min(end, total)
        if end < start:
            continue
        symbol = symbol or f"<block {n}>"
        if end - start + 1 <= WINDOW_LINES * 2:
            chunks.extend(fit_chunk(_make(rel_path, symbol, lines, start, end, language, blob)))
            continue
        for w_start, w_end in _windows(end - start + 1):
            a, b = start + w_start - 1, start + w_end - 1
            chunks.extend(
                fit_chunk(_make(rel_path, f"{symbol} <lines {a}-{b}>", lines, a, b, language, blob))
            )
    return chunks


def _chunk_windowed(rel_path: str, lines: List[str], language: str, blob: str) -> List[Chunk]:
    if len(lines) <= WINDOW_LINES:
        return fit_chunk(_make(rel_path, FILE_LEVEL_SYMBOL, lines, 1, len(lines), language, blob))
    chunks: List[Chunk] = []
    for start, end in _windows(len(lines)):
        chunks.extend(
            fit_chunk(_make(rel_path, f"<lines {start}-{end}>", lines, start, end, language, blob))
        )
    return chunks


def chunk_text_source(content: bytes, rel_path: str, language: str, blob_hash: str) -> List[Chunk]:
    """Chunk a non-C++ text file. Returns [] for empty, binary or oversized content."""
    if not content.strip() or len(content) > MAX_TEXT_FILE_BYTES or b"\x00" in content:
        return []
    lines = content.decode("utf-8", errors="replace").splitlines(keepends=True)

    extractor = _UNIT_EXTRACTORS.get(language)
    if extractor is None:
        return _chunk_windowed(rel_path, lines, language, blob_hash)

    units = extractor(_parser(language).parse(content).root_node)
    if not units:
        # Grammar produced nothing usable (e.g. a YAML file that is a bare list):
        # fall back to windows rather than dropping the file.
        return _chunk_windowed(rel_path, lines, language, blob_hash)
    if language not in _NO_PACKING:
        units = _pack(units)
    return _emit(rel_path, lines, units, language, blob_hash) or _chunk_windowed(rel_path, lines, language, blob_hash)
