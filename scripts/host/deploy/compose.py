"""Compose build/up and health polling."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from typing import Any

import deploy as d


def _compose_build_and_up(host: bool = False) -> None:
    if host:
        # qdrant and ui-proxy are images. --no-deps starts only the names below.
        build_targets = ["embedder", "reranker", "tools", "agent", "mcp", "indexer"]
        d._run(["podman", "compose", "--profile", "index", "build", *build_targets])
        services = [name for name in d.UP_SERVICES if name != "llm"]
        d._run(["podman", "compose", "up", "-d", "--no-deps", *services])
        return
    d._run(["podman", "compose", "--profile", "index", "build"])
    try:
        d._run(["podman", "compose", "up", "-d", *d.UP_SERVICES])
    except subprocess.CalledProcessError:
        if d._USE_CPU_COMPOSE_OVERRIDE or d._cdi_probe():
            raise
        print(
            "[deploy] compose up failed and CDI is still unavailable - "
            "switching to CPU fallback.",
            file=sys.stderr,
        )
        d._enable_cpu_compose_fallback(persist_env=False)
        d._run(["podman", "compose", "build", "llm", "embedder"])
        d._run(["podman", "compose", "up", "-d", *d.UP_SERVICES])


def _compose_ps_records(output: str) -> list[dict[str, Any]] | None:
    """Parse `compose ps --format json` (array or NDJSON). None if this is not JSON."""
    text = (output or "").strip()
    if not text:
        return None
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        records: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                return None
            if not isinstance(item, dict):
                return None
            records.append(item)
        return records or None
    if isinstance(loaded, dict):
        return [loaded]
    if isinstance(loaded, list):
        return [item for item in loaded if isinstance(item, dict)]
    return None


def _compose_ps_service_name(record: dict[str, Any]) -> str:
    for key in ("Service", "service"):
        raw = record.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    labels = record.get("Labels") or record.get("labels") or ""
    if isinstance(labels, dict):
        for key in ("com.docker.compose.service", "io.podman.compose.service"):
            value = labels.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(labels, str):
        for part in labels.split(","):
            key, _, value = part.partition("=")
            if key.strip() in ("com.docker.compose.service", "io.podman.compose.service") and value.strip():
                return value.strip()
    return ""


def _compose_ps_health_status(record: dict[str, Any]) -> str:
    health = str(record.get("Health") or record.get("health") or "").strip().lower()
    state = str(record.get("State") or record.get("state") or "").strip().lower()
    status = str(record.get("Status") or record.get("status") or "")
    lowered = f"{health} {state} {status}".lower()
    if "unhealthy" in lowered:
        return "unhealthy"
    if health == "healthy" or "(healthy)" in status.lower():
        return "healthy"
    if health == "starting" or "starting" in status.lower():
        return "starting"
    if state in ("running", "up") or status.lower().startswith("up"):
        return "unknown"
    return "not_found"


def _parse_compose_ps_json(output: str, services: list[str]) -> dict[str, str] | None:
    records = _compose_ps_records(output)
    if records is None:
        return None
    statuses = {service: "not_found" for service in services}
    for record in records:
        name = _compose_ps_service_name(record)
        if name in statuses:
            statuses[name] = _compose_ps_health_status(record)
    return statuses


def _parse_compose_ps_health(output: str, services: list[str]) -> dict[str, str]:
    """Best-effort text parsing of `podman compose ps` output.

    Used when `--format json` is missing. Prefer `_health_from_compose_ps`.
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


def _health_from_compose_ps(output: str, services: list[str]) -> dict[str, str]:
    """JSON `Service`/`Health` when the engine provides it, else the table parser."""
    parsed = d._parse_compose_ps_json(output, services)
    if parsed is not None:
        return parsed
    return d._parse_compose_ps_health(output, services)


def _compose_ps_payload() -> tuple[int, str, str]:
    """Stdout from `compose ps --format json` when that is JSON, else plain `ps`."""
    kwargs: dict[str, Any] = {
        "cwd": d.REPO_ROOT,
        "capture_output": True,
        "text": True,
        "env": d._compose_env(),
    }
    formatted = subprocess.run(d._compose_cmd("ps", "--format", "json"), **kwargs)
    if formatted.returncode == 0 and _compose_ps_records(formatted.stdout) is not None:
        return formatted.returncode, formatted.stdout, getattr(formatted, "stderr", None) or ""
    plain = subprocess.run(d._compose_cmd("ps"), **kwargs)
    return plain.returncode, plain.stdout, getattr(plain, "stderr", None) or ""


def _wait_healthy(services: list[str], timeout: int) -> None:
    """Polls compose until every service is healthy, or the timeout is hit."""
    print(f"[deploy] waiting up to {timeout}s for {', '.join(services)} to become healthy...")
    deadline = time.monotonic() + timeout
    pending = set(services)
    last_code = 0
    last_err = ""
    while time.monotonic() < deadline and pending:
        last_code, stdout, last_err = d._compose_ps_payload()
        statuses = d._health_from_compose_ps(stdout, list(pending))
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
        d._run(["podman", "compose", "ps"], check=False)
        extra = last_err.strip()[:300] if last_code else ""
        hint = f"\n{extra}" if extra else ""
        sys.exit(
            "[deploy] Not all services became healthy in time. Check "
            "`podman compose logs <service>` and docs/guides/troubleshooting-gpu.md."
            f"{hint}"
        )
    print("[deploy] all core services healthy.")


def _wait_published_doors(*, host: bool, timeout: int) -> None:
    """The host loopback ports, not only the in-container healthchecks."""
    env = d._parse_env_file(d.ENV_FILE)
    agent_port = (env.get("AGENT_PORT") or "8090").strip() or "8090"
    print(f"[deploy] waiting for ui-proxy on 127.0.0.1:{agent_port}", flush=True)
    d._wait_http(f"http://127.0.0.1:{agent_port}/health", timeout=timeout)
    if not host:
        return
    llm_port = (env.get("LLM_PORT") or "8080").strip() or "8080"
    llama_timeout = max(timeout, d.HOST_LLM_READY_TIMEOUT)
    print(f"[deploy] waiting for host llama-server on 127.0.0.1:{llm_port}", flush=True)
    process = None
    if d.HOST_LLM_PID.is_file():
        raw = d.HOST_LLM_PID.read_text(encoding="utf-8").strip()
        if raw.isdigit():

            class _Existing:
                def poll(self) -> int | None:
                    return None if d._host_llm_alive(int(raw)) else 1

            process = _Existing()
    d._wait_http(
        f"http://127.0.0.1:{llm_port}/health",
        timeout=llama_timeout,
        process=process,
    )
