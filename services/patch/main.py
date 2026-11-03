"""HTTP writer for one checkout. Not published on the host.

`agent` and `tools` keep the tree read-only. This process is the only one
whose checkout mount is read-write. It does not commit or push.
"""

import os
from contextvars import ContextVar
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services.common.tool_models import ApplyArgs, BeginPlanArgs, EditArgs

from .edits import (
    EditError,
    apply_proposal,
    begin_plan,
    note_target,
    propose_edit,
    should_build_solution,
)

app = FastAPI(title="patch", version="1.0.0")

PROJECT_ROOT = Path(os.environ.get("PATCH_PROJECT_ROOT", "/workspace/app"))
_checkout_root: ContextVar[Path | None] = ContextVar("patch_checkout_root", default=None)


def active_root() -> Path:
    chosen = _checkout_root.get()
    return chosen if chosen is not None else PROJECT_ROOT


class _CheckoutRootMiddleware:
    """Bind X-Project-Root in the same task as the endpoint."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        raw = ""
        for key, value in scope.get("headers") or []:
            if key.lower() == b"x-project-root":
                raw = value.decode("utf-8", errors="replace")
                break
        token = _checkout_root.set(None)
        try:
            if raw.strip():
                path = Path(raw.strip())
                resolved = path.resolve(strict=True)
                if resolved.parent != Path("/checkouts"):
                    raise ValueError("X-Project-Root must be a direct child of /checkouts")
                _checkout_root.set(resolved)
            await self.app(scope, receive, send)
        except (ValueError, OSError) as exc:
            response = JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
            await response(scope, receive, send)
        finally:
            _checkout_root.reset(token)


app.add_middleware(_CheckoutRootMiddleware)


class TargetBody(BaseModel):
    campaign_id: str
    target: str = ""


def _run(func):
    try:
        return func()
    except EditError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "patch"}


@app.post("/begin")
def begin(body: BeginPlanArgs):
    return _run(lambda: begin_plan(active_root(), body.plan, body.focus, body.max_files))


@app.post("/edit")
def edit(body: EditArgs):
    return _run(lambda: propose_edit(active_root(), body.campaign_id, body.path, body.old, body.new))


@app.post("/apply")
def apply(body: ApplyArgs):
    return _run(lambda: apply_proposal(active_root(), body.campaign_id, body.proposal_id))


@app.post("/target")
def target(body: TargetBody):
    def _note():
        targets = note_target(body.campaign_id, body.target)
        return {"ok": True, "targets": sorted(targets), "solution": should_build_solution(targets)}

    return _run(_note)
