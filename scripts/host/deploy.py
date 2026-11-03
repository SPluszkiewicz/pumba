#!/usr/bin/env python3
"""Host-side lifecycle for the stack. Run it as `python scripts/deploy.py`
(that file forwards here). Scripts under `scripts/container/` run inside a
container and are started by Compose.

`CONTAINER_RUNTIME` in `.env` is `auto` (working Podman, otherwise Docker),
`podman`, or `docker`. On Windows, if the Podman CLI cannot reach a machine
and Docker is not the one selected, the same command continues inside the
WSL distro `local-llm`. Python 3.9+ on that machine is enough; a virtualenv
is not required to deploy.

This script never invents new behavior: every step is exactly one of the
commands from the docs, just sequenced and with health/error checking added.

Usage (from the repo root):
    python scripts/deploy.py up             # first-time or repeat full deploy
    python scripts/deploy.py up --skip-models --skip-index
    python scripts/deploy.py status
    python scripts/deploy.py index
    python scripts/deploy.py workspace d:/svn/mon_projet
    python scripts/deploy.py eval D:/rest/opencv --questions scripts/host/eval_opencv_questions.json
    python scripts/deploy.py train            # QLoRA on CODEBASE_PATH, reload LoRA on llm
    python scripts/deploy.py ask "How does the indexer chunk C++ files?"
    python scripts/deploy.py ask --file my-question.md
    python scripts/deploy.py chat           # interactive REPL (SSE + conversation)
    python scripts/deploy.py logs agent --follow
    python scripts/deploy.py test           # tiny-model end-to-end smoke test
    python scripts/deploy.py doctor         # diagnose the whole stack
    python scripts/deploy.py doctor --html doctor-report.html
    python scripts/deploy.py rotate-token
    python scripts/deploy.py down
    python scripts/deploy.py down --volumes
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTAINER_DIR = REPO_ROOT / "scripts" / "container"
if str(_CONTAINER_DIR) not in sys.path:
    sys.path.append(str(_CONTAINER_DIR))
ENV_FILE = REPO_ROOT / ".env"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
MODELS_DIR = REPO_ROOT / "models"
TRAIN_LOCK_FILE = MODELS_DIR / "training" / ".train.lock"
HF_TRAIN_BASE_MARKER = MODELS_DIR / "hf" / "Qwen2.5-7B-Instruct" / "config.json"
DEFAULT_LORA_GGUF = "/models/adapters/latest/adapter.gguf"
# Services `up` waits on (each has a compose healthcheck).
CORE_SERVICES = ["qdrant", "llm", "embedder", "reranker", "tools", "agent", "mcp"]
# `ui-proxy` is the host's loopback door to `agent` (docs/getting-started/
# first-run.md). It has no healthcheck, so it is started but not waited on.
UP_SERVICES = [*CORE_SERVICES, "ui-proxy"]
# Smoke and eval_rag leave RERANKER_BASE_URL empty and do not start this
# service (the ~606 MiB GGUF is not part of `fetch_models.py --test`).
SMOKE_SERVICES = ["qdrant", "llm", "embedder", "tools", "agent", "mcp"]
PLACEHOLDER_TOKEN = "changeme-generate-a-real-secret"
CDI_GPU_DEVICE = "nvidia.com/gpu=all"
CDI_NVIDIA_YAML = Path("/etc/cdi/nvidia.yaml")
_CPU_FALLBACK_ENV = {
    "LLM_GPU_ARG": "0",
    "EMBEDDER_GPU_ARG": "0",
    "LLM_NGL": "0",
    "EMBEDDER_NGL": "0",
}
_USE_CPU_COMPOSE_OVERRIDE = False
# Matches docs/getting-started/prerequisites.md (14B Q5_K_M ~9.8 GB + embedder
# + reranker + image layers).
DISK_BUDGET_GIB = 12

CheckStatus = Literal["ok", "warn", "fail", "skip"]


@dataclass
class Check:
    """One diagnostic result for `doctor`. `hint` is only shown for
    non-"ok" statuses - it should be an actionable next step, ideally a
    command to run or a doc to read, not a restatement of `detail`."""

    name: str
    status: CheckStatus
    detail: str
    hint: str | None = None


_RUNTIME = "podman"
_VALID_RUNTIMES = ("auto", "podman", "docker")
WSL_DISTRO_DEFAULT = "local-llm"


def _compose_argv_engine(cmd: list[str]) -> bool:
    return len(cmd) >= 2 and cmd[1] == "compose" and cmd[0] in ("podman", "docker")


def _compose_cmd(*parts: str) -> list[str]:
    """Build a compose argv for the selected engine.

    Docker gets compose.docker.yaml so CDI devices are replaced by `gpus: all`.
    CPU fallback uses compose.cpu.yaml. On Docker it also stacks
    compose.docker.cpu.yaml, which drops the GPU reservation.
    Podman gets compose.podman.yaml so publish-net has no default route.
    """
    engine = "docker" if _RUNTIME == "docker" else "podman"
    cmd = [engine, "compose", "-f", "compose.yaml"]
    if engine == "docker" and not _USE_CPU_COMPOSE_OVERRIDE:
        cmd.extend(["-f", "compose.docker.yaml"])
    if _USE_CPU_COMPOSE_OVERRIDE:
        cmd.extend(["-f", "compose.cpu.yaml"])
        if engine == "docker":
            cmd.extend(["-f", "compose.docker.cpu.yaml"])
    if engine == "podman":
        cmd.extend(["-f", "compose.podman.yaml"])
    mounts = REPO_ROOT / "compose.workspaces.yaml"
    if mounts.is_file():
        cmd.extend(["-f", "compose.workspaces.yaml"])
    cmd.extend(parts)
    return cmd


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    if _compose_argv_engine(cmd):
        cmd = _compose_cmd(*cmd[2:])
    print(f"[deploy] $ {' '.join(cmd)}", flush=True)
    check = kwargs.pop("check", True)
    env = kwargs.pop("env", None)
    if env is None and _compose_argv_engine(cmd):
        env = _compose_env()
    run_kwargs = {"cwd": REPO_ROOT, "check": check, **kwargs}
    if env is not None:
        run_kwargs["env"] = env
    return subprocess.run(cmd, **run_kwargs)


def _command_output(result: subprocess.CompletedProcess[str]) -> str:
    parts: list[str] = []
    for text in (getattr(result, "stdout", None), getattr(result, "stderr", None)):
        cleaned = (text or "").strip()
        if cleaned:
            parts.append(cleaned)
    return "\n".join(parts)


def _podman_compose_hint(detail: str) -> str:
    """Hint for a failed `podman compose version`. Connection failures are not a missing binary."""
    lowered = detail.lower()
    if "cannot connect" in lowered or "podman.sock" in lowered or "podman machine" in lowered:
        linux_repo = _normalize_codebase_path(str(REPO_ROOT))
        return (
            "The podman CLI on PATH cannot reach a Linux VM. On this setup Podman "
            "runs in the WSL distro local-llm (the default Ubuntu distro is not that VM). "
            "Start it and run deploy from there:\n"
            "  wsl -d local-llm\n"
            f"  cd {linux_repo}\n"
            "  python3 scripts/deploy.py up\n"
            "See docs/getting-started/prerequisites.md."
        )
    return (
        "Compose support isn't working (bundled `podman compose` or standalone "
        "`podman-compose`). See docs/getting-started/prerequisites.md."
    )


def _compose_failure_hint(engine: str, detail: str) -> str:
    if engine == "podman":
        return _podman_compose_hint(detail)
    return (
        "Docker Compose isn't working. Install Docker Desktop, or set "
        "CONTAINER_RUNTIME=podman. See docs/getting-started/prerequisites.md."
    )


def _requested_runtime(env: dict[str, str]) -> str:
    raw = (os.environ.get("CONTAINER_RUNTIME") or env.get("CONTAINER_RUNTIME") or "auto").strip().lower()
    if raw not in _VALID_RUNTIMES:
        sys.exit(
            "[deploy] FATAL: CONTAINER_RUNTIME="
            f"{raw!r} must be auto, podman, or docker."
        )
    return raw


def _connection_failure(detail: str) -> bool:
    lowered = detail.lower()
    return "cannot connect" in lowered or "podman.sock" in lowered or "podman machine" in lowered


def choose_runtime(
    requested: str,
    *,
    podman_ok: bool,
    docker_ok: bool,
    podman_detail: str,
    on_windows: bool,
) -> tuple[str, bool]:
    """Return `(engine, reexec_into_wsl)`.

    `auto` prefers a working Podman, then Docker. On Windows, a Podman CLI
    that cannot reach a machine is handed to the `local-llm` WSL distro
    unless Docker already works.
    """
    if requested == "docker":
        return "docker", False
    if requested == "podman":
        reexec = (not podman_ok) and on_windows and _connection_failure(podman_detail)
        return "podman", reexec
    if podman_ok:
        return "podman", False
    if docker_ok:
        return "docker", False
    return "podman", on_windows and _connection_failure(podman_detail)


def _engine_probe(engine: str) -> tuple[bool, str]:
    if shutil.which(engine) is None:
        return False, f"{engine} not found on PATH"
    probe = subprocess.run(
        [engine, "compose", "version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    detail = _command_output(probe) or f"exit {probe.returncode}"
    return probe.returncode == 0, detail


def _wsl_reexec_command(argv: list[str], *, distro: str, repo: str) -> list[str]:
    quoted = " ".join(shlex.quote(arg) for arg in argv)
    inner = f"cd {shlex.quote(repo)} && exec python3 scripts/deploy.py {quoted}"
    return ["wsl", "-d", distro, "-e", "bash", "-lc", inner]


def _activate_runtime(argv: list[str]) -> None:
    """Pick podman or docker, or continue the same command inside WSL."""
    global _RUNTIME
    requested = _requested_runtime(_parse_env_file(ENV_FILE))
    podman_ok, podman_detail = _engine_probe("podman")
    docker_ok = False
    if requested in ("auto", "docker"):
        docker_ok, _docker_detail = _engine_probe("docker")
    engine, reexec = choose_runtime(
        requested,
        podman_ok=podman_ok,
        docker_ok=docker_ok,
        podman_detail=podman_detail,
        on_windows=os.name == "nt",
    )
    if reexec and os.environ.get("DEPLOY_SKIP_WSL") != "1":
        distro = os.environ.get("DEPLOY_WSL_DISTRO", WSL_DISTRO_DEFAULT)
        command = _wsl_reexec_command(
            argv,
            distro=distro,
            repo=_normalize_codebase_path(str(REPO_ROOT)),
        )
        print(
            f"[deploy] Windows Podman has no machine; continuing in WSL distro {distro}.",
            flush=True,
        )
        raise SystemExit(subprocess.call(command))
    _RUNTIME = engine
    print(f"[deploy] container runtime: {_RUNTIME}", flush=True)


def _require_podman() -> None:
    engine = _RUNTIME
    if shutil.which(engine) is None:
        sys.exit(
            f"[deploy] FATAL: `{engine}` not found on PATH. Install it first - "
            "see docs/getting-started/prerequisites.md."
        )
    probe = subprocess.run(
        [engine, "compose", "version"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if probe.returncode != 0:
        detail = _command_output(probe) or f"exit {probe.returncode}"
        hint = _compose_failure_hint(engine, detail)
        sys.exit(f"[deploy] FATAL: `{engine} compose version` failed.\n{detail}\n{hint}")


def _ensure_env_file() -> None:
    if not ENV_FILE.exists():
        shutil.copy(ENV_EXAMPLE, ENV_FILE)
        sys.exit(
            f"[deploy] Created {ENV_FILE} from .env.example. Edit it and set a real "
            "AGENT_BEARER_TOKEN (not the placeholder), then re-run this command."
        )
    content = ENV_FILE.read_text(encoding="utf-8")
    if PLACEHOLDER_TOKEN in content:
        sys.exit(
            f"[deploy] FATAL: {ENV_FILE} still has the placeholder AGENT_BEARER_TOKEN. "
            "Set a real random secret before deploying - e.g.:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )


GPU_PROFILES = ("16g", "24g", "24g-30b", "48g", "48g-30b", "48g-32b", "evo-x5")
GPU_PROFILE_DIR = REPO_ROOT / "config" / "profiles"
# Profiles whose chat GGUF is not part of `deploy.py up`. Key is fetch_models.py --extra.
PROFILE_EXTRA = {
    "24g-30b": "coder-30b-q5",
    "48g-30b": "coder-30b-q8",
    "48g-32b": "coder-32b-q5",
    "evo-x5": "mistral-small-4",
}


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


def _parse_env_file(path: Path) -> dict[str, str]:
    """Trivial `KEY=VALUE` parser for `.env`-style files - skips blank lines
    and `#` comments. Not a full dotenv implementation (no quoting, no
    variable expansion) because compose.yaml itself doesn't need one; this
    only has to read back values `podman compose` would substitute."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


