"""Clients for the `llm` and `embedder` services (both OpenAI-compatible
`llama-server` endpoints) built on the official `openai` SDK.

Why the SDK rather than hand-rolled `httpx.post` + JSON parsing: retries with
backoff on connection errors / 5xx / 429, typed responses (`tool_calls`,
`embedding`), and streaming for free later. The SDK is pinned to the 2.x line
because 3.x moved to `httpx2`, which would mean two HTTP stacks in this
service (FastAPI's TestClient, the `tools` and Qdrant calls and every test
transport are `httpx` 0.x).

The SDK is handed the app-wide `httpx.AsyncClient`, so tests keep mocking a
single transport and connection pooling is shared with the other outbound
calls. `X-Request-Id` is forwarded on every request via `extra_headers`.
"""

from __future__ import annotations

import os
import weakref
from collections.abc import Awaitable, Callable
from typing import Any, Optional

import httpx
from openai import AsyncOpenAI

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://llm:8080")
EMBEDDER_BASE_URL = os.getenv("EMBEDDER_BASE_URL", "http://embedder:8081")
REQUEST_TIMEOUT_SECONDS = float(os.getenv("AGENT_REQUEST_TIMEOUT_SECONDS", "14400"))
# SDK retries (exponential backoff) on connection errors, 408/409/429/5xx.
MAX_RETRIES = int(os.getenv("AGENT_LLM_MAX_RETRIES", "2"))

DEFAULT_MODEL = "local"  # used only when no GGUF name is configured
_configured = os.path.basename(os.getenv("LLM_MODEL_PATH", ""))
if _configured.lower().endswith(".gguf"):
    _configured = _configured[:-5]
_active_model = _configured or DEFAULT_MODEL


def active_model() -> str:
    return _active_model


def set_active_model(name: str) -> None:
    global _active_model
    _active_model = name or DEFAULT_MODEL


EMBEDDING_MODEL = "nomic-embed-text-v1.5"

# One SDK client per (httpx client, base URL); the SDK object is cheap but
# not free, and it must wrap the *same* httpx.AsyncClient the app owns.
_clients: "weakref.WeakKeyDictionary[httpx.AsyncClient, dict[str, AsyncOpenAI]]" = weakref.WeakKeyDictionary()


