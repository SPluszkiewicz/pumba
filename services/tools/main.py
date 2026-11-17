"""FastAPI app for the `tools` service.

Exposes ONLY the fixed, parameterized endpoints listed in CONTRACTS.md -
there is no generic shell/exec passthrough anywhere in this service. An LLM
decides when to call this service, so every input is treated as hostile:
paths are re-validated (`handlers/path_safety.py`), preset names are
allow-listed, and every subprocess is invoked as an argv list with
`shell=False`. `POST /cancel` does not run a command: it kills the
process group of the `X-Request-Id` and answers 204.

Configuration is read exclusively from environment variables (no hardcoded
hosts/ports/paths), per CONTRACTS.md:
  - `TOOLS_BUILD_TIMEOUT_SECONDS` (one backstop for every subprocess)
  - `TOOLS_CMAKE_PRESET` (default preset name when a request omits `preset`)
  - `TOOLS_PROJECT_ROOT` (default `/workspace/app`, matching the bind mount
    `${CODEBASE_PATH:-./sample}:/workspace/app:ro` in compose.yaml) - the
    mounted, read-only default tree every caller-supplied path is validated
    against. A registered checkout is mounted at `/checkouts/<id>` and
    selected per request by the `X-Project-Root` header (see
    `checkout_root_from_header`).
  - `TOOLS_HEALTH_STRICT` (`1` in compose: a missing binary is HTTP 503)
"""

from __future__ import annotations

import os
import shutil
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response

from services.common.metrics import instrument_app
from services.common.tool_models import (
    BuildArgs,
    BuildStepArgs,
    CompileArgs,
    GitBlameArgs,
    GitDiffArgs,
    GitLogArgs,
    GitShowArgs,
    LspCallsArgs,
    LspOutlineArgs,
    LspTypesArgs,
    PathsArgs,
    ReadFileArgs,
    SvnDiffArgs,
    SvnLogArgs,
    SvnStatusArgs,
    TestArgs,
)
from services.tools.cancel_group import begin_http, bind_request_id, end_http, kill_request, reset_request_id
from services.tools.handlers import (
    build,
    format_check,
    git,
    lint,
    lsp,
    read_file,
    static_analysis,
    svn,
)
from services.tools.handlers import (
    compile as compile_handler,
)
from services.tools.handlers import test as test_handler
from services.tools.handlers.path_safety import PathSafetyError, mounted_checkout_root

# --------------------------------------------------------------------------- config (env vars only)

PROJECT_ROOT = Path(os.environ.get("TOOLS_PROJECT_ROOT", "/workspace/app"))
_checkout_root: ContextVar[Path | None] = ContextVar("checkout_root", default=None)


def active_root() -> Path:
    """Default tree, or the checkout named by this request's X-Project-Root."""
    chosen = _checkout_root.get()
    return chosen if chosen is not None else PROJECT_ROOT


def checkout_root_from_header(raw: str) -> Path | None:
    """Accept only `/checkouts/<id>`. Anything else is refused."""
    return mounted_checkout_root(raw)


BUILD_TIMEOUT_SECONDS = int(os.environ.get("TOOLS_BUILD_TIMEOUT_SECONDS", "14400"))
DEFAULT_PRESET = os.environ.get("TOOLS_CMAKE_PRESET", "release")

# One backstop for every subprocess. A large checkout (clangd's first index,
# clang-tidy, svn status, cmake) must be allowed to finish. This only stops
# a hang; the caller aborts a question from the UI.
LINT_TIMEOUT_SECONDS = BUILD_TIMEOUT_SECONDS
FORMAT_TIMEOUT_SECONDS = BUILD_TIMEOUT_SECONDS
STATIC_ANALYSIS_TIMEOUT_SECONDS = BUILD_TIMEOUT_SECONDS
GIT_TIMEOUT_SECONDS = BUILD_TIMEOUT_SECONDS
SVN_TIMEOUT_SECONDS = BUILD_TIMEOUT_SECONDS
COMPILE_TIMEOUT_SECONDS = BUILD_TIMEOUT_SECONDS
REQUIRED_BINARIES = ("cmake", "ctest", "clang-tidy", "clang-format", "clangd", "cppcheck", "rg", "git", "svn")
# Strict 503 is for the running container (compose healthcheck). Unit tests
# and host pytest leave this off so /health stays 200 with `missing` listed.
HEALTH_STRICT = os.environ.get("TOOLS_HEALTH_STRICT", "0") == "1"


# --------------------------------------------------------------------------- request models
#
# Defined once in services/common/tool_models.py and shared with the `agent`
# (which derives the LLM's function-calling schema from the same classes).
# The old local names are kept as aliases for readers of CONTRACTS.md.
BuildRequest = BuildArgs
TestRequest = TestArgs
PathsRequest = PathsArgs


# --------------------------------------------------------------------------- app + error handling

