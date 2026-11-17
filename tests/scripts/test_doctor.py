"""Unit tests for the `doctor` subcommand in `scripts/deploy.py`.

Like test_deploy.py, everything that would touch a real `podman` binary or
the real filesystem outside `tmp_path` is monkeypatched out.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from types import SimpleNamespace

import deploy
import fetch_models
import product

# --------------------------------------------------------------------------- _parse_env_file

def test_parse_env_file_returns_empty_dict_when_missing(tmp_path):
    assert deploy._parse_env_file(tmp_path / "does-not-exist.env") == {}


def test_parse_env_file_parses_key_value_pairs(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "AGENT_BEARER_TOKEN=some-secret",
                "LLM_MODEL_PATH=/models/foo.gguf",
                "MALFORMED_LINE_NO_EQUALS",
            ]
        ),
        encoding="utf-8",
    )

    result = deploy._parse_env_file(env)

    assert result == {
        "AGENT_BEARER_TOKEN": "some-secret",
        "LLM_MODEL_PATH": "/models/foo.gguf",
    }


# --------------------------------------------------------------------------- _parse_compose_ps_health

def test_parse_compose_ps_health_detects_all_states():
    output = "\n".join(
        [
            f"{product.COMPOSE_PROJECT}-qdrant-1    Up 2 minutes (healthy)",
            f"{product.COMPOSE_PROJECT}-llm-1       Up 10 seconds (unhealthy)",
            f"{product.COMPOSE_PROJECT}-embedder-1  Up 2 seconds (health: starting)",
            f"{product.COMPOSE_PROJECT}-tools-1     Up 3 minutes",
        ]
    )

    statuses = deploy._parse_compose_ps_health(
        output, ["qdrant", "llm", "embedder", "tools", "agent"]
    )

    assert statuses == {
        "qdrant": "healthy",
        "llm": "unhealthy",
        "embedder": "starting",
        "tools": "unknown",
        "agent": "not_found",
    }


def test_parse_compose_ps_health_unhealthy_not_misread_as_healthy():
    # "unhealthy" contains "healthy" as a substring - make sure the more
    # specific state wins rather than the substring match.
    output = f"{product.COMPOSE_PROJECT}-llm-1  Up 1 minute (unhealthy)"
    statuses = deploy._parse_compose_ps_health(output, ["llm"])
    assert statuses["llm"] == "unhealthy"


# --------------------------------------------------------------------------- _doctor_check_podman

def test_doctor_check_podman_fails_when_missing(monkeypatch):
    monkeypatch.setattr(deploy.shutil, "which", lambda _name: None)

    checks = deploy._doctor_check_podman()

    assert len(checks) == 1
    assert checks[0].status == "fail"
    assert checks[0].hint is not None


def test_doctor_check_podman_ok_when_available(monkeypatch):
    monkeypatch.setattr(deploy.shutil, "which", lambda _name: "/usr/bin/podman")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="podman compose version 1.0\n"),
    )

    checks = deploy._doctor_check_podman()

    assert [c.status for c in checks] == ["ok", "ok"]


def test_doctor_check_podman_flags_broken_compose(monkeypatch):
    monkeypatch.setattr(deploy.shutil, "which", lambda _name: "/usr/bin/podman")

    def _fake_run(cmd, **_k):
        if cmd[:2] == ["podman", "--version"]:
            return SimpleNamespace(returncode=0, stdout="podman version 5.0\n")
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    checks = deploy._doctor_check_podman()

    assert [c.status for c in checks] == ["ok", "fail"]


# --------------------------------------------------------------------------- _doctor_check_env

def test_doctor_check_env_fails_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "ENV_FILE", tmp_path / "missing.env")

    checks = deploy._doctor_check_env()

    assert len(checks) == 1
    assert checks[0].status == "fail"


def test_doctor_check_env_fails_on_placeholder_token(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("AGENT_BEARER_TOKEN=changeme-generate-a-real-secret\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_FILE", env)

    checks = deploy._doctor_check_env()

    statuses = {c.name: c.status for c in checks}
    assert statuses[".env"] == "ok"
    assert statuses["AGENT_BEARER_TOKEN"] == "fail"


def test_doctor_check_env_ok_with_real_token(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("AGENT_BEARER_TOKEN=a-real-random-secret\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_FILE", env)

    checks = deploy._doctor_check_env()

    assert all(c.status == "ok" for c in checks)


# --------------------------------------------------------------------------- _doctor_check_codebase_path


def test_doctor_check_codebase_path_ok_for_existing_relative_path(tmp_path, monkeypatch):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    monkeypatch.setattr(deploy, "REPO_ROOT", tmp_path)

    check = deploy._doctor_check_codebase_path({"CODEBASE_PATH": "./app"})

    assert check.status == "ok"
    assert "./app" in check.detail


def test_doctor_check_codebase_path_fails_when_missing():
    check = deploy._doctor_check_codebase_path({"CODEBASE_PATH": "d:/does/not/exist"})

    assert check.status == "fail"
    assert check.hint is not None


# --------------------------------------------------------------------------- _doctor_check_models

def test_doctor_check_models_skips_when_env_var_unset():
    checks = deploy._doctor_check_models({}, verify_checksums=False)
    assert all(c.status == "skip" for c in checks)


def test_doctor_check_models_fails_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "MODELS_DIR", tmp_path)

    checks = deploy._doctor_check_models(
        {"LLM_MODEL_PATH": "/wherever/missing.gguf"}, verify_checksums=False
    )

    by_name = {c.name: c for c in checks}
    assert by_name["missing.gguf"].status == "fail"
    assert by_name["missing.gguf"].hint is not None
    assert by_name["EMBEDDER_MODEL_PATH"].status == "skip"  # not set in this env dict


def test_doctor_check_models_ok_without_checksum_verification(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "MODELS_DIR", tmp_path)
    (tmp_path / "present.gguf").write_bytes(b"x" * 1024)

    checks = deploy._doctor_check_models(
        {"LLM_MODEL_PATH": "/wherever/present.gguf"}, verify_checksums=False
    )

    by_name = {c.name: c for c in checks}
    assert by_name["present.gguf"].status == "ok"
    assert "checksum" not in by_name["present.gguf"].detail


def test_doctor_check_models_verifies_matching_checksum(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "MODELS_DIR", tmp_path)
    content = b"totally real model weights"
    (tmp_path / "pinned.gguf").write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    fake_spec = fetch_models.ModelSpec(name="chat", repo_id="example/pinned", filename="pinned.gguf", sha256=digest)
    monkeypatch.setattr(fetch_models, "CHAT_MODEL", fake_spec)

    checks = deploy._doctor_check_models(
        {"LLM_MODEL_PATH": "/wherever/pinned.gguf"}, verify_checksums=True
    )

    by_name = {c.name: c for c in checks}
    assert by_name["pinned.gguf"].status == "ok"
    assert "checksum verified" in by_name["pinned.gguf"].detail


def test_doctor_check_models_flags_checksum_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "MODELS_DIR", tmp_path)
    (tmp_path / "pinned.gguf").write_bytes(b"corrupted contents")
    fake_spec = fetch_models.ModelSpec(
        name="chat",
        repo_id="example/pinned",
        filename="pinned.gguf",
        sha256="0" * 64,
    )
    monkeypatch.setattr(fetch_models, "CHAT_MODEL", fake_spec)

    checks = deploy._doctor_check_models(
        {"LLM_MODEL_PATH": "/wherever/pinned.gguf"}, verify_checksums=True
    )

    by_name = {c.name: c for c in checks}
    assert by_name["pinned.gguf"].status == "fail"
    assert "CHECKSUM MISMATCH" in by_name["pinned.gguf"].detail


def test_doctor_check_models_skips_checksum_for_unpinned_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "MODELS_DIR", tmp_path)
    (tmp_path / "custom-model.gguf").write_bytes(b"whatever")

    checks = deploy._doctor_check_models(
        {"LLM_MODEL_PATH": "/wherever/custom-model.gguf"}, verify_checksums=True
    )

    by_name = {c.name: c for c in checks}
    assert by_name["custom-model.gguf"].status == "skip"
    assert "not a pinned filename" in by_name["custom-model.gguf"].detail


# --------------------------------------------------------------------------- _doctor_check_disk_space

def test_doctor_check_disk_space_warns_below_budget(monkeypatch):
    tiny_free = 1 * (1024**3)  # 1 GiB, below DISK_BUDGET_GIB
    monkeypatch.setattr(
        deploy.shutil, "disk_usage", lambda _p: SimpleNamespace(total=0, used=0, free=tiny_free)
    )

    check = deploy._doctor_check_disk_space()

    assert check.status == "warn"


def test_doctor_check_disk_space_ok_above_budget(monkeypatch):
    plenty_free = 100 * (1024**3)
    monkeypatch.setattr(
        deploy.shutil, "disk_usage", lambda _p: SimpleNamespace(total=0, used=0, free=plenty_free)
    )

    check = deploy._doctor_check_disk_space()

    assert check.status == "ok"


# --------------------------------------------------------------------------- _doctor_check_services

def test_doctor_check_services_reports_each_state(monkeypatch):
    ps_output = "\n".join(
        [
            f"{product.COMPOSE_PROJECT}-qdrant-1    Up 2 minutes (healthy)",
            f"{product.COMPOSE_PROJECT}-llm-1       Up 10 seconds (unhealthy)",
            f"{product.COMPOSE_PROJECT}-embedder-1  Up 2 seconds (health: starting)",
            f"{product.COMPOSE_PROJECT}-tools-1     Up 3 minutes",
        ]
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=ps_output, stderr="")
    )

    checks = deploy._doctor_check_services()

    statuses = {c.name: c.status for c in checks}
    assert statuses["qdrant"] == "ok"
    assert statuses["llm"] == "fail"
    assert statuses["embedder"] == "warn"
    assert statuses["tools"] == "warn"
    assert statuses["agent"] == "warn"  # not_found


def test_doctor_checks_host_llm_when_profile_is_evo(monkeypatch, tmp_path):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0,
            stdout=f"{product.COMPOSE_PROJECT}-qdrant-1 Up 2 minutes (healthy)\n",
        ),
    )
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_SERVE=host\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_FILE", env_file)
    pid_file = tmp_path / "host-llm.pid"
    pid_file.write_text("4242", encoding="utf-8")
    monkeypatch.setattr(deploy, "HOST_LLM_PID", pid_file)
    monkeypatch.setattr(deploy, "_host_llm_alive", lambda pid: pid == 4242)

    checks = {c.name: c for c in deploy._doctor_check_services()}
    assert checks["llm"].status == "ok"
    assert "4242" in checks["llm"].detail


def test_doctor_check_services_reads_json_ps(monkeypatch):
    payload = json.dumps(
        [
            {"Service": name, "Health": "healthy", "State": "running"}
            for name in deploy.CORE_SERVICES
        ]
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=payload, stderr="")
    )
    checks = {c.name: c.status for c in deploy._doctor_check_services()}
    assert checks == {name: "ok" for name in deploy.CORE_SERVICES}


def test_doctor_check_services_fails_when_compose_ps_errors(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )

    checks = deploy._doctor_check_services()

    assert len(checks) == 1
    assert checks[0].status == "fail"


def test_doctor_check_services_fails_gracefully_when_podman_missing(monkeypatch):
    def _raise(*_a, **_k):
        raise FileNotFoundError("[WinError 2] The system cannot find the file specified")

    monkeypatch.setattr(subprocess, "run", _raise)

    checks = deploy._doctor_check_services()

    assert len(checks) == 1
    assert checks[0].status == "fail"
    assert checks[0].hint is not None


# --------------------------------------------------------------------------- _doctor_check_gpu

def test_doctor_check_gpu_ok(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="NVIDIA-SMI 550.00\n")
    )

    check = deploy._doctor_check_gpu()

    assert check.status == "ok"


def test_doctor_check_gpu_fails(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="")
    )

    check = deploy._doctor_check_gpu()

    assert check.status == "fail"
    assert check.hint is not None


def test_doctor_check_gpu_fails_gracefully_when_podman_missing(monkeypatch):
    def _raise(*_a, **_k):
        raise FileNotFoundError("[WinError 2] The system cannot find the file specified")

    monkeypatch.setattr(subprocess, "run", _raise)

    check = deploy._doctor_check_gpu()

    assert check.status == "fail"
    assert check.hint is not None


# --------------------------------------------------------------------------- _render_html

def test_render_html_escapes_and_includes_status_classes():
    checks = [
        deploy.Check("safe<name>", "fail", "detail with <script>alert(1)</script>", hint="fix it"),
        deploy.Check("ok-check", "ok", "all good"),
    ]

    output = deploy._render_html(checks)

    assert "<script>alert(1)</script>" not in output
    assert "&lt;script&gt;" in output
    assert "status-fail" in output
    assert "status-ok" in output
    assert "fix it" in output
    assert f"{product.NAME} doctor report" in output


def test_render_html_omits_hint_for_ok_checks():
    checks = [deploy.Check("ok-check", "ok", "all good", hint="should not appear")]

    output = deploy._render_html(checks)

    assert "should not appear" not in output


def test_doctor_check_gpu_offload_warns_on_cpu_build_with_gpu_env(monkeypatch):
    monkeypatch.setattr(
        deploy,
        "_container_env_var",
        lambda service, var: "0" if var.endswith("_GPU_BUILD") else "999",
    )
    env = {"LLM_GPU_ARG": "1", "EMBEDDER_GPU_ARG": "1"}

    checks = deploy._doctor_check_gpu_offload(env)

    assert any(c.status == "warn" and "GPU build" in c.name for c in checks)


def test_doctor_check_gpu_offload_ok_when_build_and_ngl_match(monkeypatch):
    monkeypatch.setattr(
        deploy,
        "_container_env_var",
        lambda service, var: "1" if var.endswith("_GPU_BUILD") else "999",
    )
    env = {"LLM_GPU_ARG": "1", "EMBEDDER_GPU_ARG": "1"}

    checks = deploy._doctor_check_gpu_offload(env)

    assert all(c.status == "ok" for c in checks)


def test_doctor_check_gpu_offload_skips_when_cpu_intended():
    env = {"LLM_GPU_ARG": "0", "EMBEDDER_GPU_ARG": "0"}

    checks = deploy._doctor_check_gpu_offload(env)

    assert all(c.status == "skip" for c in checks)


# --------------------------------------------------------------------------- cmd_doctor

def _stub_all_checks(monkeypatch, *, service_checks):
    monkeypatch.setattr(deploy, "_parse_env_file", lambda _p: {"CODEBASE_PATH": "./app"})
    monkeypatch.setattr(deploy, "_doctor_check_podman", lambda: [deploy.Check("podman", "ok", "fine")])
    monkeypatch.setattr(deploy, "_doctor_check_env", lambda: [deploy.Check(".env", "ok", "fine")])
    monkeypatch.setattr(
        deploy,
        "_doctor_check_codebase_path",
        lambda _env: deploy.Check("CODEBASE_PATH", "ok", "./app"),
    )
    monkeypatch.setattr(deploy, "_doctor_check_models", lambda *_a, **_k: [deploy.Check("model", "skip", "n/a")])
    monkeypatch.setattr(
        deploy, "_doctor_check_hf_train_base", lambda: deploy.Check("HF train base", "skip", "n/a")
    )
    monkeypatch.setattr(
        deploy, "_doctor_check_lora_adapter", lambda _env: deploy.Check("LLM_LORA_PATH", "skip", "n/a")
    )
    monkeypatch.setattr(
        deploy, "_doctor_check_train_commit", lambda _env: deploy.Check("train commit", "skip", "n/a")
    )
    monkeypatch.setattr(deploy, "_doctor_check_gpu_offload", lambda _env: [])
    monkeypatch.setattr(deploy, "_doctor_check_disk_space", lambda: deploy.Check("disk", "ok", "plenty"))
    monkeypatch.setattr(deploy, "_doctor_check_services", lambda: service_checks)
    monkeypatch.setattr(deploy, "_doctor_check_gpu", lambda: deploy.Check("gpu", "ok", "fine"))


def test_cmd_doctor_returns_zero_when_all_pass(monkeypatch):
    _stub_all_checks(monkeypatch, service_checks=[deploy.Check("agent", "ok", "healthy")])

    rc = deploy.cmd_doctor(argparse.Namespace(html=None, gpu=False, verify_checksums=False))

    assert rc == 0


def test_cmd_doctor_returns_one_when_any_check_fails(monkeypatch):
    _stub_all_checks(monkeypatch, service_checks=[deploy.Check("agent", "fail", "unhealthy")])

    rc = deploy.cmd_doctor(argparse.Namespace(html=None, gpu=False, verify_checksums=False))

    assert rc == 1


def test_cmd_doctor_skips_gpu_check_unless_requested(monkeypatch):
    _stub_all_checks(monkeypatch, service_checks=[deploy.Check("agent", "ok", "healthy")])
    gpu_calls = []
    monkeypatch.setattr(deploy, "_doctor_check_gpu", lambda: gpu_calls.append(1) or deploy.Check("gpu", "ok", "x"))

    deploy.cmd_doctor(argparse.Namespace(html=None, gpu=False, verify_checksums=False))
    assert gpu_calls == []

    deploy.cmd_doctor(argparse.Namespace(html=None, gpu=True, verify_checksums=False))
    assert gpu_calls == [1]


def test_cmd_doctor_writes_html_report_when_requested(tmp_path, monkeypatch):
    _stub_all_checks(monkeypatch, service_checks=[deploy.Check("agent", "ok", "healthy")])
    html_path = tmp_path / "report.html"

    rc = deploy.cmd_doctor(
        argparse.Namespace(html=str(html_path), gpu=False, verify_checksums=False)
    )

    assert rc == 0
    assert html_path.exists()
    assert "<html" in html_path.read_text(encoding="utf-8")
