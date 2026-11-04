#!/usr/bin/env python3
"""Serve Mistral Small 4 on the GMKtec EVO-X5 Pro.

The CUDA image in this repo never sees the Radeon 8065S. llama-server runs
on the host, Vulkan, on every interface of this machine so the agent
container can call it. The page and MCP stay on 127.0.0.1.
`--backend rocm` prints a probe and `--run` refuses it.

    python scripts/host/evo_x5_serve.py
    python scripts/host/evo_x5_serve.py --reasoning high
    python scripts/host/evo_x5_serve.py --run

`--run` needs `llama-server` on PATH and the three Q5 shards under `models/`.
Without `--run` the command is only printed. P-MODE on the chassis stays on
Performance (120 W). Silent and Balanced drop the sustained clocks.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE = REPO_ROOT / "config" / "profiles" / "evo-x5.env"
ALIAS = "mistral-small-4"
# Physical cores of the Ryzen AI Max+ PRO 495. Logical threads are the slow path.
THREADS = "16"

# Mistral's pair for this model: none is 0.1, high is 0.7.
TEMPERATURE = {"none": "0.1", "high": "0.7"}


def profile_map(path: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        found[key.strip()] = value.strip()
    return found


def host_weight(container_path: str) -> Path:
    """`/models/...` in the profile is `models/...` on the host."""
    prefix = "/models/"
    if not container_path.startswith(prefix):
        raise SystemExit(f"[evo-x5] LLM_MODEL_PATH must start with {prefix}: {container_path}")
    return REPO_ROOT / "models" / container_path[len(prefix) :]


def sibling_shards(first: Path) -> list[Path]:
    """`*-00001-of-00003.gguf` plus the rest of the split, in order."""
    marker = "-00001-of-"
    if marker not in first.name:
        return [first]
    prefix, rest = first.name.split(marker, 1)
    count = int(rest.split(".", 1)[0])
    return [first.with_name(f"{prefix}-{index:05d}-of-{rest}") for index in range(1, count + 1)]


def missing_shards(first: Path) -> list[Path]:
    return [path for path in sibling_shards(first) if not path.is_file()]


def serve_env(backend: str) -> dict[str, str]:
    """ROCm on this APU sees the BIOS slice unless unified memory is enabled.

    Vulkan already adds the shared heap (about 176 Go on the 192 Go chassis).
    This profile serves on Vulkan. `--backend rocm` is a probe.
    """
    if backend == "rocm":
        return {"GGML_CUDA_ENABLE_UNIFIED_MEMORY": "1"}
    return {}


def context_tokens(profile: dict[str, str]) -> str:
    """Trained window from the profile. The host server and the agent share it."""
    raw = profile.get("LLM_CTX_SIZE", "")
    if not raw.isdigit() or int(raw) < 1:
        raise SystemExit("[evo-x5] LLM_CTX_SIZE manquant dans config/profiles/evo-x5.env")
    return raw


def serve_argv(
    binary: str,
    weight: Path,
    *,
    reasoning: str,
    port: int,
    context: str,
) -> list[str]:
    """Flags that keep every local run on the same window, cache, and threads.

    `--fit` is omitted on purpose. On this APU it spills experts onto the CPU
    when the reported heap is only the BIOS reservation.
    """
    if reasoning not in TEMPERATURE:
        raise SystemExit(f"[evo-x5] reasoning must be none or high, not {reasoning!r}")
    return [
        binary,
        "-m",
        str(weight),
        "--alias",
        ALIAS,
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        "--jinja",
        "--chat-template-kwargs",
        json.dumps({"reasoning_effort": reasoning}, separators=(",", ":")),
        "--temp",
        TEMPERATURE[reasoning],
        "-ngl",
        "999",
        "-fa",
        "on",
        "-c",
        context,
        "-t",
        THREADS,
        "-tb",
        THREADS,
        "--cache-type-k",
        "q8_0",
        "--cache-type-v",
        "q8_0",
        "--metrics",
    ]


def format_invocation(argv: list[str], env: dict[str, str]) -> str:
    prefix = " ".join(f"{key}={value}" for key, value in env.items())
    body = " ".join(argv)
    return f"{prefix} {body}".strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reasoning", choices=("none", "high"), default="none")
    parser.add_argument("--backend", choices=("vulkan", "rocm"), default="vulkan")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--binary", default="llama-server")
    parser.add_argument("--run", action="store_true", help="exec llama-server; default is print only")
    args = parser.parse_args(argv)

    profile = profile_map(PROFILE)
    weight = host_weight(profile["LLM_MODEL_PATH"])
    context = context_tokens(profile)
    command = serve_argv(
        args.binary,
        weight,
        reasoning=args.reasoning,
        port=args.port,
        context=context,
    )
    env = serve_env(args.backend)
    print(format_invocation(command, env), flush=True)
    if args.backend != "vulkan":
        print(
            "[evo-x5] Vulkan est le moteur de ce profil. --run avec ROCm s'arrête.",
            file=sys.stderr,
            flush=True,
        )
    print(
        "[evo-x5] P-MODE sur Performance. Ne démarre pas le service compose llm : le port est celui de l'hôte.",
        flush=True,
    )
    if not args.run:
        return 0

    resolved = args.binary if Path(args.binary).is_file() else shutil.which(args.binary)
    if not resolved:
        print(f"[evo-x5] {args.binary} introuvable sur PATH.", file=sys.stderr, flush=True)
        return 1
    missing = missing_shards(weight)
    if missing:
        print(
            "[evo-x5] shards absents. python scripts/deploy.py fetch-extra evo-x5",
            file=sys.stderr,
            flush=True,
        )
        for path in missing:
            print(f"  {path}", file=sys.stderr, flush=True)
        return 1
    if args.backend != "vulkan":
        return 2
    run_env = os.environ.copy()
    run_env.update(env)
    command[0] = resolved
    try:
        return subprocess.run(command, env=run_env, check=False).returncode or 0
    except OSError as exc:
        print(f"[evo-x5] {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
