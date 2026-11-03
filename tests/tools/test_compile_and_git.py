"""compile_file, ctest -R filter, and read-only git handlers (subprocess mocked)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.tools.handlers import build as build_handler
from services.tools.handlers import compile as compile_handler
from services.tools.handlers import git
from services.tools.handlers import test as test_handler
from services.tools.handlers.build import PresetValidationError
from services.tools.handlers.path_safety import PathSafetyError


def _completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=argv, returncode=returncode, stdout=stdout, stderr=stderr)


class TestCtestFilter:
    def test_valid_filter_is_appended_as_minus_r(self, tmp_path: Path) -> None:
        argv = test_handler._ctest_argv(tmp_path, "json_round_trip")
        assert argv[-2:] == ["-R", "json_round_trip"]
        assert argv[0] == "ctest"

    @pytest.mark.parametrize("bad", ["foo; rm", "-R all", "a b", "$(x)", ""])
    def test_bad_filter_rejected_before_subprocess(self, tmp_path: Path, bad: str) -> None:
        if bad == "":
            assert test_handler.validate_filter(bad) is None
            return
        with pytest.raises(test_handler.TestFilterError):
            test_handler.validate_filter(bad)
        with mock.patch("services.tools.handlers.build.subprocess.run") as mock_run:
            with pytest.raises(test_handler.TestFilterError):
                test_handler.run_test("release", tmp_path, 30, filt=bad)
            mock_run.assert_not_called()


class TestCompileFile:
    def test_uses_cmake_target_from_compile_commands(self, tmp_path: Path) -> None:
        root = tmp_path / "app"
        root.mkdir()
        (root / "src").mkdir()
        (root / "src" / "foo.cpp").write_text("int x;\n")
        cache = tmp_path / "cmake-cache" / build_handler.root_cache_key(root) / "release"
        cache.mkdir(parents=True)
        (cache / "CMakeCache.txt").write_text("# cached\n")
        (cache / "compile_commands.json").write_text(
            json.dumps(
                [
                    {
                        "directory": str(cache),
                        "file": str((root / "src" / "foo.cpp").resolve()),
                        "output": "CMakeFiles/lib.dir/src/foo.cpp.o",
                        "command": "c++ -c foo.cpp",
                    }
                ]
            )
        )
        calls: list[list[str]] = []

        def _run(argv, **_k):
            calls.append(argv)
            return _completed(argv)

        with mock.patch("services.tools.handlers.build._scratch_base", return_value=str(tmp_path)):
            with mock.patch("services.tools.handlers.build.subprocess.run", side_effect=_run):
                result = compile_handler.run_compile_file("src/foo.cpp", "release", root, 30)

        assert result["ok"] is True
        assert calls[0][:4] == ["cmake", "--build", str(cache), "--target"]
        assert "foo.cpp.o" in calls[0][4]

    def test_cache_is_keyed_by_project_root(self, tmp_path: Path) -> None:
        """The default mount and a registered checkout share one scratch tmpfs;
        a CMakeCache configured for one tree must never be reused for another."""
        root_a = tmp_path / "app"
        root_b = tmp_path / "checkouts" / "repo_1"
        root_a.mkdir()
        root_b.mkdir(parents=True)
        with mock.patch("services.tools.handlers.build._scratch_base", return_value=str(tmp_path)):
            dir_a = build_handler.persistent_build_dir("release", root_a)
            dir_b = build_handler.persistent_build_dir("release", root_b)
            dir_a_debug = build_handler.persistent_build_dir("debug", root_a)
        assert dir_a != dir_b
        assert dir_a != dir_a_debug
        assert dir_a.is_relative_to(tmp_path / "cmake-cache")
        assert build_handler.root_cache_key(root_a) == build_handler.root_cache_key(root_a)

    def test_rejects_non_source_before_subprocess(self, tmp_path: Path) -> None:
        with mock.patch("services.tools.handlers.build.subprocess.run") as mock_run:
            with pytest.raises(compile_handler.CompileValidationError):
                compile_handler.run_compile_file("README.md", "release", tmp_path, 30)
            mock_run.assert_not_called()

    def test_unknown_preset_rejected_before_subprocess(self, tmp_path: Path) -> None:
        with mock.patch("services.tools.handlers.build.subprocess.run") as mock_run:
            with pytest.raises(PresetValidationError):
                compile_handler.run_compile_file("src/foo.cpp", "nope", tmp_path, 30)
            mock_run.assert_not_called()


class TestGit:
    def test_invalid_ref_rejected(self) -> None:
        for ref in ("-C", "HEAD;rm", "main..feat", "../x", "HEAD^{}", ""):
            with pytest.raises(git.GitValidationError):
                git.validate_ref(ref)

    def test_valid_refs(self) -> None:
        assert git.validate_ref("HEAD") == "HEAD"
        assert git.validate_ref("HEAD~3") == "HEAD~3"
        assert git.validate_ref("abc1234") == "abc1234"
        assert git.validate_ref("feature/foo") == "feature/foo"

    def test_log_argv_is_a_list_without_shell(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.cpp").write_text("int a;\n")
        with mock.patch("services.tools.handlers.git.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["git"], stdout="abc\n")
            git.run_git_log(tmp_path, 10, raw_path="src/a.cpp", max_count=5)
        argv = mock_run.call_args.args[0]
        assert argv[0] == "git" and "--" in argv
        assert mock_run.call_args.kwargs["shell"] is False
        assert "-n5" in argv

    def test_missing_git_dir_raises_before_subprocess(self, tmp_path: Path) -> None:
        with mock.patch("services.tools.handlers.git.subprocess.run") as mock_run:
            with pytest.raises(git.GitValidationError, match="not a git"):
                git.run_git_log(tmp_path, 10, raw_path=None, max_count=5)
            mock_run.assert_not_called()

    def test_diff_uses_two_validated_refs(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        with mock.patch("services.tools.handlers.git.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["git"])
            git.run_git_diff(tmp_path, 10, ref_a="HEAD~1", ref_b="HEAD", raw_path=None)
        argv = mock_run.call_args.args[0]
        assert "HEAD~1" in argv and "HEAD" in argv
        assert "--" in argv

    def test_traversal_path_rejected(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        with mock.patch("services.tools.handlers.git.subprocess.run") as mock_run:
            with pytest.raises(PathSafetyError):
                git.run_git_blame(tmp_path, 10, raw_path="../etc/passwd", start_line=1, end_line=2)
            mock_run.assert_not_called()