app = FastAPI(title="tools", version="1.0.0")
instrument_app(app)


class _CheckoutRootMiddleware:
    """Bind X-Project-Root and X-Request-Id in the same task as the endpoint."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        raw = ""
        request_id = ""
        seen_root = False
        for key, value in scope.get("headers") or []:
            lowered = key.lower()
            if lowered == b"x-project-root" and not seen_root:
                raw = value.decode("utf-8", errors="replace")
                seen_root = True
            elif lowered == b"x-request-id" and not request_id:
                request_id = value.decode("utf-8", errors="replace").strip()
        begin_http(request_id)
        id_token = bind_request_id(request_id)
        try:
            try:
                chosen = checkout_root_from_header(raw)
            except ValueError as exc:
                response = JSONResponse(status_code=400, content=_error_body(str(exc)))
                await response(scope, receive, send)
                return
            token = _checkout_root.set(chosen)
            try:
                await self.app(scope, receive, send)
            finally:
                _checkout_root.reset(token)
        finally:
            reset_request_id(id_token)
            end_http(request_id)


app.add_middleware(_CheckoutRootMiddleware)


def _error_body(message: str, exit_code: int = -1) -> dict:
    # `exit_code = -1` signals "never executed a subprocess" (as opposed to
    # a real 0-255 process exit code), so callers can tell validation
    # failures apart from a tool that actually ran and failed.
    return {"ok": False, "stdout": "", "stderr": message, "exit_code": exit_code}


@app.exception_handler(PathSafetyError)
async def _handle_path_safety_error(_request: Request, exc: PathSafetyError) -> JSONResponse:
    return JSONResponse(status_code=400, content=_error_body(str(exc)))


@app.exception_handler(read_file.ReadFileNotFoundError)
async def _handle_read_file_not_found(_request: Request, exc: read_file.ReadFileNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content=_error_body(str(exc)))


@app.exception_handler(read_file.FileTooLargeError)
async def _handle_file_too_large(_request: Request, exc: read_file.FileTooLargeError) -> JSONResponse:
    return JSONResponse(status_code=413, content=_error_body(str(exc)))


@app.exception_handler(read_file.NotAFileError)
async def _handle_not_a_file(_request: Request, exc: read_file.NotAFileError) -> JSONResponse:
    return JSONResponse(status_code=400, content=_error_body(str(exc)))


@app.exception_handler(FileNotFoundError)
async def _handle_file_not_found(_request: Request, exc: FileNotFoundError) -> JSONResponse:
    # Raised by a handler for a missing path. (A missing tool binary
    # also surfaces as FileNotFoundError from subprocess.run - but that one
    # carries an errno; keep the contract simple and report 404 only when
    # the handler raised it deliberately, i.e. without errno.)
    if getattr(exc, "errno", None) is not None:
        return JSONResponse(status_code=500, content=_error_body(f"internal error: {exc}"))
    return JSONResponse(status_code=404, content=_error_body(str(exc)))


@app.exception_handler(ValueError)
async def _handle_value_error(_request: Request, exc: ValueError) -> JSONResponse:
    # Catches build.PresetValidationError, lint/format/static-analysis's
    # "empty paths" errors and
    # read_file.LineRangeError - all are ValueError subclasses raised before
    # any subprocess runs.
    return JSONResponse(status_code=400, content=_error_body(str(exc)))


@app.exception_handler(Exception)
async def _handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
    # Defense in depth: e.g. the underlying tool binary isn't installed
    # (subprocess.run raises FileNotFoundError). Never leak a bare
    # traceback; still honor the {ok, stdout, stderr, exit_code} shape.
    return JSONResponse(status_code=500, content=_error_body(f"internal error: {exc}"))


# --------------------------------------------------------------------------- routes
#
# Every route that runs a subprocess is a plain `def`: FastAPI runs it in its
# thread pool (copying the request's context, so `active_root()` still sees
# X-Project-Root). An `async def` calling the blocking `subprocess.run` would
# freeze the event loop for the whole build, starving concurrent requests and
# `/health` (the compose healthcheck would then mark the container unhealthy).
# `tests/tools/test_routes_non_blocking.py` enforces this.

@app.post("/cancel", status_code=204, response_model=None)
def cancel(request: Request) -> Response:
    """Kill the process group of this X-Request-Id. 204 even when none is running."""
    kill_request(request.headers.get("x-request-id", ""))
    return Response(status_code=204)


@app.get("/health", response_model=None)
async def health() -> dict | JSONResponse:
    missing = [name for name in REQUIRED_BINARIES if shutil.which(name) is None]
    body = {"ok": not missing, "service": "tools", "missing": missing}
    if missing and HEALTH_STRICT:
        return JSONResponse(status_code=503, content=body)
    return body


@app.post("/build")
def build_endpoint(request: BuildRequest) -> dict:
    return build.run_build(request.preset, active_root(), BUILD_TIMEOUT_SECONDS)


@app.post("/build-step")
def build_step_endpoint(request: BuildStepArgs) -> dict:
    return build.run_cached_build(request.preset, active_root(), BUILD_TIMEOUT_SECONDS, request.target)


@app.post("/test")
def test_endpoint(request: TestRequest) -> dict:
    return test_handler.run_test(request.preset, active_root(), BUILD_TIMEOUT_SECONDS, filt=request.filter)


@app.post("/compile")
def compile_endpoint(request: CompileArgs) -> dict:
    return compile_handler.run_compile_file(request.path, request.preset, active_root(), COMPILE_TIMEOUT_SECONDS)


@app.post("/git/log")
def git_log_endpoint(request: GitLogArgs) -> dict:
    return git.run_git_log(active_root(), GIT_TIMEOUT_SECONDS, raw_path=request.path, max_count=request.max_count)


@app.post("/git/blame")
def git_blame_endpoint(request: GitBlameArgs) -> dict:
    return git.run_git_blame(
        active_root(),
        GIT_TIMEOUT_SECONDS,
        raw_path=request.path,
        start_line=request.start_line,
        end_line=request.end_line,
    )


@app.post("/git/diff")
def git_diff_endpoint(request: GitDiffArgs) -> dict:
    return git.run_git_diff(
        active_root(), GIT_TIMEOUT_SECONDS, ref_a=request.ref_a, ref_b=request.ref_b, raw_path=request.path
    )


@app.post("/git/show")
def git_show_endpoint(request: GitShowArgs) -> dict:
    return git.run_git_show(active_root(), GIT_TIMEOUT_SECONDS, ref=request.ref, raw_path=request.path)


@app.post("/svn/status")
def svn_status_endpoint(request: SvnStatusArgs) -> dict:
    return svn.run_svn_status(active_root(), SVN_TIMEOUT_SECONDS, raw_path=request.path)


@app.post("/svn/diff")
def svn_diff_endpoint(request: SvnDiffArgs) -> dict:
    return svn.run_svn_diff(
        active_root(),
        SVN_TIMEOUT_SECONDS,
        raw_path=request.path,
        rev_from=request.rev_from,
        rev_to=request.rev_to,
    )


@app.post("/svn/log")
def svn_log_endpoint(request: SvnLogArgs) -> dict:
    return svn.run_svn_log(
        active_root(), SVN_TIMEOUT_SECONDS, raw_path=request.path, max_count=request.max_count
    )


@app.post("/lsp/outline")
def lsp_outline_endpoint(request: LspOutlineArgs) -> dict:
    return lsp.run_outline(request.path, request.preset, active_root(), COMPILE_TIMEOUT_SECONDS)


@app.post("/lsp/calls")
def lsp_calls_endpoint(request: LspCallsArgs) -> dict:
    return lsp.run_calls(
        request.path,
        request.line,
        request.character,
        request.direction,
        request.preset,
        active_root(),
        COMPILE_TIMEOUT_SECONDS,
    )


@app.post("/lsp/types")
def lsp_types_endpoint(request: LspTypesArgs) -> dict:
    return lsp.run_types(
        request.path,
        request.line,
        request.character,
        request.direction,
        request.preset,
        active_root(),
        COMPILE_TIMEOUT_SECONDS,
    )


@app.post("/git/status")
def git_status_endpoint() -> dict:
    """Uncommitted names for the agent overlay. Not an LLM tool."""
    return git.run_git_status(active_root(), GIT_TIMEOUT_SECONDS)


@app.post("/git/diff-names")
def git_diff_names_endpoint(request: GitDiffArgs) -> dict:
    """File list for `agent` `/review`. Not an LLM tool."""
    return git.run_git_diff_names(active_root(), GIT_TIMEOUT_SECONDS, ref_a=request.ref_a, ref_b=request.ref_b)


@app.post("/lint/clang-tidy")
def lint_endpoint(request: PathsRequest) -> dict:
    return lint.run_lint(request.paths, active_root(), LINT_TIMEOUT_SECONDS)


@app.post("/format/check")
def format_check_endpoint(request: PathsRequest) -> dict:
    return format_check.run_format_check(request.paths, active_root(), FORMAT_TIMEOUT_SECONDS)


@app.post("/static-analysis")
def static_analysis_endpoint(request: PathsRequest) -> dict:
    return static_analysis.run_static_analysis(request.paths, active_root(), STATIC_ANALYSIS_TIMEOUT_SECONDS)


# GET endpoints take the shared model as a *query-string* model (FastAPI >= 0.115).
@app.get("/read-file")
def read_file_endpoint(args: Annotated[ReadFileArgs, Query()]) -> dict:
    return read_file.run_read_file(args.path, active_root(), start_line=args.start_line, end_line=args.end_line)
