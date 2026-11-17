"""In-memory multi-turn history. No disk — agent’s rootfs is read-only."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from journal import VERIFY_TOOLS

TTL_SECONDS = max(30, int(os.getenv("AGENT_CONVERSATION_TTL_SECONDS", "3600")))
MAX_TURNS = max(1, int(os.getenv("AGENT_CONVERSATION_MAX_TURNS", "8")))
MAX_TRACE = 12
TRACE_ARG_KEYS = (
    "path",
    "pattern",
    "preset",
    "target",
    "filter",
    "query",
    "symbol",
    "name",
    "start_line",
    "end_line",
    "glob",
    "ref",
    "ref_a",
    "ref_b",
    "max_results",
    "regex",
    "line",
    "character",
)
_TRACE_HEADER = "Tools used for the previous question. Re-read a file for its current text."


@dataclass
class Turn:
    user: str
    assistant: str
    trace: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Conversation:
    conversation_id: str
    turns: list[Turn] = field(default_factory=list)
    updated_monotonic: float = field(default_factory=time.monotonic)


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def prepare_trace(calls: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Keep at most 12 calls. The latest verification failure is never dropped."""
    prepared: list[dict[str, Any]] = []
    for call in calls or []:
        arguments = call.get("arguments") or {}
        kept = {
            key: _clip(arguments[key], 120)
            for key in TRACE_ARG_KEYS
            if key in arguments
        }
        prepared.append(
            {
                "name": str(call.get("name") or ""),
                "ok": bool(call.get("ok")),
                "line": _clip(call.get("line") or "", 200),
                "arguments": kept,
            }
        )
    if len(prepared) <= MAX_TRACE:
        return prepared
    tail = prepared[-MAX_TRACE:]
    failure = next(
        (
            entry
            for entry in reversed(prepared)
            if entry["ok"] is False and entry["name"] in VERIFY_TOOLS
        ),
        None,
    )
    if failure is None or failure in tail:
        return tail
    return [failure, *tail[1:]]


def format_trace(trace: list[dict[str, Any]]) -> str:
    lines = [_TRACE_HEADER]
    for entry in trace:
        bits = [entry["name"]]
        for key in TRACE_ARG_KEYS:
            if key in entry["arguments"]:
                bits.append(f"{key}={entry['arguments'][key]}")
        flag = "true" if entry["ok"] else "false"
        lines.append("- " + " ".join(bits) + f" ok={flag}: " + entry["line"])
    return "\n".join(lines)


class ConversationStore:
    def __init__(self, ttl_seconds: float = TTL_SECONDS, max_turns: int = MAX_TURNS) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_turns = max_turns
        self._items: dict[str, Conversation] = {}

    def sweep(self, now: float | None = None) -> int:
        stamp = time.monotonic() if now is None else now
        from turns import TURNS

        dead = [
            key
            for key, item in self._items.items()
            if stamp - item.updated_monotonic > self.ttl_seconds and not TURNS.active(key)
        ]
        for key in dead:
            del self._items[key]
        return len(dead)

    def touch(self, conversation_id: str) -> None:
        item = self._items.get(conversation_id)
        if item is not None:
            item.updated_monotonic = time.monotonic()

    def history(self, conversation_id: str) -> list[dict[str, str]]:
        self.sweep()
        item = self._items.get(conversation_id)
        if item is None:
            return []
        messages: list[dict[str, str]] = []
        for turn in item.turns:
            messages.append({"role": "user", "content": turn.user})
            if turn.trace:
                messages.append({"role": "system", "content": format_trace(turn.trace)})
            messages.append({"role": "assistant", "content": turn.assistant})
        return messages

    def remember(
        self,
        conversation_id: str,
        user: str,
        assistant: str,
        trace: list[dict[str, Any]] | None = None,
    ) -> None:
        self.sweep()
        item = self._items.get(conversation_id)
        if item is None:
            item = Conversation(conversation_id)
            self._items[conversation_id] = item
        item.turns.append(Turn(user, assistant, prepare_trace(trace)))
        if len(item.turns) > self.max_turns:
            item.turns = item.turns[-self.max_turns :]
        item.updated_monotonic = time.monotonic()


STORE = ConversationStore()
