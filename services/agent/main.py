"""FastAPI orchestrator service (`agent`).

Exposes `POST /chat`, `POST /chat/stream`, and `POST /review`
(bearer-authenticated), `GET /ui` (the loopback chat page), and `GET /health`.
See CONTRACTS.md for the schemas and env var names.

Every `/chat` call gets a fresh request id (uuid4) that is forwarded as
`X-Request-Id` on every outbound call to llm/embedder/qdrant/tools and
included in every structured JSON log line emitted here, so a single request
can be traced end-to-end across services.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Optional

import httpx
import tool_loop
from checkouts import Checkout, UnknownWorkspace, fetch_local_note, resolve_workspace
from cited_files import named_file_block
from common.logging import bind_request_id, clear_request_context, get_logger
from common.metrics import (
    instrument_app,
    observe_chat_rejected,
    observe_chat_slots,
    observe_rag,
    observe_rag_retrieval,
    observe_reflection,
    observe_turn,
    set_chat_slot_limit,
)
from common.product import NAME as PRODUCT_NAME
from common.product import SLUG as PRODUCT_SLUG
from conversations import STORE
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from gate import decide
from llm_client import active_model
from local_files import bind_caller, current_caller, publish, published, reset_caller, slice_local
from model_catalog import fetch_models, select_model
from prompts import (
    DIRECT_SYSTEM_PROMPT,
    append_instructions,
    build_user_message,
    get_system_prompt,
    repo_map_block,
)
from pydantic import BaseModel, ConfigDict, Field, model_serializer
from rag import RagResult, clip_excerpt, retrieval_trace, retrieve_context
from reflect import reflect_answer
from review import review_change
from scope import active_checkout_id, project_root_header, qdrant_collection
from tool_loop import TOOLS_BASE_URL, EventCallback, run_tool_loop
from turns import TURNS, TurnBusy, TurnCancelled
from working_copy import chunks_with_live, merge_hits, overlay_chunks, overlay_hits

AGENT_BEARER_TOKEN = os.getenv("AGENT_BEARER_TOKEN", "")
AGENT_PORT = int(os.getenv("AGENT_PORT", "8090"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("AGENT_REQUEST_TIMEOUT_SECONDS", "14400"))
MAX_CONCURRENT = max(1, int(os.getenv("AGENT_MAX_CONCURRENT", "2")))


class _SlotGate:
    """Reject extra /chat callers instead of queueing them behind the cap."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._used = 0
        self._lock = asyncio.Lock()
        set_chat_slot_limit(limit)

    async def acquire(self) -> None:
        async with self._lock:
            if self._used >= self._limit:
                observe_chat_rejected()
                raise HTTPException(
                    status_code=503, detail="too many concurrent /chat requests"
                )
            self._used += 1
            observe_chat_slots(self._used)

    async def release(self) -> None:
        async with self._lock:
            self._used = max(0, self._used - 1)
            observe_chat_slots(self._used)


_CHAT_SLOTS = _SlotGate(MAX_CONCURRENT)

# One in-flight turn per conversation. The next request waits here, FIFO,
# and only then takes an AGENT_MAX_CONCURRENT slot, so a queued message
# does not hold a slot while the active turn is still writing its trace.
CANCEL_TIMEOUT_SECONDS = 2.0


class _ConversationQueue:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def acquire(self, conversation_id: str) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.get(conversation_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[conversation_id] = lock
        await lock.acquire()
        return lock


_CONVERSATIONS = _ConversationQueue()


@dataclass
class _CancelState:
    request_id: str
    http_client: httpx.AsyncClient
    flag: Optional[asyncio.Event]
    returned: list[dict[str, Any]] = field(default_factory=list)
    nudges: int = 0
    sent: bool = False
    stored: bool = False
    response: Optional["ChatResponse"] = None


_CANCEL_STATE: ContextVar[Optional[_CancelState]] = ContextVar("agent_cancel_state", default=None)
_RAW_EXECUTE_TOOL_CALL = tool_loop.execute_tool_call
_RAW_SUMMARY = tool_loop._summary
_RAW_EMIT = tool_loop._emit
_GUARDS_INSTALLED = False


log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        yield
    finally:
        await app.state.http_client.aclose()


app = FastAPI(title="agent", lifespan=lifespan)
instrument_app(app)


class ChatRequest(BaseModel):
    query: str
    conversation_id: Optional[str] = Field(default=None, max_length=80)
    workspace: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Host path of a registered checkout. Selects its repository index and mount.",
    )
    max_reply_tokens: Optional[int] = Field(
        default=None,
        ge=32,
        le=1024,
        description="Cap the local model's reply. Used by MCP ask so the IDE is not handed an essay.",
    )
    instructions: Optional[str] = Field(
        default=None,
        max_length=4000,
        description="Optional note for this question. It does not replace the built-in conventions.",
    )
    tools: bool = Field(
        default=True,
        description="When false, the model answers from the excerpts only. MCP ask sets this.",
    )


class ReviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    ref_from: str = Field(alias="from")
    ref_to: str = Field(alias="to")


class Source(BaseModel):
    file_path: str
    start_line: int
    end_line: int
    symbol_name: Optional[str] = None


class ToolCallSummary(BaseModel):
    name: str
    arguments: dict[str, Any]
    result_summary: str


class ReflectionInfo(BaseModel):
    removed: int


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    used_context: bool
    tool_calls: list[ToolCallSummary]
    reflection: Optional[ReflectionInfo] = None
    nudges: Optional[int] = None
    conversation_id: str
    route: str = "code"

    @model_serializer(mode="wrap")
    def _omit_zero_nudges(self, handler: Any) -> dict[str, Any]:
        # Same rule as `reflection`: absent from the body when there is nothing to report.
        data: dict[str, Any] = handler(self)
        if not data.get("nudges"):
            data.pop("nudges", None)
        return data


def verify_bearer_token(authorization: Optional[str] = Header(default=None)) -> None:
    """FastAPI dependency enforcing `Authorization: Bearer <AGENT_BEARER_TOKEN>`.

    Kept as a standalone, importable dependency (rather than inlined in the
    route) so it is directly unit-testable in isolation - see
    tests/agent/test_auth.py. Re-reads the env var on every call so tests can
    monkeypatch it freely.
    """
    expected_token = os.getenv("AGENT_BEARER_TOKEN", "")
    # secrets.compare_digest: constant-time comparison (stdlib) instead of `!=`,
    # so a wrong token cannot be guessed byte-by-byte from response timing.
    if (
        not expected_token
        or not authorization
        or not secrets.compare_digest(authorization.encode(), f"Bearer {expected_token}".encode())
    ):
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")


def get_http_client() -> httpx.AsyncClient:
    return app.state.http_client


_UI_PATH = Path(__file__).resolve().parent / "ui" / "index.html"
_UI_CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


class LookupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000)
    workspace: Optional[str] = Field(default=None, max_length=1000)
    include_candidates: bool = False


class LocalFilesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client: str = Field(min_length=1, max_length=40)
    workspace: str = Field(min_length=1, max_length=1000)
    files: dict[str, str]


class ReadCheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500)
    start_line: Optional[int] = Field(default=None, ge=1)
    end_line: Optional[int] = Field(default=None, ge=1)
    workspace: Optional[str] = Field(default=None, max_length=1000)


def _checkout_or_400(raw: Optional[str]) -> Optional[Checkout]:
    if not (raw or "").strip():
        _bind_checkout(None)
        return None
    try:
        return _bind_checkout(raw)
    except UnknownWorkspace as exc:
        raise HTTPException(status_code=400, detail=f"workspace is not registered: {exc}") from exc


@app.post("/local-files", dependencies=[Depends(verify_bearer_token)])
async def put_local_files(payload: LocalFilesRequest) -> dict[str, Any]:
    """Remember this caller's files for one shared checkout. Does not write the tree."""
    try:
        checkout = resolve_workspace(payload.workspace)
    except UnknownWorkspace as exc:
        raise HTTPException(status_code=400, detail=f"workspace is not registered: {exc}") from exc
    try:
        count = publish(checkout.id, payload.client, payload.files)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"client": payload.client, "checkout": checkout.id, "files": count}


