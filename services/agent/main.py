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
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Optional

import httpx
from checkouts import Checkout, UnknownWorkspace, fetch_local_note, resolve_workspace
from cited_files import named_file_block
from common.logging import bind_request_id, clear_request_context, get_logger
from common.metrics import instrument_app, observe_rag
from conversations import STORE
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from gate import decide
from llm_client import active_model
from model_catalog import fetch_models, select_model
from prompts import (
    DIRECT_SYSTEM_PROMPT,
    append_instructions,
    build_user_message,
    get_system_prompt,
    repo_map_block,
)
from pydantic import BaseModel, ConfigDict, Field
from rag import RagResult, clip_excerpt, retrieval_trace, retrieve_context
from reflect import reflect_answer
from review import review_change
from scope import project_root_header, qdrant_collection
from tool_loop import TOOLS_BASE_URL, EventCallback, run_tool_loop
from turns import TURNS, TurnCancelled
from working_copy import overlay_chunks, overlay_hits

AGENT_BEARER_TOKEN = os.getenv("AGENT_BEARER_TOKEN", "")
AGENT_PORT = int(os.getenv("AGENT_PORT", "8090"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("AGENT_REQUEST_TIMEOUT_SECONDS", "14400"))
MAX_CONCURRENT = max(1, int(os.getenv("AGENT_MAX_CONCURRENT", "2")))
_CHAT_SLOTS = asyncio.Semaphore(MAX_CONCURRENT)


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
    conversation_id: Optional[str] = None
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
    conversation_id: str
    route: str = "code"


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


@app.post("/lookup", dependencies=[Depends(verify_bearer_token)])
async def lookup(payload: LookupRequest, request: Request) -> dict[str, Any]:
    """Indexed locations only. No chat model."""
    workspace = payload.workspace or request.headers.get("x-workspace")
    checkout = _checkout_or_400(workspace)
    request_id = _bind_request()
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
    body: dict[str, Any] = {"hits": hits}
    if payload.include_candidates:
        body["retrieval"] = retrieval_trace(found)
    return body


@app.post("/read", dependencies=[Depends(verify_bearer_token)])
async def read_checkout(payload: ReadCheckoutRequest, request: Request) -> dict[str, Any]:
    """File text from the checkout mount. No chat model."""
    workspace = payload.workspace or request.headers.get("x-workspace")
    _checkout_or_400(workspace)
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
async def ui_page() -> FileResponse:
    """Chat page. No bearer: the token is typed into the page and sent only on /chat/stream."""
    return FileResponse(
        _UI_PATH,
        media_type="text/html; charset=utf-8",
        headers={"Content-Security-Policy": _UI_CSP, "Cache-Control": "no-cache"},
    )


def _bind_checkout(raw: Optional[str]) -> Optional[Checkout]:
    """Select the repository collection for this task. Empty keeps the default mount."""
    if not (raw or "").strip():
        project_root_header.set("")
        qdrant_collection.set("")
        return None
    checkout = resolve_workspace(raw or "")
    project_root_header.set(checkout.mount)
    qdrant_collection.set(checkout.collection)
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
    history = STORE.history(conversation_id)

    decision = await decide(http_client, payload.query, request_id, has_history=bool(history))
    log.info("gate_decision", route=decision.route, by=decision.by, reason=decision.reason)
    if on_event is not None:
        await on_event("route", {"route": decision.route, "by": decision.by})

    if decision.route == "direct":
        rag_result = RagResult(used_context=False, context_text="", sources=[])
        system_prompt = append_instructions(DIRECT_SYSTEM_PROMPT, payload.instructions)
        user_message = payload.query
        enable_tools = False
    else:
        rag_result = await retrieve_context(http_client, payload.query, request_id)
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
            )
        observe_rag(rag_result.used_context)
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
    except TurnCancelled:
        log.info("chat_cancelled", conversation_id=conversation_id)
        return ChatResponse(
            answer="Stopped.",
            sources=[],
            used_context=False,
            tool_calls=[],
            conversation_id=conversation_id,
            route=decision.route,
        )
    reflected = await reflect_answer(http_client, loop_result["answer"], rag_result.sources, request_id)
    STORE.remember(conversation_id, payload.query, reflected["answer"])
    log.info(
        "chat_request_complete",
        num_tool_calls=len(loop_result["tool_calls"]),
        reflected=reflected["removed"],
        conversation_id=conversation_id,
    )

    return ChatResponse(
        answer=reflected["answer"],
        sources=[Source(**source) for source in rag_result.sources],
        used_context=rag_result.used_context,
        tool_calls=[ToolCallSummary(**call) for call in loop_result["tool_calls"]],
        reflection=ReflectionInfo(removed=reflected["removed"]) if reflected["removed"] else None,
        conversation_id=conversation_id,
        route=decision.route,
    )


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


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(verify_bearer_token)])
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    payload = _with_workspace_header(payload, request.headers.get("x-workspace"))
    request_id = _bind_request()
    log.info("chat_request_received", query_len=len(payload.query), stream=False)

    if _CHAT_SLOTS.locked():
        raise HTTPException(status_code=503, detail="too many concurrent /chat requests")

    async with _CHAT_SLOTS:
        try:
            return await execute_turn(payload, request_id, get_http_client())
        except UnknownWorkspace as exc:
            raise HTTPException(status_code=400, detail=f"workspace is not registered: {exc}") from exc


