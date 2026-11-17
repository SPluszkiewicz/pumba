"""Bounded tool-calling loop between the agent and the `llm` + `tools` services.

Each iteration sends the conversation (with the tool schemas attached) to the
LLM. Tool calls are executed, or replayed when the same successful arguments
were already run. Plain text is the answer only when nothing is still open:
an open failure, an unverified edit, a malformed batch, a disguised call, a
reply cut off at `length`, or the first prose of a turn that has not used a
tool yet. Those cases send one system instruction and continue, up to the
configured limits. The turn always ends with an answer.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any, Optional

import httpx
from common.logging import get_logger
from common.metrics import observe_malformed, observe_tool
from journal import VERIFY_TOOLS, journal_excerpt
from llm_client import (
    api_messages,
    chat_completion,
    chat_completion_stream,
    extract_message,
    finish_reason,
)
from pydantic import ValidationError
from serena_named_tools import SERENA_TOOL_NAMES, SERENA_WRITE_NAMES
from serena_policy import serena_refusal
from serena_transport import McpSidecar
from token_budget import apply_budget, safe_budget
from tools_schema import TOOL_ENDPOINT_MAP, TOOL_SPECS_BY_NAME, TOOLS_OPENAI_SCHEMA
from turns import TurnCancelled, until_cancelled

TOOLS_BASE_URL = os.getenv("TOOLS_BASE_URL", "http://tools:8091")

# Real MCP client. Tests replace this object; nothing else stands in for it.
sidecar = McpSidecar()
_WRITE_TOOLS = SERENA_WRITE_NAMES
_LOOKUP_TOOLS = SERENA_TOOL_NAMES - SERENA_WRITE_NAMES
_CALL_MARKS = "({:"
REQUEST_TIMEOUT_SECONDS = float(os.getenv("AGENT_REQUEST_TIMEOUT_SECONDS", "14400"))
MAX_ITERATIONS_DEFAULT = int(os.getenv("AGENT_TOOL_LOOP_MAX_ITERATIONS", "8"))

TOOLS_UNAVAILABLE_NOTE = (
    "Tool calling is unavailable for this turn. Answer the user's original "
    "question as best you can in plain text, and say that you were unable "
    "to verify it with tools."
)

FAILURE_NOTE = (
    "A tool failed and is still open. Follow repair if it is present. "
    "After you change a file, call build_step again. "
    "Do not resend a call whose result was unchanged."
)
NEEDS_BUILD_NOTE = "Edits are written and not verified. Call build_step once. Do not answer yet."
NO_TOOL_NOTE = (
    "You have not called a tool yet. If this is about the repository, call one now. "
    "If you can answer without a tool, answer."
)
DISGUISED_NOTE = "Emit a structured tool call. A tool call written in the answer text does not run."
LENGTH_NOTE = "The reply was cut off. Send a shorter tool call or a shorter answer."
REPLAY_PREFIX = "Same arguments as an earlier call. The result is unchanged."
COULD_NOT_FINISH = "Could not finish the tool loop."
UNREACHABLE_NOTE = "I was unable to reach the language model service to answer this request."


log = get_logger("tool_loop")


def _log(event: str, request_id: str, **fields: Any) -> None:
    log.info(event, request_id=request_id, **fields)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


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


def _split_calls(
    raw_calls: list[Any],
) -> tuple[list[tuple[str, str, dict[str, Any]]], list[str], list[str]]:
    """Parse a batch. One bad call rejects the whole batch; nothing is run."""
    parsed: list[tuple[str, str, dict[str, Any]]] = []
    errors: list[str] = []
    valid_names: list[str] = []
    for raw in raw_calls:
        try:
            call = _parse_tool_call(raw if isinstance(raw, dict) else {})
        except MalformedToolCall as exc:
            errors.append(str(exc))
            continue
        parsed.append(call)
        valid_names.append(call[1])
    return parsed, errors, valid_names


def _call_key(name: str, arguments: dict[str, Any]) -> str:
    payload = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{name}|{payload}"


def _batch_identity(parsed: list[tuple[str, str, dict[str, Any]]]) -> tuple[str, ...]:
    return tuple(sorted({_call_key(name, arguments) for _, name, arguments in parsed}))


def _is_lookup(name: str) -> bool:
    return name in _LOOKUP_TOOLS or name.startswith(("git_", "svn_", "lsp_"))


def _closes(open_name: str, success_name: str) -> bool:
    """Whether an executed ok:true of `success_name` closes this open failure."""
    if open_name in VERIFY_TOOLS:
        return success_name == open_name
    if open_name in _WRITE_TOOLS:
        return success_name in _WRITE_TOOLS
    if _is_lookup(open_name):
        return True
    return False


def _contains_tool_name(value: Any) -> bool:
    if isinstance(value, str):
        return value in TOOL_SPECS_BY_NAME
    if isinstance(value, dict):
        return any(_contains_tool_name(key) or _contains_tool_name(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_tool_name(item) for item in value)
    return False


def _json_has_tool_name(text: str) -> bool:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    return _contains_tool_name(parsed)


def _fence_bodies(text: str) -> list[str]:
    """Bodies of markdown fences. An unclosed fence runs to the end of the text."""
    bodies: list[str] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        if lines[index].strip().startswith("```"):
            index += 1
            chunk: list[str] = []
            while index < len(lines) and lines[index].strip() != "```":
                chunk.append(lines[index])
                index += 1
            body = "\n".join(chunk).strip()
            if body:
                bodies.append(body)
        index += 1
    return bodies


_TAG_CALL = re.compile(r"<(tool_call|tools|function)>\s*(.*?)\s*</\1>", re.DOTALL)


def _first_object(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, TypeError, ValueError):
        start = stripped.find("{")
        if start < 0:
            return None
        try:
            parsed, _ = json.JSONDecoder().raw_decode(stripped[start:])
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        return parsed


def _as_call(parsed: Any) -> Optional[dict[str, Any]]:
    """A prose object the same shape as a structured function call, or None."""
    if not isinstance(parsed, dict):
        return None
    name = parsed.get("name")
    arguments = parsed.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
    if not isinstance(name, str) or name not in TOOL_SPECS_BY_NAME or not isinstance(arguments, dict):
        return None
    return {"id": name, "function": {"name": name, "arguments": arguments}}


def _recover_prose_call(text: str) -> Optional[dict[str, Any]]:
    """The tool call a local model wrote in the answer, when it is complete."""
    candidates = [text, *_fence_bodies(text)]
    candidates.extend(match.group(2) for match in _TAG_CALL.finditer(text))
    for candidate in candidates:
        call = _as_call(_first_object(candidate))
        if call is not None:
            return call
    return None


def _is_disguised_call(text: str) -> bool:
    """True when the prose is a tool call written as text instead of a structured call."""
    stripped = text.strip()
    if stripped and _json_has_tool_name(stripped):
        return True
    if any(_json_has_tool_name(body) for body in _fence_bodies(text)):
        return True
    for line in text.splitlines():
        trimmed = line.strip()
        for name in TOOL_SPECS_BY_NAME:
            marker = trimmed[len(name)] if len(trimmed) > len(name) else ""
            if trimmed.startswith(name) and marker in _CALL_MARKS:
                return True
    return False


def _rejected_batch_note(errors: list[str], valid_names: list[str]) -> str:
    lines = list(errors)
    if valid_names:
        names = ", ".join(dict.fromkeys(valid_names))
        lines.append(f"Valid calls not executed: {names}. Send them again.")
    return "\n".join(lines)


def _repair_text(repair: Any) -> str:
    if isinstance(repair, str):
        return repair
    return json.dumps(repair, ensure_ascii=False)


def _failure_note(name: str, detail: str, repair: Any) -> str:
    lines = [FAILURE_NOTE, name, detail]
    if repair is not None:
        lines.append(_repair_text(repair))
    return "\n".join(lines)


def _tool_content(result: dict[str, Any], *, replay: bool) -> str:
    body = json.dumps(result)
    if replay:
        return REPLAY_PREFIX + "\n" + body
    return body


class _Turn:
    """Per-turn loop state. Nothing here is written to disk."""

    def __init__(self) -> None:
        self.open_name: Optional[str] = None
        self.open_detail = ""
        self.open_repair: Any = None
        self.nudges = 0
        self.consecutive_malformed = 0
        self.identical_batches = 0
        self.batch_identity: Optional[tuple[str, ...]] = None
        self.cache: dict[str, dict[str, Any]] = {}
        self.tools_touched = False
        self.no_tool_sent = False
        self.iterations = 0
        self.summaries: list[dict[str, Any]] = []

    def observe_result(self, name: str, result: dict[str, Any]) -> None:
        """Open, replace, or close the failure. Caller skips replays and edit-phase refusals."""
        if result.get("ok") is False:
            self.open_name = name
            self.open_detail = journal_excerpt(name, _text(result.get("stderr")), _text(result.get("stdout")))
            self.open_repair = result["repair"] if "repair" in result and result["repair"] is not None else None
            return
        if result.get("ok") is True and self.open_name is not None and _closes(self.open_name, name):
            self.open_name = None
            self.open_detail = ""
            self.open_repair = None

    def remember_success(self, key: str, name: str, result: dict[str, Any]) -> None:
        if result.get("ok") is not True:
            return
        if name in SERENA_WRITE_NAMES:
            self.cache.clear()
        self.cache[key] = result


async def execute_tool_call(
    http_client: httpx.AsyncClient,
    name: str,
    arguments: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    """Executes one named tool. Serena names go to `sidecar` after the agent's
    checkout, path, and size checks. Every other name goes to tools. A dead
    destination becomes `{"ok": false, ...}` so the loop keeps running."""
    from edit_policy import gate_tool
    from edit_policy import observe_tool as observe_phase

    blocked = gate_tool(name)
    if blocked:
        return {"ok": False, "stdout": "", "stderr": blocked, "exit_code": 2}
    if name in SERENA_TOOL_NAMES:
        refused = serena_refusal(arguments)
        if refused is not None:
            return refused
        try:
            result = await sidecar.call(name, arguments, request_id)
        except Exception as exc:  # noqa: BLE001 - a dead sidecar must not crash the loop
            _log("tool_call_transport_failed", request_id, tool=name, error=str(exc))
            return {"ok": False, "stdout": "", "stderr": str(exc), "exit_code": -1}
        if isinstance(result, dict) and result.get("ok") is True:
            observe_phase(name, ok=True)
        return result
    mapping = TOOL_ENDPOINT_MAP[name]
    url = f"{TOOLS_BASE_URL}{mapping['path']}"
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
        if resp.status_code == 400:
            detail = resp.text
            try:
                body = resp.json()
            except ValueError:
                body = None
            if isinstance(body, dict):
                detail = str(body.get("error") or body.get("stderr") or detail)
                refused = {
                    "ok": False,
                    "stdout": str(body.get("stdout") or ""),
                    "stderr": detail,
                    "exit_code": 2,
                }
                if "repair" in body:
                    refused["repair"] = body["repair"]
                return refused
            return {"ok": False, "stdout": "", "stderr": detail, "exit_code": 2}
        resp.raise_for_status()
        result = resp.json()
        if not isinstance(result, dict):
            result = {"ok": True, "stdout": str(result), "stderr": "", "exit_code": 0}
        # Phase moves only on an executed ok:true. A missing `ok` is not a success.
        if result.get("ok") is True:
            observe_phase(name, ok=True)
        return result
    except Exception as exc:  # noqa: BLE001 - a failed tool call must not crash the loop
        _log("tool_call_transport_failed", request_id, tool=name, error=str(exc))
        return {"ok": False, "stdout": "", "stderr": str(exc), "exit_code": -1}


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


def _summary(name: str, arguments: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "arguments": arguments,
        "result_summary": summarize_result(name, result),
        "ok": result.get("ok") is True,
        "line": journal_excerpt(name, _text(result.get("stderr")), _text(result.get("stdout"))),
    }


def _result(
    answer: str,
    tool_calls: list[dict[str, Any]],
    nudges: int,
    *,
    outcome: str,
    iterations: int,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "answer": answer,
        "tool_calls": tool_calls,
        "outcome": outcome,
        "iterations": iterations,
    }
    if nudges:
        out["nudges"] = nudges
    return out


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
    # `name` stays on the agent list for the budget. The wire request drops it.
    messages = api_messages(messages)
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


async def _completion_without_tools(
    http_client: httpx.AsyncClient,
    messages: list[dict[str, Any]],
    request_id: str,
    *,
    stream_tokens: bool = False,
    on_event: Optional[EventCallback] = None,
    cancel_event: Optional[asyncio.Event] = None,
    max_tokens: Optional[int] = None,
) -> str:
    """One plain completion. Its prose is the answer and is not nudged.

    An empty prose, or a completion that only contains tool calls, becomes
    the fixed sentence. Tool calls in this completion are not executed.
    """
    fallback_messages = messages + [{"role": "system", "content": TOOLS_UNAVAILABLE_NOTE}]
    try:
        completion = await _complete(
            http_client,
            fallback_messages,
            request_id,
            tools=None,
            stream_tokens=stream_tokens,
            on_event=on_event,
            cancel_event=cancel_event,
            max_tokens=max_tokens,
        )
    except TurnCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - final safety net, must return a string no matter what
        _log("fallback_completion_failed", request_id, error=str(exc))
        return UNREACHABLE_NOTE
    message = extract_message(completion)
    text = _text(message.get("content"))
    if not text.strip():
        return COULD_NOT_FINISH
    return text


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
    """Runs the bounded LLM <-> tools loop.

    Returns `answer`, `tool_calls` (each with `name`, `arguments`,
    `result_summary`, `ok`, `line`), `outcome`, `iterations`, and `nudges`
    when at least one refusal was sent. `nudges` is omitted when it is 0.
    """
    from edit_policy import PHASE, gate_tool, reset_edit_phase

    reset_edit_phase()
    max_iterations = max_iterations if max_iterations is not None else MAX_ITERATIONS_DEFAULT
    nudge_limit = _env_int("AGENT_LOOP_NUDGES", 3)
    malformed_limit = _env_int("AGENT_LOOP_MALFORMED_LIMIT", 3)
    repeat_limit = _env_int("AGENT_LOOP_REPEAT_LIMIT", 3)
    tool_schema = TOOLS_OPENAI_SCHEMA if enable_tools else None
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": user_message})
    state = _Turn()

    async def forced(outcome: str) -> dict[str, Any]:
        answer = await _completion_without_tools(
            http_client,
            messages,
            request_id,
            stream_tokens=stream_tokens,
            on_event=on_event,
            cancel_event=cancel_event,
            max_tokens=max_tokens,
        )
        return _result(answer, state.summaries, state.nudges, outcome=outcome, iterations=state.iterations)

    async def reject_batch(note: str, iteration: int) -> Optional[dict[str, Any]]:
        observe_malformed()
        state.consecutive_malformed += 1
        give_up = state.consecutive_malformed >= malformed_limit or iteration >= max_iterations - 1
        _log(
            "tool_call_malformed_giveup" if give_up else "tool_call_malformed_retry",
            request_id,
            iteration=iteration,
            consecutive=state.consecutive_malformed,
        )
        if give_up:
            return await forced("malformed_giveup")
        messages.append({"role": "system", "content": note})
        return None

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
                except TurnCancelled:
                    raise
                except Exception as retry_exc:  # noqa: BLE001
                    _log("llm_call_failed", request_id, iteration=iteration, error=str(retry_exc))
                    return _result(
                        "The prompt is still larger than the context window after compression. "
                        "Start a new conversation or ask a narrower question.",
                        state.summaries,
                        state.nudges,
                        outcome="context_overflow",
                        iterations=state.iterations,
                    )
            else:
                _log("llm_call_failed", request_id, iteration=iteration, error=str(exc))
                return _result(
                    "I couldn't reach the language model service to complete this request.",
                    state.summaries,
                    state.nudges,
                    outcome="llm_unreachable",
                    iterations=state.iterations,
                )

        state.iterations += 1
        message = extract_message(completion)
        if not enable_tools:
            return _result(
                _text(message.get("content")),
                state.summaries,
                state.nudges,
                outcome="ok",
                iterations=state.iterations,
            )

        raw_tool_calls = list(message.get("tool_calls") or [])
        if not raw_tool_calls:
            recovered = _recover_prose_call(_text(message.get("content")))
            if recovered is not None:
                raw_tool_calls = [recovered]
        if raw_tool_calls:
            parsed, errors, valid_names = _split_calls(raw_tool_calls)
            if errors:
                # A malformed neighbour blocks the valid calls in the same batch.
                rejected = await reject_batch(_rejected_batch_note(errors, valid_names), iteration)
                if rejected is not None:
                    return rejected
                continue

            state.consecutive_malformed = 0
            identity = _batch_identity(parsed)
            if identity != state.batch_identity:
                state.identical_batches = 0
                state.batch_identity = identity
            if state.identical_batches >= repeat_limit:
                _log("tool_call_repeated", request_id, iteration=iteration)
                return await forced("repeated_tool")
            state.identical_batches += 1

            messages.append(message)
            seen: dict[str, dict[str, Any]] = {}
            for call_id, name, arguments in parsed:
                key = _call_key(name, arguments)
                await _emit(on_event, "tool_call", {"name": name, "arguments": arguments})
                if key in seen:
                    result = seen[key]
                    replay = True
                elif key in state.cache:
                    result = state.cache[key]
                    replay = True
                    seen[key] = result
                else:
                    refused = gate_tool(name) is not None
                    result = await execute_tool_call(http_client, name, arguments, request_id)
                    observe_tool(name, bool(result.get("ok")))
                    replay = False
                    seen[key] = result
                    if not refused:
                        state.observe_result(name, result)
                        state.remember_success(key, name, result)
                state.tools_touched = True
                summary = _summary(name, arguments, result)
                state.summaries.append(summary)
                await _emit(
                    on_event,
                    "tool_result",
                    {"name": name, "result_summary": summary["result_summary"], "ok": summary["ok"]},
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": _tool_content(result, replay=replay),
                    }
                )
            continue

        prose = _text(message.get("content"))
        if _is_disguised_call(prose):
            rejected = await reject_batch(DISGUISED_NOTE, iteration)
            if rejected is not None:
                return rejected
            continue
        if finish_reason(completion) == "length":
            rejected = await reject_batch(LENGTH_NOTE, iteration)
            if rejected is not None:
                return rejected
            continue

        if state.open_name is not None:
            reason = "tool_failed"
            note = _failure_note(state.open_name, state.open_detail, state.open_repair)
        elif PHASE.get() == "editing":
            reason = "needs_build"
            note = NEEDS_BUILD_NOTE
        elif not state.tools_touched and not state.no_tool_sent:
            reason = "no_tool"
            note = NO_TOOL_NOTE
        else:
            return _result(prose, state.summaries, state.nudges, outcome="ok", iterations=state.iterations)

        if state.nudges >= nudge_limit or iteration >= max_iterations - 1:
            return _result(prose, state.summaries, state.nudges, outcome="ok", iterations=state.iterations)

        state.nudges += 1
        if reason == "no_tool":
            state.no_tool_sent = True
        payload: dict[str, Any] = {"reason": reason, "remaining": nudge_limit - state.nudges}
        if reason == "tool_failed" and state.open_name is not None:
            payload["tool"] = state.open_name
        elif reason == "needs_build":
            payload["tool"] = "build_step"
        _log("tool_loop_nudge", request_id, iteration=iteration, reason=reason, remaining=payload["remaining"])
        await _emit(on_event, "nudge", payload)
        # The refused prose is not added. The model must not see itself as having answered.
        messages.append({"role": "system", "content": note})

    _log("tool_loop_max_iterations_reached", request_id, max_iterations=max_iterations)
    return await forced("max_iterations")
