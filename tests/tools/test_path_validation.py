"""Tests for `services.tools.handlers.path_safety` - the security foundation
every other handler in this service builds on.

Every rejection category called out in the task spec gets its own explicit
test: `..` traversal, absolute-path escape, symlink escape (post-resolution),
null bytes, and empty/whitespace input - plus acceptance cases for valid
paths so we know we're not *only* rejecting everything.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make the repo root importable as `services.tools...` regardless of how
# pytest is invoked (bare `pytest`, `python -m pytest`, from a subdirectory,
# etc.) - there is no shared root-level pytest config in this repo.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.tools.handlers.path_safety import (  # noqa: E402
    PathSafetyError,
    mounted_checkout_root,
    validate_path,
)


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.cpp").write_text("int main() { return 0; }\n")
    (root / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.22)\n")
    (root / "nested" / "deeper").mkdir(parents=True)
    (root / "nested" / "deeper" / "file.txt").write_text("hello\n")
    return root


@pytest.fixture()
def outside_secret(tmp_path: Path) -> Path:
    secret = tmp_path / "outside_secret.txt"
    secret.write_text("top secret\n")
    return secret


# --------------------------------------------------------------------------- rejection: empty/whitespace/type
class TestRejectsEmptyOrInvalidInput:
    def test_rejects_empty_string(self, project_root: Path) -> None:
        with pytest.raises(PathSafetyError):
            validate_path("", project_root)

    def test_rejects_whitespace_only(self, project_root: Path) -> None:
        with pytest.raises(PathSafetyError):
            validate_path("   \t  ", project_root)

    def test_rejects_non_string(self, project_root: Path) -> None:
        with pytest.raises(PathSafetyError):
            validate_path(None, project_root)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- rejection: null bytes
class TestRejectsNullBytes:
    @pytest.mark.parametrize(
        "raw",
        [
            "src/main.cpp\x00.txt",
            "\x00src/main.cpp",
            "src/\x00/main.cpp",
        ],
    )
    def test_rejects_null_byte_anywhere(self, raw: str, project_root: Path) -> None:
        with pytest.raises(PathSafetyError):
            validate_path(raw, project_root)


# --------------------------------------------------------------------------- rejection: '..' traversal
class TestRejectsDotDotTraversal:
    @pytest.mark.parametrize(
        "raw",
        [
            "..",
            "../outside_secret.txt",
            "../../outside_secret.txt",
            "src/../../outside_secret.txt",
            "nested/../../outside_secret.txt",
            "nested/deeper/../../../outside_secret.txt",
            "..\\outside_secret.txt",  # Windows-style separator, same rules apply
            "src/..\\..\\outside_secret.txt",
        ],
    )
    def test_rejects_traversal_sequence(self, raw: str, project_root: Path, outside_secret: Path) -> None:
        with pytest.raises(PathSafetyError):
            validate_path(raw, project_root)


# --------------------------------------------------------------------------- rejection: absolute paths
class TestRejectsAbsolutePaths:
    @pytest.mark.parametrize(
        "raw",
        [
            "/etc/passwd",
            "/outside_secret.txt",
            "//etc/passwd",
            "C:/Windows/System32/drivers/etc/hosts",
            "C:\\Windows\\System32",
            "D:/rest/pumba/CONTRACTS.md",
        ],
    )
    def test_rejects_absolute_path(self, raw: str, project_root: Path) -> None:
        with pytest.raises(PathSafetyError):
            validate_path(raw, project_root)

    def test_rejects_absolute_path_even_when_it_would_land_inside_root(self, project_root: Path) -> None:
        # An absolute path that happens to *textually* point inside root is
        # still rejected: callers must never be trusted to know (or supply)
        # a real filesystem-absolute path at all.
        absolute_but_inside = str(project_root / "src" / "main.cpp").replace("\\", "/")
        with pytest.raises(PathSafetyError):
            validate_path(absolute_but_inside, project_root)


# --------------------------------------------------------------------------- rejection: disallowed characters
class TestRejectsDisallowedSegmentCharacters:
    def test_rejects_colon_in_segment(self, project_root: Path) -> None:
        with pytest.raises(PathSafetyError):
            validate_path("weird:name.txt", project_root)


# --------------------------------------------------------------------------- rejection: symlink escape
class TestRejectsSymlinkEscape:
    def _make_symlink(self, link_path: Path, target: Path) -> None:
        try:
            os.symlink(target, link_path, target_is_directory=target.is_dir())
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlink creation not permitted in this test environment: {exc}")

    def test_symlink_to_outside_file_is_rejected_after_resolution(
        self, project_root: Path, outside_secret: Path
    ) -> None:
        link = project_root / "escape_link.txt"
        self._make_symlink(link, outside_secret)

        with pytest.raises(PathSafetyError):
            validate_path("escape_link.txt", project_root)

    def test_symlink_to_outside_directory_is_rejected_after_resolution(
        self, project_root: Path, tmp_path: Path
    ) -> None:
        outside_dir = tmp_path / "outside_dir"
        outside_dir.mkdir()
        (outside_dir / "payload.txt").write_text("nope\n")

        link = project_root / "escape_dir"
        self._make_symlink(link, outside_dir)

        with pytest.raises(PathSafetyError):
            validate_path("escape_dir/payload.txt", project_root)

    def test_symlink_pointing_back_inside_root_is_accepted(self, project_root: Path) -> None:
        # A symlink is not inherently suspicious - only ones that resolve
        # outside the root should be rejected.
        link = project_root / "alias.cpp"
        self._make_symlink(link, project_root / "src" / "main.cpp")

        resolved = validate_path("alias.cpp", project_root)
        assert resolved == (project_root / "src" / "main.cpp").resolve()


# --------------------------------------------------------------------------- acceptance: valid paths
class TestAcceptsValidPaths:
    def test_accepts_top_level_file(self, project_root: Path) -> None:
        resolved = validate_path("CMakeLists.txt", project_root)
        assert resolved == (project_root / "CMakeLists.txt").resolve()
        assert resolved.is_absolute()

    def test_accepts_nested_file(self, project_root: Path) -> None:
        resolved = validate_path("src/main.cpp", project_root)
        assert resolved == (project_root / "src" / "main.cpp").resolve()

    def test_accepts_deeply_nested_file(self, project_root: Path) -> None:
        resolved = validate_path("nested/deeper/file.txt", project_root)
        assert resolved == (project_root / "nested" / "deeper" / "file.txt").resolve()

    def test_accepts_windows_style_separators_for_a_valid_path(self, project_root: Path) -> None:
        resolved = validate_path("nested\\deeper\\file.txt", project_root)
        assert resolved == (project_root / "nested" / "deeper" / "file.txt").resolve()

    def test_accepts_path_with_leading_dot_slash(self, project_root: Path) -> None:
        resolved = validate_path("./src/main.cpp", project_root)
        assert resolved == (project_root / "src" / "main.cpp").resolve()

    def test_accepts_nonexistent_path_within_root(self, project_root: Path) -> None:
        # path_safety only guarantees containment, not existence - that is
        # each handler's responsibility (e.g. read_file returns a clean
        # "not found" instead of a security error).
        resolved = validate_path("does/not/exist.txt", project_root)
        assert resolved == (project_root / "does" / "not" / "exist.txt").resolve()

    def test_returned_path_is_relative_to_root(self, project_root: Path) -> None:
        root_resolved = project_root.resolve()
        resolved = validate_path("src/main.cpp", project_root)
        assert resolved.is_relative_to(root_resolved)


def test_mounted_checkout_root_rejects_relative_and_other_absolute_dirs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        mounted_checkout_root("co_1")
    with pytest.raises(ValueError, match="absolute"):
        mounted_checkout_root("../etc/passwd")
    assert mounted_checkout_root("") is None
    with pytest.raises(ValueError, match="direct child"):
        mounted_checkout_root(str(tmp_path.resolve()))
