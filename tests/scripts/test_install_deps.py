"""Unit tests for scripts/install_deps.py. Commands are planned, never executed."""

from __future__ import annotations

import sys
from pathlib import Path

import install_deps


def test_requirement_paths_include_the_four_service_files():
    paths = install_deps.requirement_paths(trainer=False)
    assert [path.name for path in paths] == ["requirements.txt"] * 4
    assert any(path.parent.name == "agent" for path in paths)
    assert all(path.is_file() for path in paths)


def test_requirement_paths_append_trainer_when_asked():
    paths = install_deps.requirement_paths(trainer=True)
    assert paths[-1].parent.name == "trainer"


def test_venv_python_uses_scripts_on_windows():
    assert install_deps.venv_python(Path("C:/repo/.venv"), platform="nt") == Path("C:/repo/.venv/Scripts/python.exe")


def test_venv_python_uses_bin_on_posix():
    expected = Path("/repo/.venv/bin/python")
    assert install_deps.venv_python(expected.parent.parent, platform="posix") == expected


def test_resolve_venv_dir_keeps_a_windows_env_away_from_wsl(tmp_path):
    native = tmp_path / ".venv"
    native.mkdir()
    (native / "pyvenv.cfg").write_text(
        "home = C:\\Users\\local\\AppData\\Local\\Programs\\Python\\Python311\n",
        encoding="utf-8",
    )
    assert install_deps.resolve_venv_dir(tmp_path, platform="posix") == tmp_path / ".venv-wsl"
    assert install_deps.resolve_venv_dir(tmp_path, platform="nt") == native


def test_venv_create_on_drvfs_copies_binaries():
    cmd = install_deps.venv_create_command(Path("/usr/bin/python3"), Path("/mnt/d/rest/pumba/.venv"))
    assert "--copies" in cmd


def test_explain_venv_failure_names_the_apt_package():
    message = install_deps.explain_venv_failure("ensurepip is not available")
    assert message is not None
    assert "python3." in message
    assert "apt install" in message


def test_planned_commands_create_a_missing_venv(tmp_path, monkeypatch):
    monkeypatch.setattr(install_deps, "VENV_DIR", tmp_path / ".venv")
    commands = install_deps.planned_commands(system=False, trainer=False, upgrade=False)
    assert commands[0] == [sys.executable, "-m", "venv", str(tmp_path / ".venv")]
    assert commands[1][1:4] == ["-m", "pip", "install"]
    assert commands[1][-1] == "pip"
    joined = " ".join(commands[2])
    assert "services/agent/requirements.txt" in joined.replace("\\", "/")
    assert "--upgrade" in commands[1]
    assert "ruff==0.14.10" in commands[2]


def test_planned_commands_system_skips_venv_and_can_upgrade(tmp_path):
    python = tmp_path / "python"
    commands = install_deps.planned_commands(system=True, trainer=True, upgrade=True, python=python)
    assert all("venv" not in cmd for cmd in commands)
    joined = " ".join(commands[-1])
    assert "services/trainer/requirements.txt" in joined.replace("\\", "/")
    assert commands[-1].count("--upgrade") == 1


def test_dry_run_does_not_invoke_pip(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("subprocess should not run")

    monkeypatch.setattr(install_deps.subprocess, "run", fail)
    assert install_deps.main(["--dry-run", "--system"]) == 0
