"""In-memory multi-turn history. No disk — agent’s rootfs is read-only."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

TTL_SECONDS = max(30, int(os.getenv("AGENT_CONVERSATION_TTL_SECONDS", "3600")))
MAX_TURNS = max(1, int(os.getenv("AGENT_CONVERSATION_MAX_TURNS", "8")))


@dataclass
class Conversation:
    conversation_id: str
    turns: list[tuple[str, str]] = field(default_factory=list)
    updated_monotonic: float = field(default_factory=time.monotonic)


class ConversationStore:
    def __init__(self, ttl_seconds: float = TTL_SECONDS, max_turns: int = MAX_TURNS) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_turns = max_turns
        self._items: dict[str, Conversation] = {}

    def sweep(self, now: float | None = None) -> int:
        stamp = time.monotonic() if now is None else now
        dead = [key for key, item in self._items.items() if stamp - item.updated_monotonic > self.ttl_seconds]
        for key in dead:
            del self._items[key]
        return len(dead)

    def history(self, conversation_id: str) -> list[dict[str, str]]:
        self.sweep()
        item = self._items.get(conversation_id)
        if item is None:
            return []
        messages: list[dict[str, str]] = []
        for user, assistant in item.turns:
            messages.append({"role": "user", "content": user})
            messages.append({"role": "assistant", "content": assistant})
        return messages

    def remember(self, conversation_id: str, user: str, assistant: str) -> None:
        self.sweep()
        item = self._items.get(conversation_id)
        if item is None:
            item = Conversation(conversation_id)
            self._items[conversation_id] = item
        item.turns.append((user, assistant))
        if len(item.turns) > self.max_turns:
            item.turns = item.turns[-self.max_turns :]
        item.updated_monotonic = time.monotonic()


STORE = ConversationStore()