@app.post("/lookup", dependencies=[Depends(verify_bearer_token)])
async def lookup(payload: LookupRequest, request: Request) -> dict[str, Any]:
    """Indexed locations only. No chat model."""
    workspace = payload.workspace or request.headers.get("x-workspace")
    checkout = _checkout_or_400(workspace)
    caller = bind_caller(request.headers.get("x-client"))
    request_id = _bind_request()
    try:
        found = await retrieve_context(get_http_client(), payload.query, request_id)
        hits = [
            {
                "file_path": chunk.file_path,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "symbol_name": chunk.symbol_name,
                "excerpt": clip_excerpt(chunk.text),
                "source": "index",
            }
            for chunk in found.chunks
        ]
        hits = await overlay_hits(
            get_http_client(), request_id, checkout, hits, tools_base_url=TOOLS_BASE_URL
        )
        if checkout is not None:
            local = published(checkout.id, current_caller())
            if local:
                hits = merge_hits(hits, local)
    finally:
        reset_caller(caller)
    body: dict[str, Any] = {"hits": hits}
    if payload.include_candidates:
        body["retrieval"] = retrieval_trace(found)
    return body


@app.post("/read", dependencies=[Depends(verify_bearer_token)])
async def read_checkout(payload: ReadCheckoutRequest, request: Request) -> dict[str, Any]:
    """File text from the checkout mount. No chat model."""
    workspace = payload.workspace or request.headers.get("x-workspace")
    checkout = _checkout_or_400(workspace)
    caller = bind_caller(request.headers.get("x-client"))
    try:
        local = published(checkout.id, current_caller()) if checkout is not None else {}
    finally:
        reset_caller(caller)
    rel = payload.path.strip().replace("\\", "/")
    if rel in local:
        window = slice_local(local[rel], payload.start_line, payload.end_line)
        return {"path": rel, **window}
    request_id = _bind_request()
    params: dict[str, Any] = {"path": payload.path}
    if payload.start_line is not None:
        params["start_line"] = payload.start_line
    if payload.end_line is not None:
        params["end_line"] = payload.end_line
    headers = {"X-Request-Id": request_id}
    root = project_root_header.get().strip()
    if root:
        headers["X-Project-Root"] = root
    try:
        response = await get_http_client().get(
            f"{TOOLS_BASE_URL.rstrip('/')}/read-file",
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"tools unreachable: {exc}") from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="tools returned non-JSON") from exc
    if not isinstance(body, dict) or response.status_code >= 400 or not body.get("ok", True):
        detail = body.get("stderr") if isinstance(body, dict) else response.text
        raise HTTPException(status_code=400, detail=str(detail or "read failed")[:500])
    return {
        "path": body.get("path") or payload.path,
        "start_line": body.get("start_line"),
        "end_line": body.get("end_line"),
        "total_lines": body.get("total_lines"),
        "truncated": bool(body.get("truncated")),
        "text": body.get("stdout") or "",
    }


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern="^(build|test|compile)$")
    preset: Optional[str] = Field(default=None, max_length=80)
    path: Optional[str] = Field(default=None, max_length=500)
    filter: Optional[str] = Field(default=None, max_length=200)
    workspace: Optional[str] = Field(default=None, max_length=1000)


class LspOutlineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500)
    preset: Optional[str] = Field(default=None, max_length=80)
    workspace: Optional[str] = Field(default=None, max_length=1000)


class LspHierarchyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500)
    line: int = Field(ge=1)
    character: int = Field(ge=1)
    direction: str = Field(default="both", max_length=8)
    preset: Optional[str] = Field(default=None, max_length=80)
    workspace: Optional[str] = Field(default=None, max_length=1000)


_LOG_LIMIT = 4000


def _clip_log(text: str) -> tuple[str, bool]:
    if len(text) <= _LOG_LIMIT:
        return text, False
    return text[:_LOG_LIMIT] + "\n[agent] log truncated\n", True


def _bounded_tool(payload: dict[str, Any]) -> dict[str, Any]:
    stdout, cut_out = _clip_log(str(payload.get("stdout") or ""))
    stderr, cut_err = _clip_log(str(payload.get("stderr") or ""))
    return {
        "ok": bool(payload.get("ok")),
        "exit_code": payload.get("exit_code"),
        "stdout": stdout,
        "stderr": stderr,
        "truncated": bool(payload.get("truncated")) or cut_out or cut_err,
    }


