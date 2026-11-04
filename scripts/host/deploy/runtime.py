"""Container engine selection, WSL hand-off, and compose argv."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys

import deploy as d
import product


def _compose_argv_engine(cmd: list[str]) -> bool:
    return len(cmd) >= 2 and cmd[1] == "compose" and cmd[0] in ("podman", "docker")


def _compose_cmd(*parts: str) -> list[str]:
    """Build a compose argv for the selected engine.

    Docker gets config/compose/docker.yaml so CDI devices are replaced by `gpus: all`.
    CPU fallback uses config/compose/cpu.yaml. On Docker it also stacks
    config/compose/docker.cpu.yaml, which drops the GPU reservation.
    Podman gets config/compose/podman.yaml so publish-net has no default route.
    """
    engine = "docker" if d._RUNTIME == "docker" else "podman"
    cmd = [engine, "compose", "-f", "compose.yaml"]
    if engine == "docker" and not d._USE_CPU_COMPOSE_OVERRIDE:
        cmd.extend(["-f", "config/compose/docker.yaml"])
    if d._USE_CPU_COMPOSE_OVERRIDE:
        cmd.extend(["-f", "config/compose/cpu.yaml"])
        if engine == "docker":
            cmd.extend(["-f", "config/compose/docker.cpu.yaml"])
    if engine == "podman":
        cmd.extend(["-f", "config/compose/podman.yaml"])
    if d._host_llm_selected() and ".env.test" not in parts:
        cmd.extend(["-f", "config/compose/host-llm.yaml"])
    mounts = d.REPO_ROOT / "state" / "compose.workspaces.yaml"
    if mounts.is_file():
        cmd.extend(["-f", "state/compose.workspaces.yaml"])
    cmd.extend(parts)
    return cmd


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    if d._compose_argv_engine(cmd):
        cmd = d._compose_cmd(*cmd[2:])
    print(f"[deploy] $ {' '.join(cmd)}", flush=True)
    check = kwargs.pop("check", True)
    env = kwargs.pop("env", None)
    if env is None and d._compose_argv_engine(cmd):
        env = d._compose_env()
    run_kwargs = {"cwd": d.REPO_ROOT, "check": check, **kwargs}
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
        linux_repo = d._normalize_codebase_path(str(d.REPO_ROOT))
        return (
            "The podman CLI on PATH cannot reach a Linux VM. On this setup Podman "
            f"runs in the WSL distro {d.WSL_DISTRO_DEFAULT} (the default Ubuntu distro is not that VM). "
            "Start it and run deploy from there:\n"
            f"  wsl -d {d.WSL_DISTRO_DEFAULT}\n"
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
        return d._podman_compose_hint(detail)
    return (
        "Docker Compose isn't working. Install Docker Desktop, or set "
        "CONTAINER_RUNTIME=podman. See docs/getting-started/prerequisites.md."
    )


def _requested_runtime(env: dict[str, str]) -> str:
    raw = (os.environ.get("CONTAINER_RUNTIME") or env.get("CONTAINER_RUNTIME") or "auto").strip().lower()
    if raw not in d._VALID_RUNTIMES:
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
    that cannot reach a machine is handed to the WSL distro in
    WSL_DISTRO_DEFAULT unless Docker already works.
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
        cwd=d.REPO_ROOT,
        capture_output=True,
        text=True,
    )
    detail = d._command_output(probe) or f"exit {probe.returncode}"
    return probe.returncode == 0, detail


def _wsl_reexec_command(argv: list[str], *, distro: str, repo: str) -> list[str]:
    quoted = " ".join(shlex.quote(arg) for arg in argv)
    inner = f"cd {shlex.quote(repo)} && exec python3 scripts/deploy.py {quoted}"
    return ["wsl", "-d", distro, "-e", "bash", "-lc", inner]


def _needs_container_engine(argv: list[str]) -> bool:
    """False for commands that only touch files on this machine."""
    if not argv or argv[0] in ("-h", "--help"):
        return False
    command = next((item for item in argv if not item.startswith("-")), "")
    return command not in d._HOST_ONLY_COMMANDS and command not in ("-h", "--help", "")


def _wsl_distro_ready(distro: str) -> tuple[bool, str]:
    if shutil.which("wsl") is None:
        return False, "wsl.exe is not on PATH"
    probe = subprocess.run(
        ["wsl", "-d", distro, "-e", "true"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or f"exit {probe.returncode}").strip()
        return False, detail
    return True, ""


def _activate_runtime(argv: list[str]) -> None:
    """Pick podman or docker, or continue the same command inside WSL."""
    if not d._needs_container_engine(argv):
        return
    requested = d._requested_runtime(d._parse_env_file(d.ENV_FILE))
    podman_ok, podman_detail = d._engine_probe("podman")
    docker_ok = False
    if requested in ("auto", "docker"):
        docker_ok, _docker_detail = d._engine_probe("docker")
    engine, reexec = d.choose_runtime(
        requested,
        podman_ok=podman_ok,
        docker_ok=docker_ok,
        podman_detail=podman_detail,
        on_windows=os.name == "nt",
    )
    if reexec and os.environ.get("DEPLOY_SKIP_WSL") != "1":
        distro = os.environ.get("DEPLOY_WSL_DISTRO", d.WSL_DISTRO_DEFAULT)
        ready, detail = d._wsl_distro_ready(distro)
        if not ready:
            sys.exit(
                "[deploy] FATAL: Windows Podman has no machine, and WSL distro "
                f"{distro!r} cannot be started.\n{detail}\n"
                "Install Podman inside that distro, or set DEPLOY_WSL_DISTRO to "
                "the distro that has it, or set CONTAINER_RUNTIME=docker.\n"
                "See docs/getting-started/prerequisites.md."
            )
        command = d._wsl_reexec_command(
            argv,
            distro=distro,
            repo=d._normalize_codebase_path(str(d.REPO_ROOT)),
        )
        print(
            f"[deploy] Windows Podman has no machine; continuing in WSL distro {distro}.",
            flush=True,
        )
        raise SystemExit(subprocess.call(command))
    d._RUNTIME = engine
    print(f"[deploy] container runtime: {d._RUNTIME}", flush=True)


def _require_podman() -> None:
    engine = d._RUNTIME
    if shutil.which(engine) is None:
        sys.exit(
            f"[deploy] FATAL: `{engine}` not found on PATH. Install it first - "
            "see docs/getting-started/prerequisites.md."
        )
    probe = subprocess.run(
        [engine, "compose", "version"], cwd=d.REPO_ROOT, capture_output=True, text=True
    )
    if probe.returncode != 0:
        detail = d._command_output(probe) or f"exit {probe.returncode}"
        hint = d._compose_failure_hint(engine, detail)
        sys.exit(f"[deploy] FATAL: `{engine} compose version` failed.\n{detail}\n{hint}")


def _compose_env() -> dict[str, str]:
    """Process env for `podman compose`, with CODEBASE_PATH normalized for WSL."""
    env = os.environ.copy()
    file_env = d._parse_env_file(d.ENV_FILE)
    raw = file_env.get("CODEBASE_PATH", "./sample")
    env["CODEBASE_PATH"] = d._normalize_codebase_path(raw)
    env.setdefault("COMPOSE_PROJECT_NAME", product.COMPOSE_PROJECT)
    if d._USE_CPU_COMPOSE_OVERRIDE:
        env.update(d._CPU_FALLBACK_ENV)
    return env