_WIN_DRIVE_PATH = re.compile(r"^([A-Za-z]):[/\\](.*)$")


def _host_os() -> str:
    return os.name


def _normalize_codebase_path(raw: str) -> str:
    """Path the running engine can bind.

    A Windows drive becomes `/mnt/<letter>/...` for Podman on Windows and
    inside WSL. Docker Desktop on Windows keeps the drive path. Native Linux
    keeps a path that already exists and does not invent `/mnt/<letter>`.
    """
    import workspaces as registry

    raw = raw.strip()
    if not raw:
        return "./sample"
    return registry.engine_bind_path(raw, runtime=_RUNTIME, host_os=_host_os())


def _codebase_path_candidates(raw: str) -> list[Path]:
    """Host paths to probe when checking that CODEBASE_PATH exists."""
    import workspaces as registry

    raw = raw.strip() or "./sample"
    candidates: list[Path] = []

    if raw.startswith("./") or raw.startswith("../"):
        candidates.append((REPO_ROOT / raw).resolve())
        return candidates

    seen: set[str] = set()
    for spelling in registry.path_spellings(raw):
        if spelling not in seen:
            seen.add(spelling)
            candidates.append(Path(spelling))
        match = _WIN_DRIVE_PATH.match(spelling)
        if match:
            drive, rest = match.group(1).upper(), match.group(2).replace("/", "\\")
            windows = f"{drive}:\\{rest}"
            if windows not in seen:
                seen.add(windows)
                candidates.append(Path(windows))

    return candidates


