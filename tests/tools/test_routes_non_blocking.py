"""Routes that run a subprocess must not block the event loop.

A blocking `subprocess.run` inside an `async def` route freezes the whole
service for the length of a build: concurrent requests queue up and the
compose healthcheck on `/health` times out.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.routing import APIRoute

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.tools import main as tools_main  # noqa: E402

# Pure, instant handlers that may stay `async def`.
_ASYNC_ALLOWED = {"/health", "/metrics"}


def test_subprocess_routes_are_plain_def() -> None:
    offenders = [
        route.path
        for route in tools_main.app.routes
        if isinstance(route, APIRoute)
        and route.path not in _ASYNC_ALLOWED
        and inspect.iscoroutinefunction(route.endpoint)
    ]
    assert offenders == [], f"these routes would block the event loop: {offenders}"


@pytest.mark.asyncio
async def test_health_answers_during_a_slow_build_and_root_reaches_the_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkouts" / "repo_1"
    checkout.mkdir(parents=True)
    started = threading.Event()
    release = threading.Event()
    seen: dict[str, Path] = {}

    def slow_build(preset: str, project_root: Path, _timeout: int) -> dict:
        seen["root"] = project_root
        started.set()
        release.wait(timeout=10)
        return {"ok": True, "stdout": "", "stderr": "", "exit_code": 0, "preset": preset}

    monkeypatch.setattr(tools_main.build, "run_build", slow_build)
    monkeypatch.setattr(tools_main, "checkout_root_from_header", lambda raw: checkout if raw.strip() else None)

    transport = httpx.ASGITransport(app=tools_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://tools") as client:
        build_task = asyncio.create_task(
            client.post("/build", json={"preset": "release"}, headers={"X-Project-Root": str(checkout)})
        )
        await asyncio.to_thread(started.wait, 5)
        assert started.is_set(), "build handler never started"

        health = await asyncio.wait_for(client.get("/health"), timeout=5)
        assert health.status_code == 200
        assert not build_task.done()

        release.set()
        build = await asyncio.wait_for(build_task, timeout=10)

    assert build.status_code == 200
    assert seen["root"] == checkout
