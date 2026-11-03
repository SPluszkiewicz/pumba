from __future__ import annotations

import json

import pytest
import token_budget


@pytest.mark.asyncio
async def test_under_budget_is_unchanged() -> None:
    async def fake_count(_c, text, _r):
        return 10

    token_budget.count_tokens = fake_count  # type: ignore[misc]
    messages = [{"role": "user", "content": "hi"}]
    out, n, usage = await token_budget.apply_budget(None, messages, "r", budget=100)
    assert out == messages and n == 10 and usage["compressed"] is False


@pytest.mark.asyncio
async def test_over_budget_collapses_old_tool_results() -> None:
    counts = {"n": 0}

    async def fake_count(_c, text, _r):
        counts["n"] += 1
        return 999 if len(text) > 200 else 5

    token_budget.count_tokens = fake_count  # type: ignore[misc]
    messages = [
        {"role": "system", "content": "s"},
        {"role": "tool", "content": json.dumps({"ok": True, "stdout": "huge-payload " * 20, "stderr": ""})},
    ]
    out, n, usage = await token_budget.apply_budget(None, messages, "r", budget=50)
    assert "collapsed" in out[1]["content"]
    assert n == 5 and usage["compressed"] is True


@pytest.mark.asyncio
async def test_over_budget_asks_the_model_to_summarize(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_count(_c, text, _r):
        return 10 if "Summary of earlier context" in text else 500

    async def fake_summary(_c, _middle, _r):
        return "add() was discussed and left unresolved."

    monkeypatch.setattr(token_budget, "count_tokens", fake_count)
    monkeypatch.setattr(token_budget, "_model_summary", fake_summary)
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "first question " * 40},
        {"role": "assistant", "content": "first answer " * 40},
        {"role": "user", "content": "What is the bug in add?"},
    ]
    out, n, usage = await token_budget.apply_budget(None, messages, "r", budget=50)
    assert n == 10 and usage["compressed"] is True
    assert out[-1]["content"] == "What is the bug in add?"
    assert "add() was discussed" in out[1]["content"]


@pytest.mark.asyncio
async def test_summary_failure_falls_back_to_a_shortened_note(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_count(_c, text, _r):
        return 10 if "Compressed earlier context" in text else 500

    async def fake_summary(_c, _middle, _r):
        raise RuntimeError("model unreachable")

    monkeypatch.setattr(token_budget, "count_tokens", fake_count)
    monkeypatch.setattr(token_budget, "_model_summary", fake_summary)
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "first question " * 40},
        {"role": "assistant", "content": "first answer " * 40},
        {"role": "user", "content": "What is the bug in add?"},
    ]
    out, n, usage = await token_budget.apply_budget(None, messages, "r", budget=50)
    assert n == 10 and usage["compressed"] is True
    assert "first question" in out[1]["content"]