def _require_bind_source() -> None:
    """Stop before compose when the checkout is missing.

    The engine creates a missing bind source. That mkdir fails when the
    parent is not writable, and it must not create a mount that was never there.
    """
    import workspaces as registry

    raw = _parse_env_file(ENV_FILE).get("CODEBASE_PATH", "./sample")
    if _codebase_path_exists(raw):
        chosen = _normalize_codebase_path(raw)
        given = raw.strip().replace("\\", "/")
        if chosen != given:
            print(f"[deploy] checkout: {chosen}", flush=True)
        return
    tried = "\n".join(f"  {item}" for item in registry.path_spellings(raw))
    sys.exit(
        "[deploy] FATAL: checkout directory not found.\n"
        f"Tried:\n{tried}\n"
        "Set CODEBASE_PATH to a directory that exists on this machine. "
        "A Windows drive letter is mounted as /mnt/<letter>/... under WSL. "
        "Native Linux uses the path as given. "
        "A missing directory is not created."
    )


def _codebase_path_exists(raw: str) -> bool:
    return any(path.is_dir() for path in _codebase_path_candidates(raw))


def _codebase_root_from_env(env: dict[str, str]) -> Path:
    raw = env.get("CODEBASE_PATH", "./sample")
    if raw.startswith("./") or raw.startswith("../"):
        return (REPO_ROOT / raw).resolve()
    for candidate in _codebase_path_candidates(raw):
        if candidate.is_dir():
            return candidate
    return Path(_normalize_codebase_path(raw))


def _write_env_key(key: str, value: str) -> None:
    """Set or replace one KEY=VALUE line in `.env` without clobbering others."""
    lines: list[str] = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    replaced = False
    out: list[str] = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def _compose_env() -> dict[str, str]:
    """Process env for `podman compose`, with CODEBASE_PATH normalized for WSL."""
    env = os.environ.copy()
    file_env = _parse_env_file(ENV_FILE)
    raw = file_env.get("CODEBASE_PATH", "./sample")
    env["CODEBASE_PATH"] = _normalize_codebase_path(raw)
    if _USE_CPU_COMPOSE_OVERRIDE:
        env.update(_CPU_FALLBACK_ENV)
    return env


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
    return result.returncode == 0 and CDI_GPU_DEVICE in result.stdout


def _cdi_probe() -> bool:
    """Return True when Podman can attach the NVIDIA CDI device."""
    if _cdi_list_contains_gpu():
        return True
    result = subprocess.run(
        [
            "podman",
            "run",
            "--rm",
            "--device",
            CDI_GPU_DEVICE,
            "ubuntu:24.04",
            "true",
        ],
        cwd=REPO_ROOT,
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
        f"--output={CDI_NVIDIA_YAML}",
    ]
    print(f"[deploy] trying: sudo {' '.join(generate_cmd)}", flush=True)
    result = subprocess.run(["sudo", *generate_cmd], capture_output=True, text=True)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "unknown error").strip()
        print(f"[deploy] CDI generate failed: {err}", file=sys.stderr)
        return False

    print(f"[deploy] wrote {CDI_NVIDIA_YAML}", flush=True)
    return _cdi_probe()


def _enable_cpu_compose_fallback(*, persist_env: bool = False) -> None:
    global _USE_CPU_COMPOSE_OVERRIDE
    _USE_CPU_COMPOSE_OVERRIDE = True
    if persist_env:
        for key, value in _CPU_FALLBACK_ENV.items():
            _write_env_key(key, value)
    print(
        "[deploy] CPU fallback active (compose.cpu.yaml) - GPU offload disabled.",
        flush=True,
    )


def _docker_gpu_probe() -> bool:
    """Return True when `docker run --gpus all` can start a container."""
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "run", "--rm", "--gpus", "all", "ubuntu:24.04", "true"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    return result.returncode == 0


def _prepare_docker_gpu(env: dict[str, str]) -> None:
    if not _env_gpu_build_requested(env):
        print("[deploy] GPU disabled in .env - using CPU compose overlay.")
        _enable_cpu_compose_fallback(persist_env=False)
        return
    if _docker_gpu_probe():
        print("[deploy] Docker NVIDIA GPU available.")
        return
    print(
        "[deploy] WARNING: Docker cannot use the GPU - falling back to CPU-only "
        "(slower). See docs/guides/troubleshooting-gpu.md.",
        file=sys.stderr,
    )
    # Do not rewrite .env: a failed probe must not clear LLM_GPU_ARG for the next boot.
    _enable_cpu_compose_fallback(persist_env=False)


def _prepare_gpu_for_up(env: dict[str, str]) -> None:
    """Ensure the selected engine can see an NVIDIA GPU before compose starts."""
    if _RUNTIME == "docker":
        _prepare_docker_gpu(env)
        return
    if not _env_gpu_build_requested(env):
        print("[deploy] GPU disabled in .env - using CPU compose overlay.")
        _enable_cpu_compose_fallback(persist_env=False)
        return

    if _cdi_probe():
        print("[deploy] NVIDIA CDI device available.")
        return

    print(
        "[deploy] WARNING: NVIDIA CDI device not available - attempting to generate it...",
        file=sys.stderr,
    )
    if _try_generate_cdi():
        print("[deploy] CDI generated successfully.")
        return

    print(
        "[deploy] WARNING: could not configure GPU/CDI - falling back to CPU-only "
        "(slower). See docs/guides/troubleshooting-gpu.md.",
        file=sys.stderr,
    )
    # Do not rewrite .env: a failed probe must not clear LLM_GPU_ARG for the next boot.
    _enable_cpu_compose_fallback(persist_env=False)


def _compose_build_and_up() -> None:
    _run(["podman", "compose", "build"])
    try:
        _run(["podman", "compose", "up", "-d", *UP_SERVICES])
    except subprocess.CalledProcessError:
        if _USE_CPU_COMPOSE_OVERRIDE or _cdi_probe():
            raise
        print(
            "[deploy] compose up failed and CDI is still unavailable - "
            "switching to CPU fallback.",
            file=sys.stderr,
        )
        # Do not rewrite .env: a failed probe must not clear LLM_GPU_ARG for the next boot.
        _enable_cpu_compose_fallback(persist_env=False)
        _run(["podman", "compose", "build", "llm", "embedder"])
        _run(["podman", "compose", "up", "-d", *UP_SERVICES])


def _parse_compose_ps_health(output: str, services: list[str]) -> dict[str, str]:
    """Best-effort text parsing of `podman compose ps` output shared by
    `_wait_healthy`'s polling loop and `doctor`'s one-shot check.
    Container-naming conventions differ slightly across podman-compose/
    native `podman compose` versions, but both follow
    `{project}-{service}-{index}` (or the older `{project}_{service}_{index}`)
    - this matches the service name anchored right before its trailing
    `-{index}`/`_{index}` suffix, rather than parsing a specific table/JSON
    schema. A plain substring check, or even a `-service-`-delimited one,
    would be wrong here: this project's own directory name is "local_llm",
    so every container name contains "_llm-" as part of the *project*
    prefix (e.g. "local_llm-qdrant-1") - only requiring digits right after
    the match excludes that false hit and matches solely the real `llm`
    service's own "-llm-1" suffix.

    Returns one of "healthy", "unhealthy", "starting", "unknown" (container
    is up but no recognized health keyword - e.g. a healthcheck that hasn't
    reported yet) or "not_found" (no line mentions this service at all, i.e.
    it isn't running) per service.
    """
    statuses: dict[str, str] = {service: "not_found" for service in services}
    for line in output.splitlines():
        lowered = line.lower()
        for service in services:
            if not re.search(rf"[_-]{re.escape(service)}[_-]\d+", line):
                continue
            if "unhealthy" in lowered:
                statuses[service] = "unhealthy"
            elif "healthy" in lowered:
                statuses[service] = "healthy"
            elif "starting" in lowered:
                statuses[service] = "starting"
            else:
                statuses[service] = "unknown"
    return statuses


