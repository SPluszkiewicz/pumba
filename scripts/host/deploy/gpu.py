"""NVIDIA CDI / Docker GPU probes, CPU fallback, and GPU profiles."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

import deploy as d


def apply_profile_text(env_text: str, overlay: dict[str, str]) -> str:
    """Replace existing KEY= lines from a GPU profile. Leave every other line, including the token."""
    seen: set[str] = set()
    out: list[str] = []
    for raw_line in env_text.splitlines():
        stripped = raw_line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in overlay:
                out.append(f"{key}={overlay[key]}")
                seen.add(key)
                continue
        out.append(raw_line)
    missing = [key for key in overlay if key not in seen]
    if missing:
        if out and out[-1] != "":
            out.append("")
        out.append("# gpu profile")
        for key in missing:
            out.append(f"{key}={overlay[key]}")
    return "\n".join(out) + ("\n" if env_text.endswith("\n") or out else "")


def _env_gpu_build_requested(env: dict[str, str]) -> bool:
    llm = (env.get("LLM_GPU_ARG") or "1").strip() or "1"
    embedder = (env.get("EMBEDDER_GPU_ARG") or "1").strip() or "1"
    return llm != "0" or embedder != "0"


def _cdi_list_contains_gpu() -> bool:
    if shutil.which("nvidia-ctk") is None:
        return False
    result = subprocess.run(
        ["nvidia-ctk", "cdi", "list"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and d.CDI_GPU_DEVICE in result.stdout


def _cdi_probe() -> bool:
    """Return True when Podman can attach the NVIDIA CDI device."""
    if d._cdi_list_contains_gpu():
        return True
    result = subprocess.run(
        [
            "podman",
            "run",
            "--rm",
            "--device",
            d.CDI_GPU_DEVICE,
            "ubuntu:24.04",
            "true",
        ],
        cwd=d.REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    return result.returncode == 0


def _try_generate_cdi() -> bool:
    """Generate `/etc/cdi/nvidia.yaml` via nvidia-ctk (requires sudo)."""
    if shutil.which("nvidia-ctk") is None:
        print(
            "[deploy] nvidia-ctk not found. Install nvidia-container-toolkit, then re-run:",
            file=sys.stderr,
        )
        print("[deploy]   Fedora: sudo dnf install -y nvidia-container-toolkit", file=sys.stderr)
        print(
            "[deploy]   Ubuntu/Debian: sudo apt install -y nvidia-container-toolkit",
            file=sys.stderr,
        )
        print(
            "[deploy]   Podman machine (CoreOS): podman machine ssh, then "
            "sudo rpm-ostree install nvidia-container-toolkit && reboot",
            file=sys.stderr,
        )
        return False

    generate_cmd = [
        "nvidia-ctk",
        "cdi",
        "generate",
        f"--output={d.CDI_NVIDIA_YAML}",
    ]
    print(f"[deploy] trying: sudo {' '.join(generate_cmd)}", flush=True)
    result = subprocess.run(["sudo", *generate_cmd], capture_output=True, text=True)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "unknown error").strip()
        print(f"[deploy] CDI generate failed: {err}", file=sys.stderr)
        return False

    print(f"[deploy] wrote {d.CDI_NVIDIA_YAML}", flush=True)
    return d._cdi_probe()


def _enable_cpu_compose_fallback(*, persist_env: bool = False) -> None:
    d._USE_CPU_COMPOSE_OVERRIDE = True
    if persist_env:
        for key, value in d._CPU_FALLBACK_ENV.items():
            d._write_env_key(key, value)
    print(
        "[deploy] CPU fallback active (config/compose/cpu.yaml) - GPU offload disabled.",
        flush=True,
    )


def _docker_gpu_probe() -> bool:
    """Return True when `docker run --gpus all` can start a container."""
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "run", "--rm", "--gpus", "all", "ubuntu:24.04", "true"],
        cwd=d.REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    return result.returncode == 0


def _prepare_docker_gpu(env: dict[str, str]) -> None:
    if not d._env_gpu_build_requested(env):
        print("[deploy] GPU disabled in .env - using CPU compose overlay.")
        d._enable_cpu_compose_fallback(persist_env=False)
        return
    if d._docker_gpu_probe():
        print("[deploy] Docker NVIDIA GPU available.")
        return
    print(
        "[deploy] WARNING: Docker cannot use the GPU - falling back to CPU-only "
        "(slower). See docs/guides/troubleshooting-gpu.md.",
        file=sys.stderr,
    )
    d._enable_cpu_compose_fallback(persist_env=False)


def _prepare_gpu_for_up(env: dict[str, str]) -> None:
    """Ensure the selected engine can see an NVIDIA GPU before compose starts."""
    if d._RUNTIME == "docker":
        d._prepare_docker_gpu(env)
        return
    if not d._env_gpu_build_requested(env):
        print("[deploy] GPU disabled in .env - using CPU compose overlay.")
        d._enable_cpu_compose_fallback(persist_env=False)
        return

    if d._cdi_probe():
        print("[deploy] NVIDIA CDI device available.")
        return

    print(
        "[deploy] WARNING: NVIDIA CDI device not available - attempting to generate it...",
        file=sys.stderr,
    )
    if d._try_generate_cdi():
        print("[deploy] CDI generated successfully.")
        return

    print(
        "[deploy] WARNING: could not configure GPU/CDI - falling back to CPU-only "
        "(slower). See docs/guides/troubleshooting-gpu.md.",
        file=sys.stderr,
    )
    d._enable_cpu_compose_fallback(persist_env=False)


def cmd_gpu_profile(args: argparse.Namespace) -> int:
    name = args.name
    path = d.GPU_PROFILE_DIR / f"{name}.env"
    if name not in d.GPU_PROFILES or not path.is_file():
        sys.exit(
            f"[deploy] FATAL: unknown GPU profile {name!r}. "
            f"Choose one of: {', '.join(d.GPU_PROFILES)}."
        )
    d._ensure_env_file()
    overlay = d._parse_env_file(path)
    before = d._parse_env_file(d.ENV_FILE)
    d.ENV_FILE.write_text(
        d.apply_profile_text(d.ENV_FILE.read_text(encoding="utf-8"), overlay),
        encoding="utf-8",
    )
    print(f"[deploy] Applied GPU profile {name} to {d.ENV_FILE}.", flush=True)
    for key, value in overlay.items():
        previous = before.get(key, "")
        mark = " (unchanged)" if previous == value else f" (was {previous or 'unset'})"
        print(f"  {key}={value}{mark}", flush=True)
    if before.get("EMBEDDER_GPU_ARG") != overlay.get("EMBEDDER_GPU_ARG"):
        print(
            "[deploy] EMBEDDER_GPU_ARG changed. `python scripts/deploy.py up` will rebuild embedder.",
            flush=True,
        )
    model_path = overlay.get("LLM_MODEL_PATH", "")
    relative = model_path.removeprefix("/models/").lstrip("/")
    if relative and not (d.MODELS_DIR / relative).is_file():
        extra = d.PROFILE_EXTRA.get(name)
        if extra:
            print(
                f"[deploy] {relative} is not in {d.MODELS_DIR}. "
                "`up` fetches a missing extra GGUF; or download it now:\n"
                f"  python scripts/deploy.py fetch-extra {name}",
                flush=True,
            )
        else:
            print(
                f"[deploy] {relative} is not in {d.MODELS_DIR}. "
                "Run `python scripts/deploy.py up` once so the default 14B is fetched.",
                flush=True,
            )
    if overlay.get("LLM_SERVE") == "host":
        print(
            "[deploy] EVO-X5 Pro: `up` starts llama-server on the host and skips the llm container.",
            flush=True,
        )
        print("[deploy] python scripts/deploy.py up", flush=True)
        return 0
    print(
        "[deploy] Read nvidia-smi memory.total on the target machine. "
        "Task Manager can add shared system RAM and report ~56 Go for a 24 Go card. "
        "Then recreate llm, embedder, and reranker.",
        flush=True,
    )
    return 0


def cmd_fetch_extra(args: argparse.Namespace) -> int:
    """Download the optional GGUF for one GPU case. Does not change .env."""
    name = args.name
    extra = d.PROFILE_EXTRA.get(name)
    if extra is None:
        sys.exit(
            f"[deploy] FATAL: profile {name} uses the default 14B already fetched by `up`. "
            f"Optional downloads: {', '.join(d.PROFILE_EXTRA)}."
        )
    d._require_podman()
    d._run(
        [
            "podman",
            "compose",
            "--profile",
            "setup",
            "run",
            "--rm",
            "fetch-models",
            "python",
            "scripts/container/fetch_models.py",
            "--extra",
            extra,
        ],
        cwd=str(d.REPO_ROOT),
    )
    return 0
