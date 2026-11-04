"""Unit tests for scripts/host/functional_check.py (MockTransport, no live containers)."""

from __future__ import annotations

import json
from pathlib import Path

import functional_check
import httpx


def test_run_functional_checks_passes_all_22_steps(tmp_path: Path) -> None:
    header = tmp_path / "calculator.hpp"
    header.write_text("int Evaluate(int x);\n", encoding="utf-8")

    def agent_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/ui":
            return httpx.Response(
                200,
                text="<html></html>",
                headers={"content-type": "text/html; charset=utf-8", "Content-Security-Policy": "default-src 'self'"},
            )
        auth = request.headers.get("authorization", "")
        if auth != "Bearer secret":
            return httpx.Response(401, json={"detail": "unauthorized"})
        if request.headers.get("x-workspace") == "D:/nonexistent/unregistered-tree":
            return httpx.Response(400, json={"detail": "workspace is not registered"})
        if path == "/models":
            return httpx.Response(
                200,
                json={"active": "Qwen2.5-7B-Instruct-Q5_K_M", "models": [{"id": "Qwen2.5-7B-Instruct-Q5_K_M"}]},
            )
        if path == "/lookup":
            body = json.loads(request.read())
            if "Zorglub" in body["query"]:
                return httpx.Response(200, json={"hits": []})
            if "dirty-functional-check-marker" in header.read_text(encoding="utf-8"):
                return httpx.Response(
                    200,
                    json={
                        "hits": [
                            {
                                "file_path": "src/calculator.hpp",
                                "start_line": 1,
                                "end_line": 24,
                                "symbol_name": "Evaluate",
                                "source": "working_copy",
                                "excerpt": "// dirty-functional-check-marker\nint Evaluate(int x);",
                            }
                        ]
                    },
                )
            payload: dict = {
                "hits": [
                    {
                        "file_path": "src/calculator.hpp",
                        "start_line": 23,
                        "end_line": 23,
                        "symbol_name": "Evaluate",
                        "source": "index",
                        "excerpt": "int Evaluate(std::string_view expression);",
                    }
                ]
            }
            if body.get("include_candidates"):
                payload["retrieval"] = {
                    "reranker_used": True,
                    "candidates": [{"file_path": "src/calculator.hpp", "start_line": 23, "end_line": 23}],
                    "reranked": [{"file_path": "src/calculator.hpp", "start_line": 23, "end_line": 23}],
                }
            return httpx.Response(200, json=payload)
        if path == "/read":
            body = json.loads(request.read())
            if ".." in body.get("path", ""):
                return httpx.Response(400, json={"detail": "invalid path"})
            return httpx.Response(
                200,
                json={
                    "path": "src/calculator.hpp",
                    "start_line": 18,
                    "end_line": 23,
                    "total_lines": 25,
                    "truncated": False,
                    "text": "class CalcError {};\nint Evaluate(std::string_view expression);\n",
                },
            )
        if path == "/check":
            body = json.loads(request.read())
            if body.get("action") in ("compile", "build"):
                return httpx.Response(200, json={"ok": True, "exit_code": 0, "stdout": "built", "stderr": ""})
            return httpx.Response(
                200,
                json={"ok": False, "exit_code": 8, "stdout": "known_bug: multiplication", "stderr": ""},
            )
        if path == "/lsp/outline":
            return httpx.Response(
                200,
                json={"ok": True, "stdout": "calc  7\nCalcError class 18\nEvaluate function 23\n"},
            )
        if path == "/lsp/calls":
            return httpx.Response(
                200,
                json={"ok": True, "stdout": "CATCH2_INTERNAL_TEST_0 tests/calculator_test.cpp:5\n"},
            )
        if path == "/lsp/types":
            return httpx.Response(
                200,
                json={"ok": True, "stdout": "runtime_error stdexcept:219\n"},
            )
        if path == "/chat":
            body = json.loads(request.read())
            query = body.get("query", "")
            if "2+2" in query:
                return httpx.Response(
                    200,
                    json={
                        "used_context": False,
                        "sources": [],
                        "tool_calls": [],
                        "answer": "4",
                        "conversation_id": "c1",
                    },
                )
            if "HIBOU42" in query:
                return httpx.Response(
                    200,
                    json={
                        "used_context": False,
                        "sources": [],
                        "tool_calls": [],
                        "answer": "OK",
                        "conversation_id": "conv-hibou",
                    },
                )
            if body.get("conversation_id") == "conv-hibou":
                return httpx.Response(
                    200,
                    json={
                        "used_context": False,
                        "sources": [],
                        "tool_calls": [],
                        "answer": "Le mot-code est HIBOU42.",
                        "conversation_id": "conv-hibou",
                    },
                )
            if "list_files" in query:
                return httpx.Response(
                    200,
                    json={
                        "used_context": True,
                        "sources": [],
                        "tool_calls": [{"name": "list_files", "arguments": {}}],
                        "answer": "src/calculator.cpp, src/calculator.hpp",
                        "conversation_id": "c-tools",
                    },
                )
            if body.get("enable_tools") is False:
                return httpx.Response(
                    200,
                    json={
                        "used_context": True,
                        "sources": [{"file_path": "src/calculator.cpp", "start_line": 24, "end_line": 47}],
                        "tool_calls": [],
                        "answer": "ParseExpression évalue dans la même boucle (src/calculator.cpp:26).",
                        "conversation_id": "c2",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "used_context": True,
                    "sources": [{"file_path": "src/calculator.hpp", "start_line": 23, "end_line": 23}],
                    "tool_calls": [],
                    "answer": "Declared in src/calculator.hpp:23.",
                    "conversation_id": "c3",
                },
            )
        if path == "/chat/cancel":
            return httpx.Response(200, json={"stopped": False})
        if path == "/chat/stream":
            sse = (
                "event: meta\ndata: {\"conversation_id\": \"c4\"}\n\n"
                "event: route\ndata: {\"route\": \"code\"}\n\n"
                "event: context\ndata: {\"used\": 512, \"window\": 32768}\n\n"
                "event: token\ndata: {\"text\": \"src/calculator.hpp:23\"}\n\n"
                "event: done\ndata: {\"answer\": \"src/calculator.hpp:23\"}\n\n"
            )
            return httpx.Response(200, text=sse)
        raise AssertionError(f"unexpected agent path: {path}")

    def mcp_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        body = json.loads(request.read())
        if body["method"] == "tools/list":
            tools = [{"name": n} for n in ("lookup", "check", "outline", "calls", "types", "ask")]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}})
        if body["method"] == "tools/call":
            tname = body["params"]["name"]
            outputs = {
                "lookup": "src/calculator.hpp:23-23  Evaluate [index]",
                "outline": "CalcError class 18\nEvaluate function 23",
                "calls": "CATCH2_INTERNAL_TEST_0 tests/calculator_test.cpp:5",
                "types": "runtime_error stdexcept:219",
                "check": "compile exit 0",
                "ask": "Declared in src/calculator.hpp:23.",
            }
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"content": [{"type": "text", "text": outputs[tname]}]},
                },
            )
        raise AssertionError(f"unexpected mcp call: {body}")

    def llm_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok"})

    with (
        httpx.Client(base_url="http://agent.test", transport=httpx.MockTransport(agent_handler)) as client,
        httpx.Client(base_url="http://mcp.test", transport=httpx.MockTransport(mcp_handler)) as mcp_client,
        httpx.Client(base_url="http://llm.test", transport=httpx.MockTransport(llm_handler)) as llm_client,
    ):
        results = functional_check.run_functional_checks(
            client,
            token="secret",
            workspace="D:/rest/pumba/sample",
            sample_header=header,
            mcp_client=mcp_client,
            llm_client=llm_client,
        )

    assert len(results) == 23
    assert all(r.ok for r in results), [(r.name, r.detail) for r in results if not r.ok]
    assert "dirty-functional-check-marker" not in header.read_text(encoding="utf-8")