def _wait_healthy(services: list[str], timeout: int) -> None:
    """Polls `podman compose ps` until every service is reported healthy or
    the timeout is hit. Falls through to printing the raw `podman compose
    ps` output on timeout, so you can always judge for yourself.
    """
    print(f"[deploy] waiting up to {timeout}s for {', '.join(services)} to become healthy...")
    deadline = time.monotonic() + timeout
    pending = set(services)
    while time.monotonic() < deadline and pending:
        result = subprocess.run(
            _compose_cmd("ps"),
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=_compose_env(),
        )
        statuses = _parse_compose_ps_health(result.stdout, list(pending))
        for service, status in statuses.items():
            if status == "healthy":
                pending.discard(service)
                print(f"[deploy]   {service}: healthy")
        if pending:
            time.sleep(5)

    if pending:
        print(
            f"[deploy] WARNING: timed out waiting for: {', '.join(sorted(pending))}. "
            "Current status:",
            file=sys.stderr,
        )
        _run(["podman", "compose", "ps"], check=False)
        sys.exit(
            "[deploy] Not all services became healthy in time. Check "
            "`podman compose logs <service>` and docs/guides/troubleshooting-gpu.md."
        )
    print("[deploy] all core services healthy.")


def cmd_up(args: argparse.Namespace) -> int:
    _require_podman()
    _ensure_env_file()
    _require_bind_source()
    _prepare_gpu_for_up(_parse_env_file(ENV_FILE))

    if not args.skip_models:
        _run(["podman", "compose", "--profile", "setup", "run", "--rm", "fetch-models"])

    _compose_build_and_up()
    _wait_healthy(CORE_SERVICES, args.timeout)

    if not args.skip_index:
        _run(["podman", "compose", "--profile", "index", "run", "--rm", "indexer"])

    print(
        '[deploy] Done. Try: python scripts/deploy.py ask "How does the indexer chunk C++ files?"'
    )
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    _require_podman()
    cmd = ["podman", "compose", "down"]
    if args.volumes:
        cmd.append("-v")
    _run(cmd)
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    _require_podman()
    _run(["podman", "compose", "ps"])
    return 0


# Services recreated whenever the mounts of `tools`/`agent` change. `ui-proxy`
# is included because nginx resolves `agent` once at startup: a recreated
# `agent` container can come back with a new address and the proxy would
# keep answering 502 until it is restarted too.
_MOUNT_DEPENDENT_SERVICES = ["tools", "agent", "ui-proxy"]


def _recreate_mount_dependents() -> None:
    _run(["podman", "compose", "up", "-d", "--force-recreate", "--no-deps", *_MOUNT_DEPENDENT_SERVICES])


def cmd_index(_args: argparse.Namespace) -> int:
    _require_podman()
    _require_bind_source()
    _recreate_mount_dependents()
    _run(["podman", "compose", "--profile", "index", "run", "--rm", "indexer"])
    return 0


def _existing_codebase(raw: str) -> Path | None:
    for candidate in _codebase_path_candidates(raw):
        if candidate.is_dir():
            return candidate.resolve()
    return None


def _run_repo_index(checkout: Path, collection: str) -> None:
    """Index one checkout into its repository collection. Does not touch .env."""
    import workspaces as registry

    env = _compose_env()
    env["CODEBASE_PATH"] = registry.engine_bind_path(
        checkout.as_posix(), runtime=_RUNTIME, host_os=os.name
    )
    env["QDRANT_COLLECTION"] = collection
    env["INDEXER_SOURCE_ROOT"] = "/workspace/app"
    _run(
        ["podman", "compose", "--profile", "index", "run", "--rm", "--no-deps", "indexer"],
        env=env,
    )


def cmd_eval(args: argparse.Namespace) -> int:
    """Score lookup on a registered checkout. Does not call the chat model."""
    import eval_checkout

    argv = [args.path, "--questions", args.questions, "--timeout", str(args.timeout)]
    if args.agent:
        argv.extend(["--agent", args.agent])
    if args.out:
        argv.extend(["--out", args.out])
    if args.require_clean:
        argv.append("--require-clean")
    if args.allow_unfinished:
        argv.append("--allow-unfinished")
    return eval_checkout.main(argv)


def cmd_workspace(args: argparse.Namespace) -> int:
    """Register a checkout by its remote URL and mount it next to the others.

    The first checkout of a remote is indexed. Another ticket of the same
    remote only records the path. `--update` runs the incremental indexer
    for this tree into the shared collection.
    """
    import workspaces as registry

    found = _existing_codebase(args.path)
    if found is None:
        tried = "\n".join(f"  {item}" for item in registry.path_spellings(args.path))
        sys.exit(
            "[deploy] FATAL: workspace directory not found.\n"
            f"Tried:\n{tried}\n"
            "Pass a directory that exists on this machine. "
            "A Windows drive letter is mounted as /mnt/<letter>/... under WSL. "
            "Native Linux uses the path as given."
        )
    recorded = registry.register_checkout(found)
    registry.install_vscode_mcp(recorded.checkout_path, recorded.checkout_path.as_posix())
    print(
        f"[deploy] {recorded.checkout_path.as_posix()} -> {recorded.collection} ({recorded.vcs})",
        flush=True,
    )
    changed = registry.write_compose_mounts(
        registry.load_registry(),
        host_path_for=lambda path: registry.engine_bind_path(
            path, runtime=_RUNTIME, host_os=os.name
        ),
    )
    _require_podman()
    if changed:
        _recreate_mount_dependents()
    if recorded.needs_index or args.update:
        with registry.IndexLock():
            _run_repo_index(recorded.checkout_path, recorded.collection)
        registry.mark_indexed(recorded.repo_id)
    else:
        print("[deploy] repository already indexed; local edits stay out of the index", flush=True)
    return 0


def _resolve_prompt_host_path(raw: str) -> Path:
    """Absolute host path for a prompt file, WSL-safe when given a Windows drive path."""
    raw = raw.strip()
    if not raw:
        sys.exit("[deploy] FATAL: empty --file path.")
    # Prefer an existing path as given (WSL `/mnt/...` or relative to repo).
    candidate = Path(raw).expanduser()
    if not candidate.is_file():
        # Windows drive form when deploy runs under WSL with a D:/... path.
        normalized = _normalize_codebase_path(raw)
        candidate = Path(normalized).expanduser()
    if not candidate.is_file():
        # Relative to repo root.
        candidate = (REPO_ROOT / raw).resolve()
    if not candidate.is_file():
        sys.exit(f"[deploy] FATAL: prompt file not found: {raw}")
    return candidate.resolve()


