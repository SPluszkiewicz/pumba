from __future__ import annotations

import eval_tools


def test_args_match_subset() -> None:
    assert eval_tools.args_match({"preset": "release", "extra": 1}, {"preset": "release"})
    assert not eval_tools.args_match({"preset": "debug"}, {"preset": "release"})


def test_first_tool() -> None:
    name, args = eval_tools.first_tool({"tool_calls": [{"name": "build", "arguments": {"preset": "release"}}]})
    assert name == "build" and args["preset"] == "release"
    assert eval_tools.first_tool({"tool_calls": []}) == (None, {})


def test_run_scenarios_scores_mocked_ask(monkeypatch) -> None:
    def fake_ask(_base, _token, query, _timeout):
        if "Compile only" in query:
            return {"tool_calls": [{"name": "compile_file", "arguments": {"path": "src/AgentClient.cpp"}}]}
        if "json_round_trip" in query:
            return {"tool_calls": [{"name": "test", "arguments": {"filter": "json_round_trip"}}]}
        return {"tool_calls": [{"name": "build", "arguments": {"preset": "release"}}]}

    monkeypatch.setattr(eval_tools, "ask", fake_ask)
    scenarios = eval_tools.json.loads(eval_tools.SCENARIOS.read_text(encoding="utf-8"))
    report = eval_tools.run_scenarios(scenarios, base_url="http://x", token="t", timeout=1)
    assert report["passed"] == report["total"] == 3
