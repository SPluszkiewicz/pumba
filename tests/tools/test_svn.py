"""Read-only svn handlers. The svn binary is never invoked."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.tools.handlers import svn
from services.tools.handlers.path_safety import PathSafetyError


def _completed(argv, stdout=""):
    return subprocess.CompletedProcess(args=argv, returncode=0, stdout=stdout, stderr="")


class TestSvn:
    @pytest.mark.parametrize("bad", ["-r", "HEAD:1", "1:2", "../x", "", "BASE HEAD"])
    def test_invalid_revision_rejected(self, bad: str) -> None:
        with pytest.raises(svn.SvnValidationError):
            svn.validate_rev(bad)

    def test_valid_revisions(self) -> None:
        assert svn.validate_rev("BASE") == "BASE"
        assert svn.validate_rev("12") == "12"
        assert svn.validate_rev("PREV") == "PREV"

    def test_status_argv_is_a_list(self, tmp_path: Path) -> None:
        (tmp_path / ".svn").mkdir()
        with mock.patch("services.tools.handlers.svn.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["svn"], stdout="M src/a.cpp\n")
            result = svn.run_svn_status(tmp_path, 10, raw_path=None)
        argv = mock_run.call_args.args[0]
        assert argv[:4] == ["svn", "status", "--non-interactive", "--ignore-externals"]
        assert mock_run.call_args.kwargs["shell"] is False
        assert result["ok"] is True

    def test_diff_without_revisions_is_the_changelist(self, tmp_path: Path) -> None:
        (tmp_path / ".svn").mkdir()
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.cpp").write_text("int a;\n")
        with mock.patch("services.tools.handlers.svn.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["svn"])
            svn.run_svn_diff(tmp_path, 10, raw_path="src/a.cpp", rev_from=None, rev_to=None)
        argv = mock_run.call_args.args[0]
        assert argv[0] == "svn" and "diff" in argv and "--git" in argv and "--" in argv
        assert not any(part.startswith("-r") for part in argv)
        assert argv[-1] == "src/a.cpp"

    def test_diff_range_is_one_argv_token(self, tmp_path: Path) -> None:
        (tmp_path / ".svn").mkdir()
        with mock.patch("services.tools.handlers.svn.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["svn"])
            svn.run_svn_diff(tmp_path, 10, raw_path=None, rev_from="BASE", rev_to="PREV")
        argv = mock_run.call_args.args[0]
        assert "-rBASE:PREV" in argv

    def test_one_sided_range_rejected_before_subprocess(self, tmp_path: Path) -> None:
        (tmp_path / ".svn").mkdir()
        with mock.patch("services.tools.handlers.svn.subprocess.run") as mock_run:
            with pytest.raises(svn.SvnValidationError):
                svn.run_svn_diff(tmp_path, 10, raw_path=None, rev_from="1", rev_to=None)
            mock_run.assert_not_called()

    def test_missing_svn_dir_raises_before_subprocess(self, tmp_path: Path) -> None:
        with mock.patch("services.tools.handlers.svn.subprocess.run") as mock_run:
            with pytest.raises(svn.SvnValidationError):
                svn.run_svn_status(tmp_path, 10, raw_path=None)
            mock_run.assert_not_called()

    def test_path_escape_rejected(self, tmp_path: Path) -> None:
        (tmp_path / ".svn").mkdir()
        with mock.patch("services.tools.handlers.svn.subprocess.run") as mock_run:
            with pytest.raises(PathSafetyError):
                svn.run_svn_diff(tmp_path, 10, raw_path="../secret", rev_from=None, rev_to=None)
            mock_run.assert_not_called()