def cmd_chat(_args: argparse.Namespace) -> int:
    """Interactive multi-turn REPL via the app container (`--repl`)."""
    _require_podman()
    _run(["podman", "compose", "run", "--rm", "-it", "app", "--repl"])
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    _require_podman()
    if getattr(args, "file", None):
        host_path = _resolve_prompt_host_path(args.file)
        # Mount the file so large prompts never hit ARG_MAX via argv.
        _run(
            [
                "podman",
                "compose",
                "run",
                "--rm",
                "-v",
                f"{host_path}:/prompt.txt:ro",
                "app",
                "--file",
                "/prompt.txt",
            ]
        )
        return 0

    question = getattr(args, "question", None)
    if not question:
        sys.exit(
            "[deploy] FATAL: pass a question string, or --file <path>, or '-' to read stdin."
        )

    if question == "-":
        prompt = sys.stdin.read()
        if not prompt.strip():
            sys.exit("[deploy] FATAL: stdin prompt is empty.")
        # Pipe via a temp mount-free path: write under REPO_ROOT/.prompt.tmp
        # then mount (same ARG_MAX avoidance as --file).
        tmp = REPO_ROOT / ".prompt.tmp"
        try:
            tmp.write_text(prompt, encoding="utf-8")
            _run(
                [
                    "podman",
                    "compose",
                    "run",
                    "--rm",
                    "-v",
                    f"{tmp.resolve()}:/prompt.txt:ro",
                    "app",
                    "--file",
                    "/prompt.txt",
                ]
            )
        finally:
            tmp.unlink(missing_ok=True)
        return 0

    _run(["podman", "compose", "run", "--rm", "app", question])
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    _require_podman()
    cmd = ["podman", "compose", "logs"]
    if args.follow:
        cmd.append("-f")
    cmd.append(args.service)
    _run(cmd)
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    _require_podman()
    _ensure_env_file()
    env = _parse_env_file(ENV_FILE)

    if TRAIN_LOCK_FILE.exists() and not args.force:
        sys.exit(
            f"[deploy] FATAL: {TRAIN_LOCK_FILE} exists (training may be in progress). "
            "Remove it manually or pass --force."
        )

    if not HF_TRAIN_BASE_MARKER.exists() and not args.dry_run:
        sys.exit(
            "[deploy] FATAL: HF train base missing. Run first:\n"
            "  podman compose --profile setup run --rm fetch-models "
            "python scripts/container/fetch_models.py --hf-train-base"
        )

    TRAIN_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRAIN_LOCK_FILE.write_text("running\n", encoding="utf-8")
    try:
        dataset_cmd = [
            "podman",
            "compose",
            "--profile",
            "dev",
            "run",
            "--rm",
            "devtools",
            "python",
            "services/trainer/build_dataset.py",
        ]
        if args.incremental:
            dataset_cmd.append("--incremental")
        _run(dataset_cmd)

        if args.dry_run:
            print("[deploy] dry-run: dataset built, skipping GPU training.")
            return 0

        _run(["podman", "compose", "--profile", "train", "build", "trainer"])
        _run(["podman", "compose", "--profile", "train", "run", "--rm", "trainer"])

        lora_host = MODELS_DIR / "adapters" / "latest" / "adapter.gguf"
        if not lora_host.exists():
            _run(
                [
                    "podman",
                    "compose",
                    "--profile",
                    "train",
                    "run",
                    "--rm",
                    "trainer",
                    "python",
                    "/workspace/scripts/convert_lora.py",
                ]
            )

        if lora_host.exists() and not env.get("LLM_LORA_PATH"):
            _write_env_key("LLM_LORA_PATH", DEFAULT_LORA_GGUF)
            print(f"[deploy] set LLM_LORA_PATH={DEFAULT_LORA_GGUF} in .env")

        _run(["podman", "compose", "up", "-d", "--force-recreate", "--no-deps", "llm"])

        if not args.skip_index:
            cmd_index(argparse.Namespace())
    finally:
        TRAIN_LOCK_FILE.unlink(missing_ok=True)

    print("[deploy] train complete. LoRA loaded on llm; RAG re-indexed unless --skip-index.")
    return 0


def cmd_test(_args: argparse.Namespace) -> int:
    """Runs the tiny-CPU-model end-to-end smoke test - see
    docs/guides/testing.md's "End-to-end smoke test" section, which this
    mirrors exactly."""
    _require_podman()
    env_file = ["--env-file", ".env.test"]
    _run(
        ["podman", "compose", *env_file, "--profile", "setup", "run", "--rm", "fetch-models",
         "python", "scripts/container/fetch_models.py", "--test"]
    )
    _run(["podman", "compose", *env_file, "build", "llm", "embedder", "tools", "agent", "mcp"])
    _run(["podman", "compose", *env_file, "up", "-d", *SMOKE_SERVICES])
    _wait_healthy(SMOKE_SERVICES, timeout=180)
    try:
        _run(["podman", "compose", *env_file, "--profile", "test", "run", "--rm", "smoke-test"])
    finally:
        _run(["podman", "compose", *env_file, "down"], check=False)
    return 0


# ------------------------------------------------------------------ doctor


def _doctor_check_podman() -> list[Check]:
    engine = _RUNTIME
    checks: list[Check] = []
    if shutil.which(engine) is None:
        checks.append(
            Check(
                engine,
                "fail",
                "not found on PATH",
                hint="Install it first - see docs/getting-started/prerequisites.md.",
            )
        )
        return checks

    version = subprocess.run([engine, "--version"], capture_output=True, text=True)
    checks.append(Check(engine, "ok", version.stdout.strip() or "installed, version unknown"))

    compose = subprocess.run([engine, "compose", "version"], capture_output=True, text=True)
    if compose.returncode == 0:
        detail = compose.stdout.strip().splitlines()[0] if compose.stdout.strip() else "available"
        checks.append(Check(f"{engine} compose", "ok", detail))
    else:
        detail = _command_output(compose) or f"`{engine} compose version` failed"
        checks.append(
            Check(
                f"{engine} compose",
                "fail",
                detail,
                hint=_compose_failure_hint(engine, detail),
            )
        )
    return checks


def _doctor_check_env() -> list[Check]:
    if not ENV_FILE.exists():
        return [
            Check(
                ".env",
                "fail",
                f"missing at {ENV_FILE}",
                hint="Copy .env.example to .env and set a real AGENT_BEARER_TOKEN.",
            )
        ]

    checks = [Check(".env", "ok", f"present at {ENV_FILE}")]
    env = _parse_env_file(ENV_FILE)
    token = env.get("AGENT_BEARER_TOKEN", "")
    if not token:
        checks.append(
            Check(
                "AGENT_BEARER_TOKEN",
                "fail",
                "not set in .env",
                hint='Set a real random secret, e.g. python -c "import secrets; print(secrets.token_urlsafe(32))".',
            )
        )
    elif token == PLACEHOLDER_TOKEN:
        checks.append(
            Check(
                "AGENT_BEARER_TOKEN",
                "fail",
                "still the placeholder value",
                hint='Set a real random secret, e.g. python -c "import secrets; print(secrets.token_urlsafe(32))".',
            )
        )
    else:
        checks.append(Check("AGENT_BEARER_TOKEN", "ok", "set to a non-placeholder value"))
    return checks


def _doctor_check_codebase_path(env: dict[str, str]) -> Check:
    raw = env.get("CODEBASE_PATH", "./sample")
    normalized = _normalize_codebase_path(raw)
    if _codebase_path_exists(raw):
        detail = f"{raw} -> {normalized} (directory exists)"
        if raw != normalized:
            detail = f"{raw} (compose uses {normalized})"
        return Check("CODEBASE_PATH", "ok", detail)

    import workspaces as registry

    spellings = registry.path_spellings(raw)
    detail = f"directory not found for {raw!r} (compose would use {normalized})"
    if len(spellings) > 1:
        detail = f"directory not found. Tried {' and '.join(spellings)}"
    return Check(
        "CODEBASE_PATH",
        "fail",
        detail,
        hint=(
            "Set CODEBASE_PATH to a directory that exists on this machine. "
            "A Windows drive letter is mounted as /mnt/<letter>/... under WSL. "
            "Native Linux uses the path as given."
        ),
    )


