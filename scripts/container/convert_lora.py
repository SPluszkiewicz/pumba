#!/usr/bin/env python3
"""Convert a PEFT LoRA adapter directory to adapter.gguf for llama-server.

Intended to run inside the `trainer` container (has llama.cpp b10216 at
LLAMA_CPP_DIR). Example:

    podman compose --profile train run --rm trainer \\
        python /workspace/scripts/convert_lora.py \\
        --adapter /models/adapters/latest \\
        --outfile /models/adapters/latest/adapter.gguf
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-model",
        default=os.environ.get("TRAIN_BASE_MODEL", "/models/hf/Qwen2.5-7B-Instruct"),
        help="Hugging Face base model directory",
    )
    parser.add_argument(
        "--adapter",
        default="/models/adapters/latest",
        help="PEFT adapter directory (adapter_config.json + adapter_model.safetensors)",
    )
    parser.add_argument(
        "--outfile",
        default="/models/adapters/latest/adapter.gguf",
        help="Output GGUF path for llama-server --lora",
    )
    parser.add_argument(
        "--llama-cpp-dir",
        default=os.environ.get("LLAMA_CPP_DIR", "/opt/llama.cpp"),
    )
    args = parser.parse_args()

    base_model = Path(args.base_model)
    adapter_dir = Path(args.adapter)
    outfile = Path(args.outfile)
    script = Path(args.llama_cpp_dir) / "convert_lora_to_gguf.py"

    if not base_model.is_dir():
        print(f"[convert_lora] FATAL: base model not found: {base_model}", file=sys.stderr)
        return 1
    if not (adapter_dir / "adapter_config.json").exists():
        print(f"[convert_lora] FATAL: adapter not found: {adapter_dir}", file=sys.stderr)
        return 1
    if not script.exists():
        print(f"[convert_lora] FATAL: {script} missing (run inside trainer image)", file=sys.stderr)
        return 1

    outfile.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(script),
        str(adapter_dir),
        "--outfile",
        str(outfile),
        "--outtype",
        "f16",
        "--base",
        str(base_model),
    ]
    print(f"[convert_lora] $ {' '.join(cmd)}", flush=True)
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        return 1
    print(f"[convert_lora] wrote {outfile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
