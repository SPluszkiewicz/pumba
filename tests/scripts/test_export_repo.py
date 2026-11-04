"""Unit tests for `scripts/host/export_repo.py`."""

from __future__ import annotations

import zipfile
from pathlib import Path

import export_repo
import product


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("AGENT_BEARER_TOKEN=changeme\n", encoding="utf-8")
    (tmp_path / ".env").write_text("AGENT_BEARER_TOKEN=secret\n", encoding="utf-8")
    (tmp_path / "atlassian.env").write_text("JIRA=secret\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "README.md").write_text("weights go here\n", encoding="utf-8")
    (tmp_path / "models" / "big.gguf").write_bytes(b"gguf")
    (tmp_path / "models" / ".cache").mkdir()
    (tmp_path / "models" / ".cache" / "blob").write_bytes(b"cache")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "workspaces.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (tmp_path / "services" / "agent").mkdir(parents=True)
    (tmp_path / "services" / "agent" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "services" / "agent" / "__pycache__").mkdir()
    (tmp_path / "services" / "agent" / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"pyc")
    return tmp_path


def test_export_omits_models_state_venv_and_secrets(tmp_path):
    root = _tree(tmp_path)
    out = tmp_path / "out" / "export.zip"
    count = export_repo.write_archive(out, include_env=False, root=root)
    assert count >= 4
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    root_name = product.ARCHIVE_ROOT
    assert f"{root_name}/EXPORT.txt" in names
    assert f"{root_name}/compose.yaml" in names
    assert f"{root_name}/.env.example" in names
    assert f"{root_name}/README.md" in names
    assert f"{root_name}/models/README.md" in names
    assert f"{root_name}/services/agent/main.py" in names
    assert f"{root_name}/.env" not in names
    assert f"{root_name}/atlassian.env" not in names
    assert f"{root_name}/models/big.gguf" not in names
    assert not any(name.startswith(f"{root_name}/models/.cache/") for name in names)
    assert not any(name.startswith(f"{root_name}/state/") for name in names)
    assert not any(name.startswith(f"{root_name}/.venv/") for name in names)
    assert not any("__pycache__" in name for name in names)


def test_export_include_env_packs_secrets(tmp_path):
    root = _tree(tmp_path)
    out = tmp_path / "with-env.zip"
    export_repo.write_archive(out, include_env=True, root=root)
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    root_name = product.ARCHIVE_ROOT
    assert f"{root_name}/.env" in names
    assert f"{root_name}/atlassian.env" in names
    assert f"{root_name}/models/big.gguf" not in names
