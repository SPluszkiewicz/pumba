"""Shared pytest setup for tests of the host-facing `scripts/` utilities.

`scripts/*.py` are plain top-level scripts (not an installed package), so
this puts `scripts/` on `sys.path` to let tests import them directly, the
same pattern used by `tests/agent/conftest.py` for `services/agent`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "scripts"
for _path in (_SCRIPTS / "container", _SCRIPTS / "host", _REPO / "services" / "common"):
    _as_str = str(_path)
    if _as_str not in sys.path:
        sys.path.insert(0, _as_str)


@pytest.fixture(autouse=True)
def _isolate_deploy_from_repo_env(monkeypatch, tmp_path):
    """Tests must not depend on the operator's .env (e.g. LLM_SERVE=host)."""
    import deploy

    monkeypatch.setattr(deploy, "ENV_FILE", tmp_path / "no-repo-env")
    monkeypatch.setattr(deploy, "HOST_LLM_PID", tmp_path / "no-host-llm.pid")
