"""Tests for `services.tools.handlers.list_files`."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.tools.handlers import list_files  # noqa: E402
from services.tools.handlers.path_safety import PathSafetyError  # noqa: E402


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    (root / "src").mkdir(parents=True)
    (root / "include").mkdir()
    (root / "src" / "main.cpp").write_text("int main() {}\n")
    (root / "src" / "AgentClient.cpp").write_text("\n")
    (root / "include" / "AgentClient.hpp").write_text("\n")
    (root / "CMakeLists.txt").write_text("project(demo)\n")
    return root


def _completed(argv, returncode=0, stdout="", stderr="") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=argv, returncode=returncode, stdout=stdout, stderr=stderr)


class TestArgv:
    def test_root_listing_uses_dot_and_cwd_root(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.list_files.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["rg"], returncode=0, stdout="")
            list_files.run_list_files(project_root, 30)
        argv = mock_run.call_args.args[0]
        assert argv[:3] == ["rg", "--files", "--color=never"]
        assert argv[-1] == "."
        assert mock_run.call_args.kwargs["cwd"] == str(project_root.resolve())
        assert mock_run.call_args.kwargs["shell"] is False

    def test_glob_is_its_own_argv_element(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.list_files.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["rg"], returncode=0, stdout="")
            list_files.run_list_files(project_root, 30, glob="*.hpp; rm -rf /")
        argv = mock_run.call_args.args[0]
        idx = argv.index("--glob")
        assert argv[idx + 1] == "*.hpp; rm -rf /"

    def test_subdirectory_is_validated_and_passed_relative(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.list_files.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["rg"], returncode=0, stdout="")
            response = list_files.run_list_files(project_root, 30, raw_path="src")
        assert mock_run.call_args.args[0][-1] == "src"
        assert response["path"] == "src"


class TestValidation:
    def test_traversal_rejected(self, project_root: Path) -> None:
        with pytest.raises(PathSafetyError):
            list_files.run_list_files(project_root, 30, raw_path="../")

    def test_file_path_rejected(self, project_root: Path) -> None:
        with pytest.raises(list_files.NotADirectoryError):
            list_files.run_list_files(project_root, 30, raw_path="src/main.cpp")

    def test_missing_dir_is_not_found(self, project_root: Path) -> None:
        with pytest.raises(FileNotFoundError):
            list_files.run_list_files(project_root, 30, raw_path="nope")

    @pytest.mark.parametrize("bad", [0, -3, True, "5"])
    def test_bad_max_results(self, project_root: Path, bad) -> None:
        with pytest.raises(list_files.ListFilesValidationError):
            list_files.run_list_files(project_root, 30, max_results=bad)

    def test_empty_glob_rejected(self, project_root: Path) -> None:
        with pytest.raises(list_files.ListFilesValidationError):
            list_files.run_list_files(project_root, 30, glob="   ")


class TestBoundedOutput:
    def test_sorted_normalised_and_capped(self, project_root: Path) -> None:
        raw = "./src/b.cpp\n./src/a.cpp\n.\\include\\x.hpp\n" + "".join(f"./gen/{i}.cpp\n" for i in range(10))
        with mock.patch("services.tools.handlers.list_files.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["rg"], returncode=0, stdout=raw)
            response = list_files.run_list_files(project_root, 30, max_results=5)
        assert response["files"] == sorted(response["files"])
        assert len(response["files"]) == 5
        assert response["total_files"] == 13
        assert response["truncated"] is True
        assert all(not f.startswith("./") and "\\" not in f for f in response["files"])
        assert "truncated" in response["stderr"]

    def test_hard_cap(self, project_root: Path) -> None:
        raw = "".join(f"f{i}.cpp\n" for i in range(list_files.HARD_MAX_RESULTS + 50))
        with mock.patch("services.tools.handlers.list_files.subprocess.run") as mock_run:
            mock_run.return_value = _completed(["rg"], returncode=0, stdout=raw)
            response = list_files.run_list_files(project_root, 30, max_results=10_000)
        assert len(response["files"]) == list_files.HARD_MAX_RESULTS

    def test_timeout_reported(self, project_root: Path) -> None:
        def fake_run(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

        with mock.patch("services.tools.handlers.list_files.subprocess.run", side_effect=fake_run):
            response = list_files.run_list_files(project_root, 1)
        assert response["ok"] is False
        assert "timed out" in response["stderr"]
        assert response["files"] == []


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed on this host")
class TestWithRealRipgrep:
    def test_lists_project_files(self, project_root: Path) -> None:
        response = list_files.run_list_files(project_root, 30)
        assert response["ok"] is True
        assert set(response["files"]) == {
            "CMakeLists.txt",
            "include/AgentClient.hpp",
            "src/AgentClient.cpp",
            "src/main.cpp",
        }

    def test_glob_and_subdir(self, project_root: Path) -> None:
        response = list_files.run_list_files(project_root, 30, raw_path="src", glob="*.cpp")
        assert set(response["files"]) == {"src/AgentClient.cpp", "src/main.cpp"}

    def test_no_match_is_ok(self, project_root: Path) -> None:
        response = list_files.run_list_files(project_root, 30, glob="*.rs")
        assert response["ok"] is True
        assert response["files"] == []
