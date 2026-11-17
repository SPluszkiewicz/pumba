"""Every long-running service in compose.yaml carries the hardening SECURITY.md promises."""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_COMPOSE = Path(__file__).resolve().parents[2] / "compose.yaml"


def _services() -> dict[str, dict]:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["services"]


def _serving() -> list[str]:
    # No `profiles:` means the service starts with a bare `compose up`.
    return sorted(name for name, spec in _services().items() if not spec.get("profiles"))


@pytest.mark.parametrize("name", _serving())
def test_serving_service_drops_capabilities(name: str) -> None:
    spec = _services()[name]
    assert spec.get("cap_drop") == ["ALL"], f"{name} must set cap_drop: [ALL]"
    assert "no-new-privileges:true" in (spec.get("security_opt") or []), f"{name} must set no-new-privileges"


def test_cpu_overlay_overrides_cdi_devices() -> None:
    text = (_COMPOSE.parent / "config" / "compose" / "cpu.yaml").read_text(encoding="utf-8")
    assert text.count("devices: !override []") >= 3


def test_grafana_binds_loopback_and_takes_password_from_env() -> None:
    text = (_COMPOSE.parent / "config" / "compose" / "observability.yaml").read_text(
        encoding="utf-8"
    )
    assert '"127.0.0.1:3000:3000"' in text
    assert "GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD" in text
    assert "GF_SECURITY_ADMIN_PASSWORD=admin" not in text
    assert '- "3000:3000"' not in text


def test_app_dials_agent_on_the_container_port() -> None:
    text = _COMPOSE.read_text(encoding="utf-8")
    assert "AGENT_BASE_URL=http://agent:8090" in text
    assert "AGENT_BASE_URL=http://agent:${AGENT_PORT}" not in text


def test_host_llm_overlay_drops_the_llm_container() -> None:
    text = (_COMPOSE.parent / "config" / "compose" / "host-llm.yaml").read_text(encoding="utf-8")
    assert 'profiles: ["container-llm"]' in text
    assert "ports: !override []" in text
    assert "depends_on: !override" in text


def test_serena_sidecar_is_reachable_by_the_agent_and_not_the_host() -> None:
    """The sidecar shares the agent's tools network and is not published on the host."""
    loaded = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    serena = loaded["services"]["serena"]
    agent = loaded["services"]["agent"]
    assert loaded["networks"]["tools-net"]["internal"] is True
    assert serena.get("networks") == ["tools-net"]
    assert "tools-net" in agent["networks"]
    assert "publish-net" not in (serena.get("networks") or [])
    assert not serena.get("ports")
    command = " ".join(serena["command"])
    assert serena["entrypoint"] == ["/bin/bash", "-c"]
    assert "--host 0.0.0.0" in command
    assert "--port 9121" in command
    assert "--project /workspace/app" in command


def test_serena_checkout_is_read_write_and_compile_cache_is_read_only() -> None:
    """Serena writes the checkout. The CMake cache tools writes stays read-only."""
    services = _services()
    serena = services["serena"]["volumes"]
    tools = services["tools"]["volumes"]
    checkout = "${CODEBASE_PATH:-./sample}:/workspace/app"
    cache = "cmake-cache:/workspace/scratch/cmake-cache"
    assert f"{checkout}:ro" in tools
    assert checkout in serena
    assert f"{cache}" in tools
    assert f"{cache}:ro" not in tools
    assert f"{cache}:ro" in serena


def test_serena_refuses_git_svn_and_env_write_targets() -> None:
    """The sidecar configuration refuses the same write targets patch refuses."""
    config_path = _COMPOSE.parent / "config" / "serena" / "serena_config.yml"
    ignored = yaml.safe_load(config_path.read_text(encoding="utf-8"))["ignored_paths"]
    assert ".git" in ignored
    assert ".svn" in ignored
    assert ".env" in ignored
    assert ".env.*" in ignored
    mounted = _services()["serena"]["volumes"]
    assert "./config/serena/serena_config.yml:/opt/pumba/serena_config.yml:ro" in mounted
    assert not any("/workspaces/serena/config/serena_config.yml" in item for item in mounted)
    command = " ".join(_services()["serena"]["command"])
    assert "cp /opt/pumba/serena_config.yml /workspaces/serena/config/serena_config.yml" in command


def _serena_config() -> dict:
    path = _COMPOSE.parent / "config" / "serena" / "serena_config.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_serena_shell_repl_jetbrains_dashboard_and_memories_stay_off() -> None:
    cfg = _serena_config()
    excluded = set(cfg["excluded_tools"])
    assert cfg["agent_interface"] == "tools"
    assert "serena_repl" in excluded
    assert "execute_shell_command" in excluded
    assert cfg["language_backend"] == "LSP"
    assert not cfg.get("jetbrains_launch_command")
    assert "open_dashboard" in excluded
    assert cfg["web_dashboard"] is False
    assert "no-memories" in cfg["base_modes"]
    memories = cfg["project_serena_folder_location"]
    assert "$projectDir" not in memories
    assert not memories.startswith("/workspace")


def test_strict_health_fails_the_stack_when_serena_is_dead() -> None:
    """A dead sidecar fails the stack when strict health is on, as a dead tools does."""
    serena = _services()["serena"]
    agent = _services()["agent"]
    assert agent["depends_on"]["tools"]["condition"] == "service_healthy"
    assert agent["depends_on"]["serena"]["condition"] == "service_healthy"
    assert "SERENA_HEALTH_STRICT=${SERENA_HEALTH_STRICT:-1}" in serena["environment"]
    check = " ".join(serena["healthcheck"]["test"])
    assert "SERENA_HEALTH_STRICT" in check
    assert "9121" in check
    assert "exit 1" in check
    host_llm = (_COMPOSE.parent / "config" / "compose" / "host-llm.yaml").read_text(encoding="utf-8")
    waited = host_llm.split("serena:", 1)[1]
    assert "service_healthy" in waited[:80]


def test_running_serena_is_selected_by_an_image_tag() -> None:
    """Upgrading the sidecar is an image tag, not an agent code change."""
    serena = _services()["serena"]
    assert serena["image"] == "ghcr.io/oraios/serena:2.1.0"
    assert "build" not in serena


def test_ui_proxy_runs_unprivileged_nginx() -> None:
    spec = _services()["ui-proxy"]
    assert "nginx-unprivileged" in spec["image"]
    assert spec.get("read_only") is True
    conf = (_COMPOSE.parent / "containers" / "host-proxy" / "nginx.conf").read_text(encoding="utf-8")
    directives = [line.split("#", 1)[0].strip() for line in conf.splitlines()]
    assert not any(line.startswith("user ") for line in directives), "nginx.conf must not switch user"
    check = spec.get("healthcheck") or {}
    assert "wget" in " ".join(check.get("test") or []), "ui-proxy must healthcheck the proxied /health"