async def _post_tools(path: str, body: dict[str, Any], request_id: str) -> dict[str, Any]:
    headers = {"X-Request-Id": request_id}
    root = project_root_header.get().strip()
    if root:
        headers["X-Project-Root"] = root
    try:
        response = await get_http_client().post(
            f"{TOOLS_BASE_URL.rstrip('/')}{path}",
            json=body,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"tools unreachable: {exc}") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="tools returned non-JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="tools returned a non-object")
    if response.status_code >= 400:
        detail = payload.get("stderr") or payload.get("detail") or response.text
        raise HTTPException(status_code=400, detail=str(detail)[:500])
    return payload


def _optional_preset(body: dict[str, Any], preset: Optional[str]) -> dict[str, Any]:
    if preset:
        body["preset"] = preset
    return body


@app.post("/check", dependencies=[Depends(verify_bearer_token)])
async def check(payload: CheckRequest, request: Request) -> dict[str, Any]:
    """Bounded build, test, or compile log. No chat model and no opinion."""
    workspace = payload.workspace or request.headers.get("x-workspace")
    _checkout_or_400(workspace)
    request_id = _bind_request()
    body: dict[str, Any] = {}
    _optional_preset(body, payload.preset)
    if payload.action == "build":
        path = "/build"
    elif payload.action == "test":
        path = "/test"
        if payload.filter:
            body["filter"] = payload.filter
    else:
        compile_path = (payload.path or "").strip()
        if not compile_path:
            raise HTTPException(status_code=400, detail="compile requires path")
        path = "/compile"
        body["path"] = compile_path
    result = _bounded_tool(await _post_tools(path, body, request_id))
    result["action"] = payload.action
    return result


@app.post("/lsp/outline", dependencies=[Depends(verify_bearer_token)])
async def lsp_outline(payload: LspOutlineRequest, request: Request) -> dict[str, Any]:
    workspace = payload.workspace or request.headers.get("x-workspace")
    _checkout_or_400(workspace)
    body = _optional_preset({"path": payload.path}, payload.preset)
    return _bounded_tool(await _post_tools("/lsp/outline", body, _bind_request()))


@app.post("/lsp/calls", dependencies=[Depends(verify_bearer_token)])
async def lsp_calls(payload: LspHierarchyRequest, request: Request) -> dict[str, Any]:
    workspace = payload.workspace or request.headers.get("x-workspace")
    _checkout_or_400(workspace)
    body = _optional_preset(
        {
            "path": payload.path,
            "line": payload.line,
            "character": payload.character,
            "direction": payload.direction,
        },
        payload.preset,
    )
    return _bounded_tool(await _post_tools("/lsp/calls", body, _bind_request()))


@app.post("/lsp/types", dependencies=[Depends(verify_bearer_token)])
async def lsp_types(payload: LspHierarchyRequest, request: Request) -> dict[str, Any]:
    workspace = payload.workspace or request.headers.get("x-workspace")
    _checkout_or_400(workspace)
    body = _optional_preset(
        {
            "path": payload.path,
            "line": payload.line,
            "character": payload.character,
            "direction": payload.direction,
        },
        payload.preset,
    )
    return _bounded_tool(await _post_tools("/lsp/types", body, _bind_request()))


class ModelSelectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=300)


@app.get("/models", dependencies=[Depends(verify_bearer_token)])
async def list_models(http_client: Annotated[httpx.AsyncClient, Depends(get_http_client)]) -> dict[str, Any]:
    try:
        models = await fetch_models(http_client)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="language model router is unreachable") from exc
    return {"models": models, "active": active_model()}


@app.post("/models/select", dependencies=[Depends(verify_bearer_token)])
async def choose_model(
    body: ModelSelectRequest,
    http_client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> dict[str, Any]:
    try:
        chosen = await select_model(http_client, body.model)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=f"unknown model: {exc}") from exc
    except (RuntimeError, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="language model router rejected the swap") from exc
    return {"model": chosen}


