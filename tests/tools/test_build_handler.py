"""Tests for `services.tools.handlers.build` (and the shared pipeline
`test.py` reuses).

All subprocess execution is mocked - these tests never need a real `cmake`
or `ctest` binary installed, per the task's testability requirement.
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

from services.tools.handlers import build  # noqa: E402


def _completed(argv: list[str], returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=argv, returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    (root / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.22)\n")
    return root


class TestPresetValidation:
    def test_valid_presets_accepted(self) -> None:
        assert build.validate_preset("release") == {"CMAKE_BUILD_TYPE": "Release"}
        assert build.validate_preset("debug") == {"CMAKE_BUILD_TYPE": "Debug"}

    @pytest.mark.parametrize(
        "bad_preset",
        [
            "release; rm -rf /",
            "../../etc/passwd",
            "RELEASE",
            "",
            "prod",
            "$(whoami)",
            123,  # not even a string
        ],
    )
    def test_invalid_preset_rejected(self, bad_preset) -> None:
        with pytest.raises(build.PresetValidationError):
            build.validate_preset(bad_preset)

    def test_invalid_preset_rejected_before_any_subprocess_runs(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.build.subprocess.run") as mock_run:
            with pytest.raises(build.PresetValidationError):
                build.run_build("not-a-real-preset", project_root, timeout_seconds=30)
            mock_run.assert_not_called()


class TestScratchDirIsolation:
    def test_two_sequential_builds_use_different_scratch_dirs(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.build.subprocess.run") as mock_run:
            mock_run.side_effect = lambda argv, **kwargs: _completed(argv, returncode=0, stdout="ok")

            first = build.run_build("release", project_root, timeout_seconds=30)
            second = build.run_build("release", project_root, timeout_seconds=30)

        assert first["scratch_dir"] != second["scratch_dir"]

    def test_scratch_dir_is_cleaned_up_after_success(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.build.subprocess.run") as mock_run:
            mock_run.side_effect = lambda argv, **kwargs: _completed(argv, returncode=0, stdout="ok")
            response = build.run_build("release", project_root, timeout_seconds=30)

        assert not Path(response["scratch_dir"]).exists()

    def test_scratch_dir_is_cleaned_up_even_on_failure(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.build.subprocess.run") as mock_run:
            mock_run.side_effect = lambda argv, **kwargs: _completed(argv, returncode=1, stderr="configure failed")
            response = build.run_build("release", project_root, timeout_seconds=30)

        assert response["ok"] is False
        assert not Path(response["scratch_dir"]).exists()

    def test_neither_build_leaks_into_the_others_argv(self, project_root: Path) -> None:
        seen_build_dirs: list[str] = []

        def fake_run(argv, **kwargs):
            if argv[0] == "cmake" and "-B" in argv:
                seen_build_dirs.append(argv[argv.index("-B") + 1])
            return _completed(argv, returncode=0, stdout="ok")

        with mock.patch("services.tools.handlers.build.subprocess.run", side_effect=fake_run):
            build.run_build("release", project_root, timeout_seconds=30)
            build.run_build("release", project_root, timeout_seconds=30)

        assert len(seen_build_dirs) == 2
        assert seen_build_dirs[0] != seen_build_dirs[1]


class TestArgvConstruction:
    def test_configure_then_build_invoked_as_argv_lists(self, project_root: Path) -> None:
        calls: list[list[str]] = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return _completed(argv, returncode=0, stdout="ok")

        with mock.patch("services.tools.handlers.build.subprocess.run", side_effect=fake_run):
            build.run_build("release", project_root, timeout_seconds=30)

        assert len(calls) == 2
        configure_call, build_call = calls
        assert configure_call[0] == "cmake"
        assert "-S" in configure_call and str(project_root) in configure_call
        assert "-DCMAKE_BUILD_TYPE=Release" in configure_call
        assert build_call[:2] == ["cmake", "--build"]

    def test_build_step_skipped_when_configure_fails(self, project_root: Path) -> None:
        calls: list[list[str]] = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return _completed(argv, returncode=1, stderr="configure error")

        with mock.patch("services.tools.handlers.build.subprocess.run", side_effect=fake_run):
            response = build.run_build("release", project_root, timeout_seconds=30)

        assert len(calls) == 1  # build never ran
        assert response["ok"] is False
        assert response["exit_code"] == 1


class TestTimeoutEnforcement:
    def test_timeout_is_reported_with_conventional_exit_code(self, project_root: Path) -> None:
        def fake_run(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 30))

        with mock.patch("services.tools.handlers.build.subprocess.run", side_effect=fake_run):
            response = build.run_build("release", project_root, timeout_seconds=1)

        assert response["ok"] is False
        assert response["exit_code"] == build.TIMEOUT_EXIT_CODE
        assert "timed out" in response["stderr"]

    def test_timeout_value_is_passed_through_to_subprocess_run(self, project_root: Path) -> None:
        captured_timeouts: list[int] = []

        def fake_run(argv, **kwargs):
            captured_timeouts.append(kwargs["timeout"])
            return _completed(argv, returncode=0)

        with mock.patch("services.tools.handlers.build.subprocess.run", side_effect=fake_run):
            build.run_build("release", project_root, timeout_seconds=42)

        assert all(t == 42 for t in captured_timeouts)

    def test_timeout_during_build_step_does_not_run_further_steps(self, project_root: Path) -> None:
        call_count = {"n": 0}

        def fake_run(argv, **kwargs):
            call_count["n"] += 1
            if argv[0] == "cmake" and argv[1] == "--build":
                raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 30))
            return _completed(argv, returncode=0, stdout="configured")

        with mock.patch("services.tools.handlers.build.subprocess.run", side_effect=fake_run):
            response = build.run_build("release", project_root, timeout_seconds=5)

        assert call_count["n"] == 2  # configure, then build (which timed out) - no third step
        assert response["exit_code"] == build.TIMEOUT_EXIT_CODE


class TestShellIsNeverUsed:
    def test_subprocess_run_never_called_with_shell_true(self, project_root: Path) -> None:
        with mock.patch("services.tools.handlers.build.subprocess.run") as mock_run:
            mock_run.side_effect = lambda argv, **kwargs: _completed(argv, returncode=0)
            build.run_build("release", project_root, timeout_seconds=30)

        for call in mock_run.call_args_list:
            assert call.kwargs.get("shell") is False
