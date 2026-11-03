#!/usr/bin/env python3
"""Score whether the agent calls the expected first tool (no LLM-as-judge).

Run inside the stack (devtools or host with AGENT_BASE_URL):

    python scripts/container/eval_tools.py
    python scripts/container/eval_tools.py --scenarios scripts/container/eval_tools_scenarios.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

SCENARIOS = Path(__file__).resolve().parent / "eval_tools_scenarios.json"


def first_tool(body: dict) -> tuple[str | None, dict]:
    calls = body.get("tool_calls") or []
    if not calls:
        return None, {}
    first = calls[0]
    return first.get("name"), first.get("arguments") or {}


def args_match(actual: dict, expected: dict) -> bool:
    return all(actual.get(key) == value for key, value in expected.items())


def ask(base_url: str, token: str, query: str, timeout: float) -> dict:
    resp = httpx.post(
        f"{base_url.rstrip('/')}/chat",
        json={"query": query},
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def run_scenarios(scenarios: list[dict], *, base_url: str, token: str, timeout: float) -> dict:
    results = []
    for scenario in scenarios:
        row = {"id": scenario.get("id"), "ok": False, "first_tool": None, "error": None}
        try:
            body = ask(base_url, token, scenario["question"], timeout)
            name, arguments = first_tool(body)
            row["first_tool"] = name
            row["ok"] = name == scenario["expected_tool"] and args_match(
                arguments, scenario.get("expected_args_subset") or {}
            )
        except (httpx.HTTPError, json.JSONDecodeError, KeyError) as exc:
            row["error"] = str(exc)
        results.append(row)
    passed = sum(1 for r in results if r["ok"])
    return {
        "passed": passed,
        "total": len(results),
        "score": passed / len(results) if results else 0.0,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, default=SCENARIOS)
    parser.add_argument("--base-url", default=os.environ.get("AGENT_BASE_URL", "http://agent:8090"))
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("APP_REQUEST_TIMEOUT_SECONDS", "600")))
    args = parser.parse_args()
    token = os.environ.get("AGENT_BEARER_TOKEN", "")
    if not token:
        print("AGENT_BEARER_TOKEN is required", file=sys.stderr)
        return 2
    scenarios = json.loads(args.scenarios.read_text(encoding="utf-8"))
    report = run_scenarios(scenarios, base_url=args.base_url, token=token, timeout=args.timeout)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
