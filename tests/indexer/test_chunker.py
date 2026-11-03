"""Tests for services/indexer/chunker.py.

Uses small synthetic C++ snippets (not the real `app/` tree, which is owned
by a different parallel task) so this suite never depends on that tree
existing or being in any particular state.
"""

import hashlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "indexer"))

import chunker
import pytest


def _chunk(source: str, rel_path: str = "example.cpp", root: Path = Path(".")):
    return chunker.chunk_source(source.encode("utf-8"), rel_path, root)


def _by_symbol(chunks, symbol_name):
    matches = [c for c in chunks if c.symbol_name == symbol_name]
    assert matches, f"no chunk named {symbol_name!r} in {[c.symbol_name for c in chunks]}"
    return matches[0]


class TestFunctionAndClassChunking:
    def test_free_function(self):
        src = """int add(int a, int b) {
    return a + b;
}
"""
        chunks = _chunk(src)
        fn = _by_symbol(chunks, "add")
        assert fn.language == "cpp"
        assert fn.start_line == 1
        assert fn.end_line == 3
        assert "return a + b;" in fn.text
        assert fn.file_path == "example.cpp"

    def test_class_with_inline_method(self):
        src = """class Widget {
public:
    int size() const { return size_; }
private:
    int size_ = 0;
};
"""
        chunks = _chunk(src)
        cls = _by_symbol(chunks, "Widget")
        assert cls.start_line == 1
        assert cls.end_line == 6

        method = _by_symbol(chunks, "Widget::size")
        assert method.start_line == 3
        assert method.end_line == 3

    def test_out_of_line_method_definition(self):
        src = """class Widget {
public:
    int size() const;
};

int Widget::size() const {
    return 42;
}
"""
        chunks = _chunk(src)
        # Declaration-only method has no body -> not a function_definition,
        # so only the out-of-line definition should show up as a chunk.
        method = _by_symbol(chunks, "Widget::size")
        assert method.start_line == 6
        assert method.end_line == 8


class TestEdgeCases:
    def test_templates(self):
        src = """template <typename T>
class Box {
public:
    T get() const { return value_; }
private:
    T value_;
};

template <typename T>
T add(T a, T b) {
    return a + b;
}
"""
        chunks = _chunk(src)
        box = _by_symbol(chunks, "Box")
        # Effective range includes the "template <typename T>" header line.
        assert box.start_line == 1
        assert "template" in box.text

        add_fn = _by_symbol(chunks, "add")
        assert add_fn.start_line == 9
        assert "template" in add_fn.text

        method = _by_symbol(chunks, "Box::get")
        assert method.start_line == 4

    def test_nested_classes_and_methods(self):
        src = """class Outer {
public:
    class Inner {
    public:
        void method() {}
    };
};
"""
        chunks = _chunk(src)
        outer = _by_symbol(chunks, "Outer")
        inner = _by_symbol(chunks, "Outer::Inner")
        method = _by_symbol(chunks, "Outer::Inner::method")
        assert outer.start_line < inner.start_line < method.start_line

    def test_preprocessor_macros_do_not_crash_and_are_ignored(self):
        src = """#define MAX(a, b) ((a) > (b) ? (a) : (b))
#define VERSION "1.0"

int compute(int x, int y) {
    return MAX(x, y);
}
"""
        chunks = _chunk(src)
        fn = _by_symbol(chunks, "compute")
        assert "MAX(x, y)" in fn.text
        # Macros themselves never appear as their own symbol.
        assert all(c.symbol_name != "MAX" for c in chunks)

    def test_empty_file_yields_no_chunks(self):
        assert _chunk("") == []
        assert _chunk("   \n\n\t\n") == []

    def test_markdown_file_goes_through_text_chunker(self, tmp_path):
        readme = tmp_path / "README.md"
        readme.write_text("# Not C++\nThis is indexed as markdown now.\n")
        chunks = chunker.chunk_file(readme, tmp_path)
        assert len(chunks) == 1
        assert chunks[0].language == "markdown"
        assert chunks[0].symbol_name == "Not C++"

    def test_unsupported_file_is_skipped_not_crashed(self, tmp_path):
        image = tmp_path / "logo.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        assert chunker.chunk_file(image, tmp_path) == []

    def test_file_with_no_functions_or_classes_falls_back_to_file_level(self):
        src = """#include <cstdint>

constexpr int kMagicNumber = 42;
using Byte = std::uint8_t;
"""
        chunks = _chunk(src)
        assert len(chunks) == 1
        assert chunks[0].symbol_name == chunker.FILE_LEVEL_SYMBOL
        assert chunks[0].start_line == 1

    def test_anonymous_struct_gets_synthetic_name(self):
        src = """typedef struct {
    int x;
    int y;
} Point;
"""
        chunks = _chunk(src)
        assert any(c.symbol_name == chunker.ANONYMOUS_SYMBOL for c in chunks)