def sdk_client(http_client: httpx.AsyncClient, base_url: str) -> AsyncOpenAI:
    per_http = _clients.setdefault(http_client, {})
    client = per_http.get(base_url)
    if client is None:
        client = AsyncOpenAI(
            base_url=f"{base_url.rstrip('/')}/v1",
            api_key="local",  # llama-server ignores it; the SDK requires a value
            http_client=http_client,
            max_retries=MAX_RETRIES,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        per_http[base_url] = client
    return client


async def count_tokens(http_client: httpx.AsyncClient, text: str, request_id: str) -> int:
    """Exact count via llama-server `POST /tokenize`, else `len(text) // 4`."""
    try:
        resp = await http_client.post(
            f"{LLM_BASE_URL.rstrip('/')}/tokenize",
            json={"content": text, "model": active_model()},
            headers={"X-Request-Id": request_id},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        tokens = resp.json().get("tokens")
        if isinstance(tokens, list):
            return len(tokens)
    except Exception:  # noqa: BLE001 - budget must degrade, not fail the turn
        pass
    return max(1, (len(text) + 3) // 4)


async def chat_completion(
    http_client: httpx.AsyncClient,
    messages: list[dict[str, Any]],
    request_id: str,
    tools: Optional[list[dict[str, Any]]] = None,
    tool_choice: str = "auto",
    temperature: float = 0.2,
    response_format: Optional[dict[str, Any]] = None,
    max_tokens: Optional[int] = None,
) -> dict[str, Any]:
    """POST {LLM_BASE_URL}/v1/chat/completions; returns the completion as a plain dict.

    Raises (`openai.APIError` subclasses) on transport/HTTP errors after the
    SDK's retries - callers (tool_loop.py, main.py) catch and degrade.
    """
    extra: dict[str, Any] = {}
    if tools:
        extra["tools"] = tools
        extra["tool_choice"] = tool_choice
    if response_format:
        extra["response_format"] = response_format
    if max_tokens is not None:
        extra["max_tokens"] = max_tokens
    completion = await sdk_client(http_client, LLM_BASE_URL).chat.completions.create(
        model=active_model(),
        messages=messages,  # type: ignore[arg-type]  # plain dicts are accepted at runtime
        temperature=temperature,
        extra_headers={"X-Request-Id": request_id},
        **extra,
    )
    return completion.model_dump()


def assemble_stream_message(deltas: list[Any]) -> dict[str, Any]:
    """Fold streamed chat deltas into one assistant message dict.

    `deltas` are OpenAI-like objects (`content`, `tool_calls` with `index`).
    Used by `chat_completion_stream` and unit-tested without the SDK.
    """
    content_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    for delta in deltas:
        text = getattr(delta, "content", None)
        if text:
            content_parts.append(text)
        for call in getattr(delta, "tool_calls", None) or []:
            index = int(getattr(call, "index", 0) or 0)
            slot = tool_calls.setdefault(
                index,
                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
            )
            call_id = getattr(call, "id", None)
            if call_id:
                slot["id"] = call_id
            function = getattr(call, "function", None)
            if function is not None:
                name = getattr(function, "name", None)
                if name:
                    slot["function"]["name"] += name
                arguments = getattr(function, "arguments", None)
                if arguments:
                    slot["function"]["arguments"] += arguments
    message: dict[str, Any] = {"role": "assistant", "content": "".join(content_parts) or None}
    if tool_calls:
        message["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
    else:
        message["tool_calls"] = []
    return message


def tool_delta_snapshot(delta: Any, slots: dict[int, dict[str, str]]) -> list[dict[str, Any]]:
    """Fold one streamed tool-call delta into `slots`. Return the slots that grew.

    Each item is `{index, name, arguments}` with the text accumulated so far,
    so a UI can show the call while the model is still writing it.
    """
    grown: list[dict[str, Any]] = []
    for call in getattr(delta, "tool_calls", None) or []:
        index = int(getattr(call, "index", 0) or 0)
        slot = slots.setdefault(index, {"name": "", "arguments": ""})
        function = getattr(call, "function", None)
        changed = False
        if function is not None:
            name = getattr(function, "name", None)
            if name:
                slot["name"] += name
                changed = True
            arguments = getattr(function, "arguments", None)
            if arguments:
                slot["arguments"] += arguments
                changed = True
        if changed:
            grown.append({"index": index, "name": slot["name"], "arguments": slot["arguments"]})
    return grown


async def chat_completion_stream(
    http_client: httpx.AsyncClient,
    messages: list[dict[str, Any]],
    request_id: str,
    tools: Optional[list[dict[str, Any]]] = None,
    tool_choice: str = "auto",
    temperature: float = 0.2,
    on_token: Optional[Callable[[str], Awaitable[None]]] = None,
    on_tool_delta: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None,
    max_tokens: Optional[int] = None,
) -> dict[str, Any]:
    """Same as `chat_completion` but `stream=true`; optional per-token callback."""
    extra: dict[str, Any] = {"stream": True}
    if tools:
        extra["tools"] = tools
        extra["tool_choice"] = tool_choice
    if max_tokens is not None:
        extra["max_tokens"] = max_tokens
    stream = await sdk_client(http_client, LLM_BASE_URL).chat.completions.create(
        model=active_model(),
        messages=messages,  # type: ignore[arg-type]
        temperature=temperature,
        extra_headers={"X-Request-Id": request_id},
        **extra,
    )
    deltas: list[Any] = []
    tool_slots: dict[int, dict[str, str]] = {}
    finish: str | None = None
    async for chunk in stream:  # type: ignore[attr-defined]  # stream=True → AsyncStream
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        reason = getattr(choices[0], "finish_reason", None)
        if isinstance(reason, str):
            finish = reason
        delta = choices[0].delta
        deltas.append(delta)
        text = getattr(delta, "content", None)
        if text and on_token is not None:
            await on_token(text)
        if on_tool_delta is not None:
            for snapshot in tool_delta_snapshot(delta, tool_slots):
                await on_tool_delta(snapshot)
    return {"choices": [{"finish_reason": finish, "message": assemble_stream_message(deltas)}]}


_API_MESSAGE_KEYS = ("role", "content", "tool_calls", "tool_call_id")


def api_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop agent-only keys such as `name` before the chat completion request."""
    return [{key: message[key] for key in _API_MESSAGE_KEYS if key in message} for message in messages]


def finish_reason(completion: dict[str, Any]) -> str | None:
    """`length` when the reply was cut. Missing on servers that do not send it."""
    try:
        reason = completion["choices"][0].get("finish_reason")
    except (KeyError, IndexError, AttributeError, TypeError):
        return None
    return reason if isinstance(reason, str) else None


async def embed_query(http_client: httpx.AsyncClient, text: str, request_id: str) -> list[float]:
    """POST {EMBEDDER_BASE_URL}/v1/embeddings for one already-prefixed input.

    Raises on failure; `rag.py` turns that into "no context".
    """
    response = await sdk_client(http_client, EMBEDDER_BASE_URL).embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
        encoding_format="float",  # explicit: don't rely on the SDK's base64 default with llama-server
        extra_headers={"X-Request-Id": request_id},
    )
    return list(response.data[0].embedding)


def extract_message(completion: dict[str, Any]) -> dict[str, Any]:
    """Pulls the first choice's message object out of a chat completion response."""
    return completion["choices"][0]["message"]
