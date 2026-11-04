"""Compose build/up and health polling."""

from __future__ import annotations

import re
import subprocess
import sys
import time

import deploy as d


def _compose_build_and_up(host: bool = False) -> None:
    if host:
        # qdrant and ui-proxy are images. patch is a dependency of agent and
        # is not in UP_SERVICES; --no-deps would otherwise leave it stopped.
        build_targets = ["embedder", "reranker", "tools", "agent", "mcp", "patch", "indexer"]
        d._run(["podman", "compose", "--profile", "index", "build", *build_targets])
        services = [name for name in (*d.UP_SERVICES, "patch") if name != "llm"]
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


def _parse_compose_ps_health(output: str, services: list[str]) -> dict[str, str]:
    """Best-effort text parsing of `podman compose ps` output shared by
    `_wait_healthy`'s polling loop and `doctor`'s one-shot check.
    Container-naming conventions differ slightly across podman-compose/
    native `podman compose` versions, but both follow
    `{project}-{service}-{index}` (or the older `{project}_{service}_{index}`)
    - this matches the service name anchored right before its trailing
    `-{index}`/`_{index}` suffix, rather than parsing a specific table/JSON
    schema. A plain substring check, or even a `-service-`-delimited one,
    would be wrong here if the compose project name contained the service
    name (historically "local_llm", now compose.yaml `name:` / product.COMPOSE_PROJECT).
    Requiring digits right after the match excludes a false hit inside the
    project prefix and matches solely the real `llm` service's own "-llm-1"
    suffix.

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
            d._compose_cmd("ps"),
            cwd=d.REPO_ROOT,
            capture_output=True,
            text=True,
            env=d._compose_env(),
        )
        statuses = d._parse_compose_ps_health(result.stdout, list(pending))
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
        sys.exit(
            "[deploy] Not all services became healthy in time. Check "
            "`podman compose logs <service>` and docs/guides/troubleshooting-gpu.md."
        )
    print("[deploy] all core services healthy.")
