from __future__ import annotations

import httpx
import pytest
from agent_client import (
    AgentError,
    ask_agent,
    format_chat_result,
    format_check,
    format_fact,
    format_lookup,
    format_read,
)


def _handler(request: httpx.Request) -> httpx.Response:
    body = request.read()
    assert b'"tools": false' in body or b'"tools":false' in body
    assert str(request.url) == "http://agent-test:8090/chat"
    assert request.headers.get("authorization") == "Bearer test-token"
    assert b"How does chunking work" in body
    return httpx.Response(
        200,
        json={
            "answer": "Via tree-sitter.",
            "sources": [
                {
                    "file_path": "src/chunker.cpp",
                    "start_line": 10,
                    "end_line": 40,
                    "symbol_name": "chunk_file",
                }
            ],
            "used_context": True,
            "tool_calls": [],
            "conversation_id": "cid-1",
        },
    )


@pytest.mark.asyncio
async def test_ask_agent_forwards_query_and_bearer() -> None:
    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://agent-test:8090") as client:
        # ask_agent builds AGENT_BASE_URL + /chat; MockTransport matches any host.
        payload = await ask_agent("How does chunking work?", client=client)
    assert payload["answer"] == "Via tree-sitter."
    assert payload["conversation_id"] == "cid-1"


@pytest.mark.asyncio
async def test_ask_agent_sends_conversation_id() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["json"] = request.read()
        return httpx.Response(200, json={"answer": "ok", "conversation_id": "cid-2"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await ask_agent("follow up", conversation_id=" cid-1 ", client=client)
    assert b"cid-1" in seen["json"]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_ask_agent_rejects_empty_query() -> None:
    with pytest.raises(AgentError, match="empty"):
        await ask_agent("   ")


@pytest.mark.asyncio
async def test_ask_agent_maps_503() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="busy")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(AgentError, match="busy") as exc:
            await ask_agent("hello", client=client)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_ask_agent_maps_401() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="nope")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(AgentError, match="bearer") as exc:
            await ask_agent("hello", client=client)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_ask_agent_timeout() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(AgentError, match="timed out"):
            await ask_agent("hello", client=client)


def test_format_chat_result_includes_sources_and_id() -> None:
    text = format_chat_result(
        {
            "answer": "It compiles.",
            "sources": [
                {"file_path": "src/main.cpp", "start_line": 1, "end_line": 8, "symbol_name": "main"},
                {"file_path": "CMakeLists.txt", "start_line": 1, "end_line": 5},
            ],
            "conversation_id": "abc",
        }
    )
    assert "It compiles." in text
    assert "src/main.cpp:1-8 (main)" in text
    assert "CMakeLists.txt:1-5" in text
    assert "conversation_id" not in text


def test_format_chat_result_empty_answer() -> None:
    assert "(empty answer)" in format_chat_result({})


def test_format_lookup_quotes_the_excerpt() -> None:
    text = format_lookup(
        {
            "hits": [
                {
                    "file_path": "src/calculator.hpp",
                    "start_line": 4,
                    "end_line": 12,
                    "symbol_name": "add",
                    "excerpt": "int add(int, int);",
                }
            ]
        }
    )
    assert "src/calculator.hpp:4-12 (add)" in text
    assert "int add(int, int);" in text


def test_format_lookup_empty() -> None:
    assert format_lookup({"hits": []}) == "No indexed match."


def test_format_check_is_the_log_and_the_exit_code() -> None:
    text = format_check({"action": "test", "exit_code": 1, "stdout": "1 failed", "stderr": "boom"})
    assert text.startswith("test exit 1")
    assert "1 failed" in text
    assert "boom" in text


def test_format_fact_is_the_symbol_list() -> None:
    text = format_fact({"ok": True, "stdout": "add fn 4\n", "exit_code": 0})
    assert text == "add fn 4"


def test_format_read_includes_the_file_text() -> None:
    text = format_read({"path": "src/calculator.hpp", "start_line": 1, "end_line": 3, "text": "class A {}\n"})
    assert text.startswith("src/calculator.hpp:1-3")
    assert "class A {}" in text
