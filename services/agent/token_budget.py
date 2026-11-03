"""Keep the conversation under AGENT_CONTEXT_BUDGET_TOKENS before each LLM call."""

from __future__ import annotations

import json
import os
from typing import Any

from llm_client import count_tokens

# Default per CONTRACTS.md (compose passes the same value explicitly).
BUDGET = int(os.getenv("AGENT_CONTEXT_BUDGET_TOKENS", "28544"))
# llama-server counts the chat template and the tool schemas, which `_dump` does not.
# The gap observed on a real call was about 3200 tokens.
TEMPLATE_OVERHEAD = int(os.getenv("AGENT_CONTEXT_OVERHEAD_TOKENS", "3200"))
REPLY_RESERVE = int(os.getenv("AGENT_CONTEXT_REPLY_RESERVE", "1024"))


def context_window() -> int:
    return max(1024, int(os.getenv("LLM_CTX_SIZE", "32768")))


def safe_budget() -> int:
    """Measured-token ceiling that still leaves room for the reply and the template."""
    room = context_window() - REPLY_RESERVE - TEMPLATE_OVERHEAD
    return max(1024, min(BUDGET, room))


def usage_snapshot(measured: int, *, compressed: bool) -> dict[str, Any]:
    window = context_window()
    used = min(window, max(0, measured) + TEMPLATE_OVERHEAD)
    return {
        "used": used,
        "window": window,
        "ratio": round(used / window, 3),
        "compressed": compressed,
    }


def _dump(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        parts.append(str(message.get("content") or ""))
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            parts.append(str(fn.get("arguments") or ""))
    return "\n".join(parts)


async def message_tokens(http_client, messages: list[dict[str, Any]], request_id: str) -> int:
    return await count_tokens(http_client, _dump(messages), request_id)


def _summarize_tool_message(message: dict[str, Any]) -> dict[str, Any]:
    raw = message.get("content") or ""
    try:
        body = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        body = {}
    if isinstance(body, dict):
        ok = body.get("ok")
        err = (body.get("stderr") or body.get("stdout") or "")[:120]
        text = f"tool result collapsed ({'ok' if ok else 'failed'}): {err}"
    else:
        text = "tool result collapsed"
    return {**message, "content": text}


def _shorten(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    head = max(1, (limit * 2) // 3)
    tail = max(1, limit - head - 1)
    return flat[:head] + "…" + flat[-tail:]


_SUMMARY_SYSTEM = (
    "Summarize this earlier coding-assistant conversation for the next turn. "
    "Keep file paths, symbols, decisions, errors, and tool outcomes. "
    "Drop greetings and repeated text. Plain prose, under 600 words."
)


def _transcript(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        content = str(message.get("content") or "")
        if len(content) > 6000:
            content = content[:4000] + "\n…\n" + content[-1500:]
        if content:
            lines.append(f"{message.get('role', 'note')}: {content}")
    text = "\n".join(lines)
    if len(text) > 24000:
        text = text[:16000] + "\n…\n" + text[-6000:]
    return text


async def _model_summary(http_client, middle: list[dict[str, Any]], request_id: str) -> str:
    """Ask the loaded chat model to summarize older turns."""
    from llm_client import chat_completion, extract_message

    transcript = _transcript(middle)
    if not transcript.strip():
        return ""
    completion = await chat_completion(
        http_client,
        [
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": transcript},
        ],
        request_id + "-summary",
        temperature=0.1,
        max_tokens=700,
    )
    return str(extract_message(completion).get("content") or "").strip()


def compress_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold every message except the system prompt and the latest user turn into one note.

    The latest question is kept. Older turns are shortened, not removed.
    """
    if len(messages) <= 1:
        return list(messages)
    head = messages[0] if messages[0].get("role") == "system" else None
    tail = messages[-1]
    middle = messages[1:-1] if head is not None else messages[:-1]
    lines: list[str] = []
    for message in middle:
        content = _shorten(str(message.get("content") or ""), 220)
        if not content:
            continue
        lines.append(f"{message.get('role', 'note')}: {content}")
    if not lines and head is None:
        return list(messages)
    note = {
        "role": "system",
        "content": "Compressed earlier context (older turns shortened, not dropped):\n" + "\n".join(lines),
    }
    if str(tail.get("role")) == "user":
        content = str(tail.get("content") or "")
        if len(content) > 1500:
            tail = {**tail, "content": _shorten(content, 1200)}
    out = [head, note, tail] if head is not None else [note, tail]
    return [item for item in out if item is not None]


async def apply_budget(
    http_client,
    messages: list[dict[str, Any]],
    request_id: str,
    *,
    budget: int | None = None,
) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    """Collapse bulky tool results, then compress older turns if still over the window.

    Returns (messages, measured token count, usage snapshot).
    """
    limit = budget if budget is not None else safe_budget()
    current = list(messages)
    compressed = False
    tokens = await message_tokens(http_client, current, request_id)
    if tokens <= limit:
        return current, tokens, usage_snapshot(tokens, compressed=False)

    for index, message in enumerate(current):
        if message.get("role") == "tool" and len(str(message.get("content") or "")) > 160:
            current[index] = _summarize_tool_message(message)
            compressed = True
            tokens = await message_tokens(http_client, current, request_id)
            if tokens <= limit:
                return current, tokens, usage_snapshot(tokens, compressed=True)

    head = current[0] if current and current[0].get("role") == "system" else None
    tail = current[-1]
    middle = current[1:-1] if head is not None else current[:-1]
    summary = ""
    if middle:
        try:
            summary = await _model_summary(http_client, middle, request_id)
        except Exception:  # noqa: BLE001 - a failed summary still has to fit the window
            summary = ""
    if summary:
        note = {"role": "system", "content": "Summary of earlier context:\n" + summary}
        folded = [head, note, tail] if head is not None else [note, tail]
        folded = [item for item in folded if item is not None]
    else:
        folded = compress_messages(current)
    if folded != current:
        compressed = True
        current = folded
        tokens = await message_tokens(http_client, current, request_id)
    return current, tokens, usage_snapshot(tokens, compressed=compressed)
