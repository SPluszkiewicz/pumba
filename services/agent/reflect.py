"""Optional second pass: drop sentences the cited chunks directly contradict."""

from __future__ import annotations

import json
import os
from typing import Any

from llm_client import chat_completion, extract_message

ENABLED = os.getenv("AGENT_REFLECT", "0") == "1"

REFLECT_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "reflection",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "string",
                    "description": "The answer with contradicted sentences removed, never added to.",
                },
                "removed": {"type": "integer", "description": "How many sentences were removed."},
            },
            "required": ["answer", "removed"],
            "additionalProperties": False,
        },
    },
}

_INSTRUCTION = (
    "You receive a draft answer and the only source chunks it may rely on. "
    "Delete any sentence that is directly contradicted by those chunks. "
    "Do not add facts, hedges, or new sentences. Return JSON only."
)


async def reflect_answer(
    http_client,
    answer: str,
    sources: list[dict[str, Any]],
    request_id: str,
) -> dict[str, Any]:
    """Returns `{"answer", "removed"}`. On any failure, returns the original answer."""
    if not ENABLED or not answer.strip():
        return {"answer": answer, "removed": 0}

    chunks = "\n\n".join(
        f"{s.get('file_path')}:{s.get('start_line')}-{s.get('end_line')}" for s in sources
    ) or "(no chunks)"
    messages = [
        {"role": "system", "content": _INSTRUCTION},
        {"role": "user", "content": f"Sources:\n{chunks}\n\nDraft:\n{answer}"},
    ]
    try:
        completion = await chat_completion(
            http_client, messages, request_id, tools=None, temperature=0.0, response_format=REFLECT_SCHEMA
        )
        content = extract_message(completion).get("content") or ""
        body = json.loads(content)
        revised = body.get("answer")
        removed = int(body.get("removed") or 0)
        if not isinstance(revised, str) or not revised.strip():
            return {"answer": answer, "removed": 0}
        return {"answer": revised, "removed": max(0, removed)}
    except Exception:  # noqa: BLE001 - reflection is optional
        return {"answer": answer, "removed": 0}