@app.get("/ui")
async def ui_page() -> HTMLResponse:
    """Chat page. No bearer: the token is typed into the page and sent only on /chat/stream."""
    html = (
        _UI_PATH.read_text(encoding="utf-8")
        .replace("{{PRODUCT_NAME}}", PRODUCT_NAME)
        .replace("{{PRODUCT_SLUG}}", PRODUCT_SLUG)
    )
    return HTMLResponse(
        html,
        headers={"Content-Security-Policy": _UI_CSP, "Cache-Control": "no-cache"},
    )


def _bind_checkout(raw: Optional[str]) -> Optional[Checkout]:
    """Select the repository collection for this task. Empty keeps the default mount."""
    if not (raw or "").strip():
        project_root_header.set("")
        qdrant_collection.set("")
        active_checkout_id.set("")
        return None
    checkout = resolve_workspace(raw or "")
    project_root_header.set(checkout.mount)
    qdrant_collection.set(checkout.collection)
    active_checkout_id.set(checkout.id)
    log.info(
        "workspace_selected",
        checkout=checkout.id,
        repo=checkout.repo_id,
        collection=checkout.collection,
        vcs=checkout.vcs,
    )
    return checkout


async def execute_turn(
    payload: ChatRequest,
    request_id: str,
    http_client: httpx.AsyncClient,
    *,
    on_event: Optional[EventCallback] = None,
    stream_tokens: bool = False,
    cancel_event: Optional[asyncio.Event] = None,
) -> ChatResponse:
    checkout = _bind_checkout(payload.workspace)
    conversation_id = payload.conversation_id or str(uuid.uuid4())
    STORE.touch(conversation_id)
    history = STORE.history(conversation_id)
    state = _CancelState(request_id=request_id, http_client=http_client, flag=cancel_event)
    token = _CANCEL_STATE.set(state)

    started = time.perf_counter()
    route = "unknown"
    outcome = "error"
    iterations = 0
    tool_count = 0

    async def stop() -> ChatResponse:
        nonlocal outcome, tool_count
        outcome = "cancelled"
        tool_count = len(state.returned)
        log.info("chat_cancelled", conversation_id=conversation_id)
        return await _stopped_response(
            conversation_id=conversation_id,
            query=payload.query,
            request_id=request_id,
            http_client=http_client,
            calls=list(state.returned),
            nudges=state.nudges,
            route=route if route != "unknown" else "code",
            state=state,
        )

    try:
        if cancel_event is not None and cancel_event.is_set():
            return await stop()
        decision = await decide(http_client, payload.query, request_id, has_history=bool(history))
        route = decision.route
        outcome = "ok"
        log.info("gate_decision", route=decision.route, by=decision.by, reason=decision.reason)
        if on_event is not None:
            await on_event("route", {"route": decision.route, "by": decision.by})

        if decision.route == "direct":
            rag_result = RagResult(used_context=False, context_text="", sources=[])
            system_prompt = append_instructions(DIRECT_SYSTEM_PROMPT, payload.instructions)
            user_message = payload.query
            enable_tools = False
        else:
            retrieval_started = time.perf_counter()
            rag_result = await retrieve_context(http_client, payload.query, request_id)
            retrieval_seconds = time.perf_counter() - retrieval_started
            reranker_used = rag_result.reranker_used
            if checkout is not None and rag_result.chunks:
                context_text, sources, chunks = await overlay_chunks(
                    http_client,
                    request_id,
                    checkout,
                    rag_result.chunks,
                    tools_base_url=TOOLS_BASE_URL,
                )
                rag_result = RagResult(
                    used_context=bool(chunks),
                    context_text=context_text,
                    sources=sources,
                    chunks=chunks,
                    reranker_used=reranker_used,
                )
            local = published(checkout.id, current_caller()) if checkout is not None else {}
            if local:
                context_text, sources, chunks = chunks_with_live(rag_result.chunks, local)
                rag_result = RagResult(
                    used_context=bool(chunks),
                    context_text=context_text,
                    sources=sources,
                    chunks=chunks,
                    reranker_used=reranker_used,
                )
            observe_rag(rag_result.used_context)
            observe_rag_retrieval(retrieval_seconds, len(rag_result.sources), reranker_used)
            log.info("rag_complete", used_context=rag_result.used_context, num_sources=len(rag_result.sources))
            if on_event is not None:
                await on_event(
                    "rag",
                    {
                        "used_context": rag_result.used_context,
                        "num_sources": len(rag_result.sources),
                        "sources": rag_result.sources,
                    },
                )
            system_prompt = append_instructions(
                get_system_prompt(source["file_path"] for source in rag_result.sources),
                payload.instructions,
            )
            user_message = build_user_message(payload.query, rag_result.context_text, rag_result.used_context)
            if checkout is not None:
                note = await fetch_local_note(
                    http_client, request_id, checkout, tools_base_url=TOOLS_BASE_URL
                )
                user_message = f"{user_message}\n\n{note}"
            sketch = await asyncio.to_thread(repo_map_block, payload.query)
            if sketch:
                user_message = f"{user_message}\n\n{sketch}"
            named = named_file_block(payload.query)
            if named:
                user_message = f"{user_message}\n\n{named}"
            enable_tools = payload.tools

        try:
            loop_result = await run_tool_loop(
                http_client,
                system_prompt,
                user_message,
                request_id,
                history=history,
                on_event=on_event,
                stream_tokens=stream_tokens,
                enable_tools=enable_tools,
                cancel_event=cancel_event,
                max_tokens=payload.max_reply_tokens,
            )
        except (TurnCancelled, asyncio.CancelledError):
            return await stop()
        outcome = str(loop_result.get("outcome") or "ok")
        iterations = int(loop_result.get("iterations") or 0)
        calls = list(loop_result["tool_calls"])
        tool_count = len(calls)
        reflected = await reflect_answer(http_client, loop_result["answer"], rag_result.sources, request_id)
        observe_reflection(int(reflected.get("removed") or 0))
        # Every prose the loop returns is stored with the calls that came back:
        # accepted answer, nudge ceiling, plain completion, the fixed sentence,
        # and the unreachable / context-window errors.
        STORE.remember(conversation_id, payload.query, reflected["answer"], calls)
        log.info(
            "chat_request_complete",
            num_tool_calls=tool_count,
            reflected=reflected["removed"],
            conversation_id=conversation_id,
        )

        return ChatResponse(
            answer=reflected["answer"],
            sources=[Source(**source) for source in rag_result.sources],
            used_context=rag_result.used_context,
            tool_calls=_public_tool_calls(calls),
            reflection=ReflectionInfo(removed=reflected["removed"]) if reflected["removed"] else None,
            nudges=int(loop_result.get("nudges") or 0) or None,
            conversation_id=conversation_id,
            route=decision.route,
        )
    except (TurnCancelled, asyncio.CancelledError):
        return await stop()
    except Exception:
        outcome = "error"
        raise
    finally:
        observe_turn(route, outcome, time.perf_counter() - started, iterations, tool_count)
        _CANCEL_STATE.reset(token)