class TestFileDiscovery:
    def test_iter_source_files_filters_extensions(self, tmp_path):
        (tmp_path / "a.cpp").write_text("void f() {}\n")
        (tmp_path / "b.hpp").write_text("void g();\n")
        (tmp_path / "c.h").write_text("void h();\n")
        (tmp_path / "d.cc").write_text("void i() {}\n")
        (tmp_path / "notes.txt").write_text("hello\n")
        (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
        (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n")
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "generated.cpp").write_text("int g;\n")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("[core]\n")

        found = {p.name for p in chunker.iter_source_files(tmp_path)}
        # C++ + supported text formats; build/ and .git/ are skipped; binaries too.
        assert found == {"a.cpp", "b.hpp", "c.h", "d.cc", "notes.txt", "CMakeLists.txt"}
        assert {p.name for p in chunker.iter_source_files(tmp_path) if chunker.is_cpp_file(p)} == {
            "a.cpp", "b.hpp", "c.h", "d.cc",
        }

    def test_iter_source_files_does_not_enter_generated_trees(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.cpp").write_text("void app() {}\n")
        generated = tmp_path / "cmake-build-debug" / "CMakeFiles"
        generated.mkdir(parents=True)
        (generated / "noise.cpp").write_text("int noise;\n")
        (tmp_path / "CMakeFiles").mkdir()
        (tmp_path / "CMakeFiles" / "also.cpp").write_text("int also;\n")

        found = {p.name for p in chunker.iter_source_files(tmp_path)}
        assert found == {"app.cpp"}

    def test_chunk_file_relative_path(self, tmp_path):
        sub = tmp_path / "src" / "nested"
        sub.mkdir(parents=True)
        f = sub / "thing.cpp"
        f.write_text("void thing() {}\n")

        chunks = chunker.chunk_file(f, tmp_path)
        assert chunks[0].file_path == "src/nested/thing.cpp"


class TestBlobHash:
    def test_fallback_plain_sha1_when_not_a_git_repo(self, tmp_path):
        content = b"int main() { return 0; }\n"
        h = chunker.compute_blob_hash(content, tmp_path)
        assert h == hashlib.sha1(content).hexdigest()

    def test_git_style_hash_matches_real_git_when_repo_available(self, tmp_path):
        import shutil

        if shutil.which("git") is None:
            pytest.skip("git not installed")

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        content = b"int main() { return 0; }\n"
        f = tmp_path / "main.cpp"
        f.write_bytes(content)

        expected = subprocess.run(
            ["git", "-C", str(tmp_path), "hash-object", "main.cpp"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        chunker._is_git_repo.cache_clear()
        h = chunker.compute_blob_hash(content, tmp_path)
        assert h == expected

    def test_long_function_is_split_into_line_windows(self):
        body = "\n".join(f"    int x{i} = {i};" for i in range(200))
        chunks = _chunk(f"int big() {{\n{body}\n}}\n")
        assert len(chunks) > 1
        assert all(c.symbol_name.startswith("big <lines ") for c in chunks)
        assert all(
            c.end_line - c.start_line + 1 <= chunker.text_chunker.WINDOW_LINES for c in chunks
        )

    def test_dense_function_under_120_lines_is_split_for_the_embedder(self):
        line = "    int value = " + ("a" * 180) + ";\n"
        body = line * 40
        chunks = _chunk(f"int dense() {{\n{body}}}\n")
        limit = chunker.text_chunker.MAX_EMBED_CHARS
        assert len(chunks) > 1
        assert all(len(c.text) <= limit for c in chunks)
        assert all(c.symbol_name.startswith("dense <lines ") for c in chunks)

    def test_dense_slice_under_the_old_4000_cap_is_still_split(self):
        # modules/calib3d/src/dls.cpp: 3998 characters were 3125 tokens.
        # A cap of 4000 let that slice through and the embedder rejected the file.
        body = "+" * 2500
        chunks = _chunk(f"int dense_line() {{ int x = {body}; }}\n")
        assert len(chunks) > 1
        assert all(len(c.text) <= chunker.text_chunker.MAX_EMBED_CHARS for c in chunks)

    def test_one_line_over_the_embedder_budget_is_sliced(self):
        huge = "x" * (chunker.text_chunker.MAX_EMBED_CHARS + 50)
        chunks = _chunk(f"int wide() {{ int x = {huge}; }}\n")
        assert len(chunks) > 1
        assert all(len(c.text) <= chunker.text_chunker.MAX_EMBED_CHARS for c in chunks)
        assert len({c.symbol_name for c in chunks}) == len(chunks)

    def test_same_content_same_hash_different_content_different_hash(self):
        h1 = chunker._git_style_blob_hash(b"foo")
        h2 = chunker._git_style_blob_hash(b"foo")
        h3 = chunker._git_style_blob_hash(b"bar")
        assert h1 == h2
        assert h1 != h3
