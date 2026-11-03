"""Bounded tool-calling loop between the agent and the `llm` + `tools` services.

Each iteration sends the conversation (with the tool schemas attached) to the
LLM. If it responds with tool call(s), each is executed against the `tools`
service and the result is fed back as a `tool` message; if it responds with
plain text and no tool calls, that's the final answer.

Malformed tool-call handling (invalid JSON arguments, an unknown function
name, or arguments rejected by the tool's pydantic model): retried ONCE with
a corrective system instruction that quotes the exact validation problem. If
the retry is also malformed, tool-calling is abandoned for this turn and a
plain-text-only completion is requested instead, so the request always
returns *something* rather than crashing or hanging.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from typing import Any, Optional

import httpx
from common.logging import get_logger
from common.metrics import observe_malformed, observe_tool
from llm_client import chat_completion, chat_completion_stream, extract_message
from pydantic import ValidationError
from token_budget import apply_budget, safe_budget
from tools_schema import TOOL_ENDPOINT_MAP, TOOL_SPECS_BY_NAME, TOOLS_OPENAI_SCHEMA
from turns import TurnCancelled, until_cancelled

TOOLS_BASE_URL = os.getenv("TOOLS_BASE_URL", "http://tools:8091")
PATCH_BASE_URL = os.getenv("PATCH_BASE_URL", "http://patch:8093")
_PATCH_TOOLS = frozenset({"begin_plan", "edit", "apply"})
REQUEST_TIMEOUT_SECONDS = float(os.getenv("AGENT_REQUEST_TIMEOUT_SECONDS", "14400"))
MAX_ITERATIONS_DEFAULT = int(os.getenv("AGENT_TOOL_LOOP_MAX_ITERATIONS", "8"))

MALFORMED_RETRY_NOTE = (
    "Your previous tool call was malformed. Respond again, either with a "
    "valid tool call that matches one of the provided function schemas "
    "exactly, or with a plain-text final answer if no tool call is needed."
)

TOOLS_UNAVAILABLE_NOTE = (
    "Tool calling is unavailable for this turn. Answer the user's original "
    "question as best you can in plain text, and say that you were unable "
    "to verify it with tools."
)

REPEAT_NOTE = (
    "You called the same tool again with the same arguments. "
    "Do not call tools. Answer from the results you already have."
)


log = get_logger("tool_loop")


def _log(event: str, request_id: str, **fields: Any) -> None:
    log.info(event, request_id=request_id, **fields)


class MalformedToolCall(ValueError):
    """The LLM's tool call cannot be executed; `str(exc)` is written for the model."""


