"""Unit tests for `scripts/deploy.py`.

`_run`/`subprocess.run`/`shutil.which` are always monkeypatched out - these
tests never invoke a real `podman` binary. What's under test is the command
sequencing and the safety checks (env file / placeholder token / health
polling timeout), which is exactly the value this script adds over typing
the raw `podman compose` commands by hand.
"""

from __future__ import annotations

import argparse
import subprocess
from types import SimpleNamespace

import pytest

import deploy

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

def test_ensure_env_file_creates_from_example_and_exits(tmp_path, monkeypatch):
    example = tmp_path / ".env.example"
    example.write_text("AGENT_BEARER_TOKEN=changeme-generate-a-real-secret\n", encoding="utf-8")
    env = tmp_path / ".env"
    monkeypatch.setattr(deploy, "ENV_FILE", env)
    monkeypatch.setattr(deploy, "ENV_EXAMPLE", example)

    with pytest.raises(SystemExit):
        deploy._ensure_env_file()

    assert env.exists()
    assert env.read_text(encoding="utf-8") == example.read_text(encoding="utf-8")


def test_ensure_env_file_rejects_placeholder_token(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("AGENT_BEARER_TOKEN=changeme-generate-a-real-secret\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_FILE", env)

    with pytest.raises(SystemExit):
        deploy._ensure_env_file()


def test_ensure_env_file_passes_with_real_token(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("AGENT_BEARER_TOKEN=a-real-random-secret-value\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_FILE", env)

    deploy._ensure_env_file()  # must not raise


# --------------------------------------------------------------------------- _wait_healthy

def test_wait_healthy_returns_once_all_services_report_healthy(monkeypatch):
    monkeypatch.setattr(deploy.time, "sleep", lambda _s: None)
    ps_output = "\n".join(f"local_llm-{svc}-1  Up 2 minutes (healthy)" for svc in ["qdrant", "agent"])
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


def _recorder(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(deploy, "_run", lambda cmd, **k: calls.append(cmd))
    return calls


def test_cmd_up_full_sequence(monkeypatch, _no_real_podman):
    calls = _recorder(monkeypatch)
    monkeypatch.setattr(deploy, "_ensure_env_file", lambda: None)
    monkeypatch.setattr(deploy, "_wait_healthy", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_prepare_gpu_for_up", lambda _env: None)
    monkeypatch.setattr(deploy, "_compose_build_and_up", lambda: calls.append(["build_and_up"]))

    deploy.cmd_up(argparse.Namespace(skip_models=False, skip_index=False, timeout=300))

    assert ["podman", "compose", "--profile", "setup", "run", "--rm", "fetch-models"] in calls
    assert ["build_and_up"] in calls
    assert ["podman", "compose", "--profile", "index", "run", "--rm", "indexer"] in calls


def test_cmd_up_respects_skip_flags(monkeypatch, _no_real_podman):
    calls = _recorder(monkeypatch)
    monkeypatch.setattr(deploy, "_ensure_env_file", lambda: None)
    monkeypatch.setattr(deploy, "_wait_healthy", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_prepare_gpu_for_up", lambda _env: None)
    monkeypatch.setattr(deploy, "_compose_build_and_up", lambda: calls.append(["build_and_up"]))

    deploy.cmd_up(argparse.Namespace(skip_models=True, skip_index=True, timeout=300))

    joined = [" ".join(c) for c in calls]
    assert not any("fetch-models" in c for c in joined)
    assert not any("indexer" in c for c in joined)
    assert "build_and_up" in joined


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

    result = deploy._compose_env()

    assert result["CODEBASE_PATH"] == "/mnt/d/svn/mon_projet"


# --------------------------------------------------------------------------- GPU / CDI

def test_compose_cmd_without_cpu_override(monkeypatch):
    monkeypatch.setattr(deploy, "_USE_CPU_COMPOSE_OVERRIDE", False)
    assert deploy._compose_cmd("build") == [
        "podman",
        "compose",
        "-f",
        "compose.yaml",
        "-f",
        "compose.podman.yaml",
        "-f",
        "compose.workspaces.yaml",
        "build",
    ]


def test_compose_cmd_docker_layers_gpu_overlay(monkeypatch):
    monkeypatch.setattr(deploy, "_RUNTIME", "docker")
    monkeypatch.setattr(deploy, "_USE_CPU_COMPOSE_OVERRIDE", False)
    assert deploy._compose_cmd("up", "-d") == [
        "docker",
        "compose",
        "-f",
        "compose.yaml",
        "-f",
        "compose.docker.yaml",
        "-f",
        "compose.workspaces.yaml",
        "up",
        "-d",
    ]


def test_compose_cmd_docker_cpu_does_not_request_gpus(monkeypatch):
    monkeypatch.setattr(deploy, "_RUNTIME", "docker")
    monkeypatch.setattr(deploy, "_USE_CPU_COMPOSE_OVERRIDE", True)
    assert deploy._compose_cmd("build") == [
        "docker",
        "compose",
        "-f",
        "compose.yaml",
        "-f",
        "compose.cpu.yaml",
        "-f",
        "compose.docker.cpu.yaml",
        "-f",
        "compose.workspaces.yaml",
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
        distro="local-llm",
        repo="/mnt/d/rest/local_llm",
    )
    assert cmd[:4] == ["wsl", "-d", "local-llm", "-e"]
    assert "cd /mnt/d/rest/local_llm && exec python3 scripts/deploy.py up --skip-models" in cmd[-1]


def test_prepare_gpu_on_docker_does_not_probe_cdi(monkeypatch):
    monkeypatch.setattr(deploy, "_RUNTIME", "docker")
    monkeypatch.setattr(deploy, "_USE_CPU_COMPOSE_OVERRIDE", False)
    monkeypatch.setattr(deploy, "_docker_gpu_probe", lambda: True)
    monkeypatch.setattr(
        deploy, "_cdi_probe", lambda: (_ for _ in ()).throw(AssertionError("cdi"))
    )

    deploy._prepare_gpu_for_up({"LLM_GPU_ARG": "1", "EMBEDDER_GPU_ARG": "1"})

    assert deploy._USE_CPU_COMPOSE_OVERRIDE is False


def test_compose_cmd_with_cpu_override(monkeypatch):
    monkeypatch.setattr(deploy, "_USE_CPU_COMPOSE_OVERRIDE", True)
    assert deploy._compose_cmd("up", "-d", "llm") == [
        "podman",
        "compose",
        "-f",
        "compose.yaml",
        "-f",
        "compose.cpu.yaml",
        "-f",
        "compose.podman.yaml",
        "-f",
        "compose.workspaces.yaml",
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
    monkeypatch.setattr(deploy, "_compose_build_and_up", lambda: calls.append("build_and_up"))
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
    assert set(deploy.PROFILE_EXTRA) == {"24g-30b", "48g-30b", "48g-32b", "evo-x5"}
    evo = deploy._parse_env_file(deploy.GPU_PROFILE_DIR / "evo-x5.env")
    assert evo["LLM_SERVE"] == "host"
    assert evo["LLM_GPU_ARG"] == "0"
    assert evo["LLM_THREADS"] == "16"
    assert "00001-of-00003" in evo["LLM_MODEL_PATH"]


def test_codebase_path_exists_accepts_relative_repo_path(tmp_path, monkeypatch):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    monkeypatch.setattr(deploy, "REPO_ROOT", tmp_path)

    assert deploy._codebase_path_exists("./app")
