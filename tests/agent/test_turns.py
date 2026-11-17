"""Stopping an in-flight model call, and the next message waiting for its trace."""

from __future__ import annotations

import asyncio

import httpx
import main
import pytest
import tool_loop
from fastapi.testclient import TestClient
from starlette.requests import Request
from turns import TurnBusy, TurnCancelled, Turns, until_cancelled

_TRACE_CALL = {
    "name": "edit",
    "arguments": {"path": "src/foo.cpp", "old_string": "x" * 200, "new_string": "y"},
    "result_summary": "edit failed: snippet was not unique",
    "ok": False,
    "line": "snippet was not unique",
}


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/chat",
            "raw_path": b"/chat",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 123),
            "server": ("test", 80),
        }
    )


@pytest.mark.asyncio
async def test_until_cancelled_aborts_the_inner_call() -> None:
    flag = asyncio.Event()

    async def slow() -> str:
        await asyncio.sleep(30)
        return "done"

    async def trip() -> None:
        await asyncio.sleep(0.01)
        flag.set()

    stopper = asyncio.create_task(trip())
    with pytest.raises(TurnCancelled):
        await until_cancelled(slow(), flag)
    await stopper


@pytest.mark.asyncio
async def test_until_cancelled_awaits_the_cancelled_call() -> None:
    flag = asyncio.Event()
    cleaned = asyncio.Event()

    async def slow() -> str:
        try:
            await asyncio.sleep(30)
            return "done"
        except asyncio.CancelledError:
            cleaned.set()
            raise

    async def trip() -> None:
        await asyncio.sleep(0.01)
        flag.set()

    stopper = asyncio.create_task(trip())
    with pytest.raises(TurnCancelled):
        await until_cancelled(slow(), flag)
    await stopper
    assert cleaned.is_set()


def test_start_refuses_an_in_flight_conversation() -> None:
    turns = Turns()
    turns.start("c1")
    with pytest.raises(TurnBusy):
        turns.start("c1")
    turns.finish("c1")
    turns.start("c1")
    turns.finish("c1")


