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
import tarfile
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE = REPO_ROOT / "config" / "profiles" / "evo-x5.env"
ALIAS = "mistral-small-4"
# Physical cores of the Ryzen AI Max+ PRO 495. Logical threads are the slow path.
THREADS = "16"

# Mistral's pair for this model: none is 0.1, high is 0.7.
TEMPERATURE = {"none": "0.1", "high": "0.7"}
# Same tag as the container images (CONTRACTS.md). Vulkan build: the EVO GPU
# is not NVIDIA, and this build also runs on an NVIDIA host.
LLAMA_CPP_TAG = "b10216"
_VULKAN_ASSETS = {
    "win32": "llama-b10216-bin-win-vulkan-x64.zip",
    "linux": "llama-b10216-bin-ubuntu-vulkan-x64.tar.gz",
}
BINARY_DIR = REPO_ROOT / "state" / "llama-server"


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


def _binary_filename(system: str) -> str:
    return "llama-server.exe" if system == "win32" else "llama-server"


def _contained(name: str) -> bool:
    path = Path(name)
    return bool(name) and name != "." and not path.is_absolute() and ".." not in path.parts


def _find_binary(dest: Path, binary_name: str) -> Path | None:
    direct = dest / binary_name
    if direct.is_file():
        return direct
    matches = [path for path in dest.rglob(binary_name) if path.is_file()]
    return matches[0] if matches else None


def _download(url: str, dest: Path, opener) -> None:
    with opener(url) as response, dest.open("wb") as out:
        shutil.copyfileobj(response, out)


def _extract(archive: Path, dest: Path) -> None:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zipped:
            for info in zipped.infolist():
                if _contained(info.filename):
                    zipped.extract(info, dest)
        return
    with tarfile.open(archive, "r:gz") as packed:
        members = [
            member
            for member in packed.getmembers()
            if _contained(member.name) and not member.issym() and not member.islnk()
        ]
        packed.extractall(dest, members=members)


def _open_release(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "pumba-deploy"})
    return urllib.request.urlopen(request, timeout=180)


def install_pinned_server(dest: Path, *, backend: str, system: str, opener=None) -> Path | None:
    """Download the pinned Vulkan llama-server into `dest` when it is not there yet."""
    asset = _VULKAN_ASSETS.get(system) if backend == "vulkan" else None
    if asset is None:
        print(
            f"[evo-x5] pas de binaire {LLAMA_CPP_TAG} pour {system} / {backend}.",
            file=sys.stderr,
            flush=True,
        )
        return None
    dest = Path(dest)
    binary_name = _binary_filename(system)
    if dest.is_dir():
        found = _find_binary(dest, binary_name)
        if found is not None:
            return found
    url = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_CPP_TAG}/{asset}"
    print(f"[evo-x5] llama-server absent du PATH. Téléchargement de {asset}", file=sys.stderr, flush=True)
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / asset
    try:
        _download(url, archive, opener or _open_release)
        _extract(archive, dest)
    except OSError as exc:
        print(f"[evo-x5] installation impossible : {exc}", file=sys.stderr, flush=True)
        return None
    finally:
        archive.unlink(missing_ok=True)
    found = _find_binary(dest, binary_name)
    if found is None:
        print(f"[evo-x5] {binary_name} absent de l'archive.", file=sys.stderr, flush=True)
        return None
    if system != "win32":
        found.chmod(found.stat().st_mode | 0o755)
    return found


def ensure_binary(name: str, *, backend: str, dest: Path | None = None) -> str | None:
    """PATH, an explicit file, or the pinned release cached under state/llama-server."""
    if Path(name).is_file():
        return str(Path(name))
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        system = "win32"
    elif sys.platform.startswith("linux"):
        system = "linux"
    else:
        system = sys.platform
    installed = install_pinned_server(dest or BINARY_DIR, backend=backend, system=system)
    return str(installed) if installed else None


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

    resolved = ensure_binary(args.binary, backend=args.backend)
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
