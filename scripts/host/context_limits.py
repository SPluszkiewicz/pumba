#!/usr/bin/env python3
"""Estimate how much context a GGUF can hold on the GPU.

Reads the model architecture from the GGUF header (layers, heads, trained
context) and the GPU memory from nvidia-smi, then prints the largest context
that still fits for each KV-cache type llama-server accepts.

Usage (from the repo root):
    python scripts/host/context_limits.py
    python scripts/host/context_limits.py --model models/Qwen2.5-Coder-14B-Instruct-Q5_K_M.gguf
    python scripts/host/context_limits.py --vram-mib 16376 --margin-mib 2048
"""

from __future__ import annotations

import argparse
import struct
import subprocess
import sys
from pathlib import Path

# Bytes per 32 KV elements. Same layout as ggml block types.
_BLOCK = 32
_KV_BYTES_PER_BLOCK = {
    "f16": 64,
    "bf16": 64,
    "f32": 128,
    "q8_0": 34,
    "q5_1": 24,
    "q5_0": 22,
    "q4_1": 20,
    "q4_0": 18,
    "iq4_nl": 18,
}
# CUDA context + compute graph, not the KV cache. Measured on the 14B Q5
# at 32768 tokens with a q8_0 cache: nvidia-smi used minus file size minus KV.
COMPUTE_RESERVE_MIB = 1024

_GGUF_MAGIC = b"GGUF"
_U8, _I8, _U16, _I16, _U32, _I32, _F32, _BOOL, _STR, _ARR, _U64, _I64, _F64 = range(13)


class GGUFError(ValueError):
    pass


def _read_exact(handle, size: int) -> bytes:
    data = handle.read(size)
    if len(data) != size:
        raise GGUFError("truncated GGUF header")
    return data


def _read_value(handle, vtype: int) -> object:
    if vtype == _U8:
        return _read_exact(handle, 1)[0]
    if vtype == _I8:
        return struct.unpack("<b", _read_exact(handle, 1))[0]
    if vtype == _U16:
        return struct.unpack("<H", _read_exact(handle, 2))[0]
    if vtype == _I16:
        return struct.unpack("<h", _read_exact(handle, 2))[0]
    if vtype == _U32:
        return struct.unpack("<I", _read_exact(handle, 4))[0]
    if vtype == _I32:
        return struct.unpack("<i", _read_exact(handle, 4))[0]
    if vtype == _F32:
        return struct.unpack("<f", _read_exact(handle, 4))[0]
    if vtype == _BOOL:
        return bool(_read_exact(handle, 1)[0])
    if vtype == _U64:
        return struct.unpack("<Q", _read_exact(handle, 8))[0]
    if vtype == _I64:
        return struct.unpack("<q", _read_exact(handle, 8))[0]
    if vtype == _F64:
        return struct.unpack("<d", _read_exact(handle, 8))[0]
    if vtype == _STR:
        length = struct.unpack("<Q", _read_exact(handle, 8))[0]
        return _read_exact(handle, length).decode("utf-8", "replace")
    if vtype == _ARR:
        subtype = struct.unpack("<I", _read_exact(handle, 4))[0]
        count = struct.unpack("<Q", _read_exact(handle, 8))[0]
        return [_read_value(handle, subtype) for _ in range(count)]
    raise GGUFError(f"unknown GGUF value type {vtype}")


def read_gguf_metadata(path: Path) -> dict[str, object]:
    """Read metadata key/value pairs. Stops before the tensor data."""
    with path.open("rb") as handle:
        header = _read_exact(handle, 24)
        if header[:4] != _GGUF_MAGIC:
            raise GGUFError(f"{path} is not a GGUF file")
        _version, _n_tensors, n_kv = struct.unpack_from("<IQQ", header, 4)
        meta: dict[str, object] = {}
        for _ in range(n_kv):
            key = _read_value(handle, _STR)
            vtype = struct.unpack("<I", _read_exact(handle, 4))[0]
            value = _read_value(handle, vtype)
            if isinstance(key, str):
                meta[key] = value
    return meta


