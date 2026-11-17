"""Host llama-server command for the EVO-X5. Does not launch the binary."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import evo_x5_serve as serve


def test_vulkan_command_pins_alias_context_and_the_temperature_pair():
    weight = Path("models/UD-Q5_K_M/model.gguf")
    none = serve.serve_argv("llama-server", weight, reasoning="none", port=8080, context="262144")
    high = serve.serve_argv("llama-server", weight, reasoning="high", port=8080, context="262144")
    assert "--alias" in none and none[none.index("--alias") + 1] == "mistral-small-4"
    assert "--host" in none and none[none.index("--host") + 1] == "0.0.0.0"
    assert none[none.index("-c") + 1] == "262144"
    assert "--fit" not in none
    assert "0.1" in none
    assert "0.7" in high
    assert '"reasoning_effort":"high"' in high[high.index("--chat-template-kwargs") + 1]
    assert serve.serve_env("vulkan") == {}
    assert serve.serve_env("rocm") == {"GGML_CUDA_ENABLE_UNIFIED_MEMORY": "1"}


def test_split_names_and_missing_shards(tmp_path: Path):
    first = tmp_path / "Mistral-Small-4-119B-2603-UD-Q5_K_M-00001-of-00003.gguf"
    shards = serve.sibling_shards(first)
    assert [path.name[-19:] for path in shards] == [
        "00001-of-00003.gguf",
        "00002-of-00003.gguf",
        "00003-of-00003.gguf",
    ]
    assert serve.missing_shards(first) == shards
    first.write_bytes(b"x")
    assert serve.missing_shards(first) == shards[1:]


def test_print_does_not_require_the_binary(capsys):
    assert serve.main(["--reasoning", "high"]) == 0
    out = capsys.readouterr().out
    assert "mistral-small-4" in out
    assert "reasoning_effort" in out
    assert "0.7" in out
    assert "262144" in out


def _packed(kind: str, binary_name: str) -> bytes:
    if kind == "zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr(binary_name, b"vulkan-binary")
        return buf.getvalue()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        payload = b"vulkan-binary"
        info = tarfile.TarInfo(name=binary_name)
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def test_missing_server_is_installed_from_the_pinned_vulkan_release(tmp_path, monkeypatch):
    cases = (
        ("win32", "llama-b10216-bin-win-vulkan-x64.zip", "llama-server.exe", "zip"),
        ("linux", "llama-b10216-bin-ubuntu-vulkan-x64.tar.gz", "llama-server", "tar"),
    )
    for system, asset, binary_name, kind in cases:
        payload = _packed(kind, binary_name)
        seen: list[str] = []

        def opener(url: str, _payload: bytes = payload) -> io.BytesIO:
            seen.append(url)
            return io.BytesIO(_payload)

        cache = tmp_path / system
        installed = serve.install_pinned_server(cache, backend="vulkan", system=system, opener=opener)
        assert installed == cache / binary_name
        assert installed.read_bytes() == b"vulkan-binary"
        assert seen == [
            "https://github.com/ggml-org/llama.cpp/releases/download/b10216/" + asset
        ]
        again = serve.install_pinned_server(
            cache, backend="vulkan", system=system, opener=lambda _url: (_ for _ in ()).throw(AssertionError("second"))
        )
        assert again == installed


def test_run_uses_the_installed_binary_when_path_has_none(monkeypatch, tmp_path):
    binary = tmp_path / "llama-server"
    binary.write_bytes(b"x")
    monkeypatch.setattr(serve.shutil, "which", lambda _name: None)
    monkeypatch.setattr(serve, "install_pinned_server", lambda *_a, **_k: binary)
    monkeypatch.setattr(serve, "missing_shards", lambda _path: [])
    seen: dict[str, list[str]] = {}

    def fake_run(command, **_kwargs):
        seen["command"] = command
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(serve.subprocess, "run", fake_run)
    assert serve.main(["--run"]) == 0
    assert seen["command"][0] == str(binary)


def test_run_stops_when_the_binary_cannot_be_installed(monkeypatch):
    monkeypatch.setattr(serve.shutil, "which", lambda _name: None)
    monkeypatch.setattr(serve, "install_pinned_server", lambda *_a, **_k: None)
    assert serve.main(["--run", "--binary", "llama-server-absent"]) == 1


def test_rocm_is_not_a_campaign_run(capsys):
    assert serve.main(["--backend", "rocm"]) == 0
    assert "ROCm" in capsys.readouterr().err
