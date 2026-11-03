"""Host llama-server command for the EVO-X5. Does not launch the binary."""

from __future__ import annotations

from pathlib import Path

import evo_x5_serve as serve


def test_vulkan_command_pins_alias_context_and_the_temperature_pair():
    weight = Path("models/UD-Q5_K_M/model.gguf")
    none = serve.serve_argv("llama-server", weight, reasoning="none", port=8080)
    high = serve.serve_argv("llama-server", weight, reasoning="high", port=8080)
    assert "--alias" in none and none[none.index("--alias") + 1] == "mistral-small-4"
    assert "--host" in none and none[none.index("--host") + 1] == "127.0.0.1"
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


def test_run_stops_when_the_binary_is_missing(monkeypatch):
    monkeypatch.setattr(serve.shutil, "which", lambda _name: None)
    assert serve.main(["--run", "--binary", "llama-server-absent"]) == 1


def test_rocm_is_not_a_campaign_run(capsys):
    assert serve.main(["--backend", "rocm"]) == 0
    assert "ROCm" in capsys.readouterr().err