def _detach(task: asyncio.Future[Any]) -> None:
    """Drop a tool HTTP without waiting for it. The result is not a returned call."""

    def _done(fut: asyncio.Future[Any]) -> None:
        if not fut.cancelled():
            fut.exception()

    if task.done():
        _done(task)
        return
    task.add_done_callback(_done)


async def _post_cancel(http_client: httpx.AsyncClient, request_id: str) -> None:
    """POST /cancel on tools. Do not wait longer than two seconds."""

    try:
        await http_client.post(
            f"{TOOLS_BASE_URL.rstrip('/')}/cancel",
            headers={"X-Request-Id": request_id},
            timeout=CANCEL_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001 - a missed 204 must not hide the Stopped answer
        log.info("backend_cancel_failed", request_id=request_id, target=TOOLS_BASE_URL)


async def _post_cancel_once(state: _CancelState) -> None:
    if state.sent:
        return
    state.sent = True
    await _post_cancel(state.http_client, state.request_id)


def _recording_summary(name: str, arguments: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    summary = _RAW_SUMMARY(name, arguments, result)
    state = _CANCEL_STATE.get()
    if state is not None:
        state.returned.append(summary)
    return summary


async def _recording_emit(
    on_event: Optional[EventCallback], name: str, payload: dict[str, Any]
) -> None:
    state = _CANCEL_STATE.get()
    if state is not None and name == "nudge":
        state.nudges += 1
    await _RAW_EMIT(on_event, name, payload)


async def _guarded_execute_tool_call(
    http_client: httpx.AsyncClient,
    name: str,
    arguments: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    """Run one tool call, or abandon it when the turn is cancelled.

    POST /cancel is sent before the tool HTTP is dropped, and that HTTP is not awaited.
    """
    state = _CANCEL_STATE.get()
    if state is None or state.flag is None:
        return await _RAW_EXECUTE_TOOL_CALL(http_client, name, arguments, request_id)
    if state.flag.is_set():
        await _post_cancel_once(state)
        raise TurnCancelled()
    call = asyncio.ensure_future(_RAW_EXECUTE_TOOL_CALL(http_client, name, arguments, request_id))
    waiter = asyncio.create_task(state.flag.wait())
    try:
        done, _pending = await asyncio.wait({call, waiter}, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        if not call.done():
            await _post_cancel_once(state)
            call.cancel()
            _detach(call)
        if not waiter.done():
            waiter.cancel()
            _detach(waiter)
        raise TurnCancelled() from None
    if call in done:
        if not waiter.done():
            waiter.cancel()
            _detach(waiter)
        return call.result()
    if not waiter.done():
        waiter.cancel()
        _detach(waiter)
    await _post_cancel_once(state)
    if not call.done():
        call.cancel()
        _detach(call)
    raise TurnCancelled()


def _install_cancel_guards() -> None:
    global _GUARDS_INSTALLED
    if _GUARDS_INSTALLED:
        return
    tool_loop.execute_tool_call = _guarded_execute_tool_call
    tool_loop._summary = _recording_summary
    tool_loop._emit = _recording_emit
    _GUARDS_INSTALLED = True


_install_cancel_guards()


def _public_tool_calls(calls: list[dict[str, Any]]) -> list[ToolCallSummary]:
    return [ToolCallSummary(**call) for call in calls]


async def _stopped_response(
    *,
    conversation_id: str,
    query: str,
    request_id: str,
    http_client: httpx.AsyncClient,
    calls: list[dict[str, Any]],
    nudges: int,
    route: str,
    state: Optional[_CancelState] = None,
) -> ChatResponse:
    """Write Stopped. with the calls that already returned, and cancel backends once.

    The trace is stored before the POST so a second cancel cannot drop it.
    The in-flight tool HTTP is already abandoned by the guard, which sent POST /cancel first.
    """
    if state is not None and state.response is not None:
        return state.response
    if state is None or not state.stored:
        STORE.remember(conversation_id, query, "Stopped.", calls)
        if state is not None:
            state.stored = True
    response = ChatResponse(
        answer="Stopped.",
        sources=[],
        used_context=False,
        tool_calls=_public_tool_calls(calls),
        conversation_id=conversation_id,
        route=route,
        nudges=nudges or None,
    )
    if state is not None:
        state.response = response
    try:
        if state is not None:
            await _post_cancel_once(state)
        else:
            await _post_cancel(http_client, request_id)
    except asyncio.CancelledError:
        return response
    return response


def _bind_request() -> str:
    request_id = str(uuid.uuid4())
    clear_request_context()
    bind_request_id(request_id)
    return request_id


def _with_workspace_header(payload: ChatRequest, x_workspace: Optional[str]) -> ChatRequest:
    header = (x_workspace or "").strip()
    if (payload.workspace or "").strip() or not header:
        return payload
    return payload.model_copy(update={"workspace": header})


async def _claim_turn(conversation_id: str) -> asyncio.Event:
    """Wait out a busy conversation before taking a concurrency slot, then register it.

    The same id does not receive 409. A distinct conversation still receives
    503 when every slot is taken. Cancelling this wait starts nothing.
    """
    while True:
        await TURNS.wait_until_free(conversation_id)
        await _CHAT_SLOTS.acquire()
        try:
            return TURNS.start(conversation_id)
        except TurnBusy:
            await _CHAT_SLOTS.release()


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(verify_bearer_token)])
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    payload = _with_workspace_header(payload, request.headers.get("x-workspace"))
    caller = bind_caller(request.headers.get("x-client"))
    request_id = _bind_request()
    log.info("chat_request_received", query_len=len(payload.query), stream=False)

    conversation_id = payload.conversation_id or str(uuid.uuid4())
    payload = payload.model_copy(update={"conversation_id": conversation_id})

    # Wait for the active turn's remember before taking a concurrency slot.
    # Cancelling this wait writes nothing and leaves the active turn running.
    lock = await _CONVERSATIONS.acquire(conversation_id)
    started = False
    try:
        flag = await _claim_turn(conversation_id)
        started = True
        return await execute_turn(
            payload, request_id, get_http_client(), cancel_event=flag
        )
    except UnknownWorkspace as exc:
        raise HTTPException(status_code=400, detail=f"workspace is not registered: {exc}") from exc
    finally:
        if started:
            TURNS.finish(conversation_id)
            await asyncio.shield(_CHAT_SLOTS.release())
        lock.release()
        reset_caller(caller)


@app.post("/review", dependencies=[Depends(verify_bearer_token)])
async def review(payload: ReviewRequest) -> dict[str, Any]:
    """One comment list for the diff `from`..`to`. Same concurrency cap as /chat."""
    request_id = _bind_request()
    await _CHAT_SLOTS.acquire()
    try:
        return await review_change(get_http_client(), payload.ref_from, payload.ref_to, request_id)
    finally:
        await _CHAT_SLOTS.release()


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str = Field(min_length=1, max_length=80)


@app.post("/chat/cancel", dependencies=[Depends(verify_bearer_token)])
async def cancel_chat(body: CancelRequest) -> dict[str, bool]:
    """Stop the in-flight turn for this conversation, including the model call."""
    return {"stopped": TURNS.cancel(body.conversation_id)}


@app.post("/chat/stream", dependencies=[Depends(verify_bearer_token)])
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    payload = _with_workspace_header(payload, request.headers.get("x-workspace"))
    caller = bind_caller(request.headers.get("x-client"))
    if (payload.workspace or "").strip():
        try:
            resolve_workspace(payload.workspace or "")
        except UnknownWorkspace as exc:
            raise HTTPException(status_code=400, detail=f"workspace is not registered: {exc}") from exc
    request_id = _bind_request()
    log.info("chat_request_received", query_len=len(payload.query), stream=True)

    conversation_id = payload.conversation_id or str(uuid.uuid4())
    payload = payload.model_copy(update={"conversation_id": conversation_id})
    lock = await _CONVERSATIONS.acquire(conversation_id)
    try:
        flag = await _claim_turn(conversation_id)
    except BaseException:
        lock.release()
        raise
    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

    async def on_event(name: str, data: dict[str, Any]) -> None:
        await queue.put((name, data))

    async def run() -> None:
        finished = False
        try:
            task = asyncio.current_task()
            if task is not None:
                TURNS.bind_task(conversation_id, task)
            await queue.put(("meta", {"conversation_id": conversation_id}))
            result = await execute_turn(
                payload,
                request_id,
                get_http_client(),
                on_event=on_event,
                stream_tokens=True,
                cancel_event=flag,
            )
            finished = True
            await queue.put(("done", result.model_dump()))
        except HTTPException as exc:
            await queue.put(("error", {"message": str(exc.detail)}))
        except asyncio.CancelledError:
            # execute_turn already stored Stopped. when the turn itself was cancelled.
            # This branch is a cancel before that turn started writing.
            if not finished:
                stopped = await _stopped_response(
                    conversation_id=conversation_id,
                    query=payload.query,
                    request_id=request_id,
                    http_client=get_http_client(),
                    calls=[],
                    nudges=0,
                    route="code",
                )
                await queue.put(("done", stopped.model_dump()))
            raise
        except Exception as exc:  # noqa: BLE001 - surface as an SSE error event
            log.error("chat_stream_failed", error=str(exc))
            await queue.put(("error", {"message": str(exc)}))
        finally:
            TURNS.finish(conversation_id)
            await asyncio.shield(_CHAT_SLOTS.release())
            lock.release()
            await queue.put(None)

    run_task = asyncio.create_task(run())
    reset_caller(caller)

    async def generate():  # noqa: ANN202 - StreamingResponse accepts an async iterator of bytes/str
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, data = item
                yield f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
        finally:
            if not run_task.done():
                TURNS.cancel(conversation_id)
                run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