def _doctor_check_models(env: dict[str, str], *, verify_checksums: bool) -> list[Check]:
    known_specs = {}
    # Local import, only for --verify-checksums: fetch_models pulls in
    # huggingface_hub, which the host running deploy.py need not have.
    fetch_models: Any = None
    if verify_checksums:
        try:
            import fetch_models as _fetch_models

            fetch_models = _fetch_models
        except ImportError:
            pass
        if fetch_models is not None:
            known_specs = {
                spec.filename: spec
                for spec in (
                    fetch_models.CHAT_MODEL,
                    fetch_models.EMBED_MODEL,
                    fetch_models.TEST_MODEL,
                    fetch_models.RERANK_MODEL,
                )
            }

    checks: list[Check] = []
    for env_var in ("LLM_MODEL_PATH", "EMBEDDER_MODEL_PATH", "RERANKER_MODEL_PATH"):
        raw = env.get(env_var, "")
        if not raw:
            checks.append(Check(env_var, "skip", "not set in .env"))
            continue

        filename = Path(raw).name
        path = MODELS_DIR / filename
        if not path.exists():
            checks.append(
                Check(
                    filename,
                    "fail",
                    f"missing at {path}",
                    hint="Run `python scripts/deploy.py up` or `podman compose --profile setup run --rm fetch-models`.",
                )
            )
            continue

        size_mib = path.stat().st_size / (1024 * 1024)
        detail = f"{size_mib:,.1f} MiB at {path}"
        if not verify_checksums:
            checks.append(Check(filename, "ok", detail))
            continue

        spec = known_specs.get(filename)
        if spec is None:
            checks.append(Check(filename, "skip", f"{detail} (not a pinned filename, skipping checksum)"))
        elif spec.sha256 is None:
            checks.append(Check(filename, "skip", f"{detail} (no pinned checksum for this model)"))
        else:
            actual = fetch_models._sha256_of(path)
            if actual == spec.sha256:
                checks.append(Check(filename, "ok", f"{detail}, checksum verified"))
            else:
                checks.append(
                    Check(
                        filename,
                        "fail",
                        f"{detail}, CHECKSUM MISMATCH (expected {spec.sha256}, got {actual})",
                        hint="Re-download it - see scripts/container/fetch_models.py --force.",
                    )
                )
    return checks


def _doctor_check_hf_train_base() -> Check:
    if HF_TRAIN_BASE_MARKER.exists():
        return Check("HF train base", "ok", f"present at {HF_TRAIN_BASE_MARKER.parent}")
    return Check(
        "HF train base",
        "warn",
        f"missing ({HF_TRAIN_BASE_MARKER.parent})",
        hint=(
            "podman compose --profile setup run --rm fetch-models "
            "python scripts/container/fetch_models.py --hf-train-base"
        ),
    )


def _doctor_check_lora_adapter(env: dict[str, str]) -> Check:
    raw = env.get("LLM_LORA_PATH", "").strip()
    if not raw:
        return Check("LLM_LORA_PATH", "skip", "not set (no LoRA adapter loaded)")
    if raw.startswith("/models/"):
        host_path = MODELS_DIR / raw.removeprefix("/models/").lstrip("/")
    else:
        host_path = Path(raw)
    if host_path.is_file():
        size_mib = host_path.stat().st_size / (1024 * 1024)
        return Check("LLM_LORA_PATH", "ok", f"{size_mib:,.1f} MiB at {host_path}")
    return Check(
        "LLM_LORA_PATH",
        "fail",
        f"configured but missing on host: {host_path}",
        hint="python scripts/deploy.py train",
    )


