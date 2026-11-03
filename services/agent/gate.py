"""Choose `direct` or `code` before retrieval and the tool loop.

Rules cover the obvious cases (a calculation, a greeting, a file or a build).
An undecided follow-up in an existing conversation stays on `code`. A first
undecided message is classified by one short LLM call when
`AGENT_GATE_CLASSIFY=1`. Any failure stays on `code`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Literal, Optional

import httpx
from common.logging import get_logger
from llm_client import chat_completion, extract_message

Route = Literal["direct", "code"]
By = Literal["rules", "model"]

log = get_logger("gate")

_NUMBER = (
    r"(?:\d+(?:[.,]\d+)?|\b(?:"
    r"zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"zéro|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix"
    r")\b)"
)
_OPERATOR = (
    r"(?:\+|−|-|–|\*|×|x|\/|"
    r"plus|minus|moins|times|fois|"
    r"multipli[ée](?:e)?(?:\s+par)?|divis[ée](?:e)?(?:\s+par)?|divided(?:\s+by)?)"
)
_ARITHMETIC = re.compile(
    rf"(?ix)^\s*(?:(?:"
    rf"combien\s+(?:font|fait)|que\s+fait|what(?:'s|\s+is)|calcule|calculate|compute"
    rf")\s+)?{_NUMBER}\s*{_OPERATOR}\s*{_NUMBER}\s*\??\s*$"
)
_GREETING = re.compile(
    r"(?ix)^\s*(?:(?:dis|dit|say|reply|réponds|reponds)(?:\s+\S+){0,8}\s+)?"
    r"(?:bonjour|bonsoir|hello|hi|hey|salut|merci|thanks|thank\s+you)\b.{0,40}\s*$"
)
# Keyword flags are case-insensitive. Identifier shapes stay case-sensitive:
# an ignore-case CamelCase pattern would match every word of four letters.
_CODE_WORDS = re.compile(
    r"(?i)("
    r"\.(?:cpp|cc|cxx|hpp|hh|h|cmake)\b|"
    r"\bc\+\+\b|"
    r"\b(?:cmake|cmakelists|catch2|clangd?|qdrant|gtest|ctest|indexer|embedder|reranker)\b|"
    r"\b(?:compile|compiler|build|lint|namespace|struct|class|template|header|preset)\b|"
    r"\b(?:fichier|function|fonction|symbole|symbol|test|tests|chunk|chunks)\b|"
    r"\b(?:this\s+repo(?:sitory)?|this\s+project|ce\s+d[ée]p[oô]t|ce\s+projet|codebase)\b|"
    r"`[^`]+`"
    r")"
)
_CODE_IDENT = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9]*_[A-Za-z0-9_]+\b|"
    r"\b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]+\b"
)

_CLASSIFY = (
    "Classify the user question for a C++ repository assistant. Return JSON only. "
    'route="code" when answering needs this repository: a file, a symbol, a build, a test, '
    "or project behavior. "
    'route="direct" when it is arithmetic, a greeting, or general knowledge that does not '
    "need the repository. If unsure, use code."
)

ROUTE_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "route",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"route": {"type": "string", "enum": ["direct", "code"]}},
            "required": ["route"],
            "additionalProperties": False,
        },
    },
}


@dataclass(frozen=True)
class GateDecision:
    route: Route
    by: By
    reason: str


def decide_by_rules(query: str, *, has_history: bool) -> Optional[GateDecision]:
    """A decision the rules can make alone, or None when a model may classify."""
    text = query.strip()
    if _CODE_WORDS.search(text) or _CODE_IDENT.search(text):
        return GateDecision("code", "rules", "code_signal")
    if _ARITHMETIC.match(text) or _GREETING.match(text):
        reason = "arithmetic" if _ARITHMETIC.match(text) else "greeting"
        return GateDecision("direct", "rules", reason)
    if has_history:
        return GateDecision("code", "rules", "conversation")
    return None


async def decide(
    http_client: httpx.AsyncClient,
    query: str,
    request_id: str,
    *,
    has_history: bool,
) -> GateDecision:
    ruled = decide_by_rules(query, has_history=has_history)
    if ruled is not None:
        return ruled
    if os.getenv("AGENT_GATE_CLASSIFY", "1") != "1":
        return GateDecision("code", "rules", "default")
    return await _classify(http_client, query, request_id)


async def _classify(http_client: httpx.AsyncClient, query: str, request_id: str) -> GateDecision:
    messages = [
        {"role": "system", "content": _CLASSIFY},
        {"role": "user", "content": query},
    ]
    try:
        completion = await chat_completion(
            http_client,
            messages,
            request_id,
            tools=None,
            temperature=0.0,
            response_format=ROUTE_SCHEMA,
            max_tokens=32,
        )
        content = extract_message(completion).get("content") or ""
        route = json.loads(content).get("route")
        if route == "direct" or route == "code":
            return GateDecision(route, "model", "model")
    except Exception as exc:  # noqa: BLE001 - an unclear label stays on the code path
        log.info("gate_classify_failed", request_id=request_id, error=str(exc))
    return GateDecision("code", "rules", "default")
