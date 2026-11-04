"""Runtime names come from `services/common/product.py`, not scattered literals."""

from __future__ import annotations

from pathlib import Path

import export_repo
import product
import workspaces

import deploy

REPO = Path(__file__).resolve().parents[2]


def test_host_scripts_read_the_product_module():
    assert workspaces.MCP_SERVER_NAME == product.MCP_SERVER
    assert export_repo.ARCHIVE_ROOT == product.ARCHIVE_ROOT
    assert deploy.WSL_DISTRO_DEFAULT == product.WSL_DISTRO


def test_compose_project_matches_product():
    text = (REPO / "compose.yaml").read_text(encoding="utf-8")
    assert f"\nname: {product.COMPOSE_PROJECT}\n" in text


def test_grafana_dashboard_matches_slug():
    path = REPO / "config" / "observability" / "grafana" / "dashboards" / f"{product.SLUG}.json"
    body = path.read_text(encoding="utf-8")
    assert f'"uid": "{product.SLUG}"' in body
    overlay = (REPO / "config" / "compose" / "observability.yaml").read_text(encoding="utf-8")
    assert f"{product.SLUG}.json" in overlay
