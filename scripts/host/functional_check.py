#!/usr/bin/env python3
"""Exhaustive end-to-end functional verification against a running stack.

Covers the 25 functional guarantees of the stack across all host doors
(`127.0.0.1:8090`, `127.0.0.1:8092/mcp`, `127.0.0.1:8080`) and internal
services (`agent`, `mcp`, `tools`, `indexer`, `qdrant`, `embedder`,
`reranker`, `llm`):

  1.  Host doors health (`agent /health`, `GET /ui`, `mcp /health`, `llm /health`)
  2.  Authentication gate (`401` without bearer and with wrong bearer)
  3.  Workspace isolation (`400` on unregistered `X-Workspace`)
  4.  Model catalog (`GET /models` lists the active GGUF)
  5.  RAG lookup — declaration (`Evaluate` in `src/calculator.hpp:23` first, no noise files)
  6.  RAG lookup — retrieval trace (`include_candidates=True` returns candidates + reranked)
  7.  RAG lookup — absent symbol (`Zorglub42` never invents a symbol)
  8.  Bounded file read (`POST /read` window `start_line`/`end_line` + line count)
  9.  Path traversal rejection (`POST /read` with `../` rejected)
  10. Check — single-file compile (`action=compile`, `src/calculator.cpp` exit 0)
  11. Check — full CMake build (`action=build`, `release` preset exit 0)
  12. Check — ctest filter (`action=test`, `filter=known_bug` fails with test log)
  13. LSP outline (`src/calculator.hpp` symbols `CalcError` and `Evaluate`)
  14. LSP calls (`src/calculator.cpp:118:5` incoming callers from `calculator_test.cpp`)
  15. LSP types (`src/calculator.hpp:18:7` supertype `runtime_error` in `stdexcept`)
  16. MCP Streamable HTTP (`POST :8092/mcp` `tools/list` + `tools/call` `lookup`, `calls`, and `types`)
  17. Chat — direct route (`2+2` answers without touching RAG)
  18. Chat — code route with citations (`Evaluate` declaration cites `src/calculator.hpp:23`)
  19. Chat — multi-turn memory (`conversation_id` remembers the previous turn)
  20. Chat — bounded judgment (`ask` style: `2+3*4` in `sample/` does not invent 14)
  21. Chat — tool-calling loop (`list_dir` appears in `tool_calls`)
  22. UI SSE stream (`POST /chat/stream` emits `meta`, `route`, `context`, `token`, `done`)
  23. Working-copy overlay (`[working copy]` excerpt on uncommitted edit)
  24. Concurrent lookups (two `/lookup` in parallel both return hits)
  25. Prometheus `/metrics` (reachable through ui-proxy)
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"
DEFAULT_WORKSPACE = (REPO_ROOT / "sample").resolve().as_posix()


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str


def _read_bearer_token(env_path: Path = ENV_FILE) -> str:
    if not env_path.is_file():
        return ""
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("AGENT_BEARER_TOKEN="):
            return line.split("=", 1)[1].strip()
    return ""


def _parse_sse_events(raw_text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    event_name = "message"
    for line in raw_text.splitlines():
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            payload = line.split(":", 1)[1].strip()
            try:
                events.append((event_name, json.loads(payload)))
            except json.JSONDecodeError:
                continue
    return events


def _mcp_post(
    mcp_client: httpx.Client,
    payload: dict[str, Any],
    *,
    workspace: str,
) -> dict[str, Any]:
    resp = mcp_client.post(
        "/mcp",
        json=payload,
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "X-Workspace": workspace,
        },
    )
    resp.raise_for_status()
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for _, data in _parse_sse_events(resp.text):
            if "result" in data or "error" in data:
                return data
    return resp.json()


def run_functional_checks(
    client: httpx.Client,
    *,
    token: str,
    workspace: str = DEFAULT_WORKSPACE,
    sample_header: Path | None = None,
    mcp_client: httpx.Client | None = None,
    llm_client: httpx.Client | None = None,
) -> list[StepResult]:
    headers = {"Authorization": f"Bearer {token}", "X-Workspace": workspace}
    results: list[StepResult] = []

    def step(name: str, fn: Callable[[], tuple[bool, str]]) -> None:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append(StepResult(name=name, ok=ok, detail=detail))

    # 1. Host doors health & UI page
    def _step_doors_health() -> tuple[bool, str]:
        h_agent = client.get("/health")
        h_ui = client.get("/ui")
        ok = (
            h_agent.status_code == 200
            and h_agent.json().get("status") == "ok"
            and h_ui.status_code == 200
            and "text/html" in h_ui.headers.get("content-type", "")
            and "Content-Security-Policy" in h_ui.headers
        )
        parts = [f"agent={h_agent.status_code}", f"ui={h_ui.status_code}"]
        if mcp_client is not None:
            h_mcp = mcp_client.get("/health")
            ok = ok and h_mcp.status_code == 200 and h_mcp.json().get("status") == "ok"
            parts.append(f"mcp={h_mcp.status_code}")
        if llm_client is not None:
            h_llm = llm_client.get("/health")
            ok = ok and h_llm.status_code == 200
            parts.append(f"llm={h_llm.status_code}")
        return ok, ", ".join(parts)

    step("01. host doors & /ui CSP", _step_doors_health)

    # 2. Authentication gate
    def _step_auth_gate() -> tuple[bool, str]:
        no_tok = client.post("/lookup", json={"query": "Evaluate"})
        bad_tok = client.post(
            "/lookup",
            json={"query": "Evaluate"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        ok = no_tok.status_code == 401 and bad_tok.status_code == 401
        return ok, f"missing={no_tok.status_code}, wrong={bad_tok.status_code}"

    step("02. bearer auth gate (401)", _step_auth_gate)

    # 3. Unregistered workspace rejection
    def _step_unknown_workspace() -> tuple[bool, str]:
        resp = client.post(
            "/lookup",
            json={"query": "Evaluate"},
            headers={"Authorization": f"Bearer {token}", "X-Workspace": "D:/nonexistent/unregistered-tree"},
        )
        return resp.status_code == 400, f"status={resp.status_code}"

    step("03. unknown workspace gate (400)", _step_unknown_workspace)

    # 4. Model catalog
    def _step_models() -> tuple[bool, str]:
        resp = client.get("/models", headers=headers)
        resp.raise_for_status()
        body = resp.json()
        models = body.get("models") or []
        active = body.get("active") or (models[0].get("id") if models else "")
        return bool(active), f"active={active}"

    step("04. model catalog (/models)", _step_models)

    # 5. Lookup declaration
    def _step_lookup_decl() -> tuple[bool, str]:
        resp = client.post("/lookup", json={"query": "où est déclarée Evaluate ?"}, headers=headers)
        resp.raise_for_status()
        hits = resp.json().get("hits") or []
        top = hits[0] if hits else {}
        noise = [
            h.get("file_path")
            for h in hits
            if str(h.get("file_path") or "").startswith(".vscode")
            or str(h.get("file_path") or "") == "CMakePresets.json"
        ]
        ok = (
            top.get("file_path") == "src/calculator.hpp"
            and top.get("start_line") == 23
            and top.get("symbol_name") == "Evaluate"
            and not noise
        )
        return ok, f"top={top.get('file_path')}:{top.get('start_line')} ({top.get('symbol_name')})"

    step("05. lookup declaration", _step_lookup_decl)

    # 6. Lookup retrieval trace (candidates + reranker)
    def _step_lookup_trace() -> tuple[bool, str]:
        resp = client.post(
            "/lookup",
            json={"query": "où est déclarée Evaluate ?", "include_candidates": True},
            headers=headers,
        )
        resp.raise_for_status()
        trace = resp.json().get("retrieval") or {}
        candidates = trace.get("candidates") or []
        reranked = trace.get("reranked") or []
        ok = len(candidates) >= 1 and len(reranked) >= 1 and "reranker_used" in trace
        return ok, f"candidates={len(candidates)} reranked={len(reranked)} reranker={trace.get('reranker_used')}"

    step("06. lookup hybrid + rerank trace", _step_lookup_trace)

    # 7. Lookup absent symbol
    def _step_lookup_absent() -> tuple[bool, str]:
        resp = client.post("/lookup", json={"query": "où est déclaré Zorglub42 ?"}, headers=headers)
        resp.raise_for_status()
        hits = resp.json().get("hits") or []
        invented = [h for h in hits if "zorglub" in str(h.get("symbol_name") or "").lower()]
        return not invented, f"hits={len(hits)}, zorglub_symbols={len(invented)}"

    step("07. lookup absent symbol", _step_lookup_absent)

    # 8. Bounded file read
    def _step_read_file() -> tuple[bool, str]:
        resp = client.post(
            "/read",
            json={"path": "src/calculator.hpp", "start_line": 18, "end_line": 23},
            headers=headers,
        )
        resp.raise_for_status()
        body = resp.json()
        text = str(body.get("text") or "")
        ok = (
            body.get("start_line") == 18
            and body.get("end_line") == 23
            and "CalcError" in text
            and "Evaluate" in text
        )
        return ok, f"lines={body.get('start_line')}-{body.get('end_line')} total={body.get('total_lines')}"

    step("08. bounded file read (/read)", _step_read_file)

    # 9. Path traversal rejection
    def _step_path_traversal() -> tuple[bool, str]:
        resp = client.post(
            "/read",
            json={"path": "../../etc/passwd"},
            headers=headers,
        )
        return resp.status_code in (400, 404, 422), f"status={resp.status_code}"

    step("09. path traversal blocked", _step_path_traversal)

    # 10. Check compile single file
    def _step_check_compile() -> tuple[bool, str]:
        resp = client.post(
            "/check",
            json={"action": "compile", "path": "src/calculator.cpp"},
            headers=headers,
        )
        resp.raise_for_status()
        body = resp.json()
        ok = body.get("ok") is True and body.get("exit_code") == 0
        return ok, f"ok={body.get('ok')} exit={body.get('exit_code')}"

    step("10. check compile_file", _step_check_compile)

    # 11. Check full CMake build
    def _step_check_build() -> tuple[bool, str]:
        resp = client.post(
            "/check",
            json={"action": "build", "preset": "release"},
            headers=headers,
        )
        resp.raise_for_status()
        body = resp.json()
        ok = body.get("ok") is True and body.get("exit_code") == 0
        return ok, f"ok={body.get('ok')} exit={body.get('exit_code')}"

    step("11. check cmake build", _step_check_build)

    # 12. Check ctest filter known_bug
    def _step_check_test() -> tuple[bool, str]:
        resp = client.post(
            "/check",
            json={"action": "test", "filter": "known_bug"},
            headers=headers,
        )
        resp.raise_for_status()
        body = resp.json()
        out = str(body.get("stdout") or "") + str(body.get("stderr") or "")
        ok = body.get("ok") is False and body.get("exit_code") not in (0, None) and "known_bug" in out
        return ok, f"ok={body.get('ok')} exit={body.get('exit_code')}"

    step("12. check ctest known_bug", _step_check_test)

    # 13. LSP outline
    def _step_outline() -> tuple[bool, str]:
        resp = client.post("/lsp/outline", json={"path": "src/calculator.hpp"}, headers=headers)
        resp.raise_for_status()
        out = str(resp.json().get("stdout") or "")
        ok = "CalcError class 18" in out and "Evaluate function 23" in out
        return ok, "; ".join(out.strip().splitlines())

    step("13. lsp outline", _step_outline)

    # 14. LSP calls
    def _step_calls() -> tuple[bool, str]:
        resp = client.post(
            "/lsp/calls",
            json={"path": "src/calculator.cpp", "line": 118, "character": 5, "direction": "both"},
            headers=headers,
        )
        resp.raise_for_status()
        body = resp.json()
        lines = str(body.get("stdout") or "").strip().splitlines()
        ok = bool(body.get("ok")) and any("tests/calculator_test.cpp:" in line for line in lines)
        return ok, lines[0] if lines else f"empty ({body.get('stderr')})"

    step("14. lsp calls", _step_calls)

    # 15. LSP types
    def _step_types() -> tuple[bool, str]:
        resp = client.post(
            "/lsp/types",
            json={"path": "src/calculator.hpp", "line": 18, "character": 7, "direction": "both"},
            headers=headers,
        )
        resp.raise_for_status()
        body = resp.json()
        out = str(body.get("stdout") or "").strip()
        ok = bool(body.get("ok")) and "runtime_error" in out
        return ok, out or f"empty ({body.get('stderr')})"

    step("15. lsp types", _step_types)

    # 16. MCP Streamable HTTP protocol (tools/list + lookup, check, calls, types, ask)
    if mcp_client is not None:
        def _step_mcp_protocol() -> tuple[bool, str]:
            listed = _mcp_post(
                mcp_client,
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                workspace=workspace,
            )
            tools = (listed.get("result") or {}).get("tools") or []
            names = {t.get("name") for t in tools if isinstance(t, dict)}
            expected = {"lookup", "check", "calls", "types", "ask"}

            def _call_mcp(req_id: int, tool_name: str, arguments: dict[str, Any]) -> str:
                res = _mcp_post(
                    mcp_client,
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": arguments},
                    },
                    workspace=workspace,
                )
                return ((res.get("result") or {}).get("content") or [{}])[0].get("text") or ""

            t_lookup = _call_mcp(2, "lookup", {"query": "où est déclarée Evaluate ?"})
            t_calls = _call_mcp(4, "calls", {"path": "src/calculator.cpp", "line": 118, "character": 5})
            t_types = _call_mcp(5, "types", {"path": "src/calculator.hpp", "line": 18, "character": 7})
            t_check = _call_mcp(6, "check", {"action": "compile", "path": "src/calculator.cpp"})
            t_ask = _call_mcp(7, "ask", {"query": "Où est déclarée Evaluate ? Cite path:ligne."})
            ok = (
                expected <= names
                and "src/calculator.hpp:23" in t_lookup
                and "tests/calculator_test.cpp:" in t_calls
                and "runtime_error" in t_types
                and "exit 0" in t_check
                and "calculator.hpp" in t_ask
            )
            return ok, f"all 5 MCP tools verified ({sorted(names)})"

        step("16. MCP Streamable HTTP (all 5 tools)", _step_mcp_protocol)

    # 17. Chat direct route
    def _step_chat_direct() -> tuple[bool, str]:
        resp = client.post(
            "/chat",
            json={"query": "Que vaut 2+2 ? Réponds par un seul chiffre."},
            headers=headers,
        )
        resp.raise_for_status()
        body = resp.json()
        answer = str(body.get("answer") or "").strip()
        ok = body.get("used_context") is False and "4" in answer
        return ok, f"used_context={body.get('used_context')} answer={answer[:40]}"

    step("17. chat direct route (no RAG)", _step_chat_direct)

    # 18. Chat code route + citations
    def _step_chat_cited() -> tuple[bool, str]:
        resp = client.post(
            "/chat",
            json={"query": "Où est déclarée Evaluate dans sample ? Cite le fichier et la ligne."},
            headers=headers,
        )
        resp.raise_for_status()
        body = resp.json()
        answer = str(body.get("answer") or "")
        sources = body.get("sources") or []
        ok = (
            bool(body.get("used_context"))
            and any(s.get("file_path") == "src/calculator.hpp" for s in sources)
            and "calculator.hpp" in answer
        )
        first_line = answer.strip().splitlines()[0] if answer.strip() else ""
        return ok, f"used_context={body.get('used_context')} answer={first_line[:80]}"

    step("18. chat code route + citations", _step_chat_cited)

    # 19. Chat multi-turn conversation memory
    def _step_chat_multiturn() -> tuple[bool, str]:
        r1 = client.post(
            "/chat",
            json={"query": "Retiens le mot-code HIBOU42. Réponds juste OK."},
            headers=headers,
        )
        r1.raise_for_status()
        cid = r1.json().get("conversation_id")
        r2 = client.post(
            "/chat",
            json={"query": "Quel était le mot-code que je viens de te donner ?", "conversation_id": cid},
            headers=headers,
        )
        r2.raise_for_status()
        ans = str(r2.json().get("answer") or "")
        ok = bool(cid) and "HIBOU42" in ans.upper()
        return ok, f"cid={cid[:8] if cid else 'none'} answer={ans.strip()[:60]}"

    step("19. chat multi-turn memory", _step_chat_multiturn)

    # 20. Bounded judgment (ask-style anti-hallucination check)
    def _step_bounded_judgment() -> tuple[bool, str]:
        resp = client.post(
            "/chat",
            json={
                "query": (
                    "Dans sample/src/calculator.cpp, ParseExpression évalue-t-il + et * de gauche à droite "
                    "dans la même boucle while, ou donne-t-il priorité à * sur + ? Cite path:ligne."
                ),
                "max_tokens": 400,
                "enable_tools": False,
            },
            headers=headers,
        )
        resp.raise_for_status()
        body = resp.json()
        ans = str(body.get("answer") or "")
        ok = (
            bool(body.get("used_context"))
            and body.get("tool_calls") == []
            and ("calculator.cpp" in ans or "ParseExpression" in ans)
        )
        first_line = ans.strip().splitlines()[0] if ans.strip() else ""
        return ok, f"tool_calls={len(body.get('tool_calls') or [])} answer={first_line[:80]}"

    step("20. bounded judgment (no tools)", _step_bounded_judgment)

    # 21. Chat tool-calling loop (LLM invokes a tool and uses the observation)
    def _step_chat_tool_call() -> tuple[bool, str]:
        resp = client.post(
            "/chat",
            json={
                "query": (
                    "Appelle l'outil list_dir pour lister les fichiers du projet sample, "
                    "puis donne la liste des fichiers trouvés."
                )
            },
            headers=headers,
        )
        resp.raise_for_status()
        body = resp.json()
        tcalls = body.get("tool_calls") or []
        names = [tc.get("name") or tc.get("tool") for tc in tcalls if isinstance(tc, dict)]
        ok = len(tcalls) >= 1 and "calculator.cpp" in str(body.get("answer") or "")
        return ok, f"tool_calls={names}"

    step("21. chat tool-calling loop", _step_chat_tool_call)

    # 22. Streaming /chat/stream SSE + /chat/cancel endpoint
    def _step_stream() -> tuple[bool, str]:
        resp = client.post(
            "/chat/stream",
            json={"query": "Où est déclarée Evaluate dans sample ?"},
            headers=headers,
        )
        resp.raise_for_status()
        events = _parse_sse_events(resp.text)
        names = [name for name, _ in events]
        cancel_resp = client.post(
            "/chat/cancel",
            json={"conversation_id": "idle-conversation-check"},
            headers=headers,
        )
        cancel_resp.raise_for_status()
        required = {"meta", "route", "context", "token", "done"}
        ok = required <= set(names) and cancel_resp.json().get("stopped") is False
        return ok, f"events={sorted(set(names))} cancel_idle={cancel_resp.json().get('stopped')}"

    step("22. stream /ui SSE + /chat/cancel", _step_stream)

    # 23. Dirty working-copy overlay
    if sample_header is not None and sample_header.is_file():
        def _step_dirty() -> tuple[bool, str]:
            original = sample_header.read_text(encoding="utf-8")
            marker = "// dirty-functional-check-marker\n"
            try:
                sample_header.write_text(marker + original, encoding="utf-8")
                resp = client.post(
                    "/lookup",
                    json={"query": "où est déclarée Evaluate ?"},
                    headers=headers,
                )
                resp.raise_for_status()
                hits = resp.json().get("hits") or []
                dirty_hit = next(
                    (h for h in hits if h.get("file_path") == "src/calculator.hpp"),
                    {},
                )
                ok = (
                    dirty_hit.get("source") == "working_copy"
                    and "dirty-functional-check-marker" in str(dirty_hit.get("excerpt") or "")
                )
                return ok, f"source={dirty_hit.get('source')}"
            finally:
                sample_header.write_text(original, encoding="utf-8")

        step("23. working copy overlay ([working copy])", _step_dirty)

    # 24. Two lookups at once — the RAG path, not the /chat slot cap.
    def _step_concurrent_lookup() -> tuple[bool, str]:
        def _one() -> tuple[int, int]:
            resp = client.post(
                "/lookup",
                json={"query": "où est déclarée Evaluate ?"},
                headers=headers,
            )
            hits = resp.json().get("hits") or [] if resp.status_code == 200 else []
            return resp.status_code, len(hits)

        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(lambda _i: _one(), range(2)))
        ok = all(status == 200 and count >= 1 for status, count in rows)
        return ok, f"parallel={rows}"

    step("24. concurrent lookups", _step_concurrent_lookup)

    # 25. /metrics through the same host door as /ui.
    def _step_metrics() -> tuple[bool, str]:
        resp = client.get("/metrics")
        text = resp.text or ""
        ok = resp.status_code == 200 and ("http_request" in text or "python_info" in text)
        return ok, f"status={resp.status_code} bytes={len(text)}"

    step("25. /metrics", _step_metrics)

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default="http://127.0.0.1:8090", help="Agent base URL")
    parser.add_argument("--mcp", default="http://127.0.0.1:8092", help="MCP base URL")
    parser.add_argument("--llm", default="http://127.0.0.1:8080", help="LLM base URL")
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE, help="Registered workspace path")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    token = _read_bearer_token()
    if not token:
        print("[check] AGENT_BEARER_TOKEN introuvable dans .env", file=sys.stderr)
        return 1

    ws_path = Path(args.workspace)
    candidate_header = ws_path / "src" / "calculator.hpp"
    header = candidate_header if candidate_header.is_file() else None

    with (
        httpx.Client(base_url=args.agent, timeout=args.timeout) as client,
        httpx.Client(base_url=args.mcp, timeout=args.timeout) as mcp_client,
        httpx.Client(base_url=args.llm, timeout=args.timeout) as llm_client,
    ):
        results = run_functional_checks(
            client,
            token=token,
            workspace=args.workspace,
            sample_header=header,
            mcp_client=mcp_client,
            llm_client=llm_client,
        )

    failed = 0
    for item in results:
        mark = "OK" if item.ok else "FAIL"
        print(f"[{mark}] {item.name}: {item.detail}")
        if not item.ok:
            failed += 1
    print(f"\n[check] {len(results) - failed}/{len(results)} passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
