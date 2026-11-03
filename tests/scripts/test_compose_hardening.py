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


def test_ui_proxy_runs_unprivileged_nginx() -> None:
    spec = _services()["ui-proxy"]
    assert "nginx-unprivileged" in spec["image"]
    assert spec.get("read_only") is True
    conf = (_COMPOSE.parent / "containers" / "host-proxy" / "nginx.conf").read_text(encoding="utf-8")
    directives = [line.split("#", 1)[0].strip() for line in conf.splitlines()]
    assert not any(line.startswith("user ") for line in directives), "nginx.conf must not switch user"