def architecture(meta: dict[str, object]) -> dict[str, int]:
    arch = str(meta.get("general.architecture") or "")
    if not arch:
        raise GGUFError("GGUF has no general.architecture")

    def need(suffix: str) -> int:
        key = f"{arch}.{suffix}"
        value = meta.get(key)
        if not isinstance(value, int):
            raise GGUFError(f"missing {key}")
        return value

    n_embd = need("embedding_length")
    n_head = need("attention.head_count")
    n_kv = meta.get(f"{arch}.attention.head_count_kv")
    if not isinstance(n_kv, int):
        n_kv = n_head
    key_length = meta.get(f"{arch}.attention.key_length")
    head_dim = key_length if isinstance(key_length, int) and key_length > 0 else n_embd // n_head
    trained = meta.get(f"{arch}.context_length")
    return {
        "n_layer": need("block_count"),
        "n_embd": n_embd,
        "n_head": n_head,
        "n_kv": n_kv,
        "head_dim": head_dim,
        "trained_ctx": trained if isinstance(trained, int) else 0,
    }


def kv_mib_per_token(arch: dict[str, int], cache_type: str) -> float:
    block = _KV_BYTES_PER_BLOCK[cache_type]
    elements = 2 * arch["n_layer"] * arch["n_kv"] * arch["head_dim"]
    return elements * (block / _BLOCK) / (1024 * 1024)


def max_context(kv_mib: float, budget_mib: float) -> int:
    if kv_mib <= 0 or budget_mib <= 0:
        return 0
    return int(budget_mib // kv_mib)


def gpu_vram_mib() -> int | None:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    line = out.strip().splitlines()[0].strip()
    return int(line) if line.isdigit() else None


def _env_model() -> Path | None:
    env = Path(".env")
    if not env.is_file():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("LLM_MODEL_PATH="):
            raw = line.split("=", 1)[1].strip()
            relative = raw.removeprefix("/models/").lstrip("/")
            candidate = Path("models") / (relative or Path(raw).name)
            if candidate.is_file():
                return candidate
    return None


def report(model: Path, vram_mib: int, margin_mib: int) -> str:
    meta = read_gguf_metadata(model)
    arch = architecture(meta)
    weights_mib = model.stat().st_size / (1024 * 1024)
    budget = vram_mib - margin_mib - weights_mib - COMPUTE_RESERVE_MIB
    name = meta.get("general.name") or model.name
    lines = [
        f"Modèle : {name}",
        f"Fichier : {model} ({weights_mib:.0f} Mio de poids)",
        (
            f"Architecture : {arch['n_layer']} couches, {arch['n_kv']} têtes KV, "
            f"head {arch['head_dim']}"
        ),
        f"Contexte d'entraînement : {arch['trained_ctx'] or 'inconnu'} tokens",
        f"VRAM : {vram_mib} Mio, marge {margin_mib} Mio, réserve calcul {COMPUTE_RESERVE_MIB} Mio",
        f"Budget restant pour le cache KV : {budget:.0f} Mio",
        "",
        f"{'cache KV':<10} {'Mio/token':>10} {'max GPU':>10} {'plafond':>10}",
    ]
    for cache_type in ("f16", "q8_0", "q5_0", "q4_0"):
        per_token = kv_mib_per_token(arch, cache_type)
        room = max_context(per_token, budget)
        cap = arch["trained_ctx"]
        limited = min(room, cap) if cap else room
        lines.append(f"{cache_type:<10} {per_token:10.4f} {room:10d} {limited:10d}")
    lines.append("")
    lines.append(
        "max GPU = tokens qui tiennent dans la VRAM avec la marge. "
        "plafond = ce maximum borné par le contexte d'entraînement du modèle."
    )
    if budget <= 0:
        lines.append("Le budget KV est négatif : le modèle ne tient pas sur ce GPU avec cette marge.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Context limits for a GGUF on this GPU")
    parser.add_argument("--model", type=Path, help="GGUF path (default: LLM_MODEL_PATH from .env)")
    parser.add_argument("--vram-mib", type=int, help="override nvidia-smi memory.total")
    parser.add_argument("--margin-mib", type=int, default=2048, help="VRAM to leave free (default 2048)")
    args = parser.parse_args(argv)
    model = args.model or _env_model()
    if model is None or not model.is_file():
        print("modèle introuvable : passez --model chemin/vers/fichier.gguf", file=sys.stderr)
        return 1
    vram = args.vram_mib if args.vram_mib is not None else gpu_vram_mib()
    if vram is None:
        print("nvidia-smi indisponible : passez --vram-mib", file=sys.stderr)
        return 1
    print(report(model, vram, args.margin_mib))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
