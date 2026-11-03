"""Unit tests for scripts/fix_permissions.py. chmod is never really invoked."""

from __future__ import annotations

from pathlib import Path

import fix_permissions


def test_to_linux_path_maps_windows_drives():
    assert fix_permissions.to_linux_path(Path("D:/svn/mon_projet")) == "/mnt/d/svn/mon_projet"
    assert fix_permissions.to_linux_path(Path(r"C:\rest\local_llm\models")) == "/mnt/c/rest/local_llm/models"


def test_to_linux_path_leaves_posix_paths():
    assert fix_permissions.to_linux_path(Path("/home/dev/app")) == "/home/dev/app"


def test_collect_targets_puts_models_last_and_writable(tmp_path):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "models").mkdir()
    (repo / "mkdocs.yml").write_text("site_name: x\n", encoding="utf-8")
    codebase = repo / "app"
    codebase.mkdir()

    targets = fix_permissions.collect_targets(repo, codebase)
    assert [target.label for target in targets] == ["codebase", "docs", "mkdocs.yml", "models"]
    assert targets[-1].writable is True
    assert all(not target.writable for target in targets[:-1])


def test_collect_targets_does_not_chmod_models_read_only_when_it_is_the_codebase(tmp_path):
    repo = tmp_path / "repo"
    models = repo / "models"
    models.mkdir(parents=True)
    targets = fix_permissions.collect_targets(repo, models)
    assert [target.label for target in targets] == ["docs", "mkdocs.yml", "models"]
    assert targets[-1].writable is True


def test_private_files_are_env_files_only(tmp_path):
    (tmp_path / ".env").write_text("TOKEN=1\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("TOKEN=2\n", encoding="utf-8")
    (tmp_path / "readme.md").write_text("no\n", encoding="utf-8")
    nested = tmp_path / ".git"
    nested.mkdir()
    (nested / ".env").write_text("hidden\n", encoding="utf-8")

    found = fix_permissions.private_files_under(tmp_path)
    assert {path.name for path in found} == {".env", ".env.local"}


def test_command_plan_on_windows_uses_the_podman_machine():
    plan = fix_permissions.command_plan(
        Path(r"D:\rest\local_llm\models"), "a+rwX", recursive=True, platform="nt"
    )
    assert plan[0][:4] == ["podman", "machine", "ssh", "--"]
    assert "unshare" in plan[0]
    assert plan[0][-2:] == ["a+rwX", "/mnt/d/rest/local_llm/models"]
    assert plan[2][0] == "wsl"


def test_command_plan_on_drvfs_skips_podman_unshare():
    plan = fix_permissions.command_plan(Path("/mnt/d/rest/local_llm/models"), "a+rwX", recursive=True, platform="posix")
    assert plan == [["chmod", "-R", "a+rwX", "/mnt/d/rest/local_llm/models"]]


def test_run_plan_drvfs_eperm_warns_and_continues(monkeypatch, capsys):
    def fake_run(cmd, **_kwargs):
        assert cmd[0] == "chmod"
        return type("Result", (), {
            "returncode": 1,
            "stderr": "chmod: changing permissions of '/mnt/d/x': Operation not permitted\n",
            "stdout": "",
        })()

    monkeypatch.setattr(fix_permissions.subprocess, "run", fake_run)
    fix_permissions.run_plan([["chmod", "-R", "a+rwX", "/mnt/d/rest/local_llm/models"]], dry_run=False)
    assert "Windows drive mount rejects chmod" in capsys.readouterr().out


def test_command_plan_on_posix_prefers_podman_unshare():
    path = Path("/srv/models")
    plan = fix_permissions.command_plan(path, "a+rX", recursive=False, platform="posix")
    assert plan[0] == ["podman", "unshare", "chmod", "a+rX", str(path)]
    assert plan[1] == ["chmod", "a+rX", str(path)]


def test_run_plan_falls_through_until_one_command_works(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            raise FileNotFoundError(cmd[0])
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(fix_permissions.subprocess, "run", fake_run)
    fix_permissions.run_plan([["podman", "unshare", "chmod"], ["chmod", "a+rX", "/tmp"]], dry_run=False)
    assert len(calls) == 2


def test_run_plan_dry_run_prints_only_the_preferred_command(capsys):
    fix_permissions.run_plan([["chmod", "a+rX", "/tmp/app"], ["other"]], dry_run=True)
    assert "chmod a+rX /tmp/app" in capsys.readouterr().out


def test_apply_target_locks_env_back_down(tmp_path, monkeypatch):
    tree = tmp_path / "app"
    tree.mkdir()
    secret = tree / ".env"
    secret.write_text("TOKEN=1\n", encoding="utf-8")
    seen: list[tuple[str, bool]] = []

    def fake_run(commands, *, dry_run):
        seen.append((commands[0][-2], dry_run))

    monkeypatch.setattr(fix_permissions, "run_plan", fake_run)
    fix_permissions.apply_target(
        fix_permissions.Target(tree, writable=False, recursive=True, label="codebase"),
        dry_run=True,
    )
    assert seen == [("a+rX", True), ("600", True)]


def test_main_dry_run_does_not_chmod(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "app").mkdir()
    (repo / "mkdocs.yml").write_text("site_name: x\n", encoding="utf-8")
    monkeypatch.setattr(fix_permissions, "REPO_ROOT", repo)
    monkeypatch.setattr(fix_permissions, "MODELS_DIR", repo / "models")
    monkeypatch.setattr(fix_permissions, "resolve_codebase", lambda _raw: repo / "app")

    def fail(*_args, **_kwargs):
        raise AssertionError("subprocess should not run")

    monkeypatch.setattr(fix_permissions.subprocess, "run", fail)
    assert fix_permissions.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would create" in out
    assert "a+rwX" in out
    assert "a+rX" in out