@app.post("/review", dependencies=[Depends(verify_bearer_token)])
async def review(payload: ReviewRequest) -> dict[str, Any]:
    """One comment list for the diff `from`..`to`. Same concurrency cap as /chat."""
    request_id = _bind_request()
    if _CHAT_SLOTS.locked():
        raise HTTPException(status_code=503, detail="too many concurrent /chat requests")
    async with _CHAT_SLOTS:
        return await review_change(get_http_client(), payload.ref_from, payload.ref_to, request_id)


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
    if (payload.workspace or "").strip():
        try:
            resolve_workspace(payload.workspace or "")
        except UnknownWorkspace as exc:
            raise HTTPException(status_code=400, detail=f"workspace is not registered: {exc}") from exc
    request_id = _bind_request()
    log.info("chat_request_received", query_len=len(payload.query), stream=True)

    if _CHAT_SLOTS.locked():
        raise HTTPException(status_code=503, detail="too many concurrent /chat requests")

    conversation_id = payload.conversation_id or str(uuid.uuid4())
    payload = payload.model_copy(update={"conversation_id": conversation_id})
    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

    async def on_event(name: str, data: dict[str, Any]) -> None:
        await queue.put((name, data))

    async def run() -> None:
        flag = TURNS.start(conversation_id)
        task = asyncio.current_task()
        if task is not None:
            TURNS.bind_task(conversation_id, task)
        try:
            async with _CHAT_SLOTS:
                await queue.put(("meta", {"conversation_id": conversation_id}))
                result = await execute_turn(
                    payload,
                    request_id,
                    get_http_client(),
                    on_event=on_event,
                    stream_tokens=True,
                    cancel_event=flag,
                )
                await queue.put(("done", result.model_dump()))
        except asyncio.CancelledError:
            # Same shape as a normal `done` so every client parses it the same way.
            stopped = ChatResponse(
                answer="Stopped.",
                sources=[],
                used_context=False,
                tool_calls=[],
                conversation_id=conversation_id,
            )
            await queue.put(("done", stopped.model_dump()))
            raise
        except Exception as exc:  # noqa: BLE001 - surface as an SSE error event
            log.error("chat_stream_failed", error=str(exc))
            await queue.put(("error", {"message": str(exc)}))
        finally:
            TURNS.finish(conversation_id)
            await queue.put(None)

    async def generate():  # noqa: ANN202 - StreamingResponse accepts an async iterator of bytes/str
        task = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, data = item
                yield f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
        finally:
            if not task.done():
                TURNS.cancel(conversation_id)
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