def _parse_tool_call(tool_call: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Returns (call_id, name, arguments), or raises MalformedToolCall.

    Arguments are validated against the tool's pydantic model (the same class
    the `tools` service validates the HTTP request with, see
    common/tool_models.py), so unknown tools, invalid JSON, wrong types,
    out-of-range values and hallucinated parameters are all caught here and
    turned into a precise correction for the model instead of a 4xx later.
    """
    function = tool_call.get("function") or {}
    name = function.get("name")
    raw_arguments = function.get("arguments", "{}")

    if not isinstance(name, str) or name not in TOOL_SPECS_BY_NAME:
        raise MalformedToolCall(
            f"unknown function {name!r}; available functions: {', '.join(sorted(TOOL_SPECS_BY_NAME))}"
        )

    if isinstance(raw_arguments, dict):
        arguments = raw_arguments
    else:
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
        except (json.JSONDecodeError, TypeError) as exc:
            raise MalformedToolCall(f"arguments for {name} are not valid JSON: {exc}") from exc
        if not isinstance(arguments, dict):
            raise MalformedToolCall(f"arguments for {name} must be a JSON object")

    try:
        validated = TOOL_SPECS_BY_NAME[name].model.model_validate(arguments)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()
        )
        raise MalformedToolCall(f"invalid arguments for {name}: {problems}") from exc

    call_id = tool_call.get("id") or name
    # Only what the model actually supplied is forwarded - defaults (e.g. the
    # CMake preset) are the `tools` service's decision, not the agent's.
    return str(call_id), name, validated.model_dump(exclude_unset=True)


async def execute_tool_call(
    http_client: httpx.AsyncClient,
    name: str,
    arguments: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    """Executes one tool call against TOOLS_BASE_URL. Never raises - transport
    failures are turned into a `{"ok": false, ...}` result so the loop keeps
    running."""
    from edit_policy import gate_tool, observe_tool

    blocked = gate_tool(name)
    if blocked:
        return {"ok": False, "stdout": "", "stderr": blocked, "exit_code": 2}
    mapping = TOOL_ENDPOINT_MAP[name]
    base = PATCH_BASE_URL if name in _PATCH_TOOLS else TOOLS_BASE_URL
    url = f"{base}{mapping['path']}"
    headers = {"X-Request-Id": request_id}
    root = ""
    try:
        from scope import project_root_header

        root = project_root_header.get().strip()
    except Exception:  # noqa: BLE001 - scope is optional for tests that stub the loop
        root = ""
    if root:
        headers["X-Project-Root"] = root
    try:
        if mapping["method"] == "GET":
            resp = await http_client.get(url, params=arguments, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        else:
            resp = await http_client.post(url, json=arguments, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        result = resp.json()
        if not isinstance(result, dict):
            result = {"ok": True, "stdout": str(result), "stderr": "", "exit_code": 0}
        if result.get("ok") is not False:
            observe_tool(name)
        return result
    except Exception as exc:  # noqa: BLE001 - a failed tool call must not crash the loop
        _log("tool_call_transport_failed", request_id, tool=name, error=str(exc))
        return {"ok": False, "stdout": "", "stderr": str(exc), "exit_code": -1}


def _call_key(name: str, arguments: dict[str, Any]) -> str:
    return name + "\n" + json.dumps(arguments, sort_keys=True, separators=(",", ":"))


def summarize_result(name: str, result: dict[str, Any]) -> str:
    ok = result.get("ok")
    if ok is True:
        base = f"{name} succeeded"
    elif ok is False:
        base = f"{name} failed"
    else:
        base = f"{name} completed"
    detail = (result.get("stderr") or "").strip() or (result.get("stdout") or "").strip()
    if detail:
        first_line = detail.splitlines()[0][:200]
        base = f"{base}: {first_line}"
    return base


EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


async def _emit(on_event: Optional[EventCallback], name: str, payload: dict[str, Any]) -> None:
    if on_event is not None:
        await on_event(name, payload)


async def _complete(
    http_client: httpx.AsyncClient,
    messages: list[dict[str, Any]],
    request_id: str,
    *,
    tools: Optional[list[dict[str, Any]]],
    stream_tokens: bool,
    on_event: Optional[EventCallback],
    cancel_event: Optional[asyncio.Event] = None,
    max_tokens: Optional[int] = None,
) -> dict[str, Any]:
    if not stream_tokens:
        return await until_cancelled(
            chat_completion(http_client, messages, request_id, tools=tools, max_tokens=max_tokens),
            cancel_event,
        )

    await _emit(on_event, "activity", {"phase": "generating"})

    async def on_token(text: str) -> None:
        await _emit(on_event, "token", {"text": text})

    async def on_tool_delta(snapshot: dict[str, Any]) -> None:
        await _emit(on_event, "tool_delta", snapshot)

    return await until_cancelled(
        chat_completion_stream(
            http_client,
            messages,
            request_id,
            tools=tools,
            on_token=on_token,
            on_tool_delta=on_tool_delta,
            max_tokens=max_tokens,
        ),
        cancel_event,
    )


async def _plain_text_fallback(
    http_client: httpx.AsyncClient,
    messages: list[dict[str, Any]],
    request_id: str,
    *,
    stream_tokens: bool = False,
    on_event: Optional[EventCallback] = None,
) -> str:
    """Forces a plain-text-only completion (no tools attached) so the turn
    always ends with an answer instead of erroring out."""
    fallback_messages = messages + [{"role": "system", "content": TOOLS_UNAVAILABLE_NOTE}]
    try:
        completion = await _complete(
            http_client,
            fallback_messages,
            request_id,
            tools=None,
            stream_tokens=stream_tokens,
            on_event=on_event,
        )
        message = extract_message(completion)
        return message.get("content") or "I was unable to produce an answer for this request."
    except Exception as exc:  # noqa: BLE001 - final safety net, must return a string no matter what
        _log("fallback_completion_failed", request_id, error=str(exc))
        return "I was unable to reach the language model service to answer this request."


async def run_tool_loop(
    http_client: httpx.AsyncClient,
    system_prompt: str,
    user_message: str,
    request_id: str,
    max_iterations: Optional[int] = None,
    history: Optional[list[dict[str, Any]]] = None,
    on_event: Optional[EventCallback] = None,
    stream_tokens: bool = False,
    enable_tools: bool = True,
    cancel_event: Optional[asyncio.Event] = None,
    max_tokens: Optional[int] = None,
) -> dict[str, Any]:
    """Runs the bounded LLM <-> tools loop and returns
    `{"answer": str, "tool_calls": [{"name", "arguments", "result_summary"}, ...]}`.

    `history` is prior user/assistant turns (no system prompt). `on_event`
    receives `tool_call`, `tool_result`, and (when `stream_tokens`) `token`.
    `enable_tools=False` sends no tool schema and ignores any tool call.
    """
    from edit_policy import reset_edit_phase

    reset_edit_phase()
    max_iterations = max_iterations if max_iterations is not None else MAX_ITERATIONS_DEFAULT
    tool_schema = TOOLS_OPENAI_SCHEMA if enable_tools else None
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": user_message})
    tool_call_summaries: list[dict[str, Any]] = []
    already_retried_malformed = False
    seen_calls: set[str] = set()

    for iteration in range(max_iterations):
        if cancel_event is not None and cancel_event.is_set():
            raise TurnCancelled()
        try:
            messages, context_tokens, usage = await until_cancelled(
                apply_budget(http_client, messages, request_id),
                cancel_event,
            )
            _log("context_tokens", request_id, iteration=iteration, tokens=context_tokens, **usage)
            await _emit(on_event, "context", usage)
            completion = await _complete(
                http_client,
                messages,
                request_id,
                tools=tool_schema,
                stream_tokens=stream_tokens,
                on_event=on_event,
                cancel_event=cancel_event,
                max_tokens=max_tokens,
            )
        except TurnCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - LLM unreachable or context overflow
            text = str(exc).lower()
            overflow = "exceed_context_size" in text or "exceeds the available context size" in text
            if overflow:
                _log("context_overflow", request_id, iteration=iteration, error=str(exc))
                messages, context_tokens, usage = await apply_budget(
                    http_client, messages, request_id, budget=max(1024, safe_budget() // 2)
                )
                await _emit(on_event, "context", usage)
                try:
                    completion = await _complete(
                        http_client,
                        messages,
                        request_id,
                        tools=tool_schema,
                        stream_tokens=stream_tokens,
                        on_event=on_event,
                        cancel_event=cancel_event,
                        max_tokens=max_tokens,
                    )
                except Exception as retry_exc:  # noqa: BLE001
                    _log("llm_call_failed", request_id, iteration=iteration, error=str(retry_exc))
                    return {
                        "answer": "The prompt is still larger than the context window after compression. "
                        "Start a new conversation or ask a narrower question.",
                        "tool_calls": tool_call_summaries,
                    }
            else:
                _log("llm_call_failed", request_id, iteration=iteration, error=str(exc))
                return {
                    "answer": "I couldn't reach the language model service to complete this request.",
                    "tool_calls": tool_call_summaries,
                }

        message = extract_message(completion)
        raw_tool_calls = message.get("tool_calls") or []

        if not enable_tools or not raw_tool_calls:
            return {"answer": message.get("content") or "", "tool_calls": tool_call_summaries}

        parsed_calls = []
        malformed_reason: Optional[str] = None
        for raw_call in raw_tool_calls:
            try:
                parsed_calls.append(_parse_tool_call(raw_call))
            except MalformedToolCall as exc:
                malformed_reason = str(exc)
                break

        if malformed_reason is not None:
            observe_malformed()
            if already_retried_malformed:
                _log("tool_call_malformed_giveup", request_id, iteration=iteration, reason=malformed_reason)
                answer = await _plain_text_fallback(
                    http_client, messages, request_id, stream_tokens=stream_tokens, on_event=on_event
                )
                return {"answer": answer, "tool_calls": tool_call_summaries}

            _log("tool_call_malformed_retry", request_id, iteration=iteration, reason=malformed_reason)
            already_retried_malformed = True
            messages.append({"role": "system", "content": f"{MALFORMED_RETRY_NOTE} Problem: {malformed_reason}"})
            continue

        batch_keys = [_call_key(name, arguments) for _call_id, name, arguments in parsed_calls]
        if len(batch_keys) != len(set(batch_keys)) or any(key in seen_calls for key in batch_keys):
            _log("tool_call_repeated", request_id, iteration=iteration)
            messages.append({"role": "system", "content": REPEAT_NOTE})
            answer = await _plain_text_fallback(
                http_client, messages, request_id, stream_tokens=stream_tokens, on_event=on_event
            )
            return {"answer": answer, "tool_calls": tool_call_summaries}
        seen_calls.update(batch_keys)

        messages.append(message)
        for call_id, name, arguments in parsed_calls:
            await _emit(on_event, "tool_call", {"name": name, "arguments": arguments})
            result = await execute_tool_call(http_client, name, arguments, request_id)
            observe_tool(name, bool(result.get("ok")))
            summary = summarize_result(name, result)
            tool_call_summaries.append({"name": name, "arguments": arguments, "result_summary": summary})
            await _emit(
                on_event,
                "tool_result",
                {"name": name, "result_summary": summary, "ok": bool(result.get("ok"))},
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(result),
                }
            )

    _log("tool_loop_max_iterations_reached", request_id, max_iterations=max_iterations)
    answer = await _plain_text_fallback(
        http_client, messages, request_id, stream_tokens=stream_tokens, on_event=on_event
    )
    return {"answer": answer, "tool_calls": tool_call_summaries}