def test_chat_remembers_the_trace_and_omits_zero_nudges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BEARER_TOKEN", "secret-token")

    async def loop(*_args, **_kwargs):
        return {"answer": "done", "tool_calls": [_TRACE_CALL]}

    monkeypatch.setattr(main, "run_tool_loop", loop)
    with TestClient(main.app) as client:
        resp = client.post(
            "/chat",
            json={"query": "hello"},
            headers={"Authorization": "Bearer secret-token"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "nudges" not in body
    history = main.STORE.history(body["conversation_id"])
    assert history[1]["role"] == "system"
    assert "- edit path=src/foo.cpp ok=false: snippet was not unique" in history[1]["content"]
    assert "old_string" not in history[1]["content"]
    main.STORE._items.pop(body["conversation_id"], None)  # noqa: SLF001


def test_chat_returns_nudges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BEARER_TOKEN", "secret-token")

    async def loop(*_args, **_kwargs):
        return {"answer": "done", "tool_calls": [], "nudges": 2}

    monkeypatch.setattr(main, "run_tool_loop", loop)
    with TestClient(main.app) as client:
        resp = client.post(
            "/chat",
            json={"query": "hello"},
            headers={"Authorization": "Bearer secret-token"},
        )
    assert resp.status_code == 200
    assert resp.json()["nudges"] == 2
    main.STORE._items.pop(resp.json()["conversation_id"], None)  # noqa: SLF001


@pytest.mark.asyncio
async def test_second_chat_waits_until_remember(monkeypatch: pytest.MonkeyPatch) -> None:
    cid = "al8-wait"
    entered = asyncio.Event()
    release = asyncio.Event()
    seen: list[list[dict[str, str]]] = []

    async def loop(*_args, **kwargs):
        seen.append(list(kwargs.get("history") or []))
        if len(seen) == 1:
            entered.set()
            await release.wait()
            return {"answer": "one", "tool_calls": [_TRACE_CALL], "nudges": 1}
        return {"answer": "two", "tool_calls": []}

    monkeypatch.setattr(main, "run_tool_loop", loop)
    main.app.state.http_client = httpx.AsyncClient()
    request = _request()
    first = asyncio.create_task(
        main.chat(main.ChatRequest(query="hello", conversation_id=cid), request)
    )
    try:
        await entered.wait()
        second = asyncio.create_task(
            main.chat(main.ChatRequest(query="hi", conversation_id=cid), request)
        )
        for _ in range(5):
            await asyncio.sleep(0)
        assert len(seen) == 1
        release.set()
        one, two = await asyncio.gather(first, second)
    finally:
        release.set()
        main.STORE._items.pop(cid, None)  # noqa: SLF001
        await main.app.state.http_client.aclose()
    assert one.nudges == 1
    assert "nudges" not in two.model_dump()
    assert seen[0] == []
    assert seen[1][1]["role"] == "system"
    assert "snippet was not unique" in seen[1][1]["content"]
    assert "old_string" not in seen[1][1]["content"]


@pytest.mark.asyncio
async def test_cancelling_the_waiting_chat_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    cid = "al8-waiter"
    entered = asyncio.Event()
    release = asyncio.Event()
    seen: list[int] = []

    async def loop(*_args, **_kwargs):
        seen.append(1)
        if len(seen) == 1:
            entered.set()
            await release.wait()
        return {"answer": "one", "tool_calls": []}

    monkeypatch.setattr(main, "run_tool_loop", loop)
    main.app.state.http_client = httpx.AsyncClient()
    request = _request()
    first = asyncio.create_task(
        main.chat(main.ChatRequest(query="hello", conversation_id=cid), request)
    )
    second: asyncio.Task[main.ChatResponse] | None = None
    try:
        await entered.wait()
        second = asyncio.create_task(
            main.chat(main.ChatRequest(query="hi", conversation_id=cid), request)
        )
        for _ in range(5):
            await asyncio.sleep(0)
        assert len(seen) == 1
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        release.set()
        await first
        contents = [message["content"] for message in main.STORE.history(cid)]
        assert "hello" in contents
        assert "hi" not in contents
        assert len(seen) == 1
    finally:
        release.set()
        if second is not None and not second.done():
            second.cancel()
        main.STORE._items.pop(cid, None)  # noqa: SLF001
        await main.app.state.http_client.aclose()


class _CancelClient:
    def __init__(self) -> None:
        self.posts: list[dict[str, object]] = []

    async def post(self, url: str, headers: dict[str, str] | None = None, timeout: float | None = None, **_kwargs):
        self.posts.append({"url": url, "headers": headers or {}, "timeout": timeout})
        return httpx.Response(204)


@pytest.mark.asyncio
async def test_cancel_posts_to_tools_and_drops_the_open_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    order: list[str] = []

    async def raw(_client, name, _arguments, _request_id):
        order.append(f"tool-start:{name}")
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            order.append(f"tool-cancelled:{name}")
            cancelled.set()
            raise
        order.append(f"tool-end:{name}")
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(main, "_RAW_EXECUTE_TOOL_CALL", raw)
    client = _CancelClient()

    async def loop(_client, *_args, **kwargs):
        tool_loop._summary("search", {"pattern": "foo"}, {"ok": True, "stdout": "hit", "stderr": ""})
        flag = kwargs["cancel_event"]

        async def trip() -> None:
            await started.wait()
            flag.set()

        asyncio.create_task(trip())
        await tool_loop.execute_tool_call(_client, "build", {"preset": "release"}, "req-cancel")

    monkeypatch.setattr(main, "run_tool_loop", loop)
    response = await asyncio.wait_for(
        main.execute_turn(
            main.ChatRequest(query="hello", conversation_id="al8-cancel"),
            "req-cancel",
            client,  # type: ignore[arg-type]
            cancel_event=asyncio.Event(),
        ),
        timeout=3,
    )
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    urls = {post["url"] for post in client.posts}
    assert urls == {f"{main.TOOLS_BASE_URL.rstrip('/')}/cancel"}
    assert all(post["headers"]["X-Request-Id"] == "req-cancel" for post in client.posts)
    assert all(post["timeout"] == main.CANCEL_TIMEOUT_SECONDS for post in client.posts)
    assert order[0] == "tool-start:build"
    assert "tool-end:build" not in order
    assert "tool-cancelled:build" in order
    assert response.answer == "Stopped."
    assert [call.name for call in response.tool_calls] == ["search"]
    assert "nudges" not in response.model_dump()
    text = main.STORE.history("al8-cancel")[1]["content"]
    assert "- search pattern=foo ok=true: hit" in text
    assert "build" not in text
    main.STORE._items.pop("al8-cancel", None)  # noqa: SLF001
