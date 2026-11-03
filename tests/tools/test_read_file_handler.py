"""Tests for `services.tools.handlers.read_file`."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.tools.handlers import read_file  # noqa: E402
from services.tools.handlers.path_safety import PathSafetyError  # noqa: E402


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "main.cpp").write_text("int main() { return 0; }\n")
    (root / "empty_dir").mkdir()
    return root


class TestHappyPath:
    def test_reads_existing_file_content(self, project_root: Path) -> None:
        response = read_file.run_read_file("src/main.cpp", project_root)
        assert response["ok"] is True
        assert response["exit_code"] == 0
        assert "int main" in response["stdout"]
        assert response["path"] == "src/main.cpp"
        assert response["size_bytes"] == (project_root / "src" / "main.cpp").stat().st_size

    def test_response_echoes_caller_relative_path_not_absolute(self, project_root: Path) -> None:
        response = read_file.run_read_file("src/main.cpp", project_root)
        assert str(project_root) not in response["path"]


class TestNotFound:
    def test_missing_file_raises_not_found(self, project_root: Path) -> None:
        with pytest.raises(read_file.ReadFileNotFoundError):
            read_file.run_read_file("src/does_not_exist.cpp", project_root)


class TestNotAFile:
    def test_directory_raises_not_a_file(self, project_root: Path) -> None:
        with pytest.raises(read_file.NotAFileError):
            read_file.run_read_file("empty_dir", project_root)


class TestSizeCap:
    def test_file_under_cap_is_read(self, project_root: Path) -> None:
        small = project_root / "small.txt"
        small.write_bytes(b"x" * (read_file.MAX_READ_FILE_BYTES - 1))
        response = read_file.run_read_file("small.txt", project_root)
        assert response["ok"] is True

    def test_file_over_cap_is_rejected(self, project_root: Path) -> None:
        big = project_root / "big.txt"
        big.write_bytes(b"x" * (read_file.MAX_READ_FILE_BYTES + 1))
        with pytest.raises(read_file.FileTooLargeError) as excinfo:
            read_file.run_read_file("big.txt", project_root)
        assert excinfo.value.max_bytes == read_file.MAX_READ_FILE_BYTES

    def test_file_exactly_at_cap_is_read(self, project_root: Path) -> None:
        exact = project_root / "exact.txt"
        exact.write_bytes(b"x" * read_file.MAX_READ_FILE_BYTES)
        response = read_file.run_read_file("exact.txt", project_root)
        assert response["ok"] is True


class TestPathSafetyIsEnforced:
    def test_traversal_path_is_rejected(self, project_root: Path) -> None:
        with pytest.raises(PathSafetyError):
            read_file.run_read_file("../outside.txt", project_root)

    def test_absolute_path_is_rejected(self, project_root: Path) -> None:
        with pytest.raises(PathSafetyError):
            read_file.run_read_file("/etc/passwd", project_root)


class TestLineWindow:
    @pytest.fixture()
    def long_file(self, project_root: Path) -> Path:
        path = project_root / "long.txt"
        path.write_text("".join(f"line {i}\n" for i in range(1, read_file.MAX_READ_FILE_LINES + 101)))
        return path

    def test_short_file_is_not_truncated(self, project_root: Path) -> None:
        response = read_file.run_read_file("src/main.cpp", project_root)
        assert response["truncated"] is False
        assert response["start_line"] == 1
        assert response["end_line"] == 1
        assert response["total_lines"] == 1
        assert response["stderr"] == ""

    def test_long_file_without_window_is_capped_with_hint(self, project_root: Path, long_file: Path) -> None:
        response = read_file.run_read_file("long.txt", project_root)
        assert response["truncated"] is True
        assert response["total_lines"] == read_file.MAX_READ_FILE_LINES + 100
        assert response["end_line"] == read_file.MAX_READ_FILE_LINES
        assert len(response["stdout"].splitlines()) == read_file.MAX_READ_FILE_LINES
        assert f"start_line={read_file.MAX_READ_FILE_LINES + 1}" in response["stderr"]

    def test_explicit_window_is_honoured(self, project_root: Path, long_file: Path) -> None:
        response = read_file.run_read_file("long.txt", project_root, start_line=10, end_line=12)
        assert response["stdout"] == "line 10\nline 11\nline 12\n"
        assert response["start_line"] == 10
        assert response["end_line"] == 12
        assert response["truncated"] is False

    def test_start_line_only_reads_to_cap_or_eof(self, project_root: Path, long_file: Path) -> None:
        total = read_file.MAX_READ_FILE_LINES + 100
        response = read_file.run_read_file("long.txt", project_root, start_line=total - 4)
        assert response["end_line"] == total
        assert len(response["stdout"].splitlines()) == 5
        assert response["truncated"] is False

    def test_window_wider_than_cap_is_clamped(self, project_root: Path, long_file: Path) -> None:
        response = read_file.run_read_file("long.txt", project_root, start_line=1, end_line=10_000)
        assert response["end_line"] == read_file.MAX_READ_FILE_LINES
        assert response["truncated"] is True

    def test_end_line_past_eof_is_clamped_to_eof(self, project_root: Path) -> None:
        response = read_file.run_read_file("src/main.cpp", project_root, start_line=1, end_line=999)
        assert response["end_line"] == 1
        assert response["truncated"] is False

    @pytest.mark.parametrize("start,end", [(0, None), (None, 0), (5, 3), (-1, None)])
    def test_invalid_window_rejected(self, project_root: Path, start, end) -> None:
        with pytest.raises(read_file.LineRangeError):
            read_file.run_read_file("src/main.cpp", project_root, start_line=start, end_line=end)

    def test_start_past_eof_rejected(self, project_root: Path) -> None:
        with pytest.raises(read_file.LineRangeError):
            read_file.run_read_file("src/main.cpp", project_root, start_line=50)

    def test_empty_file(self, project_root: Path) -> None:
        (project_root / "empty.txt").write_text("")
        response = read_file.run_read_file("empty.txt", project_root)
        assert response["stdout"] == ""
        assert response["total_lines"] == 0
        assert response["truncated"] is False


class TestNonUtf8Content:
    def test_non_utf8_bytes_do_not_crash(self, project_root: Path) -> None:
        binary_file = project_root / "binary.dat"
        binary_file.write_bytes(b"\xff\xfe\x00\x01valid_ascii_too")
        response = read_file.run_read_file("binary.dat", project_root)
        assert response["ok"] is True
        assert "valid_ascii_too" in response["stdout"]
