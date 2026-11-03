"""`POST /review`: one LLM pass per changed file, comments anchored to a diff line.

A comment is kept only when its `snippet` occurs on a concrete line of that
file's diff. Anything the model invents is dropped.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import httpx
from llm_client import chat_completion, extract_message
from rules_loader import rules_for

TOOLS_BASE_URL = os.getenv("TOOLS_BASE_URL", "http://tools:8091")
MAX_FILES = 8
MAX_COMMENTS_PER_FILE = 5
MIN_SNIPPET = 8

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "review_comments",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "comments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "snippet": {"type": "string"},
                            "text": {"type": "string"},
                        },
                        "required": ["snippet", "text"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["comments"],
            "additionalProperties": False,
        },
    },
}

_INSTRUCTION = (
    "You review one unified diff. Report only defects you can point at with a "
    "snippet copied verbatim from a changed or context line (no diff prefix). "
    "Return JSON. An empty comments list is correct when the diff is fine."
)


def resolve_snippet(diff: str, snippet: str) -> Optional[int]:
    """1-based new-file line that contains `snippet`, or None."""
    needle = snippet.strip()
    if len(needle) < MIN_SNIPPET:
        return None
    new_line = 0
    for raw in diff.splitlines():
        if raw.startswith("@@"):
            new_line = _hunk_new_start(raw) - 1
            continue
        if raw.startswith(("diff ", "index ", "---", "+++", "\\")):
            continue
        if raw.startswith("+") or (raw.startswith(" ") and not raw.startswith("---")):
            new_line += 1
            if needle in raw[1:]:
                return new_line
    return None


def _hunk_new_start(header: str) -> int:
    try:
        plus = header.split("+", 1)[1]
    except IndexError:
        return 1
    digits = []
    for char in plus:
        if char.isdigit():
            digits.append(char)
        else:
            break
    return int("".join(digits) or "1")


async def review_change(
    http_client: httpx.AsyncClient,
    ref_from: str,
    ref_to: str,
    request_id: str,
) -> dict[str, Any]:
    names = await _post(http_client, "/git/diff-names", {"ref_a": ref_from, "ref_b": ref_to}, request_id)
    if not names.get("ok"):
        return _empty(ref_from, ref_to, error=names.get("stderr") or "git diff --name-only failed")
    files = [line.strip() for line in str(names.get("stdout") or "").splitlines() if line.strip()]
    truncated = len(files) > MAX_FILES
    chosen = files[:MAX_FILES]
    comments: list[dict[str, Any]] = []
    dropped = 0
    for path in chosen:
        diff_body = await _post(
            http_client,
            "/git/diff",
            {"ref_a": ref_from, "ref_b": ref_to, "path": path},
            request_id,
        )
        diff_text = str(diff_body.get("stdout") or "")
        if not diff_body.get("ok") or not diff_text.strip():
            continue
        drafted = await _ask_model(http_client, path, diff_text, request_id)
        for item in drafted[:MAX_COMMENTS_PER_FILE]:
            snippet = str(item.get("snippet") or "")
            text = str(item.get("text") or "").strip()
            line = resolve_snippet(diff_text, snippet)
            if line is None or not text:
                dropped += 1
                continue
            comments.append({"path": path, "line": line, "snippet": snippet.strip(), "text": text})
    return {
        "ok": True,
        "from": ref_from,
        "to": ref_to,
        "files": chosen,
        "truncated": truncated,
        "dropped": dropped,
        "comments": comments,
    }


async def _ask_model(
    http_client: httpx.AsyncClient, path: str, diff_text: str, request_id: str
) -> list[dict[str, Any]]:
    rules = rules_for([path])
    user = f"File: {path}\n\nRules:\n{rules or '(none)'}\n\nDiff:\n{diff_text}"
    try:
        completion = await chat_completion(
            http_client,
            [{"role": "system", "content": _INSTRUCTION}, {"role": "user", "content": user}],
            request_id,
            tools=None,
            temperature=0.0,
            response_format=REVIEW_SCHEMA,
        )
        content = extract_message(completion).get("content") or ""
        body = json.loads(content)
    except Exception:  # noqa: BLE001 - one bad file must not abort the review
        return []
    comments = body.get("comments") if isinstance(body, dict) else None
    if not isinstance(comments, list):
        return []
    return [item for item in comments if isinstance(item, dict)]


def _tool_headers(request_id: str) -> dict[str, str]:
    headers = {"X-Request-Id": request_id}
    try:
        from scope import project_root_header

        root = project_root_header.get().strip()
    except Exception:  # noqa: BLE001
        root = ""
    if root:
        headers["X-Project-Root"] = root
    return headers


async def _post(http_client: httpx.AsyncClient, path: str, payload: dict[str, Any], request_id: str) -> dict[str, Any]:
    try:
        response = await http_client.post(
            f"{TOOLS_BASE_URL.rstrip('/')}{path}",
            json=payload,
            headers=_tool_headers(request_id),
        )
        response.raise_for_status()
        body = response.json()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stderr": str(exc), "stdout": ""}
    return body if isinstance(body, dict) else {"ok": False, "stderr": "tools returned a non-object", "stdout": ""}


def _empty(ref_from: str, ref_to: str, *, error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "from": ref_from,
        "to": ref_to,
        "files": [],
        "truncated": False,
        "dropped": 0,
        "comments": [],
        "error": error,
    }
