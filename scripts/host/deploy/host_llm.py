"""Host llama-server (EVO-X5): weights, health wait, start/stop."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import deploy as d


def _host_llm_selected() -> bool:
    """True when the chat model is llama-server on the host, not the llm container."""
    if not d.ENV_FILE.is_file():
        return False
    return d._parse_env_file(d.ENV_FILE).get("LLM_SERVE") == "host"


def _host_llm_port() -> str:
    if not d.ENV_FILE.is_file():
        return "8080"
    return (d._parse_env_file(d.ENV_FILE).get("LLM_PORT") or "8080").strip() or "8080"


def _host_llm_health_url() -> str:
    return f"http://127.0.0.1:{_host_llm_port()}/health"


def _stop_llm_container() -> None:
    """Drop a leftover llm container so host llama-server can bind the port."""
    d._run(["podman", "compose", "stop", "llm"], check=False)
    d._run(["podman", "compose", "rm", "-f", "llm"], check=False)


def _host_llm_alive(pid: int) -> bool:
    if os.name == "nt":
        probe = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            check=False,
            capture_output=True,
            text=True,
        )
        return str(pid) in probe.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _host_llm_weights_missing() -> list[Path]:
    import evo_x5_serve as serve

    raw = d._parse_env_file(d.ENV_FILE).get("LLM_MODEL_PATH", "")
    if not raw:
        return []
    try:
        first = serve.host_weight(raw)
    except SystemExit:
        return [Path(raw)]
    return serve.missing_shards(first)


def _require_host_llm_weights() -> None:
    missing = d._host_llm_weights_missing()
    if not missing:
        return
    listed = "\n".join(f"  {path}" for path in missing)
    sys.exit(
        "[deploy] FATAL: the host chat model is not on disk:\n"
        f"{listed}\n"
        "Download it, then re-run up:\n"
        "  python scripts/deploy.py fetch-extra evo-x5"
    )


def _wait_http(
    url: str,
    *,
    timeout: float,
    process: Any = None,
    log_path: Path | None = None,
) -> None:
    """Poll until `url` answers. A dead child process fails immediately."""
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            extra = f" Journal : {log_path}" if log_path else ""
            sys.exit(f"[deploy] FATAL: process exited before {url} answered.{extra}")
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    return
                last = f"HTTP {resp.status}"
        except (OSError, urllib.error.URLError) as exc:
            last = str(exc)
        time.sleep(1)
    extra = f" Journal : {log_path}" if log_path else ""
    sys.exit(f"[deploy] FATAL: {url} not ready in {int(timeout)}s. {last}{extra}")


def _start_host_llm(*, timeout: int = 300, wait: bool = True) -> None:
    """The EVO profile's chat model. `up` owns it; there is no second start command.

    `wait=False` only spawns the process so compose build can overlap the
    ~83 Go load. `up` then waits on `/health` via `_wait_published_doors`.
    """
    if d.HOST_LLM_PID.is_file():
        raw = d.HOST_LLM_PID.read_text(encoding="utf-8").strip()
        if raw.isdigit() and d._host_llm_alive(int(raw)):
            print(f"[deploy] llama-server hôte déjà lancé (pid {raw}).", flush=True)
            if not wait:
                return

            class _Existing:
                def poll(self) -> int | None:
                    return None if d._host_llm_alive(int(raw)) else 1

            d._wait_http(_host_llm_health_url(), timeout=timeout, process=_Existing())
            return
    d.HOST_LLM_PID.parent.mkdir(parents=True, exist_ok=True)
    log_path = d.HOST_LLM_PID.with_suffix(".log")
    log = log_path.open("ab")
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    import evo_x5_serve as serve

    port = _host_llm_port()
    binary = serve.ensure_binary("llama-server", backend="vulkan")
    if not binary:
        sys.exit(
            "[deploy] FATAL: llama-server introuvable sur PATH, "
            "et le binaire Vulkan b10216 n'a pas pu être installé."
        )
    proc = subprocess.Popen(
        [
            sys.executable,
            str(d.REPO_ROOT / "scripts" / "host" / "evo_x5_serve.py"),
            "--run",
            "--binary",
            binary,
            "--port",
            port,
        ],
        cwd=d.REPO_ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        **kwargs,
    )
    log.close()
    d.HOST_LLM_PID.write_text(str(proc.pid), encoding="utf-8")
    print(f"[deploy] llama-server hôte pid {proc.pid}. Journal {log_path}.", flush=True)
    if wait:
        d._wait_http(_host_llm_health_url(), timeout=timeout, process=proc, log_path=log_path)


def _stop_host_llm() -> None:
    if not d.HOST_LLM_PID.is_file():
        return
    raw = d.HOST_LLM_PID.read_text(encoding="utf-8").strip()
    d.HOST_LLM_PID.unlink(missing_ok=True)
    if not raw.isdigit():
        return
    pid = int(raw)
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )
        return
    # POSIX only. The server was started with start_new_session, so the pid is the group id.
    # getattr keeps this type-checking on Windows, where os.killpg is absent.
    kill_group = getattr(os, "killpg", os.kill)
    try:
        kill_group(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            return
    print(f"[deploy] llama-server hôte arrêté (pid {pid}).", flush=True)