def _doctor_check_train_commit(env: dict[str, str]) -> Check:
    manifest_path = MODELS_DIR / "adapters" / "latest" / "manifest.json"
    if not manifest_path.exists():
        return Check("train commit", "skip", "no adapter manifest (run deploy.py train)")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return Check("train commit", "warn", "could not read adapter manifest")

    trained = manifest.get("commit_sha", "")
    codebase = _codebase_root_from_env(env)
    try:
        result = subprocess.run(
            ["git", "-C", str(codebase), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return Check("train commit", "skip", "could not run git in CODEBASE_PATH")

    if result.returncode != 0:
        return Check("train commit", "skip", "CODEBASE_PATH is not a git repo")

    head = result.stdout.strip()
    if trained == head:
        return Check("train commit", "ok", f"adapter trained at {head[:12]}")
    return Check(
        "train commit",
        "warn",
        f"adapter at {trained[:12] if trained else '?'}, HEAD is {head[:12]}",
        hint="python scripts/deploy.py train --incremental",
    )


def _doctor_check_disk_space() -> Check:
    usage = shutil.disk_usage(REPO_ROOT)
    free_gib = usage.free / (1024**3)
    if free_gib < DISK_BUDGET_GIB:
        return Check(
            "disk space",
            "warn",
            f"{free_gib:.1f} GiB free on the filesystem holding {REPO_ROOT}",
            hint=f"docs/getting-started/prerequisites.md budgets ~{DISK_BUDGET_GIB} GiB for models + image layers.",
        )
    return Check("disk space", "ok", f"{free_gib:.1f} GiB free on the filesystem holding {REPO_ROOT}")


def _doctor_check_services() -> list[Check]:
    try:
        result = subprocess.run(
            ["podman", "compose", "ps"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=_compose_env(),
        )
    except OSError as exc:
        return [
            Check(
                "services",
                "fail",
                f"could not run `podman compose ps`: {exc}",
                hint="Install Podman - see docs/getting-started/prerequisites.md.",
            )
        ]
    if result.returncode != 0:
        return [
            Check(
                "services",
                "fail",
                "`podman compose ps` failed",
                hint=(result.stderr or "").strip()[:300] or None,
            )
        ]

    statuses = _parse_compose_ps_health(result.stdout, CORE_SERVICES)
    checks: list[Check] = []
    for service in CORE_SERVICES:
        status = statuses[service]
        if status == "healthy":
            checks.append(Check(service, "ok", "healthy"))
        elif status == "starting":
            checks.append(Check(service, "warn", "starting", hint="Give it more time, then re-run doctor."))
        elif status == "unhealthy":
            checks.append(
                Check(service, "fail", "unhealthy", hint=f"podman compose logs {service}")
            )
        elif status == "not_found":
            checks.append(
                Check(
                    service,
                    "warn",
                    "not running",
                    hint="python scripts/deploy.py up (or `podman compose up -d " + service + "`).",
                )
            )
        else:
            checks.append(Check(service, "warn", "running, health status unknown"))
    return checks


def _container_env_var(service: str, var: str) -> str | None:
    """Read one env var from a running compose service, or None if unavailable."""
    try:
        result = subprocess.run(
            ["podman", "compose", "exec", "-T", service, "printenv", var],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=_compose_env(),
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value if value else None


def _env_gpu_arg(env: dict[str, str], key: str, default: str = "1") -> str:
    raw = env.get(key, default).strip()
    return raw if raw else default


def _doctor_check_gpu_offload(env: dict[str, str]) -> list[Check]:
    """Warn when .env requests GPU builds but running containers were built CPU-only."""
    checks: list[Check] = []
    specs = (
        ("llm", "LLM_GPU_ARG", "LLM_GPU_BUILD", "LLM_NGL"),
        ("embedder", "EMBEDDER_GPU_ARG", "EMBEDDER_GPU_BUILD", "EMBEDDER_NGL"),
    )
    for service, arg_key, build_key, ngl_key in specs:
        want_gpu = _env_gpu_arg(env, arg_key) != "0"
        if not want_gpu:
            checks.append(Check(f"{service} GPU build", "skip", f"{arg_key}=0 in .env (CPU intended)"))
            continue

        build_val = _container_env_var(service, build_key)
        if build_val is None:
            checks.append(
                Check(
                    f"{service} GPU build",
                    "skip",
                    f"{service} not running (cannot read {build_key})",
                )
            )
            continue

        ngl_val = _container_env_var(service, ngl_key)
        if build_val == "0":
            checks.append(
                Check(
                    f"{service} GPU build",
                    "warn",
                    f"{build_key}=0 in container but {arg_key}=1 in .env",
                    hint=f"podman compose build {service} && podman compose up -d --force-recreate {service}",
                )
            )
        elif ngl_val == "0":
            checks.append(
                Check(
                    f"{service} GPU offload",
                    "warn",
                    f"{ngl_key}=0 (all layers on CPU)",
                    hint=f"Set {ngl_key}=999 in .env and recreate {service}.",
                )
            )
        else:
            detail = f"{build_key}={build_val}"
            if ngl_val:
                detail += f", {ngl_key}={ngl_val}"
            checks.append(Check(f"{service} GPU offload", "ok", detail))
    return checks


def _gpu_probe_argv() -> list[str]:
    if _RUNTIME == "docker":
        return ["docker", "run", "--rm", "--gpus", "all", "ubuntu:24.04", "nvidia-smi"]
    return ["podman", "run", "--rm", "--device", "nvidia.com/gpu=all", "ubuntu:24.04", "nvidia-smi"]


def _doctor_check_gpu() -> Check:
    argv = _gpu_probe_argv()
    try:
        result = subprocess.run(argv, capture_output=True, text=True)
    except OSError as exc:
        return Check(
            "gpu",
            "fail",
            f"could not run {argv[0]}: {exc}",
            hint="See docs/getting-started/prerequisites.md.",
        )
    if result.returncode == 0:
        first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else "ok"
        return Check("gpu", "ok", first_line)
    shown = " ".join(argv)
    return Check(
        "gpu",
        "fail",
        f"GPU probe failed (`{shown}`)",
        hint="See docs/guides/troubleshooting-gpu.md - or ignore this if you intend to run CPU-only.",
    )


def _render_terminal(checks: list[Check]) -> None:
    markers = {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]", "skip": "[SKIP]"}
    for check in checks:
        marker = markers[check.status]
        print(f"{marker} {check.name}: {check.detail}")
        if check.status != "ok" and check.hint:
            print(f"       hint: {check.hint}")

    counts = {status: sum(1 for c in checks if c.status == status) for status in ("ok", "warn", "fail", "skip")}
    print(
        f"\n[deploy] doctor summary: {counts['ok']} ok, {counts['warn']} warn, "
        f"{counts['fail']} fail, {counts['skip']} skip"
    )


def _render_html(checks: list[Check]) -> str:
    """Single self-contained HTML string (inline CSS, no JS, no external
    assets) - a point-in-time snapshot, not a live dashboard. Re-run
    `doctor --html` to refresh it."""
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    colors = {"ok": "#1a7f37", "warn": "#9a6700", "fail": "#cf222e", "skip": "#57606a"}
    rows = []
    for check in checks:
        hint_html = (
            f'<div class="hint">{html.escape(check.hint)}</div>' if check.hint and check.status != "ok" else ""
        )
        rows.append(
            f'<tr class="status-{check.status}">'
            f'<td><span class="badge">{check.status.upper()}</span></td>'
            f"<td>{html.escape(check.name)}</td>"
            f"<td>{html.escape(check.detail)}{hint_html}</td>"
            f"</tr>"
        )

    counts = {status: sum(1 for c in checks if c.status == status) for status in ("ok", "warn", "fail", "skip")}
    badge_css = "\n".join(f".status-{status} .badge {{ background: {color}; }}" for status, color in colors.items())

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>local_llm doctor report</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; margin: 2rem; color: #1f2328; background: #fff; }}
  h1 {{ font-size: 1.4rem; }}
  .meta {{ color: #57606a; margin-bottom: 1.5rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #d0d7de; vertical-align: top; }}
  th {{ background: #f6f8fa; }}
  .badge {{ display: inline-block; color: #fff; border-radius: 4px; padding: 0.15rem 0.5rem;
           font-size: 0.8rem; font-weight: 600; }}
  .hint {{ color: #57606a; font-size: 0.85rem; margin-top: 0.25rem; }}
  {badge_css}
</style>
</head>
<body>
<h1>local_llm doctor report</h1>
<p class="meta">Generated {generated_at}. This is a snapshot - re-run
<code>python scripts/deploy.py doctor --html</code> to refresh it.</p>
<p class="meta">Summary: {counts['ok']} ok, {counts['warn']} warn, {counts['fail']} fail, {counts['skip']} skip</p>
<table>
<thead><tr><th>Status</th><th>Check</th><th>Detail</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody>
</table>
</body>
</html>
"""


def cmd_gpu_profile(args: argparse.Namespace) -> int:
    name = args.name
    path = GPU_PROFILE_DIR / f"{name}.env"
    if name not in GPU_PROFILES or not path.is_file():
        sys.exit(
            f"[deploy] FATAL: unknown GPU profile {name!r}. "
            f"Choose one of: {', '.join(GPU_PROFILES)}."
        )
    _ensure_env_file()
    overlay = _parse_env_file(path)
    before = _parse_env_file(ENV_FILE)
    ENV_FILE.write_text(
        apply_profile_text(ENV_FILE.read_text(encoding="utf-8"), overlay),
        encoding="utf-8",
    )
    print(f"[deploy] Applied GPU profile {name} to {ENV_FILE}.", flush=True)
    for key, value in overlay.items():
        previous = before.get(key, "")
        mark = " (unchanged)" if previous == value else f" (was {previous or 'unset'})"
        print(f"  {key}={value}{mark}", flush=True)
    if before.get("EMBEDDER_GPU_ARG") != overlay.get("EMBEDDER_GPU_ARG"):
        print(
            "[deploy] EMBEDDER_GPU_ARG changed. Rebuild before the next up: "
            "python scripts/deploy.py build embedder",
            flush=True,
        )
    model_path = overlay.get("LLM_MODEL_PATH", "")
    relative = model_path.removeprefix("/models/").lstrip("/")
    if relative and not (MODELS_DIR / relative).is_file():
        extra = PROFILE_EXTRA.get(name)
        if extra:
            print(
                f"[deploy] {relative} is not in {MODELS_DIR}. Download it before the next serve:\n"
                f"  python scripts/deploy.py fetch-extra {name}",
                flush=True,
            )
        else:
            print(
                f"[deploy] {relative} is not in {MODELS_DIR}. "
                "Run `python scripts/deploy.py up` once so the default 14B is fetched.",
                flush=True,
            )
    if overlay.get("LLM_SERVE") == "host":
        print(
            "[deploy] EVO-X5 Pro: the 119B is not loaded by the CUDA llm container. "
            "That image cannot see the Radeon 8065S.",
            flush=True,
        )
        print("[deploy] python scripts/host/evo_x5_serve.py", flush=True)
        print(
            "[deploy] Do not start the compose llm service. It would bind port 8080 without this GPU.",
            flush=True,
        )
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
    extra = PROFILE_EXTRA.get(name)
    if extra is None:
        sys.exit(
            f"[deploy] FATAL: profile {name} uses the default 14B already fetched by `up`. "
            f"Optional downloads: {', '.join(PROFILE_EXTRA)}."
        )
    _require_podman()
    _run(
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
        cwd=str(REPO_ROOT),
    )
    return 0


def cmd_rotate_token(args: argparse.Namespace) -> int:
    """Generate a new AGENT_BEARER_TOKEN and recreate every holder of it."""
    token = secrets.token_urlsafe(32)
    _write_env_key("AGENT_BEARER_TOKEN", token)
    print(f"[deploy] wrote a new AGENT_BEARER_TOKEN to {ENV_FILE}", flush=True)
    if args.skip_recreate:
        print(
            "[deploy] --skip-recreate: restart agent, app, and mcp yourself so they pick it up",
            flush=True,
        )
        return 0
    # mcp forwards the same bearer; leaving it running keeps the old token.
    # ui-proxy is recreated so nginx re-resolves the new `agent` container.
    _run(
        ["podman", "compose", "up", "-d", "--force-recreate", "agent", "app", "mcp", "ui-proxy"],
        cwd=str(REPO_ROOT),
    )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    env = _parse_env_file(ENV_FILE)

    checks: list[Check] = []
    checks.extend(_doctor_check_podman())
    checks.extend(_doctor_check_env())
    checks.append(_doctor_check_codebase_path(env))
    checks.extend(_doctor_check_models(env, verify_checksums=args.verify_checksums))
    checks.append(_doctor_check_hf_train_base())
    checks.append(_doctor_check_lora_adapter(env))
    checks.append(_doctor_check_train_commit(env))
    checks.append(_doctor_check_disk_space())
    checks.extend(_doctor_check_services())
    checks.extend(_doctor_check_gpu_offload(env))
    if args.gpu:
        checks.append(_doctor_check_gpu())

    _render_terminal(checks)

    if args.html:
        html_path = Path(args.html)
        html_path.write_text(_render_html(checks), encoding="utf-8")
        print(f"\n[deploy] HTML report written to {html_path.resolve()}")

    return 1 if any(c.status == "fail" for c in checks) else 0


def main() -> int:
    _activate_runtime(sys.argv[1:])
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    up = subparsers.add_parser("up", help="fetch models, build, start, wait healthy, index")
    up.add_argument("--skip-models", action="store_true", help="skip the fetch-models step")
    up.add_argument("--skip-index", action="store_true", help="skip running the indexer")
    up.add_argument("--timeout", type=int, default=300, help="seconds to wait for health (default 300)")
    up.set_defaults(func=cmd_up)

    down = subparsers.add_parser("down", help="stop the stack")
    down.add_argument("--volumes", action="store_true", help="also remove the qdrant-data volume")
    down.set_defaults(func=cmd_down)

    subparsers.add_parser("status", help="podman compose ps").set_defaults(func=cmd_status)
    subparsers.add_parser(
        "index", help="recreate tools mount from CODEBASE_PATH, then re-index"
    ).set_defaults(func=cmd_index)

    workspace = subparsers.add_parser(
        "workspace",
        help="register a checkout; one index per remote URL, mount every registered copy",
    )
    workspace.add_argument("path", help="host checkout to register, e.g. d:/svn/ticket-142")
    workspace.add_argument(
        "--update",
        action="store_true",
        help="incremental reindex of this tree into the shared repository collection",
    )
    workspace.set_defaults(func=cmd_workspace)

    eval_cmd = subparsers.add_parser(
        "eval",
        help="retrieval baseline on a registered checkout (lookup only, no chat model)",
    )
    eval_cmd.add_argument("path", help="registered checkout, e.g. D:/rest/opencv")
    eval_cmd.add_argument("--questions", required=True, help="JSON question file")
    eval_cmd.add_argument("--agent", default=None, help="agent base URL (default http://127.0.0.1:8090)")
    eval_cmd.add_argument("--timeout", type=float, default=120.0)
    eval_cmd.add_argument("--out", default=None, help="also write the report JSON")
    eval_cmd.add_argument(
        "--require-clean",
        action="store_true",
        help="exit non-zero when git status is not empty",
    )
    eval_cmd.add_argument(
        "--allow-unfinished",
        action="store_true",
        help="score even when the registry does not mark the index finished",
    )
    eval_cmd.set_defaults(func=cmd_eval)

    train = subparsers.add_parser(
        "train",
        help="build QLoRA dataset from CODEBASE_PATH, fine-tune, convert LoRA, reload llm",
    )
    train.add_argument(
        "--incremental",
        action="store_true",
        help="dataset from git diff since last trained commit",
    )
    train.add_argument(
        "--dry-run",
        action="store_true",
        help="build dataset only, skip GPU training",
    )
    train.add_argument(
        "--skip-index",
        action="store_true",
        help="do not re-run the indexer after training",
    )
    train.add_argument(
        "--force",
        action="store_true",
        help="ignore an existing training lock file",
    )
    train.set_defaults(func=cmd_train)

    ask = subparsers.add_parser(
        "ask",
        help="ask the agent a question via the app CLI (string, --file, or '-' for stdin)",
    )
    ask.add_argument(
        "question",
        nargs="?",
        default=None,
        help="question text, or '-' to read the prompt from stdin",
    )
    ask.add_argument(
        "-f",
        "--file",
        metavar="PATH",
        help="read the prompt from a file (for long prompts; avoids shell ARG_MAX limits)",
    )
    ask.set_defaults(func=cmd_ask)

    subparsers.add_parser("chat", help="interactive multi-turn REPL (streams tokens)").set_defaults(
        func=cmd_chat
    )

    logs = subparsers.add_parser("logs", help="podman compose logs <service>")
    logs.add_argument("service")
    logs.add_argument("--follow", action="store_true")
    logs.set_defaults(func=cmd_logs)

    subparsers.add_parser(
        "test", help="run the tiny-model end-to-end smoke test (no GPU/large downloads needed)"
    ).set_defaults(func=cmd_test)

    doctor = subparsers.add_parser(
        "doctor", help="diagnose prereqs, .env, models, disk space, and service health"
    )
    doctor.add_argument("--html", metavar="PATH", help="also write a static HTML report to PATH")
    doctor.add_argument(
        "--gpu", action="store_true", help="also probe GPU access (slow, pulls ubuntu:24.04 if not cached)"
    )
    doctor.add_argument(
        "--verify-checksums",
        action="store_true",
        help="hash pinned model files and compare against scripts/container/fetch_models.py's checksums",
    )
    doctor.set_defaults(func=cmd_doctor)

    gpu_profile = subparsers.add_parser(
        "gpu-profile",
        help="write one GPU case into .env (14B, a larger coder, or the EVO-X5 host model); does not start containers",
    )
    gpu_profile.add_argument("name", choices=GPU_PROFILES)
    gpu_profile.set_defaults(func=cmd_gpu_profile)

    fetch_extra = subparsers.add_parser(
        "fetch-extra",
        help="download the optional GGUF for 24g-30b, 48g-30b, 48g-32b, or evo-x5",
    )
    fetch_extra.add_argument("name", choices=tuple(PROFILE_EXTRA))
    fetch_extra.set_defaults(func=cmd_fetch_extra)

    rotate = subparsers.add_parser(
        "rotate-token", help="regenerate AGENT_BEARER_TOKEN and recreate agent, app, and mcp"
    )
    rotate.add_argument("--skip-recreate", action="store_true", help="only rewrite .env")
    rotate.set_defaults(func=cmd_rotate_token)

    args = parser.parse_args()
    try:
        return args.func(args)
    except subprocess.CalledProcessError as exc:
        return exc.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
