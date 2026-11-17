"""Unit tests for `scripts/deploy.py`.

`_run`/`subprocess.run`/`shutil.which` are always monkeypatched out - these
tests never invoke a real `podman` binary. What's under test is the command
sequencing and the safety checks (env file / token / health polling), which
is exactly the value this script adds over typing the raw `podman compose`
commands by hand.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import deploy
import product

# --------------------------------------------------------------------------- _require_podman

def test_require_podman_exits_when_missing_from_path(monkeypatch):
    monkeypatch.setattr(deploy.shutil, "which", lambda _name: None)

    with pytest.raises(SystemExit):
        deploy._require_podman()


def test_require_podman_exits_when_compose_unavailable(monkeypatch):
    monkeypatch.setattr(deploy.shutil, "which", lambda _name: "/usr/bin/podman")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1)
    )

    with pytest.raises(SystemExit):
        deploy._require_podman()


def test_require_podman_passes_when_available(monkeypatch):
    monkeypatch.setattr(deploy.shutil, "which", lambda _name: "/usr/bin/podman")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0)
    )

    deploy._require_podman()  # must not raise


# --------------------------------------------------------------------------- _ensure_env_file

def test_ensure_env_file_creates_from_example_and_fills_token(tmp_path, monkeypatch):
    example = tmp_path / ".env.example"
    example.write_text(
        "AGENT_BEARER_TOKEN=changeme-generate-a-real-secret\nLLM_PORT=8080\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    monkeypatch.setattr(deploy, "ENV_FILE", env)
    monkeypatch.setattr(deploy, "ENV_EXAMPLE", example)

    deploy._ensure_env_file()

    text = env.read_text(encoding="utf-8")
    assert "changeme-generate-a-real-secret" not in text
    assert "LLM_PORT=8080" in text
    token = next(
        line.split("=", 1)[1] for line in text.splitlines() if line.startswith("AGENT_BEARER_TOKEN=")
    )
    assert len(token) >= 32


def test_ensure_env_file_replaces_placeholder_token(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("AGENT_BEARER_TOKEN=changeme-generate-a-real-secret\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_FILE", env)

    deploy._ensure_env_file()

    text = env.read_text(encoding="utf-8")
    assert "changeme-generate-a-real-secret" not in text
    token = text.split("=", 1)[1].strip()
    assert len(token) >= 32


def test_ensure_env_file_passes_with_real_token(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("AGENT_BEARER_TOKEN=a-real-random-secret-value\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_FILE", env)

    deploy._ensure_env_file()
    deploy._ensure_env_file()
    assert env.read_text(encoding="utf-8") == "AGENT_BEARER_TOKEN=a-real-random-secret-value\n"


# --------------------------------------------------------------------------- _wait_healthy

def test_wait_healthy_returns_once_all_services_report_healthy(monkeypatch):
    monkeypatch.setattr(deploy.time, "sleep", lambda _s: None)
    ps_output = "\n".join(f"{product.COMPOSE_PROJECT}-{svc}-1  Up 2 minutes (healthy)" for svc in ["qdrant", "agent"])
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=ps_output, returncode=0)
    )

    deploy._wait_healthy(["qdrant", "agent"], timeout=10)  # must not raise


def test_wait_healthy_exits_on_timeout(monkeypatch):
    monkeypatch.setattr(deploy.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="nothing healthy here", returncode=0)
    )
    # Simulate the clock running out immediately without an actual sleep.
    ticks = iter([0, 100, 200])
    monkeypatch.setattr(deploy.time, "monotonic", lambda: next(ticks, 200))

    with pytest.raises(SystemExit):
        deploy._wait_healthy(["agent"], timeout=1)


# --------------------------------------------------------------------------- cmd_* sequencing

@pytest.fixture
def _no_real_podman(monkeypatch):
    """Only used by the cmd_* tests below - NOT by the _require_podman tests
    above, which need the real function to actually run."""
    monkeypatch.setattr(deploy, "_require_podman", lambda: None)
    monkeypatch.setattr(deploy, "_wait_published_doors", lambda **_k: None)


def _recorder(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(deploy, "_run", lambda cmd, **k: calls.append(cmd))
    return calls


def test_cmd_up_full_sequence(monkeypatch, _no_real_podman):
    calls = _recorder(monkeypatch)
    monkeypatch.setattr(deploy, "_ensure_env_file", lambda: None)
    monkeypatch.setattr(deploy, "_wait_healthy", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_prepare_gpu_for_up", lambda _env: None)
    monkeypatch.setattr(deploy, "_compose_build_and_up", lambda host=False: calls.append(["build_and_up"]))

    deploy.cmd_up(argparse.Namespace(skip_models=False, skip_index=False, timeout=300))

    assert ["podman", "compose", "--profile", "setup", "run", "--rm", "fetch-models"] in calls
    assert ["build_and_up"] in calls
    assert ["podman", "compose", "--profile", "index", "run", "--rm", "indexer"] in calls


def test_cmd_up_respects_skip_flags(monkeypatch, _no_real_podman):
    calls = _recorder(monkeypatch)
    monkeypatch.setattr(deploy, "_ensure_env_file", lambda: None)
    monkeypatch.setattr(deploy, "_wait_healthy", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_prepare_gpu_for_up", lambda _env: None)
    monkeypatch.setattr(deploy, "_compose_build_and_up", lambda host=False: calls.append(["build_and_up"]))

    deploy.cmd_up(argparse.Namespace(skip_models=True, skip_index=True, timeout=300))

    joined = [" ".join(c) for c in calls]
    assert not any("fetch-models" in c for c in joined)
    assert not any("indexer" in c for c in joined)
    assert "build_and_up" in joined


def test_host_profile_up_skips_the_llm_container(monkeypatch, _no_real_podman, tmp_path):
    calls = _recorder(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_SERVE=host\nLLM_GPU_ARG=0\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_FILE", env_file)
    monkeypatch.setattr(deploy, "_ensure_env_file", lambda: None)
    monkeypatch.setattr(deploy, "_require_bind_source", lambda: None)
    monkeypatch.setattr(deploy, "_prepare_gpu_for_up", lambda _env: None)
    monkeypatch.setattr(deploy, "_wait_healthy", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_require_host_llm_weights", lambda: None)
    monkeypatch.setattr(deploy, "_start_host_llm", lambda **_k: calls.append(["host-llm"]))

    deploy.cmd_up(argparse.Namespace(skip_models=True, skip_index=True, timeout=5))

    assert ["host-llm"] in calls
    up = next(cmd for cmd in calls if "up" in cmd)
    assert "--no-deps" in up
    assert "llm" not in up
    assert "patch" not in up
    assert "agent" in up


def test_cmd_status(monkeypatch, _no_real_podman):
    calls = _recorder(monkeypatch)
    deploy.cmd_status(argparse.Namespace())
    assert calls == [["podman", "compose", "ps"]]


def test_cmd_index(monkeypatch, _no_real_podman):
    calls = _recorder(monkeypatch)
    deploy.cmd_index(argparse.Namespace())
    assert calls == [
        ["podman", "compose", "up", "-d", "--force-recreate", "--no-deps", "tools", "agent", "ui-proxy"],
        ["podman", "compose", "--profile", "index", "run", "--rm", "indexer"],
    ]


def test_cmd_down_without_volumes(monkeypatch, _no_real_podman):
    calls = _recorder(monkeypatch)
    deploy.cmd_down(argparse.Namespace(volumes=False))
    assert calls == [["podman", "compose", "down"]]


def test_cmd_down_with_volumes(monkeypatch, _no_real_podman):
    calls = _recorder(monkeypatch)
    deploy.cmd_down(argparse.Namespace(volumes=True))
    assert calls == [["podman", "compose", "down", "-v"]]


def test_cmd_chat_runs_repl(monkeypatch, _no_real_podman):
    calls = _recorder(monkeypatch)
    deploy.cmd_chat(argparse.Namespace())
    assert calls == [["podman", "compose", "run", "--rm", "-it", "app", "--repl"]]


def test_cmd_ask_passes_question_through(monkeypatch, _no_real_podman):
    calls = _recorder(monkeypatch)
    deploy.cmd_ask(argparse.Namespace(question="does it build?", file=None))
    assert calls == [["podman", "compose", "run", "--rm", "app", "does it build?"]]


def test_cmd_ask_file_mounts_prompt(monkeypatch, tmp_path, _no_real_podman):
    prompt = tmp_path / "big.md"
    prompt.write_text("Explain the indexer in detail.\n", encoding="utf-8")
    calls = _recorder(monkeypatch)

    deploy.cmd_ask(argparse.Namespace(question=None, file=str(prompt)))

    assert len(calls) == 1
    assert calls[0][:4] == ["podman", "compose", "run", "--rm"]
    assert "-v" in calls[0]
    vol = calls[0][calls[0].index("-v") + 1]
    assert vol.endswith(":/prompt.txt:ro")
    assert calls[0][-2:] == ["--file", "/prompt.txt"]


def test_cmd_logs_follow_flag(monkeypatch, _no_real_podman):
    calls = _recorder(monkeypatch)
    deploy.cmd_logs(argparse.Namespace(service="agent", follow=True))
    assert calls == [["podman", "compose", "logs", "-f", "agent"]]


def test_cmd_logs_without_follow(monkeypatch, _no_real_podman):
    calls = _recorder(monkeypatch)
    deploy.cmd_logs(argparse.Namespace(service="agent", follow=False))
    assert calls == [["podman", "compose", "logs", "agent"]]


def test_cmd_train_dry_run_builds_dataset_only(monkeypatch, tmp_path, _no_real_podman):
    calls = _recorder(monkeypatch)
    monkeypatch.setattr(deploy, "ENV_FILE", tmp_path / ".env")
    deploy.ENV_FILE.write_text("AGENT_BEARER_TOKEN=real-secret\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "HF_TRAIN_BASE_MARKER", tmp_path / "hf" / "Qwen2.5-7B-Instruct" / "config.json")
    deploy.HF_TRAIN_BASE_MARKER.parent.mkdir(parents=True)
    deploy.HF_TRAIN_BASE_MARKER.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(deploy, "TRAIN_LOCK_FILE", tmp_path / "training" / ".train.lock")
    monkeypatch.setattr(deploy, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(deploy, "_ensure_env_file", lambda: None)

    deploy.cmd_train(
        argparse.Namespace(incremental=False, dry_run=True, skip_index=False, force=False)
    )

    joined = [" ".join(c) for c in calls]
    assert any("build_dataset.py" in c for c in joined)
    assert not any("--profile train run" in c for c in joined)


def test_cmd_test_starts_mcp_and_ui_proxy(monkeypatch, _no_real_podman):
    calls = _recorder(monkeypatch)
    waited: list[list[str]] = []
    monkeypatch.setattr(deploy, "_wait_healthy", lambda services, timeout: waited.append(list(services)))

    deploy.cmd_test(argparse.Namespace())

    up = next(cmd for cmd in calls if "up" in cmd and "-d" in cmd)
    for name in ("mcp", "ui-proxy"):
        assert name in up
        assert name in waited[0]
    assert "patch" not in up
    assert "patch" not in waited[0]
    build = next(cmd for cmd in calls if "build" in cmd)
    assert "patch" not in build


def test_cmd_test_reads_env_test_not_operator_env(monkeypatch, _no_real_podman):
    seen: list[str] = []

    def wait(_services, timeout=180):
        seen.append(str(deploy.ENV_FILE).replace("\\", "/"))

    monkeypatch.setattr(deploy, "_wait_healthy", wait)
    monkeypatch.setattr(deploy, "_run", lambda *_a, **_k: None)
    previous = deploy.ENV_FILE
    deploy.cmd_test(argparse.Namespace())
    assert seen and seen[0].endswith(".env.test")
    assert deploy.ENV_FILE == previous


def test_cmd_test_always_tears_down_even_if_smoke_test_fails(monkeypatch, _no_real_podman):
    calls = []

    def _fake_run(cmd, **_k):
        calls.append(cmd)
        if "smoke-test" in cmd:
            raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(deploy, "_run", _fake_run)
    monkeypatch.setattr(deploy, "_wait_healthy", lambda *a, **k: None)

    with pytest.raises(subprocess.CalledProcessError):
        deploy.cmd_test(argparse.Namespace())

    assert any("down" in c for c in calls), "teardown must still run after a failed smoke test"


# --------------------------------------------------------------------------- CODEBASE_PATH


def test_normalize_codebase_path_maps_windows_drive_to_wsl(monkeypatch):
    monkeypatch.setattr(deploy, "_host_os", lambda: "nt")
    monkeypatch.setattr(deploy, "_RUNTIME", "podman")
    assert deploy._normalize_codebase_path("d:/svn/mon_projet") == "/mnt/d/svn/mon_projet"
    assert deploy._normalize_codebase_path("D:\\svn\\mon_projet") == "/mnt/d/svn/mon_projet"


def test_normalize_codebase_path_leaves_posix_and_relative_unchanged(monkeypatch):
    monkeypatch.setattr(deploy, "_host_os", lambda: "nt")
    monkeypatch.setattr(deploy, "_RUNTIME", "podman")
    assert deploy._normalize_codebase_path("/mnt/d/svn/mon_projet") == "/mnt/d/svn/mon_projet"
    assert deploy._normalize_codebase_path("./app") == "./app"


def test_native_linux_does_not_invent_a_mount(monkeypatch):
    monkeypatch.setattr(deploy, "_host_os", lambda: "posix")
    monkeypatch.setattr(deploy, "_RUNTIME", "podman")
    monkeypatch.setattr("workspaces.running_in_wsl", lambda: False)
    assert deploy._normalize_codebase_path("/srv/checkouts/trunk") == "/srv/checkouts/trunk"
    assert deploy._normalize_codebase_path("d:/svn/mon_projet") == "d:/svn/mon_projet"


def test_bind_source_refuses_a_missing_directory(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("CODEBASE_PATH=/srv/checkouts/trunk\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_FILE", env)
    monkeypatch.setattr(deploy, "_codebase_path_exists", lambda _raw: False)

    with pytest.raises(SystemExit) as raised:
        deploy._require_bind_source()

    message = str(raised.value)
    assert "/srv/checkouts/trunk" in message
    assert "not created" in message


def test_compose_env_normalizes_codebase_path_from_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("CODEBASE_PATH=d:/svn/mon_projet\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_FILE", env)
    monkeypatch.setattr(deploy, "_host_os", lambda: "nt")
    monkeypatch.setattr(deploy, "_RUNTIME", "podman")
    monkeypatch.setenv("CODEBASE_PATH", "should-be-overridden")
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)

    result = deploy._compose_env()

    assert result["CODEBASE_PATH"] == "/mnt/d/svn/mon_projet"
    assert result["COMPOSE_PROJECT_NAME"] == product.COMPOSE_PROJECT


# --------------------------------------------------------------------------- GPU / CDI

def _repo_without_workspace_overlay(tmp_path, monkeypatch):
    """`_compose_cmd` adds state/compose.workspaces.yaml only when that file exists.

    The generated file is gitignored, so pinning REPO_ROOT keeps these tests
    off the operator's local state.
    """
    monkeypatch.setattr(deploy, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(deploy, "_host_llm_selected", lambda: False)


def test_compose_cmd_without_cpu_override(tmp_path, monkeypatch):
    _repo_without_workspace_overlay(tmp_path, monkeypatch)
    monkeypatch.setattr(deploy, "_USE_CPU_COMPOSE_OVERRIDE", False)
    assert deploy._compose_cmd("build") == [
        "podman",
        "compose",
        "-f",
        "compose.yaml",
        "-f",
        "config/compose/podman.yaml",
        "build",
    ]


def test_compose_cmd_includes_workspace_overlay_when_present(tmp_path, monkeypatch):
    _repo_without_workspace_overlay(tmp_path, monkeypatch)
    mounts = tmp_path / "state" / "compose.workspaces.yaml"
    mounts.parent.mkdir()
    mounts.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(deploy, "_USE_CPU_COMPOSE_OVERRIDE", False)
    cmd = deploy._compose_cmd("build")
    assert "state/compose.workspaces.yaml" in cmd


def test_compose_cmd_docker_layers_gpu_overlay(tmp_path, monkeypatch):
    _repo_without_workspace_overlay(tmp_path, monkeypatch)
    monkeypatch.setattr(deploy, "_RUNTIME", "docker")
    monkeypatch.setattr(deploy, "_USE_CPU_COMPOSE_OVERRIDE", False)
    assert deploy._compose_cmd("up", "-d") == [
        "docker",
        "compose",
        "-f",
        "compose.yaml",
        "-f",
        "config/compose/docker.yaml",
        "up",
        "-d",
    ]


def test_compose_cmd_docker_cpu_does_not_request_gpus(tmp_path, monkeypatch):
    _repo_without_workspace_overlay(tmp_path, monkeypatch)
    monkeypatch.setattr(deploy, "_RUNTIME", "docker")
    monkeypatch.setattr(deploy, "_USE_CPU_COMPOSE_OVERRIDE", True)
    assert deploy._compose_cmd("build") == [
        "docker",
        "compose",
        "-f",
        "compose.yaml",
        "-f",
        "config/compose/cpu.yaml",
        "-f",
        "config/compose/docker.cpu.yaml",
        "build",
    ]


def test_choose_runtime_auto_prefers_podman():
    engine, reexec = deploy.choose_runtime(
        "auto",
        podman_ok=True,
        docker_ok=True,
        podman_detail="",
        on_windows=True,
    )
    assert (engine, reexec) == ("podman", False)


def test_choose_runtime_auto_uses_docker_when_podman_cannot_connect():
    engine, reexec = deploy.choose_runtime(
        "auto",
        podman_ok=False,
        docker_ok=True,
        podman_detail="Cannot connect to Podman",
        on_windows=True,
    )
    assert (engine, reexec) == ("docker", False)


def test_choose_runtime_windows_podman_without_machine_reexecs_wsl():
    engine, reexec = deploy.choose_runtime(
        "auto",
        podman_ok=False,
        docker_ok=False,
        podman_detail="dial unix /run/podman/podman.sock: connect: no such file",
        on_windows=True,
    )
    assert (engine, reexec) == ("podman", True)


def test_choose_runtime_explicit_docker_does_not_reexec():
    engine, reexec = deploy.choose_runtime(
        "docker",
        podman_ok=False,
        docker_ok=False,
        podman_detail="Cannot connect to Podman",
        on_windows=True,
    )
    assert (engine, reexec) == ("docker", False)


def test_wsl_reexec_command_targets_local_llm():
    cmd = deploy._wsl_reexec_command(
        ["up", "--skip-models"],
        distro=deploy.WSL_DISTRO_DEFAULT,
        repo="/mnt/d/rest/pumba",
    )
    assert cmd[:4] == ["wsl", "-d", deploy.WSL_DISTRO_DEFAULT, "-e"]
    assert "cd /mnt/d/rest/pumba && exec python3 scripts/deploy.py up --skip-models" in cmd[-1]


def test_prepare_gpu_on_docker_does_not_probe_cdi(monkeypatch):
    monkeypatch.setattr(deploy, "_RUNTIME", "docker")
    monkeypatch.setattr(deploy, "_USE_CPU_COMPOSE_OVERRIDE", False)
    monkeypatch.setattr(deploy, "_docker_gpu_probe", lambda: True)
    monkeypatch.setattr(
        deploy, "_cdi_probe", lambda: (_ for _ in ()).throw(AssertionError("cdi"))
    )

    deploy._prepare_gpu_for_up({"LLM_GPU_ARG": "1", "EMBEDDER_GPU_ARG": "1"})

    assert deploy._USE_CPU_COMPOSE_OVERRIDE is False


def test_compose_cmd_with_cpu_override(tmp_path, monkeypatch):
    _repo_without_workspace_overlay(tmp_path, monkeypatch)
    monkeypatch.setattr(deploy, "_USE_CPU_COMPOSE_OVERRIDE", True)
    assert deploy._compose_cmd("up", "-d", "llm") == [
        "podman",
        "compose",
        "-f",
        "compose.yaml",
        "-f",
        "config/compose/cpu.yaml",
        "-f",
        "config/compose/podman.yaml",
        "up",
        "-d",
        "llm",
    ]


def test_prepare_gpu_uses_cpu_overlay_when_gpu_disabled(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "LLM_GPU_ARG=0\nEMBEDDER_GPU_ARG=0\nAGENT_BEARER_TOKEN=secret\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(deploy, "ENV_FILE", env)
    monkeypatch.setattr(deploy, "_USE_CPU_COMPOSE_OVERRIDE", False)
    monkeypatch.setattr(deploy, "_cdi_probe", lambda: (_ for _ in ()).throw(AssertionError("should not probe")))

    deploy._prepare_gpu_for_up(deploy._parse_env_file(env))

    assert deploy._USE_CPU_COMPOSE_OVERRIDE is True


def test_prepare_gpu_generates_cdi_when_missing(monkeypatch):
    monkeypatch.setattr(deploy, "_USE_CPU_COMPOSE_OVERRIDE", False)
    monkeypatch.setattr(deploy, "_cdi_probe", lambda: False)
    monkeypatch.setattr(deploy, "_try_generate_cdi", lambda: True)

    deploy._prepare_gpu_for_up({"LLM_GPU_ARG": "1", "EMBEDDER_GPU_ARG": "1"})

    assert deploy._USE_CPU_COMPOSE_OVERRIDE is False


def test_prepare_gpu_falls_back_when_cdi_unavailable(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("LLM_GPU_ARG=1\nEMBEDDER_GPU_ARG=1\nAGENT_BEARER_TOKEN=secret\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_FILE", env)
    monkeypatch.setattr(deploy, "_USE_CPU_COMPOSE_OVERRIDE", False)
    monkeypatch.setattr(deploy, "_cdi_probe", lambda: False)
    monkeypatch.setattr(deploy, "_try_generate_cdi", lambda: False)

    deploy._prepare_gpu_for_up(deploy._parse_env_file(env))

    assert deploy._USE_CPU_COMPOSE_OVERRIDE is True
    written = env.read_text(encoding="utf-8")
    assert "LLM_GPU_ARG=1" in written
    assert "EMBEDDER_GPU_ARG=1" in written


def test_cmd_up_prepares_gpu_before_compose(monkeypatch, _no_real_podman):
    calls: list[str] = []
    monkeypatch.setattr(deploy, "_ensure_env_file", lambda: None)
    monkeypatch.setattr(deploy, "_wait_healthy", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_compose_build_and_up", lambda host=False: calls.append("build_and_up"))
    monkeypatch.setattr(
        deploy,
        "_prepare_gpu_for_up",
        lambda env: calls.append(f"prepare_gpu:{env.get('LLM_GPU_ARG', '?')}"),
    )
    monkeypatch.setattr(deploy, "_parse_env_file", lambda _p: {"LLM_GPU_ARG": "1"})
    monkeypatch.setattr(deploy, "_run", lambda cmd, **k: calls.append(" ".join(cmd)))

    deploy.cmd_up(argparse.Namespace(skip_models=True, skip_index=True, timeout=300))

    assert calls[0] == "prepare_gpu:1"
    assert "build_and_up" in calls


def test_cmd_rotate_token_rewrites_env_without_recreate(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("AGENT_BEARER_TOKEN=old-token\nLLM_PORT=8080\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_FILE", env_file)
    monkeypatch.setattr(deploy, "_run", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not recreate")))

    rc = deploy.cmd_rotate_token(argparse.Namespace(skip_recreate=True))
    assert rc == 0
    text = env_file.read_text(encoding="utf-8")
    assert "AGENT_BEARER_TOKEN=old-token" not in text
    assert "LLM_PORT=8080" in text
    new_token = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("AGENT_BEARER_TOKEN="))
    assert len(new_token) >= 32


def test_cmd_rotate_token_recreates_mcp(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("AGENT_BEARER_TOKEN=old-token\n", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(deploy, "ENV_FILE", env_file)
    monkeypatch.setattr(deploy, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(deploy, "_run", lambda cmd, **_k: calls.append(cmd))

    rc = deploy.cmd_rotate_token(argparse.Namespace(skip_recreate=False))
    assert rc == 0
    assert calls == [
        ["podman", "compose", "up", "-d", "--force-recreate", "agent", "app", "mcp", "ui-proxy"]
    ]


def test_collection_name_is_stable_per_remote():
    import workspaces as registry

    one = registry.repo_id_for("https://svn.example/proj")
    same = registry.repo_id_for("https://user@svn.example/proj/")
    other = registry.repo_id_for("https://svn.example/other")
    assert one == same
    assert one != other
    assert one.startswith("repo_")


def test_cpu_fallback_does_not_rewrite_gpu_keys(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_GPU_ARG=1\nLLM_NGL=auto\nEMBEDDER_GPU_ARG=0\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_FILE", env_file)
    deploy._enable_cpu_compose_fallback(persist_env=False)
    text = env_file.read_text(encoding="utf-8")
    assert "LLM_GPU_ARG=1" in text
    assert "LLM_NGL=auto" in text


def test_gpu_profile_rewrites_vram_keys_and_keeps_the_token():
    text = "AGENT_BEARER_TOKEN=secret\nLLM_NGL=auto\nEMBEDDER_NGL=0\n"
    overlay = {"LLM_NGL": "999", "RERANKER_NGL": "999"}
    merged = deploy.apply_profile_text(text, overlay)
    assert "AGENT_BEARER_TOKEN=secret" in merged
    assert "LLM_NGL=999" in merged
    assert "LLM_NGL=auto" not in merged
    assert "RERANKER_NGL=999" in merged


def test_gpu_profiles_cover_the_vram_and_model_cases():
    def model(name: str) -> str:
        return deploy._parse_env_file(deploy.GPU_PROFILE_DIR / f"{name}.env")["LLM_MODEL_PATH"]

    baseline = "/models/Qwen2.5-Coder-14B-Instruct-Q5_K_M.gguf"
    assert model("16g") == model("24g") == model("48g") == baseline
    assert model("24g-30b").endswith("Qwen3-Coder-30B-A3B-Instruct-Q5_K_M.gguf")
    assert model("48g-30b").endswith("Qwen3-Coder-30B-A3B-Instruct-Q8_0.gguf")
    assert model("48g-32b").endswith("Qwen2.5-Coder-32B-Instruct-Q5_K_M.gguf")
    assert deploy._parse_env_file(deploy.GPU_PROFILE_DIR / "24g.env")["LLM_CACHE_TYPE_K"] == "q8_0"
    assert deploy._parse_env_file(deploy.GPU_PROFILE_DIR / "48g.env")["LLM_CACHE_TYPE_K"] == "f16"
    assert deploy._parse_env_file(deploy.GPU_PROFILE_DIR / "24g-30b.env")["LLM_GPU_MARGIN_MIB"] == "512"
    assert deploy._parse_env_file(deploy.GPU_PROFILE_DIR / "16g.env")["EMBEDDER_GPU_ARG"] == "0"
    assert model("16g-gptoss").endswith("gpt-oss-20b-MXFP4.gguf")
    assert deploy._parse_env_file(deploy.GPU_PROFILE_DIR / "16g-gptoss.env")["EMBEDDER_GPU_ARG"] == "0"
    assert set(deploy.PROFILE_EXTRA) == {"16g-gptoss", "24g-30b", "48g-30b", "48g-32b", "evo-x5"}
    evo = deploy._parse_env_file(deploy.GPU_PROFILE_DIR / "evo-x5.env")
    assert evo["LLM_SERVE"] == "host"
    assert evo["LLM_GPU_ARG"] == "0"
    assert evo["LLM_THREADS"] == "16"
    assert evo["LLM_CTX_SIZE"] == "262144"
    assert evo["AGENT_CONTEXT_BUDGET_TOKENS"] == "262144"
    assert evo["LLM_BASE_URL"] == "http://host.containers.internal:8080"
    assert evo["EMBEDDER_GPU_ARG"] == "0"
    assert evo["EMBEDDER_NGL"] == "0"
    assert evo["RERANKER_NGL"] == "0"
    assert deploy._parse_env_file(deploy.GPU_PROFILE_DIR / "16g.env")["LLM_BASE_URL"] == "http://llm:8080"
    assert deploy._parse_env_file(deploy.GPU_PROFILE_DIR / "16g.env")["LLM_SERVE"] == "container"
    assert "00001-of-00003" in evo["LLM_MODEL_PATH"]
    assert evo["TRAIN_BASE_MODEL"] == "/models/hf/Mistral-Small-4-119B-2603"


def test_codebase_path_exists_accepts_relative_repo_path(tmp_path, monkeypatch):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    monkeypatch.setattr(deploy, "REPO_ROOT", tmp_path)

    assert deploy._codebase_path_exists("./app")


def test_needs_container_engine_skips_host_only_commands():
    assert deploy._needs_container_engine(["export"]) is False
    assert deploy._needs_container_engine(["gpu-profile", "evo-x5"]) is False
    assert deploy._needs_container_engine(["check"]) is False
    assert deploy._needs_container_engine(["-h"]) is False
    assert deploy._needs_container_engine([]) is False
    assert deploy._needs_container_engine(["up"]) is True
    assert deploy._needs_container_engine(["fetch-extra", "evo-x5"]) is True


def test_activate_runtime_skips_engine_for_export(monkeypatch):
    probed = []
    monkeypatch.setattr(deploy, "_engine_probe", lambda name: probed.append(name) or (False, "no"))
    deploy._activate_runtime(["export"])
    deploy._activate_runtime(["gpu-profile", "evo-x5"])
    deploy._activate_runtime(["check"])
    assert probed == []


def test_activate_runtime_exits_when_wsl_distro_missing(monkeypatch):
    monkeypatch.setattr(deploy, "_engine_probe", lambda _name: (False, "Cannot connect to Podman"))
    monkeypatch.setattr(deploy, "choose_runtime", lambda *a, **k: ("podman", True))
    monkeypatch.setattr(
        deploy, "_wsl_distro_ready", lambda _d: (False, "The system cannot find the distribution")
    )
    monkeypatch.delenv("DEPLOY_SKIP_WSL", raising=False)
    with pytest.raises(SystemExit, match="cannot be started"):
        deploy._activate_runtime(["up"])


def test_wsl_distro_ready_false_when_wsl_missing(monkeypatch):
    monkeypatch.setattr(deploy.shutil, "which", lambda _n: None)
    ok, detail = deploy._wsl_distro_ready("local-llm")
    assert ok is False
    assert "PATH" in detail


def test_wsl_distro_ready_true_when_probe_succeeds(monkeypatch):
    monkeypatch.setattr(deploy.shutil, "which", lambda _n: "wsl")
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="", stderr="")
    )
    ok, detail = deploy._wsl_distro_ready("local-llm")
    assert (ok, detail) == (True, "")


def test_cmd_up_indexer_failure_leaves_stack_up(monkeypatch, _no_real_podman):
    calls: list[list[str]] = []

    def fake_run(cmd, **_k):
        calls.append(cmd)
        if "indexer" in cmd:
            raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(deploy, "_run", fake_run)
    monkeypatch.setattr(deploy, "_ensure_env_file", lambda: None)
    monkeypatch.setattr(deploy, "_require_bind_source", lambda: None)
    monkeypatch.setattr(deploy, "_wait_healthy", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_prepare_gpu_for_up", lambda _env: None)
    monkeypatch.setattr(deploy, "_compose_build_and_up", lambda host=False: calls.append(["build_and_up"]))

    rc = deploy.cmd_up(argparse.Namespace(skip_models=True, skip_index=False, timeout=1))
    assert rc == 1
    assert ["build_and_up"] in calls


def test_host_up_fetches_evo_weights_when_missing(monkeypatch, _no_real_podman, tmp_path):
    calls = _recorder(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_SERVE=host\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_FILE", env_file)
    monkeypatch.setattr(deploy, "_ensure_env_file", lambda: None)
    monkeypatch.setattr(deploy, "_require_bind_source", lambda: None)
    monkeypatch.setattr(deploy, "_prepare_gpu_for_up", lambda _env: None)
    monkeypatch.setattr(deploy, "_wait_healthy", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_compose_build_and_up", lambda host=False: calls.append(["build_and_up"]))
    monkeypatch.setattr(deploy, "_host_llm_weights_missing", lambda: [tmp_path / "missing.gguf"])
    monkeypatch.setattr(deploy, "_require_host_llm_weights", lambda: None)
    monkeypatch.setattr(deploy, "_start_host_llm", lambda **_k: calls.append(["host-llm"]))
    fetched: list[str] = []
    monkeypatch.setattr(deploy, "cmd_fetch_extra", lambda args: fetched.append(args.name) or 0)

    deploy.cmd_up(argparse.Namespace(skip_models=False, skip_index=True, timeout=5))

    assert fetched == ["evo-x5"]
    assert ["host-llm"] in calls


def test_wait_http_returns_when_ready(monkeypatch):
    import urllib.request

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(deploy.time, "sleep", lambda _s: None)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _Resp())
    deploy._wait_http("http://127.0.0.1:8080/health", timeout=5)


def test_wait_http_exits_when_process_dies(monkeypatch):
    monkeypatch.setattr(deploy.time, "sleep", lambda _s: None)
    with pytest.raises(SystemExit, match="exited before"):
        deploy._wait_http("http://127.0.0.1:8080/health", timeout=5, process=SimpleNamespace(poll=lambda: 1))


def test_health_from_compose_ps_prefers_json_service_field():
    payload = "\n".join(
        [
            '{"Service":"qdrant","Health":"healthy","State":"running"}',
            '{"Service":"llm","Health":"unhealthy","State":"running"}',
            '{"Service":"embedder","Health":"starting","State":"running"}',
            '{"Service":"tools","State":"running","Status":"Up 3 minutes"}',
        ]
    )
    statuses = deploy._health_from_compose_ps(
        payload, ["qdrant", "llm", "embedder", "tools", "agent"]
    )
    assert statuses == {
        "qdrant": "healthy",
        "llm": "unhealthy",
        "embedder": "starting",
        "tools": "unknown",
        "agent": "not_found",
    }


def test_health_from_compose_ps_falls_back_to_table_text():
    output = f"{product.COMPOSE_PROJECT}-agent-1  Up 1 minute (healthy)"
    statuses = deploy._health_from_compose_ps(output, ["agent", "qdrant"])
    assert statuses["agent"] == "healthy"
    assert statuses["qdrant"] == "not_found"


def test_fetch_weights_explains_how_to_resume(monkeypatch):
    monkeypatch.setattr(
        deploy,
        "_run",
        lambda *_a, **_k: (_ for _ in ()).throw(subprocess.CalledProcessError(1, ["fetch"])),
    )
    with pytest.raises(SystemExit, match="--skip-models"):
        deploy._fetch_weights(host=False)


def test_start_host_llm_can_spawn_without_waiting(monkeypatch, tmp_path):
    monkeypatch.setattr(deploy, "HOST_LLM_PID", tmp_path / "host-llm.pid")
    binary = str(tmp_path / "llama-server")
    monkeypatch.setattr("evo_x5_serve.ensure_binary", lambda *_a, **_k: binary)
    seen: dict[str, list[str]] = {}

    class _Proc:
        pid = 7

        def poll(self):
            return None

    def fake_popen(argv, **_kwargs):
        seen["argv"] = argv
        return _Proc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    waited: list[str] = []
    monkeypatch.setattr(deploy, "_wait_http", lambda url, **k: waited.append(url))
    deploy._start_host_llm(timeout=12, wait=False)
    assert waited == []
    assert seen["argv"][seen["argv"].index("--binary") + 1] == binary
    assert (tmp_path / "host-llm.pid").read_text(encoding="utf-8") == "7"


def test_wait_published_doors_hits_loopback_health(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("AGENT_PORT=8090\nLLM_SERVE=host\nLLM_PORT=8080\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_FILE", env)
    seen: list[tuple[str, int]] = []
    monkeypatch.setattr(
        deploy, "_wait_http", lambda url, **k: seen.append((url, k.get("timeout")))
    )
    deploy._wait_published_doors(host=True, timeout=30)
    assert seen[0][0] == "http://127.0.0.1:8090/health"
    assert seen[1] == ("http://127.0.0.1:8080/health", max(30, deploy.HOST_LLM_READY_TIMEOUT))


def test_wait_published_doors_skips_llama_when_not_host(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("AGENT_PORT=8090\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_FILE", env)
    seen: list[str] = []
    monkeypatch.setattr(deploy, "_wait_http", lambda url, **k: seen.append(url))
    deploy._wait_published_doors(host=False, timeout=30)
    assert seen == ["http://127.0.0.1:8090/health"]


def test_health_from_compose_ps_accepts_json_array():
    payload = json.dumps(
        [
            {"Service": "agent", "Health": "healthy", "State": "running"},
            {"Service": "ui-proxy", "Health": "healthy", "State": "running"},
        ]
    )
    statuses = deploy._health_from_compose_ps(payload, ["agent", "ui-proxy", "qdrant"])
    assert statuses == {"agent": "healthy", "ui-proxy": "healthy", "qdrant": "not_found"}


def test_health_from_compose_ps_reads_compose_service_label():
    payload = json.dumps(
        {
            "Labels": "com.docker.compose.service=agent,com.docker.compose.project=pumba",
            "Health": "healthy",
            "State": "running",
        }
    )
    statuses = deploy._health_from_compose_ps(payload, ["agent"])
    assert statuses["agent"] == "healthy"


def test_compose_ps_payload_falls_back_when_format_json_fails(monkeypatch):
    def fake_run(cmd, **_k):
        if "--format" in cmd:
            return SimpleNamespace(returncode=1, stdout="", stderr="unknown flag")
        return SimpleNamespace(returncode=0, stdout="plain-table", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(deploy, "_compose_cmd", lambda *parts: ["podman", "compose", *parts])
    monkeypatch.setattr(deploy, "_compose_env", lambda: {})
    code, out, err = deploy._compose_ps_payload()
    assert (code, out, err) == (0, "plain-table", "")


def test_fetch_weights_evo_failure_points_at_fetch_extra(monkeypatch):
    monkeypatch.setattr(deploy, "_run", lambda *_a, **_k: None)
    monkeypatch.setattr(deploy, "_host_llm_weights_missing", lambda: [Path("missing.gguf")])
    monkeypatch.setattr(
        deploy,
        "cmd_fetch_extra",
        lambda *_a, **_k: (_ for _ in ()).throw(subprocess.CalledProcessError(1, ["fetch-extra"])),
    )
    with pytest.raises(SystemExit, match="fetch-extra evo-x5"):
        deploy._fetch_weights(host=True)


def test_cmd_up_waits_published_doors_and_ui_proxy(monkeypatch, _no_real_podman):
    doors: list[dict] = []
    waited: list[list[str]] = []
    monkeypatch.setattr(deploy, "_run", lambda *_a, **_k: None)
    monkeypatch.setattr(deploy, "_ensure_env_file", lambda: None)
    monkeypatch.setattr(deploy, "_require_bind_source", lambda: None)
    monkeypatch.setattr(deploy, "_prepare_gpu_for_up", lambda _env: None)
    monkeypatch.setattr(deploy, "_compose_build_and_up", lambda host=False: None)
    monkeypatch.setattr(deploy, "_wait_healthy", lambda services, timeout: waited.append(list(services)))
    monkeypatch.setattr(deploy, "_wait_published_doors", lambda **k: doors.append(k))

    deploy.cmd_up(argparse.Namespace(skip_models=True, skip_index=True, timeout=40))

    assert waited[0][-1] == "ui-proxy"
    assert "llm" in waited[0]
    assert doors == [{"host": False, "timeout": 40}]


def test_host_up_spawns_llama_then_waits_on_doors(monkeypatch, _no_real_podman, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_SERVE=host\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_FILE", env_file)
    started: list[dict] = []
    doors: list[dict] = []
    monkeypatch.setattr(deploy, "_run", lambda *_a, **_k: None)
    monkeypatch.setattr(deploy, "_ensure_env_file", lambda: None)
    monkeypatch.setattr(deploy, "_require_bind_source", lambda: None)
    monkeypatch.setattr(deploy, "_prepare_gpu_for_up", lambda _env: None)
    monkeypatch.setattr(deploy, "_require_host_llm_weights", lambda: None)
    monkeypatch.setattr(deploy, "_compose_build_and_up", lambda host=False: None)
    monkeypatch.setattr(deploy, "_start_host_llm", lambda **k: started.append(k))
    monkeypatch.setattr(deploy, "_wait_published_doors", lambda **k: doors.append(k))
    waited: list[list[str]] = []
    monkeypatch.setattr(deploy, "_wait_healthy", lambda services, timeout: waited.append(list(services)))

    deploy.cmd_up(argparse.Namespace(skip_models=True, skip_index=True, timeout=5))

    assert started == [{"timeout": deploy.HOST_LLM_READY_TIMEOUT, "wait": False}]
    assert doors == [{"host": True, "timeout": 5}]
    assert "llm" not in waited[0]
    assert waited[0][-1] == "ui-proxy"


def test_wait_http_times_out(monkeypatch):
    import urllib.error

    now = {"t": 0.0}
    monkeypatch.setattr(deploy.time, "monotonic", lambda: now["t"])
    monkeypatch.setattr(deploy.time, "sleep", lambda s: now.__setitem__("t", now["t"] + s))
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(urllib.error.URLError("refused")),
    )
    with pytest.raises(SystemExit, match="not ready"):
        deploy._wait_http("http://127.0.0.1:8090/health", timeout=2)


def test_start_host_llm_waits_for_health(monkeypatch, tmp_path):
    monkeypatch.setattr(deploy, "HOST_LLM_PID", tmp_path / "host-llm.pid")
    monkeypatch.setattr("evo_x5_serve.ensure_binary", lambda *_a, **_k: "llama-server")

    class _Proc:
        pid = 4242

        def poll(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: _Proc())
    waited: list[object] = []

    def fake_wait(url, **k):
        waited.append((url, k.get("timeout"), k.get("process") is not None))

    monkeypatch.setattr(deploy, "_wait_http", fake_wait)
    deploy._start_host_llm(timeout=12)
    assert waited == [(deploy.HOST_LLM_HEALTH_URL, 12, True)]
    assert (tmp_path / "host-llm.pid").read_text(encoding="utf-8") == "4242"


def test_package_is_split_across_modules():
    assert (deploy.REPO_ROOT / "scripts" / "host" / "deploy" / "stack.py").is_file()
    assert (deploy.REPO_ROOT / "scripts" / "host" / "deploy" / "doctor.py").is_file()
    assert not (deploy.REPO_ROOT / "scripts" / "host" / "deploy.py").exists()
