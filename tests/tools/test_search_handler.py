"""Tests for `services.tools.handlers.search`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.tools.handlers import search  # noqa: E402


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    return root


def _completed(argv, returncode=0, stdout="", stderr="") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=argv, returncode=returncode, stdout=stdout, stderr=stderr)


class TestPatternValidation:
    @pytest.mark.parametrize("bad_pattern", ["", "   ", "\t\n", None, 123])
    def test_empty_or_invalid_pattern_rejected(self, bad_pattern, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.search.subprocess.run") as mock_run:
            with pytest.raises(search.SearchValidationError):
                search.run_search(bad_pattern, False, project_root, timeout_seconds=30)
            mock_run.assert_not_called()


class TestSearchRootIsAlwaysProjectRoot:
    def test_project_root_is_the_search_path_argument(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.search.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["rg"], returncode=1)
            search.run_search("needle", False, project_root, timeout_seconds=30)

        argv = mock_run.call_args.args[0]
        assert argv[-1] == str(project_root)

    def test_caller_cannot_widen_the_search_root(self, project_root: Path) -> None:
        # There is no way to pass a custom root through the public
        # function signature at all - this test documents/locks that in.
        with mock.patch("services.tools.handlers.search.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["rg"], returncode=1)
            search.run_search("needle", False, project_root, timeout_seconds=30)

        argv = mock_run.call_args.args[0]
        assert "/" not in argv[:-1]  # no stray root-like argv element before the trailing search path
        assert argv[-1] == str(project_root)


class TestFixedStringsFlag:
    def test_fixed_strings_flag_added_when_regex_false(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.search.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["rg"], returncode=1)
            search.run_search("literal.pattern", False, project_root, timeout_seconds=30)

        argv = mock_run.call_args.args[0]
        assert "--fixed-strings" in argv

    def test_fixed_strings_flag_absent_when_regex_true(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.search.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["rg"], returncode=1)
            search.run_search("a.*b", True, project_root, timeout_seconds=30)

        argv = mock_run.call_args.args[0]
        assert "--fixed-strings" not in argv


class TestExitCodeHandling:
    def test_matches_found_is_ok(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.search.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["rg"], returncode=0, stdout="match\n")
            response = search.run_search("needle", False, project_root, timeout_seconds=30)
        assert response["ok"] is True
        assert response["exit_code"] == 0

    def test_no_matches_is_still_ok(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.search.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["rg"], returncode=1, stdout="")
            response = search.run_search("needle", False, project_root, timeout_seconds=30)
        assert response["ok"] is True
        assert response["exit_code"] == 1

    def test_real_rg_error_is_not_ok(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.search.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["rg"], returncode=2, stderr="regex parse error")
            response = search.run_search("[", True, project_root, timeout_seconds=30)
        assert response["ok"] is False
        assert response["exit_code"] == 2


class TestTimeoutEnforcement:
    def test_timeout_reported(self, project_root: Path) -> None:
        def fake_run(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 30))

        with mock.patch("services.tools.handlers.search.subprocess.run", side_effect=fake_run):
            response = search.run_search("needle", False, project_root, timeout_seconds=1)

        assert response["ok"] is False
        assert "timed out" in response["stderr"]


class TestBoundedOutput:
    def _many_matches(self, n: int) -> str:
        return "".join(f"src/f{i}.cpp:{i}:needle\n" for i in range(n))

    def test_default_cap_applies_and_reports_truncation(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.search.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["rg"], returncode=0, stdout=self._many_matches(500))
            response = search.run_search("needle", False, project_root, timeout_seconds=30)
        assert response["truncated"] is True
        assert response["total_matches"] == 500
        assert len(response["stdout"].splitlines()) == search.DEFAULT_MAX_RESULTS
        assert "truncated" in response["stderr"]
        assert response["ok"] is True

    def test_under_cap_is_untouched(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.search.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["rg"], returncode=0, stdout=self._many_matches(3))
            response = search.run_search("needle", False, project_root, timeout_seconds=30)
        assert response["truncated"] is False
        assert response["total_matches"] == 3
        assert response["stderr"] == ""

    def test_caller_max_results_is_honoured_up_to_hard_cap(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.search.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["rg"], returncode=0, stdout=self._many_matches(500))
            response = search.run_search("needle", False, project_root, timeout_seconds=30, max_results=10_000)
        assert len(response["stdout"].splitlines()) == search.HARD_MAX_RESULTS

    @pytest.mark.parametrize("bad", [0, -1, "10", True])
    def test_invalid_max_results_rejected(self, project_root: Path, bad) -> None:
        with mock.patch("services.tools.handlers.search.subprocess.run") as mock_run:
            with pytest.raises(search.SearchValidationError):
                search.run_search("needle", False, project_root, timeout_seconds=30, max_results=bad)
            mock_run.assert_not_called()

    def test_max_columns_flag_present(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.search.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["rg"], returncode=1)
            search.run_search("needle", False, project_root, timeout_seconds=30)
        assert f"--max-columns={search.MAX_LINE_COLUMNS}" in mock_run.call_args.args[0]


class TestNoShell:
    def test_never_invoked_with_shell_true(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.search.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["rg"], returncode=1)
            search.run_search("needle", False, project_root, timeout_seconds=30)

        assert mock_run.call_args.kwargs.get("shell") is False
