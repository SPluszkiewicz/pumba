from __future__ import annotations

import json

import pytest
import reflect


@pytest.mark.asyncio
async def test_disabled_returns_original(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reflect, "ENABLED", False)
    out = await reflect.reflect_answer(None, "draft", [], "r")
    assert out == {"answer": "draft", "removed": 0}


@pytest.mark.asyncio
async def test_schema_call_rewrites_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reflect, "ENABLED", True)

    async def fake_completion(*_a, **_k):
        return {"choices": [{"message": {"content": json.dumps({"answer": "kept", "removed": 2})}}]}

    monkeypatch.setattr(reflect, "chat_completion", fake_completion)
    sources = [{"file_path": "a.cpp", "start_line": 1, "end_line": 2}]
    out = await reflect.reflect_answer(None, "draft with extra", sources, "r")
    assert out == {"answer": "kept", "removed": 2}


@pytest.mark.asyncio
async def test_failure_keeps_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reflect, "ENABLED", True)

    async def boom(*_a, **_k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(reflect, "chat_completion", boom)
    out = await reflect.reflect_answer(None, "draft", [], "r")
    assert out == {"answer": "draft", "removed": 0}
