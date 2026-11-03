"""Context-limit estimates from a synthetic GGUF header."""

from __future__ import annotations

import struct

import context_limits


def _gguf(path, **fields: int) -> None:
    meta = {
        "general.architecture": "qwen2",
        "qwen2.block_count": fields.get("layers", 48),
        "qwen2.embedding_length": fields.get("embd", 5120),
        "qwen2.attention.head_count": fields.get("heads", 40),
        "qwen2.attention.head_count_kv": fields.get("kv", 8),
        "qwen2.context_length": fields.get("ctx", 32768),
    }
    parts = [struct.pack("<IQQ", 3, 0, len(meta))]
    for key, value in meta.items():
        raw = key.encode()
        parts.append(struct.pack("<Q", len(raw)) + raw)
        if isinstance(value, str):
            encoded = value.encode()
            parts.append(struct.pack("<I", 8) + struct.pack("<Q", len(encoded)) + encoded)
        else:
            parts.append(struct.pack("<II", 4, value))
    path.write_bytes(b"GGUF" + b"".join(parts))


def test_q8_holds_about_twice_f16(tmp_path):
    model = tmp_path / "model.gguf"
    _gguf(model)
    meta = context_limits.read_gguf_metadata(model)
    arch = context_limits.architecture(meta)
    f16 = context_limits.kv_mib_per_token(arch, "f16")
    q8 = context_limits.kv_mib_per_token(arch, "q8_0")
    assert arch["head_dim"] == 128
    assert f16 == 2 * 48 * 8 * 128 * 2 / (1024 * 1024)
    assert q8 < f16 * 0.6
    budget = 16376 - 2048 - 9500 - context_limits.COMPUTE_RESERVE_MIB
    q8_ctx = context_limits.max_context(q8, budget)
    f16_ctx = context_limits.max_context(f16, budget)
    assert q8_ctx > f16_ctx * 1.8
    assert f16_ctx < 22000


def test_report_caps_at_trained_context(tmp_path, capsys):
    model = tmp_path / "model.gguf"
    _gguf(model, ctx=8192, layers=4, embd=256, heads=4, kv=2)
    text = context_limits.report(model, vram_mib=16376, margin_mib=2048)
    assert "8192" in text
    assert "q8_0" in text
