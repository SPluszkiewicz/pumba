"""Cross-handler tests: subprocess argv is always a list (never `shell=True`
with a concatenated string), and shell-metacharacter-laden input is passed
through as a single, inert argv element rather than ever being interpreted.

These are unit tests with a mocked `subprocess.run` - there is no real shell
involved, so we can't prove "nothing bad happened"; instead we prove the
mocked call received the malicious string as exactly one literal argv
element, which is what makes shell injection structurally impossible here
regardless of the string's contents.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.tools.handlers import build, format_check, lint, static_analysis  # noqa: E402

# No '/' or ':' characters, so these are legal single
# path *segments* under path_safety's rules (a filename may legally contain
# shell metacharacters on POSIX - the safety guarantee comes from never
# invoking a shell, not from sanitizing filenames).
MALICIOUS_PATH_SEGMENTS = [
    "; rm -rf tmp",
    "$(echo pwned)",
    "`echo pwned`",
    "a && echo pwned",
    "a || echo pwned",
    "weird&&name;.cpp",
]


def _completed(argv, returncode=0, stdout="", stderr="") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=argv, returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    (root / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.22)\n")
    (root / "src").mkdir()
    (root / "src" / "main.cpp").write_text("int main() { return 0; }\n")
    return root


class TestArgvIsAlwaysAList:
    """`subprocess.run` must always be called positionally with a list, and
    `shell` must always be `False` (or omitted, but every handler here
    passes it explicitly)."""

    def test_build_uses_list_argv_and_no_shell(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.build.subprocess.run") as mock_run:
            mock_run.side_effect = lambda argv, **kwargs: _completed(argv, returncode=0)
            build.run_build("release", project_root, timeout_seconds=30)

        for call in mock_run.call_args_list:
            argv = call.args[0]
            assert isinstance(argv, list)
            assert all(isinstance(part, str) for part in argv)
            assert call.kwargs.get("shell") is False

    def test_lint_uses_list_argv_and_no_shell(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.lint.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["clang-tidy"], returncode=0)
            lint.run_lint(["src/main.cpp"], project_root, timeout_seconds=30)

        argv = mock_run.call_args.args[0]
        assert isinstance(argv, list)
        assert mock_run.call_args.kwargs.get("shell") is False

    def test_format_check_uses_list_argv_and_no_shell(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.format_check.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["clang-format"], returncode=0)
            format_check.run_format_check(["src/main.cpp"], project_root, timeout_seconds=30)

        argv = mock_run.call_args.args[0]
        assert isinstance(argv, list)
        assert mock_run.call_args.kwargs.get("shell") is False

    def test_static_analysis_uses_list_argv_and_no_shell(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.static_analysis.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["cppcheck"], returncode=0)
            static_analysis.run_static_analysis(["src/main.cpp"], project_root, timeout_seconds=30)

        argv = mock_run.call_args.args[0]
        assert isinstance(argv, list)
        assert mock_run.call_args.kwargs.get("shell") is False


class TestMaliciousPathSegmentPassesThroughAsInertArgvElement:
    """A filename containing shell metacharacters is legal on POSIX and is
    NOT rejected by path_safety (it only guards containment, not filename
    content) - it must still reach the tool's argv as a single, literal,
    inert element rather than being interpreted."""

    @pytest.mark.parametrize("payload", MALICIOUS_PATH_SEGMENTS)
    def test_lint_path_segment_is_a_single_literal_argv_element(self, payload: str, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.lint.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["clang-tidy"], returncode=0)
            lint.run_lint([payload], project_root, timeout_seconds=30)

        argv = mock_run.call_args.args[0]
        expected = str((project_root / payload).resolve())
        assert expected in argv
        assert argv.count(expected) == 1


class TestActualTraversalOrAbsolutePathsAreRejectedNotExecuted:
    """Real traversal/absolute-path attempts (as opposed to merely
    metacharacter-laden filenames) must be rejected by path_safety before
    any subprocess call - see tests/tools/test_path_validation.py for the
    exhaustive version of this; this just proves the handler wiring stops
    before ever calling subprocess.run."""

    @pytest.mark.parametrize("payload", ["../../etc/passwd", "/etc/passwd"])
    def test_lint_rejects_traversal_path_before_subprocess_runs(self, payload: str, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.lint.subprocess.run") as mock_run:
            with pytest.raises(ValueError):
                lint.run_lint([payload], project_root, timeout_seconds=30)
            mock_run.assert_not_called()

    def test_build_preset_with_malicious_string_is_rejected_before_subprocess_runs(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.build.subprocess.run") as mock_run:
            with pytest.raises(build.PresetValidationError):
                build.run_build("release; rm -rf /", project_root, timeout_seconds=30)
            mock_run.assert_not_called()
